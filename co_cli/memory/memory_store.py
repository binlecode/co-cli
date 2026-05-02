"""SQLite FTS5 memory index for ranked search across all text sources.

MemoryStore is a single SQLite-backed search index (``co-cli-search.db``) that any
source writes to. The ``source`` column distinguishes origin; the ``kind`` /
``type`` columns hold the ``artifact_kind`` subtype for local artifacts.

Source namespace:
  source='knowledge' — reusable artifacts under ~/.co-cli/knowledge/
                       (user, rule, article, note)
  source='obsidian'  — Obsidian vault notes
  source='drive'     — Google Drive docs (indexed on read)

All sources chunk into ``chunks_fts`` (and ``chunks_vec`` in hybrid mode). The
index is derived and rebuildable — deleting ``search.db`` and restarting
rebuilds cleanly from files. Hybrid mode adds sqlite-vec vector similarity
merged via RRF and falls back to FTS5-only if the embedding provider is
unavailable.
"""

import dataclasses
import hashlib
import logging
import re
import struct
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

from co_cli.config.core import SEARCH_DB
from co_cli.memory.frontmatter import parse_frontmatter
from co_cli.memory.search_util import normalize_bm25, run_fts, sanitize_fts5_query

if TYPE_CHECKING:
    from co_cli.config.core import Settings

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS docs (
    source      TEXT NOT NULL,
    kind        TEXT,
    path        TEXT NOT NULL,
    title       TEXT,
    mtime       REAL,
    hash        TEXT,
    category    TEXT,
    created     TEXT,
    updated     TEXT,
    type        TEXT,
    description TEXT,
    source_ref  TEXT,
    artifact_id TEXT,
    UNIQUE(source, path)
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding    BLOB NOT NULL,
    created      TEXT NOT NULL,
    PRIMARY KEY (provider, model, content_hash)
);

