# Loop decoupling — own the agent turn, demote pydantic-ai to a provider+message library (`0.9.0` milestone)

## Context

co delegates the **main agent turn** to pydantic-ai's agent *graph* (`Agent.iter()`, `co_cli/agent/orchestrate.py:429`). That delegation is the root cause of an entire class of SDK couplings catalogued in the `sdk-coupling-cleanup` plan (S1–S6) and of the wrapper scaffolding co maintains to inject behavior at every boundary the graph exposes. co already runs **helper** calls graph-free via `pydantic_ai.direct.model_request` (`co_cli/llm/call.py`) — so the thin-boundary pattern is already proven in-tree.

A deep code-first survey of two peers that own their loop (`docs/reference/RESEARCH-loop-decoupling-peer-survey.md`) confirms the design and de-risks it:
- **hermes-agent** hand-writes its loop on the OpenAI SDK — a plain `while` over counters, terminate on absence of tool calls.
- **opencode** *migrated off* an SDK-owned loop (V1 `streamText`) onto an owned loop over a thin `stream(request)` provider boundary (V2) — the exact migration this plan proposes, executed by a peer.

**The boundary co will drive** (verified against installed pydantic-ai 1.92.0): `pydantic_ai.direct.model_request_stream(model, messages, model_request_parameters=ModelRequestParameters(function_tools=[…]))` does **one** model turn and yields a streamed response. co keeps pydantic-ai as a **provider + message library** (providers, `ModelMessage`/`*Part`, `ToolDefinition`, `ModelSettings`, `RequestUsage`, exceptions) and drops only the **graph** (`Agent`, `agent.iter()`, `output_type=[str, DeferredToolRequests]`). This is a strictly smaller lift than opencode's — co does **not** build a protocol library.

**This is a fundamental system update: version bumps `0.8.x → 0.9.0`.** It is behavior-preserving *in intent* (same observable agent behavior) but it rewrites the most safety-critical code co has, so it is phased behind a parallel path with the eval suite as the cutover gate — never a big-bang.

## Problem & Outcome

**Problem:** the graph owns co's control flow, so every co-specific behavior (JSON repair, surrogate recovery, the tool-call flood cap, spans, finish_reason handling, approval) is injected at a graph-exposed edge — as a `WrapperModel`/`WrapperToolset` subclass, a synthetic `RunContext`, or a string-match against an untyped exception. The control flow co's small-model thesis most depends on is the thing co least controls.

**Outcome:** co owns an explicit, linear turn loop. The loop builds a request, drives `model_request_stream`, renders deltas, dispatches tool calls, appends results, and repeats until the model emits no tool call. finish_reason is a typed branch; the flood cap counts at the natural loop boundary; approval is an inline prompt; the wrappers and synthetic contexts are gone, their behavior now straight-line loop code. pydantic-ai remains as the provider+message library.

**What this dissolves** (the `sdk-coupling-cleanup` census): **S1** (stream-repair coupling → inline repair on the assembled response co drives), **S2** (tool-cap diffuseness → cap at the loop boundary), **S3/S6** (synthetic `RunContext` → pass `deps`), **S4** (reasoning-overflow string-match → typed finish_reason branch). The `SurrogateRecoveryModel`, `_CallSeamToolset`, and `_RepairingStreamedResponse` wrappers are deleted.

**What it does NOT solve:** the `ModelMessage` type coupling (persistence/compaction/observability stay on pydantic-ai types). This is *loop* independence, not SDK independence — and that is the deliberate, bounded scope.

