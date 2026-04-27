"""SQLite FTS5 knowledge index for ranked search across all text sources.

KnowledgeStore is a single SQLite-backed search index (``search.db``) that any
source writes to. The ``source`` column distinguishes origin; the ``kind`` /
``type`` columns hold the ``artifact_kind`` subtype for local artifacts.

Source namespace:
  source='knowledge' — reusable artifacts under ~/.co-cli/knowledge/
                       (preferences, rules, feedback, articles, notes, references)
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
from typing import TYPE_CHECKING, Any, Literal

from co_cli.config._core import SEARCH_DB
from co_cli.knowledge._frontmatter import parse_frontmatter
from co_cli.knowledge._stopwords import STOPWORDS

if TYPE_CHECKING:
    from co_cli.config._core import Settings

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
    content     TEXT,
    mtime       REAL,
    hash        TEXT,
    tags        TEXT,
    category    TEXT,
    created     TEXT,
    updated     TEXT,
    provenance  TEXT,
    certainty   TEXT,
    chunk_id    INTEGER DEFAULT 0,
    type        TEXT,
    description TEXT,
    source_ref  TEXT,
    artifact_id TEXT,
    UNIQUE(source, path, chunk_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    title,
    content,
    tags,
    tokenize='porter unicode61',
    content='docs',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON docs BEGIN
    INSERT INTO docs_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON docs BEGIN
    INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO docs_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

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
       d.kind, d.title, d.tags, d.category, d.created, d.updated, d.provenance, d.certainty,
       d.source_ref, d.artifact_id
  FROM chunks_fts
  JOIN chunks c ON c.rowid = chunks_fts.rowid
  JOIN docs d ON d.source = c.source AND d.path = c.doc_path AND d.chunk_id = 0
 WHERE chunks_fts MATCH ?
"""


def _coerce_sources(source: str | list[str] | None) -> list[str] | None:
    """Normalize source arg to list[str], or None meaning all sources."""
    if source is None:
        return None
    if isinstance(source, str):
        return [source]
    return list(source)


@dataclass
class SearchResult:
    """A single result from KnowledgeStore.search()."""

    source: str
    kind: str | None
    path: str
    title: str | None
    snippet: str | None
    score: float
    tags: str | None
    category: str | None
    created: str | None
    updated: str | None
    provenance: str | None = None
    certainty: str | None = None
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


