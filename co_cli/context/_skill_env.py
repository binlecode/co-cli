"""Skill-run environment state helpers."""

from __future__ import annotations

import os

from co_cli.deps import CoDeps


def cleanup_skill_run_state(saved_env: dict[str, str | None], deps: CoDeps) -> None:
    """Restore saved skill-run env vars and clear active skill session state."""
    for k, v in saved_env.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    deps.runtime.active_skill_name = None
