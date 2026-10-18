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
6-10% in 2021-2026**. (2024 reads 32%, but 81 of its 94 such documents are one filer's
information request responses, rasterized on the way to filing - 1.4; without them 6%.)

The documents that are not digital are **the signed ones** (affidavits 79%, letters 23%) and
**the scanned ones** (scanner-made PDFs 98%, PDFs with no producer recorded 75%). What made a
PDF matters far more than any rule about content: office suites, reporting tools and print
drivers produce digital documents 93-100% of the time.

**[corpus]** The light pass over every PDF held (2026-10-17: 267,495 documents, 3.6 million pages)
agrees: **20.4% of documents are read entirely by OCR** - no usable text of their own - and 8.2%
partly; 21.4% of pages. By era: 17% of documents dated 2011-2026, **33% of those dated 2002-2011**.

**Evidence, and how strong it is.** Each item says where its numbers come from:

- **[corpus]** the converter's light pass on all 267,495 documents: every page's text layer, OCR
  where needed, and its ledger of issues ([review](../scripts/convert/runs/v2/reports/2026-10-17-light-pass-review.md)).
  Whole-corpus counts come from its run log; issue rates from 5,000 ledgers drawn at random. No
  layout or table models, so it sees capture and text, not tables.
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

### 1.4 Do not turn a digital document into pictures of its pages

**[survey] Not a scan: digital documents rasterized by the filing process.** 81 of 5,000 random
documents (1.6%), all Trans Mountain information request responses filed in 2024 and made by an
iText-based system: each page a 300 dpi colour image (2550 x 3300) of a crisp, born-digital page
- a Health and Safety Management Plan, tables in colour, page numbers - and no text at all
(4461679). The text existed and was removed by the system that prepared the filing. It is why
information request responses read 37% not digital (without this filer: 31 of 222, 14%) and why
2024 stands out.

**[survey, by eye] About one image-only page in four is not paper.** 40 page-image pages drawn at
random from the survey and looked at: 29 scanned paper (signed letters and affidavits, faxes,
hand-filled forms, a handwritten log, stamped drawings, web maps printed and scanned back) and
**11 digital pages turned into images (28%, 95% CI ~16-43%)** - Trans Mountain's iText pages,
drawings exported as images, and a Word table rasterized to black out a column (a legitimate way
to redact: nothing is left underneath). Pixel statistics cannot tell the two apart reliably
(both can be one-bit and clean); the eye can.

**Recommendation (proposed):** a document-management or publishing step must not rasterize; if a
system "flattens" documents, it must keep or restore the text layer (or file the original).
**Check:** a page that is one full-page image with no text, from a digital source - the same
text-layer-against-OCR check catches it, whatever made it.

---

## 2. Sign digitally; do not print, sign and scan

**[survey] The documents that are scans are the signed ones.** Mostly not digital, by REGDOCS
document type: **affidavits 79% (95% CI 64-89%)**, information request responses 37% (32-43%; 14%
without one filer's rasterized responses - 1.4), written evidence 37% (27-48%), **letters 23% (21-26%)**, letters of comment 20% (15-26%) - against
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

**[2026-09 corpus]** one filing (4710294): blacked-out invoice descriptions - 13,159 words on 36
pages - still in the text layer, recoverable by copying the page. **[survey]** no other case in
5,000 documents (3 pages each, so a scattered redaction could be missed). *Correction:* a first
count gave 7 documents; looked at, every one was a single glyph on a dark box - a ticked checkbox,
a bullet, a map symbol - not a redaction, and the check now requires covered words. Rare, and a
privacy failure each time. Use a redaction tool that deletes the text; verify by searching
for a redacted phrase.

**Say what was removed.** [corpus] Some pages are filed blacked out whole: 4600031 and 4600032
(appendices, titles marked "Redacted") are pages that are one black rectangle - no text, no image,
nothing to say whether a page, a table or a paragraph was withheld, or why. A page that states
what was removed keeps the record readable. (How many pages are filed this way corpus-wide is
counted from the next conversion, which marks them `page_redacted`.)

---

## 5. Keep forms and structure machine-readable

- **Keep form fields live; do not flatten forms.** [survey] 67 of 5,000 documents have live
  fields. [samples] 9 documents had live fields and **39 had checkboxes printed flat** (detected on
  the page, no form fields): their state can only be read from pixels. [2026-09 corpus] 4,057
  values in 151 documents read exactly from fields.
- **Add bookmarks to any document over ~20 pages.** [survey] 321 of 641 such documents (50%) have
  an outline. Outlines give exact headings and attachment boundaries.
- **Bookmarks must belong to the document.** [corpus] A mail merge can carry every recipient's
  outline into each letter: 4049986 is a 2-page letter with 136 bookmarks; 3755199 has 443 on 4
  pages. Of 435 sampled documents with bookmarks, 20 average more than 2 bookmarks a page that
  match no line. Such an outline misleads any reader that trusts it.
- **Produce tagged, accessible PDFs.** [survey] 2,331 of 5,000 (47%) are tagged. Tags carry the
  structure that otherwise has to be inferred, and accessibility is an obligation in its own right.
- **Prefer PDF/A-2u or -3u.** [survey] 19 of 5,000 (0.4%). Rules out undecodable fonts (1.3);
  PDF/A-3 can embed the source data (6).
- **A file must open.** [survey] 1 of 5,000 could not be opened at all (3561022, a format error).
- **A file must be the document.** [corpus] Of 860 PDFs the scout recorded for 2002-2026 and
  downloaded on 2026-10-17, 20 are not documents: 6 that REGDOCS serves as empty files (0 bytes,
  e.g. 4471674), 13 that are web pages (filing receipts), and one, 980558, a 77-byte Linux
  desktop shortcut pointing to "08BurdReport.pdf" - the filer uploaded a link to the report, not
  the report. A further 73 recorded PDFs answer "not found".

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
| the file is a PDF with pages: not empty, not a web page, not a shortcut | missing documents (5) | first bytes |

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
- **Scans filed sideways** [survey, by eye]: 9 of 29 scanned pages were sideways, and OCR read
  them (185-542 words a page; a sideways reporting form read word for word).
- **Line-numbered paper** [samples]: written arguments on numbered paper read cleanly and the
  numbers make lines citable.
- **Blank backs of duplex scans** [samples]: recognised and skipped (0.3% of pages in the survey).
