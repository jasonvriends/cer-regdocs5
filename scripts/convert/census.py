#!/usr/bin/env python3
"""How every filing was captured, from the catalog: the census behind docs/filing-guidance.md.

    ./scripts/convert/convert.sh catalog build          # first: every ledger into output/catalog.duckdb
    ~/.venvs/regdocs-convert/bin/python scripts/convert/census.py [--out FILE]

The 5,000-document survey (check.py, 3 pages a document) estimated these rates; the light pass read
every page of every document, so here they are counted. A document is "mostly not digital" when its
most common page class is not digital, digital+images or blank (the survey's definition).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE.parents[1] / "output" / "catalog.duckdb"
NOT_DIGITAL = "main_capture NOT IN ('digital', 'digital+images', 'blank')"
CLASSES = ["digital", "digital+images", "scan_no_layer", "scanner_ocr", "stub", "suspect", "text_as_curves",
           "blank", "other"]


def main() -> int:
    import duckdb
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="also write the report to this file")
    ap.add_argument("--min-docs", type=int, default=200, help="smallest group shown in the by-group tables")
    args = ap.parse_args()
    con = duckdb.connect(str(DB), read_only=True)
    out = []

    def table(title, sql, note=None):
        res = con.execute(sql)
        names = [d[0] for d in res.description]
        rows = res.fetchall()
        out.append(f"## {title}\n")
        if note:
            out.append(note + "\n")
        out.append("| " + " | ".join(names) + " |")
        out.append("|" + "|".join("---" if i == 0 else "---:" for i in range(len(names))) + "|")
        for r in rows:
            out.append("| " + " | ".join("" if v is None else f"{v:,}" if isinstance(v, int) else str(v) for v in r) + " |")
        out.append("")

    n_docs, n_pages = con.execute("SELECT count(*), sum(pages) FROM documents").fetchone()
    nd = con.execute(f"SELECT count(*) FROM documents WHERE {NOT_DIGITAL}").fetchone()[0]
    built = con.execute("SELECT max(converted_at) FROM documents").fetchone()[0]
    out.append(f"# Census: how {n_docs:,} REGDOCS documents ({n_pages:,} pages) were captured\n")
    out.append(f"From `output/catalog.duckdb` (ledgers up to {built[:10]}). **{nd:,} documents "
               f"({100 * nd / n_docs:.1f}%) are mostly not digital.** Counts, not estimates: every page of "
               f"every document was read.\n")

    pct = lambda c: f"round(100.0 * count(*) FILTER (WHERE capture = '{c}') / count(*), 1) AS \"{c} %\""
    table("Pages by capture class",
          "SELECT capture, count(*) AS pages, round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS \"%\" "
          "FROM pages GROUP BY 1 ORDER BY 2 DESC")
    table("Documents by their main capture class",
          "SELECT main_capture, count(*) AS documents, round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS \"%\" "
          "FROM documents GROUP BY 1 ORDER BY 2 DESC")

    by = lambda key, where="TRUE", order="documents DESC": (
        f"WITH d AS (SELECT {key} AS grp, count(*) AS documents, "
        f"round(100.0 * count(*) FILTER (WHERE {NOT_DIGITAL}) / count(*), 1) AS \"mostly not digital %\" "
        f"FROM documents WHERE {where} GROUP BY 1), "
        f"p AS (SELECT {key} AS grp, sum(pages.pages_n) AS pages, "
        + ", ".join(f"round(100.0 * sum(pages.pages_n) FILTER (WHERE capture = '{c}') / sum(pages.pages_n), 1) AS \"{c} %\""
                    for c in ("scan_no_layer", "scanner_ocr", "stub", "suspect", "text_as_curves")) +
        f" FROM (SELECT doc_id, capture, count(*) AS pages_n FROM pages GROUP BY 1, 2) pages "
        f"JOIN documents USING (doc_id) WHERE {where} GROUP BY 1) "
        f"SELECT d.grp AS \"{key}\", documents, \"mostly not digital %\", pages, \"scan_no_layer %\", \"scanner_ocr %\", "
        f"\"stub %\", \"suspect %\", \"text_as_curves %\" FROM d JOIN p USING (grp) "
        f"WHERE documents >= {{min}} ORDER BY {order}")

    table("By year filed", by("year", order="grp").format(min=1),
          "Pages columns are shares of that year's pages.")
    table("By kind of tool that made the PDF", by("coalesce(producer_kind, '(none recorded)')").format(min=1))
    table(f"By producer family (at least {args.min_docs} documents)",
          by("coalesce(producer, '(none recorded)')").format(min=args.min_docs))
    table(f"By REGDOCS document type (at least {args.min_docs} documents)",
          by("coalesce(document_type, '(no facet)')").format(min=args.min_docs))

    table("Scans carrying their own OCR layer: does our OCR read the same words? By kind of tool",
          "SELECT coalesce(d.producer_kind, '(none recorded)') AS tool, count(*) AS pages, "
          "count(i.doc_id) AS \"under 90% alike\", round(100.0 * count(i.doc_id) / count(*), 1) AS \"%\" "
          "FROM pages p JOIN documents d USING (doc_id) "
          "LEFT JOIN (SELECT DISTINCT doc_id, page FROM issues WHERE kind = 'ocr_readings_disagree') i "
          "ON i.doc_id = p.doc_id AND i.page = p.page "
          "WHERE p.capture = 'scanner_ocr' GROUP BY 1 HAVING count(*) >= 500 ORDER BY 2 DESC",
          "A page is under 90% alike when our OCR finds fewer than 90% of the scanner layer's words at "
          "their places (`ocr_readings_disagree`). It says the two readings differ, not which is wrong; "
          "the voters decide line by line.")

    table("Other facts, whole corpus",
          "SELECT 'pages over 17.5 in (oversize)' AS fact, count(*) AS pages, count(DISTINCT doc_id) AS documents FROM pages WHERE size = 'oversize' "
          "UNION ALL SELECT 'pages with text-layer words under a cover-up redaction', count(*), count(DISTINCT doc_id) FROM pages WHERE redacted > 0 "
          "UNION ALL SELECT 'blank pages', count(*), count(DISTINCT doc_id) FROM pages WHERE capture = 'blank' "
          "UNION ALL SELECT 'pages with text in images the layer lacks', count(*), count(DISTINCT doc_id) FROM issues WHERE kind = 'ink_not_in_text_layer' "
          "UNION ALL SELECT 'pages where OCR finds under 60% of the text layer', count(*), count(DISTINCT doc_id) FROM issues WHERE kind = 'pdf_ocr_disagree' "
          "UNION ALL SELECT 'pages with no words at all', count(*), count(DISTINCT doc_id) FROM pages WHERE coalesce(words_evidence, 0) = 0")

    table("What the catalog cannot yet say",
          "SELECT 'documents with no REGDOCS document type' AS gap, count(*) AS documents FROM documents WHERE document_type IS NULL "
          "UNION ALL SELECT 'documents with no producer recorded in the PDF', count(*) FROM documents WHERE producer IS NULL "
          "UNION ALL SELECT 'ledgers assembled before the page boundaries step', count(*) FROM documents WHERE NOT has_boundaries "
          "UNION ALL SELECT 'ledgers naming the code that wrote them', count(*) FROM documents WHERE code_sha256 IS NOT NULL",
          "Capture classes come from reading, which did not change during the pass; the assembly-dependent "
          "counts (headings, page boundaries) and issue kinds added on 2026-10-17 (page_redacted, "
          "pdf_file_problems) cover only ledgers written since.")

    text = "\n".join(out) + "\n"
    print(text)
    if args.out:
        Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
