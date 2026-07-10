"""Hub self-update from GitHub Releases: fetch on start, swap the exe on exit.

Only active in frozen (PyInstaller) builds; running from source is a no-op.
"""

import shutil
import sys
from pathlib import Path

import requests

from app import __version__
from app.installer import DATA_DIR

# ponytail: placeholder until the repo is public; the check 404s and quietly does nothing
REPO = "rc-central/rc-central"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
PENDING = DATA_DIR / "update-pending.exe"


def _newer(tag: str, current: str) -> bool:
    try:
        def parse(v):
            return tuple(int(x) for x in v.lstrip("vV").split("."))

        return parse(tag) > parse(current)
    except ValueError:
        return False


def fetch_update() -> bool:
    """Download a newer release exe to PENDING if one exists. Never raises."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        rel = requests.get(API_URL, timeout=10).json()
        if not _newer(rel.get("tag_name", ""), __version__):
            return False
        url = next(
            a["browser_download_url"]
            for a in rel.get("assets", [])
            if a["name"].endswith(".exe")
        )
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PENDING.with_suffix(".part")
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
        tmp.replace(PENDING)
        return True
    except Exception:
        return False  # an update failure must never break the app


def apply_pending() -> None:
    """Swap the downloaded update into place. Call after the event loop exits."""
    if not (getattr(sys, "frozen", False) and PENDING.exists()):
        return
    exe = Path(sys.executable)
    old = exe.with_suffix(".old.exe")
    try:
        old.unlink(missing_ok=True)
        exe.rename(old)  # Windows allows renaming a running exe, not deleting it
        shutil.move(str(PENDING), str(exe))
    except OSError:
        pass


def cleanup() -> None:
    """Remove the leftover .old.exe from a previous update. Call on startup."""
    if getattr(sys, "frozen", False):
        try:
            Path(sys.executable).with_suffix(".old.exe").unlink(missing_ok=True)
        except OSError:
            pass  # e.g. old instance still exiting; retry next start
