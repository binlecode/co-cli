# Delivery Audit: approval-flow-simplification

**Date:** 2026-03-10
**Scope:** `approval-flow-simplification` delivery
**Source modules:** `co_cli/_tool_approvals.py` (new), `co_cli/_orchestrate.py` (changed), `co_cli/tools/shell.py` (changed)
**Supporting scope:** `co_cli/agent.py`, `co_cli/config.py`, `co_cli/deps.py`
**Doc scope:** All `docs/DESIGN-*.md`

---

## Phase 1 — Scope Resolution

The delivery slug does not map to a single module. Scope resolved to the three changed/new modules listed above, with all DESIGN docs as the documentation surface. The TODO plan (`docs/TODO-approval-flow-simplification.md`) describes five tasks:

- TASK-1: Add `co_cli/_tool_approvals.py` (new module with centralized approval helpers)
- TASK-2: Split `_handle_approvals()` in `_orchestrate.py` into collection + resumption
- TASK-3: Route shell remembered-approval checks through the helper module
- TASK-4: Update tests
- TASK-5: Sync `DESIGN-core-loop.md`

---

## Phase 2 — Feature Inventory

### Agent Tools (from `_register(` in `co_cli/agent.py`)

The delivery does not register any new agent tools. `run_shell_command` is the only approval-relevant tool and its registration is unchanged:

```
_register(run_shell_command, all_approval)  # False in normal mode
```

### Config Settings (approval-relevant fields from `co_cli/config.py` + `co_cli/deps.py`)

| Field | Source | Description |
|-------|--------|-------------|
| `shell_safe_commands` | `Settings.shell_safe_commands` → `CoConfig` | Auto-approved command prefixes |
| `shell_max_timeout` | `Settings.shell_max_timeout` → `CoConfig` | Hard ceiling on per-command timeout |
| `exec_approvals_path` | `CoConfig` (XDG-derived) | Path to `.co-cli/exec-approvals.json` |
| `session_tool_approvals` | `CoSessionState` | Per-session set of auto-approved tool names |
| `skill_tool_grants` | `CoSessionState` | Per-turn auto-approved tools from active skill |

No new config fields were introduced by this delivery.

### New/Changed Public Functions in Delivery Modules

#### `co_cli/_tool_approvals.py` (new internal helper module)

| Function | Role |
|----------|------|
| `decode_tool_args(raw_args)` | Normalizes deferred-tool args from `str \| dict \| None` to `dict` |
| `format_tool_call_description(tool_name, args)` | Builds user-facing approval description string |
| `approval_remember_hint(tool_name, args)` | Returns the `[always -> will remember: <pattern>]` hint for shell commands |
| `is_session_auto_approved(tool_name, deps)` | Checks if tool is in `deps.session.session_tool_approvals` |
| `remember_tool_approval(tool_name, args, deps)` | Dispatches persistence: shell → `exec-approvals.json`, others → session set |
| `is_shell_command_persistently_approved(cmd, deps)` | Checks + updates last-used for persistent shell approval |
| `record_approval_choice(approvals, ...)` | Records one approval result and optionally persists via `remember_tool_approval` |

#### `co_cli/_orchestrate.py` (changed)

| Function | Change |
|----------|--------|
| `_collect_deferred_tool_approvals(result, deps, frontend)` | Extracted from former `_handle_approvals()`; now collection-only, returns `DeferredToolResults` without resuming |
| `run_turn(...)` | Approval loop restructured: explicit `while DeferredToolRequests` loop calling `_collect_deferred_tool_approvals()` then `_stream_events()` separately |

`_handle_approvals()` has been removed. The approval loop now lives explicitly in `run_turn()`.

#### `co_cli/tools/shell.py` (changed)

| Function | Change |
|----------|--------|
| `run_shell_command(ctx, cmd, timeout)` | No longer calls `load_approvals()`, `find_approved()`, `update_last_used()` directly. Delegates to `is_shell_command_persistently_approved(cmd, ctx.deps)` from `_tool_approvals.py` |

---

## Phase 3 — Coverage Check Against DESIGN Docs

### `_tool_approvals.py` — New Module

This is an internal helper module (`_`-prefixed). Per engineering rules, `_prefix.py` helpers are internal/private and not part of the public API. The question is whether the behaviors they provide need DESIGN doc coverage — and under what level of detail.

