# Refactor co's agentic loop control — end-to-end, aligned with the SDK's intended use

## Context

A loop-control design review surfaced 10 smells in how co drives and wraps the pydantic-ai agent loop. The root finding is a **tier mismatch**: co runs the orchestrator at the `agent.run_stream_events()` tier (the SDK drives the graph), but the two genuine pieces of loop control co needs — a model-progress stall timeout and a per-model-request tool-call cap with a consecutive-violation circuit breaker — both require **graph-node boundaries that `run_stream_events()` does not expose**. co reconstructs those boundaries by inferring them from the flat event stream, then patches the seams. The fragile proxy stack and private-internal couplings are downstream of the same "inject behavior the SDK gives no seam for" pressure.

This is the **end-to-end loop refactor**: the local fixes (Phase A) *and* the tier migration to `agent.iter()` (Phase C), with a de-risking spike between them (Phase B). It targets co's **current pinned SDK (`pydantic-ai==1.92.0`)** and changes no dependency version — the `agent.iter()` API and the `is_model_request_node`/`is_call_tools_node` guards are present and stable across v1 (verified in the installed 1.92: `agent.iter()` accepts `message_history`, `model_settings`, `usage`, `usage_limits`, `deferred_tool_results`, `output_type` — full kwarg parity with `run_stream_events`). Refactoring against a frozen SDK is deliberate: a behavior-preserving refactor's verification ("identical before/after") is only meaningful when the dependency's behavior is fixed.

**Source-grounded smell inventory** (every claim cites code read during the review):

*Tier mismatch (shared root cause):*
- **#1 stall timer reconstructs node boundaries** — `_StallTimer.note` (`orchestrate.py:308-318`) counts `FunctionToolCallEvent`/`FunctionToolResultEvent` pairs to infer "tool in flight vs model generating," relying on the documented invariant that no stream events flow between a tool call and its result. This is the `CallToolsNode` vs `ModelRequestNode` boundary, hand-rolled.
- **#6 tool-cap state split across four runtime fields + three-site reset** — `toolset.py:168-180` infers the model-request boundary from `ctx.run_step` deltas inside `call_tool` (`tool_call_limit_run_step`, `tool_calls_in_model_request`, `consecutive_tool_cap_violations`, `tool_cap_hard_stop`). The streak is reset in the toolset AND re-finalized at the run boundary in `_execute_run` (`orchestrate.py:493-497`) because the toolset structurally cannot see the run boundary — co's comment: "idempotent with the in-wrapper transition reset."
- **#7 `_execute_run` is dual-mode** — `_run_approval_loop` (`orchestrate.py:517-527`) re-enters the full `_execute_run` apparatus (stall timer, `asyncio.timeout`, renderer) for approval-resume runs that, per co's own docstring (`:510-513`), "skip `ModelRequestNode` entirely … zero tokens are sent." A model-progress guard wraps a run with no model request.

*Fragile proxy/wrapper stack:*
- **#2 `_RepairingStreamedResponse` is an untyped `__getattr__` proxy** (`surrogate_recovery_model.py:142-168`) over `StreamedResponse` (which has a `cancel()`/`close_stream()` lifecycle and several properties). Not a subclass → `isinstance` never holds; JSON-repair correctness rests on the undocumented fact that the graph validates from `.get()`.
- **#3 `_SanitizingMCPServer` is a second untyped `__getattr__` proxy** (`mcp.py:18-41`) whose key `.approval_required()` (`:127`) resolves through `__getattr__` to the *unwrapped* server; the design keeps a separate direct `server=` reference (`:80-84,151`) to dodge the resulting uncertainty — two paths to one server, one guaranteed-sanitized.

*Private-internal coupling:*
- **#4 three hard couplings to private `_agent_graph.py`** — a matched exception **string** `_REASONING_OVERFLOW_SIGNATURE` (`orchestrate.py:77-79`); repair depends on the private `_streaming_handler` reading from `.get()`; approval-resume depends on a private node skipping `ModelRequestNode`.

*Local smells:*
- **#5 write-only `metadata=` mirror** — `orchestrate.py:446-450` and `run.py:66-70` pass `{session_id, role, request_limit}` to the SDK; grep found zero readers in co. The same values already flow as `co.agent.*` span attributes (the actual read path). One-sided member.
- **#8 magic numbers** — `asyncio.sleep(0.5)` retry backoff (`orchestrate.py:882`), `range(50)` repair bound (`surrogate_recovery_model.py:109`), `[-8:]` session-id slice repeated in two files.
- **#9 inline model-shape normalization** — `_collect_deferred_tool_approvals` (`orchestrate.py:220-239`) normalizes `questions` metadata shape variance inside the orchestrator instead of at the tool/output boundary.
- **#10 dual usage source** — `_check_output_limits` reads last-response `input_tokens` (`orchestrate.py:689`) while the turn records cumulative `RunUsage`; both correct for purpose, but unannotated.

## Problem & Outcome

**Problem:** co's loop control is functionally sound but structurally strained — genuine controls (stall detection, tool cap) are implemented by faking node boundaries at a tier that doesn't expose them, requiring cross-module state finalization, untyped proxies, and private-API coupling that can break silently on any SDK change.

**Outcome:** the orchestrator runs at the SDK's *intended* fine-grained tier (`agent.iter()` + per-node `node.stream()`, `async for node` — NOT manual `run.next()` stepping, so the SDK still owns tool dispatch, end-strategy, and the graph state machine); the stall timer keys on real node transitions (deleting the event-pair counter); the tool cap is relocated to its node boundary (decided — not delete; the boundary-detector field `tool_call_limit_run_step` + the cross-module finalize go, the counter/streak/latch stay); the dual-mode resume path collapses; the two `__getattr__` proxies become SDK-supported seams; the private-string coupling is centralized. Behavior is **identical** before and after.

**Failure cost:** Left as-is, the loop-control complexity is a standing silent-breakage surface and accreting state/reset logic — the "clean boundaries erode faster than review can enforce" tension co tracks. If the refactor itself changes behavior (stall timeout fires differently, tool cap miscounts, approval pause regresses, JSON repair stops), it breaks the agent loop's safety rails **silently** — which is why every control task carries a behavioral verification, not just a green suite.

## Does co genuinely need fine-grained control? (the tier decision — justified, not assumed)

**Yes — the evidence is empirical, not hypothetical: co already exercises node-boundary control, just at the wrong tier.** Two load-bearing controls *require* knowing the `ModelRequestNode` ↔ `CallToolsNode` boundary, and co reconstructs that boundary today (#1 from event pairs, #6 from `run_step` deltas). The question was never "does co need node-level control" — the running code answers yes — but "should it keep faking the boundary from a delegated stream, or read it from the API that exposes it."

`agent.iter()` is the SDK's *intended* answer and is strictly more aligned: `async for node in run` keeps the SDK executing nodes (dispatch order, end-strategy, graph state machine stay the SDK's job), while `Agent.is_model_request_node(node)` + `async with node.stream(run.ctx)` gives co both the node boundary *and* the streaming events it renders today.

