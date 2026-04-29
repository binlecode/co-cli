# Plan: Remove Online Distiller

**Task type: refactor**

## Context

The online distiller (`co_cli/knowledge/_distiller.py`) runs two extraction paths during a session:

1. **Per-turn fire-and-forget** — `fire_and_forget_extraction` called from `_finalize_turn()` in `main.py` every `extract_every_n_turns` turns (default: 3). Writes knowledge artifacts during the live session.
2. **Compaction-boundary sync** — `extract_at_compaction_boundary` awaited inline in `apply_compaction()` and `emergency_recover_overflow_history()` in `compaction.py`. Blocks the compaction return path.

Both paths write to `~/.co-cli/knowledge/` for use in future sessions. The dream cycle (`_dream.py`) retrospectively mines past session transcripts and does the same job at session end, without blocking any live path.

The architectural problem: distilled knowledge artifacts are only injected at session start via recall. They are not available during the current session. Therefore, online distillation during a session has no benefit for the current session, and dream already covers the future-session use case retrospectively.

The compaction-boundary sync path is the acute performance problem: in `test_iterative_summary_3_pass_preservation` it adds ~65 s across 3 compaction cycles (two LLM round trips per cycle — tool call + completion handshake — totaling ~21 s per cycle × 3 = ~63 s).

**Current-state validation:** No stale exec-plan conflicts found. `docs/specs/memory.md` §2.5 documents the per-turn extraction and compaction-boundary extraction accurately as of this reading — it will need a doc pass after delivery (handled by `sync-doc`).

## Problem & Outcome

**Problem:** Online distillation runs synchronously at compaction boundaries and every N turns, adding latency to both paths with no benefit to the current session.

**Failure cost:** 120 s test runtime for `test_iterative_summary_3_pass_preservation`; compaction is 3× slower in any real session that triggers 3+ compactions; in live sessions the compaction pause is user-visible as a multi-second freeze before the next turn can proceed.

**Outcome:** Compaction no longer triggers any LLM extraction call. Per-turn extraction cadence is removed. Dream is the primary automatic distillation path; agent-explicit `knowledge_save` remains available at any time. The test drops to ~55 s (3 summarizer calls only). Knowledge artifacts continue to accumulate through dream and agent-explicit `knowledge_save` calls.

## Scope

**In scope:**
- Remove `extract_at_compaction_boundary` calls from `compaction.py` (both sites)
- Remove `fire_and_forget_extraction` + cadence counter from `main.py._finalize_turn`
- Remove `drain_pending_extraction` from `main.py._drain_and_cleanup`
- Delete extraction functions from `_distiller.py`: `build_knowledge_extractor_agent`, `_run_extraction_async`, `_make_extraction_done_callback`, `fire_and_forget_extraction`, `drain_pending_extraction`, `extract_at_compaction_boundary`
- Rename `_distiller.py` → `_window.py` (retains `_tag_messages` and `build_transcript_window`, used by dream); update `_dream.py` import
- Delete dead prompt file `co_cli/knowledge/prompts/knowledge_extractor.md`
- Remove state fields `last_extracted_message_idx`, `last_extracted_turn_idx` from `CoSessionState`; remove `extraction_task` from `CoRuntimeState`; remove orphaned `import asyncio` from `deps.py`
- Remove `extract_every_n_turns` from `MemorySettings` and `MEMORY_ENV_MAP`
- Delete `evals/eval_memory_extraction_flow.py` (eval covers the removed feature; all imports and assertions reference deleted symbols)
- Delete extraction-specific tests; rename test file to `test_window.py`

**Out of scope:**
- Dream cycle — untouched
- `knowledge_save` tool — agent-explicit writes remain fully intact
- Knowledge recall (search tools) — untouched
- Any change to compaction summarizer timing

## Behavioral Constraints

