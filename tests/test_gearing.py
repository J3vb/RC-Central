import math

import pytest

from app import gearing


def test_final_drive_ratio():
    assert gearing.final_drive_ratio(20, 80, 2.0) == 8.0
    assert gearing.final_drive_ratio(20, 80) == 4.0  # default internal_ratio=1.0


def test_rollout_mm():
    assert gearing.rollout_mm(64, 8.0) == pytest.approx(8 * math.pi)


def test_pack_voltage():
    assert gearing.pack_voltage(3) == pytest.approx(11.1)
    assert gearing.pack_voltage(2, per_cell=4.2) == pytest.approx(8.4)


def test_motor_rpm():
    assert gearing.motor_rpm(3000, 7.4) == pytest.approx(22200)


def test_top_speed_kmh():
    assert gearing.top_speed_kmh(2000, 8, 64, 8) == pytest.approx(24.13, abs=0.1)


def test_top_speed_mph_conversion():
    assert gearing.top_speed_mph(100) == pytest.approx(100 / 1.609344)


def test_zero_pinion_raises():
    with pytest.raises(ValueError):
        gearing.final_drive_ratio(0, 80, 2.0)


def test_zero_fdr_raises():
    with pytest.raises(ValueError):
        gearing.rollout_mm(64, 0)
    with pytest.raises(ValueError):
        gearing.top_speed_kmh(2000, 8, 64, 0)


def test_compute_returns_all_keys():
    r = gearing.compute(
        pinion=22, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    assert set(r) == {"fdr", "rollout_mm", "top_speed_kmh", "top_speed_mph", "motor_rpm"}
    assert r["fdr"] == pytest.approx(1.9 * 87 / 22)


def test_compute_propagates_value_error():
    with pytest.raises(ValueError):
        gearing.compute(
            pinion=0, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
        )


def test_pinion_sweep_span():
    rows = gearing.pinion_sweep(
        base_pinion=22, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    assert [r["pinion"] for r in rows] == [19, 20, 21, 22, 23, 24, 25]
    base = [r for r in rows if r["is_base"]]
    assert len(base) == 1 and base[0]["pinion"] == 22


def test_pinion_sweep_clamps_low_end():
    rows = gearing.pinion_sweep(
        base_pinion=2, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    assert [r["pinion"] for r in rows] == [1, 2, 3, 4, 5]
    assert all(r["pinion"] >= 1 for r in rows)


def test_pinion_sweep_base_row_matches_compute():
    rows = gearing.pinion_sweep(
        base_pinion=22, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    base = next(r for r in rows if r["is_base"])
    expected = gearing.compute(
        pinion=22, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    assert base["fdr"] == expected["fdr"]
    assert base["rollout_mm"] == expected["rollout_mm"]
    assert base["top_speed_kmh"] == expected["top_speed_kmh"]


def test_solve_pinion_round_trips_with_compute():
    # The rollout produced by pinion 24 must solve back to 24.
    r = gearing.compute(
        pinion=24, spur=87, internal_ratio=1.9, tire_diameter_mm=60, kv=3000, voltage=7.4
    )
    solved = gearing.solve_pinion_for_rollout(
        target_rollout_mm=r["rollout_mm"], spur=87, internal_ratio=1.9, tire_diameter_mm=60
    )
    assert solved == 24


def test_solve_pinion_clamps_to_one():
    assert (
        gearing.solve_pinion_for_rollout(
            target_rollout_mm=0.01, spur=87, internal_ratio=1.9, tire_diameter_mm=60
        )
        == 1
    )


def test_solve_pinion_rejects_nonpositive_target():
    with pytest.raises(ValueError):
        gearing.solve_pinion_for_rollout(
            target_rollout_mm=0, spur=87, internal_ratio=1.9, tire_diameter_mm=60
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"target_rollout_mm": 30, "spur": 87, "internal_ratio": 1.9, "tire_diameter_mm": 0},
        {"target_rollout_mm": 30, "spur": 0, "internal_ratio": 1.9, "tire_diameter_mm": 60},
        {"target_rollout_mm": 30, "spur": 87, "internal_ratio": 0, "tire_diameter_mm": 60},
    ],
)
def test_solve_pinion_rejects_nonpositive_inputs(bad):
    # the module's contract is ValueError on bad input, not ZeroDivisionError or a
    # silently-clamped bogus pinion (the sole UI caller catches only ValueError)
    with pytest.raises(ValueError):
        gearing.solve_pinion_for_rollout(**bad)
