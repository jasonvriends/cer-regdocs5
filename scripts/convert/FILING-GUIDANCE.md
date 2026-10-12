# Capturing REGDOCS filings as digital documents: what the evidence says

The point of this work is not conversion for its own sake. It is to learn **how filings
should be captured** - produced, signed, scanned, redacted, filed - so that what the Canada
Energy Regulator receives is a faithful digital document, and nobody has to recover the text
afterwards. The converter ([DESIGN.md](DESIGN.md)) is the measuring instrument: it reads every
page with the text layer *and* with OCR, so it can say, page by page, whether the file holds
the words the page shows, and what was lost when it does not.

Almost every defect found was introduced by *how the PDF was produced*, usually without the
filer knowing. The correct text existed in the filer's own software and was lost on the way.

**Evidence, and how strong it is.** Each item says where its numbers come from:

- **[2026-09 corpus]** the earlier pipelines: 4,296 filings, 16,085 pages, each page compared
  with an independent commercial extraction ([LESSONS-CORPUS.md](LESSONS-CORPUS.md)).
- **[v2 samples]** this converter on 420 sample documents, 7,268 pages: random draws, 55 document
  types, 59 PDF producers, 2002 and 2026 ([samples/](samples/)); the full tables are the capture
  reports in [runs/v2/reports/](runs/v2/reports/), produced by [capture.py](capture.py).
- **Measured** means the defect and its rate were measured; **proposed** means it follows from
  what was found but its benefit was not measured.

Samples are small for rare tools and types: a rate from fewer than ~10 documents is a pointer
to look, not a finding. Every round of the converter adds documents to the samples.

---

## 1. The text layer must be the page

### 1.1 Export from the authoring application; do not print, scan or re-distil

**Measured [2026-09 corpus].** Share of pages whose text layer was worse than OCR of the page:
Adobe PDF Library 5.1%, Word direct export 5.9%, Chrome/Skia print 13.7%, Microsoft Print To PDF
14.8%, no producer recorded 28.1%.

**Measured [v2 samples], pages by producer** (share whose layer reproduces the page): Foxit
printer 97% + 2% with text also in images, Acrobat Distiller 91% + 6%, Adobe PDF Library 92% + 4%,
Workiva, Aspose, SSRS, Excel, Qt, PDFsharp, pypdf 100%. Files with no producer recorded: 18% -
74% of their pages are page images with no text at all.

### 1.2 Do not print text as graphics - new [v2 samples]

