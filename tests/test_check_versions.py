import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_versions  # noqa: E402


class FakeResp:
    def __init__(self, text="", raise_exc=None):
        self.text = text
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


def _tool(version="1.0.0", with_check=True):
    tool = {"id": "demo", "name": "Demo Tool", "version": version, "homepage": "https://vendor/x"}
    if with_check:
        tool["version_check"] = {"url": "https://vendor/x", "pattern": r"V(\d+\.\d+\.\d+)"}
    return tool


def _patch_get(monkeypatch, resp):
    monkeypatch.setattr(check_versions.requests, "get", lambda url, **kw: resp)


def test_newer_upstream_is_reported(monkeypatch):
    _patch_get(monkeypatch, FakeResp(text="Download Demo V1.2.0 now"))
    findings = check_versions.check_tools([_tool(version="1.0.0")])
    assert len(findings) == 1
    assert findings[0]["id"] == "demo"
    assert findings[0]["current"] == "1.0.0"
    assert findings[0]["latest"] == "1.2.0"


def test_equal_version_is_not_reported(monkeypatch):
    _patch_get(monkeypatch, FakeResp(text="Demo V1.0.0"))
    assert check_versions.check_tools([_tool(version="1.0.0")]) == []


def test_older_upstream_is_not_reported(monkeypatch):
    _patch_get(monkeypatch, FakeResp(text="Demo V0.9.0"))
    assert check_versions.check_tools([_tool(version="1.0.0")]) == []


def test_tool_without_version_check_is_skipped(monkeypatch):
    # get should never be called; if it were, this would blow up loudly
    def boom(*a, **k):
        raise AssertionError("should not fetch a tool with no version_check")

    monkeypatch.setattr(check_versions.requests, "get", boom)
    assert check_versions.check_tools([_tool(with_check=False)]) == []


def test_network_error_is_tolerated(monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(check_versions.requests, "get", boom)
    # a flaky vendor page must not raise or produce a false positive
    assert check_versions.check_tools([_tool()]) == []


def test_pattern_no_match_is_tolerated(monkeypatch):
    _patch_get(monkeypatch, FakeResp(text="the page markup changed and has no version"))
    assert check_versions.check_tools([_tool()]) == []


def test_json_output_written(monkeypatch, tmp_path):
    # stub the probing so main() only loads the real manifests (no network) and
    # writes whatever findings it's handed to the --json path
    monkeypatch.setattr(
        check_versions, "check_tools", lambda tools: [{"id": "demo", "latest": "2.0.0"}]
    )
    out = tmp_path / "findings.json"
    monkeypatch.setattr(sys, "argv", ["check_versions.py", "--json", str(out)])
    assert check_versions.main() == 0
    assert out.exists()
    assert '"id": "demo"' in out.read_text(encoding="utf-8")
