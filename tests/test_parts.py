from app import parts


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
