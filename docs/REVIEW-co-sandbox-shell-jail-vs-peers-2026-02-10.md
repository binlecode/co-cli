# REVIEW: co Sandbox / Shell-Jail Design vs Peer Systems (2026-02-10)

## Scope

Review current co shell execution safety model against:
- First principles (boundary, least privilege, fail-closed, explicit consent)
- MVP pragmatism (minimal changes with maximal risk reduction)
- Converged patterns in Codex, Gemini CLI, Claude Code, and aider
- Online hardening best-practice references

Primary code reviewed:
- `co_cli/main.py`
- `co_cli/_orchestrate.py`
- `co_cli/_approval.py`
- `co_cli/sandbox.py`
- `co_cli/_sandbox_env.py`
- `co_cli/config.py`
- `co_cli/_commands.py`
- `co_cli/status.py`
- `co_cli/banner.py`
- `tests/test_shell.py`
- `tests/test_commands.py`

---

## Executive Verdict

Current co design is a solid **MVP sandbox executor**, but it is **not yet a robust shell-jail policy system** under degraded conditions.

The biggest gap is not "missing a tool". It is a **policy/behavior mismatch** across entry paths and fallback modes:
- some paths enforce approval + isolation assumptions,
- other paths bypass them,
- and fallback from isolated to non-isolated execution is fail-open.

---

## First-Principles Criteria

A practical shell-jail for agentic CLI should satisfy:
1. **Single trust boundary**: all command paths go through one policy gate.
2. **Fail-closed on boundary failure**: if sandbox unavailable, default is block or explicit re-consent.
3. **Least privilege by default**: constrained FS/network/process capabilities.
4. **Predictable policy semantics**: command safety evaluation resists argument-level bypasses.
5. **Operator clarity**: active risk mode is always obvious in-session.

---

## Findings (Severity-Ordered)

### 1) Critical: `!` direct shell path bypasses approval policy plane

Evidence:
- `co_cli/main.py:155` handles `!` commands directly.
- `co_cli/main.py:160` executes `deps.sandbox.run_command(...)` immediately.
- Approval logic exists in orchestration path only (`co_cli/_orchestrate.py:336` onward).
- In subprocess fallback, that means host execution with no isolation (`co_cli/sandbox.py:142`, `co_cli/sandbox.py:153`).

Why this matters:
- Co advertises approval-first semantics (`co_cli/main.py:57`), but one command path bypasses approval entirely.
- This is a **tool/policy mismatch**, not a missing capability.

MVP fix:
- Route `!` through the same approval/policy evaluator as tool calls, or explicitly require `!` to confirm once-per-session in non-isolated mode.
- At minimum, block `!` when `isolation_level == "none"` unless user explicitly opts into danger mode.

### 2) Critical: Docker failure in `auto` mode fails open to host subprocess

Evidence:
- `co_cli/main.py:74-90` falls back from Docker probe failure to `SubprocessBackend()` with a one-time warning.
- `SubprocessBackend` is explicitly `isolation_level = "none"` (`co_cli/sandbox.py:142`).

Why this matters:
- A shell-jail boundary that silently degrades to host shell is not fail-closed.
- Long sessions can proceed under weaker assumptions after a startup warning scrolls away.

Peer convergence:
- Gemini sandbox config throws a fatal error when sandbox is explicitly requested but unavailable (`gemini-cli .../sandboxConfig.ts:85-90`).
- Codex exposes explicit sandbox modes and approval controls with escalation semantics (OpenAI docs).

MVP fix:
- Add `sandbox_fallback = warn|error` (default should move toward `error` for secure installs).
- On fallback, require explicit operator confirmation before first shell execution.

### 3) High: `/yolo` auto-approves all tool calls even when isolation is `none`

Evidence:
- `/yolo` toggles `deps.auto_confirm` with no isolation guard (`co_cli/_commands.py:120-127`).
- `_handle_approvals` short-circuits on `deps.auto_confirm` before isolation checks (`co_cli/_orchestrate.py:351-353`).

Why this matters:
- In subprocess mode, one `/yolo` removes all remaining approval friction for host commands.
- This compounds fallback risk.

MVP fix:
- Disallow `/yolo` when `isolation_level == "none"` by default.
- Or require explicit `danger_mode=true` setting and a hard warning acknowledgement.

### 4) High: Safe-command auto-approval is prefix-based and can auto-approve destructive variants

Evidence:
- `_is_safe_command` only checks for prohibited shell operators + prefix match (`co_cli/_approval.py:12-18`).
- Default safe prefixes include broad commands (`find`, `sort`, `git diff`) in `co_cli/config.py:12-29`.

Why this matters:
- Prefix match can classify destructive forms as safe (examples: `find ... -delete`, `sort -o`, `git diff --output=...`).
- This is not command injection; it is **semantic misclassification**.

