"""Behavioural tests for /skills pin and /skills unpin CLI subcommands."""

from __future__ import annotations

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.commands.skills import _cmd_skills_pin
from co_cli.commands.types import CommandContext
from co_cli.deps import CoDeps, CoSessionState
from co_cli.display.core import console
from co_cli.skills import usage as skill_usage
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_CONTENT = """\
---
description: A skill for /skills pin tests
---

Do the test task.
"""

_URL_INSTALLED_CONTENT = """\
---
description: A skill installed from a URL
source-url: https://example.com/skill.md
---

Do the URL-installed task.
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


def test_pin_agent_created_skill_sets_flag(tmp_path: Path) -> None:
    """/skills pin <agent-skill> flips pinned to True in the sidecar."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "my-skill", pinned=True))
    assert "pinned" in output.lower()
    assert skill_usage.read_records(ctx.deps)["skills"]["my-skill"]["pinned"] is True


def test_unpin_clears_flag(tmp_path: Path) -> None:
    """/skills unpin <agent-skill> flips pinned to False."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    skill_usage.set_pinned(ctx.deps, "my-skill", True)

    output = _capture_output(lambda: _cmd_skills_pin(ctx, "my-skill", pinned=False))
    assert "unpinned" in output.lower()
    assert skill_usage.read_records(ctx.deps)["skills"]["my-skill"]["pinned"] is False


def test_pin_never_viewed_skill_creates_stub(tmp_path: Path) -> None:
    """Pinning an agent-created skill with no sidecar record creates a stub."""
    (tmp_path / "my-skill.md").write_text(_VALID_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)
    assert "my-skill" not in skill_usage.read_records(ctx.deps).get("skills", {})

    _cmd_skills_pin(ctx, "my-skill", pinned=True)

    record = skill_usage.read_records(ctx.deps)["skills"]["my-skill"]
    assert record["pinned"] is True
    assert record["use_count"] == 0
    assert record["created_at"] is not None


def test_pin_bundled_skill_rejected(tmp_path: Path) -> None:
    """/skills pin on a bundled-only skill prints an explanatory error and doesn't write."""
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "doctor", pinned=True))
    assert "bundled" in output.lower()
    assert "doctor" not in skill_usage.read_records(ctx.deps).get("skills", {})


def test_pin_url_installed_skill_rejected(tmp_path: Path) -> None:
    """/skills pin on a URL-installed skill prints an explanatory error and doesn't write."""
    (tmp_path / "url-skill.md").write_text(_URL_INSTALLED_CONTENT, encoding="utf-8")
    ctx = _make_ctx(tmp_path)

    output = _capture_output(lambda: _cmd_skills_pin(ctx, "url-skill", pinned=True))
    assert "url-installed" in output.lower() or "upstream" in output.lower()
    assert "url-skill" not in skill_usage.read_records(ctx.deps).get("skills", {})


def test_pin_unknown_skill_rejected(tmp_path: Path) -> None:
    """/skills pin on an unknown name prints an error and doesn't write."""
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "no-such-skill", pinned=True))
    assert "not found" in output.lower()
    assert "no-such-skill" not in skill_usage.read_records(ctx.deps).get("skills", {})


def test_pin_empty_name_prints_usage(tmp_path: Path) -> None:
    """/skills pin with no name prints a usage hint."""
    ctx = _make_ctx(tmp_path)
    output = _capture_output(lambda: _cmd_skills_pin(ctx, "", pinned=True))
    assert "usage" in output.lower()
