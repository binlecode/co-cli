Task type: bugfix + doc-sync + rename

# Plan: compaction-followup-fixes

## Context

Post-ship trace of the compaction system (against `docs/specs/compaction.md`, delivered in `docs/exec-plans/completed/2026-04-17-163453-compaction-refactor-from-peer-survey.md`) surfaced seven issues, ranging from a real latent bug (circuit breaker with no recovery path) to spec-adjacent misnames (`inject_opening_context` implies prepend but appends) to a cache-design invariant that isn't codified anywhere.

The trace and fix triage are in the conversation that produced this plan. Summary:

- **Issue 1 (blocking):** Circuit breaker at `_history.py:529-533` returns `None` before the LLM attempt; the reset at `:549` is unreachable once tripped. `compaction_failure_count` is explicitly cross-turn (`deps.py:141-144`). Once at 3, tripped for the whole process — spec §2.9 "future success resets the counter" is unreachable.
- **Issue 5 (doc):** Proactive compaction returning `None` silently hands off to overflow recovery via the provider 400. Correct behavior, undocumented.
- **Issue 6 (name):** `inject_opening_context` actually appends at the message tail (`_history.py:755`: `return [*messages, injection]`). The name hides that the cache-correct behavior is already in place.
- **Issue 7 (invariant):** `@agent.instructions` layers (`agent/_core.py:155-158`) are concatenated into the static system-prompt block. If any of them mutates mid-session, the entire cacheable prefix invalidates. `add_personality_memories` is the suspect — it reads from a mutable memory store. No codified invariant anywhere in the spec.
- **Issues 2, 3, 4, 8:** minor / defer-until-evidence.

This plan is the post-triage scope: four ship-ready fixes (Issues 1, 5, 6, 7), the rest captured as out-of-scope with rationale.

## Problem & Outcome

**Real bug:**
- Circuit breaker has no recovery path. Spec language describes a reset condition that is structurally unreachable. Either the code is wrong (needs periodic probe) or the spec is wrong (kill-switch semantics).

**Cache-design leak:**
- The system prompt accepts dynamic content via `@agent.instructions`. `add_personality_memories` is the concrete hazard: it loads from a store that can mutate during a session, so personality recalls mid-session invalidate the static-prompt cache. No architectural invariant prevents new cache-busters from being added.

**Spec/code drift (minor):**
- Proactive→overflow implicit handoff is undocumented.
- `inject_opening_context` name actively misleads readers about cache behavior.

**Outcome:**
- Circuit breaker has a documented, tested recovery path.
- Dynamic content is provably append-only to the message tail; the invariant is codified in `docs/specs/context.md` and enforced by the placement of the four dynamic instruction layers.
- Processor name matches behavior.
- Spec describes the proactive→overflow handoff explicitly.

## Scope

In scope:
- **TASK-1:** Circuit breaker periodic probe (every 10 skipped attempts, try once) + spec clarification of the reset contract.
- **TASK-2:** Rename `inject_opening_context` → `append_recalled_memories`. Update all references.
- **TASK-3:** Audit and relocate cache-busting `@agent.instructions` layers. Codify the append-only invariant in `docs/specs/context.md`.
- **TASK-4:** Doc-only — describe the proactive→overflow handoff in `docs/specs/compaction.md` §2.9.

Out of scope (captured with rationale):
- **Issue 2 — `search_tools` name coupling.** Hardcoded string `"search_tools"` at `_history.py:573`. SDK rename would silently zero-op the dedup. Fix is cheap but risk is low (pydantic-ai has not touched this name across versions). Defer until evidence of SDK churn.
- **Issue 3 — estimator memoization.** `estimate_message_tokens` re-serializes `ToolCallPart.args` every processor pass. Potentially O(n²)-ish on long tool-heavy sessions. Defer — add a profiling probe to `evals/eval_compaction_quality.py` first; only optimize on evidence.
- **Issue 4 — sentinel prior-summary detection.** `_history.py:462-463` uses `startswith(_SUMMARY_MARKER_PREFIX)`. Already considered and rejected in the prior plan; no reproducible collision. Defer.
- **Issue 8 — explicit `cache_control: ephemeral` markers on compaction boundaries.** Provider-specific optimization (Anthropic-only) that would let the cache span across compaction events. Nice-to-have, not correctness. Future plan.

