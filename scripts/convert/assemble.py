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
    "unmatched_bookmarks_per_page": 3,   # more than this, a page on average: a mail merge's outline
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


def ocr_agreement(page: dict, ledger: dict) -> float | None:
    """A page's share of text-layer words OCR also found, or None where OCR did not run. Ledgers
    written before 2026-10-17 store 0.0 for a page whose OCR a light pass skipped; their step
    timings show whether it ran (OCR words reused from an earlier conversion are not timed)."""
    v = (page.get("agreement") or {}).get("pdf_found_by_ocr")
    steps = page.get("step_seconds")
    if v is not None and steps is not None and "ocr" not in steps and not ledger.get("ocr_reused_from"):
        return None
    return v


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


# --------------------------------------------------------------------------
# sections: where each starts and how deep, so a reader (or a chunker) can walk the document as
# a tree [L Sections]. The filer's own structure first - bookmarks - then heading numbers.

NUMBERED = re.compile(r"^(?:(?:section|part|article|appendix)\s+)?(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+[^\W\d_]", re.I)
TOC_LINE = re.compile(r"(\.\s?){4,}|\s\d{1,4}$")
NO_SPLIT = ("table", "document_index", "picture", "line_numbers") + FURNITURE


def heading_number(text: str) -> tuple[int, ...] | None:
    """3.2.1 -> (3, 2, 1); "2.0" is 2, as reports number their chapters; none from 0 (a revision row)
    or without a word after the number (a legend's "5 - 10-15%")."""
    m = NUMBERED.match(text)
    if not m:
        return None
    n = tuple(int(x) for x in m.group(1).split("."))
    while len(n) > 1 and n[-1] == 0:
        n = n[:-1]
    return n if n[0] > 0 else None


def follows(prev: tuple[int, ...], n: tuple[int, ...]) -> bool:
    """n can come straight after heading prev: 2.3 -> 2.4, 2.3.1 or 3 (not 2.5, not 7)."""
    return n == prev + (1,) or any(n == prev[:k] + (prev[k] + 1,) for k in range(len(prev)))


def _first_lines(pages: list[dict], words: list[dict]):
    """Each splittable element's lines, in reading order: (page index, element index, line words)."""
    for pi, p in enumerate(pages):
        for ei, el in enumerate(p["elements"]):
            if el["label"] in NO_SPLIT or not el["words"]:
                continue
            for ln in lines_of([words[i] for i in el["words"]]):
                yield pi, ei, ln


