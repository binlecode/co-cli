# Drop the "segment" term — align co's vocabulary to pydantic-ai's "run"

## Context

co coined "segment" for what pydantic-ai natively calls a **run** (one `agent.run_stream_events()` invocation → one `AgentRunResult`, containing 1..N model requests). The mapping is 1:1 — `_execute_stream_segment` wraps exactly one `run_stream_events()` call (`orchestrate.py:379`). There is no `Segment` type; "segment" is purely a naming convention (identifiers + comments). It competes with pydantic-ai's own vocabulary and adds a third noun (turn / segment / request) where two suffice (turn / run / request).

**Canonical hierarchy (the load-bearing clarification this plan establishes):**

> **turn ⊇ run ⊇ model request** — a **turn** (one user message) contains one or more **runs**; a **run** (one `agent.run_stream_events()` → one `AgentRunResult`) contains one or more model **requests** (one request/response exchange with the LLM).

A turn spans multiple runs only at two boundaries — tool approval (run ends with `DeferredToolRequests`, co resumes in a fresh run) and length-continuation retry. A run spans multiple model requests whenever the model emits tool calls and the SDK loops to feed results back. This three-level containment is exactly pydantic-ai's own model; "segment" was co's redundant name for the middle level (run).

This hierarchy statement is itself a deliverable: it must land verbatim (or near-verbatim) in `docs/specs/core-loop.md` during the post-delivery `sync-doc` pass, not just in identifier renames — see Post-Delivery Follow-up.

**Scope reconnaissance — three distinct meanings of "segment" in source; only one is in scope:**

In scope (means pydantic-ai run / stream-run):
- `co_cli/context/orchestrate.py` — `_execute_stream_segment` (def + 3 call sites), `_LLM_SEGMENT_WARN_SECS`, ~35 comment/docstring refs incl. `_TurnState` docs
- `co_cli/context/_timeouts.py` — `LLM_SEGMENT_TIMEOUT_SECS` + comments
- `co_cli/context/summarization.py` — imports/uses `LLM_SEGMENT_TIMEOUT_SECS` + comments
- `co_cli/display/stream_renderer.py` — docstring/comment refs ("one stream segment", `_execute_stream_segment()`, "across segments or turns")
- `co_cli/agent/toolset.py:158` — "at the segment boundary" comment
- `co_cli/tools/tool_io.py:268` — comment referencing stale `_run_stream_segment` (also a name typo — actual fn is `_execute_stream_segment`)
- tests — comment refs across 8 files + one test name `test_multi_segment_turn_records_final_usage_once`

Out of scope (unrelated meanings — must NOT touch):
- `co_cli/tools/shell/_exit_codes.py:31` — "segmentation fault (SIGSEGV)"
- `co_cli/tools/deferred_prompt.py:54` — "the segment before the first `_`" (substring of a tool name)
- `co_cli/context/_compaction_markers.py:114-123` — `content.split(...)` text segments
- `tests/test_display.py` `_run_thinking_segment` / "thinking segment" — a thinking→text phase of one stream's output, not a run boundary (judgment call: rendering-phase sense, left as-is)

Completed exec-plans under `docs/exec-plans/completed/` are immutable history and are out of scope. `docs/specs/` are updated by `sync-doc` post-delivery, not in this plan's task `files:`.

## Problem & Outcome

**Problem:** "segment" is a redundant synonym for pydantic-ai's "run" that obscures the design — readers must learn a co-specific term that maps 1:1 to a term the SDK already defines.

**Outcome:** All run-sense "segment" identifiers and prose renamed to "run". co's vocabulary becomes turn → run → request, matching pydantic-ai. Canonical phrasing settled: *"a turn is one or more runs."*

**Failure cost:** Silent — nothing breaks at runtime. The cost is ongoing: every new contributor and every architecture explanation re-derives that "segment == run", and the divergent term keeps leaking into new code, comments, and specs (it has already spread to ~10 source files and 8 test files).

## Scope

In: rename run-sense identifiers and update run-sense prose in `co_cli/` source and `tests/`. Out: the three unrelated "segment" meanings listed above; `test_display.py` thinking-segment helper; completed exec-plans; `docs/specs/` (handled by post-delivery `sync-doc`); any change to `request_limit=None` / RC1 latency behavior (explicitly separate decision).

## Behavioral Constraints

- **Zero behavior change.** Pure rename. Same control flow, same timeouts, same `TurnResult` / results. "Same outputs" means functional results and control flow — it does **not** cover observability string text: run-sense `segment` tokens inside log messages, span `status_msg`, and exception text are renamed too (see below), which intentionally changes that emitted text. No test asserts on these strings (verified), so this is safe. (Aligns with project zero-backward-compat: rename is hard and immediate, no aliases.)
- No compatibility aliases for old names (`LLM_SEGMENT_TIMEOUT_SECS`, `_execute_stream_segment`) — delete and replace.
- Surgical: touch only run-sense "segment" tokens. Do not reformat or "improve" adjacent code.

