# REGDOCS scout

`scripts/scout/scout.py` finds CER REGDOCS PDF filings, records what REGDOCS says about each
one, enriches the record with its filing memberships and REGDOCS facets, and downloads the PDFs.

State is files, not a database:

- `output/<id>/<id>.cer.meta.json.gz` - the record: what REGDOCS says about the document, when it
  said it, and what changed since. The only copy; the download step adds the file's hash to it.
- `source/<id>.pdf` - the file itself, once downloaded.
- `scripts/scout/scout-state/<from>_<to>/` - checkpoints of a date range's crawl, one JSON file
  per job (a search, or a filing's member list), so a run resumes where it stopped.

## Running

Linux or WSL (file locking uses `fcntl`), Python 3.10+, network access to apps.cer-rec.gc.ca.
`scout.sh` creates `scripts/scout/.venv` on first use.

```bash
./scripts/scout/scout.sh discover --from 2026-09-01 --to 2026-09-30   # list and record PDFs
./scripts/scout/scout.sh enrich   --from 2026-09-01                   # + filings and facets (--to: today)
./scripts/scout/scout.sh status                                        # progress, offline
./scripts/scout/scout.sh download [--limit N]                          # PDFs not yet in source/
./scripts/scout/scout.sh doc 4647200 4692360                           # specific documents
./scripts/scout/scout.sh missing                                       # PDFs in source/ without a record
./scripts/scout/scout.sh test
```

`enrich` includes discovery and is the usual command. Neither downloads; `download` fetches every
recorded PDF missing from `source/`, newest first, and never downloads a file twice.

## Resuming, refreshing, failing politely

Run the same command again to resume. `--refresh` starts a range over (records and PDFs are kept;
changes are recorded). `--max-requests N` pauses cleanly after about N requests. Exit codes: 0
done, 1 work remains or a request failed, 2 bad arguments, 130 interrupted.

Requests go one at a time, about a second apart, slowing down on errors and honouring
Retry-After; five requests failing in a row stop the run. Only one command that writes records
runs at a time. A filing's member list read by one range is reused by another; a filing REGDOCS
lists short or cannot locate is recorded as a gap and rechecked after 30 days.

Downloads that REGDOCS answers about that one file are remembered in the record and not retried:
a 404 (`download.unavailable`, retried with `--retry-unavailable`) and an item REGDOCS lists as a
PDF but serves as an HTML page (`download.excluded`, never retried). Fifty such answers in a row
stop the run, in case the download URLs themselves changed.

## Facets before 2012

Records for documents filed before 2012 came from an earlier pipeline's seed and had no facets;
REGDOCS does have them (enriching a 2010 day returned full facets). Run `enrich` over a range to
fill them in; it resumes per range.
