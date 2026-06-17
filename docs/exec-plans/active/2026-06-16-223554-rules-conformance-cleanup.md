# rules-conformance-cleanup — R4 layer back-edges

## Context

Surfaced by a dry-run of `/audit-conformance` over `co_cli/` (2026-06-16). The R5 underscore-visibility leaks from the same run are already fixed (8 renames, shipped separately). This plan covers the three remaining **R4 back-edges** — a lower-layer module importing a higher one — that the audit confirmed are real (not TYPE_CHECKING/lazy artifacts):

| Edge | Site | Imported |
|------|------|----------|
| `llm → session` | `co_cli/llm/call.py:13` | `session.usage.record_usage` |
| `llm → context` | `co_cli/llm/surrogate_recovery_model.py:33` | `context.history_processors.sanitize_surrogate_codepoints_messages` |
| `context → daemons` | `co_cli/context/compaction.py:61` | `daemons.dream.kick.write_review_kick` |

A fourth candidate, `index → observability`, was inspected and **dismissed**: `observability/tracing` is foundational cross-cutting (imports only `config`), so any layer importing it is correct, not an inversion.

Layer order (from `docs/specs/01-system.md`): `config`/`observability` (foundational) < `index`/`llm` < `memory`/`session` < `context` < `tools` < `agent`/loop < `commands`/`main`. `daemons/dream` is **high** (the dream processor imports `agent`/`memory`); `main` is the composition root and may import anything.

## Problem & Outcome

Each edge tangles a low layer to a high one: you cannot exercise/reuse the low module without dragging the high one in, and it risks import cycles. All three are **filing/wiring** errors — the imported symbol carries no genuine high-layer dependency — so each fix is a relocation with **no behavior change**.

**Outcome:** the three back-edges no longer appear in the module-level import map; the moved symbols live in their owning domain; the full suite stays green with no behavioral change.

**Failure cost:** left alone, these are the seed crystals of exactly the accretion the audit exists to stop — each one makes the next "just import it from there" edge look normal, and one (`context → daemons`) is one import away from a compaction↔daemon cycle.

## Scope

In scope: relocating three symbols and updating their importers (prod + tests). Out of scope: any behavioral change, the already-fixed R5 leaks, the dismissed `index → observability` edge, and any other rule class (R1–R3, R6–R12) — those are separate audit findings.

## Behavioral Constraints

- **No behavior change.** Every task is a pure move + import rewrite. The same function runs with the same inputs/outputs; only its import path changes.
- **Renames are total** (`feedback_zero_backward_compat`): no re-export shim left at the old location, no compat alias. Old import path must return zero grep hits in `co_cli/` and `tests/`.
- **Do not edit `completed/` exec-plans or `RESEARCH-*` docs** that mention old paths — they are historical records.

## High-Level Design

### TASK-1 — `llm → session`: split the usage module by concern

`session/usage.py` mixes two concerns that share nothing:
- **Realtime accumulator** (`UsageAccumulator`, `record_usage`) — observational, held on `CoDeps`, bumped at every model-call boundary by `llm`, `agent`, and `context`. The module docstring itself says usage capture is *"observational, never a control input."*
- **Durable ledger** (`append_turn`, `aggregate`, `UsageTotals`, `UsageWindow`, `ORIGIN_*`) — genuine session-domain accounting, written at the turn boundary and read by reporting.

The ledger half uses nothing from the accumulator half and vice versa. **Move the accumulator half down to `co_cli/observability/usage.py`** (foundational; `llm` already depends on `observability`). The durable ledger stays in `session/usage.py`.

- Importers to update: `deps.py:21` (`UsageAccumulator`), `llm/call.py:13`, `context/orchestrate.py:77`, `agent/run.py:42` (`record_usage`), `tests/test_flow_usage_tracking.py:36`.
- Result: `llm → session` edge gone; `llm → observability` (already legal) replaces it.

### TASK-2 — `llm → context`: move the sanitizer to its sole owner

`sanitize_surrogate_codepoints_messages` is **defined** in `context/history_processors.py:693` but **no code in `context/` calls it** — the only runtime caller is `llm/surrogate_recovery_model.py` (lines 222, 280). It is a pure pydantic-ai message sanitizer (uses `re`, walks dict/list). It is misfiled in `context/`.

