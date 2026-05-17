"""Bootstrap ``tool_budget.resolved`` span emission contract."""

import json
import logging
from pathlib import Path

import pytest

from co_cli.bootstrap.core import _emit_tool_budget_span
from co_cli.observability import tracing
from co_cli.tools.tool_call_limit import MAX_TOOL_CALLS_PER_MODEL_TURN
from co_cli.tools.tool_io import SPILL_THRESHOLD_CHARS


@pytest.fixture(autouse=True)
def _reset_tracing(tmp_path: Path) -> None:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    tracing._COMPILED_PATTERNS = []
    tracing._SESSION_ID.set(None)
    tracing._TRACE_ID.set(None)
    tracing._SPAN_STACK.set(())


def _read_records(log_path: Path) -> list[dict]:
    logger = logging.getLogger("co_cli.observability.spans")
    for handler in logger.handlers:
        handler.flush()
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def test_tool_budget_resolved_span_emits_all_attrs(tmp_path: Path) -> None:
    """_emit_tool_budget_span fires exactly one span with all five budget attributes."""
    log = tmp_path / "spans.jsonl"
    tracing.setup_log(log)

    model_max_ctx = 32768
    spill_ratio = 0.5
    spill_threshold_tokens = int(spill_ratio * model_max_ctx)

    _emit_tool_budget_span(
        model_max_ctx=model_max_ctx,
        spill_ratio=spill_ratio,
        spill_threshold_tokens=spill_threshold_tokens,
    )

    records = _read_records(log)
    assert len(records) == 1, f"Expected 1 span, got {len(records)}"

    rec = records[0]
    assert rec["name"] == "tool_budget.resolved"
    attrs = rec["attributes"]
    assert attrs["budget.context_window_tokens"] == model_max_ctx
    assert attrs["budget.spill_ratio"] == spill_ratio
    assert attrs["budget.tool_call_limit"] == MAX_TOOL_CALLS_PER_MODEL_TURN
    assert attrs["budget.spill_threshold_chars"] == SPILL_THRESHOLD_CHARS
    assert attrs["budget.spill_threshold_tokens"] == spill_threshold_tokens
