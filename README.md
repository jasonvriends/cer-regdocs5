# cer-regdocs5

Tools for turning Canada Energy Regulator REGDOCS filings into machine-readable
evidence.

| tool | what it does | docs |
|---|---|---|
| [scripts/scout/](scripts/scout/) | finds REGDOCS records, saves their metadata and downloads the PDFs | [docs/scout.md](docs/scout.md) |
| [scripts/convert/](scripts/convert/) | profiles every PDF page, then converts each PDF to text, tables and form fields with page-level provenance | [scripts/convert/README.md](scripts/convert/README.md) |

Background: [docs/lessons_learned.md](docs/lessons_learned.md) and
[docs/tools.md](docs/tools.md).

Both tools work relative to the repository root:

```
source/<id>.pdf                           downloaded PDFs (scout)
output/<id>/<id>.cer.meta.json.gz         REGDOCS metadata (scout)
output/<id>/convert/tier0-<sig>/          what every page is (convert, step 1)
output/<id>/convert/convert-<sig>/        the converted document (convert, step 2)
scripts/scout/scout-state/                scout's resumable crawl checkpoints
scripts/convert/runs/                     convert's run provenance and logs
```

Run on Linux or WSL. Each tool's tests sit beside it:

```bash
.venv/bin/python -m unittest discover -s scripts/scout/tests
scripts/convert/.venv/bin/python -m unittest discover -s scripts/convert/tests
```
