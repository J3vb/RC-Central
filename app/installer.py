"""Download, verify, and extract vendor tools; track installed state."""

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.paths import data_dir

DATA_DIR = data_dir()
TOOLS_DIR = DATA_DIR / "tools"
MANUALS_DIR = DATA_DIR / "manuals"

# exe names that are never the tool itself
_EXE_SKIP = ("unins", "uninstall", "setup", "install", "update", "vcredist")


class VendorFileChanged(Exception):
    """Pinned sha256 no longer matches: the vendor replaced the file, the catalog entry needs a refresh."""


class ExeNotFound(Exception):
    pass


class DownloadCancelled(Exception):
    """Raised out of _download when its cancel event is set, to unwind the stream cleanly."""


def install(tool: dict, progress=None) -> Path:
    """Download + unpack a catalog tool (extract archive, or run/keep a bare exe). Returns the resolved exe path."""
    dest = TOOLS_DIR / tool["id"]
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    inst = tool.get("install", {})
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ("download." + tool["download"]["archive"])
        _download(tool["download"]["url"], archive, progress)
        pinned = tool["download"].get("sha256")
        if pinned and _sha256(archive) != pinned.lower():
            raise VendorFileChanged(
                f"{tool['name']}: the downloaded file no longer matches the catalog hash. "
                "The vendor likely updated it - the catalog entry needs a refresh."
            )
        # Archive verified. Preserve any working install until the new one is proven: a
        # failed extract/setup (corrupt archive, declined UAC, non-zero installer exit,
        # disk-full) must roll back, not leave the tool wiped with no replacement. Same
        # sideline-then-swap pattern updater.apply_pending uses for the running binary.
        backup = None
        if dest.exists():
            backup = dest.with_name(dest.name + ".old")
            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)  # same-volume rename: atomic and reversible
        try:
            if tool["download"]["archive"] == "exe":
                # the download IS the installer / portable exe - nothing to extract.
                # keep it in the tool dir under its declared name so setup_args and
                # exe_relative_path resolve exactly as they do for archive tools.
                target = dest / (
                    inst.get("setup_relative_path")
                    or inst.get("exe_relative_path")
                    # NOT "installer.exe": _find_exe's skip-list drops names containing
                    # "install", so a default-named bare exe would fail to auto-resolve
                    or "tool.exe"
                )
                # the hint comes from the remote catalog: never let it write outside the tool dir
                if not target.resolve().is_relative_to(dest.resolve()):
                    target = dest / "tool.exe"
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
        except BaseException:
            # roll back to the preserved install so a failure never uninstalls the tool
            shutil.rmtree(dest, ignore_errors=True)
            if backup is not None:
                backup.rename(dest)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)  # new install proven; drop the old copy
    # tmp + atomic replace, like garage.save_car: a crash mid-write can't leave a
    # truncated state file (which get_state would then misread as not-installed).
    state_file = _state_file(tool["id"])
    tmp = state_file.with_name(state_file.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "version": tool["version"],
                "exe_path": str(exe),
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    tmp.replace(state_file)
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
    # tmp + atomic replace like install() above, so a crash mid-write can't leave a
    # truncated state file that get_state() would misread as not-installed.
    state_file = _state_file(tool["id"])
    tmp = state_file.with_name(state_file.name + ".tmp")
    tmp.write_text(
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
    tmp.replace(state_file)
    return exe


def uninstall(tool_id: str) -> None:
    """Remove a tool's install: deletes downloaded files unless the install was a
    located existing one (source=="existing"), whose files belong to the user."""
    state_file = _state_file(tool_id)
    if not state_file.exists():
        return
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (ValueError, OSError, KeyError):
        state = {}  # corrupt/partial state: fall through and clean up like a download
    if not isinstance(state, dict):
        state = {}  # valid JSON but not a state object: treat as a download to clean up
    if state.get("source") != "existing":
        tool_dir = TOOLS_DIR / tool_id
        if tool_dir.exists():
            shutil.rmtree(tool_dir)
    state_file.unlink(missing_ok=True)


def get_state(tool_id: str) -> dict | None:
    """Installed state for a tool, or None if not installed (or its exe vanished)."""
    f = _state_file(tool_id)
    if not f.exists():
        return None
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return None  # valid JSON but not a state object (null, list, number): not installed
        return state if Path(state["exe_path"]).exists() else None
    except (ValueError, OSError, KeyError, TypeError):
        return None  # corrupt/partial state file reads as not-installed, never crashes


def _state_file(tool_id: str) -> Path:
    return TOOLS_DIR / f"{tool_id}.state.json"


def manual_cache_path(url: str) -> Path:
    """Local cache path for a manual PDF; its existence IS the 'downloaded' state
    (no state file needed). Named by a hash of the URL so any URL maps to one file."""
    return MANUALS_DIR / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".pdf")


def manual_is_cached(url: str) -> bool:
    return manual_cache_path(url).exists()


def download_manual(url: str, progress=None, cancel=None) -> Path:
    """Fetch a manual PDF into the local cache and return its path. Reuses _download's
    HTML-guard, so a dead/moved link (a web page instead of a PDF) raises VendorFileChanged
    instead of caching garbage. Pass a threading.Event as cancel to abort mid-download."""
    MANUALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = manual_cache_path(url)
    # Unique per URL, so parallel downloads never share a temp; the except-branch cleans
    # this download's own temp on failure/cancel. Startup does the cross-run orphan sweep.
    tmp = dest.with_suffix(".part")
    try:
        _download(url, tmp, progress, cancel)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest


def clear_partial_manuals() -> None:
    """Remove orphaned .part temps left by a past download the app was killed mid-flight
    (the daemon thread dies without cleanup). Call once at startup, before any download
    is in flight, so a global sweep can't clobber a live parallel download's temp."""
    for stale in MANUALS_DIR.glob("*.part"):  # glob on a missing dir yields nothing
        stale.unlink(missing_ok=True)


def _download(url: str, dest: Path, progress, cancel=None) -> None:
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
                # cooperative cancel: a thread can't be killed, but we can stop between
                # chunks (~64 KB), so a cancel aborts within one chunk's worth of I/O
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled()
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


# Windows ShellExecuteEx elevation (see _run_elevated). Constants + struct.
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC = 0x00000100  # required: we call from a daemon thread w/ no message loop
_SW_HIDE = 0
_WAIT_TIMEOUT = 0x00000102


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIconOrMonitor", ctypes.c_void_p),  # DUMMYUNIONNAME (hIcon/hMonitor), both HANDLE
        ("hProcess", ctypes.c_void_p),
    ]


