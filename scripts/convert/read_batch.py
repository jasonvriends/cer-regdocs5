#!/usr/bin/env python3
"""Have a model read every converted document's Markdown and report what a reader would see wrong.

    PY=~/.venvs/regdocs-convert/bin/python
    $PY scripts/convert/read_batch.py prepare --name pilot --sample FILE      # or --all; writes request shards
    $PY scripts/convert/read_batch.py estimate --name pilot --price-in 2.0 --price-out 10.0   # $/MTok at batch rates
    $PY scripts/convert/read_batch.py submit --name pilot --model MODEL_ID
    $PY scripts/convert/read_batch.py collect --name pilot                    # poll, download, parse; rerun freely
    $PY scripts/convert/read_batch.py report --name pilot

The ledger's checks count words; reading found faults no count shows (runs/v2/review/PROGRESS.md,
LESSONS.md "Reading the output of 150,000 documents"). Here a model does the reading: each request
is one chunk of a document's .md (whole pages, about CHUNK_CHARS), with the document's REGDOCS
context and each page's capture facts from the ledger, and returns findings as JSON (schema below).
The model sees text only, not the page: it reports what the text itself shows (order, structure,
characters, marks cut loose from their meaning), and says so when it suspects something only the
page could confirm.

Everything is kept under runs/v2/review/read/<name>/: shards/*.jsonl (the requests), state.json
(which shard went to which batch), results/*.jsonl (raw results), findings.parquet. Each step
skips what is already done, so a stop costs nothing; `collect` resubmits nothing by itself.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "output"
READ = HERE / "runs" / "v2" / "review" / "read"
CHUNK_CHARS = 60_000          # ~15K tokens: whole pages, so a fault is seen with its context
SHARD_REQUESTS = 20_000       # requests per shard file; a batch takes one shard (limit 100,000 / 256 MB)
CHARS_PER_TOKEN = 3.6         # measured below on a sample with count_tokens before a big run

SYSTEM = """You review the output of a PDF-to-Markdown converter for Canada Energy Regulator (CER/NEB) filings: letters, orders, applications, evidence, forms, tables, maps, transcripts, in English and French.

You get one chunk of one document's Markdown. `<!-- page N -->` marks where page N starts. `*[class]*` is a picture the converter found (logo, icon, signature, geographical_map, ...). Lines `| ... |` are tables. You do not see the PDF page; judge only what the text shows, and say so in the explanation when a finding is a suspicion only the page could settle.

