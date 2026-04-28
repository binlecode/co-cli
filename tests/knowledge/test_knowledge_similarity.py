"""Tests for token-level Jaccard similarity utilities."""

from pathlib import Path

from co_cli.knowledge.artifact import ArtifactKindEnum, KnowledgeArtifact
from co_cli.knowledge.similarity import find_similar_artifacts, token_jaccard


def _artifact(
    content: str, kind: str = ArtifactKindEnum.PREFERENCE, path: str = "x.md"
) -> KnowledgeArtifact:
    return KnowledgeArtifact(
        id="test-id",
        path=Path(path),
        artifact_kind=kind,
        title=None,
        content=content,
        created="2026-01-01T00:00:00Z",
    )


class TestTokenJaccard:
    def test_identical_strings_return_one(self) -> None:
        score = token_jaccard(
            "user prefers pytest over unittest", "user prefers pytest over unittest"
        )
        assert score == 1.0

    def test_completely_disjoint_returns_zero(self) -> None:
        score = token_jaccard("cats bark loudly", "dogs meow quietly")
        assert score == 0.0

    def test_partial_overlap_between_zero_and_one(self) -> None:
        score = token_jaccard("user prefers pytest", "user prefers ruff linting")
        assert 0.0 < score < 1.0

    def test_empty_string_returns_zero(self) -> None:
        assert token_jaccard("", "user prefers pytest") == 0.0
        assert token_jaccard("user prefers pytest", "") == 0.0
        assert token_jaccard("", "") == 0.0

    def test_stopwords_only_returns_zero(self) -> None:
        # Both strings reduce to empty token sets after stopword filtering
        score = token_jaccard("the and or but", "is are was were")
        assert score == 0.0

    def test_single_char_tokens_excluded(self) -> None:
        # Single-character tokens are filtered; 'a' is also a stopword
        score = token_jaccard("a b c pytest", "a b c pytest")
        # 'pytest' survives; result is 1.0 because identical meaningful tokens
        assert score == 1.0

    def test_case_insensitive(self) -> None:
        score = token_jaccard("User Prefers Pytest", "user prefers pytest")
        assert score == 1.0

    def test_unicode_text(self) -> None:
        # Non-ASCII content should not raise
        score = token_jaccard("用户 喜欢 pytest", "用户 喜欢 pytest")
        assert score == 1.0

    def test_superset_has_score_less_than_one(self) -> None:
        score = token_jaccard(
            "pytest testing framework", "pytest testing framework extra words added"
        )
        assert 0.0 < score < 1.0


class TestFindSimilarArtifacts:
    def test_returns_matches_above_threshold(self) -> None:
        artifacts = [
            _artifact(
                "user prefers pytest over unittest", kind=ArtifactKindEnum.PREFERENCE, path="a.md"
            ),
            _artifact(
                "user prefers ruff for linting", kind=ArtifactKindEnum.PREFERENCE, path="b.md"
            ),
            _artifact(
                "completely unrelated content here", kind=ArtifactKindEnum.PREFERENCE, path="c.md"
            ),
        ]
        matches = find_similar_artifacts(
            "user prefers pytest", ArtifactKindEnum.PREFERENCE, artifacts, threshold=0.3
        )
        paths = [str(a.path) for a, _ in matches]
        assert "a.md" in paths
        assert "c.md" not in paths

    def test_sorted_by_similarity_descending(self) -> None:
        artifacts = [
            _artifact(
                "user prefers pytest over unittest long text",
                kind=ArtifactKindEnum.PREFERENCE,
                path="a.md",
            ),
            _artifact("user prefers pytest", kind=ArtifactKindEnum.PREFERENCE, path="b.md"),
        ]
        matches = find_similar_artifacts(
            "user prefers pytest", ArtifactKindEnum.PREFERENCE, artifacts, threshold=0.0
        )
        scores = [score for _, score in matches]
        assert scores == sorted(scores, reverse=True)

    def test_filters_by_artifact_kind(self) -> None:
        artifacts = [
            _artifact("user prefers pytest", kind=ArtifactKindEnum.PREFERENCE, path="pref.md"),
            _artifact("user prefers pytest", kind=ArtifactKindEnum.FEEDBACK, path="feed.md"),
        ]
        matches = find_similar_artifacts(
            "user prefers pytest", ArtifactKindEnum.PREFERENCE, artifacts, threshold=0.5
        )
        kinds = {a.artifact_kind for a, _ in matches}
        assert kinds == {ArtifactKindEnum.PREFERENCE}

    def test_no_kind_filter_searches_all_kinds(self) -> None:
        artifacts = [
            _artifact("user prefers pytest", kind=ArtifactKindEnum.PREFERENCE, path="pref.md"),
            _artifact("user prefers pytest", kind=ArtifactKindEnum.FEEDBACK, path="feed.md"),
        ]
        matches = find_similar_artifacts("user prefers pytest", None, artifacts, threshold=0.5)
        assert len(matches) == 2

    def test_returns_empty_when_nothing_above_threshold(self) -> None:
        artifacts = [
            _artifact("completely unrelated", kind=ArtifactKindEnum.PREFERENCE, path="x.md")
        ]
        matches = find_similar_artifacts(
            "user prefers pytest", ArtifactKindEnum.PREFERENCE, artifacts, threshold=0.9
        )
        assert matches == []

    def test_returns_empty_on_empty_artifacts(self) -> None:
        matches = find_similar_artifacts(
            "user prefers pytest", ArtifactKindEnum.PREFERENCE, [], threshold=0.5
        )
        assert matches == []

    def test_identical_content_hits_threshold_one(self) -> None:
        text = "user prefers pytest for testing"
        artifacts = [_artifact(text, kind=ArtifactKindEnum.PREFERENCE, path="x.md")]
        matches = find_similar_artifacts(
            text, ArtifactKindEnum.PREFERENCE, artifacts, threshold=1.0
        )
        assert len(matches) == 1
        assert matches[0][1] == 1.0
