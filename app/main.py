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


class ToolsTab(_DownloadTab):
    """The catalog: install/launch each vendor tool. Formerly the whole window."""

    COLS = ("Tool", "Vendor", "Version", "Status", "", "Website")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        tools = catalog.load_catalog() if tools is None else tools
        # Only installable tools belong here; info-only devices (no download) are
        # reference-only and live on the Manuals tab via their manual links.
        self.tools = [t for t in tools if _is_software(t)]

        self.table = QTableWidget(len(self.tools), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Live filter: a search box and a category dropdown, both feeding one pass.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", None)
        for cat in sorted({t.get("category", "") for t in self.tools if t.get("category")}):
            self.category_filter.addItem(_CATEGORY_LABELS.get(cat, cat.title()), cat)
        self.category_filter.currentIndexChanged.connect(self._apply_filter)
        # Count of installed tools with a newer catalog version, kept in sync with
        # the per-row Update buttons (see _refresh_summary).
        self.update_summary = QLabel()
        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(self.category_filter)
        controls.addWidget(self.update_summary)

        # ponytail: one shared progress bar; per-row bars if parallel installs matter
        self.progress = QProgressBar()
        self.progress.hide()

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        layout.addWidget(self.progress)

        # Per-row menu actions that only make sense once a tool is installed;
        # _refresh_row toggles their enabled state from the install state.
        self._install_actions: dict[int, tuple] = {}
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
        # stretch the tool-name column so the two trailing button columns stay compact
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

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
        self._refresh_row(row)
        self._refresh_summary()
        self._status(f"Uninstalled {tool['name']}", 5000)

    def _install(self, row: int, tool: dict) -> None:
        self.table.cellWidget(row, 4).setEnabled(False)
        self._status(f"Downloading {tool['name']}...")
        self._run_download(
            lambda cb: installer.install(tool, progress=cb),
            lambda err, r=row: self._install_finished(r, err),
        )

    def _install_finished(self, row: int, error: str | None) -> None:
        self.table.cellWidget(row, 4).setEnabled(True)
        self._refresh_row(row)
        self._refresh_summary()
        if error:
            self._clear_status()
            QMessageBox.warning(self, "Install failed", error)
        else:
            self._status(f"Installed {self.tools[row]['name']}", 5000)


class ManualsTab(_DownloadTab):
    """One row per official manual / support link across the catalog, mirroring the
    Tools tab's table. PDF links can be downloaded once and opened offline thereafter;
    web links open in the browser as before. Cross-platform (no install/launch)."""

    COLS = ("Manual", "Vendor", "Category", "Status", "", "Website")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        tools = catalog.load_catalog() if tools is None else tools
        # Flatten to one entry per link; a tool with no links has no manual to list
        # (its vendor site stays reachable from that vendor's other rows).
        self._manuals: list[dict] = []
        self._active: dict[int, "threading.Event"] = {}  # row -> cancel event; in == downloading
        installer.clear_partial_manuals()  # drop orphaned .part temps from a prior killed run
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

        self.table = QTableWidget(len(self._manuals), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Live filter: a search box and a category dropdown, both feeding one pass.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search manuals…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", None)
        for cat in sorted({m["category"] for m in self._manuals if m["category"]}):
            self.category_filter.addItem(_CATEGORY_LABELS.get(cat, cat.title()), cat)
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
        # stretch the manual-name column so the trailing button columns stay compact
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

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
            self._clear_status()
            QMessageBox.warning(self, "Download failed", error)
        elif installer.manual_is_cached(manual["url"]):
            self._status(f"Downloaded {manual['name']}", 5000)
        # else: cancelled -> _refresh_row already reset it to "Download", no message


class GearTab(QWidget):
    """Live gearing calculator: FDR, rollout, theoretical top speed."""

    def __init__(self):
        super().__init__()

        self.car_picker = QComboBox()  # links a saved car's gearing into the inputs
        self.car_picker.currentIndexChanged.connect(self._on_pick_car)

        self.pinion = QSpinBox()
        self.pinion.setRange(1, 99)
        self.pinion.setValue(22)
        self.spur = QSpinBox()
        self.spur.setRange(1, 200)
        self.spur.setValue(87)
        self.internal_ratio = QDoubleSpinBox()
        self.internal_ratio.setRange(1.0, 5.0)
        self.internal_ratio.setSingleStep(0.1)
        self.internal_ratio.setValue(1.9)
        self.tire = QDoubleSpinBox()
        self.tire.setRange(40.0, 120.0)
        self.tire.setSingleStep(0.5)
        self.tire.setValue(60.0)
        self.kv = QSpinBox()
        self.kv.setRange(0, 20000)
        self.kv.setValue(3000)
        self.cells = QSpinBox()
        self.cells.setRange(1, 8)
        self.cells.setValue(2)

        self._inputs = (
            self.pinion,
            self.spur,
            self.internal_ratio,
            self.tire,
            self.kv,
            self.cells,
        )
        for w in self._inputs:
            w.valueChanged.connect(self._recompute)

        self.fdr_out = QLabel()
        self.rollout_out = QLabel()
        self.kmh_out = QLabel()
        self.mph_out = QLabel()

        # Reverse-solve: enter a target rollout, get the nearest whole-tooth pinion.
        self.target_rollout = QDoubleSpinBox()
        self.target_rollout.setRange(1.0, 999.0)
        self.target_rollout.setSingleStep(0.5)
        self.target_rollout.setValue(30.0)
        solve_btn = QPushButton("Solve → pinion")
        solve_btn.clicked.connect(self._solve_pinion)
        solve_row = QHBoxLayout()
        solve_row.addWidget(self.target_rollout, 1)
        solve_row.addWidget(solve_btn)
        solve_widget = QWidget()
        solve_widget.setLayout(solve_row)

        form = QFormLayout()
        form.addRow("Load from car", self.car_picker)
        form.addRow("Pinion (teeth)", self.pinion)
        form.addRow("Spur (teeth)", self.spur)
        form.addRow("Internal ratio", self.internal_ratio)
        form.addRow("Tire diameter (mm)", self.tire)
        form.addRow("Motor Kv", self.kv)
        form.addRow("Battery cells (S)", self.cells)
        form.addRow("Target rollout (mm)", solve_widget)
        form.addRow(QLabel("<b>Results</b>"))
        form.addRow("Final drive ratio", self.fdr_out)
        form.addRow("Rollout (mm)", self.rollout_out)
        form.addRow("Top speed (km/h)", self.kmh_out)
        form.addRow("Top speed (mph)", self.mph_out)

        self.save_btn = QPushButton("Save results to selected car")
        self.save_btn.clicked.connect(self._save_to_car)
        self.save_btn.setEnabled(False)
        chart_btn = QPushButton("Gear ratio chart…")
        chart_btn.clicked.connect(self._open_chart)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.save_btn, 1)
        btn_row.addWidget(chart_btn)

        # What-if sweep: the current pinion ±3, so tuners see the effect of a
        # pinion swap (the common drift adjustment) at a glance. The base row is bold.
        self.sweep_table = QTableWidget(0, 4)
        self.sweep_table.setHorizontalHeaderLabels(("Pinion", "FDR", "Rollout (mm)", "km/h"))
        self.sweep_table.verticalHeader().hide()
        self.sweep_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sweep_table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(btn_row)
        layout.addWidget(self.sweep_table)
        layout.addStretch(1)

        self._reload_cars()
        self._recompute()

    def _reload_cars(self) -> None:
        """Refresh the car picker from the garage, keeping the current selection.

        Blocks signals so the rebuild doesn't fire a spurious _on_pick_car. showEvent
        calls this on every switch back to this tab, so it must re-select the car the
        user had chosen — a bare clear()+addItem auto-selects "— none —" and would
        otherwise silently drop the selection (and disable "Save results to car").
        """
        current = self.car_picker.currentData()
        self.car_picker.blockSignals(True)
        self.car_picker.clear()
        self.car_picker.addItem("— none —", None)
        for car in garage.list_cars():
            self.car_picker.addItem(car.get("name", "Unnamed"), car["id"])
        if current is not None:
            idx = self.car_picker.findData(current)
            if idx != -1:  # the selected car may have been deleted on the Garage tab
                self.car_picker.setCurrentIndex(idx)
        self.car_picker.blockSignals(False)
        self.save_btn.setEnabled(self.car_picker.currentData() is not None)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # a car may have been added/renamed on the Garage tab since we last looked
        self._reload_cars()
        super().showEvent(event)

    def _on_pick_car(self) -> None:
        car_id = self.car_picker.currentData()
        self.save_btn.setEnabled(car_id is not None)
        if car_id is None:
            return
        car = garage.load_car(car_id)
        if car:
            self.load_from_car(car)

    def load_from_car(self, car: dict) -> None:
        """Prefill the inputs from a car's gearing block, then recompute."""
        g = car.get("gearing", {})
        for widget, key, default in (
            (self.pinion, "pinion", 22),
            (self.spur, "spur", 87),
            (self.internal_ratio, "internal_ratio", 1.9),
            (self.tire, "tire_diameter_mm", 60.0),
            (self.kv, "kv", 3000),
            (self.cells, "cells", 2),
        ):
            widget.blockSignals(True)
            widget.setValue(g.get(key, default))
            widget.blockSignals(False)
        # select this car in the picker so "save results" targets it
        idx = self.car_picker.findData(car.get("id"))
        if idx != -1:
            self.car_picker.blockSignals(True)
            self.car_picker.setCurrentIndex(idx)
            self.car_picker.blockSignals(False)
            self.save_btn.setEnabled(True)
        self._recompute()

    def _current(self) -> dict:
        return gearing.compute(
            pinion=self.pinion.value(),
            spur=self.spur.value(),
            internal_ratio=self.internal_ratio.value(),
            tire_diameter_mm=self.tire.value(),
            kv=self.kv.value(),
            voltage=gearing.pack_voltage(self.cells.value()),
        )

    def _recompute(self) -> None:
        try:
            r = self._current()
            rows = gearing.pinion_sweep(
                base_pinion=self.pinion.value(),
                spur=self.spur.value(),
                internal_ratio=self.internal_ratio.value(),
                tire_diameter_mm=self.tire.value(),
                kv=self.kv.value(),
                voltage=gearing.pack_voltage(self.cells.value()),
                span=3,
            )
        except ValueError:
            for lbl in (self.fdr_out, self.rollout_out, self.kmh_out, self.mph_out):
                lbl.setText("—")
            self.sweep_table.setRowCount(0)
            return
        self.fdr_out.setText(f"{r['fdr']:.2f}")
        self.rollout_out.setText(f"{r['rollout_mm']:.1f}")
        self.kmh_out.setText(f"{r['top_speed_kmh']:.1f}")
        self.mph_out.setText(f"{r['top_speed_mph']:.1f}")
        self._fill_sweep(rows)

    def _solve_pinion(self) -> None:
        """Fill the pinion spinbox with the nearest tooth for the target rollout.

        setValue fires the existing valueChanged -> _recompute, so results and the
        sweep table refresh for free. QSpinBox clamps to its 1..99 range; integer
        teeth mean the achieved rollout may differ slightly from target, and
        _recompute shows that honest resulting value.
        """
        try:
            p = gearing.solve_pinion_for_rollout(
                target_rollout_mm=self.target_rollout.value(),
                spur=self.spur.value(),
                internal_ratio=self.internal_ratio.value(),
                tire_diameter_mm=self.tire.value(),
            )
        except ValueError:
            return
        self.pinion.setValue(p)

    def _fill_sweep(self, rows: list[dict]) -> None:
        self.sweep_table.setRowCount(len(rows))
        for row, data in enumerate(rows):
            values = (
                str(data["pinion"]),
                f"{data['fdr']:.2f}",
                f"{data['rollout_mm']:.1f}",
                f"{data['top_speed_kmh']:.1f}",
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if data["is_base"]:  # the current pinion, bolded so it stands out
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.sweep_table.setItem(row, col, item)

    def _open_chart(self) -> None:
        # snapshot of the current setup; the modal chart doesn't live-track the tab
        _GearChartDialog(
            self.pinion.value(), self.spur.value(), self.internal_ratio.value(), self
        ).exec()

    def _save_to_car(self) -> None:
        car_id = self.car_picker.currentData()
        if car_id is None:
            return
        car = garage.load_car(car_id)
        if car is None:
            QMessageBox.warning(self, "Save failed", "That car no longer exists.")
            self._reload_cars()
            return
        try:
            r = self._current()
        except ValueError:
            return
        g = car.setdefault("gearing", {})
        g.update(
            {
                "pinion": self.pinion.value(),
                "spur": self.spur.value(),
                "internal_ratio": self.internal_ratio.value(),
                "tire_diameter_mm": self.tire.value(),
                "kv": self.kv.value(),
                "cells": self.cells.value(),
                "fdr": round(r["fdr"], 3),
                "rollout_mm": round(r["rollout_mm"], 2),
                "top_speed_kmh": round(r["top_speed_kmh"], 1),
            }
        )
        garage.save_car(car)
        _show_status(self, f"Saved gearing to {car['name']}", 5000)


class _GearChartDialog(QDialog):
    """Spur × pinion FDR matrix, like the printed gear chart that ships with a kit.

    Internal ratio is a snapshot of the tab at open (change it there, like the fixed
    "RATIO" header on a printed chart). Ranges are editable and persist via QSettings.
    """

    def __init__(self, pinion: int, spur: int, internal_ratio: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gear ratio chart")
        self._pinion = pinion
        self._spur = spur
        self._ratio = internal_ratio

        self.pinion_min, self.pinion_max = QSpinBox(), QSpinBox()
        self.spur_min, self.spur_max = QSpinBox(), QSpinBox()
        # (box, settings key, first-run default centered on the current setup);
        # done() persists through this same tuple so keys can't drift apart
        self._persist = (
            (self.pinion_min, "gearchart/pinion_min", pinion - 8),
            (self.pinion_max, "gearchart/pinion_max", pinion + 8),
            (self.spur_min, "gearchart/spur_min", spur - 10),
            (self.spur_max, "gearchart/spur_max", spur + 10),
        )
        settings = _settings()
        for box, key, default in self._persist:
            box.setRange(1, 99 if box in (self.pinion_min, self.pinion_max) else 200)
            box.setValue(settings.value(key, default, type=int))  # clamps to range

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Pinion"))
        controls.addWidget(self.pinion_min)
        controls.addWidget(QLabel("–"))
        controls.addWidget(self.pinion_max)
        controls.addSpacing(16)
        controls.addWidget(QLabel("Spur"))
        controls.addWidget(self.spur_min)
        controls.addWidget(QLabel("–"))
        controls.addWidget(self.spur_max)
        controls.addStretch(1)
        controls.addWidget(QLabel(f"Internal ratio: {internal_ratio:.2f}"))

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setDefaultSectionSize(52)

        # connect only now that self.table exists (same trap as _CompareDialog:
        # the setValue calls above would otherwise rebuild into a missing table)
        for box, _key, _default in self._persist:
            box.valueChanged.connect(self._rebuild)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        self.resize(720, 480)
        self._rebuild()

    def _rebuild(self) -> None:
        plo, phi = sorted((self.pinion_min.value(), self.pinion_max.value()))
        slo, shi = sorted((self.spur_min.value(), self.spur_max.value()))
        pinions = range(plo, phi + 1)
        spurs = range(shi, slo - 1, -1)  # highest spur on top, like the printed charts
        self.table.setColumnCount(len(pinions))
        self.table.setRowCount(len(spurs))
        self.table.setHorizontalHeaderLabels([str(p) for p in pinions])
        self.table.setVerticalHeaderLabels([str(s) for s in spurs])
        for row, s in enumerate(spurs):
            for col, p in enumerate(pinions):
                item = QTableWidgetItem(f"{gearing.final_drive_ratio(p, s, self._ratio):.2f}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if p == self._pinion and s == self._spur:  # the current combo
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setBackground(QColor(_ACCENT))
                    item.setForeground(QColor("white"))  # readable on accent in both themes
                self.table.setItem(row, col, item)

    def done(self, result: int) -> None:  # noqa: N802 (Qt override)
        # done() runs on OK/Esc/titlebar-close alike, so ranges always persist
        settings = _settings()
        for box, key, _default in self._persist:
            settings.setValue(key, box.value())
        super().done(result)


class _CompareDialog(QDialog):
    """Read-only side-by-side of two cars' spec fields; differing rows highlighted.

    Highlight uses yellow bg + black text so it stays readable in either theme
    (a themed foreground could otherwise vanish on the yellow).
    """

    def __init__(self, cars: list[dict], default_id: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Compare cars")
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        for combo in (self.combo_a, self.combo_b):
            for car in cars:
                combo.addItem(car.get("name", "Unnamed"), car["id"])
        idx_a = max(0, self.combo_a.findData(default_id))  # open car, or first
        self.combo_a.setCurrentIndex(idx_a)
        self.combo_b.setCurrentIndex(1 if idx_a == 0 else 0)  # a different car

        self.table = QTableWidget(0, 3)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        # Connect only now that self.table exists: the setCurrentIndex calls above
        # fire currentIndexChanged, and _render needs the table to write into.
        for combo in (self.combo_a, self.combo_b):
            combo.currentIndexChanged.connect(self._render)

        pick_row = QHBoxLayout()
        pick_row.addWidget(self.combo_a, 1)
        pick_row.addWidget(self.combo_b, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(pick_row)
        layout.addWidget(self.table)
        self.resize(520, 480)
        self._render()

    def _render(self) -> None:
        # a car may have been deleted between opening the dialog and picking it
        a = garage.load_car(self.combo_a.currentData()) or {}
        b = garage.load_car(self.combo_b.currentData()) or {}
        rows = garage.diff_cars(a, b)
        self.table.setHorizontalHeaderLabels(
            ("Field", a.get("name", "A"), b.get("name", "B"))
        )
        self.table.setRowCount(len(rows))
        for r, (label, va, vb, differs) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(label))
            for col, text in ((1, va), (2, vb)):
                item = QTableWidgetItem(text)
                if differs:
                    item.setBackground(Qt.GlobalColor.yellow)
                    item.setForeground(Qt.GlobalColor.black)
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)


class GarageTab(QWidget):
    """Create/edit/delete RC car spec sheets."""

    def __init__(self, on_open_in_calc: Callable[[dict], None] | None = None):
        super().__init__()
        self._on_open_in_calc = on_open_in_calc
        self.current_id: str | None = None

        self._cars: list[dict] = []  # cache behind the list, for live filtering
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cars…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._on_new)
        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._on_duplicate)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete)
        self.compare_btn = QPushButton("Compare…")
        self.compare_btn.clicked.connect(self._on_compare)
        left_buttons = QHBoxLayout()
        left_buttons.addWidget(new_btn)
        left_buttons.addWidget(import_btn)
        left_buttons.addWidget(dup_btn)
        left_buttons.addWidget(del_btn)
        left_buttons.addWidget(self.compare_btn)

        # Whole-garage actions (all cars), distinct from the per-car form buttons.
        backup_btn = QPushButton("Back up all…")
        backup_btn.clicked.connect(self._on_backup)
        restore_btn = QPushButton("Restore all…")
        restore_btn.clicked.connect(self._on_restore)
        backup_row = QHBoxLayout()
        backup_row.addWidget(backup_btn)
        backup_row.addWidget(restore_btn)

        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.list)
        left.addLayout(left_buttons)
        left.addLayout(backup_row)

        self.name = QLineEdit()
        self.chassis = QLineEdit()
        self.motor = QLineEdit()
        self.esc = QLineEdit()
        self.servo = QLineEdit()
        self.tires = QLineEdit()
        self.pinion = QSpinBox()
        self.pinion.setRange(1, 99)
        self.pinion.setValue(22)
        self.spur = QSpinBox()
        self.spur.setRange(1, 200)
        self.spur.setValue(87)
        self.internal_ratio = QDoubleSpinBox()
        self.internal_ratio.setRange(1.0, 5.0)
        self.internal_ratio.setSingleStep(0.1)
        self.internal_ratio.setValue(1.9)
        self.tire = QDoubleSpinBox()
        self.tire.setRange(40.0, 120.0)
        self.tire.setSingleStep(0.5)
        self.tire.setValue(60.0)
        self.kv = QSpinBox()
        self.kv.setRange(0, 20000)
        self.kv.setValue(3000)
        self.cells = QSpinBox()
        self.cells.setRange(1, 8)
        self.cells.setValue(2)
        self.notes = QPlainTextEdit()

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Chassis", self.chassis)
        form.addRow("Motor", self.motor)
        form.addRow("ESC", self.esc)
        form.addRow("Servo", self.servo)
        form.addRow("Tires", self.tires)
        form.addRow("Pinion (teeth)", self.pinion)
        form.addRow("Spur (teeth)", self.spur)
        form.addRow("Internal ratio", self.internal_ratio)
        form.addRow("Tire diameter (mm)", self.tire)
        form.addRow("Motor Kv", self.kv)
        form.addRow("Battery cells (S)", self.cells)
        form.addRow("Notes", self.notes)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._on_save)
        self.open_calc_btn = QPushButton("Open in Gear Calculator")
        self.open_calc_btn.clicked.connect(self._on_open_calc)
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._on_copy)
        form_buttons = QHBoxLayout()
        form_buttons.addWidget(save_btn)
        form_buttons.addWidget(self.open_calc_btn)
        form_buttons.addWidget(export_btn)
        form_buttons.addWidget(copy_btn)

        # Named gearing presets (e.g. "indoor carpet", "outdoor asphalt"): snapshot the
        # current gearing, or switch the form to a saved one. activated fires only on
        # user picks, never on programmatic repopulation in _fill_form.
        self._current_presets: list[dict] = []
        self.preset_combo = QComboBox()
        self.preset_combo.activated.connect(self._on_apply_preset)
        save_preset_btn = QPushButton("Save as preset…")
        save_preset_btn.clicked.connect(self._on_save_preset)
        del_preset_btn = QPushButton("Delete preset")
        del_preset_btn.clicked.connect(self._on_delete_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(save_preset_btn)
        preset_row.addWidget(del_preset_btn)

        # Run / maintenance log for the selected car.
        self._current_log: list[dict] = []
        self.log_table = QTableWidget(0, 3)
        self.log_table.setHorizontalHeaderLabels(("Date", "Type", "Note"))
        self.log_table.verticalHeader().hide()
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_kind = QComboBox()
        self.log_kind.addItems(("Run", "Maintenance"))
        self.log_note = QLineEdit()
        self.log_note.setPlaceholderText("What happened?")
        self.log_note.returnPressed.connect(self._on_add_log)
        add_log_btn = QPushButton("Add")
        add_log_btn.clicked.connect(self._on_add_log)
        remove_log_btn = QPushButton("Remove selected")
        remove_log_btn.clicked.connect(self._on_remove_log)
        log_add_row = QHBoxLayout()
        log_add_row.addWidget(self.log_kind)
        log_add_row.addWidget(self.log_note, 1)
        log_add_row.addWidget(add_log_btn)
        log_add_row.addWidget(remove_log_btn)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addLayout(preset_row)
        right.addLayout(form_buttons)
        right.addWidget(QLabel("<b>Run / maintenance log</b>"))
        right.addWidget(self.log_table)
        right.addLayout(log_add_row)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addLayout(right, 2)

        self._blank_form()
        self._reload_list()

    def _reload_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._cars = garage.list_cars()
        for car in self._cars:
            item = QListWidgetItem(car.get("name", "Unnamed"))
            item.setData(Qt.ItemDataRole.UserRole, car["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.compare_btn.setEnabled(len(self._cars) >= 2)  # needs two cars to compare
        self._apply_filter()

    _SEARCH_FIELDS = ("name", "chassis", "motor", "esc", "servo", "tires")

    def _apply_filter(self) -> None:
        """Hide list rows that don't match the search box, matching cached cars by index."""
        query = self.search.text().strip().lower()
        for i, car in enumerate(self._cars):
            match = not query or any(
                query in str(car.get(field, "")).lower() for field in self._SEARCH_FIELDS
            )
            self.list.item(i).setHidden(not match)

    def _blank_form(self) -> None:
        car = garage.new_car()
        self.current_id = None
        self._fill_form(car)
        self.name.clear()

    def _fill_form(self, car: dict) -> None:
        self.name.setText(car.get("name", ""))
        self.chassis.setText(car.get("chassis", ""))
        self.motor.setText(car.get("motor", ""))
        self.esc.setText(car.get("esc", ""))
        self.servo.setText(car.get("servo", ""))
        self.tires.setText(car.get("tires", ""))
        g = car.get("gearing", {})
        self.pinion.setValue(g.get("pinion", 22))
        self.spur.setValue(g.get("spur", 87))
        self.internal_ratio.setValue(g.get("internal_ratio", 1.9))
        self.tire.setValue(g.get("tire_diameter_mm", 60.0))
        self.kv.setValue(g.get("kv", 3000))
        self.cells.setValue(g.get("cells", 2))
        self.notes.setPlainText(car.get("notes", ""))
        self._current_log = list(car.get("log", []))
        self._fill_log_table()
        self._current_presets = list(car.get("presets", []))
        self.preset_combo.clear()
        self.preset_combo.addItem("— preset —", None)
        for p in self._current_presets:
            self.preset_combo.addItem(p.get("name", "preset"), p.get("name"))

    def _fill_log_table(self) -> None:
        self.log_table.setRowCount(len(self._current_log))
        for row, entry in enumerate(self._current_log):
            date = str(entry.get("date", ""))[:10]  # YYYY-MM-DD from the ISO stamp
            self.log_table.setItem(row, 0, QTableWidgetItem(date))
            self.log_table.setItem(row, 1, QTableWidgetItem(entry.get("kind", "")))
            self.log_table.setItem(row, 2, QTableWidgetItem(entry.get("note", "")))
        self.log_table.resizeColumnsToContents()
        self.log_table.horizontalHeader().setStretchLastSection(True)

    def _form_to_car(self) -> dict:
        # Start from the stored car (not new_car()) and overlay only the fields the
        # form edits, so values with no widget — computed gearing (fdr/rollout/top
        # speed the calculator saved) and any field added later — survive a Save
        # instead of being reset to their new_car() defaults.
        car = (self.current_id and garage.load_car(self.current_id)) or garage.new_car()
        if self.current_id:
            car["id"] = self.current_id
        car.update(
            {
                "name": self.name.text().strip() or "Unnamed",
                "chassis": self.chassis.text().strip(),
                "motor": self.motor.text().strip(),
                "esc": self.esc.text().strip(),
                "servo": self.servo.text().strip(),
                "tires": self.tires.text().strip(),
                "notes": self.notes.toPlainText(),
                "log": self._current_log,  # the log is edited in-form before Save
                "presets": self._current_presets,
            }
        )
        car.setdefault("gearing", {}).update(
            {
                "pinion": self.pinion.value(),
                "spur": self.spur.value(),
                "internal_ratio": self.internal_ratio.value(),
                "tire_diameter_mm": self.tire.value(),
                "kv": self.kv.value(),
                "cells": self.cells.value(),
            }
        )
        return car

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        car = garage.load_car(item.data(Qt.ItemDataRole.UserRole))
        if car is None:
            return
        self.current_id = car["id"]
        self._fill_form(car)

    def _on_new(self) -> None:
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self.list.blockSignals(False)
        self._blank_form()
        self.name.setFocus()

    def _select_id(self, car_id: str) -> None:
        """Select the list row for a car id, without firing _on_select."""
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == car_id:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                break

    def _on_save(self) -> None:
        saved = garage.save_car(self._form_to_car())
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Saved {saved['name']}", 5000)

    def _on_duplicate(self) -> None:
        if not self.current_id:  # nothing open to clone
            return
        dup = garage.save_car(garage.clone_car(self._form_to_car()))
        self.current_id = dup["id"]
        self._fill_form(dup)
        self._reload_list()
        self._select_id(dup["id"])
        _show_status(self, f"Duplicated to {dup['name']}", 5000)

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import car", "", "Car spec (*.json)"
        )
        if not path:
            return
        try:
            car = garage.load_car_file(path)
            self._fill_form(car)  # render first: surfaces bad field types before we save
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            # unreadable / invalid JSON / not an object / a valid object with junk field types
            QMessageBox.warning(self, "Import failed", str(e))
            return
        saved = garage.save_car(car)
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Imported {saved['name']}", 5000)

    def _on_delete(self) -> None:
        if not self.current_id:
            return
        garage.delete_car(self.current_id)
        self._blank_form()
        self._reload_list()

    def _on_open_calc(self) -> None:
        if self._on_open_in_calc:
            self._on_open_in_calc(self._form_to_car())

    def _on_compare(self) -> None:
        if len(self._cars) >= 2:
            _CompareDialog(self._cars, self.current_id, self).exec()

    def _on_save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        car = garage.save_car(garage.add_preset(self._form_to_car(), name.strip()))
        self.current_id = car["id"]
        self._reload_list()
        self._select_id(car["id"])
        self._fill_form(car)  # refresh the preset dropdown with the new snapshot

    def _on_apply_preset(self) -> None:
        name = self.preset_combo.currentData()  # None for the "— preset —" placeholder
        if not name:
            return
        car = garage.save_car(garage.apply_preset(self._form_to_car(), name))
        self._fill_form(car)  # reflect the applied gearing in the form

    def _on_delete_preset(self) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        car = garage.save_car(garage.delete_preset(self._form_to_car(), name))
        self._fill_form(car)

    def _on_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up garage", "rc-central-backup.zip", "Zip archive (*.zip)"
        )
        if not path:
            return
        try:
            backup.make_backup(Path(path))
        except OSError as e:  # unwritable path etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Backup failed", str(e))
            return
        _show_status(self, "Backed up garage", 5000)

    def _on_restore(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore garage", "", "Zip archive (*.zip)"
        )
        if not path:
            return
        if QMessageBox.question(
            self,
            "Restore",
            "Restore cars from this backup? Cars with the same id will be overwritten.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            n = backup.restore_backup(Path(path))
        except (OSError, zipfile.BadZipFile) as e:  # bad/corrupt zip must reach the user
            QMessageBox.warning(self, "Restore failed", str(e))
            return
        self._reload_list()  # Gear tab's picker self-refreshes on its showEvent
        # An open existing car may now be stale (the restore could have overwritten
        # its file); re-fill from disk so the next Save doesn't clobber the restore.
        # A blank form (unsaved new car, current_id None) is left alone — it saves
        # under a fresh id and can't clobber anything restored.
        if self.current_id:
            current = garage.load_car(self.current_id)
            if current:
                self._fill_form(current)
                self._select_id(self.current_id)
            else:
                self._blank_form()
        _show_status(self, f"Restored {n} car(s)", 5000)

    def _on_export(self) -> None:
        car = self._form_to_car()  # already carries computed gearing from disk
        suggested = f"{car['name'] or 'car'}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export spec sheet",
            suggested,
            "Text files (*.txt);;JSON (*.json);;All files (*)",
        )
        if not path:
            return
        # .json exports the raw car (re-importable); anything else is the readable sheet
        text = (
            json.dumps(car, indent=2)
            if path.endswith(".json")
            else garage.format_spec_sheet(car)
        )
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:  # unwritable path etc. must reach the user, not a traceback
            QMessageBox.warning(self, "Export failed", str(e))
            return
        _show_status(self, f"Exported {car['name']}", 5000)

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(garage.format_spec_sheet(self._form_to_car()))
        _show_status(self, "Copied spec sheet to clipboard", 5000)

    def _on_add_log(self) -> None:
        note = self.log_note.text().strip()
        if not note:
            return
        self._current_log.append(
            garage.new_log_entry(self.log_kind.currentText(), note)
        )
        self.log_note.clear()
        self._on_save()  # persist the entry (and the rest of the form) right away
        self._fill_log_table()  # _on_save reselects with signals blocked, so refresh here

    def _on_remove_log(self) -> None:
        row = self.log_table.currentRow()
        if not 0 <= row < len(self._current_log):
            return
        del self._current_log[row]
        self._on_save()
        self._fill_log_table()


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


# (setting, action if understeering, action if oversteering) — transcribed from the
# community "Drift RC Chassis Tuning Effects" chart. The chart's two conditional
# "Track Width (rear)" rows are split into low/high-speed rows with plain values.
_TUNING_ROWS: list[tuple[str, str, str]] = [
    ("Ride Height (front)", "Decrease", "Increase"),
    ("Ride Height (rear)", "Increase", "Decrease"),
    ("Ackerman", "Increase angle", "Decrease angle"),
    ("Front Toe", "Increase toe-out", "Decrease toe-out"),
    ("Rear Toe", "Decrease toe-in", "Increase toe-in"),
    ("Caster", "Decrease angle", "Increase angle"),
    ("Track Width (front)", "Decrease", "Increase"),
    ("Track Width (rear — low speed)", "Decrease", "Increase"),
    ("Track Width (rear — high speed)", "Increase", "Decrease"),
    ("Lower Shock Position (front)", "Move inward", "Move outward"),
    ("Lower Shock Position (rear)", "Move outward", "Move inward"),
    ("Upper Shock Position (rear)", "Make more vertical", "Make more laid down"),
    ("Springs (front)", "Install softer", "Install stiffer"),
    ("Springs (rear)", "Install stiffer", "Install softer"),
    (
        "Shock Oil/Damping (front)",
        "Install thinner (or larger piston holes)",
        "Install thicker (or smaller piston holes)",
    ),
    (
        "Shock Oil/Damping (rear)",
        "Install thicker (or smaller piston holes)",
        "Install thinner (or larger piston holes)",
    ),
    (
        "Front Camber Link/Roll",
        "Longer link and/or more parallel to lower arm",
        "Shorter link and/or move axis compared to lower arm",
    ),
    ("Rear Diff", "Tighten", "Loosen"),
]


# What each chassis setting physically does — tooltips for the chart's Setting
# column. Front/rear variants of one concept share a text via the _TIP_* locals.
_TIP_RIDE = (
    "Chassis height over the ground at that end. Lowering an end generally adds "
    "grip and reduces body roll at that end; raising does the opposite."
)
_TIP_TRACK = (
    "Distance between left/right contact patches at that end. Wider resists roll "
    "and softens weight transfer at that end; effects differ with corner speed."
)
_TIP_LOWER_SHOCK = (
    "Moving the shock's lower mount changes its lean. More laid-down = softer, "
    "more progressive action at that end; more upright = firmer and more direct."
)
_TIP_SPRINGS = (
    "Roll stiffness at that end. On low-grip drift surfaces the stiffer end "
    "generally slides first."
)
_TIP_OIL = (
    "How fast weight transfers onto that end. Thicker oil slows the transfer "
    "(calmer transitions); thinner speeds it up (snappier response)."
)
_TUNING_TIPS: dict[str, str] = {
    "Ride Height (front)": _TIP_RIDE,
    "Ride Height (rear)": _TIP_RIDE,
    "Ackerman": (
        "How much more the inside wheel steers than the outside wheel in a turn. "
        "More Ackerman sharpens low-speed turn-in; less keeps the wheels more "
        "parallel for smoother high-angle steering."
    ),
    "Front Toe": (
        "Angle of the front wheels vs. the chassis centerline. Toe-out sharpens "
        "initial turn-in; toe-in calms it."
    ),
    "Rear Toe": (
        "Rear toe-in adds rear stability and forward traction; reducing it frees "
        "the rear to rotate."
    ),
    "Caster": (
        "Backward lean of the steering axis. More caster adds straight-line "
        "stability and camber gain while steering; less makes steering more direct."
    ),
    "Track Width (front)": _TIP_TRACK,
    "Track Width (rear — low speed)": _TIP_TRACK,
    "Track Width (rear — high speed)": _TIP_TRACK,
    "Lower Shock Position (front)": _TIP_LOWER_SHOCK,
    "Lower Shock Position (rear)": _TIP_LOWER_SHOCK,
    "Upper Shock Position (rear)": (
        "Same lever as the lower mount: vertical shocks act firmer and more "
        "direct, laid-down shocks act softer initially."
    ),
    "Springs (front)": _TIP_SPRINGS,
    "Springs (rear)": _TIP_SPRINGS,
    "Shock Oil/Damping (front)": _TIP_OIL,
    "Shock Oil/Damping (rear)": _TIP_OIL,
    "Front Camber Link/Roll": (
        "Link length and angle set the roll center and camber gain — how the tire "
        "leans as the chassis rolls. Longer/more parallel links smooth the camber "
        "change and add grip."
    ),
    "Rear Diff": (
        "How tightly the rear wheels are coupled. Tighter (toward spool) drives "
        "both rears equally for predictable rotation; looser lets them "
        "differentiate for more forward bite."
    ),
}


# (WT, approx. cSt) — the commonly circulated shock-oil conversion; scales
# differ by brand, so the tab carries an "approximate" caption.
_OIL_ROWS: list[tuple[str, str]] = [
    ("10", "100"),
    ("15", "150"),
    ("20", "200"),
    ("25", "275"),
    ("30", "350"),
    ("35", "425"),
    ("40", "500"),
    ("45", "575"),
    ("50", "650"),
    ("60", "800"),
]


# (symptom, gyro adjustment) for drift gyros.
_GYRO_ROWS: list[tuple[str, str]] = [
    ("Tail wags / oscillates on straights", "Lower gain"),
    ("Snap-spins on throttle transitions", "Increase gain"),
    ("Counter-steer too slow, spins before catching", "Increase gain (or faster servo response)"),
    ("Steering fights your inputs, feels robotic", "Lower gain"),
    ("Won't hold deep angle, self-straightens", "Lower gain"),
    ("Wanders at speed, needs constant correction", "Raise gain slightly"),
]


class _ChassisGuide(QWidget):
    """The understeer/oversteer chart with click-to-expand setting explainers.

    Click a setting row and its explanation drops in as a spanned row directly
    beneath it (one open at a time; clicking another setting moves it there).
    Implemented on QTableWidget with insertRow/removeRow — QTreeWidget branch
    expand/collapse cycles live-lock Qt's UIA accessibility bridge and freeze
    the GUI under screen readers / UI automation (PySide6 6.11.1, 2026-07-16).
    """

    def __init__(self):
        super().__init__()
        self._open_row: int | None = None  # table index of the open setting row, if any

        self.table = QTableWidget(len(_TUNING_ROWS), 3)
        self.table.setHorizontalHeaderLabels(("Setting", "If understeering", "If oversteering"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)  # camber-link cells are long
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_TUNING_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem("▸ " + text if col == 0 else text)
                if col == 0:
                    item.setToolTip(_TUNING_TIPS[text])
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self.table.cellClicked.connect(self._toggle_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter settings…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        # Same-parent QRadioButtons are auto-exclusive; no QButtonGroup needed.
        self.radio_both = QRadioButton("Both")
        self.radio_under = QRadioButton("Understeering")
        self.radio_over = QRadioButton("Oversteering")
        self.radio_both.setChecked(True)  # before connect: table paint not needed yet
        for radio in (self.radio_both, self.radio_under, self.radio_over):
            radio.toggled.connect(self._highlight)

        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("Symptom:"))
        controls.addWidget(self.radio_both)
        controls.addWidget(self.radio_under)
        controls.addWidget(self.radio_over)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drift chassis tuning effects — click a setting for details"))
        layout.addLayout(controls)
        layout.addWidget(self.table)

    def _setting_name(self, row: int) -> str:
        return self.table.item(row, 0).text()[2:]  # strip the "▸ "/"▾ " prefix

    def _toggle_row(self, row: int, column: int = 0) -> None:
        if self._open_row is not None and row == self._open_row + 1:
            return  # clicks on the explanation row itself do nothing
        reopen = None if row == self._open_row else row
        if self._open_row is not None:
            was = self._open_row
            self.table.removeRow(was + 1)
            self.table.item(was, 0).setText("▸ " + self._setting_name(was))
            self._open_row = None
            if reopen is not None and reopen > was:
                reopen -= 1  # rows below the removed explanation shifted up
        if reopen is None:
            return
        name = self._setting_name(reopen)
        exp = QTableWidgetItem(_TUNING_TIPS[name])
        font = exp.font()
        font.setItalic(True)
        exp.setFont(font)
        self.table.insertRow(reopen + 1)
        self.table.setItem(reopen + 1, 0, exp)
        self.table.setSpan(reopen + 1, 0, 1, 3)
        self.table.item(reopen, 0).setText("▾ " + name)
        self._open_row = reopen
        self._fit_explanation()

    def _fit_explanation(self) -> None:
        # the delegate paints wrapped text across the span but sizes the row as
        # a single line, so compute the wrapped height ourselves
        if self._open_row is None:
            return
        row = self._open_row + 1
        item = self.table.item(row, 0)
        metrics = QFontMetrics(item.font())
        width = self.table.viewport().width() - 24
        rect = metrics.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, item.text())
        self.table.setRowHeight(row, rect.height() + 12)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_explanation()  # re-wrap the open explanation at the new width

    def _apply_filter(self, text: str) -> None:
        if self._open_row is not None:
            self._toggle_row(self._open_row)  # close it; rows are 1:1 settings again
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, needle not in self._setting_name(row).lower())

    def _highlight(self, checked: bool) -> None:
        if not checked:  # a radio switch fires toggled twice (old off, new on); paint once
            return
        col_on = 1 if self.radio_under.isChecked() else 2 if self.radio_over.isChecked() else None
        for row in range(self.table.rowCount()):
            for col in (1, 2):
                item = self.table.item(row, col)
                if item is None:  # the spanned explanation row has no symptom cells
                    continue
                if col == col_on:
                    item.setBackground(QColor(_ACCENT))
                    item.setForeground(QColor("white"))  # readable on accent in both themes
                else:
                    # clear back to theme defaults (None removes the explicit brush)
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)


