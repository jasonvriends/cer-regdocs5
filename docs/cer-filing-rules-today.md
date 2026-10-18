# What the CER's filing rules say about electronic files today

The CER's rules say **what** information a filing must contain - the Filing Manual's chapters and
Guides A to CC (engineering, environmental and socio-economic assessment, lands, tolls, ...). They
say little about **what form** it must take for a machine to read it, or for anyone to prove it
unaltered. This page sets out the rules as published (read 2026-10-17) beside what the corpus shows,
as the starting point for [filing-guidance.md](filing-guidance.md) and
[digital-first-filing.md](digital-first-filing.md).

Sources:
- [Filer's Guide to Electronic Submission](https://www.cer-rec.gc.ca/en/applications-hearings/regulatory-document/filers-guide-electronic-submission.pdf), last revised 2025-06-16 (26 pages).
- [Filing Manual, Chapter 1](https://www.cer-rec.gc.ca/en/applications-hearings/submit-applications-documents/filing-manuals/filing-manual/filing-manual-chapter-1-introduction.html) - 1.5 Confidential Filing, 1.11 Filing with the CER, 1.12 Filing Digital Location Data; modified 2025-07-21.
- [Filing Manual index](https://www.cer-rec.gc.ca/en/applications-hearings/submit-applications-documents/filing-manuals/filing-manual/).
- The Commission's review of the *National Energy Board Rules of Practice and Procedure, 1995*
  (not updated since 2000): Phase I engagement in fall 2024 listed modernizing electronic filing and
  service, including removing the need for hard copies. Submissions included support for digital
  signatures and for ending paper copies after e-filing (e.g. [Pembina](https://cer-rec.gc.ca/en/about/news-room/whats-new/2024/rules-practice-procedure-feedback/pembina.pdf),
  [South Bow](https://cer-rec.gc.ca/en/about/news-room/whats-new/2024/rules-practice-procedure-feedback/south-bow.pdf),
  [Beaver First Nation](https://cer-rec.gc.ca/en/about/news-room/whats-new/2024/rules-practice-procedure-feedback/beaver-first-nation.pdf)).
  No revised rules found as of 2026-10-17.

## The rules, and what the corpus shows

| topic | what the rules say today | what the corpus shows | gap |
|---|---|---|---|
| **Format** | "Only PDF files are accepted." (Filer's Guide, Step 5) | tables, data and drawings arrive as pictures of themselves inside PDFs; tables are the weakest part of every extraction | no way to file data as data through e-filing |
| **Searchable text** | "PDF images can be uploaded, but are not recommended ... their content is not searchable." | 20.4% of 267,495 documents are read entirely by OCR, 8.2% partly; 33% of those dated 2002-2011 [corpus] | image-only PDFs are discouraged, not refused, and nothing checks at upload |
| **Text that is wrong** | nothing | scanner OCR layers and stub layers that look searchable but are not; layers that decode to nonsense (0.5% of pages, 19% of one CAD tool's) [survey] | a PDF can pass as searchable while its text is not the page |
| **Fonts** | "Use fonts supported on standard Windows installation such as Arial and Times New Roman" | what matters for extraction is that fonts are embedded with Unicode maps, not which font | the rule targets display, not extraction |
| **Size** | files over 20 MB are rejected; up to 50 documents a filing; start key sections on a new page "to facilitate cutting large PDF files into 20 MB files" | large documents split across files; scans are large (the guide itself says image PDFs "tend to be very large") | splitting breaks documents; born-digital files rarely need it |
| **Large pages** | no PDF if not legible at 11 x 17 in or smaller: file on paper only; a placeholder is created | 2.3% of documents have sheets over 17.5 in [survey]; placeholders "cannot be viewed or searched" (Ch. 1.11) | the largest drawings leave the digital record altogether |
| **Bookmarks** | "Create bookmarks for documents that have a table of contents ... mirror the table of contents" | half of documents over 20 pages have none [survey]; mail-merge outlines that belong to other documents (4049986: 136 bookmarks on 2 pages) [corpus] | asked for, not checked |
| **Security settings** | "Do not activate any security settings" (accessibility) | counted by the integrity pass | asked for, not checked |
| **Structure / accessibility** | security settings only | 47% of documents tagged [survey] | tagged PDF not asked for |
| **Forms** | nothing | live form fields in 67 of 5,000; checkboxes printed flat [survey, samples] | |
| **Redaction** | confidential material filed on paper (Ch. 1.5): cover letter, redacted public version, unredacted copy in a sealed envelope | a cover-up redaction left 13,159 words readable [2026-09 corpus]; whole pages filed as black rectangles, saying nothing of what was removed [corpus] | how to redact a public version is not described |
| **Signatures** | the filer prints, signs and mails the **filing receipt** with one paper copy of each document within three business days | affidavits 79%, letters 23% not digital [survey]: signed documents are scanned | the legal anchor of an e-filing is a wet signature on paper; no digital signature route |
| **Integrity** | "Once you have submitted the form, your document will be immediately viewable ... and cannot be altered." | the scout hashed every file it downloaded (SHA-256); REGDOCS shows no hash; the same file is filed more than once (4656998 = 4657424 = 4657571) | nothing lets a reader prove a copy is the one filed |
| **Files that are not documents** | nothing | of 860 PDFs downloaded 2026-10-17: 6 empty (0 bytes), 13 web pages, 1 desktop shortcut ("08BurdReport.pdf"); 73 others answer "not found" [corpus] | nothing checks the upload is a readable PDF |
| **Maps and GIS** (Ch. 1.12, since 2021) | "compressed Esri file geodatabase or shapefile"; WGS 84, NAD83 CSRS or Lambert (EPSG 4326, 4617, 3979); files under 10 MB **by e-mail** to the Secretary, larger ones **on a USB drive by mail** | OCR reads about two-thirds of a drawing's labels [samples] | GIS data travels outside the e-filing system, beside the record rather than in it; no attributes asked for |
| **Data use** | the filer grants the CER the right to use filings for "creating models to support automated decision-making, as well as developing datasets, maps, visualizations" (Terms 2d) | | the licence already anticipates datasets and AI; the formats do not support it |

## What this means for the guidance

1. **The licence is ahead of the formats.** Filers already grant the CER the right to build
   datasets and models from their filings; the rules then accept only PDF, and PDF of any quality.
2. **Most of the losses are allowed, not forbidden.** Image-only PDFs are "not recommended";
   nothing checks for them, or for text layers that are wrong, or for files that are not documents.
   A check at upload turns each recommendation into a rule
   ([filing-guidance.md, section 11](filing-guidance.md#11-check-at-intake-a-rule-not-advice)).
3. **Integrity rests on paper.** The signed paper receipt is the only signature; nothing a reader
   can check proves a downloaded file is the one filed. Hashes shown on the record, and digital
   signatures, would make each file verifiable without paper.
4. **Data already has a format rule (GIS), but not a route.** Chapter 1.12 names formats and
   coordinate systems, then sends the files by e-mail or USB. The same pattern - a format rule
   plus a route through e-filing - is what tables and drawings need.
5. **The Rules review is the opening.** Modernizing e-filing and dropping hard copies is already on
   the Commission's list; the corpus says what to ask for in their place.
