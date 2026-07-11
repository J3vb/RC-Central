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