**Proportionality — why own-the-loop over pay-the-couplings (honest framing).** This milestone is behavior-preserving: **zero new user capability.** The S1–S6 census is *not* the justification — most of it is individually payable without this rewrite: S2 (tool-cap) and S6 (RunContext→deps) are cheap fixes already scoped in the on-hold `sdk-coupling-cleanup` plan; S1/S4 are wrapper/string-match annoyances, not walls; S3/S5 *survive this rewrite anyway* (S3 forced by `prepare_tool_def`, S5 a kept-library concern). So the rewrite dissolves ~4 of 6 couplings, two of which were already going to be paid cheaply — at the cost of rewriting approval + streaming + recovery + the turn contract. **Weighed against the cheaper alternative (A: ship `sdk-coupling-cleanup`, accept S1/S3/S4/S5; stop), the load-bearing case for B (this milestone) is the *structural* one, not the census:** under the graph, every co-specific control behavior is *injected at an edge the graph exposes* (a `WrapperModel`/`WrapperToolset` subclass, a synthetic `RunContext`, a string-match). co's distinctiveness is small-model orchestration control — typed finish_reason handling, the flood cap, the error taxonomy, approval shape — and the wrapper-injection model structurally caps how far that control can go (S4 is the concrete proof: co cannot get a typed reasoning-overflow signal because the graph raises before returning). The forward case is therefore **control + maintainability + insulation against the eventual pydantic-ai v2 migration** (this removes the graph half of that migration's surface), and the concrete near-term pull is co's own small-model-defense roadmap (richer loop-level interventions the graph resists). **This is a legitimate but not capability-pulled case** — stated honestly so Gate 1 can weigh it: if the team judges the control/insulation payoff insufficient *now*, the defensible alternative is to ship `sdk-coupling-cleanup` and **defer this milestone until a capability need pulls it**. The plan's recommendation is to proceed (the v2-migration surface reduction alone compounds over time and the rewrite only gets harder as more behavior accretes on the graph), but the proceed/defer call is explicitly Gate 1's.

**Failure cost:** high if rushed — this is approval, streaming, recovery, and the turn contract. Mitigated by: (a) parallel path (graph path stays default until the owned path passes the full eval suite), (b) the eval suite as a real behavioral net (`evals/` are UAT smoke runs on real scenarios), (c) phase gates.

## Scope

**In:** an owned turn loop driving `direct.model_request_stream` for **both** the orchestrator and task (subagent) agents; relocation of co's existing turn logic (streaming render, length-retry, overflow recovery, error handling, flood cap, JSON repair, surrogate recovery, spans, **history-processor invocation + `_clean_message_history` normalization**) into the loop as inline code; **co owns subagent output extraction/validation** (the graph's `output_type` validation, now loop-owned — see OQ-4); replacement of `DeferredToolRequests` suspend/resume with inline approval; deletion of the graph-coupled wrappers and synthetic contexts once cut over; `0.9.0` version bump.

**Out:**
- Replacing pydantic-ai's **message model** (`ModelMessage`/`*Part`) — kept as the wire + durable type. A message-model migration is a separate, larger question (and the v2-SDK plan's concern).
- Building a provider/protocol library (opencode-style) — co reuses `pydantic_ai.direct`.
- Changing memory/skills/tools/compaction *behavior* — only their call shape changes where they currently receive a `RunContext` (they take `deps`; see `sdk-coupling-cleanup` S6, which this milestone subsumes).
- The pydantic-ai 1.x→2.x migration — separate plan; this milestone *shrinks* its surface (the graph half) but not the message-type half.

## Behavioral Constraints

- **Observable behavior is preserved across the cutover.** Same streaming output, same tool dispatch order, same flood-cap semantics (shed >3/request, hard-stop after N consecutive over-cap requests), same length-retry/overflow recovery, same approval decisions, same error surfacing. The eval suite must pass on the owned path at parity with the graph path before cutover.
- **One mechanism change, confirmed safe (OQ-1 resolved → inline):** approval moves from suspend/resume (`DeferredToolRequests`) to an inline prompt inside tool dispatch. Verified to lose **no current functionality** — deferred's only distinctive capability (serializable cross-process suspension) is provably unused (`DeferredToolRequests` never serializes; `/resume` is history-replay; co runs deferred purely in-process within one `run_turn`); subagents never require approval (`build.py:98`, `requires_approval=False`); headless/no-frontend already auto-denies (`orchestrate.py:265`), never defers. Same *decisions* (subject resolution, auto-approval, remember-choice in `approvals.py` — reused verbatim). Inline additionally *simplifies* `clarify` (drops the `clarify_answers` stash + resume-validation workaround) and the transcript (no suspend/resume boundary, no `resume_tool_names`). Validated by the approval + bounded-autonomy evals.
- **The flood cap (=3 parallel, hard-stop after N consecutive) is kept** — it is a near-unique small-model defense (peer survey); do not adopt opencode's uncapped fan-out.
- **The JSON-repair ladder is kept** (co already has it; opencode dropped theirs and regressed — do not follow).
- **Parallel path until cutover:** the graph path remains the default and untouched until the owned path is proven; no phase before cutover may break it.

