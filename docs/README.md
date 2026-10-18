# REGDOCS: documents

What this project is for: a verifiable, machine-readable extract of every REGDOCS filing, and learning from it
how Canada Energy Regulator filings should be **captured** - produced, signed, scanned, redacted, filed - so
they arrive as faithful digital documents.

| document | what it is |
|---|---|
| [ceo-report.md](ceo-report.md) + [ceo-deck/](ceo-deck/) | **the report and deck for the CEO**: what 269,025 filings taught us, and the decisions needed |
| [lessons-across-pipelines.md](lessons-across-pipelines.md) | six pipelines over REGDOCS side by side, and the lessons common to all |
| [cer-filing-rules-today.md](cer-filing-rules-today.md) | the CER's current e-filing rules beside what the corpus shows |
| [output-and-sidecars.md](output-and-sidecars.md) | what to keep in output/, what a full scan adds, which sidecars to make |
| [filing-guidance.md](filing-guidance.md) | **the findings**: what filers and intake checks should do, each item with its measured evidence |
| [digital-first-filing.md](digital-first-filing.md) | **the target**: what a digital-first filing looks like, requirement by requirement, each tied to a measured failure |
| [findings.jsonl](findings.jsonl) + [catalog.md](catalog.md) | every finding as a queryable record, and the DuckDB catalog of everything measured: how to browse and add to them |
| [lessons_learned.md](lessons_learned.md) | what five earlier pipelines (cer-regdocs1-5) learned about the corpus, extraction and quality checks |
| [../scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md) | what the current converter has measured and fixed, round by round |
| [../scripts/convert/DESIGN.md](../scripts/convert/DESIGN.md) | how the converter (the measuring instrument) works, and why |
| [scout.md](scout.md) | finding, recording and downloading REGDOCS filings |
| [tools.md](tools.md) | PDF extraction tools others use |

The code is in `scripts/scout/` and `scripts/convert/`; the filings in `source/`, what is known
about each in `output/<id>/`.
