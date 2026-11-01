#!/usr/bin/env python3
"""Second opinions from vision models (Ollama), measured before they are trusted.

    ./scripts/convert/convert.sh vote bench [--models glm-ocr:latest,...] [--lines 150] --sample FILE
    ./scripts/convert/convert.sh vote run --model glm-ocr:latest --sample FILE

Vision models write text from pixels and can make it up: DeepSeek-OCR invented addresses and
emails, several wrote text on blank pages [C §5.6]. So they never supply a value; they vote, and
only after `bench` has measured them:

  bench   line crops from pages whose text layer is sound - the layer is the answer, exactly -
          plus blank crops. Per model: words and numbers read right, words made up, text on
          blanks, seconds per crop. Results in runs/<version>/vote-bench-<time>.json
  run     on pages tagged `ocr_vote` (our OCR and the scan's own OCR layer disagree), each line
          where the two readings part is cropped and read by the model; its reading is recorded
          as a vote for one reading, both, or neither, in output/<id>/convert/<id>.votes.parquet.
          Nothing in the evidence is changed: that is a later, versioned step.
"""
from __future__ import annotations

import base64
import collections
import html
import re
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assemble as asm  # noqa: E402

OLLAMA = "http://localhost:11434"
PROMPT = ("Transcribe the text in this image exactly as printed. Output only the text, on one line. "
          "If there is no text, output nothing.")
PROMPTS = {"glm-ocr": "Text Recognition:", "deepseek-ocr": "Free OCR."}   # the prompts they were trained on
TAGS = re.compile(r"</?(?:table|thead|tbody|tr|td|th|p|br|div|span|b|i|u|sup|sub|h[1-6])(?:\s[^<>]*)?/?>", re.I)
DEFAULT_MODELS = ["glm-ocr:latest", "richardyoung/olmocr2:7b-q8", "qwen3-vl:8b-instruct", "deepseek-ocr:3b",
                  "granite3.2-vision:2b"]
DPI = 200


def model_digest(name: str) -> str | None:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as r:
        return next((m["digest"] for m in json.load(r)["models"] if m["name"] == name), None)


