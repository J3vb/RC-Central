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
    import app.ui.common
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        app.ui.common,
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


def test_uninstall_missing_is_noop(sandbox):
    # sandbox so this can never touch the real per-user TOOLS_DIR
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


def test_launch_non_admin_tracks_process_and_is_idempotent(monkeypatch):
    # the common (non-admin) path starts a QProcess, records it, and is a no-op when
    # the tool is already running. QProcess methods are stubbed so no real exe spawns.
    from app import launcher
    from PySide6.QtCore import QProcess

    launcher._procs.clear()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    starts = []
    monkeypatch.setattr(QProcess, "start", lambda self: starts.append(True))
    monkeypatch.setattr(QProcess, "waitForStarted", lambda self, ms: True)
    monkeypatch.setattr(QProcess, "state", lambda self: QProcess.ProcessState.Running)

    assert not launcher.is_running("t1")  # unknown id -> not running
    launcher.launch("t1", "C:/x/Tool.exe")
    assert launcher.is_running("t1")  # recorded in _procs, state Running
    launcher.launch("t1", "C:/x/Tool.exe")  # already running -> must not start twice
    assert len(starts) == 1


def test_launch_falls_back_to_shellexecute_when_start_fails(monkeypatch):
    # an exe whose manifest demands elevation fails CreateProcess even with
    # needs_admin=false; launch must retry via ShellExecute and NOT track it.
    from app import launcher
    from PySide6.QtCore import QProcess

    launcher._procs.clear()
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(QProcess, "start", lambda self: None)
    monkeypatch.setattr(QProcess, "waitForStarted", lambda self, ms: False)  # CreateProcess fails
    called = {}
    monkeypatch.setattr(
        launcher.os,
        "startfile",
        lambda p, cwd=None: called.update(p=p, cwd=cwd),
        raising=False,  # os.startfile only exists on Windows
    )

    launcher.launch("t2", "C:/x/Tool.exe")
    assert called["p"].endswith("Tool.exe")  # elevated fallback taken
    assert "t2" not in launcher._procs and not launcher.is_running("t2")  # untracked


def test_launch_rejects_non_windows(monkeypatch):
    from app import launcher

    monkeypatch.setattr(launcher.sys, "platform", "linux")
    with pytest.raises(OSError):
        launcher.launch("t3", "/x/Tool")


def test_fetch_update_is_noop_from_source():
    from app import updater

    # running from source (not frozen) with force=False must be a no-op, never a raise;
    # version-comparison itself is covered in test_versions.py
    assert not updater.fetch_update()


def test_load_catalog_uses_remote_and_caches(monkeypatch, tmp_path):
    from app import catalog

    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog, "CACHE_FILE", tmp_path / "catalog.json")
    tools = [_tool()]

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return tools

    monkeypatch.setattr(catalog.requests, "get", lambda *a, **k: _Resp())
    assert catalog.load_catalog() == tools  # normal 200 returned as-is
    cached = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert cached == tools  # and written to the cache for offline use


def test_load_catalog_falls_back_to_valid_cache_when_offline(monkeypatch, tmp_path):
    from app import catalog

    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    cache = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog, "CACHE_FILE", cache)
    seeded = [_tool(id="cached", name="Cached")]
    cache.write_text(json.dumps(seeded), encoding="utf-8")

    def offline(*a, **k):
        raise catalog.requests.RequestException("offline")

    monkeypatch.setattr(catalog.requests, "get", offline)
    assert catalog.load_catalog() == seeded  # valid cache used when the fetch fails


def test_catalog_valid_rejects_hostile_download_shape():
    from app import catalog

    # a hostile "archive" would become a filename suffix at install time -> traversal
    assert not catalog._valid([_tool(download={"url": "https://x/y", "archive": "../../evil"})])
    assert not catalog._valid([_tool(download={"url": "http://x/y", "archive": "zip"})])  # not https
    assert not catalog._valid([_tool(download="not-a-dict")])
    assert catalog._valid([_tool()])  # the well-formed fixture still passes


def test_install_exe_download_defaults_to_resolvable_name(monkeypatch, sandbox):
    # a bare-exe download with no relative-path hint must land under a name _find_exe
    # won't skip - "installer.exe" contains "install" and would fail to auto-resolve
    exe_bytes = b"MZ portable tool"
    monkeypatch.setattr(
        installer, "_download", lambda url, dest, progress: dest.write_bytes(exe_bytes)
    )
    tool = _tool(
        download={"url": "https://x/y.exe", "archive": "exe", "sha256": None},
        install={"portable": True},  # no exe_relative_path / setup_relative_path
    )
    exe = installer.install(tool)
    assert exe.exists() and exe.read_bytes() == exe_bytes
    assert "install" not in exe.name.lower()  # not filtered out by _EXE_SKIP


def test_updater_cleanup_recovers_interrupted_swap(monkeypatch, tmp_path):
    from app import updater

    exe = tmp_path / "RCCentral.exe"
    old = updater._sidelined(exe)
    old.write_bytes(b"MZ previous binary")  # swap died: exe missing, .old present
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(exe))

    updater.cleanup()
    assert exe.exists() and exe.read_bytes() == b"MZ previous binary"  # restored
    assert not old.exists()


def test_updater_cleanup_clears_leftover_when_exe_present(monkeypatch, tmp_path):
    from app import updater

    exe = tmp_path / "RCCentral.exe"
    exe.write_bytes(b"MZ current")
    old = updater._sidelined(exe)
    old.write_bytes(b"MZ stale leftover")  # normal post-update leftover
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(exe))

    updater.cleanup()
    assert exe.read_bytes() == b"MZ current"  # the running binary is untouched
    assert not old.exists()  # and the leftover is cleared


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
    from app.ui.common import _is_pdf

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
    from app.ui.window import MainWindow

    from PySide6.QtWidgets import QToolButton

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    win = MainWindow()
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
    from app.ui.window import MainWindow

    calls = {"n": 0}

    def counting_load():
        calls["n"] += 1
        return [_tool()]

    monkeypatch.setattr(catalog, "load_catalog", counting_load)
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    MainWindow()
    assert calls["n"] == 1


