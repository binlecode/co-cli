"""SkillIndex — upsert/search/remove/list_names cycle and atomic transaction behavior."""

from pathlib import Path

import pytest
from tests._settings import SETTINGS

from co_cli.memory.memory_store import MemoryStore
from co_cli.memory.text_chunker import Chunk
from co_cli.skills.index import SkillHit, SkillIndex

try:
    import pysqlite3 as _sqlite3
except ImportError:
    import sqlite3 as _sqlite3

_FTS5_CONFIG = SETTINGS.knowledge.model_copy(
    update={
        "search_backend": "fts5",
        "embedding_provider": "none",
        "cross_encoder_reranker_url": None,
    }
)
_STORE_CONFIG = SETTINGS.model_copy(update={"knowledge": _FTS5_CONFIG})


def _make_index(tmp_path: Path) -> SkillIndex:
    return SkillIndex(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def test_upsert_search_remove_cycle(tmp_path: Path) -> None:
    """upsert → search → remove cycle: skill appears, then disappears from results."""
    idx = _make_index(tmp_path)
    skill_path = str(tmp_path / "my-skill.md")
    try:
        idx.upsert("my-skill", "A skill for testing retrieval", skill_path)

        hits = idx.search("testing retrieval", limit=5)
        assert len(hits) == 1, f"expected 1 hit, got {len(hits)}"
        assert isinstance(hits[0], SkillHit)
        assert hits[0].name == "my-skill"
        assert hits[0].path == skill_path
        assert hits[0].description == "A skill for testing retrieval"

        names = idx.list_names()
        assert "my-skill" in names, f"list_names must include 'my-skill', got {names}"

        idx.remove("my-skill")

        after = idx.search("testing retrieval", limit=5)
        assert len(after) == 0, f"expected 0 hits after remove, got {len(after)}"

        names_after = idx.list_names()
        assert "my-skill" not in names_after, "list_names must be empty after remove"
    finally:
        idx.close()


def test_upsert_is_idempotent(tmp_path: Path) -> None:
    """Two upserts with the same name+path replace; one entry remains, updated description wins."""
    idx = _make_index(tmp_path)
    skill_path = str(tmp_path / "idempotent-skill.md")
    try:
        idx.upsert("idempotent-skill", "First description", skill_path)
        idx.upsert("idempotent-skill", "Updated description xyzzy_marker", skill_path)

        hits = idx.search("xyzzy_marker", limit=5)
        assert len(hits) == 1, f"upsert must replace not duplicate — got {len(hits)} hits"
        assert hits[0].name == "idempotent-skill"

        names = idx.list_names()
        assert names == {"idempotent-skill"}, f"expected exactly one entry, got {names}"
    finally:
        idx.close()


def test_remove_no_op_when_absent(tmp_path: Path) -> None:
    """remove() is idempotent — no exception when the skill is not indexed."""
    idx = _make_index(tmp_path)
    try:
        idx.remove("nonexistent-skill")
    finally:
        idx.close()


def test_search_empty_query_returns_empty(tmp_path: Path) -> None:
    """SkillIndex.search('') returns an empty list — no FTS5 ParseException."""
    idx = _make_index(tmp_path)
    skill_path = str(tmp_path / "any-skill.md")
    try:
        idx.upsert("any-skill", "Some description", skill_path)
        assert idx.search("", limit=5) == []
        assert idx.search("   ", limit=5) == []
    finally:
        idx.close()


def test_search_caps_at_limit(tmp_path: Path) -> None:
    """SkillIndex.search honours the limit parameter."""
    idx = _make_index(tmp_path)
    try:
        for i in range(5):
            idx.upsert(
                f"skill-{i}",
                f"unique_marker_xyz description for skill {i}",
                str(tmp_path / f"skill-{i}.md"),
            )
        hits = idx.search("unique_marker_xyz", limit=2)
        assert len(hits) == 2, f"limit=2 must cap results at 2, got {len(hits)}"
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# upsert atomicity — two-step write rolls back on mid-step failure
# ---------------------------------------------------------------------------


def _docs_row(idx: SkillIndex, path: str) -> object | None:
    return idx._store._conn.execute(
        "SELECT path FROM docs WHERE source='skill' AND path=?", (path,)
    ).fetchone()


def _chunk_rows(idx: SkillIndex, path: str) -> list:
    return idx._store._conn.execute(
        "SELECT chunk_index FROM chunks WHERE source='skill' AND doc_path=?", (path,)
    ).fetchall()


def _fts_rows(idx: SkillIndex, marker: str) -> list:
    return idx._store._conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", (marker,)
    ).fetchall()


def _drive_failing_two_step_write(
    idx: SkillIndex, path: str, title: str, description: str
) -> None:
    """Replay SkillIndex.upsert with a malformed Chunk to force a real failure.

    The Chunk has ``content`` of type ``dict``; sqlite3 raises
    ``ProgrammingError`` (or ``InterfaceError`` under pysqlite3) when binding
    that parameter — a real production exception, no monkey-patching needed.
    """
    with idx._store.transaction() as tx:
        tx.index(source="skill", path=path, title=title, description=description)
        tx.index_chunks(
            "skill",
            path,
            [
                Chunk(
                    index=0,
                    content={"not": "a string"},  # type: ignore[arg-type]
                    start_line=0,
                    end_line=0,
                )
            ],
        )


