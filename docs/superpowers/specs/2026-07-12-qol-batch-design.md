# RC Central — Quality-of-Life batch (2026-07-12)

## Overview

Six small, independent quality-of-life features across the four areas of the
app, chosen as **gap-fillers that finish things already 80% built**. Every item
reuses an existing seam — `gearing.compute`, the per-car JSON files,
`installer` state, `QDesktopServices`, `QSettings` — so none adds a dependency
or a new persistence layer.

## Goals

- Finish half-built round-trips (Garage export → also import; add clone).
- Give the Gear Calculator the one tool drift tuners reach for daily (pinion sweep).
- Add basic launcher hygiene the Tools tab lacks (uninstall, open folder).
- Remember window size and last tab between runs.

## Non-goals (deferred, with reasons)

- **Per-car photo, favorites, manual reorder** — more storage/UI for less daily value.
- **Compare-two-setups, reverse-solve, named gearing presets** — the what-if table
  covers most of it, and saved cars already act as presets.
- **"Update available" badge** — the per-row **Update** button already is one.
- **Theme toggle** — the one genuinely expensive item (needs a stylesheet or a new
  dependency such as `qdarktheme`); Win11 already gives partial dark chrome.
- **Settings tab, keyboard shortcuts** — nothing to configure yet (YAGNI); shortcuts
  add little over clicks in this UI.

## Features

### A. Duplicate car (Garage)

- New **Duplicate** button in the Garage left-column button row: `New / Import… /
  Duplicate / Delete`.
- Clones the currently open car: deep-copy of the form's car dict, fresh `id`,
  `name` → `"<name> (copy)"`, then save and select it. The run/maintenance log
  copies with it (expected for a duplicate).
- Implementation: reuse `_form_to_car()`, replace `id`, adjust `name`, `save_car()`,
  reload + select. No new `garage.py` function required, but a thin
  `garage.clone_car(car) -> dict` (returns a new-id copy, unsaved) keeps the id-reset
  logic testable without Qt.

### B. Import / Export as JSON (Garage)

- **Export** stays a single button. Add a `;;JSON (*.json)` filter to the existing
  save dialog; branch on the chosen path's suffix: `.json` → `json.dumps(car, indent=2)`,
  otherwise the current `format_spec_sheet` text.
- **Import…** button (left column) opens a car `.json`, parses it, assigns a **new**
  `id` (so importing can never clobber an existing car), saves, and selects it.
- Errors (unreadable file, invalid JSON, not an object) surface as a warning box,
  never a traceback. A missing-fields import still works because the form fill and
  `format_spec_sheet` already treat every field as optional with defaults.
- Implementation: `garage.load_car_file(path) -> dict` (read + validate it's a dict,
  assign new id) keeps parsing/id logic out of the widget and testable.

### C. Rollout what-if table (Gear Calculator)

- A read-only table below the Results block: columns `Pinion | FDR | Rollout (mm) |
  km/h`.
- Sweeps the current pinion **±3 (7 rows)**, clamping pinion ≥ 1 (so near the low end
  the row set may be shorter). The row equal to the current pinion is bolded.
- Recomputes together with the rest of the form on any input change, reusing the same
  spur / internal ratio / tire / Kv / cells. Reuses `gearing.compute` per pinion.
- Fixed ±3 range (`# ponytail:` widen or make configurable only if asked). Sweeps
  pinion, not spur, because pinion swaps are the common drift adjustment.
- Implementation: a `gearing.pinion_sweep(*, base_pinion, spur, internal_ratio,
  tire_diameter_mm, kv, voltage, span=3) -> list[dict]` helper returns the rows
  (pinion + the `compute` fields), so the sweep is unit-testable and the widget just
  renders it.

### D. Uninstall (Tools)

- New item in the **existing** action-button dropdown (the `MenuButtonPopup` menu that
  already holds "Locate existing install…"), enabled only when the tool is installed.
- Confirmation dialog first. New `installer.uninstall(tool_id)`:
  - `source == "existing"` → delete **only** the state file; never touch the user's
    own files.
  - otherwise → `shutil.rmtree(TOOLS_DIR / tool_id)` (if present) **and** delete the
    state file.
- Row refreshes back to "Not installed" / "Install".
- No running-process tracking or kill (`# ponytail:`); if a file is locked the OS
  error reaches the user via a warning box.

### E. Open install folder (Tools)

- New item in the same dropdown, enabled only when installed: opens the parent
  directory of `get_state(tool_id)["exe_path"]` via
  `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`. For "existing" installs this is
  wherever the user keeps the exe.

### F. Remember window size + last tab (App-wide)

- `MainWindow` uses `QSettings` (Qt built-in, no dependency), org/app
  `"RCCentral"/"RCCentral"`.
- `closeEvent`: store `saveGeometry()` (bytes) and the current tab **index**.
- `__init__` (after tabs are built): `restoreGeometry(...)` if present; set the current
  tab to the saved index **clamped to `tabs.count()`** — the Tools tab exists only on
  Windows, so an index saved on Windows can exceed the Linux tab count.

## Testing

- `installer.uninstall`: downloaded path removes dir + state; `source: "existing"`
  removes only state and leaves files; missing tool is a no-op.
- `garage.clone_car`: returns a distinct `id` and the `"<name> (copy)"` name; original
  unchanged.
- `garage.load_car_file`: valid JSON gets a fresh id; a non-dict / invalid JSON raises
  a clear error.
- `gearing.pinion_sweep`: correct row count with clamping at the low end; the base
  pinion row's `compute` values match `gearing.compute` for that pinion.
- UI wiring (buttons, dropdown items, geometry restore) verified via the `verify`
  skill, matching the repo convention that `tests/test_core.py` covers logic, not
  widgets.

## Files touched

- `app/garage.py` — `clone_car`, `load_car_file`.
- `app/gearing.py` — `pinion_sweep`.
- `app/installer.py` — `uninstall`.
- `app/main.py` — Garage buttons (Duplicate, Import, Export JSON filter); GearTab
  what-if table; ToolsTab dropdown items (Uninstall, Open install folder);
  MainWindow `QSettings` geometry + tab.
- `tests/test_core.py` — the logic checks above.

## Out of scope

Everything under Non-goals, and any change to the catalog schema, updater, or the
download/verify/extract pipeline.
