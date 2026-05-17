"""Pytest harness plugin for low-level per-test diagnostics.

Configures the structured-log tracing pipeline during pytest and prints a
compact per-test summary. Slow or failing tests also get span-by-span detail
in the pytest log stream.

Reads records from the spans JSON log written by
``co_cli.observability.tracing.setup_log``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from co_cli.config.core import LOGS_DIR
from co_cli.observability import tracing

_test_outcomes: dict[str, str] = {}

_SLOW_MS = int(os.getenv("CO_PYTEST_TRACE_SLOW_MS", "2000"))
_DETAIL_LIMIT = int(os.getenv("CO_PYTEST_TRACE_DETAIL_LIMIT", "12"))

_SPANS_LOG = LOGS_DIR / "co-cli-spans.jsonl"


def _is_eval_path(path_str: str) -> bool:
    parts = Path(path_str).parts
    return "evals" in parts


def _harness_enabled(config: pytest.Config) -> bool:
    if os.getenv("CO_PYTEST_HARNESS", "1") == "0":
        return False
    args = [str(a) for a in config.args]
    return not (args and all(_is_eval_path(a) for a in args if not a.startswith("-")))


@dataclass
class _SpanRow:
    trace_id: str | None
    name: str
    kind: str
    duration_ms: float | None
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    status_msg: str | None = None


def _compact_json(value: Any, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _flush_spans_logger() -> None:
    for handler in logging.getLogger("co_cli.observability.spans").handlers:
        handler.flush()


def _log_high_water_mark() -> int:
    if not _SPANS_LOG.exists():
        return 0
    return _SPANS_LOG.stat().st_size


def _records_since(byte_offset: int) -> list[_SpanRow]:
    _flush_spans_logger()
    if not _SPANS_LOG.exists():
        return []
    rows: list[_SpanRow] = []
    with _SPANS_LOG.open("r", encoding="utf-8") as f:
        f.seek(byte_offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(
                _SpanRow(
                    trace_id=rec.get("trace_id"),
                    name=rec.get("name", ""),
                    kind=rec.get("kind", "co"),
                    duration_ms=rec.get("duration_ms"),
                    attributes=rec.get("attributes") or {},
                    status=rec.get("status", "OK"),
                    status_msg=rec.get("status_msg"),
                )
            )
    return rows


def _extract_model(row: _SpanRow) -> str | None:
    model = row.attributes.get("co.model.name") or row.attributes.get("co.agent.model")
    if isinstance(model, str) and model:
        return model
    return None


def _extract_tool(row: _SpanRow) -> str | None:
    if row.kind != "tool":
        return None
    tool = row.attributes.get("co.tool.name")
    if not isinstance(tool, str) or not tool:
        return None
    if row.duration_ms is not None and row.duration_ms < 5:
        return None
    return tool


def _span_detail(row: _SpanRow) -> str:
    parts: list[str] = []
    duration_ms = row.duration_ms or 0.0
    parts.append(f"{duration_ms / 1000:.3f}s")
    parts.append(row.name)

    model = _extract_model(row)
    if model:
        parts.append(f"model={model}")

    tool = _extract_tool(row)
    if tool:
        parts.append(f"tool={tool}")
        args = row.attributes.get("co.tool.args")
        if args:
            parts.append(f"args={args[:80] if isinstance(args, str) else _compact_json(args)}")

    if row.kind == "model":
        in_tokens = row.attributes.get("co.model.tokens.input")
        out_tokens = row.attributes.get("co.model.tokens.output")
        finish = row.attributes.get("co.model.finish_reason")
        if in_tokens is not None:
            parts.append(f"in_tokens={in_tokens}")
        if out_tokens is not None:
            parts.append(f"out_tokens={out_tokens}")
        if finish:
            parts.append(f"finish={finish}")

    final = row.attributes.get("co.agent.final_result")
    if isinstance(final, str) and final:
        excerpt = final.replace("\n", " ")
        if len(excerpt) > 160:
            excerpt = excerpt[:157] + "..."
        parts.append(f"result={excerpt}")

    if row.status == "ERROR":
        parts.append(f"status=ERROR msg={row.status_msg!r}")

    return " | ".join(parts)


def _summary_line(nodeid: str, duration_s: float, outcome: str, spans: list[_SpanRow]) -> str:
    trace_ids = {row.trace_id for row in spans if row.trace_id}
    models = sorted({m for row in spans if (m := _extract_model(row))})
    tools = sorted({t for row in spans if (t := _extract_tool(row))})

    parts = [
        f"[pytest-harness] {nodeid}",
        f"outcome={outcome}",
        f"duration={duration_s:.2f}s",
        f"spans={len(spans)}",
        f"traces={len(trace_ids)}",
    ]
    if models:
        parts.append(f"models={','.join(models)}")
    if tools:
        parts.append(f"tools={','.join(tools)}")
    return " | ".join(parts)


def _ensure_tracing() -> None:
    """Install the structured-log tracing pipeline.

    Re-asserts the handler on every call: tests that pin the spans logger to a
    tmp-dir for their own isolation (e.g. ``test_tracing_decorator.py``) leave
    the logger pointing somewhere we cannot read. Re-asserting restores the
    harness's handler before the next non-isolated test runs.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tracing.setup_log(
        log_path=_SPANS_LOG,
        max_size_mb=50,
        backup_count=2,
        redact_patterns=[],
    )


def recorded_spans(since: int = 0) -> list[_SpanRow]:
    """Public helper for tests that need to inspect emitted records.

    Pass the byte offset returned from a previous ``_log_high_water_mark()``
    to read only records emitted after that point.
    """
    return _records_since(since)


def spans_log_offset() -> int:
    """Snapshot the current spans log size — pass to ``recorded_spans(since=...)``."""
    return _log_high_water_mark()


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    if not _harness_enabled(config):
        return
    _ensure_tracing()


@pytest.hookimpl
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> None:
    if not _harness_enabled(item.config):
        return
    report = pytest.TestReport.from_item_and_call(item, call)
    if report.failed:
        _test_outcomes[item.nodeid] = "failed"
    elif report.when == "call" and item.nodeid not in _test_outcomes:
        _test_outcomes[item.nodeid] = report.outcome


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    if not _harness_enabled(item.config):
        yield
        return
    _ensure_tracing()
    start = time.perf_counter()
    before_offset = _log_high_water_mark()
    try:
        yield
    except BaseException:
        _test_outcomes[item.nodeid] = "failed"
        raise
    finally:
        spans = _records_since(before_offset)
        duration_s = time.perf_counter() - start
        outcome = _test_outcomes.pop(item.nodeid, "passed")
        terminal = item.config.pluginmanager.get_plugin("terminalreporter")
        if terminal is not None:
            terminal.write_line(_summary_line(item.nodeid, duration_s, outcome, spans))
            has_model_calls = any(row.kind == "model" for row in spans)
            if outcome != "passed" or duration_s * 1000 >= _SLOW_MS or has_model_calls:
                for row in spans[:_DETAIL_LIMIT]:
                    terminal.write_line(f"[pytest-harness]   {_span_detail(row)}")
                hidden = len(spans) - _DETAIL_LIMIT
                if hidden > 0:
                    terminal.write_line(f"[pytest-harness]   ... {hidden} more spans omitted")
