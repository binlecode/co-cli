# RESEARCH — Loop-decoupling peer survey (hermes-agent, opencode)

**Date:** 2026-06-25 (re-verified against peer HEADs; supersedes the 2026-06-24 draft). **Purpose:** design reference for decoupling co's agent turn loop from the pydantic-ai *graph* (the `0.9.x` loop-ownership milestone). Code-first study of two peers that own their loop. **Peer HEADs surveyed:** `hermes-agent` `d6269da7f` (2026-06-25), `opencode` `20fd32359` (2026-06-25) in `~/workspace_genai/`. Every `file:line` below was re-verified at these HEADs; lines that moved since the prior draft are corrected (the largest correction: hermes's fill-unanswered-tool_ids invariant has relocated out of `conversation_loop.py` — see decision 6).

## The thesis, validated by both peers

co today delegates the **main agent turn** to pydantic-ai's agent graph (`agent.iter()`); helper calls already run graph-free via `pydantic_ai.direct.model_request` (`co_cli/llm/call.py`). Both peers **own** their loop:

- **hermes-agent (Python)** hand-writes the loop directly on the OpenAI Python SDK (`client.chat.completions.create`). No agent-graph framework. The loop is a plain `while` over counters. Loop function: `agent/conversation_loop.py:495`; the outer agentic `while` is `conversation_loop.py:589`.
- **opencode (TS)** *migrated off* an SDK-owned loop: **V1** delegated the whole tool loop to the Vercel `ai` SDK's `streamText`; **V2** (current) owns the loop on a thin in-house provider library exposing just `stream(request)` / `generate(request)`. This is exactly co's contemplated migration, executed by a peer. Loop core: `packages/core/src/session/runner/llm.ts:225` (the `llm.stream(request)` boundary call).

**The convergent shape (both):**

```
owned loop:  while needs_continuation:
                 build request (messages + tool defs + settings)
                 stream = provider.stream(request)        # ONE model turn — the boundary
                 for event in stream:                      # text/thinking/tool-call deltas
                     render; if tool_call: collect
                 dispatch collected tool calls; append results
                 needs_continuation = (a tool_call was emitted this turn)
             # absence of tool calls ⇒ final answer ⇒ done
```

The provider layer is a **codec + transport** that does *one* model call and yields typed events. It never loops, never executes tools. Everything above — loop, dispatch, continuation, approval, errors, compaction — is owned.

## Convergent design decisions (both peers; borrow these)

These are the decisions both peers converged on independently — the highest-confidence borrows.

1. **Termination = "did the model emit a tool call?"** — not finish_reason. hermes: `if assistant_message.tool_calls: …continue else: …break` (tool branch `conversation_loop.py:3834`; no-tool terminal `:4200-4470`). opencode: `needsContinuation` set `true` on a tool-call event (`llm.ts:241`), read at step end (`llm.ts:337`). **This dissolves co's S4** (the reasoning-overflow string-match): finish_reason becomes a *typed* signal consulted only for truncation/error, never the primary control branch.