def find_sections(pages: list[dict], words: list[dict], outline: list) -> tuple[list[dict], str | None]:
    """Section starts: {page index, element index, word ids of the heading line(s) or [], level,
    title, source}. From the bookmarks when there are at least two; else from heading numbers that
    run in sequence (the longest chain, at least three, so a numbered list or table rows do not
    pass for sections); else none - layout's section headers stay plain headings."""
    marks = []
    entries = [(lvl, title.strip(), pg) for lvl, title, pg in (outline or [])[:5000] if title.strip() and pg > 0]
    if len(entries) >= 2:
        by_page = collections.defaultdict(list)
        for pi, ei, ln in _first_lines(pages, words):
            by_page[pages[pi]["page"]].append((pi, ei, ln))
        for lvl, title, pg in entries:
            want = collections.Counter(tokens(title.split()))
            hit = None
            lines = by_page.get(pg, [])
            for k, (pi, ei, ln) in enumerate(lines):
                # a title may wrap onto a second line of the same element
                for span in (1, 2):
                    if span == 2 and (k + 1 >= len(lines) or lines[k + 1][:2] != (pi, ei)):
                        continue
                    ws = ln + (lines[k + 1][2] if span == 2 else [])
                    have = collections.Counter(tokens(w["text"] for w in ws))
                    common = sum((want & have).values())
                    if want and common >= 0.6 * sum(want.values()) and common >= 0.5 * sum(have.values()):
                        hit = (pi, ei, [w["id"] for w in ws])
                        break
                if hit:
                    break
            if hit is None:              # anchored at the page's first element; the heading is the bookmark's
                pi = next((i for i, p in enumerate(pages) if p["page"] == pg), None)
                if pi is None:
                    continue
                ei = next((i for i, el in enumerate(pages[pi]["elements"]) if el["label"] not in FURNITURE), None)
                hit = (pi, ei, [])
            marks.append({"pi": hit[0], "ei": hit[1], "words": hit[2], "level": lvl, "title": title,
                          "page": pg, "source": "bookmarks", "matched": bool(hit[2])})
        # a heading is written once per title and page, and bookmarks no line matches only when
        # there are a few of them a page: a scan's sheet labels and attachment names are real
        # sections (806765, 4367062), a mail merge's outline is every recipient's copy (4049986:
        # 136 bookmarks on 2 pages, "Marc Boucher" written 31 times, 2.1 Markdown words per
        # evidence word; 3755199: 443 on 4 pages, 6.2). Of 435 sampled documents with bookmarks,
        # 20 average more than 2 unmatched a page. The rest stay in the sections list, not written.
        unmatched = sum(not m["matched"] for m in marks)
        sparse = unmatched <= SETTINGS["unmatched_bookmarks_per_page"] * max(1, len(pages))
        matched_keys = {(m["page"], norm(m["title"])) for m in marks if m["matched"]}
        opened = set()
        for m in marks:
            key = (m["page"], norm(m["title"]))
            m["placed"] = key not in opened and (m["matched"] or (sparse and key not in matched_keys))
            if m["placed"]:
                opened.add(key)
        return marks, "bookmarks"
    cands = []
    for pi, ei, ln in _first_lines(pages, words):
        if (pages[pi].get("facts") or {}).get("size") == "oversize":
            continue                     # a drawing sheet's numbered notes are notes, not sections (834804)
        text = " ".join(w["text"] for w in ln)
        n = heading_number(text)
        # a heading line: short, not a contents line, not a sentence ending in a full stop, and a
        # title of words - OCR of a scanned sheet gives "2 B°", "3 C64" (3338141, 4041560)
        title = [t for t in tokens(w["text"] for w in ln[1:])]
        worded = sum(t.isalpha() for t in title) >= 0.5 * len(title) and any(len(t) >= 4 and t.isalpha() for t in title)
        if n and worded and len(ln) <= 14 and not TOC_LINE.search(text) and not text.rstrip().endswith((".", ";", ",")):
            cands.append((pi, ei, ln, n, text))
    if len(cands) > 3000:
        cands = cands[:3000]
    best, back = [1] * len(cands), [-1] * len(cands)
    for j in range(len(cands)):
        for i in range(j):
            if best[i] + 1 > best[j] and follows(cands[i][3], cands[j][3]):
                best[j], back[j] = best[i] + 1, i
    if not cands or max(best) < 3:
        return [], None
    j, chain = best.index(max(best)), []
    while j >= 0:
        chain.append(cands[j])
        j = back[j]
    # one level only is how numbered notes and lists look: sections of it must be spread out
    pages_hit = {pages[c[0]]["page"] for c in chain}
    if all(len(c[3]) == 1 for c in chain) and (len(pages_hit) < 2 or len(chain) / len(pages_hit) > 3):
        return [], None
    for pi, ei, ln, n, text in reversed(chain):
        marks.append({"pi": pi, "ei": ei, "words": [w["id"] for w in ln], "level": len(n), "title": text,
                      "page": pages[pi]["page"], "source": "numbering", "matched": True})
    return marks, "numbering"


