# REGDOCS: documents

What this project is for: a verifiable, machine-readable extract of every REGDOCS filing, and learning from it
how Canada Energy Regulator filings should be **captured** - produced, signed, scanned, redacted, filed - so
they arrive as faithful digital documents.

## Start here: three things to read

| read | what you get | length |
|---|---|---|
| 1. [ceo-deck/Getting our filings ready for AI.pptx](ceo-deck/) | the story in 15 slides, for executives | 15 min |
| 2. [ceo-report.md](ceo-report.md) | the same story with the numbers: what 269,025 filings taught us, ranked recommendations, decisions needed | 20 min |
| 3. [filing-guidance.md](filing-guidance.md) | **the main finding**: what filers and the CER's intake should do, each item with its measured evidence | 45 min |

Everything below is supporting material: open it when a question needs it.

## If you need more

| document | open it when you want |
|---|---|
| [corpus/](corpus/) | how big the record is: documents by size, the 25 largest, every document over 500 pages |
| [digital-first-filing.md](digital-first-filing.md) | the target state: a digital-first filing, requirement by requirement |
| [cer-filing-rules-today.md](cer-filing-rules-today.md) | the CER's current e-filing rules beside what the corpus shows |
| [output-and-sidecars.md](output-and-sidecars.md) | what each converted document's files are, and what to keep |
| [../scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md) | the converter's lab notebook: what was measured and fixed, round by round (October 2026, current) |

## Background and history

| document | what it is |
|---|---|
| [lessons-across-pipelines.md](lessons-across-pipelines.md) | the six attempts at this (July-October 2026) side by side |
| [lessons_learned.md](lessons_learned.md) | September 2026 snapshot: what the earlier pipelines learned from ~8,000-document samples; not updated |
| [findings.jsonl](findings.jsonl) + [catalog.md](catalog.md) | every finding as a record, and how to query the DuckDB catalog |
| [../scripts/convert/DESIGN.md](../scripts/convert/DESIGN.md) | how the converter works, and why |
| [scout.md](scout.md), [tools.md](tools.md), [scout-gaps.jsonl](scout-gaps.jsonl) | finding and downloading filings; extraction tools others use |
| ceo-deck/*.html, deck.json | the earlier online slide version of the deck (the .pptx replaces it) |

The code is in `scripts/scout/` and `scripts/convert/`; the filings in `source/`, what is known
about each in `output/<id>/`. How to run it all: the [README](../README.md) at the top of the repo.
