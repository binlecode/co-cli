# Plan: Single-Tier Overflow Recovery

Task type: refactor

## Context

Today's overflow recovery is a two-tier cascade in `co_cli/context/compaction.py` + `co_cli/context/orchestrate.py`:

1. **Tier 1** (`recover_overflow_history`, compaction.py:298): planner-based via `plan_compaction_boundaries` + `apply_compaction`. Returns `None` on head/tail overlap.
2. **Tier 2** (`emergency_recover_overflow_history`, compaction.py:331): structural fallback — keeps `groups[0] + static_marker + breadcrumbs + groups[-1]`. Returns `None` when `len(groups) <= 2`.

`_attempt_overflow_recovery` (orchestrate.py:586) coordinates them; `run_turn` (orchestrate.py:691) wires the one-shot gate.

The strip-primitive infra already exists and is used by COMP_EVICT: `semantic_marker()`, `_rewrite_tool_returns()`, `_build_cleared_part()`, `_build_call_id_to_args()` (all in `_tool_result_markers.py` / `history_processors.py`).

### Current-state validation (inline, post-rescan 2026-05-08)

- ✓ `compaction.py:298-328` — planner tier returns `None` on head/tail overlap.
- ✓ `compaction.py:331-366` — emergency tier returns `None` when `len(groups) <= 2`.
- ✓ `orchestrate.py:593-615` — `_attempt_overflow_recovery` exists only to compose the two tiers; consumed at `orchestrate.py:697-718`.
- ✓ `orchestrate.py:583-590` — `_history_with_pending_user_input` returns turn_state.current_history with pending UserPromptPart appended when `current_input` is set.
- ✓ `history_processors.py:78-111` — `_rewrite_tool_returns(messages, boundary, *, replacement_for)` already supports the strip pattern when `boundary=len(messages)`.
- ✓ `compaction.py:281-283` — `apply_compaction` writes the three runtime fields. Does NOT call `_reset_thrash_state` (caller responsibility today).
- ✓ `compaction.py:99-107` — `_reset_thrash_state` docstring confirms recovery-only semantics ("Both reactive recovery paths ... reset these unconditionally"). Proactive `apply_compaction` caller at `compaction.py:405` deliberately does not reset.
- ✓ `tests/test_flow_compaction_recovery.py` — one test covers the deleted emergency function; full rewrite needed.

### Cross-team changes since plan was drafted (do not block — just be aware)

- `enforce_turn_budget` deleted from `history_processors.py` (~120 lines). Recovery scope unaffected; expect a larger diff on this file.
- Span attribute rename in `compaction.py`: `turn_aggregate_*` → `request_aggregate_*` (~lines 517-519). Recovery scope unaffected.
- `tests/test_flow_turn_budget.py` deleted. Recovery scope unaffected.

## Problem & Outcome

**Problem:** Two recovery functions with two different `None`-return invariants that callers must compose. The orchestrator exists solely to thread them. The structural emergency tier drops all middle groups to a single static marker — coarser than what's possible with the same infra COMP_EVICT already uses.

**Outcome:**

- One recovery function with one terminal invariant: `None` ⇒ terminal.
- New `strip_all_tool_returns()` preserves message count and replaces every tool return with a per-tool semantic marker — finer-grained than the structural emergency tier.
- `emergency_recover_overflow_history` and `_attempt_overflow_recovery` are deleted.
- One-shot per-turn gate (`overflow_recovery_attempted`) preserved unchanged.
- Spec synced via `/sync-doc`.

## Scope

### In scope

- `co_cli/context/history_processors.py` — add `strip_all_tool_returns`.
- `co_cli/context/compaction.py` — rewrite `recover_overflow_history` as single tier; delete `emergency_recover_overflow_history`; extract `_mark_compaction_applied` shared helper.
- `co_cli/context/orchestrate.py` — delete `_attempt_overflow_recovery`; inline the call in `run_turn`.
- `tests/test_flow_compaction_recovery.py` — rewrite for the new function.
- `docs/specs/compaction.md` — sync via `/sync-doc`.

