import json
import sys
import zipfile
from pathlib import Path

import jsonschema
import pytest

from app import installer

ROOT = Path(__file__).resolve().parents[1]

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Tools tab and its launcher are Windows-only"
)


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


@WINDOWS_ONLY
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


@WINDOWS_ONLY
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

    # The Tools tab is Windows-only; the rest of the tabs are cross-platform.
    tools = ["Tools"] if sys.platform == "win32" else []
    expected = tools + ["Gear Calculator", "Garage", "Log"]
    assert win.tabs.count() == len(expected)
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == expected
    if sys.platform == "win32":
        assert win.tools_tab.table.rowCount() == 1  # existing table still wired
        assert win.table is win.tools_tab.table  # back-compat alias holds
    else:
        assert win.tools_tab is None

    win.gear_tab._recompute()
    assert win.gear_tab.fdr_out.text() not in ("", "—")
    assert win.garage_tab.list.count() == 0  # empty garage dir

    # the garage -> calculator link switches tabs without error
    car = garage.new_car("Linked")
    car["gearing"]["pinion"] = 30
    win._open_in_calc(car)
    assert win.tabs.currentWidget() is win.gear_tab
    assert win.gear_tab.pinion.value() == 30


def test_update_banner_shows_and_consent_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()

    # hidden until a check reports a staged update (isHidden reflects the local
    # flag; isVisible would need the top-level window shown, which offscreen isn't)
    assert win.update_banner.isHidden()
    assert win.update_consented is False

    # the signal (as a background check would emit it) reveals a named banner
    win.update_ready.emit("v9.9.9")
    assert not win.update_banner.isHidden()
    assert "v9.9.9" in win.update_label.text()

    # dismissing hides it and does NOT consent to swapping the binary on quit
    win._dismiss_update()
    assert win.update_banner.isHidden()
    assert win.update_consented is False

    # "Restart & update" is the only path that consents to applying the update
    win.update_ready.emit("v9.9.9")
    win._restart_to_update()
    assert win.update_consented is True


def test_log_tab_preload_stream_and_filter(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import logging

    from PySide6.QtWidgets import QApplication

    from app import logsetup
    from app import main as app_main

    _ = QApplication.instance() or QApplication([])

    # a record buffered before the tab exists must preload into the view
    logsetup._buffer.clear()
    logsetup._buffer.append("2026-01-01 00:00:00 · INFO · app.pre · preloaded line")

    tab = app_main.LogTab()
    try:
        assert "preloaded line" in tab.view.toPlainText()

        # a live record routed through the root logger must stream in
        logging.getLogger("app.live").warning("live warning line")
        assert "live warning line" in tab.view.toPlainText()

        # Warnings+ hides the INFO preload but keeps the WARNING
        tab.level_filter.setCurrentIndex(2)
        assert "preloaded line" not in tab.view.toPlainText()
        assert "live warning line" in tab.view.toPlainText()

        # back to All shows both again
        tab.level_filter.setCurrentIndex(0)
        assert "preloaded line" in tab.view.toPlainText()
        assert "live warning line" in tab.view.toPlainText()
    finally:
        logging.getLogger().removeHandler(tab._handler)
        logsetup._buffer.clear()


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


def test_tools_tab_search_and_category_filter(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    tools = [
        _tool(id="a", name="Servo Prog", vendor="Reve D", category="servo"),
        _tool(id="b", name="ESC Link", vendor="Hobbywing", category="esc"),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ToolsTab()  # constructed directly so the test runs off Windows too

    # text search matches name/vendor/category and hides the rest
    tab.search.setText("hobbywing")
    assert tab.table.isRowHidden(0)
    assert not tab.table.isRowHidden(1)

    # clearing restores every row
    tab.search.setText("")
    assert not tab.table.isRowHidden(0)
    assert not tab.table.isRowHidden(1)

    # category dropdown filters independently (index 0 is "All categories")
    servo_index = tab.category_filter.findData("servo")
    tab.category_filter.setCurrentIndex(servo_index)
    assert not tab.table.isRowHidden(0)
    assert tab.table.isRowHidden(1)

    # category + search combine with AND: servo category but a non-matching query
    tab.search.setText("esc")
    assert tab.table.isRowHidden(0)
    assert tab.table.isRowHidden(1)


def test_garage_tab_search_and_maintenance_log(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()
    tab = win.garage_tab

    # two cars, distinguished by chassis
    tab._on_new()
    tab.name.setText("Blue")
    tab.chassis.setText("Yokomo")
    tab._on_save()
    tab._on_new()
    tab.name.setText("Red")
    tab.chassis.setText("MST")
    tab._on_save()
    assert tab.list.count() == 2

    # search matches the chassis field, hiding the non-match
    tab.search.setText("yokomo")
    hidden = [tab.list.item(i).isHidden() for i in range(tab.list.count())]
    assert hidden.count(False) == 1  # exactly one visible
    tab.search.setText("")
    assert not any(tab.list.item(i).isHidden() for i in range(tab.list.count()))

    # select Red and add a maintenance log entry; it persists on the car
    for i in range(tab.list.count()):
        if tab.list.item(i).text() == "Red":
            tab.list.setCurrentRow(i)
            break
    tab.log_note.setText("replaced bearings")
    tab._on_add_log()
    assert tab.log_table.rowCount() == 1
    red = next(c for c in garage.list_cars() if c["name"] == "Red")
    assert red["log"][0]["note"] == "replaced bearings"

    # removing the entry persists too
    tab.log_table.setCurrentCell(0, 0)
    tab._on_remove_log()
    assert tab.log_table.rowCount() == 0
    red = next(c for c in garage.list_cars() if c["name"] == "Red")
    assert red["log"] == []
