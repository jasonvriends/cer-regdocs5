# CER REGDOCS document extraction: lessons learned

> **A September 2026 snapshot.** This page is what the earlier pipelines (cer-regdocs1-4 and v1 of
> this one) learned, from samples of about 8,000 documents, most of them filed in 2026. It is not
> updated any more. What the whole corpus showed in October 2026 - all 269,025 documents, 2002 to
> 2026 - is in [filing-guidance.md](filing-guidance.md) and [ceo-report.md](ceo-report.md); what
> the current converter measured and fixed is in
> [scripts/convert/LESSONS.md](../scripts/convert/LESSONS.md).

What has been learned about turning Canada Energy Regulator REGDOCS filings into
trustworthy, machine-readable evidence. Lessons are grouped by pipeline stage:

| stage | question it answers |
|---|---|
| [1. Scout](#1-scout) | which filings exist, and what does REGDOCS say about them? |
| [2. Enrich](#2-enrich) | which facets, filings and containers does each document belong to, and is it worth extracting? |
| [3. Download](#3-download) | how do we fetch and store the files safely? |
| [4. Profile](#4-profile) | what is each page, before we try to read it? |
| [5. Document conversion](#5-document-conversion) | how do we turn each page into faithful text, tables and structure? |
| [6. Quality checks and review](#6-quality-checks-and-review) | how do we know the conversion is right without ground truth? |
| [7. Operations](#7-operations) | how do we keep long runs alive, fast and reproducible? |
| [8. Search and analysis](#8-search-and-analysis) | how do we make the converted text useful? |

Supporting sections: [key findings](#key-findings), [the corpus](#the-corpus),
[recommendations for future filings](#recommendations-for-future-filings),
[method](#method-how-to-not-fool-yourself), [test documents](#test-documents).

Numbers come from measured runs on real filings. Findings that were later
reversed are recorded with their corrections.

---

## Key findings

1. **The worst failures are silent.** 316 pages of one filing came out as
   mojibake with no warning from Docling; Azure reported zero warnings on the
   same 986-page filing, including for content it got wrong. An empty warning
   list means nothing was *reported*, never that nothing was lost.
2. **The PDF text layer is the most reliable reader when it is sound.** Take
   text from the text layer and use structure readers (Docling, Azure) as an
   overlay for layout and tables.
3. **The text layer can lie, and that is cheap to detect** from the file alone,
   before conversion (mojibake, scans, digit-mojibake, redactions).
4. **Choose per page and per table by measured result, not by a global
   setting.** Every fixed setting that was right for one filing was wrong for
   the next.
5. **Generative vision models invent plausible values.** They may vote; they
   may not be the source of a regulator's number.
6. **No single table reader wins.** Score each reading by words placed in the
   right cell.
7. **Metrics can be blind to what they are meant to catch.** Check for text
   added as well as lost, and look at the page.
8. **Small samples of a skewed corpus mislead.** Median filing: 2 pages.
9. **Isolate every conversion in its own process with hard limits.**
10. **A redaction drawn over text is not a redaction.** The covered words stay in the text layer
    and anyone can copy them out: 4710294, 13,159 words on 36 pages (confirmed again by the
    current converter, 2026-10-12). Detect it by a dark fill that renders flat *and* real words
    beneath it - a lone glyph on a dark box is a ticked checkbox, not a redaction.
11. **Careful local extraction beat the cloud service** on 113 test documents
    (99.9% vs 84.2% of visible words), but the cloud service is still ahead on
    text inside drawings, checkboxes and barcodes.

---

## The corpus

**These lessons rest on a sample, not the whole record.** The first table counts what REGDOCS listed
in September 2026; only the 8,210 sample documents were downloaded and read, nearly all from 2016-2026.
(The October 2026 pass read all 269,025: see [corpus/](corpus/) and the
[census](../scripts/convert/runs/v2/reports/2026-10-18-census.md).)

| | |
|---|---:|
| documents listed in REGDOCS, 2002 to Sept 2026 | **265,239** |
| companies named on a filing | 1,774 |
| PDFs downloaded (the sample) | 8,210 |
| PDFs converted | 8,153 (51 pending, 6 could not be) |
| pages converted and checked | 83,456 |

Documents listed per year in REGDOCS - all filings, not the sample (2026 to 24 September):

| year | documents |
|---|---:|
| 2002 | 4,090 |
| 2003 | 2,445 |
| 2004 | 2,381 |
| 2005 | 3,583 |
| 2006 | 6,420 |
| 2007 | 5,354 |
| 2008 | 6,955 |
| 2009 | 6,727 |
| 2010 | 8,108 |
| 2011 | 15,772 |
| 2012 | **18,979** |
| 2013 | 13,956 |
| 2014 | 16,792 |
| 2015 | 17,628 |
| 2016 | 11,618 |
| 2017 | 16,679 |
| 2018 | 17,101 |
| 2019 | 13,907 |
| 2020 | 12,268 |
| 2021 | 12,362 |
| 2022 | 10,076 |
| 2023 | 9,599 |
| 2024 | 13,831 |
| 2025 | 11,073 |
| 2026 | 7,535 |

- The sample is concentrated in 2026 (748 in January rising to 1,232 in June),
  with a thin tail back to 2016. **It describes recent filings best**; older
  filings are probably more scan-heavy (unmeasured).
- **Median document: 2 pages. Mean: 11.** Only 71 of 8,210 (0.9%) exceed 200 pages.
- Largest filers: NGTL (867), Westcoast Energy Inc. (629), Trans Mountain (623),
  Enbridge Pipelines (586), NOVA Gas Transmission (308), Trans-Northern (226),
  TransCanada PipeLines (217), Westcoast Energy LP (213), Powell River Energy
  (197), SaskPower (176).
- Document types: Receipt 1,586 · Letter 1,525 · Other 1,068 · Supplemental
  Information 859 · Order 506 · Application 498 · Notification of Operation and
  Maintenance Activities 451 · IR Response 421 · Compliance to an Order or
  Certificate 333 · Compliance after a Board Order 212 · Information Request 99 ·
  Letter of Comment 81.

What the converted sample contains:

| | |
|---|---:|
| documents / pages | 8,158 / 83,903 |
| tables | 40,387 (1,047 tables of contents) |
| data tables (≥50% numeric body cells, ≥20 of them) | 4,138 |
| pictures and drawings | 45,789 |
| headings | 78,240 |
| information-request items | 1,056 in 196 documents |
| order numbers | 380 (192 English) |

- Data tables by document type: Other 1,586; Supplemental Information 1,055;
  Application 423; IR Response 272; Compliance to an Order or Certificate 237;
  Audit Report 178; Letter 159; Surveillance report 111. By size: 15,818 with
  ≤5 rows, 18,728 with 6–20, 4,794 with 21–100.
- IR items: 828 from responses, 228 from requests; 779 under no topic heading.
  Topics: Environment 111, Engineering 39, Socio-Economic 22, General 22,
  Economic 17, Financial 8, Land 6, Abandonment Funding 6. Subjects recurring
  across requests: Insurance (12); engagement with Indigenous Peoples (7);
  Scope (7); Indigenous Engagement (6); Post-Construction Environmental
  Monitoring (5); Parental Guarantee (5); Access to Parent's Financial Resources (5).
- Order prefixes (English, with one real example each): GO 39 (gas
  exportation), ROE 34 (crude exportation), MO 26 (leave to sell facilities),
  EPR 23 (propane exportation), XG 16 (a pipeline optimization project), EBU 12
  (butane exportation), XO 7, TG 6, GPLO 6, GPSO 4, TO 3 (tolls), GOL 3, CCA 3,
  ZO 3 (abandonment), GPMO 2, FRO 2 (financial resource requirements), TOI, MH
  (hearing order), TGI 1 each.

---

## 1. Scout

Finding filings on REGDOCS and recording what the regulator says about each.

### The site (no official API)

| URL (under `https://apps.cer-rec.gc.ca`) | purpose |
|---|---|
| `/REGDOCS/Search/Advanced` | search form; its `<select>` options carry facet IDs |
| `/REGDOCS/Search/SearchAdvancedResults` | AJAX results (partial HTML) |
| `/REGDOCS/Item/View/{id}` | item page: metadata, filing tree |
| `/REGDOCS/File/Download/{id}` | the file; may redirect to `docs2.cer-rec.gc.ca` |

Parameters: `sd`/`ed` dates, `sr` 1-based start record, `srt` (`21` oldest
first, `22` newest), `rds` facet IDs. `limit` is ignored.

### Lessons

| finding | solution |
|---|---|
| Page size comes from the `RDI-NumberOfRecords` cookie (20/50/100/200) | set the cookie to 200 |
| Without `X-Requested-With: XMLHttpRequest` you get the full page | send it, with a browser User-Agent |
| New filings mid-crawl shift offsets | sort oldest first (`srt=21`) |
| Totals are "about N" | paginate until an empty page |
| Deep offsets past 10,000 are slow and shift | halve any window reporting >10,000 |
| **Impossible dates are dropped, not rejected**: end `2026-09-31` becomes "until today"; start `2026-02-30` becomes "since 2002", ~550,000 items | clamp and validate dates first; check every returned row's date is in range and stop before writing if not |
| "Date" is the filing/registration date, not when the work was done (verified against cover letters) | treat durations as the regulatory timeline only |
| A REGDOCS markup change could silently thin the crawl | fail the page on any unparseable row |
| Crawls get interrupted | checkpoint per search page per date range; the same command resumes; exit codes distinguish done, work left, bad args, interrupted |
| Re-checking a finished range | `--refresh` restarts checkpoints but keeps records and PDFs; changes REGDOCS now reports are logged |
| A partial scrape could erase good data | replace a field only with a non-empty value; never delete on a failed read; log every metadata change with `first_seen_at`/`last_seen_at` |
| Two scouts writing at once | one writer lock across commands; `status` always available offline |

### Politeness and cost

- One request at a time, 2–4 s jittered delay, exponential backoff on 429/5xx
  (2, 4, 8, 16 s); stop after five consecutive failures. No bans observed.
- A typical 2026 month: ~1,500 items, 900 PDFs, ~350 containers; about 10
  search pages, 700+ container requests and ~200 facet searches, roughly
  45 minutes. Containers dominate.
- A one-day scout: 180 requests, ~10 minutes, 156 of them facet searches.
- A document fetched without scouting has no metadata. Its date is not on its
  own item page, so read it from the parent filing's row, then scout that one day.

---

## 2. Enrich

Adding facets, filing membership and container structure to each document, and
deciding whether it is worth extracting.

### Facets

| finding | solution |
|---|---|
| Five facet categories: Document Type (76 values), Application Type (63), File Type (4), Role (7), Commodity (4) | tag each document by running the date-range search once per facet value (~150–156 searches per range, independent of document count) |
| Facet IDs can change | discover them from the search form each run |
| Different categories combine with **AND**; values within a category with **OR** (verified live: `53`→2 rows, `66`→38, `53,66`→40) | build queries accordingly; there is no negative facet or "NOT" syntax |
| A facet value can be half-updated mid-run | replace a facet only when every search in its category finished; until then only add |
| Documents reached through a container but filed outside the range get no facets | enrich the range they were filed in; or search by filing number |

### Containers (Compound Documents and Folders)

| finding | solution |
|---|---|
| Folders stopped at 200 members: the Next button is `href="#"` with the destination in `nextPageUrl` | follow `nextPageUrl`, offset `currentIndex + numberResultsPerPage`; fail if Next is unreadable |
| HTTPX dropped query parameters already in the URL when given an empty params dict | don't pass empty params |
| Hearing folders list party sub-folders as single-cell rows | accept single-cell rows only for Folder/Compound Document with an item link |
| Some containers report N members but list N−1 (withheld items); one year retried 122 times over 10 hours | after the **same** shortfall three times, record a gap and finish; never loosen the count check |
| "Unable to locate" pages | record as an unavailable-item gap, not a failure |
| 5,323 of 42,570 container visits were repeats across date ranges | reuse clean completed container reads; never reuse one with a gap |
| Filing membership could be wrongly dropped | drop a membership only when that filing was read in full without the document |

### Eligibility: what to extract

| candidate | count | treatment |
|---|---:|---|
| Confirmed filing receipts | 1,171 (14.3% of records, 2,386 pages) | keep metadata; skip text if receipt evidence isn't needed |
| Standard paper-only / translation placeholders | 25 (incl. all 22 Geospatial Data PDFs) | keep metadata and availability; skip extraction |
| Identical PDF bytes | 44 groups, 73 redundant copies | extract once per SHA-256; keep every document ID and filing link |
| HTML documents | | images live in CER's non-public OpenText server; replace checkbox images with ☑/☐, others with alt text, or skip |
| Compound documents | | containers, not files |

- **Do not exclude the Receipt facet wholesale**: 415 of its 1,586 documents are
  substantive Annual Update remediation reports typed as Receipt.
- **Do not filter on the word "receipt"**: 96 other titles use it (receipt
  meter stations). Use Receipt facet **and** a filing-receipt title pattern.
- Keep Other, Letter, maps, maintenance notices, French documents and
  poor-quality extractions; route them to review instead.
- Store eligibility locally; never rewrite REGDOCS's own Document Type.

---

## 3. Download

| finding | solution |
|---|---|
| Re-downloading wastes requests | a PDF already stored is never fetched again; only its record refreshes |
| A crash can save the PDF but not its metadata | validate the PDF signature, hash it, repair the metadata without contacting REGDOCS; record `reconciled_at` rather than inventing a download time |
| Same file filed several times (e.g. 4656998 = 4657424 = 4657571) | SHA-256 every download; share extraction by hash |
| CER titles exceed filesystem name limits | name files by REGDOCS document ID |
| Redirects to another host | follow redirects |
| "200 OK", `application/pdf`, 0 bytes (4471674; 6 of 860, 2026-10-17) | an empty body is REGDOCS's answer about that file: recorded as unavailable like a 404, not retried with backoff (five in a row would have stopped the run) |
| A "PDF" that is a desktop shortcut (980558: 77 bytes, `[Desktop Entry] ... 08BurdReport.pdf`) | check the first bytes are `%PDF-`; the filer uploaded a link, not the report |
| Fetching a known set means scanning every record | `download <ids or REGDOCS links>` / `--list FILE`: only those, recording any without a record first |

**Storage**: compress extracted JSON (9.4 MB → 0.9 MB gzip), not PDFs (7.4 →
7.3 MB). Keep metadata files plain text for grepping (~150 KB per 986-page
filing).

---

## 4. Profile

Learning what every page is, from the file alone, before converting anything.

### What to record per page

Size, rotation and boxes; characters by class (letter, digit, replacement,
control, private use) for **visible and invisible** text separately; text
direction; fonts with encoding, embedding, `ToUnicode` and "unmappable risk";
every image placement with pixels, effective DPI, colour space and encoding
(JPEG, JBIG2, CCITT = scanner); vector paths, ruling lines and boxes; form
fields and values; annotations; outline; producer, creator and XMP tool history.

Then derive per-page hints: **kind** (digital, scan, image with text, vector
drawing, sparse, blank), **text layer** (sound, suspect, hidden OCR, none) and
**origin** (digital, scanned, scanned with OCR, raster, image with text).

### Lessons

- **It is fast and finds the dangerous pages in advance**: ~700 pages/s on CPU,
  no rendering. It found one filing's 315 mojibake pages and another's 399
  scans in seconds with no reference, and flags oversized sheets and pages with
  tens of thousands of vector paths before they crash a converter.
- **Decide digital vs scanned per page, not per document**: a document's
  producer is the last tool that saved it (a Word letter combined with scans in
  Acrobat says Acrobat).
- **Producer is a free pre-flight signal**, not a verdict. Pages needing OCR
  rescue: Adobe PDF Library 5.1%, Word 5.9%, Chrome/Skia 13.7%, Print To PDF
  14.8%, no producer 28.1%, one PDF-XChange Viewer filing 91.1%. Six of nine
  surviving mojibake pages came from Acrobat Distiller 25/26 (6.8% vs 0.06%
  baseline).
- **Use fixed keys in records**: maps keyed by data (font names) gave DuckDB a
  column per key and ran it out of memory. Flatten nested records to Parquet for
  corpus questions (milliseconds).
- Sample profile: 76,370 digital pages with a sound layer (84%); 4,354 scans;
  1,783 image-with-text or hidden-OCR scans; 2,242 image-with-text with a sound
  layer; 880 suspect; 1,755 vector drawings; 2,525 sparse or blank. 1,608
  documents have an outline; 151 hold 4,057 form values; 51 are certified-signed.

---

## 5. Document conversion

Turning each page into faithful text, tables and structure.

### 5.1 The approach that works

```text
route     each page from its profile: text layer | OCR of the page | text layer
          + OCR of images | text layer only with a structure budget (heavy pages)
convert   structure reader per page (layout, tables) in an isolated worker
text      each item's text = the text-layer words in its box; words no item
          claims become items; redacted words are left out
tables    per table, the grid that places the most words
ocr       RapidOCR on scans and untrusted layers; a scan's own OCR layer votes;
          vision models only as voters on disputed values
export    DoclingDocument, Markdown, and an evidence file of every word and
          the item holding it
```

Principles: evidence (text read from the file or by OCR, citable to page and
box) and generated aids (descriptions, summaries) are never mixed; nothing is
silently lost; thresholds order the attempts, results decide.

### 5.2 The text layer

| failure | example and rate | detection | solution |
|---|---|---|---|
| **Unmapped glyphs (mojibake)** | 315 of 986 pages in one filing; 880 suspect pages in the sample | control-character share > 0.2 matched 314 of 316 | rasterise and OCR |
| **Numeric mojibake** (`33 39 38 39 43 i255 …`) | 9 pages in 5 documents (0.06%) | letters-and-digits share can't see it (0.72); use **letter share < 0.30 and fewer than five real words** | rasterise and OCR |
| **No text layer** (scans) | 399 of 463 pages in one filing | no visible characters; page image with scanner signs | OCR (13.2% → 96.0%) |
| **Stub layer** (a stamp on a scan) | 12 characters on a ~1,000-word page | fewer than 100 characters | OCR |
| **Hidden OCR layer** | 1,783 pages | invisible text over a page image | second voter, not truth |
| **Duplicated layer** | 17,518 chars vs 6,952 correct | words more frequent in output than on the page | dedupe; prefer visual order |
| **Text under redaction boxes** | 11,246 blacked-out words were being exported | dark fill **and** a flat render in the word's middle (dark fill alone: 82,469 false hits) | leave out of every output |
| **Invisible text, drawing labels** ("PO PO PO") | | visible vs invisible character classes | score against visible words |
| **Letter-spacing, superscripts, joined emails** | 11 of 98 sample docs under 98% export | | normalise in comparisons |

Overall **14.7% of pages across 17.9% of documents** had a text layer worse than OCR.

### 5.3 Docling: gaps and fixes

Docling (2.118–2.130) gives layout, reading order, headings, TableFormer tables,
boxes, footnotes/captions/lists, cell spans; it reports dropped table cells;
it is local and inspectable; its `DoclingDocument` is a good container even
when other readers produce the items.

| gap | fix |
|---|---|
| Publishes mojibake as text with no warning | detect beforehand; rasterise |
| TableFormer reads cell text straight from the PDF backend, bypassing OCR (`table_structure_model_v2.py:510`); every OCR mode leaves table cells as mojibake | rasterise the page (5.6% → 91.4%) |
| `FULL_PAGE` OCR re-reads exact text badly (100% → 16.4% on a clean page) | choose OCR mode per page |
| Default Markdown export drops text under pictures and headers/footers (139 of 1,572 words) | `export_to_markdown(traverse_pictures=True, included_content_layers=set(ContentLayer))` |
| Markdown export drops table footnotes (docling-core 2.99); CER forms fell to 60% | append footnotes after their table |
| `concatenate` renumbers pages (1,044 false errors) | merge without renumbering; check page numbers and sizes against the PDF |
| Some pages fail to parse | retry alone, then take text from MuPDF, flagged |
| docling-parse hangs *loading* PDFs whose certification signature `/Reference` points at the catalog | cut the `/Reference` with an incremental update (loads in 0.03 s) |
| docling-parse rendered clear scans as black noise | use the pdfium backend |
| A one-page copy of a filled form converts differently (201 items/88 errors vs 186/6) | convert the filed PDF by page range |
| `document_timeout` only checked between steps, not in the loader | external timeout per process group |
| Memory grows with pages held at once | one page per call (0.95–0.98× wall time) |
| Drawing labels assigned to no region (55.5% kept) | words no item claims become items (→ 99.3%) |
| Maps read as one picture | OCR the figure region (open for scans) |
| Confidence scores page-level only; `table_score` not implemented | record them, decide nothing on them |
| TableFormer warnings log as `MatchingPostProcessor`, outside Docling's logger | listen at the root logger |
| `RapidOcrOptions` defaults to Chinese; tag inert for PP-OCRv6 (`en`/`fr`/`la` identical) | keep `lang=["en"]` |
| Rasterising removes the outline | no measured loss; `use_numbering` recovers headings |
| `ocr_empty` warnings | benign |

### 5.4 Tables

**Findings**

- TableFormer mode kept reversing: by cells dropped, accurate lost (1×1 grids);
  by words in the wrong cell on 40 pages, accurate won by a fifth; on whole
  documents accurate merged lab-table cells and lost ~20,000 words
  (96.8% → 87.3%). **Choose per table.**
- TableFormer v2 is worse here (19–23% problem words vs 3–4% for v1).
- Both TableFormer modes can agree on the same wrong grid.
- **Ruled tables belong to ruling-line grids** (drawing tables: 477 errors → 0;
  export 92.0% → 99.8%). Partly ruled tables give line grids too coarse (11×4
  for 38×28).
- **Tables ruled only between blocks** read best from text alignment (63,280
  badly placed words → 6,415). Limit: two-line headers become two rows.
- Table Transformer on whole pages downscales to ~1000 px and destroys
  structure (37.9% vs line grid 99.4%); use it on crops only.
- Table-count gaps are mostly segmentation (Azure 1,718 tables vs Docling 886
  on one filing; 1,688 of Azure's were single-page, Docling merges them).
- Line-grid quality is conditional (93–99% on some ruled pages, nothing or
  fragments on huge sheets); route on grid quality, not a binary "ruled" flag.

**Solution: choose the grid per table, scored by words**

1. TableFormer fast.
2. Where a table places <99% of its words, also accurate; keep whichever places
   more (ties keep fast).
3. Where exactly one ruled table fills the box, the line grid has ≥80% of
   TableFormer's rows and columns, and it **fits** better, use the line grid.
4. Where ruled only between blocks, use text alignment if it places under half
   as many words badly.
5. Cell text from the text-layer words in each cell box.

*Word score*: each text-layer word in a table is placed, in the wrong cell, in
no cell, or lost. *Fit*: words crossing borders, in no cell, columns merged,
words split; needed because a grid built from text-layer words passes the word
score by construction. Skip dot leaders.

**Suspect cells**: a value differing from a column sibling by one
letter-to-digit substitution is suspect. It exists because `<3hT` was read as
`<31T` (a detection limit of 31 instead of 3) with no warning. Outlier rules
produced 10,697 flags and missed it.

**Results**

| step | table words placed | lost | time (ten hard docs) |
|---|---:|---:|---:|
| TableFormer fast only | 87.6% of 350,389 | 5,982 | 16 min |
| fast + accurate everywhere | 89.6% | 2,582 | 45 min |
| fast + accurate below 99% | **89.5%** | 2,585 | **31 min** |

Adding line grids: errors 48,386 → 40,980 at the same cost. Final choice on
113 documents: 652 fast, 70 accurate, 226 line grid, 379 alignment. Small
sample (28 pages, problem words): 132 → 53 (best of two modes) → 22 (+ line
grid) → **14** (+ Table Transformer). Cell-content F1 on 518 table pages:
Docling 0.720, camelot-stream 0.691, pdfplumber 0.54–0.56, camelot-lattice
0.529; Docling + pdfplumber **0.838**; oracle 0.856. Detection limits on a lab
filing: **19,831 vs 19,831** against the cloud service.

### 5.5 Drawings, huge pages, forms, redactions

| gap | finding | solution |
|---|---|---|
| Labels on digital drawings | assigned to no region | words no item claims become items |
| Text inside scanned drawings and maps | Docling reads maps as one picture; the cloud service reads them | OCR figures at 0/90/270° (+10.8 points on six pages; 180° only +0.4–1.7); still short of the cloud service |
| Pages too large to rasterise | a 182.5 × 34.5 in sheet is 294 MP at scale 3.0; Pillow refuses >~178 MP; every strategy failed; 13 pages in 5 docs | pixel budget: `scale = min(RASTER_SCALE, sqrt(MAX_PIXELS / (w*h)))` |
| Heavy pages (tens of thousands of paths, 34×22 in sheets) | TableFormer running after 3,600 s on 3 pages | route to the text layer with a structure budget from the profile (1 hour → 1–4 s) |
| Forms | 4,057 values in 151 documents | read AcroForm fields directly; build derived PDFs as original bytes plus an incremental update |
| Annotations, outlines | | read directly from the PDF |
| Checkboxes, barcodes | cloud service found 341 checkboxes and 6 barcodes on one filing; Docling 32 and none | decoder pass over picture regions (open) |

### 5.6 OCR and vision models

- **RapidOCR PP-OCRv6 `medium`** is the working OCR (no slower than `small`,
  better). Misreads come from text-line detection in full-page context, not
  model size (all sizes read `<3hT` correctly in isolation and wrong in context).
- A scan's own OCR layer is a useful second voter. Two independent OCRs agreed
  84%; agreement with a hidden layer was 75% (local) vs 40% (cloud).

| model | strength | gap |
|---|---|---|
| GLM-OCR | best recall on diagram labels (85.9% words, 97.6% numbers) | 64.8% precision; loops on blank pages without a stop sequence; token-repeat abort on scans |
| olmOCR-2 7B | agrees 95% with Qwen on suspect pages | 75% of numbers on clean prose; needs generate endpoint, 8192 context |
| Qwen3-VL 8B | all words and numbers on clean prose | nothing on an oversized drawing |
| DeepSeek-OCR 3B | 99% word agreement on scans | **fabricated** addresses and emails; text on a blank page |
| Granite 3.2 Vision 2B | fast | text on a blank page; scan outlier (63%) |

Every VLM kept under 11% of words on an engineering-drawing table. On scans
they agreed on 99% of words but 73–92% of numbers. **Rule**: VLMs are voters on
disputed values only, after blank-page, numeric-grounding and repeatability
tests; any token absent from OCR or the text layer is rejected.

### 5.7 Tool summary

| tool | best for | avoid for |
|---|---|---|
| PyMuPDF (MuPDF) | text layer with boxes (0.03 s/page), profiling, fallback; `find_tables` 72% rows exact vs TableFormer 35% | broken layers |
| Apache Tika | independent text reader; finished all ten hard documents | tables (1 in ten docs), provenance (no boxes) |
| Docling | layout, headings, tables, container format | trusting its text or Markdown export as-is |
| pdfplumber / line grids | ruled tables; cheap cross-check (0.12 s/page) | unruled or partly ruled tables |
| Camelot | — | not worth a dependency (+0.018 F1) |
| Table Transformer | borderless tables on crops | whole pages; needs config patches for transformers 5.17 |
| opendataloader-pdf | fast (~7×) boxes and tables | OOM at 8 GB on heavy docs; diagram labels |
| anydoc | — | refuses any doc needing OCR; hosted OCR |
| PaddleOCR PP-StructureV3 | untested | paddlepaddle 3.3.1 errors on every page |
| RapidOCR | scans, untrusted layers | two instances on one GPU (deadlock) |
| Cloud (Azure Content Understanding) | drawings, checkboxes, barcodes, completing huge files | exact digital text (re-OCRs it); `&lt;` escaping; parts not concatenable |
| Ollama VLMs | voting on disputed values | being the source |

Untested candidates: PyMuPDF4LLM, MinerU, Unstructured (`fast`/`hi_res`/
`ocr_only`), Nemotron Parse, Xberg, olmOCR as a full pipeline.

### 5.8 Test results

**Docling tuned per page** (986-page filing, 315 mojibake pages; coverage =
share of reference characters per page):

| change | mean | pages ≥95% | pages <50% | time |
|---|---:|---:|---:|---:|
| trust the text layer | 70.2% | 640 | **316** | 2071 s |
| rasterise broken pages, `FULL_PAGE` everywhere | 96.9% | 781 | 7 | 2093 s |
| OCR mode per page | 98.7% | 884 | 2 | 2119 s |
| TableFormer fast | 98.8% | 882 | 0 | 1574 s |
| both modes when cells drop | 98.8% | 885 | 0 | 1921 s |
| PP-OCRv6 medium | **99.0%** | **895** | 0 | 1781 s |

**Per-page variant selection** (as-is, rasterised, smaller OCR model, higher
scale; score = characters × letter-or-digit share; switch only by a **2%
margin**): margin 1–3% 98.55% vs argmax 98.52% vs never 97.99%. 1/2/4 variants:
97.8% / 98.2% / 98.6% at ~400 / 843 / 1,603 s. Scanned filing: **13.2% → 96.0%**,
pages <50% 368 → 11. Picked the closer output in 47 of 47 validations. At
scale (4,302 docs, 15,340 pages): **99.4%** mean coverage, 19 pages <50%.

**Four converters on the ten hardest documents** (% words / % numbers):

| document | Docling | opendataloader | Tika | anydoc |
|---|---|---|---|---|
| oversized drawings | failed | failed | **99 / 95** | refused |
| giant table sheets | failed | 60 / 88 | **76 / 83** | 8 / 68 |
| dense spreadsheets | failed | **100 / 100** | **100 / 100** | 96 / 95 |
| drawing sheets | failed | failed | **97 / 71** | refused |
| unparseable pages | 98 / 93 | 99 / 92 | **100 / 99** | 99 / 94 |
| diagram labels | **54 / 59** | nothing | 18 / 22 | 1 / 1 |
| lab tables | 95 / 94 | failed | 92 / 95 | refused |
| drawing tables | 92 / 99 | 60 / 75 | **99 / 100** | 84 / 69 |
| mojibake | **99 / 99** | 77 / 72 | 97 / 93 | refused |
| scanned | 91 / 95 | 86 / 86 | **100 / 100** | refused |

**The careful pipeline on the ten, step by step**: 4 of ten produced nothing →
per-page limits and poison pages (84 min, 3 of 4 finish) → text-layer fill and
heavy-page routing (all ten, **16 min**) → tables per table (errors 48,386 →
40,980) → item text from the text layer (all ≥98.0% of words in export) → OCR,
alignment grids, redactions (113 documents in 71 min; 3,272 of 3,287 pages).

**Local vs cloud on 113 documents** (visible words):

| | local | cloud |
|---|---:|---:|
| visible words kept, 1,993 digital pages | **99.9%** | 84.2% |
| words not on the page | **0.2%** | 1.7% |
| visible words kept, 166 drawing pages | **100.0%** | 90.7% |
| words not on the page, drawings | **0.6%** | 10.7% |
| table words badly placed, per 1,000 | **47.5** | 178.7 |
| table words in a cell box but no cell's text | **64** | 84,908 |

The cloud service re-OCRs exact text ("M45C" → "M4SC", "14895" → "14805") and
kept 1.1% of 1.5 pt spreadsheet text.

**Cloud vs out-of-the-box Docling at scale** (8,200 paired PDFs): completed
8,213 vs 8,203; exact page count 8,200 vs 8,075; **14.97 h vs 67.39 h**
(5,807 vs 1,286 pages/h); cost ~$500 ($5.53 per 1,000 pages). Docling's 10
failures (5 segfaults, 4 timeouts, 1 stranded) were all large or complex files.

**VLM pilot** (40 pages, median word recall on sound pages): PyMuPDF 1.000
(0.03 s), Docling 0.988 (7.1 s), line grid 0.784, GLM-OCR 0.533, olmOCR 0.355,
DeepSeek 0.346, Granite 0.231, Qwen 0.230.

### 5.9 Open gaps

Text inside scanned drawings; checkboxes and barcodes; partly ruled lab tables;
rows run together (invisible to fit); Table Transformer on crops and
PP-StructureV3 never measured; reading order, paragraph roles and figures never
compared between tools; numeric-mojibake detection not wired into a pipeline;
the 0.2 control-character threshold tuned on one document.

---

## 6. Quality checks and review

### References, in order of preference

1. Mechanically known truth: the text layer on proven-sound pages, form values,
   digital twins of scans (320 found at 90% overlap).
2. Agreement of **independent** readers (not sharing an OCR engine, backend or
   model family), then human adjudication; numbers adjudicated separately.
3. A double-transcribed gold set (~100 pages across text, OCR, tables, French,
   maps, forms, poor scans). **Never built.**

### Checks worth running on every page

| check | catches |
|---|---|
| every text-layer word inside an item, and inside a cell if inside a table | dropped labels, grid failures |
| item/cell text vs text-layer words in the same box (digit differs = error) | misreads, mojibake |
| export holds ≥98% of the page's words | lossy exports, dropped footnotes |
| words in the output more often than on the page | duplication (20,592 words on one file) |
| words in the output not in the file | invention, misreads |
| page number and size match the PDF | renumbering |
| outline entries have a heading on their page | heading loss |
| table fit; suspect cells | wrong grids; one-character swaps |

Draw every issue on a **review PDF**: the filed PDF plus annotations (red error,
amber warning, blue info or generated, grey pending), each with its evidence.

### Record something for every page

Recording only events flagged 35 of 67 weak pages and 134 fine ones: a page
that quietly under-extracts produces no event. Record a row for every page:
characters, how character-like, which reading won, and **how far the readings
disagreed** (clean 0.009, rescued 0.96). Name doubts and judge them on the
result: `almost_no_text`, `not_character_like`, `table_mostly_dropped`,
`table_grid_disputed`, `thin_for_this_document` (under 30% of the document's
median), `ocr_empty`, `bbox_clamped`, `needed_retry`. Keep a reprocess list per
doubt so a better model can fix exactly its pages.

### Triage (8,153 documents)

| verdict | documents | pages |
|---|---:|---:|
| good (incl. "with notes") | 5,071 (62%) | 16,790 (20%) |
| review: numbers | 1,763 | 16,281 |
| review: text missing | 996 | **40,271 (48%)** |
| review: table structure | 323 | 10,114 |

Flag reasons: readings disagree on a number 5,972; less text than the rest of
the document 1,413; almost no text 748; OCR found nothing 558; table grid
disputed 528; text not character-like 283. **Only 1,704 pages actually lack
text**: review pages, not documents. Orders are most reliable (92% good);
"Other" and compliance-to-order filings are about half flagged. Keep triage a
separate, versioned policy ranked by consequence.

### Metric traps (each happened)

- Character coverage weighs a missing "the" like a wrong digit; **numeric
  agreement** is the meaningful figure.
- Scoring doubt rules against coverage is circular.
- Counting issues per cell rewarded merging cells.
- Coverage against the cloud service is not coverage against truth.
- Token counts reward duplicated hidden text.
- A fixed 1.5 pt margin on 1.5 pt text: 61,882 false issues. Scale margins.
- Stripping HTML with `<[^>]+>` deleted `\< 0.0050 …` lab values.
- A grid measure favoured a grid that, drawn on the page, cut "Backgrou | nd".
- **Conservation is not accuracy.** The light pass's checks (every evidence word in the text,
  the Markdown and docling exactly once) held on 267,495 of 267,495 documents; they prove no word
  is lost between steps, not that any word was read right.
- **A measure not taken stored as zero.** OCR skipped on a sound layer recorded "OCR found 0% of
  the layer": 87% of sampled text-layer pages, averaged into the report. Store None.
- A Markdown-to-evidence word ratio above 1 found headings written from a mail merge's bookmarks
  (4049986: 2.1; 3755199: 6.2) - text in the output that is on no page.

---

## 7. Operations

### Reliability

| failure | cause | solution |
|---|---|---|
| Segfaults, hangs, OOM kill a batch | native code | one worker process per document or page range; replace workers every N documents; retry at the end (2–3 attempts) |
| A page kills its worker repeatedly | heavy or malformed page | **poison page**: next attempt uses the text-layer reader |
| Timeout doesn't fire | checked between steps only | external timeout per process group (stopped cleanly at 10.1 s in a test) |
| Machine froze | three large documents converting whole | one page per call: 3–4 GB per worker, 34 GB free |
| Worker reached 23.8 GB mid-document | cap checked after documents | check memory during the document; free results, empty CUDA cache, `malloc_trim` per page; replace workers over 8 GB; start none below 6 GB free |
| 0.2% random crashes and host reboots | **a game sharing the GPU**; device check read total VRAM once | check **free** VRAM before each model call; ask what else was running |
| OCR 1.4 → 9.9 s a page, pages stalling (2026-10-17) | **a game sharing the GPU, again**: WSL's `nvidia-smi` shows only its own processes | Windows' `nvidia-smi.exe` names them; watch seconds a page by reader over time - a step change with no change in the documents is the machine |
| Two OCR engines deadlocked | two RapidOCR instances on one GPU | one engine per GPU |
| Stalls with no clue | no per-page logging | log each page's start, finish and memory; 30 s telemetry |
| Checker timed out, not converter | word-in-box loops (44%) | spatial index; cap review annotations |
| Two schemas in one run | code edited mid-batch | change code only between runs |

### Performance

- Fixed cost 15.1 s per document (interpreter, imports, eight converters);
  loading models once per worker was a third faster on small filings.
- One document at a time left the GPU 70% idle.
- Profiling ~700 pages/s; careful conversion ~6 pages/s with 2 GPU workers.
- Docling is not thread-safe; one per process.

### Provenance and reproducibility

- Name each output by a hash of the code (**syntax tree without docstrings**),
  every package used, settings, inputs, and **every model's weights**.
- Keep a verbatim copy of the code with each run, plus git commit and dirty flag.
  *Relearned:* the v2 ledgers said only "v2" until 2026-10-17, and half the corpus pass had been
  written before a later step existed. Each ledger now carries a hash of the files that convert,
  the commit and the dirty flag. Lessons recorded here were not carried into the next pipeline's
  code - read this file before building.
- Same settings land in the same place; a changed setting lands beside the old
  run. No overwrite flag; delete a run's folder to redo it.
- Per-item provenance beside the DoclingDocument (reader, version, agreeing and
  disagreeing readers, grade), keyed by item reference.
- Fixes are new versions with a patch log naming the parent; refuse to run if
  the PDF hash differs.
- Downstream claims record the extraction they cite: fixing mojibake changes quotes.
- Keep a copy of the code behind any paid extraction; it cannot be re-run.
- Keep a lossless structured export so re-chunking runs on CPU.

---

## 8. Search and analysis

### Indexing and retrieval

| finding | solution |
|---|---|
| Chunks lose page citations | export each page with a `<!-- page:N -->` marker; record page range per chunk; resolve citations to page and bbox |
| "What did company X file?" fails when chunks don't name X | prepend a metadata header before embedding; add a summary chunk per document and per filing |
| Embeddings miss exact identifiers | hybrid search: vector plus full-text (FTS5, Porter stemming) triggered by filing numbers and quoted terms |
| Filing numbers are the best filter | they identify a proceeding |
| ChromaDB `get()` fails past ~275K chunks | page in batches of 5,000 |
| Answers blur found and cited evidence | show them separately; citations must resolve to retrieved chunks |
| Hard-coded coverage dates go stale | read coverage from the live index |
| Embedding publication to a cloud index | batch size 32 |

### Downstream extraction and classification

| finding | solution |
|---|---|
| Classification rules change | keep it out of extraction; re-classify stored output (minutes vs hours) |
| Model-extracted claims and obligations can be wrong | accept only if evidence chunk IDs came from the exact input; mark unreviewed; "outstanding" only from explicit status; don't relabel party claims as regulator findings |
| Local LLMs emit malformed JSON | skip, retry next run, cache successes by content hash |
| Long reports exceed context | truncate keeping head and tail (~32K chars) |
| A quote matches the source | proves the text exists, not the interpretation |
| Vision evidence | label unverified; never clears a quality issue |
| Model output changes with model or prompt | cache by model digest, prompt, schema, source and limits |
| Observation counts | are not distinct-report counts |
| Filing durations from metadata | measure first-to-last document; active proceedings understated; multi-year proceedings need 5+ years of history |
| Post-construction monitoring reports | fixed-schema extraction (status, issue categories, severity, resolved) |

### Open gaps

Classification and datasets (conditions, monitoring reports, schedule rows)
never built on the best extraction; category consolidation beyond identical
names never built.

---

## Recommendations for future filings

Changes the CER could consider for the filing manual, or for checks at
submission, so that future filings convert faithfully. Almost every defect
found was introduced by how the PDF was *produced*, not by what the filer
submitted, and usually without the filer knowing. The correct text and data
existed in the filer's own software and was lost on export.

Each recommendation is marked by the strength of the evidence behind it:

- **Measured**: the defect and its rate were measured on this corpus
  (4,296 filings, 16,085 pages, each page compared with an independent
  extraction).
- **Proposed**: follows from what was found, but its benefit was not measured.

### A. Make PDFs digital-first

| # | recommendation | evidence |
|---|---|---|
| A1 | **Create PDFs by exporting or saving as PDF from the authoring application** (Word, Excel, AutoCAD, the lab system), not by scanning a printout, printing to a virtual printer, or re-distilling | **Measured.** Share of pages whose text layer was worse than OCR of the page image: Adobe PDF Library 5.1%, Word direct export 5.9%, Chrome/Skia print 13.7%, Microsoft Print To PDF 14.8%, no producer recorded 28.1%, one PDF-XChange Viewer filing 91.1%. Direct export is roughly a third of the rate of print-to-PDF paths. |
| A2 | **Do not re-distill a finished PDF through PostScript**, and do not use "reduce file size" or "optimize" options that strip font information | **Measured defect, weak on cause.** One filing had 315 of 986 pages that display correctly but extract as nonsense (fonts with no Unicode mapping). Six of the nine such pages that survived conversion came from Acrobat Distiller, about 100× the corpus rate, but six pages is too few to name a product. |
| A3 | **Every page must carry a text layer that reproduces its visible text.** Where the source is genuinely paper, the filer OCRs it before filing, at 300 dpi or better, and checks the result | **Measured.** 14.7% of pages across 17.9% of documents needed OCR rescue. A scanned filing yielded 13.2% of its text as filed and 96.0% after OCR. The filer knows the terminology and can see the source; a later extractor is guessing, and on lab certificates a one-character OCR error changes a detection limit by an order of magnitude. |
| A4 | **Do not name a required product**; require the outcome (A1–A3) instead | **Measured.** Microsoft Print To PDF produced no garbled fonts across 189 pages, though its text layers needed OCR more often; the tool matters less than the path. Tools not seen in enough volume (Foxit and others) are unmeasured. |

### B. Redact by removing, not covering

| # | recommendation | evidence |
|---|---|---|
| B1 | **Redaction must remove the text, not draw a box over it.** Use a true redaction tool that deletes the underlying text, then verify by searching the PDF for a redacted phrase | **Measured, and a privacy issue.** In one filing, 11,246 words of blacked-out invoice descriptions were still in the text layer and were being copied out by extraction. Anyone selecting and copying the page recovers them. |

### C. Keep forms and structure machine-readable

| # | recommendation | evidence |
|---|---|---|
| C1 | **Keep fillable form fields live; do not flatten forms** before filing | **Measured value, proposed rule.** 4,057 form values in 151 documents were read exactly from their fields, with no OCR. A flattened form has to be read from pixels. Checkboxes drawn as images rather than fields are a known weak spot: on one filing the cloud service detected 341 checkboxes and the local tools 32. |
| C2 | **Add bookmarks (an outline) to any document over ~20 pages**, one per section and attachment | **Proposed.** Only 1,608 of 8,210 documents had an outline. Outlines give exact headings and attachment boundaries for free. |
| C3 | **Produce tagged, accessible PDFs** (headings, tables and reading order tagged, e.g. PDF/UA) | **Proposed.** Tags carry the structure that extraction otherwise has to infer, and accessibility is an obligation in its own right. Not measured here. |
| C4 | **Prefer PDF/A-2u or PDF/A-3u** for archival filings | **Proposed.** The "u" conformance level requires every character to map to Unicode, which rules out the garbled-font defect (A2) by construction. PDF/A-3 also allows source data files to be embedded inside the PDF (see D1). |
| C5 | **Use a consistent numbering and heading template for information requests and responses** (e.g. "1.3 Topic: Subject") | **Proposed.** 1,056 IR items were recovered from headings, but 779 sat under no topic heading, which limits search and comparison across proceedings. |

### D. File data as data

| # | recommendation | evidence |
|---|---|---|
| D1 | **File data tables as machine-readable companions to the PDF**: laboratory and water-quality results, monitoring data, financial statements, schedules. CSV or Excel for filers; the CER can store them as Parquet on intake | **Proposed, strongly motivated.** Tables are the weakest part of every extraction: the best local method placed 89.5% of table words in the right cell, and the cloud service misplaced far more. The sample holds 4,138 data tables. Detection limits such as `<0.010` are exactly where a single misread character changes a value. A companion file removes the problem at source. |
| D2 | **File spatial data as GIS files** (GeoJSON, shapefile, KML) alongside maps and alignment sheets | **Proposed.** Text inside drawings is the largest remaining gap for local extraction, and coordinates are printed rotated in the margins. Every Geospatial Data PDF in the sample was a placeholder, so the category exists but carries no data. |
| D3 | **For paper-only or pending-translation items, record availability in metadata** instead of filing a placeholder PDF | **Proposed.** 25 standard placeholders were filed as PDFs and must be recognised and skipped. |

### E. Keep pages a readable size

| # | recommendation | evidence |
|---|---|---|
| E1 | **Cap page dimensions; file oversized drawings as standard-size sheets or tiles** (e.g. no larger than 36 × 48 in) | **Measured, narrow.** 13 pages in 5 documents (one 182.5 × 34.5 in, twelve 92 × 92 in) could not be processed at all; image libraries refuse them. 0.06% of documents, but a total loss on alignment sheets and site plans, which carry the spatial information hardest to recover any other way. |

### F. Check at intake, not only in the manual

A filer told to "ensure text is extractable" has no way to know they failed.
Every check below runs on the file alone in under a second, and would tell the
filer while the correct source still exists:

| check | catches | reads |
|---|---|---|
| text layer present and at least as good as OCR of one rendered page | A1, A3 | text layer + one render |
| letter share with running words; font Unicode mappings | A2 | text layer, font table |
| text under dark fills that render flat | B1 | text layer + render |
| page dimensions | E1 | page boxes |
| form fields present when the source is a known form | C1 | form table |
| data-table companion present for designated document types (e.g. lab reports) | D1 | the submission |

A message such as *"page 47 has no usable text layer"* at submission is
actionable in a way that a sentence in the manual is not. **A rule in a manual
is advice; a check at intake is a rule.**

### G. Not worth requiring

Checked and found not to matter, so not worth the cost to filers or enforcement:

- **Page rotation**: rotated pages scored 0.734 table quality against 0.713 unrotated.
- **Document length**: 986-page filings extract as well as 2-page ones.
- **Table formatting rules**: nobody can yet say what makes one printed table
  easier to read than another; file the data instead (D1).
- **A named PDF product** (A4).

---

## Method: how to not fool yourself

- **Test on the hard cases, confirm on random ones**; show every change before
  and after, including what got worse.
- **The measurement was right; the sample was wrong**: a change neutral on 14
  stub pages was 13% vs 96% on a document with 399.
- **An estimate from a handful of a skewed corpus is not an estimate** (4× and
  2× wrong before 2,135 documents).
- **A self-check validated only on the failure already found certifies nothing.**
- **Classify-then-trust fails; produce-measure-keep holds.**
- **A 40-page sample can reverse on a 691-page document.**
- **A score must not be able to see its own answer.**
- **Look at the page**, and at the image a model was given.
- **A checker is code with bugs.**
- **The page a worker dies on is not always the cause.**
- **Attribute a random failure rate before engineering around it.**
- Split test sets by document and template, never by page; report tails; keep
  failures in denominators; freeze validation sets before optimising.
- Measure routing by **regret** (best-in-hindsight vs chosen-from-features).
- Keep experiments apart from production; give a build a finite completion checklist.
- Not yet done: a stratified 1,000-page validation set; running the best
  pipeline beyond 113 documents; sampling older years.

---

## Test documents

All at `https://apps.cer-rec.gc.ca/REGDOCS/File/Download/<id>`.

| id | pages | what breaks |
|---|---:|---|
| 4656998 | 149 | certified-signature `/Reference` hangs the loader; = 4657424, 4657571 |
| 4648296 | 3 | 34×22 in ruled-table sheets; TableFormer unbounded |
| 4656888 | 8 | dense spreadsheets, 1.5 pt text |
| 4681376 | 24 | 25,000–30,000 vector paths per sheet |
| 4646070 | 273 | pages Docling cannot parse (also 4642962, 4684173) |
| 4657006 | 11 | diagram labels assigned to no region |
| 4672628 | 691 | lab tables ruled only between blocks |
| 4664902 | 3 | tables on engineering drawings |
| 4647200 | 986 | 315 mojibake pages; detection limits |
| 4710294 | 463 | 399 scans, French, redactions in the text layer |
| 4665296 | | filled form; a page copy converts differently |
| 4692330 | | form tree breaks PDF copying |
| 4590700 | | page renumbering across ranges |
| 4692360, 4646844–7 | | pages too large to rasterise |
| 4692624 | 474 | weak pages mostly drawings |
| 4576114 | | duplicated hidden text |
| 4647207 | | map: empty Markdown, useful raw text |
| 4656932 | | huge spreadsheet printed tiny |
| 4550418 / 4550419 | | Annual Update typed Receipt, and the real receipt |
| 4652449 p29, 4665165 p5 | | clean-prose and blank-page controls |

Large varied filings for scale tests: 4600563, 4647187, 4673063, 4596827,
4664851, 4666748, 4646669, 4648189, 4597172, 4648190 (French), 4572347,
4664850, 4692070. 4648189/4648190 and 4664850/4664851 are pairs.
