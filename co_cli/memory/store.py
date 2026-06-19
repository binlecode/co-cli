"""MemoryStore — memory domain store over IndexStore.

Owns memory-specific indexing logic:
  - Markdown frontmatter parsing
  - Paragraph chunking via co_cli.memory.chunker.chunk_text
  - Hash-skip per-file
  - Single waterfall search policy over rule / article / note

Source value for the index store: ``'memory'``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.index.store import RecallDegradation, SearchResult
from co_cli.memory.chunker import chunk_text
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.memory.item import IndexSourceEnum, MemoryKindEnum

if TYPE_CHECKING:
    from co_cli.config.core import Settings
    from co_cli.index.store import IndexStore

logger = logging.getLogger(__name__)

MEMORY_SOURCE = IndexSourceEnum.MEMORY.value
"""DB ``source`` column value for memory artifacts."""

_WATERFALL_CHUNK_CAP = 5
"""Maximum chunk hits in the waterfall (rule / article / note) search."""


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemoryStore:
    """Domain store for memory artifacts (rule / article / note)."""

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
        glob: str = "*.md",
    ) -> int:
        """Incrementally index a directory of memory artifacts.

        Hash-skip per file; removes stale entries for deleted files.
        Returns the number of files newly indexed (or re-indexed).

        Default glob is top-level ``*.md`` only — mirroring ``load_memory_items``
        so ``_archive/`` is never traversed and archived items stay out of the index.
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
                        created_at=frontmatter.get("created_at"),
                        updated_at=frontmatter.get("updated_at"),
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
                created_at=frontmatter.get("created_at"),
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

    def rebuild(self, memory_dir: Path, glob: str = "*.md") -> int:
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
    ) -> tuple[list[SearchResult], frozenset[RecallDegradation]]:
        """Single waterfall search over rule / article / note kinds.

        Returns chunk-level hits capped at ``_WATERFALL_CHUNK_CAP``, truncated to
        ``limit``. The degradation set is empty when recall is healthy.
        """
        results: list[SearchResult] = []
        degraded: set[RecallDegradation] = set()

        waterfall_kinds = list(kinds or ["rule", "article", "note"])
        if waterfall_kinds:
            try:
                waterfall_hits, waterfall_degraded = self._index.search(
                    query,
                    sources=[MEMORY_SOURCE],
                    kinds=waterfall_kinds,
                    limit=_WATERFALL_CHUNK_CAP,
                )
                results.extend(waterfall_hits)
                degraded |= waterfall_degraded
            except Exception as e:
                logger.warning("Waterfall search failed: %s", e)

        return results[:limit], frozenset(degraded)

    def count(self) -> int:
        return self._index.count_docs(MEMORY_SOURCE)
