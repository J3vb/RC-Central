import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import validate_catalog  # noqa: E402


class FakeResp:
    def __init__(self, status_code=200, content_type="application/octet-stream"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_get(monkeypatch, side_effect):
    """side_effect: a list of FakeResp/exceptions consumed one per call."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        item = side_effect[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(validate_catalog.requests, "get", fake_get)
    return calls


def test_reachable_file_passes(monkeypatch):
    _patch_get(monkeypatch, [FakeResp()])
    assert validate_catalog._check_url("https://x/file.zip") is None


def test_http_error_fails_without_retry(monkeypatch):
    # a definitive 404 must not be retried - one call, immediate failure
    calls = _patch_get(monkeypatch, [FakeResp(status_code=404), FakeResp()])
    assert "HTTP 404" in validate_catalog._check_url("https://x/gone.zip")
    assert calls["n"] == 1


def test_html_page_is_link_rot(monkeypatch):
    # dead vendor links serve an HTML "not found" page with HTTP 200
    _patch_get(monkeypatch, [FakeResp(content_type="text/html; charset=utf-8")])
    assert "not a file" in validate_catalog._check_url("https://x/moved")


def test_transient_error_is_retried(monkeypatch):
    # first attempt times out, second succeeds -> reachable, no failure reported
    calls = _patch_get(monkeypatch, [requests.ConnectTimeout("blip"), FakeResp()])
    assert validate_catalog._check_url("https://x/file.zip") is None
    assert calls["n"] == 2


def test_persistent_network_error_fails_after_attempts(monkeypatch):
    err = requests.ConnectionError("no route")
    calls = _patch_get(monkeypatch, [err, err, err])
    msg = validate_catalog._check_url("https://x/file.zip", attempts=3)
    assert "after 3 attempts" in msg
    assert calls["n"] == 3
