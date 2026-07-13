"""Hub self-update from GitHub Releases: fetch on start, swap the binary on exit.

Only active in frozen (PyInstaller) builds; running from source is a no-op
unless ``force=True`` — the Log tab's "Check for updates now" button passes that
so the full check+download path runs (and logs) during development too.

Releases carry one asset per platform (see ``.github/workflows/build.yml``), so
the updater selects the asset matching the running OS/arch, validates it against
that platform's executable magic, and swaps it into place on exit.
"""

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import requests

from app import __version__
from app.installer import DATA_DIR
from app.versions import is_newer

log = logging.getLogger(__name__)

REPO = "J3vb/RC-Central"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def _arch() -> str:
    """Normalize platform.machine() to the tag used in release asset names."""
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x64"
    if m in ("arm64", "aarch64"):
        return "arm64"
    return m


def _platform_asset() -> tuple[str, bytes]:
    """(release asset name, executable magic bytes) for the running platform.

    Returns ``("", b"")`` on platforms we don't publish a build for (e.g. macOS),
    which disables self-update there rather than grabbing a wrong-OS binary.
    """
    if sys.platform == "win32":
        return f"RCCentral-windows-{_arch()}.exe", b"MZ"  # PE
    if sys.platform.startswith("linux"):
        return f"RCCentral-linux-{_arch()}", b"\x7fELF"  # ELF
    return "", b""


# Downloaded-but-not-yet-applied build. The name mirrors the running binary's
# shape (a suffixless ELF on Linux, an .exe on Windows) purely for clarity.
PENDING = DATA_DIR / ("update-pending.exe" if sys.platform == "win32" else "update-pending")

# Version tag staged in PENDING this session, so the UI can name it in the
# "update ready" banner. Set by fetch_update() on a successful stage.
_staged_version: str | None = None


def staged_version() -> str | None:
    """The version tag of the update staged in PENDING this session, if any."""
    return _staged_version


def _sidelined(exe: Path) -> Path:
    """Where the running binary is moved so the update can take its place.

    Windows renames the running ``.exe`` aside as ``.old.exe`` (it allows
    renaming a running exe, not deleting it); the Linux binary has no suffix, so
    ``.old`` is appended to the whole name.
    """
    if sys.platform == "win32":
        return exe.with_suffix(".old.exe")
    return exe.with_name(exe.name + ".old")


def _newer(tag: str, current: str) -> bool:
    return is_newer(tag, current)


def fetch_update(force: bool = False) -> bool:
    """Download a newer release build to PENDING if one exists. Never raises.

    Startup passes ``force=False`` so source runs stay a no-op; the Log tab
    passes ``force=True`` to exercise the real path from source. Every step logs,
    so a failed update is visible in rc-central.log instead of vanishing.
    """
    frozen = bool(getattr(sys, "frozen", False))
    log.info("update check: current=v%s frozen=%s force=%s", __version__, frozen, force)
    if not frozen and not force:
        log.info("running from source and force=False; skipping update check")
        return False

    asset_name, magic = _platform_asset()
    if not asset_name:
        log.info("no self-update build published for this platform (%s)", sys.platform)
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

        exe_asset = next((a for a in assets if a["name"] == asset_name), None)
        if exe_asset is None:
            log.warning(
                "release %r has no asset named %r (assets: %s); cannot update",
                tag,
                asset_name,
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

        # Never stage a truncated or wrong-kind payload. The background download
        # can be cut short when the window closes, and a bad swap on exit bricks
        # the app — which then can't self-update again. Verify before promoting to
        # PENDING: the release API gives the exact size, and each platform's
        # executable starts with a known magic (PE "MZ" / ELF "\x7fELF").
        if expected_size and written != expected_size:
            log.warning(
                "update download is incomplete (%d of %d bytes); discarding",
                written,
                expected_size,
            )
            tmp.unlink(missing_ok=True)
            return False
        with open(tmp, "rb") as f:
            head = f.read(len(magic))
        if head != magic:
            log.warning(
                "update download is not a valid executable (magic=%r); discarding",
                head,
            )
            tmp.unlink(missing_ok=True)
            return False

        tmp.replace(PENDING)
        global _staged_version
        _staged_version = tag
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

    Recovers from a failed swap: the running binary is moved aside first (both
    Windows and Linux allow renaming a running executable), and if putting the
    new one in place raises (e.g. an AV lock) the original is restored. A hard
    crash or power loss inside the move window is not covered — the previous
    binary then survives as the sidelined .old copy and can be restored by hand.
    """
    if not (getattr(sys, "frozen", False) and PENDING.exists()):
        return
    exe = Path(sys.executable)
    old = _sidelined(exe)
    try:
        old.unlink(missing_ok=True)
    except OSError:
        log.warning("could not clear stale %s before swap", old.name, exc_info=True)

    try:
        exe.rename(old)
    except OSError:
        log.exception("could not move the running binary aside; update not applied")
        return

    try:
        shutil.move(str(PENDING), str(exe))
        if sys.platform != "win32":
            # the downloaded payload isn't executable; restore the bit on posix
            os.chmod(exe, 0o755)
        log.info("applied pending update: swapped in %s", PENDING.name)
    except OSError:
        log.exception("could not move the update into place; rolling back")
        try:
            if exe.exists():
                exe.unlink()  # drop any partial copy a cross-volume move left
            old.rename(exe)  # restore the previous binary so the app still launches
            log.info("rolled back to the previous binary after a failed update")
        except OSError:
            log.exception(
                "rollback failed; the previous binary is at %s and the update at %s",
                old,
                PENDING,
            )


def relaunch() -> None:
    """Start a fresh copy of the (freshly-swapped) binary as an INDEPENDENT process.

    PYINSTALLER_RESET_ENVIRONMENT=1 is mandatory here. Without it, PyInstaller 6.x
    treats a child spawned via sys.executable as a *worker subprocess* and has it reuse
    THIS process's onefile temp dir (_MEIxxxx) instead of unpacking its own. When this
    parent then exits and its bootloader deletes that dir, the relaunched app is left
    running against a half-deleted _MEI — which is exactly why certifi's cacert.pem
    disappeared (breaking HTTPS/PDF downloads) and the "failed to remove _MEI" warning
    appeared after an update. See PyInstaller "Common Issues and Pitfalls".
    """
    subprocess.Popen(
        [sys.executable], env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    )


def cleanup() -> None:
    """Remove the leftover sidelined binary from a previous update. Call on startup."""
    if getattr(sys, "frozen", False):
        old = _sidelined(Path(sys.executable))
        try:
            old.unlink(missing_ok=True)
            log.info("startup cleanup: cleared leftover %s (if any)", old.name)
        except OSError:
            # e.g. old instance still exiting; retry next start
            log.warning("startup cleanup: could not remove %s", old, exc_info=True)
