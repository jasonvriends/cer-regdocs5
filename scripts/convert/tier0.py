#!/usr/bin/env python3
"""Tier 0: what every PDF and every page *is*, read from the file alone.

    scripts/convert/.venv/bin/python scripts/convert/tier0.py run [PDF or DIR ...] [--workers 8]
    scripts/convert/.venv/bin/python scripts/convert/tier0.py status
    scripts/convert/.venv/bin/python scripts/convert/tier0.py flatten

No rendering, no models: the file's own structure, measured in milliseconds a
page. The answers decide which tool reads each page later -- a sound text layer
is copied, a scan is OCR'd, someone else's hidden OCR layer is treated as one
reader among several, a form's values are read from its fields.

Every document gets one record, whether it succeeded or not, beside its CER
record at `output/<id>/convert/tier0-<sig>/<id>.tier0.json.gz`. It holds:

  file        path, size, mtime, sha256 of the bytes that were read
  pdf         version, metadata, XMP, encryption, repair, catalog keys,
              outline, page labels, layers, embedded files (each hashed), fonts
  pages       one row per page: geometry, text layer (visible and invisible,
              by character class and direction), images (with digests),
              vector paths, annotations, form fields and their values, links,
              MuPDF's own warnings, and the time each page took
  hints       derived labels (scan, digital, blank, hidden OCR, ...). Rules,
              not facts: they are recomputable from the rest of the record
  status      ok / partial / failed, with reason codes saying why

`<sig>` hashes this script's syntax tree (so comments and docstrings do not
change it), the environment's installed packages, the Python version and the
settings. Change any of them and the next run lands beside the old one.
What the run is lives in `scripts/convert/runs/tier0-<sig>/`: `env.json` (what the
signature stands for, every package included) and `code/tier0.py`.

Each session writes `sessions/<stamp>.log` there, one readable line per
document, and `sessions/<stamp>.jsonl`, the same as events. Rerunning the same
command resumes: a document already recorded is skipped unless its file
changed.

The records are the truth; `flatten` derives one Parquet table per kind of
row (documents, pages, images, fonts, widgets, annotations, embedded files)
into the run directory, for querying the corpus with DuckDB in milliseconds.

Each document runs in a worker process, so a PDF that crashes or hangs MuPDF
costs that document only: it is recorded as failed (`worker_crashed`,
`timeout`) and a fresh worker takes over. Ctrl-C once finishes the documents
in progress; twice stops them.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import re
import shutil
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DEFAULT_OUTPUT, DEFAULT_RUNS, HERE, Stage, add_run_arguments,  # noqa: E402
                    base_environment, clip, load_index, r, run_stage, runs_of, sha256_file, utcnow,
                    write_json_atomic)

STAGE = "tier0"
TOOL_RULES_PATH = HERE / "tool_families.json"

# Everything that changes what a record says. Part of the signature.
SETTINGS = {
    "schema": 3,     # 2: fixed keys everywhere; 3: origin of pages and documents
    "hints": "hints-3",
    "line_min_len_pt": 8.0,      # a stroke shorter than this is a mark, not a rule
    "line_max_thick_pt": 2.0,    # a filled rectangle thinner than this is a rule
    "max_images_listed": 100,    # per page; the counts always cover all of them
    "max_annots_listed": 200,
    "annot_text_chars": 4000,
    "widget_value_chars": 4000,
    "text_preview_chars": 300,
    "xmp_chars": 20000,
}

# Closed sets, so every record has the same shape and a table of them has the
# same columns. Anything open-ended (font names, filters, annotation types) is
# a list of {name, n} instead of a dict keyed by the name.
CHAR_CLASSES = ("letter", "digit", "space", "punct", "control", "private_use",
                "unassigned", "replacement", "null")
DIRECTIONS = {"0": "d0", "90": "d90", "180": "d180", "270": "d270", "other": "other"}
PATH_TYPES = {"f": "fill", "s": "stroke", "fs": "fill_stroke", "clip": "clip"}
PATH_ITEMS = {"l": "line", "re": "rect", "qu": "quad", "c": "curve"}
PAGE_KINDS = ("digital", "scan", "image_with_text", "vector_drawing", "sparse", "blank", "error")
TEXT_LAYERS = ("sound", "suspect", "hidden_ocr", "none", "error")


def fixed(counter, keys) -> dict:
    """A count per key, every key present; keys may be a tuple or a rename map."""
    if isinstance(keys, dict):
        out = {v: 0 for v in keys.values()}
        for k, n in counter.items():
            out[keys.get(k, "other")] = out.get(keys.get(k, "other"), 0) + n
        return out
    return {k: counter.get(k, 0) for k in keys}


def named_counts(counter, label="name") -> list[dict]:
    return [{label: k, "n": n} for k, n in counter.most_common()]

# A simple font in one of these maps its codes to Unicode without a ToUnicode
# table; anything else (Identity-H, a custom Differences array, Type3) cannot.
SIMPLE_ENCODINGS = {"WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding",
                    "PDFDocEncoding", "MacExpertEncoding"}


# --------------------------------------------------------------------------
# small helpers







def rect_list(rect) -> list:
    return [r(v, 1) for v in tuple(rect)]




def union_area(rects: list[tuple], bound: tuple) -> float:
    """Area covered by axis-aligned rectangles, clipped to `bound`."""
    bx0, by0, bx1, by1 = bound
    rs = []
    for x0, y0, x1, y1 in rects:
        x0, x1 = max(min(x0, x1), bx0), min(max(x0, x1), bx1)
        y0, y1 = max(min(y0, y1), by0), min(max(y0, y1), by1)
        if x1 > x0 and y1 > y0:
            rs.append((x0, y0, x1, y1))
    if len(rs) > 300:     # coordinate compression is cubic; past this, an upper bound
        return min(sum((a[2] - a[0]) * (a[3] - a[1]) for a in rs), (bx1 - bx0) * (by1 - by0))
    xs = sorted({v for a in rs for v in (a[0], a[2])})
    total = 0.0
    for xa, xb in zip(xs, xs[1:]):
        spans = sorted((a[1], a[3]) for a in rs if a[0] <= xa and a[2] >= xb)
        covered, cur0, cur1 = 0.0, None, None
        for s0, s1 in spans:
            if cur1 is None or s0 > cur1:
                if cur1 is not None:
                    covered += cur1 - cur0
                cur0, cur1 = s0, s1
            else:
                cur1 = max(cur1, s1)
        if cur1 is not None:
            covered += cur1 - cur0
        total += covered * (xb - xa)
    return total


def char_class(cp: int) -> str:
    if cp == 0xFFFD:
        return "replacement"
    if cp == 0:
        return "null"
    ch = chr(cp)
    cat = unicodedata.category(ch)
    if cat.startswith("L"):
        return "letter"
    if cat.startswith("N"):
        return "digit"
    if ch.isspace() or cat.startswith("Z"):
        return "space"
    if cat.startswith(("P", "S")):
        return "punct"
    if cat == "Co":
        return "private_use"
    if cat == "Cn":
        return "unassigned"
    return "control"


def direction_bucket(d) -> str:
    """Span writing direction as the angle it runs at, snapped to 0/90/180/270."""
    dx, dy = d
    ang = math.degrees(math.atan2(-dy, dx)) % 360
    for target in (0, 90, 180, 270, 360):
        if abs(ang - target) <= 2:
            return str(target % 360)
    return "other"


def mupdf_warnings(pymupdf) -> list[str]:
    text = pymupdf.TOOLS.mupdf_warnings(reset=True)
    return [l for l in text.splitlines() if l.strip()] if text else []


# --------------------------------------------------------------------------
# what made the file: tools, XMP, origin

_TOOL_RULES = None


def tool_rules() -> tuple[str, list]:
    global _TOOL_RULES
    if _TOOL_RULES is None:
        spec = json.loads(TOOL_RULES_PATH.read_text())
        _TOOL_RULES = (spec["version"], [
            (rule["family"], rule["kind"], re.compile(rule["pattern"], re.IGNORECASE))
            for rule in spec["rules"]])
    return _TOOL_RULES


def tool_version(raw: str, after: int) -> str | None:
    """The version in a tool string: a number after the family name if there is
    one, else before it. `64-bit` is an architecture, not a version. (Scanners
    get none: the number in `RICOH IM C3000` is a model.)"""
    nums = [m for m in re.finditer(r"\d+(?:\.\d+)*", raw) if not raw[m.end():].startswith("-bit")]
    later = [m for m in nums if m.start() >= after]
    pick = (later or nums or [None])[0]
    return pick.group(0) if pick else None


def classify_tool(raw) -> dict | None:
    """A tool string as family, kind and version, by the first rule that matches."""
    if raw is None:
        return None
    raw = str(raw).replace("\x00", "").strip()
    if not raw:
        return None
    for family, kind, rx in tool_rules()[1]:
        m = rx.search(raw)
        if m:
            return {"raw": raw, "family": family, "kind": kind,
                    "version": None if kind == "scanner" else tool_version(raw, m.end())}
    return {"raw": raw, "family": None, "kind": "unknown", "version": None}


XMP_NS = {
    "http://ns.adobe.com/xap/1.0/": "xmp", "http://ns.adobe.com/pdf/1.3/": "pdf",
    "http://purl.org/dc/elements/1.1/": "dc", "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/pdfx/1.3/": "pdfx", "http://www.aiim.org/pdfa/ns/id/": "pdfaid",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
}


def _qname(tag: str) -> str:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return f"{XMP_NS.get(ns, ns)}:{local}"
    return tag


def parse_xmp(text) -> dict | None:
    """Simple XMP properties as {prefix:Name: value}, and the edit history as
    a list of events. Values in rdf:Seq/Bag/Alt are joined with ' | '."""
    if not text:
        return None
    body = text.split("...[+")[0]
    start, end = body.find("<x:xmpmeta"), body.rfind("</x:xmpmeta>")
    if start < 0:
        start, end = body.find("<rdf:RDF"), body.rfind("</rdf:RDF>")
        tail = "</rdf:RDF>"
    else:
        tail = "</x:xmpmeta>"
    if start < 0 or end < 0:
        return {"error": "no XMP packet found"}
    try:
        root = ET.fromstring(body[start:end + len(tail)])
    except ET.ParseError as e:
        return {"error": f"unparseable: {e}"}
    fields, history = {}, []
    rdf = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    for desc in root.iter(rdf + "Description"):
        for k, v in desc.attrib.items():
            q = _qname(k)
            if not q.startswith("rdf:"):
                fields[q] = v
        for child in desc:
            q = _qname(child.tag)
            if q == "xmpMM:History":
                for li in child.iter(rdf + "li"):
                    ev = {_qname(k).split(":", 1)[1]: v for k, v in li.attrib.items() if not _qname(k).startswith("rdf:")}
                    ev.update({_qname(c.tag).split(":", 1)[1]: (c.text or "").strip() for c in li})
                    history.append(ev)
                continue
            items = [(li.text or "").strip() for li in child.iter(rdf + "li") if (li.text or "").strip()]
            if items:
                fields[q] = " | ".join(items)
            elif (child.text or "").strip():
                fields[q] = child.text.strip()
            elif child.attrib:
                fields[q] = " ".join(f"{_qname(k)}={v}" for k, v in child.attrib.items())
    return {"fields": fields, "history": history}


COMMON_SCAN_DPI = (100, 150, 200, 240, 300, 400, 600, 1200)
PAGE_ORIGINS = ("digital", "scanned", "scanned_ocr", "raster", "image_with_text", "blank", "unknown")


def page_origin(p: dict, doc_from_scanner: bool) -> tuple[str, list[str]]:
    """Whether a page was made digitally or captured as an image, and why we think so.

    digital          text or vector content drawn by software, no page-sized image
    scanned          a page-sized image, no text, with signs of a scanner
    raster           a page-sized image, no text, and nothing to say it was scanned
    scanned_ocr      a page-sized image with a hidden text layer from OCR
    image_with_text  a page-sized image with visible digital text over it
    blank / unknown
    """
    h, t, im, dr = (p.get(k) or {} for k in ("hints", "text", "images", "drawings"))
    if not h:
        return "unknown", ["no hints"]
    ev = []
    vis, inv = t.get("chars_visible", 0), t.get("chars_invisible", 0)
    cover = im.get("largest_cover") or 0
    imgs = im.get("list") or []
    big = max(imgs, key=lambda i: i.get("cover") or 0, default=None)
    if cover >= 0.8 and big:
        ev.append(f"page_image:{cover:.2f}")
        filt, bpc, dpi = big.get("filter"), big.get("bpc"), (big.get("dpi") or [None])[0]
        if filt:
            ev.append(f"filter:{filt}")
        if bpc == 1 or filt in ("JBIG2Decode", "CCITTFaxDecode"):
            ev.append("bilevel")
        if dpi:
            ev.append(f"dpi:{dpi:.0f}")
            if any(abs(dpi - d) <= d * 0.02 for d in COMMON_SCAN_DPI):
                ev.append("scanner_dpi")
    if inv:
        ev.append(f"hidden_chars:{inv}")
    if vis:
        ev.append(f"visible_chars:{vis}")
    if doc_from_scanner:
        ev.append("document_from_scanner_or_ocr")

    if h.get("kind") == "blank":
        return "blank", ev
    if h.get("text_layer") == "hidden_ocr" and (cover >= 0.8 or (im.get("cover_union") or 0) >= 0.5):
        return "scanned_ocr", ev
    if cover >= 0.8:
        if vis < 50:
            scanner = "bilevel" in ev or "scanner_dpi" in ev or doc_from_scanner
            return ("scanned" if scanner else "raster"), ev
        return "image_with_text", ev
    if h.get("kind") in ("digital", "vector_drawing") or vis > 0 or dr.get("paths", 0) >= 5:
        return "digital", ev
    return "unknown", ev


def document_origin(rec: dict) -> dict:
    pdf = rec.get("pdf") or {}
    md = pdf.get("metadata") or {}
    xmp = pdf.get("xmp_parsed") or {}
    xf = xmp.get("fields") or {}
    tools = {
        "producer": classify_tool(md.get("producer")),
        "creator": classify_tool(md.get("creator")),
        "xmp_creator_tool": classify_tool(xf.get("xmp:CreatorTool")),
        "xmp_producer": classify_tool(xf.get("pdf:Producer")),
    }
    history = []
    for ev in xmp.get("history") or []:
        history.append({**ev, "agent": classify_tool(ev.get("softwareAgent"))})
    every = [t for t in tools.values() if t] + [h["agent"] for h in history if h.get("agent")]
    first = lambda kind: next((t["family"] for t in every if t["kind"] == kind), None)
    ocr_tool, scanner = first("ocr"), first("scanner")
    edited_scan = any("Scanned" in (h.get("action") or "") for h in history)

    pages = rec.get("pages") or []
    counts = collections.Counter()
    for p in pages:
        o, ev = page_origin(p, bool(scanner or ocr_tool))
        p.setdefault("hints", {})["origin"] = o
        p["hints"]["origin_evidence"] = ev
        counts[o] += 1
    dig, iwt = counts["digital"], counts["image_with_text"]
    scan_like = counts["scanned"] + counts["raster"] + counts["scanned_ocr"]
    if not dig + iwt + scan_like:
        made_as = "empty" if not pages else "unknown"
    elif not scan_like and not iwt:
        made_as = "born_digital"
    elif not dig and not iwt:
        made_as = "scanned_ocr" if counts["scanned_ocr"] * 2 >= scan_like else "scanned"
    else:
        made_as = "mixed"
    return {
        "rules": tool_rules()[0],
        "tools": tools,
        "history": history,
        "ocr_tool": ocr_tool,
        "scanner": scanner,
        "edited_scanned_doc": edited_scan,
        "pdfa": xf.get("pdfaid:part") and f"PDF/A-{xf.get('pdfaid:part')}{(xf.get('pdfaid:conformance') or '').upper()}",
        "made_as": made_as,
        "pages_by_origin": fixed(counts, PAGE_ORIGINS),
        "digital_share": r(dig / (dig + iwt + scan_like), 4) if dig + iwt + scan_like else None,
    }


# --------------------------------------------------------------------------
# per page

def page_text(page) -> dict:
    classes = {"visible": collections.Counter(), "invisible": collections.Counter()}
    directions = collections.Counter()
    font_chars = collections.Counter()
    sizes = []
    spans = 0
    span_area = 0.0
    pieces = []
    for sp in page.get_texttrace():
        chars = sp.get("chars") or ()
        if not chars:
            continue
        spans += 1
        layer = "invisible" if sp.get("type") == 3 or (sp.get("opacity") or 1) == 0 else "visible"
        cnt = classes[layer]
        s = []
        for c in chars:
            cp = c[0]
            cnt[char_class(cp)] += 1
            if cp > 0:
                s.append(chr(cp))
        n = len(chars)
        directions[direction_bucket(sp["dir"])] += n
        font_chars[sp.get("font") or "?"] += n
        sizes.append((sp.get("size") or 0, n))
        x0, y0, x1, y1 = sp["bbox"]
        span_area += max(x1 - x0, 0) * max(y1 - y0, 0)
        pieces.append("".join(s))

    text = " ".join(" ".join(pieces).split())
    words = text.split()
    real_words = sum(1 for w in words if sum(ch.isalpha() for ch in w) >= 3)
    # MuPDF's own word split, not the text joined from spans: a font drawing each glyph on its
    # own reads as single characters there (2680246), where the joined spans run together
    alnum = [w[4] for w in page.get_text("words") if any(ch.isalnum() for ch in w[4])]
    n_vis = sum(classes["visible"].values())
    n_inv = sum(classes["invisible"].values())
    allc = classes["visible"] + classes["invisible"]
    letters, digits = allc["letter"], allc["digit"]
    bad = allc["replacement"] + allc["control"] + allc["private_use"] + allc["unassigned"] + allc["null"]
    median_size = None
    if sizes:
        sizes.sort()
        half, acc = sum(n for _, n in sizes) / 2, 0
        for sz, n in sizes:
            acc += n
            if acc >= half:
                median_size = r(sz, 1)
                break
    return {
        "spans": spans,
        "chars_visible": n_vis,
        "chars_invisible": n_inv,
        "classes_visible": fixed(classes["visible"], CHAR_CLASSES),
        "classes_invisible": fixed(classes["invisible"], CHAR_CLASSES),
        "bad_char_share": r(bad / (n_vis + n_inv), 4) if n_vis + n_inv else None,
        "letter_share": r(letters / (letters + digits), 4) if letters + digits else None,
        "words": len(words),
        "real_words": real_words,
        "alnum_words": len(alnum),
        "single_char_share": r(sum(1 for w in alnum if len(w) == 1) / len(alnum), 4) if alnum else None,
        "directions": fixed(directions, DIRECTIONS),
        "fonts": named_counts(font_chars, "font"),
        "median_font_size": median_size,
        "span_area_sum": r(span_area, 1),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest() if text else None,
        "preview": clip(text, SETTINGS["text_preview_chars"]) if text else None,
    }


def page_images(page, page_area, stream_hashes: dict) -> dict:
    """Placements from get_image_info (cheap: nothing is decoded), matched to the
    image objects by pixel size, and each object hashed once per document from
    its encoded bytes. The encoding filter says what made it: DCT is JPEG,
    JBIG2 and CCITTFax are black-and-white scans, JPX is JPEG 2000."""
    doc = page.parent
    objects = page.get_images(full=True)
    by_shape = collections.defaultdict(list)
    for xref, _smask, w, h, bpc, cs, _alt, _name, filt, *_ in objects:
        by_shape[(w, h, bpc)].append((xref, filt))
        if xref not in stream_hashes:
            try:
                raw = doc.xref_stream_raw(xref)
                stream_hashes[xref] = hashlib.sha256(raw).hexdigest() if raw else None
            except Exception:
                stream_hashes[xref] = None
    infos = page.get_image_info()
    listed, rects, filters = [], [], collections.Counter()
    largest = 0.0
    for im in infos:
        x0, y0, x1, y1 = im["bbox"]
        area = max(x1 - x0, 0) * max(y1 - y0, 0)
        rects.append((x0, y0, x1, y1))
        largest = max(largest, area)
        cands = by_shape.get((im.get("width"), im.get("height"), im.get("bpc")), [])
        distinct = {c[0] for c in cands}
        if len(distinct) == 1:
            xref, filt = cands[0]
            filt = filt or "none"     # stored uncompressed
        else:
            # no image object of that size: an inline image, drawn from the
            # content stream itself; several: it cannot be told which
            xref, filt = None, ("inline" if not distinct else "ambiguous")
        filters[filt or "none"] += 1
        a, b, c, d = im["transform"][:4]
        shown_w_in, shown_h_in = math.hypot(a, b) / 72, math.hypot(c, d) / 72
        if len(listed) < SETTINGS["max_images_listed"]:
            listed.append({
                "xref": xref,
                "filter": filt,
                "bbox": rect_list(im["bbox"]),
                "cover": r(area / page_area, 4),
                "px": [im.get("width"), im.get("height")],
                "dpi": [r(im["width"] / shown_w_in, 0) if shown_w_in else None,
                        r(im["height"] / shown_h_in, 0) if shown_h_in else None],
                "colorspace": im.get("cs-name"),
                "bpc": im.get("bpc"),
                "has_mask": im.get("has-mask"),
                "bytes": im.get("size"),
                "stream_sha256": stream_hashes.get(xref) if xref else None,
                "rotated": abs(b) > 1e-3 or abs(c) > 1e-3,
            })
    return {
        "count": len(infos),
        "objects": len(objects),
        "filters": named_counts(filters, "filter"),
        "cover_union": r(union_area(rects, tuple(page.rect)) / page_area, 4) if rects else 0.0,
        "largest_cover": r(largest / page_area, 4),
        "list": listed,
    }


def page_drawings(page, page_area) -> dict:
    min_len, max_thick = SETTINGS["line_min_len_pt"], SETTINGS["line_max_thick_pt"]
    items = collections.Counter()
    kinds = collections.Counter()
    hlines = vlines = diag = rule_rects = boxes = 0
    rects = []
    paths = page.get_cdrawings()
    for p in paths:
        kinds[p.get("type") or "?"] += 1
        pr = p.get("rect")
        if pr:
            rects.append(tuple(pr))
        for it in p.get("items", ()):
            op = it[0]
            items[op] += 1
            if op == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                dx, dy = abs(x1 - x0), abs(y1 - y0)
                if max(dx, dy) >= min_len:
                    if dy <= 0.5:
                        hlines += 1
                    elif dx <= 0.5:
                        vlines += 1
                    else:
                        diag += 1
            elif op == "re":
                x0, y0, x1, y1 = it[1]
                w, h = abs(x1 - x0), abs(y1 - y0)
                if h <= max_thick and w >= min_len:
                    hlines += 1; rule_rects += 1
                elif w <= max_thick and h >= min_len:
                    vlines += 1; rule_rects += 1
                elif w >= min_len and h >= min_len:
                    boxes += 1
    return {
        "paths": len(paths),
        "path_types": fixed(kinds, PATH_TYPES),
        "items": fixed(items, PATH_ITEMS),
        "hlines": hlines, "vlines": vlines, "diagonal_lines": diag,
        "rule_rects": rule_rects, "boxes": boxes,
        "bbox_area_sum_share": r(sum(max(x1 - x0, 0) * max(y1 - y0, 0)
                                     for x0, y0, x1, y1 in rects) / page_area, 4),
    }


def page_annots(page) -> dict:
    types = collections.Counter()
    listed = []
    for a in page.annots():
        t = a.type[1]
        types[t] += 1
        if len(listed) < SETTINGS["max_annots_listed"]:
            info = a.info or {}
            listed.append({
                "type": t,
                "rect": rect_list(a.rect),
                "content": clip(info.get("content"), SETTINGS["annot_text_chars"]) or None,
                "title": info.get("title") or None,
                "subject": info.get("subject") or None,
                "created": info.get("creationDate") or None,
                "modified": info.get("modDate") or None,
            })
    widgets = []
    for w in page.widgets():
        val = w.field_value
        widgets.append({
            "type": w.field_type_string,
            "name": w.field_name,
            "label": w.field_label or None,
            "value": clip(val, SETTINGS["widget_value_chars"]) if val not in (None, "") else None,
            "on_state": w.on_state() if w.field_type_string in ("CheckBox", "RadioButton") else None,
            "read_only": bool(w.field_flags & 1),
            "rect": rect_list(w.rect),
        })
    links = page.get_links()
    return {
        "annot_types": named_counts(types, "type"),
        "annots": listed,
        "widgets": widgets,
        "widgets_filled": sum(1 for w in widgets if w["value"] not in (None, "Off")),
        "links": len(links),
        "links_uri": sorted({l["uri"] for l in links if l.get("uri")})[:50],
    }


def page_hints(p: dict) -> dict:
    """Labels derived from the facts above; rules, versioned as SETTINGS['hints']."""
    t, im, dr = p.get("text") or {}, p.get("images") or {}, p.get("drawings") or {}
    vis, inv = t.get("chars_visible", 0), t.get("chars_invisible", 0)
    chars = vis + inv
    cover = im.get("largest_cover") or 0
    paths = dr.get("paths", 0)
    bad = t.get("bad_char_share") or 0
    ls = t.get("letter_share")

    if chars < 10:
        layer = "none"
    elif inv >= 0.5 * chars:
        layer = "hidden_ocr"
    elif bad > 0.05 or (ls is not None and ls < 0.3 and t.get("real_words", 0) < 5 and chars > 50):
        layer = "suspect"
    elif (t.get("alnum_words") or 0) >= 50 and (t.get("single_char_share") or 0) >= 0.7:
        # Every word one character: a font whose codes are not its letters (2680246's Type3
        # fonts read "/ 0 1 2 3 4 0 5 6"), or junk OCR (4088410 p9). On both samples' 3,593
        # pages of 50+ words no other page passes 0.55; a table of digits reaches 0.54.
        layer = "suspect"
    else:
        layer = "sound"

    if chars < 10 and im.get("count", 0) == 0 and paths < 5:
        kind = "blank"
    elif cover >= 0.8 and vis < 50:
        kind = "scan"
    elif paths >= 5000 and vis < 2000:
        kind = "vector_drawing"
    elif cover >= 0.8:
        kind = "image_with_text"
    elif vis >= 50:
        kind = "digital"
    else:
        kind = "sparse"

    dirs = t.get("directions") or {}
    total_dir = sum(dirs.values())
    long_in = max(p["width_pt"], p["height_pt"]) / 72
    a = p.get("annotations") or {}
    return {
        "text_layer": layer,
        "kind": kind,
        "rotated_text_share": r(1 - dirs.get("0", 0) / total_dir, 3) if total_dir else None,
        "large_sheet": long_in > 17.5,
        "ruled": (dr.get("hlines", 0) >= 3 and dr.get("vlines", 0) >= 3),
        "has_form_values": a.get("widgets_filled", 0) > 0,
        "has_annotations": bool(a.get("annot_types")),
    }


def profile_page(doc, i: int, pymupdf, stream_hashes: dict) -> dict:
    t0 = time.perf_counter()
    rec = {"index": i, "errors": []}
    try:
        page = doc.load_page(i)
    except Exception as e:
        rec["errors"].append({"step": "load", "error": repr(e)})
        rec["warnings"] = mupdf_warnings(pymupdf)
        rec["ms"] = r((time.perf_counter() - t0) * 1000, 1)
        return rec
    try:
        rect = page.rect
        rec.update({
            "width_pt": r(rect.width, 1), "height_pt": r(rect.height, 1),
            "rotation": page.rotation,
            "mediabox": rect_list(page.mediabox),
            "cropbox": rect_list(page.cropbox) if page.cropbox != page.mediabox else None,
        })
        area = max(rect.width * rect.height, 1.0)
    except Exception as e:
        rec["errors"].append({"step": "geometry", "error": repr(e), "trace": traceback.format_exc(limit=3)})
        rec["warnings"] = mupdf_warnings(pymupdf)
        rec["ms"] = r((time.perf_counter() - t0) * 1000, 1)
        return rec
    try:
        rec["label"] = page.get_label() or None
    except Exception as e:
        # PyMuPDF raises when the label ranges do not start at page 0; the
        # page simply has no label then, which is what a PDF reader shows.
        rec["label"] = None
        rec["label_error"] = repr(e)
    timings = {}
    for key, fn in (("text", lambda: page_text(page)),
                    ("images", lambda: page_images(page, area, stream_hashes)),
                    ("drawings", lambda: page_drawings(page, area)),
                    ("annotations", lambda: page_annots(page))):
        s = time.perf_counter()
        try:
            rec[key] = fn()
        except Exception as e:
            rec["errors"].append({"step": key, "error": repr(e),
                                  "trace": traceback.format_exc(limit=3)})
        timings[key] = r((time.perf_counter() - s) * 1000, 1)
    try:
        rec["hints"] = page_hints(rec)
    except Exception as e:
        rec["errors"].append({"step": "hints", "error": repr(e)})
    rec["warnings"] = mupdf_warnings(pymupdf)
    rec["step_ms"] = timings
    rec["ms"] = r((time.perf_counter() - t0) * 1000, 1)
    return rec


# --------------------------------------------------------------------------
# per document

def doc_fonts(doc) -> list[dict]:
    fonts = {}
    for i in range(doc.page_count):
        try:
            entries = doc.get_page_fonts(i, full=True)
        except Exception:
            continue
        for xref, ext, ftype, basefont, name, encoding, *_ in entries:
            f = fonts.get(xref)
            if f is None:
                try:
                    tu = doc.xref_get_key(xref, "ToUnicode")[0] != "null"
                except Exception:
                    tu = None
                f = fonts[xref] = {"xref": xref, "basefont": basefont, "type": ftype,
                                   "encoding": encoding, "embedded": ext not in ("n/a", ""),
                                   "file_ext": ext, "to_unicode": tu,
                                   "unmappable_risk": ftype == "Type3" or (tu is False and encoding not in SIMPLE_ENCODINGS),
                                   "subset": len(basefont) > 7 and basefont[6] == "+",
                                   "pages": 0}
            f["pages"] += 1
    return sorted(fonts.values(), key=lambda f: -f["pages"])


def doc_embedded(doc) -> list[dict]:
    out = []
    for n in range(doc.embfile_count()):
        info = doc.embfile_info(n)
        e = {k: info.get(k) for k in ("name", "filename", "ufilename", "desc", "size",
                                      "length", "creationDate", "modDate")}
        try:
            data = doc.embfile_get(n)
            e["sha256"] = hashlib.sha256(data).hexdigest()
            e["magic"] = data[:8].hex()
        except Exception as ex:
            e["error"] = repr(ex)
        out.append(e)
    return out


def doc_catalog(doc) -> dict:
    cat = doc.pdf_catalog()
    keys = doc.xref_get_keys(cat)
    out = {"keys": list(keys)}
    for k in ("Lang", "PageMode", "PageLayout", "Version"):
        if k in keys:
            out[k] = doc.xref_get_key(cat, k)[1]
    if "AcroForm" in keys:
        t, v = doc.xref_get_key(cat, "AcroForm")
        af = int(v.split()[0]) if t == "xref" else None
        if af:
            afkeys = doc.xref_get_keys(af)
            out["acroform_keys"] = list(afkeys)
            out["xfa"] = "XFA" in afkeys
    return out


def profile_document(path: Path, doc_id: str, meta: dict) -> dict:
    """The whole record for one PDF. Never raises: failures are the record."""
    import pymupdf
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    mupdf_warnings(pymupdf)  # clear anything left from the last document

    t0 = time.perf_counter()
    reasons: list[str] = []
    rec = {"doc_id": doc_id, **meta, "started_at": utcnow(), "worker_pid": os.getpid()}
    st = path.stat()
    s = time.perf_counter()
    with open(path, "rb") as f:
        head = f.read(1024)
    rec["file"] = {"path": str(path), "name": path.name, "size": st.st_size,
                   "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(path),
                   "pdf_header_offset": head.find(b"%PDF-"), "head_hex": head[:16].hex()}
    timings = {"hash": r((time.perf_counter() - s) * 1000, 1)}

    def finish(status):
        rec["status"] = status
        rec["reasons"] = sorted(set(reasons))
        rec["step_ms"] = timings
        rec["finished_at"] = utcnow()
        rec["seconds"] = r(time.perf_counter() - t0, 3)
        return rec

    if st.st_size == 0:
        reasons.append("empty_file")
        return finish("failed")
    if rec["file"]["pdf_header_offset"] < 0:
        reasons.append("not_a_pdf")   # e.g. an HTML error page saved as .pdf
        return finish("failed")

    s = time.perf_counter()
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        reasons.append("open_failed")
        rec["error"] = {"step": "open", "error": repr(e), "trace": traceback.format_exc(limit=5)}
        rec["open_warnings"] = mupdf_warnings(pymupdf)
        return finish("failed")
    timings["open"] = r((time.perf_counter() - s) * 1000, 1)

    pdf = {"open_warnings": mupdf_warnings(pymupdf), "errors": []}
    rec["pdf"] = pdf
    if doc.needs_pass:
        if not doc.authenticate(""):
            reasons.append("password_required")
            doc.close()
            return finish("failed")
        pdf["opened_with_empty_password"] = True

    def step(name, fn):
        s = time.perf_counter()
        try:
            return fn()
        except Exception as e:
            pdf["errors"].append({"step": name, "error": repr(e), "trace": traceback.format_exc(limit=3)})
            reasons.append(f"doc_step_failed:{name}")
            return None
        finally:
            timings[name] = r((time.perf_counter() - s) * 1000, 1)

    pdf.update({
        "page_count": doc.page_count,
        "metadata": doc.metadata,
        "is_encrypted": doc.is_encrypted,
        "permissions": doc.permissions,
        "is_repaired": doc.is_repaired,
        "is_fast_webaccess": doc.is_fast_webaccess,
        "xref_length": doc.xref_length(),
        "form_fields": doc.is_form_pdf or 0,
    })
    if doc.is_repaired:
        reasons.append("repaired_on_open")
    pdf["catalog"] = step("catalog", lambda: doc_catalog(doc))
    pdf["xmp"] = step("xmp", lambda: clip(doc.get_xml_metadata() or None, SETTINGS["xmp_chars"]))
    pdf["xmp_parsed"] = step("xmp_parse", lambda: parse_xmp(pdf["xmp"]))
    pdf["toc"] = step("toc", lambda: doc.get_toc(simple=True))
    pdf["page_labels"] = step("page_labels", lambda: doc.get_page_labels())
    pdf["layers"] = step("layers", lambda: [{"xref": x, **v} for x, v in (doc.get_ocgs() or {}).items()])
    pdf["embedded_files"] = step("embedded_files", lambda: doc_embedded(doc))
    pdf["fonts"] = step("fonts", lambda: doc_fonts(doc))
    pdf["warnings"] = mupdf_warnings(pymupdf)

    s = time.perf_counter()
    pages, stream_hashes = [], {}
    for i in range(doc.page_count):
        pages.append(profile_page(doc, i, pymupdf, stream_hashes))
        if i % 50 == 49:
            pymupdf.TOOLS.store_shrink(100)
    timings["pages"] = r((time.perf_counter() - s) * 1000, 1)
    rec["pages"] = pages
    doc.close()
    pymupdf.TOOLS.store_shrink(100)

    failed_pages = [p["index"] for p in pages if p["errors"]]
    if failed_pages:
        steps = {e["step"] for p in pages for e in p["errors"]}
        reasons += [f"page_step_failed:{st}" for st in steps]
    if any(p.get("warnings") for p in pages) or pdf["open_warnings"]:
        reasons.append("mupdf_warnings")

    rec["origin"] = step("origin", lambda: document_origin(rec))
    rec["summary"] = summarize(rec)
    if not pages:
        reasons.append("no_pages")
        return finish("failed")
    if failed_pages or pdf["errors"]:
        rec["failed_pages"] = failed_pages
        return finish("partial")
    return finish("ok")


def summarize(rec: dict) -> dict:
    pages = rec.get("pages") or []
    kinds, layers = collections.Counter(), collections.Counter()
    for p in pages:
        h = p.get("hints") or {}
        kinds[h.get("kind", "error")] += 1
        layers[h.get("text_layer", "error")] += 1
    fonts = (rec.get("pdf") or {}).get("fonts") or []
    return {
        "pages": len(pages),
        "kinds": fixed(kinds, PAGE_KINDS),
        "text_layers": fixed(layers, TEXT_LAYERS),
        "rotated_pages": sum(1 for p in pages if p.get("rotation")),
        "rotated_text_pages": sum(1 for p in pages if ((p.get("hints") or {}).get("rotated_text_share") or 0) > 0.2),
        "large_sheets": sum(1 for p in pages if (p.get("hints") or {}).get("large_sheet")),
        "ruled_pages": sum(1 for p in pages if (p.get("hints") or {}).get("ruled")),
        "form_values": sum((p.get("annotations") or {}).get("widgets_filled", 0) for p in pages),
        "annotations": sum(t["n"] for p in pages for t in (p.get("annotations") or {}).get("annot_types") or []),
        "embedded_files": len((rec.get("pdf") or {}).get("embedded_files") or []),
        "fonts_unmappable_risk": sum(1 for f in fonts if f.get("unmappable_risk")),
        "type3_fonts": sum(1 for f in fonts if f.get("type") == "Type3"),
        "producer": ((rec.get("pdf") or {}).get("metadata") or {}).get("producer") or None,
        "made_as": (rec.get("origin") or {}).get("made_as"),
        "origins": (rec.get("origin") or {}).get("pages_by_origin"),
    }


def describe(ev: dict) -> str:
    """One readable line per document."""
    s = ev.get("summary") or {}
    bits = [f"{ev['status']:<7}", f"{ev['doc_id']:<12}", f"{s.get('pages', '-'):>5}p",
            f"{ev.get('seconds') or 0:7.2f}s"]
    if s:
        kinds = " ".join(f"{k}:{v}" for k, v in (s.get("kinds") or {}).items() if v)
        layers = " ".join(f"{k}:{v}" for k, v in (s.get("text_layers") or {}).items() if v)
        bits.append(f"{s.get('made_as') or '?'} [{kinds}] text[{layers}]")
        extras = [f"{k}:{s[k]}" for k in ("form_values", "annotations", "embedded_files", "large_sheets",
                                          "rotated_pages", "fonts_unmappable_risk") if s.get(k)]
        if extras:
            bits.append(" ".join(extras))
    if ev.get("reasons"):
        bits.append("why=" + ",".join(ev["reasons"]))
    if ev.get("error"):
        bits.append("error=" + clip(ev["error"], 200))
    return "  ".join(bits)








# --------------------------------------------------------------------------
# the stage

class Tier0(Stage):
    name = STAGE
    code_files = [Path(__file__).resolve(), HERE / "common.py"]
    data_files = [TOOL_RULES_PATH]
    packages = ["pymupdf"]
    settings = SETTINGS
    worker_module = "tier0"

    def environment(self) -> dict:
        import pymupdf
        return {**base_environment(), "pymupdf": pymupdf.VersionBind, "mupdf": pymupdf.VersionFitz}

    def header_lines(self, env):
        return [f"pymupdf  {env['pymupdf']} (mupdf {env['mupdf']})"]

    def describe(self, ev):
        return describe(ev)


def process(path: Path, doc_id: str, meta: dict, ctx) -> dict:
    """A worker's job: the record for one PDF."""
    meta = {k: v for k, v in meta.items() if k in ("stage", "signature", "session", "code_sha256", "settings")}
    return profile_document(path, doc_id, meta)


