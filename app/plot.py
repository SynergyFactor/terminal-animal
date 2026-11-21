"""A tiny QPainter line-plot widget (keeps the build free of matplotlib).

Marker labels are placed by trying several candidate positions and taking the first one that
stays inside the plot and doesn't collide with another label or the curve.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

BG = QColor("#161a22")
AXIS = QColor("#6b7280")
GRID = QColor("#252b37")
TEXT = QColor("#cbd5e1")
MUTED = QColor("#94a3b8")
LINE = QColor("#60a5fa")
MARK = QColor("#f87171")
LABEL_BG = QColor(22, 26, 34, 225)


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    ticks, t = [], first
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 12))
        t += step
    return ticks


def fmt_tick(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 0.001:
        return f"{v:.0e}".replace("e+0", "e").replace("e-0", "e-")
    if a >= 100:
        return f"{v:.0f}"
    return f"{v:g}"


class PlotWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self._xs: list[float] = []
        self._ys: list[float] = []
        self._xlabel = self._ylabel = self._title = ""
        self._hlines: list[tuple[float, str]] = []
        self._vlines: list[tuple[float, str]] = []
        self._ymax: float | None = None
        self._message = "Search for an animal to see its fall."

    def clear(self, message: str = "") -> None:
        self._xs, self._ys = [], []
        self._message = message
        self.update()

    def set_data(self, xs, ys, *, title="", xlabel="", ylabel="", hlines=(), vlines=(), ymax=None):
        self._xs, self._ys = list(xs), list(ys)
        self._title, self._xlabel, self._ylabel = title, xlabel, ylabel
        self._hlines, self._vlines = list(hlines), list(vlines)
        self._ymax = ymax
        self._message = ""
        self.update()

    # -------------------------------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), BG)
        small = QFont(self.font())
        small.setPointSizeF(max(7.5, self.font().pointSizeF() - 1))
        p.setFont(small)
        fm = p.fontMetrics()
        th = fm.height()

        if not self._xs:
            p.setPen(MUTED)
            p.drawText(self.rect().adjusted(12, 0, -12, 0),
                       Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value, self._message)
            return

        x0, x1 = min(self._xs), max(self._xs)
        y1 = self._ymax if self._ymax is not None else max(self._ys + [h for h, _ in self._hlines])
        y1 = y1 * 1.08 if y1 > 0 else 1.0
        y0 = 0.0
        if x1 <= x0:
            x1 = x0 + 1

        # margins sized to the text they hold
        top, bottom, right = th + 12, 2 * th + 10, 16
        ytick_n = max(2, int((self.height() - top - bottom) / (th * 2.6)))
        yticks = nice_ticks(y0, y1, ytick_n)
        ytick_w = max(fm.horizontalAdvance(fmt_tick(t)) for t in yticks)
        left = 6 + th + 8 + ytick_w + 6
        plot = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)

        def sx(x): return plot.left() + (x - x0) / (x1 - x0) * plot.width()
        def sy(y): return plot.bottom() - (y - y0) / (y1 - y0) * plot.height()

        # title
        bold = QFont(small)
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(TEXT)
        p.drawText(QRectF(left, 4, plot.width(), th + 2), Qt.AlignmentFlag.AlignHCenter, self._title)
        p.setFont(small)

        # y grid + labels
        for t in yticks:
            y = sy(t)
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(MUTED)
            p.drawText(QRectF(left - ytick_w - 6, y - th / 2, ytick_w, th),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_tick(t))

        # x grid + labels (skip any label that would collide with the previous one)
        xticks = nice_ticks(x0, x1, max(2, int(plot.width() / 85)))
        last_right = -1e9
        for t in xticks:
            x = sx(t)
            p.setPen(QPen(GRID, 1))
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            label = fmt_tick(t)
            w = fm.horizontalAdvance(label)
            lx = min(max(x - w / 2, 0), self.width() - w - 2)
            if lx < last_right + 8:
                continue
            p.setPen(MUTED)
            p.drawText(QRectF(lx, plot.bottom() + 4, w + 1, th), Qt.AlignmentFlag.AlignLeft, label)
            last_right = lx + w

        p.setPen(QPen(AXIS, 1))
        p.drawLine(plot.bottomLeft(), plot.bottomRight())
        p.drawLine(plot.bottomLeft(), plot.topLeft())

        # axis titles
        p.setPen(TEXT)
        p.drawText(QRectF(plot.left(), self.height() - th - 4, plot.width(), th),
                   Qt.AlignmentFlag.AlignHCenter, self._xlabel)
        p.save()
        p.translate(6 + th / 2, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-plot.height() / 2, -th / 2, plot.height(), th),
                   Qt.AlignmentFlag.AlignHCenter, self._ylabel)
        p.restore()

        # series (screen points are also used for label collision tests)
        pts = [QPointF(sx(x), sy(y)) for x, y in zip(self._xs, self._ys)]

        # marker lines
        dash = QPen(MARK, 1.2, Qt.PenStyle.DashLine)
        hl = [(sy(y), lab) for y, lab in self._hlines if y0 <= y <= y1]
        vl = [(sx(x), lab) for x, lab in self._vlines if x0 <= x <= x1]
        p.setPen(dash)
        for y, _ in hl:
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for x, _ in vl:
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.setPen(QPen(LINE, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # marker labels, collision-aware
        placed: list[QRectF] = []
        inner = plot.adjusted(2, 2, -2, -2)

        def free(r: QRectF) -> bool:
            if not inner.contains(r):
                return False
            if any(r.intersects(o) for o in placed):
                return False
            grown = r.adjusted(-2, -2, 2, 2)
            return not any(grown.contains(pt) for pt in pts)

        def place(text: str, candidates: list[QRectF]):
            choice = next((r for r in candidates if free(r)), None)
            if choice is None:  # fall back to the first candidate that doesn't hit another label
                choice = next((r for r in candidates if inner.contains(r) and
                               not any(r.intersects(o) for o in placed)), candidates[0])
            placed.append(choice)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(LABEL_BG)
            p.drawRoundedRect(choice, 3, 3)
            p.setPen(MARK)
            p.drawText(choice, Qt.AlignmentFlag.AlignCenter, text)

        pad = 4
        for y, text in hl:
            w, h = fm.horizontalAdvance(text) + 2 * pad, th + 2
            place(text, [
                QRectF(plot.left() + 8, y - h - 3, w, h),             # left, above
                QRectF(plot.left() + 8, y + 3, w, h),                 # left, below
                QRectF(plot.center().x() - w / 2, y + 3, w, h),       # middle, below
                QRectF(plot.right() - w - 8, y + 3, w, h),            # right, below
                QRectF(plot.right() - w - 8, y - h - 3, w, h),        # right, above
            ])
        for x, text in vl:
            w, h = fm.horizontalAdvance(text) + 2 * pad, th + 2
            cands = []
            for yy in (plot.bottom() - h - 6, plot.center().y() - h / 2, plot.top() + 6 + h + 4,
                       plot.top() + 6):
                cands += [QRectF(x + 5, yy, w, h), QRectF(x - w - 5, yy, w, h)]
            place(text, cands)
        p.setBrush(Qt.BrushStyle.NoBrush)
