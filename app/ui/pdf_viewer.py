"""In-app PDF viewer window for downloaded manuals.

QtPdf ships with the pyside6 metapackage but needs native libs (e.g. libEGL) that
minimal Linux installs may lack, so the import is guarded: when it fails, callers
fall back to the OS default viewer via is_available().
"""

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QLabel, QLineEdit, QMainWindow, QSizePolicy, QToolBar, QWidget

from app.ui.common import _settings, app_icon

log = logging.getLogger(__name__)

try:
    from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
    from PySide6.QtPdfWidgets import QPdfView

    _QTPDF_ERROR: str | None = None
except Exception as e:  # ImportError, but also broken Qt plugin setups
    _QTPDF_ERROR = str(e)
    log.warning("QtPdf unavailable, PDFs will open in the system viewer: %s", e)

_GEOMETRY_KEY = "pdf_viewer/geometry"  # shared by all viewer windows; last closed wins
_ZOOM_STEP = 1.25
_ZOOM_MIN, _ZOOM_MAX = 0.125, 8.0


def is_available() -> bool:
    """Whether the in-app viewer can render at all on this machine."""
    return _QTPDF_ERROR is None


class PdfViewerWindow(QMainWindow):
    """A non-modal top-level window rendering one PDF. Parented to the main window
    so it closes with the app, but QMainWindow keeps the Qt.Window flag, so it
    stays a separate window (read a manual while using the Workshop tab). Raises
    from __init__ when the file can't be loaded, so a broken window never shows
    and the caller can fall back to the system viewer."""

    def __init__(self, path: Path, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._path = path
        self.setWindowTitle(f"{title} — RC Central")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # free render resources

        # The document must outlive the view (parented to the window, never shared).
        self._doc = QPdfDocument(self)
        try:
            err = self._doc.load(str(path))
            if err != QPdfDocument.Error.None_:
                raise ValueError(f"QPdfDocument.load: {err.name}")
        except Exception:
            # Don't leave a hidden, parented QMainWindow behind: __init__ already ran
            # super().__init__(parent), so without this the failed window would sit in
            # the parent's children forever, never shown or deleted.
            self._doc.close()
            self.deleteLater()
            raise

        self._text_pages: tuple[int, int] | None = None  # computed lazily on first search
        self.view = QPdfView(self)
        self.view.setDocument(self._doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)  # continuous scrolling
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.setCentralWidget(self.view)

        self._build_toolbar()

        geometry = _settings().value(_GEOMETRY_KEY)
        if isinstance(geometry, (QByteArray, bytes, bytearray)):
            self.restoreGeometry(geometry)  # guard: a corrupted value must not raise here,
        else:
            self.resize(700, 850)  # which would leave this window (and its open PDF handle) leaked
        self.setMinimumSize(420, 480)

    def _build_toolbar(self) -> None:
        bar = QToolBar("View")
        bar.setMovable(False)
        bar.setFloatable(False)

        zoom_out = bar.addAction("−", lambda: self._zoom(1 / _ZOOM_STEP))
        zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_in = bar.addAction("+", lambda: self._zoom(_ZOOM_STEP))
        zoom_in.setShortcut(QKeySequence.StandardKey.ZoomIn)
        self._fit_action = bar.addAction("Fit width", self._toggle_fit)
        self._fit_action.setCheckable(True)
        self._fit_action.setChecked(True)

        bar.addSeparator()
        nav = self.view.pageNavigator()
        self._prev_action = bar.addAction("◀", lambda: self._go(-1))
        self._prev_action.setShortcut(QKeySequence.StandardKey.MoveToPreviousPage)
        self._page_label = QLabel()
        bar.addWidget(self._page_label)
        self._next_action = bar.addAction("▶", lambda: self._go(+1))
        self._next_action.setShortcut(QKeySequence.StandardKey.MoveToNextPage)
        nav.currentPageChanged.connect(self._update_page_label)
        self._doc.pageCountChanged.connect(self._update_page_label)
        self._update_page_label()

        bar.addSeparator()
        # QPdfSearchModel scans pages in the background; count() grows as it goes
        # and the view draws the match highlights itself once the model is attached.
        self._search_model = QPdfSearchModel(self)
        self._search_model.setDocument(self._doc)
        self.view.setSearchModel(self._search_model)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(180)
        bar.addWidget(self._search_edit)
        self._find_prev = bar.addAction("▲", lambda: self._find(-1))
        self._find_prev.setShortcut(QKeySequence.StandardKey.FindPrevious)
        self._find_prev.setToolTip("Previous match (Shift+F3)")
        self._find_next = bar.addAction("▼", lambda: self._find(+1))
        self._find_next.setShortcut(QKeySequence.StandardKey.FindNext)
        self._find_next.setToolTip("Next match (F3)")
        self._search_label = QLabel()
        bar.addWidget(self._search_label)
        self._search_edit.textChanged.connect(self._search_changed)
        self._search_edit.returnPressed.connect(lambda: self._find(+1))
        self._search_model.countChanged.connect(self._update_search_label)
        focus_find = QAction(self)  # window-level Ctrl+F
        focus_find.setShortcut(QKeySequence.StandardKey.Find)
        focus_find.triggered.connect(self._focus_search)
        self.addAction(focus_find)
        self._update_search_label()

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(
            "Open externally",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path))),
        )
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

    def _zoom(self, factor: float) -> None:
        # A manual zoom leaves fit-to-width mode; keep the checkbox honest.
        current = self.view.zoomFactor()
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self._fit_action.setChecked(False)
        self.view.setZoomFactor(max(_ZOOM_MIN, min(_ZOOM_MAX, current * factor)))

    def _toggle_fit(self) -> None:
        if self._fit_action.isChecked():
            self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        else:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)

    def _go(self, delta: int) -> None:
        nav = self.view.pageNavigator()
        page = nav.currentPage() + delta
        if 0 <= page < self._doc.pageCount():
            # jump() requires an explicit location; top of the page reads naturally.
            nav.jump(page, QPointF(0, 0), nav.currentZoom())

    def _search_changed(self, text: str) -> None:
        self._search_model.setSearchString(text)
        self.view.setCurrentSearchResultIndex(-1)  # index into the old results is stale
        self._update_search_label()

    def _find(self, delta: int) -> None:
        count = self._search_model.count()
        if count == 0:
            return
        cur = self.view.currentSearchResultIndex()
        # From "no selection" (-1), forward starts at the first match, backward at the last.
        i = (cur + delta) % count if cur >= 0 else (0 if delta > 0 else count - 1)
        self.view.setCurrentSearchResultIndex(i)
        link = self._search_model.resultAtIndex(i)
        nav = self.view.pageNavigator()
        nav.jump(link.page(), link.location(), nav.currentZoom())
        self._update_search_label()

    def _focus_search(self) -> None:
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _searchable_pages(self) -> tuple[int, int]:
        """(pages with a text layer, total pages). Many vendor manuals are image-only
        scans — sometimes with a lone text page — where search silently sees nothing;
        this lets the UI say so instead of showing a bare '0 / 0'."""
        if self._text_pages is None:
            total = self._doc.pageCount()
            with_text = sum(bool(self._doc.getAllText(p).text().strip()) for p in range(total))
            self._text_pages = (with_text, total)
        return self._text_pages

    def _update_search_label(self, *_args) -> None:
        count = self._search_model.count()
        if not self._search_edit.text():
            self._search_label.setText("")
        elif count == 0:
            # May flash briefly while the background scan is still running; the
            # countChanged signal rewrites it as soon as the first match lands.
            with_text, total = self._searchable_pages()
            if with_text == 0:
                hint = "No text in this PDF"
            elif with_text * 2 < total:  # ponytail: crude cut; refine if manuals land near 50%
                hint = "No matches (most pages are scans)"
            else:
                hint = "No matches"
            self._search_label.setText(f" {hint} ")
        else:
            self._search_label.setText(f" {self.view.currentSearchResultIndex() + 1} / {count} ")
        self._find_prev.setEnabled(count > 0)
        self._find_next.setEnabled(count > 0)

    def _update_page_label(self, *_args) -> None:
        nav = self.view.pageNavigator()
        page, count = nav.currentPage(), self._doc.pageCount()
        self._page_label.setText(f" {page + 1} / {count} ")
        self._prev_action.setEnabled(page > 0)
        self._next_action.setEnabled(page < count - 1)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        _settings().setValue(_GEOMETRY_KEY, self.saveGeometry())
        # Release the file handle now: WA_DeleteOnClose only schedules deleteLater(),
        # which runs too late for a caller (e.g. delete-the-downloaded-PDF) that wants
        # the file unlocked right after close() returns.
        self._doc.close()
        super().closeEvent(event)
