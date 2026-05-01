# Plan: Critical Functional Gap Coverage

**Task type:** test — add behavioral tests for six untested critical production paths.

## Context

A test-coverage audit (2026-04-30) traced every major co-cli workflow and identified
six code paths where silent failure is possible with no existing test catching it.
The gaps were confirmed by reading production source directly, not by coverage tooling.

Current suite: 68 tests, 6 agentic + 6 non-agentic LLM calls, 56 unit tests.
None of the six gaps below are exercised by any existing test.

## Problem & Outcome

**Problem:** Six production code paths have zero behavioral test coverage. Each one
can fail silently — wrong return value, corrupted history, or wrong side-effect —
with no test catching the regression.

**Outcome:** Six new test files (or additions to existing files) with real-dependency
tests (no mocks) that would catch a regression in each path. Full suite still passes.

## Scope

In scope:
- TASK-1: `plan_compaction_boundaries` algorithm — new `test_flow_compaction_boundaries.py`
- TASK-2: `proactive_window_processor` trigger + anti-thrash gate — new `test_flow_compaction_proactive.py`
- TASK-3: `_build_interrupted_turn_result` truncation logic — add to new `test_flow_orchestrate.py`
- TASK-4: `save_artifact` + `mutate_artifact` dedup/write paths — new `test_flow_memory_write.py`
- TASK-5: `persist_session_history` + `load_transcript` round-trip — new `test_flow_session_persistence.py`
- TASK-6: Approval deny path + auto-approval session rule — add to `test_flow_tool_calling_functional.py`

Out of scope:
- HTTP 400 reformulation loop (requires LLM + HTTP interception; risk/complexity out of proportion)
- Streaming event renderer (StreamRenderer is a display concern; covered indirectly by agentic tests)
- MCP tool coverage

## Behavioral Constraints

- All tests use real `CoDeps`, real SQLite, real filesystem (`tmp_path`). No monkeypatch, no
  unittest.mock, no hand-assembled fakes that bypass production code.
- `conftest.py` must not be modified — new tests must be self-contained.
- LLM-calling tests (TASK-2 proactive, TASK-6) must use `ensure_ollama_warm` before any
  `asyncio.timeout` block per testing policy.
- Module-level model/agent instances are cached at module scope, not rebuilt per test.
- Timeout constants imported from `tests._timeouts`, never hardcoded.
- Each test must answer "if deleted, would a regression go undetected?" with yes.

## High-Level Design

---

### ✓ DONE — TASK-1: `plan_compaction_boundaries` algorithm
**File:** `tests/test_flow_compaction_boundaries.py` (new)
**Production path:** `co_cli/context/_compaction_boundaries.py:plan_compaction_boundaries`
**No LLM needed** — pure function over message lists.

Four tests:

**`test_normal_three_turn_history_returns_valid_bounds`**
Build 3-turn message list (each turn: `ModelRequest[UserPromptPart] + ModelResponse[TextPart]`).
Call `plan_compaction_boundaries(messages, budget=8000, tail_fraction=0.4)`.
Assert: result is not None; `head_end >= 1`; `tail_start > head_end`; `dropped_count == tail_start - head_end`.

**`test_returns_none_when_only_one_turn_group`**
Build 1 turn (request + response). Assert `plan_compaction_boundaries(...)` returns None.
Failure mode: planner returns a boundary on a 1-group history → dropped_count=0 → empty compaction loop.

**`test_last_turn_group_always_retained_even_over_tail_budget`**
Build 2 turns. Make the last turn's tokens > `tail_fraction * budget`. Assert result retains
the last turn in tail (i.e. last `UserPromptPart` content is in `messages[tail_start:]`).
Failure mode: last user turn silently dropped from context → model loses current request.

**`test_find_first_run_end_anchors_at_first_text_response`**
Build: `[ModelRequest, ModelResponse(ToolCallPart only), ModelResponse(TextPart)]`.
Assert `find_first_run_end` returns index 2 (the TextPart response), not 1 (tool-only response).
Failure mode: head anchors too early → first substantive model output falls into dropped middle.

---

