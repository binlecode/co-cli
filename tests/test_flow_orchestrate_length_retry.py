"""Tests for length-continuation auto-retry in run_turn().

Production path: co_cli/context/orchestrate.py:run_turn() — finish_reason='length' branch.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.agent.core import build_agent, build_tool_registry
from co_cli.context.orchestrate import _length_retry_settings, run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# _length_retry_settings — gate semantics via direct invocation
#
# The gate admits responses with a TextPart present and finish_reason='length'.
# Tool-call truncations are deliberately blocked: a truncated ToolCallPart would
# carry malformed JSON args into retry history, producing an unanswered
# tool_calls entry that OpenAI/Ollama reject. They fall through to
# _check_output_limits' ceiling status instead.
# ---------------------------------------------------------------------------


# SimpleNamespace duck-types SessionRunResult for the two attributes the gate reads
# (result.response.finish_reason, result.response.parts). Returning Any avoids
# leaking the duck type into call sites and keeps `# type: ignore` off every test.
def _fake_result(parts: list, finish_reason: str = "length") -> Any:
    return SimpleNamespace(response=ModelResponse(parts=parts, finish_reason=finish_reason))


def test_blocks_thinking_only() -> None:
    """Thinking-only response must not trigger retry (no progress to continue from)."""
    result = _fake_result([ThinkingPart(content="reasoning")])
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    assert _length_retry_settings(result, settings) is None


def test_blocks_empty_parts() -> None:
    """Empty parts list must not trigger retry."""
    result = _fake_result([])
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    assert _length_retry_settings(result, settings) is None


def test_blocks_tool_call_only() -> None:
    """Truncated tool call alone must not trigger retry (history would be poisoned)."""
    result = _fake_result([ToolCallPart(tool_name="shell", args="{}")])
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    assert _length_retry_settings(result, settings) is None


def test_blocks_tool_call_after_thinking() -> None:
    """Thinking + tool call (no text) must not trigger retry."""
    result = _fake_result(
        [ThinkingPart(content="reasoning"), ToolCallPart(tool_name="shell", args="{}")]
    )
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    assert _length_retry_settings(result, settings) is None


def test_passes_text_after_thinking() -> None:
    """Thinking + text triggers retry; max_tokens doubles in scalar and extra_body."""
    result = _fake_result([ThinkingPart(content="reasoning"), TextPart(content="answer so far")])
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    boosted = _length_retry_settings(result, settings)
    assert boosted is not None
    assert boosted["max_tokens"] == 8192
    assert boosted["extra_body"]["max_tokens"] == 8192


def test_caps_at_ceiling() -> None:
    """Boost capped at 16384 even if doubling would exceed it."""
    result = _fake_result([TextPart(content="answer")])
    settings = {"max_tokens": 10_000, "extra_body": {"max_tokens": 10_000}}
    boosted = _length_retry_settings(result, settings)
    assert boosted is not None
    assert boosted["max_tokens"] == 16_384
    assert boosted["extra_body"]["max_tokens"] == 16_384


def test_blocks_at_ceiling() -> None:
    """At the ceiling there is no further room — gate returns None."""
    result = _fake_result([TextPart(content="answer")])
    settings = {"max_tokens": 16_384, "extra_body": {"max_tokens": 16_384}}
    assert _length_retry_settings(result, settings) is None


def test_blocks_non_length_finish_reason() -> None:
    """Only finish_reason='length' triggers retry; 'stop' must not."""
    result = _fake_result([TextPart(content="answer")], finish_reason="stop")
    settings = {"max_tokens": 4096, "extra_body": {"max_tokens": 4096}}
    assert _length_retry_settings(result, settings) is None


# ---------------------------------------------------------------------------
# Integration — real LLM, noreason path
# ---------------------------------------------------------------------------

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_TOOL_REG = build_tool_registry(SETTINGS_NO_MCP)
_AGENT = build_agent(config=SETTINGS_NO_MCP, model=_LLM_MODEL, tool_registry=_TOOL_REG)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        tool_index=dict(_TOOL_REG.tool_index),
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
    )


@pytest.mark.asyncio
@pytest.mark.timeout(200)
async def test_length_retry_completes_truncated_noreason_response() -> None:
    """run_turn must auto-retry with doubled max_tokens when finish_reason='length'.

    Ollama ignores max_completion_tokens (pydantic-ai's mapping of max_tokens) but honors
    max_tokens injected via extra_body (the openai client merges extra_body at the request
    root). Constrain output to 300 tokens this way, request a ~500-token response. The first
    segment is truncated; _length_retry_settings doubles max_tokens (and extra_body.max_tokens)
    to 600, and the continuation segment completes the remaining ~200 tokens.

    Asserts the turn succeeds and history shows ≥2 ModelResponse entries.
    """
    noreason = SETTINGS_NO_MCP.llm.noreason_model_settings()
    # Ollama ignores max_completion_tokens (pydantic-ai's mapping of max_tokens).
    # Inject max_tokens in extra_body — the openai client merges extra_body at the root of
    # the HTTP body, so Ollama sees and honors the max_tokens field there.
    # max_tokens in the scalar settings must match so _length_retry_settings detects
    # current_max > 0 and fires the boost.
    extra_body = {**noreason.get("extra_body", {}), "max_tokens": 300}
    constrained_settings = {**noreason, "max_tokens": 300, "extra_body": extra_body}

    deps = _make_deps()
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS * 2):
        turn = await run_turn(
            agent=_AGENT,
            user_input=(
                "Write a 5-paragraph essay about why Python is popular. "
                "Each paragraph must be at least 4 sentences."
            ),
            deps=deps,
            message_history=[],
            model_settings=constrained_settings,  # type: ignore[arg-type]
            frontend=SilentFrontend(),
        )

    assert turn.outcome == "continue"
    model_responses = [m for m in turn.messages if isinstance(m, ModelResponse)]
    assert len(model_responses) >= 2, (
        f"expected at least one length-retry segment; "
        f"got {len(model_responses)} ModelResponse(s) in history"
    )
