"""Behavioral tests for the turn-boundary post-turn hook.

Calls _post_turn_hook directly and asserts on observable state:
- turns_since_memory_review counter (bumped +1 per call)
- model_requests_since_skill_review counter (bumped +model_request_count per call)
- Counter resets to 0 when KICK fires (threshold crossed)

No monkeypatching. No LLM calls.
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


def _make_deps(
    tmp_path: Path,
    *,
    review_enabled: bool,
    memory_interval: int = 5,
    skill_interval: int = 5,
    with_model: bool = True,
):
    """Real CoDeps with CO_HOME pointed at tmp_path."""
    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={
            "skills": SETTINGS_NO_MCP.skills.model_copy(
                update={
                    "review_enabled": review_enabled,
                    "review_memory_nudge_interval": memory_interval,
                    "review_skill_nudge_interval": skill_interval,
                }
            )
        }
    )
    if with_model:
        from co_cli.llm.factory import build_model

        model = build_model(SETTINGS_NO_MCP.llm)
    else:
        model = None
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    deps.user_skills_dir = tmp_path / "skills"
    deps.user_skills_dir.mkdir(parents=True, exist_ok=True)
    return deps


def test_review_disabled_short_circuits_no_state_mutation(tmp_path: Path) -> None:
    """review_enabled=False — hook returns without bumping counters."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=False)
    _post_turn_hook(deps, [], model_request_count=10)

    assert deps.session.turns_since_memory_review == 0
    assert deps.session.model_requests_since_skill_review == 0


def test_no_model_short_circuits_no_state_mutation(tmp_path: Path) -> None:
    """deps.model is None — hook returns without bumping counters."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, with_model=False)
    assert deps.model is None

    _post_turn_hook(deps, [], model_request_count=10)

    assert deps.session.turns_since_memory_review == 0
    assert deps.session.model_requests_since_skill_review == 0


def test_below_threshold_counters_accumulate_no_reset(tmp_path: Path) -> None:
    """Three sub-threshold turns accumulate both counters without reset."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=10, skill_interval=10)

    _post_turn_hook(deps, [], model_request_count=2)
    _post_turn_hook(deps, [], model_request_count=3)
    _post_turn_hook(deps, [], model_request_count=4)

    # Memory counter bumps +1 per call = 3
    assert deps.session.turns_since_memory_review == 3
    # Skill counter bumps +N per call = 2+3+4 = 9
    assert deps.session.model_requests_since_skill_review == 9


def test_zero_iteration_turn_advances_memory_counter_only(tmp_path: Path) -> None:
    """A turn with model_request_count=0 advances turns_since_memory_review but not skill."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=5, skill_interval=5)

    _post_turn_hook(deps, [], model_request_count=0)
    _post_turn_hook(deps, [], model_request_count=0)

    assert deps.session.turns_since_memory_review == 2
    assert deps.session.model_requests_since_skill_review == 0


def test_memory_threshold_resets_memory_counter(tmp_path: Path) -> None:
    """Counter reaching memory threshold resets turns_since_memory_review to 0."""
    from co_cli.main import _post_turn_hook

    # memory_interval=1 means every turn triggers a memory KICK
    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=1, skill_interval=100)

    _post_turn_hook(deps, [], model_request_count=1)

    # Counter reset to 0 after KICK
    assert deps.session.turns_since_memory_review == 0


def test_skill_threshold_resets_skill_counter(tmp_path: Path) -> None:
    """Counter reaching skill threshold resets model_requests_since_skill_review to 0."""
    from co_cli.main import _post_turn_hook

    # skill_interval=3, memory_interval=100 so only skill KICK fires
    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=3)

    _post_turn_hook(deps, [], model_request_count=3)

    # Counter reset to 0 after KICK
    assert deps.session.model_requests_since_skill_review == 0


def test_counter_reset_allows_next_trigger(tmp_path: Path) -> None:
    """After a KICK fires, counter resets and subsequent turns can re-accumulate."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=1, skill_interval=100)

    # First call triggers KICK and resets counter
    _post_turn_hook(deps, [], model_request_count=1)
    assert deps.session.turns_since_memory_review == 0

    # Second call accumulates again (interval=1 so resets immediately again)
    _post_turn_hook(deps, [], model_request_count=1)
    assert deps.session.turns_since_memory_review == 0


def test_skill_threshold_overshoot_resets_counter(tmp_path: Path) -> None:
    """A single turn with iter_count > skill threshold fires KICK and resets counter."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=5)

    _post_turn_hook(deps, [], model_request_count=12)

    assert deps.session.model_requests_since_skill_review == 0


def test_none_deps_short_circuits(tmp_path: Path) -> None:
    """Defensive guard — hook handles deps=None without raising."""
    from co_cli.main import _post_turn_hook

    _post_turn_hook(None, [], model_request_count=5)  # type: ignore[arg-type]
