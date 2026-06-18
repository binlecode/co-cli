"""Bounded-retry behavior of the fidelity backstop, plus output-side redaction.

The retry-behavior tests drive ``_verify_and_retry`` with a real in-test ``run_call``
thunk (a plain async function that counts its own invocations and returns crafted
strings — production code, no ``llm_call`` monkeypatch, per testing.md:18). Output
redaction is asserted on a real ``summarize_messages`` call against the configured
model with a non-empty ``redact_patterns``.
"""

import asyncio

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.context.summarization import _verify_and_retry, summarize_messages
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

# Source with three high-signal identifiers the backstop will extract.
_SOURCE = (
    "user: fix the inverted check in co_cli/auth.py:42\n"
    "tool_result: raised InvalidSignatureError\n"
    "user: 'use Argon2 not bcrypt'\n"
)
# A first-pass summary that drops every identifier (over threshold → retry fires).
_DROPS_ALL = "## Goal\nFix an authentication bug the user reported."
# A retry summary that preserves every identifier verbatim.
_KEEPS_ALL = (
    "## Critical Context\nco_cli/auth.py:42 raised InvalidSignatureError\n"
    "## User Corrections\nuse Argon2 not bcrypt"
)


def _counting_thunk(result: str):
    """A real async ``run_call`` thunk that records each invocation's feedback."""
    calls: list[str | None] = []

    async def run_call(feedback: str | None) -> str:
        calls.append(feedback)
        return result

    return run_call, calls


@pytest.mark.asyncio
async def test_retry_fires_once_and_keeps_better_summary():
    """A drops-identifiers first summary triggers exactly one retry; the
    keeps-identifiers retry result is the one returned."""
    run_call, calls = _counting_thunk(_KEEPS_ALL)
    result = await _verify_and_retry(_SOURCE, _DROPS_ALL, run_call)
    assert len(calls) == 1
    assert result == _KEEPS_ALL


@pytest.mark.asyncio
async def test_retry_fires_once_when_both_drop_and_returns_nonempty():
    """When the retry also drops identifiers, the backstop still fires exactly once
    (no second retry) and returns a non-empty summary (accept-best, tie keeps first)."""
    run_call, calls = _counting_thunk("## Goal\nStill a vague recap.")
    result = await _verify_and_retry(_SOURCE, _DROPS_ALL, run_call)
    assert len(calls) == 1
    assert result.strip()


@pytest.mark.asyncio
async def test_retry_exception_is_degrade_safe():
    """A raising retry never loses the good-enough first summary."""

    async def run_call(feedback: str | None) -> str:
        raise RuntimeError("breaker open")

    result = await _verify_and_retry(_SOURCE, _DROPS_ALL, run_call)
    assert result == _DROPS_ALL


@pytest.mark.asyncio
async def test_zero_identifier_source_never_retries():
    """An identifier-free source short-circuits — the thunk is never invoked."""
    run_call, calls = _counting_thunk(_KEEPS_ALL)
    source = "user: please make the wording warmer and friendlier"
    result = await _verify_and_retry(source, _DROPS_ALL, run_call)
    assert calls == []
    assert result == _DROPS_ALL


_DEPS = CoDeps(
    shell=ShellBackend(),
    model=build_model(SETTINGS_NO_MCP.llm),
    config=SETTINGS_NO_MCP,
    session=CoSessionState(),
)

_REDACTION_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="Add password hashing to signup. Use Argon2id.")]),
    ModelResponse(parts=[TextPart(content="Done — signup now uses Argon2id hashing.")]),
]


@pytest.mark.asyncio
async def test_summarize_messages_redacts_output():
    """Output-side redaction is applied to the produced summary. The pattern targets
    a token the model GENERATES (a section header that never appears in the input),
    so a vacuous pass is impossible: the header is replaced with ``[REDACTED]``."""
    config = SETTINGS_NO_MCP.model_copy(deep=True)
    config.observability.redact_patterns = ["Active Task"]
    deps = CoDeps(
        shell=ShellBackend(),
        model=build_model(SETTINGS_NO_MCP.llm),
        config=config,
        session=CoSessionState(),
    )

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(deps, _REDACTION_MESSAGES)

    assert "[REDACTED]" in result, f"output redaction did not fire:\n{result}"
    assert "Active Task" not in result, f"redacted token survived:\n{result}"
