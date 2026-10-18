#!/usr/bin/env python3
"""Convert REGDOCS filings into evidence: every word, where it is, who read it, and what is missing.

    ./scripts/convert/convert.sh run 4738464 90495 ...      # documents by REGDOCS id
    ./scripts/convert/convert.sh run --sample samples/early-10.txt
    ./scripts/convert/convert.sh status
    ./scripts/convert/convert.sh report --sample samples/recent-10.txt

Research mode: every page is read by the text layer and by OCR, every page by four layout
models, every table by four table readers, every picture by the figure classifier. All
readings are kept; the choices made from them are recorded with their reasons. See DESIGN.md.

Per document, in output/<id>/convert/: words.parquet, regions.parquet, tables.parquet,
elements.parquet and cells.parquet (stable ids <doc>:p<page>:e<n>), pages.json (the ledger),
docling.json.gz, <id>.md, and manifest.json (every output's SHA-256, the PDF's and the code's).
"""
from __future__ import annotations

import argparse
import collections
import datetime
import functools
import gzip
import json
import multiprocessing as mp
import multiprocessing.connection
import os
import re
import platform
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402
import readers as rd  # noqa: E402

VERSION = "v2"   # v2: PP-OCRv6 medium; votes applied
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE, OUT = ROOT / "source", ROOT / "output"
RUNS = HERE / "runs"
CODE_FILES = ("convert.py", "readers.py", "assemble.py", "tool_families.json")
FACETS = ["Document Type", "Application Type", "Commodity", "Role", "File Type"]


