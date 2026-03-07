from dataclasses import dataclass, field, replace as _dataclass_replace
from pathlib import Path
from typing import Any

from pydantic_ai.usage import RunUsage

from co_cli.config import Settings, WebPolicy
from co_cli.shell_backend import ShellBackend


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    Flat fields only — no config objects. main.py reads Settings once and
    injects scalar values here. Tools access ctx.deps.field_name directly.
    """

    shell: ShellBackend
    session_id: str = ""
    obsidian_vault_path: Path | None = None  # Batch 2: Obsidian vault

    # Google credentials — resolved lazily on first Google tool call via google_auth
    google_credentials_path: str | None = None
    google_creds: Any | None = field(default=None, repr=False)
    _google_creds_resolved: bool = field(default=False, repr=False, init=False)

    # Shell safe commands — auto-approved without prompting
    shell_safe_commands: list[str] = field(default_factory=list)

    # Persistent exec approvals path (.co-cli/exec-approvals.json)
    exec_approvals_path: Path = field(default_factory=lambda: Path(".co-cli/exec-approvals.json"))

    # Skills directory — set in chat_loop() to match the path used by _load_skills()
    skills_dir: Path = field(default_factory=lambda: Path(".co-cli/skills"))

    # Knowledge store directories — project-local memory + user-global library
    memory_dir: Path = field(default_factory=lambda: Path(".co-cli/memory"))
    library_dir: Path = field(default_factory=lambda: Path(".co-cli/library"))

    # Mutable per-session state
    auto_approved_tools: set[str] = field(default_factory=set)
    # Active skill-env vars for the current turn — set by dispatch(), cleared by chat_loop() finally
    active_skill_env: dict[str, str] = field(default_factory=dict)
    # Active skill allowed-tools grants — set by dispatch(), cleared by chat_loop() after run_turn()
    # Turn-scoped: non-skill turns leave this empty so the grant does not bleed over.
    active_skill_allowed_tools: set[str] = field(default_factory=set)
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)

    # Shell limits
    shell_max_timeout: int = 600  # Hard ceiling for per-command timeout (seconds)

    # LLM API keys — used by preflight checks and knowledge index
    gemini_api_key: str | None = None

    # Batch 5: Web intelligence
    brave_search_api_key: str | None = None
    web_fetch_allowed_domains: list[str] = field(default_factory=list)
    web_fetch_blocked_domains: list[str] = field(default_factory=list)
    web_policy: WebPolicy = field(default_factory=WebPolicy)
    web_http_max_retries: int = 2
    web_http_backoff_base_seconds: float = 1.0
    web_http_backoff_max_seconds: float = 8.0
    web_http_jitter_ratio: float = 0.2

    # Memory lifecycle
    memory_max_count: int = 200
    memory_dedup_window_days: int = 7
    memory_dedup_threshold: int = 85
    # Memory recall temporal decay (FTS5 path)
    memory_recall_half_life_days: int = 30

    # Memory consolidation
    memory_consolidation_top_k: int = 5
    memory_consolidation_timeout_seconds: int = 20

    # Personality / role
    personality: str | None = None

    # Always-on soul critique (loaded from souls/{role}/critique.md at session start)
    personality_critique: str = ""

    # History governance
    max_history_messages: int = 40
    tool_output_trim_chars: int = 2000
    summarization_model: str = ""

    # Safety thresholds (from Settings, used by history processors)
    doom_loop_threshold: int = 3
    max_reflections: int = 3

    # Knowledge index (FTS5 / hybrid search) — set in main.py, None when backend="grep"
    knowledge_index: Any | None = field(default=None, repr=False)
    knowledge_search_backend: str = "fts5"
    knowledge_reranker_provider: str = "local"

    # MCP server count — for capability introspection
    mcp_count: int = 0

    # Session-scoped todo list — written/read by todo_write/todo_read tools.
    # Cleared on session start; not persisted across sessions.
    session_todos: list[dict] = field(default_factory=list)

    # Background compaction — pre-computed summary set by chat_loop(),
    # consumed (and cleared) by truncate_history_window() on next turn.
    precomputed_compaction: Any = field(default=None, repr=False)

    # Skill registry — populated in chat_loop() after _load_skills(); used by
    # the add_available_skills system prompt to describe skills to the model.
    skill_registry: list[dict] = field(default_factory=list)

    # Approval risk classifier feature flags
    approval_risk_enabled: bool = False
    approval_auto_low_risk: bool = False

    # Sub-agent model roles — model_roles shared by reference; sub-agents must treat as read-only
    model_roles: dict[str, list[str]] = field(default_factory=dict)
    ollama_host: str = "http://localhost:11434"

    # Background task runner — created in main.py, injected here, accessed by tools and slash commands
    task_runner: Any | None = field(default=None, repr=False)

    # Sub-agent turn usage accumulator — delegation tools add their sub-agent usage here
    turn_usage: RunUsage | None = None

    # LLM provider identity and context window size (for overflow detection in run_turn)
    llm_provider: str = Settings.model_fields["llm_provider"].default
    ollama_num_ctx: int = Settings.model_fields["ollama_num_ctx"].default

    # Context overflow detection thresholds (Ollama only — Gemini enforces its own hard limit)
    ctx_warn_threshold: float = Settings.model_fields["ctx_warn_threshold"].default
    ctx_overflow_threshold: float = Settings.model_fields["ctx_overflow_threshold"].default

    # Infrastructure state (managed by processors, not tools)
    # Opening context state persists across session for topic-shift detection.
    # Safety state is reset per turn by run_turn().
    _opening_ctx_state: Any = field(default=None, repr=False, init=False)
    _safety_state: Any = field(default=None, repr=False, init=False)


def make_subagent_deps(base: "CoDeps") -> "CoDeps":
    """Create an isolated CoDeps copy for a sub-agent.

    Copies all scalar config fields (API keys, paths, limits) from *base*
    and resets mutable session state to clean defaults so the sub-agent
    does not inherit the main agent's approval grants, pagination tokens,
    compaction cache, or todo list.

    Fields reset to clean defaults:
        auto_approved_tools      — sub-agent starts with no auto-approvals
        active_skill_env         — no parent skill env bleeds in
        active_skill_allowed_tools — no parent skill tool grants
        drive_page_tokens        — no pagination state from parent
        precomputed_compaction   — no stale compaction from parent session
        session_todos            — sub-agent manages its own task list
        skill_registry           — populated fresh if needed
        turn_usage               — sub-agent does not inherit parent's usage total

    init=False fields (_opening_ctx_state, _safety_state) are implicitly
    reset to None by dataclasses.replace().
    """
    return _dataclass_replace(
        base,
        auto_approved_tools=set(),
        active_skill_env={},
        active_skill_allowed_tools=set(),
        drive_page_tokens={},
        precomputed_compaction=None,
        session_todos=[],
        skill_registry=[],
        turn_usage=None,
    )
