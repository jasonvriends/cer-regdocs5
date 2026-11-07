# Full pass: where we are, and the review of its output

Read this first when picking the work up again. Update it at the end of every batch.

## The full pass (state at 2026-11-05 18:14 EST; ~350 documents an hour, so ~119,500 left is ~2 weeks of running)

- **Running:** `setsid nohup ./scripts/convert/convert.sh run --sample scripts/convert/runs/v2/full-resume-2026-11-04.txt --workers 5`
  from `~/repos/cer-regdocs5`, log `runs/v2/logs/full-main-2026-11-05-1727.log`. To check it:
  `grep -ciE '^(ok|partial|failed) ' <log>` and `tail <log>`. To stop it: `kill -INT -<pgid>`.
  Relaunch with the same command: converted documents are skipped (the skip check is now ~1 min).
- **Done:** 150,715 documents, 2,109,731 pages (catalog rebuilt 2026-11-05 from every ledger).
  **Left:** ~119,500 of the 269,984 in the resume list (it runs newest first; at 2759xxx, i.e. 2015).
- **Left out, to redo after the pass** (`known-bad-2026-11-02.txt`, not in the resume list):
  - 6 MuPDF double-free / stall documents: 4458299 4537539 4642786 4473998 4449303 3297497 (needs the
    probe-every-page retry fix in LESSONS.md).
  - 44 "no progress for 300s (at start)" from full-main-2026-11-02-0840.log, and 2955068
    (`UnicodeEncodeError: surrogates not allowed` writing the ledger: a lone surrogate in the
    REGDOCS metadata or PDF info; escape it before writing). Retry with `--stall 1800`, fewer workers.
- **Runs since LESSONS.md was last written (2026-10-27):** logs 10-27-1651 (16,025 ok), 10-29 (13,933),
  10-30-0857 (1,949), then 10-30-2220, 10-31-0834, 10-31-2020, 11-01-0708 converted 0-114 each
  (stopped by hand or the machine; every relaunch re-ran the known-bad documents first), 11-02-0840
  (24,760 ok, 45 failed), 11-04-1824 (713), 11-05-0532 (2,565; cut off by a machine restart at
  12:52 with no summary), 11-05-1727 (running).

## Findings so far (to fold into LESSONS.md once the batches confirm them)

1. **The start stalls appear only late in a long run.** All 44 began after ~12,000 documents in the
   11-02 run, then about one every 300 documents, on files of any size (2797403 is 108 KB). No
   run under 12,000 documents had one. Points at a worker or the machine degrading with run length
   (GPU check `nvidia-smi` hanging? file handles? WSL drive cache?), not at the PDFs. *Test:* retry
   the 44 in a fresh run; if they all pass in seconds, they were never hard documents.
2. **The internal checks are saturated.** On all 150,715: export recall 1.0, docling holds every
   evidence word, 4 errors (2 documents, `page_skipped`). They prove nothing is lost between
   readers and output; they say nothing about whether the readers were right. The review below is
   the only check on that.
3. **Every run log carries ~56,000 lines × 8 of `PydanticSerializationUnexpectedValue` for
   `CodeItem` with `label UNKNOWN` and `BaseMeta` (regdocs__reader).** Logs are ~95 MB each because
   of it. Find out whether a CodeItem with label unknown is a real mislabel in the docling.json
   (a text item stored as code) or only a serializer warning; then silence or fix.
4. Corpus shape (full mode, 2015-2026): pages captured digital 71.8%, scan without layer 9.9%,
   digital+images 6.5%, scanner OCR layer 5.1%, stub layer 4.2%, suspect 1.1%, text as curves 0.5%,
   blank 0.3%. OCR supplied 21% of pages (11.7% in 2026, 32% in 2018 and 2024).
   Issues: `layout_models_disagree` on 995,514 pages (86% of documents), `text_layer_hidden_ocr`
   107,448 pages, `text_layer_stub` 87,654, `ocr_readings_disagree` 77,410, `table_grid_doubtful`
   35,323, `text_layer_suspect` 23,138, `table_not_read` 1,157, `export_repeats_words` 166.
5. The ledgers come from five code commits (943860d, 9ddc9f6, 844226a, e1d5f72, a3b09e0); compare
   like with like when a finding depends on code.

## The review at corpus scale: `review_scan.py`

Reading finds a fault; a signature counts it on every document. `scripts/convert/review_scan.py scan`
writes one row per converted document (Markdown + ledger signals: long and sentence headings,
lead-ins as headings and their order, list markers, bullet glyphs, CJK and other scripts, private-use
and replacement characters, picture placeholders, hyphen breaks, glued footnotes, letter-spaced and
glued text, repeated lines) to `scan/chunk-*.parquet`, 5,000 documents a chunk, skipping documents
already scanned: rerun it to resume or to add documents converted since. `review_scan.py report`
prints each signal's count, share and worst three documents. Every new fault found by reading gets
a signal here before the next batch; the batches below are then drawn from the scan (the worst and
a random draw per signal), not just from the ledger's issue kinds.

## The review: batches of 10 documents

Each batch file (`batch-*.txt`, ids from the catalog, picked by a stable hash) is a set to read:
open `output/<id>/convert/<id>.md` beside the PDF pages (render with MuPDF at 100 dpi), check text,
order, headings, tables and pictures against what the page shows, and write what is wrong below.
Mark the batch done here before starting the next, so a new session continues from the first
`todo`.