class KnowledgeStore:
    """SQLite FTS5 index for ranked search across knowledge sources.

    Supports two backends:
      - 'fts5' (default): BM25 ranked full-text search only.
      - 'hybrid': FTS5 + sqlite-vec cosine vector search, RRF merge.

    Usage:
        idx = KnowledgeStore(config=Settings())
        idx.sync_dir("knowledge", knowledge_dir)
        results = idx.search("pytest testing")
        idx.close()
    """

    def __init__(self, *, config: "Settings", knowledge_db_path: Path | None = None) -> None:
        db_path = knowledge_db_path if knowledge_db_path is not None else SEARCH_DB
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
        self._docs_vec_table = f"docs_vec_{self._embedding_dims}"
        self._chunks_vec_table = f"chunks_vec_{self._embedding_dims}"
        self._llm_host = config.llm.host
        self._llm_api_key = config.llm.api_key
        self._embed_api_url = config.knowledge.embed_api_url
        self._cross_encoder_url = config.knowledge.cross_encoder_reranker_url
        self._tei_batch_size = config.knowledge.tei_rerank_batch_size
        self._llm_reranker = config.knowledge.llm_reranker
        self._chunk_size = config.knowledge.chunk_size
        self._chunk_overlap = (
            max(0, min(config.knowledge.chunk_overlap, config.knowledge.chunk_size - 1))
            if config.knowledge.chunk_size > 0
            else 0
        )

        # Determine effective reranker provider from new config fields:
        # cross-encoder (TEI) takes priority; LLM listwise as fallback; none if neither configured.
        if self._cross_encoder_url is not None:
            self._reranker_provider = "tei"
        elif self._llm_reranker is not None:
            _p = self._llm_reranker.provider
            self._reranker_provider = (
                "ollama" if _p == "ollama" else ("gemini" if _p == "gemini" else "none")
            )
        else:
            self._reranker_provider = "none"

        from co_cli.knowledge._embedder import build_embedder
        from co_cli.knowledge._reranker import build_llm_reranker

        self._embed_fn = build_embedder(
            self._embedding_provider,
            self._llm_host,
            self._embedding_model,
            self._embed_api_url,
            self._llm_api_key,
        )
        if self._llm_reranker is not None:
            _p = self._llm_reranker.provider
            _llm_rerank_provider = (
                "ollama" if _p == "ollama" else ("gemini" if _p == "gemini" else "none")
            )
            _llm_rerank_model = self._llm_reranker.model
        else:
            _llm_rerank_provider = "none"
            _llm_rerank_model = ""
        self._rerank_llm_fn = build_llm_reranker(
            _llm_rerank_provider,
            self._llm_host,
            _llm_rerank_model,
            self._llm_api_key,
        )

        self._conn = sqlite3.connect(str(self._db_path), timeout=5)  # type: ignore[attr-defined]  # pysqlite3 is a binary extension without type stubs
        self._conn.row_factory = sqlite3.Row  # type: ignore[attr-defined]  # pysqlite3 is a binary extension without type stubs
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

        try:
            _existing_cols = {
                row[1] for row in self._conn.execute("PRAGMA table_info(docs)").fetchall()
            }
            for _col in ("source_ref", "artifact_id"):
                if _col not in _existing_cols:
                    self._conn.execute(f"ALTER TABLE docs ADD COLUMN {_col} TEXT")
                    logger.info("KnowledgeStore: migrated docs table — added column %s", _col)
            self._conn.commit()
        except Exception as _e:
            self._conn.close()
            raise RuntimeError("KnowledgeStore: schema migration failed") from _e

        if self._backend == "hybrid":
            try:
                self._load_sqlite_vec()
                # Table names are dim-suffixed (e.g. docs_vec_1024) so a dim
                # change just creates a new table — old tables are left in place.
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._docs_vec_table}"
                    f" USING vec0(embedding float[{self._embedding_dims}])"
                )
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
        content: str | None = None,
        mtime: float | None = None,
        hash: str | None = None,
        tags: str | None = None,
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
            "SELECT hash FROM docs WHERE source = ? AND path = ? AND chunk_id = 0",
            (source, path),
        ).fetchone()

        if hash is not None and existing is not None and existing["hash"] == hash:
            return  # unchanged

        # Delete existing rows for this path
        self._conn.execute("DELETE FROM docs WHERE source = ? AND path = ?", (source, path))

        content_str = content or ""
        self._conn.execute(
            """INSERT INTO docs
                   (source, kind, path, title, content, mtime, hash, tags, category,
                    created, updated, type, description, source_ref, artifact_id, chunk_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                source,
                kind,
                path,
                title,
                content_str,
                mtime,
                hash,
                tags,
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

        if self._backend == "hybrid":
            text = f"{title or ''}\n{content or ''}"
            emb = self._embed_cached(text)
            if emb is not None:
                row = self._conn.execute(
                    "SELECT rowid FROM docs WHERE source=? AND path=? AND chunk_id=0",
                    (source, path),
                ).fetchone()
                if row:
                    self._conn.execute(
                        f"DELETE FROM {self._docs_vec_table} WHERE rowid=?", (row["rowid"],)
                    )
                    blob = struct.pack(f"{len(emb)}f", *emb)
                    self._conn.execute(
                        f"INSERT INTO {self._docs_vec_table}(rowid, embedding) VALUES (?, ?)",
                        (row["rowid"], blob),
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
            self._conn.execute(
                """INSERT INTO chunks (source, doc_path, chunk_index, content, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source, doc_path, chunk.index, chunk.content, chunk.start_line, chunk.end_line),
            )

        if self._backend == "hybrid":
            for chunk in chunks:
                emb = self._embed_cached(chunk.content or "")
                if emb is not None:
                    row = self._conn.execute(
                        "SELECT rowid FROM chunks WHERE source=? AND doc_path=? AND chunk_index=?",
                        (source, doc_path, chunk.index),
                    ).fetchone()
                    if row:
                        blob = struct.pack(f"{len(emb)}f", *emb)
                        self._conn.execute(
                            f"INSERT INTO {self._chunks_vec_table}(rowid, embedding) VALUES (?, ?)",
                            (row["rowid"], blob),
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
        source: str | list[str] | None = None,
        kind: str | None = None,
        tags: list[str] | None = None,
        tag_match_mode: Literal["any", "all"] = "any",
        created_after: str | None = None,
        created_before: str | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Search the index with BM25 ranking (FTS5) or hybrid BM25+vector.

        Returns an empty list when the query is empty, stopword-only, or
        produces no matches.

        In hybrid mode, falls back to FTS5 if the embedding provider fails.

        Source filter shortcuts:
          source="knowledge" → local knowledge artifacts
          source="obsidian"  → Obsidian vault notes only
          source="drive"     → Google Drive documents only
          source=["knowledge", "obsidian", "drive"] → multiple sources via IN-clause
        """
        if self._build_fts_query(query) is None:
            return []

        if self._backend == "hybrid":
            return self._hybrid_search(
                query,
                source=source,
                kind=kind,
                tags=tags,
                tag_match_mode=tag_match_mode,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
        # Fetch a larger candidate pool when reranker is active so it has
        # meaningful signal to reorder; otherwise fetch exactly what caller needs.
        fetch_limit = limit * 4 if self._reranker_provider != "none" else limit
        results = self._fts_search(
            query,
            source=source,
            kind=kind,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=fetch_limit,
        )
        return self._rerank_results(query, results, limit)

    def _hybrid_search(
        self,
        query: str,
        *,
        source: str | list[str] | None,
        kind: str | None,
        tags: list[str] | None,
        tag_match_mode: Literal["any", "all"],
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Hybrid BM25 + vector search with chunk-level RRF. Falls back to FTS5."""
        fts_chunks = self._fts_chunks_raw(
            query,
            source=source,
            kind=kind,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=limit * 4,
        )
        try:
            emb = self._embed_cached(query)
            if emb is not None:
                vec_chunks = self._vec_search(
                    emb,
                    source=source,
                    kind=kind,
                    tags=tags,
                    tag_match_mode=tag_match_mode,
                    created_after=created_after,
                    created_before=created_before,
                    limit=limit * 4,
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

    def _fts_search(
        self,
        query: str,
        *,
        source: str | list[str] | None,
        kind: str | None,
        tags: list[str] | None,
        tag_match_mode: Literal["any", "all"],
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """BM25 FTS5 search over chunks_fts — single leg for all sources."""
        fts_query = self._build_fts_query(query)
        if fts_query is None:
            return []

        tags = list(dict.fromkeys(tags)) if tags else tags
        # Chunks leg always uses a larger pool: one document produces N chunk rows,
        # so limiting at chunk granularity causes a single long article to crowd out
        # other matching documents before Python-side doc-level dedup can run.
        chunks_fetch_limit = limit * 20

        nonmem = _coerce_sources(source)
        all_rows: list[tuple[Any, str]] = self._run_chunks_fts(
            fts_query,
            sources=nonmem,
            kind=kind,
            created_after=created_after,
            created_before=created_before,
            limit=chunks_fetch_limit,
        )

        if tags:
            tag_set = set(tags)
            if tag_match_mode == "all":
                all_rows = [
                    (row, leg)
                    for row, leg in all_rows
                    if tag_set <= {t for t in (row["tags"] or "").split() if t}
                ]
            else:
                all_rows = [
                    (row, leg)
                    for row, leg in all_rows
                    if tag_set & {t for t in (row["tags"] or "").split() if t}
                ]

        results: list[SearchResult] = []
        for row, _leg in all_rows:
            rank = row["rank"]
            score = 1.0 / (1.0 + abs(rank))
            results.append(
                SearchResult(
                    source=row["source"],
                    kind=row["kind"],
                    path=row["path"],
                    title=row["title"],
                    snippet=row["snippet"],
                    score=score,
                    tags=row["tags"],
                    category=row["category"],
                    created=row["created"],
                    updated=row["updated"],
                    provenance=row["provenance"],
                    certainty=row["certainty"],
                    chunk_index=row["chunk_index"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    type=None,
                    description=None,
                    source_ref=row["source_ref"],
                    artifact_id=row["artifact_id"],
                )
            )

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
        kind: str | None,
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
        if kind is not None:
            sql += " AND d.kind = ?"
            params.append(kind)
        if created_after is not None:
            sql += " AND d.created >= ?"
            params.append(created_after)
        if created_before is not None:
            sql += " AND d.created <= ?"
            params.append(created_before)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:  # type: ignore[attr-defined]  # pysqlite3 is a binary extension without type stubs
            logger.warning(f"Chunks FTS search error: {e}")
            return []
        return [(row, "chunks") for row in rows]

    def _fts_chunks_raw(
        self,
        query: str,
        *,
        source: str | list[str] | None,
        kind: str | None,
        tags: list[str] | None,
        tag_match_mode: Literal["any", "all"],
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Return chunk-level FTS results for hybrid RRF (non-memory sources only)."""
        fts_query = self._build_fts_query(query)
        if fts_query is None:
            return []

        tags = list(dict.fromkeys(tags)) if tags else tags
        chunks_fetch_limit = limit * 20

        nonmem = _coerce_sources(source)
        chunk_rows = self._run_chunks_fts(
            fts_query,
            sources=nonmem,
            kind=kind,
            created_after=created_after,
            created_before=created_before,
            limit=chunks_fetch_limit,
        )

        if tags:
            tag_set = set(tags)
            predicate = (
                (lambda rl: tag_set <= {t for t in (rl[0]["tags"] or "").split() if t})
                if tag_match_mode == "all"
                else (lambda rl: tag_set & {t for t in (rl[0]["tags"] or "").split() if t})
            )
            chunk_rows = [rl for rl in chunk_rows if predicate(rl)]

        def _build(row: Any) -> SearchResult:
            rank = row["rank"]
            score = 1.0 / (1.0 + abs(rank))
            return SearchResult(
                source=row["source"],
                kind=row["kind"],
                path=row["path"],
                title=row["title"],
                snippet=row["snippet"],
                score=score,
                tags=row["tags"],
                category=row["category"],
                created=row["created"],
                updated=row["updated"],
                provenance=row["provenance"],
                certainty=row["certainty"],
                chunk_index=row["chunk_index"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                type=None,
                description=None,
                source_ref=row["source_ref"],
                artifact_id=row["artifact_id"],
            )

        return [_build(row) for row, _leg in chunk_rows]

    def _vec_search(
        self,
        embedding: list[float],
        *,
        source: str | list[str] | None,
        kind: str | None,
        tags: list[str] | None,
        tag_match_mode: Literal["any", "all"],
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Vector search against chunks_vec (non-memory sources only)."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        nonmem = _coerce_sources(source)
        return self._vec_chunks_search(
            blob,
            sources=nonmem,
            kind=kind,
            tags=tags,
            tag_match_mode=tag_match_mode,
            created_after=created_after,
            created_before=created_before,
            limit=limit * 4,
        )

    def _vec_chunks_search(
        self,
        blob: bytes,
        *,
        sources: list[str] | None,
        kind: str | None,
        tags: list[str] | None,
        tag_match_mode: Literal["any", "all"],
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
            f"SELECT source, kind, path, title, tags, category, created, updated, "
            f"provenance, certainty, type, description, source_ref, artifact_id"
            f" FROM docs WHERE path IN ({doc_ph}) AND chunk_id = 0"
        )
        doc_params: list[Any] = doc_paths
        if kind is not None:
            doc_sql += " AND kind = ?"
            doc_params.append(kind)
        if created_after is not None:
            doc_sql += " AND created >= ?"
            doc_params.append(created_after)
        if created_before is not None:
            doc_sql += " AND created <= ?"
            doc_params.append(created_before)

        doc_rows = self._conn.execute(doc_sql, doc_params).fetchall()
        if tags:
            tag_set = set(tags)
            if tag_match_mode == "all":
                doc_rows = [
                    r for r in doc_rows if tag_set <= {t for t in (r["tags"] or "").split() if t}
                ]
            else:
                doc_rows = [
                    r for r in doc_rows if tag_set & {t for t in (r["tags"] or "").split() if t}
                ]
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
                    tags=meta["tags"],
                    category=meta["category"],
                    created=meta["created"],
                    updated=meta["updated"],
                    provenance=meta["provenance"],
                    certainty=meta["certainty"],
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
        After RRF, doc score = sum of its chunks' RRF scores; winning chunk
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
            doc_rrf[path] = doc_rrf.get(path, 0.0) + score
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
        """Rerank candidates using the configured provider. Falls back on error."""
        if self._reranker_provider == "none" or not candidates:
            return candidates[:limit]
        try:
            if self._reranker_provider == "tei":
                return self._tei_rerank(query, candidates, limit)
            texts = self._fetch_reranker_texts(candidates)
            scores = self._generate_rerank_scores(query, texts)
            reranked = [dataclasses.replace(r, score=scores[i]) for i, r in enumerate(candidates)]
            reranked.sort(key=lambda r: r.score, reverse=True)
            return reranked[:limit]
        except Exception as e:
            logger.warning(f"Reranking failed ({self._reranker_provider}), using unranked: {e}")
            return candidates[:limit]

    def _fetch_reranker_texts(self, candidates: list[SearchResult]) -> list[str]:
        """Fetch relevant text for reranking.

        chunk_index is not None → fetch chunks.content (exact matching chunk text).
        chunk_index is None → fetch docs.content[:200] preamble (memory results).
        """
        doc_level = [r for r in candidates if r.chunk_index is None]
        chunk_level = [r for r in candidates if r.chunk_index is not None]

        # Batch-fetch doc preambles (memory / doc-level results)
        doc_texts: dict[str, str] = {}
        if doc_level:
            paths = [r.path for r in doc_level]
            ph = ",".join("?" * len(paths))
            rows = self._conn.execute(
                f"SELECT path, title, content FROM docs WHERE path IN ({ph}) AND chunk_id = 0",
                paths,
            ).fetchall()
            for row in rows:
                title = row["title"] or ""
                content = (row["content"] or "")[:200]
                doc_texts[row["path"]] = f"{title}\n{content}".strip()

        # Per-row chunk content fetch (candidate sets are <= limit*4, typically 20-40 rows)
        chunk_texts: dict[tuple, str] = {}
        for r in chunk_level:
            row = self._conn.execute(
                "SELECT content FROM chunks WHERE source=? AND doc_path=? AND chunk_index=?",
                (r.source, r.path, r.chunk_index),
            ).fetchone()
            if row and row["content"]:
                chunk_texts[(r.source, r.path, r.chunk_index)] = row["content"]

        texts: list[str] = []
        for r in candidates:
            if r.chunk_index is None:
                texts.append(doc_texts.get(r.path) or r.title or "")
            else:
                content = chunk_texts.get((r.source, r.path, r.chunk_index), "")
                texts.append(f"{r.title or ''}\n{content}".strip() if content else r.title or "")
        return texts

    def _generate_rerank_scores(self, query: str, texts: list[str]) -> list[float]:
        """Generate relevance scores for texts. Returns [0.0]*n for unknown provider."""
        if self._reranker_provider in ("ollama", "gemini"):
            return self._llm_rerank(query, texts)
        logger.warning(
            f"Unknown reranker provider {self._reranker_provider!r}; returning zero scores"
        )
        return [0.0] * len(texts)

    def _llm_rerank(self, query: str, texts: list[str]) -> list[float]:
        """LLM listwise rerank. Returns scores aligned to input candidate order."""
        n = len(texts)
        if n == 0:
            return []
        numbered = "\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        prompt = (
            f"Rank these documents by relevance to the query: '{query}'\n\n"
            f"Documents:\n{numbered}\n\n"
            f"Return a JSON array of document numbers in order from most to least relevant. "
            f"Include all {n} numbers. Example: [{', '.join(str(i + 1) for i in range(n))}]"
        )
        ranked_indices = self._call_reranker_llm(prompt, n)
        scores = [0.0] * n
        for rank, one_idx in enumerate(ranked_indices):
            idx = one_idx - 1
            if 0 <= idx < n:
                scores[idx] = 1.0 - rank / n
        return scores

    def _call_reranker_llm(self, prompt: str, n: int) -> list[int]:
        """Dispatch to provider-specific LLM and return list of 1-based ranked indices."""
        return self._rerank_llm_fn(prompt, n)

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
        """Tokenize query, filter stopwords, quote terms, AND-join.

        Returns None if no non-stopword tokens survive (e.g. "the a an").
        Quoted terms prevent FTS5 syntax injection from user input.
        Non-word characters (backticks, quotes, punctuation) are stripped from
        each token before quoting to prevent FTS5 phrase-string syntax errors.
        """
        tokens = []
        for raw in query.lower().split():
            # Strip characters that break FTS5 double-quoted phrase strings.
            # Keep word chars and hyphens; drop everything else (backticks, quotes, etc.)
            t = re.sub(r"[^\w-]", "", raw)
            if t and t not in STOPWORDS and len(t) > 1:
                tokens.append(t)
        if not tokens:
            return None
        return " AND ".join(f'"{t}"' for t in tokens)

    def needs_reindex(self, source: str, path: str, current_hash: str) -> bool:
        """Return True if the file at path needs re-indexing (hash changed or absent)."""
        row = self._conn.execute(
            "SELECT hash FROM docs WHERE source = ? AND path = ? AND chunk_id = 0",
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

        from co_cli.knowledge._chunker import chunk_text as _chunk_text

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

                fm, body = parse_frontmatter(raw)
                artifact_kind = fm.get("artifact_kind") or fm.get("kind")
                title = fm.get("title") or file_path.stem
                tags_list = fm.get("tags") or []
                tags_str = " ".join(tags_list) if tags_list else None
                mtime = file_path.stat().st_mtime

                self.index(
                    source=source,
                    kind=artifact_kind,
                    path=path_str,
                    title=title,
                    content=body.strip(),
                    mtime=mtime,
                    hash=file_hash,
                    tags=tags_str,
                    category=fm.get("auto_category"),
                    created=fm.get("created"),
                    updated=fm.get("updated"),
                    type=artifact_kind,
                    description=fm.get("description"),
                    source_ref=fm.get("source_ref"),
                    artifact_id=str(fm["id"]) if fm.get("id") is not None else None,
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

    def remove(self, source: str, path: str) -> None:
        """Remove a single document from the index by path.

        The docs_ad trigger fires on DELETE, handling docs_fts cleanup automatically.
        In hybrid mode, also cleans the docs_vec and chunks_vec entries.
        """
        # Remove chunk rows first (rowid references must be cleaned before parent rows)
        self.remove_chunks(source, path)

        if self._backend == "hybrid":
            row = self._conn.execute(
                "SELECT rowid FROM docs WHERE source=? AND path=?", (source, path)
            ).fetchone()
            if row:
                self._conn.execute(
                    f"DELETE FROM {self._docs_vec_table} WHERE rowid=?", (row["rowid"],)
                )
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
            if self._backend == "hybrid":
                row = self._conn.execute(
                    "SELECT rowid FROM docs WHERE source=? AND path=?", (source, path)
                ).fetchone()
                if row:
                    self._conn.execute(
                        f"DELETE FROM {self._docs_vec_table} WHERE rowid=?", (row["rowid"],)
                    )
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
        # Probe FTS: run a raw FTS match against docs_fts to verify the table is intact.
        self._conn.execute(
            "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? LIMIT 1",
            ("probe",),
        ).fetchone()

        # Probe vec tables: verify the dim-suffixed tables exist and are accessible.
        if self._backend == "hybrid":
            for table in (self._docs_vec_table, self._chunks_vec_table):
                self._conn.execute(f"SELECT rowid FROM {table} LIMIT 1").fetchone()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def _sha256(content: str) -> str:
    """SHA256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
