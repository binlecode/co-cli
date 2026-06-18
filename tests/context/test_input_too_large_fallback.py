"""Distinct INPUT_TOO_LARGE fallback: an oversized region degrades to a static
marker without eroding the circuit-breaker budget, while a genuine summarizer
Exception still increments the skip count (the existing SUMMARIZER_ERROR path is
untouched).

Both halves assert through the public ``compact_messages`` chokepoint — not by
calling the private ``_gated_summarize_or_none`` directly — so the test exercises
the real fallback wiring.
"""

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.context import summarization
from co_cli.context._compaction_markers import STATIC_MARKER_PREFIX
from co_cli.context.compaction import compact_messages
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="head turn")]),
    ModelRequest(parts=[UserPromptPart(content="dropped user turn — summarize me")]),
    ModelResponse(parts=[TextPart(content="dropped assistant turn")]),
    ModelRequest(parts=[UserPromptPart(content="tail turn")]),
]
_BOUNDS = (1, 3, 0)


def _make_ctx(model_max_context_tokens: int) -> RunContext[CoDeps]:
    deps = CoDeps(
        shell=ShellBackend(),
        model=build_model(SETTINGS_NO_MCP.llm),
        config=SETTINGS_NO_MCP.model_copy(deep=True),
        session=CoSessionState(),
        model_max_context_tokens=model_max_context_tokens,
    )
    return RunContext(deps=deps, model=deps.model, usage=RunUsage())


def _marker_content(result: list) -> str:
    marker = result[1]
    return marker.parts[0].content


@pytest.mark.asyncio
async def test_input_too_large_degrades_to_static_marker_sparing_breaker(monkeypatch):
    """A window too small for the assembled prompt yields a static marker and
    leaves compaction_skip_count unchanged."""

    async def _no_provider(*args, **kwargs):
        raise AssertionError("provider must not be called for an oversized region")

    monkeypatch.setattr(summarization, "llm_call", _no_provider)
    ctx = _make_ctx(model_max_context_tokens=100)
    before = ctx.deps.runtime.compaction_skip_count

    result, summary_text, input_too_large = await compact_messages(ctx, _MESSAGES, _BOUNDS)

    assert summary_text is None, "oversized region should not produce an LLM summary"
    assert input_too_large, "the fit-guard bail must be signalled as a deliberate degrade"
    assert _marker_content(result).startswith(STATIC_MARKER_PREFIX)
    assert ctx.deps.runtime.compaction_skip_count == before, "breaker budget was eroded"


@pytest.mark.asyncio
async def test_summarizer_error_still_increments_skip_count(monkeypatch):
    """A genuine summarizer Exception (ample window, provider raises) still moves
    the circuit-breaker counter — the existing SUMMARIZER_ERROR path is intact."""

    async def _failing_provider(*args, **kwargs):
        raise RuntimeError("flaky model")

    monkeypatch.setattr(summarization, "llm_call", _failing_provider)
    ctx = _make_ctx(model_max_context_tokens=200_000)
    before = ctx.deps.runtime.compaction_skip_count

    result, summary_text, input_too_large = await compact_messages(ctx, _MESSAGES, _BOUNDS)

    assert summary_text is None
    assert not input_too_large, "a genuine summarizer error is not a fit-guard bail"
    assert _marker_content(result).startswith(STATIC_MARKER_PREFIX)
    assert ctx.deps.runtime.compaction_skip_count == before + 1, "skip count did not increment"
