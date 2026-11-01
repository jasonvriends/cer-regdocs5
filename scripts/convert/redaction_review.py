#!/usr/bin/env python3
"""A review spreadsheet of documents with words still in the file under black or dark boxes.

    ~/.venvs/regdocs-convert/bin/pip install -q openpyxl          # once
    ~/.venvs/regdocs-convert/bin/python scripts/convert/redaction_review.py

Reads output/catalog.duckdb (pages.redacted > 0) and each affected page of source/<id>.pdf for
privacy words, and writes output/redaction-review-<date>.xlsx: Read me, Documents (by review
priority, with links and reviewer columns), Pages. IDs, counts and page numbers only - never the
hidden text. output/ is not in git; keep the sheet off GitHub.
"""
from __future__ import annotations

import collections
import csv
import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DB = ROOT / "output" / "catalog.duckdb"
REVIEWED = HERE / "runs" / "v2" / "reports" / "redaction-review-2026-10-18.csv"
KW = re.compile(r"redact|privacy|personal information|confidential|withheld|landowner|home address|phone|e-?mail|telephone", re.I)
STRONG = ("redact", "privacy", "personal information", "withheld", "landowner", "home address", "confidential")


def gather():
    import duckdb
    import pymupdf
    c = duckdb.connect(str(DB), read_only=True)
    c.execute("SET enable_progress_bar=false")
    pages = c.execute("SELECT doc_id, page, redacted FROM pages WHERE redacted > 0 ORDER BY doc_id, page").fetchall()
    docs = {r[0]: r for r in c.execute(
        """SELECT doc_id, title, company, date, document_type, pages, url, sha256 FROM documents
           WHERE doc_id IN (SELECT DISTINCT doc_id FROM pages WHERE redacted > 0)""").fetchall()}
    notes = {r["doc_id"]: r["reviewed"] for r in csv.DictReader(open(REVIEWED))} if REVIEWED.exists() else {}
    pymupdf.TOOLS.mupdf_display_errors(False)
    out, open_doc = [], {}
    for d, p, red in pages:
        try:
            if d not in open_doc:
                open_doc.clear()
                open_doc[d] = pymupdf.open(ROOT / "source" / f"{d}.pdf")
            hint = ", ".join(sorted({m.group(0).lower() for m in KW.finditer(open_doc[d][p - 1].get_text())}))
        except Exception:
            hint = "(could not open)"
        out.append({"doc": d, "page": p, "hidden": red, "hint": hint})
    return out, docs, notes


def priority(ps, note):
    hidden = sum(p["hidden"] for p in ps)
    per = hidden / len(ps)
    hints = sorted({h for p in ps for h in p["hint"].split(", ") if h and not h.startswith("(")})
    also = f"; page mentions: {', '.join(hints)}" if hints else ""
    if note.startswith("real"):
        return "1 Known leak", "already reviewed: " + note
    if note.startswith(("false", "probably")):
        return "4 Low", "already reviewed: " + note
    if any(h in STRONG or h.startswith("redact") for h in hints):
        return "2 High", "page mentions: " + ", ".join(hints)
    if per <= 150:
        return "3 Medium", f"few hidden words per page ({per:.0f}): looks like targeted boxes" + also
    return "4 Low", f"many hidden words per page ({per:.0f}): likely shading or design" + also


