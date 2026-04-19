# Plan: Three Focused Cuts to Reduce SDK Coupling Where It Adds No Value

**Task type:** `refactor` — three independent cuts that remove SDK coupling that adds complexity without value. Scope is intentionally narrow; no parallel framework introduced.

## Context

co-cli is a pydantic-ai-based agentic system. The SDK provides `Agent`, `RunContext[CoDeps]`, `ToolReturn`, `ModelMessage`, deferred-tool mechanics, history processors, stream events — all of these ARE the architecture and stay.

An earlier draft of this plan (now superseded) proposed a translator module, app-type registry, `SegmentResult` discriminated union, and an SDK-import tripwire to quarantine pydantic-ai. That was the wrong instinct. In an SDK-based system, decoupling should target **non-necessary** coupling — places where SDK shapes are abused for purposes the SDK didn't design them for — not places where the SDK type IS the right vocabulary. Wrapping `AgentRunResult → SegmentResult` and `ModelRequest ← InterruptMarker` is ceremony: it doubles the surface (call site AND translator change on SDK shifts) without removing complexity.

Three smells qualify under the "non-necessary coupling" criterion:

1. **History processors mutate `ctx.deps`.** `append_recalled_memories` and `detect_safety_issues` lie about being pure transformers; the comment `INTENTIONAL DEVIATION from pydantic-ai's pure-transformer contract` (`co_cli/context/_history.py:720`, `:854`) is honest documentation of a layered hack. The work belongs in the orchestrator as preflight, not inside the SDK's transformer slot.
2. **MCP metadata inferred from wrapper depth.** `co_cli/agent/_mcp.py` walks `.wrapped` chains and uses `wrapper_count > 1` to infer the approval flag (`_mcp.py:71-86`). A future SDK change to wrapper composition can silently flip MCP approval behavior.
3. **Clarify rides the approval channel.** `QuestionRequired(ApprovalRequired)` + `meta["_kind"]=="question"` + `ToolApproved(override_args={"user_answer": ...})` smashes two distinct user interactions (asking permission vs asking a question) into one channel with string-keyed dispatch (`tool_approvals.py:21-30`, `orchestrate.py:201-210`).

The other six smells from the prior draft are not addressed here:
- Interrupt building `ModelRequest(UserPromptPart)` IS how you append an abort marker to history.
- `/compact` synthetic two-message pair IS the compaction mechanism.
- `TurnResult.output: Any` reflects a real `str | DeferredToolRequests` union; typing it as that union is fine, but inventing a wrapper type to avoid `Any` adds nothing.
- `ToolApprovalDecisions = DeferredToolResults` alias rename is cosmetic.
- `_preserve_search_tool_breadcrumbs` comment is documentation, not coupling.
- `Agent.instrument_all` at import time is a documented and harmless one-liner.

These stay.

## Problem & Outcome

**Problem:** Three places in co-cli abuse SDK shapes for purposes the SDK didn't design them for, and pay an ongoing complexity tax for it.

**Failure cost:** New contributors must read multi-paragraph deviation comments to understand what `_history.py` processors actually do. SDK upgrades risk silently changing MCP approval behavior. Question-asking and approval-granting share one tangled dispatch path that prevents adding new pause-for-human types (e.g., choice from list, file picker) cleanly.

**Outcome:** After this plan:
- History processors stay registered as pure transformers; preflight stages own state mutation and run explicitly before each segment.
- MCP toolsets record their policy at build time; discovery reads that policy directly.
- `clarify` dispatches on type, not on a metadata string. Resume payload construction still produces `ToolApproved` (SDK contract); only the orchestrator branching changes.

No translator module. No `app_types.py` registry. No `SegmentResult` discriminated union. No SDK-import tripwire test. No new framework.

## Scope

**In scope:**
1. Move `append_recalled_memories` and `detect_safety_issues` out of `history_processors=[...]` into preflight stages called explicitly by `run_turn()`.
2. Record MCP tool policy (approval, deferred) explicitly when toolsets are built; remove `.wrapped` walks and `wrapper_count > 1` inference.
3. Split `clarify` dispatch from `ApprovalRequired` dispatch in the orchestrator's deferred-tool collection step.

