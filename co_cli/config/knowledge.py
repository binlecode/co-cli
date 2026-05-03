"""Knowledge search, embedding, chunking, and lifecycle settings."""

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_KNOWLEDGE_SEARCH_BACKEND = "hybrid"
DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER = "tei"
DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024
DEFAULT_KNOWLEDGE_EMBED_API_URL = "http://127.0.0.1:8283"
DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL = "http://127.0.0.1:8282"
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 600
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 80
DEFAULT_TEI_RERANK_BATCH_SIZE = 50


class KnowledgeSettings(BaseSettings):
    """Knowledge search, embedding, and chunking settings."""

    model_config = SettingsConfigDict(
        extra="forbid",
        env_prefix="CO_KNOWLEDGE_",
        env_nested_delimiter="__",
    )

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
    embed_api_url: str = Field(default=DEFAULT_KNOWLEDGE_EMBED_API_URL)
    chunk_size: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_SIZE, ge=0)
    chunk_overlap: int = Field(default=DEFAULT_KNOWLEDGE_CHUNK_OVERLAP, ge=0)
    consolidation_enabled: bool = Field(default=False)
    consolidation_trigger: Literal["session_end", "manual"] = Field(default="session_end")
    consolidation_lookback_sessions: int = Field(default=5, ge=1)
    consolidation_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    max_artifact_count: int = Field(default=300, ge=1)
    decay_after_days: int = Field(default=90, ge=1)
    # Deprecated: superseded by _ARTIFACTS_CANON_CAP in tools/memory/recall.py. Config key retained for one version; not consumed by recall.
    character_recall_limit: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            "CO_KNOWLEDGE_CHARACTER_RECALL_LIMIT",
            "CO_CHARACTER_RECALL_LIMIT",
            "character_recall_limit",
        ),
    )
    session_chunk_tokens: int = Field(default=400, ge=64)
    session_chunk_overlap: int = Field(default=80, ge=0)

    @classmethod
    def settings_customise_sources(cls, settings_cls, **kwargs) -> tuple:
        # env vars take priority over init kwargs (JSON config) — preserves existing contract.
        return (kwargs["env_settings"], kwargs["init_settings"])
