# RESEARCH — Agentic loop peer survey (hermes-agent, opencode; +codex, openclaw for delegation)

A durable design reference on how peer agent systems structure their **turn loop** — the
control flow that turns a user message into an answer by streaming model calls, dispatching
tools, and deciding when to stop. Distilled from a code-first read of four peers: the loop
thesis and mechanics come from **hermes-agent** (Python, hand-written loop) and **opencode**
(TypeScript, owned loop over a thin provider boundary); the delegation survey adds **codex**
and **openclaw**. Peer `file:line` citations are as of the 2026-06-25 peer HEADs
(`hermes-agent d6269da7f`, `opencode 20fd32359`).

This is a *design record*, not a task tracker: it captures the convergent principles worth
holding to when designing or evolving an agent loop, and maps co's own loop design against
them.

## The thesis — the loop is owned application code

The agent loop is control flow the application owns, not a behavior delegated to a framework.
Both surveyed loop-peers structure it as a plain iteration over a thin, single-call model
boundary:

- **hermes-agent** hand-writes the loop on the OpenAI SDK (`client.chat.completions.create`) —
  a `while` over counters (`agent/conversation_loop.py:495`; outer `while` `:589`).
- **opencode** runs an owned loop over an in-house `stream(request)` provider boundary
  (`packages/core/src/session/runner/llm.ts:225`).

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

The provider layer is a **codec + transport**: one model call in, typed events out. It never
loops and never executes tools. Loop, dispatch, continuation, approval, error recovery, and
compaction all live above it. This is the single most load-bearing design choice — every
principle below follows from having the loop body under application control, where it can be
read, instrumented, and specialized (small-model defenses, local-first constraints) rather
than configured through a framework's extension points.

**co maps here.** co drives one model turn through `model_turn` over
`pydantic_ai.direct.model_request_stream`, keeping pydantic-ai as a *provider + message
library* (provider classes, `ModelMessage`/`*Part` types, `ToolDefinition` schema,
`ModelSettings`/`RequestUsage`/`ModelHTTPError`) while owning the loop, dispatch, approval, and
recovery itself. `pydantic_ai.direct.model_request_stream(...)` *is* the thin `stream()`
boundary opencode had to build a whole protocol library to obtain.

## Convergent design decisions

Both loop-peers independently converge on these. They are the durable checklist for an agent
loop — each is a decision that, made wrong, produces a recurring class of bug.

1. **Termination = "did the model emit a tool call?"** — not `finish_reason`. hermes
   (`conversation_loop.py:3834`; no-tool terminal `:4200-4470`); opencode `needsContinuation`
   (`llm.ts:241,337`). A turn ends when the model answers with prose and no tool call.
   `finish_reason` is a diagnostic, not the termination predicate.
2. **`finish_reason` normalized to a small typed vocabulary at the boundary.** hermes
   `map_finish_reason` (`transports/types.py:106,167`); opencode typed union
   (`schema/ids.ts:39`, mappers `protocols/openai-chat.ts:370,449-456`). Branch on typed
   values, never a raw provider string — string-matching a provider's `finish_reason` at the
   decision point is a portability and correctness hazard.
3. **Context overflow classified once, typed, at the boundary** — not string-matched where the
   decision is made. opencode `isContextOverflowFailure` (`provider-error.ts:29-32`).
4. **Typed error classification with action-hint flags.** hermes `FailoverReason` +
   `ClassifiedError` (`error_classifier.py:24,69`) carrying `retryable`/`should_compress`; the
   recovery path reads the flags instead of re-deriving the class. One classification site, one
   source of truth for "what do we do about this error."
5. **Fill unanswered `tool_call_id`s with stub results before *every* model call**, so the
   tool-call ↔ tool-result pairing stays valid. hermes `_sanitize_api_messages`
   (`agent_runtime_helpers.py:2057-2076`, called `:828`) — a pre-call safety net run every
   step, catching orphans from session load and compaction, not only mid-turn exceptions. An
   unpaired tool-call id is a provider 400; making the pairing an invariant of every request
   removes the whole failure class.
6. **A JSON-arg repair ladder for malformed local-model tool args**, with a last-resort `"{}"`
   so a bad arg never crashes the session. hermes (`message_sanitization.py:185-279`).
   (opencode's V2 dropped its repair and regressed — a JSON parse failure is fatal there. For
   a local-model target, keep the repair.)
7. **Mixed response (text AND tool calls) → keep and render the text, then execute the
   tools.** opencode streams text then settles tools; hermes keeps the text
   (`conversation_loop.py:4020-4046`) and mutes inline narration only when *every* tool call in
   the turn is housekeeping (`memory`/`todo`/`skill_manage`/`session_search`) — its guard
   against double-rendering.
8. **History = the simplest representation that works.** hermes: raw OpenAI-shaped
   `list[dict]`, with ephemeral scaffolding tagged by `_`-prefixed keys and popped before the
   durable answer is appended (`conversation_loop.py:4521-4530`).
