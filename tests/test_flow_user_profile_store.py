"""Tests for USER.md profile storage — read/write round-trip + budget enforcement.

All I/O is real against a tmp_path-rooted profile file (never the user-global
~/.co-cli/USER.md), matching the explicit-path convention CoDeps uses for
memory_dir / sessions_dir.
"""

from pathlib import Path

import pytest

from co_cli.memory.user_profile import (
    UserProfileBudgetError,
    read_user_profile,
    write_user_profile,
)


def test_absent_profile_reads_empty(tmp_path: Path) -> None:
    """A profile that was never written reads as the empty string."""
    assert read_user_profile(tmp_path / "USER.md") == ""


def test_write_within_budget_round_trips(tmp_path: Path) -> None:
    """Writing within budget persists the exact text for the next read."""
    path = tmp_path / "USER.md"
    text = "User prefers concise answers and works in Python."
    write_user_profile(path, text, char_budget=1500)
    assert read_user_profile(path) == text


def test_write_over_budget_rejected_and_leaves_file_untouched(tmp_path: Path) -> None:
    """An over-budget write raises with usage reported and does not overwrite."""
    path = tmp_path / "USER.md"
    kept = "kept profile"
    write_user_profile(path, kept, char_budget=1500)

    with pytest.raises(UserProfileBudgetError) as excinfo:
        write_user_profile(path, "x" * 51, char_budget=50)

    assert excinfo.value.attempted == 51
    assert excinfo.value.char_budget == 50
    assert read_user_profile(path) == kept
