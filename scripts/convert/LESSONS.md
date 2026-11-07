# Lessons learned: convert

What building convert has shown so far, with the evidence for each lesson. The five earlier
pipelines' findings are in [docs/lessons_learned.md](../../docs/lessons_learned.md); this document picks up where
they stop. The newest work is first; everything from "What we now know" down is the history of
the docling-based converter that v1 replaced (its baseline, bench and legacy files are in git
history, cer-regdocs5).

## v1: the evidence-ledger converter (2026-10-11)

Rebuilt as [DESIGN.md](DESIGN.md) describes: words from the text layer and from OCR on every page,
four layout models (heron, heron-101, egret-large, egret-xlarge) merged by vote, four table
readers per table, the figure classifier on every picture, a DoclingDocument built with
docling-core whose items name their evidence words. Docling's converter is no longer used.

**Test sets:** `samples/early-10.txt` (2002), `samples/recent-10.txt` (Oct 2026), and
`samples/random-100-a.txt`, drawn at random from the 267,365 PDFs not in any earlier sample.

| | 20 first | + random 100 (120 docs) |
|---|---:|---:|
| pages | 820 | 2,010 |
| documents failed / pages failed | 0 / 0 | 0 / 0 |
| evidence words | 285,850 | 666,475 |
| evidence words named by exactly one DoclingDocument item | 100% | 100% |
| item text holding the words it names | 100% | 100% |
| evidence words in the Markdown | 100% | 100% |
| time, RTX 4090, everything run on every page | 0.8 s/page | 1.1 s/page |

### What the readings show

- **Text layer vs OCR.** On pages whose layer is sound, OCR finds 85-99% of the layer's words at
  the same place (by producer: Acrobat Distiller 0.976, Word 0.942-0.987, Adobe PDF Library
  0.82-0.98). The layer stays the evidence; OCR is the check. 384 of 2,010 pages (19%) had their
  text from OCR: no layer, a suspect layer, or a scanner's hidden OCR layer.
- **Scans with a digital stamp.** 3225708 pp. 27-35 hold ~25 digital words (a stamp) over a
  scanned page; the layer holds only the stamp, and OCR recovers the other ~600 words as "ink
  not in the text layer". 521 pages had such words. Without OCR on every page these pages read
  as nearly empty with a sound layer: silent loss of exactly the kind [C Key findings 1] warns of.
- **Two OCR readings of one scan.** Our OCR and a scanner's own hidden layer agree on 65-83% of
  words on 2013 Paper Capture scans; ours read "Office natlonal da l'ánergle". Neither is known
  better: 67 pages are tagged `ocr_vote` for a voter pass (olmOCR-2 / GLM-OCR), not decided here.
- **Layout models agree least on** code (52-73%), key-value regions (82-87%), captions (85%),
  tables (80-93%); most on page footers, text and pictures (95-100%). 849 pages hold a region
  the models split on (`layout_disputed`).
- **Tables (632).** Words placed, on average: TableFormer accurate 96.7%, fast 93.7%, MuPDF
  text alignment 49%, MuPDF ruling lines 31% (but best on ruled tables: chosen 117 times, mostly
  Word and Adobe PDF Library tables; the 2002 Distiller tables are not ruled at all).
- **Pictures (2,000+):** logos 676, signatures 283, engineering drawings 234, icons, maps 97,
  photographs, charts. 601 pages have picture text only OCR read (`picture_text_by_ocr`).

### Problems found, and fixed

- **Transcripts read as tables.** Margin numbers made TableFormer see two-column tables
  (90560: 72 tables). Two numbering styles exist: line numbers 1-30 on the left restarting each
  page (GH-3-97, MH-2-95) and paragraph numbers on the right counting on across pages
  (EH-1-2000: 162, 163 ...), and a wrapped paragraph breaks the one-pitch test. The REGDOCS
  title ("Hearing Transcript") marks the document; a numbered page of it is read as one item per
  number, the number with its text. 90560: 72 tables to 4; 90517: 65 to 8 and 346 s to 101 s.
- **The canonical output was not the one checked.** The 100% check ran on the Markdown. Run on
  the DoclingDocument JSON loaded back, it found 757 words "missing" on 90476: text under
  pictures, which docling's `iterate_items` skips unless `traverse_pictures=True` - the same
  trap that cost the old pipeline's Markdown 91% of a filing [L docling's behaviour]. The check
  now reads the JSON as a consumer would, and the README says how to iterate it.
- **A table item named words its text did not hold:** words inside the table's box but in no
  cell. They are now a text item after the table (3225708: 97.8% to 100%).
- **Overlapping cells repeated a word** (90517: 1.12 Markdown words per evidence word): each
  word now goes to one cell, the smallest holding it.
- **RapidOCR returns odd shapes on blank pages and some others** (a float for the word list, or
  inside it). Unguarded, it failed the page; on 459903 it hid that the page is a photograph with
  no text. OCR failure is now an issue on the page, never the page's failure.
- **A grid cut into slivers scores perfectly on words.** 3768464 p28, a 2-column Word table with
  shaded bands: ruling lines gave 9 x 10 with 8 empty columns, TableFormer accurate 5 x 2, both
  placing 100% of the words. Rows and columns no word falls in are now counted, and among grids
  within 1% of the best word score the one with the least empty structure wins. Neither grid
  was right (about 9 x 2 with spanning headers): merging the two readings is open.
- **Contents pages** are read as one entry per line (dot leaders, or a `document_index` region),
  every grid still recorded.

### A second random 100 (`samples/random-100-b.txt`)

2,029 pages, much more scanned: 876 pages from OCR (a 572-page 2018 fax campaign, 2002 French
hearing volumes with Paper Capture OCR layers). No failures; every document's DoclingDocument and
Markdown hold 100% of its evidence words; no new code fault. Tables (769): TableFormer accurate
98.8% of words placed, fast 98.0%. What it showed:

- **"Scanned, no text" is right on all 11 pages tagged so**: photographs scanned sideways
  (797813), a blank scan, an oversize sheet with nothing printed. The tag stays: a picture
  reader may still want them.
- **On maps OCR misses what the layer holds, and that is not a bad layer.** Trans Mountain map
  sheets (3283303): the layer holds 621 labels; OCR finds 32% of them. Not text size (the boxes
  are 7-12 pt): labels set at angles along roads and rivers, over aerial imagery. The layer stays
  the evidence; `pdf_ocr_disagree` is now information on a page a map or drawing mostly covers,
  a warning elsewhere. (Agreement is also measured on words of OCR-readable size, ≥5 pt at 200 dpi.)
- **OCR on clean French scans is good with small, typical errors** (".hommes", "jeunes·qui",
  "rra d dn es"): the scan's own OCR layer (84% agreement on 155587) is the natural second
  reading. Both are kept; the voter pass decides word by word.

### Vision models as voters, measured against the text layer (2026-10-12)

`vote.py bench`: 150 line crops from sound-layer pages of the two random samples (the layer is
the answer, exactly; half the lines hold numbers) and 6 blank strips. Ollama, temperature 0.

| model | words right | numbers right | text on blanks | s/crop | verdict |
|---|---:|---:|---:|---:|---|
| qwen3-vl 8B instruct | 95.7% | 95.3% | 0/6 | 0.70 | voter |
| GLM-OCR | 92.9% | 91.9% | 0/6 | **0.10** | voter |
| olmOCR-2 7B (q8) | 90.7% | 91.3% | **6/6** | 0.66 | not a voter |
| DeepSeek-OCR 3B | 78.0% | 71.3% | **6/6** | 0.52 | not a voter |
| Granite 3.2 Vision 2B | 56.7% | 44.8% | 0/6 | 0.40 | not a voter |

- **A model carries on after the answer.** Asked for one line, GLM-OCR gave the line right and
  then repeated it, or added Markdown fences, `<table>` markup or `---` rules, until the token
  limit ("100% 100% 100% ..." for "O'Chiese First Nation"). Read only the first line of the
  answer; give each model the prompt it was trained on (GLM-OCR `Text Recognition:`, DeepSeek-OCR
  `Free OCR.`); treat a looping answer as an abstention, never a reading. With that, GLM-OCR went
  from 11 loops in 23 crops and text on every blank to none of either.
- **Strip only known tags** from a model's HTML (`<table>`, `<td>` ...): `<0.01` must survive.
- **Two models write text on blank crops** (olmOCR-2, DeepSeek-OCR), as the earlier pipelines
  found on blank pages [C §5.6]. They are left out.
