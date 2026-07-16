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


from app.ui.window import MainWindow  # noqa: F401


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
