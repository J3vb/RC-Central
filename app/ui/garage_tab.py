"""The Garage tab: create/edit/delete RC car spec sheets, and the compare dialog."""

import json
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import backup, garage, parts
from app.ui.common import _ACCENT, _show_status
from app.ui.setup_diagram import SetupDiagramPanel


def _part_combo() -> QComboBox:
    """An editable combo for a spec field: browse the known parts, or type your own.

    NoInsert matters - an editable combo otherwise appends whatever is typed as a real
    item on Enter, which would quietly grow a duplicate-riddled list that _refresh_
    suggestions then rebuilds anyway. MatchContains lets "yd-2" find "Yokomo YD-2S".
    """
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = combo.completer()
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    return combo


class _CompareDialog(QDialog):
    """Read-only side-by-side of two cars' spec fields; differing rows highlighted.

    Highlight uses the app accent bg + white text so it stays readable on accent
    in both themes.
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
        self.setMinimumSize(440, 340)
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
                    item.setBackground(QColor(_ACCENT))
                    item.setForeground(QColor("white"))  # readable on accent in both themes
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)


class GarageTab(QWidget):
    """Create/edit/delete RC car spec sheets."""

    # The car the user is working on (its id, or None) — emitted from user-driven
    # actions only. Programmatic selection goes through open_car(), which never
    # emits, so the Workshop header and this tab can sync without loops.
    car_selected = Signal(object)
    # Emitted after a "Restore all": car files on disk changed under possibly-unchanged
    # ids, so the Gearing/Tuning sub-tabs must force-reload past their same-id guards.
    garage_restored = Signal()
    # Emitted when a Save seeded gearing from the car's chassis. Same problem as above
    # (data changed under an unchanged id) but a distinct cause, so it gets its own
    # signal rather than borrowing garage_restored's — only Gearing needs to react.
    gearing_seeded = Signal()

    def __init__(self):
        super().__init__()
        self.current_id: str | None = None

        self._cars: list[dict] = []  # cache behind the list, for live filtering
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cars…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        self.empty_hint = QLabel("No cars yet — click New to add your first car.")
        new_btn = QPushButton("&New")
        new_btn.clicked.connect(self._on_new)
        import_btn = QPushButton("&Import…")
        import_btn.clicked.connect(self._on_import)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._on_duplicate)
        del_btn = QPushButton("&Delete")
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
        left.addWidget(self.empty_hint)
        left.addLayout(left_buttons)
        left.addLayout(backup_row)

        self.name = QLineEdit()
        # Part fields are editable combos, never closed lists: the suggestions are a
        # convenience and any value the user types is stored verbatim (see app/parts.py).
        self.chassis = _part_combo()
        self.motor = _part_combo()
        self.esc = _part_combo()
        self.servo = _part_combo()
        self.tires = _part_combo()
        self._part_fields = {
            "chassis": self.chassis,
            "motor": self.motor,
            "esc": self.esc,
            "servo": self.servo,
            "tires": self.tires,
        }
        self.notes = QPlainTextEdit()

        # Chassis setup is edited on the diagram panel (third column, below): value
        # boxes around a car schematic. Aliases keep _fill_form/_form_to_car, the
        # base-setup handlers and the tests addressing the setup UI exactly as when
        # these were plain form rows.
        self.setup_panel = SetupDiagramPanel()
        self.setup_panel.save_base_btn.clicked.connect(self._on_save_base)
        self.setup_panel.apply_base_btn.clicked.connect(self._on_apply_base)
        self._setup_fields = self.setup_panel.fields
        self.save_base_btn = self.setup_panel.save_base_btn
        self.apply_base_btn = self.setup_panel.apply_base_btn

        # Gearing (pinion/spur/…) is edited on the Gearing sub-tab, not here; the
        # form only overlays spec fields, so a car's gearing block rides through Save.
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Chassis", self.chassis)
        form.addRow("Motor", self.motor)
        form.addRow("ESC", self.esc)
        form.addRow("Servo", self.servo)
        form.addRow("Tires", self.tires)
        form.addRow("Notes", self.notes)

        save_btn = QPushButton("&Save")
        save_btn.clicked.connect(self._on_save)
        export_btn = QPushButton("&Export…")
        export_btn.clicked.connect(self._on_export)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._on_copy)
        form_buttons = QHBoxLayout()
        form_buttons.addWidget(save_btn)
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
        remove_log_btn = QPushButton("Delete selected")
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
        layout.addWidget(self.setup_panel, 1)

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
        self.empty_hint.setVisible(not self._cars)
        self._refresh_suggestions()  # a car saved with a new part name offers it next time
        self._apply_filter()

    def _refresh_suggestions(self) -> None:
        """Repopulate the part combos from the curated seed plus the garage's own values.

        clear() also wipes an editable combo's line edit, so whatever the user has typed
        is captured and restored around the rebuild - otherwise saving a car would blank
        the form fields underneath them.
        """
        for field, combo in self._part_fields.items():
            typed = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(parts.suggestions(field, self._cars))
            combo.setCurrentText(typed)
            combo.blockSignals(False)

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
        for field, combo in self._part_fields.items():
            combo.setCurrentText(car.get(field, ""))
        setup = car.get("setup") or {}  # absent on cars saved before the setup block
        for key, edit in self._setup_fields.items():
            edit.setText(str(setup.get(key) or ""))
        self.apply_base_btn.setEnabled(car.get("base_setup") is not None)
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
        # Start from the stored car (not new_car()) and overlay only the fields the
        # form edits, so values with no widget — the whole gearing block and presets
        # (owned by the Gearing sub-tab) and any field added later — survive a Save
        # instead of being reset to their new_car() defaults. The exceptions are
        # _on_save's garage.apply_chassis_defaults / apply_chassis_setup calls, which
        # may seed gearing or setup from a newly picked chassis — and only ever on a
        # car whose corresponding block is untouched.
        car = (self.current_id and garage.load_car(self.current_id)) or garage.new_car()
        if self.current_id:
            car["id"] = self.current_id
        car.update(
            {
                "name": self.name.text().strip() or "Unnamed",
                **{f: c.currentText().strip() for f, c in self._part_fields.items()},
                "setup": {k: e.text().strip() for k, e in self._setup_fields.items()},
                "notes": self.notes.toPlainText(),
                "log": self._current_log,  # the log is edited in-form before Save
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
        self.car_selected.emit(car["id"])

    def open_car(self, car_id: str | None) -> None:
        """Programmatically open a car (or blank the form). Never emits car_selected —
        this is the Workshop header's entry point, and emitting back would loop."""
        car = garage.load_car(car_id) if car_id else None
        if car:
            self.current_id = car["id"]
            self._fill_form(car)
            self._select_id(car["id"])
        else:
            self.list.blockSignals(True)
            self.list.clearSelection()
            self.list.setCurrentItem(None)
            self.list.blockSignals(False)
            self._blank_form()

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
        car = self._form_to_car()
        seeded = garage.apply_chassis_defaults(car)
        setup_seeded = garage.apply_chassis_setup(car)
        saved = garage.save_car(car)
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        if setup_seeded:
            # seeding changed setup values behind the form's back; show them
            self._fill_form(saved)
        applied = [w for w, s in (("gearing", seeded), ("base setup", setup_seeded)) if s]
        if applied:
            _show_status(
                self,
                f"Saved {saved['name']} — applied {saved['chassis']} " + " + ".join(applied),
                6000,
            )
        else:
            _show_status(self, f"Saved {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])  # covers create and rename alike
        if seeded:
            # the car's gearing changed under an unchanged id, which GearTab's same-id
            # guard would otherwise ignore — same situation as a garage restore
            self.gearing_seeded.emit()

    def _on_save_base(self) -> None:
        saved = garage.save_car(garage.save_base_setup(self._form_to_car()))
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        self.apply_base_btn.setEnabled(True)
        _show_status(self, f"Saved base setup for {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_apply_base(self) -> None:
        car = self._form_to_car()
        if not garage.apply_base_setup(car):  # no base saved yet
            return
        saved = garage.save_car(car)
        self.current_id = saved["id"]
        self._fill_form(saved)
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Applied base setup to {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_duplicate(self) -> None:
        if not self.current_id:  # nothing open to clone
            return
        dup = garage.save_car(garage.clone_car(self._form_to_car()))
        self.current_id = dup["id"]
        self._fill_form(dup)
        self._reload_list()
        self._select_id(dup["id"])
        _show_status(self, f"Duplicated to {dup['name']}", 5000)
        self.car_selected.emit(dup["id"])

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
        self.car_selected.emit(saved["id"])

    def _on_delete(self) -> None:
        if not self.current_id:
            return
        name = self.name.text().strip() or "this car"
        if QMessageBox.question(
            self, "Delete", f"Delete '{name}' and everything saved with it (specs, gearing, setup, presets, log)?"
        ) != QMessageBox.StandardButton.Yes:
            return
        garage.delete_car(self.current_id)
        self._blank_form()
        self._reload_list()
        self.car_selected.emit(None)
        _show_status(self, f"Deleted {name}", 5000)

    def _on_compare(self) -> None:
        if len(self._cars) >= 2:
            dlg = _CompareDialog(self._cars, self.current_id, self)
            dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # else it lingers per open
            dlg.exec()

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
        self._reload_list()
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
        self.car_selected.emit(self.current_id or None)  # combo re-reads restored names
        self.garage_restored.emit()  # force Gearing/Tuning to reseed from the new on-disk data

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
