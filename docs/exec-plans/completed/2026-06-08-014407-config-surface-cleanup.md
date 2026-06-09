# Config Surface Cleanup — dead settings + dream consolidation config-bypass

## Context

A system-wide config-surface audit (2026-06-08, grounded against source) found the config hierarchy clean — `load_config()` → lazy `_settings` singleton → `copy.deepcopy` into `CoDeps.config` → `ctx.deps.config.<section>.<field>`, read-only after bootstrap; no getenv config-bypass except `CO_HOME` (necessary, pre-Settings). Three actionable items surfaced; everything else (the `SPILL_THRESHOLD_CHARS` per-tool vs `compaction.spill_ratio` context-fraction split, `memory.*`/`skills.*` parallel decay knobs, bootstrap-time mutation of the deep-copied session config, hardcoded model-coherence constants) was verified coherent and is out of scope.

### Code Accuracy Verification (grounded against source, 2026-06-08)

**1. Dead settings — defined, env-mapped, documented, zero consumers:**
- `MemorySettings.max_item_count` — field `co_cli/config/memory.py:58` (`default=300, ge=1`); env-map entry `:30` (`CO_MEMORY_MAX_ITEM_COUNT`). No `DEFAULT_` const. **Zero reads** in `co_cli/` or `tests/` (no corpus-size cap / eviction logic exists; lifecycle is governed by `decay_after_days`). Spec already hedges: `memory.md:74` "*not directly enforced*"; `config.md:184` overclaims ("Max memory items before decay").
- `MemorySettings.recall_half_life_days` — field `co_cli/config/memory.py:61` (`default=DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS, ge=1`); env-map entry `:33`; const `DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS = 30` `:16`. **Zero reads** (recall ranking uses BM25/vector + `recall_protection_days` in `memory/decay.py`; no time-decay half-life scoring exists). Spec already hedges: `memory.md:77` "*not currently consumed by recall ranking*"; `config.md:226` overclaims ("Half-life for time-decay scoring in recall ranking").

**2. Dream consolidation config-bypass (real, minor):**
- `_write_consolidated_item` (`co_cli/daemons/dream/_housekeeping.py:138`) calls `save_memory_item(...)` **without** `consolidation_similarity_threshold`, so its save-time Jaccard re-dedup falls back to the hardcoded `0.75` default param (`co_cli/memory/service.py:145`, consumed at `:221` → `find_similar_memory_items`). The cluster **merge-decision** path already honors config (`_housekeeping.py:93` reads `deps.config.memory.consolidation_similarity_threshold`); only the post-merge save re-dedup ignores it. The other caller `co_cli/tools/memory/manage.py:161` passes the config value correctly.
- `save_memory_item` has **~15 test callers** plus `manage.py` relying on the `0.75` default param. Removing the default is high-churn and unjustified — a sensible default on a pure library function is not an anti-pattern. Fix is the single omitting caller.

**3. Hidden ops env var (optional):**
- `maybe_autospawn_dream` (`co_cli/bootstrap/core.py:475`) reads `os.environ.get("CO_DREAM_NO_AUTOSPAWN")` directly — a deliberate test/CI escape hatch documented in the docstring (`:459`), outside the `Settings` surface. Works correctly; only a surface-consistency wart.

## Problem & Outcome

**Problem:** two memory settings are user-visible (field + env var + spec rows) but inert — they mislead operators into thinking they tune behavior that doesn't exist. Separately, the dream daemon's post-consolidation save silently ignores a configured `consolidation_similarity_threshold`.

**Outcome:** the dead settings are removed from the config surface (or, if PO elects, implemented); the dream consolidation save path honors `config.memory.consolidation_similarity_threshold`; the config surface contains only settings that drive behavior.

**Failure cost:** silent. Today an operator can set `CO_MEMORY_MAX_ITEM_COUNT` / `CO_MEMORY_RECALL_HALF_LIFE_DAYS` and observe **no effect** — a trust-eroding no-op with no error. And a user who tightens `consolidation_similarity_threshold` still gets `0.75`-governed dedup on every dream-written consolidated item.

## Scope

**In scope:**
- Fix `_write_consolidated_item` to pass `deps.config.memory.consolidation_similarity_threshold`.
- Remove the two dead `MemorySettings` fields + their env-map entries + the unused `DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS` const (TL position; PO confirms remove-vs-implement).

**Out of scope:**
- `docs/specs/*` edits (`config.md`, `memory.md` rows for the removed settings) — handled by `/sync-doc` post-delivery (workflow rule). Sync surface enumerated in Testing.
- Removing the `0.75` default param on `save_memory_item` — rejected: ~15 test callers + `manage.py` depend on it; a pure-function default is not the anti-pattern.
- `CO_DREAM_NO_AUTOSPAWN` → `dream.autospawn` promotion — deferred (see Open Questions); deliberate, working, low value.
- All verified-coherent items (spill split, parallel decay knobs, bootstrap mutation, model-coherence constants).

