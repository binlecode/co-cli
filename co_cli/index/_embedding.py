"""Embedding service — embed text with provider dispatch + content-hash cache.

The cache lives in the same SQLite DB as the index (``embedding_cache`` table),
but is owned conceptually by this service. Domain code never touches it
directly.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from co_cli.index._circuit import CircuitBreaker

logger = logging.getLogger(__name__)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EmbeddingService:
    """Embed text and cache embeddings keyed by (provider, model, content_hash).

    Construction is pure (no network). First embedding call attempts the provider.
    Returns ``None`` on provider failure — callers must handle None.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        embed_fn: Callable[[str], list[float] | None],
        conn: Any,
    ) -> None:
        self._provider = provider
        self._model = model
        self._embed_fn = embed_fn
        self._conn = conn
        self._breaker: CircuitBreaker | None = CircuitBreaker() if provider != "none" else None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def embed(self, text: str) -> list[float] | None:
        """Return embedding for text, hitting the cache when possible."""
        content_hash = _sha256(text)
        row = self._conn.execute(
            "SELECT embedding FROM embedding_cache "
            "WHERE provider=? AND model=? AND content_hash=?",
            (self._provider, self._model, content_hash),
        ).fetchone()

        if row is not None:
            blob = row["embedding"]
            n = len(blob) // 4
            return list(struct.unpack(f"{n}f", blob))

        if self._breaker is not None and self._breaker.is_open():
            logger.debug("embed circuit breaker open — skipping %s call", self._provider)
            return None

        try:
            embedding = self._embed_fn(text)
        except Exception as e:
            if self._breaker is not None:
                self._breaker.on_failure(e)
            logger.warning("Embedding generation failed (%s): %s", self._provider, e)
            return None

        if embedding is None:
            return None

        if self._breaker is not None:
            self._breaker.on_success()

        blob = struct.pack(f"{len(embedding)}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO embedding_cache"
            "(provider, model, content_hash, embedding, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                self._provider,
                self._model,
                content_hash,
                blob,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return embedding

    def pack(self, embedding: list[float]) -> bytes:
        """Pack a float vector into the binary blob format used by sqlite-vec."""
        return struct.pack(f"{len(embedding)}f", *embedding)
