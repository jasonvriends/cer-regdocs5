# convert against cer-regdocs2: what we have, why it is better, how to check

As of 2026-09-30, run `convert-42241a3e`. 113 test documents; the full corpus has not been
re-run with this code.

## What convert is

A PDF-to-structured-text converter for the CER REGDOCS corpus. It replaces cer-regdocs2's
Stage 3 and 4 (Azure `prebuilt-layout` analysis, then normalization). It runs locally, on
one GPU.

```text
tier0     per page: size, text layer (sound, suspect, hidden OCR, none), images, drawings
route     each page to a reader: text layer | full-page OCR | text layer + OCR of images
convert   docling per page (layout, tables), in a worker with memory and time limits
text      each text item's text = the text-layer words in its box (never docling's own
          reading); words no item claims become items; redacted words are left out
tables    per table, the grid that fits the words best: TableFormer fast or accurate,
          the ruling lines, or text alignment
ocr       RapidOCR on scans and broken text layers; a scan's own OCR layer votes
check     every word accounted for; issues with page and box; review PDF
export    DoclingDocument JSON, Markdown, evidence (every word, the item that holds it)
```

Every choice is recorded with its reason and scores in `<id>.convert.json.gz`, and every
text-layer word in `<id>.evidence.jsonl.gz`.

## Why it is better

Against cer-regdocs2's Azure output for the same 113 files (matched by SHA-256), measured
by `scripts/convert/compare_legacy.py`. Neither system's output is the yardstick. On digital pages the
yardstick is the text layer's words that show on the page: not invisible, not under a
redaction. Tables are judged against those same words.

| | convert | cer-regdocs2 |
|---|---:|---:|
| visible words kept, 1,993 ordinary digital pages | **99.9%** | 84.2% |
| words not on the page (misread, merged, invented) | **0.2%** | 1.7% |
| visible words kept, 166 drawing pages | **100.0%** | 90.7% |
| words not on the page, drawings | **0.6%** | 10.7% |
| table words badly placed (fit), per 1,000 table words | **47.5** | 178.7 |
| table words in a cell's box but in no cell's text | **64** | 84,908 |
| scan pages with text (of 1,095) | 1,086 | 1,086 |
| agreement with a scan's own OCR layer | 75% | 40% |

What those numbers are made of:

1. **Digital text is copied, not re-read.** Azure OCRs the page image even where the PDF holds
   exact text. On 4664902 it read "M45C" as "M4SC", "Z662-23" as "2662-23", "IHP-02" as
   "HP-02", "14895" as "14805". On 4656888 (1.5 pt spreadsheet text) it kept 1.1% of the
   words, and on 4648296 (34 x 22 in sheets) 8%. convert takes those words from the text layer.
2. **Tables are chosen per table and scored in words.** Ruled drawing tables take the ruling
   lines (4664902: 477 errors to 0). Lab tables ruled only between blocks take text alignment
   (4672628: 90% fewer badly placed words, 63,280 to 6,415). Everything else takes TableFormer, fast or accurate,
   whichever places more words.
3. **Nothing is silently lost.** Every text-layer word is either in an item or reported.
   Checks include: the export holds at least 98% of the words, words in no item become
   items, and table words in no cell are kept beside the table.
4. **Redactions are respected.** Text blacked out on the page but left in the file is left
   out (4710294: 11,246 words).
5. **Nothing stops the run.** Heavy sheets are routed by tier 0. Workers have memory and time
   limits and a text-layer fallback. The loader hang on certified-signature PDFs is avoided.
   All 113 documents converted, 3,272 of their 3,287 pages. The 15 left are blank or
   undecided pages, pending with a reason.

## Where it is not better, or not proven

- **Scans have no answer key.** The OCR comparison is agreement, not accuracy: the two OCRs
  agree 84% with each other. Where a scan has its own OCR layer, convert agrees with it 75% and
  Azure 40%. That layer is itself an OCR of unknown quality. A hand-typed sample of about 100
  pages would settle it (cer-regdocs2's report says the same).
- **Table fit sees only some rows run together.** Since run convert-87f469f1 it flags a stack of
  values beside other lines at the same heights (`table_rows_merged`); rows of text run
  together still look like wrapped text. The alignment grid splits a two-line header into two rows. The line and alignment grids
  build cell text from the text layer, so their cell checks agree by construction; fit is the
  check that means something there.
- **Drawings are counted apart.** Their text layer is often line-style labels ("PO PO PO")
  as much as words, so "kept" means less there.
- **Structure beyond text and tables was not compared:** reading order, paragraph roles,
  section paths, figures. Azure provides some of these; convert has docling's layout labels and
  headings.
- **113 documents, not the corpus.** The ten were chosen as the hardest, the 98 at random plus
  every certified-signature filing, the 5 as scans. The full corpus (8,206 documents) has not
  been run with this code. Time: 71 minutes for the 113 on one GPU with 2 workers.

## How to check it yourself

Everything below runs from the repository root.

**1. Re-run the comparison.** It uses the saved outputs, so no conversion is needed.

```bash
scripts/convert/.venv/bin/python scripts/convert/compare_legacy.py --run convert-42241a3e \
  4656998 4648296 4656888 4681376 4646070 4657006 4672628 4664902 4647200 4710294
```

Each line is one document; the summary JSON follows. Add `--out file.json` for the
per-document detail.

**2. Convert again from scratch.** Output goes to a new run folder, named after a hash of the
code.

```bash
scripts/convert/.venv/bin/python scripts/convert/convert.py run source/4664902.pdf source/4710294.pdf --workers 2
scripts/convert/.venv/bin/python scripts/convert/compare_legacy.py --run convert-<new> 4664902 4710294
```

**3. Look, don't trust the numbers.** For any document `d` in run `r`, under
`output/<d>/convert/<r>/`:

| file | what to look for |
|---|---|
| `<d>.review.pdf` | the filed PDF with every issue drawn on its page (colour = severity) |
| `<d>.md` | the export; compare it with the PDF side by side |
| `<d>.convert.json.gz` | `summary`; `table_choices`: each table's scores per reading and the one kept; `routes`; `docling_source` |
| `<d>.evidence.jsonl.gz` | every word: page, box, and the item that holds it (`how`: docling, added, ocr) |
| `<d>.issues.json` | every issue with page, box, kind, severity |

Spot checks worth doing:

- **Tables:** open 4664902 p2 and 4672628 p124 in the review PDF, and read their tables in
  the `.md`: values should sit under the right headers.
- **Redaction:** in 4710294, pages 408 onward are blacked out. Search the `.md` for a
  phrase from under a black box (copy it from the PDF's text layer); it must not be there.
- **Azure's misreadings:** find "M4SC" or "2662-23" in cer-regdocs2's
  `workspace/4_normalize/pages.jsonl` for document 4664902, and "M45C" in convert's `.md`.
- **OCR:** 4710294 p7 is a scanned French letter; its accents should be right in the `.md`.

**4. The regression sets:** the ten in `BASELINE.md` (the hardest; each change is shown
before and after on them), and the 98 random plus signed documents, for the easy cases.
`BASELINE.md` has the commands and every run's numbers. `LESSONS.md` has what went wrong
along the way and how each problem was found.
