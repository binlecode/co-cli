# dream-housekeeping

## Problem

With Plan 1 (`online-reviewer-and-daemon-mvp`) shipped, the dream daemon process exists and executes reviewer KICKs from REPL. Items accumulate in the durable memory store via reviewer-driven extraction. Memory-side housekeeping doesn't happen automatically — duplicate / similar items accumulate; items extracted once but never recalled keep occupying the working set. Without merge + decay, the memory store grows monotonically and quality degrades.

This plan extends the daemon (introduced in Plan 1) with **clock-driven memory housekeeping** that operates on the durable memory item store. The daemon never reads transcripts here — that's reviewer's job, already in place from Plan 1. Daemon's new work is purely store-level memory consolidation.

**Companion plan:** `2026-05-22-104835-plan2b-skill-lifecycle-absorption.md` extends the same daemon with parallel skill-side housekeeping (`merge_skills` / `decay_skills`) and absorbs the orphaned curator subsystem. Ships after this plan; reuses the scheduled-tick and `run_housekeeping()` infrastructure introduced here.

## Dependencies

This plan ships after all of:

1. **Plan 1** (`online-reviewer-and-daemon-mvp`) — shipped 2026-05-22. Introduces the daemon process, recall metric fields, reviewer-populated memory store.

2. **Daemon-decouple correction** (`2026-05-22-105500-plan1.5-dream-daemon-decouple.md`) — shipped 2026-05-23.

3. **Timestamp rename** (`2026-05-22-230000-timestamp-rename-at-suffix.md`) — shipped 2026-05-23. Renamed `item.created` → `item.created_at`, `item.last_recalled` → `item.last_recalled_at`. Source already uses the renamed fields; design pseudocode below is updated to match.

## Status

Ready — all three dependencies shipped. Can begin implementation.

## Goals

1. **Add a 24h scheduled tick** to the daemon's polling main loop (post-correction):
   - Each polling iteration: drain queue → check scheduled-tick due → if due, run housekeeping → sleep
   - `dream.run_interval_hours` (default 24)
   - `dream.run_at` (default `"03:00"` local) — preferred time-of-day

