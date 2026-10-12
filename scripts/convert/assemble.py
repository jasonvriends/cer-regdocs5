"""From readings to a document: evidence words, merged regions, words to regions, tables
chosen by the words they place, the DoclingDocument, Markdown, and the checks.

Pure functions over plain data (no models), so each step can be re-run from the Parquet
readings without the GPU.
"""
from __future__ import annotations

import collections
import re
import unicodedata

SETTINGS = {
    "nms_iou": 0.8,              # one model's boxes this alike are one detection with two labels
    "merge_iou": 0.5,            # different models' boxes this alike are one region
    "min_support_conf": 0.5,     # a region one model alone found is kept only at this score
    "pad_frac": 0.25,            # a word is in a box within min(1.5 pt, this share of the box's size)
    "pad_pt": 1.5,
    "ocr_extra_overlap": 0.3,    # an OCR word overlapping no text-layer word by this much is extra ink
    "table_inside": 0.7,         # a word is placed when this share of it is inside its cell
    "table_text_fallback": 0.6,  # text-alignment grids (built from the words) only below this best score
    "doubtful_table": 0.9,
    "disputed_region": 0.6,      # vote share under which a region's label is disputed
}
OWNERS = ("table", "document_index", "picture", "form")   # these take the words inside them first
FURNITURE = ("page_header", "page_footer")


# --------------------------------------------------------------------------
# geometry

def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def inter(a, b):
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def iou(a, b):
    i = inter(a, b)
    return i / (area(a) + area(b) - i) if i else 0.0


def centre(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def inside(box, x, y, pad=None):
    if pad is None:
        pad = min(SETTINGS["pad_pt"], SETTINGS["pad_frac"] * min(box[2] - box[0], box[3] - box[1]))
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


# --------------------------------------------------------------------------
# tokens, for comparing readings and checking exports

QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "‐": "-", "‑": "-", "–": "-", "—": "-"})


def norm(t: str) -> str:
    return unicodedata.normalize("NFKC", t).translate(QUOTES).strip().strip(".,;:()[]{}\"'«»*_`#|").casefold()


def tokens(texts) -> list[str]:
    return [n for n in (norm(t) for t in texts) if n and any(ch.isalnum() for ch in n)]


# --------------------------------------------------------------------------
# evidence words: which reader's words are the page's

def match_readings(pdf: list[dict], ocr: list[dict]) -> dict:
    """How far the text layer and OCR agree: share of each reader's words the other also read
    at the same place with the same text."""
    def key(w):
        return norm(w["text"])
    hits_pdf = hits_ocr = 0
    used = set()
    for w in pdf:
        k = key(w)
        if not k:
            continue
        for j, o in enumerate(ocr):
            if j not in used and iou(w["box"], o["box"]) > 0.3 and key(o) == k:
                used.add(j)
                hits_pdf += 1
                break
    n_pdf = sum(1 for w in pdf if key(w))
    n_ocr = sum(1 for o in ocr if key(o))
    return {"pdf_words": n_pdf, "ocr_words": n_ocr,
            "pdf_found_by_ocr": round(hits_pdf / n_pdf, 4) if n_pdf else None,
            "ocr_found_in_pdf": round(len(used) / n_ocr, 4) if n_ocr else None}


