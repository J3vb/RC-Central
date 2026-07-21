"""The Garage's chassis-setup panel: a top-down car schematic with the editable
setup values placed around it, each tied to its spot on the car by a leader line.

Everything is painted from the live QPalette (plus the shared accent), so the
panel restyles itself on the Settings dark/light toggle like every stock widget.
Two live highlights ride on the drawing: the focused field's leader line turns
solid accent (you see which corner of the car you're editing), and any field
whose value differs from the saved base setup gets an accent ring on its anchor
plus a dot on its caption (you see what you've drifted from your baseline).
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import garage
from app.ui.common import _ACCENT
from app.ui.tuning import _TUNING_ROWS, _TUNING_TIPS

# Compact captions (with units) for the boxes around the drawing; keyed like
# garage._SETUP_LABELS. A key missing here falls back to its full label, so a
# newly added setup field still gets a box instead of silently vanishing.
_CAPTIONS = {
    "ride_height_front": "Ride ht F (mm)",
    "ride_height_rear": "Ride ht R (mm)",
    "camber_front": "Camber F (°)",
    "camber_rear": "Camber R (°)",
    "toe_front": "Toe F (°)",
    "toe_rear": "Toe R (°)",
    "caster": "Caster (°)",
    "spring_front": "Spring F",
    "spring_rear": "Spring R",
    "shock_oil_front": "Shock oil F",
    "shock_oil_rear": "Shock oil R",
    "rear_diff": "Rear diff",
}

# Which Tuning-chart row advises on each field, for the tooltip. camber_front/
# camber_rear are deliberately unmapped: the chart's only camber row covers
# camber *links* (roll geometry), and wrong advice is worse than none.
_TUNING_MAP = {
    "ride_height_front": "Ride Height (front)",
    "ride_height_rear": "Ride Height (rear)",
    "toe_front": "Front Toe",
    "toe_rear": "Rear Toe",
    "caster": "Caster",
    "spring_front": "Springs (front)",
    "spring_rear": "Springs (rear)",
    "shock_oil_front": "Shock Oil/Damping (front)",
    "shock_oil_rear": "Shock Oil/Damping (rear)",
    "rear_diff": "Rear Diff",
}

# Where each field's leader line lands on the car, in the drawing's unit space
# (x 0..1 across the width, y 0..1 nose-to-tail), plus which side of the car the
# field's box sits on ("l"/"r") so the line always leaves the box's inner edge.
# Keys mirror garage._SETUP_LABELS.
_ANCHORS: dict[str, tuple[float, float, str]] = {
    "camber_front": (0.13, 0.145, "l"),  # FL wheel
    "ride_height_front": (0.30, 0.145, "l"),  # front axle
    "spring_front": (0.40, 0.26, "l"),  # front tower, left mount
    "spring_rear": (0.40, 0.74, "l"),  # rear tower, left mount
    "camber_rear": (0.13, 0.855, "l"),  # RL wheel
    "ride_height_rear": (0.30, 0.855, "l"),  # rear axle
    "toe_front": (0.87, 0.145, "r"),  # FR wheel
    "caster": (0.79, 0.21, "r"),  # FR knuckle / lower arm
    "shock_oil_front": (0.60, 0.26, "r"),  # front tower, right mount
    "shock_oil_rear": (0.60, 0.74, "r"),  # rear tower, right mount
    "toe_rear": (0.87, 0.855, "r"),  # RR wheel
    "rear_diff": (0.50, 0.855, "r"),  # diff on the rear axle centre
}

# Grid rows 1..6 per column, top-to-bottom: front fields at the top (the drawn
# car's nose points up), rear at the bottom, each on the side of its anchor.
_LEFT_KEYS = (
    "camber_front",
    "ride_height_front",
    "spring_front",
    "spring_rear",
    "camber_rear",
    "ride_height_rear",
)
_RIGHT_KEYS = (
    "toe_front",
    "caster",
    "shock_oil_front",
    "shock_oil_rear",
    "toe_rear",
    "rear_diff",
)

# The body's RIGHT flank, nose to tail, as quadTo segments (ctrl_x, ctrl_y, x, y)
# in unit space; the left flank is the same run mirrored (x -> 1 - x) in reverse.
# Concave dips at the wheel rows read as fender cutouts; the bulge is a side pod.
_BODY_RIGHT = (
    (0.74, 0.035, 0.75, 0.095),  # nose flare out to the front fender
    (0.66, 0.145, 0.75, 0.195),  # concave FR wheel cutout
    (0.77, 0.280, 0.71, 0.400),  # taper in behind the front wheels
    (0.80, 0.500, 0.79, 0.620),  # side-pod bulge
    (0.78, 0.700, 0.75, 0.735),  # pod into the rear fender
    (0.66, 0.855, 0.75, 0.945),  # concave RR wheel cutout
    (0.73, 0.975, 0.60, 0.980),  # rounded tail corner
)


def _tooltip(key: str, label: str) -> str:
    """The field's full label plus, where the Tuning chart has a row for it, its
    understeer/oversteer advice and the what-it-does explanation."""
    row_label = _TUNING_MAP.get(key)
    if row_label is None:
        return label
    parts = [label]
    for setting, under, over in _TUNING_ROWS:
        if setting == row_label:
            parts.append(f"If understeering: {under}\nIf oversteering: {over}")
            break
    tip = _TUNING_TIPS.get(row_label)
    if tip:
        parts.append(tip)
    return "\n\n".join(parts)


class _FieldBox(QWidget):
    """A caption over a line edit, sized to occupy exactly one grid cell so the
    leader-line geometry is just this widget's own rect."""

    def __init__(self, caption: str):
        super().__init__()
        self._text = caption
        self.caption = QLabel(caption)
        font = self.caption.font()
        font.setPointSizeF(font.pointSizeF() - 1.5)
        self.caption.setFont(font)
        self.edit = QLineEdit()
        self.edit.setMinimumWidth(56)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addWidget(self.caption)
        layout.addWidget(self.edit)

    def set_drifted(self, drifted: bool) -> None:
        """Append/remove the accent dot marking a value that differs from base."""
        if drifted:
            self.caption.setText(f'{self._text} <span style="color:{_ACCENT}">●</span>')
        else:
            self.caption.setText(self._text)


