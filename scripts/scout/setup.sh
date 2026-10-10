#!/usr/bin/env bash
# Usage: ./scripts/scout/setup.sh
#        VENV=~/.venvs/scout ./scripts/scout/setup.sh   # put the environment somewhere else
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
VENV="${VENV:-.venv}"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r scripts/scout/requirements.txt

echo "Scout ready: $VENV/bin/python scripts/scout/scout.py --help"
