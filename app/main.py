"""RC Central - install and launch RC drift setup tools, plus gearing + garage."""

import json
import logging
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QFontMetrics, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import (
    __version__,
    backup,
    catalog,
    garage,
    gearing,
    installer,
    launcher,
    logsetup,
    paths,
    updater,
)


from app.ui.common import (  # noqa: F401
    _ACCENT, _CATEGORY_LABELS, _DARK_MODE_DEFAULT, _DARK_MODE_KEY,
    _DownloadTab, _InstallSignals, _SETTINGS_APP, _SETTINGS_ORG,
    _STARTUP_CHECK_DEFAULT, _STARTUP_CHECK_KEY, _asset_path, _is_pdf,
    _is_software, _link_button, _settings, _show_status, app_icon,
)


from app.ui.tools import ToolsTab  # noqa: F401


from app.ui.manuals import ManualsTab  # noqa: F401


from app.ui.gear import GearTab, _GearChartDialog  # noqa: F401


from app.ui.garage_tab import GarageTab, _CompareDialog  # noqa: F401


_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _line_level(line: str) -> int:
    """Recover a record's level from a formatted line (see logsetup.FORMAT).

    Both the preloaded buffer and the live stream arrive as formatted strings, so
    parsing the level field keeps the tab's filter uniform across the two.
    """
    parts = line.split(" · ", 3)
    if len(parts) >= 2:
        return _LEVEL_NAMES.get(parts[1].strip(), logging.INFO)
    return logging.INFO


class QtLogBridge(QObject):
    """Carries a formatted record from any thread onto the GUI thread.

    A logging.Handler can't itself be a QObject, so the handler holds a bridge and
    emits its signal; Qt's queued connection marshals the string across threads —
    which is what makes it safe for the updater's background thread to log.
    """

    record = Signal(str)


