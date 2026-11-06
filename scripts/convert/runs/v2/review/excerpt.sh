#!/usr/bin/env bash
# Print one page of a converted document's Markdown, for reading: excerpt.sh <doc_id> <page> [lines]
d=$1; p=$2; n=${3:-45}
f="$(dirname "$0")/../../../../../output/$d/convert/$d.md"
awk -v p="<!-- page $p -->" -v q="<!-- page $((p+1)) -->" '$0==p{on=1} $0==q{exit} on' "$f" | grep -v '^\s*$' | head -n "$n" | cut -c1-400
