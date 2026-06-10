"""Skill lifecycle helpers — reload, index, run-state cleanup, and file discovery."""

from __future__ import annotations

import os
from pathlib import Path

from co_cli.deps import CoDeps


def discover_skill_files(bundled_dir: Path, user_dir: Path) -> list[Path]:
    """Return sorted <name>/SKILL.md paths from both dirs (bundled first, user second).

    Skips dirs that don't exist.
    """
    result: list[Path] = []
    if bundled_dir.exists():
        result.extend(sorted(bundled_dir.glob("*/SKILL.md")))
    if user_dir.exists():
        result.extend(sorted(user_dir.glob("*/SKILL.md")))
    return result


def read_skill_meta(skill_path: Path) -> dict:
    """Read frontmatter metadata from a skill .md file. Returns {} on any read or parse error."""
    from co_cli.memory.frontmatter import parse_frontmatter

    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    meta, _ = parse_frontmatter(content)
    return meta


def refresh_skills(deps: CoDeps) -> None:
    """Reload skills from disk and replace deps.skill_catalog.

    Idempotent — safe to call on every write that changes the skill catalog.
    """
    from co_cli.skills.index import set_skill_catalog
    from co_cli.skills.loader import load_skills

    new_skills = load_skills(
        deps.skills_dir,
        user_skills_dir=deps.user_skills_dir,
    )
    set_skill_catalog(new_skills, deps)


def cleanup_skill_run_state(saved_env: dict[str, str | None], deps: CoDeps) -> None:
    """Restore saved skill-run env vars and clear active skill session state."""
    for k, v in saved_env.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    deps.runtime.active_skill_name = None
    deps.runtime.active_skill_env = {}
