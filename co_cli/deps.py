from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from pydantic_ai.usage import RunUsage

from co_cli.config.core import (
    DEFAULT_REASONING_DISPLAY,
    MEMORY_DIR,
    SESSIONS_DIR,
    TOOL_RESULTS_DIR,
    USER_DIR,
    Settings,
)
from co_cli.config.llm import DEFAULT_MAX_CTX
from co_cli.tools.file_read_tracker import FileReadTracker

if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

    from co_cli.index.store import IndexStore
    from co_cli.llm.factory import LlmModel
    from co_cli.memory.store import MemoryStore
    from co_cli.session.store import SessionStore
    from co_cli.skills.skill_types import SkillInfo
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


@dataclass(frozen=True)
class ApprovalSubject:
    """Resolved representation of what is being approved.

    tool_name:    the registered tool name (e.g. "shell_exec")
    kind:         category matching SessionApprovalRule.kind
    value:        the scoped key used for session rule matching
    display:      human-readable description shown in the approval prompt
    can_remember: whether 'a' should store a session rule
    preview:      optional content preview (write_file: first N lines of content;
                  None for all other tool kinds — display is sufficient)
    """

    tool_name: str
    kind: ApprovalKindEnum
    value: str
    display: str
    can_remember: bool
    preview: str | None = None


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
    spill_threshold_chars: int | float | None = None
    is_read_only: bool = False
    is_concurrent_safe: bool = False
    retries: int | None = None
    requires_config: str | None = None
    check_fn: Callable[[CoDeps], bool] | None = None
    approval_subject_fn: Callable[[dict[str, Any]], ApprovalSubject] | None = None


class TodoItem(TypedDict):
    """Shape of a single todo entry in CoSessionState.session_todos."""

    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]
    priority: Literal["high", "medium", "low"]


@dataclass
class GoogleSessionState:
    """Google integration session state — lazy-resolved credentials and Drive pagination."""

    creds: Any | None = field(default=None, repr=False)
    creds_resolved: bool = False
    drive_page_tokens: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CoSessionState:
    """Mutable tool-visible session state.

    State here is readable and writable by tools and slash commands during the
    session. Delegation agents receive a partially-inherited instance — see fork_deps.

    session_path is the session identity handle, not just a persistence path. Tools
    derive the short session ID from session_path.stem[-8:] and use it for recall
    self-exclusion (e.g. recall.py) and delegation identity (e.g. delegation.py,
    capabilities.py).
    """

    google: GoogleSessionState = field(default_factory=GoogleSessionState)
    session_approval_rules: list[SessionApprovalRule] = field(default_factory=list)
    session_todos: list[TodoItem] = field(default_factory=list)
    session_path: Path = field(default_factory=Path)
    background_tasks: dict[str, BackgroundTaskState] = field(default_factory=dict)
    # User-preference: set at session start from CLI/config, mutable via /reasoning command.
    reasoning_display: str = DEFAULT_REASONING_DISPLAY
    # Memory-domain turn counter: bumped +1 per turn by _post_turn_hook.
    # Tracks user-intent signal (one per turn, regardless of tool calls).
    # Reset to 0 when a memory KICK fires or memory_create/append/replace runs.
    turns_since_memory_review: int = 0
    # Skill-domain model-request counter: bumped +model_request_count per turn by _post_turn_hook.
    # Tracks agent-action signal (tool + reasoning steps, not just turns).
    # Reset to 0 when a skill KICK fires or skill_create/skill_edit/skill_patch runs.
    model_requests_since_skill_review: int = 0


