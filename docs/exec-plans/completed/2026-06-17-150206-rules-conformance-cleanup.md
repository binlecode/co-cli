# Plan: rules-conformance-cleanup

## Context

Periodic whole-codebase conformance audit (`/audit-conformance`, scope `co_cli/`) run 2026-06-17. The tree is in strong structural shape — boundaries (R4), visibility (R5), `__init__` hygiene, import-time side effects (R6), optimistic flags (R7), swallowed errors (R12), backward-compat residue (R8), naming (R9), and duplication (R11) are all clean. This run follows an earlier same-session cleanup that already removed 6 dead/one-sided items and fixed an import-time side effect + a duplication.

The one accreted class that remains is **R1 one-sided (write-only / dead) members** — 6 confirmed after a blind cold-read pass dropped 1 false positive. This is a *recurring* class (the prior round fixed a sibling batch), so it is the correct single theme for this plan.

## Recurrence note

- **R1 one-sided/dead fields** recur across rounds (prior round: 6; this round: 6). The structural reason is that fields are added to result/state dataclasses optimistically and the consumer is dropped or never wired. No structural eliminator exists short of discipline at the write site; this plan drains the current batch.
- **Template-defect recurrence (resolved):** the L2 completed-plan grep surfaced 37 historical `co status` workaround disclaimers — a phantom-command class. The defect (skill template) was fixed earlier this session; no task needed. Recorded here so the next audit does not re-open it.

## Scope (this round)

One coherent theme: **subtract confirmed write-only / dead members.** All 6 are behavior-preserving deletions (no production reader). Three are clean; three carry test/persistence coupling that the task must clear in the same change.

`SessionReviewOutput` was a candidate but is **REFUTED** — its `output_type=` role drives pydantic-ai's structured-generation schema and the reviewer prompt contract; removal would change the LLM call. Left as-is.

---

## ✓ DONE TASK-1: Remove three clean write-only/dead fields

Behavior-preserving deletion of fields with zero readers and no test/serialization coupling.

- `WebRetryResult.status_code` — `co_cli/tools/web/search.py:33` (R1). Set in 4 constructors (search.py:138,144,161,168), never read on any instance. Frozen dataclass, no serialization. Delete the field + the kwarg from all constructor sites.
- `TurnResult.streamed_text` — `co_cli/context/orchestrate.py:109` (R1). Written at orchestrate.py:512,554,820 from `turn_state.latest_streamed_text`; never read on a `TurnResult` instance (consumers read `.messages/.outcome/.model_requests/.interrupted`). Delete the field + the 3 constructor kwargs. Leave `_TurnState.latest_streamed_text` (it is read internally at orchestrate.py:615).
- `SearchResult.confidence` — `co_cli/index/_retrieval.py:60` (R1+R10). Never assigned in either constructor (:82, :416) and never read anywhere — fully dead. Delete the field.

**files:** `co_cli/tools/web/search.py`, `co_cli/context/orchestrate.py`, `co_cli/index/_retrieval.py`

**done_when:** all three fields removed; `rg "\.status_code|\.streamed_text|\.confidence" co_cli/ tests/` shows no reference to the removed members (remaining hits are unrelated symbols); full suite green; no behavior change.

**success_signal:** N/A (internal subtraction; no user-observable surface).

---

## ✓ DONE TASK-2: Remove `CoRuntimeState.background_status_callback` + its survival test

- `CoRuntimeState.background_status_callback` — `co_cli/deps.py:231` (R1). Written once at `bootstrap/core.py:473` (`deps.runtime.background_status_callback = on_status`); no production read/call site. The live status path reaches consumers via `frontend.on_status`, not this field.
- Coupling: `tests/test_flow_fork_deps.py:46-48` asserts the field survives `reset_for_turn()` — a storage-survival test, not a behavior test. Delete those assertions with the field.