class QtLogHandler(logging.Handler):
    """Root-logger handler that forwards each record to a QtLogBridge signal."""

    def __init__(self, bridge: QtLogBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.record.emit(self.format(record))
        except Exception:
            self.handleError(record)


# (setting, action if understeering, action if oversteering) — transcribed from the
# community "Drift RC Chassis Tuning Effects" chart. The chart's two conditional
# "Track Width (rear)" rows are split into low/high-speed rows with plain values.
_TUNING_ROWS: list[tuple[str, str, str]] = [
    ("Ride Height (front)", "Decrease", "Increase"),
    ("Ride Height (rear)", "Increase", "Decrease"),
    ("Ackerman", "Increase angle", "Decrease angle"),
    ("Front Toe", "Increase toe-out", "Decrease toe-out"),
    ("Rear Toe", "Decrease toe-in", "Increase toe-in"),
    ("Caster", "Decrease angle", "Increase angle"),
    ("Track Width (front)", "Decrease", "Increase"),
    ("Track Width (rear — low speed)", "Decrease", "Increase"),
    ("Track Width (rear — high speed)", "Increase", "Decrease"),
    ("Lower Shock Position (front)", "Move inward", "Move outward"),
    ("Lower Shock Position (rear)", "Move outward", "Move inward"),
    ("Upper Shock Position (rear)", "Make more vertical", "Make more laid down"),
    ("Springs (front)", "Install softer", "Install stiffer"),
    ("Springs (rear)", "Install stiffer", "Install softer"),
    (
        "Shock Oil/Damping (front)",
        "Install thinner (or larger piston holes)",
        "Install thicker (or smaller piston holes)",
    ),
    (
        "Shock Oil/Damping (rear)",
        "Install thicker (or smaller piston holes)",
        "Install thinner (or larger piston holes)",
    ),
    (
        "Front Camber Link/Roll",
        "Longer link and/or more parallel to lower arm",
        "Shorter link and/or move axis compared to lower arm",
    ),
    ("Rear Diff", "Tighten", "Loosen"),
]


# What each chassis setting physically does — tooltips for the chart's Setting
# column. Front/rear variants of one concept share a text via the _TIP_* locals.
_TIP_RIDE = (
    "Chassis height over the ground at that end. Lowering an end generally adds "
    "grip and reduces body roll at that end; raising does the opposite."
)
_TIP_TRACK = (
    "Distance between left/right contact patches at that end. Wider resists roll "
    "and softens weight transfer at that end; effects differ with corner speed."
)
_TIP_LOWER_SHOCK = (
    "Moving the shock's lower mount changes its lean. More laid-down = softer, "
    "more progressive action at that end; more upright = firmer and more direct."
)
_TIP_SPRINGS = (
    "Roll stiffness at that end. On low-grip drift surfaces the stiffer end "
    "generally slides first."
)
_TIP_OIL = (
    "How fast weight transfers onto that end. Thicker oil slows the transfer "
    "(calmer transitions); thinner speeds it up (snappier response)."
)
_TUNING_TIPS: dict[str, str] = {
    "Ride Height (front)": _TIP_RIDE,
    "Ride Height (rear)": _TIP_RIDE,
    "Ackerman": (
        "How much more the inside wheel steers than the outside wheel in a turn. "
        "More Ackerman sharpens low-speed turn-in; less keeps the wheels more "
        "parallel for smoother high-angle steering."
    ),
    "Front Toe": (
        "Angle of the front wheels vs. the chassis centerline. Toe-out sharpens "
        "initial turn-in; toe-in calms it."
    ),
    "Rear Toe": (
        "Rear toe-in adds rear stability and forward traction; reducing it frees "
        "the rear to rotate."
    ),
    "Caster": (
        "Backward lean of the steering axis. More caster adds straight-line "
        "stability and camber gain while steering; less makes steering more direct."
    ),
    "Track Width (front)": _TIP_TRACK,
    "Track Width (rear — low speed)": _TIP_TRACK,
    "Track Width (rear — high speed)": _TIP_TRACK,
    "Lower Shock Position (front)": _TIP_LOWER_SHOCK,
    "Lower Shock Position (rear)": _TIP_LOWER_SHOCK,
    "Upper Shock Position (rear)": (
        "Same lever as the lower mount: vertical shocks act firmer and more "
        "direct, laid-down shocks act softer initially."
    ),
    "Springs (front)": _TIP_SPRINGS,
    "Springs (rear)": _TIP_SPRINGS,
    "Shock Oil/Damping (front)": _TIP_OIL,
    "Shock Oil/Damping (rear)": _TIP_OIL,
    "Front Camber Link/Roll": (
        "Link length and angle set the roll center and camber gain — how the tire "
        "leans as the chassis rolls. Longer/more parallel links smooth the camber "
        "change and add grip."
    ),
    "Rear Diff": (
        "How tightly the rear wheels are coupled. Tighter (toward spool) drives "
        "both rears equally for predictable rotation; looser lets them "
        "differentiate for more forward bite."
    ),
}


# (WT, approx. cSt) — the commonly circulated shock-oil conversion; scales
# differ by brand, so the tab carries an "approximate" caption.
_OIL_ROWS: list[tuple[str, str]] = [
    ("10", "100"),
    ("15", "150"),
    ("20", "200"),
    ("25", "275"),
    ("30", "350"),
    ("35", "425"),
    ("40", "500"),
    ("45", "575"),
    ("50", "650"),
    ("60", "800"),
]


# (symptom, gyro adjustment) for drift gyros.
_GYRO_ROWS: list[tuple[str, str]] = [
    ("Tail wags / oscillates on straights", "Lower gain"),
    ("Snap-spins on throttle transitions", "Increase gain"),
    ("Counter-steer too slow, spins before catching", "Increase gain (or faster servo response)"),
    ("Steering fights your inputs, feels robotic", "Lower gain"),
    ("Won't hold deep angle, self-straightens", "Lower gain"),
    ("Wanders at speed, needs constant correction", "Raise gain slightly"),
]


class _ChassisGuide(QWidget):
    """The understeer/oversteer chart with click-to-expand setting explainers.

    Click a setting row and its explanation drops in as a spanned row directly
    beneath it (one open at a time; clicking another setting moves it there).
    Implemented on QTableWidget with insertRow/removeRow — QTreeWidget branch
    expand/collapse cycles live-lock Qt's UIA accessibility bridge and freeze
    the GUI under screen readers / UI automation (PySide6 6.11.1, 2026-07-16).
    """

    def __init__(self):
        super().__init__()
        self._open_row: int | None = None  # table index of the open setting row, if any

        self.table = QTableWidget(len(_TUNING_ROWS), 3)
        self.table.setHorizontalHeaderLabels(("Setting", "If understeering", "If oversteering"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)  # camber-link cells are long
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_TUNING_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem("▸ " + text if col == 0 else text)
                if col == 0:
                    item.setToolTip(_TUNING_TIPS[text])
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self.table.cellClicked.connect(self._toggle_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter settings…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        # Same-parent QRadioButtons are auto-exclusive; no QButtonGroup needed.
        self.radio_both = QRadioButton("Both")
        self.radio_under = QRadioButton("Understeering")
        self.radio_over = QRadioButton("Oversteering")
        self.radio_both.setChecked(True)  # before connect: table paint not needed yet
        for radio in (self.radio_both, self.radio_under, self.radio_over):
            radio.toggled.connect(self._highlight)

        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("Symptom:"))
        controls.addWidget(self.radio_both)
        controls.addWidget(self.radio_under)
        controls.addWidget(self.radio_over)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drift chassis tuning effects — click a setting for details"))
        layout.addLayout(controls)
        layout.addWidget(self.table)

    def _setting_name(self, row: int) -> str:
        return self.table.item(row, 0).text()[2:]  # strip the "▸ "/"▾ " prefix

    def _toggle_row(self, row: int, column: int = 0) -> None:
        if self._open_row is not None and row == self._open_row + 1:
            return  # clicks on the explanation row itself do nothing
        reopen = None if row == self._open_row else row
        if self._open_row is not None:
            was = self._open_row
            self.table.removeRow(was + 1)
            self.table.item(was, 0).setText("▸ " + self._setting_name(was))
            self._open_row = None
            if reopen is not None and reopen > was:
                reopen -= 1  # rows below the removed explanation shifted up
        if reopen is None:
            return
        name = self._setting_name(reopen)
        exp = QTableWidgetItem(_TUNING_TIPS[name])
        font = exp.font()
        font.setItalic(True)
        exp.setFont(font)
        self.table.insertRow(reopen + 1)
        self.table.setItem(reopen + 1, 0, exp)
        self.table.setSpan(reopen + 1, 0, 1, 3)
        self.table.item(reopen, 0).setText("▾ " + name)
        self._open_row = reopen
        self._fit_explanation()

    def _fit_explanation(self) -> None:
        # the delegate paints wrapped text across the span but sizes the row as
        # a single line, so compute the wrapped height ourselves
        if self._open_row is None:
            return
        row = self._open_row + 1
        item = self.table.item(row, 0)
        metrics = QFontMetrics(item.font())
        width = self.table.viewport().width() - 24
        rect = metrics.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, item.text())
        self.table.setRowHeight(row, rect.height() + 12)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_explanation()  # re-wrap the open explanation at the new width

    def _apply_filter(self, text: str) -> None:
        if self._open_row is not None:
            self._toggle_row(self._open_row)  # close it; rows are 1:1 settings again
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, needle not in self._setting_name(row).lower())

    def _highlight(self, checked: bool) -> None:
        if not checked:  # a radio switch fires toggled twice (old off, new on); paint once
            return
        col_on = 1 if self.radio_under.isChecked() else 2 if self.radio_over.isChecked() else None
        for row in range(self.table.rowCount()):
            for col in (1, 2):
                item = self.table.item(row, col)
                if item is None:  # the spanned explanation row has no symptom cells
                    continue
                if col == col_on:
                    item.setBackground(QColor(_ACCENT))
                    item.setForeground(QColor("white"))  # readable on accent in both themes
                else:
                    # clear back to theme defaults (None removes the explicit brush)
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)


