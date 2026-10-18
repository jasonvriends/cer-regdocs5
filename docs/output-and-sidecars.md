# The output folder: what to keep, what a full scan adds, and which sidecars to make

A recommendation, 2026-10-18. Principle: **the PDF is the original; per-document files are the
evidence; corpus-wide files are indexes built from the evidence.** Evidence is kept; indexes are
rebuilt whenever they are needed and never edited by hand.

## What is there now

```
source/<id>.pdf                          the file as REGDOCS served it (SHA-256 in the scout record)
output/<id>/<id>.cer.meta.json.gz        the scout record: what REGDOCS says about the document, when,
                                         and every change since; the download's hash
output/<id>/convert/<id>.pages.json      the ledger: per page, its facts, how its text layer reads, which
                                         reader's words are the evidence and why, agreement between
                                         readings, issues, to-do tags; per document, the code that wrote it
output/<id>/convert/<id>.words.parquet   every word of every reader, with its box, page and reader
output/<id>/convert/<id>.docling.json.gz the document's structure (DoclingDocument) built over those words
output/<id>/convert/<id>.md              the readable text, with page markers
  (full mode adds) <id>.regions.parquet  every layout model's boxes, and the merged regions
                   <id>.tables.parquet   every table reader's grid for every table, with its score
output/catalog.duckdb                    every ledger and scout record, as tables (derived)
output/integrity.parquet                 every file's signatures, PDF/A, tags, forms, attachments (derived)
```

Five files a document, about 12-18 KB a page: roughly 50-60 GB for the corpus beside the source PDFs.

## What to keep, and why

"Keep" means the kind of file, from **the latest conversion of each document**. A full conversion
writes the same files (ledger, words, structure, Markdown) with `mode: full` and replaces the light
ones; nothing of a light conversion needs keeping once its document has been converted in full -
the outputs people will use come from the full mode. Until then, the light files are that
document's only extract, so they are replaced, not deleted ahead of the run. (Full mode does not
reuse the light pass's OCR today; making it do so on pages whose OCR it would repeat is a possible
saving to measure, not an assumption.)

| file | keep? | why |
|---|---|---|
| source PDF | **always, unchanged** | the original; everything else is checkable against its hash |
| scout record | **always** | the only record of what REGDOCS said and when, including changes and documents REGDOCS later withdrew; facets that classify the document |
| words.parquet | **always** (latest mode) | the evidence. It holds the OCR, which costs GPU hours to redo (over a day for the corpus); with it, every later assembly - headings, page joins, element IDs, fixes - runs without the GPU (`--reuse-ocr`) |
| pages.json (ledger) | **always** (latest mode) | the record of every decision and doubt, page by page; the to-do list for better readers; what makes the extract verifiable |
| regions / tables .parquet (full mode) | **always** | every reading, not only the winner: when a table is questioned, the alternatives and their scores are there |
| docling.json.gz | keep the latest | structure for downstream tools; rebuildable from words + ledger |
| .md | keep the latest | for people and plain-text tools; rebuildable |
| catalog.duckdb, integrity.parquet, census | rebuild when needed | indexes; minutes to hours from the evidence |

Nothing in the current tree is redundant. What should *not* be kept is a second copy of anything
derived, or outputs from older code beside newer ones: latest only, with the code hash in each ledger
saying which code made it.

## What a full scan adds

The light pass reads text and how each page was captured. Full mode adds what the page's *layout*
says, on every page:

| adds | which enables |
|---|---|
| **tables as cells** - four readers per table, the best kept and all recorded | a table catalogue: find any table, export it to CSV or Parquet; the corpus count of tables and of doubtful grids |
| **layout regions** - headings, paragraphs, lists, captions, figures, footnotes, forms, from four layout models that vote | element-level structure everywhere, not only where bookmarks or numbering exist (79% of documents have neither) |
| **figures classified** (photograph, drawing, map, chart) with their text read | drawings and maps counted and findable; text inside figures |
| **checkboxes and form layouts** | the state of flattened forms |
| **reading order** on multi-column and rotated pages | text that reads in the right order for search and language models |
| **a second opinion on every page** - layout models that disagree mark a page for review | errors found where the light pass has nothing to compare |

Its cost is GPU time: many times the light pass per page. It should be measured on a sample before
the corpus run, and run only when authorized.

## Which sidecars, at which level

### Per document (in `output/<id>/convert/`) - the evidence

| sidecar | contents | status |
|---|---|---|
| `pages.json` | the ledger, with the code hash and the PDF's hash | exists |
| `words.parquet` | every reading of every word | exists |
| `elements.parquet` | one row per element - `<id>:p<page>:e<n>`, type (paragraph, heading, list item, caption, table, figure, form field), text, box, word IDs, section path | exists (2026-10-18): the unit of citation for search and language models |
| `tables.parquet` + `cells.parquet` | each table's readings; the chosen grid as cells (row, column, spans, header flag, text, word IDs) | exist (full mode) |
| `docling.json.gz`, `.md` | structure and text for downstream tools | exist |
| `manifest.json` | every file above with its SHA-256, the PDF's hash, the code hash, the run | exists (2026-10-18): closes the chain of custody from the PDF to every derived file |

### Per filing (a REGDOCS folder or compound document) - the context

REGDOCS groups documents into filings (an application with its attachments, an information request
round). The scout already records each document's containers.

| sidecar | contents | why |
|---|---|---|
| **`filing.json`** | the filing's documents in order, their types, dates and hashes; the filing's capture summary (pages not digital, redaction flags, signatures) | a filing is how people and decisions use the record: "is this application complete and readable" is a filing-level question; it is also where attachments split across files are put back together |

### Corpus-wide - the indexes (rebuilt, never edited)

| index | contents | why |
|---|---|---|
| `catalog.duckdb` | documents, pages, issues (exists); filings, elements, tables (proposed) | every question about the corpus as SQL |
| `integrity.parquet` | per-file signatures, PDF/A, tags, forms, attachments | the integrity evidence |
| **`duplicates.parquet`** | SHA-256 to every REGDOCS ID filed with that file | the same file filed under several IDs (4656998 = 4657424 = 4657571): convert once, cite all |
| **`reprocess/<issue>.txt`** | one list of pages per to-do kind (OCR vote, doubtful table, oversize, text in images) | a better reader runs on exactly the pages it can fix, across the corpus |
| **elements and cells, partitioned by year** | the per-document elements and cells gathered for search and analysis | the input to a search index or a language model's retrieval - later |
| reports | census, review lists | what the corpus says, regenerated from the catalog |

## Why per document, per filing and corpus-wide

- **Per document** keeps evidence beside its PDF: one document can be reconverted, checked or deleted
  without touching another, and nothing about it depends on a corpus-wide build.
- **Per filing** is where meaning lives for the regulator - an application and its attachments, a
  question and its response.
- **Corpus-wide** files are only indexes: fast to query, safe to delete, rebuilt from the evidence in
  minutes to hours (the catalog: 78 minutes for 269,025 documents).

What each level must carry: the hash of what it was built from and the hash of the code that built
it. With those, any answer - a search hit, a number in a table, a model's citation - traces back to a
word, a page and a file whose hash matches the one REGDOCS received.
