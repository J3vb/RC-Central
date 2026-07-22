"""Share-card payload codec: garage car <-> compact setup code.

Pure logic, no Qt. The same encoded string travels two ways: embedded in a
share-card PNG's text chunk (key ``PNG_TEXT_KEY``) and as a copy-paste
"setup code" (``RCSETUP1.`` + base64url(zlib(compact JSON))). Decoded input is
untrusted — everything funnels through the ``build_card`` whitelist, so an
import can never contain a field an export couldn't produce. The envelope
(``{"rccard": 1, "card": {...}}``) is deliberately the schema for a future
community setups catalog.
"""

from __future__ import annotations

import base64
import json
import zlib

from app import garage

PREFIX = "RCSETUP1."
PNG_TEXT_KEY = "rccard"
_VERSION = 1
_MAX_FIELD = 200
_MAX_NOTES = 2000
_MAX_DECODED = 16384  # decompressed payload cap (zlib-bomb guard)
_MAX_CODE = 65536

_TEXT_FIELDS = ("name", *(key for key, _ in garage._SPEC_LABELS))
# Inputs only — fdr/rollout_mm/top_speed_kmh are recomputed, never shared.
# Domains mirror the Gear tab widgets (gear.py setRange): anything outside is
# dropped, so an import can never carry a value the spinboxes would silently
# clamp or truncate on the next save.
_GEARING_DOMAINS: dict[str, tuple[float, float, bool]] = {
    "pinion": (1, 99, True),
    "spur": (1, 200, True),
    "internal_ratio": (1.0, 12.0, False),
    "tire_diameter_mm": (40.0, 120.0, False),
    "kv": (0, 20000, True),
    "cells": (1, 8, True),
}
_SETUP_KEYS = tuple(key for key, _ in garage._SETUP_LABELS)


def _text(value: object, cap: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()[:cap]


def build_card(car: dict) -> dict:
    """The shareable subset of a car dict, whitelisted and capped.

    Also the import-side cleaner: applied to every decoded payload, so junk
    keys are dropped, values are coerced to capped strings, and gearing values
    survive only as sane numbers.
    """
    card: dict = {key: _text(car.get(key), _MAX_FIELD) for key in _TEXT_FIELDS}
    card["notes"] = _text(car.get("notes"), _MAX_NOTES)
    gearing_in = car.get("gearing")
    gearing: dict = {}
    if isinstance(gearing_in, dict):
        for key, (lo, hi, is_int) in _GEARING_DOMAINS.items():
            value = gearing_in.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if is_int and not float(value).is_integer():
                continue
            if lo <= value <= hi:
                gearing[key] = int(value) if is_int else value
    card["gearing"] = gearing
    setup_in = car.get("setup")
    setup: dict = {}
    if isinstance(setup_in, dict):
        for key in _SETUP_KEYS:
            value = _text(setup_in.get(key), _MAX_FIELD)
            if value:  # empty fields don't travel — keeps codes short
                setup[key] = value
    card["setup"] = setup
    return card


def encode(card: dict) -> str:
    """Card dict -> setup code string."""
    # ensure_ascii=False: emoji/CJK notes stay <=4 bytes/char in UTF-8, so a
    # legal card always fits decode's _MAX_DECODED cap (\uXXXX escaping would
    # inflate 2000 emoji past it, making our own cards unimportable).
    payload = json.dumps(
        {"rccard": _VERSION, "card": card}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(zlib.compress(payload)).decode("ascii")


def decode(code: str) -> dict:
    """Setup code string -> cleaned card dict. Raises ValueError with a
    human-readable message on anything malformed; never raises anything else."""
    if not isinstance(code, str):
        raise ValueError("Setup code must be text.")
    code = "".join(code.split())  # chat apps wrap long codes across lines
    if not code.startswith(PREFIX):
        raise ValueError("Not an RC Central setup code (should start with RCSETUP1).")
    body = code[len(PREFIX) :]
    if not body:
        raise ValueError("Setup code is empty.")
    if len(body) > _MAX_CODE:
        raise ValueError("Setup code is too large.")
    body += "=" * (-len(body) % 4)  # re-pad: copy/paste often eats the '='
    try:
        data = base64.urlsafe_b64decode(body.encode("ascii"))
    except ValueError as e:  # binascii.Error and UnicodeEncodeError both land here
        raise ValueError("Setup code is garbled (not valid base64).") from e
    decomp = zlib.decompressobj()
    try:
        raw = decomp.decompress(data, _MAX_DECODED)
    except zlib.error as e:
        raise ValueError("Setup code is garbled (bad compression).") from e
    if decomp.unconsumed_tail or decomp.unused_data:
        raise ValueError("Setup code is too large.")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        # ValueError covers JSONDecodeError, UnicodeDecodeError and CPython's
        # int-digit-limit message; RecursionError comes from hostile deep
        # nesting ('['*4000) and must not escape the ValueError contract.
        raise ValueError("Setup code is garbled (not valid data).") from e
    if not isinstance(envelope, dict):
        raise ValueError("Setup code carries no setup data.")
    version = envelope.get("rccard")
    if version != _VERSION:
        if isinstance(version, int) and version > _VERSION:
            raise ValueError(
                "This setup code needs a newer version of RC Central."
            )
        raise ValueError("Not an RC Central setup code.")
    card = envelope.get("card")
    if not isinstance(card, dict):
        raise ValueError("Setup code carries no setup data.")
    return build_card(card)


def card_to_car(card: dict) -> dict:
    """Overlay a (decoded) card onto a fresh car: new id, full default shape."""
    card = build_card(card)
    car = garage.new_car(card["name"] or "Shared setup")
    for key in _TEXT_FIELDS[1:]:
        car[key] = card[key]
    car["notes"] = card["notes"]
    car["gearing"].update(card["gearing"])
    car["setup"].update(card["setup"])
    return car
