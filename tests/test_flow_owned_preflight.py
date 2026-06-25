"""Behavioral tests for the owned loop's per-step preflight.

Asserts observable outcomes only: the processor chain actually transforms history
(deps-threaded spill fires), ``clean_message_history`` merges consecutive requests
while leaving the source list untouched (CD-M-1), and ``assemble_instructions`` emits
the static prefix plus the per-turn dynamic nudges on the right conditions.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from tests._settings import SETTINGS_NO_MCP

from co_cli.agent._instructions import WRAP_UP_TEXT
from co_cli.agent.preflight import (
    assemble_instructions,
    build_request_params,
    clean_message_history,
    run_history_processors,
)
from co_cli.config.llm import resolve_request_limit
from co_cli.config.tuning import SPILL_PREVIEW_CHARS
from co_cli.deps import CoDeps, CoSessionState
from co_cli.tools.shell_backend import ShellBackend


def _deps(tmp_path) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        tool_results_dir=tmp_path / "tool-results",
    )


@pytest.mark.asyncio
async def test_run_history_processors_threads_deps_and_spills_large_result(tmp_path) -> None:
    """A large tool result under a tiny spill threshold gets spilled — proving the
    chain runs the deps-reading spill processor with the passed-in deps."""
    deps = _deps(tmp_path)
    # Tiny spill threshold so the single large result is forced to disk; stay well under
    # the proactive (LLM-summarizer) threshold so that processor is a below-threshold no-op.
    deps.spill_threshold_tokens = 10
    deps.static_floor_tokens = 0

    big = "X" * (SPILL_PREVIEW_CHARS * 4)
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="run it")]),
        ModelResponse(parts=[ToolCallPart(tool_name="file_read", args={}, tool_call_id="c1")]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="file_read", content=big, tool_call_id="c1")]
        ),
    ]

    out = await run_history_processors(history, deps)

    returns = [
        p
        for m in out
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert returns, "expected the tool return to survive the chain"
    # The oversized content must no longer be carried inline — spill replaced it.
    assert all(len(str(p.content)) < len(big) for p in returns)


def test_clean_message_history_merges_consecutive_requests_without_mutating_source() -> None:
    """Two consecutive ModelRequests carrying tool returns merge into one request with
    the tool-return parts ordered first; the source list is left unchanged (CD-M-1)."""
    req_a = ModelRequest(
        parts=[
            UserPromptPart(content="hello"),
            ToolReturnPart(tool_name="t", content="r1", tool_call_id="a"),
        ]
    )
    req_b = ModelRequest(parts=[ToolReturnPart(tool_name="t", content="r2", tool_call_id="b")])
    source: list[ModelMessage] = [req_a, req_b]
    source_ids_before = [id(m) for m in source]

    cleaned = clean_message_history(source)

    # Merged into a single ModelRequest.
    assert len(cleaned) == 1
    merged = cleaned[0]
    assert isinstance(merged, ModelRequest)
    # Tool-return parts sorted to the front of the merged request.
    kinds = [type(p).__name__ for p in merged.parts]
    assert kinds[0] == "ToolReturnPart"
    assert kinds[1] == "ToolReturnPart"
    assert any(isinstance(p, UserPromptPart) for p in merged.parts)
    # Source history untouched — still two distinct request objects.
    assert [id(m) for m in source] == source_ids_before
    assert len(source) == 2
    assert len(source[0].parts) == 2
    assert len(source[1].parts) == 1


def test_assemble_instructions_emits_static_and_fires_wrap_up_and_safety(tmp_path) -> None:
    """The static prefix is present; the wrap-up nudge fires on the last allowed request
    and the doom-loop safety warning fires when the history shows a repeat streak."""
    deps = _deps(tmp_path)
    limit = resolve_request_limit(deps.config.llm)
    assert limit is not None
    assert limit >= 2

    # A doom-loop streak: identical tool call repeated past the threshold.
    same_call = ModelResponse(
        parts=[ToolCallPart(tool_name="file_read", args={"path": "x"}, tool_call_id="r")]
    )
    messages: list[ModelMessage] = [same_call for _ in range(deps.config.doom_loop_threshold + 1)]

    parts = assemble_instructions(
        deps,
        static_instructions="SYSTEM PROMPT",
        messages=messages,
        request_count=limit - 1,
    )

    static_parts = [p for p in parts if not p.dynamic]
    assert any("SYSTEM PROMPT" in p.content for p in static_parts)
    joined = "\n\n".join(p.content for p in parts)
    assert WRAP_UP_TEXT in joined
    assert "repeating the same tool call" in joined


def test_assemble_instructions_no_wrap_up_before_last_request(tmp_path) -> None:
    """The wrap-up nudge stays silent on any step before the last allowed request."""
    deps = _deps(tmp_path)
    limit = resolve_request_limit(deps.config.llm)
    assert limit is not None

    parts = assemble_instructions(
        deps,
        static_instructions="SYS",
        messages=[],
        request_count=0,
    )
    joined = "\n\n".join(p.content for p in parts)
    assert WRAP_UP_TEXT not in joined


def test_build_request_params_carries_instructions_and_output_mode() -> None:
    """Subagent-shaped params disallow text output and carry the instruction parts."""
    instr = [InstructionPart(content="sys", dynamic=False)]
    params = build_request_params(instruction_parts=instr, allow_text_output=False)
    assert params.allow_text_output is False
    assert params.instruction_parts == instr
    # Text-mode default keeps text output allowed.
    text_params = build_request_params(instruction_parts=instr)
    assert text_params.allow_text_output is True
