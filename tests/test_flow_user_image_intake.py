"""REPL user-image-intake wiring (TASK-4).

A lone image path the user drags into the terminal is detected BEFORE slash dispatch (so a
bare absolute path, which starts with "/", is honored rather than rejected as an unknown
command) and spliced into a multimodal turn. A blind model gets one notice and runs
text-only. A slash-delegated body that happens to be an image path is NOT attached.

Real model per test policy (no mocks). The live turns skip on a text-only host.
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.toolsets import FunctionToolset
from tests._ollama import ensure_ollama_warm
from tests._settings import SETTINGS_NO_MCP as _CONFIG_NO_MCP
from tests._settings import TEST_LLM
from tests._timeouts import LLM_TOOL_CONTEXT_TIMEOUT_SECS

from co_cli.agent.build import build_orchestrator
from co_cli.agent.spec import OrchestratorSpec
from co_cli.agent.toolset import _CallSeamToolset
from co_cli.check import probe_ollama_model
from co_cli.commands.completer import SlashCommandCompleter
from co_cli.commands.core import BUILTIN_COMMANDS
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.headless import HeadlessFrontend as SilentFrontend
from co_cli.llm.factory import build_model
from co_cli.main import IterationState, _attach_user_image, _handle_one_input
from co_cli.skills.skill_types import SkillInfo
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.vision.intake import detect_lone_image_path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "vision" / "red_square.png"
_LLM_MODEL = build_model(_CONFIG_NO_MCP.llm)

# Minimal spec — no system prompt, no per-turn instructions, no tool schemas.
# The intake behavior under test (lone-image-path detection, BinaryContent
# attachment, and the model answering about the image) is independent of the
# orchestrator's prompt and tool catalog; the full spec only inflates the
# prefill (~16k tokens), making the live turn fragile to local-GPU throttling
# under suite load. Prefilling only the user prompt + image keeps it fast.
_MINIMAL_SPEC = OrchestratorSpec(
    static_instruction_builders=(),
    per_turn_instructions=(),
    history_processors=(),
)

_AGENT_VISION_CAPABLE = (
    True if TEST_LLM.uses_gemini() else probe_ollama_model(TEST_LLM.host, TEST_LLM.model).vision
)


def _make_deps(
    tmp_path: Path,
    *,
    agent_vision_capable: bool,
    skill_catalog: dict[str, SkillInfo] | None = None,
) -> CoDeps:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    empty_toolset: FunctionToolset[CoDeps] = FunctionToolset()
    return CoDeps(
        shell=ShellBackend(),
        model=_LLM_MODEL,
        toolset=_CallSeamToolset(empty_toolset),
        config=_CONFIG_NO_MCP,
        session=CoSessionState(session_path=sessions_dir / "2026-06-14T120000.000-abcd1234.jsonl"),
        agent_vision_capable=agent_vision_capable,
        model_max_context_tokens=_CONFIG_NO_MCP.llm.max_context_tokens,
        workspace_dir=_REPO_ROOT,
        file_search_roots=[_REPO_ROOT],
        sessions_dir=sessions_dir,
        tool_results_dir=tmp_path / "tool-results",
        user_skills_dir=tmp_path / "skills",
        skill_catalog=skill_catalog or {},
    )


def _first_user_prompt_content(history: list) -> object:
    for msg in history:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, UserPromptPart):
                    return part.content
    return None


def _final_text(history: list) -> str:
    texts = [
        part.content
        for msg in history
        if isinstance(msg, ModelResponse)
        for part in msg.parts
        if isinstance(part, TextPart)
    ]
    return texts[-1] if texts else ""


def _initial_state() -> IterationState:
    return IterationState(message_history=[], last_interrupt_time=0.0, should_exit=False)


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; intake attach N/A"
)
@pytest.mark.asyncio
async def test_lone_absolute_image_path_attaches_and_answers(tmp_path: Path) -> None:
    """A bare absolute image path (starts with '/') is honored before slash dispatch.

    The user turn carries BinaryContent and the vision-capable model answers about the image.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    deps = _make_deps(tmp_path, agent_vision_capable=True)
    agent = build_orchestrator(_MINIMAL_SPEC, deps)
    frontend = SilentFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        result = await _handle_one_input(
            user_input=str(_FIXTURE),
            eof=False,
            state=_initial_state(),
            deps=deps,
            agent=agent,
            frontend=frontend,
            completer=SlashCommandCompleter(),
            now=0.0,
            queue=deque(),
        )

    content = _first_user_prompt_content(result.message_history)
    assert isinstance(content, list)
    assert any(isinstance(part, BinaryContent) for part in content)
    assert "red" in _final_text(result.message_history).lower()


def test_blind_model_image_path_one_notice_text_only(tmp_path: Path, capsys) -> None:
    """A blind model gets exactly one notice and the turn input stays text-only."""
    deps = _make_deps(tmp_path, agent_vision_capable=False)
    path = detect_lone_image_path(str(_FIXTURE), deps.workspace_dir)
    assert path is not None

    turn_input = _attach_user_image(str(_FIXTURE), path, deps)
    assert turn_input == str(_FIXTURE)

    normalized = " ".join(capsys.readouterr().out.split())
    assert normalized.count("can't see it") == 1


@pytest.mark.skipif(
    not _AGENT_VISION_CAPABLE, reason="agent model is not vision-capable; intake attach N/A"
)
@pytest.mark.asyncio
async def test_slash_delegated_image_path_not_attached(tmp_path: Path) -> None:
    """A skill whose delegated body is an image path runs text-only — no auto-attach.

    The slash-delegated branch never invokes the detector, so the delegated user prompt
    reaches the turn as a plain string even on a vision-capable model.
    """
    await ensure_ollama_warm(TEST_LLM.model, TEST_LLM.host)
    skill = SkillInfo(name="imgskill", body=str(_FIXTURE))
    deps = _make_deps(tmp_path, agent_vision_capable=True, skill_catalog={"imgskill": skill})
    agent = build_orchestrator(_MINIMAL_SPEC, deps)
    frontend = SilentFrontend(approval_response="y")

    async with asyncio.timeout(LLM_TOOL_CONTEXT_TIMEOUT_SECS):
        result = await _handle_one_input(
            user_input="/imgskill",
            eof=False,
            state=_initial_state(),
            deps=deps,
            agent=agent,
            frontend=frontend,
            completer=SlashCommandCompleter(),
            now=0.0,
            queue=deque(),
        )

    content = _first_user_prompt_content(result.message_history)
    assert isinstance(content, str)
    assert content == str(_FIXTURE)


def test_detect_returns_none_for_every_slash_command() -> None:
    """Collision safety: no slash command (builtin or arg'd) is read as a lone image path.

    The detector requires an image suffix; no command name ends in one, so the command set
    and the image-path set are disjoint — slash dispatch is never shadowed.
    """
    for name in BUILTIN_COMMANDS:
        assert detect_lone_image_path(f"/{name}", _REPO_ROOT) is None
    # arg'd forms whose args carry an image suffix still resolve to no existing file
    assert detect_lone_image_path("/memory search shot.png", _REPO_ROOT) is None
    assert detect_lone_image_path("/compact logo.jpg", _REPO_ROOT) is None
