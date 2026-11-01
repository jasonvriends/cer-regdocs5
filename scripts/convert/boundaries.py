#!/usr/bin/env python3
"""What crosses a page boundary: research over converted documents, CPU only, from saved evidence.

    ./scripts/convert/convert.sh boundaries furniture --sample FILE ... [--out runs/v2/reports/x.json]

furniture   lines repeated in the same top or bottom band across a document's pages (running
            headers and footers, page numbers), against what the layout models labelled
            page_header / page_footer on the same words. Light mode has no layout models, so
            repetition is the only way it can tell furniture from body text.

tables      a table ending in the bottom quarter of a page followed by one starting in the top
            quarter of the next (furniture aside): the same number of columns? does the second
            open with the first's header line? - how often a table runs across pages.

flow        what a page break interrupts: section headings at the top of a page or partway down,
            and sentences split across the break (a page's last body line without closing
            punctuation, the next page's first body line going on) - furniture, by repetition,
            set aside.

Reads each document's ledger (pages.json), words.parquet and regions.parquet; writes nothing
but its report.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "output"
BAND = 0.12              # top and bottom share of the page height where furniture sits
DIGITS = re.compile(r"\d+|\b[ivxlc]{1,6}\b")         # page numbers, roman ones too ("ii", "iv")


def shape(words: list[dict]) -> str:
    """A line's text with numbers as '#', so "Page 3 of 12" repeats as "page # of #" (and a
    roman page number as "#")."""
    return DIGITS.sub("#", " ".join(asm.norm(w["text"]) for w in words)).strip()


def load(doc_id: str):
    import pyarrow.parquet as pq
    d = OUT / doc_id / "convert"
    L = json.loads((d / f"{doc_id}.pages.json").read_text())
    words = [w for w in pq.read_table(d / f"{doc_id}.words.parquet").to_pylist() if w["evidence"]]
    rf = d / f"{doc_id}.regions.parquet"
    regions = [r for r in pq.read_table(rf).to_pylist() if r["model"] == "merged" and r["kept"]] if rf.exists() else []
    return L, words, regions


def furniture(doc_id: str) -> dict | None:
    L, words, regions = load(doc_id)
    pages = {p["page"]: p for p in L["pages"] if not p.get("error")}
    if len(pages) < 4:
        return None
    by_page = collections.defaultdict(list)
    for w in words:
        w["box"] = (w["x0"], w["y0"], w["x1"], w["y1"])
        by_page[w["page"]].append(w)
    furn_regions = collections.defaultdict(list)
    for r in regions:
        if r["label"] in ("page_header", "page_footer"):
            furn_regions[r["page"]].append((r["label"], (r["x0"], r["y0"], r["x1"], r["y1"])))
    # every line in a band, by its shape
    seen = collections.defaultdict(set)          # (band, shape) -> pages
    lines = []                                   # (page, band, shape, words)
    for pno, ws in by_page.items():
        h = pages.get(pno, {}).get("facts", {}).get("height_pt")
        if not h:
            continue
        for ln in asm.lines_of(ws):
            y = sum(asm.centre(w["box"])[1] for w in ln) / len(ln)
            band = "top" if y < BAND * h else "bottom" if y > (1 - BAND) * h else None
            if band is None:
                continue
            s = shape(ln)
            if not s or not any(ch.isalnum() for ch in s):
                continue
            seen[(band, s)].add(pno)
            lines.append((pno, band, s, ln))
    need = max(3, 0.3 * len(pages))
    out = collections.Counter()
    examples = collections.defaultdict(list)
    for pno, band, s, ln in lines:
        repeated = len(seen[(band, s)]) >= need
        cx, cy = asm.centre((min(w["box"][0] for w in ln), min(w["box"][1] for w in ln),
                             max(w["box"][2] for w in ln), max(w["box"][3] for w in ln)))
        labelled = any(asm.inside(b, cx, cy, 4.0) for _, b in furn_regions.get(pno, []))
        key = ("repeated" if repeated else "single") + "+" + ("layout_furniture" if labelled else "layout_body")
        out[key] += 1
        if len(examples[key]) < 3:
            examples[key].append(f"p{pno} {band}: {s[:60]}")
    return {"doc_id": doc_id, "pages": len(pages), "counts": dict(out), "examples": dict(examples),
            "furniture_regions": sum(len(v) for v in furn_regions.values())}


def similar(a: str, b: str) -> bool:
    """Two header lines alike enough: OCR differs a little between pages ("tera com" / "tera
    comm"), and a line may be cut at a different word."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if len(ta) >= 3 and len(tb) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    sa, sb = set(ta), set(tb)
    return len(sa & sb) / len(sa | sb) >= 0.7


def table_pairs(doc_id: str) -> list[dict]:
    import pyarrow.parquet as pq
    L, words, regions = load(doc_id)
    pages = {p["page"]: p for p in L["pages"] if not p.get("error")}
    tf = OUT / doc_id / "convert" / f"{doc_id}.tables.parquet"
    cols = {(t["page"], t["region"]): t["cols"] for t in pq.read_table(tf).to_pylist() if t["chosen"]} \
        if tf.exists() else {}
    by_page = collections.defaultdict(list)
    for w in words:
        w["box"] = (w["x0"], w["y0"], w["x1"], w["y1"])
        by_page[w["page"]].append(w)
    tables = collections.defaultdict(list)
    body_top = {}
    for r in regions:
        box = (r["x0"], r["y0"], r["x1"], r["y1"])
        if r["label"] == "table":
            tables[r["page"]].append((r["region"], box))
        if r["label"] not in ("page_header", "page_footer"):
            body_top[r["page"]] = min(body_top.get(r["page"], 1e9), r["y0"])

    def first_line(pno, box):
        ws = [w for w in by_page.get(pno, []) if asm.inside(box, *asm.centre(w["box"]), 2.0)]
        lines = asm.lines_of(ws)
        return shape(lines[0]) if lines else ""

    out = []
    for pno, ts in tables.items():
        h = pages.get(pno, {}).get("facts", {}).get("height_pt")
        nxt = tables.get(pno + 1)
        h2 = pages.get(pno + 1, {}).get("facts", {}).get("height_pt")
        if not h or not nxt or not h2:
            continue
        a = max(ts, key=lambda t: t[1][3])                 # the lowest table on this page
        b = min(nxt, key=lambda t: t[1][1])                # the highest on the next
        if a[1][3] < 0.75 * h or b[1][1] > max(0.25 * h2, body_top.get(pno + 1, 0) + 1):
            continue
        ca, cb = cols.get((pno, a[0])), cols.get((pno + 1, b[0]))
        ha, hb = first_line(pno, a[1]), first_line(pno + 1, b[1])
        out.append({"doc_id": doc_id, "page": pno, "cols": [ca, cb], "same_cols": ca is not None and ca == cb,
                    "header_repeated": similar(ha, hb), "header": ha[:60], "next_first_line": hb[:60]})
    return out


END = re.compile(r"[.!?:;]['\")\]]?$")


def flow(doc_id: str) -> dict | None:
    L, words, regions = load(doc_id)
    pages = {p["page"]: p for p in L["pages"] if not p.get("error")}
    if len(pages) < 2:
        return None
    by_page = collections.defaultdict(list)
    for w in words:
        w["box"] = (w["x0"], w["y0"], w["x1"], w["y1"])
        by_page[w["page"]].append(w)
    # furniture: band lines that repeat, and what the layout calls header/footer/line numbers
    seen, band_lines = collections.defaultdict(set), {}
    for pno, ws in by_page.items():
        h = pages.get(pno, {}).get("facts", {}).get("height_pt") or 792
        lines = asm.lines_of(ws)
        band_lines[pno] = lines
        for ln in lines:
            y = sum(asm.centre(w["box"])[1] for w in ln) / len(ln)
            if y < BAND * h or y > (1 - BAND) * h:
                seen[shape(ln)].add(pno)
    need = max(3, 0.3 * len(pages))
    furn = collections.defaultdict(list)
    heads = collections.defaultdict(list)
    tabular = collections.defaultdict(list)
    for r in regions:
        box = (r["x0"], r["y0"], r["x1"], r["y1"])
        if r["label"] in ("page_header", "page_footer", "line_numbers"):
            furn[r["page"]].append(box)
        elif r["label"] in ("section_header", "title"):
            heads[r["page"]].append(box)
        elif r["label"] in ("table", "picture", "document_index", "form"):
            tabular[r["page"]].append(box)
    body = {}
    for pno, lines in band_lines.items():
        keep = []
        for ln in lines:
            cx = sum(asm.centre(w["box"])[0] for w in ln) / len(ln)
            cy = sum(asm.centre(w["box"])[1] for w in ln) / len(ln)
            if (len(seen[shape(ln)]) >= need and len(pages) >= 4) or \
                    any(asm.inside(b, cx, cy, 3.0) for b in furn[pno]) or \
                    any(asm.inside(b, cx, cy, 3.0) for b in tabular[pno]):
                continue
            keep.append(ln)
        body[pno] = keep
    out = collections.Counter()
    examples = collections.defaultdict(list)
    for pno, hs in heads.items():
        lines = body.get(pno) or []
        top = min((asm.centre(ln[0]["box"])[1] for ln in lines), default=None)
        for b in hs:
            first = top is not None and b[1] <= top + 2
            out["heading_first_on_page" if first else "heading_mid_page"] += 1
    for pno in sorted(body):
        nxt = body.get(pno + 1)
        if not body[pno] or not nxt:
            continue
        last = " ".join(w["text"] for w in body[pno][-1])
        first = " ".join(w["text"] for w in nxt[0])
        open_end = not END.search(last.strip())
        goes_on = first[:1].islower() or first[:1] in ",;)" if first else False
        key = "break_mid_sentence" if open_end and goes_on else \
            "break_open_end_only" if open_end else "break_at_sentence_end"
        out[key] += 1
        if len(examples[key]) < 4:
            examples[key].append(f"p{pno}: ...{last[-50:]} || {first[:50]}...")
    return {"doc_id": doc_id, "pages": len(pages), "counts": dict(out), "examples": dict(examples)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["furniture", "tables", "flow"])
    ap.add_argument("--sample", action="append", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    ids = list(dict.fromkeys(l.strip() for s in args.sample for l in Path(s).read_text().split() if l.strip()))
    if args.what == "tables":
        pairs = []
        for d in ids:
            try:
                pairs += table_pairs(d)
            except (OSError, ValueError, KeyError):
                continue
        c = collections.Counter((p["same_cols"], p["header_repeated"]) for p in pairs)
        docs = collections.defaultdict(list)
        for p in pairs:
            docs[p["doc_id"]].append(p)
        summary = {"pairs": len(pairs), "documents": len(docs), "of_documents": len(ids),
                   "documents_with_a_header_repeated": sum(1 for v in docs.values() if any(p["header_repeated"] for p in v)),
                   "same_cols_and_header": c[(True, True)], "same_cols_only": c[(True, False)],
                   "header_only": c[(False, True)], "neither": c[(False, False)]}
        print(json.dumps(summary, indent=1))
        if args.out:
            Path(args.out).write_text(json.dumps({"summary": summary, "pairs": pairs}, indent=1))
        return 0
    if args.what == "flow":
        rows, total = [], collections.Counter()
        for d in ids:
            try:
                r = flow(d)
            except (OSError, ValueError, KeyError):
                continue
            if r:
                rows.append(r)
                total.update(r["counts"])
        breaks = total["break_mid_sentence"] + total["break_open_end_only"] + total["break_at_sentence_end"]
        summary = {"documents": len(rows), **dict(total),
                   "share_of_breaks_mid_sentence": round(total["break_mid_sentence"] / max(1, breaks), 3),
                   "share_of_headings_mid_page": round(total["heading_mid_page"] / max(1, total["heading_mid_page"]
                                                                                       + total["heading_first_on_page"]), 3),
                   "documents_with_a_sentence_across_pages": sum(1 for r in rows if r["counts"].get("break_mid_sentence"))}
        print(json.dumps(summary, indent=1))
        if args.out:
            Path(args.out).write_text(json.dumps({"summary": summary, "documents": rows}, indent=1))
        return 0
    rows, total = [], collections.Counter()
    for d in ids:
        try:
            r = furniture(d)
        except (OSError, ValueError, KeyError):
            continue
        if r:
            rows.append(r)
            total.update(r["counts"])
    rep, lab = total["repeated+layout_furniture"], total["single+layout_furniture"]
    summary = {"documents": len(rows), "band_lines": dict(total),
               "repeated_lines_layout_calls_furniture": rep / max(1, rep + total["repeated+layout_body"]),
               "layout_furniture_lines_that_repeat": rep / max(1, rep + lab),
               "documents_with_repeated_lines": sum(1 for r in rows if r["counts"].get("repeated+layout_body", 0)
                                                    + r["counts"].get("repeated+layout_furniture", 0))}
    print(json.dumps(summary, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "documents": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