## High-Level Design

Mechanical rename, no structural change. Name map:

| Old | New |
|-----|-----|
| `_execute_stream_segment` | `_execute_run` (drops `stream` — see rationale) |
| `LLM_SEGMENT_TIMEOUT_SECS` | `LLM_RUN_TIMEOUT_SECS` |
| `_LLM_SEGMENT_WARN_SECS` | `_LLM_RUN_WARN_SECS` |
| `test_multi_segment_turn_records_final_usage_once` | `test_multi_run_turn_records_final_usage_once` |

Prose rule: in run-sense comments/docstrings, "segment" → "run", "segment boundary" → "run boundary", "per-segment" → "per-run", "across segments" → "across runs", "initial/resume segment" → "initial/resume run". Fix the stale `_run_stream_segment` reference in `tool_io.py:268` to the new `_execute_run` name.

String-literal rule (in scope, not just comments): run-sense "segment" inside runtime string literals is renamed the same way. Specifically in `orchestrate.py`:
- `:411` `logger.debug("LLM segment elapsed: ...")` → "LLM run elapsed: ..."
- `:414` `logger.warning("LLM segment slow: ...")` → "LLM run slow: ..."
- `:418` `pop_span(status_msg="segment ended without AgentRunResultEvent")` → "run ended without AgentRunResultEvent"
- `:420` exception `"_execute_stream_segment: stream ended ... segment contract violated"` → "_execute_run: stream ended ... run contract violated"

These are the only run-sense string literals; renaming them is what makes TASK-1's `done_when` grep pass.

Why `_execute_run`, not `_execute_stream_run` (two tokens drop, not one): the rename drops both `segment` *and* `stream`. `segment` → `run` is the plan's purpose. Dropping `stream` is justified, not creep: (1) the function's own altitude is "execute one run and reconcile `_TurnState`" — it opens exactly one `agent.run_stream_events()` call and explicitly delegates all streaming mechanics to collaborators (`StreamRenderer` owns rendering/buffering/gating, `_handle_stream_event` owns dispatch), so `stream` names a detail the function pushes down rather than its responsibility; (2) `stream` disambiguates nothing — `orchestrate.py` has a single run-execution function with no `_execute_sync_run`/`_execute_buffered_run` sibling (the only non-streaming `agent.run()` lives in `agent/run.py`, a different module). The name should carry the *unit* (run), not the *transport* (stream).

Whole-token rule: where a docstring/comment references the function call `_execute_stream_segment()` (notably `stream_renderer.py:4` and `test_flow_observability_spans.py:4`), rewrite the complete identifier to `_execute_run()` — do not word-strip "segment" leaving a malformed `_execute_stream()`.

## Tasks

### ✓ DONE TASK-1 — Rename run-sense identifiers and prose in `co_cli/context/`
- files: `co_cli/context/orchestrate.py`, `co_cli/context/_timeouts.py`, `co_cli/context/summarization.py`
- done_when: `grep -rin "segment" co_cli/context/` returns only the `_compaction_markers.py` text-split occurrences; no `LLM_SEGMENT`/`_execute_stream_segment`/`_LLM_SEGMENT_WARN` identifiers remain; import of the timeout constant in `summarization.py` resolves to `LLM_RUN_TIMEOUT_SECS`.
- success_signal: N/A (pure refactor)
- prerequisites: none

### ✓ DONE TASK-2 — Update run-sense refs in display / agent / tools comments
- files: `co_cli/display/stream_renderer.py`, `co_cli/agent/toolset.py`, `co_cli/tools/tool_io.py`
- done_when: `grep -rin "segment" co_cli/display/stream_renderer.py co_cli/agent/toolset.py co_cli/tools/tool_io.py` returns nothing; `tool_io.py:268` references `_execute_run` (not stale `_run_stream_segment`).
- success_signal: N/A (pure refactor)
- prerequisites: none

### ✓ DONE TASK-3 — Update run-sense refs and test name in tests
- files: `tests/test_flow_usage_tracking.py`, `tests/test_flow_model_request_cap.py`, `tests/test_flow_approval_subject.py`, `tests/test_flow_orchestrate_length_retry.py`, `tests/test_flow_phase2_migrated.py`, `tests/test_flow_compaction_boundaries.py`, `tests/test_flow_observability_spans.py`
- done_when: `grep -rin "segment" tests/` returns only the `test_display.py` thinking-segment occurrences; `test_multi_segment_turn_records_final_usage_once` renamed to `test_multi_run_turn_records_final_usage_once`.
- success_signal: N/A (pure refactor)
- prerequisites: TASK-1 (test name references / behavior unchanged, but rename source first so any name-based references stay consistent)

