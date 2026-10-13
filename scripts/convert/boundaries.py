#!/usr/bin/env python3
"""What crosses a page boundary: research over converted documents, CPU only, from saved evidence.

    ./scripts/convert/convert.sh boundaries furniture --sample FILE ... [--out runs/v2/reports/x.json]

furniture   lines repeated in the same top or bottom band across a document's pages (running
            headers and footers, page numbers), against what the layout models labelled
            page_header / page_footer on the same words. Light mode has no layout models, so
            repetition is the only way it can tell furniture from body text.

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["furniture"])
    ap.add_argument("--sample", action="append", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    ids = list(dict.fromkeys(l.strip() for s in args.sample for l in Path(s).read_text().split() if l.strip()))
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