def test_tabs_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.window import MainWindow
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    # scope QSettings to a temp INI: the header combo writes the active-car key
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])
    win = MainWindow()

    # The Tools tab is Windows-only; the rest of the tabs are cross-platform.
    tools = ["Tools"] if sys.platform == "win32" else []
    expected = tools + ["Manuals", "Workshop", "Settings"]
    assert win.tabs.count() == len(expected)
    assert [win.tabs.tabText(i) for i in range(win.tabs.count())] == expected
    workshop = win.workshop_tab
    assert [workshop.subtabs.tabText(i) for i in range(workshop.subtabs.count())] == [
        "Garage", "Gearing", "Tuning",
    ]
    settings_tab = win.settings_tab
    assert [settings_tab.subtabs.tabText(i) for i in range(settings_tab.subtabs.count())] == [
        "Preferences", "Log",
    ]
    if sys.platform == "win32":
        assert win.tools_tab.table.rowCount() == 1  # existing table still wired
        assert win.table is win.tools_tab.table  # back-compat alias holds
    else:
        assert win.tools_tab is None

    win.gear_tab._recompute()
    assert win.gear_tab.fdr_out.text() not in ("", "—")
    assert win.garage_tab.list.count() == 0  # empty garage dir

    # picking a car in the Workshop header seeds the calculator and persists the id
    car = garage.new_car("Linked")
    car["gearing"]["pinion"] = 30
    garage.save_car(car)
    workshop._refresh_combo()  # showEvent doesn't fire offscreen; refresh by hand
    workshop.car_combo.setCurrentIndex(workshop.car_combo.findData(car["id"]))
    assert win.gear_tab.pinion.value() == 30
    assert app.ui.common._settings().value(app.ui.common._ACTIVE_CAR_KEY) == car["id"]
    assert win.garage_tab.name.text() == "Linked"


def test_update_banner_shows_and_consent_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.window import MainWindow

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = MainWindow()

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
    from app.ui.log import LogTab

    _ = QApplication.instance() or QApplication([])

    # a record buffered before the tab exists must preload into the view
    logsetup._buffer.clear()
    logsetup._buffer.append("2026-01-01 00:00:00 · INFO · app.pre · preloaded line")

    tab = LogTab()
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
    from app.ui.window import MainWindow

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = MainWindow()

    tab = win.garage_tab
    tab.name.setText("Test Rig")
    tab._on_save()

    assert tab.list.count() == 1
    cars = garage.list_cars()
    assert len(cars) == 1 and cars[0]["name"] == "Test Rig"
    # the form no longer edits gearing; Save must leave the default block intact
    assert cars[0]["gearing"]["pinion"] == 22


def test_tools_tab_search_and_category_filter(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.tools import ToolsTab

    tools = [
        _tool(id="a", name="Servo Prog", vendor="Reve D", category="servo"),
        _tool(id="b", name="ESC Link", vendor="Hobbywing", category="esc"),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()  # constructed directly so the test runs off Windows too

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
    from app.ui.manuals import ManualsTab
    from app.ui.tools import ToolsTab

    info = {  # a hardware device: no PC software, only a manual link
        "id": "gyd550", "name": "GYD550", "vendor": "Futaba", "version": "n/a",
        "category": "gyro",
        "links": [{"name": "Manual (PDF)", "url": "https://example.invalid/gyd550.pdf"}],
    }
    software = _tool(id="sw", name="USB Link", vendor="Hobbywing")  # has "download"
    monkeypatch.setattr(catalog, "load_catalog", lambda: [info, software])
    _ = QApplication.instance() or QApplication([])

    # the Tools tab shows only the installable tool; the info-only device is filtered out
    tools = ToolsTab()  # constructed directly so the test runs off Windows too
    assert tools.table.rowCount() == 1
    assert tools.table.item(0, 0).text() == "USB Link"
    assert [t["id"] for t in tools.tools] == ["sw"]

    # but the info-only device's manual is still reachable on the Manuals tab
    manuals = ManualsTab()
    names = [manuals.table.item(r, 0).text() for r in range(manuals.table.rowCount())]
    assert "Manual (PDF)" in names


def test_tools_tab_website_button_opens_homepage(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.tools import ToolsTab
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(homepage="https://example.invalid/vendor")]
    )
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()

    web = tab.table.cellWidget(0, 5)  # Website column, alongside the action button at col 4
    assert web.text() == "Website"
    opened = {}
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    web.click()
    assert opened["u"].endswith("/vendor")


def test_manuals_tab_table_rows_and_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.manuals import ManualsTab
    from PySide6.QtGui import QDesktopServices

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
    tab = ManualsTab()  # cross-platform: one row per link

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
        QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
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
    from app.ui.manuals import ManualsTab
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    url = "https://example.invalid/manual.pdf"
    installer.MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    installer.manual_cache_path(url).write_bytes(b"%PDF-1.4 fake")  # pre-seed the cache

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool(links=[{"name": "M", "url": url}])])
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()

    assert tab.table.item(0, 3).text() == "Downloaded"
    button = tab.table.cellWidget(0, 4)
    assert button.text() == "Open"

    opened = {}
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    button.click()
    # opens the local cached file (a file:// URL), never the remote http URL
    assert opened["u"].startswith("file:") and opened["u"].endswith(".pdf")


