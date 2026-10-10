#!/usr/bin/env python3
"""Pass 1: convert the pages whose text layer can be trusted, and say exactly
what was not converted, where, and why.

    scripts/convert/.venv/bin/python scripts/convert/convert.py run [PDF or DIR ...] [--workers 2]
    scripts/convert/.venv/bin/python scripts/convert/convert.py status

Reads tier 0's record of each PDF and routes every page. This pass converts
the pages tier 0 calls `digital` with a `sound` text layer (84% of pages in
source/); every other page is recorded as pending with the route it needs
(OCR, hidden-OCR vote, ...) and marked on the review PDF, so nothing is left
out silently.

Readers, for a routed page:

  docling     layout (Heron), reading order, TableFormer (fast) for tables,
              heading levels from the PDF outline, numbering and font style;
              text from the PDF's own text layer via docling-parse. No OCR.
  Table Transformer  rows, columns and spanning cells of each table docling found
              (microsoft/table-transformer-structure-recognition-v1.1-all), filled
              with text-layer words: one more grid in the per-table choice
  MuPDF       an independent parse of the same text layer: every word on the
              page, used to check docling's output and to recover the text
              docling does not extract inside pictures and drawings.
  tier 0      form field values and annotations, straight from the file.

Checks, each written to `issues.json` with the page and box:

  text_not_extracted      words under no extracted item (error)
  text_not_in_table_cell  words inside a table but in none of its cells (error)
  table_text_lost         words inside a cell's box that no cell's text holds (error);
                          every table also gets a word score: placed, wrong cell,
                          no cell, lost
  table_rows_merged       table rows run together: two cells side by side each hold
                          several lines at the same heights, one of them all values
                          (warning)
  text_disagrees          docling's reading of an item differs from the text layer's
                          words in its box (info: the item's text is the text layer's;
                          docling's reading is kept in `orig`)
  ocr_disagrees_with_hidden_layer  on a scan with its own OCR layer, our OCR of an
                          item differs from that layer's words in the box (a vote,
                          not a verdict; `error` when a number is involved)
  ocr_failed              an OCR page docling could not read; its own OCR layer, if
                          any, is used unchecked, else the page has no text (error)
  text_under_redaction    text-layer words under a redaction box (a dark fill over
                          them, nothing showing when rendered): left out of every
                          output; the issue gives the count, never the words (info)
  picture_text_added      text-layer words inside a picture, added to the
                          document as text under the picture (info)
  outline_heading_missed  an outline entry with no heading found for it (info)
  item_spans_unconverted_page  an item with text on a page this pass does not
                          convert, so part of it is from an untrusted layer (warning)
  docling_warning         something docling logged while converting the page,
                          e.g. table cells it dropped (warning)
  page_from_fallback_reader  docling could not parse the page even alone; its
                          text blocks come from MuPDF, without layout (warning)
  page_empty              a routed page with text but no items (error)
  page_identity_mismatch  a page in docling's output whose number or size is not
                          the PDF's (error); page_missing_from_output likewise
  converter_error         docling reported an error or failed a range (error)
  page_not_converted      a page this pass does not convert, and its route
                          (pending)

Everything lands beside the filing's other records:

  output/<id>/convert/convert-<sig>/
    <id>.convert.json.gz    this record: routes, docling's status, errors,
                            warnings and confidence per page, checks, hashes
    <id>.docling.json.gz    the document (DoclingDocument)
    <id>.items.jsonl.gz     one row per item: box, reader, agreement with MuPDF
    <id>.issues.json        every problem, located
    <id>.review.pdf         the original with every issue drawn on it
    forms.json              form fields and values
    annotations.json        annotations and their text

Boxes are in points, top-left origin, in the page as displayed (rotation
applied): the convention docling uses and a PDF viewer shows.

The run id covers this file and common.py, docling and everything it
requires, PyMuPDF, the settings, the sha256 of every model file under
scripts/convert/models/, and the tier 0 run whose records it reads.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DEFAULT_OUTPUT, DEFAULT_RUNS, HERE, Stage, add_run_arguments,  # noqa: E402
                    base_environment, clip, describe_basic, load_index, r, read_json_gz, release_memory, rss_gb, run_stage,
                    runs_of, sha256_bytes, sha256_file, utcnow, write_bytes_atomic)

STAGE = "convert"
MODELS = HERE / "models" / "docling"
TATR_REPO = "microsoft/table-transformer-structure-recognition-v1.1-all"
TATR_DIR = HERE / "models" / "tatr" / TATR_REPO.replace("/", "--")

SETTINGS = {
    "schema": 1,
    "route_rules": "route-1",          # which tier 0 pages this pass converts
    "device": "cuda",
    "table_mode": "fast",              # accurate won on bench/tables.py's 40 pages, but on the ten
                                       # (run convert-0f81d371) it merged 4672628's lab tables into a
                                       # third fewer cells and lost ~20,000 words from the export, at
                                       # 1.5x the time; it helped 4656998 and 4664902. Choose per table.
    "table_choice": ["fast", "accurate"],  # a page with tables is read in each mode; each table keeps
                                       # the reading that places the most text-layer words in their
                                       # cells (table_word_score). The first mode is the page's reading.
    "table_choice_below": 0.99,        # ...re-read only if a table places fewer of its words than this in
                                       # the first mode: 99% of the words gained, 44% of the table pages
                                       # re-read (the ten, run convert-42ebc408)
    "align_keep_below": 0.5,           # text-alignment grid kept only below half the badly placed words
    "tatr": {"threshold": 0.5,         # detection score below which a row, column or spanning cell is ignored
             "scale": 2.0,             # table crop rendered at 144 dpi, then resized to 1000 px on its long side
             "pad_pt": 10.0},          # around the table: the model was trained on padded crops
    "tatr_keep_below": 0.5,            # Table Transformer's grid kept only below half the badly placed words
    "fit": "fit-2",                    # table_structure_score: fit-1 + rows merged
    "table_cell_matching": True,
    "ocr": {"engine": "RapidOCR PP-OCRv6 small, torch, GPU", "renderer": "pypdfium2",
            # docling-parse rendered 4710294's scans as noise (black, coloured streaks) where MuPDF
            # and pdfium render them cleanly; layout and OCR then found nothing. OCR pages are read
            # through docling's pdfium backend.
            "full_page_mode": "full_page", "images_mode": "pdf_aware_layout_regions",
            "hidden_vote_threshold": 0.8},
    "heading_hierarchy": True,
    "chunk_pages": 1,                  # one page per docling call, as legacy ingest did: every status,
                                       # warning, error, confidence and timing belongs to one page, and
                                       # memory is released after each
    "page_source": "original",         # docling reads the filed PDF itself (page_range), never a copy:
                                       # a one-page copy was faster (0.194 s/page against 0.259) but
                                       # docling read a filled form's copy differently (201 items and
                                       # 88 errors against 186 and 6), though MuPDF saw no difference
    "docling_source": "original; signature /Reference cut by incremental update",
    "log_capture": "root, WARNING+",   # TableFormer logs as "MatchingPostProcessor", outside docling's tree
    "merge": "keep-page-numbers-1",    # see merge_chunks()
    "box_pad_pt": 1.5,                 # a word counts as inside a box within this margin
    "agreement_pad_frac": 0.25,        # comparing text in a box: the margin is at most this share of the
                                       # box's height or width (1.5 pt spans a whole line of 1.5 pt text)
    "agreement": "words-3",            # MuPDF's words in the box (scaled margin) against docling's text
    "review_max_per_kind": 50,         # annotations per kind per page in review.pdf; issues.json has all
    "agreement_threshold": 0.95,       # below this similarity, an item is disputed
    "agreement_min_chars": 3,
    "cluster_gap_pt": 18.0,            # uncovered words closer than this form one issue
    "outline_match_ratio": 0.8,
    "min_free_gpu_mb": 6000,
    "page_timeout_s": 300,             # docling's document_timeout, per page since each call is one page
    "review_pdf": "original bytes + incremental update",
    # Step 3: pages tier 0 says are too heavy for docling skip it and take their text from
    # the text layer. docling's time limit does not stop a page mid-step: page 11 of 4681376
    # (30,642 paths) ran an hour. (4656998's 12 GB was not a page: see docling_source.) To be calibrated on the baseline; a page is heavy if it is oversized AND over
    # either limit.
    "structure_budget": {"long_side_pt": 1260, "max_paths": 10000, "max_words": 2500},
    # Steps 1 and 2: every text-layer word on a converted page is kept. A word no docling
    # item claims becomes a text item of its own (reader "text layer"), so text is never
    # lost to a missed region; the evidence file records who claimed each word.
    "evidence": "every word, claimed or added",
    "export": "markdown with text under pictures and furniture; every evidence word checked",
    "export_min_recall": 0.98,         # share of evidence words that must reach the Markdown export
}

ROUTES = {
    # (tier 0 origin, text layer) -> route. First match wins; None = any.
    "rules": [
        (("digital", "sound"), "text_layer"),
        (("digital", "suspect"), "ocr_untrusted_text_layer"),
        (("digital", "hidden_ocr"), "ocr_with_hidden_vote"),
        (("digital", "none"), "vector_only"),
        (("scanned_ocr", None), "ocr_with_hidden_vote"),
        (("scanned", None), "ocr"),
        (("raster", None), "ocr"),
        (("image_with_text", None), "text_layer_and_ocr"),
        (("blank", None), "blank"),
        ((None, None), "undecided"),
    ],
    "converted_now": {"text_layer", "ocr", "ocr_with_hidden_vote", "ocr_untrusted_text_layer", "vector_only",
                      "text_layer_and_ocr"},
    # Which converter reads each route. "ocr_full": the whole page is OCR'd and the text layer, if
    # any, is not used for the text (at most as a second reader). "ocr_images": the text layer is
    # read as on any digital page, and OCR reads only regions it does not cover (images).
    "reader": {"text_layer": "text", "ocr": "ocr_full", "ocr_with_hidden_vote": "ocr_full",
               "ocr_untrusted_text_layer": "ocr_full", "vector_only": "ocr_full",
               "text_layer_and_ocr": "ocr_images"},
}

ROUTE_REASONS = {
    "ocr_untrusted_text_layer": "the text layer is suspect (unmapped or implausible characters); needs OCR",
    "ocr_with_hidden_vote": "a scan with someone else's OCR layer; needs OCR with that layer as a second reader",
    "vector_only": "no text layer; needs OCR of the drawn content",
    "ocr": "a page image with no text layer; needs OCR",
    "text_layer_and_ocr": "digital text over a page image; needs the text layer plus OCR of the image",
    "blank": "blank page",
    "undecided": "tier 0 could not say what this page is",
}

SEVERITY_COLOURS = {  # review PDF: stroke colour per severity
    "error": (0.85, 0.1, 0.1),
    "warning": (0.95, 0.6, 0.0),
    "info": (0.1, 0.45, 0.85),
    "pending": (0.5, 0.5, 0.5),
}


# --------------------------------------------------------------------------
# identity: models and the tier 0 run

def model_identity() -> dict:
    """Per model directory: the Hugging Face revision and a hash over every file."""
    out = {}
    if not MODELS.exists() or not TATR_DIR.exists():
        return {"error": f"{MODELS} or {TATR_DIR} missing: download the models first"}
    for d in sorted([p for p in MODELS.iterdir() if p.is_dir()] + [TATR_DIR]):
        files = sorted(p for p in d.rglob("*") if p.is_file() and ".cache" not in p.parts)
        h = collections.OrderedDict((str(f.relative_to(d)), sha256_file(f)) for f in files)
        revs = set()
        for meta in (d / ".cache").rglob("*.metadata") if (d / ".cache").exists() else []:
            first = meta.read_text().split("\n", 1)[0].strip()
            if re.fullmatch(r"[0-9a-f]{40}", first):
                revs.add(first)
        out[d.name] = {"repo": d.name.replace("--", "/", 1), "revision": sorted(revs),
                       "files": len(files), "bytes": sum(f.stat().st_size for f in files),
                       "sha256": sha256_bytes(json.dumps(h).encode())}
    return out


def tier0_run(runs: Path, wanted: str | None) -> Path:
    candidates = runs_of(runs, "tier0")
    if wanted:
        candidates = [d for d in candidates if d.name in (wanted, f"tier0-{wanted}")]
    if not candidates:
        raise SystemExit("no tier 0 run found: run scripts/convert/tier0.py first")
    return candidates[-1]


def gpu_free_mb() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[0])
    except Exception:
        return None


class Convert(Stage):
    name = STAGE
    code_files = [Path(__file__).resolve(), HERE / "common.py"]
    packages = ["docling", "pymupdf"]
    settings = SETTINGS
    worker_module = "convert"
    min_start_gap_s = 5.0

    def __init__(self, tier0_dir: Path):
        self.tier0_dir = tier0_dir
        self._models = None

    def models(self):
        if self._models is None:
            self._models = model_identity()
        return self._models

    def worker_meta(self):
        return {"tier0_dir": str(self.tier0_dir)}

    def extra_identity(self):
        return {"models": self.models(), "tier0_run": self.tier0_dir.name, "routes": ROUTES["rules"]}

    def environment(self):
        import importlib.metadata as m
        env = base_environment()
        env.update({"docling": m.version("docling"), "docling_core": m.version("docling-core"),
                    "docling_parse": m.version("docling-parse"), "pymupdf": m.version("pymupdf"),
                    "models": self.models(), "gpu": _gpu_name()})
        try:
            import torch
            env.update({"torch": torch.__version__, "cuda": torch.version.cuda})
        except Exception:
            pass
        return env

    def header_lines(self, env):
        return [f"docling  {env['docling']} (core {env['docling_core']}, parse {env['docling_parse']}), "
                f"torch {env.get('torch')} cuda {env.get('cuda')}, {env.get('gpu')}",
                "models   " + ", ".join(f"{k.split('--')[-1]}@{(v.get('revision') or ['?'])[0][:8]}"
                                         for k, v in env["models"].items() if isinstance(v, dict)),
                f"tier0    {self.tier0_dir}"]

    def progress_note(self, out: Path) -> str:
        files = sorted(out.parent.glob("*.progress.*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            return "stopped before its first page"
        started, done_ = None, set()
        for line in files[-1].read_text().splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "page_start":
                started = ev
            elif ev.get("event") == "page_done":
                done_.add(ev["page"])
        if started and started["page"] not in done_:
            return f"stopped on page {started['page']} (started {started['at']}, worker then at {started.get('rss_gb')} GB)"
        return f"{len(done_)} page(s) finished, none in progress"

    def telemetry_fields(self):
        return ["gpu_used_mb", "gpu_free_mb", "gpu_util_pct"]

    def telemetry_values(self):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
            return [x.strip() for x in out.split(",")]
        except Exception:
            return ["", "", ""]

    def gate(self):
        free = gpu_free_mb()
        if free is not None and free < SETTINGS["min_free_gpu_mb"]:
            return f"{free} MB GPU memory free, need {SETTINGS['min_free_gpu_mb']}"
        return None

    def describe(self, ev):
        s = ev.get("summary") or {}
        if not s:
            return describe_basic(ev)
        bits = [f"{ev['status']:<7}", f"{ev['doc_id']:<12}", f"{s.get('pages', 0):>5}p",
                f"{ev.get('seconds') or 0:7.2f}s",
                f"converted {s.get('pages_converted', 0)}/{s.get('pages', 0)}",
                f"items {s.get('items', 0)} (tables {s.get('tables', 0)}, pictures {s.get('pictures', 0)})"]
        sev = s.get("issues_by_severity") or {}
        if any(sev.values()):
            bits.append("issues " + " ".join(f"{k}:{v}" for k, v in sev.items() if v))
            kinds = s.get("issues_by_kind") or {}
            bits.append("[" + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:4]) + "]")
        if ev.get("reasons"):
            bits.append("why=" + ",".join(ev["reasons"]))
        if ev.get("error"):
            bits.append("error=" + clip(ev["error"], 200))
        return "  ".join(bits)


def _gpu_name():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None


# --------------------------------------------------------------------------
# the worker: models load once

class _Capture(logging.Handler):
    """Collects every warning and error logged while one page converts."""
    DENY = ("docling.datamodel.stage_model_specs",)   # identical on every run: about this code, not the filing

    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        if record.name.startswith(self.DENY):
            return
        if len(self.records) < 500:
            self.records.append({"logger": record.name, "level": record.levelname,
                                 "message": clip(record.getMessage(), 2000)})


def init_worker(meta: dict):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    def converter(mode):
        opts = PdfPipelineOptions(artifacts_path=MODELS, do_ocr=False, do_table_structure=True)
        opts.table_structure_options.mode = TableFormerMode(mode)
        opts.table_structure_options.do_cell_matching = SETTINGS["table_cell_matching"]
        opts.heading_hierarchy_options.enabled = SETTINGS["heading_hierarchy"]
        opts.accelerator_options = AcceleratorOptions(device=SETTINGS["device"])
        opts.document_timeout = SETTINGS["page_timeout_s"]
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        conv.initialize_pipeline(InputFormat.PDF)
        return conv, opts

    def ocr_converter(mode):
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions
        opts = PdfPipelineOptions(artifacts_path=MODELS, do_ocr=True, do_table_structure=True)
        opts.ocr_options = RapidOcrOptions(backend="torch", mode=OcrMode(mode))
        opts.table_structure_options.mode = TableFormerMode(modes[0])
        opts.table_structure_options.do_cell_matching = SETTINGS["table_cell_matching"]
        opts.heading_hierarchy_options.enabled = SETTINGS["heading_hierarchy"]
        opts.accelerator_options = AcceleratorOptions(device=SETTINGS["device"])
        opts.document_timeout = SETTINGS["page_timeout_s"]
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(
            pipeline_options=opts, backend=PyPdfiumDocumentBackend)})
        conv.initialize_pipeline(InputFormat.PDF)
        return conv, opts

    modes = SETTINGS["table_choice"] or [SETTINGS["table_mode"]]
    convs = {m: converter(m) for m in modes}
    conv, opts = convs[modes[0]]
    ocr_full, ocr_full_opts = ocr_converter(SETTINGS["ocr"]["full_page_mode"])
    ocr_images, _ = ocr_converter(SETTINGS["ocr"]["images_mode"])
    cap = _Capture()
    logging.getLogger().addHandler(cap)     # every logger: TableFormer's are not under "docling"
    return {"converter": conv, "capture": cap, "pipeline_options": json.loads(opts.model_dump_json()),
            "ocr_pipeline_options": json.loads(ocr_full_opts.model_dump_json()),
            "readers": {"text": conv, "ocr_full": ocr_full, "ocr_images": ocr_images},
            "table_modes": modes, "table_converters": {m: c for m, (c, _) in convs.items()}}


# --------------------------------------------------------------------------
# routing

def over_budget(t0page: dict) -> str | None:
    """Why a page is too heavy for docling, from tier 0's record of it; None if it is not."""
    b = SETTINGS["structure_budget"]
    w, h = t0page.get("width_pt") or 0, t0page.get("height_pt") or 0
    if max(w, h) <= b["long_side_pt"]:
        return None
    paths = (t0page.get("drawings") or {}).get("paths") or 0
    words = (t0page.get("text") or {}).get("words") or 0
    over = []
    if paths > b["max_paths"]:
        over.append(f"{paths:,} vector paths")
    if words > b["max_words"]:
        over.append(f"{words:,} words")
    if not over:
        return None
    return (f"oversized page ({w / 72:.0f} x {h / 72:.0f} in) with {' and '.join(over)}, "
            f"over the structure budget")


