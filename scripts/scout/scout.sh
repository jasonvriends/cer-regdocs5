#!/usr/bin/env bash
# Usage: ./scripts/scout/scout.sh <command> [options]    (--help lists them)
#        ./scripts/scout/scout.sh test                   run the tests
# Sets up scripts/scout/.venv on first use, and again when requirements.txt
# changes; VENV=~/.venvs/scout puts it somewhere else.
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
venv="${VENV:-$here/.venv}"

if [[ ! -x "$venv/bin/python" || "$here/requirements.txt" -nt "$venv/.installed" ]]; then
    echo "Setting up $venv ..." >&2
    python3 -m venv "$venv"
    "$venv/bin/python" -m pip install -q --upgrade pip
    "$venv/bin/python" -m pip install -q -r "$here/requirements.txt"
    touch "$venv/.installed"
fi

if [[ "${1:-}" == test ]]; then
    shift
    exec "$venv/bin/python" -m unittest discover -s "$here/tests" "$@"
fi
exec "$venv/bin/python" "$here/scout.py" "$@"
