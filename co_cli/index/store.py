"""IndexStore — SQLite (FTS5 + sqlite-vec) storage primitive.

Public surface to domain modules (memory, session). Owns:
  - SQLite connection and schema (docs / chunks / chunks_fts / chunks_vec)
  - Write CRUD (upsert, index_chunks, remove, remove_stale)
  - Transactions
  - Search facade (delegates to private RetrievalService)
  - Embedding cache (delegates to private EmbeddingService)

Source-agnostic — operates on opaque `source` strings (`'memory'`,
`'session'`, `'drive'`, `'canon'`).
"""

from __future__ import annotations

import logging
import re
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Any

from co_cli.config.core import SEARCH_DB
from co_cli.index._embedding import EmbeddingService
from co_cli.index._providers import build_embedder
from co_cli.index._retrieval import RecallDegradation, RetrievalService, SearchResult
from co_cli.index.chunk import Chunk
from co_cli.index.schema import SCHEMA_SQL
from co_cli.index.search_util import kind_clause
from co_cli.observability.tracing import pop_span, push_span

if TYPE_CHECKING:
    from co_cli.config.core import Settings

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

logger = logging.getLogger(__name__)


class IndexTransaction:
    """Deferred-commit transaction over the IndexStore connection.

    Use via ``with store.transaction() as tx: tx.upsert(...); tx.index_chunks(...)``.
    Commits on __exit__ success, rolls back on exception. Nesting raises.
    """

    def __init__(self, store: IndexStore) -> None:
        self._store = store
        self._active = False

    def __enter__(self) -> IndexTransaction:
        if self._store._transaction_open:
            raise RuntimeError("Nested transactions not supported")
        self._store._transaction_open = True
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        try:
            if exc_type is None:
                self._store._conn.commit()
            else:
                self._store._conn.rollback()
        finally:
            self._store._transaction_open = False
            self._active = False

    def upsert(
        self,
        *,
        source: str,
        path: str,
        kind: str | None = None,
        title: str | None = None,
        mtime: float | None = None,
        hash: str | None = None,
        category: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        description: str | None = None,
        source_ref: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        self._guard()
        self._store._upsert_no_commit(
            source=source,
            kind=kind,
            path=path,
            title=title,
            mtime=mtime,
            hash=hash,
            category=category,
            created_at=created_at,
            updated_at=updated_at,
            description=description,
            source_ref=source_ref,
            artifact_id=artifact_id,
        )

    def index_chunks(self, source: str, doc_path: str, chunks: list[Chunk]) -> None:
        self._guard()
        self._store._index_chunks_no_commit(source, doc_path, chunks)

    def remove(self, source: str, path: str) -> None:
        self._guard()
        self._store._remove_no_commit(source, path)

    def _guard(self) -> None:
        if not self._active:
            raise RuntimeError("IndexTransaction methods called outside `with` block")


class IndexStore:
    """SQLite FTS5 index store with optional sqlite-vec hybrid mode.

    Construction is eager: opens the connection, applies schema, loads
    sqlite-vec when backend='hybrid', and builds the embedding service.

    Usage:
        store = IndexStore(config=settings)
        store.upsert(source='memory', path='...', kind='note', ...)
        store.index_chunks('memory', '...', [Chunk(...), ...])
        results, degraded = store.search('pytest', sources=['memory'])
        store.close()
    """

    def __init__(self, *, config: Settings, db_path: Path | None = None) -> None:
        resolved = db_path if db_path is not None else SEARCH_DB
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = resolved

        requested = config.memory.search_backend
        embedding_provider = config.memory.embedding_provider
        self._backend = (
            "fts5" if requested == "hybrid" and embedding_provider == "none" else requested
        )
        self._embedding_dims = config.memory.embedding_dims
        self._vec_table = (
            f"chunks_vec_{self._embedding_dims}" if self._backend == "hybrid" else None
        )

        self._conn = sqlite3.connect(str(self._db_path), timeout=5)  # type: ignore[attr-defined]
        self._conn.row_factory = sqlite3.Row  # type: ignore[attr-defined]
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        self._transaction_open: bool = False

        if self._backend == "hybrid":
            try:
                self._load_sqlite_vec()
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._vec_table}"
                    f" USING vec0(embedding float[{self._embedding_dims}])"
                )
                self._conn.commit()
            except Exception as e:
                self._conn.close()
                raise RuntimeError(
                    "Hybrid backend requires sqlite extension loading support. "
                    "Install pysqlite3 and ensure sqlite-vec can be loaded."
                ) from e

        embed_fn = build_embedder(
            embedding_provider,
            config.llm.host,
            config.memory.embedding_model,
            config.memory.embed_api_url,
            config.llm.api_key,
        )
        self._embedding = EmbeddingService(
            provider=embedding_provider,
            model=config.memory.embedding_model,
            embed_fn=embed_fn,
            conn=self._conn,
        )

        self._retrieval = RetrievalService(
            conn=self._conn,
            backend=self._backend,
            vec_table=self._vec_table,
            embedding=self._embedding if self._backend == "hybrid" else None,
            cross_encoder_url=(
                config.memory.cross_encoder_reranker_url if self._backend == "hybrid" else None
            ),
            tei_batch_size=config.memory.tei_rerank_batch_size,
            rerank_text_char_budget=config.memory.rerank_text_char_budget,
            vector_similarity_floor=config.memory.vector_similarity_floor,
            rerank_score_floor=config.memory.rerank_score_floor,
        )

    @property
    def backend(self) -> str:
        return self._backend

    def _load_sqlite_vec(self) -> None:
        import sqlite_vec

        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

    def _get_vec_table_dim(self, table_name: str) -> int | None:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if row is None:
            return None
        m = re.search(r"float\[(\d+)\]", row[0] or "")
        return int(m.group(1)) if m else None

    def transaction(self) -> IndexTransaction:
        return IndexTransaction(self)

    def _upsert_no_commit(
        self,
        *,
        source: str,
        kind: str | None = None,
        path: str,
        title: str | None = None,
        mtime: float | None = None,
        hash: str | None = None,
        category: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        description: str | None = None,
        source_ref: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO docs
                   (source, kind, path, title, mtime, hash, category,
                    created_at, updated_at, description, source_ref, artifact_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, path) DO UPDATE SET
                   kind=excluded.kind, title=excluded.title,
                   mtime=excluded.mtime, hash=excluded.hash,
                   category=excluded.category, created_at=excluded.created_at,
                   updated_at=excluded.updated_at, description=excluded.description,
                   source_ref=excluded.source_ref, artifact_id=excluded.artifact_id
               WHERE excluded.hash IS NOT docs.hash""",
            (
                source,
                kind,
                path,
                title,
                mtime,
                hash,
                category,
                created_at,
                updated_at,
                description,
                source_ref,
                artifact_id,
            ),
        )

    def upsert(
        self,
        *,
        source: str,
        path: str,
        kind: str | None = None,
        title: str | None = None,
        mtime: float | None = None,
        hash: str | None = None,
        category: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        description: str | None = None,
        source_ref: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        """UPSERT one document row. Skips when hash matches stored value."""
        self._upsert_no_commit(
            source=source,
            kind=kind,
            path=path,
            title=title,
            mtime=mtime,
            hash=hash,
            category=category,
            created_at=created_at,
            updated_at=updated_at,
            description=description,
            source_ref=source_ref,
            artifact_id=artifact_id,
        )
        self._conn.commit()

    def _index_chunks_no_commit(
        self,
        source: str,
        doc_path: str,
        chunks: list[Chunk],
    ) -> None:
        if self._backend == "hybrid" and self._vec_table is not None:
            existing_rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM chunks WHERE source=? AND doc_path=?",
                    (source, doc_path),
                ).fetchall()
            ]
            if existing_rowids:
                placeholders = ",".join("?" * len(existing_rowids))
                self._conn.execute(
                    f"DELETE FROM {self._vec_table} WHERE rowid IN ({placeholders})",
                    existing_rowids,
                )

        self._conn.execute(
            "DELETE FROM chunks WHERE source=? AND doc_path=?",
            (source, doc_path),
        )

        for chunk in chunks:
            cur = self._conn.execute(
                """INSERT INTO chunks (source, doc_path, chunk_index, content, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source, doc_path, chunk.index, chunk.content, chunk.start_line, chunk.end_line),
            )
            if self._backend == "hybrid" and self._vec_table is not None:
                emb = self._embedding.embed(chunk.content or "")
                if emb is not None:
                    blob = struct.pack(f"{len(emb)}f", *emb)
                    self._conn.execute(
                        f"INSERT INTO {self._vec_table}(rowid, embedding) VALUES (?, ?)",
                        (cur.lastrowid, blob),
                    )

    def index_chunks(
        self,
        source: str,
        doc_path: str,
        chunks: list[Chunk],
    ) -> None:
        """Replace chunks for (source, doc_path) atomically; commits on success."""
        self._index_chunks_no_commit(source, doc_path, chunks)
        self._conn.commit()

    def _remove_chunks_no_commit(self, source: str, path: str) -> None:
        if self._backend == "hybrid" and self._vec_table is not None:
            rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM chunks WHERE source=? AND doc_path=?",
                    (source, path),
                ).fetchall()
            ]
            if rowids:
                placeholders = ",".join("?" * len(rowids))
                self._conn.execute(
                    f"DELETE FROM {self._vec_table} WHERE rowid IN ({placeholders})",
                    rowids,
                )
        self._conn.execute(
            "DELETE FROM chunks WHERE source=? AND doc_path=?",
            (source, path),
        )

    def remove_chunks(self, source: str, path: str) -> None:
        """Remove all chunk rows for (source, path), including FTS + vec entries."""
        self._remove_chunks_no_commit(source, path)
        self._conn.commit()

    def _remove_no_commit(self, source: str, path: str) -> None:
        self._remove_chunks_no_commit(source, path)
        self._conn.execute(
            "DELETE FROM docs WHERE source = ? AND path = ?",
            (source, path),
        )

    def remove(self, source: str, path: str) -> None:
        """Remove one document and its chunks."""
        self._remove_no_commit(source, path)
        self._conn.commit()

    def remove_stale(
        self,
        source: str,
        current_paths: set[str],
        directory: Path | None = None,
    ) -> int:
        """Remove index entries for paths no longer present in current_paths.

        When ``directory`` is given, only considers entries whose path starts
        with str(directory) — prevents a subfolder scan from evicting siblings.

        Atomic: all deletions in a single transaction.
        """
        rows = self._conn.execute(
            "SELECT path FROM docs WHERE source = ?",
            (source,),
        ).fetchall()

        if directory is not None:
            dir_prefix = str(directory)
            rows = [
                r
                for r in rows
                if r["path"].startswith(dir_prefix + "/") or r["path"] == dir_prefix
            ]

        to_delete = list({row["path"] for row in rows if row["path"] not in current_paths})
        if not to_delete:
            return 0

        for path in to_delete:
            self._remove_no_commit(source, path)
        self._conn.commit()
        return len(to_delete)

    def rebuild_source(self, source: str) -> None:
        """Wipe all chunks + docs for a source (caller re-indexes from files)."""
        self._remove_chunks_for_source(source)
        self._conn.execute("DELETE FROM chunks WHERE source = ?", (source,))
        self._conn.execute("DELETE FROM docs WHERE source = ?", (source,))
        self._conn.commit()

    def _remove_chunks_for_source(self, source: str) -> None:
        if self._backend == "hybrid" and self._vec_table is not None:
            chunk_rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM chunks WHERE source = ?", (source,)
                ).fetchall()
            ]
            if chunk_rowids:
                placeholders = ",".join("?" * len(chunk_rowids))
                self._conn.execute(
                    f"DELETE FROM {self._vec_table} WHERE rowid IN ({placeholders})",
                    chunk_rowids,
                )

    def needs_reindex(self, source: str, path: str, current_hash: str) -> bool:
        """True when the file at path needs re-indexing (hash changed or absent)."""
        row = self._conn.execute(
            "SELECT hash FROM docs WHERE source = ? AND path = ?",
            (source, path),
        ).fetchone()
        if row is None:
            return True
        return row["hash"] != current_hash

    def find_by_source_ref(self, source_ref: str, source: str) -> str | None:
        """Return the path of the doc with the given source_ref, or None."""
        row = self._conn.execute(
            "SELECT path FROM docs WHERE source = ? AND source_ref = ?",
            (source, source_ref),
        ).fetchone()
        return row["path"] if row else None

    def list_items(self, source: str, kinds: list[str] | None, limit: int) -> list[dict[str, Any]]:
        """Return inventory rows for a source, sorted by created_at DESC."""
        k_sql, k_params = kind_clause(kinds, "d.kind")
        rows = self._conn.execute(
            f"""SELECT d.path, d.kind, d.title, d.created_at,
                       c.content AS snippet
                FROM docs d
                LEFT JOIN chunks c
                  ON c.doc_path = d.path AND c.source = d.source AND c.chunk_index = 0
                WHERE d.source = ?{k_sql}
                ORDER BY d.created_at DESC LIMIT ?""",
            [source, *k_params, limit],
        ).fetchall()
        return [
            {
                "kind": row["kind"],
                "title": row["title"] or Path(row["path"]).stem,
                "snippet": (row["snippet"] or "")[:100],
                "score": 0.0,
                "path": row["path"],
                "filename_stem": Path(row["path"]).stem,
            }
            for row in rows
        ]

    def count_docs(self, source: str) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM docs WHERE source=?", (source,)).fetchone()
        return row[0] if row else 0

    def list_titles_by_source(self, source: str) -> set[str]:
        rows = self._conn.execute("SELECT title FROM docs WHERE source=?", (source,)).fetchall()
        return {row["title"] for row in rows if row["title"]}

    def get_path_by_title(self, source: str, title: str) -> str | None:
        row = self._conn.execute(
            "SELECT path FROM docs WHERE source=? AND title=?", (source, title)
        ).fetchone()
        return row["path"] if row else None

    def get_chunk_content(self, source: str, doc_path: str, chunk_index: int) -> str | None:
        row = self._conn.execute(
            "SELECT content FROM chunks WHERE source=? AND doc_path=? AND chunk_index=?",
            (source, doc_path, chunk_index),
        ).fetchone()
        return row["content"] if row else None

    def search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        kinds: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 5,
    ) -> tuple[list[SearchResult], frozenset[RecallDegradation]]:
        """Ranked search facade — delegates to the private RetrievalService.

        Emits an ``index.search`` span per invocation so recall work (FTS5/BM25 +
        embedding + hybrid merge) is attributable in ``co trace`` under the
        ``memory_search`` / ``session_search`` tool span. ``co.index.hits`` is
        THIS invocation's returned count — callers that search twice (e.g.
        ``MemoryStore.search_memory_items`` kinds-filtered path) emit one span per
        call, none of which is the tool's final merged/capped list.
        """
        push_span(
            "index.search",
            attributes={
                "co.index.query_len": len(query),
                "co.index.sources": sources,
                "co.index.kinds": kinds,
                "co.index.limit": limit,
            },
        )
        try:
            results, degraded = self._retrieval.search(
                query,
                sources=sources,
                kinds=kinds,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
        except BaseException as exc:
            pop_span(status="ERROR", status_msg=str(exc))
            raise
        pop_span(
            attributes={
                "co.index.hits": len(results),
                "co.index.degraded": sorted(d.value for d in degraded),
            }
        )
        return results, degraded

    def probe(self) -> None:
        """Health check — raises on first error found."""
        self._conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1",
            ("probe",),
        ).fetchone()

        if self._backend == "hybrid" and self._vec_table is not None:
            self._conn.execute(f"SELECT rowid FROM {self._vec_table} LIMIT 1").fetchone()

    def close(self) -> None:
        self._conn.close()
