#!/usr/bin/env python3
"""A browsable catalog of everything measured: DuckDB, rebuilt from the evidence, never edited.

    ./scripts/convert/convert.sh catalog build [--sample FILE ...]   # default: every converted document
    ./scripts/convert/convert.sh catalog ui                          # browse it in DuckDB's web UI
    ./scripts/convert/convert.sh catalog sql "SELECT ..."            # one query

The sources of truth stay where they are - each document's ledger (pages.json) and Parquet files,
the scout records, the survey JSONL, docs/findings.jsonl, the bench results. This file
(output/catalog.duckdb) is derived from them and can be deleted and rebuilt at any time.

Tables:
  documents   one row per converted document: REGDOCS facets (read live from the scout record),
              what made the PDF, mode (full / light), words, checks, main capture class
  pages       one row per page: text layer verdict, capture class, size, orientation, words by
              reader, regions, tables, pictures, checkboxes, todo tags
  issues      every issue, with page and evidence
  survey      the 5,000-document capture survey, one row per page checked
  findings    every finding (docs/findings.jsonl): claim, evidence, status, what it corrected
  samples     which sample each test document belongs to
  benches     each bench result (voters, OCR models, drawings) as JSON
Views over the Parquet files (globbed when queried - slow across the whole corpus):
  words, regions, table_readings, votes
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402
from capture import page_class  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output"
DB = OUT / "catalog.duckdb"


def cer(doc_id: str) -> dict:
    f = OUT / doc_id / f"{doc_id}.cer.meta.json.gz"
    return json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {}


def doc_ids(samples) -> list[str]:
    if samples:
        return list(dict.fromkeys(l.strip() for s in samples for l in Path(s).read_text().split() if l.strip()))
    return [e.name for e in os.scandir(OUT) if e.is_dir() and e.name.isdigit()
            and (Path(e.path) / "convert" / f"{e.name}.pages.json").exists()]


def read_one(d: str):
    """One document's rows -> (document, pages, issues), or None when its ledger cannot be read."""
    import collections
    pages, issues = [], []
    try:
        L = json.loads((OUT / d / "convert" / f"{d}.pages.json").read_text())
    except (OSError, ValueError):
        return None
    r, c, s = cer(d), L["context"], L["summary"]
    tool = c["pdf"].get("producer") or c["pdf"].get("creator") or {}
    facets = r.get("facets") or {}
    classes = collections.Counter()
    for p in L["pages"]:
        cls = page_class(p)
        classes[cls] += 1
        f, w, lay = p.get("facts") or {}, p.get("words") or {}, p.get("layout") or {}
        pages.append({"doc_id": d, "page": p["page"], "text_layer": p.get("text_layer"), "source": p.get("source"),
                      "capture": cls, "size": f.get("size"), "orientation": f.get("orientation"),
                      "width_pt": f.get("width_pt"), "height_pt": f.get("height_pt"), "rotation": f.get("rotation"),
                      "columns": f.get("columns"), "kind": f.get("kind"), "image_cover": f.get("largest_image_cover"),
                      "paths": f.get("paths"), "words_pdf": w.get("pdf"), "words_ocr": w.get("ocr"),
                      "words_evidence": w.get("evidence"), "ocr_only_words": w.get("ocr_extra"),
                      "unclaimed": w.get("unclaimed"), "hidden": w.get("hidden"), "redacted": w.get("redacted"),
                      "voted_lines": w.get("voted_lines"),
                      "pdf_found_by_ocr": asm.ocr_agreement(p, L),
                      "regions": lay.get("kept"), "disputed_regions": lay.get("disputed"),
                      "tables": len(p.get("tables") or []), "pictures": len(p.get("pictures") or []),
                      "checkboxes": len(p.get("checkboxes") or []), "seconds": p.get("seconds"),
                      "todo": list(p.get("todo") or [])})
        for i in p.get("issues") or []:
            issues.append({"doc_id": d, "page": p["page"], "kind": i["kind"], "severity": i["severity"],
                           "evidence": i.get("evidence")})
    for i in L.get("issues") or []:
        issues.append({"doc_id": d, "page": None, "kind": i["kind"], "severity": i["severity"],
                       "evidence": i.get("evidence")})
    dc = s.get("docling") or {}
    doc = ({"doc_id": d, "mode": L.get("mode", "full"), "version": L.get("version"),
                 "converted_at": L.get("converted_at"), "pages": s.get("pages"),
                 "words_evidence": s.get("words_evidence"), "words_pdf": s.get("words_pdf"),
                 "words_ocr": s.get("words_ocr"), "pages_text_layer": (s.get("pages_by_source") or {}).get("pdf", 0),
                 "pages_ocr": (s.get("pages_by_source") or {}).get("ocr", 0), "tables": s.get("tables"),
                 "pictures": s.get("pictures"), "export_recall": s.get("export_recall"),
                 "evidence_named_once": dc.get("named_once"), "text_holds_words": dc.get("text_holds_words"),
                 "errors": (s.get("issues") or {}).get("error", 0), "warnings": (s.get("issues") or {}).get("warning", 0),
                 "main_capture": classes.most_common(1)[0][0] if classes else None,
                 "title": r.get("title"), "date": r.get("date"), "year": (r.get("date") or "")[:4] or None,
                 "document_type": ",".join(facets.get("Document Type") or []) or None,
                 "commodity": ",".join(facets.get("Commodity") or []) or None,
                 "company": (r.get("company") or {}).get("name"), "filing": (r.get("filing") or {}).get("number"),
                 "url": r.get("url"), "producer": tool.get("family"), "producer_kind": tool.get("kind"),
                 "producer_raw": tool.get("raw"), "sha256": c["pdf"].get("sha256"),
                 # which code wrote the ledger: older ones name none, and before the page
                 # boundaries step they have no boundaries summary (review 2026-10-17)
                 "code_sha256": (L.get("code") or {}).get("sha256"), "code_commit": (L.get("code") or {}).get("commit"),
                 "has_boundaries": "boundaries" in s,
                 "mupdf_messages": sum((s.get("mupdf_messages") or {}).values()) if "mupdf_messages" in s else None})
    return doc, pages, issues


