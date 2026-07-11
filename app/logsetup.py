"""App-wide logging: rotating file + an in-memory buffer, configured once.

Kept Qt-free on purpose so it stays importable and testable without a GUI. The
Qt bridge that streams records live into the Log tab lives in app/main.py; this
module only owns the root-logger configuration, the shared format, and the
buffer the tab preloads from.
"""

import logging
from collections import deque
from logging.handlers import RotatingFileHandler

from app import __version__
from app.paths import data_dir

LOG_DIR = data_dir() / "logs"
LOG_FILE = LOG_DIR / "rc-central.log"

# time · level · logger · message — the Log tab parses the level field back out
# of formatted lines, so keep the " · " separator in sync with main.py.
FORMAT = "%(asctime)s · %(levelname)s · %(name)s · %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_BUFFER_MAXLEN = 1000
_buffer: "deque[str]" = deque(maxlen=_BUFFER_MAXLEN)
_initialized = False


class BufferHandler(logging.Handler):
    """Keep the last N formatted records in memory.

    Lets the Log tab preload everything logged before it existed — the startup
    cleanup and the first update check both happen before the window is built.
    """

    def __init__(self, buffer: "deque[str]"):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:  # never let logging take down the caller
            self.handleError(record)


def buffered_records() -> list[str]:
    """A snapshot of the formatted records buffered so far."""
    return list(_buffer)


def init() -> None:
    """Configure the root logger once. Idempotent, and never raises.

    Attaches an in-memory buffer, a console stream, and — when the log directory
    is writable — a rotating file handler. The file is the only sink that
    survives the window closing, so it is what captures the on-exit
    ``apply_pending()`` swap. If the directory can't be created (locked-down box,
    read-only volume) the app still runs with console + buffer only.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(FORMAT, datefmt=DATE_FORMAT)

    # Buffer first, so it captures records even if the file handler can't open.
    buffer_handler = BufferHandler(_buffer)
    buffer_handler.setFormatter(formatter)
    root.addHandler(buffer_handler)

    # Console is handy when running from source; keep it at INFO to stay quiet.
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).warning(
            "could not open log file at %s; continuing with console + buffer only",
            LOG_FILE,
            exc_info=True,
        )

    # urllib3's per-request DEBUG chatter would drown the app's own records.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "logging started (RC Central v%s); file=%s", __version__, LOG_FILE
    )
