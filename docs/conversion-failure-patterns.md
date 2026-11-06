# What goes wrong when a machine reads REGDOCS filings: the patterns

Every kind of failure seen when 150,000+ CER filings were converted to text for machines to read,
one entry per distinct pattern, not per occurrence. It is the list to hand anyone planning to put
AI over REGDOCS (search, question answering, summaries, extraction): these are the problems they
will meet, and they come from how the filings were captured.

Each pattern says what the page shows, what reaches the machine, what that does to an AI system,
and **whether the information is in the file at all**:

- **In the file**: better software can recover it; the converter is at fault (listed so no one
  mistakes it for a capture problem, and because every AI pipeline will have to solve it again).
- **Partly**: the file holds the pieces but not how they go together; software can only guess.
- **Not in the file**: only pixels hold it; any machine must infer it, and inference makes errors
  no one can see. Only capturing it digitally at filing time removes the pattern.

The remedy column points to the requirements in [digital-first-filing.md](digital-first-filing.md)
(D = born digital, S = structure, T = data as data; see that document for the evidence behind each).

Evidence: the full conversion pass (v2, 2026-10-19 to 11-06), 210 documents read by hand and
1,050 read by agents against their page images and text layers (`scripts/convert/runs/v2/review/`):
random draws stratified by document type and producer, then targeted draws of schedules and tolls,
transcripts, French filings, drawings, Indigenous engagement filings and 2015 scans. Signatures
counted on every converted document (`scripts/convert/review_scan.py`). Rates are given only to
show a pattern is real and common; they are not the point. Document ids are REGDOCS ids. The register behind this page (each pattern, up to five examples with
page and quotes, and the lesson for the converter) is `scripts/convert/runs/v2/review/patterns.json`
(`scripts/convert/patterns.py list | show ID`).

## The patterns at a glance

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
| E7 | Transcript structure broken (numbers in the speech, turns split) | partly | quotations interrupted; a question attributed to the witness | S |
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
| J3 | Icons, indentation and braces carry the relationships | partly | exhibits and attachments, counsel and client, indistinguishable | S, T |
| **K** | **Redaction** | | | |
| K1 | Redaction leaves a silent gap; exemption stamps read as numbers | not in the file | a sentence missing a name with nothing to say so | redaction rules |
| K2 | Redacted text still in the file under the black box | in the file | withheld text can leak into search | real redaction |

---

## A. The page is a picture

Scans, photographs and pages flattened to images hold no text; text is read back by OCR. In the
full pass OCR supplied the text of 21% of pages (12% in 2026, 32% in 2018 and 2024).

**A1. OCR drops or mangles whole lines.** *Not in the file.*
The page says "Rule 12, Liability of the Carrier, paragraph (b) has been updated to clarify the
liability of the carrier for indirect losses"; the machine gets "Éule C lit of the carrier for
indirect losses" (2955501, Enbridge tariff letter, 2016 scan). The opening sentence of a French
specification's clause 8.1 is gone, leaving "e a", "o d" (3117386 p156, text inside an image); "from
time to time ... permit all persons participating ... to communicate" became "from im m e ... as er
te with each other" (4735582 p12). A faxed letter loses whole lines ("unacceptable amount of stress to
the already fragile population. A spill at the wrong", 3710044 p75), leaving "n on". A page with a
partial text layer (75 words, judged sound) and OCR for the rest comes out "- 23 ts - -", "- 24
-related that in" (3717098 p2). The converter's own completeness check said 100%: it compares the
output with what OCR read, and OCR is the only witness. *For AI:* the missing sentence is not
marked missing; an answer drawn from the page is wrong or incomplete and nothing says so.

**A2. A scanner's OCR layer passes for real text.** *Not in the file.*
A scan with the scanner's own OCR behind it looks digital: 3746809 p2 (an affidavit, OCR by I.R.I.S.)
was classed digital and its layer kept: "wr~ttenevidence", "tt18 best of my knowledge", and the
handwritten commissioner's block as headings `#### ,1' AAA-Z ! "7, J-O;L J`. 8,108 pages classed
digital are a single full-page image; on 2,350 of them an independent OCR agrees with the layer on
fewer than 90% of words. *For AI:* errors arrive with the authority of a text layer.

