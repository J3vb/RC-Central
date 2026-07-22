"""The Tools tab: install/launch each catalog tool."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
)

from app import catalog, installer, launcher
from app.ui.common import (
    _CATEGORY_LABELS, _DownloadTab, _GAP, _is_software, _link_button, _MARGIN,
)

log = logging.getLogger(__name__)


class ToolsTab(_DownloadTab):
    """The catalog: install/launch each vendor tool. Formerly the whole window."""

    COLS = ("Tool", "Vendor", "Version", "Status", "", "Website")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        tools = catalog.load_catalog() if tools is None else tools

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)

        # Live filter: a search box and a category dropdown, both feeding one pass.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.category_filter = QComboBox()  # populated per-catalog in set_catalog
        self.category_filter.currentIndexChanged.connect(self._apply_filter)
        # Count of installed tools with a newer catalog version, kept in sync with
        # the per-row Update buttons (see _refresh_summary).
        self.update_summary = QLabel()
        self.update_summary.setObjectName("mutedLabel")  # secondary text (see theme._QSS)
        controls = QHBoxLayout()
        controls.setSpacing(_GAP)
        controls.addWidget(self.search, 1)
        controls.addWidget(self.category_filter)
        controls.addWidget(self.update_summary)

        # ponytail: one shared progress bar; per-row bars if parallel installs matter
        self.progress = QProgressBar()
        self.progress.hide()
        # One install at a time. Guards set_catalog from rebuilding self.tools mid-install
        # and blocks a 2nd install from sharing the single progress bar. Set before the
        # set_catalog call below, which reads it.
        self._installing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(_GAP)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        layout.addWidget(self.progress)

        # Per-row menu actions that only make sense once a tool is installed;
        # _refresh_row toggles their enabled state from the install state.
        self._install_actions: dict[int, tuple] = {}
        # stretch the tool-name column so the two trailing button columns stay compact
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.set_catalog(tools)

    def set_catalog(self, tools: list[dict]) -> None:
        """(Re)populate the table from a catalog; safe to call again for a background
        refresh. Declines while an install is mid-flight (see the guard below)."""
        if self._installing:
            # ponytail: stale-for-a-session beats repopulating under a live install
            return
        # Only installable tools belong here; info-only devices (no download) are
        # reference-only and live on the Manuals tab via their manual links.
        self.tools = [t for t in tools if _is_software(t)]
        self.table.setRowCount(len(self.tools))

        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("All categories", None)
        for cat in sorted({t.get("category", "") for t in self.tools if t.get("category")}):
            self.category_filter.addItem(_CATEGORY_LABELS.get(cat, cat.title()), cat)
        self.category_filter.setCurrentIndex(0)
        self.category_filter.blockSignals(False)

        self._install_actions.clear()
        for row, tool in enumerate(self.tools):
            name = QTableWidgetItem(tool["name"])
            name.setToolTip(tool.get("description", ""))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(tool["vendor"]))
            self.table.setItem(row, 2, QTableWidgetItem(tool["version"]))
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(lambda _=False, r=row: self._on_action(r))
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            menu = QMenu(button)
            menu.addAction(
                "Locate existing install…",
                lambda _=False, r=row: self._locate_existing(r),
            )
            open_action = menu.addAction(
                "Open install folder",
                lambda _=False, r=row: self._open_install_folder(r),
            )
            uninstall_action = menu.addAction(
                "Uninstall",
                lambda _=False, r=row: self._uninstall(r),
            )
            self._install_actions[row] = (open_action, uninstall_action)
            # USB/adapter drivers are often needed *before* first launch, so these
            # stay always-enabled (not in _install_actions). "Install" = open the
            # driver URL; drivers vary (web page/.inf/.zip/.exe) and opening is
            # universally correct. Guard url — the remote catalog is unvalidated.
            valid_drivers = [d for d in (tool.get("drivers") or []) if d.get("url")]
            if valid_drivers:
                menu.addSeparator()
                for d in valid_drivers:
                    menu.addAction(
                        f"Install driver: {d.get('name') or 'driver'}…",
                        lambda _=False, u=d["url"]: QDesktopServices.openUrl(QUrl(u)),
                    )
            button.setMenu(menu)
            self.table.setCellWidget(row, 4, button)
            self.table.setCellWidget(row, 5, _link_button("Website", tool.get("homepage")))
            self._refresh_row(row)
        self._refresh_summary()
        self.table.resizeColumnsToContents()
        self._apply_filter()  # re-apply any live search text against the new rows

    def _apply_filter(self) -> None:
        """Show only rows matching both the search text and the chosen category."""
        query = self.search.text().strip().lower()
        category = self.category_filter.currentData()
        for row, tool in enumerate(self.tools):
            matches_text = not query or any(
                query in str(tool.get(field, "")).lower()
                for field in ("name", "vendor", "category", "description")
            )
            matches_category = category is None or tool.get("category") == category
            self.table.setRowHidden(row, not (matches_text and matches_category))

    def _refresh_row(self, row: int) -> None:
        tool = self.tools[row]
        state = installer.get_state(tool["id"])
        if state is None:
            status, action = "Not installed", "Install"
        elif state["version"] != tool["version"]:
            status, action = f"Installed v{state['version']}", "Update"
        else:
            status, action = f"Installed v{state['version']}", "Launch"
        self.table.setItem(row, 3, QTableWidgetItem(status))
        self.table.cellWidget(row, 4).setText(action)
        for act in self._install_actions.get(row, ()):
            act.setEnabled(state is not None)  # uninstall / open-folder need an install

    def _refresh_summary(self) -> None:
        """Count rows whose action button reads 'Update'. Reading the button text the
        rows already set keeps the badge from ever disagreeing with them (and needs no
        extra state read). Global count, not filtered — total updates, not visible ones."""
        n = sum(
            1
            for r in range(len(self.tools))
            if self.table.cellWidget(r, 4).text() == "Update"
        )
        self.update_summary.setText(f"{n} update{'' if n == 1 else 's'} available" if n else "")

    def _on_action(self, row: int) -> None:
        tool = self.tools[row]
        state = installer.get_state(tool["id"])
        if state and state["version"] == tool["version"]:
            try:
                launcher.launch(
                    tool["id"],
                    state["exe_path"],
                    tool.get("install", {}).get("needs_admin", False),
                )
            except OSError as e:  # e.g. UAC prompt declined
                QMessageBox.warning(self, "Launch failed", str(e))
                return
            self._status(f"Launched {tool['name']}", 5000)
        else:
            self._install(row, tool)

    def _locate_existing(self, row: int) -> None:
        tool = self.tools[row]
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Locate {tool['name']} executable",
            "",
            "Programs (*.exe);;All files (*)",
        )
        if not path:
            return
        version, ok = QInputDialog.getText(
            self,
            "Installed version",
            f"Which version of {tool['name']} is this?",
            text=tool["version"],
        )
        if not ok:
            return
        try:
            installer.register_existing(tool, path, version.strip() or tool["version"])
        except Exception as e:  # bad path etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Couldn't add existing install", str(e))
            return
        # A background catalog refresh can rebuild self.tools during the dialogs above,
        # so the captured row may be stale. Re-resolve by tool id; if the tool is gone,
        # skip the row-scoped refresh (the summary/status still hold).
        row = next((i for i, t in enumerate(self.tools) if t["id"] == tool["id"]), None)
        if row is not None:
            self._refresh_row(row)
        self._refresh_summary()
        self._status(f"Linked existing {tool['name']}", 5000)

    def _open_install_folder(self, row: int) -> None:
        state = installer.get_state(self.tools[row]["id"])
        if state is None:
            return
        folder = Path(state["exe_path"]).parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _uninstall(self, row: int) -> None:
        tool = self.tools[row]
        if installer.get_state(tool["id"]) is None:
            return
        if QMessageBox.question(
            self, "Uninstall", f"Remove {tool['name']} and its downloaded files?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            installer.uninstall(tool["id"])
        except OSError as e:  # a locked file etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Uninstall failed", str(e))
            return
        # The confirmation dialog above can outlive the row: a background catalog refresh
        # may rebuild self.tools. The uninstall already ran, so re-resolve by tool id and
        # only skip the row-scoped refresh if the tool is gone; summary/status still hold.
        row = next((i for i, t in enumerate(self.tools) if t["id"] == tool["id"]), None)
        if row is not None:
            self._refresh_row(row)
        self._refresh_summary()
        self._status(f"Uninstalled {tool['name']}", 5000)

    def _install(self, row: int, tool: dict) -> None:
        if self._installing:
            return  # one shared progress bar: block a 2nd install until this one finishes
        self._installing = True
        self.table.cellWidget(row, 4).setEnabled(False)
        self._status(f"Downloading {tool['name']}...")
        self._run_download(
            lambda cb: installer.install(tool, progress=cb),
            lambda err, t=tool: self._install_finished(t, err),
        )

    def _install_finished(self, tool: dict, error: str | None) -> None:
        self._installing = False
        # Re-resolve the row by id (like _uninstall/_locate_existing): a catalog refresh
        # is blocked while installing, but this keeps a stale index from ever being used.
        row = next((i for i, t in enumerate(self.tools) if t["id"] == tool["id"]), None)
        if row is not None:
            self.table.cellWidget(row, 4).setEnabled(True)
            self._refresh_row(row)
        self._refresh_summary()
        if error:
            log.warning("install of %s failed: %s", tool["name"], error)
            self._clear_status()
            QMessageBox.warning(
                self,
                "Install failed",
                f"Couldn't download and install {tool['name']}.\n"
                "Check your internet connection and try again — details are in Settings ▸ Log.",
            )
        else:
            self._status(f"Installed {tool['name']}", 5000)
