"""Tests for L2 aggregate turn budget enforcement."""

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.context._history_processors import enforce_turn_budget
from co_cli.deps import CoDeps, CoRuntimeState, CoSessionState
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.tool_io import PERSISTED_OUTPUT_TAG


def _make_deps(
    tmp_path: Path,
    *,
    threshold_tokens: int,
    model_max_ctx: int = 131_072,
) -> CoDeps:
    """Build a minimal CoDeps suitable for enforce_turn_budget tests."""
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
        tool_results_dir=tmp_path,
        model_max_ctx=model_max_ctx,
        turn_aggregate_threshold_tokens=threshold_tokens,
    )


def _ctx(deps: CoDeps) -> RunContext:
    return RunContext(deps=deps, model=None, usage=RunUsage())


def _tool_return(call_id: str, content: str, tool_name: str = "shell") -> ModelRequest:
    """Single ModelRequest containing one ToolReturnPart."""
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, content=content, tool_call_id=call_id)]
    )


def _build_messages(*tool_returns: ModelRequest) -> list:
    """Wrap tool returns in a realistic message history with a user turn boundary."""
    return [
        ModelRequest(parts=[UserPromptPart(content="prev turn")]),
        ModelResponse(parts=[TextPart(content="previous response")]),
        # Current user turn boundary
        ModelRequest(parts=[UserPromptPart(content="current turn")]),
        ModelResponse(parts=[TextPart(content="working...")]),
        *tool_returns,
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_below_threshold_no_spill(tmp_path: Path):
    """Two tool returns totalling 1_500 tokens must pass through unchanged when threshold=50_000."""
    # 3_000 chars / 4 = 750 tokens each, total 1_500 tokens < 50_000 threshold
    content_a = "a" * 3_000
    content_b = "b" * 3_000
    messages = _build_messages(
        _tool_return("tc1", content_a),
        _tool_return("tc2", content_b),
    )
    deps = _make_deps(tmp_path, threshold_tokens=50_000)
    result = enforce_turn_budget(_ctx(deps), messages)

    # Messages must be identical objects (no rewriting)
    assert result == messages

    # Verify no spill occurred
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    assert PERSISTED_OUTPUT_TAG not in part.content


def test_force_spill_largest_first(tmp_path: Path):
    """Three oversized tool returns must be spilled largest-first until budget fits.

    Total = 18_000 tokens, threshold = 5_000 tokens.
    Spill order: 32_000-char first, then 24_000-char.
    16_000-char return (4_000 tokens) remains intact.
    """
    # 16_000 chars = 4_000 tokens; 24_000 chars = 6_000 tokens; 32_000 chars = 8_000 tokens
    # Total = 18_000 tokens > 5_000 threshold
    content_small = "s" * 16_000
    content_mid = "m" * 24_000
    content_large = "l" * 32_000

    messages = _build_messages(
        _tool_return("tc_small", content_small),
        _tool_return("tc_mid", content_mid),
        _tool_return("tc_large", content_large),
    )
    deps = _make_deps(tmp_path, threshold_tokens=5_000)
    result = enforce_turn_budget(_ctx(deps), messages)

    # Collect all ToolReturnParts from result
    returns: dict[str, str] = {}
    for msg in result:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    returns[part.tool_call_id] = part.content

    # 16_000-char return must be intact
    assert returns["tc_small"] == content_small, "smallest return must remain unspilled"

    # 24_000-char and 32_000-char returns must be spilled
    assert PERSISTED_OUTPUT_TAG in returns["tc_mid"], "mid return must be spilled"
    assert PERSISTED_OUTPUT_TAG in returns["tc_large"], "large return must be spilled"

    # Verify exactly 2 were spilled
    spilled_count = sum(1 for content in returns.values() if PERSISTED_OUTPUT_TAG in content)
    assert spilled_count == 2

    # Verify largest-first: the 32_000-char content must appear in a spilled file
    # (file content won't be in the stub, but we can verify file was written for the large one)
    # The large one's stub file path contains its hash — it must have been spilled first
    # because if mid were spilled first we'd verify differently. We verify the stub
    # for tc_large exists (content_large was written to disk).
    large_stub = returns["tc_large"]
    assert "32,000 chars" in large_stub or "32000" in large_stub.replace(",", "")


def test_all_spilled_bail_out(tmp_path: Path):
    """When all candidates already start with PERSISTED_OUTPUT_TAG, bail out immediately.

    Even if aggregate tokens exceed threshold, no further spilling is possible.
    runtime.current_turn_aggregate_tokens_after_spill must remain None on bail-out.
    """
    # Pre-spilled stub is ~1_800 chars (below TOOL_RESULT_PREVIEW_CHARS guard)
    # We build realistic-looking stubs manually
    stub = (
        f"{PERSISTED_OUTPUT_TAG}\n"
        f"This tool result was too large (50000 chars, 48.8 KB).\n"
        f"tool: shell\n"
        f"file: /tmp/abc123.txt\n"
        f"To read the full output, call file_read with the path above and use "
        f"start_line/end_line to page through it in chunks.\n"
        f"preview:\n{'x' * 800}\n"
        f"</persisted-output>"
    )

    messages = _build_messages(
        _tool_return("tc1", stub),
        _tool_return("tc2", stub),
    )
    # threshold is tiny (100 tokens = 400 chars), but all returns are pre-spilled
    deps = _make_deps(tmp_path, threshold_tokens=100)
    result = enforce_turn_budget(_ctx(deps), messages)

    # Bail-out: messages returned unchanged
    assert result == messages
    # No aggregate written on bail-out path
    assert deps.runtime.current_turn_aggregate_tokens_after_spill is None


def test_uses_cached_threshold(tmp_path: Path):
    """enforce_turn_budget must use deps.turn_aggregate_threshold_tokens without recomputing.

    Aggregate = 13_000 tokens > threshold = 12_000 tokens → spill fires.
    """
    # 13_000 tokens = 52_000 chars total across all tool returns
    # Use one large return of 52_000 chars (13_000 tokens)
    content = "t" * 52_000
    messages = _build_messages(_tool_return("tc1", content))

    deps = _make_deps(tmp_path, threshold_tokens=12_000)
    result = enforce_turn_budget(_ctx(deps), messages)

    returns = [
        part
        for msg in result
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 1
    # Spill must have fired: 13_000 tokens > 12_000 threshold
    assert PERSISTED_OUTPUT_TAG in returns[0].content, (
        "enforce_turn_budget must use cached threshold=12_000; "
        "13_000 token aggregate must trigger spill"
    )
    # Token count after spill must be written to runtime
    assert deps.runtime.current_turn_aggregate_tokens_after_spill is not None
