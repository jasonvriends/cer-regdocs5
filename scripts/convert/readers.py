"""Readers: each turns a page, or a box on it, into words, boxes or grids, and says who it is.

Every reader here either copies what the file holds (MuPDF) or draws boxes (layout, tables,
figures); only OCR reads text from pixels, and it reports a confidence per word. None of them
writes text a page does not show. All boxes are PDF points, top-left origin, on the page as
displayed (rotation applied).
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import re
import time
import unicodedata
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models" / "docling"
LAYOUT_MODELS = {                       # name -> folder; all four share docling's 17 labels
    "heron": "docling-project--docling-layout-heron",
    "heron-101": "docling-project--docling-layout-heron-101",
    "egret-large": "docling-project--docling-layout-egret-large",
    "egret-xlarge": "docling-project--docling-layout-egret-xlarge",
}
FIGURES = "docling-project--DocumentFigureClassifier-v2.5"
TABLEFORMER = MODELS / "docling-project--docling-models" / "model_artifacts" / "tableformer"
RAPIDOCR = MODELS / "RapidOcr"

SETTINGS = {
    "layout_dpi": 72,            # what docling feeds its layout models (scale 1.0)
    "layout_threshold": 0.3,     # docling's default score threshold
    "ocr_dpi": 200,
    "table_dpi": 144,            # TableFormer's input scale (docling: 2.0)
    "figure_dpi": 144,
    "max_pixels": 40_000_000,    # pixel budget: a 182 x 34 in sheet at 200 dpi would be 250 MP [C §5.5]
    "redaction_dpi": 100,
    # PP-OCRv6 medium: 92.5% of a sound layer's words read at their place on 60 pages, against
    # 89.9% for small; small misread whole lines on some scans (154133) [L v1, OCR models]
    "ocr_model": "PP-OCRv6 medium",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def model_identity() -> dict:
    """Every model folder this converter loads: file count, bytes, and a hash of its weights."""
    out = {}
    dirs = [MODELS / d for d in (*LAYOUT_MODELS.values(), FIGURES)] + [TABLEFORMER, RAPIDOCR]
    for d in dirs:
        files = sorted(p for p in d.rglob("*") if p.is_file() and ".cache" not in p.parts
                       and p.suffix in (".safetensors", ".pth", ".onnx", ".json", ".txt"))
        h = hashlib.sha256()
        for f in files:
            h.update(f.name.encode())
            h.update(sha256_file(f).encode())
        out[d.name] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files),
                       "sha256": h.hexdigest()}
    return out


# --------------------------------------------------------------------------
# rendering

def scale_for(page, dpi: float) -> float:
    """dpi/72, lowered so the render stays within the pixel budget."""
    s = dpi / 72
    w, h = page.rect.width, page.rect.height
    return min(s, math.sqrt(SETTINGS["max_pixels"] / max(w * h, 1)))


def render(page, dpi: float, clip=None):
    """RGB array of the page as displayed, and the scale used."""
    import pymupdf
    s = scale_for(page, dpi)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(s, s), clip=clip, alpha=False, colorspace=pymupdf.csRGB)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3).copy(), s


# --------------------------------------------------------------------------
# the text layer (reader "pdf")

def char_class(ch: str) -> str:
    cp = ord(ch)
    if cp == 0xFFFD:
        return "replacement"
    cat = unicodedata.category(ch)
    if cat.startswith("L"):
        return "letter"
    if cat.startswith("N"):
        return "digit"
    if ch.isspace() or cat.startswith("Z"):
        return "space"
    if cat.startswith(("P", "S")):
        return "punct"
    return {"Co": "private_use", "Cn": "unassigned"}.get(cat, "control")


def direction(d) -> str:
    ang = math.degrees(math.atan2(-d[1], d[0])) % 360
    for t in (0, 90, 180, 270, 360):
        if abs(ang - t) <= 2:
            return str(t % 360)
    return "other"


def pdf_words(page) -> tuple[list[dict], dict]:
    """MuPDF's words with boxes, and what the text layer says about itself.

    A word is `hidden` when the span it sits in is invisible (render mode 3 or opacity 0: a
    scan's OCR layer, white-on-white) and `redacted` when a dark opaque fill covers it and its
    middle renders flat and dark [L OCR, redactions]."""
    import pymupdf
    m = page.rotation_matrix
    classes = {"visible": collections.Counter(), "invisible": collections.Counter()}
    dirs, fonts = collections.Counter(), collections.Counter()
    hidden_boxes = []
    for sp in page.get_texttrace():
        chars = sp.get("chars") or ()
        if not chars:
            continue
        inv = sp.get("type") == 3 or (sp.get("opacity") or 1) == 0
        for c in chars:
            if c[0] > 0:
                classes["invisible" if inv else "visible"][char_class(chr(c[0]))] += 1
        dirs[direction(sp["dir"])] += len(chars)
        fonts[sp.get("font") or "?"] += len(chars)
        if inv:
            hidden_boxes.append(pymupdf.Rect(sp["bbox"]) * m)

    words = []
    for x0, y0, x1, y1, text, block, line, wno in page.get_text("words"):
        r = pymupdf.Rect(x0, y0, x1, y1) * m
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        words.append({"text": text, "box": (r.x0, r.y0, r.x1, r.y1), "block": block, "line": line,
                      "conf": None, "hidden": any(hb.contains(pymupdf.Point(cx, cy)) for hb in hidden_boxes),
                      "redacted": False})
    for i in redacted_words(page, words):
        words[i]["redacted"] = True

    vis, inv = classes["visible"], classes["invisible"]
    allc = vis + inv
    n = sum(allc.values())
    bad = sum(allc[k] for k in ("replacement", "control", "private_use", "unassigned"))
    letters, digits = allc["letter"], allc["digit"]
    alnum = [w["text"] for w in words if any(ch.isalnum() for ch in w["text"])]
    real = sum(1 for w in alnum if sum(ch.isalpha() for ch in w) >= 3)
    health = {
        "chars_visible": sum(vis.values()), "chars_invisible": sum(inv.values()),
        "bad_char_share": round(bad / n, 4) if n else None,
        "letter_share": round(letters / (letters + digits), 4) if letters + digits else None,
        "single_char_share": round(sum(1 for w in alnum if len(w) == 1) / len(alnum), 4) if alnum else None,
        "real_words": real, "alnum_words": len(alnum),
        "directions": dict(dirs), "fonts": [f for f, _ in fonts.most_common(5)],
    }
    return words, health


def text_layer_verdict(h: dict) -> str:
    """sound / suspect / hidden_ocr / none, from the layer's own characters [C §5.2]."""
    vis, inv = h["chars_visible"], h["chars_invisible"]
    if vis + inv < 10:
        return "none"
    if inv >= 0.5 * (vis + inv):
        return "hidden_ocr"
    if (h["bad_char_share"] or 0) > 0.05:
        return "suspect"                      # unmapped glyphs (mojibake)
    ls = h["letter_share"]
    if ls is not None and ls < 0.3 and h["real_words"] < 5 and vis > 50:
        return "suspect"                      # numeric mojibake
    if h["alnum_words"] >= 50 and (h["single_char_share"] or 0) >= 0.7:
        return "suspect"                      # one-character words: Type3 codes, junk OCR
    return "sound"


def redacted_words(page, words: list[dict]) -> set[int]:
    """Words under a dark opaque fill whose middle renders flat and dark: blacked out on the
    page, still in the text layer. The render decides; drawing order and fills alone gave
    82,469 false hits on shaded cells [L OCR, redactions]."""
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
        r = pymupdf.Rect(dr["rect"]) * m
        if r.width >= 2 and r.height >= 2:
            fills.append(r)
    if not fills:
        return set()
    cand = [i for i, w in enumerate(words)
            if any(f.contains(pymupdf.Point((w["box"][0] + w["box"][2]) / 2, (w["box"][1] + w["box"][3]) / 2))
                   for f in fills)]
    if not cand:
        return set()
    dpi = SETTINGS["redaction_dpi"]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
    k = pix.w / page.rect.width
    out = set()
    for i in cand:
        x0, y0, x1, y1 = words[i]["box"]
        dx, dy = (x1 - x0) / 4, (y1 - y0) / 4
        cut = a[max(int((y0 + dy) * k), 0):int((y1 - dy) * k) + 1, max(int((x0 + dx) * k), 0):int((x1 - dx) * k) + 1]
        if cut.size and cut.mean() < 80 and cut.std() < 12:
            out.add(i)
    return out


def page_facts(page) -> dict:
    """What the page is, from the file alone: size, orientation, images, ruling lines, forms."""
    import pymupdf
    r = page.rect
    w_in, h_in = r.width / 72, r.height / 72
    long_in, short_in = max(w_in, h_in), min(w_in, h_in)
    if long_in > 17.5:
        size = "oversize"
    elif abs(short_in - 8.5) < 0.3 and abs(long_in - 11) < 0.4:
        size = "letter"
    elif abs(short_in - 8.27) < 0.3 and abs(long_in - 11.69) < 0.4:
        size = "a4"
    elif abs(short_in - 8.5) < 0.3 and abs(long_in - 14) < 0.4:
        size = "legal"
    elif abs(short_in - 11) < 0.4 and abs(long_in - 17) < 0.5:
        size = "tabloid"
    elif long_in < 8:
        size = "small"
    else:
        size = "other"
    area = max(r.width * r.height, 1)
    infos = page.get_image_info()
    largest = max(((i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]) for i in infos), default=0) / area
    hl = vl = 0
    paths = page.get_cdrawings()
    for p in paths:
        for it in p.get("items", ()):
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                if max(abs(x1 - x0), abs(y1 - y0)) >= 8:
                    hl += abs(y1 - y0) <= 0.5
                    vl += abs(x1 - x0) <= 0.5
            elif it[0] == "re":
                x0, y0, x1, y1 = it[1]
                ww, hh = abs(x1 - x0), abs(y1 - y0)
                hl += hh <= 2 and ww >= 8
                vl += ww <= 2 and hh >= 8
    widgets = [{"name": wd.field_name, "type": wd.field_type_string,
                "value": None if wd.field_value in (None, "") else str(wd.field_value)[:2000],
                "box": tuple(pymupdf.Rect(wd.rect) * page.rotation_matrix)} for wd in page.widgets()]
    annots = [{"type": a.type[1], "content": (a.info or {}).get("content") or None,
               "box": tuple(pymupdf.Rect(a.rect) * page.rotation_matrix)} for a in page.annots()]
    return {"width_pt": round(r.width, 1), "height_pt": round(r.height, 1), "size": size,
            "orientation": "landscape" if r.width > r.height * 1.05 else "portrait",
            "rotation": page.rotation, "images": len(infos), "largest_image_cover": round(largest, 3),
            "paths": len(paths), "hlines": hl, "vlines": vl, "ruled": hl >= 3 and vl >= 3,
            "widgets": widgets, "annotations": annots}


