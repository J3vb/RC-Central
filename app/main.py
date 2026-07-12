"""RC Central - install and launch RC drift setup tools, plus gearing + garage."""

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
    QScrollArea,
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
    catalog,
    garage,
    gearing,
    installer,
    launcher,
    logsetup,
    updater,
)


def _asset_path(name: str) -> Path:
    # _MEIPASS is where PyInstaller unpacks --add-data at runtime; fall back to source tree.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "app" / "assets" / name


def app_icon() -> QIcon:
    return QIcon(str(_asset_path("icon.png")))


# Pretty display names for catalog category codes; unknowns fall back to .title().
_CATEGORY_LABELS = {"esc": "ESC", "servo": "Servo", "radio": "Radio", "gyro": "Gyro"}


def _is_software(tool: dict) -> bool:
    """A tool RC Central can download and launch (vs. an info-only card)."""
    return "download" in tool


def _info_url(tool: dict) -> str | None:
    """Where an info-only card's Manual button points: first manual link, else homepage."""
    links = tool.get("links") or []
    return links[0]["url"] if links else tool.get("homepage")


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


class _InstallSignals(QObject):
    """Bridge from the download thread back to the Qt main thread."""

    progress = Signal(int, int)
    done = Signal()
    error = Signal(str)


class ToolsTab(QWidget):
    """The catalog: install/launch each vendor tool. Formerly the whole window."""

    COLS = ("Tool", "Vendor", "Version", "Status", "", "Website")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        self.tools = catalog.load_catalog() if tools is None else tools

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
        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(self.category_filter)

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
            if _is_software(tool):
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
                button.setMenu(menu)
            self.table.setCellWidget(row, 4, button)
            self.table.setCellWidget(row, 5, _link_button("Website", tool.get("homepage")))
            self._refresh_row(row)
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

    def _status(self, msg: str, timeout: int = 0) -> None:
        """Show a message on the window's status bar, if we're inside one."""
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(msg, timeout)

    def _clear_status(self) -> None:
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().clearMessage()

    def _refresh_row(self, row: int) -> None:
        tool = self.tools[row]
        if not _is_software(tool):
            self.table.setItem(row, 3, QTableWidgetItem("No PC software"))
            button = self.table.cellWidget(row, 4)
            button.setText("Manual")
            button.setEnabled(_info_url(tool) is not None)  # no link -> nothing to open
            return
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

    def _on_action(self, row: int) -> None:
        tool = self.tools[row]
        if not _is_software(tool):
            url = _info_url(tool)
            if url:
                QDesktopServices.openUrl(QUrl(url))
            return
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
        self._status(f"Uninstalled {tool['name']}", 5000)

    def _install(self, row: int, tool: dict) -> None:
        self.table.cellWidget(row, 4).setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self._status(f"Downloading {tool['name']}...")

        signals = _InstallSignals(self)  # parented so it outlives this scope
        signals.progress.connect(self._on_progress)
        signals.done.connect(lambda r=row: self._install_finished(r, None))
        signals.error.connect(lambda msg, r=row: self._install_finished(r, msg))

        def work():
            try:
                installer.install(tool, progress=signals.progress.emit)
                signals.done.emit()
            except Exception as e:  # anything here must reach the user, not a traceback
                signals.error.emit(str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setMaximum(total)  # 0 total -> busy indicator
        self.progress.setValue(done if total else 0)

    def _install_finished(self, row: int, error: str | None) -> None:
        self.progress.hide()
        self.table.cellWidget(row, 4).setEnabled(True)
        self._refresh_row(row)
        if error:
            self._clear_status()
            QMessageBox.warning(self, "Install failed", error)
        else:
            self._status(f"Installed {self.tools[row]['name']}", 5000)


class ManualsTab(QWidget):
    """One-stop reference: every catalog tool's official website + manual links.

    Pure links (no install/launch), so unlike the Tools tab it is cross-platform.
    """

    _SEARCH_FIELDS = ("name", "vendor", "category", "description")

    def __init__(self, tools: list[dict] | None = None):
        super().__init__()
        self._tools = sorted(
            catalog.load_catalog() if tools is None else tools,
            key=lambda t: (t.get("category", ""), t["name"].lower()),
        )

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search manuals…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        inner = QWidget()
        col = QVBoxLayout(inner)
        self._rows: list[QWidget] = []  # index-aligned with self._tools, for filtering
        self._headers: dict[str, QLabel] = {}  # category -> its bold section header
        current_cat = None
        for tool in self._tools:
            cat = tool.get("category", "")
            if cat != current_cat:  # a bold section header each time the category changes
                current_cat = cat
                header = QLabel(f"<b>{_CATEGORY_LABELS.get(cat, cat.title()) or 'Other'}</b>")
                self._headers[cat] = header
                col.addWidget(header)
            row = self._tool_row(tool)
            self._rows.append(row)
            col.addWidget(row)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(scroll)

    def _tool_row(self, tool: dict) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{tool['name']} — {tool['vendor']}")
        label.setToolTip(tool.get("description", ""))
        h.addWidget(label, 1)
        h.addWidget(_link_button("Website", tool.get("homepage")))
        for link in tool.get("links", []):
            h.addWidget(_link_button(link["name"], link.get("url")))
        return row

    def _apply_filter(self) -> None:
        """Hide tool rows that don't match the search box, and any now-empty header."""
        query = self.search.text().strip().lower()
        shown_cats: set[str] = set()
        for tool, row in zip(self._tools, self._rows):
            match = not query or any(
                query in str(tool.get(f, "")).lower() for f in self._SEARCH_FIELDS
            )
            row.setVisible(match)
            if match:
                shown_cats.add(tool.get("category", ""))
        for cat, header in self._headers.items():
            header.setVisible(not query or cat in shown_cats)


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

        form = QFormLayout()
        form.addRow("Load from car", self.car_picker)
        form.addRow("Pinion (teeth)", self.pinion)
        form.addRow("Spur (teeth)", self.spur)
        form.addRow("Internal ratio", self.internal_ratio)
        form.addRow("Tire diameter (mm)", self.tire)
        form.addRow("Motor Kv", self.kv)
        form.addRow("Battery cells (S)", self.cells)
        form.addRow(QLabel("<b>Results</b>"))
        form.addRow("Final drive ratio", self.fdr_out)
        form.addRow("Rollout (mm)", self.rollout_out)
        form.addRow("Top speed (km/h)", self.kmh_out)
        form.addRow("Top speed (mph)", self.mph_out)

        self.save_btn = QPushButton("Save results to selected car")
        self.save_btn.clicked.connect(self._save_to_car)
        self.save_btn.setEnabled(False)

        # What-if sweep: the current pinion ±3, so tuners see the effect of a
        # pinion swap (the common drift adjustment) at a glance. The base row is bold.
        self.sweep_table = QTableWidget(0, 4)
        self.sweep_table.setHorizontalHeaderLabels(("Pinion", "FDR", "Rollout (mm)", "km/h"))
        self.sweep_table.verticalHeader().hide()
        self.sweep_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sweep_table.horizontalHeader().setStretchLastSection(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.save_btn)
        layout.addWidget(self.sweep_table)
        layout.addStretch(1)

        self._reload_cars()
        self._recompute()

    def _reload_cars(self) -> None:
        """Refresh the car picker from the garage (blocks signals to avoid a spurious load)."""
        self.car_picker.blockSignals(True)
        self.car_picker.clear()
        self.car_picker.addItem("— none —", None)
        for car in garage.list_cars():
            self.car_picker.addItem(car.get("name", "Unnamed"), car["id"])
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
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Saved gearing to {car['name']}", 5000)


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
        left_buttons = QHBoxLayout()
        left_buttons.addWidget(new_btn)
        left_buttons.addWidget(import_btn)
        left_buttons.addWidget(dup_btn)
        left_buttons.addWidget(del_btn)
        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.list)
        left.addLayout(left_buttons)

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
        car = garage.new_car()
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
                "log": self._current_log,
            }
        )
        car["gearing"].update(
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
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Saved {saved['name']}", 5000)

    def _on_duplicate(self) -> None:
        if not self.current_id:  # nothing open to clone
            return
        dup = garage.save_car(garage.clone_car(self._form_to_car()))
        self.current_id = dup["id"]
        self._fill_form(dup)
        self._reload_list()
        self._select_id(dup["id"])
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Duplicated to {dup['name']}", 5000)

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
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Imported {saved['name']}", 5000)

    def _on_delete(self) -> None:
        if not self.current_id:
            return
        garage.delete_car(self.current_id)
        self._blank_form()
        self._reload_list()

    def _on_open_calc(self) -> None:
        if self._on_open_in_calc:
            self._on_open_in_calc(self._form_to_car())

    def _on_export(self) -> None:
        car = self._form_to_car()
        if self.current_id:  # the form doesn't hold computed gearing; carry it from disk
            stored_gearing = (garage.load_car(self.current_id) or {}).get("gearing", {})
            for key in ("fdr", "rollout_mm", "top_speed_kmh"):
                if stored_gearing.get(key) is not None:
                    car["gearing"][key] = stored_gearing[key]
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
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Exported {car['name']}", 5000)

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(garage.format_spec_sheet(self._form_to_car()))
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage("Copied spec sheet to clipboard", 5000)

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
            (self.gear_tab, "Gear Calculator"),
            (self.garage_tab, "Garage"),
            (self.log_tab, "Log"),
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
        settings = QSettings("RCCentral", "RCCentral")
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        tab = int(settings.value("tab", 0))
        self.tabs.setCurrentIndex(max(0, min(tab, self.tabs.count() - 1)))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        settings = QSettings("RCCentral", "RCCentral")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("tab", self.tabs.currentIndex())
        super().closeEvent(event)

    def _build_update_banner(self) -> None:
        banner = QToolBar("Update")
        banner.setMovable(False)
        banner.setFloatable(False)
        banner.setStyleSheet(
            "QToolBar { background: #1f6feb; border: none; padding: 4px 8px; spacing: 8px; }"
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
    updater.cleanup()
    win = MainWindow()
    win.show()

    def check_for_update():
        if updater.fetch_update():
            win.update_ready.emit(updater.staged_version() or "")

    threading.Thread(target=check_for_update, daemon=True).start()
    code = app.exec()
    # Only swap the binary in when the user asked for it from the banner, then
    # relaunch into the new version so "Restart & update" actually restarts.
    if win.update_consented:
        updater.apply_pending()
        if getattr(sys, "frozen", False):
            try:
                subprocess.Popen([sys.executable])
            except OSError:
                updater.log.exception("could not relaunch after applying the update")
    sys.exit(code)


if __name__ == "__main__":
    main()
