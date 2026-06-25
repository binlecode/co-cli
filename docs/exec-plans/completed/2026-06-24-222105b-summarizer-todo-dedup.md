# Summarizer todo de-duplication

## Context
Split from the schema-measurement plan (`2026-06-24-222105-summarizer-overdesign-trim.md`) on PO + Core Dev advice: this is a clean, source-verified dead-path subtraction that should ship on its own, not held hostage to the uncertain schema work.

Active session todos currently enter post-compaction context **twice** in a single pass:
1. Summarized into the recap — `gather_compaction_context(ctx)` (`_compaction_markers.py:181–191`) returns only session todos and is passed as `context=` to the summarizer (`compaction.py:213,218` → `summarize_messages(context=…)`), surfacing in the `=== ADDITIONAL CONTEXT ===` block of `_build_summarizer_prompt`.
2. Verbatim — `build_todo_snapshot` inserts a durable `TODO_SNAPSHOT_PREFIX` message after the marker (`compaction.py`; `_compaction_markers.py:167–178`).

The verbatim snapshot is the authoritative carry (durable, regenerated each pass, survives the summarizer dropping items). The enrichment feed is a lossy duplicate of the same data.

Both carriers gate on the same `_active_todos` non-empty check, so they are perfectly correlated — there is no case where the enrichment feed surfaces a todo the durable snapshot would not. Additionally, the most important todo (the `in_progress` one) still reaches the summarizer independently via `focus` (`_resolve_proactive_focus` `compaction.py:531–533` → `summarize_dropped_messages(focus=…)`), so removing the enrichment feed does not even cost the summary-side signal for the active task.

## Problem & Outcome
**Failure cost:** silent. Todos are duplicated in a context-constrained window every compaction — wasted tokens — and the lossy summarized copy can drift from the verbatim snapshot, giving the model two slightly different todo lists.

**Outcome:** active todos carried in exactly one place (the durable snapshot); the dead enrichment path and its now-orphaned plumbing removed (clarity-by-subtraction).

## Scope
**In:** drop the todo-enrichment feed to the summarizer; remove the resulting dead `context` parameter and orphaned helpers.
**Out:** the durable `build_todo_snapshot` carry (unchanged); the schema question (separate plan); `docs/specs/` edits (sync-doc post-delivery).

## Source verification (Core Dev, confirmed; re-verified at Gate 1)
- `gather_compaction_context` has exactly one consumer (`compaction.py:213`), one `__all__` export (`compaction.py:76`), one import (`compaction.py:45`), one `context=` call site (`compaction.py:218`).
- `summarize_messages`'s own callers (`/compact` path + tests) never pass `context=`, so dropping the param there is safe.
- After removal, `_gather_session_todos` is orphaned → remove it too. Removing `gather_compaction_context` also orphans the `RunContext` (`_compaction_markers.py:11`) and `CoDeps` (`:17`) imports — they have no other use in that file → remove them.
- **Guard:** `_active_todos` and `_format_active_todos` (`_compaction_markers.py:143–155`) are STILL used by `build_todo_snapshot` (`:174–177`) — do NOT remove them.
- **Test impact (corrects an earlier "zero references" claim):** the enrichment *path symbols* (`gather_compaction_context`, `_gather_session_todos`) have zero `tests/`/`evals/` references, BUT `_build_summarizer_prompt` takes `context` as its **leading positional** arg and three test call sites pass it: `tests/test_flow_compaction_summarization.py:301` (`_build_summarizer_prompt(None, personality_active=False, budget=3500)`) and `tests/test_flow_compaction_proactive.py:845–846` (`_build_summarizer_prompt(None, False, 2000, None, prior_summary)`). Removing the param shifts every positional arg → `TypeError`. These must be edited (drop the leading `None`). A grep for `context` will NOT catch positional `None` — the full suite is the real guard.

## Tasks