2. **finish_reason normalized to a small typed vocabulary at the provider boundary.**
   - hermes: `"stop" | "tool_calls" | "length" | "content_filter"` via `map_finish_reason` (field `agent/transports/types.py:106`, fn `:167`), fallback `"stop"`.
   - opencode: typed literal union `["stop","length","tool-calls","content-filter","error","unknown"]` (`packages/llm/src/schema/ids.ts:39`), per-protocol mappers (`protocols/openai-chat.ts:370`). Derives `tool-calls` from accumulated tool calls when the provider returns `stop` (`openai-chat.ts:449-456`).
   - The loop branches on typed values / `._tag`, **never** on a raw provider string.
   - **co mapping (closes the prior draft's open item):** co does **not** build its own mapper — CD-m-6 verified pydantic-ai's `finish_reason` is already a typed `Literal`. co gets decision-2's *value* (a typed finish_reason) for free from the **kept** library; the owned-loop work is only to *branch* on it (length-retry, reasoning-overflow) rather than string-match a raised exception. Note this explicitly in the design so the "missing mapper" reads as intentional, not an omission.

3. **Context overflow classified once, typed, at the boundary** — not string-matched at the decision point. opencode: `isContextOverflowFailure` checks `reason._tag === "InvalidRequest" && classification === "context-overflow"` (`provider-error.ts:29-32`); the regex bank runs once to *produce* that classification. (co already has this shape in `_http_error_classifier.py` — keep it, feed the typed loop.)

4. **Typed error classification with action-hint flags.** hermes `FailoverReason` enum + `ClassifiedError` dataclass carrying `retryable / should_compress / should_rotate_credential / should_fallback` (`agent/error_classifier.py:24,69`); the retry loop reads flags instead of re-classifying. Clean separation of "what went wrong" from "what to do." (co adopts only the flags it has actions for — overflow→compact, 400→reformulate, transient→terminal/retry — and drops `should_rotate/should_fallback`, which are multi-provider gateway concerns co lacks.)

5. **Invariant: fill unanswered `tool_call_id`s with error/stub results before the API call** so the tool-call ↔ tool-result pairing the API requires stays valid. **CORRECTION — citation moved:** in hermes this is no longer in `conversation_loop.py:4521` (that line now holds ephemeral-scaffolding cleanup, decision 8). It is now `_sanitize_api_messages` → `agent/agent_runtime_helpers.py:2057-2076` ("Inject stub results for calls whose result was dropped"), called **unconditionally before every API call** at `conversation_loop.py:828`. The shift is instructive: hermes moved this from a break-time fixup to a **pre-call safety net that runs every step**, catching orphans from session loading and compaction too, not just mid-turn exceptions. **co should adopt the pre-call placement** (run it in preflight, every step — see OQ-6/§6.4), not only on the exception path.

6. **JSON-arg repair ladder for malformed local-model tool args**, last-resort `"{}"` so a bad arg never crashes the session (hermes `agent/message_sanitization.py:185-279`, last-resort `:279`). **co already has this** in `surrogate_recovery_model.py:_repair_json_args` — owning the loop relocates it inline, no longer a `WrapperModel` concern. (opencode V2 *dropped* its repair and regressed — see Do-NOT-borrow; co keeps the ladder.)

7. **Mixed response (text AND tool calls in one assistant message) → keep + render the text, then execute the tools.** Both peers do this — strong validation of co's OQ-7 "render+keep" lean.
   - opencode renders text deltas in real time as they stream (`protocols/openai-chat.ts:414` accumulates text deltas; `publish-llm-event.ts:246-264` publishes them) and settles tools after the stream.
   - hermes keeps the text as a fallback final answer (`conversation_loop.py:4020-4046`) — **and adds a refinement co should consider** (see OQ-7 below): it mutes the inline narration only when *every* tool call in the turn is "housekeeping" (`memory`/`todo`/`skill_manage`/`session_search`), and shows it when any *substantive* tool (read/write/search/terminal) is present. This is a concrete mitigation for the design's stated double-render risk.

8. **History = the simplest representation that works.** hermes: raw OpenAI-shaped `list[dict]`, no message-object hierarchy. Ephemeral retry/recovery scaffolding lives in the same list tagged with underscore-prefixed keys (`_thinking_prefill`, `_empty_recovery_synthetic`, `_empty_terminal_sentinel`), popped before the durable answer is appended (`conversation_loop.py:4521-4530`). co's analogue: keep `ModelMessage` as the wire+durable type; co's length-retry tail-drop (`state.drop_truncated_tail()`) is the same discipline.

9. **Two message models with explicit translators** (opencode): durable `SessionMessage` (event-sourced, tool *state* `pending|running|completed|error`) ↔ thin wire `Message`/`ContentPart`, one-way translators each direction (`to-llm-message.ts:157`, `publish-llm-event.ts:54-423`). co's lighter analogue: keep `ModelMessage` as the wire type, keep co's session JSONL as the durable layer — explicitly **out of scope** for this milestone (loop independence, not message-model independence).

10. **Explicit per-turn state object, reconstructed each turn.** Both warn (by counter-example) against per-turn counters living on the long-lived agent object — hermes does this (`agent._invalid_tool_retries`, `agent._empty_content_retries`, reset at turn boundaries) and it is its worst readability/drift problem. co already has the right shape (`_TurnState`); the owned loop should keep it and add hermes's `_turn_exit_reason` discipline (one grep-able value answering "why did the turn end").

## Where the peers DIVERGE — co must choose (don't assume a convergent borrow)

The prior draft listed these as "borrow these"; re-verification shows the two peers take **opposite** approaches. Each needs an explicit co decision rather than an inherited (non-existent) convergence. D1 (no inner retry, opencode-shape) and D3 (cached static + per-step dynamic instructions, hermes-shape) are **decided and built** on the owned-loop path — their behavior is already documented in `core-loop.md` (parity), so they are dropped from this survey. The one **still-open** divergence is D2.

### D2 — Guaranteed termination at the step ceiling (the prior draft's item 9, corrected)
Both peers guarantee a user-facing answer when the loop hits its ceiling, but by **opposite mechanisms**:
- **opencode: force a clean final text turn.** On the last step (`llm.ts:195` `isLastStep`), it **strips tools** (`:196` materialize→`undefined`), **forces `toolChoice:"none"`** (`:206`), and **injects** `MAX_STEPS_PROMPT` ("Tools are disabled… Respond with text only", `max-steps.ts:1-16`) as an assistant message (`:204`). If the model still emits a tool call, it is failed (`llm.ts:237-239`). This *guarantees* a text answer exists.
- **hermes: grace-call + synthesized apology.** A `_budget_grace_call` flag grants one extra model call after the iteration budget is exhausted (`conversation_loop.py:605-614`); if errors persist near the ceiling it synthesizes a final assistant message (`I apologize, but I encountered repeated errors…`) and breaks (`:4626-4632`). No tool-strip, no forced-text turn.
- **co decision — deferred, NOT in the cutover (pending post-milestone work):** co's tool-cap hard-stop surfaces the salvaged prior answer (parity with the graph), so it can terminate with **no** final text — weaker than *both* peers. Design §6.3 weighs this and explicitly defers the fix because forcing a final turn *changes observable behavior vs the graph*, which the behavior-preserving cutover forbids. **Pending work (post-milestone small-model-defense roadmap):** adopt opencode's forced-text final turn on hard-stop (strip tools + one tools-disabled model call) so a hard-stopped turn always yields a clean answer. Owning the loop is what enables it; the cutover does not ship it.

## Mapping to co's open questions (the design-doc guidance)

Direct peer evidence for the OQs the design (`…-loop-decoupling-design.md`) and plan flag as open or risky. Each gives the peer precedent + a co recommendation.

### OQ-4 — Subagent structured output → **opencode is a direct precedent for option (b)**
opencode's `generateObject` enforces structured output by registering the schema as an **output tool and forcing `toolChoice`** to it: `tools: { [GENERATE_OBJECT_TOOL_NAME]: tool }, toolChoice: ToolChoice.named(GENERATE_OBJECT_TOOL_NAME)` (`packages/llm/src/llm.ts:116-119`), then decoding the forced call's input against the schema and erroring if the model didn't call it (`llm.ts:120-129`). It does **not** parse free-text JSON. This is exactly co's OQ-4 option (b) (output-tool + detect its call), and it confirms the plan's CD-m-1 inversion: the parity-preserving mechanism is the forced output tool, not text-parse. hermes has no explicit structured-output enforcement (subagents aren't a hermes concept the same way). **Recommendation:** OQ-4 → option (b), with opencode `llm.ts:116-129` as the peer template; A/B against the dream-reviewer evals as the plan already states.

