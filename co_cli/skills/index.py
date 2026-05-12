"""SkillIndex — FTS5 search index for skill name+description.

Same underlying DB as MemoryStore but a separate API. The boundary is:
MemoryStore owns knowledge/session/canon/obsidian/drive; SkillIndex owns
the 'skill' source exclusively. Callers never see a MemoryStore through
SkillIndex — they use upsert / remove / list_names / search.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from co_cli.config.core import Settings


@dataclass
class SkillHit:
    """One ranked hit from SkillIndex.search()."""

    name: str
    description: str
    score: float
    path: str


class SkillIndex:
    """FTS5 index over skill name + description. Same DB as MemoryStore, separate API.

    Indexed content is ``"<name>: <description>"`` — body is not indexed.
    Idempotent: re-calling upsert() with the same name+path replaces the entry.
    """

    def __init__(self, *, config: Settings, memory_db_path: Path | None = None) -> None:
        from co_cli.memory.memory_store import MemoryStore

        self._store = MemoryStore(config=config, memory_db_path=memory_db_path)

    def upsert(self, name: str, description: str, path: str) -> None:
        """Index or replace a skill row under source='skill'."""
        from co_cli.memory.text_chunker import Chunk

        self._store.index(source="skill", path=path, title=name, description=description)
        self._store.index_chunks(
            "skill",
            path,
            [Chunk(index=0, content=f"{name}: {description}", start_line=0, end_line=0)],
        )

    def remove(self, name: str) -> None:
        """Remove a skill from the index by name. No-op if not indexed."""
        path = self._store.get_path_by_title("skill", name)
        if path is not None:
            self._store.remove("skill", path)

    def list_names(self) -> set[str]:
        """Return the set of currently-indexed skill names."""
        return self._store.list_titles_by_source("skill")

    def search(self, query: str, limit: int = 5) -> list[SkillHit]:
        """BM25 FTS5 search over the skill index, deduplicated by name.

        Returns [] when the query is empty or stopword-only.
        """
        if not query.strip():
            return []
        raw = self._store.search(query, sources=["skill"], limit=limit * 5)
        seen: dict[str, SkillHit] = {}
        for r in raw:
            name = r.title or ""
            if not name or name in seen:
                continue
            seen[name] = SkillHit(
                name=name,
                description=r.description or "",
                score=r.score,
                path=r.path or "",
            )
            if len(seen) >= limit:
                break
        return list(seen.values())

    def close(self) -> None:
        """Close the underlying database connection."""
        self._store.close()
