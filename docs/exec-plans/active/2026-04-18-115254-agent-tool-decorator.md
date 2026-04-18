# Plan: `@agent_tool` decorator + unified `ToolInfo`

**Task type:** `refactor` (with regression surface check — behavior unchanged; registration mechanism replaced)

## Context

**Current state (verified by reading source):**

- `co_cli/deps.py:75` — `ToolInfo` dataclass is the canonical per-tool metadata descriptor. Fields: `name`, `description`, `approval`, `source`, `visibility`, `integration`, `max_result_size`, `is_read_only`, `is_concurrent_safe`, `retries`. Already CLAUDE.md-compliant (`*Info` suffix = read-only descriptor).
- `co_cli/agent/_native_toolset.py:61` — `_build_native_toolset()` calls `_register_tool(fn, **kwargs)` where every policy field (approval, visibility, is_read_only, is_concurrent_safe, integration, retries, max_result_size) is passed as keyword args at the registration site.
- `co_cli/agent/_native_toolset.py:238` and `:261` — Integration gating is imperative: `if config.obsidian_vault_path: ...` and `if config.google_credentials_path: ...` wrap blocks of `_register_tool` calls.
- `co_cli/agent/_mcp.py:49` — `discover_mcp_tools()` already produces `ToolInfo` records for MCP tools. Approval inferred from `DeferredLoadingToolset` wrapper count (fragile but working; outside scope of this refactor).
- `co_cli/bootstrap/core.py:244` — `tool_registry.tool_index.update(mcp_index)` merges native + MCP indexes into one `dict[str, ToolInfo]` on `CoDeps`.
- `tool_index` is read by: `tool_io.py` (`max_result_size` lookup), `_tool_lifecycle.py` (trace tags), `capabilities.py` (introspection tool output), `_deferred_tool_prompt.py` (approval UX), `_instructions.py` (category-awareness prompt), `bootstrap/banner.py` & `bootstrap/check.py` (startup diagnostics).
- `docs/specs/tools.md:174` — Already documents `obsidian_vault_path` and `google_credentials_path` as "registration gates."

**Pain point:**

Tool policy lives at the registration site, not the definition site. A reviewer reading `co_cli/tools/shell.py` sees the function but not the policy (approval, visibility, retries, max_result_size). They must open `co_cli/agent/_native_toolset.py` and locate the matching `_register_tool` call. For ~22 native tools this is a two-file round trip on every audit and every tool-edit PR.

Integration gating compounds the split: the `if config.obsidian_vault_path:` block is imperative scaffolding that knows about each integration individually. Adding a new config-gated integration means editing `_native_toolset.py` to add another `if` branch.

No existing plan file matches this slug (`docs/exec-plans/active/*-agent-tool-decorator.md` — empty). No shipped-work to skip.

## Problem & Outcome

**Problem:** Tool policy is declared at the registration site, separated from the function definition. Adding, editing, or auditing a tool requires reading two files. Integration gates are hand-written conditionals in `_native_toolset.py` rather than declared per-tool.

