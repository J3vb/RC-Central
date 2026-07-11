"""The per-user data directory, resolved per OS.

Every module that persists state (installer, garage, logs, catalog cache) hangs
its files off ``data_dir()`` so there is one place — and one platform table — for
where RC Central keeps things. Kept Qt-free so it stays importable and testable
without a GUI.
"""

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """The ``RCCentral`` data directory for the current OS.

    Windows keeps it under ``%LOCALAPPDATA%``, Linux under the XDG data home
    (``~/.local/share``), macOS under Application Support. The env-var reads
    fall back to the conventional home-relative path when the variable is unset.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:  # linux and other posix
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "RCCentral"
