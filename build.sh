#!/usr/bin/env sh
# Build on macOS or Linux. Target defaults to this machine's OS; override with --os.
#   ./build.sh                 # auto-detect macos/linux
#   ./build.sh --skip-cache
set -e
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python   # prefer the project venv
case "$(uname -s)" in
  Darwin) TARGET=macos ;;
  *)      TARGET=linux ;;
esac
case " $* " in *" --os "*) exec "$PY" build.py "$@" ;; esac
exec "$PY" build.py --os "$TARGET" "$@"
