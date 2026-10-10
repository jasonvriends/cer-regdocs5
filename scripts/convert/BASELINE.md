# Baseline: the 10 hardest documents

Ten filings from `source/`, each chosen because it breaks something different.
Together they cover every failure seen in the first full conversion run. Use
them as the test set: a change to the pipeline should make these numbers better
without making the rest worse.

Numbers are from conversion run `convert-7f154874` (docling 2.130.0, one page
per call, 2 workers, 2026-09-27) over tier 0 run `tier0-197b06ca`. Across all
8,206 documents that run extracted **99.49%** of text-layer words on the pages
it converted.

## Summary

| # | document | pages | what breaks | baseline |
|---|---|---:|---|---|
| 1 | [4656998](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4656998) | 149 | memory: one worker grew past 24 GB | no output (stopped twice) |
| 2 | [4648296](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4648296) | 3 | time: over an hour on 3 pages | no output (timed out) |
| 3 | [4656888](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4656888) | 8 | time: dense spreadsheet pages | no output (timed out) |
| 4 | [4681376](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4681376) | 24 | time: drawing sheets; legacy stalled here too | no output (timed out) |
| 5 | [4646070](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4646070) | 273 | docling cannot parse 2 pages | partial; 2 pages from the fallback reader |
| 6 | [4657006](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4657006) | 11 | text docling never extracts | 55.5% of words extracted |
| 7 | [4672628](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4672628) | 691 | table grids | 34,885 cell errors |
| 8 | [4664902](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4664902) | 3 | table grids on engineering drawings | 221 errors per page |
| 9 | [4647200](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4647200) | 986 | broken text layer (mojibake) | 315 pages left for OCR |
| 10 | [4710294](https://apps.cer-rec.gc.ca/REGDOCS/File/Download/4710294) | 463 | scanned, no text layer | 399 pages left for OCR |

## After the safeguards (run `convert-1ac4b3f9`, 2026-09-28)

The same ten, converted with a per-page time limit, a mid-document memory cap, per-page
progress files and poison pages (a page a previous attempt died on goes to the text-layer
reader), and the checker fixes. 84 minutes for the ten, most of it waiting out stuck pages.

| # | document | before | now |
|---|---|---|---|
| 1 | 4656998 | no output | **failed**: pages 1 and 2 each exhausted memory; only two attempts |
| 2 | 4648296 | no output | **ok**, 17 s; page 3 from the text layer |
| 3 | 4656888 | no output | **ok**, 21 s, 0 errors |
| 4 | 4681376 | no output | **ok**, 10 s; page 11 from the text layer |
| 5 | 4646070 | partial | partial, the same |
| 6 | 4657006 | 55.5% of words | 55.5% |
| 7 | 4672628 | 34,885 cell errors | 38,608 cell errors |
| 8 | 4664902 | 221 errors per page | the same |
| 9 | 4647200 | 315 pages pending | the same |
| 10 | 4710294 | 399 pages pending | 404 pending |

Finding heavy pages by failing on them costs an hour per page and cannot cope with a document
that has many; routing them by tier 0 before conversion is the next step.

## After the text-layer fill and the signature fix (run `convert-73c152b4`, 2026-09-28)

Words no docling item claims are now kept as text items from the text layer, every
text-layer word is recorded in an evidence file, and the Markdown export is checked
against it. Pages tier 0 marks as oversized and dense skip docling. And docling reads
signed filings through a copy with the signature's `/Reference` cut (see document 1).
Documents 1 and 6, and the regression check:

| # | document | before | now |
|---|---|---|---|
| 1 | 4656998 | **failed** | **ok**, 80 s: 100 of 149 pages converted, 49 scans pending OCR; pages 53–54 from the text layer (structure budget); 98.6% of words in the export |
| 6 | 4657006 | 55.5% of words | 99.3% of words in the export (layout coverage still 55.5%; 276 text items added from the text layer) |
| – | 4590700 | 83 errors, 136 warnings | 79 errors, 140 warnings: 4 missing-text errors became warnings as the text is now kept |

4657424 and 4657571 are byte-identical to 4656998 and convert the same way.

## All ten again (run `convert-f9d519d4`, 2026-09-28, 2 workers)

**16 minutes** for the ten (84 before); none failed.

| # | document | after the safeguards | now |
|---|---|---|---|
| 1 | 4656998 | failed | **ok**, 81 s; 100/149 converted, 49 scans pending |
| 2 | 4648296 | ok, 17 s; page 3 from the text layer | ok, **1 s**: all 3 sheets skip docling (structure budget); 12 → 22 errors, 4,477 → 1,868 warnings |
| 3 | 4656888 | ok, 21 s, 0 errors | the same (24 s) |
| 4 | 4681376 | ok, 10 s; page 11 from the text layer | ok, 4 s; 4 sheets skip docling; 5 → 11 errors (one is the export check) |
| 5 | 4646070 | partial | partial, the same (59 → 60 errors: the export check) |
| 6 | 4657006 | 55.5% of words | **99.3%** of words in the export |
| 7 | 4672628 | 38,608 cell errors | 34,123 cell errors; 6,676 → 6,127 words in no cell |
| 8 | 4664902 | 663 errors | 664 (the export check) |
| 9 | 4647200 | 329 pages pending | the same |
| 10 | 4710294 | 404 pending | the same |

The export check (`export_missing_text`, at least 98% of text-layer words in the
Markdown) fails on five: 4664902 91.5%, 4710294 91.1%, 4672628 96.8%, 4681376 96.8%,
4646070 97.9%. Not lost text but the table problem seen from the export: in 4664902
"Hydro" is inside the tables 82 times in the text layer and in none of docling's 1,260
cells. Those words are already counted as cell errors.

In this run the review PDF failed on those five: the export issue has no page, and
the annotator took every issue to have one. Fixed (a document-wide issue is a note on
page 1) and checked on four of them in run `convert-fc5d0920`.

## Check on ordinary documents (run `convert-ab703af3`, 2026-09-28)

50 documents drawn at random (seed 20260928) from the 8,201 that converted `ok` in
`convert-7f154874`, and the 48 other filings with a certification signature (`/Perms`):

| | documents | status changed | pages converted changed | errors | warnings | export under 98% |
|---|---:|---:|---:|---:|---:|---:|
| random | 50 | 0 | 0 | 137 → 119 | 105 → 128 | 5 |
| signed | 48 | 0 | 0 | 144 → 146 | 132 → 136 | 6 |

None of the 48 signed filings needed the copy: their signatures have no `/Reference`.
Their first run showed 22 of 48 under 98%: docling's Markdown export drops footnotes
that belong to a table (a CER reporting form lost 40% of its words). The export now
writes them after their table; documents without such footnotes export byte for byte
as before. The 11 left under 98% are docling's text differing from the text layer
(letter-spaced "F I L I N G", "10³m³" as "103m3", joined e-mail addresses), which the
text checks already count.

## TableFormer accurate instead of fast (run `convert-0f81d371`, 2026-09-28)

Tried because `bench/tables.py` found accurate a fifth better on 40 table pages. On the
ten, against `convert-f9d519d4` (the same code with fast):

| document | cell disagreements | words in no cell | errors | export |
|---|---|---|---|---|
| 4656998 | 88 → 48 | 106 → 21 | 103 → 38 | 98.6% → 98.7% |
| 4664902 | 810 → 513 | 31 → 116 | 664 → 475 | 91.5% → 92.0% |
| 4646070 | 19 → 8 | 266 → 521 | 60 → 65 | the same |
| 4672628 | 34,123 → 18,204 | 8,653 → 39,920 | 37,001 → 37,921 | **96.8% → 87.3%** |
| 4647200 | 11,715 → 11,512 | 2,211 → 2,201 | 10,514 → 10,616 | 99.0% → 99.3% |
| all ten | 46,783 → 30,306 | 11,318 → 42,840 | 48,499 → 49,264 | 30 → 44 minutes |

On 4672628, accurate made a third fewer cells (78,395 → 52,287) holding 28% less text
(909k → 659k characters): words inside a merged cell's box that are not in its text. The
cell check counts one issue per cell, so fewer cells looked like fewer errors; only the
export check saw ~20,000 words go. **Kept fast.** Neither mode is better everywhere, which
is the case for choosing per table (plan step 4), scored by words, not by issues.

## Table word score (run `convert-c7896018`, 2026-09-28, TableFormer fast)

Every table now says where its text-layer words went: **placed** (in its cell's text),
**wrong cell** (only another cell has it), **no cell** (no cell box holds it; kept beside
the table) or **lost** (a cell box holds it, no cell's text does; new error
`table_text_lost`). This is the measure for any table change from here on; the per-cell
issue counts rewarded accurate mode for merging cells.

| document | table words | placed | wrong cell | no cell | lost |
|---|---:|---:|---:|---:|---:|
| 4656998 | 9,480 | 98.2% | 56 | 104 | 11 |
| 4681376 | 401 | 93.5% | 0 | 0 | 26 |
| 4646070 | 819 | 94.6% | 0 | 13 | 31 |
| 4672628 | 149,465 | 78.2% | 20,985 | 6,391 | 5,232 |
| 4664902 | 6,129 | 75.9% | 944 | 31 | 501 |
| 4647200 | 180,450 | 94.8% | 7,171 | 2,105 | 177 |
| 4710294 | 4,045 | 97.8% | 34 | 50 | 4 |
| **all ten** | **350,389** | **87.6%** | **29,190** | **8,694** | **5,982** |

Scored offline on the saved accurate run, the same measure puts accurate ahead on 4656998
(99.4%), 4664902 (82.9%), 4646070 (98.0%) and 4710294 (98.1%), and far behind on 4672628
(59.2%, 23,168 lost): the right answer is per table.

## Tables chosen per table (runs `convert-42ebc408`, `convert-99ebbc9d`, 2026-09-29)

A page whose tables do not all place 99% of their words in fast mode is read again in
accurate mode; each table keeps the grid that places more words (ties keep fast). Every
choice, with both scores, is in the record (`table_choices`).

| | fast only | both, every table page | both, below 99% only |
|---|---:|---:|---:|
| table words placed | 87.6% | 89.6% | **89.5%** |
| lost | 5,982 | 2,582 | 2,585 |
| wrong cell | 29,190 | 26,001 | 26,041 |
| no cell | 8,694 | 8,197 | 8,240 |
| the ten, wall time | 16 min | 45 min | **31 min** |

| document | fast only | chosen (below 99%) | tables kept fast / accurate |
|---|---|---|---|
| 4656998 | 98.2% | 99.6% | 80 / 11 |
| 4646070 | 94.6%, 31 lost | 97.9%, 1 lost | 9 / 2 |
| 4672628 | 78.2%, 5,232 lost | 80.7%, 1,916 lost; export 96.8% → 98.2% | 136 / 61 |
| 4664902 | 75.9% | 82.9% | 0 / 2 |
| 4647200 | 94.8% | 96.2% | 485 / 106 |
| 4710294 | 97.8% | 98.8% | 28 / 1 |

What is left is mostly 4672628: 20,665 words in the wrong cell on ruled lab tables that
neither TableFormer mode reads well. That is the case for the line-grid reading next.

## Ruling lines as a third reading (run `convert-871e4fb6`, 2026-09-29)

After fast or accurate is chosen, the table's grid from its ruling lines (PyMuPDF
`find_tables`, lines both ways; cell text from the text-layer words inside) replaces it when
exactly one ruled table fills the box, the grid has at least 80% of TableFormer's rows and
columns, and it fits the words better. **Fit** (`table_structure_score`) counts words crossing
a cell border, in no cell, sharing a cell with a word far to their side (columns merged) and
parted from a neighbour at word spacing (split). The word score cannot judge a line grid:
its cell text is the words in its boxes, so every word is "placed".

| document | errors before → now | export | tables fast / accurate / lines |
|---|---|---|---|
| 4664902 | 477 → **0** | 92.0% → **99.8%** | 0 / 0 / 2 |
| 4647200 | 9,065 → **3,625** | 99.4% → 99.6% | 375 / 56 / 160 |
| 4672628 | 38,583 → 37,133 | 98.2% → 98.7% | 131 / 55 / 11 |
| 4656998 | 39 → 18 | 98.6% → 98.7% | 75 / 6 / 10 |
| 4710294 | 120 → 102 | 91.1% → 92.9% | 23 / 0 / 6 |
| **all ten** | **48,386 → 40,980** | | 32 minutes, as before |

Fit, all ten: 84,451 words badly placed, 63,280 of them in 4672628.

Checked by eye, drawing each grid on the page: on 4664902's ruled drawing tables the line
grid is exact, where TableFormer runs columns together. On 4672628's lab tables, ruled only
between blocks, the line grid gives 11 × 4 cells for a 38 × 28 table; the 80% rule keeps
TableFormer there. A grid from text alignment (columns from gaps) scored well on those
tables, but the picture showed its rows run together and words cut ("Backgrou | nd"): the
fit measure cannot tell rows run together from wrapped text, so it is not used.

Caveat: on a line-grid table the cell checks (`cell_text_disagrees`, `table_text_lost`)
agree by construction; its errors fall to zero because the text is the text layer's. Fit is
the check that still means something there.

On the 98 ordinary and signed documents (same run signature, 2026-09-29), against
`convert-ab703af3`: no status or page changes; errors 265 → 185, warnings 264 → 198; export
up on 13, down by under 0.5 points on 3; 18% more time. Tables kept: 344 fast, 22 accurate,
37 lines. The two with new error kinds are better readings: 4657455 p3 took accurate (435
of 459 words placed against 401), whose cell text differs from the text layer more often;
4205309 took a line grid (7 badly placed words against 141 straddling in fast), which leaves
a heading inside the table's box but outside its rules in no cell, kept beside it.

4672628's lab tables are the gap left: partly ruled, TableFormer's cells offset from the
printed columns, and no reading tried so far fits them.

## Item text from the text layer (run `convert-d8203050`, 2026-09-29)

docling keeps the layout; each text item's text is now the text-layer words in its box,
in MuPDF's order (docling's reading kept in `orig`). A word goes to one item: where boxes
overlap, to the item whose docling reading has it, else the smallest. `text_disagrees` is
now `info`, since the output no longer uses docling's reading. Against `convert-871e4fb6`:

