"""Create/read/update/delete RC car spec sheets as per-car JSON files. No Qt.

Mirrors app/installer.py: a per-user DATA_DIR (see app/paths.py), one JSON file
per record, module-level path constants that tests monkeypatch.
"""

import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import parts
from app.paths import data_dir

DATA_DIR = data_dir()
GARAGE_DIR = DATA_DIR / "garage"


# The chassis-setup fields, in display order, shared by the Garage form, the
# exported spec sheet and the compare view. String-valued on purpose: vendors mix
# units and notations ("5.5", "-2°", "#300", "yellow spring"), and a wrong forced
# unit is worse than free text. Front/rear are separate fields so they can seed
# and diff independently even though the form pairs them on one row.
_SETUP_LABELS = (
    ("ride_height_front", "Ride height front (mm)"),
    ("ride_height_rear", "Ride height rear (mm)"),
    ("camber_front", "Camber front (°)"),
    ("camber_rear", "Camber rear (°)"),
    ("toe_front", "Toe front (°)"),
    ("toe_rear", "Toe rear (°)"),
    ("caster", "Caster (°)"),
    ("spring_front", "Spring front"),
    ("spring_rear", "Spring rear"),
    ("shock_oil_front", "Shock oil front"),
    ("shock_oil_rear", "Shock oil rear"),
    ("rear_diff", "Rear diff"),
)


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
        "setup": {key: "" for key, _ in _SETUP_LABELS},
        "base_setup": None,  # snapshot to return to; see save_base_setup()
        "log": [],  # run/maintenance history; see new_log_entry()
        "presets": [],  # named gearing snapshots; see add_preset()
        "setup_presets": [],  # named chassis-setup snapshots; see add_setup_preset()
        "notes": "",
    }


# the gearing fields a chassis may seed; also the ones checked for "has the user
# touched this?", so we only ever decline when we would actually overwrite something
_SEEDABLE = ("internal_ratio", "spur", "pinion")


def gearing_is_untouched(car: dict) -> bool:
    """Whether a car's gearing is still exactly as new_car() left it.

    Compared against a fresh new_car() rather than hardcoded numbers so this can't drift
    if the defaults change. A non-None ``fdr`` means the Gear Calculator has been used
    and saved on this car, which counts as touched even if the inputs happen to match.
    """
    gearing = car.get("gearing") or {}
    if not gearing:
        return True  # no gearing block yet (old/imported car): seeding can't overwrite anything
    if gearing.get("fdr") is not None:
        return False
    defaults = new_car()["gearing"]
    return all(gearing.get(key) == defaults[key] for key in _SEEDABLE)


def apply_chassis_defaults(car: dict) -> bool:
    """Seed gearing from the car's chassis, but only if the user never touched it.

    Returns whether anything was written, so the UI can tell the Gearing tab to re-read
    (its usual same-id guard would otherwise ignore a change under an unchanged car id).

    Never overwrites: a car whose gearing differs from the defaults in any seedable
    field is left completely alone. A chassis we have no verified data for is a no-op
    too - see parts.CHASSIS_GEARING for why an entry may be deliberately absent.
    """
    defaults = parts.CHASSIS_GEARING.get((car.get("chassis") or "").strip())
    if not defaults or not gearing_is_untouched(car):
        return False
    car.setdefault("gearing", {}).update(defaults)
    return True


def setup_is_untouched(car: dict) -> bool:
    """Whether every chassis-setup field is still blank.

    A car saved before the setup block existed has no "setup" key, which counts as
    untouched — seeding it can't overwrite anything the user entered.
    """
    setup = car.get("setup") or {}
    return not any(str(setup.get(key) or "").strip() for key, _ in _SETUP_LABELS)


def apply_chassis_setup(car: dict) -> bool:
    """Seed the setup block from the chassis' factory sheet, only if untouched.

    Returns whether anything was written. Same contract as apply_chassis_defaults:
    never overwrites a single user-entered field, and a chassis without verified
    vendor data is a no-op — see parts.CHASSIS_SETUP for why entries may be absent.
    """
    defaults = parts.CHASSIS_SETUP.get((car.get("chassis") or "").strip())
    if not defaults or not setup_is_untouched(car):
        return False
    setup = car.setdefault("setup", {key: "" for key, _ in _SETUP_LABELS})
    setup.update(defaults)
    return True


def save_base_setup(car: dict) -> dict:
    """Snapshot the car's current setup as its base; apply_base_setup returns to it."""
    car["base_setup"] = copy.deepcopy(car.get("setup") or {})
    return car


def apply_base_setup(car: dict) -> bool:
    """Copy the saved base setup back onto car['setup']. No-op without a saved base."""
    base = car.get("base_setup")
    if base is None:
        return False
    car.setdefault("setup", {}).update(copy.deepcopy(base))
    return True


def list_presets(car: dict) -> list[dict]:
    """Named gearing snapshots on a car (empty for cars saved before presets existed, or
    for a corrupt/hand-edited file whose 'presets' isn't a list)."""
    presets = car.get("presets", [])
    return presets if isinstance(presets, list) else []


