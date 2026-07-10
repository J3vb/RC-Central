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

    monkeypatch.setattr(catalog, "load_catalog", lambda: [_tool()])
    _ = QApplication.instance() or QApplication([])
    win = app_main.MainWindow()
    assert win.table.rowCount() == 1
    assert win.table.cellWidget(0, 4).text() == "Install"
