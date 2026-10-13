# Browsing what has been learned

Everything measured is kept in files - each document's ledger and Parquet files under
`output/<id>/convert/`, the scout records, the survey, the bench results - and two indexes make it
browsable and reusable:

- **[findings.jsonl](findings.jsonl)** - every finding as a record: the claim, the numbers behind
  it, where the evidence is, its strength, and its status (`confirmed`, `corrected`, `wrong`,
  `proposed`, `open`). A finding that turned out wrong stays, marked `wrong`, with `corrected_by`
  naming the finding that replaced it and `why_wrong` saying what misled it. The prose versions
  are [filing-guidance.md](filing-guidance.md), [lessons_learned.md](lessons_learned.md) and
  [../scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md).
- **`output/catalog.duckdb`** - a DuckDB database rebuilt from all of the above, never edited by
  hand. Delete it any time; `catalog build` makes it again.

## Build, browse, query

```bash
./scripts/convert/convert.sh catalog build                       # every converted document
./scripts/convert/convert.sh catalog build --sample scripts/convert/samples/random-100-a.txt
./scripts/convert/convert.sh catalog ui                          # DuckDB's web UI, http://localhost:4213
./scripts/convert/convert.sh catalog sql "SELECT * FROM findings WHERE topic = 'signatures'"
```

| table | one row per |
|---|---|
| `documents` | converted document: REGDOCS facets (live from the scout record), producer and tool kind, mode (full / light), words, checks, main capture class |
| `pages` | page: text layer verdict, capture class, size, orientation, words by reader, regions, tables, pictures, checkboxes, todo tags |
| `issues` | issue: kind, severity, page, evidence |
| `survey` | page checked in the 5,000-document capture survey |
| `findings` | finding (from findings.jsonl) |
| `samples` | test document and the sample it belongs to |
| `benches` | bench result (voters, OCR models, drawings), as JSON |
| `words`, `regions`, `table_readings`, `votes` | views over the Parquet files (sample builds only) |

Across the whole corpus, query the Parquet files directly rather than through views (opening
268,000 files is slow, so do it for a question, not for every build):

```sql
SELECT reader, count(*) FROM read_parquet('output/*/convert/*.words.parquet') GROUP BY reader;
```

## Questions it answers

```sql
-- every finding a survey corrected, and what misled the first one
SELECT w.id, w.finding, w.why_wrong, c.finding AS corrected_to
FROM findings w JOIN findings c ON c.id = w.corrected_by WHERE w.status = 'wrong';

-- capture class by REGDOCS document type
SELECT document_type, count(*) docs,
       avg(CASE WHEN main_capture IN ('scan_no_layer','scanner_ocr','stub','suspect','text_as_curves')
                THEN 1 ELSE 0 END) AS share_not_digital
FROM documents WHERE document_type IS NOT NULL GROUP BY ALL ORDER BY docs DESC;

-- what is still to do, by kind of page
SELECT unnest(todo) AS todo, count(*) FROM pages GROUP BY ALL ORDER BY 2 DESC;

-- which tool made the scanned pages
SELECT producer, count(*) FROM pages p JOIN documents d USING (doc_id)
WHERE capture = 'scan_no_layer' GROUP BY ALL ORDER BY 2 DESC;
```

## Adding a finding

Append one line to `findings.jsonl` (next free id; date; area: capture, converter, scout,
method, submission; a topic; the finding in a sentence; the evidence with its numbers; the source
path; status and strength). When a finding is overturned, keep it: set `"status": "wrong"`,
`"corrected_by"` and `"why_wrong"`, and give the replacement `"supersedes"`.
