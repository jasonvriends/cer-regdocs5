# convert

A fresh extraction pipeline for CER REGDOCS filings -- about 265,000 PDFs from
2012 to 2026, kept as regulatory evidence and extracted for RAG and AI use.

The idea: learn what every page *is* before choosing how to read it, then send
each page, and later each region, to the tool that reads that kind of content
best. The findings from the docling pipeline (`../legacy/docs/lessons_learned.md`)
are the starting evidence, not its code.

## Setup

```bash
./scripts/convert/setup.sh      # environment in scripts/convert/.venv, docling's models into scripts/convert/models/ (700 MB)
```

`requirements.txt` is the exact `pip freeze` of the environment. Each run
records the full freeze in its `env.json`; its run id hashes only the
packages that stage uses and everything they require, so installing docling
does not change tier 0's id.

## Stages

| stage | reads | writes, per document | speed on `source/` |
|---|---|---|---|
| [tier0.py](tier0.py) | the PDF | what every page is, and what made the file | 700 pages/s, CPU |
| [convert.py](convert.py) | the PDF, tier 0's record | the DoclingDocument, per-item provenance, issues, review PDF | 6 pages/s, 2 GPU workers, one page per call |

Both share [common.py](common.py): run ids, run folders, session logs, isolated
workers, resuming. [DESIGN.md](DESIGN.md) is the plan for the stages to come.

## Tier 0: the file's own structure

```bash
scripts/convert/.venv/bin/python scripts/convert/tier0.py run                 # every PDF in source/
scripts/convert/.venv/bin/python scripts/convert/tier0.py run source/4647200.pdf some/dir/
scripts/convert/.venv/bin/python scripts/convert/tier0.py status              # what each run holds, and why things failed
```

No rendering and no models. It reads each PDF with PyMuPDF and records, per
document and per page, everything the file says about itself:

| | per document | per page |
|---|---|---|
| identity | path, size, mtime, **sha256** | label, size, rotation, boxes |
| text | fonts: type, encoding, embedded, ToUnicode, *unmappable risk* | characters by class (letter, digit, replacement, control, private use) for **visible** and **invisible** text separately; direction (0/90/180/270); fonts; words; text sha256; preview |
| images | | each placement: bbox, cover, pixels, effective DPI, colour space, encoding filter (JPEG / JBIG2 / CCITT = scanner), sha256 of the stored bytes |
| vector | | paths, lines by orientation, ruling lines, boxes, curves |
| forms | AcroForm / XFA | every field: type, name, **value**, checkbox state |
| markup | | annotations (highlights, stamps, comments with their text, author, date), links |
| structure | metadata, XMP, catalog, outline, page labels, layers, **embedded files (hashed)** | |
| process | timings per step, MuPDF warnings, errors with tracebacks | the same, per page |

Then **hints**: derived labels such as `kind` (digital / scan /
image_with_text / vector_drawing / sparse / blank) and `text_layer` (sound /
suspect / hidden_ocr / none). These are rules versioned as `SETTINGS["hints"]`,
and can be recomputed from the facts in the same record.

On the two documents that shaped the old pipeline, tier 0 finds both of its
historic failures in seconds with no reference: 4647200's **315 suspect
pages** (the mojibake that took a second extractor to discover) and
4710294's **399 scans** with no text layer.

### Digital or scanned, and what made it

Every page gets an **origin**, with the evidence for it
(`hints.origin`, `hints.origin_evidence`):

| origin | meaning | evidence it reads |
|---|---|---|
| `digital` | text or drawings made by software | visible text layer, vector paths, no page-sized image |
| `scanned` | a page-sized image, no text, with signs of a scanner | bilevel image (JBIG2, CCITT, 1-bit), a scanner resolution (200/300/400/600 dpi), a scanner or OCR tool in the metadata |
| `scanned_ocr` | a scan with a hidden OCR text layer | invisible text over a page image |
| `raster` | a page-sized image with no text and no sign of a scanner | e.g. a page exported as a picture |
| `image_with_text` | a page image with visible digital text over it | a stamped or annotated scan, or a page with a full background image |
| `blank`, `unknown` | | |

Every document gets **what made it** (`origin` in the record):

- `made_as`: `born_digital`, `scanned`, `scanned_ocr` or `mixed` (digital
  and scanned pages together, typically a letter with scanned attachments),
  with the page counts behind it
- the producer, the creator and the XMP `CreatorTool` and `pdf:Producer`,
  each as the raw string plus a **family**, a **kind** and a **version**:
  `Microsoft: Print To PDF` is family *Microsoft Print to PDF*, kind
  *printer_driver*; `Acrobat PDFMaker 25 for Word` is *Acrobat PDFMaker for
  Word*, *office_addin*, version 25
- `ocr_tool` and `scanner` when any of those strings or the XMP edit history
  names one; `pdfa` conformance; the parsed XMP and its edit history

