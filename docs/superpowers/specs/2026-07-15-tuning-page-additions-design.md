# Tuning Page Additions — Design

Date: 2026-07-15
Status: approved (brainstorm with LordJebus, 2026-07-15)

## Purpose

The Tuning tab currently holds one reference: the drift chassis understeer/oversteer
chart. This round adds the rest of what's useful while wrenching at the track:

1. **Shock Oil sub-tab** — WT ↔ cSt conversion reference
2. **Gyro sub-tab** — symptom → gain adjustment reference
3. **Setting explainers** — tooltips on the chassis chart explaining what each setting does
4. **My Log sub-tab** — per-car tuning log ("stiffened front springs → worse"), stored
   in the car's existing Garage log

Explicitly out of scope (decided during brainstorm): drift-specific symptom rows
(chatter, snap-spin, etc.) for the chassis chart.

## Architecture

`TuningTab` becomes a thin container: a `QVBoxLayout` holding one inner `QTabWidget`
with four sub-tabs, in order:

| Sub-tab | Widget | Content |
|---|---|---|
| Chassis | `_ChassisGuide(QWidget)` | The existing chart, search box, symptom radios — moved verbatim from today's `TuningTab`, plus tooltips |
| Shock Oil | `_OilGuide(QWidget)` | Static table from `_OIL_ROWS` + "approximate" note label |
| Gyro | `_GyroGuide(QWidget)` | Static table from `_GYRO_ROWS` |
| My Log | `_TuningLog(QWidget)` | Car picker + note entry + filtered log table |

All in `app/main.py` next to the current `TuningTab`, following its idioms
(read-only `QTableWidget`, hidden vertical header, stretch columns). Attribute
names for tests: `TuningTab.subtabs`, `.chassis`, `.oil`, `.gyro`, `.mylog`.

`MainWindow` registration is unchanged — the outer tab is still `(self.tuning_tab, "Tuning")`.

## Content

### Setting explainers (tooltips on `_ChassisGuide` column-0 items)

A `_TUNING_TIPS: dict[str, str]` keyed by the `_TUNING_ROWS` setting name. One
tooltip per row; rows sharing a concept (front/rear pairs) may share text. Values
are drift-RC oriented; user validates during implementation review:

- **Ride Height (front/rear)**: Chassis height over the ground at that end. Lowering an end generally adds grip and reduces body roll at that end; raising does the opposite.
- **Ackerman**: How much more the inside wheel steers than the outside wheel in a turn. More Ackerman sharpens low-speed turn-in; less keeps the wheels more parallel for smoother high-angle steering.
- **Front Toe**: Angle of the front wheels vs. the chassis centerline. Toe-out sharpens initial turn-in; toe-in calms it.
- **Rear Toe**: Rear toe-in adds rear stability and forward traction; reducing it frees the rear to rotate.
- **Caster**: Backward lean of the steering axis. More caster adds straight-line stability and camber gain while steering; less makes steering more direct.
- **Track Width (front/rear)**: Distance between left/right contact patches at that end. Wider resists roll and softens weight transfer at that end; effects differ with corner speed.
- **Lower Shock Position (front/rear)**: Moving the shock's lower mount changes its lean. More laid-down = softer, more progressive action at that end; more upright = firmer and more direct.
- **Upper Shock Position (rear)**: Same lever: vertical shocks act firmer and more direct, laid-down shocks act softer initially.
- **Springs (front/rear)**: Roll stiffness at that end. On low-grip drift surfaces the stiffer end generally slides first.
- **Shock Oil/Damping (front/rear)**: How fast weight transfers onto that end. Thicker oil slows the transfer (calmer transitions); thinner speeds it up (snappier response).
- **Front Camber Link/Roll**: Link length and angle set the roll center and camber gain — how the tire leans as the chassis rolls. Longer/more parallel links smooth the camber change and add grip.
- **Rear Diff**: How tightly the rear wheels are coupled. Tighter (toward spool) drives both rears equally for predictable rotation; looser lets them differentiate for more forward bite.

### `_OIL_ROWS` — Shock Oil sub-tab

Two columns: WT | approx. cSt. Values are the commonly circulated conversions;
a caption label reads *"Approximate — scales differ by brand; check your oil
maker's own chart."*

| WT | ~cSt |
|---|---|
| 10 | 100 |
| 15 | 150 |
| 20 | 200 |
| 25 | 275 |
| 30 | 350 |
| 35 | 425 |
| 40 | 500 |
| 45 | 575 |
| 50 | 650 |
| 60 | 800 |

### `_GYRO_ROWS` — Gyro sub-tab

Two columns: Symptom | Adjustment.

| Symptom | Adjustment |
|---|---|
| Tail wags / oscillates on straights | Lower gain |
| Snap-spins on throttle transitions | Increase gain |
| Counter-steer too slow, spins before catching | Increase gain (or faster servo response) |
| Steering fights your inputs, feels robotic | Lower gain |
| Won't hold deep angle, self-straightens | Lower gain |
| Wanders at speed, needs constant correction | Raise gain slightly |

## My Log sub-tab

**Data — no new schema.** A tuning entry is a normal car log entry
(`garage.new_log_entry("Tuning", note)`) appended to the car's existing `log`
list and saved with `garage.save_car`. Consequences, all free: entries appear in
the Garage tab's log table (kind column shows "Tuning"), and backup/restore,
export, and duplicate-with-empty-log semantics need no changes. `garage.py` is
not modified.

**UI (top to bottom):**
- Car picker `QComboBox`, reloaded on `showEvent` (GearTab's idiom) so it stays
  fresh as cars are added/deleted.
- Entry row: one-line note `QLineEdit` (placeholder e.g. "front springs softer → better turn-in")
  + "Add" button. No structured better/worse field — the note carries it.
- Log table (Date | Note): the picked car's `log` filtered to `kind == "Tuning"`,
  newest first. "Delete selected" button below.

**Staleness rule:** Add/Delete must load the car fresh from disk
(`garage.load_car`), modify, save — never save a dict held since the tab was
last shown, so edits made meanwhile in the Garage tab aren't clobbered.

**Edge cases:**
- Empty garage → picker empty, note field + buttons disabled, hint label
  "Create a car in the Garage first."
- Picked car deleted elsewhere → `showEvent` reload re-populates; `load_car`
  returning `None` on Add/Delete is treated as "car gone": refresh, no crash.

## Error handling

Reference sub-tabs are static. My Log's only failure surface is car load/save,
which reuses `garage.py`'s existing tested functions with the staleness rule above.

## Testing

In `tests/test_core.py`, offscreen-QApplication idiom:
- Update the existing `test_tuning_tab` for the new paths (`tab.chassis.table`,
  `tab.chassis.search`, radios) — behavior assertions unchanged.
- Sub-tab names/order: `["Chassis", "Shock Oil", "Gyro", "My Log"]`.
- Explainers: column-0 items have non-empty tooltips.
- Oil/Gyro: row counts and a spot-checked cell each.
- My Log round-trip (tmp `GARAGE_DIR`): add entry → appears in table and in the
  car JSON with `kind="Tuning"`; a pre-existing "Run" entry does NOT appear;
  delete removes it from disk; empty garage → controls disabled.

No QSettings, no new dependencies.
