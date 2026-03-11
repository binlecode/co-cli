from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.usage import RunUsage

from co_cli.config import (
    ModelEntry,
    WebPolicy,
    DEFAULT_DOOM_LOOP_THRESHOLD,
    DEFAULT_MAX_REFLECTIONS,
    DEFAULT_TOOL_OUTPUT_TRIM_CHARS,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_KNOWLEDGE_RERANKER_PROVIDER,
    DEFAULT_KNOWLEDGE_EMBED_API_URL,
    DEFAULT_KNOWLEDGE_RERANK_API_URL,
    DEFAULT_MEMORY_MAX_COUNT,
    DEFAULT_MEMORY_DEDUP_WINDOW_DAYS,
    DEFAULT_MEMORY_DEDUP_THRESHOLD,
    DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS,
    DEFAULT_MEMORY_CONSOLIDATION_TOP_K,
    DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_AUTO_SAVE_TAGS,
    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    DEFAULT_SHELL_MAX_TIMEOUT,
    DEFAULT_WEB_HTTP_MAX_RETRIES,
    DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS,
    DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS,
    DEFAULT_WEB_HTTP_JITTER_RATIO,
    DEFAULT_LLM_PROVIDER,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_NUM_CTX,
    DEFAULT_CTX_WARN_THRESHOLD,
    DEFAULT_CTX_OVERFLOW_THRESHOLD,
    DEFAULT_MODEL_HTTP_RETRIES,
)
from co_cli._shell_backend import ShellBackend

# CoConfig-specific path defaults (relative to cwd; overridden at runtime by main.py)
DEFAULT_EXEC_APPROVALS_PATH = Path(".co-cli/exec-approvals.json")
DEFAULT_SKILLS_DIR = Path(".co-cli/skills")
DEFAULT_MEMORY_DIR = Path(".co-cli/memory")
DEFAULT_LIBRARY_DIR = Path(".co-cli/library")

if TYPE_CHECKING:
    from co_cli.agents._factory import ModelRegistry
    from co_cli.config import Settings


@dataclass
class CoServices:
    """Injected service handles — shared across the session.

    Services are created once in main.py and shared by reference. Sub-agents
    receive the same handles since they are safe to share (shell, index, runner
    are all stateless or internally thread-safe).
    """

    shell: ShellBackend
    knowledge_index: Any | None = field(default=None, repr=False)
    task_runner: Any | None = field(default=None, repr=False)
    model_registry: "ModelRegistry | None" = field(default=None, repr=False)


