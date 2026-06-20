# Exec Plan — Gap C: local cross-session user modeling

**Slug:** `gap-c-local-user-modeling`
**Created:** 2026-06-19 14:15:25

## Context

`docs/reference/RESEARCH-self-learning-co-vs-hermes.md` §5.3 logs **Gap C**: hermes synthesizes a second-order behavioral model of the user (Honcho dialectic backend); co's model of the user is only the union of memory items plus whatever the per-session memory reviewer happened to write to `~/.co-cli/USER.md`. There is no pass that looks **across** recent sessions to re-derive a consolidated, deepening profile — catch contradictions, drop stale preferences, surface recurring working-style signal.

Current state (source-read 2026-06-19):
- The memory reviewer (`co_cli/daemons/dream/_reviewer.py:71` `MEMORY_REVIEW_SPEC`) already owns `user_profile_view` / `user_profile_write` and writes USER.md — but it sees **one** transcript per KICK (`process_review`, `_reviewer.py:146`). So USER.md is only ever updated from a single session at a time.
- USER.md primitive: `co_cli/memory/user_profile.py` — `read_user_profile` / `write_user_profile(path, text, char_budget)` (wholesale rewrite, atomic, `UserProfileBudgetError` over budget). Budget from `memory.user_profile_char_budget` (default 1500).
- Housekeeping (`_housekeeping.py:535` `run_housekeeping`) is the cross-cutting, scheduled, cross-store pass: `merge_memory` → `merge_skills` → `decay_skills` + prunes. Today it explicitly **never reads transcripts** (`_housekeeping.py:3` docstring: "Reviewer is the sole transcript reader").
- Session enumeration: `co_cli/session/browser.py:68` `list_sessions(sessions_dir) -> list[SessionSummary]`; transcript load: `co_cli/session/persistence.py` `load_transcript`.
- Forked-agent pattern: `fork_deps_for_reviewer(deps)` + `run_standalone(spec, child_deps, prompt)` (`_reviewer.py:111`). Specs are `TaskAgentSpec` (`co_cli/agent/spec.py`).
- Local model is the configured `llm.host` Ollama model — no external/remote dependency anywhere in this path.

**Settled design decision (user, 2026-06-19):** package the synthesis as a **dream-daemon housekeeping sub-pass** (`synthesize_user_profile`), forked-agent like the reviewers. Not a model-callable tool (synthesis is periodic + autonomous, not opportunistic) and not a user skill (skills are invocable procedures, not background passes).

**Cadence design decision (user, 2026-06-19 — supersedes "daily piggyback"):** the housekeeping tick stays the *clock*, but synthesis is **gated on session accumulation, not wall-clock**. A fixed time window has no logical relation to how many sessions a day actually produces — on a heavy day a fixed "10 most-recent" window silently drops sessions before the next tick; on a quiet day it cosmetically re-scans an unchanged set. Instead: persist a checkpoint marker for the newest **fully-settled** session; each tick, count sessions newer than that marker; when `≥ lookback//2` have accumulated, fire synthesis over a `lookback`-wide window **anchored at the marker** (the oldest un-settled sessions, drained oldest-first), and **on success advance the marker forward by `lookback//2`** — settling the oldest half of the window and leaving the newer half un-settled so it reappears in the next window. Window `N` with step `N/2` overlaps consecutive runs 50%, so every session lands in ≥2 windows and **no session is ever skipped** — the marker crawls forward and never leaps past un-processed sessions. This is **rate-vs-interval, not calendar** (CD-M-6): synthesis fires at most once per tick regardless of stream density, so a dense stream drains over several ticks — the marker visibly lags, it never drops. (Nyquist framing: `N/2` is the step/rate, `N` the window, 50% overlap the coverage margin.)

## Problem & Outcome

**Problem:** USER.md is only ever derived from one session at a time, so co never builds a cross-session model of the user; stale/contradicted preferences accumulate and recurring behavioral signal is never consolidated.

**Outcome:** A local-model housekeeping sub-pass, fired whenever `lookback/2` new sessions have accumulated since the last run, re-derives the whole USER.md from a `lookback`-wide marker-anchored window of session transcripts + the current profile, consolidating durable signal and dropping contradicted/stale facts — all on-device, persisted to the single USER.md. The marker-anchored, oldest-first, advance-by-`lookback//2` window guarantees full session coverage (50% overlap, no skips) regardless of stream density — a dense backlog drains over several ticks rather than being truncated.