### Subagent engagement → **agent-as-tool, via the common tool protocol (4-peer survey, 2026-06-25)**
**Principle (confirmed):** from an agent's perspective there is **no "subagent" in its capability surface — only tools.** A task agent is reached by the parent emitting an ordinary tool call that the normal dispatch machinery runs; the child's result returns as a normal tool-result message into the parent's history. There is no separate delegation/service surface the model sees. Every peer that *has* delegation implements it this exact way; the one that lacks it has no delegation surface at all (not an alternative one):

| Peer | Engaged as tool? | Mechanism (file:line) |
|---|---|---|
| **hermes** | **Yes** | `delegate_task` is a registered tool (`tools/delegate_tool.py:3179-3198`, toolset `toolsets.py:238-241`); emitted as a normal tool call, routed inside the standard dispatch loop (`agent/tool_executor.py:1174-1209`), child built via `_build_child_agent` reusing the same loop, result returned as a tool-result message (`tool_executor.py:825`). |
| **openclaw** | **Yes** | `sessions_spawn` is a built-in tool (`src/agents/tools/sessions-spawn-tool.ts:253-516`); executed by the common `executePreparedToolCall` (`packages/agent-core/src/agent-loop.ts:929-932`), result fed back as a `ToolResultMessage` into parent history (`agent-loop.ts:351-364`). |
| **codex** | **Yes** | `spawn_agent` is a registered `ToolExecutor` (`codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs:27-37`); dispatched through the unified `ToolRouter` like any tool (`tools/router.rs:210-239`), child runs a full independent session (`codex_delegate.rs:69-139`), `SpawnAgentResult: ToolOutput` returns into parent history (`spawn.rs:258-274`). |
| **opencode** | **No delegation yet** | No agent/spawn/task tool exists; "subagent" is only a UI-visibility mode flag (`packages/core/src/agent.ts:67`), the `skill` tool is pure content-injection (`tool/skill.ts:62-96`), and `task` is an explicit *un-implemented* builtin TODO (`tool/builtins.ts:27`). When built it is slated as a **tool** — same shape, just absent today. |

