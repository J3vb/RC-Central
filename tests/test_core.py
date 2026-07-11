import json
import zipfile
from pathlib import Path

import jsonschema
import pytest

from app import installer

ROOT = Path(__file__).resolve().parents[1]


def _tool(**overrides):
    tool = {
        "id": "fake-tool",
        "name": "Fake Tool",
        "vendor": "Test",
        "version": "1.0",
        "download": {"url": "https://example.invalid/fake.zip", "archive": "zip", "sha256": None},
        "install": {"exe_relative_path": "FakeTool.exe", "portable": True},
    }
    tool.update(overrides)
    return tool


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect installs to tmp and replace the network download with a local fixture zip."""
    monkeypatch.setattr(installer, "TOOLS_DIR", tmp_path / "tools")
    fixture = tmp_path / "fake.zip"
    with zipfile.ZipFile(fixture, "w") as z:
        z.writestr("FakeTool.exe", b"MZ fake exe")
        z.writestr("readme.txt", "hi")
    monkeypatch.setattr(
        installer,
        "_download",
        lambda url, dest, progress: dest.write_bytes(fixture.read_bytes()),
    )
    return tmp_path


def test_catalog_entries_match_schema():
    schema = json.loads((ROOT / "catalog" / "schema.json").read_text(encoding="utf-8"))
    entries = sorted((ROOT / "catalog" / "tools").glob("*.json"))
    assert entries, "catalog must have at least one entry"
    for f in entries:
        tool = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(tool, schema)
        assert tool["id"] == f.stem, f"{f.name}: id must match filename"


def test_install_and_state(sandbox):
    exe = installer.install(_tool())
    assert exe.name == "FakeTool.exe" and exe.exists()
    state = installer.get_state("fake-tool")
    assert state["version"] == "1.0"
    assert state["exe_path"] == str(exe)


def test_register_existing(sandbox, tmp_path):
    exe = tmp_path / "AlreadyHere.exe"
    exe.write_bytes(b"MZ")
    returned = installer.register_existing(_tool(), str(exe), "9.9")
    assert returned == exe
    state = installer.get_state("fake-tool")
    assert state["version"] == "9.9"
    assert state["exe_path"] == str(exe)
    assert state["source"] == "existing"


def test_register_existing_missing_file(sandbox, tmp_path):
    with pytest.raises(installer.ExeNotFound):
        installer.register_existing(_tool(), str(tmp_path / "nope.exe"), "1.0")


def test_install_scans_when_relative_path_missing(sandbox):
    tool = _tool()
    tool["install"]["exe_relative_path"] = "RenamedInNewVersion.exe"
    assert installer.install(tool).name == "FakeTool.exe"


def test_hash_mismatch_raises(sandbox):
    tool = _tool()
    tool["download"]["sha256"] = "0" * 64
    with pytest.raises(installer.VendorFileChanged):
        installer.install(tool)
    assert installer.get_state("fake-tool") is None


def test_install_runs_setup_when_configured(sandbox, monkeypatch):
    dest = installer.TOOLS_DIR / "fake-tool"
    ran = {}

    def fake_run(cmd, check, timeout):
        ran["cmd"] = cmd
        (dest / "Installed.exe").write_bytes(b"MZ")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    tool = _tool()
    tool["install"] = {
        "setup_args": ["/VERYSILENT", "/DIR={dest}"],
        "exe_relative_path": "Installed.exe",
    }
    exe = installer.install(tool)
    assert exe.name == "Installed.exe"
    assert ran["cmd"][0].endswith("FakeTool.exe")  # the extracted setup was run
    assert ran["cmd"][-1] == f"/DIR={dest}"  # {dest} placeholder substituted


def test_launch_needs_admin_uses_shellexecute(monkeypatch):
    from app import launcher

    called = {}
    monkeypatch.setattr(
        launcher.os, "startfile", lambda p, cwd=None: called.update(p=p, cwd=cwd)
    )
    launcher.launch("fake-tool", "C:/fake/Tool.exe", needs_admin=True)
    assert called["p"].endswith("Tool.exe")
    assert called["cwd"].endswith("fake")


def test_updater_version_compare():
    from app import updater

    assert updater._newer("v0.2.0", "0.1.0")
    assert updater._newer("0.1.10", "0.1.9")
    assert not updater._newer("v0.1.0", "0.1.0")
    assert not updater._newer("garbage", "0.1.0")
    assert not updater.fetch_update()  # not frozen -> no-op


def test_download_rejects_html_page(tmp_path, monkeypatch):
    class FakeResp:
        headers = {"content-type": "text/html; charset=UTF-8"}

        def raise_for_status(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(installer.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(installer.VendorFileChanged):
        installer._download("https://example.invalid/x.zip", tmp_path / "x.zip", None)


def test_find_exe_skips_uninstallers(tmp_path):
    (tmp_path / "Tool.exe").write_bytes(b"MZ")
    (tmp_path / "unins000.exe").write_bytes(b"MZ")
    assert installer._find_exe(tmp_path, None).name == "Tool.exe"


def test_find_exe_ambiguous_raises(tmp_path):
    (tmp_path / "A.exe").write_bytes(b"MZ")
    (tmp_path / "B.exe").write_bytes(b"MZ")
    with pytest.raises(installer.ExeNotFound):
        installer._find_exe(tmp_path, None)


def test_ui_smoke(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    from PySide6.QtWidgets import QToolButton

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()
    assert win.table.rowCount() == 1
    button = win.table.cellWidget(0, 4)
    assert isinstance(button, QToolButton)
    assert button.text() == "Install"
    assert [a.text() for a in button.menu().actions()] == ["Locate existing install…"]


def test_tabs_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()

    assert win.tabs.count() == 3
    assert [win.tabs.tabText(i) for i in range(3)] == [
        "Tools",
        "Gear Calculator",
        "Garage",
    ]
    assert win.tools_tab.table.rowCount() == 1  # existing table still wired
    assert win.table is win.tools_tab.table  # back-compat alias holds

    win.gear_tab._recompute()
    assert win.gear_tab.fdr_out.text() not in ("", "—")
    assert win.garage_tab.list.count() == 0  # empty garage dir

    # the garage -> calculator link switches tabs without error
    car = garage.new_car("Linked")
    car["gearing"]["pinion"] = 30
    win._open_in_calc(car)
    assert win.tabs.currentWidget() is win.gear_tab
    assert win.gear_tab.pinion.value() == 30


def test_garage_tab_save_and_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()

    tab = win.garage_tab
    tab.name.setText("Test Rig")
    tab.pinion.setValue(25)
    tab._on_save()

    assert tab.list.count() == 1
    cars = garage.list_cars()
    assert len(cars) == 1 and cars[0]["name"] == "Test Rig"
    assert cars[0]["gearing"]["pinion"] == 25
