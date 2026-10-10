"""What each page and region is: layout regions, a page kind, the text source and rotation.

    scripts/convert/.venv/bin/python scripts/convert/pages.py run [PDF or DIR ...] [--workers 1]
    scripts/convert/.venv/bin/python scripts/convert/pages.py status

Runs after tier 0 and before the readers (DESIGN.md, Page kinds). For every page:

  regions   docling's layout model (Heron): label, box, confidence. Its raw regions,
            before docling drops those it found no text in, so scans keep theirs.
            No OCR and no table structure: those are the readers' work.
  kind      text, table, picture, form, contents, transcript, mixed, empty, or
            not_read (a page too heavy for the layout model, from tier 0)
  source    where the text comes from: the text layer or OCR (tier 0's route)
  rotation  the direction most of the page's text runs, from tier 0

A page's kind is the region group covering most of its content area, page
headers and footers left out, with what the survey's contact sheets showed the
layout model gets wrong:

  form        a key/value region alone is not a form (letters' address blocks
              are marked so); a form has checkboxes, AcroForm fields or a form region
  transcript  line numbers on one pitch (convert.line_numbers), in a document
              where most pages with text have them
  contents    a document_index region, or most lines ending in dot leaders

Record: output/<id>/convert/pages-<sig>/<id>.pages.json.gz.
"""
import argparse
import base64
import collections
import json
import logging
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DEFAULT_OUTPUT, DEFAULT_RUNS, HERE, Stage, add_run_arguments, base_environment,  # noqa: E402
                    clip, describe_basic, load_index, r, read_json_gz, run_stage, runs_of, sha256_file, utcnow)
import pagelib as pl  # noqa: E402

STAGE = "pages"

SETTINGS = {
    "schema": 1,
    "device": "cuda",
    "backend": "pypdfium2",            # docling-parse rendered 4710294's scans as noise (convert's OCR note)
    "chunk_pages": 10,                 # pages per layout call: bounds memory on long filings
    "keep_empty_clusters": True,       # without OCR a scan has no text cells, and docling drops every region
                                       # without one: scanned text pages read as empty (4462406 p13, p62)
    "furniture": ["page_header", "page_footer"],
    "groups": {"text": "text", "list_item": "text", "section_header": "text", "title": "text",
               "caption": "text", "footnote": "text", "formula": "text", "key_value_region": "text",
               "code": "code", "table": "table", "picture": "picture", "chart": "picture",
               "document_index": "contents", "form": "form",
               "checkbox_selected": "form", "checkbox_unselected": "form"},
    "kind_min_share": {"contents": 0.3, "form": 0.3, "table": 0.6, "picture": 0.6, "text": 0.8},
    "mixed_part_share": 0.2,           # a group covering this much of a mixed page is named in its kind
    "contents_dot_lines": 0.25,        # share of a page's lines with dot leaders that makes it contents
    "transcript_doc_share": 0.5,       # share of a document's pages with text that carry line numbers
    "empty_max_words": 5,
    # A second opinion from a vision model (Ollama), only where the layout model is weak. On 120
    # random pages (2026-10-10) the two agreed on 90; of the earlier round's disagreements, read by
    # eye, qwen3-vl looked better on scans and checkbox forms. Taking its kind on every scanned page
    # then changed 177 of the sample's 3,027 pages; of 24 read by eye about 8 were right, nearly all
    # of them forms (comment logs, certificates, test sheets), and 13 wrong: scanned tables and
    # engineering drawings called text or mixed (4462406, 2813525, 586443). So its kind is taken
    # only when it says form; otherwise both readings are kept and the page is marked disputed.
    # Asked about scanned pages, pages with no content regions, and pages a picture covers at least
    # `picture_share` of (also for the picture's type). Its confidence is not used (0.98 on every
    # page, right or wrong).
    "vlm": {"model": "qwen3-vl:8b-instruct", "url": "http://localhost:11434", "dpi": 72,
            "picture_share": 0.2,      # a picture this large is asked about; a letterhead's logo is not
            "takes_kind": ["form"],    # the only kinds its reading replaces the layout model's with
            "timeout_s": 120, "kinds": ["text", "table", "form", "contents", "transcript", "picture",
                                        "mixed", "empty"],
            "picture_types": ["none", "map", "aerial_photo", "site_photo", "chart", "engineering_drawing",
                              "instrument_record", "logo", "other"]},
}
VLM_PROMPT = (
    "Classify this document page. Answer with JSON only: "
    '{"kind": one of %(kinds)s, "picture_type": one of %(picture_types)s, "rotated": true or false}. '
    "text = prose, letters, reports; table = mostly a table; form = fields to fill in or filled in, "
    "checkboxes, certificates; contents = table of contents or index; transcript = numbered lines of a "
    "hearing transcript; picture = mostly a map, photo, chart or drawing; mixed = text with a large table "
    "or picture; empty = blank or nearly.")
