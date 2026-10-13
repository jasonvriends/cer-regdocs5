# What a digital-first REGDOCS filing looks like

A filing that arrives as a faithful digital document needs no one to recover its text: every word
can be searched, quoted and checked; every table is data; every signature is verifiable. This is
what such a filing looks like, requirement by requirement. Each requirement exists because its
absence was **measured** to lose information in REGDOCS filings - the evidence is in
[filing-guidance.md](filing-guidance.md), rates from a random survey of 5,000 documents unless
noted. Requirements marked *proposed* follow from the evidence but their benefit was not measured.

The checker [check.py](../scripts/convert/check.py) tests a PDF against the requirements a single
file can show (`./scripts/convert/convert.sh check filing.pdf`).

---

## 1. The document is born digital

| # | requirement | why (measured) | check |
|---|---|---|---|
| D1 | **Export or save as PDF from the application that made it** (Word, Excel, the CAD or lab system), not print-to-PDF of a printout, not a scan, not re-distilled | office suites 1%, reporting tools 0% of documents not digital; scanner-made PDFs 98%, PDFs with no producer recorded 75% | text layer vs OCR |
| D2 | **Never turn pages into images** - no "flatten", no rasterizing publishing step | 81 of 5,000 documents were digital pages rasterized by one filer's system (2024); about 1 in 4 image-only pages is a digital page, not paper | page image with no text |
| D3 | **Text stays text** - no "print text as graphics", CAD text exported as text (TrueType, searchable-text export) | 0.4% of pages are text drawn as vector outlines (exhibit lists, CAD drawings: up to 205,000 paths a page, no characters) | no text, many paths |
| D4 | **Text decodes to the characters shown** - fonts embedded with Unicode maps; no re-distilling, no stripping fonts | 0.5% of pages overall, 19% of MicroStation pages, extract as nonsense while looking right | letter share, real words |
| D5 | **PDF/A-2u or PDF/A-3u** | 19 of 5,000 documents; "u" rules out D4 by construction; PDF/A-3 can carry the data files (section 4) | XMP `pdfaid` |
| D6 | **Paper that has to be filed is OCR'd by the filer and checked** (300 dpi or better) | scanner OCR layers miss over 40% of the page on a quarter of pages for some scanners; stub layers ("received" stamps) on 2% of pages look searchable and are not | text layer vs OCR |

## 2. Signatures are digital

| # | requirement | why (measured) | check |
|---|---|---|---|
| S1 | **Sign digitally** - an e-signature service or a digitally signed PDF | affidavits 79% not digital, letters 23%: the scans are the signed documents; e-signed documents in the samples were fully digital | - |
| S2 | **Sworn and commissioned documents too** *(proposed)* - a digital commissioning process | affidavits stayed scanned while letters moved to digital (samples, 2012-2026: 34% to 11% of signed documents) | - |
| S3 | **If a wet signature is unavoidable, scan the signature page only** and file it with the digital document | a scanned letter replaces the typed text with a picture of it | text layer vs OCR, per page |

## 3. Structure travels with the document

| # | requirement | why (measured) | check |
|---|---|---|---|
| T1 | **Tagged PDF** (headings, lists, tables, reading order; PDF/UA) *(proposed for extraction; required for accessibility)* | 47% of documents tagged; reading order on two-column pages is where untagged extraction disagrees most | `StructTreeRoot` |
| T2 | **Bookmarks on documents over 20 pages**, one per section and attachment *(proposed)* | half of long documents have none | outline |
| T3 | **Forms keep live fields**; checkboxes are fields, not drawings | 67 of 5,000 documents have live fields; in the samples 39 documents carried checkboxes printed flat, readable only from pixels | form fields |
| T4 | **Redact by removing** the text (a true redaction tool), never by drawing a box | one filing's blacked-out invoices: 13,159 words still readable under the boxes | text under dark fills |
| T5 | **Pages a readable size** - drawings as standard sheets or tiles *(proposed cap: 36 x 48 in)* | 2.3% of documents have sheets over 17.5 in; some over 90 in could not be processed at all | page boxes |

## 4. Data is filed as data

