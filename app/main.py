"""RC Central - install and launch RC drift setup tools, plus gearing + garage."""

import sys
import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import __version__, catalog, garage, gearing, installer, launcher, updater


class _InstallSignals(QObject):
    """Bridge from the download thread back to the Qt main thread."""

    progress = Signal(int, int)
    done = Signal()
    error = Signal(str)


class ToolsTab(QWidget):
    """The catalog: install/launch each vendor tool. Formerly the whole window."""

    COLS = ("Tool", "Vendor", "Version", "Status", "")

    def __init__(self):
        super().__init__()
        self.tools = catalog.load_catalog()

        self.table = QTableWidget(len(self.tools), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # ponytail: one shared progress bar; per-row bars if parallel installs matter
        self.progress = QProgressBar()
        self.progress.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.progress)

        for row, tool in enumerate(self.tools):
            name = QTableWidgetItem(tool["name"])
            name.setToolTip(tool.get("description", ""))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(tool["vendor"]))
            self.table.setItem(row, 2, QTableWidgetItem(tool["version"]))
            button = QToolButton()
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(lambda _=False, r=row: self._on_action(r))
            menu = QMenu(button)
            menu.addAction(
                "Locate existing install…",
                lambda _=False, r=row: self._locate_existing(r),
            )
            button.setMenu(menu)
            self.table.setCellWidget(row, 4, button)
            self._refresh_row(row)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

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
        state = installer.get_state(tool["id"])
        if state is None:
            status, action = "Not installed", "Install"
        elif state["version"] != tool["version"]:
            status, action = f"Installed v{state['version']}", "Update"
        else:
            status, action = f"Installed v{state['version']}", "Launch"
        self.table.setItem(row, 3, QTableWidgetItem(status))
        self.table.cellWidget(row, 4).setText(action)

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
        self._status(f"Linked existing {tool['name']}", 5000)

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

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.save_btn)
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
        except ValueError:
            for lbl in (self.fdr_out, self.rollout_out, self.kmh_out, self.mph_out):
                lbl.setText("—")
            return
        self.fdr_out.setText(f"{r['fdr']:.2f}")
        self.rollout_out.setText(f"{r['rollout_mm']:.1f}")
        self.kmh_out.setText(f"{r['top_speed_kmh']:.1f}")
        self.mph_out.setText(f"{r['top_speed_mph']:.1f}")

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

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._on_new)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._on_delete)
        left_buttons = QHBoxLayout()
        left_buttons.addWidget(new_btn)
        left_buttons.addWidget(del_btn)
        left = QVBoxLayout()
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
        form_buttons = QHBoxLayout()
        form_buttons.addWidget(save_btn)
        form_buttons.addWidget(self.open_calc_btn)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addLayout(form_buttons)

        layout = QHBoxLayout(self)
        layout.addLayout(left, 1)
        layout.addLayout(right, 2)

        self._blank_form()
        self._reload_list()

    def _reload_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for car in garage.list_cars():
            item = QListWidgetItem(car.get("name", "Unnamed"))
            item.setData(Qt.ItemDataRole.UserRole, car["id"])
            self.list.addItem(item)
        self.list.blockSignals(False)

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

    def _on_save(self) -> None:
        saved = garage.save_car(self._form_to_car())
        self.current_id = saved["id"]
        self._reload_list()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == saved["id"]:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                break
        win = self.window()
        if isinstance(win, QMainWindow):
            win.statusBar().showMessage(f"Saved {saved['name']}", 5000)

    def _on_delete(self) -> None:
        if not self.current_id:
            return
        garage.delete_car(self.current_id)
        self._blank_form()
        self._reload_list()

    def _on_open_calc(self) -> None:
        if self._on_open_in_calc:
            self._on_open_in_calc(self._form_to_car())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"RC Central v{__version__}")
        self.resize(760, 500)

        self.tabs = QTabWidget()
        self.tools_tab = ToolsTab()
        self.gear_tab = GearTab()
        self.garage_tab = GarageTab(on_open_in_calc=self._open_in_calc)
        for widget, label in (
            (self.tools_tab, "Tools"),
            (self.gear_tab, "Gear Calculator"),
            (self.garage_tab, "Garage"),
        ):
            self.tabs.addTab(widget, label)
        self.setCentralWidget(self.tabs)

        # back-compat: existing tests (and any external callers) reach the table here
        self.table = self.tools_tab.table

    def _open_in_calc(self, car: dict) -> None:
        self.gear_tab.load_from_car(car)
        self.tabs.setCurrentWidget(self.gear_tab)


def main() -> None:
    app = QApplication(sys.argv)
    updater.cleanup()
    win = MainWindow()
    win.show()
    threading.Thread(target=updater.fetch_update, daemon=True).start()
    code = app.exec()
    updater.apply_pending()  # swap in a downloaded hub update on the way out
    sys.exit(code)


if __name__ == "__main__":
    main()
