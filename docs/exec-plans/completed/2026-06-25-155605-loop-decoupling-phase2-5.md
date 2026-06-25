# Phase 2.5 — In-turn agent-as-tool delegation (read-mostly child)

**Parent milestone:** `2026-06-24-234633-loop-decoupling-milestone.md` (Phase 2.5). **Design:** `2026-06-24-234633-loop-decoupling-design.md` §6.12. **Survey:** `docs/reference/RESEARCH-loop-decoupling-peer-survey.md` §"Subagent engagement".

This phase plan details the read-mostly slice (Option A). Write-capable children + approval propagation are **Phase 3.5** (separate plan, depends on Phase 3).

## Context

The owned loop (Phases 1–2) gives co a single subagent driver, `run_standalone_owned` (`co_cli/agent/loop.py:392`), today reached only by **daemons** via `run_standalone(spec, deps, prompt)` (`co_cli/agent/run.py:18`) — a direct call, not a tool the model emits. There is **no in-turn delegation**: the orchestrator cannot hand a subtask to a child mid-turn.

Peer-convergent finding (4-repo survey): every peer that has delegation engages a task agent **as a tool through the common dispatch protocol** — hermes `delegate_task`, openclaw `sessions_spawn`, codex `spawn_agent`; opencode's un-built `task` is also slated as a tool. From an agent's view there is no "subagent" in its surface, only tools.

Grounded current-state facts (verified at HEAD):
- Tools are `@agent_tool`-decorated `async def fn(ctx, …)` returning a value wrapped into a `ToolReturnPart` by `_to_return_part` (`dispatch.py:130`); `dispatch_tools` (`dispatch.py:260`) runs them, parallel ≤cap, in original order.
- **The `@agent_tool` wrapper `_dispatch_capped` acquires `ctx.deps.tool_dispatch_sem` around every tool body** (`co_cli/tools/agent_tool.py:64-69`); `TOOL_REGISTRY_BY_NAME` returns that wrapper (`co_cli/tools/agent_tool.py:74`).
- `fork_deps(base)` (`deps.py:405`) shares `usage_accumulator`, `file_tracker`, `resource_locks`, **and `tool_dispatch_sem`** by reference; resets `CoRuntimeState` and **increments `agent_depth`** (`deps.py:447`). `toolset` is intentionally excluded.
- `run_standalone_owned` (`loop.py:392`): sets `deps.toolset = _build_subagent_toolset(spec)` from `spec.tool_names` **itself** (`loop.py:410`), forces structured output (`allow_text_output=False`, `loop.py:442`), validates the `final_result` call into `spec.output_type`, **hardcodes `settings_noreason`** (`loop.py:418`), and calls `record_usage(deps, turn_usage)` itself (`loop.py:493`). **It returns `None` when the child exhausts `default_budget` without a `final_result` call, or breaks on `cap_state.hard_stop` (`loop.py:434,488`).**
- `record_usage` writes **only** the in-memory `usage_accumulator` (`co_cli/observability/usage.py:49`); the durable JSONL ledger line is written once at the turn boundary by `main.py:130` reading `deps.usage_accumulator`. Because fork shares the accumulator by reference, **child tokens already roll into the parent turn with no double-count** — verified, not assumed.
- Approval-required tools to EXCLUDE from a read-mostly child: `tasks/control.py:37`, `memory/manage.py`, `google/gmail.py:143`, `system/skills.py` (manage ops), `files/write.py:265,320`, `user_profile/write.py:25`.

## Problem & Outcome

**Problem:** co has no way to isolate a subtask's context. There is no second, lossless context lever — compaction (the 5-processor chain) is the only one today, and it summarizes *after* the parent has already absorbed the subtask's full tool transcript, lossily.

**Outcome:** the orchestrator can emit a `delegate` tool call that runs a child agent in an **isolated forked context** with a **read-mostly** tool surface, and receives back only a **distilled summary** as the tool result. The child's intermediate tool calls/results never enter the parent's history.

**Failure cost:** without this, the only lever for a long read/gather task is compaction — which degrades the parent's working context lossily, and only after the noise has already entered the window. Small-model answer quality drops as the window fills with tool output the parent never needed to retain.

