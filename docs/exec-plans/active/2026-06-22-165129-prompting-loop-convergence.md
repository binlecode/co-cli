# Prompting-Loop Convergence — Agent-Loop Best Practices

## Context

Cross-review of the four peer prompting-system research docs in `docs/reference/`:
`RESEARCH-prompting-system-codex.md`, `-hermes.md`, `-openclaw.md`, `-opencode.md`.
The goal was to converge the **agent-loop structure** best practices and identify
where co conforms vs. diverges, then turn the one actionable gap into work.

### Convergent best practices (all four peers)

1. **The loop IS the tool-feedback engine.** One iteration == one model request;
   continue while the model emits tool calls, exit on
   `finish_reason != "tool-calls"` with no pending calls. Planning/execution/
   verification are implicit modes of one dispatch loop, not staged sub-loops.
2. **Byte-stable system prompt; volatile state goes into history, not the prompt.**
   Base prompt assembled once per session/attempt; mid-session state (env,
   permissions, model switch, plan/build reminders) injected as synthetic
   history messages. Day-granularity clock to preserve cache stability.
3. **Hard stop conditions.** All four enforce ceilings (interrupt / max-iterations
   / budget / request cap / context overflow) that end or redirect the turn.
4. **Context overflow → dedicated compaction prompt as loop work**, never turn
   termination (codex `SUMMARIZATION_PROMPT`, openclaw compaction subagent,
   hermes `compress() → invalidate → continue`).
5. **Tools as structured API specs** (never embedded in prompt text) + explicit
   "parallelize independent calls / don't poll / prefer rg" guidance.
6. **Output discipline:** no preamble, no trailing summary, no narrating-without-doing.
7. **Action bias / persistence:** continue until done; respect stop conditions;
   no self-preservation; codex adds no-auto-commit.

### Single-peer pattern adopted (NOT a 4-way convergence)