**Measured.** A Microsoft Print to PDF exhibit list (4495825): no text layer, no image, 3,129
vector paths a page - the text was drawn as outlines. It looks like text, cannot be searched or
copied, and only OCR recovers it (528 words a page). 35% of Microsoft Print to PDF pages in the
samples, 85% of the Exhibit List pages. Cause: a printer-driver setting ("print text as
graphics", "convert fonts to outlines") or an application drawing text as shapes.

**Measured, and the same failure in drawings.** In the 2002-2014 sample, three drawings and
technical sheets printed to PDF through a PostScript driver or pdfFactory (335411, 524567, 534342)
hold no text at all: their labels are strokes, 5,000 to 205,000 paths a page, readable only by
OCR. This is how CAD exports behave when their stroke fonts (AutoCAD SHX) are drawn as geometry.
Export drawings with text kept as text (TrueType fonts, or the CAD tool's "searchable text" export
option) - and file the GIS or CAD data with them (6).

**Check.** No characters in the text layer on a page with visible text and many paths.

### 1.3 Text must decode to the characters it shows

**Measured [2026-09 corpus], strong for the defect, weak for the cause.** One filing: 315 of 986
pages display correctly and extract as nonsense (fonts with no Unicode mapping). Silent: the file
opens, prints and reads correctly by eye. **[v2 samples]:** 0-6% of pages by producer (Microsoft
Print to PDF 6%). Do not re-distil a finished PDF through PostScript; do not strip font data to
reduce size. PDF/A "u" conformance rules it out by construction (only 7 of 420 sample documents
are PDF/A).

---

## 2. Sign digitally; do not print, sign and scan - new [v2 samples]

**Measured.** The documents that are scans are the signed ones. By document (their most common
page class): **affidavits 5 of 5 scanned**, **letters 19 of 47** (10 with no text layer, 9 with a
scanner's OCR layer), **letters of comment 5 of 12**. Orders, receipts, information requests,
plans and certificates in the samples: all digital. A signed letter is printed, signed with a pen,
scanned - and the text the filer typed is replaced by a picture of it. One letter-writing campaign
(3700525) filed 572 scanned fax pages.

Documents signed through an e-signature service (Adobe Acrobat Sign, 2 documents, 11 pages) were
100% digital - a small sample, but the mechanism is plain: nothing is printed.

**Recommendation (proposed):** accept and encourage digital signatures (e-signature services, or
a signed digital PDF); where a wet signature is required, file the digital document with a
scanned signature page, not a scan of the whole document.

---

## 3. A scan is not searchable just because it has a text layer - new [v2 samples]

### 3.1 Scanner OCR layers vary from good to useless

**Measured.** On scans carrying an invisible OCR layer, the share of that layer's words our OCR
also reads at the same place (median by producer): iText 95%, OmniPage 92%, Adobe Acrobat 79%,
Acrobat Paper Capture 67%, EPSON Scan 51%, Canon MFP 36%, Xerox MFP 33%. Where two independent
vision models were asked to settle a disagreement, they sided with the scanner's layer on some
lines and with ours on most (LESSONS, v2). A multifunction printer's "searchable PDF" can carry
a layer that matches a third of the page.

### 3.2 Stub layers: a stamp makes a scan look digital

**Measured.** Scanners and markup tools leave a few digital words on a scanned page - a
"received" stamp, a Bluebeam markup, a page number. The file then has a text layer, tests as
"searchable", and holds ~20 words of a page that shows 500-1,600. RICOH MFP: 92% of pages;
Bluebeam: 59%; EQScan: 24%; Adobe Acrobat: 9%.

**Recommendation (A3, [2026-09 corpus], strengthened):** where the source is genuinely paper, the
filer OCRs it at 300 dpi or better *and checks the result*; a layer that holds a fraction of the
page's words fails, however it was made.

**Check.** Words in the text layer against words OCR finds on one rendered page; the ratio
catches no layer, a stub layer and a poor scanner layer alike.

---

## 4. Redact by removing, not covering

**Measured [2026-09 corpus], a privacy issue.** One filing: 11,246 words of blacked-out invoice
descriptions were still in the text layer; anyone selecting and copying the page recovers them.
**[v2 samples]:** none of the 420 sample documents had text under a cover-up. Rare, and severe
when it happens. Use a redaction tool that deletes the text; verify by searching for a redacted
phrase.

---

## 5. Keep forms and structure machine-readable

- **Keep form fields live; do not flatten forms.** [2026-09 corpus] 4,057 values in 151 documents
  read exactly from their fields. [v2 samples] 9 documents had live fields; **39 had checkboxes
  printed flat** (detected on the page, no form fields), whose state can only be read from pixels.
  Checkbox states are read by the layout models, but a field is exact.
- **Add bookmarks to any document over ~20 pages.** [v2 samples] 26 of 70 such documents have an
  outline. Proposed: outlines give exact headings and attachment boundaries.
- **Produce tagged, accessible PDFs.** [v2 samples] 187 of 420 are tagged. Proposed: tags carry
  structure that otherwise has to be inferred, and accessibility is an obligation in its own right.
- **Prefer PDF/A-2u or -3u.** [v2 samples] 7 of 420. Rules out undecodable fonts (1.3); PDF/A-3
  can embed the source data (6.1).

---

## 6. File data as data

- **Data tables as companion files** (CSV, Excel): lab results, monitoring data, schedules.
  [2026-09 corpus] tables are the weakest part of every extraction. [v2 samples] a lab report's
  printed grid (2684908) ran its rows together on one reader and placed 97% of values on another:
  a detection limit printed as `<0.01` is exactly where a misplaced value changes a finding.
- **Spatial data as GIS files** alongside maps and alignment sheets. [v2 samples] OCR reads about
  two-thirds of a digital drawing's labels and reading at more angles barely helps (LESSONS, v2):
  labels on maps are the hardest text to recover from a picture.
- **Machine-readable codes are fine, and useful.** [v2 samples] courier waybills carry the address
  and tracking number as Code 128 and PDF417; decoded exactly.

---

## 7. Pages a readable size

**Measured [2026-09 corpus], narrow.** 13 pages in 5 documents (182.5 x 34.5 in; 92 x 92 in) could
not be processed at all. [v2 samples] 15 of 420 documents have sheets over 17.5 in; all were read
under a pixel budget. Cap page size, or file large drawings as tiles - and file the GIS data (6).

---

## 8. Check at intake

A filer told to "ensure text is extractable" has no way to know they failed. These checks run on
the file alone in about a second a page, while the correct source still exists:

| check | catches | reads |
|---|---|---|
| words in the text layer vs words OCR finds on a rendered page | no layer, stub layer, poor scanner OCR, text as graphics (1.1, 1.2, 3) | text layer + one render |
| letter share and real words; fonts' Unicode maps | undecodable text (1.3) | text layer, fonts |
| text under dark fills that render flat | cover-up redaction (4) | text layer + render |
| checkboxes on the page, no form fields | flattened forms (5) | page + form table |
| outline on long documents; PDF/A; tags | structure (5) | catalog |
| page dimensions | oversize sheets (7) | page boxes |

*"Page 47 has no usable text layer"* at submission is actionable; a sentence in a manual is not.
**A rule in a manual is advice; a check at intake is a rule.** The converter already runs each
of these checks; packaged as a pre-submission tool they need no new method.

---

## 9. Not worth requiring

- **Page rotation** [2026-09 corpus]: rotated pages read as well as upright ones.
- **Document length** [2026-09 corpus]: 986-page filings read as well as 2-page ones.
- **Table formatting rules**: nobody can yet say what makes a printed table easier to read; file
  the data instead (6).
- **A named PDF product**: the path matters more than the product (Microsoft Print to PDF is fine
  for text and harmful when it prints text as graphics).
- **Line-numbered paper** [v2 samples]: written arguments on numbered paper read cleanly and the
  numbers make lines citable.
- **Blank backs of duplex scans** [v2 samples]: 76 pages; recognised and skipped.