### Out of scope

- COMP_WINDOW, COMP_EVICT, `plan_compaction_boundaries`, `summarize_messages`, `is_context_overflow` — all reused unchanged.
- Renaming `recover_overflow_history` — public name unchanged.

## Behavioral Constraints

1. **Pairing invariant.** Strip-only-fits path preserves pairing by construction (only `.content` rewritten). Strip+summarize path depends on `apply_compaction` respecting `UserPromptPart` group boundaries. Both paths covered by tests.
2. **Strip operates unconditionally.** No `COMPACTABLE_TOOLS` filter, no recency cap, no boundary. Recovery is the one place where preserving signal in non-compactable returns is less valuable than recovering the turn. Proactive `evict_old_tool_results` keeps its filter.
3. **Budget gate between strip and summarize.** After strip, if `estimate_message_tokens(stripped) <= budget`, return directly — no LLM. Otherwise run planner + `apply_compaction`.
4. **Terminal condition.** Planner returns `None` ⇒ recovery returns `None` ⇒ caller drives the existing `"Context overflow — unrecoverable."` path.
5. **Idempotent on already-stripped content.** `_build_cleared_part` short-circuits when `is_cleared_marker(part.content)` is True — re-strip preserves the existing marker rather than re-marking it with a degraded size signal. Realistic case: EVICT fires earlier in the same turn, then recovery strip would otherwise re-mark `[file_read] /path (full, 8,432 chars)` as `[file_read] /path (full, 50 chars)`, losing the original size cue.

## High-Level Design

**`strip_all_tool_returns(messages, call_id_to_args)`** — new helper in `history_processors.py`. Wraps `_rewrite_tool_returns(messages, len(messages), replacement_for=…)` with `_build_cleared_part` as the replacement. No filter, no boundary, no cap.

**`_build_cleared_part` idempotency short-circuit** — extend `_build_cleared_part` (`history_processors.py:241`) to early-return the original part unchanged when `is_cleared_marker(part.content)` is True. Two-line addition, applies to both EVICT and recovery strip callers. Without it, recovery re-running over an EVICT-stripped history would re-mark already-marked returns with a degraded size signal.

**`_mark_compaction_applied(ctx, result)`** — new private helper in `compaction.py`. Writes the **three runtime fields only**: `compaction_applied_this_turn`, `post_compaction_token_estimate`, `message_count_at_last_compaction`. Called by both `apply_compaction` (refactored to delegate the field writes) and the strip-only-fits path. Does NOT include `_reset_thrash_state` — that's recovery-specific (per its docstring at `compaction.py:99-107`); folding it into `apply_compaction` would silently change the proactive sliding-window path's behavior. Does not touch `previous_compaction_summary` — that stays summarizer-owned.

**`_reset_thrash_state(ctx)`** stays an explicit recovery-path call. The new `recover_overflow_history` calls it once at the end — covers both the strip-only-fits return path and the strip+summarize return path. Audit during TASK-1: confirm the proactive `apply_compaction` caller (`compaction.py:405`) and the `/compact` REPL command (`commands/compact.py:42`) are unaffected.

**`recover_overflow_history(ctx, messages)`** — rewritten body:

1. Empty → `None`.
2. Strip all tool returns. If fits budget, call `_mark_compaction_applied(ctx, stripped)` + `_reset_thrash_state(ctx)` and return stripped.
3. Else `plan_compaction_boundaries` on stripped. `None` → log + return `None` (terminal).
4. Else `apply_compaction(ctx, stripped, bounds, announce=False)` (which calls `_mark_compaction_applied` internally), then `_reset_thrash_state(ctx)`, return result.