| batch | what it targets | status | findings |
|---|---|---|---|
| 01-random | 10 documents of up to 40 pages, any kind | **done** 2026-11-05 | 5 faults to fix: form answers, bookmark headings, lead-in order, CJK symbols, list markers (see Batch 01) |
| 02-stub-layer | `text_layer_stub` pages: OCR replaced a near-empty layer | todo | |
| 03-hidden-ocr | `text_layer_hidden_ocr`: a scanner's hidden OCR layer | todo | |
| 04-ocr-disagree | `ocr_readings_disagree`: two OCR readings differ | todo | |
| 05-table-doubtful | `table_grid_doubtful` | todo | |
| 06-suspect-layer | `text_layer_suspect` | todo | |
| 07-text-as-curves | pages whose text is drawn as curves | todo | |
| 08-pdf-ocr-disagree | `pdf_ocr_disagree` warnings: text layer and OCR differ | todo | |
| 09-repeats-notext | `export_repeats_words` (5), `no_text_found` warnings (5) | todo | |
| 10-redaction | redacted pages and text under redactions | todo | |
| 11-drawings-oversize | oversize pages (drawings, maps) | todo | |
| 12-forms | pages with form fields | todo | |
| 13-table-not-read | `table_not_read` | todo | |
| 14-scans-2015-2018 | scanned documents, 2015-2018, up to 30 pages | todo | |

Catalog queries used: `scripts/convert/catalog.py` tables `documents`, `pages`, `issues`
(`./scripts/convert/convert.sh catalog sql "..."`). Rebuild it after the pass:
`convert.sh catalog build --workers 8` (150,715 ledgers in ~10 minutes).

## Batch notes

### Batch 01 (random)

- **3337507** (scanned aerial map, Canon MFP 2017): OCR read the red labels and the legend well.
  Slips: "RESIDENCE 9B METERS" (page shows 96?), "200 METERRADIUS" (space lost), the small date
  stamp at top left missed; the whole figure classed `screenshot_from_computer`.
- **4411254** (CER order, Foxit 2023): every word right, reading order right. **Numbered
  conditions export as `- 1. ...`** (a bullet holding a literal number, a list docling read as
  unordered with the number as text), and `(a)`/`(b)` are not nested under 3. Bold/italic lost.
  **Measured:** 347 of 1,500 random converted .md files (23%) have `- N. ` lines, 297 (20%) `- (a) `;
  801 have any list, so ~43% of documents with lists carry it. Fix in the export: a list item whose
  text starts with its own marker becomes an ordered item (or keeps the marker, without the `- `).
- **3934254** (CER Application for Short-Term Natural Gas Export, Crystal Reports 2020):
  **the form's answers are lost.** The chosen option ("The applicant is a corporation") is marked
  by a green check-mark image; the export prints all four options as plain lines and a bare
  `*[icon]*` after them. The ledger has what is needed: picture class `icon`, conf 0.99, box
  12 x 11 pt at x 47, on the chosen line's baseline. Crystal Reports made **10,010 documents
  (30,963 pages, 2015-2026)**, ~8,764 titled as these short-term export/import applications; all
  likely carry their answers this way (page 3 of this one has the export/import choices too).
  *Fix (no reconversion):* a small icon picture immediately left of a text line → mark that line
  as selected (`☑`), and the unmarked siblings `☐`; test on this form in EN and FR (3716451).
  Also: the logo is detected as four overlapping regions → `*[logo]*` printed 3-4 times; the grey
  section bands ("Applicant Legal Status") are not headings.
- **3954446** (NGTL Written Final Argument, Aspose 2020, 23 p): words all right, **structure
  wrong: every numbered paragraph is a heading.** The PDF's 134 bookmarks are the paragraphs
  themselves ("20. As a result, and based on ...", some cut with "..."): a law-firm Word template
  styles numbered paragraphs as headings. `find_sections` trusts bookmarks, so the paragraph's
  first lines become `####` and the rest a `- ` list item (layout models had said list_item 6,
  section_header 2 on page 6). Also footnote markers flattened into the text ("process.22").
  **Measured:** 259 of 1,500 sampled documents (17%) take sections from bookmarks; 69 (4.6%) have
  headings over 150 characters, 16 (1%) five or more. *Fix:* a bookmark whose title is a sentence
  (over ~20 words, or ends in "." / "..." / ";", or longer than the line it matched) is a
  paragraph number, not a heading: keep the numbered paragraph as one paragraph, and use only the
  short bookmarks (III., A., B.) as headings.
- Furniture: page numbers ("- 6 -") and footers (`LEGAL_CAL:...`) are labelled furniture in
  docling.json, but the .md prints them in the body (by design, for word recall). Consider a
  marked form (e.g. an HTML comment) so readers of the .md can skip them.
- **3411115, 3947680** (NEB/CER letters, 2018/2020): body right. The bilingual letterhead is read
  across both languages ("National Energy Office national / Board de l'énergie") and the footer's
  labels and numbers come apart (": 403-292-4800" before "Telephone/Téléphone"); addresses split one
  line per word group. Boilerplate on thousands of letters: noise more than loss.
- **4200288** (Trans Mountain letter, Word 2022): body right. **OCR wrote the Trans Mountain logo
  as the Chinese character 而**, and **bullets are doubled** (`- • Landowner requests`).
  **Measured on the 1,500:** 349 documents (23%) have `- •`-style doubled bullets (the PDF's glyph
  kept inside a Markdown item; strip a leading bullet glyph from list item text). **352 documents
  (23%) hold CJK characters, 3,035 of them**, 2,899 of 2,902 evidence words from OCR (1,509 OCR-only
  words on digital pages, 1,390 on OCR pages), conf 0.6-0.99: 而 936 (TM logo), 囚 219, 口 174,
  场 172, 一 171, 回 109, 日 104. They are graphic marks forced into characters by PP-OCR's
  Chinese dictionary: logos, valve symbols on schematics (3802842 p15: every 口 is a valve),
  check boxes. *Fix:* no CER filing in the corpus is in Chinese; treat an OCR word that is only
  CJK as a symbol, not text (drop from evidence, or record as `symbol` with its box), or limit
  the recognizer's character set to Latin. Check Inuktitut syllabics are not caught by the rule.