### ✓ DONE — TASK-2: `proactive_window_processor` trigger + anti-thrash gate
**File:** `tests/test_flow_compaction_proactive.py` (new)
**Production path:** `co_cli/context/compaction.py:proactive_window_processor`
**Non-agentic LLM** — the summarizer call inside `apply_compaction` uses the real model.
Use `make_settings(num_ctx=..., compaction_ratio=...)` to control trigger threshold precisely.

Three tests:

**`test_processor_returns_messages_unchanged_when_below_threshold`**
Build a short 2-turn history (well under any reasonable token budget).
Call `proactive_window_processor(ctx, messages)` directly.
Assert result is the same list object (unchanged) — no compaction applied.
Assert `deps.runtime.compaction_applied_this_turn` is False.
**No LLM call** (threshold not crossed, returns early).

**`test_processor_applies_compaction_when_above_threshold`**
Build a 4-turn history with large TextPart content. Override settings via `make_settings`
so token threshold is crossed. Call `proactive_window_processor(ctx, messages)`.
Assert result is shorter than input (compaction applied).
Assert `deps.runtime.compaction_applied_this_turn` is True.
Assert a compaction marker is present in result messages.
**LLM call** — wrap with `asyncio.timeout(LLM_CALL_TIMEOUT_SECS)`.

**`test_anti_thrash_gate_skips_compaction_after_consecutive_low_yield`**
Set `deps.runtime.consecutive_low_yield_proactive_compactions = cfg.proactive_thrash_window`.
Build above-threshold history. Call `proactive_window_processor(ctx, messages)`.
Assert result is the same list (gate blocked compaction).
Assert `deps.runtime.compaction_applied_this_turn` is False.
**No LLM call** (gate returns early before summarizer).

---

### ✓ DONE — TASK-3: `_build_interrupted_turn_result` truncation logic
**File:** `tests/test_flow_orchestrate.py` (new)
**Production path:** `co_cli/context/orchestrate.py:_build_interrupted_turn_result`
**No LLM needed** — pure function.

Two tests:

**`test_interrupted_result_drops_unanswered_tool_call_response`**
Build history ending with a `ModelResponse` that contains a `ToolCallPart` (no matching
`ToolReturnPart` in history — unanswered). Pass to `_build_interrupted_turn_result`.
Assert: `result.interrupted` is True; last message in `result.messages` is **not** the
`ModelResponse` with the `ToolCallPart`; last message is a `ModelRequest` containing
"interrupted" in the `UserPromptPart` content (abort marker).
Failure mode: unanswered tool call stays in history → next-turn model sees a dangling
`ToolCallPart` without a response → pydantic-ai raises `UnexpectedModelBehavior`.

**`test_interrupted_result_preserves_clean_history_and_appends_abort_marker`**
Build history ending with a `ModelResponse` containing only a `TextPart` (clean end).
Pass to `_build_interrupted_turn_result`.
Assert: `result.interrupted` is True; all original messages are present in `result.messages`;
last message is the abort marker `ModelRequest`.
Failure mode: clean history silently truncated → conversation context lost on interrupt.

---

### ✓ DONE — TASK-4: `save_artifact` + `mutate_artifact` service paths
**File:** `tests/test_flow_memory_write.py` (new)
**Production path:** `co_cli/memory/service.py:save_artifact`, `mutate_artifact`
**No LLM needed** — filesystem + FTS5 only.

Construct a real `KnowledgeStore` backed by a `tmp_path` DB, and a real `knowledge_dir`
under `tmp_path`. Use `make_settings()` for config.

Five tests:

**`test_save_artifact_straight_save_creates_file_and_indexes`**
Call `save_artifact(knowledge_dir, content="...", artifact_kind="note", ...)`.
Assert: `result.action == "saved"`; `result.path.exists()` is True;
`result.artifact_id` is non-empty. Query `knowledge_store` search to confirm the
artifact is findable by keyword from its content.
Failure mode: file written but not indexed → `memory_search` misses newly created artifacts.

**`test_save_artifact_url_keyed_dedup_updates_existing`**
Save an article with `source_url="https://example.com/page"`.
Save again with same `source_url`, different content.
Assert second call returns `result.action in ("appended", "merged")`;
only one file exists in `knowledge_dir` for that URL.
Failure mode: duplicate articles accumulate silently — user gets stale content in search.

