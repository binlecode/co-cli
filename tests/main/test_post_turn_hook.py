"""Unit tests for _post_turn_hook: counter increments and KICK file writes.

Verifies:
- turns_since_memory_review bumped +1 per call
- iters_since_skill_review bumped +turn_iteration_count per call
- Threshold trip writes a KICK JSON file to DREAM_QUEUE_DIR
- Counters reset to 0 after a KICK fires
"""

from __future__ import annotations

import importlib
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
    import co_cli.config.core as core_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)


def _make_deps(
    tmp_path: Path,
    *,
    review_enabled: bool,
    memory_interval: int = 5,
    skill_interval: int = 5,
    with_model: bool = True,
):
    """Real CoDeps with CO_HOME pointed at tmp_path so DREAM_QUEUE_DIR resolves there."""
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)

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


def _kick_files(tmp_path: Path) -> list[Path]:
    """Return all KICK JSON files in the dream queue dir under tmp_path."""
    queue_dir = tmp_path / "daemons" / "dream" / "queue"
    if not queue_dir.exists():
        return []
    return [p for p in queue_dir.iterdir() if p.suffix == ".json"]


# ---------------------------------------------------------------------------
# Counter increment tests
# ---------------------------------------------------------------------------


def test_turns_since_memory_review_increments_by_one_per_call(tmp_path: Path) -> None:
    """turns_since_memory_review bumped +1 per _post_turn_hook call."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=100)

    _post_turn_hook(deps, [], turn_iteration_count=3)
    assert deps.session.turns_since_memory_review == 1

    _post_turn_hook(deps, [], turn_iteration_count=3)
    assert deps.session.turns_since_memory_review == 2


def test_iters_since_skill_review_increments_by_turn_iteration_count(tmp_path: Path) -> None:
    """iters_since_skill_review bumped by turn_iteration_count per call."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=100)

    _post_turn_hook(deps, [], turn_iteration_count=4)
    assert deps.session.iters_since_skill_review == 4

    _post_turn_hook(deps, [], turn_iteration_count=7)
    assert deps.session.iters_since_skill_review == 11


# ---------------------------------------------------------------------------
# KICK file write tests
# ---------------------------------------------------------------------------


def test_memory_threshold_trip_writes_kick_file(tmp_path: Path) -> None:
    """When memory threshold is reached, a KICK JSON file appears in DREAM_QUEUE_DIR."""
    from co_cli.main import _post_turn_hook

    # memory_interval=1 trips on first call
    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=1, skill_interval=100)

    _post_turn_hook(deps, [], turn_iteration_count=1)

    files = _kick_files(tmp_path)
    assert len(files) >= 1, "Expected at least one KICK file after threshold trip"
    memory_kicks = [f for f in files if "memory" in f.read_text()]
    assert len(memory_kicks) >= 1


def test_skill_threshold_trip_writes_kick_file(tmp_path: Path) -> None:
    """When skill threshold is reached, a KICK JSON file appears in DREAM_QUEUE_DIR."""
    from co_cli.main import _post_turn_hook

    # skill_interval=3, memory_interval=100 so only skill fires
    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=3)

    _post_turn_hook(deps, [], turn_iteration_count=3)

    files = _kick_files(tmp_path)
    assert len(files) >= 1, "Expected at least one KICK file after skill threshold trip"
    skill_kicks = [f for f in files if "skill" in f.read_text()]
    assert len(skill_kicks) >= 1


def test_memory_threshold_resets_counter_to_zero(tmp_path: Path) -> None:
    """turns_since_memory_review resets to 0 when threshold is reached and KICK fires."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=1, skill_interval=100)
    _post_turn_hook(deps, [], turn_iteration_count=1)

    assert deps.session.turns_since_memory_review == 0


def test_skill_threshold_resets_counter_to_zero(tmp_path: Path) -> None:
    """iters_since_skill_review resets to 0 when threshold is reached and KICK fires."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=True, memory_interval=100, skill_interval=3)
    _post_turn_hook(deps, [], turn_iteration_count=3)

    assert deps.session.iters_since_skill_review == 0


def test_review_disabled_no_kick_files_written(tmp_path: Path) -> None:
    """review_enabled=False — no KICK files written even when thresholds are exceeded."""
    from co_cli.main import _post_turn_hook

    deps = _make_deps(tmp_path, review_enabled=False, memory_interval=1, skill_interval=1)
    _post_turn_hook(deps, [], turn_iteration_count=1)

    assert _kick_files(tmp_path) == []
    assert deps.session.turns_since_memory_review == 0
    assert deps.session.iters_since_skill_review == 0
