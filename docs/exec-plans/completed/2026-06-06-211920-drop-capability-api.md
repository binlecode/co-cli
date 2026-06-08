# Drop pydantic-ai capability SDK coupling

## Context

co attaches two pydantic-ai capabilities to every agent (`co_cli/agent/build.py:54` and `:111`):

- **`ObservabilityCapability`** (`co_cli/observability/capability.py`) — opens/closes tracing spans on the run/model/tool lifecycle and calls `record_usage()` on `after_model_request`.
- **`CoToolLifecycle`** (`co_cli/tools/lifecycle.py`) — six cross-cutting behaviors via loop hooks: tool-call dedup (`before_node_run`), per-turn tool-call cap (`wrap_tool_execute` + `after_node_run`), JSON arg repair (`before_tool_validate`), path normalization (`before_tool_execute`), MCP result spill + span enrichment (`after_tool_execute`).

The capability API is pydantic-ai's general loop-spanning middleware. A necessity audit found JSON arg repair is the only behavior unreachable by a `WrapperToolset` or history-processor seam (it must touch the raw args string *before* `validate_json` at `tool_manager.py:228`, which is downstream of the model response and inside tool-call validation where a toolset wrapper only sees the already-validated dict). It IS reachable two ways: at the `before_tool_validate` capability hook (current), or on the `ModelResponse`'s `ToolCallPart.args` via a `WrapperModel` (streaming-aware) — so relocating it off the capability is possible. The other five behaviors are reachable via `WrapperModel`, `WrapperToolset`, or per-tool logic. A peer survey (hermes = plain inline functions in its own loop; openclaw = explicit stream/`execute` wrappers; opencode = inline dispatch + AI-SDK `repairToolCall`) confirms co is the lone user of a capability-style lifecycle abstraction. The two capabilities also carry an inter-capability LIFO ordering invariant enforced only by a comment (`build.py:50-53`), with silent failure (`_NoOpSpan`) if reordered.

Verified seams co already owns, onto which the behaviors relocate:
- `SurrogateRecoveryModel(WrapperModel)` is in the agent path for both providers (`co_cli/llm/factory.py:56,69`). Its `request_stream` carries `run_context` (the agent loop streams via `agent.run_stream_events`, `orchestrate.py:362`).
- `UsageLimits(request_limit=...)` already bounds the loop (`agent/run.py:51`).
- `tool_output()` / `spill_with_span()` (`tool_io.py`) already spill native-tool results; only MCP results bypass it.
- `_SequentialMCPToolset(WrapperToolset)` (`agent/mcp.py:44`) already wraps the MCP toolset; `assemble_routing_toolset` (`agent/core.py:58`) composes native + MCP into a `CombinedToolset.filtered(...)`.
- `@trace("co.turn", new_trace=True)` (`orchestrate.py:694`) already opens the per-turn root span; usage is appended to the ledger at the turn boundary in `main.py:121` from `deps.usage_accumulator`.

Two dependencies discovered during current-state validation that reshape the naive "delete everything" framing:
1. **The per-turn cap is load-bearing.** `consecutive_tool_cap_violations` (incremented in `CoToolLifecycle.after_node_run`) drives a hard-stop turn abort at `orchestrate.py:432` (`>= TOOL_CAP_HARD_STOP_CONSECUTIVE`). Deleting the cap silently removes the small-model runaway guard.
2. **Usage recording needs `deps`.** `record_usage(deps, usage)` writes `deps.usage_accumulator`. `WrapperModel.request()` (non-stream, used by subagent `agent.run`) has no `RunContext`. Recording at the **run-result boundary** (`result.usage()`) is path-agnostic and avoids this; `run_standalone` already computes `result.usage()` (`agent/run.py:59`), and the direct path already records at its own boundary (`llm/call.py:73`).

## Problem & Outcome

**Problem:** co's cross-cutting tool/model logic is routed through pydantic-ai's capability API — a general abstraction justified by exactly one of six behaviors, carrying a comment-only ordering invariant with silent-failure risk, and making co the outlier among peers. The breadth is not justified under co's "only SDK surface that is absolutely necessary" standard.