### TASK-4 — Verify suite green after rename
- files: (none — verification only)
- done_when: `uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-drop-segment.log` passes with no collection errors or failures attributable to the rename; `scripts/quality-gate.sh lint` clean.
- success_signal: Full suite passes unchanged, proving the rename is behavior-preserving.
- prerequisites: TASK-1, TASK-2, TASK-3

## Testing

No new tests — pure rename, zero behavior change. The existing suite is the regression net (TASK-4). The rename must not alter any assertion's meaning; only the renamed test function and updated comments change in `tests/`.

## Open Questions

Resolved (G1): `_execute_stream_segment` → `_execute_run` drops the `stream` token as well as `segment`. Justified under High-Level Design ("Why `_execute_run`, not `_execute_stream_run`") — `stream` names a delegated implementation detail and disambiguates no sibling, so the name carries the unit (run), not the transport. Remaining name choices (`LLM_RUN_TIMEOUT_SECS`, `_LLM_RUN_WARN_SECS`) follow directly from the turn/run/request vocabulary.

## Post-Delivery Follow-up (mandatory, not optional)

`docs/specs/` prose alignment is deferred to post-delivery `sync-doc` per workflow — but it is a **hard follow-up**, not optional, to avoid shipping with an indefinite source/spec vocabulary split. The sync-doc pass must do three things:

1. **Add the canonical hierarchy statement** (turn ⊇ run ⊇ model request, from Context) as an explicit, prominent definition in `docs/specs/core-loop.md` — the canonical architecture doc. This is the primary point of the rename; renaming refs without stating the model leaves the clarification implicit. Cross-reference from `pydantic-ai-integration.md`.
2. **Sweep the ~48 run-sense "segment" refs** across 9 specs, concentrated in `core-loop.md` (26) and `pydantic-ai-integration.md` (5), into turn/run/request vocabulary.
3. **Correct a pre-existing drift:** `core-loop.md:170` cites `_LLM_SEGMENT_HANG_TIMEOUT_SECS`, a constant that does not exist in source (actual: `LLM_SEGMENT_TIMEOUT_SECS` / `_LLM_SEGMENT_WARN_SECS`, becoming `LLM_RUN_TIMEOUT_SECS` / `_LLM_RUN_WARN_SECS`).

---

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-m-1 | adopt | Line numbers were stale and the grep is line-agnostic anyway. | TASK-1 done_when: dropped the `(lines ~114-123)` citation. |
| CD-m-2 | adopt | Word-stripping would leave malformed `_execute_stream()`; must rewrite the full identifier. | Added a "Whole-token rule" paragraph under High-Level Design covering `stream_renderer.py:4` and `test_flow_observability_spans.py:4`. |
| PO-m-1 | adopt | Source/spec split is acceptable only if the sync-doc pass is mandatory, not optional; the stale `_LLM_SEGMENT_HANG_TIMEOUT_SECS` name should be fixed in the same pass. | Added "Post-Delivery Follow-up (mandatory)" section spelling out the spec surface and the pre-existing drift to fix. |
| PO-m-2 | reject | No change requested — PO confirmed `_execute_run` alongside `run_turn` is clarifying. | — |

## Cycle C2 — Gate 1 Review

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| G1-1 | adopt | Four run-sense `segment` tokens live in runtime string literals (2 log msgs, 1 span `status_msg`, 1 exception) — TASK-1's done_when grep forces renaming them, but the prose rule only named comments/docstrings. | Added a "String-literal rule" block under High-Level Design listing the four `orchestrate.py` sites. |
| G1-2 | adopt | The span `status_msg` and log lines are observability output, so "same outputs" was in mild tension; no test asserts on them (verified). | Clarified the "Zero behavior change" constraint: "same outputs" = functional results/control flow, not observability string text. |

## Final — Team Lead

