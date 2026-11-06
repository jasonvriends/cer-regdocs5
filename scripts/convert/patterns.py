#!/usr/bin/env python3
"""The register of distinct conversion failure patterns: one record per pattern, a few examples each.

    PY=~/.venvs/regdocs-convert/bin/python
    $PY scripts/convert/patterns.py list                       # every pattern, its examples count
    $PY scripts/convert/patterns.py show D1                    # one pattern with its examples
    $PY scripts/convert/patterns.py add D1 4593518 1 --shows "..." --says "..." [--checked] [--note ...]
    $PY scripts/convert/patterns.py new I2 "name" --family I --in-file partly --remedy S --lesson "..."
    $PY scripts/convert/patterns.py merge                      # fold agents' confirmed new patterns in

The point is the distinct problem, not its count: a pattern keeps at most MAX_EXAMPLES examples
(the first confirmed against the page image are kept first), enough to go back to the documents.
The register is runs/v2/review/patterns.json; docs/conversion-failure-patterns.md is the prose.
Each pattern carries `lesson`: what the conversion process should do about it.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = HERE / "runs" / "v2" / "review" / "patterns.json"
AGENT_OUT = HERE / "runs" / "v2" / "review" / "agent-read" / "out"
MAX_EXAMPLES = 5


def load() -> dict:
    return json.loads(REG.read_text()) if REG.exists() else {"patterns": {}, "candidates": []}


def save(r: dict) -> None:
    tmp = REG.with_suffix(".tmp")
    tmp.write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, REG)


def add_example(p: dict, ex: dict) -> bool:
    exs = p.setdefault("examples", [])
    if any(e["doc_id"] == ex["doc_id"] and e.get("page") == ex.get("page") for e in exs):
        return False
    if len(exs) >= MAX_EXAMPLES:
        if not ex.get("checked_image"):
            return False
        unchecked = [i for i, e in enumerate(exs) if not e.get("checked_image")]
        if not unchecked:
            return False
        exs.pop(unchecked[-1])                      # a checked example replaces an unchecked one
    exs.append(ex)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("id")
    a = sub.add_parser("add")
    a.add_argument("id"); a.add_argument("doc_id"); a.add_argument("page", type=int)
    a.add_argument("--shows", default=""); a.add_argument("--says", default="")
    a.add_argument("--checked", action="store_true"); a.add_argument("--note", default="")
    n = sub.add_parser("new")
    n.add_argument("id"); n.add_argument("name"); n.add_argument("--family", required=True)
    n.add_argument("--in-file", required=True, choices=["yes", "partly", "no"])
    n.add_argument("--remedy", default=""); n.add_argument("--lesson", default="")
    sub.add_parser("merge")
    args = ap.parse_args()
    r = load()
    P = r["patterns"]
    if args.cmd == "list":
        for k, p in P.items():
            ok = sum(1 for e in p.get("examples", []) if e.get("checked_image"))
            print(f"{k:4} {p['name'][:70]:70} in file: {p['in_file']:6} examples {len(p.get('examples', []))} ({ok} checked)")
        print(f"{len(P)} patterns; {sum(1 for c in r.get('candidates', []) if not c.get('resolved'))} candidates waiting to be confirmed")
    elif args.cmd == "show":
        print(json.dumps(P[args.id], indent=1, ensure_ascii=False))
    elif args.cmd == "add":
        ok = add_example(P[args.id], {"doc_id": args.doc_id, "page": args.page, "page_shows": args.shows,
                                      "output_says": args.says, "checked_image": args.checked, "note": args.note})
        save(r); print("added" if ok else "not added (duplicate, or the pattern has enough examples)")
    elif args.cmd == "new":
        P[args.id] = {"name": args.name, "family": args.family, "in_file": args.in_file,
                      "remedy": args.remedy, "lesson": args.lesson, "examples": []}
        save(r); print(f"pattern {args.id} created")
    elif args.cmd == "merge":
        # agents' new patterns (INSTRUCTIONS-v2 output) go to `candidates` once; a person or the
        # coordinating session then turns each into a pattern (new) or an example (add)
        seen = {(c["doc_id"], c["page"], c["name"]) for c in r.setdefault("candidates", [])}
        merged = r.setdefault("merged_batches", [])
        k = 0
        for f in sorted(glob.glob(str(AGENT_OUT / "batch-*.jsonl"))):
            b = os.path.basename(f)
            if b in merged or not os.path.exists(f[:-6] + ".done"):
                continue
            for line in open(f):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                for x in d.get("new") or []:
                    key = (d["doc_id"], x.get("page"), x.get("name"))
                    if key not in seen:
                        r["candidates"].append({"doc_id": d["doc_id"], "batch": b, **x}); seen.add(key); k += 1
            merged.append(b)
        save(r); print(f"{k} new candidates from {len(merged)} batches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