def main() -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    pages, docs, notes = gather()
    bydoc = collections.defaultdict(list)
    for p in pages:
        bydoc[p["doc"]].append(p)
    by_sha = collections.defaultdict(list)
    for d, v in docs.items():
        by_sha[v[7]].append(d)
    rows = []
    for d, ps in bydoc.items():
        v = docs[d]
        pr, why = priority(ps, notes.get(d, ""))
        rows.append({"pr": pr, "why": why, "doc": d, "title": v[1], "company": v[2], "filed": v[3], "type": v[4],
                     "pages": v[5], "aff": len(ps), "plist": ", ".join(str(p["page"]) for p in ps),
                     "hidden": sum(p["hidden"] for p in ps), "same": " ".join(x for x in by_sha[v[7]] if x != d),
                     "url": v[6], "first": ps[0]["page"]})
    rows.sort(key=lambda r: (r["pr"], -r["hidden"]))

    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0E4D57")
    link_font = Font(color="0563C1", underline="single")
    wb = Workbook()
    ws = wb.active
    ws.title = "Read me"
    for line in [
        "Redaction review: documents with words still in the file under black or dark boxes", "",
        "Each listed document has pages where words sit in the PDF's text layer underneath a dark filled box. A person "
        "sees a blacked-out area; a computer, a search engine, or anyone who copies the text reads the words underneath.",
        "Not every case is a leak: some are shaded table rows, dark legend bars or design. A person has to look.", "",
        f"Source: the October 2026 light pass over all REGDOCS PDFs (output/catalog.duckdb). "
        f"{len(rows)} documents, {len(pages):,} pages.",
        "This sheet lists document IDs, counts and page numbers only. It does not contain the hidden text.", "",
        "Priority (a guide for order of review, not a verdict):",
        "  1 Known leak - already reviewed and confirmed: words recoverable under the boxes",
        "  2 High - the page itself mentions redaction, privacy, personal information, landowners, addresses or similar",
        "  3 Medium - few hidden words per page (150 or fewer): looks like boxes over names, numbers or addresses",
        "  4 Low - many hidden words per page, or reviewed as shading / false alarm", "",
        "How to check one: click 'open' (the PDF from REGDOCS at the first affected page). Select text across the black "
        "box, or press Ctrl+A and Ctrl+C and paste into Notepad: if words appear, the redaction leaks.",
        "Record what you find in the yellow columns (Verdict, Action, Reviewer, Date, Notes).",
        "'Same file as' lists other REGDOCS IDs holding the identical file: a verdict applies to them too.",
        "The 'Pages' sheet has one row per affected page with its own link."]:
        ws.append([line])
    ws.column_dimensions["A"].width = 140
    ws["A1"].font = Font(bold=True, size=14)
    for r in range(1, ws.max_row + 1):
        ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.create_sheet("Documents")
    ws.append(["Priority", "Why", "Doc ID", "Open at first page", "Title", "Company", "Filed", "Type", "Pages in doc",
               "Pages affected", "Affected page numbers", "Hidden words", "Same file as",
               "Verdict", "Action", "Reviewer", "Date", "Notes"])
    fills = {"1": "F4B6B6", "2": "F9D7A8", "3": "FFF2B3", "4": "E2EFDA"}
    yellow = PatternFill("solid", fgColor="FFF9DB")
    for r in rows:
        ws.append([r["pr"], r["why"], int(r["doc"]), "open", r["title"], r["company"], r["filed"], r["type"],
                   r["pages"], r["aff"], r["plist"], r["hidden"], r["same"], "", "", "", "", ""])
        i = ws.max_row
        ws.cell(i, 4).hyperlink = f"{r['url']}#page={r['first']}"
        ws.cell(i, 4).font = link_font
        ws.cell(i, 1).fill = PatternFill("solid", fgColor=fills[r["pr"][0]])
        for j in range(14, 19):
            ws.cell(i, j).fill = yellow
    for j, w in enumerate([13, 60, 10, 12, 60, 30, 11, 26, 9, 9, 30, 10, 14, 16, 22, 14, 11, 40], 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    for c in ws[1]:
        c.font, c.fill = head_font, head_fill
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions
    for col, choices in (("N", "Leak - personal info,Leak - other,Intended shading,False alarm,Unsure"),
                         ("O", "Ask filer for corrected version,Remove from public view,Re-redact,No action")):
        dv = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{ws.max_row}")

    ws2 = wb.create_sheet("Pages")
    ws2.append(["Priority", "Doc ID", "Page", "Open page", "Hidden words on page", "Page mentions"])
    pri = {r["doc"]: r["pr"] for r in rows}
    for p in sorted(pages, key=lambda p: (pri[p["doc"]], p["doc"], p["page"])):
        ws2.append([pri[p["doc"]], int(p["doc"]), p["page"], "open", p["hidden"], p["hint"]])
        i = ws2.max_row
        ws2.cell(i, 4).hyperlink = f"{docs[p['doc']][6]}#page={p['page']}"
        ws2.cell(i, 4).font = link_font
    for c in ws2[1]:
        c.font, c.fill = head_font, head_fill
    for j, w in enumerate([13, 10, 7, 10, 12, 60], 1):
        ws2.column_dimensions[get_column_letter(j)].width = w
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions
    wb.active = 1

    out = ROOT / "output" / f"redaction-review-{datetime.date.today()}.xlsx"
    wb.save(out)
    print(f"wrote {out}:", ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(r['pr'] for r in rows).items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