def choose_evidence(verdict: str, pdf: list[dict], ocr: list[dict]) -> tuple[str, list[int], list[int]]:
    """-> (source, indexes of pdf words used, indexes of ocr words used).

    A sound layer is the page's text; OCR words over ink the layer lacks (stamps, text inside
    images, handwriting) are added beside it, flagged. Any other layer gives way to OCR."""
    visible = [i for i, w in enumerate(pdf) if not w["hidden"] and not w["redacted"]]
    if verdict == "stub":
        # a scan carrying a few digital words (a stamp, a markup): OCR is the page's text, and the
        # stub's words stay where OCR does not cover them
        oboxes = [o["box"] for o in ocr]
        keep = [i for i in visible
                if sum(inter(pdf[i]["box"], b) for b in oboxes) <= SETTINGS["ocr_extra_overlap"] * max(area(pdf[i]["box"]), 1e-6)]
        return "ocr", keep, list(range(len(ocr)))
    if verdict == "sound" and visible:
        # covered by all the layer's words together: a letter-spaced heading ("F I L I N G") is
        # one OCR word over many one-letter layer words. Hidden words count too: they are the
        # same ink read by someone else's OCR.
        boxes = [w["box"] for w in pdf if not w["redacted"]]
        extra = [j for j, o in enumerate(ocr)
                 if sum(inter(o["box"], b) for b in boxes) <= SETTINGS["ocr_extra_overlap"] * max(area(o["box"]), 1e-6)]
        return "pdf", visible, extra
    return "ocr", [], list(range(len(ocr)))


# --------------------------------------------------------------------------
# layout: several models, one set of regions

def nms(dets: list[dict]) -> list[dict]:
    """One model's detections: boxes alike are one detection; its other labels are kept."""
    out = []
    for d in sorted(dets, key=lambda d: -d["conf"]):
        twin = next((o for o in out if iou(o["box"], d["box"]) >= SETTINGS["nms_iou"]), None)
        if twin:
            twin["alt"].append((d["label"], d["conf"]))
        else:
            out.append({**d, "alt": []})
    return out


def merge_layouts(per_model: dict[str, list[dict]]) -> list[dict]:
    """Regions from every model's detections: boxes that agree are one region, labelled by
    the vote of the models that found it (each model its best label, weighted by score)."""
    flat = [(m, d) for m, dets in per_model.items() for d in nms(dets)]
    regions = []
    for m, d in sorted(flat, key=lambda md: -md[1]["conf"]):
        r = next((r for r in regions if m not in r["models"] and iou(r["box"], d["box"]) >= SETTINGS["merge_iou"]), None)
        if r is None:
            r = {"box": d["box"], "models": {}}
            regions.append(r)
        r["models"][m] = (d["label"], d["conf"])
    n_models = len(per_model)
    out = []
    for i, r in enumerate(regions):
        votes = collections.Counter()
        for lab, conf in r["models"].values():
            votes[lab] += conf
        label, top = votes.most_common(1)[0]
        support = len(r["models"])
        best = max(c for _, c in r["models"].values())
        keep = support >= 2 or best >= SETTINGS["min_support_conf"]
        out.append({"id": i, "box": tuple(round(v, 2) for v in r["box"]), "label": label,
                    "votes": {k: round(v, 3) for k, v in votes.items()},
                    "share": round(top / sum(votes.values()), 3), "support": support, "of": n_models,
                    "models": {k: [v[0], v[1]] for k, v in r["models"].items()}, "kept": keep})
    return out


def assign(words: list[dict], regions: list[dict]) -> dict[int, list[int]]:
    """Each evidence word to one region: tables, pictures and forms first (a page-wide text
    box must not take a table's words [L Route by what a page is]), then the smallest
    region holding its centre. -> region id -> word indexes; unclaimed under -1."""
    kept = [r for r in regions if r["kept"]]
    owners = sorted((r for r in kept if r["label"] in OWNERS), key=lambda r: area(r["box"]))
    rest = sorted((r for r in kept if r["label"] not in OWNERS), key=lambda r: area(r["box"]))
    out = collections.defaultdict(list)
    for i, w in enumerate(words):
        x, y = centre(w["box"])
        r = next((r for r in owners if inside(r["box"], x, y)), None) or \
            next((r for r in rest if inside(r["box"], x, y)), None)
        out[r["id"] if r else -1].append(i)
    return out


