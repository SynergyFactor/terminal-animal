"""Shared dark-theme styling for spin boxes and combo boxes (app + manager).

Arrow glyphs are drawn once into small PNGs (1x and @2x for high-DPI screens) and referenced from
the stylesheet, so they render crisply and identically on every platform.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

from .cache import user_cache_path

ARROW = "#e5e7eb"
ARROW_DISABLED = "#4b5563"
_W, _H = 10, 6          # logical arrow size in px

_cache: str | None = None


def _draw(path: Path, direction: str, color: str, scale: int) -> None:
    w, h = _W * scale, _H * scale
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    if direction == "up":
        pts = [QPointF(0, h), QPointF(w / 2, 0), QPointF(w, h)]
    else:
        pts = [QPointF(0, 0), QPointF(w, 0), QPointF(w / 2, h)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    if path.is_symlink():
        path.unlink()                    # never write through a symlink
    img.save(str(path), "PNG")


def _ui_folder() -> Path:
    """A private folder for the arrow images.

    Uses the per-user cache folder (not a fixed name in the shared temp dir, which another local
    user could pre-create with symlinks); falls back to a fresh private temp dir.
    """
    try:
        folder = user_cache_path().parent / "ui"
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink():
            raise OSError("refusing symlinked ui folder")
        probe = folder / ".write-test"
        probe.write_bytes(b"")
        probe.unlink()
        return folder
    except OSError:
        return Path(tempfile.mkdtemp(prefix="TerminalVelocity-ui-"))


def _arrows() -> dict[str, str]:
    folder = _ui_folder()
    out = {}
    for direction in ("up", "down"):
        for state, color in (("", ARROW), ("-off", ARROW_DISABLED)):
            name = f"{direction}{state}"
            _draw(folder / f"{name}.png", direction, color, 1)
            _draw(folder / f"{name}@2x.png", direction, color, 2)
            out[name] = (folder / f"{name}.png").as_posix()
    return out


def control_style() -> str:
    """Stylesheet for spin boxes and combo boxes. Call after QApplication exists.

    If the arrow images can't be written, Qt's default arrows are used instead.
    """
    global _cache
    if _cache is None:
        try:
            a = _arrows()
        except OSError:
            _cache = ""
            return _cache
        _cache = f"""
QAbstractSpinBox {{ padding-right: 24px; min-height: 22px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 22px; background: #252b37;
    border-left: 1px solid #2d3443; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 5px;
    border-bottom: 1px solid #2d3443; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 5px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: #334056; }}
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{ background: #1d4ed8; }}
QAbstractSpinBox::up-arrow {{ image: url("{a['up']}"); width: {_W}px; height: {_H}px; }}
QAbstractSpinBox::down-arrow {{ image: url("{a['down']}"); width: {_W}px; height: {_H}px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {{ image: url("{a['up-off']}"); }}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: url("{a['down-off']}"); }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 26px;
    border-left: 1px solid #2d3443; background: #252b37;
    border-top-right-radius: 5px; border-bottom-right-radius: 5px; }}
QComboBox::drop-down:hover {{ background: #334056; }}
QComboBox::down-arrow {{ image: url("{a['down']}"); width: {_W}px; height: {_H}px; }}
QComboBox::down-arrow:disabled {{ image: url("{a['down-off']}"); }}
"""
    return _cache
