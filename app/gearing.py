"""Gearing / rollout / top-speed math for RC drift setups. No Qt.

Functions raise ValueError on a non-positive divisor so callers (the UI) can
blank their outputs in a try/except instead of crashing on a transient bad
input. That also keeps the math trivially unit-testable.
"""

import math

NOMINAL_CELL_V = 3.7  # LiPo nominal per-cell voltage


def final_drive_ratio(pinion: int, spur: int, internal_ratio: float = 1.0) -> float:
    """FDR = internal_ratio * spur / pinion. Raises ValueError if pinion <= 0."""
    if pinion <= 0:
        raise ValueError("pinion must be > 0")
    return internal_ratio * spur / pinion


def rollout_mm(tire_diameter_mm: float, fdr: float) -> float:
    """Distance travelled per motor revolution = tire_circumference / FDR.

    = tire_diameter_mm * pi / fdr. Raises ValueError if fdr <= 0.
    """
    if fdr <= 0:
        raise ValueError("fdr must be > 0")
    return tire_diameter_mm * math.pi / fdr


def pack_voltage(cells: int, per_cell: float = NOMINAL_CELL_V) -> float:
    """Nominal pack voltage from a LiPo cell count."""
    return cells * per_cell


def motor_rpm(kv: float, voltage: float) -> float:
    """Unloaded motor RPM = Kv * voltage."""
    return kv * voltage


def top_speed_kmh(kv: float, voltage: float, tire_diameter_mm: float, fdr: float) -> float:
    """Theoretical top speed in km/h.

    wheel_rpm = motor_rpm / fdr; each wheel turn covers tire_diameter_mm * pi
    (mm). km/h = wheel_rpm * tire_circumference_mm * 60 min/h / 1e6 mm/km.
    Raises ValueError if fdr <= 0.
    """
    if fdr <= 0:
        raise ValueError("fdr must be > 0")
    wheel_rpm = motor_rpm(kv, voltage) / fdr
    return wheel_rpm * tire_diameter_mm * math.pi * 60 / 1_000_000


def top_speed_mph(kmh: float) -> float:
    """km/h -> mph."""
    return kmh / 1.609344


def compute(
    *,
    pinion: int,
    spur: int,
    internal_ratio: float,
    tire_diameter_mm: float,
    kv: float,
    voltage: float,
) -> dict:
    """Aggregate the whole gearing picture for the UI.

    Returns fdr, rollout_mm, top_speed_kmh, top_speed_mph, motor_rpm.
    Propagates ValueError from the primitives (e.g. pinion 0).
    """
    fdr = final_drive_ratio(pinion, spur, internal_ratio)
    kmh = top_speed_kmh(kv, voltage, tire_diameter_mm, fdr)
    return {
        "fdr": fdr,
        "rollout_mm": rollout_mm(tire_diameter_mm, fdr),
        "top_speed_kmh": kmh,
        "top_speed_mph": top_speed_mph(kmh),
        "motor_rpm": motor_rpm(kv, voltage),
    }


def pinion_sweep(
    *,
    base_pinion: int,
    spur: int,
    internal_ratio: float,
    tire_diameter_mm: float,
    kv: float,
    voltage: float,
    span: int = 3,
) -> list[dict]:
    """Compute the gearing picture for pinions around base_pinion.

    Swapping the pinion is the common gearing-drift adjustment: a tooth up
    trades top speed for punch, a tooth down the reverse. Sweeps
    base_pinion-span..base_pinion+span (low end clamped at 1), ascending, each
    row = {"pinion", "is_base", **compute(...)}.
    """
    rows = []
    for p in range(max(1, base_pinion - span), base_pinion + span + 1):
        result = compute(
            pinion=p,
            spur=spur,
            internal_ratio=internal_ratio,
            tire_diameter_mm=tire_diameter_mm,
            kv=kv,
            voltage=voltage,
        )
        rows.append({"pinion": p, "is_base": p == base_pinion, **result})
    return rows


def solve_pinion_for_rollout(
    *,
    target_rollout_mm: float,
    spur: int,
    internal_ratio: float,
    tire_diameter_mm: float,
) -> int:
    """Nearest whole-tooth pinion whose rollout is closest to target_rollout_mm.

    Inverts rollout = tire_diameter_mm * pi * pinion / (internal_ratio * spur).
    Rollout is monotonic in pinion, so rounding the exact solution gives the
    closest achievable pinion. Clamped to >= 1 (a pinion is at least 1 tooth).
    Raises ValueError on a non-positive target.
    """
    if target_rollout_mm <= 0:
        raise ValueError("target_rollout_mm must be > 0")
    pinion = round(target_rollout_mm * internal_ratio * spur / (tire_diameter_mm * math.pi))
    return max(1, pinion)