## Scope

**In:** a `delegate` tool (owned-path-only); an in-turn delegation driver wrapping `run_standalone_owned` with the four interface seams; a read-mostly child spec + trivial structured output; a one-line orchestrator-facing when-to-delegate heuristic; visibility wiring.

**Out:**
- **Write-capable children + child→parent approval propagation** — Phase 3.5 (depends on Phase 3). The 2.5 child surface has **no approval-required tools**, so 2.5 is fully decoupled from approval.
- Background/async delegation (2.5 is synchronous — the tool call blocks until the child returns).
- Nested multi-level delegation. Depth cap = 1 is the **cutover value, not a design ceiling** — a future nested-delegation need re-opens it as scope (consistent with the milestone's deferral framing), not as undoing a baked-in assumption.
- The graph path — delegation is owned-path-only.

## Behavioral Constraints

- **Additive only.** No existing eval calls `delegate`; owned-vs-graph parity on existing flows is unchanged. The graph path is untouched.
- **Context isolation is the contract.** The child's intermediate `ToolReturnPart`s must NOT appear in the parent history — only the final summary, as the `delegate` tool result.
- **No nested semaphore starvation.** A delegated child must NOT contend with the parent for `tool_dispatch_sem` slots (the parent holds slots while `delegate` runs). The child gets its **own** dispatch semaphore (see TASK-2 / Design).
- **Child usage rolls into the parent turn** (already true via the shared `usage_accumulator` — assert, don't re-engineer).
- **Recursion bounded.** Depth cap = 1: the child cannot delegate (tool absent from its surface) and `agent_depth` is the backstop.

## High-Level Design

```
orchestrator (owned loop) emits ToolCallPart(delegate, {task})
  → dispatch_tools runs delegate(ctx, task)        # delegate is is_concurrent_safe=False → runs alone
      → guard: ctx.deps.runtime.agent_depth >= DELEGATE_DEPTH_CAP(=1) → return refusal string
      → child_deps = fork_deps(ctx.deps, share_dispatch_sem=False)   # OWN sem; agent_depth+1; shares usage_accumulator
      → result = run_standalone_owned(DELEGATE_CHILD_SPEC, child_deps, task, settings=<parent turn settings>)
          ↑ run_standalone_owned sets child toolset from spec.tool_names; child history is LOCAL to child_deps
      → return result.summary                        # becomes the delegate ToolReturnPart.content
  → parent appends ONE ToolReturnPart (the summary); child transcript never enters parent history
```

- **Semaphore (the CD-M-1 fix):** every owned-path tool body acquires `tool_dispatch_sem` via the `@agent_tool` wrapper (`co_cli/tools/agent_tool.py:66`), so `delegate` holds a slot for the child's entire synchronous lifetime. Since `fork_deps` shares the sem by reference, a child drawing slots from the **same** pool starves behind the parent's held slots (hard deadlock once the parallel cap rises or nesting deepens). Fix: the in-turn child receives its **own** `tool_dispatch_sem` (new `share_dispatch_sem=False` path on `fork_deps`), fully decoupling child tool concurrency from parent-held slots. Additionally `delegate` is `is_concurrent_safe=False`, which sets `sequential=True` on its `ToolDefinition` (`toolset.py:130`) so dispatch runs it after the `gather` batch (`dispatch.py:310-314`) — it never co-occupies a parallel batch with sibling tools (a heavyweight orchestration handoff should run alone). (Note: the pre-existing double-acquire — `dispatch.py:185` + the wrapper — is a separate efficiency observation, NOT fixed here; surgical scope.)
- **Child spec** (`DELEGATE_CHILD_SPEC`): a `TaskAgentSpec` with curated read-mostly `tool_names` (file read/search, web search/fetch, memory search/view, session search/view, todo read, capabilities, vision view — all `is_approval_required=False`, and **excluding `delegate`**), instructions to do the subtask and return a concise distilled result, `output_type=DelegationResult`, `default_budget = DELEGATE_CHILD_BUDGET` (a named constant, value mirroring the daemon review budget scale).
- **Output type** `DelegationResult(BaseModel)`: a single `summary: str`. Reuses `run_standalone_owned`'s forced-output-tool mechanism; `delegate` returns `result.summary`.
- **Child model settings:** the **parent turn's** settings (interactive foreground work) — requires a new `settings` param on `run_standalone_owned` (currently hardcoded `settings_noreason` at `loop.py:418`; the daemon path keeps `settings_noreason`).
- **`delegate` visibility = ALWAYS (orchestrator-only).** Overrides the episodic-tool DEFERRED default deliberately: this is a flagship new capability whose value depends on the model actually reaching for it. A DEFERRED `delegate` is hidden until a `tool_view` reveal (`toolset.py:82-88`), compounding the under-firing risk (PO-M-1). ALWAYS makes it discoverable at one tool-def of prefill — the right trade for a capability we want exercised. Revisit to DEFERRED only if it proves noisy.
- **When-to-delegate heuristic (PO-M-1):** the `delegate` tool description + a one-line orchestrator instruction anchor on an observable cue — *"delegate a multi-step read/search/gather subtask whose intermediate results you won't need to retain; do small one-shot lookups inline."* A near-unconditional reflex on a concrete cue, not a judgment call (counters small-model under-firing).

## Tasks

### ✓ DONE TASK-1 — Read-mostly child spec + structured output
- `files:` `co_cli/agent/delegation.py` (new)
- Define `DelegationResult(BaseModel)` (`summary: str`), `DELEGATE_DEPTH_CAP = 1`, `DELEGATE_CHILD_BUDGET` (named constant, value on the daemon-review scale — cite `_reviewer.py:83`'s budget when setting it), and `DELEGATE_CHILD_SPEC: TaskAgentSpec` with a curated read-mostly `tool_names` tuple (every name `is_approval_required=False`; `delegate` excluded), an `instructions(deps)` builder, `output_type=DelegationResult`.
- `done_when:` a test asserts **every** `tool_name` resolves in `TOOL_REGISTRY_BY_NAME` and **none** has `is_approval_required=True` (read off `ToolInfo`); `delegate` is absent from the tuple; the spec drives a child end-to-end returning a `DelegationResult` (via TASK-2's test).
- `success_signal:` a delegated child can read/search and produce a one-field summary.
- `prerequisites:` none.

### ✓ DONE TASK-2 — In-turn delegation driver + own-sem fork
- `files:` `co_cli/agent/delegation.py`, `co_cli/deps.py` (add `share_dispatch_sem: bool = True` to `fork_deps`; when False, the child gets a fresh `asyncio.Semaphore(MAX_TOOL_DISPATCH_WORKERS)`), `co_cli/agent/loop.py` (add a `settings` param to `run_standalone_owned`, defaulting to today's `settings_noreason` so the daemon path is unchanged)
- `delegate_to_child(parent_deps, task)`: (1) depth-guard on `parent_deps.runtime.agent_depth >= DELEGATE_DEPTH_CAP` → return a refusal string, no fork; (2) `fork_deps(parent_deps, share_dispatch_sem=False)`; (3) `result = run_standalone_owned(DELEGATE_CHILD_SPEC, child_deps, task, settings=<parent turn settings>)`; (4) **if `result is None` (child exhausted `default_budget` or hard-stopped without a `final_result` call — see Context) return a fixed fallback string** (e.g. "Delegated subtask did not produce a result within its budget."), else return `result.summary`. Do **not** set `child_deps.toolset` (the driver delegates that to `run_standalone_owned`/`spec.tool_names`). Usage rollup needs **no new code** — assert the shared accumulator already carries it. Let `CancelledError` propagate (cancels the child).
- `settings` plumbing note: `run_standalone_owned`'s new param is `settings: ModelSettings | None = None`, resolved to today's `deps.model.settings_noreason` inside when `None` (a `deps`-derived value can't be a def-time default) — keeping the daemon path byte-for-byte unchanged.
- `done_when:` an integration test from a parent `CoDeps`: child runs and returns a summary; the parent's `usage_accumulator` total **increases by** the child's tokens; a call at `agent_depth == DELEGATE_DEPTH_CAP` is refused without forking; **a child that returns `None` (budget exhausted / hard-stop, with no `final_result`) yields the fallback string, not an `AttributeError` into the parent turn**; the child's tools draw from a **different** semaphore object than the parent's (assert identity) so no cross-pool contention; cancelling the awaiting parent task cancels the child (clean `CancelledError`, no orphan).
- `success_signal:` delegation runs in-turn with parent-attributed usage, bounded recursion, and no shared-sem contention.
- `prerequisites:` TASK-1.

### ✓ DONE TASK-3 — The `delegate` tool + registration + heuristic
- `files:` `co_cli/tools/system/delegate.py` (new), `co_cli/agent/toolset.py` (add the side-effect import so the tool self-registers — **non-conditional**), `co_cli/agent/_instructions.py` or the orchestrator instruction builder (the one-line when-to-delegate heuristic)
- `@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS, is_approval_required=False, is_concurrent_safe=False) async def delegate(ctx, task: str) -> str` calling `delegate_to_child(ctx.deps, task)`; the docstring carries the when-to-delegate cue. Add the heuristic to the orchestrator instructions.
- `done_when:` driving the **owned loop** (flag on) through a real turn, the orchestrator emits a `delegate` call (no `tool_view` reveal needed — ALWAYS visibility) and the run completes with the child's summary as exactly one `ToolReturnPart`; assert the **child's intermediate tool results are absent** from the parent message history (the context-isolation contract). Exercised via the owned-loop integration path, not grep.
- `success_signal:` `delegate` works end-to-end on the owned loop and isolates child context.
- `prerequisites:` TASK-2.

### ✓ DONE TASK-4 — Confirm child cannot re-delegate
- `files:` `co_cli/agent/delegation.py` (child surface excludes `delegate` — TASK-1 already), test
- Belt-and-suspenders alongside TASK-2's `agent_depth` guard: assert a child's visible tools exclude `delegate`.
- `done_when:` a behavior test asserts a child's visible tools exclude `delegate` AND a repo-wide grep confirms `delegate` is in no child `tool_names`; full test suite passes.
- `success_signal:` a child cannot re-delegate.
- `prerequisites:` TASK-3.

### ✓ DONE TASK-5 — Context-isolation flow test
- `files:` `tests/test_flow_delegation.py` (new)
- Owned-path flow test: parent delegates a read/search subtask; assert (a) the parent history contains the `delegate` summary `ToolReturnPart` but **none** of the child's intermediate tool results; (b) the child executed ≥1 read-mostly tool; (c) parent turn usage includes the child's tokens. No `tool_view` reveal needed (ALWAYS visibility).
- `done_when:` the flow test passes on the owned loop; full suite green; tail the LLM-call log during the run.
- `success_signal:` the context-isolation contract holds under a realistic delegation.
- `prerequisites:` TASK-3.

## Testing

Functional-only. The behavioral net is TASK-5's flow test (context isolation = the contract) plus TASK-2's driver test (usage rollup, depth guard, own-sem identity, cancellation). No structural assertions; assertions mirror `done_when`. All LLM calls hit `llm.host` from config with `noreason`/`reasoning` settings from the shared helpers — never a coined `ModelSettings`. Tail the spans/log every run; fail fast (`-x`). No eval is added (delegation has no existing seeded scenario; reverse-engineering one to trip the path is forbidden — a context-isolation eval is a separate scenario-authoring task if wanted).

## Decisions

| Issue | Decision | Rationale | Change |
|---|---|---|---|
| CD-M-1 | adopt | Verified: `@agent_tool` wrapper acquires `tool_dispatch_sem` per body (`co_cli/tools/agent_tool.py:66`); `delegate` holds a slot for the child's whole synchronous life; `fork_deps` shares the sem (`deps.py:437`), so the child starves/deadlocks behind parent-held slots. | High-Level Design + TASK-2: child gets its **own** sem via `fork_deps(..., share_dispatch_sem=False)`; `delegate` is `is_concurrent_safe=False`. Pre-existing double-acquire noted, not fixed (scope). |
| CD-M-2 | adopt (all 3) | (a) Usage rollup is **already correct** — `record_usage`→shared `usage_accumulator` (`co_cli/observability/usage.py:49`), ledger at `main.py:130`; fork shares it (`deps.py:457`). (b) settings are hardcoded `settings_noreason` (`loop.py:418`) → a param is required, not conditional. (c) `run_standalone_owned` sets `deps.toolset` itself (`loop.py:410`) → driver must NOT also set it. | TASK-2 rewritten: usage = assert-only; commit to `settings` param on `run_standalone_owned`; deleted the `child_deps.toolset` step. |
| CD-M-3 | adopt (via visibility change) | DEFERRED hides `delegate` until a `tool_view` reveal (`toolset.py:82-88`), making the original `done_when` unsatisfiable AND compounding under-firing. | Visibility set to **ALWAYS** (orchestrator-only) — resolves the reveal problem at the source; TASK-3/TASK-5 `done_when` no longer need a reveal step. |
| CD-m-1 | adopt | A new tool module self-registers only if imported in `toolset.py`'s side-effect block. | TASK-3 `files:` makes the `toolset.py` import **non-conditional**. |
| CD-m-2 | adopt (note) | `record_usage` sits outside `run_standalone_owned`'s try (`loop.py:493`) → on cancel, child partial tokens aren't rolled in. Telemetry-only (`co_cli/observability/usage.py`), acceptable. | TASK-2 cancellation `done_when` treats partial-usage-loss-on-cancel as acceptable (telemetry-only), not a fix. |
| CD-m-3 | adopt | Peers set a real budget (`_reviewer.py:83`); "a default_budget" was unspecified. | TASK-1 names `DELEGATE_CHILD_BUDGET` constant, value on the review-budget scale. |
| PO-M-1 | adopt | Delegation value is conditional on the orchestrator knowing *when* to delegate; a small model under-fires an unguided new tool (the recall under-firing lesson). | High-Level Design + TASK-3: a one-line when-to-delegate heuristic (tool description + orchestrator instruction), anchored on an observable cue. |
| PO-m-1 | adopt | "forced compaction / no graceful alternative" overstated; compaction works, just lossily and after the fact. | Problem & Outcome / Failure cost trimmed to "no second lossless lever; compaction degrades lossily, after the noise enters." |
| PO-m-2 | adopt | Depth cap = 1 is a cutover constraint, not a design ceiling (milestone framing). | Scope/Out: cap = 1 stated as the cutover value; nested delegation re-opens as scope. |
| G1-1 (Gate 1) | adopt | Source-verified: `run_standalone_owned` returns `None` when the child exhausts `default_budget` or hard-stops without a `final_result` call (`loop.py:434,488`). `delegate_to_child` returning `result.summary` would raise `AttributeError` into the orchestrator's turn — the child (small model, bounded budget) is the least reliable place to assume a clean structured return. | TASK-2: `None`-guard returns a fixed fallback string; new `done_when` covers the budget-exhausted child. Also corrected drifted file:line cites (`agent_tool.py`→`co_cli/tools/`, `record_usage`→`co_cli/observability/usage.py:49`, loop/deps offsets) across Context + Decisions, and the `settings: ... \| None = None` plumbing note. |

## Open Questions

None deferred. Resolved inline (codebase- or doctrine-grounded):
- **Child model settings → parent turn's settings** (foreground work; daemon path keeps `settings_noreason`). New `settings` param.
- **Depth cap → 1** (cutover value; tool absent from child surface + `agent_depth` backstop).
- **`delegate` visibility → ALWAYS** (override the DEFERRED episodic default — discoverability of a flagship capability outweighs one tool-def of prefill; the under-firing risk is the larger cost).
- **Child dispatch semaphore → own (not shared)** — the CD-M-1 fix.

---

## Final — Team Lead

Plan approved. Core Dev `Blocking: CD-M-1/2/3` and PO `Blocking: PO-M-1` were all **adopted** with concrete, source-grounded fixes (semaphore decoupling via own-sem fork; usage-rollup confirmed already-correct + settings param + toolset-step deleted; visibility ALWAYS resolving the DEFERRED-reveal break; when-to-delegate heuristic). No genuine disagreement remained, so the cycle converged on TL resolution without a C2 spin; every blocker maps to a specific task/section change above.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev loop-decoupling-phase2-5`

---

## Delivery Summary — 2026-06-25

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | child spec tools all resolve, none approval-required, `delegate` absent; spec drives a child end-to-end | ✓ pass |
| TASK-2 | child runs & rolls usage into parent; depth-cap refused without forking; `None`→fallback (no `AttributeError`); child draws a distinct sem; cancel propagates | ✓ pass |
| TASK-3 | owned loop emits `delegate`; one `ToolReturnPart` summary; child intermediate results absent from parent history | ✓ pass |
| TASK-4 | child surface excludes `delegate` + repo-wide grep confirms no child `tool_names` carries it | ✓ pass |
| TASK-5 | owned-path flow test: isolation holds, child ran ≥1 read tool, parent usage includes child tokens | ✓ pass |

**Tests:** scoped — `tests/test_flow_delegation.py` 7 passed (3 deterministic + 4 real-Ollama); regression set (`test_flow_fork_deps`, `test_flow_owned_subagent`, `test_flow_owned_turn`, `test_agent_build_task_agent`) 9 passed; floor guards (`test_orchestrator_schema_budget`, `test_instruction_budget`, `test_instruction_floor_coupling`) 4 passed. 0 failed.

**Implementation notes:**
- New: `co_cli/agent/delegation.py` (`DELEGATE_CHILD_SPEC`, `DelegationResult`, `delegate_to_child`, `DELEGATE_DEPTH_CAP=1`, `DELEGATE_CHILD_BUDGET=REVIEW_MAX_ITERATIONS`), `co_cli/tools/system/delegate.py` (`delegate` tool, ALWAYS visibility, `is_concurrent_safe=False`).
- Changed: `fork_deps(..., share_dispatch_sem=True)` — `False` gives the in-turn child its own `tool_dispatch_sem` (CD-M-1). `run_standalone_owned(..., settings=None)` — defaults to `settings_noreason` (daemon path byte-for-byte unchanged); `delegate_to_child` passes the parent turn's settings. `delegate` self-registers via a non-conditional import in `toolset.py`. When-to-delegate heuristic added as `DELEGATE_GUIDANCE` in `context/guidance.py` (gated on tool presence) + the tool docstring.
- Floor guard re-pinned consciously: `ALWAYS_BUCKET_CEILING` 20,100 → 21,100 (delegate adds 970 chars at ALWAYS — the reviewed discoverability trade, not DEFERRED).

**Doc Sync:** fixed — `agents.md` (in-turn delegation path in the architecture diagram, spec table, concrete-specs table, runners prose + pseudocode, Config, Public Interface, Files, Test Gates) and `core-loop.md` (isolated-contexts row). Also corrected **pre-existing drift** found while editing those sections (flagged, from Phase 1/2): `run_standalone`'s documented signature `(…, budget, model_settings) -> tuple` was stale (actual `(spec, deps, prompt) -> None`), and `TaskAgentSpec.error_message` was a phantom field (no such field in `spec.py`).

**None→fallback test scoping (deviation):** the no-mock policy precludes forcing `run_standalone_owned` to return `None` through `delegate_to_child` (its spec/budget are fixed). Instead, `test_run_standalone_owned_returns_none_when_budget_exhausted` pins the real `None` source deterministically (budget-1 child forced to call a non-final tool first); `delegate_to_child`'s one-line `None`-guard maps that to the fallback.

**Overall: DELIVERED**
All 5 tasks pass `done_when`; lint clean; scoped + regression + floor-guard tests green; docs synced (plus two adjacent pre-existing inaccuracies corrected).

---

## Implementation Review — 2026-06-25

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | child tools resolve, all non-approval, `delegate` absent; spec drives a child to a `DelegationResult` | ✓ pass | `delegation.py:39-77` (result/constants/spec); runtime check: all 11 tools resolve, all `is_approval_required=False`, `delegate` absent; `DELEGATE_CHILD_BUDGET=REVIEW_MAX_ITERATIONS` mirrors `_reviewer.py:83,98` |
| TASK-2 | child runs & rolls usage; depth-cap refused w/o fork; `None`→fallback; distinct sem; cancel propagates | ✓ pass | `delegation.py:80-103` (4 steps); `deps.py:405,442-446` (own-sem fork); `loop.py:393,420-421` (`settings` param); daemon path unchanged (`run.py:50` omits settings → `settings_noreason`); `deps.model.settings`=foreground reasoning settings (`factory.py:65`) |
| TASK-3 | owned loop emits `delegate`; one summary `ToolReturnPart`; child intermediates absent from parent history | ✓ pass | `delegate.py:16-38` (ALWAYS/no-approval/sequential); `toolset.py:45` non-conditional self-register; `guidance.py:35` heuristic via `orchestrator.py:48-51,84`; `dispatch.py:130-143` wraps bare-str → one `ToolReturnPart` |
| TASK-4 | child surface excludes `delegate` + repo-wide grep confirms no child `tool_names` carries it | ✓ pass | `delegation.py:45-57` (tuple omits `delegate`); regex sweep of all `tool_names=(...)` tuples in `co_cli/` → NONE contain `delegate` |
| TASK-5 | owned-path flow: isolation holds, child ran ≥1 read tool, parent usage includes child tokens | ✓ pass | `test_flow_delegation.py:222-263` — `delegate` return present, `file_read` return absent, exactly one delegate return, secret (`ZEBRA-7793`, file-only) in summary proves the hidden child ran `file_read` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Lazy imports (`run_standalone_owned`, `fork_deps`) not cycle-justified — `loop.py` loads at bootstrap regardless, so laziness bought nothing; violates the "confirm the cycle exists else it's a misplaced lazy import" convention | `delegation.py:91-92` | blocking (introduced convention violation) | Moved both to module-level imports; verified no cycle on all import orders (toolset→delegate→delegation→loop; cold; loop-first) and re-ran the 7 delegation tests green |
| `parent_deps.model.settings` evaluated before `run_standalone_owned`'s None-guard — latent `AttributeError` only on a contrived `model=None` direct call | `delegation.py:102` | minor | Left as-is — unreachable in production (the `delegate` tool only runs inside a live turn where `model` is already dereferenced at turn entry) |
| Bare-str NATIVE return skips the spill path on a pathologically large summary | `delegate.py:38` | minor | Left as-is — child is instructed toward a concise summary; `spill_largest_tool_results` history processor catches oversized results downstream |
| `DELEGATE_GUIDANCE` near-duplicates the tool docstring | `guidance.py:21-25` | minor | Left as-is — intentional two-consumer design (PO-M-1): docstring → per-call tool schema, guidance → standing system-prompt reflex; mirrors `CAPABILITIES_GUIDANCE` |
| `test_fork...decouples` asserts semaphore object identity (borderline structural) | `test_flow_delegation.py:91` | minor | Left as-is — guards a real `fork_deps(share_dispatch_sem=...)` flag regression (would fail if the flag were ignored); cheapest deterministic guard for the no-starvation wiring |

### Tests
- Command: `uv run pytest` (full suite)
- Result: **882 passed, 0 failed** in 301s
- Log: `.pytest-logs/20260625-165604-review-impl.log`
- Adversarial: 4 parallel per-task reviewers + cold cross-checks (import-cycle empirics, daemon-path-unchanged, stub-litmus on every new test) — no false-positive PASS, no missed FAIL.

### Behavioral Verification
- `uv run co --help`: ✓ boots (full import + bootstrap graph loads, zero LLM cost)
- Live tool surface: ✓ `delegate` registered ALWAYS / no-approval / `is_concurrent_safe=False`; `build_toolset_guidance` emits the when-to-delegate cue when `delegate` is present
- Owned-loop delegation + context isolation: ✓ verified via `test_owned_turn_delegate_isolates_child_transcript` (real Ollama) — orchestrator emits `delegate`, child's `file_read` result never enters parent history, only the summary does. Chat interaction non-gating (LLM-mediated, verified via the flow test).
- `success_signal` (all 5): ✓ verified — child reads/distills a one-field summary (TASK-1); in-turn run with parent-attributed usage, bounded recursion, distinct sem (TASK-2); end-to-end on owned loop with context isolation (TASK-3/5); child cannot re-delegate (TASK-4).

### Overall: PASS
All 5 tasks satisfy `done_when` with file:line evidence; one introduced convention violation (misplaced lazy import) auto-fixed and re-verified; full suite green; boot smoke + isolation flow test confirm user-visible behavior.
