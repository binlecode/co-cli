# TODO: Coding Tool Convergence

Unimplemented work to align co-cli's coding workflow with converged patterns from peer
systems while preserving approval-first architecture.

**Goal:** Coding tasks default to native file tools over shell. Coder-specific model used
only through tool-level delegation. Safety posture remains approval-first with no bypass path.

## Sequencing

1. TODO 1 → TODO 2 (both P0, independent but file tools needed before delegation)
2. TODO 3 depends on TODO 1 (delegate_coder uses edit_file/write_file)
3. TODO 4 depends on TODO 3 (eval gates measure delegation quality)
4. TODO 5 and TODO 6 are independent P2 additions

---

## TODO 1 — Native File Tools (P0)

**What:** Add first-class file operation tools: `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file`. Shell remains available but is no longer the primary editing surface.

**Why:** File edits through `run_shell_command` have a larger error surface and no path-boundary guarantees. Native tools make edit intent explicit at the tool-call level.

**Files:**
- `co_cli/tools/files.py` (new) — 5 tool functions + `resolve_workspace_path` helper
- `co_cli/agent.py` — register read-only tools with `requires_approval=all_approval`; `write_file`/`edit_file` with `requires_approval=True`
- `tests/test_tools_files.py` (new)

**Implementation:**
- All paths resolved against workspace root; traversal and symlink escapes raise `ValueError`
- `read_file` supports optional `start_line`/`end_line` for large files
- `find_in_files` is grep-backed with `glob` pattern filter and `max_matches` cap
- `edit_file` has `replace_all: bool = False` flag; raises if `search` string not found (no silent no-op)
- All tools return `dict[str, Any]` with `display` + metadata fields (`path`, `line_count`, `match_count`, `bytes_written`, `changed`)

**Done-when:**
- `uv run pytest tests/test_tools_files.py` passes
- Path traversal test: `../../etc/passwd` raises `ValueError` before any I/O
- Symlink escape test: symlink pointing outside workspace root is rejected
- `edit_file` with `replace_all=False` on a string with 2 occurrences raises `ValueError`
- `write_file` and `edit_file` calls appear in approval flow (functional test)

---

## TODO 2 — Shell Policy Engine (P0)

**What:** Replace the flat prefix + operator blocklist in `_approval.py` with a structured policy evaluator (`ShellDecision`: `ALLOW / REQUIRE_APPROVAL / DENY`) in a new `co_cli/shell_policy.py` module.

**Why:** Current `_is_safe_command` misses policy-grade patterns: control chars, heredoc injection, env-var injection forms, compound chaining that bypasses prefix checks.

**Files:**
- `co_cli/shell_policy.py` (new) — `ShellDecision`, `ShellPolicyResult`, `evaluate_shell_command()`
- `co_cli/_orchestrate.py` — replace `_is_safe_command` call with `evaluate_shell_command`
- `tests/test_shell_policy.py` (new)

**Implementation:**
- `DENY`: control chars, heredoc injection in restricted mode, env-injection forms (`VAR=$(...)` chains)
- `REQUIRE_APPROVAL`: shell chaining (`&&`, `||`, `;`), redirections (`>`, `>>`), subshells (`$(...)` as arg)
- `ALLOW`: exact prefix-safe read-only ops matching configured `safe_prefixes`
- Unknown/unclassified → `REQUIRE_APPROVAL` (backward-compatible default)
- No behavior change to approval UX or prompt flow

**Done-when:**
- `uv run pytest tests/test_shell_policy.py` passes
- `git status` still auto-approves (regression check)
- `rm -rf /` returns `DENY`
- `cat file && rm file` returns `REQUIRE_APPROVAL`
- `export VAR=$(curl ...)` returns `DENY`

---

## TODO 3 — `delegate_coder` Subagent Tool (P1)

**Prerequisite:** TODO 1 must be complete (delegate result is applied via `edit_file`/`write_file`).

**What:** Add `delegate_coder` tool that runs a coding-specialized subagent (e.g. `qwen3-coder-next`) and returns a structured plan + diff preview. Parent agent executes mutations through approved file tools — no subagent direct write path.

**Why:** Coding-specialized models outperform general models on code generation. Delegation must stay tool-level and traceable; subagent never bypasses approval.

**Files:**
- `co_cli/agents/coder.py` (new) — `CoderResult` Pydantic model, `make_coder_agent()`
- `co_cli/tools/delegation.py` (new) — `delegate_coder()` tool
- `co_cli/agent.py` — register `delegate_coder` with `requires_approval=all_approval`
- `co_cli/config.py` — optional `coder_delegate_model` field, env: `CO_CLI_CODER_DELEGATE_MODEL`
- `tests/test_delegate_coder.py` (new)

