"""The Workshop tab: a shared active-car switcher over Garage | Gearing | Tuning."""

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import garage
from app.ui.common import _ACTIVE_CAR_KEY, _GAP, _MARGIN, _settings
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
        header.setSpacing(_GAP)
        header.addWidget(QLabel("Car:"))
        header.addWidget(self.car_combo, 1)

        self.subtabs = QTabWidget()
        self.subtabs.addTab(self.garage, "Garage")
        self.subtabs.addTab(self.gear, "Gearing")
        self.subtabs.addTab(self.tuning, "Tuning")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(_GAP)
        layout.addLayout(header)
        layout.addWidget(self.subtabs)

        self._refresh_combo()
        # open the Garage form on the persisted active car so it matches the header
        active = self.car_combo.currentData()
        if active:
            self.garage.open_car(active)

        # Ctrl+S saves the car from any Workshop sub-tab, not just with the Garage
        # form in front of you — the Gearing tab has its own "Save results to car",
        # but the muscle memory is one shortcut for "keep what I just changed".
        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self._on_save_shortcut)

        # connect only after the initial populate (same trap as _CompareDialog:
        # the setCurrentIndex during _refresh_combo would otherwise fire handlers)
        self.car_combo.currentIndexChanged.connect(self._on_combo_changed)
        self.garage.car_selected.connect(self._on_garage_selected)
        self.garage.garage_restored.connect(self._on_garage_restored)
        self.garage.gearing_seeded.connect(self._on_gearing_seeded)

    @staticmethod
    def _active_id() -> str | None:
        """The persisted active car id, or None. The settings key is the single
        source of truth for which car the whole Workshop is scoped to."""
        return _settings().value(_ACTIVE_CAR_KEY, "") or None

    def _refresh_combo(self) -> None:
        """Rebuild the switcher from the garage, re-selecting the persisted car.

        A persisted id that no longer resolves lands on "— no car —", which is
        exactly the deleted-active-car fallback.
        """
        active = self._active_id()
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        self.car_combo.addItem("— no car —", None)
        for car in garage.list_cars():
            self.car_combo.addItem(car.get("name", "Unnamed"), car["id"])
        self.car_combo.setCurrentIndex(max(0, self.car_combo.findData(active)))
        self.car_combo.blockSignals(False)

    def _on_combo_changed(self) -> None:
        car_id = self.car_combo.currentData()
        # open_car declines when the Garage form holds unsaved edits and the user
        # cancels; the combo has already moved by then, so put it back. blockSignals
        # keeps that correction from re-entering this handler.
        if not self.garage.open_car(car_id):
            self.car_combo.blockSignals(True)
            self.car_combo.setCurrentIndex(
                max(0, self.car_combo.findData(self._active_id()))
            )
            self.car_combo.blockSignals(False)
            return
        _settings().setValue(_ACTIVE_CAR_KEY, car_id or "")
        # Gearing/Tuning normally resync on showEvent, but the combo can change
        # while one of them is the visible sub-tab — poke them directly.
        self.gear._load_active_car()
        self.tuning.mylog._reload()

    def _on_save_shortcut(self) -> None:
        """Ctrl+S: save whatever the visible sub-tab owns for the active car.

        Gearing saves its computed block (its own button is disabled without a
        car); Garage and Tuning both round-trip through the Garage form, which is
        what holds the specs, setup and notes.

        Does nothing when there's nothing to save — otherwise Ctrl+S from the
        Tuning tab with no car open would quietly create an empty "Unnamed" one.
        """
        if self.subtabs.currentWidget() is self.gear:
            if self.gear.save_btn.isEnabled():
                self.gear._save_to_car()
            return
        if self.garage.current_id or self.garage.is_dirty():
            self.garage._on_save()

    def _on_garage_selected(self, car_id) -> None:
        # Garage is the visible sub-tab when its signal fires; Gearing/Tuning
        # pick the new key up on their next showEvent.
        _settings().setValue(_ACTIVE_CAR_KEY, car_id or "")
        self._refresh_combo()  # re-reads names, so saves/renames relabel the combo

    def _on_gearing_seeded(self) -> None:
        # A chassis pick wrote gearing onto the active car; its id didn't change, so
        # force Gearing past the same-id guard or it would keep showing the old values.
        self.gear._load_active_car(force=True)

    def _on_garage_restored(self) -> None:
        # A restore overwrote car files on disk; the active id is unchanged but its
        # data may differ, so force Gearing/Tuning past their same-id guards.
        self.gear._load_active_car(force=True)
        self.tuning.mylog._reload()
