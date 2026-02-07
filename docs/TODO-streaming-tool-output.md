# TODO: Streaming Tool Output

**Priority:** Highest-impact UX improvement. All responses currently render after `agent.run()` completes; every peer CLI streams token-by-token.

---

## Problem

When using Gemini as the LLM provider, shell command output is summarized instead of displayed directly.

```
Co > list files
Co is thinking...
Execute command: ls -la? [y/n]: y
Here are the files.   <-- Expected: actual file listing
```

This is an **architecture problem**, not a model quirk. `agent.run()` gives the LLM full control over what it shows the user. Gemini's training objective ("be helpful and conversational") causes it to summarize tool output rather than relay it verbatim. System prompts don't reliably prevent this — they're suggestions, not constraints.

Ollama models follow instructions more literally, but any model could summarize. The fix must be architectural.

---

## Current State

The chat loop has two mechanisms for showing tool output:

1. **`agent.run()`** (main.py:277-281) — LLM sees tool results and decides what to show. Gemini summarizes.
2. **`_display_tool_outputs()`** (main.py:191-213) — Interim workaround that post-processes `result.all_messages()` to print `ToolReturnPart` content in panels *after* the full run completes.

Both have the same latency problem: nothing appears until the entire `agent.run()` finishes (tool execution + LLM inference).

```python
# Current flow (main.py:274-292):
console.print("[dim]Co is thinking...[/dim]")
result = await agent.run(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
    usage_limits=UsageLimits(request_limit=settings.max_request_limit),
)

while isinstance(result.output, DeferredToolRequests):
    result = await _handle_approvals(agent, deps, result, model_settings)

all_msgs = result.all_messages()
_display_tool_outputs(len(message_history), all_msgs)
message_history = all_msgs
console.print(Markdown(result.output))
```

---

## Fix: `run_stream()` with `event_stream_handler`

### Principle

Don't rely on the model to relay tool output. Print tool results directly to the console as they arrive, then stream the model's final response token-by-token.

```
Tool executes → print result directly to console (bypass LLM)
                              ↓
LLM sees tool result → generates commentary/next step
                              ↓
Stream LLM's final text to console token-by-token
```

### API Choice

pydantic-ai (≥1.52) offers three streaming APIs:

| Method | Description | Best For |
|--------|-------------|----------|
| `run_stream()` | Stream final text + optional event callback | **This use case** — simple, clean |
| `run_stream_events()` | Flat async iterator of all events | Custom UIs, logging |
| `iter()` | Full graph-node control, manual stepping | Complex agent orchestration |

**`run_stream()` with `event_stream_handler`** is the right choice:
- Simplest API — one context manager, one async for loop
- Event handler fires for tool calls/results without interrupting the stream
- `stream_text()` gives token-by-token output for the final response
- Passes `model_settings`, `message_history`, `usage_limits` through naturally

### What Changes

| Aspect | Before (`agent.run`) | After (`run_stream`) |
|--------|---------------------|---------------------|
| Tool output | LLM decides what to show | Printed directly via event handler |
| Final response | Rendered all at once | Streamed token-by-token |
| UX feel | "Co is thinking..." then wall of text | Progressive output as it happens |
| Gemini summarization | Breaks tool output | Irrelevant — tool output bypasses LLM |
| `_display_tool_outputs()` | Post-run workaround | Removed — event handler replaces it |
| Approval flow | `run()` → approval → `run()` | `run_stream()` → approval → `run()` |

### Implementation

#### Event handler

```python
from collections.abc import AsyncIterable

from pydantic_ai import AgentStreamEvent, FunctionToolCallEvent, FunctionToolResultEvent, RunContext
from rich.panel import Panel

async def _print_tool_events(
    ctx: RunContext[CoDeps],
    event_stream: AsyncIterable[AgentStreamEvent],
) -> None:
    """Print tool calls and results directly to console as they happen."""
    async for event in event_stream:
        if isinstance(event, FunctionToolCallEvent):
            tool = event.part.tool_name
            console.print(f"[dim]→ calling {tool}...[/dim]")
        elif isinstance(event, FunctionToolResultEvent):
            content = event.result.content
            if isinstance(content, str) and content.strip():
                console.print(Panel(content.rstrip(), border_style="shell"))
            elif isinstance(content, dict) and "display" in content:
                console.print(content["display"])
```

#### Chat loop

