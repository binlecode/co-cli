"""Knowledge search, embedding, chunking, and lifecycle settings."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_KNOWLEDGE_SEARCH_BACKEND = "hybrid"
DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER = "tei"
DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024
DEFAULT_KNOWLEDGE_EMBED_API_URL = "http://127.0.0.1:8283"
DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL = "http://127.0.0.1:8282"
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 600
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 80
DEFAULT_TEI_RERANK_BATCH_SIZE = 50


KNOWLEDGE_ENV_MAP: dict[str, str] = {
    "search_backend": "CO_KNOWLEDGE_SEARCH_BACKEND",
    "embedding_provider": "CO_KNOWLEDGE_EMBEDDING_PROVIDER",
    "embedding_model": "CO_KNOWLEDGE_EMBEDDING_MODEL",
    "embedding_dims": "CO_KNOWLEDGE_EMBEDDING_DIMS",
    "cross_encoder_reranker_url": "CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL",
    "embed_api_url": "CO_KNOWLEDGE_EMBED_API_URL",
    "chunk_size": "CO_KNOWLEDGE_CHUNK_SIZE",
    "chunk_overlap": "CO_KNOWLEDGE_CHUNK_OVERLAP",
    "consolidation_enabled": "CO_KNOWLEDGE_CONSOLIDATION_ENABLED",
    "decay_after_days": "CO_KNOWLEDGE_DECAY_AFTER_DAYS",
    "character_recall_limit": "CO_CHARACTER_RECALL_LIMIT",
    "session_chunk_tokens": "CO_KNOWLEDGE_SESSION_CHUNK_TOKENS",
    "session_chunk_overlap": "CO_KNOWLEDGE_SESSION_CHUNK_OVERLAP",
}


# Default reranker model per provider — single source of truth, overridable via settings.json.
_RERANKER_DEFAULT_MODEL: dict[str, str] = {
    "gemini": "gemini-3.1-flash-preview",
    "ollama": "qwen2.5:3b",
}


class LlmModelSettings(BaseModel):
    """A model+provider bundle for auxiliary model references (e.g. LLM reranker)."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="")
    api_params: dict[str, Any] = Field(default_factory=dict)
    provider: Literal["ollama", "gemini"]

    @model_validator(mode="after")
    def _fill_model_default(self) -> "LlmModelSettings":
        if not self.model:
            self.model = _RERANKER_DEFAULT_MODEL.get(self.provider, "")
        return self


class KnowledgeSettings(BaseModel):
    """Knowledge search, embedding, and chunking settings."""

    model_config = ConfigDict(extra="forbid")

    search_backend: Literal["grep", "fts5", "hybrid"] = Field(
        default=DEFAULT_KNOWLEDGE_SEARCH_BACKEND
    )
    embedding_provider: Literal["ollama", "gemini", "tei", "none"] = Field(
        default=DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER
    )
    embedding_model: str = Field(default=DEFAULT_KNOWLEDGE_EMBEDDING_MODEL)
    embedding_dims: int = Field(default=DEFAULT_KNOWLEDGE_EMBEDDING_DIMS, ge=1)
    cross_encoder_reranker_url: str | None = Field(
        default=DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL
    )
    tei_rerank_batch_size: int = Field(default=DEFAULT_TEI_RERANK_BATCH_SIZE)
    llm_reranker: LlmModelSettings | None = Field(default=None)
    embed_api_url: str = Field(default=DEFAULT_KNOWLEDGE_EMBED_API_URL)
    chunk_size: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_SIZE, ge=0)
    chunk_overlap: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_OVERLAP, ge=0)
    consolidation_enabled: bool = Field(default=False)
    consolidation_trigger: Literal["session_end", "manual"] = Field(default="session_end")
    consolidation_lookback_sessions: int = Field(default=5, ge=1)
    consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    max_artifact_count: int = Field(default=300, ge=1)
    decay_after_days: int = Field(default=90, ge=1)
    character_recall_limit: int = Field(default=3, ge=1)
    session_chunk_tokens: int = Field(default=400, ge=64)
    session_chunk_overlap: int = Field(default=80, ge=0)
