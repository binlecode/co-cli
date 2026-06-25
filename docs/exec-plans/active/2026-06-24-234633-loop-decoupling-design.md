# Phase 0 design — owned agent turn loop (`0.9.0` loop-decoupling)

**Layer:** build-time design artifact for the milestone plan (`2026-06-24-234633-loop-decoupling-milestone.md`). Not a shipped spec — `docs/specs/core-loop.md` + `pydantic-ai-integration.md` are updated to this at Phase 6. Design input: `docs/reference/RESEARCH-loop-decoupling-peer-survey.md` (hermes + opencode) and the current `core-loop.md`.

This document is the thing `/orchestrate-plan loop-decoupling` should pressure-test. It states the target design and flags every decision (OQ-3/4/6/7/8) inline with a recommendation, so Core Dev (implementation risk) and PO (scope) have a concrete object to attack.

## 1. The boundary

co drives **one model turn at a time** through pydantic-ai's `direct` layer and owns everything around it. pydantic-ai stays as a **provider + message library**; only the *graph* leaves.

```
        ┌─────────────────────────────────────────── co owns ───────────────────────────────────┐
        │  run_turn → turn loop → step loop:                                                      │
        │    preflight (history processors + instructions)   ┐                                    │
        │    build ModelRequestParameters(function_tools=…)  ├─ co code                           │
        │    ── model_turn(model, messages, params) ─────────┼──→  THE BOUNDARY  ────────┐         │
        │    iterate typed events → render                   │                           │         │
        │    response = stream.get()  (assembled, repaired)  ┘                           │         │
        │    classify parts → dispatch tools (cap+approval+spill) → append → repeat       │         │
        └────────────────────────────────────────────────────────────────────────────────┼───────┘
                                                                                           │
   pydantic-ai as library:  direct.model_request_stream · OpenAIChatModel/OllamaProvider · GoogleModel
                            ModelMessage/*Part · ModelMessagesTypeAdapter · ToolDefinition · ModelSettings
                            RequestUsage · ModelHTTPError/ModelAPIError
```

**The single call that replaces the graph:** `pydantic_ai.direct.model_request_stream(model, messages, model_request_parameters=ModelRequestParameters(function_tools=[ToolDefinition…]))` → an async-context `StreamedResponse`. co iterates it for render deltas and calls `.get()` for the assembled `ModelResponse` (OQ-2 resolved). Verified present in 1.92.0.

## 2. Vocabulary delta

Current: **`turn ⊇ run ⊇ model request`** — a *run* is one `agent.iter()` / `AgentRunResult`; a turn spans multiple runs at approval-resume and length-retry boundaries.

Owned: **`turn ⊇ step`** — the *run* level disappears (there is no `AgentRunResult`, no `agent.iter()`). A **turn** is one user message; a **step** is one model request + its tool dispatch. The turn loop re-enters only for length-continuation retry; approval no longer creates a new "run" (it is inline within a step). This is simpler than the SDK's three-level model and is the hermes/opencode shape.

### 2.1 Why three layers, not two — the rationale (→ MERGE TO SPEC, Phase 6 → `core-loop.md`)

> **Spec-merge marker:** this subsection is the load-bearing *why* behind the owned loop's layering. It is design-artifact prose now; at PHASE 6 it merges into `docs/specs/core-loop.md` (and the framing half of `pydantic-ai-integration.md`) as the canonical explanation of co's two-level loop. Do not let it die with this plan.

A turn and a step are **intrinsic to any agentic loop**; the "run" is intrinsic to **graph engines**, not to agentic loops. Strip the loop to its primitive and there are exactly two natural scopes:

```
while True:                 # the TURN — one user message, possibly many round-trips
    response = call_model()
    if no tool calls: break # done-ness is a LOCAL predicate, not an object
    dispatch tools          # the STEP repeats
```

