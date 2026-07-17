"""The Gear tab: live gearing calculator plus the gear ratio chart dialog."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
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
from app.ui.common import _ACCENT, _settings, _show_status


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
