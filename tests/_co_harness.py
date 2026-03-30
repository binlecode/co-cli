"""Pytest harness plugin for low-level per-test diagnostics.

Enables OTel span export during pytest and prints a compact per-test summary.
Slow or failing tests also get span-by-span detail in the pytest log stream.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from pydantic_ai import Agent
from pydantic_ai.agent import InstrumentationSettings

from co_cli.config import LOGS_DB
from co_cli.observability._telemetry import SQLiteSpanExporter

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
_VERSION = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]
_SLOW_MS = int(os.getenv("CO_PYTEST_TRACE_SLOW_MS", "2000"))
_DETAIL_LIMIT = int(os.getenv("CO_PYTEST_TRACE_DETAIL_LIMIT", "12"))
_INSTALLED = False


def _is_eval_path(path_str: str) -> bool:
    parts = Path(path_str).parts
    return "evals" in parts


def _harness_enabled(config: pytest.Config) -> bool:
    if os.getenv("CO_PYTEST_HARNESS", "1") == "0":
        return False
    # Evals must not couple to the pytest harness. If the invocation explicitly
    # targets eval files or directories, skip all harness behavior.
    args = [str(a) for a in config.args]
    if args and all(_is_eval_path(a) for a in args if not a.startswith("-")):
        return False
    return True


@dataclass
class _SpanRow:
    rowid: int
    trace_id: str
    name: str
    duration_ms: float | None
    attributes: dict[str, Any]


def _compact_json(value: Any, limit: int = 180) -> str:
    text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _load_spans_after(rowid: int) -> list[_SpanRow]:
    if not LOGS_DB.exists():
        return []
    with sqlite3.connect(LOGS_DB) as conn:
        rows = conn.execute(
            """
            SELECT rowid, trace_id, name, duration_ms, attributes
            FROM spans
            WHERE rowid > ?
            ORDER BY rowid ASC
            """,
            (rowid,),
        ).fetchall()
    parsed: list[_SpanRow] = []
    for raw_rowid, trace_id, name, duration_ms, attributes_json in rows:
        try:
            attributes = json.loads(attributes_json) if attributes_json else {}
        except json.JSONDecodeError:
            attributes = {}
        parsed.append(
            _SpanRow(
                rowid=int(raw_rowid),
                trace_id=trace_id,
                name=name,
                duration_ms=duration_ms,
                attributes=attributes,
            )
        )
    return parsed


def _db_high_water_mark() -> int:
    if not LOGS_DB.exists():
        return 0
    with sqlite3.connect(LOGS_DB) as conn:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM spans").fetchone()
    return int(row[0]) if row else 0


def _extract_model(row: _SpanRow) -> str | None:
    model = row.attributes.get("model_name") or row.attributes.get("gen_ai.request.model")
    if isinstance(model, str) and model:
        return model
    return None


def _extract_tool(row: _SpanRow) -> str | None:
    tool = row.attributes.get("gen_ai.tool.name")
    if not isinstance(tool, str) or not tool:
        return None
    # Exclude deferred-approval resolution spans: pydantic-ai fires execute_tool
    # spans for both deferred approval bookkeeping (~1–4ms) and actual tool
    # execution (always >10ms for real I/O). Only count real execution in the
    # tools= summary so denied tools are not reported as having run.
    if row.duration_ms is not None and row.duration_ms < 5:
        return None
    return tool


def _extract_api(row: _SpanRow) -> str | None:
    host = row.attributes.get("server.address")
    port = row.attributes.get("server.port")
    if isinstance(host, str) and host:
        return f"{host}:{port}" if port is not None else host
    return None


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
        args = row.attributes.get("gen_ai.tool.call.arguments")
        if args:
            parts.append(f"args={args}")

    api = _extract_api(row)
    if api:
        provider = row.attributes.get("gen_ai.provider.name")
        system = row.attributes.get("gen_ai.system")
        parts.append(f"api={api}")
        if provider:
            parts.append(f"provider={provider}")
        if system:
            parts.append(f"system={system}")

    final_result = row.attributes.get("final_result")
    if isinstance(final_result, str) and final_result:
        excerpt = final_result.replace("\n", " ")
        if len(excerpt) > 160:
            excerpt = excerpt[:157] + "..."
        parts.append(f"result={excerpt}")

    return " | ".join(parts)


def _summary_line(nodeid: str, duration_s: float, outcome: str, spans: list[_SpanRow]) -> str:
    trace_ids = {row.trace_id for row in spans if row.trace_id}
    models = sorted({model for row in spans if (model := _extract_model(row))})
    tools = sorted({tool for row in spans if (tool := _extract_tool(row))})
    apis = sorted({api for row in spans if (api := _extract_api(row))})

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
    if apis:
        parts.append(f"apis={','.join(apis)}")
    return " | ".join(parts)


def _ensure_telemetry() -> TracerProvider:
    global _INSTALLED
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "co-cli-pytest",
                    "service.version": _VERSION,
                }
            )
        )
        trace.set_tracer_provider(provider)

    if not _INSTALLED:
        provider.add_span_processor(SimpleSpanProcessor(SQLiteSpanExporter()))
        Agent.instrument_all(
            InstrumentationSettings(
                tracer_provider=provider,
                version=3,
            )
        )
        _INSTALLED = True
    return provider


@pytest.hookimpl
def pytest_configure(config: pytest.Config) -> None:
    if not _harness_enabled(config):
        return
    _ensure_telemetry()


@pytest.hookimpl
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> None:
    if not _harness_enabled(item.config):
        return
    report = pytest.TestReport.from_item_and_call(item, call)
    if report.failed:
        item._co_harness_outcome = "failed"
    elif report.when == "call" and not hasattr(item, "_co_harness_outcome"):
        item._co_harness_outcome = report.outcome


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    if not _harness_enabled(item.config):
        yield
        return
    provider = _ensure_telemetry()
    start = time.perf_counter()
    before_rowid = _db_high_water_mark()
    try:
        yield
    except BaseException:
        item._co_harness_outcome = "failed"
        raise
    finally:
        provider.force_flush()
        spans = _load_spans_after(before_rowid)
        duration_s = time.perf_counter() - start
        outcome = getattr(item, "_co_harness_outcome", "passed")
        terminal = item.config.pluginmanager.get_plugin("terminalreporter")
        if terminal is None:
            return

        terminal.write_line(_summary_line(item.nodeid, duration_s, outcome, spans))
        if outcome != "passed" or duration_s * 1000 >= _SLOW_MS:
            for row in spans[:_DETAIL_LIMIT]:
                terminal.write_line(f"[pytest-harness]   {_span_detail(row)}")
            hidden = len(spans) - _DETAIL_LIMIT
            if hidden > 0:
                terminal.write_line(f"[pytest-harness]   ... {hidden} more spans omitted")
