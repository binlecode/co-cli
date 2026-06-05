"""TASK-3: dream daemon observability lands as co-dream*.jsonl under logs/.

Drives the same wiring call the daemon makes in ``_run_foreground`` (the
``setup_observability`` symbol and the ``LOGS_DIR`` constant imported into
``process``) within a temp ``CO_HOME``, then asserts the two structured-log
files appear directly under ``logs/`` — not in the old ``logs/dream/`` subdir —
and that WARNING+ records are captured without a dedicated errors file.

Restores process-global logging state and ``CO_HOME`` on teardown.
"""

import importlib
import json
import logging
import os
from collections.abc import Generator
from pathlib import Path

import pytest

_SPANS_LOGGER = "co_cli.observability.spans"
_SUPPRESS = ["openai", "httpx", "anthropic", "hpack"]


@pytest.fixture(autouse=True)
def _restore_state() -> Generator[None, None, None]:
    original_home = os.environ.get("CO_HOME")
    root = logging.getLogger()
    spans = logging.getLogger(_SPANS_LOGGER)
    root_handlers = list(root.handlers)
    root_level = root.level
    spans_handlers = list(spans.handlers)
    spans_propagate = spans.propagate
    suppress_levels = {name: logging.getLogger(name).level for name in _SUPPRESS}

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

    if original_home is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original_home
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)


def _flush(logger_name: str) -> None:
    for handler in logging.getLogger(logger_name).handlers:
        handler.flush()


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_dream_logs_land_directly_under_logs_dir(tmp_path: Path) -> None:
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.daemons.dream.process as process_mod

    importlib.reload(core_mod)
    importlib.reload(process_mod)

    logs_dir = core_mod.LOGS_DIR
    process_mod.setup_observability(
        logs_dir,
        app_log_name="co-dream.jsonl",
        spans_log_name="co-dream-spans.jsonl",
        errors_log_name=None,
        settings=core_mod.get_settings(),
    )

    logging.getLogger("test.dream").info("dream-info")
    logging.getLogger("test.dream").warning("dream-warning")
    from co_cli.observability.tracing import pop_span, push_span

    push_span("span.dream")
    pop_span()
    _flush("")
    _flush(_SPANS_LOGGER)

    app_path = logs_dir / "co-dream.jsonl"
    spans_path = logs_dir / "co-dream-spans.jsonl"

    app_records = _records(app_path)
    span_records = _records(spans_path)

    assert any(r.get("msg") == "dream-info" for r in app_records)
    assert any(
        r.get("msg") == "dream-warning" and r.get("level") == "WARNING" for r in app_records
    )
    assert any(r.get("name") == "span.dream" and r.get("trace_id") for r in span_records)

    assert not (logs_dir / "dream").exists()
