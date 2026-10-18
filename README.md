# cer-regdocs5

Turning Canada Energy Regulator REGDOCS filings into verifiable, machine-readable evidence, and
learning from them how filings should be made in future.

- **What we found:** [docs/ceo-report.md](docs/ceo-report.md) (the report for the CEO) and
  [docs/filing-guidance.md](docs/filing-guidance.md) (the guidance, area by area, with its evidence).
- **What we learned building it:** [docs/lessons-across-pipelines.md](docs/lessons-across-pipelines.md),
  [docs/lessons_learned.md](docs/lessons_learned.md), [scripts/convert/LESSONS.md](scripts/convert/LESSONS.md).
- **Every document:** [docs/README.md](docs/README.md).

## From REGDOCS to evidence, step by step

Run on Linux or WSL, from the repository root. Each tool sets up its own Python environment on first
use (`scripts/scout/.venv`; `~/.venvs/regdocs-convert` for convert, about 7 GB with torch). Convert
expects an NVIDIA GPU.

### 1. Scout: find the filings and record what REGDOCS says about them

```bash
./scripts/scout/scout.sh enrich --from 2026-10-01 --to 2026-10-31   # records + filings + facets; resumes
./scripts/scout/scout.sh status                                      # progress, offline
./scripts/scout/scout.sh doc 4647200 4692360                         # record specific documents
```

Records go to `output/<id>/<id>.cer.meta.json.gz`; checkpoints to `scripts/scout/scout-state/`.
Requests go one at a time, about a second apart. Details: [docs/scout.md](docs/scout.md).

### 2. Download: fetch the PDFs

```bash
./scripts/scout/scout.sh download                                    # every recorded PDF not yet in source/
./scripts/scout/scout.sh download 4647200 https://apps.cer-rec.gc.ca/REGDOCS/Item/View/4692360
./scripts/scout/scout.sh download --list ids.txt                     # ids or links, one per line
```

Files go to `source/<id>.pdf` and are never downloaded twice. Empty files, web pages and "not found"
answers are recorded on the document's record and not retried.

### 3. Convert: read every page

```bash
./scripts/convert/convert.sh run --all --light --workers 8           # light: text + OCR where needed, every PDF
./scripts/convert/convert.sh run --sample ids.txt --light            # only these documents
./scripts/convert/convert.sh run 4647200 --force                     # full mode (layout, tables, figures), redo
./scripts/convert/convert.sh run --all --light --reuse-ocr --force   # reassemble from saved OCR, no GPU
```

- **Light mode** reads text and how each page was made; it is what the whole corpus has been through.
  **Full mode** (no `--light`) adds layout, tables and figures, and is much slower; a full run replaces
  a document's light outputs.
- Outputs go to `output/<id>/convert/`: the ledger (`pages.json`), every word (`words.parquet`), every
  element and table cell with a stable id (`elements.parquet`, `cells.parquet`: `<id>:p<page>:e<n>`), the
  structure (`docling.json.gz`), the text (`.md`) and `manifest.json` (each file's SHA-256, the PDF's
  and the code's). Each ledger names the code that wrote it and says whether it is complete.
- Finished documents are skipped, so a stopped run resumes. Run long jobs detached and keep their logs:
  `setsid nohup ./scripts/convert/convert.sh run --all --light --workers 8 >> scripts/convert/runs/v2/logs/<name>.log 2>&1 &`
- The GPU is shared with everything else on the machine: a game or another GPU program slows every
  page down (see [LESSONS.md](scripts/convert/LESSONS.md)).
- One PDF's capture problems, page by page, without converting: `./scripts/convert/convert.sh check some.pdf`.

### 4. Measure: the catalog, the census and the integrity pass

```bash
./scripts/convert/convert.sh catalog build                           # every ledger into output/catalog.duckdb (~80 min)
~/.venvs/regdocs-convert/bin/python scripts/convert/integrity.py     # signatures, PDF/A, tags per file (~4 h; resumes)
~/.venvs/regdocs-convert/bin/python scripts/convert/census.py --out scripts/convert/runs/v2/reports/<date>-census.md
./scripts/convert/convert.sh catalog sql "SELECT capture, count(*) FROM pages GROUP BY 1"
```

The catalog and `output/integrity.parquet` are derived: delete and rebuild them at any time.

## Layout

```
source/<id>.pdf                          the PDFs as REGDOCS served them
output/<id>/<id>.cer.meta.json.gz        what REGDOCS says about each document (scout)
output/<id>/convert/                     the converted document and its ledger (convert)
output/catalog.duckdb, integrity.parquet indexes over everything (rebuilt, never edited)
scripts/scout/                           scout and download
scripts/convert/                         convert, check, catalog, census, integrity
scripts/convert/runs/v2/                 run identity, logs, reports, redo lists
docs/                                    findings, guidance, lessons, the CEO report and deck
```

`source/`, `output/` and the scout's checkpoints are not in git; everything under `scripts/` and
`docs/`, including run logs and reports, is.

Tests: `./scripts/scout/scout.sh test`.