The converter keeps every word; what goes wrong is where words go and what marks mean. Known faults, with their kind:
- leadin_order: an order's or permit's lead-ins (IN THE MATTER OF, BEFORE, WHEREAS, AND WHEREAS, IT IS ORDERED; RELATIVEMENT À, DEVANT, ATTENDU QUE) written as headings after the text they introduce, stacked, or cutting a sentence.
- form_answer_lost: a form's options printed with no sign of which was chosen (a loose *[icon]* nearby, "Yes No" with no mark), or a value separated from its label.
- paragraph_as_heading: a numbered paragraph or sentence written as a heading (##, ###, ####), often with its continuation as a "- " list item.
- word_order: words or lines out of order inside a sentence, a line or a table cell; two columns or two lines interleaved; a word moved to the end of a block.
- table_broken: a table whose cells do not hold its values (a whole column packed in one cell, rows written as plain lines after a header-only table, values shifted into the wrong column, a numbered column missing).
- row_numbers_removed: a line like `*Lines 147 148 ...*` that holds a table's or list's own numbers, not a transcript's margin.
- wrong_characters: text that is not what the page can say: CJK or other scripts in an English/French text (e.g. 而 for a logo, 口 for a symbol), Greek/Cyrillic/Arabic digits in numbers, private-use or replacement characters, Wingdings letters (þ ¨) standing for check boxes.
- broken_words: words split by spaces (letter-spaced "F I L I N G", "RECL AMAT ION") or joined; hyphenated line breaks kept ("navi- gation").
- page_misplaced: a page whose content is clearly missing under its own marker (only a header and footer) while its text appears elsewhere.
- ocr_noise: OCR garbage in the body (handwriting or stamps read as text, junk tokens), or OCR errors in what should be clean text.
- list_markup: numbered items written as "- 1.", doubled bullets "- •".
- other: anything else that would mislead a reader, a search or a quotation.

Report each distinct fault once per chunk with its kind, the page, how much it matters, and a short exact quote from the chunk that shows it. Severity: "meaning" when a reader would be misled about what the document says (wrong order of reasons, lost answer, wrong number, word in the wrong sentence); "structure" when headings, lists or tables are wrong but the sense survives; "noise" when it only clutters. Do not report a page header, footer or letterhead being present, maps and drawings being lists of labels, or empty form fields, which are expected. If a fault is not in the list, use "other" and describe it so it can become a new kind. If the chunk reads correctly, return no findings. Be precise: a finding someone will check against the PDF."""

KINDS = ["leadin_order", "form_answer_lost", "paragraph_as_heading", "word_order", "table_broken",
         "row_numbers_removed", "wrong_characters", "broken_words", "page_misplaced", "ocr_noise",
         "list_markup", "other"]
SCHEMA = {
    "type": "object",
    "properties": {
        "reads_correctly": {"type": "boolean"},
        "findings": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": KINDS},
                "page": {"type": "integer"},
                "severity": {"type": "string", "enum": ["meaning", "structure", "noise"]},
                "quote": {"type": "string"},
                "explanation": {"type": "string"},
            },
            "required": ["kind", "page", "severity", "quote", "explanation"],
            "additionalProperties": False}},
        "note": {"type": "string"},
    },
    "required": ["reads_correctly", "findings", "note"],
    "additionalProperties": False,
}


def cer(d: str) -> dict:
    f = OUT / d / f"{d}.cer.meta.json.gz"
    try:
        return json.loads(gzip.decompress(f.read_bytes())) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def chunks(md: str) -> list[tuple[int, int, str]]:
    """Whole pages, packed up to CHUNK_CHARS; a single longer page is cut at line breaks."""
    parts = re.split(r"(?=<!-- page \d+ -->)", md)
    pages = [(int(m.group(1)) if (m := re.match(r"<!-- page (\d+) -->", p)) else 0, p) for p in parts if p.strip()]
    out, cur, first, last = [], "", None, None
    for n, text in pages:
        while len(text) > CHUNK_CHARS:                     # one huge page: cut it
            cut = text.rfind("\n", 0, CHUNK_CHARS) or CHUNK_CHARS
            if cur:
                out.append((first, last, cur)); cur = ""
            out.append((n, n, text[:cut])); text = f"<!-- page {n} (continued) -->\n" + text[cut:]
        if cur and len(cur) + len(text) > CHUNK_CHARS:
            out.append((first, last, cur)); cur = ""
        if not cur:
            first = n
        cur += text; last = n
    if cur:
        out.append((first, last, cur))
    return out


def page_facts(d: str, first: int, last: int) -> str:
    try:
        L = json.loads((OUT / d / "convert" / f"{d}.pages.json").read_text())
    except (OSError, ValueError):
        return ""
    rows = []
    for p in L["pages"]:
        if first <= p["page"] <= last:
            w = p.get("words") or {}
            rows.append(f"p{p['page']}: text from {p.get('source')}, layer {p.get('text_layer')}, "
                        f"{w.get('evidence', 0)} words, {len(p.get('tables') or [])} tables, "
                        f"{len(p.get('pictures') or [])} pictures")
    return "\n".join(rows[:400])


def request_for(d: str, i: int, n: int, first: int, last: int, text: str, meta: dict) -> dict:
    r = meta
    facets = r.get("facets") or {}
    head = (f"Document {d}: {r.get('title') or '?'}\nDate {r.get('date') or '?'}; type "
            f"{', '.join(facets.get('Document Type') or []) or '?'}; company {r.get('company') or '?'}; submitter {r.get('submitter') or '?'}\n"
            f"Chunk {i + 1} of {n}, pages {first}-{last}.\nPage facts from the converter's ledger:\n{page_facts(d, first, last)}\n\n"
            f"Markdown:\n{text}")
    return {"custom_id": f"{d}-{i:04d}", "doc_id": d, "chunk": i, "pages": [first, last], "user": head}


def ids_for(args) -> list[str]:
    if args.sample:
        lines = [l.split("\t")[0].strip() for s in args.sample for l in Path(s).read_text().splitlines()]
        return list(dict.fromkeys(l for l in lines if l.isdigit()))
    return sorted(e.name for e in os.scandir(OUT) if e.is_dir() and e.name.isdigit()
                  and os.path.exists(f"{e.path}/convert/{e.name}.md"))


def prepare(args) -> int:
    base = READ / args.name
    (base / "shards").mkdir(parents=True, exist_ok=True)
    done = set()
    for f in (base / "shards").glob("*.jsonl"):
        for l in f.open():
            done.add(json.loads(l)["doc_id"])
    ids = [d for d in ids_for(args) if d not in done]
    n0 = len(list((base / "shards").glob("*.jsonl")))
    buf, shard, chars = [], n0, 0
    for k, d in enumerate(ids, 1):
        try:
            md = (OUT / d / "convert" / f"{d}.md").read_text(errors="replace")
        except OSError:
            continue
        cs = chunks(md)
        try:
            meta = json.loads((OUT / d / "convert" / f"{d}.pages.json").read_text())["context"]["regdocs"]
        except (OSError, ValueError, KeyError):
            meta = {}
        for i, (a, b, t) in enumerate(cs):
            req = request_for(d, i, len(cs), a, b, t, meta)
            buf.append(req); chars += len(req["user"])
            if len(buf) >= SHARD_REQUESTS:
                write_shard(base, shard, buf); shard += 1; buf = []
        if k % 10000 == 0:
            print(f"  {k}/{len(ids)} documents", flush=True)
    if buf:
        write_shard(base, shard, buf)
    print(f"{len(ids)} documents prepared, {chars / 1e6:.1f} M characters of requests in {args.name}")
    return 0


def write_shard(base: Path, n: int, rows: list[dict]) -> None:
    f = base / "shards" / f"shard-{n:04d}.jsonl"
    with open(f.with_suffix(".tmp"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(f.with_suffix(".tmp"), f)


def params(model: str, effort: str, user: str) -> dict:
    return {"model": model, "max_tokens": 8000,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}}}


def estimate(args) -> int:
    base = READ / args.name
    reqs = chars = 0
    for f in sorted((base / "shards").glob("*.jsonl")):
        for l in f.open():
            reqs += 1; chars += len(json.loads(l)["user"])
    tin = chars / CHARS_PER_TOKEN + reqs * 1200          # chunk + system prompt (cached after the first)
    tout = reqs * args.out_tokens
    print(f"{reqs:,} requests, ~{tin / 1e6:,.0f} M input tokens, ~{tout / 1e6:,.0f} M output tokens (assumed {args.out_tokens}/request)")
    print(f"  ~${tin / 1e6 * args.price_in + tout / 1e6 * args.price_out:,.0f} at ${args.price_in}/${args.price_out} per MTok")
    return 0


def client():
    import anthropic
    return anthropic.Anthropic()


def load_state(base: Path) -> dict:
    f = base / "state.json"
    return json.loads(f.read_text()) if f.exists() else {"batches": {}}


def save_state(base: Path, st: dict) -> None:
    (base / "state.json.tmp").write_text(json.dumps(st, indent=1))
    os.replace(base / "state.json.tmp", base / "state.json")


def submit(args) -> int:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    base = READ / args.name
    st = load_state(base)
    c = client()
    for f in sorted((base / "shards").glob("*.jsonl")):
        if f.name in st["batches"]:
            continue
        rows = [json.loads(l) for l in f.open()]
        b = c.messages.batches.create(requests=[
            Request(custom_id=r["custom_id"], params=MessageCreateParamsNonStreaming(**params(args.model, args.effort, r["user"])))
            for r in rows])
        st["batches"][f.name] = {"id": b.id, "model": args.model, "effort": args.effort, "requests": len(rows),
                                 "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"), "collected": False}
        save_state(base, st)
        print(f"{f.name}: batch {b.id}, {len(rows)} requests", flush=True)
        if args.one:
            break
    return 0


def collect(args) -> int:
    base = READ / args.name
    st = load_state(base)
    c = client()
    (base / "results").mkdir(exist_ok=True)
    for shard, b in st["batches"].items():
        if b["collected"]:
            continue
        info = c.messages.batches.retrieve(b["id"])
        rc = info.request_counts
        print(f"{shard} {b['id']}: {info.processing_status} ok {rc.succeeded} err {rc.errored} "
              f"expired {rc.expired} processing {rc.processing}", flush=True)
        if info.processing_status != "ended":
            continue
        out = base / "results" / shard
        with open(out.with_suffix(".tmp"), "w") as fh:
            for r in c.messages.batches.results(b["id"]):
                rec = {"custom_id": r.custom_id, "type": r.result.type}
                if r.result.type == "succeeded":
                    m = r.result.message
                    rec["stop_reason"] = m.stop_reason
                    rec["text"] = next((x.text for x in m.content if x.type == "text"), "")
                    rec["usage"] = m.usage.model_dump()
                else:
                    rec["error"] = str(getattr(r.result, "error", ""))
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(out.with_suffix(".tmp"), out)
        b["collected"] = True
        save_state(base, st)
    parse(base)
    return 0


def parse(base: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    rows, chunks_, usage = [], [], {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    for f in sorted((base / "results").glob("*.jsonl")):
        for l in f.open():
            r = json.loads(l)
            d, ch = r["custom_id"].rsplit("-", 1)
            ok = r["type"] == "succeeded" and r.get("stop_reason") != "refusal"
            data = {}
            if ok:
                try:
                    data = json.loads(r["text"])
                except ValueError:
                    ok = False
                for k in usage:
                    usage[k] += (r.get("usage") or {}).get(k) or 0
            chunks_.append({"doc_id": d, "chunk": int(ch), "ok": ok, "type": r["type"],
                            "stop_reason": r.get("stop_reason"), "reads_correctly": data.get("reads_correctly"),
                            "findings": len(data.get("findings") or []), "note": data.get("note")})
            for x in data.get("findings") or []:
                rows.append({"doc_id": d, "chunk": int(ch), **x})
    if chunks_:
        pq.write_table(pa.Table.from_pylist(chunks_), base / "chunks.parquet")
    if rows:
        pq.write_table(pa.Table.from_pylist(rows), base / "findings.parquet")
    (base / "usage.json").write_text(json.dumps(usage, indent=1))
    print(f"parsed {len(chunks_)} chunks, {len(rows)} findings; usage {usage}")


def report(args) -> int:
    import duckdb
    base = READ / args.name
    c = duckdb.connect()
    c.execute(f"CREATE VIEW f AS SELECT * FROM '{base}/findings.parquet'")
    c.execute(f"CREATE VIEW k AS SELECT * FROM '{base}/chunks.parquet'")
    print(c.sql("SELECT count(DISTINCT doc_id) docs, count(*) chunks, count(*) FILTER (WHERE NOT ok) failed, "
                "count(*) FILTER (WHERE reads_correctly) reads_correctly FROM k").df().to_string())
    print(c.sql("SELECT kind, severity, count(*) findings, count(DISTINCT doc_id) docs FROM f GROUP BY ALL ORDER BY 1, 3 DESC").df().to_string())
    print(c.sql("SELECT doc_id, page, left(quote, 90) quote, left(explanation, 160) why FROM f WHERE kind='other' LIMIT 40").df().to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "estimate", "submit", "collect", "report"):
        s = sub.add_parser(name)
        s.add_argument("--name", required=True, help="the run: a folder under runs/v2/review/read/")
        if name == "prepare":
            s.add_argument("--sample", action="append", help="a file of ids (first column), else every document")
        if name in ("submit", "estimate"):
            s.add_argument("--model", required=(name == "submit"), help="the model id to read with")
            s.add_argument("--price-in", type=float, default=0.0, help="estimate: $/MTok input at batch rates")
            s.add_argument("--price-out", type=float, default=0.0, help="estimate: $/MTok output at batch rates")
            s.add_argument("--effort", default="low")
            s.add_argument("--out-tokens", type=int, default=900, help="estimate: output tokens per request")
            s.add_argument("--one", action="store_true", help="submit one shard only")
    a = ap.parse_args()
    return {"prepare": prepare, "estimate": estimate, "submit": submit, "collect": collect, "report": report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
