Task type: code-refactor

# Plan: Tool File Categorization — Category-Prefix Directory per Tool Group

## Context

`co_cli/tools/` has two layouts coexisting: four clean category packages (`files/`, `memory/`,
`web/`, `google/`) where tool files live under `<category>/` directories, and nine flat tool
files at the top level (`shell.py`, `todo.py`, `task_control.py`, `agents.py`, `obsidian.py`,
`execute_code.py`, `capabilities.py`, `user_input.py`, `agent_delegate.py`). The convention is
already established by the category packages — the flat files are stragglers.

**Note on peer practice:** A survey of hermes-agent, opencode, fork-claude-code, openclaw, and
codex shows that flat-file and one-dir-per-tool are both common patterns; category-directory
layout is not a universal peer convention. The category-directory approach here is a co-cli
internal convention established by the existing `files/`, `memory/`, `web/`, `google/` packages
and is being extended consistently to all remaining tool files.

The infrastructure files (`agent_tool.py`, `approvals.py`, `background.py`, `categories.py`,
`deferred_prompt.py`, `display.py`, `lifecycle.py`, `resource_lock.py`, `shell_backend.py`,
`tool_io.py`, `_shell_env.py`, `_shell_policy.py`) are **not** tool files and are used broadly
outside `tools/` — they stay at the top level.

## Problem & Outcome

**Problem:** Flat tool files break the `<category>/<file>.py` layout convention established by
the existing packages, making it harder to locate tool implementations and reason about which
files define agent-visible tools vs. shared infrastructure.

**Outcome:** All files that define `@agent_tool` functions live under a named category
directory. Flat tool files are gone. Import sites updated. No tool names, signatures, or
behavior change.

## Scope

**Files to move (tool-defining files only):**

| Current path | New path | Category |
|---|---|---|
| `co_cli/tools/shell.py` | `co_cli/tools/shell/execute.py` | shell |
| `co_cli/tools/todo.py` | `co_cli/tools/todo/rw.py` | todo |
| `co_cli/tools/task_control.py` | `co_cli/tools/tasks/control.py` | tasks |
| `co_cli/tools/agents.py` | `co_cli/tools/agents/delegation.py` | agents |
| `co_cli/tools/agent_delegate.py` | **deleted** (dead code — confirmed) | agents |
| `co_cli/tools/obsidian.py` | `co_cli/tools/obsidian/tools.py` | obsidian |
| `co_cli/tools/execute_code.py` | `co_cli/tools/code/execute.py` | code |
| `co_cli/tools/capabilities.py` | `co_cli/tools/system/capabilities.py` | system |
| `co_cli/tools/user_input.py` | `co_cli/tools/system/user_input.py` | system |

**Sub-file naming rationale:** Existing category packages use descriptive sub-file names
(`files/read.py`, `files/write.py`, `memory/recall.py`) rather than mirroring the directory
name. `todo/todo.py`, `agents/agents.py`, `obsidian/obsidian.py` would break this pattern.
`rw.py` (read+write), `delegation.py` (the mechanism), and `tools.py` (list/search/read set)
follow the descriptive convention.

**Infrastructure files that stay at top level (cross-cutting, used outside `tools/`):**
`agent_tool.py`, `approvals.py`, `background.py`, `categories.py`, `deferred_prompt.py`,
`display.py`, `lifecycle.py`, `resource_lock.py`, `shell_backend.py`, `tool_io.py`,
`_shell_env.py`, `_shell_policy.py`

**Key observation:** Imports inside the moved files (e.g. `from co_cli.tools._shell_policy`,
`from co_cli.tools.background`, `from co_cli.tools.approvals`) all target infrastructure that
stays at the top level — no intra-file imports need updating. Only the **import sites** that
reference the moved files change.

**Non-goals:**
- Tool names, signatures, or behavior are unchanged.
- `background.py` and `shell_backend.py` are infrastructure, not tool files — not moved even
  though they are shell/task-adjacent.
- No changes to `docs/specs/` as a result of this refactor (pure structural rename).

