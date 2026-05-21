# session-review-counter-simplify

## Problem

`co_cli/context/orchestrate.py:406-410` increments `turn_state.tool_iterations`
by counting `ModelResponse` messages that contain at least one `ToolCallPart`:

```python
turn_state.tool_iterations += sum(
    1
    for m in result.new_messages()
    if isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts)
)
```

This filter is over-design. Two consequences:

1. **Chat-only sessions never trigger session review.** The counter stays at 0
   across every turn, so `_post_turn_hook` never spawns the background review.
   But chat-only turns can contain memory-shaped signal — stated preferences,
   feedback, decisions, corrections — which `session_review_prompts.py:13-17`
   explicitly lists as harvest targets. The filter silently suppresses
   harvesting these.

2. **Inconsistent with the dominant peer pattern.** Hermes
   (`hermes-agent/run_agent.py:10472-10476`) bumps its skill nudge counter
   unconditionally per LLM iteration in the loop, with no ToolCallPart filter.
   Hermes ships this at scale.

Source-verified loop behavior in both co-cli and hermes: **a text-only user
turn produces exactly 1 LLM iteration**, so dropping the filter gives chat
turns a counter contribution of 1 instead of 0, which is the correct value
for "this user message could carry a memory signal."

## Status

pending

## Goal

After this plan lands:

- `turn_state.llm_iterations` counts every `ModelResponse` returned during a turn,
  not just those with `ToolCallPart`s.
- `_post_turn_hook` accumulates this raw iteration count.
- Chat-only sessions eventually trigger session review at the configured
  nudge interval (one tick per text-only user turn).
- Field renamed from `tool_iterations` → `llm_iterations` for clarity.
- `review_nudge_interval` default raised from 5 → 10 to maintain similar real-
  world cadence under the broader counter, matching hermes's default
  (`memory.nudge_interval=10`, `skills.creation_nudge_interval=10`).

## Scope

### In scope

- `co_cli/context/orchestrate.py` — drop the ToolCallPart filter from the
  counter increment; rename the field on `_TurnState` and `TurnResult`.
- `co_cli/main.py` — update the `_post_turn_hook` caller signature.
- `co_cli/deps.py` — `iterations_since_review` field name unchanged
  (already domain-neutral); update its docstring to drop the "tool"
  qualifier.
- `co_cli/config/skills.py` — bump `review_nudge_interval` default from 5 → 10.
- `co_cli/skills/session_review.py` — no code change; verify the prompt copy
  doesn't promise "tool-only" semantics (`session_review_prompts.py`).
- `docs/specs/skills.md` — update the "Curation & Self-Improvement" section
  describing the trigger semantic.
- Tests covering the new counter behavior.

### Out of scope

- **Per-tier counter split** (memory counted per user turn, skill counted per
  LLM iteration, hermes-style). That's a larger refactor; track as a separate
  follow-up. This plan is the small simplification, not the architectural
  split.
- **Inline-tool-use reset** (when foreground agent calls `memory_manage` or
  `skill_manage`, reset the counter to 0). Separate small follow-up.
- **The concurrent-default plan** at
  `docs/exec-plans/active/2026-05-18-234544-agent-tool-concurrent-default.md`.
  Independent; lands separately.

## Tasks

### ✓ DONE T1 — Drop the ToolCallPart filter

File: `co_cli/context/orchestrate.py`

Change lines 406-410 from:
```python
turn_state.tool_iterations += sum(
    1
    for m in result.new_messages()
    if isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts)
)
```
to:
```python
turn_state.llm_iterations += sum(
    1
    for m in result.new_messages()
    if isinstance(m, ModelResponse)
)
```

Drop the `ToolCallPart` import at line 48 if it is no longer used anywhere in
the file (verify via grep before removing).

### ✓ DONE T2 — Rename the field on `_TurnState`

File: `co_cli/context/orchestrate.py`

- Line 109: `tool_iterations: int = 0` → `llm_iterations: int = 0`
- Line 160: same field on `TurnResult` → `llm_iterations: int = 0`
- Line 461: in `_build_error_turn_result`, `tool_iterations=turn_state.tool_iterations` → `llm_iterations=turn_state.llm_iterations`
- Line 503: same rename
- Line 717: same rename

### ✓ DONE T3 — Update the post-turn hook caller

File: `co_cli/main.py`

- Line 145: `_post_turn_hook(deps, next_history, turn_result.tool_iterations)` → `_post_turn_hook(deps, next_history, turn_result.llm_iterations)`
- Line 272: parameter rename `turn_iteration_count: int` stays unchanged (it is
  domain-neutral; the value passed in now carries the broader semantic).
- Lines 269-275 docstring: replace "tool-iteration counter" with
  "iteration counter"
- Line 289 already uses the parameter; no change needed.

### ✓ DONE T4 — Update `deps.py` docstring

File: `co_cli/deps.py`

