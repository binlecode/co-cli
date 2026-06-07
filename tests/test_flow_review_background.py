"""Behavioral tests for reviewer child-deps isolation (B3 contract).

Real CoDeps, no monkeypatching. Verifies the daemon reviewer contract:
fork_deps_for_reviewer + refresh_skills(child) surfaces disk-current skills
while leaving the parent's registry unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from tests._settings import SETTINGS_NO_MCP


@pytest.fixture(autouse=True)
def _restore_co_home() -> Generator[None, None, None]:
    original = os.environ.get("CO_HOME")
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original
    # _make_deps reloads config.core against the temp CO_HOME; reload it back so the
    # module-level USER_DIR binding does not leak the (now-deleted) temp dir to later tests.
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)


def _make_deps(tmp_path: Path, *, review_enabled: bool = True):
    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from co_cli.deps import CoDeps
    from co_cli.llm.factory import build_model
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={
            "skills": SETTINGS_NO_MCP.skills.model_copy(
                update={
                    "review_enabled": review_enabled,
                }
            )
        }
    )
    deps = CoDeps(
        shell=ShellBackend(),
        config=config,
        model=build_model(SETTINGS_NO_MCP.llm),
    )
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


def _write_skill_to_disk(user_skills_dir: Path, name: str, description: str) -> Path:
    path = user_skills_dir / f"{name}.md"
    path.write_text(
        "---\n"
        f"description: {description}\n"
        "user-invocable: false\n"
        "disable-model-invocation: false\n"
        "---\n\n"
        f"# {name}\n\nBody.\n"
    )
    return path


# ---------------------------------------------------------------------------
# B3 contract — refresh_skills on child deps surfaces disk-current skills
# ---------------------------------------------------------------------------


def test_child_deps_refresh_surfaces_disk_skill_when_parent_registry_stale(
    tmp_path: Path,
) -> None:
    """The daemon reviewer depends on this contract:
    fork → refresh_skills(child_deps) → render_skill_manifest(child_deps.skill_index)
    surfaces skills written to disk after parent's index was last loaded.
    """
    from co_cli.context.manifests.skill_manifest import render_skill_manifest
    from co_cli.deps import fork_deps_for_reviewer
    from co_cli.skills.lifecycle import refresh_skills

    deps = _make_deps(tmp_path)
    assert deps.skill_index == {}, "parent starts with empty index (stale)"

    _write_skill_to_disk(deps.user_skills_dir, "pass-a-skill", "Created by a prior review pass")

    child = fork_deps_for_reviewer(deps)
    # Before refresh: child shares parent's stale (empty) reference by value.
    assert "pass-a-skill" not in child.skill_index

    refresh_skills(child)

    assert "pass-a-skill" in child.skill_index
    manifest = render_skill_manifest(child.skill_index, child.skills_dir, child.user_skills_dir)
    assert 'name="pass-a-skill"' in manifest


def test_child_refresh_does_not_mutate_parent_registry(tmp_path: Path) -> None:
    """refresh_skills(child) must not retroactively populate the parent's index.

    Verifies set_skill_index rebinds only the receiving deps — the parent
    keeps its original (stale) snapshot, which is the failure mode the reorder
    is defending against.
    """
    from co_cli.deps import fork_deps_for_reviewer
    from co_cli.skills.lifecycle import refresh_skills

    deps = _make_deps(tmp_path)
    parent_initial_index = deps.skill_index
    _write_skill_to_disk(deps.user_skills_dir, "pass-a-skill", "x")

    child = fork_deps_for_reviewer(deps)
    refresh_skills(child)

    # Parent index remains the same object and stays empty.
    assert deps.skill_index is parent_initial_index
    assert deps.skill_index == {}
    # Child got a fresh dict.
    assert child.skill_index is not parent_initial_index
    assert "pass-a-skill" in child.skill_index