## High-Level Design

### The boundary

```
co owns:   build ModelRequestParameters(function_tools=[ToolDefinition…]) from the @agent_tool catalog
           build messages (ModelMessage list — unchanged type)
           async with model_request_stream(model, messages, params) as stream:
               async for event in stream:        # render text/thinking/tool-call deltas
                   ...
               response = stream.get()            # assembled ModelResponse — JSON repair lands HERE (inline)
           parts = response.parts
           tool_calls = [p for p in parts if isinstance(p, ToolCallPart)]
           if not tool_calls: final answer → done
           else: dispatch (cap + spill + approval inline) → append ToolReturnParts → loop
```

This replaces `Agent.iter()` + the node walk. `finish_reason` is read off `response` (typed); the empty/thinking-only + length case becomes an explicit branch co owns (no SDK raise to string-match).

### Turn state

Keep `_TurnState` (reconstructed each turn — never agent-object counters). Add a typed `turn_exit_reason` enum (hermes's `_turn_exit_reason` discipline: one value answering "why did the turn end"). Fold the flood-cap state into a `ToolCapState` object counted at the loop boundary (this is `sdk-coupling-cleanup` TASK-1, which this milestone absorbs).

### Error space (typed, borrowed from hermes `FailoverReason`)

Classify provider errors once into a typed reason + action-hint flags (`retryable`, `should_compress`); the loop reads flags. co's `_http_error_classifier.is_context_overflow` already produces a typed overflow signal — feed it the loop directly. **Transient/timeout → terminal, no inner retry (D1, opencode-shape):** co does not adopt hermes's inner retry+jittered-backoff loop (`conversation_loop.py:946`) — local-first / 1–2 backends make the rate-limit/rotation ladder inapplicable, and opencode likewise has none. Preserve the **fill-unanswered-tool_call_ids** invariant, at hermes's upgraded placement — a **pre-call safety net run every step** (`agent/agent_runtime_helpers.py:2057-2076`, called at `conversation_loop.py:828`), not a break-time-only fixup.

### Shared by orchestrator + task agents

`build_task_agent` (`co_cli/agent/build.py:58`) also constructs an `Agent`. The owned loop is the single driver for both — a subagent is the same loop with a narrower tool set and a different output expectation. Both cut over together.

## Phases

Each phase is independently shippable, keeps the graph path working, and ends at a gate. Detailed per-task `done_when` is authored at each phase's own `/orchestrate-plan` pass — this milestone plan defines the phase contracts.

### PHASE 0 — Decision gate (design already drafted)
- The design is already authored (`2026-06-24-234633-loop-decoupling-design.md`), so this phase is a **ratification gate, not a from-scratch authoring phase** (PO-m-2): confirm the resolved decisions (OQ-1 inline approval, OQ-2 `stream.get()`, OQ-3 keep `FunctionToolset` schema gen, OQ-5 sequencing, OQ-7 render+keep), lock the OQ-4 lean (output-tool path, A/B at Phase 2), and record the OQ-1 out-of-band-approval tripwire. No `docs/specs/` edit yet — specs update at Phase 6 (layer rule).
- `gate:` decisions ratified at Gate 1.

### PHASE 1 — Thin provider client (the boundary), graph path untouched
- A `co_cli/llm/` client: `async def model_turn(messages, tool_defs, settings, *, repair: bool) -> stream of typed co events` (text/thinking/tool-call deltas + assembled response), driving `direct.model_request_stream`, with surrogate-retry + JSON-repair + the `chat` span **inlined** (folding `SurrogateRecoveryModel`'s three concerns into the client — not a `WrapperModel`).
- `gate:` client unit-tested in isolation; helper-call path (`llm_call`) optionally rebased onto it; graph path still default.

### PHASE 2 — Owned loop behind a flag, parallel to the graph
- New owned `run_turn` driving the Phase-1 client: **run the history-processor chain** (compaction/dedup/evict/spill/elide — currently graph-hosted) with `deps`, **then `_clean_message_history` normalization** (CD-m-2) → build request from the `@agent_tool` catalog + assemble instructions/system prompt + per-turn dynamic instructions → stream → render → dispatch tool calls (flood cap + MCP spill inline, folding `_CallSeamToolset`) → append results → repeat until no tool calls. Typed finish_reason branch. `ToolCapState` at the loop boundary. Resolve OQ-6 (history-processor placement) and OQ-7 (mixed text+tool-call response) here.
- **Tool-cap is a behavior change, not pure relocation (CD-m-3):** today the `cap+1`-th call is shed *per-call inside dispatch* (`toolset.py:168-172`), order-of-execution dependent under `tool_dispatch_sem`. The owned loop counts at the step boundary *before* fan-out — pin the explicit rule "execute calls at index < `MAX_TOOL_CALLS_PER_MODEL_REQUEST`, return `make_exceeded_payload` for the rest" and validate against the eval exercising >3 parallel calls; do not assume byte-parity with the per-call path.
- Selectable via a spec/config flag; default stays graph.
- `gate:` owned path runs a real turn end-to-end (chat + one tool call + multi-step) under the flag; flow evals pass on the owned path; the history-processor chain + `_clean_message_history` fire at parity with the graph path.

### PHASE 3 — Inline approval (replace `DeferredToolRequests`)
- Approval prompt inside tool dispatch (subject resolution, auto-approval, remember-choice reused from `approvals.py`). Multiple approval-required calls in one response are **pre-prompted sequentially before the parallel fan-out** (avoids racing terminal prompts — see OQ-1 footnote). This pre-sequencing is **co-original — no peer precedent** (hermes blocks per-tool inside execution; opencode races questions uncapped); only the reject-halts-step half is a peer pattern (opencode `llm.ts:290-293`). Interrupt/deny = loop-halt. No deferred suspend/resume on the owned path. `clarify` prompts inline and returns its answer directly (drops the `clarify_answers` stash).
- `gate:` approval-discipline + bounded-autonomy evals pass on the owned path at parity; headless still auto-denies.

### PHASE 4 — Error/recovery/length-retry into the owned loop
- Relocate the orchestrate.py error handling: typed error classification (hermes `FailoverReason` shape), length-continuation retry, overflow strip-then-summarize, transient/timeout (→ terminal, **no inner retry** per D1), interrupt. Fill-unanswered-tool_ids invariant (run in **preflight every step**, not break-time only — see OQ-6). Stall timer.
- `gate:` context-stability, groundedness, length-retry, overflow, stall evals pass on the owned path.

### PHASE 5 — Cutover + delete the graph path
- Flip default to the owned loop. Run the **full eval suite** at parity. Then delete: `Agent`/`agent.iter()` usage, `output_type=[str, DeferredToolRequests]` (and the `Agent[CoDeps, str | DeferredToolRequests]` type aliases in **`main.py:18,182` and `commands/types.py:9,21`** — CD-m-5), `SurrogateRecoveryModel`, `_CallSeamToolset`, `_RepairingStreamedResponse`, the synthetic `RunContext`s (S3 `schema_budget` **persists** — forced by `prepare_tool_def`, confirmed by OQ-3), the S4 string-match, the now-dead `DeferredToolRequests/Results/ToolApproved` wiring, **plus the orphans the inline-approval change creates (CD-m-4): `deps.runtime.clarify_answers` + `resume_tool_names` fields (`deps.py:208,215`), and the `deferred_tool_awareness_prompt` per-turn instruction (`orchestrator.py:91`) — all dead once approval is inline.**
- **Eyes-open note (PO-m-3):** the `DeferredToolRequests` machinery deleted here is the cheapest existing scaffold for a future out-of-band/headless-with-sign-off approval (the OQ-1 tripwire). Deleting it means that future *rebuilds* rather than revives — accepted deliberately; the call stands on the verified evidence that the capability is unused today.
- `gate:` full eval suite green on owned path; `scripts/quality-gate.sh full` green; repo-wide grep shows **zero** stale references to the deleted symbols/fields (across `co_cli/` AND `tests/`); no graph imports remain except the kept library surface.

### PHASE 6 — Spec sync + `0.9.0`
- Update `docs/specs/core-loop.md`, `pydantic-ai-integration.md` (now "pydantic-ai as provider+message library"), prompt-assembly/personality references. Archive the `sdk-coupling-cleanup` plan as subsumed (or close S1/S2/S4/S6 as resolved-by-this).
- **Merge the layering rationale (design §2.1 "Why three layers, not two") into `core-loop.md`** — it is the canonical *why* behind co's two-level `turn ⊇ step` loop (run-layer = graph artifact, not intrinsic; mismatched-granularity argument). Carry the framing half into `pydantic-ai-integration.md`. The design subsection carries a `→ MERGE TO SPEC, Phase 6` marker; this is its destination.
- `0.9.0` version bump + CHANGELOG.

## Testing

The **eval suite is the cutover gate** — `evals/` are UAT smoke runs on real seeded scenarios (no mocks), so they are the behavioral net for "owned path == graph path." Per-phase, the relevant flow tests (`tests/test_flow_*`) and evals named in each phase gate. The parallel-path design means every phase before 5 is verifiable *against the still-live graph path* as the reference oracle. No structural tests (functional-only policy); the loop's correctness is proven by behavior parity, not by asserting its internal shape.

**Standing per-phase boundary invariant (G1-1).** The whole milestone keeps pydantic-ai as a **public** provider+message+schema library (the leverage doctrine: leverage public utility surface; own the loop/workflow; private-module reaches are the carve-out even when non-invasive). Inventory at Gate 1 confirmed co has **zero** `pydantic_ai._*` imports today. To keep it that way as the owned path accretes, **every phase's `done_when` carries a one-line review-grep guard**: `grep -rE 'from pydantic_ai\.[a-z_]*\._|from pydantic_ai\._' co_cli/` must stay empty (a documented parity-fallback such as G1-1's `_output` reach is the only allowed exception, and must carry its private-reach + v2-break-point note inline). This is a review check in the gate, not a structural fitness-test — it stays inside the functional-only testing rule. Ongoing whole-codebase hygiene (accreted underscore-leaks beyond this milestone) is owned by `/audit-conformance`, not a milestone phase.

## Open Questions

### Resolved
- **OQ-1 (approval mechanism) — RESOLVED → inline.** Verified safe with no current functionality loss (deferred's cross-process capability is unused; subagents never require approval per `build.py:98`; headless already auto-denies per `orchestrate.py:265`). See Behavioral Constraints. **Tripwire to record at Phase 0:** the *only* thing inline forecloses is *out-of-band* approval (propose tool → exit → human approves in a separate invocation) — a capability co does not have today (headless denies, not defers). If a future roadmap item wants asynchronous/headless-with-sign-off approval, that is a new feature to design, not a regression this change introduces.
- **OQ-2 (streaming assembly) — RESOLVED → use `stream.get()`.** Drive `model_request_stream`, iterate events for render, call `stream.get()` for the assembled `ModelResponse`; JSON repair lands on `get()` exactly as `_RepairingStreamedResponse` does today, now inline. Reuses SDK delta→part assembly; minimal new code.

### Resolved (this planning pass, source-grounded)
- **OQ-3 (tool-definition source) — RESOLVED → keep `FunctionToolset` as the schema generator.** co already generates tool schemas via `FunctionToolset.tools[name].prepare_tool_def` (`schema_budget.py:63`); re-deriving JSON schema from signatures would reimplement pydantic-ai's function-schema introspection. The owned loop reads `ToolDefinition`s from the existing native `FunctionToolset` (built by `build_native_toolset`) for `ModelRequestParameters.function_tools`, and uses it **only** as a schema source — never for dispatch (dispatch is co's `dispatch_tools`). Consequence: `schema_budget.py:62`'s synthetic `RunContext` (S3) **persists** — it is genuinely forced (`prepare_tool_def` needs a `RunContext`), confirming S3's `sdk-coupling-cleanup` no-action verdict.

### Open — settle at the named phase
- **OQ-4 (subagent structured output) — Phase 2 (narrowed by source; lean inverted per CD-m-1).** `TaskAgentSpec.output_type` is `type[BaseModel]` (`spec.py:51`) — subagents *always* return a structured model (e.g. `SessionReviewOutput`), never `str`, so the owned loop **must** handle structured output for the subagent path (not optional). **Source-verified parity fact:** for a single non-`str` `output_type`, the SDK builds an `OutputToolset` with `allow_text_output=False` (`pydantic_ai/_output.py:613,651-658`) — the model is *steered to emit the result as a `final_result` tool call*, validated against the schema; it is **not** prompted to emit free-text JSON. The dream-reviewer model (`agent/run.py:61`) was tuned against that tool path. So the two mechanisms are: **(b)** register the output model as an output tool via `ModelRequestParameters.output_tools` and detect its call — *parity-preserving* (same prompt contract the subagent model sees today); **(a)** parse the final text as the `BaseModel` + re-prompt on failure — simpler plumbing but *changes the prompt contract*. Lean: **(b)** as the default (preserves the tuned contract); fall back to (a) only if (b)'s plumbing proves heavy. **Peer-confirmed (2026-06-25 re-sync):** opencode's `generateObject` is exactly option (b) — forced `toolChoice.named(...)` + schema-decode + error-if-not-called (`packages/llm/src/llm.ts:116-129`); hermes has no structured-output enforcement. The precedent removes remaining doubt on (b). Phase-2 task must A/B both against the dream-reviewer evals before choosing. This makes "**co owns subagent output validation/extraction**" an explicit kept-loop responsibility (see Scope).
- **OQ-5 (sequencing vs `sdk-coupling-cleanup`) — RESOLVED → loop decoupling goes first.** The `sdk-coupling-cleanup` plan is deprioritized and put on hold: its actionable items (S2 tool-cap, S6 RunContext→deps) are *absorbed* by this milestone, so shipping it first would touch the same code twice. It will be **re-reviewed and rescoped after** loop decoupling lands — by then S2/S6 are gone and only the forced couplings (S1/S3/S4/S5, several also dissolved here) remain to reassess. S2/S6 are therefore folded into Phases 2/5 of this plan, not run separately.
- **OQ-6 (history-processor relocation) — Phase 2, NEW.** The compaction/dedup/evict/spill/elide chain is registered on the `Agent` (`build.py:48`) and runs *inside* the graph today. The owned loop must invoke it itself before each `model_request_stream` call, with `deps` (this is exactly `sdk-coupling-cleanup` S6 — those processors take `RunContext` but read only `ctx.deps`). Question: where in the loop does the chain run (once per request, before assembling the request?), and how is proactive-vs-overflow escalation ordered without the graph's processor pipeline? This is the largest single subsystem the graph currently hosts for co — under-weighted in the first draft. **Plus the post-processor step the graph also owns (CD-m-2):** after the registered processors, the graph runs `_clean_message_history` (`_agent_graph.py:893,2053`) — it merges consecutive `ModelRequest`s, sorts tool-return/retry parts to the front of a merged request, enforces "history ends with a `ModelRequest`", and back-fills timestamps. co's inline-approval + tool-result appends (§3.1) produce exactly the consecutive-request / part-ordering shapes that helper normalizes. Phase 2's preflight task must explicitly **port `_clean_message_history` semantics** (it's a kept-library helper co can call) or deliberately drop them with justification — the first draft's §6.4 stopped at the 5 processors and omitted this. **Peer evidence (2026-06-25 re-sync):** neither peer has a processor *pipeline* — both compact in a single summarize step (opencode **before** the call, `llm.ts:208`; hermes **after**, `conversation_loop.py:4182`). co's "before each request" placement matches opencode (correct — compact before spending the call); the 5-stage chain is genuinely co-unique (no template, test hardest). **Note for Phase 4 (placement, not Phase-2 scope):** the preflight built here is the home for the fill-unanswered stub-injection when **Phase 4** adds it — hermes runs that as a pre-call safety net *every step* (`conversation_loop.py:828` → `agent/agent_runtime_helpers.py:2057-2076`), not only on the exception path, so Phase 4 should extend this preflight rather than fix orphans at break-time. Phase 2 itself does not build stub-injection (parity with the graph path, which doesn't either).
- **OQ-7 (mixed text + tool-call response) — DECIDED → render+keep, eval-gated at Phase 2.** When one model response carries *both* `TextPart`s and `ToolCallPart`s, the owned loop renders and keeps the text *and* executes the tools (the graph discarded leading text as an output-validation artifact, with a recover-from-history fallback at `_agent_graph.py:1100` that becomes unnecessary). Rationale: narration before a tool call is useful and transparent; there is no reason to discard model text once co owns the loop. **Both peers render+keep (re-sync):** opencode streams text then settles tools; hermes keeps text (`conversation_loop.py:4020-4046`). Validation gate: watch groundedness + daily-chat evals for double-rendering (model repeating the text after tool results); if observed, port hermes's **housekeeping-mute** (suppress narration only when every tool call in the step is housekeeping — `memory`/`todo`/`skill_manage`/`session_search`) rather than reverting to discard. Single approach; the gate is a check, not a branch.
- **OQ-8 (instruction / system-prompt injection) — Phase 1/2, NEW.** The `Agent` injects static `instructions` + per-turn dynamic `agent.instructions(...)` (e.g. the wrap-up nudge). The owned loop must assemble the system prompt and re-inject per-turn dynamic instructions into the message/parameters itself each request (pydantic-ai's `direct._ensure_instruction_parts` bridges `instruction_parts` on `ModelRequestParameters`). Confirm dynamic per-turn instructions reach every request, not just the first. **hermes precedent (re-sync):** cache the static system prompt once and replay it verbatim every call, inserting dynamic nudges **after** the cached block (`conversation_loop.py:798-809`) — matters for co's Ollama prompt-cache stability; keep dynamic instructions after the cached static block, never interleaved.

## Next step

This is a milestone, not a single task — it should go through `/orchestrate-plan loop-decoupling` for the Core Dev (implementation risk) + PO (scope, first-principles) critique and Gate 1, with Phase 0's design spec as the first deliverable. The peer survey (`docs/reference/RESEARCH-loop-decoupling-peer-survey.md`) is the design input.

---

## Decisions

C1: Core Dev `approve / Blocking: none`; PO `revise / Blocking: PO-M-1`. C2: PO `approve / Blocking: none` — PO-M-1 confirmed resolved. Convergence at C2.

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| Boundary claim (CD) | verified | Core Dev confirmed `direct.model_request_stream` + `stream.get()` returns a full `ModelResponse` (`models/__init__.py:1133-1146`); reasoning-overflow raise is graph-only (`_agent_graph.py:1059`) so co must own the predicate. No blocker. | — |
| CD-m-1 | adopt | Subagent output is an SDK output-tool, `allow_text_output=False` (`_output.py:613,651-658`); the dream-reviewer was tuned to the tool path. My OQ-4 lean was backwards. | OQ-4: inverted lean to option (b) output-tool (parity-preserving); A/B at Phase 2 |
| CD-m-2 | adopt | `_clean_message_history` runs after processors (`_agent_graph.py:893,2053`) and normalizes exactly the consecutive-request / part-order shapes inline appends create. | OQ-6 + Phase 2 + Scope: port `_clean_message_history` in preflight |
| CD-m-3 | adopt | Pre-fan-out counting differs from today's per-call shed (`toolset.py:168-172`); pin + validate, don't assume byte-parity. | Phase 2: explicit shed rule + eval-watch |
| CD-m-4 | adopt | Inline approval orphans `clarify_answers`/`resume_tool_names` (`deps.py:208,215`) + `deferred_tool_awareness_prompt` (`orchestrator.py:91`). | Phase 5 dropped inventory += those three |
| CD-m-5 | adopt | `Agent[CoDeps, str \| DeferredToolRequests]` aliases live in `main.py:18,182` + `commands/types.py:9,21`. | Phase 5 gate names both modules |
| CD-m-6 | noted | finish_reason predicate verified graph-only + typed `Literal`. | — |
| PO-M-1 (blocking) | adopt | A behavior-preserving milestone-scale rewrite must argue proportionality in writing; proceed/defer is Gate 1's call. | Problem & Outcome: honest **Proportionality** subsection (A vs B; control + maintainability + v2-insulation; *not* capability-pulled; defer option teed to Gate 1). PO confirmed resolved C2. |
| PO-m-1 | adopt | Output validation is loop-owned now; name it. | Scope `In:` += "co owns subagent output extraction/validation" |
| PO-m-2 | adopt | Design companion already exists; Phase 0 is a ratification gate. | Phase 0 retitled "Decision gate"; specs untouched until Phase 6 |
| PO-m-3 | adopt | Deleting the deferred scaffold is right but should be eyes-open. | Phase 5: eyes-open note re: out-of-band tripwire |

## Research re-sync (2026-06-25)

The peer survey (`docs/reference/RESEARCH-loop-decoupling-peer-survey.md`) was re-verified against current peer HEADs (`hermes-agent d6269da7f`, `opencode 20fd32359`). This does **not** reopen Gate 1 — it folds verified detail into the already-approved phases/OQs. Net changes:
- **Citation fix:** hermes's fill-unanswered-tool_ids invariant relocated from `conversation_loop.py:4521` to a pre-call safety net `agent/agent_runtime_helpers.py:2057-2076` (called at `:828`), now run **every step** — adopted at that placement (Error space, Phase 4, OQ-6).
- **D1 (retry posture) — clarified → transient/timeout terminal, no inner retry.** The peers diverge (hermes has an inner retry+backoff loop; opencode has none); co takes the opencode shape, justified by local-first / 1–2 backends. This is **parity with co's current behavior** (co surfaces provider errors as terminal today) — it documents the existing posture, not a change. Lands in Phase 4's error matrix.
- **D2 (guaranteed termination on hard-stop) — recorded as a future enhancement, NOT a cutover phase.** opencode forces a tools-stripped final text turn at its step ceiling (`llm.ts:195-206`, `max-steps.ts`); co's tool-cap hard-stop only "surfaces the prior answer if any," weaker than both peers. **But adopting it would change observable behavior vs the graph, breaking this milestone's behavior-preserving / parity contract** — so it is explicitly out of scope for the cutover (Phases 1–6) and belongs to the post-milestone "richer loop-level small-model defenses" roadmap the Proportionality section names. Owning the loop *enables* it; the cutover does not ship it.
- **OQ-4 (b) peer-confirmed** by opencode `generateObject` (forced output tool, `llm.ts:116-129`) — and opencode *builds its own output def* rather than reaching into framework output machinery, reinforcing Phase 2's G1-1 public-construction preference; **OQ-7** gains hermes's housekeeping-mute as the conditional double-render mitigation; **OQ-8** gains hermes's cached-static + per-step-dynamic precedent; **approval pre-sequencing** flagged as co-original (no peer precedent — only the reject-halt is borrowed). The fill-unanswered citation fix refines **Phase 4's** placement (pre-call every step), not Phase 2.

## Final — Team Lead

**Gate 1 — APPROVED, PROCEED (decided 2026-06-25).** The proportionality call is made: this milestone runs **now, before any other SDK-thread followup**. Rationale accepted: the decoupling is the *common-root* fix (dissolves S1/S2/S4/S6), it only gets harder as more behavior accretes on the graph, and it **shrinks the pending `migrate-pydantic-ai-v2` plan to a fraction** of its current scope (most of v2's breaks are graph/Agent surface this milestone deletes). Sequencing: **(1) this milestone → (2) re-review `sdk-coupling-cleanup` (likely near-empty) → (3) `migrate-pydantic-ai-v2` LAST.** v2 goes last so the SDK-version bump lands once against the final settled architecture (decoupled + coupling re-reviewed), re-grounded once rather than twice; v1 is on a ~6-month security-only window, comfortable for this order. First executable step: **Phase 1** (Phase 0 design already drafted, ratified at this gate).

Plan approved — Core Dev `Blocking: none` (C1), PO `Blocking: none` (C2).

This is a **milestone**: Gate 1 approval greenlights the *milestone direction + Phase 0*, not all seven phases at once. Each phase re-enters `/orchestrate-plan <slug>-phaseN` for its own per-task `done_when` before implementation.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: **right problem? correct scope?** The load-bearing decision is the **Proportionality** call (Problem & Outcome): this is behavior-preserving (zero new user capability); the payoff is control + maintainability + v2-migration insulation, **not capability-pulled**. Proceed with the milestone now, or ship `sdk-coupling-cleanup` and **defer** until a capability need pulls it — that judgment is yours.
> If approved, the first executable step is **Phase 1** (the thin provider client) — Phase 0's design is already drafted (`2026-06-24-234633-loop-decoupling-design.md`); ratify it here. Run: `/orchestrate-plan loop-decoupling-phase1` to detail Phase 1's tasks, then `/orchestrate-dev`.
