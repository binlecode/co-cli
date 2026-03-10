# Delivery Audit: approval-simplify

**Date:** 2026-03-07
**Scope:** `approval-simplify` — simplify approval flow from four-tier to three-tier; add shell pattern transparency; delete risk classifier.

---

## Feature Inventory

### Agent Tools

`run_shell_command` is registered with `requires_approval=False` in `co_cli/agent.py` (line 291). This is correct — the shell tool manages its own approval path internally via `evaluate_shell_command` + `ApprovalRequired`. The three-tier deferred chain in `_handle_approvals` handles shell commands that reach the deferral point.

### Config Settings

Approval-related settings in `co_cli/config.py` after delivery:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | `_DEFAULT_SAFE_COMMANDS` list | Commands auto-approved without prompting (prefix match) |
| MCP `approval` | per-server `mcp_servers.<name>.approval` | `"auto"` | `"auto"` wraps server in `ApprovalRequiredToolset`; `"never"` bypasses |

Fields `approval_risk_enabled` and `approval_auto_low_risk` are absent from `Settings` and `CoConfig`. Confirmed by grep exit code 1 across `co_cli/`.

### CLI Commands

`/approvals` command present at line 1034 of `co_cli/_commands.py` (`SlashCommand("approvals", ...)`). Dispatches to `_cmd_approvals` which provides `list` and `clear [id]` subcommands using `load_approvals` / `save_approvals` from `_exec_approvals.py`.

### Session State

`CoSessionState.session_tool_approvals` (set) and `CoSessionState.skill_tool_grants` (set) present in `co_cli/deps.py`. `CoConfig.exec_approvals_path` present. No risk-related fields remain.

---

## Coverage Check

### Delivered vs. DESIGN-flow-approval.md

**Three-tier decision chain** — DESIGN describes tiers as: skill_tool_grants → session_tool_approvals → user prompt. Code in `_handle_approvals` (`_orchestrate.py` lines 418–443) implements exactly this sequence. Match.

**Shell-specific path** — DESIGN pseudocode: DENY → terminal_error; ALLOW → execute; persistent fnmatch check → execute or raise `ApprovalRequired`. Code in `tools/shell.py` implements this in order (lines 41–50). Match.

**Pattern transparency** — DESIGN: "when the user selects 'a' for a shell command, the approval prompt displays the derived fnmatch pattern before the user answers". Code at `_orchestrate.py` line 428–429 prepends `[always → will remember: <pattern>]` to `desc` before `frontend.prompt_approval(desc)`. Match.

**"a" persistence semantics** — DESIGN table: shell → `derive_pattern(cmd)` persisted to `.co-cli/exec-approvals.json`; non-shell → `session_tool_approvals` set. Code at lines 438–443 implements exactly this split. Match.

**Approval re-entry loop** — DESIGN: `while result.output is DeferredToolRequests`. Code at `run_turn` lines 506–512. Match.

**Budget sharing** — DESIGN: token usage accumulated across hops, no reset. Code passes `usage=turn_usage` through each re-entry call. Match.

**MCP approval inheritance** — DESIGN: `approval="auto"` wraps server in `ApprovalRequiredToolset`, flows through tiers 1–3 unchanged. Code in `agent.py` (approval wrapping at registration). Match.

**FrontendProtocol.prompt_approval docstring** — DESIGN notes docstring should say "Returns 'y', 'n', or 'a'". Code at `_orchestrate.py` line 94: `"""Prompt user for approval. Returns 'y', 'n', or 'a'."""`. Match.

**`/approvals` command** — DESIGN: "Patterns are never deleted automatically — use `/approvals clear [id]` at the REPL to manage them." `_commands.py` registers `/approvals list` and `/approvals clear [id]`. Match.

### Stale Reference Cleanup

All DESIGN docs checked for lingering `approval_risk`, `_approval_risk`, and `four-tier` references. All grep checks returned exit code 1 (no matches) against `docs/ --include="DESIGN-*.md"` and `docs/reference/ROADMAP-co-evolution.md`.

---

## Done-When Verification

| Task | Check | Result |
|------|-------|--------|
| TASK-1 | `grep "approval_risk_enabled" co_cli/_orchestrate.py` | No match |
| TASK-1 | `grep "will remember" co_cli/_orchestrate.py` | Line 429 — match present |
| TASK-1 | `grep "Returns.*'a'" co_cli/_orchestrate.py` | Line 94 — match present |
| TASK-2 | `grep -rn "approval_risk_enabled\|approval_auto_low_risk" co_cli/` | No matches |
| TASK-3 | `test -f co_cli/_approval_risk.py` | File absent |
| TASK-3 | `test -f tests/test_approval_risk.py` | File absent |
| TASK-4 | `grep -n "approval_risk\|risk classifier\|_approval_risk\|four-tier" docs/DESIGN-flow-approval.md` | No matches |
| TASK-5 | `grep -rn "approval_risk\|_approval_risk" docs/ --include="DESIGN-*.md"` | No matches |
| TASK-5 | `grep -rn "four-tier" docs/ --include="DESIGN-*.md"` | No matches |
| TASK-5 | `grep -rn "four-tier" docs/reference/ROADMAP-co-evolution.md` | No matches |

---

## Test Gate

Full regression suite from `TODO-approval-simplify.md`:

```
uv run pytest tests/test_approval.py tests/test_exec_approvals.py tests/test_shell_policy.py tests/test_orchestrate.py
```

Result: **52 passed, 0 failed** in 0.25s.

Coverage:
- `test_approval.py` — shell policy tiers, `_is_safe_command`, `_validate_args`, `_check_skill_grant`. Risk classifier tests absent (correct — module deleted).
- `test_exec_approvals.py` — `load_approvals`, `save_approvals`, `derive_pattern`, `find_approved`, `add_approval`, `update_last_used`, `prune_stale`.
- `test_shell_policy.py` — DENY / ALLOW / REQUIRE_APPROVAL classification.
- `test_orchestrate.py` — streaming, approval loop, tool preamble, `_patch_dangling_tool_calls`, skill grant logging.

---

## Gaps and Issues

None found. All five tasks from the plan are fully shipped:

1. Risk classifier removed from `_handle_approvals`; pattern transparency added.
2. `approval_risk_enabled` and `approval_auto_low_risk` removed from `Settings`, `CoConfig`, and `main.py`.
3. `_approval_risk.py` deleted; `test_approval_risk.py` deleted; risk classifier tests removed from `test_approval.py`.
4. `DESIGN-flow-approval.md` updated to three-tier model with pattern transparency note.
5. All other DESIGN docs purged of `approval_risk` and `four-tier` references.

---

## Verdict

**HEALTHY** — Delivery is complete and accurate. Code, tests, and docs are fully aligned. All done_when gates pass. No stale references remain in any DESIGN doc.
