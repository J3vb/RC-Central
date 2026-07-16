# Tuning Chassis Chart Accordion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chassis chart's hover-only tooltips with click-to-expand explanation rows (accordion) so the explainers are discoverable.

**Architecture:** `_ChassisGuide` swaps its `QTableWidget` for a `QTreeWidget` (`self.table` → `self.tree`). Each of the 18 settings is a top-level 3-column item; its single child row spans all columns and holds a word-wrapped italic `QLabel` with the `_TUNING_TIPS` text. `setAnimated(True)` makes expansion slide; native ▸/▾ branch arrows advertise clickability. Filter/highlight/tooltips keep today's behavior on tree APIs.

**Tech Stack:** Python 3, PySide6 (Qt Widgets), pytest with offscreen QApplication.

Spec: `docs/superpowers/specs/2026-07-16-tuning-chassis-accordion-design.md`

## Global Constraints

- All GUI code lives in `app/main.py`; do not create new modules. `_TUNING_ROWS`, `_TUNING_TIPS`, the other sub-tabs, and `garage.py` are untouched.
- No new dependencies. No QSettings persistence.
- Commit messages: plain imperative matching repo history, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- UI tests: `monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")` then `QApplication.instance() or QApplication([])`. Always keep a Python reference to the constructed `TuningTab` — `TuningTab().chassis.tree` without one lets Qt delete the C++ widget tree mid-test (learned 2026-07-16).
- Run tests with `uv run python -m pytest tests/test_core.py -q` (full: `uv run python -m pytest tests/ -q`; suite currently 153 tests, all green).

## File Structure

Only two files change:

- `app/main.py` — the `_ChassisGuide` class body is replaced wholesale; two import lines gain names (`QTreeWidget`, `QTreeWidgetItem` from QtWidgets; `QSize` from QtCore if not already imported).
- `tests/test_core.py` — `test_tuning_tab` and `test_tuning_explainer_tooltips` are ported to tree paths; new `test_tuning_accordion` appended after `test_tuning_explainer_tooltips`.

`TuningTab`, `MainWindow`, and all other tests are NOT touched.

---

### Task 1: Accordion chassis chart

**Files:**
- Modify: `app/main.py` (imports + the whole `class _ChassisGuide` body)
- Test: `tests/test_core.py` (`test_tuning_tab`, `test_tuning_explainer_tooltips`, new `test_tuning_accordion`)

**Interfaces:**
- Consumes: existing `_TUNING_ROWS`, `_TUNING_TIPS`, `_ACCENT`; `TuningTab` container from the sub-tab work (registers `_ChassisGuide()` as `self.chassis`, unchanged).
- Produces: `_ChassisGuide.tree` (`QTreeWidget`), `.search`, `.radio_both/.radio_under/.radio_over` (unchanged names), `._toggle(item, column=0)`, `._apply_filter(text)`, `._highlight(checked)`, `._fit_explanations()`.

- [ ] **Step 1: Port `test_tuning_tab` to tree paths** (same assertions, tree API)

Replace the whole function in `tests/test_core.py`:

```python
def test_tuning_tab(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()

    assert tab.subtabs.tabText(0) == "Chassis"
    assert tab.subtabs.widget(0) is tab.chassis
    chart = tab.chassis
    tree = chart.tree

    assert tree.topLevelItemCount() == len(app_main._TUNING_ROWS) == 18
    assert tree.columnCount() == 3
    assert tree.headerItem().text(1) == "If understeering"
    first = tree.topLevelItem(0)
    assert first.text(0) == "Ride Height (front)"
    assert first.text(1) == "Decrease"
    assert first.text(2) == "Increase"

    # search filters on the setting column, case-insensitive
    chart.search.setText("DIFF")
    visible = [
        i for i in range(tree.topLevelItemCount()) if not tree.topLevelItem(i).isHidden()
    ]
    assert visible == [17]  # only Rear Diff
    chart.search.setText("")
    assert not any(tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount()))

    # a symptom radio highlights only its column; Both clears the highlight
    accent = QColor(app_main._ACCENT)
    chart.radio_under.setChecked(True)
    assert first.background(1).color() == accent
    assert first.background(2).color() != accent
    chart.radio_over.setChecked(True)
    assert first.background(2).color() == accent
    assert first.background(1).color() != accent
    chart.radio_both.setChecked(True)
    assert first.background(1).color() != accent
    assert first.background(2).color() != accent
```

