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


from app.ui.log import LogTab, QtLogBridge, QtLogHandler, _LEVEL_NAMES, _line_level  # noqa: F401


from app.ui.tuning import (  # noqa: F401
    TuningTab, _ChassisGuide, _GyroGuide, _GYRO_ROWS, _OIL_ROWS, _OilGuide,
    _TIP_LOWER_SHOCK, _TIP_OIL, _TIP_RIDE, _TIP_SPRINGS, _TIP_TRACK,
    _TUNING_ROWS, _TUNING_TIPS, _TuningLog,
)


from app.ui.theme import _make_palette, _dark_palette, _light_palette, apply_theme  # noqa: F401


from app.ui.settings import SettingsTab  # noqa: F401


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