| # | requirement | why | check |
|---|---|---|---|
| A1 | **Tables of results as CSV (UTF-8) or Excel (.xlsx) beside the PDF**, with a data dictionary (column, meaning, unit) - lab and monitoring results, schedules, financial tables *(proposed)* | tables are the weakest part of every extraction: the best readers place ~97% of table words, and a misplaced `<0.01` changes a finding | manifest |
| A2 | **Spatial data as GeoPackage (.gpkg)**, the coordinate reference system stated (EPSG code); GeoJSON for small files *(proposed)* | OCR reads about two-thirds of a drawing's labels; labels on maps are the hardest text to recover | manifest |
| A3 | **Drawings with their CAD file** (DXF, or DWG) beside the PDF *(proposed)* | the PDF is for reading; the CAD file is the data, its text kept as text (D3) | manifest |
| A4 | **Barcodes and QR codes are welcome** - they carry exact data | courier waybills' addresses and tracking numbers decode exactly | - |

**Formats - what filers send, and what CER keeps.** Filers send what their tools already make;
intake converts to formats built for storage and analysis, keeping the original beside them.

| data | filers submit | CER stores (derived at intake) | not |
|---|---|---|---|
| tables | CSV (UTF-8) or Excel (.xlsx), plus a data dictionary | Parquet (typed, compressed, columnar; queried with DuckDB) | Parquet as a *submission* format: ideal to store, but few filers' tools produce it |
| spatial | GeoPackage (.gpkg), GeoJSON for small files; CRS as an EPSG code | GeoParquet | shapefiles (3-7 files per layer, 10-character field names, 2 GB limit); KML for data (drops attributes) |
| drawings | DXF or DWG, beside the PDF | as filed | drawings as images only |
| documents | PDF/A-2u or -3u (data files may be embedded in PDF/A-3) | as filed, plus the converter's evidence (words, structure, ledger) | |
| the filing | `manifest.json` (section 5) | the catalog (DuckDB) | |

## 5. The filing describes itself *(proposed)*

A small `manifest.json` beside the files, so intake knows what arrived without reading it:

```json
{
  "filing": "C41176",
  "submitted_by": "Irving Oil Limited",
  "documents": [
    {"file": "C41176-1 Order application.pdf", "document_type": "Application",
     "sha256": "...", "pages": 12, "language": "en", "pdfa": "2u", "signed": "digital"},
    {"file": "C41176-1 Table 3 results.csv", "document_type": "Data", "describes": "C41176-1 Order application.pdf",
     "sha256": "...", "rows": 214}
  ]
}
```

The hashes make the custody of each file checkable from the moment it is filed; the document types
REGDOCS assigns later are known at the start; a data file names the document it belongs to.

---

## What the checker says about real filings

A digital-first filing (an order filed in October 2026):

```
source/4738475.pdf: 1 pages (1 checked), made by Adobe PDF Library 25.1.182
  pages fine: 1 of 1
  tagged: yes; PDF/A: no; form fields: 0
```

A digital document rasterized on the way to filing (an information request response, 2024):

```
source/4461679.pdf: 21 pages (3 checked), made by iText Core 8.0.4
  pages fine: 0 of 3
  page 1: scan_no_layer - a page image with no text ... [layer 0 words, OCR 479]
  page 11: scan_no_layer - a page image with no text ... [layer 0 words, OCR 448]
  page 21: scan_no_layer - a page image with no text ... [layer 0 words, OCR 438]
  no bookmarks on a document over 20 pages
  tagged: no; PDF/A: no; form fields: 0
```

A sworn affidavit printed, signed and scanned (2015):

```
source/2796708.pdf: 1 pages (1 checked), made by Xerox WorkCentre 5865
  pages fine: 0 of 1
  page 1: scan_no_layer - a page image with no text ... [layer 0 words, OCR 230]
  tagged: no; PDF/A: no; form fields: 0
```

The first meets D1-D4 and T1 (it could add PDF/A, D5). The second fails D2 and T2: its pages were
digital before a system turned them into pictures. The third fails S1-S2: a digital signing or
commissioning process would have kept its text.

---

## The order that matters

By what the survey says each would recover:

1. **D1 and S1/S2** - born digital and signed digitally: the bulk of non-digital documents (signed
   letters, affidavits, scanned correspondence).
2. **D2** - no rasterizing: a single filer's publishing setting produced 1.6% of a random sample.
3. **An intake check** that runs D1-D4, D6, T4, T5 automatically and tells the filer which page
   failed, while the source still exists. *A rule in a manual is advice; a check at intake is a rule.*
4. **A1/A2** - data as data: small in count, large in value for the documents that carry results.
5. **T1, T2, D5, the manifest** - structure and custody: cheap for filers whose tools already
   produce them.
