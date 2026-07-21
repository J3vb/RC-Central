"""Dark/light palettes, the app stylesheet, and theme application."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.ui.common import _accent, _MUTED, _on_accent


def _make_palette(
    *, window, base, text, button, alt_base, tooltip_base, disabled,
    border, hover, pressed,
) -> QPalette:
    """The one role→colour sequence both themes share; Highlight is the (possibly
    user-customized) accent, read live via _accent().

    Every colour a theme varies is a parameter, so the two palettes can't drift in
    which roles they set — only in the values they pass. All args are QColor.
    HighlightedText is not a parameter: it tracks the accent's lightness (black on
    light accents, white on dark ones), not the theme.

    Mid/Midlight/Dark are set deliberately (border/hover/pressed) instead of letting
    Fusion derive them: the stylesheet below reads them back via QSS palette(mid),
    palette(midlight) and palette(dark), which is what lets ONE static stylesheet
    serve both themes with no per-theme string building.
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
    p.setColor(QPalette.ColorRole.Highlight, QColor(_accent()))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(_on_accent()))
    p.setColor(QPalette.ColorRole.Mid, border)
    p.setColor(QPalette.ColorRole.Midlight, hover)
    p.setColor(QPalette.ColorRole.Dark, pressed)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    return p


def _dark_palette() -> QPalette:
    """A neutral dark grey palette; Highlight is the shared accent (_accent())."""
    window, text = QColor("#353535"), QColor("#ffffff")
    return _make_palette(
        window=window, base=QColor("#2a2a2a"), text=text, button=window,
        alt_base=QColor("#2f2f2f"), tooltip_base=window,
        disabled=QColor("#7f7f7f"),
        border=QColor("#4a4a4a"), hover=QColor("#424242"), pressed=QColor("#2b2b2b"),
    )


def _light_palette() -> QPalette:
    """A cool-neutral light palette that mirrors the dark one and shares the accent.
    Deliberately flat — the native Windows style renders tan tab/header gradients
    and accent-colored data text that clash with the app's identity."""
    return _make_palette(
        window=QColor("#f4f5f7"), base=QColor("#ffffff"), text=QColor("#1c1f23"),
        button=QColor("#e9ebef"), alt_base=QColor("#eef0f3"), tooltip_base=QColor("#ffffff"),
        disabled=QColor("#a0a4ab"),
        border=QColor("#c9ccd2"), hover=QColor("#dfe2e8"), pressed=QColor("#d3d7de"),
    )


# One static stylesheet for both themes: every colour is either a QSS palette(role)
# lookup (resolved from whichever palette is active when the sheet is applied) or a
# deliberately theme-invariant literal (_MUTED). Each block is independent — if a
# widget type misrenders, delete its block and that widget reverts to plain Fusion.
_QSS = f"""
/* --- Tabs: flat underline style; the main tab bar is larger via #mainTabs --- */
QTabWidget::pane {{ border: none; border-top: 1px solid palette(mid); }}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 5px 12px;
    margin-right: 2px;
    color: {_MUTED};
}}
QTabBar::tab:selected {{
    color: palette(text);
    border-bottom: 2px solid palette(highlight);
}}
QTabBar::tab:hover:!selected {{
    color: palette(text);
    background: palette(midlight);
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabWidget#mainTabs > QTabBar::tab {{ padding: 8px 18px; font-weight: 600; }}

/* --- Buttons --- */
QPushButton, QToolButton {{
    background: palette(button);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 4px 10px;
}}
QToolButton {{ padding: 3px 8px; }}  /* denser: these live inside table rows */
QPushButton:hover, QToolButton:hover {{ background: palette(midlight); }}
QPushButton:pressed, QToolButton:pressed {{ background: palette(dark); }}
QPushButton:focus, QToolButton:focus {{ border-color: palette(highlight); }}
QPushButton:disabled, QToolButton:disabled {{
    background: transparent;
    border-color: palette(midlight);
}}
/* The setup panel packs four buttons into a narrow column; tighter padding keeps
   "Save as base" … "Reset" un-clipped at the default window width. */
QWidget#setupPanel QPushButton {{ padding: 4px 6px; }}
/* Toolbar actions (PDF viewer) stay flat; the borders above would make them boxy. */
QToolBar {{ border: none; padding: 2px; spacing: 4px; }}
QToolBar QToolButton {{ background: transparent; border: none; padding: 3px 6px; }}
QToolBar QToolButton:hover {{ background: palette(midlight); border-radius: 4px; }}

/* --- Inputs. Spin boxes are deliberately NOT here: styling them without also
   taking over their ::up-button/::down-button subcontrols renders the arrows
   cramped, so they keep the native Fusion look (the exact bug-farm the design
   said to stay out of). --- */
QLineEdit, QPlainTextEdit, QComboBox {{
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: palette(highlight);
}}
/* Editable combos and spin boxes embed a QLineEdit; without this it matches the
   rule above and draws its own border inside the outer widget (border-in-border). */
QComboBox QLineEdit, QAbstractSpinBox QLineEdit {{
    border: none;
    background: transparent;
    padding: 0;
}}

/* --- Item views --- */
QTableView {{
    border: 1px solid palette(mid);
    gridline-color: palette(midlight);
    selection-background-color: palette(highlight);
    selection-color: palette(highlighted-text);
}}
QListView {{ border: 1px solid palette(mid); background: palette(base); }}
QListView::item {{ padding: 3px 4px; }}
QHeaderView::section {{
    background: palette(window);
    border: none;
    border-bottom: 1px solid palette(mid);
    padding: 4px 8px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: palette(window); border: none; }}

/* --- Progress --- */
QProgressBar {{
    border: 1px solid palette(mid);
    border-radius: 4px;
    background: palette(base);
    text-align: center;
}}
QProgressBar::chunk {{ background: palette(highlight); border-radius: 3px; }}

/* --- Chrome --- */
QToolTip {{
    background: palette(base);
    color: palette(text);
    border: 1px solid palette(mid);
    padding: 4px 8px;
}}
QStatusBar {{ border-top: 1px solid palette(mid); }}
QLabel#mutedLabel {{ color: {_MUTED}; }}
"""


def apply_theme(app: QApplication, dark: bool) -> None:
    """Paint the app with the dark or light palette on the Fusion style.

    Fusion (not the native OS style) is used in BOTH modes so the app has one flat,
    controlled look with a shared blue accent — the native Windows style renders tan
    tab/header gradients and accent-colored data text that don't match either palette.

    The stylesheet is re-set after the palette on every call: QSS palette(role)
    references resolve when the sheet is applied, so re-applying is what makes a
    live dark/light toggle restyle everything.
    """
    app.setStyle("Fusion")
    app.setPalette(_dark_palette() if dark else _light_palette())
    app.setStyleSheet(_QSS)