## Behavioral Constraints

1. **Zero behavior change from the removals.** The two fields have no consumers, so deleting them cannot alter runtime behavior; only the config *surface* shrinks.
2. **Hard removal, no compat shim** (zero-backward-compat house rule). With `MemorySettings` `extra="forbid"`, a `settings.json`/env that still sets the removed keys will now raise at load — acceptable; these were never functional, migration is a manual one-off.
3. **Dream fix is config-honoring only** — no change to clustering logic (`_housekeeping.py:93` already correct), only the save-time threshold the consolidated write uses.
4. **`save_memory_item` signature unchanged** — the `0.75` default param stays.

## High-Level Design

Two independent edits in two files. TASK-1 threads the already-available `deps.config.memory.consolidation_similarity_threshold` into the one save call that omits it. TASK-2 deletes two inert fields + two env-map rows + one unused module constant. No new abstractions; no logic added.

## Tasks

### ✓ DONE TASK-1 — Dream consolidation honors configured threshold
- **files:** `co_cli/daemons/dream/_housekeeping.py`, `tests/test_flow_skills_manage.py`
- **done_when:** `_write_consolidated_item` passes `consolidation_similarity_threshold=deps.config.memory.consolidation_similarity_threshold` to `save_memory_item`; a functional test builds deps with `config.memory.consolidation_similarity_threshold` set to a non-default value and asserts the consolidated-write save-time dedup is governed by that value (not `0.75`) — verified by observable merge/no-merge outcome, run via `uv run pytest <test file> -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-cfg.log`.
- **success_signal:** A user who sets `consolidation_similarity_threshold` sees the dream daemon's consolidated writes deduped at that threshold, not the hardcoded default.
- Note: place the test in whichever existing dream/memory consolidation test module already constructs the consolidation path; the file above is a placeholder — Dev picks the module that exercises `_write_consolidated_item` and adjusts `files:` with a `⚠ Extra file:` note if different.

### ✓ DONE TASK-2 — Remove dead memory settings
- **files:** `co_cli/config/memory.py`, `settings.reference.json`
- **done_when:** `grep -rn "max_item_count\|recall_half_life_days\|DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS" co_cli/ settings.reference.json` returns nothing; `uv run python -c "from co_cli.config.core import load_config; load_config()"` exits 0 **and** a round-trip load of `settings.reference.json` succeeds under `extra="forbid"` (the reference file must stay loadable).
- **success_signal:** N/A (dead-field removal — no runtime behavior change).
- Includes: delete field `max_item_count` (`co_cli/config/memory.py:58`), field `recall_half_life_days` (`:61`), env-map entries `:30` and `:33`, the unused const `DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS` (`:16`); and delete `"recall_half_life_days": 30` from `settings.reference.json:36` (this file has no `max_item_count` entry, so only the one line). Required because `MemorySettings` is `extra="forbid"` (`memory.py:40`) — a reference config still carrying the removed key would raise on load for anyone who copies it.
- **prerequisites:** none (independent of TASK-1).

## Testing

- **TASK-1 functional test:** asserts the configured threshold governs the dream consolidated-write dedup (observable merge/no-merge), via the existing consolidation test module.
- **TASK-2 load check:** `load_config()` succeeds with the fields removed; grep-clean of the symbols in `co_cli/`.
- **Full suite:** at `/ship` (safety net), per the standard gate. Watch for any test/fixture that set the removed keys (audit found none in `tests/`).
- **Reference-config round-trip:** TASK-2's `done_when` includes loading `settings.reference.json` through `load_config`/`MemorySettings` to confirm it stays valid under `extra="forbid"` after the key is removed.
- **Post-delivery `/sync-doc` surface** (auto-invoked by `/orchestrate-dev`): remove the `memory.max_item_count` and `memory.recall_half_life_days` rows from `docs/specs/config.md` (`:184`, `:226`) and `docs/specs/memory.md` (`:74`, `:77`). Note this is a **spec-accuracy correction** (config.md currently overclaims these as functional), not a removal of advertised-and-working behavior — the behavior never existed.

## Open Questions

1. **Remove vs implement the two dead settings (for PO).** TL position: **remove** — `max_item_count` duplicates the role of `decay_after_days` (lifecycle already bounded by decay); `recall_half_life_days` would require building time-decay recall scoring that doesn't exist and wasn't requested. Implementing either is a net-new feature, not cleanup. Resolve remove unless PO argues a concrete near-term need.
2. **`CO_DREAM_NO_AUTOSPAWN` promotion (for PO).** Promote to `dream.autospawn` config field + `CO_DREAM_AUTOSPAWN` env map, or leave as the documented ops hatch? TL position: **leave** — it's an external test/CI knob, not a user setting; promoting it adds surface for no operator benefit. Out of scope unless PO objects.

