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


@pytest.fixture(autouse=True)
def _hermetic_qsettings(tmp_path, monkeypatch):
    """Keep every test's QSettings in its own temp INI, never the machine's real store,
    so MainWindow's geometry/last-tab restore can't read stale developer/CI state."""
    import app.main
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        app.main,
        "QSettings",
        lambda *a, **k: QSettings(str(tmp_path / "qsettings.ini"), QSettings.Format.IniFormat),
    )


@pytest.fixture(autouse=True)
def _hermetic_manuals_dir(tmp_path, monkeypatch):
    """Redirect the manual cache to tmp so ManualsTab's startup sweep (clear_partial_manuals)
    and any download never touch the real user data dir."""
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")


def test_catalog_entries_match_schema():
    schema = json.loads((ROOT / "catalog" / "schema.json").read_text(encoding="utf-8"))
    entries = sorted((ROOT / "catalog" / "tools").glob("*.json"))
    assert entries, "catalog must have at least one entry"
    for f in entries:
        tool = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(tool, schema)
        assert tool["id"] == f.stem, f"{f.name}: id must match filename"


def test_info_only_entry_validates_without_download():
    # a hardware-only device is an info card: no download/install, just links
    schema = json.loads((ROOT / "catalog" / "schema.json").read_text(encoding="utf-8"))
    info = {
        "id": "some-gyro",
        "name": "Some Gyro",
        "vendor": "Test",
        "version": "n/a",
        "category": "gyro",
        "links": [{"name": "Manual", "url": "https://example.invalid/manual.pdf"}],
    }
    jsonschema.validate(info, schema)


def test_drivers_entry_validates():
    # a tool may list USB/adapter drivers the Tools tab surfaces as "Install driver…"
    schema = json.loads((ROOT / "catalog" / "schema.json").read_text(encoding="utf-8"))
    tool = _tool(
        drivers=[{"name": "CH340 USB driver", "url": "https://example.invalid/ch340.zip"}]
    )
    jsonschema.validate(tool, schema)


def test_download_without_install_rejected():
    # dependentRequired: a software entry must still declare how to install
    schema = json.loads((ROOT / "catalog" / "schema.json").read_text(encoding="utf-8"))
    bad = {
        "id": "x", "name": "X", "vendor": "T", "version": "1",
        "download": {"url": "https://x/f.zip", "archive": "zip"},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


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


def test_uninstall_removes_downloaded_install(sandbox):
    installer.install(_tool())
    assert installer.get_state("fake-tool") is not None
    assert (installer.TOOLS_DIR / "fake-tool").exists()
    installer.uninstall("fake-tool")
    assert installer.get_state("fake-tool") is None
    assert not (installer.TOOLS_DIR / "fake-tool").exists()
    assert not installer._state_file("fake-tool").exists()


def test_uninstall_keeps_existing_users_files(sandbox, tmp_path):
    exe = tmp_path / "AlreadyHere.exe"
    exe.write_bytes(b"MZ")
    installer.register_existing(_tool(), str(exe), "1.0")
    # a tool dir sitting alongside a located ("existing") install must be left alone -
    # this sentinel is what actually distinguishes the existing branch from a download
    # (without it the assert can't fail even if the source=="existing" guard is deleted).
    tool_dir = installer.TOOLS_DIR / "fake-tool"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "keep.txt").write_text("x", encoding="utf-8")
    installer.uninstall("fake-tool")
    assert exe.exists()  # the user's own file must not be deleted
    assert (tool_dir / "keep.txt").exists()  # the existing-guard skipped rmtree
    assert installer.get_state("fake-tool") is None


def test_uninstall_missing_is_noop():
    installer.uninstall("never-installed-xyz")  # must not raise


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


def test_install_exe_download_runs_installer(sandbox, monkeypatch):
    # archive:"exe" -> the download IS the installer: copied in, run, app resolved
    dest = installer.TOOLS_DIR / "fake-tool"
    ran = {}

    def fake_run(cmd, check, timeout):
        ran["cmd"] = cmd
        (dest / "App.exe").write_bytes(b"MZ")  # the installer "installs" the app

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    monkeypatch.setattr(
        installer, "_download", lambda url, d, progress: d.write_bytes(b"MZ installer")
    )
    tool = _tool()
    tool["download"] = {"url": "https://x/setup.exe", "archive": "exe", "sha256": None}
    tool["install"] = {
        "setup_relative_path": "setup.exe",
        "setup_args": ["/S", "/D={dest}"],
        "exe_relative_path": "App.exe",
    }
    exe = installer.install(tool)
    assert exe.name == "App.exe" and exe.exists()
    assert ran["cmd"][0].endswith("setup.exe")  # the copied installer was run
    assert ran["cmd"][-1] == f"/D={dest}"  # {dest} placeholder substituted