def test_upsert_commits_both_row_and_chunks_on_success(tmp_path: Path) -> None:
    """Happy path: upsert commits docs row + chunks together; both present after."""
    idx = _make_index(tmp_path)
    skill_path = str(tmp_path / "atomic-skill.md")
    try:
        idx.upsert("atomic-skill", "marker_committed behavior under test", skill_path)

        row = _docs_row(idx, skill_path)
        assert row is not None, "docs row must be present after successful upsert"

        chunks = _chunk_rows(idx, skill_path)
        assert len(chunks) == 1, f"expected 1 chunk row, got {len(chunks)}"

        fts = _fts_rows(idx, "marker_committed")
        assert len(fts) == 1, "FTS5 must contain the chunk after successful upsert"

        hits = idx.search("marker_committed", limit=5)
        assert len(hits) == 1, "successful upsert must be discoverable via search"
        assert hits[0].name == "atomic-skill"
    finally:
        idx.close()


def test_upsert_rolls_back_when_index_chunks_fails(tmp_path: Path) -> None:
    """A mid-step failure in index_chunks rolls back the index() insert.

    The transaction wraps both writes; sqlite3's connection context manager
    issues a ROLLBACK on the propagating exception, so the docs row written
    by ``index`` never reaches the committed state.
    """
    idx = _make_index(tmp_path)
    skill_path = str(tmp_path / "broken-skill.md")
    try:
        assert _docs_row(idx, skill_path) is None
        assert _chunk_rows(idx, skill_path) == []

        with pytest.raises(_sqlite3.Error):
            _drive_failing_two_step_write(
                idx,
                skill_path,
                "broken-skill",
                "rollback_marker bad chunk content type",
            )

        assert _docs_row(idx, skill_path) is None, (
            "docs row must be rolled back after index_chunks raised"
        )
        assert _chunk_rows(idx, skill_path) == [], (
            "chunks must be empty — rollback covers both writes"
        )
        assert _fts_rows(idx, "rollback_marker") == [], (
            "FTS5 must contain no rows for the failed upsert"
        )

        names = idx.list_names()
        assert "broken-skill" not in names, "list_names must not include rolled-back skill"

        hits = idx.search("rollback_marker", limit=5)
        assert hits == [], "search must return no hits for a rolled-back upsert"
    finally:
        idx.close()


def test_upsert_rollback_leaves_db_usable_for_next_upsert(tmp_path: Path) -> None:
    """After a rolled-back upsert, the index still accepts new upserts cleanly."""
    idx = _make_index(tmp_path)
    bad_path = str(tmp_path / "bad-skill.md")
    good_path = str(tmp_path / "good-skill.md")
    try:
        with pytest.raises(_sqlite3.Error):
            _drive_failing_two_step_write(idx, bad_path, "bad-skill", "will roll back")

        idx.upsert("good-skill", "fresh_after_rollback works", good_path)

        hits = idx.search("fresh_after_rollback", limit=5)
        assert len(hits) == 1
        assert hits[0].name == "good-skill"
        assert "bad-skill" not in idx.list_names()
    finally:
        idx.close()


# ---------------------------------------------------------------------------
# MemoryTransaction — nesting, lifecycle, and rollback semantics
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(config=_STORE_CONFIG, memory_db_path=tmp_path / "search.db")


def test_nested_transaction_raises(tmp_path: Path) -> None:
    """A second ``with store.transaction():`` inside an open one raises RuntimeError."""
    store = _make_store(tmp_path)
    try:
        with store.transaction() as outer:
            assert outer is not None
            # SIM117: keep the inner `with` distinct — the pytest.raises
            # wrapper must observe the failure from the inner transaction
            # enter, which would not happen if merged with the outer block.
            with pytest.raises(RuntimeError, match="Nested transactions not supported"):  # noqa: SIM117
                with store.transaction():
                    pass
    finally:
        store.close()


def test_transaction_method_outside_with_raises(tmp_path: Path) -> None:
    """Calling tx.index() before entering the ``with`` block raises RuntimeError."""
    store = _make_store(tmp_path)
    try:
        tx = store.transaction()
        with pytest.raises(RuntimeError, match="outside `with` block"):
            tx.index(source="skill", path="/x", title="x", description="d")
    finally:
        store.close()


class _RollbackTriggerError(Exception):
    """Sentinel raised inside a transaction block to verify rollback behavior."""


def test_transaction_remove_rolls_back_on_exception(tmp_path: Path) -> None:
    """tx.remove() inside a transaction that raises must leave doc + chunks intact."""
    store = _make_store(tmp_path)
    skill_path = str(tmp_path / "persistent-skill.md")
    try:
        store.index(source="skill", path=skill_path, title="persistent-skill", description="d")
        store.index_chunks(
            "skill",
            skill_path,
            [Chunk(index=0, content="rollback_marker body", start_line=0, end_line=0)],
        )

        doc_before = store._conn.execute(
            "SELECT path FROM docs WHERE source='skill' AND path=?", (skill_path,)
        ).fetchone()
        assert doc_before is not None
        chunks_before = store._conn.execute(
            "SELECT chunk_index FROM chunks WHERE source='skill' AND doc_path=?",
            (skill_path,),
        ).fetchall()
        assert len(chunks_before) == 1

        # PT012 + SIM117: the multi-statement body is the contract under
        # test — tx.remove() must be issued *then* an exception raised
        # inside the transaction block to trigger rollback. Merging the two
        # `with` statements would collapse the assertion frame.
        with pytest.raises(_RollbackTriggerError):  # noqa: PT012, SIM117
            with store.transaction() as tx:
                tx.remove("skill", skill_path)
                raise _RollbackTriggerError

        doc_after = store._conn.execute(
            "SELECT path FROM docs WHERE source='skill' AND path=?", (skill_path,)
        ).fetchone()
        assert doc_after is not None, "docs row must survive rolled-back tx.remove()"
        chunks_after = store._conn.execute(
            "SELECT chunk_index FROM chunks WHERE source='skill' AND doc_path=?",
            (skill_path,),
        ).fetchall()
        assert len(chunks_after) == 1, "chunk rows must survive rolled-back tx.remove()"
    finally:
        store.close()
