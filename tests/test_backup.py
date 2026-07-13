import zipfile

import pytest

from app import backup, garage


@pytest.fixture
def backup_sandbox(tmp_path, monkeypatch):
    """Point both backup and garage at the same tmp garage dir so a roundtrip works."""
    gdir = tmp_path / "garage"
    monkeypatch.setattr(backup, "GARAGE_DIR", gdir)
    monkeypatch.setattr(garage, "GARAGE_DIR", gdir)
    return tmp_path


def test_backup_restore_roundtrip(backup_sandbox, tmp_path):
    garage.save_car(garage.new_car("Alpha"))
    garage.save_car(garage.new_car("Beta"))
    zip_path = backup.make_backup(tmp_path / "b.zip")
    for f in backup.GARAGE_DIR.glob("*.json"):  # wipe, then restore
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
