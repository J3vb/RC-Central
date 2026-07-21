"""The Garage's chassis-setup panel: a top-down car schematic with the editable
setup values placed around it, each tied to its spot on the car by a leader line.

Everything is painted from the live QPalette (plus the shared accent), so the
panel restyles itself on the Settings dark/light toggle like every stock widget.
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
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


class _FieldBox(QWidget):
    """A caption over a line edit, sized to occupy exactly one grid cell so the
    leader-line geometry is just this widget's own rect."""

    def __init__(self, caption: str):
        super().__init__()
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


class SetupDiagramPanel(QWidget):
    """The chassis-setup editor: value boxes flanking a painted car schematic.

    Owns the widgets only — GarageTab wires the base buttons and reads/writes the
    edits through ``fields``, so its save/fill/handler code (and the tests
    addressing ``_setup_fields``) work exactly as when these were form rows.
    """

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(260)
        self._boxes: dict[str, _FieldBox] = {}
        captions = dict(garage._SETUP_LABELS)  # full labels as fallback captions
        for key, _label in garage._SETUP_LABELS:
            self._boxes[key] = _FieldBox(_CAPTIONS.get(key, captions[key]))
        self.fields: dict[str, QLineEdit] = {k: b.edit for k, b in self._boxes.items()}

        self.save_base_btn = QPushButton("Save as base")
        self.save_base_btn.setToolTip(
            "Snapshot the setup values above as this car's base setup — e.g. the "
            "factory sheet from the manual — so you can return to them any time."
        )
        self.apply_base_btn = QPushButton("Apply base")
        self.apply_base_btn.setToolTip("Set the fields back to the saved base setup.")
        base_row = QHBoxLayout()
        base_row.addWidget(self.save_base_btn)
        base_row.addWidget(self.apply_base_btn)
        base_row.addStretch(1)

        self._grid = QGridLayout(self)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(4)
        self._grid.addWidget(QLabel("<b>Chassis setup</b>"), 0, 0, 1, 3)
        for row, (left, right) in enumerate(zip(_LEFT_KEYS, _RIGHT_KEYS), start=1):
            self._grid.addWidget(self._boxes[left], row, 0)
            self._grid.addWidget(self._boxes[right], row, 2)
        self._grid.addLayout(base_row, 7, 0, 1, 3)
        for col in range(3):
            self._grid.setColumnStretch(col, 1)
        self._grid.setColumnMinimumWidth(1, 84)  # never let the car collapse away
        for row in range(1, 7):
            self._grid.setRowStretch(row, 1)

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.PaletteChange:
            self.update()  # repaint the car + leader lines in the new theme's colors
        super().changeEvent(event)

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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = car.width()
        # Axles, under the wheels.
        painter.setPen(subtle)
        for y in (0.145, 0.855):
            painter.drawLine(pt(0.13, y), pt(0.87, y))
        # Deck.
        painter.setPen(outline)
        painter.setBrush(palette.button().color())
        deck = QRectF(pt(0.24, 0.05), pt(0.76, 0.95))
        painter.drawRoundedRect(deck, 0.08 * w, 0.08 * w)
        # Wheels; the front pair is turned a little, because drift.
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
        for key, (ax, ay, side) in _ANCHORS.items():
            box = self._boxes.get(key)
            if box is None:  # a new setup field without an anchor: box only, no line
                continue
            geo = box.geometry()
            start = QPointF(
                geo.right() if side == "l" else geo.left(), geo.center().y()
            )
            anchor = pt(ax, ay)
            painter.setPen(subtle)
            painter.drawLine(start, anchor)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(_ACCENT))
            painter.drawEllipse(anchor, 3, 3)
        painter.end()