| Feature | Mentioned in DESIGN docs? | Doc(s) | Assessment |
|---------|--------------------------|--------|------------|
| Module exists (`_tool_approvals.py`) | YES | `DESIGN-core-loop.md` §6 Files, `DESIGN-flow-approval.md` Owning Code table, `DESIGN-tools-execution.md` Shell Tool §4 Files | Full — listed in all three relevant doc File tables |
| `decode_tool_args()` | PARTIAL | `DESIGN-flow-approval.md` pseudocode calls `decode_tool_args(call.args)` | Mentioned in pseudocode only; no prose description. Acceptable for internal helper: pseudocode is sufficient |
| `format_tool_call_description()` | PARTIAL | `DESIGN-flow-approval.md` pseudocode (`format_tool_call_description(call.tool_name, args)`), Owning Code table | Named in pseudocode + file table. No standalone description. Acceptable for internal helper |
| `approval_remember_hint()` | NO | Not mentioned in any DESIGN doc | **Gap.** The function is called by `format_tool_call_description()` and its output (the `[always -> will remember: <pattern>]` prompt hint) is described in `DESIGN-flow-approval.md` prose ("the approval prompt displays the derived pattern before the user answers"). However, the helper function itself is not named. Minor: the behavior is documented; the function name is not |
| `is_session_auto_approved()` | PARTIAL | `DESIGN-flow-approval.md` pseudocode calls `is_session_auto_approved(call.tool_name, deps)` | Named in pseudocode. Acceptable for internal helper |
| `remember_tool_approval()` | PARTIAL | `DESIGN-flow-approval.md` Owning Code table, `DESIGN-tools-execution.md` §2 | Named in both. No description of its dispatch logic (shell vs non-shell). Minor |
| `is_shell_command_persistently_approved()` | YES | `DESIGN-flow-approval.md` Owning Code + shell-specific inline policy pseudocode, `DESIGN-core-loop.md` §4.6, `DESIGN-tools-execution.md` §2 Tier 3 | Full — named in all three docs |
| `record_approval_choice()` | PARTIAL | `DESIGN-flow-approval.md` pseudocode + Owning Code table, `DESIGN-tools-execution.md` Owning Code table | Named in pseudocode and file tables. No description of its `remember` parameter dispatch. Minor |

### `run_shell_command` in `co_cli/tools/shell.py`

| Feature | Mentioned in DESIGN docs? | Doc(s) | Assessment |
|---------|--------------------------|--------|------------|
| Tool registration (`requires_approval=False` unless `all_approval`) | YES | `DESIGN-core.md` §3.5 Approval Boundary, `DESIGN-tools-execution.md` §Shell Tool Files | Covered |
| Policy check: DENY → terminal_error | YES | `DESIGN-flow-approval.md` Shell-Specific Inline Policy, `DESIGN-core-loop.md` §4.6, `DESIGN-tools-execution.md` §2 | Full coverage |
| Policy check: ALLOW → execute | YES | Same docs above | Full coverage |
| Policy check: REQUIRE_APPROVAL + persistent check via `is_shell_command_persistently_approved` | YES | All three docs, `DESIGN-tools-execution.md` §2 Tier 3 explicitly | Full coverage |
| `ctx.tool_call_approved` fallthrough | YES | `DESIGN-flow-approval.md` §Shell-Specific Inline Policy step 3, `DESIGN-core-loop.md` §4.6 | Covered |
| `raise ApprovalRequired(metadata={"cmd": cmd})` | YES | Same docs | Covered |
| `ModelRetry` on timeout/error | YES | `DESIGN-tools-execution.md` §2 error scenarios table | Covered |
| Delegation to `is_shell_command_persistently_approved` instead of direct `_exec_approvals` calls | PARTIAL | `DESIGN-tools-execution.md` §2 Tier 3 mentions `_tool_approvals.is_shell_command_persistently_approved` but the DESIGN-core-loop §4.7 pseudocode still shows the old pattern with `add_approval(...)` directly | **Gap — DESIGN-core-loop §4.7 pseudocode is stale** (see below) |

### `_collect_deferred_tool_approvals` in `co_cli/_orchestrate.py`

