# REGDOCS: documents

What this project is for: learning how Canada Energy Regulator filings should be **captured** -
produced, signed, scanned, redacted, filed - so they arrive as faithful digital documents.

| document | what it is |
|---|---|
| [filing-guidance.md](filing-guidance.md) | **the findings**: what filers and intake checks should do, each item with its measured evidence |
| [digital-first-filing.md](digital-first-filing.md) | **the target**: what a digital-first filing looks like, requirement by requirement, each tied to a measured failure |
| [lessons_learned.md](lessons_learned.md) | what five earlier pipelines (cer-regdocs1-5) learned about the corpus, extraction and quality checks |
| [../scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md) | what the current converter has measured and fixed, round by round |
| [../scripts/convert/DESIGN.md](../scripts/convert/DESIGN.md) | how the converter (the measuring instrument) works, and why |
| [scout.md](scout.md) | finding, recording and downloading REGDOCS filings |
| [tools.md](tools.md) | PDF extraction tools others use |

The code is in `scripts/scout/` and `scripts/convert/`; the filings in `source/`, what is known
about each in `output/<id>/`.
