"""Load the tool catalog: signature-verified remote JSON, local cache, bundled fallback."""

import base64
import json
import logging
import re
import sys
from pathlib import Path

import requests
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.paths import data_dir

log = logging.getLogger(__name__)

CATALOG_URL = "https://raw.githubusercontent.com/J3vb/RC-Central/main/catalog/catalog.json"
# Detached Ed25519 signature over catalog.json's exact bytes, served alongside it.
CATALOG_SIG_URL = CATALOG_URL + ".sig"

# The remote catalog is the app's control plane: it dictates every download URL and the
# exe each tool launches. So it gets the same provenance gate the self-updater already
# applies to release binaries — the SAME Ed25519 key (app/updater.py's _UPDATE_PUBLIC_KEY;
# private half is the CI UPDATE_SIGNING_KEY secret). Reusing the key across both is safe: a
# signed catalog fails the updater's PE/ELF magic check and a signed binary fails _valid()'s
# shape check, so neither signature can be replayed as the other.
_CATALOG_PUBLIC_KEY = "m/DJAivTLTZQyIrFeovo4n9CZ8p3Ytbccv6sFN3G3Yk="

DATA_DIR = data_dir()
CACHE_FILE = DATA_DIR / "catalog.json"


def _bundled_tools_dir() -> Path:
    # _MEIPASS is where PyInstaller unpacks --add-data at runtime
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "catalog" / "tools"


# keep in sync with catalog/schema.json's download.archive enum: the schema runs
# only in CI, this runs on every launch, and _valid() rejects the WHOLE catalog if
# any one entry fails - so a value allowed there but missing here silently strands
# every user on their cached copy.
_ARCHIVES = ("zip", "7z", "rar", "exe")


def _valid_urls(items) -> bool:
    """True when `items` is absent or a list of dicts whose `url` is an https string."""
    if items is None:
        return True
    return isinstance(items, list) and all(
        isinstance(d, dict) and isinstance(d.get("url"), str) and d["url"].startswith("https://")
        for d in items
    )


def _valid_tool(t) -> bool:
    if not (
        isinstance(t, dict)
        and isinstance(t.get("name"), str)
        and isinstance(t.get("id"), str)
        # id becomes a filesystem path component (TOOLS_DIR / tool["id"]) in
        # installer.py, so it must stay a strict slug - never "../.."
        and re.fullmatch(r"[a-z0-9][a-z0-9-]*", t["id"])
        # the Tools/Manuals tabs index tool["vendor"]/["version"] directly, so a
        # signed-but-malformed catalog missing them would KeyError-crash the refresh
        and isinstance(t.get("vendor"), str)
        and isinstance(t.get("version"), str)
    ):
        return False
    dl = t.get("download")
    if dl is not None:
        # install() builds the download path from download.archive and fetches
        # download.url; the JSON schema pins both but runs only in CI, so re-check
        # here - a hostile "archive" like "../../x" would otherwise escape the temp dir.
        if not (
            isinstance(dl, dict)
            and isinstance(dl.get("url"), str)
            and dl["url"].startswith("https://")
            and dl.get("archive") in _ARCHIVES
        ):
            return False
    # drivers[]/links[]/homepage are opened directly by the UI; the schema pins them to
    # https but runs only in CI, so re-check here like download.url above.
    if not (_valid_urls(t.get("drivers")) and _valid_urls(t.get("links"))):
        return False
    homepage = t.get("homepage")
    if homepage is not None and not (isinstance(homepage, str) and homepage.startswith("https://")):
        return False
    return True


def _valid(tools) -> bool:
    """Minimal shape check before trusting or caching a fetched catalog."""
    return isinstance(tools, list) and bool(tools) and all(_valid_tool(t) for t in tools)


def cached_catalog() -> list[dict]:
    """Catalog from disk only, no network: cached fetch if valid, else bundled.

    The single source of truth for the offline fallback chain — load_catalog's tail
    and the instant-startup seed both come through here."""
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cached = None
        if _valid(cached):
            return cached
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(_bundled_tools_dir().glob("*.json"))
    ]


def _verified_remote() -> list[dict] | None:
    """The remote catalog, but only if its detached signature verifies against the
    pinned key. Returns None for a bad shape or a missing/forged signature so the
    caller falls back; may raise requests.RequestException on a network failure."""
    payload = requests.get(CATALOG_URL, timeout=10)
    payload.raise_for_status()
    sig = requests.get(CATALOG_SIG_URL, timeout=10)
    sig.raise_for_status()
    try:
        # verify the exact bytes served, not a re-serialization of the parsed JSON
        VerifyKey(base64.b64decode(_CATALOG_PUBLIC_KEY)).verify(payload.content, sig.content)
    except (BadSignatureError, ValueError):
        log.warning("remote catalog signature did not verify; ignoring the remote copy")
        return None
    try:
        tools = json.loads(payload.content)
    except ValueError:
        log.warning("remote catalog is not valid JSON; ignoring the remote copy")
        return None
    if not _valid(tools):
        log.warning("remote catalog has an unexpected shape; ignoring the remote copy")
        return None
    return tools


def load_catalog() -> list[dict]:
    """Newest catalog we can trust: signature-verified remote > cached > bundled."""
    try:
        tools = _verified_remote()
    except requests.RequestException as e:
        log.info("catalog fetch failed (%s); using the cached/bundled copy", e)
        tools = None
    if tools is None:
        return cached_catalog()
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(tools), encoding="utf-8")
    except OSError:
        log.warning("could not cache the fetched catalog", exc_info=True)  # keep the good fetch
    return tools
