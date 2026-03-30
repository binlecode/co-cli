import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pydantic_ai.usage import RunUsage

from co_cli.config import (
    MCPServerConfig,
    ModelConfig,
    WebPolicy,
    DATA_DIR,
    SEARCH_DB,
    DEFAULT_DOOM_LOOP_THRESHOLD,
    DEFAULT_MAX_REFLECTIONS,
    DEFAULT_TOOL_OUTPUT_TRIM_CHARS,
    DEFAULT_MAX_HISTORY_MESSAGES,
    DEFAULT_KNOWLEDGE_SEARCH_BACKEND,
    DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL,
    DEFAULT_KNOWLEDGE_EMBED_API_URL,
    DEFAULT_SUBAGENT_SCOPE_CHARS,
    DEFAULT_SUBAGENT_MAX_REQUESTS_CODER,
    DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH,
    DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS,
    DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING,
    DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER,
    DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
    DEFAULT_KNOWLEDGE_EMBEDDING_DIMS,
    DEFAULT_MEMORY_MAX_COUNT,
    DEFAULT_MEMORY_DEDUP_WINDOW_DAYS,
    DEFAULT_MEMORY_DEDUP_THRESHOLD,
    DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS,
    DEFAULT_MEMORY_CONSOLIDATION_TOP_K,
    DEFAULT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_AUTO_SAVE_TAGS,
    DEFAULT_MEMORY_INJECTION_MAX_CHARS,
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
    DEFAULT_TOOL_RETRIES,
    DEFAULT_SESSION_TTL_MINUTES,
    DEFAULT_BACKGROUND_MAX_CONCURRENT,
    DEFAULT_BACKGROUND_TASK_RETENTION_DAYS,
    DEFAULT_BACKGROUND_AUTO_CLEANUP,
    DEFAULT_BACKGROUND_TASK_INACTIVITY_TIMEOUT,
    DEFAULT_REASONING_DISPLAY,
)
from co_cli.tools._shell_backend import ShellBackend

# CoConfig-specific path defaults (relative to cwd; overridden at runtime in create_deps())
DEFAULT_SKILLS_DIR = Path(".co-cli/skills")
DEFAULT_MEMORY_DIR = Path(".co-cli/memory")
DEFAULT_LIBRARY_DIR = Path(".co-cli/library")
DEFAULT_SESSION_PATH = Path(".co-cli/session.json")
DEFAULT_TASKS_DIR = Path(".co-cli/tasks")

from co_cli.commands._skill_types import SkillConfig
from co_cli.context._types import CompactionResult, MemoryRecallState, SafetyState

if TYPE_CHECKING:
    from pydantic_ai import Agent, DeferredToolRequests
    from co_cli._model_factory import ModelRegistry
    from co_cli.config import Settings


class ApprovalKindEnum(str, Enum):
    """Category of an approval subject — determines the scoping key stored in SessionApprovalRule."""

    SHELL = "shell"
    PATH = "path"
    DOMAIN = "domain"
    TOOL = "tool"


@dataclass(frozen=True)
class SessionApprovalRule:
    """A session-scoped approval rule recorded when the user chooses 'a'.

    kind: category of the approved subject
      SHELL  — shell utility (value = first token, e.g. "git")
      PATH   — file write/edit (value = bare parent_dir, e.g. "/proj/src");
               shared across write_file and edit_file for the same directory
      DOMAIN — web fetch (value = hostname, e.g. "docs.python.org")
      TOOL   — named tool (value = tool_name, e.g. "save_memory");
               covers MCP tools and generic tools; can_remember=True
    value: the scoped key used for matching future requests
    """

    kind: ApprovalKindEnum
    value: str


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
    task_agent: "Agent[CoDeps, str | DeferredToolRequests] | None" = field(default=None, repr=False)