CREATE TABLE IF NOT EXISTS chunks (
    source      TEXT NOT NULL,
    doc_path    TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT,
    start_line  INTEGER,
    end_line    INTEGER,
    hash        TEXT,
    PRIMARY KEY (source, doc_path, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    tokenize='porter unicode61',
    content='chunks',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content)
    VALUES ('delete', old.rowid, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.rowid, new.content);
END;
"""

_CHUNKS_FTS_SQL = """
SELECT c.source, c.doc_path AS path,
       snippet(chunks_fts, 0, '>', '<', '...', 40) AS snippet,
       bm25(chunks_fts) AS rank,
       c.chunk_index, c.start_line, c.end_line,
       d.kind, d.title, d.category, d.created, d.updated,
       d.source_ref, d.artifact_id
  FROM chunks_fts
  JOIN chunks c ON c.rowid = chunks_fts.rowid
  JOIN docs d ON d.source = c.source AND d.path = c.doc_path
 WHERE chunks_fts MATCH ?
"""


def _chunks_like_search(
    conn: Any,
    tokens: list[str],
    *,
    sources: list[str] | None,
    kinds: list[str] | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
) -> list[Any]:
    """LIKE fallback for chunks FTS — returns rows with the same columns as _CHUNKS_FTS_SQL."""
    like_conds = " OR ".join("c.content LIKE ?" for _ in tokens)
    like_params = [f"%{t}%" for t in tokens]

    # Per-row match-count: negate so normalize_bm25 maps more matches → higher score.
    case_exprs = " + ".join("CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in tokens)
    rank_expr = f"-({case_exprs})" if tokens else "-1.0"

    # rank CASE params appear in SELECT (before WHERE), so they come first in lp.
    lp: list[Any] = list(like_params) + list(like_params)

    lsql = (
        "SELECT c.source, c.doc_path AS path,"
        f" substr(c.content, 1, 300) AS snippet, {rank_expr} AS rank,"
        " c.chunk_index, c.start_line, c.end_line,"
        " d.kind, d.title, d.category, d.created, d.updated,"
        " d.source_ref, d.artifact_id"
        " FROM chunks c"
        " JOIN docs d ON d.source = c.source AND d.path = c.doc_path"
        f" WHERE ({like_conds})"
    )
    if sources is not None and len(sources) == 1:
        lsql += " AND c.source = ?"
        lp.append(sources[0])
    elif sources is not None and len(sources) > 1:
        ph = ",".join("?" * len(sources))
        lsql += f" AND c.source IN ({ph})"
        lp.extend(sources)
    kind_sql, kind_params = _kind_clause(kinds, "d.kind")
    lsql += kind_sql
    lp.extend(kind_params)
    if created_after is not None:
        lsql += " AND d.created >= ?"
        lp.append(created_after)
    if created_before is not None:
        lsql += " AND d.created <= ?"
        lp.append(created_before)
    lsql += " LIMIT ?"
    lp.append(limit)
    try:
        return conn.execute(lsql, lp).fetchall()
    except Exception:
        return []


def _kind_clause(kinds: list[str] | None, col: str = "kind") -> tuple[str, list]:
    """Return (sql_fragment, params) for a kind IN filter, or ('', []) if None."""
    if kinds is None:
        return "", []
    placeholders = ",".join("?" * len(kinds))
    return f" AND {col} IN ({placeholders})", list(kinds)


@dataclass
class SearchResult:
    """A single result from MemoryStore.search()."""

    source: str
    kind: str | None
    path: str
    title: str | None
    snippet: str | None
    score: float
    category: str | None
    created: str | None
    updated: str | None
    confidence: float | None = None
    chunk_index: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    type: str | None = None
    description: str | None = None
    source_ref: str | None = None
    artifact_id: str | None = None

    def to_tool_output(self, *, conflict: bool = False) -> dict:
        """Return the standard tool output dict for this search result."""
        return {
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
            "path": self.path,
            "confidence": self.confidence,
            "conflict": conflict,
        }


# Type alias for the (path, chunk_index) key used in RRF merging.
type _ChunkKey = tuple[str, int | None]


class MemoryStore:
    """SQLite FTS5 index for ranked search across all memory sources.

    Supports two backends:
      - 'fts5' (default): BM25 ranked full-text search only.
      - 'hybrid': FTS5 + sqlite-vec cosine vector search, RRF merge.

    Usage:
        idx = MemoryStore(config=Settings())
        idx.sync_dir("knowledge", knowledge_dir)
        results = idx.search("pytest testing", sources=["knowledge"])
        idx.close()
    """

    def __init__(self, *, config: "Settings", memory_db_path: Path | None = None) -> None:
        db_path = memory_db_path if memory_db_path is not None else SEARCH_DB
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._embedding_provider = config.knowledge.embedding_provider
        # hybrid requires a real embedding provider; degrade to fts5 when none is configured.
        _requested_backend = config.knowledge.search_backend
        self._backend = (
            "fts5"
            if _requested_backend == "hybrid" and self._embedding_provider == "none"
            else _requested_backend
        )
        self._embedding_model = config.knowledge.embedding_model
        self._embedding_dims = config.knowledge.embedding_dims
        self._chunks_vec_table = f"chunks_vec_{self._embedding_dims}"
        self._llm_host = config.llm.host
        self._llm_api_key = config.llm.api_key
        self._embed_api_url = config.knowledge.embed_api_url
        self._cross_encoder_url = config.knowledge.cross_encoder_reranker_url
        self._tei_batch_size = config.knowledge.tei_rerank_batch_size
        self._chunk_size = config.knowledge.chunk_size
        self._chunk_overlap = (
            max(0, min(config.knowledge.chunk_overlap, config.knowledge.chunk_size - 1))
            if config.knowledge.chunk_size > 0
            else 0
        )
        self._session_chunk_tokens = config.knowledge.session_chunk_tokens
        self._session_chunk_overlap = config.knowledge.session_chunk_overlap

        self._reranker_provider = "tei" if self._cross_encoder_url is not None else "none"

        from co_cli.memory._embedder import build_embedder

        self._embed_fn = build_embedder(
            self._embedding_provider,
            self._llm_host,
            self._embedding_model,
            self._embed_api_url,
            self._llm_api_key,
        )

        self._conn = sqlite3.connect(str(self._db_path), timeout=5)  # type: ignore[attr-defined]  # pysqlite3 is a binary extension without type stubs
        self._conn.row_factory = sqlite3.Row  # type: ignore[attr-defined]  # pysqlite3 is a binary extension without type stubs
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

        if self._backend == "hybrid":
            try:
                self._load_sqlite_vec()
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._chunks_vec_table}"
                    f" USING vec0(embedding float[{self._embedding_dims}])"
                )
                self._conn.commit()
            except Exception as e:
                self._conn.close()
                raise RuntimeError(
                    "Hybrid backend requires sqlite extension loading support. "
                    "Install pysqlite3 and ensure sqlite-vec can be loaded."
                ) from e

    def _load_sqlite_vec(self) -> None:
        """Load the sqlite-vec extension into the current connection."""
        import sqlite_vec

        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

    def _get_vec_table_dim(self, table_name: str) -> int | None:
        """Return the embedding dimension declared in an existing vec0 table, or None."""
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if row is None:
            return None
        m = re.search(r"float\[(\d+)\]", row[0] or "")
        return int(m.group(1)) if m else None

    def index(
        self,
        *,
        source: str,
        kind: str | None = None,
        path: str,
        title: str | None = None,
        mtime: float | None = None,
        hash: str | None = None,
        category: str | None = None,
        created: str | None = None,
        updated: str | None = None,
        type: str | None = None,
        description: str | None = None,
        source_ref: str | None = None,
        artifact_id: str | None = None,
        **_kwargs: object,
    ) -> None:
        """Insert or update a document in the index.

        Skips the write if `hash` matches the stored value (no change).
        Uses INSERT OR REPLACE for upsert semantics.
        """
        existing = self._conn.execute(
            "SELECT hash FROM docs WHERE source = ? AND path = ?",
            (source, path),
        ).fetchone()

        if hash is not None and existing is not None and existing["hash"] == hash:
            return  # unchanged

        self._conn.execute("DELETE FROM docs WHERE source = ? AND path = ?", (source, path))

        self._conn.execute(
            """INSERT INTO docs
                   (source, kind, path, title, mtime, hash, category,
                    created, updated, type, description, source_ref, artifact_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source,
                kind,
                path,
                title,
                mtime,
                hash,
                category,
                created,
                updated,
                type,
                description,
                source_ref,
                artifact_id,
            ),
        )
        self._conn.commit()

    def index_chunks(
        self,
        source: str,
        doc_path: str,
        chunks: list[Any],
    ) -> None:
        """Write paragraph chunks to chunks/chunks_fts (and chunks_vec in hybrid mode).

        Replaces all existing chunks for (source, doc_path) atomically.

        Args:
            source: Source label ('knowledge', 'obsidian', 'drive').
            doc_path: Path key matching the docs.path for this document.
            chunks: List of Chunk objects from _chunker.chunk_text().
        """
        if self._backend == "hybrid":
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
                    f"DELETE FROM {self._chunks_vec_table} WHERE rowid IN ({placeholders})",
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
            if self._backend == "hybrid":
                emb = self._embed_cached(chunk.content or "")
                if emb is not None:
                    blob = struct.pack(f"{len(emb)}f", *emb)
                    self._conn.execute(
                        f"INSERT INTO {self._chunks_vec_table}(rowid, embedding) VALUES (?, ?)",
                        (cur.lastrowid, blob),
                    )

        self._conn.commit()

    def remove_chunks(self, source: str, path: str) -> None:
        """Remove all chunk rows for (source, path), including FTS and vec entries."""
        if self._backend == "hybrid":
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
                    f"DELETE FROM {self._chunks_vec_table} WHERE rowid IN ({placeholders})",
                    rowids,
                )
        self._conn.execute(
            "DELETE FROM chunks WHERE source=? AND doc_path=?",
            (source, path),
        )
        self._conn.commit()

    def search(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        kinds: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Search the index with BM25 ranking (FTS5) or hybrid BM25+vector.

        Returns an empty list when the query is empty, stopword-only, or
        produces no matches.

        In hybrid mode, falls back to FTS5 if the embedding provider fails.

        Source filter shortcuts:
          sources=["knowledge"] → local knowledge artifacts
          sources=["obsidian"]  → Obsidian vault notes only
          sources=["drive"]     → Google Drive documents only
          sources=["knowledge", "obsidian", "drive"] → multiple sources via IN-clause
        """
        if self._build_fts_query(query) is None:
            return []

        if self._backend == "hybrid":
            return self._hybrid_search(
                query,
                sources=sources,
                kinds=kinds,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
        # Fetch a larger candidate pool when reranker is active so it has
        # meaningful signal to reorder; otherwise fetch exactly what caller needs.
        fetch_limit = limit * 4 if self._reranker_provider != "none" else limit
        results = self._fts_search(
            query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=fetch_limit,
        )
        return self._rerank_results(query, results, limit)

    def _hybrid_search(
        self,
        query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Hybrid BM25 + vector search with chunk-level RRF. Falls back to FTS5."""
        fts_chunks = self._fts_chunks_raw(
            query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=limit * 4,
        )
        try:
            emb = self._embed_cached(query)
            if emb is not None:
                vec_chunks = self._vec_chunks_search(
                    struct.pack(f"{len(emb)}f", *emb),
                    sources=sources,
                    kinds=kinds,
                    created_after=created_after,
                    created_before=created_before,
                    limit=limit * 16,
                )
                merged = self._hybrid_merge(fts_chunks, vec_chunks)
                return self._rerank_results(query, merged, limit)
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to FTS: {e}")

        # Fallback: collapse chunk-level FTS results to doc-level
        fallback_seen: dict[str, SearchResult] = {}
        for r in fts_chunks:
            if r.path not in fallback_seen or r.score > fallback_seen[r.path].score:
                fallback_seen[r.path] = r
        fts_results = sorted(fallback_seen.values(), key=lambda r: r.score, reverse=True)
        return self._rerank_results(query, fts_results, limit)

    @staticmethod
    def _chunk_row_to_result(row: Any) -> "SearchResult":
        return SearchResult(
            source=row["source"],
            kind=row["kind"],
            path=row["path"],
            title=row["title"],
            snippet=row["snippet"],
            score=normalize_bm25(row["rank"]),
            category=row["category"],
            created=row["created"],
            updated=row["updated"],
            chunk_index=row["chunk_index"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            source_ref=row["source_ref"],
            artifact_id=row["artifact_id"],
        )

    def _fts_search(
        self,
        query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """BM25 FTS5 search over chunks_fts — single leg for all sources."""
        fts_query = self._build_fts_query(query)
        if fts_query is None:
            return []

        # Chunks leg always uses a larger pool: one document produces N chunk rows,
        # so limiting at chunk granularity causes a single long article to crowd out
        # other matching documents before Python-side doc-level dedup can run.
        chunks_fetch_limit = limit * 20

        all_rows: list[tuple[Any, str]] = self._run_chunks_fts(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=chunks_fetch_limit,
        )

        results = [self._chunk_row_to_result(row) for row, _leg in all_rows]

        # Deduplicate by path, keep highest score per document
        seen: dict[str, SearchResult] = {}
        for r in results:
            if r.path not in seen or r.score > seen[r.path].score:
                seen[r.path] = r
        sorted_results = sorted(seen.values(), key=lambda r: r.score, reverse=True)
        return sorted_results[:limit]

    def _run_chunks_fts(
        self,
        fts_query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[tuple[Any, str]]:
        """Execute chunks_fts (non-memory) leg. Returns (row, 'chunks') tuples."""
        # Empty explicit source list → no results.
        if sources is not None and not sources:
            return []
        sql = _CHUNKS_FTS_SQL
        params: list[Any] = [fts_query]
        if sources is not None and len(sources) == 1:
            sql += " AND c.source = ?"
            params.append(sources[0])
        elif sources is not None and len(sources) > 1:
            placeholders = ",".join("?" * len(sources))
            sql += f" AND c.source IN ({placeholders})"
            params.extend(sources)
        kind_sql, kind_params = _kind_clause(kinds, "d.kind")
        sql += kind_sql
        params.extend(kind_params)
        if created_after is not None:
            sql += " AND d.created >= ?"
            params.append(created_after)
        if created_before is not None:
            sql += " AND d.created <= ?"
            params.append(created_before)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        def _like_fallback(conn: Any, tokens: list[str]) -> list[Any]:
            return _chunks_like_search(
                conn,
                tokens,
                sources=sources,
                kinds=kinds,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )

        rows = run_fts(self._conn, sql, params, label="Chunks FTS", like_fallback=_like_fallback)
        return [(row, "chunks") for row in rows]

    def _fts_chunks_raw(
        self,
        query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Return chunk-level FTS results for hybrid RRF (non-memory sources only)."""
        fts_query = self._build_fts_query(query)
        if fts_query is None:
            return []

        chunks_fetch_limit = limit * 20

        chunk_rows = self._run_chunks_fts(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=chunks_fetch_limit,
        )

        return [self._chunk_row_to_result(row) for row, _leg in chunk_rows]

    def _vec_chunks_search(
        self,
        blob: bytes,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Vector search against chunks_vec (non-memory sources)."""
        vec_rows = self._conn.execute(
            f"SELECT rowid, distance FROM {self._chunks_vec_table} WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (blob, limit),
        ).fetchall()
        if not vec_rows:
            return []

        rowid_to_distance = {row["rowid"]: row["distance"] for row in vec_rows}
        rowids = list(rowid_to_distance.keys())
        placeholders = ",".join("?" * len(rowids))
        chunk_sql = (
            f"SELECT rowid, source, doc_path, chunk_index, start_line, end_line"
            f" FROM chunks WHERE rowid IN ({placeholders})"
        )
        chunk_params: list[Any] = list(rowids)
        if sources is not None and len(sources) == 1:
            chunk_sql += " AND source = ?"
            chunk_params.append(sources[0])
        elif sources is not None and len(sources) > 1:
            src_ph = ",".join("?" * len(sources))
            chunk_sql += f" AND source IN ({src_ph})"
            chunk_params.extend(sources)

        chunk_rows = self._conn.execute(chunk_sql, chunk_params).fetchall()
        if not chunk_rows:
            return []

        # Batch-fetch doc metadata for unique doc_paths
        doc_paths = list({row["doc_path"] for row in chunk_rows})
        doc_ph = ",".join("?" * len(doc_paths))
        doc_sql = (
            f"SELECT source, kind, path, title, category, created, updated, "
            f"type, description, source_ref, artifact_id"
            f" FROM docs WHERE path IN ({doc_ph})"
        )
        doc_params: list[Any] = doc_paths
        kind_sql, kind_params = _kind_clause(kinds)
        doc_sql += kind_sql
        doc_params.extend(kind_params)
        if created_after is not None:
            doc_sql += " AND created >= ?"
            doc_params.append(created_after)
        if created_before is not None:
            doc_sql += " AND created <= ?"
            doc_params.append(created_before)

        doc_rows = self._conn.execute(doc_sql, doc_params).fetchall()
        doc_meta = {row["path"]: row for row in doc_rows}

        results: list[SearchResult] = []
        for c in chunk_rows:
            meta = doc_meta.get(c["doc_path"])
            if meta is None:
                continue
            dist = rowid_to_distance.get(c["rowid"], 1.0)
            results.append(
                SearchResult(
                    source=meta["source"],
                    kind=meta["kind"],
                    path=c["doc_path"],
                    title=meta["title"],
                    snippet=None,
                    score=max(0.0, 1.0 - dist),
                    category=meta["category"],
                    created=meta["created"],
                    updated=meta["updated"],
                    chunk_index=c["chunk_index"],
                    start_line=c["start_line"],
                    end_line=c["end_line"],
                    type=None,
                    description=None,
                    source_ref=meta["source_ref"],
                    artifact_id=meta["artifact_id"],
                )
            )
        return results

    def _hybrid_merge(
        self,
        fts_chunks: list[SearchResult],
        vec_chunks: list[SearchResult],
    ) -> list[SearchResult]:
        """RRF on chunk-level lists; collapse to doc-level after fusion.

        Non-memory results (chunk_index=int) keyed by (path, chunk_index).
        After RRF, doc score = max of its chunks' RRF scores; winning chunk
        (highest per-key score) carries its snippet/chunk_index/start_line/end_line.
        k=60: Cormack 2009 standard.
        """
        k = 60

        # --- Non-memory leg: RRF keyed on (path, chunk_index) ---
        chunk_rrf: dict[_ChunkKey, float] = {}
        chunk_fts_by_key: dict[_ChunkKey, SearchResult] = {}
        chunk_vec_by_key: dict[_ChunkKey, SearchResult] = {}

        for i, r in enumerate(fts_chunks):
            key = (r.path, r.chunk_index)
            chunk_rrf[key] = chunk_rrf.get(key, 0.0) + 1.0 / (k + i + 1)
            chunk_fts_by_key[key] = r
        for j, r in enumerate(vec_chunks):
            key = (r.path, r.chunk_index)
            chunk_rrf[key] = chunk_rrf.get(key, 0.0) + 1.0 / (k + j + 1)
            if key not in chunk_fts_by_key:
                chunk_vec_by_key[key] = r

        # Accumulate doc scores; track winning chunk per doc
        doc_rrf: dict[str, float] = {}
        doc_winner_key: dict[str, _ChunkKey] = {}
        doc_winner_score: dict[str, float] = {}

        for key, score in chunk_rrf.items():
            path = key[0]
            doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)
            if path not in doc_winner_score or score > doc_winner_score[path]:
                doc_winner_key[path] = key
                doc_winner_score[path] = score

        chunk_merged: list[SearchResult] = []
        for path, total_score in doc_rrf.items():
            winner_key = doc_winner_key[path]
            base = chunk_fts_by_key.get(winner_key) or chunk_vec_by_key[winner_key]
            snippet = (
                chunk_fts_by_key[winner_key].snippet if winner_key in chunk_fts_by_key else None
            )
            fts_score = (
                chunk_fts_by_key[winner_key].score if winner_key in chunk_fts_by_key else None
            )
            vec_score = (
                chunk_vec_by_key[winner_key].score if winner_key in chunk_vec_by_key else None
            )
            logger.debug(
                "hybrid merge: path=%s chunk=%s rrf=%.4f bm25=%s cosine=%s",
                path,
                winner_key[1],
                total_score,
                f"{fts_score:.4f}" if fts_score is not None else "—",
                f"{vec_score:.4f}" if vec_score is not None else "—",
            )
            chunk_merged.append(dataclasses.replace(base, score=total_score, snippet=snippet))

        chunk_merged.sort(key=lambda r: r.score, reverse=True)
        return chunk_merged

    def _embed_cached(self, text: str) -> list[float] | None:
        """Return embedding for text, using cache. Returns None on provider failure."""
        content_hash = _sha256(text)
        row = self._conn.execute(
            "SELECT embedding FROM embedding_cache WHERE provider=? AND model=? AND content_hash=?",
            (self._embedding_provider, self._embedding_model, content_hash),
        ).fetchone()

        if row is not None:
            blob = row["embedding"]
            n = len(blob) // 4
            return list(struct.unpack(f"{n}f", blob))

        embedding = self._generate_embedding(text)
        if embedding is None:
            return None

        from datetime import datetime

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO embedding_cache(provider, model, content_hash, embedding, created) VALUES (?, ?, ?, ?, ?)",
            (
                self._embedding_provider,
                self._embedding_model,
                content_hash,
                blob,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return embedding

    def _generate_embedding(self, text: str) -> list[float] | None:
        """Call the configured embedding provider. Returns None on failure."""
        return self._embed_fn(text)

    def _rerank_results(
        self,
        query: str,
        candidates: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        """Rerank via TEI cross-encoder, or pass-through if TEI is not configured."""
        if self._reranker_provider == "none" or not candidates:
            return candidates[:limit]
        try:
            return self._tei_rerank(query, candidates, limit)
        except Exception as e:
            logger.warning(f"Reranking failed (tei), using unranked: {e}")
            return candidates[:limit]

    def _fetch_reranker_texts(self, candidates: list[SearchResult]) -> list[str]:
        chunk_texts: dict[tuple, str] = {}
        for r in candidates:
            row = self._conn.execute(
                "SELECT content FROM chunks WHERE source=? AND doc_path=? AND chunk_index=?",
                (r.source, r.path, r.chunk_index),
            ).fetchone()
            if row and row["content"]:
                chunk_texts[(r.source, r.path, r.chunk_index)] = row["content"]

        return [
            f"{r.title or ''}\n{chunk_texts.get((r.source, r.path, r.chunk_index), '')}".strip()
            or r.title
            or ""
            for r in candidates
        ]

    def _tei_rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        """Rerank candidates using cross-encoder API at self._cross_encoder_url/rerank."""
        texts = [t or "" for t in self._fetch_reranker_texts(candidates)]
        import httpx

        scored: dict[int, float] = {}
        for batch_start in range(0, len(texts), self._tei_batch_size):
            batch = texts[batch_start : batch_start + self._tei_batch_size]
            resp = httpx.post(
                f"{self._cross_encoder_url}/rerank",
                json={"query": query, "texts": batch},
                timeout=30.0,
            )
            resp.raise_for_status()
            for item in resp.json():
                scored[batch_start + item["index"]] = item["score"]

        reranked = [
            dataclasses.replace(candidates[i], score=scored.get(i, 0.0))
            for i in range(len(candidates))
        ]
        reranked.sort(key=lambda r: r.score, reverse=True)
        return reranked[:limit]

    def _build_fts_query(self, query: str) -> str | None:
        """Sanitize query for FTS5 MATCH. Returns None if nothing survives."""
        result = sanitize_fts5_query(query)
        return result if result else None

    def needs_reindex(self, source: str, path: str, current_hash: str) -> bool:
        """Return True if the file at path needs re-indexing (hash changed or absent)."""
        row = self._conn.execute(
            "SELECT hash FROM docs WHERE source = ? AND path = ?",
            (source, path),
        ).fetchone()
        if row is None:
            return True
        return row["hash"] != current_hash

    def sync_dir(
        self,
        source: str,
        directory: Path,
        glob: str = "**/*.md",
    ) -> int:
        """Incrementally index a directory of markdown files.

        Parses frontmatter for metadata. Uses SHA256 hash for change detection
        (only re-indexes changed files). Removes stale entries for deleted files.
        Every indexed file is also chunked into ``chunks_fts``.

        Args:
            source: Source label ('knowledge', 'obsidian', 'drive').
            directory: Directory to scan.
            glob: Glob pattern for files (default '**/*.md', recursive).

        Returns:
            Number of files indexed (new or changed).
        """
        if not directory.exists():
            return 0

        from co_cli.memory.chunker import chunk_text as _chunk_text

        current_paths: set[str] = set()
        indexed = 0

        for file_path in directory.glob(glob):
            path_str = str(file_path)
            current_paths.add(path_str)
            try:
                raw = file_path.read_text(encoding="utf-8")
                file_hash = _sha256(raw)

                if not self.needs_reindex(source, path_str, file_hash):
                    continue

                frontmatter, body = parse_frontmatter(raw)
                artifact_kind = frontmatter.get("artifact_kind") or frontmatter.get("kind")
                title = frontmatter.get("title") or file_path.stem
                mtime = file_path.stat().st_mtime

                self.index(
                    source=source,
                    kind=artifact_kind,
                    path=path_str,
                    title=title,
                    content=body.strip(),
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
                text_chunks = _chunk_text(
                    body.strip(),
                    chunk_size=self._chunk_size,
                    overlap=self._chunk_overlap,
                )
                self.index_chunks(source, path_str, text_chunks)
                indexed += 1
            except Exception as e:
                logger.warning(f"Failed to index {file_path}: {e}")

        self.remove_stale(source, current_paths, directory=directory)
        return indexed

    def index_session(self, session_path: Path) -> None:
        """Index a session JSONL into chunks/chunks_fts/chunks_vec.

        Idempotent — content hash skip avoids re-embedding unchanged sessions.
        Partial-write recovery: falls through hash-skip when chunks are absent.
        doc_path = uuid8 (8-char ID from the session filename).
        """
        from co_cli.memory.chunker import Chunk
        from co_cli.memory.session import parse_session_filename
        from co_cli.memory.session_chunker import chunk_session

        parsed = parse_session_filename(session_path.name)
        if parsed is None:
            logger.warning("Unrecognised session filename: %s", session_path.name)
            return
        uuid8, created_at = parsed

        sess_chunks = chunk_session(
            session_path,
            chunk_tokens=self._session_chunk_tokens,
            overlap_tokens=self._session_chunk_overlap,
        )
        if not sess_chunks:
            return

        full_text = "\n\n".join(c.text for c in sess_chunks)
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

        if not self.needs_reindex("session", uuid8, content_hash):
            chunk_count = self._conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE source='session' AND doc_path=?",
                (uuid8,),
            ).fetchone()[0]
            if chunk_count > 0:
                return  # hash-skip — content unchanged AND chunks present
            # else: partial-write recovery — fall through and re-index

        self.index(
            source="session",
            kind="session",
            path=uuid8,
            title=uuid8,
            content=full_text,
            mtime=session_path.stat().st_mtime,
            hash=content_hash,
            created=created_at.isoformat(),
            updated=created_at.isoformat(),
        )

        chunk_records = [
            Chunk(
                index=i,
                content=c.text,
                start_line=c.start_jsonl_line,
                end_line=c.end_jsonl_line,
            )
            for i, c in enumerate(sess_chunks)
        ]
        self.index_chunks(source="session", doc_path=uuid8, chunks=chunk_records)

    def sync_sessions(self, sessions_dir: Path, exclude: Path | None = None) -> int:
        """Incrementally index past sessions into the unified chunks pipeline.

        Iterates sessions_dir/*.jsonl, skips `exclude` (the active session),
        calls index_session per file, and removes stale entries for deleted sessions.

        Note: doc_path for sessions is uuid8, not a filesystem path, so
        remove_stale is called without a directory filter to avoid prefix mismatch.

        Returns:
            Number of sessions successfully processed.
        """
        from co_cli.memory.session import parse_session_filename

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

        # Remove stale session entries — no directory filter since doc_path=uuid8,
        # not a filesystem path. source='session' already scopes the deletion.
        self.remove_stale("session", current_uuid8s)
        return processed

    def remove(self, source: str, path: str) -> None:
        """Remove a single document from the index by path."""
        # Remove chunk rows first (rowid references must be cleaned before parent rows)
        self.remove_chunks(source, path)

        self._conn.execute(
            "DELETE FROM docs WHERE source = ? AND path = ?",
            (source, path),
        )
        self._conn.commit()

    def remove_stale(
        self,
        source: str,
        current_paths: set[str],
        directory: Path | None = None,
    ) -> int:
        """Remove index entries for paths that no longer exist.

        Args:
            source: Source label to scope removal.
            current_paths: Set of currently-existing path strings.
            directory: When provided, only consider entries whose path starts
                with str(directory). Prevents a subfolder scan from evicting
                entries that belong to sibling directories.

        Returns:
            Number of entries removed.
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

        # counts unique paths, not chunk rows — intentional
        to_delete = list({row["path"] for row in rows if row["path"] not in current_paths})
        if not to_delete:
            return 0

        for path in to_delete:
            # Remove chunk rows before docs rows
            self.remove_chunks(source, path)
            self._conn.execute(
                "DELETE FROM docs WHERE source = ? AND path = ?",
                (source, path),
            )
        self._conn.commit()
        return len(to_delete)

    def rebuild(
        self,
        source: str,
        directory: Path,
        glob: str = "**/*.md",
    ) -> int:
        """Wipe all entries for source and re-index from scratch.

        Args:
            source: Source label to rebuild.
            directory: Directory to re-index.
            glob: Glob pattern.

        Returns:
            Number of files indexed.
        """
        # Clean chunk rows before doc rows to avoid orphaned vec references
        if self._backend == "hybrid":
            chunk_rowids = [
                row[0]
                for row in self._conn.execute(
                    "SELECT rowid FROM chunks WHERE source = ?", (source,)
                ).fetchall()
            ]
            if chunk_rowids:
                placeholders = ",".join("?" * len(chunk_rowids))
                self._conn.execute(
                    f"DELETE FROM {self._chunks_vec_table} WHERE rowid IN ({placeholders})",
                    chunk_rowids,
                )
        self._conn.execute("DELETE FROM chunks WHERE source = ?", (source,))
        self._conn.execute("DELETE FROM docs WHERE source = ?", (source,))
        self._conn.commit()
        return self.sync_dir(source, directory, glob)

    def probe(self) -> None:
        """Run a minimal health check; raise on the first error found.

        Used by bootstrap to surface FTS/vector configuration problems early.
        Unlike search(), this method does NOT suppress errors.
        """
        self._conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1",
            ("probe",),
        ).fetchone()

        if self._backend == "hybrid":
            self._conn.execute(f"SELECT rowid FROM {self._chunks_vec_table} LIMIT 1").fetchone()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def _sha256(content: str) -> str:
    """SHA256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
