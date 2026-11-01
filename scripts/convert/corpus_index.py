#!/usr/bin/env python3
"""An index of how big the REGDOCS record is: docs/corpus/, rebuilt from the catalog.

    ./scripts/convert/convert.sh catalog build                       # first, if the catalog is stale
    ~/.venvs/regdocs-convert/bin/python scripts/convert/corpus_index.py

Writes
  docs/corpus/README.md                       the record at a glance and the 25 largest documents
  docs/corpus/documents-over-500-pages.md     every document over 500 pages, as a table
  docs/corpus/documents-over-100-pages.csv    every document over 100 pages, for a spreadsheet
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DB = ROOT / "output" / "catalog.duckdb"
INTEGRITY = ROOT / "output" / "integrity.parquet"
OUT = ROOT / "docs" / "corpus"
NOT_DIGITAL = ("scan_no_layer", "scanner_ocr", "stub", "suspect", "text_as_curves", "other")


def main() -> int:
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    con.execute("SET enable_progress_bar=false")
    con.execute(f"CREATE TEMP VIEW integ AS SELECT doc_id, bytes FROM read_parquet('{INTEGRITY}')"
                if INTEGRITY.exists() else "CREATE TEMP VIEW integ AS SELECT NULL::VARCHAR doc_id, NULL::BIGINT bytes")
    con.execute("""CREATE TEMP VIEW docs AS
        SELECT d.*, i.bytes,
               (SELECT string_agg(o.doc_id, ' ' ORDER BY o.doc_id) FROM documents o
                 WHERE o.sha256 = d.sha256 AND o.doc_id <> d.doc_id) AS same_file_as
        FROM documents d LEFT JOIN integ i USING (doc_id)""")
    OUT.mkdir(parents=True, exist_ok=True)

    n_docs, n_pages, n_words = con.execute("SELECT count(*), sum(pages), sum(words_evidence) FROM documents").fetchone()
    bands = con.execute("""SELECT CASE WHEN pages <= 20 THEN '1-20' WHEN pages <= 100 THEN '21-100'
                                WHEN pages <= 500 THEN '101-500' WHEN pages <= 1000 THEN '501-1,000'
                                WHEN pages <= 5000 THEN '1,001-5,000' ELSE 'over 5,000' END AS band,
                                min(pages) AS lo, count(*) AS docs, sum(pages) AS pages
                           FROM documents GROUP BY 1 ORDER BY lo""").fetchall()
    top = con.execute("""SELECT doc_id, pages, round(bytes / 1e6, 1), date, document_type, company, title, main_capture,
                                same_file_as, url FROM docs ORDER BY pages DESC LIMIT 25""").fetchall()

    def mb(v):
        return "" if v is None else f"{v:,.1f}"

    def made(c):
        return "not digital" if c in NOT_DIGITAL else ("digital" if c else "")

    lines = ["# The REGDOCS record: how big it is", "",
             f"Every PDF in REGDOCS, read page by page: **{n_docs:,} documents, {n_pages:,} pages, "
             f"{n_words / 1e9:.2f} billion words**. Built by `scripts/convert/corpus_index.py` from the catalog; "
             "rebuild after a new catalog.", "",
             "| document size | documents | pages | share of pages |", "|---|---:|---:|---:|"]
    for band, _, d, p in bands:
        lines.append(f"| {band} pages | {d:,} | {p:,} | {100 * p / n_pages:.1f}% |")
    lines += ["", "- [documents-over-500-pages.md](documents-over-500-pages.md): every document over 500 pages",
              "- [documents-over-100-pages.csv](documents-over-100-pages.csv): every document over 100 pages, "
              "for a spreadsheet (pages, size, date, type, company, title, how it was made, duplicates, link)", "",
              "## The 25 largest documents", "",
              "| # | doc | pages | MB | filed | type | company | title | made | same file as |",
              "|---:|---|---:|---:|---|---|---|---|---|---|"]
    for i, (d, p, b, date, typ, comp, title, cap, same, url) in enumerate(top, 1):
        lines.append(f"| {i} | [{d}]({url}) | {p:,} | {mb(b)} | {date or ''} | {typ or ''} | {comp or ''} | "
                     f"{(title or '').replace('|', '/')} | {made(cap)} | {same or ''} |")
    lines += ["", "\"Made\" is the document's main capture class from the light pass: *not digital* means its pages are "
              "mostly images, scanner OCR, stub layers, undecodable text or text drawn as outlines. \"Same file as\" "
              "lists other REGDOCS IDs holding the identical file (same SHA-256).", ""]
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    rows = con.execute("""SELECT doc_id, pages, round(bytes / 1e6, 1), date, document_type, company, title, main_capture,
                                 same_file_as, url FROM docs WHERE pages > 500 ORDER BY pages DESC""").fetchall()
    md = [f"# Documents over 500 pages ({len(rows):,})", "",
          "Largest first. Rebuilt by `scripts/convert/corpus_index.py`.", "",
          "| doc | pages | MB | filed | type | company | title | made | same file as |",
          "|---|---:|---:|---|---|---|---|---|---|"]
    for d, p, b, date, typ, comp, title, cap, same, url in rows:
        md.append(f"| [{d}]({url}) | {p:,} | {mb(b)} | {date or ''} | {typ or ''} | {comp or ''} | "
                  f"{(title or '').replace('|', '/')} | {made(cap)} | {same or ''} |")
    (OUT / "documents-over-500-pages.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    rows = con.execute("""SELECT doc_id, pages, round(bytes / 1e6, 1), date, year, document_type, company, filing, title,
                                 main_capture, producer, same_file_as, url
                          FROM docs WHERE pages > 100 ORDER BY pages DESC""").fetchall()
    with open(OUT / "documents-over-100-pages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["doc_id", "pages", "mb", "filed", "year", "document_type", "company", "filing", "title",
                    "made", "capture_class", "producer", "same_file_as", "url"])
        for d, p, b, date, year, typ, comp, filing, title, cap, prod, same, url in rows:
            w.writerow([d, p, b, date, year, typ, comp, filing, title, made(cap), cap, prod, same, url])
    print(f"wrote {OUT}: {len(rows):,} documents over 100 pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
