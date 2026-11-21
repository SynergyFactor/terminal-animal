"""Logging to a small rotating file, plus a last-resort handler for unexpected errors.

The windowed executable has no console, so without this an unexpected error would vanish.
Log: <user cache folder>/TerminalVelocity/TerminalVelocity.log
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
import traceback
from pathlib import Path

from .cache import user_cache_path

log = logging.getLogger("terminal_velocity")


def log_path() -> Path:
    return user_cache_path().parent / "TerminalVelocity.log"


def setup_logging(level: int = logging.INFO) -> Path | None:
    """Log to a rotating file (512 KB x 2). Returns the log path, or None if it can't be written."""
    root = logging.getLogger()
    root.setLevel(level)
    path: Path | None = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8", delay=True)
    except OSError:
        path = None
        handler = logging.StreamHandler(sys.stderr) if sys.stderr else logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    return path


def install_excepthook(show_error) -> None:
    """Log uncaught exceptions (main and worker threads) and tell the user once per error type.

    show_error(title, text) is called on the GUI thread for main-thread errors.
    """
    shown: set[str] = set()

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("Unhandled error:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        key = exc_type.__name__
        if key not in shown:
            shown.add(key)
            where = log_path()
            try:
                show_error("Unexpected error",
                           f"Something went wrong ({exc_type.__name__}: {exc}).\n\n"
                           f"The app will keep running. Details were written to:\n{where}")
            except Exception:  # noqa: BLE001 - never let the error handler itself crash
                pass

    def thread_hook(args):
        log.error("Unhandled error in thread %s:\n%s", getattr(args.thread, "name", "?"),
                  "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = hook
    threading.excepthook = thread_hook
