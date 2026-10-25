#!/bin/zsh
set -e
cd "${0:A:h}"
if python3 -c 'import cryptography, reportlab, pypdf, jsonschema' >/dev/null 2>&1; then
  exec python3 run.py
fi
if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import cryptography, reportlab, pypdf, jsonschema' >/dev/null 2>&1; then
  exec .venv/bin/python run.py
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
exec .venv/bin/python run.py
