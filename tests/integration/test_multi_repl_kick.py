"""Integration test: multiple write_review_kick calls produce separate KICK files.

Verifies that two calls with different domains each write a distinct KICK file
(no overwrite) because filenames embed a unique UUID. Also verifies that two
calls with the same domain also each produce a separate file.

CO_HOME override pattern: we reload co_cli.config.core and the kick producer
(co_cli.session.review_kick) so that DREAM_QUEUE_DIR re-binds to the tmp path
before write_review_kick is called.

No LLM calls. Filesystem only.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_co_home() -> Generator[None, None, None]:
    original = os.environ.get("CO_HOME")
    yield
    if original is None:
        os.environ.pop("CO_HOME", None)
    else:
        os.environ["CO_HOME"] = original
    import co_cli.config.core as core_mod
    import co_cli.session.review_kick as kick_mod

    importlib.reload(core_mod)
    importlib.reload(kick_mod)


def _setup(tmp_path: Path):
    """Set CO_HOME, reload core + kick producer, return the kick module."""
    os.environ["CO_HOME"] = str(tmp_path)
    import co_cli.config.core as core_mod
    import co_cli.session.review_kick as kick_mod

    importlib.reload(core_mod)
    importlib.reload(kick_mod)
    return kick_mod


def test_different_domains_produce_separate_kick_files(tmp_path: Path) -> None:
    """Two write_review_kick calls with different domains produce two distinct files."""
    kick_mod = _setup(tmp_path)

    kick_mod.write_review_kick(domain="memory", session_id="s", persisted_message_count=3)
    kick_mod.write_review_kick(domain="skill", session_id="s", persisted_message_count=3)

    queue_dir = kick_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert len(kick_files) == 2, f"Expected 2 KICK files, got {len(kick_files)}"

    domains = {json.loads(f.read_text())["domain"] for f in kick_files}
    assert domains == {"memory", "skill"}


def test_kick_files_have_unique_names(tmp_path: Path) -> None:
    """Each KICK file has a unique filename (UUID-based) even for rapid successive calls."""
    kick_mod = _setup(tmp_path)

    for i in range(5):
        kick_mod.write_review_kick(domain="memory", session_id="s", persisted_message_count=i)

    queue_dir = kick_mod.DREAM_QUEUE_DIR
    kick_files = sorted(queue_dir.glob("*.json"))
    assert len(kick_files) == 5, f"Expected 5 KICK files, got {len(kick_files)}"

    names = [f.name for f in kick_files]
    assert len(set(names)) == 5, "All KICK filenames must be distinct"
