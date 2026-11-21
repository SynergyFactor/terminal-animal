"""Build a standalone Terminal Velocity executable with PyInstaller.

    python build.py                    # Windows (default) -> dist/TerminalVelocity.exe
    python build.py --os macos         # macOS            -> dist/TerminalVelocity.app
    python build.py --os linux         # Linux            -> dist/TerminalVelocity

Options:
    --cache MODE       what to do with the bundled offline cache before building:
                         ask      interactive menu (default when run in a terminal)
                         missing  add animals not cached yet (default in CI/non-interactive)
                         stale    re-scrape entries from an older parser version
                         update   re-scrape everything in place
                         rebuild  delete and rebuild from scratch
                         skip     leave the cache as is
    --delay SECONDS    spacing between Wikipedia requests while scraping (default 1.0)
    --fallback MODE    fallback trait databases: auto (download/build if missing; default),
                       refresh (re-download and rebuild), skip
    --skip-cache       same as --cache skip;  --refresh-cache  same as --cache rebuild
    --no-install       don't pip-install requirements first
    --onedir           folder build instead of a single file (starts faster)

PyInstaller cannot cross-compile: each --os target must be built on that OS
(or use the included GitHub Actions workflow to build all three).
"""
from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "TerminalVelocity"
HOST = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "unknown")

# Qt modules the app never uses — excluding them keeps the bundle small.
EXCLUDES = [
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtSvg",
    "PySide6.QtWebEngineCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.Qt3DCore",
    "tkinter", "unittest", "pydoc", "matplotlib", "numpy",
]


# Only these data files ship inside the app (not backups, temp files or raw datasets).
BUNDLED_DATA = ["animals.json", "traits.json.gz", "overrides.csv"]


def run(cmd: list[str]) -> None:
    """Run a command without a shell (arguments are passed as a list, never interpolated)."""
    print("\n$", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def positive_float(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not 0.2 <= v <= 60:
        raise argparse.ArgumentTypeError("must be between 0.2 and 60 seconds")
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--os", choices=["windows", "macos", "linux"], default="windows",
                    help="target platform (default: windows)")
    ap.add_argument("--cache", choices=["ask", "missing", "stale", "update", "rebuild", "skip"])
    ap.add_argument("--delay", type=positive_float, default=1.0)
    ap.add_argument("--fallback", choices=["auto", "refresh", "skip"], default="auto")
    ap.add_argument("--skip-cache", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--refresh-cache", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-install", action="store_true")
    ap.add_argument("--onedir", action="store_true")
    args = ap.parse_args()

    if HOST == "unknown":
        print(f"error: unsupported build platform '{platform.system()}'.", file=sys.stderr)
        return 2
    if args.os != HOST:
        print(f"error: target '{args.os}' requested but this machine is '{HOST}'.\n"
              f"PyInstaller can't cross-compile. Build on a {args.os} machine, build for this one with\n"
              f"    python build.py --os {HOST}\n"
              "or push to GitHub and let .github/workflows/build.yml build every OS.",
              file=sys.stderr)
        return 2

    try:
        return build(args)
    except subprocess.CalledProcessError as e:
        print(f"\nerror: step failed (exit code {e.returncode}): {' '.join(map(str, e.cmd))}",
              file=sys.stderr)
        return e.returncode or 1
    except KeyboardInterrupt:
        print("\nBuild cancelled.", file=sys.stderr)
        return 130


def build(args) -> int:
    py = sys.executable
    if sys.version_info < (3, 10):
        print("error: Python 3.10 or newer is required.", file=sys.stderr)
        return 2
    if not args.no_install:
        run([py, "-m", "pip", "install", "-r", "requirements.txt"])

    index = ROOT / "data" / "traits.json.gz"
    if args.fallback == "refresh" or (args.fallback == "auto" and not index.exists()):
        try:
            run([py, "scripts/fallback_data.py"] + (["--download"] if args.fallback == "refresh" else []))
        except subprocess.CalledProcessError:
            print("warning: fallback databases unavailable; gaps will rely on Wikidata/overrides.")

    cache = ROOT / "data" / "animals.json"
    mode = args.cache or ("skip" if args.skip_cache else "rebuild" if args.refresh_cache else
                          "ask" if sys.stdin.isatty() else "missing")
    if mode != "skip":
        cmd = [py, "scripts/build_cache.py", "--delay", str(args.delay)]
        if mode != "ask":
            cmd += ["--mode", mode, "--yes"]
        try:
            run(cmd)
        except subprocess.CalledProcessError:
            print("warning: cache scrape failed; app will rely on live Wikipedia lookups.")
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        cache.write_text("{}", "utf-8")

    for d in ("build", "dist"):
        shutil.rmtree(ROOT / d, ignore_errors=True)

    sep = ";" if args.os == "windows" else ":"
    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", NAME]
    for name in BUNDLED_DATA:
        if (ROOT / "data" / name).exists():
            cmd += ["--add-data", f"data/{name}{sep}data"]
        else:
            print(f"note: data/{name} not found; building without it")
    # macOS: --windowed + onedir produces a proper .app bundle (onefile .app is deprecated).
    if args.onedir or args.os == "macos":
        cmd.append("--onedir")
    else:
        cmd.append("--onefile")
    if args.os == "macos":
        cmd += ["--osx-bundle-identifier", "io.github.terminalvelocity"]
    icon = {"windows": ROOT / "assets/icon.ico", "macos": ROOT / "assets/icon.icns"}.get(args.os)
    if icon and icon.exists():
        cmd += ["--icon", str(icon)]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append("run.py")
    run(cmd)

    out = {"windows": f"dist/{NAME}.exe", "macos": f"dist/{NAME}.app", "linux": f"dist/{NAME}"}[args.os]
    if args.onedir and args.os != "macos":
        out = f"dist/{NAME}/"
    print(f"\nDone -> {ROOT / out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
