"""Token-level Jaccard similarity for knowledge artifact dedup."""

from __future__ import annotations

from co_cli.knowledge._stopwords import STOPWORDS
from co_cli.knowledge.artifact import KnowledgeArtifact


def token_jaccard(a: str, b: str) -> float:
    """Return Jaccard similarity between two strings after tokenisation.

    Tokenises by lowercasing and splitting on whitespace, then filters
    single-character tokens and STOPWORDS. Returns 0.0 when either token set
    is empty to avoid division-by-zero.
    """
    a_tokens = _tokenise(a)
    b_tokens = _tokenise(b)
    if not a_tokens or not b_tokens:
        return 0.0
    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(intersection) / len(union)


def find_similar_artifacts(
    content: str,
    artifact_kind: str | None,
    artifacts: list[KnowledgeArtifact],
    threshold: float,
) -> list[tuple[KnowledgeArtifact, float]]:
    """Return artifacts whose content similarity to *content* exceeds *threshold*.

    Only compares artifacts with the same *artifact_kind* when one is supplied.
    Results are sorted by similarity descending.
    """
    candidates = (
        [a for a in artifacts if a.artifact_kind == artifact_kind]
        if artifact_kind is not None
        else list(artifacts)
    )
    matches: list[tuple[KnowledgeArtifact, float]] = []
    for artifact in candidates:
        score = token_jaccard(content, artifact.content)
        if score >= threshold:
            matches.append((artifact, score))
    matches.sort(key=lambda pair: pair[1], reverse=True)
    return matches


def is_content_superset(new_content: str, existing_content: str) -> bool:
    """Return True when *new_content* tokens are a superset of *existing_content* tokens.

    A strict superset means the new content contains all the meaningful words
    from the existing entry and adds more — warranting a replace rather than an
    append.
    """
    existing_tokens = _tokenise(existing_content)
    if not existing_tokens:
        return False
    new_tokens = _tokenise(new_content)
    return existing_tokens.issubset(new_tokens) and new_tokens != existing_tokens


def _tokenise(text: str) -> frozenset[str]:
    return frozenset(t for t in text.lower().split() if len(t) > 1 and t not in STOPWORDS)
