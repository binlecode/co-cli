# Plan: Compaction Runtime Flag Cleanup

Task type: code-refactor

**Sequencing:** Trails `2026-04-25-115715-compaction-hardening-followup.md`. Pick this up
after the followup ships — both touch `co_cli/context/compaction.py` and bundling them
dilutes review focus (followup is about gate/policy/quality of inline compaction; this is
about transient state-flag hygiene).

## Context

`CoRuntimeState` carries two booleans that look redundant:

- `history_compaction_applied: bool = False` (`co_cli/deps.py:141`)
- `compacted_in_current_turn: bool = False` (`co_cli/deps.py:144`)

Set together at every compaction site (`co_cli/context/compaction.py:237–238, 322–323`),
reset together by `reset_for_turn()` (`co_cli/deps.py:166–167`), and the docstring on
`CoRuntimeState` lists them adjacently in the per-turn group (`deps.py:128`).

Two distinct readers consume the value:

- `co_cli/main.py:132` reads `history_compaction_applied` to set `history_compacted=` on
  the persisted session record (drives child-session branching after a compaction).
- `co_cli/context/compaction.py:397` reads `compacted_in_current_turn` to suppress the
  provider-reported token count when computing `_effective_token_count` (the API count
  lags by one turn; trusting it after fresh compaction would re-trigger compaction
  spuriously).

Both reads happen within the turn lifecycle, both flags hold the identical value at
identical times, and `reset_for_turn()` clears both atomically. The two fields encode
the same fact ("did compaction run this turn?") for two different downstream uses.

## Problem & Outcome

**Problem:** Code smell, not a bug. Two state fields with identical lifecycle and value
invite drift — a future change that touches one site can forget the other, and the next
reader has to re-derive that the two are equivalent. The names also do not telegraph the
two readers' intents (persistence vs token-count suppression), so the next maintainer
has to grep call sites to understand why both exist.

**Failure cost today:** Zero. Both flags are written and reset together at every site;
no codepath sets one without the other.

**Outcome:** One field, both readers point at it. Name reflects the fact ("compaction ran
this turn"), not either reader's downstream use — naming after a single consumer would
mislead the other.

## Scope

**In:**

- `co_cli/deps.py` — collapse the two fields to one (default name candidate:
  `compaction_applied_this_turn`); update the per-turn group docstring at line 128;
  update `reset_for_turn()` to clear the single field.
- `co_cli/main.py` — update the `_finalize_turn` read at line 132 to the new field name.
- `co_cli/context/compaction.py` — update the four write sites (237, 238, 322, 323) and
  the `_effective_token_count` read at line 397 to the single field.
- `tests/` — grep for `history_compaction_applied` and `compacted_in_current_turn` and
  rename references; `tests/context/test_history.py:761–762` asserts both flags simultaneously
  against the same compaction call — collapse to a single assertion after renaming (semantic
  edit, not pure rename).

**Out:**

- No semantic change to compaction triggering, persistence branching, or token-count
  suppression. Behavior under all three call paths (proactive M3, hygiene, manual
  `/compact`) is identical pre and post.
- No changes to `compaction_skip_count`, `consecutive_low_yield_proactive_compactions`,
  or any other `CoRuntimeState` field.
- No spec updates inline — `/sync-doc` auto-invoked by `orchestrate-dev` after delivery
  handles all three specs that reference the old names: `docs/specs/compaction.md` (lines
  374, 437, 438), `docs/specs/core-loop.md` (lines 122, 271, 308), and
  `docs/specs/system.md` (line 117).
- No change to `reset_for_turn`'s behavior beyond clearing one field instead of two.

## Behavioral Constraints

1. **Atomic reset preserved.** The single field must still clear in `reset_for_turn()`
   so persistence and token-count-suppression both see a fresh `False` at turn start.
2. **All write sites updated together.** Compaction sets the flag to `True` at each
   compaction success path. Missing one site would silently break either persistence
   (sessions stop being marked as compacted) or token-count suppression (next turn
   double-compacts).
3. **No new readers introduced.** This refactor is rename-only; do not fold extra logic
   into the new field on the way through.

## Task Breakdown

| # | Task | Effort | Risk |
|---|------|--------|------|
| TASK-1 ✓ DONE | Collapse the two flags into one in `CoRuntimeState`; rename writers and readers; update tests | XS | Low |

Single-task plan. Suitable for `/deliver` (solo path) — no orchestrate-dev or review-impl
gates needed.

## Files Affected

| File | Change |
|------|--------|
| `co_cli/deps.py` | Drop one field, rename one, update docstring + `reset_for_turn` |
| `co_cli/main.py` | One reference update at line 132 |
| `co_cli/context/compaction.py` | Five reference updates (lines 237, 238, 322, 323, 397) |
| `tests/` | Grep-rename test references |

## Out of Scope (deferred to separate plans)

- Any larger refactor of `CoRuntimeState` field organization (per-turn vs cross-turn
  grouping, dataclass split, etc.).
- Renaming `compaction_skip_count` further or reorganizing the circuit-breaker state.
- Refactoring `_effective_token_count` itself — only its read of the flag changes.