Done-ness is a one-line check ("no tool calls", §3.1), not a separate contract. Nothing here needs a third layer. Both surveyed peers land on exactly this shape **because they hand-write the loop** (hermes on the OpenAI SDK, `conversation_loop.py:589`; opencode on a thin `stream()` boundary, `llm.ts:225`) — the provider layer is a codec + transport that does *one* call and never loops (peer survey §"The convergent shape", lines 12–25). With no engine, there is no run-completion semantics to model.

pydantic-ai's middle "run" layer is **not** something the problem domain asked for — it is the SDK's `pydantic_graph` execution unit leaking into co's vocabulary. This is **vendor-confirmed, not co's inference**: pydantic-ai's own docs state the agent is built on `pydantic-graph` (a finite-state-machine library) and that `agent.run()` "simply traverses the underlying graph from start to finish," with `Agent.iter` exposing the node-by-node walk as an `AgentRun` until the graph returns `End` ([ai.pydantic.dev/agent](https://ai.pydantic.dev/agent/)). The run *is* the graph traversal. Two graph properties force it into co's layering:

1. **The graph's unit is "walk to a typed `End`."** A run carries a validated `output_type` contract (`run.result`, `output_type=[str, DeferredToolRequests]`) — a completion boundary co never wanted (co's done-ness is "no tool calls"). The contract *is* the run layer.
2. **The graph can't span co's turn, so co wraps it.** Approval-resume and length-continuation each *restart the graph* (a fresh `agent.iter()` = a new run). The graph's natural unit (one clean walk to `End`) is **finer than co's turn yet coarser than a single model request** — so co must wedge a turn-wrapper around N runs. That mismatch *is* the third layer.

So the run is a real, coherent abstraction (validated graph output) that is simply **mismatched in size** to co's needs and contract-bearing in a way co's "no tool calls" never required. Deleting the graph collapses co's vocabulary to the peers' two layers — which is precisely what §7 means by "the run level disappears." Owning the loop is not adding structure; it is removing a layer the engine imposed.

