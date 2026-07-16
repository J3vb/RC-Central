"""Dark/light palettes and theme application."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.ui.common import _ACCENT


def _make_palette(
    *, window, base, text, button, alt_base, tooltip_base, highlighted_text, disabled
) -> QPalette:
    """The one role→colour sequence both themes share; Highlight is always _ACCENT.

    Every colour a theme varies is a parameter, so the two palettes can't drift in
    which roles they set — only in the values they pass. All args are QColor.
    """
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    p.setColor(QPalette.ColorRole.ToolTipBase, tooltip_base)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, button)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, QColor(_ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, highlighted_text)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return p


def _dark_palette() -> QPalette:
    """A neutral dark grey palette; Highlight is the shared accent (_ACCENT)."""
    window, text = QColor("#353535"), QColor("#ffffff")
    return _make_palette(
        window=window, base=QColor("#2a2a2a"), text=text, button=window,
        alt_base=window, tooltip_base=window, highlighted_text=text,
        disabled=QColor("#7f7f7f"),
    )


def _light_palette() -> QPalette:
    """A cool-neutral light palette that mirrors the dark one and shares the accent.
    Deliberately flat — the native Windows style renders tan tab/header gradients
    and accent-colored data text that clash with the app's identity."""
    return _make_palette(
        window=QColor("#f4f5f7"), base=QColor("#ffffff"), text=QColor("#1c1f23"),
        button=QColor("#e9ebef"), alt_base=QColor("#eef0f3"), tooltip_base=QColor("#ffffff"),
        highlighted_text=QColor("#ffffff"), disabled=QColor("#a0a4ab"),
    )


def apply_theme(app: QApplication, dark: bool) -> None:
    """Paint the app with the dark or light palette on the Fusion style.

    Fusion (not the native OS style) is used in BOTH modes so the app has one flat,
    controlled look with a shared blue accent — the native Windows style renders tan
    tab/header gradients and accent-colored data text that don't match either palette.
    """
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if dark else _light_palette())
