"""Terminal Velocity — Manager: a GUI for every terminal operation.

    python manager.py        (or manager.bat / ./manager.sh)

Tabs: Cache (scrape/update/rebuild/clear), Fallback data (download databases, build the index),
Missing data (fill gaps with cited overrides), Build (package the app, run it, run tests).
Every action runs the same script you'd run in a terminal; its output streams into the log below.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_cache  # noqa: E402
import fallback_data  # noqa: E402
from app import traits  # noqa: E402
from app.cache import user_cache_path  # noqa: E402
from app.logsetup import install_excepthook, setup_logging  # noqa: E402
from app.main import dark_palette  # noqa: E402
from app.theme import control_style  # noqa: E402

HOST = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "linux")
PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")

STYLE = """
QWidget { color: #e5e7eb; }
QMainWindow, QWidget#central { background: #0f1115; }
QTabWidget::pane { border: 1px solid #262c38; border-radius: 6px; background: #161a22; top: -1px; }
QTabBar::tab { background: #12151b; border: 1px solid #262c38; padding: 7px 16px; margin-right: 2px;
               border-top-left-radius: 6px; border-top-right-radius: 6px; color: #94a3b8; }
QTabBar::tab:selected { background: #161a22; color: #f3f4f6; border-bottom-color: #161a22; }
QGroupBox { border: 1px solid #262c38; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #93c5fd; }
QPushButton { background: #1d222c; border: 1px solid #2d3443; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { border-color: #3b82f6; }
QPushButton:disabled { color: #4b5563; border-color: #1f2430; }
QPushButton[primary="true"] { background: #1d4ed8; border-color: #2563eb; color: white; }
QPushButton[primary="true"]:hover { background: #2563eb; }
QPushButton[danger="true"] { color: #fca5a5; }
QLineEdit, QDoubleSpinBox, QComboBox { background: #12151b; border: 1px solid #2d3443;
    border-radius: 5px; padding: 4px 6px; }
QPlainTextEdit { background: #0b0d11; border: 1px solid #262c38; border-radius: 6px; color: #cbd5e1; }
QTableWidget { background: #12151b; border: 1px solid #262c38; gridline-color: #262c38;
               alternate-background-color: #151922; }
QHeaderView::section { background: #1a1f28; color: #94a3b8; border: none; padding: 4px 6px;
                       border-right: 1px solid #262c38; }
QProgressBar { background: #12151b; border: 1px solid #262c38; border-radius: 5px; text-align: center;
               height: 16px; color: #e5e7eb; }
QProgressBar::chunk { background: #2563eb; border-radius: 4px; }
QLabel[muted="true"] { color: #94a3b8; }
"""


def muted(text: str) -> QLabel:
    lab = QLabel(text, wordWrap=True)
    lab.setProperty("muted", True)
    return lab


class Manager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Terminal Velocity — Manager")
        self.setStyleSheet(STYLE + control_style())
        self.settings = QSettings("TerminalVelocity", "Manager")
        self.proc: QProcess | None = None
        self.actions: list[QPushButton] = []      # disabled while a task runs
        self._after = None

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 10)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._cache_tab(), "Cache")
        self.tabs.addTab(self._fallback_tab(), "Fallback data")
        self.tabs.addTab(self._missing_tab(), "Missing data")
        self.tabs.addTab(self._build_tab(), "Build && run")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        root.addWidget(self.tabs, 3)

        bar = QHBoxLayout()
        self.task_label = QLabel("Ready.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setMaximumWidth(260)
        self.cancel = QPushButton("Cancel", enabled=False)
        self.cancel.setProperty("danger", True)
        self.cancel.clicked.connect(self.cancel_task)
        clear = QPushButton("Clear log")
        bar.addWidget(self.task_label, 1)
        bar.addWidget(self.progress)
        bar.addWidget(self.cancel)
        bar.addWidget(clear)
        root.addLayout(bar)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setFont(QFont("Consolas" if HOST == "windows" else "Menlo" if HOST == "macos"
                               else "DejaVu Sans Mono", 9))
        self.log.setMaximumBlockCount(5000)
        clear.clicked.connect(self.log.clear)
        root.addWidget(self.log, 2)

        self.resize(940, 820)
        self.refresh()

    # -- helpers ---------------------------------------------------------------------------------
    def button(self, text, slot, primary=False, danger=False) -> QPushButton:
        b = QPushButton(text)
        b.clicked.connect(slot)
        if primary:
            b.setProperty("primary", True)
        if danger:
            b.setProperty("danger", True)
        self.actions.append(b)
        return b

    def confirm(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    # -- Cache tab -------------------------------------------------------------------------------
    def _cache_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        box = QGroupBox("Status")
        b = QVBoxLayout(box)
        self.cache_status = QLabel(wordWrap=True)
        self.cache_status.setTextFormat(Qt.TextFormat.RichText)
        b.addWidget(self.cache_status)
        lay.addWidget(box)

        box = QGroupBox("Bundled cache (scripts/animals.txt → data/animals.json)")
        g = QVBoxLayout(box)
        for mode, text, tip, kw in [
            ("missing", "Add missing animals", "Scrape animals in the list that aren't cached yet.", {"primary": True}),
            ("stale", "Update outdated entries", "Re-scrape entries made by an older parser or with a failed image.", {}),
            ("update", "Update everything in place", "Re-scrape all; an entry keeps its old data if its fetch fails.", {}),
            ("apply", "Re-apply overrides && fallback data", "Offline: recompute values from overrides and the trait index.", {}),
            ("rebuild", "Delete && rebuild from scratch", "Old cache is kept as animals.json.bak.", {"danger": True}),
        ]:
            row = QHBoxLayout()
            btn = self.button(text, lambda _=False, m=mode: self.run_cache(m), **kw)
            btn.setMinimumWidth(240)
            row.addWidget(btn)
            row.addWidget(muted(tip), 1)
            g.addLayout(row)
        lay.addWidget(box)

        box = QGroupBox("Request settings")
        f = QFormLayout(box)
        self.delay = QDoubleSpinBox(minimum=0.2, maximum=10.0, singleStep=0.25, decimals=2, suffix=" s")
        self.delay.setValue(float(self.settings.value("delay", 1.0)))
        self.delay.valueChanged.connect(lambda v: self.settings.setValue("delay", v))
        f.addRow("Delay between requests:", self.delay)
        self.contact = QLineEdit(self.settings.value("contact", os.environ.get("TV_CONTACT", "")),
                                 placeholderText="your email or project URL (sent in the User-Agent)")
        self.contact.textChanged.connect(lambda t: self.settings.setValue("contact", t))
        f.addRow("Contact for Wikimedia:", self.contact)
        lay.addWidget(box)

        box = QGroupBox("App's local cache (animals looked up live)")
        h = QHBoxLayout(box)
        self.user_status = muted("")
        h.addWidget(self.user_status, 1)
        h.addWidget(self.button("Clear local cache", self.clear_user, danger=True))
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def run_cache(self, mode: str):
        if mode == "rebuild" and not self.confirm(
                "Rebuild cache", "Delete the bundled cache and scrape every animal again?\n"
                                 "The current file is kept as animals.json.bak."):
            return
        self.run_script(["scripts/build_cache.py", "--mode", mode, "--yes",
                         "--delay", f"{self.delay.value():g}"], f"Cache: {mode}")

    def clear_user(self):
        if self.confirm("Clear local cache", f"Delete {user_cache_path()}?"):
            self.run_script(["scripts/build_cache.py", "--clear-user"], "Clear local cache")

    # -- Fallback tab ----------------------------------------------------------------------------
    def _fallback_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(muted(
            "When Wikipedia's text has no usable size, values come from (in order): your overrides → "
            "species-level database value → Wikidata → genus/family/order median → estimate from length. "
            "The databases below are downloaded once into sources/ and condensed into a small index "
            "(data/traits.json.gz) that ships inside the app."))
        self.src_table = QTableWidget(0, 5)
        self.src_table.setHorizontalHeaderLabels(["Source", "Covers", "Licence", "Status", "Page"])
        self.src_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.src_table.verticalHeader().setVisible(False)
        self.src_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.src_table.cellDoubleClicked.connect(self._open_source_page)
        lay.addWidget(self.src_table)
        self.index_status = muted("")
        lay.addWidget(self.index_status)
        row = QHBoxLayout()
        row.addWidget(self.button("Download missing && build index",
                                  lambda: self.run_script(["scripts/fallback_data.py"], "Fallback data"),
                                  primary=True))
        row.addWidget(self.button("Re-download everything",
                                  lambda: self.run_script(["scripts/fallback_data.py", "--download"],
                                                          "Re-download fallback data")))
        row.addWidget(self.button("Rebuild index only",
                                  lambda: self.run_script(["scripts/fallback_data.py", "--build"],
                                                          "Build trait index")))
        row.addWidget(self.button("Open sources folder", lambda: self.open_path(ROOT / "sources")))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(muted("Double-click a row to open its download page. If a download fails, save the "
                            "file from that page into sources/ under the name shown, then rebuild the index. "
                            "After building, use Cache → Re-apply to update cached animals."))
        lay.addStretch(1)
        return w

    LICENCES = {"amniote": "CC0", "amphibio": "CC BY 4.0", "animaltraits": "CC0"}

    def _open_source_page(self, row, _col):
        key = list(fallback_data.SOURCES)[row]
        QDesktopServices.openUrl(QUrl(fallback_data.SOURCES[key]["page"]))

    # -- Missing data tab ------------------------------------------------------------------------
    COLS = ["Animal", "Scientific name", "Mass (kg)", "Mass from", "Length (m)", "Length from",
            "Source / citation"]
    EDITABLE = {2, 4, 6}

    def _missing_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(muted(
            "Animals whose mass or length is missing, estimated, or only a genus/family/order average. "
            "Type a value and its source to override it. Overrides are saved to data/overrides.csv, "
            "take priority over every other source, and are bundled into the app."))
        top = QHBoxLayout()
        self.show_all = QCheckBox("Show all cached animals")
        self.show_all.toggled.connect(lambda *_: self.load_missing())
        top.addWidget(self.show_all)
        top.addStretch(1)
        self.missing_count = muted("")
        top.addWidget(self.missing_count)
        lay.addLayout(top)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(self.button("Save overrides && apply", self.save_overrides, primary=True))
        row.addWidget(self.button("Reload", self.load_missing))
        row.addWidget(self.button("Open overrides.csv", lambda: self.open_path(traits.OVERRIDES_PATH)))
        row.addStretch(1)
        lay.addLayout(row)
        return w

    @staticmethod
    def _describe(rec: dict, key: str, unit: str, origin_key: str) -> str:
        v = rec.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            return "missing"
        return f"{v:.4g} {unit} · {rec.get(origin_key) or 'Wikipedia'}"

    @staticmethod
    def _weak(origin) -> bool:
        origin = origin if isinstance(origin, str) else ""
        return not origin or "median" in origin or origin.startswith("Estimated")

    def load_missing(self):
        cache = build_cache.load(build_cache.OUT)
        ov = {r.get("title", "").lower(): r for r in traits.read_override_rows()}
        rows = []
        for rec in sorted(cache.values(), key=lambda r: r["title"]):
            key = rec["title"].lower()
            weak = (not rec.get("mass_kg") or self._weak(rec.get("mass_origin")) or not rec.get("length_m")
                    or self._weak(rec.get("length_origin")))
            if self.show_all.isChecked() or weak or key in ov:
                rows.append((rec, ov.get(key, {})))
        self.table.setRowCount(len(rows))
        for i, (rec, o) in enumerate(rows):
            vals = [rec["title"], rec.get("sci_name") or "",
                    o.get("mass_kg") or "", self._describe(rec, "mass_kg", "kg", "mass_origin"),
                    o.get("length_m") or "", self._describe(rec, "length_m", "m", "length_origin"),
                    o.get("source") or ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c not in self.EDITABLE:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if c in (3, 5) and ("missing" in str(v) or "median" in str(v) or "est" in str(v).lower()):
                        item.setForeground(QColor("#fbbf24"))
                self.table.setItem(i, c, item)
        weak_n = sum(1 for rec, _ in rows if not rec.get("mass_kg"))
        self.missing_count.setText(f"{len(rows)} shown · {weak_n} with no mass at all" if cache
                                   else "Cache is empty — build it on the Cache tab first.")

    def save_overrides(self):
        existing = {r.get("title", "").lower(): r for r in traits.read_override_rows()}
        bad = []
        for i in range(self.table.rowCount()):
            title = self.table.item(i, 0).text()
            mass, length, source = (self.table.item(i, c).text().strip() for c in (2, 4, 6))
            for label, v, upper in (("mass", mass, 1e6), ("length", length, 100.0)):
                if v:
                    try:
                        f = float(v)
                    except ValueError:
                        f = float("nan")
                    if not (0 < f <= upper):            # also rejects nan/inf
                        bad.append(f"{title}: {label} '{v}' (must be > 0 and ≤ {upper:g})")
            if mass or length:
                existing[title.lower()] = {"title": title, "scientific_name": self.table.item(i, 1).text(),
                                           "mass_kg": mass, "length_m": length, "source": source}
            else:
                existing.pop(title.lower(), None)
        if bad:
            QMessageBox.warning(self, "Invalid values", "Please fix these values:\n" + "\n".join(bad))
            return
        missing_src = [r["title"] for r in existing.values() if not r.get("source")]
        if missing_src and not self.confirm("No source given",
                                            "These overrides have no source/citation:\n"
                                            + ", ".join(missing_src) + "\n\nSave anyway?"):
            return
        try:
            traits.write_overrides(sorted(existing.values(), key=lambda r: r["title"]))
        except OSError as e:
            QMessageBox.critical(self, "Couldn't save", f"Couldn't write {traits.OVERRIDES_PATH}:\n{e}")
            return
        self.log_line(f"Saved {len(existing)} override(s) to {traits.OVERRIDES_PATH}")
        self.run_script(["scripts/build_cache.py", "--mode", "apply"], "Apply overrides",
                        after=self.load_missing)

    # -- Build tab -------------------------------------------------------------------------------
    def _build_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box = QGroupBox("Target platform")
        h = QHBoxLayout(box)
        self.os_group = QButtonGroup(self)
        for key, label in (("windows", "Windows (.exe)"), ("macos", "macOS (.app)"), ("linux", "Linux")):
            rb = QRadioButton(label)
            rb.setProperty("os", key)
            if key != HOST:
                rb.setEnabled(False)
                rb.setToolTip("PyInstaller can't cross-compile; build on that OS or use "
                              ".github/workflows/build.yml")
            rb.setChecked(key == HOST)
            self.os_group.addButton(rb)
            h.addWidget(rb)
        h.addStretch(1)
        lay.addWidget(box)
        lay.addWidget(muted(f"This machine is {HOST}. Other targets need their own OS or the GitHub "
                            "Actions workflow (defaults to Windows)."))

        box = QGroupBox("Options")
        f = QFormLayout(box)
        self.onedir = QCheckBox("One-folder build (starts faster than a single file)")
        self.install = QCheckBox("Install / upgrade requirements first")
        self.install.setChecked(self.settings.value("install", "true") == "true")
        self.install.toggled.connect(lambda v: self.settings.setValue("install", "true" if v else "false"))
        self.cache_mode = QComboBox()
        self.cache_mode.addItems(["skip", "missing", "stale", "update", "rebuild"])
        self.fallback_mode = QComboBox()
        self.fallback_mode.addItems(["auto", "refresh", "skip"])
        f.addRow(self.onedir)
        f.addRow(self.install)
        f.addRow("Cache step before building:", self.cache_mode)
        f.addRow("Fallback databases:", self.fallback_mode)
        lay.addWidget(box)

        row = QHBoxLayout()
        row.addWidget(self.button("Build executable", self.build, primary=True))
        row.addWidget(self.button("Open dist folder", lambda: self.open_path(ROOT / "dist")))
        row.addStretch(1)
        lay.addLayout(row)

        box = QGroupBox("Run && test")
        h = QHBoxLayout(box)
        h.addWidget(self.button("Run app from source", self.run_app))
        h.addWidget(self.button("Run app with --clear-cache", lambda: self.run_app("--clear-cache")))
        h.addWidget(self.button("Run tests", lambda: self.run_script(
            ["-m", "unittest", "discover", "-s", "tests", "-v"], "Tests")))
        h.addStretch(1)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def build(self):
        target = self.os_group.checkedButton().property("os")
        args = ["build.py", "--os", target, "--cache", self.cache_mode.currentText(),
                "--fallback", self.fallback_mode.currentText(), "--delay", f"{self.delay.value():g}"]
        if self.onedir.isChecked():
            args.append("--onedir")
        if not self.install.isChecked():
            args.append("--no-install")
        self.run_script(args, f"Build ({target})")

    def run_app(self, *extra):
        ok = QProcess.startDetached(sys.executable, ["run.py", *extra], str(ROOT))
        self.log_line("Started the app." if (ok[0] if isinstance(ok, tuple) else ok)
                      else "Couldn't start the app.")

    # -- process runner --------------------------------------------------------------------------
    def run_script(self, args: list[str], title: str, after=None):
        if self.proc is not None:
            return
        self._after = after
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        if self.contact.text().strip():
            env.insert("TV_CONTACT", self.contact.text().strip())
        p = QProcess(self)
        p.setProcessEnvironment(env)
        p.setWorkingDirectory(str(ROOT))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(self._read_output)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(lambda err: self.log_line(f"Process error: {err}"))
        self.proc = p
        self._set_busy(True, title)
        self.log_line(f"\n▶ {title}:  python {' '.join(args)}")
        p.start(sys.executable, args)

    def _read_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(text.replace("\r\n", "\n"))
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        for m in PROGRESS.finditer(text):
            i, n = int(m.group(1)), int(m.group(2))
            self.progress.setRange(0, n)
            self.progress.setValue(i)

    def _finished(self, code, _status):
        title = self.task_label.text().rstrip("…")
        ok = code == 0
        self.log_line(f"■ {'Done' if ok else f'Finished with exit code {code}'}.")
        self.proc.deleteLater()
        self.proc = None
        self._set_busy(False, f"{title.removeprefix('Running: ')} — {'done' if ok else 'see log'}.")
        traits.reload()
        self.refresh()
        if self._after and ok:
            self._after()
        self._after = None

    def cancel_task(self):
        if self.proc is not None:
            self.log_line("Cancelling… (cache progress is saved every few animals)")
            self.proc.kill()

    def _set_busy(self, busy: bool, text: str):
        for b in self.actions:
            b.setEnabled(not busy)
        self.cancel.setEnabled(busy)
        self.task_label.setText(f"Running: {text}…" if busy else text)
        if busy:
            self.progress.setRange(0, 0)       # indeterminate until "[i/N]" appears
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)

    def log_line(self, text: str):
        self.log.appendPlainText(text)

    def open_path(self, path: Path):
        if path.suffix:                       # a file: open it if it exists, else its folder
            target = path if path.exists() else path.parent
        else:
            path.mkdir(parents=True, exist_ok=True)
            target = path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # -- status ----------------------------------------------------------------------------------
    def refresh(self):
        try:
            self._refresh()
        except Exception as e:  # noqa: BLE001 - a status glitch must not take the manager down
            self.log_line(f"Couldn't read status: {type(e).__name__}: {e}")

    def _refresh(self):
        try:
            names = build_cache.read_list(build_cache.LIST)
        except build_cache.CacheError as e:
            names = []
            self.log_line(f"warning: {e}")
        cache = build_cache.load(build_cache.OUT)
        have = [n for n in names if build_cache.find_key(cache, n)]
        stale = sum(1 for n in have if build_cache.is_stale(cache[build_cache.find_key(cache, n)]))
        no_mass = sum(1 for r in cache.values() if not r.get("mass_kg"))
        idx = traits.index()
        self.cache_status.setText(
            f"<b>{len(have)}/{len(names)}</b> listed animals cached &nbsp;·&nbsp; "
            f"<b>{len(names) - len(have)}</b> missing &nbsp;·&nbsp; <b>{stale}</b> outdated &nbsp;·&nbsp; "
            f"<b>{no_mass}</b> with no mass<br>"
            f"<span style='color:#94a3b8'>Fallback index: "
            + (f"{len(idx['species']):,} species (built {idx.get('built', '?')})" if idx["species"]
               else "not built — see the Fallback data tab") + "</span>")
        user = build_cache.load(user_cache_path())
        self.user_status.setText(f"{len(user)} animals in {user_cache_path()}")

        self.src_table.setRowCount(len(fallback_data.SOURCES))
        for i, (key, src) in enumerate(fallback_data.SOURCES.items()):
            path = fallback_data.RAW / src["file"]
            state = f"✓ {path.stat().st_size / 1e6:.1f} MB" if path.exists() else f"missing ({src['file']})"
            for c, v in enumerate([key, src["covers"], self.LICENCES.get(key, ""), state, src["page"]]):
                self.src_table.setItem(i, c, QTableWidgetItem(v))
        self.src_table.resizeColumnsToContents()
        self.src_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        counts = idx.get("counts", {})
        self.index_status.setText(
            "Index: " + (f"{len(idx['species']):,} species, {len(idx['genus']):,} genera, "
                         f"{len(idx['family']):,} families · rows per source: "
                         + ", ".join(f"{k} {v:,}" for k, v in counts.items())
                         if idx["species"] else "not built"))
        if self.tabs.currentIndex() == 2:
            self.load_missing()


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Terminal Velocity Manager")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    install_excepthook(lambda title, text: QMessageBox.warning(app.activeWindow(), title, text))
    win = Manager()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
