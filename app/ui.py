"""Main window: viewport (image + plot), live search, stats panel."""
from __future__ import annotations

import html
import logging

from PySide6.QtCore import QEvent, QLocale, QObject, QRunnable, QThreadPool, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDoubleValidator, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from . import physics, wiki
from .cache import AnimalCache
from .plot import PlotWidget
from .theme import control_style

log = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 5 * 1024 * 1024

STYLE = """
QMainWindow, QWidget#central { background: #0f1115; }
QWidget { color: #e5e7eb; }
QFrame#viewport { background: #161a22; border: 1px solid #262c38; border-radius: 8px; }
QComboBox { background: #1d222c; border: 1px solid #2d3443; border-radius: 5px; padding: 4px 8px; }
QComboBox:hover { border-color: #3b82f6; }
QComboBox QAbstractItemView { background: #1d222c; border: 1px solid #2d3443;
                              selection-background-color: #1e3a5f; }
QLineEdit#search { padding: 8px 10px; font-size: 14px; border: 1px solid #2d3443;
                   border-radius: 6px; background: #161a22; color: #f3f4f6; }
QLineEdit#search:focus { border-color: #3b82f6; }
QLabel#searchLabel { color: #60a5fa; font-size: 14px; font-weight: 600; background: transparent;
                     border-right: 1px solid #2d3443; padding-right: 10px; }
QListWidget#results { border: 1px solid #2d3443; border-radius: 6px; background: #1a1f28;
                      font-size: 13px; outline: none; }
QListWidget#results::item { padding: 4px 8px; color: #e5e7eb; }
QListWidget#results::item:selected, QListWidget#results::item:hover { background: #1e3a5f; }
QLineEdit, QDoubleSpinBox { background: #161a22; border: 1px solid #2d3443; border-radius: 5px;
                            padding: 4px 6px; color: #f3f4f6; }
QLineEdit:focus, QDoubleSpinBox:focus { border-color: #3b82f6; }
QLabel#name { font-size: 18px; font-weight: 600; color: #f9fafb; }
QLabel#desc { color: #cbd5e1; }
QScrollArea#descArea, QScrollArea#sourcesArea { background: transparent; border: none; }
QScrollArea#descArea > QWidget > QWidget, QScrollArea#sourcesArea > QWidget > QWidget { background: transparent; }
QLabel#status, QLabel#sources { color: #94a3b8; font-size: 11px; }
QLabel[class="key"] { color: #94a3b8; }
QLabel[class="val"] { color: #f3f4f6; font-weight: 600; }
QLabel[class="ctl"] { color: #cbd5e1; }
QToolButton#sourcesToggle { border: none; color: #93c5fd; padding: 2px 4px; }
QToolButton#sourcesToggle:hover { color: #bfdbfe; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #2d3443; border-radius: 4px; min-height: 20px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""
SOURCES_HEIGHT = 120  # fixed height of the expandable sources panel


class SearchBar(QLineEdit):
    """Line edit with a permanent "Search" label inside it on the left."""

    def __init__(self, parent=None):
        super().__init__(parent, objectName="search")
        self.label = QLabel("Search", self, objectName="searchLabel")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setPlaceholderText("Type an animal name, e.g. cat, red fox, house sparrow")
        self.setClearButtonEnabled(True)

    def _layout_label(self):
        self.label.adjustSize()
        lw, lh = self.label.width(), self.label.height()
        self.label.move(12, (self.height() - lh) // 2)
        self.setTextMargins(lw + 6, 0, 0, 0)

    def showEvent(self, e):
        super().showEvent(e)
        self._layout_label()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_label()


# -- background work -----------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class _Job(QRunnable):
    def __init__(self, job_id: int, signals: _Signals, fn, *args):
        super().__init__()
        self.job_id, self.signals, self.fn, self.args = job_id, signals, fn, args

    def run(self):
        try:
            result = self.fn(*self.args)
        except Exception as e:  # noqa: BLE001 - surface any network/parse failure to the UI
            log.warning("background job %s failed", getattr(self.fn, "__name__", "?"), exc_info=True)
            self.signals.failed.emit(self.job_id, _friendly(e))
        else:
            self.signals.done.emit(self.job_id, result)


def _friendly(e: Exception) -> str:
    """A short, user-facing description of a failure."""
    text = str(e)
    if "429" in text or "ratelimited" in text:
        return "Wikipedia is rate-limiting requests (try again shortly)"
    if any(code in text for code in ("500", "502", "503", "504")):
        return "Wikipedia is temporarily unavailable"
    if isinstance(e, LookupError):
        return text
    if isinstance(e, (OSError, TimeoutError)):
        return "no connection to Wikipedia"
    return f"{type(e).__name__}: {text[:160]}"


def plain(label: QLabel) -> QLabel:
    """Render text literally — never as HTML (protects against markup in downloaded data)."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


