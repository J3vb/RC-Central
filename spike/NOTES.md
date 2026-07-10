# Phase 2 embedding spike results

Run `uv run python spike/embed_spike.py [exe]` and record what happened per tool.
The app ships external-launch either way; embedding is opt-in per tool only if it proves solid here.

| Exe | Docks inside the Qt window? | Resize OK? | Focus/keyboard OK? | Notes |
| --- | --- | --- | --- | --- |
| notepad.exe | ? | ? | ? | Win11 Notepad is single-instance; may need another exe |
| RS-ST servo program V2.0.2.exe | ? | ? | ? | install via the app first, then point the spike at it |

Known risks to watch (from research): foreign window floating over the container
instead of docking, DPI-awareness mismatch blur, teardown segfaults, dialogs
escaping the frame.
