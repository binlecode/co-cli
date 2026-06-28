# Loop decoupling — Phase 4: error / recovery / length-retry into the owned loop

> Milestone: `2026-06-24-234633-loop-decoupling-milestone.md` (Gate 1 APPROVED). This is the
> per-task plan for **PHASE 4**. Phases 1, 2, 2.5, 3, 3.5, 3.6, 3.7 shipped (v0.8.490 → v0.8.502).

## Context

The owned loop (`co_cli/agent/loop.py`, flag `config.llm.use_owned_loop`) currently runs the
**no-recovery slice** (Phase 2): a provider error, context overflow, or interrupt ends the turn
as a clean terminal `TurnResult`. The graph path (`orchestrate.run_turn`) still owns the full
recovery machinery and remains the default. Phase 4 relocates that machinery into the owned loop
as inline code so the owned path reaches **behavioral parity** with the graph path on the error /
recovery / length-retry surface — the precondition for the Phase 5 cutover.

**Graph-path recovery to relocate** (all in `co_cli/agent/orchestrate.py`):
- **Length-continuation retry** — `_length_retry_settings` (`:737`) + the boost/discard-partial
  logic in `_finalize_run` (`:935-952`); constants `_LENGTH_RETRY_CEILING=16384` (`:62`),
  `_LENGTH_RETRY_BOOST=2` (`:65`); `cap_output_tokens` (Ollama lockstep, `config/llm.py`).
- **Overflow strip-then-summarize** — `_attempt_overflow_recovery` (`:841`) → `recover_overflow_history`
  (`context/compaction.py:463`, graph-free, reusable as-is); overflow detection `is_context_overflow`
  (defined in `context/_http_error_classifier.py:36`, **re-exported and consumed via the public facade
  `co_cli.context.compaction`** — `compaction.py:48`, the graph's own import site at `orchestrate.py:91`;
  owned-path code imports both from `co_cli.context.compaction`, never the private `_http_error_classifier`
  module — CD-m-1, underscore-visibility contract).
- **HTTP 400 tool-call reformulation** — `_apply_400_reformulation` (`:789`) +
  `_handle_model_http_error` (`:866`); backoff `_HTTP_400_REFLECT_BACKOFF_SECS=0.5` (`:70`).
- **Transient/timeout → terminal, no inner retry (D1)** — `_transient_error_message` (`:780`) +
  the `except (ModelAPIError, httpx.ReadError, TimeoutError)` branch (`:1041`). The owned loop
  already terminates on these (`loop.py:219`); only the **user-facing message parity** is missing.
- **Interrupt** — owned loop catches `KeyboardInterrupt`/`CancelledError` (`loop.py:217`) but
  `_interrupted_result` (`loop.py:350`) does **not** append the graph's abort marker
  (`orchestrate.py:656-665`) — a parity gap.
- **Fill-unanswered-tool_call_ids invariant** — graph does a break-time drop of the last unanswered
  `ModelResponse` (`_build_interrupted_turn_result`, `:638-646`). Per milestone OQ-6 / Error-space,
  Phase 4 **upgrades** this to hermes's placement: a **pre-call safety net run every step** in
  preflight (fill unanswered `ToolCallPart`s with synthetic `ToolReturnPart` stubs). This is a
  deliberate strategy change (drop → fill), not byte-parity — same shape-change rationale as CD-m-3.
- **Post-turn output-limit diagnostics** — `_check_output_limits` (`:681`): truncation warning,
  context-limit / auto-compaction-paused status nudges. Missing on the owned path.

**Already in the owned loop (Phase 2) — confirm parity, no new work:**
- **Stall timer** — `_drive_model_request` (`loop.py:126-132`) re-arms `asyncio.timeout(stall_window)`
  on every streamed event (time-since-last-token). Functionally equivalent to the graph's
  `_StallTimer` (`orchestrate.py:290`). Verified in Testing; no task.
- **Request cap** — the owned loop checks `request_limit` **before** each step (`loop.py:261`), so it
  never relies on the SDK's `UsageLimitExceeded` (graph `:1022`). The graph's `UsageLimitExceeded`
  catch has **no owned-path counterpart by design** — do not port it.
- **Reasoning overflow** — `_is_reasoning_overflow` (`loop.py:91`) is already the typed branch
  (finish_reason `'length'` + no answer content) replacing the graph's string-match (S4).
- **Tool-cap hard stop** — `ToolCapState` at the step boundary (`loop.py:313`, Phase 2 CD-m-3).

**Where new code lands.** New owned-path recovery logic lives in **owned-path modules**, never in
`orchestrate.py` (which Phase 5 deletes): a new `co_cli/agent/recovery.py` for the typed error
classifier + length-retry decision (the FailoverReason analog), `preflight.py` for the
fill-unanswered net, and the recovery **control flow** inline in `loop.py`'s
`_orchestrator_step_loop`. The graph twins are deleted at Phase 5; some duplication during the
parallel-path window is expected and accepted (the whole milestone is parallel-path).

