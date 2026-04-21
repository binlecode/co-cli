# Plan: Fix Compaction System Gaps

Created: 2026-04-21  
Slug: compaction-gap-fixes  
Source: deep code scan comparing co-cli vs hermes-agent; pydantic-ai internals verified at `.venv/lib/python3.12/site-packages/pydantic_ai/`

---

## Product Intent

### Goal
Fix three functional gaps in the compaction pipeline that either silently corrupt context quality or
produce terminal session failures. All three were found by tracing the full compaction call graph,
verifying pydantic-ai internals directly, and confirming each gap against real code paths.

### Functional areas
- `co_cli/context/_history.py` — M0, M3, overflow recovery, boundary planner
- `co_cli/context/orchestrate.py` — preflight injection, turn loop, overflow handler

### Non-goals
- No new compaction mechanisms (no auxiliary summarizer, no session rollover)
- No changes to the summarizer prompt or summary format
- No config-knob additions beyond what the fixes require

### Success criteria
1. Safety/recall content is delivered via `@agent.system_prompt(dynamic=True)` — never appears in
   `turn_state.current_history`; each turn's OTel span records what was injected
2. M0 hygiene trigger uses `max(estimate, reported)` — same accuracy as M3
3. Overflow recovery retries with a reduced tail fraction before declaring terminal
4. All existing tests pass; regression tests added for each fix

### Status
Planning — not started

### Known gaps (evidence-first)

---

## Gap 1 — Safety/recall injections accumulate across turns

### Evidence

`orchestrate.py:552-554` docstring states:
> "pydantic-ai never persisted the processor output back to the stored message list"

This is factually wrong. Verified in installed pydantic-ai:

```
capabilities/history_processor.py:36
  request_context.messages = await _run_history_processor(processor, ctx, request_context.messages)

_agent_graph.py:845
  ctx.state.message_history[:] = messages   # replaces capture_run_messages with processor output
```

`ctx.state.message_history` IS `capture_run_messages`. `all_messages()` returns `capture_run_messages`.
So processor output (including preflight injections) IS written back permanently.

Flow each turn:
1. `preflight_history = [*current_history, *safety_msgs, recall_msg]` (`orchestrate.py:560-580`)
2. M3 anchors its tail to the last `UserPromptPart` (which is the new user request, appended at
   `_agent_graph.py:782`) — safety/recall fall inside the protected tail
3. After run: `all_messages() = [*M3_output_including_tail, model_response]`
4. `turn_state.current_history = all_messages()` (`orchestrate.py:645`)
5. Next turn: `current_history` already contains last turn's safety/recall; a new batch is appended

**Result:** N turns × (recall ~500 tokens) of stale injections accumulate in `current_history`.
The docstring "ephemeral" only applies to retry iterations within the same turn's `while` loop —
not across turn boundaries. Stale recall from 10 turns ago remains visible to the model.

### Proposed fix

Move safety/recall OUT of `preflight_history` entirely and register them as **dynamic system prompt
functions** via pydantic-ai's `@agent.system_prompt(dynamic=True)`.

**How pydantic-ai dynamic system prompts work** (verified at `_agent_graph.py:273-281`):
- At turn entry, `_sys_parts()` creates slots for each registered system prompt function.
- Each turn, `_reevaluate_dynamic_prompts()` calls every function marked `dynamic=True` and
  updates its `SystemPromptPart` in-place inside the FIRST `ModelRequest` slot.
- This is NOT a new message appended to history — it is an update of an existing slot that was
  written at session start.
- `all_messages()` never accumulates additional `SystemPromptPart`-only messages across turns.

**Fix:**
1. Register a `@agent.system_prompt(dynamic=True)` function for **recall** (date + personality
   memories). Called each turn; returns current date and retrieved memories. pydantic-ai replaces
   the slot content in-place — no accumulation.
2. Register a `@agent.system_prompt(dynamic=True)` function for **safety** (doom-loop / shell-error
   warnings). Returns the warning text when the condition is active, empty string otherwise.
   Because the function runs every turn, the warning is present whenever the condition holds —
   no one-shot flag needed. Remove `doom_loop_warning_issued` and
   `shell_error_streak_warning_issued` flags from `orchestrate.py`.
3. Remove safety/recall injection from `_run_model_preflight` entirely. `preflight_history` becomes
   `current_history` with no appended injections.

**Observability implication:** recalled memory and active safety state will no longer appear in the
stored transcript (they live in the dynamic system prompt slot, not in message history). Add an
OTel trace span at each dynamic function invocation recording the injected content, so that
`co traces` provides the per-turn decision context that the transcript no longer holds.

**Comparison with peers:**
- hermes-agent: `api_msg = msg.copy()` — injects into copy, persists clean original
- fork-claude-code: `prependUserContext()` with `isMeta: true` — synthetic message for API only
- pydantic-ai native: `@agent.system_prompt(dynamic=True)` — in-place slot update, no copy needed
  The native approach is strictly cleaner: no copy, no flag, no stripping.

### Files to change
- `co_cli/context/orchestrate.py` — register two `@agent.system_prompt(dynamic=True)` functions
  (recall, safety); remove `_run_model_preflight` injection logic; remove one-shot safety flags
