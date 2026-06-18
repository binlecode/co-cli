"""Integration test: _post_turn_hook reaches nudge threshold → KICK file in DREAM_QUEUE_DIR.

Verifies the end-to-end path from _post_turn_hook through write_review_kick to
atomic KICK file creation in DREAM_QUEUE_DIR. Uses real filesystem (tmp_path),
real CoDeps with a live LLM model reference — no LLM calls are made; the model
handle is only needed to pass the `deps.model is None` guard in _post_turn_hook.

CO_HOME override + importlib.reload pattern:
  write_review_kick (co_cli.dream_queue) uses a module-level DREAM_QUEUE_DIR
  binding. Reloading co_cli.config.core alone only updates that module's symbols.
  We also reload the kick producer (and main) so the binding the producer writes
  to re-resolves to the new tmp path.
"""

from __future__ import annotations

import importlib
import json
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
    import co_cli.dream_queue as kick_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(kick_mod)
    importlib.reload(main_mod)


def _setup_co_home(tmp_path: Path) -> None:
    """Set CO_HOME and reload config.core, the kick producer, and main so paths align."""
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.dream_queue as kick_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(kick_mod)
    importlib.reload(main_mod)


def _make_deps(tmp_path: Path, *, memory_interval: int, skill_interval: int):
    """Real CoDeps with CO_HOME → tmp_path and review_enabled=True."""
    _setup_co_home(tmp_path)

    from co_cli.deps import CoDeps
    from co_cli.llm.factory import build_model
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={
            "memory": SETTINGS_NO_MCP.memory.model_copy(update={"review_enabled": True}),
            "skills": SETTINGS_NO_MCP.skills.model_copy(
                update={
                    "review_enabled": True,
                    "review_memory_nudge_interval": memory_interval,
                    "review_skill_nudge_interval": skill_interval,
                }
            ),
        }
    )
    model = build_model(SETTINGS_NO_MCP.llm)
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    # Point session_path to a temp file so session_id is deterministic
    session_file = tmp_path / "sessions" / "test-session-abc.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file
    return deps


def test_memory_kick_file_created_when_threshold_reached(tmp_path: Path) -> None:
    """After memory nudge interval turns, a KICK file appears in DREAM_QUEUE_DIR."""
    deps = _make_deps(tmp_path, memory_interval=3, skill_interval=100)

    import co_cli.main as main_mod

    _post_turn_hook = main_mod._post_turn_hook

    # Trigger memory KICK: 3 turns at interval=3
    for _ in range(3):
        _post_turn_hook(deps, [], model_request_count=1)

    import co_cli.config.core as core_mod

    queue_dir = core_mod.DREAM_QUEUE_DIR
    kick_files = list(queue_dir.glob("*.json"))
    assert len(kick_files) >= 1, "Expected at least one KICK file after threshold"

    # Verify KICK file payload structure
    kick_file = kick_files[0]
    payload = json.loads(kick_file.read_text())
    assert payload["domain"] == "memory"
    assert payload["session_id"] == "test-session-abc"
    assert "persisted_message_count" in payload
    assert "created_at" in payload


def test_skill_kick_file_created_when_threshold_reached(tmp_path: Path) -> None:
    """After skill nudge interval iters, a KICK file appears in DREAM_QUEUE_DIR."""
    deps = _make_deps(tmp_path, memory_interval=100, skill_interval=5)

    import co_cli.main as main_mod

    _post_turn_hook = main_mod._post_turn_hook

    # Trigger skill KICK: one turn with iter_count=5
    _post_turn_hook(deps, [], model_request_count=5)

    import co_cli.config.core as core_mod

    queue_dir = core_mod.DREAM_QUEUE_DIR
    kick_files = list(queue_dir.glob("*.json"))
    assert len(kick_files) >= 1, "Expected at least one KICK file for skill domain"

    payload = json.loads(kick_files[0].read_text())
    assert payload["domain"] == "skill"
    assert payload["session_id"] == "test-session-abc"


def test_kick_payload_carries_runtime_persisted_message_count(tmp_path: Path) -> None:
    """KICK payload threads the actual deps.runtime.persisted_message_count value.

    The threshold tests assert presence of the field; this pins that the real
    runtime value (7) is what lands in the payload, not a default or stale count.
    """
    deps = _make_deps(tmp_path, memory_interval=1, skill_interval=100)
    deps.runtime.persisted_message_count = 7

    import co_cli.main as main_mod

    _post_turn_hook = main_mod._post_turn_hook
    _post_turn_hook(deps, [], model_request_count=1)

    import co_cli.config.core as core_mod

    queue_dir = core_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert kick_files, "KICK file must exist"

    payload = json.loads(kick_files[0].read_text())
    assert payload["persisted_message_count"] == 7


def test_no_kick_file_when_review_disabled(tmp_path: Path) -> None:
    """When review_enabled=False, no KICK file is written."""
    _setup_co_home(tmp_path)

    import co_cli.main as main_mod
    from co_cli.deps import CoDeps
    from co_cli.llm.factory import build_model
    from co_cli.tools.shell_backend import ShellBackend

    config = SETTINGS_NO_MCP.model_copy(
        update={
            "skills": SETTINGS_NO_MCP.skills.model_copy(
                update={
                    "review_enabled": False,
                    "review_memory_nudge_interval": 1,
                    "review_skill_nudge_interval": 1,
                }
            )
        }
    )
    model = build_model(SETTINGS_NO_MCP.llm)
    deps = CoDeps(shell=ShellBackend(), config=config, model=model)
    session_file = tmp_path / "sessions" / "test-session.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file

    _post_turn_hook = main_mod._post_turn_hook
    for _ in range(5):
        _post_turn_hook(deps, [], model_request_count=5)

    import co_cli.config.core as core_mod

    queue_dir = core_mod.DREAM_QUEUE_DIR
    kick_files = list(queue_dir.glob("*.json")) if queue_dir.exists() else []
    assert kick_files == [], "No KICK files expected when review_enabled=False"