def signature_references(pdf) -> list[int]:
    """Signature dictionaries with a /Reference (DocMDP, FieldMDP): their /Data points
    back into the document, the catalog itself for DocMDP, and docling-parse 7.22
    follows it into the whole object graph while loading. 4656998 grew past 12 GB
    before its first page; 3 of 51 certified filings in tier 0 never loaded."""
    found, seen = set(), set()

    def ref(x, key):
        t, v = pdf.xref_get_key(x, key)
        return int(v.split()[0]) if t == "xref" else None

    def check_sig(x):
        if x and pdf.xref_get_key(x, "Type") == ("name", "/Sig") and pdf.xref_get_key(x, "Reference")[0] != "null":
            found.add(x)

    def fields(x, depth=0):
        if x is None or x in seen or depth > 20:
            return
        seen.add(x)
        check_sig(ref(x, "V"))
        t, v = pdf.xref_get_key(x, "Kids")
        for k in re.findall(r"(\d+) 0 R", v if t == "array" else ""):
            fields(int(k), depth + 1)

    cat = pdf.pdf_catalog()
    af = ref(cat, "AcroForm")
    if af:
        t, v = pdf.xref_get_key(af, "Fields")
        for k in re.findall(r"(\d+) 0 R", v if t == "array" else ""):
            fields(int(k))
    t, v = pdf.xref_get_key(cat, "Perms")
    for k in re.findall(r"(\d+) 0 R", v if t == "dict" else ""):
        check_sig(int(k))
    return sorted(found)


def docling_source(path: Path, pdf, work: Path, sha256: str) -> tuple[Path, dict]:
    """The file docling reads: the filed PDF itself, or, when it holds signature
    references, a copy that is the filed bytes unchanged plus one incremental update
    setting those /Reference entries to null. Pages, fonts and content are the
    original objects; only the signatures' link back to the document is cut."""
    import pymupdf
    import shutil
    sigs = signature_references(pdf)
    if not sigs:
        return path, {"file": "original"}
    work.mkdir(parents=True, exist_ok=True)
    copy = work / f"{path.stem}.docling-source.pdf"
    shutil.copyfile(path, copy)
    d = pymupdf.open(copy)
    for x in sigs:
        d.xref_set_key(x, "Reference", "null")
    d.saveIncr()
    d.close()
    size = path.stat().st_size
    with open(copy, "rb") as f:
        prefix = sha256_bytes(f.read(size))
    return copy, {"file": "original + incremental update", "why": "signature /Reference removed",
                  "signature_xrefs": sigs, "bytes_added": copy.stat().st_size - size,
                  "prefix_is_original": prefix == sha256}


def route_for(origin: str | None, layer: str | None) -> str:
    for (o, l), route in ROUTES["rules"]:
        if (o is None or o == origin) and (l is None or l == layer):
            return route
    return "undecided"


def chunked(pages: list[int], size: int):
    """Routed pages in runs of consecutive pages, at most `size` per run."""
    run = []
    for p in sorted(pages):
        if run and (p != run[-1] + 1 or len(run) >= size):
            yield run[0], run[-1], run
            run = []
        run.append(p)
    if run:
        yield run[0], run[-1], run


def merge_chunks(chunks, name):
    """One document from the chunks, every page keeping its own number.

    DoclingDocument.concatenate renumbers pages to run on from the previous
    document (pages 58-70 after 1-56 become 57-69). Its merge buffer offsets
    each document by its running `_max_page`; setting that to the page before
    each chunk makes the offset zero (or `shift`, for a chunk whose pages are
    numbered from 1). A private detail of docling-core, so the page identity
    check in process() verifies the result on every document.
    """
    from docling_core.types.doc import DoclingDocument
    idx = DoclingDocument._DocIndex()
    for pages, shift, d in chunks:
        if not d.pages:
            continue
        # offset = _max_page - min(d.pages) + 1; make it `shift`, so the chunk's
        # page k becomes page k + shift
        idx._max_page = min(d.pages) - 1 + shift
        idx.index(doc=d, page_nrs={p - shift for p in pages} & set(d.pages))
    out = DoclingDocument(name=name)
    out._update_from_index(idx)
    return out


def release_gpu_and_ram():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    release_memory()


# --------------------------------------------------------------------------
# geometry and words

def tl_box(bbox, page_height) -> tuple[float, float, float, float]:
    b = bbox.to_top_left_origin(page_height=page_height)
    return (min(b.l, b.r), min(b.t, b.b), max(b.l, b.r), max(b.t, b.b))