**A3. Handwriting, stamps and signatures read as words.** *Not in the file.*
Signatures become "Hamilm", "Ahim Sos" (2897620, a hearing panel's letter); an exhibit stamp
becomes "This is Exhibit / of Ronald Eric / before me at / this 7 / 739" (3718917); a mail-room stamp
becomes a heading (2998266). On handwritten inspection forms the values themselves are
unreadable (4257912). *For AI:* noise that looks like content, at the top of the page where
models weigh it most.

**A4. Graphic marks read as characters.** *Not in the file.*
OCR's dictionary forces logos, symbols and boxes into letters, mostly Chinese: the Trans Mountain
logo is 而 on every Trans Mountain filing; on a schematic every valve is 口 (3802842 p15); 中, 六,
必, 回 in drawings; a Tsuut'ina letterhead is 米. On digital pages these OCR-only marks are inserted
into sentences: "emergency response philosophy 金 focuses on" (2969783). Check marks in a table become "7", "1" or "L", changing what a count of
ticks says (4368547). 19.6% of documents hold
such characters, 554,068 in all. Some filings really are in Chinese (4105885, a mill certificate),
so they cannot simply be deleted. *For AI:* invented tokens inside English and French text.

**A5. Handwritten corrections on typed text produce a plausible wrong value.** *Not in the file.*
A letter's subject has a handwritten scribble over the last digits of "GH-003-2018" and a handwritten
3 over "Round 3"; the machine gets "GH-003-2013NOVA Gas Transmission Ltd." and "submit Round
Information Requests" (3803169), while the cc line on the same page still says GH-003-2018. *For
AI:* not junk but a different, real-looking hearing number.

**A6. OCR drops small marks that carry value.** *Not in the file.*
A schedule shows zero as "$ -"; OCR reads the "$" and not the dash, so Materials & Supplies,
Prepayments and Deferrals have empty cells (3390240 p2, a scan). *For AI:* zero and "not reported"
are the same, and the sum cannot be followed. A calibration certificate's "99.99°C" reads "99.9°C"
(4368429 p14).

**A7. Indigenous-language text read as junk or stripped.** *Not in the file.*
OCR built for Latin script turns Inuktitut syllabics into Latin letters and symbols: a letterhead's
tagline becomes "uUAda ⊃AD qb ⊂" (3096662, a letter to the NEB); a greeting in an Indigenous
orthography loses its superscript letters (4667022). *For AI:* in filings about Indigenous
engagement and rights, the Indigenous-language text is exactly what cannot be read.

**A8. OCR substitutes look-alike characters in codes, ratings and identifiers.** *Not in the file.*
Words survive OCR errors because readers and models correct them; codes do not. A drawing's
material list "150LB FLANGE, RFWN, A105N, CL150" comes out "158LB FLANGE, RFWN, A185N, CL152"
(3748169); "NAD1983" becomes "NAC1983" and "CGVD28" "CGVC28" (2957277). *For AI:* a pressure class or
datum that is wrong but well-formed.

## B. The text layer says something other than the page

A digital PDF can draw the right glyphs while storing other characters behind them.

**B1. Wrong characters behind right-looking glyphs.** *Not in the file.*
2967975 (Energy East, 376 pages) shows "(100)" in its table headers and stores `ϭϬϬͿ`; 2957200 shows
3 and stores `٣`; a journal article shows "P-value <0.001" and stores "o0.001" (3718783). Both pages pass every check as sound digital text. Smaller cases of the same kind:
French narrow spaces stored as a Tamil sign ("suivantesௗ:", Justice Canada's Acts, 3718909); table of
contents leaders stored as the replacement character (2786529). A shift that lands on ordinary
letters cannot be seen at all. *For AI:* numbers that are wrong while the page looks right; the
most dangerous pattern, because no reader of the PDF would ever notice.

**B2. Symbol fonts: bullets and check boxes as letters.** *Partly in the file.*
Wingdings and Symbol characters come through as Latin letters or private-use code points: a checked
box is "þ", an empty one "¨" (3768118, Application to Participate); `~` and `{` for options
(2857182); `` and `` bullets (4289041). 9.0% of documents hold private-use characters (759,847).
The font name says what they mean; nothing in the text does. *For AI:* a filled form reads as
"English þ French ¨".

**B4. Text drawn two or three times for bold.** *Partly in the file.*
Some tools fake bold by printing the same text again at a tiny offset; the text layer then holds
every copy: "MMTP MMTP MMTP Complex Complex Complex", "Dorsey Dorsey Dorsey" (3096924, 4724669).
*For AI and search:* repeated words, inflated counts, broken phrases.

**B3. Words broken or run together by spacing.** *Partly in the file.*
Titles spaced for effect come out "F I L I N G R E C E I P T" (2784712); wide kerning splits
words: "T HOMPSON-NI COL A REGIONAL DIST RICT", "RECL AMAT ION" (3335408). *For AI and search:*
the word is not there to be found.

**B5. A hidden text layer always replaced by the converter's own OCR.** *In the file.*
Every page whose text layer is invisible (a scanner's OCR, or a CAD tool's searchable copy) took its
text from the converter's own OCR, 107,588 pages in all; the layer was set aside even where it was
better. A scanned letter's layer reads "The purpose of this letter is to notify interested
parties"; the output has "He e t" (4247461). CAD tools draw text as lines and add an invisible copy so the sheet is searchable. The converter
took that copy for a scanner's hidden OCR and used its own OCR instead, which lost the drawing's
notes: "EXCAVATION NOTE: TRENCH SHORING WILL BE USED ...", "VERTICAL DATUM: CANADIAN GEODETIC
VERTICAL DATUM 1928" came out as "R SED", "VER E" (4248055, a Trans-Northern drawing sheet; the text
layer had all 863 words). 3,307 pages in 608 documents carry a "hidden OCR" layer with no image under
it to have been read from. *For AI:* the safety notes on a drawing vanish; a converter lesson, and
the reason CAD text should be exported as text.

**B6. Invisible text that no reader sees surfaces as content.** *In the file.*
A report's text layer hides internal-link anchor ids in 4-point text behind the visible labels; the
page shows "PCG", the machine gets "uu-PCG-4559451-aa PCG", 22 times (4590652 p60). *For AI:* tokens
no person ever saw, in tables and answers.

## C. Forms

**C1. The answer is a picture or a widget, not text.** *Not in the file.*
The CER's own export application forms mark the chosen option with a green check-mark image: the
machine gets all the options as plain lines and a loose `*[icon]*` (3934254, 4496452 confirmed by
image; ~10,000 such documents, 2015-2026, English and French). A reporting form's radio button
"(•) No" comes out "Yes No" (4572357). Drawn check boxes are read (SSRS forms: `☒ [English]`), and
forms the CER's e-filing system prints carry their answers as text; the pattern is answers that
are pixels. *For AI:* "Is the applicant a corporation?" has no answer in the text.

**C2. Labels and values come apart.** *Partly in the file.*
Two-column form rows are read across: "Nom : M Carl Téléphone: 716-684-5411 / Hasselback Poste :"
(3716451); a submission date printed after the product list, far from its label (4577847); two
neighbouring labels merged word by word: "explain Exempt Confirm why pursuant exemption exemption to
is MH‐001‐2013?" (4572357). *For AI:* a value read as belonging to the wrong field.

**C3. A check box read in the opposite state.** *Partly in the file.*
The page shows "The wetland is not fish bearing" ticked and "No" ticked; the machine gets
"☐ [The wetland is not fish bearing...]" and "☐ [☑ No]" (2774076): the form field's stored value
and the drawn tick disagree, and the empty value wins. *For AI:* not a missing answer but the
opposite one.

## D. Reading order

A PDF stores text in drawing order, not reading order. Without tags (structure), the machine
guesses the order from positions.

**D1. Lead-ins in their own column put reasons under the wrong heading.** *Partly in the file.*
Orders and permits set "IN THE MATTER OF", "BEFORE", "WHEREAS", "AND WHEREAS" (French
"RELATIVEMENT À", "DEVANT", "ATTENDU QUE") in a bold column beside the text. They come out as
headings after the paragraph they introduce, stacked, or splitting a sentence ("## ATTENDU QU'au"
after "moyen de l'ordonnance ...", 4239846); in 3321223 the lines of one recital are reordered.
About 56% of the 17,026 orders are affected, every year and every producer. The same
happens to bold run-in headings anywhere: "Section 2 describes the importance of ..." becomes a
heading "Section 2" and a sentence with no subject (3900173 p8); "Incident Management:" split off
after its question (3724204 p12); a French condition's opening phrase "Au moins 90 jours avant la
mise en service des installations électriques," split into two headings around its sentence
(4652907 p4). *For AI:* an order's
recitals are its reasons; read under the wrong lead-in, the reasons are misattributed.

**D2. Letterhead and sidebar blocks land inside the address or subject.** *Partly in the file.*
A sender column is read between "Attention:" and the addressee's name (4364682, 4372528); a law
firm's address lands inside the "Re:" block (4403573); bilingual letterheads read across
("National Energy Office national / Board de l'énergie"). Found on letters from most filers.
*For AI:* who wrote to whom about what, the first thing any summary needs, is tangled.

**D3. Two columns or neighbouring lines interleaved.** *Partly in the file.*
Body text in two columns read line by line across both (3117386 p118, a French specification;
3348046 p7, agreement clauses 7.5-7.10); bilingual receipts merged half-line by half-line
(4518996). *For AI:* sentences made of two sentences.

**D4. A word moved out of its sentence.** *Partly in the file.*
In a quotation of the Constitution Act, «peuples autochtones du Canada» comes out «peuples du
Canada», with "autochtones" at the end of the footnote (4648280): a small region cut through the
line. 2.5% of documents have at least one word moved this way. *For AI:* a quotation that is
exact in every word but one, and that one changes the meaning.

**D5. Rotated text read with its words in scrambled order.** *Partly in the file.*
Vertical column headers come out with their words shuffled: "Distance of Wetland from Access Road
(m)" reads "of from Road Distance Wetland Access (m)", "Wetland Unique ID" reads "ID Wetland Unique"
(3309458 p36). *For AI:* columns whose labels no longer say what they hold.

**D6. A page stored rotated read in reverse or scrambled order.** *In the file.*
A landscape infographic stored with /Rotate 270 has a correct text layer ("Transportation accounts
for 23% of the total energy that Canadians consume — second only in consumption to Canada's
industrial sector"); the machine gets "Canada's to consumption in only second — consume Canadians",
and on another page one word per line in reverse (2957894 p20, p23). 179,146 pages in about 17,700
documents are stored rotated; in a sample of 40, half kept fewer than half of the text layer's word
pairs. *For AI:* whole pages of prose unreadable; a converter fault.

## E. Structure

Headings, paragraphs and lists are visible to a reader but not stored in most PDFs, or stored
wrongly.

**E1. Numbered paragraphs become headings.** *Partly in the file.*
Law-firm templates make every numbered paragraph a heading, and Word writes each as a bookmark:
3954446 (a final argument) has 134 bookmarks such as "20. As a result, and based on ...". The
paragraph's first lines become a heading and the rest a list item. One in five final arguments
(19.8%) and 8.5% of written evidence come out this way. *For AI:* a document of a hundred
one-paragraph "sections"; chunking and navigation by section are meaningless.

**E2. Headings that are not on the page.** *In the file.*
File names, stamps, labels and bookmarks become headings: a merged PDF's bookmarks all point at page 1,
so a cover page that shows only "Section 6 - Lands" gets "## sec_87(1)_crown_notification_pipeline_v2010.pdf"
and "#### TO: <ENTER LANDOWNER(S) NAME(S)>" (2838739); "## winter road map.vsd" (3561254), "## C61 cover
page" over four levels (4142865), "## E-FILE" (4109467). *For AI:* invented section titles.

**E3. List numbers and bullets kept as text.** *In the file.*
"- 1. The term of this Order shall commence ..." and "- • Landowner requests": the item is a bullet
holding its own number or bullet glyph (27% and 22% of documents). *For AI:* minor, but list
numbers ("condition 3") are how filings are cited.

**E4. Footnote markers fused to words; hyphens kept at line ends.** *Partly in the file.*
"facility planning process.22" (3954446), "navi- gation", "mar- itime" (consolidated Acts,
2812079); 13.5% of documents keep line-end hyphens. *For AI and search:* broken words and numbers
glued to text.

**E5. Superscripts flattened into numbers, formulas and units.** *Partly in the file.*
A raised footnote mark joins the number before it: Trans Mountain's crude quality table gives a
vapour pressure limit of "≤70 / 76" kPa with footnote 4, and the machine gets "≤70 / 764" (4460101
p5). Exponents become trailing words: "(1 + Line 9) raised to Line 10" reads "(1 + Line 9)Line 10"
(4639857, a contribution formula); "750 people/km²" with footnote 2 reads "km2.2" (4652224). *For
AI:* a number that is wrong by a factor of ten, in a specification, with nothing to show it.

**E6. A wrapped line starting with a number taken for a list item.** *Partly in the file.*
A legal description of lands wraps as "... PART / 1 25R6353, PART 1 ON 25R10709 SUBJECT TO AN /
EASEMENT AS L."; the machine makes the line starting "1" a bullet and swaps the last two lines
(4680777). *For AI:* plan and part numbers in a legal description altered.

**E7. Transcript furniture mixed into the speech.** *Partly in the file.*
Numbered lines and time codes come out inside the speech: "1 04:01:00 questions. 2 04:01:01 PRESIDING
COMMISSIONER KATHY PENNEY: Thanks very 3 04:01:04 much." (4305597); a printed page number left as a
stray number (4555824); one transcript exported as a table on some pages and a list on others
(3777308); a split turn puts the Chair's "Mr. Yates?" inside Mr. Yates's own answer (3062862). *For
AI:* quotations interrupted by numbers; words attributed to the wrong speaker.

## F. Tables

**F1. A table over several pages filed under its first page.** *In the file.*
A 286-row table over pages 1-4 is written under page 1 (3306261); page 21 of an Energy East
mitigation table holds only its header (2969783); page 138 of a Manitoba Hydro filing shows a
Peguis First Nation comment and the response, written under page 137 (3790697). Readers of the
Markdown take it for lost text. 5.2% of substantial pages have most of their text
under another page's number. *For AI:* page citations ("PDF page 21") cannot be found or are wrong.

**F2. A table's own row numbers taken for margin numbering.** *In the file.*
A commitments table's numbers 147-162 are removed from their rows (4051056); a financial schedule's
"Line" column is removed while its rows say "Total Rate Base (Line 4 + Line 9 + Line 10)" (4087662).
*For AI:* items that are cited by number can no longer be found by number; arithmetic cannot be
followed.

**F3. A dense numeric table collapses.** *Partly in the file.*
TC Canadian Mainline's IT bid floor table (4166098) comes out as a header row and one cell holding
the whole Average column ("0.1035 1.6473 0.5986 ..."), with the receipt and delivery points as plain
lines after it. *For AI:* every toll detached from its path; the table cannot be queried.

**F4. Cells shifted, split or merged.** *Partly in the file.*
Rows shifted one column left (4142865 p4), a 14-column grid for a 6-column table (2926972), cell
text merged across rows (4165029: a corporation's name scrambled over two rows). *For AI:* values
under the wrong header.

**F5. A table's units and notes not attached to its numbers.** *Partly in the file.*
Units ("$000s", "10³m³/d"), "(unaudited)", footnotes and column dates sit in captions or notes; the
cells carry bare numbers. *For AI:* "1,745" without knowing it is thousands of dollars.

**F6. Row numbers offset against their rows.** *Partly in the file.*
A surcharge table's Line 1 (Suffield 2 / Union SWDA / 3.88543) comes out under Line 2, with an
empty Line 1 and the last two rows fused (3884322 p29). *For AI:* "Line 17" returns its neighbour.

**F7. A sub-table's header separated from its rows.** *Partly in the file.*
A monitoring plan's "Metro Vancouver Region" crime-rate rows are written after the next section's
heading, "Communicable Diseases" (3336044 p38, Table 9). *For AI:* the rows read as data about
something else.

**F8. Neighbouring tables merged into one.** *Partly in the file.*
Three tariff tables stacked on one page (Light, Medium and Heavy crude) come out as one table; the
second and third lose their header rows and their titles are fused into the previous table's last
cell: "(6.2803) Uncommitted Line 9 IJT Joint Rate ... Medium Crude ..." (3084351). *For AI:* a
toll read under the wrong product.

**F9. Text from outside a table spliced into a row.** *Partly in the file.*
A form's margin stamp "Form Date: Feb 27, 2013" lands in a consultation record's June 8, 2015 row
(3179563 p8).
A footnote to row 6.0 ("The hydro-testing contractor provided all hydro-test fluid and trucked it
away") is woven into row 10.0's cells: "Details including 1 The hydro-testing | regarding any
unsuccessful pressure tests ... contractor provided ..." (2902086 p3). *For AI:* a requirement that
reads differently from the filing.

## G. Drawings, maps and charts

**G1. Labels without order or relation.** *Not in the file.*
Maps, alignment sheets, schematics and charts give their labels as a bag of words: "Fraser Kiver
Coquitlam # # KP 1167" (3306697), a chart recorder's scale as numbers (3005387), a population chart's
axis as "Population 09 size" (3718493). *For AI:* the words can be found; what they label, and any
value a chart shows, cannot.

**G2. Chart labels read into the table beside it.** *Partly in the file.*
A schedule table with a Gantt chart beside it takes the bars' date labels as cells: "11-May-23 |
18-May-21", "9-M3$a-23" where the page says 18-May-23 (4553651 p16). *For AI:* a valid-looking date,
two years off, inside a table.

**G3. Box-like marks read as form check boxes.** *Not in the file.*
Redaction placeholder boxes labelled "PII" become "☒ [PII 31-Jan-2022 12:31 (Mountain Time), rev 1]"
(4464158 p5, a management-of-change form).
A borehole log's legend of plot symbols (triangle, open square, filled square) comes out as
"☐ [Field Vane Test] / ☒ [Remoulded] / ☒ [Torvane]" (2958070 p17). *For AI:* a chart reads as a form
with answers chosen, and the plotted data are not there.

## H. Pages and files

**H1. Bilingual side-by-side text read across both languages.** *Partly in the file.*
Receipts and notices set English and French in two columns; the machine merges them line by line
(4518996). *For AI:* neither language is readable on its own.

**H2. Content cut off or missing in the filed PDF itself.** *Not in the file.*
A schedule titled "Schedule A: Project Map" with no map on the page (2950002 p8).
A table column ends at the page edge ("Dat / 20-Au", 2813818 p5); the French half of an
acceptance text runs off the page (4572575 p2). The conversion matches the page: the loss happened
when the document was made into a PDF. *For AI and for every reader:* the text is gone.

## J. Marks that carry meaning

**J1. Tracked changes flattened.** *Partly in the file.*
Blacklines filed to show what changed (red strike-through for deleted text, blue underline for
inserted) lose the marks: "Pro formaProforma", "this capacitythe Design Capacity", and struck-out
clause 9.1(a) as live text (3895437, a transportation services agreement v.1 to v.2); "687945/August
2018June 2020", "rare elementfeature" (3935185 p77, a condition compliance filing). The marks are
drawn lines; the text has no sign of them. *For AI:* requirements that were deleted are quoted as
in force, and the words of both versions run together. In coordinates the digits of both versions
fuse into one wrong, valid-looking value: "46053857114E 63307660815N", "KP 16+49063 to 16+516490"
(4272854 p8).

**J2. Colour or highlight carries the meaning.** *Partly in the file.*
A spreadsheet printed to PDF marks the items that apply by highlighting their cells; the text has
no trace of it, so every item reads the same (3578263). *For AI:* which rows matter is lost.

**J3. Icons and indentation carry the hierarchy.** *Partly in the file.*
An exhibit list marks documents with a PDF icon and attachments with a folder icon, indented; the
machine gets "囚" and `*[icon]*` (3898988). A hearing's list of appearances groups counsel with
braces against the party they act for; the machine gives the names, then the parties (2837588).
*For AI:* which item belongs to which, and who represented whom, is lost.

## K. Redaction

**K1. Redaction leaves a silent gap; exemption stamps read as numbers.** *Not in the file.*
An access-to-information release blanks a name and stamps the ground: the page shows "s.17",
"s.19(1)" and "We should involve [blank] as well in this"; the machine gets "5.17 / 5.19(1)" and
"We should involvi as well in this, as he is the Project Engineer" (4574102 p20). *For AI:* a
sentence that reads complete, with no sign that text was withheld or why.

**K2. Redacted text still in the file under the black box.** *In the file.*
A black box drawn over text that is still in the text layer: 59 words on one page (4493099), found
on 1,110 pages of 231 documents. The converter keeps them out of its output; any tool that does
not will index and quote what was meant to be withheld.

---

## What this says about AI on REGDOCS

- **Counting words does not catch any of this.** Every document above passed the converter's
  checks with 100% of its words. The patterns are about where words go and what marks mean.
- **41 of the 50 patterns are not, or only partly, in the file.** Software can fix the 9 that are
  (B5, B6, D6, E2, E3, F1, F2, I1, K2) once; the rest need inference from pixels or positions, and
  inference makes errors that look like answers: a pressure class read CL152, a hearing number read
  GH-003-2013, a vapour pressure read 764. Vision models add their own (section 7 of
  [filing-guidance.md](filing-guidance.md): they make up data).
- **Each pattern has a filing-side cure**: born-digital export with real text (A, B), forms filed as
  data or as fillable fields (C), tagged PDF so reading order and structure travel with the
  document (D, E), tables and drawings filed as data beside the PDF (F, G).

*Status (2026-11-06): complete for the document kinds in REGDOCS as read.* 50 patterns in 11 families:
14 not in the file, 27 partly, 9 in the file (the converter's own). New patterns per
round of 100 documents fell 4, 5, 6, 3, 2, 0, 2; the targeted rounds found two more (A8 codes, E7
transcripts) and then none in four kinds read specifically. Not yet tested properly: filings
written in Indigenous languages (A7 rests on two documents; selecting by title found none). A
pattern is added when a reader finds a failure that fits none of these and confirms it against the
page.
