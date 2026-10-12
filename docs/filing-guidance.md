# Capturing REGDOCS filings as digital documents: what the evidence says

The point of this work is not conversion for its own sake. It is to learn **how filings
should be captured** - produced, signed, scanned, redacted, filed - so that what the Canada
Energy Regulator receives is a faithful digital document, and nobody has to recover the text
afterwards. The converter ([scripts/convert/DESIGN.md](../scripts/convert/DESIGN.md)) and the
intake check ([check.py](../scripts/convert/check.py)) are the measuring instruments: they read
every page with the text layer *and* with OCR, so they can say, page by page, whether the file
holds the words the page shows, and what was lost when it does not.

Almost every defect found was introduced by *how the PDF was produced*, usually without the
filer knowing. The correct text existed in the filer's own software and was lost on the way.

## The headline

**[survey]** Of 5,000 REGDOCS documents drawn at random, **23.2% (95% CI 22.1-24.4%) are mostly
not digital documents** - their pages are scans, stub layers, broken layers or text drawn as
graphics. Pages: 65% digital, 11% digital with text also inside images, 15% page images with no
text at all, 5% scans with an OCR layer, 2% scans with a stub layer, 0.5% layers that do not decode,
0.4% text drawn as graphics. It is falling: **28-47% of documents in most years before 2012,
6-10% in 2021-2026** (2024: 32%, a year to look into).

The documents that are not digital are **the signed ones** (affidavits 79%, letters 23%) and
**the scanned ones** (scanner-made PDFs 98%, PDFs with no producer recorded 75%). What made a
PDF matters far more than any rule about content: office suites, reporting tools and print
drivers produce digital documents 93-100% of the time.

**Evidence, and how strong it is.** Each item says where its numbers come from:

- **[survey]** `check.py` on 5,000 documents drawn at random from the 268,185 PDFs held, 3 pages
  each (first, middle, last), 11,086 pages: rates by producer, tool, year and document type with
  95% Wilson intervals ([report](../scripts/convert/runs/v2/reports/2026-10-12-survey-5000.md)).
  It reads the text layer and OCR only, so it sees capture, not structure.
- **[samples]** the full converter on 625 sample documents (random draws, 55 document types, 59
  producers, 2002-2014 by year, signed documents 2012-2026): structure it alone sees - checkboxes,
  tables, drawings ([samples](../scripts/convert/samples/),
  [capture reports](../scripts/convert/runs/v2/reports/)).
- **[2026-09 corpus]** the earlier pipelines: 4,296 filings, 16,085 pages, each page compared with
  an independent commercial extraction ([lessons_learned.md](lessons_learned.md)).

Where a sample-based figure disagreed with the survey, the survey stands and the correction is
noted: several sample rates had been set by one or two documents.

---

## 1. The text layer must be the page

### 1.1 Export from the authoring application; do not print, scan or re-distil

**[survey]** Documents mostly not digital, by what made them: reporting tools (Crystal Reports,
SSRS) 0%, office suites 1% (Word 1%), print drivers 4%, PostScript/Distiller 7%, PDF libraries 11%,
PDF editors 21% (Adobe Acrobat 22%), **no producer recorded 75%**, **scanners 98%**.

**[2026-09 corpus]** Share of pages whose text layer was worse than OCR of the page: Adobe PDF
Library 5.1%, Word direct export 5.9%, Chrome/Skia print 13.7%, Microsoft Print To PDF 14.8%, no
producer recorded 28.1%.

### 1.2 Do not print text as graphics

