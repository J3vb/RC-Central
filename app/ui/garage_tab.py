"""The Garage tab: create/edit/delete RC car spec sheets, and the compare dialog."""

import json
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImageReader
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import backup, garage, parts, sharecard
from app.ui.common import _accent, _GAP, _MARGIN, _on_accent, _section_label, _show_status
from app.ui.setup_diagram import SetupDiagramPanel
from app.ui.share_card import ShareCardDialog


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
                    item.setBackground(QColor(_accent()))
                    item.setForeground(QColor(_on_accent()))  # readable on any accent
                self.table.setItem(r, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)


def _car_from_png(path: str) -> dict:
    """A fresh car from a setup-card PNG's embedded payload."""
    # QImageReader reads the text chunk without decoding pixels (a dropped 4K
    # screenshot stays instant) and even recovers it from a truncated card.
    reader = QImageReader(path)
    payload = reader.text(sharecard.PNG_TEXT_KEY)
    if not payload:
        if not reader.canRead():
            raise ValueError("Could not read this file as an image.")
        raise ValueError(
            "No RC Central setup data found in this image — it may have been "
            "re-saved by an app that strips it. Ask for the setup code instead."
        )
    return sharecard.card_to_car(sharecard.decode(payload))


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
        # Accept dropped setup cards / car JSONs anywhere on the tab.
        # ponytail: children (QLineEdit etc.) still natively handle drops that
        # land exactly on them (they insert the path as text); a tab-wide event
        # filter is the upgrade path if that ever confuses anyone.
        self.setAcceptDrops(True)

        self._cars: list[dict] = []  # cache behind the list, for live filtering
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search cars…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        self.empty_hint = QLabel("No cars yet — click New to add your first car.")
        self.empty_hint.setObjectName("mutedLabel")  # secondary text (see theme._QSS)
        new_btn = QPushButton("&New")
        new_btn.clicked.connect(self._on_new)
        # Plain siblings, not a QToolButton split button: QToolButton loses
        # click-to-focus and picks up the table-row QSS padding.
        self.import_btn = QPushButton("&Import…")
        self.import_btn.clicked.connect(self._on_import)
        self.paste_btn = QPushButton("Paste code…")
        self.paste_btn.clicked.connect(self._on_paste_code)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._on_duplicate)
        del_btn = QPushButton("&Delete")
        del_btn.clicked.connect(self._on_delete)
        self.compare_btn = QPushButton("Compare…")
        self.compare_btn.clicked.connect(self._on_compare)
        # Two grouped rows instead of a five-button wall the narrow column clips:
        # create-ish actions on one, destructive + read-only on the other.
        left_buttons = QVBoxLayout()
        left_buttons.setSpacing(4)
        create_row = QHBoxLayout()
        create_row.setSpacing(4)
        create_row.addWidget(new_btn)
        create_row.addWidget(self.import_btn)
        create_row.addWidget(self.paste_btn)
        create_row.addWidget(dup_btn)
        act_row = QHBoxLayout()
        act_row.setSpacing(4)
        act_row.addWidget(del_btn)
        act_row.addWidget(self.compare_btn)
        left_buttons.addLayout(create_row)
        left_buttons.addLayout(act_row)

        # Whole-garage actions (all cars), distinct from the per-car form buttons.
        backup_btn = QPushButton("Back up all…")
        backup_btn.clicked.connect(self._on_backup)
        restore_btn = QPushButton("Restore all…")
        restore_btn.clicked.connect(self._on_restore)
        backup_row = QHBoxLayout()
        backup_row.setSpacing(_GAP)
        backup_row.addWidget(backup_btn)
        backup_row.addWidget(restore_btn)

        left = QVBoxLayout()
        left.setSpacing(_GAP)
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
        # ~5 lines: notes are an aside — the freed vertical space goes to the run/
        # maintenance log table below, which is what actually grows over time.
        self.notes.setMaximumHeight(110)

        # Chassis setup is edited on the diagram panel (third column, below): value
        # boxes around a car schematic. Aliases keep _fill_form/_form_to_car, the
        # base-setup handlers and the tests addressing the setup UI exactly as when
        # these were plain form rows.
        self.setup_panel = SetupDiagramPanel()
        self.setup_panel.save_base_btn.clicked.connect(self._on_save_base)
        self.setup_panel.apply_base_btn.clicked.connect(self._on_apply_base)
        self.setup_panel.factory_btn.clicked.connect(self._on_factory_setup)
        self.setup_panel.reset_btn.clicked.connect(self._on_reset_setup)
        self.setup_panel.preset_combo.activated.connect(self._on_apply_setup_preset)
        self.setup_panel.save_preset_btn.clicked.connect(self._on_save_setup_preset)
        self.setup_panel.del_preset_btn.clicked.connect(self._on_delete_setup_preset)
        self._setup_fields = self.setup_panel.fields
        self.save_base_btn = self.setup_panel.save_base_btn
        self.apply_base_btn = self.setup_panel.apply_base_btn
        self.setup_preset_combo = self.setup_panel.preset_combo
        # editable combo: currentTextChanged fires for typing AND programmatic sets
        self.chassis.currentTextChanged.connect(self._update_factory_enabled)

        # Gearing (pinion/spur/…) is edited on the Gearing sub-tab, not here; the
        # form only overlays spec fields, so a car's gearing block rides through Save.
        form = QFormLayout()
        form.setHorizontalSpacing(_MARGIN)
        form.setVerticalSpacing(6)
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
        self.share_btn = QPushButton("Share card…")
        self.share_btn.clicked.connect(self._on_share)
        form_buttons = QHBoxLayout()
        form_buttons.setSpacing(_GAP)
        form_buttons.addWidget(save_btn)
        form_buttons.addWidget(export_btn)
        form_buttons.addWidget(copy_btn)
        form_buttons.addWidget(self.share_btn)

        # Run / maintenance log for the selected car.
        self._current_log: list[dict] = []
        self.log_table = QTableWidget(0, 3)
        self.log_table.setHorizontalHeaderLabels(("Date", "Type", "Note"))
        self.log_table.verticalHeader().hide()
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setAlternatingRowColors(True)
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
        log_add_row.setSpacing(_GAP)
        log_add_row.addWidget(self.log_kind)
        log_add_row.addWidget(self.log_note, 1)
        log_add_row.addWidget(add_log_btn)
        log_add_row.addWidget(remove_log_btn)

        right = QVBoxLayout()
        right.setSpacing(_GAP)
        right.addLayout(form)
        right.addLayout(form_buttons)
        right.addWidget(_section_label("Run / maintenance log"))
        right.addWidget(self.log_table)
        right.addLayout(log_add_row)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(_MARGIN)  # column gutters breathe a little wider than _GAP
        # 4:7:5, not 1:2:1 — the extra goes to the setup panel so its four-button
        # row ("Save as base" … "Reset") fits at the default window width
        layout.addLayout(left, 4)
        layout.addLayout(right, 7)
        layout.addWidget(self.setup_panel, 5)

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
        self.setup_panel.set_base(car.get("base_setup"))  # refresh the drift marks
        self.setup_panel.set_presets(garage.list_setup_presets(car))
        self._update_factory_enabled()
        self.notes.setPlainText(car.get("notes", ""))
        self._current_log = list(car.get("log", []))
        self._fill_log_table()
        self.setup_panel.show_value_starts()  # long values show their head, not tail

    def _fill_log_table(self) -> None:
        self.log_table.setRowCount(len(self._current_log))
        for row, entry in enumerate(self._current_log):
            # YYYY-MM-DD HH:MM from the ISO stamp — the time matters now that
            # change-history entries can land several times a day
            date = str(entry.get("date", ""))[:16].replace("T", " ")
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
        # The log is union-merged rather than overlaid: entries other tabs wrote to
        # disk while this form held the car (a Gearing save's history, a Tuning
        # note) must survive a Garage Save. Disk order first, then any form-only
        # additions not saved yet. Deletions don't need reconciling here —
        # _on_remove_log writes them through to disk immediately.
        disk_log = car.get("log", [])
        known = {e.get("id") for e in disk_log}
        merged = disk_log + [e for e in self._current_log if e.get("id") not in known]
        car.update(
            {
                "name": self.name.text().strip() or "Unnamed",
                **{f: c.currentText().strip() for f, c in self._part_fields.items()},
                "setup": {k: e.text().strip() for k, e in self._setup_fields.items()},
                "notes": self.notes.toPlainText(),
                "log": merged,
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

    def _save_car(self, car: dict) -> dict | None:
        """garage.save_car, but a transient OSError becomes a warning instead of a
        crashed slot — callers abort their follow-up UI updates when this returns
        None. Mirrors the OSError handling in _on_backup/_on_restore/_on_export."""
        try:
            return garage.save_car(car)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return None

    def _on_save(self) -> None:
        car = self._form_to_car()
        seeded = garage.apply_chassis_defaults(car)
        setup_seeded = garage.apply_chassis_setup(car)
        saved = self._save_car(car)
        if saved is None:
            return
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        # save_car may have appended change-history entries; show them and keep
        # _current_log a faithful copy (not an alias) of what's on disk
        self._current_log = list(saved.get("log", []))
        self._fill_log_table()
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
        saved = self._save_car(garage.save_base_setup(self._form_to_car()))
        if saved is None:
            return
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        self.apply_base_btn.setEnabled(True)
        # this save path skips _fill_form, so refresh the drift marks and the
        # log view (save_car may have appended change-history entries) here
        self.setup_panel.set_base(saved.get("base_setup"))
        self._current_log = list(saved.get("log", []))
        self._fill_log_table()
        _show_status(self, f"Saved base setup for {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_apply_base(self) -> None:
        car = self._form_to_car()
        if not garage.apply_base_setup(car):  # no base saved yet
            return
        saved = self._save_car(car)
        if saved is None:
            return
        self.current_id = saved["id"]
        self._fill_form(saved)
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Applied base setup to {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_save_setup_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save setup preset", "Preset name:")
        if not ok or not name.strip():
            return
        saved = self._save_car(garage.add_setup_preset(self._form_to_car(), name.strip()))
        if saved is None:
            return
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        self._fill_form(saved)  # repopulates the preset combo
        _show_status(self, f"Saved setup preset {name.strip()}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_apply_setup_preset(self) -> None:
        name = self.setup_preset_combo.currentData()
        if not name:  # the "— preset —" placeholder
            return
        saved = self._save_car(garage.apply_setup_preset(self._form_to_car(), name))
        if saved is None:
            return
        self.current_id = saved["id"]
        self._fill_form(saved)
        # keep the applied preset selected so "Del" can target it — _fill_form's
        # set_presets() otherwise snaps the combo back to the "— preset —" placeholder,
        # leaving _on_delete_setup_preset reading None (a permanent no-op)
        self.setup_preset_combo.setCurrentIndex(self.setup_preset_combo.findData(name))
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Applied setup preset {name}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_delete_setup_preset(self) -> None:
        name = self.setup_preset_combo.currentData()
        if not name:
            return
        saved = self._save_car(garage.delete_setup_preset(self._form_to_car(), name))
        if saved is None:
            return
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        self._fill_form(saved)
        _show_status(self, f"Deleted setup preset {name}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_reset_setup(self) -> None:
        """Reset every setup field to its default: the chassis' verified factory
        value where one exists, blank otherwise. Form-only, like the Factory fill:
        nothing persists until the next Save (which then logs the changes)."""
        chassis = self.chassis.currentText().strip()
        defaults = parts.CHASSIS_SETUP.get(chassis) or {}
        targets = {
            key: str(defaults.get(key, "")).strip() for key in self._setup_fields
        }
        if all(e.text().strip() == targets[k] for k, e in self._setup_fields.items()):
            return  # already at the default state, don't bother asking
        prompt = (
            f"Reset all setup values to the {chassis} factory settings?"
            if defaults
            else "Clear all setup values back to blank?"
        )
        if QMessageBox.question(
            self, "Reset setup", prompt
        ) != QMessageBox.StandardButton.Yes:
            return
        for key, edit in self._setup_fields.items():
            edit.setText(targets[key])
        self.setup_panel.show_value_starts()  # long values show their head, not tail
        _show_status(
            self,
            f"Reset to {chassis} factory setup — Save to keep it"
            if defaults
            else "Setup cleared — Save to keep it",
            6000,
        )

    def _update_factory_enabled(self) -> None:
        chassis = self.chassis.currentText().strip()
        self.setup_panel.factory_btn.setEnabled(chassis in parts.CHASSIS_SETUP)

    def _on_factory_setup(self) -> None:
        """Fill the setup fields from the chassis' factory sheet (form-only: the
        values land in the boxes and are persisted by the next Save, like typing)."""
        defaults = parts.CHASSIS_SETUP.get(self.chassis.currentText().strip())
        if not defaults:
            return
        if any(e.text().strip() for e in self._setup_fields.values()):
            if QMessageBox.question(
                self, "Factory setup", "Replace the current setup values with the factory sheet?"
            ) != QMessageBox.StandardButton.Yes:
                return
        for key, value in defaults.items():  # a partial sheet leaves other fields alone
            if key in self._setup_fields:
                self._setup_fields[key].setText(str(value))
        self.setup_panel.show_value_starts()  # long values show their head, not tail
        _show_status(self, "Factory setup filled in — Save to keep it", 6000)

    def _on_duplicate(self) -> None:
        if not self.current_id:  # nothing open to clone
            return
        dup = self._save_car(garage.clone_car(self._form_to_car()))
        if dup is None:
            return
        self.current_id = dup["id"]
        self._fill_form(dup)
        self._reload_list()
        self._select_id(dup["id"])
        _show_status(self, f"Duplicated to {dup['name']}", 5000)
        self.car_selected.emit(dup["id"])

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import car", "", "Car spec or setup card (*.json *.png)"
        )
        if path:
            self._import_path(path)

    def _import_path(self, path: str) -> None:
        """File import — a raw car JSON or a setup-card PNG."""
        try:
            if path.lower().endswith(".png"):
                car = _car_from_png(path)
            else:
                car = garage.load_car_file(path)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            # unreadable / invalid JSON / not an object / no embedded payload
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self._import_car(car)

    def _import_car(self, car: dict) -> None:
        """Persist an imported car; shared by file import, drag-drop and paste."""
        try:
            self._fill_form(car)  # render first: surfaces bad field types before we save
            saved = garage.save_car(car)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            # junk field types, or the garage dir refused the write — either
            # way, undo the half-filled form so the next Save can't clobber
            # the previously open car with the rejected import's fields.
            QMessageBox.warning(self, "Import failed", str(e))
            current = garage.load_car(self.current_id) if self.current_id else None
            self._fill_form(current or garage.new_car(""))
            return
        self.current_id = saved["id"]
        self._reload_list()
        self._select_id(saved["id"])
        _show_status(self, f"Imported {saved['name']}", 5000)
        self.car_selected.emit(saved["id"])

    def _on_paste_code(self) -> None:
        code, ok = QInputDialog.getMultiLineText(
            self, "Paste setup code", "Setup code (starts with RCSETUP1):"
        )
        if not ok or not code.strip():
            return
        try:
            car = sharecard.card_to_car(sharecard.decode(code))
        except ValueError as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self._import_car(car)

    @staticmethod
    def _droppable_paths(mime) -> list[str]:
        return [
            url.toLocalFile()
            for url in mime.urls()
            if url.isLocalFile()
            and url.toLocalFile().lower().endswith((".json", ".png"))
        ]

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._droppable_paths(event.mimeData()):
            # Never accept a Move: Explorer would delete the source file after
            # a Shift-drag. We only ever read what's dropped.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        paths = self._droppable_paths(event.mimeData())
        if not paths:
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

        def import_all() -> None:
            for path in paths:
                self._import_path(path)

        # After the OS drop handshake returns: a modal error box in here would
        # freeze the drag source (Explorer sits inside DoDragDrop until then).
        QTimer.singleShot(0, import_all)

    def _on_share(self) -> None:
        # render_card()->sharecard.encode() raises ValueError when a setup is too large
        # to fit in a shareable code; surface it as a warning instead of crashing the slot.
        try:
            dialog = ShareCardDialog(self._form_to_car(), self)
        except ValueError as e:
            QMessageBox.warning(self, "Can't share setup", str(e))
            return
        dialog.exec()

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
        entry_id = self._current_log[row].get("id")
        car = self.current_id and garage.load_car(self.current_id)
        if car:
            # Write-through delete by id against fresh disk state (mirrors the
            # Tuning log): entries other tabs added meanwhile survive, and the
            # deleted one is gone from disk so _form_to_car can't merge it back.
            car["log"] = [e for e in car.get("log", []) if e.get("id") != entry_id]
            saved = self._save_car(car)
            if saved is None:
                return
            self._current_log = list(saved.get("log", []))
        else:  # unsaved new car: the log only exists in the form
            del self._current_log[row]
        self._fill_log_table()