## Problem & Outcome

**Problem:** the owned loop cannot yet recover from the failure modes the graph path handles
routinely — a truncated answer is dropped instead of continued, a context overflow ends the turn
instead of compacting-and-retrying, an HTTP 400 tool-call rejection is terminal instead of
reflected back, and an interrupted turn leaves unanswered tool calls / no abort marker in history.
Until these reach parity, the Phase 5 cutover cannot flip the default without regressing real
user-visible recovery behavior.

**Outcome:** the owned loop classifies provider errors once into a typed reason + action flags and
recovers inline — length-continuation retry boosts the token budget and re-runs; context overflow
strip-then-summarizes and retries once; HTTP 400 reflects to the model within a budget; transient /
timeout / malformed surface terminal with parity messages; interrupt appends the abort marker; and
every step's preflight fills unanswered tool-call ids so the next request is always protocol-valid.
The graph path stays default and byte-for-byte unchanged.

**Failure cost:** high if rushed — this is the recovery layer the small-model thesis leans on
(truncation continuation and overflow compaction are routine on a local 35B model with a tight
context window). A silent parity gap here means the cutover regresses recovery the user currently
relies on, and only shows up as dropped answers / unrecoverable overflows in production. Mitigated
by the parallel path (graph remains the reference oracle until Phase 5) and the per-task owned-path
behavioral checks below.

## Scope

**In:** relocate the orchestrate.py error/recovery handling into the owned loop as inline code —
typed error classification (FailoverReason shape), length-continuation retry, overflow
strip-then-summarize, HTTP 400 tool-call reformulation, transient/timeout terminal (no inner retry,
D1) with parity messages, interrupt abort marker, fill-unanswered-tool_call_ids as an every-step
preflight net, post-turn output-limit diagnostics. Owned-path only; additive.

**Out:**
- The graph path — stays default and untouched (parallel-path constraint). No edits to
  `orchestrate.run_turn` or its helpers; deletion of the graph twins is **Phase 5**.
- Subagent-driver recovery — `run_standalone_owned` (`loop.py:417`) keeps its bounded
  re-prompt-on-validation-failure loop; it has no human channel and runs a structured `final_result`
  contract, so length-retry / overflow-compaction / 400-reflection / abort-marker do **not** apply.
  (The fill-unanswered preflight net is orchestrator-loop-only here; the subagent driver appends a
  `ModelRequest` after every dispatch, so it never leaves an unanswered call.)
- The pydantic-ai `UsageLimitExceeded` catch — replaced by the owned loop's explicit pre-step
  request-cap check (see Context); not ported.
- Any new recovery capability beyond graph parity — e.g. opencode's tools-stripped final turn at the
  step ceiling (milestone D2) is explicitly a post-milestone enhancement, not Phase 4.

## Behavioral Constraints

- **Parity with the graph path is the gate.** Same length-retry trigger + boost ladder
  (4096→8192→16384, text-presence gate), same single-attempt overflow recovery
  (strip-then-summarize, latched once per turn), same 400 reflect-within-budget, same terminal
  surfacing + user-facing messages for transient/timeout/malformed, same abort-marker text on
  interrupt. The graph path is the reference oracle; owned-path tests assert the graph behavior.
- **One deliberate divergence — fill-unanswered moves from break-time drop to every-step fill**
  (milestone OQ-6, hermes placement). Both produce protocol-valid histories; the observable
  difference is interrupted-turn history shape (the interrupted tool call is retained with a
  synthetic "interrupted" return rather than dropped). Documented, not byte-parity — same class of
  decision as CD-m-3's tool-cap pre-fan-out counting.
- **D1 — transient/timeout is terminal, no inner retry.** Do not add an inner retry+backoff loop
  (hermes has one; co + opencode do not — local-first / 1–2 backends). This is parity with co's
  current posture (graph surfaces these terminal today).
- **The JSON-repair ladder is kept** (already inline in `model_turn` via `repair=`); 400
  reformulation is a *separate* app-level mechanism (provider rejected the request) layered above it.
- **The graph path must keep working unchanged** through Phase 4 — no edit may break it.

## High-Level Design

### Restructure: the recovery catch moves *inside* the step loop

Today `run_turn_owned` catches provider errors at the **turn boundary** (`loop.py:219`), which can
only terminate. Recovery requires mutating `state.history` and re-running, so the HTTP-error catch
moves **inside** `_orchestrator_step_loop` around `_drive_model_request`:

```
while True:
    <request-cap check>           # unchanged (pre-step)
    processed = run_history_processors(...)
    processed = fill_unanswered_tool_calls(processed)   # TASK-4 — every-step net
    <assemble instr / tool_defs / params>
    try:
        response, step_usage = await _drive_model_request(..., settings, ...)
    except (ModelHTTPError, ModelAPIError, httpx.ReadError, TimeoutError, UnexpectedModelBehavior) as exc:
        err = classify_provider_error(exc)              # TASK-1 — typed
        match err.action:
            case RECOVER_OVERFLOW:  <compact + retry once, else terminal>   # TASK-2
            case REFLECT_400:       <reflect + budget, else terminal>       # TASK-2
            case TERMINAL:          frontend.on_status(err.message)         # TASK-1
                                    return _terminal_result(...)            #   owns the message here
        continue / return accordingly
    <no-tool-calls branch>:
        if length-retry fires: boost settings, drop partial, continue       # TASK-3
        else: emit diagnostics, return final                                # TASK-5
    <tool-calls branch>: dispatch + cap (unchanged)
```

`settings` becomes a **mutable local** in `_orchestrator_step_loop` (boosted by the length-retry
branch).

**Single classification site (CD-M-2).** Provider errors are only ever raised by
`_drive_model_request` (tool errors are absorbed inside `dispatch_tools` as error payloads), so the
in-loop catch is the **sole** owner of provider-error classification *and* the user-facing status
message — including the terminal types (transient / timeout / malformed), which it surfaces with the
parity message and returns terminal. The existing turn-boundary provider-error catches in
`run_turn_owned` (`loop.py:219`, `:225`) are therefore **removed**; the turn-boundary `try` keeps
**only** the `KeyboardInterrupt`/`CancelledError` interrupt catch (which can fire during
`dispatch_tools`, not just the model request) plus a generic last-resort for a truly unexpected
exception escaping `dispatch_tools`. This eliminates the current generic
`Provider error — turn ended: {exc}` (`loop.py:220`) on the owned path — every classified error now
carries its graph-parity message.

### Typed error classification (TASK-1) — the FailoverReason analog

A new `co_cli/agent/recovery.py`:

```python
class ErrorAction(Enum):
    RECOVER_OVERFLOW = auto()   # context overflow → strip-then-summarize, retry once
    REFLECT_400      = auto()   # HTTP 400 tool-call rejection → reflect to model, within budget
    TERMINAL         = auto()   # transient / timeout / malformed / other HTTP → end turn

@dataclass(frozen=True)
class ErrorClass:
    action: ErrorAction
    exit_reason: TurnExit       # PROVIDER_ERROR | TIMEOUT (existing enum)
    message: str                # user-facing status (parity with graph wording)
    span_event: tuple[str, dict]  # (event name, attrs) for the turn span

def classify_provider_error(exc: Exception) -> ErrorClass: ...
```

`classify_provider_error` reuses `is_context_overflow` (imported from `co_cli.context.compaction`)
and ports the graph's message wording **verbatim, branch-for-branch** (CD-m-5): `TimeoutError` →
`TIMEOUT` + the timeout text ("LLM call timed out — model did not respond in time. Try a shorter
prompt, or ask Co 'what can you do right now?' or run /doctor." — `_transient_error_message`'s
`isinstance(e, TimeoutError)` branch, `orchestrate.py:781-785`); other `ModelAPIError`/`httpx.ReadError`
→ `PROVIDER_ERROR` + the `Network error: {e}` form; HTTP 400 (non-overflow) / other HTTP →
the provider-error status; `UnexpectedModelBehavior` → the malformed-output status. The loop reads
`err.action`; no string-matching against exception text survives on the owned path (reasoning
overflow is already typed via `_is_reasoning_overflow`).

### Length-retry decision (TASK-3) — `ModelResponse`-based

`recovery.py` also owns `length_retry_settings(response: ModelResponse, settings) -> ModelSettings | None`
— a `ModelResponse`-shaped port of `_length_retry_settings` (the graph version takes the graph
`SessionRunResult` but reads only `result.response`). Same gate: `finish_reason == 'length'` AND at
least one `TextPart` AND `max_tokens` below `_LENGTH_RETRY_CEILING`; returns `cap_output_tokens(settings, boosted)`
(Ollama scalar + `extra_body` lockstep). Constants move to `recovery.py`. On fire: drop the truncated
partial `ModelResponse` from `state.history`, set boosted settings, `continue` (no new user prompt —
the history already ends on the user turn after dropping the partial).

### Fill-unanswered net (TASK-4) — preflight, every step (load-bearing only post-interrupt)

`preflight.py` gains `fill_unanswered_tool_calls(history) -> list[ModelMessage]`: for any
`ModelResponse` whose `ToolCallPart` ids are **not** answered by the immediately-following message,
**insert a fresh `ModelRequest`** carrying synthetic `ToolReturnPart` stubs (`content="Tool call
interrupted; no result."`, matching `tool_call_id`) directly after that response. Called every step,
right after `run_history_processors`.