Plan approved (G1 edits folded in — dev ready).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev drop-segment-term-align-to-run`

---

## Delivery Summary — 2026-06-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `grep segment co_cli/context/` only `_compaction_markers`; no `LLM_SEGMENT`/`_execute_stream_segment` identifiers; summarization import resolves to `LLM_RUN_TIMEOUT_SECS` | ✓ pass |
| TASK-2 | `grep segment` of stream_renderer/toolset/tool_io empty; `tool_io.py:268` → `_execute_run` | ✓ pass |
| TASK-3 | `grep segment tests/` only `test_display.py` thinking-segment; test renamed to `test_multi_run_turn_records_final_usage_once` | ✓ pass |
| TASK-4 | Full suite green + lint clean | — deferred to `/review-impl` (orchestrate-dev runs scoped tests only) |

**Team:** TL — TASK-1, TASK-2; Dev-1 — TASK-3.

**Tests:** scoped — 36 passed, 0 failed (the 7 flow test files touching the renamed run path + the renamed test). Full-suite regression net is review-impl's job.

**Doc Sync:** fixed (full-scope `/sync-doc`) — added the canonical `turn ⊇ run ⊇ model request` hierarchy block to `core-loop.md` (cross-ref from `pydantic-ai-integration.md`); swept ~40 run-sense "segment" refs across 9 specs into turn/run/request; §2.2 heading renamed to "Run Contract"; corrected the pre-existing `_LLM_SEGMENT_HANG_TIMEOUT_SECS` phantom-constant drift at `core-loop.md` to `LLM_RUN_TIMEOUT_SECS`. The 3 out-of-scope "segment" senses (tool-name `_`-split ×2, history-chunk ×1) left intact.

**Stale-symbol scan:** `grep _execute_stream_segment|LLM_SEGMENT|_LLM_SEGMENT` across `co_cli/`, `tests/`, `docs/specs/` returns nothing — no aliases, clean rename (zero-backward-compat satisfied).

**Behavior:** zero functional change. Renamed observability strings (2 log lines, 1 span `status_msg`, 1 exception) intentionally changed; no test asserts on them.

**Overall: DELIVERED**
Pure vocabulary rename across source + tests + specs; scoped tests green, lint clean, doc sync complete. Next: `/review-impl drop-segment-term-align-to-run` for the full-suite safety net and behavioral verification.

---

## Implementation Review — 2026-06-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | grep context/ only `_compaction_markers`; no `LLM_SEGMENT`/`_execute_stream_segment` ids; summarization import → `LLM_RUN_TIMEOUT_SECS` | ✓ pass | `_timeouts.py:3` value still 120; `orchestrate.py:59` `_LLM_RUN_WARN_SECS` still 90; def `orchestrate.py:339`, call sites `:473`/`:783` consistent; `summarization.py:30` import, used `:414`; 4 run-sense string literals renamed (`:411`,`:413`,`:408/416`,`:418`); no alias assignments |
| TASK-2 | grep of stream_renderer/toolset/tool_io empty; `tool_io.py` → `_execute_run` | ✓ pass | `tool_io.py:268` comment now `_execute_run` (exists at `orchestrate.py:339`); phantom `_run_stream_segment` gone repo-wide; all edits are comment/docstring prose, no logic touched |
| TASK-3 | grep tests/ only `test_display.py` thinking-segment; test renamed | ✓ pass | `test_flow_usage_tracking.py:98` `def test_multi_run_turn_records_final_usage_once`; old name zero hits; `test_display.py` untouched (`git diff --stat` empty); **every diff hunk inspected — all comment/docstring/test-name; zero `assert` predicate or fixture change** |

Cross-task integration: full-repo scan `grep _execute_stream_segment|LLM_SEGMENT|_LLM_SEGMENT|_run_stream_segment` over `co_cli/` + `tests/` + `docs/specs/` returns nothing — clean rename, no stale references, no aliases (zero-backward-compat satisfied).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Section comment "run one run" (repetitive prose) | `orchestrate.py:271` | minor | Changed to "execute one run" for consistency with the docstring |
| Pre-existing uncommitted changes unrelated to this plan | `commands/filescope.py`, `commands/skills.py`, `commands/tools.py`, `main.py`, `uv.lock` | minor (scope) | Not touched by this delivery; flagged for ship-time staged-file hygiene — stage only this plan's files |

_No blocking findings. No mocks/fakes/global-state/security issues._

### Tests
- Command: `uv run pytest`
- Result: **746 passed, 0 failed** (incl. renamed `test_multi_run_turn_records_final_usage_once`)
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- No user-facing CLI surface changed — internal function/constant/comment rename only. The 4 renamed observability strings (log/span/exception) are visible only via `co tail`/`co trace`, not asserted, not user-facing.
- CLI bootstraps cleanly (`co --help` renders; bootstrap imports resolve).
- Core loop path `run_turn → _execute_run` exercised end-to-end by the green flow-test suite.
- `success_signal` for all tasks was N/A (pure refactor) — nothing to smoke-check.

### Overall: PASS
Pure, behavior-preserving vocabulary rename; full suite green, lint clean, no stale references or aliases, specs aligned with the canonical turn ⊇ run ⊇ request hierarchy. Ready to ship — stage only this plan's files (exclude the 5 pre-existing unrelated changes).
