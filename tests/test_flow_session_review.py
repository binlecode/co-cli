"""Behavioral tests for the session-end combined review and /skills review CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP

from co_cli.display.core import console


def _make_deps(tmp_path: Path):
    """Real CoDeps with CO_HOME pointed at tmp_path."""
    import os

    os.environ["CO_HOME"] = str(tmp_path)
    # Re-import to pick up the overridden CO_HOME constant.
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"review_enabled": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


@pytest.mark.asyncio
async def test_session_review_disabled_by_config(tmp_path: Path) -> None:
    """_maybe_run_session_review returns immediately when review_enabled=False."""
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"review_enabled": False})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)

    # If it tries to fork an agent it will fail — absence of error proves early return.
    from co_cli.main import _maybe_run_session_review

    await _maybe_run_session_review(deps, [])


@pytest.mark.asyncio
async def test_session_review_skips_when_no_model(tmp_path: Path) -> None:
    """_maybe_run_session_review returns early when deps.model is None."""
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"review_enabled": True})}
    )
    deps = CoDeps(shell=ShellBackend(), config=config)
    assert deps.model is None

    from co_cli.main import _maybe_run_session_review

    await _maybe_run_session_review(deps, [])


def test_write_review_report_creates_json_and_md(tmp_path: Path) -> None:
    """_write_review_report writes run.json + run.md with correct structure."""
    import os

    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from co_cli.agents.session_review import SessionReviewOutput, _write_review_report
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP
    deps = CoDeps(shell=ShellBackend(), config=config)

    output = SessionReviewOutput(
        summary="Updated /foo + new preference: bar",
        skills_patched=["foo"],
        skills_created=[],
        knowledge_created=["pref-bar"],
        knowledge_updated=[],
    )
    usage = RunUsage(requests=3, input_tokens=100, output_tokens=50)
    run_id = "abcdef1234567890"

    _write_review_report(deps, run_id, output, usage, transcript_length=42)

    reviews_dir = core_mod.SESSION_REVIEWS_DIR
    run_dirs = list(reviews_dir.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    json_data = json.loads((run_dir / "run.json").read_text())
    assert json_data["run_id"] == run_id
    assert json_data["summary"] == "Updated /foo + new preference: bar"
    assert json_data["skills_patched"] == ["foo"]
    assert json_data["knowledge_created"] == ["pref-bar"]
    assert json_data["transcript_length"] == 42
    assert "usage" in json_data
    assert json_data["usage"]["requests"] == 3

    md_text = (run_dir / "run.md").read_text()
    assert "# Session Review Report" in md_text
    assert run_id in md_text
    assert "foo" in md_text


def test_session_reviewer_delegation_tools() -> None:
    """discover_delegation_tools('session_reviewer') returns the expected set."""
    from co_cli.agents.core import discover_delegation_tools

    tools = {fn.__name__ for fn in discover_delegation_tools("session_reviewer", SETTINGS_NO_MCP)}
    assert "skill_view" in tools
    assert "skill_manage" in tools
    assert "knowledge_search" in tools
    assert "knowledge_view" in tools
    assert "knowledge_manage" in tools
    # Must NOT include terminal/file/web tools
    assert "shell" not in tools
    assert "file_read" not in tools
    assert "web_fetch" not in tools
    assert "skill_search" not in tools


def test_serialize_messages_include_tool_results_false() -> None:
    """serialize_messages with include_tool_results=False drops ToolReturnPart."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from co_cli.context.summarization import serialize_messages

    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="hello")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="skill_view", args='{"name":"foo"}', tool_call_id="1"),
            ],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="skill_view", content="result", tool_call_id="1")]
        ),
        ModelResponse(parts=[TextPart(content="found it")], model_name="test"),
    ]

    with_results = serialize_messages(messages, [], include_tool_results=True)
    without_results = serialize_messages(messages, [], include_tool_results=False)

    assert "tool_result" in with_results
    assert "tool_result" not in without_results
    assert "tool_call" in without_results
    assert "found it" in without_results


# ---------------------------------------------------------------------------
# /skills review CLI commands
# ---------------------------------------------------------------------------


def _make_review_deps(tmp_path: Path, *, with_model: bool = False):
    """CoDeps with review_enabled=True for CLI tests."""
    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={"skills": SETTINGS_NO_MCP.skills.model_copy(update={"review_enabled": True})}
    )
    if with_model:
        from co_cli.llm.factory import build_model

        model = build_model(SETTINGS_NO_MCP.llm)
    else:
        model = None
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


def _make_review_ctx(deps, message_history=None):
    from co_cli.commands.types import CommandContext

    return CommandContext(
        message_history=message_history if message_history is not None else [],
        deps=deps,
        agent=None,
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_review_run_no_model(tmp_path: Path) -> None:
    """review run prints error when no model is configured."""
    from co_cli.commands.skills import _cmd_skills_review

    deps = _make_review_deps(tmp_path, with_model=False)
    ctx = _make_review_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_review(ctx, "run")

    output = cap.get()
    assert "No model" in output or "model" in output.lower()


@pytest.mark.asyncio
async def test_review_unknown_subcommand(tmp_path: Path) -> None:
    """Unknown review subcommand prints usage."""
    from co_cli.commands.skills import _cmd_skills_review

    deps = _make_review_deps(tmp_path)
    ctx = _make_review_ctx(deps)

    with console.capture() as cap:
        await _cmd_skills_review(ctx, "badcmd")

    output = cap.get()
    assert "Usage" in output


def test_session_review_instructions_include_skills_manifest(tmp_path: Path) -> None:
    """Combined instructions string prepends <available_skills> before SESSION_REVIEW_INSTRUCTIONS."""
    from co_cli.context.manifests.skill_manifest import render_skill_manifest
    from co_cli.skills.session_review_prompts import SESSION_REVIEW_INSTRUCTIONS
    from co_cli.skills.skill_types import SkillConfig

    skill_registry: dict[str, SkillConfig] = {
        "git-workflows": SkillConfig(
            name="git-workflows",
            description="Git branching and merge workflows",
            path=tmp_path / "git-workflows.md",
        ),
        "python-testing": SkillConfig(
            name="python-testing",
            description="Python pytest patterns",
            path=tmp_path / "python-testing.md",
        ),
    }
    manifest = render_skill_manifest(skill_registry, tmp_path, tmp_path)
    combined = (
        f"{manifest}\n\n{SESSION_REVIEW_INSTRUCTIONS}" if manifest else SESSION_REVIEW_INSTRUCTIONS
    )

    assert "<available_skills>" in combined
    assert "git-workflows" in combined
    assert "python-testing" in combined
    assert SESSION_REVIEW_INSTRUCTIONS in combined
