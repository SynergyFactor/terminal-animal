import argparse
import logging
import os
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .cache import user_cache_path
from .logsetup import install_excepthook, setup_logging
from .ui import MainWindow

log = logging.getLogger(__name__)


def dark_palette() -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#0f1115",
        QPalette.ColorRole.WindowText: "#e5e7eb",
        QPalette.ColorRole.Base: "#161a22",
        QPalette.ColorRole.AlternateBase: "#1a1f28",
        QPalette.ColorRole.Text: "#f3f4f6",
        QPalette.ColorRole.PlaceholderText: "#6b7280",
        QPalette.ColorRole.Button: "#1d222c",
        QPalette.ColorRole.ButtonText: "#e5e7eb",
        QPalette.ColorRole.BrightText: "#ffffff",
        QPalette.ColorRole.Highlight: "#2563eb",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#1d222c",
        QPalette.ColorRole.ToolTipText: "#e5e7eb",
        QPalette.ColorRole.Link: "#60a5fa",
        QPalette.ColorRole.LinkVisited: "#a78bfa",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#4b5563"))
    return pal


def handle_cli(argv: list[str]) -> list[str]:
    """App flags (also work on the built executable). Returns the args left for Qt."""
    ap = argparse.ArgumentParser(prog="TerminalVelocity", add_help=True)
    ap.add_argument("--clear-cache", action="store_true",
                    help="delete the local cache of animals looked up live, then start")
    ap.add_argument("--refresh-all", action="store_true",
                    help="mark every cached animal outdated so each is re-fetched when opened")
    args, rest = ap.parse_known_args(argv[1:])
    if args.clear_cache:
        path = user_cache_path()
        try:
            path.unlink(missing_ok=True)
            log.info("cleared %s", path)
            print(f"Cleared {path}")
        except OSError as e:
            log.warning("couldn't clear %s: %s", path, e)
            print(f"Couldn't clear {path}: {e}")
    if args.refresh_all:
        os.environ["TV_REFRESH_ALL"] = "1"
    return [argv[0], *rest]


def main() -> int:
    log_file = setup_logging()
    log.info("Terminal Velocity %s starting (log: %s)", __version__, log_file)
    app = QApplication(handle_cli(sys.argv))
    app.setApplicationName("Terminal Velocity")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    install_excepthook(lambda title, text: QMessageBox.warning(app.activeWindow(), title, text))
    try:
        window = MainWindow()
    except Exception:
        log.exception("startup failed")
        QMessageBox.critical(None, "Terminal Velocity couldn't start",
                             f"Startup failed. Details were written to:\n{log_file}")
        return 1
    window.show()
    return app.exec()
