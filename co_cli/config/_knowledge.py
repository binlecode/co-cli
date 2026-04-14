"""Knowledge search, embedding, and chunking settings."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_KNOWLEDGE_SEARCH_BACKEND = "hybrid"
DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER = "tei"
DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "embeddinggemma"
DEFAULT_KNOWLEDGE_EMBEDDING_DIMS = 1024
DEFAULT_KNOWLEDGE_EMBED_API_URL = "http://127.0.0.1:8283"
DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL = "http://127.0.0.1:8282"
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 600
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 80
DEFAULT_TEI_RERANK_BATCH_SIZE = 50


class LlmModelSettings(BaseModel):
    """A model+provider bundle for auxiliary model references (e.g. LLM reranker)."""

    model: str
    api_params: dict[str, Any] = Field(default_factory=dict)
    provider: Literal["ollama-openai", "gemini"]


class KnowledgeSettings(BaseModel):
    """Knowledge search, embedding, and chunking settings."""

    model_config = ConfigDict(extra="ignore")

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
