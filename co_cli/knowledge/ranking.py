"""Post-retrieval ranking helpers: confidence scoring and contradiction detection.

These are knowledge business-logic functions consumed by the tool layer after
search() returns a result set. Kept here so they are reachable from any consumer,
not buried in a single tool module.
"""

import math
from datetime import UTC, datetime
from pathlib import Path

from co_cli.knowledge.frontmatter import parse_frontmatter

# Negation markers used for heuristic contradiction detection.
_NEGATION_MARKERS: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "don't",
        "do not",
        "stopped",
        "changed",
        "no longer",
        "don't use",
        "avoid",
    }
)


def _read_content_for_contradiction(path: str) -> str:
    """Read file content for contradiction detection. Returns empty string on failure."""
    if not path:
        return ""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        _, body = parse_frontmatter(raw)
        return body.strip().lower()
    except Exception:
        return ""


def _has_negation_conflict(content_a: str, content_b: str) -> bool:
    """Check if content_a and content_b form an opposing pair.

    Tokenizes both contents. For each token in content_a that also appears in
    content_b, checks if a negation marker appears within a 5-token window
    around that token in either content.
    """
    tokens_a = content_a.split()
    tokens_b = content_b.split()
    shared_words = set(tokens_a) & set(tokens_b) - _NEGATION_MARKERS

    if not shared_words:
        return False

    for tokens, other_tokens in [(tokens_a, tokens_b), (tokens_b, tokens_a)]:
        for idx, token in enumerate(tokens):
            if token in shared_words:
                window_start = max(0, idx - 5)
                window_end = min(len(tokens), idx + 6)
                window = set(tokens[window_start:window_end])
                if window & _NEGATION_MARKERS and token in other_tokens:
                    return True

    return False


def detect_contradictions(results: list) -> set[str]:
    """Detect contradicting result pairs within the same category.

    Groups results by category, then for each pair checks if words in one
    result's content appear in the other's content alongside a negation marker
    within a 5-token window.

    Returns a set of path strings for results involved in conflicts.

    Limitation: heuristic window-based detection produces false positives on
    complex sentences. LLM-based detection is a Phase 2 enhancement.
    """
    conflict_paths: set[str] = set()

    by_category: dict[str, list] = {}
    for r in results:
        if r.category:
            by_category.setdefault(r.category, []).append(r)

    for category_results in by_category.values():
        if len(category_results) < 2:
            continue

        for idx_a in range(len(category_results)):
            for idx_b in range(idx_a + 1, len(category_results)):
                r_a = category_results[idx_a]
                r_b = category_results[idx_b]

                content_a = _read_content_for_contradiction(r_a.path)
                content_b = _read_content_for_contradiction(r_b.path)
                if not content_a or not content_b:
                    continue

                if _has_negation_conflict(content_a, content_b):
                    conflict_paths.add(r_a.path)
                    conflict_paths.add(r_b.path)

    return conflict_paths


def compute_confidence(
    path: str | None,
    score: float,
    created: str | None,
    provenance: str | None,
    certainty: str | None,
    half_life_days: int,
) -> float:
    """Compute composite confidence score for a search result.

    Formula: 0.5 * score + 0.3 * decay + 0.2 * (prov_weight * certainty_mult)
    Decay uses exponential half-life on age in days from created.

    Limitation: heuristic formula; LLM-based confidence is a Phase 2 enhancement.
    """
    _ = path  # reserved for future path-based signals
    if created and half_life_days > 0:
        try:
            age_days = (
                datetime.now(UTC) - datetime.fromisoformat(created)
            ).total_seconds() / 86400
            decay = math.exp(-math.log(2) * max(0, age_days) / half_life_days)
            decay = max(0.0, min(1.0, decay))
        except Exception:
            decay = 1.0
    else:
        decay = 1.0

    provenance_weights = {
        "user-told": 1.0,
        "planted": 0.8,
        "detected": 0.7,
        "session": 0.6,
        "web-fetch": 0.5,
        "auto_decay": 0.3,
    }
    certainty_multipliers = {"high": 1.0, "medium": 0.8, "low": 0.6}
    prov_w = provenance_weights.get(provenance or "", 0.5)
    cert_m = certainty_multipliers.get(certainty or "", 0.8)
    return 0.5 * score + 0.3 * decay + 0.2 * (prov_w * cert_m)
