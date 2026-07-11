import logging
import sys

import pytest
import requests

from app import updater

# The release asset name and executable magic the updater expects on THIS
# platform (PE "MZ" on Windows, ELF "\x7fELF" on Linux). Deriving the fixtures
# from these keeps the tests meaningful on whichever OS CI runs them.
ASSET_NAME, MAGIC = updater._platform_asset()


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
    monkeypatch.setattr(updater, "PENDING", tmp_path / updater.PENDING.name)
    monkeypatch.setattr(updater, "_staged_version", None)  # avoid cross-test leakage
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
    assert not updater.PENDING.exists()


def test_newer_with_matching_asset(sandbox, monkeypatch, caplog):
    payload = MAGIC + b"fake exe body"
    api = FakeResp(
        json_data={
            "tag_name": "v99.0.0",
            "assets": [
                {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
                {
                    "name": ASSET_NAME,
                    "browser_download_url": f"https://x/{ASSET_NAME}",
                    "size": len(payload),
                },
            ],
        }
    )
    download = FakeResp(content=payload)
    _patch_requests(monkeypatch, api, download)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is True
    assert updater.PENDING.exists() and updater.PENDING.read_bytes() == payload
    assert updater.staged_version() == "v99.0.0"  # exposed for the UI banner
    assert "newer version available" in caplog.text
    assert "downloaded and verified" in caplog.text


def _matching_asset_release(*, size):
    return FakeResp(
        json_data={
            "tag_name": "v99.0.0",
            "assets": [
                {
                    "name": ASSET_NAME,
                    "browser_download_url": f"https://x/{ASSET_NAME}",
                    "size": size,
                }
            ],
        }
    )


def test_download_incomplete_is_discarded(sandbox, monkeypatch, caplog):
    # A truncated download (fewer bytes than the API's declared size) must not stage.
    payload = MAGIC + b"short"
    api = _matching_asset_release(size=len(payload) + 1000)
    _patch_requests(monkeypatch, api, FakeResp(content=payload))

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "incomplete" in caplog.text
    assert not updater.PENDING.exists()


def test_download_wrong_magic_is_discarded(sandbox, monkeypatch, caplog):
    # A 200 that is actually an HTML/error body (wrong magic) must not stage.
    payload = b"<html>not found</html>"
    api = _matching_asset_release(size=len(payload))
    _patch_requests(monkeypatch, api, FakeResp(content=payload))

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is False
    assert "not a valid executable" in caplog.text
    assert not updater.PENDING.exists()


def _frozen_exe(monkeypatch, tmp_path, pending_bytes):
    exe = tmp_path / ("RCCentral.exe" if sys.platform == "win32" else "RCCentral")
    exe.write_bytes(b"old-running-binary")
    pending = tmp_path / updater.PENDING.name
    pending.write_bytes(pending_bytes)
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(exe))
    monkeypatch.setattr(updater, "PENDING", pending)
    return exe, pending


def test_apply_pending_swaps_in_update(monkeypatch, tmp_path):
    exe, pending = _frozen_exe(monkeypatch, tmp_path, b"new-version")

    updater.apply_pending()

    assert exe.read_bytes() == b"new-version"
    assert updater._sidelined(exe).read_bytes() == b"old-running-binary"
    assert not pending.exists()


def test_apply_pending_rolls_back_on_failed_swap(monkeypatch, tmp_path, caplog):
    exe, _pending = _frozen_exe(monkeypatch, tmp_path, b"new-version")

    def boom(src, dst):
        raise OSError("locked by antivirus")

    monkeypatch.setattr(updater.shutil, "move", boom)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        updater.apply_pending()

    # the original binary must be restored so the app still launches
    assert exe.exists() and exe.read_bytes() == b"old-running-binary"
    assert "rolled back" in caplog.text


def test_newer_without_matching_asset(sandbox, monkeypatch, caplog):
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
    assert "no asset named" in caplog.text
    assert not updater.PENDING.exists()


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