- `co_cli/context/_history.py` — `build_recall_injection` and `build_safety_injection` become
  plain functions returning `str` (not `list[ModelMessage]`), called by the dynamic prompt functions
- `co_cli/observability/` — add OTel trace spans in each dynamic prompt function capturing injected
  content per turn

### Tests to add
- After two turns with recall enabled, `turn_state.current_history` contains NO
  `SystemPromptPart`-only `ModelRequest` objects — recall content is absent from committed history
- Safety warning appears in what is sent to the LLM every turn the condition is active (not just
  the first turn) and disappears once the condition clears
- Both one-shot flags removed — verify no `doom_loop_warning_issued` or
  `shell_error_streak_warning_issued` attributes remain on any state object

---

## Gap 2 — M0 hygiene trigger uses char estimate only; can miss under anti-thrashing

### Evidence

`_history.py:881-882` (inside `maybe_run_pre_turn_hygiene`):
```python
token_count = estimate_message_tokens(message_history)  # chars / 4 only
if token_count <= int(budget * deps.config.compaction.hygiene_ratio):
    return message_history
```

M3 (`summarize_history_window:803-805`) uses:
```python
estimate = estimate_message_tokens(messages)
reported = latest_response_input_tokens(messages)
token_count = max(estimate, reported)
```

For code/JSON/shell-heavy sessions, `chars/4` underestimates actual tokens by 1.3–1.8×.
Failure scenario:
1. Actual token count: 92% of budget. Char estimate: 76%.
2. M0 gate: 76% < 88% — does NOT fire.
3. M3 gate: max(76%, 92%) = 92% > 75% — DOES fire, compacts, persists.
4. Session grows. M3 anti-thrashing gate fires (two consecutive low-yield M3 runs).
5. M3 blocked. M0 still doesn't fire (char estimate still below 88%).
6. Session grows to overflow. One-shot recovery is the only safety net.
7. If recovery's tail alone exceeds budget → terminal.

M0's docstring at line 871 acknowledges this:
> "Uses rough token estimate only — no provider-reported count is available pre-turn."

The comment is correct — at turn entry, no API-reported count from the current turn exists yet.
But the LAST turn's `usage.input_tokens` IS available via `turn_state.latest_usage`.

### Proposed fix

In `run_turn()`, before calling `maybe_run_pre_turn_hygiene`, read the last reported token count
from `deps.runtime.turn_usage` (which is updated at `orchestrate.py:372` after each segment via
`_merge_turn_usage`). Pass it to `maybe_run_pre_turn_hygiene` as a hint.

Inside `maybe_run_pre_turn_hygiene`, compute:
```python
reported_hint = reported_input_tokens  # from previous turn's usage, 0 if unknown
token_count = max(estimate_message_tokens(message_history), reported_hint)
```

This mirrors M3's `max(estimate, reported)` logic. The reported count is one turn stale (it's the
count the provider used for the previous turn) but is still a better floor than char estimate alone.

Edge case: first turn has no reported count → `reported_hint = 0` → falls back to char estimate,
same behavior as today. No regression.

### Files to change
- `co_cli/context/_history.py` — `maybe_run_pre_turn_hygiene` signature adds
  `reported_input_tokens: int = 0`; uses `max(estimate, reported_input_tokens)` for trigger
- `co_cli/context/orchestrate.py` — `run_turn` reads `deps.runtime.turn_usage.input_tokens`
  (or 0 if None) and passes it to `maybe_run_pre_turn_hygiene`

### Tests to add
- `maybe_run_pre_turn_hygiene` fires when `reported_input_tokens` pushes the effective count
  above the hygiene threshold, even when char estimate is below it
- First-turn (no prior usage) falls back to char estimate only — no regression

---

## Gap 3 — Overflow recovery is one-shot with no fallback compaction intensity

### Evidence

`orchestrate.py:667-691`:
```python
if not turn_state.overflow_recovery_attempted:
    turn_state.overflow_recovery_attempted = True
    compacted = await recover_overflow_history(...)
    if compacted is not None:
        turn_state.current_history = compacted
        continue
# terminal — second overflow OR compacted is None
```

`recover_overflow_history` calls `plan_compaction_boundaries` with the same `tail_fraction`
(default 0.40) as M3. If the first attempt fails (returns `None`) OR the compacted result still
overflows, the session is terminal immediately.

No attempt is made at a more aggressive compaction (smaller tail fraction). For a session where
the tail is 42% of budget (just over the 40% threshold), the first recovery produces a history
that still overflows. The session dies without any retry.

### Proposed fix

Add a fallback retry inside the overflow recovery handler with a halved `tail_fraction`:

```python
if not turn_state.overflow_recovery_attempted:
    turn_state.overflow_recovery_attempted = True
    # First attempt: normal tail_fraction
    compacted = await recover_overflow_history(ctx, history, tail_fraction_override=None)
    if compacted is not None:
        turn_state.current_history = compacted
        continue
    # Second attempt: aggressive tail_fraction (half)
    compacted = await recover_overflow_history(
        ctx, history,
        tail_fraction_override=deps.config.compaction.tail_fraction / 2.0
    )
    if compacted is not None:
        turn_state.current_history = compacted
        frontend.on_status("Context overflow — aggressive compaction applied.")
        continue
# Only now: terminal
```

