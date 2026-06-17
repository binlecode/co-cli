"""Retrieval service — FTS5 + sqlite-vec + RRF + TEI rerank.

Pure read-path orchestration. Reads from the index tables (docs/chunks/
chunks_fts/chunks_vec) and the embedding service. Never writes.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any

from co_cli.index._circuit import CircuitBreaker
from co_cli.index._embedding import EmbeddingService
from co_cli.index.schema import (
    CHUNK_DEDUP_FETCH_MULTIPLIER,
    FTS_CANDIDATE_MULTIPLIER,
    FTS_SNIPPET_TOKENS,
    VECTOR_CANDIDATE_MULTIPLIER,
)
from co_cli.index.search_util import (
    kind_clause,
    normalize_bm25,
    run_fts,
    sanitize_fts5_query,
    source_clause,
)
from co_cli.observability.tracing import current_span

logger = logging.getLogger(__name__)

_CHUNKS_FTS_SQL = f"""
SELECT c.source, c.doc_path AS path,
       snippet(chunks_fts, 0, '>', '<', '...', {FTS_SNIPPET_TOKENS}) AS snippet,
       bm25(chunks_fts) AS rank,
       c.chunk_index, c.start_line, c.end_line,
       d.kind, d.title, d.category, d.created_at, d.updated_at,
       d.description, d.source_ref, d.artifact_id
  FROM chunks_fts
  JOIN chunks c ON c.rowid = chunks_fts.rowid
  JOIN docs d ON d.source = c.source AND d.path = c.doc_path
 WHERE chunks_fts MATCH ?
