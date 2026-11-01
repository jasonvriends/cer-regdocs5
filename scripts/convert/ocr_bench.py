#!/usr/bin/env python3
"""Which RapidOCR models read best: measured on pages whose text layer is sound (the answer).

    ~/.venvs/regdocs-convert/bin/python scripts/convert/ocr_bench.py --pages 60 --sample FILE ...

Each configuration OCRs the same rendered pages; its words are compared with the text layer's
visible words at the same place (assemble.match_readings) and as a bag of tokens. Digital pages
are cleaner than scans, so this ranks the engines; it does not give a scan's error rate.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402
import readers as rd  # noqa: E402

CONFIGS = {
    "v6-small (local)": None,
    "v6-medium": ("PPOCRV6", "MEDIUM"),
    "v5-mobile": ("PPOCRV5", "MOBILE"),
    "v5-server": ("PPOCRV5", "SERVER"),
    "v4-mobile": ("PPOCRV4", "MOBILE"),
}


def engine(spec):
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
    t = EngineType.TORCH
    p = {"Det.engine_type": t, "Cls.engine_type": t, "Rec.engine_type": t,
         "EngineConfig.torch.use_cuda": True, "Global.log_level": "error"}
    if spec is None:
        M = rd.RAPIDOCR
        p.update({"Det.model_path": str(M / "PP-OCRv6_det_small.pth"), "Cls.model_path": str(M / "ch_ptocr_mobile_v2.0_cls_mobile.pth"),
                  "Rec.model_path": str(M / "PP-OCRv6_rec_small.pth"), "Rec.rec_keys_path": str(M / "ppocrv6_dict.txt")})
    else:
        v, size = OCRVersion[spec[0]], ModelType[spec[1]]
        p.update({"Det.ocr_version": v, "Rec.ocr_version": v, "Det.model_type": size, "Rec.model_type": size})
    return RapidOCR(params=p)


def ocr_words(eng, page) -> list[dict]:
    img, s = rd.render(page, rd.SETTINGS["ocr_dpi"])
    res = eng(img, return_word_box=True)
    out = []
    found = res.word_results if isinstance(res.word_results, (list, tuple)) else ()
    for line in found:
        if not isinstance(line, (list, tuple)):
            continue
        for item in line:
            if isinstance(item, (list, tuple)) and len(item) == 3 and str(item[0]).strip():
                xs, ys = [q[0] for q in item[2]], [q[1] for q in item[2]]
                out.append({"text": str(item[0]), "box": (min(xs) / s, min(ys) / s, max(xs) / s, max(ys) / s)})
    return out


def main() -> int:
    import pymupdf
    from convert import RUNS, SOURCE, VERSION, now, out_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--sample", action="append", required=True)
    args = ap.parse_args()
    ids = [l.strip() for f in args.sample for l in Path(f).read_text().split() if l.strip()]
    rng = random.Random(11)
    pool = []
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        if led.exists():
            pool += [(d, p["page"]) for p in json.loads(led.read_text())["pages"]
                     if p.get("source") == "pdf" and (p.get("words") or {}).get("pdf", 0) >= 40]
    pages = rng.sample(pool, min(args.pages, len(pool)))
    truth = {}
    for d, pno in pages:
        ws, _ = rd.pdf_words(pymupdf.open(SOURCE / f"{d}.pdf")[pno - 1])
        truth[(d, pno)] = [w for w in ws if not w["hidden"] and not w["redacted"]]
    print(f"{len(pages)} sound pages from {len({d for d, _ in pages})} documents")
    results = {}
    for name, spec in CONFIGS.items():
        try:
            eng = engine(spec)
        except Exception as e:
            print(f"  {name:18} could not load: {e!r}"[:200])
            continue
        placed = bag_right = bag_truth = made = 0
        tsum = 0.0
        for d, pno in pages:
            page = pymupdf.open(SOURCE / f"{d}.pdf")[pno - 1]
            t0 = time.perf_counter()
            ow = ocr_words(eng, page)
            tsum += time.perf_counter() - t0
            m = asm.match_readings(truth[(d, pno)], ow)
            placed += (m["pdf_found_by_ocr"] or 0) * m["pdf_words"]
            T = collections.Counter(asm.tokens(w["text"] for w in truth[(d, pno)]))
            R = collections.Counter(asm.tokens(w["text"] for w in ow))
            right = sum(min(n, R[t]) for t, n in T.items())
            bag_right += right
            bag_truth += sum(T.values())
            made += sum(R.values()) - right
        n_truth = sum(len(truth[k]) for k in truth)
        results[name] = {"words_at_place": round(placed / max(1, n_truth), 4), "tokens_right": round(bag_right / max(1, bag_truth), 4),
                         "tokens_extra_per_100": round(100 * made / max(1, bag_truth), 2), "s_per_page": round(tsum / len(pages), 2)}
        r = results[name]
        print(f"  {name:18} at place {r['words_at_place']:.1%}  tokens right {r['tokens_right']:.1%}  "
              f"extra {r['tokens_extra_per_100']}/100  {r['s_per_page']} s/page", flush=True)
    f = RUNS / VERSION / f"ocr-bench-{now().replace(':', '')}.json"
    f.write_text(json.dumps({"at": now(), "pages": pages, "results": results}, indent=1))
    print(f"written {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