def add_preset(car: dict, name: str) -> dict:
    """Snapshot the car's current gearing under name, replacing any preset with that name."""
    presets = car.setdefault("presets", [])
    presets[:] = [p for p in presets if p.get("name") != name]
    presets.append({"name": name, "gearing": copy.deepcopy(car.get("gearing", {}))})
    return car


def apply_preset(car: dict, name: str) -> dict:
    """Copy a named preset's gearing onto car['gearing']. No-op if name is unknown."""
    for p in car.get("presets", []):
        if p.get("name") == name:
            g = p.get("gearing")
            if isinstance(g, dict):  # a preset element may lack/mistype its gearing block
                car.setdefault("gearing", {}).update(copy.deepcopy(g))
            break
    return car


def delete_preset(car: dict, name: str) -> dict:
    """Remove the named preset. No-op if absent."""
    presets = car.get("presets")
    if presets:
        presets[:] = [p for p in presets if p.get("name") != name]
    return car


# Setup presets mirror the gearing presets above 1:1 ("carpet" vs "asphalt" full
# setups); the base setup stays its own separate snapshot on top of these.


def list_setup_presets(car: dict) -> list[dict]:
    """Named setup snapshots on a car (empty for cars saved before they existed, or for a
    corrupt/hand-edited file whose 'setup_presets' isn't a list)."""
    presets = car.get("setup_presets", [])
    return presets if isinstance(presets, list) else []


def add_setup_preset(car: dict, name: str) -> dict:
    """Snapshot the car's current setup under name, replacing any preset with that name."""
    presets = car.setdefault("setup_presets", [])
    presets[:] = [p for p in presets if p.get("name") != name]
    presets.append({"name": name, "setup": copy.deepcopy(car.get("setup", {}))})
    return car


def apply_setup_preset(car: dict, name: str) -> dict:
    """Copy a named preset's setup onto car['setup']. No-op if name is unknown."""
    for p in car.get("setup_presets", []):
        if p.get("name") == name:
            s = p.get("setup")
            if isinstance(s, dict):  # a preset element may lack/mistype its setup block
                car.setdefault("setup", {}).update(copy.deepcopy(s))
            break
    return car


def delete_setup_preset(car: dict, name: str) -> dict:
    """Remove the named setup preset. No-op if absent."""
    presets = car.get("setup_presets")
    if presets:
        presets[:] = [p for p in presets if p.get("name") != name]
    return car


def clone_car(car: dict) -> dict:
    """A deep copy as a fresh, unsaved spec sheet: new id, "(copy)" name, empty log."""
    dup = copy.deepcopy(car)
    dup["id"] = uuid.uuid4().hex
    dup["name"] = (car.get("name") or "Car") + " (copy)"
    dup["log"] = []  # a duplicate starts with an empty run/maintenance log
    dup.pop("updated_at", None)  # save_car re-stamps it
    return dup


def _sanitize_car(car: dict) -> dict:
    """Coerce a car's structured blocks to their expected container types so every
    downstream consumer (forms, gearing snapshots, add/apply_preset) can trust them. A
    hand-edited or hostile file with a non-dict gearing/setup, a non-list presets, or a
    non-dict preset element would otherwise pass through and crash on first use."""
    for key in ("gearing", "setup"):
        if key in car and not isinstance(car[key], dict):
            del car[key]
    for key in ("presets", "setup_presets"):
        if key not in car:
            continue
        val = car[key]
        if isinstance(val, list):
            car[key] = [p for p in val if isinstance(p, dict)]  # drop malformed elements
        else:
            del car[key]  # a non-list crashes the preset dropdown/add_preset later
    return car


def load_car_file(path) -> dict:
    """Import a spec sheet from an external JSON file, with a fresh id so it never clobbers an existing car."""
    car = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(car, dict):
        raise ValueError("not a car spec sheet: expected a JSON object")
    car["id"] = uuid.uuid4().hex
    if not isinstance(car.get("name"), str) or not car["name"].strip():
        car["name"] = "Imported car"  # hand-made files may lack one; save/status need it
    return _sanitize_car(car)


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