def test_manuals_tab_parallel_downloads_and_dup_guard(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import threading

    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.manuals import ManualsTab

    same = "https://example.invalid/shared.pdf"
    tools = [
        _tool(id="a", name="A", links=[{"name": "A (PDF)", "url": "https://example.invalid/a.pdf"}]),
        _tool(id="b", name="B", links=[{"name": "B (PDF)", "url": same}]),
        _tool(id="c", name="C", links=[{"name": "C (PDF)", "url": same}]),  # shares B's URL
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()

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
    from app.ui.manuals import ManualsTab

    url = "https://example.invalid/m.pdf"
    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(links=[{"name": "M (PDF)", "url": url}])]
    )
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()

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
    from app.ui.manuals import ManualsTab

    url = "https://example.invalid/shared.pdf"  # two rows link the SAME manual
    tools = [
        _tool(id="a", name="A", links=[{"name": "Shared (PDF)", "url": url}]),
        _tool(id="b", name="B", links=[{"name": "Shared (PDF)", "url": url}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()

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
    from app.ui.manuals import ManualsTab
    from PySide6.QtWidgets import QMessageBox

    url = "https://example.invalid/m.pdf"
    monkeypatch.setattr(
        catalog, "load_catalog", lambda: [_tool(links=[{"name": "M (PDF)", "url": url}])]
    )
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()

    warned = {"n": 0}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.update(n=warned["n"] + 1)
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
    from app.ui.manuals import ManualsTab
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

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
    tab = ManualsTab()
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
        QDesktopServices, "openUrl", lambda u: opened.update(u=u.toString())
    )
    tab._open_folder(rows["A (PDF)"])
    assert opened["u"].startswith("file:") and "manuals" in opened["u"].lower()

    # declining the delete confirmation keeps the file
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    tab._delete_pdf(rows["A (PDF)"])
    assert installer.manual_is_cached(url)

    # confirming deletes it and resets BOTH rows sharing the URL back to "Download"
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
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
    from app.ui.manuals import ManualsTab

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    url = "https://example.invalid/shared.pdf"  # two tools link the SAME manual
    tools = [
        _tool(id="a", name="Tool A", links=[{"name": "Shared (PDF)", "url": url}]),
        _tool(id="b", name="Tool B", links=[{"name": "Shared (PDF)", "url": url}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()
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
    from app.ui.manuals import ManualsTab

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    # an unvalidated remote catalog link missing "name" must be skipped, not crash the build
    tool = _tool(links=[
        {"url": "https://example.invalid/nameless.pdf"},  # malformed: no name
        {"name": "Good (PDF)", "url": "https://example.invalid/good.pdf"},
    ])
    monkeypatch.setattr(catalog, "load_catalog", lambda: [tool])
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()  # would raise KeyError on link["name"] before the guard
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "Good (PDF)"


def test_manuals_tab_search_and_category_filter(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.manuals import ManualsTab

    monkeypatch.setattr(installer, "MANUALS_DIR", tmp_path / "manuals")
    tools = [
        _tool(id="a", name="Servo Prog", vendor="Reve D", category="servo",
              links=[{"name": "Servo manual (PDF)", "url": "https://example.invalid/servo.pdf"}]),
        _tool(id="b", name="ESC Link", vendor="Hobbywing", category="esc",
              links=[{"name": "ESC support page", "url": "https://example.invalid/esc"}]),
    ]
    monkeypatch.setattr(catalog, "load_catalog", lambda: tools)
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()
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
    from app.ui.window import MainWindow

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    win = MainWindow()
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
    from app.ui.garage_tab import GarageTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

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
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    # fill a car and export it to JSON (dialog stubbed to a .json path)
    tab.name.setText("Exported Rig")
    tab.chassis.setText("Yokomo")
    out = tmp_path / "rig.json"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "JSON (*.json)")
    )
    tab._on_export()
    dumped = json.loads(out.read_text(encoding="utf-8"))  # a valid, re-importable car dict
    assert dumped["name"] == "Exported Rig" and dumped["gearing"]["pinion"] == 22

    # import it back: a fresh car appears with a new id but the same fields
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), "Car spec (*.json)")
    )
    tab._on_import()
    cars = garage.list_cars()
    assert len(cars) == 1  # the export was never saved; only the import persists
    imported = cars[0]
    assert imported["name"] == "Exported Rig"
    assert imported["chassis"] == "Yokomo"
    assert imported["gearing"]["pinion"] == 22  # default gearing block round-trips
    assert imported["id"] != dumped["id"]  # a fresh id, so import can't clobber


def test_garage_tab_export_txt_writes_spec_sheet(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    tab.name.setText("Sheet Rig")
    out = tmp_path / "rig.txt"  # non-.json path -> the readable spec-sheet branch
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "Text files (*.txt)")
    )
    tab._on_export()
    assert out.read_text(encoding="utf-8") == garage.format_spec_sheet(tab._form_to_car())


def test_garage_tab_import_bad_json_warns(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    bad = tmp_path / "not-a-car.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, but not a car object
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "")
    )
    warned = {}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.update(shown=True)
    )
    tab._on_import()  # must warn, never raise
    assert warned.get("shown")
    assert garage.list_cars() == []  # nothing saved from a bad import


def test_garage_tab_import_malformed_car_warns(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    # a JSON object (passes the "is a dict" check) but with a junk field type that only
    # blows up when rendered into the form - must warn, never crash the GUI, never save.
    # (name, not gearing: the form no longer renders gearing, so a junk name is the
    # field that trips setText during _fill_form)
    bad = tmp_path / "bad-types.json"
    bad.write_text('{"name": 123}', encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "")
    )
    warned = {}
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: warned.update(shown=True)
    )
    tab._on_import()
    assert warned.get("shown")
    assert garage.list_cars() == []  # a car that fails to render is never persisted