9. **Two message models with explicit translators** (opencode): a durable `SessionMessage` ↔ a
   thin wire `Message`/`ContentPart` (`to-llm-message.ts:157`,
   `publish-llm-event.ts:54-423`). Persist rich, send lean.
10. **Explicit per-turn state, reconstructed each turn** — not per-turn counters living on the
    long-lived agent object (hermes's `agent._invalid_tool_retries` etc. are its worst drift
    bug). A single grep-able turn-exit reason beats a scatter of flags whose lifetimes diverge.

**Design reading.** Decisions 1–4 are about *typing the boundary*: turn the provider's loose
strings and exceptions into a small closed vocabulary the loop branches on. Decisions 5–6 are
*pre-call invariants*: make every request well-formed by construction rather than reacting to
provider rejections. Decisions 7–10 are *state discipline*: one representation, one writer, one
lifetime per concept. A loop that gets all three families right is stable across providers and
model tiers; most loop bugs trace to violating one of them.

## Guaranteeing a terminal answer on a ceiling exit

An owned loop has ceilings that stop it *before* the model naturally answers — a total
model-request cap, and a consecutive-over-cap tool-flood breaker. A loop that simply returns at
a ceiling leaves the user with no synthesized answer at exactly the moment a thrashing (often
small) model most owes one: "here's what I found / where I got stuck." The gathered context is
discarded unharvested; because nothing crashes, the degraded turn never surfaces as a bug.

**The convergent design: on a ceiling exit, make one more model call with tools removed, asking
for a written summary of the work so far.** Stripping the tools is what forces the model to
synthesize from the context it already gathered instead of reaching for yet another tool — and
the answer is usually latent in that context already.

- **opencode:** on the last step (`llm.ts:195` `isLastStep`) it strips tools (`:196`), forces
  `toolChoice:"none"` (`:206`), and injects a "tools are disabled, respond with text only"
  prompt (`max-steps.ts:1-16`); a tool call after that is failed (`llm.ts:237-239`).
- **hermes:** `handle_max_iterations` (`chat_completion_helpers.py:1338`, called from
  `turn_finalizer.py:53-70`) appends a "provide a final response summarizing what you've found,
  without calling any more tools" user turn (`:1342-1347`) and makes one **toolless** call
  (tools popped on the codex path `:1436`, omitted on the chat path `:1442-1445`). The comment
  states the design plainly: "one extra API call with tools stripped."

Two refinements the peers teach:

- **Preflight-first is load-bearing.** The forced call fires right after an abnormal stop, when
  history can carry orphaned tool-call ids the provider 400s on — run the same
  tool-call-pairing sanitizer every step runs (decision 5) *before* the forced call (hermes
  runs `_sanitize_api_messages` first, `:1390`).
- **Keep a synthesized fallback for when even the forced call errors** — the true "never show
  nothing" floor (hermes `chat_completion_helpers.py:1562`): fall back to the last assistant
  text, else a canned message. The forced call is a strict floor-raise — a better common case
  with the same worst case — never a new failure mode.

**co maps here.** co runs this on both ceiling exits (model-request cap, tool-flood
hard-stop): a preflighted, toolless summary call (empty `function_tools` ⇒ text-only output),
streamed to the user, returning the synthesized answer; on any provider error or stall inside
it, it falls back to the last assistant text or a canned message. A user interrupt during the
call is deliberately not swallowed — it propagates to the turn's interrupt handler. This makes
a ceiling a *graceful wrap-up* rather than an error.

## Subagent engagement — agent-as-tool via the common tool protocol

From an agent's view there is **no "subagent" in its capability surface — only tools.** A task
agent is reached by the parent emitting an ordinary tool call the normal dispatch machinery
runs; the child's result returns as a normal tool-result message. Every peer with delegation
does exactly this:

| Peer | As tool? | Mechanism (file:line) |
|---|---|---|
| **hermes** | Yes | `delegate_task` registered tool (`tools/delegate_tool.py:3179-3198`); dispatched in the standard loop (`tool_executor.py:1174-1209`), child via `_build_child_agent`, result as tool-result (`tool_executor.py:825`). |
| **openclaw** | Yes | `sessions_spawn` built-in tool (`sessions-spawn-tool.ts:253-516`); via common `executePreparedToolCall` (`agent-loop.ts:929-932`), result as `ToolResultMessage` (`:351-364`). |
| **codex** | Yes | `spawn_agent` registered `ToolExecutor` (`multi_agents_v2/spawn.rs:27-37`); via unified `ToolRouter` (`router.rs:210-239`), child runs a full session (`codex_delegate.rs:69-139`), returns into parent history (`spawn.rs:258-274`). |
| **opencode** | No delegation yet | `task` is an un-implemented builtin TODO (`tool/builtins.ts:27`); when built, slated as a tool. |