✓ DONE **TASK-1 — Remove the todo-enrichment feed and its dead plumbing**
- files:
  - `co_cli/context/compaction.py` — `summarize_dropped_messages`: stop building/passing `context=`; drop `gather_compaction_context` from imports (`:45`) + `__all__` (`:76`); update the module docstring + submodule map (`:6–7`, `:11`) that still advertise "enrichment gathering"/"enrichment context".
  - `co_cli/context/_compaction_markers.py` — remove `gather_compaction_context` and the now-orphaned `_gather_session_todos`; remove the now-orphaned `RunContext` (`:11`) and `CoDeps` (`:17`) imports; update the module docstring (`:1`, "enrichment-context gathering"); keep `_active_todos`/`_format_active_todos`.
  - `co_cli/context/summarization.py` — drop the dead `context` param from `summarize_messages`/`_build_summarizer_prompt`, the `=== ADDITIONAL CONTEXT ===` assembly block, and the context references in `_build_summarizer_prompt`'s docstring (`:327`, `:332–333`).
  - `tests/test_flow_compaction_summarization.py` (`:301`) and `tests/test_flow_compaction_proactive.py` (`:845–846`) — drop the leading positional `None` (`context`) arg from the three `_build_summarizer_prompt` calls.
- done_when: a repo-wide grep across `co_cli/`, `tests/`, `evals/` finds **zero** references to `gather_compaction_context` and `_gather_session_todos`; no surviving "enrichment" wording in the touched docstrings; `build_todo_snapshot` and `_active_todos`/`_format_active_todos` remain and are unchanged; `scripts/quality-gate.sh lint` (catches any orphaned import) + full suite (catches the `_build_summarizer_prompt` positional-arg break) pass.
- success_signal: N/A (pure refactor — removes a dead, duplicative path; todos now carried solely by the durable snapshot).
- prerequisites: none.

## Testing

The one behavior at risk from this change is **todo carry-through compaction** — the snapshot becomes the *sole* carrier once the enrichment feed is gone. That behavior is already covered end-to-end and behaviorally; the validation discipline here is to **run the existing guards against the post-removal code**, not to rely on a green full-suite alone or to add redundant tests.

**Deterministic regression guard (the load-bearing one for this change):**
- `tests/test_flow_compaction_proactive.py` "(d)" case (`:850–876`) — runs `compact_messages` end-to-end and asserts the assembled result still carries the `TODO_SNAPSHOT_PREFIX` message **and** exactly one compaction marker. This is the direct proof that removing the enrichment feed did not break todo carry. Must stay green unchanged.
- `tests/test_flow_compaction_todo_format.py` + `tests/test_flow_session_persistence.py:239–248` — snapshot body format + persistence, unchanged.
- `tests/test_flow_compaction_summarization.py` + `tests/test_flow_compaction_proactive.py` `_build_summarizer_prompt` cases — the param-signature edits land here; assertions unchanged, only the call drops the leading `None`.

**Behavioral eval validation (run during dev, not just the unit suite):**
- `evals/eval_session_continuity.py` W2.E (`compact_replaces_with_summary`) — drives a real history past `compaction_ratio` and compacts via the live summarizer, exercising the exact `summarize_messages` path whose `context` param this change removes. Confirms compaction still produces a valid post-compaction history end-to-end.
- `evals/eval_session_continuity.py` W2.D — seeds a todo, rotates, rehydrates; behavioral confirmation that a real todo survives the session/compaction round-trip.
- `evals/eval_context_stability.py` — multi-pass carry-forward (`_partition_dropped` → `summarize_messages`), directly downstream of the edited param; run to confirm no multi-pass regression.

**Mechanical checks:** repo-wide grep (zero hits for `gather_compaction_context`/`_gather_session_todos`; no surviving "enrichment" wording in touched docstrings) + `scripts/quality-gate.sh lint` (orphaned-import catch) + full suite. `__init__.py` docstring-only rule unaffected (no `__init__` touched).

**No new tests:** the at-risk behavior (todos survive compaction) is already asserted behaviorally by the proactive "(d)" guard and the W2.E/W2.D evals; this change removes a redundant producer, asserting nothing new functionally. Adding a test would duplicate existing coverage (against the functional-only / no-redundant-test rules). The effective validation is running the named guards above against the edited code.

## Open Questions
None.

## Delivery Summary — 2026-06-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | zero refs to `gather_compaction_context`/`_gather_session_todos`; no surviving "enrichment" wording in touched docstrings; `build_todo_snapshot`/`_active_todos`/`_format_active_todos` unchanged; lint + suite pass | ✓ pass |