- [ ] **Step 2: Port `test_tuning_explainer_tooltips` to tree paths**

Replace the whole function:

```python
def test_tuning_explainer_tooltips(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app import main as app_main

    # every chart row has a tip, and no tip is orphaned
    assert set(app_main._TUNING_TIPS) == {r[0] for r in app_main._TUNING_ROWS}

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()  # keep a reference or Qt deletes the widget tree
    tree = tab.chassis.tree
    assert all(tree.topLevelItem(i).toolTip(0) for i in range(tree.topLevelItemCount()))
```

- [ ] **Step 3: Add `test_tuning_accordion`** (right after `test_tuning_explainer_tooltips`)

```python
def test_tuning_accordion(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from app import main as app_main

    _ = QApplication.instance() or QApplication([])
    tab = app_main.TuningTab()
    chart = tab.chassis
    tree = chart.tree

    # every setting row carries exactly one collapsed explanation row
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        assert item.childCount() == 1
        assert not item.isExpanded()

    # toggling a row expands it; the child label shows that setting's tip
    first = tree.topLevelItem(0)
    chart._toggle(first)
    assert first.isExpanded()
    label = tree.itemWidget(first.child(0), 0)
    assert isinstance(label, QLabel)
    assert label.text() == app_main._TUNING_TIPS[first.text(0)]

    # toggling again collapses
    chart._toggle(first)
    assert not first.isExpanded()

    # clicking an explanation row is a no-op (only setting rows toggle)
    chart._toggle(first.child(0))
    assert not first.isExpanded()
```

- [ ] **Step 4: Run the three tests to verify they fail**

Run: `uv run python -m pytest tests/test_core.py::test_tuning_tab tests/test_core.py::test_tuning_explainer_tooltips tests/test_core.py::test_tuning_accordion -q`
Expected: 3 FAILED — `AttributeError: '_ChassisGuide' object has no attribute 'tree'` (and `has no attribute '_toggle'`)

- [ ] **Step 5: Extend imports in `app/main.py`**

Add `QTreeWidget` and `QTreeWidgetItem` to the `PySide6.QtWidgets` import block (alphabetical position, after `QTabWidget`). Check the `PySide6.QtCore` import line: if `QSize` is not there, add it (alphabetical).

- [ ] **Step 6: Replace the `_ChassisGuide` class body**

Replace the entire class (docstring, `__init__`, `_apply_filter`, `_highlight`) with:

