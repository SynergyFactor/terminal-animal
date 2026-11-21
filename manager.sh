#!/usr/bin/env sh
# Opens the Terminal Velocity Manager (GUI for cache, fallback data, build and tests).
set -e
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python   # prefer the project venv
exec "$PY" manager.py "$@"