**Outcome:** All capability-API coupling removed. The six behaviors are reimplemented with explicit mechanics on seams co already owns (a `WrapperToolset` at the `call_tool` boundary, the existing `WrapperModel`, per-tool logic, and run-boundary usage recording) — or deleted where the agentic loop already covers them. Observability output (`co tail` / `co trace` span tree, token ledger) preserved at parity. `ObservabilityCapability`, `CoToolLifecycle`, and the `capabilities=[...]` attachment no longer exist.

**Failure cost:** If left as-is — no functional break today, but the comment-only LIFO invariant (`build.py:50`) silently drops `co.tool.*` span attributes onto a `_NoOpSpan` if the capability list is ever reordered, and the unjustified abstraction keeps accreting cross-cutting logic (the current six behaviors are the evidence). The coupling also pins co to pydantic-ai's capability internals across SDK upgrades.

## Scope

**In:**
- Remove `capabilities=[...]` from both agent builders (`agent/build.py`).
- Delete `ObservabilityCapability`, `CoToolLifecycle`, and (conditionally) `tool_call_limit.py`, `PATH_NORMALIZATION_TOOLS`.
- Introduce one explicit `WrapperToolset` at the routing `call_tool` boundary hosting: tool span + `co.tool.*` attributes, per-turn cap (if ported), MCP result spill.
- Move model/chat span + (optionally) per-request observability into `SurrogateRecoveryModel`.
- Relocate `serialize_messages` / `serialize_response` (still imported by `llm/call.py`) to a non-capability module.
- Move usage recording to run-result boundaries.
- Move path normalization into `file_write` / `file_patch` tool bodies.
- Update/rewrite affected tests.

**Out:**
- Compaction, history processors, summarization (untouched).
- The direct-call path `llm/call.py` (already capability-free; only consumes the relocated serialize helpers).
- Spec doc edits (handled by `sync-doc` post-delivery; no `docs/specs/` in any task `files:`).
- Any change to the spill thresholds, cap value, or tracing record schema (behavioral parity only).

## Behavioral Constraints

- **Zero backward-compat** (per project rule): no aliases, no compat shims, no legacy-format readers. Renames are hard and immediate.
- **Observability parity:** the span tree emitted to the spans log must remain readable by `co tail` / `co trace` and the evals that parse spans. Span *kinds* `agent` / `model` / `tool` (names `invoke_agent <name>` / `chat <model>` / `tool <name>`) and key attributes (`co.tool.name`, `co.tool.source`, `co.tool.requires_approval`, `co.tool.result_size`, `co.model.tokens.*`, `co.model.input/output`) preserved.
- **Token ledger parity:** per-turn `usage.jsonl` totals unchanged for an equivalent turn. CRITICAL: `RunUsage` is *cumulative within a turn* — the orchestrator passes prior usage into each `run_stream_events` segment and updates `turn_state.latest_usage = result.usage()` (`orchestrate.py:369,400`), so a multi-segment turn (approval-resume) carries earlier segments' tokens forward. Recording each segment's `result.usage()` would double-count. The replacement must record each run's *final cumulative* usage exactly once (orchestrator turn-end from the final `latest_usage`; `run_standalone` once; `llm/call.py` unchanged), with forked subagent/summarizer tokens still rolling into the shared `deps.usage_accumulator` via their own once-per-run boundary.
- **Small-model runaway guard:** if the cap is dropped rather than ported, the `orchestrate.py` hard-stop consumer must be removed coherently and the regression acknowledged; if ported, hard-stop behavior is preserved bit-for-bit.
- **`current_span()` contract:** tools that call `current_span().add_event(...)` (e.g. `spill_with_span`) must still find an active tool span during execution — the routing wrapper must push the tool span before `super().call_tool(...)` and pop after.

## High-Level Design

**One new explicit wrapper + reuse of two existing seams + targeted deletions.** No new abstraction layer; the wrapper is a plain `WrapperToolset` whose `call_tool` body is linear, ordered code (no LIFO puzzle, no global-span bridge between components).