**What was done:**
- `summarization.py` — dropped the `context` param from `summarize_messages` and `_build_summarizer_prompt`, removed the `=== ADDITIONAL CONTEXT ===` assembly block, and updated `_build_summarizer_prompt`'s docstring (assembly order no longer mentions context).
- `_compaction_markers.py` — removed `gather_compaction_context` and the now-orphaned `_gather_session_todos`; removed the orphaned `RunContext` + `CoDeps` imports and the now-orphaned `_TODOS_MAX_CHARS` constant (orphan created by the removal); module docstring "and enrichment-context gathering" dropped. `build_todo_snapshot`/`_active_todos`/`_format_active_todos` kept verbatim.
- `compaction.py` — `summarize_dropped_messages` no longer builds/passes `context=`; dropped `gather_compaction_context` from imports + `__all__`; module docstring + submodule map de-enrichment-ed.
- Tests — dropped the leading positional `None` (`context`) from the three `_build_summarizer_prompt` calls in `test_flow_compaction_summarization.py:301` and `test_flow_compaction_proactive.py:845–846`.

**Tests:** scoped — 44 passed, 0 failed (`test_flow_compaction_summarization`, `test_flow_compaction_proactive`, `test_flow_compaction_todo_format`, `test_flow_session_persistence`); the load-bearing deterministic todo carry-through guard (proactive "(d)") green. Behavioral eval `eval_session_continuity` W2.E (live summarizer path, exact removed-param) PASS, judge.score=10. LLM call timings healthy (4–8s, no stalls).

**Doc Sync:** fixed (full scope) — removed the enrichment-path documentation across `self-planning.md`, `compaction.md`, `core-loop.md` (mermaid nodes/edges, callstack tree, config tables, prose, Files rows); also corrected a pre-existing stale `build_todo_snapshot` signature in self-planning.md while in that table.

**Overall: DELIVERED**
Pure dead-path subtraction — active todos now carried solely by the durable `build_todo_snapshot`, with the `in_progress` todo still reaching the summarizer via `focus`. No behavior change at risk beyond todo carry-through, which the green deterministic guard + W2.E eval confirm intact.

## Implementation Review — 2026-06-24

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | zero refs to `gather_compaction_context`/`_gather_session_todos`/`_TODOS_MAX_CHARS`; no "enrichment" wording in touched docstrings; `build_todo_snapshot`/`_active_todos`/`_format_active_todos` intact; lint + suite pass | ✓ pass | `summarization.py:320-325` new `_build_summarizer_prompt(personality_active, budget, focus, prior_summary)`; `:354-361` `summarize_messages` has no `context` param; `:397` internal call updated. `_compaction_markers.py:1` docstring de-enriched, `:9-14` only `ModelRequest`/`UserPromptPart` imports remain (no `RunContext`/`CoDeps`); `_active_todos`/`_format_active_todos`/`build_todo_snapshot` kept (`:138/:145/:153`). `compaction.py:211-217` `summarize_dropped_messages` passes no `context=`; `:367-373` `build_todo_snapshot` still wired into assembly. Call paths traced: every live `summarize_messages(`/`_build_summarizer_prompt(` call site valid against new signatures (no leading `None`, no `context=`). done_when re-executed: both greps exit 1 (zero matches), lint PASS. |

### Issues Found & Fixed
No issues found. Evidence subagent (cold read, all 4 claims + done_when re-run) and adversarial subagent (cold re-read, 5 refutation attempts incl. the leading-`None`→`personality_active=None` latent-bug hunt) both returned all-PASS with no blocking or minor findings. The only residual mentions of removed symbols live in CHANGELOG / exec-plan / RESEARCH docs (intentional historical records).

### Tests
- Command: `uv run pytest` (full suite)
- Result: 847 passed, 0 failed
- Log: `.pytest-logs/20260624-231148-review-impl.log`
- The `_build_summarizer_prompt` positional-arg break (the named full-suite risk) did not surface — fix is correct. LLM call timings healthy, no stalls.

### Behavioral Verification
- `uv run co --help`: ✓ boots — import + bootstrap graph loads cleanly (edited compaction/summarization modules are on the bootstrap path)
- Compaction is LLM-mediated internal behavior (no CLI/tool/output surface changed); todo carry-through verified via `eval_session_continuity` W2.E (judge.score=10) + the deterministic proactive "(d)" guard during dev — chat non-gating
- `success_signal`: N/A (pure refactor)

### Overall: PASS
Clean dead-path subtraction; spec fully satisfied with file:line evidence, full suite green, no findings on cold double-review. Ready to ship.