2. **Memory merge phase** — recall-informed, kind-aware:
   - Cluster similar items by token-Jaccard; LLM-merge each cluster into a canonical item; archive originals
   - Use `recall_count` to pick the canonical anchor (highest-recall item's body wins)
   - **Exclude `kind=article`** — articles don't merge; they decay or stay (RAG-integrity)

3. **Memory decay phase** — recall-informed:
   - Items past `decay_after_days` AND with zero recall in `recall_protection_days` → archive
   - Items recalled within the protection window → protected from decay
   - Pinned items always protected

4. **`co dream run`** subcommand for manual one-shot housekeeping (debugging / forced run).

5. **Retire `run_dream_cycle` legacy orchestrator.** Daemon housekeeping is the only automated trigger; `/memory dream` slash command and `co_cli/memory/dream.py:run_dream_cycle` are obsoleted. See Tasks for removal scope.

## Non-goals

- **No transcript reading.** Daemon's clock-driven housekeeping operates only on the memory store + recall metrics. Reviewer (Plan 1) is the sole transcript reader.
- **No skill housekeeping in this plan.** Companion plan absorbs skill-side merge/decay + curator. See cross-link.
- **No event-triggered merge/decay.** Only the 24h timer triggers housekeeping. Manual `co dream run` is the escape hatch; no automatic merge after every REVIEW.
- **No cross-item synthesis.** Synthesis emits NEW items beyond what's in any source cluster; explicitly out of scope.
- **No historical backfill.** Reviewer doesn't backfill (Plan 1 decision); daemon doesn't either.
- **No REPL-idle gating.** Daemon and REPL coordinate solely via atomic file writes on the memory store; housekeeping is bounded-cost batch work that runs whenever the scheduled tick is due, regardless of REPL activity. The Plan 1 reviewer never grew an idle gate, and housekeeping doesn't need one either.

## Design

### Daemon main loop — post-correction + scheduled-tick

After the daemon-decouple correction ships, the loop is pure polling with signal-driven shutdown:

```python
async def main_loop(deps, queue_dir, state, cfg):
    await _initial_drain(deps, queue_dir, cfg, state)
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown.set)
    loop.add_signal_handler(signal.SIGINT, shutdown.set)

    while not shutdown.is_set():
        if list_queue_files(queue_dir):
            await _drain_queue(deps, queue_dir, cfg, state)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=cfg.poll_interval_seconds)
        except asyncio.TimeoutError:
            pass
```

This plan adds two checks inside the loop body — scheduled-tick due check, and sentinel-file (manual housekeeping) check:

```python
while not shutdown.is_set():
    if list_queue_files(queue_dir):
        await _drain_queue(deps, queue_dir, cfg, state)

    if DREAM_RUN_TAG.exists():                       # ← manual trigger
        await run_housekeeping(state, cfg)
        DREAM_RUN_TAG.unlink(missing_ok=True)
    elif scheduled_tick_due(state, cfg):             # ← scheduled tick
        await run_housekeeping(state, cfg)

    try:
        await asyncio.wait_for(shutdown.wait(), timeout=cfg.poll_interval_seconds)
    except asyncio.TimeoutError:
        pass


async def run_housekeeping(state, cfg):
    await merge_memory(state)
    await decay_memory(state)
    state.last_housekeeping_at = now()
    save_dream_state(state)
```

`scheduled_tick_due(state, cfg)` returns `True` when `now ≥ state.last_housekeeping_at + run_interval_hours`, clamped to the next `run_at` time-of-day boundary.

**Forward-compatible hook:** the companion skill-lifecycle plan adds `merge_skills(state)` and `decay_skills(state)` calls into `run_housekeeping()` after the memory phases. The wrapper is structured to accept those additions cleanly.

### Memory merge phase

```python
async def merge_memory(state):
    items = [i for i in load_memory_items(memory_dir) if i.memory_kind != "article"]
    if not items: return
    clusters = identify_clusters(
        items,
        similarity_threshold=config.memory.consolidation_similarity_threshold,
        max_cluster_size=MAX_CLUSTER_SIZE,
        max_clusters=MAX_MERGES_PER_CYCLE,
    )
    for cluster in clusters:
        canonical = max(cluster, key=lambda x: x.recall_count)
        merged_body = await llm_merge_call(
            cluster,
            anchor=canonical,
            prompt=DREAM_MEMORY_MERGE_PROMPT,
        )
        write_consolidated_item(merged_body, source_type=CONSOLIDATED)
        archive_originals(cluster)
        state.stats.memory_merged += 1
```

**Article exclusion from merge.** `kind=article` items are external source content. LLM-merging two articles produces synthesized text that mixes two sources, violating the RAG-integrity contract (same concern that ruled out per-chunk contextual retrieval in `2026-05-19-161033-plan3-memory-growth.md`). Article redundancy is handled two ways: (a) **decay** drops unused articles using `recall_count` / `last_recalled`; (b) **agent curation** distills important articles into `kind=note` / `kind=rule` items during research conversations, and those derived items merge normally. Notes and rules ARE the consolidation tier; articles are the raw substrate. Merge operates on `[user, rule, note]` only.

**Tunables retained from existing dream code:**
- `MAX_CLUSTER_SIZE = 5`
- `MAX_MERGES_PER_CYCLE = 10`

Per daily run: ≤10 merge clusters × 1 domain = ≤10 LLM calls/day. Bounded cost.

### Memory decay phase

```python
async def decay_memory(state):
    items = load_memory_items(memory_dir)
    now = datetime.now(UTC)
    candidates = []
    for item in items:
        if item.decay_protected: continue
        age_days = (now - parse_iso(item.created_at)).days
        if age_days < config.memory.decay_after_days: continue
        if item.last_recalled_at is not None:
            recall_age = (now - parse_iso(item.last_recalled_at)).days
            if recall_age < config.memory.recall_protection_days: continue
        candidates.append(item)
    batch = candidates[:MAX_DECAY_PER_CYCLE]
    state.stats.memory_decayed += archive_artifacts(batch, memory_dir, memory_store)
```

Field names match `co_cli/memory/item.py` (post-timestamp-rename): `decay_protected` (the pin equivalent), `created_at`, `last_recalled_at`. ISO8601 string parse via `co_cli/memory/decay.py:_parse_iso8601`.

### Config additions

```python
# in co_cli/config/dream.py — current shipped fields (from Plan 1 + 1.5):
class DreamSettings(BaseModel):
    enabled: bool = False
    review_timeout_seconds: int = 120
    retry_backoff_seconds: int = 30
    max_retry_attempts: int = 3
    poll_interval_seconds: int = 5
    # Added in this plan:
    run_interval_hours: int = Field(default=24, ge=1, le=720)
    run_at: str = Field(default="03:00", regex=r"^[0-2]\d:[0-5]\d$")
    max_pass_seconds: int = Field(default=600, ge=60)

# in co_cli/config/memory.py — additions:
recall_protection_days: int = Field(default=30, ge=1)
# decay_after_days already exists at 90
```

Removed from `co_cli/config/memory.py`:
- `consolidation_enabled` — no longer needed; reviewer (Plan 1) and daemon housekeeping replace the session-end orchestrator
- `consolidation_trigger` — daemon is the only trigger
- `consolidation_lookback_sessions` — not used; dream doesn't read transcripts

### `co dream run` subcommand — sentinel-file based

Manual housekeeping trigger via filesystem (no socket):

```
$ co dream run
Housekeeping requested. Check `co dream status` for results.
```

Implementation (CLI):
1. Check daemon liveness via PID file (per the daemon-decouple correction). If not running, error: "daemon not running; start with `co dream start`."
2. Write empty sentinel file at `~/.co-cli/dream/run.tag` (atomic).
3. Print the request acknowledgment. Exit.

Implementation (daemon):
- Each polling iteration, check if `DREAM_RUN_TAG` exists. If yes, run housekeeping, delete the tag.
- Worst-case latency from CLI request to daemon start = `poll_interval_seconds` (default 5).

The result summary lives in `_dream_state.json` (per-phase counters, `last_housekeeping_at`). User runs `co dream status` after the request to see the new counters. No bidirectional channel needed.

### Legacy orchestrator retirement

`co_cli/memory/dream.py:run_dream_cycle` (mine → merge → decay) and its `/memory dream` slash command (`co_cli/commands/memory.py:133-139`) are obsoleted by this plan:

- **Mining** moved to Plan 1's reviewer (already shipped).
- **Merge + decay** move to `_housekeeping.py` (this plan).
- **`/memory dream` slash** removed — `co dream run` is the new manual trigger.

Keep these around: `MemoryItem`, `archive_artifacts`, `find_decay_candidates`, `token_jaccard`, `_DREAM_MERGE_PROMPT` text. They're reused as building blocks. Delete: the `run_dream_cycle` orchestrator, `_mine_transcripts`, `DreamState` / `DreamStats` / `DreamResult` (replaced by daemon's `_dream_state.json` + per-phase counters), `dream_state_path`, `load_dream_state`, `save_dream_state`, `build_dream_miner_agent`, `_chunk_dream_window`, `dream_miner.md` prompt.