**Out of scope:**
- Translator module, app-type registry, `SegmentResult`/`TurnOutcome` discriminated unions, SDK-import tripwire test.
- Interrupt marker / `/compact` / `TurnResult.output: Any` — legitimate SDK use, not abuse.
- Cosmetic renames (`ToolApprovalDecisions` alias, instrumentation timing, breadcrumb comments).
- Updating `docs/specs/` — handled post-delivery via `/sync-doc`.

## Behavioral Constraints

1. **No product regression.** Approval UX, clarification UX, MCP tool discovery — byte-identical from the user's perspective.
2. **Each task independently shippable.** Three independent `/deliver` runs, zero cross-task dependencies.
3. **No new abstraction layer.** Each task removes coupling; none adds a translator, registry, or wrapper type to compensate.

## Implementation Plan

### ✓ DONE — TASK-1: MCP policy recorded at build time

```text
files:
  - co_cli/agent/_mcp.py

done_when: >
  _build_mcp_toolsets pairs each toolset with its policy (approval + deferred)
    recorded explicitly at construction time (e.g. a small dataclass or tuple
    held alongside the toolset list)
  AND discover_mcp_tools reads policy from that recorded structure, not from
    walking .wrapped chains
  AND grep "wrapper_count" and grep "\.wrapped" in co_cli/agent/_mcp.py both
    return zero hits
  AND uv run pytest -k mcp passes.

success_signal: >
  MCP tool discovery returns the same tool names, prefixes, and approval flags
  as before. Future SDK changes to wrapper composition cannot silently flip
  approval behavior because policy is no longer derived from wrapper topology.

prerequisites: []
```

### ✓ DONE — TASK-2: Preflight stages replace deps-mutating processors

```text
files:
  - co_cli/context/_history.py
  - co_cli/context/orchestrate.py
  - co_cli/agent/_core.py

done_when: >
  append_recalled_memories and detect_safety_issues are removed from
    Agent(history_processors=[...])
  AND the two functions stay in co_cli/context/_history.py, renamed to reflect
    their new role as explicit callables (e.g. build_recall_injection,
    build_safety_injection) with signatures that return injection content
    (or None) instead of transforming the message list in place
  AND they do NOT mutate ctx.deps internally; state writes to
    memory_recall_state and safety_state happen in run_turn() based on the
    return values
  AND run_turn() invokes them before each model-bound _execute_stream_segment
    — not once per turn; approval-resume segments that do NOT hit the model
    must skip preflight to preserve current token-accounting and recall gating
    behavior
  AND remaining processors (truncate_tool_results, compact_assistant_responses,
    summarize_history_window) stay registered as pure transformers
  AND grep "INTENTIONAL DEVIATION" in co_cli/context/_history.py returns zero hits
  AND uv run pytest passes the existing memory recall, safety detection, and
    summarization tests without modification.

success_signal: >
  Memory recall and safety detection inject the same system-prompt content into
  the conversation as before. The history-processor chain reads as a clean
  pure-transformer pipeline; state-mutation work is visible in the orchestrator
  where it belongs.

prerequisites: []
```

### ✓ DONE — TASK-3: Clarify is its own dispatch path

```text
files:
  - co_cli/context/tool_approvals.py
  - co_cli/context/orchestrate.py
  - co_cli/tools/user_input.py

done_when: >
  clarify's deferred-tool dispatch in _collect_deferred_tool_approvals branches
    on isinstance / a typed discriminator, not on meta.get("_kind") == "question"
  AND grep '"_kind"' in co_cli/context/orchestrate.py returns zero hits
  AND the resume payload still uses ToolApproved(override_args={"user_answer":
    ...}) because that IS pydantic-ai's resume contract — only the dispatch
    branching changes, not what is sent to the SDK
  AND uv run pytest passes the existing clarify and tool-approval tests.

  Optional: extract a build_clarify_resume helper if the clarify branch body
    exceeds ~3 lines after the split; otherwise inline construction is fine.
    The isinstance dispatch is the real win, not the helper.

success_signal: >
  clarify's UX is byte-identical: same prompt rendering, same answer injection,
  same resume behavior. Approval and clarification read as two separate concerns
  in the orchestrator instead of one branch with string-keyed dispatch.

prerequisites: []
```

