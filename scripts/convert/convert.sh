#!/usr/bin/env bash
# Usage: ./scripts/convert/convert.sh <run|status|report|vote> [ids ...] [--sample FILE]
# Sets up the environment (torch, models' code; ~7 GB) in ~/.venvs/regdocs-convert on first
# use, and again when requirements.txt changes: on WSL a Linux-side venv loads far faster than
# one on /mnt. VENV=... puts it elsewhere.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv="${VENV:-$HOME/.venvs/regdocs-convert}"

if [[ ! -x "$venv/bin/python" || "$here/requirements.txt" -nt "$venv/.installed" ]]; then
    echo "Setting up $venv ..." >&2
    python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q --upgrade pip
    "$venv/bin/python" -m pip install -q -r "$here/requirements.txt"
    touch "$venv/.installed"
fi
if [[ "${1:-}" == vote ]]; then
    shift
    exec "$venv/bin/python" "$here/vote.py" "$@"
fi
exec "$venv/bin/python" "$here/convert.py" "$@"