def _shell_execute_ex(verb: str, file: str, params: str, timeout: int) -> None:
    """Run 'file params' via ShellExecuteExW(verb), wait, raise on a nonzero exit.

    Windows-only (uses ctypes.windll). Kept as a thin seam so _run_elevated's argument
    building is unit-testable by monkeypatching this function.
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    sei.lpVerb = verb
    sei.lpFile = file
    sei.lpParameters = params
    sei.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        raise ctypes.WinError(ctypes.get_last_error())  # e.g. 1223 = UAC declined
    if not sei.hProcess:
        return  # no process handle returned (rare) - nothing to wait on
    try:
        if kernel32.WaitForSingleObject(sei.hProcess, int(timeout * 1000)) == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(cmd=file, timeout=timeout)
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        if code.value != 0:
            raise subprocess.CalledProcessError(code.value, f"{file} {params}")
    finally:
        kernel32.CloseHandle(sei.hProcess)


def _run_elevated(setup: Path, args: list[str], timeout: int) -> None:
    """Run an installer elevated (UAC) via ShellExecuteEx, waiting for it to finish.

    ShellExecuteEx passes lpParameters to the child verbatim, so an NSIS /D= install path
    with spaces survives - unlike PowerShell Start-Process, which re-quotes -ArgumentList
    and mangles /D=. Args are space-joined and NOT quoted; the catalog orders /D={dest}
    last, exactly as NSIS requires.
    """
    _shell_execute_ex("runas", str(setup), " ".join(args), timeout)


def _run_setup(setup: Path, args: list[str], timeout: int = 900) -> None:
    """Run a bundled installer, elevating (UAC) only if its manifest demands admin.

    Most bundled installers are asInvoker (e.g. Hobbywing's Inno setup) and run fine with
    CreateProcess. A requireAdministrator installer (e.g. EdgeTX's NSIS setup) fails
    CreateProcess from a non-elevated process with WinError 740; retry it elevated via
    ShellExecuteEx (_run_elevated), which raises the UAC prompt, waits, and surfaces the
    installer's exit code.
    """
    try:
        # ponytail: the direct path relies on subprocess list-quoting, correct for
        # Inno/asInvoker installers; an asInvoker NSIS installer with a spaced /D= would
        # need _run_elevated's verbatim args too - no such catalog entry exists.
        subprocess.run([str(setup), *args], check=True, timeout=timeout)
    except OSError as e:
        if getattr(e, "winerror", None) != 740:  # 740 = ERROR_ELEVATION_REQUIRED
            raise
        _run_elevated(setup, args, timeout)


def _extract(archive: Path, dest: Path) -> None:
    # every extractor here sanitizes member paths (no ../ escape)
    if archive.suffix == ".7z":
        import py7zr  # deferred: slow import, only some vendors ship 7z

        with py7zr.SevenZipFile(archive) as z:
            z.extractall(dest)
    elif archive.suffix == ".rar":
        _extract_rar(archive, dest)
    else:
        import zipfile

        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    _unwrap_nested_zip(dest)


def _bsdtar() -> Path:
    """Windows' bundled libarchive CLI, which reads RAR and RAR5.

    Shipped in System32 since Windows 10 1803, so a vendor who publishes .rar (Power HD,
    FlySky's receiver updater) costs us no new dependency and no redistributed UnRAR
    binary - which is the only reason RAR support is cheap enough to be worth having.
    The Tools tab is Windows-only, so there is no cross-platform gap to cover.
    """
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"


def _extract_rar(archive: Path, dest: Path) -> None:
    tar = _bsdtar()
    if not tar.is_file():
        raise RuntimeError(
            "this tool ships as a .rar and Windows' built-in archive support "
            f"({tar}) is missing, so it can't be unpacked automatically. "
            "Download it from the vendor's site instead."
        )
    dest.mkdir(parents=True, exist_ok=True)
    # NO -P: without it bsdtar refuses absolute paths and '..' members
    # (SECURE_NODOTDOT), giving the same containment zipfile/py7zr provide.
    try:
        subprocess.run(
            [str(tar), "-xf", str(archive), "-C", str(dest)],
            check=True,
            timeout=300,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        # bsdtar is a console tool: on Windows its stderr is the OEM codepage, not UTF-8.
        enc = "oem" if os.name == "nt" else "utf-8"
        detail = (e.stderr or b"").decode(enc, errors="replace").strip()
        raise RuntimeError(f"could not unpack the vendor's .rar archive: {detail}") from e


def _unwrap_nested_zip(dest: Path) -> None:
    """Unpack a lone inner zip in place: some vendors ship a zip inside a zip.

    Futaba's GYD560 package is a zip whose payload is another zip plus a PDF, so the
    updater exe sits one level deeper than exe_relative_path can address. Only fires
    when the outer archive yielded no exe at all and exactly one zip, so an archive
    that legitimately bundles a zip beside its app is left alone.

    ponytail: one level only - a package nested deeper needs a real unpack step in the
    manifest rather than more guessing here.
    """
    if any(dest.rglob("*.exe")):
        return
    nested = list(dest.rglob("*.zip"))
    if len(nested) != 1:
        return
    import zipfile

    with zipfile.ZipFile(nested[0]) as z:
        z.extractall(dest)  # sanitizes member paths, same as the outer extract
    nested[0].unlink()  # content is now in dest; keep the tool dir free of the husk


def _find_exe(root: Path, relative: str | None) -> Path:
    """Resolve the tool's exe: catalog hint first, else the single plausible exe."""
    if relative:
        candidate = (root / relative).resolve()
        # the hint comes from the remote catalog: never let it escape the tool dir
        if candidate.is_relative_to(root.resolve()) and candidate.exists():
            return candidate
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
