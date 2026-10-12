#!/usr/bin/env python3
"""Intake check: how a PDF was captured, page by page, and what that will cost a reader.

    ./scripts/convert/convert.sh check some.pdf                  # one file, a readable report
    ./scripts/convert/convert.sh check some.pdf --json           # the same as JSON
    ./scripts/convert/convert.sh check --random 5000 --pages 3 --out FILE.jsonl   # corpus survey

The checks docs/filing-guidance.md section 8 lists, on the file alone: no layout or table models,
only the text layer and one OCR reading of each page checked. Every page gets a capture class:

  digital          the text layer holds the words the page shows
  digital+images   ... plus text only in images (20+ OCR words over ink the layer lacks)
  suspect          the layer does not decode to the characters shown (mojibake)
  stub             a scan carrying a few digital words (a stamp, a markup)
  scanner_ocr      a scan with an invisible OCR layer (its agreement with our OCR is given)
  scan_no_layer    a page image with no text at all
  text_as_curves   text drawn as vector outlines: no text layer, many paths, OCR reads words
  blank            nothing printed
  other

and the document: tagged, PDF/A, bookmarks on long documents, live form fields, words under a
cover-up redaction, oversize sheets. A page is "fine" only as digital (or digital+images).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402
import readers as rd  # noqa: E402

FINE = {"digital", "digital+images", "blank"}
ADVICE = {
    "suspect": "the text layer does not decode to the characters shown: export again from the source application (no re-distilling, keep font data)",
    "stub": "a scan with a few digital words: OCR the page (300 dpi) and check the result",
    "scanner_ocr": "a scan with an OCR layer: check the layer reads the page (agreement below)",
    "scan_no_layer": "a page image with no text: export from the source, sign digitally, or OCR it",
    "text_as_curves": "text drawn as graphics: turn off 'print text as graphics' / export CAD text as text",
    "digital+images": "text inside images is not in the text layer",
}


def classify(verdict, n_layer, n_ocr, extra, facts, ink) -> str:
    if n_layer == 0 and n_ocr == 0 and ink is not None and ink < 0.002:
        return "blank"
    if verdict == "sound":
        if facts["largest_image_cover"] >= 0.8 and n_layer < 100 and n_ocr > 4 * max(1, n_layer):
            return "stub"
        return "digital+images" if extra >= 20 else "digital"
    if verdict == "suspect":
        return "suspect"
    if verdict == "hidden_ocr":
        return "scanner_ocr"
    if facts["largest_image_cover"] >= 0.8:
        return "scan_no_layer"
    if n_layer == 0 and n_ocr >= 20 and facts["paths"] >= 200:
        return "text_as_curves"
    return "other"


def check_page(ocr, page) -> dict:
    facts = rd.page_facts(page)
    words, health = rd.pdf_words(page)
    verdict = rd.text_layer_verdict(health)
    visible = [w for w in words if not w["hidden"] and not w["redacted"]]
    o = ocr.ocr(page) if ocr else []
    extra = sum(1 for w in o if sum(asm.inter(w["box"], v["box"]) for v in visible)
                <= asm.SETTINGS["ocr_extra_overlap"] * max(asm.area(w["box"]), 1e-6))
    ink = rd.ink_share(page) if not visible and not o else None
    cls = classify(verdict, len(visible), len(o), extra, facts, ink)
    hidden = [w for w in words if w["hidden"]]
    out = {"class": cls, "layer_words": len(visible), "ocr_words": len(o), "ocr_only_words": extra,
           "size": facts["size"], "orientation": facts["orientation"], "paths": facts["paths"],
           "image_cover": facts["largest_image_cover"], "redacted_words": sum(1 for w in words if w["redacted"]),
           "form_fields": len(facts["widgets"])}
    if hidden and o:
        out["scanner_layer_agreement"] = asm.match_readings(hidden, o)["pdf_found_by_ocr"]
    return out


def check_file(path: Path, ocr, max_pages: int = 0) -> dict:
    import pymupdf
    t0 = time.perf_counter()
    doc = pymupdf.open(path)
    n = doc.page_count
    if max_pages and n > max_pages:     # first, middle and last pages, spread across the rest
        idx = sorted({round(i * (n - 1) / (max_pages - 1)) for i in range(max_pages)}) if max_pages > 1 else [0]
    else:
        idx = list(range(n))
    pages = [{"page": i + 1, **check_page(ocr, doc[i])} for i in idx]
    cat = doc.pdf_catalog()
    keys = doc.xref_get_keys(cat)
    md = doc.metadata or {}
    tool = rd.tool_family(md.get("producer")) or rd.tool_family(md.get("creator")) or {}
    out = {"file": str(path), "doc_id": path.stem, "pages": n, "checked_pages": len(pages),
           "producer": tool.get("family"), "tool_kind": tool.get("kind"), "producer_raw": md.get("producer"),
           "tagged": "StructTreeRoot" in keys, "pdfa": "pdfaid:part" in (doc.get_xml_metadata() or ""),
           "outline": len(doc.get_toc() or []), "form_fields": doc.is_form_pdf or 0,
           "oversize_pages": sum(1 for p in pages if p["size"] == "oversize"),
           "redacted_words": sum(p["redacted_words"] for p in pages),
           "pages_checked": pages, "seconds": round(time.perf_counter() - t0, 2)}
    doc.close()
    return out


def report(r: dict) -> str:
    lines = [f"{r['file']}: {r['pages']} pages ({r['checked_pages']} checked), made by {r['producer_raw'] or 'an unrecorded tool'}"]
    bad = [p for p in r["pages_checked"] if p["class"] not in FINE]
    lines.append(f"  pages fine: {len(r['pages_checked']) - len(bad)} of {len(r['pages_checked'])}")
    for p in bad:
        note = ADVICE.get(p["class"], "")
        agree = f" (its OCR layer matches our OCR on {p['scanner_layer_agreement']:.0%})" if p.get("scanner_layer_agreement") is not None else ""
        lines.append(f"  page {p['page']}: {p['class']}{agree} - {note} [layer {p['layer_words']} words, OCR {p['ocr_words']}]")
    for p in r["pages_checked"]:
        if p["class"] == "digital+images":
            lines.append(f"  page {p['page']}: {p['ocr_only_words']} words only in images")
    if r["redacted_words"]:
        lines.append(f"  {r['redacted_words']} words still in the text layer under cover-up redactions - remove them")
    if r["oversize_pages"]:
        lines.append(f"  {r['oversize_pages']} oversize sheets (over 17.5 in)")
    if r["pages"] > 20 and not r["outline"]:
        lines.append("  no bookmarks on a document over 20 pages")
    lines.append(f"  tagged: {'yes' if r['tagged'] else 'no'}; PDF/A: {'yes' if r['pdfa'] else 'no'}; "
                 f"form fields: {r['form_fields']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdfs", nargs="*")
    ap.add_argument("--random", type=int, help="check this many PDFs drawn at random from source/")
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--pages", type=int, default=0, help="check at most this many pages per file (0: all)")
    ap.add_argument("--out", help="append one JSON line per file here (resumes: files already in it are skipped)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-ocr", action="store_true", help="text layer only (cannot see scans or stub layers)")
    args = ap.parse_args()
    from convert import SOURCE
    paths = [Path(p) for p in args.pdfs]
    if args.random:
        names = sorted(n for n in os.listdir(SOURCE) if n.endswith(".pdf"))
        paths += [SOURCE / n for n in random.Random(args.seed).sample(names, min(args.random, len(names)))]
    done = set()
    if args.out and Path(args.out).exists():
        done = {json.loads(l)["doc_id"] for l in Path(args.out).read_text().splitlines() if l.strip()}
    ocr = None if args.no_ocr else rd.OcrOnly()
    out = open(args.out, "a") if args.out else None
    t0 = time.perf_counter()
    for k, p in enumerate(paths, 1):
        if p.stem in done:
            continue
        try:
            r = check_file(p, ocr, args.pages)
        except Exception as e:
            r = {"file": str(p), "doc_id": p.stem, "error": repr(e)}
        if out:
            out.write(json.dumps(r) + "\n")
            out.flush()
            if k % 100 == 0:
                print(f"{k}/{len(paths)} files, {time.perf_counter() - t0:.0f}s", flush=True)
        else:
            print(json.dumps(r, indent=1) if args.json else report(r) if "error" not in r else f"{p}: {r['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
