# Corpus light pass: review of the first 207,127 documents (2026-10-17)

Taken while the pass was still running (about 61,000 documents to go, the oldest), from the
run log (`logs/light-all.log`, `.1`) for every document and from the ledgers of a random 3,000
(seed 17, 36,306 pages) read at low disk priority. No code was changed during the run
(LESSONS: change code only between runs); the fixes below wait for it to finish.

## What the pass did

| | |
|---|---:|
| documents converted | 207,127 |
| pages | 2,788,617 |
| words | 977,485,012 |
| pages read from the text layer / by OCR | 2,201,091 / 587,521 (21%) |
| documents read entirely by OCR / partly | 34,469 / 17,225 |
| seconds a page: median / 95th percentile | 0.20 / 2.20 |
| failed (latest attempt) | 0 - the 5 logged FAILED all have complete ledgers from later runs |
| partial (a page not read) | 5 - see below |

Every document reports text 100% and docling's words named once. These are conservation
checks - no word lost between the readers and the outputs - and they held on all 207,127; they
say nothing about whether a word was *read* right. Correctness comes from the independent
readings: text layer against OCR, our OCR against a scan's own OCR layer, and the voters to come.

## Issues in the sample (3,000 documents, 36,306 pages)

| severity | kind | pages | documents |
|---|---|---:|---:|
| info | ink_not_in_text_layer (stamps, text in images) | 3,312 | 778 |
| warning | text_layer_hidden_ocr (a scan with an OCR layer; ours used) | 2,518 | 181 |
| warning | ocr_readings_disagree (our OCR vs the scan's layer, < 90%) | 1,607 | 147 |
| warning | text_layer_stub | 1,137 | 99 |
| warning | pdf_ocr_disagree | 345 | 58 |
| warning | text_layer_suspect | 204 | 40 |
| info | blank_page | 191 | 34 |
| warning | text_layer_none | 48 | 20 |
| warning | no_text_found | 19 | 8 |
| warning | export_repeats_words | - | 7 |
| info | text_under_redaction | 2 | 2 |

Text layers: sound 28,531 pages (79%), none 3,915, hidden OCR 2,518, stub 1,138, suspect 204.
Todo tags: ocr_page 7,141, text_in_images 3,312, ocr_vote 1,607, oversize 568,
pdf_ocr_disagree 345, form_fields 98, scanned_no_text 14. Sections: none in 79% of documents,
bookmarks 14%, numbering 7%. The sample's documents are all dated 2011-2026: what is left in
the pass is the older material.

## Findings

1. **Agreement is recorded as 0% where it was never measured.** Light mode runs no OCR on a
   sound text layer, and `match_readings` then gives `pdf_found_by_ocr` 0.0 (no OCR word found
   any text-layer word) instead of None. 24,933 of 28,530 text-layer pages in the sample carry a
   0; `report` averages them into its pdf_vs_ocr columns. (`assemble.py:91`, called from
   `convert.py:133` and `:181`.)
2. **Bookmarks that match nothing become headings, and repeat text.** 4049986 is a 2-page
   letter with 136 bookmarks (a mail merge's, one set per recipient); 34 matched a line and the
   other 102 were written into the Markdown as headings ("Marc Boucher" 31 times): 2.1 Markdown
   words per evidence word. In the sample 174 documents (5.8%) have bookmarks of which fewer than
   half match; 7 tripped `export_repeats_words`. (`assemble.py:441-449`, `:504-507`.)
3. **Whole pages blacked out read as "no text found".** 4600031 and 4600032 (Enbridge appendices,
   titles say "Redacted") are pages that are one black rectangle over the whole sheet (ink 100%,
   one path, no text, no image). A fully redacted page is a fact about the filing, not a reading
   failure. 120 documents have no words at all (`light-pass-zero-words.txt`): mostly photographs
   (oversize or full-page images) and these black pages.
4. **The ledgers were written by different code, and do not say which.** 54% of the sample
   (1,613 of 3,000, converted 2026-10-13 and -14) have no `boundaries` summary: they predate
   furniture, joins and stitched tables. Every ledger's `run` is just "v2". After a restart the
   code that converts a document is whatever is on disk.
5. **MuPDF's own warnings are in the log but not tied to a document.** About 60,000 lines
   (13,479 "zlib error: incorrect header check", 9,922 missing XObject resources, 7,809 broken
   xref entries, ...) are printed by MuPDF directly and interleave with eight workers' output.
   They are evidence about how the PDFs were made and they are lost.
6. **5 documents still lack a page**, all converted on 2026-10-13 before pages MuPDF does not
   finish went to pdfium: 4537539 (p.130, the ArcGIS map), 4467586, 4467446, 4467453, 4467457
   (`../redo-partial.txt`). `redo-timeouts.txt` is done: every document in it has a complete
   ledger; `redo-empty-words.txt` is empty.
7. **892 documents have under 20 words a page** (`light-pass-sparse.txt`): drawings, photographs
   and forms, mostly; not yet looked at one by one.

## The rest of the pass (60,368 documents, finished 2026-10-17 20:56)

The documents converted after the review above: IDs down to about 90,000, dated 2002-2011
(a sample of 2,000, 27,987 pages). `done: 58894 converted, 0 failed` for the last launch.

| | |
|---|---:|
| pages / words | 803,271 / 244,848,020 |
| pages by OCR | 181,942 (23%) |
| documents read entirely by OCR / partly | 20,129 (33%) / 4,724 |
| seconds a page: median / 95th percentile | 0.60 / 2.36 |
| partial | 2 (761878, 760858), both from the GPU contention, in `../redo-gpu-contention.txt` |
| no words | 16, all photographs (434153: three site photos and a meter, no text) |
| under 20 words a page | 252 |

The same issue kinds as above and no new ones; conservation checks again 100%. Older filings
have no text layer more often: 18% of sampled pages (11% in 2011-2026), and fewer scans carry
their own OCR layer (781 pages, 2.8%; 6.9%). Bookmark headings repeat words in 4 of 2,000.

**GPU contention (2026-10-17 ~07:00-07:50):** a game on Windows took ~5 GB of the card; with the
pass at 22.2 of 24.5 GB, OCR went from 1.4 to 9.9 s a page and 43 documents had pages stall and
go to pdfium or be left out (none in the 74,800 before). Relaunched once the game closed.

**Whole corpus:** 267,500 documents in the logs, about 3.6 million pages and 1.22 billion words.

## Script updates, in order (after the pass)

1. `match_readings`: None when the other reader did not run; `report` then averages only
   measured pages.
2. Sections from bookmarks: a bookmark that matches no line is kept in the ledger's sections
   list but not written as a heading when most of the document's bookmarks do not match, or when
   its title already opened a section on the same page. Measure on the 174 and on 4049986
   (target: export ratio about 1.0, matched headings unchanged).
3. A `page_redacted` kind (info) for a page covered by one dark fill with no words, in place of
   `no_text_found`; counted for the filing guidance.
4. The ledger records the code: git commit of `scripts/convert` and whether it was dirty.
5. MuPDF warnings captured per document (`pymupdf.TOOLS.mupdf_display_errors(False)`, then
   `mupdf_warnings()` per page) into the ledger's issues as info, not stderr.
6. Then, without the GPU: `run --all --light --reuse-ocr --force` to reassemble every document on
   the one code (boundaries, fixes 1-3), and `run --light --force --sample runs/v2/redo-partial.txt`.
7. Then `status`, `report` and `check` over the whole corpus, saved here, and LESSONS.md.

## For LESSONS.md

- Conservation checks held on 207,127 of 207,127: they catch the pipeline losing words, not a
  reader misreading them. Quality is measured only where two readings are compared.
- A ledger that does not name its code cannot be compared with another: half the pass was
  assembled before the boundaries step.
- Bookmarks are not the document's structure when they come from a mail merge: check them
  against the page before trusting them.
- Throughput by era: 2.4 documents a second on 2025-2026 filings, about 1 a second in the
  2016-2019 range (more OCR), and about 1.5 on the oldest short scans.

## For the filing guidance

- Whole pages filed as black rectangles (4600031, 4600032).
- 17% of documents are scans read entirely by OCR; a further 8% partly.
- Scans with their own OCR layer: on 1,607 of 2,518 such pages that layer and our OCR agree on
  under 90% of the words.
- Broken PDFs (zlib streams, xref entries, missing resources): counted per document once fix 5
  is in.
