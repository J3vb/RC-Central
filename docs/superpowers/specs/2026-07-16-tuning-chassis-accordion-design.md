# Tuning Chassis Chart Accordion Explainers — Design

Date: 2026-07-16
Status: approved (brainstorm with LordJebus, 2026-07-16)

## Purpose

The chassis chart's setting explainers currently exist only as hover tooltips —
invisible to anyone who doesn't think to hover. Replace hover-only discovery with
an accordion: click a setting row and its explanation expands directly beneath it,
pushing the rows below down; click again to collapse. An expand indicator on every
row makes the affordance visible.

Chosen during brainstorm over two rejected alternatives: a static detail panel
under the table, and an ⓘ icon column with popups. The accordion was picked for
its in-context reading flow ("drops down from the row you click").

## Architecture

`_ChassisGuide` swaps its `QTableWidget` for a **`QTreeWidget`** (attribute renamed
`self.table` → `self.tree`). Qt's tree gives the interaction natively:

- Each of the 18 `_TUNING_ROWS` settings is a **top-level item** with the same three
  columns as today (Setting / If understeering / If oversteering).
- Each top-level item has **one child item** holding the `_TUNING_TIPS` explanation,
  spanning all columns (`setFirstColumnSpanned(True)`), rendered as a word-wrapped
  `QLabel` via `setItemWidget` in a dimmed/italic style so it reads as detail.
- `setAnimated(True)` — expanding *slides* the rows down to make room.
- `setRootIsDecorated(True)` — native ▸/▾ branch indicators on every row.
- Single click anywhere on a setting row toggles its explanation
  (`itemClicked` → toggle expansion; `itemActivated` covers Enter for keyboard;
  Left/Right arrow keys work natively). Multiple rows may be open at once.
- Read-only stays: `setEditTriggers(NoEditTriggers)`; header via
  `QTreeWidget.header()` with today's resize modes (col 0 ResizeToContents,
  cols 1–2 Stretch).

Everything else in `_ChassisGuide` keeps its current behavior, ported to tree API:

| Feature | Before (table) | After (tree) |
|---|---|---|
| Search filter | `setRowHidden(row, …)` | `topLevelItem(i).setHidden(…)` — hiding a parent hides its explanation too |
| Symptom highlight | `item(row, col).setBackground(…)` | `topLevelItem(i).setBackground(col, …)` / clear via `setData(col, role, None)` |
| Tooltips | column-0 `setToolTip` | kept unchanged on column 0 of top-level items (bonus for mouse users) |

`_TUNING_ROWS`, `_TUNING_TIPS`, the search box, the symptom radios, the title label,
and the other three sub-tabs (Shock Oil / Gyro / My Log) are untouched.

## Explanation row sizing

Tree items don't word-wrap text, so the child row uses `setItemWidget` with a
`QLabel(wordWrap=True)`. Row height must track the wrapped text: a helper recomputes
the child's `sizeHint` from `label.heightForWidth(viewport width)` and runs on
`itemExpanded` and on `resizeEvent`, so explanations re-wrap when the window
resizes. Collapsed rows cost nothing.

## Testing

Offscreen pytest in `tests/test_core.py`, same idioms as today:

- `test_tuning_tab` — ported to tree paths: `topLevelItemCount`, `topLevelItem(i).text(col)`,
  `.isHidden()`, `.background(col).color()`. Same assertions otherwise (18 rows,
  headers, filter, highlight).
- `test_tuning_explainer_tooltips` — unchanged in spirit: `_TUNING_TIPS` key parity
  with `_TUNING_ROWS`; every top-level item has a column-0 tooltip.
- `test_tuning_accordion` (new) — every setting row has exactly one child; toggling
  a row expands it and its child label shows that setting's `_TUNING_TIPS` text;
  toggling again collapses; filtering to one setting hides the other parents
  (children follow).

## Out of scope

- Accordion treatment for Shock Oil / Gyro tables (no per-row explanations exist).
- One-at-a-time auto-collapse (multiple open is fine; revisit only if users ask).
- Removing the hover tooltips (kept — zero cost).