## Tasks

- [x] ✓ DONE **T1.** Add scheduled-tick check + sentinel-file check inside the polling main loop (`co_cli/daemons/dream/_loop.py:22-67`):
  - Implement `scheduled_tick_due(state, cfg)` — returns `True` when `now ≥ state.last_housekeeping_at + run_interval_hours`, clamped to next `run_at` boundary
  - Add manual-trigger check: `if DREAM_RUN_TAG.exists(): run_housekeeping; unlink tag`
  - Add scheduled-tick check: `elif scheduled_tick_due(state, cfg): run_housekeeping`
  - Both fire `run_housekeeping(state, cfg)` from inside the polling loop body, between queue drain and the sleep
  - Note: the current loop uses `continue` after a drain to rescan; the housekeeping checks must run *after* queue is drained, not interleaved mid-drain — restructure so housekeeping fires on the empty-queue branch

- [x] ✓ DONE **T2.** Implement `run_housekeeping(state, cfg)`:
  - Run `merge_memory` (the companion skill-lifecycle plan adds `merge_skills` after this)
  - Run `decay_memory` (companion plan adds `decay_skills` after this)
  - Update `state.last_housekeeping_at`; persist `_dream_state.json`
  - Wall-clock cap: abort if `max_pass_seconds` exceeded; log incomplete state

