"""Skill lifecycle helpers — reload, index, and run-state cleanup."""

from __future__ import annotations

import os

from co_cli.deps import CoDeps


def refresh_skills(deps: CoDeps) -> None:
    """Reload skills from disk, update deps.skill_commands, and sync the skill index.

    Idempotent — safe to call on every write that changes the skill catalog.
    """
    from co_cli.skills.loader import load_skills
    from co_cli.skills.registry import set_skill_commands

    new_skills = load_skills(
        deps.skills_dir,
        deps.config,
        user_skills_dir=deps.user_skills_dir,
    )
    set_skill_commands(new_skills, deps)
    if deps.skill_index is not None:
        for name, skill in new_skills.items():
            user_path = deps.user_skills_dir / f"{name}.md"
            skill_path = (
                str(user_path) if user_path.is_file() else str(deps.skills_dir / f"{name}.md")
            )
            deps.skill_index.upsert(name, skill.description, skill_path)
        for stale in deps.skill_index.list_names() - set(new_skills):
            deps.skill_index.remove(stale)


def cleanup_skill_run_state(saved_env: dict[str, str | None], deps: CoDeps) -> None:
    """Restore saved skill-run env vars and clear active skill session state."""
    for k, v in saved_env.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    deps.runtime.active_skill_name = None
