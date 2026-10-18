# What 269,025 Filings Taught Us

*Copy of the report kept in Claude Docs ([live version](https://claude.ai/code/artifact/9c51e394-b73b-4ad3-b24c-74aca8315b40)); deck: [Filing for AI and Automation](https://claude.ai/artifact/Dpw5xoaVKWBV7kiriCT8Vk), slides in [ceo-deck/](ceo-deck/).*

Filing for AI and automation at the Canada Energy Regulator · Oct 18, 2026 · @Jason Vriends

## Summary

**Nearly a quarter of what the CER receives cannot be trusted by a machine as filed: 62,520 of 269,025 documents (23.2%), about 740,000 pages, are pictures of text or carry text that does not match the page.** We counted this by reading every page of every PDF in REGDOCS.

This matters because every AI or automation tool reads the file, not the page a person sees. Where the file holds no text, a machine guesses; where it holds the wrong text, a machine quotes it; where a vision model fills the gap, it can invent a value that looks right. The values that matter most to decisions - results in tables, limits, names, addresses - are where the errors are likeliest.

The cause is how files are produced, not what they are about: scanners and copiers make non-digital documents 97-100% of the time, office and reporting software almost never. Printing documents to sign them and scanning them back is the largest single source. Integrity rests on paper: only 0.5% of documents are digitally signed, and REGDOCS publishes no fingerprint that would prove a copy is the one filed.

**The choice:** check the data by hand (about 23 person-years for the backlog, at a stated assumption), automate and manage the risk (cheap, but errors are only flagged), or require digital-first filing so the data is right at the source. **We recommend digital first for every new filing, automation for the existing record, and people only where the machine flags doubt** - with an automatic check at upload that tells a filer which page failed, while the source still exists.

## What we did

We downloaded every PDF REGDOCS lists from 2002 to October 2026 and read each one page by page: **269,025 documents, 3,615,229 pages, 1.23 billion words.**

- **Every page, two ways.** The text stored in the file was compared with an independent reading of the page image (OCR) wherever the page had images or doubtful text. Each page got a record of how it was made and how far the two readings agree.
- **The census.** Those records were gathered into one database and counted by year, by the software that made the file, and by REGDOCS document type. The counts match an earlier random sample of 5,000 documents to the decimal.
- **The integrity pass.** Every file was opened once more to record its digital signatures, what was saved after them, and its structure (bookmarks, tags, forms, PDF/A).
- **The rules.** The findings were set beside the CER's current Filer's Guide to Electronic Submission (June 2025) and Filing Manual chapter 1.
- **Six attempts.** This is the sixth pipeline built over REGDOCS since July 2026, including a cloud build on Azure; each taught something the next one used.

No company is named in this report: the causes are practices and tools, and the same practice produces the same file whoever uses it.

## What the files are like

**23.2% of documents are mostly not digital, and it is how a file is made that decides it.** Pages: 74.6% hold their own text exactly; 11.2% are images with no text; 4.8% are scans carrying a scanner's hidden OCR text; 3.0% are scans with a few stamped words; 1.1% hold text that decodes to nonsense; 0.4% are text drawn as outlines.

*Chart: share of documents mostly not digital by year filed, 2002-2026 - see the year table in [the census report](../scripts/convert/runs/v2/reports/2026-10-18-census.md).*

The share is falling but not on its own: one publishing system undid years of progress in 2024, and 16,254 of the non-digital documents were digital until something degraded them on the way to filing.

| Software that made the PDF | Documents not digital |
| --- | --- |
| Reporting systems | 0.0% |
| Office software | 1.7% |
| Print drivers | 5.9% |
| PDF editors | 21.5% |
| Scanners and copiers | 97-100% |

| REGDOCS document type | Documents not digital |
| --- | --- |
| Rulings | 0.2% |
| Hearing transcripts | 0.7% |
| Orders | 2.5% |
| Letters | 30.6% |
| Requests to make an oral statement | 64.9% |
| Affidavits | 88.2% |

The CER's own outputs are digital. What is not is what gets signed: correspondence and sworn documents are 46% of all non-digital documents, because they are printed, signed and scanned back.

## Why it matters for AI

**Search tools and language models read the text inside the file, not the page a hearing panel sees - and on a quarter of the record those are not the same thing.**

| What the file holds | How much | What an AI tool gets |
| --- | --- | --- |
| Page images with no text | 406,093 pages | nothing until OCR guesses, with no record of which guesses are wrong |
| A scanner's hidden OCR text | 172,381 pages | a reading that differs from an independent one by more than 10% on 67% of these pages; the file does not say which is right |
| A scan with a few stamped words | 107,949 pages | a file that tests as searchable and holds a stamp's worth of words |
| Words pasted as pictures in digital documents | 140,804 pages with 20+ such words | the document's text, minus its tables and inserts |
| Words under black redaction boxes | 313 documents flagged | the hidden words, as ordinary text - in those checked, landowners' names and addresses |

**Vision models do not fill these gaps safely; they make up data.** In tests on pages whose text was known exactly:

- A model reading a contact table changed a street number, rewrote a postal code, replaced two e-mail addresses with invented ones and added a recipient who does not exist - with nothing marking any of it as a guess.
- The best of five models got one number in twenty wrong; on scans, models agreed on 99% of words but only 73-92% of numbers.
- Two of five models wrote text on blank strips every time.
- Every model lost almost all the words of an engineering-drawing table.

A missing value can be seen and checked; an invented one looks exactly like a real one. The values a model is likeliest to get wrong - results in tables, limits, names, addresses - are the ones decisions rest on. A model may help choose between two readings or flag doubt; it must never be the source of a value.

## Integrity

**Nothing in the record lets anyone prove a file is the one filed: the signature of record is a paper receipt, and REGDOCS publishes no fingerprint of any file.**

- **Digital signatures are rare: 1,410 of 269,025 documents (0.5%)** - 21 of 2,023 affidavits, 398 of 54,213 letters - though rising, from 40 a year in 2018 to 224 in 2025.
- **Digitally signed documents stay digital:** only 73 of the 1,410 are mostly not digital, against 23.2% overall. Signing digitally removes the print-sign-scan step that produces most scanned correspondence.
- **What happened after signing:** 993 signatures cover the whole file; 312 were followed only by validation data that e-signature services add so a signature can be checked years later; 105 had something else saved after signing, from a metadata update to annotations and stamps added on pages. Telling those apart takes a signature validator.
- **The same file under several IDs:** 3,308 files appear under 8,252 REGDOCS IDs (4,944 extra copies).

For automated decisions, integrity has to be provable later without paper. A fingerprint (SHA-256) published for every file at receipt, digital signatures to the PAdES standard, a trusted timestamp in place of the paper receipt, and a signed manifest listing every file in a filing would make each filing tamper-evident and machine-readable in the same step.

## The choice

**There are three ways to make the record fit for AI; we recommend combining them, with digital-first filing for everything new.**

|  | A. Validate by hand | B. Automate, manage the risk | C. Digital first at the source |
| --- | --- | --- | --- |
| What it means | people check extracted data against the page | machines read everything; doubtful pages are flagged | filers submit files a machine can read exactly; an automatic check at upload |
| Existing record | about 23 person-years for the 740,000 doubtful pages; about 113 for every page | about $20,000 of cloud extraction, or days of a local GPU | does not reach the existing record |
| New filings | 0.6-1.5 people a year | flagged, not prevented | errors prevented at the source |
| Main risk | cost and time | errors flagged, not prevented; no hand-checked ground truth yet | filer adoption; a rule and a check are both needed |

Person-years assume 3 minutes to check a page against its image and 1,600 working hours a year; they are estimates, not measurements. Cloud cost is $5.53 per 1,000 pages, measured on 90,484 pages in an August 2026 cloud build. New-filing volume is 10,000-48,000 doubtful pages a year since 2020.

**Recommendation: C for every new filing, B for the existing record, and A only on the pages B flags.** The backlog is where the cost is; the flow of new filings is small enough to fix at the source, and a single publishing system in 2024 showed how fast the problem returns without a rule.

## Recommendations, ranked by value

**An automatic check at upload comes first, because it turns every other recommendation from advice into a rule; after it, digital signing and "export, don't scan" fix the most documents.** Today e-filing accepts PDF only, image-only PDFs are "not recommended" rather than refused, and nothing is checked at upload.

| # | Recommendation | Reach in the record | Why it ranks here | Effort |
| --- | --- | --- | --- | --- |
| 1 | Check every file automatically at upload | every problem below | turns each ask into a rule while the filer still has the source; the checker exists | CER system; tool exists |
| 2 | Sign digitally, sworn documents too | 28,592 non-digital documents (46%) | the largest single source; digitally signed documents stay digital | process |
| 3 | Export the PDF; never scan, flatten or paste screenshots | 16,254 documents degraded by digital tools; 140,804 pages with pictures of text | avoidable at no cost; one system made 4,137 in a year | a setting |
| 4 | Fingerprint and timestamp every file, published on the record | every file | integrity provable without paper | CER system, small |
| 5 | File data as data: tables as CSV with a data dictionary, maps as GeoPackage, through e-filing | tables are the weakest step of every extraction (samples) | the values decisions rest on, and where AI readers invent most | rule and e-filing change |
| 6 | Redact by removing the text, and say what was withheld | 313 documents flagged | each real case exposes personal information | a tool |
| 7 | Text that matches the page: OCR checked, fonts that decode (PDF/A-2u), no hidden duplicate text | 172,381 scanner-OCR, 107,949 stub and 38,596 undecodable pages | text that looks right and is not is the most dangerous case for AI | setting or process |
| 8 | Tagged PDF: headings, tables and lists marked as such | 54% of documents untagged; almost half of tagged ones mark their tables (sample of 50) | exact headings and table cells instead of model guesses | a setting |
| 9 | Bulk access, and metadata published with each file | the whole record | every automated use starts here | CER system |
| 10 | A signed manifest per filing | every filing | integrity and a machine table of contents in one | proposal |
| 11 | Bookmarks on long documents, live forms filled electronically, no JavaScript | 18,024 long documents without bookmarks | structure the author already had | a setting |
| 12 | Readable page sizes, whole documents | 7,682 documents with oversize sheets; 20 MB splits | keeps drawings and long documents whole in the record | rule and CER limit |
| 13 | The file is a real document | 20 of 860 recent downloads were not | a missing document is invisible to AI | intake check |

The ranking weighs reach (counts from the census), risk (a wrong value or a privacy leak weighs more than a missing one), effort and leverage; it is a judgement, the counts are not. Not worth requiring: rules on page rotation, document length, fonts by name or table formatting - none affected how well a page could be read. The full guidance gives each recommendation's evidence, proposed Filing Manual wording and intake check.

## What the CER's own systems should fix

**Not every problem is the filer's; these were found in REGDOCS itself while collecting the record.**

| Found | Evidence | Fix |
| --- | --- | --- |
| No fingerprint of any file | REGDOCS shows no hash; the signature of record is a paper receipt | publish a SHA-256 per file; timestamp the receipt |
| No bulk or programmatic access | the record could only be collected by crawling the website, one request a second | a bulk download or API, with each file's fingerprint and metadata |
| Metadata only on the website | document type, company and filing come from page listings, not the file | publish the metadata with the file |
| Files served empty | 6 of 860 recent downloads were 0 bytes | reject empty uploads; repair the records |
| Documents served as web pages whose images are not public | filing receipts and forms show broken images | publish them as PDF, or open the images |
| Records that answer "not found" | 73 recorded PDFs | mark withdrawn documents |
| The same file under several IDs | 3,308 files under 8,252 IDs | link copies to one file |
| Unsearchable placeholders | oversize drawings and paper-only documents are kept off the digital record | accept drawings and their data electronically |
| Documents cut into 20 MB pieces | the upload size limit | raise the limit for born-digital files |

The Commission's review of its Rules of Practice and Procedure, unchanged since 2000, already lists modernizing electronic filing and service; it is the natural place for both the filer asks and these fixes.

## What we learned building it

**Six attempts to turn REGDOCS into trustworthy data taught the same lesson from different directions: extraction can recover much, but only at a cost, and never with certainty - the reliable fix is at the source.**

| When | Attempt | What it showed |
| --- | --- | --- |
| Oct 11-18 | Current converter: every page of every document | the census and integrity findings in this report; counted, not estimated |
| Oct 10 | First version of the current project | a 5,000-document random survey; each page's risks can be known before reading it |
| Sep 19-23 | Open-source extractor tuned for accuracy | 316 badly broken pages produced no warning at all; a vision model invented addresses and e-mails |
| ~Sep 12 | Local inventory with AI models | output that passed every check still mislabelled what pages contained |
| Aug 7-18 | Cloud build on Azure, with search and AI answers | the cloud was 4.5 times faster and finished every file, at about $500 for 90,000 pages, but also reported no warnings on content it got wrong |
| Jul 10-22 | First prototype with a question-answering app | answers came within days, on text nobody had verified |

What they teach together:

1. **The worst failures are silent.** Every tool, cloud and local, lost or garbled text without saying so. Only a second, independent reading exposes it.
2. **There is still no hand-checked ground truth.** Accuracy claims so far are agreement between machines. About 100 pages checked by people would turn them into measured accuracy.
3. **AI models may vote, never supply the value.** Four attempts reached that rule independently.
4. **Build the evidence before the application.** Search and answers on unmeasured text look finished and are not.
5. **Name what produced every output, and record every page.** Without the code and file fingerprints, two results cannot be compared; without a record for every page, "fine" and "not noticed" look the same.
6. **Lessons are relearned unless they are built in.** Two lessons this month had already been written down by earlier attempts. A check in the pipeline is a rule; a note in a document is advice - the same is true of filing guidance.

## What is not yet known, and the decisions needed

**Five decisions would act on what is known; three questions remain open.**

Decisions:

1. Put digital-first filing into the Rules of Practice and Procedure review, where modernizing e-filing is already on the list.
2. Pilot the automatic check at upload; the checker exists.
3. Accept data files and digital signatures through e-filing; fingerprint and timestamp every file.
4. Review the 313 documents with words under redaction boxes now - some expose personal information.
5. Have people check about 100 pages as ground truth before automated extraction is relied on for decisions.

Still open:

- **Tables, figures and forms across the whole record.** This pass read text and how each page was made; table and form findings come from samples. A full extraction of structure would count them. It needs an estimated week or more of computing time, still to be measured, and has not been started.
- **Which reading is right where two disagree.** On 67% of scanned pages with hidden OCR text, two readings differ; ground truth would settle it.
- **How many redaction flags are real leaks.** 313 documents are flagged; only the ones reviewed so far are confirmed.

A search tool over the whole record is the next phase, once the full extraction is authorized.
