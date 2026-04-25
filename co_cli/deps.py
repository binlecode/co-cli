from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.usage import RunUsage

from co_cli.config._core import (
    DEFAULT_REASONING_DISPLAY,
    KNOWLEDGE_DIR,
    SEARCH_DB,
    SESSIONS_DIR,
    TOOL_RESULTS_DIR,
    USER_DIR,
    Settings,
)
from co_cli.memory.state import MemoryRecallState

if TYPE_CHECKING:
    from co_cli.agent._core import ToolRegistry
    from co_cli.commands._skill_types import SkillConfig
    from co_cli.knowledge._store import KnowledgeStore
    from co_cli.llm._factory import LlmModel
    from co_cli.memory._store import MemoryIndex
    from co_cli.tools.background import BackgroundTaskState
    from co_cli.tools.resource_lock import ResourceLockStore
    from co_cli.tools.shell_backend import ShellBackend


class ApprovalKindEnum(StrEnum):
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
               shared across file_write and file_patch for the same directory
      DOMAIN — web fetch (value = hostname, e.g. "docs.python.org")
      TOOL   — named tool (value = tool_name, e.g. "knowledge_article_save");
               covers MCP tools and generic tools; can_remember=True
    value: the scoped key used for matching future requests
    """

    kind: ApprovalKindEnum
    value: str


class VisibilityPolicyEnum(Enum):
    """Whether a tool is visible by default or hidden behind search_tools."""

    ALWAYS = "always"
    DEFERRED = "deferred"


class ToolSourceEnum(Enum):
    """Origin of a registered tool."""

    NATIVE = "native"
    MCP = "mcp"


@dataclass(frozen=True)
class ToolInfo:
    """Canonical metadata for one registered tool — set once at registration, never mutated."""

    name: str
    description: str
    approval: bool
    source: ToolSourceEnum
    visibility: VisibilityPolicyEnum
    integration: str | None = None
    max_result_size: int | float | None = None
    is_read_only: bool = False
    is_concurrent_safe: bool = False
    retries: int | None = None
    requires_config: str | None = None


@dataclass
class CoSessionState:
    """Mutable tool-visible session state.

    State here is readable and writable by tools and slash commands during the
    session. Delegation agents receive a partially-inherited instance — see fork_deps.
    """

    google_creds: Any | None = field(default=None, repr=False)
    google_creds_resolved: bool = False
    session_approval_rules: list[SessionApprovalRule] = field(default_factory=list)
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)
    session_todos: list[dict] = field(default_factory=list)
    session_path: Path = field(default_factory=Path)
    memory_recall_state: MemoryRecallState = field(default_factory=MemoryRecallState)
    background_tasks: dict[str, BackgroundTaskState] = field(default_factory=dict)
    # User-preference: set at session start from CLI/config, mutable via /reasoning command.
    reasoning_display: str = DEFAULT_REASONING_DISPLAY
    # Cursor for delta-based memory extraction — index of first unextracted message in history.
    last_extracted_message_idx: int = 0
    # Turn counter for cadence-gated extraction.
    last_extracted_turn_idx: int = 0
    # Count of messages durably persisted to session_path.
    persisted_message_count: int = 0


@dataclass
class CoRuntimeState:
    """Mutable orchestration/processor transient state.

    State here is owned by the orchestration layer and history processors. Tools
    should not normally reach into runtime — doing so is a design smell that
    should be explicitly justified.

    Per-turn (reset by reset_for_turn() at run_turn() entry):
      turn_usage, tool_progress_callback, resume_tool_names,
      history_compaction_applied, compacted_in_current_turn
    Cross-turn (managed by orchestration layer):
      active_skill_name, compaction_skip_count,
      consecutive_low_yield_proactive_compactions, last_overbudget_batch_signature,
      extraction_task
    """

    # Circuit breaker for inline compaction summarisation.
    compaction_skip_count: int = 0
    turn_usage: RunUsage | None = None
    tool_progress_callback: Callable[[str], None] | None = field(default=None, repr=False)
    active_skill_name: str | None = None
    resume_tool_names: frozenset[str] | None = None
    history_compaction_applied: bool = False
    # Set when proactive compaction fires within the current turn; cleared by reset_for_turn().
    # Suppresses stale API-reported token counts that would re-trigger compaction spuriously.
    compacted_in_current_turn: bool = False
    # Delegation depth — incremented by fork_deps(); guards against recursive delegation.
    agent_depth: int = 0
    # Anti-thrashing counter: consecutive proactive compactions that saved less than
    # the configured minimum savings threshold.
    # Reset by compaction.py when overflow recovery or hygiene fires.
    consecutive_low_yield_proactive_compactions: int = 0
    # Dedup key for enforce_batch_budget's "still over budget" warning.
    # Same batch repeated request-to-request emits the warning once, not per cycle.
    last_overbudget_batch_signature: tuple[str, ...] | None = None
    # Background knowledge-extraction task slot — owned per-deps so sessions and
    # forked sub-agent deps do not share the in-flight task.
    extraction_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def reset_for_turn(self) -> None:
        """Reset per-turn fields at the start of each run_turn() call."""
        self.turn_usage = None
        self.tool_progress_callback = None
        self.resume_tool_names = None
        self.history_compaction_applied = False
        self.compacted_in_current_turn = False


# Path defaults — all user-global; resolved from USER_DIR constants at runtime
# skills_dir: co-bundled skills shipped with the package
_DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"
_DEFAULT_USER_SKILLS_DIR = USER_DIR / "skills"
_DEFAULT_KNOWLEDGE_DIR = KNOWLEDGE_DIR
_DEFAULT_SESSIONS_DIR = SESSIONS_DIR
_DEFAULT_TOOL_RESULTS_DIR = TOOL_RESULTS_DIR


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    config: Settings instance (read-only after bootstrap).
    Workspace paths and runtime degradation state live here, not on config.
    Tools access via ctx.deps.config.llm.provider, ctx.deps.knowledge_dir, etc.
    """

    # Service handles
    shell: ShellBackend
    # Config — the Settings instance, read-only after bootstrap
    config: Settings
    # Resource lock store (shared across parent and delegation agents)
    resource_locks: ResourceLockStore = field(default=None, repr=False)  # type: ignore[assignment]  # __post_init__ always initializes from None
    # File mtime registry — shared across parent and delegation agents by reference for staleness detection
    file_read_mtimes: dict[str, float] = field(default_factory=dict, repr=False)
    # Paths for which only a partial read (start_line/end_line) was performed — cleared on full read
    file_partial_reads: set[str] = field(default_factory=set, repr=False)
    # Service handles (optional, set during bootstrap)
    knowledge_store: KnowledgeStore | None = field(default=None, repr=False)
    memory_index: MemoryIndex | None = field(default=None, repr=False)
    model: LlmModel | None = field(default=None, repr=False)
    # Bootstrap-set registries
    tool_index: dict[str, ToolInfo] = field(default_factory=dict)
    tool_registry: ToolRegistry | None = field(default=None, repr=False)
    skill_commands: dict[str, SkillConfig] = field(default_factory=dict)
    # Grouped mutable state
    session: CoSessionState = field(default_factory=CoSessionState)
    runtime: CoRuntimeState = field(default_factory=CoRuntimeState)

    # Workspace paths — resolved from cwd at bootstrap, not from config
    workspace_root: Path = field(default_factory=Path.cwd)
    obsidian_vault_path: Path | None = None
    knowledge_dir: Path = field(default_factory=lambda: _DEFAULT_KNOWLEDGE_DIR)
    skills_dir: Path = field(default_factory=lambda: _DEFAULT_SKILLS_DIR)
    user_skills_dir: Path = field(default_factory=lambda: _DEFAULT_USER_SKILLS_DIR)
    knowledge_db_path: Path = field(default_factory=lambda: SEARCH_DB)
    sessions_dir: Path = field(default_factory=lambda: _DEFAULT_SESSIONS_DIR)
    tool_results_dir: Path = field(default_factory=lambda: _DEFAULT_TOOL_RESULTS_DIR)

    # Runtime degradation state — mutated during bootstrap, read-only after
    degradations: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.resource_locks is None:
            from co_cli.tools.resource_lock import ResourceLockStore

            self.resource_locks = ResourceLockStore()


