# Loop terminal-answer guarantee — forced tools-off summary turn on a ceiling exit (D2)

## Context

The loop-decoupling milestone (`0.9.0`) shipped with **one deliberately-deferred gap — D2** (`docs/reference/RESEARCH-loop-decoupling-peer-survey.md` §"The one pending gap"). co's owned orchestrator loop has **two ceiling exits that can end a turn with no usable answer**:

- **flood hard-stop** (`co_cli/agent/loop.py:383`): after `TOOL_CAP_HARD_STOP_CONSECUTIVE` consecutive over-cap steps, the loop emits `_last_assistant_text(state.history) or TOOL_CAP_NO_ANSWER_TEXT` and returns `_continue_result`. If the model never wrote prose (only fired tool calls), the user gets a bare stub.
- **request cap** (`co_cli/agent/loop.py:302-308`): on `requests_completed >= request_limit`, the loop returns `_terminal_result(..., outcome="error", exit_reason=REQUEST_CAP)` with `output=None` — **no answer synthesized at all.**

Both peers that own their loop **converge** on the same fix (source-verified 2026-06-30): on a ceiling exit, make **one more model call with tools removed**, asking for a written summary of the work already in context. The tools-off constraint forces synthesis instead of another tool call — and the answer is usually latent in the gathered context already.
- opencode: strip tools + `toolChoice:"none"` + `MAX_STEPS_PROMPT` (`llm.ts:195-206`, `max-steps.ts`).
- hermes: `handle_max_iterations` (`chat_completion_helpers.py:1338`, called `turn_finalizer.py:53-70`) appends a "summarize, no more tools" user turn and makes one **toolless** call (`tools` popped/omitted). The `_budget_grace_call` "grace turn with tools" flag is **dead code** — never set `True`; not a real design.

**Grounding for the co implementation (all read this pass):**
- `build_request_params(instruction_parts=…, function_tools=[])` already produces a toolless request (`output_mode="text"`, `allow_text_output=True`) — `preflight.py:317-343`. Empty `function_tools` **is** the tool-strip; co needs no `tool_choice="none"` plumbing.
- `drive_model_request` (`loop.py:135-165`) is the shared streamed-request primitive — it renders deltas and returns `(ModelResponse, RunUsage)`. The forced-summary turn reuses it directly, so the summary streams to the user.
- The per-step preflight co already runs — `fill_unanswered_tool_calls` + `clean_message_history` (`loop.py:311,318`; `preflight.py:86,136`) — is exactly hermes's pre-call safety net. The forced call must route through it (see Behavioral Constraints).
- The subagent driver `run_standalone_owned` (`loop.py:618`) returns a **structured `final_result` BaseModel**, not free text — a written-summary turn does not fit its output contract. D2 is **orchestrator-loop only**.

## Problem & Outcome

**Problem:** a turn that hits either ceiling can terminate with no usable written answer — a stub (flood) or a bare error (request cap). This is co's weakest small-model-defense edge: exactly when a small model has been thrashing, the user is most owed a synthesized "here's what I found / where I got stuck," and gets the least.

**Outcome:** on either ceiling exit, the orchestrator loop runs one preflighted, toolless model call asking for a written summary of the work so far, streams it, and returns it as the turn's answer. If that call itself fails, it falls back to today's salvage behavior — so the change is **strictly a floor-raise**: same worst case, better common case.

**Failure cost:** silent today — a small model that floods or runs long leaves the user staring at a stub or an error with no synthesis of the work that *was* done. The context (tool results) is discarded unharvested. No crash, so it never surfaces as a bug; it just quietly degrades the worst-turn experience.

## Scope

**In:**
- A single forced-summary helper on the owned orchestrator loop (`run_turn_owned`), called from **both** ceiling exits (flood hard-stop `loop.py:383`, request cap `loop.py:302`).
- The forced-summary prompt text.
- A fallback to today's salvage when the forced call errors or returns empty.
- An eval that forces a ceiling and asserts a clean written answer results.

