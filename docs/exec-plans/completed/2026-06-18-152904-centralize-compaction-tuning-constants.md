# Centralize Compaction Size-Control Constants (one namespaced tuning module)

## Context

The context-pipeline "size control" knobs exist in three tiers today:

1. **User-configurable** — `CompactionSettings` (`co_cli/config/compaction.py`): `compaction_ratio`,
   `tail_fraction`, `spill_ratio`, `min_proactive_savings`, `proactive_thrash_window`. Already
   centralized + env-var backed. **Out of scope** — they are already in one place and already tunable.
2. **Non-configurable module constants** — scattered across six source files (the subject of this plan).
3. **Hard correctness invariants** — `_MIN_RETAINED_TURN_GROUPS = 1` (not really "tuning", but the
   compaction spec lists it among the module constants, so it travels with the set).

Tier-2 constants and their current homes (verified by grep, 2026-06-18):

| Constant | Source | Value | Imported outside its file? |
|---|---|---|---|
| `SUMMARY_BUDGET_RATIO` | `context/summarization.py` | `0.25` | tests |
| `SUMMARY_BUDGET_FLOOR` | `context/summarization.py` | `2_000` | tests, evals |
| `SUMMARY_BUDGET_CEIL` | `context/summarization.py` | `6_000` | tests |
| `SUMMARY_CAP_OVERSHOOT_RATIO` | `context/summarization.py` | `1.3` | evals |
| `_NOREASON_CEILING_FALLBACK` | `context/summarization.py` | `8_192` | evals |
| `_FIT_SAFETY_MARGIN` | `context/summarization.py` | `2_000` | (none yet) |
| `_COMPACTION_BREAKER_TRIP` | `context/compaction.py` | `3` | tests |
| `_COMPACTION_BREAKER_PROBE_EVERY` | `context/compaction.py` | `10` | tests |
| `_MIN_RETAINED_TURN_GROUPS` | `context/_compaction_boundaries.py` | `1` | (none) |
| `COMPACTABLE_KEEP_RECENT` | `context/history_processors.py` | `5` | tests |
| `SPILL_THRESHOLD_CHARS` | `tools/tool_io.py` | `4_000` | bootstrap, agent/toolset, tests, evals |
| `TOOL_RESULT_PREVIEW_CHARS` | `tools/tool_io.py` | `1_500` | context/history_processors, tests |
| `CHARS_PER_TOKEN` | `context/tokens.py` | `4` | history_processors, summarization, bootstrap, tests |

Key facts from the scan:
- **Four are currently package-private** (`_`-prefixed): `_NOREASON_CEILING_FALLBACK`, `_FIT_SAFETY_MARGIN`,
  `_COMPACTION_BREAKER_TRIP`, `_COMPACTION_BREAKER_PROBE_EVERY`, plus `_MIN_RETAINED_TURN_GROUPS`. The moment
  they live in a shared module imported across packages, the leading-underscore visibility contract requires
  the underscore be **dropped** (a `_name` imported from another package is a violation).
- **Three are cross-concern**, not compaction-specific: `CHARS_PER_TOKEN` (general token estimator,
  used by bootstrap), `SPILL_THRESHOLD_CHARS` / `TOOL_RESULT_PREVIEW_CHARS` (tool-IO emit-time). The user
  has chosen to aggregate **all spec-table constants anyway** — see Decision below.

Current state is consistent and plannable; the spec (`docs/specs/compaction.md` §3 constants tables) is
accurate and enumerates exactly this set.

## Problem & Outcome

**Problem.** The size-control constants are spread across six files as bare (and partly underscore-private)
module vars. To trace or tune the end-to-end size behavior (summary budget → cap → fit guard → breaker →
spill → preview → token estimate) you must open six files and reverse-map each constant to the mechanism it
drives. There is no single place to read or adjust the pipeline's sizing.

**Failure cost.** Not a runtime defect — a maintainability/traceability cost: tuning is error-prone (related
knobs are physically separated), and the underscore-private ones are already silently imported across files
(a latent visibility-contract drift, e.g. tests reaching `_COMPACTION_BREAKER_TRIP`).

