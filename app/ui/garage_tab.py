"""The Garage tab: create/edit/delete RC car spec sheets, and the compare dialog."""

import json
import zipfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import backup, garage
from app.ui.common import _show_status


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