def build(args) -> int:
    import collections
    import duckdb
    import pyarrow as pa
    ids = doc_ids(args.sample)
    docs, pages, issues = [], [], []
    from concurrent.futures import ProcessPoolExecutor
    # ledgers read in parallel: one at a time from the Windows drive is latency, not work
    # (~70,000 took over an hour), and 12 readers overlap it
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for n, res in enumerate(ex.map(read_one, ids, chunksize=200), 1):
            if res:
                docs.append(res[0])
                pages.extend(res[1])
                issues.extend(res[2])
            if n % 5000 == 0:
                print(f"  {n}/{len(ids)} ledgers read", flush=True)

    tmp = DB.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    for name, rows in (("documents", docs), ("pages", pages), ("issues", issues)):
        if rows:
            con.register("t", pa.Table.from_pylist(rows))
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM t")
            con.unregister("t")
    survey = HERE / "runs" / "v2" / "capture-fast" / "survey-5000.jsonl"
    if survey.exists():
        rows = []
        for line in survey.read_text().splitlines():
            r = json.loads(line)
            for p in r.get("pages_checked") or []:
                rows.append({"doc_id": r["doc_id"], "producer": r.get("producer"), "tool_kind": r.get("tool_kind"),
                             "doc_pages": r.get("pages"), "tagged": r.get("tagged"), "pdfa": r.get("pdfa"),
                             "outline": r.get("outline"), "form_fields": r.get("form_fields"),
                             **{k: v for k, v in p.items() if not isinstance(v, (list, dict))}})
        con.register("t", pa.Table.from_pylist(rows))
        con.execute("CREATE TABLE survey AS SELECT * FROM t")
        con.unregister("t")
    findings = ROOT / "docs" / "findings.jsonl"
    if findings.exists():
        con.execute(f"CREATE TABLE findings AS SELECT * FROM read_json_auto('{findings}', union_by_name=true)")
    sample_rows = [{"sample": f.stem, "doc_id": l.strip()} for f in sorted((HERE / "samples").glob("*.txt"))
                   for l in f.read_text().split() if l.strip()]
    sample_rows += [{"sample": "survey-" + f.stem, "doc_id": l.split(",")[0]} for f in sorted((HERE / "survey").glob("*.csv"))
                    for l in f.read_text().splitlines()[1:] if l.strip()]
    con.register("t", pa.Table.from_pylist(sample_rows))
    con.execute("CREATE TABLE samples AS SELECT * FROM t")
    con.unregister("t")
    benches = [{"name": f.name.split("-20")[0], "file": str(f.relative_to(ROOT)), "result": f.read_text()}
               for f in sorted((HERE / "runs").glob("*/*-bench-*.json"))]
    if benches:
        con.register("t", pa.Table.from_pylist(benches))
        con.execute("CREATE TABLE benches AS SELECT * FROM t")
        con.unregister("t")
    # views over these documents' Parquet files (for a sample build); across the whole corpus a
    # view would open every file when created, so query the files directly there (docs/catalog.md)
    if args.sample:
        import pyarrow.parquet as pq
        for view, pattern in (("words", "words"), ("regions", "regions"), ("table_readings", "tables"), ("votes", "votes")):
            files = []
            for d in ids:
                f = OUT / d / "convert" / f"{d}.{pattern}.parquet"
                try:
                    if f.exists() and pq.read_schema(f).names:      # skip unreadable (no-column) files
                        files.append(str(f))
                except Exception:
                    pass
            if files:
                con.execute(f"CREATE VIEW {view} AS SELECT regexp_extract(filename, '([0-9]+)[.]{pattern}', 1) AS doc_id, * "
                            f"FROM read_parquet({files!r}, filename=true, union_by_name=true)")
    counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("documents", "pages", "issues", "findings", "samples") if con.execute(
                  f"SELECT count(*) FROM information_schema.tables WHERE table_name='{t}'").fetchone()[0]}
    con.close()
    os.replace(tmp, DB)
    print(f"{DB}: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    return 0


def main() -> int:
    import duckdb
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sample", action="append", help="only these documents (default: every converted one)")
    b.add_argument("--workers", type=int, default=12, help="ledgers read at once")
    sub.add_parser("ui")
    q = sub.add_parser("sql")
    q.add_argument("query")
    args = ap.parse_args()
    if args.cmd == "build":
        return build(args)
    if not DB.exists():
        print(f"no catalog yet: run `catalog build` first ({DB})")
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    if args.cmd == "sql":
        print(con.execute(args.query).fetchdf().to_string(max_rows=200) if hasattr(con, "fetchdf") else
              con.sql(args.query))
        return 0
    con.execute("CALL start_ui()")
    print("DuckDB UI started (http://localhost:4213); Ctrl-C to stop")
    try:
        import time
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
