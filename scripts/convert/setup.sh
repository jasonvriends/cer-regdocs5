#!/usr/bin/env bash
# Usage: ./scripts/convert/setup.sh
#        VENV=~/.venvs/convert ./scripts/convert/setup.sh   # put the 6.5 GB environment somewhere else
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
VENV="${VENV:-scripts/convert/.venv}"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r scripts/convert/requirements.txt
"$VENV/bin/python" scripts/convert/convert.py models

echo "Convert ready: $VENV/bin/python scripts/convert/tier0.py --help"
