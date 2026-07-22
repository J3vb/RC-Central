"""Setup share card: a garage car rendered as a 1200x675 dark PNG that
carries its own importable payload in a PNG text chunk.

Always drawn with the dark palette (Discord-native look) tinted by the
sharer's accent, whatever theme the app is running. What's drawn is exactly
what's embedded: both come from the same `sharecard.build_card` snapshot.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app import gearing, sharecard
from app.ui.common import _GAP, _MARGIN, _MUTED, _accent
from app.ui.setup_diagram import (
    _ANCHORS,
    _CAPTIONS,
    _LEFT_KEYS,
    _RIGHT_KEYS,
    draw_car_schematic,
)
from app.ui.theme import _dark_palette

_W, _H = 1200, 675
# The schematic and its two flanking value columns, in card pixels.
_CAR = QRectF(755, 140, 190, 380)
_ROW_TOP, _ROW_STEP = 148, 62
_LEFT_COL = QRectF(545, 0, 190, 0)  # right-aligned, leaders run to the car
_RIGHT_COL = QRectF(965, 0, 190, 0)


def _font(base: QFont, size: float, *, bold: bool = False) -> QFont:
    f = QFont(base)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def _gearing_line(card: dict) -> str:
    """"20T / 87T · FDR 8.27 · rollout 22.8 mm" from whatever survived cleaning."""
    g = card["gearing"]
    pinion, spur = g.get("pinion"), g.get("spur")
    if not (pinion and spur):
        return ""
    parts = [f"{pinion:g}T / {spur:g}T"]
    ratio = g.get("internal_ratio")
    if ratio:
        try:
            fdr = gearing.final_drive_ratio(pinion, spur, ratio)
            parts.append(f"FDR {fdr:.2f}")
            tire = g.get("tire_diameter_mm")
            if tire:
                parts.append(f"rollout {gearing.rollout_mm(tire, fdr):.1f} mm")
        except ValueError:
            pass  # nonsense numbers: show the teeth, skip the math
    return " · ".join(parts)


def render_card(car: dict) -> QImage:
    card = sharecard.build_card(car)
    palette = _dark_palette()
    accent = QColor(_accent())
    white = palette.windowText().color()
    muted = QColor(_MUTED)

    img = QImage(_W, _H, QImage.Format.Format_ARGB32)
    img.fill(palette.window().color())
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    base_font = painter.font()

    def text(x: float, y: float, width: float, s: str, f: QFont, color: QColor,
             align=Qt.AlignmentFlag.AlignLeft) -> float:
        painter.setFont(f)
        painter.setPen(color)
        fm = QFontMetrics(f)
        elided = fm.elidedText(s, Qt.TextElideMode.ElideRight, int(width))
        painter.drawText(
            QRectF(x, y, width, fm.height() + 4),
            int(align | Qt.AlignmentFlag.AlignTop),
            elided,
        )
        return y + fm.height()

    # Accent frame.
    painter.setPen(QPen(accent, 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(QRectF(1.5, 1.5, _W - 3, _H - 3))

    # Header: car name in the sharer's colour, chassis under it.
    y = text(44, 30, 660, card["name"] or "Unnamed", _font(base_font, 30, bold=True), accent)
    if card["chassis"]:
        y = text(44, y + 4, 660, card["chassis"], _font(base_font, 15), white)

    # Spec column.
    label_font = _font(base_font, 9)
    value_font = _font(base_font, 12, bold=True)
    y = 168
    for label, value in (
        ("MOTOR", card["motor"]),
        ("ESC", card["esc"]),
        ("SERVO", card["servo"]),
        ("TIRES", card["tires"]),
    ):
        if not value:
            continue
        text(44, y + 3, 80, label, label_font, muted)
        text(132, y, 360, value, value_font, white)
        y += 40
    line = _gearing_line(card)
    if line:
        text(44, y + 3, 80, "GEARING", label_font, muted)
        text(132, y, 400, line, value_font, white)
        y += 40
    if card["notes"]:
        text(44, y + 3, 80, "NOTES", label_font, muted)
        # Word-wrapped, not the single-line helper: notes are the one
        # multi-line field, and elide-by-width would clip line 2+ silently.
        painter.setFont(_font(base_font, 11))
        painter.setPen(white)
        painter.drawText(
            QRectF(132, y, 400, _H - 60 - y),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            | int(Qt.TextFlag.TextWordWrap),
            card["notes"],
        )

    # The car itself, plus each present setup value leadered to its anchor.
    draw_car_schematic(painter, _CAR, palette)
    caption_font = _font(base_font, 9)
    leader = QPen(palette.mid().color(), 1.0, Qt.PenStyle.DotLine)
    for keys, col, edge_x, align in (
        (_LEFT_KEYS, _LEFT_COL, _LEFT_COL.right(), Qt.AlignmentFlag.AlignRight),
        (_RIGHT_KEYS, _RIGHT_COL, _RIGHT_COL.left(), Qt.AlignmentFlag.AlignLeft),
    ):
        for slot, key in enumerate(keys):
            value = card["setup"].get(key)
            if not value:
                continue  # empty fields keep their slot but draw nothing
            row_y = _ROW_TOP + slot * _ROW_STEP
            # .get fallbacks match the setup panel's documented degradation:
            # a new field missing from these dicts must not crash the share.
            text(col.left(), row_y, col.width(), _CAPTIONS.get(key, key), caption_font, muted, align)
            text(col.left(), row_y + 14, col.width(), value, value_font, white, align)
            if key not in _ANCHORS:  # unanchored new field: value only, no leader
                continue
            ax, ay, _side = _ANCHORS[key]
            anchor = QPointF(_CAR.left() + ax * _CAR.width(), _CAR.top() + ay * _CAR.height())
            painter.setPen(leader)
            painter.drawLine(QPointF(edge_x, row_y + 20), anchor)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(anchor, 3, 3)

    text(
        44, _H - 44, _W - 88,
        "RC Central — save this image and drag the file into RC Central to import the full setup",
        _font(base_font, 10), muted, Qt.AlignmentFlag.AlignHCenter,
    )
    painter.end()

    img.setText(sharecard.PNG_TEXT_KEY, sharecard.encode(card))
    return img


class ShareCardDialog(QDialog):
    """Preview one car's setup card and hand it to the community."""

    def __init__(self, car: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share setup card")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._car_name = car.get("name") or "setup"
        self._image = render_card(car)
        self._code = self._image.text(sharecard.PNG_TEXT_KEY)

        self.preview = QLabel()
        self.preview.setPixmap(
            QPixmap.fromImage(self._image).scaled(
                600,
                338,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        note = QLabel(
            "Copy image is picture-only — to share an importable card use "
            "Save PNG… or the setup code."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)

        self.save_btn = QPushButton("Save PNG…")
        self.save_btn.clicked.connect(self._on_save)
        self.copy_image_btn = QPushButton("Copy image")
        self.copy_image_btn.clicked.connect(self._on_copy_image)
        self.copy_code_btn = QPushButton("Copy setup code")
        self.copy_code_btn.clicked.connect(self._on_copy_code)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.setSpacing(_GAP)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.copy_image_btn)
        buttons.addWidget(self.copy_code_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)
        layout.setSpacing(_GAP)
        layout.addWidget(self.preview)
        layout.addWidget(note)
        layout.addLayout(buttons)
        self.setMinimumSize(640, 440)

    def _on_save(self) -> None:
        safe = re.sub(r'[\\/:*?"<>|]+', "_", self._car_name).strip() or "setup"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save setup card", f"{safe} setup card.png", "PNG image (*.png)"
        )
        if not path:
            return
        if not self._image.save(path, "PNG"):
            QMessageBox.warning(self, "Save failed", "Could not write the PNG file.")

    def _on_copy_image(self) -> None:
        QApplication.clipboard().setImage(self._image)

    def _on_copy_code(self) -> None:
        QApplication.clipboard().setText(self._code)