class SetupDiagramPanel(QWidget):
    """The chassis-setup editor: value boxes flanking a painted car schematic.

    Owns the widgets only — GarageTab wires the base buttons and reads/writes the
    edits through ``fields``, so its save/fill/handler code (and the tests
    addressing ``_setup_fields``) work exactly as when these were form rows.
    """

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(280)
        self._boxes: dict[str, _FieldBox] = {}
        captions = dict(garage._SETUP_LABELS)  # full labels as fallback captions
        for key, label in garage._SETUP_LABELS:
            box = _FieldBox(_CAPTIONS.get(key, captions[key]))
            tip = _tooltip(key, label)
            box.setToolTip(tip)
            box.edit.setToolTip(tip)
            self._boxes[key] = box
        self.fields: dict[str, QLineEdit] = {k: b.edit for k, b in self._boxes.items()}

        # Drift-from-base marks: which fields differ from the saved base setup.
        self._base: dict | None = None
        self._dirty: set[str] = set()
        # Focus highlight: the key whose edit currently has keyboard focus.
        self._focused_key: str | None = None
        self._edit_keys = {box.edit: key for key, box in self._boxes.items()}
        for key, box in self._boxes.items():
            box.edit.textChanged.connect(lambda _t, k=key: self._on_field_edited(k))
            box.edit.installEventFilter(self)

        self.save_base_btn = QPushButton("Save as base")
        self.save_base_btn.setToolTip(
            "Snapshot the setup values above as this car's base setup — e.g. the "
            "factory sheet from the manual — so you can return to them any time."
        )
        self.apply_base_btn = QPushButton("Apply base")
        self.apply_base_btn.setToolTip("Set the fields back to the saved base setup.")
        self.factory_btn = QPushButton("Factory")
        self.factory_btn.setToolTip(
            "Fill the fields from this chassis' verified factory sheet (doesn't save)."
        )
        self.factory_btn.setEnabled(False)  # GarageTab enables it per chassis
        base_row = QHBoxLayout()
        base_row.addWidget(self.save_base_btn)
        base_row.addWidget(self.apply_base_btn)
        base_row.addWidget(self.factory_btn)
        base_row.addStretch(1)

        # Named full-setup snapshots, mirroring the Gear tab's preset row: picking
        # from the combo applies (activated only fires on user picks), Save… asks
        # for a name, Del removes the selected one.
        self.preset_combo = QComboBox()
        self.preset_combo.setToolTip("Apply a saved setup preset.")
        self.save_preset_btn = QPushButton("Save…")
        self.save_preset_btn.setToolTip("Save the current setup as a named preset.")
        self.save_preset_btn.setAccessibleName("Save setup preset")
        self.del_preset_btn = QPushButton("Del")
        self.del_preset_btn.setToolTip("Delete the selected setup preset.")
        self.del_preset_btn.setAccessibleName("Delete setup preset")
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.preset_combo, 1)
        preset_row.addWidget(self.save_preset_btn)
        preset_row.addWidget(self.del_preset_btn)

        self._grid = QGridLayout(self)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(4)
        self._grid.addWidget(QLabel("<b>Chassis setup</b>"), 0, 0, 1, 3)
        for row, (left, right) in enumerate(zip(_LEFT_KEYS, _RIGHT_KEYS), start=1):
            self._grid.addWidget(self._boxes[left], row, 0)
            self._grid.addWidget(self._boxes[right], row, 2)
        self._grid.addLayout(base_row, 7, 0, 1, 3)
        self._grid.addLayout(preset_row, 8, 0, 1, 3)
        for col in range(3):
            self._grid.setColumnStretch(col, 1)
        self._grid.setColumnMinimumWidth(1, 100)  # room for the car + leader lines
        for row in range(1, 7):
            self._grid.setRowStretch(row, 1)
        self._restyle_captions()

    def set_presets(self, presets: list[dict]) -> None:
        """Repopulate the preset combo (placeholder first, name as item data)."""
        self.preset_combo.clear()
        self.preset_combo.addItem("— preset —", None)
        for p in presets:
            self.preset_combo.addItem(p.get("name", "preset"), p.get("name"))

    # -- drift-from-base marks ----------------------------------------------------

    def set_base(self, base: dict | None) -> None:
        """Tell the panel the car's saved base setup (or None); it marks every
        field whose current value differs. Call whenever the shown car changes."""
        self._base = base
        self._dirty = (
            {k for k in self.fields if self._differs(k)} if base is not None else set()
        )
        for key, box in self._boxes.items():
            box.set_drifted(key in self._dirty)
        self.update()

    def _differs(self, key: str) -> bool:
        return (
            str((self._base or {}).get(key) or "").strip()
            != self.fields[key].text().strip()
        )

    def _on_field_edited(self, key: str) -> None:
        if self._base is None:
            return
        drifted = self._differs(key)
        if drifted != (key in self._dirty):
            (self._dirty.add if drifted else self._dirty.discard)(key)
            self._boxes[key].set_drifted(drifted)
            self.update()

    # -- focus highlight ----------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        key = self._edit_keys.get(obj)
        if key is not None:
            if event.type() == QEvent.Type.FocusIn:
                self._focused_key = key
                self.update()
            elif event.type() == QEvent.Type.FocusOut:
                if self._focused_key == key:
                    self._focused_key = None
                self.update()
        return super().eventFilter(obj, event)

    # -- painting -----------------------------------------------------------------

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.PaletteChange:
            self._restyle_captions()
            self.update()  # repaint the car + leader lines in the new theme's colors
        super().changeEvent(event)

    def _restyle_captions(self) -> None:
        """Dim the captions to a 70/30 text/background blend: quieter than the
        edits' text but, unlike any stock palette role, readable in both themes."""
        palette = self.palette()
        text, window = palette.windowText().color(), palette.window().color()
        blend = QColor(
            round(0.7 * text.red() + 0.3 * window.red()),
            round(0.7 * text.green() + 0.3 * window.green()),
            round(0.7 * text.blue() + 0.3 * window.blue()),
        )
        for box in self._boxes.values():
            cap_palette = box.caption.palette()
            cap_palette.setColor(cap_palette.ColorRole.WindowText, blend)
            box.caption.setPalette(cap_palette)

    def _car_rect(self) -> QRectF:
        """The drawing area: centred in the middle column beside the field rows,
        at the car's fixed 1:2 width:length aspect."""
        area = QRectF(self._grid.cellRect(1, 1) | self._grid.cellRect(6, 1))
        w = max(10.0, min(area.width() - 16, (area.height() - 16) / 2))
        return QRectF(
            area.center().x() - w / 2, area.center().y() - w, w, 2 * w
        )

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # The panel paints before its child widgets, so the car and the leader
        # lines land underneath the boxes with no stacking tricks.
        super().paintEvent(event)
        car = self._car_rect()
        if car.width() <= 10:  # layout not realised yet (first show, tests)
            return

        def pt(x: float, y: float) -> QPointF:
            return QPointF(car.left() + x * car.width(), car.top() + y * car.height())

        palette = self.palette()
        outline = QPen(palette.windowText().color(), 1.2)
        subtle = QPen(palette.mid().color(), 1)
        arm_pen = QPen(palette.mid().color(), 1.4)
        accent = QColor(_ACCENT)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # A thin accent rule under the section header.
        header = self._grid.cellRect(0, 0) | self._grid.cellRect(0, 2)
        painter.setPen(QPen(accent, 2))
        painter.drawLine(header.bottomLeft(), header.bottomRight())

        w = car.width()
        # Axles, under the wheels.
        painter.setPen(subtle)
        for y in (0.145, 0.855):
            painter.drawLine(pt(0.13, y), pt(0.87, y))
        # Body: flat nose/tail, fender cutouts at the wheels, side-pod bulge.
        # Base (not Button) fill — Button equals Window in the dark palette, which
        # would leave the body invisible there.
        body = QPainterPath(pt(0.42, 0.02))
        body.lineTo(pt(0.58, 0.02))
        ends = [(0.58, 0.02)] + [(x, y) for _cx, _cy, x, y in _BODY_RIGHT]
        for cx, cy, x, y in _BODY_RIGHT:
            body.quadTo(pt(cx, cy), pt(x, y))
        body.lineTo(pt(0.40, 0.98))
        for i in range(len(_BODY_RIGHT) - 1, -1, -1):  # mirrored left flank, tail up
            cx, cy = _BODY_RIGHT[i][0], _BODY_RIGHT[i][1]
            sx, sy = ends[i]
            body.quadTo(pt(1 - cx, cy), pt(1 - sx, sy))
        body.closeSubpath()
        painter.setPen(outline)
        painter.setBrush(palette.base().color())
        painter.drawPath(body)
        # Racing stripe down the middle, translucent so it works on both themes.
        stripe = QColor(accent)
        stripe.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(stripe)
        painter.drawRoundedRect(
            QRectF(pt(0.475, 0.055), pt(0.525, 0.945)), 0.02 * w, 0.02 * w
        )
        # Canopy over the stripe.
        painter.setPen(QPen(palette.windowText().color(), 1.0))
        painter.setBrush(palette.button().color())
        painter.drawEllipse(QRectF(pt(0.40, 0.40), pt(0.60, 0.56)))
        # Suspension arms, before the wheels so the hubs cover the outer ends.
        painter.setPen(arm_pen)
        for cy in (0.145, 0.855):
            for inner, hub in ((0.70, 0.83), (0.30, 0.17)):
                painter.drawLine(pt(inner, cy - 0.045), pt(hub, cy))
                painter.drawLine(pt(inner, cy + 0.045), pt(hub, cy))
        # Wheels; the front pair is turned a little, because drift.
        painter.setPen(outline)
        painter.setBrush(palette.mid().color())
        for cx, cy, angle in (
            (0.13, 0.145, -12),
            (0.87, 0.145, -12),
            (0.13, 0.855, 0),
            (0.87, 0.855, 0),
        ):
            painter.save()
            painter.translate(pt(cx, cy))
            painter.rotate(angle)
            wheel = QRectF(-0.065 * w, -0.17 * w, 0.13 * w, 0.34 * w)
            painter.drawRoundedRect(wheel, 0.03 * w, 0.03 * w)
            painter.restore()
        # Shock towers with their mount points.
        painter.setBrush(palette.button().color())
        for y in (0.26, 0.74):
            painter.drawLine(pt(0.36, y), pt(0.64, y))
            for x in (0.40, 0.60):
                painter.drawEllipse(pt(x, y), 0.018 * w, 0.018 * w)
        # Motor, driving the rear diff.
        painter.drawRect(QRectF(pt(0.54, 0.62), pt(0.70, 0.72)))
        painter.drawLine(pt(0.62, 0.72), pt(0.50, 0.855))
        painter.drawEllipse(pt(0.50, 0.855), 0.05 * w, 0.05 * w)

        # Leader lines over the car (their anchor dots must not hide under the
        # wheel/tower fills) but still under the child widgets painted after us.
        # The focused field's line goes solid accent; a field that drifted from
        # the base setup gets a hollow accent ring around its dot.
        leader = QPen(palette.mid().color(), 1.0, Qt.PenStyle.DotLine)
        for key, (ax, ay, side) in _ANCHORS.items():
            box = self._boxes.get(key)
            if box is None:  # a new setup field without an anchor: box only, no line
                continue
            geo = box.geometry()
            start = QPointF(
                geo.right() if side == "l" else geo.left(), geo.center().y()
            )
            anchor = pt(ax, ay)
            focused = key == self._focused_key
            painter.setPen(QPen(accent, 1.6) if focused else leader)
            painter.drawLine(start, anchor)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(anchor, 4 if focused else 3, 4 if focused else 3)
            if key in self._dirty:
                painter.setPen(QPen(accent, 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(anchor, 5.5, 5.5)
        painter.end()