def test_gear_sweep_dialog(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage, gearing
    from app.ui.gear import GearTab, _SweepDialog

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GearTab()

    tab.pinion.setValue(20)
    captured = {}
    monkeypatch.setattr(_SweepDialog, "exec", lambda self: captured.update(dlg=self))
    tab._open_sweep()  # span=3 -> pinions 17..23 = 7 rows
    table = captured["dlg"].table
    assert table.rowCount() == 7

    # exactly one row (the current pinion) is bold, and it reads "20"
    bold_rows = [
        r for r in range(table.rowCount()) if table.item(r, 0).font().bold()
    ]
    assert len(bold_rows) == 1
    assert table.item(bold_rows[0], 0).text() == "20"

    # the base row's FDR matches gearing.compute for that pinion
    expected = gearing.compute(
        pinion=20, spur=tab.spur.value(), internal_ratio=tab.internal_ratio.value(),
        tire_diameter_mm=tab.tire.value(), kv=tab.kv.value(),
        voltage=gearing.pack_voltage(tab.cells.value()),
    )
    assert table.item(bold_rows[0], 1).text() == f"{expected['fdr']:.2f}"


def test_gear_chart_panel(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.gear import GearTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GearTab()
    chart = tab.chart

    # first run: ranges center on the default setup (22/87) -> pinion 14..30, spur 77..97
    assert chart.table.columnCount() == 17
    assert chart.table.rowCount() == 21
    assert chart.table.horizontalHeaderItem(0).text() == "14"
    assert chart.table.verticalHeaderItem(0).text() == "97"  # highest spur on top

    # the current-combo cell holds 1.9 * 87 / 22 and is the only styled one
    row, col = 97 - 87, 22 - 14
    cell = chart.table.item(row, col)
    assert cell.text() == "7.51"
    assert cell.font().bold()
    assert not chart.table.item(row, col + 1).font().bold()

    # the inline chart live-tracks the inputs: bumping the pinion moves the highlight
    tab.pinion.setValue(23)
    assert not chart.table.item(row, col).font().bold()
    assert chart.table.item(row, col + 1).font().bold()

    # editing a range rebuilds; an inverted range (min > max) still renders sorted
    chart.pinion_min.setValue(40)  # max is 30 -> sorted -> columns 30..40
    assert chart.table.columnCount() == 11
    assert chart.table.horizontalHeaderItem(0).text() == "30"

    # range edits persist immediately; a fresh tab restores them over its defaults
    tab2 = GearTab()
    assert tab2.chart.pinion_min.value() == 40
    assert tab2.chart.spur_max.value() == 97


def test_tuning_tab(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from app.ui.common import _ACCENT
    from app.ui.tuning import TuningTab, _TUNING_ROWS

    _ = QApplication.instance() or QApplication([])
    tab = TuningTab()

    assert tab.subtabs.tabText(0) == "Chassis"
    assert tab.subtabs.widget(0) is tab.chassis
    chart = tab.chassis

    assert chart.table.rowCount() == len(_TUNING_ROWS) == 18
    assert chart.table.columnCount() == 3
    assert chart.table.horizontalHeaderItem(1).text() == "If understeering"
    assert chart.table.item(0, 0).text() == "▸ Ride Height (front)"  # arrow = click affordance
    assert chart.table.item(0, 1).text() == "Decrease"
    assert chart.table.item(0, 2).text() == "Increase"

    # search filters on the setting column, case-insensitive
    chart.search.setText("DIFF")
    visible = [r for r in range(chart.table.rowCount()) if not chart.table.isRowHidden(r)]
    assert visible == [17]  # only Rear Diff
    chart.search.setText("")
    assert not any(chart.table.isRowHidden(r) for r in range(chart.table.rowCount()))

    # a symptom radio highlights only its column; Both clears the highlight
    accent = QColor(_ACCENT)
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

    from app.ui.tuning import TuningTab, _TUNING_ROWS, _TUNING_TIPS

    # every chart row has a tip, and no tip is orphaned
    assert set(_TUNING_TIPS) == {r[0] for r in _TUNING_ROWS}

    _ = QApplication.instance() or QApplication([])
    tab = TuningTab()  # keep a reference or Qt deletes the widget tree
    table = tab.chassis.table
    assert all(table.item(r, 0).toolTip() for r in range(table.rowCount()))


def test_tuning_accordion(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.ui.tuning import TuningTab, _TUNING_ROWS, _TUNING_TIPS

    _ = QApplication.instance() or QApplication([])
    tab = TuningTab()
    chart = tab.chassis
    t = chart.table
    base = len(_TUNING_ROWS)
    assert t.rowCount() == base

    # opening: a spanned italic explanation row appears under the clicked setting
    chart._toggle_row(0)
    assert t.rowCount() == base + 1
    assert t.item(0, 0).text().startswith("▾")
    exp = t.item(1, 0)
    assert exp.text() == _TUNING_TIPS["Ride Height (front)"]
    assert exp.font().italic()
    assert t.columnSpan(1, 0) == 3

    # clicking another setting moves the explanation there (one open at a time)
    chart._toggle_row(6)  # Caster renders at row 6 while row 1 is the explanation
    assert t.rowCount() == base + 1
    assert t.item(0, 0).text().startswith("▸")
    assert t.item(5, 0).text().startswith("▾")  # Caster back at index 5 after the removal
    assert t.item(6, 0).text() == _TUNING_TIPS["Caster"]

    # clicking the open setting closes it
    chart._toggle_row(5)
    assert t.rowCount() == base
    assert not any(t.item(r, 0).text().startswith("▾") for r in range(base))

    # clicks on the explanation row itself are a no-op
    chart._toggle_row(0)
    chart._toggle_row(1)
    assert t.rowCount() == base + 1

    # filtering closes the open explanation and hides non-matching settings
    chart.search.setText("diff")
    assert t.rowCount() == base
    visible = [r for r in range(t.rowCount()) if not t.isRowHidden(r)]
    assert visible == [17]
    chart.search.setText("")


def test_tuning_oil_guide(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.ui.tuning import TuningTab, _OIL_ROWS

    _ = QApplication.instance() or QApplication([])
    tab = TuningTab()
    assert tab.subtabs.tabText(1) == "Shock Oil"
    t = tab.oil.table
    assert t.rowCount() == len(_OIL_ROWS) == 10
    assert t.columnCount() == 2
    assert (t.item(4, 0).text(), t.item(4, 1).text()) == ("30", "350")


def test_tuning_gyro_guide(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.ui.tuning import TuningTab, _GYRO_ROWS, _GYRO_TIPS

    # every symptom row has a tip, and no tip is orphaned
    assert set(_GYRO_TIPS) == {r[0] for r in _GYRO_ROWS}

    _ = QApplication.instance() or QApplication([])
    tab = TuningTab()
    assert tab.subtabs.tabText(2) == "Gyro"
    t = tab.gyro.table
    assert t.rowCount() == len(_GYRO_ROWS) == 6
    assert t.item(0, 0).text() == "▸ Tail wags / oscillates on straights"
    assert t.item(0, 1).text() == "Lower gain"
    assert all(t.item(r, 0).toolTip() for r in range(t.rowCount()))

    # same accordion as the chassis chart: spanned explanation row, one open at a time
    tab.gyro._toggle_row(0)
    assert t.rowCount() == 7
    assert t.item(1, 0).text() == _GYRO_TIPS["Tail wags / oscillates on straights"]
    assert t.columnSpan(1, 0) == 2
    tab.gyro._toggle_row(0)
    assert t.rowCount() == 6


def test_tuning_log(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QTableWidget

    from app import garage
    from app.ui.tuning import TuningTab
    import app.ui.common

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    # scope QSettings to a temp INI: the active-car key must not touch the registry
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    tab = TuningTab()
    log = tab.mylog
    names = [tab.subtabs.tabText(i) for i in range(tab.subtabs.count())]
    assert names == ["Chassis", "Shock Oil", "Gyro", "My Log"]
    assert log.table.selectionBehavior() == QTableWidget.SelectionBehavior.SelectRows

    # no active car: entry controls disabled, hint shown
    # (isHidden, not isVisible — the tab itself is never shown in offscreen tests)
    assert not log.add_btn.isEnabled()
    assert not log.note.isEnabled()
    assert not log.hint.isHidden()

    # a car with a pre-existing Run entry, made the Workshop's active car
    car = garage.new_car("Drift Car")
    car["log"].append(garage.new_log_entry("Run", "pack 1"))
    garage.save_car(car)
    settings = app.ui.common._settings()
    settings.setValue(app.ui.common._ACTIVE_CAR_KEY, car["id"])
    settings.sync()
    log._reload()
    assert log.add_btn.isEnabled()
    assert log.hint.isHidden()
    assert log.table.rowCount() == 0  # the Run entry is not a tuning entry

    # add a tuning note -> shows in the table and lands on disk as kind="Tuning"
    log.note.setText("front springs softer → better turn-in")
    log._add()
    assert log.note.text() == ""  # input cleared for the next note
    assert log.table.rowCount() == 1
    assert log.table.item(0, 1).text() == "front springs softer → better turn-in"
    saved = garage.load_car(car["id"])
    assert [e["kind"] for e in saved["log"]].count("Tuning") == 1
    assert any(e["kind"] == "Run" for e in saved["log"])

    # delete removes the tuning entry from disk but keeps the Run entry
    log.table.setCurrentCell(0, 1)
    log._delete()
    assert log.table.rowCount() == 0
    saved = garage.load_car(car["id"])
    assert [e["kind"] for e in saved["log"]] == ["Run"]


def test_gear_tab_follows_active_car_and_preserves_tweaks(monkeypatch, tmp_path):
    # switching away and back (showEvent -> _load_active_car) must keep the user's
    # in-progress spinbox tweaks: only an actual active-car CHANGE re-seeds them
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.gear import GearTab
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    a = garage.new_car("Car A")
    a["gearing"]["pinion"] = 30
    garage.save_car(a)
    settings = app.ui.common._settings()
    settings.setValue(app.ui.common._ACTIVE_CAR_KEY, a["id"])
    settings.sync()

    tab = GearTab()
    assert tab.pinion.value() == 30  # seeded from the active car at construction
    assert tab.save_btn.isEnabled()

    tab.pinion.setValue(33)  # a what-if tweak
    tab._load_active_car()  # simulates returning to the sub-tab
    assert tab.pinion.value() == 33  # same active car: tweak survives, no re-seed

    # the active car deleted elsewhere falls back to no-car without error
    garage.delete_car(a["id"])
    tab._load_active_car()
    assert not tab.save_btn.isEnabled()
    assert not tab.save_preset_btn.isEnabled()


def test_gear_tab_solve_buttons_fill_pinion(monkeypatch, tmp_path):
    # both reverse-solve buttons must mirror the pure solver's whole-tooth answer into
    # the pinion spinbox, and the setValue must refresh the results row (the wiring the
    # pure-function tests in test_gearing.py cannot reach).
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage, gearing
    from app.ui.gear import GearTab
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    tab = GearTab()
    tab.spur.setValue(87)
    tab.internal_ratio.setValue(1.9)
    tab.tire.setValue(60.0)

    # Target FDR -> FDR-closest whole tooth (nonlinear: not merely target rounding)
    tab.target_fdr.setValue(6.0)
    tab._solve_pinion_fdr()
    expected = gearing.solve_pinion_for_fdr(target_fdr=6.0, spur=87, internal_ratio=1.9)
    assert tab.pinion.value() == expected
    # setValue fired _recompute, so the results row shows the achieved FDR for that pinion
    assert tab.fdr_out.text() == f"{gearing.final_drive_ratio(expected, 87, 1.9):.2f}"

    # the target-rollout button (also untested at the UI layer) drives the pinion too
    tab.target_rollout.setValue(28.0)
    tab._solve_pinion()
    assert tab.pinion.value() == gearing.solve_pinion_for_rollout(
        target_rollout_mm=28.0, spur=87, internal_ratio=1.9, tire_diameter_mm=60.0
    )


def test_gear_tab_force_reseed_after_restore(monkeypatch, tmp_path):
    # a garage "Restore all" rewrites the active car's data under the SAME id; the
    # id-equality guard suppresses a normal reload, so force=True must override it
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.gear import GearTab
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    car = garage.new_car("Car A")
    car["gearing"]["pinion"] = 30
    garage.save_car(car)
    settings = app.ui.common._settings()
    settings.setValue(app.ui.common._ACTIVE_CAR_KEY, car["id"])
    settings.sync()

    tab = GearTab()
    assert tab.pinion.value() == 30  # seeded from the active car

    # the restore overwrites the same-id car with different gearing on disk
    car["gearing"]["pinion"] = 15
    garage.save_car(car)
    tab._load_active_car()  # same id -> guard suppresses reload, stale value persists
    assert tab.pinion.value() == 30
    tab._load_active_car(force=True)  # the restore path forces the reseed
    assert tab.pinion.value() == 15


def test_settings_tab_toggles_persist_and_apply(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from app.ui.common import _DARK_MODE_KEY, _STARTUP_CHECK_KEY, _settings
    from app.ui.settings import SettingsTab

    app = QApplication.instance() or QApplication([])
    tab = SettingsTab()

    tab.dark_toggle.setChecked(True)  # fires _on_dark_toggled -> apply_theme + persist
    assert _settings().value(_DARK_MODE_KEY, type=bool) is True
    assert app.palette().color(QPalette.ColorRole.Window).lightness() < 128  # dark applied

    tab.dark_toggle.setChecked(False)  # back to light (leaves other tests in light too)
    assert _settings().value(_DARK_MODE_KEY, type=bool) is False
    assert app.palette().color(QPalette.ColorRole.Window).lightness() > 128

    tab.update_toggle.setChecked(False)  # fires _on_update_toggled -> persist
    assert _settings().value(_STARTUP_CHECK_KEY, type=bool) is False
    tab.update_toggle.setChecked(True)
    assert _settings().value(_STARTUP_CHECK_KEY, type=bool) is True


def test_workshop_active_car_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import garage
    from app.ui.workshop import WorkshopTab
    import app.ui.common

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    a = garage.save_car(garage.new_car("Car A"))
    garage.save_car(garage.new_car("Car B"))
    tab = WorkshopTab()

    # header pick -> Garage form follows, key persisted, gear save enabled
    tab.car_combo.setCurrentIndex(tab.car_combo.findData(a["id"]))
    assert tab.garage.name.text() == "Car A"
    assert app.ui.common._settings().value(app.ui.common._ACTIVE_CAR_KEY) == a["id"]
    assert tab.gear.save_btn.isEnabled()

    # a combo rebuild (showEvent / garage changes) keeps the active selection
    tab._refresh_combo()
    assert tab.car_combo.currentData() == a["id"]

    # Garage-side selection flows back into the header without looping
    b_id = next(c["id"] for c in garage.list_cars() if c["name"] == "Car B")
    tab.garage.open_car(b_id)  # silent path first: header must NOT move…
    assert tab.car_combo.currentData() == a["id"]
    tab.garage.car_selected.emit(b_id)  # …the user-action signal is what moves it
    assert tab.car_combo.currentData() == b_id

    # deleting the active car in the Garage falls back to "— no car —"
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    tab.garage._on_delete()
    assert tab.car_combo.currentData() is None
    assert (app.ui.common._settings().value(app.ui.common._ACTIVE_CAR_KEY) or "") == ""
    tab.gear._load_active_car()
    assert not tab.gear.save_btn.isEnabled()


def test_tools_tab_uninstall_and_action_enablement(monkeypatch, sandbox):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.tools import ToolsTab
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()  # constructed directly so the test runs off Windows too

    # before install, both install-only menu actions are disabled
    open_action, uninstall_action = tab._install_actions[0]
    assert not open_action.isEnabled() and not uninstall_action.isEnabled()

    installer.install(_tool())
    tab._refresh_row(0)
    assert open_action.isEnabled() and uninstall_action.isEnabled()
    assert (installer.TOOLS_DIR / "fake-tool").exists()

    # uninstall (confirmation auto-accepted) removes the install and refreshes the row
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
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
    from app.ui.tools import ToolsTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool(version="2.0")])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()  # constructed directly so the test runs off Windows too

    assert tab.update_summary.text() == ""  # nothing installed -> no badge

    # install an older version: the row reads "Update" and the badge counts it
    installer.install(_tool(version="1.0"))
    tab._refresh_row(0)
    tab._refresh_summary()
    assert tab.table.cellWidget(0, 4).text() == "Update"
    assert tab.update_summary.text() == "1 update available"


def test_tools_tab_install_flow_installs_and_reenables(monkeypatch, sandbox):
    # the Install click disables the button, dispatches a background download, and on
    # completion _install_finished re-enables the button and flips the row to Launch.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.tools import ToolsTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()  # constructed directly so the test runs off Windows too

    captured = {}
    monkeypatch.setattr(
        tab, "_run_download", lambda work, on_finished: captured.update(work=work, cb=on_finished)
    )
    tab._on_action(0)  # row 0 is not installed -> routes to _install
    assert not tab.table.cellWidget(0, 4).isEnabled()  # disabled while downloading
    assert "work" in captured  # a background download was dispatched

    captured["work"](lambda *a: None)  # run the download (sandbox fixture zip) + install
    captured["cb"](None)  # signal success back on the GUI thread
    assert installer.get_state("fake-tool") is not None
    assert tab.table.cellWidget(0, 4).isEnabled()
    assert tab.table.cellWidget(0, 4).text() == "Launch"


def test_tools_tab_install_finished_error_warns(monkeypatch, sandbox):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import catalog
    from app.ui.tools import ToolsTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()

    warned = {"n": 0}
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.update(n=warned["n"] + 1))
    tab.table.cellWidget(0, 4).setEnabled(False)  # _install disabled it before the thread
    tab._install_finished(0, "network boom")
    assert warned["n"] == 1  # the failure reaches the user, not a silent swallow
    assert tab.table.cellWidget(0, 4).isEnabled()  # button restored so they can retry


def test_tools_tab_action_launches_installed_tool(monkeypatch, sandbox):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app import catalog, launcher
    from app.ui.tools import ToolsTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()

    installer.install(_tool())
    tab._refresh_row(0)
    assert tab.table.cellWidget(0, 4).text() == "Launch"

    launched = {}
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda tool_id, exe_path, needs_admin=False: launched.update(
            id=tool_id, exe=exe_path, admin=needs_admin
        ),
    )
    tab._on_action(0)  # installed & current -> launch, not re-install
    assert launched["id"] == "fake-tool"
    assert launched["exe"].endswith("FakeTool.exe")
    assert launched["admin"] is False

    # a declined UAC prompt (OSError) surfaces as a warning dialog, not a traceback
    def boom(*a, **k):
        raise OSError("UAC declined")

    warned = {"n": 0}
    monkeypatch.setattr(launcher, "launch", boom)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.update(n=warned["n"] + 1))
    tab._on_action(0)
    assert warned["n"] == 1


def test_tools_tab_locate_existing_registers_and_refreshes(monkeypatch, sandbox, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

    from app import catalog
    from app.ui.tools import ToolsTab

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    tab = ToolsTab()

    exe = tmp_path / "external" / "Real.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ real")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(exe), ""))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("2.5", True))

    open_action, uninstall_action = tab._install_actions[0]
    assert not uninstall_action.isEnabled()  # nothing linked yet
    tab._locate_existing(0)

    state = installer.get_state("fake-tool")
    assert state["version"] == "2.5" and state["source"] == "existing"
    assert state["exe_path"] == str(exe)
    # v2.5 differs from the catalog's v1.0, so the row offers Update and the
    # install-only menu actions light up
    assert tab.table.cellWidget(0, 4).text() == "Update"
    assert uninstall_action.isEnabled()


