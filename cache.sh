#!/usr/bin/env sh
# Cache manager: run with no arguments for a menu, or e.g.  ./cache.sh --mode update
set -e
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python   # prefer the project venv
exec "$PY" scripts/build_cache.py "$@"
