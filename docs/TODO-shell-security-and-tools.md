# TODO: Critical Tool Convergence Program

**Date:** 2026-02-10
**Owner:** co-cli core
**Status:** Deferred to Phase 2.5 (after Phase 2c completion)
**Primary reference:** `docs/REVIEW-co-sandbox-shell-jail-vs-peers-2026-02-10.md`
**Merged scope:** incorporates former `docs/TODO-subprocess-fallback-policy.md`

---

## Program Objective

Close converged peer gaps while preserving first-principles security, MVP scope, and Pythonic simplicity.

**Capability targets**:
1. Shell/sandbox security hardening (S0+S1)
2. Workspace file tools (C1)
3. Task/todo tools (C3)

---

## Non-Negotiable Invariants

1. One trust boundary for command execution.
2. No approval bypass path for side effects.
3. Fail-closed or explicit user re-consent on sandbox degradation.
4. Least privilege by default.
5. Status surfaces must clearly expose active risk mode.
6. Prompt/tool contract must match runtime behavior exactly.
7. YOLO/auto-approve can reduce repeated prompts, but cannot override deny rules.
8. Approval grants must be scoped and expire; no unbounded blanket elevation.
9. Risk-state changes must trigger explicit re-consent before further side effects.

---

## Scope

This program covers:
- shell/sandbox control-plane hardening
- MVP implementations for file and todo tool families
- approval consistency and testable policy invariants
- prompt + docs alignment with real tool signatures and behavior

This program does not cover:
- multi-agent orchestration
- plugin marketplace UX
- long-running autonomous schedulers
- memory tools (shipped in Phase 1c)
- MCP client (covered by Phase 2a)

---

## Delivery Principles (First-Principles, MVP, Pythonic)

1. Fix safety/control-plane correctness before adding power.
2. Prefer explicit small APIs over broad magical abstractions.
3. Keep one obvious path for each operation.
4. Reuse existing `CoDeps` + deferred approval architecture.
5. Encode safety in code and tests, not in prompt wording.
6. Keep return contracts stable (`dict` with `display` for user-facing data tools).

---

## Execution Plan

## Phase S0: Shell Boundary Hardening (Blocker)

Goal: remove policy mismatches and establish a dependable execution boundary.

### Required Outcomes

- [ ] `!` direct shell path no longer bypasses policy/approval controls.
- [ ] sandbox auto-fallback has explicit policy (`warn` vs `error`) and secure default.
- [ ] `/yolo` behavior is constrained when isolation is `none`.
- [ ] `/yolo` cannot approve `deny` class actions under any isolation mode.
- [ ] `/yolo` becomes scoped/expiring approval caching, not session-wide blanket auto-approve.
- [ ] safe-command auto-approval is narrowed to truly safe forms.
- [ ] unsandboxed mode is persistently visible in UI/status.

### Tasks

- [ ] Unify `!command` execution with the same approval/policy guard used for deferred tool calls.
- [ ] Add `sandbox_fallback` setting and env mapping with explicit behavior matrix.
- [ ] Enforce guard: when `isolation_level == "none"`, prohibit blanket session-wide auto-approve and require narrow scoped grants.
- [ ] Replace global session-wide YOLO toggle with bounded approval scopes (tool class + risk class + duration/call count).
- [ ] Enforce immutable deny semantics: policy `deny` always denies, regardless of YOLO state.
- [ ] On sandbox downgrade (`full -> none`), clear active YOLO grants and require explicit re-consent.
- [ ] Tighten safe-command defaults and add deny patterns for write-capable flag variants.
- [ ] Add persistent unsandboxed indicator in banner/prompt/status.
- [ ] Update shell/sandbox design docs to reflect exact runtime policy.

### Detailed YOLO Safety Spec (Merged)

YOLO is retained only as approval-friction reduction inside policy boundaries:
- Scope: applies only to `ask_user` decisions, never `deny`.
- Shape: grant is bounded by tool family/risk class.
- Lifetime: grant expires by time and/or max approval count.
- Isolation gate: in `isolation_level == "none"`, only narrow per-call/per-tool grants are allowed, or YOLO is disabled.
- Re-consent: any runtime risk escalation clears grants and prompts again.

