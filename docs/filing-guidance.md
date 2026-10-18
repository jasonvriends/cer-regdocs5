# Filing for automation: what the CER should ask of electronic filings

**The question.** The CER is moving toward AI and automation to speed up its work, including permitting.
Every automated step - search, an answer from a language model, a table pulled into a model, a check
against a condition - reads the *file*, not the page a person sees. This guide says what the files in
REGDOCS are actually like, what that costs, and what to ask filers for instead.

**The evidence.** The light pass of the converter read **every page of every PDF held - 269,025
documents, 3,615,229 pages, 1.23 billion words** - and recorded, page by page, how each was captured
and how far two independent readings of it agree. Unless marked otherwise, every number below is a
count over the whole corpus, not an estimate.

| source | what | |
|---|---|---|
| **[census]** | every page, every document: capture, text-layer health, agreement between readings, redactions, page size | [census](../scripts/convert/runs/v2/reports/2026-10-18-census.md), [catalog](catalog.md) |
| **[integrity]** | every file's catalog and tail: signatures and what changed after them, PDF/A, tags, bookmarks, form fields, attachments | [integrity.py](../scripts/convert/integrity.py) |
| **[samples]** | the full converter (layout, tables, figures) on 625 sample documents | [samples](../scripts/convert/samples/) |
| **[2026-09 corpus]** | earlier pipelines: 4,296 filings compared page by page with a commercial extraction | [lessons_learned.md](lessons_learned.md) |
| **[rules]** | the CER's current e-filing rules | [cer-filing-rules-today.md](cer-filing-rules-today.md) |

Companies are not named: the causes are practices and tools, and the same practice produces the same
file whoever uses it.

---

## The headline

**62,520 of 269,025 documents (23.2%) are mostly not digital**: their pages are pictures of text, or
carry text a machine cannot trust. One page in five is affected (740,000 pages, 20.5%). The 5,000-document survey
estimated 23.2% (95% CI 22.1-24.4%); the census counts the same.

| pages | share | what a machine gets |
|---|---:|---|
| digital: the file holds the page's words | 74.6% | the words, exactly |
| digital, plus text inside images the file lacks | 3.9% | most words; stamps, figures and scanned inserts need OCR |
| **page image, no text** (scan_no_layer) | **11.2%** | nothing until OCR is run |
| **scan with the scanner's own OCR layer** | **4.8%** | a reading that differs from an independent one on 67% of these pages |
| **stub layer**: a scan carrying a few digital words | **3.0%** | a handful of words; the file *tests* as searchable |
| **text that decodes to nonsense** (suspect) | **1.1%** | wrong characters that look right on screen |
| **text drawn as vector outlines** | **0.4%** | nothing until OCR is run |
| blank, other | 1.0% | |

**It is falling, and it is not only paper.** Mostly-not-digital documents were 20-45% a year in
2002-2011 and 5-9% in 2022-2026 (2024 aside). Of the 62,520, **32,272 were made by scanners and OCR tools,
16,254 by digital tools** - documents that were digital and were degraded on the way to filing - and
13,994 by tools that recorded no name. The 2024 spike (36%) is one filer's publishing system turning
4,137 digital documents into page images; without it, 2024 reads about 8%.

**What makes a document not digital is how it was produced, not what it is about.** Reporting tools
(Crystal Reports, SSRS) 0.0% not digital; office suites 1.7%; print drivers 5.9%; PDF libraries
11.5%; PDF editors 21.5%; **scanners and copiers 97-100%**.

**The rules today ask little of the file.** E-filing accepts PDF only; image-only PDFs are "not
recommended"; nothing is checked at upload; the legal signature is a paper receipt mailed within
three business days [rules].

---

## 1. Born digital: export, do not print, scan or flatten

**Problem.** [census] 40,295 documents are mostly page images with no text; 14,275 more carry only a
scanner's OCR layer. 16,254 of the not-digital documents came from digital tools: a document-management
or publishing step rasterized pages that were text a moment before (4,137 documents in 2024 from one
system, 300 dpi colour images of crisp digital pages), or a "print to PDF" of a printout. [survey, by
eye] about one image-only page in four is a digital page turned into a picture, not paper.
- **Pictures of text inside digital documents** [census]: on 140,804 digital pages at least 20 words
  exist only inside images - a table pasted as a screenshot, a scanned letter or certificate inserted
  into a report, a chart exported as a picture; 272,191 pages carry some words only in images. The
  document is digital; those words are not.