## Final — Team Lead

Plan approved (C2 — both Core Dev and PO returned `Blocking: none`). PO confirmed remove-vs-implement: **remove** both dead settings (no consumers, no near-term need; implementing either is net-new feature work, not cleanup), and **leave** `CO_DREAM_NO_AUTOSPAWN` as the documented ops hatch. Adopted Core Dev blocker CD-M-1: TASK-2 now also strips `recall_half_life_days` from `settings.reference.json:36` and round-trip-loads that file (required under `MemorySettings` `extra="forbid"`). Both open questions resolved; both TASK-1 minors folded into Dev's test-module selection.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev config-surface-cleanup`

## Delivery Summary — 2026-06-08

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `_write_consolidated_item` passes `consolidation_similarity_threshold=deps.config.memory.consolidation_similarity_threshold`; functional test asserts config value (not `0.75`) governs save-time dedup via observable merge/no-merge | ✓ pass |
| TASK-2 | grep-clean of `max_item_count`/`recall_half_life_days`/`DEFAULT_MEMORY_RECALL_HALF_LIFE_DAYS` in `co_cli/`+`settings.reference.json`; `load_config()` exits 0; reference `memory` block round-trips under `extra="forbid"` | ✓ pass |

**Files changed:**
- `co_cli/daemons/dream/_housekeeping.py` — TASK-1 fix (1 line: thread config threshold into the consolidated save).
- `tests/daemons/dream/test_housekeeping.py` — TASK-1 functional test. `⚠ files: adjustment` — plan placeholder `tests/test_flow_skills_manage.py` was wrong; the memory-consolidation path lives in this module (has `_item`/`_write_md` fixtures). Test drives `_write_consolidated_item` directly (no LLM — the merge LLM call is in `_merge_cluster`).
- `co_cli/config/memory.py` — TASK-2: deleted 2 fields, 2 env-map entries, 1 unused const.
- `settings.reference.json` — TASK-2: removed `recall_half_life_days` (memory block now `{}`).
- `docs/specs/config.md`, `docs/specs/memory.md` — `/sync-doc`: removed the 3 stale rows (config.md also had an orphan duplicate `Memory` section holding only the dead setting — removed).

**Negative control:** with the TASK-1 fix stripped, the new test fails (`0.43` Jaccard body saved as a 2nd file under the hardcoded `0.75`); with the fix, it dedups into the existing note (1 file). Confirms the test guards the behavior, not a tautology.

**Tests:** scoped — 25 passed, 0 failed (`tests/daemons/dream/test_housekeeping.py` + `tests/test_flow_bootstrap_config_loading.py`). No config test referenced the removed fields.
**Doc Sync:** fixed (config.md: dead row + orphan Memory subsection; memory.md: 2 dead rows).

**Overall: DELIVERED**
Both tasks pass `done_when`, lint clean, scoped tests green, docs synced.

## Implementation Review — 2026-06-08

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_write_consolidated_item` passes configured threshold; functional test asserts merge/no-merge at non-default value | ✓ pass | `_housekeeping.py:144` — kwarg present; `service.py:221,226` — threshold consumed by Jaccard gate; `test_housekeeping.py:196,203-207` — threshold=0.3, file-count assertion |
| TASK-2 | grep-clean; `load_config()` OK; reference round-trip under `extra="forbid"` | ✓ pass | `memory.py` — 2 fields, 2 env-map entries, 1 const absent; `settings.reference.json` — `"memory": {}`; grep over `co_cli/`+ref file returns zero hits |

### Adversarial check — Jaccard non-vacuity
Existing body `"alpha bravo charlie delta echo"` (5 tokens, no STOPWORDS hits). Merged body `"alpha bravo charlie foxtrot golf"` (5 tokens, 3 shared). Jaccard = 3/7 ≈ 0.43. This is > 0.3 (threshold in test → dedup → 1 file) and < 0.75 (default → no dedup → 2 files). Test assertion `len(md_files) == 1` is mathematically non-vacuous and would catch a regression to the hardcoded default.

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -x`
- Result: 625 passed, 0 failed, 1 warning
- Log: `.pytest-logs/20260608-140759-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ starts clean, config loads, all commands present
- `uv run python -c "load_config()"`: ✓ `memory.decay_after_days=90`, `consolidation_threshold=0.75` — remaining fields intact, removed fields absent
- No user-facing CLI surface changed by either task; dream consolidation threshold fix is internal daemon path verified by functional test.

### Overall: PASS
Both tasks fully implemented, full suite green (625/625), no blocking issues, lint clean, behavioral verification clean.
