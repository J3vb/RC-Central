import logging

import pytest

from app import logsetup


@pytest.fixture
def fresh(tmp_path):
    """Run init() against a tmp log dir and restore the root logger afterwards.

    logsetup keeps module-level state (the idempotency flag, the shared buffer,
    and the log path), so each test resets it and detaches whatever init() adds.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level

    orig_dir, orig_file = logsetup.LOG_DIR, logsetup.LOG_FILE
    log_dir = tmp_path / "logs"
    logsetup.LOG_DIR = log_dir
    logsetup.LOG_FILE = log_dir / "rc-central.log"
    logsetup._initialized = False
    logsetup._buffer.clear()

    yield log_dir

    for handler in root.handlers[:]:
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(saved_level)
    logsetup.LOG_DIR, logsetup.LOG_FILE = orig_dir, orig_file
    logsetup._initialized = False
    logsetup._buffer.clear()


def test_init_is_idempotent(fresh):
    root = logging.getLogger()
    before = len(root.handlers)
    logsetup.init()
    after_first = len(root.handlers)
    logsetup.init()  # second call must add nothing
    after_second = len(root.handlers)

    assert after_first > before
    assert after_second == after_first


def test_buffer_captures_records(fresh):
    logsetup.init()
    logging.getLogger("app.test").info("captured into the buffer")

    assert any("captured into the buffer" in line for line in logsetup.buffered_records())


def test_file_gets_written(fresh):
    log_file = fresh / "rc-central.log"
    logsetup.init()
    logging.getLogger("app.test").warning("persisted to the file")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert log_file.exists()
    assert "persisted to the file" in log_file.read_text(encoding="utf-8")


def test_init_survives_unwritable_dir(fresh, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(logsetup.LOG_DIR), "mkdir", boom)

    logsetup.init()  # must not raise
    logging.getLogger("app.test").info("still logging without a file")

    assert not (fresh / "rc-central.log").exists()
    assert any(
        "still logging without a file" in line for line in logsetup.buffered_records()
    )
