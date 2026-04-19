# Plan: Tool Naming Rename — shell, clarify, task_*, todo_*

**Task type:** refactor (pure renames — no behavior change, no schema change)

---

## Context

Peer survey (docs/reference/RESEARCH-tools-*.md) and naming analysis (conversation 2026-04-19) identified four co-cli tool names as verbose outliers against converged community consensus. All four are Python function names — pydantic-ai derives the LLM-visible tool name from `fn.__name__` (confirmed: `co_cli/tools/_agent_tool.py:39`). Renaming the function is sufficient; no separate registration string exists.

Naming rule applied uniformly: noun-first prefix for all multi-tool domains (`task_*`, `todo_*`) so tool lists group by domain and the model learns one selection heuristic per domain, not one per tool.

Current state validated: no active exec-plan conflicts. No stale DONE tasks in related active plans. `test_core_loop_interrupts.py` is untracked and references old tool names — must be updated. (`test_skill_env.py` is untracked but contains no old tool name references — no update needed.)

Regression surface check:
- `"run_shell_command"` appears as a hardcoded string in: `shell.py` (logger.debug), `tool_approvals.py` (if-branch + display strings + function docstring), `tool_categories.py` (frozenset), `tool_display.py` (dict key), `_history.py` (two checks), `web.py` (docstring), `execute_code.py` (docstring), and 8 test files: `test_shell.py`, `test_agent.py`, `test_tool_registry.py`, `test_tool_prompt_discovery.py`, `test_approvals.py`, `test_display.py`, `test_tool_calling_functional.py`, `test_commands.py`. Also 2 eval files: `evals/eval_file_tools.py`, `evals/eval_ollama_tool_search.py`.
- Background task names are string-keyed in `_deferred_tool_prompt.py`, `tool_display.py`, `_commands.py`, and 4 test files: `test_background.py`, `test_agent.py`, `test_tool_prompt_discovery.py`, `test_tool_calling_functional.py`. Also in `evals/eval_ollama_tool_search.py`.
- `request_user_input` appears in `tool_approvals.py` (function docstring), `user_input.py` (module docstring + function), `test_user_input.py`, `test_tool_calling_functional.py`.
- `write_todos`/`read_todos` appear in `_native_toolset.py`, `todo.py`, `test_todo.py`, `test_tool_registry.py`.

---

## Problem & Outcome

**Problem:** ALWAYS-visibility tool names `run_shell_command` (17 chars, 4 tokens) and `request_user_input` (19 chars, 5 tokens) anchor on weak first tokens (`run`, `request`) and are verbose outliers vs peer consensus. Background task 4-tuple uses verb-first inconsistently with long prefix `_background_task`. Todo pair uses verb-first while background tasks are `check_task_status` (noun-verb mix).

**Failure cost:** Verbose names increase schema token budget every turn; anchor on weak first-tokens degrades small-model tool selection accuracy; inconsistent prefix patterns (verb-first vs noun-verb) force the model to learn different selection heuristics per domain.

**Outcome:** After this rename:
- `shell` (1 token) replaces `run_shell_command` — matches hermes/opencode/fork-cc consensus
- `clarify` (1 token) replaces `request_user_input` — matches hermes/opencode/gemini consensus
- `task_start`, `task_status`, `task_cancel`, `task_list` — noun-first prefix, consistent grouping
- `todo_write`, `todo_read` — noun-first, consistent with `task_*` pattern; keeps split schema (no action-dispatch complexity)

---

## Scope

**In:** Rename 8 tool functions and update all string references across source, context modules, tests, and evals. No behavior change. No schema change. No new tools.

**Out:** Consolidating `todo_write`/`todo_read` into a single action-dispatched `todo` tool — that is a schema change and belongs in a separate plan. Renaming `web_search`, `web_fetch`, `read_file`, `write_file`, `glob`, `grep`, `patch` — all match or near-match consensus, no rename needed.

---

## Behavioral Constraints

1. The LLM-visible tool name is `fn.__name__` — renaming the Python function IS the rename.
2. Python module filenames (`shell.py`, `user_input.py`, `task_control.py`, `todo.py`) are NOT renamed — only function names inside change. Avoids cascading import churn.
3. `requires_approval` policy, `is_concurrent_safe`, `visibility`, and all runtime logic are unchanged.
4. All docstring cross-references between tools must be updated to use new names — stale cross-refs silently misdirect the model.
5. The logger.debug call in `shell.py:56` hardcodes `"run_shell_command"` as a string attribute — must be updated to `"shell"`.
6. `resolve_approval_subject` in `tool_approvals.py` branches on `tool_name == "run_shell_command"` — must be updated to `"shell"`.
7. `COMPACTABLE_TOOLS` frozenset in `tool_categories.py` references `"run_shell_command"` — must be updated.
8. `_history.py` has two checks `part.tool_name == "run_shell_command"` — must be updated.
9. Evals in `evals/` check actual LLM-visible tool names in pass/fail logic — old tool name strings in eval assertions will silently misgrade after rename.
10. `co_cli/prompts/rules/` files are injected into the system prompt every turn — stale tool names in these files cause the model to call non-existent tools. Must be updated in the same pass as the renames (TASK-3).

