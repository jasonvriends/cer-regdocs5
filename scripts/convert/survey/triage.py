"""Where to look after a batch: what failed, what is new, what is concentrated or out of line.

    scripts/convert/.venv/bin/python scripts/convert/survey/triage.py \\
        --sample survey/sample-X.csv --convert convert-<sig> --pages pages-<sig> [--known convert-<sig> ...]

Writes <runs>/triage/<convert run>-<time>/report.md with page images of the worst cases, drawn
from each document's review.pdf so the issues are marked on them. It finds symptoms, not causes:
it says which documents and pages to read first.

  failed       a document a stage failed on or finished partly, with its reason
  new kinds    an issue kind no --known run has
  concentrated one document holding a large share of an issue kind
  out of line  a document with many more errors and warnings per page than its page kinds have
               across the batch
  export       a document whose Markdown lacks more than 2% of its evidence words
  disputed     pages where the vision model and the layout model disagree on the kind
"""
import argparse
import collections
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DEFAULT_OUTPUT, DEFAULT_RUNS, read_json_gz  # noqa: E402

SERIOUS = ("error", "warning")


def stage_record(out: Path, doc: str, run: str, stage: str):
    f = out / doc / "convert" / run / f"{doc}.{stage}.json.gz"
    return read_json_gz(f) if f.exists() else None


def issues_of(out: Path, doc: str, run: str) -> list[dict]:
    f = out / doc / "convert" / run / f"{doc}.issues.json"
    return json.loads(f.read_text()) if f.exists() else []


