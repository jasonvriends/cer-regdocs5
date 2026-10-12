#!/usr/bin/env python3
"""Reading order: how far our item order departs from the text layer's own order.

    ~/.venvs/regdocs-convert/bin/python scripts/convert/order_bench.py --sample FILE ...

On a born-digital page the text layer usually stores words in the order they were written.
Per page, items (in DoclingDocument order) are ranked by their first text-layer word; the share
of item pairs in the other order is the page's disagreement. It is a symptom, not a verdict:
either order can be the wrong one, so the worst pages are listed to be looked at.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from docling_core.types.doc import DoclingDocument
    from docling_core.types.doc.common.content_layer import ContentLayer
    from convert import out_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="append", required=True)
    ap.add_argument("--worst", type=int, default=15)
    args = ap.parse_args()
    ids = [l.strip() for f in args.sample for l in Path(f).read_text().split() if l.strip()]
    rows = []
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        dj = out_dir(d) / f"{d}.docling.json.gz"
        if not led.exists() or not dj.exists():
            continue
        L = json.loads(led.read_text())
        pages = {p["page"]: p for p in L["pages"]}
        doc = DoclingDocument.model_validate_json(gzip.decompress(dj.read_bytes()))
        firsts = collections.defaultdict(list)       # page -> first word id of each body item, in order
        for item, _ in doc.iterate_items(included_content_layers={ContentLayer.BODY}, with_groups=False,
                                         traverse_pictures=True):
            ids_ = ((item.meta.get_custom_part() if item.meta else {}) or {}).get("regdocs__words") or []
            if ids_ and item.prov:
                firsts[item.prov[0].page_no].append(min(ids_))
        for pno, seq in firsts.items():
            p = pages.get(pno) or {}
            if p.get("source") != "pdf" or len(seq) < 4:
                continue
            n = len(seq)
            inv = sum(1 for i in range(n) for j in range(i + 1, n) if seq[i] > seq[j])
            rows.append({"doc": d, "page": pno, "items": n, "disagree": inv / (n * (n - 1) / 2),
                         "columns": (p.get("facts") or {}).get("columns"),
                         "producer": ((L["context"]["pdf"].get("producer") or L["context"]["pdf"].get("creator") or {}).get("family"))})
    if not rows:
        print("no pages")
        return 1
    by = collections.defaultdict(list)
    for r in rows:
        by[r["columns"]].append(r["disagree"])
    print(f"{len(rows)} born-digital pages with 4+ body items")
    for cols, v in sorted(by.items(), key=lambda kv: str(kv[0])):
        v = sorted(v)
        print(f"  columns={cols}: {len(v)} pages, median disagreement {v[len(v)//2]:.3f}, "
              f">0.2 on {sum(1 for x in v if x > 0.2)} pages")
    prod = collections.defaultdict(list)
    for r in rows:
        prod[r["producer"]].append(r["disagree"])
    print("  by producer (mean, pages):", ", ".join(f"{k} {sum(v)/len(v):.3f} ({len(v)})"
                                                  for k, v in sorted(prod.items(), key=lambda kv: -len(kv[1]))[:10]))
    print("worst pages:")
    for r in sorted(rows, key=lambda r: -r["disagree"])[:args.worst]:
        print(f"  {r['doc']} p{r['page']}: {r['disagree']:.2f} over {r['items']} items, columns {r['columns']}, {r['producer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
