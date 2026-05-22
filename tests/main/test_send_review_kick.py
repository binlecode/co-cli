"""Unit tests for _send_review_kick: KICK JSON file structure and field correctness.

Verifies:
- A .json file is written to DREAM_QUEUE_DIR
- File contains required fields: domain, session_id, persisted_message_count, created_at
- Field values match what was passed in
- Missing / not-yet-running socket does not raise (best-effort nudge)
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


def _make_deps(tmp_path: Path, session_stem: str = "abc12345"):
    """Minimal CoDeps with CO_HOME pointing at tmp_path."""
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.main as main_mod

    importlib.reload(core_mod)
    importlib.reload(main_mod)

    from co_cli.deps import CoDeps, CoSessionState
    from co_cli.tools.shell_backend import ShellBackend

    session = CoSessionState()
    session.session_path = tmp_path / f"{session_stem}.jsonl"
    deps = CoDeps(shell=ShellBackend(), config=SETTINGS_NO_MCP, session=session)
    return deps


def _queue_files(tmp_path: Path) -> list[Path]:
    queue_dir = tmp_path / "daemons" / "dream" / "queue"
    if not queue_dir.exists():
        return []
    return [p for p in queue_dir.iterdir() if p.suffix == ".json"]


# ---------------------------------------------------------------------------
# File write tests
# ---------------------------------------------------------------------------


def test_kick_file_is_written_to_dream_queue_dir(tmp_path: Path) -> None:
    """_send_review_kick writes exactly one .json file to DREAM_QUEUE_DIR."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path)
    _send_review_kick(deps, domain="memory", persisted_message_count=5)

    files = _queue_files(tmp_path)
    assert len(files) == 1


def test_kick_file_contains_required_fields(tmp_path: Path) -> None:
    """KICK JSON contains domain, session_id, persisted_message_count, created_at."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path, session_stem="deadbeef")
    _send_review_kick(deps, domain="skill", persisted_message_count=12)

    files = _queue_files(tmp_path)
    assert len(files) == 1
    payload = json.loads(files[0].read_text())

    assert "domain" in payload
    assert "session_id" in payload
    assert "persisted_message_count" in payload
    assert "created_at" in payload


def test_kick_file_domain_matches_argument(tmp_path: Path) -> None:
    """KICK JSON domain field matches the domain argument passed."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path)
    _send_review_kick(deps, domain="memory", persisted_message_count=0)

    files = _queue_files(tmp_path)
    payload = json.loads(files[0].read_text())
    assert payload["domain"] == "memory"


def test_kick_file_persisted_message_count_matches_argument(tmp_path: Path) -> None:
    """KICK JSON persisted_message_count matches the argument passed."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path)
    _send_review_kick(deps, domain="skill", persisted_message_count=42)

    files = _queue_files(tmp_path)
    payload = json.loads(files[0].read_text())
    assert payload["persisted_message_count"] == 42


def test_kick_file_session_id_derives_from_session_path_stem(tmp_path: Path) -> None:
    """KICK JSON session_id equals deps.session.session_path.stem."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path, session_stem="myses99")
    _send_review_kick(deps, domain="memory", persisted_message_count=1)

    files = _queue_files(tmp_path)
    payload = json.loads(files[0].read_text())
    assert payload["session_id"] == "myses99"


def test_kick_does_not_raise_when_socket_missing(tmp_path: Path) -> None:
    """_send_review_kick swallows socket errors (no daemon running) and returns cleanly."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path)
    # No daemon listening — should not raise
    _send_review_kick(deps, domain="memory", persisted_message_count=0)


def test_two_kicks_write_two_distinct_files(tmp_path: Path) -> None:
    """Two calls to _send_review_kick produce two distinct KICK files."""
    from co_cli.main import _send_review_kick

    deps = _make_deps(tmp_path)
    _send_review_kick(deps, domain="memory", persisted_message_count=1)
    _send_review_kick(deps, domain="skill", persisted_message_count=2)

    files = _queue_files(tmp_path)
    assert len(files) == 2
    stems = {f.stem for f in files}
    assert len(stems) == 2, "Each KICK file must have a unique filename"