**Implementation:**
- `CoderResult`: `summary`, `diff_preview`, `files_touched: list[str]`, `tests_run: list[str]`, `confidence: float`
- Subagent gets read tools only — no write/edit registration in the coder agent
- Parent receives `CoderResult` and applies mutations via its own approved `edit_file`/`write_file` calls
- OTel: parent tool span contains nested subagent run span

**Done-when:**
- `uv run pytest tests/test_delegate_coder.py` passes
- Subagent has no registered `write_file`/`edit_file` tools (assert in test)
- Coder model routes to `CO_CLI_CODER_DELEGATE_MODEL`, not parent model
- OTel trace shows nested spans: parent tool call → subagent run

---

## TODO 4 — Coding Eval Gates (P1)

**What:** Add `evals/eval_coding_toolchain.py` with 5 measurable quality gates covering the file-tools + delegation pipeline.

**Why:** No current eval covers coding-delegation quality. Gate regressions are undetected until user reports.

**Files:**
- `evals/eval_coding_toolchain.py` (new)
- `evals/coding_toolchain.jsonl` (new) — test cases
- `evals/coding_toolchain-result.md` (output artifact)

**Implementation:**
- Case format: `{"id": "...", "prompt": "...", "expected_files": ["..."], "checks": [...]}`
- Metrics: `edit_success_rate`, `patch_apply_rate`, `post_edit_test_pass_rate`, `approval_prompts_per_case`, `tool_error_recovery_rate`
- Gate thresholds: `edit_success_rate >= 0.80`, `patch_apply_rate >= 0.90`, `tool_error_recovery_rate >= 0.70`
- Exit code non-zero on gate failure (CI-compatible)

**Done-when:**
- `uv run python evals/eval_coding_toolchain.py` runs to completion with JSON + markdown output
- At least 5 test cases in `coding_toolchain.jsonl`
- Non-zero exit when any gate threshold is missed

---

## TODO 5 — Workspace Checkpoint + Rewind (P2)

**What:** Add `/checkpoint [label]` and `/rewind [id|last]` slash commands backed by git snapshot (or filesystem copy fallback for non-git workspaces).

**Why:** No recovery path for unwanted agent edits. Approval-first reduces risk but doesn't eliminate it; rewind provides a backstop.

**Files:**
- `co_cli/workspace_checkpoint.py` (new) — `create_checkpoint()`, `list_checkpoints()`, `restore_checkpoint()`
- `co_cli/_commands.py` — register `/checkpoint` and `/rewind` handlers
- `tests/test_rewind.py` (new)

**Implementation:**
- Git-backed: `git stash` or lightweight branch snapshot against workspace root
- Non-git fallback: copy changed files manifest to `.co-cli/checkpoints/<id>/`
- `restore_checkpoint` requires explicit approval confirmation prompt before write
- Rewind restores file content + handles create/delete/modify lifecycle events

**Done-when:**
- `uv run pytest tests/test_rewind.py` passes
- `/checkpoint` creates a restorable snapshot (verified by mutating a file, rewinding, confirming original content)
- Rewind is blocked without approval confirmation
- Non-git workspace fallback exercised in test

---

## TODO 6 — Approval Risk Classifier (P2, optional)

**What:** Add `co_cli/_approval_risk.py` with `classify_tool_call() → ApprovalRisk (LOW/MEDIUM/HIGH)`. Route approval prompts based on risk tier rather than static per-tool flag.

**Why:** Static approval routing causes fatigue on low-risk repeated actions. Risk-tiered routing reduces prompts without lowering the safety floor.

**Files:**
- `co_cli/_approval_risk.py` (new)
- `co_cli/_orchestrate.py` — integrate into `_handle_approvals`
- `co_cli/config.py` — `approval_risk_enabled: bool = False`, `approval_auto_low_risk: bool = False`
- `tests/test_approval_risk.py` (new)

**Implementation:**
- `HIGH` → always prompt
- `MEDIUM` → prompt unless explicit scoped session approval granted
- `LOW` → auto-approve only if `approval_auto_low_risk=True` (disabled by default)
- Feature entirely disabled by default; existing behavior unchanged when `approval_risk_enabled=False`

**Done-when:**
- `uv run pytest tests/test_approval_risk.py` passes
- Feature disabled by default: existing approval behavior unchanged (regression test)
- With feature enabled: `read_file` on existing path classifies as `LOW`; `write_file` with new path classifies as `HIGH`
