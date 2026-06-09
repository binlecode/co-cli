"""Unit tests: skill_create/skill_edit/skill_patch reset model_requests_since_skill_review to 0.

No LLM. Real filesystem writes.
Verifies that each mutating action resets the session counter to 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage
from tests._settings import SETTINGS

from co_cli.agent.core import build_native_toolset
from co_cli.deps import CoDeps, CoSessionState
from co_cli.skills.loader import load_skills
from co_cli.tools.shell_backend import ShellBackend
from co_cli.tools.system.skills import skill_create, skill_delete, skill_edit, skill_patch

_BUNDLED_SKILLS_DIR = Path("co_cli/skills")

_VALID_SKILL = """\
---
description: A test skill for manage-resets tests
---

# test-skill

**Invocation:** /test-skill

## Phase 1 — Do the thing

Do the thing.
"""

_VALID_SKILL_V2 = """\
---
description: An updated test skill for manage-resets tests
---

# test-skill

**Invocation:** /test-skill

## Phase 1 — Do the thing

Do the thing (updated).
"""


def _make_deps(tmp_path: Path, initial_iters: int = 8) -> CoDeps:
    user_skills_dir = tmp_path / "skills"
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=user_skills_dir)
    _, tool_catalog = build_native_toolset()
    session = CoSessionState()
    session.model_requests_since_skill_review = initial_iters
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS,
        tool_catalog=tool_catalog,
        session=session,
        skill_catalog=skill_catalog,
        skills_dir=_BUNDLED_SKILLS_DIR,
        user_skills_dir=user_skills_dir,
        tool_results_dir=tmp_path / "tool-results",
    )


def _make_ctx(deps: CoDeps) -> RunContext[CoDeps]:
    return RunContext(deps=deps, model=None, usage=RunUsage(), tool_name="skill_create")


def _is_error(result) -> bool:
    return result.metadata is not None and result.metadata.get("error") is True


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_resets_model_requests_since_skill_review(tmp_path: Path) -> None:
    """skill_create resets model_requests_since_skill_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_iters=8)
    ctx = _make_ctx(deps)

    result = await skill_create(ctx, name="test-skill", content=_VALID_SKILL)

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.model_requests_since_skill_review == 0


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_resets_model_requests_since_skill_review(tmp_path: Path) -> None:
    """skill_edit resets model_requests_since_skill_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_iters=6)
    user_skills_dir = deps.user_skills_dir
    skill_path = user_skills_dir / "test-skill.md"
    skill_path.write_text(_VALID_SKILL, encoding="utf-8")
    # Reload skill_catalog so edit finds the skill
    deps.skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=user_skills_dir)

    ctx = _make_ctx(deps)

    result = await skill_edit(ctx, name="test-skill", content=_VALID_SKILL_V2)

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.model_requests_since_skill_review == 0


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_resets_model_requests_since_skill_review(tmp_path: Path) -> None:
    """skill_patch resets model_requests_since_skill_review to 0 on success."""
    deps = _make_deps(tmp_path, initial_iters=4)
    user_skills_dir = deps.user_skills_dir
    skill_path = user_skills_dir / "test-skill.md"
    skill_path.write_text(_VALID_SKILL, encoding="utf-8")
    deps.skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=user_skills_dir)

    ctx = _make_ctx(deps)

    result = await skill_patch(
        ctx,
        name="test-skill",
        old_string="Do the thing.",
        new_string="Do the thing (patched).",
        replace_all=False,
    )

    assert not _is_error(result), f"Expected success, got error: {result}"
    assert deps.session.model_requests_since_skill_review == 0


# ---------------------------------------------------------------------------
# delete does NOT reset (not in spec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_does_not_reset_model_requests_since_skill_review(tmp_path: Path) -> None:
    """skill_delete does not reset model_requests_since_skill_review."""
    deps = _make_deps(tmp_path, initial_iters=5)
    user_skills_dir = deps.user_skills_dir
    skill_path = user_skills_dir / "test-skill.md"
    skill_path.write_text(_VALID_SKILL, encoding="utf-8")
    deps.skill_catalog = load_skills(_BUNDLED_SKILLS_DIR, user_skills_dir=user_skills_dir)

    ctx = _make_ctx(deps)

    result = await skill_delete(ctx, name="test-skill")

    assert not _is_error(result), f"Expected success, got error: {result}"
    # delete does not reset the counter
    assert deps.session.model_requests_since_skill_review == 5
