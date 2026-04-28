"""Shared helpers for knowledge tool modules."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai import RunContext

from co_cli.deps import CoDeps
from co_cli.knowledge.frontmatter import parse_frontmatter, render_frontmatter
from co_cli.knowledge.mutator import _atomic_write, _reindex_knowledge_file

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug, max 50 chars."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _find_by_slug(knowledge_dir: Path, slug: str) -> Path | None:
    """Return the knowledge file whose stem matches slug, or None."""
    return next((p for p in knowledge_dir.glob("*.md") if p.stem == slug), None)


async def _touch_recalled(
    paths: list[str],
    ctx: RunContext[CoDeps],
) -> None:
    """Fire-and-forget: increment recall_count and set last_recalled on hit artifacts.

    Skips silently if the file no longer exists (race with /knowledge forget).
    Does not block the recall return path — always launched via asyncio.create_task.
    """
    now = datetime.now(UTC).isoformat()
    for path_str in paths:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(raw)
            fm["last_recalled"] = now
            fm["recall_count"] = int(fm.get("recall_count") or 0) + 1
            md_content = render_frontmatter(fm, body)
            _atomic_write(path, md_content)
            if ctx.deps.knowledge_store is not None:
                _reindex_knowledge_file(ctx, path, body, md_content, fm, path.stem)
        except Exception:
            logger.warning("_touch_recalled: failed to update %s", path_str, exc_info=True)


def _find_article_by_url(knowledge_dir: Path, origin_url: str) -> Path | None:
    """Return the existing article whose ``source_ref`` matches origin_url, else None."""
    if not knowledge_dir.exists():
        return None
    for path in knowledge_dir.glob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8")
            if origin_url not in raw:
                continue
            fm, _ = parse_frontmatter(raw)
            if fm.get("source_ref") == origin_url:
                return path
        except Exception:
            continue
    return None