| Feature | Mentioned in DESIGN docs? | Doc(s) | Assessment |
|---------|--------------------------|--------|------------|
| Function signature and responsibility (collection-only, returns `DeferredToolResults`) | YES | `DESIGN-flow-approval.md` Deferred Tool Request Path, `DESIGN-core-loop.md` §4.5, §4.7 | Covered |
| Tier 1: skill grant check (`_check_skill_grant`) | YES | All three approval-relevant docs | Covered |
| Tier 2: session auto-approval (`is_session_auto_approved`) | YES | Same | Covered |
| Tier 3: user prompt + `record_approval_choice` | YES | `DESIGN-flow-approval.md` pseudocode | Covered |
| `record_approval_choice` called for denial (not just approval) | PARTIAL | `DESIGN-flow-approval.md` pseudocode shows `record_approval_choice(approvals, approved=False, ...)` on denial | Covered in pseudocode; prose could be clearer |
| Resumption is separate (`_stream_events` called by `run_turn`, not by this function) | YES | `DESIGN-flow-approval.md` §Deferred Tool Request Path, `DESIGN-core-loop.md` §4.5 | Covered |

### Pseudocode Accuracy Check

| Doc | Section | Discrepancy |
|-----|---------|-------------|
| `DESIGN-core-loop.md` | §4.7 pseudocode | **Stale.** Shows `add_approval(deps.config.exec_approvals_path, cmd, call.tool_name)` and `deps.session.session_tool_approvals.add(call.tool_name)` called inline. Actual code delegates both to `record_approval_choice(remember=True)` → `remember_tool_approval()`. The behavior is identical but the pseudocode bypasses the helper layer introduced by this delivery. |
| `DESIGN-core-loop.md` | §4.7 pseudocode | Shows `args = json.loads(call.args) if str else call.args`. Actual code uses `decode_tool_args(call.args)` which also handles `None`. |
| `DESIGN-flow-approval.md` | §Three-Tier Decision Chain pseudocode | Accurate. Uses `decode_tool_args()`, `format_tool_call_description()`, `is_session_auto_approved()`, `record_approval_choice()` — matches actual `_orchestrate.py` imports and calls. |

---

## Phase 4 — Second Pass: Delivery Task Completion

| Task | Claimed | Evidence | Status |
|------|---------|----------|--------|
| TASK-1: `_tool_approvals.py` exists with all helpers | YES | Module verified; all 7 functions present | COMPLETE |
| TASK-2: `_handle_approvals()` removed, split into collection + resumption | YES | `_handle_approvals` does not appear in `_orchestrate.py`; `_collect_deferred_tool_approvals` returns `DeferredToolResults`; `run_turn()` contains the explicit `while DeferredToolRequests` loop | COMPLETE |
| TASK-3: `run_shell_command` delegates to `is_shell_command_persistently_approved` | YES | `shell.py` imports from `_tool_approvals` and calls `is_shell_command_persistently_approved(cmd, ctx.deps)`; no direct calls to `load_approvals`, `find_approved`, `update_last_used` | COMPLETE |
| TASK-4: Tests updated | PARTIAL | Test files exist (`tests/test_approval.py`, `tests/test_orchestrate.py`, `tests/test_exec_approvals.py`, `tests/test_shell_policy.py`). Prior audit (`AUDIT-approval-simplify.md`) noted that some approval tests may still be internal-helper-level tests conflicting with CLAUDE.md testing policy. Not re-verified in this audit; left as prior-audit finding | CARRY-OVER (from prior AUDIT) |
| TASK-5: `DESIGN-core-loop.md` synced | PARTIAL | `_tool_approvals.py` now appears in §6 Files table. However, §4.7 pseudocode is **stale** — it still shows direct `add_approval()` and `session_tool_approvals.add()` calls instead of delegating through `record_approval_choice()` + `remember_tool_approval()`. See Phase 3 pseudocode accuracy check | **BLOCKING GAP** |

---

## Phase 5 — Coverage Summary Table

