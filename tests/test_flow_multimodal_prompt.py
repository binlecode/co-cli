"""Multimodal user prompt threading through run_turn (TASK-3).

A user-dragged image is spliced into the user turn as a ``[text, BinaryContent]`` list.
These verify the prompt char-count counts text only (not the part count) and that a real
vision-capable turn answers about an image carried in a list prompt.

Real model per test policy (no mocks). The live turn skips on a text-only host.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import BinaryContent
from pydantic_ai.toolsets import FunctionToolset
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.spec import OrchestratorSpec
from co_cli.agent.toolset import _CallSeamToolset
from co_cli.check import probe_ollama_model
from co_cli.context.orchestrate import _prompt_char_count, run_turn
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.tools.shell_backend import ShellBackend

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "vision" / "red_square.png"
_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)

# Minimal spec — no system prompt, no per-turn instructions, no tool schemas.
# The image-grounding behavior under test (a [text, BinaryContent] list prompt
# threading through run_turn) is independent of the orchestrator's prompt and
# tool catalog; the full spec only inflates the prefill (~16k tokens) and makes
# the live turn fragile to local-GPU throttling under suite load. Prefilling
# only the user prompt + image keeps the turn fast and the assertion sharp.
_MINIMAL_SPEC = OrchestratorSpec(
    name="orchestrator-multimodal-test",
    static_instruction_builders=(),
    per_turn_instructions=(),
    history_processors=(),
)

_AGENT_VISION_CAPABLE = (
    True if TEST_LLM.uses_gemini() else probe_ollama_model(TEST_LLM.host, TEST_LLM.model).vision
)


def _make_deps() -> CoDeps:
    empty_toolset: FunctionToolset[CoDeps] = FunctionToolset()
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        toolset=_CallSeamToolset(empty_toolset),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(),
        agent_vision_capable=_AGENT_VISION_CAPABLE,
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
    )


def test_prompt_char_count_counts_text_not_parts() -> None:
    """A [text, BinaryContent] prompt records the text length, not the part count (2)."""
    pixels = BinaryContent(data=b"\x89PNGfakepixels", media_type="image/png")
    text = "What color is this? One word."
    assert _prompt_char_count([text, pixels]) == len(text)
    assert _prompt_char_count("plain text") == len("plain text")


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; multimodal turn N/A"
)
@pytest.mark.asyncio
async def test_run_turn_accepts_list_prompt_with_image() -> None:
    """A list user prompt carrying real image bytes drives a normal, image-grounded turn."""
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)

    deps = _make_deps()
    agent = build_orchestrator(_MINIMAL_SPEC, deps)
    frontend = SilentFrontend(approval_response="y")
    pixels = BinaryContent(data=_FIXTURE.read_bytes(), media_type="image/png")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        turn = await run_turn(
            agent=agent,
            user_input=["What is the dominant color of this image? Answer in one word.", pixels],
            deps=deps,
            message_history=[],
            frontend=frontend,
        )

    assert turn.outcome == "continue"
    assert "red" in (turn.output or "").lower()
