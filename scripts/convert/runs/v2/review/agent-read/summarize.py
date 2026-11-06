#!/usr/bin/env python3
"""Tally the reading agents' findings: out/batch-*.jsonl -> counts by kind and severity, by document
type and producer (from batches/*.tsv), and every "other" finding to read for new fault kinds.
    ~/.venvs/regdocs-convert/bin/python summarize.py [--other]"""
import collections, glob, json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
meta = {}
for f in glob.glob(f"{here}/batches/*.tsv"):
    for l in open(f):
        d, pages, n, y, prod, t = l.rstrip("\n").split("\t")
        meta[d] = (t, prod, y)
docs, bad, kinds, bytype, other, broken = 0, 0, collections.Counter(), collections.defaultdict(lambda: [0, 0]), [], 0
done = len(glob.glob(f"{here}/out/*.done"))
for f in sorted(glob.glob(f"{here}/out/batch-*.jsonl")):
    for l in open(f):
        try:
            r = json.loads(l)
        except ValueError:
            broken += 1; continue
        docs += 1
        t = meta.get(r["doc_id"], ("?",))[0]
        bytype[t][0] += 1
        sev = {x["severity"] for x in r.get("findings") or []}
        if "meaning" in sev:
            bad += 1; bytype[t][1] += 1
        for x in r.get("findings") or []:
            kinds[(x["kind"], x["severity"])] += 1
            if x["kind"] == "other":
                other.append((r["doc_id"], x["page"], x["quote"][:80], x["explanation"][:200]))
print(f"{docs} documents read ({done} batches done, {broken} unparseable lines); {bad} with a meaning-level fault")
for (k, s), n in sorted(kinds.items(), key=lambda x: -x[1]):
    print(f"  {k:22} {s:10} {n}")
print("by document type (read, with a meaning fault):")
for t, (n, b) in sorted(bytype.items(), key=lambda x: -x[1][0])[:20]:
    print(f"  {t[:45]:45} {n:4} {b:4}")
if "--other" in sys.argv:
    for o in other:
        print(" ", *o)