- **Baidu Unlimited-OCR could not be tested under Ollama**: both community uploads
  (`richardyoung/unlimited-ocr`, `frob/unlimited-ocr`) answer with filler ("输出正确", "Text to the
  source image.") where DeepSeek-OCR, its parent, reads the same crop. Testing it needs Baidu's
  weights through transformers or vLLM.
- The figures understate every model alike: a table row whose cells align makes one "line"
  whose crop takes in neighbouring text, and letter-spaced text ("A n n u a l") counts its
  letters as words. The ranking is what the bench is for.

### OCR models, votes, barcodes, blanks: v2 (2026-10-12)

**Our OCR was the weak reader on some scans, and the voters showed it.** On lines where our OCR
and a scan's own OCR layer differed, both voters agreed with each other on 81.5% of 2,173 lines,
and on 372 of them both chose the scan's layer: 154133 p7, ours "RES E E J T T E S E TE T E S ULE",
the layer and both voters "RESIDENTS ARE AT A GREATER RISK DUE TO THE INADEQUACY OF THE SCHEDULE".
The line is clean bold capitals; RapidOCR's small models misread it in full-page context, as the
earlier pipelines found [C §5.6]. `ocr_bench.py`, 60 sound pages (the layer as the answer):

| RapidOCR models | words at their place | extra per 100 | s/page |
|---|---:|---:|---:|
| **PP-OCRv6 medium** | **92.5%** | **3.2** | 0.71 |
| PP-OCRv6 small (v1) | 89.9% | 4.3 | 0.60 |
| PP-OCRv5 mobile | 88.1% | 3.9 | 0.71 |
| PP-OCRv5 server | 77.0% | 8.3 | 4.05 |
| PP-OCRv4 mobile | 75.3% | 10.1 | 0.59 |

v2 uses v6 medium (weights copied into `models/`, hashed in the run identity); 154133 now reads
the line right. A line both voters give to the scan's layer takes that layer's words as its
evidence, ours kept as a reading (`lines_from_scan_ocr_layer`). Votes are applied by the line's
place, not by word ids, which shift when a page gains words, and only when cast on the same OCR
model's words and version.

- **A vote file lost its own provenance.** Votes were written beside older ones, and pyarrow's
  `Table.from_pylist` takes its columns from the first row: an older row without `ocr_model`
  dropped that field from every new vote, so 2,084 of 2,606 votes per voter read as stale and
  only 47 lines were applied. The table is now built from every row's keys, and stale votes are
  dropped when writing. A provenance field that can vanish silently needs its own check.
- **Barcodes are now read** (zxing-cpp on every picture region): the classifier found 35 barcodes
  in the facets sample and nothing decoded them. Courier waybills (619625) carry the address and
  tracking number as Code 128 and the shipment as PDF417: 19 values, each an evidence word
  (reader `barcode`, its box, the raw value kept beside a printable one).
- **Blank backs of duplex scans** were 53 "no text found" warnings in one 102-page filing
  (2784935, every second page). A page with no words and under 0.2% of it dark is `blank_page`
  (info), off the todo list.

**v2 over every sample** (320 documents: facets-100, random-100-a, random-100-b, early-10,
recent-10): 6,194 pages, 0 failed, 0 error issues. 2,167,243 evidence words, every one named by
exactly one DoclingDocument item; item text and Markdown hold 100% of them in every document.
4,710 pages from the text layer, 1,484 from OCR; 352 lines from a scan's own OCR layer by
both-voter decision; 76 blank pages; barcodes on 10 pages. Left on the todo list: 2,457 pages with a
region the layout models split on, 1,773 with picture text only OCR read, 504 drawings, 152
`ocr_vote` pages, 53 doubtful table grids. Run time 2 h 14 min, ~1.3 s a page with every reader
on every page.

**The facets sample** (`samples/facets-100.txt`): 100 documents across 55 REGDOCS document types,
two each, rarest first - geospatial data, plans/profiles/books of reference, exhibit lists,
certificates, permits, hearing orders, rulings, transcripts, maps. 2,155 pages, no failures, every
document's DoclingDocument and Markdown holding 100% of its words; tables (1,344) placed 98.7% of
their words with TableFormer accurate. Its new problems were the two above (blank backs, barcodes).

### Drawings, reading order, checkboxes, stacked cells (2026-10-12)

- **OCR at more rotations does not pay on drawings.** `picture_bench.py`, 60 digital drawings
  and maps (the layer's labels the answer): OCR of the crop at 0° found 67.3% of the labels;
  0°+90°+270° 70.7% and all four 71.4%, but with 40-58 more junk words per 100 found and three
  times the time; 300 dpi found fewer (65.5%). The earlier pipeline's +10.8 points came from six
  pages [C §5.5]; on sixty it is +3.4. Not adopted: the missing third of a drawing's labels needs
  another reader, not another angle.
- **Reading order, measured** (`order_bench.py`): the share of item pairs our order puts the other
  way from the text layer's own order, on born-digital pages. Single-column pages agree (median
  0); two-column pages do not (median 0.18, a third of them over 0.2). Disagreement is a symptom,
  not a verdict - on a Scotiabank report page (552663 p33) the layer stored the footer and chart
  data first and ours was right. Two faults of ours were real: a large region whose lines its
  smaller neighbours took was placed by its own top, ahead of text it no longer held (835125's
  address block), and words no region claimed were appended at the page's end. Items are now
  placed by the box of their words, items within 4 pt of the same top are one line, columns are
  decided per page, and every item on the page is ordered together. Random-100-a: mean
  disagreement fell for every producer (Adobe PDF Library 0.074 to 0.066, Word 0.083 to 0.076,
  Distiller 0.137 to 0.125); two-column median 0.182 to 0.167. On all three hundreds (3,011
  born-digital pages) with the grid and checkbox fixes too: two-column median 0.127 to 0.100,
  two-column pages over 0.2 from 232 to 208, single-column from 127 to 116; Adobe PDF Library
  0.052 to 0.035, Word 0.042 to 0.032, Distiller 0.084 to 0.070. Still open on two-column pages,
  and on files with no producer at all (mean 0.26 over 168 pages).
- **Regression run, all 320 samples after these changes:** 0 failures; 2,167,243 evidence words,
  every one named once in the DoclingDocument, item text and Markdown at 100% for every
  document; 510 checkboxes (123 ticked, 497 with a label); 33 tables where the ruling-line grid
  stacked values and lost to a finer one.
- **Checkbox states were lost**: a checkbox region holds no words of its own, and regions without
  words were dropped. Kept now with the state the layout models give (☒ / ☐) and its label - the
  words inside its box (the models' box usually takes in the label) or just right of it. On a
  2011 Application to Participate (2414747) the text layer's own Wingdings glyphs ("~" ";" ticked,
  "{" not) agree with the models' states.
- **Values stacked in one cell** are rows run together, and a coarse grid places every word that
  way: 2684908's ALS lab report, ruling lines 5 x 6 at 100% of words placed, TableFormer accurate
  43 x 7 at 97.2%. A cell of three or more lines, two-thirds of them values (a number, date or
  amount - not an equipment tag), no longer counts as placing its words (`fit`); the coarse grid
  falls to 42% and the 43 x 7 grid is chosen. Wrapped prose in a cell is not penalised.
- Table cell boxes are clamped to the page (a grid line drawn to x = -107).

### The producers sample (`samples/producers-100.txt`, 2026-10-12)

100 fresh documents drawn across 59 PDF producers, rarest first (Visio, PowerPoint, Google Docs,
ABBYY, Kofax, Workiva, Acrobat Sign, Sharp / RICOH / Konica / HP / Canon / EQScan scanners,
MicroStation, Bluebeam, LibreOffice, oversize sheets). 1,074 pages, no failures, every document's
DoclingDocument and Markdown at 100% of its words. Per producer, two new patterns:

- **Stub text layers were called sound.** Scanners and markup tools leave ~20 digital words on a
  scanned page (a "received" stamp, a Bluebeam markup); the layer's own characters are healthy, so
  the page counted as a text-layer page and OCR's 500-1,600 words a page came in as "extra ink"
  (4244189, Bluebeam: 34,454 such words). Nothing was lost - extra ink is evidence - but the page
  was misclassified, kept out of scan statistics and of voting. A page that is mostly image, whose
  layer holds under 100 words while OCR finds over 4 times as many, is now `stub`: OCR is its
  source and the stub's words stay as evidence where OCR does not cover them. 4244189: 58 of 61
  pages now OCR, extra ink 34,454 to 49. The earlier pipelines named this case [C §5.2].
- **Numbered paper.** Written arguments and evidence printed with line numbers 1-27 down the margin
  (2905206, Nitro): the numbers fell outside the layout regions, or inside a heading's, and came out
  as 410 stray one-number paragraphs. A page's margin run of numbers is now one furniture item
  (`line_numbers`), kept and citable, the paragraphs clean. Only the longest unbroken 1, 2, 3 ...
  run: footnote markers below restart at 1 and stay with their footnotes. 2905206: 410 to 4
  unclaimed words.
- Re-run of the whole sample with both: 0 failures, 100% everywhere; 103 pages now `stub`;
  397 pages from OCR, 677 from the text layer; 60 unclaimed words in the 100 documents.

### Round 3: regression and the 2002-2014 sample (`samples/years-2002-2014-100.txt`)

- **Regression over all 420 earlier sample documents** after the stub, numbered-paper,
  reading-order, checkbox and grid changes: 7,268 pages, 0 failed pages, 2,486,728 evidence words
  every one named once in the DoclingDocument, item text and Markdown at 100% in every document.
- **The fresh 100, spread evenly over 2002-2014** (7-8 a year), 1,104 pages: no failures, 100%
  everywhere, no new converter fault. Tables (784) placed 96.1% of words (TableFormer accurate).
- **Text drawn as strokes, in drawings.** Three drawings and technical sheets printed through a
  PostScript driver or pdfFactory have no text layer and 5,000-205,000 paths a page; OCR read
  270-413 words from each. CAD stroke fonts (AutoCAD SHX) exported as geometry. Into the guidance.
- **No record filed before 2012 had facets** (a sample of 3,000 records: 2002-2011 none, 2012 on
  all). Not REGDOCS: enriching one 2010 day (168 requests) returned full facets for its letter
  (Letter of Comment, s.52 Facilities, Gas). The pre-2012 records came from the earlier pipeline's
  seed and were never enriched, so document-type findings for the scan-heavy years were blind.
  Fix: run `scout.sh enrich` over 2002-2011 (~107,000 documents).

### Signed documents, 2012-2026 (`samples/signed-2012-2026.txt`)

105 letters, letters of comment, affidavits and applications to participate, 7 a year; no
converter failures. Mostly-scanned documents fell from 34% (2012-16) to 11% (2017-21, 2022-26);
affidavits stayed scanned (6 of 6). Into docs/filing-guidance.md section 2: the recommendation is about
sworn documents now more than letters.

### Tools added for the guidance

- `check.py`: the intake check on one PDF, or a survey of many (`--random N --pages 3 --out`),
  text layer plus one OCR reading a page, no layout or table models; `check_summary.py` turns a
  survey into rates with 95% intervals by producer, tool kind, year and document type.
- `capture.py` reads facets live from the scout record, so enrichment after conversion counts.

### The 5,000-document capture survey (2026-10-12)

`check.py --random 5000 --pages 3`: 4,999 documents read (one would not open), 11,086 pages,
116 minutes on the GPU - text layer and OCR only. 23.2% of documents (95% CI 22.1-24.4%) are mostly
not digital; by year 28-47% before 2012 and 6-10% in 2021-2026; affidavits 79%, letters 23%,
scanner-made PDFs 98%, office and reporting tools 0-1%. Results in docs/filing-guidance.md.

**Samples of a few hundred documents set wrong rates for anything a handful of documents
dominate** [C Key findings 8]. The full converter's samples had given: Canon / Xerox / EPSON scanner
OCR layers 36% / 33% / 51% agreement (the survey: medians 92% / 84% / 76%, with a quarter of pages
under 60% for Xerox and EPSON); stub layers on 92% of RICOH pages (survey 17%); text drawn as
graphics on 35% of Microsoft Print to PDF pages (survey 2%). Each was one or two filings. The
samples are for finding *kinds* of problem and checking the converter; rates for the guidance come
from the survey, with intervals, and every sample rate quoted there now says so or is corrected.

- **A covered redaction leaves the text readable.** 4710294 (invoices, 2024): 13,159 words on 36
  pages under black boxes are still in the text layer - anyone copying the page reads them. The
  converter leaves them out of every output and records only their count; the guidance says to
  redact by removing. A rasterized page is the opposite case: the redaction is real (4243920, a
  Word table blacked out and turned into an image).
- **A privacy finding must be looked at before it is reported.** The survey's 7 "cover-up
  redactions" were each one glyph on a dark box (a ticked checkbox "X", Wingdings "qqqq" on a map,
  a bullet). A redaction hides text, so three or more covered words of two or more letters or
  digits are now required; 4710294's real redaction is still found (13,159 words, 36 pages), the 7
  are not, and those glyphs stay evidence in conversion instead of being dropped as "redacted".

- **2024's outlier is one filer's publishing system.** 81 of 2024's 94 mostly-not-digital
  documents are Trans Mountain information request responses made with iText: born-digital pages
  rasterized to 300 dpi colour images with no text (4461679). Not scanned - the capture class
  calls them `scan_no_layer`, so it cannot yet tell a rendered page from a scanned one; a scan has
  noise and often one bit per pixel, a render is clean colour. Worth a class of its own.

