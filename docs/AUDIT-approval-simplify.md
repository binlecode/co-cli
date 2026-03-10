# Audit: approval-simplify

**Date:** 2026-03-10
**Scope:** `approval-simplify` — reassess the March 7 delivery audit against the latest code, workflow docs, and enforced testing rules.

---

## Verdict

**NEEDS_ATTENTION**

The approval implementation still matches the intended three-tier model, and the updated functional regression slice passes. However, the prior delivery audit is still not fully accurate against the current repository state:

1. Workflow review docs are not fully aligned with the current approval model.
2. The earlier helper-level test signoff has since been replaced with functional-path coverage.

---

## What Still Holds

- `run_shell_command` is still registered without orchestration-level approval wrapping in `co_cli/agent.py`.
- Shell approval still follows inline policy first, then deferred approval only when needed.
- `_handle_approvals()` still implements the three-tier chain:
  - `skill_tool_grants`
  - `session_tool_approvals`
  - user prompt `y/n/a`
- Shell `"a"` still persists a derived pattern to exec approvals; non-shell `"a"` still stores a session-only tool grant.
- The updated approval-related functional slice passes:

```bash
uv run pytest tests/test_shell.py tests/test_orchestrate.py -q
uv run pytest tests/test_commands.py -q -k 'dispatch_non_slash or dispatch_unknown_command or cmd_approvals_list_shows_saved_pattern or cmd_approvals_clear_removes_saved_patterns'
```

Result on 2026-03-10: **33 passed** in **4.76s** and **4 passed** in **0.62s**.

---

## Findings

### 1. Prior audit overstates doc alignment

The earlier review says:

- "None found" under gaps
- "Code, tests, and docs are fully aligned"

That is no longer true if workflow artifacts in `docs/` are included.

Current stale workflow-doc references include:

- `docs/REVIEW-flow-approval.md`
  - is explicitly marked as pre-`approval-simplify`, but much of the body still analyzes the removed four-tier model
  - still links to deleted `_approval_risk.py` as part of the preserved historical review text

This does **not** invalidate the DESIGN-doc cleanup claim. It does invalidate the broader "docs fully aligned" verdict.

### 2. Prior test signoff was policy-problematic, but is now superseded

The earlier review treats the following coverage as acceptable:

- `_validate_args`
- `_is_safe_command`
- `_check_skill_grant`
- `evaluate_shell_command`

Current `CLAUDE.md` testing policy now explicitly forbids unit-style tests of internal helpers in isolation and requires functional tests focused on real user-triggered paths.

That earlier coverage was policy-problematic. It has since been replaced by functional-path tests covering:

- `tests/test_shell.py` for safe-command execution, deferred approval, remembered shell approvals, and hard-deny behavior through `run_shell_command`
- `tests/test_orchestrate.py` for `"a"` persistence behavior through the orchestration approval loop
- `tests/test_commands.py` for `/approvals list` and `/approvals clear` user-facing command flows

The old helper-level files (`tests/test_approval.py`, `tests/test_exec_approvals.py`, `tests/test_shell_policy.py`) were removed as part of that cleanup.

---

## Evidence

### Code paths

- `co_cli/agent.py` — `run_shell_command` registration
- `co_cli/_orchestrate.py` — approval prompt docstring, remember hint, re-entry loop
- `co_cli/tools/shell.py` — inline shell DENY / ALLOW / REQUIRE_APPROVAL path

### Workflow docs with stale references

- `docs/REVIEW-flow-approval.md`

### Testing policy and tests

- `CLAUDE.md` — current mandatory testing rules
- `tests/test_orchestrate.py`
- `tests/test_shell.py`
- `tests/test_commands.py`

---

## Recommended Follow-Up

1. Update or clearly mark stale workflow review docs that still preserve pre-`approval-simplify` analysis of `_approval_risk.py` or the four-tier model.
2. Keep the previous delivery review classified as `NEEDS_ATTENTION` until the workflow-history drift is either cleaned up further or explicitly accepted as preserved history.