**`run_turn` overflow branch** (`orchestrate.py:697-718`) — inline what `_attempt_overflow_recovery` did: build `recovery_ctx`, call `recover_overflow_history`, on success set `current_history`/`current_input` and emit `"Context overflow — compacting and retrying..."`; on `None` emit `"Context overflow — unrecoverable."` and return error. Add `recover_overflow_history` to the existing module-level import at orchestrate.py:74.

**Deletions:** `emergency_recover_overflow_history` (`compaction.py:331-366`), `"emergency_recover_overflow_history"` from `__all__`, `_attempt_overflow_recovery` (`orchestrate.py:593-615`). Audit `compaction.py` imports — `groups_to_messages`, `static_marker`, `group_by_turn` may become unused; grep before pruning.

## Tasks

### ✓ DONE — TASK-1 — Add helpers, rewrite `recover_overflow_history`, delete emergency tier

- **files:** `co_cli/context/history_processors.py`, `co_cli/context/compaction.py`
- **done_when:**
  - `python -c "from co_cli.context.history_processors import strip_all_tool_returns; from co_cli.context.compaction import recover_overflow_history, _mark_compaction_applied; print('ok')"` exits 0.
  - `grep -rn "emergency_recover_overflow_history" co_cli/ tests/ docs/specs/` returns no hits.
  - `_build_cleared_part` short-circuits when `is_cleared_marker(part.content)` is True (verified by TASK-3 idempotency assertion).
  - `_reset_thrash_state` is called from inside `recover_overflow_history` only — NOT from `apply_compaction`. Verify with `grep -n "_reset_thrash_state" co_cli/context/compaction.py`.
- **success_signal:** `strip_all_tool_returns` strips ALL tool returns regardless of name. `_mark_compaction_applied` writes the three runtime fields and is called by both `apply_compaction` and the strip-only-fits path. `recover_overflow_history` follows the strip-then-summarize design. Re-running strip on already-marked content preserves the existing markers verbatim. Proactive sliding-window path (`compaction.py:405`) and `/compact` REPL command (`commands/compact.py:42`) remain functionally unchanged.

### ✓ DONE — TASK-2 — Inline overflow recovery in `run_turn`, delete `_attempt_overflow_recovery`

- **files:** `co_cli/context/orchestrate.py`
- **prerequisites:** [TASK-1]
- **done_when:**
  - `python -c "from co_cli.context.orchestrate import run_turn; print('ok')"` exits 0.
  - `grep -rn "_attempt_overflow_recovery" co_cli/ tests/ docs/specs/` returns no hits.
- **success_signal:** Recovery-succeeds emits `"Context overflow — compacting and retrying..."`; terminal path emits `"Context overflow — unrecoverable."`. `recover_overflow_history` imported at module level (orchestrate.py:74).

### ✓ DONE — TASK-3 — Rewrite `tests/test_flow_compaction_recovery.py`

- **files:** `tests/test_flow_compaction_recovery.py`
- **prerequisites:** [TASK-1]
- **done_when:** `uv run pytest tests/test_flow_compaction_recovery.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-overflow-single-tier.log` passes.
- **success_signal:** Behavioral coverage of all three paths plus pairing invariant on both paths.

Use real `CoDeps` / `ShellBackend` / `CoSessionState` (no mocks). `RunContext(model=None)` for all tests — routes the summary path through `_summarization_gate_open`'s "model absent" early-return, producing a deterministic `STATIC_MARKER_PREFIX`-prefixed marker without any LLM call. Set `deps.model_max_ctx` directly to control budget.

Cases:

1. **`test_recover_strip_only_fits`** — pre-strip estimate exceeds budget, post-strip fits. Assert: same message count, oversized returns replaced by per-tool markers (assert content starts with `[<tool_name>] ` directly — `is_cleared_marker` only recognizes `COMPACTABLE_TOOLS` prefixes and would miss stripped non-compactable returns), no `SUMMARY_MARKER_PREFIX`, last message is the pending `UserPromptPart`. Mix a `memory_create` (non-compactable) return into the history; assert its content starts with `[memory_create] ` (covers constraint #2).

2. **`test_recover_strip_plus_summary_fits`** — strip alone insufficient, planner finds bounds. Assert: result shorter than input, first non-head message starts with `STATIC_MARKER_PREFIX`, last message is the pending `UserPromptPart`.

3. **`test_recover_terminal_when_planner_returns_none`** — single-turn history, oversized after strip. Assert `recover_overflow_history` returns `None`.

4. **`test_recover_preserves_tool_call_id_pairing`** — parametrized over both paths (strip-only-fits, strip+summarize). Assert every `tool_call_id` in `ToolCallPart`s has a matching `ToolReturnPart` and vice versa.

5. **`test_strip_is_idempotent_on_marked_content`** — *Property: re-stripping already-stripped content preserves the existing marker (no degraded size signal).* Construct a `ToolReturnPart` whose content is already a per-tool marker (e.g. `[file_read] /x.py (full, 8,432 chars)`). Run `strip_all_tool_returns` on a history containing it. Assert content is unchanged byte-for-byte. Direct unit test on the helper, not on `recover_overflow_history`.

### ✓ DONE — TASK-4 — Spec sync via `/sync-doc`

- **files:** `docs/specs/compaction.md`
- **prerequisites:** [TASK-1, TASK-2, TASK-3]
- **done_when:**
  - `grep -nE "emergency_recover|_attempt_overflow|two-tier|len\(groups\) <= 2" docs/specs/compaction.md` returns no hits.
  - §1 mechanism table, §2.7 prose, §2.11 error table, §5 test gates, Diagram 1 OVF subgraph all describe single-tier strip-then-summarize.

Auto-invoked by `/orchestrate-dev` per the workflow.

## Testing

- Real fixtures, no mocks, per `tests/_settings.py`.
- `RunContext(model=None)` for all tests — gates the summarizer to its static-marker fallback.
- Budget control via `deps.model_max_ctx` (e.g. 2000 tokens).
- All pytest runs piped to `.pytest-logs/$(date +%Y%m%d-%H%M%S)-<scope>.log`.
- `scripts/quality-gate.sh full` before ship.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev overflow-recovery-single-tier`

## Delivery Summary — 2026-05-08

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | smoke import passes; `emergency_recover_overflow_history` gone from `compaction.py`; `_reset_thrash_state` confined to `recover_overflow_history` (not in `apply_compaction`); `_build_cleared_part` short-circuits on `is_cleared_marker` | ✓ pass |
| TASK-2 | `run_turn` smoke import passes; `_attempt_overflow_recovery` gone everywhere; both status messages emitted on appropriate paths | ✓ pass |
| TASK-3 | `pytest tests/test_flow_compaction_recovery.py -x` — 6 tests passed (5 cases + 1 parametrized variant) | ✓ pass |
| TASK-4 | `grep emergency_recover|_attempt_overflow|two-tier|len(groups) <= 2` across `docs/specs/compaction.md` and `docs/specs/core-loop.md` returns no hits; §1 mechanism table, Diagram 1 OVF subgraph, §2.7 prose, §2.11 error rows, §5 test-gates rows all describe single-tier strip-then-summarize | ✓ pass |

**Tests:** scoped (touched files + adjacent compaction tests) — 32 passed (recovery: 6, proactive: 26), 0 failed
**Doc Sync:** narrow scope — `compaction.md` (mechanism table, Diagram 1, §2.7, §2.11, §5) + `core-loop.md` (file inventory line). `bootstrap.md` "two-tier" hit is unrelated (skill loading) — left unchanged.
**Lint:** clean (`scripts/quality-gate.sh lint` PASS).

**Cross-team change accommodation:**
- `enforce_turn_budget` removal in `history_processors.py` (working tree) — recovery edits coexist; lint clean.
- `turn_aggregate_*` → `request_aggregate_*` rename in `compaction.py` (working tree) — recovery did not touch span attrs; no conflict.
- Coworker uncommitted spec edits to `core-loop.md`, `observability.md`, `prompt-assembly.md` — only the recovery-relevant line in `core-loop.md` was edited; other coworker edits left intact per the "bundle uncommitted" memory rule.

**Design correction folded in vs. original plan:** `_mark_compaction_applied` writes the three runtime fields ONLY. `_reset_thrash_state` stays an explicit recovery-path call (not folded into `apply_compaction`) — preserves proactive sliding-window thrash semantics. Verified by the 26 proactive tests passing without modification.

**Overall: DELIVERED**
Single-tier overflow recovery shipped with strip-then-summarize semantics, idempotent `_build_cleared_part`, single-write-site `_mark_compaction_applied`, and full spec sync. No regressions in proactive compaction or `/compact` paths.

## Implementation Review — 2026-05-08

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | smoke import; `emergency_recover_overflow_history` gone; `_reset_thrash_state` confined; `_build_cleared_part` short-circuits | ✓ pass | `history_processors.py:311-330` — `strip_all_tool_returns` no filter/cap/boundary; `history_processors.py:262-263` — `is_cleared_marker` short-circuit; `compaction.py:112-127` — `_mark_compaction_applied` writes three fields, no thrash reset; `compaction.py:332,348` — `_reset_thrash_state` called only inside `recover_overflow_history`; grep confirms `emergency_recover_overflow_history` absent from co_cli/ tests/ docs/specs/ |
| TASK-2 | `run_turn` smoke import; `_attempt_overflow_recovery` gone | ✓ pass | `orchestrate.py:74` — `recover_overflow_history` imported at module level; `orchestrate.py:671-691` — overflow inlined with one-shot gate, both status strings confirmed at lines 686, 689; grep confirms `_attempt_overflow_recovery` absent from co_cli/ tests/ docs/specs/ |
| TASK-3 | `pytest tests/test_flow_compaction_recovery.py -x` passes | ✓ pass | 6 tests pass: strip-only-fits, strip+summary, terminal, pairing (×2 parametrized), idempotency — all real `CoDeps`/`ShellBackend`/`CoSessionState`, no mocks |
| TASK-4 | no stale terms in `compaction.md`; §1/Diagram 1/§2.7/§2.11/§5 describe single-tier | ✓ pass | `grep -nE "emergency_recover|_attempt_overflow|two-tier|len\(groups\) <= 2" docs/specs/compaction.md` → no hits; §2.7 prose at compaction.md:429-434 describes strip-then-summarize verbatim |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `core-loop.md` error matrix row (§2.5) described old algorithm — no strip step, planner implied unconditional | core-loop.md:306 | minor | Updated to describe strip-then-summarize with budget gate |
| `compaction.md` §4 Files table omitted `strip_all_tool_returns` from `history_processors.py` row | compaction.md:577 | minor | Added as "Recovery helper (unregistered)" to distinguish from registered processors |

### Tests
- Command: `uv run pytest -v`
- Result: 199 passed, 0 failed
- Log: `.pytest-logs/20260508-*-review-impl.log`
- Recovery suite: `uv run pytest tests/test_flow_compaction_recovery.py -v` → 6 passed

### Doc Sync
- Scope: narrow — both fixes are within existing doc sections, no API renames
- Result: fixed: `core-loop.md:306` algorithm description; `compaction.md:577` file table entry

### Behavioral Verification
- No `co status` command exists in this CLI; smoke imports used instead
- `python -c "from co_cli.context.history_processors import strip_all_tool_returns; from co_cli.context.compaction import recover_overflow_history, _mark_compaction_applied; print('ok')"` → ok
- `python -c "from co_cli.context.orchestrate import run_turn; print('ok')"` → ok
- All 6 recovery tests exercise the behavioral paths end-to-end with real deps

### Overall: PASS
All blocking criteria verified, two minor doc-code mismatches found and fixed, full suite green at 199 passed.
