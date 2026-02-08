# TODO: Agent Tool-Call + Recursive Flow Hardening

**Date:** 2026-02-08  
**Origin:** Review of current agent design with focus on tool-calling, deferred approvals, and recursive loop behavior.

---

## 1. Findings (Current State)

### F1 — Strong baseline alignment (keep)

- Explicit side-effect gating with `requires_approval=True` is correctly implemented for write/risky tools.
- Deferred approval loop correctly uses `while isinstance(result.output, DeferredToolRequests)` so chained side effects can be approved step-by-step.
- Tool business logic is separated from approval UI logic (tools do not prompt users directly).

Code loci:
- `co_cli/agent.py`
- `co_cli/main.py`

### F2 — Recursive budget is not clearly per-turn cumulative (high risk)

- `UsageLimits(request_limit=...)` is re-created on each deferred resume call.
- Without carrying `RunUsage`, request budgeting can reset per hop instead of enforcing one budget across the whole user turn.
- This can increase loop/exhaustion risk in long tool chains, especially in YOLO mode.

Code loci:
- `co_cli/main.py` (`_stream_agent_run`, `_handle_approvals`, `chat_loop`)

### F3 — Web tools are behind peer-converged permission/safety baseline (high risk)

- `web_fetch` has no SSRF/private-network guard, no redirect revalidation, no domain policy controls, and no web permission mode (`allow|ask|deny`).
- Current architecture treats web tools as read-only and ungated by default; top systems converge on explicit URL/network policy controls.

Code loci:
- `co_cli/tools/web.py`
- `co_cli/agent.py`

### F4 — Provider-level malformed tool-call failures are not normalized/retried (medium risk)

- Provider HTTP/tool-call argument failures can escape as generic turn-ending errors.
- Existing retry strategy primarily handles `ModelRetry` from tool code, not all model/provider structured-output failures.

Code loci:
- `co_cli/main.py`
- `docs/TODO-ollama-tool-call-resilience.md`

### F5 — Orchestration is centralized but UI-coupled (medium risk, quality debt)

- Approval and streaming orchestration are tightly coupled to Rich + prompt input.
- This limits testability and reuse for headless/API/non-interactive execution paths.

Code loci:
- `co_cli/main.py`
- `docs/TODO-approval-flow-extraction.md`

---

## 2. Recursive Flow Loci (Tool Call Is Not the Only One)

The general recursive loop includes these loci:

1. Model inference recursion inside `agent.run_stream_events(...)`.
2. Tool execution recursion for read-only tools (no human gate).
3. Deferred approval recursion (`DeferredToolRequests` -> approvals -> resume).
4. Denial recursion (`ToolDenied`) where model replans after rejection.
5. Tool error recursion (`ModelRetry`) where model self-corrects and retries.
6. Interrupt repair path (`_patch_dangling_tool_calls`) to preserve valid history shape.
7. Pre-request history processor recursion (`truncate_history_window` summarization calls).

Implication: evaluating only tool-call routing is insufficient; loop safety must be reasoned across all seven loci.

---

## 3. Gap vs. Converged Best Practice

### Aligned

- Per-tool approval model for side effects.
- Human-in-the-loop deny/approve semantics with structured feedback.
- Sandbox-first execution for shell commands.

### Not yet aligned

- Web safety and policy envelope (network/domain/permission mode).
- Unified per-turn recursion budget accounting across deferred resumes.
- Decoupled orchestration layer for reliable testing and multiple frontends.

---

## 4. Implementation Plan

### Phase A — Per-turn recursion budget hardening

- [ ] Carry `RunUsage` across initial run and every deferred resume.
- [ ] Enforce a single `UsageLimits` budget across the whole user turn.
- [ ] Add regression test: multi-hop deferred approvals cannot exceed turn budget.

### Phase B — Web policy convergence (security)

- [ ] Implement SSRF/private-network guard + redirect target re-check.
- [ ] Add content-type allowlist + byte-limit guardrails.
- [ ] Add `web_permission_mode: allow|ask|deny`.
- [ ] Add domain allow/block policy settings.

### Phase C — Provider/tool-call resilience

- [ ] Add targeted retry strategy for recoverable provider/tool-call HTTP failures.
- [ ] Normalize error messages for model self-healing while preventing silent loops.
- [ ] Add integration cases for malformed tool-call recovery behavior.

### Phase D — Orchestration extraction

- [ ] Extract streaming + approval state machine into `co_cli/_orchestrate.py`.
- [ ] Keep `main.py` focused on CLI UI wiring only.
- [ ] Add non-interactive functional tests for orchestrator callbacks.

---

## 5. Acceptance Criteria

- No deferred approval chain can exceed configured per-turn request budget.
- Web fetch rejects private/loopback/link-local/metadata targets and unsafe redirects.
- `allow|ask|deny` web permission behavior is enforced and tested.
- Recoverable provider tool-call failures retry predictably and do not terminate turns unnecessarily.
- Orchestrator behavior is testable without terminal I/O.

---

## 6. Related Docs

- `docs/TODO-web-tool-hardening.md`
- `docs/TODO-ollama-tool-call-resilience.md`
- `docs/TODO-approval-flow-extraction.md`
- `docs/TODO-approval-interrupt-tests.md`
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