- [x] ✓ DONE **T3.** Memory merge phase:
  - Port `co_cli/memory/dream.py:_merge_similar_artifacts` logic into a new `merge_memory(state)` in `co_cli/daemons/dream/_housekeeping.py`
  - Add recall-aware canonical pick: `max(cluster, key=lambda x: x.recall_count)`
  - **Filter `kind=article` items out of the cluster input** — articles don't merge; they decay or stay (see Article exclusion above)
  - Move prompt to `co_cli/daemons/dream/prompts/memory_merge.md`

- [x] ✓ DONE **T4.** Memory decay phase:
  - Port `co_cli/memory/dream.py:_decay_sweep` logic into a new `decay_memory(state)` in `co_cli/daemons/dream/_housekeeping.py`
  - Reuse `co_cli/memory/decay.py:find_decay_candidates` as the candidacy filter (already honors `decay_protected` + `last_recalled` cutoff)
  - Add recall-informed protection: extend `find_decay_candidates` to also skip items where `(now - parse_iso(last_recalled)) < recall_protection_days` (separate window from `decay_after_days`)

- [x] ✓ DONE **T5.** Config knob changes:
  - Add to `co_cli/config/dream.py` (extra env entries in `DREAM_ENV_MAP`):
    - `run_interval_hours: int = 24` + env `CO_DREAM_RUN_INTERVAL_HOURS`
    - `run_at: str = "03:00"` + env `CO_DREAM_RUN_AT`
    - `max_pass_seconds: int = 600` + env `CO_DREAM_MAX_PASS_SECONDS`
  - Add to `co_cli/config/memory.py` (and to `MEMORY_ENV_MAP`):
    - `recall_protection_days: int = 30` + env `CO_MEMORY_RECALL_PROTECTION_DAYS`
  - Remove from `co_cli/config/memory.py` (and `MEMORY_ENV_MAP`):
    - `consolidation_enabled`
    - `consolidation_trigger`
    - `consolidation_lookback_sessions`

- [x] ✓ DONE **T6.** `co dream run` subcommand — sentinel-file based (post-correction):
  - Add `DREAM_RUN_TAG = USER_DIR / "dream" / "run.tag"` constant in `co_cli/config/core.py`
  - CLI handler in `co_cli/commands/dream.py`: check daemon liveness via PID file; if alive, atomic-write empty sentinel at `DREAM_RUN_TAG`; print "Housekeeping requested. Check `co dream status` for results."
  - Daemon side: covered by T1 (sentinel-file check inside polling loop)
  - If daemon not running: error message; do NOT spawn ad-hoc pass

- [x] ✓ DONE **T7.** New daemon-side persisted housekeeping state:
  - The old `_dream_state.json` lives in `memory_dir` (from `co_cli/memory/dream.py:dream_state_path`); it is deleted in T8.
  - T7 creates a NEW persistent schema at `DREAM_DAEMON_DIR / "_dream_state.json"` (distinct location). The runtime `DaemonState` in `co_cli/daemons/dream/_state.py` is in-memory only; T7 adds a separate persisted `HousekeepingState` (or similarly named) serialized to `DREAM_DAEMON_DIR / "_dream_state.json"`.
  - Fields: `last_housekeeping_at: datetime | None`, `stats: { memory_merged: int, memory_decayed: int }`
  - Forward-compatible: companion plan adds `skill_merged` / `skill_decayed` counters without breaking memory counters

- [x] ✓ DONE **T8.** Retire legacy `run_dream_cycle` orchestrator and `/memory dream` slash:
  - Delete from `co_cli/memory/dream.py`: `run_dream_cycle`, `_mine_transcripts`, `_merge_similar_artifacts`, `_decay_sweep`, `_preview_merge_clusters`, `_preview_decay_candidates`, `DreamState`, `DreamStats`, `DreamResult`, `dream_state_path`, `load_dream_state`, `save_dream_state`, `build_dream_miner_agent`, `_chunk_dream_window`. (Logic ported into `_housekeeping.py` via T3/T4.)
  - Decide module fate: either delete `co_cli/memory/dream.py` entirely (preferred — module loses its purpose) or leave behind only shared helpers that `_housekeeping.py` imports.
  - Delete `co_cli/memory/prompts/dream_miner.md`. Move `dream_merge.md` to `co_cli/daemons/dream/prompts/memory_merge.md` (per T3).
  - Remove the `/memory dream` slash: delete handler `_subcmd_knowledge_dream` (`co_cli/commands/memory.py:131-145`) and its dispatch entry (`co_cli/commands/memory.py:280-281`); update the usage string on line 14 and the dispatch docstring.
  - Update `_subcmd_knowledge_stats` (`co_cli/commands/memory.py:222-260`): it imports `load_dream_state` (line 225) and displays `state.last_dream_at` / `state.stats.*` (lines 241-250). Those fields come from the legacy `DreamState` being deleted. Replace with a stub line pointing to `co dream status`, or wire to the new daemon-side `_dream_state.json` if available.
  - Drop eval imports of `run_dream_cycle` (`evals/eval_memory.py:53,781`, `evals/eval_daily_chat.py:55,532`).
  - Update or delete `docs/specs/dream.md` § "Batch maintenance cycle" — the dream cycle is no longer a separate orchestrator.