```python
async with agent.run_stream(
    user_input,
    deps=deps,
    message_history=message_history,
    model_settings=model_settings,
    usage_limits=UsageLimits(request_limit=settings.max_request_limit),
    event_stream_handler=_print_tool_events,
) as run:
    # Stream the model's final text token-by-token.
    # IMPORTANT: do NOT use delta=True — it prevents the final result
    # message from being included in all_messages(), which breaks
    # conversation memory. Use accumulated mode and track printed offset.
    printed = 0
    async for text in run.stream_text():
        new = text[printed:]
        if new:
            console.print(new, end="")
            printed = len(text)
    console.print()  # newline after streaming

result = run.result

# Approval flow — falls back to run() for the resume call
while isinstance(result.output, DeferredToolRequests):
    result = await _handle_approvals(agent, deps, result, model_settings)

if isinstance(result.output, str) and printed == 0:
    # Only print if we didn't already stream (e.g. after approval resume)
    console.print(Markdown(result.output))

message_history = result.all_messages()
```

### Approval flow interaction

`run_stream()` and `DeferredToolRequests` don't compose in a single call. When the agent needs tool approval, it returns `DeferredToolRequests` as output before generating any text stream. Strategy:

1. `run_stream()` for the initial call — event handler shows tool calls/results in real time
2. After stream completes, check `result.output` type
3. If `DeferredToolRequests` — fall back to `_handle_approvals()` which uses `run()` for resume
4. If `str` — already streamed, done

The resumed call after approval uses `run()` not `run_stream()`. This is acceptable:
- Approval-required tools already show their output via the event handler on the initial call
- The LLM's post-approval response is typically short
- Converting `_handle_approvals` to streaming is a follow-up optimization

### `_display_tool_outputs()` removal

After migration, `_display_tool_outputs()` (main.py:191-213) is no longer needed — the event handler shows tool results as they arrive, not after the full run. Remove the function and its callsite at line 290.

### Files to Modify

| File | Change |
|------|--------|
| `co_cli/main.py` | Add `_print_tool_events()` event handler |
| `co_cli/main.py` | Replace `agent.run()` with `run_stream()` + event handler in chat loop |
| `co_cli/main.py` | Remove `_display_tool_outputs()` — replaced by event handler |
| `co_cli/main.py` | Verify `_patch_dangling_tool_calls` works with `run_stream` message history |

### Considerations

**`stream_text()` caveat:** `stream_text(delta=True)` is simpler to print (each yield is just the new text) but it does **not** populate `all_messages()` with the final result message — pydantic-ai skips building the complete response. Since we need `all_messages()` for conversation memory, we must use accumulated mode and track the printed offset.

**Conversation memory:** `run_stream` returns a `StreamedRunResult` which has `.all_messages()` just like `run`. Memory accumulation is unchanged.

**Ctrl+C handling:** `KeyboardInterrupt` during streaming needs the same dangling-tool-call patch. The `async with` block handles cleanup; catch the interrupt and patch history.

**Markdown rendering:** With `agent.run()` we render the full response as Markdown. With streaming, we get text chunks. Print as plain text for the initial implementation — the model's commentary is usually short when tool output is already displayed. Full Markdown rendering can be a follow-up (accumulate then render).

---

## Alternatives Considered

### `iter()` — Full Graph Control

```python
async with agent.iter(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
) as run:
    async for node in run:
        if Agent.is_call_tools_node(node):
            async with node.stream(run.ctx) as handle_stream:
                async for event in handle_stream:
                    if isinstance(event, FunctionToolCallEvent):
                        console.print(f"[dim]→ calling {event.part.tool_name}...[/dim]")
                    elif isinstance(event, FunctionToolResultEvent):
                        console.print(event.result.content)
        elif Agent.is_model_request_node(node):
            async with node.stream(run.ctx) as request_stream:
                async for event in request_stream:
                    if isinstance(event, FinalResultEvent):
                        break
                printed = 0
                async for text in request_stream.stream_text():
                    new = text[printed:]
                    if new:
                        console.print(new, end="")
                    printed = len(text)
                console.print()

    message_history = run.result.all_messages()
```

**Rejected:** More code, same outcome. `run_stream` with event handler is strictly simpler for this use case. `iter()` is better suited for complex orchestration (e.g., injecting user approval mid-graph).

### Post-process `result.new_messages()` (No Streaming)

This is what `_display_tool_outputs()` does today. Tool output only appears after the full run completes. User waits for both tool execution AND model inference before seeing anything. No streaming benefit. The model still summarizes — we just also show raw output above it, creating duplicated/confusing output.

### `run_stream_events()` — Flat Event Iterator

**Viable but less clean:** Requires manually tracking text deltas via `PartDeltaEvent` / `TextPartDelta` and assembling output. `run_stream` separates concerns better — events go to the handler, final text goes to `stream_text()`.

### `GoogleModelSettings` Tuning

pydantic-ai supports `GoogleModelSettings` with Gemini-specific options (thinking budget, safety settings). No option prevents summarization — it's a training behavior, not a configurable parameter.

---

## References

- [pydantic-ai Agents docs — streaming APIs](https://ai.pydantic.dev/agent/)
- [pydantic-ai Google model settings](https://ai.pydantic.dev/models/google/)