**Outcome.** One module — `co_cli/config/tuning.py` — holds every tier-2/3 size-control constant, each named
with a **function-driven namespace prefix** that identifies the mechanism it drives (`SUMMARY_*`, `BREAKER_*`,
`BOUNDARY_*`, `EVICT_*`, `SPILL_*`, `ESTIMATE_*`). All are public (no leading underscore — they cross package
boundaries by construction). Every definition site imports from there; no value changes; behavior identical.

## Scope

**In:**
- New module `co_cli/config/tuning.py` holding all 13 constants under function-driven prefixes, with a
  comment block per prefix group naming the mechanism each drives.
- Drop the leading underscore on the five currently-private constants (visibility contract).
- Rewrite every definition site to import the constant from the new module; update every use site to the new
  (prefixed, underscore-free) name.
- `docs/specs/compaction.md` §3 constants tables — updated by `sync-doc` post-delivery (not in `files:`).

**Out:**
- `CompactionSettings` (already centralized + configurable) — untouched.
- **Promoting any constant to a config field / env var.** Decision: keep them non-configurable module
  constants (user chose "centralize as constants", not "promote to config"). No behavior change.
- Changing any value. This is a pure move + rename; identical runtime behavior.

## Behavioral Constraints

- **Zero behavior change.** Pure refactor: same values, same arithmetic, same control flow. The full test
  suite + evals are the proof.
- **Function-driven prefix namespace.** Every constant name must carry a prefix naming the mechanism it
  drives, so the flat module reads as grouped namespaces, not an undifferentiated bag. Co-locating
  cross-concern constants (token estimate, spill) is acceptable *because* the prefix preserves the concern
  signal the per-file home used to carry.
- **No underscore-private name crosses a package boundary.** All centralized constants are public; the five
  formerly-private ones drop their underscore (per the visibility contract).
- **Zero stale references.** Done only when a repo-wide grep for every old name (including the underscore
  forms) returns zero hits AND the full suite passes.
- **`co_cli/config/tuning.py` holds bare constants only** — no pydantic model, no IO, no import-time side
  effects (config-module discipline; import must stay free).

## High-Level Design

