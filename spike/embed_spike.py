"""Throwaway Phase 2 spike: embed a foreign Win32 window inside a Qt widget.

Usage:
    uv run python spike/embed_spike.py [path-to-exe]

Defaults to notepad.exe. Record results in spike/NOTES.md.
"""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

user32 = ctypes.windll.user32


def find_main_window(pid: int, timeout: float = 10.0) -> int:
    """Poll for a visible top-level window owned by pid."""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found.clear()
        user32.EnumWindows(on_window, 0)
        if found:
            return found[0]
        time.sleep(0.2)
    raise SystemExit(
        f"no visible window for pid {pid} after {timeout}s "
        "(single-instance apps like Win11 Notepad hand off to an existing process - try another exe)"
    )


def main() -> None:
    exe = sys.argv[1] if len(sys.argv) > 1 else "notepad.exe"
    app = QApplication(sys.argv)

    child = subprocess.Popen([exe])
    hwnd = find_main_window(child.pid)
    print(f"embedding hwnd {hwnd:#x} from {exe}")

    foreign = QWindow.fromWinId(hwnd)
    container = QWidget.createWindowContainer(foreign)

    win = QMainWindow()
    win.setWindowTitle(f"RC Central embed spike - {exe}")
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(QLabel(f"Embedded: {exe} (hwnd {hwnd:#x})"))
    layout.addWidget(container, stretch=1)
    win.setCentralWidget(central)
    win.resize(900, 600)
    win.show()

    code = app.exec()
    child.terminate()
    sys.exit(code)


if __name__ == "__main__":
    main()
