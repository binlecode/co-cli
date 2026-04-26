"""SQLite FTS5 memory index — storage, sync, and search.

Manages a user-global SQLite database (~/.co-cli/session-index.db) that indexes
user-prompt and assistant-text content from past session JSONL transcripts.
Supports keyword search via FTS5 BM25 ranking with porter unicode61 tokeniser.

Change detection uses file size: since transcripts are append-only, a size
increase always means new content. A session whose size is unchanged is skipped.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from co_cli.memory.indexer import extract_messages
from co_cli.memory.session import parse_session_filename

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    session_path TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    file_size    INTEGER NOT NULL,
    indexed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    line_index   INTEGER NOT NULL,
    part_index   INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    timestamp    TEXT,
    UNIQUE(session_id, line_index, part_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    tokenize='porter unicode61',
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


@dataclass
class SessionSearchResult:
    """One search hit deduplicated to the best-matching message per session."""

    session_id: str
    session_path: str
    created_at: str
    role: str
    snippet: str
    score: float


class MemoryIndex:
    """FTS5 index over session transcript content.

    Lifecycle:
      store = MemoryIndex(db_path)
      store.sync_sessions(sessions_dir, exclude=current_path)
      results = store.search("query")
      store.close()
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def index_session(self, session_path: Path) -> None:
        """Index all messages from one session JSONL file.

        Deletes existing rows for this session then re-inserts — triggers
        automatically maintain the FTS index.
        """
        parsed = parse_session_filename(session_path.name)
        if parsed is None:
            logger.warning("Unrecognised session filename: %s", session_path.name)
            return

        uuid8, created_at = parsed
        try:
            file_size = session_path.stat().st_size
        except OSError as exc:
            logger.warning("Cannot stat %s: %s", session_path.name, exc)
            return

        messages = extract_messages(session_path)
        indexed_at = datetime.now(UTC).isoformat()

        with self._conn:
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (uuid8,))
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, session_path, created_at, file_size, indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    file_size  = excluded.file_size,
                    indexed_at = excluded.indexed_at
                """,
                (
                    uuid8,
                    str(session_path.resolve()),
                    created_at.isoformat(),
                    file_size,
                    indexed_at,
                ),
            )
            self._conn.executemany(
                """
                INSERT OR IGNORE INTO messages
                    (session_id, line_index, part_index, role, content, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (uuid8, msg.line_index, msg.part_index, msg.role, msg.content, msg.timestamp)
                    for msg in messages
                ],
            )

    def sync_sessions(
        self,
        sessions_dir: Path,
        exclude: Path | None = None,
    ) -> None:
        """Sync all sessions in sessions_dir into the index.

        Skips sessions whose recorded file_size matches the current size.
        Re-indexes any session whose file has grown (append-only transcripts).
        The exclude path (typically the active session) is always skipped.
        """
        if not sessions_dir.exists():
            return

        rows = self._conn.execute("SELECT session_id, file_size FROM sessions").fetchall()
        known_sizes: dict[str, int] = {row["session_id"]: row["file_size"] for row in rows}

        exclude_resolved = exclude.resolve() if exclude is not None else None

        for session_file in sorted(sessions_dir.glob("*.jsonl")):
            if exclude_resolved is not None and session_file.resolve() == exclude_resolved:
                continue
            parsed = parse_session_filename(session_file.name)
            if parsed is None:
                continue
            uuid8, _ = parsed
            try:
                current_size = session_file.stat().st_size
            except OSError:
                continue
            if known_sizes.get(uuid8) == current_size:
                continue
            self.index_session(session_file)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
    ) -> list[SessionSearchResult]:
        """Search indexed sessions by keyword.

        Returns up to limit results, deduplicated to one per session (the
        highest-scoring matching message). Results are sorted by score desc.
        """
        sql = """
            SELECT
                s.session_id,
                s.session_path,
                s.created_at,
                m.role,
                snippet(messages_fts, 0, '[', ']', '...', 10) AS snippet,
                bm25(messages_fts) AS rank
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.id
            JOIN sessions s ON m.session_id = s.session_id
            WHERE messages_fts MATCH ?
            ORDER BY rank
        """
        try:
            rows = self._conn.execute(sql, (query,)).fetchall()
        except sqlite3.OperationalError as exc:
            logger.warning("Session search failed: %s", exc)
            return []

        # Deduplicate: keep best-scoring (lowest abs rank) message per session
        best: dict[str, SessionSearchResult] = {}
        for row in rows:
            sid = row["session_id"]
            score = 1.0 / (1.0 + abs(row["rank"]))
            if sid in best and score <= best[sid].score:
                continue
            best[sid] = SessionSearchResult(
                session_id=sid,
                session_path=row["session_path"],
                created_at=row["created_at"],
                role=row["role"],
                snippet=row["snippet"],
                score=score,
            )

        sorted_results = sorted(best.values(), key=lambda r: r.score, reverse=True)
        return sorted_results[:limit]

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception as exc:
            logger.debug("Session index close error: %s", exc)
