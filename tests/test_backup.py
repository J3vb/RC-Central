import zipfile

import pytest

from app import backup, garage


@pytest.fixture
def backup_sandbox(tmp_path, monkeypatch):
    """Point garage at a tmp dir; backup reads garage.GARAGE_DIR, so one patch suffices."""
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    return tmp_path


def test_backup_restore_roundtrip(backup_sandbox, tmp_path):
    garage.save_car(garage.new_car("Alpha"))
    garage.save_car(garage.new_car("Beta"))
    zip_path = backup.make_backup(tmp_path / "b.zip")
    for f in garage.GARAGE_DIR.glob("*.json"):  # wipe, then restore
        f.unlink()
    assert backup.restore_backup(zip_path) == 2
    assert {c["name"] for c in garage.list_cars()} == {"Alpha", "Beta"}


def test_backup_empty_garage_is_valid_zip(backup_sandbox, tmp_path):
    assert zipfile.is_zipfile(backup.make_backup(tmp_path / "empty.zip"))


def test_restore_ignores_foreign_members(backup_sandbox, tmp_path):
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../escape.json", "{}")  # zip-slip attempt
        z.writestr("garage/nested/deep.json", "{}")  # nested, not a direct member
        z.writestr("garage/good.json", '{"id":"x","name":"Good"}')
    assert backup.restore_backup(bad) == 1  # only the legit top-level garage member
    assert not (tmp_path / "escape.json").exists()


def test_restore_keys_file_by_id_not_member_name(backup_sandbox, tmp_path):
    # A member whose filename != its embedded id must land at <id>.json, so the car
    # is loadable/deletable by id (no "ghost car" that lists but can't be opened).
    bad = tmp_path / "mismatch.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("garage/renamed.json", '{"id":"realid","name":"Good"}')
    assert backup.restore_backup(bad) == 1
    assert garage.load_car("realid") is not None
    assert (garage.GARAGE_DIR / "realid.json").exists()


def test_restore_skips_malformed_member_without_poisoning(backup_sandbox, tmp_path):
    # One non-JSON / id-less member must not be written (it would crash list_cars),
    # while the valid members around it still restore.
    bad = tmp_path / "mixed.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("garage/broken.json", "{not json")
        z.writestr("garage/noid.json", '{"name":"NoId"}')
        z.writestr("garage/good.json", '{"id":"ok","name":"Good"}')
    assert backup.restore_backup(bad) == 1  # only the well-formed car with an id
    garage.list_cars()  # must not raise
    assert {c["name"] for c in garage.list_cars()} == {"Good"}


def test_restore_rejects_id_that_would_escape_dir(backup_sandbox, tmp_path):
    # A crafted id must not write outside the garage dir even though it parses fine.
    bad = tmp_path / "escape.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("garage/x.json", '{"id":"../pwned","name":"Evil"}')
    assert backup.restore_backup(bad) == 0
    assert not (tmp_path / "pwned.json").exists()


def test_restore_skips_zip_bomb_member(backup_sandbox, tmp_path):
    # A member deflating past _MAX_MEMBER is skipped before decompression;
    # legit members beside it still restore.
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("garage/bomb.json", b"0" * (backup._MAX_MEMBER + 1))
        z.writestr("garage/good.json", '{"id":"ok","name":"Good"}')
    assert backup.restore_backup(bomb) == 1
    assert {c["name"] for c in garage.list_cars()} == {"Good"}


def test_restore_total_decompression_budget(backup_sandbox, tmp_path, monkeypatch):
    # Many small members can't exceed the whole-restore budget in aggregate.
    monkeypatch.setattr(backup, "_MAX_TOTAL", 50)
    bad = tmp_path / "many.zip"
    with zipfile.ZipFile(bad, "w") as z:
        for i in range(5):
            z.writestr(f"garage/c{i}.json", f'{{"id":"c{i}","name":"AAAA"}}')  # 25 bytes each
    assert backup.restore_backup(bad) == 2  # 25 + 25 == 50; the third would bust it


def test_restore_uppercase_extension_member(backup_sandbox, tmp_path):
    # make_backup's glob is case-insensitive on Windows; restore must match .JSON too.
    bad = tmp_path / "upper.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("garage/CAR.JSON", '{"id":"up","name":"Upper"}')
    assert backup.restore_backup(bad) == 1
    assert garage.load_car("up") is not None