- [x] ✓ DONE **T9.** Tests:
  - Scheduled tick: compute next tick from `last_housekeeping_at + run_interval_hours + run_at` — boundaries correct across midnight, DST
  - Merge memory: cluster with mixed `recall_count` → highest-recall item's body becomes canonical
  - Merge memory: mixed-kind store with article + note + rule items → only note/rule cluster; articles untouched
  - Decay memory: item with age > 90d AND zero recall in 30d → archived
  - Decay memory: item with age > 90d but recalled within 30d → protected
  - Decay memory: `decay_protected` item with age > 90d AND zero recall → protected (pin overrides)
  - Tiebreaker on all-zero recall (e.g., most recent `created` wins)
  - `co dream run` triggers housekeeping; returns summary
  - `co dream run` when daemon not running: error, no spawn

- [x] ✓ DONE **T10.** Spec sync:
  - Update `docs/specs/dream.md` — replace § "Batch maintenance cycle" with the daemon housekeeping model; remove the "Plan 2 territory" deferred-absorption note
  - Update `docs/specs/memory.md` — decay is recall-informed; `consolidation_*` knobs gone
  - Auto-invoked by orchestrate-dev `/sync-doc`

## Test plan

| Test | Scope | Type |
|---|---|---|
| Scheduled tick timing | Next run computed correctly across midnight / DST / `run_at` boundary | Unit |
| Merge memory recall-aware canonical | Highest-recall item's body becomes canonical | Unit |
| Merge memory tiebreaker | All-zero-recall cluster picks by recency | Unit |
| Merge memory article exclusion | Mixed-kind store: only note/rule merged; articles untouched | Unit |
| Decay memory recall protection | Recent recall protects regardless of age | Unit |
| Decay memory candidacy | Aged AND zero-recall → archive | Unit |
| Decay protected item | `decay_protected=True` + aged + zero-recall → protected | Unit |
| `co dream run` manual trigger | Sentinel-file write triggers next-poll housekeeping; summary returned | Integration |
| `co dream run` when daemon down | Clean error message; no spawn | Integration |
| Legacy `/memory dream` slash gone | Slash returns "unknown command"; `run_dream_cycle` import errors | Static check |
| End-to-end one cycle | Daemon runs scheduled tick → memory merge + memory decay → state updated | E2E |

## Risks

- **Recall-informed decay over-aggressive on early adopters.** Items extracted before recall metrics existed have `recall_count=0` lazily defaulted on load. If user just upgraded and runs dream the next day, items might get decayed despite never having had a chance to be recalled. Mitigation: `recall_protection_days` window is checked from `created` for items with `last_recalled=None` (no recall yet → protected for first 30 days of life).

- **Merge LLM cost.** ≤10 merge calls/day for memory alone. Reasonable for any provider.

- **Merge produces lower-quality canonical body.** The highest-recall item's body becomes the LLM merge anchor, but the merge prompt may still over-summarize or lose nuance. Mitigation: existing `_MERGED_BODY_MIN_CHARS` guard (skip merge if result too short) plus future eval coverage.

- **Wall-clock cap aborts mid-pass.** If `max_pass_seconds` (600) is exceeded, housekeeping aborts. Mitigation: log incomplete state; next tick resumes; per-call timeouts inside merge/decay protect against any single-call hang.

- **Hook points for skill-lifecycle-absorption.** `run_housekeeping()` must structure its body so the companion plan can add `merge_skills` and `decay_skills` calls without re-architecting the wrapper.

## Implementation Footprint Summary

