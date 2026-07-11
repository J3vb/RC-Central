"""Hub self-update from GitHub Releases: fetch on start, swap the exe on exit.

Only active in frozen (PyInstaller) builds; running from source is a no-op
unless ``force=True`` — the Log tab's "Check for updates now" button passes that
so the full check+download path runs (and logs) during development too.
"""

import logging
import shutil
import sys
from pathlib import Path

import requests

from app import __version__
from app.installer import DATA_DIR

log = logging.getLogger(__name__)

REPO = "J3vb/RC-Central"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PENDING = DATA_DIR / "update-pending.exe"


def _newer(tag: str, current: str) -> bool:
    try:
        def parse(v):
            return tuple(int(x) for x in v.lstrip("vV").split("."))

        return parse(tag) > parse(current)
    except ValueError:
        return False


def fetch_update(force: bool = False) -> bool:
    """Download a newer release exe to PENDING if one exists. Never raises.

    Startup passes ``force=False`` so source runs stay a no-op; the Log tab
    passes ``force=True`` to exercise the real path from source. Every step logs,
    so a failed update is visible in rc-central.log instead of vanishing.
    """
    frozen = bool(getattr(sys, "frozen", False))
    log.info("update check: current=v%s frozen=%s force=%s", __version__, frozen, force)
    if not frozen and not force:
        log.info("running from source and force=False; skipping update check")
        return False
    try:
        log.info("querying latest release: %s", API_URL)
        resp = requests.get(API_URL, timeout=10)
        log.info("GitHub responded HTTP %s", resp.status_code)
        resp.raise_for_status()  # 404 on draft/prerelease-only, 403 on rate limit
        rel = resp.json()
        tag = rel.get("tag_name", "")
        assets = rel.get("assets", [])
        log.info("latest release tag=%r", tag)
        log.debug("release assets: %s", [a.get("name") for a in assets])

        if not _newer(tag, __version__):
            log.info(
                "no newer version available (latest=%r, current=v%s)",
                tag,
                __version__,
            )
            return False
        log.info("newer version available: %r > v%s", tag, __version__)

        exe_asset = next((a for a in assets if a["name"].endswith(".exe")), None)
        if exe_asset is None:
            log.warning(
                "release %r has no .exe asset (assets: %s); cannot update",
                tag,
                [a.get("name") for a in assets],
            )
            return False

        url = exe_asset["browser_download_url"]
        expected_size = exe_asset.get("size")
        log.info(
            "downloading update asset %r (%s bytes) from %s",
            exe_asset["name"],
            expected_size,
            url,
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PENDING.with_suffix(".part")
        written = 0
        with requests.get(url, stream=True, timeout=30) as dl:
            dl.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in dl.iter_content(chunk_size=65536):
                    f.write(chunk)
                    written += len(chunk)

        # Never stage a truncated or non-exe payload. The background download can
        # be cut short when the window closes, and a bad swap on exit bricks the
        # app — which then can't self-update again. Verify before promoting to
        # PENDING: the release API gives the exact size, and every Windows exe
        # starts with the "MZ" magic.
        if expected_size and written != expected_size:
            log.warning(
                "update download is incomplete (%d of %d bytes); discarding",
                written,
                expected_size,
            )
            tmp.unlink(missing_ok=True)
            return False
        with open(tmp, "rb") as f:
            magic = f.read(2)
        if magic != b"MZ":
            log.warning(
                "update download is not a Windows executable (magic=%r); discarding",
                magic,
            )
            tmp.unlink(missing_ok=True)
            return False

        tmp.replace(PENDING)
        log.info("update downloaded and verified (%d bytes) to %s", written, PENDING)
        return True
    except requests.RequestException as e:
        # network down, HTTP error, or non-JSON body: expected, log without a trace
        log.warning("update check failed: %s", e)
        return False
    except Exception:
        log.exception("update check failed unexpectedly")
        return False  # an update failure must never break the app


def apply_pending() -> None:
    """Swap the downloaded update into place. Call after the event loop exits.

    Crash-safe: the running exe is moved aside first (Windows allows renaming a
    running exe, not deleting it), and if putting the new one in place fails the
    original is restored — the app must never be left without a launchable exe.
    """
    if not (getattr(sys, "frozen", False) and PENDING.exists()):
        return
    exe = Path(sys.executable)
    old = exe.with_suffix(".old.exe")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        log.warning("could not clear stale %s before swap", old.name, exc_info=True)

    try:
        exe.rename(old)
    except OSError:
        log.exception("could not move the running exe aside; update not applied")
        return

    try:
        shutil.move(str(PENDING), str(exe))
        log.info("applied pending update: swapped in %s", PENDING.name)
    except OSError:
        log.exception("could not move the update into place; rolling back")
        try:
            if exe.exists():
                exe.unlink()  # drop any partial copy a cross-volume move left
            old.rename(exe)  # restore the previous exe so the app still launches
            log.info("rolled back to the previous exe after a failed update")
        except OSError:
            log.exception(
                "rollback failed; the previous exe is at %s and the update at %s",
                old,
                PENDING,
            )


def cleanup() -> None:
    """Remove the leftover .old.exe from a previous update. Call on startup."""
    if getattr(sys, "frozen", False):
        old = Path(sys.executable).with_suffix(".old.exe")
        try:
            old.unlink(missing_ok=True)
            log.info("startup cleanup: cleared leftover %s (if any)", old.name)
        except OSError:
            # e.g. old instance still exiting; retry next start
            log.warning("startup cleanup: could not remove %s", old, exc_info=True)