| | the ten | 98 ordinary and signed |
|---|---|---|
| export up / down | 9 / 0 | 41 / 0 |
| export under 98% | 3 → **0** | 10 → **5** |
| errors | 40,980 → 40,210 | 185 → 105 |
| warnings | 7,673 → 4,023 | 198 → 123 |
| words in the export not on the page | 4,705 → 2,419 | 1,133 → 700 |
| words repeated beyond the page's count | 208,744 → 209,102 | 11,214 → 11,216 |

Every one of the ten now reaches at least 98.0% of its words in the export (4681376 lowest,
98.0%; 4710294 92.9% → 99.8%; 4555638, letter-spaced, 84.9% → 100%). Time: +13% on the ten,
none on the sample.

The first version gave each word to the smallest box. Where boxes overlap, that took words
from the item docling had given them to; the emptied item kept docling's text, and the words
appeared twice (4656888, 1.5 pt text: 20,592 repeated words). Preferring docling's own
assignment fixed it: items left with docling's text fell from 15,454 to 4 there. What is
left keeping docling's text (308 in 4647200, 367 in 4681376) holds no words of its own.

## OCR, alignment grids and redactions (run `convert-42241a3e`, 2026-09-30)

All 113 test documents (the ten, 5 scans, the 98 ordinary and signed), 2 workers, 71 minutes.

