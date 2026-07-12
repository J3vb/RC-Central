"""Download, verify, and extract vendor tools; track installed state."""

import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.paths import data_dir

DATA_DIR = data_dir()
TOOLS_DIR = DATA_DIR / "tools"

# exe names that are never the tool itself
_EXE_SKIP = ("unins", "uninstall", "setup", "install", "update", "vcredist")


class VendorFileChanged(Exception):
    """Pinned sha256 no longer matches: the vendor replaced the file, the catalog entry needs a refresh."""


class ExeNotFound(Exception):
    pass


def install(tool: dict, progress=None) -> Path:
    """Download + unpack a catalog tool (extract archive, or run/keep a bare exe). Returns the resolved exe path."""
    dest = TOOLS_DIR / tool["id"]
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ("download." + tool["download"]["archive"])
        _download(tool["download"]["url"], archive, progress)
        pinned = tool["download"].get("sha256")
        if pinned and _sha256(archive) != pinned.lower():
            raise VendorFileChanged(
                f"{tool['name']}: the downloaded file no longer matches the catalog hash. "
                "The vendor likely updated it - the catalog entry needs a refresh."
            )
        if dest.exists():
            shutil.rmtree(dest)
        inst = tool.get("install", {})
        if tool["download"]["archive"] == "exe":
            # the download IS the installer / portable exe - nothing to extract.
            # keep it in the tool dir under its declared name so setup_args and
            # exe_relative_path resolve exactly as they do for archive tools.
            target = dest / (
                inst.get("setup_relative_path")
                or inst.get("exe_relative_path")
                or "installer.exe"
            )
            target.parent.mkdir(parents=True, exist_ok=True)  # covers a nested relative path
            shutil.copy2(archive, target)
        else:
            _extract(archive, dest)
    if inst.get("setup_args"):
        # archive ships a silent-capable installer (e.g. Inno Setup) instead of a
        # portable exe: run it into the tool dir, then resolve the installed app
        setup = _find_exe(dest, inst.get("setup_relative_path"))
        args = [a.replace("{dest}", str(dest)) for a in inst["setup_args"]]
        _run_setup(setup, args)
    exe = _find_exe(dest, inst.get("exe_relative_path"))
    _state_file(tool["id"]).write_text(
        json.dumps(
            {
                "version": tool["version"],
                "exe_path": str(exe),
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return exe


def register_existing(tool: dict, exe_path: str, version: str) -> Path:
    """Record a user-provided existing install, skipping the download.

    The exe stays wherever the user has it; we only write the state file so
    get_state()/launcher treat it like any other install.
    """
    exe = Path(exe_path)
    if not exe.is_file():
        raise ExeNotFound(f"{exe_path} is not a file")
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    _state_file(tool["id"]).write_text(
        json.dumps(
            {
                "version": version,
                "exe_path": str(exe),
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "source": "existing",  # distinguishes a located install from a download
            }
        ),
        encoding="utf-8",
    )
    return exe


def get_state(tool_id: str) -> dict | None:
    """Installed state for a tool, or None if not installed (or its exe vanished)."""
    f = _state_file(tool_id)
    if not f.exists():
        return None
    state = json.loads(f.read_text(encoding="utf-8"))
    return state if Path(state["exe_path"]).exists() else None


def _state_file(tool_id: str) -> Path:
    return TOOLS_DIR / f"{tool_id}.state.json"


def _download(url: str, dest: Path, progress) -> None:
    with requests.get(url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        if "text/html" in resp.headers.get("content-type", ""):
            raise VendorFileChanged(
                "the vendor URL returned a web page instead of a file - "
                "the download link has likely moved; the catalog entry needs updating."
            )
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _ps_single_quote(s: str) -> str:
    """Wrap a value as a PowerShell single-quoted literal, doubling embedded quotes.

    Single-quoting keeps a path with spaces intact and stops an apostrophe (a legal
    Windows filename char, e.g. C:\\Users\\O'Brien) from closing the string early or
    injecting into the -Command we pass to powershell.
    """
    return "'" + s.replace("'", "''") + "'"


def _run_setup(setup: Path, args: list[str], timeout: int = 900) -> None:
    """Run a bundled installer, elevating only if its manifest demands admin.

    Most bundled installers are asInvoker (e.g. Hobbywing's Inno setup) and run fine
    with CreateProcess. A requireAdministrator installer (e.g. EdgeTX's NSIS setup)
    cannot be started that way from a non-elevated process - CreateProcess fails with
    WinError 740. Fall back to ShellExecute 'runas' via PowerShell, which raises the
    UAC prompt and (-Wait) blocks until the silent install finishes.
    """
    try:
        subprocess.run([str(setup), *args], check=True, timeout=timeout)
    except OSError as e:
        if getattr(e, "winerror", None) != 740:  # 740 = ERROR_ELEVATION_REQUIRED
            raise
        # -PassThru + `exit $p.ExitCode` makes powershell surface the installer's own
        # exit code, so a failed elevated install raises here instead of being mistaken
        # for success (and only later confusing us as a missing exe).
        # ponytail ceiling unchanged: Start-Process re-quotes -ArgumentList when it
        # serializes to the child, which still mangles an NSIS /D= path that has spaces.
        arglist = ",".join(_ps_single_quote(a) for a in args)
        subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"$p = Start-Process -FilePath {_ps_single_quote(str(setup))} "
                f"-ArgumentList {arglist} -Verb RunAs -Wait -PassThru; exit $p.ExitCode",
            ],
            check=True,
            timeout=timeout,
        )


def _extract(archive: Path, dest: Path) -> None:
    # both extractors sanitize member paths (no ../ escape)
    if archive.suffix == ".7z":
        import py7zr  # deferred: slow import, only some vendors ship 7z

        with py7zr.SevenZipFile(archive) as z:
            z.extractall(dest)
    else:
        import zipfile

        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)


def _find_exe(root: Path, relative: str | None) -> Path:
    """Resolve the tool's exe: catalog hint first, else the single plausible exe."""
    if relative and (root / relative).exists():
        return root / relative
    candidates = [
        p
        for p in sorted(root.rglob("*.exe"))
        if not any(s in p.name.lower() for s in _EXE_SKIP)
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise ExeNotFound(
        f"could not pick an exe in {root}: candidates {[p.name for p in candidates]}"
    )
