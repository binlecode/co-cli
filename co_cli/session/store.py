"""SessionStore — session domain store over IndexStore.

Owns session-specific indexing logic:
  - JSONL filename parsing
  - Transcript extraction → chunk_session pipeline
  - Hash-skip via full-text content hash
  - Append-only sync (no mutation surface for the agent)

The `doc_path` for a session is its uuid8 (not a filesystem path).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from co_cli.index.store import SearchResult
from co_cli.session.chunker import chunk_session
from co_cli.session.filename import parse_session_filename

if TYPE_CHECKING:
    from co_cli.config.core import Settings
    from co_cli.index.store import IndexStore

logger = logging.getLogger(__name__)

SESSION_SOURCE = "session"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SessionStore:
    """Domain store for past session transcripts."""

    def __init__(self, *, index: IndexStore, config: Settings) -> None:
        self._index = index
        self._chunk_tokens = config.memory.session_chunk_tokens
        self._chunk_overlap = config.memory.session_chunk_overlap

    def index_session(self, session_path: Path) -> None:
        """Index a session JSONL into the shared chunks pipeline.

        Idempotent — content-hash skip avoids re-embedding unchanged sessions.
        doc_path is the uuid8 (8-char ID from the session filename).
        """
        parsed = parse_session_filename(session_path.name)
        if parsed is None:
            logger.warning("Unrecognised session filename: %s", session_path.name)
            return
        uuid8, created_at = parsed

        sess_chunks = chunk_session(
            session_path,
            chunk_tokens=self._chunk_tokens,
            overlap_tokens=self._chunk_overlap,
        )
        if not sess_chunks:
            return

        full_text = "\n\n".join(c.content for c in sess_chunks)
        content_hash = _sha256(full_text)

        if not self._index.needs_reindex(SESSION_SOURCE, uuid8, content_hash):
            return

        with self._index.transaction() as tx:
            tx.upsert(
                source=SESSION_SOURCE,
                kind=SESSION_SOURCE,
                path=uuid8,
                title=uuid8,
                mtime=session_path.stat().st_mtime,
                hash=content_hash,
                created_at=created_at.isoformat(),
                updated_at=created_at.isoformat(),
            )
            tx.index_chunks(SESSION_SOURCE, uuid8, sess_chunks)

    def sync(self, sessions_dir: Path, exclude: Path | None = None) -> int:
        """Incrementally index all sessions in sessions_dir.

        Skips ``exclude`` (the active session). Removes stale entries for
        deleted sessions. Returns the number of sessions processed.
        """
        if not sessions_dir.exists():
            return 0

        current_uuid8s: set[str] = set()
        processed = 0

        for file_path in sessions_dir.glob("*.jsonl"):
            if exclude is not None and file_path == exclude:
                continue
            parsed = parse_session_filename(file_path.name)
            if parsed is None:
                continue
            uuid8, _ = parsed
            current_uuid8s.add(uuid8)
            try:
                self.index_session(file_path)
                processed += 1
            except Exception as e:
                logger.warning("Failed to index session %s: %s", file_path.name, e)

        # doc_path is uuid8 (not a filesystem path); no directory filter.
        self._index.remove_stale(SESSION_SOURCE, current_uuid8s)
        return processed

    def search(self, query: str, limit: int) -> list[SearchResult]:
        """Ranked search over indexed sessions."""
        return self._index.search(query, sources=[SESSION_SOURCE], limit=limit)

    def count(self) -> int:
        return self._index.count_docs(SESSION_SOURCE)