## Proposed Changes

### Change 1: Circuit breaker periodic probe

Current: `_history.py:529-533` short-circuits at `>= 3`. No path back to 0.

Proposed:

```python
# co_cli/context/_history.py

_CIRCUIT_BREAKER_PROBE_EVERY = 10
"""When circuit breaker is tripped (failure_count >= 3), attempt the LLM anyway
every Nth subsequent trigger. A success resets the counter to 0. Prevents
permanent bypass from a transient provider hiccup that happened to hit 3 in a
row early in the session."""


async def _summarize_dropped_messages(
    ctx: RunContext[CoDeps],
    dropped: list[ModelMessage],
    *,
    announce: bool,
) -> str | None:
    if not ctx.deps.model:
        return None

    count = ctx.deps.runtime.compaction_failure_count
    if count >= 3:
        # Skip most attempts; periodically probe.
        if (count - 3) % _CIRCUIT_BREAKER_PROBE_EVERY != 0:
            log.warning("Compaction: circuit breaker active (count=%d), static marker", count)
            ctx.deps.runtime.compaction_failure_count += 1  # keep counting for probe cadence
            return None
        log.info("Compaction: circuit breaker probe (count=%d)", count)
        # Fall through to attempt

    # ... existing try/except with reset on success ...
```

On success, `compaction_failure_count = 0` (unchanged). On failure during a probe, the counter keeps incrementing, so the next probe is exactly 10 attempts later.

Rationale: cheap defense-in-depth against a session that accidentally tripped the breaker early and would otherwise spend the rest of its lifetime with static markers only. Does not increase LLM cost in the steady failing case (1 probe per 10 attempts).

### Change 2: Rename `inject_opening_context` → `append_recalled_memories`

The current name implies the injection lands at the opening of the prompt. The implementation (`_history.py:755`: `return [*messages, injection]`) appends at the message tail — which is the cache-correct behavior. The name hides this and readers either mistake it for a cache-buster or miss the invariant.

Rename scope:
- Function definition `co_cli/context/_history.py:695`.
- Agent registration `co_cli/agent/_core.py:147`.
- All spec references: `docs/specs/context.md`, `docs/specs/core-loop.md`, `docs/specs/compaction.md` (diagrams and tables).
- Test references — grep for `inject_opening_context` in `tests/`.
- No doc of the rename in CHANGELOG; git log is the changelog.

### Change 3: Audit `@agent.instructions` for cache-busting; codify append-only invariant

Current layers (`co_cli/agent/_instructions.py`):

| Layer | Evaluated per | Variance | Cache impact |
|---|---|---|---|
| `add_current_date` | request | daily | one miss per day — fine |
| `add_shell_guidance` | request | literal string | none |
| `add_personality_memories` | request | file-based memory store, can mutate mid-session | **cache-buster when memory is updated** |
| `add_category_awareness_prompt` | request | `tool_index`, stable post-bootstrap | none in steady state |

`add_personality_memories` is the hazard. If a user saves or updates a `personality-context` memory mid-session, the static system prompt block changes on the next request → the full cacheable prefix invalidates.

**Fix:** move personality-memory content out of `@agent.instructions` and into a tail-appended `ModelRequest([SystemPromptPart(...)])` (the same mechanism `append_recalled_memories` uses). Place the processor right after `append_recalled_memories` in the chain — or fold into it with a combined payload.

**Invariant (to codify in `docs/specs/context.md` §2.1):**

> **Append-only invariant for dynamic content.**
>
> Any content that can vary within a single session MUST be appended to the tail of the message list via a history processor that returns `[*messages, injection]`. It MUST NOT be placed in `@agent.instructions` unless it is provably static within a session (example: `add_current_date` — changes once per day but not within a session in any practical case).
>
> Rationale: `@agent.instructions` output is concatenated into the static system-prompt block pydantic-ai sends to the provider. Providers cache the system-prompt block as the prefix of every request. Any per-request variance in that block invalidates the cache for the entire prefix, including fixed tool schemas and soul assets.
>
> New dynamic surfaces go in the tail. Audit every new `@agent.instructions` registration against this rule.

### Change 4: Document proactive → overflow handoff

