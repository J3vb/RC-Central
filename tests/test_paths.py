"""data_dir()'s per-OS branches, exercised directly (they were only ever hit
implicitly, and only for whichever OS the test runner happened to be on)."""

import sys
from pathlib import Path

from app import paths


def test_data_dir_windows_uses_localappdata(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    assert paths.data_dir() == Path(r"C:\Users\x\AppData\Local") / "RCCentral"


def test_data_dir_windows_falls_back_when_localappdata_unset(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert paths.data_dir() == Path.home() / "AppData" / "Local" / "RCCentral"


def test_data_dir_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.data_dir() == Path.home() / "Library" / "Application Support" / "RCCentral"


def test_data_dir_linux_uses_xdg(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/x/.xdg")
    assert paths.data_dir() == Path("/home/x/.xdg") / "RCCentral"


def test_data_dir_linux_falls_back_when_xdg_unset(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.data_dir() == Path.home() / ".local" / "share" / "RCCentral"
