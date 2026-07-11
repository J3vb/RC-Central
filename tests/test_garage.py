import pytest

from app import garage


@pytest.fixture
def garage_sandbox(tmp_path, monkeypatch):
    """Redirect garage storage to a tmp dir (mirrors the installer sandbox fixture)."""
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    return tmp_path


def test_new_car_has_id_and_gearing():
    car = garage.new_car("RWD Street")
    assert car["name"] == "RWD Street"
    assert car["id"]
    assert car["gearing"]["pinion"] == 22


def test_save_assigns_id_and_timestamp(garage_sandbox):
    car = {"name": "No Id Yet"}
    saved = garage.save_car(car)
    assert saved["id"]
    assert saved["updated_at"].endswith("+00:00") or saved["updated_at"].endswith("Z")


def test_save_and_load_roundtrip(garage_sandbox):
    car = garage.new_car("Loaded")
    car["gearing"]["pinion"] = 24
    car["gearing"]["fdr"] = 7.51
    car["notes"] = "grippy asphalt"
    garage.save_car(car)

    loaded = garage.load_car(car["id"])
    assert loaded["name"] == "Loaded"
    assert loaded["gearing"]["pinion"] == 24
    assert loaded["gearing"]["fdr"] == 7.51
    assert loaded["notes"] == "grippy asphalt"


def test_list_sorted_by_name(garage_sandbox):
    garage.save_car(garage.new_car("Zeta"))
    garage.save_car(garage.new_car("alpha"))
    names = [c["name"] for c in garage.list_cars()]
    assert names == ["alpha", "Zeta"]  # case-insensitive sort


def test_list_empty_when_no_dir(garage_sandbox):
    assert garage.list_cars() == []


def test_delete_removes_car_and_is_idempotent(garage_sandbox):
    car = garage.save_car(garage.new_car("Doomed"))
    garage.delete_car(car["id"])
    assert garage.load_car(car["id"]) is None
    garage.delete_car(car["id"])  # no error the second time


def test_load_missing_returns_none(garage_sandbox):
    assert garage.load_car("does-not-exist") is None