**files:** `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `tests/test_flow_fork_deps.py`

**done_when:** field + bootstrap assignment removed; the survival assertions deleted (not the whole test if it covers other reset behavior); `rg background_status_callback co_cli/ tests/` = 0; full suite green.

**success_signal:** N/A.

---

## ✓ DONE TASK-3: Resolve the two write-only `HousekeepingStats` counters — DECISION: Option B (drop)

`done_pruned` (`state.py:49`, incremented `_housekeeping.py:509`) and `session_pruned` (`state.py:50`, incremented `_housekeeping.py:553`) are written but **omitted from the `/memory` daemon summary** (`commands/memory.py:225-226`, which surfaces only the other 4 counters). `done_pruned` is read nowhere; `session_pruned` is read only by persistence-round-trip tests (`test_housekeeping.py:440,457,523,525`).

These count real housekeeping work, and their 4 sibling counters ARE surfaced — so the omission may be the bug, not the counters. **Gate-1 decision:**
- **Option A (surface):** add both to the `/memory` summary line. Makes the counters two-sided; preserves the work signal. *Recommended* — the increments track real pruning the operator would want to see.
- **Option B (drop):** delete both fields + increments + the `session_pruned` test assertions. Pure subtraction.

`HousekeepingStats` is a pydantic `BaseModel` persisted as JSON with per-field `=0` defaults, so either direction is forward-load-safe (stale on-disk keys are ignored).

**files (Option A):** `co_cli/commands/memory.py`; **(Option B):** `co_cli/daemons/dream/state.py`, `co_cli/daemons/dream/_housekeeping.py`, `tests/daemons/dream/test_housekeeping.py`

**done_when:** chosen option implemented; counters are two-sided (A) or fully removed (B); full suite green.

**success_signal (Option A):** `/memory` shows `done_pruned`/`session_pruned` counts after a housekeeping pass.

---

## Deferred backlog

- **Data-schema note (low priority, not code):** souls/canon `.md` data files use bare YAML `created:`/`updated:` keys (e.g. `personality/prompts/souls/finch/canon/*.md:3,11`), diverging from the memory-item schema's `created_at`/`updated_at` (`memory/item.py:64-65`). No Python reads these keys (the personality loader/validator never references them), so it is inert stored-data drift, not a code R9 finding. Rename only if canon timestamps should mirror the memory schema.

---

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `WebRetryResult.status_code`, `TurnResult.streamed_text`, `SearchResult.confidence` removed; rg shows no refs to removed members | ✓ pass |
| TASK-2 | `background_status_callback` field + bootstrap assignment + survival test removed; rg = 0 | ✓ pass |
| TASK-3 (Option B) | `done_pruned`/`session_pruned` fields + increments + test assertions removed | ✓ pass |

**TASK-3 decision:** Option B (drop), judged on functional need rather than counter-symmetry. `done_pruned` is self-described "rare, diagnostic" disk janitorial with no reader; `session_pruned` is opt-in/default-off and its operator signal already exists via `logger.info` per pass. Neither belongs on the knowledge-health `/memory` dashboard. Removing the now-orphaned `state` param from `prune_done_and_snapshots`/`prune_sessions` was folded in (dead code the change created).

**Tests:** scoped — 41 passed (22 housekeeping + fork-deps; 19 orchestrate/retrieval/usage), 0 failed.
**Doc Sync:** fixed — core-loop.md (`streamed_text` rows removed, "Frozen dataclass" corrected) + dream.md (counters removed from state node/table, `prune_*` signatures + 4× stale `_state.py`→`state.py` + test descriptions).

**Overall: DELIVERED**
All three subtraction tasks passed done_when; lint clean, scoped tests green, specs synced.

---

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | 3 fields removed; rg shows no refs | ✓ pass | `search.py:30` (`status_code` gone + 4 kwargs at 134/137/155/162); `orchestrate.py:106` (`streamed_text` gone + 3 kwargs); `_retrieval.py:57` (`confidence` gone). `rg` residual hits are httpx `e.status_code`/`resp.status_code` + internal `_TurnState.latest_streamed_text` (read at orchestrate.py:612/423, kept by design) — all unrelated. |
| TASK-2 | field + bootstrap assignment + survival test removed; rg=0 | ✓ pass | `deps.py:227` field gone (comment block too); `bootstrap/core.py:473` assignment gone; `test_flow_fork_deps.py` survival test + `CoRuntimeState` import dropped; `rg background_status_callback` = 0. `Callable` import in deps.py still used (4 sites) — no orphan. |
| TASK-3 (Option B) | counters + increments + assertions removed; suite green | ✓ pass | `state.py:46` both counters gone; `_housekeeping.py` increments gone + orphaned `state` param dropped from `prune_done_and_snapshots`/`prune_sessions` and call sites; `test_housekeeping.py` assertions + reload check removed; `rg done_pruned\|session_pruned` = 0. `HousekeepingState` still imported/used elsewhere — no orphan. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Extra files in working tree not in any task `files:` (check.py, config/core.py, config/skills.py, main.py, observability/tail.py, observability/trace_view.py, personality.md, the `_housekeeping.py` @cache/generic-clustering refactor, both SKILL.md) | working tree | minor | Not introduced by this delivery — all present at session-start git status (prior same-session conformance round + unrelated edits). `/ship` must stage only TASK-1/2/3 files + synced specs. Flagged, not fixed. |

### Tests
- Command: `uv run pytest` (scoped to changed modules/test files — pure behavior-preserving deletions, no full-suite-only coupling)
- Result: 31 passed, 0 failed (22 housekeeping + 2 fork-deps + 9 turn-result/usage incl. 2 real-LLM turns)
- Log: `.pytest-logs/*-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads clean after deps.py field removal), exit 0
- All three tasks: `success_signal` = N/A (internal subtraction, no user-observable surface) — nothing to smoke beyond the import graph.

### Overall: PASS
Three pure behavior-preserving deletions; done_when greps clean, no orphaned imports, lint + scoped tests green, boot smoke OK. At ship, stage only the TASK-1/2/3 files plus synced core-loop.md/dream.md — the other working-tree edits belong to separate work.
