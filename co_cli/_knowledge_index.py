"""SQLite FTS5 knowledge index for ranked search across all text sources.

KnowledgeIndex is a single SQLite-backed search index (search.db) that any
source can write to. The `source` column distinguishes origin. The `kind`
column ('memory', 'article') distinguishes knowledge file types.

Source namespace:
  source='memory'   — kind:memory files (agent memories, lifecycle-managed)
  source='library'  — kind:article files (user-global saved references)
  source='obsidian' — Obsidian vault notes
  source='drive'    — Google Drive docs (indexed on read)

The index is derived and rebuildable — deleting search.db and restarting
rebuilds cleanly from files.

Phase 1: FTS5 BM25 ranking.
Phase 2: + sqlite-vec vector similarity. Enabled when backend='hybrid'.
         Falls back to FTS5-only if embedding provider is unavailable.
"""

import hashlib
import json
import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from co_cli._frontmatter import parse_frontmatter

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3

logger = logging.getLogger(__name__)

# Common English stopwords — tokens that survive FTS5 but add no signal
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "that", "this",
    "these", "those", "it", "its", "as", "up", "if", "so", "no", "not",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "they",
    "what", "which", "who", "how", "when", "where", "why",
})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS docs (
    source     TEXT NOT NULL,
    kind       TEXT,
    path       TEXT NOT NULL,
    title      TEXT,
    content    TEXT,
    mtime      REAL,
    hash       TEXT,
    tags       TEXT,
    category   TEXT,
    created    TEXT,
    updated    TEXT,
    provenance TEXT,
    certainty  TEXT,
    chunk_id   INTEGER DEFAULT 0,
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
"""

_SELECT_SQL = """
SELECT d.source, d.kind, d.path, d.title, d.tags, d.category, d.created, d.updated,
       d.provenance, d.certainty,
       snippet(docs_fts, 1, '>', '<', '...', 40) AS snippet,
       bm25(docs_fts) AS rank
  FROM docs_fts
  JOIN docs d ON d.rowid = docs_fts.rowid
 WHERE docs_fts MATCH ?
"""


@dataclass
class SearchResult:
    """A single result from KnowledgeIndex.search()."""
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


class KnowledgeIndex:
    """SQLite FTS5 index for ranked search across knowledge sources.

    Supports two backends:
      - 'fts5' (default): BM25 ranked full-text search only.
      - 'hybrid': FTS5 + sqlite-vec cosine vector search, weighted merge.

    Usage:
        idx = KnowledgeIndex(DATA_DIR / "search.db")
        idx.sync_dir("memory", knowledge_dir)
        results = idx.search("pytest testing")
        idx.close()
    """

    def __init__(
        self,
        db_path: Path,
        *,
        backend: str = "fts5",
        embedding_provider: str = "ollama",
        embedding_model: str = "embeddinggemma",
        embedding_dims: int = 256,
        ollama_host: str = "http://localhost:11434",
        gemini_api_key: str | None = None,
        hybrid_vector_weight: float = 0.7,
        hybrid_text_weight: float = 0.3,
        reranker_provider: str = "none",
        reranker_model: str = "",
        chunk_size: int = 600,
        chunk_overlap: int = 80,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._backend = backend
        self._embedding_provider = embedding_provider
        self._embedding_model = embedding_model
        self._embedding_dims = embedding_dims
        self._ollama_host = ollama_host
        self._gemini_api_key = gemini_api_key
        self._hybrid_vector_weight = hybrid_vector_weight
        self._hybrid_text_weight = hybrid_text_weight
        self._reranker_provider = reranker_provider
        self._reranker_model = reranker_model
        self._chunk_size = chunk_size
        self._chunk_overlap = max(0, min(chunk_overlap, chunk_size - 1)) if chunk_size > 0 else 0
        if not self._reranker_model:
            if self._reranker_provider == "gemini":
                self._reranker_model = "gemini-2.0-flash"
            elif self._reranker_provider == "local":
                self._reranker_model = "BAAI/bge-reranker-base"
            else:
                self._reranker_model = "qwen2.5:3b"
        self._reranker_instance: Any = None

        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

        # Safe migration: add provenance and certainty columns to existing databases.
        # SQLite does not support ADD COLUMN IF NOT EXISTS; catch OperationalError on duplicate.
        for col in ("provenance TEXT", "certainty TEXT"):
            try:
                self._conn.execute(f"ALTER TABLE docs ADD COLUMN {col}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        self._migrate_chunk_id()

        if self._backend == "hybrid":
            try:
                self._load_sqlite_vec()
                self._conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS docs_vec USING vec0(embedding float[{embedding_dims}])"
                )
                self._conn.commit()
            except Exception as e:
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

    def _migrate_chunk_id(self) -> None:
        """Rebuild docs table to add chunk_id + new UNIQUE(source, path, chunk_id) if absent."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(docs)").fetchall()}
        if "chunk_id" in cols:
            return

        self._conn.executescript("""
            DROP TRIGGER IF EXISTS docs_ai;
            DROP TRIGGER IF EXISTS docs_ad;
            DROP TRIGGER IF EXISTS docs_au;
            DROP TABLE IF EXISTS docs_fts;
            ALTER TABLE docs RENAME TO docs_old;
            CREATE TABLE docs (
                source TEXT NOT NULL, kind TEXT, path TEXT NOT NULL,
                title TEXT, content TEXT, mtime REAL, hash TEXT,
                tags TEXT, category TEXT, created TEXT, updated TEXT,
                provenance TEXT, certainty TEXT, chunk_id INTEGER DEFAULT 0,
                UNIQUE(source, path, chunk_id)
            );
            CREATE VIRTUAL TABLE docs_fts USING fts5(
                title, content, tags,
                tokenize='porter unicode61', content='docs', content_rowid='rowid'
            );
            CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
                INSERT INTO docs_fts(rowid, title, content, tags)
                VALUES (new.rowid, new.title, new.content, new.tags);
            END;
            CREATE TRIGGER docs_ad AFTER DELETE ON docs BEGIN
                INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
                VALUES ('delete', old.rowid, old.title, old.content, old.tags);
            END;
            CREATE TRIGGER docs_au AFTER UPDATE ON docs BEGIN
                INSERT INTO docs_fts(docs_fts, rowid, title, content, tags)
                VALUES ('delete', old.rowid, old.title, old.content, old.tags);
                INSERT INTO docs_fts(rowid, title, content, tags)
                VALUES (new.rowid, new.title, new.content, new.tags);
            END;
            INSERT INTO docs (source, kind, path, title, content, mtime, hash,
                              tags, category, created, updated, provenance, certainty, chunk_id)
                SELECT source, kind, path, title, content, mtime, hash,
                       tags, category, created, updated, provenance, certainty, 0
                FROM docs_old;
            DROP TABLE docs_old;
        """)

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
        provenance: str | None = None,
        certainty: str | None = None,
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

        # Delete existing rows for this path (handles re-indexing with different chunk count)
        self._conn.execute("DELETE FROM docs WHERE source = ? AND path = ?", (source, path))

        # Generate chunks
        content_str = content or ""
        if self._chunk_size > 0 and len(content_str) > self._chunk_size:
            step = self._chunk_size - self._chunk_overlap
            chunks = [content_str[i:i + self._chunk_size]
                      for i in range(0, len(content_str), step)]
        else:
            chunks = [content_str]

        for chunk_id, chunk_text in enumerate(chunks):
            self._conn.execute(
                """INSERT INTO docs
                       (source, kind, path, title, content, mtime, hash, tags, category,
                        created, updated, provenance, certainty, chunk_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, kind, path, title, chunk_text, mtime, hash, tags, category,
                 created, updated, provenance, certainty, chunk_id),
            )
        self._conn.commit()

        if self._backend == "hybrid":
            text = f"{title or ''}\n{content or ''}"
            emb = self._embed_cached(text)
            if emb is not None:
                row = self._conn.execute(
                    "SELECT rowid FROM docs WHERE source=? AND path=? AND chunk_id=0", (source, path)
                ).fetchone()
                if row:
                    self._conn.execute("DELETE FROM docs_vec WHERE rowid=?", (row["rowid"],))
                    blob = struct.pack(f"{len(emb)}f", *emb)
                    self._conn.execute(
                        "INSERT INTO docs_vec(rowid, embedding) VALUES (?, ?)",
                        (row["rowid"], blob),
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
          source="library"  → library articles only
          source="memory"   → memories only (explicit override, not the default)
          source=["library", "obsidian", "drive"] → multiple sources via IN-clause
        """
        if self._backend == "hybrid":
            return self._hybrid_search(
                query, source=source, kind=kind, tags=tags,
                tag_match_mode=tag_match_mode, created_after=created_after,
                created_before=created_before, limit=limit,
            )
        # Fetch a larger candidate pool when reranker is active so it has
        # meaningful signal to reorder; otherwise fetch exactly what caller needs.
        fetch_limit = limit * 4 if self._reranker_provider != "none" else limit
        results = self._fts_search(
            query, source=source, kind=kind, tags=tags,
            tag_match_mode=tag_match_mode, created_after=created_after,
            created_before=created_before, limit=fetch_limit,
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
        """Hybrid BM25 + vector search with weighted merge. Falls back to FTS5."""
        fts_results = self._fts_search(
            query, source=source, kind=kind, tags=tags,
            tag_match_mode=tag_match_mode, created_after=created_after,
            created_before=created_before, limit=limit * 4,
        )
        try:
            emb = self._embed_cached(query)
            if emb is not None:
                vec_results = self._vec_search(
                    emb, source=source, kind=kind, tags=tags,
                    tag_match_mode=tag_match_mode, created_after=created_after,
                    created_before=created_before, limit=limit * 4,
                )
                merged = self._hybrid_merge(
                    fts_results, vec_results,
                    self._hybrid_vector_weight, self._hybrid_text_weight,
                )
                # Deduplicate by path keeping highest score
                seen: dict[str, SearchResult] = {}
                for r in merged:
                    if r.path not in seen or r.score > seen[r.path].score:
                        seen[r.path] = r
                merged = sorted(seen.values(), key=lambda r: r.score, reverse=True)
                return self._rerank_results(query, merged, limit)
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to FTS: {e}")
        return fts_results[:limit]

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
        """BM25 FTS5 search. Returns empty list for stopword-only queries."""
        fts_query = self._build_fts_query(query)
        if fts_query is None:
            return []

        sql = _SELECT_SQL
        params: list[Any] = [fts_query]

        if isinstance(source, list):
            placeholders = ",".join("?" * len(source))
            sql += f" AND d.source IN ({placeholders})"
            params.extend(source)
        elif source is not None:
            sql += " AND d.source = ?"
            params.append(source)
        if kind is not None:
            sql += " AND d.kind = ?"
            params.append(kind)
        tags = list(dict.fromkeys(tags)) if tags else tags
        if created_after is not None:
            sql += " AND d.created >= ?"
            params.append(created_after)
        if created_before is not None:
            sql += " AND d.created <= ?"
            params.append(created_before)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit * 20 if tags else limit)

        try:
            rows = self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS search error for query '{query}': {e}")
            return []

        if tags:
            tag_set = set(tags)
            if tag_match_mode == "all":
                rows = [
                    r for r in rows
                    if tag_set <= {t for t in (r["tags"] or "").split() if t}
                ]
            else:  # "any"
                rows = [
                    r for r in rows
                    if tag_set & {t for t in (r["tags"] or "").split() if t}
                ]
            rows = rows[:limit]

        results = []
        for row in rows:
            rank = row["rank"]
            score = 1.0 / (1.0 + abs(rank))
            results.append(SearchResult(
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
            ))

        # Deduplicate by path keeping highest score (chunks produce multiple rows per doc)
        seen: dict[str, SearchResult] = {}
        for r in results:
            if r.path not in seen or r.score > seen[r.path].score:
                seen[r.path] = r
        results = list(seen.values())
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

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
        """Cosine vector search via sqlite-vec. Returns SearchResult list."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        vec_rows = self._conn.execute(
            "SELECT rowid, distance FROM docs_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (blob, limit * 4),
        ).fetchall()

        if not vec_rows:
            return []

        rowid_to_distance = {row["rowid"]: row["distance"] for row in vec_rows}
        rowids = list(rowid_to_distance.keys())

        placeholders = ",".join("?" * len(rowids))
        sql = f"SELECT rowid, source, kind, path, title, tags, category, created, updated, provenance, certainty FROM docs WHERE rowid IN ({placeholders})"
        params: list[Any] = list(rowids)

        if isinstance(source, list):
            src_placeholders = ",".join("?" * len(source))
            sql += f" AND source IN ({src_placeholders})"
            params.extend(source)
        elif source is not None:
            sql += " AND source = ?"
            params.append(source)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if created_after is not None:
            sql += " AND created >= ?"
            params.append(created_after)
        if created_before is not None:
            sql += " AND created <= ?"
            params.append(created_before)

        doc_rows = self._conn.execute(sql, params).fetchall()

        if tags:
            tag_set = set(tags)
            if tag_match_mode == "all":
                doc_rows = [
                    r for r in doc_rows
                    if tag_set <= {t for t in (r["tags"] or "").split() if t}
                ]
            else:
                doc_rows = [
                    r for r in doc_rows
                    if tag_set & {t for t in (r["tags"] or "").split() if t}
                ]

        results = []
        for row in doc_rows:
            distance = rowid_to_distance.get(row["rowid"], 1.0)
            score = max(0.0, 1.0 - distance)
            results.append(SearchResult(
                source=row["source"],
                kind=row["kind"],
                path=row["path"],
                title=row["title"],
                snippet=None,
                score=score,
                tags=row["tags"],
                category=row["category"],
                created=row["created"],
                updated=row["updated"],
                provenance=row["provenance"],
                certainty=row["certainty"],
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _hybrid_merge(
        self,
        fts: list[SearchResult],
        vec: list[SearchResult],
        vector_weight: float,
        text_weight: float,
    ) -> list[SearchResult]:
        """Weighted score merge of FTS and vector results, union by path."""
        by_path: dict[str, dict[str, Any]] = {}

        for r in fts:
            by_path[r.path] = {
                "result": r,
                "fts_score": r.score,
                "vec_score": 0.0,
            }

        for r in vec:
            if r.path in by_path:
                by_path[r.path]["vec_score"] = r.score
            else:
                by_path[r.path] = {
                    "result": r,
                    "fts_score": 0.0,
                    "vec_score": r.score,
                }

        merged = []
        for entry in by_path.values():
            combined = vector_weight * entry["vec_score"] + text_weight * entry["fts_score"]
            r = entry["result"]
            merged.append(SearchResult(
                source=r.source,
                kind=r.kind,
                path=r.path,
                title=r.title,
                # Prefer FTS snippet when available (vec results have no snippet)
                snippet=r.snippet if entry["fts_score"] > 0.0 else None,
                score=combined,
                tags=r.tags,
                category=r.category,
                created=r.created,
                updated=r.updated,
                provenance=r.provenance,
                certainty=r.certainty,
            ))

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged

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

        from datetime import datetime, timezone
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO embedding_cache(provider, model, content_hash, embedding, created) VALUES (?, ?, ?, ?, ?)",
            (self._embedding_provider, self._embedding_model, content_hash, blob,
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()
        return embedding

    def _generate_embedding(self, text: str) -> list[float] | None:
        """Call the configured embedding provider. Returns None on failure."""
        try:
            if self._embedding_provider == "ollama":
                import httpx
                resp = httpx.post(
                    f"{self._ollama_host}/api/embed",
                    json={"model": self._embedding_model, "input": text},
                    timeout=30.0,
                )
                resp.raise_for_status()
                return resp.json()["embeddings"][0]

            if self._embedding_provider == "gemini":
                from google import genai
                client = genai.Client(api_key=self._gemini_api_key)
                result = client.models.embed_content(
                    model=self._embedding_model,
                    contents=text,
                )
                return result.embeddings[0].values

            # provider == "none" or unknown
            return None

        except Exception as e:
            logger.warning(f"Embedding generation failed ({self._embedding_provider}): {e}")
            return None

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
            if self._reranker_provider == "local":
                return self._local_cross_encoder_rerank(query, candidates, limit)
            texts = self._fetch_reranker_texts(candidates)
            scores = self._generate_rerank_scores(query, texts)
            reranked = [
                SearchResult(
                    source=r.source,
                    kind=r.kind,
                    path=r.path,
                    title=r.title,
                    snippet=r.snippet,
                    score=scores[i],
                    tags=r.tags,
                    category=r.category,
                    created=r.created,
                    updated=r.updated,
                    provenance=r.provenance,
                    certainty=r.certainty,
                )
                for i, r in enumerate(candidates)
            ]
            reranked.sort(key=lambda r: r.score, reverse=True)
            return reranked[:limit]
        except Exception as e:
            logger.warning(f"Reranking failed ({self._reranker_provider}), using unranked: {e}")
            return candidates[:limit]

    def _fetch_reranker_texts(self, candidates: list[SearchResult]) -> list[str]:
        """Fetch title+content snippets for reranking from the DB."""
        paths = [r.path for r in candidates]
        placeholders = ",".join("?" * len(paths))
        rows = self._conn.execute(
            f"SELECT path, title, content FROM docs WHERE path IN ({placeholders}) AND chunk_id = 0",
            paths,
        ).fetchall()
        by_path = {row["path"]: row for row in rows}
        texts = []
        for r in candidates:
            row = by_path.get(r.path)
            if row:
                title = row["title"] or ""
                content = (row["content"] or "")[:200]
                texts.append(f"{title}\n{content}".strip())
            else:
                texts.append(r.title or "")
        return texts

    def _generate_rerank_scores(self, query: str, texts: list[str]) -> list[float]:
        """Generate relevance scores for texts. Returns [0.0]*n for unknown provider."""
        if self._reranker_provider in ("ollama", "gemini"):
            return self._llm_rerank(query, texts)
        logger.warning(f"Unknown reranker provider {self._reranker_provider!r}; returning zero scores")
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
        if self._reranker_provider == "ollama":
            return self._ollama_generate_ranked(prompt, n)
        if self._reranker_provider == "gemini":
            return self._gemini_generate_ranked(prompt, n)
        return list(range(1, n + 1))

    def _parse_ranked_indices(self, parsed: Any, n: int) -> list[int]:
        """Extract a list of integer indices from JSON output.

        Handles both plain arrays and objects where models wrap the list in a key
        (e.g. {"ranking": [2, 1]} or {"relevancy_order": [2, 1]}).
        Falls back to identity order when nothing parseable is found.
        """
        if isinstance(parsed, list):
            ints = [int(x) for x in parsed if isinstance(x, (int, float))]
            if ints:
                return ints
        if isinstance(parsed, dict):
            # Try well-known keys first, then any list-valued key
            for key in ("ranking", "relevancy_order", "order", "result", "indices", "ranked"):
                val = parsed.get(key)
                if isinstance(val, list):
                    ints = [int(x) for x in val if isinstance(x, (int, float))]
                    if ints:
                        return ints
            for val in parsed.values():
                if isinstance(val, list) and all(isinstance(x, (int, float)) for x in val):
                    return [int(x) for x in val]
        return list(range(1, n + 1))

    def _ollama_generate_ranked(self, prompt: str, n: int) -> list[int]:
        """Call Ollama /api/generate with JSON format, return 1-based ranked indices."""
        import httpx
        resp = httpx.post(
            f"{self._ollama_host}/api/generate",
            json={"model": self._reranker_model, "prompt": prompt, "format": "json", "stream": False},
            timeout=60.0,
        )
        resp.raise_for_status()
        raw = resp.json()["response"]
        return self._parse_ranked_indices(json.loads(raw), n)

    def _gemini_generate_ranked(self, prompt: str, n: int) -> list[int]:
        """Call Gemini with JSON response type, return 1-based ranked indices."""
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=self._gemini_api_key)
        response = client.models.generate_content(
            model=self._reranker_model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return self._parse_ranked_indices(json.loads(response.text), n)

    def _local_cross_encoder_rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        """ONNX cross-encoder reranking via fastembed. Falls back gracefully."""
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError:
            logger.warning("fastembed not installed; falling back to unranked results (uv sync --group reranker)")
            return candidates[:limit]

        texts = self._fetch_reranker_texts(candidates)
        if self._reranker_instance is None:
            self._reranker_instance = TextCrossEncoder(model_name=self._reranker_model)

        scores = list(self._reranker_instance.rerank(query, texts))
        ranked = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [
            SearchResult(
                source=r.source,
                kind=r.kind,
                path=r.path,
                title=r.title,
                snippet=r.snippet,
                score=s,
                tags=r.tags,
                category=r.category,
                created=r.created,
                updated=r.updated,
                provenance=r.provenance,
                certainty=r.certainty,
            )
            for s, r in ranked[:limit]
        ]

    def _build_fts_query(self, query: str) -> str | None:
        """Tokenize query, filter stopwords, quote terms, AND-join.

        Returns None if no non-stopword tokens survive (e.g. "the a an").
        Quoted terms prevent FTS5 syntax injection from user input.
        """
        tokens = [
            t for t in query.lower().split()
            if t and t not in STOPWORDS and len(t) > 1
        ]
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
        kind_filter: str | None = None,
    ) -> int:
        """Incrementally index a directory of markdown files.

        Parses frontmatter for metadata. Uses SHA256 hash for change detection
        (only re-indexes changed files). Removes stale entries for deleted files.

        Args:
            source: Source label ('memory', 'library', 'obsidian', 'drive').
            directory: Directory to scan.
            glob: Glob pattern for files (default '**/*.md', recursive).
            kind_filter: When set, only index files whose frontmatter kind matches.
                         When None, all files are indexed regardless of kind.

        Returns:
            Number of files indexed (new or changed).
        """
        if not directory.exists():
            return 0

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
                kind = fm.get("kind", "memory")
                if kind_filter is not None and kind != kind_filter:
                    continue
                title = fm.get("title") or file_path.stem
                tags_list = fm.get("tags") or []
                tags_str = " ".join(tags_list) if tags_list else None
                mtime = file_path.stat().st_mtime

                self.index(
                    source=source,
                    kind=kind,
                    path=path_str,
                    title=title,
                    content=body.strip(),
                    mtime=mtime,
                    hash=file_hash,
                    tags=tags_str,
                    category=fm.get("auto_category"),
                    created=fm.get("created"),
                    updated=fm.get("updated"),
                    provenance=fm.get("provenance"),
                    certainty=fm.get("certainty"),
                )
                indexed += 1
            except Exception as e:
                logger.warning(f"Failed to index {file_path}: {e}")

        self.remove_stale(source, current_paths, directory=directory)
        return indexed

    def remove(self, source: str, path: str) -> None:
        """Remove a single document from the index by path.

        The docs_ad trigger fires on DELETE, handling docs_fts cleanup automatically.
        In hybrid mode, also cleans the docs_vec entry.
        """
        if self._backend == "hybrid":
            row = self._conn.execute(
                "SELECT rowid FROM docs WHERE source=? AND path=?", (source, path)
            ).fetchone()
            if row:
                self._conn.execute("DELETE FROM docs_vec WHERE rowid=?", (row["rowid"],))
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
            rows = [r for r in rows if r["path"].startswith(dir_prefix + "/") or r["path"] == dir_prefix]

        # counts unique paths, not chunk rows — intentional
        to_delete = list({row["path"] for row in rows if row["path"] not in current_paths})
        if not to_delete:
            return 0

        for path in to_delete:
            if self._backend == "hybrid":
                row = self._conn.execute(
                    "SELECT rowid FROM docs WHERE source=? AND path=?", (source, path)
                ).fetchone()
                if row:
                    self._conn.execute("DELETE FROM docs_vec WHERE rowid=?", (row["rowid"],))
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
        self._conn.execute("DELETE FROM docs WHERE source = ?", (source,))
        self._conn.commit()
        return self.sync_dir(source, directory, glob)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def _sha256(content: str) -> str:
    """SHA256 hex digest of a string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
