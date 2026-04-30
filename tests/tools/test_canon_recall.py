"""Unit tests for search_canon — token-overlap scoring over souls memory files."""

from pathlib import Path

from co_cli.tools.memory._canon_recall import search_canon


def test_search_canon_title_weight() -> None:
    """Title tokens (filename stem) outrank body-only mentions."""
    hits = search_canon("humor tactical", role="tars", limit=5)
    assert hits, "Expected at least one hit for 'humor tactical' in tars memories"
    assert hits[0]["title"].startswith("tars-humor-is-tactical")


def test_search_canon_returns_channel_canon() -> None:
    """Every hit must carry channel='canon'."""
    hits = search_canon("humor", role="tars", limit=5)
    assert hits
    assert all(h["channel"] == "canon" for h in hits)


def test_search_canon_top_m_cap() -> None:
    """limit is respected — never returns more than limit hits."""
    hits = search_canon("tars", role="tars", limit=2)
    assert len(hits) <= 2


def test_search_canon_role_isolation() -> None:
    """Searching finch returns finch hits; tars results never carry the finch role."""
    finch_hits = search_canon("preparation", role="finch", limit=3)
    tars_hits = search_canon("preparation", role="tars", limit=3)
    assert finch_hits, "Finch has 'preparation' canon — must return hits"
    assert all(h["role"] == "finch" for h in finch_hits)
    assert all(h["role"] == "tars" for h in tars_hits)


def test_search_canon_path_traversal_rejected() -> None:
    """Role strings with traversal components are rejected before any file I/O."""
    assert search_canon("humor", role="../etc", limit=3) == []
    assert search_canon("humor", role="..", limit=3) == []
    assert search_canon("humor", role="tars/../finch", limit=3) == []


def test_search_canon_missing_memories_dir(tmp_path: Path) -> None:
    """A role whose souls dir exists but has no 'memories/' subdir returns []."""
    role_dir = tmp_path / "norole"
    role_dir.mkdir()
    # No memories/ subdir created.
    assert search_canon("anything", role="norole", limit=3, _souls_dir=tmp_path) == []


def test_search_canon_missing_role_dir(tmp_path: Path) -> None:
    """A completely unknown role returns [] without raising."""
    assert search_canon("anything", role="doesnotexist", limit=3, _souls_dir=tmp_path) == []


def test_search_canon_stopword_only_returns_empty() -> None:
    """Queries that reduce to zero non-stopword tokens return []."""
    assert search_canon("the and a is of", role="tars", limit=3) == []


def test_search_canon_empty_role_returns_empty() -> None:
    """Empty or None role returns [] without raising."""
    assert search_canon("humor", role="", limit=3) == []
    assert search_canon("humor", role=None, limit=3) == []  # type: ignore[arg-type]


def test_search_canon_stale_file_race(tmp_path: Path) -> None:
    """An unreadable entry (simulated via directory named *.md) is skipped; others continue."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "good-memory-humor-tactical.md").write_text(
        "humor tactical approach used here"
    )
    (memories_dir / "another-memory-humor.md").write_text("humor is tactical front-loaded")
    # A directory named *.md causes IsADirectoryError (OSError subclass) on read_text — simulates
    # a file deleted between glob and read.
    stale = memories_dir / "stale-deleted.md"
    stale.mkdir()

    results = search_canon("humor tactical", role="test-role", limit=5, _souls_dir=tmp_path)
    assert len(results) >= 1, "Real entries should still be returned when one is unreadable"
    assert all(r["title"] != "stale-deleted" for r in results)


def test_search_canon_body_contains_query_token(tmp_path: Path) -> None:
    """Returned body contains at least one query token when the file body has one."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "entry.md").write_text("This is about loyalty and dedication.")

    hits = search_canon("loyalty", role="test-role", limit=3, _souls_dir=tmp_path)
    assert hits, "Expected a hit for 'loyalty'"
    assert "loyalty" in hits[0]["body"]


def test_search_canon_excludes_frontmatter(tmp_path: Path) -> None:
    """YAML frontmatter must not contribute to scoring or appear in returned body.

    Memory files carry frontmatter like `tags: [character, source-material]` and
    `auto_category: character`. Tokenizing the raw file would inflate scores and
    leak YAML into the body the LLM sees.
    """
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "entry.md").write_text(
        "---\n"
        "auto_category: character\n"
        "tags:\n"
        "- character\n"
        "- planted\n"
        "- source-material\n"
        "provenance: planted\n"
        "---\n"
        "\n"
        "Real prose talks about courage and resolve."
    )

    # Tokens that appear ONLY in frontmatter must not match.
    assert search_canon("character", role="test-role", limit=5, _souls_dir=tmp_path) == []
    assert search_canon("planted", role="test-role", limit=5, _souls_dir=tmp_path) == []
    assert search_canon("provenance", role="test-role", limit=5, _souls_dir=tmp_path) == []

    # Tokens that appear in prose must still match — and body must exclude YAML.
    hits = search_canon("courage", role="test-role", limit=5, _souls_dir=tmp_path)
    assert hits, "Expected hit for 'courage' from prose"
    body = hits[0]["body"]
    assert "courage" in body
    assert "---" not in body
    assert "tags:" not in body
    assert "auto_category" not in body


def test_search_canon_score_ordering(tmp_path: Path) -> None:
    """Higher-scoring hits appear first — title 2x body weight."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    # Title-match entry scores higher than body-only entry.
    (memories_dir / "humor-test.md").write_text("some content here")
    (memories_dir / "other.md").write_text("humor is a topic in this body text here")

    hits = search_canon("humor", role="test-role", limit=5, _souls_dir=tmp_path)
    assert hits[0]["title"] == "humor-test", "Title-match entry must rank first"