- **3716451** (the same export application in French, 2018): same loss, four check-mark
  `*[icon]*` cut loose from the product and period choices; the two-column contact block is read
  across the row ("Nom : M Carl Téléphone: 716-684-5411 / Hasselback Poste :").
- **4495921** (CER letter with an NGTL map, 2024): letter right; the map's labels come out as a
  jumble of neighbouring names ("Strome HolmbergiMS Killam PTI Lateral - NPS Loop 6 ..."), and
  OCR typos in the disclaimer (known open item: text in drawings and maps).
- **4265387** (CER order AO-001-MO-020-2021, Foxit 2022): **the lead-ins ("IN THE MATTER OF",
  "BEFORE", "AND WHEREAS") became `##` headings placed after the paragraph they introduce**, so
  every recital reads under the wrong lead-in. 4411254, the same kind of order, came out right:
  it depends on how the lead-in sits beside its paragraph (a separate bold column at the same top).
  **Measured on the 1,500:** 162 documents have a lead-in heading; in **83 (5.5% of all
  documents, ~30% of the 279 orders in the sample)** the paragraph before the first lead-in starts
  in lower case, i.e. text came before its lead-in. Spot check 4712683 (French order GO-051-2026,
  2026): "RELATIVEMENT À" and "DEVANT" after their text, two "ATTENDU QUE" stacked. Corpus: 25,629
  documents titled order/ordonnance. *Fix:* a lead-in is not a heading; join it to the paragraph
  whose first line shares its top (same baseline), in that order, as a bold run.

**Batch 01 summary.** 10 documents, 0 with words lost; 6 with a structural or meaning fault
worth fixing: form answers lost (check-mark icons), numbered paragraphs as headings (bookmarks),
lead-ins out of order (orders), plus corpus-wide export noise (doubled bullets 23%, `- 1.` lists
23%, CJK symbols 23%, repeated `*[logo]*`). None of these show in the ledger's checks, which count
words, not where they go or what marks mean.

### Corpus scan, 2026-11-05 (150,979 documents; `scan-report-2026-11-05.txt`)

Sizes of the batch 01 faults, every document: numbered items as bullets 27.4% (695,512 lines),
letters as bullets 19.1%, doubled bullet glyphs 22.2% (1.2 M lines), CJK characters 19.6%
(554,068), lead-in headings 9.4% with text before the first lead-in 5.7% and lead-ins stacked
5.2%, headings over 150 characters 5.4% (88,852), numbered sentence headings 4.1%.
Read from the worst documents of the signals no one had looked at:

- **Wrong characters in a "sound" text layer: numbers silently wrong (most severe so far).**
  2967975 (Energy East V22 fish habitat, 376 p, Adobe PDF Library): table headers "(100)" are stored
  as `ϭϬϬͿ` (Greek/Coptic and Cyrillic, a font's ToUnicode off by a fixed amount: the
  well-known Calibri/Word broken-CMap pattern). 2957200 (V4 geotechnical report, Acrobat): the
  digit 3 in a table column is `٣` (Arabic-Indic). Both pages judged `text_layer: sound`, capture
  `digital`; only `table_grid_doubtful` fired. Scan: cyrillic 144 documents, Greek words 84,
  Arabic/Hebrew 51, Indic/Thai 333; but an offset that lands in Latin cannot be seen by script, so
  this is a floor. *Fix:* where OCR ran on the page (it does for pictures; run it on a sample of
  digital pages too), compare digits by box; a layer whose digits disagree with OCR's is
  `suspect`. Also decode known offsets (U+03E0+ to 0-9) when the OCR agrees.
- **Private-use characters, 9.0% of documents (759,847)**: Symbol/Wingdings bullets and check
  boxes from the text layer (`U+F0B7`, `U+F0A7`, `U+F06F`, ...), e.g. 4289041 `` bullets,
  4724792 `` options in a table. Map the known Symbol/Wingdings code points to Unicode (• ▪ ☐ ☑ ✓)
  before export; then the doubled-bullet rule strips them.
- **Replacement characters**: 31 documents, 14,067: table-of-contents leader dots with no Unicode
  (2786529 `1.1 Purpose of the Report ���...`). Drop runs of U+FFFD as leaders.
- **Hyphen breaks, 13.5% (286,837)**: line-end hyphens kept with a space ("navi- gation",
  "mar- itime" in the consolidated Acts 2812079; "long- standing" 3954446). Join a lower-case word
  broken at a line end (keep the hyphen only for real compounds: "vice- présidents").
- **French narrow no-break space as a Tamil sign**: Justice Canada's French Acts (3718909) store
  U+202F as `ௗ` (U+0BD7): "suivantesௗ:", "20 ௗ%". Map it back.
- **Correction to the CJK finding: some filings do hold real Chinese.** 4105885 (Bluebeam, 195 p,
  stub layer) is a Chinese mill's material certificate (材质合格证, Jiangsu Yuzuan), and OCR read the
  Chinese correctly; its letters come out spaced ("M A T E R I A L"). So do not drop CJK
  outright: drop it when it is a lone character in a picture or drawing, or on a page whose
  other text is Latin; keep runs of CJK. Letter-spaced text: 7.1% of documents, to read next.
