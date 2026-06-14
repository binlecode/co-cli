"""Verify the dream reviewers receive the active soul's curation lens.

The per-soul curation lens scopes the active character's retention judgment into
the memory/skill review prompts. These tests assert the observable behavior: the
configured role's curation lens reaches the reviewer's instructions, the lens is
role-specific, and disabling personality falls back to the bare base prompt.
"""

from __future__ import annotations

import pytest
from tests._settings import SETTINGS

from co_cli.daemons.dream._reviewer import (
    _memory_review_instructions,
    _skill_review_instructions,
)
from co_cli.deps import CoDeps, CoSessionState
from co_cli.personality.prompts.loader import load_soul_curation
from co_cli.tools.shell_backend import ShellBackend


def _deps_for(personality: str) -> CoDeps:
    return CoDeps(
        shell=ShellBackend(),
        config=SETTINGS.model_copy(update={"personality": personality}),
        session=CoSessionState(),
        index_store=None,
        memory_store=None,
    )


@pytest.mark.parametrize("role", ["tars", "finch", "jeff"])
@pytest.mark.parametrize("builder", [_memory_review_instructions, _skill_review_instructions])
def test_active_soul_lens_reaches_reviewer(role: str, builder) -> None:
    instructions = builder(_deps_for(role))
    lens = load_soul_curation(role)
    assert lens, f"{role} ships no curation.md"
    assert lens in instructions


def test_lens_is_role_specific() -> None:
    tars = _memory_review_instructions(_deps_for("tars"))
    finch = _memory_review_instructions(_deps_for("finch"))
    jeff = _memory_review_instructions(_deps_for("jeff"))
    assert tars != finch != jeff != tars


def test_no_personality_falls_back_to_base() -> None:
    deps = _deps_for("tars")
    deps.config.personality = None
    instructions = _memory_review_instructions(deps)
    assert load_soul_curation("tars") not in instructions