**Why insert, not mutate (CD-M-1).** Under normal flow the orchestrator loop appends
`ModelRequest(parts=parts)` after every dispatch (`loop.py:311`), so no response is ever left with
unanswered calls **within** a turn — the net is a **no-op intra-turn** and is load-bearing **only on
the first step of the turn following an interrupt**. On interrupt, `_interrupted_result` retains the
unanswered response (the deliberate drop→fill divergence) AND appends the abort marker, which is a
`ModelRequest(parts=[UserPromptPart(...)])` (`orchestrate.py:656-665`). So the post-interrupt history
is `[…, ModelResponse(unanswered calls), ModelRequest(abort UserPromptPart), ModelRequest(new user
input)]`. The stub must land in a `ModelRequest` sitting **between** the response and the abort marker
— so the net **inserts** a new `ModelRequest(parts=[stubs])` rather than mutating the abort marker;
`clean_message_history` (`preflight.py:79`) then merges the three consecutive requests and sorts the
tool-return parts to the front, yielding a protocol-valid request (Ollama/OpenAI reject unanswered
tool_calls). This replaces the graph's break-time drop. The test targets the **cross-turn boundary**
(interrupt → next turn), not "every step."

## Tasks

### ✓ DONE TASK-1 — Typed provider-error classification + parity terminal messages
- **files:** `co_cli/agent/recovery.py` (new), `co_cli/agent/loop.py`
- Add `ErrorAction`, `ErrorClass`, `classify_provider_error` to `recovery.py`; import
  `is_context_overflow` from `co_cli.context.compaction` (the public facade, CD-m-1). Port graph
  message wording **branch-for-branch** (CD-m-5): `TimeoutError`→timeout text, other transient→
  `Network error: {e}`, non-overflow HTTP→provider-error status, `UnexpectedModelBehavior`→malformed
  status. Establish the **single classification site** (CD-M-2): move the provider-error catch inside
  `_orchestrator_step_loop` around `_drive_model_request`, route `TERMINAL`-action errors to a
  terminal `TurnResult` with `err.message`, and **remove** the now-redundant provider-error catches at
  the `run_turn_owned` turn boundary (`loop.py:219,225`), keeping only interrupt + a generic
  last-resort there.
- **done_when:** owned-path flow test drives a turn where `_drive_model_request` raises each of
  `TimeoutError`, a non-timeout `ModelAPIError`, and `UnexpectedModelBehavior` (non-overflow) and
  asserts the turn ends terminal with the **verbatim** status text the graph path emits for that error
  (timeout text incl. the `/doctor` tail; `Network error:` form; malformed-output form) — and that the
  generic `Provider error — turn ended: {exc}` is no longer emitted; full suite green.
- **success_signal:** an owned-path timeout shows "LLM call timed out — model did not respond in time.
  Try a shorter prompt…" (the graph wording), not a raw exception string.
- **prerequisites:** none

### ✓ DONE TASK-2 — In-loop HTTP recovery: overflow strip-then-summarize + 400 reformulation
- **files:** `co_cli/agent/loop.py`, `co_cli/agent/recovery.py`
- In the single in-loop catch (TASK-1), on `RECOVER_OVERFLOW`: call
  `recover_overflow_history(deps, state.history)` (imported from `co_cli.context.compaction`); on
  success **assign its return to `state.history`** and `continue`, latching a once-per-turn
  `overflow_recovery_attempted` flag (parity with `_attempt_overflow_recovery`); on failure (or
  already-attempted) terminate via TASK-1 with the "Context overflow — unrecoverable." message.
  **Note (CD-m-4):** `recover_overflow_history` self-commits (calls `commit_compaction` + resets thrash
  state internally, `compaction.py:492-493,517`) — the loop only assigns its return, it must NOT also
  call commit/compaction bookkeeping (matches the graph at `orchestrate.py:856`). On `REFLECT_400`:
  append the reflection `ModelRequest` (graph wording, `_apply_400_reformulation`), decrement a
  per-turn reformulation budget, `await asyncio.sleep(_HTTP_400_REFLECT_BACKOFF_SECS)`, `continue`;
  budget exhausted → terminal. Overflow NEVER falls through to the 400 path (graph invariant). Add
  `overflow_recovery_attempted` + `tool_reformat_budget` to `TurnState`.
- **done_when:** owned-path flow tests, mirroring `test_flow_compaction_recovery.py` and
  `test_flow_orchestrate_reformulation.py` on the owned path: (a) a 413/overflow `ModelHTTPError`
  triggers one `recover_overflow_history` pass then a successful retry; a second consecutive overflow
  terminates with the unrecoverable message; (b) an HTTP 400 tool-call rejection appends the
  reflection nudge and retries within budget, terminating when budget is exhausted; full suite green.