Peer convergence:
- Codex and Gemini use parser-assisted/split-command logic and policy rules rather than pure prefix matching (OpenAI exec-policy docs; Gemini `policy-engine.ts` + `shell-utils.ts`).

MVP fix:
- Narrow safe defaults to strictly read-only commands with no write-capable flags.
- Add lightweight deny-pattern checks for known write flags on “safe” roots.
- Medium-term: parser-backed policy evaluation for shell args.

### 5) Medium: No persistent, strong risk-state signaling for degraded sandbox

Evidence:
- Fallback warning is one-time print (`co_cli/main.py:89`).
- Status table marks sandbox as "Active" whenever not unavailable (`co_cli/status.py:147`), including subprocess mode.
- Banner shows mode text (`co_cli/banner.py:36`) but no persistent caution state beyond startup.

Why this matters:
- Risk state should be sticky and obvious, especially after fallback.

MVP fix:
- Add persistent “UNSANDBOXED” indicator in prompt/header and `/status` severity level.
- Distinguish `Active (full isolation)` vs `Active (no isolation)` explicitly in status severity.

### 6) Medium: Docker hardening is good baseline but not full jail profile

Evidence (already good):
- Drops all caps (`co_cli/sandbox.py:80`)
- `no-new-privileges` (`co_cli/sandbox.py:81`)
- PID/mem/cpu/network limits (`co_cli/sandbox.py:76-79`)
- Non-root user (`co_cli/sandbox.py:75`)

Remaining gap:
- No explicit seccomp/app-armor profile management in co runtime config.
- Workspace is mounted RW by default and only mode available.

MVP fix:
- Keep current defaults, but add explicit roadmap:
  - `sandbox_mode = read_only|workspace_write|danger_full_access`
  - optional hardened profile toggle (`seccomp`, `read_only_rootfs`, tempfs mounts)

---

## What Co Already Does Well

- Clean protocol split: Docker vs subprocess backend (`SandboxProtocol`).
- Approval orchestration separated from tool implementation (`requires_approval` flow).
- Timeout layering and process-group kill handling are sane.
- Subprocess environment is allowlist-based and pager-hardened (`co_cli/_sandbox_env.py`).
- Functional tests around shell mechanics are broad (`tests/test_shell.py`).

---

## Peer Convergence Snapshot

### Codex
- Explicit sandbox modes and approval modes; escalation is explicit and policy-aware.
- Exec-policy docs describe parser-based command analysis and command-splitting safety.

### Gemini CLI
- Explicit sandbox enablement and fatal handling when requested sandbox cannot be satisfied.
- Policy engine supports allow/deny/ask_user, priorities, modes, and parser-assisted shell splitting/redirection handling.

### Claude Code
- Permission model is explicit; “Bash tool bypasses permissions by design” documented as a special case.
- Focuses on explicit trust boundaries and enterprise policy controls.

### aider
- No true sandbox; relies heavily on explicit confirmation prompts.
- Demonstrates that if no jail exists, confirmation policy must stay strict and consistent.

---

## MVP Remediation Plan (Priority)

### P0 (Immediate)
1. Add `sandbox_fallback` with `error` option and enforce on `auto` Docker failure.
2. Disable `/yolo` in `isolation_level == "none"` unless explicit danger setting is enabled.
3. Unify `!` command path with approval/policy gate (or explicit high-friction opt-in when unsandboxed).
4. Tighten safe-command defaults and remove write-capable “safe” roots/flag patterns.

### P1 (Near-term)
1. Add persistent unsandboxed warning state in prompt/status.
2. Add end-to-end tests for policy invariants:
   - fallback + first shell command behavior
   - `/yolo` behavior under no isolation
   - `!` parity with orchestration approval path

### P2 (Post-MVP hardening)
1. Introduce explicit sandbox modes (`read_only`, `workspace_write`, `danger_full_access`).
2. Add optional hardened container profile controls (seccomp/profile knobs).
3. Move from prefix-only safe checks to parser-backed shell policy evaluation.

---

## Source References

- OpenAI Codex Security: https://developers.openai.com/codex/security
- OpenAI Codex Exec Policy: https://developers.openai.com/codex/exec-policy
- Anthropic Claude Code Security: https://docs.anthropic.com/en/docs/claude-code/security
- Gemini CLI sandbox docs (repo): `/Users/binle/workspace_genai/gemini-cli/docs/cli/sandbox.md`
- Gemini CLI policy docs (repo): `/Users/binle/workspace_genai/gemini-cli/docs/core/policy-engine.md`
- Gemini CLI policy engine (repo): `/Users/binle/workspace_genai/gemini-cli/packages/core/src/policy/policy-engine.ts`
- OWASP Docker Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html
- Docker runtime privilege/capabilities docs: https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities
- Docker CLI `docker run` reference (`--security-opt`): https://docs.docker.com/reference/cli/docker/container/run/

