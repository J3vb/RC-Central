"""Load the tool catalog: remote JSON with a local cache, bundled fallback."""

import json
import sys
from pathlib import Path

import requests

from app.paths import data_dir

CATALOG_URL = "https://raw.githubusercontent.com/J3vb/RC-Central/main/catalog/catalog.json"

DATA_DIR = data_dir()
CACHE_FILE = DATA_DIR / "catalog.json"


def _bundled_tools_dir() -> Path:
    # _MEIPASS is where PyInstaller unpacks --add-data at runtime
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "catalog" / "tools"


def _valid(tools) -> bool:
    """Minimal shape check before trusting or caching a fetched catalog."""
    return (
        isinstance(tools, list)
        and bool(tools)
        and all(isinstance(t, dict) and "id" in t and "name" in t for t in tools)
    )


def load_catalog() -> list[dict]:
    """Newest catalog we can get: remote > cached > bundled."""
    try:
        resp = requests.get(CATALOG_URL, timeout=10)
        resp.raise_for_status()
        tools = resp.json()
        if not _valid(tools):
            raise ValueError("remote catalog has unexpected shape")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(tools), encoding="utf-8")
        return tools
    except (requests.RequestException, ValueError):
        pass
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
        if _valid(cached):
            return cached
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(_bundled_tools_dir().glob("*.json"))
    ]
