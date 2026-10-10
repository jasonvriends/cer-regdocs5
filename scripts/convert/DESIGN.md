# convert: design of the conversion pass

Status: tier 0 is built (`tier0.py`), and so is a first conversion pass
(`convert.py`). **The plan has since changed**: text layer first, structure as an
overlay (see [LESSONS.md](LESSONS.md), which supersedes the build order below),
and each region routed by what it is ([Page kinds](#page-kinds-what-each-page-and-region-is),
2026-10-10).

The corpus: about 265,000 CER REGDOCS PDFs, 2012-2026, used as regulatory
evidence and extracted for RAG and AI. The 8,210 in `source/` today are the
sample everything below is measured on.

## Principles

1. **Two layers, never mixed.** The *evidence layer* is text and values read
   from the file or by OCR, each one citable to a page and a box, with the
   reader that produced it and whether other readers agreed. *Retrieval aids*
   are generated (figure descriptions, chart-to-table, summaries), marked as
   generated, and always point back to the evidence they describe. A
   regulator's number is never something a model wrote.
2. **Nothing is silently lost.** Every page ends with a measured account of
   what was not read: characters in the text layer and ink on the page that no
   extracted item covers. An empty problem list must mean "checked, nothing
   found", never "nothing reported".
3. **Decide per page, and per region, from evidence.** Tier 0 says what a page
   is before anything is converted; the route picks readers from that; checks
   judge the result against independent readers, not against the extractor's
   own opinion of itself.
4. **Everything is traceable.** Every output can be traced to the PDF's
   sha256, the code, every package, every model's weights, the settings and the
   session log that produced it.
5. **Pass 1 is never edited.** Fixes and enrichments are later versions with a
   patch log, each naming its parent.

## Stages

```
tier0   file structure, no rendering                        built: 669 pages/s
pages   what each page and region is: layout regions,       next (see Page kinds)
        page kind, rotation, scan or text layer
tier1   thumbnail + classical vision, only where tier0 is unsure
route   per region: which reader, from its label and the page's kind
read    readers write items (text, table, picture, field, ...) with boxes
build   one DoclingDocument from all readers' items
check   independent checks -> issues, each with page, box, kind, severity
export  review PDF, tables, figures, forms, markdown, RAG chunks
enrich  later passes: new versions, patch log, parent hash
```

## Page kinds: what each page and region is

Tier 0 says how a page's text is stored (a sound text layer, a scan, a broken
layer). It does not say what the page *is*. Until 2026-10-10 the route went by
storage alone, so a transcript, a contents page, a form and a spreadsheet all
went to one reader (docling's layout and TableFormer) and its mistakes were
patched in the checks afterwards: a transcript read as a two-column table, then
a check to undo that, then a rule to stop the check undoing real tables. The
`pages` stage decides what each page and region is first; the route sends each
region to the reader for its kind.

Two labels per page, never one: **where the text comes from** (text layer or
OCR) and **what the content is**. A scanned table is a table that needs OCR
first. And a page is not one kind: a page holds regions (a paragraph, a table, a
photo), each routed to its own reader. The page kind summarises its regions; the
document is only context (a page of numbered lines in a document that is mostly
numbered lines is a transcript; one numbered table in a report is not).

### The survey (2026-10-10)

300 documents drawn at random, 12 a year from 2002 to 2026 (3,027 pages;
[survey/sample-2026-10-10.csv](survey/sample-2026-10-10.csv)),
weighted back to the corpus by year. docling's layout model (Heron) on every
page, without OCR or table structure, at about 16 pages/s on the 4090; its raw
regions (before docling drops regions it found no text for, which hides them on
scans). A page's kind is the region group covering most of its content area,
page headers and footers left out. Contact sheets of each kind were checked by
eye.

| page kind | share of pages | of them scans | reader |
|---|---:|---:|---|
| text: letters, decisions, reports | 41% | 1% | text layer + layout: headings, paragraphs, lists |
| table | 23% | 22% | table readers, compared per table (see LESSONS) |
| drawing, map, photo, chart | 12% | 38% | picture classifier; the words printed on it from the text layer or OCR |
| form: fields, checkboxes, certificates | 10% | 47% | AcroForm values; labelled values (key/value) |
| mixed: text beside a table or picture | 6% | ~5% | each region to its own reader |
| empty or nearly | 3% | 99% | recorded blank; a faint scan goes to tier1 |
| contents and indexes | 2% | 12% | entries: title, page number |
| transcript (numbered lines) | 1% | 0% | numbered lines, each citable by page and line |

What the contact sheets showed, and what the stage must add to the layout model:

- **Forms are over-called.** The layout model marks a letter's address block as
  a key/value region, so letters, a résumé and an email read as forms. A form
  needs checkboxes, AcroForm fields or a `form` region, not key/value alone.
- **Rotation.** Landscape tables printed sideways are common (4262837, 247294);
  the page must be turned before tables are read.
- **Pictures need a class.** Map, aerial photo, site photo, chart (with axes),
  engineering drawing, chart-recorder disc (2931583): docling's picture
  classifier, on every picture.
- **Transcripts** are labelled `code` (monospaced type). Line numbers count only
  on one line pitch (a table's numbered rows are as tall as their wrapped cells:
  4656998 p39, 4647200 p432, 90732 p26) and only where most of the document is
  numbered.
- **Text pages** were right on the sheets checked, but for a map with little
  ink (3224341 p9) and a rotated brochure (2967481 p2).

Effort follows the shares: tables are the largest error source on the largest
non-text share of pages; transcripts, where the first fixes were spent, are 1%.

### What the stage records

`output/<id>/convert/pages-<sig>/<id>.pages.json.gz`, per page: the kind, the
text source, rotation, and every region (label, box, confidence), so a route
can be explained and re-run from it without the layout model. Measured each
time on the same 300 documents, kind by kind, against the labels checked by eye.

### tier1: only where tier0 cannot decide

On a thumbnail (about 150 dpi), CPU only:

| signal | how | for |
|---|---|---|
| page orientation 0/90/180/270 | RapidOCR angle classifier | scans and `hidden_ocr` pages before OCR |
| skew | Hough / projection profile | deskew before OCR; Azure reports this, docling does not |
| ruling lines on a scan | morphological open, long H/V kernels | scanned ruled tables -> line grid |
| blank vs near-blank | ink ratio | skip, and say so |
| barcodes | zxing-cpp | decode (`PDF417 -> CCG2414506`) |
| ink outside the text layer | render vs text boxes | stamps, handwriting, text baked into images on digital pages |

## Readers, and where docling fits

**The DoclingDocument is the container; docling's pipeline is one reader.**
docling-core builds documents directly (text, tables with cell spans,
pictures, key-value/form items, each with page + bbox provenance), so every
reader writes into the same format:

| reader | used for | grade |
|---|---|---|
| text layer, copied (PyMuPDF) | text regions on `sound` pages | exact |
| docling layout model (Heron) | regions on every page: text, title, table, picture, caption, footnote, formula, header/footer | structure |
| docling standard pipeline, RapidOCR `medium` | text on scans, `suspect` and `none` pages | OCR |
| someone else's hidden OCR layer | a second reading of scans that carry one | OCR, voter |
| line grid (vector rules / scan morphology) + text-layer words | ruled tables | exact text, exact grid |
| Table Transformer + text-layer words | borderless tables on digital pages | exact text, inferred grid |
| TableFormer (docling) | tables on scans; a third grid elsewhere | OCR / inferred |
| AcroForm fields | form values, checkboxes, radio buttons: 4,057 values in 151 documents | exact |
| annotations | FreeText and comment text, stamps, redactions | exact |
| PDF outline | headings where present (1,608 of 8,210 documents) | exact |
| docling heading hierarchy | headings elsewhere | structure |
| picture classifier (docling) | every picture: chart, map, photo, logo, signature, drawing | structure |
| olmOCR-2 / GLM-OCR / qwen3-vl (Ollama) | voters on disputed values; never the only source | OCR, voter |
| qwen3-vl:8b-instruct (Ollama) | figure descriptions, chart -> table | **generated** |
| code / formula (docling) | only regions the layout model labels formula | structure |

Docling's plugins (pluggy factories for layout, table structure, OCR and
picture description) let an outside engine replace a stage inside docling's
own pipeline. Docling's options apply per *document*, though, so per-page
routing is done here: a page is sent to the reader its route names, not
converted under one set of options for the whole file.

