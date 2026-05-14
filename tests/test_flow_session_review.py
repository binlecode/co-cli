"""Behavioral tests for the session-end combined review."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS_NO_MCP


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
    assert "skill_search" in tools
    assert "skill_view" in tools
    assert "skill_manage" in tools
    assert "knowledge_search" in tools
    assert "knowledge_view" in tools
    assert "knowledge_manage" in tools
    # Must NOT include terminal/file/web tools
    assert "shell" not in tools
    assert "file_read" not in tools
    assert "web_fetch" not in tools


def test_skill_curator_delegation_tools() -> None:
    """discover_delegation_tools('skill_curator') returns skill tools only — no knowledge tools."""
    from co_cli.agents.core import discover_delegation_tools

    tools = {fn.__name__ for fn in discover_delegation_tools("skill_curator", SETTINGS_NO_MCP)}
    assert "skill_search" in tools
    assert "skill_view" in tools
    assert "skill_manage" in tools
    assert "knowledge_manage" not in tools
    assert "knowledge_search" not in tools


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
                ToolCallPart(tool_name="skill_search", args='{"query":"foo"}', tool_call_id="1"),
            ],
            model_name="test",
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name="skill_search", content="result", tool_call_id="1")]
        ),
        ModelResponse(parts=[TextPart(content="found it")], model_name="test"),
    ]

    with_results = serialize_messages(messages, [], include_tool_results=True)
    without_results = serialize_messages(messages, [], include_tool_results=False)

    assert "tool_result" in with_results
    assert "tool_result" not in without_results
    assert "tool_call" in without_results
    assert "found it" in without_results