"""


@dataclass
class SearchResult:
    """A single ranked result returned by RetrievalService.search()."""

    source: str
    kind: str | None
    path: str
    title: str | None
    snippet: str | None
    score: float
    category: str | None
    created_at: str | None
    updated_at: str | None
    chunk_index: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    description: str | None = None
    source_ref: str | None = None
    artifact_id: str | None = None


type _ChunkKey = tuple[str, int | None]


def _dedup_by_path(results: list[SearchResult]) -> list[SearchResult]:
    """Collapse chunk-level results to doc-level (max score per path, desc)."""
    seen: dict[str, SearchResult] = {}
    for r in results:
        if r.path not in seen or r.score > seen[r.path].score:
            seen[r.path] = r
    return sorted(seen.values(), key=lambda r: r.score, reverse=True)


def _chunk_row_to_result(row: Any) -> SearchResult:
    return SearchResult(
        source=row["source"],
        kind=row["kind"],
        path=row["path"],
        title=row["title"],
        snippet=row["snippet"],
        score=normalize_bm25(row["rank"]),
        category=row["category"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        chunk_index=row["chunk_index"],
        start_line=row["start_line"],
        end_line=row["end_line"],
        description=row["description"],
        source_ref=row["source_ref"],
        artifact_id=row["artifact_id"],
    )


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
    """LIKE fallback for chunks FTS — same column shape as _CHUNKS_FTS_SQL."""
    like_conds = " OR ".join("c.content LIKE ?" for _ in tokens)
    like_params = [f"%{t}%" for t in tokens]

    case_exprs = " + ".join("CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in tokens)
    rank_expr = f"-({case_exprs})" if tokens else "-1.0"

    lp: list[Any] = list(like_params) + list(like_params)

    lsql = (
        "SELECT c.source, c.doc_path AS path,"
        f" substr(c.content, 1, 300) AS snippet, {rank_expr} AS rank,"
        " c.chunk_index, c.start_line, c.end_line,"
        " d.kind, d.title, d.category, d.created_at, d.updated_at,"
        " d.description, d.source_ref, d.artifact_id"
        " FROM chunks c"
        " JOIN docs d ON d.source = c.source AND d.path = c.doc_path"
        f" WHERE ({like_conds})"
    )
    src_sql, src_params = source_clause(sources, "c.source")
    lsql += src_sql
    lp.extend(src_params)
    k_sql, k_params = kind_clause(kinds, "d.kind")
    lsql += k_sql
    lp.extend(k_params)
    if created_after is not None:
        lsql += " AND d.created_at >= ?"
        lp.append(created_after)
    if created_before is not None:
        lsql += " AND d.created_at <= ?"
        lp.append(created_before)
    lsql += " LIMIT ?"
    lp.append(limit)
    try:
        return conn.execute(lsql, lp).fetchall()
    except Exception:
        return []


class RetrievalService:
    """Read-only search service over the index tables.

    Composes:
      - SQLite connection (read access to docs/chunks/chunks_fts/chunks_vec)
      - EmbeddingService (query embedding in hybrid mode)
      - Optional TEI cross-encoder reranker
    """

    def __init__(
        self,
        *,
        conn: Any,
        backend: str,
        vec_table: str | None,
        embedding: EmbeddingService | None,
        cross_encoder_url: str | None,
        tei_batch_size: int,
        rerank_text_char_budget: int,
        vector_similarity_floor: float,
        rerank_score_floor: float,
    ) -> None:
        self._conn = conn
        self._backend = backend
        self._vec_table = vec_table
        self._embedding = embedding
        self._cross_encoder_url = cross_encoder_url
        self._tei_batch_size = tei_batch_size
        self._rerank_text_char_budget = rerank_text_char_budget
        self._vector_similarity_floor = vector_similarity_floor
        self._rerank_score_floor = rerank_score_floor
        self._reranker_provider = "tei" if cross_encoder_url is not None else "none"
        self._rerank_breaker: CircuitBreaker | None = (
            CircuitBreaker() if cross_encoder_url is not None else None
        )

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
        """Ranked search over the index. Empty / stopword-only queries return []."""
        fts_query = sanitize_fts5_query(query)
        if not fts_query:
            return []

        if self._backend == "hybrid":
            return self._hybrid_search(
                fts_query,
                sources=sources,
                kinds=kinds,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
            )
        fetch_limit = (
            limit * FTS_CANDIDATE_MULTIPLIER if self._reranker_provider != "none" else limit
        )
        results = self._fts_search(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=fetch_limit,
        )
        return self._rerank(query, results, limit)

    def _hybrid_search(
        self,
        fts_query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        """Hybrid BM25 + vector with chunk-level RRF; falls back to FTS5 on vec error."""
        fts_chunks = self._fts_chunks_raw(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            fetch_limit=limit * FTS_CANDIDATE_MULTIPLIER,
        )
        try:
            if self._embedding is not None:
                emb = self._embedding.embed(fts_query)
                if emb is not None:
                    vec_chunks = self._vec_chunks_search(
                        self._embedding.pack(emb),
                        sources=sources,
                        kinds=kinds,
                        created_after=created_after,
                        created_before=created_before,
                        limit=limit * VECTOR_CANDIDATE_MULTIPLIER,
                    )
                    merged = self._hybrid_merge(fts_chunks, vec_chunks)
                    return self._rerank(fts_query, merged, limit)
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to FTS: {e}")

        current_span().add_event(
            "index.hybrid_degraded_to_fts",
            {"co.index.backend": self._backend},
        )
        return self._rerank(fts_query, _dedup_by_path(fts_chunks), limit)

    def _fts_search(
        self,
        fts_query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[SearchResult]:
        chunks_fetch_limit = limit * CHUNK_DEDUP_FETCH_MULTIPLIER
        rows = self._run_chunks_fts(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=chunks_fetch_limit,
        )
        results = [_chunk_row_to_result(row) for row in rows]
        return _dedup_by_path(results)[:limit]

    def _run_chunks_fts(
        self,
        fts_query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        limit: int,
    ) -> list[Any]:
        if sources is not None and not sources:
            return []
        sql = _CHUNKS_FTS_SQL
        params: list[Any] = [fts_query]
        src_sql, src_params = source_clause(sources, "c.source")
        sql += src_sql
        params.extend(src_params)
        k_sql, k_params = kind_clause(kinds, "d.kind")
        sql += k_sql
        params.extend(k_params)
        if created_after is not None:
            sql += " AND d.created_at >= ?"
            params.append(created_after)
        if created_before is not None:
            sql += " AND d.created_at <= ?"
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

        return run_fts(self._conn, sql, params, label="Chunks FTS", like_fallback=_like_fallback)

    def _fts_chunks_raw(
        self,
        fts_query: str,
        *,
        sources: list[str] | None,
        kinds: list[str] | None,
        created_after: str | None,
        created_before: str | None,
        fetch_limit: int,
    ) -> list[SearchResult]:
        rows = self._run_chunks_fts(
            fts_query,
            sources=sources,
            kinds=kinds,
            created_after=created_after,
            created_before=created_before,
            limit=fetch_limit,
        )
        return [_chunk_row_to_result(row) for row in rows]

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
        if self._vec_table is None:
            return []
        vec_rows = self._conn.execute(
            f"SELECT rowid, distance FROM {self._vec_table} "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (blob, limit),
        ).fetchall()
        if not vec_rows:
            return []

        rowid_to_distance = {row["rowid"]: row["distance"] for row in vec_rows}
        rowids = list(rowid_to_distance.keys())
        placeholders = ",".join("?" * len(rowids))
        chunk_sql = (
            "SELECT rowid, source, doc_path, chunk_index, start_line, end_line"
            f" FROM chunks WHERE rowid IN ({placeholders})"
        )
        chunk_params: list[Any] = list(rowids)
        src_sql, src_params = source_clause(sources, "source")
        chunk_sql += src_sql
        chunk_params.extend(src_params)

        chunk_rows = self._conn.execute(chunk_sql, chunk_params).fetchall()
        if not chunk_rows:
            return []

        doc_paths = list({row["doc_path"] for row in chunk_rows})
        doc_ph = ",".join("?" * len(doc_paths))
        doc_sql = (
            "SELECT source, kind, path, title, category, created_at, updated_at,"
            " type, description, source_ref, artifact_id"
            f" FROM docs WHERE path IN ({doc_ph})"
        )
        doc_params: list[Any] = doc_paths
        k_sql, k_params = kind_clause(kinds)
        doc_sql += k_sql
        doc_params.extend(k_params)
        if created_after is not None:
            doc_sql += " AND created_at >= ?"
            doc_params.append(created_after)
        if created_before is not None:
            doc_sql += " AND created_at <= ?"
            doc_params.append(created_before)

        doc_rows = self._conn.execute(doc_sql, doc_params).fetchall()
        doc_meta = {row["path"]: row for row in doc_rows}

        results: list[SearchResult] = []
        for c in chunk_rows:
            meta = doc_meta.get(c["doc_path"])
            if meta is None:
                continue
            dist = rowid_to_distance.get(c["rowid"], 1.0)
            similarity = max(0.0, 1.0 - dist)
            if similarity < self._vector_similarity_floor:
                continue
            results.append(
                SearchResult(
                    source=meta["source"],
                    kind=meta["kind"],
                    path=c["doc_path"],
                    title=meta["title"],
                    snippet=None,
                    score=similarity,
                    category=meta["category"],
                    created_at=meta["created_at"],
                    updated_at=meta["updated_at"],
                    chunk_index=c["chunk_index"],
                    start_line=c["start_line"],
                    end_line=c["end_line"],
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

        k=60 follows Cormack 2009. Doc score = max of its chunks' RRF scores;
        the winning chunk (highest per-key score) carries snippet/line info.
        """
        k = 60

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

        doc_rrf: dict[str, float] = {}
        doc_winner_key: dict[str, _ChunkKey] = {}
        doc_winner_score: dict[str, float] = {}

        for key, score in chunk_rrf.items():
            path = key[0]
            doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)
            if path not in doc_winner_score or score > doc_winner_score[path]:
                doc_winner_key[path] = key
                doc_winner_score[path] = score

        merged: list[SearchResult] = []
        for path, total_score in doc_rrf.items():
            winner_key = doc_winner_key[path]
            base = chunk_fts_by_key.get(winner_key) or chunk_vec_by_key[winner_key]
            snippet = (
                chunk_fts_by_key[winner_key].snippet if winner_key in chunk_fts_by_key else None
            )
            merged.append(dataclasses.replace(base, score=total_score, snippet=snippet))

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged

    def _rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        limit: int,
    ) -> list[SearchResult]:
        if self._reranker_provider == "none" or not candidates:
            return candidates[:limit]
        if self._rerank_breaker is not None and self._rerank_breaker.is_open():
            logger.debug("rerank circuit breaker open — skipping TEI call")
            return candidates[:limit]
        try:
            result = self._tei_rerank(query, candidates, limit)
            if self._rerank_breaker is not None:
                self._rerank_breaker.on_success()
            return [r for r in result if r.score >= self._rerank_score_floor]
        except Exception as e:
            if self._rerank_breaker is not None:
                self._rerank_breaker.on_failure(e)
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
                chunk_texts[(r.source, r.path, r.chunk_index)] = row["content"][
                    : self._rerank_text_char_budget
                ]

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