class _OilGuide(QWidget):
    """Shock oil WT ↔ cSt conversion reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_OIL_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("WT", "approx. cSt"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_OIL_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

        note = QLabel("Approximate — scales differ by brand; check your oil maker's own chart.")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table)


class _GyroGuide(QWidget):
    """Drift gyro symptom → gain adjustment reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_GYRO_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("Symptom", "Adjustment"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_GYRO_ROWS):
            for col, text in enumerate(texts):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeRowsToContents()

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)


class _TuningLog(QWidget):
    """Per-car tuning notes, stored as kind="Tuning" entries in the car's garage log.

    Reuses the Garage's log schema untouched, so entries also appear in the Garage
    tab's log table and ride along with backup/restore/export for free. Add/Delete
    load the car fresh from disk so edits made meanwhile in the Garage tab are
    never clobbered by a stale dict held here.
    """

    def __init__(self):
        super().__init__()
        self._shown: list[dict] = []  # entries behind the table rows, newest first

        self.car_combo = QComboBox()
        self.hint = QLabel("Create a car in the Garage first.")
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. front springs softer → better turn-in")
        self.add_btn = QPushButton("Add")
        self.delete_btn = QPushButton("Delete selected")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("Date", "Note"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        # connect only now that self.table exists (same trap as _CompareDialog)
        self.car_combo.currentIndexChanged.connect(self._render)
        self.add_btn.clicked.connect(self._add)
        self.note.returnPressed.connect(self._add)
        self.delete_btn.clicked.connect(self._delete)

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.note, 1)
        entry_row.addWidget(self.add_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.car_combo)
        layout.addWidget(self.hint)
        layout.addLayout(entry_row)
        layout.addWidget(self.table)
        layout.addWidget(self.delete_btn)
        self._reload_cars()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # cars are created/deleted on the Garage tab; refresh on every switch here
        self._reload_cars()
        super().showEvent(event)

    def _reload_cars(self) -> None:
        current = self.car_combo.currentData()
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        for car in garage.list_cars():
            self.car_combo.addItem(car.get("name", "Unnamed"), car["id"])
        idx = self.car_combo.findData(current)
        self.car_combo.setCurrentIndex(max(0, idx))  # keep pick; else first car
        self.car_combo.blockSignals(False)
        has_cars = self.car_combo.count() > 0
        for widget in (self.car_combo, self.note, self.add_btn, self.delete_btn):
            widget.setEnabled(has_cars)
        self.hint.setVisible(not has_cars)
        self._render()

    def _render(self) -> None:
        car = garage.load_car(self.car_combo.currentData() or "") or {}
        self._shown = sorted(
            (e for e in car.get("log", []) if e.get("kind") == "Tuning"),
            key=lambda e: e.get("date", ""),
            reverse=True,
        )
        self.table.setRowCount(len(self._shown))
        for row, entry in enumerate(self._shown):
            date = str(entry.get("date", ""))[:10]  # YYYY-MM-DD from the ISO stamp
            self.table.setItem(row, 0, QTableWidgetItem(date))
            self.table.setItem(row, 1, QTableWidgetItem(entry.get("note", "")))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _add(self) -> None:
        note = self.note.text().strip()
        car_id = self.car_combo.currentData()
        if not note or not car_id:
            return
        car = garage.load_car(car_id)
        if car is None:  # deleted on the Garage tab since the picker was filled
            self._reload_cars()
            return
        car.setdefault("log", []).append(garage.new_log_entry("Tuning", note))
        garage.save_car(car)
        self.note.clear()
        self._render()

    def _delete(self) -> None:
        row = self.table.currentRow()
        car_id = self.car_combo.currentData()
        if row < 0 or row >= len(self._shown) or not car_id:
            return
        entry_id = self._shown[row].get("id")
        car = garage.load_car(car_id)
        if car is None:
            self._reload_cars()
            return
        car["log"] = [e for e in car.get("log", []) if e.get("id") != entry_id]
        garage.save_car(car)
        self._render()