# -- formatting ----------------------------------------------------------------------------------
def fmt_mass(kg: float) -> str:
    if kg >= 1000:
        return f"{kg / 1000:.3g} t"
    if kg >= 1:
        return f"{kg:.3g} kg"
    if kg >= 1e-3:
        return f"{kg * 1e3:.3g} g"
    return f"{kg * 1e6:.3g} mg"


def fmt_len(m: float) -> str:
    if m >= 1000:
        return f"{m / 1000:.3g} km"
    if m >= 1:
        return f"{m:.3g} m"
    if m >= 0.01:
        return f"{m * 100:.3g} cm"
    return f"{m * 1000:.3g} mm"


def fmt_area(a: float) -> str:
    if a >= 0.01:
        return f"{a:.3g} m²"
    if a >= 1e-4:
        return f"{a * 1e4:.3g} cm²"
    return f"{a * 1e6:.3g} mm²"


def fmt_reynolds(re_: float) -> str:
    if re_ < 10:
        return f"{re_:.2g}"
    if re_ < 1e6:
        return f"{float(f'{re_:.3g}'):,.0f}"
    return f"{re_:.2e}"


def fmt_energy(j: float) -> str:
    for unit, k in (("MJ", 1e6), ("kJ", 1e3), ("J", 1), ("mJ", 1e-3)):
        if j >= k:
            return f"{j / k:.3g} {unit}"
    return f"{j * 1e6:.3g} µJ"