### Routes (first cut, from tier 0 on `source/`)

| page (tier 0) | pages | readers |
|---|---:|---|
| digital, sound | 76,370 | text layer + layout; tables by line grid / TATR / TableFormer; fields; annotations |
| scan, none | 4,354 | tier1 orientation + skew; OCR; TableFormer on tables |
| image_with_text / scan, hidden_ocr | 1,783 | OCR, and the hidden layer as a second reader |
| image_with_text, sound | 2,242 | text layer for the text; OCR inside the images |
| digital, suspect | 880 | OCR; the text layer is not trusted (the mojibake class) |
| vector_drawing | 1,755 | text layer; tiled OCR under a pixel budget; picture classifier |
| sparse / blank | 2,525 | tier1 decides: blank, a figure page, or a scan too faint to call |

## Provenance inside the document

Each item's page and box are docling's own `prov`. What docling has no field
for sits in a sidecar keyed by the item's `self_ref` (`#/texts/12`),
`<id>.items.jsonl`, one row per item:

```json
{"ref": "#/tables/3/cells/41", "page": 17, "bbox": [..], "reader": "line_grid",
 "reader_version": "convert-grid 1 / pdfplumber 0.11.10",
 "readers_agreeing": ["tableformer", "tatr"], "readers_disagreeing": [],
 "grade": "exact", "text_sha256": "..."}
```