Both controls need the boundary. The **stall timer (#1)** firmly needs it. The **per-request tool cap (#6)** is now **decided: relocate, not delete** — settled, not deferred. The earlier "maybe redundant with `UsageLimits(request_limit)`" framing is closed on three grounded points: (a) the two are **orthogonal axes** — the cap bounds tool-calls *within one model response* (`MAX_TOOL_CALLS_PER_MODEL_REQUEST=3`, parallel-flood), `request_limit` bounds *responses per turn* (sequential-loop), so neither substitutes for the other; (b) the **peer survey** (Audit Log) confirms no top agent guards parallel-flood via request-limits — it's a co-specific small-local-model defense; (c) deleting the cap **removes a control rail that `request_limit` provably does not replicate — i.e. a behavior change — which this plan's own zero-behavior-change constraint forbids.** Phase C therefore relocates the cap to its node boundary unconditionally; there is no delete branch.

## Scope

**In:** the full loop-control refactor on `1.92.0` — Phase A (six tier-agnostic fixes), Phase B (the `iter()` behavioral-validation spike), Phase C (the `iter()` migration: relocate the stall timer and the tool cap to node boundaries, collapse the dual-mode resume).

**Out:**
- The pydantic-ai v2.0.0 upgrade — a **separate, independent plan** (`…-migrate-pydantic-ai-v2.md`). Recommended to run *after* this refactor (refactoring on a frozen SDK is safer), but the two plans share no task dependencies.
- Any SDK version bump (the loop API is version-invariant within v1).
- Behavior change of any kind — every control rail must be preserved exactly.
- Spec edits — handled by `sync-doc` post-delivery.
- Manual `run.next()` node stepping — explicitly NOT the target (would force co to own dispatch/end-strategy; that is "against the SDK").

## Behavioral Constraints

- **Zero behavior change.** This is a pure internal refactor. These must be behaviorally identical before/after, verified by real runs:
  - The model-progress stall timeout fires on a genuinely-stalled model but NOT during a long legitimate tool call.
  - The per-model-request tool-call cap + consecutive-violation hard stop trigger at the same thresholds on the same inputs (the cap is relocated to the node boundary, never deleted — see "Does co genuinely need fine-grained control?").
  - The deferred-tool approval pause/resume produces identical prompts and resumes without re-sending tokens.
  - JSON repair still fires on malformed tool-arg JSON.
  - MCP discovery returns sanitized, prefixed, approval-gated tools.
  - Reasoning-overflow recovery still triggers.
  - Streaming UI (thinking/text/tool surfaces) renders identically from the demultiplexed two-node event feed.
- If the `iter()` form cannot reproduce any of these exactly, that is a TASK-B1 no-go for Phase C — surface it; Phase A still ships.
- **One benign, deliberate timing shift is permitted under C1:** the stall window's *disarm* moves from "first `FunctionToolCallEvent`" (mid-stream, today) to "`CallToolsNode` entry" (after the model-request node completes). The deadline is still disabled throughout tool execution, so the observable rail ("fires on stall, not on long tools") is preserved — this is the node-boundary keying, not a regression. It is not byte-identical event timing, and that is acceptable.
- Touch only loop-control code. Do not refactor adjacent unrelated code.

## High-Level Design

Three phases, run in order. Phase A is independent local cleanup. Phase B is a spike that de-risks Phase C and decides the tool-cap question. Phase C is the tier migration.

**Target loop shape (Phase C)** — the SDK-intended pattern, corrected against the installed 1.92 source for how events actually flow:
```
async with agent.iter(input, deps=…, message_history=…, model_settings=…,
                      usage=…, usage_limits=UsageLimits(request_limit=…),
                      deferred_tool_results=…) as run:
    async for node in run:
        if Agent.is_model_request_node(node):
            # arm stall window; (if cap kept) reset per-request tool count
            async with node.stream(run.ctx) as request_stream:
                async for event in request_stream:   # PartStart/Delta/End, FinalResult ONLY
                    render_model_event(event)
        elif Agent.is_call_tools_node(node):
            # disarm stall window; tool dispatch stays the SDK's job
            async with node.stream(run.ctx) as tool_stream:
                async for event in tool_stream:       # FunctionToolCall/Result ONLY
                    render_tool_event(event)
    result = run.result   # AgentRunResultEvent is NOT emitted under iter(); read run.result
```
**Verified-fact corrections (installed 1.92):** `node.stream()` does NOT yield the flat event union `run_stream_events` does. `ModelRequestNode.stream()` yields only `PartStartEvent|PartDeltaEvent|PartEndEvent|FinalResultEvent`; `CallToolsNode.stream()` yields only `FunctionToolCallEvent|FunctionToolResultEvent`. The *union* equals today's event set, so the renderer CAN be fed equivalently, but co's single `_handle_stream_event` switch (`orchestrate.py:337-382`) must be **re-partitioned into two per-node handlers** — a restructure, not a context-manager swap. And `AgentRunResultEvent` is never emitted under `iter()` (only `run_stream_events` synthesizes it at `abstract.py:1259`), so the result-capture branch (`orchestrate.py:379-380`) and the `result is None → RuntimeError` contract (`:469-473`) must be rewired to `run.result`. co registers **no** pydantic-ai capabilities (verified: `build.py` constructs `Agent` with only `toolsets=`), so the bare `async for node` form emits no capability-bypass warning and is safe.

**Phase A details:** #2 — **settled: keep the proxy, do not subclass** (full reasoning in TASK-A2). Verified against the installed base: `usage()`→`self._usage` (`models/__init__.py:1149`), `get()`→`self._parts_manager.get_parts()` (`:1136`), and `_get_event_iterator` populates both from raw provider chunks (`models/openai.py:2756-2767`). co's wrapper only sees the inner's already-assembled events, so a subclass could populate its own state only via `self._parts_manager = inner._parts_manager` (the private coupling being removed) — infeasible cleanly. The proxy is correct and the `isinstance` failure is cosmetic (graph duck-types). A2 keeps the proxy and replaces bare `__getattr__` with an explicit `Protocol`/documented read surface. #3 — sanitize MCP schemas in a `WrapperToolset.get_tools` override (the seam `_SequentialMCPToolset` already uses, `mcp.py:53`); the **discovery path** (`_discover_one` → `entry.server.list_tools()`, `mcp.py:151`) does NOT go through `get_tools`, so A3 must re-source it (keep a thin direct handle — discovery reads only name/description/sequential, not the sanitized schema). #4 — **no typed exception exists in 1.92** (verified: bare `UnexpectedModelBehavior` raised in `CallToolsNode._run_stream`, `_agent_graph.py:1059-1061`), so the deliverable is to centralize the string match into one named, documented coupling point flagged for re-verification on SDK bump.

## Tasks

### ✓ DONE — TASK-A1 — Delete the write-only `metadata=` mirror
- `files:` `co_cli/agent/orchestrate.py`, `co_cli/agent/run.py`
- `done_when:` the `metadata={…}` kwarg is removed from both the `run_stream_events` call (`orchestrate.py:446-450`) and `run.py:66-70`; a repo-wide grep confirms nothing reads `ctx.metadata`/`run_context.metadata`/`AgentMetadata`; the `co.agent.*` span attributes still carry session/role/request_limit; and the full suite passes.
- `success_signal:` N/A (deletion of dead output).
- `prerequisites:` none

### ✓ DONE — TASK-A2 — Keep the repair proxy; add a typed contract additively over `__getattr__` (subclass ruled out)
- `decision (settled, verified against installed SDK):` **keep the delegating proxy — do NOT subclass `StreamedResponse`.** A no-private-coupling subclass is infeasible: a concrete `_get_event_iterator` populates `_usage`/`_parts_manager` by consuming **raw provider chunks** (`models/openai.py:2756-2767` does `self._usage += ...` and `yield from self._parts_manager.handle_*`), but co's wrapper only sees the inner stream's *already-assembled* events — the raw chunks are gone. So a subclass's own `_parts_manager`/`_usage` could be filled only by `self._parts_manager = inner._parts_manager` (private coupling — the exact smell being removed) or by re-implementing assembly. The proxy is correct precisely because it delegates ALL state to one consistent inner object, and `isinstance(x, StreamedResponse)` failing is **cosmetic** — the graph duck-types `get()`/`usage()`/`__aiter__` and the proxy works today (suite green ⇒ nothing rejects it).
- `files:` `co_cli/llm/surrogate_recovery_model.py`
- `done_when:` the proxy is retained and **`__getattr__` stays as the catch-all delegation** — it is NOT replaced by an enumerated member list (the SDK reads the streamed response beyond `get()`: `_build_agent_stream(ctx, sr, ...)` at `_agent_graph.py:633` plus instrumentation, so enumerate-and-drop would `AttributeError` on any un-listed access — a regression). The smell is addressed **additively**: declare the known-hot read surface (`get`/`usage`/`__aiter__`/`model_name`/`timestamp`/`close_stream`) and/or a `Protocol` for type-clarity, while `__getattr__` remains the fallback for everything else; no behavior changes (still `get()`→`_repair_response(inner.get())`, `usage()`→`inner.usage()`, iteration→inner); the duplicated span-close attribute dict is extracted — `_close_model_span` (`:171-190`) and `request()`'s inline block (`:232-242`) build the identical 5-attribute dict from `(response, usage)`, so factor one `_model_span_close_attributes(response, usage)` helper (DRY); `_repair_response` stays the one shared repair fn; a **behavioral run with malformed tool-call JSON confirms repair still fires** AND `_close_model_span` reports correct token usage; and the full suite passes.
- `success_signal:` a malformed-tool-arg turn is repaired and proceeds with correct span usage, via a proxy whose read surface is now explicitly typed rather than a bare attribute bag.
- `prerequisites:` none

### ✓ DONE — TASK-A3 — Sanitize MCP schemas at the toolset seam; remove the server proxy
- `files:` `co_cli/agent/mcp.py`
- `done_when:` MCP schema sanitization happens in a `WrapperToolset.get_tools` override; `_SanitizingMCPServer` is removed (repo-wide grep returns zero); `.approval_required()` wraps the sanitized toolset; the discovery path is explicitly re-sourced (thin direct handle, stated in the change) and verified; **and — because sanitization moves from mutating the raw `inputSchema` BEFORE the SDK's `inputSchema → tool_def.parameters_json_schema` conversion to operating on the get_tools output AFTER conversion — a check confirms the runtime tool-def schema reaching the model is sanitized to the SAME result as today** (not merely that discovery succeeds; the sanitize call must target the equivalent content on the converted schema, or the conversion must be confirmed lossless for what `sanitize_mcp_schema` touches); and `discover_mcp_tools` against a real configured MCP server returns correctly-prefixed, approval-gated tools with the live toolset's schemas sanitized.
- `success_signal:` MCP tools are discovered and the live toolset's schemas are sanitized through one `get_tools` seam.
- `prerequisites:` none

### ✓ DONE — TASK-A4 — Centralize the reasoning-overflow string coupling
- `files:` `co_cli/agent/orchestrate.py`
- `done_when:` the `_REASONING_OVERFLOW_SIGNATURE` match is consolidated into one named, documented coupling point flagged for re-verification on SDK bump (verified: no typed exception in 1.92 — bare `exceptions.UnexpectedModelBehavior` with an interpolated f-string at `_agent_graph.py:1059-1060`; co's `"exceeded before any response was generated"` is the stable substring, correct to match); the stale in-source cite in the existing comment (`orchestrate.py:78` says `_agent_graph.py:1012`; the actual raise is `:1059-1060`) is corrected; and a behavioral or unit check exercises the overflow branch confirming it still routes to recovery.
- `success_signal:` reasoning-overflow recovery still triggers, with the private-string coupling isolated to one point.
- `prerequisites:` none

### ✓ DONE — TASK-A5 — Name magic numbers and annotate the dual usage source
- `files:` `co_cli/agent/orchestrate.py`, `co_cli/llm/surrogate_recovery_model.py`, `co_cli/agent/run.py`
- `done_when:` the retry backoff (`0.5`), JSON-repair trim bound (`50`), and session-id display width (`[-8:]`, dedup the repeat) are named constants with unit suffixes per `code-conventions.md`; `_check_output_limits`' last-response-vs-cumulative usage choice carries a one-line rationale; lint passes and the full suite passes.
- `success_signal:` N/A (clarity refactor).
- `prerequisites:` none

### ✓ DONE (reduced — Option A accepted) — TASK-A6 — Deferred-approval question metadata
> **Scope reduced by decision; original `done_when` ("no or-chain remains") deliberately waived.** Discovery: `output.metadata` is the SDK's untyped `dict[str, Any]`, and a unit test (`test_flow_approval_subject.py:152`) feeds it a partial dict (no `"multiple"`) directly, bypassing `QuestionRequired`. The consumer's defensive `.get()`/`or`-chain is *correct* for an untyped external-data boundary — stripping it regressed the test (`KeyError: 'multiple'`). **Maintainer decision (Option A): accept the reduction** — clarity-by-subtraction means deleting dead code, not input validation at an untyped boundary. Landed: deleted write-only `self.questions` (`approvals.py`); consumer defensiveness retained intentionally. Closed — no further work.
- `files:` `co_cli/tools/approvals.py` (the boundary — verified), `co_cli/agent/orchestrate.py` (the consumer)
- `done_when:` the model-emitted shape variance is collapsed to one typed structure **at `QuestionRequired.__init__` (`co_cli/tools/approvals.py:28-30`)** — the verified boundary where the `questions: list[dict]` metadata enters co's typed world (`super().__init__(metadata={"questions": questions})`); the question/label/text/message and options dict-or-str variance is normalized there (not in the loop), so `_collect_deferred_tool_approvals` (`orchestrate.py:220-239`) consumes a single shape without the `q.get("question") or q.get("label") or q.get("text") or q.get("message")` chain (`:232-233`) or the options `o["label"] if isinstance(o, dict) else o` branch (`:224-230`); a repo-wide grep confirms no remaining `q.get("question") or q.get("label")`-style fallback in the orchestrator; and a real approval/clarify turn renders the prompt identically across the key variants.
- `success_signal:` a clarify/approval turn shows the same prompt, with model-shape variance collapsed at the tool boundary.
- `prerequisites:` none

### ✓ DONE — TASK-B1 — Spike: behaviorally validate the (fully-specified) `iter()` shape (Phase C go/no-go) → **GO**
- `note:` all **design** decisions are settled (loop shape, `run.result`, two-node handlers, stall `arm()`/`disarm()`, tool-cap = relocate). B1 is **not** a decision step — it is the empirical safety gate that confirms the settled shape reproduces today's behavior before C1–C3 commit. The SDK mechanics are pre-verified: `agent.iter()` + `is_model_request_node`/`is_call_tools_node` exist; `CallToolsNode.stream` yields `FunctionToolCallEvent`/`FunctionToolResultEvent` (`_agent_graph.py:1409,1416`) — `BuiltinTool*` events exist (`:1141-1143`) but cannot fire for co (no builtin tools registered); `ModelRequestNode.stream` yields the model-event stream co renders today; `iter()` accepts `deferred_tool_results`. The only thing a green API check cannot prove is byte-identical *rendering/timing*, which is exactly what B1 observes.
- `files:` (throwaway prototype branch — output is recorded findings)
- `done_when:` a prototype rewires `_execute_run`'s inner loop to `agent.iter()` with per-node-type streaming (per the verified target shape) and **records evidence** that: (1) the demultiplexed two-node event feed reproduces today's `StreamRenderer` UI exactly; (2) the `deferred_tool_results` approval-resume works through `iter()`; (3) the stall window armed on the model-request node / disarmed on the call-tools node reproduces stall behavior (fires on stall, not on long tools). A go/no-go is recorded in the delivery summary; no-go on any of (1)/(2)/(3) means Phase C is dropped and only Phase A ships.
- `success_signal:` recorded go/no-go confirming the settled shape reproduces today's behavior exactly.
- `prerequisites:` none (independent of Phase A)

### ✓ DONE — TASK-C1 — Migrate `_execute_run` to `agent.iter()`; relocate the stall timer to node boundaries
- `files:` `co_cli/agent/orchestrate.py`
- `done_when:` `_execute_run` drives the loop via `async with agent.iter(...) as run: async for node in run:` with per-node `node.stream(run.ctx)` and result from `run.result`; the renderer dispatch is re-partitioned into two per-node handlers that are **pure `-> None` rendering functions** (the result-capture concern leaves event handling entirely — they no longer return `SessionRunResult | None`, since the result now comes from `run.result`); the `_StallTimer` event-pair counter (`orchestrate.py:308-318`) is deleted and replaced by a **named `_StallTimer` seam** that **preserves today's "time since last model token" semantics — the deadline is RE-ARMED (`reschedule(now + LLM_RUN_TIMEOUT_SECS)`) on EVERY model-request-node event**, exactly as the current `note` re-arms on each non-tool event (`orchestrate.py:317-318`), and **disarmed (`reschedule(None)`) on entering a call-tools node** so a long legitimate tool call never trips it (re-armed on return to the next model-request node). **Arm-once-at-node-entry is explicitly WRONG** — a slow-but-progressing model (a token every < timeout seconds over a long total) would falsely trip it: that is a regression, not the current behavior. The disarm point shifting from "first `FunctionToolCallEvent`" to "call-tools-node entry" is the only timing change and is benign (no model generation occurs between the model node's last event and the node transition); the `result is None → RuntimeError` contract is rewired to `run.result` AND its error strings (`orchestrate.py:470,472`) are reworded off "ended without AgentRunResultEvent" to the `run.result`-was-None contract; the now-orphaned `AgentRunResultEvent` import (`:20`) is removed; a repo-wide grep for `run_stream_events` AND `AgentRunResultEvent` returns zero in the orchestrator; the full suite passes; AND behavioral runs confirm (a) a stalled model trips the timeout, (b) a long legitimate tool call does NOT, and (c) the streaming UI is unchanged.
- `success_signal:` the loop runs on `agent.iter()` with stall detection and rendering behaviorally identical.
- `prerequisites:` TASK-B1 (go)

### ✓ DONE — TASK-C2 — Relocate the tool cap's per-request reset to the model-request node boundary
- `files:` `co_cli/agent/toolset.py`, `co_cli/agent/orchestrate.py`, `co_cli/deps.py` (runtime state — `CoRuntimeState`, `:186-195`)
- `done_when:` **relocate (decided — no delete branch):** ONLY the boundary-detector field `tool_call_limit_run_step` (defined `deps.py:186`, reset `:254`, read `toolset.py:168-175`) and the cross-module finalize (`orchestrate.py:493-497`) are removed; the per-request count is reset at the **model-request node boundary** in `_execute_run` (replacing the `ctx.run_step != tool_call_limit_run_step` inference at `toolset.py:168-175`); the other three fields STAY (`tool_calls_in_model_request`, `consecutive_tool_cap_violations`, `tool_cap_hard_stop`, with their resets at `deps.py:252-255`), as do the toolset increment/streak/exceeded-payload (`toolset.py:176-180,198-201`) and the three `tool_cap_hard_stop` enforcement sites (`orchestrate.py:528` approval-loop break, `:573` turn-driver gate, `:584` latch comment); a repo-wide grep confirms no remaining `ctx.run_step`-based boundary reconstruction (`tool_call_limit_run_step` returns zero); the full suite passes; AND a behavioral run confirms the cap + consecutive-violation hard stop fire at the same thresholds as before (parallel-flood of >3 tool calls in one response is capped; N consecutive over-cap requests hard-stop).
- `success_signal:` runaway parallel-flood tool loops are bounded identically, with the per-request count now reset at the node boundary instead of inferred from `ctx.run_step`.
- `prerequisites:` TASK-B1 (go), TASK-C1

### ✓ DONE — TASK-C3 — Collapse the dual-mode approval-resume path
- `files:` `co_cli/agent/orchestrate.py`
- `done_when:` approval-resume runs through the same `iter()` loop with `deferred_tool_results=` (a resume that skips the model-request node simply yields no such node, so the stall window is never armed) and the special-case dual-mode handling (#7) is removed or reduced to one documented path; a repo-wide grep confirms no separate resume-only execution apparatus remains; the full suite passes; AND a real multi-approval turn pauses, resumes, and completes identically with no tokens re-sent on resume.
- `success_signal:` approval pause/resume behaves identically through one unified loop.
- `prerequisites:` TASK-B1 (go), TASK-C1

### ✓ DONE — TASK-D1 — Integration gate: full suite + stale-symbol sweep + behavioral verification of all rails
- `files:` (none — verification task)
- `done_when:` `scripts/quality-gate.sh full` passes (output teed to a timestamped `.pytest-logs/` file, tailed live); a repo-wide grep confirms zero stale symbols (`_SanitizingMCPServer`, `run_stream_events`, `AgentRunResultEvent`, `tool_call_limit_run_step`, the `metadata=` mirror) and the overflow string in exactly one coupling point; and every control rail is confirmed by real CLI turns — (1) stall timeout fires on stall but not on long tools, (2) parallel-flood tool loops bounded by the relocated per-request cap + hard stop, (3) approval pause/resume with no token re-send, (4) malformed-JSON repair, (5) MCP discovery sanitized/prefixed/approval-gated, (6) reasoning-overflow recovery, (7) streaming UI unchanged. Any red is RCA'd to root cause.
- `success_signal:` co's loop control runs on the simplified `iter()`-based design with every safety rail verified by observation.
- `prerequisites:` all adopted tasks

## Testing

The existing agent-loop and integration tests are the regression net. Every control rail (stall, tool cap, approval resume, JSON repair, MCP discovery, overflow recovery, rendering) is a **silent-failure surface** — a green suite does not prove them, so each carries a behavioral run at its task and again at TASK-D1. No new structural/fitness tests (co rejects them; `testing.md`). All pytest output tees to a timestamped `.pytest-logs/` file, tailed live to watch LLM call timing.

## Open Questions

None. The tool-cap question is **decided: relocate** (orthogonal axes + peer survey + zero-behavior-change constraint forbid delete — see "Does co genuinely need fine-grained control?" and the Audit Log peer comparison). The A2 subclass-vs-proxy question is **decided: keep the proxy** (no-private-coupling subclass verified infeasible). B1 remains as the empirical go/no-go on the settled shape, not a design decision. The v2 upgrade is out of scope (separate independent plan, recommended to run after this).

---
# Audit Log

## Decisions

Content was reviewed across two cycles in an earlier (Phase-A+B-only) form; both reviewers reached `Blocking: none` on C2. Key vetted decisions, carried forward, plus the two restructure decisions from this rewrite:

| Issue | Decision | Rationale | Where it landed |
|-------|----------|-----------|-----------------|
| Tier move justified? | yes | co already reconstructs node boundaries at the wrong tier — empirical, not aspirational. | "Does co need fine-grained control?" |
| Tool-cap: relocate or delete? | **relocate (decided)** | orthogonal to `UsageLimits` (parallel-flood vs sequential), no peer guards flood via request-limits, and delete = behavior change forbidden by the zero-change constraint. | TASK-C2 (relocate, no branch) |
| A2: subclass or keep proxy? | **keep proxy (decided)** | no-private-coupling `StreamedResponse` subclass verified infeasible (`_get_event_iterator` consumes raw chunks the inner already consumed); `isinstance` smell is cosmetic. | TASK-A2 |
| `node.stream()` event identity | corrected | verified: events split across two node types; `AgentRunResultEvent` not emitted under `iter()` → `run.result`. | High-Level Design target shape; TASK-C1 |
| StreamedResponse subclass | `_get_event_iterator` | verified: get()-only override leaves empty `_parts_manager`. | TASK-A2 |
| MCP discovery path | re-source | `_discover_one` bypasses `get_tools`. | TASK-A3 |
| Overflow signal | centralize string | verified: no typed exception in 1.92. | TASK-A4 |
| A6 producer | model-emitted | coerce at the tool output boundary, not in the loop. | TASK-A6 |
| End-to-end vs split | **end-to-end (this rewrite)** | per user direction: fold the iter() migration into one plan. The SDK shape is now verified, so the migration tasks are concrete (not sight-unseen); the spike (TASK-B1) is retained as the first migration step to de-risk. | Scope, Phase B/C tasks |
| v2 coupling | **fully decoupled** | per user direction: this plan and the v2 upgrade are completely independent. v2 references removed; order (refactor → upgrade) is an external recommendation, not a dependency. | Scope "Out" |

## Final — Team Lead

Plan approved (content vetted across two prior review cycles; restructured to end-to-end per user direction).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Note: ships the full loop refactor (Phase A fixes + Phase B spike + Phase C iter() migration) on the frozen 1.92 SDK. Independent of the v2 upgrade plan; run this first.
> Once approved, run: `/orchestrate-dev loop-control-refactor`

## Gate 1 — Sign-off (PO + TL): **APPROVED — proceed**

Every file:line citation re-verified against current source; SDK facts re-verified against installed 1.92. No drift since authoring (last loop-control commit `ba543fc2` predates the plan; SDK still pinned at 1.92.0). Both roles reached **Blocking: none**.

**Design confirmation (TL, source-grounded):** the tier move is best-practice-aligned by the SDK's own layering — `run_stream_events` → `run` → `iter` → graph nodes (`abstract.py`: `_run_stream_events:1179` spawns `self.run` and synthesizes `AgentRunResultEvent` at `:1259`; `run` is built on `async with self.iter(...)` at `:352`). co today consumes the top convenience wrapper that *flattens* the node boundaries it then re-infers; `iter()` is the underlying primitive that exposes them natively. The migration is also the *minimum* that reaches the root smell: the cross-module tool-cap state exists only because the toolset can't see the run boundary, and only the orchestrator-at-`iter()` can reset the per-request count cleanly. SDK guidance also blesses A3 ("extend `WrapperToolset` for cross-cutting toolset behavior"). Honest scoping: the simplicity win is *less hidden state* (concentrated in C2), not fewer loop lines — C1 trades implicit inference for explicit node structure.

**Carry into dev (non-blocking):**
1. **B1 tool-cap verdict — expect RELOCATE, not delete.** The per-request cap (`MAX_TOOL_CALLS_PER_MODEL_REQUEST = 3`, `config/tuning.py:104`) bounds tool-calls *within one model response* (parallel flood; over-cap returns the "pick the most important" payload at `toolset.py:198-201`). `UsageLimits(request_limit)` bounds *responses per turn* (sequential loop length). Orthogonal axes — a model emitting many calls in one response is `1` request to `UsageLimits` but tripped by the cap. B1's "delete as redundant" branch ("must demonstrably provide equivalent bounding") is therefore structurally unsatisfiable unless the feared runaway is provably sequential; treat RELOCATE as the expected outcome.
2. **TASK-C2 `files:`** should name `co_cli/deps.py` (`CoRuntimeState`, line 187) explicitly — that's where the four runtime fields live.
3. **TASK-A6** shape variance is wider than the shorthand: `orchestrate.py:232-233` is a 4-way `question/label/text/message` fallback *plus* options dict-or-str variance (`:224-230`). The boundary coercion must collapse both, not just the question-key chain.

> Approved at Gate 1 by PO + TL. Run: `/orchestrate-dev loop-control-refactor`

## Peer comparison — co's two needs vs. hermes / openclaw / opencode (code-first, file:line)

| Control | **co** | **hermes** | **openclaw** | **opencode** |
|---|---|---|---|---|
| NEED 1 — model-stall timeout (model-dead ≠ tool-slow) | ✅ stall window armed/disarmed on node boundary | ✅ **direct parity** — stale-stream timer, time-since-last-chunk, 180s scaling w/ ctx (`chat_completion_helpers.py:2539-2621`) | ❌ HTTP timeout + `AbortSignal` only; tools unbounded (`agent-loop.ts:477`) | ❌ none; only Effect-level user interrupt (`llm.ts:221`) |
| NEED 2a — per-response tool cap (parallel-flood, co's `=3`) | ✅ general cap, all tools | ⚠️ partial — only `delegate_task`→3 (`run_agent.py:3439`); general tools uncapped | ❌ `Promise.all`, no cap (`agent-loop.ts:725`) | ❌ unbounded `FiberSet` (`llm.ts:175,260`) |
| NEED 2b — sequential guard (requests/steps per turn) | ✅ `UsageLimits(request_limit)` (SDK) | ✅ iteration budget 90/50 (`iteration_budget.py:17`) | ❌ `while(true)`, no maxSteps (`agent-loop.ts:298`) | ✅ `agent.steps` (`llm.ts:193`) |
| NEED 2c — consecutive-violation breaker | ✅ N over-cap → hard stop | ✅ richer — failure/no-progress tiers (`tool_guardrails.py:63`) | ❌ none | ❌ none |
| Loop structure | **delegates to SDK** (pydantic-ai → `iter()`) | hand-written `while` (`conversation_loop.py:589`) | hand-written `while(true)` (`agent-loop.ts:298`) | hand-written nested `while` (`llm.ts:349`) |

**Parity verdicts:**
- **NEED 1** — co ≈ hermes (the local-model cohort both have a true model-stall detector; cloud-leaning openclaw/opencode have none). Best-practice-aligned. hermes's "scale window with context size" is a borrowable refinement, not a gap.
- **NEED 2a** — **co is the outlier**: no peer has a *general* parallel-flood cap (hermes guards only `delegate_task`). Confirms the cap is a **small-local-model defense** co needs and frontier-leaning peers don't.
- **NEED 2b/2c** — co sits with hermes; openclaw has no sequential cap at all. co's breaker is a co+hermes convergence.
- **Structural** — all 3 peers hand-write the loop and own dispatch; co alone delegates to an SDK. co's tier-mismatch is the tax for that delegation; `iter()` is co reclaiming the node visibility peers get for free. "Own the loop" is the peer-validated alternative *if* the pydantic-ai dependency itself is ever revisited (out of scope here).

**B1 verdict — re-sharpened by peer data (supersedes the G1 "expect RELOCATE" note's framing):**
- Strike the "**delete as redundant with `UsageLimits`**" option entirely — doubly dead: orthogonal axes (parallel-flood vs sequential) *and* no peer treats flood as a request-limit concern.
- This progression (G1 "expect relocate" → peer-data sharpening) is now **closed as a final decision: RELOCATE.** The deciding point is the plan's own **zero-behavior-change constraint** — deleting the cap removes a parallel-flood rail `request_limit` does not replicate, which *is* a behavior change and is therefore out of bounds regardless of whether the current configured model happens to flood. The cap is kept as the small-model rail and relocated to the node boundary (TASK-C2, no branch). B1 no longer carries a tool-cap verdict; it is purely the iter()-shape behavioral go/no-go.

## All impl decisions resolved (uncertainty-elimination pass) — verified against installed SDK + co source

This pass closed every deferred/forked decision so dev carries zero open design choices:

| Decision | Resolution | Verified by |
|---|---|---|
| A2 subclass vs proxy | **keep proxy** + typed `Protocol`/documented read surface; no subclass | `models/openai.py:2756-2767` — `_get_event_iterator` populates `_usage`/`_parts_manager` from raw provider chunks the inner stream already consumed ⇒ a no-private-coupling subclass is infeasible |
| A4 overflow signal | **string match, settled**; correct co's stale in-source cite (`orchestrate.py:78` → `:1059-1060`) | `_agent_graph.py:1059-1060` — bare `UnexpectedModelBehavior` f-string; no typed exception |
| A6 coercion boundary | **`QuestionRequired.__init__`** (`co_cli/tools/approvals.py:28-30`) — named, no grep | `approvals.py:20-30` — the verified entry point for the `questions` metadata |
| A3 discovery handle | **thin direct handle is safe** — discovery reads only `name`/`description` | `mcp.py:154,161` — `_discover_one` never touches `inputSchema` |
| Tool cap C2 | **relocate, no delete branch** | orthogonal axes + peer survey + zero-behavior-change constraint |
| C1 node.stream events | **confirmed** — `CallToolsNode.stream`→`FunctionTool*` (`BuiltinTool*` can't fire, no builtin tools); `ModelRequestNode.stream`→model events | `_agent_graph.py:1409,1416,1141-1143`; `build.py` registers `toolsets=` only |
| B1 role | **validation-only go/no-go** (all design settled); no verdicts | — |

Remaining run-and-observe items (B1 rendering/timing reproduction; the seven D1 behavioral rails) are **empirical safety verifications, not open design decisions** — they confirm the settled design behaves identically, which only running code can prove.

## Zero-regression pass (goal: no functional/logic regression; cleanup & enhancement welcome)

Scrutinized each change against source for hidden behavior change. Two genuine regression risks in task *wording* were found and fixed; the rest confirmed equivalent.

- **🔴 C1 stall timer — per-event re-arm preserved (fixed).** Today's `_StallTimer.note` re-arms the deadline on EVERY non-tool event (`orchestrate.py:317-318`) — the deadline measures "time since last model token." A node-boundary rewrite that arms *once* at node entry would falsely trip on a slow-but-progressing model = **regression**. C1 now mandates re-arm on every model-request-node event + disarm on call-tools-node entry. (The disarm-point shift is the only timing change; benign — see Behavioral Constraints.)
- **🔴 A2 proxy — `__getattr__` kept as catch-all (fixed).** The SDK reads the streamed response beyond `get()` (`_build_agent_stream(ctx, sr, …)` at `_agent_graph.py:633` + instrumentation). Replacing `__getattr__` with an enumerated member list would `AttributeError` on any un-listed access = **regression**. A2 now keeps `__getattr__` as the fallback and adds the typed contract *additively*.
- **🟠 A3 MCP sanitize — runtime-schema equivalence required (tightened).** Sanitization moves from mutating raw `inputSchema` *before* the SDK's `inputSchema→parameters_json_schema` conversion to operating *after* it (in `get_tools`). A3 now requires confirming the runtime tool-def schema reaching the model is sanitized to the same result — not just that discovery works.
- **🟢 A6 — must replicate exact precedence.** The `question→label→text→message` precedence and options dict-or-str coercion (`orchestrate.py:224-233`) must be reproduced verbatim at `QuestionRequired.__init__`; same input ⇒ same prompt. (Already in the task; reaffirmed as a zero-regression invariant.)
- **🟢 A1 metadata — confirmed safe.** The SDK merges `metadata` onto `RunContext` and exposes it to user code only (`agent/abstract.py:333-334`); it is NOT injected into the model request, and co has zero readers. Removing it drops an unused exposed value — no functional impact.
- **🟢 C1 result + ordering — confirmed equivalent.** `run.result` returns the same `AgentRunResult` that `AgentRunResultEvent` wrapped. Graph node order (model node fully completes → call-tools node) matches the flat stream's order, and "text after `FinalResultEvent`" is preserved as long as the model-node handler drains all its events. No reordering.
- **Net:** the only intended behavior *delta* is the stall timer's disarm micro-realignment (more precise, observably identical). Everything else is byte-for-byte functional parity; the wins are structural (less hidden state, supported seams, DRY).

## Impl-level conformance pass (clean / comprehensive / modular / DRY) — applied to tasks

Verified every cited site against current source; tightened the plan where it was not impl-comprehensive. Findings folded in:

- **F1 (A2 — most significant; fork later CLOSED → keep proxy, see "All impl decisions resolved"):** the "convert proxy → `StreamedResponse` subclass" premise was unsafe. Base source: `usage()`→`self._usage` (`models/__init__.py:1149`), `get()`→`self._parts_manager.get_parts()` (`:1136`), and `_get_event_iterator` must populate both as it goes (`:1127`). A *delegating* subclass leaves its own `_parts_manager`/`_usage` empty → silently breaks repair AND `_close_model_span:175` usage. The proxy is functionally correct; "isinstance never holds" is cosmetic (graph duck-types). First rewritten to a subclass-vs-proxy decision, then **closed: keep the proxy** (no-private-coupling subclass verified infeasible).
- **F5 (A2 — DRY):** `_close_model_span` (`:171-190`) and `request()` (`:232-242`) build the identical 5-attr span dict; A2 now extracts `_model_span_close_attributes(response, usage)`.
- **F2 (C2):** the three `tool_cap_hard_stop` *enforcement* sites (`orchestrate.py:528,573,584`) were unnamed; C2 now lists them per branch.
- **F3 (C2 + Outcome; delete branch later REMOVED):** "three of four fields" was the delete-branch count stated as general; corrected to branch-specific, then the delete branch was eliminated entirely — **C2 is relocate-only**, removing only `tool_call_limit_run_step` + finalize.
- **F4 (C1 + D1):** `AgentRunResultEvent` import (`:20`) orphaned after C1; now explicitly removed and added to the D1 sweep; the `:470,472` error strings reworded to the `run.result` contract.
- **F6 (C1):** `_StallTimer` retained as a named seam with explicit `arm()`/`disarm()` (not inlined `reschedule()`).
- **F7 (C1):** the two per-node handlers are now spec'd as pure `-> None` rendering fns (result-capture leaves event handling) — a modularity win made explicit.
- **F8 (Behavioral Constraints):** documented the one benign disarm-timing shift (first-tool-event → CallToolsNode entry) as permitted, not a regression.
- **Confirmed clean / correctly scoped:** `build.py` needs no change for `iter()`; `_repair_response` correctly shared; A1 covers both `metadata=` sites; `llm/call.py` direct path and `_message_sanitize` correctly out of scope.

## Gate 1 — FINAL sign-off (PO + TL): **PASS — proceed to dev**

Cleared after the full review arc: G1 review → peer comparison → v2-interaction check → impl-conformance pass → uncertainty-elimination pass → zero-regression pass. **Blocking: none.**

- **Right problem / correct scope** — confirmed; unchanged.
- **Source-accurate** — every file:line re-verified against current code; SDK facts against installed 1.92; no drift since authoring.
- **No open design decisions** — A2 (keep proxy), tool-cap (relocate), A3 (thin handle), A4 (string match), A6 (`QuestionRequired.__init__`), C1 event identity all settled against source. B1 reduced to an empirical go/no-go.
- **Zero functional/logic regression** — two traps fixed (C1 per-event stall re-arm; A2 `__getattr__` catch-all); A1/result/ordering/A3/A6 confirmed equivalent. Only behavior delta: the stall timer's benign disarm realignment (more precise, observably identical).
- **Impl-comprehensive** — every deletion's downstream consumers named; D1 sweep covers all stale symbols.

Left for dev by design (safety verification, not uncertainty): B1's empirical UI/timing reproduction (no-go ⇒ Phase A still ships) and the seven D1 behavioral rails.

Standing TL note (non-blocking): B1 could be folded into C1 given how thoroughly the SDK mechanics are now verified; kept standalone as cheap insurance against silent streaming-UI breakage. Maintainer's call.

> Approved at Gate 1 by PO + TL. Run: `/orchestrate-dev loop-control-refactor`

## Delivery Summary — Phase A (2026-06-24)

Run 1 of the agreed sequencing (Phase A now; B1 + Phase C after gate review). Assignment: TL took A1/A2/A4/A5/A6 (shared-file cluster — orchestrate.py/surrogate/run.py/approvals.py); Dev-1 took A3 (mcp.py, isolated).

| Task | done_when | Status |
|------|-----------|--------|
| A1 | `metadata=` removed from both call sites; nothing reads `ctx.metadata` | ✓ pass — both sites gone; verified session_id survives via `_SESSION_ID` contextvar (`observability/tracing.py:224`), instrumentation off, zero metadata readers |
| A2 | proxy kept, `__getattr__` retained as catch-all, span-attr dict DRYed, repair still fires | ✓ pass — documented read-surface contract added; `_model_span_close_attributes` helper extracted; subclass ruled out in docstring |
| A3 | sanitize at `WrapperToolset.get_tools`; `_SanitizingMCPServer` gone; runtime schema equivalent | ✓ pass (Dev-1) — folded into `_SequentialMCPToolset.get_tools`; proved `inputSchema→parameters_json_schema` verbatim (`pydantic_ai/mcp.py:667`) ⇒ identical sanitization; discovery re-sourced to raw handle |
| A4 | single match site; stale comment cite fixed | ✓ pass — constant already centralized (matched once, `:1046`); comment corrected `:1012`→`:1059-1060` |
| A5 | magic numbers named; dual-usage rationale annotated | ✓ pass — `_HTTP_400_REFLECT_BACKOFF_SECS`, `_JSON_REPAIR_MAX_TRIM_STEPS` named; `_check_output_limits` annotated. (`[-8:]` dedup MOOTED — both sites lived inside the A1-deleted metadata blocks) |
| A6 | no or-chain in orchestrator; variance collapsed at boundary | ✓ done (reduced — Option A) — deleted write-only `self.questions`; consumer defensiveness retained by decision (untyped SDK metadata boundary; stripping it regressed a unit test). Original "no or-chain" goal waived. |

**Tests:** scoped — 48 passed, 0 failed (`test_surrogate_recovery_model`, `test_flow_tool_call_repair`, `test_flow_observability_spans`, `test_flow_approval_subject`, `test_flow_orchestrate_reasoning_overflow`, `test_flow_orchestrate_reformulation`, `test_flow_mcp_schema`, `test_flow_mcp_timeout`). One regression caught mid-run (A6 `KeyError: 'multiple'`) and resolved by reverting the over-reach. Lint clean.
**Doc Sync:** none needed — all changes internal, behavior-preserving; no spec/schema/public-API touched.

**Overall: DELIVERED — all six Phase A tasks closed.**
Net −5 lines across 5 files, zero regression, 48 scoped tests green, lint clean. A6 resolved by maintainer Option A (accept the reduction — defensive consumer is correct at the untyped SDK metadata boundary; only the dead `self.questions` removed). Phase B1 + Phase C remain for Run 2 after you review B1's go/no-go.

## Implementation Review — Phase A (2026-06-24)

Reviewed: A1, A2, A3, A4, A5, A6. Stance: issues exist — PASS earned. Three cold-read reviewers (grouped by file) + adversarial recheck of the two SDK-coupling claims against installed source.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| A1 | `metadata=` removed both sites; no `ctx.metadata` reader; spans carry role/request_limit | ✓ pass | `orchestrate.py:436-452` + `run.py:61-66` have no `metadata=`; `AgentMetadata`/`ctx.metadata` grep zero; role+request_limit at `orchestrate.py:422,426`/`run.py:55,57` |
| A2 | proxy kept, `__getattr__` catch-all retained, span dict DRYed | ✓ pass | `surrogate:144` proxy; `:179-180` `__getattr__` fallback; `_model_span_close_attributes` `:183-191` used at both `:203` and `:245`. **SDK-coupling verified**: subclass correctly ruled out — `models/openai.py:2756-2767` fills `_usage`/`_parts_manager` from raw chunks; proxy `get()`/`usage()`/`__aiter__` match graph read at `_agent_graph.py:637` |
| A3 | sanitize at `get_tools`; `_SanitizingMCPServer` gone; runtime schema equivalent | ✓ pass | `mcp.py:36-56` `_SequentialMCPToolset.get_tools` sanitizes `parameters_json_schema`; proxy removed (source grep zero). **SDK-coupling verified**: `pydantic_ai/mcp.py:667` assigns `inputSchema` verbatim ⇒ post-conversion sanitize is lossless-equivalent |
| A4 | single match site; comment cite fixed | ✓ pass | matched once at `orchestrate.py:1055`; comment now `:1059-1060` (no `1012`); recovery routes `:1055-1063` |
| A5 | magic numbers named; usage rationale annotated | ✓ pass | `_HTTP_400_REFLECT_BACKOFF_SECS` `:75`, `_JSON_REPAIR_MAX_TRIM_STEPS` `surrogate:42`; `_check_output_limits` rationale `orchestrate.py:690-691` |
| A6 | (reduced — Option A) write-only `self.questions` deleted; consumer defensiveness retained | ✓ pass | `self.questions` removed (`.questions` reader grep zero); `QuestionRequired.__init__` metadata intact `approvals.py:29`; consumer byte-unchanged |

### Issues Found (non-blocking — none break a done_when)
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| **Pre-existing:** `set_session_context` exported (`__all__`) but never called in production → `_SESSION_ID` always `None` in spans (write-only orphan since v0.8.206) | `observability/tracing.py:88,364` | minor (pre-existing, out of scope) | **Recommend dedicated follow-up.** Not fixed here — wiring it into session bootstrap is an observability decision in a different subsystem; bleeding it into the loop-control branch violates surgical-changes. A1's verdict unaffected: the deleted metadata fed only (off) instrumentation, and spans already emitted `session_id=None`, so deleting it lost nothing observable. (Corrects A1's earlier "session survives via contextvar" note — the mechanism was misstated; the conclusion stands.) |
| A3 `done_when` says "`.approval_required()` wraps the sanitized toolset" but actual nesting is reverse (sanitize outermost, approval inner) | `mcp.py:120` / `core.py:46` | cosmetic | Behavior correct either way (orthogonal concerns); done_when wording noted, not a defect |
| Scope-creep: unrelated pre-existing edit in working tree | `docs/reference/RESEARCH-summarization-prompting-peer-survey.md` | hygiene | **Must be excluded from the refactor commit** — predates this branch |

### Tests
- Command: `uv run pytest` (full suite)
- Result: **403 passed, 0 failed**
- Log: `.pytest-logs/20260624-131338-review-impl.log`
- Note: `test_display.py::test_waiting_ticker_repaints_then_hands_off_to_thinking` (wall-clock timing) passed this run (`0.21s`); confirmed flaky-timing, not a regression.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, exit 0)
- LLM-mediated rails verified via green flow tests (chat non-gating): JSON repair (`test_flow_tool_call_repair`), MCP discovery+sanitize (`test_flow_mcp_schema`/`_timeout`), approval routing+resume (`test_flow_approval_subject`), reasoning-overflow recovery (`test_flow_orchestrate_reasoning_overflow`), span attributes (`test_flow_observability_spans`)
- `success_signal` spot-checks: A2 repair-fires ✓, A3 sanitized-discovery ✓ (live-server run not available — covered by tests + SDK-grounded equivalence), A4 overflow-recovery ✓, A6 identical-prompt ✓

### Overall: PASS
All six Phase A tasks earn PASS — zero-regression cleanups, full suite green, both SDK-coupling claims verified against installed source. Three non-blocking notes (one pre-existing observability orphan recommended as a follow-up, one cosmetic done_when wording, one staged-hygiene exclusion). Ready for Gate 2 on Phase A; Phase B1 + Phase C remain.

## B1 Spike — Recorded Go/No-Go (2026-06-24): **GO**

Isolated prototype (`tmp/b1_spike.py`, `tmp/b1_resume_baseline.py`, `tmp/b1_resume_node_order.py` — throwaway, no `co_cli/` changes) using real production code (`CoDeps`, `build_orchestrator(ORCHESTRATOR_SPEC)`, the real `clarify` tool) with a deterministic `FunctionModel` for exact event-feed comparison. 15 relevant production tests pass against the harness assumptions.

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| (1) UI/event reproduction | **GO** | Same scripted turn through `run_stream_events()` (13 events) vs `iter()`+per-node (12) — **byte-identical in type/order/payload** after stripping the one synthesized `AgentRunResultEvent` (`iter()` reads `run.result` instead). Tool events split across the two nodes; union = flat feed. No event arises that `_handle_stream_event` doesn't already handle. `run.result.output` matches. |
| (2) Approval-resume | **GO** | Deferred `clarify` → resume via `agent.iter(deferred_tool_results=…)`. Resume's first real node is `call-tools` (approved tool executing) → **zero tokens to re-decide**. Parity test: `run_stream_events` and `iter()` resume each issue the same 1 model request (the post-tool answer), same output. |
| (3) Stall window | **GO** | `_IterStallTimer`: re-arm `asyncio.timeout` on every model-request-node event, disarm on call-tools-node entry, re-arm after. Silent model → fires; long legit tool → does NOT fire. Same as the 3 production stall tests; tool boundary detected structurally (node type), not by event type. |

**Carry into C1:** always wrap BOTH model-request and call-tools nodes in `node.stream()` — bare node iteration uses the non-streamed `request` path (co always streams). Read the final result from `run.result`, never from a per-node event.

**Gate decision:** GO recorded; Phase C (C1 → C2 → C3) may proceed on maintainer approval.

## Delivery Summary — Phase C (2026-06-24)

Run 2, executed after the B1 GO + maintainer approval. TL-sequential (all touch `orchestrate.py`); implementation grounded in the verified B1 prototype shape.

| Task | done_when | Status |
|------|-----------|--------|
| C1 | `_execute_run` on `agent.iter()` + per-node `node.stream`; renderer re-partitioned into two `-> None` handlers; stall timer re-keyed to node boundaries; result from `run.result`; `run_stream_events`/`AgentRunResultEvent` gone from the orchestrator | ✓ pass — `agent.iter()` loop with `Agent.is_model_request_node`/`is_call_tools_node`; `_handle_model_request_event` + `_handle_tool_event`; `_StallTimer` now `arm()`/`disarm()` (re-arm per model event, disarm on call-tools node, re-arm after); `result = run.result`; orphaned import + error strings fixed |
| C2 | per-request count reset at the model-request node boundary; `tool_call_limit_run_step` + the `_execute_run` finalize deleted; toolset keeps increment/streak/latch + the 3 enforcement sites; no `ctx.run_step` reconstruction | ✓ pass — reset inlined at the model-request node branch; `tool_call_limit_run_step` field + reset deleted from `deps.py`; run-end finalize removed; toolset `call_tool` counts + latches only; `tool_call_limit_run_step`/`run_step` grep zero in toolset |
| C3 | approval-resume through the same `iter()` loop; no separate resume-only apparatus | ✓ pass — `_run_approval_loop` calls the one unified `_execute_run`; resume's first node is call-tools (zero re-decide tokens) so the stall window is never armed for an absent model request; docstring corrected to the `iter()` node reality |

**Tests:** scoped — 60 passed, 0 failed (stall_timeout, model_request_cap, approval_subject, tool_call_functional, tool_call_repair, turn_result_model_requests, usage_tracking, observability_spans, reasoning_overflow, reformulation). Decisive cap cases green: `test_under_cap_request_after_over_cap_does_not_hard_stop`, `test_hard_stop_surfaces_final_answer_after_consecutive_violations`, `test_hard_stop_survives_deferred_exit`. Lint clean. One regression caught mid-run (none — clean first pass).
**Doc Sync:** fixed — 4 specs (core-loop.md, pydantic-ai-integration.md, compaction.md, tools.md) updated off the old `run_stream_events`/`ctx.run_step`/`AgentRunResultEvent`/`metadata=` mechanics to the `agent.iter()` + node-boundary model. No spec files added/removed; system.md index unchanged.

**Overall: DELIVERED — Phase C complete (C1, C2, C3).**
The loop now runs on `agent.iter()` with the stall timer and tool cap owned at real node boundaries, the dual-mode resume collapsed, and behavior preserved (only the documented benign stall-disarm realignment). Full A+C set ready for `/review-impl`.

## Implementation Review — full A+C set (2026-06-24) — completes TASK-D1

Reviewed all `✓ DONE` tasks (A1–A6, B1, C1–C3). Two cold-read reviewers (orchestrate C1/C3 + SDK coupling; toolset/deps C2) + full suite + behavioral. A1–A6 carried their prior PASS and were re-confirmed intact after Phase C's further edits to the shared files. B1 = spike (recorded go/no-go, no production source).

### ✓ DONE — TASK-D1 — Integration gate
Satisfied by this review: full suite green, repo-wide stale-symbol sweep zero, all 7 control rails confirmed.

### Evidence
| Task | Spec Fidelity | Key Evidence |
|------|---------------|--------------|
| C1 | ✓ pass | `orchestrate.py` `agent.iter()` loop + per-node `node.stream(run.ctx)`; `_handle_model_request_event`/`_handle_tool_event` (pure `-> None`); `_StallTimer.arm()`/`disarm()` node-keyed; `result = run.result`. **SDK coupling verified vs installed dep**: `is_model_request_node`/`is_call_tools_node` (`agent/abstract.py:1543,1553`); node.stream event unions (`_agent_graph.py:584,1029-1031`); `AgentRunResultEvent` emitted only in `run_stream_events`, never under `iter()` (`abstract.py:1112,1259`) |
| C2 | ✓ pass | per-request reset inlined at the model-request node branch; `tool_call_limit_run_step` deleted + run-end finalize gone; toolset counts+latches only; all 3 surviving cap fields two-sided; 3 `tool_cap_hard_stop` enforcement sites intact; behavioral equivalence reasoned (last-request case harmless) |
| C3 | ✓ pass | `_run_approval_loop` → unified `_execute_run`; no separate resume apparatus; docstring matches iter()/node reality |
| A1–A6 | ✓ pass (re-confirmed) | metadata gone; overflow string single-site (`:1060`); named constants + usage rationale; A2 proxy + DRY helper intact; A6 defensive consumer (by design); A3 sanitize seam (prior review) |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale test verifies the removed toolset `ctx.run_step` reset (C2 relocated it to the orchestrator node boundary) | `test_flow_tool_call_limit.py::test_run_step_transition_resets_per_request_counter` | blocking (full-suite failure) | **Deleted** — stale-test exception (verifies removed mechanic); functional reset coverage lives end-to-end in `test_flow_model_request_cap.py`. Module docstring corrected. |
| Phantom `_run_model_preflight` reference in `_execute_run` docstring (pre-existing) | `orchestrate.py:390` | minor | Fixed — reworded to "the caller (`run_turn`)" |
| Pre-existing: `set_session_context` exported but never called → spans emit `session_id=None` | `observability/tracing.py:88,364` | minor (out of scope) | Recommended follow-up — not fixed in this branch |
| Staged-hygiene: unrelated pre-existing working-tree edit | `docs/reference/RESEARCH-summarization-prompting-peer-survey.md` | hygiene | Must be excluded from the commit |

### Tests
- Command: `uv run pytest` (full suite, foreground)
- Result: **847 passed, 0 failed** (0 skipped, 0 xfail)
- Log: `.pytest-logs/<ts>-review-impl-final.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph, exit 0)
- 7 control rails confirmed green in the suite (chat non-gating; LLM-mediated rails via flow tests): (1) stall timeout `test_flow_orchestrate_stall_timeout`, (2) parallel-flood cap + hard-stop `test_flow_model_request_cap`/`test_flow_tool_call_limit`, (3) approval pause/resume `test_flow_approval_subject`, (4) JSON repair `test_flow_tool_call_repair`/`test_surrogate_recovery_model`, (5) MCP discovery/sanitize `test_flow_mcp_schema`/`_timeout`, (6) reasoning-overflow recovery `test_flow_orchestrate_reasoning_overflow`, (7) streaming UI `test_flow_observability_spans`/`test_flow_tool_call_functional`

### Overall: PASS
All tasks (A1–A6, B1, C1–C3) pass; D1 integration gate complete. The loop runs on `agent.iter()` with both control rails owned at real node boundaries, zero functional/logic regression (one stale test removed; only the documented benign stall-disarm realignment as intended behavior delta). Ready for Gate 2 → ship. Exclude the `RESEARCH-summarization…` doc and clean the `tmp/b1_*.py` spikes at ship; `set_session_context` is a recommended follow-up.

## Delivery Addendum — tunable stall timeout (2026-06-24, ships with this branch)

A **separate, follow-on feature** was implemented on this branch after the review above, riding it because it builds directly on the refactor's `_StallTimer` (which exists only here). It is a deliberate **behavior-ADD** — the one exception to this plan's "zero behavior change" core — and ships in the same delivery.

**What:** the model-generation stall window is now operator-tunable via `llm.run_stall_timeout_secs` (default `120`, env `CO_LLM_RUN_STALL_TIMEOUT_SECS`), split from the now-renamed `SUMMARIZE_CALL_TIMEOUT_SECS` (the `/compact` ceiling). Peer-aligned (hermes/openclaw make their model-wait timeout tunable; co was the lone hardcoder) and fits co's local-model latency variance. Minimal: one flat config field, not hermes's provider/model-class matrix.

**Files:** `config/llm.py`, `config/mcp.py`, `context/timeouts.py`, `context/summarization.py`, `agent/orchestrate.py` (`_StallTimer` window injection + `_execute_run` config read), `tests/test_flow_orchestrate_stall_timeout.py` (config-override path replaces the monkeypatch); specs `config.md`/`core-loop.md`/`pydantic-ai-integration.md`.

**Gate 2:** closed by a focused cold-read review (scoped to this delta; the loop refactor passed full `/review-impl` separately). Default (120) preserves prior behavior exactly; full suite green at **847 passed**.

**Changelog must state both:** loop-control refactor (zero behavior change) **+** tunable stall timeout (new config knob, behavior-add).
