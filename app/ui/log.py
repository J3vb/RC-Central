"""Live log tab: level parsing helpers, the Qt logging bridge, and the Log tab."""

import logging

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import logsetup


_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _line_level(line: str) -> int:
    """Recover a record's level from a formatted line (see logsetup.FORMAT).

    Both the preloaded buffer and the live stream arrive as formatted strings, so
    parsing the level field keeps the tab's filter uniform across the two.
    """
    parts = line.split(" · ", 3)
    if len(parts) >= 2:
        return _LEVEL_NAMES.get(parts[1].strip(), logging.INFO)
    return logging.INFO


class QtLogBridge(QObject):
    """Carries a formatted record from any thread onto the GUI thread.

    A logging.Handler can't itself be a QObject, so the handler holds a bridge and
    emits its signal; Qt's queued connection marshals the string across threads —
    which is what makes it safe for the updater's background thread to log.
    """

    record = Signal(str)


class QtLogHandler(logging.Handler):
    """Root-logger handler that forwards each record to a QtLogBridge signal."""

    def __init__(self, bridge: QtLogBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.record.emit(self.format(record))
        except Exception:
            self.handleError(record)


class LogTab(QWidget):
    """Live application log: preloaded from the in-memory buffer, then streaming.

    A QtLogHandler on the root logger pushes each new record here, so records
    emitted on the updater's background thread arrive safely on the GUI thread.
    """

    _MAX_RECORDS = 5000  # bound memory on a long-running session
    _FILTERS = (
        ("All", logging.NOTSET),
        ("Info+", logging.INFO),
        ("Warnings+", logging.WARNING),
    )

    def __init__(self):
        super().__init__()
        self._records: list[str] = []
        self._min_level = logging.NOTSET

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(self._MAX_RECORDS)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        open_btn = QPushButton("Open log file")
        open_btn.clicked.connect(self._open_log_file)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear)

        self.level_filter = QComboBox()
        for label, _level in self._FILTERS:
            self.level_filter.addItem(label)
        self.level_filter.currentIndexChanged.connect(self._on_filter_changed)

        controls = QHBoxLayout()
        controls.addWidget(open_btn)
        controls.addWidget(copy_btn)
        controls.addWidget(clear_btn)
        controls.addStretch(1)
        controls.addWidget(QLabel("Show:"))
        controls.addWidget(self.level_filter)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view)

        # Bridge live records onto the GUI thread. Parent the bridge so it dies
        # with this widget, and drop the root handler when we're destroyed so a
        # stray record can never reach a deleted bridge.
        self._bridge = QtLogBridge(self)
        self._bridge.record.connect(self._append_record)
        self._handler = QtLogHandler(self._bridge)
        self._handler.setFormatter(
            logging.Formatter(logsetup.FORMAT, datefmt=logsetup.DATE_FORMAT)
        )
        # Snapshot the buffer before attaching, so no record is both preloaded
        # and delivered live.
        self._records.extend(logsetup.buffered_records())
        root = logging.getLogger()
        root.addHandler(self._handler)
        handler = self._handler
        self.destroyed.connect(lambda: root.removeHandler(handler))

        self._rerender()

    def _passes(self, line: str) -> bool:
        return _line_level(line) >= self._min_level

    def _rerender(self) -> None:
        self.view.setPlainText(
            "\n".join(line for line in self._records if self._passes(line))
        )
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_filter_changed(self, index: int) -> None:
        self._min_level = self._FILTERS[index][1]
        self._rerender()

    def _append_record(self, line: str) -> None:
        self._records.append(line)
        if len(self._records) > self._MAX_RECORDS:
            del self._records[: len(self._records) - self._MAX_RECORDS]
        if self._passes(line):
            self.view.appendPlainText(line)
            self._scroll_to_end()

    def _open_log_file(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(logsetup.LOG_FILE))):
            QMessageBox.information(
                self, "Log file", f"The log file is at:\n{logsetup.LOG_FILE}"
            )

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.view.toPlainText())

    def _clear(self) -> None:
        self._records.clear()
        self.view.clear()
