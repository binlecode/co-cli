"""MemoryStore — memory domain store over IndexStore.

Owns memory-specific indexing logic:
  - Markdown frontmatter parsing
  - Paragraph chunking via co_cli.memory.chunker.chunk_text
  - Hash-skip per-file
  - Two-pass search policy (user-priority + waterfall)

Source value for the index store: ``'memory'``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.index.store import SearchResult
from co_cli.memory.chunker import chunk_text
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.memory.item import IndexSourceEnum, MemoryKindEnum

if TYPE_CHECKING:
    from co_cli.config.core import Settings
    from co_cli.index.store import IndexStore

logger = logging.getLogger(__name__)

MEMORY_SOURCE = IndexSourceEnum.MEMORY.value
"""DB ``source`` column value for memory artifacts."""

_USER_PRIORITY_CAP = 3
"""User-kind chunk hits returned in the priority pass."""

_WATERFALL_CHUNK_CAP = 5
"""Maximum chunk hits in the waterfall (rule / article / note) pass."""


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemoryStore:
    """Domain store for memory artifacts (user / rule / article / note)."""

    def __init__(self, *, index: IndexStore, config: Settings) -> None:
        self._index = index
        self._chunk_tokens = config.memory.chunk_tokens
        self._chunk_overlap_tokens = (
            max(0, min(config.memory.chunk_overlap_tokens, config.memory.chunk_tokens - 1))
            if config.memory.chunk_tokens > 0
            else 0
        )

    @property
    def index(self) -> IndexStore:
        """Expose the underlying index store for low-level lookups."""
        return self._index

    def sync_dir(
        self,
        memory_dir: Path,
        glob: str = "**/*.md",
    ) -> int:
        """Incrementally index a directory of memory artifacts.

        Hash-skip per file; removes stale entries for deleted files.
        Returns the number of files newly indexed (or re-indexed).
        """
        if not memory_dir.exists():
            return 0

        current_paths: set[str] = set()
        indexed = 0

        for file_path in memory_dir.glob(glob):
            path_str = str(file_path)
            current_paths.add(path_str)
            try:
                raw = file_path.read_text(encoding="utf-8")
                file_hash = _sha256(raw)

                if not self._index.needs_reindex(MEMORY_SOURCE, path_str, file_hash):
                    continue

                frontmatter, body = parse_frontmatter(raw)
                memory_kind = frontmatter.get("memory_kind")
                title = frontmatter.get("title") or file_path.stem
                mtime = file_path.stat().st_mtime

                with self._index.transaction() as tx:
                    tx.upsert(
                        source=MEMORY_SOURCE,
                        kind=memory_kind,
                        path=path_str,
                        title=title,
                        mtime=mtime,
                        hash=file_hash,
                        category=frontmatter.get("auto_category"),
                        created=frontmatter.get("created"),
                        updated=frontmatter.get("updated"),
                        description=frontmatter.get("description"),
                        source_ref=frontmatter.get("source_ref"),
                        artifact_id=str(frontmatter["id"])
                        if frontmatter.get("id") is not None
                        else None,
                    )
                    chunks = chunk_text(
                        body.strip(),
                        chunk_tokens=self._chunk_tokens,
                        overlap_tokens=self._chunk_overlap_tokens,
                    )
                    tx.index_chunks(MEMORY_SOURCE, path_str, chunks)
                indexed += 1
            except Exception as e:
                logger.warning(f"Failed to index {file_path}: {e}")

        self._index.remove_stale(MEMORY_SOURCE, current_paths, directory=memory_dir)
        return indexed

    def reindex_one(
        self,
        path: Path,
        body: str,
        markdown_content: str,
        frontmatter: dict,
    ) -> None:
        """Re-index a single artifact file (used after write through service.py)."""
        content_hash = _sha256(markdown_content)
        memory_kind = frontmatter.get("memory_kind", MemoryKindEnum.NOTE.value)
        with self._index.transaction() as tx:
            tx.upsert(
                source=MEMORY_SOURCE,
                kind=memory_kind,
                path=str(path),
                title=frontmatter.get("title") or path.stem,
                mtime=path.stat().st_mtime,
                hash=content_hash,
                created=frontmatter.get("created"),
                description=frontmatter.get("description"),
                source_ref=frontmatter.get("source_ref"),
                artifact_id=str(frontmatter["id"]) if frontmatter.get("id") is not None else None,
            )
            chunks = chunk_text(
                body.strip(),
                chunk_tokens=self._chunk_tokens,
                overlap_tokens=self._chunk_overlap_tokens,
            )
            tx.index_chunks(MEMORY_SOURCE, str(path), chunks)

    def remove(self, path: Path) -> None:
        self._index.remove(MEMORY_SOURCE, str(path))

    def rebuild(self, memory_dir: Path, glob: str = "**/*.md") -> int:
        """Wipe all memory rows and re-index from scratch."""
        self._index.rebuild_source(MEMORY_SOURCE)
        return self.sync_dir(memory_dir, glob)

    def list_memory_items(self, kinds: list[str] | None, limit: int) -> list[dict]:
        return self._index.list_items(MEMORY_SOURCE, kinds, limit)

    def find_by_source_ref(self, source_ref: str) -> str | None:
        return self._index.find_by_source_ref(source_ref, MEMORY_SOURCE)

    def search_memory_items(
        self,
        query: str,
        kinds: list[str] | None,
        limit: int,
    ) -> list[SearchResult]:
        """Two-pass search policy: user-kind priority + waterfall over other kinds.

        - Pass 1 (priority): user-kind hits up to ``_USER_PRIORITY_CAP``.
        - Pass 2 (waterfall): remaining kinds (rule / article / note unless caller specified).
          Returns chunk-level hits capped at ``_WATERFALL_CHUNK_CAP``.

        Final list = pass-1 results followed by pass-2 results, truncated to ``limit``.
        """
        results: list[SearchResult] = []

        if kinds is None or "user" in kinds:
            try:
                user_hits = self._index.search(
                    query,
                    sources=[MEMORY_SOURCE],
                    kinds=["user"],
                    limit=_USER_PRIORITY_CAP,
                )
                results.extend(user_hits)
            except Exception as e:
                logger.warning("User-kind priority search failed: %s", e)

        waterfall_kinds = list(set(kinds or ["rule", "article", "note"]) - {"user"})
        if waterfall_kinds:
            try:
                waterfall_hits = self._index.search(
                    query,
                    sources=[MEMORY_SOURCE],
                    kinds=waterfall_kinds,
                    limit=_WATERFALL_CHUNK_CAP,
                )
                results.extend(waterfall_hits)
            except Exception as e:
                logger.warning("Waterfall search failed: %s", e)

        return results[:limit]

    def count(self) -> int:
        return self._index.count_docs(MEMORY_SOURCE)