def test_mainwindow_restores_clamped_tab_and_closeevent(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.window import MainWindow
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")

    # scope QSettings to a temp INI so nothing leaks to the registry across tests
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    seed = QSettings(str(ini), QSettings.Format.IniFormat)
    seed.setValue("tab", 99)  # larger than any possible tab count
    seed.sync()

    _ = QApplication.instance() or QApplication([])
    win = MainWindow()
    assert win.tabs.currentIndex() == win.tabs.count() - 1  # clamped, not 99

    win.close()  # closeEvent persists geometry + tab and must not raise
    written = QSettings(str(ini), QSettings.Format.IniFormat)
    assert int(written.value("tab")) == win.tabs.count() - 1


def test_gear_preset_action_preserves_computed_gearing(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.gear import GearTab
    from PySide6.QtWidgets import QInputDialog
    import app.ui.common

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    ini = tmp_path / "settings.ini"
    monkeypatch.setattr(
        app.ui.common,
        "QSettings",
        lambda *a, **k: QSettings(str(ini), QSettings.Format.IniFormat),
    )
    _ = QApplication.instance() or QApplication([])

    # a car whose computed gearing was filled by the calculator, set active
    car = garage.new_car("Computed")
    car["gearing"].update({"fdr": 7.5, "rollout_mm": 28.5, "top_speed_kmh": 42.1})
    garage.save_car(car)
    settings = app.ui.common._settings()
    settings.setValue(app.ui.common._ACTIVE_CAR_KEY, car["id"])
    settings.sync()
    tab = GearTab()

    # saving a preset must not null the computed gearing on disk...
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("carpet", True))
    tab._on_save_preset()

    reloaded = garage.load_car(car["id"])
    assert reloaded["gearing"]["fdr"] == 7.5
    assert reloaded["gearing"]["rollout_mm"] == 28.5
    assert reloaded["gearing"]["top_speed_kmh"] == 42.1
    # ...and the snapshot captures the computed values, not None
    assert reloaded["presets"][0]["gearing"]["fdr"] == 7.5
    # the dropdown now lists the preset after its placeholder row
    assert tab.preset_combo.count() == 2 and tab.preset_combo.itemText(1) == "carpet"


def test_garage_restore_refreshes_open_form(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import backup, catalog, garage
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    # save Alpha and open it, back it up, then make an unsaved local edit
    tab.name.setText("Alpha")
    tab.chassis.setText("Yokomo")
    tab._on_save()
    zip_path = backup.make_backup(tmp_path / "b.zip")
    tab.chassis.setText("stale")  # edit that would clobber the restore on next Save

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(zip_path), ""))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    tab._on_restore()

    # the form was re-filled from disk, not left showing the stale edit
    assert tab.chassis.text() == "Yokomo"


