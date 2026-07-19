"""The Gear tab: live gearing calculator, inline gear ratio chart, pinion sweep dialog."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import garage, gearing
from app.ui.common import _ACCENT, _ACTIVE_CAR_KEY, _settings, _show_status


class GearTab(QWidget):
    """Live gearing calculator: FDR, rollout, theoretical top speed."""

    def __init__(self):
        super().__init__()

        # The Workshop's active car: gearing is seeded from it and saved back to it.
        self._active_id: str | None = None
        self._seeded_id: str | None = None  # last car seeded into the spinboxes

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

        # Reverse-solve: enter a target rollout or FDR, get the nearest whole-tooth
        # pinion. FDR is how setups are usually shared, rollout how tire wear is chased.
        self.target_rollout = QDoubleSpinBox()
        self.target_rollout.setRange(1.0, 999.0)
        self.target_rollout.setSingleStep(0.5)
        self.target_rollout.setValue(30.0)
        self.target_rollout.setAccessibleName("Target rollout (mm)")
        rollout_row = self._solve_row(
            self.target_rollout, "Solve pinion for target rollout", self._solve_pinion
        )

        self.target_fdr = QDoubleSpinBox()
        self.target_fdr.setRange(0.1, 99.0)
        self.target_fdr.setSingleStep(0.1)
        self.target_fdr.setValue(7.5)
        self.target_fdr.setAccessibleName("Target FDR")
        fdr_row = self._solve_row(
            self.target_fdr, "Solve pinion for target FDR", self._solve_pinion_fdr
        )

        form = QFormLayout()
        form.addRow("Pinion (teeth)", self.pinion)
        form.addRow("Spur (teeth)", self.spur)
        form.addRow("Internal ratio", self.internal_ratio)
        form.addRow("Tire diameter (mm)", self.tire)
        form.addRow("Motor Kv", self.kv)
        form.addRow("Battery cells (S)", self.cells)
        form.addRow("Target rollout (mm)", rollout_row)
        form.addRow("Target FDR", fdr_row)
        form.addRow(QLabel("<b>Results</b>"))
        form.addRow("Final drive ratio", self.fdr_out)
        form.addRow("Rollout (mm)", self.rollout_out)
        form.addRow("Top speed (km/h)", self.kmh_out)
        form.addRow("Top speed (mph)", self.mph_out)

        # Named gearing presets for the active car (moved here from the Garage form
        # with the gearing dedupe). activated fires only on user picks, never on
        # programmatic repopulation in _refresh_presets.
        self.preset_combo = QComboBox()
        self.preset_combo.activated.connect(self._on_apply_preset)
        self.save_preset_btn = QPushButton("Save as preset…")
        self.save_preset_btn.clicked.connect(self._on_save_preset)
        self.del_preset_btn = QPushButton("Delete preset")
        self.del_preset_btn.clicked.connect(self._on_delete_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_preset_btn)
        preset_row.addWidget(self.del_preset_btn)

        self.save_btn = QPushButton("Save results to car")
        self.save_btn.clicked.connect(self._save_to_car)
        self.save_btn.setEnabled(False)
        sweep_btn = QPushButton("Pinion sweep…")
        sweep_btn.clicked.connect(self._open_sweep)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.save_btn, 1)
        btn_row.addWidget(sweep_btn)

        # Inline gear ratio chart; live-tracks the inputs via set_setup in _recompute.
        self.chart = _GearChartPanel(
            self.pinion.value(), self.spur.value(), self.internal_ratio.value()
        )

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(preset_row)
        layout.addLayout(btn_row)
        layout.addWidget(self.chart, 1)

        self._load_active_car()
        self._recompute()

    def _load_active_car(self, force: bool = False) -> None:
        """Sync to the Workshop's active car: seed inputs and enable the save/preset row.

        Re-seeds the spinboxes only when the active car actually changed — showEvent
        calls this on every switch back to this sub-tab, and re-seeding on the same
        car would clobber the user's in-progress what-if tweaks. ``force`` overrides
        that guard for when the active car's data changed on disk under the same id
        (a garage "Restore all"), which the id check alone can't detect.
        """
        car_id = _settings().value(_ACTIVE_CAR_KEY, "") or None
        car = garage.load_car(car_id) if car_id else None
        self._active_id = car["id"] if car else None
        self.save_btn.setEnabled(self._active_id is not None)
        if car and (force or self._active_id != self._seeded_id):
            self.load_from_car(car)
        self._seeded_id = self._active_id
        self._refresh_presets(car)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # the active car may have changed on the Garage sub-tab since we last looked
        self._load_active_car()
        super().showEvent(event)

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
        self.chart.set_setup(
            self.pinion.value(), self.spur.value(), self.internal_ratio.value()
        )
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

    def _solve_row(self, spinbox, button_name: str, on_click):
        """A [target spinbox | 'Solve → pinion' button] row for form.addRow.

        Both solve buttons share the visible label 'Solve → pinion', so each needs a
        distinct accessible name or a screen reader announces the two identically.
        """
        button = QPushButton("Solve → pinion")
        button.setAccessibleName(button_name)
        button.clicked.connect(on_click)
        row = QHBoxLayout()
        row.addWidget(spinbox, 1)
        row.addWidget(button)
        return row

    def _apply_solved_pinion(self, solve_fn, **kwargs) -> None:
        """Run a reverse-solver and mirror its whole-tooth pinion into the spinbox.

        setValue fires the existing valueChanged -> _recompute, so results and the
        sweep table refresh for free, and _recompute shows the honest achieved value
        (integer teeth rarely hit the target exactly). Bad inputs (ValueError) leave
        the current pinion untouched.
        """
        try:
            p = solve_fn(**kwargs)
        except ValueError:
            return
        self.pinion.setValue(p)

    def _solve_pinion(self) -> None:
        """Fill the pinion with the rollout-closest whole tooth for the target rollout."""
        self._apply_solved_pinion(
            gearing.solve_pinion_for_rollout,
            target_rollout_mm=self.target_rollout.value(),
            spur=self.spur.value(),
            internal_ratio=self.internal_ratio.value(),
            tire_diameter_mm=self.tire.value(),
        )

    def _solve_pinion_fdr(self) -> None:
        """Fill the pinion with the FDR-closest whole tooth for the target FDR."""
        self._apply_solved_pinion(
            gearing.solve_pinion_for_fdr,
            target_fdr=self.target_fdr.value(),
            spur=self.spur.value(),
            internal_ratio=self.internal_ratio.value(),
        )

    def _open_sweep(self) -> None:
        # snapshot of the current setup; the modal sweep doesn't live-track the tab
        try:
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
            return
        dlg = _SweepDialog(rows, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # else it lingers per open
        dlg.exec()

    def _save_to_car(self) -> None:
        if self._active_id is None:
            return
        car = garage.load_car(self._active_id)
        if car is None:
            QMessageBox.warning(self, "Save failed", "That car no longer exists.")
            self._load_active_car()
            return
        try:
            r = self._current()
        except ValueError:
            return
        g = car.setdefault("gearing", {})
        g.update(
            {
                **self._spinbox_gearing(),
                "fdr": round(r["fdr"], 3),
                "rollout_mm": round(r["rollout_mm"], 2),
                "top_speed_kmh": round(r["top_speed_kmh"], 1),
            }
        )
        garage.save_car(car)
        _show_status(self, f"Saved gearing to {car['name']}", 5000)

    def _refresh_presets(self, car: dict | None) -> None:
        """Repopulate the preset dropdown from the car; disable the row without one."""
        has_car = car is not None
        for w in (self.preset_combo, self.save_preset_btn, self.del_preset_btn):
            w.setEnabled(has_car)
        self.preset_combo.clear()
        self.preset_combo.addItem("— preset —", None)
        for p in garage.list_presets(car or {}):
            self.preset_combo.addItem(p.get("name", "preset"), p.get("name"))

    def _spinbox_gearing(self) -> dict:
        return {
            "pinion": self.pinion.value(),
            "spur": self.spur.value(),
            "internal_ratio": self.internal_ratio.value(),
            "tire_diameter_mm": self.tire.value(),
            "kv": self.kv.value(),
            "cells": self.cells.value(),
        }

    def _active_car(self) -> dict | None:
        """The active car, fresh from disk; resync (and warn never) when it's gone."""
        car = garage.load_car(self._active_id) if self._active_id else None
        if car is None:
            self._load_active_car()  # deleted meanwhile: fall back to no-car state
        return car

    def _on_save_preset(self) -> None:
        car = self._active_car()
        if car is None:
            return
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        # update (not replace) the gearing block so computed fdr/rollout/top-speed
        # saved earlier ride into the snapshot instead of being dropped
        car.setdefault("gearing", {}).update(self._spinbox_gearing())
        car = garage.save_car(garage.add_preset(car, name.strip()))
        self._refresh_presets(car)

    def _on_apply_preset(self) -> None:
        name = self.preset_combo.currentData()  # None for the "— preset —" placeholder
        if not name:
            return
        car = self._active_car()
        if car is None:
            return
        car = garage.save_car(garage.apply_preset(car, name))
        self.load_from_car(car)  # reflect the applied gearing in the inputs

    def _on_delete_preset(self) -> None:
        name = self.preset_combo.currentData()
        if not name:
            return
        car = self._active_car()
        if car is None:
            return
        car = garage.save_car(garage.delete_preset(car, name))
        self._refresh_presets(car)
        _show_status(self, f"Deleted preset '{name}'", 5000)


