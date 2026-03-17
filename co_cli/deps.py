from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.usage import RunUsage

from co_cli.config import (
    MCPServerConfig,
    ModelEntry,
    WebPolicy,
    DATA_DIR,
    SEARCH_DB,
    DEFAULT_DOOM_LOOP_THRESHOLD,
    DEFAULT_MAX_REFLECTIONS,
    DEFAULT_TOOL_OUTPUT_TRIM_CHARS,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_KNOWLEDGE_RERANKER_PROVIDER,
    DEFAULT_KNOWLEDGE_EMBED_API_URL,
    DEFAULT_KNOWLEDGE_RERANK_API_URL,
    DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER,
    DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
    DEFAULT_KNOWLEDGE_EMBEDDING_DIMS,
    DEFAULT_KNOWLEDGE_HYBRID_VECTOR_WEIGHT,
    DEFAULT_KNOWLEDGE_HYBRID_TEXT_WEIGHT,
    DEFAULT_KNOWLEDGE_RERANKER_MODEL,
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
    DEFAULT_LLM_HOST,
    DEFAULT_OLLAMA_NUM_CTX,
    DEFAULT_CTX_WARN_THRESHOLD,
    DEFAULT_CTX_OVERFLOW_THRESHOLD,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MAX_REQUEST_LIMIT,
    DEFAULT_TOOL_RETRIES,
    DEFAULT_SESSION_TTL_MINUTES,
    DEFAULT_BACKGROUND_MAX_CONCURRENT,
    DEFAULT_BACKGROUND_TASK_RETENTION_DAYS,
    DEFAULT_BACKGROUND_AUTO_CLEANUP,
    DEFAULT_BACKGROUND_TASK_INACTIVITY_TIMEOUT,
)
from co_cli.tools._shell_backend import ShellBackend

# CoConfig-specific path defaults (relative to cwd; overridden at runtime in create_deps())
DEFAULT_EXEC_APPROVALS_PATH = Path(".co-cli/exec-approvals.json")
DEFAULT_SKILLS_DIR = Path(".co-cli/skills")
DEFAULT_MEMORY_DIR = Path(".co-cli/memory")
DEFAULT_LIBRARY_DIR = Path(".co-cli/library")
DEFAULT_SESSION_PATH = Path(".co-cli/session.json")
DEFAULT_TASKS_DIR = Path(".co-cli/tasks")