## Delivery Order

Three independent tasks. Order is preference, not dependency:

1. **TASK-1** (MCP policy) — smallest, single file, zero blast radius beyond MCP discovery.
2. **TASK-2** (preflight) — biggest cleanup; removes the documented contract lie and the largest source of reader confusion in `_history.py`.
3. **TASK-3** (clarify split) — narrow change to orchestrator dispatch; isolated to the deferred-tool collection step.

Each ships as a single `/deliver` run. No `/orchestrate-dev` orchestration needed; each task is single-dev scope.

## Test Strategy

Each task is a behavior-preserving refactor. Acceptance is **byte-identical user-visible behavior** plus the done-when conditions:

1. **MCP tool list unchanged** — `discover_mcp_tools()` returns the same tool names, prefixes, and approval flags before and after TASK-1.
2. **Memory recall and safety detection content unchanged** — the same system-prompt content is injected into the conversation before and after TASK-2; existing memory/safety/summarization tests pass without modification.
3. **Clarify UX byte-identical** — `clarify(question, options)` produces the same prompt rendering and the same answer injection before and after TASK-3.
4. **Full pytest suite passes** after each task lands.

No new test files required beyond what existing coverage already provides; if existing coverage misses a behavior these tasks touch, add the test in the same `/deliver` run.

## Risks

### Risk: preflight relocation changes token accounting (TASK-2)

Moving `append_recalled_memories` out of `history_processors` means injection content lives in `message_history` when the segment starts, rather than appearing in the SDK's per-request transform. Validate via existing memory and summarization tests; the SDK reads from the same message list either way, so behavior should be unchanged. If a token-accounting drift is observed, the preflight implementation needs to insert at the same position the processor did.

### Risk: clarify resume protocol unchanged (TASK-3)

pydantic-ai's deferred-tool resume protocol only accepts `ToolApproved` / `ToolDenied`. The clarify split changes the **dispatch site**, not the resume protocol — the helper still returns `ToolApproved(override_args={"user_answer": ...})` because that's what the SDK expects. The win is removing the string-keyed branching from the orchestrator and giving `clarify` its own named seam, not changing what's sent back to the SDK.

### Risk: MCP test coverage for the policy switch (TASK-1)

If `tests/test_mcp*.py` only covers happy-path discovery, the wrapper-count → policy-record swap could regress edge cases (e.g. servers configured without `approval="ask"`). Verify the existing coverage exercises both `approval="ask"` and `approval=null` configurations before the change; if not, add the missing case in the same task.

## Ship Criteria