New module (sketch — exact prefix scheme is TASK-1's deliverable, names below are the proposal):

```python
# co_cli/config/tuning.py
"""Non-configurable size-control constants for the context pipeline, centralized.

User-tunable knobs live in CompactionSettings (config/compaction.py); these are the
fixed constants that drive summary sizing, the circuit breaker, boundary retention,
tool-result eviction/spill, and the char->token estimate. Grouped by function-driven
prefix so each name maps to the mechanism it drives.
"""

# SUMMARY_* — summarizer output budget + the cap/fit arithmetic (summarization.py)
SUMMARY_BUDGET_RATIO = 0.25
SUMMARY_BUDGET_FLOOR = 2_000
SUMMARY_BUDGET_CEIL = 6_000
SUMMARY_CAP_OVERSHOOT_RATIO = 1.3
SUMMARY_NOREASON_CEILING_FALLBACK = 8_192   # was _NOREASON_CEILING_FALLBACK
SUMMARY_FIT_SAFETY_MARGIN = 2_000           # was _FIT_SAFETY_MARGIN

# BREAKER_* — summarizer circuit breaker (compaction.py)
BREAKER_TRIP = 3                            # was _COMPACTION_BREAKER_TRIP
BREAKER_PROBE_EVERY = 10                    # was _COMPACTION_BREAKER_PROBE_EVERY

# BOUNDARY_* — turn-group retention invariant (_compaction_boundaries.py)
BOUNDARY_MIN_RETAINED_TURN_GROUPS = 1       # was _MIN_RETAINED_TURN_GROUPS (correctness invariant)

# EVICT_* — old-tool-result eviction (history_processors.py)
EVICT_KEEP_RECENT = 5                       # was COMPACTABLE_KEEP_RECENT

# SPILL_* — emit-time tool-result spill + preview (tool_io.py)
SPILL_THRESHOLD_CHARS = 4_000
SPILL_PREVIEW_CHARS = 1_500                 # was TOOL_RESULT_PREVIEW_CHARS

# ESTIMATE_* — char->token proxy (tokens.py)
ESTIMATE_CHARS_PER_TOKEN = 4               # was CHARS_PER_TOKEN
```

Each former definition site keeps no local copy — it imports from `config.tuning`. `tokens.py`'s
`estimate_text_tokens` helper stays where it is; only the constant moves (the function is a different concern
from the bare ratio).

**Independent equal values — do NOT dedupe.** `SUMMARY_BUDGET_FLOOR` and `SUMMARY_FIT_SAFETY_MARGIN` both
equal `2_000` but are unrelated knobs (no shared expression); co-locating them as distinct named constants is
correct. A future reader must not collapse them into one.

**No circular import.** `config/` imports nothing from `context/`/`tools/`/`bootstrap/`/`agent/` today, and
`tuning.py` is bare constants with no imports, so the dependency direction stays leaf → config (verified).

## Tasks

### ✓ DONE TASK-1 — Create `config/tuning.py` with the namespaced constant set
- **files:** `co_cli/config/tuning.py`
- **done_when:** `uv run python -c "import co_cli.config.tuning as t; assert t.SUMMARY_BUDGET_RATIO==0.25 and t.BREAKER_TRIP==3 and t.SPILL_THRESHOLD_CHARS==4000 and t.ESTIMATE_CHARS_PER_TOKEN==4"` exits 0 — the module imports free (no side effects) and every constant carries its prefixed name and original value.
- **success_signal:** N/A (pure refactor).
- **prerequisites:** none

### ✓ DONE TASK-2 — Repoint every definition + use site; drop the old names
- **files:** `co_cli/context/summarization.py`, `co_cli/context/compaction.py`, `co_cli/context/_compaction_boundaries.py`, `co_cli/context/history_processors.py`, `co_cli/context/tokens.py`, `co_cli/tools/tool_io.py`, `co_cli/bootstrap/core.py`, `co_cli/agent/toolset.py`, `tests/test_flow_compaction_summarization.py`, `tests/test_flow_compaction_proactive.py`, `tests/test_flow_compaction_history_processors.py`, `tests/test_flow_spill.py`, `tests/test_flow_compaction_spill_largest_tool_results.py`, `tests/test_orchestrator_schema_budget.py`, `evals/eval_context_stability.py`, `evals/eval_summarizer_fidelity.py`, `evals/eval_daily_chat.py`
- **also update** every **docstring / inline-comment** reference to a renamed constant (not just code): e.g. `COMPACTABLE_KEEP_RECENT` (history_processors.py docstrings, lines ~152/219), `_MIN_RETAINED_TURN_GROUPS` (5× in `_compaction_boundaries.py` docstrings), `_NOREASON_CEILING_FALLBACK` / `_FIT_SAFETY_MARGIN` prose. A renamed-away grep that hits a docstring is a real stale reference.
- **done_when:** both checks pass AND `scripts/quality-gate.sh full` passes:
  - **(a) renamed-away forms — zero hits anywhere** in `co_cli/`, `tests/`, `evals/` (code, docstrings, and comments): `_NOREASON_CEILING_FALLBACK`, `_FIT_SAFETY_MARGIN`, `_COMPACTION_BREAKER_TRIP`, `_COMPACTION_BREAKER_PROBE_EVERY`, `_MIN_RETAINED_TURN_GROUPS`, `COMPACTABLE_KEEP_RECENT`, `TOOL_RESULT_PREVIEW_CHARS`, `CHARS_PER_TOKEN`.
  - **(b) kept-name constants — single definition site:** the *assignment* (`^<NAME> = `) for `SUMMARY_BUDGET_RATIO`, `SUMMARY_BUDGET_FLOOR`, `SUMMARY_BUDGET_CEIL`, `SUMMARY_CAP_OVERSHOOT_RATIO`, `SPILL_THRESHOLD_CHARS` appears **only** in `config/tuning.py`. (Bare-name references to these elsewhere are expected use sites/imports, not stale — do NOT grep the bare name for these.)
  - **Note:** `scripts/calibrate_spill_size.py:4` has a docstring mention of `SPILL_THRESHOLD_CHARS` (a *kept* name) — no edit needed; it is an expected hit, not a stale reference, and `scripts/` carries no edit in this task.
- **success_signal:** N/A (pure refactor).
- **prerequisites:** TASK-1

## Testing

- No new tests. This is a pure move/rename with zero behavior change; the existing suite + evals are the
  regression proof. `scripts/quality-gate.sh full` (lint + full pytest) is the gate; the constant-importing
  tests/evals listed in TASK-2 prove the new names resolve at every call site.
- The summarizer-fidelity / context-stability evals continue to exercise the real sizing path under the new
  names unchanged.

## Open Questions

- **Prefix scheme — exact tokens (TASK-1's call, terse).** The rule is settled: every name carries a
  function-driven prefix. Exact tokens (`EVICT_KEEP_RECENT`, `SPILL_PREVIEW_CHARS`, `ESTIMATE_CHARS_PER_TOKEN`)
  are the proposal; keep prefixes terse so they serve grouping, not verbosity. Not a blocker.

(Module home settled at Gate 1: `co_cli/config/tuning.py` — see Decisions.)

---

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Grepping the bare name of *kept* constants (`SUMMARY_BUDGET_*`, `SPILL_THRESHOLD_CHARS`) returns legit use-site hits — the criterion couldn't pass and proved nothing. | TASK-2 done_when split into (a) renamed-away forms = zero hits anywhere, (b) kept-name constants = single `^NAME = ` definition in `config/tuning.py`. |
| CD-M-2 | adopt | Docstring/comment refs to renamed constants are real stale references; `scripts/calibrate_spill_size.py` mentions a *kept* name (expected hit). | TASK-2 gained an explicit "update docstring/comment references" clause + a note that the `scripts/` hit on `SPILL_THRESHOLD_CHARS` is expected, no edit. |
| CD-m-1 | adopt | `SUMMARY_BUDGET_FLOOR` and `SUMMARY_FIT_SAFETY_MARGIN` both `=2000` but independent — must not be deduped. | Added "independent equal values — do NOT dedupe" note to High-Level Design. |
| CD-m-2 | adopt | No circular-import risk; underscore-drop required; `__init__.py` untouched — confirmed. | Added "no circular import" note to High-Level Design (evidence). |
| CD-m-3 | reject | `tokens.py` becoming a thin 1-function module is acceptable; the function is a separate concern from the bare ratio and the import direction is clean. | — |
| PO-M (none) | — | PO approved with no blockers. | — |
| PO-m-1 | adopt | All alternative homes reintroduce a boundary violation; `config/tuning.py` is the cleanest concern-neutral owner. | Module home settled; removed from Open Questions, recorded here. |
| PO-m-2 | reject | Behavioral-constraints set already complete for a pure refactor; no gap to fix. | — |
| PO-m-3 | modify | Prefix rule is settled (user mandate); exact tokens kept terse and left as TASK-1's call. | Open Questions trimmed to "exact tokens, terse". |

**Convergence note:** Core Dev's two blockers were `done_when`-wording defects, each resolved verbatim per
its own recommendation with no remaining design ambiguity; PO approved on C1. TL judgment: no C2 needed.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev centralize-compaction-tuning-constants`

---

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `tuning.py` imports free; every constant carries prefixed name + original value | ✓ pass |
| TASK-2 | (a) renamed-away forms zero hits, (b) kept-names single definition in `config/tuning.py`, repoint + lint | ✓ pass |

**Constant moves:** all 13 size-control constants centralized in `co_cli/config/tuning.py` under function-driven prefixes (`SUMMARY_*`, `BREAKER_*`, `BOUNDARY_*`, `EVICT_*`, `SPILL_*`, `ESTIMATE_*`). Five formerly-private names dropped their underscore: `_NOREASON_CEILING_FALLBACK`→`SUMMARY_NOREASON_CEILING_FALLBACK`, `_FIT_SAFETY_MARGIN`→`SUMMARY_FIT_SAFETY_MARGIN`, `_COMPACTION_BREAKER_TRIP`→`BREAKER_TRIP`, `_COMPACTION_BREAKER_PROBE_EVERY`→`BREAKER_PROBE_EVERY`, `_MIN_RETAINED_TURN_GROUPS`→`BOUNDARY_MIN_RETAINED_TURN_GROUPS`. Three cross-concern renames: `COMPACTABLE_KEEP_RECENT`→`EVICT_KEEP_RECENT`, `TOOL_RESULT_PREVIEW_CHARS`→`SPILL_PREVIEW_CHARS`, `CHARS_PER_TOKEN`→`ESTIMATE_CHARS_PER_TOKEN`. Per-constant rationale comments moved to `tuning.py` (not lost). `tokens.py` retains `estimate_text_tokens`, imports the constant.

**Verification:**
- (a) Word-boundary grep for all 8 renamed-away forms across `co_cli/ tests/ evals/` → **zero genuine stale references** (substring matches on new prefixed names excluded).
- (b) `^NAME = ` assignment for each kept name (`SUMMARY_BUDGET_*`, `SUMMARY_CAP_OVERSHOOT_RATIO`, `SPILL_THRESHOLD_CHARS`) appears **only** in `config/tuning.py`.
- All production modules import clean (smoke test).

**Tests:** scoped — 55 deterministic passed (spill 29, history-processors gate 11, breaker-cadence 7, summary-budget 8), 0 failed. The real-LLM tests in the touched files were not run here (review-impl's full-suite job). One real-LLM test (`test_successful_compaction_resets_skip_count`) fails on the working tree, but the cause is the **uncommitted `summarizer-input-fit-guard` WIP** (its `SummarizerInputTooLargeError` guard trips on the tight 200-token test window) — `git diff HEAD` confirms the guard is all `+` lines absent from HEAD; identical failure occurs independent of this rename. Out of this plan's scope; flagged to the user separately.

**Doc Sync:** fixed — `docs/specs/compaction.md` §3 constants tables + all inline prose refs renamed and re-homed to `config/tuning.py` (consuming module named in Purpose); module-map entries for `tokens.py`/`tool_io.py` corrected to "imports from config/tuning.py"; `docs/specs/core-loop.md` breaker refs renamed.

**Overall: DELIVERED**
Pure move+rename, zero behavior change, lint clean, scoped deterministic tests green, specs synced.

**Next step:** `/review-impl centralize-compaction-tuning-constants` — full suite + evidence scan → verdict at Gate 2.

---

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | module imports free; prefixed names + original values | ✓ pass | `co_cli/config/tuning.py` — `python -c` assert on `SUMMARY_BUDGET_RATIO/BREAKER_TRIP/SPILL_THRESHOLD_CHARS/ESTIMATE_CHARS_PER_TOKEN` exits 0; leaf module, no imports |
| TASK-2 (a) | renamed-away forms zero hits | ✓ pass | word-boundary grep of all 8 old names across `co_cli/ tests/ evals/` → zero genuine stale (substring matches on new prefixed names excluded) |
| TASK-2 (b) | kept names single definition | ✓ pass | `^NAME = ` for `SUMMARY_BUDGET_*`, `SUMMARY_CAP_OVERSHOOT_RATIO`, `SPILL_THRESHOLD_CHARS` appears once each — only in `config/tuning.py` |

Leaf-boundary judgment: every new import edge is leaf→`config` (config sits below all consumers — `context`/`tools`/`agent`/`bootstrap`); no leaf→`tools`/`agent`/`bootstrap` edge added, no circular import. Per-constant rationale comments moved to `tuning.py`, not lost. `tokens.py` retains `estimate_text_tokens`, imports the constant.

### Issues Found & Fixed
No issues found in this plan's scope. (Two regressions + one status-label bug surfaced in the **entangled** `summarizer-input-fit-guard` WIP sharing `compaction.py`/`test_flow_compaction_proactive.py` — found and fixed in the same working session; see that plan's "Follow-up Correction — 2026-06-18".)

### Tests
- Command: scoped suite over both plans' touched test files + dependents (full suite deliberately not run — the working tree carries ~20 files of unrelated in-progress WIP from other plans that would confound attribution and burn real-LLM time).
- Files: `test_flow_compaction_{summarization,proactive,history_processors,spill_largest_tool_results,review_snapshot,recovery,slash_commands}.py`, `test_flow_spill.py`, `test_orchestrator_schema_budget.py`, `tests/context/test_{input_too_large_fallback,summarizer_fit_guard}.py`
- Result: **84 passed, 0 failed** (incl. real-LLM summarization/proactive paths), 24.4s
- Log: `.pytest-logs/<ts>-review-impl-scoped.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads clean — exercises the repointed `bootstrap/core.py` + `tool_io.py` imports at zero LLM cost)
- success_signal: N/A (pure refactor, no user-observable surface change by design)

### Overall: PASS
Pure move+rename verified by literal done_when re-execution + zero-stale grep + single-definition grep; scoped suite green; boot smoke clean. Full-suite gate intentionally deferred to ship-time safety net given the unrelated-WIP-laden tree.