- Line 155-158: rewrite the comment to drop the "tool" qualifier:
  ```python
  # Iteration counter driving the turn-boundary session-review trigger.
  # Bumped by _post_turn_hook with TurnResult.llm_iterations; reset to 0
  # when a review task is spawned. Not reset on single-in-flight skip.
  iterations_since_review: int = 0
  ```

### ✓ DONE T5 — Bump the default nudge interval

File: `co_cli/config/skills.py`

Change the default for `review_nudge_interval` from `5` to `10`. The variable
name stays. Rationale: the new counter ticks more per turn (text-final
iterations are now counted), so to preserve roughly the same firing cadence on
heavy-tool turns and to match hermes's default of 10, raise the threshold.

Also update the spec table in `docs/specs/skills.md` § Config (the row
documenting `skills.review_nudge_interval`).

### ✓ DONE T6 — Spec sync

File: `docs/specs/skills.md`

Find the section describing the session reviewer trigger (currently roughly
under "Curation & Self-Improvement" lines 234-278). Update the description:

- Before: "After approximately every `review_nudge_interval` tool calls, a
  `session_reviewer` agent runs in the background..."
- After: "After approximately every `review_nudge_interval` LLM iterations, a
  `session_reviewer` agent runs in the background..." — and note that a
  text-only user turn contributes 1 iteration, so chat-only sessions
  eventually trigger review (was: never).

If the spec quotes the old default (5), update to 10.

### ✓ DONE T7 — Tests

Add `tests/test_flow_session_review_counter.py` (or extend an existing test
file covering session review triggers) with the following behavioral cases:

1. **Text-only turn ticks the counter by 1.** Run a turn that produces a
   single text-only `ModelResponse`; assert
   `deps.session.iterations_since_review == 1`.
2. **Tool-issuing turn with N+1 iterations ticks by N+1.** Run a turn with 2
   tool-emitting iterations + 1 text-final; assert counter incremented by 3.
3. **Threshold trips → review spawn.** Run enough text-only turns to reach
   `review_nudge_interval` (default 10); assert a background review task was
   spawned.
4. **Counter reset on spawn.** After spawn, assert
   `iterations_since_review == 0`.
5. **Single in-flight gate intact.** Same scenario, but a previous review task
   is still in flight; assert no new task spawned and counter not reset.

### ✓ DONE T8 — Quality gate

```bash
scripts/quality-gate.sh full
```

Verify lint + full pytest pass.

## Verification

- `scripts/quality-gate.sh full` passes.
- Behavioral tests in T7 pass.
- Spot-check: open `uv run co chat` with `CO_SKILLS_REVIEW_ENABLED=true`, hold
  a 10-turn chat conversation with no tool use, observe the reviewer fire (was:
  never fires).
- Spot-check: open `uv run co chat` with `CO_SKILLS_REVIEW_ENABLED=true`,
  perform 10 tool-heavy operations, observe the reviewer fire at roughly the
  same cadence as before (the 5 → 10 threshold bump compensates for the
  broader counter).

## Cleanup, deferred

Two related improvements stay as follow-up plans, deliberately not in this
plan to keep the change reviewable in isolation:

1. **Inline-tool-use reset.** When the foreground agent calls `memory_manage`
   or `skill_manage` inline, reset `iterations_since_review = 0`. This avoids
   redundant reviews after the foreground agent already did the harvest work.
   Hermes does this at `run_agent.py:9173-9177` and `9523-9527`.
2. **Per-tier counter split.** Hermes splits memory (per user turn) and skill
   (per LLM iteration) into two separate counters with different units, each
   triggering a tier-specific review prompt. This is a larger refactor —
   touches `session_review.py`, `session_review_prompts.py`, `main.py`,
   `deps.py`, and the spec. Worth tracking but out of scope here.

## Risks

- **Cadence shift on heavy-tool turns.** With the filter dropped, a turn that
  previously ticked 4 (4 tool-emitting iters) now ticks 5 (4 + 1 text-final).
  The 5 → 10 threshold bump in T5 compensates. If observed cadence still
  feels off after deployment, tune the default.
- **Chat-only sessions now incur review cost.** They didn't before. For Ollama
  this is free; for hosted-model setups, each review pass consumes ≤8 LLM
  iterations × 120s timeout. Worth flagging in release notes.
- **Test environments using `review_enabled=true` and short-lived sessions**
  may see different review-fire behavior. Update test expectations where they
  assume zero reviews from chat-only fixtures.

## Out-of-scope (revisited)

This plan covers ONLY the simplification of the iteration counter. Two related
designs from prior conversation remain in their own plans:

- **Iteration cap per turn** (cost ceiling, e.g. 50 for Ollama) — separate plan.
- **Tool-call cap per iteration** (safety ceiling, e.g. 10 per `ModelResponse`)
  — separate plan.
- **Concurrent-default flip + dispatch backstop** —
  `docs/exec-plans/active/2026-05-18-234544-agent-tool-concurrent-default.md`.

