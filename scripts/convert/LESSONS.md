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

- **Change code only between runs.** A worker replaced mid-run re-imports the file; an edit
  made during a run gave one worker different code than the run recorded, and that run was
  discarded.
- **Test on the hard cases, not the corpus.** Full runs took hours and taught one failure at a
  time; the ten baseline documents take minutes and cover every failure seen so far.
- **A clean exit is not success.** tier 0 and the first conversion runs finished "ok" while
  hiding a renumbering bug, a scoring bug and an export gap; each was found by a check that
  compared two independent readings.
