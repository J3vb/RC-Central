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
        "notes": "",
    }


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