def _word_schema():
    import pyarrow as pa
    return pa.schema([("id", pa.int64()), ("page", pa.int64()), ("reader", pa.string()), ("text", pa.string()),
                      ("raw", pa.string()), ("format", pa.string()), ("conf", pa.float64()), ("evidence", pa.bool_()),
                      ("extra", pa.bool_()), ("hidden", pa.bool_()), ("redacted", pa.bool_()), ("voted", pa.bool_()),
                      ("block", pa.int64()), ("line", pa.int64()), ("item", pa.string()),
                      ("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())])


WORD_SCHEMA = _word_schema()


def _element_schemas():
    import pyarrow as pa
    box = [("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())]
    elements = pa.schema([("element_id", pa.string()), ("doc_id", pa.string()), ("page", pa.int64()), ("n", pa.int64()),
                          ("label", pa.string()), ("text", pa.string()), ("section_path", pa.string()),
                          ("heading_level", pa.int64()), ("furniture", pa.bool_()), ("continues", pa.bool_()), *box,
                          ("word_ids", pa.list_(pa.int64())), ("reader", pa.string()), ("rows", pa.int64()),
                          ("cols", pa.int64()), ("figure", pa.string())])
    cells = pa.schema([("element_id", pa.string()), ("doc_id", pa.string()), ("page", pa.int64()),
                       ("r0", pa.int64()), ("r1", pa.int64()), ("c0", pa.int64()), ("c1", pa.int64()),
                       ("header", pa.bool_()), ("text", pa.string()), *box, ("word_ids", pa.list_(pa.int64()))])
    return elements, cells


ELEMENT_SCHEMA, CELL_SCHEMA = _element_schemas()


CHECKPOINT_PAGES = 100  # a document longer than this saves its pages every this many, and resumes there
GPU_LIMIT = 0.88        # share of the card in use above which a worker waits (a game took it to 0.906)
_GPU = {"checked": 0.0}


def gpu_memory() -> tuple[float | None, float | None]:
    """(used, total) MiB of the card, as nvidia-smi sees it - every process's, other programs' too."""
    import subprocess
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        used, total = (float(x) for x in r.stdout.splitlines()[0].split(","))
        return used, total
    except Exception:
        return None, None


def gpu_room(progress, pno: int, every: float = 30, max_wait: float = 1800) -> float:
    """Wait while the card is crowded; seconds waited. On WSL a full card does not fail, it spills to
    system memory and every page crawls: a game on Windows took the card to 22.2 of 24.5 GB and OCR
    from 1.4 to 9.9 s a page, and pages stalled (2026-10-17) - the same lesson the earlier pipelines
    wrote down. The worker hands back its own cache first; what is left is someone else's. Waiting
    reports progress, so the parent's stall limit does not stop the document."""
    t = time.monotonic()
    if t - _GPU["checked"] < every:
        return 0.0
    _GPU["checked"] = t
    used, total = gpu_memory()
    if used is None or used / total <= GPU_LIMIT:
        return 0.0
    rd.release_gpu(0)
    said = False
    while time.monotonic() - t < max_wait:
        used, total = gpu_memory()
        if used is None or used / total <= GPU_LIMIT:
            break
        if not said:
            print(f"GPU {used / 1024:.1f} of {total / 1024:.1f} GB in use: a worker waits (another program on the card?)",
                  flush=True)
            said = True
        progress("gpu wait", pno)
        time.sleep(15)
    return time.monotonic() - t


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def out_dir(doc_id: str) -> Path:
    return OUT / doc_id / "convert"


def read_gz_json(path: Path):
    return json.loads(gzip.decompress(path.read_bytes()))


# --------------------------------------------------------------------------
# context: what REGDOCS and the file say about the document

def context(doc_id: str, pdf, sha256: str) -> tuple[dict, list[dict]]:
    issues = []
    cer = OUT / doc_id / f"{doc_id}.cer.meta.json.gz"
    rec = read_gz_json(cer) if cer.exists() else {}
    if not rec:
        issues.append({"kind": "no_regdocs_record", "severity": "warning",
                       "evidence": "no scout record: the file's REGDOCS context is unknown"})
    dl = rec.get("download") or {}
    if dl.get("sha256") and dl["sha256"] != sha256:
        issues.append({"kind": "pdf_differs_from_download", "severity": "error",
                       "evidence": f"source PDF sha256 {sha256[:12]} is not the downloaded {dl['sha256'][:12]}"})
    md = pdf.metadata or {}
    ctx = {
        "regdocs": {"title": rec.get("title"), "date": rec.get("date"), "url": rec.get("url"),
                    "view_url": rec.get("view_url"), "submitter": rec.get("submitter"),
                    "filing": (rec.get("filing") or {}).get("number"), "company": (rec.get("company") or {}).get("name"),
                    "project": (rec.get("project") or {}).get("name"),
                    "facets": {k: (rec.get("facets") or {}).get(k) for k in FACETS},
                    "identifiers": rec.get("identifiers"), "downloaded": dl.get("downloaded_at"),
                    "download_sha256": dl.get("sha256")},
        "pdf": {"sha256": sha256, "pages": pdf.page_count, "producer": tool_family(md.get("producer")),
                "creator": tool_family(md.get("creator")), "format": md.get("format"),
                "created": md.get("creationDate"), "encrypted": pdf.is_encrypted, "repaired": pdf.is_repaired,
                "outline_entries": len(pdf.get_toc() or []), "form_fields": pdf.is_form_pdf or 0},
    }
    return ctx, issues


def tool_family(raw):
    return rd.tool_family(raw)


# --------------------------------------------------------------------------
# one page

def convert_page(models: rd.Models, pdf_path: Path, page, pno: int, ctx: dict, transcript_doc: bool = False,
                 votes: list[dict] = (), base: int = 0, light: bool = False, cached=None, ocr_cached=None) -> dict:
    """One page. `light`: the evidence pass for the whole corpus - words from the text layer, OCR
    only where the layer is not sound or images may hold text, no layout, table or figure models."""
    t0 = time.perf_counter()
    timings = {}

    def timed(name, fn):
        s = time.perf_counter()
        try:
            return fn()
        finally:
            timings[name] = round(time.perf_counter() - s, 3)

    issues = []
    facts = timed("facts", lambda: rd.page_facts(page))
    pdf_w, health = cached or timed("pdf_words", lambda: rd.pdf_words(page))
    verdict = rd.text_layer_verdict(health)
    vis_words = [w for w in pdf_w if not w["hidden"] and not w["redacted"]]
    # light: a sound layer with no image of any size needs no OCR (nothing it could add)
    skip_ocr = light and verdict == "sound" and facts["largest_image_cover"] < 0.05
    ocr_ran = not skip_ocr
    try:
        ocr_w = [] if skip_ocr else ocr_cached if ocr_cached is not None else timed("ocr", lambda: models.ocr(page))
    except Exception as e:                  # OCR is one reader: its failure is recorded, not the page's
        ocr_w, ocr_ran = [], False
        issues.append({"kind": "ocr_failed", "severity": "warning", "evidence": repr(e)})

    def compare(pdf_part):
        # without an OCR reading nothing was measured: None, not "OCR found 0%" (a light pass's
        # sound pages read 0.0 and pulled report's averages down; review 2026-10-17)
        m = asm.match_readings(pdf_part, ocr_w)
        if not ocr_ran:
            m.update(ocr_words=None, pdf_found_by_ocr=None, ocr_found_in_pdf=None)
        return m
    agreement = timed("agreement", lambda: compare([w for w in pdf_w if not w["hidden"] and not w["redacted"]]))
    hidden_agreement = compare([w for w in pdf_w if w["hidden"]]) if any(w["hidden"] for w in pdf_w) else None
    # a stub layer: a mostly-image page whose layer holds a few words (a stamp, a markup) while OCR
    # finds many times more - Sharp, RICOH, EQScan, Bluebeam scans (producers sample) [C §5.2]
    if verdict == "sound" and facts["largest_image_cover"] >= 0.8 and len(vis_words) < 100 \
            and len(ocr_w) > 4 * max(1, len(vis_words)):
        verdict = "stub"
    source, pdf_used, ocr_used = asm.choose_evidence(verdict, pdf_w, ocr_w)

    # every reading of every word, pdf first; `evidence` marks the ones the page's text is made of
    words = []
    for i, w in enumerate(pdf_w):
        words.append({**w, "reader": "pdf", "evidence": i in set(pdf_used), "extra": False})
    used = set(ocr_used)
    for j, w in enumerate(ocr_w):
        words.append({**w, "reader": "ocr", "evidence": j in used, "extra": source == "pdf" and j in used})
    # where two independent voters both chose the scan's own OCR layer for a line, its words are
    # the evidence for that line and ours stay as a reading (154133: ours "RES E E J T T E S E",
    # the layer and both voters "RESIDENTS ARE AT A GREATER RISK ...") [L v1, voters]
    voted_lines = 0
    if source == "ocr" and votes:
        by_line = collections.defaultdict(dict)
        for v in votes:
            by_line[(round(v["x0"]), round(v["y0"]))][v["voter"]] = v
        for line in by_line.values():
            if len(line) >= 2 and all(v["vote"] == "theirs" for v in line.values()):
                # by place, not by id: ids shift when a page gains words (barcodes)
                v = next(iter(line.values()))
                box = (v["x0"], v["y0"], v["x1"], v["y1"])
                for w in words:
                    if asm.inside(box, *asm.centre(w["box"]), 2.0):
                        if w["reader"] == "ocr":
                            w["evidence"] = False
                        elif w["reader"] == "pdf" and w["hidden"]:
                            w.update(evidence=True, voted=True)
                voted_lines += 1
    ev = [i for i, w in enumerate(words) if w["evidence"]]
    ev_words = [words[i] for i in ev]

    if verdict != "sound" and pdf_w:
        issues.append({"kind": f"text_layer_{verdict}", "severity": "warning",
                       "evidence": f"text layer {verdict}; the page's text is OCR"})
    # judged on words big enough for OCR at its resolution: a map's 2 pt labels are exact in
    # the layer and unreadable at 200 dpi (3283303: 32% "agreement" on a sound layer)
    min_h = 5.0 * 200 / rd.SETTINGS["ocr_dpi"]
    readable = [w for w in pdf_w if not w["hidden"] and not w["redacted"] and w["box"][3] - w["box"][1] >= min_h]
    agreement["readable"] = compare(readable) if readable else None
    fa = (agreement["readable"] or {}).get("pdf_found_by_ocr")
    n_extra = sum(1 for w in ev_words if w["extra"])
    if n_extra:
        issues.append({"kind": "ink_not_in_text_layer", "severity": "info",
                       "evidence": f"{n_extra} OCR words over ink the text layer does not hold (stamps, text in images)"})
    n_red = sum(1 for w in pdf_w if w["redacted"])
    if voted_lines:
        issues.append({"kind": "lines_from_scan_ocr_layer", "severity": "info",
                       "evidence": f"{voted_lines} line(s) read from the scan's own OCR layer: both voters chose it over ours"})
    if n_red:
        issues.append({"kind": "text_under_redaction", "severity": "info",
                       "evidence": f"{n_red} text-layer words under a redaction, left out of every output"})

    # ---- layout: every model, merged
    layouts = {} if light else timed("layout", lambda: models.layout_all(page))
    regions = asm.merge_layouts({m: v["detections"] for m, v in layouts.items()}) if layouts else []
    # barcodes in every picture region, decoded: exact values, evidence like the text layer
    codes = []
    for r in regions:
        if r["kept"] and r["label"] == "picture":
            try:
                codes += rd.barcodes(page, r["box"])
            except Exception as e:
                issues.append({"kind": "barcode_read_failed", "severity": "info", "evidence": repr(e)})
    unique = []                  # overlapping picture regions decode the same barcode twice
    for c in codes:
        if not any(u["raw"] == c["raw"] and asm.iou(u["box"], c["box"]) > 0.5 for u in unique):
            unique.append(c)
    codes = unique
    for c in codes:
        words.append({**c, "reader": "barcode", "evidence": True, "extra": False})
    if codes:
        ev = [i for i, w in enumerate(words) if w["evidence"]]
        ev_words = [words[i] for i in ev]
        issues.append({"kind": "barcodes_decoded", "severity": "info",
                       "evidence": f"{len(codes)} barcode(s): " + ", ".join(sorted({c['format'] for c in codes}))})
    disputed = [r for r in regions if r["kept"] and r["share"] < asm.SETTINGS["disputed_region"]]
    if disputed:
        issues.append({"kind": "layout_models_disagree", "severity": "info",
                       "evidence": f"{len(disputed)} region(s) whose label the models split on"})

    # ---- words to regions, and each region read by the tool for its label
    groups = asm.assign(ev_words, regions)
    elements, tables, pictures, checkboxes = [], [], [], []
    page_img = None
    # A transcript page is numbered lines, each citable by page and line: read as lines, not by
    # the layout model, which takes the margin numbers for a two-column table [L Route by what a page is]
    numbered = asm.line_numbers(ev_words, page.rect.width, strict=transcript_doc != "title") if source == "pdf" else []
    transcript = transcript_doc and bool(numbered)
    # checkboxes hold no words (the box is the content): kept for their state, ticked or not
    # numbered paper (written arguments, evidence): the margin's line numbers, wherever a region
    # took them, are one furniture item - kept and citable, not stray paragraphs (2905206)
    if not transcript:
        margin = asm.line_numbers(ev_words, page.rect.width, strict=False)
        # the longest unbroken 1, 2, 3 ... run: footnote markers below it restart at 1 and stay
        # with their footnotes (the reset rule is for transcripts' several pages to a sheet)
        runs, cur = [], []
        for m in margin:
            if cur and int(m["text"]) != int(cur[-1]["text"]) + 1:
                runs.append(cur)
                cur = []
            cur.append(m)
        runs.append(cur)
        margin = max(runs, key=len) if margin else []
        if len(margin) >= asm.LINE_NUMBERS["min_lines"]:
            marked = {i for i, w in enumerate(ev_words) if any(w is m for m in margin)}
            for k in list(groups):
                groups[k] = [i for i in groups[k] if i not in marked]
            bs = [ev_words[i]["box"] for i in marked]
            elements.append({"region": None, "label": "line_numbers", "reader": source + "+margin_numbers", "share": None,
                             "box": (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)),
                             "words": [ev[i] for i in sorted(marked)]})
    work = [r for r in regions if r["kept"] and (groups.get(r["id"]) or r["label"] in
                                                 ("picture", "checkbox_selected", "checkbox_unselected"))]
    if transcript:
        # the numbers belong to their lines, even where a header or footer box took one
        marked = {i for i, w in enumerate(ev_words) if any(w is m for m in numbered)}
        for k in list(groups):
            groups[k] = [i for i in groups[k] if i not in marked]
        body = [i for r in work if r["label"] not in asm.FURNITURE for i in groups.get(r["id"], [])] + \
            groups.get(-1, []) + sorted(marked)
        groups[-1] = []
        work = [r for r in work if r["label"] in asm.FURNITURE]
        # each number with the text from it down to the next: a line (left margin) or a
        # paragraph (right margin), the unit a transcript is cited by
        marks = sorted(numbered, key=lambda w: w["box"][1])
        mark_ids = {id(w) for w in marks}
        segs = [[] for _ in marks]
        before = []
        for i in body:
            w = ev_words[i]
            if id(w) in mark_ids:
                continue
            cy = asm.centre(w["box"])[1]
            k = max((j for j, m in enumerate(marks) if cy >= m["box"][1] - 0.5 * (m["box"][3] - m["box"][1])), default=None)
            (before if k is None else segs[k]).append(i)
        for ln in asm.lines_of([ev_words[i] | {"_k": i} for i in before]):
            ids = [ev[w["_k"]] for w in ln]
            bs = [ev_words[w["_k"]]["box"] for w in ln]
            elements.append({"region": None, "label": "text", "reader": source + "+transcript_line", "share": None,
                             "box": (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)),
                             "words": ids, "line_no": None})
        mark_index = {id(ev_words[i]): i for i in marked}
        for m, seg in zip(marks, segs):
            ids = [ev[mark_index[id(m)]]] + [ev[i] for i in seg]
            bs = [m["box"]] + [ev_words[i]["box"] for i in seg]
            elements.append({"region": None, "label": "text", "reader": source + "+transcript_line", "share": None,
                             "box": (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)),
                             "words": ids, "line_no": int(m["text"])})
    for r in work:                           # place each region by the words it was given
        ws_ = [ev_words[i] for i in groups.get(r["id"], [])]
        r["order_box"] = (min(w["box"][0] for w in ws_), min(w["box"][1] for w in ws_),
                          max(w["box"][2] for w in ws_), max(w["box"][3] for w in ws_)) if ws_ else None
    for r in asm.reading_order(work, page.rect.width):
        idx = [ev[i] for i in groups.get(r["id"], [])]
        el = {"region": r["id"], "label": r["label"], "box": r["box"], "share": r["share"],
              "words": idx, "reader": source}
        if r["label"] in ("table", "document_index"):
            if page_img is None:
                page_img, page_scale = rd.render(page, rd.SETTINGS["table_dpi"])
            tw = [words[i] for i in idx]
            readings = {}
            for name, fn in (
                    ("tableformer_fast", lambda: models.tableformer_cells("fast", page_img, page_scale, page, r["box"], tw)),
                    ("tableformer_accurate", lambda: models.tableformer_cells("accurate", page_img, page_scale, page, r["box"], tw)),
                    ("mupdf_lines", lambda: rd.mupdf_table_cells(pdf_path, pno, r["box"], "lines")),
                    ("mupdf_text", lambda: rd.mupdf_table_cells(pdf_path, pno, r["box"], "text"))):
                s = time.perf_counter()
                try:
                    cells = fn()
                    readings[name] = {**asm.score_grid(cells, tw), "grid": cells}
                except Exception as e:
                    readings[name] = {"error": repr(e), "cells": 0, "score": 0}
                readings[name]["seconds"] = round(time.perf_counter() - s, 3)
            best, why = asm.choose_grid(readings)
            tables.append({"region": r["id"], "box": r["box"], "chosen": best, "why": why,
                           "readings": {k: {kk: vv for kk, vv in v.items() if kk != "grid"} for k, v in readings.items()},
                           "grids": {k: v.get("grid") for k, v in readings.items()}})
            # a contents page's entries are lines (title ... page), not a table: read so, while
            # every grid is still recorded [L Routing by kind, measured on the sample]
            as_lines = r["label"] == "document_index" or asm.dot_leader_share(tw) >= 0.5
            tables[-1]["read_as"] = "lines" if as_lines else "grid"
            if as_lines:
                el.update(label="contents", reader=source + "+contents_line")
                for ln in asm.lines_of([words[i] | {"_k": i} for i in idx]):
                    bs = [w["box"] for w in ln]
                    elements.append({"region": r["id"], "label": "contents", "reader": source + "+contents_line",
                                     "share": r["share"], "words": [w["_k"] for w in ln],
                                     "box": (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))})
                continue
            if best:
                cells = readings[best]["grid"]
                # each word in one cell, the smallest holding its centre: overlapping cells
                # would otherwise each repeat it (90517: 1.12 export words per evidence word)
                pw, ph = page.rect.width, page.rect.height
                for c in cells:                   # a grid line drawn past the page edge (x = -107)
                    b = c["box"]
                    c["box"] = (max(0.0, b[0]), max(0.0, b[1]), min(pw, b[2]), min(ph, b[3]))
                    c["words"] = []
                by_size = sorted(cells, key=lambda c: asm.area(c["box"]))
                for i in idx:
                    c = next((c for c in by_size if asm.inside(c["box"], *asm.centre(words[i]["box"]), 0.5)), None)
                    if c is not None:
                        c["words"].append(i)
                in_cells = {i for c in cells for i in c["words"]}
                el.update(cells=cells, rows=readings[best]["rows"], cols=readings[best]["cols"], grid=best,
                          loose=[i for i in idx if i not in in_cells])
                if readings[best]["score"] < asm.SETTINGS["doubtful_table"]:
                    issues.append({"kind": "table_grid_doubtful", "severity": "warning", "box": r["box"],
                                   "evidence": f"best grid ({best}) places {readings[best]['score']:.0%} of the table's words"})
            else:
                el["label"] = "text"           # no grid: its words stay, as text
                issues.append({"kind": "table_not_read", "severity": "warning", "box": r["box"], "evidence": why})
        elif r["label"] in ("checkbox_selected", "checkbox_unselected"):
            # the models' box usually takes in the label ("Intervenor"); when it holds no words,
            # the label is the words just right of it on the same line, recorded but left in their item
            inside_box = [w for w in ev_words if asm.inside(r["box"], *asm.centre(w["box"]), 1.0)]
            if idx or inside_box:            # the label, whichever item took its words
                label = " ".join(" ".join(w["text"] for w in ln) for ln in asm.lines_of(inside_box or [words[i] for i in idx]))
            else:
                cy = asm.centre(r["box"])[1]
                near = sorted((w for w in ev_words if abs(asm.centre(w["box"])[1] - cy) < max(4.0, r["box"][3] - r["box"][1])
                               and 0 <= w["box"][0] - r["box"][2] < 200), key=lambda w: w["box"][0])
                parts, last = [], r["box"][2]
                for w in near:
                    if w["box"][0] - last > 25:
                        break
                    parts.append(w["text"])
                    last = w["box"][2]
                label = " ".join(parts)
            el.update(checked=r["label"] == "checkbox_selected", checkbox_label=label or None)
            checkboxes.append({"region": r["id"], "box": r["box"], "checked": el["checked"],
                               "label": el["checkbox_label"], "share": r["share"]})
        elif r["label"] == "picture":
            try:
                el["figure"] = models.classify_figure(page, r["box"])
            except Exception as e:
                el["figure"] = {"error": repr(e)}
            pictures.append({"region": r["id"], "box": r["box"], **el["figure"],
                             "words": len(idx), "words_by_ocr": sum(1 for i in idx if words[i]["reader"] == "ocr")})
        elements.append(el)

    # words no region claimed become text of their own, so nothing is lost [C §5.3]
    loose = [ev[i] for i in groups.get(-1, [])]

    for g in asm.clusters([words[i] for i in loose]):
        ids = [loose[k] for k in g]
        bs = [words[i]["box"] for i in ids]
        elements.append({"region": None, "label": "text", "reader": source + "+unclaimed", "share": None,
                         "box": (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)),
                         "words": ids})
    if loose and not light:
        issues.append({"kind": "words_outside_regions", "severity": "info",
                       "evidence": f"{len(loose)} evidence words in no layout region, kept as text of their own"})

    if source == "pdf" and ocr_w and fa is not None and agreement["readable"]["pdf_words"] >= 20 and fa < 0.6:
        # on a map or drawing OCR misses labels set at angles over imagery (3283303): expected there
        page_area = page.rect.width * page.rect.height
        drawn = sum(asm.area(p["box"]) for p in pictures if p.get("class") in
                    ("geographical_map", "topographical_map", "engineering_drawing")) >= 0.5 * page_area
        issues.append({"kind": "pdf_ocr_disagree", "severity": "info" if drawn else "warning",
                       "evidence": f"OCR found {fa:.0%} of the text layer's words at their places"
                                   + ("; the page is mostly a map or drawing" if drawn else "")})

    # every item on the page in one reading order, placed by its words: words no region claimed
    # are otherwise appended at the page's end wherever they sit. A transcript's lines keep the
    # order of their numbers.
    if not transcript:
        for el in elements:
            ws_ = [words[i] for i in el["words"]]
            el["order_box"] = (min(w["box"][0] for w in ws_), min(w["box"][1] for w in ws_),
                               max(w["box"][2] for w in ws_), max(w["box"][3] for w in ws_)) if ws_ else None
        elements = asm.reading_order(elements, page.rect.width)

    # ---- checks
    in_items = collections.Counter(i for el in elements for i in el["words"])
    dup = [i for i, n in in_items.items() if n > 1]
    missing = [i for i in ev if i not in in_items]
    if dup:
        issues.append({"kind": "words_in_two_items", "severity": "error", "evidence": f"{len(dup)} words in more than one item"})
    if missing:
        issues.append({"kind": "words_lost", "severity": "error", "evidence": f"{len(missing)} evidence words in no item"})
    facts["ink_share"] = round(rd.ink_share(page), 5) if not ev else None
    if not ev and facts["ink_share"] < 0.002:
        issues.append({"kind": "blank_page", "severity": "info",
                       "evidence": f"{facts['ink_share']:.2%} of the page renders dark: blank (a duplex scan's back, a separator)"})
    elif not ev and facts["ink_share"] >= 0.95:
        # a page filed blacked out whole: 4600031 and 4600032 are one black fill over the sheet,
        # no text, no image - a fact about the filing, not a reading that failed (review 2026-10-17)
        issues.append({"kind": "page_redacted", "severity": "info",
                       "evidence": f"{facts['ink_share']:.0%} of the page renders dark and no reader found words: "
                                   "blacked out whole"})
    elif not ev and (facts["images"] or facts["paths"] > 50):
        photo = pictures and all(p.get("class") == "photograph" for p in pictures)
        issues.append({"kind": "no_text_found", "severity": "info" if photo else "warning",
                       "evidence": "a photograph with no words on it" if photo else
                       "the page has images or drawings but no reader found words on it"})
    # a scan carrying its own OCR layer: two OCR readings; where they part, a voter decides later
    hidden_vote = hidden_agreement and hidden_agreement["pdf_found_by_ocr"] is not None \
        and hidden_agreement["pdf_words"] >= 10 and hidden_agreement["pdf_found_by_ocr"] < 0.9
    if hidden_vote:
        issues.append({"kind": "ocr_readings_disagree", "severity": "warning",
                       "evidence": f"our OCR found {hidden_agreement['pdf_found_by_ocr']:.0%} of the scan's own OCR layer"})
    for i in issues:
        i.setdefault("page", pno)

    todo = sorted({t for t, cond in (
        ("ocr_page", source == "ocr" and bool(ev)),
        ("scanned_no_text", source == "ocr" and not ev and facts["largest_image_cover"] > 0.8
         and (facts.get("ink_share") or 0) >= 0.002),
        ("barcodes", bool(codes)),
        ("pdf_ocr_disagree", any(i["kind"] == "pdf_ocr_disagree" for i in issues)),
        ("ocr_vote", bool(hidden_vote)),
        ("layout_disputed", bool(disputed)),
        ("table_grid_doubtful", any(i["kind"] == "table_grid_doubtful" for i in issues)),
        ("picture_text_by_ocr", any(p["words_by_ocr"] for p in pictures)),
        ("drawing", any(p.get("class") in ("engineering_drawing", "geographical_map", "topographical_map") for p in pictures)),
        ("form_fields", bool(facts["widgets"])),
        ("text_in_images", n_extra > 0),
        ("oversize", facts["size"] == "oversize"),
    ) if cond})

    left = [r for r in regions if r["kept"] and r["label"] not in asm.FURNITURE
            and r["box"][2] < 0.55 * page.rect.width and (r["box"][2] - r["box"][0]) < 0.5 * page.rect.width]
    right = [r for r in regions if r["kept"] and r["label"] not in asm.FURNITURE and r["box"][0] > 0.45 * page.rect.width
             and (r["box"][2] - r["box"][0]) < 0.5 * page.rect.width]
    labels = collections.Counter(r["label"] for r in regions if r["kept"])
    facts["columns"] = 2 if len(left) >= 2 and len(right) >= 2 else 1
    facts["text_direction"] = max(health["directions"], key=health["directions"].get) if health["directions"] else None
    facts["kind"] = "transcript" if transcript else None
    facts["line_numbers"] = len(numbered)
    return {
        "page": pno, "facts": facts, "text_layer": verdict, "health": health, "source": source,
        "agreement": agreement, "hidden_layer_agreement": hidden_agreement,
        "words": {"pdf": len(pdf_w), "ocr": len(ocr_w), "evidence": len(ev), "ocr_extra": n_extra, "voted_lines": voted_lines,
                  "hidden": sum(1 for w in pdf_w if w["hidden"]), "redacted": n_red, "unclaimed": len(loose)},
        "layout": {"models": {m: {"detections": len(v["detections"]), "seconds": v["seconds"]} for m, v in layouts.items()},
                   "regions": len(regions), "kept": sum(1 for r in regions if r["kept"]), "labels": dict(labels),
                   "disputed": len(disputed), "mean_share": round(sum(r["share"] for r in regions if r["kept"]) /
                                                                  max(1, sum(1 for r in regions if r["kept"])), 3)},
        "tables": [{k: v for k, v in t.items() if k != "grids"} for t in tables],
        "pictures": pictures, "checkboxes": checkboxes, "issues": issues, "todo": todo,
        "elements": elements, "seconds": round(time.perf_counter() - t0, 3), "step_seconds": timings,
        "_words": words, "_regions": regions, "_layouts": layouts, "_tables": tables,
    }