class TuningTab(QWidget):
    """Tuning references in sub-tabs: chassis chart, shock oil, gyro, my log."""

    def __init__(self):
        super().__init__()
        self.subtabs = QTabWidget()
        self.chassis = _ChassisGuide()
        self.subtabs.addTab(self.chassis, "Chassis")
        self.oil = _OilGuide()
        self.subtabs.addTab(self.oil, "Shock Oil")
        self.gyro = _GyroGuide()
        self.subtabs.addTab(self.gyro, "Gyro")
        self.mylog = _TuningLog()
        self.subtabs.addTab(self.mylog, "My Log")
        layout = QVBoxLayout(self)
        layout.addWidget(self.subtabs)


class LogTab(QWidget):
    """Live application log: preloaded from the in-memory buffer, then streaming.

    A QtLogHandler on the root logger pushes each new record here, so records
    emitted on the updater's background thread arrive safely on the GUI thread.
    """

    _MAX_RECORDS = 5000  # bound memory on a long-running session
    _FILTERS = (
        ("All", logging.NOTSET),
        ("Info+", logging.INFO),
        ("Warnings+", logging.WARNING),
    )

    _check_done = Signal()

    def __init__(self):
        super().__init__()
        self._records: list[str] = []
        self._min_level = logging.NOTSET

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(self._MAX_RECORDS)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        self.check_btn = QPushButton("Check for updates now")
        self.check_btn.clicked.connect(self._check_updates)
        open_btn = QPushButton("Open log file")
        open_btn.clicked.connect(self._open_log_file)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)

        self.level_filter = QComboBox()
        for label, _level in self._FILTERS:
            self.level_filter.addItem(label)
        self.level_filter.currentIndexChanged.connect(self._on_filter_changed)

        controls = QHBoxLayout()
        controls.addWidget(self.check_btn)
        controls.addWidget(open_btn)
        controls.addWidget(copy_btn)
        controls.addWidget(clear_btn)
        controls.addStretch(1)
        controls.addWidget(QLabel("Show:"))
        controls.addWidget(self.level_filter)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view)

        self._check_done.connect(lambda: self.check_btn.setEnabled(True))

        # Bridge live records onto the GUI thread. Parent the bridge so it dies
        # with this widget, and drop the root handler when we're destroyed so a
        # stray record can never reach a deleted bridge.
        self._bridge = QtLogBridge(self)
        self._bridge.record.connect(self._append_record)
        self._handler = QtLogHandler(self._bridge)
        self._handler.setFormatter(
            logging.Formatter(logsetup.FORMAT, datefmt=logsetup.DATE_FORMAT)
        )
        # Snapshot the buffer before attaching, so no record is both preloaded
        # and delivered live.
        self._records.extend(logsetup.buffered_records())
        root = logging.getLogger()
        root.addHandler(self._handler)
        handler = self._handler
        self.destroyed.connect(lambda: root.removeHandler(handler))

        self._rerender()

    def _passes(self, line: str) -> bool:
        return _line_level(line) >= self._min_level

    def _rerender(self) -> None:
        self.view.setPlainText(
            "\n".join(line for line in self._records if self._passes(line))
        )
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_filter_changed(self, index: int) -> None:
        self._min_level = self._FILTERS[index][1]
        self._rerender()

    def _append_record(self, line: str) -> None:
        self._records.append(line)
        if len(self._records) > self._MAX_RECORDS:
            del self._records[: len(self._records) - self._MAX_RECORDS]
        if self._passes(line):
            self.view.appendPlainText(line)
            self._scroll_to_end()

    def _check_updates(self) -> None:
        self.check_btn.setEnabled(False)
        updater.log.info("manual update check requested from the Log tab")
        win = self.window()  # grab on the GUI thread; the worker only emits its signal

        def work():
            try:
                if updater.fetch_update(force=True) and hasattr(win, "update_ready"):
                    win.update_ready.emit(updater.staged_version() or "")
            finally:
                self._check_done.emit()  # re-enable the button on the GUI thread

        threading.Thread(target=work, daemon=True).start()

    def _open_log_file(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(logsetup.LOG_FILE))):
            QMessageBox.information(
                self, "Log file", f"The log file is at:\n{logsetup.LOG_FILE}"
            )

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())

    def _clear(self) -> None:
        self._records.clear()
        self.view.clear()