- **OCR.** Scans, broken text layers and pages with no text layer are read by RapidOCR
  (PP-OCRv6, GPU) through docling's pdfium backend: docling-parse rendered 4710294's scans as
  noise. A scan's own OCR layer votes on ours (`ocr_disagrees_with_hidden_layer`). Pages
  converted: 3,272 of 3,287 (the ten alone: 2,462 → 2,622 of 2,631; 4710294 59 → 463,
  4647200 657 → 986).
- **Alignment grids.** Tables ruled only between blocks take columns from text alignment and
  rows from the gaps between lines, when that places under half as many words badly. Tables
  kept: 652 fast, 70 accurate, 226 lines, 379 aligned. Known limit: a header wrapped onto two
  lines becomes two rows.
- **Redactions.** Words under a dark box whose middle renders flat are left out of every output
  (4710294: 11,246 words of blacked-out invoice descriptions were in its text layer and in the
  export). Of 7,532 redacted four-word sequences, none distinctive reached the export.

Against cer-regdocs2 (Azure prebuilt-layout), `scripts/convert/compare_legacy.py`, same yardsticks:

| | convert | cer-regdocs2 |
|---|---:|---:|
| visible words kept, 1,993 ordinary digital pages | **99.9%** | 84.2% |
| words not on the page (misread, merged, invented) | **0.2%** | 1.7% |
| visible words kept, 166 drawing pages | **100.0%** | 90.7% |
| words not on the page, drawings | **0.6%** | 10.7% |
| table words badly placed (fit), per 1,000 | **47.5** | 178.7 |
| table words in no cell's text though inside a cell (lost) | **64** | 84,908 |
| scan pages with text (1,095) | 1,086 | 1,086 |
| agreement with a scan's own OCR layer | 75% | 40% |

