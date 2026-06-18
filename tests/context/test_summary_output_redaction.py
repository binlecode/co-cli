"""Output-side credential redaction on the compaction summary, and the config
flag that gates it. Redaction is asserted on a real ``summarize_messages`` call
against the configured model with a non-empty ``redact_patterns`` whose pattern
targets a token the model GENERATES (a section header that never appears in the
input), so a vacuous pass is impossible.
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

from co_cli.context.summarization import summarize_messages
from co_cli.deps import CoDeps, CoSessionState
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_MESSAGES = [
    ModelRequest(parts=[UserPromptPart(content="Add password hashing to signup. Use Argon2id.")]),
    ModelResponse(parts=[TextPart(content="Done — signup now uses Argon2id hashing.")]),
]


@pytest.mark.asyncio
async def test_summarize_messages_redacts_output_when_flag_on():
    """With the flag on (default), a pattern matching a model-generated section
    header is replaced with ``[REDACTED]`` in the returned summary."""
    config = SETTINGS_NO_MCP.model_copy(deep=True)
    config.observability.redact_patterns = ["Active Task"]
    config.observability.redact_summary_output = True
    deps = CoDeps(
        shell=ShellBackend(),
        model=build_model(SETTINGS_NO_MCP.llm),
        config=config,
        session=CoSessionState(),
    )

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(deps, _MESSAGES)

    assert "[REDACTED]" in result, f"output redaction did not fire:\n{result}"
    assert "Active Task" not in result, f"redacted token survived:\n{result}"


@pytest.mark.asyncio
async def test_summarize_messages_skips_output_redaction_when_flag_off():
    """With the flag off, the same model-generated header survives unredacted —
    proving the flag actually gates the output-side pass."""
    config = SETTINGS_NO_MCP.model_copy(deep=True)
    config.observability.redact_patterns = ["Active Task"]
    config.observability.redact_summary_output = False
    deps = CoDeps(
        shell=ShellBackend(),
        model=build_model(SETTINGS_NO_MCP.llm),
        config=config,
        session=CoSessionState(),
    )

    await ensure_ollama_warm(TEST_LLM.model)
    async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
        result = await summarize_messages(deps, _MESSAGES)

    assert "Active Task" in result, f"flag-off run unexpectedly redacted the header:\n{result}"