MVP defaults:
- deny override: forbidden
- default grant mode: next matching call only (or short TTL)
- unsandboxed mode: YOLO disabled by default

### Detailed Fallback Policy Spec (Merged)

`sandbox_fallback` applies only when `sandbox_backend == "auto"`:
- `warn` keeps current degraded startup path with explicit persistent warning.
- `error` fails fast and refuses unsandboxed fallback.

Behavior matrix:

| `sandbox_backend` | `sandbox_fallback` | Docker available | Result |
|---|---|---|---|
| `auto` | `warn` | Yes | `DockerSandbox` |
| `auto` | `warn` | No | `SubprocessBackend` + persistent warning |
| `auto` | `error` | Yes | `DockerSandbox` |
| `auto` | `error` | No | startup error (refuse degraded mode) |
| `docker` | (ignored) | Yes | `DockerSandbox` |
| `docker` | (ignored) | No | startup error |
| `subprocess` | (ignored) | (ignored) | `SubprocessBackend` |

Merged implementation checklist:
- [ ] Add `sandbox_fallback: Literal["warn", "error"] = "warn"` in `Settings`.
- [ ] Add env mapping for `CO_CLI_SANDBOX_FALLBACK`.
- [ ] Add `sandbox_fallback` default to `settings.defaults.json`.
- [ ] Implement fail-fast branch in `_create_sandbox()` for `auto + error + no Docker`.
- [ ] Add persistent risk-state flag to status/banner surfaces when fallback is active.

### Target Files

- `co_cli/main.py`
- `co_cli/_orchestrate.py`
- `co_cli/_approval.py`
- `co_cli/_commands.py`
- `co_cli/deps.py`
- `co_cli/config.py`
- `co_cli/status.py`
- `co_cli/_banner.py`
- `settings.defaults.json`
- `docs/DESIGN-09-tool-shell.md`

### Tests (New or Updated)

- [ ] `tests/test_shell_policy_invariants.py` (new):
- [ ] verify `!` path policy parity with orchestrated shell flow.
- [ ] verify fallback behavior (`warn`/`error`) under Docker unavailable conditions.
- [ ] verify `/yolo` restrictions when unsandboxed.
- [ ] verify YOLO cannot approve policy-denied actions.
- [ ] verify YOLO grants expire as configured (TTL and/or call budget).
- [ ] verify sandbox downgrade clears YOLO grants and forces re-consent.
- [ ] verify safe-command classifier rejects destructive flag variants.
- [ ] verify persistent risk-state rendering in status/banner surfaces.

### Exit Criteria

- [ ] No known approval bypass path remains.
- [ ] No silent fail-open to host shell without explicit policy.
- [ ] Policy invariants are covered by functional tests.

---

## Phase S1: Shell Policy Engine MVP Upgrade

Goal: move from brittle prefix checks toward structured command-policy evaluation while keeping implementation small.

### MVP Scope

- [ ] introduce policy table concept for shell decisions (`allow`, `ask_user`, `deny`) with priority ordering.
- [ ] keep prefix support but evaluate parsed command roots/subcommands where possible.
- [ ] conservative fallback: parser uncertainty downgrades to `ask_user`, never to `allow`.

### Tasks

- [ ] Define shell policy config schema for user/project settings.
- [ ] Add parser-assisted command root extraction for policy matching.
- [ ] Preserve current behavior as compatibility baseline, then tighten defaults.
- [ ] Document migration path from `shell_safe_commands` to policy rules.

### Exit Criteria

- [ ] shell command decisions are explicit and explainable.
- [ ] uncertain parsing cannot auto-allow.
- [ ] tests cover split-command + redirection + wrapper-stripping behavior.

---

## Phase C1: Workspace File Tools (Critical Capability)

Goal: stop overusing shell for standard read/write/edit/list operations.

### Tools

- `list_directory(path: str = ".", recursive: bool = False, limit: int = 200)`
- `read_file(path: str, max_chars: int = 20000)`
- `write_file(path: str, content: str, create_dirs: bool = False)`
- `edit_file(path: str, old: str, new: str, replace_all: bool = False)`