## Import Sites Affected

Primary:
- `co_cli/agent/_native_toolset.py` — imports from shell, todo, task_control, agents, obsidian,
  execute_code, capabilities, user_input

Tests (where they import directly from moved files):
- `tests/test_flow_agent_delegation.py` — `from co_cli.tools.agents import ...` (was `agent_delegate`)
- `tests/test_flow_capability_checks.py` — `from co_cli.tools.capabilities import capabilities_check`

All other tests import `shell_backend`, `tool_io`, `approvals`, `resource_lock`, `background` —
these stay at top level, no test changes needed for them.

## Implementation Plan

- **✓ DONE — TASK-0: Delete `agent_delegate.py`**
  - **files:** `co_cli/tools/agent_delegate.py`
  - Decision: delete. Confirmed dead code — not imported by `_native_toolset.py` or any live
    code path; duplicate of `agents.py` with near-identical content and line count.
  - **done_when:** `co_cli/tools/agent_delegate.py` is deleted from the repository.
  - **prerequisites:** []

- **✓ DONE — TASK-1: Scaffold 7 new category packages**
  - **files:** `co_cli/tools/shell/__init__.py`, `co_cli/tools/todo/__init__.py`,
    `co_cli/tools/tasks/__init__.py`, `co_cli/tools/agents/__init__.py`,
    `co_cli/tools/obsidian/__init__.py`, `co_cli/tools/code/__init__.py`,
    `co_cli/tools/system/__init__.py`
  - **done_when:** All 7 `__init__.py` files exist with a single-line module docstring (no
    imports, per `__init__.py` rule).
  - **prerequisites:** [TASK-0]

- **✓ DONE — TASK-2: Move `shell`, `todo`, `tasks` tool files**
  - Move `shell.py` → `shell/execute.py`
  - Move `todo.py` → `todo/rw.py`
  - Move `task_control.py` → `tasks/control.py`
  - **files:** `co_cli/tools/shell/execute.py`, `co_cli/tools/todo/rw.py`,
    `co_cli/tools/tasks/control.py` (old flat files deleted)
  - **done_when:** Old flat files gone; new paths importable; `from co_cli.tools.shell.execute
    import shell` resolves.
  - **prerequisites:** [TASK-1]

- **✓ DONE — TASK-3: Move `agents`, `obsidian` tool files**
  - Move `agents.py` → `agents/delegation.py`
  - Move `obsidian.py` → `obsidian/tools.py`
  - **files:** `co_cli/tools/agents/delegation.py`, `co_cli/tools/obsidian/tools.py`
  - **done_when:** Old flat files gone; new paths importable.
  - **prerequisites:** [TASK-0, TASK-1]

- **✓ DONE — TASK-4: Move `code`, `system` tool files**
  - Move `execute_code.py` → `code/execute.py`
  - Move `capabilities.py` → `system/capabilities.py`
  - Move `user_input.py` → `system/user_input.py`
  - **files:** `co_cli/tools/code/execute.py`, `co_cli/tools/system/capabilities.py`,
    `co_cli/tools/system/user_input.py`
  - **done_when:** Old flat files gone; new paths importable.
  - **prerequisites:** [TASK-1]

- **✓ DONE — TASK-5: Update all import sites**
  - Update `co_cli/agent/_native_toolset.py`: update imports for all moved modules using new
    paths (`shell.execute`, `todo.rw`, `tasks.control`, `agents.delegation`, `obsidian.tools`,
    `code.execute`, `system.capabilities`, `system.user_input`).
  - Update `tests/test_flow_agent_delegation.py`: `co_cli.tools.agents` →
    `co_cli.tools.agents.delegation`.
  - Update `tests/test_flow_capability_checks.py`: `co_cli.tools.capabilities` →
    `co_cli.tools.system.capabilities`.
  - **files:** `co_cli/agent/_native_toolset.py`, `tests/test_flow_agent_delegation.py`,
    `tests/test_flow_capability_checks.py`
  - **done_when:** No `co_cli.tools.shell`, `co_cli.tools.todo`, `co_cli.tools.task_control`,
    `co_cli.tools.agents`, `co_cli.tools.obsidian`, `co_cli.tools.execute_code`,
    `co_cli.tools.capabilities`, or `co_cli.tools.user_input` import paths remain anywhere
    in the codebase.
  - **prerequisites:** [TASK-2, TASK-3, TASK-4]