class Grid:
    """Words and boxes bucketed on a square grid, so that "which words are in
    this box" and "is this word in any of these boxes" look only at nearby
    candidates. A page holding one table of 12,000 cells and 1,900 words took
    an hour with plain loops."""
    CELL = 24.0

    def __init__(self):
        self.buckets = collections.defaultdict(list)

    def _span(self, box, pad):
        c = self.CELL
        return (range(int((box[0] - pad) // c), int((box[2] + pad) // c) + 1),
                range(int((box[1] - pad) // c), int((box[3] + pad) // c) + 1))

    @classmethod
    def of_words(cls, words):
        g = cls()
        for w in words:
            g.buckets[(int(w["cx"] // cls.CELL), int(w["cy"] // cls.CELL))].append(w)
        return g

    def words_in(self, box, pad):
        xs, ys = self._span(box, pad)
        return [w for ix in xs for iy in ys for w in self.buckets.get((ix, iy), ())
                if contains(box, w["cx"], w["cy"], pad)]

    @classmethod
    def of_boxes(cls, boxes, pad):
        g = cls()
        for b in boxes:
            xs, ys = g._span(b, pad)
            for ix in xs:
                for iy in ys:
                    g.buckets[(ix, iy)].append(b)
        return g

    def any_contains(self, x, y, pad):
        return any(contains(b, x, y, pad) for b in self.buckets.get((int(x // self.CELL), int(y // self.CELL)), ()))

    @classmethod
    def of_items(cls, pairs, pad):
        """(box, ref) pairs, to ask which item a point belongs to."""
        g = cls()
        for b, ref in pairs:
            xs, ys = g._span(b, pad)
            for ix in xs:
                for iy in ys:
                    g.buckets[(ix, iy)].append((b, ref))
        return g

    def owners(self, x, y, pad):
        return [ref for b, ref in self.buckets.get((int(x // self.CELL), int(y // self.CELL)), ())
                if contains(b, x, y, pad)]

    def owner(self, x, y, pad):
        for b, ref in self.buckets.get((int(x // self.CELL), int(y // self.CELL)), ()):
            if contains(b, x, y, pad):
                return ref
        return None


def contains(box, x, y, pad):
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "‐": "-", "‑": "-", "–": "-", "—": "-"})


def norm_token(t: str) -> str:
    t = unicodedata.normalize("NFKC", t).translate(QUOTES).strip()
    return t.strip(".,;:()[]{}\"'«»")


def tokens(words: list[str]) -> list[str]:
    """Normalised tokens for comparing two readings: bullets and dashes on
    their own dropped, and a word broken across lines ("C34599-" "33") joined."""
    out = []
    for w in (norm_token(x) for x in words):
        if not w or not any(ch.isalnum() for ch in w):
            continue
        if out and out[-1].endswith("-") and len(out[-1]) > 1:
            out[-1] = out[-1][:-1] + w
        else:
            out.append(w)
    return [t.rstrip("-") or t for t in out]


def page_words(page) -> list[dict]:
    """MuPDF's words, in displayed-page coordinates (rotation applied)."""
    import pymupdf
    m = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, w, block, line, wno in page.get_text("words"):
        rr = pymupdf.Rect(x0, y0, x1, y1) * m
        out.append({"text": w, "box": (rr.x0, rr.y0, rr.x1, rr.y1),
                    "cx": (rr.x0 + rr.x1) / 2, "cy": (rr.y0 + rr.y1) / 2,
                    "block": block, "line": line, "wno": wno, "seq": len(out)})
    return out


def covered_words(page, words: list[dict], dpi: int = 100) -> set[int]:
    """Indexes of words that cannot be seen: under a dark opaque fill, with the middle of
    their box rendering flat and dark. A redaction drawn over text left in the file
    (4710294: invoice descriptions blacked out, 13,159 words still in its text layer).
    The render decides, not drawing order: 4672628's white text on black bands is drawn
    over its fill, and 4656888's cell shading made drawing order read as 82,469 covered."""
    import numpy as np
    import pymupdf
    m = page.rotation_matrix
    fills = []
    for dr in page.get_drawings():
        f = dr.get("fill")
        if f is None or (dr.get("fill_opacity") or 1) < 0.99:
            continue
        f = tuple(f) + (0, 0, 0)
        if 0.299 * f[0] + 0.587 * f[1] + 0.114 * f[2] > 0.25:
            continue
        rr = pymupdf.Rect(dr["rect"]) * m
        if rr.width >= 2 and rr.height >= 2:
            fills.append((rr.x0, rr.y0, rr.x1, rr.y1))
    if not fills:
        return set()
    g = Grid.of_boxes(fills, 0.0)
    cand = [i for i, w in enumerate(words) if g.any_contains(w["cx"], w["cy"], 0.0)]
    if not cand:
        return set()
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    k = dpi / 72.0
    out = set()
    for i in cand:
        x0, y0, x1, y1 = words[i]["box"]
        dx, dy = (x1 - x0) / 4, (y1 - y0) / 4          # the middle half: redaction boxes have white borders
        cut = a[max(int((y0 + dy) * k), 0):int((y1 - dy) * k) + 1, max(int((x0 + dx) * k), 0):int((x1 - dx) * k) + 1]
        if cut.size and cut.mean() < 80 and cut.std() < 12:
            out.add(i)
    return out


def text_in(g: "Grid", box, pad) -> list[str]:
    """MuPDF's words whose centres are inside a box, in its own order, as tokens.

    The margin shrinks with the box: a fixed 1.5 pt takes in the next line of
    1.5 pt text (61,882 false disagreements on one spreadsheet printout).
    Whole words, not characters: a cell box narrower than its printed word
    would otherwise yield "ovide" for "Provide:"."""
    pad = min(pad, SETTINGS["agreement_pad_frac"] * min(box[2] - box[0], box[3] - box[1]))
    return tokens([w["text"] for w in sorted(g.words_in(box, pad), key=reading_order)])


def export_markdown(doc) -> str:
    """docling's Markdown export, with everything: text under pictures, every content
    layer, and the footnotes of tables and pictures. docling-core 2.99 skips a footnote
    in the body because "the floating item holding them" will write it, and the Markdown
    table and picture serializers then never do: a CER reporting form (4662229) lost 40%
    of its words that way."""
    from docling_core.transforms.serializer.markdown import (MarkdownDocSerializer, MarkdownParams,
                                                             MarkdownPictureSerializer,
                                                             MarkdownTableSerializer)
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.types.doc.document import DOCUMENT_TOKENS_EXPORT_LABELS, ContentLayer

    def with_footnotes(base):
        class Serializer(base):
            def serialize(self, *, item, doc_serializer, doc, **kwargs):
                res = super().serialize(item=item, doc_serializer=doc_serializer, doc=doc, **kwargs)
                if kwargs.get("_nested_in_table") or not item.footnotes:
                    return res
                ftn = doc_serializer.serialize_footnotes(item=item, **kwargs)
                if not ftn.text:
                    return res
                return create_ser_result(text="\n\n".join(t for t in (res.text, ftn.text) if t),
                                         span_source=[res, ftn])
        return Serializer()

    return MarkdownDocSerializer(
        doc=doc,
        table_serializer=with_footnotes(MarkdownTableSerializer),
        picture_serializer=with_footnotes(MarkdownPictureSerializer),
        params=MarkdownParams(labels=DOCUMENT_TOKENS_EXPORT_LABELS, layers=set(ContentLayer),
                              traverse_pictures=True),
    ).serialize().text


def md_words(md: str) -> list[str]:
    """The words of a Markdown export: escapes, tags, entities and table rules removed."""
    import html
    md = html.unescape(re.sub(r"<!--.*?-->|</?[A-Za-z][^<>]*>", " ", md))
    md = re.sub(r"\\(.)", r"\1", md)
    md = re.sub(r"[*_`#|]+", " ", md)
    return md.split()


def reading_order(w: dict):
    """MuPDF's own order, the order the words came in."""
    return w["seq"]


def cluster(words: list[dict], gap: float) -> list[list[dict]]:
    """Group words into runs on the same line that are close together."""
    groups: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["cy"] / 4), w["box"][0])):
        g = groups[-1] if groups else None
        if g:
            last = g[-1]
            same_line = abs(last["cy"] - w["cy"]) < max(3.0, (w["box"][3] - w["box"][1]) * 0.6)
            if same_line and w["box"][0] - last["box"][2] < gap:
                g.append(w)
                continue
            # the next line, directly below and overlapping horizontally: same paragraph
            gb = union([x["box"] for x in g])
            if 0 <= w["box"][1] - gb[3] < gap / 2 and w["box"][0] < gb[2] and w["box"][2] > gb[0]:
                g.append(w)
                continue
        groups.append([w])
    return groups


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


KEEP = set("<>=%.,-/+$")


def table_geometry(tb, ph: float):
    """A table's box and its cells as (box, text), top-left origin, in points."""
    from docling_core.types.doc import CoordOrigin

    def tl(bb):
        bb = bb if bb.coord_origin == CoordOrigin.TOPLEFT else bb.to_top_left_origin(page_height=ph)
        return (bb.l, bb.t, bb.r, bb.b)
    box = tl(tb.prov[0].bbox) if tb.prov else None
    return box, [(tl(c.bbox), c.text) for c in tb.data.table_cells if c.bbox is not None]


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union_ = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union_ if union_ > 0 else 0.0


def table_structure_score(words: list[dict], boxes: list[tuple], pad: float) -> dict:
    """How well a grid's cell boxes fit the words, whatever the cells' text says:
    words crossing a cell border (straddles), in no cell, sharing a cell with a word
    far to their side on the same line (two columns in one cell: merged), and next to
    a word at word spacing that went to another cell (split), and in rows run together
    (rows_merged, see merged_row_cells). A grid whose text comes from the text layer
    places every word by construction, so the word score cannot judge it; this can.
    Rows of text run together still look like wrapped text, which is why a line grid
    must also be about as fine as TableFormer's."""
    words = [w for w in words if any(ch.isalnum() for ch in w["text"])]   # not dot leaders, as tokens()
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][2] - boxes[i][0]) * (boxes[i][3] - boxes[i][1]))
    g = Grid.of_items([(boxes[i], i) for i in order], pad)
    home = {}
    n = collections.Counter()
    by_cell = collections.defaultdict(list)
    for w in words:
        ci = g.owner(w["cx"], w["cy"], pad)
        home[id(w)] = ci
        if ci is None:
            n["no_cell"] += 1
            continue
        by_cell[ci].append(w)
        x0, y0, x1, y1 = w["box"]
        c = boxes[ci]
        if (max(0, c[0] - x0) + max(0, x1 - c[2]) > (x1 - x0) / 3
                or max(0, c[1] - y0) + max(0, y1 - c[3]) > (y1 - y0) / 3):
            n["straddles"] += 1
    for ws in by_cell.values():
        ws = sorted(ws, key=lambda w: (round(w["cy"]), w["box"][0]))
        for a, b in zip(ws, ws[1:]):
            h = a["box"][3] - a["box"][1]
            if abs(a["cy"] - b["cy"]) < h / 2 and b["box"][0] - a["box"][2] > max(6.0, 1.5 * h):
                n["merged"] += len(ws)
                break
    ws = sorted(words, key=lambda w: (w["block"], w["line"], w["box"][0]))
    for a, b in zip(ws, ws[1:]):
        if (a["block"], a["line"]) != (b["block"], b["line"]):
            continue
        h = a["box"][3] - a["box"][1]
        if (b["box"][0] - a["box"][2] <= max(6.0, 1.5 * h) and home[id(a)] is not None
                and home[id(b)] is not None and home[id(a)] != home[id(b)]):
            n["split"] += 1
    n["rows_merged"] = sum(len(by_cell[ci]) for ci in merged_row_cells({ci: (boxes[ci], ws) for ci, ws in by_cell.items()}))
    out = {k: n[k] for k in ("straddles", "no_cell", "merged", "split", "rows_merged")}
    out["bad"] = sum(out.values())
    return out


def text_lines(words: list[dict]) -> list[dict]:
    """A cell's words as lines, top to bottom: centre, height and text of each."""
    lines = []
    for w in sorted(words, key=lambda w: (w["cy"], w["box"][0])):
        h = w["box"][3] - w["box"][1]
        if lines and abs(lines[-1]["cy"] - w["cy"]) < 0.5 * max(h, lines[-1]["h"]):
            ln = lines[-1]
            ln["words"].append(w)
            ln["cy"] = sum(x["cy"] for x in ln["words"]) / len(ln["words"])
            ln["h"] = max(ln["h"], h)
        else:
            lines.append({"cy": w["cy"], "h": h, "words": [w]})
    return lines


NUMBER = re.compile(r"^[(\-+−]?[$€£]?\d[\d,.:/\-]*%?\)?$")


def is_value(line: dict) -> bool:
    """A line that reads as one value: a number, date or amount, with at most a short
    unit beside it ("12.5 m3", "2024-01-01", "(1,250)"). A code with digits in it is
    not one: "MG-30-F5-1003/" wraps like any word (4664902's ruled equipment lists)."""
    ws = [w["text"] for w in line["words"]]
    nums = [t for t in ws if NUMBER.match(t)]
    return bool(nums) and len(ws) <= 3 and all(len(t) <= 4 for t in ws if t not in nums)


def merged_row_cells(cells: dict) -> set:
    """Cells holding rows run together: {key: (box, words)} -> the keys.

    Two cells side by side, each with several lines, and at least two of those lines
    at the same heights in both: the page has a row per line there, the grid one.
    Wrapped text looks the same when two neighbours both wrap, so one of the two must
    be a stack of values (every line a number, a date or a code), which wrapped prose
    is not. A header broken over two lines next to another is not a stack of values."""
    stacked = []
    for k, (box, ws) in cells.items():
        lines = text_lines(ws)
        if len(lines) >= 2:
            stacked.append((box, k, lines, all(is_value(ln) for ln in lines)))
    stacked.sort(key=lambda s: s[0][1])
    out = set()
    for i, (a, ka, la, va) in enumerate(stacked):
        for b, kb, lb, vb in stacked[i + 1:]:
            if b[1] >= a[3]:
                break
            if not (va or vb) or min(a[2], b[2]) - max(a[0], b[0]) > 0:
                continue                   # neither a stack of values, or not side by side
            if min(a[3], b[3]) - max(a[1], b[1]) < 0.5 * min(a[3] - a[1], b[3] - b[1]):
                continue                   # not in the same row
            shared = sum(1 for x in la if any(abs(x["cy"] - y["cy"]) < 0.5 * min(x["h"], y["h"]) for y in lb))
            if shared >= 2:
                out.update((ka, kb))
    return out


def line_grid(page, box, words: list[dict], pad: float):
    """The table's grid from its ruling lines (PyMuPDF), cell text from the text-layer
    words inside each cell. None unless exactly one ruled table fills the box."""
    import pymupdf
    try:
        found = page.find_tables(clip=pymupdf.Rect(box), vertical_strategy="lines", horizontal_strategy="lines")
    except Exception:                  # PyMuPDF raises SystemError on some layouts
        return None
    if len(found.tables) != 1:
        return None
    cells = [tuple(c) for c in found.tables[0].cells if c]
    if len(cells) < 2:
        return None
    xs = sorted({round(v, 1) for c in cells for v in (c[0], c[2])})
    ys = sorted({round(v, 1) for c in cells for v in (c[1], c[3])})
    g = Grid.of_words(words)
    out = []
    for c in cells:
        inside = sorted(g.words_in(c, pad), key=reading_order)
        out.append({"box": c, "text": " ".join(w["text"] for w in inside),
                    "rows": (ys.index(round(c[1], 1)), ys.index(round(c[3], 1))),
                    "cols": (xs.index(round(c[0], 1)), xs.index(round(c[2], 1)))})
    return {"cells": out, "num_rows": len(ys) - 1, "num_cols": len(xs) - 1}


def align_grid(page, box, words: list[dict]):
    """A grid for tables ruled only between blocks (4672628's lab results): columns
    from text alignment (PyMuPDF, vertical "text" strategy), rows at every gap between
    text lines that runs the table's width, the outer lines stretched to the table's
    box, and cells joined across a border where a word crosses it or where two words of
    one line sit at word spacing (a label running over several columns). Cell text is
    the text-layer words inside. It splits a header that wraps onto a second line into
    two rows, which fit does not see, so it is kept only on a clear win."""
    import bisect
    import pymupdf
    try:
        ts = page.find_tables(clip=pymupdf.Rect(box), vertical_strategy="text",
                              horizontal_strategy="lines").tables
    except Exception:                  # PyMuPDF raises SystemError on some layouts
        return None
    if len(ts) != 1:
        return None
    xs = sorted({round(v, 1) for c in ts[0].cells if c for v in (c[0], c[2])})
    ys = sorted({round(v, 1) for c in ts[0].cells if c for v in (c[1], c[3])})
    words = [w for w in words if any(ch.isalnum() for ch in w["text"])]
    if len(xs) < 2 or len(ys) < 2 or not words:
        return None
    xs[0] = min(xs[0], box[0], min(w["box"][0] for w in words) - 0.5)
    xs[-1] = max(xs[-1], box[2], max(w["box"][2] for w in words) + 0.5)
    ys[0] = min(ys[0], box[1], min(w["box"][1] for w in words) - 0.5)
    ys[-1] = max(ys[-1], box[3], max(w["box"][3] for w in words) + 0.5)
    gaps, hi = [], None
    for lo, h in sorted((w["box"][1], w["box"][3]) for w in words):
        if hi is not None and lo > hi + 0.5:
            gaps.append(round((hi + lo) / 2, 1))
        hi = h if hi is None else max(hi, h)
    ys = sorted(set(ys) | {g for g in gaps if ys[0] < g < ys[-1]})
    nr, nc = len(ys) - 1, len(xs) - 1
    parent = {(i, j): (i, j) for i in range(nr) for j in range(nc)}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def row_of(y):
        return bisect.bisect_right(ys, y) - 1

    def col_of(x):
        return min(max(bisect.bisect_right(xs, x) - 1, 0), nc - 1)

    def join(i, j0, j1):
        for j in range(min(j0, j1), max(j0, j1)):
            parent[find((i, j + 1))] = find((i, min(j0, j1)))
    for w in words:                    # a word crossing a column border
        i = row_of(w["cy"])
        if 0 <= i < nr:
            join(i, col_of(w["box"][0] + 0.5), col_of(w["box"][2] - 0.5))
    ws = sorted(words, key=lambda w: (w["block"], w["line"], w["box"][0]))
    for a, b in zip(ws, ws[1:]):       # two words of one phrase
        if (a["block"], a["line"]) != (b["block"], b["line"]):
            continue
        if b["box"][0] - a["box"][2] > max(6.0, 1.5 * (a["box"][3] - a["box"][1])):
            continue
        i = row_of(a["cy"])
        if 0 <= i < nr and row_of(b["cy"]) == i:
            join(i, col_of(a["cx"]), col_of(b["cx"]))
    groups = collections.defaultdict(list)
    for k in parent:
        groups[find(k)].append(k)
    g = Grid.of_words(words)
    cells = []
    for members in groups.values():
        i, js = members[0][0], [j for _, j in members]
        b = (xs[min(js)], ys[i], xs[max(js) + 1], ys[i + 1])
        inside = sorted(g.words_in(b, 0.0), key=reading_order)
        cells.append({"box": b, "text": " ".join(w["text"] for w in inside),
                      "rows": (i, i + 1), "cols": (min(js), max(js) + 1)})
    return {"cells": cells, "num_rows": nr, "num_cols": nc}


_tatr = None


def tatr_model():
    """Table Transformer v1.1 (PubTables-1M + FinTabNet), loaded once per worker.

    The published config has "dilation": null, which transformers 5 rejects; null has
    always meant False for this model. Its image-processor config is rejected too, so
    preprocessing is done as Microsoft's own inference code does it: longest side
    1000 px, ImageNet normalisation."""
    global _tatr
    if _tatr is None:
        import torch
        from transformers import TableTransformerConfig, TableTransformerForObjectDetection
        cfg = json.loads((TATR_DIR / "config.json").read_text())
        cfg["dilation"] = bool(cfg.get("dilation"))
        model = TableTransformerForObjectDetection.from_pretrained(TATR_DIR, config=TableTransformerConfig(**cfg))
        _tatr = model.to(SETTINGS["device"] if torch.cuda.is_available() else "cpu").eval()
    return _tatr


def tatr_detect(img) -> list[tuple[str, float, list]]:
    """(label, score, box in the image's pixels) for every detection above the threshold."""
    import torch
    import torchvision.transforms.functional as F
    model = tatr_model()
    s = 1000 / max(img.size)
    small = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))))
    x = F.normalize(F.to_tensor(small), [0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    with torch.no_grad():
        out = model(pixel_values=x.unsqueeze(0).to(model.device))
    prob = out.logits.softmax(-1)[0, :, :-1]
    scores, labels = prob.max(-1)
    cx, cy, w, h = out.pred_boxes[0].unbind(-1)
    boxes = torch.stack([(cx - w / 2) * img.width, (cy - h / 2) * img.height,
                         (cx + w / 2) * img.width, (cy + h / 2) * img.height], -1)
    keep = scores > SETTINGS["tatr"]["threshold"]
    return [(model.config.id2label[int(lb)], float(sc), bx.tolist())
            for lb, sc, bx in zip(labels[keep], scores[keep], boxes[keep])]


def bands(found: list[tuple[float, tuple]], axis: int) -> list[tuple]:
    """Rows (axis 1) or columns (axis 0), best first, dropping any that mostly overlaps
    one already kept: the model proposes near-duplicates (53 rows and 19 columns on a
    table of about 48 by 17) and keeps them all above its threshold."""
    kept = []
    for _, b in sorted(found, key=lambda sb: -sb[0]):
        lo, hi = b[axis], b[axis + 2]
        if all(max(0.0, min(hi, k[axis + 2]) - max(lo, k[axis])) < 0.5 * min(hi - lo, k[axis + 2] - k[axis])
               for k in kept):
            kept.append(b)
    return sorted(kept, key=lambda b: b[axis])


def edges(spans: list[tuple], axis: int, lo: float, hi: float) -> list[float]:
    """Borders of contiguous bands: halfway between neighbours, the outer two at lo and hi."""
    out = [lo]
    for a, b in zip(spans, spans[1:]):
        out.append((a[axis + 2] + b[axis]) / 2)
    return out + [hi]


def tatr_grid(page_img, box, words: list[dict]):
    """The table's grid from Table Transformer: rows and columns as contiguous bands,
    cells joined where it finds a spanning cell, text from the text-layer words inside.
    Like the line grid it only draws boxes, so it cannot invent text; unlike the line
    grid it needs no ruling lines (a borderless 13-column equipment table: 17 problem
    words to 0 in the legacy lab). `page_img` is (PIL image of the displayed page, scale)."""
    img, k = page_img
    pad = SETTINGS["tatr"]["pad_pt"]
    words = [w for w in words if any(ch.isalnum() for ch in w["text"])]
    if not words:
        return None
    crop = (max(0.0, box[0] - pad), max(0.0, box[1] - pad),
            min(img.width / k, box[2] + pad), min(img.height / k, box[3] + pad))
    found = tatr_detect(img.crop(tuple(round(v * k) for v in crop)).convert("RGB"))

    def to_pt(b):
        return (crop[0] + b[0] / k, crop[1] + b[1] / k, crop[0] + b[2] / k, crop[1] + b[3] / k)
    rows = bands([(sc, to_pt(b)) for lb, sc, b in found if lb == "table row"], 1)
    cols = bands([(sc, to_pt(b)) for lb, sc, b in found if lb == "table column"], 0)
    if not rows or not cols or len(rows) * len(cols) < 2:
        return None
    x0 = min(box[0], min(w["box"][0] for w in words) - 0.5)
    x1 = max(box[2], max(w["box"][2] for w in words) + 0.5)
    y0 = min(box[1], min(w["box"][1] for w in words) - 0.5)
    y1 = max(box[3], max(w["box"][3] for w in words) + 0.5)
    xs, ys = edges(cols, 0, x0, x1), edges(rows, 1, y0, y1)
    nr, nc = len(rows), len(cols)
    owner = {(i, j): (i, j) for i in range(nr) for j in range(nc)}
    spans = {}
    for _, _, sp in (x for x in found if x[0] == "table spanning cell"):
        sp = to_pt(sp)
        # the bands as detected: the outer ones are stretched to the table's edges
        ii = [i for i in range(nr) if sp[1] <= (rows[i][1] + rows[i][3]) / 2 <= sp[3]]
        jj = [j for j in range(nc) if sp[0] <= (cols[j][0] + cols[j][2]) / 2 <= sp[2]]
        if len(ii) * len(jj) < 2 or any(owner[(i, j)] != (i, j) for i in ii for j in jj):
            continue                   # covers one cell, or overlaps a span already taken
        if not span_needed(words, xs, ys, ii, jj):
            continue
        for i in ii:
            for j in jj:
                owner[(i, j)] = (ii[0], jj[0])
        spans[(ii[0], jj[0])] = (ii[-1] + 1, jj[-1] + 1)
    g = Grid.of_words(words)
    cells = []
    for (i, j), o in owner.items():
        if o != (i, j):
            continue
        i1, j1 = spans.get((i, j), (i + 1, j + 1))
        b = (xs[j], ys[i], xs[j1], ys[i1])
        inside = sorted(g.words_in(b, 0.0), key=reading_order)
        cells.append({"box": b, "text": " ".join(w["text"] for w in inside), "rows": (i, i1), "cols": (j, j1)})
    return {"cells": cells, "num_rows": nr, "num_cols": nc}


def span_needed(words: list[dict], xs: list[float], ys: list[float], ii: list[int], jj: list[int]) -> bool:
    """Whether the text needs a spanning cell over rows ii and columns jj. Each direction
    must be needed on its own: the rows it joins, by a word crossing a row border or by
    all its words being in one row; the columns likewise. The model proposes spans at
    low scores that join rows each holding text of their own (4646070's table of
    contents: four entries in one cell, because they cross the narrow number column)."""
    box = (xs[jj[0]], ys[ii[0]], xs[jj[-1] + 1], ys[ii[-1] + 1])
    inside = [w for w in words if contains(box, w["cx"], w["cy"], 0.0)]

    def needed(borders, lo, hi, centre):
        if any(w["box"][lo] + (w["box"][hi] - w["box"][lo]) / 3 < b < w["box"][hi] - (w["box"][hi] - w["box"][lo]) / 3
               for w in inside for b in borders):
            return True
        return len({sum(w[centre] >= b for b in borders) for w in inside}) <= 1
    return needed([ys[i] for i in ii[1:]], 1, 3, "cy") and needed([xs[j] for j in jj[1:]], 0, 2, "cx")


def page_image(page, scale: float):
    """The page as displayed (rotation applied), as a PIL image, and its pixels per point."""
    import pymupdf
    from PIL import Image
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), scale


def table_data_from_grid(grid: dict, ph: float):
    """A docling TableData from line_grid()'s cells."""
    from docling_core.types.doc import BoundingBox, CoordOrigin, TableCell, TableData
    cells = [TableCell(bbox=BoundingBox(l=c["box"][0], t=c["box"][1], r=c["box"][2], b=c["box"][3],
                                        coord_origin=CoordOrigin.TOPLEFT),
                       row_span=c["rows"][1] - c["rows"][0], col_span=c["cols"][1] - c["cols"][0],
                       start_row_offset_idx=c["rows"][0], end_row_offset_idx=c["rows"][1],
                       start_col_offset_idx=c["cols"][0], end_col_offset_idx=c["cols"][1],
                       text=c["text"], column_header=c["rows"][0] == 0)
             for c in grid["cells"]]
    return TableData(num_rows=grid["num_rows"], num_cols=grid["num_cols"], table_cells=cells)


def table_word_score(words: list[dict], cells: list[tuple], pad: float) -> dict:
    """Where a table's text-layer words ended up, counted in words, not issues.

    `cells` are (box, text) pairs. Each word goes to the smallest cell whose box holds
    it, and is then placed (its cell's text has it), in a wrong cell (only another cell
    has it), in no cell (no cell box holds it; it is kept beside the table) or lost (a
    cell box holds it, no cell's text has it). Counting issues per cell rewards a grid
    with fewer, merged cells; counting words does not."""
    order = sorted(range(len(cells)), key=lambda i: (cells[i][0][2] - cells[i][0][0]) * (cells[i][0][3] - cells[i][0][1]))
    g = Grid.of_items([(cells[i][0], i) for i in order], pad)   # smallest cell first
    left = [collections.Counter(tokens((t or "").split())) for _, t in cells]
    flat = [squash(tokens((t or "").split())) for _, t in cells]
    everywhere = collections.Counter()
    for c in left:
        everywhere.update(c)
    n = collections.Counter()
    unplaced, examples = [], collections.defaultdict(list)
    for w in words:
        toks = tokens([w["text"]])
        if not toks:
            continue
        ci = g.owner(w["cx"], w["cy"], pad)
        if ci is None:
            n["no_cell"] += 1
            continue
        if all(left[ci][t] > 0 for t in toks):
            for t in toks:
                left[ci][t] -= 1
                everywhere[t] -= 1
            n["placed"] += 1
        elif squash(toks) and squash(toks) in flat[ci]:
            n["placed"] += 1              # joined or split differently ("Credit1" for "Credit 1")
        else:
            unplaced.append(toks)
    for toks in unplaced:                 # after every cell has taken its own words
        if all(everywhere[t] > 0 for t in toks):
            for t in toks:
                everywhere[t] -= 1
            n["wrong_cell"] += 1
            kind = "wrong_cell"
        else:
            n["lost"] += 1
            kind = "lost"
        if len(examples[kind]) < 30:
            examples[kind].append(" ".join(toks))
    n["words"] = n["placed"] + n["wrong_cell"] + n["no_cell"] + n["lost"]
    return {"words": n["words"], "placed": n["placed"], "wrong_cell": n["wrong_cell"],
            "no_cell": n["no_cell"], "lost": n["lost"],
            "placed_share": r(n["placed"] / n["words"], 4) if n["words"] else None,
            "examples": dict(examples)}


def squash(toks: list[str]) -> str:
    """What two readings must agree on: letters, digits and the characters that
    change a value, with spacing ignored ("Credit 1" and "Credit1" agree)."""
    return "".join(ch for t in toks for ch in t if ch.isalnum() or ch in KEEP)


def compare(theirs: list[str], mine: list[str]) -> tuple[float, str, str, bool]:
    """Similarity of docling's and MuPDF's reading, what only each has, and
    whether a digit is among the differences."""
    a, b = squash(theirs), squash(mine)
    if a == b:
        return 1.0, "", "", False
    if len(a) + len(b) > 20000:   # too long for a character diff; compare as multisets
        ca, cb = collections.Counter(a), collections.Counter(b)
        ratio = 2 * sum((ca & cb).values()) / (len(a) + len(b))
        da, db = "".join((ca - cb).elements()), "".join((cb - ca).elements())
    else:
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        ratio = sm.ratio()
        da = " ".join(a[i1:i2] for op, i1, i2, _, _ in sm.get_opcodes() if op in ("replace", "delete"))
        db = " ".join(b[j1:j2] for op, _, _, j1, j2 in sm.get_opcodes() if op in ("replace", "insert"))
    return ratio, da, db, any(ch.isdigit() for ch in da + db)


# --------------------------------------------------------------------------
# one document

def process(path: Path, doc_id: str, meta: dict, ctx) -> dict:
    import pymupdf
    from docling_core.types.doc import (BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument,
                                        ListItem, PictureItem, ProvenanceItem, SectionHeaderItem, Size,
                                        TableItem, TextItem, TitleItem)
    from docling_core.types.doc.document import ContentLayer

    t0 = time.perf_counter()
    out_dir = Path(meta["out"]).parent
    rec = {k: meta[k] for k in ("stage", "signature", "session", "code_sha256", "settings")}
    rec.update({"doc_id": doc_id, "started_at": utcnow(), "worker_pid": os.getpid(),
                "tier0_run": Path(meta["tier0_dir"]).name})
    reasons: list[str] = []
    timings = {}
    st = path.stat()
    rec["file"] = {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(path)}

    def finish(status):
        rec["status"] = status
        rec["reasons"] = sorted(set(reasons))
        rec["step_ms"] = timings
        rec["finished_at"] = utcnow()
        rec["seconds"] = r(time.perf_counter() - t0, 3)
        return rec

    # ---- tier 0 record: the route, and a check that it describes these bytes
    t0_path = Path(meta["output"]) / doc_id / "convert" / Path(meta["tier0_dir"]).name / f"{doc_id}.tier0.json.gz"
    if not t0_path.exists():
        reasons.append("tier0_missing")
        return finish("failed")
    t0rec = read_json_gz(t0_path)
    rec["tier0"] = {"path": str(t0_path), "sha256": sha256_file(t0_path), "status": t0rec.get("status")}
    if (t0rec.get("file") or {}).get("sha256") != rec["file"]["sha256"]:
        reasons.append("tier0_describes_other_bytes")
        return finish("failed")
    t0pages = t0rec.get("pages") or []
    n_pages = len(t0pages)

    routes = {}
    for p in t0pages:
        h = p.get("hints") or {}
        routes[p["index"] + 1] = route_for(h.get("origin"), h.get("text_layer"))
    todo = [pno for pno, rt in routes.items() if rt in ROUTES["converted_now"]]
    reader_of = {pno: ROUTES["reader"][routes[pno]] for pno in todo}
    ocr_full_pages = {pno for pno in todo if reader_of[pno] == "ocr_full"}
    rec["routes"] = {"by_page": {str(k): v for k, v in routes.items()},
                     "counts": dict(collections.Counter(routes.values())), "rules": SETTINGS["route_rules"]}

    issues: list[dict] = []

    def issue(page, box, kind, severity, evidence, check, **extra):
        issues.append({"page": page, "bbox": [r(v, 1) for v in box] if box else None, "kind": kind,
                       "severity": severity, "evidence": evidence, "check": check, **extra})

    pdf = pymupdf.open(path)
    for pno, rt in routes.items():
        if rt not in ROUTES["converted_now"]:
            pr = pdf[pno - 1].rect
            issue(pno, (0, 0, pr.width, pr.height), "page_not_converted", "pending",
                  ROUTE_REASONS.get(rt, rt), "route 1", route=rt)

    # ---- docling, one routed page per call
    # Each call's status, warnings, errors, confidence and timing then belong
    # to exactly one page. Memory grows with the pages docling holds until it
    # assembles a document (three large filings converted whole at once took
    # the machine down); one page at a time bounds it, and it is handed back
    # after each call.
    cap = ctx["capture"]
    ranges, chunks = [], []
    src, rec["docling_source"] = docling_source(path, pdf, out_dir / "work", rec["file"]["sha256"])

    # Per page, as it happens: the page, when docling started and finished it,
    # and what the worker held. A worker stopped mid-document (memory, time)
    # leaves the page it was on here, for the log and for the next attempt.
    attempt = int(meta.get("attempt") or 1)
    progress_path = out_dir / f"{doc_id}.progress.{attempt}.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = open(progress_path, "w", buffering=1)

    def note(**kv):
        progress.write(json.dumps({"at": utcnow(), **kv, "rss_gb": r(rss_gb(os.getpid()), 2)}) + "\n")

    # Pages an earlier attempt started and never finished are not given to
    # docling again: they go to the fallback reader, with the reason.
    poison = {}
    for k in range(1, attempt):
        prev = out_dir / f"{doc_id}.progress.{k}.jsonl"
        if prev.exists():
            started, done_ = {}, set()
            for line in prev.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "page_start":
                    started[ev["page"]] = ev
                elif ev.get("event") == "page_done":
                    done_.add(ev["page"])
            for pno, ev in started.items():
                if pno not in done_:
                    poison[pno] = f"attempt {k} stopped while docling was converting this page " \
                                  f"(worker at {ev.get('rss_gb')} GB when it started)"
    rec["poison_pages"] = {str(k): v for k, v in poison.items()}

    # Pages too heavy for docling, known from tier 0 before any conversion: they
    # take their text from the text layer, and say so. Found by crashing instead,
    # one attempt finds one page; 4681376 was found that way.
    text_only = {}
    by_index = {p["index"] + 1: p for p in t0pages}
    for pno in todo:
        why = over_budget(by_index.get(pno) or {})
        if why:
            text_only[pno] = why
    rec["text_only_pages"] = {str(k): v for k, v in text_only.items()}
    todo_docling = [p for p in todo if p not in poison and p not in text_only]

    seen = {}

    def visible_words(pno):
        """The page's text-layer words that can be seen, and those under a redaction."""
        if pno not in seen:
            ws = page_words(pdf[pno - 1])
            cov = covered_words(pdf[pno - 1], ws)
            seen[pno] = ([w for i, w in enumerate(ws) if i not in cov], [ws[i] for i in sorted(cov)])
        return seen[pno]

    table_choices = []                 # one row per table: each mode's word score, and the mode kept
    table_mode_at = {}                 # (page, box rounded) -> mode kept, for the checks

    def choose_tables(d, pages, entry):
        """Read the pages again in each other table mode and give every table the grid
        that places the most text-layer words in their cells. The layout step is the same
        in every mode, so a table's twin is the one in the same place."""
        modes = ctx["table_modes"]
        pad = SETTINGS["box_pad_pt"]
        on = {p: [t for t in d.tables if t.prov and t.prov[0].page_no == p] for p in pages}
        if not any(on.values()):
            return
        grids = {p: Grid.of_words(visible_words(p)[0]) for p in pages if on[p]}
        page_imgs = {}                                             # page -> rendered for Table Transformer
        best = {}                                                  # id(table) -> (score, mode, data)
        for t in (t for p in pages for t in on[p]):
            ph = d.pages[t.prov[0].page_no].size.height
            box, cells = table_geometry(t, ph)
            best[id(t)] = [(table_word_score(grids[t.prov[0].page_no].words_in(box, pad), cells, pad), modes[0], None)]
        weak = [p for p in pages if any(best[id(t)][0][0]["placed_share"] is not None
                                        and best[id(t)][0][0]["placed_share"] < SETTINGS["table_choice_below"]
                                        for t in on[p])]
        for mode in (modes[1:] if weak else []):
            ts = time.perf_counter()
            try:
                res2 = ctx["table_converters"][mode].convert(str(src), page_range=(weak[0], weak[-1]),
                                                              raises_on_error=False)
                d2 = res2.document
            except Exception as e:
                entry.setdefault("table_choice_errors", []).append({"mode": mode, "message": repr(e)})
                continue
            entry.setdefault("table_choice_seconds", {})[mode] = r(time.perf_counter() - ts, 3)
            if d2 is None:
                continue
            for p in weak:
                if p not in d2.pages:
                    continue
                ph = d.pages[p].size.height
                theirs = [(t2, table_geometry(t2, ph)) for t2 in d2.tables if t2.prov and t2.prov[0].page_no == p]
                for t in on[p]:
                    box, _ = table_geometry(t, ph)
                    match = max(theirs, key=lambda x: iou(box, x[1][0]), default=None)
                    if match is None or iou(box, match[1][0]) < 0.8:
                        continue
                    t2, (_, cells2) = match
                    if any(getattr(c, "ref", None) for c in t2.data.table_cells):
                        continue               # a cell pointing into the other reading's document
                    best[id(t)].append((table_word_score(grids[p].words_in(box, pad), cells2, pad), mode, t2.data))
            del res2, d2
        for p in pages:
            for t in on[p]:
                tried = best[id(t)]
                # most words placed; then fewest lost; ties keep the first mode
                win = max(tried, key=lambda x: (x[0]["placed"], -x[0]["lost"], -tried.index(x)))
                if win[2] is not None:
                    t.data = win[2]
                ph = d.pages[p].size.height
                box, cells = table_geometry(t, ph)
                kept = win[1]
                row = {"page": p, "bbox": [r(v, 1) for v in box],
                       **{m: {k: sc[k] for k in ("words", "placed", "wrong_cell", "no_cell", "lost")}
                          for sc, m, _ in tried}}
                # Then the ruling lines: on a fully ruled table their grid is exact, where
                # TableFormer's runs columns together (4664902). Kept only if it is about as
                # fine as TableFormer's (a partly ruled table gives a few large cells) and
                # fits the words better.
                words = grids[p].words_in(box, pad)
                lg = line_grid(pdf[p - 1], box, words, pad) if words else None
                if lg:
                    tf_fit = table_structure_score(words, [b for b, _ in cells], pad)
                    lg_fit = table_structure_score(words, [c["box"] for c in lg["cells"]], pad)
                    fine = (lg["num_rows"] >= 0.8 * t.data.num_rows and lg["num_cols"] >= 0.8 * t.data.num_cols)
                    row["structure"] = {kept: tf_fit, "lines": lg_fit | {"rows": lg["num_rows"], "cols": lg["num_cols"],
                                                                         "fine_enough": fine}}
                    if fine and lg_fit["bad"] < tf_fit["bad"]:
                        t.data = table_data_from_grid(lg, ph)
                        kept = "lines"
                # Then text alignment, for tables ruled only between blocks, where neither
                # TableFormer nor the ruling lines find the value columns; and Table
                # Transformer, for borderless tables. Each is kept only if it places fewer
                # than half as many words badly as what is kept so far; the better of the two.
                if words and kept != "lines":
                    cur = table_structure_score(words, [b for b, _ in table_geometry(t, ph)[1]], pad)
                    row.setdefault("structure", {})[kept] = cur
                    ts = time.perf_counter()
                    try:
                        if p not in page_imgs:
                            page_imgs[p] = page_image(pdf[p - 1], SETTINGS["tatr"]["scale"])
                        tg = tatr_grid(page_imgs[p], box, words)
                    except Exception as e:
                        tg = None
                        entry.setdefault("table_choice_errors", []).append({"mode": "tatr", "message": repr(e)})
                    entry["tatr_seconds"] = r(entry.get("tatr_seconds", 0) + time.perf_counter() - ts, 3)
                    better = []
                    for name, g, below in (("aligned", align_grid(pdf[p - 1], box, words), SETTINGS["align_keep_below"]),
                                           ("tatr", tg, SETTINGS["tatr_keep_below"])):
                        if not g:
                            continue
                        fit = table_structure_score(words, [c["box"] for c in g["cells"]], pad)
                        row["structure"][name] = fit | {"rows": g["num_rows"], "cols": g["num_cols"]}
                        if fit["bad"] < below * cur["bad"]:
                            better.append((fit["bad"], name, g))
                    if better:
                        _, kept, g = min(better, key=lambda x: x[0])
                        t.data = table_data_from_grid(g, ph)
                table_mode_at[(p, tuple(r(v, 0) for v in box))] = kept
                row["kept"] = kept
                table_choices.append(row)

    def run_docling(a, b, pages, retry=False):
        """Convert pages a..b; keep the ones docling finished; return the ones it did not."""
        cap.records.clear()
        note(event="page_start", page=a, last=b, retry=retry)
        cs = time.perf_counter()
        entry = {"pages": [a, b], "routed": len(pages), "retry": retry}
        finished = set()
        shift = 0                      # page numbers in docling's result are the PDF's own
        try:
            reader = reader_of[a]
            entry["reader"] = reader
            res = ctx["readers"][reader].convert(str(src), page_range=(a, b), raises_on_error=False)
            entry["status"] = str(res.status.value if hasattr(res.status, "value") else res.status)
            entry["errors"] = [{"component": str(getattr(e, "component_type", "")), "module": getattr(e, "module_name", None),
                                "message": clip(getattr(e, "error_message", str(e)), 2000)} for e in res.errors or []]
            conf = getattr(res, "confidence", None)
            entry["confidence"] = {k + shift: json.loads(v.model_dump_json())
                                   for k, v in (conf.pages.items() if conf else []) if k + shift in pages}
            d = res.document
            if d is not None:
                finished = {p for p in pages if (p - shift) in d.pages
                            and d.pages[p - shift].size.width > 0 and d.pages[p - shift].size.height > 0}
                if finished:
                    if reader != "ocr_full":           # table choice scores against the text layer
                        try:
                            choose_tables(d, sorted(finished), entry)
                        except Exception as e:     # the first reading stands
                            entry["table_choice_errors"] = [{"message": repr(e),
                                                             "trace": traceback.format_exc(limit=5)}]
                    chunks.append((sorted(finished), shift, d))
            del res
        except Exception as e:
            entry["status"] = "exception"
            entry["errors"] = [{"message": repr(e), "trace": traceback.format_exc(limit=5)}]
        entry["warnings"] = list(cap.records)
        entry["unfinished"] = sorted(set(pages) - finished)
        entry["timed_out"] = any("exceeded timeout" in w["message"] for w in entry["warnings"])
        entry["seconds"] = r(time.perf_counter() - cs, 3)
        ranges.append(entry)
        release_gpu_and_ram()
        note(event="page_done", page=a, last=b, status=entry["status"], seconds=entry["seconds"],
             timed_out=entry["timed_out"])
        return entry["unfinished"]

    s = time.perf_counter()
    unfinished = []
    for a, b, pages in chunked(todo_docling, SETTINGS["chunk_pages"]):
        unfinished += run_docling(a, b, pages)
    # A page docling did not finish (its threaded pipeline can close early and
    # drop pages: "pipeline terminated early") gets one more try on its own,
    # unless it ran out of time: a second try would take as long again.
    timed_out = {p for e in ranges if e["timed_out"] for p in e["unfinished"]}
    still = [p for p in unfinished if p in timed_out or run_docling(p, p, [p], retry=True)]
    still += sorted(poison) + sorted(text_only)
    progress.close()
    if src != path:
        src.unlink(missing_ok=True)
        try:
            src.parent.rmdir()
        except OSError:
            pass
    for e in ranges:
        a, b = e["pages"]
        for w in e.get("warnings") or []:
            issue(a if a == b else None, None, "docling_warning", "warning",
                  f"{w['logger']}: {w['message']}", "docling log",
                  **({} if a == b else {"pages": [a, b]}))
    rec["docling_retries"] = {"pages": unfinished, "recovered": sorted(set(unfinished) - set(still)), "failed": still}
    for e in ranges:
        if e["status"] != "success" and not (e["unfinished"] == [] and e["status"] == "partial_success"):
            reasons.append(f"docling_{e['status']}" + ("_on_retry" if e["retry"] else ""))
    chunks.sort(key=lambda c: c[0][0])
    timings["docling"] = r((time.perf_counter() - s) * 1000, 1)
    rec["docling"] = {"ranges": ranges, "pipeline_options": ctx["pipeline_options"],
                      "table_modes": ctx["table_modes"]}
    rec["table_choices"] = table_choices
    doc = merge_chunks(chunks, doc_id) if chunks else DoclingDocument(name=doc_id)
    chunks.clear()
    doc.name = doc_id

    # Pages docling could not parse even on their own: the text layer is still
    # there (tier 0 read it), so MuPDF's text blocks go in, marked as such.
    fallback = {}
    for pno in still:
        page = pdf[pno - 1]
        m = page.rotation_matrix
        pr = page.rect
        doc.add_page(page_no=pno, size=Size(width=pr.width, height=pr.height))
        n = 0
        # A suspect text layer is why the page was sent to OCR: it is no fallback.
        blocks = [] if routes.get(pno) == "ocr_untrusted_text_layer" else page.get_text("blocks")
        vis, cov = visible_words(pno) if blocks else ([], [])
        g_vis = Grid.of_words(vis) if cov else None
        for x0, y0, x1, y1, text, _bno, btype in blocks:
            bb = pymupdf.Rect(x0, y0, x1, y1) * m
            if g_vis is not None:          # a redacted page: only the words that show
                text = " ".join(w["text"] for w in sorted(g_vis.words_in((bb.x0, bb.y0, bb.x1, bb.y1), 0.0),
                                                          key=reading_order))
            text = " ".join(text.split())
            if btype != 0 or not text:
                continue
            bl = BoundingBox(l=bb.x0, t=bb.y0, r=bb.x1, b=bb.y1, coord_origin=CoordOrigin.TOPLEFT
                             ).to_bottom_left_origin(page_height=pr.height)
            doc.add_text(label=DocItemLabel.TEXT, text=text,
                         prov=ProvenanceItem(page_no=pno, bbox=bl, charspan=(0, len(text))))
            n += 1
        fallback[pno] = n
        if pno in text_only:
            issue(pno, None, "structure_skipped", "warning",
                  f"{text_only[pno]}; docling not run; {n} text block(s) taken from the text layer, "
                  f"without layout or table structure", "route by tier 0")
            continue
        if pno in poison:
            why = poison[pno]
        elif pno in timed_out:
            why = f"docling took longer than {SETTINGS['page_timeout_s']} s on this page"
        else:
            said = next((e for e in ranges if e["retry"] and e["pages"] == [pno, pno]), {})
            why = "docling could not parse this page: " + (
                "; ".join(x.get("message") or "" for x in said.get("errors") or []) or said.get("status") or "no result")
        if pno in ocr_full_pages:
            issue(pno, None, "ocr_failed", "error" if not n else "warning",
                  f"{clip(why, 300)}; " + (f"{n} text block(s) taken from the page's own OCR layer, unchecked"
                                           if n else "no text from this page"), "docling status")
            continue
        issue(pno, None, "page_from_fallback_reader", "warning",
              f"{clip(why, 300)}; {n} text block(s) taken from MuPDF, without layout or table structure",
              "docling status")
    rec["docling_fallback_pages"] = fallback
    rec["docling_timed_out_pages"] = sorted(timed_out)
    if set(fallback) - set(text_only):
        reasons.append("fallback_reader_used")
    if text_only:
        reasons.append("structure_skipped")

    # Every page must be the page it says it is: same number, same size as the PDF.
    mismatched = []
    for pno, pg in doc.pages.items():
        if pno not in routes:
            mismatched.append((pno, "not a page of this PDF"))
            continue
        pr = pdf[pno - 1].rect
        if abs(pg.size.width - pr.width) > 1 or abs(pg.size.height - pr.height) > 1:
            mismatched.append((pno, f"docling {pg.size.width:.0f}x{pg.size.height:.0f}, PDF {pr.width:.0f}x{pr.height:.0f}"))
    missing = sorted(set(todo) - set(doc.pages))

    def range_of(pno):
        return next((e for e in ranges if e["pages"][0] <= pno <= e["pages"][1]), {})

    def docling_said(pno):
        e = range_of(pno)
        return (f"docling pages {e.get('pages')}: {e.get('status')}"
                + (": " + "; ".join(x.get("message") or "" for x in e.get("errors") or [])[:400] if e.get("errors") else ""))

    unfinished = [pno for pno, why in mismatched if why.startswith("docling 0x0")]
    for pno in unfinished + missing:
        # A page docling did not finish: the only pages that carry the converter's error.
        issue(pno, None, "converter_error", "error", f"page not converted: {docling_said(pno)}", "docling status")
    for pno, why in mismatched:
        if pno not in unfinished:
            issue(pno if pno in routes else None, None, "page_identity_mismatch", "error", why, "identity 1")
    if [m for m in mismatched if m[0] not in unfinished]:
        reasons.append("page_identity_mismatch")
    if unfinished or missing:
        reasons.append("pages_not_converted")
    for pno in unfinished:
        doc.pages.pop(pno, None)

    # ---- checks against MuPDF, page by page
    s = time.perf_counter()
    pad, thr, minc = SETTINGS["box_pad_pt"], SETTINGS["agreement_threshold"], SETTINGS["agreement_min_chars"]
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    items_rows = []
    by_page = collections.defaultdict(list)
    for item, _lvl in doc.iterate_items(included_content_layers=layers, traverse_pictures=True):
        provs = getattr(item, "prov", None) or []
        outside = sorted({pv.page_no for pv in provs if pv.page_no not in doc.pages})
        if outside:
            # Part of this item lies on a page this pass does not convert (a
            # paragraph running on from a suspect or scanned page), so part of
            # its text came from a text layer that is not trusted.
            inside = sorted({pv.page_no for pv in provs if pv.page_no in doc.pages})
            issue(inside[0] if inside else None, None, "item_spans_unconverted_page", "warning",
                  f"{item.self_ref} also has text on page(s) {outside}, which this pass does not convert; "
                  f"that part of its text is from an untrusted text layer", "identity 1", ref=item.self_ref)
        for pv in provs:
            if pv.page_no not in doc.pages:
                continue
            ph = doc.pages[pv.page_no].size.height
            # An item can have a box on each page it runs across (a paragraph
            # continued overleaf); each box's charspan says which text is there.
            text = getattr(item, "text", None)
            if text is not None and len(provs) > 1 and pv.charspan and pv.charspan[1] > pv.charspan[0]:
                text = text[pv.charspan[0]:pv.charspan[1]]
            by_page[pv.page_no].append((item, tl_box(pv.bbox, ph), text))

    added_picture_text = 0
    added_text = added_in_table = 0
    table_words = collections.Counter()
    item_words = collections.defaultdict(list)     # ref -> [(page, box, words it owns)]
    item_objs, row_of = {}, collections.defaultdict(list)
    ocr_stats = collections.Counter()
    redacted_refs, redacted_words = set(), 0
    evidence_rows = []
    page_checks = {}

    def add_text_item(pno, group, ph, reader):
        """Words from the text layer as a text item of their own, with their box."""
        gb = union([w["box"] for w in group])
        txt = " ".join(w["text"] for w in sorted(group, key=reading_order))
        bl = BoundingBox(l=gb[0], t=gb[1], r=gb[2], b=gb[3], coord_origin=CoordOrigin.TOPLEFT
                         ).to_bottom_left_origin(page_height=ph)
        ti = doc.add_text(label=DocItemLabel.TEXT, text=txt,
                          prov=ProvenanceItem(page_no=pno, bbox=bl, charspan=(0, len(txt))))
        items_rows.append({"ref": ti.self_ref, "label": "text", "page": pno, "bbox": [r(v, 1) for v in gb],
                           "reader": reader, "grade": "exact", "chars": len(txt),
                           "text_sha256": sha256_bytes(txt.encode())})
        for w in group:
            w["claimed"] = ti.self_ref
        return ti, gb, txt
    for pno in todo:
        page = pdf[pno - 1]
        # An OCR'd page: its text layer (someone else's OCR, a suspect layer, or none) is
        # not the text. The text-layer checks see no words; the OCR is voted on below.
        ocr_page = pno in ocr_full_pages
        layer_words = page_words(page)
        vis_words, cov_words = ([], []) if ocr_page else visible_words(pno)
        words = [] if ocr_page else vis_words
        entries = by_page.get(pno, [])
        redacted_here = set()
        if cov_words:
            # Text under a redaction is not the document's text: docling read it from the
            # file, so its items and cells there are rebuilt from the words that show, and
            # nothing of it goes into issues, evidence or `orig`.
            g_cov, g_vis = Grid.of_words(cov_words), Grid.of_words(vis_words)
            for it, bx, _ in entries:
                if not g_cov.words_in(bx, pad):
                    continue
                if isinstance(it, TableItem):
                    ph_ = doc.pages[pno].size.height
                    for c in it.data.table_cells:
                        if c.bbox is not None:
                            cb = c.bbox if c.bbox.coord_origin == CoordOrigin.TOPLEFT else c.bbox.to_top_left_origin(page_height=ph_)
                            c.text = " ".join(w["text"] for w in sorted(g_vis.words_in((cb.l, cb.t, cb.r, cb.b), 0.0),
                                                                        key=reading_order))
                elif isinstance(it, TextItem):
                    redacted_here.add(it.self_ref)
                    redacted_refs.add(it.self_ref)
            for g in cluster(cov_words, SETTINGS["cluster_gap_pt"]):
                issue(pno, union([w["box"] for w in g]), "text_under_redaction", "info",
                      f"{len(g)} word(s) in the text layer under a redaction box: left out", "redaction 1",
                      words=len(g))
            redacted_words += len(cov_words)
        text_boxes = [(it, bx, tx) for it, bx, tx in entries if isinstance(it, TextItem)]
        tables = [(it, bx) for it, bx, _ in entries if isinstance(it, TableItem)]
        pictures = [(it, bx) for it, bx, _ in entries if isinstance(it, PictureItem)]
        ph = doc.pages[pno].size.height if pno in doc.pages else page.rect.height

        in_text, in_table, in_picture, uncovered = [], [], [], []
        g_text = Grid.of_boxes([bx for _, bx, _ in text_boxes], pad)
        g_table = Grid.of_boxes([bx for _, bx in tables], pad)
        g_pic = Grid.of_boxes([bx for _, bx in pictures], pad)
        for w in words:
            if g_text.any_contains(w["cx"], w["cy"], pad):
                in_text.append(w)
            elif g_table.any_contains(w["cx"], w["cy"], pad):
                in_table.append(w)
            elif g_pic.any_contains(w["cx"], w["cy"], pad):
                in_picture.append(w)
            else:
                uncovered.append(w)
        w_text, w_table, w_pic = Grid.of_words(in_text), Grid.of_words(in_table), Grid.of_words(in_picture)

        if words and not entries:
            issue(pno, None, "page_empty", "warning",
                  f"{len(words)} words in the text layer, no docling items; all kept from the text layer", "coverage 2")
        # Step 2: a word no docling item claims is not lost; it becomes a text item of its own.
        for g in cluster(uncovered, SETTINGS["cluster_gap_pt"]):
            _, gb, txt = add_text_item(pno, g, ph, "text layer: words claimed by no docling item")
            added_text += 1
            issue(pno, gb, "text_added_from_text_layer", "warning",
                  f"{len(g)} word(s) under no docling item, kept as a text item from the text layer: {clip(txt, 300)}",
                  "coverage 2", words=len(g))

        # words inside a table but in no cell
        cell_boxes = []
        for tb, tbx in tables:
            for c in tb.data.table_cells:
                if c.bbox is not None:
                    cb = c.bbox if c.bbox.coord_origin == CoordOrigin.TOPLEFT else c.bbox.to_top_left_origin(page_height=ph)
                    cell_boxes.append((tb, c, (cb.l, cb.t, cb.r, cb.b)))
        g_cells = Grid.of_boxes([bx for _, _, bx in cell_boxes], pad)
        not_in_cell = [w for w in in_table if not g_cells.any_contains(w["cx"], w["cy"], pad)]
        for g in cluster(not_in_cell, SETTINGS["cluster_gap_pt"]):
            # The grid is wrong (an error), but the text is kept, beside the table.
            _, gb, txt = add_text_item(pno, g, ph, "text layer: words in a table but in no cell")
            added_in_table += 1
            issue(pno, gb, "text_not_in_table_cell", "error",
                  f"{len(g)} word(s) inside a table but in no cell (kept as text beside it): {clip(txt, 300)}",
                  "coverage 2", words=len(g))

        # Step 2, the rest: an item's text is the text-layer words it holds. A word goes to
        # one item only. Where boxes overlap, it goes to the item whose docling reading has
        # it (docling assigned it there), else to the smallest box: taking the smallest box
        # alone moved words out of the item docling gave them to, and the emptied item kept
        # docling's text, so the words appeared twice (4656888: 20,592 repeated words).
        by_area = sorted(range(len(text_boxes)),
                         key=lambda i: (text_boxes[i][1][2] - text_boxes[i][1][0]) * (text_boxes[i][1][3] - text_boxes[i][1][1]))
        g_own = Grid.of_items([(text_boxes[i][1], i) for i in by_area], pad)
        theirs_left = [collections.Counter(tokens((tx or "").split())) for _, _, tx in text_boxes]
        owned = collections.defaultdict(list)
        for w in sorted(in_text, key=reading_order):
            cands = g_own.owners(w["cx"], w["cy"], pad)          # smallest first
            if not cands:
                continue
            i = cands[0]
            if len(cands) > 1:
                tok = tokens([w["text"]])
                for c in cands:
                    if tok and all(theirs_left[c][t] > 0 for t in tok):
                        i = c
                        break
            for t in tokens([w["text"]]):
                if theirs_left[i][t] > 0:
                    theirs_left[i][t] -= 1
            owned[i].append(w)
        for i, (it, bx, tx) in enumerate(text_boxes):
            if ocr_page:
                continue                   # OCR text stays OCR text
            item_words[it.self_ref].append((pno, bx, sorted(owned.get(i, []), key=reading_order)))
            item_objs[it.self_ref] = it

        # per item: docling's text against MuPDF's words in the same box
        disputed = 0
        if ocr_page:
            # The page's own hidden OCR layer, where it has one, is a second reader: an
            # independent OCR of the same scan. A suspect layer is not (it is why we OCR).
            vote = routes[pno] == "ocr_with_hidden_vote"
            g_hidden = Grid.of_words(layer_words) if vote else None
            for it, bx, tx in text_boxes:
                row = {"ref": it.self_ref, "label": str(it.label.value), "page": pno,
                       "bbox": [r(v, 1) for v in bx], "reader": "layout: docling; text: RapidOCR (full page)",
                       "grade": "ocr", "chars": len(tx or ""), "text_sha256": sha256_bytes((tx or "").encode())}
                hidden = text_in(g_hidden, bx, pad) if vote else []
                if hidden:                 # nothing there in their layer is no vote, not a disagreement
                    theirs = tokens((tx or "").split())
                    # case is not what two OCRs are voting on
                    ag, d_only, m_only, numeric = compare([t.lower() for t in theirs], [t.lower() for t in hidden])
                    row["hidden_layer_agreement"] = r(ag, 3)
                    if max(len(squash(hidden)), len(squash(theirs))) >= minc and ag < SETTINGS["ocr"]["hidden_vote_threshold"]:
                        disputed += 1
                        issue(pno, bx, "ocr_disagrees_with_hidden_layer", "error" if numeric else "warning",
                              f"similarity {ag:.2f}; only our OCR: {clip(d_only, 200)!r}; only the page's OCR layer: "
                              f"{clip(m_only, 200)!r}; ours: {clip(tx, 200)!r}", "ocr vote 1", ref=it.self_ref,
                              numeric=numeric)
                items_rows.append(row)
                ocr_stats["items"] += 1
                ocr_stats["voted"] += "hidden_layer_agreement" in row
                if "hidden_layer_agreement" in row:
                    ocr_stats["agreement_sum"] += row["hidden_layer_agreement"]
        for it, bx, tx in ([] if ocr_page else text_boxes):
            mine = text_in(w_text, bx, pad)
            n_words = len(w_text.words_in(bx, pad))
            theirs = tokens((tx or "").split())
            ag, d_only, m_only, numeric = compare(theirs, mine)
            items_rows.append({"ref": it.self_ref, "label": str(it.label.value), "page": pno,
                               "bbox": [r(v, 1) for v in bx], "reader": "layout: docling; text: docling-parse",
                               "grade": "exact", "chars": len(tx or ""),
                               "text_sha256": sha256_bytes((tx or "").encode()),
                               "mupdf_words": n_words, "mupdf_agreement": r(ag, 3),
                               "heading_level": getattr(it, "level", None) if isinstance(it, SectionHeaderItem) else None})
            row_of[it.self_ref].append(items_rows[-1])
            if it.self_ref in redacted_here:
                continue                   # docling's reading holds redacted text
            if max(len(squash(mine)), len(squash(theirs))) >= minc and ag < thr:
                disputed += 1
                # The text layer's words are what the document says; docling's differing
                # reading is recorded, and is an error only where the item keeps it.
                issue(pno, bx, "text_disagrees", "info",
                      f"similarity {ag:.2f}; only docling: {clip(d_only, 200)!r}; only MuPDF: {clip(m_only, 200)!r}; "
                      f"docling: {clip(tx, 200)!r}", "agreement 2", ref=it.self_ref, numeric=numeric)

        for tb, tbx in (tables if ocr_page else []):
            items_rows.append({"ref": tb.self_ref, "label": "table", "page": pno, "bbox": [r(v, 1) for v in tbx],
                               "reader": "docling TableFormer on RapidOCR text", "grade": "ocr",
                               "rows": tb.data.num_rows, "cols": tb.data.num_cols, "cells": len(tb.data.table_cells)})
        for tb, tbx in ([] if ocr_page else tables):
            cells = tb.data.table_cells
            cell_dis = 0
            for c in cells:
                if c.bbox is None or not (c.text or "").strip():
                    continue
                cb = c.bbox if c.bbox.coord_origin == CoordOrigin.TOPLEFT else c.bbox.to_top_left_origin(page_height=ph)
                cbx = (cb.l, cb.t, cb.r, cb.b)
                mine = text_in(w_table, cbx, pad)
                theirs = tokens(c.text.split())
                ag, d_only, m_only, numeric = compare(theirs, mine)
                if ag < thr and max(len(squash(mine)), len(squash(theirs))) >= 1:
                    cell_dis += 1
                    issue(pno, cbx, "cell_text_disagrees", "error" if numeric else "warning",
                          f"cell r{c.start_row_offset_idx}c{c.start_col_offset_idx}: docling {clip(c.text, 120)!r}, "
                          f"MuPDF {clip(' '.join(mine), 120)!r}", "agreement 2", ref=tb.self_ref)
            boxed = []
            for c in cells:
                if c.bbox is not None:
                    cb = c.bbox if c.bbox.coord_origin == CoordOrigin.TOPLEFT else c.bbox.to_top_left_origin(page_height=ph)
                    boxed.append(((cb.l, cb.t, cb.r, cb.b), c.text))
            score = table_word_score(w_table.words_in(tbx, pad), boxed, pad)
            fit = table_structure_score(w_table.words_in(tbx, pad), [b for b, _ in boxed], pad)
            for k in ("words", "placed", "wrong_cell", "no_cell", "lost"):
                table_words[k] += score[k]
            for k in ("straddles", "merged", "split", "rows_merged", "bad"):
                table_words["fit_" + k] += fit[k]
            if fit["rows_merged"]:
                issue(pno, tbx, "table_rows_merged", "warning",
                      f"{fit['rows_merged']} of the table's words are in cells holding several rows each "
                      f"(stacked values beside stacked lines at the same heights)", "tables 2", ref=tb.self_ref,
                      words=fit["rows_merged"])
            if score["lost"]:
                issue(pno, tbx, "table_text_lost", "error",
                      f"{score['lost']} of {score['words']} word(s) inside a cell's box but in no cell's text: "
                      f"{clip(' '.join(score['examples']['lost']), 300)}", "tables 1", ref=tb.self_ref,
                      words=score["lost"])
            items_rows.append({"ref": tb.self_ref, "label": "table", "page": pno, "bbox": [r(v, 1) for v in tbx],
                               "reader": (lambda m: "ruling lines (PyMuPDF) + text-layer words" if m == "lines" else
                                          "text alignment (PyMuPDF) + row gaps + text-layer words" if m == "aligned" else
                                          "Table Transformer v1.1 + text-layer words" if m == "tatr" else
                                          f"docling TableFormer ({m}) + text-layer cells")(
                                   table_mode_at.get((pno, tuple(r(v, 0) for v in tbx)), ctx['table_modes'][0])),
                               "grade": "inferred_grid",
                               "rows": tb.data.num_rows, "cols": tb.data.num_cols, "cells": len(cells),
                               "cells_disagreeing": cell_dis, "word_score": score, "structure": fit})

        # text inside pictures: docling does not read it; the text layer has it
        for pic, pbx in pictures:
            inside = sorted(w_pic.words_in(pbx, pad), key=reading_order)
            groups = cluster(inside, SETTINGS["cluster_gap_pt"])
            refs = []
            for g in groups:
                gb = union([w["box"] for w in g])
                txt = " ".join(w["text"] for w in g)
                bl = BoundingBox(l=gb[0], t=gb[1], r=gb[2], b=gb[3], coord_origin=CoordOrigin.TOPLEFT
                                 ).to_bottom_left_origin(page_height=ph)
                ti = doc.add_text(label=DocItemLabel.TEXT, text=txt, parent=pic,
                                  prov=ProvenanceItem(page_no=pno, bbox=bl, charspan=(0, len(txt))))
                refs.append(ti.self_ref)
                for w in g:
                    w["claimed"] = ti.self_ref
                items_rows.append({"ref": ti.self_ref, "label": "text", "page": pno, "bbox": [r(v, 1) for v in gb],
                                   "reader": "MuPDF (text layer) inside a picture", "grade": "exact",
                                   "chars": len(txt), "text_sha256": sha256_bytes(txt.encode()), "parent": pic.self_ref})
            added_picture_text += len(groups)
            items_rows.append({"ref": pic.self_ref, "label": "picture", "page": pno, "bbox": [r(v, 1) for v in pbx],
                               "reader": "docling layout", "grade": "structure", "text_items_added": len(refs)})
            if groups:
                issue(pno, pbx, "picture_text_added", "info",
                      f"{len(inside)} text-layer word(s) inside this picture added as {len(groups)} text item(s)",
                      "coverage 1", ref=pic.self_ref)

        # Step 1: the evidence layer. Every text-layer word on the page, its box, and the
        # item that holds it: a docling item, or one added from the text layer.
        own_text = Grid.of_items([(bx, it.self_ref) for it, bx, _ in text_boxes], pad)
        own_table = Grid.of_items([(bx, it.self_ref) for it, bx in tables], pad)
        if ocr_page:                       # the OCR's own words are this page's evidence
            for it, bx, tx in text_boxes:
                for t in (tx or "").split():
                    evidence_rows.append({"page": pno, "w": t, "bbox": [r(v, 2) for v in bx],
                                          "item": it.self_ref, "how": "ocr"})
            for tb, tbx in tables:
                for c in tb.data.table_cells:
                    for t in (c.text or "").split():
                        evidence_rows.append({"page": pno, "w": t, "bbox": [r(v, 2) for v in tbx],
                                              "item": tb.self_ref, "how": "ocr"})
        for w in words:
            ref = w.get("claimed")
            how = "added" if ref else ("text_layer_only" if pno in fallback else "docling")
            if not ref:
                ref = (own_text.owner(w["cx"], w["cy"], pad) or own_table.owner(w["cx"], w["cy"], pad))
            evidence_rows.append({"page": pno, "w": w["text"], "bbox": [r(v, 2) for v in w["box"]],
                                  "block": w["block"], "line": w["line"], "wno": w["wno"],
                                  "item": ref, "how": how if ref else "unclaimed"})

        page_checks[str(pno)] = {"words": len(words), "in_text": len(in_text), "in_table": len(in_table),
                                 "in_picture": len(in_picture), "uncovered": len(uncovered),
                                 "not_in_cell": len(not_in_cell), "items": len(entries),
                                 "text_items_disputed": disputed, "reader": "ocr" if ocr_page else reader_of[pno],
                                 "coverage": r(1 - (len(uncovered) + len(not_in_cell)) / len(words), 4) if words else None}
    # The text-layer words become the items' text (checked above against docling's).
    retexted, kept_docling = 0, collections.Counter()
    for ref, parts in item_words.items():
        it = item_objs[ref]
        provs = it.prov or []
        why = ("part on an unconverted page" if any(pv.page_no not in doc.pages for pv in provs) else
               "its words all belong to smaller or identical overlapping items" if not any(ws for _, _, ws in parts) else
               "boxes and pages do not match" if len(parts) != len(provs) else None)
        if why and ref in redacted_refs:   # nothing of it shows: docling's text was redacted text
            it.text = it.orig = ""
            for pv in provs:
                pv.charspan = (0, 0)
            kept_docling["emptied: under a redaction"] += 1
            continue
        if why:
            kept_docling[why] += 1
            continue
        texts = [" ".join(w["text"] for w in ws) for _, _, ws in parts]
        if isinstance(it, ListItem) and texts[0]:
            first, _, rest = texts[0].partition(" ")
            if len(first) == 1 and not first.isalnum():
                texts[0] = rest            # the bullet: the Markdown writes its own marker
        new = " ".join(t for t in texts if t)
        if ref in redacted_refs:
            it.orig = new                  # docling's reading had redacted text in it
            it.text = new
        elif new != it.text:
            it.orig = it.text              # docling's reading, kept in the document
            it.text = new
        at = 0
        for pv, t in zip(provs, texts):
            pv.charspan = (at, at + len(t))
            at += len(t) + 1
        retexted += 1
        for row in row_of[ref]:
            row["reader"] = "layout: docling; text: MuPDF (text layer)"
            row["chars"] = len(new)
            row["text_sha256"] = sha256_bytes(new.encode())
    rec["items_text_from_text_layer"] = {"items": retexted, "kept_docling_text": dict(kept_docling)}
    timings["checks"] = r((time.perf_counter() - s) * 1000, 1)

    # ---- the outline against the headings docling found
    toc = (t0rec.get("pdf") or {}).get("toc") or []
    headings = collections.defaultdict(list)
    for item, _ in doc.iterate_items():
        if isinstance(item, (SectionHeaderItem, TitleItem)) and item.prov:
            headings[item.prov[0].page_no].append(item)
    outline = {"entries": len(toc), "matched": 0, "on_converted_pages": 0}
    for lvl, title, pno in toc:
        if pno not in todo or not (title or "").strip():
            continue
        outline["on_converted_pages"] += 1
        t = " ".join(title.split()).lower()
        best = max((difflib.SequenceMatcher(None, t, " ".join(h.text.split()).lower()).ratio()
                    for h in headings.get(pno, [])), default=0)
        if best >= SETTINGS["outline_match_ratio"]:
            outline["matched"] += 1
        else:
            hits = pdf[pno - 1].search_for(title.strip()[:120])
            box = None
            if hits:
                h0 = hits[0] * pdf[pno - 1].rotation_matrix
                box = (h0.x0, h0.y0, h0.x1, h0.y1)
            issue(pno, box, "outline_heading_missed", "info",
                  f"outline level {lvl} {clip(title, 150)!r} has no matching heading on its page (best {best:.2f})",
                  "outline 1")
    rec["outline"] = outline

    # ---- forms and annotations, straight from tier 0
    forms, annots = [], []
    for p in t0pages:
        a = p.get("annotations") or {}
        for w in a.get("widgets") or []:
            forms.append({"page": p["index"] + 1, **w})
        for an in a.get("annots") or []:
            annots.append({"page": p["index"] + 1, **an})

    # ---- write
    s = time.perf_counter()
    files = {}

    def put(name, data: bytes):
        write_bytes_atomic(out_dir / name, data)
        files[name] = {"sha256": sha256_bytes(data), "bytes": len(data)}

    import gzip
    docj = json.dumps(doc.export_to_dict(), ensure_ascii=False).encode()
    put(f"{doc_id}.docling.json.gz", gzip.compress(docj, mtime=0))
    put(f"{doc_id}.items.jsonl.gz", gzip.compress(b"".join(json.dumps(x, ensure_ascii=False).encode() + b"\n"
                                                           for x in items_rows), mtime=0))
    # Step 6, early: the Markdown export holds everything (text under pictures, page
    # furniture), and every evidence word must reach it. docling's default export left
    # out 91% of one filing's words.
    md = export_markdown(doc)
    put(f"{doc_id}.md", md.encode())
    ev = collections.Counter(tokens([e["w"] for e in evidence_rows]))
    got = collections.Counter(tokens(md_words(md)))
    total, found = sum(ev.values()), sum((ev & got).values())
    export_recall = found / total if total else None
    if total and export_recall < SETTINGS["export_min_recall"]:
        missing = list((ev - got).elements())
        issue(None, None, "export_missing_text", "error",
              f"{total - found} of {total} evidence words not in the Markdown export, e.g. {clip(' '.join(missing[:40]), 300)}",
              "export 1")
    put(f"{doc_id}.evidence.jsonl.gz", gzip.compress(b"".join(json.dumps(x, ensure_ascii=False).encode() + b"\n"
                                                              for x in evidence_rows), mtime=0))
    put(f"{doc_id}.issues.json", json.dumps(issues, indent=1, ensure_ascii=False).encode())
    pb = progress_path.read_bytes()
    files[progress_path.name] = {"sha256": sha256_bytes(pb), "bytes": len(pb)}
    put("forms.json", json.dumps(forms, indent=1, ensure_ascii=False, default=str).encode())
    put("annotations.json", json.dumps(annots, indent=1, ensure_ascii=False, default=str).encode())
    try:
        rp = review_pdf(pdf, issues, rec, meta)
        put(f"{doc_id}.review.pdf", rp)
        files[f"{doc_id}.review.pdf"]["starts_with_original"] = rp[:rec["file"]["size"]] == path.read_bytes()
    except Exception as e:
        reasons.append("review_pdf_failed")
        rec["review_pdf_error"] = {"error": repr(e), "trace": traceback.format_exc(limit=5)}
    pdf.close()
    timings["write"] = r((time.perf_counter() - s) * 1000, 1)
    rec["files"] = files

    sev = collections.Counter(i["severity"] for i in issues)
    kinds = collections.Counter(i["kind"] for i in issues if i["severity"] != "pending")
    rec["checks"] = {"pages": page_checks}
    rec["summary"] = {
        "pages": n_pages, "pages_converted": len(todo), "routes": rec["routes"]["counts"],
        "items": len(items_rows), "tables": len(doc.tables), "pictures": len(doc.pictures),
        "texts": len(doc.texts), "picture_text_items_added": added_picture_text,
        "issues_by_severity": {k: sev.get(k, 0) for k in ("error", "warning", "info", "pending")},
        "issues_by_kind": dict(kinds),
        "words": sum(c["words"] for c in page_checks.values()),
        "words_uncovered": sum(c["uncovered"] + c["not_in_cell"] for c in page_checks.values()),
        "outline_matched": f"{outline['matched']}/{outline['on_converted_pages']}",
        "text_items_added": added_text, "table_text_items_added": added_in_table,
        "table_words": table_words["words"], "table_words_placed": table_words["placed"],
        "table_words_wrong_cell": table_words["wrong_cell"], "table_words_no_cell": table_words["no_cell"],
        "table_words_lost": table_words["lost"],
        "table_placed_share": r(table_words["placed"] / table_words["words"], 4) if table_words["words"] else None,
        "tables_kept_by_mode": dict(collections.Counter(c["kept"] for c in table_choices)),
        "pages_by_reader": dict(collections.Counter(reader_of.values())),
        "words_under_redaction": redacted_words, "items_under_redaction": len(redacted_refs),
        "ocr_items": ocr_stats["items"], "ocr_items_voted": ocr_stats["voted"],
        "ocr_hidden_agreement_mean": r(ocr_stats["agreement_sum"] / ocr_stats["voted"], 4) if ocr_stats["voted"] else None,
        "table_fit": {k: table_words["fit_" + k] for k in ("straddles", "merged", "split", "rows_merged", "bad")},
        "text_only_pages": len(text_only),
        "evidence_words": len(evidence_rows),
        "evidence_unclaimed": sum(1 for e in evidence_rows if e["how"] == "unclaimed"),
        "export_word_recall": r(export_recall, 4),
    }
    words = rec["summary"]["words"]
    rec["summary"]["coverage"] = r(1 - rec["summary"]["words_uncovered"] / words, 4) if words else None
    if any(e["status"] != "success" for e in ranges):
        return finish("partial")
    return finish("ok")


# --------------------------------------------------------------------------
# the review PDF

def review_pdf(pdf, issues: list[dict], rec: dict, meta: dict) -> bytes:
    """The original bytes, unchanged, with one annotation per issue appended as
    an incremental update: the review PDF begins with exactly the filed PDF.
    (Copying pages into a new document instead broke on a filing whose form
    field tree PyMuPDF could not follow.)"""
    import pymupdf
    import tempfile
    src = Path(pdf.name)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "review.pdf"
        tmp.write_bytes(src.read_bytes())
        review = pymupdf.open(tmp)
        if review.needs_pass:
            review.authenticate("")
        _annotate(review, issues, meta)
        try:
            review.save(tmp, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        except Exception:
            # Some files cannot take an incremental update (e.g. repaired on open):
            # then a full save, and the review PDF no longer starts with the original bytes.
            full = review.tobytes(garbage=0, deflate=True)
            review.close()
            return full
        review.close()
        return tmp.read_bytes()


def _annotate(review, issues: list[dict], meta: dict) -> None:
    import pymupdf
    by_page = collections.defaultdict(list)
    for i in issues:
        # a document-wide issue (the export check) is a note on the first page
        by_page[i["page"] or 1].append(i)
    cap = SETTINGS["review_max_per_kind"]
    for pno, items in by_page.items():
        page = review[pno - 1]
        derot = page.derotation_matrix
        drawn, over = collections.Counter(), collections.Counter()
        shown = []
        for i in items:
            if drawn[i["kind"]] < cap:
                drawn[i["kind"]] += 1
                shown.append(i)
            else:
                over[(i["kind"], i["severity"])] += 1
        # one note for what was not drawn on this page, so the count is still visible
        for (kind, sev), k in over.items():
            shown.append({"kind": kind, "severity": sev, "bbox": None, "check": "review cap",
                          "evidence": f"{k} more {kind} issue(s) on this page not drawn (at most {cap} per kind); "
                                      f"every one is in issues.json"})
        for n, i in enumerate(shown):
            colour = SEVERITY_COLOURS.get(i["severity"], (0, 0, 0))
            if i["bbox"]:
                x0, y0, x1, y1 = i["bbox"]
                rect = pymupdf.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1) * derot
            else:
                rect = pymupdf.Rect(8, 8 + 14 * n, 22, 22 + 14 * n) * derot
            if i["kind"] == "page_not_converted":
                rect = pymupdf.Rect(6, 6, 30, 30) * derot
                a = page.add_text_annot(rect.tl, "", icon="Note")
            else:
                a = page.add_rect_annot(rect)
                a.set_border(width=1.2 if i["severity"] == "error" else 0.8)
                a.set_opacity(0.9)
            a.set_colors(stroke=colour)
            a.set_info(title=f"convert {i['severity']}: {i['kind']}",
                       content=f"{i['evidence']}\n\ncheck: {i['check']}\nrun: convert-{meta['signature']}",
                       subject=i["kind"])
            a.update()


# --------------------------------------------------------------------------
# commands

def cmd_run(args) -> int:
    runs = Path(args.runs)
    t0dir = tier0_run(runs, args.tier0)
    stage = Convert(t0dir)
    idx = load_index(t0dir)

    def ready(todo):
        keep, skipped = [], collections.Counter()
        for p in todo:
            ev = idx.get(p.stem)
            if not ev:
                skipped["no_tier0_record"] += 1
            elif ev["status"] == "failed":
                skipped["tier0_failed"] += 1
            else:
                keep.append(p)
        return keep, skipped

    return run_stage(stage, args, filter_todo=ready)


def cmd_status(args) -> int:
    for run_dir in runs_of(Path(args.runs), STAGE):
        idx = load_index(run_dir)
        counts = collections.Counter(ev["status"] for ev in idx.values())
        sev, kinds = collections.Counter(), collections.Counter()
        pages = conv = words = unc = 0
        for ev in idx.values():
            s = ev.get("summary") or {}
            sev.update(s.get("issues_by_severity") or {})
            kinds.update(s.get("issues_by_kind") or {})
            pages += s.get("pages", 0)
            conv += s.get("pages_converted", 0)
            words += s.get("words", 0)
            unc += s.get("words_uncovered", 0)
        print(f"run {run_dir.name}: {len(idx)} documents, {conv}/{pages} pages converted")
        print("  status  " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        if words:
            print(f"  words   {words:,} in the text layer of converted pages, {unc:,} not extracted "
                  f"({100 * (1 - unc / words):.2f}% covered)")
        print("  issues  " + ", ".join(f"{k} {v}" for k, v in sev.most_common()))
        for k, v in kinds.most_common(12):
            print(f"  {v:8}  {k}")
    return 0


def flatten_doc(args_tuple) -> dict[str, list[dict]]:
    """One document's record, issues and items as rows."""
    rec_path = Path(args_tuple)
    rows = collections.defaultdict(list)
    try:
        rec = read_json_gz(rec_path)
    except Exception as e:
        return {"unreadable": [{"path": str(rec_path), "error": repr(e)}]}
    did, s = rec["doc_id"], rec.get("summary") or {}
    sev = s.get("issues_by_severity") or {}
    rows["documents"].append({
        "doc_id": did, "status": rec.get("status"), "reasons": ",".join(rec.get("reasons") or []),
        "seconds": rec.get("seconds"), "sha256": (rec.get("file") or {}).get("sha256"),
        "pages": s.get("pages"), "pages_converted": s.get("pages_converted"),
        "items": s.get("items"), "texts": s.get("texts"), "tables": s.get("tables"), "pictures": s.get("pictures"),
        "picture_text_items_added": s.get("picture_text_items_added"),
        "words": s.get("words"), "words_uncovered": s.get("words_uncovered"), "coverage": s.get("coverage"),
        "outline_matched": s.get("outline_matched"), **{f"issues_{k}": v for k, v in sev.items()},
        "tier0_run": rec.get("tier0_run"), "signature": rec.get("signature"), "session": rec.get("session")})
    routes = (rec.get("routes") or {}).get("by_page") or {}
    checks = (rec.get("checks") or {}).get("pages") or {}
    conf = {}
    for rg in (rec.get("docling") or {}).get("ranges") or []:
        conf.update(rg.get("confidence") or {})
    for pno, route in routes.items():
        c, cf = checks.get(pno) or {}, conf.get(pno) or conf.get(int(pno)) or {}
        rows["pages"].append({"doc_id": did, "page_no": int(pno), "route": route, **c,
                              "docling_mean_grade": cf.get("mean_grade"), "docling_low_grade": cf.get("low_grade"),
                              "docling_layout_score": cf.get("layout_score"), "docling_parse_score": cf.get("parse_score")})
    out = rec_path.parent
    try:
        for i in json.loads((out / f"{did}.issues.json").read_text()):
            b = i.get("bbox") or [None] * 4
            rows["issues"].append({"doc_id": did, "page_no": i.get("page"), "kind": i["kind"],
                                   "severity": i["severity"], "check": i.get("check"), "evidence": i.get("evidence"),
                                   "route": i.get("route"), "ref": i.get("ref"), "words": i.get("words"),
                                   "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]})
    except FileNotFoundError:
        pass
    import gzip
    try:
        with gzip.open(out / f"{did}.items.jsonl.gz") as f:
            for line in f:
                it = json.loads(line)
                b = it.pop("bbox", None) or [None] * 4
                rows["items"].append({"doc_id": did, **it, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]})
    except FileNotFoundError:
        pass
    return rows


def cmd_flatten(args) -> int:
    import multiprocessing as mp
    import shutil
    import pyarrow as pa
    import pyarrow.parquet as pq
    runs = runs_of(Path(args.runs), STAGE)
    if args.run:
        runs = [d for d in runs if d.name in (args.run, f"{STAGE}-{args.run}")]
    if not runs:
        print("no such run")
        return 2
    run_dir = runs[-1]
    sig = run_dir.name.split("-", 1)[1]
    idx = load_index(run_dir)
    paths = [str(Path(args.output) / d / "convert" / f"{STAGE}-{sig}" / f"{d}.{STAGE}.json.gz") for d in sorted(idx)]
    tmp = run_dir / "parquet.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.monotonic()
    totals = collections.Counter()
    with mp.get_context("spawn").Pool(args.workers) as pool:
        for part, start in enumerate(range(0, len(paths), args.batch)):
            batch = collections.defaultdict(list)
            for rows in pool.imap(flatten_doc, paths[start:start + args.batch], chunksize=10):
                for table, rs in rows.items():
                    batch[table] += rs
            for table, rs in batch.items():
                if rs:
                    (tmp / table).mkdir(parents=True, exist_ok=True)
                    pq.write_table(pa.Table.from_pylist(rs), tmp / table / f"part-{part:05d}.parquet", compression="zstd")
                    totals[table] += len(rs)
    out = run_dir / "parquet"
    shutil.rmtree(out, ignore_errors=True)
    os.replace(tmp, out)
    write_bytes_atomic(out / "manifest.json", json.dumps({"built_at": utcnow(), "documents": len(paths),
                                                          "rows": dict(totals)}, indent=2).encode())
    print(f"{out}  ({time.monotonic() - t0:.0f}s)")
    for table, n in sorted(totals.items()):
        print(f"  {table:12} {n:>10,} rows")
    return 1 if totals["unreadable"] else 0


def cmd_models(args) -> int:
    """The models this pass uses, into a folder of their own so each file can be hashed."""
    from docling.utils.model_downloader import download_models
    download_models(MODELS, with_layout=True, with_tableformer=True, with_code_formula=False,
                    with_picture_classifier=True, with_rapidocr=False, progress=True)
    from huggingface_hub import snapshot_download
    snapshot_download(TATR_REPO, local_dir=TATR_DIR)
    for name, m in model_identity().items():
        print(f"{name}: revision {', '.join(m['revision']) or '?'}, {m['files']} files, sha256 {m['sha256'][:16]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="convert PDFs (default: every PDF in source/)")
    add_run_arguments(run)
    run.add_argument("--tier0", help="tier 0 run to read (default: the newest)")
    run.set_defaults(workers=2, timeout=3600, recycle_after=100)   # 2 workers: legacy batch.py's default
    sub.add_parser("status", help="what every run holds")
    sub.add_parser("models", help=f"download the models into {MODELS}")
    fl = sub.add_parser("flatten", help="derive Parquet tables (documents, pages, issues, items) from a run")
    fl.add_argument("--run", help="signature of the run (default: the newest)")
    fl.add_argument("--workers", type=int, default=8)
    fl.add_argument("--batch", type=int, default=1000, help="documents per Parquet part")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "models": cmd_models, "flatten": cmd_flatten}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