def sheet(out: Path, doc: str, run: str, pages: list[int], path: Path, title: str) -> bool:
    """Up to six pages of the document's review PDF (issues drawn on them) side by side."""
    import pymupdf
    src = out / doc / "convert" / run / f"{doc}.review.pdf"
    if not src.exists():
        return False
    pdf = pymupdf.open(src)
    pages = [p for p in pages if 1 <= p <= len(pdf)][:6]
    if not pages:
        return False
    w, h = 300, 400
    doc_out = pymupdf.open()
    pg = doc_out.new_page(width=w * len(pages), height=h + 20)
    pg.insert_text((5, 13), title[:160], fontsize=9)
    for k, p in enumerate(pages):
        pg.show_pdf_page(pymupdf.Rect(k * w + 4, 20, (k + 1) * w - 4, h + 16), pdf, p - 1)
        pg.insert_text((k * w + 6, h + 16), f"p{p}", fontsize=8)
    pg.get_pixmap(dpi=90).save(path)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", required=True, help="CSV with a doc_id column")
    ap.add_argument("--convert", required=True, help="convert run folder name")
    ap.add_argument("--pages", help="pages run folder name (kinds and vision-model disputes)")
    ap.add_argument("--tier0", help="tier 0 run folder name (failures)")
    ap.add_argument("--known", nargs="*", default=[], help="earlier convert runs whose issue kinds are known")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--top", type=int, default=8, help="cases shown per finding")
    args = ap.parse_args(argv)
    out, runs = Path(args.output), Path(args.runs)
    ids = [row["doc_id"] for row in csv.DictReader(open(args.sample))]
    rep_dir = runs / "triage" / f"{args.convert}-{time.strftime('%Y%m%dT%H%M%S')}"
    rep_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Triage: {args.convert}", "", f"{len(ids)} documents from `{args.sample}`; pages run "
             f"`{args.pages}`; known issue kinds from {', '.join(f'`{k}`' for k in args.known) or 'none'}.", ""]

    def show(doc, pages, title, name):
        img = rep_dir / f"{name}.png"
        if sheet(out, doc, args.convert, pages, img, title):
            lines.append(f"![{title}]({img.name})")
            lines.append("")

    # ---- gather
    failed, kinds_of, recs = [], {}, {}
    by_kind = collections.defaultdict(collections.Counter)        # issue kind -> doc -> count
    pages_of = collections.defaultdict(list)                      # (doc, issue kind) -> pages
    doc_rate, kind_pages = {}, collections.Counter()
    disputed = []
    for doc in ids:
        for stage, run in (("tier0", args.tier0), ("pages", args.pages), ("convert", args.convert)):
            if not run:
                continue
            rec = stage_record(out, doc, run, stage)
            if rec is None:
                failed.append((doc, stage, "no record"))
            elif rec.get("status") != "ok":
                err = (rec.get("error") or {}).get("error") or ""
                failed.append((doc, stage, f"{rec.get('status')}: {', '.join(rec.get('reasons') or [])} {err}"[:200]))
            if stage == "convert":
                recs[doc] = rec
            if stage == "pages" and rec:
                kinds_of[doc] = {p["page"]: p["kind"].split(":")[0] for p in rec.get("pages") or []}
                disputed += [(doc, p["page"], p["layout_kind"], (p.get("vlm") or {}).get("kind"))
                             for p in rec.get("pages") or [] if p.get("kind_disputed")]
        serious = [x for x in issues_of(out, doc, args.convert) if x["severity"] in SERIOUS]
        for x in serious:
            by_kind[x["kind"]][doc] += 1
            if x.get("page"):
                pages_of[(doc, x["kind"])].append(x["page"])
        n = (recs.get(doc) or {}).get("summary", {}).get("pages") or 0
        if n:
            doc_rate[doc] = len(serious) / n
        for k in (kinds_of.get(doc) or {}).values():
            kind_pages[k] += 1

    # ---- failed
    lines += ["## Failed or partly converted", ""]
    lines += [f"- `{d}` {s}: {why}" for d, s, why in failed] or ["None."]
    lines.append("")

    # ---- new issue kinds
    known = set()
    for run in args.known:
        for doc_dir in out.glob(f"*/convert/{run}"):
            f = doc_dir / f"{doc_dir.parent.parent.name}.issues.json"
            if f.exists():
                known |= {x["kind"] for x in json.loads(f.read_text())}
    new = [k for k in by_kind if args.known and k not in known]
    lines += ["## Issue kinds not seen in the known runs", ""]
    if not args.known:
        lines.append("No known runs given.")
    for k in new or []:
        doc, n = by_kind[k].most_common(1)[0]
        lines.append(f"- **{k}**: {sum(by_kind[k].values())} in {len(by_kind[k])} document(s); most in `{doc}` ({n})")
        show(doc, sorted(set(pages_of[(doc, k)]))[:6], f"{k} in {doc}", f"new_{k}_{doc}")
    if args.known and not new:
        lines.append("None.")
    lines.append("")

    # ---- concentrated
    lines += ["## Concentrated: one document holds most of an issue kind", ""]
    conc = []
    for k, docs in by_kind.items():
        tot = sum(docs.values())
        doc, n = docs.most_common(1)[0]
        if tot >= 20 and n / tot >= 0.3:
            conc.append((n, k, doc, tot))
    for n, k, doc, tot in sorted(conc, reverse=True)[:args.top]:
        lines.append(f"- **{k}**: `{doc}` has {n} of {tot} ({100 * n / tot:.0f}%)")
        pages = collections.Counter(pages_of[(doc, k)])
        show(doc, [p for p, _ in pages.most_common(6)], f"{k}: {doc}", f"conc_{k}_{doc}")
    if not conc:
        lines.append("None.")
    lines.append("")

    # ---- out of line
    lines += ["## Out of line: many more issues per page than the batch", ""]
    rates = sorted(doc_rate.values())
    med = statistics.median(rates) if rates else 0
    p90 = rates[int(0.9 * (len(rates) - 1))] if rates else 0
    lines.append(f"Errors and warnings per page: median {med:.2f}, 90th percentile {p90:.2f}.")
    lines.append("")
    worst = sorted(((rate, doc) for doc, rate in doc_rate.items() if rate > max(1.0, 3 * p90)), reverse=True)
    for rate, doc in worst[:args.top]:
        top = collections.Counter({k: by_kind[k][doc] for k in by_kind if by_kind[k][doc]}).most_common(3)
        lines.append(f"- `{doc}`: {rate:.1f} per page; " + ", ".join(f"{k} {n}" for k, n in top))
        pages = collections.Counter(p for (d, _), ps in pages_of.items() if d == doc for p in ps)
        show(doc, [p for p, _ in pages.most_common(6)], f"{doc}: {rate:.1f} issues/page", f"line_{doc}")
    if not worst:
        lines.append("None.")
    lines.append("")

    # ---- export
    lines += ["## Export: Markdown missing evidence words", ""]
    low = sorted((r["summary"].get("export_word_recall"), d) for d, r in recs.items()
                 if r and r.get("summary", {}).get("export_word_recall") is not None
                 and r["summary"]["export_word_recall"] < 0.98)
    lines += [f"- `{d}`: {100 * rc:.1f}% of evidence words" for rc, d in low] or ["None."]
    lines.append("")

    # ---- disputed
    lines += ["## Page kind disputed by the vision model", ""]
    if disputed:
        c = collections.Counter((a, b) for _, _, a, b in disputed)
        lines.append(f"{len(disputed)} page(s): " + ", ".join(f"layout {a} / vision {b} {n}" for (a, b), n in c.most_common(8)))
    else:
        lines.append("None.")
    lines.append("")

    # ---- page kinds and issues, for scale
    lines += ["## Scale", "", "| page kind | pages |", "|---|---:|"]
    lines += [f"| {k} | {n} |" for k, n in kind_pages.most_common()]
    lines += ["", "| issue kind | errors and warnings | documents |", "|---|---:|---:|"]
    lines += [f"| {k} | {sum(d.values())} | {len(d)} |" for k, d in sorted(by_kind.items(), key=lambda kv: -sum(kv[1].values()))]
    (rep_dir / "report.md").write_text("\n".join(lines) + "\n")
    print(rep_dir / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