DOTS = re.compile(r"(\.\s?){4,}|…{2,}|_{6,}")


class Pages(Stage):
    name = STAGE
    code_files = [Path(__file__).resolve(), HERE / "pagelib.py", HERE / "common.py"]
    packages = ["docling", "pymupdf"]
    settings = SETTINGS
    worker_module = "pages"
    min_start_gap_s = 5.0

    def __init__(self, tier0_dir: Path):
        self.tier0_dir = tier0_dir
        self._models = None

    def models(self):
        if self._models is None:
            ident = pl.model_identity()
            self._models = {k: v for k, v in ident.items() if "layout" in k} if "error" not in ident else ident
        return self._models

    def worker_meta(self):
        return {"tier0_dir": str(self.tier0_dir)}

    def extra_identity(self):
        return {"models": self.models(), "tier0_run": self.tier0_dir.name, "vlm": vlm_identity()}

    def environment(self):
        import importlib.metadata as m
        env = base_environment()
        env.update({"docling": m.version("docling"), "docling_core": m.version("docling-core"),
                    "pymupdf": m.version("pymupdf"), "models": self.models(), "gpu": pl._gpu_name()})
        return env

    def header_lines(self, env):
        return [f"docling  {env['docling']}, {env.get('gpu')}", f"tier0    {self.tier0_dir}"]

    def gate(self):
        return gpu_gate()

    def describe(self, ev):
        s = ev.get("summary") or {}
        if not s:
            return describe_basic(ev)
        kinds = s.get("kinds") or {}
        bits = [f"{ev['status']:<7}", f"{ev['doc_id']:<12}", f"{s.get('pages', 0):>5}p",
                f"{ev.get('seconds') or 0:7.2f}s",
                " ".join(f"{k}:{v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:5])]
        if s.get("ocr_pages"):
            bits.append(f"ocr:{s['ocr_pages']}")
        if ev.get("reasons"):
            bits.append("why=" + ",".join(ev["reasons"]))
        if ev.get("error"):
            bits.append("error=" + clip(ev["error"], 200))
        return "  ".join(bits)


def gpu_gate():
    free = pl.gpu_free_mb()
    need = pl.SETTINGS["min_free_gpu_mb"]
    return f"{free} MB GPU memory free, need {need}" if free is not None and free < need else None


# --------------------------------------------------------------------------
# the worker

def init_worker(meta: dict):
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(artifacts_path=pl.MODELS, do_ocr=False, do_table_structure=False)
    opts.layout_options.keep_empty_clusters = SETTINGS["keep_empty_clusters"]
    opts.accelerator_options = AcceleratorOptions(device=SETTINGS["device"])
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(
        pipeline_options=opts, backend=PyPdfiumDocumentBackend)})
    conv.initialize_pipeline(InputFormat.PDF)
    cap = pl._Capture()
    logging.getLogger().addHandler(cap)
    return {"converter": conv, "capture": cap}


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def shares(regions: list[dict]) -> dict[str, float]:
    """Share of the content area by region group; a region inside a larger one counts once, by the outer."""
    rs = [x for x in regions if x["label"] not in SETTINGS["furniture"]]
    by = collections.Counter()
    for x in rs:
        b = x["box"]
        inside = any(o is not x and area(o["box"]) > area(b) and o["box"][0] <= b[0] + 2 and o["box"][1] <= b[1] + 2
                     and o["box"][2] >= b[2] - 2 and o["box"][3] >= b[3] - 2 for o in rs)
        if not inside:
            by[SETTINGS["groups"].get(x["label"], x["label"])] += area(b)
    tot = sum(by.values())
    return {k: v / tot for k, v in by.items()} if tot else {}


