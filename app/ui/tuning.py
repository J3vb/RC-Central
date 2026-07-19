"""Tuning tab: chassis chart, shock oil / gyro references, and per-car tuning log."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import garage
from app.ui.common import _ACCENT, _ACTIVE_CAR_KEY, _settings, _show_status


# (setting, action if understeering, action if oversteering) — transcribed from the
# community "Drift RC Chassis Tuning Effects" chart. The chart's two conditional
# "Track Width (rear)" rows are split into low/high-speed rows with plain values.
_TUNING_ROWS: list[tuple[str, str, str]] = [
    ("Ride Height (front)", "Decrease", "Increase"),
    ("Ride Height (rear)", "Increase", "Decrease"),
    ("Ackerman", "Increase angle", "Decrease angle"),
    ("Front Toe", "Increase toe-out", "Decrease toe-out"),
    ("Rear Toe", "Decrease toe-in", "Increase toe-in"),
    ("Caster", "Decrease angle", "Increase angle"),
    ("Track Width (front)", "Decrease", "Increase"),
    ("Track Width (rear — low speed)", "Decrease", "Increase"),
    ("Track Width (rear — high speed)", "Increase", "Decrease"),
    ("Lower Shock Position (front)", "Move inward", "Move outward"),
    ("Lower Shock Position (rear)", "Move outward", "Move inward"),
    ("Upper Shock Position (rear)", "Make more vertical", "Make more laid down"),
    ("Springs (front)", "Install softer", "Install stiffer"),
    ("Springs (rear)", "Install stiffer", "Install softer"),
    (
        "Shock Oil/Damping (front)",
        "Install thinner (or larger piston holes)",
        "Install thicker (or smaller piston holes)",
    ),
    (
        "Shock Oil/Damping (rear)",
        "Install thicker (or smaller piston holes)",
        "Install thinner (or larger piston holes)",
    ),
    (
        "Front Camber Link/Roll",
        "Longer link and/or more parallel to lower arm",
        "Shorter link and/or move axis compared to lower arm",
    ),
    ("Rear Diff", "Tighten", "Loosen"),
]


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
        "both rears as one so the rear breaks loose predictably; looser lets them "
        "differentiate, adding rear grip through corners."
    ),
}


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


# (symptom, gyro adjustment) for drift gyros.
_GYRO_ROWS: list[tuple[str, str]] = [
    ("Tail wags / oscillates on straights", "Lower gain"),
    ("Snap-spins on throttle transitions", "Increase gain"),
    ("Counter-steer too slow, spins before catching", "Increase gain (or faster servo response)"),
    ("Steering fights your inputs, feels robotic", "Lower gain"),
    ("Won't hold deep angle, self-straightens", "Lower gain"),
    ("Wanders at speed, needs constant correction", "Raise gain slightly"),
]


# Why each gyro adjustment works — tooltips and click-to-expand explainers for
# the Symptom column, keyed by symptom text (same pattern as _TUNING_TIPS).
_GYRO_TIPS: dict[str, str] = {
    "Tail wags / oscillates on straights": (
        "The gyro is over-correcting: each counter-steer overshoots and the next "
        "correction chases it. Lower the gain until the oscillation dies out."
    ),
    "Snap-spins on throttle transitions": (
        "The rear steps out faster than the gyro catches it when power comes on "
        "or off. More gain gives a bigger, earlier counter-steer so the slide is "
        "caught before it passes the point of no return."
    ),
    "Counter-steer too slow, spins before catching": (
        "The correction arrives too late rather than too small. Raise gain so the "
        "gyro commands more angle sooner; if it still lags, the servo is the "
        "bottleneck — a faster servo fixes what gain can't."
    ),
    "Steering fights your inputs, feels robotic": (
        "The gyro is overriding your hands instead of assisting them. Lower the "
        "gain to hand steering authority back to the transmitter."
    ),
    "Won't hold deep angle, self-straightens": (
        "High gain treats a deliberate deep slide as an error and steers out of "
        "it. Lower the gain so the chassis can sit at bigger angles before the "
        "gyro intervenes."
    ),
    "Wanders at speed, needs constant correction": (
        "Too little stabilizing authority: small yaw disturbances go uncorrected "
        "until you fix them by hand. Raise gain a notch at a time — too far and "
        "wandering turns into tail-wagging."
    ),
}


class _AccordionTable(QWidget):
    """Reference table with click-to-expand explanation rows.

    Click a first-column row and its explanation (from `tips`, keyed by that
    cell's text) drops in as a spanned row directly beneath it — one open at a
    time; clicking another row moves it there. The same text doubles as the
    cell's hover tooltip.
    Implemented on QTableWidget with insertRow/removeRow — QTreeWidget branch
    expand/collapse cycles live-lock Qt's UIA accessibility bridge and freeze
    the GUI under screen readers / UI automation (PySide6 6.11.1, 2026-07-16).
    """

    def __init__(
        self, rows: list[tuple[str, ...]], headers: tuple[str, ...], tips: dict[str, str]
    ):
        super().__init__()
        self._tips = tips
        self._open_row: int | None = None  # table index of the open row, if any

        self.table = QTableWidget(len(rows), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)  # explanation and reference cells can be long
        for row, texts in enumerate(rows):
            for col, text in enumerate(texts):
                item = QTableWidgetItem("▸ " + text if col == 0 else text)
                if col == 0:
                    item.setToolTip(tips[text])
                self.table.setItem(row, col, item)
        # subclasses must call table.resizeRowsToContents() AFTER setting their
        # header resize modes: with word wrap on, row heights depend on column
        # widths, and the pre-stretch defaults produce tall mostly-empty rows
        self.table.cellClicked.connect(self._toggle_row)

    def _row_name(self, row: int) -> str:
        return self.table.item(row, 0).text()[2:]  # strip the "▸ "/"▾ " prefix

    def _toggle_row(self, row: int) -> None:
        if self._open_row is not None and row == self._open_row + 1:
            return  # clicks on the explanation row itself do nothing
        reopen = None if row == self._open_row else row
        if self._open_row is not None:
            was = self._open_row
            self.table.removeRow(was + 1)
            self.table.item(was, 0).setText("▸ " + self._row_name(was))
            self._open_row = None
            if reopen is not None and reopen > was:
                reopen -= 1  # rows below the removed explanation shifted up
        if reopen is None:
            return
        name = self._row_name(reopen)
        exp = QTableWidgetItem(self._tips[name])
        font = exp.font()
        font.setItalic(True)
        exp.setFont(font)
        self.table.insertRow(reopen + 1)
        self.table.setItem(reopen + 1, 0, exp)
        self.table.setSpan(reopen + 1, 0, 1, self.table.columnCount())
        self.table.item(reopen, 0).setText("▾ " + name)
        self._open_row = reopen
        self._fit_explanation()

    def _fit_explanation(self) -> None:
        # the delegate paints wrapped text across the span but sizes the row as
        # a single line, so compute the wrapped height ourselves
        if self._open_row is None:
            return
        row = self._open_row + 1
        item = self.table.item(row, 0)
        metrics = QFontMetrics(item.font())
        width = self.table.viewport().width() - 24
        rect = metrics.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, item.text())
        self.table.setRowHeight(row, rect.height() + 12)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit_explanation()  # re-wrap the open explanation at the new width


class _ChassisGuide(_AccordionTable):
    """The understeer/oversteer chart with search, symptom highlight, and explainers."""

    def __init__(self):
        super().__init__(
            _TUNING_ROWS, ("Setting", "If understeering", "If oversteering"), _TUNING_TIPS
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.resizeRowsToContents()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search settings…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        # Same-parent QRadioButtons are auto-exclusive; no QButtonGroup needed.
        self.radio_both = QRadioButton("Both")
        self.radio_under = QRadioButton("Understeering")
        self.radio_over = QRadioButton("Oversteering")
        self.radio_both.setChecked(True)  # before connect: table paint not needed yet
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
        layout.addWidget(self.table)

    def _apply_filter(self, text: str) -> None:
        if self._open_row is not None:
            self._toggle_row(self._open_row)  # close it; rows are 1:1 settings again
        needle = text.strip().lower()
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, needle not in self._row_name(row).lower())

    def _highlight(self, checked: bool) -> None:
        if not checked:  # a radio switch fires toggled twice (old off, new on); paint once
            return
        col_on = 1 if self.radio_under.isChecked() else 2 if self.radio_over.isChecked() else None
        for row in range(self.table.rowCount()):
            for col in (1, 2):
                item = self.table.item(row, col)
                if item is None:  # the spanned explanation row has no symptom cells
                    continue
                if col == col_on:
                    item.setBackground(QColor(_ACCENT))
                    item.setForeground(QColor("white"))  # readable on accent in both themes
                else:
                    # clear back to theme defaults (None removes the explicit brush)
                    item.setData(Qt.ItemDataRole.BackgroundRole, None)
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)


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


class _GyroGuide(_AccordionTable):
    """Drift gyro symptom → gain adjustment reference with click-to-expand explainers."""

    def __init__(self):
        super().__init__(_GYRO_ROWS, ("Symptom", "Adjustment"), _GYRO_TIPS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.resizeRowsToContents()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drift gyro troubleshooting — click a symptom for details"))
        layout.addWidget(self.table)


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
        self._car_id: str | None = None  # the Workshop's active car

        self.hint = QLabel("Create or select a car in the Garage first.")
        self.note = QLineEdit()
        self.note.setPlaceholderText("e.g. front springs softer → better turn-in")
        self.add_btn = QPushButton("Add")
        self.delete_btn = QPushButton("Delete selected")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("Date", "Note"))
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)

        # connect only now that self.table exists (same trap as _CompareDialog)
        self.add_btn.clicked.connect(self._add)
        self.note.returnPressed.connect(self._add)
        self.delete_btn.clicked.connect(self._delete)

        entry_row = QHBoxLayout()
        entry_row.addWidget(self.note, 1)
        entry_row.addWidget(self.add_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addLayout(entry_row)
        layout.addWidget(self.table)
        layout.addWidget(self.delete_btn)
        self._reload()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # the active car may have changed on the Garage sub-tab; refresh on switch
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        car_id = _settings().value(_ACTIVE_CAR_KEY, "") or None
        car = garage.load_car(car_id) if car_id else None
        self._car_id = car["id"] if car else None
        has_car = self._car_id is not None
        for widget in (self.note, self.add_btn, self.delete_btn):
            widget.setEnabled(has_car)
        self.hint.setVisible(not has_car)
        self._render()

    def _render(self) -> None:
        car = garage.load_car(self._car_id or "") or {}
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
        if not note or not self._car_id:
            return
        car = garage.load_car(self._car_id)
        if car is None:  # deleted on the Garage tab since we last synced
            self._reload()
            return
        car.setdefault("log", []).append(garage.new_log_entry("Tuning", note))
        garage.save_car(car)
        _show_status(self, "Added tuning note", 5000)
        self.note.clear()
        self._render()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._shown) or not self._car_id:
            return
        entry_id = self._shown[row].get("id")
        car = garage.load_car(self._car_id)
        if car is None:
            self._reload()
            return
        car["log"] = [e for e in car.get("log", []) if e.get("id") != entry_id]
        garage.save_car(car)
        _show_status(self, "Deleted log entry", 5000)
        self._render()


class TuningTab(QWidget):
    """Tuning references in sub-tabs: chassis chart, shock oil, gyro, my log."""

    def __init__(self):
        super().__init__()
        self.subtabs = QTabWidget()
        self.chassis = _ChassisGuide()
        self.subtabs.addTab(self.chassis, "Chassis")
        self.oil = _OilGuide()
        self.subtabs.addTab(self.oil, "Shock Oil")
        self.gyro = _GyroGuide()
        self.subtabs.addTab(self.gyro, "Gyro")
        self.mylog = _TuningLog()
        self.subtabs.addTab(self.mylog, "My Log")
        layout = QVBoxLayout(self)
        layout.addWidget(self.subtabs)
