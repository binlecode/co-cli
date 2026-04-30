"""Tests for compaction summarization, token estimation, and budget resolution."""

import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.context.summarization import (
    estimate_message_tokens,
    resolve_compaction_budget,
    summarize_messages,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_LLM_MODEL = build_model(SETTINGS_NO_MCP.llm)
_DEPS = CoDeps(
    shell=ShellBackend(), model=_LLM_MODEL, config=SETTINGS_NO_MCP, session=CoSessionState()
)

_SAMPLE_MESSAGES = [
    ModelRequest(
        parts=[UserPromptPart(content="Write a function that reverses a string in Python.")]
    ),
    ModelResponse(
        parts=[
            TextPart(
                content=(
                    "def reverse_string(s: str) -> str:\n    return s[::-1]\n\n"
                    "This function uses Python's slice syntax to reverse the string."
                )
            )
        ]
    ),
    ModelRequest(parts=[UserPromptPart(content="Now add a test for it.")]),
    ModelResponse(
        parts=[
            TextPart(
                content=(
                    "def test_reverse_string():\n    assert reverse_string('hello') == 'olleh'\n"
                    "    assert reverse_string('') == ''\n    assert reverse_string('a') == 'a'"
                )
            )
        ]
    ),
]


# ---------------------------------------------------------------------------
# Deterministic helpers — no LLM
# ---------------------------------------------------------------------------


def test_estimate_message_tokens_grows_with_content():
    """Token estimate must grow proportionally with content length."""
    short = [ModelRequest(parts=[UserPromptPart(content="hi")])]
    long = [ModelRequest(parts=[UserPromptPart(content="hi " * 1000)])]
    assert estimate_message_tokens(long) > estimate_message_tokens(short)


def test_estimate_message_tokens_empty_list():
    """Token estimate for an empty message list must be zero."""
    assert estimate_message_tokens([]) == 0


def test_resolve_compaction_budget_prefers_explicit_context_window():
    """resolve_compaction_budget must return context_window when one is provided."""
    budget = resolve_compaction_budget(SETTINGS_NO_MCP, context_window=32_000)
    assert budget == 32_000


def test_resolve_compaction_budget_falls_back_when_none():
    """resolve_compaction_budget must return a plausible context window when context_window is None."""
    budget = resolve_compaction_budget(SETTINGS_NO_MCP, context_window=None)
    assert budget >= 1_000


# ---------------------------------------------------------------------------
# LLM-backed summarization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_messages_from_scratch_returns_structured_text():
    """From-scratch summarizer must return non-empty text with at least one section header."""
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(_DEPS, _SAMPLE_MESSAGES)
    assert isinstance(result, str)
    assert result.strip()
    assert "##" in result or "Active Task" in result or "Completed" in result


@pytest.mark.asyncio
async def test_summarize_messages_iterative_incorporates_new_turns():
    """Iterative summarizer must produce output that incorporates both prior summary and new turns."""
    prior_summary = (
        "## Active Task\nUser asked: 'Write a function that reverses a string in Python.'\n"
        "## Completed Actions\n1. WRITE reverse_string() — slice-based reversal"
    )
    new_messages = [
        ModelRequest(parts=[UserPromptPart(content="Now add a test for it.")]),
        ModelResponse(
            parts=[TextPart(content="Added test_reverse_string() with three assertions.")]
        ),
    ]
    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(_DEPS, new_messages, previous_summary=prior_summary)
    assert isinstance(result, str)
    assert result.strip()
    assert "reverse" in result.lower()
