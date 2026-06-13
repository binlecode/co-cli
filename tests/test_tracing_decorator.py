"""Tests for co_cli.observability.tracing — decorator, contextvars, redaction."""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from co_cli.observability import tracing


@pytest.fixture(autouse=True)
def _reset_tracing(tmp_path: Path) -> None:
    """Each test gets its own tmp spans log; module state is reset between tests."""
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    tracing._COMPILED_PATTERNS = []
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _read_records(log_path: Path) -> list[dict]:
    """Flush handlers and read all JSON records from the spans log."""
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_nested_parent_child_linkage(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    @tracing.trace("child")
    def child() -> None:
        pass

    @tracing.trace("parent")
    def parent() -> None:
        child()

    parent()
    records = _read_records(log)
    assert len(records) == 2

    by_name = {r["name"]: r for r in records}
    assert by_name["child"]["parent_span_id"] == by_name["parent"]["span_id"]
    assert by_name["child"]["trace_id"] == by_name["parent"]["trace_id"]


def test_exception_emits_error_record_and_reraises(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    @tracing.trace("boom")
    def f() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        f()

    rec = _read_records(log)[0]
    assert rec["status"] == "ERROR"
    assert rec["status_msg"] == "nope"
    assert tracing._SPAN_STACK.get() == ()


def test_asyncio_gather_siblings_share_parent(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    @tracing.trace("a")
    async def a() -> None:
        await asyncio.sleep(0)

    @tracing.trace("b")
    async def b() -> None:
        await asyncio.sleep(0)

    @tracing.trace("parent")
    async def parent() -> None:
        await asyncio.gather(a(), b())

    asyncio.run(parent())
    records = _read_records(log)
    by_name = {r["name"]: r for r in records}
    parent_id = by_name["parent"]["span_id"]
    assert by_name["a"]["parent_span_id"] == parent_id
    assert by_name["b"]["parent_span_id"] == parent_id
    assert by_name["a"]["trace_id"] == by_name["parent"]["trace_id"]


def test_new_trace_flag_resets_trace_id_before_push(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    @tracing.trace("grandchild")
    def grandchild() -> None:
        pass

    @tracing.trace("turn", new_trace=True)
    def turn() -> None:
        grandchild()

    @tracing.trace("outer")
    def outer() -> None:
        turn()

    outer()
    records = _read_records(log)
    by_name = {r["name"]: r for r in records}

    assert by_name["turn"]["trace_id"] != by_name["outer"]["trace_id"]
    assert by_name["grandchild"]["trace_id"] == by_name["turn"]["trace_id"]
    assert by_name["grandchild"]["parent_span_id"] == by_name["turn"]["span_id"]


def test_run_with_context_propagates_to_executor_thread() -> None:
    """`run_with_context` snapshots the current context and binds a callable
    the executor thread can run — propagating session_id and the span stack
    across the thread boundary."""

    def worker() -> tuple[str | None, int]:
        return tracing._SESSION_ID.get(), len(tracing._SPAN_STACK.get())

    async def outer() -> tuple[str | None, int]:
        tracing.set_session_context("sess-abc")
        tracing.push_span("outer_async")
        loop = asyncio.get_running_loop()
        bound = tracing.run_with_context(worker)
        return await loop.run_in_executor(None, bound)

    session, stack_depth = asyncio.run(outer())
    assert session == "sess-abc"
    assert stack_depth == 1


def test_redaction_status_msg(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log, redact_patterns=[r"sk-[A-Za-z0-9]{20,}"])

    @tracing.trace("err_with_secret")
    def f() -> None:
        raise RuntimeError("auth failed for sk-abc123def456ghi789jkl")

    with pytest.raises(RuntimeError):
        f()

    rec = _read_records(log)[0]
    assert rec["status"] == "ERROR"
    assert "sk-abc123def456ghi789jkl" not in rec["status_msg"]
    assert "[REDACTED]" in rec["status_msg"]


def test_no_op_span_when_stack_empty_does_not_raise(tmp_path: Path) -> None:
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    tracing.current_span().set_attribute("orphan", "value")
    tracing.current_span().add_event("orphan_event")
    tracing.current_span().set_status("OK")

    assert _read_records(log) == []