def reading_order(regions: list[dict], page_width: float) -> list[dict]:
    """Top to bottom, left to right within a line; on a page that runs in two columns, the left
    column before the right, a full-width region closing the band.

    Each region is placed by `order_box`, the box of the words it was given, when it has one:
    a large region whose lines its smaller neighbours took is otherwise placed by its own top,
    ahead of text it no longer holds (835125's address block came out of order). Items whose
    tops are within 4 pt are one line. Columns are decided per page - two or more narrow
    regions on each side that overlap vertically - so a narrow item on a single-column page
    (a date set to the right) is not held back to the end of the band."""
    def ob(r):
        return r.get("order_box") or r["box"]
    mid = page_width / 2
    left = [r for r in regions if ob(r)[2] <= mid + 0.05 * page_width and ob(r)[2] - ob(r)[0] < 0.55 * page_width]
    right = [r for r in regions if ob(r)[0] >= mid - 0.05 * page_width and ob(r)[2] - ob(r)[0] < 0.55 * page_width]
    overlap = sum(1 for a in left for b in right if min(ob(a)[3], ob(b)[3]) > max(ob(a)[1], ob(b)[1]))
    body = sorted(regions, key=lambda r: (int(ob(r)[1] // 4), ob(r)[0]))
    if not (len(left) >= 2 and len(right) >= 2 and overlap >= 2):
        return body
    out, lcol, rcol = [], [], []
    for r in body:
        if r in left:
            lcol.append(r)
        elif r in right:
            rcol.append(r)
        else:
            out += lcol + rcol + [r]
            lcol, rcol = [], []
    return out + lcol + rcol


def lines_of(words: list[dict]) -> list[list[dict]]:
    """Words as lines, top to bottom, each left to right."""
    lines = []
    for w in sorted(words, key=lambda w: (centre(w["box"])[1], w["box"][0])):
        h = w["box"][3] - w["box"][1]
        cy = centre(w["box"])[1]
        if lines and abs(lines[-1]["cy"] - cy) < 0.5 * max(h, lines[-1]["h"]):
            ln = lines[-1]
            ln["w"].append(w)
            ln["cy"] = sum(centre(x["box"])[1] for x in ln["w"]) / len(ln["w"])
            ln["h"] = max(ln["h"], h)
        else:
            lines.append({"cy": cy, "h": h, "w": [w]})
    return [sorted(ln["w"], key=lambda w: w["box"][0]) for ln in lines]


def text_of(words: list[dict]) -> str:
    return "\n".join(" ".join(w["text"] for w in ln) for ln in lines_of(words))


LINE_NUMBERS = {"margin_frac": 0.15, "min_lines": 8, "min_rises": 0.8, "min_on_pitch": 0.9}


def line_numbers(words: list[dict], page_width: float, strict: bool = True) -> list[dict]:
    """A transcript's numbers down a margin, or []. Two kinds seen: line numbers 1-30 on the left,
    back to 1 on each transcript page (GH-3-97), and paragraph numbers on the right counting on
    across pages (EH-1-2000: 162, 163 ...). Numbers rise one by one. `strict` adds the pitch
    test: lines on one pitch, which a table's numbered rows fail (4656998 p39: 0.38), and which a
    wrapped numbered paragraph fails too - so it is dropped where the title says transcript
    [L Route by what a page is]."""
    s = LINE_NUMBERS
    for side in ("left", "right"):
        nums = sorted((w for w in words if w["text"].isdecimal() and len(w["text"]) <= 6 and
                       (w["box"][2] < s["margin_frac"] * page_width if side == "left"
                        else w["box"][0] > (1 - s["margin_frac"]) * page_width)), key=lambda w: centre(w["box"])[1])
        if len(nums) < s["min_lines"]:
            continue
        vals = [int(w["text"]) for w in nums]
        if sum(1 for a, b in zip(vals, vals[1:]) if b == a + 1 or (b == 1 and a > 1)) < s["min_rises"] * (len(vals) - 1):
            continue
        if not strict:
            return nums
        gaps = [centre(b["box"])[1] - centre(a["box"])[1] for a, b in zip(nums, nums[1:]) if int(b["text"]) == int(a["text"]) + 1]
        pitch = min(gaps, default=0)
        if pitch > 0 and sum(1 for d in gaps if abs(d / pitch - round(d / pitch)) <= 0.1) >= s["min_on_pitch"] * len(gaps):
            return nums
    return []


def clusters(words: list[dict], gap: float = 18.0) -> list[list[int]]:
    """Unclaimed words grouped into blocks: same line and close, or the line below overlapping."""
    groups: list[list[int]] = []
    for i in sorted(range(len(words)), key=lambda i: (centre(words[i]["box"])[1], words[i]["box"][0])):
        b = words[i]["box"]
        placed = False
        for g in reversed(groups[-6:]):
            gb = (min(words[j]["box"][0] for j in g), min(words[j]["box"][1] for j in g),
                  max(words[j]["box"][2] for j in g), max(words[j]["box"][3] for j in g))
            h = b[3] - b[1]
            if b[1] - gb[3] < 1.2 * h and b[0] < gb[2] + gap and b[2] > gb[0] - gap:
                g.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])
    return groups


# --------------------------------------------------------------------------
# tables: every reader's grid, scored by the evidence words it places

def score_grid(cells: list[dict], words: list[dict]) -> dict:
    """placed: a word whose centre is in exactly one cell and mostly inside it; straddle: centre
    in a cell, crossing its border; orphan: in no cell; multi: in several. Scored by words, never
    by cells: counting issues per cell rewarded merging them [L TableFormer accurate on the ten]."""
    placed = straddle = orphan = multi = 0
    used_rows, used_cols = set(), set()
    for w in words:
        x, y = centre(w["box"])
        hits = [c for c in cells if inside(c["box"], x, y, 0.5)]
        if not hits:
            orphan += 1
        elif len(hits) > 1:
            multi += 1
        elif inter(w["box"], hits[0]["box"]) >= SETTINGS["table_inside"] * max(area(w["box"]), 1e-6):
            placed += 1
        else:
            straddle += 1
        for c in hits[:1]:
            used_rows.update(range(c["r0"], c["r1"]))
            used_cols.update(range(c["c0"], c["c1"]))
    n = len(words)
    rows = max((c["r1"] for c in cells), default=0)
    cols = max((c["c1"] for c in cells), default=0)
    # values stacked in one cell: three or more lines, most of them numbers, dates or amounts -
    # rows run together. A coarse grid from a partly ruled table places every word this way
    # (2684908's lab report: ruling lines 5 x 6 at 100%, TableFormer accurate 43 x 7 at 97%).
    # Wrapped prose in a cell is not penalised.
    stacked = 0
    by_cell = collections.defaultdict(list)
    for w in words:
        x, y = centre(w["box"])
        hit = next((k for k, c in enumerate(cells) if inside(c["box"], x, y, 0.5)), None)
        if hit is not None:
            by_cell[hit].append(w)
    for ws in by_cell.values():
        lns = lines_of(ws)
        if len(lns) >= 3 and sum(1 for ln in lns if all(is_value(w["text"]) for w in ln)) >= 2 * len(lns) / 3:
            stacked += len(ws)
    # rows and columns no word falls in: a grid cut into slivers places every word and still
    # is not the table (3768464 p28: ruling lines 9 x 10 with 8 empty columns, for a 2-column table)
    empty = (rows - len(used_rows)) + (cols - len(used_cols))
    return {"words": n, "placed": placed, "straddle": straddle, "orphan": orphan, "multi": multi,
            "score": round(placed / n, 4) if n else (1.0 if cells else 0.0),
            "rows": rows, "cols": cols, "cells": len(cells),
            "empty_rows": rows - len(used_rows), "empty_cols": cols - len(used_cols),
            "empty_share": round(empty / (rows + cols), 4) if rows + cols else 0.0,
            "stacked_values": stacked,
            "fit": round((placed - stacked) / n, 4) if n else (1.0 if cells else 0.0)}


# a number with an optional comparison, sign, currency or percent; or a date. Not an equipment
# tag ("MG-30-F5-1003/", "3-PS-1"): "has a digit" is not "is a value" [L rows run together]
VALUE = re.compile(r"^(?:[<>~≤≥±]\s?)?[-+(]?[$€£]?\d[\d,]*(?:\.\d+)?\)?%?$"
                   r"|^\d{1,2}-[A-Z]{3}-\d{2,4}$|^\d{4}-\d{2}-\d{2}$|^\d{1,2}/\d{1,2}/\d{2,4}$", re.I)


def is_value(t: str) -> bool:
    """A number, date, amount or measurement ("<0.01", "28-AUG-07", "$1,234", "79.0")."""
    return bool(VALUE.match(t.strip(".,;:")))


PREFERENCE = ["mupdf_lines", "tableformer_fast", "tableformer_accurate", "mupdf_text"]


def choose_grid(readings: dict[str, dict]) -> tuple[str | None, str]:
    """The reading that places the most words. A text-alignment grid is built from the words
    themselves and passes the word score by construction [L Ruling lines], so it is chosen only
    when every other grid places under `table_text_fallback` of them."""
    ok = {k: v for k, v in readings.items() if v.get("cells") and not v.get("error")}
    if not ok:
        return None, "no reader produced a grid"
    honest = {k: v for k, v in ok.items() if k != "mupdf_text"}
    pool = honest
    if not honest or max(v["score"] for v in honest.values()) < SETTINGS["table_text_fallback"]:
        pool = ok
    # most words placed, values stacked in one cell not counting as placed (`fit`); within 1% of
    # the best, the grid with the least empty structure
    def fit(k):
        return pool[k].get("fit", pool[k]["score"])
    top = max(fit(k) for k in pool)
    near = [k for k in pool if fit(k) >= top - 0.01]
    best = min(near, key=lambda k: (pool[k].get("empty_share", 0), -fit(k),
                                    PREFERENCE.index(k) if k in PREFERENCE else 9))
    why = (f"places {pool[best]['score']:.1%} of its words ({pool[best].get('stacked_values', 0)} stacked in cells), "
           f"{pool[best].get('empty_share', 0):.0%} empty rows/columns") + (
        "; text alignment allowed: no other grid placed 60%" if pool is ok and "mupdf_text" in ok else "")
    return best, why


# --------------------------------------------------------------------------
# DoclingDocument and Markdown

LABELS = {"caption": "CAPTION", "footnote": "FOOTNOTE", "page_header": "PAGE_HEADER", "page_footer": "PAGE_FOOTER",
          "text": "TEXT", "list_item": "LIST_ITEM", "key_value_region": "TEXT", "form": "TEXT",
          "checkbox_selected": "CHECKBOX_SELECTED", "checkbox_unselected": "CHECKBOX_UNSELECTED",
          "formula": "FORMULA", "code": "CODE", "section_header": "SECTION_HEADER", "title": "TITLE"}


def build_document(name: str, pages: list[dict], words: list[dict]):
    """The chosen structure as a DoclingDocument. Each item's meta names its evidence words
    (`regdocs__words`), its region and the readers behind it. -> (document, item per word)."""
    from docling_core.types.doc import (BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument,
                                        ProvenanceItem, Size, TableCell, TableData)
    from docling_core.types.doc.common.content_layer import ContentLayer
    from docling_core.types.doc.common.meta import BaseMeta

    doc = DoclingDocument(name=name)
    owner = {}

    def prov(pno, box, text=""):
        return ProvenanceItem(page_no=pno, charspan=(0, len(text)),
                              bbox=BoundingBox(l=box[0], t=box[1], r=box[2], b=box[3], coord_origin=CoordOrigin.TOPLEFT))

    def tag(item, ids, **fields):
        item.meta = BaseMeta()
        item.meta.set_custom_field("regdocs", "words", ids)
        for k, v in fields.items():
            item.meta.set_custom_field("regdocs", k, v)
        for i in ids:
            owner[i] = item.self_ref

    for p in pages:
        pno = p["page"]
        doc.add_page(page_no=pno, size=Size(width=p["facts"]["width_pt"], height=p["facts"]["height_pt"]))
        list_group = None
        for el in p["elements"]:
            ids, label, box = el["words"], el["label"], el["box"]
            ws = [words[i] for i in ids]
            text = text_of(ws)
            layer = ContentLayer.FURNITURE if label in FURNITURE or label == "line_numbers" else ContentLayer.BODY
            meta = {"region": el.get("region"), "label_share": el.get("share"), "reader": el["reader"]}
            if label != "list_item":
                list_group = None
            if label in ("table", "document_index"):
                cells = [TableCell(bbox=BoundingBox(l=c["box"][0], t=c["box"][1], r=c["box"][2], b=c["box"][3],
                                                    coord_origin=CoordOrigin.TOPLEFT),
                                   row_span=c["r1"] - c["r0"], col_span=c["c1"] - c["c0"],
                                   start_row_offset_idx=c["r0"], end_row_offset_idx=c["r1"],
                                   start_col_offset_idx=c["c0"], end_col_offset_idx=c["c1"],
                                   text=text_of([words[i] for i in c["words"]]), column_header=c["header"])
                         for c in el["cells"]]
                data = TableData(num_rows=el["rows"], num_cols=el["cols"], table_cells=cells)
                item = doc.add_table(data=data, prov=prov(pno, box),
                                     label=DocItemLabel.DOCUMENT_INDEX if label == "document_index" else DocItemLabel.TABLE)
                tag(item, [i for c in el["cells"] for i in c["words"]], grid=el["grid"], **meta)
                if el.get("loose"):              # words inside the table, in no cell: kept after it
                    lw = [words[i] for i in el["loose"]]
                    lt = text_of(lw)
                    t = doc.add_text(label=DocItemLabel.TEXT, text=lt, prov=prov(pno, box, lt))
                    tag(t, el["loose"], **{**meta, "reader": meta["reader"] + "+table_loose"})
            elif label == "picture":
                item = doc.add_picture(prov=prov(pno, box))
                tag(item, [], figure=el.get("figure"), **meta)
                if ids:
                    t = doc.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(pno, box, text), parent=item)
                    tag(t, ids, **meta)
            elif label == "list_item":
                if list_group is None:
                    list_group = doc.add_list_group()
                item = doc.add_list_item(text=text, prov=prov(pno, box, text), parent=list_group)
                tag(item, ids, **meta)
            elif label in ("section_header", "title"):
                item = (doc.add_title if label == "title" else doc.add_heading)(text=text, prov=prov(pno, box, text))
                tag(item, ids, **meta)
            elif label in ("checkbox_selected", "checkbox_unselected") and "checked" in el:
                mark = ("☒" if el["checked"] else "☐") + (" " + text if text else "")
                item = doc.add_text(label=DocItemLabel[LABELS[label]], text=mark, prov=prov(pno, box, mark))
                tag(item, ids, checkbox_label=el.get("checkbox_label"), **meta)
            elif label in ("code", "formula"):      # their own item types, not a TextItem with that label
                item = (doc.add_code if label == "code" else doc.add_formula)(text=text, prov=prov(pno, box, text))
                tag(item, ids, **meta)
            else:
                item = doc.add_text(label=DocItemLabel[LABELS.get(label, "TEXT")], text=text,
                                    prov=prov(pno, box, text), content_layer=layer)
                tag(item, ids, **meta)
    return doc, owner