The families are rules in [tool_families.json](tool_families.json), ordered
regular expressions with a version (`tools-1`), and the file's hash is part of
the run signature. They cover 99.5% of the producer and creator strings in
`source/`; the rest are placeholders such as `0` and `(unspecified)`. A
document's producer is the last tool that saved it, so a letter from Word
combined with scans in Acrobat says Acrobat. That is why origin is decided per
page, from the page itself.

### Where things go

Records sit beside each filing's CER record; what a run *is* sits here:

```
output/<id>/
  <id>.cer.meta.json.gz                   scout's record of the filing
  convert/tier0-<sig>/<id>.tier0.json.gz    this stage's record of the PDF

scripts/convert/runs/tier0-<sig>/
  env.json                  what <sig> stands for: code AST hash, every installed
                            package, Python, platform, settings
  code/tier0.<sha12>.py     each distinct script that wrote into this run; a
                            record's code_sha256 names its file
  code/tool_families.<sha12>.json   the tool rules the run used
  sessions/<stamp>.log      one readable line per document
  sessions/<stamp>.jsonl    the same as events, plus session start/end with
                            argv, host, user, git commit and uncommitted files
  parquet/<table>/*.parquet derived by `flatten`; see below
```

`env.json` and `code/` are committed; sessions and Parquet are not.

A log line says what happened and why:

```
ok       4647200    986p   6.52s  [digital:971 scan:14 sparse:1] text[sound:671 suspect:315]  rotated_pages:514 fonts_unmappable_risk:23  why=mupdf_warnings
failed   html          -p   0.00s  why=not_a_pdf
failed   crashme       -p   0.21s  why=worker_crashed  error=worker killed by SIGSEGV
```

Status is `ok`, `partial` (some page or document step failed; the rest is
recorded) or `failed`. The reason codes include `not_a_pdf`, `empty_file`,
`open_failed`, `password_required`, `no_pages`, `repaired_on_open`,
`doc_step_failed:<step>`, `page_step_failed:<step>`, `mupdf_warnings`,
`worker_crashed`, `timeout`, `interrupted` and `internal_error` (a bug in
tier0.py rather than a property of the PDF).

### Resuming, crashes, interrupts

Run the same command again: documents already recorded are skipped unless
their size or mtime changed. `--retry-failed` redoes failures. Each document
runs in a worker process, so a segfault or hang in MuPDF costs that document
only (`--timeout`, default 600 s); workers are replaced every
`--recycle-after` documents. Ctrl-C once finishes the documents in progress;
twice stops them.

### Querying: flatten to Parquet

The records are nested (document, pages, images, ...), which is right for a
record and slow for questions across the corpus. `flatten` derives one table
per kind of row, each column a plain value, in a few seconds:

```bash
scripts/convert/.venv/bin/python scripts/convert/tier0.py flatten           # newest run
```

| table | one row per |
|---|---|
| `documents` | PDF: status, reasons, sha256, metadata, `made_as`, producer / creator / XMP tool family, kind and version, OCR tool, scanner, pages by kind, text layer and origin |
| `pages` | page: geometry, hints including `hint_origin` and its evidence, character classes, directions, images, paths, widgets, timings |
| `images` | image placement: bbox, pixels, DPI, encoding filter, stream sha256 |
| `fonts`, `page_fonts` | font per document; characters per font per page |
| `widgets` | form field: type, name, value, checkbox state, rect |
| `annotations` | annotation: type, text, author, dates, rect |
| `toc` | outline entry: level, title, page |
| `xmp`, `xmp_history` | XMP property (key, value); XMP edit-history event, with the agent's family |
| `embedded_files`, `warnings` | attachment; MuPDF warning |

```sql
SELECT hint_kind, hint_text_layer, count(*)
FROM 'scripts/convert/runs/tier0-<sig>/parquet/pages/*.parquet' GROUP BY ALL;
```

Parquet stores each column compressed and typed, so a query reads only the
columns it names: the corpus questions above take milliseconds. Querying the
JSON directly is possible but slow. Record schema 1 used maps keyed by data
(font names, annotation types), which gave DuckDB a column per distinct key
and ran it out of memory; schema 2 has fixed keys throughout.

## Convert, pass 1: the pages whose text layer can be trusted

```bash
scripts/convert/.venv/bin/python scripts/convert/convert.py run                # every PDF in source/ with a tier 0 record
scripts/convert/.venv/bin/python scripts/convert/convert.py run source/4590700.pdf --workers 1
scripts/convert/.venv/bin/python scripts/convert/convert.py status
```

Each page is routed from tier 0. This pass converts `digital` pages with a
`sound` text layer; every other page gets a `page_not_converted` issue naming
the route it needs (`ocr`, `ocr_with_hidden_vote`, `ocr_untrusted_text_layer`,
...), so the review PDF shows it and nothing is dropped silently.

