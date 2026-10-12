#!/usr/bin/env python3
"""How each filing was captured, and what that cost: evidence for FILING-GUIDANCE.md.

    ~/.venvs/regdocs-convert/bin/python scripts/convert/capture.py --sample FILE [...] [--out FILE]

Reads each converted document's ledger (pages.json) and its PDF's catalog. Every page gets one
capture class, from what the converter measured on it:

  digital          a text layer that reproduces the page (OCR finds the same words)
  digital+images   a sound layer, plus text only in images (OCR words over ink the layer lacks)
  suspect          a layer that does not decode to the characters shown (mojibake) - OCR used
  stub             a scan carrying a few digital words (a stamp, a markup) - OCR used
  scanner_ocr      a scan with an invisible OCR layer from the scanner or filer
  scan_no_layer    a page image with no text at all
  text_as_curves   text drawn as vector outlines: no text layer, no page image, many paths, and
                   OCR reads words (a "print text as graphics" setting; Microsoft Print to PDF)
  blank            nothing printed (duplex backs, separators)
  other            the rest

and per document: tagged PDF, PDF/A, outline, live form fields, checkboxes printed flat, words
left under cover-up redactions, oversize sheets. Grouped by producer family and kind, year and
REGDOCS document type, as shares of pages - the rates a filing manual can cite.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CLASSES = ["digital", "digital+images", "suspect", "stub", "scanner_ocr", "scan_no_layer", "text_as_curves", "blank", "other"]


def page_class(p: dict) -> str:
    w, f = p.get("words") or {}, p.get("facts") or {}
    tl = p.get("text_layer")
    if any(i["kind"] == "blank_page" for i in p.get("issues") or []):
        return "blank"
    if tl == "sound":
        return "digital+images" if w.get("ocr_extra", 0) >= 20 else "digital"
    if tl == "suspect":
        return "suspect"
    if tl == "stub":
        return "stub"
    if tl == "hidden_ocr":
        return "scanner_ocr"
    if (f.get("largest_image_cover") or 0) >= 0.8:
        return "scan_no_layer"
    if w.get("pdf", 0) == 0 and w.get("ocr", 0) >= 20 and (f.get("paths") or 0) >= 200:
        return "text_as_curves"
    return "other"


def catalog_facts(pdf_path: Path) -> dict:
    import pymupdf
    try:
        d = pymupdf.open(pdf_path)
    except Exception:
        return {}
    cat = d.pdf_catalog()
    keys = d.xref_get_keys(cat)
    xmp = d.get_xml_metadata() or ""
    tagged = "StructTreeRoot" in keys
    mark = d.xref_get_key(cat, "MarkInfo")
    out = {"tagged": tagged or ("Marked true" in str(mark)), "pdfa": "pdfaid:part" in xmp,
           "outline": len(d.get_toc() or []), "form_fields": d.is_form_pdf or 0,
           "pages": d.page_count}
    d.close()
    return out


def main() -> int:
    import gzip
    from convert import OUT, SOURCE, out_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="append", required=True)
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args()
    ids = list(dict.fromkeys(l.strip() for f in args.sample for l in Path(f).read_text().split() if l.strip()))
    groups = {k: collections.defaultdict(collections.Counter) for k in ("producer", "kind", "year", "doc_type")}
    docs = collections.Counter()
    doc_rows = []
    hidden_quality = collections.defaultdict(list)
    by_doc = {k: collections.defaultdict(collections.Counter) for k in ("kind", "doc_type", "year")}
    n_docs = 0
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        if not led.exists():
            continue
        n_docs += 1
        L = json.loads(led.read_text())
        c = L["context"]
        tool = c["pdf"].get("producer") or c["pdf"].get("creator") or {}
        # facets read live from the scout record: enrichment after conversion (2002-2011 had none
        # at conversion time) shows here without converting again
        cer = OUT / d / f"{d}.cer.meta.json.gz"
        facets = (json.loads(gzip.decompress(cer.read_bytes())).get("facets") or {}) if cer.exists() \
            else c["regdocs"]["facets"]
        keys = {"producer": tool.get("family") or "(none recorded)", "kind": tool.get("kind") or "(none recorded)",
                "year": (c["regdocs"].get("date") or "????")[:4],
                "doc_type": ",".join(facets.get("Document Type") or []) or "(no facet)"}
        cat = catalog_facts(SOURCE / f"{d}.pdf")
        dom = collections.Counter(page_class(p) for p in L["pages"]).most_common(1)
        for g in ("kind", "doc_type", "year"):
            by_doc[g][keys[g]][dom[0][0] if dom else "other"] += 1
        redacted = sum((p.get("words") or {}).get("redacted", 0) for p in L["pages"])
        checkboxes = sum(len(p.get("checkboxes") or []) for p in L["pages"])
        oversize = sum(1 for p in L["pages"] if (p.get("facts") or {}).get("size") == "oversize")
        for p in L["pages"]:
            cls = page_class(p)
            for g, k in keys.items():
                groups[g][k][cls] += 1
            if cls == "scanner_ocr" and p.get("hidden_layer_agreement"):
                a = p["hidden_layer_agreement"].get("pdf_found_by_ocr")
                if a is not None:
                    hidden_quality[keys["producer"]].append(a)
        docs["tagged"] += bool(cat.get("tagged"))
        docs["pdfa"] += bool(cat.get("pdfa"))
        docs["outline"] += bool(cat.get("outline"))
        docs["outline_long"] += bool(cat.get("outline")) and cat.get("pages", 0) > 20
        docs["long"] += cat.get("pages", 0) > 20
        docs["form_fields"] += bool(cat.get("form_fields"))
        docs["flat_checkboxes"] += bool(checkboxes) and not cat.get("form_fields")
        docs["redaction_left_text"] += bool(redacted)
        docs["oversize"] += bool(oversize)
        doc_rows.append({"doc": d, **keys, **cat, "redacted_words": redacted, "checkboxes": checkboxes})

    lines = [f"# Capture report: {n_docs} documents, {sum(sum(v.values()) for v in groups['year'].values())} pages", ""]

    def table(title, g, min_pages=1):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| group | pages | " + " | ".join(CLASSES) + " |")
        lines.append("|---|---:|" + "---:|" * len(CLASSES))
        rows = sorted(groups[g].items(), key=lambda kv: -sum(kv[1].values()))
        for k, cnt in rows:
            n = sum(cnt.values())
            if n < min_pages:
                continue
            lines.append(f"| {k} | {n} | " + " | ".join(f"{100 * cnt[c] / n:.0f}%" if cnt[c] else "" for c in CLASSES) + " |")
        lines.append("")

    def doc_table(title, g, min_docs=1):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| group | documents | " + " | ".join(CLASSES) + " |")
        lines.append("|---|---:|" + "---:|" * len(CLASSES))
        for k, cnt in sorted(by_doc[g].items(), key=lambda kv: -sum(kv[1].values())):
            n = sum(cnt.values())
            if n >= min_docs:
                lines.append(f"| {k} | {n} | " + " | ".join(str(cnt[c]) if cnt[c] else "" for c in CLASSES) + " |")
        lines.append("")

    lines.append("Pages show how much text a practice affects; documents (by their most common page class)")
    lines.append("show how many filings it affects, so one long filing cannot set a type's rate.")
    lines.append("")
    doc_table("Documents by their main capture class, by REGDOCS document type", "doc_type", 3)
    doc_table("Documents by their main capture class, by kind of tool", "kind")
    doc_table("Documents by their main capture class, by year", "year")
    table("Pages by capture class, by producer family", "producer", 10)
    table("By kind of tool", "kind")
    table("By year filed", "year")
    table("By REGDOCS document type", "doc_type", 10)
    lines.append("## Scanner OCR layers: how much of the layer our OCR also reads, by producer")
    lines.append("")
    for k, v in sorted(hidden_quality.items(), key=lambda kv: -len(kv[1])):
        v = sorted(v)
        lines.append(f"- {k}: {len(v)} pages, median {v[len(v) // 2]:.0%}, under 60% on {sum(1 for x in v if x < 0.6)}")
    lines.append("")
    lines.append("## Documents")
    lines.append("")
    lines.append(f"- tagged PDF (structure for accessibility): {docs['tagged']} of {n_docs}")
    lines.append(f"- PDF/A: {docs['pdfa']} of {n_docs}")
    lines.append(f"- an outline (bookmarks): {docs['outline']} of {n_docs}; of documents over 20 pages, "
                 f"{docs['outline_long']} of {docs['long']}")
    lines.append(f"- live form fields: {docs['form_fields']}; checkboxes printed flat (detected, no form fields): "
                 f"{docs['flat_checkboxes']}")
    lines.append(f"- words left in the text layer under a cover-up redaction: {docs['redaction_left_text']} documents")
    lines.append(f"- oversize sheets (over 17.5 in): {docs['oversize']} documents")
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
