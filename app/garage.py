"""Create/read/update/delete RC car spec sheets as per-car JSON files. No Qt.

Mirrors app/installer.py: a per-user DATA_DIR (see app/paths.py), one JSON file
per record, module-level path constants that tests monkeypatch.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.paths import data_dir

DATA_DIR = data_dir()
GARAGE_DIR = DATA_DIR / "garage"


def new_car(name: str = "New Car") -> dict:
    """A blank in-memory spec sheet with a fresh id. Not persisted until save_car()."""
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "chassis": "",
        "motor": "",
        "esc": "",
        "servo": "",
        "tires": "",
        "gearing": {
            "pinion": 22,
            "spur": 87,
            "internal_ratio": 1.9,
            "tire_diameter_mm": 60.0,
            "kv": 3000,
            "cells": 2,
            "fdr": None,  # filled when saved from the Gear Calculator
            "rollout_mm": None,
            "top_speed_kmh": None,
        },
        "log": [],  # run/maintenance history; see new_log_entry()
        "notes": "",
    }


def new_log_entry(kind: str, note: str) -> dict:
    """A single run/maintenance entry, timestamped now. Not persisted on its own."""
    return {
        "id": uuid.uuid4().hex,
        "date": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "note": note,
    }


# Labels for the gearing fields, in display order, for the exported spec sheet.
_GEARING_LABELS = (
    ("pinion", "Pinion"),
    ("spur", "Spur"),
    ("internal_ratio", "Internal ratio"),
    ("tire_diameter_mm", "Tire diameter (mm)"),
    ("fdr", "Final drive ratio"),
    ("rollout_mm", "Rollout (mm)"),
    ("top_speed_kmh", "Top speed (km/h)"),
)

_SPEC_LABELS = (
    ("chassis", "Chassis"),
    ("motor", "Motor"),
    ("esc", "ESC"),
    ("servo", "Servo"),
    ("tires", "Tires"),
)


def format_spec_sheet(car: dict) -> str:
    """Render a car as a shareable plain-text spec sheet. Skips empty fields."""
    lines = [car.get("name", "").strip() or "Unnamed car", "=" * 32]
    for key, label in _SPEC_LABELS:
        value = str(car.get(key, "")).strip()
        if value:
            lines.append(f"{label}: {value}")

    gearing = car.get("gearing", {})
    geared = [
        f"  {label}: {gearing[key]}"
        for key, label in _GEARING_LABELS
        if gearing.get(key) is not None
    ]
    if geared:
        lines.append("")
        lines.append("Gearing:")
        lines.extend(geared)

    notes = str(car.get("notes", "")).strip()
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.append(notes)

    return "\n".join(lines) + "\n"


def save_car(car: dict) -> dict:
    """Persist a spec sheet. Assigns an id if missing, stamps updated_at, returns the car."""
    car.setdefault("id", uuid.uuid4().hex)
    car["updated_at"] = datetime.now(timezone.utc).isoformat()
    GARAGE_DIR.mkdir(parents=True, exist_ok=True)
    _car_file(car["id"]).write_text(json.dumps(car, indent=2), encoding="utf-8")
    return car


def load_car(car_id: str) -> dict | None:
    """Read a spec sheet, or None if there's no file for that id."""
    f = _car_file(car_id)
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def list_cars() -> list[dict]:
    """Every saved spec sheet, sorted by name (case-insensitive)."""
    if not GARAGE_DIR.exists():
        return []
    cars = [json.loads(f.read_text(encoding="utf-8")) for f in GARAGE_DIR.glob("*.json")]
    return sorted(cars, key=lambda c: c.get("name", "").lower())


def delete_car(car_id: str) -> None:
    """Remove a spec sheet. No-op if it's already gone."""
    _car_file(car_id).unlink(missing_ok=True)


def _car_file(car_id: str) -> Path:
    return GARAGE_DIR / f"{car_id}.json"
