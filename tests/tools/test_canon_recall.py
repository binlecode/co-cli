"""Unit tests for search_canon — token-overlap scoring over souls memory files.

Direct unit coverage of the canon-recall algorithm. Integration via the canon
channel of memory_search lives in test_memory_search_canon.py; the canon eval
in evals/eval_canon_recall.py covers end-to-end correctness.
"""

from pathlib import Path

from co_cli.tools.memory._canon_recall import search_canon


def test_search_canon_title_weight() -> None:
    """Title tokens (filename stem) outrank body-only mentions in real soul data."""
    hits = search_canon("humor tactical", role="tars", limit=5)
    assert hits, "Expected at least one hit for 'humor tactical' in tars memories"
    assert hits[0]["title"].startswith("tars-humor-is-tactical")


def test_search_canon_returns_channel_canon() -> None:
    """Every hit must carry channel='canon' — recall.py merges by channel field."""
    hits = search_canon("humor", role="tars", limit=5)
    assert hits
    assert all(h["channel"] == "canon" for h in hits)


def test_search_canon_returns_full_body() -> None:
    """Hits ship the full post-frontmatter body inline — not a snippet, no path indirection."""
    hits = search_canon("humor", role="tars", limit=5)
    assert hits
    assert "body" in hits[0]
    body = hits[0]["body"]
    assert body, "Body must be non-empty"
    assert "---" not in body[:5], "Frontmatter must be stripped from body"
    assert "humor" in body.lower(), "Body must contain a query token"


def test_search_canon_top_m_cap() -> None:
    """limit is respected — never returns more than limit hits."""
    hits = search_canon("tars", role="tars", limit=2)
    assert len(hits) <= 2


def test_search_canon_role_isolation() -> None:
    """Searching finch returns finch memories; tars results must not contain finch hits."""
    finch_hits = search_canon("preparation", role="finch", limit=3)
    tars_hits = search_canon("preparation", role="tars", limit=3)
    assert finch_hits, "Finch has 'preparation' canon — must return hits"
    assert all(h["role"] == "finch" for h in finch_hits)
    assert all(h["role"] != "finch" for h in tars_hits)


def test_search_canon_path_traversal_rejected() -> None:
    """Role strings with traversal components are rejected before any file I/O."""
    assert search_canon("humor", role="../etc", limit=3) == []
    assert search_canon("humor", role="..", limit=3) == []
    assert search_canon("humor", role="tars/../finch", limit=3) == []


def test_search_canon_missing_memories_dir(tmp_path: Path) -> None:
    """A role whose souls dir exists but has no 'memories/' subdir returns []."""
    role_dir = tmp_path / "norole"
    role_dir.mkdir()
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
    assert search_canon("humor", role=None, limit=3) == []


def test_search_canon_stale_file_race(tmp_path: Path) -> None:
    """An unreadable entry (simulated via directory named *.md) is skipped; others continue."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "good-memory-humor-tactical.md").write_text(
        "humor tactical approach used here"
    )
    (memories_dir / "another-memory-humor.md").write_text("humor is tactical front-loaded")
    stale = memories_dir / "stale-deleted.md"
    stale.mkdir()

    results = search_canon("humor tactical", role="test-role", limit=5, _souls_dir=tmp_path)
    assert len(results) >= 1, "Real entries should still be returned when one is unreadable"
    assert all(r["title"] != "stale-deleted" for r in results)


def test_search_canon_excludes_frontmatter(tmp_path: Path) -> None:
    """YAML frontmatter must not contribute to scoring or appear in returned bodies."""
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

    # Frontmatter-only tokens, even paired, must not match — confirms tokenizer skips frontmatter.
    assert search_canon("character planted", role="test-role", limit=5, _souls_dir=tmp_path) == []
    assert (
        search_canon("provenance auto_category", role="test-role", limit=5, _souls_dir=tmp_path)
        == []
    )

    # Two prose tokens clear the score floor (2 body matches = score 2).
    hits = search_canon("courage resolve", role="test-role", limit=5, _souls_dir=tmp_path)
    assert hits, "Expected hit for 'courage resolve' from prose"
    body = hits[0]["body"]
    assert "courage" in body
    assert "---" not in body[:5]
    assert "tags:" not in body
    assert "auto_category" not in body


def test_search_canon_score_ordering(tmp_path: Path) -> None:
    """Higher-scoring hits appear first — title 2x body weight."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "humor-tactical.md").write_text("some content here")
    (memories_dir / "other.md").write_text("humor is a topic in this body text here")

    hits = search_canon("humor tactical", role="test-role", limit=5, _souls_dir=tmp_path)
    assert hits[0]["title"] == "humor-tactical", "Title-match entry must rank first"


def test_search_canon_score_floor(tmp_path: Path) -> None:
    """Score 1 (single body-token coincidence) is rejected — admits only score >= 2."""
    memories_dir = tmp_path / "test-role" / "memories"
    memories_dir.mkdir(parents=True)
    (memories_dir / "unrelated.md").write_text("body mentions humor exactly once and nothing else")

    hits = search_canon("humor", role="test-role", limit=5, _souls_dir=tmp_path)
    assert hits == [], (
        "Single body-token coincidence (score=1) must be rejected by the score floor"
    )
