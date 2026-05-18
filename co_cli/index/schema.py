"""DDL constants for the index store.

Tables:
- docs            — one row per document with metadata
- chunks          — one row per chunk; FTS5 + vec rowid join key
- chunks_fts      — FTS5 virtual table mirroring chunks.content
- embedding_cache — content-hash keyed cached embeddings (provider, model)
- chunks_vec_{N}  — sqlite-vec virtual table (created lazily in hybrid mode)
"""

SCHEMA_SQL = """
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

FTS_SNIPPET_TOKENS = 40
"""Passed to FTS5 snippet() — context window for match highlighting."""

CHUNK_DEDUP_FETCH_MULTIPLIER = 20
"""Chunks fetched per requested doc — dedup by path collapses many chunks per doc."""

RERANKER_CANDIDATE_MULTIPLIER = 4
"""Reranker pool size — gives the reranker meaningful signal to reorder."""
