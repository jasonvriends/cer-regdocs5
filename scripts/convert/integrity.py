#!/usr/bin/env python3
"""What each PDF says about its own integrity and structure: evidence for docs/filing-guidance.md.

    ~/.venvs/regdocs-convert/bin/python scripts/convert/integrity.py [--sample FILE] [--workers 12]

Opens every PDF in source/ once (the catalog and the file's tail, not its pages) and writes one row a
file to output/integrity.parquet (derived; delete and rebuild at any time):

  signatures        signature fields, how many are signed, and whether each signed one covers the
                    whole file (its ByteRange ends at the last byte); what was saved after the last
                    one: nothing, validation data only (long-term validation), or other changes
  certified         a certification signature (DocMDP /Perms): the author fixed what may change
  appended_updates  %%EOF markers in the last 1 MB beyond the one a file has (two for a linearized
                    file): saves appended after the file was first written (an approximation)
  encrypted, permissions, needs_password
  pdfa, tagged, outline, form_fields, embedded_files, javascript, pdf_version, bytes, pages
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE, OUT = ROOT / "source", ROOT / "output"
TAIL = 1 << 20     # the last 1 MB: 8 MB from the Windows drive made the pass I/O-bound


def inspect(doc_id: str) -> dict:
    import pymupdf
    path = SOURCE / f"{doc_id}.pdf"
    row = {"doc_id": doc_id, "bytes": None, "opened": False, "error": None}
    try:
        size = path.stat().st_size
        row["bytes"] = size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL))
            tail = f.read()
        pymupdf.TOOLS.mupdf_display_errors(False)
        doc = pymupdf.open(path)
    except Exception as e:
        row["error"] = repr(e)[:200]
        return row
    try:
        row.update(opened=True, pages=doc.page_count, encrypted=bool(doc.is_encrypted),
                   needs_password=bool(doc.needs_pass), permissions=int(doc.permissions),
                   pdf_version=(doc.metadata or {}).get("format"), linearized=bool(doc.is_fast_webaccess),
                   embedded_files=doc.embfile_count(), form_fields=int(bool(doc.is_form_pdf)),
                   outline=len(doc.get_toc(simple=True) or []))
        cat = doc.pdf_catalog()
        cat_obj = doc.xref_object(cat, compressed=True) if cat > 0 else ""
        names = doc.xref_get_key(cat, "Names") if cat > 0 else ("null", "null")
        names_obj = doc.xref_object(int(names[1].split()[0]), compressed=True) if names[0] == "xref" else names[1]
        row["javascript"] = "/JavaScript" in names_obj or "/JS" in cat_obj
        mark = doc.xref_get_key(cat, "MarkInfo") if cat > 0 else ("null", "null")
        row["tagged"] = "/StructTreeRoot" in cat_obj or "Marked true" in str(mark)
        xmp = doc.get_xml_metadata() or ""
        m = re.search(r"pdfaid:part(?:>|=\")\s*(\d)", xmp)
        c = re.search(r"pdfaid:conformance(?:>|=\")\s*([A-Za-z])", xmp)
        row["pdfa"] = (m.group(1) + (c.group(1).lower() if c else "")) if m else None
        row["certified"] = "/Perms" in cat_obj and "DocMDP" in (doc.xref_object(int(doc.xref_get_key(cat, "Perms")[1].split()[0]))
                                                                 if doc.xref_get_key(cat, "Perms")[0] == "xref" else cat_obj)
        eofs = tail.count(b"%%EOF")
        row["appended_updates"] = max(0, eofs - (2 if row["linearized"] else 1))
        # signature fields: only where the form says there may be some
        sig_fields = signed = covering = 0
        last_end = 0
        if doc.get_sigflags() > 0 or row["form_fields"]:
            seen = set()
            for page in doc:
                for w in page.widgets() or []:
                    if w.field_type != pymupdf.PDF_WIDGET_TYPE_SIGNATURE or w.xref in seen:
                        continue
                    seen.add(w.xref)
                    sig_fields += 1
                    v = doc.xref_get_key(w.xref, "V")
                    if v[0] != "xref":
                        continue
                    signed += 1
                    br = doc.xref_get_key(int(v[1].split()[0]), "ByteRange")
                    nums = [int(x) for x in re.findall(r"-?\d+", br[1])] if br[0] == "array" else []
                    if len(nums) == 4:
                        last_end = max(last_end, nums[2] + nums[3])
                        covering += nums[2] + nums[3] == size
        # what was saved after the last signature: nothing; only validation data (a Document Security
        # Store of certificates and revocation lists, added by e-signature services for long-term
        # validation - 3884214, DocuSign); or anything else, which changed the signed file
        after = "none"
        if signed and last_end < size:
            with open(path, "rb") as f:
                f.seek(last_end)
                added = f.read()
            # judged object by object: the catalog is rewritten to point at the store, and the
            # document info and metadata to a new date; any page, annotation, font or image is a change
            touches = (b"/Type/Page", b"/Type /Page", b"/Annots", b"/Subtype/Widget", b"/Subtype /Widget",
                       b"/Font", b"/XObject", b"/Subtype/Image", b"/Subtype /Image")
            dicts = re.findall(rb"\d+ 0 obj\s*(<<.*?)(?:stream|endobj)", added, re.S)
            changed = [d for d in dicts if not re.search(rb"/Type\s*/(Catalog|Metadata)", d)
                       and not re.search(rb"^<<\s*/(Producer|Creator|ModDate|CreationDate|Title|Author)", d)
                       and any(t in d for t in touches)]
            after = "validation_data" if (b"/DSS" in added or b"/VRI" in added) and not changed else "other_changes"
        row.update(signature_fields=sig_fields, signed=signed, signed_whole_file=covering, after_last_signature=after)
    except Exception as e:
        row["error"] = repr(e)[:200]
    finally:
        doc.close()
    return row


def main() -> int:
    import pyarrow as pa
    import pyarrow.compute  # noqa: F401
    import pyarrow.parquet as pq
    from concurrent.futures import ProcessPoolExecutor
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="append", help="only these ids (default: every PDF in source/)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=str(OUT / "integrity.parquet"))
    args = ap.parse_args()
    if args.sample:
        ids = list(dict.fromkeys(l.strip() for s in args.sample for l in Path(s).read_text().split() if l.strip()))
    else:
        ids = sorted((n[:-4] for n in os.listdir(SOURCE) if n.endswith(".pdf") and n[:-4].isdigit()), key=int)
    # results in parts of 5,000 beside the output, so a stopped pass resumes where it was
    parts = Path(args.out + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    done = set()
    for f in parts.glob("part-*.parquet"):
        done.update(pq.read_table(f, columns=["doc_id"]).column("doc_id").to_pylist())
    todo = [i for i in ids if i not in done]
    print(f"{len(ids):,} files, {len(done):,} already inspected, {len(todo):,} to go", flush=True)
    n_part = len(list(parts.glob("part-*.parquet")))
    buf = []

    def flush():
        nonlocal n_part, buf
        if buf:
            tmp = parts / f"part-{n_part:05d}.parquet.tmp"
            pq.write_table(pa.Table.from_pylist(buf), tmp, compression="zstd")
            os.replace(tmp, parts / f"part-{n_part:05d}.parquet")
            n_part, buf = n_part + 1, []

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for n, r in enumerate(ex.map(inspect, todo, chunksize=100), 1):
            buf.append(r)
            if len(buf) >= 5000:
                flush()
                print(f"  {len(done) + n:,}/{len(ids):,} files", flush=True)
    flush()
    tables = [pq.read_table(f) for f in sorted(parts.glob("part-*.parquet"))]
    table = pa.concat_tables(tables, promote_options="default") if tables else pa.table({})
    table = table.filter(pa.compute.is_in(table["doc_id"], value_set=pa.array(ids))) if args.sample else table
    tmp = Path(args.out + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, args.out)
    bad = sum(1 for e in table["error"].to_pylist() if e)
    print(f"{args.out}: {table.num_rows:,} files, {bad} with an error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