def looping(text: str) -> bool:
    """A model stuck repeating itself: one token over and over, or a short phrase cycling."""
    t = text.split()
    if len(t) < 8:
        return False
    if max(collections.Counter(t).values()) >= max(6, len(t) // 2):
        return True
    return any(t[i:i + 3] == t[i + 3:i + 6] == t[i + 6:i + 9] for i in range(len(t) - 8))


def ask(model: str, png: bytes, max_tokens: int = 200, timeout=120) -> tuple[str, float]:
    """-> (the model's reading, seconds); a reading that loops comes back as LOOP."""
    prompt = next((v for k, v in PROMPTS.items() if model.startswith(k)), PROMPT)
    body = {"model": model, "prompt": prompt, "images": [base64.b64encode(png).decode()], "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens, "stop": ["\n"]},
            "keep_alive": "10m"}
    req = urllib.request.Request(f"{OLLAMA}/api/generate", json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # a crop is one printed line: the first line of the answer is the reading; models carry on
        # repeating it or adding Markdown and table markup after it (GLM-OCR, 2026-10-12)
        raw = json.load(r).get("response", "")
        # known HTML tags only (DeepSeek-OCR wraps lines in <table>): a loose <[^>]+> once ate
        # lab values written "< 0.0050" up to the next ">" [L The checker's own cost]
        raw = html.unescape(TAGS.sub(" ", raw))
        out = " ".join(next((ln for ln in raw.splitlines() if ln.strip()), "").split())
    return ("LOOP" if looping(out) else out), round(time.perf_counter() - t, 2)


def budget(box) -> int:
    """Tokens a line this wide can hold: about one per 3 pt of width, with room."""
    return int(min(300, 20 + (box[2] - box[0]) / 3))


def crop(pdf, page_no: int, box, pad=3.0) -> bytes:
    import pymupdf
    page = pdf[page_no - 1]
    r = pymupdf.Rect(box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad) & page.rect
    return page.get_pixmap(dpi=DPI, clip=r).tobytes("png")


def is_number(t: str) -> bool:
    return any(ch.isdigit() for ch in t)


def compare(truth: list[str], read: list[str]) -> dict:
    """Multiset agreement of tokens: right, missed, made up; numbers separately."""
    T, R = collections.Counter(truth), collections.Counter(read)
    right = sum(min(n, R[t]) for t, n in T.items())
    nums_t = collections.Counter({t: n for t, n in T.items() if is_number(t)})
    nums_r = sum(min(n, R[t]) for t, n in nums_t.items())
    return {"truth": sum(T.values()), "read": sum(R.values()), "right": right,
            "made_up": sum(R.values()) - right, "numbers": sum(nums_t.values()), "numbers_right": nums_r}


# --------------------------------------------------------------------------
# bench: the text layer is the answer

def line_crops(ids: list[str], n: int, seed=7) -> list[dict]:
    """Lines from sound-layer pages: their words are known exactly. Lines of 3+ words, some with numbers."""
    import pyarrow.parquet as pq
    from convert import OUT, out_dir
    rng = random.Random(seed)
    pool = []
    for d in ids:
        led = out_dir(d) / f"{d}.pages.json"
        wf = out_dir(d) / f"{d}.words.parquet"
        if not led.exists() or not wf.exists():
            continue
        sound = {p["page"] for p in json.loads(led.read_text())["pages"] if p.get("source") == "pdf"}
        words = [w for w in pq.read_table(wf).to_pylist() if w["reader"] == "pdf" and w["evidence"] and w["page"] in sound]
        by_page = collections.defaultdict(list)
        for w in words:
            by_page[w["page"]].append({**w, "box": (w["x0"], w["y0"], w["x1"], w["y1"])})
        for pno, ws in by_page.items():
            for ln in asm.lines_of(ws):
                if 3 <= len(ln) <= 14:
                    pool.append({"doc": d, "page": pno, "words": [w["text"] for w in ln],
                                 "box": (min(w["box"][0] for w in ln), min(w["box"][1] for w in ln),
                                         max(w["box"][2] for w in ln), max(w["box"][3] for w in ln))})
    numeric = [c for c in pool if any(is_number(w) for w in c["words"])]
    plain = [c for c in pool if c not in numeric]
    k = min(len(numeric), n // 2)
    picks = rng.sample(numeric, k) + rng.sample(plain, min(len(plain), n - k))
    return picks


def blank_crops(ids: list[str], n: int, seed=7) -> list[dict]:
    """Empty strips of pages: below the last word of a sound page, where nothing is printed."""
    import pyarrow.parquet as pq
    import pymupdf
    from convert import SOURCE, out_dir
    rng = random.Random(seed)
    out = []
    for d in rng.sample(ids, min(len(ids), n * 3)):
        wf = out_dir(d) / f"{d}.words.parquet"
        if not wf.exists():
            continue
        ws = pq.read_table(wf).to_pylist()
        if not ws:
            continue
        pdf = pymupdf.open(SOURCE / f"{d}.pdf")
        pno = ws[0]["page"]
        page = pdf[pno - 1]
        bottom = max((w["y1"] for w in ws if w["page"] == pno), default=0)
        top = bottom + 20
        if top + 30 < page.rect.height - 60 and not page.get_images():
            out.append({"doc": d, "page": pno, "words": [], "box": (72, top, page.rect.width - 72, top + 24)})
        if len(out) >= n:
            break
    return out


def cmd_bench(args) -> int:
    import pymupdf
    from convert import RUNS, SOURCE, VERSION, ids_from, now
    ids = ids_from(args)
    lines = line_crops(ids, args.lines)
    blanks = blank_crops(ids, max(10, args.lines // 10))
    models = args.models.split(",") if args.models else DEFAULT_MODELS
    print(f"bench: {len(lines)} line crops (text layer as the answer), {len(blanks)} blank crops, models {models}")
    pdfs = {}
    results = {}
    for m in models:
        tot = collections.Counter()
        blank_text, loops, secs, samples = 0, 0, [], []
        for c in lines + blanks:
            pdf = pdfs.setdefault(c["doc"], pymupdf.open(SOURCE / f"{c['doc']}.pdf"))
            try:
                text, s = ask(m, crop(pdf, c["page"], c["box"]), budget(c["box"]))
            except Exception as e:
                text, s = f"<error {e!r}>", 0
            secs.append(s)
            if text == "LOOP":
                loops += 1
                continue                    # an abstention, never a reading
            if not c["words"]:
                blank_text += bool(asm.tokens(text.split()))
                continue
            r = compare(asm.tokens(c["words"]), asm.tokens(text.split()))
            tot.update(r)
            if len(samples) < 8 and r["right"] < r["truth"]:
                samples.append({"truth": " ".join(c["words"]), "read": text, "doc": c["doc"], "page": c["page"]})
        res = {"model": m, "digest": model_digest(m), "lines": len(lines),
               "words_right": round(tot["right"] / max(1, tot["truth"]), 4),
               "numbers_right": round(tot["numbers_right"] / max(1, tot["numbers"]), 4),
               "made_up_per_100_read": round(100 * tot["made_up"] / max(1, tot["read"]), 2),
               "blanks_with_text": f"{blank_text}/{len(blanks)}", "loops": loops, "seconds_per_crop": round(sum(secs) / max(1, len(secs)), 2),
               "misreads": samples}
        results[m] = res
        print(f"  {m:30} words {res['words_right']:.1%}  numbers {res['numbers_right']:.1%}  "
              f"made up {res['made_up_per_100_read']}/100  blanks with text {res['blanks_with_text']}  loops {loops}  "
              f"{res['seconds_per_crop']} s/crop", flush=True)
    f = RUNS / VERSION / f"vote-bench-{now().replace(':', '')}.json"
    f.write_text(json.dumps({"at": now(), "samples": args.sample, "results": results}, indent=1, ensure_ascii=False))
    print(f"written {f}")
    return 0


# --------------------------------------------------------------------------
# run: votes where two OCR readings disagree

def disputed_lines(words: list[dict], page_no: int) -> list[dict]:
    """Lines on the page where our OCR and the scan's hidden layer read differently."""
    ours = [w for w in words if w["page"] == page_no and w["reader"] == "ocr" and w["evidence"]]
    hidden = [w for w in words if w["page"] == page_no and w["reader"] == "pdf" and w["hidden"]]
    out = []
    for ln in asm.lines_of(ours):
        box = (min(w["box"][0] for w in ln), min(w["box"][1] for w in ln),
               max(w["box"][2] for w in ln), max(w["box"][3] for w in ln))
        theirs = sorted((w for w in hidden if asm.inside(box, *asm.centre(w["box"]), 2.0)), key=lambda w: w["box"][0])
        a, b = asm.tokens(w["text"] for w in ln), asm.tokens(w["text"] for w in theirs)
        if a != b:
            out.append({"box": box, "ours": [w["id"] for w in ln], "theirs": [w["id"] for w in theirs],
                        "ours_text": " ".join(w["text"] for w in ln), "theirs_text": " ".join(w["text"] for w in theirs)})
    return out


def cmd_run(args) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pymupdf
    from convert import SOURCE, ids_from, now, out_dir
    digest = model_digest(args.model)
    if not digest:
        print(f"model {args.model} is not pulled in Ollama")
        return 2
    tally = collections.Counter()
    for d in ids_from(args):
        led = out_dir(d) / f"{d}.pages.json"
        if not led.exists():
            continue
        L = json.loads(led.read_text())
        # a vote is about these exact OCR words: it names the model and version that made them
        ocr_model, version = L.get("ocr_model"), L.get("version")
        pages = [p["page"] for p in L["pages"] if "ocr_vote" in (p.get("todo") or [])]
        if not pages:
            continue
        words = [{**w, "box": (w["x0"], w["y0"], w["x1"], w["y1"])} for w in pq.read_table(out_dir(d) / f"{d}.words.parquet").to_pylist()]
        pdf = pymupdf.open(SOURCE / f"{d}.pdf")
        rows = []
        for pno in pages:
            for ln in disputed_lines(words, pno):
                b = ln["box"]
                if b[2] - b[0] < 4 or b[3] - b[1] < 3:
                    continue                    # too small to show a model anything
                try:
                    text, s = ask(args.model, crop(pdf, pno, b), budget(b))
                except Exception as e:          # one crop the server cannot take is one abstention
                    text, s = "ERROR", 0
                    tally["error"] += 1
                v = [] if text in ("LOOP", "ERROR") else asm.tokens(text.split())
                c_ours = compare(asm.tokens(ln["ours_text"].split()), v)
                c_theirs = compare(asm.tokens(ln["theirs_text"].split()), v)
                fo = c_ours["right"] / max(1, c_ours["truth"])
                ft = c_theirs["right"] / max(1, c_theirs["truth"])
                vote = "abstain" if text in ("LOOP", "ERROR") else "ours" if fo > ft + 0.1 else "theirs" if ft > fo + 0.1 \
                    else "both" if fo >= 0.8 else "neither"
                tally[vote] += 1
                rows.append({"page": pno, "x0": ln["box"][0], "y0": ln["box"][1], "x1": ln["box"][2], "y1": ln["box"][3],
                             "ours": json.dumps(ln["ours"]), "theirs": json.dumps(ln["theirs"]),
                             "ours_text": ln["ours_text"], "theirs_text": ln["theirs_text"], "voter_text": text,
                             "voter": args.model, "voter_digest": digest, "vote": vote,
                             "ocr_model": ocr_model, "convert_version": version,
                             "ours_match": round(fo, 3), "theirs_match": round(ft, 3), "seconds": s, "at": now()})
        if rows:
            f = out_dir(d) / f"{d}.votes.parquet"
            # keep other voters' votes on these same OCR words; votes on another model's words are
            # stale. The schema is every row's keys: from_pylist takes the first row's alone, and
            # an older row without `ocr_model` dropped that field from every new vote (2026-10-12)
            old = pq.read_table(f).to_pylist() if f.exists() else []
            keep = [r for r in old if r["voter"] != args.model and r.get("ocr_model") == ocr_model
                    and r.get("convert_version") == version]
            allrows = keep + rows
            keys = list(dict.fromkeys(k for r in allrows for k in r))
            pq.write_table(pa.Table.from_pylist([{k: r.get(k) for k in keys} for r in allrows]), f, compression="zstd")
            print(f"  {d}: {len(rows)} disputed lines on {len(pages)} page(s)", flush=True)
    print("votes: " + ", ".join(f"{k} {v}" for k, v in tally.most_common()))
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--models")
    b.add_argument("--lines", type=int, default=150)
    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    for c in (b, r):
        c.add_argument("ids", nargs="*")
        c.add_argument("--sample", action="append")
    args = ap.parse_args(argv)
    return {"bench": cmd_bench, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
