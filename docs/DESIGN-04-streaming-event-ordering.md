---
title: "04 — Streaming Event Ordering"
parent: Core
nav_order: 4
---

# Design: Streaming Event Ordering

## 1. What & How
`co_cli/_orchestrate.py` consumes typed `pydantic-ai` stream events and applies semantic boundaries for when to append, flush, and commit text/thinking output so leading tokens are never dropped and valid event interleavings cannot split output incorrectly. Scope is limited to `_stream_events()` event handling and render-boundary behavior; approval flow, tool semantics, and frontend protocol are unchanged.

```mermaid
flowchart TD
    A[pydantic-ai stream events] --> B[_stream_events()]
    B --> C{Event type}
    C -->|PartStart/PartDelta text| D[Append text_buffer]
    C -->|PartStart/PartDelta thinking| E[Append thinking_buffer]
    C -->|FinalResult/PartEnd| F[No boundary side effects]
    C -->|ToolCall/ToolResult| G[Flush thinking + commit text]
    G --> H[Render tool event]
    D --> I[Throttled render]
    E --> I
    B --> J[Stream end]
    J --> K[Flush thinking + commit text]
```

## 2. Core Logic

### First-principles RCA
1. Boundaries were isolated first: provider stream chunks, `pydantic-ai` typed events, and co-cli rendering callbacks.
2. Raw stream reconstruction showed content started correctly, so corruption was not primarily in model output.
3. Valid interleaving was observed: `PartStartEvent(TextPart)` -> `FinalResultEvent` -> `PartDeltaEvent(TextPartDelta)`, proving initial content can arrive in start events.

### Root causes and fixes
1. `PartStartEvent` content for text/thinking was not appended, dropping leading tokens.
Fix: append on both `PartStartEvent` and `PartDeltaEvent`.
2. Generic non-text events could trigger commit boundaries, so `FinalResultEvent` could split output.
Fix: treat `FinalResultEvent` and `PartEndEvent` as meta/no-op for rendering boundaries.

### Event semantics
- Content events:
  - `PartStartEvent(TextPart|ThinkingPart)` -> append content
  - `PartDeltaEvent(TextPartDelta|ThinkingPartDelta)` -> append content
- Meta events:
  - `FinalResultEvent`, `PartEndEvent` -> no boundary side effects
- Tool display boundaries:
  - Before `FunctionToolCallEvent` and `FunctionToolResultEvent`, flush thinking and commit text

### State model
Explicit transient state tracks:
- `text_buffer`
- `thinking_buffer`
- render timestamps for throttling
- `thinking_active`
- `streamed_text`

### Pseudocode
```text
for event in run_stream_events():
  if part_start(text|thinking): append
  elif part_delta(text|thinking): append
  elif final_result or part_end: no-op for rendering
  elif tool_call or tool_result:
    flush_thinking()
    commit_text()
    render_tool_event()
  elif agent_run_result:
    store_result

on stream end:
  flush_thinking()
  commit_text()
```

### Verification
- `tests/test_orchestrate.py::test_stream_events_preserves_text_from_part_start_event`
- `tests/test_orchestrate.py::test_stream_events_preserves_thinking_from_part_start_event`
- `tests/test_orchestrate.py::test_stream_events_does_not_commit_text_on_final_result_event`
- `.venv/bin/pytest tests/test_orchestrate.py -q`
- `.venv/bin/pytest tests/test_approval.py -q`

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| _(none)_ | - | - | No user-configurable setting is introduced; ordering behavior is defined in `_stream_events()` event handling. |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_orchestrate.py` | Implements `_stream_events()` event consumption, buffering, flush/commit boundaries |
| `tests/test_orchestrate.py` | Regression tests for start-event content handling and `FinalResultEvent` boundary behavior |
| `docs/DESIGN-04-streaming-event-ordering.md` | Design rationale, event model, and verification references |
