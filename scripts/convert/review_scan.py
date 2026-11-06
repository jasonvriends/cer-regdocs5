#!/usr/bin/env python3
"""Scan every converted document's Markdown and ledger for the faults a reader would see.

    ~/.venvs/regdocs-convert/bin/python scripts/convert/review_scan.py scan [--workers 8]
    ~/.venvs/regdocs-convert/bin/python scripts/convert/review_scan.py report

The ledger's checks count words; they cannot see a word in the wrong place or a mark that lost
its meaning. Each signal here is the signature of a fault found by reading documents
(runs/v2/review/PROGRESS.md), counted per document so it can be sized across the corpus and the
worst documents read next. `scan` writes one Parquet file per 5,000 documents to
runs/v2/review/scan/ and skips chunks already written, so it resumes after a stop; documents
converted since are picked up by running it again (they land in new chunks).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output"
SCAN = HERE / "runs" / "v2" / "review" / "scan"
CHUNK = 5000

LEAD = re.compile(r"^#+ (AND WHEREAS|WHEREAS|NOW THEREFORE|IN THE MATTER OF|BEFORE|IT IS ORDERED|ATTENDU QUE|"
                  r"ET ATTENDU QUE|DANS L.AFFAIRE D.|RELATIVEMENT( À)?|DEVANT|IL EST ORDONNÉ)\s*:?\s*$", re.I)
SCRIPTS = {   # characters no CER filing should hold as text; OCR forces drawn marks into them
    "cjk": r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]",
    "cyrillic": r"[Ѐ-ӿ]", "greek_word": r"\b[Ͱ-Ͽ]{3,}\b",
    "arabic_hebrew": r"[֐-ۿ]", "indic_thai": r"[ऀ-๿]",
    "private_use": r"[-]", "replacement": "�", "control": r"[\x00-\x08\x0b\x0c\x0e-\x1f]",
    "ligature": r"[ﬀ-ﬆ]",
}
SCRIPTS = {k: re.compile(v) for k, v in SCRIPTS.items()}
BULLET_GLYPH = re.compile(r"^- [•●▪■◦○➢►▶✓✔\-–]\s")
PICTURE = re.compile(r"^\*\[([a-z_]+)\]\*$")


def doc_ids() -> list[str]:
    return sorted(e.name for e in os.scandir(OUT) if e.is_dir() and e.name.isdigit()
                  and os.path.exists(f"{e.path}/convert/{e.name}.pages.json"))


def scan_one(d: str) -> dict | None:
    try:
        md = (OUT / d / "convert" / f"{d}.md").read_text(errors="replace")
        L = json.loads((OUT / d / "convert" / f"{d}.pages.json").read_text())
    except (OSError, ValueError):
        return None
    s = L.get("summary") or {}
    lines = [l.rstrip() for l in md.split("\n")]
    body = [l for l in lines if l.strip()]
    heads = [l for l in body if l.startswith("#")]
    r = {"doc_id": d, "pages": s.get("pages"), "words_evidence": s.get("words_evidence"),
         "pages_ocr": (s.get("pages_by_source") or {}).get("ocr", 0),
         "sections_source": (s.get("sections") or {}).get("source"),
         "code_commit": (L.get("code") or {}).get("commit"),
         "md_chars": len(md), "md_lines": len(body), "headings": len(heads),
         "long_headings": sum(len(h) > 150 for h in heads),
         "sentence_headings": sum(bool(re.match(r"^#+ \d+(\.\d+)*\.?\s+\S+(\s+\S+){12,}", h)) for h in heads),
         "list_items": sum(l.startswith("- ") for l in body),
         "list_number_in_bullet": sum(bool(re.match(r"^- (\d+|[ivxIVX]+)[.)] ", l)) for l in body),
         "list_letter_in_bullet": sum(bool(re.match(r"^- \(?[a-zA-Z]\) ", l)) for l in body),
         "doubled_bullets": sum(bool(BULLET_GLYPH.match(l)) for l in body),
         "page_markers": sum(l.startswith("<!-- page ") for l in lines),
         "table_rows": sum(l.startswith("|") for l in body)}
    for k, rx in SCRIPTS.items():
        r[k] = len(rx.findall(md))
    # lead-ins as headings, and whether text came before the first one (it belongs after)
    idx = [i for i, l in enumerate(body) if LEAD.match(l)]
    r["leadin_headings"] = len(idx)
    prev = [l for l in body[max(0, idx[0] - 4):idx[0]] if not l.startswith(("#", "*[", "<!--"))] if idx else []
    r["leadin_misordered"] = bool(prev and re.match(r"^[a-zà-ÿ(]", prev[-1]))
    r["leadin_stacked"] = sum(1 for a, b in zip(idx, idx[1:]) if b == a + 1)
    # pictures: by class, a run of the same placeholder, icons (check marks on forms)
    pics = collections.Counter(m.group(1) for l in body if (m := PICTURE.match(l)))
    r["pictures_md"] = sum(pics.values())
    r["icons"] = pics.get("icon", 0)
    r["logos"] = pics.get("logo", 0)
    r["picture_classes"] = json.dumps(dict(pics)) if pics else None
    r["repeated_placeholder"] = sum(1 for a, b in zip(body, body[1:]) if a == b and PICTURE.match(a))
    # text faults
    r["hyphen_space_breaks"] = len(re.findall(r"[a-z]{2,}- [a-z]{2,}", md))
    r["letter_spaced"] = len(re.findall(r"(?:\b[A-Za-z] ){6,}[A-Za-z]\b", md))
    r["glued_tokens"] = len(re.findall(r"[A-Za-z]{35,}", md))
    r["one_char_lines"] = sum(len(l.strip()) == 1 for l in body if not l.startswith(("|", "#")))
    r["footnote_glued"] = len(re.findall(r"[a-z]{3,}[.,;:]\d{1,3}(?=\s|$)", md))
    rep = collections.Counter(l for l in body if len(l) > 20 and not l.startswith(("<!--", "|", "*[")))
    r["repeated_lines_max"] = max(rep.values(), default=0)
    r["checkbox_glyphs"] = len(re.findall(r"[☐☑☒■□✓✔✗✘]", md))
    return r


def scan(args) -> int:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from concurrent.futures import ProcessPoolExecutor
    SCAN.mkdir(parents=True, exist_ok=True)
    ids = doc_ids()
    done = set()
    for f in SCAN.glob("chunk-*.parquet"):
        done.update(pq.read_table(f, columns=["doc_id"]).column("doc_id").to_pylist())
    todo = [d for d in ids if d not in done]
    n0 = len(list(SCAN.glob("chunk-*.parquet")))
    print(f"{len(ids)} converted, {len(done)} scanned, {len(todo)} to scan", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for c in range(0, len(todo), CHUNK):
            part = todo[c:c + CHUNK]
            rows = [r for r in ex.map(scan_one, part, chunksize=100) if r]
            out = SCAN / f"chunk-{n0 + c // CHUNK:04d}.parquet"
            pq.write_table(pa.Table.from_pylist(rows), out.with_suffix(".tmp"))
            os.replace(out.with_suffix(".tmp"), out)
            print(f"  {c + len(part)}/{len(todo)} -> {out.name}", flush=True)
    return 0


def report(args) -> int:
    import duckdb
    con = duckdb.connect()
    con.execute(f"CREATE VIEW s AS SELECT * FROM read_parquet('{SCAN}/chunk-*.parquet', union_by_name=true)")
    n = con.execute("SELECT count(*) FROM s").fetchone()[0]
    cols = [c for c, t in con.execute("SELECT column_name, column_type FROM (DESCRIBE s)").fetchall()
            if t in ("BIGINT", "BOOLEAN") and c not in ("pages", "words_evidence", "pages_ocr", "md_chars",
                                                         "md_lines", "page_markers", "headings", "list_items",
                                                         "pictures_md", "table_rows")]
    print(f"{n:,} documents scanned\n")
    print(f"{'signal':26} {'docs':>8} {'share':>7} {'total':>10}  worst")
    for c in cols:
        v = f"CAST({c} AS BIGINT)"
        k, t = con.execute(f"SELECT count(*) FILTER (WHERE {v} > 0), sum({v}) FROM s").fetchone()
        worst = con.execute(f"SELECT doc_id FROM s WHERE {v} > 0 ORDER BY {v} DESC LIMIT 3").fetchall()
        print(f"{c:26} {k:8,} {100 * k / n:6.1f}% {t or 0:10,}  {' '.join(w[0] for w in worst)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--workers", type=int, default=8)
    sub.add_parser("report")
    a = ap.parse_args()
    return scan(a) if a.cmd == "scan" else report(a)


if __name__ == "__main__":
    sys.exit(main())