if TYPE_CHECKING:
    from co_cli._model_factory import ModelRegistry
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
    workspace_root: Path = field(default_factory=Path.cwd)
    obsidian_vault_path: Path | None = None
    google_credentials_path: str | None = None
    shell_safe_commands: list[str] = field(default_factory=list)
    shell_max_timeout: int = DEFAULT_SHELL_MAX_TIMEOUT
    exec_approvals_path: Path = field(default_factory=lambda: DEFAULT_EXEC_APPROVALS_PATH)
    skills_dir: Path = field(default_factory=lambda: DEFAULT_SKILLS_DIR)
    memory_dir: Path = field(default_factory=lambda: DEFAULT_MEMORY_DIR)
    library_dir: Path = field(default_factory=lambda: DEFAULT_LIBRARY_DIR)
    # knowledge_db_path: global SQLite FTS/vec index (DATA_DIR, not workspace-relative).
    # Overridable so tests can use a tmp_path db without touching the real one.
    knowledge_db_path: Path = field(default_factory=lambda: SEARCH_DB)
    # session_path: stores session ID and compaction counter across REPL restarts.
    # Workspace-relative (.co-cli/session.json) so each project directory maintains
    # its own session — switching projects starts fresh context automatically.
    # Not tool-accessible; used only by the orchestration layer (_bootstrap, main).
    session_path: Path = field(default_factory=lambda: DEFAULT_SESSION_PATH)
    # tasks_dir: filesystem store for background task state (pending/running/done).
    # Workspace-relative so background tasks are scoped to the current project.
    tasks_dir: Path = field(default_factory=lambda: DEFAULT_TASKS_DIR)
    session_ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES
    background_max_concurrent: int = DEFAULT_BACKGROUND_MAX_CONCURRENT
    background_task_retention_days: int = DEFAULT_BACKGROUND_TASK_RETENTION_DAYS
    background_auto_cleanup: bool = DEFAULT_BACKGROUND_AUTO_CLEANUP
    background_task_inactivity_timeout: int = DEFAULT_BACKGROUND_TASK_INACTIVITY_TIMEOUT
    llm_api_key: str | None = None
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
    knowledge_embedding_provider: str = DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER
    knowledge_embedding_model: str = DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
    knowledge_embedding_dims: int = DEFAULT_KNOWLEDGE_EMBEDDING_DIMS
    knowledge_hybrid_vector_weight: float = DEFAULT_KNOWLEDGE_HYBRID_VECTOR_WEIGHT
    knowledge_hybrid_text_weight: float = DEFAULT_KNOWLEDGE_HYBRID_TEXT_WEIGHT
    knowledge_reranker_model: str = DEFAULT_KNOWLEDGE_RERANKER_MODEL
    theme: str = "light"
    mcp_count: int = 0
    mcp_servers: dict[str, "MCPServerConfig"] = field(default_factory=dict)
    role_models: dict[str, ModelEntry] = field(default_factory=dict)
    llm_host: str = DEFAULT_LLM_HOST
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_num_ctx: int = DEFAULT_OLLAMA_NUM_CTX
    ctx_warn_threshold: float = DEFAULT_CTX_WARN_THRESHOLD
    ctx_overflow_threshold: float = DEFAULT_CTX_OVERFLOW_THRESHOLD
    model_http_retries: int = DEFAULT_MODEL_HTTP_RETRIES
    max_request_limit: int = DEFAULT_MAX_REQUEST_LIMIT
    tool_retries: int = DEFAULT_TOOL_RETRIES

    @classmethod
    def from_settings(cls, s: "Settings") -> "CoConfig":
        """Construct a CoConfig from a Settings instance, copying all pure-copy fields.

        Computed/session fields (session_id, exec_approvals_path, skills_dir, memory_dir,
        session_path, tasks_dir, personality_critique, knowledge_search_backend, mcp_count)
        are left at dataclass defaults — callers override them via dataclasses.replace().
        """
        return cls(
            obsidian_vault_path=Path(s.obsidian_vault_path) if s.obsidian_vault_path else None,
            google_credentials_path=s.google_credentials_path,
            shell_safe_commands=list(s.shell_safe_commands),
            shell_max_timeout=s.shell_max_timeout,
            llm_api_key=s.llm_api_key,
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
            knowledge_embedding_provider=s.knowledge_embedding_provider,
            knowledge_embedding_model=s.knowledge_embedding_model,
            knowledge_embedding_dims=s.knowledge_embedding_dims,
            knowledge_hybrid_vector_weight=s.knowledge_hybrid_vector_weight,
            knowledge_hybrid_text_weight=s.knowledge_hybrid_text_weight,
            knowledge_reranker_model=s.knowledge_reranker_model,
            # library_dir: resolved here so tools and bootstrap always see the fully-resolved
            # path rather than the relative default. library_path is optional in Settings;
            # fall back to the XDG data dir when unset.
            library_dir=Path(s.library_path) if s.library_path else DATA_DIR / "library",
            theme=s.theme,
            role_models=dict(s.role_models),
            mcp_servers=dict(s.mcp_servers) if s.mcp_servers else {},
            llm_host=s.llm_host,
            llm_provider=s.llm_provider,
            llm_num_ctx=s.llm_num_ctx,
            ctx_warn_threshold=s.ctx_warn_threshold,
            ctx_overflow_threshold=s.ctx_overflow_threshold,
            model_http_retries=s.model_http_retries,
            max_request_limit=s.max_request_limit,
            tool_retries=s.tool_retries,
            session_ttl_minutes=s.session_ttl_minutes,
            background_max_concurrent=s.background_max_concurrent,
            background_task_retention_days=s.background_task_retention_days,
            background_auto_cleanup=s.background_auto_cleanup,
            background_task_inactivity_timeout=s.background_task_inactivity_timeout,
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
