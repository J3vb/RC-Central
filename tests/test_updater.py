import base64
import hashlib
import logging
import sys

import pytest
import requests
from nacl.signing import SigningKey

from app import updater

# The release asset name and executable magic the updater expects on THIS
# platform (PE "MZ" on Windows, ELF "\x7fELF" on Linux). Deriving the fixtures
# from these keeps the tests meaningful on whichever OS CI runs them.
ASSET_NAME, MAGIC = updater._platform_asset()

# fetch_update now requires a valid Ed25519 signature, so the fixtures sign their
# payloads with a throwaway key and pin the matching public key onto the updater.
_SIGNING_KEY = SigningKey.generate()
_PUBKEY_B64 = base64.b64encode(bytes(_SIGNING_KEY.verify_key)).decode()
_SIGNED_NAME = "RCCentral-windows-x64.exe"


def _sign(payload: bytes) -> bytes:
    return _SIGNING_KEY.sign(payload).signature


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

    @property
    def content(self):
        return self._content

    @property
    def text(self):
        return self._content.decode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_requests(monkeypatch, api_resp, download_resp=None):
    def fake_get(url, **kwargs):
        return download_resp if kwargs.get("stream") else api_resp

    monkeypatch.setattr(updater.requests, "get", fake_get)


def _patch_signed_flow(monkeypatch, tmp_path, payload, *, signature=..., sha_line=...):
    """Wire a fully-signed release (exe + .sha256 + .sig assets, URL-routed responses)
    with the test public key pinned. ``signature`` / ``sha_line`` default to correct
    values; pass an override to forge them, or None to omit that sidecar asset."""
    monkeypatch.setattr(updater, "_platform_asset", lambda: (_SIGNED_NAME, b"MZ"))
    monkeypatch.setattr(updater, "_UPDATE_PUBLIC_KEY", _PUBKEY_B64)
    if signature is ...:
        signature = _sign(payload)
    if sha_line is ...:
        sha_line = hashlib.sha256(payload).hexdigest().encode() + b"  " + _SIGNED_NAME.encode() + b"\n"

    assets = [{"name": _SIGNED_NAME, "browser_download_url": "https://dl/exe", "size": len(payload)}]
    responses = {"https://dl/exe": FakeResp(content=payload)}
    if sha_line is not None:
        assets.append({"name": _SIGNED_NAME + ".sha256", "browser_download_url": "https://dl/sha"})
        responses["https://dl/sha"] = FakeResp(content=sha_line)
    if signature is not None:
        assets.append({"name": _SIGNED_NAME + ".sig", "browser_download_url": "https://dl/sig"})
        responses["https://dl/sig"] = FakeResp(content=signature)
    api = FakeResp(json_data={"tag_name": "v99.0.0", "assets": assets})
    monkeypatch.setattr(updater.requests, "get", lambda url, **kw: responses.get(url, api))

    pending = tmp_path / updater.PENDING.name
    monkeypatch.setattr(updater, "DATA_DIR", tmp_path)
    monkeypatch.setattr(updater, "PENDING", pending)
    monkeypatch.setattr(updater, "_staged_version", None)
    return pending


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


def test_up_to_date_sets_last_check_current(sandbox, monkeypatch):
    # reached GitHub and the latest tag equals the running version: not newer (False),
    # but last_check_current() must report True so a manual check can say "up to date".
    api = FakeResp(json_data={"tag_name": f"v{updater.__version__}", "assets": []})
    _patch_requests(monkeypatch, api)

    assert updater.fetch_update(force=True) is False
    assert updater.last_check_current() is True