# --------------------------------------------------------------------------
# one document

def saved_ocr(doc_id: str, light: bool) -> tuple[dict[int, list[dict]], str | None]:
    """The OCR words an earlier conversion of this document saved, by page - for pages where OCR
    ran - when it read them with the same OCR model in the same mode; else nothing. Assembly can
    then be redone without the GPU [L Sections]."""
    import pyarrow.parquet as pq
    led, wf = out_dir(doc_id) / f"{doc_id}.pages.json", out_dir(doc_id) / f"{doc_id}.words.parquet"
    try:
        L = json.loads(led.read_text())
        if L.get("ocr_model") != rd.SETTINGS["ocr_model"] or L.get("mode", "full") != ("light" if light else "full") \
                or not light:          # full mode also OCRs crops and turned renders: not reused
            return {}, None
        # OCR ran on the page, or a rerun took its words from the run before (which then records no
        # OCR step: without this, a second reuse would OCR those pages again)
        ran = {p["page"] for p in L["pages"] if ("ocr" in (p.get("step_seconds") or {}) or p.get("ocr_reused"))
               and not p.get("error")}
        cache = {pno: [] for pno in ran}
        for w in sorted(pq.read_table(wf).to_pylist(), key=lambda w: w["id"]):
            if w["reader"] == "ocr" and w["page"] in cache:
                cache[w["page"]].append({"text": w["text"], "conf": w["conf"], "line": w["line"], "block": w["block"],
                                         "rotate": 0, "box": (w["x0"], w["y0"], w["x1"], w["y1"]),
                                         "hidden": False, "redacted": False})
        return cache, L.get("converted_at")
    except (OSError, ValueError, KeyError):
        return {}, None


