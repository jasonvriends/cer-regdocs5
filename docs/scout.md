# REGDOCS scout

`scripts/scout/scout.py` finds CER REGDOCS PDF records, records their metadata,
optionally enriches them with filing/container relationships and REGDOCS
facets, and can download the PDFs. `scripts/scout/scout_crawl.py` supplies the
resumable crawl.

The scout uses files rather than a persistent database:

- `output/<id>/<id>.cer.meta.json.gz` is the authoritative metadata record
  (gzipped JSON).
- `source/<id>.pdf` is the downloaded PDF.
- `scripts/scout/scout-state/<from>_<to>/` contains resumable crawl checkpoints.
- DuckDB is used only as an in-memory work queue while a command is running.

## Setup

Run on Linux or in WSL. The crawler uses POSIX file locking (`fcntl`), so native
Windows Python is not supported without changing the locking implementation.

```bash
chmod +x scripts/scout/setup.sh
./scripts/scout/setup.sh
.venv/bin/python scripts/scout/scout.py --help
```

The machine needs Python 3.10 or newer and network access to
`https://apps.cer-rec.gc.ca`.

## Discover, enrich, and download

`enrich` includes discovery, so it is the usual command when complete metadata
is wanted:

```bash
.venv/bin/python -u scripts/scout/scout.py enrich --from 2026-09-01 --to 2026-09-30
.venv/bin/python scripts/scout/scout.py status
.venv/bin/python -u scripts/scout/scout.py download
```

Use `discover` when only search-result metadata is needed:

```bash
.venv/bin/python -u scripts/scout/scout.py discover --from 2026-09-01 --to 2026-09-30
```

Neither `discover` nor `enrich` downloads PDFs. `download` fetches every
recorded PDF that is missing from `source/`; an existing PDF is not downloaded
again. It paces, retries and stops exactly as `enrich` does: only network
errors and temporary HTTP errors are retried, and five documents failing in a
row stop the run. Rerun the same command to continue.

## Resume and refresh

Run the identical command again after a crash or interruption. It resumes from
the JSON checkpoints. Exit code 0 means complete, 1 means work remains or a
request failed, 2 means invalid arguments, and 130 means interrupted.

```bash
.venv/bin/python -u scripts/scout/scout.py enrich --from 2026-09-01 --to 2026-09-30
```

Do not add `--refresh` while resuming. Use it only to discard a completed
range's crawl checkpoints and query REGDOCS again; existing records and PDFs
are retained and metadata changes are recorded.

```bash
.venv/bin/python -u scripts/scout/scout.py discover --from 2026-09-01 --to 2026-09-30 --refresh
.venv/bin/python -u scripts/scout/scout.py enrich   --from 2026-09-01 --to 2026-09-30
```

Confirmed REGDOCS container gaps are reused for 30 days and then become
eligible for rechecking. Clean completed container reads are reused across date
ranges. Use `--max-requests N` to stop cleanly after a request budget and rerun
the same command to continue.

## Other commands

```bash
.venv/bin/python scripts/scout/scout.py status
.venv/bin/python scripts/scout/scout.py doc 4642410 4647200
.venv/bin/python scripts/scout/scout.py missing
.venv/bin/python scripts/scout/scout.py download --limit 100
```

- `status` reads saved progress without contacting REGDOCS.
- `doc` records specified REGDOCS document IDs.
- `missing` adds metadata for PDFs already in `source/` that lack a record.
- `download --limit N` downloads at most N outstanding PDFs.

Only one command that writes records can run at a time. Requests are sequential
and politely paced; retryable failures slow the request rate automatically.

For unattended output that can be tailed:

```bash
nohup .venv/bin/python -u scripts/scout/scout.py enrich \
  --from 2026-09-01 --to 2026-09-30 > scout-enrich.log 2>&1 &
tail -f scout-enrich.log
```
