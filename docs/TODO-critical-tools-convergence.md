# TODO: Critical Tool Convergence Program (Rewritten)

**Date:** 2026-02-10  
**Owner:** co-cli core  
**Status:** Active rewrite baseline  
**Primary reference:** `docs/REVIEW-co-sandbox-shell-jail-vs-peers-2026-02-10.md`

---

## Why This Rewrite Exists

The previous plan focused on adding missing tool families. That is still needed, but the latest review found a more fundamental issue:

- the main risk is **control-plane mismatch** across shell entry paths and fallback modes,
- not simply a lack of tool count.

This rewritten TODO makes shell/sandbox policy integrity the first milestone. New tool families ship only after that baseline is stable.

---

## Program Objective

Close converged peer gaps while preserving first-principles security, MVP scope, and Pythonic simplicity.

Converged capability targets remain:
1. Workspace file tools
2. Persistent memory tools
3. Task/todo tools
4. MCP client integration

But now with explicit prerequisite:
- harden shell/sandbox approval boundary before capability expansion.

---

## Non-Negotiable Invariants

1. One trust boundary for command execution.
2. No approval bypass path for side effects.
3. Fail-closed or explicit user re-consent on sandbox degradation.
4. Least privilege by default.
5. Status surfaces must clearly expose active risk mode.
6. Prompt/tool contract must match runtime behavior exactly.

---

## Scope

This program covers:
- shell/sandbox control-plane hardening
- MVP implementations for file/memory/todo/MCP tool families
- approval consistency and testable policy invariants
- prompt + docs alignment with real tool signatures and behavior

This program does not cover:
- multi-agent orchestration
- plugin marketplace UX
- long-running autonomous schedulers
- non-essential integration expansion beyond MCP v1 stdio

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
- [ ] safe-command auto-approval is narrowed to truly safe forms.
- [ ] unsandboxed mode is persistently visible in UI/status.

### Tasks

- [ ] Unify `!command` execution with the same approval/policy guard used for deferred tool calls.
- [ ] Add `sandbox_fallback` setting and env mapping with explicit behavior matrix.
- [ ] Enforce guard: when `isolation_level == "none"`, disable or high-friction-gate session-wide auto-approve.
- [ ] Tighten safe-command defaults and add deny patterns for write-capable flag variants.
- [ ] Add persistent unsandboxed indicator in banner/prompt/status.
- [ ] Update shell/sandbox design docs to reflect exact runtime policy.

### Target Files

- `co_cli/main.py`
- `co_cli/_orchestrate.py`
- `co_cli/_approval.py`
- `co_cli/_commands.py`
- `co_cli/config.py`
- `co_cli/status.py`
- `co_cli/banner.py`
- `docs/DESIGN-09-tool-shell.md`

### Tests (New or Updated)

- [ ] `tests/test_shell_policy_invariants.py` (new):
- [ ] verify `!` path policy parity with orchestrated shell flow.
- [ ] verify fallback behavior (`warn`/`error`) under Docker unavailable conditions.
- [ ] verify `/yolo` restrictions when unsandboxed.
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

## Phase C2: Memory v1 Tools (Critical Capability)

Goal: provide explicit durable memory primitives under user control.

### Tools

- `save_memory(key: str, value: str, scope: str = "user")`
- `recall_memory(query: str, limit: int = 10)`
- `list_memories(limit: int = 50, scope: str | None = None)`
- `delete_memory(key: str, scope: str = "user")`

### Storage

- SQLite in XDG data path (preferred MVP).

### Approval Policy

- no approval: `recall_memory`, `list_memories`
- requires approval: `save_memory`, `delete_memory`

### Constraints

- [ ] durable preference/fact storage only.
- [ ] avoid transient task state in memory store.
- [ ] enforce key/value limits and normalization.

### Tests

- [ ] save/recall/list/delete roundtrip.
- [ ] upsert behavior and scope filters.
- [ ] persistence across restart.
- [ ] approval wiring for writes.

### Exit Criteria

- [ ] memory behavior is explicit, local, and predictable.

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

## Phase C4: MCP Client v1 (Critical Capability)

Goal: extensibility without native reimplementation of every integration.

### Scope

- stdio transport first
- project/user config
- tool discovery at startup
- approval compatibility with host policy flow

### Tasks

- [ ] add `mcp_servers` config schema.
- [ ] implement MCP setup/runtime wiring helper.
- [ ] attach MCP toolsets in agent creation.
- [ ] ensure lifecycle management and teardown correctness.
- [ ] prevent tool name collisions via server prefixing.
- [ ] expose MCP status in `co status` surfaces.

### Tests

- [ ] config parse and validation coverage.
- [ ] tool attachment and prefix collision prevention.
- [ ] deferred approval compatibility checks.

### Exit Criteria

- [ ] at least one stdio MCP server works end-to-end.
- [ ] native + MCP tools coexist without approval regressions.

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

## Milestones and Sequence

## M0: Safety Foundation (Week 1)

- [ ] Phase S0 complete.

Exit:
- [ ] no approval bypass for shell execution.
- [ ] fallback behavior explicit and tested.
- [ ] unsandboxed risk state persistently visible.

## M1: Policy Uplift + File Tools (Weeks 2-3)

- [ ] Phase S1 complete.
- [ ] Phase C1 complete.

Exit:
- [ ] shell policy decisions deterministic and tested.
- [ ] file tool workflows replace shell for common operations.

## M2: Memory + Todos (Weeks 3-4)

- [ ] Phase C2 complete.
- [ ] Phase C3 complete.

Exit:
- [ ] durable memory and explicit task tracking are production-usable.

## M3: MCP v1 (Weeks 4-5)

- [ ] Phase C4 complete.

Exit:
- [ ] MCP stdio server integration is stable with approval parity.

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

5. Risk: MCP lifecycle complexity destabilizes chat loop.
- Mitigation: stdio-only MVP and strict config validation first.

6. Risk: prompt/docs drift from runtime.
- Mitigation: prompt-contract tests and mandatory docs sync per milestone.

---

## Program Definition of Done

- [ ] Shell/sandbox control plane is consistent and policy-safe.
- [ ] All four critical capability families are shipped at MVP level.
- [ ] Side effects are consistently behind approval policy.
- [ ] No silent fail-open behavior in default secure path.
- [ ] Prompt/tool contracts match runtime registration.
- [ ] Functional tests cover policy invariants and each capability family.
- [ ] Core workflows (`chat`, approval flow, status, existing tools) have no regressions.

---

## Immediate Next Actions

1. Execute Phase S0 tasks first.
2. Add `tests/test_shell_policy_invariants.py` and make it gating.
3. Update `docs/DESIGN-09-tool-shell.md` after S0 lands.
4. Start C1 file tools only after S0 exit criteria are green.