## The documents

### 1. 4656998: a certified signature that sends docling-parse through the whole file

**Cause, found 2026-09-28:** not the drawing sheets. docling-parse 7.22 hangs *loading*
the file, before any page: the certification signature's `/Reference` has `/Data 1 0 R`,
the catalog, and the loader follows it through all 38,194 objects (the structure tree
included), growing without end. It stopped on pages 1 and 2, letter-size pages with 241
words, because those were the first pages asked for. Removing the form, the structure
tree or just the `/Reference` makes it load in 0.03 s. Of 51 certified filings in tier 0,
three hang, all this same file. The description below is what was believed before.


149 pages, 42 MB, including 16 sheets of 34 × 22 in (2448 × 1584 pt), with up
to 16,279 vector paths and 1,137 images per page. A worker converting it grew
to 23.8 GB and kept climbing about 0.5 GB a minute, with 10 GB left on the
machine, until it was stopped by hand; the retry was stopped again. **4657424
and 4657571** look like the same drawing package filed again: identical sheet
counts and path maxima, and the same outcome at 12 GB.

- **Shows:** memory that grows within one page, not across pages.
- **Better means:** it finishes, and stays under 8 GB per worker. Pages docling
  cannot handle within the limits come from the fallback reader with a warning.

### 2. 4648296: three huge table sheets