# --------------------------------------------------------------------------
# tool families: what made the PDF

_RULES = None


def tool_family(raw) -> dict | None:
    """A producer/creator string as family, kind and version (tool_families.json)."""
    global _RULES
    if raw is None or not str(raw).strip():
        return None
    if _RULES is None:
        spec = json.loads((HERE / "tool_families.json").read_text())
        _RULES = [(r["family"], r["kind"], re.compile(r["pattern"], re.I)) for r in spec["rules"]]
    raw = str(raw).replace("\x00", "").strip()
    for family, kind, rx in _RULES:
        m = rx.search(raw)
        if m:
            nums = [x for x in re.finditer(r"\d+(?:\.\d+)*", raw) if not raw[x.end():].startswith("-bit")]
            later = [x for x in nums if x.start() >= m.end()]
            pick = (later or nums or [None])[0]
            return {"raw": raw, "family": family, "kind": kind,
                    "version": None if kind == "scanner" or not pick else pick.group(0)}
    return {"raw": raw, "family": None, "kind": "unknown", "version": None}


# --------------------------------------------------------------------------
# the models

def ocr_engine(device: str = "cuda"):
    """RapidOCR with PP-OCRv6 medium, from models/ (hashed in the run identity)."""
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import EngineType
    t = EngineType.TORCH
    return RapidOCR(params={
        "Det.model_path": str(RAPIDOCR / "PP-OCRv6_det_medium.pth"),
        "Cls.model_path": str(RAPIDOCR / "ch_ptocr_mobile_v2.0_cls_mobile.pth"),
        "Rec.model_path": str(RAPIDOCR / "PP-OCRv6_rec_medium.pth"),
        "Rec.rec_keys_path": str(RAPIDOCR / "ppocrv6_dict.txt"),
        "Det.engine_type": t, "Cls.engine_type": t, "Rec.engine_type": t,
        "EngineConfig.torch.use_cuda": device == "cuda", "Global.log_level": "error"})