**Out:**
- The **subagent driver** (`run_standalone_owned`) — returns a structured `final_result` model; a free-text summary turn is contract-incompatible. Its ceiling behavior is a separate concern, not touched here.
- The other terminal exits (`REASONING_OVERFLOW`, `PROVIDER_ERROR`, `TIMEOUT`, `INTERRUPTED`) — these are genuine failures where no meaningful summary is available (the model already couldn't produce a token, or the transport is down). Not ceiling-of-productive-work cases; out of scope.
- Changing the ceiling thresholds themselves (`TOOL_CAP_HARD_STOP_CONSECUTIVE`, `request_limit`) — untouched.

## Behavioral Constraints

- **This intentionally changes observable behavior vs. the old graph path.** That is *why* it was deferred from the behavior-preserving cutover. The graph path is deleted (`0.9.0`), so there is no parity gate to honor — this is an additive small-model-defense improvement.
- **Strictly a floor-raise.** The forced call is attempted; on any failure the turn falls back to the exact salvage behavior it has today (`_last_assistant_text(...) or TOOL_CAP_NO_ANSWER_TEXT`). Worst case is unchanged.
- **Preflight-first is load-bearing.** The forced call fires immediately after an abnormal stop, when history can carry orphaned tool-call ids (a flood step's calls were shed, not answered). The forced turn MUST run `fill_unanswered_tool_calls` + `clean_message_history` before the call, or the provider 400s on unpaired tool-call ids (hermes's exact reason, `chat_completion_helpers.py:1390`).
- **The summary streams — but the double-emit guard must NOT use `renderer.streamed_text` (CD-M-2).** `StreamRenderer._streamed_text` **latches**: it is set True on the first text delta (`co_cli/display/stream_renderer.py:86`) and is never reset within a turn (one renderer per turn, `loop.py:227`; `finish()` returns it without resetting, `:170`). So if any pre-ceiling step streamed text, the turn-level flag is stale-True and would suppress the fallback `on_final_output` — the user sees nothing, defeating the floor. The helper must: (a) call `renderer.finish()` after its `drive_model_request` (the normal path does this at `:344` before harvesting), and (b) decide the `on_final_output` fallback on **whether the forced call itself produced/streamed text**, not the turn-latched flag — e.g. capture `streamed_before = renderer.streamed_text` before the forced call and emit only when the forced call added nothing.
- **Stall budget + interrupt (CD-m-3).** The forced call reuses `stall_window` via `drive_model_request` — correct (it is a per-token progress stall, not a wall-clock budget). A stall firing raises `TimeoutError`, which is an **intended fallback trigger** (caught → salvage). `asyncio.CancelledError` is deliberately **NOT** caught in the helper, so a user Esc during the forced call propagates to `run_turn_owned`'s handler (`loop.py:248`) and stashes the interrupt result. Do not add `CancelledError` to the helper's except tuple.
- **One model call, no loop.** The forced turn is a single request; if the model still emits a tool call, ignore the tool call and take its text (or fall back). No re-entry into the tool loop.

## High-Level Design

Add one helper to `co_cli/agent/loop.py`, called from both ceiling exits inside `_orchestrator_step_loop` (CD-m-1 — the exits live in the step loop, not `run_turn_owned`):

```
async def _forced_summary_turn(state, deps, frontend, settings, static_instructions,
                               renderer, stall_window, turn_usage, requests_completed) -> str:
    # 1. append a tools-off summary request as a user turn
    state.history = [*state.history, ModelRequest(parts=[UserPromptPart(content=_SUMMARY_PROMPT)])]
    # 2. same preflight the main loop runs every step (pairs orphaned tool-call ids)
    processed = fill_unanswered_tool_calls(await run_history_processors(state.history, deps))
    state.history = processed
    # 3. toolless request — empty function_tools IS the strip (build_request_params default)
    instr = _instruction_parts_for_step(deps, static_instructions, processed, requests_completed)
    params = build_request_params(instruction_parts=instr, function_tools=[])
    streamed_before = renderer.streamed_text            # latch-aware (CD-M-2)
    try:
        response, step_usage = await drive_model_request(
            deps, clean_message_history(processed), params, settings, renderer, stall_window)
        turn_usage.incr(step_usage)
        state.model_requests += 1
        state.history = [*state.history, response]
        text = _final_text(response)
    except (ModelHTTPError, ModelAPIError, httpx.ReadError, TimeoutError, UnexpectedModelBehavior):
        text = ""                                       # CancelledError deliberately NOT caught
    renderer.finish()
    text = text or _last_assistant_text(state.history) or TOOL_CAP_NO_ANSWER_TEXT
    # emit only if the forced call itself streamed nothing new (not the turn-latched flag)
    if not (renderer.streamed_text and not streamed_before) and not <forced-call-streamed-text>:
        frontend.on_final_output(text)
    return text
```

(The exact emit predicate is settled in TASK-1 against the renderer API; the invariant is: emit the harvested text once iff the forced call did not itself stream it.)

Both ceiling exits then: run `_forced_summary_turn`, set `exit_reason`, return `_continue_result(state, turn_usage, text)`.

- **flood hard-stop (`loop.py:383-392`):** replace the `salvaged = _last_assistant_text(...) or TOOL_CAP_NO_ANSWER_TEXT` block with the forced-summary call. `exit_reason` stays `TOOL_CAP`.
- **request cap (`loop.py:302-308`):** replace the `_terminal_result(..., outcome="error")` with the forced-summary call returning `_continue_result`. This flips REQUEST_CAP from an error-with-no-output to a continue-with-summary — the intended improvement. `exit_reason` stays `REQUEST_CAP`. **User-facing consequence (PO-m-1), intended:** `main.py:170` prints "An error occurred during this turn." only when `outcome == "error"`; after the flip a request-cap exit shows the synthesized summary and **no** error banner. This is correct — the turn is no longer an error — and is a deliberate accepted change, not an incidental side-effect.

The prompt (`_SUMMARY_PROMPT`, hermes's shape, phrased to fit **both** exits — PO-m-3, so it is not tool-call-specific): *"You've reached this turn's step limit. Do not call any more tools. Give a final response summarizing what you found and did so far, and clearly state anything left unfinished."*

## Tasks

✓ DONE **TASK-1 — Forced-summary helper + both ceiling call sites**
- files: `co_cli/agent/loop.py`
- Add `_SUMMARY_PROMPT` constant and `_forced_summary_turn(...)`; wire it into the flood hard-stop (`:383-392`) and the request cap (`:302-308`) exits inside `_orchestrator_step_loop` per High-Level Design. Route through `fill_unanswered_tool_calls` + `clean_message_history` (preflight-first); toolless via `build_request_params(function_tools=[])`; call `renderer.finish()`; gate the fallback `on_final_output` on the forced call's own streamed output (NOT the turn-latched `renderer.streamed_text`); retain the existing salvage as the in-helper fallback; do not catch `CancelledError`.
- done_when: TASK-3's deterministic FunctionModel tests pass end-to-end through `run_turn_owned` — both a request-cap and a flood exit return `outcome=="continue"` with non-empty `TurnResult.output` (the forced summary), and reverting `_forced_summary_turn` makes them fail.
- success_signal: a user whose turn hits either ceiling sees a written "here's what I found / what's unfinished" summary instead of an error line or a bare stub.
- prerequisites: none

✓ DONE **TASK-2 — Preflight + fallback correctness (hardening of TASK-1)**
- files: `co_cli/agent/loop.py`
- Ensure a provider error raised inside the helper falls back to salvage (never propagates), and that the preflight pairs orphaned tool-call ids left by a shed flood step so the forced call does not 400.
- done_when: (TASK-3-covered) with a FunctionModel whose forced-summary step **raises**, the turn still returns via `_continue_result` with the salvaged text and does not raise; and with a post-flood history carrying an **orphaned tool-call id**, the forced call completes without a provider 400 and the turn returns non-empty output (pairing proven by absence of the 400 — an observable outcome, not an internal-shape assertion, CD-m-2).
- success_signal: N/A (hardening of TASK-1)
- prerequisites: TASK-1

✓ DONE **TASK-3 — Deterministic ceiling coverage in the existing FunctionModel flow tests**
- files: `tests/test_flow_model_request_cap.py`
- This file **already** forces both ceilings deterministically with `FunctionModel` (`_within_cap_runaway_model:135`, `_hard_stop_then_text_model:168`, `_hard_stop_no_text_model:206`). Extend those harnesses so the model yields plain text on the forced-summary step, and update assertions for the new behavior: (a) `test_model_request_cap_stops_runaway_loop:158` and `test_wrap_up_nudge_ignored_never_persists_as_part:357` flip from `outcome=="error"` to `outcome=="continue"` + non-empty `turn.output`; reconcile the `"Model-request cap"` status assertion (`:159`) with the status the forced-summary path emits; (b) `test_tool_cap_hard_stop_without_text_returns_canned_message:226` now asserts the forced summary is returned, not the canned message; (c) add cases for the two TASK-2 fallbacks (forced call raises → salvaged text; orphaned tool-call id → no 400, non-empty output). Deterministic, no real LLM — the correct tool for control-flow verification, and it avoids reverse-engineering a synthetic real-LLM task to trip a code path (per eval policy).
- done_when: `uv run pytest tests/test_flow_model_request_cap.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-d2.log` is green, and the full suite passes.
- success_signal: N/A (regression + behavioral guard)
- prerequisites: TASK-1

## Testing

Per `.agent_docs/testing.md`: functional assertions only (assert the turn returns written `output` on a ceiling exit — mirror `done_when`; never assert "helper was called" or internal message-list shape). Both ceilings are **deterministically forceable with `FunctionModel`** — the existing `tests/test_flow_model_request_cap.py` already does exactly this, so it is the correct home for all D2 coverage (control-flow behavior, not model capability). **No real-LLM eval** is added: forcing a ceiling requires a synthetic task reverse-engineered to trip a code path, which co's eval policy forbids (evals must be real use-case scenarios), and a real model cannot deterministically prove "the forced summary fired *because* of the ceiling." Fail-fast (`pytest -x`); pipe every run to a timestamped `.pytest-logs/` file; tail the log live; any long/stalled call is RCA-first, never a timeout bump.

## Open Questions

None. All design decisions resolved against source: tool-strip = empty `function_tools` (`preflight.py:337-338`, valid toolless request on the OpenAI-chat path); single shared helper for both exits (in `_orchestrator_step_loop`); orchestrator-only scope (subagent returns structured `final_result`, `loop.py:686` `allow_text_output=False`); floor-raise fallback; double-emit gated on the forced call's own output (not the latched `renderer.streamed_text`); deterministic `FunctionModel` coverage in the existing flow-test file. CD-m-4's re-open (renderer lifecycle + existing-test reconciliation) is now settled in Behavioral Constraints / TASK-3.

---

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Verified `tests/test_flow_model_request_cap.py:158,357` hardcode `outcome=="error"` for the request cap — the flip breaks them. | TASK-3 now owns updating these to `outcome=="continue"` + non-empty output and reconciling the `:159` status assertion; file added to scope. |
| CD-M-2 | adopt | Verified `_streamed_text` latches (`co_cli/display/stream_renderer.py:86`, never reset; `finish()` returns without reset `:170`) — my latch-based double-emit guard would suppress the fallback emit. | High-Level Design pseudocode + Behavioral Constraints: `renderer.finish()` + gate emit on the forced call's own output via `streamed_before` capture, not the turn-latched flag. |
| CD-M-3 | adopt | Verified the file already forces both ceilings deterministically with `FunctionModel` (`:135,168,206`); a real-LLM eval both duplicates that and violates the no-reverse-engineered-scenario eval rule. | Dropped the `evals/eval_terminal_answer_guarantee.py` task; folded all coverage into TASK-3 (`test_flow_model_request_cap.py`); rewrote Testing. |
| CD-m-1 | adopt | Exits live in `_orchestrator_step_loop`; instruction count should use the loop-local `requests_completed`. | High-Level Design: noted step-loop home; helper takes `requests_completed`. |
| CD-m-2 | adopt | "Assert paired at the `drive_model_request` boundary" is an internal-shape assertion (functional-only forbids). | TASK-2 done_when reframed to the observable outcome (no 400, non-empty output). |
| CD-m-3 | adopt | Stall `TimeoutError` is an intended fallback trigger; `CancelledError` must stay uncaught. | Added the stall-budget + interrupt constraint. |
| CD-m-4 | adopt | Re-open Open Questions until renderer + test reconciliation settled. | Settled both this cycle; Open Questions notes the resolution. |
| PO-m-1 | adopt | `main.py:170` prints the error banner only for `outcome=="error"`; the flip suppresses it on request-cap — intended, should be explicit. | High-Level Design request-cap bullet names the banner suppression as deliberate. |
| PO-m-2 | noted | Confirms orchestrator-only + failure-exit exclusions are right-scoped, not under-scoped. | — |
| PO-m-3 | adopt | "tool-call limit" is imprecise for the request-cap exit. | `_SUMMARY_PROMPT` reworded to "step limit". |

_Convergence at C2: Core Dev `Blocking: none` — all three C1 blockers verified resolved against source (`test_flow_model_request_cap.py:158,357`; `co_cli/display/stream_renderer.py:86,170`; the FunctionModel ceiling harnesses). PO `Blocking: none` at C1; the revision did not alter scope, so PO was not re-run._

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: **right problem? correct scope?**
> Once approved, run: `/orchestrate-dev loop-terminal-answer-guarantee`

---

## Delivery Summary — 2026-06-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | request-cap + flood exits return `outcome=="continue"` with non-empty forced-summary output through `run_turn_owned`; reverting the helper fails them | ✓ pass |
| TASK-2 | forced-call raise → salvage (no propagation); orphan-carrying history → clean non-empty output, no 400 | ✓ pass |
| TASK-3 | `tests/test_flow_model_request_cap.py -x` green + related owned-loop suites green | ✓ pass |

**Tests:** scoped — 56 passed, 0 failed (`test_flow_model_request_cap.py` [12] + owned-turn/recovery/tool-cap-state/chat-loop/preflight). Full suite deferred to `/review-impl`.
**Doc Sync:** fixed — `core-loop.md` (bounds table, wrap-up framing, Mermaid flowchart, §2.3, §2.6 rewritten with `_forced_summary_turn`), `pydantic-ai-integration.md` (§2.5 pseudocode + prose, §2.13 framing), `loop.py` module docstring. `01-system.md` index clean.

**Overall: DELIVERED**
Both ceiling exits now run one preflighted, toolless `_forced_summary_turn` and return a written answer (`outcome="continue"`); on provider error/stall the turn falls back to today's salvage — strictly a floor-raise. TASK-1 + TASK-2 landed in the single helper (its preflight + salvage fallback *are* TASK-2's hardening).

### Implementation notes (findings vs plan premises)

- **Orphans cannot reach the forced call.** The plan's premise that "a flood step's calls were shed, not answered" is inaccurate: `dispatch_tools` (`co_cli/agent/dispatch.py:262-267`) returns an exceeded-payload `ToolReturnPart` for *every* shed call, so a flood step leaves no orphaned tool-call id. Any orphan seeded via `message_history` is paired by the per-step preflight (`loop.py:311`) at step 1, before any ceiling. The helper's `fill_unanswered_tool_calls` is therefore a defensive/idempotent net (kept for consistency with every other step + hermes parity), not load-bearing. TASK-2's orphan coverage (`test_ceiling_with_orphaned_tool_call_returns_clean_summary`) is written as an honest whole-turn tolerance test: seeded-orphan history + ceiling → clean non-empty output, no error — the observable user-facing guarantee, per CD-m-2.
- **CD-M-2 emit predicate settled to the forced call's own text, not `streamed_before`.** `renderer.streamed_text` latches for the whole turn, so `streamed_before` capture (plan pseudocode line 78) can't distinguish whether *this* call streamed when a prior step already streamed text. The discriminator used is `_final_text(response)` from the forced call itself: non-empty ⇒ it streamed and `renderer.finish()` committed it ⇒ skip `on_final_output`; empty (or the call raised) ⇒ emit the salvage once. Sidesteps the latch entirely and satisfies "emit iff the forced call did not itself stream it."
- **`model_requests` count.** The forced summary is a genuine extra model request, so it increments `state.model_requests` (a request-cap turn with limit 3 reports 4). Test updated accordingly.

---

## Implementation Review — 2026-06-30

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | request-cap + flood exits return `outcome=="continue"` with non-empty forced-summary output through `run_turn_owned`; reverting the helper fails them | ✓ pass | `loop.py:288-352` `_forced_summary_turn`; wired at request-cap `loop.py:381-393` and flood hard-stop `loop.py:473-485`; toolless via `build_request_params(function_tools=[])` → `output_mode="text"` (`preflight.py:337-340`); preflight-first `loop.py:327`; `renderer.finish()` `loop.py:348`; salvage fallback `loop.py:349`. Tests `test_flow_model_request_cap.py:163,219,277` assert `continue`+`_FORCED_SUMMARY` — would fail against the old error/stub returns. |
| TASK-2 | forced-call raise → salvage (no propagation); orphan-carrying history → clean non-empty output, no 400 | ✓ pass | except tuple `loop.py:340-347` (no `CancelledError`); `test_forced_summary_failure_falls_back_to_visible_text:232` → "partial progress"; `..._no_prior_text_returns_canned_message:290` → `TOOL_CAP_NO_ANSWER_TEXT`; `test_ceiling_with_orphaned_tool_call_returns_clean_summary:303` → clean output, no 400. |
| TASK-3 | `test_flow_model_request_cap.py -x` green + full suite | ✓ pass | 12/12 in the file; FunctionModel harnesses key off `not info.function_tools` to yield prose on the forced step. Assertions observe output values (not structure). |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `test_error_outcome_turn_still_records_usage` asserted `outcome=="error"`, broken by the D2 request-cap flip to `"continue"` — a cross-file dependency TASK-3 (scoped to `test_flow_model_request_cap.py`) missed | tests/test_flow_usage_tracking.py:150-164 | blocking | Rewrote to drive a genuine error outcome via a terminal `ModelHTTPError(500)` (`recovery.py:118-123`) instead of flipping the assertion — preserves the "usage recorded on every return path incl. error" guard the finally block (`loop.py:273-277`) provides, which a flip would have silently dropped. Full suite green after. |
| Extra uncommitted files present at review start (`docs/reference/RESEARCH-*.md` ×2, `uv.lock`) — not in any task's `files:`, not part of this delivery | — | minor | Pre-existing edits, not introduced by D2. Flagged for staged-file hygiene at `/ship` — do not stage with this plan. |

### Tests
- Command: `uv run pytest -p no:cacheprovider`
- Result: **890 passed, 0 failed** (8m12s)
- Log: `.pytest-logs/20260630-234455-review-impl-full2.log`
- Slow calls (37–50s) are real-LLM delegation/synthesis tests — known warm-model latency, unrelated to D2, no stalls.

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- D2 is LLM-mediated loop control — verified via the deterministic `FunctionModel` flow tests (the plan's chosen tool; no real-LLM eval per eval policy). `success_signal` verified: on both ceilings the turn returns `outcome=="continue"` with a written summary (`_FORCED_SUMMARY`) instead of an error line or bare stub; request-cap error banner is suppressed (`main.py:170` gates on `outcome=="error"`). Chat interaction non-gating.

### Overall: PASS
One blocking issue found and fixed (a cross-file test dependency the plan under-scoped); full suite green, lint clean, boot smoke passes. Both ceiling exits deliver a synthesized written answer as designed — strictly a floor-raise.
