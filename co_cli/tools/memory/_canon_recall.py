"""Canon recall — token-overlap search over active role's character memory files."""

from __future__ import annotations

import re
from pathlib import Path

import co_cli.personality
from co_cli.memory._stopwords import STOPWORDS
from co_cli.memory.frontmatter import strip_frontmatter

_SOULS_DIR: Path = (Path(co_cli.personality.__file__).parent / "prompts" / "souls").resolve()

_TOKEN_RE = re.compile(r"[a-z0-9]+")


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
    # Defense-in-depth: reject role strings with path traversal components before resolution.
    if ".." in role or "/" in role or "\\" in role:
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
        if score == 0:
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