**Move it (and any private helper it solely depends on) into `llm/`** — its owning domain is surrogate recovery. Recommended home: a new `co_cli/llm/_message_sanitize.py` (or inline into `surrogate_recovery_model.py` if it has no other intended caller).

- Verify before moving: confirm `sanitize_surrogate_codepoints_messages` does not call any other `history_processors` private; if it does, move that helper too or it creates a new back-edge.
- Importers to update: `llm/surrogate_recovery_model.py:33`, `tests/test_flow_compaction_history_processors.py:20` (move its tests to a new `tests/test_flow_llm_message_sanitize.py` if the function leaves `context/`).
- Result: `llm → context` edge gone.

### TASK-3 — `context → daemons`: relocate the KICK producer out of the consumer's package

`daemons/dream/kick.py` is a **low producer** — its docstring states it imports *"only config constants and the atomic writer — nothing from context/ — so it cannot form an import cycle."* It writes a JSON request to the dream-daemon filesystem queue. But it is filed inside `daemons/dream/` (the **consumer/processor** package, which is high), so every producer (`main.py`, `context/compaction.py`) must import *up* into it. This is a module-home violation, consistent with `feedback_queue_sole_bridge` (the producer-side writer should not live in the consumer's package).

**Relocate `write_review_kick` to a neutral home at or below `context`** so both `main` and `context` import *down*. The function already depends only on `config` + `fileio`, so it can live anywhere foundational.

- Importers to update: `main.py:44`, `context/compaction.py:61`, plus tests `tests/integration/test_review_kick_end_to_end.py`, `test_multi_repl_kick.py` (they import `kick_mod` / patch `DREAM_QUEUE_DIR`).
- See Open Question on the exact target home — this is the one genuine judgment call.

## Tasks

- [ ] **TASK-1** Split `session/usage.py`; create `observability/usage.py` with `UsageAccumulator` + `record_usage`; update 5 importers. `done_when`: `llm → session` absent from the import map; `record_usage`/`UsageAccumulator` import from `observability.usage`; suite green; no behavior change.
- [ ] **TASK-2** Move `sanitize_surrogate_codepoints_messages` (+ any solely-used helper) from `context/history_processors.py` into `llm/`; update caller + tests. `done_when`: `llm → context` absent from the import map; `context/` no longer defines the function; suite green.
- [ ] **TASK-3** Relocate `write_review_kick` out of `daemons/dream/kick.py` to the agreed neutral home; update producers + tests. `done_when`: `context → daemons` absent from the import map; suite green; KICK files still produced identically (integration tests pass).

## Testing

Functional, behavior-preserving moves — existing tests are the oracle. Per task, run the touched files first, then the full suite at ship:
- TASK-1: `tests/test_flow_usage_tracking.py` + any orchestrate/agent-run tests.
- TASK-2: the relocated sanitize tests.
- TASK-3: `tests/integration/test_review_kick_end_to_end.py`, `test_multi_repl_kick.py`.

Re-run the audit edge map after all three (`tmp/import_edges.py`) and confirm the three edges are gone and no **new** back-edge was introduced by a move. No new tests — these are relocations; adding a test asserting "module X does not import Y" is a forbidden structural/fitness test (`.agent_docs/testing.md`, `review.md` Code Regulation Model).

## Open Questions

1. **TASK-3 target home for `write_review_kick`.** It must be ≤ `context` and ≤ `main`, depending only on `config` + `fileio`. Candidates:
   - `co_cli/daemons/review_kick.py` (daemons package *root*, above `dream/`) — keeps it in the daemon family but still leaves a `context → daemons` edge; only acceptable if `daemons` (root) is treated as foundational contract, not processor. Likely still flagged.
   - `co_cli/session/review_kick.py` — session is below context; plausible if the KICK is considered session-scoped (it carries `session_id`).
   - A small new low module dedicated to the daemon-queue *contract* (e.g. `co_cli/daemons/queue_contract.py` kept dependency-free). 
   Recommendation: decide at Gate 1 — this is the one real "where does it belong" judgment in the plan.
2. **TASK-2 home:** standalone `llm/_message_sanitize.py` vs inlining into `surrogate_recovery_model.py`. Default: standalone module if the function is independently testable (it is); inline only if the team prefers fewer files.