def page_kind(p: dict, transcript_doc: bool) -> str:
    """The page's kind from its region shares and signals (module docstring)."""
    if p.get("not_read"):
        return "not_read"
    s = p["shares"]
    m = SETTINGS["kind_min_share"]
    if not s:
        return "empty" if p["words"] <= SETTINGS["empty_max_words"] else "text"
    if transcript_doc and p["line_numbers"]:
        return "transcript"
    if s.get("contents", 0) >= m["contents"] or p["dot_lines"] >= SETTINGS["contents_dot_lines"]:
        return "contents"
    if p["widgets"] or (s.get("form", 0) >= m["form"]):
        return "form"
    if s.get("table", 0) >= m["table"]:
        return "table"
    if s.get("picture", 0) >= m["picture"]:
        return "picture"
    if s.get("text", 0) + s.get("code", 0) >= m["text"]:
        return "text"
    parts = sorted(k if k != "code" else "text" for k, v in s.items() if v >= SETTINGS["mixed_part_share"])
    return "mixed:" + "+".join(sorted(set(parts))) if parts else "text"


def vlm_identity() -> dict:
    """The vision model's name and digest, or why it is not available."""
    v = SETTINGS["vlm"]
    try:
        with urllib.request.urlopen(f"{v['url']}/api/tags", timeout=10) as resp:
            tags = json.load(resp)["models"]
        m = next((t for t in tags if t["name"] == v["model"]), None)
        return {"model": v["model"], "digest": m["digest"]} if m else {"model": v["model"], "error": "not pulled"}
    except Exception as e:
        return {"model": v["model"], "error": repr(e)}


def ask_vlm(png: bytes) -> dict:
    v = SETTINGS["vlm"]
    body = {"model": v["model"], "prompt": VLM_PROMPT % {"kinds": v["kinds"], "picture_types": v["picture_types"]},
            "images": [base64.b64encode(png).decode()], "stream": False, "format": "json",
            "options": {"temperature": 0}}
    req = urllib.request.Request(f"{v['url']}/api/generate", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=v["timeout_s"]) as resp:
        ans = json.loads(json.load(resp)["response"])
    return {k: ans.get(k) for k in ("kind", "picture_type", "rotated")}


def needs_second_opinion(p: dict) -> str | None:
    """Why the vision model is asked about this page, or None."""
    if p.get("not_read"):
        return None
    if p["source"] == "ocr":
        return "scanned"
    if not p["shares"]:
        return "no regions"
    if p["shares"].get("picture", 0) >= SETTINGS["vlm"]["picture_share"]:
        return "picture"
    return None


def second_opinion(pdf, pages: list[dict]) -> int:
    """Ask the vision model where the layout model is weak; take its kind only where it is reliable
    (SETTINGS["vlm"]), mark the other disagreements. Returns how many pages were asked."""
    asked = 0
    down = None
    for p in pages:
        why = needs_second_opinion(p)
        if not why:
            continue
        if down:
            p["vlm"] = {"asked_because": why, "error": down}
            continue
        ts = time.perf_counter()
        try:
            ans = ask_vlm(pdf[p["page"] - 1].get_pixmap(dpi=SETTINGS["vlm"]["dpi"]).tobytes("png"))
        except Exception as e:             # Ollama down or not answering: the layout reading stands
            down = repr(e)
            p["vlm"] = {"asked_because": why, "error": down}
            continue
        asked += 1
        p["vlm"] = {"asked_because": why, **ans, "seconds": r(time.perf_counter() - ts, 2)}
        if ans.get("kind") in SETTINGS["vlm"]["kinds"] and ans["kind"] != p["kind"].split(":")[0]:
            if ans["kind"] in SETTINGS["vlm"]["takes_kind"]:
                p["kind"] = ans["kind"]
                p["kind_by"] = "vlm"
            else:
                p["kind_disputed"] = True
    return asked