- `apply_compaction` and `emergency_recover_overflow_history` must complete without any LLM call beyond the summarizer.
- Dream cycle (`_maybe_run_dream_cycle` in `main.py`) must still run at session end unchanged.
- Agent-explicit `knowledge_save` calls during a turn must continue to work (not affected by this change).
- `CO_MEMORY_EXTRACT_EVERY_N_TURNS` env var must be ignored after removal (unknown env vars are already rejected by pydantic `extra="forbid"` — the env map entry must be removed to prevent load errors).
- Users with `consolidation_enabled = false` (the default) retain only agent-explicit `knowledge_save` for automatic knowledge accumulation after this change. This is an accepted trade-off: both the removed online extraction and dream write artifacts only available in future sessions; within-session knowledge access is unaffected. Users who want automatic distillation can enable `consolidation_enabled = true`.
- No new public API is introduced. No existing public tool interface changes.

## High-Level Design

The change is purely subtractive. Three call sites are severed, three families of supporting code are removed, and one module is renamed. The dream cycle already provides the replacement behavior without any new code.

```
Before:
  _finalize_turn()         → fire_and_forget_extraction() [every N turns]
  apply_compaction()       → await extract_at_compaction_boundary()  [blocks]
  emergency_recover()      → await extract_at_compaction_boundary()  [blocks]
  _drain_and_cleanup()     → await drain_pending_extraction()

After:
  _finalize_turn()         → [nothing]
  apply_compaction()       → [nothing]
  emergency_recover()      → [nothing]
  _drain_and_cleanup()     → await _maybe_run_dream_cycle()  [unchanged]
```

`_window.py` retains:
- `_tag_messages(messages)` — tagged stream builder
- `build_transcript_window(messages, *, max_text, max_tool)` — used by `_dream.py`

## Implementation Plan

### ✓ DONE — TASK-1: Remove caller sites in compaction.py and main.py

**files:**
- `co_cli/context/compaction.py`
- `co_cli/main.py`

**Changes:**
- `compaction.py`: remove the deferred import block and `await extract_at_compaction_boundary(...)` call at lines 266–269 (inside `apply_compaction`) and lines 354–357 (inside `emergency_recover_overflow_history`).
- `main.py._finalize_turn`: remove the `fire_and_forget_extraction` import, the `deps.session.last_extracted_turn_idx += 1` cadence counter, the `if deps.session.last_extracted_turn_idx % n == 0:` gate, and the `fire_and_forget_extraction(...)` call. Remove the `n = deps.config.memory.extract_every_n_turns` line and its guarding `if n > 0:` block entirely.
- `main.py._drain_and_cleanup`: remove the `drain_pending_extraction` import and `await drain_pending_extraction(deps)` call.

**done_when:** `uv run pytest tests/context/test_context_compaction.py -x` passes; `grep -n "extract_at_compaction_boundary\|fire_and_forget_extraction\|drain_pending_extraction" co_cli/context/compaction.py co_cli/main.py` returns no matches.

**success_signal:** N/A (internal refactor; no user-visible behavior change this task alone).

---

### ✓ DONE — TASK-2: Strip extraction functions from _distiller.py and rename to _window.py

**files:**
- `co_cli/knowledge/_distiller.py` (delete after extracting retained code)
- `co_cli/knowledge/_window.py` (new — contains retained utilities)
- `co_cli/knowledge/_dream.py` (update import)
- `co_cli/knowledge/prompts/knowledge_extractor.md` (delete)

**Changes:**
- Create `co_cli/knowledge/_window.py` containing only the module docstring, `_tag_messages`, and `build_transcript_window` (with all their existing imports and type hints). Remove all extraction-related imports (`pydantic_ai`, `Agent`, `knowledge_save`, `asyncio.CancelledError` path, otel tracer import used only in extraction).
- Delete `co_cli/knowledge/_distiller.py`.
- In `co_cli/knowledge/_dream.py`: change `from co_cli.knowledge._distiller import build_transcript_window` → `from co_cli.knowledge._window import build_transcript_window`.
- Delete `co_cli/knowledge/prompts/knowledge_extractor.md`.