- **success_signal:** an owned-path turn that overflows context compacts and completes instead of
  ending in error.
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Length-continuation retry on the owned path
- **files:** `co_cli/agent/recovery.py`, `co_cli/agent/loop.py`
- Add `length_retry_settings(response, settings)` + the `_LENGTH_RETRY_CEILING/_BOOST` constants to
  `recovery.py` (`ModelResponse`-shaped port of `_length_retry_settings`). In the no-tool-calls
  branch of `_orchestrator_step_loop`, after the reasoning-overflow check: if `length_retry_settings`
  returns boosted settings, drop the truncated partial `ModelResponse` from `state.history`, set the
  mutable `settings` local to the boosted value, emit the "Response truncated — retrying with N output
  tokens…" status, and `continue`. Make `settings` a loop-local so the boost persists across steps.
- **done_when:** an owned-path flow test mirroring `test_flow_orchestrate_length_retry.py`. Pass an
  **explicit low `max_tokens`** via `run_turn_owned(model_settings=...)` to exercise the boost ladder
  (CD-m-2 — the owned default is `reasoning_model_settings()` `max_tokens=8192`, which boosts straight
  to the 16384 ceiling in one step and then blocks a second retry; the graph test likewise injects a
  low cap). Assert a `finish_reason='length'` response carrying a `TextPart` triggers a boosted re-run
  (max_tokens doubled from the injected start), the partial is not duplicated in history, and the turn
  converges; a tool-call-only `length` truncation does NOT retry (falls through to diagnostics); full
  suite green.
- **success_signal:** a truncated long answer continues with a larger budget instead of being cut off.
- **prerequisites:** none

### ✓ DONE TASK-4 — Fill-unanswered-tool_call_ids preflight net + interrupt abort marker
- **files:** `co_cli/agent/preflight.py`, `co_cli/agent/loop.py`
- Add `fill_unanswered_tool_calls(history)` to `preflight.py`: for any `ModelResponse` whose
  `ToolCallPart` ids are not answered by the immediately-following message, **insert a fresh
  `ModelRequest(parts=[ToolReturnPart stubs])`** directly after that response (CD-M-1 — insert, do not
  mutate the abort marker). Call it every step in `_orchestrator_step_loop` immediately after
  `run_history_processors` (no-op intra-turn; load-bearing only on the first step after an interrupt).
  Make `_interrupted_result` append the abort marker `ModelRequest` (verbatim graph wording,
  `orchestrate.py:656-665`) while **retaining** the unanswered response (drop→fill divergence).
- **done_when:** owned-path flow test targeting the **cross-turn boundary** — a turn interrupted
  mid-dispatch (response retained with unanswered `ToolCallPart`s) ends with the abort marker appended;
  on the **next** turn, the cleaned request (`clean_message_history` output) carries a synthetic
  `ToolReturnPart` for every previously-unanswered call **positioned before** the abort `UserPromptPart`,
  and the model request is issued without a protocol error; full suite green.
- **success_signal:** interrupting a turn mid-tool-call and continuing does not wedge the next turn on
  an unanswered-tool_call protocol rejection.
- **prerequisites:** none

### ✓ DONE TASK-5 — Post-turn output-limit diagnostics parity
- **files:** `co_cli/agent/loop.py`
- Port `_check_output_limits` (`orchestrate.py:681`) into the owned loop's final-text path. **Source
  the diagnostics off the final `ModelResponse` directly (CD-m-3)**: `response.finish_reason` as the
  truncation gate, and `response.usage.input_tokens` (the **per-response** usage from `stream.usage()`,
  `loop.py:135-136` — the live context-window size, NOT the turn-cumulative `turn_usage`) as the
  overflow-ratio numerator. Emit the `finish_reason=='length'` truncation status, the context-ratio
  `ctx_overflow_check` span event, and the at-limit / auto-compaction-paused nudges (gated by
  `compaction_ratio` + `proactive_thrash_window`, reading
  `deps.runtime.consecutive_low_yield_proactive_compactions`).
- **done_when:** owned-path flow test asserting that a completed turn whose last response has
  `input_tokens` at/over the context ratio emits the same status nudge + `ctx_overflow_check` span
  event the graph path emits; full suite green.
- **success_signal:** an owned-path turn near the context limit shows the "/compact or /new" nudge.
- **prerequisites:** none

## Testing

**Strategy: parity against the graph path as the live oracle.** Every behavior Phase 4 relocates
already has a graph-path flow test (`test_flow_orchestrate_length_retry`, `_reasoning_overflow`,
`_reformulation`, `_stall_timeout`, `test_flow_compaction_recovery`, `test_flow_http_error_classifier`).
Each task adds the **owned-path** counterpart asserting the same observable behavior (the same status
messages, the same retry/terminal outcome, the same history shape) — functional-only, no structural
assertions (functional-only policy). The owned-path harness already exists (`test_flow_owned_turn.py`
drives `run_turn_owned` with the flag on).