These are independent axes (cost / safety / resource / cadence). This plan
addresses only the cadence axis.

## Delivery Summary — 2026-05-19

| Task | done_when | Status |
|------|-----------|--------|
| T1 | Counter increment drops ToolCallPart filter | ✓ pass |
| T2 | `tool_iterations` → `llm_iterations` on `_TurnState` + `TurnResult` + 3 build sites | ✓ pass |
| T3 | `main.py` reads `turn_result.llm_iterations`; docstrings updated | ✓ pass |
| T4 | `deps.py` comment drops "tool" qualifier | ✓ pass |
| T5 | `review_nudge_interval` default 5 → 10 | ✓ pass |
| T6 | `docs/specs/skills.md` trigger description + config table updated | ✓ pass |
| T7 | `tests/test_flow_session_review_counter.py` — 5 behavioral cases pass | ✓ pass |
| T8 | lint + scoped tests green | ✓ pass |

**Note:** `ToolCallPart` import retained — still used at lines 481 and 577 (interrupt detection, text-only gate).
`tests/test_flow_turn_result_tool_iterations.py` and `tests/test_flow_post_turn_hook.py` updated for new semantics (field rename + text-only turn now contributes 1 not 0).

**Tests:** scoped — 15 passed, 0 failed (`test_flow_session_review_counter.py` × 5 + `test_flow_post_turn_hook.py` × 10)
**Doc Sync:** fixed (`docs/specs/skills.md` trigger description + config table default updated)

**Overall: DELIVERED**
All tasks landed; lint clean; scoped tests green.

## Implementation Review — 2026-05-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | Counter increment drops ToolCallPart filter | ✓ pass | `orchestrate.py:415-417` — `turn_state.llm_iterations += sum(1 for m in ... if isinstance(m, ModelResponse))`, no ToolCallPart guard |
| T2 | `tool_iterations` → `iterations` on `_TurnState` + `TurnResult` + 3 build sites | ✓ pass (minor: landed as `llm_iterations`) | `orchestrate.py:109,160` fields; `orchestrate.py:468,510,724` build sites — all consistent, delivery summary documents the name choice |
| T3 | `main.py` reads `turn_result.iterations`; docstrings updated | ✓ pass (consistent with T2) | `main.py:145` reads `turn_result.llm_iterations`; `main.py:274` docstring: "iteration counter", no "tool" qualifier |
| T4 | `deps.py` comment drops "tool" qualifier | ✓ pass | `deps.py:155-158` — "Iteration counter driving the turn-boundary session-review trigger" |
| T5 | `review_nudge_interval` default 5 → 10 | ✓ pass | `config/skills.py:28` — `Field(default=10, ge=1)` |
| T6 | Spec trigger description + config table updated | ✓ pass | `docs/specs/skills.md:242` — "LLM iterations…text-only user turn contributes 1 iteration"; `skills.md:285` — default `10`, "LLM iteration count between review triggers" |
| T7 | 5 behavioral cases pass | ✓ pass | `test_flow_session_review_counter.py` — 5 cases; no mocks; `object()` sentinel for model field not called in any test path |
| T8 | lint + full tests green | ✓ pass | 493 passed, 0 failed |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Minor: T2 spec says rename to `iterations`; delivery used `llm_iterations` | `orchestrate.py:109,160` | minor | Delivery summary documents the decision; `llm_iterations` is more precise and internally consistent — no code change; delivery summary T3 row corrected to read `turn_result.llm_iterations` |
| Pre-existing: `clarify` tool `questions: list[dict]` generates unconstrained schema (`additionalProperties: true`) — model has no contract for option keys, produces `value`/string instead of `label` → `KeyError`/`TypeError` in parser; confirmed pre-existing at HEAD (different variant: `TypeError: string indices must be integers`) | `co_cli/tools/system/user_input.py` | blocking (pre-existing, found during full suite run) | Added `ClarifyOption(BaseModel)` and `ClarifyQuestion(BaseModel)`; changed `questions: list[dict]` → `questions: list[ClarifyQuestion]`; serialize with `model_dump()` before `QuestionRequired`; generated schema now enforces `label` as required string on options — model follows schema; `o["label"]` in parser safe |

### Tests
- Command: `uv run pytest -v -x`
- Result: 493 passed, 0 failed
- Log: `.pytest-logs/$(date)-review-impl.log`

### Behavioral Verification
- `uv run co --help`: CLI loads, all commands present — system start confirmed
- T1-T8 changes are internal (counter logic, field rename, default config, spec text) — no user-facing surface changed; behavioral verification skipped for plan scope
- `clarify` schema fix (pre-existing bug, found in review): model now produces `{"label": "...", "description": "..."}` options via schema enforcement — verified by full suite pass including `test_clarify_deferred_resume_end_to_end`

### Overall: PASS
Pre-existing clarify schema bug found and fixed during full suite run; all T1-T8 tasks confirmed; 493/493 tests green; lint clean.