**Readers.** docling (Heron layout, reading order, TableFormer fast, heading
levels from the outline, numbering and font style; text from the PDF's own
text layer through docling-parse, no OCR). MuPDF reads the same text layer
independently. Form fields and annotations come from tier 0's record.

**Checks.** Every MuPDF word on a converted page must be inside an extracted
item, and inside a cell if it is inside a table. Each item's text and each
cell's must match MuPDF's words in the same box. The comparison ignores
spacing, bullets and line-break hyphens, and keeps `< > . , % -`. A mismatch is
an `error` when a digit differs and a `warning` otherwise. Every page docling
returns must have the PDF's page number and size. Outline entries must have a
heading on their page.

| issue | severity | what it means |
|---|---|---|
| `text_not_extracted` | error | text-layer words under no extracted item |
| `text_not_in_table_cell` | error | words inside a table, in none of its cells: usually a grid TableFormer got wrong |
| `text_disagrees`, `cell_text_disagrees` | error / warning | docling's text for a box differs from MuPDF's for the same box |
| `page_identity_mismatch`, `page_missing_from_output` | error | a page docling returned is not the PDF's page |
| `page_empty`, `converter_error` | error | a page with text and no items; docling failed |
| `picture_text_added` | info | text-layer words inside a picture, added to the document under it |
| `outline_heading_missed` | info | an outline entry with no heading found for it |
| `page_not_converted` | pending | a page for a later pass, with its route |

**Output**, beside the filing's other records:

```
output/<id>/convert/convert-<sig>/
  <id>.convert.json.gz    routes; docling's status, errors, warnings and confidence;
                          checks per page; sha256 of every file below
  <id>.docling.json.gz    the DoclingDocument
  <id>.items.jsonl.gz     one row per item: page, box, reader, grade, agreement with MuPDF
  <id>.issues.json        every issue, with page and box
  <id>.review.pdf         the original with every issue drawn on it
  forms.json              form fields and values
  annotations.json        annotations and their text
```

Boxes are in points, top-left origin, on the page as displayed (rotation
applied), which is what docling uses and a PDF viewer shows. In
`review.pdf`, red is an error, amber a warning, blue information and grey a
page left for a later pass; each annotation's note gives the evidence and the
check. The original PDF is never modified.

The run id covers `convert.py` and `common.py`, docling and everything it
requires, PyMuPDF, the settings, the sha256 of every model file with its
Hugging Face revision, and the tier 0 run it reads.

**Memory.** Docling holds every page of a call until it assembles the
document, so memory grows with the pages converted at once. The first full
run converted each filing in one call; three of the largest (313, 628 and 986
pages) in flight together exhausted RAM, froze WSL and took the machine down.
Now:

- each docling call converts **one page** (`chunk_pages` 1), as legacy
  ingest did, so every status, warning, error, confidence and timing belongs to
  exactly one page, and docling's own warnings (such as table cells it dropped)
  become located `docling_warning` issues. After each page the worker frees the
  result, empties the CUDA cache and hands memory back (`malloc_trim`);
- docling always reads the filed PDF itself, by page range. A one-page copy of
  each page was faster (0.194 s against 0.259 s a page), but docling read a
  filled form's copy differently (201 items and 88 errors against 186 and 6),
  although MuPDF found the copy identical, so the copy is not used;
- 2 workers by default, as legacy `batch.py` had; a worker holding more than
  `--max-worker-ram-gb` (8) after a document is
  replaced; no worker starts while less than `--min-free-ram-gb` (6) is
  available; below `--critical-free-ram-gb` (3) the largest worker is stopped
  and its document retried at the end (`--max-attempts`, 2), as are crashes
  and timeouts;
- every 30 s, `sessions/<stamp>.telemetry.csv` records RAM available, swap,
  each worker's RSS, the documents in flight and the GPU, and a `doc_started`
  event names the worker each document went to, so a stall can be traced.

The three filings that took the machine down now convert together at 3-4 GB
per worker with 34 GB free throughout.

**A page docling cannot parse.** docling's threaded pipeline reports "pipeline
terminated early" when a page never comes back; converted alone, the page says
why ("Page 20 failed to parse."). Every page docling does not finish is tried
once more on its own; if it fails again, MuPDF's text blocks for it go into the
document with their boxes and a `page_from_fallback_reader` warning (no layout,
no tables). Only those pages carry the converter's error, not the whole range.

**Found on the way, and guarded against:** docling-core's
`DoclingDocument.concatenate` renumbers pages to run consecutively, so pages
converted in ranges with gaps between them took the wrong numbers. The
coverage check caught it (1,044 "not extracted" errors on one filing).
`merge_chunks` merges without renumbering, and every page's number and size is
checked against the PDF.