**Added:**
- `co_cli/daemons/dream/prompts/memory_merge.md` (move from existing `dream_merge.md`)
- `co_cli/daemons/dream/_housekeeping.py` — memory merge + decay phases
- Scheduled-tick + sentinel-file check logic in `co_cli/daemons/dream/_loop.py` (extends the polling main loop from the daemon-decouple correction)
- `DREAM_RUN_TAG` constant in `co_cli/config/core.py`
- `co dream run` CLI subcommand (sentinel-file based; no socket)
- `recall_protection_days` memory config knob
- `run_interval_hours`, `run_at`, `max_pass_seconds` dream config knobs

**Refactored:**
- Port `_merge_similar_artifacts` → `merge_memory` and `_decay_sweep` → `decay_memory` into `co_cli/daemons/dream/_housekeeping.py`; add recall-aware logic
- `co_cli/memory/decay.py:find_decay_candidates` — extend with `recall_protection_days` window check
- `_dream_state.json` schema — add `last_housekeeping_at` + memory counters

**Deleted:**
- `co_cli/memory/dream.py:run_dream_cycle`, `_mine_transcripts`, `_merge_similar_artifacts`, `_decay_sweep`, `_preview_*`, `DreamState`, `DreamStats`, `DreamResult`, `build_dream_miner_agent`, `_chunk_dream_window`, `dream_state_path`, `load_dream_state`, `save_dream_state` (module likely deletable entirely after port)
- `co_cli/memory/prompts/dream_miner.md`
- `co_cli/memory/prompts/dream_merge.md` (moved to `co_cli/daemons/dream/prompts/memory_merge.md`)
- `/memory dream` slash subcommand in `co_cli/commands/memory.py:133-139`
- `co_cli/config/memory.py`: `consolidation_enabled`, `consolidation_trigger`, `consolidation_lookback_sessions` + their `MEMORY_ENV_MAP` entries