**The one thing the graph genuinely buys, co designs out.** pydantic-ai's own justification for a graph is durable suspend/resume — "the logic required to interrupt and resume execution" otherwise "dominating the implementation" ([ai.pydantic.dev/graph](https://ai.pydantic.dev/graph/), which also cautions a graph "might be unnecessary" if you're not sure you need one). For co that suspend/resume need is *approval* (today's `DeferredToolRequests` suspend → resume). §6.5 makes approval **inline within a step**, so co never suspends the loop — removing the sole property that would have justified the graph. This is why owning the loop is net-simpler for co specifically, not just stylistically.

**This layering choice tracks a frontier consensus, not a co idiosyncrasy.** The single-agent tool-calling loop is converging industry-wide on the owned `while`-loop: Anthropic's *Building Effective Agents* reports the most successful implementations "weren't using complex frameworks... but instead building with simple, composable patterns" and warns that wrong assumptions about framework internals are "a common source of error" ([anthropic.com](https://www.anthropic.com/research/building-effective-agents)); the 2026 framing is explicitly "who owns the loop?" with graphs re-scoped *upward* to durable/branching **multi-agent** orchestration, not the inner loop ([LangChain docs](https://docs.langchain.com/oss/python/langgraph/workflows-agents)). co's four owned-loop peers (hermes, opencode, openclaw, codex) are all two-level; **opencode is a documented migration off** an SDK-graph-owned loop (Vercel `ai` SDK) onto a thin `stream()` boundary — the exact move co is making. The corollary is the escape hatch: if co ever grows genuine multi-agent *branching* (not today's single-driver subagents, §6.11), a graph at *that* altitude would be the right tool — this milestone removes the graph from the inner loop, where it is overkill, not from co's future.

## 3. The owned loop (target control flow)

```python
async def run_turn(*, agent_ctx, user_input, message_history, model_settings, frontend, deps) -> TurnResult:
    deps.runtime.reset_for_turn(); deps.usage_accumulator.reset()
    state = TurnState(history=message_history, pending_input=user_input, settings=model_settings)
    with co_turn_span():
        while True:                                  # turn loop: only length-retry re-enters
            try:
                await _run_steps(state, deps, frontend)        # the agentic step loop (§3.1)
            except CtxOverflow as e:        ... recover_overflow_history(deps, …) → retry or terminal
            except ProviderHTTP400Reformulate: ... reflect → retry
            except (ProviderError, Timeout, Interrupt) as e: ... return terminal/interrupted TurnResult
            else:
                boosted = _length_retry_settings(state)        # truncated final text → boost & re-enter
                if boosted: state.settings = boosted; state.drop_truncated_tail(); continue
                return _finalize(state)                         # success TurnResult
```

### 3.1 The step loop

```python
async def _run_steps(state, deps, frontend):
    while True:
        # PREFLIGHT (OQ-6) — runs every step, with deps (no RunContext)
        msgs = run_history_processors(state.history, deps)      # elide→dedup→evict→spill→proactive
        instr = assemble_instructions(deps)                     # static + dynamic (safety/wrap-up/time/…)
        params = ModelRequestParameters(function_tools=tool_defs(deps), instruction_parts=instr)

        # MODEL REQUEST — the boundary
        with model_span():
            async with model_turn(deps.model, msgs, params, state.settings, repair=deps.uses_ollama) as stream:
                async for ev in stream:                          # typed events (§4)
                    render(ev, renderer, frontend)
                response = stream.get()                          # assembled + JSON-repaired
        accumulate_usage(deps, response.usage); state.model_requests += 1

        # REQUEST CAP (co-owned; replaces UsageLimits) — circuit breaker, default 40
        if request_limit and state.model_requests >= request_limit:
            raise RequestCapReached()                            # → terminal in run_turn

        tool_calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        state.history.append(response)                           # keep text + tool calls (OQ-7)

        # FINAL-ANSWER branch — typed, no string-match (S4 dissolved)
        if not tool_calls:
            state.final_response = response
            if _is_reasoning_overflow(response):                 # empty/thinking-only + finish_reason=='length'
                raise ReasoningOverflow()                        # typed → actionable status, terminal
            state.exit_reason = TurnExit.FINAL_TEXT
            return

        # TOOL DISPATCH
        state.cap.note_calls(len(tool_calls))                    # ToolCapState (S2)
        approved = await collect_inline_approvals(tool_calls, frontend, deps)   # OQ-1, pre-fanout
        results = await dispatch_tools(tool_calls, approved, deps)              # parallel ≤3, spill inline
        state.history.extend(results)
        if state.cap.hard_stop:
            state.exit_reason = TurnExit.TOOL_CAP; return        # surface prior answer if any (parity; forced-final-turn is a future enhancement, §6.3)
```

The step loop ends a step in exactly three ways: **final text** (no tool calls), **tool-cap hard-stop**, or an **exception** (cap/overflow/error/interrupt) bubbling to `run_turn`. There is no `run.result` contract to satisfy; `state.final_response` carries the answer.

## 4. Typed event model (the boundary's output)

`model_turn` yields a small typed union, decoupling render from the SDK's part/delta classes:

| Event | Carries | Render |
| --- | --- | --- |
| `TextDelta` | str | append to text surface |
| `ThinkingDelta` | str | append to thinking surface (mode-gated) |
| `ToolCallStarted` | tool name, id, args-so-far | tool-start annotation |
| (assembled) | `stream.get()` → `ModelResponse` | source of truth for parts + finish_reason + usage |

Open: emit our own event dataclasses, or pass through the SDK's `PartStartEvent`/`PartDeltaEvent` (co already handles these in `_handle_model_request_event`). **Lean: pass through SDK part-events for render, treat `stream.get()` as the authoritative assembled response** — minimal new types, the events are render-only. Revisit if a second provider's event shape diverges.

## 5. Kept / dropped inventory (symbol-level)

**Kept (pydantic-ai as library):** `direct.model_request_stream`, `OpenAIChatModel`/`OllamaProvider`/`GoogleModel`/`GoogleProvider`, `ModelMessage`/`ModelResponse`/`*Part`, `ModelMessagesTypeAdapter` (persistence/compaction/observability unchanged), `ToolDefinition`, `ModelRequestParameters`, `ModelSettings`/`GoogleModelSettings`, `RequestUsage`, `ModelHTTPError`/`ModelAPIError`.

**Dropped (the graph + its scaffolding):**
- `Agent`, `agent.iter()`, the node walk, `run.result`, `Agent.is_model_request_node`/`is_call_tools_node` — replaced by §3.
- `output_type=[str, DeferredToolRequests]` — done-ness is "no tool calls".
- `SurrogateRecoveryModel` (WrapperModel) — its 3 concerns inline into `model_turn` (surrogate retry, chat span, JSON repair).
- `_RepairingStreamedResponse` — repair lands on `stream.get()` inline (**S1 dissolved**: no dependence on the graph validating `get()`; co calls `get()` itself).
- `_CallSeamToolset` (WrapperToolset) — span + cap + spill inline into `dispatch_tools` (**S2 dissolved**: `ToolCapState` counts at the step boundary co owns).
- `DeferredToolRequests`/`DeferredToolResults`/`ToolApproved`/`ApprovalRequired`/`ToolDenied` — replaced by inline approval (§7).
- `UsageLimits(request_limit=…)` — co counts `model_requests` itself (§3.1).
- Synthetic `RunContext` at `compact.py:41` and `orchestrate.py:858` (**S3-partial/S6 dissolved**: processors take `deps`). `schema_budget.py:62`'s `RunContext` is re-evaluated (still needs `prepare_tool_def` — may persist if OQ-3 keeps `FunctionToolset` as schema gen).
- `_REASONING_OVERFLOW_SIGNATURE` string-match (**S4 dissolved**: `_is_reasoning_overflow(response)` reads `finish_reason` + parts, typed).

## 6. Component designs

### 6.1 Provider client `model_turn` (Phase 1)
A `co_cli/llm/` async context manager wrapping `direct.model_request_stream`, with `SurrogateRecoveryModel`'s three concerns inlined: (a) catch `UnicodeEncodeError` → re-sanitize messages → retry once; (b) push/pop the `co.model.*` `chat` span; (c) on `.get()`, run `_repair_json_args` over each string `ToolCallPart.args` when `repair=True` (Ollama). Reuses `_message_sanitize`, `_repair_json_args`, `serialize_messages`/`serialize_response`. Unit-testable in isolation.

### 6.2 Turn state
Keep an explicit `TurnState` (reconstructed each turn — never agent-object counters; the current `_TurnState` is already this shape). Add a typed `TurnExit` enum (hermes's `_turn_exit_reason` discipline): `FINAL_TEXT | TOOL_CAP | REQUEST_CAP | OVERFLOW_UNRECOVERABLE | REASONING_OVERFLOW | PROVIDER_ERROR | TIMEOUT | INTERRUPTED | REFORMULATION_EXHAUSTED`. One grep-able value answers "why did the turn end", set at every exit.

### 6.3 Tool cap (S2 → `ToolCapState`)
The `sdk-coupling-cleanup` S2 design, now native: `note_calls(n)` counts a step's tool calls and latches `hard_stop` after `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive over-`MAX_TOOL_CALLS_PER_MODEL_REQUEST` steps; the step boundary is a literal line in §3.1 (no node-boundary inference). Excess calls beyond the cap shed `make_exceeded_payload`. Reset per turn. On `hard_stop` the cutover preserves co's current behavior — surface the prior answer if any (parity with the graph's `_check_turn_caps`).
- **Future enhancement, NOT in the cutover (D2 — recorded 2026-06-25 re-sync):** opencode forces a clean tools-stripped final text turn at its step ceiling (`packages/core/src/session/runner/llm.ts:195-206` + `max-steps.ts`: strip tools, `toolChoice:"none"`, inject "respond with text only"); hermes uses a grace-call + synthesized message (`conversation_loop.py:605-614,4626`). co's hard-stop can terminate with no final text — weaker than *both* peers. Adopting a forced final turn would *change observable behavior vs the graph*, so it is **out of the behavior-preserving cutover** and belongs to the post-milestone small-model-defense roadmap. Owning the loop is what *enables* it; the cutover does not ship it.

### 6.4 History processors (OQ-6 — the heavy relocation)
`run_history_processors(history, deps)` applies the five processors in the current order — `elide_old_multimodal_prompts → dedup_tool_results → evict_old_tool_results → spill_largest_tool_results → proactive_window_processor` — **once per step**, before assembling the request, calling each with `deps` (not `RunContext` — S6). `proactive_window_processor`'s inline-summary + circuit-breaker + the no-progress escalation into `recover_overflow_history` move verbatim; only the parameter type changes. This is the single largest subsystem the graph hosts for co and the riskiest part of Phase 2.
- **Decision for orchestrate-plan:** does preflight run every step (matches today's per-request processor firing) or only when the model-request is about to be sent? Today the SDK runs processors before every `ModelRequestNode`; §3.1 preserves that (every step). Confirm no double-compaction across steps within one turn.
- **Peer-confirmed co-unique (2026-06-25 re-sync):** neither peer runs a multi-stage processor *chain* — both compact in a *single* summarize step (opencode `compactIfNeeded` **before** the call, `llm.ts:208`; hermes `should_compress` **after**, `conversation_loop.py:4182`). co's "before each request" placement matches opencode and is correct (compact before spending the call); the 5-stage pipeline has **no peer template**, so it is the section to test hardest. **Placement note for the Phase-4 fill-unanswered work (not Phase 2):** this preflight is the home for the fill-unanswered stub-injection (§6.9) when it lands — hermes runs that as a pre-call safety net *every step* (`conversation_loop.py:828` → `agent/agent_runtime_helpers.py:2057-2076`), the analogue of this chain's tail + `_clean_message_history` (CD-m-2). The Phase-2 preflight builds the chain + normalization; the stub-fill extends it in Phase 4 (Phase 2 is parity with the graph, which does not stub-fill).

### 6.5 Inline approval (OQ-1 resolved)
`collect_inline_approvals(tool_calls, frontend, deps)` — for the approval-required subset of a step's tool calls, prompt **sequentially before** the parallel fan-out (avoids racing terminal prompts under the ≤3 parallel cap). Reuses `resolve_approval_subject`, `is_auto_approved`, `record_approval_choice` verbatim. Denied calls become denial tool-results fed back to the model; a reject may halt the step (opencode clears pending fibers on reject, `llm.ts:290-293` — *this* half is the peer pattern). **The pre-fan-out sequencing itself is co-original (2026-06-25 re-sync):** neither peer pre-sequences — hermes blocks per-tool inside execution on a `threading.Event` (`tools/approval.py:1352`), opencode races questions uncapped in parallel (`llm.ts:177,264`). co sequences because it has one terminal and a ≤3 cap; the survey gives co no precedent here, so this gets first-principles scrutiny (risk #2), not borrowed confidence. `frontend is None` → auto-deny (headless parity). `clarify` prompts inline and returns its answer directly — drops the `clarify_answers` runtime stash + the resume-validation `override_args` workaround entirely.
- **Tripwire (record only):** inline forecloses *out-of-band* approval (propose → exit → approve later). co has no such flow today (headless denies). A future headless-with-sign-off feature would be new design, not a regression here.

### 6.6 finish_reason / reasoning-overflow (S4 dissolved)
`_is_reasoning_overflow(response)` = `response.parts` empty-or-thinking-only **and** `finish_reason == 'length'` — co now owns the predicate the SDK applied at `_agent_graph.py:1059`. Surfaces the actionable "simplify / raise max_tokens" status, terminal (`TurnExit.REASONING_OVERFLOW`). The text-present + `length` case is the length-continuation retry (`_length_retry_settings`, §3 turn loop), unchanged.
- **Note:** co now replicates the SDK's emptiness predicate. If pydantic-ai changes it, co diverges — but co is no longer at the mercy of the SDK *raising*; the predicate is small, owned, and test-pinned. Net coupling is lower (no string-match) and louder on drift (a behavior test, not a message-substring test).
- **Typed finish_reason is inherited, not minted (2026-06-25 re-sync):** unlike the peers (hermes's `map_finish_reason`, opencode's literal union `schema/ids.ts:39`), co does **not** build its own finish_reason vocabulary — CD-m-6 confirmed pydantic-ai's `finish_reason` is already a typed `Literal` on the kept `ModelResponse`. co's owned work is only to *branch* on it. And the length-continuation retry + `_is_reasoning_overflow` predicate are **beyond both peers** (opencode treats `length` like `stop`; hermes maps it but does not continuation-retry) — a deliberate co-specific small-model defense, kept as beyond-parity.

### 6.7 Mixed text + tool-call response (OQ-7)
When a step's `response` has both `TextPart`s and `ToolCallPart`s: **render and keep the text, then execute the tools.** The graph discarded leading text as an output-validation artifact (with a recover-from-history fallback at `_agent_graph.py:1100`); owning the loop removes the reason to discard — narration before a tool call is useful and transparent. **Lean: render+keep — peer-validated both sides (2026-06-25 re-sync):** opencode streams text deltas then settles tools; hermes keeps text (`conversation_loop.py:4020-4046`). Risk to validate against evals: double-rendering if the model repeats the text after tool results (watch groundedness/daily-chat evals). **Mitigation if it materializes:** port hermes's **housekeeping-mute** — suppress the inline narration only when *every* tool call in the step is housekeeping (`memory`/`todo`/`skill_manage`/`session_search`) and show it when any substantive tool is present — rather than reverting to discard. Decision belongs to Phase 2.

### 6.8 Instruction injection (OQ-8)
`assemble_instructions(deps)` builds the static system prompt once (cached) + evaluates the per-turn dynamic instructions (`safety_prompt`, `wrap_up_prompt`, `current_time_prompt`, `deferred_tool_awareness_prompt`, `skill_manifest_prompt`) **every step**, passed via `ModelRequestParameters.instruction_parts` (bridged by `direct._ensure_instruction_parts`). Confirm the wrap-up nudge still fires on the `requests == limit - 1` step (it reads `deps`/state, not SDK `ctx.usage` — re-source the request count from `state.model_requests`). Dynamic instructions stay ephemeral (never appended to durable history) — same as today.
- **hermes precedent (2026-06-25 re-sync):** hermes builds the static system prompt **once per session, caches it, and replays it verbatim every call** (`conversation_loop.py:798-802`), inserting dynamic per-turn nudges **after** the cached block (`:806-809`) — explicitly for upstream prompt-cache stability. co's cached-static + per-step-dynamic shape matches this; it matters for co's Ollama prompt-cache sensitivity, so keep dynamic instructions **after** the cached static block, never interleaved into it.

### 6.9 Error / recovery model
`run_turn`'s error matrix (current §2.5) relocates largely intact, now over co-typed exceptions instead of SDK-raised ones. Adopt hermes's `FailoverReason`-style typed classification only where co has >1 recovery action (overflow→compact, 400→reformulate). **Transient/timeout → terminal, no inner retry (D1 — decided per 2026-06-25 re-sync, opencode-shape):** co does *not* adopt hermes's inner retry-with-jittered-backoff loop (`conversation_loop.py:946`, `agent/retry_utils.py:28`) — co is local-first (1–2 backends, mostly localhost Ollama), so hermes's rate-limit/credential-rotation ladder is inapplicable; opencode likewise has no transient retry (only overflow→recover re-entry). A bounded retry for genuine localhost hiccups stays optional/deferred. **Preserve the fill-unanswered invariant, at the upgraded placement:** fill every unanswered `ToolCallPart` id with an error/stub tool-result so the tool-call↔result pairing stays valid. hermes has moved this from a break-time fixup to a **pre-call safety net run every step** (`agent/agent_runtime_helpers.py:2057-2076`, called at `conversation_loop.py:828`) — co should do the same: run the stub-fill in preflight (§6.4), every step, not only on the exception path, so it also catches orphans from compaction and session-load. The stall timeout (`asyncio.timeout(run_stall_timeout_secs)`, armed on model events, disarmed during tool dispatch) wraps the step loop's model-request section — relocated from `_execute_run`, same semantics.

### 6.10 Usage accumulation
co accumulates `RequestUsage` across steps itself (replacing the SDK's turn-scoped `RunUsage` threaded via `agent.iter(usage=…)`): one running total on `TurnState`, recorded once in `run_turn`'s `finally` (today's `record_usage` contract). Forked subagent/summarizer tokens still roll into `deps.usage_accumulator` via their own boundaries.

### 6.11 Subagents (OQ-4) + tool-def source (OQ-3)
- The owned step loop is the **single driver** for the orchestrator *and* task agents (`build_task_agent`). A subagent is the same loop with a narrower tool set (`requires_approval=False`, so §6.5 is a no-op for them) and an output expectation.
- **OQ-4 (lean inverted per milestone CD-m-1; peer-confirmed 2026-06-25 re-sync):** `TaskAgentSpec.output_type` is always a `BaseModel`, and the SDK steers it via an output tool (`allow_text_output=False`), which the dream-reviewer was tuned against. So the parity-preserving mechanism is **(b)** register the output model as an output tool (`ModelRequestParameters.output_tools`) and detect its call — *not* free-text JSON parse + re-prompt. **opencode is a direct precedent:** its `generateObject` forces `toolChoice.named(...)` + schema-decode + error-if-not-called (`packages/llm/src/llm.ts:116-129`); hermes has no structured-output enforcement. Lean: **(b)**; A/B against the dream-reviewer evals at Phase 2, fall back to (a) re-prompt only if (b)'s plumbing proves heavy.
- **OQ-3:** generate `ToolDefinition`s from the `@agent_tool` catalog, or keep a `FunctionToolset` purely as a schema generator (`prepare_tool_def`, which `schema_budget.py` also uses). Lean: reuse the schema generator if cheaper than re-deriving JSON schemas; this also decides whether `schema_budget.py:62`'s synthetic `RunContext` survives. Phase 1.

## 7. What dissolves (S1–S6 mapping)

| Coupling | Fate under owned loop |
| --- | --- |
| S1 stream-repair → graph validates `get()` | **dissolved** — co calls `get()` itself; repair is inline |
| S2 diffuse tool-cap | **dissolved** — `ToolCapState` at the literal step boundary (§6.3) |
| S3 synthetic `RunContext` (`prepare_tool_def`) | **conditional** — survives iff OQ-3 keeps `FunctionToolset` as schema gen |
| S4 reasoning-overflow string-match | **dissolved** — typed `_is_reasoning_overflow` (§6.6) |
| S5 MCP schema-shape | **unchanged** — still sanitize the converted MCP schema (kept library surface) |
| S6 `RunContext`-as-deps-carrier | **dissolved** — processors/compaction take `deps` (§6.4) |

So the milestone resolves S1, S2, S4, S6 outright; S3 is decided by OQ-3; S5 persists (it's a kept-library concern, not a graph coupling). This is why `sdk-coupling-cleanup` is on hold and rescoped after — most of its census evaporates here.

## 8. Risks / for orchestrate-plan to attack

1. **History-processor relocation (OQ-6)** — the heaviest, co-specific (no peer has co's compaction pipeline). Faithful per-step firing + overflow-escalation ordering without the SDK pipeline is the main correctness risk.
2. **Inline approval under parallel dispatch** — pre-prompt-before-fanout must be airtight or prompts race the terminal. **No peer precedent** (hermes blocks per-tool, opencode races uncapped); co's pre-sequencing is original, so this gets first-principles scrutiny, not borrowed confidence (§6.5).
3. **finish_reason predicate drift (§6.6)** — co now owns the emptiness predicate; pin it behaviorally.
4. **Eval parity is the only safe cutover gate** — the owned path must match the graph path on the full `evals/` suite before Phase 5 deletes the graph. Strangler-fig (flagged parallel path) is mandatory; no big-bang.
5. **Scope creep into the message model** — explicitly out. This is *loop* independence; `ModelMessage` stays.