def mark_sections(pages: list[dict], words: list[dict], outline: list) -> dict:
    """Split each section's heading out of the element it sits in (in light mode a heading is
    usually clustered with the paragraph below it) and mark it `section`; returns the summary
    for the ledger."""
    marks, source = find_sections(pages, words, outline)
    by_el = collections.defaultdict(list)
    for m in marks:
        if m.get("placed", True):
            by_el[(m["pi"], m["ei"])].append(m)
    for pi, p in enumerate(pages):
        out = []
        for ei, el in enumerate(p["elements"]):
            ms = by_el.get((pi, ei))
            if not ms:
                out.append(el)
                continue
            rest = list(el["words"])
            for m in ms:
                sec = {k: m[k] for k in ("level", "title", "source", "matched")}
                head = [i for i in rest if i in set(m["words"])]
                if not head:             # a bookmark with no line found: the section opens here
                    out.append({"region": None, "label": "section_header", "reader": "bookmarks", "share": None,
                                "box": el["box"], "words": [], "section": sec})
                    continue
                k = rest.index(head[0])
                before, rest = rest[:k], [i for i in rest[k:] if i not in set(head)]
                if before:
                    out.append(_part(el, before, words))
                h = _part(el, head, words)
                h.update(label="section_header", section=sec, reader=el["reader"] + "+" + m["source"])
                out.append(h)
            if rest:
                out.append(_part(el, rest, words))
        p["elements"] = out
    levels = [m["level"] for m in marks]
    return {"source": source, "headings": len(marks), "matched": sum(m["matched"] for m in marks),
            "not_written": sum(not m.get("placed", True) for m in marks),
            "depth": max(levels) if levels else 0, "bookmarks": len(outline or []),
            "list": [{**{k: m[k] for k in ("page", "level", "title", "source", "matched")},
                      "written": m.get("placed", True)} for m in marks[:2000]]}


def _part(el: dict, ids: list[int], words: list[dict]) -> dict:
    bs = [words[i]["box"] for i in ids]
    box = (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))
    return {**{k: v for k, v in el.items() if k not in ("cells", "loose", "section")}, "words": ids, "box": box,
            "order_box": box}


# --------------------------------------------------------------------------
# page boundaries: what repeats on every page is furniture; what a page break interrupts goes on
# [L Page boundaries]

BAND = 0.12
PAGE_NO = re.compile(r"\d+|\b[ivxlc]{1,6}\b")


def line_shape(ws: list[dict]) -> str:
    """A line's text with numbers (roman too) as '#': "Page 3 of 12" and "Page 4 of 12" alike."""
    return PAGE_NO.sub("#", " ".join(norm(w["text"]) for w in ws)).strip()


def mark_furniture(pages: list[dict], words: list[dict]) -> dict:
    """Lines in the top or bottom 12% of the page that repeat (see `runs`; bare page numbers too)
    are running headers and footers: their words are taken out of whatever elements hold them and
    become page_header / page_footer items (the furniture layer). The layout models label only 40%
    of these (F040); light mode has no layout at all. Lines are read across the page, not within
    an element: clusters cut a header differently on each page (4403940). Words in a table are
    its header row, not furniture."""
    if len(pages) < 4:
        return {"lines": 0}
    seen, cand = collections.defaultdict(set), []
    for pi, p in enumerate(pages):
        h = (p.get("facts") or {}).get("height_pt")
        if not h:
            continue
        free = [i for el in p["elements"] if el["label"] not in NO_SPLIT for i in el["words"]]
        for ln in lines_of([words[i] for i in free]):
            y = sum(centre(w["box"])[1] for w in ln) / len(ln)
            band = "page_header" if y < BAND * h else "page_footer" if y > (1 - BAND) * h else None
            s = line_shape(ln)
            if band and s and (any(ch.isalnum() for ch in s) or s.strip("# -–|/.ofpage") == ""):
                seen[(band, s)].add(p["page"])
                cand.append((pi, band, s, [w["id"] for w in ln]))

    def runs(pgs: set) -> bool:
        """On at least three pages, and on at least half the pages from its first to its last: a
        filing of several documents has each one's header only over its own pages (4403940: an
        article from p.43, its header on alternate pages), and odd and even pages differ."""
        return len(pgs) >= 3 and len(pgs) >= 0.5 * (max(pgs) - min(pgs) + 1)
    by_page = collections.defaultdict(list)
    for pi, band, s, ids in cand:
        if runs(seen[(band, s)]):
            by_page[pi].append((band, ids))
    n = 0
    for pi, hits in by_page.items():
        p = pages[pi]
        taken = {i for _, ids in hits for i in ids}
        out = []
        for el in p["elements"]:
            if el["label"] in NO_SPLIT or not taken.intersection(el["words"]):
                out.append(el)
                continue
            rest = [i for i in el["words"] if i not in taken]
            if rest:
                out.append(_part(el, rest, words))
        for band, ids in hits:
            f = {"region": None, "label": band, "reader": words[ids[0]].get("reader", "pdf") + "+repeated",
                 "share": None, "words": ids}
            f.update({k: v for k, v in _part(f, ids, words).items() if k in ("box", "order_box")})
            out.append(f)
            n += 1
        p["elements"] = out
    return {"lines": n}