# The gearing fields the USER sets (change-history tracks these; the computed
# fdr/rollout/top-speed trio would only echo them as noise). kv/cells labels
# match the Gear tab's form.
_GEARING_INPUT_LABELS = (
    ("pinion", "Pinion"),
    ("spur", "Spur"),
    ("internal_ratio", "Internal ratio"),
    ("tire_diameter_mm", "Tire diameter (mm)"),
    ("kv", "Motor Kv"),
    ("cells", "Battery cells (S)"),
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

    setup = car.get("setup", {})
    set_rows = [
        f"  {label}: {str(setup[key]).strip()}"
        for key, label in _SETUP_LABELS
        if str(setup.get(key) or "").strip()
    ]
    if set_rows:
        lines.append("")
        lines.append("Chassis setup:")
        lines.extend(set_rows)

    notes = str(car.get("notes", "")).strip()
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.append(notes)

    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    """Render a field value for display; None (unset gearing) shows as blank."""
    return "" if value is None else str(value)


def _values_equal(x, y) -> bool:
    """Whether two field values count as the same for compare highlighting.

    Numerically-equal numbers are equal regardless of type/precision, so a car
    with tire_diameter_mm 60 (int, from hand-edited JSON) doesn't highlight as
    differing from one with 60.0 (float, from the form spinboxes).
    """
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return x == y
    return _fmt(x) == _fmt(y)


def _tracked_changes(old: dict, new: dict) -> list[dict]:
    """One dated log entry per tracked field whose persisted value changed.

    Tracked: spec fields, gearing inputs, chassis setup — in that deterministic
    order. Comparison goes through _values_equal so 60 vs 60.0 (and a missing key
    vs "") never logs; either side blank renders as an em dash in the note.
    """
    groups = (
        ("Spec", _SPEC_LABELS, lambda car: car),
        ("Gearing", _GEARING_INPUT_LABELS, lambda car: car.get("gearing") or {}),
        ("Setup", _SETUP_LABELS, lambda car: car.get("setup") or {}),
    )
    entries = []
    for kind, labels, block in groups:
        old_block, new_block = block(old), block(new)
        for key, label in labels:
            old_v, new_v = old_block.get(key), new_block.get(key)
            if not _values_equal(old_v, new_v):
                note = (
                    f"{label}: {_fmt(old_v).strip() or '—'}"
                    f" → {_fmt(new_v).strip() or '—'}"
                )
                entries.append(new_log_entry(kind, note))
    return entries


def diff_cars(a: dict, b: dict) -> list[tuple[str, str, str, bool]]:
    """Per-field (label, value_a, value_b, differs) for a side-by-side compare view.

    Same field order as format_spec_sheet: name, spec fields, gearing, then setup. Values
    render via _fmt; `differs` compares the raw values (see _values_equal) so
    numerically-equal numbers of different types aren't flagged.
    """
    rows = [("Name", a.get("name"), b.get("name"))]
    for key, label in _SPEC_LABELS:
        rows.append((label, a.get(key), b.get(key)))
    ga, gb = a.get("gearing", {}), b.get("gearing", {})
    for key, label in _GEARING_LABELS:
        rows.append((label, ga.get(key), gb.get(key)))
    sa, sb = a.get("setup", {}), b.get("setup", {})
    for key, label in _SETUP_LABELS:
        rows.append((label, sa.get(key), sb.get(key)))
    return [(label, _fmt(va), _fmt(vb), not _values_equal(va, vb)) for label, va, vb in rows]


def save_car(car: dict) -> dict:
    """Persist a spec sheet. Assigns an id if missing, stamps updated_at, returns the car.

    Change history: every save diffs the tracked fields against the car's file on
    disk and appends one dated log entry per change (see _tracked_changes). A first
    save, an import/duplicate (fresh id) or a restore (bypasses save_car) logs
    nothing.
    """
    car.setdefault("id", uuid.uuid4().hex)
    try:
        old = load_car(car["id"])
    except (ValueError, OSError):
        # a corrupt existing file must not fail the save that would replace it
        old = None
    if old is not None:
        car.setdefault("log", []).extend(_tracked_changes(old, car))
    car["updated_at"] = datetime.now(timezone.utc).isoformat()
    GARAGE_DIR.mkdir(parents=True, exist_ok=True)
    # Write to a temp file then atomically rename, so a crash mid-write can't leave a
    # truncated/0-byte spec sheet on disk (which would also break list_cars for every
    # other car). glob("*.json") ignores the ".json.tmp" temp.
    target = _car_file(car["id"])
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(car, indent=2), encoding="utf-8")
    tmp.replace(target)
    return car


def load_car(car_id: str) -> dict | None:
    """Read a spec sheet, or None if there's no file for that id (or it's corrupt).

    A corrupt/truncated/unreadable file returns None rather than raising, mirroring
    list_cars: one bad car file must not crash whatever tab opens it. Every caller
    already handles the None (missing-file) case.
    """
    f = _car_file(car_id)
    if not f.exists():
        return None
    try:
        car = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    # A hand-edited on-disk file is an untrusted boundary too: sanitize it exactly like an
    # imported one so add_preset/apply_preset/the forms can't crash on a bad-typed block.
    return _sanitize_car(car) if isinstance(car, dict) else None


def list_cars() -> list[dict]:
    """Every saved spec sheet, sorted by name (case-insensitive).

    A corrupt, truncated, or non-object JSON file is skipped rather than fatal: one
    bad file (an interrupted save, a hand-edit gone wrong) must not take down listing
    for every other car.
    """
    if not GARAGE_DIR.exists():
        return []
    cars = []
    for f in GARAGE_DIR.glob("*.json"):
        try:
            car = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(car, dict):
            cars.append(car)
    return sorted(cars, key=lambda c: str(c.get("name") or "").lower())


def delete_car(car_id: str) -> None:
    """Remove a spec sheet. No-op if it's already gone."""
    _car_file(car_id).unlink(missing_ok=True)


def _car_file(car_id: str) -> Path:
    return GARAGE_DIR / f"{car_id}.json"
