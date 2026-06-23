"""Tests for length-continuation auto-retry in run_turn().

Production path: co_cli/agent/orchestrate.py:run_turn() — finish_reason='length' branch.
"""

import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import (
    LLM_COMPACTION_SUMMARY_TIMEOUT_SECS,
    PYTEST_PER_TEST_TIMEOUT_SECS,
)

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrate import _check_output_limits, _TurnState, run_turn
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Integration — real LLM, noreason path
# ---------------------------------------------------------------------------

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_TOOLSET, _TOOL_INDEX = build_native_toolset()


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        toolset=_TOOLSET,
        tool_catalog=_TOOL_INDEX,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_context_tokens=SETTINGS_NO_MCP.llm.max_context_tokens,
    )


_AGENT = build_orchestrator(ORCHESTRATOR_SPEC, _make_deps())


@pytest.mark.asyncio
# Length-retry runs ≥2 sequential LLM calls; raise the default ceiling with headroom.
@pytest.mark.timeout(PYTEST_PER_TEST_TIMEOUT_SECS + 20)
async def test_length_retry_completes_truncated_noreason_response() -> None:
    """run_turn must auto-retry with doubled max_tokens when finish_reason='length'.

    Ollama ignores max_completion_tokens (pydantic-ai's mapping of max_tokens) but honors
    max_tokens injected via extra_body (the openai client merges extra_body at the request
    root). Constrain output to 80 tokens — well below any reasonable essay response —
    so the first run is reliably truncated regardless of model verbosity. The retry
    doubles max_tokens each pass (80→160→320→…) until the model completes.

    Asserts the turn succeeds, ≥2 LLM calls fired (the retry), the prompt appears
    exactly once in the persisted history (no duplication), and the history ends on
    the complete ModelResponse (the truncated partial was discarded, not persisted).
    """
    noreason = SETTINGS_NO_MCP.llm.noreason_model_settings()
    # Ollama ignores max_completion_tokens (pydantic-ai's mapping of max_tokens).
    # Inject max_tokens in extra_body — the openai client merges extra_body at the root of
    # the HTTP body, so Ollama sees and honors the max_tokens field there.
    # max_tokens in the scalar settings must match so _length_retry_settings detects
    # current_max > 0 and fires the boost.
    # 80 tokens is tight enough to force truncation on any concise model response.
    extra_body = {**noreason.get("extra_body", {}), "max_tokens": 80}
    constrained_settings = {**noreason, "max_tokens": 80, "extra_body": extra_body}

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
    # The retry fired: ≥2 LLM calls this turn. The truncated partial is discarded
    # (clean re-ask), so the count lives in model_requests, not in persisted history.
    assert turn.model_requests >= 2, (
        f"expected at least one length-retry run; got {turn.model_requests} model request(s)"
    )
    # No duplicated user prompt: the originating prompt appears exactly once.
    essay_prompt = "Write a 5-paragraph essay about why Python is popular. "
    prompt_occurrences = sum(
        1
        for m in turn.messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
        and isinstance(p.content, str)
        and essay_prompt in p.content
    )
    assert prompt_occurrences == 1, (
        f"user prompt must appear exactly once; found {prompt_occurrences} copies"
    )
    # History ends on the complete answer, not a dangling truncated/assistant artifact.
    assert isinstance(turn.messages[-1], ModelResponse), (
        f"history must end on a ModelResponse; ended on {type(turn.messages[-1]).__name__}"
    )


@pytest.mark.asyncio
async def test_overflow_warning_uses_provider_input_count() -> None:
    """Overflow warning fires off the provider's real input count, not a chars/4 estimate.

    After dropping ``last_reported_input_tokens``, ``_check_output_limits`` re-sources the
    final request's input tokens straight from the ``AgentRunResult``'s last ``ModelResponse``
    (provider ground-truth). Run a real turn, then shrink ``model_max_context_tokens`` below the
    provider-reported input so the ratio crosses 1.0 and the "Context limit reached" status
    fires carrying that exact provider count.
    """
    deps = _make_deps()
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await _AGENT.run(
            "Say hello.",
            deps=deps,
            model_settings=SETTINGS_NO_MCP.llm.noreason_model_settings(),
        )

    provider_input = result.response.usage.input_tokens
    assert provider_input > 0, "provider must report a non-zero input token count"

    # Shrink the window below the provider-reported input so ratio >= 1.0.
    deps.model_max_context_tokens = provider_input - 1
    frontend = SilentFrontend()
    turn_state = _TurnState(current_input=None, current_history=[], latest_result=result)

    _check_output_limits(turn_state, deps, frontend)

    assert any(
        f"Context limit reached ({provider_input:,} / {deps.model_max_context_tokens:,} tokens)"
        in s
        for s in frontend.statuses
    ), f"expected provider-count overflow warning; got {frontend.statuses}"
