"""Main application window: tab assembly and update banner."""

import sys
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QWidget,
)

from app import __version__, catalog

from app.ui.common import _accent, _on_accent, _settings, _show_status, app_icon
from app.ui.manuals import ManualsTab
from app.ui.settings import SettingsTab
from app.ui.tools import ToolsTab
from app.ui.workshop import WorkshopTab


class MainWindow(QMainWindow):
    # Emitted (with the ready version tag) when a background check has staged an
    # update. Carried across threads by Qt's queued connection, so the check can
    # run off the GUI thread and still light up the banner safely.
    update_ready = Signal(str)
    # Carries a freshly fetched catalog from the background refresh thread back to the
    # GUI thread (queued connection), same cross-thread pattern as update_ready.
    catalog_ready = Signal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"RC Central v{__version__}")
        self.setWindowIcon(app_icon())
        # User-tuned default; also the floor — every tab lays out clean at this
        # size and shrinking below it clips the Garage's three-column layout.
        self.resize(1075, 791)
        self.setMinimumSize(1075, 791)

        # An update is only swapped in on quit once the user has explicitly asked
        # for it via the banner; dismissing leaves the download unused.
        self.update_consented = False

        # Seed both tabs instantly from the on-disk snapshot (cached fetch, else
        # bundled) — a local read, not a blocking network round-trip. The background
        # thread wired below refreshes it. One shared snapshot, one source of truth.
        tools = catalog.cached_catalog()
        self._catalog = tools

        self.tabs = QTabWidget()
        # the stylesheet gives this one bar larger, bolder tabs than the sub-tab
        # bars nested inside pages (see theme._QSS's #mainTabs rule)
        self.tabs.setObjectName("mainTabs")
        self.workshop_tab = WorkshopTab()
        self.manuals_tab = ManualsTab(tools)
        self.settings_tab = SettingsTab()
        # aliases into the Workshop's sub-tabs, for tests and external callers
        self.garage_tab = self.workshop_tab.garage
        self.gear_tab = self.workshop_tab.gear
        self.tuning_tab = self.workshop_tab.tuning

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
            (self.workshop_tab, "Workshop"),
            # Settings is appended LAST so every existing tab keeps its index — the
            # saved-tab restore below clamps but doesn't remap, so inserting mid-list
            # would restore the wrong tab.
            (self.settings_tab, "Settings"),
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

        # Refresh the catalog off the GUI thread: a daemon thread fetches and emits the
        # result, delivered here by Qt's queued connection (connect BEFORE start; the
        # thread only emits, never touches widgets). _catalog_thread is kept as an
        # attribute purely so tests can join() it deterministically.
        self.catalog_ready.connect(self._refresh_catalog)
        self._catalog_thread = threading.Thread(
            target=lambda: self.catalog_ready.emit(catalog.load_catalog()), daemon=True
        )
        self._catalog_thread.start()

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

    def _refresh_catalog(self, fresh: list) -> None:
        if fresh == self._catalog:
            return  # remote unreachable or unchanged — keep what's shown
        # ponytail: a queued catalog_ready can land inside the nested event loop of an
        # open dialog, native file picker, or per-row menu; rebuilding the Tools table
        # there deletes widgets the loop is still driving. Skip the one-shot refresh
        # rather than rebuild under it — stale-for-a-session accepted, same trade-off as
        # set_catalog's in-flight guards. Leave self._catalog untouched so state stays
        # consistent (the next launch re-fetches).
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        self._catalog = fresh
        if self.tools_tab is not None:
            self.tools_tab.set_catalog(fresh)
        self.manuals_tab.set_catalog(fresh)
        _show_status(self, "Catalog updated", 5000)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        settings = _settings()
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("tab", self.tabs.currentIndex())
        super().closeEvent(event)

    def _build_update_banner(self) -> None:
        banner = QToolBar("Update")
        banner.setMovable(False)
        banner.setFloatable(False)
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
        self._apply_banner_style()

    def _apply_banner_style(self) -> None:
        """(Re)paint the banner from the live accent. A widget-level sheet, so it
        overrides the app stylesheet's QToolBar rules; text/buttons use the
        black-or-white that reads on the accent. SettingsTab calls this again
        when the user picks a new accent colour."""
        fg = _on_accent()
        tint = "255,255,255" if fg == "#ffffff" else "0,0,0"
        self.update_banner.setStyleSheet(
            f"QToolBar {{ background: {_accent()}; border: none; padding: 6px 10px; spacing: 8px; }}"
            f"QToolBar QLabel {{ color: {fg}; }}"
            f"QToolBar QPushButton {{ background: rgba({tint},0.16);"
            f" border: 1px solid rgba({tint},0.5); border-radius: 4px;"
            f" color: {fg}; padding: 4px 12px; }}"
            f"QToolBar QPushButton:hover {{ background: rgba({tint},0.28); }}"
            f"QToolBar QPushButton:pressed {{ background: rgba({tint},0.10); }}"
        )

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
