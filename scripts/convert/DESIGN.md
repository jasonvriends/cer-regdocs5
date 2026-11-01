# convert: design

**Purpose.** Learn how REGDOCS filings should be *captured* - produced, signed, scanned, redacted,
filed - so that future filings arrive as faithful digital documents. The findings go in
[docs/filing-guidance.md](../../docs/filing-guidance.md); the converter is the measuring instrument, and the
evidence it needs is exactly what this design produces: for every page, whether the file holds
the words the page shows, and what was lost when it does not.

To do that it turns every REGDOCS filing into evidence: every word on every page, where it is, which reader
read it, and what is still missing, with structure (headings, paragraphs, tables, pictures,
forms) laid over the words, never instead of them.

The *why* behind each decision is in the lessons, cited as **[C §n]** for
[docs/lessons_learned.md](../../docs/lessons_learned.md) (the five earlier pipelines, cer-regdocs1-5) and
**[L heading]** for [LESSONS.md](LESSONS.md) (this converter's own history).

## Goals, in order

1. **Nothing silently lost.** Every page ends with a measured account of what was read and
   what was not. An empty problem list means "checked, nothing found". The worst failures in
   every earlier pipeline were silent: 316 pages of mojibake with no warning, cloud output
   with zero warnings over content it got wrong [C Key findings 1].
2. **Chain of custody.** Any word in any output traces to the PDF's sha256, the page, the box,
   the reader and its version and weights, and the REGDOCS record it was downloaded from.
3. **Know what is missing, so it can be fixed later.** Gaps are rows that can be queried
   ("every scanned drawing", "every table whose grid is doubtful"), so a better tool can be
   pointed at exactly those pages [C §6 Record something for every page].
4. **Accuracy first, speed later.** For now (research), every box is read by several tools and
   all readings are kept, so we learn which tool is right for which box on which kind of page.
   The production route is then the measured winner per case, not a guess [C Key findings 4].

## Principles

- **Words come from readers that cannot invent them.** The text layer (MuPDF) when it is sound,
  OCR when it is not. Structure readers (layout, table models) only draw boxes; the words in
  a box are the evidence words inside it [C §5.1, L Text from the text layer]. Generative vision
  models may vote, never supply a value: they made up addresses and numbers [C §5.6].
- **Every reading is kept; choices are recorded with their reason and score.** The choice is a
  separate, re-runnable step over the readings [C §6 References].
- **Decide per box, from what the box is and what page it is on.** The route is by box label
  (text, table, picture, form ...) and the page's features, not one setting for the corpus
  [L Route by what a page is].
- **REGDOCS context is an input.** The scout record says the filing's Document Type, Commodity,
  company, date and title. A transcript, an order, an IR response and a site plan are read
  differently, and that knowledge belongs in the route and in the analysis.
- **Measure on random samples, look at the page, count words added as well as lost**
  [C Method, C §6 Metric traps].

## Pipeline, per document

```
context   scout record (facets, title, date, filing) + PDF facts (producer family, made-as, fonts)
pages     per page: size, orientation, rotation, text direction, text layer health, images,
          ruling lines, form fields, annotations                     (was tier0; [C §4])
words     reader "pdf": MuPDF words with boxes; flags hidden / redacted   (always)
          reader "ocr": RapidOCR PP-OCRv6 medium on the rendered page    (research: every page)
          reader "barcode": zxing-cpp on every picture region: exact values
verdict   per page: is the text layer the page? agreement of pdf vs ocr words, char health
          [C §5.2]. Evidence words = pdf words where sound, else ocr words; ocr words over ink
          the text layer lacks (stamps, text in images) are added and flagged
layout    several models on the rendered page: heron, heron-101, egret-large, egret-xlarge.
          Boxes merged across models (IoU); each region keeps every model's label and score;
          its label is the vote
read      per region, by label:
            text, heading, list, caption, footnote, header/footer  -> evidence words in box
            table, document_index -> grids from TableFormer fast, TableFormer accurate,
                     MuPDF lines (ruling), MuPDF text alignment; each scored by words placed;
                     the best kept, all recorded               [C §5.4, L Tables]
            picture -> figure classifier (map, drawing, photo, chart, signature, stamp, logo...)
                     + the words inside it (text layer, or OCR of the crop)   [C §5.5]
            form, key_value, checkbox -> words + AcroForm fields + checkbox state  [C §5.5]
          words no region claims become text items of their own  (55.5% -> 99.3% [C §5.3])
sections  where each section starts and how deep: the filer's bookmarks (two or more; each
          title matched to its line on the target page, else the section opens at the page's
          top with the bookmark's text), else heading numbers that run in sequence (the
          longest chain of 2.3 -> 2.4 / 2.3.1 / 3, at least three; titles of words; not on
          drawing sheets; one-level chains spread over pages). The heading line is split out of
          the element it was clustered with                              [L Sections]
build     DoclingDocument (docling-core, our own code, not docling's converter); each item
          carries meta `regdocs__words` (evidence word ids) and `regdocs__reader`; each
          section is a SECTION group holding its heading (with its level) and what follows,
          so a chunker walks the tree instead of a window over text
vote      vote.py: on lines where our OCR and a scan's own OCR layer differ, two vision models
          measured first (vote.py bench); where both choose the layer, its words are the evidence
check     coverage (every evidence word in exactly one item), duplicates, tables' fit,
          pdf-vs-ocr disagreements, readers disagreeing on a region's label
ledger    per page: features, readers run and their status, coverage, issues, todo tags
export    Markdown written from the items, checked to hold every evidence word
```

## The rules: which reader for which page and box (as measured, 2026-10-12)

Research mode still runs every reader; these are the rules that choose among them, and the
measured winners that a production route can keep. Evidence in [LESSONS.md](LESSONS.md).

**Per page - where the words come from**

| page | rule | measured |
|---|---|---|
| text layer sound | the layer's words; OCR words over ink the layer lacks added, flagged | OCR finds 85-99% of a sound layer's words at their place |
| stub layer (image page, <100 layer words, OCR finds 4x more) | OCR; the stub's own words kept where OCR does not cover them | 2% of pages (survey) |
| suspect, none, scanner OCR layer | OCR (PP-OCRv6 medium); a scanner layer kept as a second reading | v6 medium 92.5% vs small 89.9% |
| two OCR readings disagree on a line | qwen3-vl and GLM-OCR vote; both for the scanner layer: its words | voters 95.7% / 92.9% vs the layer as truth |
| nothing on it (<0.2% ink) | blank | duplex backs, separators |
| transcript (title, or margin numbers on half the pages) | one item per line or paragraph number | 72 tables to 4 (90560) |
| numbered paper (8+ margin numbers, not a transcript) | the numbers one furniture item | 410 to 4 stray paragraphs |

**Per box - which reader for what the box is** (boxes from four layout models merged by vote;
a box kept if two models found it or one at 0.5)

| box | rule | measured |
|---|---|---|
| text, heading, list, caption, footnote, code, formula, key-value, form | the evidence words in it (tables, pictures and forms take theirs first; else the smallest box) | 100% of words in exactly one item |
| page header, footer | the same, in the furniture layer | |
| table | **TableFormer fast + accurate + ruling lines**, kept by fit (words placed, stacked values not counted), ties to the least empty grid; text alignment only below 60% | 96.6% of table words placed across 4,348 tables (accurate alone 95.2%, fast alone 91.3%) |
| table that is a contents page (dot leaders, document_index) | one item per entry line | |
| picture | figure classifier (26 classes); barcodes decoded; the words in it as its child text | 2,000+ pictures; OCR at more angles not worth it (+3 points, +40 junk words per 100) |
| checkbox | state from the models' vote; label from the words in or beside its box | agrees with the text layer's tick glyphs where present |
| words in no box | text items of their own, grouped by place | nothing lost |

Not yet decided from measurement: one layout model instead of four (no truth for regions yet),
and reading order on two-column pages (median disagreement 0.10 with the text layer's order).

## Why DoclingDocument, and why not docling's converter

DoclingDocument is open (MIT), serialises losslessly to JSON, gives every item a page, box and
character span, and has Markdown/HTML/chunking tools. Its item metadata takes namespaced custom
fields (`regdocs__...`) and its prediction fields carry `confidence` and `created_by`, so our
provenance fits without forking the format. It has no word level, so words live in a sidecar
the items point into.

Docling's *converter pipeline* is not used: it was the source of most of the old code's
workarounds - pages renumbered on merge, a signature `/Reference` hanging the loader, renders of
scans as noise, Markdown dropping footnotes and picture text, timeouts that do not stop a step,
memory growing with pages held [C §5.3, L docling's behaviour]. The models it wraps (Heron,
Egret, TableFormer, the figure classifier, RapidOCR) are called directly.

## Outputs

```
output/<id>/
  <id>.cer.meta.json.gz              scout: what REGDOCS says (the custody of the file itself)
  convert/
    <id>.words.parquet               every word from every reader: page, box, text, reader,
                                     confidence, flags; never edited, only appended to
    <id>.regions.parquet             every layout detection of every model, and the merged regions
    <id>.tables.parquet              every table reading of every table reader, with its score
    <id>.pages.json                  the ledger: per page features, verdict, readers, coverage,
                                     issues, todo; per document context and run identity
    <id>.docling.json.gz             the chosen structure, items pointing at evidence words
    <id>.md                          export
```

Boxes are PDF points, top-left origin, on the page as displayed (rotation applied).

Corpus questions are SQL over the Parquet files (DuckDB), e.g. which table reader wins on
landscape pages from Excel, or how often the layout models disagree on scanned forms.

## Run identity

`run.json` per run (in `scripts/convert/runs/<version>/`): pipeline version, git commit and dirty
files, every package version, sha256 of every model's weights, settings. Each document's
ledger names its run. A changed version writes beside the old; a later fix names its parent
and refuses to run if the PDF's sha256 differs [C §7 Provenance]. Simpler than hashing syntax
trees, which cost more than it bought.

## Operations

Worker processes each hold the models and convert one document at a time; the parent replaces
a worker on a crash, or when no page has finished for `--stall` seconds (a cap scaled by pages
stays as a backstop), and records the failure, so one bad PDF costs one document
[C §7 Reliability, L Memory and time]. Workers hand cached GPU memory back after OCR.
Assembly can be redone without the GPU: `--reuse-ocr` takes the OCR words a light conversion
saved (same OCR model) instead of reading the page again. Pages are rendered under a pixel budget [C §5.5]. GPU memory is checked
before starting [C §7 Reliability: a game on the GPU].

## Measuring

`survey/*.csv` and `samples/*.txt` are the test sets: random by year, by document type, and the
first 20 (10 from 2002, 10 from October 2026). `convert.py report` summarises a run: coverage,
pdf-vs-ocr agreement, layout-model agreement by label, table-reader wins by page feature.

## Build order

1. Words, verdict, ledger, layout ensemble, per-label readers, DoclingDocument, Markdown
   (this version), measured on the first 20.
2. The report across page features; the samples.
3. Table Transformer as a fifth table reader; OCR of picture crops at 0/90/270 [C §5.5].
4. Second-opinion models on todo pages only, as voters: olmOCR-2 and GLM-OCR (Ollama, already
   measured [C §5.6]), Baidu Unlimited-OCR (DeepSeek-OCR based: blank-page test first), PaddleOCR-VL,
   Chandra. Each voter measured on the `ocr_vote` pages before it is trusted with any.
5. The production route: the measured winner per case, one reader per box.