Add to `docs/specs/compaction.md` §2.9 (error handling and degradation table, or immediately after it):

> **Proactive → overflow handoff.** When `plan_compaction_boundaries` returns `None` during proactive compaction (single-turn pressure, or a prior compaction already consumed the middle), `summarize_history_window` returns messages unchanged and the over-budget request is sent to the provider as-is. The provider rejects it with a context-length error, which `_is_context_overflow` detects and `run_turn` routes to `recover_overflow_history`. The overflow planner runs with the same `TAIL_FRACTION` and `min_groups_tail=1`; if it also returns `None`, the turn is terminal with "Context overflow — unrecoverable." This is intentional: proactive and overflow share one planner — if the planner cannot help, letting the provider reject the request is the correct escalation. Do not add a retry loop at the proactive layer.

## Behavioral Constraints

- Circuit breaker probe cadence MUST be a named module constant (`_CIRCUIT_BREAKER_PROBE_EVERY`), not settings-configurable.
- Rename of `inject_opening_context` MUST propagate to all test files, spec files, and the agent registration — grep must return zero references to the old name after the change.
- Moving `add_personality_memories` out of `@agent.instructions` MUST not change what reaches the model — only where in the message list it lives. Content and formatting are untouched.
- The append-only invariant SHALL NOT introduce a new history processor when an existing one can carry the payload. Prefer folding personality-memories into `append_recalled_memories` (one tail-appended `SystemPromptPart` per request) over adding a sixth processor.
- `add_current_date`, `add_shell_guidance`, `add_category_awareness_prompt` stay in `@agent.instructions` — none violate the invariant.

## High-Level Design

```
co_cli/context/_history.py
├── _CIRCUIT_BREAKER_PROBE_EVERY = 10                                # NEW
├── _summarize_dropped_messages(...)                                 # UPDATED: probe branch
├── summarize_history_window(...)                                    # unchanged
├── recover_overflow_history(...)                                    # unchanged
└── append_recalled_memories(...)                                    # RENAMED from inject_opening_context
    └── optionally consumes personality-memory content alongside     # UPDATED (Change 3)
        recalled-memory content in one tail-appended injection

co_cli/agent/_core.py
├── history_processors=[..., append_recalled_memories, ...]           # UPDATED: rename
└── @agent.instructions registrations minus add_personality_memories  # UPDATED: layer moved

co_cli/agent/_instructions.py
└── add_personality_memories  → removed from @agent.instructions;     # MOVED
    content now flows through the append-only processor

docs/specs/
├── context.md §2.1 — append-only invariant                           # NEW
├── compaction.md §2.9 — proactive→overflow handoff                   # NEW paragraph
├── context.md §2.2 — processor table: rename old name                # UPDATED
├── core-loop.md §2.4 — processor table: rename old name              # UPDATED
└── compaction.md diagrams — rename old name                          # UPDATED

tests/
└── update all inject_opening_context references                      # UPDATED
```

## Implementation Plan

- ### ✓ DONE — **TASK-1: Circuit breaker periodic probe**
  `files:` [co_cli/context/_history.py, docs/specs/compaction.md, tests/test_history.py]
  `done_when:`
  - `_CIRCUIT_BREAKER_PROBE_EVERY = 10` added as a named module constant with docstring.
  - `_summarize_dropped_messages` probes the LLM once every `_CIRCUIT_BREAKER_PROBE_EVERY` skipped attempts when `compaction_failure_count >= 3`.
  - Non-probe skips still increment the counter (so probe cadence is measurable).
  - Probe success resets the counter to 0 (existing path).
  - Spec §2.9 circuit-breaker table describes the probe cadence explicitly.
  - Tests: (a) with `compaction_failure_count = 3` and no probe due, LLM is not attempted and static marker returned; (b) with `compaction_failure_count = 3 + 10k` (probe due), LLM IS attempted; (c) probe success resets to 0; (d) probe failure increments (so next probe is exactly 10 later).
  `success_signal:` a session that hits the breaker early can still recover automatically; user sees "Compacting conversation..." again after ~10 skipped attempts instead of never.
  `prerequisites:` []

