# FIX: Streaming Leading Token Drop (Thinking + Text)

## Summary
In verbose chat streaming, the first few tokens of `thinking` (and potentially regular text) could be missing from terminal output.

Observed symptom example:
- Expected: `Now, the user is asking...`
- Shown: `, the user is asking...`

## User Impact
- Reduced debuggability: reasoning traces start mid-sentence.
- Perceived instability in stream rendering.
- Possible loss of leading tokens for normal text when provider emits initial text in a start event.

## Root Cause Analysis
The stream handler in `co_cli/_orchestrate.py` processed only `PartDeltaEvent` for `ThinkingPartDelta` and `TextPartDelta`.

However, pydantic-ai streaming can deliver initial content in `PartStartEvent` (`ThinkingPart`/`TextPart`) before deltas.

Result:
1. Initial chunk arrives as `PartStartEvent`.
2. Handler ignored it.
3. Later deltas were appended/rendered.
4. Final output missed the leading chunk.

## Fix Implementation
### 1. Handle `PartStartEvent` content explicitly
File: `co_cli/_orchestrate.py`

Changes:
- Added imports: `PartStartEvent`, `ThinkingPart`, `TextPart`.
- In `_stream_events()`, added branch:
  - `PartStartEvent` + `ThinkingPart`: append `part.content` when `verbose=True`.
  - `PartStartEvent` + `TextPart`: append `part.content` always.

### 2. Unify append/render logic
File: `co_cli/_orchestrate.py`

Changes:
- Introduced `_append_thinking(content)` and `_append_text(content)`.
- Both start-event and delta-event paths now call the same helpers.

Why:
- Eliminates duplicated buffering logic.
- Prevents future drift where one event path is updated and the other is not.

### 3. Edge-case guard (best-practice cleanup)
File: `co_cli/_orchestrate.py`

Change:
- `_append_text()` now returns early for empty content **before** flushing thinking.

Why:
- Avoids unnecessary state transitions caused by empty chunks.
- Keeps flushing behavior tied to real text arrival.

## Regression Tests Added
File: `tests/test_orchestrate.py`

New async tests:
- `test_stream_events_preserves_text_from_part_start_event()`
  - Simulates `TextPart("Hel")` + `TextPartDelta("lo")`.
  - Asserts final commit includes full `"Hello"`.
- `test_stream_events_preserves_thinking_from_part_start_event()`
  - Simulates `ThinkingPart("Sure")` + `ThinkingPartDelta(", thing")`.
  - Asserts final thinking commit includes full `"Sure, thing"`.

Test harness details:
- Uses a minimal async event source (`StaticEventAgent`) to feed deterministic event sequences into `_stream_events()`.
- Uses real `CoDeps` (`SubprocessBackend`) to avoid type-ignore shortcuts.

## Verification
Executed:
- `.venv/bin/pytest tests/test_orchestrate.py -q`

Result:
- `10 passed`

## Anti-pattern Review
Checked against project and code-quality constraints:
- No global mutable state introduced.
- No rendering side-effects moved into orchestration decisions (frontend contract unchanged).
- No broad exception swallowing added.
- No new bypasses around approval/security logic.
- No test-only `type: ignore` shortcuts in the added regression tests.
- No behavior change for non-verbose thinking visibility policy.

## Files Changed
- `co_cli/_orchestrate.py`
- `tests/test_orchestrate.py`
- `docs/FIX-streaming-leading-token-drop.md`
