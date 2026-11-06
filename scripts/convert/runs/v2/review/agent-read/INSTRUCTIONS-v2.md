# Finding new failure patterns in converted CER filings (discovery mode)

Working directory: /home/jasonvriends/repos/cer-regdocs5/scripts/convert/runs/v2/review/agent-read

We are building a catalogue of the DISTINCT ways machine conversion of these filings goes wrong,
to show why filings should be captured digitally. Occurrences of known patterns matter little;
patterns not yet in the catalogue matter a lot. The catalogue so far (full text with examples:
/home/jasonvriends/repos/cer-regdocs5/docs/conversion-failure-patterns.md, read it once):

| # | pattern | in the file? | what it does to AI | remedy |
|---|---|---|---|---|
| **A** | **The page is a picture** | | | |
| A1 | OCR drops or mangles whole lines | not in the file | answers built on text that is missing, silently | D1, D6 |
| A2 | A scanner's OCR layer passes for real text | not in the file | trusted errors; no check flags them | D1, D6 |
| A3 | Handwriting, stamps and signatures read as words | not in the file | junk tokens as content, even as headings | D1, signatures |
| A4 | Graphic marks read as characters (logos, symbols, boxes as Chinese) | not in the file | invented words in English and French text | D1, D3 |
| A5 | Handwritten corrections on typed text produce a plausible wrong value | not in the file | a different, valid-looking hearing number | D1 |
| A7 | Indigenous-language text read as junk or stripped (syllabics, special letters) | not in the file | Inuktitut becomes "uUAda ⊃AD qb ⊂" | D1, D4 |
| A8 | OCR substitutes look-alike characters in codes, ratings and identifiers | not in the file | "CL150" reads "CL152", "NAD1983" reads "NAC1983" | D1, D3 |
| A6 | OCR drops small marks or digits that carry value | not in the file | zero and "not reported" look the same | D1, T |
| **B** | **The text layer says something other than the page** | | | |
| B1 | Wrong characters behind right-looking glyphs | not in the file | wrong numbers in tables, with nothing to show it | D4, D5 |
| B2 | Symbol fonts: bullets and check boxes as letters | partly | "þ" for checked, "¨" for empty; meaning lost | D4, D5 |
| B3 | Words broken or run together by spacing | partly | search misses the word; tokens split | D3, D4 |
| B4 | Text drawn two or three times for bold | partly | "MMTP MMTP MMTP Complex Complex Complex" | D4 |
| B5 | A hidden text layer always replaced by the converter's own OCR, even where it was better | in the file | lines the layer had are lost to OCR | converter |
| B6 | Invisible text no reader sees surfaces as content | in the file | "uu-PCG-4559451-aa PCG" in a table | converter |
| **C** | **Forms** | | | |
| C1 | The answer is a picture or a widget, not text | not in the file | which option was chosen is lost | T (data as data) |
| C2 | Labels and values come apart | partly | a value attached to the wrong field | T, S |
| C3 | A check box read in the opposite state | partly | the form says No where the filer ticked Yes | T |
| **D** | **Reading order** | | | |
| D1 | Bold lead-ins split from their text (orders' recitals, run-in headings) | partly | reasons read under the wrong lead-in; sentences without a subject | S (tags) |
| D2 | Letterhead and sidebar blocks land inside the address or subject | partly | sender, addressee and subject mixed | S |
| D3 | Two columns or neighbouring lines interleaved | partly | sentences made of two sentences | S |
| D4 | A word moved out of its sentence | partly | a quotation that changes meaning | S |
| D5 | Rotated text read with its words in scrambled order | partly | column headers that say nothing | S |
| D6 | A page stored rotated read in reverse or scrambled order | in the file | a whole landscape page unreadable | converter |
| **E** | **Structure** | | | |
| E1 | Numbered paragraphs become headings | partly | a document of 100 "sections" one paragraph long | S |
| E2 | Headings that are not on the page | in the file | spurious sections from file names and stamps | S |
| E3 | List numbers and bullets kept as text | in the file | "- 1." lists; numbering not machine-readable | S |
| E4 | Footnote markers fused to words; hyphens kept at line ends | partly | "process.22", "navi- gation": search and quotes break | D1, S |
| E5 | Superscripts flattened into numbers, formulas and units | partly | 76 kPa with footnote 4 reads "764" | D4, S |
| E6 | A wrapped line starting with a number taken for a list item | partly | plan numbers in a land description altered | S |
| E7 | Transcript furniture mixed into the speech | partly | line numbers and time codes inside quotations | S |
| **F** | **Tables** | | | |
| F1 | A table over several pages filed under its first page | in the file | "page 21" holds nothing; citations point to the wrong page | T, S |
| F2 | A table's own row numbers taken for margin numbering | in the file | "Line 4 + Line 9" with the line numbers gone | T |
| F3 | A dense numeric table collapses into a column in one cell | partly | every number detached from its row | T |
| F4 | Cells shifted, split or merged | partly | values under the wrong column header | T |
| F5 | A table's units and notes not attached to its numbers | partly | "1,745" without "$000s" | T |
| F6 | Row numbers offset against their rows | partly | "Line 17" returns the neighbouring row | T |
| F7 | A sub-table's header separated from its rows | partly | crime rates read as disease data | T, S |
| F8 | Neighbouring tables merged into one | partly | Medium crude tolls read as Light crude | T |
| F9 | Text from outside a table (footnotes, margin stamps) spliced into a row | partly | a requirement's wording changed; a form's edition date read as a record's date | T |
| **G** | **Drawings, maps and charts** | | | |
| G1 | Labels without order or relation | not in the file | findable words, no meaning; charts' values unreadable | T |
| G2 | Chart labels read into the table beside it | partly | a date two years off, in a table cell | T |
| G3 | Box-like marks read as form check boxes (legends, redaction boxes) | not in the file | a plot legend reads as answers ticked on a form | T |
| **H** | **Pages and files** | | | |
| H1 | Bilingual side-by-side text read across both languages | partly | French and English half-lines merged | S, filed as two texts |
| H2 | Content cut off or missing in the filed PDF itself | not in the file | a column or a language half missing for every reader | D1 |
| I1 | Pictures misclassified or repeated | in the file | one logo printed 3-4 times; a map called a screenshot | - |
| **J** | **Marks that carry meaning** | | | |
| J1 | Tracked changes flattened: deleted and inserted text read as one | partly | deleted clauses quoted as in force | file the clean final |
| J2 | Colour or highlight carries the meaning | partly | which items apply is lost | T; never meaning by colour alone |
| J3 | Icons and indentation carry the hierarchy | partly | exhibits and their attachments indistinguishable | S, T |
| **K** | **Redaction** | | | |
| K1 | Redaction leaves a silent gap; exemption stamps read as numbers | not in the file | a sentence missing a name with nothing to say so | redaction rules |
| K2 | Redacted text still in the file under the black box | in the file | withheld text can leak into search | real redaction |

## Task

Your batch file `batches/batch-NNN.tsv` lists 25 documents: doc_id, pages to read, page count,
year, producer, document type. For each document:

1. Skip it if `out/batch-NNN.jsonl` already has its doc_id (you may be resuming).
2. For each listed page run `../excerpt.sh <doc_id> <page> 150` and read the Markdown.
   (`<!-- page N -->` starts page N; `*[class]*` is a picture; `| ... |` a table.)
3. Note which catalogue patterns you see, by id (A1, D1, ...). Do not verify or describe these.
4. Look hard for anything that fits NO pattern above, or a variant different enough that the
   catalogue's description would not cover it (different cause or different consequence). For each
   candidate, run `./render.sh <doc_id> <page>` and Read the PNG: a new pattern counts only if the
   page image confirms that the output says something other than the page. Before calling text
   "missing", grep the whole `.md` (`/home/jasonvriends/repos/cer-regdocs5/output/<id>/convert/<id>.md`)
   for a phrase from it: text filed under another page is F1, not a loss.
5. Append ONE line of JSON to `out/batch-NNN.jsonl`:
   {"doc_id": "...", "pages_read": [..], "known": ["A1", "D1"], "new": [{"page": N,
   "name": "short name of the pattern", "page_shows": "what the page says", "output_says":
   "exact quote from the output", "why_it_matters": "for search / AI / a reader",
   "in_file": "yes|partly|no", "lesson": "what the converter could do about it", "checked_image": true}], "note": "one line"}
   One line per document, valid JSON. `new` is empty for most documents; that is expected.

When all 25 are written, create `out/batch-NNN.done` and finish with: the new patterns you
confirmed (name, doc_id, page, one line each), or "no new patterns". Do not edit any other file.
