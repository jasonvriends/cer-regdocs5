#!/usr/bin/env python3
"""Rates from a check.py survey, with 95% confidence intervals: the numbers docs/filing-guidance.md cites.

    ~/.venvs/regdocs-convert/bin/python scripts/convert/check_summary.py runs/v2/capture-fast/FILE.jsonl [--out FILE.md]

A document counts as "not fine" when most of its checked pages are not digital (a scan, a stub
layer, a suspect layer, text drawn as graphics). Rates are shares of documents with a Wilson
interval, so a group of 8 documents shows how little 8 can say. Facets are read live from the
scout record.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check import FINE  # noqa: E402

CLASSES = ["digital", "digital+images", "suspect", "stub", "scanner_ocr", "scan_no_layer", "text_as_curves", "blank", "other"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def main() -> int:
    from convert import OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("survey")
    ap.add_argument("--out")
    ap.add_argument("--min-docs", type=int, default=20)
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.survey).read_text().splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r]
    groups = {g: collections.defaultdict(lambda: {"docs": 0, "bad": 0, "pages": collections.Counter()})
              for g in ("producer", "tool_kind", "year", "doc_type")}
    total = {"docs": 0, "bad": 0, "pages": collections.Counter()}
    agree = collections.defaultdict(list)
    docs = collections.Counter()
    for r in rows:
        cer = OUT / r["doc_id"] / f"{r['doc_id']}.cer.meta.json.gz"
        rec = json.loads(gzip.decompress(cer.read_bytes())) if cer.exists() else {}
        keys = {"producer": r.get("producer") or "(none recorded)", "tool_kind": r.get("tool_kind") or "(none recorded)",
                "year": (rec.get("date") or "????")[:4],
                "doc_type": ",".join((rec.get("facets") or {}).get("Document Type") or []) or "(no facet)"}
        cls = collections.Counter(p["class"] for p in r["pages_checked"])
        bad = sum(n for c, n in cls.items() if c not in FINE) > len(r["pages_checked"]) / 2
        for g, k in keys.items():
            groups[g][k]["docs"] += 1
            groups[g][k]["bad"] += bad
            groups[g][k]["pages"].update(cls)
        total["docs"] += 1
        total["bad"] += bad
        total["pages"].update(cls)
        for p in r["pages_checked"]:
            if p.get("scanner_layer_agreement") is not None:
                agree[keys["producer"]].append(p["scanner_layer_agreement"])
        docs["tagged"] += r["tagged"]
        docs["pdfa"] += r["pdfa"]
        docs["long"] += r["pages"] > 20
        docs["long_outline"] += r["pages"] > 20 and r["outline"] > 0
        docs["form_fields"] += bool(r["form_fields"])
        docs["redacted"] += bool(r["redacted_words"])
        docs["oversize"] += bool(r["oversize_pages"])

    lo, hi = wilson(total["bad"], total["docs"])
    n_pages = sum(total["pages"].values())
    lines = [f"# Capture survey: {total['docs']} documents, {n_pages} pages checked", "",
             f"Documents mostly not digital: {total['bad']} ({100 * total['bad'] / total['docs']:.1f}%, "
             f"95% CI {100 * lo:.1f}-{100 * hi:.1f}%).", "",
             "Pages by class: " + ", ".join(f"{c} {100 * total['pages'][c] / n_pages:.1f}%" for c in CLASSES if total["pages"][c]), ""]

    def table(title, g):
        lines.extend([f"## {title} (groups of {args.min_docs}+ documents)", "",
                      "| group | documents | mostly not digital | 95% CI | " + " | ".join(CLASSES) + " |",
                      "|---|---:|---:|---|" + "---:|" * len(CLASSES)])
        for k, v in sorted(groups[g].items(), key=lambda kv: -kv[1]["docs"]):
            if v["docs"] < args.min_docs:
                continue
            a, b = wilson(v["bad"], v["docs"])
            n = sum(v["pages"].values())
            lines.append(f"| {k} | {v['docs']} | {100 * v['bad'] / v['docs']:.0f}% | {100 * a:.0f}-{100 * b:.0f}% | "
                         + " | ".join(f"{100 * v['pages'][c] / n:.0f}%" if v["pages"][c] else "" for c in CLASSES) + " |")
        lines.append("")

    table("By producer family", "producer")
    table("By kind of tool", "tool_kind")
    table("By year filed", "year")
    table("By REGDOCS document type", "doc_type")
    lines.append("## Scanner OCR layers: agreement with our OCR, by producer (pages)")
    lines.append("")
    for k, v in sorted(agree.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= 10:
            v = sorted(v)
            lines.append(f"- {k}: {len(v)} pages, median {v[len(v) // 2]:.0%}, under 60% on {100 * sum(1 for x in v if x < 0.6) / len(v):.0f}%")
    lines.extend(["", "## Documents", "",
                  f"- tagged: {docs['tagged']} of {total['docs']}; PDF/A: {docs['pdfa']}",
                  f"- bookmarks on documents over 20 pages: {docs['long_outline']} of {docs['long']}",
                  f"- live form fields: {docs['form_fields']}",
                  f"- words under a cover-up redaction: {docs['redacted']} documents (pages checked only)",
                  f"- oversize sheets: {docs['oversize']} documents"])
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
