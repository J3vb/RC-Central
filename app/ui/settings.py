"""Settings tab: app preferences (persisted in QSettings) and the log viewer."""

import threading

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import paths, updater
from app.ui.common import (
    _DARK_MODE_DEFAULT, _DARK_MODE_KEY, _STARTUP_CHECK_DEFAULT, _STARTUP_CHECK_KEY, _settings,
)
from app.ui.log import LogTab
from app.ui.theme import apply_theme


class SettingsTab(QWidget):
    """Two sub-tabs: Preferences (QSettings-backed toggles) and the live Log."""

    _check_done = Signal()

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

        self.check_btn = QPushButton("Check for updates now")
        self.check_btn.clicked.connect(self._check_updates)
        self._check_done.connect(lambda: self.check_btn.setEnabled(True))

        open_folder_btn = QPushButton("Open data folder")
        open_folder_btn.clicked.connect(self._open_data_folder)

        prefs = QWidget()
        prefs_layout = QVBoxLayout(prefs)
        prefs_layout.addWidget(self.dark_toggle)
        prefs_layout.addWidget(self.update_toggle)
        prefs_layout.addWidget(self.check_btn)
        prefs_layout.addWidget(open_folder_btn)
        prefs_layout.addStretch(1)

        self.log = LogTab()

        self.subtabs = QTabWidget()
        self.subtabs.addTab(prefs, "Preferences")
        self.subtabs.addTab(self.log, "Log")

        layout = QVBoxLayout(self)
        layout.addWidget(self.subtabs)

    def _on_dark_toggled(self, checked: bool) -> None:
        apply_theme(QApplication.instance(), checked)
        _settings().setValue(_DARK_MODE_KEY, checked)

    def _on_update_toggled(self, checked: bool) -> None:
        _settings().setValue(_STARTUP_CHECK_KEY, checked)

    def _check_updates(self) -> None:
        self.check_btn.setEnabled(False)
        updater.log.info("manual update check requested from Settings")
        win = self.window()  # grab on the GUI thread; the worker only emits its signal

        def work():
            try:
                if updater.fetch_update(force=True) and hasattr(win, "update_ready"):
                    win.update_ready.emit(updater.staged_version() or "")
            finally:
                self._check_done.emit()  # re-enable the button on the GUI thread

        threading.Thread(target=work, daemon=True).start()

    def _open_data_folder(self) -> None:
        folder = paths.data_dir()
        folder.mkdir(parents=True, exist_ok=True)  # may not exist on a fresh run
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