class OcrOnly:
    """Just the OCR reader, for checks that need no layout or table models (check.py)."""

    def __init__(self, device: str = "cuda"):
        self.ocr_engine = ocr_engine(device)


class Models:
    """Every model, loaded once per worker, on the GPU."""

    def __init__(self, device: str = "cuda"):
        import torch
        from transformers import (AutoImageProcessor, AutoModelForImageClassification,
                                  AutoModelForObjectDetection)
        self.torch, self.device = torch, device
        self.layout = {}
        for name, folder in LAYOUT_MODELS.items():
            d = MODELS / folder
            self.layout[name] = (AutoImageProcessor.from_pretrained(d),
                                 AutoModelForObjectDetection.from_pretrained(d).to(device).eval())
        d = MODELS / FIGURES
        self.fig_proc = AutoImageProcessor.from_pretrained(d)
        self.fig_model = AutoModelForImageClassification.from_pretrained(d).to(device).eval()

        import docling_ibm_models.tableformer.common as c
        from docling_ibm_models.tableformer.data_management.tf_predictor import TFPredictor
        self.tableformer = {}
        for mode in ("fast", "accurate"):
            cfg = c.read_config(str(TABLEFORMER / mode / "tm_config.json"))
            cfg["model"]["save_dir"] = str(TABLEFORMER / mode)
            self.tableformer[mode] = TFPredictor(cfg, device, 4)

        self.ocr_engine = ocr_engine(device)

    # ---- layout: every model on the same render
    def layout_all(self, page) -> dict[str, list[dict]]:
        from PIL import Image
        img, s = render(page, SETTINGS["layout_dpi"])
        pil = Image.fromarray(img)
        out = {}
        for name, (proc, model) in self.layout.items():
            t0 = time.perf_counter()
            with self.torch.inference_mode():
                inp = proc(images=[pil], return_tensors="pt").to(self.device)
                res = proc.post_process_object_detection(
                    model(**inp), target_sizes=self.torch.tensor([[pil.height, pil.width]]),
                    threshold=SETTINGS["layout_threshold"])[0]
            dets = []
            for sc, lb, bx in zip(res["scores"].tolist(), res["labels"].tolist(), res["boxes"].tolist()):
                x0, y0, x1, y1 = (v / s for v in bx)
                dets.append({"label": model.config.id2label[int(lb)], "conf": round(sc, 4),
                             "box": (max(x0, 0), max(y0, 0), min(x1, page.rect.width), min(y1, page.rect.height))})
            out[name] = {"detections": dets, "seconds": round(time.perf_counter() - t0, 3)}
        return out

    # ---- OCR: words with boxes and confidence
    def ocr(self, page, clip=None, rotate: int = 0, dpi: float | None = None) -> list[dict]:
        """OCR words with page boxes. `rotate` (90, 180, 270) turns the render counter-clockwise
        first, for text running up or down the page (labels on drawings [C §5.5]); the boxes are
        mapped back to the page as displayed."""
        img, s = render(page, dpi or SETTINGS["ocr_dpi"], clip=clip)
        ox, oy = (clip.x0, clip.y0) if clip is not None else (0, 0)
        h, w = img.shape[:2]
        k = (rotate // 90) % 4
        if k:
            img = np.ascontiguousarray(np.rot90(img, k))

        def back(x, y):
            # undo k counter-clockwise quarter turns of a w x h image
            for _ in range(k):
                ww = w if (k - _) % 2 == 1 else h      # width of the image before this turn
                x, y = ww - 1 - y, x
            return x, y
        res = self.ocr_engine(img, return_word_box=True)
        words = []
        found = res.word_results if isinstance(res.word_results, (list, tuple)) else ()   # a blank page: a float
        for line_no, line in enumerate(found):
            if not isinstance(line, (list, tuple)):
                continue
            for item in line:
                # an empty line comes back as (), and some lines carry a stray float
                if not isinstance(item, (list, tuple)) or len(item) != 3 or not str(item[0]).strip():
                    continue
                text, conf, quad = item
                pts = [back(p[0], p[1]) for p in quad]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                words.append({"text": str(text), "conf": round(float(conf), 4), "line": line_no, "block": None,
                              "rotate": rotate,
                              "box": (ox + min(xs) / s, oy + min(ys) / s, ox + max(xs) / s, oy + max(ys) / s),
                              "hidden": False, "redacted": False})
        return words

    # ---- figures
    def classify_figure(self, page, box) -> dict:
        import pymupdf
        from PIL import Image
        img, _ = render(page, SETTINGS["figure_dpi"], clip=pymupdf.Rect(box))
        with self.torch.inference_mode():
            inp = self.fig_proc(images=[Image.fromarray(img)], return_tensors="pt").to(self.device)
            probs = self.fig_model(**inp).logits.softmax(-1)[0]
        top = probs.topk(3)
        lab = self.fig_model.config.id2label
        return {"class": lab[int(top.indices[0])], "conf": round(float(top.values[0]), 4),
                "top3": [(lab[int(i)], round(float(v), 4)) for v, i in zip(top.values, top.indices)]}

    # ---- tables: TableFormer, fed our words as its tokens
    def tableformer_cells(self, mode: str, page_img: np.ndarray, scale: float, page, box, words: list[dict]) -> list[dict]:
        tokens = [{"id": i, "text": w["text"],
                   "bbox": {"l": w["box"][0] * scale, "t": w["box"][1] * scale, "r": w["box"][2] * scale,
                            "b": w["box"][3] * scale, "coord_origin": "TOPLEFT"}}
                  for i, w in enumerate(words) if w["text"].strip()]
        page_input = {"width": page.rect.width * scale, "height": page.rect.height * scale,
                      "image": page_img, "tokens": tokens}
        tbl = [round(box[0]) * scale, round(box[1]) * scale, round(box[2]) * scale, round(box[3]) * scale]
        out = self.tableformer[mode].multi_table_predict(page_input, [tbl], do_matching=True)[0]
        cells = []
        for el in out.get("tf_responses") or []:
            b = el.get("bbox") or {}
            if not b:
                continue
            cells.append({"r0": el["start_row_offset_idx"], "r1": el["end_row_offset_idx"],
                          "c0": el["start_col_offset_idx"], "c1": el["end_col_offset_idx"],
                          "box": (b["l"] / scale, b["t"] / scale, b["r"] / scale, b["b"] / scale),
                          "header": bool(el.get("column_header"))})
        return cells


OcrOnly.ocr = Models.ocr      # the same OCR method, without loading the other models


def barcodes(page, box, dpi: int = 300) -> list[dict]:
    """Barcodes and QR codes inside a box, decoded (zxing-cpp). A decoded value is exact, like
    a text layer: courier waybills carry the address and tracking number this way (619625)."""
    import pymupdf
    import zxingcpp
    from PIL import Image
    clip = pymupdf.Rect(box) & page.rect
    if clip.is_empty:
        return []
    img, s = render(page, dpi, clip=clip)
    out = []
    for r in zxingcpp.read_barcodes(Image.fromarray(img)):
        p = r.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        raw = r.text
        out.append({"text": " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split()) or raw,
                    "raw": raw, "format": str(r.format).split(".")[-1], "conf": None, "block": None, "line": None,
                    "box": (clip.x0 + min(xs) / s, clip.y0 + min(ys) / s, clip.x0 + max(xs) / s, clip.y0 + max(ys) / s),
                    "hidden": False, "redacted": False})
    return out


def ink_share(page, dpi: int = 50) -> float:
    """Share of the page that renders dark: a blank scanned page is all but white."""
    import pymupdf
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    a = np.frombuffer(pix.samples, np.uint8)
    return float((a < 128).mean()) if a.size else 0.0


def mupdf_table_cells(pdf_path: Path, page_no: int, box, strategy: str) -> list[dict]:
    """PyMuPDF's table finder in the box: "lines" (ruling lines) or "text" (alignment).
    It reads its own copy of the file: find_tables() changes the page it reads (3922740 p2's
    crop box became its media box) [L A fresh sample]."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        inv = ~page.rotation_matrix
        clip = pymupdf.Rect(box) * inv
        tabs = page.find_tables(clip=clip, strategy=strategy)
        best = max(tabs.tables, key=lambda t: t.row_count * t.col_count, default=None)
        if best is None:
            return []
        m = page.rotation_matrix
        xs, ys = set(), set()
        raw = []
        for ri, row in enumerate(best.rows):
            for ci, cell in enumerate(row.cells):
                if cell is None:
                    continue
                r = pymupdf.Rect(cell) * m
                raw.append((ri, ci, r))
        # spans: a cell box covering several row/column positions
        rows = sorted({round(r.y0, 1) for _, _, r in raw})
        cols = sorted({round(r.x0, 1) for _, _, r in raw})
        cells = []
        for ri, ci, r in raw:
            r1 = ri + max(1, sum(1 for y in rows if r.y0 - 0.5 <= y < r.y1 - 0.5))
            c1 = ci + max(1, sum(1 for x in cols if r.x0 - 0.5 <= x < r.x1 - 0.5))
            cells.append({"r0": ri, "r1": r1, "c0": ci, "c1": c1, "box": (r.x0, r.y0, r.x1, r.y1), "header": ri == 0})
        return cells
    finally:
        doc.close()