**co** registers a `delegate` tool (`co_cli/tools/system/delegate.py`) — the parent emits it →
`dispatch_tools` runs it → `run_delegate` forks deps and drives the delegated agent through the
owned subagent driver → the delegated agent's distilled summary returns as the tool result
(`co_cli/agent/delegation.py`). Daemons invoke task agents directly via `run_standalone`; the
model-emitted delegation path is the same machinery reached through an ordinary tool call.

## Delegation sub-axes — surface / approval / depth / return

| Axis | hermes | openclaw | codex | **co** |
|---|---|---|---|---|
| **Child tool surface** | inherit parent ∩ − blocklist (`delegate_tool.py:1066-1082`, blocklist `44-54`) | inherit allowlist − role/depth deny (`agent-tools.policy.ts:84-146`) | inherit = parent, full surface (`multi_agents_common.rs:204-211`) | **inherit orchestrator visibility − blocklist** — full native+MCP, DEFERRED self-loaded via `tool_view`, minus `_DELEGATE_AGENT_BLOCKLIST = {delegate}` (`delegation.py:48`; `DELEGATE_AGENT_SPEC` `:124`) |
| **Approval of a child's gated call** | auto-deny default (threadpool, no user channel) (`delegate_tool.py:57-112`) | sandbox + tool policy, silent fail (`agent-tools.policy.ts:115-146`) | **propagate to human**, child inherits `approval_policy` (`codex_delegate.rs:267-312`) | **propagate to human** — surfaces on the parent `Frontend`; headless → auto-deny |
| **Nesting / depth** | configurable `max_spawn_depth`, default 1 | configurable, default 1 (`agent-limits.ts:13`) | configurable `agent_max_depth`; V2 unbounded | **hard 1, no config** — `DELEGATE_DEPTH_CAP` + `agent_depth` guard (`delegation.py:162`) + `delegate` surface-exclusion |
| **Return to parent** | summary only (`delegate_tool.py:1786,1850`) | latest output only (`subagent-announce-output.ts:302-414`) | full event stream minus approval mechanics (`codex_delegate.rs:289-401`) | summary only — distilled summary as the tool result (`delegate.py:5`) |

**Reads:**

- **Write-capable child, approval propagated to the human — co = codex.** co reaches the parent
  `Frontend` from inside the child, so the trust boundary holds across the delegation seam.
- **Surface construction — all four converge on inherit − blocklist.** co's general delegated
  agent gets the orchestrator's own visibility surface minus a one-tool `{delegate}` blocklist.
  co's twist: durable/outward tools become *reachable* but stay *gated* by approval propagation
  (the boundary hermes/openclaw lack), and DEFERRED tools stay lazy-loaded for small-model
  schema leanness.
- **Depth — co most conservative** (hard 1; peers default 1 but expose a config ceiling).
  **Return shape — co with the 2/3 majority** (summary-only): the parent gets a distilled
  result, not the child's raw event stream.

## Anti-patterns — do NOT borrow

The peers' loops carry scar tissue from constraints co does not share (many providers, gateway
infrastructure, cloud-first). Borrow the skeleton and the decisions above; leave these.

- **hermes's ~4000-line loop body** — ~90% multi-provider + gateway machinery (Bedrock,
  thinking-signature replay, credential rotation, rate breakers). The reusable core is the
  ~200-line skeleton, not the body.
- **hermes's per-turn counters on the long-lived agent object** — a drift-bug source; use a
  reconstructed per-turn state object (decision 10).
- **hermes's gateway threading-`Event` approval queue / dual persistence / dual streaming
  paths / fuzzy tool-name repair** — multi-server, many-provider machinery a local-first single
  loop does not need.
- **opencode's Effect runtime** (`Effect.gen`/`Stream`/`FiberSet`, `Effect.die`-as-control-flow,
  compaction-via-thrown-`TurnTransitionError`) — in Python, plain `asyncio` + `CancelledError` +
  ordinary control flow is clearer.
- **opencode's uncapped parallel tool fan-out** (`llm.ts:177,264`) — a per-response flood cap
  is the better small-model design (a small model that emits ten tool calls at once is usually
  thrashing, not parallelizing).
- **opencode V2's dropped tool-arg repair** — JSON parse failures are fatal there; keep the
  repair ladder (decision 6).

## The model boundary

opencode built an in-house multi-protocol library (`packages/llm/src/protocols/*`) to get a
thin `stream()` boundary. A loop built on pydantic-ai does not need to: `direct.model_request`
/ `direct.model_request_stream` already *is* that boundary. The design pattern to preserve, on
whatever substrate:

| Layer | The thin boundary provides | The loop owns |
|---|---|---|
| Provider / transport | one streamed model call, typed events out | when to call, with what messages/tools/settings |
| Message model | typed request/response parts | history representation, compaction, persistence |
| Tool schema | tool-definition types (schema only) | dispatch, cap, approval, result synthesis |
| Settings / usage / errors | typed settings, usage counts, provider exceptions | classification, recovery, retry, budgets |
| **Loop / termination / approval** | — | **all of it** |

The library is a codec, message model, and provider catalog; the loop is the application. Keep
the split at exactly that line.