class _OilGuide(QWidget):
    """Shock oil WT ↔ cSt conversion reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_OIL_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("WT", "approx. cSt"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_OIL_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

        note = QLabel("Approximate — scales differ by brand; check your oil maker's own chart.")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table)


class _GyroGuide(QWidget):
    """Drift gyro symptom → gain adjustment reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_GYRO_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("Symptom", "Adjustment"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_GYRO_ROWS):
            for col, text in enumerate(texts):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeRowsToContents()

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)


class _TuningLog(QWidget):
    """Per-car tuning notes, stored as kind="Tuning" entries in the car's garage log.

    Reuses the Garage's log schema untouched, so entries also appear in the Garage
    tab's log table and ride along with backup/restore/export for free. Add/Delete
    load the car fresh from disk so edits made meanwhile in the Garage tab are
    never clobbered by a stale dict held here.
    """

    def __init__(self):
        super().__init__()
        self._shown: list[dict] = []  # entries behind the table rows, newest first

        self.car_combo = QComboBox()
        self.hint = QLabel("Create a car in the Garage first.")
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. front springs softer → better turn-in")
        self.add_btn = QPushButton("Add")
        self.delete_btn = QPushButton("Delete selected")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("Date", "Note"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        # connect only now that self.table exists (same trap as _CompareDialog)
        self.car_combo.currentIndexChanged.connect(self._render)
        self.add_btn.clicked.connect(self._add)
        self.note.returnPressed.connect(self._add)
        self.delete_btn.clicked.connect(self._delete)

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.note, 1)
        entry_row.addWidget(self.add_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.car_combo)
        layout.addWidget(self.hint)
        layout.addLayout(entry_row)
        layout.addWidget(self.table)
        layout.addWidget(self.delete_btn)
        self._reload_cars()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # cars are created/deleted on the Garage tab; refresh on every switch here
        self._reload_cars()
        super().showEvent(event)

    def _reload_cars(self) -> None:
        current = self.car_combo.currentData()
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        for car in garage.list_cars():
            self.car_combo.addItem(car.get("name", "Unnamed"), car["id"])
        idx = self.car_combo.findData(current)
        self.car_combo.setCurrentIndex(max(0, idx))  # keep pick; else first car
        self.car_combo.blockSignals(False)
        has_cars = self.car_combo.count() > 0
        for widget in (self.car_combo, self.note, self.add_btn, self.delete_btn):
            widget.setEnabled(has_cars)
        self.hint.setVisible(not has_cars)
        self._render()

    def _render(self) -> None:
        car = garage.load_car(self.car_combo.currentData() or "") or {}
        self._shown = sorted(
            (e for e in car.get("log", []) if e.get("kind") == "Tuning"),
            key=lambda e: e.get("date", ""),
            reverse=True,
        )
        self.table.setRowCount(len(self._shown))
        for row, entry in enumerate(self._shown):
            date = str(entry.get("date", ""))[:10]  # YYYY-MM-DD from the ISO stamp
            self.table.setItem(row, 0, QTableWidgetItem(date))
            self.table.setItem(row, 1, QTableWidgetItem(entry.get("note", "")))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _add(self) -> None:
        note = self.note.text().strip()
        car_id = self.car_combo.currentData()
        if not note or not car_id:
            return
        car = garage.load_car(car_id)
        if car is None:  # deleted on the Garage tab since the picker was filled
            self._reload_cars()
            return
        car.setdefault("log", []).append(garage.new_log_entry("Tuning", note))
        garage.save_car(car)
        self.note.clear()
        self._render()

    def _delete(self) -> None:
        row = self.table.currentRow()
        car_id = self.car_combo.currentData()
        if row < 0 or row >= len(self._shown) or not car_id:
            return
        entry_id = self._shown[row].get("id")
        car = garage.load_car(car_id)
        if car is None:
            self._reload_cars()
            return
        car["log"] = [e for e in car.get("log", []) if e.get("id") != entry_id]
        garage.save_car(car)
        self._render()