def test_offline_leaves_last_check_current_false(sandbox, monkeypatch):
    # a failed check (offline) also returns False, but must NOT read as "up to date"
    def boom(url, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(updater.requests, "get", boom)

    assert updater.fetch_update(force=True) is False
    assert updater.last_check_current() is False


def test_newer_with_matching_asset(monkeypatch, tmp_path, caplog):
    payload = b"MZ" + b"fake exe body"
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        result = updater.fetch_update(force=True)

    assert result is True
    assert pending.exists() and pending.read_bytes() == payload
    assert updater.staged_version() == "v99.0.0"  # exposed for the UI banner
    assert "newer version available" in caplog.text
    assert "signature verified" in caplog.text
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


def test_relaunch_resets_pyinstaller_environment(monkeypatch):
    # The self-update relaunch MUST set PYINSTALLER_RESET_ENVIRONMENT=1. Without it,
    # PyInstaller 6.x treats the child as a worker that reuses THIS process's _MEI temp
    # dir, which the exiting parent then deletes -> the relaunched app loses
    # certifi/cacert.pem (breaking HTTPS/PDF downloads) and warns "failed to remove _MEI".
    captured = {}
    monkeypatch.setattr(
        updater.subprocess, "Popen",
        lambda argv, env=None, **k: captured.update(argv=argv, env=env),
    )
    monkeypatch.setenv("PATH", "sentinel-path")  # unrelated env must carry through

    updater.relaunch()

    assert captured["argv"] == [sys.executable]
    assert captured["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert captured["env"]["PATH"] == "sentinel-path"


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


def test_fetch_update_rejects_sha256_mismatch(monkeypatch, tmp_path):
    payload = b"MZ" + b"x" * 64
    wrong = b"0" * 64 + b"  " + _SIGNED_NAME.encode() + b"\n"
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload, sha_line=wrong)

    assert updater.fetch_update(force=True) is False  # fails at the sha gate, before sig
    assert not pending.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_fetch_update_rejects_missing_signature(monkeypatch, tmp_path, caplog):
    # fail closed: a newer release with no .sig is refused, never staged unsigned
    payload = b"MZ" + b"x" * 64
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload, signature=None)

    with caplog.at_level(logging.INFO, logger="app.updater"):
        assert updater.fetch_update(force=True) is False
    assert not pending.exists()
    assert "refusing to stage an unsigned update" in caplog.text


def test_fetch_update_rejects_tampered_binary(monkeypatch, tmp_path, caplog):
    # the exact review threat: the attacker swaps the binary AND recomputes its sha256,
    # but can't forge the signature - the delivered bytes fail against the real one
    real = b"MZ" + b"the genuine build"
    evil = b"MZ" + b"trojaned build!!"
    pending = _patch_signed_flow(monkeypatch, tmp_path, evil, signature=_sign(real))

    with caplog.at_level(logging.INFO, logger="app.updater"):
        assert updater.fetch_update(force=True) is False
    assert not pending.exists()
    assert "signature verification failed" in caplog.text


def test_fetch_update_rejects_bad_signature(monkeypatch, tmp_path):
    payload = b"MZ" + b"x" * 64
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload, signature=b"not a real signature")

    assert updater.fetch_update(force=True) is False
    assert not pending.exists()


def test_fetch_update_rejects_signature_from_wrong_key(monkeypatch, tmp_path):
    # a signature from a DIFFERENT key (a forger's) must not verify against the pin
    payload = b"MZ" + b"x" * 64
    forged = SigningKey.generate().sign(payload).signature
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload, signature=forged)

    assert updater.fetch_update(force=True) is False
    assert not pending.exists()


def test_fetch_update_accepts_signed_release(monkeypatch, tmp_path):
    # the good path: correct size, magic, sha256, and a genuine signature -> staged
    payload = b"MZ" + b"x" * 64
    pending = _patch_signed_flow(monkeypatch, tmp_path, payload)

    assert updater.fetch_update(force=True) is True
    assert pending.read_bytes() == payload


def test_sign_release_produces_verifiable_signature(monkeypatch, tmp_path):
    from pathlib import Path as _Path

    from nacl.signing import VerifyKey

    sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))
    import sign_release

    key = SigningKey.generate()
    binary = tmp_path / "RCCentral.exe"
    binary.write_bytes(b"MZ hello world")
    monkeypatch.setenv("UPDATE_SIGNING_KEY", base64.b64encode(bytes(key)).decode())
    monkeypatch.setattr(sys, "argv", ["sign_release.py", str(binary)])

    assert sign_release.main() == 0
    sig = (tmp_path / "RCCentral.exe.sig").read_bytes()
    VerifyKey(bytes(key.verify_key)).verify(binary.read_bytes(), sig)  # raises if bad