def test_garage_delete_requires_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog, garage
    from app.ui.garage_tab import GarageTab
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    tab = GarageTab()

    tab.name.setText("Alpha")
    tab._on_save()
    assert tab.current_id is not None

    # declining the confirmation leaves the car untouched
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    tab._on_delete()
    assert len(garage.list_cars()) == 1
    assert tab.current_id is not None

    # accepting it deletes the car and blanks the form
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    tab._on_delete()
    assert garage.list_cars() == []
    assert tab.name.text() == ""
    assert tab.current_id is None


def test_compare_dialog_opens_and_populates(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import garage
    from app.ui.common import _ACCENT
    from app.ui.garage_tab import _CompareDialog

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])
    alpha = garage.new_car("Alpha")
    alpha["chassis"] = "TC-01"  # Beta lacks this, so the Chassis row differs
    a = garage.save_car(alpha)
    garage.save_car(garage.new_car("Beta"))

    # constructing must fully render the table (no _render before self.table exists)
    dlg = _CompareDialog(garage.list_cars(), a["id"])
    assert dlg.table.rowCount() > 0
    assert dlg.combo_a.currentData() != dlg.combo_b.currentData()  # two distinct cars

    # a differing row highlights with the app accent, not the old hardcoded yellow
    rows = [dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())]
    chassis_row = rows.index("Chassis")
    item = dlg.table.item(chassis_row, 1)
    assert item.background().color().name() == _ACCENT
    assert item.foreground().color().name() == "#ffffff"


