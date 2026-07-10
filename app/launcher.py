"""Spawn and track vendor tool processes."""

from pathlib import Path

from PySide6.QtCore import QProcess

_procs: dict[str, QProcess] = {}


def launch(tool_id: str, exe_path: str) -> QProcess:
    """Start a tool (or return its already-running process)."""
    if is_running(tool_id):
        return _procs[tool_id]
    exe = Path(exe_path)
    proc = QProcess()
    proc.setProgram(str(exe))
    proc.setWorkingDirectory(str(exe.parent))
    proc.start()
    _procs[tool_id] = proc
    return proc


def is_running(tool_id: str) -> bool:
    proc = _procs.get(tool_id)
    return proc is not None and proc.state() != QProcess.ProcessState.NotRunning
