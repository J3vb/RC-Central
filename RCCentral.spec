# PyInstaller spec — one onefile, windowed build per platform.
#
# Building from a spec (rather than the CLI) keeps the per-OS differences in one
# place and sidesteps the CLI's platform-specific --add-data separator (";" on
# Windows, ":" elsewhere): here the data files are plain (src, dest) tuples.
#
# Build:  uv run pyinstaller RCCentral.spec   ->   dist/RCCentral[.exe]

import sys

# The .ico is embedded on Windows; PyInstaller ignores --icon for a Linux ELF.
icon = "app/assets/icon.ico" if sys.platform == "win32" else None

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    datas=[
        ("catalog", "catalog"),
        ("app/assets", "app/assets"),
    ],
    # QtPdf/QtPdfWidgets are imported behind a try/except (app/ui/pdf_viewer.py),
    # so name them explicitly to be sure the Qt6Pdf libs get collected.
    hiddenimports=["PySide6.QtPdf", "PySide6.QtPdfWidgets"],
)

pyz = PYZ(a.pure)

# onefile: bundle scripts, binaries and datas straight into the EXE (no COLLECT).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RCCentral",
    console=False,  # windowed (GUI) app
    icon=icon,
)