class checkpoint:
    """A long document's finished pages, saved range by range beside its outputs
    (`<id>.partial/pages-<from>-<to>.pkl.gz`), each written whole or not at all. Ranges are
    reused only by the same code, mode and file, in order from page 1; anything else is cleared."""

    def __init__(self, doc_id: str, pdf_sha: str, light: bool):
        self.dir = out_dir(doc_id) / f"{doc_id}.partial"
        self.key = {"code": code_identity()["sha256"], "mode": "light" if light else "full", "pdf": pdf_sha}

    def load(self) -> list[dict]:
        import pickle
        parts, nxt = [], 1
        for f in sorted(self.dir.glob("pages-*.pkl.gz")) if self.dir.is_dir() else []:
            try:
                part = pickle.loads(gzip.decompress(f.read_bytes()))
            except Exception:
                break
            if part.get("key") != self.key or part.get("from") != nxt:
                break
            parts.append(part)
            nxt = part["to"] + 1
        if not parts:
            self.clear()
        return parts

    def save(self, pages: list, words: list, regions: list, tables: list) -> None:
        import pickle
        if not pages:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        a, b = pages[0]["page"], pages[-1]["page"]
        f = self.dir / f"pages-{a:06d}-{b:06d}.pkl.gz"
        tmp = f.with_name(f.name + ".part")
        tmp.write_bytes(gzip.compress(pickle.dumps({"key": self.key, "from": a, "to": b, "pages": pages, "words": words,
                                                    "regions": regions, "tables": tables}), compresslevel=3))
        os.replace(tmp, f)

    def clear(self) -> None:
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


def mupdf_messages(text: str) -> collections.Counter:
    """MuPDF's stored messages, counted: "... repeated N times..." adds N to the line before."""
    out, last = collections.Counter(), None
    for line in text.splitlines():
        m = re.fullmatch(r"\.\.\. repeated (\d+) times\.\.\.", line.strip())
        if m and last:
            out[last] += int(m.group(1))
        elif line.strip():
            last = line.strip()[:200]
            out[last] += 1
    return out


