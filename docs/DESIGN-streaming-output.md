# DESIGN: Streaming Output

Real-time tool output and Markdown-rendered text streaming via `run_stream_events()` + `rich.Live` + `rich.Markdown`.

---

## Problem

Two UX problems with the original `agent.run()` approach:

1. **Gemini summarizes tool output.** `agent.run()` gives the LLM full control over what it shows. Gemini's training ("be helpful and conversational") causes it to summarize shell output rather than relay it verbatim. System prompts don't reliably prevent this — they're suggestions, not constraints.

2. **No progressive output.** Nothing appears until the entire `agent.run()` finishes (all tool calls + full LLM response). Every peer CLI streams token-by-token.

---

## pydantic-ai Streaming APIs (v1.52+)

Four APIs available. Each makes different trade-offs between simplicity, completeness, and control.

### 1. `run()` + `event_stream_handler`

Handler receives all events in real time (tool calls, results, text deltas). The `run()` call blocks until completion and returns `AgentRunResult`. Functionally equivalent to `run_stream_events()` — the difference is ergonomic (callback vs inline loop).

**Best for:** Incrementally adding streaming to an existing `run()` codebase.

### 2. `run_stream()`

Context manager yielding `StreamedRunResult` with `stream_text()` for token-by-token output. Supports an optional `event_stream_handler` for tool events.

**Disqualified for co-cli.** Treats the first output match as final and stops the graph early. Incompatible with `output_type=[str, DeferredToolRequests]` — graph may stop before all tool calls execute, and `stream_text()` raises `UserError` when output is `DeferredToolRequests`.

### 3. `run_stream_events()`

Flat async iterable of all events — tool calls, tool results, text deltas, and the final `AgentRunResultEvent`. Runs the full agent graph (same as `run()`). Works naturally with `DeferredToolRequests` and accepts `deferred_tool_results` for approval resume.

**Best for:** Production agents that need both tool display and text streaming with deferred tools.

### 4. `iter()`

Full graph-node control — manually step through model request nodes and tool call nodes. 3-4x more code than `run_stream_events()` for the same streaming outcome.

**Best for:** Complex orchestration requiring injected logic between graph nodes. Overkill for co-cli.

---

## Decision: `run_stream_events()`

### Requirements

1. **Real-time tool output** — tool call annotations and tool result display as they happen
2. **Token-by-token text** — progressive Markdown rendering during LLM response
3. **DeferredToolRequests compatibility** — full graph completion, `str | DeferredToolRequests` output type

### Elimination

- `run_stream()` — disqualified (early graph stop, crashes on deferred tools)
- `iter()` — same outcome, 3-4x more code
- `run()` + handler — functionally equivalent to `run_stream_events()`, but callback ergonomics split display state across handler function and caller

### Why inline loop

`run_stream_events()` yields events in an `async for` loop where display state (text buffer, pending tool commands, result) lives as local variables. Control flow — event dispatch, rendering, result extraction — is visible in one place. Matches the pydantic-ai docs' recommended pattern for agents with deferred tools.

### Single consumer, single API

The chat loop is the only consumer. No reason to abstract over multiple streaming backends or make the choice configurable. If pydantic-ai deprecates `run_stream_events()`, the migration is localized to ~30 lines.

---

## Implementation

`_stream_agent_run()` in `co_cli/main.py` wraps `run_stream_events()` and dispatches four event types inline:

- **Text deltas** — accumulated into a buffer, rendered as `rich.Markdown` inside `rich.Live` with 20 FPS throttle (`_RENDER_INTERVAL = 0.05`). Uses `auto_refresh=False` with manual `refresh()` for precise throttle control.
- **Text commit** — before any tool event, `Live` is flushed (update + refresh + stop) to prevent interleaving Markdown with tool output. Buffer and render state are reset.
- **Tool calls** — dim annotation showing tool name and arguments (shell commands show the `cmd` value).
- **Tool results** — `Panel` for shell output (string content), verbatim for structured tools (dict with `display` field).
- **Result** — captured from `AgentRunResultEvent` as the final return value.
- **Cancellation cleanup** — `finally` block calls `Live.stop()` on interrupt to restore terminal state.

**Signature:** `_stream_agent_run(agent, *, user_input, deps, message_history, model_settings, usage_limits, deferred_tool_results)` — `usage_limits` is a parameter (not read from `settings`). The `settings.max_request_limit` read happens only in `chat_loop`.

**Returns:** `(result, streamed_text)` — the caller uses `streamed_text` to skip `Markdown(result.output)` when text was already rendered during streaming.

Both the main chat loop and `_handle_approvals` use `_stream_agent_run`, so post-approval tool results and LLM follow-up also stream in real time.

---

## Markdown Rendering

`rich.Live` + `rich.Markdown` with a fixed 50ms (20 FPS) throttle. Accumulate-and-rerender: each throttled update re-parses the full buffer as Markdown. No two-zone split (scrollback + live region) — co-cli's terse responses don't need it. Adding the two-zone split later is a ~30-line change if response length grows.

### Peer analysis

All 4 peer CLIs converge on **accumulate + re-render Markdown progressively**:

| System | Stack | Approach | Throttle | Two-Zone Split |
|--------|-------|----------|----------|----------------|
| **Aider** (Python) | `rich.Live` + `rich.Markdown` | Accumulate buffer, full re-render on each chunk | Adaptive: `min(max(render_time*10, 1/20), 2)` | Yes — last 6 lines in Live, rest printed to scrollback |
| **Codex** (Rust) | `pulldown_cmark` + ratatui | Newline-gated: render on `\n`, hold partial lines | Adaptive: queue depth (8 lines) or age (120ms) | Yes — committed lines + animation queue |
| **Gemini CLI** (TS) | Custom parser + Ink `<Static>` | Accumulate, split at safe Markdown boundaries | React render cycle | Yes — `<Static>` for completed, pending for streaming |
| **OpenCode** (TS) | `marked` + `morphdom` | Accumulate, DOM diff on each update | Fixed 100ms | Yes — morphdom patches only changed nodes |

### Why no two-zone split

The two-zone split solves expensive re-renders for long responses (hundreds of lines). Doesn't apply to co-cli: system prompt says "Be terse" (1-5 sentences), expensive content (shell output) is already in Panels from tool result events. If needed later, track line count, `console.print()` completed lines, keep remainder in Live.

---

## References

- [pydantic-ai Agents docs — streaming APIs](https://ai.pydantic.dev/agent/)
- [pydantic-ai Deferred tools docs](https://ai.pydantic.dev/deferred-tools/)
- pydantic-ai source: `agent/abstract.py` — `EventStreamHandler`, `run_stream_events()`
- pydantic-ai source: `messages.py` — `AgentStreamEvent`, `FunctionToolCallEvent`, `FunctionToolResultEvent`, `PartDeltaEvent`, `TextPartDelta`
- pydantic-ai source: `result.py` — `StreamedRunResult`, `stream_text()` raises on non-str output
