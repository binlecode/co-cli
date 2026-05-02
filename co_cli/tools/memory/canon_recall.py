"""Canon recall — token-overlap search over active role's character memory files."""

from __future__ import annotations

import re
from pathlib import Path

import co_cli.personality
from co_cli.memory.frontmatter import strip_frontmatter
from co_cli.memory.stopwords import STOPWORDS

_SOULS_DIR: Path = (Path(co_cli.personality.__file__).parent / "prompts" / "souls").resolve()

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Minimum score for canon admission. Score = 2*|q & title| + |q & body|, so the floor
# admits "≥1 title-token match" OR "≥2 body-token matches" while rejecting score-1 hits
# (single incidental body-token overlap in unrelated prose). Without this floor any
# query-token coincidence pads the result list with noise — the algorithm has no
# MATCH-level filter like FTS5, so the floor is what compensates.
_MIN_SCORE = 2


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1}


def search_canon(
    query: str,
    *,
    role: str | None,
    limit: int,
    _souls_dir: Path | None = None,
) -> list[dict]:
    """Glob the active role's memories and return up to `limit` hits ranked by token overlap.

    Title (filename stem) matches count 2x — filenames are curated descriptors. Each hit
    carries the full post-frontmatter body; the corpus is small (per-role <1KB on average)
    so the model receives whole scenes rather than a snippet that would force a follow-up
    read. Returns [] if: role is empty/None, query has no non-stopword tokens, the role has
    no memories directory, or the resolved path escapes the souls dir (defense-in-depth).
    """
    if not role:
        return []

    q = _tokenize(query)
    if not q:
        return []

    base = (_souls_dir or _SOULS_DIR).resolve()
    role_dir = (base / role / "memories").resolve()
    try:
        role_dir.relative_to(base)
    except ValueError:
        return []
    if not role_dir.is_dir():
        return []

    hits: list[dict] = []
    for path in sorted(role_dir.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = strip_frontmatter(raw).strip()
        title_tokens = _tokenize(path.stem)
        body_tokens = _tokenize(body)
        score = 2 * len(q & title_tokens) + len(q & body_tokens)
        if score < _MIN_SCORE:
            continue
        hits.append(
            {
                "channel": "canon",
                "role": role,
                "title": path.stem,
                "body": body,
                "score": score,
            }
        )

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]