def convert_document(models: rd.Models, doc_id: str, run: str, light: bool = False, progress=None,
                     reuse_ocr: bool = False, skip: tuple = (), pdfium: tuple = ()) -> dict:
    """`skip`: pages not to read at all - MuPDF did not finish them on an earlier try (an ArcGIS
    map page in 4537539 hangs text extraction and rendering alike). `pdfium`: pages whose render
    crashed or hung MuPDF on an earlier try, drawn by pdfium instead [L Memory and time]."""
    progress = progress or (lambda stage, n: None)    # tells the parent the document is moving
    ocr_saved, ocr_from = saved_ocr(doc_id, light) if reuse_ocr else ({}, None)
    import pymupdf
    import pyarrow as pa
    import pyarrow.parquet as pq
    t0 = time.perf_counter()
    pdf_path = SOURCE / f"{doc_id}.pdf"
    sha = rd.sha256_file(pdf_path)
    # MuPDF's own complaints about the file (broken streams, missing resources, a damaged xref)
    # go to this document's ledger instead of stderr, where eight workers' ~60,000 lines in the
    # light pass named no document (review 2026-10-17)
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_warnings(reset=True)
    pdf = pymupdf.open(pdf_path)
    ctx, doc_issues = context(doc_id, pdf, sha)
    # A transcript is known from the document, not one page: its title, or line numbers on most
    # pages with text (a numbered table in a report is not a transcript) [L Route by what a page is]
    numbered = with_text = 0
    layer = {}                       # each page's text layer, read once and reused below
    for i, pg in enumerate(pdf):
        if i + 1 in skip:
            progress("text layer", i + 1)
            continue
        if i + 1 in pdfium:
            pg.regdocs_render = "pdfium"
        try:
            layer[i + 1] = rd.pdfium_words(pg) if i + 1 in pdfium else rd.pdf_words(pg)
        except Exception as e:           # MuPDF gave up on the page ("layer/clip nesting too deep",
            pdfium = tuple(pdfium) + (i + 1,)          # 3718735): pdfium reads it, here and below
            pg.regdocs_render = "pdfium"
            layer[i + 1] = rd.pdfium_words(pg)
            doc_issues.append({"kind": "text_read_by_pdfium", "severity": "info", "page": i + 1,
                               "evidence": f"MuPDF: {e}"[:300]})
        progress("text layer", i + 1)
        ws = layer[i + 1][0]
        if len(ws) > 5:
            with_text += 1
            numbered += bool(asm.line_numbers(ws, pg.rect.width))
    title = (ctx["regdocs"].get("title") or "").casefold()
    transcript_doc = "title" if "transcript" in title else \
        "line numbers" if with_text and numbered / with_text >= 0.5 else None
    ctx["document"] = {"transcript": transcript_doc is not None, "transcript_by": transcript_doc,
                       "numbered_pages": numbered, "pages_with_text": with_text}
    votes, stale = collections.defaultdict(list), 0
    vf = out_dir(doc_id) / f"{doc_id}.votes.parquet"
    if vf.exists():
        for v in pq.read_table(vf).to_pylist():
            if v.get("ocr_model") == rd.SETTINGS["ocr_model"] and v.get("convert_version") == VERSION:
                votes[v["page"]].append(v)
            else:
                stale += 1
    if stale:
        doc_issues.append({"kind": "votes_stale", "severity": "info",
                           "evidence": f"{stale} vote(s) were cast on another OCR model's words; not applied, vote again"})
    pages, all_words, region_rows, table_rows = [], [], [], []
    # long documents in ranges: each range's pages saved as it finishes, so a crash, a restart or a
    # stall costs at most one range, not hours (the largest document is 11,563 pages; full mode reads
    # about 2.4 s a page). Ranges are reused only by the same code, mode and file.
    ck = checkpoint(doc_id, sha, light) if pdf.page_count > CHECKPOINT_PAGES else None
    start = 1
    if ck:
        for part in ck.load():
            pages += part["pages"]; all_words += part["words"]
            region_rows += part["regions"]; table_rows += part["tables"]
        start = len(pages) + 1
        if start > 1:
            progress("resumed at page", start)
    mark = (len(pages), len(all_words), len(region_rows), len(table_rows))
    for pno in range(start, pdf.page_count + 1):
        page = pdf[pno - 1]
        if pno in skip:
            r = page.rect
            p = {"page": pno, "error": "skipped", "elements": [], "todo": ["page_skipped"],
                 "issues": [{"page": pno, "kind": "page_skipped", "severity": "error",
                             "evidence": "MuPDF did not finish reading this page on an earlier try; not read"}],
                 "_words": [], "_regions": [], "_layouts": {}, "_tables": [],
                 "facts": {"width_pt": r.width, "height_pt": r.height, "skipped": True}}
            pages.append(p)
            progress("page", pno)
            continue
        if pno in pdfium:
            page.regdocs_render = "pdfium"
        waited = gpu_room(progress, pno)
        try:
            p = convert_page(models, pdf_path, page, pno, ctx, transcript_doc, votes.get(pno, []), len(all_words), light,
                             layer.pop(pno, None), ocr_saved.get(pno))
            if pno in pdfium:
                p["renderer"] = "pdfium"
                p["issues"].append({"page": pno, "kind": "rendered_by_pdfium", "severity": "info",
                                    "evidence": "MuPDF crashed or hung on this page on an earlier try: read and drawn by pdfium"})
            if pno in ocr_saved:
                p["ocr_reused"] = True
            if waited:
                p["issues"].append({"page": pno, "kind": "gpu_wait", "severity": "info",
                                    "evidence": f"waited {waited:.0f}s for room on the GPU (another program using it)"})
            progress("page", pno)
        except Exception as e:
            p = {"page": pno, "error": repr(e), "trace": traceback.format_exc(), "elements": [], "todo": ["page_failed"],
                 "issues": [{"page": pno, "kind": "page_failed", "severity": "error", "evidence": repr(e)}],
                 "_words": [], "_regions": [], "_layouts": {}, "_tables": [], "facts": rd.page_facts(page)}
        base = len(all_words)
        for el in p["elements"]:              # word indexes are per page until here; make them per document
            el["words"] = [base + i for i in el["words"]]
            for c in el.get("cells") or []:
                c["words"] = [base + i for i in c["words"]]
            if "loose" in el:
                el["loose"] = [base + i for i in el["loose"]]
        for i, w in enumerate(p.pop("_words")):
            all_words.append({**w, "id": base + i, "page": pno})
        for r in p.pop("_regions"):
            region_rows.append({"page": pno, "region": r["id"], "model": "merged", "label": r["label"],
                                "conf": r["share"], "support": r["support"], "kept": r["kept"],
                                "x0": r["box"][0], "y0": r["box"][1], "x1": r["box"][2], "y1": r["box"][3],
                                "votes": json.dumps(r["votes"]), "models": json.dumps(r["models"])})
        for m, v in p.pop("_layouts").items():
            for d in v["detections"]:
                region_rows.append({"page": pno, "region": None, "model": m, "label": d["label"], "conf": d["conf"],
                                    "support": None, "kept": None, "x0": d["box"][0], "y0": d["box"][1],
                                    "x1": d["box"][2], "y1": d["box"][3], "votes": None, "models": None})
        for t in p.pop("_tables"):
            for reader, rdg in t["readings"].items():
                table_rows.append({"page": pno, "region": t["region"], "reader": reader, "chosen": reader == t["chosen"],
                                   **{k: rdg.get(k) for k in ("score", "words", "placed", "straddle", "orphan", "multi",
                                                              "rows", "cols", "seconds", "error")},
                                   "cells": rdg.get("cells") if isinstance(rdg.get("cells"), int) else None,
                                   "grid": json.dumps(t["grids"].get(reader))})
        pages.append(p)
        if ck and (pno % CHECKPOINT_PAGES == 0 or pno == pdf.page_count):
            ck.save(pages[mark[0]:], all_words[mark[1]:], region_rows[mark[2]:], table_rows[mark[3]:])
            mark = (len(pages), len(all_words), len(region_rows), len(table_rows))

    out = out_dir(doc_id)
    out.mkdir(parents=True, exist_ok=True)

    # page boundaries: running headers and footers by repetition, sentences across breaks [L Page boundaries]
    try:
        boundaries = {**asm.mark_furniture(pages, all_words), **asm.stitch_tables(pages, all_words),
                      **asm.join_across_pages(pages, all_words)}
    except Exception as e:
        boundaries = {"error": repr(e)}
        doc_issues.append({"kind": "boundaries_failed", "severity": "warning", "evidence": repr(e)})

    # sections: the filer's bookmarks, else heading numbers in sequence [L Sections]
    try:
        outline = pdf.get_toc(simple=True)
    except Exception:
        outline = []
    try:
        sections = asm.mark_sections(pages, all_words, outline)
    except Exception as e:
        sections = {"source": None, "headings": 0, "error": repr(e)}
        doc_issues.append({"kind": "sections_failed", "severity": "warning", "evidence": repr(e)})

    # document, export, checks over the whole document
    doc, owner = asm.build_document(doc_id, pages, all_words)
    md = asm.markdown(pages, all_words)
    for w in all_words:
        w["item"] = owner.get(w["id"])
    doc_json = gzip.compress(doc.model_dump_json().encode(), mtime=0)
    # full conversions check the JSON as a consumer reloads it; the corpus pass checks the same
    # items in memory (reloading every document costs more than converting it)
    dcheck = asm.check_docling(doc if light else doc_json, all_words)
    if dcheck["not_named"] or dcheck["named_twice"]:
        doc_issues.append({"kind": "docling_words_unaccounted", "severity": "error",
                           "evidence": f"in the DoclingDocument: {dcheck['not_named']} evidence words in no item, "
                                       f"{dcheck['named_twice']} in two"})
    if dcheck["text_holds_words"] < 0.99:
        doc_issues.append({"kind": "docling_text_missing_words", "severity": "error",
                           "evidence": f"item texts hold {dcheck['text_holds_words']:.1%} of the words they name"})
    ev_tokens = asm.md_tokens(" ".join(w["text"] for w in all_words if w["evidence"]))
    md_tok = asm.md_tokens(md)
    have, want = collections.Counter(md_tok), collections.Counter(ev_tokens)
    recall = sum(min(n, have[t]) for t, n in want.items()) / sum(want.values()) if want else 1.0
    ratio = len(md_tok) / len(ev_tokens) if ev_tokens else 1.0
    if recall < 0.98:
        doc_issues.append({"kind": "export_missing_words", "severity": "error",
                           "evidence": f"Markdown holds {recall:.1%} of the evidence words"})
    if ratio > 1.3:
        doc_issues.append({"kind": "export_repeats_words", "severity": "warning",
                           "evidence": f"Markdown has {ratio:.2f} words per evidence word"})

    # an explicit schema: a document with no words (a photograph) still gets a readable file;
    # inferred from no rows, the file had no columns and no reader could open it (459903)
    pq.write_table(pa.Table.from_pylist(
        [{**{k: w.get(k) for k in WORD_SCHEMA.names[:-4]}, "x0": w["box"][0], "y0": w["box"][1],
          "x1": w["box"][2], "y1": w["box"][3]} for w in all_words], schema=WORD_SCHEMA),
        out / f"{doc_id}.words.parquet", compression="zstd")
    if region_rows:
        pq.write_table(pa.Table.from_pylist(region_rows), out / f"{doc_id}.regions.parquet", compression="zstd")
    if table_rows:
        pq.write_table(pa.Table.from_pylist(table_rows), out / f"{doc_id}.tables.parquet", compression="zstd")
    (out / f"{doc_id}.docling.json.gz").write_bytes(doc_json)
    (out / f"{doc_id}.md").write_text(md, encoding="utf-8")
    # elements and cells with stable ids (<doc>:p<page>:e<n>): what a search hit, a citation or a
    # table export points at; written by every mode, so a full run carries them without a rebuild
    el_rows, cell_rows = asm.elements(doc_id, pages, all_words)
    pq.write_table(pa.Table.from_pylist(el_rows, schema=ELEMENT_SCHEMA), out / f"{doc_id}.elements.parquet",
                   compression="zstd")
    if cell_rows:
        pq.write_table(pa.Table.from_pylist(cell_rows, schema=CELL_SCHEMA), out / f"{doc_id}.cells.parquet",
                       compression="zstd")
    else:
        (out / f"{doc_id}.cells.parquet").unlink(missing_ok=True)    # latest only: no cells, no stale file

    mupdf = mupdf_messages(pymupdf.TOOLS.mupdf_warnings(reset=True))
    if mupdf:
        doc_issues.append({"kind": "pdf_file_problems", "severity": "info",
                           "evidence": f"MuPDF reported {sum(mupdf.values())} problem(s) reading the file: "
                                       + "; ".join(f"{m} (x{n})" for m, n in mupdf.most_common(3))})
    issues = doc_issues + [i for p in pages for i in p["issues"]]
    sev = collections.Counter(i["severity"] for i in issues)
    todo = collections.Counter(t for p in pages for t in p["todo"])
    n_err = sum(1 for p in pages if p.get("error"))
    status = "failed" if n_err and n_err == len(pages) else "partial" if n_err else "ok"
    ledger = {
        # status leads with version and mode: the skip check reads only the ledger's first bytes, and
        # a ledger with failed pages was taken as done (4477638: 127 of 147 pages lost to a broken
        # pipe on 2026-10-13, never retried by the corpus pass)
        "doc_id": doc_id, "version": VERSION, "mode": "light" if light else "full", "status": status, "run": run,
        "code": code_identity(), "converted_at": now(), "ocr_model": rd.SETTINGS["ocr_model"], "ocr_reused_from": ocr_from,
        "seconds": round(time.perf_counter() - t0, 2), "context": ctx,
        "summary": {"pages": len(pages), "words_evidence": sum(1 for w in all_words if w["evidence"]),
                    "words_pdf": sum(1 for w in all_words if w["reader"] == "pdf"),
                    "words_ocr": sum(1 for w in all_words if w["reader"] == "ocr"),
                    "pages_by_source": dict(collections.Counter(p.get("source") for p in pages)),
                    "tables": sum(len(p.get("tables") or []) for p in pages),
                    "pictures": sum(len(p.get("pictures") or []) for p in pages),
                    "docling": dcheck, "export_recall": round(recall, 4), "export_word_ratio": round(ratio, 3),
                    "issues": dict(sev), "todo": dict(todo),
                    "sections": {k: v for k, v in sections.items() if k != "list"}, "boundaries": boundaries,
                    "mupdf_messages": dict(mupdf.most_common(50))},
        "sections": sections.get("list", []),
        "issues": doc_issues,
        "pages": [{k: v for k, v in p.items() if k != "elements"} for p in pages],
        "files": sorted(f.name for f in out.iterdir() if f.name.startswith(doc_id) and f.is_file()),
    }
    # the ledger last, and whole or not at all: a restart mid-write left nothing half-written to trip on
    tmp = out / f"{doc_id}.pages.json.part"
    tmp.write_text(json.dumps(ledger, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, out / f"{doc_id}.pages.json")
    # the manifest after everything else: each output's hash, beside the PDF's and the code's - the chain
    # from the file REGDOCS served to every derived file (docs/output-and-sidecars.md)
    files = {f.name: rd.sha256_file(f) for f in sorted(out.iterdir())
             if f.is_file() and f.name.startswith(doc_id) and not f.name.endswith((".part", ".manifest.json"))}
    manifest = {"doc_id": doc_id, "pdf_sha256": sha, "mode": ledger["mode"], "status": status,
                "code": ledger["code"], "converted_at": ledger["converted_at"], "files": files}
    tmp = out / f"{doc_id}.manifest.json.part"
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.replace(tmp, out / f"{doc_id}.manifest.json")
    if ck:
        ck.clear()                        # the document is whole: its ranges are no longer needed
    return {"doc_id": doc_id, "status": status, **ledger["summary"], "seconds": ledger["seconds"]}


# --------------------------------------------------------------------------
# the worker: models load once; the parent survives its crashes and hangs

def worker(conn, run: str, light: bool = False, reuse_ocr: bool = False):
    import logging
    logging.disable(logging.WARNING)
    code_identity()                       # the files this process loaded, before they can change
    try:
        models = rd.OcrOnly(lazy=reuse_ocr) if light else rd.Models()
    except Exception as e:
        conn.send({"ready": False, "error": repr(e), "trace": traceback.format_exc()})
        return
    conn.send({"ready": True})
    while True:
        job = conn.recv()
        if job is None:
            return
        try:
            doc_id, skip, pdfium = job
            res = convert_document(models, doc_id, run, light,
                                   lambda stage, n: conn.send({"progress": stage, "n": n}), reuse_ocr, tuple(skip),
                                   tuple(pdfium))
        except Exception as e:
            res = {"doc_id": doc_id, "status": "failed", "error": repr(e), "trace": traceback.format_exc()}
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        conn.send(res)


@functools.cache
def code_identity() -> dict:
    """The code that converts, once per process: a hash of the files it loads (what makes two
    ledgers comparable, committed or not) and the git commit, with whether those files differ
    from it. Every ledger was "v2" whatever code wrote it; half the light pass predated the page
    boundaries step and nothing said so (review 2026-10-17)."""
    import hashlib
    import subprocess
    h = hashlib.sha256()
    for name in CODE_FILES:
        h.update(name.encode() + b"\0" + (HERE / name).read_bytes() + b"\0")
    out = {"sha256": h.hexdigest(), "commit": None, "dirty": None}
    try:
        def git(*a):
            return subprocess.run(["git", "-C", str(HERE), *a], capture_output=True, text=True, timeout=60)
        r = git("rev-parse", "--short", "HEAD")
        if r.returncode == 0:
            out["commit"] = r.stdout.strip()
            out["dirty"] = bool(git("status", "--porcelain", "--", *CODE_FILES).stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def run_identity() -> tuple[str, Path]:
    import importlib.metadata as md
    d = RUNS / VERSION
    d.mkdir(parents=True, exist_ok=True)
    f = d / "run.json"
    if not f.exists():
        f.write_text(json.dumps({
            "version": VERSION, "created_at": now(), "python": sys.version, "platform": platform.platform(),
            "packages": {x.metadata["Name"]: x.version for x in md.distributions() if x.metadata["Name"]},
            "models": rd.model_identity(), "settings": {"readers": rd.SETTINGS, "assemble": asm.SETTINGS},
        }, indent=1, default=str))
    return VERSION, f


def ids_from(args) -> list[str]:
    ids = list(args.ids)
    for s in args.sample or []:
        ids += [l.split(",")[0].strip() for l in Path(s).read_text().splitlines() if l.strip() and l.strip()[0].isdigit()]
    return list(dict.fromkeys(ids))


def cmd_run(args) -> int:
    ids = ids_from(args)
    if args.all:                       # every PDF in source/, newest first
        ids += sorted((n[:-4] for n in os.listdir(SOURCE) if n.endswith(".pdf") and n[:-4].isdigit()),
                      key=int, reverse=True)
        ids = list(dict.fromkeys(ids))
    if not ids:
        print("nothing to convert: give ids, --sample or --all")
        return 2
    run, run_file = run_identity()
    todo, skipped, again = [], collections.Counter(), collections.Counter()
    def ledger_of(led):
        """Only what deciding to skip needs: version and mode lead the ledger, and a complete
        ledger ends with "}". Reading whole ledgers (MBs each, ~70,000 of them) took over an hour
        from the Windows drive after a restart emptied its cache."""
        try:
            with open(led, "rb") as f:
                head = f.read(400).decode("utf-8", "replace")
                f.seek(-8, os.SEEK_END)
                whole = f.read().rstrip().endswith(b"}")
        except OSError:
            return {}
        if not whole:                     # cut off mid-write (a restart): convert it again
            return {}
        out = {}
        for key in ("version", "mode", "status"):
            m = re.search(rf'"{key}": "([^"]*)"', head)
            if m:
                out[key] = m.group(1)
        return out
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        L = ledger_of(led) if led.exists() and not args.force else {}
        if not (SOURCE / f"{d}.pdf").exists():
            skipped["no PDF in source/"] += 1
        elif L.get("version") == VERSION and L.get("status", "ok") != "ok":
            again[L["status"]] += 1         # pages failed or were left out last time
            todo.append(d)
        elif L.get("version") == VERSION:
            # a light pass never replaces a full conversion; a full run redoes a light one
            if args.light or L.get("mode", "full") == "full":
                skipped[f"already converted ({VERSION})"] += 1
            else:
                todo.append(d)
        else:
            todo.append(d)
    if skipped:
        print("skipped: " + ", ".join(f"{k} {v}" for k, v in skipped.items()))
    if again:
        print("converting again: " + ", ".join(f"{k} {v}" for k, v in again.items()))
    code = code_identity()
    print(f"convert {VERSION}{' (light)' if args.light else ''}: {len(todo)} document(s); run identity {run_file}; "
          f"code {code['sha256'][:12]} at {code['commit']}{' + uncommitted changes' if code['dirty'] else ''}",
          flush=True)
    ctx = mp.get_context("spawn")
    failed = 0
    queue = list(reversed(todo))          # pop() from the end: todo order kept
    skips = collections.defaultdict(set)  # pages MuPDF did not finish, per document: not read on the retry
    pdfiums = collections.defaultdict(set)  # pages whose MuPDF render failed: drawn by pdfium on the retry

    class Slot:
        """One worker process and the document it is on."""
        def __init__(self):
            self.conn, child = ctx.Pipe()
            self.proc = ctx.Process(target=worker, args=(child, run, args.light, args.reuse_ocr), daemon=True)
            self.proc.start()
            self.doc = None
            self.since = 0.0

        def wait_ready(self):
            msg = self.conn.recv()
            if not msg.get("ready"):
                raise SystemExit(f"models failed to load: {msg.get('error')}\n{msg.get('trace')}")
            return self

        def give(self, d):
            self.doc, self.since = d, time.perf_counter()
            self.moved, self.where = self.since, "start"
            # a long document gets longer: a fixed hour stopped a 171-page drawing set, and
            # REGDOCS has documents of 900 and 7,280 pages (LESSONS.md, Memory and time)
            try:
                import pymupdf
                with pymupdf.open(SOURCE / f"{d}.pdf") as pdf:
                    pages = pdf.page_count
            except Exception:
                pages = 0
            self.limit = max(args.timeout, args.timeout_per_page * pages)
            self.pages = pages
            self.conn.send((d, sorted(skips[d]), sorted(pdfiums[d])))

    def report(d, res, s):
        nonlocal failed
        if res["status"] == "failed":
            failed += 1
            print(f"FAILED   {d:<10} {time.perf_counter() - s:7.1f}s  {res.get('error')}", flush=True)
            if res.get("trace"):
                print(res["trace"], flush=True)
            return
        sev = res.get("issues") or {}
        dc = res.get("docling") or {}
        print(f"{res['status']:<8} {d:<10} {res['pages']:>4}p {res['seconds']:7.1f}s  "
              f"words {res['words_evidence']} ({'/'.join(f'{k}:{v}' for k, v in res['pages_by_source'].items())})  "
              f"tables {res['tables']} pictures {res['pictures']}  docling {dc.get('named_once', 0)}/{dc.get('evidence_words', 0)} "
              f"text {dc.get('text_holds_words', 0):.1%}  md {res['export_recall']:.1%}  "
              f"issues {' '.join(f'{k}:{v}' for k, v in sev.items()) or 'none'}", flush=True)

    # several workers, each holding its own models; a crash or a hang costs one document
    t_all = time.perf_counter()
    # all workers load their models at once (one after another, 10 took 25 minutes to start)
    slots = [Slot() for _ in range(max(1, min(args.workers, len(queue))))] if queue else []
    for sl in slots:
        sl.wait_ready()
    for sl in slots:
        if queue:
            sl.give(queue.pop())
    while any(sl.doc for sl in slots):
        busy = [sl for sl in slots if sl.doc]
        ready = mp.connection.wait([sl.conn for sl in busy] + [sl.proc.sentinel for sl in busy], timeout=5)
        for i, sl in enumerate(slots):
            if not sl.doc:
                continue
            res = None
            now = time.perf_counter()
            if sl.conn in ready:
                try:
                    res = sl.conn.recv()
                    while "progress" in res:         # a page done: the document is moving
                        sl.moved, sl.where = now, f"{res['progress']} {res['n']}"
                        res = sl.conn.recv() if sl.conn.poll() else None
                        if res is None:
                            break
                except (EOFError, OSError):      # the worker died mid-send
                    res = "crashed"
            elif sl.proc.sentinel in ready:
                res = "crashed"
            elif now - sl.moved > args.stall or now - sl.since > sl.limit:
                res = "stalled" if now - sl.moved > args.stall else "timeout"
            if res in ("crashed", "stalled", "timeout"):
                if sl.proc.is_alive():
                    sl.proc.kill()
                sl.proc.join(30)                 # not reaped yet, it still looks alive and gets the next document
                stalled = res != "timeout"       # a crash on a page (4473998: "double free") is as page-bound as a hang
                why = {"crashed": f"worker crashed (exit {sl.proc.exitcode})", "stalled": f"no progress for {args.stall:.0f}s",
                       "timeout": f"timeout after {sl.limit:.0f}s"}[res]
                # a page that never finishes costs that page, not the document: retry without it
                stage, _, n = sl.where.rpartition(" ")
                # the next page not already left out: skipped pages report no progress, so after
                # "text layer 22" with 23 skipped the worker is on 24 (4449303 retried 23 six times)
                after = int(n) if n.isdigit() else 0
                left = sorted(set(range(1, sl.pages + 1)) - skips[sl.doc])
                stuck = next((q for q in left if q > after), 0)
                if stage == "text layer" and not stuck:            # past the pre-scan: on the first page read
                    stuck = left[0] if left else 0
                if stuck in skips[sl.doc] or (stage == "page" and stuck in pdfiums[sl.doc] and stuck in skips[sl.doc]):
                    stuck = 0                                      # never the same retry twice
                # a page MuPDF did not finish (reading its text or drawing it) is read by pdfium next
                # time, text and image; if pdfium fails it too, the page is left out
                if stalled and 0 < stuck <= sl.pages and stuck not in pdfiums[sl.doc] \
                        and len(pdfiums[sl.doc]) < max(10, sl.pages // 5):
                    pdfiums[sl.doc].add(stuck)
                    print(f"RETRY    {sl.doc:<10} {now - sl.since:7.1f}s  {why} ({stage or sl.where} {stuck}): "
                          f"again with page(s) {sorted(pdfiums[sl.doc])} read by pdfium", flush=True)
                    queue.append(sl.doc)
                    res = "retry"
                elif stalled and 0 < stuck <= sl.pages and len(skips[sl.doc]) < 3:
                    skips[sl.doc].add(stuck)
                    print(f"RETRY    {sl.doc:<10} {now - sl.since:7.1f}s  {why} ({stage or sl.where} {stuck}): "
                          f"again without page(s) {sorted(skips[sl.doc])}", flush=True)
                    queue.append(sl.doc)
                    res = "retry"
                else:
                    res = {"doc_id": sl.doc, "status": "failed", "error": f"{why} (at {sl.where})"}
            if res is None:
                continue
            if res != "retry":
                report(sl.doc, res, sl.since)
            sl.doc = None
            # a worker whose pipe broke may still look alive while it exits (4473998): anything but
            # a clean result means it is finished with, and replaced
            if res == "retry" or not isinstance(res, dict) or res.get("status") == "failed" and \
                    str(res.get("error", "")).startswith(("worker crashed", "no progress", "timeout")):
                if sl.proc.is_alive():
                    sl.proc.kill()
                sl.proc.join(30)
            if not sl.proc.is_alive():
                slots[i] = sl = Slot().wait_ready()
            while queue:
                d = queue.pop()
                try:
                    sl.give(d)
                    break
                except OSError:               # died between its result and the next document
                    queue.append(d)
                    sl.proc.kill()
                    sl.proc.join(30)
                    slots[i] = sl = Slot().wait_ready()
    for sl in slots:
        if sl.proc.is_alive():
            sl.conn.send(None)
            sl.proc.join(10)
    print(f"done: {len(todo) - failed} converted, {failed} failed, {time.perf_counter() - t_all:.0f}s")
    return 1 if failed else 0


def ledgers(ids: list[str]) -> list[dict]:
    if not ids:   # every converted document: listing output/ is quick, globbing into it is not
        ids = [e.name for e in os.scandir(OUT) if e.is_dir() and (Path(e.path) / "convert" / f"{e.name}.pages.json").exists()]
    out = []
    for d in ids:
        f = out_dir(d) / f"{d}.pages.json"
        if f.exists():
            out.append(json.loads(f.read_text()))
    return out


def cmd_status(args) -> int:
    ls = ledgers(ids_from(args))
    print(f"{len(ls)} converted document(s)")
    todo, sev = collections.Counter(), collections.Counter()
    for L in ls:
        todo.update(L["summary"]["todo"])
        sev.update(L["summary"]["issues"])
    print("issues  " + ", ".join(f"{k} {v}" for k, v in sev.most_common()))
    print("todo    " + ", ".join(f"{k} {v}" for k, v in todo.most_common()))
    return 0


def cmd_report(args) -> int:
    """What the readings say, across the documents: by page feature, which reader, how well."""
    import duckdb
    ids = ids_from(args)
    ls = ledgers(ids)
    if not ls:
        print("no converted documents")
        return 1
    rows = []
    for L in ls:
        c = L["context"]
        for p in L["pages"]:
            f = p.get("facts") or {}
            rows.append({"doc": L["doc_id"], "page": p["page"], "year": (c["regdocs"].get("date") or "")[:4],
                         "doc_type": ",".join(c["regdocs"]["facets"].get("Document Type") or []) or None,
                         "producer": ((c["pdf"].get("producer") or c["pdf"].get("creator") or {}).get("family")),
                         "size": f.get("size"), "orientation": f.get("orientation"), "columns": f.get("columns"),
                         "text_layer": p.get("text_layer"), "source": p.get("source"),
                         "pdf_found_by_ocr": asm.ocr_agreement(p, L),
                         "evidence": (p.get("words") or {}).get("evidence"),
                         "unclaimed": (p.get("words") or {}).get("unclaimed"),
                         "ocr_extra": (p.get("words") or {}).get("ocr_extra"),
                         "layout_share": (p.get("layout") or {}).get("mean_share"),
                         "disputed": (p.get("layout") or {}).get("disputed"), "seconds": p.get("seconds")})
    con = duckdb.connect()
    con.register("pages", __import__("pyarrow").Table.from_pylist(rows))

    def show(title, sql):
        print(f"\n## {title}")
        res = con.execute(sql)
        names = [d[0] for d in res.description]
        data = res.fetchall()
        widths = [max(len(str(n)), *(len(f"{r[i]}") for r in data)) if data else len(n) for i, n in enumerate(names)]
        print("  ".join(n.ljust(w) for n, w in zip(names, widths)))
        for r in data:
            print("  ".join(f"{v}".ljust(w) for v, w in zip(r, widths)))

    print(f"{len(ls)} documents, {len(rows)} pages")
    show("Pages by producer, size, orientation", """
        SELECT coalesce(producer,'?') producer, size, orientation, count(*) pages,
               round(avg(pdf_found_by_ocr),3) pdf_vs_ocr, round(avg(layout_share),3) layout_agree,
               sum(unclaimed) unclaimed, sum(ocr_extra) ocr_extra, round(avg(seconds),1) s_per_page
        FROM pages GROUP BY ALL ORDER BY pages DESC""")
    show("Text layer verdict and where the text came from", """
        SELECT year, text_layer, source, count(*) pages, round(avg(pdf_found_by_ocr),3) pdf_vs_ocr
        FROM pages GROUP BY ALL ORDER BY year, pages DESC""")
    show("By REGDOCS document type", """
        SELECT coalesce(doc_type,'(no facet)') doc_type, count(DISTINCT doc) docs, count(*) pages,
               round(avg(layout_share),3) layout_agree, sum(disputed) disputed_regions, sum(unclaimed) unclaimed
        FROM pages GROUP BY ALL ORDER BY pages DESC""")

    # tables and layout from the Parquet readings
    tfiles = [str(out_dir(L["doc_id"]) / f"{L['doc_id']}.tables.parquet") for L in ls
              if (out_dir(L["doc_id"]) / f"{L['doc_id']}.tables.parquet").exists()]
    if tfiles:
        con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet({tfiles!r}, filename=true)")
        show("Which table reader places the most words, by page feature (ties count for each)", """
            WITH t2 AS (SELECT regexp_extract(filename, '([0-9]+)[.]tables', 1) doc, * FROM t),
                 best AS (SELECT doc, page, region, max(score) top FROM t2 GROUP BY ALL)
            SELECT p.producer, p.size, p.orientation, coalesce(p.doc_type,'(none)') doc_type, count(DISTINCT t2.doc||t2.page||t2.region) n_tables,
                   sum(CASE WHEN reader='tableformer_fast' AND score=top THEN 1 ELSE 0 END) fast_best,
                   sum(CASE WHEN reader='tableformer_accurate' AND score=top THEN 1 ELSE 0 END) accurate_best,
                   sum(CASE WHEN reader='mupdf_lines' AND score=top THEN 1 ELSE 0 END) lines_best,
                   sum(CASE WHEN reader='mupdf_text' AND score=top THEN 1 ELSE 0 END) text_best,
                   round(avg(top),3) best_score
            FROM t2 JOIN best USING (doc, page, region) JOIN pages p ON p.doc=t2.doc AND p.page=t2.page
            GROUP BY ALL ORDER BY n_tables DESC LIMIT 25""")
        show("Table readers: words placed, and how often each was chosen", """
            SELECT reader, count(*) n_tables, round(avg(score),3) mean_score, sum(placed) placed, sum(straddle) straddle,
                   sum(orphan) orphan, sum(CASE WHEN chosen THEN 1 ELSE 0 END) chosen,
                   sum(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) errors, round(avg(seconds),2) s
            FROM t GROUP BY reader ORDER BY chosen DESC""")
    rfiles = [str(out_dir(L["doc_id"]) / f"{L['doc_id']}.regions.parquet") for L in ls
              if (out_dir(L["doc_id"]) / f"{L['doc_id']}.regions.parquet").exists()]
    if rfiles:
        con.execute(f"CREATE VIEW r AS SELECT * FROM read_parquet({rfiles!r})")
        show("Merged regions by label: models agreeing", """
            SELECT label, count(*) regions, round(avg(support),2) models_found_it, round(avg(conf),3) vote_share,
                   sum(CASE WHEN conf < 0.6 THEN 1 ELSE 0 END) disputed
            FROM r WHERE model='merged' AND kept GROUP BY label ORDER BY regions DESC""")
        con.execute(f"CREATE VIEW r2 AS SELECT regexp_extract(filename, '([0-9]+)[.]regions', 1) doc, * FROM read_parquet({rfiles!r}, filename=true)")
        show("Layout models agreeing, by box type and page feature", """
            SELECT r2.label, p.producer, p.size, p.orientation, p.source, count(*) regions,
                   round(avg(support),2) models_found_it, round(avg(conf),3) vote_share
            FROM r2 JOIN pages p ON p.doc=r2.doc AND p.page=r2.page
            WHERE model='merged' AND kept GROUP BY ALL HAVING count(*) >= 10 ORDER BY vote_share LIMIT 25""")
        show("Detections per layout model", """
            SELECT model, count(*) detections, round(avg(conf),3) mean_conf FROM r WHERE model<>'merged'
            GROUP BY model ORDER BY model""")
    pics = collections.Counter(p.get("class") for L in ls for pg in L["pages"] for p in pg.get("pictures") or [])
    if pics:
        print("\n## Pictures by class\n" + "\n".join(f"  {v:5}  {k}" for k, v in pics.most_common()))
    todo = collections.Counter(t for L in ls for p in L["pages"] for t in p.get("todo") or [])
    print("\n## Todo (pages)\n" + "\n".join(f"  {v:5}  {k}" for k, v in todo.most_common()))
    kinds = collections.Counter((i["kind"], i["severity"]) for L in ls for i in L["issues"] + [x for p in L["pages"] for x in p.get("issues") or []])
    print("\n## Issues\n" + "\n".join(f"  {v:5}  {k} ({s})" for (k, s), v in kinds.most_common()))
    exp = [(L["doc_id"], L["summary"]["export_recall"], L["summary"]["export_word_ratio"]) for L in ls]
    print("\n## Export: share of evidence words in the Markdown, words per evidence word\n" +
          "\n".join(f"  {d:<10} {a:.2%}  {b:.2f}" for d, a, b in sorted(exp, key=lambda x: x[1])))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "status", "report"):
        c = sub.add_parser(name)
        c.add_argument("ids", nargs="*")
        c.add_argument("--sample", action="append", help="a file of REGDOCS ids, one per line")
        if name == "run":
            c.add_argument("--force", action="store_true", help="redo documents already converted")
            c.add_argument("--light", action="store_true",
                           help="evidence pass: text layer, OCR only where needed, no layout/table/figure models")
            c.add_argument("--all", action="store_true", help="every PDF in source/, newest first")
            c.add_argument("--workers", type=int, default=1,
                           help="worker processes, each with its own models (light mode: 6-8 is reasonable)")
            c.add_argument("--reuse-ocr", action="store_true",
                           help="light mode: take OCR words from the document's last light conversion (same OCR "
                                "model) instead of running OCR again - redo assembly without the GPU")
            c.add_argument("--stall", type=float, default=300,
                           help="stop a document when no page has finished for this many seconds")
            c.add_argument("--timeout", type=float, default=3600, help="seconds per document, at least")
            c.add_argument("--timeout-per-page", type=float, default=60,
                           help="seconds per page, when that is more than --timeout (900 pages: 15 hours); "
                                "a cap only - a stuck document is stopped by --stall")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
