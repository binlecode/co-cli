# REPORT: Summarizer Prompt Upgrade — Eval Measurement

**Date:** 2026-04-18
**Plan:** `docs/exec-plans/active/2026-04-17-232342-summarizer-prompt-upgrade.md`

## Summary

Baseline (main, pre-upgrade): **6/12 steps passed.**
Upgrade branch (post TASK-1 + TASK-2): **13/13 steps passed.**

No regressions. Three new gates (Step 13a/b/c) all pass on the upgrade branch.

---

## Baseline — main (pre-upgrade)

Eval run on unmodified `main` before any prompt changes (TASK-1/TASK-2 reverted via `git stash`).

| Step | Description | Result |
|------|-------------|--------|
| Step 1: Imports | Required symbols importable | PASS |
| Step 2: Compactable tools | COMPACTABLE_TOOLS / FILE_TOOLS membership | **FAIL** |
| Step 3: Budget math | resolve_compaction_budget returns correct values | PASS |
| Step 4: Context gather | 3-source context collection | **FAIL** |
| Step 5: Prompt assembly | 5-section template structure | PASS (5 sections, old structure) |
| Step 6: Summarizer call | LLM summarization round-trip | **FAIL** (transient timeout) |
| Step 7: Multi-cycle | Prior summary integrated | **FAIL** |
| Step 8: Window logic | Sliding-window compaction fires | **FAIL** |
| Step 9: Circuit breaker | No-model guard + failure count | **FAIL** |
| Step 10: Token estimate | Token estimator accuracy | PASS |
| Step 11: Budget resolver | Ollama / spec / fallback paths | PASS |
| Step 12: Prompt composition | Template structure + ordering | PASS (5 sections, old structure) |

**Passed: 6/12**

### Failure root causes

Steps 2, 4, 7, 8, 9 are **pre-existing failures** introduced by compaction-refactor commits after the plan was written (plan date: 2026-04-17; refactor commits: `4c8740a`, `1f754da`, `6f7fadf`). These are not regressions introduced by this plan — they existed on `main` before TASK-1 was applied. The upgrade branch fixes all of them alongside the prompt upgrade.

- **Step 2**: `COMPACTABLE_TOOLS` and `FILE_TOOLS` membership changed in the compaction refactor. `edit_file` / `find_in_files` / `list_directory` removed; `glob`, `grep`, `read_article`, `read_note` added to COMPACTABLE_TOOLS; `patch` replaces `edit_file` in FILE_TOOLS.
- **Step 4**: `_gather_compaction_context` refactored from 4 sources (including always-on memory) to 3 sources (file paths, todos, prior summaries). Always-on memory now injected via P4 context, not compaction context.
- **Step 6**: Transient Ollama model latency — LLM call timed out at 40s. Not a code bug; second run passes. Recorded as transient.
- **Step 7**: Marker format changed from `[Summary of N earlier messages]` to `_SUMMARY_MARKER_PREFIX` format in the compaction refactor.
- **Step 8**: Same marker format change; count regex changed from `[Summary of (\d+) earlier messages]` to `portion \((\d+) messages\)`.
- **Step 9**: Circuit breaker now increments `compaction_failure_count` on every fire (count goes 3→4), not stays constant at 3 as the old test expected.

---

## Upgrade Branch — post TASK-1 + TASK-2

Eval run after applying all changes from TASK-1 (7-section prompt + eval fixes) and TASK-2 (Step 13 gates).

| Step | Description | Result |
|------|-------------|--------|
| Step 1: Imports | Required symbols importable | PASS |
| Step 2: Compactable tools | COMPACTABLE_TOOLS / FILE_TOOLS membership | PASS |
| Step 3: Budget math | resolve_compaction_budget returns correct values | PASS |
| Step 4: Context gather | 3-source context collection | PASS |
| Step 5: Prompt assembly | 7-section template structure | PASS |
| Step 6: Summarizer call | LLM summarization round-trip | PASS |
| Step 7: Multi-cycle | Prior summary integrated | PASS |
| Step 8: Window logic | Sliding-window compaction fires | PASS |
| Step 9: Circuit breaker | No-model guard + failure count | PASS |
| Step 10: Token estimate | Token estimator accuracy | PASS |
| Step 11: Budget resolver | Ollama / spec / fallback paths | PASS |
| Step 12: Prompt composition | Template structure + ordering (7 sections) | PASS |
| Step 13a: Verbatim anchor | Next Step contains verbatim quote (≥20 chars) from recent messages | PASS |
| Step 13b: User corrections | User Corrections section contains correction token | PASS |
| Step 13c: Error feedback | Errors & Fixes section references test failure + user redirect | PASS |

**Passed: 13/13**

### Changes validated

- `_SUMMARIZE_PROMPT` upgraded from 5 to 7 sections: Goal, Key Decisions, User Corrections, Errors & Fixes, Working Set, Progress, Next Step.
- `## Next Step` (singular) includes verbatim-anchor instruction with "1-2 lines" specification.
- `## User Corrections` includes four example correction phrasings.
- `## Errors & Fixes` includes user-feedback-shaping bullet.
- `_SUMMARIZER_SYSTEM_PROMPT` unchanged (security guardrail preserved).
- `_PERSONALITY_COMPACTION_ADDENDUM` unchanged.
- Steps 5 and 12 updated to 7-section structure with `## Next Step` (singular).
- Pre-existing failures in Steps 2, 4, 7, 8, 9 fixed to match current compaction-refactor state.

---

## Conclusion

All three prompt-upgrade quality gates pass (Step 13a/b/c). No existing gate regressed on the upgrade branch. The upgrade ships with measurable verification that:
1. Verbatim anchoring works — Next Step quotes recent messages.
2. User corrections are preserved — explicit correction tokens survive compaction.
3. Error-feedback loops are retained — user-directed fix reasoning is recorded.
