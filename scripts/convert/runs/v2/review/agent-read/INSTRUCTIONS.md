# Reading converted CER filings: instructions for each reading agent

Working directory: /home/jasonvriends/repos/cer-regdocs5/scripts/convert/runs/v2/review/agent-read

## Task

Your batch file `batches/batch-NNN.tsv` lists 25 documents: doc_id, pages to read (comma-separated),
page count, year, producer, document type. For each document, in order:

1. Skip it if `out/batch-NNN.jsonl` already has a line with its doc_id (you may be resuming).
2. For each listed page run `../excerpt.sh <doc_id> <page> 150` and read the Markdown.
3. If something looks wrong and the page would settle it (order, a lost answer, a table, odd
   characters), run `./render.sh <doc_id> <page>` and Read the PNG it prints. Check at most 3
   pages per batch this way; prefer findings of severity "meaning".
4. Append ONE line of JSON to `out/batch-NNN.jsonl` (create it if needed), e.g. with a heredoc
   `cat >> out/batch-NNN.jsonl <<'J' ... J`, then go on to the next document:
   {"doc_id": "...", "pages_read": [..], "reads_correctly": true|false, "findings": [{"kind": "...",
   "page": N, "severity": "meaning|structure|noise", "quote": "exact short quote", "explanation": "...",
   "checked_image": true|false}], "note": "one line on the document"}
   One line per document, valid JSON, no line breaks inside it.

When all 25 are written, create `out/batch-NNN.done` and finish with a short summary: how many
documents read correctly, the findings by kind, and any fault that is NOT one of the kinds below
(describe it with the doc_id and page). Do not edit any other file. Do not read other batches.

## What you are looking for

You review the output of a PDF-to-Markdown converter for Canada Energy Regulator (CER/NEB) filings: letters, orders, applications, evidence, forms, tables, maps, transcripts, in English and French.

You get one chunk of one document's Markdown. `<!-- page N -->` marks where page N starts. `*[class]*` is a picture the converter found (logo, icon, signature, geographical_map, ...). Lines `| ... |` are tables. You do not see the PDF page; judge only what the text shows, and say so in the explanation when a finding is a suspicion only the page could settle.

The converter keeps every word; what goes wrong is where words go and what marks mean. Known faults, with their kind:
- leadin_order: an order's or permit's lead-ins (IN THE MATTER OF, BEFORE, WHEREAS, AND WHEREAS, IT IS ORDERED; RELATIVEMENT À, DEVANT, ATTENDU QUE) written as headings after the text they introduce, stacked, or cutting a sentence.
- form_answer_lost: a form's options printed with no sign of which was chosen (a loose *[icon]* nearby, "Yes No" with no mark), or a value separated from its label.
- paragraph_as_heading: a numbered paragraph or sentence written as a heading (##, ###, ####), often with its continuation as a "- " list item.
- word_order: words or lines out of order inside a sentence, a line or a table cell; two columns or two lines interleaved; a word moved to the end of a block.
- table_broken: a table whose cells do not hold its values (a whole column packed in one cell, rows written as plain lines after a header-only table, values shifted into the wrong column, a numbered column missing).
- row_numbers_removed: a line like `*Lines 147 148 ...*` that holds a table's or list's own numbers, not a transcript's margin.
- wrong_characters: text that is not what the page can say: CJK or other scripts in an English/French text (e.g. 而 for a logo, 口 for a symbol), Greek/Cyrillic/Arabic digits in numbers, private-use or replacement characters, Wingdings letters (þ ¨) standing for check boxes.
- broken_words: words split by spaces (letter-spaced "F I L I N G", "RECL AMAT ION") or joined; hyphenated line breaks kept ("navi- gation").
- page_misplaced: a page whose content is clearly missing under its own marker (only a header and footer) while its text appears elsewhere.
- ocr_noise: OCR garbage in the body (handwriting or stamps read as text, junk tokens), or OCR errors in what should be clean text.
- list_markup: numbered items written as "- 1.", doubled bullets "- •".
- other: anything else that would mislead a reader, a search or a quotation.

Report each distinct fault once per chunk with its kind, the page, how much it matters, and a short exact quote from the chunk that shows it. Severity: "meaning" when a reader would be misled about what the document says (wrong order of reasons, lost answer, wrong number, word in the wrong sentence); "structure" when headings, lists or tables are wrong but the sense survives; "noise" when it only clutters. Do not report a page header, footer or letterhead being present, maps and drawings being lists of labels, or empty form fields, which are expected. If a fault is not in the list, use "other" and describe it so it can become a new kind. If the chunk reads correctly, return no findings. Be precise: a finding someone will check against the PDF.
