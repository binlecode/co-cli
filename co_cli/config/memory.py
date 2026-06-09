"""Memory tier settings — search backend, embedding, chunking, lifecycle."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MEMORY_SEARCH_BACKEND = "hybrid"
DEFAULT_MEMORY_EMBEDDING_PROVIDER = "tei"
DEFAULT_MEMORY_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_MEMORY_EMBEDDING_DIMS = 1024
DEFAULT_MEMORY_EMBED_API_URL = "http://127.0.0.1:8283"
DEFAULT_MEMORY_CROSS_ENCODER_RERANKER_URL = "http://127.0.0.1:8282"
DEFAULT_MEMORY_CHUNK_TOKENS = 600
DEFAULT_MEMORY_CHUNK_OVERLAP_TOKENS = 80
DEFAULT_TEI_RERANK_BATCH_SIZE = 50


MEMORY_ENV_MAP: dict[str, str] = {
    "search_backend": "CO_MEMORY_SEARCH_BACKEND",
    "embedding_provider": "CO_MEMORY_EMBEDDING_PROVIDER",
    "embedding_model": "CO_MEMORY_EMBEDDING_MODEL",
    "embedding_dims": "CO_MEMORY_EMBEDDING_DIMS",
    "cross_encoder_reranker_url": "CO_MEMORY_CROSS_ENCODER_RERANKER_URL",
    "tei_rerank_batch_size": "CO_MEMORY_TEI_RERANK_BATCH_SIZE",
    "embed_api_url": "CO_MEMORY_EMBED_API_URL",
    "chunk_tokens": "CO_MEMORY_CHUNK_TOKENS",
    "chunk_overlap_tokens": "CO_MEMORY_CHUNK_OVERLAP_TOKENS",
    "consolidation_similarity_threshold": "CO_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD",
    "decay_after_days": "CO_MEMORY_DECAY_AFTER_DAYS",
    "recall_protection_days": "CO_MEMORY_RECALL_PROTECTION_DAYS",
}


class MemorySettings(BaseModel):
    """Memory tier settings: search, embedding, chunking, lifecycle."""

    model_config = ConfigDict(extra="forbid")

    search_backend: Literal["grep", "fts5", "hybrid"] = Field(
        default=DEFAULT_MEMORY_SEARCH_BACKEND
    )
    embedding_provider: Literal["ollama", "gemini", "tei", "none"] = Field(
        default=DEFAULT_MEMORY_EMBEDDING_PROVIDER
    )
    embedding_model: str = Field(default=DEFAULT_MEMORY_EMBEDDING_MODEL)
    embedding_dims: int = Field(default=DEFAULT_MEMORY_EMBEDDING_DIMS, ge=1)
    cross_encoder_reranker_url: str | None = Field(
        default=DEFAULT_MEMORY_CROSS_ENCODER_RERANKER_URL
    )
    tei_rerank_batch_size: int = Field(default=DEFAULT_TEI_RERANK_BATCH_SIZE)
    embed_api_url: str = Field(default=DEFAULT_MEMORY_EMBED_API_URL)
    chunk_tokens: int = Field(default=DEFAULT_MEMORY_CHUNK_TOKENS, ge=0)
    chunk_overlap_tokens: int = Field(default=DEFAULT_MEMORY_CHUNK_OVERLAP_TOKENS, ge=0)
    consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    decay_after_days: int = Field(default=90, ge=1)
    recall_protection_days: int = Field(default=30, ge=1)