Only 3 pages, but each is a 34 × 22 in sheet holding one ruled table of about
3,300 words. TableFormer was still working after 3,600 s.

- **Shows:** time per page with no upper bound.
- **Better means:** it finishes in minutes; a page over the per-page limit is
  marked `page_from_fallback_reader` saying so, rather than holding a worker for
  an hour.

### 3. 4656888: dense spreadsheet printouts

8 letter-size pages, each with about 60,000 visible characters in about 1,900
"words", with 219 horizontal and 55 vertical ruling lines: one enormous table
per page. It timed out at an hour. Profiling showed the time was in **our
checks**, not docling. Matching every word against every one of about 12,000
cells took 44% of the run. A spatial index now makes those checks give the same
results in a few seconds (verified identical on five documents).

- **Shows:** the checker's own cost on dense tables.
- **Better means:** checks take seconds; whatever docling makes of the grid is
  then measured like any other table.

### 4. 4681376: drawing sheets with tens of thousands of ruling lines

24 pages. Pages 11–13 are 34 × 22 in sheets with 25,000–30,000 vector paths and
nearly 5,000 vertical rules each. It timed out at an hour. The legacy pipeline
stalled on this same document at page 10 of 24, right before these sheets.

- **Shows:** a failure both pipelines share, so not something one of them introduced.
- **Better means:** it finishes; the three sheets are converted or clearly marked.

### 5. 4646070: pages docling cannot parse

273 pages. Pages 20 and 51 carry only 3 and 8 words. In a batch, docling
reports "pipeline terminated early"; converted alone, it reports "Page 20
failed to parse". MuPDF reads both pages without trouble.
**4642962** (33 pages) and **4684173** (6 pages) fail the same way.

- **Shows:** the fallback path: nothing is lost, and each such page is marked.
- **Baseline:** status `partial`; 14 and 5 text blocks taken from MuPDF, with a
  `page_from_fallback_reader` warning on each page.
- **Better means:** the same text with layout, from a parser that can read these pages.

### 6. 4657006: text docling never extracts