- ### ✓ DONE — **TASK-2: Rename `inject_opening_context` → `append_recalled_memories`**
  `files:` [co_cli/context/_history.py, co_cli/agent/_core.py, docs/specs/context.md, docs/specs/core-loop.md, docs/specs/compaction.md, tests/* (any referencing the old name)]
  `done_when:`
  - Function renamed in definition, docstring updated to "Append recalled memories at the message tail…" (name now matches behavior).
  - Agent registration updated.
  - All spec references updated.
  - `grep -r inject_opening_context .` returns zero results in `co_cli/`, `tests/`, and `docs/`.
  - All existing tests still pass under the new name.
  `success_signal:` reading the processor name tells you where the injection lands; no behavioral change.
  `prerequisites:` []

- ### ✓ DONE — **TASK-3: Move `add_personality_memories` out of `@agent.instructions`; codify the append-only invariant**
  `files:` [co_cli/agent/_core.py, co_cli/agent/_instructions.py, co_cli/context/_history.py (if folding into append_recalled_memories), docs/specs/context.md, tests/test_prompt_assembly.py, tests/test_history.py]
  `done_when:`
  - `add_personality_memories` is no longer registered via `agent.instructions(...)`.
  - Personality-memory content is emitted as part of the tail-appended `SystemPromptPart` produced by `append_recalled_memories` (preferred) OR a new processor registered as the last entry in `history_processors`.
  - What reaches the model (content + formatting) is equivalent to pre-change.
  - `docs/specs/context.md` §2.1 contains the "Append-only invariant for dynamic content" paragraph from Change 3.
  - Test: building `Agent` and observing the static instructions block shows no personality-memory content (it now lives in per-request injection).
  - Test: `append_recalled_memories` (or the new processor) correctly includes personality memories when `config.personality` is set.
  `success_signal:` modifying a personality-context memory mid-session no longer invalidates the prompt cache for the static prefix; only the tail changes.
  `prerequisites:` [TASK-2] (rename must land first so the processor has the final name)

- ### ✓ DONE — **TASK-4: Document proactive → overflow handoff**
  `files:` [docs/specs/compaction.md]
  `done_when:` §2.9 includes the "Proactive → overflow handoff" paragraph from Change 4. No code change.
  `success_signal:` a reader of `compaction.md` alone understands why `summarize_history_window` returns messages unchanged when the planner returns `None`, without having to read `orchestrate.py`.
  `prerequisites:` []

TASK-1, TASK-2, TASK-4 are independent. TASK-3 depends on TASK-2 (to avoid renaming inside an active refactor).

## Testing

### Unit

```python
# tests/test_history.py
@pytest.mark.asyncio
async def test_circuit_breaker_probes_every_n_skips():
    """count == 3 skips LLM; count == 13 probes; count == 14 skips again."""

@pytest.mark.asyncio
async def test_circuit_breaker_probe_success_resets_counter():
    """Probe attempt succeeds → compaction_failure_count == 0."""

@pytest.mark.asyncio
async def test_circuit_breaker_probe_failure_increments():
    """Probe attempt fails → counter +1, next probe at count+10."""

@pytest.mark.asyncio
async def test_append_recalled_memories_tail_position():
    """Injection is the last element of the returned message list."""

def test_personality_memories_not_in_static_instructions():
    """After TASK-3, build_static_instructions(config_with_personality) output does NOT
    contain personality memory content — it now lives in the append-only processor."""

@pytest.mark.asyncio
async def test_personality_memories_in_tail_injection():
    """append_recalled_memories (or the new processor) emits a SystemPromptPart at the tail
    containing personality memory content when personality is set."""
```

### Integration

- End-to-end run: trip the circuit breaker (force 3 consecutive failures in a test by substituting the summarizer agent via... wait — no fakes allowed. Use a real sub-agent setup with a deliberately bad model/settings? Or test the probe condition in isolation via the existing `test_circuit_breaker_skips_llm_after_three_failures` pattern with count=13 instead of 3). **Open question — see below.**

### Spec

- Read `docs/specs/context.md` and `docs/specs/compaction.md` end-to-end; every mention of `inject_opening_context` is gone; the append-only invariant appears once and is referenced by the processor descriptions.

## Open Questions

1. **Can we test the probe without fakes?** The existing test `test_circuit_breaker_skips_llm_after_three_failures` sets `compaction_failure_count = 3` directly and checks the no-LLM behavior. We can do the same for probes: set `compaction_failure_count = 13` and assert the LLM IS attempted (which with `ctx.deps.model = None` will exit at the first guard — so we need a real small model bound). Alternative: use `_LLM_MODEL` (same pattern as `test_circuit_breaker_skips_llm_after_three_failures:148`) and assert either a real response or a real exception — both are valid probe outcomes. Recommendation: direct-state tests, same shape as the existing breaker test.

2. **Should personality memories be folded into `append_recalled_memories` or live in a new processor?** Folding keeps the chain at 5 processors and avoids a new registration site. A new processor keeps concerns separate (knowledge recall vs personality continuity). Recommendation: **fold** — both emit identical shapes (tail-appended `SystemPromptPart`), and keeping the chain length stable reduces churn in the spec processor tables. Confirm during implementation; no blocker.

## Release Notes

None user-visible. Two subtle effects:

- Circuit breaker recovery: sessions that hit 3 consecutive compaction failures early will see compaction attempts resume roughly every 10 triggers instead of silently staying on static markers forever.
- Prompt cache hit rate: modifying a personality-context memory mid-session no longer invalidates the full cacheable prefix. Expected to improve cache hit rates on sessions that actively extract/update memories. Invisible to the user; visible in telemetry if measured.

Git commit messages will describe the changes; no separate CHANGELOG.

---

## Pre-flight — Team Lead

This plan is TL-authored directly (no `/orchestrate-plan` sub-agent critique). If the user wants the full PO + Core Dev parallel critique before Gate 1, invoke:

```
/orchestrate-plan compaction-followup-fixes
```

Otherwise, proceed to Gate 1 (PO + TL approve plan → right problem, correct scope?), then:

```
/orchestrate-dev compaction-followup-fixes
```

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/context/_history.py:541` | Probe logic condition is `skips_since_trip == 0 or skips_since_trip % _CIRCUIT_BREAKER_PROBE_EVERY != 0`. The plan pseudocode had `(count-3) % N != 0` which would probe at count=3 (first trip). The implementation adds an explicit `skips_since_trip == 0` guard to skip the first trip. The constant docstring ("First probe fires at failure_count == 3 + N, i.e. after N skips") correctly documents this intent. Implementation is consistent with docstring and tests. No defect, but the plan pseudocode was never updated to match — minor doc drift in the plan itself (irrelevant post-ship). | minor | TASK-1 |
| `evals/eval_memory_extraction_flow.py:327,342,531,547,550,580` | Six references to `inject_opening_context` remain (import and all call-sites). The TASK-2 `done_when` criterion says `grep -r inject_opening_context co_cli/ tests/ docs/specs/` returns zero, but the scope deliberately excludes `evals/`. The stale function name in evals now fails at import (`inject_opening_context` no longer exists in `_history.py`), which will cause any eval run to crash with `ImportError`. | blocking | TASK-2 |
| `evals/eval_memory_recall.py:2,5,11` | Module docstring and inline comments reference `inject_opening_context` by name (no import, so no crash, but stale after rename). | minor | TASK-2 |
| `evals/eval_compaction_quality.py:81,1218,1221,1565,1568` | Import `inject_opening_context` at line 81 and four call-sites. Same issue as `eval_memory_extraction_flow.py` — will crash at import. | blocking | TASK-2 |
| `co_cli/prompts/personalities/_injector.py:32` | Docstring still says "Called by `add_personality_memories()` in `agent.py`". `add_personality_memories` was removed; the caller is now `append_recalled_memories`. Stale docstring, no runtime impact. | minor | TASK-3 |
| `docs/specs/personality.md:38-62` | §2 "Core Logic" intro still describes `add_personality_memories()` as an `@agent.instructions` callback firing per-turn. The function was removed from `_instructions.py` and the `agent.instructions(...)` registration was deleted. The spec is now factually wrong. | blocking | TASK-3 |
| `docs/specs/flow-prompt-assembly.md:249-260` | §2.6 still lists `add_personality_memories` as item 4 in the instruction stack ("personality memories: `## Learned Context`..."). The function no longer participates in the instruction stack — content now arrives via `append_recalled_memories` at history-processor time, not instruction-concatenation time. Spec is structurally wrong: it describes the old mechanism under the new name at a later point. | blocking | TASK-3 |
| `tests/test_history.py:195-211` | `test_circuit_breaker_probes_at_cadence` asserts `deps.runtime.compaction_failure_count != 13` after calling `summarize_history_window` with count=13. This assertion is correct (either success→0 or failure→14), but it does not verify that LLM was actually attempted — it only rules out the skip branch. The deletion check: if the skip branch silently ran (bug in probe condition), count would stay at 14 (skip increments), which would still satisfy `!= 13`. The assertion is too weak to catch a probe-cadence regression where the skip branch increments before returning. A tighter check would be: assert count in (0, 14) or assert `compaction_failure_count == 14 or compaction_failure_count == 0`. The current form passes even if the probe fires but then triggers the skip increment erroneously. | minor | TASK-1 |
| `docs/specs/context.md:107` | Append-only invariant paragraph mentions `add_personality_memories` by name as an example of a moved function — this is useful historical context and appropriate to keep. No defect. | — | TASK-3 |
| `docs/specs/compaction.md:546` | Proactive→overflow handoff paragraph is well-placed and accurate. | — | TASK-4 |

**Overall: 3 blocking / 4 minor**

### Blocking fixes required

**B1 — Eval import crashes (TASK-2):**
`evals/eval_memory_extraction_flow.py` and `evals/eval_compaction_quality.py` both import `inject_opening_context` from `co_cli.context._history`. The function no longer exists under that name. Any `uv run python evals/eval_*.py` invocation will raise `ImportError`. Fix: replace all six occurrences of `inject_opening_context` with `append_recalled_memories` in `eval_memory_extraction_flow.py` and all five occurrences in `eval_compaction_quality.py`. The TASK-2 grep scope (`co_cli/ tests/ docs/specs/`) did not cover `evals/`.

**B2 — `docs/specs/personality.md` stale (TASK-3):**
Lines 38–62 still describe `add_personality_memories()` as a live `@agent.instructions` callback. The function was deleted from `_instructions.py` and its registration removed from `_core.py`. The spec must be updated to reflect that personality-memory content is now injected at history-processor time via `append_recalled_memories`, not at instruction-concatenation time. The per-turn diagram in §2 must be rewritten accordingly.

**B3 — `docs/specs/flow-prompt-assembly.md` stale (TASK-3):**
Lines 249–260 list `add_personality_memories` as item 4 in the per-request instruction stack and describe its output ("## Learned Context"). The function is gone; this list must be updated to 4 entries (`add_current_date`, `add_shell_guidance`, `add_category_awareness_prompt`, plus SDK-supplied parts) and a note added that personality memories now arrive via `append_recalled_memories` in the history-processor chain (already described at §2.7 line 269).

---

All blocking findings (B1, B2, B3) and all minor findings have been resolved.

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_CIRCUIT_BREAKER_PROBE_EVERY = 10` constant; probe logic in `_summarize_dropped_messages`; tests at count=3 (skip), count=4 (skip+increment), count=13 (probe); spec updated | ✓ pass |
| TASK-2 | `append_recalled_memories` in all co_cli/, tests/, docs/specs/, evals/; zero `inject_opening_context` references across live code | ✓ pass |
| TASK-3 | `add_personality_memories` removed from `@agent.instructions`; folded into `append_recalled_memories`; append-only invariant in context.md §2.1; personality.md and flow-prompt-assembly.md updated | ✓ pass |
| TASK-4 | §2.9 "Proactive → overflow handoff" paragraph added to compaction.md | ✓ pass |

**Tests:** full suite — 626 passed, 0 failed
**Independent Review:** 3 blocking (eval imports, personality.md, flow-prompt-assembly.md) — all fixed; 4 minor (weak test assertion, stale docstring, eval comments) — all fixed
**Doc Sync:** fixed (circuit breaker probe cadence in compaction.md, core-loop.md, context.md; personality.md and flow-prompt-assembly.md per-turn injection descriptions; constants table updated)

**Overall: DELIVERED**
All four tasks shipped: circuit breaker probe recovery, processor rename, personality memory cache fix + invariant, and proactive→overflow handoff doc. 626 tests pass; all independent review findings resolved.
