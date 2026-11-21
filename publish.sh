#!/usr/bin/env sh
# Guided upload to GitHub. Add --check to run all checks without uploading.
set -e
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python   # prefer the project venv
exec "$PY" publish.py "$@"