def header_alike(a: str, b: str) -> bool:
    """Two header lines alike enough: OCR differs a little between pages ("tera com" / "tera
    comm"), and a line may be cut at a different word (F041)."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if len(ta) >= 3 and len(tb) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return len(set(ta) & set(tb)) / len(set(ta) | set(tb)) >= 0.7


def stitch_tables(pages: list[dict], words: list[dict]) -> dict:
    """A table ending a page whose header line opens the next page's first table is one table
    (F041): the continuation's cells are laid on the first page's columns - each page's grid is
    read on its own, and the readers often split the same columns differently ([4, 3], [13, 12])
    - its rows appended, and its repeated header row moved to the page's furniture."""
    n = 0
    carry = None                 # a table stitched onto the page before, which may go on again
    for a, b in zip(pages, pages[1:]):
        held, carry = carry, None
        if b["page"] != a["page"] + 1:
            continue
        body_a = [e for e in a["elements"] if e["label"] not in FURNITURE + ("line_numbers",) and e["words"]
                  and not e["reader"].endswith("+table_loose")]
        body_b = [e for e in b["elements"] if e["label"] not in FURNITURE + ("line_numbers",) and e["words"]]
        if not body_b:
            continue
        ta = max((e for e in body_a if e["label"] == "table" and e.get("cells")), key=lambda e: e["box"][3], default=None)
        foot = ta["box"] if ta is not None else None
        if held is not None and not body_a:      # page a was all continuation: the table goes on from there
            ta, foot = held
        tb = min((e for e in body_b if e["label"] == "table" and e.get("cells")), key=lambda e: e["box"][1], default=None)
        if ta is None or tb is None:
            continue
        ha, hb = (a.get("facts") or {}).get("height_pt") or 792, (b.get("facts") or {}).get("height_pt") or 792
        below = [e for e in body_a if e["box"][1] > foot[3] + 2]
        above = [e for e in body_b if e["box"][3] < tb["box"][1] - 2]
        if foot[3] < 0.75 * ha or tb["box"][1] > 0.25 * hb or below or above:
            continue
        head_a = [c for c in ta["cells"] if c["r0"] == 0]
        head_b = [c for c in tb["cells"] if c["r0"] == 0]
        line_a = line_shape(sorted((words[i] for c in head_a for i in c["words"]), key=lambda w: w["box"][0]))
        line_b = line_shape(sorted((words[i] for c in head_b for i in c["words"]), key=lambda w: w["box"][0]))
        if not header_alike(line_a, line_b):
            continue
        # the first page's columns, as x ranges; each continuation cell goes to the one its middle is in
        spans = collections.defaultdict(lambda: [1e9, -1e9])
        for c in ta["cells"]:
            if c["c1"] - c["c0"] == 1:
                sp = spans[c["c0"]]
                sp[0], sp[1] = min(sp[0], c["box"][0]), max(sp[1], c["box"][2])
        if len(spans) < 2:
            continue

        def column(cell):
            cx = (cell["box"][0] + cell["box"][2]) / 2
            return min(spans, key=lambda k: 0 if spans[k][0] <= cx <= spans[k][1]
                       else min(abs(cx - spans[k][0]), abs(cx - spans[k][1])))
        merged = {}
        for c in tb["cells"]:
            if c["r0"] == 0:
                continue
            key = (ta["rows"] + c["r0"] - 1, column(c))
            if key in merged:
                merged[key]["words"] += c["words"]
                bx = merged[key]["box"]
                merged[key]["box"] = (min(bx[0], c["box"][0]), min(bx[1], c["box"][1]),
                                      max(bx[2], c["box"][2]), max(bx[3], c["box"][3]))
            else:
                merged[key] = {"r0": key[0], "r1": key[0] + 1, "c0": key[1], "c1": key[1] + 1,
                               "box": c["box"], "words": list(c["words"]), "header": False, "page": b["page"]}
        added_rows = max((c["r0"] for c in tb["cells"]), default=0)
        ta["cells"] = ta["cells"] + list(merged.values())
        ta["rows"] = ta["rows"] + added_rows
        ta.setdefault("continued", []).append({"page": b["page"], "box": tb["box"], "first_row": ta["rows"] - added_rows,
                                               "cols_there": tb.get("cols")})
        ta["words"] = ta["words"] + [i for c in merged.values() for i in c["words"]]
        rest = list(b["elements"])
        rest.remove(tb)
        hdr = [i for c in head_b for i in c["words"]]
        if hdr:
            rest.append({**_part(tb, hdr, words), "label": "page_header", "region": tb.get("region"),
                         "reader": tb["reader"] + "+repeated_table_header"})
        if tb.get("loose"):
            rest.append({**_part(tb, tb["loose"], words), "label": "text", "reader": tb["reader"] + "+table_loose"})
        b["elements"] = rest
        n += 1
        if not [e for e in rest if e["label"] not in FURNITURE + ("line_numbers",) and e["words"]
                and e["box"][1] > tb["box"][1] and not e["reader"].endswith("+table_loose")]:
            carry = (ta, tb["box"])          # the continuation reached this page's foot: it may go on
    return {"tables_stitched": n}


