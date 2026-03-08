from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.usage import RunUsage

from co_cli.config import ModelEntry, WebPolicy
from co_cli._shell_backend import ShellBackend


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


@dataclass
class CoConfig:
    """Injected configuration — set at session bootstrap; may be updated during startup (e.g. backend resolution, model check).

    main.py reads Settings once and populates this dataclass with scalar values.
    Tools access ctx.deps.config.field_name. No tool should import Settings.
    """

    session_id: str = ""
    obsidian_vault_path: Path | None = None
    google_credentials_path: str | None = None
    shell_safe_commands: list[str] = field(default_factory=list)
    shell_max_timeout: int = 600
    exec_approvals_path: Path = field(default_factory=lambda: Path(".co-cli/exec-approvals.json"))
    skills_dir: Path = field(default_factory=lambda: Path(".co-cli/skills"))
    memory_dir: Path = field(default_factory=lambda: Path(".co-cli/memory"))
    library_dir: Path = field(default_factory=lambda: Path(".co-cli/library"))
    gemini_api_key: str | None = None
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
    web_http_max_retries: int = 2
    web_http_backoff_base_seconds: float = 1.0
    web_http_backoff_max_seconds: float = 8.0
    web_http_jitter_ratio: float = 0.2
    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    memory_recall_half_life_days: int = 30
    memory_consolidation_top_k: int = 5
    memory_consolidation_timeout_seconds: int = 20
    memory_auto_save_tags: list[str] = field(default_factory=lambda: ["correction", "preference"])
    knowledge_chunk_size: int = 600
    knowledge_chunk_overlap: int = 80
    personality: str | None = None
    personality_critique: str = ""
    max_history_messages: int = 40
    tool_output_trim_chars: int = 2000
    doom_loop_threshold: int = 3
    max_reflections: int = 3
    knowledge_search_backend: str = "fts5"
    knowledge_reranker_provider: str = "local"
    mcp_count: int = 0
    role_models: dict[str, list[ModelEntry]] = field(default_factory=dict)
    ollama_host: str = "http://localhost:11434"
    llm_provider: str = "ollama"
    ollama_num_ctx: int = 262144
    ctx_warn_threshold: float = 0.85
    ctx_overflow_threshold: float = 1.0


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