def rotation(t0page: dict) -> int:
    """Degrees the page's text mostly runs at, as displayed: 0, 90, 180 or 270."""
    d = (t0page.get("text") or {}).get("directions") or {}
    tot = sum(d.values())
    if not tot:
        return 0
    k, v = max(d.items(), key=lambda kv: kv[1])
    return int(k[1:]) if k in ("d90", "d180", "d270") and v / tot > 0.5 else 0


def runs_of_pages(pages: list[int], size: int):
    """Contiguous runs of page numbers, each cut to at most `size` pages."""
    run = []
    for p in pages:
        if run and (p != run[-1] + 1 or len(run) == size):
            yield run
            run = []
        run.append(p)
    if run:
        yield run


def process(path: Path, doc_id: str, meta: dict, ctx) -> dict:
    import pymupdf

    t0 = time.perf_counter()
    rec = {k: meta[k] for k in ("stage", "signature", "session", "code_sha256", "settings")}
    rec.update({"doc_id": doc_id, "started_at": utcnow(), "tier0_run": Path(meta["tier0_dir"]).name})
    st = path.stat()
    rec["file"] = {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(path)}
    reasons = []

    def finish(status):
        rec["status"] = status
        rec["reasons"] = sorted(set(reasons))
        rec["finished_at"] = utcnow()
        rec["seconds"] = r(time.perf_counter() - t0, 3)
        return rec

    t0_path = Path(meta["output"]) / doc_id / "convert" / Path(meta["tier0_dir"]).name / f"{doc_id}.tier0.json.gz"
    if not t0_path.exists():
        reasons.append("tier0_missing")
        return finish("failed")
    t0rec = read_json_gz(t0_path)
    if (t0rec.get("file") or {}).get("sha256") != rec["file"]["sha256"]:
        reasons.append("tier0_describes_other_bytes")
        return finish("failed")
    t0pages = {p["index"] + 1: p for p in t0rec.get("pages") or []}

    # ---- layout regions, a run of pages per call; pages tier 0 calls too heavy are not read
    heavy = {pno: why for pno, tp in t0pages.items() if (why := pl.over_budget(tp))}
    regions, calls = {}, []
    cap = ctx["capture"]
    for run in runs_of_pages([p for p in sorted(t0pages) if p not in heavy], SETTINGS["chunk_pages"]):
        cap.records.clear()
        cs = time.perf_counter()
        call = {"pages": [run[0], run[-1]]}
        try:
            res = ctx["converter"].convert(str(path), page_range=(run[0], run[-1]), raises_on_error=False)
            call["status"] = str(getattr(res.status, "value", res.status))
            for pg in res.pages:
                cl = pg.predictions.layout.clusters if pg.predictions and pg.predictions.layout else []
                regions[pg.page_no] = [{"label": str(getattr(c.label, "value", c.label)),
                                        "box": [r(c.bbox.l, 1), r(c.bbox.t, 1), r(c.bbox.r, 1), r(c.bbox.b, 1)],
                                        "conf": r(c.confidence, 3)} for c in cl]
            del res
        except Exception as e:
            call["status"] = "exception"
            call["error"] = repr(e)
        call["warnings"] = list(cap.records)
        call["seconds"] = r(time.perf_counter() - cs, 3)
        calls.append(call)
        pl.release_gpu_and_ram()
    rec["layout_calls"] = calls

    # ---- per page: signals from the text layer, then the kind
    pdf = pymupdf.open(path)
    pages = []
    for pno, tp in sorted(t0pages.items()):
        hints = tp.get("hints") or {}
        route = pl.route_for(hints.get("origin"), hints.get("text_layer"))
        reader = pl.ROUTES["reader"].get(route) if route in pl.ROUTES["converted_now"] else None
        ocr = reader == "ocr_full"         # the text layer, if any, is not the page's text
        page = pdf[pno - 1]
        words = [] if ocr else pl.page_words(page)
        lines = pl.text_lines(words) if words else []
        texts = [" ".join(w["text"] for w in sorted(ln["words"], key=lambda w: w["box"][0])) for ln in lines]
        regs = regions.get(pno, [])
        source = {"text": "text layer", "ocr_full": "ocr", "ocr_images": "text layer + ocr of images"}.get(reader, route)
        p = {"page": pno, "route": route, "source": source,
             "rotation": rotation(tp), "page_rotation": tp.get("rotation"), "words": len(words),
             "line_numbers": len(pl.line_numbers(words, page.rect.width)) if words else 0,
             "dot_lines": r(sum(1 for t in texts if DOTS.search(t)) / len(texts), 3) if texts else 0,
             "widgets": len((tp.get("annotations") or {}).get("widgets") or []),
             "regions": regs, "shares": {k: r(v, 3) for k, v in shares(regs).items()}}
        if pno in heavy:
            p["not_read"] = heavy[pno]
        elif pno not in regions:
            p["not_read"] = "the layout model returned no page"
            reasons.append("layout_page_missing")
        pages.append(p)

    with_text = [p for p in pages if p["words"] > SETTINGS["empty_max_words"]]
    numbered = sum(1 for p in with_text if p["line_numbers"])
    transcript_doc = bool(with_text) and numbered / len(with_text) >= SETTINGS["transcript_doc_share"]
    for p in pages:
        p["kind"] = p["layout_kind"] = page_kind(p, transcript_doc)
        p["kind_by"] = "layout"
    vlm_asked = second_opinion(pdf, pages)
    rec["pages"] = pages
    rec["document"] = {"numbered_pages": numbered, "pages_with_text": len(with_text),
                       "transcript": transcript_doc}
    if any(c["status"] not in ("success", "partial_success") for c in calls):
        reasons.append("layout_call_failed")
    rec["summary"] = {"pages": len(pages), "kinds": dict(collections.Counter(p["kind"] for p in pages)),
                      "ocr_pages": sum(1 for p in pages if p["source"] == "ocr"),
                      "rotated_pages": sum(1 for p in pages if p["rotation"]),
                      "regions": sum(len(p["regions"]) for p in pages), "not_read": len(heavy),
                      "vlm_asked": vlm_asked, "kind_by_vlm": sum(1 for p in pages if p["kind_by"] == "vlm")}
    if any((p.get("vlm") or {}).get("error") for p in pages):
        reasons.append("vlm_unavailable")
    missing = sum(1 for p in pages if p.get("not_read") and p["page"] not in heavy)
    return finish("failed" if pages and missing == len(pages) else "partial" if missing else "ok")


# --------------------------------------------------------------------------
# commands

def cmd_run(args) -> int:
    t0dir = pl.tier0_run(Path(args.runs), args.tier0)
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

    return run_stage(Pages(t0dir), args, filter_todo=ready)


def cmd_status(args) -> int:
    for run_dir in runs_of(Path(args.runs), STAGE):
        idx = load_index(run_dir)
        counts = collections.Counter(ev["status"] for ev in idx.values())
        kinds = collections.Counter()
        pages = ocr = 0
        for ev in idx.values():
            s = ev.get("summary") or {}
            kinds.update(s.get("kinds") or {})
            pages += s.get("pages", 0)
            ocr += s.get("ocr_pages", 0)
        print(f"run {run_dir.name}: {len(idx)} documents, {pages} pages ({ocr} for OCR)")
        print("  status  " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
        for k, v in kinds.most_common():
            print(f"  {v:8}  {100 * v / pages:5.1f}%  {k}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="find each page's regions and kind (default: every PDF in source/)")
    add_run_arguments(run)
    run.add_argument("--tier0", help="tier 0 run to read (default: the newest)")
    run.set_defaults(workers=1, timeout=1800)
    sub.add_parser("status", help="what every run holds")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