This plan is complete when:
- The three smells listed in Context are gone (verified by the grep checks in each task's `done_when`).
- MCP discovery, memory recall, safety detection, and clarify all behave identically to before.
- Acceptance tests 1–4 above pass.

---

# Audit Log

## Cycle C1 — Team Lead

**Pivot from prior draft.** An earlier 7-task version of this plan (translator module, `app_types.py` registry, `SegmentResult` discriminated union, SDK-import tripwire) was rescoped after user feedback identified it as architectural-purity work without proportional payoff:

> "the translator layer is overhead for no gain, the plan should not chase for puritom of app shape, which would reject most parts of sdk, this is already an sdk based agentic system, so the focus of decoupling are on non-necessary parts where coupling only adds complexity with no real value"

This rewrite applies that lens. The three remaining tasks each target a documented hack (the `INTENTIONAL DEVIATION` comments, the `.wrapped` walk, the `_kind` string dispatch) where the SDK is being abused for a purpose it wasn't designed for. The other six original smells are either legitimate SDK use or cosmetic.

The prior C1 critique by PO (translator allowlist broken on arrival, `SegmentResult` union landing too late, speculative split criterion) is fully resolved by the rescope: those constructs no longer exist in this plan.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, ship as three independent `/deliver` runs in the order listed.

## Cycle C1 — PO

**Assessment:** revise
**Blocking:** PO-M-1
**Summary:** The rescope lands correctly in principle — all three smells are genuine abuse-of-SDK rather than architectural-purity targets, and the six carve-outs are honestly called legitimate SDK use. TASK-1 is a clean approve as written. TASK-3 is directionally right but the done_when overspecifies a helper that is mostly moving construction to a different file. TASK-2 needs one scope call honored literally: a new `co_cli/context/preflight.py` module for two functions violates the plan's own "No new abstraction layer" constraint. Fix TASK-2's location and soften TASK-3's helper mandate and this is ready to ship.

**Major issues:**

- **PO-M-1** [TASK-2 — files / done_when]: Creating `co_cli/context/preflight.py` for exactly two functions (`append_recalled_memories`, `detect_safety_issues`) is the kind of sub-module that the rescope explicitly rejected. The Behavioral Constraint "No new abstraction layer. Each task removes coupling; none adds a translator, registry, or wrapper type to compensate" is bent here: a new module that exists only to hold the two functions that were removed from the processor list IS a new layer. Two functions is not a package. The real win is "these run explicitly in `run_turn()`, not as processor-list members" — that's independent of where the function bodies live. **Recommendation:** drop the new file. Keep the two functions in `co_cli/context/_history.py` (renaming them to reflect their new role, e.g. `build_recall_injection` / `build_safety_injection`, and changing the return signature to injection-content-or-None) and have `run_turn()` import and call them directly. The grep check on `INTENTIONAL DEVIATION` still applies — the deviation comment goes away because the functions are no longer registered as pure transformers. Also clarify in the done_when that preflight runs once per **model-bound** segment (matching current processor behavior), not once per turn — on approval-resume segments where the SDK skips `ModelRequestNode`, preflight must also skip, otherwise memory recall fires on segments that don't hit the model. This is the token-accounting risk the plan already flags, promoted to a scoping requirement so the dev doesn't have to rediscover it.

**Minor issues:**

- **PO-m-1** [TASK-3 — done_when]: The mandate for a `build_clarify_resume(tool_call_id, answer)` helper is the weakest part of TASK-3. The helper still returns `ToolApproved(override_args={"user_answer": ...})` — it's a 2-line function that moves the same construction from `orchestrate.py` to `tool_approvals.py` (or `user_input.py`). That's a readability nudge, not decoupling. The real win is the isinstance/typed-discriminator dispatch that separates "ask permission" from "ask question" as two branches in `_collect_deferred_tool_approvals`. **Recommendation:** demote the helper from a done_when requirement to "optional — extract if the dispatch branch body is more than ~3 lines after the split; otherwise inline is fine". Keep the grep on `"_kind"` returning zero hits as the hard success signal — that's the behavior-bearing part.

- **PO-m-2** [TASK-1 — done_when]: The `approval` field on `ToolInfo` is derived from MCP server config (`cfg.approval == "ask"`) at build time — that's already known at `_build_mcp_toolsets` time. The cleanest shape is likely `(toolset, approval: bool, prefix: str)` tuple or a small `MCPToolsetPolicy` frozen dataclass held alongside the toolset, which the plan hints at ("a small dataclass or tuple"). **Recommendation:** no change needed — the done_when is right to stay agnostic on the exact shape. Just noting this as confirmation that the task is cleanly scoped.

- **PO-m-3** [Context / Scope]: The Context section's "six smells not addressed" list is exemplary — calling out interrupt-marker / `/compact` / `TurnResult.output: Any` as legitimate SDK use is the right framing and prevents scope creep in future reviews. No action; this is strengthening the plan, not weakening it.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| PO-M-1   | adopt    | Two functions don't justify a new module; "No new abstraction layer" applies to TASK-2's own structure too. Per-segment timing caveat is a real correctness requirement, not a post-hoc risk. | TASK-2 `files:` drops `co_cli/context/preflight.py (new)`. `done_when` rewritten: functions stay in `_history.py` renamed to `build_recall_injection` / `build_safety_injection`; timing specified as per model-bound segment (approval-resume segments without model calls skip preflight). |
| PO-m-1   | adopt    | Helper is 2-line relocation; isinstance dispatch is the real win. | TASK-3 `done_when` demotes the helper to optional (extract only if branch body > ~3 lines); isinstance dispatch + `_kind` grep remain as hard checks. Outcome bullet rewritten to drop "clarify-specific resume helper" language. |
| PO-m-2   | no-action | Confirmation, not a change request. | — |
| PO-m-3   | no-action | Confirmation, not a change request. | — |

**Stop conditions status.** PO was the only critic spawned this cycle (user asked for PO Gate 1 only). The blocker is resolved; remaining minors were adopted or confirmations. Per the diminishing-returns shortcut, this qualifies as done: zero unresolved blocking items, two adopts, no rejects.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, ship as three independent `/deliver` runs in the order listed.

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `wrapper_count` and `.wrapped` grep return zero hits; `uv run pytest -k mcp` passes | ✓ pass |
| TASK-2 | `INTENTIONAL DEVIATION` grep returns zero hits; `build_recall_injection`/`build_safety_injection` as preflight callables; `uv run pytest` passes | ✓ pass |
| TASK-3 | `"_kind"` grep in `orchestrate.py` returns zero hits; isinstance dispatch in `_collect_deferred_tool_approvals`; `uv run pytest` passes | ✓ pass |

**Tests:** full suite — 542 passed, 0 failed
**Independent Review:** 1 blocking finding (double-injection of date+personality on retry loops) found and fixed before integration; clean after fix
**Doc Sync:** fixed — `core-loop.md`, `prompt-assembly.md`, `cognition.md`, `personality.md`, `compaction.md`, `tools.md` updated; all other specs clean

**Overall: DELIVERED**
All three SDK coupling smells removed. History processors are a clean pure-transformer chain; safety and recall run as explicit preflight callables returning ephemeral `list[ModelMessage]` (not stored back to `turn_state.current_history`, preserving retry-iteration invariant). MCP policy recorded at build time via `_MCPToolsetEntry` frozen dataclass. `clarify` dispatches on `"question" in meta` typed discriminator, not on `meta.get("_kind") == "question"` string.

## Implementation Review — 2026-04-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `wrapper_count`/`.wrapped` grep zero; pytest -k mcp passes | ✓ pass | `_mcp.py:15-28` — `_MCPToolsetEntry` frozen dataclass with `approval: bool`, `prefix: str`; `_mcp.py:90-105` — `discover_mcp_tools` reads `entry.approval` directly; grep confirms zero `.wrapped` refs |
| TASK-2 | `INTENTIONAL DEVIATION` grep zero; preflight callables; pytest passes | ✓ pass | `_core.py:139-143` — 3 processors only; `_history.py:707-762` `build_recall_injection` returns `(ModelRequest, int, bool)`; `orchestrate.py:539-581` `_run_model_preflight` returns ephemeral `list[ModelMessage]` without mutating `turn_state.current_history`; retry loops at lines 675, 702 re-enter clean `current_history` |
| TASK-3 | `"_kind"` grep zero; isinstance dispatch; pytest passes | ✓ pass | `orchestrate.py:202` — `if "question" in meta:`; `tool_approvals.py:23-27` — `QuestionRequired` docstring documents typed discriminator; `orchestrate.py:208-210` — `ToolApproved(override_args={"user_answer": answer})` resume unchanged |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale docstring: `append_recalled_memories` | `tests/_timeouts.py:58` | minor | Updated to `build_recall_injection` |

### Tests
- Command: `uv run pytest -x -v`
- Result: 542 passed, 0 failed
- Log: `.pytest-logs/20260419-*-review-impl.log`

### Doc Sync
- Scope: full — tasks rename public API (`build_recall_injection`, `build_safety_injection`), touch shared orchestration modules
- Result: fixed — `core-loop.md`, `prompt-assembly.md`, `cognition.md`, `personality.md`, `compaction.md`, `tools.md`; all other specs clean

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, MCP 1 ready, all components confirmed
- `success_signal` TASK-1: MCP tool discovery reads policy from `_MCPToolsetEntry.approval` — no wrapper-chain walk path exists in code
- `success_signal` TASK-2: History-processor chain reads clean (3 pure transformers); preflight owns state writes, visible in `_run_model_preflight`
- `success_signal` TASK-3: Approval and clarification are two separate named branches in `_collect_deferred_tool_approvals`

### Overall: PASS
All three SDK coupling smells removed, 542 tests green, lint clean, docs synced. Ship-ready.
