# REVIEW: test-fixes — Co-System Health Check
_Date: 2026-03-10_

## What Was Reviewed
**DESIGN docs:** DESIGN-core-loop.md, DESIGN-system.md, DESIGN-system-bootstrap.md
**Source modules:** `co_cli/_commands.py`, `co_cli/config.py`, `co_cli/deps.py`, `co_cli/_orchestrate.py`, `co_cli/_tool_approvals.py`
**TODO docs:** TODO-approval-flow-simplification.md, TODO-unified-model-build.md

## Auditor — TODO Health

| TODO doc | Task | Verdict | Key finding |
|----------|------|---------|-------------|
| approval-flow-simplification | TASK-1: Add `_tool_approvals.py` | SHIPPED | Module exists with all described functions: `decode_tool_args`, `format_tool_call_description`, `approval_remember_hint`, `is_session_auto_approved`, `remember_tool_approval`, `is_shell_command_persistently_approved`, `record_approval_choice` |
| approval-flow-simplification | TASK-2: Split `_handle_approvals()` into `_collect_deferred_tool_approvals` + `_resume_deferred_tool_requests` | NOT SHIPPED | `_handle_approvals()` still exists as a single combined helper in `_orchestrate.py` (lines 402–453). The two-function split described in the TODO has not been done. The `done_when` condition ("_handle_approvals() no longer exists as the single combined helper") is false. |
| approval-flow-simplification | TASK-3: `shell.py` routes through `is_shell_command_persistently_approved` | NOT SHIPPED | `co_cli/tools/shell.py` still directly calls `load_approvals()`, `find_approved()`, `update_last_used()` (lines 45–48). It does not delegate to `is_shell_command_persistently_approved` from `_tool_approvals.py`. `done_when` condition is false. |
| approval-flow-simplification | TASK-4: Update tests | PARTIAL / STALE | `tests/test_approval.py` exists but tests `_approval.py._is_safe_command`, `_shell_policy.evaluate_shell_command`, and `_orchestrate._check_skill_grant`. It does not cover `_tool_approvals.py` module boundaries (session auto-approval, shell remembered-approval, `record_approval_choice`). The listed test files `test_orchestrate.py` and `test_exec_approvals.py` do not exist. `done_when` is not met. Stale assumption: TASK-2 and TASK-3 prerequisites not done. |
| approval-flow-simplification | TASK-5: Sync `DESIGN-core-loop.md` | NOT DONE | `DESIGN-core-loop.md` still describes `_handle_approvals()` as the single approval step throughout (section 1 scope boundary, sections 4.5, 4.7, flow diagram, files table). Prerequisite TASK-2 not shipped, so this is correctly blocked. |
| unified-model-build | TASK-1: `ResolvedModel` + `ModelRegistry` in `_factory.py` | SHIPPED | Both classes present; `from_config`, `get`, `is_configured` all verified. |
| unified-model-build | TASK-2: `model_registry` in `CoServices`, `model_http_retries` in `CoConfig`, registry build in `main.py` | SHIPPED | `deps.py` has both fields (`model_registry: "ModelRegistry | None"` on `CoServices`, `model_http_retries: int = 2` on `CoConfig`). |
| unified-model-build | TASK-3: Sub-agent factories accept `ResolvedModel`; delegation uses registry | SHIPPED | `coder.py`, `research.py`, `analysis.py` all accept `ResolvedModel`. `delegation.py` uses `is_configured` guards and `registry.get()` in all three delegation functions. |
| unified-model-build | TASK-4: Collapse `_history.py` summarization chain | SHIPPED | `_resolve_summarization_model` absent; `summarize_messages` and `_run_summarization_with_policy` accept `ResolvedModel`; registry lookups present in `truncate_history_window` and `precompute_compaction`; `from co_cli.config import settings` import removed from `_history.py`. |
| unified-model-build | TASK-5: `_signal_analyzer.py` uses registry | SHIPPED | `analyze_for_signals` signature uses `services: CoServices`; `services.model_registry.get("analysis", fallback)` present; `CoConfig` removed from this file. |
| unified-model-build | TASK-6: Tests for `ModelRegistry` | SHIPPED | Both `test_model_registry_builds_from_config` and `test_model_registry_get_fallback_when_unconfigured` present in `tests/test_model_roles_config.py` and match the spec. |