def test_install_exe_download_nested_relative_path(sandbox, monkeypatch):
    # archive:"exe" with a nested exe_relative_path must create the parent dir before copy
    monkeypatch.setattr(
        installer, "_download", lambda url, d, progress: d.write_bytes(b"MZ portable")
    )
    tool = _tool()
    tool["download"] = {"url": "https://x/app.exe", "archive": "exe", "sha256": None}
    tool["install"] = {"exe_relative_path": "sub/App.exe", "portable": True}
    exe = installer.install(tool)  # would crash with FileNotFoundError before the fix
    assert exe.name == "App.exe" and exe.exists() and exe.parent.name == "sub"


@WINDOWS_ONLY
def test_install_elevates_when_setup_requires_admin(sandbox, monkeypatch):
    # a requireAdministrator installer fails CreateProcess (WinError 740) -> the install
    # must retry it elevated via ShellExecuteEx, passing the /D= path verbatim (unquoted).
    dest = installer.TOOLS_DIR / "fake-tool"
    elevated = {}

    def fake_direct(cmd, check, timeout):
        raise OSError(0, "requires elevation", None, 740)  # direct CreateProcess

    def fake_elevated(verb, file, params, timeout):
        elevated["verb"] = verb
        elevated["params"] = params
        (dest / "App.exe").write_bytes(b"MZ")  # the elevated installer "installs" the app

    monkeypatch.setattr(installer.subprocess, "run", fake_direct)
    monkeypatch.setattr(installer, "_shell_execute_ex", fake_elevated)
    tool = _tool()
    tool["install"] = {
        "setup_relative_path": "FakeTool.exe",  # present in the sandbox zip
        "setup_args": ["/S", "/D={dest}"],
        "exe_relative_path": "App.exe",
    }
    exe = installer.install(tool)
    assert exe.name == "App.exe"
    assert elevated["verb"] == "runas"
    assert elevated["params"] == f"/S /D={dest}"  # verbatim, /D= unquoted and last


@WINDOWS_ONLY
def test_install_propagates_elevated_failure(sandbox, monkeypatch):
    # a failed elevated install must raise, not be mistaken for success
    def fake_direct(cmd, check, timeout):
        raise OSError(0, "requires elevation", None, 740)

    def fake_elevated(verb, file, params, timeout):
        raise installer.subprocess.CalledProcessError(1, file)

    monkeypatch.setattr(installer.subprocess, "run", fake_direct)
    monkeypatch.setattr(installer, "_shell_execute_ex", fake_elevated)
    tool = _tool()
    tool["install"] = {
        "setup_relative_path": "FakeTool.exe",
        "setup_args": ["/S", "/D={dest}"],
        "exe_relative_path": "App.exe",
    }
    with pytest.raises(installer.subprocess.CalledProcessError):
        installer.install(tool)


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


def test_manual_cache_path_is_deterministic_and_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    a = installer.manual_cache_path("https://x/one.pdf")
    b = installer.manual_cache_path("https://x/two.pdf")
    assert a == installer.manual_cache_path("https://x/one.pdf")  # same url -> same file
    assert a != b  # different url -> different file
    assert a.suffix == ".pdf" and a.parent == installer.MANUALS_DIR


def test_download_manual_caches_and_leaves_no_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    monkeypatch.setattr(
        installer, "_download",
        lambda url, dest, progress, cancel=None: dest.write_bytes(b"%PDF data"),
    )
    url = "https://example.invalid/manual.pdf"
    assert not installer.manual_is_cached(url)
    path = installer.download_manual(url)
    assert path == installer.manual_cache_path(url)
    assert path.read_bytes() == b"%PDF data"
    assert installer.manual_is_cached(url)
    assert not path.with_suffix(".part").exists()  # temp renamed in, not left behind


def test_download_manual_failure_caches_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")

    def boom(url, dest, progress, cancel=None):
        dest.write_bytes(b"partial")  # a real mid-stream failure leaves a partial file
        raise installer.VendorFileChanged("dead link returned a web page")

    monkeypatch.setattr(installer, "_download", boom)
    url = "https://example.invalid/manual.pdf"
    with pytest.raises(installer.VendorFileChanged):
        installer.download_manual(url)
    assert not installer.manual_is_cached(url)  # no .pdf left as if it were downloaded
    assert not installer.manual_cache_path(url).with_suffix(".part").exists()  # cleaned up


def test_is_pdf_heuristic():
    from app.main import _is_pdf

    assert _is_pdf("https://x/a.pdf")
    assert _is_pdf("https://x/a.PDF")  # case-insensitive
    assert _is_pdf("https://x/a.pdf?ver=2")  # query string ignored
    assert _is_pdf("https://x/a.pdf#page=3")  # fragment ignored
    assert not _is_pdf("https://x/support")
    assert not _is_pdf(None)