**done_when:** `uv run pytest tests/knowledge/ -x` passes; `python -c "from co_cli.knowledge._window import build_transcript_window; print('ok')"` prints `ok`; `ls co_cli/knowledge/_distiller.py` returns no such file; `grep -r "_distiller" co_cli/` returns no matches.

**success_signal:** N/A.

**prerequisites:** [TASK-1]

---

### ✓ DONE — TASK-3: Remove orphaned state fields and config

**files:**
- `co_cli/deps.py`
- `co_cli/config/memory.py`

**Changes:**
- `deps.py` — `CoSessionState`: remove fields `last_extracted_message_idx: int = 0` and `last_extracted_turn_idx: int = 0` and their comments.
- `deps.py` — `CoRuntimeState`: remove field `extraction_task: asyncio.Task[None] | None` and its comment; remove `extraction_task` from the cross-turn state docstring list.
- `deps.py`: remove `import asyncio` (line 3) — confirmed sole usage is the `extraction_task` annotation.
- `config/memory.py` — `MemorySettings`: remove `extract_every_n_turns` field.
- `config/memory.py` — `MEMORY_ENV_MAP`: remove the `"extract_every_n_turns": "CO_MEMORY_EXTRACT_EVERY_N_TURNS"` entry.

**done_when:** `python -c "from co_cli.deps import CoSessionState, CoRuntimeState; s=CoSessionState.__dataclass_fields__; assert 'last_extracted_message_idx' not in s and 'last_extracted_turn_idx' not in s; r=CoRuntimeState.__dataclass_fields__; assert 'extraction_task' not in r; print('ok')"` prints `ok`; `grep "asyncio" co_cli/deps.py` returns no matches.

**success_signal:** N/A.

**prerequisites:** [TASK-1]

---

### ✓ DONE — TASK-4: Delete extraction eval, tests; rename test file to test_window.py

**files:**
- `evals/eval_memory_extraction_flow.py` (delete)
- `tests/knowledge/test_distiller_window.py` (delete)
- `tests/knowledge/test_window.py` (new — retains window builder tests only)
- `tests/bootstrap/test_config.py`

**Changes:**
- Delete `evals/eval_memory_extraction_flow.py` — covers the removed feature; all imports (`fire_and_forget_extraction`, `drain_pending_extraction` from `co_cli.knowledge._distiller`) and all state references (`extract_every_n_turns`, `last_extracted_turn_idx`, `last_extracted_message_idx`) would crash after TASK-1–3.
- Create `tests/knowledge/test_window.py` containing only the 6 window-builder tests from the original file (those not importing or exercising extraction functions):
  - `test_tool_call_part_appears_in_window`
  - `test_build_transcript_window_interleaves_text_and_tool_in_order`
  - `test_tool_return_truncated_at_300`
  - `test_large_read_tool_output_skipped`
  - `test_build_transcript_window_applies_independent_caps`
  - `test_build_transcript_window_empty_messages_returns_empty_string`
  - Update their import: `from co_cli.knowledge._window import build_transcript_window`
- Delete `tests/knowledge/test_distiller_window.py`.
- `tests/bootstrap/test_config.py`: delete `test_memory_extract_every_n_turns_env_override` and `test_memory_extract_every_n_turns_default`.

**done_when:** `uv run pytest tests/knowledge/test_window.py tests/bootstrap/test_config.py -x` passes; `ls tests/knowledge/test_distiller_window.py` returns no such file; `ls evals/eval_memory_extraction_flow.py` returns no such file.

**success_signal:** N/A.

**prerequisites:** [TASK-2, TASK-3]

## Testing

The full test gate is `scripts/quality-gate.sh full`. The key regression check for this change:

```bash
uv run pytest tests/context/test_context_compaction.py -x
uv run pytest tests/knowledge/ -x
uv run pytest tests/bootstrap/test_config.py -x
```

The 120 s test `test_iterative_summary_3_pass_preservation` should drop to ~55 s after TASK-1.

No new tests are written — this is purely a deletion. The window builder tests in `test_window.py` are the surviving test surface for the retained utility code.

## Open Questions