### Approval Policy

- no approval: `list_directory`, `read_file`
- requires approval: `write_file`, `edit_file`

### Security Constraints

- [ ] all path resolution bounded to workspace root.
- [ ] traversal and symlink escape blocked.
- [ ] deterministic size and output limits.

### Tasks

- [ ] add centralized path safety helper.
- [ ] implement file tools in one module.
- [ ] register tools in `get_agent()` with explicit approval flags.
- [ ] update prompts/examples to use real signatures.

### Tests

- [ ] list/read/write/edit happy paths.
- [ ] traversal and symlink escape rejection.
- [ ] approval wiring for write/edit.
- [ ] agent registration and return contract validation.

### Exit Criteria

- [ ] default file workflows use file tools, not shell.
- [ ] no path escape in functional tests.

---

## Phase C3: Todo Tools (Critical Capability)

Goal: explicit progress state instead of implicit plan text only.

### Tools

- `todo_create(items: list[str], replace: bool = False)`
- `todo_list(status: str | None = None)`
- `todo_update(id: str, status: str, note: str = "")`
- `todo_clear(completed_only: bool = False)`

### Data Model

- `id`, `text`, `status`, `created_at`, `updated_at`, `note`

### Approval Policy

- MVP session-local todos can be no-approval.
- revisit policy if/when persisted by default.

### Tests

- [ ] create/list/update/clear flows.
- [ ] status transition rules.
- [ ] deterministic id handling and validation.

### Exit Criteria

- [ ] agent can maintain visible, deterministic task state.

---

## Cross-Cutting Work

## Prompt/Contract Hygiene

- [ ] remove references to nonexistent tools/signatures.
- [ ] add prompt-contract tests tied to registered tool names.
- [ ] include command-policy guidance matching implemented behavior.

## Approval and Safety Governance

- [ ] maintain central read-only vs side-effectful classification table.
- [ ] assert no new bypass paths in review checklist.
- [ ] reject PRs that add alternate side-effect execution planes.

## Observability

- [ ] include new tools in `/tools` and status reporting.
- [ ] add trace tags for tool family and approval outcome.

## Documentation

- [ ] keep `DESIGN-*` docs synced with behavior.
- [ ] keep this TODO as forward backlog only; move implementation detail to design docs/tests when complete.

---

## Milestones and Sequence (PHASE 2.5)

⏸️ **All milestones deferred to Phase 2.5** (after Phase 2c completion)

## M0: Safety Foundation (Phase 2.5, Week 1)

- [ ] Phase S0 complete.

Exit:
- [ ] no approval bypass for shell execution.
- [ ] fallback behavior explicit and tested.
- [ ] unsandboxed risk state persistently visible.

## M1: Policy Uplift + Tools (Phase 2.5, Weeks 2-3)

- [ ] Phase S1 complete.
- [ ] Phase C1 complete.
- [ ] Phase C3 complete.

Exit:
- [ ] shell policy decisions deterministic and tested.
- [ ] file tool workflows replace shell for common operations.
- [ ] explicit task tracking is production-usable.

---

## Risk Register

1. Risk: expanding capability before fixing shell policy baseline.
- Mitigation: make S0 hard blocker.

2. Risk: reintroducing approval bypass through convenience paths.
- Mitigation: invariant tests + PR checklist requiring single control plane.

3. Risk: false-safe shell auto-approval from simplistic matching.
- Mitigation: policy engine uplift and conservative default-to-ask behavior.

4. Risk: path safety vulnerabilities in file tools.
- Mitigation: centralized path safety helper + adversarial tests.

5. Risk: prompt/docs drift from runtime.
- Mitigation: prompt-contract tests and mandatory docs sync per milestone.

---

## Program Definition of Done

- [ ] Shell/sandbox control plane is consistent and policy-safe.
- [ ] File and todo capability families are shipped at MVP level.
- [ ] Side effects are consistently behind approval policy.
- [ ] No silent fail-open behavior in default secure path.
- [ ] Prompt/tool contracts match runtime registration.
- [ ] Functional tests cover policy invariants and each capability family.
- [ ] Core workflows (`chat`, approval flow, status, existing tools) have no regressions.