- One-character lines 43.6% of documents and a repeated line in nearly all: too broad as signals
  (page numbers, table cells, signatures); refine before using.

### Read-200: 8 documents from each of the 25 largest document types, one random page each

`read-200.tsv` (doc, page chosen at random, year, producer, type); read with
`review/excerpt.sh <doc> <page>`; page images checked where the text looks wrong.
**All 200 read (2026-11-05)**; findings folded into LESSONS.md "Reading the output of 150,000 documents".

- **A word taken out of its sentence (meaning changed).** 4648280 p3: in the quoted definition
  «peuples autochtones du Canada» the word "autochtones" was put at the end of the footnote, so
  the quotation reads «peuples du Canada». Same line (y 698), but a one-word item (#/texts/51)
  cut through it and is written after the line's item. No issue raised. **Measured on read-200:
  5 of 200 documents (2.5%)** have a text-layer line whose middle word belongs to another item
  than the words either side (4104450 "MH-3-2007,", 3956607 "(A73728-2),", 4600335 "6.4.5.4;",
  3241747 in a fixed-width log). Two are inside link annotations, two not: the cause is a small
  region overlapping part of a line. *Fix:* assign words to items by text-layer line first: a
  line's words stay together in the item holding most of them, unless the line truly spans two
  columns.
- **Sidebars and letterhead blocks interleave with the address block.** 4364682 (NorthRiver): the
  sender column (Martha Peden, T:, E:, Filed Electronically) is read between "Attention:" and
  "Ms. Ramona Sladic", and the sender's name becomes `##`. 4403573 (MLT Aikins): the firm's
  address column lands inside the "Re:" block; the date splits ("September" / "6, 2023").
- **Handwritten signatures OCR'd into text.** 2897620 (NEB panel letter): "Hamilm", "Ahim Sos"
  under `*[signature]*`. Text OCR'd inside a picture classed `signature` is noise; keep it in
  the evidence but not in the Markdown body.
- **Digits broken at a hyphen** (4031359 "514 879- 4173"): the hyphen-break fix must not join or
  split digit groups; phone numbers are digits-hyphen-digits.
- **Large tables over-split into columns.** 2926972 p13 (French cost grid): 14 columns, most empty;
  merged "Plage inférieure / supérieure" ranges spread over cells. The amounts are there; the
  grid is not the table's.
- Good: 2955998, 2897620 body, 3811789, 4648280 body (French), 4041666 (scanned calibration
  form, key/value pairs mostly kept; "Westccoast" is in the PDF's own OCR layer).
**Rows 11-20** (Application 3-8, Order 1-4):
- **Orders: lead-ins out of order in ~56% of all 17,026 orders** (scan joined with the catalog):
  Adobe PDF Library 70% of 6,604, Foxit 42% of 6,126, Word 57% of 3,658, Distiller 56%; every year
  2015-2026 (38%-83%). Read: 3709624, 3081141, 3321223 (NEB 2016-2018) broken; 4361108 (Word 2023,
  lead-ins inline) right. 3321223 is worse than order: the lines of one recital are reordered
  ("the National Energy Board Act (NEB Act) and / thereunder; and / the regulations made"): the
  lead-in column (IN THE MATTER OF) and the text column are read as two columns, and the text
  column's lines are interleaved with region boundaries. **The single largest meaning fault
  found: an order's recitals are its reasons.** The fix belongs in the layout step: a bold
  lead-in at the left with text starting on the same baseline is one paragraph, read lead-in first.
- **Forms (Crystal Reports export applications, EN and FR): every one read has the same three
  faults**: the chosen option lost (no mark at all on some pages: 4498492 p1 prints all seven
  "Check one" options plain); two-column field rows read across ("Nom : M Trevor Téléphone:
  403-984-9144 / Wilkie Poste : 6002"); and values far from labels (4577847 p2: the submission
  date "27/06/2025" printed after the product list, its label at the top). These forms are filled
  PDFs from a CER web form: the values are in fixed boxes beside the labels, so a form reader
  (label box → value box by position) would read them exactly.
- 2969783 (Energy East mitigation table, p21): the table's six column headers run together into
  one line; check whether the table body is a table.
**Rows 21-30** (Order 5-8, Other 1-6):
- Orders confirmed again: 4451223, 4506112 (Word 2024, lead-ins inline) right; 4303236 (Foxit 2023,
  French) "RELATIVEMENT" after its text. 4305608 (annex table) right.
- **A second form family loses its answers: radio buttons.** 4572357 (CER Reporting Form for
  set-aside mechanisms, Adobe PDF Library 2025): "Exempt pursuant to MH-001-2013? (o) Yes (•) No"
  exports as "Yes No"; and the two labels in neighbouring rows ("Exempt pursuant to MH-001-2013?" /
  "Confirm exemption is still valid and briefly explain why exemption was granted") are
  **interleaved word by word** into one line ("explain Exempt Confirm why pursuant exemption
  exemption to is MH‐001‐2013? still was valid granted and briefly"): two lines with nearly the
  same baseline merged, then sorted by x. Meaning gone from the words alone.
- **A table that runs over pages is written once, under its first page.** 2969783 (Energy East
  mitigation table, pages 19-21, one docling table): page 21's marker is followed only by page 21's
  headers and footers; its rows are under page 19. Words kept, but the page markers no longer
  say where text is: a citation "p. 21" cannot be found in the .md. Write each page's rows under
  that page's marker (repeat the header row), or mark the continuation.
- **OCR-only words enter running text on digital pages.** Same table: "emergency response
  philosophy 金 focuses on minimizing ..." (a bullet or mark read by OCR, inserted into a text-layer
  cell). OCR "extra" words on a digital page belong only where the text layer has none (pictures).
- 4372528 (TQM cover letter): sidebar inserted between "Attention:" and the addressee, as 4364682.
- Good: 3962211 (book of oral histories, 49 p), 4555280 (WTW report), 4600550, 3891907 (a Gantt
  chart as a table, readable).
**Rows 31-45** (Other 7-8, Supplemental Information 1-8, Information Request Response 1-5):
- **Row numbers taken out of tables as "line numbers".** 4051056 p7 (ITC Lake Erie Commitments
  Tracking Table): the Number column (147-162) is empty in every row and the numbers are written
  once as `*Lines 147 148 ... 162*`, so no commitment can be found by its number. 4087662
  (financial schedules): the "Line" column is removed while rows cite it ("Equity return (Line 14
  - Line 16 - Line 17)", "Total Rate Base (Line 4 + Line 9 + Line 10)"): the arithmetic can no
  longer be followed. `assemble.line_numbers` (built for transcripts' margins) passes its pitch
  test on tables of equal-height rows. **Measured: 65 of 1,500 documents (4.3%) have a `*Lines`
  line; none is a transcript.** Right in some (3322792 Union Gas evidence with numbered lines);
  wrong in tables (4051056, 4087662), a table of contents (3797408 `*Lines 1 2 ... 8*` under
  "Table of Contents") and forms (3309998). *Fix:* never take line numbers from words inside a
  table region, nor where the numbers sit in a ruled column; keep the margin rule for prose.
- **4401964 p5** (TMEP site table, Word 2023): the page holds only the table's header text, as in
  2969783 (a table continued from the previous page, written there).
- **Slides lose their order** (2909360, a PowerPoint deck, producer none): bullets and dates in
  fragments ("October / 19, / 2015"), an OCR fragment "otii iat".
- **Drawing text one word per line** (4461910 alignment sheet: a file path "P:\19731 - Kinder
  Morgan TMX\81 Drafting\..." one token per line; 3005387 a circular chart recorder: numbers
  without meaning). Known open item; low value.
- Trans Mountain logo as 而 again (4155360, 4467676, 4082705): every TM filing carries it.
- Good: 4397274 (UNDRIP), 4305406 (wildlife plan table), 3392367, 4432980, 3067093 (map labels
  readable), 4082705 body.
**Rows 46-60** (IR Response 6-8, Compliance after a Board Order 1-8, Written Evidence 1-4):
- **Page markers do not say where text is (multi-page tables).** 3306261: a 286-row table over
  pages 1-4 written under page 1; pages 2-4 hold a line each. **Measured on the 1,500: 66 documents
  (4.4%), 1,064 of 20,362 pages with >=100 words (5.2%) have under 25% of their words in their own
  page's .md section** (4647074: 254 pages, 3296614: 168). Anyone citing "p. N" from the .md,
  or chunking by page for search, gets the wrong page.
- **Paragraphs as headings is concentrated where structure matters most**: documents with >= 3
  numbered sentence-headings: **Argument 19.8% of 903, Written Evidence 8.5% of 3,571**, Affidavit
  3.0%, Application 2.2%, Letter 0.9%. Both routes: bookmarks (e.g. Argument 80, Evidence 155) and
  convert's own heading numbers (Argument 50, Evidence 90). Read: 2786260 (Scia'new written
  evidence, no bookmarks): "## 1 Scia'new First Nation ... has aboriginal rights in ..." with
  the paragraph's second line as a `- ` item. *Fix both routes:* a numbered line that continues
  into a sentence is a numbered paragraph.
- **Words broken by spacing in the text layer**: 3335408 (TMEP environmental alignment sheet):
  "T HOMPSON-NI COL A REGIONAL DIST RICT", "RECL AMAT ION", "POT ENT IAL". Wide kerning read as
  spaces; a search for "Thompson-Nicola" or "reclamation" misses them.
- Good: 4264682, 3241747 (relay settings tables), 4302847 (PCM table), 3559385, 3593234,
  3777303 (org chart as text), 3559367, 2785293, 3768535 (TEK interview text).
**Rows 61-80** (Written Evidence 5-8, Receipt 1-8, O&M Notification 1-8):
- **CER web-form outputs (Chromium, PDFsharp, Aspose) keep their answers**: the e-filing system
  prints each answer as text ("You selected groundwater ... Yes"; "First name / Jenner"). The
  lost-answer fault belongs to filled PDF forms (Crystal Reports export applications, Acrobat
  reporting forms), not to forms in general.
- **Receipts**: letter-spaced titles in 2015 (2784712 "F I L I N G R E C E I P T | A C C U S É ...",
  SSRS: a search for "filing receipt" misses it); signature rules `______ ______` become `##`
  headings (4724317, 4575444, 4576360); bilingual side-by-side columns merged line by line
  (4518996 "paper document listed above. nécessaire de copies sur papier pour chaque docu").
  Low value: receipts' facts are in the REGDOCS metadata.
- 3911970 (STSA constitution, Ghostscript): numbered clause "6.3 Being a party to any particular
  Nation-to-Nation..." split into `###` + `- ` again (no bookmarks: the layout/numbering route).
- Maps and charts: OCR fragments ("e ue-Lafrance Chem diOk" 4253014; "Population 09 size"
  3718493). Known open item.
- Good: 4683883, 4660213, 4474575, 4368431, 4507338, 4372399 (O&M attachment tables), 2837209
  (a list of plates with leader dots; one OCR `一`).
**Rows 81-104** (Information Request 1-8, Letter of Comment 1-8, Application to Participate 1-8):
- **Check boxes drawn as boxes are read; answers marked by an image or a radio widget are not.**
  SSRS Applications to Participate (3768118, 2857182, 2015-2019) export `☒ [English þ]` /
  `☐ [French ¨]` and `☒ [~ Commenter]` / `☐ [{ Intervenor]`: state right. But each option is written
  twice (the line, then the checkbox record), and the Wingdings glyphs come through as Latin
  letters: `þ` (checked), `¨` (empty), `~`, `{`, a private-use char. *Fix:* map a Wingdings/Symbol
  font's characters by font name before export (þ→☑, ¨→☐, §→▪, l→●, Ø→➢ ...), and write the option
  once with its state. Compare 3934254 / 4572357 (image check mark, radio button): lost.
- OCR CJK from logos again: 3592707 `米` (Tsuut'ina letterhead).
- Good (19 of 24): IR attachments with references and preambles (3905412, 3744942, 4371044),
  tables of contents with leader dots (3308691), share provisions (3737496), letters of comment
  typed and scanned (3748290, 3277118 EPSON scan, 2812222, 3700612, 3913696), SSRS contact
  blocks (3330461, 2931248).
**Rows 105-128** (Affidavit 1-8, Map 1-8, Ruling 1-8):
- **A scan's own OCR layer passed as digital text.** 3746809 p2 (affidavit, creator I.R.I.S.):
  one 2550x3300 page image with an OCR layer; classed `digital`, `sound`, no issue, so convert
  kept the layer ("wr~ttenevidence", "tt18 best", and handwriting OCR'd into headings
  `#### ,1' AAA-Z ! "7, J-O;L J`) although its own OCR agreed with only 81% of the words.
  **Measured (catalog): 8,108 pages classed `digital` are covered >= 90% by one image (2,099
  documents); on 2,350 of them (787 documents) OCR agrees with the layer on < 90% of words
  (median 0.80).** *Fix:* a page under one full-page image is a scan whatever its text layer's
  render mode; route it as `scanner_ocr` (compare, vote), and treat agreement < 0.9 as `suspect`.
- **Handwriting and stamps OCR'd as body text**: 3718917 (exhibit stamp: "This is Exhibit / of
  Ronald Eric / before me at / this 7 / 739"), 3746809 (signatures, dates). Keep OCR text from
  `signature` pictures and handwriting out of the body.
- Maps and drawings (8 of 8): label soup by design (3306697 "Fraser Kiver ... KP 1167",
  3923228 survey plan numbers, 4593478). Labels are findable words; there is no reading order.
  Known open item; not worth more than findability.
- Rulings (8 of 8): right (3962372, 3770411, 3013105, 3974121, 3926582 French, 4642898).
**Rows 129-160** (Argument 1-8, Procedural Update 1-8, Surveillance report 1-8, Compliance to an
Order 1-8):
- **A numeric table read as a header plus a packed column.** 4166098 p20 (TC Canadian Mainline
  Schedule 8.1, IT bid floors, ~40 ruled rows): only the header became a table; one cell holds the
  whole Average column ("(c) 0.1035 1.6473 0.5986 ..."), and the receipt and delivery points follow
  as plain lines ("1 Centram MDA Emerson 2"). Every toll is detached from its path. **Measured:
  13 of 675 sampled documents with tables (1.9%) have a cell holding >= 8 numbers (80 cells;
  4712878: 27)**, a floor, since a table not detected at all (most of 4166098) does not show.
  Surveillance reports and financial schedules (Excel-made, dense, ruled) are the documents whose
  numbers matter most. *Fix/check:* a table cell whose words span several text-layer lines with
  one number each is several rows; rebuild the grid from the ruling lines (MuPDF tables) there.
- Argument: numbered paragraphs as headings (4039724 "## 7. Trans Mountain filed written
  evidence ..."); real margin line numbers handled right (4603928 CAPP, `*Lines 1 ... 22*`);
  6 of 8 right (2905759 statute text, 4026506 scan, 4600423, 3747772, 4684713, 2765600).
- Letterheads: logos OCR'd into the name ("EENBRIDGE", "Q Hydro Québec", "T N" Trans-Northern);
  an address line and "Attention:" merged into one `##` (4692677 Pembina).
- Procedural updates: the first page is letterhead only in this view; pages 2+ right (3805184,
  4585410 French annex).
- Good: surveillance report schedules from Excel (4665183, 4423156, 4652562 "Line No." kept as a
  column), compliance filings (4683932 EPP, 4684594).
**Rows 161-200** (Hearing Transcript, Letter Decision, Registration to Participate, Errata, Permit):
- Transcripts right in substance; the paragraph number sometimes on its own line before its text
  (2921911 "19565." then "- I'd like ..."), a speaker's name split ("Mr. / Morley", 3777458).
- Letter decisions (8 of 8) right, French included.
- Registrations (SSRS): consent check marks as `*[icon]*`; values right.
- Permits: lead-in fault as in orders, cutting a sentence: 4239846 "## ATTENDU QU'au" after
  "moyen de l'ordonnance AO-012-OPL-004-2020 ...". Otherwise right (3614087, 4726666, 4039887).
- Errata: right (letterheads as elsewhere).

## Next (in order of what a fix is worth)

1. Fix in convert, then re-export (no reconversion needed for most: the ledgers, words and regions
   hold what is needed): lead-ins in orders/permits; form answers from icons and radio widgets;
   sentence bookmarks and numbered lines as paragraphs; line numbers never from tables; page
   markers for multi-page tables; CJK and private-use glyph mapping; list markers and bullets;
   hyphen joins. Test each on the documents named in LESSONS.md, then rescan with
   `review_scan.py` and compare the counts.
2. Detectors still missing: wrong-but-sound text layers (compare digits with OCR on a sample of
   digital pages); full-page image + text layer classed digital (route as scanner_ocr).
3. Read more: a second 200 by producer and year (2015-2018 Adobe PDF Library, Crystal Reports,
   Excel schedules, Energy East binders), and the worst 3 of every scan signal.

## Reading at scale with sub-agents (started 2026-11-05)

API batches (`scripts/convert/read_batch.py`, ready, shards prepared) are not used: reading runs on
the subscription with sub-agents. `review/agent-read/`: `batches/batch-000..079.tsv` (2,000
documents not yet read, stratified by document type x producer, two random pages each),
`INSTRUCTIONS.md` (what each agent does and the fault kinds; the same text as read_batch.SYSTEM),
`render.sh` (a page to PNG), `out/batch-NNN.jsonl` (one JSON line per document, appended as read)
and `out/batch-NNN.done`. `summarize.py [--other]` tallies them.
**To continue:** find batches without `out/*.done`; launch one sub-agent per batch (Sonnet), 4 at a
time, prompt: "Read .../agent-read/INSTRUCTIONS.md and follow it exactly. Your batch is NNN". An
agent resumes a half-done batch by skipping doc_ids already in its output.
Launched: 000-003.
Done: 000-003 (100 documents). Launched: 004-007.

### Agent reading, first 100 documents (batches 000-003)

- **OCR loses whole lines and line starts, and no check can see it** (the ledger's recall is
  measured against OCR's own reading). Confirmed by image: 2955501 p1 (Enbridge tariff letter,
  scan, no text layer): "Rule 12, Liability of the Carrier, paragraph (b) has been updated to
  clarify the liability" came out "- Éule C lit of the carrier"; 3117386 p156 (French Hydro-Québec
  specification, text inside an image, 214 of 228 words OCR-only): the opening sentence of 8.1 and a
  sentence of 8.2 are gone, leaving "e a", "o d"; 4735582 p12 (suspect layer, OCR): "from time to
  time ... permit all persons participating ... to communicate" became "from im m e ... as er te
  with each other". *Measure next:* lines of the page image with ink but no OCR words (a blank-line
  test per page), and pages where two OCR readings exist but disagree.
- Agents confirm the batch-01 faults elsewhere: form answers lost (4496452: the green tick on
  "Corporation incorporated in Canada" missing, by image), lead-ins out of order (4411133,
  4596489, 4364599, 4605564), page misplaced (3543081 p7, by image), row numbers as `*Lines*`
  (3063519), CJK symbols in drawings (中 而 六 必 回), letter-spaced receipts.
- New: two columns interleaved line by line in body text (3117386 p118, French spec; 3348046 p7
  agreement clauses 7.5-7.10, suspected); file-label headings not on the page (4142865 "## C61
  cover page" ... `#####`; 3561254 "## winter road map.vsd"); step labels displaced in a French
  info sheet (3349142, by image).
Done: 000-007 (200 documents, instructions v1: every finding). From batch 008 on: **discovery
mode** (`INSTRUCTIONS-v2.md`): agents tag known patterns by catalogue id and report only patterns
not in `docs/conversion-failure-patterns.md`, each confirmed on the page image. The goal is the
catalogue of distinct patterns (for the AI / digital-first case), not counts; stop when several
batches in a row find no new pattern. After each round, add confirmed new patterns to the
catalogue (and regenerate INSTRUCTIONS-v2.md's table from it).

### Discovery rounds (unique patterns; register `review/patterns.json`, tool `scripts/convert/patterns.py`)

| round | batches | new patterns confirmed |
|---|---|---|
| 1 | 008-011 (100 docs) | E5 superscripts into numbers (4460101 "76⁴"→"764"), J1 tracked changes flattened (3895437, 3935185), F6 row numbers offset (3884322), F7 sub-table header separated (3336044) |

Each pattern keeps at most 5 examples (doc, page, page_shows, output_says, checked_image) and a
`lesson` for the converter. `patterns.py merge` pulls agents' `new` entries into `candidates`;
resolve each into a pattern (`new`) or an example (`add`), then refresh INSTRUCTIONS-v2.md's
table from docs/conversion-failure-patterns.md. Launched round 2: 012-015.
| 2 | 012-015 (100 docs) | J2 colour carries meaning (3578263), K1 redaction gap + stamps as numbers (4574102), K2 text under redaction (ledger: 4493099), A5 handwritten correction gives a wrong valid-looking id (3803169 GH-003-2018 → 2013), H2 content cut off in the filed PDF (2813818, 4572575) |
Launched round 3: 016-019.
| 3 (partial) | 017 | F8 neighbouring tables merged, titles fused (3084351) |
| 3 (partial) | 016 | D5 rotated text scrambled (3309458) |
| 3 | 016-019 (100 docs) | D5 rotated text scrambled (3309458), F8 tables merged (3084351), A6 OCR drops zero dashes (3390240), G2 chart labels into table (4553651), B5 correct invisible CAD text layer discarded as hidden OCR (4248055; 3,307 pages / 608 docs with no image under a hidden layer: a converter bug), J3 icons/indent carry hierarchy (3898988, unchecked) |
Rounds so far: 4, 5, 6 new patterns: not saturated. Launched round 4: 020-023.
| 4 (partial) | 020 | none new; B5 widened: every hidden layer (107,588 pages) is replaced by our OCR, even where better (4247461) |
| 4 (partial) | 022 | G3 legend symbols as check boxes (2958070); C2 variant: repeated form sections collapsed (3643115) |
| 4 (partial) | 021 | none new (A4 example 3921545, F7 example 4543487: category bands) |
| 4 | 020-023 (100 docs) | G3 legend symbols as check boxes (2958070), F9 footnote spliced into a row (2902086), C3 check box read in the opposite state (2774076); B5 widened; variants to F2, E1, J1, F4, F7, A4, C2 |
Launched round 5: 024-027.
| 5 (partial) | 024 | none new (H2 widened: content missing from the filed PDF, 2950002) |
| 5 | 024-027 (100 docs) | D6 rotated pages read reversed (2957894; 179,146 rotated pages, sample half scrambled: converter), E6 wrapped number line as list item (4680777) |
New per round: 4, 5, 6, 3, 2. Launched round 6: 028-031.
| 6 (partial) | 030 | none new (F9 widened: margin stamp in a row, 3179563) |
| 6 (partial) | 028 | none new (E2: merged-PDF bookmarks as headings on page 1, 2838739 - converter; A5: handwritten date 2016 → 2026, 3750376) |
| 6 | 028-031 (100 docs) | **none new**: variants widened E2 (merged-PDF bookmarks), A5, F9 (margin stamp), G3 (redaction boxes as check boxes), A6 (dropped digit 99.99→99.9), B5 (TOC truncated) |
New per round: 4, 5, 6, 3, 2, 0.
Launched round 7 (confirmation): 032-035.
| 7 (partial) | 034 | A7 Indigenous-language text (syllabics) read as junk (3096662) - significant for CER; B1 example '<' stored as 'o' (3718783) |
| 7 (partial) | 035 | none new (B1 example 3812355: Coptic digits in a chainage, 'ISSUED FOR 5(9,(:') |
| 7 | 032-035 (100 docs) | A7 Indigenous-language syllabics as junk (3096662), B6 invisible text surfaces (4590652); B1 examples ('<' as 'o' 3718783, Coptic digits 3812355) |
New per round: 4, 5, 6, 3, 2, 0, 2. New patterns are now rarer and narrower (one document each).

### Targeted rounds (2026-11-06): kinds least read so far
Committed the work so far on branch `review-patterns` (6fc15d2). Batches `batch-T01..T06.tsv`:
T01 Excel schedules / tolls / tariffs / surveillance, T02 hearing transcripts, T03 French, T04 CAD and
drawing sets (AutoCAD, MicroStation, Bluebeam, ArcGIS), T05 Indigenous engagement and TLU/TEK, T06 2015
scans. 25 documents each, excluding everything already read.
| targeted (partial) | T01, T02, T04 | A8 OCR look-alike substitutions in codes (3 drawings), E7 transcript furniture in speech (3 transcripts); B2 example: degree sign dropped |
| targeted | T01 done | B2 lesson: degree sign U+F0B0 deleted by the converter; Excel toll worksheets: every value kept its row, sign and decimals (3348073 checked against the text layer) |
| targeted | T02 done | widened D4 (cell tail spilled, 3718215), J3 (braces: who represents whom, 2837588), E7 (speaker turn split, 3062862) |
| targeted | T03 done | none new. French characters intact (accents, ç, « », apostrophes, 1er, spacing before : ; ? !). Variants: elided lead-in split 'ATTENDU' / 'QU'Enbridge' (D1, 3777805, 4632349); URLs broken at line-end hyphens (E4, 3299262) |
| targeted | T05 done | none new. Indigenous names in Latin script intact (Sexqeltqin, Dene Tha', Dene Tsaa Tse K'Nai, Maskwacis). Sample by title held no syllabics or orthography letters (ʷ ł ə ʔ): *open:* select by content (text layer or OCR holding U+1400-167F, ʷ, ł, ə, ʔ) to test A7 properly. H2 again: a letter citing attached maps with none filed (2839648) |
| targeted | T04 done | none new; A8 example: dimension 45.0 m read 15.0 m (3293729); J1 on a scan: struck pipe sizes live (2776896); F4/F6 on hydrotech creek sheets (2995908) |
| targeted | T06 done | none new; H2 example: an orphan web-print fragment filed as the document (2790791) |

## Status 2026-11-06: catalogue complete for now

50 patterns, 11 families (14 not in the file, 27 partly, 9 the converter's own), from 210 documents
read by hand and 1,050 by agents against page images. New per round of 100: 4, 5, 6, 3, 2, 0, 2;
targeted rounds: 2 then 0 across schedules, transcripts, French, drawings, Indigenous filings, 2015
scans. Open: (1) filings written in Indigenous languages, selected by content (syllabics
U+1400-167F, ʷ ł ə ʔ in the text layer or OCR), to test A7; (2) the converter fixes for the 9
in-the-file patterns (LESSONS.md, "What the pattern register says"), then re-export and re-read
their examples; (3) measure D6 (rotated pages) and B5 (hidden layers) page by page, not by sample.
To add a pattern later: `scripts/convert/patterns.py new|add`, then the prose in
docs/conversion-failure-patterns.md.
| targeted | T07 built | Indigenous-language text selected by content (702 documents hold syllabics or orthography letters; 21 syllabics): 25 documents, first pages with the text |
| targeted | T07 done | Indigenous-language text, selected by content: exact in 18 of 25; B7 new (text layer lacks the letters, 2785525); A7 on scans (2784902, 2785452). Register 51 patterns; counts updated in catalogue, CEO report (repo and Claude Docs), deck slide 5, lessons |