**Why it matters for automation.** An image has no words until OCR guesses them. Every guess is a
place a number, a name or a negation can change, and nothing in the file records which. 4,137
documents a year of avoidable OCR is a standing cost and a standing risk.

**Solution.** Save or export the PDF from the application that made the document (Word, Excel, the
reporting system, the CAD or lab system). Never rasterize, flatten or print-to-image a digital
document. Where a system must "flatten", it keeps the text layer or files the original. Insert tables,
text and charts as objects from their source, not as screenshots; attach a scanned insert as its own
document rather than as a picture inside a digital one.

**Guidance text.** *"Electronic documents must be created directly from the application in which they
were authored. Documents must not be printed and scanned, printed to an image, or converted to
images by a publishing or document-management system. Tables, text and charts must not be inserted
as images. Scanning is acceptable only for material that exists solely on paper (section 2)."*

**Check at intake.** Words in the text layer against words OCR finds on a rendered page: a page image
with no text from a digital tool fails, whatever made it.

---

## 2. Text that is the page: no stub layers, no unchecked OCR, no undecodable fonts

**Problem.**
- **Stub layers** [census]: 107,949 pages (3.0%) are scans carrying a few digital words - a "received"
  stamp, a markup, a page number. The file holds a text layer, so it tests as "searchable", while
  holding a fraction of the page. Multifunction copiers that stamp (RICOH 28.6% of their pages,
  Bluebeam 25.2%, Lexmark 12.6%, Canon 11.7%).
- **Scanner OCR layers** [census]: 172,381 pages carry the filer's or scanner's own invisible OCR
  text. On **115,560 of them (67%)** an independent OCR finds fewer than 90% of that layer's words at
  their places - 50-72% of pages whatever the tool. Two readings disagree; nothing in the file says
  which is right. [2026-09 corpus, samples] vision-model voters sided with the independent reading on
  most disputed lines and with the scanner's layer on some.
- **Undecodable text** [census]: 38,596 pages (1.1%) display correctly and extract as nonsense - fonts
  without a Unicode map. MicroStation 12.0% of its pages, CAD and GIS tools 5.3%. [2026-09 corpus]
  one filing: 315 of 986 pages, silently.
- **Hidden text that repeats or conflicts** [2026-09 corpus]: a page whose invisible layer holds the
  text more than once, or text the page does not show - one page held 17,518 characters of repeated
  passages behind 6,952 visible (4576114). Search and counting tools reward it as "more text".
- **Text drawn as outlines** [census]: 14,898 pages (0.4%): no characters at all, only strokes - a
  "print text as graphics" setting or a CAD export (Microsoft Print to PDF 5.9% of its pages,
  Bluebeam 5.3%, MicroStation 4.9%).

**Why it matters for automation.** This is the most dangerous class for AI: the file *answers*. A
search or a language model reads the hidden layer, not the image a hearing panel reads, so it can
cite text the page does not say. A page with a stub layer looks searchable and is not.

**Solution.** Where the source is genuinely paper, the filer runs OCR at 300 dpi or better *and
checks it*; a layer holding a fraction of the page's words fails. Fonts are embedded with Unicode
maps; PDF/A-2u or -3u ("u": Unicode) guarantees it. No "print text as graphics"; CAD exported with
searchable text.

**Guidance text.** *"Where a document exists only on paper, the filer must apply optical character
recognition at 300 dpi or better and verify that the text layer reproduces the page. Text in
electronic documents must be extractable as the characters displayed: fonts must be embedded with
Unicode mappings (PDF/A-2u or PDF/A-3u conformance satisfies this), and text must not be converted
to outlines or graphics."*

**Check at intake.** The same layer-against-OCR comparison: a stub layer, a poor scanner layer and an
undecodable layer each fail it; a letter-share test catches mojibake from the file alone.

---

## 3. Signatures and integrity: sign digitally, and make every file verifiable

**Problem.**
- **Signed documents are the scanned ones** [census]: affidavits **88.2%** not digital, requests to
  make an oral statement 64.9%, written evidence 36.0%, letters of comment 33.0%, letters 30.6% -
  against orders 2.5%, rulings 0.2%, hearing transcripts 0.7%. Correspondence and sworn types are
  **28,592 of the 62,520** not-digital documents (46%). Printing to sign and scanning back is the
  single largest source.