**`test_save_artifact_jaccard_dedup_skips_near_identical`**
Enable `consolidation_enabled=True`. Save artifact with content A.
Save again with near-identical content (>0.9 Jaccard similarity).
Assert second call returns `result.action == "skipped"`.
Failure mode: near-duplicate artifacts pile up → search results return noise.

**`test_mutate_artifact_append_adds_content_at_end`**
Save an artifact. Call `mutate_artifact(slug=..., action="append", content="new line")`.
Assert `result.action == "appended"`; reading the file confirms "new line" appears at end.
Failure mode: append silently no-ops or overwrites → memory modification is lost.

**`test_mutate_artifact_replace_rejects_non_unique_target`**
Save an artifact with repeated passage ("same line\nsame line"). Call `mutate_artifact`
with `action="replace"`, `target="same line"`. Assert `ValueError` is raised.
Failure mode: replace picks wrong occurrence → artifact body silently corrupted.

---

### ✓ DONE — TASK-5: `persist_session_history` + `load_transcript` round-trip
**File:** `tests/test_flow_session_persistence.py` (new)
**Production path:** `co_cli/memory/transcript.py:persist_session_history`, `load_transcript`
**No LLM needed** — filesystem only.

Three tests:

**`test_normal_turn_appends_delta_to_existing_session`**
Write two messages via `persist_session_history(history_compacted=False, persisted_message_count=0)`.
Write two more with `persisted_message_count=2`.
Load via `load_transcript`. Assert all 4 messages present in correct order.
Failure mode: delta append miscounts → messages written twice or skipped on reload.

**`test_compaction_branches_to_child_session`**
Write initial history to `session_path`.
Call `persist_session_history(..., history_compacted=True)`.
Assert: returned path is different from `session_path`; child file exists;
`load_transcript(child_path)` returns the compacted messages.
Load parent via `load_transcript` — child session does NOT appear in parent.
Failure mode: compacted history lost (never branched) → session lost on resume after compaction.

**`test_load_transcript_skips_pre_boundary_on_large_file`**
Write messages to a session file, then write a `compact_boundary_marker`, then write more messages.
Pad the file to exceed `SKIP_PRECOMPACT_THRESHOLD` (5 MB) by writing large content before the boundary.
Call `load_transcript`. Assert only post-boundary messages are returned (pre-boundary messages absent).
Failure mode: large session reloads full uncompacted history → OOM or wrong context on resume.

---

### ✓ DONE — TASK-6: Approval deny path + auto-approval session rule
**File:** `tests/test_flow_tool_calling_functional.py` (add to existing)
**Production path:** `co_cli/context/orchestrate.py:_collect_deferred_tool_approvals` +
`co_cli/tools/approvals.py:record_approval_choice`
**Agentic LLM** — needs real `run_turn` so pydantic-ai deferred-tool machinery fires.

The existing `run_turn` test harness from `test_flow_tool_calling_functional.py` is reused
(same `run_turn` + headless `Frontend` + real `CoDeps` pattern).

Two tests:

**`test_denied_tool_does_not_execute`**
Prompt Co to write a file to `tmp_path / "denied.txt"` using `file_write`.
Inject a `HeadlessFrontend` that returns `"n"` (deny) on `prompt_approval`.
Assert: `result.outcome == "continue"`; `denied.txt` does NOT exist after the turn.
Failure mode: `ToolDenied` is not properly wired into the SDK resume path → tool runs
despite denial → unauthorized file writes or shell commands execute silently.

**`test_auto_approval_skips_prompt_for_remembered_session_rule`**
Pre-seed `deps.session.session_approval_rules` with a `SessionApprovalRule`
matching the shell utility (e.g. `ApprovalKindEnum.SHELL, value="git"`).
Prompt Co to run `git status`. Inject a `HeadlessFrontend` that fails the test if
`prompt_approval` is ever called.
Assert: turn completes without `prompt_approval` being invoked; tool result contains
git output.
Failure mode: `is_auto_approved` mis-matches the session rule → user re-prompted every
turn for a tool they already approved, breaking the "always" approval contract.

---

## Task Order

```
TASK-1  plan_compaction_boundaries   pure logic   no LLM    ~1h
TASK-3  _build_interrupted_turn      pure logic   no LLM    ~30m
TASK-5  session persistence          filesystem   no LLM    ~1h
TASK-4  memory write service         filesystem   no LLM    ~1.5h
TASK-2  proactive_window_processor   logic+LLM    2 calls   ~1h
TASK-6  approval deny + auto         agentic      2 calls   ~1.5h
```