def resolve_workspace_paths(config: Settings, cwd: Path) -> dict[str, Any]:
    """Resolve cwd-relative workspace paths from Settings. Used by create_deps()."""
    return {
        "workspace_root": cwd,
        "obsidian_vault_path": Path(config.obsidian_vault_path)
        if config.obsidian_vault_path
        else None,
        "skills_dir": Path(__file__).parent / "skills",
        "user_skills_dir": USER_DIR / "skills",
        "sessions_dir": SESSIONS_DIR,
        "tool_results_dir": TOOL_RESULTS_DIR,
        "knowledge_dir": Path(config.knowledge_path) if config.knowledge_path else KNOWLEDGE_DIR,
    }


def fork_deps(base: CoDeps) -> CoDeps:
    """Create an isolated CoDeps copy for a delegation tool agent.

    Shares handles, registries, config, and paths by reference.
    Session: inherits credentials, approval rules, and reasoning_display; resets per-session fields.
    Runtime: reset to clean defaults.

    Intentionally shared fields (by reference, not copied):
      file_read_mtimes    — cross-agent staleness detection
      file_partial_reads  — cross-agent partial-read tracking
      resource_locks      — cross-agent lock coordination
      degradations        — read-only after bootstrap
    These are safe to share because per-turn mutable state (CoRuntimeState) is always fresh.
    """
    inherited_session = CoSessionState(
        google_creds=base.session.google_creds,
        google_creds_resolved=base.session.google_creds_resolved,
        session_approval_rules=list(base.session.session_approval_rules),
        reasoning_display=base.session.reasoning_display,
    )
    return CoDeps(
        shell=base.shell,
        config=base.config,
        resource_locks=base.resource_locks,
        file_read_mtimes=base.file_read_mtimes,
        file_partial_reads=base.file_partial_reads,
        knowledge_store=base.knowledge_store,
        memory_index=base.memory_index,
        model=base.model,
        tool_index=base.tool_index,
        skill_commands=base.skill_commands,
        session=inherited_session,
        runtime=CoRuntimeState(agent_depth=base.runtime.agent_depth + 1),
        workspace_root=base.workspace_root,
        obsidian_vault_path=base.obsidian_vault_path,
        knowledge_dir=base.knowledge_dir,
        skills_dir=base.skills_dir,
        user_skills_dir=base.user_skills_dir,
        knowledge_db_path=base.knowledge_db_path,
        sessions_dir=base.sessions_dir,
        tool_results_dir=base.tool_results_dir,
        degradations=base.degradations,
    )
