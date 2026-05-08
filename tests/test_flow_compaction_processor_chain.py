"""Integration test for the registered history-processor chain ordering.

Verifies the contract introduced by the L2 consolidation: enforce_request_size
spills tool returns before proactive_window_processor decides to summarize.
When spill alone can resolve the pressure, proactive fast-paths and never
runs the LLM summarizer; when spill can't (text-heavy pressure with few tool
returns), proactive fires.

Uses model=None so proactive uses its static-marker fallback path — no LLM
calls in this test, just the ordering contract.
"""

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.context.compaction import is_compaction_marker, proactive_window_processor
from co_cli.context.history_processors import (
    dedup_tool_results,
    enforce_request_size,
    evict_old_tool_results,
    sanitize_surrogate_codepoints,
)
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG


def _make_deps(tmp_path: Path, *, spill_threshold_tokens: int, model_max_ctx: int) -> CoDeps:
    """Real CoDeps with model=None (proactive uses static-marker fallback, no LLM)."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path,
        model_max_ctx=model_max_ctx,
        spill_threshold_tokens=spill_threshold_tokens,
        model=None,
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage())


async def _run_chain(ctx: RunContext, messages: list[ModelMessage]) -> list[ModelMessage]:
    """Run the four registered history processors in their registered order."""
    out = dedup_tool_results(ctx, messages)
    out = evict_old_tool_results(ctx, out)
    out = enforce_request_size(ctx, out)
    out = await proactive_window_processor(ctx, out)
    return sanitize_surrogate_codepoints(ctx, out)


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(tool: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=tool, args={}, tool_call_id=call_id)])


def _tool_return(tool: str, call_id: str, content: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool, content=content, tool_call_id=call_id)]
    )


def _has_compaction_marker(messages: list[ModelMessage]) -> bool:
    """True if any UserPromptPart in messages is a compaction marker."""
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if isinstance(part, UserPromptPart) and is_compaction_marker(part.content):
                return True
    return False


def _spilled_count(messages: list[ModelMessage]) -> int:
    count = 0
    for msg in messages:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if (
                isinstance(part, ToolReturnPart)
                and isinstance(part.content, str)
                and part.content.startswith(PERSISTED_OUTPUT_TAG)
            ):
                count += 1
    return count


@pytest.mark.asyncio
async def test_spill_resolves_pressure_proactive_fast_paths(tmp_path: Path):
    """Tool-return-heavy pressure: enforce_request_size spills, proactive fast-paths.

    Budget = 800 tokens; spill threshold = 400 tokens; compaction threshold = 0.50 * 800 = 400.
    Three 24K-char shell returns (≈ 6K tokens each, 18K total) blow past both thresholds.
    After enforce_request_size spills until aggregate ≤ 400, proactive sees a request well
    under its 400-token trigger and fast-paths without inserting a compaction marker.
    """
    content = "x" * 24_000
    messages: list[ModelMessage] = [
        _user("first turn"),
        _assistant_text("ok"),
        _user("now run things"),
        _tool_call("shell", "tc1"),
        _tool_return("shell", "tc1", content),
        _tool_call("shell", "tc2"),
        _tool_return("shell", "tc2", content),
        _tool_call("shell", "tc3"),
        _tool_return("shell", "tc3", content),
    ]
    deps = _make_deps(tmp_path, spill_threshold_tokens=400, model_max_ctx=800)

    out = await _run_chain(_ctx(deps), messages)

    assert _spilled_count(out) >= 2, "spill must fire on the largest tool returns"
    assert not _has_compaction_marker(out), (
        "proactive must fast-path once spill brings aggregate under the compaction threshold"
    )
    assert deps.runtime.compaction_applied_this_turn is False


@pytest.mark.asyncio
async def test_text_pressure_unspillable_proactive_fires(tmp_path: Path):
    """Text-heavy pressure with no spillable tool returns: proactive must fire (static marker).

    Budget = 800 tokens; spill threshold = 400 tokens. The history is text-only across
    multiple turns — no ToolReturnParts, so enforce_request_size has no candidates and
    sets skip_reason='no_candidates'. Proactive then sees the same pressure and fires
    summarization (static-marker fallback because model=None), inserting a compaction marker.
    """
    big = "narrative " * 800
    messages: list[ModelMessage] = [
        _user(big),
        _assistant_text(big),
        _user(big),
        _assistant_text(big),
        _user(big),
        _assistant_text(big),
        _user("latest user turn"),
    ]
    deps = _make_deps(tmp_path, spill_threshold_tokens=400, model_max_ctx=800)

    out = await _run_chain(_ctx(deps), messages)

    assert _spilled_count(out) == 0, "no tool returns to spill"
    assert _has_compaction_marker(out), (
        "proactive must fire when spill has no candidates and pressure remains"
    )
    assert deps.runtime.compaction_applied_this_turn is True
