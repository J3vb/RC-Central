"""The Manuals tab: one row per official manual / support link across the catalog."""

import logging
import threading

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
)

from app import catalog, installer
from app.ui.common import (
    _CATEGORY_LABELS,
    _DownloadTab,
    _InstallSignals,
    _is_pdf,
    _is_software,
    _link_button,
)

log = logging.getLogger(__name__)


class ManualsTab(_DownloadTab):
    """One row per official manual / support link across the catalog, mirroring the
    Tools tab's table. PDF links can be downloaded once and opened offline thereafter;
    web links open in the browser as before. Cross-platform (no install/launch)."""

    COLS = ("Manual", "Vendor", "Category", "Status", "", "Website")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        tools = catalog.load_catalog() if tools is None else tools
        self._active: dict[int, "threading.Event"] = {}  # row -> cancel event; in == downloading
        installer.clear_partial_manuals()  # drop orphaned .part temps from a prior killed run

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Live filter: a search box and a category dropdown, both feeding one pass.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search manuals…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.category_filter = QComboBox()  # populated per-catalog in set_catalog
        self.category_filter.currentIndexChanged.connect(self._apply_filter)
        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(self.category_filter)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)

        # Per-row menu actions that only make sense once a PDF is downloaded;
        # _refresh_row toggles their enabled state from the cache state.
        self._pdf_actions: dict[int, tuple] = {}
        # stretch the manual-name column so the trailing button columns stay compact
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.set_catalog(tools)

    def set_catalog(self, tools: list[dict]) -> None:
        """(Re)populate the table from a catalog; safe to call again for a background
        refresh. Declines while any download is in flight (its row closures hold indexes)."""
        if self._active:
            return
        # Flatten to one entry per link; a tool with no links has no manual to list
        # (its vendor site stays reachable from that vendor's other rows).
        self._manuals: list[dict] = []
        for tool in sorted(tools, key=lambda t: (t.get("category", ""), t["name"].lower())):
            links = tool.get("links", [])
            if not links and not _is_software(tool) and tool.get("homepage"):
                # An info-only device with a homepage but no manual links would other-
                # wise appear nowhere (it's filtered off the Tools tab). Give it one
                # row pointing at its homepage so the device stays reachable.
                links = [{"name": f"{tool['name']} (website)", "url": tool["homepage"]}]
            for link in links:
                name = link.get("name")
                if not name:  # skip a malformed (unvalidated remote) link that can't be a row
                    continue
                self._manuals.append(
                    {
                        "name": name,
                        "url": link.get("url"),
                        "vendor": tool["vendor"],
                        "category": tool.get("category", ""),
                        "homepage": tool.get("homepage"),
                        "tool_name": tool["name"],
                        "description": tool.get("description", ""),
                    }
                )
        self.table.setRowCount(len(self._manuals))

        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories", None)
        for cat in sorted({m["category"] for m in self._manuals if m["category"]}):
            self.category_filter.addItem(_CATEGORY_LABELS.get(cat, cat.title()), cat)
        self.category_filter.setCurrentIndex(0)
        self.category_filter.blockSignals(False)

        self._pdf_actions.clear()
        for row, manual in enumerate(self._manuals):
            name = QTableWidgetItem(manual["name"])
            name.setToolTip(f"{manual['tool_name']} — {manual['description']}".strip(" —"))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(manual["vendor"]))
            cat = manual["category"]
            self.table.setItem(row, 2, QTableWidgetItem(_CATEGORY_LABELS.get(cat, cat.title())))
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(lambda _=False, r=row: self._on_action(r))
            if _is_pdf(manual["url"]):  # cacheable rows get a Tools-style dropdown menu
                button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
                menu = QMenu(button)
                open_action = menu.addAction(
                    "Open containing folder", lambda _=False, r=row: self._open_folder(r)
                )
                delete_action = menu.addAction(
                    "Delete downloaded PDF", lambda _=False, r=row: self._delete_pdf(r)
                )
                self._pdf_actions[row] = (open_action, delete_action)
                button.setMenu(menu)
            self.table.setCellWidget(row, 4, button)
            self.table.setCellWidget(row, 5, _link_button("Website", manual["homepage"]))
            self._refresh_row(row)
        self.table.resizeColumnsToContents()
        self._apply_filter()  # re-apply any live search text against the new rows

    def _apply_filter(self) -> None:
        """Show only rows matching both the search text and the chosen category."""
        query = self.search.text().strip().lower()
        category = self.category_filter.currentData()
        for row, manual in enumerate(self._manuals):
            matches_text = not query or any(
                query in str(manual.get(field, "")).lower()
                # searches the manual name too, unlike the old tab (searching "PDF" now works)
                for field in ("name", "vendor", "category", "tool_name", "description")
            )
            matches_category = category is None or manual["category"] == category
            self.table.setRowHidden(row, not (matches_text and matches_category))

    def _refresh_row(self, row: int) -> None:
        manual = self._manuals[row]
        url = manual["url"]
        button = self.table.cellWidget(row, 4)
        if not _is_pdf(url):
            self.table.setItem(row, 3, QTableWidgetItem("Web page"))
            button.setText("Open")
            button.setEnabled(bool(url))  # a link with no URL has nothing to open
            return
        cached = installer.manual_is_cached(url)
        self.table.setItem(row, 3, QTableWidgetItem("Downloaded" if cached else ""))
        button.setText("Open" if cached else "Download")
        button.setEnabled(True)
        for act in self._pdf_actions.get(row, ()):
            act.setEnabled(cached)  # open-folder / delete need a downloaded file

    def _on_action(self, row: int) -> None:
        url = self._manuals[row]["url"]
        if not url:
            return
        if not _is_pdf(url):
            QDesktopServices.openUrl(QUrl(url))
        elif installer.manual_is_cached(url):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(installer.manual_cache_path(url))))
        elif row in self._active:  # the button is showing "Cancel"
            self._cancel_download(row)
        elif any(self._manuals[r]["url"] == url for r in self._active):
            # same file already downloading in another row; that one refreshes both on
            # finish. A second thread would clobber the shared <hash>.part temp.
            self._status("That manual is already downloading.", 4000)
        else:
            self._start_download(row, url)

    def _refresh_idle_rows(self) -> None:
        """Refresh every row that isn't mid-download, so a sibling sharing a URL updates too
        without clobbering a downloading row's inline bar + Cancel button."""
        for r in range(len(self._manuals)):
            if r not in self._active:
                self._refresh_row(r)

    def _open_folder(self, row: int) -> None:
        if installer.manual_is_cached(self._manuals[row]["url"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(installer.MANUALS_DIR)))

    def _delete_pdf(self, row: int) -> None:
        manual = self._manuals[row]
        path = installer.manual_cache_path(manual["url"])
        if not path.exists():
            return
        if QMessageBox.question(
            self, "Delete", f"Delete the downloaded '{manual['name']}'?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except OSError as e:  # a locked file etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Delete failed", str(e))
            return
        self._refresh_idle_rows()  # this row + any sibling sharing the URL flip to "Download"
        self._status(f"Deleted {manual['name']}", 5000)

    def _start_download(self, row: int, url: str) -> None:
        cancel = threading.Event()
        self._active[row] = cancel

        bar = QProgressBar()  # per-row progress, shown right in the Status cell
        bar.setTextVisible(True)
        self.table.setCellWidget(row, 3, bar)
        self.table.cellWidget(row, 4).setText("Cancel")
        self._status(f"Downloading {self._manuals[row]['name']}...")

        signals = _InstallSignals(self)  # parented so it outlives this scope
        signals.progress.connect(lambda done, total, b=bar: self._update_bar(b, done, total))
        signals.done.connect(lambda r=row: self._download_finished(r, None))
        signals.error.connect(lambda msg, r=row: self._download_finished(r, msg))

        def work():
            try:
                installer.download_manual(url, progress=signals.progress.emit, cancel=cancel)
                signals.done.emit()
            except installer.DownloadCancelled:
                signals.done.emit()  # not a failure: the row resets via the cache check
            except Exception as e:  # anything else must reach the user, not a traceback
                signals.error.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _update_bar(bar: QProgressBar, done: int, total: int) -> None:
        bar.setMaximum(total)  # 0 total -> indeterminate/busy
        bar.setValue(done if total else 0)

    def _cancel_download(self, row: int) -> None:
        self._active[row].set()  # worker stops at its next chunk, then _download_finished runs
        button = self.table.cellWidget(row, 4)
        button.setText("Cancelling…")
        button.setEnabled(False)

    def _download_finished(self, row: int, error: str | None) -> None:
        self._active.pop(row, None)
        self.table.removeCellWidget(row, 3)  # drop this row's inline progress bar
        manual = self._manuals[row]
        self._refresh_idle_rows()  # this row + any sibling sharing the URL flip to their new state
        if error:
            log.warning("manual download failed for %s: %s", manual["name"], error)
            self._clear_status()
            QMessageBox.warning(
                self,
                "Download failed",
                f"Couldn't download {manual['name']}.\n"
                "Check your internet connection and try again — details are in Settings ▸ Log.",
            )
        elif installer.manual_is_cached(manual["url"]):
            self._status(f"Downloaded {manual['name']}", 5000)
        # else: cancelled -> _refresh_row already reset it to "Download", no message