# -- window --------------------------------------------------------------------------------------
STATS = [
    ("mass", "Average mass"), ("length", "Average body length"),
    ("vt", "Terminal velocity"), ("area", "Frontal area (belly-down)"),
    ("d99", "Distance to reach 99 % v<sub>t</sub>"), ("t99", "Time to reach 99 % v<sub>t</sub>"),
    ("risk", "Severe-injury likelihood at v<sub>t</sub>"), ("energy", "Impact energy at v<sub>t</sub>"),
    ("h50", "Fall height for 50 % risk"), ("h10", "Fall height for 10 % risk"),
    ("cd", "Drag coefficient"), ("re", "Reynolds number"),
]
CHARTS = ["Speed vs distance fallen", "Speed vs time", "Injury risk vs fall height"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Terminal Velocity — Animal Fall Calculator")
        self.setStyleSheet(STYLE + control_style())

        self.cache = AnimalCache()
        self.pool = QThreadPool.globalInstance()
        self._jobs: dict[int, tuple] = {}
        self._job_counter = 0
        self._signals = _Signals(self)          # lives in the GUI thread -> queued delivery
        self._signals.done.connect(self._job_done)
        self._signals.failed.connect(self._job_failed)
        self._search_seq = 0
        self._search_busy = False       # at most one live search request in flight
        self._search_pending = False
        self._fetch_seq = 0
        self.record: dict | None = None
        self.result: physics.FallResult | None = None
        self._pixmap: QPixmap | None = None

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(10)

        # viewport ---------------------------------------------------------------------------
        viewport = QFrame(objectName="viewport")
        viewport.setMinimumHeight(300)
        vp = QHBoxLayout(viewport)
        vp.setContentsMargins(10, 10, 10, 10)
        self.image = QLabel("No animal selected", alignment=Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(220, 220)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image.setStyleSheet("color:#64748b;")
        vp.addWidget(self.image, 2)
        right = QVBoxLayout()
        self.chart_select = QComboBox()
        self.chart_select.addItems(CHARTS)
        self.chart_select.currentIndexChanged.connect(lambda *_: self._draw_chart())
        self.plot = PlotWidget()
        right.addWidget(self.chart_select)
        right.addWidget(self.plot, 1)
        vp.addLayout(right, 3)
        root.addWidget(viewport, 1)

        # search -----------------------------------------------------------------------------
        self.search = SearchBar()
        self.search.textEdited.connect(self._on_text)
        self.search.installEventFilter(self)
        root.addWidget(self.search)

        self.results = QListWidget(central, objectName="results")
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.itemClicked.connect(self._choose_item)
        self.results.hide()

        self.debounce = QTimer(self, singleShot=True, interval=450)
        self.debounce.timeout.connect(self._live_search)

        # info panel: every part below has a fixed height so the controls never move ---------
        head = QHBoxLayout()
        self.name = plain(QLabel("", objectName="name"))
        self.link = QLabel("")
        self.link.setTextFormat(Qt.TextFormat.RichText)
        self.link.linkActivated.connect(self._open_link)     # vetted; never opens arbitrary URLs
        head.addWidget(self.name, 1)
        head.addWidget(self.link)
        root.addLayout(head)

        self.desc = plain(QLabel("Type an animal's name above. Data comes from Wikipedia.",
                                 objectName="desc", wordWrap=True))
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        desc_area = QScrollArea(objectName="descArea", widgetResizable=True)
        desc_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        desc_area.setWidget(self.desc)
        desc_area.setFixedHeight(self.desc.fontMetrics().lineSpacing() * 4 + 6)
        root.addWidget(desc_area)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(5)
        self.values: dict[str, QLabel] = {}
        for i, (key, label) in enumerate(STATS):
            r, c = divmod(i, 2)
            k = QLabel(label)
            k.setProperty("class", "key")
            v = QLabel("—")
            v.setProperty("class", "val")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(k, r, c * 2)
            grid.addWidget(v, r, c * 2 + 1)
            self.values[key] = v
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        root.addLayout(grid)

        controls = QWidget()
        controls.setFixedHeight(36)
        adjust = QHBoxLayout(controls)
        adjust.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel("Mass override (kg):")
        lbl.setProperty("class", "ctl")
        adjust.addWidget(lbl)
        self.mass_edit = QLineEdit(placeholderText="auto")
        validator = QDoubleValidator(physics.MIN_MASS, physics.MAX_MASS, 9)
        validator.setLocale(QLocale.c())                      # "." decimals regardless of OS locale
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.mass_edit.setValidator(validator)
        self.mass_edit.setMaxLength(20)
        self.mass_edit.setFixedWidth(130)
        self.mass_edit.editingFinished.connect(lambda: self._recompute())
        adjust.addWidget(self.mass_edit)
        adjust.addSpacing(16)
        lbl = QLabel("Drag coefficient:")
        lbl.setProperty("class", "ctl")
        adjust.addWidget(lbl)
        self.cd_spin = QDoubleSpinBox(minimum=0.0, maximum=3.0, singleStep=0.1, decimals=2)
        self.cd_spin.setSpecialValueText("Auto")
        self.cd_spin.setFixedWidth(80)
        self.cd_spin.valueChanged.connect(lambda *_: self._recompute())
        adjust.addWidget(self.cd_spin)
        adjust.addStretch(1)
        self.sources_toggle = QToolButton(objectName="sourcesToggle", checkable=True,
                                          text="Data sources && model notes")
        self.sources_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sources_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.sources_toggle.toggled.connect(self._toggle_sources)
        adjust.addWidget(self.sources_toggle)
        root.addWidget(controls)

        self.source = plain(QLabel("", objectName="sources", wordWrap=True))
        self.source.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sources_area = QScrollArea(objectName="sourcesArea", widgetResizable=True)
        self.sources_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sources_area.setWidget(self.source)
        self.sources_area.setFixedHeight(SOURCES_HEIGHT)
        self.sources_area.hide()
        root.addWidget(self.sources_area)

        self.status = plain(QLabel("", objectName="status"))
        self.status.setFixedHeight(self.status.fontMetrics().height() + 2)
        root.addWidget(self.status)
        self.resize(840, 840)

    def _open_link(self, url: str):
        if wiki.is_wikipedia_url(url):
            QDesktopServices.openUrl(QUrl(url))
        else:
            log.warning("blocked link: %r", url)

    def _toggle_sources(self, checked: bool):
        """Expand/collapse the sources panel, growing the window so nothing above it moves."""
        self.sources_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.sources_area.setVisible(checked)
        if not (self.isMaximized() or self.isFullScreen()):
            delta = SOURCES_HEIGHT + self.centralWidget().layout().spacing()
            self.resize(self.width(), self.height() + (delta if checked else -delta))

    # -- jobs -----------------------------------------------------------------------------------
    def _run(self, fn, args, on_done, on_fail):
        self._job_counter += 1
        jid = self._job_counter
        self._jobs[jid] = (on_done, on_fail)
        self.pool.start(_Job(jid, self._signals, fn, *args))

    @Slot(int, object)
    def _job_done(self, jid, payload):
        cbs = self._jobs.pop(jid, None)
        if cbs:
            cbs[0](payload)

    @Slot(int, str)
    def _job_failed(self, jid, msg):
        cbs = self._jobs.pop(jid, None)
        if cbs:
            cbs[1](msg)

    # -- search ---------------------------------------------------------------------------------
    def _on_text(self, text: str):
        self._search_seq += 1
        if not text.strip():
            self.results.hide()
            self.debounce.stop()
            return
        self._show_results(self.cache.search(text))
        self.debounce.start()

    def _live_search(self):
        text = self.search.text()
        if len(text.strip()) < 2:
            return
        if self._search_busy:            # one request at a time; run the latest text afterwards
            self._search_pending = True
            return
        self._search_busy = True
        seq = self._search_seq
        self.status.setText("Searching Wikipedia…")

        def finished():
            self._search_busy = False
            if self._search_pending:
                self._search_pending = False
                if seq != self._search_seq and self.search.text().strip():
                    self._live_search()

        def done(hits):
            finished()
            if seq != self._search_seq:
                return
            self.status.setText("")
            local = self.cache.search(text)
            seen = {h["title"].lower() for h in local}
            merged = local + [h for h in hits if h["title"].lower() not in seen]
            self._show_results(merged[:10])

        def fail(msg):
            finished()
            if seq == self._search_seq:
                busy = "429" in msg or "503" in msg
                self.status.setText("Wikipedia is busy — showing cached animals; try again in a moment."
                                    if busy else
                                    "Offline or Wikipedia unreachable — showing cached animals only.")

        self._run(wiki.search, (text,), done, fail)

    def _show_results(self, hits: list[dict]):
        self.results.clear()
        for h in hits:
            if not isinstance(h, dict) or not isinstance(h.get("title"), str):
                continue
            desc = h.get("description") if isinstance(h.get("description"), str) else ""
            label = h["title"] + (f"  —  {desc}" if desc else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, h["title"])
            if h.get("cached"):
                item.setToolTip("Available offline")
            self.results.addItem(item)
        if not hits:
            self.results.hide()
            return
        row_h = self.results.sizeHintForRow(0) or 22
        height = min(len(hits), 7) * row_h + 6
        g = self.search.geometry()
        self.results.setGeometry(g.left(), g.bottom() + 2, g.width(), height)
        self.results.setCurrentRow(-1)
        self.results.show()
        self.results.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not hasattr(self, "status"):
            return
        if self.results.isVisible():
            g = self.search.geometry()
            self.results.setGeometry(g.left(), g.bottom() + 2, g.width(), self.results.height())
        self._update_pixmap()

    def eventFilter(self, obj, ev):
        if obj is self.search and ev.type() == QEvent.Type.KeyPress and self.results.isVisible():
            key, n, row = ev.key(), self.results.count(), self.results.currentRow()
            if key == Qt.Key.Key_Down:
                self.results.setCurrentRow(min(row + 1, n - 1))
                return True
            if key == Qt.Key.Key_Up:
                self.results.setCurrentRow(max(row - 1, 0))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem() or self.results.item(0)
                if item:
                    self._choose_item(item)
                return True
            if key == Qt.Key.Key_Escape:
                self.results.hide()
                return True
        if obj is self.search and ev.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(150, self.results.hide)  # let clicks on the list land first
        return super().eventFilter(obj, ev)

    # -- selection ------------------------------------------------------------------------------
    def _choose_item(self, item: QListWidgetItem):
        title = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(title, str) or not title:
            return
        self._search_seq += 1
        self.debounce.stop()
        self.results.hide()
        self.search.setText(title)
        self.load(title)

    def load(self, title: str):
        cached = self.cache.get(title)
        if cached:
            self._show_record(cached)
            if not wiki.is_stale(cached):
                return
            # Outdated entry: show it now, refresh quietly in the background.
        self._fetch_seq += 1
        seq = self._fetch_seq
        self.status.setText(f"Updating “{title}” from Wikipedia…" if cached
                            else f"Loading “{title}” from Wikipedia…")

        def done(rec):
            if seq != self._fetch_seq:
                return
            if not isinstance(rec, dict) or not isinstance(rec.get("title"), str):
                self.status.setText(f"Couldn't load “{title}”: unexpected data")
                return
            self.status.setText("")
            if rec.get("image_failed") and cached and cached.get("image"):
                rec["image"], rec["image_failed"] = cached["image"], False
                rec["image_file"] = cached.get("image_file")
            self.cache.put(rec)
            self._show_record(rec)

        def fail(msg):
            if seq != self._fetch_seq:
                return
            if cached:
                self.status.setText("Showing cached data (couldn't refresh from Wikipedia).")
            else:
                self.status.setText(f"Couldn't load “{title}”: {msg}")

        self._run(wiki.fetch_animal, (title,), done, fail)

    def _show_record(self, rec: dict):
        self.record = rec
        self.name.setText(str(rec.get("title", "")))
        url = rec.get("url")
        self.link.setText(f'<a href="{html.escape(url, quote=True)}">Wikipedia ↗</a>'
                          if wiki.is_wikipedia_url(url) else "")
        desc = rec.get("summary") or rec.get("description") or ""
        self.desc.setText(desc if isinstance(desc, str) else "")
        self.mass_edit.blockSignals(True)
        self.mass_edit.clear()
        self.mass_edit.blockSignals(False)

        self._pixmap = None
        img = rec.get("image")
        if isinstance(img, (bytes, bytearray)) and 0 < len(img) <= MAX_IMAGE_BYTES:
            pm = QPixmap()
            if pm.loadFromData(bytes(img)) and not pm.isNull():
                self._pixmap = pm
        if self._pixmap is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("No image available")
        self._update_pixmap()
        self._recompute()

    def _update_pixmap(self):
        if self._pixmap is not None:
            self.image.setPixmap(self._pixmap.scaled(
                self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    # -- calculation ----------------------------------------------------------------------------
    def _recompute(self):
        rec = self.record
        if not rec:
            return
        override = self.mass_edit.text().strip()
        mass = None
        if override:
            try:
                mass = float(override)
            except ValueError:
                mass = None
        if mass is None:
            override = ""
            mass = rec.get("mass_kg") if isinstance(rec.get("mass_kg"), (int, float)) else None
        cd = self.cd_spin.value() or None

        if not mass:
            self.result = None
            for v in self.values.values():
                v.setText("—")
            self.plot.clear("No size data found in any source — enter a mass override below.")
            self.source.setText("")
            self.mass_edit.setPlaceholderText("required")
            self.mass_edit.setFocus()
            return
        try:
            r = physics.compute(mass, rec.get("length_m"), cd)
        except ValueError as e:
            self.result = None
            for v in self.values.values():
                v.setText("—")
            self.plot.clear(f"Can't calculate: {e}.")
            self.status.setText(f"Can't calculate: {e}. Enter a mass override below.")
            return
        if isinstance(rec.get("mass_kg"), (int, float)) and rec["mass_kg"] > 0:
            self.mass_edit.setPlaceholderText(f"auto ({fmt_mass(rec['mass_kg'])})")
        self.result = r
        def tag(origin: str | None) -> str:
            if not origin or origin == "Wikipedia":
                return ""
            short = str(origin).replace(" value)", ")").replace("Estimated from length", "est. from length")
            return f'  <span style="color:#94a3b8;font-weight:400">· {html.escape(short)}</span>'

        est_m = tag(rec.get("mass_origin")) if not override else ""
        est_l = (tag(rec.get("length_origin")) if rec.get("length_m") else
                 '  <span style="color:#94a3b8;font-weight:400">· est. from mass</span>')
        kmh, mph = r.v_t * 3.6, r.v_t * 2.237
        color = {"Very low": "#4ade80", "Low": "#a3e635", "Moderate": "#fbbf24",
                 "High": "#fb923c", "Very high": "#f87171"}[physics.risk_label(r.injury_prob)]
        sv = self.values
        sv["mass"].setText(fmt_mass(mass) + ("  (override)" if override else est_m))
        sv["length"].setText(fmt_len(r.length_m) + est_l)
        sv["vt"].setText(f"{r.v_t:.3g} m/s  ({kmh:.0f} km/h, {mph:.0f} mph)")
        sv["area"].setText(fmt_area(r.area_m2))
        sv["d99"].setText(fmt_len(r.d_99))
        sv["t99"].setText(f"{r.t_99:.3g} s")
        sv["risk"].setText(f'<span style="color:{color}">{r.injury_prob * 100:.0f} % — '
                           f'{physics.risk_label(r.injury_prob)}</span>')
        sv["energy"].setText(fmt_energy(r.impact_energy_j))
        sv["h50"].setText(fmt_len(r.h50) if r.h50 is not None else "Never (survives v<sub>t</sub>)")
        sv["h10"].setText(fmt_len(r.h10) if r.h10 is not None else "Never")
        sv["cd"].setText(f"{r.cd:.2f}" + ("" if cd else " (auto)"))
        sv["re"].setText(fmt_reynolds(r.reynolds))

        src = [f"Text: Wikipedia, “{rec.get('title', '')}” (CC BY-SA 4.0) — {rec.get('url') or ''}"]
        if rec.get("image") and rec.get("image_file"):
            src.append(f"Image: {wiki.image_page_url(rec['image_file'])} (author and licence on that page)")
        if rec.get("sci_name"):
            src.append(f"Scientific name: {rec['sci_name']}" + (f" ({rec['rank']})" if rec.get("rank") else ""))
        for label, origin, text in (("Mass", rec.get("mass_origin"), rec.get("mass_source")),
                                    ("Length", rec.get("length_origin"), rec.get("length_source"))):
            if label == "Mass" and override:
                src.append("Mass: your override")
            elif origin == "Wikipedia" or (not origin and text):
                src.append(f"{label} from Wikipedia: “{text}”" if text else f"{label}: Wikipedia article")
            elif origin:
                src.append(f"{label}: {origin} — {text}")
        notes = rec.get("data_notes")
        src += [str(n) for n in notes] if isinstance(notes, list) else []
        src.append("Injury likelihood is a heuristic scaled from human fall data; "
                   "assumes a passive belly-down fall at sea level.")
        self.source.setText("\n".join(src))
        self._draw_chart()

    def _draw_chart(self):
        r = self.result
        if r is None:
            return
        idx = self.chart_select.currentIndex()
        n = 160
        if idx == 0:
            xmax = r.d_99 * 1.3
            xs = [xmax * i / n for i in range(n + 1)]
            self.plot.set_data(xs, [r.speed_at_distance(x) for x in xs],
                               title="Speed vs distance fallen", xlabel="Distance fallen (m)",
                               ylabel="Speed (m/s)", hlines=[(r.v_t, f"Terminal {r.v_t:.3g} m/s")],
                               vlines=[(r.d_99, f"99 % at {fmt_len(r.d_99)}")])
        elif idx == 1:
            xmax = r.t_99 * 1.3
            xs = [xmax * i / n for i in range(n + 1)]
            self.plot.set_data(xs, [r.speed_at_time(x) for x in xs],
                               title="Speed vs time", xlabel="Time (s)", ylabel="Speed (m/s)",
                               hlines=[(r.v_t, f"Terminal {r.v_t:.3g} m/s")],
                               vlines=[(r.t_99, f"99 % at {r.t_99:.3g} s")])
        else:
            xmax = r.h50 * 3 if r.h50 is not None else r.d_99 * 1.3
            xmax = max(xmax, 1e-3)
            xs = [xmax * i / n for i in range(n + 1)]
            vlines = [(r.h50, f"50 % at {fmt_len(r.h50)}")] if r.h50 is not None else []
            self.plot.set_data(xs, [100 * r.injury_at_height(x) for x in xs],
                               title="Severe-injury likelihood vs fall height",
                               xlabel="Fall height (m)", ylabel="Likelihood (%)",
                               vlines=vlines, ymax=100)