**Config knobs (this plan's additions / removals):**

| Knob | Default | Purpose |
|---|---|---|
| `dream.run_interval_hours` | 24 | Scheduled housekeeping cadence |
| `dream.run_at` | "03:00" | Preferred local time-of-day |
| `dream.max_pass_seconds` | 600 | Wall-clock cap per housekeeping pass |
| `memory.recall_protection_days` | 30 | Recall window that protects from decay |

**Removed:** `memory.consolidation_trigger`, `memory.consolidation_lookback_sessions`.

## Future scope (out of scope here)

- **Skill-side housekeeping** — companion plan `2026-05-22-104835-plan2b-skill-lifecycle-absorption.md`.
- **Cross-item synthesis.** Higher-order theme items from clusters. Revisit after merge + decay are stable.
- **Event-triggered merge.** Currently merge only fires on the 24h tick. Could plausibly trigger merge after N items added since last merge; premature.

---

## Delivery Summary — 2026-05-23

| Task | done_when | Status |
|---|---|---|
| T1 | `scheduled_tick_due` + `_maybe_housekeep` integrated into polling loop on empty-queue branch | ✓ pass |
| T2 | `run_housekeeping(deps, cfg)` wraps merge → decay under `asyncio.timeout(max_pass_seconds)`; persists state | ✓ pass |
| T3 | `merge_memory(deps, state)` in `_housekeeping.py` with recall-anchored canonical + article exclusion; prompt moved to `daemons/dream/prompts/memory_merge.md` | ✓ pass |
| T4 | `decay_memory(deps, state)` + `find_decay_candidates` extended with separate `recall_protection_days` window | ✓ pass |
| T5 | dream.* gains `run_interval_hours` / `run_at` / `max_pass_seconds`; memory.* gains `recall_protection_days` and drops `consolidation_enabled` / `consolidation_trigger` / `consolidation_lookback_sessions` | ✓ pass |
| T6 | `DREAM_RUN_TAG` in `config/core.py`; `co dream run` checks PID liveness, atomic-writes sentinel, errors cleanly when daemon down | ✓ pass |
| T7 | `HousekeepingState` + `HousekeepingStats` pydantic models with `load_/save_housekeeping_state` at `DREAM_DAEMON_DIR/_dream_state.json` | ✓ pass |
| T8 | `co_cli/memory/dream.py`, `_window.py`, `prompts/dream_miner.md` deleted; `/memory dream` slash gone; `/memory stats` wired to `HousekeepingState`; eval imports point at `merge_memory` | ✓ pass |
| T9 | 11 housekeeping tests (scheduled tick boundaries, canonical pick, article exclusion, decay candidacy variants, sentinel-error path) | ✓ pass |
| T10 | `dream.md` §2 rewritten + 4 other specs cleaned up via cross-doc scope expansion (`memory.md`, `config.md`, `observability.md`, `01-system.md`); `decay.py` docstring updated | ✓ pass |

**Plan deviation — T5 scope expansion (user-approved mid-execution).** The plan called for removing `consolidation_enabled` from config, but did not address that the same field also gated write-time Jaccard dedup in `save_memory_item`. After user confirmation, dedup is now hardcoded ON: the parameter was dropped from `save_memory_item` and the caller in `co_cli/tools/memory/manage.py`. Test `test_save_memory_item_jaccard_dedup_skips_near_identical` was updated accordingly.

**Module deletion went beyond plan.** Plan offered the option to leave shared helpers in `co_cli/memory/dream.py`; I went with the preferred path (full deletion). `co_cli/memory/_window.py` was an orphan after that (only consumer was `_mine_transcripts`), so it was deleted too.

**Tests:** scoped — 33 passed, 0 failed (`tests/daemons/` + `tests/test_flow_memory_write.py`). Full-suite run deferred to `/review-impl`.
**Doc Sync:** clean after fixes — `dream.md`, `memory.md`, `config.md`, `observability.md`, `01-system.md` all updated; one source docstring touched (`co_cli/memory/decay.py`).

**Overall: DELIVERED**
All tasks pass `done_when`. Lint clean. Scoped tests green. Spec sync covers all cross-doc drift discovered during integration.

**Next step:** `/review-impl plan2a` — full suite + evidence scan + auto-fix → verdict appended to plan.

---

## Implementation Review — 2026-05-23

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| T1 | scheduled_tick_due + _maybe_housekeep on empty-queue branch | ✓ pass | `_loop.py:33-54` scheduled_tick_due; `_loop.py:57-67` _maybe_housekeep; `_loop.py:89` invoked only when `not files` |
| T2 | run_housekeeping wraps merge→decay under timeout, persists state | ✓ pass | `_housekeeping.py:210-233` — `asyncio.timeout(cfg.max_pass_seconds)` wrapping; `state.last_housekeeping_at` set on every path; partial counters persisted on TimeoutError |
| T3 | merge_memory recall-anchored canonical + article exclusion; prompt moved | ✓ pass | `_housekeeping.py:97-98` article filter; `_housekeeping.py:111` `max(cluster, key=(recall_count, created_at))`; prompt at `daemons/dream/prompts/memory_merge.md:1` |
| T4 | decay_memory + find_decay_candidates honors recall_protection_days | ✓ pass | `decay.py:38,52-53` separate recall_cutoff window; `_housekeeping.py:200` reuses find_decay_candidates |
| T5 | dream + memory config knobs added/removed; dedup hardcoded ON | ✓ pass | `config/dream.py:27-29` new fields + env map lines 11-13; `config/memory.py:62` recall_protection_days; `service.py:142-260` Jaccard dedup always on; no production refs to consolidation_* knobs |
| T6 | DREAM_RUN_TAG in core; co dream run checks PID + atomic sentinel write | ✓ pass | `config/core.py` DREAM_RUN_TAG = DREAM_DAEMON_DIR/"run.tag"; `commands/dream.py:93-109` checks `status_daemon` then `atomic_write_text(DREAM_RUN_TAG, "")`; clean error exit 1 when daemon down |
| T7 | HousekeepingState/Stats pydantic + load/save at DREAM_DAEMON_DIR | ✓ pass | `_state.py:42-46` HousekeepingStats; `_state.py:49-59` HousekeepingState; `_state.py:67-82` load/save with atomic_write_text |
| T8 | dream.py / _window.py / dream_miner.md deleted; /memory dream gone; /memory stats wired to HousekeepingState; eval imports → merge_memory | ✓ pass | Files absent; `commands/memory.py` no `_subcmd_knowledge_dream` and no dispatch entry; `eval_memory.py:52` and `eval_daily_chat.py:55` import merge_memory from daemons/dream/_housekeeping |
| T9 | 12 housekeeping tests covering scheduled-tick boundaries, canonical, article exclusion, decay variants, sentinel-error path | ✓ pass | `tests/daemons/dream/test_housekeeping.py:79-253` — 12 functions, zero mocks/fakes (only `monkeypatch.setenv` for CO_HOME isolation) |
| T10 | dream.md §2 rewritten + 4 spec sync (memory, config, observability, 01-system); decay.py docstring updated | ✓ pass | Zero references to consolidation_*/run_dream_cycle/"Plan 2 territory" in docs/specs/; new knobs documented in config.md and dream.md |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `run_housekeeping(deps, cfg)` cfg parameter untyped | _housekeeping.py:210 | blocking-minor | Added `cfg: DreamSettings`; module import added at line 18 |
| `scheduled_tick_due(state, cfg)` cfg untyped | _loop.py:28 | blocking-minor | Added `cfg: DreamSettings`; module import added |
| `_maybe_housekeep(deps, cfg)` both params untyped | _loop.py:52 | blocking-minor | Added `deps: CoDeps` (TYPE_CHECKING) + `cfg: DreamSettings` |
| Stale `run_dream_cycle()` reference in docstring | evals/_timeouts.py:71 | blocking-minor | Rewrote docstring to describe current callsite (merge_memory in eval_memory.py / eval_daily_chat.py); constant DREAM_CYCLE_BUDGET_S retained as still-used budget knob |
| Test fixture `Content multi A`/`B` collapses to identical tokensets {content, multi} after stopword/single-char filter — 100% Jaccard match, T5 deviation now dedupes them to one item | tests/tools/memory/test_recall_metrics.py:157-158 | blocking | Updated fixture to distinct content (`"alpha bravo charlie delta"` / `"echo foxtrot golf hotel"`) so two genuinely distinct items are created — test purpose (multi-path recall update) preserved |

### Pre-existing Patterns (Not Flagged)

- `main_loop` / `_process_kick_file` / `_process_review` in `_loop.py` still have untyped `deps`/`cfg` — pre-existing Plan 1 code, outside plan2a scope. Consistent with how Plan 1 shipped; not introduced by this delivery.
- `commands/dream.py` uses `typer.echo` for CLI commands (start/status/stop/run) while `handle_dream_slash` uses `console.print`. This pattern matches sibling commands and is module-consistent.
- CHANGELOG.md and `docs/reference/RESEARCH-*.md` retain references to the deleted `run_dream_cycle` — these are intentional historical snapshots (changelog = release history; reference docs = frozen analysis); specs (docs/specs/) are clean.

### Tests

- Command: `uv run pytest`
- Result: **571 passed, 0 failed** in 325.87s
- Log: `.pytest-logs/20260523-011639-review-impl-full-2.log`

Test-fixture fix verified independently:
- `uv run pytest tests/tools/memory/test_recall_metrics.py -v` → 8 passed.
- `uv run pytest tests/daemons/dream/test_housekeeping.py -v -x` → 12 passed.

### Behavioral Verification

- `uv run co dream --help`: ✓ lists `run` subcommand with description "Request a one-shot housekeeping pass from the running daemon."
- `uv run co dream status` (daemon down): ✓ returns `{"running": false, "queue_depth": 34, "failed_count": 0}`
- `uv run co dream run` (daemon down): ✓ prints "dream daemon not running; start with `co dream start`." to stderr; exits 1; sentinel file NOT written (verified by `test_dream_run_errors_when_daemon_not_running`)
- `success_signal` verified for T6: user sees clean error and no spawn when daemon is down — matches T6 spec.
- Positive `co dream run` (daemon up → housekeeping pass) deferred to eval-layer E2E coverage (`eval_memory.py W3.F`, `eval_daily_chat.py W1.D`) per the test file's own docstring contract.
- `co status` is not a top-level subcommand in this project (CLI surface: `chat`, `tail`, `trace`, `dream`); skill's generic verification step not applicable here.

### Overall: **PASS**

Plan2a is fully implemented and verified. All 10 tasks meet their `done_when` criteria; 5 latent issues uncovered during review were auto-fixed (4 type-hint gaps + 1 stale test fixture from the T5 deviation). Full test suite green (571 passed). Lint clean. Behavioral verification of the new `co dream run` subcommand confirms spec match.

**Ready for `/ship plan2a`.**