def markdown(pages: list[dict], words: list[dict]) -> str:
    """Written from the elements, so it holds exactly what the document holds: furniture,
    text under pictures and table cells (a spanning cell once) included."""
    out = []
    for p in pages:
        out.append(f"<!-- page {p['page']} -->")
        for el in p["elements"]:
            ws = [words[i] for i in el["words"]]
            label = el["label"]
            if label in ("table", "document_index") and el.get("cells"):
                grid = [["" for _ in range(el["cols"])] for _ in range(el["rows"])]
                for c in el["cells"]:
                    if c["r0"] < el["rows"] and c["c0"] < el["cols"]:
                        grid[c["r0"]][c["c0"]] = text_of([words[i] for i in c["words"]]).replace("\n", " ").replace("|", "\\|")
                rows = ["| " + " | ".join(r) + " |" for r in grid]
                if rows:
                    rows.insert(1, "|" + "---|" * el["cols"])
                out.append("\n".join(rows))
                loose = [words[i] for i in el.get("loose", [])]
                if loose:
                    out.append(text_of(loose))
            elif label == "picture":
                fig = (el.get("figure") or {}).get("class", "picture")
                out.append(f"*[{fig}]*" + (f"\n\n{text_of(ws)}" if ws else ""))
            elif label in ("section_header", "title"):
                out.append(("# " if label == "title" else "## ") + text_of(ws).replace("\n", " "))
            elif label == "line_numbers":            # numbered paper's margin: kept, out of the paragraphs
                out.append("*Lines " + " ".join(w["text"] for w in sorted(ws, key=lambda w: w["box"][1])) + "*")
            elif label in ("checkbox_selected", "checkbox_unselected") and "checked" in el:
                # the mark, and the label when the box holds it; a label outside the box follows as its own text
                out.append(("☒" if el["checked"] else "☐") + (" " + " ".join(text_of(ws).split()) if ws else
                                                             f" [{el['checkbox_label']}]" if el.get("checkbox_label") else ""))
            elif label == "list_item":
                out.append("- " + text_of(ws).replace("\n", " "))
            elif el.get("line_no") is not None:      # a transcript's numbered line or paragraph
                rest = [w for w in ws if not (w["text"] == str(el["line_no"]) and w is ws[0])]
                out.append(f"{el['line_no']}  " + " ".join(text_of(rest).split()))
            elif ws:
                out.append(text_of(ws))
    return "\n\n".join(out) + "\n"