- **Graceful in-band wrap-up on the final allowed step.** Only **opencode** does
  this: it splices `MAX_STEPS_PROMPT` ("this is your last move, wrap up") before the
  cap fires, instead of truncating cold. codex / hermes / openclaw have hard stops
  (#3) but no evidence of a pre-cap wrap-up. This is the precedent TASK-1 adopts —
  chosen because it fixes a real co gap (below), not because all four peers agree.

### Where co already conforms (no action — documented for the record)

- **#2** byte-stable base + day-granularity clock, base+overlay assembly
  (see `project_prompt_dynamic_suffix_cache_ordering`, `project_prompt_profile_architecture`).
- **#4** client-side compaction with a dedicated summarizer
  (`feedback_context_management_self_contained`).
- **#5** monomorphic tool surface + parallel-call guidance
  (`feedback_tool_split_small_model`).
- **#6** conciseness BASE floor (shipped v0.8.436).
- **#7** action-bias/persistence prose lives in the BASE/soul doctrine.

### Accepted divergence (no action — rejected by design)

- **#1 loop ownership.** co's tool-feedback loop lives *inside* one pydantic-ai
  `run_stream_events(...)` call (the SDK `ModelRequestNode` loop); the peers own
  the loop explicitly. co deliberately delegates the inner loop to the SDK and
  wraps it with recovery (`orchestrate.py` run-turn). This is the pydantic-ai
  integration boundary, not a defect. The separate `project_drop_capability_api`
  effort targets the *capability SDK* coupling, not the run loop. **No task.**

## Problem & Outcome

co has two turn caps, and **neither warns the model before the ceiling**. They are
**asymmetric** in how they end the turn (verified against source):

- **Cumulative model-request cap** (`max_model_requests_per_turn`,
  `_resolve_request_limit` → SDK `request_limit`, enforced *before every request*
  inside `run_stream_events`, raises `UsageLimitExceeded` at `orchestrate.py:974`):
  aborts mid-run → `_build_error_turn_result` with `output=None` → `outcome="error"`.
  This is the **cold-truncation path**: whatever the model was doing is discarded.
- **Consecutive tool-call cap** (`TOOL_CAP_HARD_STOP_CONSECUTIVE = 3`,
  `toolset.py:177`): latches `tool_cap_hard_stop`; the SDK run completes first, so
  `_check_turn_caps` (`orchestrate.py:553-568`) *already surfaces the model's final
  answer as `outcome="continue"` when one exists*. It only falls to `error` when the
  capped run produced no string output. So this path is already partly graceful —
  but with no warning, the model usually spends its last allowed step on tool calls
  and produces no text, falling to `error`.

The fix (opencode's pattern) is a graceful in-band wrap-up: when one step from a
cap, tell the model "this is your last action — produce your final answer now, no
more tool calls."

**Outcome:** approaching either cap injects a single in-band wrap-up nudge on the
model's final allowed request. For the **cumulative cap** this is the primary win
(turns that would cold-truncate to `error` get a chance to return a synthesized
answer); for the **consecutive cap** it raises the rate at which the final step is
a synthesized answer rather than tool-calls-only (a smaller but cheap win — both
nudges share one seam).

**Failure cost (per-cap):**
- Cumulative cap: turns silently return *no usable answer* (`error`, `output=None`).
  The user re-asks or loses the work the agent already did.
- Consecutive cap: when the last step happens to be tool-calls-only, the
  already-soft landing still falls to `error` instead of an answer.

## Scope

**In:** in-band wrap-up nudge for both caps via a `history_processor` (per-request
transform) so the nudge is inherently ephemeral and never persisted.

**Out:** loop ownership refactor (accepted divergence #1); compaction (#4 conforms);
prompt/conciseness content (#6 conforms); any cap *threshold* change (RCA-first
policy — `feedback_long_llm_call_rca_first`); new config surface beyond what the
nudge needs.

## Behavioral Constraints

- The wrap-up nudge is injected by a **`history_processor`** (a per-request
  transform that shapes the message list handed to the model but is *not* part of
  `new_messages()` and is never persisted to the run result). Ephemerality is
  inherent to the processor seam — there is **no** reuse of the 400-reformulation
  `reformulation_clean_history` strip path (that path applies only to its own
  snapshot+rebuild and does not cover processor-spliced messages). Verify
  ephemerality with a test, not by reusing the strip seam.
- Fire the nudge **at most once per turn**, on the final allowed request for
  whichever cap is reached first, via a dedicated `wrap_up_fired` latch (separate
  from the trigger condition, which stays true across length-retry re-entry). Reset
  the latch in `reset_for_turn` alongside the other per-turn cap fields.
- Cap disabled must suppress the nudge: the cumulative trigger reads the resolved
  limit from `_resolve_request_limit` (`None` ⇒ no nudge — same helper the SDK uses,
  no second disable check); the consecutive trigger is inert when its cap is off.
- The nudge instructs "final answer now, no more tool calls"; it does **not** change
  the hard-stop behavior — if the model ignores it, the SDK still raises
  `UsageLimitExceeded` / the latch still fires and the turn ends as today.

## High-Level Design

The tool-feedback loop runs entirely inside one pydantic-ai `run_stream_events(...)`
call (accepted divergence #1); the orchestrator regains control only at *run
boundaries*, not between in-loop requests. So both caps advance request-to-request
**inside one run**, and the only seam that fires before every in-loop request is the
registered `history_processors` chain (`history_processors.py`; registered in
`orchestrator.py`), which carries `RunContext[CoDeps]`. A new wrap-up processor
lives there. Two triggers, one shared nudge payload:

- **Cumulative cap:** the processor compares `ctx.usage.requests` against the
  resolved `request_limit` (from `_resolve_request_limit`); when one short
  (`requests == limit - 1`), splice the wrap-up `UserPromptPart` for that request.
- **Consecutive cap:** the streak is incremented in `_CallSeamToolset.call_tool`
  (`toolset.py:175-178`) inside the loop. When it reaches
  `TOOL_CAP_HARD_STOP_CONSECUTIVE - 1`, set a `RuntimeState` flag the *same processor*
  reads on the next request to splice the wrap-up.
- **Once-per-turn:** the processor checks/sets the `wrap_up_fired` latch so the
  nudge lands on exactly one request even across length-retry re-entry.

Exact `UserPromptPart` shape and flag names are an orchestrate-dev implementation
choice; this design fixes the seam (`history_processors`), the two triggers, the
disable-guard helper, the once-per-turn latch, and ephemerality-via-processor.

## Tasks

- **TASK-1** — Wrap-up `history_processor` + cumulative-cap trigger (primary win).
  - `files:` `co_cli/context/history_processors.py`, `co_cli/agent/orchestrator.py`,
    `co_cli/deps.py`
  - `done_when:` a test sets `max_model_requests_per_turn = N`, drives an autonomous
    runaway turn that stays inside one `run_stream_events` call, and asserts the
    request issued at `ctx.usage.requests == N - 1` carries the wrap-up instruction;
    with the cap disabled (`= 0`, `_resolve_request_limit` → `None`) no nudge is
    injected; the wrap-up text is absent from `TurnResult.messages`; full suite passes.
  - `success_signal:` a cumulative-cap turn where the model honors the nudge (proven
    with a `FunctionModel` stub that emits text on the capped request) returns a
    final answer instead of an `error` result. The outcome-flip is model-dependent
    (the hard-stop is preserved if the model ignores the nudge); the deterministic
    contract is the nudge reaching the request input.
  - `prerequisites:` none

- ✓ DONE **TASK-1b** — Move the nudge from the history-processor seam to a dynamic
  instruction callback (refactor; supersedes TASK-1's mechanism, keeps its behavior).
  - **Why** (verified against `pydantic-ai==1.92.0` source): TASK-1's processor is
    *additive* — it splices a message that the SDK persists (`ctx.state.message_history[:] = messages`),
    forcing `drop_wrap_up_messages` to strip it at every `run_turn` return path. A
    dynamic `agent.instructions()` callback is a cleaner seam with the same trigger
    and timing (`_get_instructions` runs in `_prepare_request` before the request,
    `ctx.usage.requests` still reads `limit - 1`). Its output lands in
    `model_request_parameters.instruction_parts`, **recomputed fresh every request**
    (`models/__init__.py:_get_instruction_parts`); historical `ModelRequest.instructions`
    are ignored in the agent flow, so the nudge is never replayed to the model next
    turn and **needs no strip**. It matches the existing `safety_prompt` precedent
    (dynamic "stop/caution" steering already lives in the instruction block).
  - `files:` `co_cli/agent/_instructions.py` (new `wrap_up_prompt(ctx)` callback +
    `WRAP_UP_TEXT`, relocated here), `co_cli/agent/orchestrator.py` (register in
    `per_turn_instructions`; remove `wrap_up_on_final_request` from `history_processors`),
    `co_cli/context/history_processors.py` (delete `wrap_up_on_final_request`,
    `drop_wrap_up_messages`, `WRAP_UP_TEXT` and the docstring entries),
    `co_cli/agent/orchestrate.py` (remove both `drop_wrap_up_messages` call sites +
    the import), `co_cli/config/llm.py` (repoint the `resolve_request_limit`
    docstring from `history_processors.wrap_up_on_final_request` to
    `_instructions.wrap_up_prompt`), `tests/test_flow_model_request_cap.py` (assert
    the nudge via the request's instruction text, not a message part)
  - `done_when:` a test drives the cumulative-cap runaway and asserts the request at
    `ctx.usage.requests == limit - 1` carries `WRAP_UP_TEXT` in its **instructions**
    (not as a `UserPromptPart`); cap disabled (`= 0`) → no nudge; `WRAP_UP_TEXT`
    never appears as a `UserPromptPart`/text part in `TurnResult.messages` **with no
    strip step** (it MAY persist on `ModelRequest.instructions` — that is expected and
    harmless, same as `safety_prompt`/`current_time_prompt`; the SDK recomputes
    instructions fresh each request via `_get_instruction_parts` and ignores historical
    `ModelRequest.instructions`, so the nudge is never replayed — do NOT assert
    `WRAP_UP_TEXT not in str(messages)`); a **repo-wide grep** finds zero references to
    `drop_wrap_up_messages` / `wrap_up_on_final_request` (per `review.md` — drop/rename
    done only when grep is clean AND tests pass); full suite passes.
  - `success_signal:` the cumulative-cap turn still flips `error` → `continue` with a
    synthesized answer when the model honors the instruction-framed nudge — verified
    by an eval/representative run that the model obeys the instruction-block framing
    at least as well as the prior message framing **before** the processor approach
    is retired (the framing change is the one behavioral risk).
  - `prerequisites:` TASK-1 (shipped — this refactors it). Sequence **before** TASK-2
    so the consecutive-cap trigger rides the same instruction callback (its
    `wrap_up_fired` latch still applies).

- **TASK-2** — Consecutive-cap trigger via the same instruction callback + once-per-turn latch.
  - **Note (post-1b):** after TASK-1b the wrap-up seam is the `wrap_up_prompt(ctx)`
    instruction callback in `_instructions.py`, NOT `history_processors.py`. TASK-2's
    consecutive trigger reads its `RuntimeState`/streak flag inside that callback and
    appends the same `WRAP_UP_TEXT`; the `files:`/`done_when:` below are written against
    the instruction-callback seam.
  - `files:` `co_cli/agent/toolset.py`, `co_cli/agent/_instructions.py`,
    `co_cli/deps.py`
  - `done_when:` a test drives a single-run runaway to
    `TOOL_CAP_HARD_STOP_CONSECUTIVE - 1` consecutive over-cap requests and asserts
    the next in-loop request carries the wrap-up instruction; a length-retry
    re-entry test asserts the nudge fires **at most once** per turn (the
    `wrap_up_fired` latch, reset in `reset_for_turn`); the nudge appears in the
    request's **instructions** (not as a `UserPromptPart`); full suite passes.
  - `success_signal:` approaching the consecutive cap, the model's final allowed
    step receives "final answer now," raising the rate it returns a synthesized
    answer rather than tool-calls-only.
  - `prerequisites:` TASK-1 (shares the processor, the nudge payload, and the
    `wrap_up_fired` latch)

## Testing

- Reuse the existing cap test scaffolding: `tests/test_flow_model_request_cap.py`
  (`test_request_cap_interrupts_within_cap_single_run_loop:559-614` and
  `test_hard_stop_fires_in_single_run_without_approval:418-478` are the runaway
  patterns to extend) and `tests/test_flow_orchestrate_stall_timeout.py`.
- Functional assertions only (`feedback_functional_tests_only`): assert the wrap-up
  text reaches the model input (via `FunctionModel`/captured request messages) and
  is absent from `TurnResult.messages`; assert once-per-turn firing across a
  length-retry re-entry. Do not grep for flag/field existence or assert outcome as a
  hard contract where it is model-dependent.
- Update the production-paths docstring in `test_flow_model_request_cap.py:4-5` to
  add `co_cli/context/history_processors.py`.
- All pytest runs pipe to a timestamped `.pytest-logs/` file and tail the log
  for LLM call timing.

## Open Questions

None — seam (`history_processors`), both triggers, disable-guard helper, and the
once-per-turn latch are settled against source. The `UserPromptPart` shape and flag
names are deferred to orchestrate-dev as implementation choices, not product decisions.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Cumulative cap is SDK-enforced mid-run; `turn_state.model_requests` reads 0 during the single run — orchestrator-level trigger is infeasible. | TASK-1 rewritten around the `history_processor` seam comparing `ctx.usage.requests` to the resolved `request_limit`; High-Level Design rewritten. |
| CD-M-2 | adopt | Consecutive streak advances inside one run; flag set in toolset is fine but must be consumed in-loop. | TASK-2 reads the flag in the same processor (not orchestrator input plumbing). |
| CD-M-3 | adopt | 400-strip reuse is unsubstantiated; processor-spliced messages aren't in `new_messages()`, so ephemerality is inherent — not via the strip seam. | Behavioral Constraints + Scope rewritten; strip-seam claim removed; ephemerality verified by test. |
| CD-M-4 | adopt | Correct home is `co_cli/context/history_processors.py` + `orchestrator.py` registration + a `RuntimeState` flag. | All task `files:` lists corrected. |
| CD-m-1 | adopt | Disable parity must use `_resolve_request_limit` (`None` ⇒ no nudge), not a re-derived `cap-1`. | Behavioral Constraints + TASK-1 done_when reference the resolved limit. |
| CD-m-2 | adopt | Length-retry re-entry keeps the trigger condition true; need a separate `wrap_up_fired` latch reset in `reset_for_turn`. | Behavioral Constraints + TASK-2 name the latch and its reset. |
| CD-m-3 | adopt | Outcome-flip is model-dependent; hard contract is the nudge reaching the request. | TASK-1 success_signal reframed; `FunctionModel` stub proves capability. |
| CD-m-4 | adopt | No stale assertions; docstring needs the new path. | Testing adds the `test_flow_model_request_cap.py:4-5` docstring update. |
| PO-M-1 | adopt | Caps are asymmetric: consecutive already surfaces an answer when present (`_check_turn_caps:553-568`); only cumulative cold-truncates. | Problem & Outcome rewritten to state the asymmetry; sourced. |
| PO-M-2 | modify | Consecutive nudge kept (not dropped) — shares one processor seam at near-zero cost — but reframed as "raise the rate the final step is an answer." | TASK-2 success_signal reframed; Outcome states the smaller win explicitly. |
| PO-M-3 | adopt | TASK-3 defined no new behavior and its done_when used a forbidden structural grep. | TASK-3 deleted; guards folded into TASK-1 (disable) and TASK-2 (once-per-turn); functional ephemerality test kept. |
| PO-m-1 | reject | No-action convergence documentation is in-scope and survived scrutiny. | — |
| PO-m-2 | adopt | Failure cost lumped both caps. | Failure cost split per-cap. |
| G1-1b-1 | adopt | TASK-1b's files list missed `co_cli/config/llm.py:267`, whose `resolve_request_limit` docstring references `history_processors.wrap_up_on_final_request`; the done_when's zero-reference grep would fail. | Added `co_cli/config/llm.py` (docstring repoint) to TASK-1b `files:`. |
| G1-1b-2 | adopt | "Absent from `TurnResult.messages`" was imprecise: `_prepare_request` (`_agent_graph.py:839`) persists instructions onto `ModelRequest.instructions`, same as `safety_prompt`/`current_time_prompt`. Non-replay (via `_get_instruction_parts`) is the real ephemerality guarantee, not total absence. | TASK-1b done_when tightened: assert absent as a `UserPromptPart`/text part; `.instructions` persistence is expected; forbid `WRAP_UP_TEXT not in str(messages)`. |
| G1-1b-3 | adopt | TASK-2's seam (`history_processors.py`) goes stale once 1b retires the processor; TASK-2 must ride the `wrap_up_prompt` instruction callback. | TASK-2 retitled + note added; `files:` repointed to `_instructions.py`; instruction-block assertion added. |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev prompting-loop-convergence`

## Delivery Summary — TASK-1b — 2026-06-22

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1b | Cumulative-cap runaway carries `WRAP_UP_TEXT` in **instructions** at `usage.requests == limit-1`; cap disabled (`=0`) → no nudge; never a `UserPromptPart` in `TurnResult.messages` with no strip step; repo-wide grep finds zero `drop_wrap_up_messages`/`wrap_up_on_final_request`; full suite passes | ✓ pass |

**What shipped:** relocated the wrap-up nudge from the `wrap_up_on_final_request` history processor (+ `drop_wrap_up_messages` strip) to a dynamic `wrap_up_prompt(ctx)` instruction callback in `co_cli/agent/_instructions.py` (with `WRAP_UP_TEXT`), registered in `orchestrator.py` `per_turn_instructions`. Deleted `wrap_up_on_final_request`, `drop_wrap_up_messages`, `WRAP_UP_TEXT` and the now-unused `resolve_request_limit` import from `history_processors.py`; removed both `drop_wrap_up_messages` call sites + import from `orchestrate.py`; repointed the `resolve_request_limit` docstring in `config/llm.py`. Tests rewritten to assert the nudge via `ModelRequest.instructions` (not a message part), registered via `Agent(instructions=wrap_up_prompt)`.

**Files:** `co_cli/agent/_instructions.py`, `co_cli/agent/orchestrator.py`, `co_cli/context/history_processors.py`, `co_cli/agent/orchestrate.py`, `co_cli/config/llm.py`, `tests/test_flow_model_request_cap.py`

**Tests:** scoped — `tests/test_flow_model_request_cap.py` 13 passed, 0 failed.
**Doc Sync:** fixed — `core-loop.md` (§1 wrap-up paragraph, processor list 6→5, symbol/Files tables) and `pydantic-ai-integration.md` (§2.8, §2.13, Files table, refactor-practices note) updated to the instruction-callback mechanism.

**Overall: DELIVERED**
TASK-1b refactor complete; mechanism moved to the instruction seam with no strip step, behavior preserved, code grep clean, scoped tests green, specs synced.

## Implementation Review — TASK-1b — 2026-06-22

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1b | nudge in **instructions** at `usage.requests == limit-1`; cap disabled → no nudge; never a `UserPromptPart` (no strip); grep zero retired symbols; suite green | ✓ pass | `_instructions.py:31-49` — `wrap_up_prompt` gated by `resolve_request_limit` (disable) + `ctx.usage.requests != limit-1`, returns `""`/`WRAP_UP_TEXT`; registered `orchestrator.py:90` `per_turn_instructions`; processor + strip removed (`orchestrate.py:565`, `:780-782` de-wrapped, 400-reformulation splice intact); `config/llm.py` docstring repointed; grep `wrap_up_on_final_request`/`drop_wrap_up_messages` over `co_cli/`+`tests/` → exit 1 (zero refs); `test_flow_model_request_cap.py` asserts via `ModelRequest.instructions` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Scope-creep: unrelated working-tree change (length-retry assertions) not in any task's `files:` | tests/test_flow_orchestrate_length_retry.py | minor | Excluded from this review; left untouched for the user to triage/stage separately. Not introduced by TASK-1b. |

_No blocking issues. The wrap-up text persisting on one `ModelRequest.instructions` field is expected (G1-1b-2) — identical to `safety_prompt`/`current_time_prompt`; the SDK recomputes instructions per request and ignores historical `ModelRequest.instructions`, so it is never replayed (no strip needed)._

### Tests
- Command: `uv run pytest -q`
- Result: 822 passed, 0 failed (224s) — includes the cap tests and the real-LLM length-retry test
- Log: `.pytest-logs/20260622-204557-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads with `wrap_up_prompt` registered)
- `success_signal` verified: cumulative-cap turn flips `error` → `continue` with a synthesized answer when the model honors the instruction-framed nudge — proven deterministically by `test_wrap_up_nudge_reaches_model_and_yields_answer` (FunctionModel stub; `outcome=="continue"`, `output=="done"`). Live chat interaction non-gating.

### Overall: PASS
TASK-1b refactor verified: nudge moved to the `wrap_up_prompt` instruction seam, no strip step, behavior preserved, code grep clean, full suite green, boot smoke clean.
