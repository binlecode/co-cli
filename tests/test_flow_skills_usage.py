"""Behavioural tests for /skills usage CLI subcommand."""

from __future__ import annotations

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.commands.skills import _cmd_skills_usage
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import console
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for /skills usage tests
---

Do the test task.
"""


def _make_ctx(tmp_path: Path) -> CommandContext:
    skill_commands = load_skills(_BUNDLED_SKILLS_DIR, SETTINGS, user_skills_dir=tmp_path)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        skill_commands=skill_commands,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    return CommandContext(message_history=[], deps=deps, agent=None)  # type: ignore[arg-type]


def _capture_output(fn) -> str:
    with console.capture() as cap:
        fn()
    return cap.get()


def test_usage_empty_library(tmp_path: Path) -> None:
    """/skills usage prints a helpful hint when no records exist."""
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "No skill usage records" in output


def test_usage_lists_agent_created_skill(tmp_path: Path) -> None:
    """/skills usage table includes an agent-created skill with its counters."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "my-skill")
    skill_usage.bump_view(ctx.deps, "my-skill")

    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "my-skill" in output
    assert "2" in output


def test_usage_named_skill_prints_full_record(tmp_path: Path) -> None:
    """/skills usage <name> prints all fields for one skill."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "my-skill")

    output = _capture_output(lambda: _cmd_skills_usage(ctx, "my-skill"))
    for field in (
        "use_count",
        "view_count",
        "patch_count",
        "created_at",
        "last_used_at",
        "last_viewed_at",
        "last_patched_at",
        "state",
        "pinned",
    ):
        assert field in output, f"expected field {field!r} in output"


def test_usage_named_skill_with_no_record(tmp_path: Path) -> None:
    """/skills usage <name> prints a hint when no record exists for that name."""
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_usage(ctx, "nonexistent-skill-xyz"))
    assert "No usage record" in output


def test_usage_excludes_bundled_skill_entries(tmp_path: Path) -> None:
    """A bundled skill that was viewed produces no sidecar row (hooks filter)."""
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "doctor")
    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "doctor" not in output