**Overall verdict for `TODO-approval-flow-simplification.md`: `needs_cleanup`**

TASK-1 is done. TASK-2 and TASK-3 are the core unfinished work — blocking TASK-4 and TASK-5. The TODO is structurally sound (all tasks have `files:` and machine-verifiable `done_when:` conditions) but is not `ready_for_plan` because two of its tasks (TASK-2 and TASK-3) are partially shipped: the helper module (TASK-1) that their prereq depends on is done, but the callers haven't been updated to use it. The doc accurately reflects what still needs to happen; it does not need redesign, just execution of TASK-2 and TASK-3.

**Overall verdict for `TODO-unified-model-build.md`: `ready_for_plan`**

All six tasks are shipped. The TODO is complete and can be removed (or archived as done). No stale assumptions, no contradictions with current source.

---

## Code Dev — Doc Accuracy Audit

| Doc | Section | Status | Finding |
|-----|---------|--------|---------|
| DESIGN-core-loop.md | §1 Scope / §2 diagram / §4.5–4.7 | OK | `_handle_approvals()` is still the live function name in `_orchestrate.py`. The previously-flagged rename to `_collect_deferred_tool_approvals` + `_resume_deferred_tool_requests` has **not** happened. Doc naming is accurate. |
| DESIGN-core-loop.md | §6 Files table | blocking | `co_cli/_tool_approvals.py` is absent from the Files table. The module exists (untracked per git status) and contains the extracted approval helpers (`decode_tool_args`, `format_tool_call_description`, `approval_remember_hint`, `is_session_auto_approved`, `remember_tool_approval`, `is_shell_command_persistently_approved`, `record_approval_choice`). No DESIGN doc documents it. |
| DESIGN-core-loop.md | §4.3 pseudocode | minor | `_handle_approvals()` returns a two-tuple `(result, streamed_text)` — the outer `run_turn()` loop unpacks both. The pseudocode writes `result = _handle_approvals(...)`, eliding the `streamed_text` return leg. Not semantically wrong, but the type mismatch would confuse a developer implementing against the pseudocode. |
| DESIGN-system.md | §4.1 Agent Factory | blocking | Documents `get_agent(all_approval, web_policy, mcp_servers, personality, model_name?)` without the `config: CoConfig \| None = None` parameter. `agent.py` line 45 shows this parameter exists; the main chat flow passes `config=deps.config` to bypass the global settings singleton. Omitting it means a developer following the doc would miss the correct call pattern. |
| DESIGN-system.md | §4.2 CoDeps diagram + table | blocking | `CoServices` diagram lists only `shell`, `knowledge_index`, `task_runner`. `deps.py` line 26 declares a fourth field: `model_registry: "ModelRegistry \| None"`. This field drives all role-model lookups (compaction, delegation, signal analysis). Both the Mermaid diagram and the key-fields table are incomplete. |
| DESIGN-system-bootstrap.md | §1 Settings loading | OK | `__getattr__` lazy-singleton pattern described correctly. `config.py` implements `__getattr__(name)` → `get_settings()` when `name == "settings"`, matching the doc exactly. |
| DESIGN-system-bootstrap.md | §1 `create_deps()` pseudocode | minor | Pseudocode ends at `return CoDeps(services=services, ...)` with no mention that `main.py` sets `services.model_registry = ModelRegistry.from_config(config)` immediately after. The registry wiring is missing from the depicted sequence. |
| DESIGN-system-bootstrap.md | §7 State Mutations Summary | blocking | Table lists six fields mutated during startup. `deps.services.model_registry` (set in `main.py` pre-bootstrap, line 214) is absent. This is a critical session-level field used by nearly every role-model consumer. |
| DESIGN-core-loop.md | §5 Config | OK | All defaults checked: `max_request_limit=50`, `model_http_retries=2`, `doom_loop_threshold=3`, `max_reflections=3`, `tool_output_trim_chars=2000`, `max_history_messages=40`, `session_ttl_minutes=60`. All match `Settings` field defaults in `config.py`. |
| DESIGN-system-bootstrap.md | §1 role_models defaults | OK | Gemini default `gemini-3-flash-preview`, ollama all five roles. Matches `config.py` constants. |

