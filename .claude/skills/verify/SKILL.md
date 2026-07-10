---
name: verify
description: Build/launch/drive recipe for verifying RC Central (PySide6 GUI) changes end-to-end on Windows.
---

# Verifying RC Central

## Launch

```
uv sync
uv run python -m app.main   # run in background; window title "RC Central v<version>" (see app/__init__.py)
```

Exit code 0 on clean close. Installed tools + state land in `%LOCALAPPDATA%\RCCentral\tools\`.
Delete that dir to reset to a fresh-install state.

## Drive the GUI

- Screenshot: PowerShell `System.Drawing` `CopyFromScreen` (see any `shot.ps1` from past sessions).
- **UIA exposes the table rows (DataItem names = cell text) and titlebar buttons, but NOT
  the QPushButtons placed via `setCellWidget`** — they appear as an empty DataItem. To click
  the Install/Launch button: find the last DataItem of the row, take its BoundingRectangle,
  `SetForegroundWindow` the app, and send a real mouse click at the rect center.
- Read row state textually via UIA DataItem names (no screenshot needed):
  window `RC Central` → Descendants of ControlType DataItem.
- Close cleanly via the titlebar `Close` button (UIA InvokePattern works on it).

## Flows worth driving

1. Install: click the row button → progress bar → status flips to "Installed vX", files in
   `%LOCALAPPDATA%\RCCentral\tools\<id>\` + `<id>.state.json`. Real download from the vendor (~8 MB for Rêve D).
2. Launch: button spawns the vendor exe (process path under `RCCentral`); clicking Launch
   again must NOT spawn a second process.
3. Restart persistence: relaunch app → row shows "Installed vX" from state without reinstalling.

## Gotchas

- Vendor URL health: `uv run python scripts/validate_catalog.py --check-urls`. Rêve D serves
  "file not found" HTML pages with HTTP 200 — status codes alone prove nothing; the script
  checks content-type.
- The catalog remote fetch 404s (placeholder URL) and silently falls back to the bundled
  `catalog/tools/*.json` — edits to those files take effect on next app start, unless a
  cached `%LOCALAPPDATA%\RCCentral\catalog.json` exists and wins.
- Tools with `needs_admin: true` (Hobbywing USB Link) launch via ShellExecute → **UAC
  prompt** the human must approve; the resulting process is elevated, so a non-admin shell
  cannot Stop-Process/CloseMainWindow it (UIPI) — the human has to close it.
- An elevated foreground window can silently defeat `SetForegroundWindow`, making
  coordinate clicks land on the wrong app. Before clicking, `SetWindowPos` RC Central to
  HWND_TOPMOST at a known position (non-elevated windows accept this).