@dataclass(frozen=True)
class CoConfig:
    """Injected configuration — set at session bootstrap; may be updated during startup (e.g. backend resolution, model check).

    main.py reads Settings once and populates this dataclass with scalar values.
    Tools access ctx.deps.config.field_name. No tool should import Settings.
    """

    workspace_root: Path = field(default_factory=Path.cwd)
    obsidian_vault_path: Path | None = None
    google_credentials_path: str | None = None
    shell_safe_commands: list[str] = field(default_factory=list)
    shell_max_timeout: int = DEFAULT_SHELL_MAX_TIMEOUT
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
    memory_injection_max_chars: int = DEFAULT_MEMORY_INJECTION_MAX_CHARS
    subagent_scope_chars: int = DEFAULT_SUBAGENT_SCOPE_CHARS
    subagent_max_requests_coder: int = DEFAULT_SUBAGENT_MAX_REQUESTS_CODER
    subagent_max_requests_research: int = DEFAULT_SUBAGENT_MAX_REQUESTS_RESEARCH
    subagent_max_requests_analysis: int = DEFAULT_SUBAGENT_MAX_REQUESTS_ANALYSIS
    subagent_max_requests_thinking: int = DEFAULT_SUBAGENT_MAX_REQUESTS_THINKING
    knowledge_chunk_size: int = DEFAULT_KNOWLEDGE_CHUNK_SIZE
    knowledge_chunk_overlap: int = DEFAULT_KNOWLEDGE_CHUNK_OVERLAP
    personality: str | None = None
    system_prompt: str = ""
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    tool_output_trim_chars: int = DEFAULT_TOOL_OUTPUT_TRIM_CHARS
    doom_loop_threshold: int = DEFAULT_DOOM_LOOP_THRESHOLD
    max_reflections: int = DEFAULT_MAX_REFLECTIONS
    knowledge_search_backend: str = DEFAULT_KNOWLEDGE_SEARCH_BACKEND
    knowledge_cross_encoder_reranker_url: str | None = DEFAULT_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL
    knowledge_llm_reranker: "ModelConfig | None" = None
    knowledge_embed_api_url: str = DEFAULT_KNOWLEDGE_EMBED_API_URL
    knowledge_embedding_provider: str = DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER
    knowledge_embedding_model: str = DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
    knowledge_embedding_dims: int = DEFAULT_KNOWLEDGE_EMBEDDING_DIMS
    theme: str = "light"
    reasoning_display: str = DEFAULT_REASONING_DISPLAY
    mcp_servers: dict[str, "MCPServerConfig"] = field(default_factory=dict)
    role_models: dict[str, ModelConfig] = field(default_factory=dict)
    llm_host: str = DEFAULT_LLM_HOST
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_num_ctx: int = DEFAULT_OLLAMA_NUM_CTX
    ctx_warn_threshold: float = DEFAULT_CTX_WARN_THRESHOLD
    ctx_overflow_threshold: float = DEFAULT_CTX_OVERFLOW_THRESHOLD
    model_http_retries: int = DEFAULT_MODEL_HTTP_RETRIES
    tool_retries: int = DEFAULT_TOOL_RETRIES

    def uses_ollama_openai(self) -> bool:
        """Return True when the session LLM backend is Ollama's OpenAI-compatible API."""
        return self.llm_provider == "ollama-openai"

    def uses_gemini(self) -> bool:
        """Return True when the session LLM backend is Gemini."""
        return self.llm_provider == "gemini"

    def supports_context_ratio_tracking(self) -> bool:
        """Return True when input/output usage can be compared against an Ollama context budget."""
        return self.uses_ollama_openai() and self.llm_num_ctx > 0

    @classmethod
    def from_settings(cls, s: "Settings", *, cwd: Path) -> "CoConfig":
        """Construct a fully resolved CoConfig from a Settings instance and current working directory.

        All cwd-relative paths and settings fields are resolved in a single call.
        """
        # Resolve MCP server env tokens before construction (frozen=True prevents post-init mutation)
        resolved_servers: dict[str, "MCPServerConfig"] = {}
        for name, srv_cfg in (s.mcp_servers or {}).items():
            if name == "github":
                env = dict(srv_cfg.env) if srv_cfg.env else {}
                if "GITHUB_PERSONAL_ACCESS_TOKEN" not in env:
                    token = os.getenv("GITHUB_TOKEN_BINLECODE", "")
                    if token:
                        env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
                        # TODO(token-env): generalize when MCPServerConfig gains a token_env field
                srv_cfg = srv_cfg.model_copy(update={"env": env})
            resolved_servers[name] = srv_cfg
        return cls(
            workspace_root=cwd,
            obsidian_vault_path=Path(s.obsidian_vault_path) if s.obsidian_vault_path else None,
            google_credentials_path=s.google_credentials_path,
            shell_safe_commands=list(s.shell_safe_commands),
            shell_max_timeout=s.shell_max_timeout,
            memory_dir=cwd / ".co-cli" / "memory",
            skills_dir=cwd / ".co-cli" / "skills",
            session_path=cwd / ".co-cli" / "session.json",
            tasks_dir=cwd / ".co-cli" / "tasks",
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
            memory_injection_max_chars=s.memory_injection_max_chars,
            subagent_scope_chars=s.subagent_scope_chars,
            subagent_max_requests_coder=s.subagent_max_requests_coder,
            subagent_max_requests_research=s.subagent_max_requests_research,
            subagent_max_requests_analysis=s.subagent_max_requests_analysis,
            subagent_max_requests_thinking=s.subagent_max_requests_thinking,
            knowledge_chunk_size=s.knowledge_chunk_size,
            knowledge_chunk_overlap=s.knowledge_chunk_overlap,
            personality=s.personality,
            knowledge_search_backend=s.knowledge_search_backend,
            max_history_messages=s.max_history_messages,
            tool_output_trim_chars=s.tool_output_trim_chars,
            doom_loop_threshold=s.doom_loop_threshold,
            max_reflections=s.max_reflections,
            knowledge_cross_encoder_reranker_url=s.knowledge_cross_encoder_reranker_url,
            knowledge_llm_reranker=s.knowledge_llm_reranker,
            knowledge_embed_api_url=s.knowledge_embed_api_url,
            knowledge_embedding_provider=s.knowledge_embedding_provider,
            knowledge_embedding_model=s.knowledge_embedding_model,
            knowledge_embedding_dims=s.knowledge_embedding_dims,
            # library_dir: resolved here so tools and bootstrap always see the fully-resolved
            # path rather than the relative default. library_path is optional in Settings;
            # fall back to the XDG data dir when unset.
            library_dir=Path(s.library_path) if s.library_path else DATA_DIR / "library",
            theme=s.theme,
            reasoning_display=s.reasoning_display,
            role_models=dict(s.role_models),
            mcp_servers=resolved_servers,
            llm_host=s.llm_host,
            llm_provider=s.llm_provider,
            llm_num_ctx=s.llm_num_ctx,
            ctx_warn_threshold=s.ctx_warn_threshold,
            ctx_overflow_threshold=s.ctx_overflow_threshold,
            model_http_retries=s.model_http_retries,
            tool_retries=s.tool_retries,
            session_ttl_minutes=s.session_ttl_minutes,
            background_max_concurrent=s.background_max_concurrent,
            background_task_retention_days=s.background_task_retention_days,
            background_auto_cleanup=s.background_auto_cleanup,
            background_task_inactivity_timeout=s.background_task_inactivity_timeout,
        )


