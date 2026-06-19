"""USER.md profile storage — read/write the always-injected user profile.

The user profile is a single model-curated markdown blob (default location
``USER_PROFILE_PATH``), deterministically injected into every session's static
prompt (see the orchestrator's user-profile instruction provider). Its primary
writer is the dream memory reviewer; the main agent writes it only on explicit
user request.

Callers pass the concrete path (``deps.user_profile_path``) so the location is
per-instance like ``memory_dir`` / ``sessions_dir`` rather than a frozen global.

Wholesale rewrite only (no targeted edits) — small-model-friendly. Writes are
atomic and capped at a configured character budget sourced from config.
"""

from __future__ import annotations

from pathlib import Path

from co_cli.fileio.atomic import atomic_write_text


class UserProfileBudgetError(ValueError):
    """Raised when a profile write exceeds the configured character budget.

    Carries the attempted size and the budget so the caller can report current
    usage back to the model and prompt it to consolidate (hermes behavior).
    """

    def __init__(self, attempted: int, char_budget: int) -> None:
        self.attempted = attempted
        self.char_budget = char_budget
        super().__init__(
            f"User profile is {attempted} chars, over the {char_budget}-char budget. "
            "Consolidate and rewrite the whole profile under budget."
        )


def read_user_profile(path: Path) -> str:
    """Return the profile text at ``path``, or ``""`` if the file does not exist."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_user_profile(path: Path, text: str, *, char_budget: int) -> None:
    """Atomically overwrite the whole profile at ``path``.

    Raises ``UserProfileBudgetError`` when ``text`` exceeds ``char_budget`` —
    the file is left untouched so the model can consolidate and retry.
    """
    if len(text) > char_budget:
        raise UserProfileBudgetError(len(text), char_budget)
    atomic_write_text(path, text)
