"""The Workshop tab: a shared active-car switcher over Garage | Gearing | Tuning."""

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import garage
from app.ui.common import _ACTIVE_CAR_KEY, _settings
from app.ui.garage_tab import GarageTab
from app.ui.gear import GearTab
from app.ui.tuning import TuningTab


class WorkshopTab(QWidget):
    """Garage, Gearing, and Tuning under one roof, scoped to one active car.

    The header combo and the Garage list sync two ways, and this class is the
    only writer of the active-car key. Gearing and Tuning refresh from the key
    on their own showEvent; the combo handler also pokes them directly, because
    the combo can change while one of them is the visible sub-tab.
    """

    def __init__(self):
        super().__init__()
        self.garage = GarageTab()
        self.gear = GearTab()
        self.tuning = TuningTab()

        self.car_combo = QComboBox()
        header = QHBoxLayout()
        header.addWidget(QLabel("Car:"))
        header.addWidget(self.car_combo, 1)

        self.subtabs = QTabWidget()
        self.subtabs.addTab(self.garage, "Garage")
        self.subtabs.addTab(self.gear, "Gearing")
        self.subtabs.addTab(self.tuning, "Tuning")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.subtabs)

        self._refresh_combo()
        # open the Garage form on the persisted active car so it matches the header
        active = self.car_combo.currentData()
        if active:
            self.garage.open_car(active)

        # connect only after the initial populate (same trap as _CompareDialog:
        # the setCurrentIndex during _refresh_combo would otherwise fire handlers)
        self.car_combo.currentIndexChanged.connect(self._on_combo_changed)
        self.garage.car_selected.connect(self._on_garage_selected)

    def _refresh_combo(self) -> None:
        """Rebuild the switcher from the garage, re-selecting the persisted car.

        A persisted id that no longer resolves lands on "— no car —", which is
        exactly the deleted-active-car fallback.
        """
        active = _settings().value(_ACTIVE_CAR_KEY, "") or None
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        self.car_combo.addItem("— no car —", None)
        for car in garage.list_cars():
            self.car_combo.addItem(car.get("name", "Unnamed"), car["id"])
        self.car_combo.setCurrentIndex(max(0, self.car_combo.findData(active)))
        self.car_combo.blockSignals(False)

    def _on_combo_changed(self) -> None:
        car_id = self.car_combo.currentData()
        _settings().setValue(_ACTIVE_CAR_KEY, car_id or "")
        self.garage.open_car(car_id)  # silent: never emits car_selected back
        # Gearing/Tuning normally resync on showEvent, but the combo can change
        # while one of them is the visible sub-tab — poke them directly.
        self.gear._load_active_car()
        self.tuning.mylog._reload()

    def _on_garage_selected(self, car_id) -> None:
        # Garage is the visible sub-tab when its signal fires; Gearing/Tuning
        # pick the new key up on their next showEvent.
        _settings().setValue(_ACTIVE_CAR_KEY, car_id or "")
        self._refresh_combo()  # re-reads names, so saves/renames relabel the combo