Keeping this beside the DoclingDocument, not inside it, keeps the document
loadable by any docling tool, and makes the evidence queryable (flattened to
Parquet like tier 0).

Docling's confidence scores are recorded but decide nothing: they are
page-level, `table_score` is not implemented, and their computation may
change. In the old pipeline the worst failure, 316 pages of mojibake, came with
no warning at all.

## Checks and issues

`<id>.issues.json`, one row per problem, each located:

```json
{"page": 17, "bbox": [..], "kind": "text_not_extracted", "severity": "error",
 "evidence": "84 text-layer characters under no item", "check": "coverage 1"}
```

Checks that are cheap enough for every page:

- **coverage**: text-layer characters and ink under no item (`text_not_extracted`, `ink_not_extracted`)
- **readers disagree**: on a number, a cell, a grid (`value_disputed`, `grid_disputed`)
- **suspect cell**: one-character letter/digit swap against its column (the `<3hT` check)
- **numeric mojibake**: letter share against real words (legacy §14.2)
- **failed or skipped**: a reader that errored, a page too large for any scale
- **generated**: every retrieval aid, so nothing generated passes as evidence

## The review PDF

`<id>.review.pdf` is a copy of the original with every issue drawn as a PDF
annotation: red where something was not extracted or failed, amber where
readers disagree, blue for generated content, each with a popup giving the
kind, the evidence and the check. It opens in any PDF reader. The original is
never modified; the copy records the original's sha256 in its metadata.

## Later passes: fixes and enrichments

Follows docling's `enrich_doclingdocument` example: load the saved document,
open the PDF, crop each item by its box, run a model, change the items. Here:

- the PDF's sha256 must match the one the document was built from, or the
  pass refuses to run;
- the result is a new version, `enrich/<pass>-<sig>/`, with `patch.jsonl`
  (item, old, new, why, tool and version) and the parent version's hash;
- a pass selects its pages or items by a query over the issues or tier 0
  tables, e.g. every `value_disputed` in a table on a `scan` page, so a
  better model later can be pointed at exactly the pages it could fix.

## Output

```
output/<id>/
  <id>.cer.meta.json.gz                    scout
  convert/tier0-<sig>/<id>.tier0.json.gz     tier 0
  convert/convert-<sig>/
    <id>.docling.json.gz                   canonical
    <id>.items.jsonl.gz                    per-item provenance
    <id>.issues.json                       every problem, located
    <id>.review.pdf                        the original, annotated
    <id>.provenance.json                   route and timing per page, docling's scores, versions
    tables/t0001.json  t0001.csv           cells with boxes; CSV is a projection
    figures/f0001.png  f0001.json          crop; class; description (generated)
    forms.json  annotations.json
    <id>.md  <id>.chunks.jsonl             for RAG; each chunk lists the items it came from
    manifest.json                          sha256 of every file above
  convert/enrich/<pass>-<sig>/               later passes
```

Everything except the DoclingDocument and the items sidecar is a projection
that can be rebuilt from them.

## Run identity

As in tier 0, plus the models: the signature hashes the code's syntax tree,
every installed package, the settings, and **every model's weights** (Hugging
Face repo + revision + sha256 of each file; Ollama model digest). A package
version does not pin a model, and the legacy run id missed exactly that.

## Build order

1. `convert` on digital `sound` pages only: text layer + layout + fields +
   annotations + outline, into a DoclingDocument, with coverage issues and the
   review PDF. This is 84% of pages and the whole provenance path end to end.
2. Tables: line grid, TATR, TableFormer; grid and value disagreement.
3. Scans: tier1, OCR, hidden layer as voter, Ollama voters on disputed values.
4. Pictures: classifier on all; descriptions and chart-to-table as retrieval aids.
5. Exports: markdown, chunks, tables and figures as files.
6. Enrichment passes.

Each step is measured before the next: on a stratified sample across years and
page kinds, including the pages that got worse.

Steps 1 and 2 above, and the OCR route of 3, are built, routed by page. From 2026-10-10:

1. `pages`: layout regions and a kind for every page; the survey's 300
   documents as its test set.
2. Route each region by its label: text, tables, contents entries, transcript
   lines, form values, pictures. The transcript and dot-leader handling in
   `convert.py`'s checks moves into the route.
3. Measure on the 300, before and after, by kind; then the next kind by share.
4. The corpus.

## Open questions

- A small gold set: pages whose true text is known without typing it. Digital
  twins of scans (legacy lab found 320 at 90% overlap) and values proven by
  a sound text layer are the cheapest source.
- Sampling across 2012-2026: `source/` is mostly 2026, and older filings are
  probably more scan-heavy. Tier 0 on a spread of years answers that for
  about 75 minutes of CPU per 265,000 documents once they are downloaded.
- GPU: one RTX 4090, with Ollama sharing it. The legacy run lost 0.2% of
  documents to another process on the GPU (legacy §14.7): free VRAM must be
  checked before each model call, not once at start.
