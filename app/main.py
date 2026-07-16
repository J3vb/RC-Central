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


from app.ui.tuning import (  # noqa: F401
    TuningTab, _ChassisGuide, _GyroGuide, _GYRO_ROWS, _OIL_ROWS, _OilGuide,
    _TIP_LOWER_SHOCK, _TIP_OIL, _TIP_RIDE, _TIP_SPRINGS, _TIP_TRACK,
    _TUNING_ROWS, _TUNING_TIPS, _TuningLog,
)


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