```python
class _ChassisGuide(QWidget):
    """The understeer/oversteer chart with click-to-expand setting explainers.

    Each setting is a QTreeWidget top-level row; its explanation is a spanned
    child row holding a word-wrapped label, so clicking a setting slides the
    explanation open beneath it (accordion). Hover tooltips are kept as a bonus.
    """

    def __init__(self):
        super().__init__()

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(("Setting", "If understeering", "If oversteering"))
        self.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.tree.setAnimated(True)  # expanding slides the rows below down
        self.tree.setWordWrap(True)  # camber-link cells are long
        self.tree.setExpandsOnDoubleClick(False)  # single click owns toggling
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for texts in _TUNING_ROWS:
            item = QTreeWidgetItem(list(texts))
            item.setToolTip(0, _TUNING_TIPS[texts[0]])
            child = QTreeWidgetItem()
            item.addChild(child)
            self.tree.addTopLevelItem(item)
            child.setFirstColumnSpanned(True)  # only works once the item is in the tree
            tip = QLabel(_TUNING_TIPS[texts[0]])
            tip.setWordWrap(True)
            tip.setContentsMargins(8, 4, 8, 6)
            font = tip.font()
            font.setItalic(True)
            tip.setFont(font)
            self.tree.setItemWidget(child, 0, tip)
        self.tree.itemClicked.connect(self._toggle)
        self.tree.itemActivated.connect(self._toggle)  # Enter key
        self.tree.itemExpanded.connect(lambda _item: self._fit_explanations())

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter settings…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        # Same-parent QRadioButtons are auto-exclusive; no QButtonGroup needed.
        self.radio_both = QRadioButton("Both")
        self.radio_under = QRadioButton("Understeering")
        self.radio_over = QRadioButton("Oversteering")
        self.radio_both.setChecked(True)  # before connect: tree paint not needed yet
        for radio in (self.radio_both, self.radio_under, self.radio_over):
            radio.toggled.connect(self._highlight)

        controls = QHBoxLayout()
        controls.addWidget(self.search, 1)
        controls.addWidget(QLabel("Symptom:"))
        controls.addWidget(self.radio_both)
        controls.addWidget(self.radio_under)
        controls.addWidget(self.radio_over)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drift chassis tuning effects — click a setting for details"))
        layout.addLayout(controls)
        layout.addWidget(self.tree)

    def _toggle(self, item, column: int = 0) -> None:
        if item.parent() is None:  # explanation rows don't toggle anything
            item.setExpanded(not item.isExpanded())

    def _fit_explanations(self) -> None:
        # A word-wrapped QLabel's height depends on the width the view gives it;
        # recompute expanded children so multi-line tips aren't clipped.
        width = self.tree.viewport().width() - self.tree.indentation()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            child = parent.child(0)
            label = self.tree.itemWidget(child, 0)
            if label is not None and parent.isExpanded():
                child.setSizeHint(0, QSize(width, label.heightForWidth(width) + 8))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_explanations()  # re-wrap open explanations at the new width

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(needle not in item.text(0).lower())

    def _highlight(self, checked: bool) -> None:
        if not checked:  # a radio switch fires toggled twice (old off, new on); paint once
            return
        col_on = 1 if self.radio_under.isChecked() else 2 if self.radio_over.isChecked() else None
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            for col in (1, 2):
                if col == col_on:
                    item.setBackground(col, QColor(_ACCENT))
                    item.setForeground(col, QColor("white"))  # readable on accent in both themes
                else:
                    # clear back to theme defaults (None removes the explicit brush)
                    item.setData(col, Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(col, Qt.ItemDataRole.ForegroundRole, None)
```

Note the title label text changed from "change one setting at a time" to
"click a setting for details" — it now teaches the interaction.

- [ ] **Step 7: Run the three tests to verify they pass**

Run: `uv run python -m pytest tests/test_core.py::test_tuning_tab tests/test_core.py::test_tuning_explainer_tooltips tests/test_core.py::test_tuning_accordion -q`
Expected: 3 passed

- [ ] **Step 8: Run the full suite**

Run: `uv run python -m pytest tests/ -q`
Expected: 154 passed (153 + 1 new)

- [ ] **Step 9: Commit**

```bash
git add app/main.py tests/test_core.py
git commit -m "Turn chassis chart explainers into click-to-expand accordion rows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Execution addendum (2026-07-16)

Task 1 was executed as written and then **reworked in a follow-up commit**: the
QTreeWidget accordion live-locks Qt's UIA accessibility bridge after expanding
and collapsing multiple branches by mouse (freezes the whole GUI under screen
readers / UI automation; reproduced with a bare QTreeWidget — core Qt bug on
PySide6 6.11.1). The shipped implementation keeps the chart a QTableWidget and
inserts/removes a spanned explanation row on click, one open at a time, with a
▸/▾ text-prefix affordance and QFontMetrics-computed row height. See the spec's
Implementation addendum for the full rationale and the final interface
(`_toggle_row`, `_setting_name`, `_fit_explanation`).

## Final Verification (after the task)

1. `uv run python -m pytest tests/ -q` — everything green.
2. GUI check per the project `verify` skill: launch, Tuning → Chassis — every row shows a ▸ arrow; clicking a row slides its explanation open (italic, word-wrapped, full width); clicking again closes it; several rows can be open at once; the filter hides rows together with any open explanation; symptom radios still highlight; resize the window with an explanation open (text re-wraps, not clipped). Check dark and light themes. Close via titlebar; exit code 0.