class TuningTab(QWidget):
    """Tuning references in sub-tabs: chassis chart, shock oil, gyro, my log."""

    def __init__(self):
        super().__init__()
        self.subtabs = QTabWidget()
        self.chassis = _ChassisGuide()
        self.subtabs.addTab(self.chassis, "Chassis")
        self.oil = _OilGuide()
        self.subtabs.addTab(self.oil, "Shock Oil")
        self.gyro = _GyroGuide()
        self.subtabs.addTab(self.gyro, "Gyro")
        self.mylog = _TuningLog()
        self.subtabs.addTab(self.mylog, "My Log")
        layout = QVBoxLayout(self)
        layout.addWidget(self.subtabs)


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


def _make_palette(
    *, window, base, text, button, alt_base, tooltip_base, highlighted_text, disabled
) -> QPalette:
    """The one role→colour sequence both themes share; Highlight is always _ACCENT.

    Every colour a theme varies is a parameter, so the two palettes can't drift in
    which roles they set — only in the values they pass. All args are QColor.
    """
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    p.setColor(QPalette.ColorRole.ToolTipBase, tooltip_base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, button)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, QColor(_ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, highlighted_text)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return p


def _dark_palette() -> QPalette:
    """A neutral dark grey palette; Highlight is the shared accent (_ACCENT)."""
    window, text = QColor("#353535"), QColor("#ffffff")
    return _make_palette(
        window=window, base=QColor("#2a2a2a"), text=text, button=window,
        alt_base=window, tooltip_base=window, highlighted_text=text,
        disabled=QColor("#7f7f7f"),
    )


def _light_palette() -> QPalette:
    """A cool-neutral light palette that mirrors the dark one and shares the accent.
    Deliberately flat — the native Windows style renders tan tab/header gradients
    and accent-colored data text that clash with the app's identity."""
    return _make_palette(
        window=QColor("#f4f5f7"), base=QColor("#ffffff"), text=QColor("#1c1f23"),
        button=QColor("#e9ebef"), alt_base=QColor("#eef0f3"), tooltip_base=QColor("#ffffff"),
        highlighted_text=QColor("#ffffff"), disabled=QColor("#a0a4ab"),
    )


def apply_theme(app: QApplication, dark: bool) -> None:
    """Paint the app with the dark or light palette on the Fusion style.

    Fusion (not the native OS style) is used in BOTH modes so the app has one flat,
    controlled look with a shared blue accent — the native Windows style renders tan
    tab/header gradients and accent-colored data text that don't match either palette.
    """
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if dark else _light_palette())


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