- **✓ DONE — TASK-6: Full test suite verification**
  - `uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-categorization.log`
  - **files:** (none — verification only)
  - **done_when:** All tests pass. Zero import errors.
  - **prerequisites:** [TASK-5]

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | `agent_delegate.py` deleted | ✓ pass |
| TASK-1 | All 7 `__init__.py` exist with docstrings | ✓ pass |
| TASK-2 | `shell/execute.py`, `todo/rw.py`, `tasks/control.py` importable; old flat files gone | ✓ pass |
| TASK-3 | `agents/delegation.py`, `obsidian/tools.py` importable; old flat files gone | ✓ pass |
| TASK-4 | `code/execute.py`, `system/capabilities.py`, `system/user_input.py` importable; old flat files gone | ✓ pass |
| TASK-5 | No old flat import paths remain in codebase | ✓ pass |
| TASK-6 | All tests pass, zero import errors | ✓ pass |

**Extra files touched (not in original plan `files:` lists):**
- `tests/test_flow_agent_delegation.py` — imported deleted `agent_delegate`; updated to `agents.delegation`
- `tests/test_flow_capability_checks.py` — imported moved `capabilities`; updated to `system.capabilities`

**Tests:** full suite — 67 passed, 0 failed
**Doc Sync:** fixed (`core-loop.md` and `compaction.md` — `tools/shell.py` → `tools/shell/execute.py`)

**Overall: DELIVERED**
All 9 flat tool files moved to category packages; import sites updated; 2 missed import sites found and fixed; full test suite green.

## Implementation Review — 2026-04-30

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | `agent_delegate.py` deleted | ✓ pass | `ls co_cli/tools/agent_delegate.py` → not found |
| TASK-1 | All 7 `__init__.py` with docstrings, no imports | ✓ pass | Each file is docstring-only (e.g. `shell/__init__.py:1` — `"""Shell execution tools."""`) |
| TASK-2 | Old flat files gone; new paths importable | ✓ pass | `shell/execute.py:19` — `async def shell(...)`, `todo/rw.py:27` — `def todo_write(...)`, `tasks/control.py:29` — `async def task_start(...)` |
| TASK-3 | Old flat files gone; new paths importable | ✓ pass | `agents/delegation.py:164` — `async def web_research(...)`, `obsidian/tools.py:173` — `def obsidian_search(...)` |
| TASK-4 | Old flat files gone; new paths importable | ✓ pass | `code/execute.py:13` — `async def code_execute(...)`, `system/capabilities.py:142` — `async def capabilities_check(...)`, `system/user_input.py:14` — `async def clarify(...)` |
| TASK-5 | No old flat import paths remain | ✓ pass | `_native_toolset.py:13–40` — all imports use new paths; grep for 8 old paths returned zero results |
| TASK-6 | All tests pass | ✓ pass | 67 passed, 0 failed |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 67 passed, 0 failed
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync
- Scope: narrow — structural rename only, no API changes; `core-loop.md` and `compaction.md` already updated by orchestrate-dev
- Result: clean — no stale paths remain in `docs/specs/`

### Behavioral Verification
- `_build_native_toolset(settings)`: ✓ 24 tools registered from new paths — all moved tools (`shell`, `todo_read`, `todo_write`, `task_*`, `knowledge_analyze`, `reason`, `web_research`, `code_execute`, `capabilities_check`, `clarify`) confirmed live
- No user-facing surface changed (tool names, signatures, behavior unchanged) — CLI smoke test skipped

### Overall: PASS
Pure structural refactor: all 9 flat tool files in new category packages, all import sites updated, lint clean, 67/67 tests green, doc sync verified, toolset builds and registers correctly.
