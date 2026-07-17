# Design: Log → Settings, Workshop merge (Garage + Gearing + Tuning)

Date: 2026-07-17 · Status: approved

## Problem

Polish pass on the tab layout (nothing broken). The Log tab is opened rarely
(debugging only) yet holds a top-level spot. Garage, Gear Calculator, and
Tuning feel like separate apps despite sharing one data layer
(`app/garage.py`) — each keeps its own car picker and there is no shared
"selected car".

## Design

Tab bar shrinks 7 → 4: **Tools** (win32-only) | **Manuals** | **Workshop** | **Settings**.

### Settings absorbs Log

- `SettingsTab` hosts sub-tabs **Preferences | Log**.
- Preferences = existing settings + the "Check for updates now" button moved
  from the Log page (it sits next to the startup-check toggle it belongs
  with). Its `self.window()` → `update_ready` wiring survives the move
  unchanged.
- The Log page is the existing `LogTab` minus that button; handler lifecycle
  and `logsetup.buffered_records()` preload unchanged.

### Workshop tab (new `app/ui/workshop.py`)

- Header row `Car: [combo]` (active-car quick switcher) above sub-tabs
  **Garage | Gearing | Tuning** (the existing widgets, modified).
- **Shared active car** persisted in QSettings (`workshop/active_car`,
  defined in `app/ui/common.py`; empty = none). Only WorkshopTab writes the
  key: from the header combo's change handler and from a new
  `GarageTab.car_selected` signal (emitted on user-driven select / save /
  duplicate / import / restore / delete only). Programmatic selection uses a
  silent `GarageTab.open_car(id)` — no sync loops.
- Garage's list stays the car-management UI and syncs two-way with the
  header. `GearTab.car_picker` and `_TuningLog.car_combo` are removed; both
  follow the active car via their existing `showEvent` disk reloads
  (disk stays the sync bus — no new signal plumbing beyond the header).
- Gearing re-seeds its spinboxes only when the active car id actually
  changes, so sub-tab flips never clobber in-progress what-if tweaks.

### Gearing dedupe

- The six gearing fields (pinion/spur/internal ratio/tire Ø/Kv/cells) and
  the presets row leave the Garage form; presets move to the Gearing
  sub-tab. Garage keeps spec fields + notes + run log + car management.
- Garage Save preserves each car's stored `gearing`/`presets` untouched
  (`_form_to_car` already overlays onto the on-disk car).
- Garage's "Open in Gear Calculator" button and the `_open_in_calc`
  callback are deleted — the active car follows automatically.

## Edge cases

- No cars: combo shows "— no car —"; gear save/presets and tuning-log
  disabled; calculator usable stateless; Garage blank.
- Active car deleted (in-app or externally): fall back to "— no car —",
  key cleared; `garage.load_car → None` guards every follower.
- Rename: Save re-reads names into the combo; followers never cache names.
- Saved-tab-index restore: existing clamp accepted (one-time possible
  wrong-tab restore after the 7→4 shrink).

## Verification

Phased implementation (Settings/Log → followers+dedupe → WorkshopTab), with
the full pytest suite green after each phase, then a manual GUI pass:
car creation/selection sync, what-if preservation, preset save keeping
computed gearing, Tuning note appearing in Garage's log, delete fallback,
update-check from Settings, live log streaming, active-car restore on
relaunch.
