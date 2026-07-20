import pytest

from app import garage, gearing, parts


def test_suggestions_union_curated_and_garage_values():
    cars = [{"chassis": "Homebrew special"}, {"chassis": "Yokomo YD-2"}]
    got = parts.suggestions("chassis", cars)
    assert "Homebrew special" in got  # the user's own value is offered back
    assert "MST RMX 2.5" in got  # curated seed still present
    assert got.count("Yokomo YD-2") == 1  # a value in both lists appears once


def test_suggestions_ignores_blank_and_missing_values():
    cars = [{"motor": "   "}, {"motor": None}, {}, {"motor": "Acuvance Luxon"}]
    got = parts.suggestions("motor", cars)
    assert "" not in got
    assert all(v.strip() for v in got)
    assert got.count("Acuvance Luxon") == 1


def test_suggestions_sorted_case_insensitively():
    got = parts.suggestions("esc", [{"esc": "aaa lowercase"}, {"esc": "ZZZ upper"}])
    assert got == sorted(got, key=str.casefold)


def test_unknown_field_falls_back_to_garage_values_only():
    # so adding a spec field to garage.new_car() is useful without touching parts.py
    assert parts.suggestions("brakes", [{"brakes": "Custom"}]) == ["Custom"]
    assert parts.suggestions("brakes", []) == []


def test_curated_entries_are_unique_and_stripped():
    for field, values in parts.CURATED.items():
        assert len(set(values)) == len(values), f"{field} has duplicates"
        assert all(v == v.strip() and v for v in values), f"{field} has blank/padded entries"


# --- chassis-seeded gearing ------------------------------------------------------


def test_every_geared_chassis_is_a_real_chassis_name():
    # a typo here (or a mangled "Rêve D") would silently never match the combo value
    unknown = set(parts.CHASSIS_GEARING) - set(parts.CHASSIS)
    assert unknown == set()


def test_seeded_values_are_within_the_gear_tab_input_ranges():
    # QDoubleSpinBox/QSpinBox clamp silently, so a value outside the widget's range
    # would be stored as something the user never chose. This is what caught the
    # original 5.0 internal-ratio ceiling against MST's 8.182 FRX.
    for name, seed in parts.CHASSIS_GEARING.items():
        if "internal_ratio" in seed:
            assert 1.0 <= seed["internal_ratio"] <= 12.0, name
            # the spinbox shows 3dp and writes its own value back on save, so a seed
            # needing more precision would be silently rounded the first time a user
            # saves gearing on that car
            assert round(seed["internal_ratio"], 3) == seed["internal_ratio"], name
        if "spur" in seed:
            assert 1 <= seed["spur"] <= 200, name
        if "pinion" in seed:
            assert 1 <= seed["pinion"] <= 99, name


def test_seeded_gearing_reproduces_the_vendors_own_published_ratio():
    # Yokomo's YD-2 manual prints 2.6 x 84 / 20 = 10.92 in its own gear-ratio table.
    seed = parts.CHASSIS_GEARING["Yokomo YD-2"]
    fdr = gearing.final_drive_ratio(seed["pinion"], seed["spur"], seed["internal_ratio"])
    assert fdr == pytest.approx(10.92, abs=0.005)


def test_apply_chassis_defaults_seeds_an_untouched_car():
    car = garage.new_car()
    car["chassis"] = "Rêve D RDX"
    assert garage.apply_chassis_defaults(car) is True
    assert car["gearing"]["internal_ratio"] == 2.6
    # RDX has no verified kit spur/pinion, so those keep the new_car() defaults
    assert car["gearing"]["spur"] == garage.new_car()["gearing"]["spur"]


@pytest.mark.parametrize("field,value", [("pinion", 21), ("spur", 88), ("internal_ratio", 2.0)])
def test_apply_chassis_defaults_never_overwrites_a_touched_field(field, value):
    car = garage.new_car()
    car["chassis"] = "Yokomo YD-2"
    car["gearing"][field] = value
    assert garage.apply_chassis_defaults(car) is False
    assert car["gearing"][field] == value
    assert car["gearing"]["internal_ratio"] == (2.0 if field == "internal_ratio" else 1.9)


def test_apply_chassis_defaults_declines_once_the_calculator_has_been_used():
    # inputs still at defaults, but a saved fdr proves the user worked on this car
    car = garage.new_car()
    car["chassis"] = "Yokomo YD-2"
    car["gearing"]["fdr"] = 7.51
    assert garage.apply_chassis_defaults(car) is False
    assert car["gearing"]["internal_ratio"] == 1.9


@pytest.mark.parametrize("chassis", ["", "   ", "Some Unlisted Chassis", "Onisiki Kodama"])
def test_apply_chassis_defaults_is_a_noop_without_verified_data(chassis):
    # Onisiki Kodama is deliberately absent: its manual prints no tooth counts
    car = garage.new_car()
    car["chassis"] = chassis
    assert garage.apply_chassis_defaults(car) is False
    assert car["gearing"] == garage.new_car()["gearing"]


def test_gearing_is_untouched_tracks_new_car_rather_than_hardcoded_numbers():
    assert garage.gearing_is_untouched(garage.new_car()) is True
    car = garage.new_car()
    car["gearing"]["spur"] += 1
    assert garage.gearing_is_untouched(car) is False