@dataclass
class CoConfig:
    """Injected configuration — set at session bootstrap; may be updated during startup (e.g. backend resolution, model check).

    main.py reads Settings once and populates this dataclass with scalar values.
    Tools access ctx.deps.config.field_name. No tool should import Settings.
    """

    session_id: str = ""
    workspace_root: Path | None = None
    obsidian_vault_path: Path | None = None
    google_credentials_path: str | None = None
    shell_safe_commands: list[str] = field(default_factory=list)
    shell_max_timeout: int = DEFAULT_SHELL_MAX_TIMEOUT
    exec_approvals_path: Path = field(default_factory=lambda: DEFAULT_EXEC_APPROVALS_PATH)
    skills_dir: Path = field(default_factory=lambda: DEFAULT_SKILLS_DIR)
    memory_dir: Path = field(default_factory=lambda: DEFAULT_MEMORY_DIR)
    library_dir: Path = field(default_factory=lambda: DEFAULT_LIBRARY_DIR)
    gemini_api_key: str | None = None
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
    web_http_max_retries: int = DEFAULT_WEB_HTTP_MAX_RETRIES
    web_http_backoff_base_seconds: float = DEFAULT_WEB_HTTP_BACKOFF_BASE_SECONDS
    web_http_backoff_max_seconds: float = DEFAULT_WEB_HTTP_BACKOFF_MAX_SECONDS
    web_http_jitter_ratio: float = DEFAULT_WEB_HTTP_JITTER_RATIO
    memory_max_count: int = DEFAULT_MEMORY_MAX_COUNT
    memory_dedup_window_days: int = DEFAULT_MEMORY_DEDUP_WINDOW_DAYS
    memory_dedup_threshold: int = DEFAULT_MEMORY_DEDUP_THRESHOLD
    memory_recall_half_life_days: int = DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS
    memory_consolidation_top_k: int = DEFAULT_MEMORY_CONSOLIDATION_TOP_K
    memory_consolidation_timeout_seconds: int = DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS
    memory_auto_save_tags: list[str] = field(default_factory=lambda: list(DEFAULT_MEMORY_AUTO_SAVE_TAGS))
    knowledge_chunk_size: int = DEFAULT_KNOWLEDGE_CHUNK_SIZE
    knowledge_chunk_overlap: int = DEFAULT_KNOWLEDGE_CHUNK_OVERLAP
    personality: str | None = None
    personality_critique: str = ""
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    tool_output_trim_chars: int = DEFAULT_TOOL_OUTPUT_TRIM_CHARS
    doom_loop_threshold: int = DEFAULT_DOOM_LOOP_THRESHOLD
    max_reflections: int = DEFAULT_MAX_REFLECTIONS
    knowledge_search_backend: str = "fts5"
    knowledge_reranker_provider: str = DEFAULT_KNOWLEDGE_RERANKER_PROVIDER
    knowledge_embed_api_url: str = DEFAULT_KNOWLEDGE_EMBED_API_URL
    knowledge_rerank_api_url: str = DEFAULT_KNOWLEDGE_RERANK_API_URL
    mcp_count: int = 0
    role_models: dict[str, list[ModelEntry]] = field(default_factory=dict)
    ollama_host: str = DEFAULT_OLLAMA_HOST
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_num_ctx: int = DEFAULT_OLLAMA_NUM_CTX
    ctx_warn_threshold: float = DEFAULT_CTX_WARN_THRESHOLD
    ctx_overflow_threshold: float = DEFAULT_CTX_OVERFLOW_THRESHOLD
    model_http_retries: int = DEFAULT_MODEL_HTTP_RETRIES

    @classmethod
    def from_settings(cls, s: "Settings") -> "CoConfig":
        """Construct a CoConfig from a Settings instance, copying all pure-copy fields.

        Computed/session fields (session_id, exec_approvals_path, skills_dir, memory_dir,
        library_dir, personality_critique, knowledge_search_backend, mcp_count) are left
        at dataclass defaults — callers override them via dataclasses.replace().
        """
        return cls(
            obsidian_vault_path=Path(s.obsidian_vault_path) if s.obsidian_vault_path else None,
            google_credentials_path=s.google_credentials_path,
            shell_safe_commands=list(s.shell_safe_commands),
            shell_max_timeout=s.shell_max_timeout,
            gemini_api_key=s.gemini_api_key,
            brave_search_api_key=s.brave_search_api_key,
            web_fetch_allowed_domains=list(s.web_fetch_allowed_domains),
            web_fetch_blocked_domains=list(s.web_fetch_blocked_domains),
            web_policy=s.web_policy,
            web_http_max_retries=s.web_http_max_retries,
            web_http_backoff_base_seconds=s.web_http_backoff_base_seconds,
            web_http_backoff_max_seconds=s.web_http_backoff_max_seconds,
            web_http_jitter_ratio=s.web_http_jitter_ratio,
            memory_max_count=s.memory_max_count,
            memory_dedup_window_days=s.memory_dedup_window_days,
            memory_dedup_threshold=s.memory_dedup_threshold,
            memory_recall_half_life_days=s.memory_recall_half_life_days,
            memory_consolidation_top_k=s.memory_consolidation_top_k,
            memory_consolidation_timeout_seconds=s.memory_consolidation_timeout_seconds,
            memory_auto_save_tags=list(s.memory_auto_save_tags),
            knowledge_chunk_size=s.knowledge_chunk_size,
            knowledge_chunk_overlap=s.knowledge_chunk_overlap,
            personality=s.personality,
            max_history_messages=s.max_history_messages,
            tool_output_trim_chars=s.tool_output_trim_chars,
            doom_loop_threshold=s.doom_loop_threshold,
            max_reflections=s.max_reflections,
            knowledge_reranker_provider=s.knowledge_reranker_provider,
            knowledge_embed_api_url=s.knowledge_embed_api_url,
            knowledge_rerank_api_url=s.knowledge_rerank_api_url,
            role_models={k: list(v) for k, v in s.role_models.items()},
            ollama_host=s.ollama_host,
            llm_provider=s.llm_provider,
            llm_num_ctx=s.llm_num_ctx,
            ctx_warn_threshold=s.ctx_warn_threshold,
            ctx_overflow_threshold=s.ctx_overflow_threshold,
            model_http_retries=s.model_http_retries,
        )


@dataclass
class CoSessionState:
    """Mutable tool-visible session state.

    State here is readable and writable by tools and slash commands during the
    session. Sub-agents always receive a fresh instance — no inheritance.
    """

    google_creds: Any | None = field(default=None, repr=False)
    google_creds_resolved: bool = False
    session_tool_approvals: set[str] = field(default_factory=set)
    active_skill_env: dict[str, str] = field(default_factory=dict)
    skill_tool_grants: set[str] = field(default_factory=set)
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)
    session_todos: list[dict] = field(default_factory=list)
    skill_registry: list[dict] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    tool_approvals: dict[str, bool] = field(default_factory=dict)
    active_skill_name: str | None = None


@dataclass
class CoRuntimeState:
    """Mutable orchestration/processor transient state.

    State here is owned by the orchestration layer and history processors. Tools
    should not normally reach into runtime — doing so is a design smell that
    should be explicitly justified.
    """

    precomputed_compaction: Any = field(default=None, repr=False)
    turn_usage: RunUsage | None = None
    opening_ctx_state: Any = field(default=None, repr=False)
    safety_state: Any = field(default=None, repr=False)


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools — grouped by responsibility.

    Ownership rules:
      services  = injected capabilities (shell, knowledge index, task runner)
      config    = injected read-only settings (API keys, paths, limits, thresholds)
      session   = mutable tool-visible session state (creds, approvals, todos)
      runtime   = mutable orchestration/processor transient state

    pydantic-ai receives this as the single deps_type. Tools access fields via
    ctx.deps.services.shell, ctx.deps.config.memory_dir, etc.
    """

    services: CoServices
    config: CoConfig
    session: CoSessionState = field(default_factory=CoSessionState)
    runtime: CoRuntimeState = field(default_factory=CoRuntimeState)


def make_subagent_deps(base: "CoDeps") -> "CoDeps":
    """Create an isolated CoDeps copy for a sub-agent.

    Shares services and config by reference (safe — handles are stateless or
    thread-safe; config is read-only). Resets session and runtime to clean
    defaults so the sub-agent does not inherit the main agent's approval grants,
    pagination tokens, todos, compaction cache, or turn usage.
    """
    return CoDeps(
        services=base.services,
        config=base.config,
        session=CoSessionState(),
        runtime=CoRuntimeState(),
    )