@dataclass
class CoRuntimeState:
    """Mutable orchestration/processor transient state.

    State here is owned by the orchestration layer and history processors. Tools
    should not normally reach into runtime — doing so is a design smell that
    should be explicitly justified.

    Per-turn (reset by reset_for_turn() at run_turn() entry):
      turn_usage, tool_progress_callback, status_callback, resume_tool_names,
      compaction_applied_this_turn, current_request_tokens_estimate
    Cross-turn (managed by orchestration layer):
      active_skill_name, compaction_skip_count,
      consecutive_low_yield_proactive_compactions,
      last_reported_input_tokens, persisted_message_count
    """

    # Per-model-request brake counter; resets implicitly on ctx.run_step transition.
    tool_call_limit_run_step: int = -1
    tool_calls_in_model_request: int = 0
    # Consecutive model requests where tool_calls_in_model_request exceeded the cap.
    # Reset to 0 per turn by reset_for_turn(); also reset to 0 when a non-violating
    # CallToolsNode fires (so only unbroken consecutive violations trigger the hard-stop).
    consecutive_tool_cap_violations: int = 0
    # Written by enforce_request_size (all exit paths); read by proactive_window_processor
    # for OTEL diagnostics only (no logic branches on it).
    current_request_tokens_estimate: int | None = None
    # Circuit breaker for inline compaction summarisation.
    compaction_skip_count: int = 0
    turn_usage: RunUsage | None = None
    tool_progress_callback: Callable[[str], None] | None = field(default=None, repr=False)
    # Turn-scoped display callback: set by run_turn() before the agent starts,
    # cleared by reset_for_turn(). Used by history processors (e.g. proactive
    # compaction) that run inside the agent loop without direct Frontend access.
    status_callback: Callable[[str], None] | None = field(default=None, repr=False)
    active_skill_name: str | None = None
    active_skill_env: dict[str, str] = field(default_factory=dict)
    resume_tool_names: frozenset[str] | None = None
    # Clarify answers collected during deferred-tool approval, keyed by tool_call_id.
    # Orchestration-owned transient state threaded to the clarify tool on its approved
    # resume — the deferred-resume case this docstring's "explicitly justified" carve-out
    # anticipates. Injecting here (rather than via ToolApproved.override_args) keeps
    # user_answers out of the model-facing schema and preserves the original questions
    # args through resume validation.
    clarify_answers: dict[str, list[str]] = field(default_factory=dict)
    # True when compaction ran this turn; drives session-branching (main.py) and the
    # within-turn re-trigger short-circuit in proactive_window_processor. Cleared by
    # reset_for_turn().
    compaction_applied_this_turn: bool = False
    # Delegation depth — incremented by fork_deps(); guards against recursive delegation.
    agent_depth: int = 0
    # Anti-thrashing counter: consecutive proactive compactions that saved less than
    # the configured minimum savings threshold.
    # Reset by compaction.py when overflow recovery or hygiene fires.
    consecutive_low_yield_proactive_compactions: int = 0
    # Latest provider-reported input_tokens. Written by TokenTrackingCapability.after_model_request
    # from each ModelResponse with usage.input_tokens > 0, and overwritten by commit_compaction with
    # the local post-compaction estimate so the next trigger pass doesn't see the stale pre-compaction
    # value. Read by proactive_window_processor, enforce_request_size, and the OTEL turn-end ratio.
    # Cleared by /new and /clear.
    last_reported_input_tokens: int | None = None
    # Count of messages durably persisted to session_path; accumulates across the session.
    # Not reset per-turn — append-only persistence requires this to grow monotonically.
    persisted_message_count: int = 0
    # Wired in bootstrap/core.py:create_deps to frontend.on_status. Not cleared per-turn.
    background_status_callback: Callable[[str], None] | None = field(default=None, repr=False)

    def reset_for_turn(self) -> None:
        """Reset per-turn fields at the start of each run_turn() call."""
        self.turn_usage = None
        self.tool_progress_callback = None
        self.status_callback = None
        self.resume_tool_names = None
        self.clarify_answers = {}
        self.compaction_applied_this_turn = False
        self.current_request_tokens_estimate = None
        self.consecutive_tool_cap_violations = 0


def _resource_lock_store_factory() -> ResourceLockStore:
    from co_cli.tools.resource_lock import ResourceLockStore

    return ResourceLockStore()


# Maximum parallel tool calls per session. Enforced by CoDeps.tool_dispatch_sem.
# Forked agents (reviewer) share the parent's semaphore by reference so total
# session concurrency across all agents is bounded by this single cap.
MAX_TOOL_DISPATCH_WORKERS: int = 10