**Failure cost:** Reviewers miss policy drift (a PR changes the tool's behavior but leaves registration flags stale, or vice versa). Onboarding to the tool system requires reading both the function files and the registration file to understand the surface. Adding a new config-gated integration requires editing the central registration module rather than self-contained tool files.

**Outcome:** Every native tool declares its policy at the definition site via `@agent_tool(...)`. `ToolInfo` remains the single descriptor — native and MCP paths both produce it. `_native_toolset.py` collapses to a flat `NATIVE_TOOLS` tuple + a data-driven loop that reads `__co_tool_info__`, applies declarative `requires_config` gates, and calls pydantic-ai's `FunctionToolset.add_function`. Registration raises `TypeError` if a listed function lacks the decorator (fail-fast; no optional-path branch).

Behavior from a user's perspective: **unchanged**. Same tools, same approval UX, same visibility, same `tool_index` contents.

## Scope

**In scope:**

1. Extend `ToolInfo` with `requires_config: str | None = None` — a single flat Settings field name. Default `None` means no gate (MCP call sites unchanged).
2. Introduce `@agent_tool(...)` decorator that constructs a `ToolInfo` and attaches it to the function object as `__co_tool_info__`. The decorator validates invariants at import time (e.g. `is_read_only` + `approval=True` is contradictory).
3. Decorate every native tool function currently registered in `_build_native_toolset()`. No changes to tool bodies — just the decorator on the signature.
4. Refactor `_build_native_toolset()` to: a single flat `NATIVE_TOOLS` tuple of function references, a data-driven loop that reads `__co_tool_info__`, applies the `requires_config` gate against Settings, calls `add_function`. Integration `if`-blocks deleted. The old `_register_tool` closure and its `assert` are deleted (the decorator owns the invariant).
5. Bootstrap-time fail-fast: any function in `NATIVE_TOOLS` without `__co_tool_info__` raises `TypeError` with module + name. This is the regression guard — no separate test needed; every test that boots `CoDeps` exercises it.
6. Behavioral tests: `tool_index` populated with expected policies; `requires_config` gate correctly skips integration tools when config absent.

**Out of scope:**

- MCP tool client-side policy overrides (per-tool `max_result_size`, `is_read_only`, etc. for MCP tools). Current MCP path populates `ToolInfo` with sensible defaults; changing that is a separate concern.
- MCP approval-inference-from-wrapper-count cleanup (`_mcp.py:86`). Fragile but working; untouched here.
- Auto-discovery of tools via module walk. `NATIVE_TOOLS` stays explicit — explicit registration gives integration gating a clear home and avoids import-order surprises.
- `docs/specs/tools.md` updates. Specs are outputs of delivery (`sync-doc` post-delivery), not inputs.
- Decorator for MCP tools. MCP tools come from external servers; they cannot be decorated. The `ToolInfo` construction in `discover_mcp_tools()` stays as-is.
- Delegation path (`build_agent(..., tool_fns=...)` in `_core.py:167`). Delegation agents register tools with `requires_approval=False` explicitly and do not read the decorator. Unchanged.
- `description=` override in the decorator. First docstring line remains authoritative.
- Dotted config paths in `requires_config` (e.g. `"google.enabled"`). All current integration gates are flat fields; revisit if a nested gate appears.
- Environment gates (`sys.platform`, feature flags). Not needed by any current tool. If needed later, a separate `requires_env` predicate could be added.

## Behavioral Constraints

Each constraint must hold under the refactor. Any test that violates one is the test to fix; any code that violates one is a bug.

1. **Policy source of truth moves, not changes.** For every tool currently in `_build_native_toolset()`, the post-refactor `ToolInfo` in `tool_index` must be field-for-field identical to the pre-refactor record: same `approval`, `visibility`, `is_read_only`, `is_concurrent_safe`, `integration`, `retries`, `max_result_size`. Deltas are bugs.
2. **Mandatory decorator on registered natives.** If a function appears in `NATIVE_TOOLS` but lacks `__co_tool_info__`, the builder raises `TypeError` whose message includes `fn.__module__` and `fn.__name__`. No silent defaulting, no optional-path branch.
3. **Declarative config gate is presence-based.** A tool with `requires_config="obsidian_vault_path"` registers if and only if `getattr(config, "obsidian_vault_path", None)` is truthy. `requires_config=None` (the default) means unconditionally register.
4. **MCP contract unchanged.** `discover_mcp_tools()` continues to produce `ToolInfo` records with `source=ToolSourceEnum.MCP`, populating at minimum `name`, `description`, `approval`, `visibility`, `integration`. Default values for the remaining fields are acceptable (matches today's behavior).
5. **Source categorization preserved.** Every tool in `tool_index` has `source=ToolSourceEnum.NATIVE` or `source=ToolSourceEnum.MCP`. The decorator sets `NATIVE`; the MCP adapter sets `MCP`. No third value.
6. **Invariant validation at import time.** The decorator rejects contradictory combinations at import: `is_read_only=True and approval=True` raises `ValueError`; `is_read_only=True and is_concurrent_safe=False` raises `ValueError`. The pre-refactor `assert` inside `_register_tool` is deleted in TASK-5 — the invariant is not duplicated.
7. **Delegation path untouched.** `build_agent()` with `tool_fns` (delegation) continues to call `delegation_agent.tool(fn, requires_approval=False)` without reading the decorator. Behavior of `research_web`, `analyze_knowledge`, `reason_about` subagents unchanged.
8. **No module-level side effects beyond decoration.** Importing a tool module attaches `__co_tool_info__` to decorated functions; no other import-time state is introduced. Tool modules remain free of global mutable state per CLAUDE.md.
9. **Import isolation of `_agent_tool.py`.** Importing `co_cli.tools._agent_tool` must not transitively import any `co_cli.agent.*` module. The decorator module imports only `ToolInfo`, `ToolSourceEnum`, `VisibilityPolicyEnum` from `co_cli.deps` — never `CoDeps`, never tool implementations, never agent construction code. This prevents future deps-module edits from creating a circular import (`deps` → `_agent_tool` → agent internals → `deps`).

## High-Level Design

### Module layout

```
co_cli/
├── deps.py                              # ToolInfo: +requires_config field
├── tools/
│   ├── _agent_tool.py                   # NEW — @agent_tool decorator + internal builder
│   ├── shell.py                         # @agent_tool(...) on run_shell_command
│   ├── files.py                         # @agent_tool(...) on read_file/glob/grep/write_file/patch
│   ├── knowledge.py                     # @agent_tool(...) on all 7 knowledge tools
│   ├── memory.py                        # @agent_tool(...) on search_memory
│   ├── session_search.py                # (if it exposes a tool)
│   ├── todo.py                          # @agent_tool(...) on read_todos/write_todos
│   ├── capabilities.py                  # @agent_tool(...) on check_capabilities
│   ├── web.py                           # @agent_tool(...) on web_search/web_fetch
│   ├── execute_code.py                  # @agent_tool(...) on execute_code
│   ├── task_control.py                  # @agent_tool(...) on 4 background task tools
│   ├── agents.py                        # @agent_tool(...) on 3 delegation tools
│   ├── obsidian.py                      # @agent_tool(...) with requires_config=("obsidian_vault_path",)
│   └── google/
│       ├── drive.py                     # @agent_tool(...) with requires_config=("google_credentials_path",)
│       ├── gmail.py                     # same
│       └── calendar.py                  # same
└── agent/
    ├── _native_toolset.py               # REFACTORED — flat NATIVE_TOOLS + data-driven loop
    └── _mcp.py                          # UNCHANGED
```

### `@agent_tool` decorator (definition-site policy)

```python
# co_cli/tools/_agent_tool.py
# Import-isolation constraint (Behavioral Constraint 9): this module imports
# ONLY the listed names from co_cli.deps. No CoDeps, no agent internals.
from collections.abc import Callable
from typing import TypeVar
from co_cli.deps import ToolInfo, ToolSourceEnum, VisibilityPolicyEnum

F = TypeVar("F", bound=Callable)

AGENT_TOOL_ATTR = "__co_tool_info__"

def agent_tool(
    *,
    visibility: VisibilityPolicyEnum,
    approval: bool = False,
    is_read_only: bool = False,
    is_concurrent_safe: bool = False,
    integration: str | None = None,
    requires_config: str | None = None,
    retries: int | None = None,
    max_result_size: int = 50_000,
) -> Callable[[F], F]:
    # Import-time invariants — same contract as the pre-refactor _register_tool assert.
    if is_read_only and not is_concurrent_safe:
        raise ValueError("@agent_tool: is_read_only=True requires is_concurrent_safe=True")
    if is_read_only and approval:
        raise ValueError("@agent_tool: is_read_only=True is incompatible with approval=True")

    def decorator(fn: F) -> F:
        name = fn.__name__
        description = fn.__doc__.split("\n")[0].strip() if fn.__doc__ else name
        info = ToolInfo(
            name=name,
            description=description,
            source=ToolSourceEnum.NATIVE,
            visibility=visibility,
            approval=approval,
            is_read_only=is_read_only,
            is_concurrent_safe=is_concurrent_safe,
            integration=integration,
            requires_config=requires_config,
            retries=retries,
            max_result_size=max_result_size,
        )
        setattr(fn, AGENT_TOOL_ATTR, info)
        return fn

    return decorator
```

### Refactored registration (data-driven)

```python
# co_cli/agent/_native_toolset.py
from co_cli.tools._agent_tool import AGENT_TOOL_ATTR
from co_cli.tools.shell import run_shell_command
from co_cli.tools.files import glob, grep, patch, read_file, write_file
# ... all native tool imports ...

# Flat explicit list. Order is presentation order (no behavioral impact).
NATIVE_TOOLS: tuple[Callable, ...] = (
    # Introspection & todos
    check_capabilities,
    read_todos,
    write_todos,
    # Knowledge reads
    search_knowledge, list_knowledge, read_article, search_articles,
    search_memory,
    # Workspace reads
    glob, read_file, grep,
    # Web
    web_search, web_fetch,
    # Execution
    run_shell_command,
    # File writes (deferred)
    write_file, patch,
    # Knowledge writes (deferred)
    update_knowledge, append_knowledge, save_article,
    # Background tasks (deferred)
    start_background_task, check_task_status, cancel_background_task, list_background_tasks,
    # Code execution (deferred)
    execute_code,
    # Delegation (deferred)
    research_web, analyze_knowledge, reason_about,
    # Obsidian (requires obsidian_vault_path)
    list_notes, search_notes, read_note,
    # Google (requires google_credentials_path)
    search_drive_files, read_drive_file,
    list_gmail_emails, search_gmail_emails, create_gmail_draft,
    list_calendar_events, search_calendar_events,
)


def _build_native_toolset(
    config: Settings,
) -> tuple[FunctionToolset[CoDeps], dict[str, ToolInfo]]:
    toolset: FunctionToolset[CoDeps] = FunctionToolset()
    index: dict[str, ToolInfo] = {}

    for fn in NATIVE_TOOLS:
        info: ToolInfo | None = getattr(fn, AGENT_TOOL_ATTR, None)
        if info is None:
            raise TypeError(
                f"{fn.__module__}.{fn.__name__}: missing @agent_tool(...) decorator. "
                "Every function in NATIVE_TOOLS must declare policy at definition site."
            )
        if info.requires_config is not None and not getattr(
            config, info.requires_config, None
        ):
            continue
        kwargs: dict[str, Any] = {
            "requires_approval": info.approval,
            "sequential": not info.is_concurrent_safe,
            "defer_loading": info.visibility == VisibilityPolicyEnum.DEFERRED,
        }
        if info.retries is not None:
            kwargs["retries"] = info.retries
        toolset.add_function(fn, **kwargs)
        index[info.name] = info

    return toolset, index
```

The approval-resume filter (`_approval_resume_filter`) is unchanged — it continues to read `ctx.deps.tool_index` and the `VisibilityPolicyEnum`.

### Per-tool decoration example (illustrative, not exhaustive)

```python
# co_cli/tools/shell.py
from co_cli.tools._agent_tool import agent_tool
from co_cli.deps import VisibilityPolicyEnum

@agent_tool(
    visibility=VisibilityPolicyEnum.ALWAYS,
    is_concurrent_safe=True,
    max_result_size=30_000,
)
async def run_shell_command(ctx, cmd: str, timeout: int = 120) -> ToolReturn:
    """Execute a shell command and return combined stdout + stderr as text."""
    # body unchanged
```

```python
# co_cli/tools/obsidian.py
@agent_tool(
    visibility=VisibilityPolicyEnum.DEFERRED,
    is_read_only=True,
    is_concurrent_safe=True,
    integration="obsidian",
    requires_config="obsidian_vault_path",
)
async def list_notes(ctx, ...) -> ToolReturn:
    """List notes in the Obsidian vault."""
    # body unchanged
```

### Why this design

**Decorator, not wrapper.** The decorator sets an attribute and returns the original function. pydantic-ai's `FunctionToolset.add_function(fn, ...)` continues to introspect `fn.__signature__`, `fn.__doc__`, and type hints exactly as today — no schema drift risk.

**Keep `ToolInfo`.** `CLAUDE.md` says `*Info` suffix = read-only descriptor. `ToolInfo` already fits. Adding a `ToolMeta` alongside would create two descriptors for the same concept.

**`requires_config` as a single flat settings path (`str | None`).** Handles all current integration gates (`obsidian_vault_path`, `google_credentials_path`). Every tool today uses exactly one path, so a single string avoids inventing ALL-of vs ANY-of semantics without a user. When a second case finally demands multi-path gating, extending to a tuple is a one-line type change + a grep across ~13 decorator call sites — cheap to migrate at that point.

**Explicit `NATIVE_TOOLS` tuple over auto-discovery.** Auto-discovering via `importlib` walk of `co_cli.tools` introduces import-order ambiguity and makes "is this tool registered?" harder to answer from a cold read. The tuple is ~35 lines of plain identifiers — cheap, clear, diff-friendly.

**No structural tests.** Bootstrap itself raises `TypeError` if a listed function lacks the decorator. Any functional test that boots `CoDeps` exercises this path. A dedicated "every tool has a decorator" test would be structural noise per CLAUDE.md's "behavior over structure" rule.

### Alternatives considered

- **Formal `Tool` class wrapping the function.** Rejected: pydantic-ai introspects the function directly; wrapping forces us to forward signature, docstring, type hints. Zero win, extra indirection.
- **`@tool` (lowercase, short name).** Rejected: collides with common local variable `tool` and is ambiguous inside `co_cli/tools/` where many things are called `tool_*`.
- **`@Tool` (class-as-decorator).** Rejected: Python convention is lowercase for annotating decorators; capitalized names suggest classes-used-directly.
- **Callable predicate for `requires_config`.** Rejected for now: tuple of paths covers today's needs; callables lose declarative readability.
- **Two separate descriptors (`ToolMeta` for declared, `ToolInfo` for registered).** Rejected: the only difference is that `name`/`description`/`source` are derived from `fn.__name__`/`fn.__doc__`/constant at decoration time. That's a derivation, not a second type.

## Implementation Plan

### ✓ DONE — TASK-1 — Extend `ToolInfo` and create `@agent_tool` decorator

```yaml
files:
  - co_cli/deps.py
  - co_cli/tools/_agent_tool.py  # new
done_when: |
  # 1. Decorator attaches correct ToolInfo
  uv run python -c "
  from co_cli.tools._agent_tool import agent_tool
  from co_cli.deps import ToolInfo, VisibilityPolicyEnum, ToolSourceEnum

  @agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_read_only=True, is_concurrent_safe=True)
  async def foo(): 'Short desc.'

  info = foo.__co_tool_info__
  assert isinstance(info, ToolInfo)
  assert info.name == 'foo'
  assert info.description == 'Short desc.'
  assert info.source == ToolSourceEnum.NATIVE
  assert info.visibility == VisibilityPolicyEnum.ALWAYS
  assert info.is_read_only is True
  assert info.is_concurrent_safe is True
  assert info.approval is False
  assert info.requires_config is None
  print('OK')
  " | grep -q OK &&

  # 2. Contradictory flag combinations raise at import/decoration time
  uv run python -c "
  from co_cli.tools._agent_tool import agent_tool
  from co_cli.deps import VisibilityPolicyEnum
  try:
      @agent_tool(visibility=VisibilityPolicyEnum.DEFERRED, is_read_only=True, approval=True)
      async def bad(): 'x'
  except ValueError as e:
      assert 'read_only' in str(e).lower()
      print('OK')
  " | grep -q OK &&

  # 3. Import isolation (Behavioral Constraint 9) — importing the decorator module
  # must not pull in any co_cli.agent.* module.
  uv run python -c "
  import sys
  # Fresh import tree.
  for mod in list(sys.modules):
      if mod.startswith('co_cli'):
          del sys.modules[mod]
  import co_cli.tools._agent_tool  # noqa: F401
  leaked = sorted(m for m in sys.modules if m.startswith('co_cli.agent'))
  assert not leaked, f'_agent_tool pulled in agent modules: {leaked}'
  print('OK')
  " | grep -q OK &&

  # 4. Full suite still green (ToolInfo change is additive; MCP sites unaffected).
  uv run pytest tests/test_tool_registry.py tests/test_bootstrap.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task1.log | grep -q "passed"
success_signal: Importing a decorated tool function attaches a ToolInfo record; contradictory flag combinations raise ValueError; decorator module imports no agent internals.
```

Sub-steps:
1. Add `requires_config: str | None = None` to `ToolInfo` in `co_cli/deps.py`. Default `None` preserves every `discover_mcp_tools()` call site in `co_cli/agent/_mcp.py:87–94` unmodified — MCP records continue to construct without this field.
2. Create `co_cli/tools/_agent_tool.py` per the High-Level Design snippet. Export `agent_tool` and the attribute name constant `AGENT_TOOL_ATTR`. The module imports **only** `ToolInfo`, `ToolSourceEnum`, `VisibilityPolicyEnum` from `co_cli.deps` (Behavioral Constraint 9). Do not import `CoDeps`, `fork_deps`, or anything from `co_cli.agent.*`.
3. Run `tests/test_tool_registry.py` and `tests/test_bootstrap.py` locally (no code should depend on `requires_config` yet; change is additive).

Guard conditions mirrored from existing `_register_tool` assert (`_native_toolset.py:89`): `is_read_only=True` requires `is_concurrent_safe=True`. No new constraint introduced.

---

### ✓ DONE — TASK-2 — Decorate tool group A: always-visible & knowledge

```yaml
prerequisites: [TASK-1]
files:
  - co_cli/tools/capabilities.py
  - co_cli/tools/todo.py
  - co_cli/tools/knowledge.py
  - co_cli/tools/memory.py
done_when: |
  uv run python -c "
  from co_cli.tools.capabilities import check_capabilities
  from co_cli.tools.todo import read_todos, write_todos
  from co_cli.tools.knowledge import (
      search_knowledge, list_knowledge, read_article, search_articles,
      update_knowledge, append_knowledge, save_article,
  )
  from co_cli.tools.memory import search_memory
  for fn in (check_capabilities, read_todos, write_todos,
             search_knowledge, list_knowledge, read_article, search_articles,
             update_knowledge, append_knowledge, save_article, search_memory):
      assert hasattr(fn, '__co_tool_info__'), f'{fn.__name__} missing decorator'
  print('OK')
  " | grep -q OK
success_signal: All group-A native tools carry the decorator; importing the modules populates __co_tool_info__.
```

For each function, copy its current kwargs from `_native_toolset.py` into an `@agent_tool(...)` decorator above the function. Add `from co_cli.tools._agent_tool import agent_tool` and `from co_cli.deps import VisibilityPolicyEnum` to each file. Do not change function bodies.

Expected decorator parameters per tool (from current `_native_toolset.py`):
- `check_capabilities`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True`
- `write_todos`: `visibility=ALWAYS, is_concurrent_safe=True`
- `read_todos`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True`
- `search_knowledge`, `list_knowledge`, `read_article`, `search_articles`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True`
- `search_memory`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True`
- `update_knowledge`, `append_knowledge`, `save_article`: `visibility=DEFERRED, approval=True, is_concurrent_safe=True, retries=1`

Note: `co_cli/tools/session_search.py` is intentionally NOT in this list. It is a helper consumed by `search_memory` (see `co_cli/tools/memory.py:7,35`), not a tool registered in `_native_toolset.py`. No decoration required.

---

### ✓ DONE — TASK-3 — Decorate tool group B: files, execution, web, background

```yaml
prerequisites: [TASK-1]
files:
  - co_cli/tools/files.py
  - co_cli/tools/shell.py
  - co_cli/tools/execute_code.py
  - co_cli/tools/web.py
  - co_cli/tools/task_control.py
done_when: |
  uv run python -c "
  from co_cli.tools.files import glob, read_file, grep, write_file, patch
  from co_cli.tools.shell import run_shell_command
  from co_cli.tools.execute_code import execute_code
  from co_cli.tools.web import web_search, web_fetch
  from co_cli.tools.task_control import (
      start_background_task, check_task_status,
      cancel_background_task, list_background_tasks,
  )
  for fn in (glob, read_file, grep, write_file, patch,
             run_shell_command, execute_code, web_search, web_fetch,
             start_background_task, check_task_status,
             cancel_background_task, list_background_tasks):
      assert hasattr(fn, '__co_tool_info__'), f'{fn.__name__} missing decorator'
  # Spot-check policy correctness
  assert read_file.__co_tool_info__.max_result_size == 80_000
  assert read_file.__co_tool_info__.is_read_only is True
  assert run_shell_command.__co_tool_info__.max_result_size == 30_000
  assert write_file.__co_tool_info__.approval is True
  assert write_file.__co_tool_info__.retries == 1
  assert web_search.__co_tool_info__.retries == 3
  print('OK')
  " | grep -q OK
success_signal: All group-B native tools carry the decorator with policy matching the pre-refactor registration.
```

Per-tool parameters:
- `glob`, `grep`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True`
- `read_file`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True, max_result_size=80_000` (note the size override)
- `write_file`, `patch`: `visibility=DEFERRED, approval=True, retries=1`
- `run_shell_command`: `visibility=ALWAYS, is_concurrent_safe=True, max_result_size=30_000`
- `web_search`, `web_fetch`: `visibility=ALWAYS, is_read_only=True, is_concurrent_safe=True, retries=3`
- `execute_code`: `visibility=DEFERRED` (no other flags — `is_concurrent_safe=False` is the default and is correct here per current behavior)
- `start_background_task`: `visibility=DEFERRED, approval=True, is_concurrent_safe=True`
- `check_task_status`, `list_background_tasks`: `visibility=DEFERRED, is_read_only=True, is_concurrent_safe=True`
- `cancel_background_task`: `visibility=DEFERRED, is_concurrent_safe=True`

---

### ✓ DONE — TASK-4 — Decorate tool group C: delegation + integrations

```yaml
prerequisites: [TASK-1]
files:
  - co_cli/tools/agents.py
  - co_cli/tools/obsidian.py
  - co_cli/tools/google/drive.py
  - co_cli/tools/google/gmail.py
  - co_cli/tools/google/calendar.py
done_when: |
  uv run python -c "
  from co_cli.tools.agents import research_web, analyze_knowledge, reason_about
  from co_cli.tools.obsidian import list_notes, search_notes, read_note
  from co_cli.tools.google.drive import search_drive_files, read_drive_file
  from co_cli.tools.google.gmail import (
      list_gmail_emails, search_gmail_emails, create_gmail_draft,
  )
  from co_cli.tools.google.calendar import (
      list_calendar_events, search_calendar_events,
  )
  all_fns = (research_web, analyze_knowledge, reason_about,
             list_notes, search_notes, read_note,
             search_drive_files, read_drive_file,
             list_gmail_emails, search_gmail_emails, create_gmail_draft,
             list_calendar_events, search_calendar_events)
  for fn in all_fns:
      assert hasattr(fn, '__co_tool_info__'), f'{fn.__name__} missing decorator'
  # Integration gates
  for fn in (list_notes, search_notes, read_note):
      assert fn.__co_tool_info__.requires_config == 'obsidian_vault_path'
      assert fn.__co_tool_info__.integration == 'obsidian'
  for fn in (search_drive_files, read_drive_file):
      assert fn.__co_tool_info__.requires_config == 'google_credentials_path'
      assert fn.__co_tool_info__.integration == 'google_drive'
  for fn in (list_gmail_emails, search_gmail_emails, create_gmail_draft):
      assert fn.__co_tool_info__.requires_config == 'google_credentials_path'
      assert fn.__co_tool_info__.integration == 'google_gmail'
  for fn in (list_calendar_events, search_calendar_events):
      assert fn.__co_tool_info__.requires_config == 'google_credentials_path'
      assert fn.__co_tool_info__.integration == 'google_calendar'
  assert create_gmail_draft.__co_tool_info__.approval is True
  print('OK')
  " | grep -q OK
success_signal: Delegation and integration tools carry decorators; integration gates declare requires_config matching the current imperative gating.
```

Per-tool parameters:
- `research_web`, `analyze_knowledge`, `reason_about`: `visibility=DEFERRED, is_concurrent_safe=True`
- `list_notes`, `search_notes`, `read_note`: `visibility=DEFERRED, is_read_only=True, is_concurrent_safe=True, integration="obsidian", requires_config="obsidian_vault_path"`
- `search_drive_files`, `read_drive_file`: `visibility=DEFERRED, is_read_only=True, is_concurrent_safe=True, integration="google_drive", requires_config="google_credentials_path", retries=3`
- `list_gmail_emails`, `search_gmail_emails`: `visibility=DEFERRED, is_read_only=True, is_concurrent_safe=True, integration="google_gmail", requires_config="google_credentials_path", retries=3`
- `list_calendar_events`, `search_calendar_events`: `visibility=DEFERRED, is_read_only=True, is_concurrent_safe=True, integration="google_calendar", requires_config="google_credentials_path", retries=3`
- `create_gmail_draft`: `visibility=DEFERRED, approval=True, is_concurrent_safe=True, integration="google_gmail", requires_config="google_credentials_path", retries=1`

---

### ✓ DONE — TASK-5 — Refactor `_native_toolset.py` to data-driven loop + parity dump script

```yaml
prerequisites: [TASK-2, TASK-3, TASK-4]
files:
  - co_cli/agent/_native_toolset.py
  - scripts/dump_tool_index.py  # new — committed parity script
done_when: |
  # 1. Named test files pass under the refactor.
  uv run pytest tests/test_bootstrap.py tests/test_tool_registry.py tests/test_capabilities.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log | grep -q "passed" &&

  # 2. NATIVE_TOOLS is exported, populated, and all entries are decorated.
  # Config constructed via tests/_settings.py::make_settings to avoid env leakage
  # (OBSIDIAN_VAULT_PATH / GOOGLE_CREDENTIALS_PATH would otherwise flip gate behavior).
  uv run python -c "
  from tests._settings import make_settings
  from co_cli.agent._native_toolset import _build_native_toolset, NATIVE_TOOLS
  assert isinstance(NATIVE_TOOLS, tuple) and len(NATIVE_TOOLS) >= 30
  for fn in NATIVE_TOOLS:
      assert hasattr(fn, '__co_tool_info__'), fn.__name__

  # With no integration config, integration tools are NOT in index.
  config_bare = make_settings(obsidian_vault_path=None, google_credentials_path=None)
  _, index_bare = _build_native_toolset(config_bare)
  assert 'list_notes' not in index_bare, 'obsidian tool leaked with no vault path'
  assert 'search_drive_files' not in index_bare, 'google tool leaked with no credentials'
  assert 'read_file' in index_bare
  assert index_bare['read_file'].max_result_size == 80_000

  # With obsidian_vault_path set, obsidian tools are registered, google absent.
  config_obs = make_settings(obsidian_vault_path='/tmp/fake-vault', google_credentials_path=None)
  _, index_obs = _build_native_toolset(config_obs)
  assert 'list_notes' in index_obs
  assert 'search_drive_files' not in index_obs
  print('OK')
  " | grep -q OK &&

  # 3. Parity script runs and produces deterministic output (for pre/post diff).
  uv run python scripts/dump_tool_index.py --obsidian-vault-path=/x --google-credentials-path=/y > /tmp/tool-index-after.txt &&
  test -s /tmp/tool-index-after.txt
success_signal: Bootstrap runs under the data-driven loop; integration gates respect requires_config; the full tool_index matches pre-refactor policies.
```

Sub-steps:
1. Replace `_build_native_toolset()` body with the High-Level Design snippet (NATIVE_TOOLS tuple + loop).
2. Delete the integration `if` blocks (`if config.obsidian_vault_path:` and `if config.google_credentials_path:`).
3. Delete the per-call `_register_tool` inner closure (`_native_toolset.py:78–114`) — the loop replaces it entirely. **Crucially: the pre-refactor `assert not (is_read_only and not is_concurrent_safe)` at line 89 is DELETED, not duplicated — the decorator now owns that invariant at import time (Behavioral Constraint 6). Duplicating both would run the same guard twice at different times.**
4. `_approval_resume_filter` at the top of the file stays untouched.
5. Create `scripts/dump_tool_index.py`: a simple Python CLI using `argparse` that accepts `--obsidian-vault-path` and `--google-credentials-path` as optional string flags, constructs `Settings` via `make_settings`-style direct construction (do not depend on `tests/`), calls `_build_native_toolset`, and prints one tab-separated line per tool sorted by name: `name\tapp=<bool>\tvis=<val>\tro=<bool>\tcs=<bool>\tintg=<str>\tret=<int|None>\tmax=<int>`. This is committed so the next refactor reuses it; it is the canonical regression diff for tool-policy changes.
6. Run `scripts/quality-gate.sh lint --fix` and the named pytest files.

**Behavioral parity check (regression surface — mandatory before commit):** On the `main` branch, run `uv run python scripts/dump_tool_index.py --obsidian-vault-path=/x --google-credentials-path=/y > /tmp/tool-index-before.txt`. On the refactor branch, run the same command producing `/tmp/tool-index-after.txt`. Then `diff /tmp/tool-index-before.txt /tmp/tool-index-after.txt` — the diff must be empty. Any non-empty diff is a bug in the decoration tasks; fix the affected decorator kwargs before committing TASK-5.

---

### ✓ DONE — TASK-6 — Behavioral tests for gating + policy parity

```yaml
prerequisites: [TASK-5]
files:
  - tests/test_tool_registry.py
done_when: |
  uv run pytest tests/test_tool_registry.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task6.log | grep -q "passed"
success_signal: Regression tests assert (a) requires_config gates register/unregister tools correctly across multiple config permutations; (b) tool_index policy fields match expectations for representative tools; (c) native and MCP tools coexist in the merged tool_index.
```

Tests to add (extend `tests/test_tool_registry.py`; do not create a new file unless the existing file does not fit semantically):

1. **`test_requires_config_gates_integration_tools`** — exercise three permutations using `tests/_settings.py::make_settings` (which shields from `OBSIDIAN_VAULT_PATH` / `GOOGLE_CREDENTIALS_PATH` env vars):
   - `(obsidian=None, google=None)`: assert obsidian and google tool names absent from `tool_index`.
   - `(obsidian="/tmp/x", google=None)`: assert obsidian tools present, google tools absent.
   - `(obsidian=None, google="/tmp/y")`: assert google tools present, obsidian tools absent.

2. **`test_tool_index_policies_match_expectation`** — build with `make_settings(obsidian_vault_path="/x", google_credentials_path="/y")`; assert specific known non-default policies that are most likely to regress silently during decoration:
   - `read_file.max_result_size == 80_000`
   - `run_shell_command.max_result_size == 30_000`
   - `write_file.approval is True and write_file.retries == 1`
   - `patch.approval is True and patch.retries == 1`
   - `web_search.retries == 3 and web_fetch.retries == 3`
   - `create_gmail_draft.approval is True and create_gmail_draft.retries == 1` (the only approval-required integration tool; highest regression risk)
   - `search_drive_files.retries == 3`
   Do not re-assert every tool — the parity dump script (TASK-5) already provides a full-matrix check.

3. **`test_native_and_mcp_both_populate_tool_index`** — build toolset, verify at least one `ToolSourceEnum.NATIVE` entry; if `tests/test_tool_registry.py` already has MCP coverage, extend rather than duplicate. Verify the merged `tool_index` contains records of both sources when both paths have populated it (construct the test alongside the existing MCP discovery pattern already in the file).

All tests must use real `CoDeps`/`Settings` per CLAUDE.md's "Real dependencies only — no fakes" rule. Always construct Settings via `make_settings(...)` passing explicit values for integration fields — never bare `Settings()`, which inherits environment.

**Note on the missing-decorator invariant:** There is no dedicated test for "function in NATIVE_TOOLS without `__co_tool_info__` raises TypeError." The bootstrap path raises this every time the system starts. Every pytest that constructs `CoDeps`, boots the agent, or calls `build_tool_registry(config)` exercises it. A dedicated structural test would duplicate that invariant without adding coverage — per CLAUDE.md's "behavior over structure" rule, we rely on the existing boot path.

## Testing

**Scope of verification at each task completion:** run only the directly-affected test files. Full suite (`scripts/quality-gate.sh full`) runs after TASK-6 as the ship gate.

Per-task test invocations:

- TASK-1: inline Python check (see `done_when`)
- TASK-2–4: inline Python checks verifying `__co_tool_info__` attachment; also `uv run pytest tests/test_bootstrap.py -x` to confirm no import breakage
- TASK-5: `uv run pytest tests/test_bootstrap.py tests/test_tool_registry.py tests/test_capabilities.py -x` + the behavioral parity check (tool_index diff)
- TASK-6: `uv run pytest tests/test_tool_registry.py -x`, then full suite

**Regression surface check (mandatory before TASK-5 commit):** capture pre/post `tool_index` snapshots with `obsidian_vault_path` and `google_credentials_path` set; diff must be empty. This guards against any silent policy drift during decoration.

**Tool dispatch still works end-to-end:** `tests/test_tool_calling_functional.py` must pass unchanged — it exercises agent → tool dispatch through the real orchestration path. No accommodation should be needed.

**Approval flow:** `tests/test_approvals.py` must pass — it exercises approval prompts for approval=True tools, which flow through `tool_index[name].approval`. No expected change.

## Open Questions

*(All questions were resolved by reading source during the validation phase.)*

- Does `session_search.py` expose a registered tool? — **No.** Verified at `co_cli/tools/memory.py:7,35`: `session_search` is a helper imported and called by `search_memory`, not registered in `_native_toolset.py`. Excluded from TASK-2's `files:` list.
- Do delegation tools (research_web, analyze_knowledge, reason_about) need the decorator given they bypass it in the delegation path? — **Yes, decorate them.** They are registered in the orchestrator's native toolset (`_native_toolset.py:233`) in addition to being listed in `tool_fns` for delegation. The decorator is consumed by orchestrator registration; the delegation path passes `requires_approval=False` explicitly and ignores the attribute.

## Final — Team Lead

Plan approved.

Both blocking critiques (CD-M-1 circular-import risk, CD-M-2 env-leaky `Settings()`) have concrete fixes applied. Both PO blocking concerns (PO-M-1 premature tuple generalization, PO-M-2 unnecessary test helper) have been tightened to match YAGNI and behavior-over-structure principles. All minor issues folded in. The plan is now smaller: one fewer test, one fewer helper, one simpler type, and one committed parity script that pays forward.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev agent-tool-decorator`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/tools/_agent_tool.py` | Invariant checks correct; import isolation holds; no dead code | clean | TASK-1 |
| `co_cli/deps.py:88` | `requires_config` not included in `dump_tool_index.py` output — parity script blind to this field's value | minor | TASK-1 |
| `co_cli/agent/_native_toolset.py:140` | `getattr(config, requires_config, None)` — typo in field name fails silently rather than raising | minor | TASK-5 |
| `co_cli/tools/execute_code.py:12` | `approval=False` in metadata contradicts manual `ApprovalRequired` in body — pre-existing inconsistency preserved | minor (pre-existing) | TASK-3 |
| `scripts/dump_tool_index.py` | `requires_config` column absent from dump output | minor | TASK-5 |
| `tests/test_tool_registry.py` | No test for BC-2 (missing decorator → TypeError); intentional per plan's boot-path coverage reasoning | minor (intentional) | TASK-6 |
| All decorated tool files | `__co_tool_info__` present on all; invariants hold; no stale imports; no module-level side effects | clean | TASK-2/3/4 |

**Overall: clean / 0 blocking / 5 minor**

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | decorator attaches correct ToolInfo; ValueError on contradictory flags; import isolation; 34 tests pass | ✓ pass |
| TASK-2 | all 11 group-A tools carry `__co_tool_info__` | ✓ pass |
| TASK-3 | all 13 group-B tools carry `__co_tool_info__` with correct spot-check policies | ✓ pass |
| TASK-4 | all 13 group-C tools carry `__co_tool_info__`; integration gates declare correct requires_config | ✓ pass |
| TASK-5 | pytest 39 pass; config gating correct; parity diff empty; dump script runs | ✓ pass |
| TASK-6 | pytest tests/test_tool_registry.py 19 pass | ✓ pass |

**Tests:** full suite — 619 passed, 0 failed (1 flaky timing test passed on re-run)
**Independent Review:** clean / 0 blocking / 5 minor
**Doc Sync:** fixed (added `_agent_tool.py` to tools.md section 1 infrastructure list)

**Overall: DELIVERED**
All 6 tasks shipped. Policy declaration moved from registration site to definition site. Behavioral parity confirmed via empty diff on `dump_tool_index.py` pre/post snapshots. Full suite green.

---

## Implementation Review — 2026-04-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | decorator attaches ToolInfo; ValueError on contradictory flags; import isolation | ✓ pass | `_agent_tool.py:41-54` — ToolInfo constructed with all declared fields; `_agent_tool.py:33-36` — invariant guards; import isolation confirmed at runtime |
| TASK-2 | all 11 group-A tools carry `__co_tool_info__` | ✓ pass | `capabilities.py:16`, `todo.py:26`, `knowledge.py:206,490,570,786,843,981,1042`, `memory.py:11` — decorators present, spot-checked |
| TASK-3 | all 13 group-B tools carry `__co_tool_info__` with correct policies | ✓ pass | `files.py:56,136,256,353,539`, `shell.py:14`, `execute_code.py:12`, `web.py:419,515`, `task_control.py:28` — decorators confirmed; `read_file.max_result_size=80000`, `run_shell_command.max_result_size=30000` verified live |
| TASK-4 | all 13 group-C tools carry `__co_tool_info__`; integration gates correct | ✓ pass | `agents.py:163,269,322`, `obsidian.py:152,216,310`, `drive.py:62,128`, `gmail.py:50,90,141`, `calendar.py:82,153` — requires_config verified live |
| TASK-5 | data-driven loop; config gating; 37 tools in NATIVE_TOOLS; parity diff empty | ✓ pass | `_native_toolset.py:46-96` — flat NATIVE_TOOLS tuple; `_native_toolset.py:133-152` — data-driven loop; config-gating verified live (list_notes absent without vault path) |
| TASK-6 | test_tool_registry.py: 3 new tests for gating + policy parity + source coexistence | ✓ pass | `test_tool_registry.py:319,351,369` — all three tests confirmed; full suite 619 passed |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| C901 complexity (13 > 12): `_generate_report` function | `scripts/llm_call_audit.py:278` | blocking (pre-existing) | Extracted `_api_finding`, `_finish_finding`, `_cut_finding` helpers; complexity reduced to ≤10 |
| Wrong setting name in Config table: `knowledge_dir` should be `knowledge_path` | `docs/specs/tools.md:177` | minor (pre-existing) | Fixed — setting name corrected to `knowledge_path` |

### Tests
- Command: `uv run pytest -v`
- Result: 619 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: full (delivery touched shared registration module and ToolInfo schema)
- Result: fixed — `knowledge_path` setting name corrected in tools.md Config table (pre-existing inaccuracy)

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM Online, Shell Active, Google Configured, Obsidian Not Configured (requires_config gate working correctly with no vault path), MCP 1 ready
- `done_when` re-execution: TASK-1 (decorator + isolation), TASK-3 (spot-checks), TASK-5 (config gating, 37 tools) — all pass
- `success_signal` verified: tool policy declared at definition site; contradictory flags raise ValueError at import time; integration tools absent from index when config absent

### Overall: PASS
All 6 tasks verified with file:line evidence. Two pre-existing issues fixed (C901 complexity in audit script, stale setting name in spec). 619 tests green. Behavioral verification clean. Ship-ready.