def test_clear_partial_manuals_removes_orphans(tmp_path, monkeypatch):
    # startup cleanup clears a .part left by a past download the app was killed mid-flight
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    (installer.MANUALS_DIR / "deadbeef.part").write_bytes(b"leftover")
    installer.clear_partial_manuals()
    assert not list(installer.MANUALS_DIR.glob("*.part"))


def test_clear_partial_manuals_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "nope")
    installer.clear_partial_manuals()  # glob on a missing dir yields nothing; must not raise


def test_download_manual_does_not_sweep_other_parts(monkeypatch, tmp_path):
    # parallel-safety: downloading one URL must NOT delete another in-flight download's temp
    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    sibling = installer.MANUALS_DIR / "aaaa1111.part"  # pretend another row is mid-download
    sibling.write_bytes(b"in flight")
    monkeypatch.setattr(
        installer, "_download",
        lambda url, dest, progress, cancel=None: dest.write_bytes(b"%PDF"),
    )
    installer.download_manual("https://example.invalid/other.pdf")
    assert sibling.exists()  # the other download's temp survived


def test_download_manual_cancel_raises_and_caches_nothing(monkeypatch, tmp_path):
    import threading

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")

    class FakeResp:  # a streaming response with two chunks; cancel fires before the first
        headers = {"content-type": "application/pdf", "content-length": "8"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"1234"
            yield b"5678"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(installer.requests, "get", lambda *a, **k: FakeResp())
    ev = threading.Event()
    ev.set()  # already cancelled -> aborts at the first chunk
    url = "https://example.invalid/manual.pdf"
    with pytest.raises(installer.DownloadCancelled):
        installer.download_manual(url, cancel=ev)
    assert not installer.manual_is_cached(url)  # nothing cached
    assert not installer.manual_cache_path(url).with_suffix(".part").exists()  # temp cleaned


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
    assert [a.text() for a in button.menu().actions()] == [
        "Locate existing install…",
        "Open install folder",
        "Uninstall",
    ]


def test_mainwindow_loads_catalog_once(monkeypatch, tmp_path):
    # ToolsTab (Windows) + ManualsTab must share one fetch, not fetch twice at startup
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    calls = {"n": 0}

    def counting_load():
        calls["n"] += 1
        return [_tool()]

    monkeypatch.setattr(catalog, "load_catalog", counting_load)
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    app_main.MainWindow()
    assert calls["n"] == 1


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
    expected = tools + ["Manuals", "Garage", "Gear Calculator", "Tuning", "Log", "Settings"]
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


def test_tools_tab_excludes_info_only_tools(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    info = {  # a hardware device: no PC software, only a manual link
        "id": "gyd550", "name": "GYD550", "vendor": "Futaba", "version": "n/a",
        "category": "gyro",
        "links": [{"name": "Manual (PDF)", "url": "https://example.invalid/gyd550.pdf"}],
    }
    software = _tool(id="sw", name="USB Link", vendor="Hobbywing")  # has "download"
    monkeypatch.setattr(catalog, "load_catalog", lambda: [info, software])
    _ = QApplication.instance() or QApplication([])

    # the Tools tab shows only the installable tool; the info-only device is filtered out
    tools = app_main.ToolsTab()  # constructed directly so the test runs off Windows too
    assert tools.table.rowCount() == 1
    assert tools.table.item(0, 0).text() == "USB Link"
    assert [t["id"] for t in tools.tools] == ["sw"]

    # but the info-only device's manual is still reachable on the Manuals tab
    manuals = app_main.ManualsTab()
    names = [manuals.table.item(r, 0).text() for r in range(manuals.table.rowCount())]
    assert "Manual (PDF)" in names


def test_tools_tab_website_button_opens_homepage(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(homepage="https://example.invalid/vendor")]
    )
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ToolsTab()

    web = tab.table.cellWidget(0, 5)  # Website column, alongside the action button at col 4
    assert web.text() == "Website"
    opened = {}
    monkeypatch.setattr(
        app_main.QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    web.click()
    assert opened["u"].endswith("/vendor")


def test_manuals_tab_table_rows_and_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")  # cache stays in tmp
    tool = _tool(
        category="esc",
        homepage="https://example.invalid/site",
        links=[
            {"name": "Support page", "url": "https://example.invalid/support"},
            {"name": "Manual (PDF)", "url": "https://example.invalid/manual.pdf"},
        ],
    )
    monkeypatch.setattr(catalog, "load_catalog", lambda: [tool])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()  # cross-platform: one row per link

    assert tab.table.rowCount() == 2
    rows = {tab.table.item(r, 0).text(): r for r in range(2)}

    web = rows["Support page"]  # a non-PDF link opens in the browser
    assert tab.table.item(web, 3).text() == "Web page"
    assert tab.table.cellWidget(web, 4).text() == "Open"

    pdf = rows["Manual (PDF)"]  # a PDF starts uncached -> Download
    assert tab.table.item(pdf, 3).text() == ""
    assert tab.table.cellWidget(pdf, 4).text() == "Download"

    opened = {}
    monkeypatch.setattr(
        app_main.QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    tab.table.cellWidget(web, 4).click()  # web link -> its URL
    assert opened["u"].endswith("/support")
    tab.table.cellWidget(pdf, 5).click()  # Website column -> the homepage
    assert opened["u"].endswith("/site")

    # clicking Download on an uncached PDF routes to the (threaded) download, not open
    routed = {}
    monkeypatch.setattr(tab, "_start_download", lambda r, u: routed.update(row=r, url=u))
    tab.table.cellWidget(pdf, 4).click()
    assert routed == {"row": pdf, "url": "https://example.invalid/manual.pdf"}


def test_manuals_tab_cached_pdf_opens_local_file(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    url = "https://example.invalid/manual.pdf"
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    installer.manual_cache_path(url).write_bytes(b"%PDF-1.4 fake")  # pre-seed the cache

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool(links=[{"name": "M", "url": url}])])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()

    assert tab.table.item(0, 3).text() == "Downloaded"
    button = tab.table.cellWidget(0, 4)
    assert button.text() == "Open"

    opened = {}
    monkeypatch.setattr(
        app_main.QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    button.click()
    # opens the local cached file (a file:// URL), never the remote http URL
    assert opened["u"].startswith("file:") and opened["u"].endswith(".pdf")


def test_manuals_tab_parallel_downloads_and_dup_guard(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import threading

    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    same = "https://example.invalid/shared.pdf"
    tools = [
        _tool(id="a", name="A", links=[{"name": "A (PDF)", "url": "https://example.invalid/a.pdf"}]),
        _tool(id="b", name="B", links=[{"name": "B (PDF)", "url": same}]),
        _tool(id="c", name="C", links=[{"name": "C (PDF)", "url": same}]),  # shares B's URL
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()

    started = []
    monkeypatch.setattr(tab, "_start_download", lambda r, u: started.append((r, u)))
    tab.table.cellWidget(0, 4).click()  # two different rows both start -> parallel, no refuse
    tab.table.cellWidget(1, 4).click()
    assert len(started) == 2

    # row C shares row B's URL and B is (pretend) in flight -> no duplicate download thread
    started.clear()
    tab._active[1] = threading.Event()
    row_c = next(r for r in range(3) if tab.table.item(r, 0).text() == "C (PDF)")
    tab.table.cellWidget(row_c, 4).click()
    assert started == []  # the dup-URL guard kept it from starting a second thread


def test_manuals_tab_click_cancels_active_download(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import threading

    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    url = "https://example.invalid/m.pdf"
    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(links=[{"name": "M (PDF)", "url": url}])]
    )
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()

    ev = threading.Event()
    tab._active[0] = ev  # simulate an in-flight download; its button shows "Cancel"
    tab.table.cellWidget(0, 4).setText("Cancel")
    tab.table.cellWidget(0, 4).click()  # -> _cancel_download
    assert ev.is_set()  # the worker will see this and abort
    assert tab.table.cellWidget(0, 4).text() == "Cancelling…"
    assert not tab.table.cellWidget(0, 4).isEnabled()


def test_manuals_tab_finished_success_refreshes_siblings(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import threading

    from PySide6.QtWidgets import QApplication, QProgressBar

    from app import catalog
    from app import main as app_main

    url = "https://example.invalid/shared.pdf"  # two rows link the SAME manual
    tools = [
        _tool(id="a", name="A", links=[{"name": "Shared (PDF)", "url": url}]),
        _tool(id="b", name="B", links=[{"name": "Shared (PDF)", "url": url}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()

    # simulate row 0 downloading with its inline bar, then completing (file now cached)
    tab._active[0] = threading.Event()
    tab.table.setCellWidget(0, 3, QProgressBar())
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    installer.manual_cache_path(url).write_bytes(b"%PDF")
    tab._download_finished(0, None)

    assert 0 not in tab._active
    assert tab.table.cellWidget(0, 3) is None  # inline progress bar removed
    # BOTH the finished row and its sibling sharing the URL flip to Open/Downloaded
    assert [tab.table.cellWidget(r, 4).text() for r in range(2)] == ["Open", "Open"]
    assert [tab.table.item(r, 3).text() for r in range(2)] == ["Downloaded", "Downloaded"]


def test_manuals_tab_finished_cancel_resets_and_error_warns(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import threading

    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    url = "https://example.invalid/m.pdf"
    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(links=[{"name": "M (PDF)", "url": url}])]
    )
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()

    warned = {"n": 0}
    monkeypatch.setattr(
        app_main.QMessageBox, "warning", lambda *a, **k: warned.update(n=warned["n"] + 1)
    )

    # cancel: no error, nothing cached -> row resets to "Download", NO dialog
    tab._active[0] = threading.Event()
    tab._download_finished(0, None)
    assert tab.table.cellWidget(0, 4).text() == "Download"
    assert warned["n"] == 0

    # real failure: warning dialog, row still reset
    tab._active[0] = threading.Event()
    tab._download_finished(0, "network boom")
    assert warned["n"] == 1
    assert tab.table.cellWidget(0, 4).text() == "Download"


def test_manuals_tab_pdf_row_menu_open_and_delete(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    url = "https://example.invalid/shared.pdf"
    tools = [
        _tool(id="a", name="A", links=[
            {"name": "Support", "url": "https://example.invalid/support"},  # web link -> no menu
            {"name": "A (PDF)", "url": url},
        ]),
        _tool(id="b", name="B", links=[{"name": "B (PDF)", "url": url}]),  # sibling, same URL
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()
    rows = {tab.table.item(r, 0).text(): r for r in range(tab.table.rowCount())}

    # a web-page row has no dropdown; PDF rows carry the two actions
    assert tab.table.cellWidget(rows["Support"], 4).menu() is None
    menu = tab.table.cellWidget(rows["A (PDF)"], 4).menu()
    assert [a.text() for a in menu.actions()] == ["Open containing folder", "Delete downloaded PDF"]
    assert all(not a.isEnabled() for a in menu.actions())  # disabled until downloaded

    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    installer.manual_cache_path(url).write_bytes(b"%PDF")  # now "downloaded"
    tab._refresh_idle_rows()
    assert all(a.isEnabled() for a in menu.actions())

    # Open containing folder -> the manuals cache dir
    opened = {}
    monkeypatch.setattr(
        app_main.QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    tab._open_folder(rows["A (PDF)"])
    assert opened["u"].startswith("file:") and "manuals" in opened["u"].lower()

    # declining the delete confirmation keeps the file
    monkeypatch.setattr(
        app_main.QMessageBox, "question", lambda *a, **k: app_main.QMessageBox.StandardButton.No
    )
    tab._delete_pdf(rows["A (PDF)"])
    assert installer.manual_is_cached(url)

    # confirming deletes it and resets BOTH rows sharing the URL back to "Download"
    monkeypatch.setattr(
        app_main.QMessageBox, "question", lambda *a, **k: app_main.QMessageBox.StandardButton.Yes
    )
    tab._delete_pdf(rows["A (PDF)"])
    assert not installer.manual_is_cached(url)
    assert tab.table.cellWidget(rows["A (PDF)"], 4).text() == "Download"
    assert tab.table.cellWidget(rows["B (PDF)"], 4).text() == "Download"  # sibling reset too
    assert all(not a.isEnabled() for a in menu.actions())  # menu disabled again


def test_manuals_tab_refresh_all_updates_sibling_row(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    url = "https://example.invalid/shared.pdf"  # two tools link the SAME manual
    tools = [
        _tool(id="a", name="Tool A", links=[{"name": "Shared (PDF)", "url": url}]),
        _tool(id="b", name="Tool B", links=[{"name": "Shared (PDF)", "url": url}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()
    assert [tab.table.cellWidget(r, 4).text() for r in range(2)] == ["Download", "Download"]

    # simulate row 0's download completing: the file is now cached and _download_finished runs
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    installer.manual_cache_path(url).write_bytes(b"%PDF")
    tab._download_finished(0, None)

    # BOTH rows (not just row 0) must now read "Open"/"Downloaded"
    assert [tab.table.cellWidget(r, 4).text() for r in range(2)] == ["Open", "Open"]
    assert [tab.table.item(r, 3).text() for r in range(2)] == ["Downloaded", "Downloaded"]


def test_manuals_tab_skips_link_without_name(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    # an unvalidated remote catalog link missing "name" must be skipped, not crash the build
    tool = _tool(links=[
        {"url": "https://example.invalid/nameless.pdf"},  # malformed: no name
        {"name": "Good (PDF)", "url": "https://example.invalid/good.pdf"},
    ])
    monkeypatch.setattr(catalog, "load_catalog", lambda: [tool])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()  # would raise KeyError on link["name"] before the guard
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Good (PDF)"


def test_manuals_tab_search_and_category_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    tools = [
        _tool(id="a", name="Servo Prog", vendor="Reve D", category="servo",
              links=[{"name": "Servo manual (PDF)", "url": "https://example.invalid/servo.pdf"}]),
        _tool(id="b", name="ESC Link", vendor="Hobbywing", category="esc",
              links=[{"name": "ESC support page", "url": "https://example.invalid/esc"}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()
    assert tab.table.rowCount() == 2
    esc_row = next(r for r in range(2) if "ESC" in tab.table.item(r, 0).text())
    servo_row = 1 - esc_row

    # text search matches vendor and hides the rest
    tab.search.setText("hobbywing")
    assert not tab.table.isRowHidden(esc_row) and tab.table.isRowHidden(servo_row)

    # searching the manual name itself now works (the old grouped-list tab couldn't)
    tab.search.setText("servo manual")
    assert tab.table.isRowHidden(esc_row) and not tab.table.isRowHidden(servo_row)

    tab.search.setText("")  # cleared -> every row back
    assert not tab.table.isRowHidden(esc_row) and not tab.table.isRowHidden(servo_row)

    # category dropdown filters independently (index 0 is "All categories")
    tab.category_filter.setCurrentIndex(tab.category_filter.findData("servo"))
    assert tab.table.isRowHidden(esc_row) and not tab.table.isRowHidden(servo_row)


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


def test_garage_tab_duplicate(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    # a saved car with a log entry, then selected
    tab.name.setText("Original")
    tab._on_save()
    tab.log_note.setText("first run")
    tab._on_add_log()
    assert tab.log_table.rowCount() == 1

    tab._on_duplicate()

    cars = garage.list_cars()
    assert len(cars) == 2  # the clone was saved alongside the original
    copy = next(c for c in cars if c["name"] == "Original (copy)")
    assert copy["log"] == []  # a duplicate starts with an empty log
    assert copy["id"] != next(c for c in cars if c["name"] == "Original")["id"]
    # the tab now shows the copy, selected
    assert tab.current_id == copy["id"]
    assert tab.name.text() == "Original (copy)"


def test_garage_tab_export_import_json_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    # fill a car and export it to JSON (dialog stubbed to a .json path)
    tab.name.setText("Exported Rig")
    tab.chassis.setText("Yokomo")
    tab.pinion.setValue(28)
    out = tmp_path / "rig.json"
    monkeypatch.setattr(
        app_main.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "JSON (*.json)")
    )
    tab._on_export()
    dumped = json.loads(out.read_text(encoding="utf-8"))  # a valid, re-importable car dict
    assert dumped["name"] == "Exported Rig" and dumped["gearing"]["pinion"] == 28

    # import it back: a fresh car appears with a new id but the same fields
    monkeypatch.setattr(
        app_main.QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), "Car spec (*.json)")
    )
    tab._on_import()
    cars = garage.list_cars()
    assert len(cars) == 1  # the export was never saved; only the import persists
    imported = cars[0]
    assert imported["name"] == "Exported Rig"
    assert imported["chassis"] == "Yokomo"
    assert imported["gearing"]["pinion"] == 28
    assert imported["id"] != dumped["id"]  # a fresh id, so import can't clobber


def test_garage_tab_export_txt_writes_spec_sheet(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    tab.name.setText("Sheet Rig")
    out = tmp_path / "rig.txt"  # non-.json path -> the readable spec-sheet branch
    monkeypatch.setattr(
        app_main.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "Text files (*.txt)")
    )
    tab._on_export()
    assert out.read_text(encoding="utf-8") == garage.format_spec_sheet(tab._form_to_car())


def test_garage_tab_import_bad_json_warns(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    bad = tmp_path / "not-a-car.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, but not a car object
    monkeypatch.setattr(
        app_main.QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "")
    )
    warned = {}
    monkeypatch.setattr(
        app_main.QMessageBox, "warning", lambda *a, **k: warned.update(shown=True)
    )
    tab._on_import()  # must warn, never raise
    assert warned.get("shown")
    assert garage.list_cars() == []  # nothing saved from a bad import


def test_garage_tab_import_malformed_car_warns(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    # a JSON object (passes the "is a dict" check) but with a junk field type that only
    # blows up when rendered into the form - must warn, never crash the GUI, never save.
    bad = tmp_path / "bad-types.json"
    bad.write_text('{"name": "X", "gearing": {"pinion": "not a number"}}', encoding="utf-8")
    monkeypatch.setattr(
        app_main.QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "")
    )
    warned = {}
    monkeypatch.setattr(
        app_main.QMessageBox, "warning", lambda *a, **k: warned.update(shown=True)
    )
    tab._on_import()
    assert warned.get("shown")
    assert garage.list_cars() == []  # a car that fails to render is never persisted


def test_gear_tab_whatif_table(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage, gearing
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GearTab()

    tab.pinion.setValue(20)  # triggers _recompute; span=3 -> pinions 17..23 = 7 rows
    assert tab.sweep_table.rowCount() == 7

    # exactly one row (the current pinion) is bold, and it reads "20"
    bold_rows = [
        r for r in range(tab.sweep_table.rowCount())
        if tab.sweep_table.item(r, 0).font().bold()
    ]
    assert len(bold_rows) == 1
    assert tab.sweep_table.item(bold_rows[0], 0).text() == "20"

    # the base row's FDR matches gearing.compute for that pinion
    expected = gearing.compute(
        pinion=20, spur=tab.spur.value(), internal_ratio=tab.internal_ratio.value(),
        tire_diameter_mm=tab.tire.value(), kv=tab.kv.value(),
        voltage=gearing.pack_voltage(tab.cells.value()),
    )
    assert tab.sweep_table.item(bold_rows[0], 1).text() == f"{expected['fdr']:.2f}"


def test_gear_chart_dialog(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    dlg = app_main._GearChartDialog(22, 87, 1.9)

    # first run: ranges center on the current setup -> pinion 14..30, spur 77..97
    assert dlg.table.columnCount() == 17
    assert dlg.table.rowCount() == 21
    assert dlg.table.horizontalHeaderItem(0).text() == "14"
    assert dlg.table.verticalHeaderItem(0).text() == "97"  # highest spur on top

    # the current-combo cell holds 1.9 * 87 / 22 and is the only styled one
    row, col = 97 - 87, 22 - 14
    cell = dlg.table.item(row, col)
    assert cell.text() == "7.51"
    assert cell.font().bold()
    assert not dlg.table.item(row, col + 1).font().bold()

    # editing a range rebuilds; an inverted range (min > max) still renders sorted
    dlg.pinion_min.setValue(40)  # max is 30 -> sorted -> columns 30..40
    assert dlg.table.columnCount() == 11
    assert dlg.table.horizontalHeaderItem(0).text() == "30"

    # closing persists the ranges; a fresh dialog restores them over its defaults
    dlg.done(0)
    dlg2 = app_main._GearChartDialog(22, 87, 1.9)
    assert dlg2.pinion_min.value() == 40
    assert dlg2.spur_max.value() == 97


def test_tuning_tab(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()

    # the chart now lives on the Chassis sub-tab of an inner QTabWidget
    assert tab.subtabs.tabText(0) == "Chassis"
    assert tab.subtabs.widget(0) is tab.chassis
    chart = tab.chassis

    assert chart.table.rowCount() == len(app_main._TUNING_ROWS) == 18
    assert chart.table.columnCount() == 3
    assert chart.table.horizontalHeaderItem(1).text() == "If understeering"
    assert chart.table.item(0, 0).text() == "Ride Height (front)"
    assert chart.table.item(0, 1).text() == "Decrease"
    assert chart.table.item(0, 2).text() == "Increase"

    # search filters on the setting column, case-insensitive
    chart.search.setText("DIFF")
    visible = [r for r in range(chart.table.rowCount()) if not chart.table.isRowHidden(r)]
    assert visible == [17]  # only Rear Diff
    chart.search.setText("")
    assert not any(chart.table.isRowHidden(r) for r in range(chart.table.rowCount()))

    # a symptom radio highlights only its column; Both clears the highlight
    accent = QColor(app_main._ACCENT)
    chart.radio_under.setChecked(True)
    assert chart.table.item(0, 1).background().color() == accent
    assert chart.table.item(0, 2).background().color() != accent
    chart.radio_over.setChecked(True)
    assert chart.table.item(0, 2).background().color() == accent
    assert chart.table.item(0, 1).background().color() != accent
    chart.radio_both.setChecked(True)
    assert chart.table.item(0, 1).background().color() != accent
    assert chart.table.item(0, 2).background().color() != accent


def test_tuning_explainer_tooltips(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    # every chart row has a tip, and no tip is orphaned
    assert set(app_main._TUNING_TIPS) == {r[0] for r in app_main._TUNING_ROWS}

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()  # keep a reference or Qt deletes the widget tree
    table = tab.chassis.table
    assert all(table.item(r, 0).toolTip() for r in range(table.rowCount()))


def test_gear_tab_reload_preserves_car_selection(monkeypatch, tmp_path):
    # switching away and back (showEvent -> _reload_cars) must keep the picked car,
    # not silently reset to "— none —" and disable the save button
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GearTab()

    a = garage.save_car(garage.new_car("Car A"))
    garage.save_car(garage.new_car("Car B"))
    tab._reload_cars()  # picks up the two saved cars

    tab.car_picker.setCurrentIndex(tab.car_picker.findData(a["id"]))
    assert tab.car_picker.currentData() == a["id"]

    tab._reload_cars()  # simulates returning to the tab
    assert tab.car_picker.currentData() == a["id"]  # selection survived
    assert tab.save_btn.isEnabled()

    # a car deleted elsewhere falls back to "— none —" without error
    garage.delete_car(a["id"])
    tab._reload_cars()
    assert tab.car_picker.currentData() is None
    assert not tab.save_btn.isEnabled()


def test_tools_tab_uninstall_and_action_enablement(monkeypatch, sandbox):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ToolsTab()  # constructed directly so the test runs off Windows too

    # before install, both install-only menu actions are disabled
    open_action, uninstall_action = tab._install_actions[0]
    assert not open_action.isEnabled() and not uninstall_action.isEnabled()

    installer.install(_tool())
    tab._refresh_row(0)
    assert open_action.isEnabled() and uninstall_action.isEnabled()
    assert (installer.TOOLS_DIR / "fake-tool").exists()

    # uninstall (confirmation auto-accepted) removes the install and refreshes the row
    monkeypatch.setattr(
        app_main.QMessageBox,
        "question",
        lambda *a, **k: app_main.QMessageBox.StandardButton.Yes,
    )
    tab._uninstall(0)
    assert installer.get_state("fake-tool") is None
    assert not (installer.TOOLS_DIR / "fake-tool").exists()
    assert tab.table.item(0, 3).text() == "Not installed"
    assert not open_action.isEnabled() and not uninstall_action.isEnabled()


def test_tools_tab_update_summary_badge(monkeypatch, sandbox):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool(version="2.0")])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ToolsTab()  # constructed directly so the test runs off Windows too

    assert tab.update_summary.text() == ""  # nothing installed -> no badge

    # install an older version: the row reads "Update" and the badge counts it
    installer.install(_tool(version="1.0"))
    tab._refresh_row(0)
    tab._refresh_summary()
    assert tab.table.cellWidget(0, 4).text() == "Update"
    assert tab.update_summary.text() == "1 update available"


def test_mainwindow_restores_clamped_tab_and_closeevent(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")

    # scope QSettings to a temp INI so nothing leaks to the registry across tests
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app_main,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    seed = QSettings(str(ini), QSettings.Format.IniFormat)
    seed.setValue("tab", 99)  # larger than any possible tab count
    seed.sync()

    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()
    assert win.tabs.currentIndex() == win.tabs.count() - 1  # clamped, not 99

    win.close()  # closeEvent persists geometry + tab and must not raise
    written = QSettings(str(ini), QSettings.Format.IniFormat)
    assert int(written.value("tab")) == win.tabs.count() - 1


def test_garage_preset_action_preserves_computed_gearing(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    # a car whose computed gearing was filled by the Gear Calculator
    car = garage.new_car("Computed")
    car["gearing"].update({"fdr": 7.5, "rollout_mm": 28.5, "top_speed_kmh": 42.1})
    garage.save_car(car)
    tab._reload_list()
    tab.current_id = car["id"]
    tab._fill_form(garage.load_car(car["id"]))

    # saving a preset must not null the computed gearing on disk...
    monkeypatch.setattr(app_main.QInputDialog, "getText", lambda *a, **k: ("carpet", True))
    tab._on_save_preset()

    reloaded = garage.load_car(car["id"])
    assert reloaded["gearing"]["fdr"] == 7.5
    assert reloaded["gearing"]["rollout_mm"] == 28.5
    assert reloaded["gearing"]["top_speed_kmh"] == 42.1
    # ...and the snapshot captures the computed values, not None
    assert reloaded["presets"][0]["gearing"]["fdr"] == 7.5


def test_garage_restore_refreshes_open_form(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import backup, catalog, garage
    from app import main as app_main

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = app_main.GarageTab()

    # save Alpha (pinion 22) and open it, back it up, then make an unsaved local edit
    tab.name.setText("Alpha")
    tab.pinion.setValue(22)
    tab._on_save()
    zip_path = backup.make_backup(tmp_path / "b.zip")
    tab.pinion.setValue(40)  # stale edit that would clobber the restore on next Save

    monkeypatch.setattr(app_main.QFileDialog, "getOpenFileName", lambda *a, **k: (str(zip_path), ""))
    monkeypatch.setattr(
        app_main.QMessageBox, "question",
        lambda *a, **k: app_main.QMessageBox.StandardButton.Yes,
    )
    tab._on_restore()

    # the form was re-filled from disk (22), not left showing the stale 40
    assert tab.pinion.value() == 22


def test_compare_dialog_opens_and_populates(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import garage
    from app import main as app_main

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    a = garage.save_car(garage.new_car("Alpha"))
    garage.save_car(garage.new_car("Beta"))

    # constructing must fully render the table (no _render before self.table exists)
    dlg = app_main._CompareDialog(garage.list_cars(), a["id"])
    assert dlg.table.rowCount() > 0
    assert dlg.combo_a.currentData() != dlg.combo_b.currentData()  # two distinct cars


def test_manuals_tab_shows_homepage_only_info_device(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app import main as app_main

    # an info-only device with a homepage but no links: reachable via a Manuals row
    device = {
        "id": "d", "name": "Gyro X", "vendor": "V", "version": "n/a",
        "category": "gyro", "homepage": "https://vendor.example",
    }
    monkeypatch.setattr(catalog, "load_catalog", lambda: [device])
    _ = QApplication.instance() or QApplication([])
    tab = app_main.ManualsTab()
    assert tab.table.rowCount() == 1