None. All questions answerable by inspection of the source.

## Final — Team Lead

Plan approved. All C1 blocking issues resolved (CD-M-1: eval deletion added to scope and TASK-4; CD-M-2: `import asyncio` removal added to TASK-3; PO-M-1: explicit behavioral constraint documenting the `consolidation_enabled = false` trade-off added).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev remove-online-distiller`

## Delivery Summary — 2026-04-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | compaction tests pass; no extraction calls in compaction.py or main.py | ✓ pass |
| TASK-2 | knowledge tests pass; `_window` import works; `_distiller.py` deleted; no `_distiller` refs | ✓ pass |
| TASK-3 | python assertion prints `ok`; no asyncio in deps.py | ✓ pass |
| TASK-4 | window/config tests pass; deleted files confirmed absent | ✓ pass |

**Tests:** scoped (tests/context/test_context_compaction.py, tests/knowledge/, tests/bootstrap/test_config.py) — 208 passed, 1 deselected (pre-existing LLM-flaky test `test_summarizer_verbatim_anchor_in_next_step` confirmed unrelated to this change by baseline verification)

**Doc Sync:** fixed — memory.md (section 1 flowchart, §2.5 rewritten, config table, files table), compaction.md (3 diagram/prose sites), core-loop.md (4 flowchart/prose sites), dream.md (4 sites), system.md (1 site); stale path `co_cli/prompts/personalities/_loader.py` in memory.md flagged (pre-existing, coworker personality restructuring in progress)

**Overall: DELIVERED**
All 4 tasks shipped. Online extraction removed from both compaction and turn paths; `_distiller.py` renamed to `_window.py` with only window-builder retained; orphaned state fields and config removed; extraction eval and tests deleted; 5 specs updated.

## Implementation Review — 2026-04-28

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | no extraction calls in compaction.py or main.py | ✓ pass | compaction.py:227-269 `apply_compaction` — no extraction call; compaction.py:324-362 `emergency_recover_overflow_history` — no extraction call; main.py:81-113 `_finalize_turn` — no extraction call; main.py:143-157 `_drain_and_cleanup` — calls `_maybe_run_dream_cycle` only |
| TASK-2 | `_window` importable; `_distiller.py` deleted; no `_distiller` refs | ✓ pass | `_window.py` exists with `_tag_messages` + `build_transcript_window`; `dream.py:40` imports `from co_cli.knowledge._window import build_transcript_window`; `_distiller.py` absent; `knowledge_extractor.md` absent from prompts/ |
| TASK-3 | state fields removed; no asyncio in deps.py | ✓ pass | `CoSessionState` (deps.py:91-109) — no `last_extracted_message_idx` / `last_extracted_turn_idx`; `CoRuntimeState` (deps.py:112-166) — no `extraction_task`; deps.py imports — no `asyncio`; `MemorySettings` (config/memory.py:13-18) — no `extract_every_n_turns` field |
| TASK-4 | deleted files absent; window/config tests pass | ✓ pass | `test_distiller_window.py` absent; `eval_memory_extraction_flow.py` absent; `test_window.py` has 6 tests all green; `test_config.py` has no `extract_every_n_turns` references |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Import sort violation (I001): `from co_cli.knowledge._window import build_transcript_window` placed after `similarity` instead of before `archive` | dream.py:40 | blocking | Auto-fixed with `ruff --fix` — import now at line 29, sorted before `co_cli.knowledge.archive` |

### Tests
- Command: `uv run pytest -v`
- Result: 699 passed, 0 failed
- Log: `.pytest-logs/20260428-001353-review-impl.log`

### Doc Sync
- Scope: already run by orchestrate-dev — 5 specs updated
- Result: clean; lint fix to dream.py was import re-sort only (no semantic change, no doc update needed)

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components online, no extraction-related errors
- No user-facing behavior changed by this refactor — behavioral verification complete.

### Overall: PASS
All blocking findings resolved (one import-sort violation auto-fixed), full test suite green (699/699), doc sync already complete, system starts cleanly.
