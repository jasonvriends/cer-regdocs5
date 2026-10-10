"""What the page stages share: reading the text layer, line numbers, routes, models and the GPU.

convert.py and pages.py both use these; each stage's run id hashes this file, so a change here
reruns both, and a change to one stage's own file reruns only that stage.
"""
import collections
import json
import logging
import re
import subprocess
from pathlib import Path

from common import HERE, clip, release_memory, runs_of, sha256_bytes, sha256_file

SETTINGS = {
    # A hearing transcript numbers its lines down the left margin (1-30 a page, often beside a
    # rule). TableFormer reads such a page as a two-column table and groups the numbers into
    # the wrong rows: 6,335 of the oldest 100 documents' 9,341 pages, half their errors. A
    # table holding those numbers is dropped and its words kept as one text item per line.
    "line_numbers": {"margin_frac": 0.15,   # the numbers end in the left 15% of the page
                     "max": 60,             # a page numbers at most this many lines
                     "min_lines": 15,       # this many numbers on the page, rising one by one
                     "min_rises": 0.8,      # ...this share of steps +1 (or back to 1: a new page of transcript)
                     "min_on_pitch": 0.9,   # ...and this share of +1 steps a whole number of line heights apart:
                                            # a transcript's lines sit on one pitch (72 tables in the oldest
                                            # 100, all >= 0.97); a table's numbered rows wrap to varying heights
                                            # (4656998 p39 work plan 0.38, 4647200 p432 sampling stations 0.42)
                     "min_in_table": 8},    # a table holding at least this many of them was the transcript
    # Step 3: pages tier 0 says are too heavy for docling skip it and take their text from
    # the text layer. docling's time limit does not stop a page mid-step: page 11 of 4681376
    # (30,642 paths) ran an hour. (4656998's 12 GB was not a page: see docling_source.) To be calibrated on the baseline; a page is heavy if it is oversized AND over
    # either limit.
    "structure_budget": {"long_side_pt": 1260, "max_paths": 10000, "max_words": 2500},
    "min_free_gpu_mb": 6000,
}

MODELS = HERE / "models" / "docling"
TATR_REPO = "microsoft/table-transformer-structure-recognition-v1.1-all"
TATR_DIR = HERE / "models" / "tatr" / TATR_REPO.replace("/", "--")

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


def _gpu_name():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None


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


def route_for(origin: str | None, layer: str | None) -> str:
    for (o, l), route in ROUTES["rules"]:
        if (o is None or o == origin) and (l is None or l == layer):
            return route
    return "undecided"


def release_gpu_and_ram():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    release_memory()


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


def line_numbers(words: list[dict], page_width: float) -> list[dict]:
    """A transcript's line numbers down the left margin, or [] if the page has none.
    Numbers on separate lines, nearly every step +1, or back to 1 where a new page of
    the transcript starts (90793: four transcript pages printed on each PDF page), and
    on one line pitch: a table's numbered rows are as tall as their wrapped cells."""
    s = SETTINGS["line_numbers"]
    nums = sorted((w for w in words if w["box"][2] < s["margin_frac"] * page_width
                   and w["text"].isdigit() and 1 <= int(w["text"]) <= s["max"]), key=lambda w: w["cy"])
    if len(nums) < s["min_lines"]:
        return []
    vals = [int(w["text"]) for w in nums]
    rises = sum(1 for a, b in zip(vals, vals[1:]) if b == a + 1 or (b == 1 and a > 1))
    if rises < s["min_rises"] * (len(vals) - 1):
        return []
    gaps = [b["cy"] - a["cy"] for a, b in zip(nums, nums[1:]) if int(b["text"]) == int(a["text"]) + 1]
    pitch = min(gaps, default=0)
    if pitch <= 0:
        return []
    on_pitch = sum(1 for d in gaps if abs(d / pitch - round(d / pitch)) <= 0.1)
    return nums if on_pitch >= s["min_on_pitch"] * len(gaps) else []
