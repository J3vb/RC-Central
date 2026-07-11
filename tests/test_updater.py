import logging

import pytest
import requests

from app import updater


class FakeResp:
    """Stand-in for a requests.Response covering both the API call and download."""

    def __init__(
        self, *, json_data=None, status_code=200, content=b"", raise_exc=None, json_exc=None
    ):
        self._json = json_data
        self.status_code = status_code
        self._content = content
        self._raise_exc = raise_exc
        self._json_exc = json_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json

    def iter_content(self, chunk_size=1):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_requests(monkeypatch, api_resp, download_resp=None):
    def fake_get(url, **kwargs):
        return download_resp if kwargs.get("stream") else api_resp

    monkeypatch.setattr(updater.requests, "get", fake_get)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """Redirect the pending-update paths into tmp so nothing touches real dirs."""
    monkeypatch.setattr(updater, "DATA_DIR", tmp_path)
    monkeypatch.setattr(updater, "PENDING", tmp_path / "update-pending.exe")
    return tmp_path


def test_source_run_is_noop(caplog):
    # No force, not frozen: the update check should short-circuit and say so.
    with caplog.at_level(logging.INFO, logger="app.updater"):
        assert updater.fetch_update() is False
    assert "skipping update check" in caplog.text


def test_no_newer_version(sandbox, monkeypatch, caplog):
    api = FakeResp(json_data={"tag_name": "v0.0.1", "assets": []})
    _patch_requests(monkeypatch, api)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "no newer version available" in caplog.text
    assert not (sandbox / "update-pending.exe").exists()


def test_newer_with_exe_asset(sandbox, monkeypatch, caplog):
    api = FakeResp(
        json_data={
            "tag_name": "v99.0.0",
            "assets": [
                {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
                {"name": "RCCentral.exe", "browser_download_url": "https://x/RCCentral.exe"},
            ],
        }
    )
    download = FakeResp(content=b"NEWEXE")
    _patch_requests(monkeypatch, api, download)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is True
    pending = sandbox / "update-pending.exe"
    assert pending.exists() and pending.read_bytes() == b"NEWEXE"
    assert "newer version available" in caplog.text
    assert "update downloaded" in caplog.text


def test_newer_without_exe_asset(sandbox, monkeypatch, caplog):
    api = FakeResp(
        json_data={
            "tag_name": "v99.0.0",
            "assets": [{"name": "notes.txt", "browser_download_url": "https://x/notes.txt"}],
        }
    )
    _patch_requests(monkeypatch, api)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "no .exe asset" in caplog.text
    assert not (sandbox / "update-pending.exe").exists()


def test_network_error(sandbox, monkeypatch, caplog):
    def boom(url, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(updater.requests, "get", boom)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "update check failed" in caplog.text


def test_http_error_status(sandbox, monkeypatch, caplog):
    api = FakeResp(status_code=404, raise_exc=requests.HTTPError("404 Not Found"))
    _patch_requests(monkeypatch, api)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "HTTP 404" in caplog.text  # status was logged before it raised
    assert "update check failed" in caplog.text


def test_non_json_response(sandbox, monkeypatch, caplog):
    # A 200 with a non-JSON body: resp.json() blows up and must be logged, not swallowed.
    api = FakeResp(status_code=200, json_exc=ValueError("Expecting value"))
    _patch_requests(monkeypatch, api)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "failed" in caplog.text.lower()
