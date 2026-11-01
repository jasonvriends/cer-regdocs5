# PDF extraction tools

Tools others use to turn PDFs into structured text and JSON, grouped by how
they are usually deployed. Listed as of mid-2026; not yet benchmarked here.

## Hosted services

The most common approach: send each document to a hosted service and spot-check
the output. cer-regdocs2 did this with Azure. It is fast to adopt, but every page
is OCR'd, even where the PDF holds exact text, and the evidence trail is weak.

- Azure Document Intelligence
- AWS Textract
- Google Document AI
- Adobe PDF Extract
- Reducto
- LlamaParse
- Unstructured
- Mistral OCR

## Open-source toolkits

Run locally. Usually one tool runs per document, with its defaults.

- docling
- MinerU
- Marker / Surya
- PaddleOCR (PP-StructureV3, PaddleOCR-VL)
- olmOCR
- dots.ocr
- GraniteDocling