TASK-1, TASK-3, TASK-5 have no dependencies — can parallelize.
TASK-4 depends only on `tmp_path` setup (independent).
TASK-2 and TASK-6 require LLM access — run last and sequentially to avoid model contention.

## Acceptance Criteria

- `scripts/quality-gate.sh full` passes (lint + all 68 + new tests green).
- Each new test answers YES to: "if deleted, would this regression go undetected?"
- No monkeypatch, no unittest.mock, no hand-assembled fakes in any new test.
- Test file names follow `test_flow_<area>.py`.
- All timeout values imported from `tests._timeouts`.

## Delivery Summary — 2026-04-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_flow_compaction_boundaries.py -v` passes (4 tests) | ✓ pass |
| TASK-2 | `uv run pytest tests/test_flow_compaction_proactive.py -v` passes (3 tests) | ✓ pass |
| TASK-3 | `uv run pytest tests/test_flow_orchestrate.py -v` passes (2 tests) | ✓ pass |
| TASK-4 | `uv run pytest tests/test_flow_memory_write.py -v` passes (5 tests) | ✓ pass |
| TASK-5 | `uv run pytest tests/test_flow_session_persistence.py -v` passes (3 tests) | ✓ pass |
| TASK-6 | `uv run pytest tests/test_flow_tool_calling_functional.py -v` passes (4 tests incl. 2 new) | ✓ pass |

**Tests:** scoped (touched files) — 21 passed, 0 failed (76.75s)
**Doc Sync:** narrow scope — all tasks confined to test additions, no production API changes; no doc sync needed.

**Overall: DELIVERED**
All 6 behavioral gap tests shipped: 5 new files + 2 tests added to existing file; 21 tests green; lint clean; no mocks or fakes anywhere in the new suite.

## Implementation Review — 2026-04-30

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `pytest tests/test_flow_compaction_boundaries.py` passes (4 tests) | ✓ pass | `_compaction_boundaries.py:130` — `plan_compaction_boundaries` present; `:92` — `find_first_run_end` skips tool-only at `:109` |
| TASK-2 | `pytest tests/test_flow_compaction_proactive.py` passes (3 tests) | ✓ pass | `compaction.py:395` — `proactive_window_processor`; anti-thrash gate at `:443`; `ensure_ollama_warm` called before `asyncio.timeout` at test lines 107/108 |
| TASK-3 | `pytest tests/test_flow_orchestrate.py` passes (2 tests) | ✓ pass | `orchestrate.py:431` — `_build_interrupted_turn_result`; ToolCallPart drop at `:444-449`; abort marker content contains "interrupted" at `:456` |
| TASK-4 | `pytest tests/test_flow_memory_write.py` passes (5 tests) | ✓ pass | `service.py:122` — `save_artifact`; URL dedup at `:148`; Jaccard skip at `:241`; `mutate_artifact` at `:319`; non-unique replace guard at `:370` |
| TASK-5 | `pytest tests/test_flow_session_persistence.py` passes (3 tests) | ✓ pass | `transcript.py:89` — `persist_session_history`; branch-on-compaction at `:107`; `load_transcript` boundary skip at `:145-159` |
| TASK-6 | `pytest tests/test_flow_tool_calling_functional.py` passes (4 tests) | ✓ pass | `HeadlessFrontend.approval_calls` confirmed in `display/headless.py:46`; harness shows auto-approval run: `approvals` list non-empty, `prompt_approval` never called (0 calls) |

### Issues Found & Fixed

No issues found.

### Tests

- Command: `uv run pytest -v`
- Result: 86 passed, 0 failed (up from 68; 18 new tests across 6 tasks)
- Log: `.pytest-logs/20260430-*-review-impl.log`

### Doc Sync

- Scope: skipped — all tasks are test-only additions; no production API, schema, or module was changed.

### Behavioral Verification

No user-facing changes — behavioral verification skipped.

### Overall: PASS

Full suite green (86/86), lint clean, no mocks or fakes, all six `done_when` criteria confirmed by file:line evidence.