- **Digital signatures are rare, and keep documents digital** [integrity]: **1,410 of 269,025
  documents (0.5%)** carry a signed digital signature - 21 of 2,023 affidavits, 398 of 54,213
  letters - rising from 40 a year in 2018 to 224 in 2025. Only 73 of the 1,410 are mostly not
  digital, against 23.2% of all documents; e-signature tools 2.5%. Of the 1,410, 993 are signed
  over the whole file; 312 were followed only by long-term validation data (certificates and
  revocation lists an e-signature service adds, e.g. DocuSign); **105 had something else saved
  after the signature** - from a metadata update to annotations and stamps added on pages (2680051,
  946380) - which only a signature validator can judge. 346 carry a certification signature.
- **Nothing proves a copy is the one filed** [rules]: REGDOCS shows no hash; the signature of record
  is a paper receipt; the filing system says documents "cannot be altered" but gives a reader no way
  to check. The same file is filed more than once under different IDs: [census] 3,308 files appear
  under 8,252 REGDOCS IDs - 4,944 extra copies (4656998 = 4657424 = 4657571).

**Why it matters for automation.** Automated decisions need evidence whose integrity can be shown
later without paper: which file, unaltered, signed by whom, when. A scanned signature proves none of
that and costs the document's text.

**Solution.**
- **Digital signatures** to the PAdES standard (ETSI EN 319 142), with long-term validation data, for
  documents that need a signature - including sworn and commissioned ones, through a digital
  commissioning process.
- **A SHA-256 hash for every file at receipt**, shown on the REGDOCS record and the filing receipt, so
  anyone can prove a downloaded copy is the one filed.
- **A trusted timestamp** (RFC 3161) on the receipt, replacing the paper receipt as the record of
  filing.

**Guidance text.** *"Documents requiring a signature should be signed with a digital signature
conforming to PAdES. A wet-ink signature, where unavoidable, is filed as a scanned signature page
accompanying the electronic document - not as a scan of the whole document. The CER records a
SHA-256 hash and a timestamp for every file received and publishes them with the record."*

**Check at intake.** Signature present and covering the file (or followed only by validation data);
hash computed and recorded on receipt.

---

## 4. Redaction: remove the text, and say what was removed

**Problem.**
- **Cover-up redactions that leave the words** [census]: 313 documents (1,406 pages) have words in the
  text layer under dark fills. Of 12 looked at: real redactions leaving the words recoverable -
  landowners' names and addresses under black boxes (a participant involvement summary), contact
  names on a page that says "Personal contact information redacted for privacy", invoice
  descriptions (13,159 words on 36 pages); a table's data rows shaded out (probably intended); and
  a false alarm (dark legend bars on a map). Every flagged document needs review
  ([list](../scripts/convert/runs/v2/reports/redaction-review-2026-10-18.csv)).
- **Pages filed as black rectangles** [corpus]: whole pages that are one black fill, saying nothing
  about what was withheld or why (4600031, 4600032).
- [rules] The Filing Manual describes how to *request* confidentiality (section 1.5), not how to
  produce a public redacted version.

**Why it matters for automation.** A text extractor, a search index and a language model read the
words under the box as ordinary text: personal information becomes searchable and quotable. A blank
black page gives an automated reader no way to know that something was withheld.

**Solution.** Redact with a tool that deletes the underlying text and images; verify by searching the
output for a redacted phrase. Mark each redaction with its reason (a label in the box, or a line on
the page).

**Guidance text.** *"Public versions must be redacted with a tool that permanently removes the
redacted content. Drawing shapes over text, or highlighting it in black, is not redaction. Each
redaction should state the basis for withholding."*

**Check at intake.** Words in the text layer beneath dark fills that render flat (with real words
beneath, so a ticked checkbox is not a redaction).

---

## 5. Structure travels with the document

**Problem.**
- **Bookmarks** [rules] are asked for only for documents with a table of contents.
  [integrity] **15,950 of 33,974 documents over 20 pages (47%)** have an outline. **Bookmarks that belong to another
  document** [census]: a mail merge carries every recipient's outline into each letter (4049986: a
  2-page letter with 136 bookmarks; 3755199: 443 on 4 pages); 20 of 435 sampled documents with
  bookmarks average more than 2 a page that match no line.
- **Tags** (headings, lists, tables, reading order; accessibility): [integrity] **124,340 of 269,025
  documents (46.2%)** are tagged - usually because the authoring tool tags by default. Not asked for
  [rules].
- **Forms** flattened: [integrity] 3,234 documents (1.2%) have live form fields. [samples] 39 documents had
  checkboxes printed flat - their state readable only from pixels; [2026-09 corpus] 4,057 values in
  151 documents read exactly from live fields. [survey, by eye] Among scanned pages: hand-filled forms
  and a handwritten log - handwriting is the hardest text for any reader.