**So 3/3 peers with delegation expose it purely as a tool through the common protocol; the 4th's placeholder is also a tool.** This is the strongest possible backing for the principle: agent-as-tool is the convergent (and only) design.

**co mapping (partial today):**
- ✅ **Protocol-sharing half is built.** co's owned subagent driver runs the child through the *same* tool surface and dispatch as the orchestrator — `run_standalone_owned` wires `_build_subagent_toolset` → `_CallSeamToolset` and routes through the shared `dispatch_tools` / `build_tool_defs` (`co_cli/agent/loop.py:360-381,395-499`). A subagent's own tools are not a separate path.
- ❌ **Engaged-as-tool half is NOT built.** co has no orchestrator-emitted agent tool today — task agents are invoked only by **daemons** via a direct `run_standalone(spec, deps, prompt)` call (`daemons/dream/_reviewer.py:122,141`, `_housekeeping.py:652`), not by the model emitting a tool call mid-turn. The infrastructure for it exists (`fork_deps` + `agent_depth` recursion guard, `deps.py:406-447`; `TaskAgentSpec` docstring already names "in-turn delegation"), but no `delegate`/`spawn_agent`-style tool is registered.
- **Direction:** to satisfy the principle in-turn, co would register a single delegation tool (parent emits it → `dispatch_tools` runs it → it forks deps and drives the child through `run_standalone_owned` → returns the child's output as the tool result). This is **not** part of the behavior-preserving cutover (the graph path has no such tool either) — it is a post-milestone capability that owning the loop makes clean, with hermes/openclaw/codex as line-for-line templates.

### OQ-6 — History-processor relocation → **no peer has co's pipeline; placement = "before the call"**
This remains the heaviest, most co-specific subsystem — confirmed by absence: **neither peer runs a multi-stage processor chain.** Both compact in a **single summarize step**:
- opencode: `compactIfNeeded` runs **before** the model call (`llm.ts:208`), a single summarization (`compaction.ts`), re-entering the turn via a thrown `TurnTransitionError`.
- hermes: `should_compress(tokens)` runs **after** the model call at the end of the outer iteration (`conversation_loop.py:4182-4192`), a single `_compress_context` call.
**Recommendation for OQ-6:** co's design (run the 5-processor chain **before** assembling each request) matches opencode's *placement* and is the correct choice (compact before you spend tokens on the call). The 5-stage *pipeline* itself is genuinely co-unique — there is no peer template to copy, so this is the section to test hardest. Two concrete borrows from the survey: (a) put the **fill-unanswered-tool_ids stub injection (decision 5) into this same preflight**, every step, per hermes's relocation to a pre-call safety net (`agent_runtime_helpers.py:2057`); (b) port `_clean_message_history` normalization (CD-m-2) in the same place — hermes's pre-call sanitizer (`conversation_loop.py:828`) is its analogue of exactly that normalization step.

### OQ-7 — Mixed text + tool-call → **both peers render+keep; hermes offers the double-render fix**
Covered as convergent decision 7. The design's lean (render+keep) is peer-validated on both sides. The design's *open risk* — "double-rendering if the model repeats the text after tool results" — has a concrete peer mitigation co can adopt: **hermes's housekeeping-mute** (`conversation_loop.py:4020-4046`) suppresses the inline narration only when every tool call in the turn is housekeeping, and stashes the text as `_last_content_with_tools` to surface if the post-tool turn comes back empty. **Recommendation:** ship render+keep as designed; if the groundedness/daily-chat evals show double-render, port hermes's housekeeping-set heuristic rather than reverting to discard.

### OQ-8 — Instruction injection cadence → **decided + built (was D3)**
Resolved on the hermes shape: cached static system prompt + per-step dynamic instructions kept *after* the cached block, for Ollama prompt-cache stability (cite `conversation_loop.py:798-809`). Built on the owned-loop path and documented in `core-loop.md` (parity) — no longer open.

### Approval under parallel dispatch → **co's pre-fan-out sequencing is a co-INNOVATION, not a borrow**
The design (§6.5) wants to *pre-prompt approval-required calls sequentially **before** the parallel fan-out*. Re-verification shows **neither peer does this**:
- hermes: dispatch is concurrent (`_should_parallelize_tool_batch`) but each tool's approval **blocks inside its own execution** on a `threading.Event` (`tools/approval.py:1352`, event at `:695`); there is no per-message pre-sequencing and **no per-message parallel cap**.
- opencode: tool fibers run **uncapped in parallel** (`llm.ts:177,264`, `FiberSet` with no semaphore); each `question.ask()` awaits its own reply concurrently; a rejection clears **all** fibers and interrupts (`llm.ts:142-143,290-293`).
So co's "**sequential pre-prompt before fan-out**" has **no peer precedent** — it is co's own design to avoid racing terminal prompts under its ≤3 cap. Only the *reject-halts-the-step* half is a peer pattern (opencode `llm.ts:290-293`). **Recommendation:** keep the design's pre-sequencing (it's the right call for a single terminal), but flag it as co-original in the design (don't cite "peer pattern" for the sequencing — only for the reject-halt) so its correctness gets first-principles scrutiny, not borrowed confidence. This is design risk #2 and the survey gives co no shortcut on it.

### finish_reason `length` handling → **co's length-retry is beyond both peers**
Neither peer does co's length-continuation retry: opencode treats `length` the same as `stop` (no special handling, just exits — the next turn may compact); hermes maps `length` to the typed vocab but has no continuation-retry on it. co's design §6.6 (`_is_reasoning_overflow` predicate + `_length_retry_settings` boost-and-re-enter) is a **co-specific small-model defense richer than both peers** — validate it as beyond-parity, not parity, and keep it.

## Do NOT borrow

- **hermes's ~4000-line loop body** — ~90% multi-provider + weak-open-model + gateway scar tissue (codex Responses, Bedrock, Anthropic thinking-signature replay, credential rotation, Nous-Portal rate breakers, GitHub-Models caps, Z.AI backoff overrides). co is local-first with 1–2 backends. Borrow the ~200-line skeleton, leave the body. (Note the loop has since been *extracted* from the old god-file `run_agent.py` into `conversation_loop.py` + `turn_context.py` + `turn_finalizer.py` — the modularization is worth noting but the body is still not co's to copy.)
- **hermes's per-turn counters on the agent object** (`agent._invalid_tool_retries`, `agent._empty_content_retries`) — drift bugs; use a reconstructed `TurnState`.
- **hermes's `tc.function.name` OpenAI-shape access / backward-compat shims** — co enforces zero-backward-compat; use flat `ToolCallPart`.
- **hermes's gateway threading-Event approval queue / dual history+trajectory persistence / dual streaming+non-streaming paths / fuzzy tool-name repair** — multi-session-server + eval-training + many-provider machinery co doesn't have.
- **opencode's Effect runtime** — `Effect.gen/Stream/FiberSet/Cause`, `Effect.die`-as-control-flow, compaction-via-thrown-`TurnTransitionError`. In Python use `asyncio` + `CancelledError` + plain control flow. (co's overflow recovery should be a plain `except`/retry, not a thrown transition sentinel.)
- **opencode's in-house multi-protocol library** (`packages/llm/src/protocols/*`, route machinery) — justified by their many-provider mandate. **co does not build this** — co keeps pydantic-ai's `direct` + message types + provider classes as its thin boundary (see below).
- **opencode's uncapped parallel tool fan-out** (`llm.ts:177,264`, no semaphore; their docstring still TODOs the missing bound). co's per-response flood cap (=3) is the *better* small-model design — keep it.
- **opencode V2's dropped tool-arg repair** — confirmed at HEAD: no repair mechanism, JSON parse failures are fatal. co should keep its JSON-repair ladder (decision 6).