| Feature | Doc Coverage | Severity |
|---------|-------------|----------|
| `_tool_approvals.py` module (existence, file table) | FULL | — |
| `decode_tool_args()` | PARTIAL (pseudocode only) | Minor |
| `format_tool_call_description()` | PARTIAL (pseudocode + file table) | Minor |
| `approval_remember_hint()` | MISSING from all docs | Minor — behavior described, name absent |
| `is_session_auto_approved()` | PARTIAL (pseudocode) | Minor |
| `remember_tool_approval()` | PARTIAL (file table, no dispatch logic) | Minor |
| `is_shell_command_persistently_approved()` | FULL | — |
| `record_approval_choice()` | PARTIAL (pseudocode + file table) | Minor |
| `run_shell_command` policy path | FULL | — |
| `run_shell_command` delegates to `_tool_approvals` (not `_exec_approvals` directly) | PARTIAL | Minor |
| `_collect_deferred_tool_approvals` — collection-only, returns `DeferredToolResults` | FULL | — |
| Three-tier decision chain | FULL | — |
| DESIGN-core-loop §4.7 pseudocode accuracy | STALE | **Blocking** |
| DESIGN-flow-approval pseudocode accuracy | FULL (accurate) | — |
| Approval re-entry loop in `run_turn()` | FULL | — |

---

## Verdict

**NEEDS_ATTENTION**

### Blocking Issues

1. **DESIGN-core-loop.md §4.7 pseudocode is stale.** The `_collect_deferred_tool_approvals()` pseudocode in §4.7 still shows the pre-delivery pattern — calling `add_approval(...)` and `deps.session.session_tool_approvals.add()` directly inline. The delivery introduced `record_approval_choice()` + `remember_tool_approval()` to centralize this logic, but the §4.7 pseudocode was not updated to reflect the helper delegation. DESIGN-core-loop §4.7 must be updated to use `record_approval_choice(remember=True)` and acknowledge `remember_tool_approval()` as the persistence dispatcher.

### Non-Blocking Gaps

2. **`approval_remember_hint()` is not named in any DESIGN doc.** The behavior (displaying the derived fnmatch pattern before the user responds) is documented in prose in `DESIGN-flow-approval.md`. The function name does not appear. Because it is an internal helper, this is a minor gap — acceptable but worth noting for future doc passes.

3. **`decode_tool_args()` pseudocode in DESIGN-core-loop §4.7 uses an informal expression** (`json.loads(call.args) if str else call.args`) rather than naming `decode_tool_args()`. DESIGN-flow-approval §pseudocode correctly names it. The core-loop doc has a minor inconsistency with the actual code shape.

4. **Prior carry-over from AUDIT-approval-simplify:** test coverage may still include internal-helper-level tests (`test_approval.py`) that conflict with the mandatory functional-test policy in CLAUDE.md. This was documented as a NEEDS_ATTENTION finding in the prior audit and is not re-assessed here; it should not be closed until reviewed.

### What Passes

- All three delivery tasks for code changes (TASK-1, TASK-2, TASK-3) are complete and accurate.
- `DESIGN-flow-approval.md` is the canonical approval flow doc and its pseudocode accurately reflects the current code — correct helper names, correct delegation pattern.
- `_tool_approvals.py` is referenced in all relevant File tables (`DESIGN-core-loop.md §6`, `DESIGN-flow-approval.md` Owning Code, `DESIGN-tools-execution.md` Shell §4`).
- `run_shell_command` behavior — all three policy tiers — is accurately documented across `DESIGN-flow-approval.md`, `DESIGN-core-loop.md`, and `DESIGN-tools-execution.md`.
- The approval re-entry loop shape in `run_turn()` is fully and accurately documented.

---

## Required Follow-Up

| Action | Blocking? | Target Doc |
|--------|-----------|-----------|
| Update §4.7 pseudocode in DESIGN-core-loop.md to show `record_approval_choice(remember=True)` delegation instead of direct `add_approval()` / `session_tool_approvals.add()` calls | YES | `docs/DESIGN-core-loop.md` |
| Update §4.7 pseudocode to use `decode_tool_args(call.args)` instead of the informal `json.loads...` expression | No | `docs/DESIGN-core-loop.md` |
| Add `approval_remember_hint()` to the Owning Code table in DESIGN-flow-approval.md (or note it in prose) | No | `docs/DESIGN-flow-approval.md` |
| Resolve carry-over: assess whether `test_approval.py` tests are functional-path tests per CLAUDE.md policy | No | `tests/test_approval.py` |