### Finding Details

- **blocking — DESIGN-core-loop.md §6 Files, `_tool_approvals.py` missing**: The file `co_cli/_tool_approvals.py` exists with seven public helpers forming an extraction layer for deferred-approval logic. It is referenced in `TODO-approval-flow-simplification.md` as TASK-1 (now shipped) but does not appear in any DESIGN doc's Files table. Developers reading DESIGN-core-loop.md to understand the approval subsystem will not know the module exists.

- **blocking — DESIGN-system.md §4.1, `get_agent()` missing `config` parameter**: `agent.py` signature is `get_agent(*, all_approval, web_policy, mcp_servers, personality, model_name, config: CoConfig | None = None)`. The `config` parameter is the mechanism by which live sessions inject resolved `CoConfig` instead of re-reading the global `settings` singleton. A developer following the documented signature would write the call without `config=deps.config`, silently falling back to unresolved singleton state.

- **blocking — DESIGN-system.md §4.2 + DESIGN-system-bootstrap.md §7, `CoServices.model_registry` undocumented**: `deps.py` line 26 defines `model_registry: "ModelRegistry | None"`. `main.py` line 214 sets `services.model_registry = ModelRegistry.from_config(config)`. Seven call sites across `_commands.py`, `_history.py`, `_signal_analyzer.py`, and `tools/delegation.py` depend on this field. Neither the CoDeps architecture diagram in DESIGN-system.md nor the bootstrap state-mutations table in DESIGN-system-bootstrap.md §7 mentions it.

- **minor — DESIGN-core-loop.md §4.3, `_handle_approvals()` return type elided in pseudocode**: The live function (lines 447–453 in `_orchestrate.py`) returns `await _stream_events(...)`, which is a `(result, streamed_text)` tuple. The pseudocode omits the `streamed_text` leg (`result = _handle_approvals(...)` vs the actual `result, streamed_text = await _handle_approvals(...)`).

- **minor — DESIGN-system-bootstrap.md §1 pseudocode and §7, `model_registry` bootstrap step absent**: The `create_deps()` pseudocode and State Mutations Summary table both omit `services.model_registry`. The field is set in `main.py` between `create_deps()` and `run_model_check()`, making it a pre-bootstrap mutation that belongs in §7.

**Overall: 3 blocking, 2 minor**

---

## Verdict

**Overall: ACTION_REQUIRED**

| Priority | Action | Source |
|----------|--------|--------|
| P1 | Add `co_cli/_tool_approvals.py` to DESIGN-core-loop.md §6 Files table with its purpose (extracted approval helpers) | Code Dev blocking — §6 Files |
| P1 | Add `config: CoConfig \| None = None` parameter to `get_agent()` signature in DESIGN-system.md §4.1 | Code Dev blocking — §4.1 |
| P1 | Add `model_registry: ModelRegistry \| None` to `CoServices` diagram + key-fields table in DESIGN-system.md §4.2, and to State Mutations Summary table in DESIGN-system-bootstrap.md §7 | Code Dev blocking — §4.2 + §7 |
| P2 | Execute TASK-2 (split `_handle_approvals()`) and TASK-3 (route `shell.py` through `is_shell_command_persistently_approved`) in TODO-approval-flow-simplification.md | Auditor — needs_cleanup |
| P2 | Delete TODO-unified-model-build.md — all six tasks are shipped | Auditor — ready_for_plan |
| P3 | Fix `_handle_approvals()` pseudocode in DESIGN-core-loop.md §4.3 to show two-tuple return `(result, streamed_text)` | Code Dev minor — §4.3 |
| P3 | Add `model_registry` wiring step to `create_deps()` pseudocode in DESIGN-system-bootstrap.md §1 | Code Dev minor — §1 pseudocode |

**Recommended next step:** Run `/sync-doc DESIGN-system.md DESIGN-system-bootstrap.md DESIGN-core-loop.md` to fix the three blocking doc gaps, then execute TASK-2 and TASK-3 from `TODO-approval-flow-simplification.md`.
