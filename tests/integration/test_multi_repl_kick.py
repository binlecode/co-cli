"""Integration test: multiple _send_review_kick calls produce separate KICK files.

Verifies that two calls with different domains each write a distinct KICK file
(no overwrite) because filenames embed a unique UUID. Also verifies that two
calls with the same domain also each produce a separate file.

CO_HOME override pattern: we reload both co_cli.config.core and co_cli.main so
that DREAM_QUEUE_DIR in main.py re-binds to the tmp path before _send_review_kick
is called.

No LLM calls. Filesystem only.
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
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)


def _setup_and_make_deps(tmp_path: Path):
    """Set CO_HOME, reload core+main, return (deps, main_mod)."""
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)

    from co_cli.deps import CoDeps
    from co_cli.tools.shell_backend import ShellBackend

    deps = CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP)
    session_file = tmp_path / "sessions" / "multi-kick-session.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    deps.session.session_path = session_file
    return deps, main_mod


def test_different_domains_produce_separate_kick_files(tmp_path: Path) -> None:
    """Two _send_review_kick calls with different domains produce two distinct files."""
    deps, main_mod = _setup_and_make_deps(tmp_path)
    _send_review_kick = main_mod._send_review_kick

    _send_review_kick(deps, domain="memory", persisted_message_count=3)
    _send_review_kick(deps, domain="skill", persisted_message_count=3)

    queue_dir = main_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert len(kick_files) == 2, f"Expected 2 KICK files, got {len(kick_files)}"

    domains = {json.loads(f.read_text())["domain"] for f in kick_files}
    assert domains == {"memory", "skill"}


def test_same_domain_twice_produces_two_files(tmp_path: Path) -> None:
    """Two _send_review_kick calls with the same domain produce two distinct files (no overwrite)."""
    deps, main_mod = _setup_and_make_deps(tmp_path)
    _send_review_kick = main_mod._send_review_kick

    _send_review_kick(deps, domain="memory", persisted_message_count=1)
    _send_review_kick(deps, domain="memory", persisted_message_count=2)

    queue_dir = main_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert len(kick_files) == 2, f"Expected 2 KICK files for same domain, got {len(kick_files)}"

    counts = {json.loads(f.read_text())["persisted_message_count"] for f in kick_files}
    assert counts == {1, 2}, "Both kicks must be preserved with their own payload"


def test_kick_files_have_unique_names(tmp_path: Path) -> None:
    """Each KICK file has a unique filename (UUID-based) even for rapid successive calls."""
    deps, main_mod = _setup_and_make_deps(tmp_path)
    _send_review_kick = main_mod._send_review_kick

    for i in range(5):
        _send_review_kick(deps, domain="memory", persisted_message_count=i)

    queue_dir = main_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert len(kick_files) == 5, f"Expected 5 KICK files, got {len(kick_files)}"

    # All filenames must be unique
    names = [f.name for f in kick_files]
    assert len(set(names)) == 5, "All KICK filenames must be distinct"


def test_kick_payload_contains_session_id(tmp_path: Path) -> None:
    """KICK file payload records the session_id from deps.session.session_path.stem."""
    deps, main_mod = _setup_and_make_deps(tmp_path)
    _send_review_kick = main_mod._send_review_kick

    _send_review_kick(deps, domain="memory", persisted_message_count=0)

    queue_dir = main_mod.DREAM_QUEUE_DIR
    kick_files = list(queue_dir.glob("*.json"))
    assert kick_files, "KICK file must exist"

    payload = json.loads(kick_files[0].read_text())
    assert payload["session_id"] == "multi-kick-session"
