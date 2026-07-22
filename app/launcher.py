"""Spawn vendor tool processes, detached from the hub's lifetime.

The catalog tools are Windows-only vendor executables, so the Tools tab that
calls this only exists on Windows (see app/ui/window.py). ``launch`` guards on
that so the Windows-only ``os.startfile`` path is never reached elsewhere.
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess


def launch(tool_id: str, exe_path: str, needs_admin: bool = False) -> None:
    """Start a tool, detached. Raises OSError if the user declines UAC.

    Fire-and-forget on purpose: a launched vendor tool must NOT be bound to RC
    Central's lifetime. An *attached* QProcess is killed by its destructor when the
    hub exits — fatal for a firmware flasher writing to a servo/ESC/gyro mid-update.
    """
    if sys.platform != "win32":
        raise OSError("Launching catalog tools is only supported on Windows.")
    exe = Path(exe_path)
    if needs_admin:
        # ShellExecute handles the UAC elevation QProcess can't
        os.startfile(str(exe), cwd=str(exe.parent))
        return
    # startDetached (not start()) so the child outlives the hub. An exe whose manifest
    # demands elevation fails CreateProcess even when the catalog says needs_admin=false,
    # so fall back to ShellExecute/UAC just as the attached path used to.
    ok, _pid = QProcess.startDetached(str(exe), [], str(exe.parent))
    if not ok:
        os.startfile(str(exe), cwd=str(exe.parent))