11 tabloid pages (1224 × 792 pt), up to 12,141 paths and 4,744 words a page,
all digital with a sound text layer. Only **55.5%** of the words were
extracted: 1,261 of 2,833 are under no item (276 `text_not_extracted` issues,
e.g. "Analysis by: Solaris MCI", "Arc Flash and Shock Hazard Present PPE
Required"). From labels like these they appear to be electrical diagrams, where
labels sit among the drawing and docling's layout model assigns them to no
region. That is my reading, not checked page by page.

- **Shows:** the largest coverage gap on pages this pass converts.
- **Better means:** coverage near 100%, e.g. by adding uncovered text-layer
  words as items, as is already done for words inside pictures.

### 7. 4672628: the most table errors in the corpus

691 pages from PDF-XChange, 125 of them large sheets, 665 ruled, mostly
laboratory results tables. **34,885 `cell_text_disagrees` errors** and 6,676
`text_not_in_table_cell` errors (e.g. "Require Ratio Adsorption", "(mg/L)" in
no cell): TableFormer's grid does not match the printed one, so values land in
the wrong cell or in none.

- **Shows:** what the legacy lab measured: docling gets 35% of table rows exactly
  right, where a line grid read with the text layer gets far more.
- **Better means:** far fewer cell errors, and above all no value in the wrong cell.

### 8. 4664902: tables on engineering drawings

3 pages from Bluebeam, 1020 × 792 pt, 2 tables. **221 errors per page**, the
highest density in the corpus: 634 cell errors, where labels like "Size" and
"kPag" end up in no cell.

- **Shows:** tables embedded in drawings, where TableFormer's grid is unreliable.
- **Better means:** fewer errors per page; a small, fast test for any table-reader change.

### 9. 4647200: a broken text layer

986 pages, the document the legacy pipeline was tuned on. 315 pages carry a text
layer of unmapped glyph codes: text that looks present but is mojibake. Tier 0
flags all 315 as `suspect` with no reference to compare against, and this pass
leaves them pending for the `ocr_untrusted_text_layer` route. The 657 pages it
converts carry 8,723 text and cell disagreements marked as errors (a digit
differs), mostly in tables.

- **Shows:** detecting an untrusted text layer before believing it.
- **Better means:** the 315 pages converted by OCR in a later pass, with the same
  detection rate.

### 10. 4710294: a scanned filing

463 pages, 399 of them scans with no text layer. Tier 0 marks them `scanned`,
and this pass leaves them pending for the `ocr` route. Under settings tuned on
another document, the legacy pipeline once extracted only 13.2% of this one's
text.

- **Shows:** the OCR route, which this pass does not attempt yet.
- **Better means:** the 399 pages converted by OCR, with page-level agreement
  against a second reader.

## Other cases worth keeping

- **4665296**: a filled form (101 field values). A one-page copy of a page
  changed docling's output here (201 items and 88 errors, against 186 and 6),
  which is why docling always reads the original PDF.
- **4692330**: the review PDF failed (`ValueError: 61 is not in list`) because
  PyMuPDF could not copy its form-field tree. The fix builds the review PDF as the
  original bytes plus an incremental update.
- **4590700**: pages converted with gaps between them. It exposed the page
  renumbering in docling-core's `concatenate` (1,044 false "not extracted"
  errors) and is the regression check for page identity: 79 errors, 140 warnings
  (83 and 136 before words claimed by no item were kept).

## Re-running the baseline

```bash
scripts/convert/.venv/bin/python scripts/convert/convert.py run \
  source/4656998.pdf source/4648296.pdf source/4656888.pdf source/4681376.pdf \
  source/4646070.pdf source/4657006.pdf source/4672628.pdf source/4664902.pdf \
  source/4647200.pdf source/4710294.pdf --workers 2
scripts/convert/.venv/bin/python scripts/convert/convert.py flatten
```

Then compare against the numbers above, per document:

```sql
SELECT doc_id, status, pages_converted, coverage, issues_error, issues_warning, seconds
FROM 'scripts/convert/runs/convert-<sig>/parquet/documents/*.parquet'
WHERE doc_id IN ('4656998','4648296','4656888','4681376','4646070',
                 '4657006','4672628','4664902','4647200','4710294');
```

For documents 1–4, the telemetry CSV in the run's `sessions/` folder shows
memory per worker over time.
