"""Load the tool catalog: remote JSON with a local cache, bundled fallback."""

import json
import os
import sys
from pathlib import Path

import requests

CATALOG_URL = "https://raw.githubusercontent.com/J3vb/RC-Central/main/catalog/catalog.json"

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "RCCentral"
CACHE_FILE = DATA_DIR / "catalog.json"


def _bundled_tools_dir() -> Path:
    # _MEIPASS is where PyInstaller unpacks --add-data at runtime
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "catalog" / "tools"


def load_catalog() -> list[dict]:
    """Newest catalog we can get: remote > cached > bundled."""
    try:
        resp = requests.get(CATALOG_URL, timeout=10)
        resp.raise_for_status()
        tools = resp.json()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(tools), encoding="utf-8")
        return tools
    except (requests.RequestException, ValueError):
        pass
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(_bundled_tools_dir().glob("*.json"))
    ]
