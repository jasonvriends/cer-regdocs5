# convert

**What this is for:** learning how filings should be captured so future filings are faithful
digital documents - see [FILING-GUIDANCE.md](FILING-GUIDANCE.md), measured with
[capture.py](capture.py) on every round's samples.

It turns REGDOCS filings into evidence: every word on every page with its box and the reader that
read it, structure (headings, paragraphs, tables, pictures, forms) laid over those words, and a
per-page ledger of what is still missing. [DESIGN.md](DESIGN.md) is the approach and why;
[LESSONS.md](LESSONS.md) and [LESSONS-CORPUS.md](LESSONS-CORPUS.md) are the evidence behind it.

```bash
./scripts/convert/convert.sh run 4738464 90495                 # by REGDOCS id (PDF in source/)
./scripts/convert/convert.sh run --sample scripts/convert/samples/recent-10.txt
./scripts/convert/convert.sh status                             # issues and todo across converted documents
./scripts/convert/convert.sh report --sample scripts/convert/samples/early-10.txt
./scripts/convert/convert.sh vote bench --sample scripts/convert/samples/random-100-a.txt   # measure voters
./scripts/convert/convert.sh vote run --model qwen3-vl:8b-instruct --sample ...           # then run again to apply
```

The first run installs the environment in `~/.venvs/regdocs-convert` (torch with CUDA; a GPU is
expected). Models live in `scripts/convert/models/`.

| file | what it is |
|---|---|
| [convert.py](convert.py) | the pipeline per page and document, the ledger, the worker, `run` / `status` / `report` |
| [readers.py](readers.py) | the readers: MuPDF words and page facts, RapidOCR, four layout models, TableFormer, MuPDF tables, the figure classifier |
| [assemble.py](assemble.py) | readings to a document: evidence words, merged regions, words to regions, grid choice, DoclingDocument, Markdown |
| [capture.py](capture.py) | how each page was captured (digital, scan, stub layer, text as curves, ...) by producer, year and document type: the evidence for FILING-GUIDANCE.md |
| [vote.py](vote.py) | vision models (Ollama) as voters: `bench` measures them against the text layer, `run` votes where two OCR readings differ |
| [ocr_bench.py](ocr_bench.py) | which RapidOCR models read best, against sound text layers |
| [tool_families.json](tool_families.json) | producer/creator strings to tool families (what made the PDF) |
| `samples/`, `survey/` | test sets: first 20 (2002 and Oct 2026), random by year, by document type |
| `runs/<version>/run.json` | run identity: packages, model weight hashes, settings |

Output, per document, in `output/<id>/convert/`: `words.parquet` (every word of every reader),
`regions.parquet` (every layout detection and the merged regions), `tables.parquet` (every table
reading and its score), `pages.json` (the ledger), `docling.json.gz` (DoclingDocument), `<id>.md`.

Reading `docling.json.gz`: text inside pictures (labels on drawings, map text) sits under the
picture item, and page headers and footers in the furniture layer. Iterate with
`doc.iterate_items(traverse_pictures=True, included_content_layers=set(ContentLayer))` or that
text is skipped. Each item's `meta` names its evidence words (`regdocs__words`, ids into
`words.parquet`), its layout region and the reader behind it.
