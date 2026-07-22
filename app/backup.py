"""Back up and restore user-authored data (garage spec sheets) as one zip. No Qt.

Only the garage holds irreplaceable, hand-authored data (spec sheets + run logs).
Catalog cache, tool downloads, and manual PDFs are all re-downloadable and large,
so they are deliberately excluded. The garage directory is owned by garage.py;
this module reads garage.GARAGE_DIR at call time so tests need patch only that one
constant and a future relocation can't desync the two.
"""

import json
import zipfile
import zlib
from pathlib import Path

from app import garage

# A restored member can be unreadable in several ways; none should abort the whole
# restore or crash the caller: not JSON (ValueError incl. JSONDecodeError), I/O
# error (OSError), encrypted (RuntimeError), or corrupt compressed data
# (BadZipFile on CRC, zlib.error on a malformed deflate stream).
_MEMBER_READ_ERRORS = (ValueError, OSError, RuntimeError, zipfile.BadZipFile, zlib.error)

# Zip-bomb guard: a legit spec sheet is a few KB, so these caps are generous.
# ZipExtFile truncates decompression at the member's declared file_size, so
# checking that size before z.read bounds the work; a header lying low just
# yields truncated bytes that fail json.loads and are skipped like any other
# corrupt member.
_MAX_MEMBER = 5 * 2**20  # decompressed bytes per member
_MAX_TOTAL = 50 * 2**20  # decompression budget for the whole restore


def make_backup(dest_zip) -> Path:
    """Write every garage car JSON into dest_zip under 'garage/'. Returns dest_zip.

    An empty or absent garage yields a valid empty zip.
    """
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    garage_dir = garage.GARAGE_DIR
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        files = sorted(garage_dir.glob("*.json")) if garage_dir.exists() else []
        for f in files:
            z.write(f, arcname=f"garage/{f.name}")
    return dest_zip


def restore_backup(src_zip) -> int:
    """Restore garage cars from src_zip, overwriting existing cars by id.

    Returns the number of cars restored. Each 'garage/*.json' member is read,
    parsed, and written to '<id>.json' where <id> is the car's own validated id —
    NOT the zip member name. That keys the file by id (matching garage's invariant,
    so no ghost cars), and means a crafted member name or id cannot escape the
    garage directory: the target's parent is asserted to be GARAGE_DIR. A member
    that is unreadable, not JSON, not a car object, or whose id would escape the
    directory is skipped rather than poisoning the garage or aborting the restore.
    A member whose declared decompressed size busts the per-member or whole-restore
    cap is skipped before decompression (zip-bomb guard).
    """
    garage_dir = garage.GARAGE_DIR
    garage_dir.mkdir(parents=True, exist_ok=True)
    base = garage_dir.resolve()
    restored = 0
    budget = _MAX_TOTAL
    with zipfile.ZipFile(src_zip) as z:
        for name in z.namelist():
            stem = name[len("garage/"):]
            if not (name.startswith("garage/") and stem.lower().endswith(".json") and "/" not in stem):
                continue  # not a top-level garage/*.json member
            size = z.getinfo(name).file_size
            if size > _MAX_MEMBER or size > budget:
                continue  # zip-bomb guard: skipped before any decompression
            budget -= size
            try:
                data = z.read(name)
                car = json.loads(data)
                if not (isinstance(car, dict) and isinstance(car.get("id"), str) and car["id"]):
                    continue  # not a car spec sheet (no usable id)
                if "\x00" in car["id"]:
                    continue  # NUL byte: Windows resolve() won't flag it, but write_bytes raises
                target = garage_dir / f"{car['id']}.json"
                if target.resolve().parent != base:
                    continue  # a crafted id ("../x", "C:x") that would escape the garage
            except _MEMBER_READ_ERRORS:
                continue  # unreadable/encrypted/corrupt/not-JSON, or a bad id (NUL byte): skip this member
            # The write stays OUTSIDE the read guard: a destination I/O error (disk full,
            # permission denied, a locked target) is a real failure the caller must surface
            # via its error dialog, not a silently-skipped "corrupt member" that just lowers
            # the restored count.
            target.write_bytes(data)
            restored += 1
    return restored
