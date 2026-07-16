"""Settings tab: app preferences persisted in QSettings."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton, QVBoxLayout, QWidget

from app import paths
from app.ui.common import (
    _DARK_MODE_DEFAULT, _DARK_MODE_KEY, _STARTUP_CHECK_DEFAULT, _STARTUP_CHECK_KEY, _settings,
)
from app.ui.theme import apply_theme


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
