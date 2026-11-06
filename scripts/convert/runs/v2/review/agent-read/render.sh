#!/usr/bin/env bash
# Render one PDF page to PNG for checking a finding: render.sh <doc_id> <page> -> prints the PNG path
d=$1; p=$2; root="$(cd "$(dirname "$0")/../../../../../.." && pwd)"
out="${TMPDIR:-/tmp}/agent-read-$d-p$p.png"
~/.venvs/regdocs-convert/bin/python -I -c "
import pymupdf,sys; doc=pymupdf.open(sys.argv[1]); pg=doc[int(sys.argv[2])-1]
z=min(1.4, 1600/max(pg.rect.width,pg.rect.height)); pg.get_pixmap(matrix=pymupdf.Matrix(z,z)).save(sys.argv[3])" "$root/source/$d.pdf" "$p" "$out" 2>/dev/null && echo "$out"
