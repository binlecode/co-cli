"""Tests for KnowledgeSettings session chunk fields.

Real construction, real env vars, real KnowledgeStore. No mocks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from co_cli.config.knowledge import KnowledgeSettings

FIXTURE = Path(__file__).parent.parent / "memory" / "fixtures" / "session_with_tool_turns.jsonl"


def test_session_chunk_defaults() -> None:
    """KnowledgeSettings() exposes session_chunk_tokens=400 and session_chunk_overlap=80."""
    s = KnowledgeSettings()
    assert s.session_chunk_tokens == 400
    assert s.session_chunk_overlap == 80


def test_session_chunk_tokens_direct_construction() -> None:
    """session_chunk_tokens round-trips via direct field construction."""
    s = KnowledgeSettings(session_chunk_tokens=256)
    assert s.session_chunk_tokens == 256


def test_session_chunk_overlap_direct_construction() -> None:
    """session_chunk_overlap round-trips via direct field construction."""
    s = KnowledgeSettings(session_chunk_overlap=40)
    assert s.session_chunk_overlap == 40


def test_extra_forbid_rejects_typo() -> None:
    """extra='forbid' rejects a typo like session_chuck_tokens."""
    with pytest.raises(ValidationError):
        KnowledgeSettings(session_chuck_tokens=256)  # type: ignore[call-arg]


def test_session_chunk_tokens_lower_bound() -> None:
    """session_chunk_tokens ge=64 rejects values below 64."""
    with pytest.raises(ValidationError):
        KnowledgeSettings(session_chunk_tokens=32)


def test_session_chunk_overlap_lower_bound() -> None:
    """session_chunk_overlap ge=0 rejects negative values."""
    with pytest.raises(ValidationError):
        KnowledgeSettings(session_chunk_overlap=-1)


def test_session_chunk_tokens_env_var() -> None:
    """CO_KNOWLEDGE_SESSION_CHUNK_TOKENS env var is read by Settings.knowledge."""
    from co_cli.config.core import Settings

    old = os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_TOKENS", None)
    try:
        os.environ["CO_KNOWLEDGE_SESSION_CHUNK_TOKENS"] = "256"
        s = Settings()
        assert s.knowledge.session_chunk_tokens == 256
    finally:
        if old is None:
            os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_TOKENS", None)
        else:
            os.environ["CO_KNOWLEDGE_SESSION_CHUNK_TOKENS"] = old


def test_session_chunk_overlap_env_var() -> None:
    """CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP env var is read by Settings.knowledge."""
    from co_cli.config.core import Settings

    old = os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP", None)
    try:
        os.environ["CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP"] = "50"
        s = Settings()
        assert s.knowledge.session_chunk_overlap == 50
    finally:
        if old is None:
            os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP", None)
        else:
            os.environ["CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP"] = old


def test_propagation_to_store_and_index_session(tmp_path: Path) -> None:
    """KnowledgeStore receives session_chunk_tokens from config and passes it to chunk_session."""
    import shutil
    from datetime import UTC, datetime

    from co_cli.config.core import Settings
    from co_cli.memory.knowledge_store import KnowledgeStore
    from co_cli.memory.session import session_filename

    # Copy fixture to a properly-named session file so parse_session_filename succeeds.
    ts = datetime(2026, 4, 14, 12, 0, 0, tzinfo=UTC)
    name = session_filename(ts, "aabb1234-0000-0000-0000-000000000000")
    session_path = tmp_path / name
    shutil.copy(FIXTURE, session_path)

    old = os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_TOKENS", None)
    try:
        os.environ["CO_KNOWLEDGE_SESSION_CHUNK_TOKENS"] = "256"
        config = Settings()
        store = KnowledgeStore(config=config, knowledge_db_path=tmp_path / "search.db")

        assert store._session_chunk_tokens == 256

        store.index_session(session_path)

        rows = store._conn.execute("SELECT content FROM chunks WHERE source='session'").fetchall()
        assert rows, "index_session produced no chunks"

        longest = max(len(row[0]) for row in rows)
        # chunk_tokens=256 → max chars ~ 256*4; allow 20 % headroom
        assert longest // 4 <= 256 * 1.2, (
            f"longest chunk ({longest} chars) exceeds expected token budget"
        )
    finally:
        if old is None:
            os.environ.pop("CO_KNOWLEDGE_SESSION_CHUNK_TOKENS", None)
        else:
            os.environ["CO_KNOWLEDGE_SESSION_CHUNK_TOKENS"] = old