- **PDF/A**: [integrity] 1,460 documents (0.5%) declare PDF/A - levels 1a, 1b, 2b, 3a, 3b - and **none
  the "u" level** that guarantees text decodes to Unicode.
- **Other file properties** [integrity]: 1,872 files restrict permissions (owner-password encryption,
  which the Filer's Guide asks filers not to set); 138 carry embedded files (attachments inside the PDF); 179
  carry JavaScript (code that runs when the file opens; PDF/A forbids it); 6 could not be opened
  (format errors). [census] 18,615 documents record no producer at all - and 75% of those are not
  digital: a file that does not say what made it is usually a scan.

**Why it matters for automation.** Structure the author already had - headings, a table's header
row, a form field's value, where an attachment starts - otherwise has to be inferred by a model, and
inference is where errors come from. A tagged table says which cell is a header; a live form field
says which box is ticked.

**Solution.** Tagged PDF (PDF/UA, ISO 14289) from the authoring application; bookmarks for every
section and attachment of documents over 20 pages, belonging to that document; form fields kept
live, and forms filled in electronically rather than by hand; PDF/A-2u or -3u, which also rules out
JavaScript; attachments only as declared data files (section 6); document properties (title, author,
producing application) left as the authoring tool writes them.

**Guidance text.** *"Documents over 20 pages must include bookmarks for each section and attachment.
Documents should be tagged PDFs conforming to PDF/UA. Forms must be filed with their fields intact,
not flattened, and completed electronically. Documents must not contain JavaScript or undeclared
attachments. PDF/A-2u or PDF/A-3u is the preferred format."*

**Check at intake.** Catalog checks from the file alone: outline present and matching page text,
structure tree, form fields, PDF/A identification, JavaScript and embedded files, producer recorded.

---

## 6. Data as data: tables, maps and drawings beside the PDF

**Problem.**
- **Tables are the weakest part of every extraction** [samples, 2026-09 corpus]. Reading a printed
  table back into rows and columns is reconstruction, and readers fail on different tables: the best
  combination of four table readers still leaves words in the wrong cell or no cell (14 problem words
  on a 28-page lab sample after the best choice per page, from 132 with one reader); one reader alone
  gets 35% of table rows exactly right against 72% for another. One filing's grids produced 34,885
  misplaced words. A detection limit printed `<0.01` is exactly where a misplaced value changes a
  finding.
- **Machine-readable codes are welcome** [samples]: courier waybills carry the address and tracking
  number as Code 128 and PDF417, decoded exactly. A code is data a machine reads without guessing.
- **Maps and drawings** [samples]: OCR reads about two-thirds of a drawing's labels; [census] Plans,
  Profiles and Books of Reference 57.4% not digital, maps 31.9%.
- **The route** [rules]: e-filing accepts only PDF. GIS data has had a format rule since 2021
  (shapefile or file geodatabase, named coordinate systems) - but travels by e-mail under 10 MB or
  on a USB drive by mail, outside the record.

**Why it matters for automation.** A model asked about monitoring results, tolls or crossings needs
the values, typed and with units, not a picture of a grid. Data filed as data is exact, and every
downstream use - a dataset, a dashboard, a model - starts from it rather than from an extraction.

**Solution.** File tables of results as CSV (UTF-8) or Excel (.xlsx) beside the PDF, with a data
dictionary (column, meaning, unit, detection-limit convention); spatial data as GeoPackage (OGC) with
its coordinate system as an EPSG code; drawings with their DXF or DWG. Accept these files through
e-filing, linked to the document they support. PDF/A-3 can carry them embedded in the PDF itself.

**Guidance text.** *"Where a document presents tabular data - laboratory and monitoring results,
schedules, financial tables, crossings or condition-compliance registers - the data must also be
filed in CSV or Excel format with a data dictionary. Spatial data must be filed as GeoPackage with
the coordinate reference system identified. These files are submitted through electronic filing with
the document they support."*

**Check at intake.** The data files named in the filing's manifest (section 10) are present, parse, and
match their dictionaries.

---

## 7. Where AI reading is risky: vision models make up data

**Problem.** Generative vision models - the "read this page" models behind many AI document tools -
do not fail by leaving a gap. They fail by writing something plausible.
- **Invented values that look clean** [2026-09 corpus]: asked to read a contact table, a vision model
  returned the company name with one letter changed, the street number 980 as 680, the postal code
  V6Z 0C8 as "V5Z OCB", two e-mail addresses replaced by invented ones ("storage@", "motherboard@"),
  and **a third recipient who does not exist**. Nothing in its output marked any of it as a guess.
- **Numbers are where they slip** [samples, bench of 150 lines whose answer is known exactly]: the
  best of five models read 95.7% of words and 95.3% of numbers right - one number in twenty wrong,
  with no warning. On scans, models agreed with each other on 99% of words but only 73-92% of
  numbers.
- **Text where there is none** [samples]: two of five models wrote text on blank strips, every time
  (6 of 6); earlier pipelines saw the same on blank pages.
- **Tables and drawings fail worst** [2026-09 corpus]: every model tested kept under 11% of the words
  of an engineering-drawing table; models asked for one line carried on with table markup, repeated
  values ("100% 100% 100% ...") until stopped.
- **Valid is not right** [cer-regdocs3]: output that passed every structural check still listed
  instruments a page only mentioned, and labelled one form two ways.

**Why it matters for automation.** A missing value is visible and can be checked; an invented one
looks exactly like a real one. In a regulatory record the values that matter most - a detection
limit printed `<0.010`, a pressure, a setback, a landowner's address, a date in a condition - are the
ones a model is likeliest to "correct". The risk is highest where the file gives a machine nothing
better to read: page images, scans, and tables or drawings printed as pictures.

| content | risk if a vision model reads it from the image | the safer source |
|---|---|---|
| numbers in tables: results, limits, volumes, money | **high**: about 1 in 20 numbers wrong, unmarked | the data file (6), or the text layer |
| names, addresses, e-mails, legal descriptions | **high**: replaced by plausible inventions | the text layer |
| drawings, maps, alignment sheets | **high**: labels mostly lost or invented | CAD and GIS files (6) |
| blank, faint or stamped areas | **high**: text written where there is none | none needed |
| handwriting, signatures | high | digital signature (3) |
| ordinary prose in a digital document | low - but the text layer already holds it exactly | the text layer |

**Solution.**
- **For filers:** the remedy is the rest of this guide - a text layer that is the page (1, 2) and data
  as data (6) leave nothing for a model to guess.
- **For the CER's own use of AI:** a vision model may *vote* between readings or flag a doubtful
  line; it must never be the source of a value. Every token a model returns is checked against the
  text layer or an independent OCR of the same place, and anything absent from both is rejected.
  Models are tested on blank pages and on known answers before use; model-derived records stay marked
  unreviewed until a person confirms them. The converter already works this way.

**Guidance text.** *"Values that matter to a decision - measurements, results, limits, quantities,
names and locations - must be filed in a form a machine can read exactly: as text in the document and,
for tabular and spatial data, as data files (section 6). The CER does not rely on values recovered
from images of pages."*

**Check at intake.** Sections 1, 2 and 6's checks: where they pass, no vision model is needed.

---

## 8. Readable pages, whole documents

**Problem.** [census] 25,989 pages in 7,682 documents (2.9%) are over 17.5 inches. [rules] A drawing
not legible at 11 x 17 inches goes on paper with a placeholder that "cannot be viewed or searched";
files over 20 MB are rejected, so large documents are cut into pieces at section breaks. [2026-09
corpus] pages of 182.5 x 34.5 inches could not be processed at all by some tools.

**Why it matters for automation.** A placeholder takes the drawing out of the digital record. A
document cut into files must be stitched back together before anything can read it as one.

**Solution.** Drawings at standard sheet sizes or as tiles, with the CAD and GIS data (section 6).
Raise the size limit for born-digital files, which rarely need it; scans are what make files large.

**Check at intake.** Page dimensions; document split across files declared in the manifest.

---

## 9. The file is the document

**Problem.** [corpus] Of 860 PDFs downloaded on 2026-10-17, 20 are not documents: 6 REGDOCS serves
as empty files (0 bytes), 13 are web pages (filing receipts), and one (980558) is a 77-byte desktop
shortcut to "08BurdReport.pdf" - a link to the report, not the report. 73 recorded PDFs answer "not
found". [census] 18,615 documents record no producer.

**Why it matters for automation.** A missing document is invisible to a model that answers from
what it has; it cannot say what it never saw.

**Solution and check.** At upload: the file begins `%PDF-`, opens, has pages and text or images on
them, and is not a link. The filer is told at once, while the document still exists on their side.

---

## 10. A filing that describes itself *(proposed)*

A small manifest beside the files - generated by the e-filing form from what the filer uploads -
listing each file with its SHA-256, type, page count, language, PDF/A level and signature, and each
data file with the document it supports:

```json
{
  "filing": "C00000",
  "filer": "Example Pipelines Ltd.",
  "documents": [
    {"file": "C00000-1 Application.pdf", "document_type": "Application", "sha256": "...",
     "pages": 12, "language": "en", "pdfa": "2u", "signature": "PAdES"},
    {"file": "C00000-1 Table 3 water quality.csv", "document_type": "Data",
     "describes": "C00000-1 Application.pdf", "sha256": "...", "rows": 214,
     "dictionary": "C00000-1 Table 3 dictionary.csv"}
  ]
}
```

Signed and timestamped, it is the filing's chain of custody and its table of contents for machines in
one: tamper-evident and machine-readable at once.

---

## 11. Check at intake: a rule, not advice

A filer told to "ensure text is extractable" cannot know they failed. These checks run on the file
alone in about a second a page, at upload, while the correct source still exists:

| check | catches (section) | reads |
|---|---|---|
| words in the text layer vs OCR of a rendered page | no layer, stub layer, poor scanner OCR, text as outlines, rasterized digital pages, pictures of text (1, 2) | text layer + one render |
| letter share and real words | undecodable text (2) | text layer |
| text under dark fills that render flat | cover-up redaction (4) | text layer + render |
| signature coverage, hash, timestamp | integrity (3) | file |
| outline, tags, form fields, PDF/A, JavaScript, undeclared attachments, producer | structure (5) | catalog |
| data files present, parse, match dictionary | data as data (6) | files |
| page dimensions | oversize sheets (8) | page boxes |
| `%PDF-`, opens, has content, not a link | the file is the document (9) | first bytes |

`check.py` already runs most of these on one PDF (`./scripts/convert/convert.sh check filing.pdf`).
*"Page 47 has no usable text layer"* at upload is actionable; a sentence in a manual is not.

---

## 12. Not worth requiring

- **Page rotation** [2026-09 corpus]: rotated pages read as well as upright ones; [survey, by eye] 9 of
  29 scanned pages were sideways and OCR read them.
- **Document length** [2026-09 corpus]: 986-page filings read as well as 2-page ones.
- **Table formatting rules**: nobody can say what makes a printed table easier to read; file the data
  (6).
- **A named PDF product**: the path matters more than the product (section 1).
- **Specific fonts** [rules ask for Arial or Times New Roman]: what matters is that fonts are embedded
  with Unicode maps (2).
- **Line-numbered paper** [samples]: reads cleanly, and makes lines citable.
- **Blank backs of duplex scans** [census]: 14,635 pages, recognised and skipped.

---

## 13. What the CER's own systems should fix

Not every problem is the filer's. These were found in REGDOCS itself, while collecting the corpus:

| found | evidence | fix |
|---|---|---|
| files served empty | 6 of 860 downloads on 2026-10-17: "200 OK", PDF type, 0 bytes (4471674) | reject an empty upload; repair or mark the record |
| HTML documents whose images the public cannot fetch | filing receipts and forms reference letterhead and checkbox images held in the content server without public access [cer-regdocs1] | publish them as PDF, or open the images |
| records that answer "not found" | 73 recorded PDFs return 404 | mark withdrawn documents on the record |
| no fingerprint of any file | REGDOCS shows no hash; the filer's signature of record is a paper receipt [rules] | publish SHA-256 per file and timestamp the receipt (section 3) |
| the same file under several IDs | 3,308 files under 8,252 IDs | link copies to one file |
| placeholders that cannot be searched | paper-only and oversize documents [rules] | accept the drawing and its data electronically (6, 8) |
| documents cut into 20 MB files | the size limit [rules] | raise the limit for born-digital files |
| no bulk or programmatic access | the corpus could only be collected by crawling the website, one request a second | a bulk download or an API, with each file's hash and metadata |
| metadata only on the website | document type, company and filing come from page listings, not the file | publish the record's metadata with the file (the manifest, section 10) |

---

## What this does not yet measure

- **Tables, figures and forms across the whole corpus.** The light pass reads text and capture, not
  structure; table and form findings come from samples. A full conversion would count them.
- **Which reading is right where two disagree.** Scanner layers and independent OCR differ on 67% of
  scanner-OCR pages; the voters decide line by line, and no hand-transcribed ground truth exists yet
  (see [lessons-across-pipelines.md](lessons-across-pipelines.md)).
- **The redaction count.** 313 documents are flagged; only reviewed ones can be called leaks.