- **Scan or rasterized page: the eye, not pixel statistics.** One-bit images and clean white
  backgrounds occur in both (Trans Mountain's iText renders are one-bit on some pages). 40 random
  image-only pages judged by eye: 29 paper, 11 digital pages turned into images. `check.py`
  reports an `image_kind` (bilevel / noisy / clean) as a hint, not a verdict. 9 of the 29 scans
  were sideways and OCR read them.
- **Table routing, measured on 4,348 tables:** words placed with fast + accurate + ruling lines
  96.6%, fast + accurate 96.1%, accurate + lines 95.9%, accurate 95.2%, fast 91.3%. Written into
  DESIGN as the table rule.

### The corpus pass and the catalog (2026-10-12)

- **One worker made the corpus pass take 9 days.** 3.5 pages/s: per-document overhead (each
  page's text layer read twice, the DoclingDocument reloaded to check it, six files written to a
  slow mount) in a single process. Workers now run in parallel, the text layer is read once, and
  the light pass checks its document in memory: 13.3 pages/s with 6 workers. Workers also loaded
  their models one after another (10 took 25 minutes to start); they now load at once.
- **A document with no words wrote a Parquet file with no columns**, which nothing can read
  (459903, a photograph). Building the catalog found it. `words.parquet` now has an explicit
  schema; files written before the fix are found by `pq.read_schema(...).names == []` and redone.
- Findings are now records too (docs/findings.jsonl), loaded into the catalog with everything
  else (docs/catalog.md).

### The corpus light pass, finished (2026-10-12 to -17)

Every PDF held, in light mode: 267,495 documents, 3,591,888 pages, 1.22 billion words, 0 failed
at the end; 769,463 pages (21.4%) read by OCR, 54,598 documents (20.4%) entirely by OCR and
21,949 (8.2%) partly. Reviewed from the run logs for every document and from 5,000 ledgers
(`runs/v2/reports/2026-10-17-light-pass-review.md`). Twelve relaunches, each after a fix above
(stalls, pdfium, GPU memory, crashes, cut-off ledgers, the skip check) or a machine restart.

- **The conservation checks held on every document, and that proves less than it looks.** Text
  100%, Markdown 100%, every evidence word named once by docling: 267,495 of 267,495. They show
  the pipeline loses no word between readers and outputs. They say nothing about whether a word
  was read right; that is only known where two readings are compared (text layer and OCR, our
  OCR and a scanner's layer, the voters).
- **A measure that was never taken must not read as zero.** Light mode skips OCR on a sound text
  layer, and the agreement was then stored as "OCR found 0% of the layer's words": 24,933 of
  28,530 text-layer pages in the sample, averaged into `report`. Now None, and `report` and the
  catalog read older ledgers' step timings to tell the two apart.
- **A ledger that does not name its code cannot be compared with another.** Every ledger said
  "v2"; 54% of the sample was written before the page boundaries step. Each ledger now carries a
  hash of the files a conversion loads, the git commit, and whether they differed from it.
- **Bookmarks are not always the document's structure.** A mail merge keeps every recipient's
  outline: 4049986 (2 pages, 136 bookmarks, 31 to one line) wrote "Marc Boucher" 31 times, 2.1
  Markdown words per evidence word; 3755199 (443 on 4 pages) 6.2. But unmatched bookmarks are
  usually real - attachment names, a scan's sheet labels (806765, 4367062). The tell is density:
  20 of 435 sampled documents with bookmarks average more than 2 unmatched a page. Now a heading
  is written once per title and page, and unmatched bookmarks only at 3 or fewer a page: those
  documents went to 1.00, and 30 with sound bookmarks kept every heading but one duplicate.
- **MuPDF's messages are evidence, and stderr loses them.** About 60,000 lines (13,479 "zlib
  error: incorrect header check", 9,922 missing XObjects, 7,809 broken xref entries) came out of
  eight workers at once, naming no document; one document printed 7,670 in a row. They now go
  to the document's ledger (`summary.mupdf_messages`, issue `pdf_file_problems`).
- **A page filed blacked out whole is a fact, not a failed reading.** 4600031 and 4600032: each
  page one black fill, no text, no image; they reported nothing at all. Now `page_redacted`.
- **Throughput follows the era.** 2.4 documents a second on 2025-2026 filings (median 0.20 s a
  page), about 1 a second in the OCR-heavy middle years, 4,000-7,000 an hour on 2002-2011 (median
  0.60 s a page; 33% of those documents are scans with no text of their own, against 17% after).
- **A machine shared is a machine that slows down.** See Memory and time: a game on the same GPU.
- **"Converted" is not "done".** Four documents converted on 2026-10-13 by a run that kept no log
  had pages fail with `BrokenPipeError` (4477638: 127 of 147 pages); their ledgers were complete,
  so the skip check took them as done and the corpus pass never tried them again. The catalog's
  `page_failed` issues found them five days later. A ledger now states its status (ok, partial,
  failed) beside its version and mode, where the skip check reads, and anything not ok is converted
  again on the next run. And every run keeps a log, including the quick ones.

### Tags: the author's own structure (2026-10-18)

MuPDF reads a tagged PDF's structure with the text (`get_text("xml", flags=TEXT_COLLECT_STRUCTURE)`:
each block inside its `<struct std="H1|P|Table|TR|TD|TH|L|LI|Figure ...">`). In 50 tagged filings drawn
at random (3-60 pages, first 15 pages read): 23 tag tables (TR/TD), 12 with header cells (TH); 15 tag
headings (H1 15, H2 8); 28 lists; 22 figures. With 46.2% of the corpus tagged, roughly a fifth of all
documents would give their tables' rows and cells exactly and one in seven its headings - a reader to
add beside the layout and table models, not instead of them (tags can be wrong; they are compared,
like every reading).
**Built (2026-10-18): `pdf_tags`, a fifth table reader.** The tagged table covering most of a
table region gives a cell per TD/TH (TH = header); it is scored like the others and wins a tie.
20 tagged, table-heavy sample documents in full mode: tags gave a grid for 948 of 2,613 tables
(36%), at least as good as the best model's on 820 of them (86%), chosen for 338; mean chosen
score 97.53% to 97.82%, no document worse; 812687 (448 tables) 98.5% to 100%.

### The largest documents are printed spreadsheets (2026-10-18)

The eight documents over 5,000 pages hold 2.0% of all pages; six of them are one dataset. Eighteen
months of one-minute wind readings (2013-01-01 to 2014-06-30; the PDF title still names the .xlsx;
producer PScript5, a print driver, then Distiller) were printed to 7,280 + 7,280 pages and again, from
mid-April 2013, to 11,563 pages, and the set filed twice (2498371/2498372/2498585 and 2498373/2499030/2498586; 2498585 = 2498586 byte for
byte): 52,246 pages, 1.4% of the corpus, about 800,000 distinct rows. Every page is 55 rows of four columns
of digital text - no OCR needed, every word exact - yet full mode spends about 2.5-3 s a page
rebuilding the table (about 40 worker-hours), and the 11,563-page one is the memory test for the
pass. A printout of a sheet is recognisable from the file (an .xlsx title, a print-driver producer,
thousands of pages of one repeating column layout); such a document could be read as one table
across all its pages instead of page by page. *Open:* measure that route on these six.

### Large documents: memory follows words, not pages (2026-10-19)

Rehearsal B (`runs/v2/reports/2026-10-19-rehearsal-large.md`): 986, 11,563 and 2,759 pages, all ok,
checkpoints resumed after a relaunch. Pages cost about 40 MB per 100 in a worker, so the 11,563-page
document ended its pages at 11.1 GB and assembled with no jump. The 2,759-page one holds 7.1 M words -
the most in REGDOCS - and jumped from 12.7 GB to 34.2 GB at assembly (43 GB of 47 for the machine).
Size the risk by words: documents over 2 M words (7 of them) run last, alone.

### The full pass, first three days (2026-10-19 to -22)

37,585 documents, 485,461 pages (2026 back to mid-2023), 5 workers: 0 GPU waits, every document
docling x = y, text 100%, md 100%. 2.76 worker-seconds a page; pages with a table on every page cost
4.4 s against 2.0 s with none, and the slowest 1% of documents take 37% of the time (4656932: 389
tables in 23 pages, 48 s a page). OCR supplied the text of 20.0% of pages, matching the census (20.5%).

**MuPDF aborts on certain pages (`free(): double free detected in tcache 2`), and the retry gives up
too early.** 5 documents of 37,585: 3 failed (4642786, 4473998, 4449303), 2 partial with pages left
out (4537539 page 130; 4458299 pages 4, 7, 10). Probing every page in its own process shows the
crashes are page-bound and clustered: 4642786 has 11 such pages (21-23, 28, 29, 31, 81-85), 4473998
6, 4449303 9. pdfium renders and reads every one of them. Two faults in the retry: a page sent to
pdfium still crashes, because other MuPDF calls on that page remain (the page is then skipped); and
each retry adds one page at a cost of a worker restart (about 45 s), with at most 3 pages skipped -
so a document with 6-11 bad pages fails. *Fix after the pass, not during it:* on the first crash, probe
every page in a throwaway process and send all crashing pages to pdfium at once, with no MuPDF call
on them; then convert the 5 again.

### The full pass, stopped and resumed (2026-10-24 to -26)

The machine lost power at 60,615 documents (full-main-2026-10-19.log), and the pass was then stopped
twice on purpose to free the GPU for a game and resumed each time (logs full-main-2026-10-24, -25).
76,128 documents at the last stop.

- **Resuming lost nothing.** Each relaunch skipped exactly the documents the logs had counted as ok
  (60,615, then 72,738) and redid only those cut off mid-write. A stop is `kill -INT` to the run's
  process group (it was started with `setsid`), and the workers exit with it.
- **The skip check still costs about 40 minutes after a restart.** It reads only each ledger's first
  400 bytes and its last 8, but 60,000-70,000 ledgers on the Windows drive with a cold cache are 40
  minutes of waiting on disk, with the GPU idle. The log is empty until it ends, which looks like a
  hang; it is not.
- **Every relaunch pays again for the five known-bad documents: about 2 hours of worker time.**
  The 2 partial ones are converted again because their status is not ok, and the 3 failed ones
  because they have no ledger. 4449303 alone takes 72 minutes: each stalled page waits out the full
  300 s before its retry. Measured 119 and 114 worker-minutes on the two relaunches. Until the
  retry fix above, leave them out of the run list, or accept that cost on every resume.
- **A failed document leaves no trace in the output.** The 3 have no `output/<id>/convert/`
  folder: the full run replaced their light outputs, and a failure writes nothing. Only the run
  logs say they failed and why, so the catalog, built from ledgers, counts them as missing
  rather than failed. A failure should write a ledger of its own (status `failed`, the reason,
  the pages reached), so the gap is recorded in the data where the search and the catalog read it.
- **Count failures by every spelling the log uses.** The run prints `FAILED` in capitals and
  `ok` and `partial` in lower case. A progress check that matched `^(ok|partial|failed)` reported
  0 failed for two days while 3 failed each run. Match case-insensitively, or on the documented
  forms.

### A table's numbers need the table's context (2026-10-27)

A cell can keep its row and column labels and still be misread when the table's qualifiers are lost:
units ("in thousands of dollars", "$/GJ", "10³m³/d"), notes such as "(unaudited)" or "seasonally
adjusted", footnotes, column dates. Reported for US government PDFs (r/Rag, 2026-10-26: one Federal
Reserve H.4.1 page had "Millions of dollars" in 1 of 3 chunks; a Census table's "Not seasonally
adjusted" reached none of them; a column outside the ruled area was missed by table detection).
Here, `cells.parquet` keeps each cell's grid position, header flag, box and words, and the scoring
counts words a reader leaves outside every cell, so a missed column shows as unplaced words rather
than disappearing. But nothing yet attaches a table's caption, units, notes, footnotes or each
column's full header path to its cells, and docling's Markdown table export drops footnotes (above).
*To do with the table export:* carry caption, units, notes, footnotes and header path on every
table and cell. Test it on two tables identical but for their unit headers, which must export as
different values. Count how many CER tables state units only in a caption or a note.

### Reading the output of 150,000 documents (2026-11-05)

After 150,715 documents of the full pass, every ledger check was clean: export recall 1.0 and
docling holding every evidence word in all of them, 4 errors in 2 documents. Then the output was
read: 10 random documents, then 200 (8 from each of the 25 largest document types, one random
page each, `runs/v2/review/read-200.tsv`), each fault sized on all 150,979 documents with
`review_scan.py` or on 1,500 random ones. Notes per document: `runs/v2/review/PROGRESS.md`.

- **Counting words proves nothing about where they go, and on scans it does not even prove the
  words are there.** Of the first 210 documents read, none had lost a word from its text layer, and
  about one in three had a fault that changes what it says. The agents' reading of 1,050 more (below)
  then found words lost after all: OCR drops whole lines on scans ("Rule 12, Liability of the
  Carrier" came out "Éule C lit", 2955501), and the converter replaces a scanner's hidden layer,
  even a better one, with its own OCR. The completeness check compares the output with the
  readings, and OCR is the only reading of a scan: it cannot see what OCR never read. Each fault
  below needed a reader to see it first, then a signature to count it.
- **Orders: the recitals sit under the wrong lead-in in ~56% of all 17,026 orders.** The bold
  lead-ins (IN THE MATTER OF, BEFORE, WHEREAS, AND WHEREAS; RELATIVEMENT À, DEVANT, ATTENDU QUE) are
  a column of their own beside the text; they become `##` headings written after the paragraph
  they introduce, stacked, or cutting a sentence ("## ATTENDU QU'au" after "moyen de
  l'ordonnance ...", 4239846). Adobe PDF Library 70%, Word 57%, Foxit 42%; every year 2015-2026.
  Right only where the lead-in is inline (4361108, 4411254). A lead-in is not a heading: it
  belongs to the paragraph sharing its first baseline, before it.
- **Filled PDF forms lose their answers when the answer is an image or a radio widget.** The CER
  export applications (Crystal Reports, ~10,000 documents 2015-2026, EN and FR) mark the chosen
  option with a green check-mark image: it exports as a loose `*[icon]*` after the options
  (3934254, 3716451, 4743077). The Acrobat reporting form's radio buttons export as "Yes No"
  (4572357). Drawn check boxes are read (SSRS Applications to Participate: `☒ [English þ]`), and
  the e-filing system's own PDFs print answers as text. The ledger already holds what is needed:
  the icon's box on the chosen line (12 x 11 pt, conf 0.99).
- **Numbered paragraphs become headings** (5.4% of documents have headings over 150 characters;
  among Arguments 19.8% have three or more, Written Evidence 8.5%): law-firm templates bookmark
  every numbered paragraph (3954446: 134 bookmarks, "20. As a result, and based on ..."), and
  convert's heading-number rule takes "7. Trans Mountain filed ..." the same way. The paragraph's
  first lines become `####`, the rest a `- ` item. A bookmark or numbered line that is a sentence
  is a paragraph.
- **Text layers can be wrong while looking sound.** 2967975 stores "(100)" as `ϭϬϬͿ` (a font's
  ToUnicode off by a constant), 2957200 a 3 as `٣`; both pages `digital`, `sound`, table values
  wrong. And 8,108 pages classed `digital` are one full-page image with a scanner's OCR layer
  (2,099 documents); on 2,350 of them convert's OCR agrees with that layer on < 90% of words
  (3746809, I.R.I.S.: "wr~ttenevidence", handwriting as headings). A page under one image is a
  scan, whatever its layer claims.
- **Tables: page markers and numbers.** A table over several pages is written once under its
  first page (5.2% of pages with >= 100 words have < 25% of their words under their own marker;
  3306261, 2969783), so "p. N" cannot be found in the .md. The transcript line-number rule takes
  a table's row-number column for margin numbers (65 of 1,500 documents have `*Lines ...*`, none a
  transcript): commitment numbers removed from a tracking table (4051056), and "Line" numbers
  removed from a schedule whose rows say "Line 4 + Line 9 + Line 10" (4087662). A dense ruled
  numeric table can come out as a header plus one cell holding a whole column (4166098, TC
  Mainline tolls; 1.9% of documents with tables, a floor).
- **OCR invents Chinese from marks.** PP-OCR's dictionary turns logos, valve symbols and check
  boxes into CJK characters: 19.6% of documents, 554,068 characters (Trans Mountain's logo is 而 on
  every TM filing; 口 are valves on a schematic, 3802842 p15), and OCR-only words enter text-layer
  sentences ("philosophy 金 focuses", 2969783). But some filings are in Chinese (4105885, a mill
  certificate, read correctly): drop isolated CJK in pictures and in Latin text, keep runs.
- **A word can leave its sentence.** A small region across part of a line takes its words out and
  writes them after: «peuples autochtones du Canada» became «peuples du Canada» with
  "autochtones" at the end of the footnote (4648280). 5 of 200 documents (2.5%).
- **Export noise worth one pass**: numbered items as `- 1.` (27.4% of documents), `- (a)` (19.1%),
  doubled bullets `- •` (22.2%), private-use Symbol/Wingdings glyphs (9.0%, 759,847), hyphen
  breaks kept with a space (13.5%), TOC leaders as U+FFFD (31 documents), U+202F stored as a Tamil
  sign in Justice Canada's French Acts, words split by kerning ("RECL AMAT ION"), the letterhead's
  logo repeated 3-4 times.
- **What reads well**: letters, rulings, letter decisions, transcripts, information requests,
  CER e-form outputs, French documents, most tables made by Word and Excel, scanned letters.
  Maps and drawings give label soup by design: the words are findable, there is no order.
- **The full pass's own lessons since 10-27**: 44 documents stalled "at start" only after ~12,000
  documents in one run (11-02), then about one every 300, on files of any size: the run, not the
  PDFs (retry them in a fresh run). One failed writing its ledger on a lone surrogate in the
  metadata (2955068). The skip check now takes about a minute, not 40.

### Reading at scale: how the 51 patterns were found (2026-11-05 to -06)

210 documents read by hand, then 1,050 by sub-agents (Sonnet), 25 a batch, each checking a page's
Markdown against its rendered image and the PDF's own text layer (`runs/v2/review/agent-read/`).
The register (`runs/v2/review/patterns.json`, `patterns.py`) keeps one record per pattern with up to
five examples; the prose is [docs/conversion-failure-patterns.md](../../docs/conversion-failure-patterns.md).

- **Count patterns, not occurrences, and stop when they stop coming.** The first 200 agent reads
  asked for every fault and produced counts; switching to "tag known patterns by id, report only
  what fits none, confirmed on the image" turned 100 documents into a few minutes of new knowledge.
  New patterns per 100 random documents: 4, 5, 6, 3, 2, 0, 2.
- **Then read where the corpus is rare.** Targeted batches (schedules, transcripts, French, drawings,
  Indigenous filings, 2015 scans) found A8 and E7 in under 40 documents, against about one new
  pattern per 50 documents at random; then nothing more. French text, Excel toll worksheets and
  Indigenous names in Latin script came out right.
- **Selecting by title does not select by content.** "First Nation" in titles gave engagement letters,
  none written in an Indigenous language. Searching every document's text for syllabics and
  orthography letters found 702; of 25 read character by character, 18 came through exactly
  (Cree and Inuktitut syllabics, Heiltsuk, Salish, Kwikwetlem, Secwepemc). The losses were OCR on
  scans (A7: ʔ as ?, ʷ as w, ə as e) and a filed text layer that already lacked the letters (B7,
  2785525: ʷ stored as a control code, ejectives missing). One page printed empty boxes for Cree
  syllabics its text layer held (4735593): the machine read better than the page.
- **Agents' false alarms have three causes, all avoidable:** text filed under another page's marker
  read as lost (F1: grep the whole .md first), the excerpt tool's 400-character cut read as
  truncation, and faults in the filed PDF itself read as conversion faults (H2: compare with the
  image and the text layer). Their tags for known patterns, on pages they did not render, are
  approximate; only image-checked examples went into the register.
- **A verified claim is cheap; check the surprising ones.** Spot checks confirmed every "meaning"
  claim tested (2955501 lost line, 4460101 "764", 3895437 blackline) and corrected one diagnosis:
  the CAD drawing whose notes vanished (4248055) was not a dropped line but a correct hidden layer
  discarded, which led to B5 (107,588 pages).
- **The CER's BERDI hit the same walls.** Its table extraction (Camelot, text layer only) estimates
  88-94% of tables complete and withheld the tables of 30 PDFs; its four "problematic" tests are B1,
  F3, F4 and F8. Its garbled-text test looks for `(cid:NN)` only, so wrong-but-decodable characters
  (2967975 "ϭϬϬͿ") pass it. On the documents of its five problem projects that we had converted
  (mostly post-2015 compliance filings) our table fault rates were at or below the corpus baseline.
  Three of their assessment volumes, converted for the purpose (408937 and 489855 Brunswick, 2006-07;
  702963 Eastern Mainline, 2011; 425 tables), had 1.2-2.6x the baseline share of mostly-blank tables
  (3.3-7.1% against 2.7%), no garbled cells (our converter falls back to OCR where a font has no
  character map, which Camelot cannot), and 712 private-use glyphs in one (B2). The mostly-blank
  table checked (489855 p35, a residual effects matrix) is empty in the filed PDF itself (H2):
  **a blank-cell test, theirs or ours, cannot tell a table empty on the page from one the converter
  emptied.** These projects' filings are harder; part of what both tests flag is the filing, not
  the extraction.

### What the pattern register says the converter should do (2026-11-06)

From `runs/v2/review/patterns.json` (one record per distinct failure, up to five examples each, read
from 1,285 documents against their page images; the prose catalogue is
[docs/conversion-failure-patterns.md](../../docs/conversion-failure-patterns.md)). Ordered by whether
the information is in the file: "in the file" faults are ours to fix; "partly" need better
inference; "not in the file" can only be flagged, never recovered.

- **B5 A hidden text layer always replaced by the converter's own OCR, even where the layer was better** (in the file; e.g. 4248055 p1, 4247461 p1): Every page with a hidden text layer (107,588 pages) took its text from the converter's OCR alone. Keep the layer as a second reading and vote line by line (as ocr_vote does for two OCR readings): a line the layer has and OCR lost or mangled should come from the layer. With no page image under it (3,307 pages, CAD drawings), the hidden layer is the drawing tool's own text: use it as the text.
- **B6 Invisible text that no reader sees surfaces as content (link anchors, hidden ids)** (in the file; e.g. 4590652 p60): Drop text a reader cannot see before building tables and paragraphs: tiny (< 5 pt) or covered text, white-on-white, text outside the crop box, link-anchor ids behind a visible label. Keep it in the evidence flagged hidden, never in the body.
- **D6 A page stored rotated has its whole text read in reverse or scrambled order** (in the file; e.g. 2957894 p20, 3577544 p384): Honour the page's /Rotate before ordering text: 179,146 pages in ~17,700 documents are stored at 90 or 270 degrees; in a sample of 40 rotated digital pages, 20 keep under half of the text layer's word pairs (an upper bound: tables lower it). Order words in the rotated reading direction, then compare with the text layer's own order.
- **E2 Headings that are not on the page** (in the file; e.g. 3561254 p1, 4142865 p1): Never make a heading from a file name, a stamp, a signature rule (______) or an attachment label. A bookmark that matches no line on its page is not a heading: today it is anchored at the page's first element and written as one (merged PDFs point every sub-file's bookmarks at page 1). Take headings only from text drawn on the page.
- **E3 List numbers and bullets kept as text** (in the file; e.g. 4411254 p1, 4200288 p1): When a list item's text starts with its own marker (1., (a), •, -), drop the Markdown bullet and keep the marker (or emit an ordered list).
- **F1 A table over several pages filed under its first page** (in the file; e.g. 3306261 p2, 2969783 p21): Write each page's rows under that page's marker (repeat the header, mark it continued). 5.2% of substantial pages have most of their words under another page.
- **F2 A table's own row numbers taken for margin numbering** (in the file; e.g. 4051056 p7, 4087662): Never take line numbers from words inside a table region or a ruled column; keep the margin rule for prose with numbers outside the text block.
- **I1 Pictures misclassified or repeated** (in the file; e.g. 3337507 p1, 4427645 p1): Merge overlapping picture regions before export (one logo was 4 regions); low-confidence classes should say 'picture'.
- **K2 Redacted text still in the file under the black box** (in the file; e.g. 4493099 p1, 4097987 p20): Words under a redaction box are left out of every output (done: text_under_redaction, 1,110 pages in 231 documents); report them so the filer's failed redaction is known, never index them.
- **B2 Symbol fonts: bullets and check boxes as letters** (partly; e.g. 3768118 p4, 2857182 p3): Map Wingdings/Symbol characters by font name before export (þ→☑, ¨→☐, private-use bullets → •); write a check box's option once, with its state. Never drop a private-use code point silently: the degree sign stored as U+F0B0 is deleted today (4334400: '100 °F' becomes '100 F'); map Symbol's U+F0xx range to Unicode (U+F0B0 → °, U+F0B1 → ±, U+F0B7 → •).
- **B3 Words broken or run together by spacing** (partly; e.g. 2784712 p1, 3335408 p7): Join single letters spaced at a regular pitch into words; join split words using the dictionary and the gap size; never trust a space narrower than the font's word space.
- **B4 Text drawn two or three times for bold** (partly; e.g. 3096924 p2, 4724669 p42): Drop a glyph run that repeats the same text at (almost) the same position; keep one copy.
- **C2 Labels and values come apart** (partly; e.g. 3716451 p2, 4577847 p2): Read form pages as label boxes and value boxes by position (a form reader), not as running text.
- **C3 A check box read in the opposite state** (partly; e.g. 2774076 p1): Where a form widget's value and a drawn tick disagree, report both or flag the field; never emit the widget's empty state over a visible tick.
- **D1 Bold lead-ins split from their text (orders' recitals, run-in headings)** (partly; e.g. 4265387 p1, 4593518 p1): A bold lead-in whose baseline equals the first line of the text beside it is part of that paragraph, read first; never a heading. Affects ~56% of 17,026 orders.
- **D2 Letterhead and sidebar blocks land inside the address or subject** (partly; e.g. 4364682 p1, 4403573 p1): Detect the letterhead/sidebar column (narrow block at the top or side, repeated across letters of one filer) and emit it before the body, never between label and value.
- **D3 Two columns or neighbouring lines interleaved** (partly; e.g. 3117386 p118, 3348046 p7): Detect columns before ordering lines; never merge two lines whose baselines differ by more than a fraction of the line height.
- **D4 A word moved out of its sentence** (partly; e.g. 4648280 p3, 4104450 p13): Assign words to items by text-layer line first: a line's words stay in the item that holds most of them unless the line truly spans two columns.
- **D5 Rotated text read with its words in scrambled order** (partly; e.g. 3309458 p36, 4584919 p417): Read rotated text (vertical table headers, side labels) as lines in its own direction, using the glyph rotation in the text layer; never sort rotated words by horizontal position.
- **E1 Numbered paragraphs become headings** (partly; e.g. 3954446 p6, 2786260 p1): A bookmark or numbered line that is a sentence (over ~20 words, or ends with . ... ;) is a numbered paragraph; keep only short bookmarks as headings. 19.8% of Arguments, 8.5% of Written Evidence.
- **E4 Footnote markers fused to words; hyphens kept at line ends** (partly; e.g. 3954446 p6, 2812079): Mark superscript footnote references ([^22]); join a lower-case word broken at a line-end hyphen (not digit groups, not real compounds).
- **E5 Superscripts and subscripts flattened into the text (footnote marks into numbers, exponents, units)** (partly; e.g. 4460101 p5, 4639857 p2): Emit superscript/subscript runs as such (^4, <sup>), and footnote references as references; never concatenate a raised digit to the number before it. Flag cells where a number ends in a raised digit.
- **E6 A wrapped line starting with a number taken for a list item, and lines reordered** (partly; e.g. 4680777 p1): Do not start a list item at a line that continues the previous line's sentence (lower-case or mid-phrase before it, no list marker spacing); keep wrapped lines in order.
- **E7 Transcript structure broken (line numbers, time codes, page numbers in the speech; speaker turns split)** (partly; e.g. 4305597 p84, 4555824 p38): Recognise transcript layouts (numbered lines, time codes, printed page numbers): move line numbers and time codes into structure (or drop them), keep speaker turns as units; export every page of one transcript the same way.
- **F3 A dense numeric table collapses into a column in one cell** (partly; e.g. 4166098 p20): A cell whose words sit on several text-layer lines with one number each is several rows: rebuild the grid from the ruling lines there.
- **F4 Cells shifted, split or merged** (partly; e.g. 4142865 p4, 2926972 p13): Score grids against the ruling lines and the header count; reject grids with mostly empty columns.
- **F5 A table's units and notes not attached to its numbers** (partly; e.g. ): Carry caption, units, notes, footnotes and the header path on every table and cell.
- **F6 Row numbers offset against their rows** (partly; e.g. 3884322 p29, 4202048 p10): Align a numbered column to rows by baseline, not by order; check the first and last rows (an empty first row, two rows fused at the end are the sign).
- **F7 A table's group header or category band separated from its rows** (partly; e.g. 3336044 p38, 4543487 p2): Keep a table's internal group headers with the rows that follow them; never emit a section heading between parts of one ruled table.
- **F8 Neighbouring tables merged into one; the next table's title fused into the last row** (partly; e.g. 3084351 p1): Split a detected table where a full-width title row or a gap in the ruling separates two grids; never append a table title to the previous table's last cell.
- **F9 Text from outside a table (footnotes, margin stamps) spliced into a row** (partly; e.g. 2902086 p3, 3179563 p8): Keep text below a table's last rule (notes, footnotes) out of the grid; attach a footnote to the cell that carries its marker.
- **G2 Chart labels read into the table beside it, with plausible wrong values** (partly; e.g. 4553651 p16): Keep text inside a chart region (Gantt bars, plotted labels) out of the adjacent table's cells; mark chart text as chart text.
- **H1 Bilingual side-by-side text read across both languages** (partly; e.g. 4518996 p2, 4657049 p3): Detect the two-language column layout and emit each column whole.
- **J1 Tracked changes flattened: deleted and inserted text read as one** (partly; e.g. 3895437 p39, 3935185 p77): Detect strike-through lines drawn through text and underlines in a second colour; mark deleted text (~~...~~) and inserted text, or flag the page as a blackline so its text is never quoted as in force.
- **J2 Colour or highlight carries the meaning** (partly; e.g. 3578263 p1): Record cell fill and text colour where a table uses them (a highlight column, red/green status), at least as a flag that colour carries meaning on the page; the text alone is incomplete there.
- **J3 Icons, indentation and braces carry the relationships (hierarchy, who represents whom)** (partly; e.g. 3898988 p1, 2837588 p4): Keep indentation level and icon class with each row of an index; an exhibit list's PDF/folder icons say what is a document and what is an attachment.
- **A1 OCR drops or mangles whole lines** (not in the file; e.g. 2955501 p1, 3117386 p156): Recall is measured against OCR itself, so lost lines are invisible. Add an independent check: text lines in the page image (ink rows) with no OCR words; a second OCR model on pages where the first leaves gaps; flag pages whose OCR word count is far below the page's ink.
- **A2 A scanner's OCR layer passes for real text** (not in the file; e.g. 3746809 p2): A page under one full-page image is a scan whatever its layer says: route as scanner_ocr, compare with independent OCR, treat agreement < 0.9 as suspect (8,108 such pages classed digital; 2,350 with agreement < 0.9).
- **A3 Handwriting, stamps and signatures read as words** (not in the file; e.g. 2897620 p2, 3718917 p1): Keep OCR text from pictures classed signature/stamp and from handwriting out of the body (keep it in the evidence); never make it a heading.
- **A4 Graphic marks read as characters (logos, symbols, check marks)** (not in the file; e.g. 4200288 p1, 3802842 p15): Treat an OCR word that is a lone CJK character (or a lone mark) in a Latin-script page or inside a picture as a symbol, not text; keep runs of CJK (real Chinese exists: 4105885). Never let OCR-only words into text-layer sentences on digital pages.
- **A5 Handwriting read as a plausible wrong value (corrections over typed text, handwritten dates)** (not in the file; e.g. 3803169 p1, 3750376 p18): Where handwriting overlaps typed text, flag the line as amended rather than merging both readings; check identifiers (hearing numbers, file numbers) against other mentions in the same document.
- **A6 OCR drops small marks or digits that carry value (zero dashes, repeated digits)** (not in the file; e.g. 3390240 p2, 4368429 p14): Where a currency sign or a column of numbers has an empty slot, check the image for a dash or dot; report 'unread' rather than an empty cell.
- **A7 Indigenous-language text read as junk or stripped (syllabics, special letters)** (not in the file; e.g. 3096662 p1, 4667022 p4): OCR models trained on Latin script turn Canadian Aboriginal syllabics into Latin letters and math symbols and drop superscript letters (ʷ) in Salish and other orthographies. Detect syllabic-looking shapes and flag the line as unread; use a syllabics-capable model where available; never emit the junk as text.
- **A8 OCR substitutes look-alike characters in codes, ratings and identifiers** (not in the file; e.g. 3748169 p1, 2957277 p2): Dictionary-free tokens (class ratings, material codes, datums, drawing numbers) get no help from language models: check them against known vocabularies (CL150/300/600, NAD83, A105N) and flag mismatches; report OCR confidence per token for codes.
- **B1 Wrong characters behind right-looking glyphs** (not in the file; e.g. 2967975, 2957200): Compare digits and words with OCR on a sample of every digital document; a layer that disagrees is suspect even if it decodes to 'words'. Decode known offsets only where OCR agrees. Map known substitutions (U+0BD7 for U+202F in Justice Canada French; U+FFFD runs as leaders).
- **C1 The answer is a picture or a widget, not text** (not in the file; e.g. 3934254 p1, 4496452 p1): Attach a small icon picture (check mark) to the text line it sits on and mark that option chosen, the others not; read AcroForm widget values (radio buttons) into the output.
- **G1 Drawings, maps and charts: labels without order or relation** (not in the file; e.g. 3306697 p1, 3005387 p42): Do not attempt reading order; keep labels findable with their boxes; never present them as prose.
- **G3 Box-like marks read as form check boxes (chart legends, redaction boxes)** (not in the file; e.g. 2958070 p17, 4464158 p5): Run the checkbox detector only on form regions: a box-like symbol in a chart legend, or a redaction placeholder box ('PII'), is not an answer.
- **H2 Content cut off or missing in the filed PDF itself** (not in the file; e.g. 2813818 p5, 4572575 p2): Not a converter fault: record it (words ending at the page or cell edge, a column clipped) so the gap is known; nothing can recover it.
- **K1 Redaction leaves a silent gap; exemption stamps read as numbers** (not in the file; e.g. 4574102 p20): Detect redaction boxes and exemption stamps (s.17, s.19(1), 'Pages withheld'); emit an explicit marker [redacted: s.19(1)] where text was withheld; never read the stamp as body text.

### Open

- Lines the voters split on, or call "neither": 498 / 279 per voter; a third voter, or a human.
- Text inside scanned drawings and maps; checkboxes (detected as regions, state not yet used).
- Reading order on multi-column and rotated pages is a simple column rule; not yet measured.
- Table grids that neither reader gets right; Table Transformer as a fifth reader.
- Baidu Unlimited-OCR through transformers/vLLM, since Ollama uploads do not work.

## What we now know

1. **The text layer is the reliable part.** MuPDF and PDFBox read it completely and fast,
   and never failed on any of the ten hardest documents. Tika (PDFBox) finished all ten; the
   structure readers failed on three or four each.
2. **Structure readers add value but lose text and break on heavy pages.** Every loss of text
   found so far came from treating a structure reader's output as *the* text: text inside
   pictures, labels on diagrams, docling's Markdown export, pages docling cannot parse.
3. **Tier 0 knows the dangerous pages in advance**: oversized sheets, vector path counts in the
   tens of thousands, scans, broken text layers. Pages do not need to be found by crashing on them.

## The plan that follows from it

| step | what | why |
|---|---|---|
| 1 | **Evidence layer from the text layer** on every digital page: every word with its box, straight from MuPDF | complete by construction; seconds for the corpus |
| 2 | **Structure as an overlay.** docling (or opendataloader) runs per page, within a budget set by tier 0; its items take their text from the evidence-layer words inside their boxes, and any word no item claims becomes an item of its own | text cannot be lost, even if the structure reader fails; a heavy page gets less structure, flagged |
| 3 | **Route by tier 0 before conversion.** Oversized or path-heavy pages get lower render settings (legacy's pixel budget) or text layer only | no crashes, no hour-long pages |
| 4 | **Tables, compared per table.** TableFormer (v1, and v2 once measured), Table Transformer and a line grid from the page's ruling lines; keep the reading with the fewest misplaced words | docling's grid is the main source of errors (34,885 on one filing); see below |
| 5 | **OCR route** for the 12,151 pending pages (scans, broken text layers), with a second reader voting | the remaining 16% of pages |
| 6 | **Exports** (Markdown, RAG chunks), with a check that every evidence word reaches them | docling's default export dropped 91% of one filing's words |

Working method: the ten baseline documents are the test set, and every change shows before
and after on them. A random sample of about 50 ordinary documents guards against breaking the
easy cases. Full-corpus runs only confirm a change. `bench/` keeps the tools comparable.

## Tables: TableFormer, Table Transformer, or both

Neither is better overall; they fail on different tables. In the legacy lab's 28-page sample,
counting text-layer words that end up in the wrong cell, in no cell or nowhere ("problem
words"), and choosing the best reading per page:

| readers available per page | problem words |
|---|---:|
| docling as saved (TableFormer fast) | 132 |
| best of TableFormer fast / accurate | 53 |
| + line grid (pdfplumber, from ruling lines) | 22 |
| + Table Transformer | **14** |

- **Table Transformer** read a borderless 13-column equipment table the line grid could not
  (17 problem words to 0), and did worse than the line grid on a large ruled table (14 of 17
  columns found).
- **The line grid** is best on ruled tables and costs 0.12 s a page; it cannot see an unruled table.
- **TableFormer alone** gets 35% of table rows exactly right, against 72% for PyMuPDF's table
  finder (lab parser benchmark, 16 pages with a line-grid reference).
- Table Transformer and the line grid only draw boxes; the words come from the text layer, so
  **neither can invent text**. And the choice per table needs no ground truth: the conservation
  check scores each reading against the text layer.

So all of them, chosen per table. Caveat: 28 pages is a small sample.

### TableFormer accurate on the ten (run convert-0f81d371, 2026-09-28)

The bench result did not hold on whole documents. Accurate cut cell disagreements by a
third across the ten, but on 4672628's lab tables it merged cells (a third fewer) and their
text did not survive the merge: about 20,000 words left the export (96.8% to 87.3%). It
helped 4656998 (103 to 38 errors) and 4664902 (664 to 475). Two lessons: **score tables by
words, never by issue counts** (one issue per cell rewards fewer cells), and **a 40-page
sample can reverse on a 691-page filing**. Fast stays the default; the choice has to be per
table.

### OCR, redactions, and comparing with Azure (2026-09-30)

- **The renderer is a reader too.** OCR through docling returned nothing on 4710294's clear
  French scans: docling-parse rendered them as black noise (plain Flate RGB images), so layout
  and OCR saw nothing. The same engine on a MuPDF render read the page in 0.6 s; docling's
  pdfium backend fixed it. Look at the image a model is given.
- **The text layer is not always the page.** Invoice descriptions blacked out on the page
  were still in the text layer, and convert was copying them out: a privacy failure, found only
  because Azure, reading the image, disagreed. A dark fill over a word is not enough evidence
  (spreadsheet shading made 82,469 false hits); the render decides.
- **Compare systems on what the page shows.** Against the raw text layer, Azure looked worse
  than it is: invisible text, line-style labels on drawings, and redacted text are in the layer
  and not on the page. The yardstick is visible, unredacted words; drawings are reported apart.

### Text from the text layer (2026-09-29)

- **Check the output for text added, not only text lost.** Taking item text from the text
  layer raised export recall everywhere, and silently repeated 20,592 words on one filing:
  overlapping boxes, each word given to the smallest, left the item docling had used holding
  docling's copy. Counting words that appear in the export more often than on the page caught
  it; recall alone never would.
- **Where two readings overlap, prefer the assignment the structure reader made.** docling
  already decided which item a word in an overlap belongs to; geometry is the fallback.

### Ruling lines, and how to judge a grid (2026-09-29)

- **A score must not be able to see its own answer.** The word score compares a cell's text
  with the text-layer words in its box; a grid built from those words passes by construction.
  Line grids needed a measure of the boxes alone (fit: words crossing borders, in no cell,
  columns merged, words split apart).
- **Look at the page before trusting a new measure.** Fit favoured a text-alignment grid on
  4672628 by three to one; drawn on the page, it had run rows together and cut words in half.
  It also counted dot leaders as words (1,096 "straddles" from 190 real words) until it
  skipped them as `tokens()` does.
- **Ruled tables are the line grid's; TableFormer's are the rest.** 4664902: 477 errors to 0.
  4647200: 9,065 to 3,625. A partly ruled table gives a line grid too coarse to use.

### Table Transformer and rows run together (2026-10-02)

The 67 documents of the 113 that have tables, leaving out 4647200, 4672628 and 4710294: run
convert-42241a3e against convert-87f469f1, 448 tables on which Table Transformer was tried.

| | before | after |
|---|---:|---:|
| table words badly placed (fit, as measured before) | 1,078 | **570** |
| table words placed | 53,478 | 53,489 |
| table words lost | 52 | 48 |
| tables kept from Table Transformer | | 19 |
| Table Transformer's own time, 1,003 pages | | 23 s |

- **Table Transformer wins where nothing else had the grid**: tables of contents and
  borderless lists (4646070: 397 badly placed words to 43), books of reference with two-line
  land descriptions (4692613: 103 to 26). It is kept on about 4% of the tables it is tried on.
- **Its spanning cells need checking against the text.** At low scores it proposes spans that
  join rows each holding an entry of its own (4646070's contents page: four entries in one
  cell). A span is now kept only if each direction is needed: the rows it joins by a word
  crossing a row border or by all its words being in one row, the columns likewise.
- **"Has a digit" is not "is a value."** The first merged-row check read equipment tags
  (`MG-30-F5-1003/`) as values and flagged wrapped text in 4664902's correct ruled tables. A
  value is now a number, date or amount with at most a short unit.
- **Some ruled rows hold several records.** 4664902's pressure-test tables put three to five
  test packages, each with its own minimum and maximum, in one ruled row: the line grid is
  exact to the ruling and still pairs no pressure with its package. `table_rows_merged`
  (2,060 of the 2,178 words it flags in this set) says so; splitting such rows at their text
  lines is still to do.
- The words-placed score fell by a few words on two documents where fit improved (4681022,
  4692613): words on a band border. Not yet looked at.

TableFormer v2 with `do_cell_matching=False`, suggested upstream for tables with spanning
cells, is the "v2, its own cells" row below: 22.9% problem words against 3.4% for v1 accurate.

### TableFormer v1 against v2 (bench/tables.py, 2026-09-28)

The same 40 table pages (25 from troubled documents, 15 random), 12,603 text-layer words
inside 45 tables, converted from the original PDF with each variant; every variant saw the
same table areas. Scored by the same conservation check:

| variant | problem words | share | s/page | best on pages |
|---|---:|---:|---:|---:|
| **TableFormer v1 accurate** | **424** | **3.4%** | 1.55 | **34** |
| TableFormer v1 fast (convert's setting) | 528 | 4.2% | 0.87 | 26 |
| TableFormer v2, cell matching | 2,415 | 19.2% | 1.50 | 3 |
| TableFormer v2, its own cells | 2,891 | 22.9% | 1.56 | 3 |
| best variant per page | 336 | 2.7% | | |

- **v2, as shipped, is worse here**: it finds as many tables and more cells (5,528 against
  5,167), then puts words in the wrong cells on dense tables (4681297 p20: 289 problem words
  against 21; 4672628's lab tables: about 250 against 26). It was best on one page of 40, by
  two words.
- **v1 accurate beats v1 fast by a fifth**, at 1.8× the time. The legacy pipeline chose fast
  after measuring *cells dropped*; measured by *words in the wrong cell*, accurate is better.
  Choosing the better of the two per page cuts a further fifth.

## Route by what a page is, not fix what a reader got wrong (2026-10-10)

The oldest 100 documents (GH-3-97 hearing transcripts, 9,341 pages) had 1,697 errors. Three
causes: transcripts' margin line numbers read by TableFormer as two-column tables (half the
errors); a page-wide `code` box over a table taking its words, so every cell read as empty;
and contents pages' dot leaders reported as text outside cells. Fixing each in the checks cut
errors to 489 with no words lost from the export (recall unchanged, 99.9%). But the
transcript rule then flattened three real tables numbered down the margin (4656998 p39,
4647200 p432, 90732 p26) until a line-pitch test was added, and one shape of table can
still pass it.

Each fix was right and each opened the next case. The cause was the route: it knew how a
page's text is stored, not what the page is, so every kind of page went to one reader and
its mistakes were undone afterwards. A random survey of 300 documents across 2002-2026 (see
DESIGN, Page kinds) put transcripts at 1% of pages, where the day's work had gone, and
tables at 23%. **Decide what each page and region is first, route each region to the reader
for its kind, and spend effort by share of pages.** Measure on a random sample before
fixing, not on the documents that happen to come first: the oldest 100 were all one kind.

### Routing by kind, measured on the sample (2026-10-10)

Converting the 300-document sample with and without the pages stage (convert-1c3ac531 against
convert-e3f26a9e): transcript pages 21 errors to 0, contents pages 70 errors and 138 warnings to 0
and 1; every other kind identical; export recall unchanged; 7% faster. What is left is tables:
errors per 100 pages are 3 on text pages and 319 on table pages, 544 on mixed pages. Two
checks reported false errors and were fixed: a table inside an image (OCR, no text layer to
compare against: 4158647, 639 errors to 0) and the export check, which stripped Markdown
formatting from the export but not from the evidence (four of five documents "missing" words
were `CIRSDRU_09_01`, `Thiele*`, `#VALUE!`). One loss is real: OCR'd tables with overlapping
cells, where the Markdown grid shows one cell per position (2813525, 2.7% of its words).
*Correction:* a later look found no hidden cells in 2813525 and its table exports whole; about
160 of its evidence words still do not match the export and the cause is not yet known. Cells
wholly under other cells are now kept beside the table anyway (`table_cells_overlap`).

Two more causes of most of the remaining errors, both measured on the sample: a dense table's
rows ran together in the text-alignment grid, because word boxes overlap the next line and no
gap separates rows (4438220: 990 errors to 1, rows now from evenly spaced lines); and the OCR
vote against a page's own OCR layer, where on 33 of 107 voted pages the two readers disagree
throughout (one of them unreadable: an embedded layer reading "ui. /ff" for "14403kPa", or our
OCR on handwriting). Those 33 pages held 1,291 of 1,399 vote errors; a page agreeing under 80%
is now one warning, not an error per item (4088410: 673 errors to 70).

The vision model's kind, taken on every scanned page, changed 177 pages; of 24 read by eye,
about 8 were right (forms) and 13 wrong (scanned tables and drawings called text). It now
decides only "form"; other disagreements are kept and marked.

### A fresh sample (2026-10-11)

100 more documents drawn at random, 4 a year, none from the first sample
([survey/sample-2026-10-11.csv](survey/sample-2026-10-11.csv)), run as the corpus will be run.
The first sample's fixes held: 99.92% of evidence words in the export, none under 98%; transcript
and contents pages 0 errors. It found four things the first 300 had not:

- **A superscript digit crashed the pages stage.** `"¹".isdigit()` is true and `int("¹")` raises
  (2680246). Digits are now `isdecimal()`.
- **find_tables() changes the page it reads.** On a rotated page with a crop box smaller than its
  media box, PyMuPDF's page then reports the media box (3922740 p2: 612x792 became 1224x792), and
  every box read from it afterwards was misplaced. The table finders read their own copy.
- **A text layer of one-character words is not text.** Type3 fonts whose codes are not their
  letters read "/ 0 1 2 3 4 0 5 6" (2680246, 187 warnings), and tier0 called them sound. A page of
  50+ words of which 70% are one letter or digit (MuPDF's words, not tier0's joined spans) is now
  suspect and goes to OCR: on both samples' 4,469 pages only that document's pages changed.
- **Docling reads text MuPDF does not.** 3922740 p2's table holds text no MuPDF word matches;
  probably content outside the crop box. Not fixed: one page.

### By document type, and triage (2026-10-11)

400 documents, 20 of each of the 20 most common REGDOCS document types
([survey/sample-2026-10-11-types.csv](survey/sample-2026-10-11-types.csv)). No failures and no
issue kind the earlier samples lacked; every type keeps 99.86% or more of its words in the
export. `survey/triage.py` pointed at each of the following, and the cause was then read on the
pages:

- **Table cells kept docling's reading.** TableFormer drops and garbles words in cells
  (862407: "...BLENHEIM BLACK CREEK" for "...BLENHEIM LYING W OF BLACK CREEK"), while every other
  grid takes the text layer's words. Cells now take the words in their boxes, as text items do,
  and a poor grid is a `table_grid_doubtful` warning instead of cell errors. Errors on the 400:
  578 to 147.
- **Markdown repeated spanning cells.** A cell is written into every position it spans:
  556364's tables came out at 2.9 words per evidence word. A spanning cell is now written once,
  except in tables whose cells overlap (docling's OCR of scanned forms), where collapsing spans
  lost words; documents over 1.3 words per evidence word: 21 to 7. Recall fell by 0.1-0.3% on
  three documents, all still above 99%. The export now records `export_word_ratio`.
- **The export check misread links and backslashes:** `[text](url)` left "A8K6T7](https..."
  as one word, and a backslash was dropped on one side only (a drawing's `P:\19731...` path).
  Two documents "under 98%" were both only this.
- **A stray control character became a text item and a warning** (2415559's "\x85").

## Sections: a tree, from the filer's structure first (2026-10-13)

A chunker that slides a window over text loses where it is; one that walks sections keeps it
("the schema carries most of the weight", r/Rag). The DoclingDocument had headings as a flat
list with no levels, and in light mode no headings at all.

- **Bookmarks are the filer's own tree, and the cheapest.** 4034412 (148 pages): 118 sections, 7
  levels, 116 titles found on their pages. 4646669 (Book of Authorities): 13 tabs, 3 found - a
  bookmark names a tab ("TAB 3 - CER Reasons for Decision") the page does not repeat, so the
  section opens at the page's top with the bookmark's text, marked `bookmark_only`. A single
  bookmark (the document's title) is not a tree; two or more are needed.
- **Heading numbers work when they must run in sequence.** 777308 (code of practice): 72
  sections, 4 levels; 3768464: down to 2.3.2.1. Taken alone, a number at the start of a line is
  as often a list, a legend or a note. False sections found on 77 random documents (random-100-a), each now
  rejected: a revision row "0 2010/09/07 ISSUED FOR CONSTRUCTION" (no section 0; "2.0" is 2); a
  slope legend "5 - 10-15% moderate slopes" (a title starts with a word); a drawing's numbered
  notes 1-15 (not on sheets over 17.5 in; a one-level chain needs two pages and at most three a
  page); OCR of scanned sheets "2 B°", "3 C64", "5 力 ℃" (a title has a word of four letters and
  is mostly words). The chain is the longest run where each number can follow the last
  (2.3 -> 2.4, 2.3.1 or 3), at least three long, so one stray number does not start a tree.
- **In light mode a heading sits in the same cluster as the paragraph under it**, so the
  heading line is split out into its own element; the evidence check still finds every word
  named once (77 of 77).
- **Evidence kept means assembly can be redone.** `--reuse-ocr` takes the OCR words a light
  conversion saved instead of running OCR again: 4657424 (149 pages, 64 OCR'd) reassembled in 48 s
  against 188 s, the GPU never loaded, all 41,735 words identical - and 77 sections from its 79
  bookmarks. Sections (and the next assembly change) reach the whole corpus without the GPU.
- *Open:* tagged PDFs' structure trees (H1-H6) as a third source (47% of documents are tagged;
  needs a structure-tree reader); a section tree for documents with neither (title-case lines,
  contents pages); layout's section headers in full mode take their level from their numbers or
  the open section.

## Page boundaries: running headers and footers (2026-10-13)

What crosses a page break decides how text reads and chunks: a running header in the middle of a
paragraph, a table split in two, a section that starts mid-page. `boundaries.py` measures these
from saved evidence (CPU only). First, furniture, over the 274 full-mode sample documents of four
pages or more (`runs/v2/reports/boundaries-furniture.json`):

- **The layout models miss most running headers and footers.** A line in the top or bottom 12%
  of the page whose text (numbers, roman ones too, as `#`) recurs on at least three pages and 30%
  of them: 13,322 such lines in 195 of the 274 documents; the four-model layout vote calls only
  40% of them page_header / page_footer. Of 30 the layout called body, judged by reading: about
  23 are running heads ("trans mountain pipeline ulc attachment #.# august #", "tmep-civ# revision
  # november # page # of #", "transcript mh-#-#", a law firm's "cassels page #", letterheads),
  2-3 are a table's header row repeated on each page (a multi-page table - next), about 4
  unclear. 75% of what the layout does call furniture repeats.
- **So repetition is the rule for furniture, in both modes**, and the only one light mode has
  (no layout models): a band line that repeats is furniture, unless it sits in a table region,
  where it is a repeated header row and the table continues. *Proposed; not yet in the
  assembler* - it changes which words are body, so it goes in with the reassembly of the corpus
  (`--reuse-ocr`) and is measured there.
- **Tables run across pages in one document in six, and the header line says so, not the
  columns.** A table ending in the bottom quarter of a page and one starting in the top quarter
  of the next (`runs/v2/reports/boundaries-tables.json`): 1,073 such pairs in 103 of the 625
  full-mode documents (16%); 3611012 alone has 242. The second repeats the first's header line
  (words alike, not equal: OCR reads "tera com" on one page and "tera comm" on the next) in 669
  pairs, 49 documents. The column count is the weaker sign: in 123 pairs the header repeats but
  the readers chose different grids on each page ([4, 3], [13, 12], one page with no grid at
  all) - each page's table is read on its own. So stitching (*proposed*): join a table to the
  one opening the next page when the header line repeats, then lay the continuation's words on
  the **first page's** columns rather than trusting its own grid; drop the repeated header row
  (it is a page's furniture, not data). Same columns without a repeated header (131 pairs) is
  mixed: continuations (mitigation tables, records of engagement) and separate tables (a
  financial statement's consecutive schedules) - by rows and column positions, to be measured.
- **Pages are not units of meaning: 70% of headings start partway down a page, and about one page
  break in six splits a sentence.** Over 430 full-mode documents of two pages or more
  (`runs/v2/reports/boundaries-flow.json`), furniture (repeated band lines, layout headers and
  footers) and tables/pictures set aside: 12,274 of 17,487 section headings are not the first body
  line on their page. Of 5,903 page breaks, 1,173 (20%) end a page without closing punctuation
  and start the next in lower case; of 15 read, about 12 are a sentence split across the break
  ("...dissolution, reorganization or || similar transaction involving..."; "...the taxpayers' ||
  dollars have been spent...") and 3 are not (a fax header, a running footer the repetition
  rule missed, a fragment) - so roughly 16% of breaks. 136 of the 430 documents have at least
  one. Another 2,283 breaks end without punctuation but go on with a capital: mixed, from real
  continuations into a proper name ("The Consumers' || Gas Company Ltd.") to contents pages,
  headings and lists. So (*proposed*): chunk by section, never by page; where a body text item
  ends open and the next page's first body item goes on in lower case (furniture between them
  removed), make them one item with two provenance entries, one per page - DoclingDocument
  allows an item several `prov`, so the page of every word stays known. A capital after an
  open end needs more than this to decide.
- **Built (2026-10-13): furniture by repetition and sentences joined across breaks**, in both modes,
  before sections (so heading numbers in a running head are not sections). Measured on 20 light
  documents rebuilt from their saved OCR (`--reuse-ocr`, no GPU), evidence intact in all 20:
  - *Furniture*: a band line is furniture when it repeats on at least three pages **and on at
    least half the pages from its first to its last** - not "30% of the document": a filing of
    several documents has each one's header only over its own pages, alternating odd/even
    (4403940, an article from p.43: 26 of 93 pages, every other page of its span). Bare page
    numbers count. Lines are read across the whole page, not inside an element: light-mode
    clusters cut a header differently on each page, so its shape within a cluster never repeated.
    Map sheets' graticule labels and title blocks are caught too - the frame of the sheet, kept
    in the furniture layer, not lost.
  - *Joins*: body text that ends a page open and goes on in lower case on the next, **at the foot
    of one and the head of the next, in the same column** (4415269's two-column submission tables
    were joined across columns), not a list marker ("b) If two...") and not a footnote (a tail in
    smaller type than the page's body). 22 joins in the 20 documents; about 19 are a sentence
    split by the break ("...whether weldments are in || accordance with the maintenance
    project..."); the rest a footnote or a column light mode cannot see. A wrong join loses
    nothing: each part keeps its own page and box (`prov` per page).
- **Built: tables stitched across pages** (full mode; light mode reads no tables). A table at a
  page's foot (nothing below it) and the next page's first table at its head (nothing above it)
  whose first rows say the same thing (words alike) are one table: the continuation's cells go
  onto the **first page's columns** by where their middles fall, its rows are appended, its
  repeated header row becomes that page's furniture, and the table carries a `prov` per page; a
  table running on over several pages is carried page to page. Tested on the CPU on the 103
  full-mode documents with a table at a page boundary, rebuilt from their saved words, regions
  and chosen grids: 186 stitches, evidence intact in all 103. 995751's weed survey is one table
  of 33 rows over pages 42-46, its six columns aligned at every break (KP 1.66 -> 2.50 -> 3.33
  ...); 940189's condition table runs from item 6 on p.1 to item 7 on p.2, eight columns. Stricter
  than the measurement (669 pairs with a repeated header line): nothing may sit between the
  table and the page edge, and the header comes from the grid's first row. Full documents are
  read again only with the GPU (no `--reuse-ocr` for full mode), so stitching reaches them on
  their next full conversion.
- **Lines the layout calls furniture that do not repeat** are first-page-only letterhead and
  footers ("www.neb-one.gc.ca"), and roman page numbers before they were read as numbers.

## Lessons, with evidence

### Memory and time

- **A worker's memory grows with what docling holds at once, and a single page can be enough.**
  Converting whole filings in one call, three of the largest (313, 628 and 986 pages) together
  exhausted RAM, froze WSL and took the machine down. One page per call fixed that: the same
  three together ran at 3-4 GB per worker with 34 GB free.
- **The page a worker dies on is not always the cause.** 4656998 "reached 12 GB on page 1",
  and page 1 was taken to be one of its 34 × 22 in drawing sheets. It is a letter-size page
  with 241 words. docling-parse 7.22 hangs *loading the file*, before any page: the
  certification signature's `/Reference` points at the catalog, and the loader walks the
  whole object graph from there. Found by timing the bare loader, then removing parts of the
  file until it loaded (0.03 s without the `/Reference`). docling now reads such files as the
  original bytes plus an incremental update that cuts it. Check the page before blaming it.
- **docling's `document_timeout` does not stop a step in the middle.** With a 300 s limit,
  4656998 ran for over 9 minutes before the memory cap stopped it (in the loader, which the
  timeout does not cover at all), and page 11 of 4681376 ran an hour. A timeout that is
  checked only between steps cannot bound one long step.
- **Watch memory during a document, not just after it.** A per-worker cap checked after each
  document let one worker grow to 23.8 GB mid-document. The cap now applies mid-document
  (`--kill-worker-ram-gb`), and each page's start and finish, with the worker's memory, is
  logged, so a stopped worker is reported with the page it was on.
- **A fixed time limit per document stops the long ones, so the limit scales with pages.**
  REGDOCS documents run from 1 page to thousands: in the 5,000-document survey the median is 3
  pages, the 99th percentile 171, and the longest are 7,280 (2498373), 716 and 604 pages; 4 of
  5,000 have 500 or more, but they hold 13% of the pages. In the corpus light pass (2026-10-13)
  the slowest documents took 2.2-2.8 s a page (4672628: 691 pages, 31 minutes; 4664989: 450
  pages, 21 minutes) - mostly scans that need OCR - against 0.2-0.5 s for digital ones. 4724234
  (171 pages, 125 of them A0 drawing sheets and 3 at 66 x 47 in, Bluebeam) hit the one-hour limit
  while the disk was shared with the scout, then finished in 22 minutes on retry (7.7 s a page).
  At those rates a 900-page scanned document needs about 40 minutes in light mode and far more in
  full mode, and the 7,280-page one would need hours even if digital. The limit is now
  `max(--timeout, --timeout-per-page x pages)`: an hour at least, 20 s a page (900 pages: 5
  hours). Full mode runs layout, table and figure models on every page and is far slower per page;
  raise `--timeout-per-page` for full runs of long documents rather than the floor. Oversize
  sheets cost more than their page count says (pixels, not pages): 4724234's 7.7 s a page is
  three times a scanned letter page's.
- **The hard documents are the same documents, whatever the tool.** cer-regdocs2's Azure/Docling
  comparison (`reports/azure-docling/REPORT.md`, 8,213 documents, August 2026) quarantined 10
  documents (8 unique files) after repeated 1,200 s timeouts or segmentation faults: 4647200 (986
  pages, 44 MiB), 4672628 (691), 4664989 (450), 4646680, 4648258, 4657424/4657571/4656998 (one
  file, 149 pages), 4659381, 4681578. Azure finished all of them by splitting the large ones into
  300-page ranges (4647200 in 284 s). In the corpus light pass, 9 of the 10 finished (4672628 in
  31 minutes, 4664989 in 21), and 4647200 hit the fixed hour again. cer-regdocs4 had tuned on
  4647200 and measured it at 1,574-2,119 s for a full docling run (1.6-2.1 s a page) - 315 of its
  986 pages have a text layer that decodes to nonsense, so they all need OCR. What makes these
  documents slow is size in bytes and pages needing OCR, not page count alone: the 285- and
  297-page ones that crashed docling took 22 s here (all text layer). 4647200 is queued in
  `runs/v2/redo-timeouts.txt` for the scaled limit (986 pages: 5.5 hours at most).
- **A document that is slow is not a document that is stuck: limit time without progress.** Three
  more documents hit the hour in the pass (4646037, 4646096, 4646565: 108-208 pages, 20-43 MiB,
  mostly 11 x 17 in page images from one filing). 4646037 alone, profiled: 635 s, 90% OCR, 364 s
  of it waiting on the GPU (`.cpu()`), and RapidOCR's text-line angle classifier 224 s. Newest
  first puts a filing's scanned documents side by side, so several workers OCR at once on one
  GPU and each runs many times slower - an hour on a 10-minute document. A page-scaled limit
  does not catch that (108 pages x 20 s is under the hour); it is not the document, it is the
  company it keeps. Workers now report each page (and the text-layer pre-scan) to the parent, and
  a document is stopped when **no page has finished for `--stall` seconds** (900), reported with
  where it was ("no progress for 900s (at page 6)"); `max(--timeout, --timeout-per-page x pages)`
  (60 s a page) stays as a generous cap. Azure's 300-page ranges are the other route - each range a
  unit of work, retried on its own - not needed while pages report progress.
- **On WSL a full GPU does not fail, it crawls - and that looked like slow documents.** With the
  stall limit on, four ordinary documents stopped with no page finished in 15 minutes, one of them
  a 4-page, 0.2 MiB letter (4595648, at page 1). The card was full: 23.3 of 24.5 GB, up from 14.2
  GB with the same 10 workers an hour earlier. PyTorch keeps the memory the largest page needed,
  and the workers emptied the cache only between documents, so each grew to ~2.3 GB; past the
  card, WSL's driver spills to system memory instead of raising out-of-memory, and every worker
  slows together. `release_gpu()` now hands the cache back after any OCR call that leaves a
  worker over 1 GB. After the restart: peak 19.7 GB over 15 minutes, 0 stalls, ~23 pages a second
  (from 6.7). Some of what was put down to GPU contention above was probably this. Watch the
  card's memory, not only its utilisation: 100% busy can mean thrashing.
- **Some pages never finish, and that costs the page, not the document.** Eleven documents stalled
  in one hour, ten from one filing (4553641-4553652), two of them while reading the text layer -
  so not the GPU. 4537539 page 130, an ArcGIS map (69 nested form XObjects, 644 KB of drawing
  operators, ESRI symbol fonts): MuPDF 1.28.2 neither extracts its words nor renders it at 36 dpi
  within minutes, from the Windows drive or from Linux's own disk; the other 169 pages read in
  well under a second each. A call into MuPDF cannot be stopped from inside, so the parent does
  it: on a stall it knows where the worker was ("text layer 129": stuck on page 130), and the
  document goes back in the queue without that page, recorded as `page_skipped` (an error, so it
  is listed as missing); after three such pages the document fails. The stall limit is 300 s:
  the slowest real page seen took under 90 s with ten workers sharing the GPU, and a limit of
  20 s (tried) skipped pages that were only slow OCR (117 and 125 read in 0.05 s alone).
  **A crash while drawing a page costs a renderer, not the page.** 4473998 (65 slides) crashed the
  worker natively ("free(): double free"), at a different page each time once the last was
  skipped (41, 45, 55). Isolated: its text reads fine; MuPDF 1.28.2's *rendering* of pages with a
  Display P3 JPEG under 30% transparency corrupts the heap, at 72 dpi as at 200, ICC on or off;
  the JPEG alone decodes. pdfium draws all 65 pages in 1.8 s. A page whose conversion crashes or
  hangs (its text already read in the pre-scan) is now drawn by pdfium on the retry and keeps its
  text and its OCR (`rendered_by_pdfium`); a hang while reading text, or pdfium failing too,
  leaves the page out. 4473998: six retries, then 65 of 65 pages, 6 drawn by pdfium.
  **Hanging text too goes to pdfium, and a retry must move on.** 4449303 (68 pages, Microsoft Word)
  hangs MuPDF's text extraction on nine pages with letter-spaced headings ("P A T C H I N G A S S O C I
  A T E S"); pdfium reads their text and draws them in under 0.1 s. Skipping them lost the pages,
  and worse, the pass retried the same page six times: a skipped page reported no progress, so
  "text layer 22" kept naming page 23 while the worker hung on 24. Now skipped pages report
  progress, the stuck page is the next one not already handled, a page is never retried the same
  way twice, and any page MuPDF did not finish is read entirely by pdfium (`pdfium_words`, its
  render, facts without interpreting the content); only a page pdfium fails too is left out.
  4449303: 68 of 68 pages, 9 read by pdfium. *Open:* each hang costs the stall limit (here 9 x
  300 s); a document with several could go to pdfium whole after the third.
  **MuPDF can also refuse a page outright** ("layer/clip nesting too deep", 3718735 p.115): an
  exception in the text pre-scan failed the whole document. The pre-scan now hands such a page
  to pdfium too (`text_read_by_pdfium`): 200 of 200 pages.
  **GPU memory, again:** at 1 GB kept per worker, OCR-heavy stretches still reached 21.9-22.7 GB
  and 3 pages a second; workers now keep 256 MB and the pass runs 8 of them.
  Reading from the Windows drive is ~28 times slower per page than from Linux's disk (5 pages:
  0.56 s against 0.02 s) - slow, not stuck.
  *Open:* the angle classifier (a third of OCR time here) could be skipped on pages whose
  orientation is already known; measure what it costs in words before turning it off.
- **A worker that crashed may still look alive; replace it on any failure, not on `is_alive()`.**
  Six hours after the reaping fix the pass stopped again the same way: 4473998's worker broke its
  pipe while exiting, `is_alive()` was still true, it was handed the next document and `send`
  raised `BrokenPipeError`. Now any crash, stall or timeout kills, joins and replaces the
  worker, and a failed hand-off puts the document back and replaces the worker again.
- **A killed worker is not dead until it is reaped.** The pass stopped after 1,049 documents:
  `kill()` on the timed-out worker left `is_alive()` true for a moment, the worker was handed the
  next document, and reading its pipe raised `ConnectionResetError`, which the crash handler (it
  caught `EOFError`) did not. The parent now joins a killed worker and treats any `OSError` on
  the pipe as that worker crashing: one document lost, not the run.
- **The GPU is shared with whatever else runs on Windows.** At 07:00 on 2026-10-17 a game took
  about 5 GB of the card; the pass reached 22.2 of 24.5 GB, OCR went from 1.4 to 9.9 s a page,
  and 43 documents had pages stall and go to pdfium or be left out - none in the 74,800 before.
  WSL shows only its own processes (`nvidia-smi` listed one, memory N/A); Windows'
  `nvidia-smi.exe` named the game. Stopped and relaunched once it closed; the 43 were redone.
  Watch seconds a page by reader over time: a step change with no change in the documents is the
  machine, not the PDFs.
  **Now built in:** before a page, at most every 30 s, a worker reads the card's memory
  (`nvidia-smi`, every process's); above 88% it hands back its own cache, and if the card is still
  crowded it waits, reporting progress so the stall limit does not stop the document, for up to
  30 minutes. The page records `gpu_wait` with the seconds waited.
- **The legacy pipeline did not get through these documents either.** It never reached 4656998 or
  4657424 (the same file), and stopped 4681376 at page 10 of 24 with no progress for 30 minutes, the same place
  convert stalls.

### The checker's own cost and its false alarms

- **Profile before blaming the converter.** 4656888 timed out at an hour; profiling showed 44%
  of the time in convert's own word-in-box loops, not docling. A spatial index made the checks
  take seconds, with identical results.
- **Then check what is left.** Even with fast checks, 4656888 took 68 minutes: the review PDF
  was drawing 61,882 annotations. It now draws at most 50 per kind per page, and one note with
  the count of the rest.
- **A fixed margin fails on tiny text.** Those 61,882 issues were false: at 1.5 pt text with
  lines 2.2 pt apart, a fixed 1.5 pt margin took in the next line. The margin now scales with
  the box, which took 4656888 to zero while leaving four other documents unchanged. Comparing
  characters instead of words was tried and rejected: a cell box narrower than its printed word
  gave "ovide" for "Provide:".
- **A checker is code with bugs; test it on documents whose answer is known.** In the tool
  comparison, removing HTML tags with `<[^>]+>` deleted lab values written as `\< 0.0050 ...`
  up to the next `>`, and scored Tika at 28% instead of 92% on one filing.

### docling's behaviour

- **`DoclingDocument.concatenate` renumbers pages** to run on from the previous document, so
  pages converted in ranges with gaps took the wrong numbers. The coverage check caught it:
  1,044 false "not extracted" errors on one filing. Pages are now merged keeping their numbers,
  and every page's number and size is checked against the PDF.
- **A copy of a page is not the page.** A one-page copy was 25% faster to convert, but docling
  read a filled form's copy differently (201 items and 88 errors, against 186 and 6), though
  MuPDF found the copy identical. docling reads the filed PDF itself.
- **Some pages docling cannot parse at all.** In a batch this shows as "pipeline terminated
  early"; converted alone, the page says "Page 20 failed to parse." MuPDF reads it without
  trouble, so such pages take their text from MuPDF, flagged.
- **docling's default Markdown export leaves out text its document holds**: text under pictures
  and page headers and footers. On 4657006 the default export had 139 of 1,572 words;
  `traverse_pictures=True` and every content layer brings them all back.
- **Even that export drops a table's footnotes.** docling-core 2.99 skips a footnote in the
  body because the table holding it will write it, and the Markdown table serializer never
  does. 22 of 48 signed filings (mostly CER reporting forms) fell under 98% of words, one to
  60%. convert's export appends them after their table. Only the export check caught it:
  coverage was 100%, because the words were in docling's document, just not in its Markdown.
- **TableFormer logs outside docling's logger tree** (as `MatchingPostProcessor`), so its
  dropped-cell warnings are only heard at the root logger.

### Tools compared (bench, ten baseline documents)

- **Tika** finished all ten and recovered the most text, but found 1 table in ten documents
  and gives no boxes.
- **docling**, scored on everything its document holds, is within a few points of Tika wherever
  it finishes (99% on 4647200, 95% on 4672628), with tables and boxes; it failed on the four
  heaviest.
- **opendataloader** has boxes and tables and is about 7× faster than docling, but ran out of
  memory at 8 GB on three documents, found nothing on 4657006, and scored 60% on two table-heavy
  ones.
- **anydoc** refuses any document with a page that needs OCR.
- **4657006 defeats every tool** (54% at best, docling). Something in how it draws its labels.

### Process

- **When the GPU dies, the run does not stop: it fails every document in about a second.** At about
  04:12 on 2026-11-07 the card failed (`AcceleratorError: CUDA error: unknown error`, first seen as
  page_failed on 2462514); the run then marked 2,372 documents in a row FAILED with no reason, until
  the machine went down at 05:09. Nothing was lost: failed documents write no finished ledger, and the
  relaunch after the reboot reconverted them (2462513 and on, all ok). *Fix between runs:* stop the
  run after about 20 consecutive failures, and carry the exception's text into the FAILED line
  instead of `None`.
- **Change code only between runs.** A worker replaced mid-run re-imports the file; an edit
  made during a run gave one worker different code than the run recorded, and that run was
  discarded.
- **Test on the hard cases, not the corpus.** Full runs took hours and taught one failure at a
  time; the ten baseline documents take minutes and cover every failure seen so far.
- **A clean exit is not success.** tier 0 and the first conversion runs finished "ok" while
  hiding a renumbering bug, a scoring bug and an export gap; each was found by a check that
  compared two independent readings.