# Path defaults — all user-global; resolved from USER_DIR constants at runtime
# skills_dir: co-bundled skills shipped with the package
_DEFAULT_SKILLS_DIR = Path(__file__).parent / "skills"
_DEFAULT_USER_SKILLS_DIR = USER_DIR / "skills"
_DEFAULT_MEMORY_DIR = MEMORY_DIR
_DEFAULT_SESSIONS_DIR = SESSIONS_DIR
_DEFAULT_TOOL_RESULTS_DIR = TOOL_RESULTS_DIR


@dataclass
class CoDeps:
    """Runtime dependencies for agent tools.

    config: Settings instance (read-only after bootstrap).
    Workspace paths and runtime degradation state live here, not on config.
    Tools access via ctx.deps.config.llm.provider, ctx.deps.memory_store, etc.
    """

    # Service handles
    shell: ShellBackend
    # Config — the Settings instance, read-only after bootstrap
    config: Settings
    # Resource lock store (shared across parent and delegation agents)
    resource_locks: ResourceLockStore = field(
        default_factory=_resource_lock_store_factory, repr=False
    )
    # Dispatch backstop — caps concurrent tool calls to MAX_TOOL_DISPATCH_WORKERS per session.
    # Shared by reference across fork_deps forks (reviewer) so total session
    # concurrency is bounded by a single cap regardless of how many agents are active.
    tool_dispatch_sem: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_TOOL_DISPATCH_WORKERS), repr=False
    )
    # File read tracker — shared across parent and delegation agents by reference for staleness detection
    file_tracker: FileReadTracker = field(default_factory=FileReadTracker, repr=False)
    # Service handles (optional, set during bootstrap)
    index_store: IndexStore | None = field(default=None, repr=False)
    memory_store: MemoryStore | None = field(default=None, repr=False)
    session_store: SessionStore | None = field(default=None, repr=False)
    model: LlmModel | None = field(default=None, repr=False)
    # Optional pinned-distinct judge model (phase-2 evals). None → fall back to ``model``
    # with [judge_model_same_as_agent] reason flag. Set in bootstrap from
    # settings.llm.judge_model via build_judge_model().
    judge_model: LlmModel | None = field(default=None, repr=False)
    # Bootstrap-set registries
    tool_index: dict[str, ToolInfo] = field(default_factory=dict)
    toolset: AbstractToolset[CoDeps] | None = field(default=None, repr=False)
    skill_index: dict[str, SkillInfo] = field(default_factory=dict)
    # Grouped mutable state
    session: CoSessionState = field(default_factory=CoSessionState)
    runtime: CoRuntimeState = field(default_factory=CoRuntimeState)

    # Workspace paths — resolved from config at bootstrap, not from cwd
    workspace_dir: Path = field(default_factory=Path.cwd)
    # Read scope for file_read / file_search — decoupled from the write/cwd anchor.
    # Empty here is filled with [workspace_dir] in __post_init__ (BC-4 default); an
    # explicit non-empty list (resolve_workspace_paths / fork) is authoritative (BC-3).
    file_search_roots: list[Path] = field(default_factory=list)
    memory_dir: Path = field(default_factory=lambda: _DEFAULT_MEMORY_DIR)
    skills_dir: Path = field(default_factory=lambda: _DEFAULT_SKILLS_DIR)
    user_skills_dir: Path = field(default_factory=lambda: _DEFAULT_USER_SKILLS_DIR)
    sessions_dir: Path = field(default_factory=lambda: _DEFAULT_SESSIONS_DIR)
    tool_results_dir: Path = field(default_factory=lambda: _DEFAULT_TOOL_RESULTS_DIR)

    # Effective context window size — single source of truth, set unconditionally at bootstrap.
    # Ollama: read from the loaded Modelfile via /api/show, capped by max_ctx (probe-failure
    # fallback: max_ctx). Other providers: max_ctx ceiling. Defaults to the configured ceiling
    # so a deps built outside bootstrap still resolves a usable budget (never a 0-divide / 0-budget).
    model_max_ctx: int = DEFAULT_MAX_CTX
    # Bootstrap-cached: int(spill_ratio x model_max_ctx). Immutable after bootstrap.
    # Read by enforce_request_size; never recomputed at read sites.
    spill_threshold_tokens: int = 0
    # Bootstrap-cached: measured static-instruction tokens + ALWAYS-schema tokens — the
    # floor-inclusive prefill present on every request, absent from the `messages` list. Added to
    # the local estimate at the L2/L3 compaction triggers so a stale/zeroed/missing provider report
    # cannot undercount live input by one floor. Immutable after bootstrap; defaults 0 (floor-blind)
    # for ad-hoc/test deps.
    static_floor_tokens: int = 0

    # Runtime degradation state — mutated during bootstrap, read-only after
    degradations: MappingProxyType[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        # The read scope defaults to the write anchor when unconfigured (BC-4), so
        # direct construction (tests, ad-hoc) tracks workspace_dir without routing
        # through resolve_workspace_paths.
        if not self.file_search_roots:
            self.file_search_roots = [self.workspace_dir]


def fork_deps_for_reviewer(parent: CoDeps) -> CoDeps:
    """Fork deps for a domain reviewer agent (memory or skill)."""
    return fork_deps(parent)


def resolve_workspace_paths(config: Settings) -> dict[str, Any]:
    """Resolve workspace paths from Settings. Used by create_deps()."""
    workspace_dir = Path(config.workspace_path).resolve() if config.workspace_path else Path.cwd()
    # Empty -> default to [workspace_dir] (BC-4). Non-empty -> exactly the resolved
    # list, authoritative and total — no implicit workspace_dir append (BC-3).
    file_search_roots = (
        [Path(p).resolve() for p in config.file_search_paths]
        if config.file_search_paths
        else [workspace_dir]
    )
    return {
        "workspace_dir": workspace_dir,
        "file_search_roots": file_search_roots,
        "skills_dir": Path(__file__).parent / "skills",
        "user_skills_dir": USER_DIR / "skills",
        "sessions_dir": SESSIONS_DIR,
        "tool_results_dir": TOOL_RESULTS_DIR,
        "memory_dir": Path(config.memory_path) if config.memory_path else MEMORY_DIR,
    }


def fork_deps(base: CoDeps) -> CoDeps:
    """Create an isolated CoDeps copy for a delegation tool agent.

    Shares handles, registries, config, and paths by reference.
    Session: inherits credentials, approval rules, and reasoning_display; resets per-session fields.
    Runtime: reset to clean defaults.

    Intentionally shared fields (by reference, not copied):
      file_tracker        — cross-agent staleness detection (mtime + partial-read state)
      resource_locks      — cross-agent lock coordination
      degradations        — read-only after bootstrap
    These are safe to share because per-turn mutable state (CoRuntimeState) is always fresh.

    toolset is intentionally excluded — task agents wire their own minimal
    tool surface via TaskAgentSpec.tool_names resolved by build_task_agent.
    """
    inherited_session = CoSessionState(
        google=GoogleSessionState(
            creds=base.session.google.creds,
            creds_resolved=base.session.google.creds_resolved,
            # drive_page_tokens not inherited — fresh pagination per delegation agent
        ),
        session_approval_rules=list(base.session.session_approval_rules),
        reasoning_display=base.session.reasoning_display,
    )
    return CoDeps(
        shell=base.shell,
        config=base.config,
        resource_locks=base.resource_locks,
        file_tracker=base.file_tracker,
        tool_dispatch_sem=base.tool_dispatch_sem,
        index_store=base.index_store,
        memory_store=base.memory_store,
        session_store=base.session_store,
        model=base.model,
        judge_model=base.judge_model,
        tool_index=base.tool_index,
        skill_index=base.skill_index,
        session=inherited_session,
        runtime=CoRuntimeState(agent_depth=base.runtime.agent_depth + 1),
        workspace_dir=base.workspace_dir,
        file_search_roots=base.file_search_roots,
        memory_dir=base.memory_dir,
        skills_dir=base.skills_dir,
        user_skills_dir=base.user_skills_dir,
        sessions_dir=base.sessions_dir,
        tool_results_dir=base.tool_results_dir,
        model_max_ctx=base.model_max_ctx,
        spill_threshold_tokens=base.spill_threshold_tokens,
        degradations=base.degradations,
    )
