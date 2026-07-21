"""Shared UI helpers, constants, and download-tab plumbing."""

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import QLabel, QMainWindow, QProgressBar, QToolButton, QWidget


def _asset_path(name: str) -> Path:
    # _MEIPASS is where PyInstaller unpacks --add-data at runtime; fall back to source tree.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "app" / "assets" / name


def app_icon() -> QIcon:
    return QIcon(str(_asset_path("icon.png")))


# Pretty display names for catalog category codes; unknowns fall back to .title().
_CATEGORY_LABELS = {"esc": "ESC", "servo": "Servo", "radio": "Radio", "gyro": "Gyro"}


def _is_software(tool: dict) -> bool:
    """A tool RC Central can download and launch (vs. an info-only card)."""
    return "download" in tool


def _is_pdf(url: str | None) -> bool:
    """Whether a manual link is a downloadable PDF (vs. a web page we just open).
    ponytail: extension heuristic; add an explicit links[].type to the schema if a
    vendor serves a PDF without a .pdf URL and it needs offline caching too."""
    return bool(url) and url.lower().split("?")[0].split("#")[0].endswith(".pdf")


def _link_button(text: str, url: str | None) -> QToolButton:
    """A text button that opens a URL in the browser; disabled when there is no URL."""
    btn = QToolButton()
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    btn.setText(text)
    if url:
        btn.setToolTip(url)
        btn.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
    else:
        btn.setEnabled(False)
    return btn


# The default accent colour. The live accent is user-customizable (Settings ▸
# Appearance) and read via _accent(); every consumer reads at render/apply time,
# so the palettes, banner, and item highlights can never drift apart.
_ACCENT = "#1f6feb"
_ACCENT_KEY = "accent_color"


def _accent() -> str:
    """The accent colour: the user's stored pick, or the default. A junk stored
    value (hand-edited registry) falls back to the default rather than breaking
    every highlight in the app."""
    stored = _settings().value(_ACCENT_KEY, "")
    return stored if stored and QColor(stored).isValid() else _ACCENT


def _on_accent() -> str:
    """Black or white, whichever reads on the current accent — light accents need
    dark text on selections, the banner, and highlighted table cells."""
    return "#000000" if QColor(_accent()).lightnessF() >= 0.6 else "#ffffff"

# Muted secondary text (hints, captions, empty states). One literal that reads on
# both themes' backgrounds; the stylesheet applies it to QLabel#mutedLabel.
_MUTED = "#8a8f98"

# Spacing system: pane-edge margins and the gap between controls, applied to every
# tab's layouts. Inner control padding comes from the app stylesheet (theme._QSS),
# so code sets layout geometry only and the two can't fight over the same pixels.
_MARGIN = 12
_GAP = 8


def _section_label(text: str) -> QLabel:
    """A bold section-header label — the one hierarchy marker shared across tabs."""
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label

# QSettings identity and the persisted preference keys/defaults, defined once so a
# reader and a writer can't disagree on the org/app pair, a key string, or a default.
_SETTINGS_ORG = "RCCentral"
_SETTINGS_APP = "RCCentral"
_DARK_MODE_KEY, _DARK_MODE_DEFAULT = "dark_mode", False
_STARTUP_CHECK_KEY, _STARTUP_CHECK_DEFAULT = "check_updates_on_startup", True
# The Workshop's shared "active car" (a car id; empty string = none). Written only
# by WorkshopTab; the Gearing and Tuning sub-tabs read it on showEvent.
_ACTIVE_CAR_KEY = "workshop/active_car"


def _settings() -> QSettings:
    return QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def _show_status(widget: QWidget, msg: str, msecs: int = 0) -> None:
    """Show msg on widget's top-level status bar (clear it when msg is empty), if any."""
    win = widget.window()
    if isinstance(win, QMainWindow):
        bar = win.statusBar()
        bar.showMessage(msg, msecs) if msg else bar.clearMessage()


class _InstallSignals(QObject):
    """Bridge from the download thread back to the Qt main thread."""

    progress = Signal(int, int)
    done = Signal()
    error = Signal(str)


class _DownloadTab(QWidget):
    """Shared plumbing for a tab that fetches files in the background behind one progress
    bar: a status-bar helper and a thread runner. Subclasses create self.progress."""

    progress: QProgressBar

    def _status(self, msg: str = "", timeout: int = 0) -> None:
        """Show msg on the window's status bar (clear it when msg is empty), if any."""
        _show_status(self, msg, timeout)

    def _clear_status(self) -> None:
        self._status()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)  # 0 total -> busy indicator
        self.progress.setValue(done if total else 0)

    def _run_download(self, work, on_finished) -> None:
        """Run work(progress_cb) on a daemon thread while showing the progress bar, then
        call on_finished(error) on the GUI thread (error is a str, or None on success)."""
        self.progress.setValue(0)
        self.progress.show()

        signals = _InstallSignals(self)  # parented so it outlives this scope
        signals.progress.connect(self._on_progress)

        def finish(error: str | None) -> None:
            self.progress.hide()
            on_finished(error)

        signals.done.connect(lambda: finish(None))
        signals.error.connect(lambda msg: finish(msg))

        def run():
            try:
                work(signals.progress.emit)
                signals.done.emit()
            except Exception as e:  # anything here must reach the user, not a traceback
                signals.error.emit(str(e))

        threading.Thread(target=run, daemon=True).start()
