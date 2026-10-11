"""Compare convert runs on the survey sample, page kind by page kind.

    scripts/convert/.venv/bin/python scripts/convert/survey/compare.py --pages pages-<sig> convert-<sig> convert-<sig> ...

For each run: documents converted, errors and warnings per 100 pages of each page kind
(the kind from the pages run), the share of evidence words that reach the Markdown, and
seconds per page. Kinds come from one pages run so every convert run is judged on the same pages.
"""
import argparse
import collections
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DEFAULT_OUTPUT, read_json_gz  # noqa: E402

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="convert run folder names, e.g. convert-4210025d")
    ap.add_argument("--pages", required=True, help="pages run folder name giving each page's kind")
    ap.add_argument("--sample", default=str(HERE / "sample-2026-10-10.csv"))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = ap.parse_args(argv)
    out = Path(args.output)
    ids = [row["doc_id"] for row in csv.DictReader(open(args.sample))]

    kind_of = {}
    for i in ids:
        f = out / i / "convert" / args.pages / f"{i}.pages.json.gz"
        if f.exists():
            for p in read_json_gz(f).get("pages") or []:
                kind_of[(i, p["page"])] = p["kind"].split(":")[0]
    pages_of_kind = collections.Counter(kind_of.values())

    rows = {}
    for run in args.runs:
        sev = collections.defaultdict(collections.Counter)      # kind -> severity -> count
        kinds = collections.defaultdict(collections.Counter)    # severity -> issue kind -> count
        docs = secs = pages = 0
        recall, statuses = [], collections.Counter()
        for i in ids:
            d = out / i / "convert" / run
            rec_f = d / f"{i}.convert.json.gz"
            if not rec_f.exists():
                continue
            rec = read_json_gz(rec_f)
            docs += 1
            statuses[rec["status"]] += 1
            secs += rec.get("seconds") or 0
            s = rec.get("summary") or {}
            pages += s.get("pages", 0)
            if s.get("export_word_recall") is not None:
                recall.append(s["export_word_recall"])
            iss = d / f"{i}.issues.json"
            for x in json.load(open(iss)) if iss.exists() else []:
                if x["severity"] in ("error", "warning"):
                    sev[kind_of.get((i, x.get("page")), "?")][x["severity"]] += 1
                    kinds[x["severity"]][x["kind"]] += 1
        rows[run] = (docs, statuses, secs, pages, recall, sev, kinds)

    print(f"page kinds from {args.pages}: " + ", ".join(f"{k} {v}" for k, v in pages_of_kind.most_common()))
    for run, (docs, statuses, secs, pages, recall, sev, kinds) in rows.items():
        print(f"\n{run}: {docs} documents ({', '.join(f'{k} {v}' for k, v in statuses.items())}), "
              f"{pages} pages, {secs / pages if pages else 0:.2f} s/page")
        if recall:
            print(f"  export word recall: mean {sum(recall) / len(recall):.4f}, lowest {min(recall):.4f}, "
                  f"below 0.98: {sum(1 for x in recall if x < 0.98)}")
        print(f"  {'page kind':12} {'pages':>6} {'errors':>7} {'/100p':>6} {'warnings':>9} {'/100p':>6}")
        for k, n in pages_of_kind.most_common():
            e, w = sev[k]["error"], sev[k]["warning"]
            print(f"  {k:12} {n:6} {e:7} {100 * e / n:6.1f} {w:9} {100 * w / n:6.1f}")
        for s in ("error", "warning"):
            print(f"  {s}s by check: " + ", ".join(f"{k} {v}" for k, v in kinds[s].most_common(6)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