def test_manuals_tab_shows_homepage_only_info_device(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import catalog
    from app.ui.manuals import ManualsTab

    # an info-only device with a homepage but no links: reachable via a Manuals row
    device = {
        "id": "d", "name": "Gyro X", "vendor": "V", "version": "n/a",
        "category": "gyro", "homepage": "https://vendor.example",
    }
    monkeypatch.setattr(catalog, "load_catalog", lambda: [device])
    _ = QApplication.instance() or QApplication([])
    tab = ManualsTab()
    assert tab.table.rowCount() == 1


def test_find_exe_rejects_traversal(tmp_path):
    from app import installer

    (tmp_path / "evil.exe").write_bytes(b"MZ")
    root = tmp_path / "tool"
    root.mkdir()
    real = root / "servo.exe"
    real.write_bytes(b"MZ")

    # a hostile catalog hint must not escape the tool dir: the guard ignores it
    # and the single-candidate scan finds the real exe instead
    assert installer._find_exe(root, "../evil.exe") == real


def test_install_exe_download_rejects_traversal(sandbox, monkeypatch):
    # archive:"exe" with a hostile setup_relative_path must not write outside the tool dir
    monkeypatch.setattr(
        installer, "_download", lambda url, d, progress: d.write_bytes(b"MZ payload")
    )
    tool = _tool()
    tool["download"] = {"url": "https://x/app.exe", "archive": "exe", "sha256": None}
    # the hostile setup hint is rejected; the copy falls back to the safe default name
    # ("tool.exe" - NOT "installer.exe", which _find_exe's skip-list would drop), which
    # then resolves end-to-end
    tool["install"] = {"setup_relative_path": "../evil.exe"}
    exe = installer.install(tool)

    dest = installer.TOOLS_DIR / "fake-tool"
    assert exe == dest / "tool.exe"  # fell back to the safe default
    assert exe.exists()
    assert not (installer.TOOLS_DIR / "evil.exe").exists()  # nothing escaped the tool dir


def test_load_catalog_rejects_malformed_remote(monkeypatch, tmp_path):
    from app import catalog

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"not": "a tool list"}  # valid JSON, wrong shape

    monkeypatch.setattr(catalog.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog, "CACHE_FILE", tmp_path / "catalog.json")

    tools = catalog.load_catalog()

    # fell through to the bundled catalog, and did not cache the bad payload
    assert isinstance(tools, list) and tools
    assert all("id" in t for t in tools)
    assert not (tmp_path / "catalog.json").exists()


