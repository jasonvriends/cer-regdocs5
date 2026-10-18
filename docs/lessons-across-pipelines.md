# Six pipelines over REGDOCS: what each tried, and what they teach together

Between July and October 2026 the same goal - turn Canada Energy Regulator REGDOCS filings into
trustworthy, machine-readable evidence - was attempted six times. This page reads them side by side.
Detailed lessons live in each pipeline's own documents; the consolidated, stage-by-stage list is
[lessons_learned.md](lessons_learned.md), and the current converter's in
[scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md).

## The six

| | when | what it was | scale reached | kept |
|---|---|---|---|---|
| **cer-regdocs1** | Jul 10-22 | end-to-end prototype: scout, download, Docling to Markdown, Chroma vector index, Ollama Q&A, a Gradio app (trends, compliance gaps, monitoring-report extraction) | a working app over the documents it converted | SQLite state machine and audit history; SHA-256 per download; Docling in a subprocess so a crash kills only the worker; per-item page and box sidecars; a lossless Docling export so re-chunking needs no GPU; a numeric "verify" against the text layer |
| **cer-regdocs2** ("REGDOCS Atlas") | Aug 7-18 | Azure Content Understanding as the extractor, Docling as comparison; Azure AI Search; Foundry answers with citations; model-extracted entities, events, claims and obligations; Terraform deployment | 8,213 documents, 90,484 pages through Azure (about $500, $5.53 per 1,000 pages) | the Azure-vs-Docling evaluation; "model-derived records stay marked unreviewed"; page-range splitting for very large files |
| **cer-regdocs3** | ~Sep 12 (local, never pushed) | an inventory of what each PDF contains (letters, schedules, tables, maps) by local models, every claim tied to a verbatim quote | a 10-call pilot | quote verification of model output; a pending queue separate from run status |
| **cer-regdocs4** | Sep 19-23 | Docling tuned to Azure-level accuracy on the hardest filing, page by page | 4,296 filings, 16,085 pages compared with a commercial extraction; the first filing guidance | per-page choice of reader; a row for every page, not only events; the rejection of generative OCR |
| **cer-regdocs5, v1** | Oct 10 | profile every page first (tier 0), then convert; a 5,000-document capture survey | the survey; the stage-by-stage lessons | the survey's rates; tier 0's facts per page |
| **cer-regdocs5, v2** (current) | Oct 11-18 | an evidence ledger: words from the text layer or OCR are the evidence, models only draw boxes; light pass over everything | **all 269,025 documents, 3.6 million pages, 1.2 billion words**; a census; an integrity pass | everything above that survived, now measured on the whole corpus |

## What each found

**cer-regdocs1 - the app came before the evidence.** Questions could be asked of the corpus within
days, but nothing said whether the text behind an answer was right; `verify` measured only whether
the text layer's numbers reached the Markdown, and scans were "unverifiable". It found that REGDOCS
HTML documents point at images the public cannot fetch (a Content Server permission setting), and
that a vector store's unbounded query fails past 275,000 chunks.

**cer-regdocs2 - the cloud is faster and finishes; nothing is ground truth.** Azure completed every
document including the 986-page 4647200 (split into 300-page ranges), 4.5 times faster than Docling,
with exact page counts; Docling missed pages on 125 documents and quarantined nine. But Azure
reported zero warnings on content it got wrong, and Docling occasionally read the text better. Its
recommendation - a quality-gated hybrid, and **about 100 manually transcribed pages as ground
truth** - is still the right one, and the ground truth has still not been made.

**cer-regdocs3 - schema-valid is not correct.** Local models listed a document's contents and
quoted their evidence; structurally valid output still mislabelled forms, repeated report headers as
items, and listed instruments a page only mentioned. Category names needed people to reconcile.
It stopped at a pilot and was never committed.

**cer-regdocs4 - the worst failures are silent, and generative models invent.** On 4647200, the
first run reached 70% coverage and 316 pages were badly broken **with no warning at all**; per-page
choices raised it to 99%. A vision model asked to read a page invented e-mail addresses, a street
number and a postal code that looked entirely clean. The pipeline's own flags caught 35 of 67 weak
pages, because it recorded events, not every page. Its filing guidance found 14.7% of pages with a
text layer worse than OCR.

**cer-regdocs5 v1 - know each page before reading it.** A tier-0 profile (size, images, ruling lines,
text-layer health) predicts the pages that crash or hang. The 5,000-document survey gave the first
corpus rates: 23.2% of documents mostly not digital.

**cer-regdocs5 v2 - count, don't estimate.** The light pass read every page of every document; the
census confirmed the survey to the decimal (23.2%) and added what a sample could not show: by year,
tool, producer and document type; scanner OCR layers that differ from an independent reading on
67% of their pages; 313 documents with words under cover-up boxes (real leaks among those checked:
landowners' names and addresses, contacts on a page marked "redacted for privacy"); files that are
empty, web pages or a desktop shortcut.

## Lessons common to all six

1. **Silent loss is the main risk, and every tool has it.** Docling (316 pages, no warning), Azure
   (no warning on wrong content), our own checks (100% conservation on every document, which proves
   nothing was lost between steps, not that anything was read right). Only a second, independent
   reading exposes it.
2. **There is still no ground truth.** Every pipeline measured against another machine: the text
   layer, Azure, OCR. cer-regdocs2 asked for 100 hand-transcribed pages in August; none exist. Until
   they do, accuracy claims are agreement claims.
3. **Generative models may vote, never supply the value.** Fabrication in cer-regdocs4; "unreviewed"
   in cer-regdocs2; verbatim quotes in cer-regdocs3; voters only in v2. The pattern converged
   independently four times.
4. **Choose per page, not per corpus.** Every fixed setting right for one filing was wrong for the
   next (cer-regdocs4, v2): what a page is decides how it is read.
5. **Isolate every conversion and bound it by progress, not by the clock.** Subprocess per document
   (1), quarantine (2), one page per pass (4), stall limits and replaced workers (v2). Native crashes,
   hangs and memory blow-ups come from a small set of files, and the same files for every tool.
6. **Record every page, not just the events.** A pipeline that writes down only what went wrong
   cannot tell "fine" from "not noticed" (cer-regdocs4: 35 of 67). The ledger now has a row for every
   page.
7. **Name what produced every output.** Hash the input (all), the code and the models (4, v2). v2
   ran for days writing ledgers that said only "v2", and half the corpus was assembled by an earlier
   version.
8. **Build the evidence before the app.** cer-regdocs1 and 2 built search, answers and dashboards on
   text nobody had measured; the later pipelines spent their time making the text trustworthy.
   Answers are only as good as the extraction under them, and that has to be shown, not assumed.
9. **The problems start at the source.** Every pipeline rediscovered the same filings: scans of
   digital documents, signatures printed and scanned, stub layers, oversize drawings, cover-up
   redactions, files that are not documents. Better extraction recovers some of it at a cost; better
   filings would remove it. That is the case for [filing-guidance.md](filing-guidance.md).
10. **Lessons are relearned unless they live in code.** v2 rediscovered a game sharing the GPU and
    the need to hash the code - both already written in the lessons of earlier pipelines. A lesson
    in a document is advice; a check in the pipeline is a rule. Read the lessons before building,
    and turn each into a test or a guard.

## What a seventh attempt should not repeat

- Start from the evidence ledger, the census and the catalog; do not rebuild scout or download.
- Make the 100-page ground truth before claiming accuracy.
- Keep generative models as voters, with verbatim grounding.
- Give every output its code hash, and every page a row.
- Change the filings, not only the extractor: the rest of the error rate is set before the file
  reaches the CER.
