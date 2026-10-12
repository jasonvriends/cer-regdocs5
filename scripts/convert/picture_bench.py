#!/usr/bin/env python3
"""Text inside drawings and maps: does OCR at several rotations, or a finer render, find more?

    ~/.venvs/regdocs-convert/bin/python scripts/convert/picture_bench.py --pictures 60 --sample FILE ...

Measured on digital drawings and maps (pictures the classifier calls engineering_drawing,
geographical_map or topographical_map on pages whose text layer is sound): the layer's words
inside the picture are the answer. Each setting OCRs the picture crop; its tokens are compared
with the answer's as a bag (labels run in every direction, so place is not compared).
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

DRAWINGS = ("engineering_drawing", "geographical_map", "topographical_map")
SETTINGS = {"0 @200": ((0,), 200), "0,90,270 @200": ((0, 90, 270), 200), "all four @200": ((0, 90, 180, 270), 200),
            "0 @300": ((0,), 300), "0,90,270 @300": ((0, 90, 270), 300)}


def main() -> int:
    import pymupdf
    import pyarrow.parquet as pq
    from convert import RUNS, SOURCE, VERSION, now, out_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--pictures", type=int, default=60)
    ap.add_argument("--sample", action="append", required=True)
    args = ap.parse_args()
    ids = [l.strip() for f in args.sample for l in Path(f).read_text().split() if l.strip()]
    pool = []
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        if not led.exists():
            continue
        for p in json.loads(led.read_text())["pages"]:
            if p.get("source") != "pdf":
                continue
            for pic in p.get("pictures") or []:
                if pic.get("class") in DRAWINGS and pic.get("words", 0) >= 8:
                    pool.append((d, p["page"], tuple(pic["box"]), pic["class"]))
    rng = random.Random(5)
    picks = rng.sample(pool, min(args.pictures, len(pool)))
    print(f"{len(picks)} digital drawings/maps with 8+ text-layer words (of {len(pool)})")
    models = rd.Models()
    tot = {k: collections.Counter() for k in SETTINGS}
    for d, pno, box, cls in picks:
        page = pymupdf.open(SOURCE / f"{d}.pdf")[pno - 1]
        ws, _ = rd.pdf_words(page)
        truth = collections.Counter(asm.tokens(w["text"] for w in ws if not w["hidden"]
                                               and asm.inside(box, *asm.centre(w["box"]), 1.0)))
        clip = pymupdf.Rect(box)
        for name, (rots, dpi) in SETTINGS.items():
            t0 = time.perf_counter()
            got = collections.Counter()
            for r in rots:   # union over rotations: a token counts once per occurrence found by any
                got |= collections.Counter(asm.tokens(w["text"] for w in models.ocr(page, clip, rotate=r, dpi=dpi)))
            right = sum(min(n, got[t]) for t, n in truth.items())
            tot[name].update({"truth": sum(truth.values()), "right": right,
                              "extra": sum(got.values()) - right, "ms": int(1000 * (time.perf_counter() - t0))})
    res = {}
    for name, c in tot.items():
        res[name] = {"found": round(c["right"] / max(1, c["truth"]), 4),
                     "extra_per_100": round(100 * c["extra"] / max(1, c["truth"]), 1),
                     "s_per_picture": round(c["ms"] / 1000 / max(1, len(picks)), 2)}
        print(f"  {name:16} found {res[name]['found']:.1%}  extra {res[name]['extra_per_100']}/100  "
              f"{res[name]['s_per_picture']} s/picture", flush=True)
    f = RUNS / VERSION / f"picture-bench-{now().replace(':', '')}.json"
    f.write_text(json.dumps({"at": now(), "pictures": picks, "results": res}, indent=1))
    print(f"written {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
