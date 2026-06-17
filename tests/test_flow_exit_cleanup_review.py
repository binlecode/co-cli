"""Behavioral tests for REPL-exit cleanup.

Verifies:
- _drain_and_cleanup completes without error (smoke test).
- _fire_session_end_kicks writes memory and skill KICK files unconditionally
  (no counter threshold check at session end).
- _drain_and_cleanup triggers session-end KICKs when review is enabled.

All tests use real CoDeps, real asyncio, no monkeypatching.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import AsyncExitStack
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
    # _make_deps reloads config.core, the kick producer, and main against the temp
    # CO_HOME; reload them back so module-level USER_DIR / DREAM_QUEUE_DIR bindings
    # don't leak the temp dir to later tests.
    import importlib

    import co_cli.config.core as core_mod
    import co_cli.main as main_mod
    import co_cli.session.review_kick as kick_mod

    importlib.reload(core_mod)
    importlib.reload(kick_mod)
    importlib.reload(main_mod)


def _make_deps(tmp_path: Path, *, review_enabled: bool = True, with_model: bool = True):
    os.environ["CO_HOME"] = str(tmp_path)
    import importlib

    import co_cli.config.core as core_mod
    import co_cli.main as main_mod
    import co_cli.session.review_kick as kick_mod

    importlib.reload(core_mod)
    # Reload the kick producer (and main) so the module-level DREAM_QUEUE_DIR
    # binding the producer writes to is re-resolved against the updated USER_DIR
    # (CO_HOME override). The producer now lives in session.review_kick.
    importlib.reload(kick_mod)
    importlib.reload(main_mod)

    from co_cli.deps import CoDeps
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
    import importlib

    import co_cli.config.core as core_mod

    importlib.reload(core_mod)
    queue_dir = core_mod.DREAM_QUEUE_DIR
    if not queue_dir.exists():
        return []
    return sorted(queue_dir.glob("*.json"))


# ---------------------------------------------------------------------------
# Drain smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_and_cleanup_handles_none_deps(tmp_path: Path) -> None:
    """_drain_and_cleanup handles deps=None without raising."""
    from co_cli.main import _drain_and_cleanup

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(None, stack)


# ---------------------------------------------------------------------------
# Session-end KICKs
# ---------------------------------------------------------------------------


def test_fire_session_end_kicks_writes_memory_and_skill_kicks(tmp_path: Path) -> None:
    """_fire_session_end_kicks writes both memory and skill KICK files unconditionally."""
    from co_cli.main import _fire_session_end_kicks

    deps = _make_deps(tmp_path)

    _fire_session_end_kicks(deps)

    kicks = _kick_files(tmp_path)
    assert len(kicks) == 2
    payloads = [json.loads(k.read_text()) for k in kicks]
    domains = {p["domain"] for p in payloads}
    assert domains == {"memory", "skill"}
    # Producer-path KICKs carry no transcript_override — the daemon must take the
    # live-file read path, not the snapshot path. Absence (not None) is the contract.
    for payload in payloads:
        assert "transcript_override" not in payload


def test_fire_session_end_kicks_disabled_writes_no_kicks(tmp_path: Path) -> None:
    """review_enabled=False — _fire_session_end_kicks writes no KICK files."""
    from co_cli.main import _fire_session_end_kicks

    deps = _make_deps(tmp_path, review_enabled=False)

    _fire_session_end_kicks(deps)

    assert _kick_files(tmp_path) == []


def test_fire_session_end_kicks_no_model_writes_no_kicks(tmp_path: Path) -> None:
    """deps.model is None — _fire_session_end_kicks writes no KICK files."""
    from co_cli.main import _fire_session_end_kicks

    deps = _make_deps(tmp_path, with_model=False)

    _fire_session_end_kicks(deps)

    assert _kick_files(tmp_path) == []


@pytest.mark.asyncio
async def test_drain_triggers_session_end_kicks(tmp_path: Path) -> None:
    """_drain_and_cleanup calls _fire_session_end_kicks — both domain KICKs are written."""
    from co_cli.main import _drain_and_cleanup

    deps = _make_deps(tmp_path)

    async with AsyncExitStack() as stack:
        await _drain_and_cleanup(deps, stack)

    kicks = _kick_files(tmp_path)
    domains = {json.loads(k.read_text())["domain"] for k in kicks}
    assert domains == {"memory", "skill"}