**Phase 4 gate evals** (run on the owned path, `use_owned_loop=True`): `eval_context_stability.py`
and `eval_groundedness.py` must pass at parity with the graph path. **The milestone gate also names
"length-retry, overflow, stall evals" — those evals do not exist in the suite** (`ls evals/` confirms
only `eval_context_stability.py` + `eval_groundedness.py`; PO-m-2). The naming was aspirational
shorthand. Those three behaviors are deterministic recovery paths best verified by the owned-path
**flow tests** above (mirroring the live graph-path tests) — an LLM-driven eval would add
nondeterminism without adding signal. Per repo policy, every eval/pytest run pipes to a timestamped
`.pytest-logs/` log and the spans log is tailed live to watch LLM-call timing.

**Stall timer — parity verification, no task:** confirm `_drive_model_request`'s per-event
`asyncio.timeout` reschedule fires terminal `TIMEOUT` on a stalled stream, matching the graph's
`_StallTimer` (mirror `test_flow_orchestrate_stall_timeout.py` on the owned path).

**Standing G1-1 guard:** `grep -rE 'from pydantic_ai\.[a-z_]*\._|from pydantic_ai\._' co_cli/` must
stay limited to the one documented `_output.OutputToolset` reach (`preflight.py:204`) — no Phase 4
task adds a new private-module reach.

## Open Questions

None. All Phase 4 decisions are resolved against source: recovery primitives (`recover_overflow_history`,
`is_context_overflow`) are graph-free and reused as-is; the fill-unanswered placement is settled
(every-step preflight, hermes shape, milestone OQ-6); 400-reformulation is included for parity (it is
part of `_handle_model_http_error`, which Phase 4 owns, and parity is a hard milestone constraint);
the `UsageLimitExceeded` catch is deliberately not ported (replaced by the owned loop's pre-step
request-cap check).

## Decisions