from app.ui.theme import _make_palette, _dark_palette, _light_palette, apply_theme  # noqa: F401


class SettingsTab(QWidget):
    """App preferences, all persisted in QSettings: theme, startup check, data folder."""

    def __init__(self):
        super().__init__()
        settings = _settings()

        self.dark_toggle = QCheckBox("Dark mode")
        self.dark_toggle.setChecked(settings.value(_DARK_MODE_KEY, _DARK_MODE_DEFAULT, type=bool))
        self.dark_toggle.toggled.connect(self._on_dark_toggled)

        self.update_toggle = QCheckBox("Check for updates on startup")
        self.update_toggle.setChecked(
            settings.value(_STARTUP_CHECK_KEY, _STARTUP_CHECK_DEFAULT, type=bool)
        )
        self.update_toggle.toggled.connect(self._on_update_toggled)

        open_folder_btn = QPushButton("Open data folder")
        open_folder_btn.clicked.connect(self._open_data_folder)

        layout = QVBoxLayout(self)
        layout.addWidget(self.dark_toggle)
        layout.addWidget(self.update_toggle)
        layout.addWidget(open_folder_btn)
        layout.addStretch(1)

    def _on_dark_toggled(self, checked: bool) -> None:
        apply_theme(QApplication.instance(), checked)
        _settings().setValue(_DARK_MODE_KEY, checked)

    def _on_update_toggled(self, checked: bool) -> None:
        _settings().setValue(_STARTUP_CHECK_KEY, checked)

    def _open_data_folder(self) -> None:
        folder = paths.data_dir()
        folder.mkdir(parents=True, exist_ok=True)  # may not exist on a fresh run
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class MainWindow(QMainWindow):
    # Emitted (with the ready version tag) when a background check has staged an
    # update. Carried across threads by Qt's queued connection, so the check can
    # run off the GUI thread and still light up the banner safely.
    update_ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"RC Central v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(760, 500)

        # An update is only swapped in on quit once the user has explicitly asked
        # for it via the banner; dismissing leaves the download unused.
        self.update_consented = False

        # One catalog fetch, shared by both tabs that render it, so startup makes a
        # single (blocking) network round-trip and both tabs show the same snapshot.
        tools = catalog.load_catalog()

        self.tabs = QTabWidget()
        self.gear_tab = GearTab()
        self.garage_tab = GarageTab(on_open_in_calc=self._open_in_calc)
        self.tuning_tab = TuningTab()
        self.log_tab = LogTab()
        self.manuals_tab = ManualsTab(tools)

        tabs: list[tuple[QWidget, str]] = []
        # The Tools tab installs and launches Windows-only vendor programmers, so
        # it exists only on Windows (x64 and ARM, where the x86/x64 exes run under
        # OS emulation). Elsewhere the app is the cross-platform gearing + garage.
        self.tools_tab: ToolsTab | None = None
        if sys.platform == "win32":
            self.tools_tab = ToolsTab(tools)
            tabs.append((self.tools_tab, "Tools"))
        tabs += [
            (self.manuals_tab, "Manuals"),
            (self.garage_tab, "Garage"),
            (self.gear_tab, "Gear Calculator"),
            (self.tuning_tab, "Tuning"),
            (self.log_tab, "Log"),
            # Settings is appended LAST so every existing tab keeps its index — the
            # saved-tab restore below clamps but doesn't remap, so inserting mid-list
            # would restore the wrong tab.
            (SettingsTab(), "Settings"),
        ]
        for widget, label in tabs:
            self.tabs.addTab(widget, label)

        self.setCentralWidget(self.tabs)

        # A dismissable "update ready" bar above the tabs, hidden until a check
        # reports one is staged. A native top QToolBar keeps QMainWindow ownership
        # of the banner (a hand-rolled central-widget wrapper trips PySide6's
        # teardown), and its widgets are added directly — no nested container.
        self._build_update_banner()

        self.update_ready.connect(self._show_update_banner)

        # back-compat: existing tests (and any external callers) reach the table
        # here — present only when the Tools tab is.
        if self.tools_tab is not None:
            self.table = self.tools_tab.table

        # Restore window size and last tab from the previous run. The Tools tab is
        # Windows-only, so a tab index saved on Windows can exceed the count here —
        # clamp it into range rather than trust it.
        settings = _settings()
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        tab = int(settings.value("tab", 0))
        self.tabs.setCurrentIndex(max(0, min(tab, self.tabs.count() - 1)))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        settings = _settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("tab", self.tabs.currentIndex())
        super().closeEvent(event)

    def _build_update_banner(self) -> None:
        banner = QToolBar("Update")
        banner.setMovable(False)
        banner.setFloatable(False)
        banner.setStyleSheet(
            f"QToolBar {{ background: {_ACCENT}; border: none; padding: 4px 8px; spacing: 8px; }}"
            "QToolBar QLabel { color: white; }"
        )
        self.update_label = QLabel()
        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        restart_btn = QPushButton("Restart && update")
        restart_btn.clicked.connect(self._restart_to_update)
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.clicked.connect(self._dismiss_update)
        banner.addWidget(self.update_label)
        banner.addWidget(spacer)
        banner.addWidget(restart_btn)
        banner.addWidget(dismiss_btn)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, banner)
        banner.hide()
        self.update_banner = banner

    def _show_update_banner(self, version: str) -> None:
        pretty = version or "A new version"
        self.update_label.setText(f"{pretty} is ready to install.")
        self.update_banner.show()

    def _dismiss_update(self) -> None:
        # Consent stays False, so nothing is swapped in on quit; the next launch
        # re-checks and can offer it again.
        self.update_banner.hide()

    def _restart_to_update(self) -> None:
        self.update_consented = True
        # End the event loop; main() then applies the update and relaunches. quit()
        # (rather than close()) leaves the window object intact for main() to read.
        QApplication.quit()

    def _open_in_calc(self, car: dict) -> None:
        self.gear_tab.load_from_car(car)
        self.tabs.setCurrentWidget(self.gear_tab)


def main() -> None:
    logsetup.init()  # first line: nothing logged after here should be missed
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    settings = _settings()
    apply_theme(app, settings.value(_DARK_MODE_KEY, _DARK_MODE_DEFAULT, type=bool))
    updater.cleanup()
    win = MainWindow()
    win.show()

    def check_for_update():
        if updater.fetch_update():
            win.update_ready.emit(updater.staged_version() or "")

    if settings.value(_STARTUP_CHECK_KEY, _STARTUP_CHECK_DEFAULT, type=bool):
        threading.Thread(target=check_for_update, daemon=True).start()
    code = app.exec()
    # Only swap the binary in when the user asked for it from the banner, then
    # relaunch into the new version so "Restart & update" actually restarts.
    if win.update_consented:
        updater.apply_pending()
        if getattr(sys, "frozen", False):
            try:
                updater.relaunch()  # resets the PyInstaller env so the child gets a fresh _MEI
            except OSError:
                updater.log.exception("could not relaunch after applying the update")
    sys.exit(code)


if __name__ == "__main__":
    main()
