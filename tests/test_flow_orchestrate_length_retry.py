"""Tests for length-continuation auto-retry in run_turn().

Production path: co_cli/context/orchestrate.py:run_turn() — finish_reason='length' branch.
"""

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import (
    LLM_COMPACTION_SUMMARY_TIMEOUT_SECS,
    PYTEST_PER_TEST_TIMEOUT_SECS,
)

from co_cli.agent.build import build_orchestrator
from co_cli.agent.core import build_native_toolset
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.context.orchestrate import run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

# ---------------------------------------------------------------------------
# Integration — real LLM, noreason path
# ---------------------------------------------------------------------------

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_TOOLSET, _TOOL_INDEX = build_native_toolset(SETTINGS_NO_MCP)


def _make_deps() -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        toolset=_TOOLSET,
        tool_index=_TOOL_INDEX,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=SETTINGS_NO_MCP.llm.max_ctx,
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
    so the first segment is reliably truncated regardless of model verbosity. The retry
    doubles max_tokens each pass (80→160→320→…) until the model completes.

    Asserts the turn succeeds and history shows ≥2 ModelResponse entries.
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
    model_responses = [m for m in turn.messages if isinstance(m, ModelResponse)]
    assert len(model_responses) >= 2, (
        f"expected at least one length-retry segment; "
        f"got {len(model_responses)} ModelResponse(s) in history"
    )