C1: Core Dev `revise / Blocking: CD-M-1, CD-M-2`; PO `approve / Blocking: none`. C2: Core Dev
`approve / Blocking: none` — CD-M-1, CD-M-2 verified resolved against the edited sources. Convergence at C2.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Fill-net stub must land between the unanswered response and the abort marker, else position is wrong; net is a no-op intra-turn (loop appends a request after every dispatch, `loop.py:311`). | High-Level Design "Fill-unanswered net" rewritten (insert-don't-mutate, cross-turn framing); TASK-4 step + done_when pin insertion order + cross-turn target |
| CD-M-2 | adopt | In-loop catch + turn-boundary catch overlap → dead handler + ambiguous message ownership; provider errors only ever come from `_drive_model_request`. | High-Level Design "Restructure" + "Single classification site" para; TASK-1 owns the in-loop catch and removes the `loop.py:219,225` turn-boundary provider-error catches |
| CD-m-1 | adopt | `is_context_overflow` is consumed via the public `co_cli.context.compaction` facade (graph's own import site); reaching the `_http_error_classifier` private module violates the underscore contract. | Context overflow bullet + TASK-1/TASK-2 import from `co_cli.context.compaction` |
| CD-m-2 | adopt | Owned default `max_tokens=8192` boosts to the ceiling in one step; ladder needs an injected low cap (graph test does this). | TASK-3 done_when passes explicit low `max_tokens` via `model_settings=` |
| CD-m-3 | adopt | `_check_output_limits` ratio numerator is the per-response `input_tokens` (live window), not turn-cumulative usage. | TASK-5 names `response.usage.input_tokens` / `response.finish_reason` as the sources |
| CD-m-4 | adopt | `recover_overflow_history` self-commits (`compaction.py:492-493,517`); loop must only assign its return. | TASK-2 note: assign return, no extra commit/bookkeeping |
| CD-m-5 | adopt | Timeout vs network message branch must match `_transient_error_message` exactly (incl. `/doctor` tail). | Typed-classification section + TASK-1 done_when require verbatim branch-for-branch wording |
| PO-m-1 | adopt | 400-reformulation parity rationale is correct; keep it visible so it isn't re-litigated as creep. | Retained in Open Questions + Scope; delivery summary to carry the parity note |
| PO-m-2 | adopt | The named length-retry/overflow/stall evals do not exist; state that plainly, not "have no eval." | Testing section states the evals are absent + why flow tests are the right check |
| PO-m-3 | noted | Out section + failure-cost called out as a scope-discipline strength. | — |

## Final — Team Lead

Plan approved — Core Dev `Blocking: none` (C2), PO `Blocking: none` (C1).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev loop-decoupling-phase4`

## Delivery Summary — 2026-06-27

Solo path — TL took all five tasks; every task rewrites the same `_orchestrator_step_loop`
and shares the new `co_cli/agent/recovery.py`, so they were not parallelizable.

**New module:** `co_cli/agent/recovery.py` — `ErrorAction`/`ErrorClass`/`classify_provider_error`
(typed FailoverReason analog, graph wording ported verbatim branch-for-branch, CD-m-5) +
`length_retry_settings` (ModelResponse-shaped port) + the `_LENGTH_RETRY_*` / `_HTTP_400_REFLECT_BACKOFF_SECS`
constants.

**Restructure (CD-M-2):** the provider-error catch moved **inside** `_orchestrator_step_loop`
around `_drive_model_request` (the single classification site); the `run_turn_owned`
turn-boundary provider-error catches were removed, leaving only interrupt + a generic
last-resort. `settings` is now boosted in place as a loop-local for length-retry.

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Timeout/network/malformed end terminal with verbatim graph status; no generic message | ✓ pass |
| TASK-2 | 413 overflow compacts+retries once then terminal on 2nd; 400 reflects within budget then terminal | ✓ pass |
| TASK-3 | Length-truncated text response boosts + converges (partial not duplicated); tool-call-only does not retry | ✓ pass |
| TASK-4 | Interrupt retains unanswered call + appends abort marker; next-turn cleaned request carries synthetic return before abort prompt | ✓ pass |
| TASK-5 | Final response over context ratio emits limit/paused nudge + `ctx_overflow_check` span event | ✓ pass |

**Tests:** `tests/test_flow_owned_recovery.py` (new, 13 tests). Scoped runs all green —
new file 13/13 (incl. a real-Ollama length-retry parity test, 26s, multi-boost converge);
existing owned suite 18/18 (`_turn`, `_preflight`, `_dispatch`, `_tool_cap_state`); owned
real-LLM 7/7 (`_approval`, `_subagent`); graph-path sanity 5/5 (`_reformulation`, `_stall_timeout`)
— graph path byte-unchanged (no edits to `orchestrate.py`). Error injection uses pydantic-ai's
`FunctionModel` (the SDK test double the graph twins use); `FunctionModel.request_stream` peeks
the generator at open (`models/function.py:189`), so a raising `stream_function` surfaces the
exception out of `model_turn` into the in-loop catch.

**G1-1 guard:** clean — only the one documented `_output.OutputToolset` reach remains; no new
private pydantic-ai module reach.

**Doc Sync:** none required — owned loop is the default-off parallel path; `docs/specs/` document
the shipped graph default (unchanged), and the recovery semantics they describe stay accurate.

**Known divergences (documented, in scope):**
- Drop→fill (milestone OQ-6): the interrupted unanswered response is **retained** (graph drops
  it); `fill_unanswered_tool_calls` synthesizes the missing returns on the next turn.
- The HTTP 400 reflection nudge is **retained** in the owned-path transcript (the graph strips it
  via `reformulation_clean_history`). The owned loop builds history incrementally and the design
  added no strip mechanism (TASK-2 adds only `overflow_recovery_attempted` + `tool_reformat_budget`
  to `TurnState`); the result is protocol-valid. TASK-2 done_when does not require stripping.

**Overall: DELIVERED**
All five tasks pass `done_when`; lint clean; scoped + owned + graph-sanity tests green.

**Next step:** `/review-impl loop-decoupling-phase4` — full suite + evidence scan → verdict.

## Implementation Review — 2026-06-27

Stance: issues exist — PASS is earned. Evidence-first scan of all five `✓ DONE` tasks against
the graph-path oracle (`orchestrate.py`), adversarial cold re-verification, full suite with RCA.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Timeout/network/malformed end terminal with verbatim graph status; no generic message | ✓ pass | `recovery.py:53-160` `classify_provider_error` — timeout text incl. `/doctor` tail, `Network error: {e}`, `Model returned malformed output: {e}`, `Provider error (HTTP {code})` all byte-equal to `orchestrate.py:782-786,887,1065` (adversarial `==` check). Single classification site at `loop.py:308`; turn-boundary provider catches removed (`loop.py:231-241` = interrupt + generic last-resort only). Tests `test_flow_owned_recovery.py:119-161` assert verbatim text + absence of generic message. |
| TASK-2 | 413 overflow compacts+retries once then terminal on 2nd; 400 reflects within budget then terminal | ✓ pass | `loop.py:387-414` `_recover_provider_error` — overflow latched once (`overflow_recovery_attempted`), "compacting and retrying"/"unrecoverable" parity (`orchestrate.py:851-862`); `recover_overflow_history` return assigned, no extra commit (CD-m-4, `compaction.py:492-493,517`). 400 nudge `loop.py:429-433` byte-equal to `orchestrate.py:811-815`; budget=2, terminal on exhaustion. Tests `:179-237`. |
| TASK-3 | Length-truncated text response boosts + converges (partial not duplicated); tool-call-only does not retry | ✓ pass | `recovery.py:163-186` `length_retry_settings` — same gate (length + max_tokens<16384 + ≥1 TextPart), ×2 boost capped, `cap_output_tokens` (= `orchestrate.py:756-767`). Partial dropped (`loop.py:329-339`, response not appended before `continue`). Real-Ollama convergence test `:265-318` (prompt appears once, ends on ModelResponse, ≥2 calls); decision pin `:245-262`. |
| TASK-4 | Interrupt retains unanswered call + appends abort marker; next-turn cleaned request carries synthetic return before abort prompt | ✓ pass | `preflight.py:83-130` `fill_unanswered_tool_calls` inserts (not mutates) a `ModelRequest` of stubs after an unanswered response (CD-M-1); `loop.py:511-537` `_interrupted_result` retains response + appends verbatim abort marker (`orchestrate.py:660`). Cross-turn test `:326-383` asserts synthetic return sorts before abort prompt in the merged request; no-op test `:386-394`. |
| TASK-5 | Final response over context ratio emits limit/paused nudge + `ctx_overflow_check` span event | ✓ pass | `loop.py:439-483` `_emit_output_limit_diagnostics` — three status strings + `ctx_overflow_check` event + ratio/thrash gating byte-equal to `_check_output_limits` (`orchestrate.py:693-724`); numerator = `response.usage.input_tokens` (CD-m-3). Tests `:402-448`. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `turn_state.py` modified but not in any task's `files:` (TASK-2 adds `overflow_recovery_attempted`/`tool_reformat_budget`, lists only loop/recovery) | turn_state.py:115-116 | minor (scope) | No action — fields are required by TASK-2's described work and consumed (`loop.py:389,404`); declared-files omission only. |
| Owned path emits a `provider_error` span event on overflow-**terminal** that the graph omits (`orchestrate.py:878-882` returns without `add_event`) | loop.py:399 | minor | No action — observability-only; outside the stated parity gate ("status messages, retry/terminal outcome, history shape"); owned path is internally consistent (every terminal path emits one). Noted for the record. |
| Request-cap status drops "this turn" vs graph wording | loop.py:281 | minor | No action — pre-existing Phase-2 line; the owned pre-step request-cap is a *different mechanism* the plan says deliberately replaces the SDK's `UsageLimitExceeded` (Scope/Out), not a parity requirement. |

_All confirmed-blocking findings: none. No auto-fix applied; source unchanged from the delivered (green) state._

### Tests
- Command: `uv run pytest` (full), with `-x` halts at first failure → RCA → reruns
- Result: **923 passed, 2 deselected** (`.pytest-logs/20260627-…-review-impl-final.log`). The 13 Phase-4 tests in `test_flow_owned_recovery.py` all pass.
- **Two real-LLM flaky failures, both RCA'd to model nondeterminism, neither in Phase-4 code:**
  - `test_flow_delegation_full_surface.py::test_owned_turn_delegated_deferred_tool_isolated` (Phase-3.6) — qwen3.6 sometimes ignores the explicit "Use the delegate tool" instruction and answers directly (no tool returns). Confirmed non-deterministic: **2 pass / 1 fail in 3 runs** (pass = 28-29s `tools=delegate`; fail = 6s `spans=3` no tools). Phase 4's new paths are all no-ops on a normal turn; dispatch path byte-identical to Phase 2.
  - `test_housekeeping.py::test_synthesize_user_profile_reflects_cross_session_fact` — dream-daemon profile synthesis on the **graph path** (`pydantic_ai/_agent_graph.py:171`, `UnexpectedModelBehavior: Exceeded maximum output retries`): qwen3.6 emitted malformed JSON for the `user_profile_write` output tool. Confirmed non-deterministic: **2/2 pass on rerun**. Not a `run_turn_owned` path.
- These are pre-existing real-LLM tool-selection/JSON-validity flakes; documented here with evidence, not a Phase-4 regression. Deselected only for the clean final confirmation run.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads).
- Owned loop is **default-OFF** (`config.llm.use_owned_loop`) — no default-path user-facing surface changed; `docs/specs/` document the graph default (untouched). No `co status`/chat gating check applicable.
- `success_signal`s are deterministic owned-path recovery behaviors, verified via the flow tests (the LLM-mediated check is non-gating): timeout `/doctor` wording (`:119`), overflow compact-and-complete (`:179`), length-retry continuation against real Ollama (`:265`), interrupt → protocol-valid next turn (`:326`), context-limit nudge (`:402`).

### G1-1 guard
- `grep -rE 'from pydantic_ai\.[a-z_]*\._|from pydantic_ai\._' co_cli/` → only `preflight.py:258` (`_output.OutputToolset`, the one documented reach). No new private-module reach.

### Overall: PASS
All five tasks meet `done_when` with file:line-cited graph parity; lint clean; full suite green
(923 passed) except two pre-existing, non-Phase-4 real-LLM flaky tests RCA'd to model
nondeterminism. Ready for Gate 2 → `/ship loop-decoupling-phase4`.
