"""Integration tests for the observability bootstrap stack.

Covers the parameterized file-logging filenames (TASK-1), the shared
``setup_observability`` coordinator (TASK-2), and the main app's wiring through
that coordinator (TASK-4). Uses real handlers, real ``logging``/``tracing``
emit, and real files under ``tmp_path`` — no mocks.

These functions mutate process-global logging state (root + spans-logger
handlers, the spans-logger ``propagate`` flag, and the suppressed third-party
logger levels). The autouse fixture snapshots all of it and restores on
teardown so state never bleeds across tests or into the harness.
"""

import importlib
import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.observability.file_logging import setup_file_logging
from co_cli.observability.setup import SUPPRESS_LOGGERS, setup_observability
from co_cli.observability.tracing import pop_span, push_span

_SPANS_LOGGER = "co_cli.observability.spans"


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Generator[None, None, None]:
    root = logging.getLogger()
    spans = logging.getLogger(_SPANS_LOGGER)
    root_handlers = list(root.handlers)
    root_level = root.level
    spans_handlers = list(spans.handlers)
    spans_propagate = spans.propagate
    suppress_levels = {name: logging.getLogger(name).level for name in SUPPRESS_LOGGERS}

    yield

    for handler in list(root.handlers):
        if handler not in root_handlers:
            root.removeHandler(handler)
            handler.close()
    for handler in list(spans.handlers):
        if handler not in spans_handlers:
            spans.removeHandler(handler)
            handler.close()
    root.setLevel(root_level)
    spans.propagate = spans_propagate
    for name, level in suppress_levels.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture(autouse=True)
def _restore_co_home() -> Generator[None, None, None]:
    original = os.environ.get("CO_HOME")
    yield
    changed = os.environ.get("CO_HOME") != original
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original
    if changed:
        import co_cli.config.core as core_mod
        import co_cli.main as main_mod

        importlib.reload(core_mod)
        importlib.reload(main_mod)


def _flush(logger_name: str) -> None:
    for handler in logging.getLogger(logger_name).handlers:
        handler.flush()


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_setup_file_logging_custom_app_name_and_no_errors_file(tmp_path: Path) -> None:
    """TASK-1: app-log filename is caller-controlled; errors file is opt-out."""
    setup_file_logging(tmp_path, app_log_name="co-dream.jsonl", errors_log_name=None)

    logging.getLogger("test.task1").info("hello-task1")
    _flush("")

    app_records = _records(tmp_path / "co-dream.jsonl")
    assert any(r.get("msg") == "hello-task1" for r in app_records)
    assert not (tmp_path / "errors.jsonl").exists()


def test_setup_observability_wires_app_spans_and_suppression(tmp_path: Path) -> None:
    """TASK-2: one call wires app log + separated span stream + noise suppression."""
    setup_observability(
        tmp_path,
        app_log_name="co-dream.jsonl",
        spans_log_name="co-dream-spans.jsonl",
        errors_log_name=None,
        settings=SETTINGS,
    )

    logging.getLogger("test.task2").info("app-record-task2")
    push_span("span.task2")
    pop_span()
    _flush("")
    _flush(_SPANS_LOGGER)

    app_path = tmp_path / "co-dream.jsonl"
    spans_path = tmp_path / "co-dream-spans.jsonl"

    assert any(r.get("msg") == "app-record-task2" for r in _records(app_path))

    span_records = _records(spans_path)
    assert any(r.get("name") == "span.task2" and r.get("trace_id") for r in span_records)

    assert "span.task2" not in app_path.read_text()

    assert logging.getLogger("httpx").level == logging.WARNING


def test_main_app_wiring_targets_co_cli_files(tmp_path: Path) -> None:
    """TASK-4: the real main entrypoint still produces co-cli.jsonl + co-cli-spans.jsonl.

    Drives the actual `main._setup_observability()` under a temp CO_HOME (env +
    module reload — the repo's isolation pattern), confirming the behavior-preserving
    refactor still wires the main app's `co-cli*` files. No monkeypatch (test policy).
    """
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)

    main_mod._setup_observability()

    logging.getLogger("test.task4").info("main-record")
    push_span("span.task4")
    pop_span()
    _flush("")
    _flush(_SPANS_LOGGER)

    logs_dir = core_mod.LOGS_DIR
    assert any(r.get("msg") == "main-record" for r in _records(logs_dir / "co-cli.jsonl"))
    assert any(r.get("name") == "span.task4" for r in _records(logs_dir / "co-cli-spans.jsonl"))


def test_setup_file_logging_idempotent_for_relative_path(tmp_path: Path) -> None:
    """Failure mode: a relative log_dir defeats baseFilename dedup, double-attaching
    the handler so every record is written twice. Two setup calls + one record must
    yield exactly one line."""
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        setup_file_logging(Path("rel-logs"), app_log_name="co-dream.jsonl", errors_log_name=None)
        setup_file_logging(Path("rel-logs"), app_log_name="co-dream.jsonl", errors_log_name=None)

        logging.getLogger("test.dedup").info("once")
        _flush("")

        written = [
            r for r in _records(tmp_path / "rel-logs" / "co-dream.jsonl") if r.get("msg") == "once"
        ]
        assert len(written) == 1
    finally:
        os.chdir(original_cwd)