def check_docling(json_bytes: bytes, words: list[dict]) -> dict:
    """The canonical output checked as a consumer would read it: the DoclingDocument JSON loaded
    back. Every evidence word must be named by exactly one item (`regdocs__words`), and each
    item's text (a table's cells) must hold the words it names."""
    import gzip
    from docling_core.types.doc import DoclingDocument, TableItem
    from docling_core.types.doc.common.content_layer import ContentLayer
    doc = DoclingDocument.model_validate_json(gzip.decompress(json_bytes))
    named = collections.Counter()
    held = want = 0
    for item, _ in doc.iterate_items(included_content_layers=set(ContentLayer), with_groups=False, traverse_pictures=True):
        ids = ((item.meta.get_custom_part() if item.meta else {}) or {}).get("regdocs__words") or []
        named.update(ids)
        if not ids:
            continue
        text = " ".join(c.text for c in item.data.table_cells) if isinstance(item, TableItem) else getattr(item, "text", "")
        have = collections.Counter(md_tokens(text))
        need = collections.Counter(md_tokens(" ".join(words[i]["text"] for i in ids)))
        held += sum(min(n, have[t]) for t, n in need.items())
        want += sum(need.values())
    ev = [w["id"] for w in words if w["evidence"]]
    return {"evidence_words": len(ev), "named_once": sum(1 for i in ev if named[i] == 1),
            "named_twice": sum(1 for i in ev if named[i] > 1), "not_named": sum(1 for i in ev if not named[i]),
            "text_holds_words": round(held / want, 4) if want else 1.0}


DOTS = re.compile(r"(\.\s?){4,}|…{2,}|_{6,}")


def dot_leader_share(words: list[dict]) -> float:
    """Share of the lines that run on in dot leaders: a contents page's entries."""
    lines = [" ".join(w["text"] for w in ln) for ln in lines_of(words)]
    return sum(1 for t in lines if DOTS.search(t)) / len(lines) if lines else 0.0


def md_tokens(md: str) -> list[str]:
    md = re.sub(r"<!--.*?-->|^\*\[[a-z_]+\]\*$", " ", md, flags=re.M)   # page marks, our picture labels
    md = md.replace("\\|", " ").replace("\\", "")
    return tokens(re.sub(r"[|*_`#\[\]]+", " ", md).split())
