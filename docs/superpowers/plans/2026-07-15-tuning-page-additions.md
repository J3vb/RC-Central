# Tuning Page Additions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Tuning tab into four sub-tabs — the existing chassis chart (now with explainer tooltips), a shock-oil conversion table, a gyro tuning guide, and a per-car tuning log stored in the Garage's existing car log.

**Architecture:** `TuningTab` (in `app/main.py`) becomes a thin container holding an inner `QTabWidget` with four sub-widgets: `_ChassisGuide` (today's chart moved verbatim + tooltips), `_OilGuide` and `_GyroGuide` (static tables from module constants), and `_TuningLog` (car picker + note entry; entries are ordinary `kind="Tuning"` car-log entries via `garage.new_log_entry` / `garage.save_car`, so backup/restore/export need no changes). `garage.py` is NOT modified.

**Tech Stack:** Python 3, PySide6 (Qt Widgets), pytest with offscreen QApplication.

Spec: `docs/superpowers/specs/2026-07-15-tuning-page-additions-design.md`

## Global Constraints

- All GUI code lives in `app/main.py` (one class per tab — this repo deliberately has no `app/ui/` package). Do not create new modules.
- Follow existing table idioms: `verticalHeader().hide()`, `setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)`, header stretch modes, `setWordWrap(True)` + `resizeRowsToContents()` for long cells.
- `garage.py` must not be modified. Use only: `garage.list_cars()`, `garage.load_car(id) -> dict | None`, `garage.save_car(car)`, `garage.new_log_entry(kind, note)` (returns `{"id", "date", "kind", "note"}` with ISO-UTC date), `garage.new_car(name)`.
- No QSettings persistence anywhere in this feature. No new dependencies.
- Commit messages: plain imperative style matching repo history (e.g. "Add gear ratio chart dialog to GearTab"), ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- UI tests: `monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")` then `QApplication.instance() or QApplication([])`; garage tests also `monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")`.
- Run tests with `python -m pytest tests/test_core.py -q` (full: `python -m pytest tests/ -q`; suite currently 149 tests, all green).

## Preconditions

The v1 Tuning tab (chassis chart, `_TUNING_ROWS`, `TuningTab`, its tests) exists in the working tree but is **uncommitted** on `dev`. Commit it as its own commit before starting Task 1 so each task's diff stays reviewable:

```bash
git add app/main.py tests/test_core.py
git commit -m "Add Tuning tab with drift chassis tuning guide

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

## File Structure

Only two files change, repeatedly:

- `app/main.py` — everything lands in the block between `_TUNING_ROWS` and `class LogTab` (currently ~line 1509–1619). Final layout of that block, top to bottom: `_TUNING_ROWS`, `_TUNING_TIPS` (Task 2), `_OIL_ROWS` (Task 3), `_GYRO_ROWS` (Task 4), `_ChassisGuide` (Task 1), `_OilGuide` (Task 3), `_GyroGuide` (Task 4), `_TuningLog` (Task 5), `TuningTab` container (Task 1).
- `tests/test_core.py` — `test_tuning_tab` is updated in Task 1; new test functions are appended right after it in Tasks 2–5.

`MainWindow` registration (`(self.tuning_tab, "Tuning")`) and `test_tabs_smoke` are already correct and are NOT touched.

---

### Task 1: Restructure TuningTab into a sub-tab container

Move today's chart into `_ChassisGuide`; `TuningTab` becomes a container with an inner `QTabWidget`. Behavior is unchanged — only widget paths move (`tab.table` → `tab.chassis.table`).

**Files:**
- Modify: `app/main.py` (the `class TuningTab` block, ~line 1546)
- Test: `tests/test_core.py::test_tuning_tab` (~line 1314)

**Interfaces:**
- Consumes: existing `_TUNING_ROWS`, `_ACCENT`.
- Produces: `TuningTab.subtabs` (`QTabWidget`), `TuningTab.chassis` (`_ChassisGuide` with attributes `.table`, `.search`, `.radio_both`, `.radio_under`, `.radio_over`). Tasks 3–5 add sub-tabs after "Chassis" via `self.subtabs.addTab(...)`.

- [ ] **Step 1: Update the existing test to the new paths**

In `tests/test_core.py`, rewrite `test_tuning_tab` (same assertions, new paths, plus the sub-tab assert):

```python
def test_tuning_tab(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()

    # the chart now lives on the Chassis sub-tab of an inner QTabWidget
    assert tab.subtabs.tabText(0) == "Chassis"
    assert tab.subtabs.widget(0) is tab.chassis
    chart = tab.chassis

    assert chart.table.rowCount() == len(app_main._TUNING_ROWS) == 18
    assert chart.table.columnCount() == 3
    assert chart.table.horizontalHeaderItem(1).text() == "If understeering"
    assert chart.table.item(0, 0).text() == "Ride Height (front)"
    assert chart.table.item(0, 1).text() == "Decrease"
    assert chart.table.item(0, 2).text() == "Increase"

    # search filters on the setting column, case-insensitive
    chart.search.setText("DIFF")
    visible = [r for r in range(chart.table.rowCount()) if not chart.table.isRowHidden(r)]
    assert visible == [17]  # only Rear Diff
    chart.search.setText("")
    assert not any(chart.table.isRowHidden(r) for r in range(chart.table.rowCount()))

    # a symptom radio highlights only its column; Both clears the highlight
    accent = QColor(app_main._ACCENT)
    chart.radio_under.setChecked(True)
    assert chart.table.item(0, 1).background().color() == accent
    assert chart.table.item(0, 2).background().color() != accent
    chart.radio_over.setChecked(True)
    assert chart.table.item(0, 2).background().color() == accent
    assert chart.table.item(0, 1).background().color() != accent
    chart.radio_both.setChecked(True)
    assert chart.table.item(0, 1).background().color() != accent
    assert chart.table.item(0, 2).background().color() != accent
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core.py::test_tuning_tab -q`
Expected: FAIL — `AttributeError: 'TuningTab' object has no attribute 'subtabs'`

- [ ] **Step 3: Restructure the classes**

In `app/main.py`, rename today's `class TuningTab(QWidget)` to `class _ChassisGuide(QWidget)` (docstring below), keep its entire `__init__`, `_apply_filter`, and `_highlight` bodies unchanged, and add this new `TuningTab` immediately after it:

```python
class _ChassisGuide(QWidget):
    """The understeer/oversteer chart with search filter and symptom highlight."""

    # __init__, _apply_filter, _highlight: unchanged from the previous TuningTab —
    # table + tooltip loop, search box, radios, controls row, title label.


class TuningTab(QWidget):
    """Tuning references in sub-tabs: chassis chart, shock oil, gyro, my log."""

    def __init__(self):
        super().__init__()
        self.subtabs = QTabWidget()
        self.chassis = _ChassisGuide()
        self.subtabs.addTab(self.chassis, "Chassis")
        layout = QVBoxLayout(self)
        layout.addWidget(self.subtabs)
```

(The title label "Drift chassis tuning effects — change one setting at a time" stays inside `_ChassisGuide` — it describes the chart, not the whole page.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core.py::test_tuning_tab -q`
Expected: PASS

- [ ] **Step 5: Run the full suite** (`test_tabs_smoke` must still pass — outer tab unchanged)

Run: `python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Restructure TuningTab into sub-tab container with Chassis guide

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Setting explainer tooltips on the chassis chart

**Files:**
- Modify: `app/main.py` (add `_TUNING_TIPS` after `_TUNING_ROWS`; one-line change in `_ChassisGuide.__init__`'s item loop)
- Test: `tests/test_core.py` (new `test_tuning_explainer_tooltips`, after `test_tuning_tab`)

**Interfaces:**
- Consumes: `_TUNING_ROWS`, `_ChassisGuide` from Task 1.
- Produces: `_TUNING_TIPS: dict[str, str]` — one key per `_TUNING_ROWS` setting name.

- [ ] **Step 1: Write the failing test**

```python
def test_tuning_explainer_tooltips(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    # every chart row has a tip, and no tip is orphaned
    assert set(app_main._TUNING_TIPS) == {r[0] for r in app_main._TUNING_ROWS}

    _ = QApplication.instance() or QApplication([])
    table = app_main.TuningTab().chassis.table
    assert all(table.item(r, 0).toolTip() for r in range(table.rowCount()))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core.py::test_tuning_explainer_tooltips -q`
Expected: FAIL — `AttributeError: module 'app.main' has no attribute '_TUNING_TIPS'`

- [ ] **Step 3: Add the tips dict and apply it**

In `app/main.py`, directly after `_TUNING_ROWS`:

```python
# What each chassis setting physically does — tooltips for the chart's Setting
# column. Front/rear variants of one concept share a text via the _TIP_* locals.
_TIP_RIDE = (
    "Chassis height over the ground at that end. Lowering an end generally adds "
    "grip and reduces body roll at that end; raising does the opposite."
)
_TIP_TRACK = (
    "Distance between left/right contact patches at that end. Wider resists roll "
    "and softens weight transfer at that end; effects differ with corner speed."
)
_TIP_LOWER_SHOCK = (
    "Moving the shock's lower mount changes its lean. More laid-down = softer, "
    "more progressive action at that end; more upright = firmer and more direct."
)
_TIP_SPRINGS = (
    "Roll stiffness at that end. On low-grip drift surfaces the stiffer end "
    "generally slides first."
)
_TIP_OIL = (
    "How fast weight transfers onto that end. Thicker oil slows the transfer "
    "(calmer transitions); thinner speeds it up (snappier response)."
)
_TUNING_TIPS: dict[str, str] = {
    "Ride Height (front)": _TIP_RIDE,
    "Ride Height (rear)": _TIP_RIDE,
    "Ackerman": (
        "How much more the inside wheel steers than the outside wheel in a turn. "
        "More Ackerman sharpens low-speed turn-in; less keeps the wheels more "
        "parallel for smoother high-angle steering."
    ),
    "Front Toe": (
        "Angle of the front wheels vs. the chassis centerline. Toe-out sharpens "
        "initial turn-in; toe-in calms it."
    ),
    "Rear Toe": (
        "Rear toe-in adds rear stability and forward traction; reducing it frees "
        "the rear to rotate."
    ),
    "Caster": (
        "Backward lean of the steering axis. More caster adds straight-line "
        "stability and camber gain while steering; less makes steering more direct."
    ),
    "Track Width (front)": _TIP_TRACK,
    "Track Width (rear — low speed)": _TIP_TRACK,
    "Track Width (rear — high speed)": _TIP_TRACK,
    "Lower Shock Position (front)": _TIP_LOWER_SHOCK,
    "Lower Shock Position (rear)": _TIP_LOWER_SHOCK,
    "Upper Shock Position (rear)": (
        "Same lever as the lower mount: vertical shocks act firmer and more "
        "direct, laid-down shocks act softer initially."
    ),
    "Springs (front)": _TIP_SPRINGS,
    "Springs (rear)": _TIP_SPRINGS,
    "Shock Oil/Damping (front)": _TIP_OIL,
    "Shock Oil/Damping (rear)": _TIP_OIL,
    "Front Camber Link/Roll": (
        "Link length and angle set the roll center and camber gain — how the tire "
        "leans as the chassis rolls. Longer/more parallel links smooth the camber "
        "change and add grip."
    ),
    "Rear Diff": (
        "How tightly the rear wheels are coupled. Tighter (toward spool) drives "
        "both rears equally for predictable rotation; looser lets them "
        "differentiate for more forward bite."
    ),
}
```

In `_ChassisGuide.__init__`, extend the population loop:

```python
        for row, texts in enumerate(_TUNING_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setToolTip(_TUNING_TIPS[text])
                self.table.setItem(row, col, item)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core.py::test_tuning_explainer_tooltips -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Add setting explainer tooltips to chassis tuning chart

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Shock Oil sub-tab

**Files:**
- Modify: `app/main.py` (add `_OIL_ROWS` after `_TUNING_TIPS`; add `_OilGuide` after `_ChassisGuide`; register in `TuningTab.__init__`)
- Test: `tests/test_core.py` (new `test_tuning_oil_guide`)

**Interfaces:**
- Consumes: `TuningTab.subtabs` from Task 1.
- Produces: `TuningTab.oil` (`_OilGuide` with `.table`), sub-tab index 1 titled "Shock Oil", `_OIL_ROWS: list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_tuning_oil_guide(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()
    assert tab.subtabs.tabText(1) == "Shock Oil"
    t = tab.oil.table
    assert t.rowCount() == len(app_main._OIL_ROWS) == 10
    assert t.columnCount() == 2
    assert (t.item(4, 0).text(), t.item(4, 1).text()) == ("30", "350")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core.py::test_tuning_oil_guide -q`
Expected: FAIL — `AttributeError: module 'app.main' has no attribute '_OIL_ROWS'`

- [ ] **Step 3: Implement**

Constant (after `_TUNING_TIPS`):

```python
# (WT, approx. cSt) — the commonly circulated shock-oil conversion; scales
# differ by brand, so the tab carries an "approximate" caption.
_OIL_ROWS: list[tuple[str, str]] = [
    ("10", "100"),
    ("15", "150"),
    ("20", "200"),
    ("25", "275"),
    ("30", "350"),
    ("35", "425"),
    ("40", "500"),
    ("45", "575"),
    ("50", "650"),
    ("60", "800"),
]
```

Widget (after `_ChassisGuide`):

```python
class _OilGuide(QWidget):
    """Shock oil WT ↔ cSt conversion reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_OIL_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("WT", "approx. cSt"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_OIL_ROWS):
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)

        note = QLabel("Approximate — scales differ by brand; check your oil maker's own chart.")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.table)
```

In `TuningTab.__init__`, after the Chassis line:

```python
        self.oil = _OilGuide()
        self.subtabs.addTab(self.oil, "Shock Oil")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core.py::test_tuning_oil_guide -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Add Shock Oil conversion sub-tab to Tuning

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Gyro sub-tab

**Files:**
- Modify: `app/main.py` (add `_GYRO_ROWS` after `_OIL_ROWS`; add `_GyroGuide` after `_OilGuide`; register in `TuningTab.__init__`)
- Test: `tests/test_core.py` (new `test_tuning_gyro_guide`)

**Interfaces:**
- Consumes: `TuningTab.subtabs` from Task 1.
- Produces: `TuningTab.gyro` (`_GyroGuide` with `.table`), sub-tab index 2 titled "Gyro", `_GYRO_ROWS: list[tuple[str, str]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_tuning_gyro_guide(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()
    assert tab.subtabs.tabText(2) == "Gyro"
    t = tab.gyro.table
    assert t.rowCount() == len(app_main._GYRO_ROWS) == 6
    assert t.item(0, 0).text() == "Tail wags / oscillates on straights"
    assert t.item(0, 1).text() == "Lower gain"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core.py::test_tuning_gyro_guide -q`
Expected: FAIL — `AttributeError: module 'app.main' has no attribute '_GYRO_ROWS'`

- [ ] **Step 3: Implement**

Constant (after `_OIL_ROWS`):

```python
# (symptom, gyro adjustment) for drift gyros.
_GYRO_ROWS: list[tuple[str, str]] = [
    ("Tail wags / oscillates on straights", "Lower gain"),
    ("Snap-spins on throttle transitions", "Increase gain"),
    ("Counter-steer too slow, spins before catching", "Increase gain (or faster servo response)"),
    ("Steering fights your inputs, feels robotic", "Lower gain"),
    ("Won't hold deep angle, self-straightens", "Lower gain"),
    ("Wanders at speed, needs constant correction", "Raise gain slightly"),
]
```

Widget (after `_OilGuide`):

```python
class _GyroGuide(QWidget):
    """Drift gyro symptom → gain adjustment reference."""

    def __init__(self):
        super().__init__()
        self.table = QTableWidget(len(_GYRO_ROWS), 2)
        self.table.setHorizontalHeaderLabels(("Symptom", "Adjustment"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, texts in enumerate(_GYRO_ROWS):
            for col, text in enumerate(texts):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeRowsToContents()

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
```

In `TuningTab.__init__`, after the Shock Oil lines:

```python
        self.gyro = _GyroGuide()
        self.subtabs.addTab(self.gyro, "Gyro")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core.py::test_tuning_gyro_guide -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Add Gyro tuning guide sub-tab to Tuning

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: My Log sub-tab (per-car tuning log)

**Files:**
- Modify: `app/main.py` (add `_TuningLog` after `_GyroGuide`; register in `TuningTab.__init__`)
- Test: `tests/test_core.py` (new `test_tuning_log`)

**Interfaces:**
- Consumes: `TuningTab.subtabs`; `garage.list_cars()`, `garage.load_car(id)`, `garage.save_car(car)`, `garage.new_log_entry("Tuning", note)`.
- Produces: `TuningTab.mylog` (`_TuningLog` with `.car_combo`, `.note`, `.add_btn`, `.delete_btn`, `.hint`, `.table`, `._add()`, `._delete()`, `._reload_cars()`), sub-tab index 3 titled "My Log". Final sub-tab order: Chassis / Shock Oil / Gyro / My Log.

- [ ] **Step 1: Write the failing test**

```python
def test_tuning_log(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import garage
    from app import main as app_main

    monkeypatch.setattr(garage, "GARAGE_DIR", tmp_path / "garage")
    _ = QApplication.instance() or QApplication([])

    tab = app_main.TuningTab()
    log = tab.mylog
    names = [tab.subtabs.tabText(i) for i in range(tab.subtabs.count())]
    assert names == ["Chassis", "Shock Oil", "Gyro", "My Log"]

    # empty garage: entry controls disabled, hint shown
    # (isHidden, not isVisible — the tab itself is never shown in offscreen tests)
    assert not log.add_btn.isEnabled()
    assert not log.note.isEnabled()
    assert not log.hint.isHidden()

    # a car with a pre-existing Run entry
    car = garage.new_car("Drift Car")
    car["log"].append(garage.new_log_entry("Run", "pack 1"))
    garage.save_car(car)
    log._reload_cars()
    assert log.add_btn.isEnabled()
    assert log.hint.isHidden()
    assert log.table.rowCount() == 0  # the Run entry is not a tuning entry

    # add a tuning note -> shows in the table and lands on disk as kind="Tuning"
    log.note.setText("front springs softer → better turn-in")
    log._add()
    assert log.note.text() == ""  # input cleared for the next note
    assert log.table.rowCount() == 1
    assert log.table.item(0, 1).text() == "front springs softer → better turn-in"
    saved = garage.load_car(car["id"])
    assert [e["kind"] for e in saved["log"]].count("Tuning") == 1
    assert any(e["kind"] == "Run" for e in saved["log"])

    # delete removes the tuning entry from disk but keeps the Run entry
    log.table.setCurrentCell(0, 1)
    log._delete()
    assert log.table.rowCount() == 0
    saved = garage.load_car(car["id"])
    assert [e["kind"] for e in saved["log"]] == ["Run"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_core.py::test_tuning_log -q`
Expected: FAIL — `AttributeError: 'TuningTab' object has no attribute 'mylog'`

- [ ] **Step 3: Implement**

Widget (after `_GyroGuide`):

```python
class _TuningLog(QWidget):
    """Per-car tuning notes, stored as kind="Tuning" entries in the car's garage log.

    Reuses the Garage's log schema untouched, so entries also appear in the Garage
    tab's log table and ride along with backup/restore/export for free. Add/Delete
    load the car fresh from disk so edits made meanwhile in the Garage tab are
    never clobbered by a stale dict held here.
    """

    def __init__(self):
        super().__init__()
        self._shown: list[dict] = []  # entries behind the table rows, newest first

        self.car_combo = QComboBox()
        self.hint = QLabel("Create a car in the Garage first.")
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. front springs softer → better turn-in")
        self.add_btn = QPushButton("Add")
        self.delete_btn = QPushButton("Delete selected")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("Date", "Note"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        # connect only now that self.table exists (same trap as _CompareDialog)
        self.car_combo.currentIndexChanged.connect(self._render)
        self.add_btn.clicked.connect(self._add)
        self.note.returnPressed.connect(self._add)
        self.delete_btn.clicked.connect(self._delete)

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.note, 1)
        entry_row.addWidget(self.add_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.car_combo)
        layout.addWidget(self.hint)
        layout.addLayout(entry_row)
        layout.addWidget(self.table)
        layout.addWidget(self.delete_btn)
        self._reload_cars()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # cars are created/deleted on the Garage tab; refresh on every switch here
        self._reload_cars()
        super().showEvent(event)

    def _reload_cars(self) -> None:
        current = self.car_combo.currentData()
        self.car_combo.blockSignals(True)
        self.car_combo.clear()
        for car in garage.list_cars():
            self.car_combo.addItem(car.get("name", "Unnamed"), car["id"])
        idx = self.car_combo.findData(current)
        self.car_combo.setCurrentIndex(max(0, idx))  # keep pick; else first car
        self.car_combo.blockSignals(False)
        has_cars = self.car_combo.count() > 0
        for widget in (self.car_combo, self.note, self.add_btn, self.delete_btn):
            widget.setEnabled(has_cars)
        self.hint.setVisible(not has_cars)
        self._render()

    def _render(self) -> None:
        car = garage.load_car(self.car_combo.currentData() or "") or {}
        self._shown = sorted(
            (e for e in car.get("log", []) if e.get("kind") == "Tuning"),
            key=lambda e: e.get("date", ""),
            reverse=True,
        )
        self.table.setRowCount(len(self._shown))
        for row, entry in enumerate(self._shown):
            date = str(entry.get("date", ""))[:10]  # YYYY-MM-DD from the ISO stamp
            self.table.setItem(row, 0, QTableWidgetItem(date))
            self.table.setItem(row, 1, QTableWidgetItem(entry.get("note", "")))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _add(self) -> None:
        note = self.note.text().strip()
        car_id = self.car_combo.currentData()
        if not note or not car_id:
            return
        car = garage.load_car(car_id)
        if car is None:  # deleted on the Garage tab since the picker was filled
            self._reload_cars()
            return
        car.setdefault("log", []).append(garage.new_log_entry("Tuning", note))
        garage.save_car(car)
        self.note.clear()
        self._render()

    def _delete(self) -> None:
        row = self.table.currentRow()
        car_id = self.car_combo.currentData()
        if row < 0 or row >= len(self._shown) or not car_id:
            return
        entry_id = self._shown[row].get("id")
        car = garage.load_car(car_id)
        if car is None:
            self._reload_cars()
            return
        car["log"] = [e for e in car.get("log", []) if e.get("id") != entry_id]
        garage.save_car(car)
        self._render()
```

In `TuningTab.__init__`, after the Gyro lines:

```python
        self.mylog = _TuningLog()
        self.subtabs.addTab(self.mylog, "My Log")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_core.py::test_tuning_log -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: all pass (149 pre-existing + 3 new = 152; count may drift if other work landed)

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Add per-car My Log sub-tab to Tuning

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Final Verification (after all tasks)

1. `python -m pytest tests/ -q` — everything green.
2. GUI check per the project `verify` skill: `uv run python -m app.main` (background), then confirm — Tuning tab shows the four sub-tabs; hovering a chassis Setting cell shows its tooltip; Shock Oil and Gyro tables render; on My Log, pick a car, add a note, see it timestamped; switch to Garage and confirm the same entry in the car's log table with kind "Tuning"; delete it from My Log. Check dark and light themes (Settings tab) for readability. Close via titlebar; expect exit code 0.
