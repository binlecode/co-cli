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

### F4 — Provider-level malformed tool-call failures are not fully normalized (medium risk)

- HTTP 400 reflection-retry is implemented, but error messages from other provider failures are not yet normalized for model self-healing.
- No integration test coverage for malformed tool-call recovery behavior.

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
- Per-turn cumulative recursion budget across deferred resumes.
- Web safety: SSRF guard, content-type allowlist, domain policy, permission mode.
- HTTP 400 reflection-retry for recoverable provider failures.

### Not yet aligned

- Normalized error messages for all provider/tool-call failures (beyond HTTP 400).
- Decoupled orchestration layer for reliable testing and multiple frontends.

---

## 4. Implementation Plan

### Phase C — Provider/tool-call resilience (remaining)

- [ ] Normalize error messages for model self-healing while preventing silent loops.
- [ ] Add integration cases for malformed tool-call recovery behavior.

### Phase D — Orchestration extraction

- [ ] Extract streaming + approval state machine into `co_cli/_orchestrate.py`.
- [ ] Keep `main.py` focused on CLI UI wiring only.
- [ ] Add non-interactive functional tests for orchestrator callbacks.

---

## 5. Acceptance Criteria

- Recoverable provider tool-call failures retry predictably and do not terminate turns unnecessarily.
- Orchestrator behavior is testable without terminal I/O.

---

## 6. Related Docs

- `docs/TODO-web-tool-hardening.md`
- `docs/TODO-ollama-tool-call-resilience.md`
- `docs/TODO-approval-flow-extraction.md`
- `docs/TODO-approval-interrupt-tests.md`
- `docs/RESEARCH-cli-agent-tools-landscape-2026.md`