**Failure cost:** Without it, USER.md drifts: per-session writes append-and-overwrite with no cross-session reconciliation, so contradictions persist and the "deepening model of who you are" claim (research §5.3 / hermes claim #5) stays a gap. Silent, because each per-session write looks locally correct.

## Scope

In scope:
- New `synthesize_user_profile` housekeeping sub-pass + a `PROFILE_SYNTHESIS_SPEC` forked agent + its prompt.
- Config flag + lookback knob; wire into `run_housekeeping` gated off by default.
- A persisted **last-synthesized session marker** in `HousekeepingState` + the accumulate-`N/2`-then-look-back-`N` trigger gate.
- §-delimiter format convention in the `user_profile_view`/`user_profile_write` docstrings (CD-M-5).
- Functional test exercising the pass against the configured local model.

**Already landed during planning (decision-driven, ahead of `/orchestrate-dev`):** the synthesis prompt `profile_synthesis.md` (TASK-2 deliverable) and the §-docstring convention edits to `user_profile/write.py` + `view.py` (CD-M-5). Dev should treat these as done and verify, not re-author. Everything else (config, sub-pass, state, wire-in, test) is unstarted.

Out of scope:
- Any external/remote backend (Honcho, MemoryProvider ABC) — explicitly excluded per mission and user constraint.
- Touching the per-session memory reviewer's USER.md writes — they stay; this pass is the cross-session reconciler on top.
- A second USER-facing tool or skill.
- Spec edits (`docs/specs/dream.md` "sole transcript reader" wording) — handled by `sync-doc` post-delivery.
- A *separate timer/interval* distinct from the housekeeping tick — the tick stays the clock; synthesis is gated on session count, not its own schedule.

## Behavioral Constraints

- **Local-only:** uses the configured agent model (`deps`-forked, Ollama `llm.host`). No network/external call introduced.
- **Consolidate, don't truncate:** the synthesis must merge the current profile with cross-session signal, dropping only contradicted/stale facts — never blind-truncate to fit budget. Budget overflow surfaces as `UserProfileBudgetError`; the agent must consolidate and retry within budget.
- **Wholesale rewrite:** writes go through the existing `write_user_profile` (atomic, budget-capped). No new persistence path.
- **Graceful no-op:** disabled flag, USER.md profile disabled (`memory.user_profile_enabled=False`), or below the `lookback//2` accumulation threshold (see Session-gated trigger) → pass does nothing and logs, never errors.
- **Off by default:** `profile_synthesis_enabled=False` ships off, consistent with `review_enabled` / `dream.enabled`.
- **Session-gated trigger (not wall-clock):** each housekeeping tick counts sessions newer than the persisted marker. Synthesis fires only when `≥ lookback//2` have accumulated; otherwise it logs and no-ops. The window is **anchored at the marker** (oldest un-settled sessions, drained oldest-first) and the marker advances `lookback//2` per successful run, so consecutive windows overlap 50% and **no session is ever skipped, regardless of stream density**. This is rate-vs-interval, not calendar (CD-M-6): synthesis fires at most once per tick, so a dense backlog drains over several ticks (marker visibly lags, never drops). Fewer than `lookback//2` new sessions → no-op.
- **Crash-safe marker:** the checkpoint is a session boundary (`session_id` + `created_at`), not a mutable integer counter. "Sessions since last run" is recomputed each tick from `list_sessions(...)` (ground truth on disk), so it survives daemon restarts without separate counter-persistence discipline. The marker advances **only after a successful write**, so a failed/cancelled run re-counts the same sessions next tick (set-state-flags-after-success).
- **Bounded:** reads at most `profile_synthesis_lookback_sessions` transcripts. It runs under its **own inner `asyncio.timeout` sub-budget** nested in the housekeeping merge block, so a slow merge cannot starve it (mirrors why `decay_skills` sits outside the merge timeout, `_housekeeping.py:544-550`).
- **No-block on background write:** `user_profile_write` is approval-required + DEFERRED on the main agent, but task agents register every spec tool with `requires_approval=False` (`co_cli/agent/build.py`) and the fork excludes `deps.toolset` (`deps.py:416-417`) — so the synthesis fork writes USER.md without any approval gate, exactly as `MEMORY_REVIEW_SPEC` already does daily. No auto-approve wiring needed.
- **Best-effort, never corrupting:** synthesis is the last LLM phase; on timeout/model error it is cancelled mid-run with no partial write (`write_user_profile` is wholesale/atomic), USER.md is left untouched, and `profile_synthesized` does not increment — the next daily tick retries. Context overflow from too many transcripts degrades to a logged no-op (caught by the merge-block `except`), never silent truncation.

## Failure Modes (anticipated, from the existing reviewer's USER.md behavior)

The per-session reviewer already writes USER.md with a small local model; observed/anticipated failure modes that the synthesis prompt + test must counter:
- **Fact loss:** small model rewrites the profile and silently drops still-valid facts not mentioned in the recent window. → prompt must pin "preserve durable facts from the current profile unless contradicted."
- **Budget thrash:** model exceeds `user_profile_char_budget`, write rejected, no retry. → prompt states the budget and instructs consolidation; pass logs the rejection rather than crashing.
- **Recency over-weighting:** one recent session's transient preference overwrites a long-standing one. → prompt frames the current profile as the prior and the window as evidence, not replacement.
- **Churn:** re-running on an unchanged session set rewrites the profile cosmetically. → acceptable (idempotency is best-effort), but the test asserts no fact loss across a re-run.

These are anticipated from the existing reviewer; TASK-4 promotes them to an executed behavioral check.

## High-Level Design

1. **Config** (`co_cli/config/memory.py`, `MemorySettings`):
   - `profile_synthesis_enabled: bool = False`
   - `profile_synthesis_lookback_sessions: int = 10` (`ge=2`)
   - Add both to `MEMORY_ENV_MAP`.
   Rationale for home: USER.md sizing (`user_profile_*`) already lives on `MemorySettings`; cadence is the housekeeping tick + a session-count gate, so no `DreamSettings` knob is needed. The trigger threshold is **derived** (`lookback // 2`), not a separate knob — the window/step relation is fixed by the coverage guarantee, not independently tunable. `ge=2` so the derived step is `≥1`.

2. **Spec + prompt** (`_housekeeping.py` or sibling; prompt `co_cli/daemons/dream/prompts/profile_synthesis.md` — **already written**):
   - `PROFILE_SYNTHESIS_SPEC = TaskAgentSpec(...)` with `tool_names=("user_profile_view", "user_profile_write", "memory_search")`, `include_skill_manifest=False`, `default_budget=REVIEW_MAX_ITERATIONS`, output `SessionReviewOutput` (reuse).
   - Prompt content encodes the four Failure-Mode counters + the four Honcho-Dreamer techniques (CD-M-4: volatility test, update-vs-contradiction, ≥2-session pattern threshold, quality-over-quantity), the §-delimiter convention (CD-M-5), and a closing one-line reconciliation summary. See TASK-2 `done_when` for the checklist.

3. **Sub-pass** (`synthesize_user_profile(deps, state)` in `_housekeeping.py`):
   - Gate: return early if not `deps.config.memory.profile_synthesis_enabled` or not `user_profile_enabled`.
   - `sessions = list_sessions(deps.sessions_dir)` (most-recent-first). Compute the **un-settled** sessions: those newer than `state.last_synthesized_session` — i.e. take from the front until the marker `session_id` is reached (marker absent → all are un-settled). If that count `< lookback // 2` → log + return (not enough accumulated yet).
   - **Window is anchored at the marker, not at the newest session:** order the un-settled sessions oldest→newest and take the oldest `lookback` as the window. Draining oldest-first means a backlog larger than `lookback` is never skipped — the window covers the sessions immediately above the marker, not just the most recent ones. `load_transcript` each, serialize with `serialize_messages` (reuse reviewer's serialization, `include_tool_results=False`).
   - No eligible sessions → log + return.
   - Fork via `fork_deps_for_reviewer(deps)`, build prompt embedding the windows, `run_standalone(PROFILE_SYNTHESIS_SPEC, child_deps, prompt)`. The agent calls `user_profile_view` then `user_profile_write` itself.
   - **On success only:** advance the marker **forward by `lookback // 2`** — set `state.last_synthesized_session` to the session at index `lookback // 2 - 1` from the **oldest** end of the window (the `≥ lookback//2` gate guarantees the window holds at least that many, so the index always exists). The oldest half is now settled; the newer half stays un-settled and reappears in the next window → 50% overlap. Bump `HousekeepingStats.profile_synthesized`. The marker never leaps past un-processed sessions, so nothing is dropped: a dense backlog simply drains `lookback // 2` per tick (the marker lags visibly, observable via the un-settled count — see "no silent caps", log the remaining backlog).

4. **Wire-in** (`run_housekeeping`, `_housekeeping.py:558`): call `synthesize_user_profile(deps, state)` after `merge_skills`, wrapping the `run_standalone` call in its **own inner `asyncio.timeout`** (sub-budget = a fraction of `cfg.max_pass_seconds`, e.g. a `_PROFILE_SYNTHESIS_MAX_SECONDS` constant) so a slow merge can't consume the whole window before synthesis runs. Synthesis is best-effort: an inner timeout or model error is logged and the pass moves on; USER.md stays untouched (wholesale/atomic write), the marker does **not** advance, and the counter doesn't bump — so the same sessions are re-counted and the run retries on the next tick.

5. **State** (`state.py`):
   - Add `profile_synthesized: int = 0` to `HousekeepingStats`.
   - Add `last_synthesized_session: SessionMarker | None = None` to `HousekeepingState`, where `SessionMarker` is a small `BaseModel` carrying `session_id: str` + `created_at: str`. Marker advances only after a successful synthesis write (see step 3).

**Invariant note:** this is the first housekeeping phase that reads transcripts, amending "reviewer is sole transcript reader" → "reviewer + profile synthesis read transcripts." The `_housekeeping.py` module docstring and `docs/specs/dream.md` carry that wording — module docstring updated in TASK-3; spec wording deferred to `sync-doc`.

**Concurrency & durability note — why no queue is needed (CD-M-7):** Synthesis needs no queue, lock, or separate scheduler. Four properties already give it exactly-once-eventually, no-overlap, no-corruption behavior — in plain words:

- **Single-loop.** The dream daemon is one sequential `await` loop (`_loop.py:113`). Housekeeping — and therefore synthesis — runs *inside* that loop on the empty-queue branch, after the per-session reviewers have drained. The same call stack that decides "is a tick due" is the one that runs synthesis, so it can never re-enter or overlap itself, and it never races the per-session reviewer (they are just different points in the same loop). A long synthesis only delays the next tick; it cannot pile up. This is also why a "cycle slip" — the classic overrun where an independently-timed periodic job fires again before the last run finished — **cannot happen here**: synthesis is not on its own timer.
- **Atomic-write.** `write_user_profile` overwrites the whole file atomically (`atomic_write_text`), so the one genuine cross-process writer (the live agent, in another process) can at worst last-writer-win — never corrupt or half-write USER.md. That race pre-exists (reviewer vs. live agent) and synthesis does not worsen it.
- **Disk-recomputed-marker.** "Sessions since last run" is recomputed every tick from `list_sessions(...)` — the on-disk ground truth — not from an in-memory counter. So the trigger survives daemon restarts with no counter-persistence discipline and nothing to replay.
- **Success-gated-advance.** The marker advances only after a successful write. A synthesis that times out, errors, or is cancelled mid-run leaves the marker untouched, so the next tick re-counts the same sessions and retries. No work is lost across an overrun — it is merely deferred one tick.

Together these make a queue over-design: producer and consumer are the same loop (nothing to decouple), and durability/retry come for free from disk + atomic write. A queue would also cut against the queue-decoupling doctrine (a queue is for decoupling work *across* processes, not *within* a single loop). The realistic worst case is purely latency — a long run eats into one tick and defers the next — never overlap, loss, or corruption.

**Boundary note — three USER.md writers, one contract, divergent reasoning (do NOT DRY the prompts):** USER.md has three writers — the live agent (`07_memory_protocol.md` Explicit-saves rule, approval-gated, explicit single-fact), the per-session reviewer (`memory_review.md`, one transcript), and this cross-session synthesis (`profile_synthesis.md`, deep reconciliation). The **write contract** (wholesale rewrite, view-first, char-budget consolidation) is enforced in **code** — `write_user_profile` / `UserProfileBudgetError` (`co_cli/memory/user_profile.py`) — so all three agree on mechanics at the seam regardless of prompt wording; that is the single source of truth, not prose. The **§-delimiter format convention** (CD-M-5) is the shared *format* half of the contract and lives in the `user_profile_view`/`user_profile_write` tool docstrings — the one surface all three writers touch — not duplicated per prompt. It is a readability convention only: no parser, no validation, the asset stays free-text and a missing § breaks nothing (preserves the no-schema / no-migration doctrine). The **reasoning depth deliberately diverges**: the four Honcho-derived techniques (CD-M-4) live **only** in `profile_synthesis.md`. Do not extract a shared prompt fragment across the three — they sit in different prompt layers (runtime-injected rule vs dreamer-fork instructions, no shared include mechanism beyond `_with_curation_lens`), editing the live rule trips the floor guards, and the live path is explicit-only by design. The profile-vs-memory scope rule restated in `07_memory_protocol.md` + `memory_review.md` is accepted duplication, not a refactor target.

## Tasks

✓ DONE **TASK-1 — Config knobs**
- files: `co_cli/config/memory.py`
- done_when: `MemorySettings` exposes `profile_synthesis_enabled` (default `False`) and `profile_synthesis_lookback_sessions` (default `10`, `ge=2`), both present in `MEMORY_ENV_MAP`; `uv run python -c "from co_cli.config.memory import MemorySettings; s=MemorySettings(); assert s.profile_synthesis_enabled is False and s.profile_synthesis_lookback_sessions==10"` exits 0.
- success_signal: config loads with the new fields defaulted off.
- prerequisites: none

✓ DONE **TASK-2 — Synthesis prompt**
- files: `co_cli/daemons/dream/prompts/profile_synthesis.md`
- done_when: prompt file exists and encodes the four Failure-Modes counters (preserve durable facts, stay under budget, current-profile-as-prior, consolidate-don't-truncate) **plus the four Honcho-Dreamer reasoning techniques (CD-M-4):** (1) the six-month **volatility test** for durability; (2) the **update-vs-contradiction** split (same-attribute new-value → replace; genuinely-incompatible → drop the loser by recency/consistency); (3) **patterns require ≥2 sessions** before promotion to a durable trait; (4) **quality-over-quantity / no cosmetic churn**. Stays scoped to USER.md only (no memory-item creation; `memory_search` is read-only cross-check). Referenced by the spec in TASK-3.
- success_signal: N/A (prompt asset; efficacy proven by TASK-4)
- prerequisites: none

✓ DONE **TASK-3 — Sub-pass + spec + wire-in + state**
- files: `co_cli/daemons/dream/_housekeeping.py`, `co_cli/daemons/dream/state.py`
- done_when: `HousekeepingState` carries `last_synthesized_session: SessionMarker | None` (+ the `SessionMarker` model); `HousekeepingStats` carries `profile_synthesized: int = 0`. `synthesize_user_profile(deps, state)` exists, gated off by default; counts sessions newer than the marker and no-ops when `< lookback // 2` have accumulated; otherwise selects the **oldest `lookback` un-settled sessions (marker-anchored, oldest-first)**, forks `PROFILE_SYNTHESIS_SPEC` via `run_standalone` under its own inner `asyncio.timeout`, and **on successful write only** advances the marker **forward by `lookback // 2`** (settling the oldest half of the window) + bumps `profile_synthesized`; called inside `run_housekeeping` after `merge_skills`; module docstring's "sole transcript reader" line amended. **Pre-check:** `rg "HousekeepingStats|HousekeepingState" tests/` first and fix any stale shape/`model_dump()`-equality assertion the new counter/marker field would break. Repo-wide grep `rg "sole transcript reader"` shows only the amended/spec occurrences (spec deferred to sync-doc), and the full suite passes (`uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log`).
- success_signal: with the flag on and ≥`lookback//2` seeded sessions, a housekeeping pass writes a consolidated USER.md, advances the marker, and increments the counter; a second immediate tick (no new sessions) no-ops without rewriting.
- prerequisites: TASK-1, TASK-2

✓ DONE **TASK-4 — Behavioral test**
- files: `tests/test_dream_housekeeping.py` (or the existing housekeeping test module)
- done_when: a test seeds `≥ lookback//2` session transcripts carrying a consistent user-fact + a contradicted/stale one and a starting USER.md, runs `synthesize_user_profile` with the flag on against the configured warm local model (config model settings, ensure_ollama_warm outside the timeout), and asserts observable behavior: USER.md is rewritten to reflect the durable fact and stays within `user_profile_char_budget`; a disabled-flag run leaves USER.md untouched; and a run with **fewer than `lookback//2`** new sessions since the marker leaves USER.md untouched (the accumulation gate). Test passes in the full suite run from TASK-3.
- success_signal: the pass produces a profile reflecting cross-session signal; disabled run and below-threshold run are both no-ops.
- prerequisites: TASK-3

## Testing

- TASK-4 is the behavioral gate: real local model, real USER.md file, seeded real session JSONL (per evals/test policy — no mock model, no test stores). Watch LLM call timing in the tail log; RCA any long call rather than bumping timeouts.
- Functional assertions only (mirror `done_when`): profile content reflects the durable cross-session fact, budget respected, disabled → no write. No structural assertions (don't assert the spec's tool list or that a method was called).
- Full suite via the logged `pytest` invocation in TASK-3 is the no-regression net (the wire-in touches the shared `run_housekeeping`).

## Open Questions

- **Cadence — resolved.** Session-count gate keyed to a persisted marker, fired off the housekeeping tick (accumulate `lookback//2`, then synthesize a `lookback`-wide **marker-anchored** window oldest-first and advance the marker `lookback//2`). No separate timer, no wall-clock window, no drop on dense streams. See the cadence design decision in Context and decisions CD-M-3 (gate) + CD-M-6 (marker-anchored drain, no-skip).
- **Lookback default (10 sessions)?** Deferred — 10 is a starting guess. The window must outpace daily session churn for a durable trait to land before it ages out, so size `N` to typical daily session volume (a trait recurring with period `≤ N/2` is reliably resolved; rarer-than-`N/2` reads as transient). Re-raise if it overflows the local model's context or under-samples; tune via the config knob, not code.
- **Did a run earn its keep?** Mostly resolved by the session-gate (PO-m-2): a run now fires only when `≥ lookback//2` genuinely-new sessions exist, so cosmetic-churn runs on an unchanged set are structurally impossible. The residual gap — a run that fires on new sessions but only cosmetically rewrites without reconciling anything — has no signal yet; `profile_synthesized` counts runs, not reconciliations. Build no observability now; evaluate the cost-vs-value tradeoff before the flag is ever defaulted on.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Load-bearing risk (DEFERRED/approval `user_profile_write` blocking a bg fork) verified a non-issue: task agents register tools `requires_approval=False` and the fork excludes `deps.toolset`. Worth pinning so it isn't re-litigated at dev time. | Behavioral Constraints — added "No-block on background write" bullet citing `build.py` + `deps.py:416-417`. |
| CD-M-2 | modify | Real starvation risk: synthesis ran last inside the shared 600s merge timeout, so a slow merge could starve it (same reason `decay_skills` sits outside the merge timeout). Gave synthesis its own inner sub-budget instead of accepting best-effort-after-merges. | High-Level Design step 4 + Behavioral Constraints — synthesis wrapped in its own inner `asyncio.timeout` (`_PROFILE_SYNTHESIS_MAX_SECONDS`); corrected the inaccurate "partial-counter-persist covers it" to best-effort/never-corrupting. |
| CD-m-1 | adopt | New `profile_synthesized` counter could break a hardcoded `HousekeepingStats` shape assertion; checklist requires a stale-assertion pre-check before relying on suite-green. | TASK-3 `done_when` — added `rg "HousekeepingStats\|HousekeepingState" tests/` pre-check. |
| CD-m-2 | adopt | N full transcripts with no truncation guard and no compaction in this path can overflow the local model's context; should be stated as degrading to a logged no-op, not silent truncation. | Behavioral Constraints — folded into "Best-effort, never corrupting" bullet. |
| CD-M-5 | adopt (user, 2026-06-19) | Recover hermes's § entry delimiter as a **format convention** (distinct from hermes's entry-CRUD edit model, which co still rejects): a minimal-cost syntactic aid that lets the model see and revise one fact at a time during a wholesale rewrite. Placed at the shared **tool-docstring** surface (`user_profile_view`/`write`) — the one seam all three writers touch — plus reinforced in `profile_synthesis.md`. Convention only: no parser, no validation, asset stays free-text, missing § breaks nothing (no-schema / no-migration doctrine intact). Faithful to hermes, which documents the delimiter in its tool schema description, not a code schema. | `co_cli/tools/user_profile/write.py` + `view.py` docstrings; `profile_synthesis.md`; boundary note. |
| CD-M-4 | adopt (user, 2026-06-19) | Researched Honcho's Dreamer (the peer system hermes uses) for synthesis-reasoning best practice. Storage paradigm is incompatible (vector observation store vs co's wholesale USER.md) and is **rejected** — Honcho issue #729 shows that approach bloated to 300+ near-duplicate observations by session 32, a failure co's wholesale-rewrite-under-budget avoids by construction. But four of its Dreamer reasoning techniques port directly and beat our generic four counters: volatility test, update-vs-contradiction split, ≥2-session pattern threshold, quality-over-quantity. Independently, Honcho validates two contested decisions in this plan — count-threshold trigger (not wall-clock) and advance-marker-only-on-success (`last_dream_document_count`/`last_dream_at` advance atomically only on successful consolidation). Honcho cloned to `~/workspace_genai/honcho` for future reference. | TASK-2 done_when (four techniques added); prompt `profile_synthesis.md` authored to them; this row. Observation-store storage rejected-by-design (no change to wholesale-rewrite design). |
| CD-M-3 | modify (user, 2026-06-19) | A fixed wall-clock window ("daily, 10 most-recent") has no logical relation to session production: it silently drops sessions on heavy days and cosmetically re-scans on quiet days. Replaced with a session-count gate — keep the housekeeping tick as clock, but fire only when `lookback//2` new sessions have accumulated since a persisted marker, then look back `lookback`. The `N`/`N/2` window/step gives 50% overlap → every session covered, no churn. Marker is a crash-safe session boundary recomputed from `list_sessions`, advanced only after a successful write. Flips the deferred "separate cadence" Open Question to resolved and largely answers PO-m-2. | Context (new cadence decision), Problem/Outcome, Scope, Behavioral Constraints (session-gated trigger + crash-safe marker bullets), High-Level Design steps 1/3/4/5, TASK-1 (`ge=2`), TASK-3, TASK-4, Open Questions. |
| CD-m-3 | adopt (noted) | TASK-2 is correctly file-exists-only; the four Failure-Mode counters are exercised by TASK-4's behavioral test, which is the real functional gate. No change needed — already reflected in TASK-2/TASK-4 split. | — |
| CD-M-6 | modify (user, 2026-06-19) | The newest-anchored `lookback` window with marker→newest advance silently **drops** sessions whenever more than `lookback` arrive within one tick interval: the window never covers positions `[lookback:count]`, and advancing the marker to the newest leaps past them. This is **rate-vs-interval, not calendar** — synthesis fires at most once per tick regardless of stream density, and the count gate only raises the firing *floor*, never the look-back ceiling or the cadence. Fix: anchor the window at the marker (oldest un-settled, drained oldest-first) and advance the marker `lookback//2` per successful run, so the marker never leaps past un-processed sessions; a dense backlog drains `lookback//2`/tick with 50% overlap — coverage guaranteed, lag visible (log remaining backlog), nothing dropped. | Context (cadence decision rewritten), Problem/Outcome, Behavioral Constraints (session-gated trigger), High-Level Design step 3, TASK-3. |
| CD-M-7 | reject (queue — over-design) | Confirmed a queue/lock for synthesis is unnecessary: single-loop serialization (no overlap/re-entrancy, no reviewer race), atomic wholesale write (no corruption), disk-recomputed marker (restart-safe, no counter), and success-gated advance (auto-retry) already give exactly-once-eventually with no work loss. A "cycle slip" cannot occur because synthesis is not on an independent timer. A queue would also violate the queue-decoupling doctrine (producer and consumer are the same loop). | High-Level Design — added the "Concurrency & durability" note. |
| PO-m-1 | reject | `profile_synthesis_*` naming: the pass synthesizes a consolidated profile from multiple inputs, "synthesis" is accurate, and `synthesize_user_profile` is the user-approved name for this pass. Renaming would diverge from the approved decision for marginal nuance. | — |
| PO-m-2 | adopt | No signal distinguishes a run that reconciled a contradiction from cosmetic churn; the cost-vs-value tradeoff must be evaluable before defaulting the flag on. | Open Questions — added "Did a run earn its keep?" deferred item. |

## Delivery Summary — 2026-06-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `MemorySettings` exposes both knobs (defaults off/10, `ge=2`), in `MEMORY_ENV_MAP`; one-liner assert exits 0 | ✓ pass |
| TASK-2 | Prompt encodes 4 Failure-Mode counters + 4 Honcho techniques + § convention, USER.md-scoped | ✓ pass (pre-landed, verified) |
| TASK-3 | `SessionMarker`/`last_synthesized_session`/`profile_synthesized` added; `synthesize_user_profile` gated, marker-anchored oldest-first window, inner timeout, success-gated advance + counter; wired after `merge_skills`; docstring amended; suite green | ✓ pass |
| TASK-4 | Real-model test: durable cross-session fact survives, budget respected, disabled + below-threshold both no-op | ✓ pass |

**Tests:** scoped — 3 synthesis tests passed (1 real-model, 28.2s, healthy call timings); full suite — 803 passed, 0 failed.
**Doc Sync:** fixed — dream.md (§2.6 added + renumber, mermaid, state/symbol/config/observability rows), memory.md (§7 writers + 2 config rows), config.md (2 config rows). Spec "sole transcript reader" invariant amended to reviewer + profile synthesis.

**Overall: DELIVERED**
All four tasks pass `done_when`; lint clean, full suite green, docs synced. CD-M-5 docstrings + TASK-2 prompt verified as pre-landed. No extra files touched beyond the planned `files:` plus the sync-doc spec edits (dream.md/memory.md/config.md).

## Implementation Review — 2026-06-20

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | both knobs exposed (off/10, `ge=2`), in `MEMORY_ENV_MAP`; one-liner exits 0 | ✓ pass | `config/memory.py:81-82` (fields + `ge=2`), `:46-47` (env map); one-liner `EXIT=0` |
| TASK-2 | prompt encodes 4 Failure-Mode counters + 4 Honcho techniques + § convention, USER.md-scoped | ✓ pass | `profile_synthesis.md` — counters L16/18/29/32; techniques L18/20/22-24/34; USER.md-only L12/36; § L28/30; closing summary L36 |
| TASK-3 | state fields + `synthesize_user_profile` gated/marker-anchored-oldest-first/inner-timeout/success-advance; wired after `merge_skills`; docstring amended | ✓ pass | `state.py:48-57,45,69`; `_housekeeping.py:617` gate, `:631-632` oldest-first window, `:654` advance `window[step-1]`, `:705` inner `asyncio.timeout`, `:706` after `merge_skills`, `:1-8` docstring amended; `< step` gate guarantees `window[step-1]` valid (`ge=2`) |
| TASK-4 | real warm-model test: durable fact survives, budget respected, disabled + below-threshold no-op | ✓ pass | `test_housekeeping.py:500-579` — 3 synthesis tests, real `build_model(config.llm)`, `ensure_ollama_warm` outside timeout (`:514`→`:516`), temp-dir isolated; functional content assertions |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Marker advances on `run_standalone` completion, not a confirmed `user_profile_write` call ("on successful write only" wording) | `_housekeeping.py:652-659`, docstring `:604-606` | minor (not fixed) | Working-as-designed and peer-aligned: failure cases (timeout/model error) correctly skip the advance via `except` at `:707`; a reviewed window yielding no write is legitimately settled (no-cosmetic-churn / best-effort idempotency the plan accepts). `_run_memory_review` has the identical shape. Wording is imprecise but faithful to the plan's own phrasing — left as-is per surgical-changes. |
| Context-overflow degradation is implicit (generic `except` at `:707`) rather than a dedicated branch | `_housekeeping.py:707` | minor | Acceptable — no silent truncation; degrades to logged no-op as the constraint requires. |
| `_unsettled_sessions` return annotated bare `list` (element type lost; `SessionSummary` lazy-imported) | `_housekeeping.py:582` | minor | Style only; lazy-import pattern is module-consistent. |
| Extra files in working tree outside this plan's scope | (see below) | not blocking | Belong to concurrent in-flight work (model-profile plans 02/03, evals, `llm.py`, `assembly.py`, `ship/SKILL.md`, `uv.lock`, `test_profile_rules_composition.py`). Not introduced by this plan — recorded, excluded from this review. |

### Tests
- Command: `uv run pytest tests/daemons/dream/test_housekeeping.py -v` (directly-affected module; full suite deferred — working tree carries unrelated concurrent changes)
- Result: 20 passed, 0 failed
- Real-model synthesis test: 21.0s, healthy warm call timings (3.4s/7.7s/2.8s/5.2s), no stalls
- Log: `.pytest-logs/<ts>-review-impl-gapc.log`
- Lint: `scripts/quality-gate.sh lint` — PASS (ruff check + format clean)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `synthesize_user_profile` (real local model): ✓ `success_signal` verified — agent reconciliation summary "Consolidated Rust developer fact to 'writes Rust daily' based on recurring evidence; dropped stale vacation note": durable cross-session fact preserved, stale fact dropped, within budget; disabled-flag and below-threshold runs both no-op (USER.md byte-identical). LLM-mediated behavior verified via the real-model test; chat interaction non-gating.

### Overall: PASS
All four tasks meet `done_when` with file:line evidence; the directly-affected test module is green (incl. a real-model behavioral gate), lint clean, boot smoke passes. The one substantive finding (marker advance keyed on pass-completion vs. confirmed write) is working-as-designed and peer-consistent — minor wording imprecision only, no code change warranted.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev gap-c-local-user-modeling`