`recover_overflow_history` signature adds `tail_fraction_override: float | None = None` and passes
it to `plan_compaction_boundaries`. The second attempt can also use a lower floor on
`_MIN_RETAINED_TURN_GROUPS` if needed (keep ≥1 group, same invariant).

The aggressive retry does NOT attempt a second LLM summarization call — it uses the same
`_summarize_dropped_messages` path. Total cost: at most 2 LLM summarization calls per overflow.

### Files to change
- `co_cli/context/_history.py` — `recover_overflow_history` accepts `tail_fraction_override`
  parameter, threads it through to `plan_compaction_boundaries`
- `co_cli/context/orchestrate.py` — overflow handler calls recovery twice (normal then aggressive)
  before declaring terminal

### Tests to add
- Overflow recovery with an oversized tail: first attempt returns `None`, second attempt with
  halved tail_fraction succeeds → turn continues
- Both attempts return `None` → still terminal (no infinite loop)

---

## Implementation Tasks

### Task 1 — Gap 1: Replace preflight injection with dynamic system prompts ✓ DONE
**Files:** `co_cli/context/orchestrate.py`, `co_cli/context/_history.py`, `co_cli/observability/`  
**Complexity:** Medium (pydantic-ai API change, removes one-shot flags, adds OTel spans)

Steps:
1. In `co_cli/context/_history.py`, refactor `build_recall_injection` and `build_safety_injection`
   to return `str` instead of `list[ModelMessage]` — plain text the dynamic prompt functions return
2. In `co_cli/context/orchestrate.py` (or `co_cli/agent.py` where the agent is built), register:
   ```python
   @agent.system_prompt(dynamic=True)
   async def recall_prompt(ctx: RunContext[CoDeps]) -> str:
       return await build_recall_injection(ctx)  # date + personality memories

   @agent.system_prompt(dynamic=True)
   async def safety_prompt(ctx: RunContext[CoDeps]) -> str:
       return await build_safety_injection(ctx)  # "" when no active condition
   ```
3. Remove safety/recall injection from `_run_model_preflight`; `preflight_history` becomes
   `current_history` with no appended messages
4. Remove `doom_loop_warning_issued` and `shell_error_streak_warning_issued` flags from
   `TurnState` (or wherever they live) — safety fires every turn the condition is active
5. In each dynamic prompt function, emit an OTel trace span with the injected content:
   ```python
   with tracer.start_as_current_span("dynamic_prompt.recall") as span:
       span.set_attribute("content", content)
   ```
6. Write tests

### Task 2 — Gap 2: M0 uses max(estimate, reported) for trigger ✓ DONE
**Files:** `co_cli/context/_history.py`, `co_cli/context/orchestrate.py`  
**Complexity:** Low

Steps:
1. Add `reported_input_tokens: int = 0` parameter to `maybe_run_pre_turn_hygiene`
2. Use `max(estimate, reported_input_tokens)` for the trigger comparison
3. In `run_turn`, read `deps.runtime.turn_usage.input_tokens if deps.runtime.turn_usage else 0`
   and pass to `maybe_run_pre_turn_hygiene`
4. Write tests

### Task 3 — Gap 3: Two-attempt overflow recovery ✓ DONE
**Files:** `co_cli/context/_history.py`, `co_cli/context/orchestrate.py`  
**Complexity:** Low-medium

Steps:
1. Add `tail_fraction_override: float | None = None` to `recover_overflow_history`
2. Thread override through to `plan_compaction_boundaries` call
3. In overflow handler in `run_turn`: after first `None` result, retry with
   `tail_fraction_override=deps.config.compaction.tail_fraction / 2.0`
4. Write tests for both success and double-None cases

---

## Delivery Order

Tasks 2 → 1 → 3

Rationale:
- Task 2 is low-risk, purely additive parameter with backwards-compatible default
- Task 1 has the widest impact (dynamic system prompt refactor) — do after 2 is green
- Task 3 adds retry logic to the overflow handler — do after 1 to have clean test baseline

---

## Open Questions

1. **Gap 1 — RESOLVED:** Use `@agent.system_prompt(dynamic=True)`. Verified at
   `_agent_graph.py:273-281`: pydantic-ai updates the dynamic slot in-place each turn, never
   appends. Identity-strip (Option B) and `instructions=` (Option A) are both superseded.

2. **Gap 3 — aggressive tail fraction — RESOLVED:** `max(halved, 0.15)` is redundant. In the
   overflow recovery context, the priority is aggressive compaction to avoid terminal failure, not
   context quality. `plan_compaction_boundaries` already enforces safety via the soft-overrun
   multiplier (`1.25 × tail_fraction`) and `_MIN_RETAINED_TURN_GROUPS=1`, which together guarantee
   at least one turn group is always kept regardless of how small `tail_fraction` becomes. No floor
   guard is needed.