def test_load_catalog_skips_corrupt_cache(monkeypatch, tmp_path):
    from app import catalog

    def offline(*a, **k):
        raise catalog.requests.RequestException("no network")

    monkeypatch.setattr(catalog.requests, "get", offline)
    cache = tmp_path / "catalog.json"
    cache.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setattr(catalog, "CACHE_FILE", cache)

    tools = catalog.load_catalog()  # must not raise on the bad cache
    assert isinstance(tools, list) and all("id" in t for t in tools)


def test_load_catalog_rejects_path_traversal_id(monkeypatch, tmp_path):
    from app import catalog

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            # id is well-formed JSON but not a slug: it becomes a filesystem path
            # component in installer.py, so "../.." would point outside TOOLS_DIR
            return [{"id": "../..", "name": "x"}]

    monkeypatch.setattr(catalog.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(catalog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog, "CACHE_FILE", tmp_path / "catalog.json")

    tools = catalog.load_catalog()

    # fell through to the bundled catalog, and did not cache the hostile payload
    assert isinstance(tools, list) and tools
    assert all("id" in t for t in tools)
    assert not (tmp_path / "catalog.json").exists()


def test_valid_rejects_path_traversal_id():
    from app import catalog

    assert catalog._valid([{"id": "../..", "name": "x"}]) is False
    assert catalog._valid([{"id": "agfrc-servo-programmer", "name": "x"}]) is True
    # non-string id/name (corrupt or hostile catalog) must not crash the shape check
    assert catalog._valid([{"id": 5, "name": "x"}]) is False
    assert catalog._valid([{"id": "ok-id", "name": None}]) is False
