"""Settings tab: app preferences (persisted in QSettings) and the log viewer."""

import threading

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import __version__, paths, updater
from app.ui.common import (
    _accent, _ACCENT_KEY, _DARK_MODE_DEFAULT, _DARK_MODE_KEY, _GAP, _MARGIN,
    _section_label, _show_status, _STARTUP_CHECK_DEFAULT, _STARTUP_CHECK_KEY, _settings,
)
from app.ui.log import LogTab
from app.ui.theme import apply_theme


class SettingsTab(QWidget):
    """Two sub-tabs: Preferences (QSettings-backed toggles) and the live Log."""

    _check_done = Signal(str)  # outcome: "staged" | "current" | "failed"

    def __init__(self):
        super().__init__()
        settings = _settings()

        self.dark_toggle = QCheckBox("Dark mode")
        self.dark_toggle.setChecked(settings.value(_DARK_MODE_KEY, _DARK_MODE_DEFAULT, type=bool))
        self.dark_toggle.toggled.connect(self._on_dark_toggled)

        self.accent_btn = QPushButton("Accent colour…")
        self.accent_btn.clicked.connect(self._pick_accent)
        self.accent_reset_btn = QPushButton("Reset")
        self.accent_reset_btn.clicked.connect(self._reset_accent)
        accent_row = QHBoxLayout()
        accent_row.setSpacing(_GAP)
        accent_row.addWidget(self.accent_btn, 1)
        accent_row.addWidget(self.accent_reset_btn)

        self.update_toggle = QCheckBox("Check for updates on startup")
        self.update_toggle.setChecked(
            settings.value(_STARTUP_CHECK_KEY, _STARTUP_CHECK_DEFAULT, type=bool)
        )
        self.update_toggle.toggled.connect(self._on_update_toggled)

        self.check_btn = QPushButton("Check for updates now")
        self.check_btn.clicked.connect(self._check_updates)
        self._check_done.connect(self._on_check_done)

        open_folder_btn = QPushButton("Open data folder")
        open_folder_btn.clicked.connect(self._open_data_folder)

        self.about_btn = QPushButton("About RC Central…")
        self.about_btn.clicked.connect(self._about)

        # A capped settled column (settings-dialog convention) instead of five
        # widgets stretched across the whole pane, grouped under section headers.
        prefs = QWidget()
        prefs.setMaximumWidth(420)
        prefs_layout = QVBoxLayout(prefs)
        prefs_layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        prefs_layout.setSpacing(_GAP)
        prefs_layout.addWidget(_section_label("Appearance"))
        prefs_layout.addWidget(self.dark_toggle)
        prefs_layout.addLayout(accent_row)
        prefs_layout.addSpacing(_GAP)
        prefs_layout.addWidget(_section_label("Updates"))
        prefs_layout.addWidget(self.update_toggle)
        prefs_layout.addWidget(self.check_btn)
        prefs_layout.addSpacing(_GAP)
        prefs_layout.addWidget(_section_label("Data"))
        prefs_layout.addWidget(open_folder_btn)
        prefs_layout.addWidget(self.about_btn)
        prefs_layout.addStretch(1)

        self.log = LogTab()

        self.subtabs = QTabWidget()
        self.subtabs.addTab(prefs, "Preferences")
        self.subtabs.addTab(self.log, "Log")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.addWidget(self.subtabs)

    def _on_dark_toggled(self, checked: bool) -> None:
        apply_theme(QApplication.instance(), checked)
        _settings().setValue(_DARK_MODE_KEY, checked)

    def _pick_accent(self) -> None:
        color = QColorDialog.getColor(QColor(_accent()), self, "Accent colour")
        if not color.isValid():  # dialog cancelled
            return
        _settings().setValue(_ACCENT_KEY, color.name())
        self._reapply_accent()

    def _reset_accent(self) -> None:
        _settings().remove(_ACCENT_KEY)  # absent key = default accent
        self._reapply_accent()

    def _reapply_accent(self) -> None:
        """Restyle live: apply_theme rebuilds palette + stylesheet (everything
        accent-coloured is palette(highlight)-driven), and the update banner —
        which bakes the accent into its own widget-level sheet — is repainted."""
        apply_theme(QApplication.instance(), self.dark_toggle.isChecked())
        win = self.window()
        if hasattr(win, "_apply_banner_style"):  # absent in isolated tests
            win._apply_banner_style()
        _show_status(self, "Accent colour updated", 5000)

    def _on_update_toggled(self, checked: bool) -> None:
        _settings().setValue(_STARTUP_CHECK_KEY, checked)

    def _check_updates(self) -> None:
        self.check_btn.setEnabled(False)
        _show_status(self, "Checking for updates…")
        updater.log.info("manual update check requested from Settings")
        win = self.window()  # grab on the GUI thread; the worker only emits its signal

        def work():
            outcome = "failed"
            try:
                if updater.fetch_update(force=True):
                    outcome = "staged"
                    if hasattr(win, "update_ready"):
                        win.update_ready.emit(updater.staged_version() or "")
                elif updater.last_check_current():
                    outcome = "current"
            finally:
                self._check_done.emit(outcome)  # report back on the GUI thread

        threading.Thread(target=work, daemon=True).start()

    def _on_check_done(self, outcome: str) -> None:
        """Re-enable the button and give feedback: status-bar for success, dialog for
        failure, nothing for a staged update (the main-window banner is its feedback)."""
        self.check_btn.setEnabled(True)
        if outcome == "current":
            _show_status(self, "You're up to date.", 5000)
        else:
            _show_status(self, "")  # clear the "Checking for updates…" status
            if outcome == "failed":
                QMessageBox.warning(
                    self, "Update check",
                    "Couldn't check for updates. Details are in the Log tab.",
                )

    def _open_data_folder(self) -> None:
        folder = paths.data_dir()
        folder.mkdir(parents=True, exist_ok=True)  # may not exist on a fresh run
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _about(self) -> None:
        QMessageBox.about(
            self, "About RC Central",
            f"<b>RC Central</b> v{__version__}<br>"
            "One app to install, update, and launch RC drift setup tools.<br><br>"
            'MIT licensed — <a href="https://github.com/J3vb/RC-Central">github.com/J3vb/RC-Central</a>',
        )
