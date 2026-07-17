"""Spawn and track vendor tool processes.

The catalog tools are Windows-only vendor executables, so the Tools tab that
calls this only exists on Windows (see app/ui/window.py). ``launch`` guards on
that so the Windows-only ``os.startfile`` path is never reached elsewhere.
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess

_procs: dict[str, QProcess] = {}


def launch(tool_id: str, exe_path: str, needs_admin: bool = False) -> None:
    """Start a tool (no-op if already running). Raises OSError if the user declines UAC."""
    if sys.platform != "win32":
        raise OSError("Launching catalog tools is only supported on Windows.")
    if is_running(tool_id):
        return
    exe = Path(exe_path)
    if needs_admin:
        # ponytail: ShellExecute handles the UAC elevation QProcess can't; costs us
        # process tracking, so is_running() stays False for these tools
        os.startfile(str(exe), cwd=str(exe.parent))
        return
    proc = QProcess()
    proc.setProgram(str(exe))
    proc.setWorkingDirectory(str(exe.parent))
    proc.start()
    if not proc.waitForStarted(3000):
        # exes whose manifest demands elevation fail CreateProcess even when the
        # catalog says needs_admin=false - retry through ShellExecute/UAC
        os.startfile(str(exe), cwd=str(exe.parent))
        return
    _procs[tool_id] = proc


def is_running(tool_id: str) -> bool:
    proc = _procs.get(tool_id)
    return proc is not None and proc.state() != QProcess.ProcessState.NotRunning