---

## High-Level Design

Four independent rename groups, applied layer by layer:

```
Layer 0: co_cli/tools/  — rename function definitions + update intra-module strings
Layer 1: co_cli/agent/ + co_cli/context/ — update imports, frozensets, dicts, if-branches
Layer 2: cross-module docstring/string references in tools and context internals
Layer 3: tests + evals — update imports and string assertions
```

Layers 1 and 2 are independent (no shared files) and can be done in a single pass.
Layer 3 depends on Layers 0-2 being complete.

---

## Implementation Plan

### ✓ DONE — TASK-1: Rename tool function definitions (Layer 0)

Rename the 8 tool functions in their source files. Update only the function name, the module docstring, and any string literals inside the same file that reference the old function name.

**files:**
- `co_cli/tools/shell.py` — rename `run_shell_command` → `shell`; update `logger.debug` hardcoded string at line 56 (`"run_shell_command"` → `"shell"`); docstring cross-ref to `start_background_task` unchanged (different tool, not being renamed here)
- `co_cli/tools/user_input.py` — rename `request_user_input` → `clarify`; update module docstring
- `co_cli/tools/task_control.py` — rename `start_background_task` → `task_start`, `check_task_status` → `task_status`, `cancel_background_task` → `task_cancel`, `list_background_tasks` → `task_list`; update all intra-file docstring cross-refs (e.g. "returned by start_background_task" → "returned by task_start", "Prefer check_task_status" → "Prefer task_status")
- `co_cli/tools/todo.py` — rename `write_todos` → `todo_write`, `read_todos` → `todo_read`; update module docstring and intra-file cross-refs

**done_when:** `grep -rn "run_shell_command\|request_user_input\|start_background_task\|check_task_status\|cancel_background_task\|list_background_tasks\|write_todos\|read_todos" co_cli/tools/shell.py co_cli/tools/user_input.py co_cli/tools/task_control.py co_cli/tools/todo.py` returns 0 matches

**success_signal:** N/A (no user-visible change; agent names visible only post-TASK-2 integration)

---

### ✓ DONE — TASK-2: Update agent + context layer (Layer 1, part A)

Update all import aliases, frozenset entries, dict keys, and conditional branches that hard-code old tool names.

**files:**
- `co_cli/agent/_native_toolset.py` — update all 8 import names and NATIVE_TOOLS tuple entries
- `co_cli/context/tool_approvals.py` — update `if tool_name == "run_shell_command":` (line 94), both display strings (lines 103, 105), and function docstring at line 87 to `"shell"`
- `co_cli/context/tool_categories.py` — update `COMPACTABLE_TOOLS` frozenset: `"run_shell_command"` → `"shell"`
- `co_cli/context/tool_display.py` — update dict keys: `"run_shell_command"` → `"shell"`, `"start_background_task"` → `"task_start"`, `"check_task_status"` → `"task_status"`

**prerequisites:** [TASK-1]

**done_when:** `grep -rn "run_shell_command\|request_user_input\|start_background_task\|check_task_status\|cancel_background_task\|list_background_tasks\|write_todos\|read_todos" co_cli/agent/_native_toolset.py co_cli/context/tool_approvals.py co_cli/context/tool_categories.py co_cli/context/tool_display.py` returns 0 matches

**success_signal:** N/A

---

### ✓ DONE — TASK-3: Update context internals + source cross-refs + prompt rules + README (Layer 1, part B)

Update string references in context internals, docstring cross-refs in other tool modules, LLM-visible prompt rule files, and README.

**files:**
- `co_cli/context/_deferred_tool_prompt.py` — update `_NATIVE_CATEGORIES` and `_NATIVE_CATEGORY_REPS` dict keys: all 4 background task names
- `co_cli/context/_history.py` — update 2 occurrences of `part.tool_name == "run_shell_command"` → `"shell"` (lines ~824, ~840)
- `co_cli/commands/_commands.py` — update `"start_background_task"` string reference (line 285) → `"task_start"`
- `co_cli/tools/execute_code.py` — update docstring cross-ref: `"use run_shell_command instead"` → `"use shell instead"`
- `co_cli/tools/web.py` — update docstring cross-ref: `"run_shell_command: curl"` → `"shell: curl"`
- `co_cli/prompts/rules/04_tool_protocol.md` — update `run_shell_command` → `shell` (line ~45); LLM-visible instruction — stale name causes tool selection mismatch every turn
- `co_cli/prompts/rules/05_workflow.md` — update `write_todos` → `todo_write`, `read_todos` → `todo_read` (lines ~30-31); LLM-visible instruction — same risk
- `README.md` — update Shell capability row: `run_shell_command` → `shell` (line ~292)

**prerequisites:** [TASK-1]

