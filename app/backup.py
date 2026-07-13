"""Back up and restore user-authored data (garage spec sheets) as one zip. No Qt.

Only the garage holds irreplaceable, hand-authored data (spec sheets + run logs).
Catalog cache, tool downloads, and manual PDFs are all re-downloadable and large,
so they are deliberately excluded. Mirrors garage.py's module-constant pattern so
tests can monkeypatch GARAGE_DIR.
"""

import zipfile
from pathlib import Path

from app.paths import data_dir

DATA_DIR = data_dir()
GARAGE_DIR = DATA_DIR / "garage"


def make_backup(dest_zip) -> Path:
    """Write every garage car JSON into dest_zip under 'garage/'. Returns dest_zip.

    An empty or absent garage yields a valid empty zip.
    """
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        files = sorted(GARAGE_DIR.glob("*.json")) if GARAGE_DIR.exists() else []
        for f in files:
            z.write(f, arcname=f"garage/{f.name}")
    return dest_zip


def restore_backup(src_zip) -> int:
    """Extract garage/*.json from src_zip into GARAGE_DIR, overwriting by id.

    Returns the number of car files restored. Reading each member and writing by
    basename (rather than extractall) confines writes to GARAGE_DIR, so a crafted
    'garage/../evil.json' member can't escape. Non-garage members are ignored.
    """
    GARAGE_DIR.mkdir(parents=True, exist_ok=True)
    restored = 0
    with zipfile.ZipFile(src_zip) as z:
        for name in z.namelist():
            stem = name[len("garage/"):]
            if name.startswith("garage/") and name.endswith(".json") and "/" not in stem:
                (GARAGE_DIR / Path(name).name).write_bytes(z.read(name))
                restored += 1
    return restored