class _SweepDialog(QDialog):
    """What-if sweep: the current pinion ±3, so tuners see the effect of a
    pinion swap (the common drift adjustment) at a glance. The base row is bold."""

    def __init__(self, rows: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pinion sweep")
        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(("Pinion", "FDR", "Rollout (mm)", "km/h"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
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
                self.table.setItem(row, col, item)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        self.resize(420, 300)


class _GearChartPanel(QWidget):
    """Spur × pinion FDR matrix, like the printed gear chart that ships with a kit.

    Lives inline in the Gear tab and tracks its inputs via set_setup. Ranges are
    editable and persist via QSettings on every change.
    """

    def __init__(self, pinion: int, spur: int, internal_ratio: float, parent=None):
        super().__init__(parent)
        self._pinion = pinion
        self._spur = spur
        self._ratio = internal_ratio

        self.pinion_min, self.pinion_max = QSpinBox(), QSpinBox()
        self.spur_min, self.spur_max = QSpinBox(), QSpinBox()
        # the "Pinion"/"Spur" QLabels aren't buddies of these four boxes, so give
        # screen readers a name (same gap as the tab's composite target rows)
        self.pinion_min.setAccessibleName("Chart pinion min")
        self.pinion_max.setAccessibleName("Chart pinion max")
        self.spur_min.setAccessibleName("Chart spur min")
        self.spur_max.setAccessibleName("Chart spur max")
        # (box, settings key, first-run default centered on the current setup);
        # _rebuild persists through this same tuple so keys can't drift apart
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
        controls.addWidget(QLabel("<b>Gear ratio chart</b>"))
        controls.addSpacing(16)
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

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setDefaultSectionSize(52)

        # connect only now that self.table exists (same trap as _CompareDialog:
        # the setValue calls above would otherwise rebuild into a missing table)
        for box, _key, _default in self._persist:
            box.valueChanged.connect(self._on_range_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.table)
        self._rebuild()

    def set_setup(self, pinion: int, spur: int, internal_ratio: float) -> None:
        """Re-highlight (and re-price) the matrix for the tab's current inputs."""
        if (pinion, spur, internal_ratio) == (self._pinion, self._spur, self._ratio):
            return  # tire/kv/cells changed too, but the chart depends only on these three
        self._pinion, self._spur, self._ratio = pinion, spur, internal_ratio
        self._rebuild()

    def _on_range_changed(self) -> None:
        """A chart range spinbox changed: persist the four ranges, then rebuild. Keeps the
        QSettings writes off the _rebuild path, which set_setup fires on every tab input."""
        settings = _settings()
        for box, key, _default in self._persist:
            settings.setValue(key, box.value())
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