def doc_out(output: Path, doc_id: str, sig: str) -> Path:
    return Tier0().doc_out(output, doc_id, sig)


def cmd_run(args) -> int:
    return run_stage(Tier0(), args)







def cmd_status(args) -> int:
    data = Path(args.runs)
    runs = runs_of(data, STAGE)
    if not runs:
        print(f"no runs under {data}")
        return 0
    for run_dir in runs:
        idx = load_index(run_dir)
        counts = collections.Counter(ev["status"] for ev in idx.values())
        reasons = collections.Counter(r_ for ev in idx.values() for r_ in ev.get("reasons") or [])
        kinds, layers = collections.Counter(), collections.Counter()
        pages = 0
        for ev in idx.values():
            s = ev.get("summary") or {}
            pages += s.get("pages", 0)
            kinds.update(s.get("kinds") or {})
            layers.update(s.get("text_layers") or {})
        sessions = sorted((run_dir / "sessions").glob("*.log"))
        print(f"run {run_dir.name}: {len(idx)} documents, {pages} pages, {len(sessions)} session(s)")
        print("  status  " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        if kinds:
            print("  pages   " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
            print("  text    " + ", ".join(f"{k} {v}" for k, v in layers.most_common()))
        for k, v in reasons.most_common(15):
            print(f"  {v:8}  {k}")
        failed = [ev for ev in idx.values() if ev["status"] == "failed"]
        for ev in failed[:args.show]:
            print("  failed  " + describe(ev))
    return 0


# --------------------------------------------------------------------------
# flatten: records -> Parquet

def _prefixed(prefix: str, d: dict | None) -> dict:
    return {f"{prefix}{k}": v for k, v in (d or {}).items()}


def origin_columns(o: dict, xf: dict) -> dict:
    cols = {"made_as": o.get("made_as"), "digital_share": o.get("digital_share"),
            "ocr_tool": o.get("ocr_tool"), "scanner": o.get("scanner"),
            "edited_scanned_doc": o.get("edited_scanned_doc"), "pdfa": o.get("pdfa") or None,
            "tool_rules": o.get("rules"), "xmp_history_events": len(o.get("history") or []),
            **_prefixed("origin_", o.get("pages_by_origin")),
            "xmp_create_date": xf.get("xmp:CreateDate"), "xmp_modify_date": xf.get("xmp:ModifyDate"),
            "xmp_source_modified": xf.get("pdfx:SourceModified"), "xmp_company": xf.get("pdfx:Company")}
    for role, t in (o.get("tools") or {}).items():
        t = t or {}
        cols.update({f"{role}_family": t.get("family"), f"{role}_kind": t.get("kind"),
                     f"{role}_version": t.get("version")})
    cols["xmp_creator_tool_raw"] = ((o.get("tools") or {}).get("xmp_creator_tool") or {}).get("raw")
    return cols


def flatten_record(path: str) -> dict[str, list[dict]]:
    """One record as rows for each table. Every column is a scalar."""
    try:
        with gzip.open(path) as f:
            rec = json.load(f)
    except Exception as e:
        return {"unreadable": [{"path": path, "error": repr(e)}]}
    did = rec["doc_id"]
    pdf, f_, summ = rec.get("pdf") or {}, rec.get("file") or {}, rec.get("summary") or {}
    md, cat = pdf.get("metadata") or {}, pdf.get("catalog") or {}
    fonts = pdf.get("fonts") or []
    rows = collections.defaultdict(list)
    rows["documents"].append({
        "doc_id": did, "status": rec.get("status"), "reasons": ",".join(rec.get("reasons") or []),
        "error": (rec.get("error") or {}).get("error"),
        "seconds": rec.get("seconds"), "path": f_.get("path"), "size": f_.get("size"),
        "sha256": f_.get("sha256"), "pdf_header_offset": f_.get("pdf_header_offset"),
        "page_count": pdf.get("page_count"), "pdf_format": md.get("format"),
        "producer": md.get("producer"), "creator": md.get("creator"), "title": md.get("title"),
        "author": md.get("author"), "created": md.get("creationDate"), "modified": md.get("modDate"),
        "encryption": md.get("encryption"), "is_encrypted": pdf.get("is_encrypted"),
        "is_repaired": pdf.get("is_repaired"), "form_fields": pdf.get("form_fields"),
        "xfa": cat.get("xfa"), "lang": cat.get("Lang"),
        "toc_entries": len(pdf.get("toc") or []), "page_label_rules": len(pdf.get("page_labels") or []),
        "layers": len(pdf.get("layers") or []), "embedded_files": len(pdf.get("embedded_files") or []),
        "fonts": len(fonts), "fonts_unmappable_risk": summ.get("fonts_unmappable_risk"),
        "type3_fonts": summ.get("type3_fonts"), "form_values": summ.get("form_values"),
        "annotations": summ.get("annotations"), "large_sheets": summ.get("large_sheets"),
        "rotated_pages": summ.get("rotated_pages"), "ruled_pages": summ.get("ruled_pages"),
        **_prefixed("pages_", summ.get("kinds")), **_prefixed("text_", summ.get("text_layers")),
        "open_warnings": len(pdf.get("open_warnings") or []) + len(pdf.get("warnings") or []),
        "doc_errors": len(pdf.get("errors") or []), "failed_pages": len(rec.get("failed_pages") or []),
        "started_at": rec.get("started_at"), "finished_at": rec.get("finished_at"),
        "session": rec.get("session"), "signature": rec.get("signature"), "code_sha256": rec.get("code_sha256"),
        **origin_columns(rec.get("origin") or {}, (pdf.get("xmp_parsed") or {}).get("fields") or {}),
    })
    xmp = pdf.get("xmp_parsed") or {}
    for k, v in (xmp.get("fields") or {}).items():
        rows["xmp"].append({"doc_id": did, "key": k, "value": v})
    for i, ev in enumerate((rec.get("origin") or {}).get("history") or []):
        a = ev.get("agent") or {}
        rows["xmp_history"].append({"doc_id": did, "i": i, "action": ev.get("action"), "when": ev.get("when"),
                                    "agent": ev.get("softwareAgent"), "agent_family": a.get("family"),
                                    "agent_kind": a.get("kind"), "changed": ev.get("changed"),
                                    "parameters": ev.get("parameters")})
    for fo in fonts:
        rows["fonts"].append({"doc_id": did, **fo})
    for e in pdf.get("embedded_files") or []:
        rows["embedded_files"].append({"doc_id": did, **e})
    for lvl, title, page in pdf.get("toc") or []:
        rows["toc"].append({"doc_id": did, "level": lvl, "title": title, "page_no": page})
    for w in (pdf.get("open_warnings") or []) + (pdf.get("warnings") or []):
        rows["warnings"].append({"doc_id": did, "page_no": None, "warning": w})

    for p in rec.get("pages") or []:
        pno = p["index"] + 1
        t, im, dr, an, h = (p.get(k) or {} for k in ("text", "images", "drawings", "annotations", "hints"))
        imgs = im.get("list") or []
        biggest = max(imgs, key=lambda i: i.get("cover") or 0, default={})
        rows["pages"].append({
            "doc_id": did, "page_no": pno, "label": p.get("label"),
            "width_pt": p.get("width_pt"), "height_pt": p.get("height_pt"), "rotation": p.get("rotation"),
            "cropbox_differs": p.get("cropbox") is not None,
            **{f"hint_{k}": (",".join(v) if isinstance(v, list) else v) for k, v in h.items()},
            "chars_visible": t.get("chars_visible"), "chars_invisible": t.get("chars_invisible"),
            **_prefixed("vis_", t.get("classes_visible")), **_prefixed("inv_", t.get("classes_invisible")),
            "bad_char_share": t.get("bad_char_share"), "letter_share": t.get("letter_share"),
            "words": t.get("words"), "real_words": t.get("real_words"),
            **_prefixed("dir_", t.get("directions")),
            "fonts_used": len(t.get("fonts") or []),
            "main_font": (t.get("fonts") or [{}])[0].get("font"),
            "median_font_size": t.get("median_font_size"), "span_area_sum": t.get("span_area_sum"),
            "text_sha256": t.get("text_sha256"), "preview": t.get("preview"),
            "images": im.get("count"), "image_objects": im.get("objects"),
            "image_cover": im.get("cover_union"), "image_largest_cover": im.get("largest_cover"),
            "largest_image_dpi": (biggest.get("dpi") or [None])[0],
            "largest_image_filter": biggest.get("filter"),
            "paths": dr.get("paths"), **_prefixed("path_", dr.get("path_types")),
            **_prefixed("item_", dr.get("items")),
            **{k: dr.get(k) for k in ("hlines", "vlines", "diagonal_lines", "rule_rects", "boxes")},
            "drawing_area_share": dr.get("bbox_area_sum_share"),
            "annotations": sum(a["n"] for a in an.get("annot_types") or []),
            "widgets": len(an.get("widgets") or []), "widgets_filled": an.get("widgets_filled"),
            "links": an.get("links"),
            "warnings": len(p.get("warnings") or []), "errors": len(p.get("errors") or []),
            "error_steps": ",".join(e["step"] for e in p.get("errors") or []) or None,
            "ms": p.get("ms"), **_prefixed("ms_", p.get("step_ms")),
        })
        for fo in t.get("fonts") or []:
            rows["page_fonts"].append({"doc_id": did, "page_no": pno, "font": fo["font"], "chars": fo["n"]})
        for i, img in enumerate(imgs):
            (x0, y0, x1, y1), (pw, ph), (dx, dy) = img["bbox"], img["px"], img["dpi"]
            rows["images"].append({
                "doc_id": did, "page_no": pno, "i": i, "xref": img.get("xref"), "filter": img.get("filter"),
                "x0": x0, "y0": y0, "x1": x1, "y1": y1, "cover": img.get("cover"),
                "px_w": pw, "px_h": ph, "dpi_x": dx, "dpi_y": dy, "colorspace": img.get("colorspace"),
                "bpc": img.get("bpc"), "has_mask": img.get("has_mask"), "bytes": img.get("bytes"),
                "stream_sha256": img.get("stream_sha256"), "rotated": img.get("rotated")})
        for a in an.get("annots") or []:
            x0, y0, x1, y1 = a["rect"]
            rows["annotations"].append({"doc_id": did, "page_no": pno, **{k: v for k, v in a.items() if k != "rect"},
                                        "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        for w in an.get("widgets") or []:
            x0, y0, x1, y1 = w["rect"]
            rows["widgets"].append({"doc_id": did, "page_no": pno, **{k: v for k, v in w.items() if k != "rect"},
                                    "value": None if w.get("value") is None else str(w["value"]),
                                    "x0": x0, "y0": y0, "x1": x1, "y1": y1})
        for w in p.get("warnings") or []:
            rows["warnings"].append({"doc_id": did, "page_no": pno, "warning": w})
    return rows


def cmd_flatten(args) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq

    runs = runs_of(Path(args.runs), STAGE)
    if args.run:
        runs = [d for d in runs if d.name in (args.run, f"{STAGE}-{args.run}")]
    if not runs:
        print("no such run")
        return 2
    run_dir = runs[-1]
    sig = run_dir.name.split("-", 1)[1]
    index = load_index(run_dir)
    paths = [str(doc_out(Path(args.output), d, sig)) for d in sorted(index)]
    tmp = run_dir / "parquet.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.monotonic()
    totals = collections.Counter()
    part = 0
    with mp.get_context("spawn").Pool(args.workers) as pool:
        for start in range(0, len(paths), args.batch):
            batch = collections.defaultdict(list)
            for rows in pool.imap(flatten_record, paths[start:start + args.batch], chunksize=20):
                for table, rs in rows.items():
                    batch[table] += rs
            for table, rs in batch.items():
                if rs:
                    (tmp / table).mkdir(parents=True, exist_ok=True)
                    pq.write_table(pa.Table.from_pylist(rs), tmp / table / f"part-{part:05d}.parquet",
                                   compression="zstd")
                    totals[table] += len(rs)
            part += 1
            print(f"  {min(start + args.batch, len(paths))}/{len(paths)} documents", flush=True)
    out = run_dir / "parquet"
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    write_json_atomic(out / "manifest.json", {"built_at": utcnow(), "documents": len(paths),
                                              "rows": dict(totals), "seconds": r(time.monotonic() - t0, 1),
                                              "from_sessions": sorted(p.name for p in (run_dir / "sessions").glob("*.jsonl"))})
    print(f"{out}  ({time.monotonic() - t0:.0f}s)")
    for table, n in sorted(totals.items()):
        print(f"  {table:15} {n:>10,} rows")
    if totals["unreadable"]:
        print(f"  {totals['unreadable']} records could not be read: see {out}/unreadable/")
    return 1 if totals["unreadable"] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=str(DEFAULT_RUNS),
                    help="run provenance, logs and Parquet (default: %(default)s)")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help="per-document records go under <output>/<id>/convert/ (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="profile PDFs (default: every PDF in source/)")
    add_run_arguments(run)
    st = sub.add_parser("status", help="what every run holds")
    st.add_argument("--show", type=int, default=20, help="failed documents to list")
    fl = sub.add_parser("flatten", help="derive Parquet tables from a run's records")
    fl.add_argument("--run", help="signature of the run (default: the newest)")
    fl.add_argument("--workers", type=int, default=8)
    fl.add_argument("--batch", type=int, default=2000, help="documents per Parquet part")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "flatten": cmd_flatten}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
