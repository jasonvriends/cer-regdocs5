# Lessons learned: convert

What building convert has shown so far, with the evidence for each lesson. The docling
pipeline's own history is in [../legacy/docs/lessons_learned.md](../legacy/docs/lessons_learned.md);
this document picks up where it stops. The test set is [BASELINE.md](BASELINE.md), the tool
comparison [../bench/RESULTS.md](../bench/RESULTS.md).

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