1. **Routing tool-call wrapper** (new `WrapperToolset`, applied in `assemble_routing_toolset` over the `CombinedToolset`). `call_tool(name, tool_args, ctx, tool)` body, in order: push `tool <name>` span → cap check (per-call rejection if this run-step's count exceeds the cap, returning the exceeded payload while still under the span) → `await super().call_tool(...)` → if result is an MCP string over threshold, `spill_with_span(...)` → set `co.tool.*` attributes → pop span. `ctx.run_step` and `ctx.deps` are available here (confirmed). This single seam replaces `ObservabilityCapability`'s tool hooks, `CoToolLifecycle`'s cap hooks, and the MCP-spill branch.
   - **Not a renamed `CoToolLifecycle`:** the capability's defect was bundling six *unrelated* concerns behind a comment-only LIFO ordering invariant with silent `_NoOpSpan` failure on reorder. This wrapper co-locates only the three concerns that *must* live at the `call_tool` boundary (you cannot span, cap, or spill a tool call anywhere else), as straight-line ordered code with no cross-component global-span bridge and no reorderable invariant. Cohesion at a single natural seam, not the old grab-bag.
   - **Cap counter granularity (per model request, not per call):** today `consecutive_tool_cap_violations` transitions once per `CallToolsNode` (`lifecycle.py:205-235`), after all tools in one model response run. A `WrapperToolset.call_tool` fires per *individual* tool call, so a model request = one `ctx.run_step` (all tools of one assistant message share it). The port keeps per-request semantics with **immediate increment, delayed reset**:
     - **Increment is immediate** — fire `consecutive_tool_cap_violations += 1` exactly once, at the `(cap+1)`-th call within a run_step (`tool_calls_in_model_request == MAX_TOOL_CALLS_PER_MODEL_REQUEST + 1`). It must NOT be deferred to the next request: if the 3rd consecutive over-cap request is the last in a segment, a deferred increment would never fire before the `:432` check and the hard-stop would be MISSED.
     - **Reset is on the next request** — when `ctx.run_step` changes and the prior request stayed `<= cap`, set the streak to 0.
     - **Equivalence:** immediate-increment + delayed-reset yields the identical hard-stop *decision* to the old per-node logic — the only divergence (counter reads transiently high after a non-exceeding final request) is always below threshold, because reaching the threshold fires the hard-stop the instant the 3rd increment happens.
     - **Exact counter parity (recommended):** add a one-line finalize at the segment boundary in `orchestrate.py` (co-located with the TASK-6 usage record): after `_execute_stream_segment` returns and before the `:432` check, `if tool_calls_in_model_request <= cap: consecutive_tool_cap_violations = 0` — idempotent with the in-wrapper transition reset, closes the last-non-exceeding-request gap.
     - This makes the `orchestrate.py:432` hard-stop (`>= TOOL_CAP_HARD_STOP_CONSECUTIVE`) fire on the same boundary as today; `reset_for_turn()` (`deps.py:239`) still zeroes the streak per turn.

2. **Model/chat span** → `SurrogateRecoveryModel`. Must cover BOTH paths: streaming `request_stream` (has `run_context`, used by the orchestrator at `orchestrate.py:362`) and non-stream `request` (no `run_context`, used by subagents via `agent.run` in `run_standalone`). Push a `chat <model>` span with `co.model.input` on entry; set `co.model.output` / `co.model.tokens.*` on close. On the streaming path the final response/usage is only available after the stream is consumed, so the span must close on context-manager exit with attributes populated from the assembled response — the discrete risk to validate. `serialize_messages` / `serialize_response` move to a shared module (proposed: `co_cli/observability/serialize.py`) since both this wrapper and `llm/call.py` consume them. The span uses the contextvar stack only — it needs no `deps`, so the no-`run_context` `request()` path is fine for spans.

3. **Usage recording** → run-result boundary, ONCE per run (see Token-ledger-parity constraint — per-segment recording double-counts because `RunUsage` is cumulative). Record the orchestrator turn's *final* cumulative usage once, pinned to the existing **`finally` block at `orchestrate.py:810`** — the sole point that catches all return paths (cap hard-stop `:741`, HTTP/API/malformed errors `:794/:799/:804`, interrupted `:808`, success `:762`) and which already reads `turn_state.latest_usage` for the `turn.*` span attributes. A happy-path-only placement would drop tokens for every error/interrupted/cap-stopped turn — a regression from today's per-request accumulation. Also record `run_standalone`'s `result.usage()` once (`agent/run.py:59`). The summarizer records once via the unchanged `llm/call.py:73` direct path; daemon subagents record once via `run_standalone` into the daemon-origin ledger — no in-turn agent shares the live turn accumulator, so the orchestrator turn's `latest_usage` is the sole session-origin contributor per turn. Remove the per-request `record_usage` from the capability. `main.py` turn-boundary `append_turn` + reset is unchanged.

4. **Agent/`invoke_agent` span** → emitted around the `agent.run` / stream call sites (a small push/pop or `@trace`), or folded under the existing `co.turn` root for the main loop with an explicit subagent span in `run_standalone`. Resolved in TASK-6.

5. **Path normalization** → into `file_write` and `file_patch` bodies via a shared `resolve_workspace_path(deps, path)` helper (peer-standard). `file_read` already self-resolves (multi-root); unchanged. `PATH_NORMALIZATION_TOOLS` deleted.

6. **JSON arg repair (row 1)** and **dedup (row 3)** → deleted; the agentic loop's `ModelRetry` feedback and duplicate-tolerance cover them. (Repair is the Open Question below.)

## Tasks

- ✓ DONE **TASK-1 — Relocate JSON arg repair into `SurrogateRecoveryModel`, gated (row 1; OQ-1 resolved)**
  - files: `co_cli/tools/lifecycle.py` (remove `before_tool_validate` + `_repair_json_args` + bracket/JSON helpers), `co_cli/llm/surrogate_recovery_model.py`, `co_cli/llm/factory.py` (pass a gate flag for the Ollama path), `tests/test_flow_tool_call_repair.py`
  - done_when: the syntactic-repair logic (`strict=False` reparse, trailing-comma strip, bracket balance, control-char escape) is removed from the capability and applied inside `SurrogateRecoveryModel` to each `ToolCallPart.args` on the returned `ModelResponse` BEFORE pydantic validation, on the Ollama-backed model only (Gemini path unchanged); a flow test where the Ollama model emits a trailing-comma/unclosed-bracket tool call resolves the turn WITHOUT a `ModelRetry` round-trip (repair succeeds first pass), and the Gemini path is unaffected.
  - success_signal: a quantized-model trailing-comma/unclosed-bracket tool call completes the turn with no retry.

- ✓ DONE **TASK-2 — Port the per-turn tool-call cap to the routing wrapper (row 2)**
  - files: `co_cli/agent/toolset.py`, `co_cli/agent/core.py`, `co_cli/tools/tool_call_limit.py`, `co_cli/context/orchestrate.py`
  - prerequisites: TASK-6 wrapper scaffold (shared)
  - done_when: the routing `WrapperToolset.call_tool` enforces `MAX_TOOL_CALLS_PER_MODEL_REQUEST` per run-step (per-call rejection payload past the cap), increments `consecutive_tool_cap_violations` IMMEDIATELY at the `(cap+1)`-th call of a run_step (once per request, never deferred), resets the streak on `ctx.run_step` change when the prior request behaved, and finalizes the last request's reset at the `orchestrate.py` segment boundary (one-line idempotent finalize before the `:432` check); a flow test issuing >cap tool calls across `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive *model requests* triggers the hard-stop INCLUDING when the 3rd over-cap request is the last in a segment, and a single over-cap request followed by an under-cap request does NOT (counter resets) — proving per-request granularity and immediate increment.
  - success_signal: a runaway small-model turn is hard-stopped on the same boundary as before.

- ✓ DONE **TASK-3 — Delete tool-call dedup (row 3)**
  - files: `co_cli/tools/lifecycle.py`
  - done_when: `_dedup_tool_call_parts` and the `before_node_run` dedup are removed; the suite passes with duplicate identical tool calls executing and the loop tolerating them.
  - success_signal: N/A (refactor).

- ✓ DONE **TASK-4 — Delete redundant path normalization (row 4)**
  - files: `co_cli/tools/categories.py`, `co_cli/tools/files/write.py` (verify only)
  - done_when: `PATH_NORMALIZATION_TOOLS` and the capability's `before_tool_execute` path rewrite are deleted; verified by grep that no consumer reads a pre-resolved `args["path"]` (working-set markers in `_tool_result_markers.py` key off `file_read`/`file_search`/`shell` only — confirm); a `file_write`/`file_patch` with a workspace-relative path still lands at the correct absolute location because `enforce_write_boundary` (`write.py:287,346`) already resolves relative→absolute as documented. No new helper/module is introduced (no second consumer exists).
  - success_signal: N/A (refactor — write behavior already correct without the capability; this proves removal is safe, not that a new path works).
  - note: the original capability normalization was redundant with `enforce_write_boundary`; this task confirms-and-deletes rather than relocates.

- ✓ DONE **TASK-5 — Fold MCP result spill into the routing wrapper (row 5)**
  - files: `co_cli/agent/toolset.py` (routing wrapper from TASK-6)
  - prerequisites: TASK-6
  - done_when: an MCP tool returning a string larger than its spill threshold is spilled to a tool-results file with a placeholder (parity with the prior `CoToolLifecycle.after_tool_execute` MCP branch), verified by a flow test with a stub oversized MCP result.
  - success_signal: oversized MCP output is persisted and replaced with a `<persisted-output>` placeholder.

- ✓ DONE **TASK-6 — Reimplement tracing/usage on owned seams (row 6)**
  - files: `co_cli/agent/toolset.py`, `co_cli/llm/surrogate_recovery_model.py`, `co_cli/observability/serialize.py` (new), `co_cli/agent/run.py`, `co_cli/context/orchestrate.py`, `co_cli/llm/call.py`
  - done_when: (a) `serialize_messages`/`serialize_response` live in `observability/serialize.py` and both `surrogate_recovery_model` and `llm/call.py` import them; (b) the routing wrapper emits the `tool` span with `co.tool.*` attributes; (c) `SurrogateRecoveryModel` emits the `chat` span with `co.model.*` attributes on BOTH the streaming (`request_stream`) and non-stream (`request`) paths — assert `co.model.tokens.input/output` are non-zero on a real *streamed* turn (the assembled-response risk), not only the non-stream path; (d) usage recorded once per run, pinned to the `orchestrate.py:810` `finally` block — assert (i) a 2-segment approval-resume turn produces a `usage.jsonl` line equal to the SUM of the two model requests' tokens (NOT double), proving no cumulative-`RunUsage` double-count, AND (ii) an error/interrupted/cap-stopped turn still appends a non-zero ledger line equal to `latest_usage`, proving return-path-agnostic recording (not happy-path only); (e) `co trace` on a real turn shows the `agent`/`model`/`tool` span tree at parity.
  - success_signal: a real `uv run co` turn produces an equivalent span tree and a correct (non-double-counted) ledger line.

- ✓ DONE **TASK-7 — Remove capability classes + attachment**
  - files: `co_cli/agent/build.py`, `co_cli/observability/capability.py` (delete), `co_cli/tools/lifecycle.py` (delete)
  - prerequisites: TASK-1..TASK-6
  - done_when: `capabilities=[...]` is gone from both builders; `observability/capability.py` and `tools/lifecycle.py` are deleted; no remaining import of `pydantic_ai.capabilities` anywhere in `co_cli/`; `rg "pydantic_ai.capabilities|AbstractCapability|ObservabilityCapability|CoToolLifecycle" co_cli/` returns nothing.
  - success_signal: N/A (refactor).

- ✓ DONE **TASK-8 — Update/clean tests**
  - files: `tests/test_observability_capability.py`, `tests/test_flow_tool_call_limit.py`, `tests/test_flow_tool_call_repair.py`, `tests/test_flow_model_request_cap.py`, `tests/test_flow_usage_tracking.py`, `tests/test_flow_spill.py`
  - prerequisites: TASK-1..TASK-7
  - done_when: each file is explicitly classified and handled — these import/instantiate the deleted classes directly and will break test collection otherwise: `test_observability_capability.py` (builds `Agent(..., capabilities=[ObservabilityCapability()])`), `test_flow_tool_call_limit.py` (instantiates `CoToolLifecycle()`, drives `wrap_tool_execute`/`after_node_run`), `test_flow_tool_call_repair.py` (calls `before_tool_validate`), `test_flow_model_request_cap.py` (`capabilities=[...]` inline), `test_flow_spill.py` + `test_flow_usage_tracking.py` (import and call hook methods) → **rewrite to assert behavior at the new seams** (wrapper `call_tool`, `SurrogateRecoveryModel`, run-boundary ledger) or **delete** if purely structural; NO residual `from co_cli.tools.lifecycle import` / `observability.capability import` left anywhere (grep clean); full suite green via `scripts/quality-gate.sh full`.
  - success_signal: cap (per-request granularity + hard-stop), MCP spill, repair disposition, usage (no double-count), and span-tree behaviors verified at the new seams.

## Testing

- Per-task flow tests at the new seams (cap hard-stop, MCP spill, path resolution, usage ledger, span tree), assertions at the integration boundary per `done_when`.
- `scripts/quality-gate.sh full` as the suite gate; run with `-x` fail-fast and tee to `.pytest-logs/` per project policy; tail the log to watch LLM-call timing.
- Behavioral parity checks for observability: capture a `co trace` tree before/after on an equivalent turn.
- `/clean-tests` pass on the touched test files (functional-only, purge structural capability assertions).

## Open Questions

- **OQ-1 — RESOLVED: relocate (keep repair), gated to the Ollama path.** Peer re-check (verified against source): hermes repairs always (`message_sanitization.py:185`), openclaw repairs gated to flaky providers (`shouldRepairMalformedToolCallArguments` — kimi/`openai-completions`/azure-responses, `attempt.tool-call-argument-repair.ts:301`), opencode does NOT repair (lowercase-or-`invalid` + retry). 2 of 3 repair; common practice does NOT favor removal — it favors *gated* repair for the model classes that emit malformed JSON, which is exactly co's `OllamaProvider`+`OpenAIChatModel` (OpenAI-compatible-endpoint) population. Decision: relocate `_repair_json_args` logic into `SurrogateRecoveryModel`, repairing `ToolCallPart.args` on the `ModelResponse` before validation; apply on the Ollama-backed model, not Gemini (openclaw-style gating; repair is idempotent on valid JSON so gating is cleanliness, not correctness). TASK-1 done_when updated to assert the relocated, gated repair.
- **OQ-2 — RESOLVED: port** (TASK-2). Live hard-stop consumer at `orchestrate.py:432`; no peer drops it. Detailed counter algorithm (immediate-increment / delayed-reset + segment-boundary finalize) specified in High-Level Design item 1.
- **OQ-3 — `invoke_agent` span home:** keep a discrete agent span (push/pop around `agent.run`/stream sites) vs. rely on the existing `co.turn` root for the main loop with an explicit subagent span only in `run_standalone`. Resolve in TASK-6; affects whether subagent spans nest correctly under `co trace`.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev drop-capability-api`

## Delivery Summary — 2026-06-06

Executed TL-solo serial (no Dev fan-out): every core task collides on the same file cluster (`agent/toolset.py` across TASK-6/2/5, `surrogate_recovery_model.py` across TASK-6/1, `lifecycle.py` across TASK-1/3/7) and the usage/span parity invariants demand single-author coherence.

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Repair relocated into `SurrogateRecoveryModel`, gated to Ollama (`repair_tool_args=True`); Gemini unaffected | ✓ pass |
| TASK-2 | Per-request cap in routing wrapper — immediate increment at (cap+1)-th call, delayed reset, segment-boundary finalize; hard-stop incl. last-in-segment; over→under does not stop | ✓ pass |
| TASK-3 | Tool-call dedup deleted (lifecycle.py removed) | ✓ pass |
| TASK-4 | `PATH_NORMALIZATION_TOOLS` deleted; `enforce_write_boundary` already resolves relative→absolute; no consumer reads a pre-resolved path | ✓ pass |
| TASK-5 | MCP oversized-string spill folded into `_CallSeamToolset.call_tool` | ✓ pass |
| TASK-6 | serialize.py created + consumed by call.py/model; tool span + co.tool.*; chat span on BOTH paths (streamed tokens asserted non-zero); usage once-per-run at finally (no double-count + error-path); span tree at parity | ✓ pass |
| TASK-7 | `capabilities=[...]` gone from both builders; capability.py + lifecycle.py deleted; `rg` for capability symbols returns nothing in `co_cli/` | ✓ pass |
| TASK-8 | All 6 listed test files rewritten/retargeted at the new seams; dedup test deleted; capability test renamed to span-tree test; grep-clean | ✓ pass |

**Resolved OQ-3:** discrete `invoke_agent <name>` agent span pushed at each run call site (`_execute_stream_segment` for the orchestrator, `run_standalone` for task agents) — preserves the per-segment nesting the capability emitted.

**Extra files (beyond task `files:`):** docstring fixes for grep-clean / dangling refs — `agent/spec.py`, `observability/tracing.py`, `tools/files/fs_guards.py`, `agent/build.py`; `build_task_agent` reworked to route task-agent tools through `_CallSeamToolset` (subagent span/cap/spill parity); deleted `tests/test_flow_tool_call_dedup.py`; renamed `tests/test_observability_capability.py` → `tests/test_flow_observability_spans.py`; updated two pre-existing `tests/test_surrogate_recovery_model.py` span-event tests (recovery event now lands on the chat span).

**Tests:** scoped + broad — 545 passed, 0 failed (`flow/observ/agent/surrogate/build/toolset/usage/spill/tracing/daemon`). Lint clean.
**Doc Sync:** full — fixed observability.md, tools.md, agents.md, compaction.md, sessions.md, dream.md, core-loop.md (removed capability machinery; relocated usage capture to run boundaries; cap mechanics).

**Overall: DELIVERED**
All eight tasks pass `done_when`; lint clean; scoped + broad tests green; doc sync complete. Span-tree, token-ledger, and hard-stop parity preserved.

**Next step:** `/review-impl drop-capability-api` — full suite + evidence scan + behavioral verification → verdict appended to plan.

## Implementation Review — 2026-06-07

Reviewed all 8 `✓ DONE` tasks. Stance: issues exist — PASS earned. Four parallel read-only evidence subagents (model/repair, toolset cap/spill, deletions/build, tests) + one adversarial subagent on the two correctness-critical claims (cap-streak equivalence, usage no-double-count).

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | Gated repair relocated to model, Gemini unaffected | ✓ pass | `surrogate_recovery_model.py:75-139` repair; `:229-230` applied pre-validation; `factory.py:56-62` Ollama `repair_tool_args=True`, `:70-72` Gemini default False; streaming via `_RepairingStreamedResponse.get()` `:142-168` |
| TASK-2 | Per-request cap in wrapper; immediate increment; finalize before unchanged hard-stop | ✓ pass | `toolset.py:166-176` (`==cap+1` increments once; delayed reset reads prior count before zeroing `:169`→`:173`); `orchestrate.py:438-439` finalize; `:470` hard-stop consumer unchanged (`>= TOOL_CAP_HARD_STOP_CONSECUTIVE`) |
| TASK-3 | Dedup deleted | ✓ pass | `grep _dedup_tool_call_parts\|before_node_run co_cli/` → empty; `lifecycle.py` absent |
| TASK-4 | Path-norm deleted; write still resolves rel→abs | ✓ pass | `PATH_NORMALIZATION_TOOLS` grep empty; `fs_guards.py:41` `(workspace_dir/path).resolve()`; `_tool_result_markers.py` keys exclude file_write/file_patch; `write.py` untouched (verify-only) |
| TASK-5 | MCP oversized-string spill in wrapper | ✓ pass | `toolset.py:201-212` gated `isinstance(str) and info.source==MCP` → `spill_with_span` |
| TASK-6 | serialize.py; tool+chat spans (streamed tokens non-zero); usage once-per-run; span tree at parity | ✓ pass | `serialize.py:30,72` imported by model+`call.py:11`; chat span both paths `:206-242`/`:253-291`; usage once at `orchestrate.py:857` (cumulative latest_usage), `run.py:78`, `call.py:73`; **live: `co trace` tree co.turn→invoke_agent→[chat×2,tool] with in=11776/11871** |
| TASK-7 | capabilities gone; files deleted; grep clean | ✓ pass | `grep capabilities= build.py` empty; `capability.py`+`lifecycle.py` absent; `rg pydantic_ai.capabilities\|AbstractCapability\|ObservabilityCapability\|CoToolLifecycle co_cli/` empty; `build_task_agent` routes via `_CallSeamToolset(FunctionToolset)` `build.py:100-110` |
| TASK-8 | tests retargeted at new seams; no residual imports; green | ✓ pass | residual-import grep empty; deleted-dedup + capability-test absent; observability-spans test present; all 7 files behavioral; 625-test suite green |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Pre-existing: `reset_for_turn()` zeroed `consecutive_tool_cap_violations` but not its sibling cap-state fields — a rare cross-turn `run_step` collision could wrongly reject a valid first tool call | `deps.py:239` | blocking (state-machine correctness; surfaced by adversarial review) | Completed the per-turn reset — added `tool_call_limit_run_step = -1` and `tool_calls_in_model_request = 0`. Cap tests + full suite green after fix. |

### Noted (pre-existing, NOT fixed — out of scope)
- **No-approval-turn hard-stop gap:** the runaway hard-stop is only raised inside `_run_approval_loop` (`orchestrate.py:470`), so 3 consecutive over-cap requests in a turn that never defers an approval are not hard-stopped. Confirmed by adversarial review to **predate this delivery** (shipped in `f7c77973`); the plan required the consumer be preserved *bit-for-bit*, which it is. Fixing would change hard-stop semantics and risk a behavioral regression → **recommend a separate follow-up plan**, not a fix here.
- `test_flow_model_request_cap.py::test_max_model_requests_default_is_90` — pre-existing structural constant-pin (`== 90`); the cap's behavior is covered by the integration tests. Low-tier cleanup candidate, unrelated to this change.

### Scope-creep extra files (all announced in delivery, none silent)
`agent/spec.py`, `observability/tracing.py`, `tools/files/fs_guards.py` (docstring-only fixes for grep-clean — verified no logic change); `tests/test_flow_observability_spans.py` (rename of declared `test_observability_capability.py`); `tests/test_surrogate_recovery_model.py` (2 span-event tests updated); `tests/test_flow_tool_call_dedup.py` (deleted — TASK-3); `docs/specs/*.md` (sync-doc, explicitly in plan Scope); `deps.py` (this review's fix).

### Tests
- Command: `uv run pytest -q -p no:randomly`
- Result: **625 passed, 0 failed**
- Log: `.pytest-logs/20260607-*-review-impl.log`

### Behavioral Verification
- `co chat` bootstrap (EOF): ✓ agent builds via `_CallSeamToolset` (Tools: 38, ✓ Ready, exit 0)
- Live completed turn (`file_search`): ✓ emitted `co.turn → invoke_agent → [chat×2, tool file_search]` to the spans log; `co.tool.source=native`; real streamed tokens `in=11776/11871 out=57/64`
- `co trace <id>`: ✓ renders the agent/model/tool tree at parity
- Token ledger: ✓ **one** `usage.jsonl` line `input_tokens=23647 output_tokens=121` = turn total, recorded once (no double-count) — live proof of TASK-6d
- `success_signal` checks: TASK-6 verified live (span tree + correct ledger); TASK-1/2/5 verified via integration tests through production code paths

### Overall: PASS
All 8 tasks confirmed with file:line evidence; one pre-existing cap-reset incompleteness found and fixed; full suite green; span-tree / token-ledger / hard-stop parity verified live via `co trace` and `usage.jsonl`. One pre-existing hard-stop limitation documented for a separate follow-up. Ready for Gate 2 → `/ship`.