**done_when:** `grep -rn "run_shell_command\|start_background_task\|check_task_status\|cancel_background_task\|list_background_tasks\|write_todos\|read_todos" co_cli/context/_deferred_tool_prompt.py co_cli/context/_history.py co_cli/commands/_commands.py co_cli/tools/execute_code.py co_cli/tools/web.py co_cli/prompts/rules/04_tool_protocol.md co_cli/prompts/rules/05_workflow.md README.md` returns 0 matches

**success_signal:** N/A

---

### ✓ DONE — TASK-4: Update primary test files (Layer 3, part A)

Update test imports and all string assertions in the five highest-impact test files.

**files:**
- `tests/test_shell.py` — update `from co_cli.tools.shell import run_shell_command` → `shell`; rename all call sites and references
- `tests/test_user_input.py` — update import and all string references to `request_user_input` → `clarify`
- `tests/test_background.py` — update all 4 background task function names and string assertions
- `tests/test_todo.py` — update `write_todos`/`read_todos` → `todo_write`/`todo_read`
- `tests/test_approvals.py` — update all `resolve_approval_subject("run_shell_command", ...)` → `"shell"` (8 occurrences)

**prerequisites:** [TASK-2, TASK-3]

**done_when:** `uv run pytest tests/test_shell.py tests/test_user_input.py tests/test_background.py tests/test_todo.py tests/test_approvals.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log; tail -5 .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log` shows all passing

**success_signal:** N/A

---

### ✓ DONE — TASK-5: Update secondary test files (Layer 3, part B)

Update remaining tracked test files that contain old tool name strings.

**files:**
- `tests/test_agent.py` — update all 8 old tool name strings
- `tests/test_tool_registry.py` — update `"run_shell_command"`, `"write_todos"` string references
- `tests/test_tool_prompt_discovery.py` — update import of `run_shell_command` → `shell`; update all background task string assertions
- `tests/test_core_loop_interrupts.py` — update `"run_shell_command"` string reference (line 14)
- `tests/test_display.py` — update `"run_shell_command"` in `resolve_approval_subject` call (line 101) and output assertion (line 115)

**prerequisites:** [TASK-4]

**done_when:** `uv run pytest tests/test_agent.py tests/test_tool_registry.py tests/test_tool_prompt_discovery.py tests/test_core_loop_interrupts.py tests/test_display.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log; tail -5 .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log` shows all passing

**success_signal:** N/A

---

### ✓ DONE — TASK-6: Update integration tests + evals + full suite (Layer 3, part C)

Update the two integration/functional test files that mix old tool names in prompts and assertions, update all three eval files whose pass/fail logic checks tool names, then verify the full suite.

**files:**
- `tests/test_tool_calling_functional.py` — update `"run_shell_command"` in parametrize prompt (lines 67–68), comment string (line 219), and `"request_user_input"` in the user-input integration test (line 234, 246, 256)
- `tests/test_commands.py` — update `"run_shell_command"` in `_PROMPT_SHELL` (line 127) and `assert tool_called` message (line 164)
- `evals/eval_file_tools.py` — update 8 occurrences of `"run_shell_command"` in eval tool-call assertions (lines 16–17, 21, 68, 85, 118, 134, 150, 154, 212)
- `evals/eval_ollama_tool_search.py` — update 6 occurrences of `"run_shell_command"` and `"start_background_task"` in eval assertions
- `evals/eval_compaction_quality.py` — update `expected_compactable` set at line 402: `"run_shell_command"` → `"shell"`; update docstring at line 386

**prerequisites:** [TASK-5]

**done_when:** `uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-rename-full.log; tail -10 .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-rename-full.log` shows full suite passing

**success_signal:** `uv run co chat` starts; `check_capabilities` response lists `shell`, `clarify`, `todo_write`, `todo_read`, `task_start`, `task_status`, `task_cancel`, `task_list` as registered tool names.

---

## Testing

- **Pre-rename baseline:** `uv run pytest tests/test_shell.py tests/test_user_input.py tests/test_background.py tests/test_todo.py -x` must be green before starting TASK-1.
- **Per-task gates:** TASK-4 and TASK-5 each have a targeted pytest run in `done_when`.
- **Full suite gate:** TASK-6 `done_when` is the full suite — no partial ships.
- **Manual smoke:** Post-ship, `uv run co chat` → `check_capabilities` shows new tool names.

---

## Open Questions

None — all answered by source inspection before drafting.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tool-naming-rename`

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep returns 0 old names in tool source files | ✓ pass |
| TASK-2 | grep returns 0 old names in agent + context files | ✓ pass |
| TASK-3 | grep returns 0 old names in context internals + prompts + README | ✓ pass |
| TASK-4 | primary test files pass | ✓ pass |
| TASK-5 | secondary test files pass | ✓ pass |
| TASK-6 | full suite — 667 passed | ✓ pass |

**Tests:** full suite — 667 passed, 0 failed
**Doc Sync:** fixed — `tools.md` updated with all 8 renamed tool names

**Overall: DELIVERED**
All 8 tool renames shipped (shell, clarify, todo_write, todo_read, task_start, task_status, task_cancel, task_list) across source, context, tests, evals, prompt rules, and README. Shipped together with agentic-functional-llm-split delivery.