SENTENCE_END = re.compile(r"[.!?:;]['\")\]]?$")
ENUMERATOR = re.compile(r"^\(?([a-z]{1,2}|[ivxl]{1,5})[).]$")      # "b)", "iv.", "(c)": a list item, not a sentence
FLOWING = ("text", "paragraph", "footnote")


def join_across_pages(pages: list[dict], words: list[dict]) -> dict:
    """A page that ends a body text item without closing punctuation, followed by a page whose
    first body text item goes on in lower case: one sentence split by the break (about one break
    in six, F042). The second item is marked `continues`, so the document holds the two as one
    item with a provenance entry per page."""
    joins = 0
    for a, b in zip(pages, pages[1:]):
        if b["page"] != a["page"] + 1:
            continue
        body_a = [e for e in a["elements"] if e["label"] not in FURNITURE + ("line_numbers",) and e["words"]]
        body_b = [e for e in b["elements"] if e["label"] not in FURNITURE + ("line_numbers",) and e["words"]]
        if not body_a or not body_b:
            continue
        last, first = body_a[-1], body_b[0]
        if last["label"] not in FLOWING or first["label"] not in FLOWING or last.get("section") or first.get("section"):
            continue
        tail = lines_of([words[i] for i in last["words"]])[-1]
        head = lines_of([words[i] for i in first["words"]])[0]
        end, start = tail[-1]["text"], head[0]["text"]
        # the same column, at the foot of one page and the head of the next: on a two-column page
        # the last item read is not the last line written (4415269 joined across columns)
        ha, hb = (a.get("facts") or {}).get("height_pt") or 792, (b.get("facts") or {}).get("height_pt") or 792
        bottom_a = max(words[i]["box"][3] for e in body_a for i in e["words"])
        top_b = min(words[i]["box"][1] for e in body_b for i in e["words"])
        xa = (min(w["box"][0] for w in tail), max(w["box"][2] for w in tail))
        xb = (min(w["box"][0] for w in head), max(w["box"][2] for w in head))
        overlap = min(xa[1], xb[1]) - max(xa[0], xb[0])
        placed = tail[0]["box"][3] >= bottom_a - 0.05 * ha and head[0]["box"][1] <= top_b + 0.05 * hb \
            and overlap >= 0.5 * min(xa[1] - xa[0], xb[1] - xb[0])
        # footnotes sit at the foot of the page in smaller type: their last line is not where the
        # sentence breaks off (4403940, a law review: "See James Keller & Bill || thus has...")
        hs = sorted(words[i]["box"][3] - words[i]["box"][1] for e in body_a for i in e["words"])
        typical = hs[len(hs) // 2] if hs else 0
        small = sum(w["box"][3] - w["box"][1] for w in tail) / len(tail) < 0.85 * typical
        if placed and not small and not SENTENCE_END.search(end) and (start[:1].islower() or start[:1] in ",;)") \
                and not ENUMERATOR.match(start) and any(ch.isalpha() for ch in " ".join(w["text"] for w in tail)):
            first["continues"] = True
            joins += 1
    return {"joins": joins}


def build_document(name: str, pages: list[dict], words: list[dict]):
    """The chosen structure as a DoclingDocument. Each item's meta names its evidence words
    (`regdocs__words`), its region and the readers behind it. -> (document, item per word)."""
    from docling_core.types.doc import (BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, GroupLabel,
                                        ProvenanceItem, Size, TableCell, TableData)
    from docling_core.types.doc.common.content_layer import ContentLayer
    from docling_core.types.doc.common.meta import BaseMeta

    doc = DoclingDocument(name=name)
    owner = {}
    stack = []                   # open sections: (level, group); body items go in the deepest
    flow = {"item": None, "ids": []}    # the last body text item, which the next page may continue

    def under():
        return stack[-1][1] if stack else None

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
            if layer == ContentLayer.BODY and not el.get("continues") and label not in FLOWING:
                flow["item"] = None     # a table, heading, picture...: the sentence did not go on past it
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
                item = doc.add_table(data=data, prov=prov(pno, box), parent=under(),
                                     label=DocItemLabel.DOCUMENT_INDEX if label == "document_index" else DocItemLabel.TABLE)
                for cont in el.get("continued") or []:      # a table stitched across pages: a prov per page
                    item.prov.append(prov(cont["page"], cont["box"]))
                tag(item, [i for c in el["cells"] for i in c["words"]], grid=el["grid"], **meta)
                if el.get("continued"):
                    item.meta.set_custom_field("regdocs", "continued", el["continued"])
                if el.get("loose"):              # words inside the table, in no cell: kept after it
                    lw = [words[i] for i in el["loose"]]
                    lt = text_of(lw)
                    t = doc.add_text(label=DocItemLabel.TEXT, text=lt, prov=prov(pno, box, lt), parent=under())
                    tag(t, el["loose"], **{**meta, "reader": meta["reader"] + "+table_loose"})
            elif label == "picture":
                item = doc.add_picture(prov=prov(pno, box), parent=under())
                tag(item, [], figure=el.get("figure"), **meta)
                if ids:
                    t = doc.add_text(label=DocItemLabel.TEXT, text=text, prov=prov(pno, box, text), parent=item)
                    tag(t, ids, **meta)
            elif label == "list_item":
                if list_group is None:
                    list_group = doc.add_list_group(parent=under())
                item = doc.add_list_item(text=text, prov=prov(pno, box, text), parent=list_group)
                tag(item, ids, **meta)
            elif el.get("section"):         # a section opens: a group holding its heading and what follows
                sec = el["section"]
                while stack and stack[-1][0] >= sec["level"]:
                    stack.pop()
                g = doc.add_group(label=GroupLabel.SECTION, name=sec["title"][:200], parent=under())
                stack.append((sec["level"], g))
                item = doc.add_heading(text=text or sec["title"], level=min(max(sec["level"], 1), 100),
                                       prov=prov(pno, box, text) if ids else None, parent=g)
                tag(item, ids, section_source=sec["source"], bookmark_only=not ids, **meta)
            elif label in ("section_header", "title"):
                item = (doc.add_title(text=text, prov=prov(pno, box, text), parent=under()) if label == "title" else
                        doc.add_heading(text=text, level=min(len(stack) + 1, 100), prov=prov(pno, box, text),
                                        parent=under()))
                tag(item, ids, **meta)
            elif label in ("checkbox_selected", "checkbox_unselected") and "checked" in el:
                mark = ("☒" if el["checked"] else "☐") + (" " + text if text else "")
                item = doc.add_text(label=DocItemLabel[LABELS[label]], text=mark, prov=prov(pno, box, mark),
                                    parent=under())
                tag(item, ids, checkbox_label=el.get("checkbox_label"), **meta)
            elif label in ("code", "formula"):      # their own item types, not a TextItem with that label
                item = (doc.add_code if label == "code" else doc.add_formula)(text=text, prov=prov(pno, box, text),
                                                                              parent=under())
                tag(item, ids, **meta)
            elif el.get("continues") and flow["item"] is not None and layer == ContentLayer.BODY:
                # the sentence the last page broke off: the same item, a provenance entry per page
                item = flow["item"]
                start = len(item.text) + 1
                item.text = item.text + " " + text
                item.orig = item.text
                p_ = prov(pno, box, text)
                p_.charspan = (start, start + len(text))
                item.prov.append(p_)
                flow["ids"] = flow["ids"] + ids
                item.meta.set_custom_field("regdocs", "words", flow["ids"])
                item.meta.set_custom_field("regdocs", "continued_on_page", pno)
                for i in ids:
                    owner[i] = item.self_ref
            else:
                item = doc.add_text(label=DocItemLabel[LABELS.get(label, "TEXT")], text=text,
                                    prov=prov(pno, box, text), content_layer=layer,
                                    parent=None if layer == ContentLayer.FURNITURE else under())
                tag(item, ids, **meta)
                if layer == ContentLayer.BODY and label in FLOWING:
                    flow["item"], flow["ids"] = item, list(ids)
    return doc, owner


def markdown(pages: list[dict], words: list[dict]) -> str:
    """Written from the elements, so it holds exactly what the document holds: furniture,
    text under pictures and table cells (a spanning cell once) included."""
    out, last_para = [], None
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
            elif el.get("section"):
                sec = el["section"]
                out.append("#" * min(sec["level"] + 1, 6) + " " + (text_of(ws).replace("\n", " ") or sec["title"]))
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
            elif el.get("continues") and last_para is not None:
                out[last_para] += " " + " ".join(text_of(ws).split())     # the sentence the break cut
            elif ws:
                out.append(text_of(ws))
                last_para = len(out) - 1 if label in FLOWING else None
                continue
            if label not in FURNITURE + ("line_numbers",):
                last_para = None if not el.get("continues") else last_para
    return "\n\n".join(out) + "\n"


def check_docling(json_bytes: bytes, words: list[dict]) -> dict:
    """The canonical output checked as a consumer would read it: the DoclingDocument JSON loaded
    back. Every evidence word must be named by exactly one item (`regdocs__words`), and each
    item's text (a table's cells) must hold the words it names."""
    import gzip
    from docling_core.types.doc import DoclingDocument, TableItem
    from docling_core.types.doc.common.content_layer import ContentLayer
    doc = json_bytes if isinstance(json_bytes, DoclingDocument) else \
        DoclingDocument.model_validate_json(gzip.decompress(json_bytes))
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