**[survey]** 0.4% of pages: no text layer, many vector paths, words only OCR can read. Rare, but
total where it happens. **[samples]** A Microsoft Print to PDF exhibit list (4495825): 3,129 paths
and no characters a page; OCR read 528 words a page. Drawings and technical sheets printed through a
PostScript driver or pdfFactory (335411, 524567, 534342): labels as strokes, 5,000-205,000 paths a
page - CAD stroke fonts (AutoCAD SHX) exported as geometry. Cause: a driver setting ("print text
as graphics", "convert fonts to outlines") or a CAD export without searchable text.

*Correction:* the samples put this at 35% of Microsoft Print to PDF pages; that was one document.
The survey: 2% of that tool's pages.

### 1.3 Text must decode to the characters it shows

**[survey]** 0.5% of pages overall, **19% of Bentley MicroStation pages** (CAD fonts without a
Unicode mapping) and 3% of Ghostscript pages. **[2026-09 corpus]** one filing: 315 of 986 pages
display correctly and extract as nonsense; silent - the file opens, prints and reads correctly by
eye. Do not re-distil a finished PDF; do not strip font data; PDF/A "u" conformance rules it out
(19 of 5,000 documents are PDF/A).

---

## 2. Sign digitally; do not print, sign and scan

**[survey] The documents that are scans are the signed ones.** Mostly not digital, by REGDOCS
document type: **affidavits 79% (95% CI 64-89%)**, information request responses 37% (32-43%),
written evidence 37% (27-48%), **letters 23% (21-26%)**, letters of comment 20% (15-26%) - against
orders 3%, notifications 3%, applications to participate 2%, receipts 0%, rulings 0%.

**[samples] Fading for letters, not for affidavits.** 105 signed documents, 7 a year 2012-2026:
mostly scanned 2012-2016 12 of 35 (34%, 20-51%); 2017-2021 4 of 35 (11%, 5-26%); 2022-2026 4 of 35
(11%, 5-26%). Many digital letters now carry a pasted signature image, which keeps the text.
Affidavits stayed scanned (6 of 6). The survey's years agree: 6-10% of all documents in 2021-26.

Documents signed through an e-signature service (Adobe Acrobat Sign, 2 documents in the samples)
were fully digital.

**Recommendation (proposed):** accept and encourage digital signing - e-signature services, a
signed digital PDF, and a digital process for sworn and commissioned documents, where the paper
habit remains. Where a wet signature is required, file the digital document with a scanned
signature page, not a scan of the whole document.

---

## 3. A scan is not searchable just because it has a text layer

### 3.1 Scanner OCR layers are mostly good, and a quarter of pages are not

**[survey]** On scans carrying an invisible OCR layer, the share of the layer's words our OCR also
reads at the same place: medians 76-96% by producer (Copitrak 96%, Canon 92%, OmniPage 90%,
Acrobat Paper Capture 86%, Xerox MFP 84%, EPSON Scan 76%), but **19-28% of pages under 60%** for
Xerox, EPSON, Acrobat and Paper Capture (OmniPage 5%, Canon 6%). The two readers disagree, and
neither is known right: vision-model voters sided with our OCR on most disputed lines and with the
scanner's layer on some ([LESSONS.md](../scripts/convert/LESSONS.md), v2).

*Correction:* the samples gave Canon 36%, Xerox 33% and EPSON 51% medians; those were a few poor
documents. The finding that stands is the tail: a "searchable PDF" is unreliable on a quarter of
pages for some scanners, and nothing in the file says which.

### 3.2 Stub layers: a stamp makes a scan look digital

**[survey]** 2% of pages: a scanned page carrying a few digital words (a "received" stamp, a
markup, a page number), so the file has a text layer and tests as "searchable" while holding ~20
of the page's hundreds of words. RICOH MFP 17% of pages, Canon MFP 14%, Adobe Acrobat 9%, PDF
editors 8%.

*Correction:* the samples gave RICOH 92% and Bluebeam 59%; each was one filing.

**Recommendation ([2026-09 corpus] A3, strengthened):** where the source is genuinely paper, the
filer OCRs it at 300 dpi or better *and checks the result*; a layer that holds a fraction of the
page's words fails, however it was made.

**Check.** Words in the text layer against words OCR finds on one rendered page; the ratio
catches no layer, a stub layer and a poor scanner layer alike.

---

## 4. Redact by removing, not covering

**[survey]** 7 of 5,000 documents (0.14%) have words still in the text layer under a dark box that
renders flat - and only 3 pages of each were checked, so this undercounts. **[2026-09 corpus]**
one filing: 11,246 words of blacked-out invoice descriptions recoverable by copying the page. Rare,
and a privacy failure each time. Use a redaction tool that deletes the text; verify by searching
for a redacted phrase.

---

## 5. Keep forms and structure machine-readable

- **Keep form fields live; do not flatten forms.** [survey] 67 of 5,000 documents have live
  fields. [samples] 9 documents had live fields and **39 had checkboxes printed flat** (detected on
  the page, no form fields): their state can only be read from pixels. [2026-09 corpus] 4,057
  values in 151 documents read exactly from fields.
- **Add bookmarks to any document over ~20 pages.** [survey] 321 of 641 such documents (50%) have
  an outline. Outlines give exact headings and attachment boundaries.
- **Produce tagged, accessible PDFs.** [survey] 2,331 of 5,000 (47%) are tagged. Tags carry the
  structure that otherwise has to be inferred, and accessibility is an obligation in its own right.
- **Prefer PDF/A-2u or -3u.** [survey] 19 of 5,000 (0.4%). Rules out undecodable fonts (1.3);
  PDF/A-3 can embed the source data (6).
- **A file must open.** [survey] 1 of 5,000 could not be opened at all (3561022, a format error).

---

## 6. File data as data

- **Data tables as companion files** (CSV, Excel): lab results, monitoring data, schedules.
  [2026-09 corpus] tables are the weakest part of every extraction. [samples] a lab report's
  printed grid (2684908) ran its rows together on one reader and placed 97% of values on another:
  a detection limit printed as `<0.01` is exactly where a misplaced value changes a finding.
- **Spatial data as GIS files** alongside maps and alignment sheets. [samples] OCR reads about
  two-thirds of a digital drawing's labels, and reading at more angles barely helps: labels on
  maps are the hardest text to recover from a picture. [survey] Map documents: 32% mostly not
  digital (19-47%).
- **Machine-readable codes are fine, and useful.** [samples] courier waybills carry the address and
  tracking number as Code 128 and PDF417; decoded exactly.

---

## 7. Pages a readable size

**[survey]** 113 of 5,000 documents (2.3%) have sheets over 17.5 in. **[2026-09 corpus]** 13 pages
in 5 documents (182.5 x 34.5 in; 92 x 92 in) could not be processed at all; the current converter
reads them under a pixel budget. Cap page size, or file large drawings as tiles - and file the GIS
data (6).

---

## 8. Check at intake

A filer told to "ensure text is extractable" has no way to know they failed. These checks run on
the file alone in about a second a page, while the correct source still exists. `check.py`
already runs them on one PDF (`./scripts/convert/convert.sh check filing.pdf`):

| check | catches | reads |
|---|---|---|
| words in the text layer vs words OCR finds on a rendered page | no layer, stub layer, poor scanner OCR, text as graphics (1, 3) | text layer + one render |
| letter share and real words | undecodable text (1.3) | text layer |
| text under dark fills that render flat | cover-up redaction (4) | text layer + render |
| outline on long documents; PDF/A; tags; form fields | structure (5) | catalog |
| page dimensions | oversize sheets (7) | page boxes |

(Flattened checkboxes need the layout models, so they are in the converter, not yet in `check.py`.)

*"Page 47 has no usable text layer"* at submission is actionable; a sentence in a manual is not.
**A rule in a manual is advice; a check at intake is a rule.**

---

## 9. Not worth requiring

- **Page rotation** [2026-09 corpus]: rotated pages read as well as upright ones.
- **Document length** [2026-09 corpus]: 986-page filings read as well as 2-page ones.
- **Table formatting rules**: nobody can yet say what makes a printed table easier to read; file
  the data instead (6).
- **A named PDF product**: the path matters more than the product.
- **Line-numbered paper** [samples]: written arguments on numbered paper read cleanly and the
  numbers make lines citable.
- **Blank backs of duplex scans** [samples]: recognised and skipped (0.3% of pages in the survey).