@dataclass
class CoCapabilityState:
    """Bootstrap-set capability registry — shared by reference with sub-agents.

    Populated during agent construction and startup (MCP discovery, skill loading)
    in main.py and bootstrap; not mutated afterward. Sub-agents receive the same
    instance via make_subagent_deps (passed by reference), so they always see the
    correct tool and skill registry.

    Covers the complete bootstrap-set capability surface: tool names, approval map,
    MCP errors, skill commands, skill registry, and slash command count.
    """

    tool_names: list[str] = field(default_factory=list)
    tool_approvals: dict[str, bool] = field(default_factory=dict)
    mcp_discovery_errors: dict[str, str] = field(default_factory=dict)
    skill_commands: dict[str, SkillConfig] = field(default_factory=dict)
    skill_registry: list[dict] = field(default_factory=list)
    slash_command_count: int = 0


@dataclass
class CoSessionState:
    """Mutable tool-visible session state.

    State here is readable and writable by tools and slash commands during the
    session. Sub-agents receive a partially-inherited instance — see make_subagent_deps.
    """

    google_creds: Any | None = field(default=None, repr=False)
    google_creds_resolved: bool = False
    session_approval_rules: list[SessionApprovalRule] = field(default_factory=list)
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)
    session_todos: list[dict] = field(default_factory=list)
    session_id: str = ""
    memory_recall_state: MemoryRecallState = field(default_factory=MemoryRecallState)


