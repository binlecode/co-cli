"""SkillIndex — upsert/search/remove/list_names cycle over the shared FTS5 DB."""

from pathlib import Path

from tests._settings import SETTINGS

from co_cli.skills.index import SkillHit, SkillIndex

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
