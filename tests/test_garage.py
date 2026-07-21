import json

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


def test_list_cars_skips_corrupt_and_non_object_files(garage_sandbox):
    # one truncated/corrupt/hand-mangled file must not take down listing for every car
    garage.save_car(garage.new_car("Good"))
    (garage.GARAGE_DIR / "broken.json").write_text("{not valid json", encoding="utf-8")
    (garage.GARAGE_DIR / "notacar.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert [c["name"] for c in garage.list_cars()] == ["Good"]


def test_save_car_is_atomic_and_leaves_no_temp(garage_sandbox):
    car = garage.save_car(garage.new_car("Atomic"))
    # write-then-rename: the real file exists and no ".tmp" is left behind (and a
    # ".json.tmp" would not be picked up by list_cars' *.json glob anyway)
    assert (garage.GARAGE_DIR / f"{car['id']}.json").exists()
    assert list(garage.GARAGE_DIR.glob("*.tmp")) == []


def test_delete_removes_car_and_is_idempotent(garage_sandbox):
    car = garage.save_car(garage.new_car("Doomed"))
    garage.delete_car(car["id"])
    assert garage.load_car(car["id"]) is None
    garage.delete_car(car["id"])  # no error the second time


def test_load_missing_returns_none(garage_sandbox):
    assert garage.load_car("does-not-exist") is None


def test_new_car_has_empty_log():
    assert garage.new_car()["log"] == []


def test_new_log_entry_fields():
    entry = garage.new_log_entry("Run", "practice session")
    assert entry["kind"] == "Run"
    assert entry["note"] == "practice session"
    assert entry["id"]
    assert entry["date"].endswith("+00:00") or entry["date"].endswith("Z")


def test_log_survives_save_roundtrip(garage_sandbox):
    car = garage.new_car("Logged")
    car["log"].append(garage.new_log_entry("Maintenance", "new tires"))
    garage.save_car(car)
    loaded = garage.load_car(car["id"])
    assert len(loaded["log"]) == 1
    assert loaded["log"][0]["note"] == "new tires"


def test_format_spec_sheet_includes_filled_fields_and_skips_empty():
    car = garage.new_car("RWD Street")
    car["chassis"] = "MST RMX"
    car["motor"] = ""  # empty -> omitted
    car["gearing"]["fdr"] = 7.5
    car["notes"] = "grippy asphalt"
    sheet = garage.format_spec_sheet(car)
    assert "RWD Street" in sheet
    assert "Chassis: MST RMX" in sheet
    assert "Motor:" not in sheet  # empty field skipped
    assert "Final drive ratio: 7.5" in sheet
    assert "grippy asphalt" in sheet


def test_format_spec_sheet_handles_unnamed_car():
    car = garage.new_car("")
    assert garage.format_spec_sheet(car).startswith("Unnamed car")


def test_clone_car_fresh_id_name_and_empty_log():
    original = garage.new_car("RWD Street")
    original["log"].append(garage.new_log_entry("Run", "practice"))
    clone = garage.clone_car(original)
    assert clone["id"] != original["id"]
    assert clone["name"].endswith("(copy)")
    assert clone["log"] == []


def test_clone_car_is_deep_copy():
    original = garage.new_car("Deep")
    clone = garage.clone_car(original)
    clone["gearing"]["pinion"] = 99
    assert original["gearing"]["pinion"] == 22  # original untouched


def test_load_car_file_assigns_fresh_id_and_preserves_fields(tmp_path):
    car = garage.new_car("Imported")
    path = tmp_path / "c.json"
    path.write_text(json.dumps(car), encoding="utf-8")
    loaded = garage.load_car_file(path)
    assert loaded["id"] != car["id"]
    assert loaded["name"] == "Imported"


def test_load_car_file_rejects_non_dict(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        garage.load_car_file(path)


def test_load_car_file_rejects_malformed_json(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        garage.load_car_file(path)


def test_diff_cars_flags_only_changed_fields():
    a = garage.new_car("Alpha")
    b = garage.new_car("Alpha")
    b["gearing"]["pinion"] = 30
    rows = garage.diff_cars(a, b)
    assert [label for label, _va, _vb, differs in rows if differs] == ["Pinion"]


def test_diff_cars_identical_all_equal():
    import copy

    a = garage.new_car("Same")
    assert all(not differs for *_rest, differs in garage.diff_cars(a, copy.deepcopy(a)))


def test_diff_cars_handles_missing_gearing():
    rows = garage.diff_cars({"name": "Sparse"}, garage.new_car("Full"))
    pinion = next(r for r in rows if r[0] == "Pinion")
    assert pinion[1] == "" and pinion[2] == "22"  # no KeyError; sparse side blank


def test_add_preset_snapshots_current_gearing():
    car = garage.new_car("Presetful")
    car["gearing"]["pinion"] = 30
    garage.add_preset(car, "carpet")
    car["gearing"]["pinion"] = 22  # mutate after: preset must be a deep copy
    assert garage.list_presets(car)[0]["gearing"]["pinion"] == 30


def test_apply_preset_restores_gearing():
    car = garage.new_car("Switcher")
    car["gearing"]["pinion"] = 30
    garage.add_preset(car, "high")
    car["gearing"]["pinion"] = 18
    garage.add_preset(car, "low")
    garage.apply_preset(car, "high")
    assert car["gearing"]["pinion"] == 30


def test_add_preset_same_name_replaces():
    car = garage.new_car("Dupe")
    garage.add_preset(car, "carpet")
    garage.add_preset(car, "carpet")
    assert len(garage.list_presets(car)) == 1


def test_delete_preset():
    car = garage.new_car("Trim")
    garage.add_preset(car, "carpet")
    garage.delete_preset(car, "carpet")
    assert garage.list_presets(car) == []


def test_list_presets_missing_key_returns_empty():
    assert garage.list_presets({}) == []  # a car saved before presets existed


def test_new_car_has_blank_setup_and_no_base():
    car = garage.new_car("Fresh")
    assert set(car["setup"]) == {key for key, _ in garage._SETUP_LABELS}
    assert all(v == "" for v in car["setup"].values())
    assert car["base_setup"] is None
    assert garage.setup_is_untouched(car)


def test_setup_is_untouched_tolerates_cars_predating_the_block():
    # a car saved before the setup block existed has no key at all
    assert garage.setup_is_untouched({"name": "Old"})
    touched = garage.new_car("T")
    touched["setup"]["camber_front"] = "-2"
    assert not garage.setup_is_untouched(touched)


def test_save_base_setup_snapshots_and_apply_restores():
    car = garage.new_car("Based")
    car["setup"]["ride_height_front"] = "5.0"
    garage.save_base_setup(car)
    car["setup"]["ride_height_front"] = "6.5"  # drift away from base…
    assert garage.apply_base_setup(car)
    assert car["setup"]["ride_height_front"] == "5.0"  # …and return


def test_save_base_setup_is_a_deep_copy():
    car = garage.new_car("Deep")
    car["setup"]["toe_rear"] = "2.0"
    garage.save_base_setup(car)
    car["setup"]["toe_rear"] = "3.0"  # mutating current must not follow into the base
    assert car["base_setup"]["toe_rear"] == "2.0"


def test_apply_base_setup_is_a_noop_without_a_saved_base():
    car = garage.new_car("Baseless")
    car["setup"]["camber_rear"] = "-1.5"
    assert not garage.apply_base_setup(car)
    assert car["setup"]["camber_rear"] == "-1.5"  # untouched by the no-op


def test_base_setup_survives_save_roundtrip(garage_sandbox):
    car = garage.new_car("Persistent")
    car["setup"]["spring_front"] = "yellow"
    garage.save_base_setup(car)
    garage.save_car(car)
    loaded = garage.load_car(car["id"])
    assert loaded["base_setup"]["spring_front"] == "yellow"


def test_format_spec_sheet_includes_setup_and_skips_blanks():
    car = garage.new_car("Sheeted")
    car["setup"]["camber_front"] = "-2°"
    sheet = garage.format_spec_sheet(car)
    assert "Chassis setup:" in sheet
    assert "Camber front (°): -2°" in sheet
    assert "Toe front" not in sheet  # blank fields are skipped

    blank = garage.new_car("Blank")
    assert "Chassis setup:" not in garage.format_spec_sheet(blank)


def test_diff_cars_includes_setup_fields():
    a = garage.new_car("A")
    b = garage.new_car("A")
    b["setup"]["rear_diff"] = "Spool"
    rows = garage.diff_cars(a, b)
    assert [label for label, _va, _vb, differs in rows if differs] == ["Rear diff"]


def test_diff_cars_handles_missing_setup_block():
    rows = garage.diff_cars({"name": "Old"}, garage.new_car("New"))
    diff = next(r for r in rows if r[0] == "Rear diff")
    assert diff[1] == "" and diff[2] == ""  # no KeyError; both render blank


def test_new_car_has_empty_setup_presets():
    assert garage.new_car("Fresh")["setup_presets"] == []


def test_add_setup_preset_snapshots_current_setup():
    car = garage.new_car("Presetful")
    car["setup"]["camber_front"] = "-2"
    garage.add_setup_preset(car, "carpet")
    car["setup"]["camber_front"] = "-3"  # mutate after: preset must be a deep copy
    assert garage.list_setup_presets(car)[0]["setup"]["camber_front"] == "-2"


def test_apply_setup_preset_restores_setup():
    car = garage.new_car("Switcher")
    car["setup"]["rear_diff"] = "Spool"
    garage.add_setup_preset(car, "carpet")
    car["setup"]["rear_diff"] = "Ball diff"
    garage.add_setup_preset(car, "asphalt")
    garage.apply_setup_preset(car, "carpet")
    assert car["setup"]["rear_diff"] == "Spool"


def test_add_setup_preset_same_name_replaces():
    car = garage.new_car("Dupe")
    garage.add_setup_preset(car, "carpet")
    garage.add_setup_preset(car, "carpet")
    assert len(garage.list_setup_presets(car)) == 1


def test_delete_setup_preset():
    car = garage.new_car("Trim")
    garage.add_setup_preset(car, "carpet")
    garage.delete_setup_preset(car, "carpet")
    assert garage.list_setup_presets(car) == []


def test_list_setup_presets_missing_key_returns_empty():
    assert garage.list_setup_presets({}) == []  # a car saved before they existed