@dataclass
class CoRuntimeState:
    """Mutable orchestration/processor transient state.

    State here is owned by the orchestration layer and history processors. Tools
    should not normally reach into runtime — doing so is a design smell that
    should be explicitly justified.

    Per-turn (reset by reset_for_turn() at run_turn() entry):
      turn_usage, safety_state, tool_progress_callback, active_tool_filter
    Cross-turn (managed by orchestration layer):
      precomputed_compaction, active_skill_name
    """

    precomputed_compaction: CompactionResult | None = field(default=None, repr=False)
    # turn_usage: authoritative per-turn usage accumulator.
    # Reset to None at the start of each foreground turn by run_turn().
    # The foreground orchestrator merges each segment's usage after _execute_stream_segment().
    # Sub-agent tools may also merge usage into it during the same turn.
    turn_usage: RunUsage | None = None
    # Installed by StreamRenderer.install_progress() on FunctionToolCallEvent,
    # cleared by StreamRenderer.clear_progress() on FunctionToolResultEvent.
    # run_turn() sets this to None in its finally block as a safety net.
    # Only one tool owns progress at a time (single-owner model).
    tool_progress_callback: Callable[[str], None] | None = field(default=None, repr=False)
    safety_state: SafetyState | None = field(default=None, repr=False)
    active_skill_name: str | None = None
    # active_tool_filter: per-resume-segment schema filter.
    # None = no filter; all registered tools visible (main agent turns).
    # set  = only these names visible; always includes _ALWAYS_ON_TOOL_NAMES.
    # Lifecycle: set in _run_approval_loop per resume hop; cleared after loop + reset_for_turn().
    active_tool_filter: set[str] | None = None

    def reset_for_turn(self) -> None:
        """Reset per-turn fields at the start of each run_turn() call.

        Called once at run_turn() entry. Clears the usage accumulator,
        initialises a fresh safety state, and ensures no stale progress
        callback carries over from an interrupted prior turn.
        """
        self.turn_usage = None
        self.safety_state = SafetyState()
        self.tool_progress_callback = None
        self.active_tool_filter = None


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools — grouped by responsibility.

    Ownership rules:
      services      = injected capabilities (shell, knowledge index, task runner)
      config        = injected read-only settings (API keys, paths, limits, thresholds)
      capabilities  = bootstrap-set capability registry; shared by reference with sub-agents
      session       = mutable tool-visible session state (creds, approvals, todos)
      runtime       = mutable orchestration/processor transient state

    pydantic-ai receives this as the single deps_type. Tools access fields via
    ctx.deps.services.shell, ctx.deps.config.memory_dir, etc.
    """

    services: CoServices
    config: CoConfig
    capabilities: CoCapabilityState = field(default_factory=CoCapabilityState)
    session: CoSessionState = field(default_factory=CoSessionState)
    runtime: CoRuntimeState = field(default_factory=CoRuntimeState)


def make_subagent_deps(base: "CoDeps") -> "CoDeps":
    """Create an isolated CoDeps copy for a sub-agent.

    Shares services, config, and capabilities by reference (safe — handles are stateless
    or thread-safe; config and capabilities are read-only after bootstrap).

    Session fields:
      Inherited: google_creds, google_creds_resolved (resolved once, safe to share),
                 session_approval_rules (copied — sub-agent grants must not leak to parent).
      Fresh:     drive_page_tokens, session_todos, session_id.

    Capabilities are shared by reference — loaded once at startup, never mutated afterward.
    Runtime is reset to clean defaults (no compaction cache, no turn usage).
    """
    inherited_session = CoSessionState(
        google_creds=base.session.google_creds,
        google_creds_resolved=base.session.google_creds_resolved,
        session_approval_rules=list(base.session.session_approval_rules),
    )
    return CoDeps(
        services=base.services,
        config=base.config,
        capabilities=base.capabilities,
        session=inherited_session,
        runtime=CoRuntimeState(),
    )
