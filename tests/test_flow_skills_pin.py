"""Behavioural tests for /skills pin, /skills unpin, and /skills usage CLI subcommands."""

from __future__ import annotations

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.commands.skills import _cmd_skills_pin, _cmd_skills_usage
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import console
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for CLI command tests
---

Do the test task.
"""


def _make_ctx(tmp_path: Path) -> CommandContext:
    skill_index = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=tmp_path)
    deps = CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        session=CoSessionState(),
        skill_index=skill_index,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=tmp_path,
        tool_results_dir=tmp_path / "tool-results",
    )
    return CommandContext(message_history=[], deps=deps, agent=None)  # type: ignore[arg-type]


def _capture_output(fn) -> str:
    with console.capture() as cap:
        fn()
    return cap.get()


def test_pin_agent_created_skill_sets_flag(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "my-skill", pinned=True))
    assert "pinned" in output.lower()
    record = skill_usage.read_record(ctx.deps, "my-skill")
    assert record is not None
    assert record["pinned"] is True


def test_unpin_clears_flag(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.set_pinned(ctx.deps, "my-skill", True)

    output = _capture_output(lambda: _cmd_skills_pin(ctx, "my-skill", pinned=False))
    assert "unpinned" in output.lower()
    record = skill_usage.read_record(ctx.deps, "my-skill")
    assert record is not None
    assert record["pinned"] is False


def test_pin_bundled_skill_rejected(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "doctor", pinned=True))
    assert "bundled" in output.lower()
    assert skill_usage.read_record(ctx.deps, "doctor") is None


def test_pin_unknown_skill_rejected(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "no-such-skill", pinned=True))
    assert "not found" in output.lower()
    assert skill_usage.read_record(ctx.deps, "no-such-skill") is None


def test_pin_empty_name_prints_usage(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "", pinned=True))
    assert "usage" in output.lower()


# ---------------------------------------------------------------------------
# /skills usage
# ---------------------------------------------------------------------------


def test_usage_empty_library(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "No skill usage records" in output


def test_usage_lists_agent_created_skill(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "my-skill")
    skill_usage.bump_view(ctx.deps, "my-skill")

    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "my-skill" in output
    assert "2" in output


def test_usage_named_skill_prints_full_record(tmp_path: Path) -> None:
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "my-skill")
    skill_usage.bump_view(ctx.deps, "my-skill")
    skill_usage.bump_view(ctx.deps, "my-skill")

    output = _capture_output(lambda: _cmd_skills_usage(ctx, "my-skill"))
    # The named-record render is a Field|Value table — assert the real stored counts
    # show through on their own rows, not merely that the labels are present.
    view_row = next(line for line in output.splitlines() if "view_count" in line)
    use_row = next(line for line in output.splitlines() if "use_count" in line)
    assert "3" in view_row
    assert "0" in use_row


def test_usage_named_skill_with_no_record(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_usage(ctx, "nonexistent-skill-xyz"))
    assert "No usage record" in output


def test_usage_excludes_bundled_skill_entries(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    skill_usage.bump_view(ctx.deps, "doctor")
    output = _capture_output(lambda: _cmd_skills_usage(ctx, ""))
    assert "doctor" not in output
    assert skill_usage.read_record(ctx.deps, "doctor") is None
