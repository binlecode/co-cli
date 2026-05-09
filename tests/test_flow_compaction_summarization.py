"""Tests for compaction summarization, token estimation, and budget resolution."""

import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP, TEST_LLM
from tests._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS

from co_cli.context._compaction_markers import static_marker, summary_marker
from co_cli.context.summarization import (
    _SUMMARIZE_PROMPT,
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


def test_resolve_compaction_budget_uses_model_max_ctx_when_probed():
    """resolve_compaction_budget must return model_max_ctx when set on deps."""
    probed = 32_768
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=probed,
    )
    assert resolve_compaction_budget(deps) == probed


def test_resolve_compaction_budget_returns_model_max_ctx_after_bootstrap_fallback():
    """resolve_compaction_budget returns deps.model_max_ctx — bootstrap always sets it
    (probed value or max_ctx ceiling on probe failure / non-Ollama providers)."""
    fallback = SETTINGS_NO_MCP.llm.max_ctx
    deps = CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        config=SETTINGS_NO_MCP,
        session=CoSessionState(),
        model_max_ctx=fallback,
    )
    assert resolve_compaction_budget(deps) == fallback


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


def test_summarize_prompt_template_does_not_embed_prior_summary():
    """_SUMMARIZE_PROMPT must not contain iterative-branch markers.

    Regression: with branch B removed, the prior summary can only reach the
    LLM once — via message_history (as SUMMARY_MARKER in dropped messages).
    The prompt template must never re-embed it. If 'PREVIOUS SUMMARY:' appears
    in the template, the LLM would see the prior summary twice.
    """
    assert "PREVIOUS SUMMARY:" not in _SUMMARIZE_PROMPT
    assert "NEW TURNS TO INCORPORATE" not in _SUMMARIZE_PROMPT
    # Integration instruction must survive — carry-forward rule intact
    assert "integrate its content" in _SUMMARIZE_PROMPT


def test_static_marker_proactive_shape_contains_verbatim_phrase():
    """static_marker with has_tail=True must say 'preserved verbatim'."""
    marker = static_marker(5, has_tail=True)
    content = marker.parts[0].content
    assert "preserved verbatim" in content
    assert "next message" not in content


def test_static_marker_compact_shape_omits_verbatim_phrase():
    """/compact-shape static_marker (has_tail=False) must say 'next message', not 'preserved verbatim'."""
    marker = static_marker(5, has_tail=False)
    content = marker.parts[0].content
    assert "preserved verbatim" not in content
    assert "next message" in content


def test_summary_marker_proactive_shape_contains_verbatim_phrase():
    """summary_marker with has_tail=True must say 'preserved verbatim'."""
    marker = summary_marker(5, "## Active Task\nUser asked: 'foo'", has_tail=True)
    content = marker.parts[0].content
    assert "preserved verbatim" in content
    assert "AFTER this summary" in content
    assert "next message" not in content


def test_summary_marker_compact_shape_omits_verbatim_phrase():
    """/compact-shape summary_marker (has_tail=False) must say 'next message', not 'preserved verbatim'."""
    marker = summary_marker(5, "## Active Task\nUser asked: 'foo'", has_tail=False)
    content = marker.parts[0].content
    assert "preserved verbatim" not in content
    assert "next message" in content
    assert "AFTER this summary" not in content