## The boundary, mapped to co (the key takeaway)

opencode built a new protocol library to get a thin `stream()` boundary. **co does not need to** — `pydantic_ai.direct.model_request_stream(model, messages, model_request_parameters=ModelRequestParameters(function_tools=[…]))` *is* that boundary, already shipped and already used by co's helper calls. So co's migration is **strictly smaller** than opencode's:

| Layer | Keep (pydantic-ai as library) | Drop (the graph) |
|---|---|---|
| Provider/transport | `OpenAIChatModel`/`OllamaProvider`, `GoogleModel`; `direct.model_request[_stream]` | — |
| Message model | `ModelMessage`/`ModelResponse`/`*Part`, `ModelMessagesTypeAdapter` (persistence/compaction/observability unchanged) | — |
| Tool schema | `ToolDefinition` (+ co's `@agent_tool` metadata; `FunctionToolset` kept **as schema generator only**, OQ-3) | `FunctionToolset`/`WrapperToolset` *dispatch* wrappers |
| Settings/usage/exc | `ModelSettings`, `RequestUsage`, `ModelHTTPError` | — |
| **Loop** | — | `Agent`, `agent.iter()`, the graph, `output_type=[str,DeferredToolRequests]` |
| **Cross-cut wrappers** | behaviors kept, **inlined into the loop** | `SurrogateRecoveryModel`, `_CallSeamToolset`, `_RepairingStreamedResponse` |
| **Approval** | co's tool/subject/auto-approval logic | `DeferredToolRequests/Results/ToolApproved` suspend-resume → inline prompt |

**What owning the loop dissolves** (the S1–S6 census from the `sdk-coupling-cleanup` plan): S1 (stream-repair coupling — repair becomes inline on the assembled response co already drives), S2 (tool-cap diffuseness — cap at the natural loop boundary), S3/S6 (synthetic `RunContext` — co passes `deps` directly), S4 (reasoning-overflow string-match — typed finish_reason branch). The wrappers (`SurrogateRecoveryModel`, `_CallSeamToolset`) become inline linear loop code.

**What it does NOT solve:** the `ModelMessage` type coupling remains (woven through persistence/compaction/observability). Owning the loop is *loop* independence, not SDK independence — pydantic-ai stays as a provider+message library.

## Usage accumulation (minor — peers diverge, low stakes for co)

- hermes accumulates token counts on long-lived `agent.session_*` counters across calls (`conversation_loop.py:1846-1854`), persisting to the session DB each call.
- opencode accumulates **per-step only**, publishing `SessionEvent.Step.Ended` with that step's tokens (`publish-llm-event.ts`); the caller aggregates.

co's design §6.10 (running total on `TurnState`, recorded once in `run_turn`'s `finally`) is a clean middle path and needs no change — noted only for completeness.
