# Issue: Gemini Summarizes Shell Tool Output

**Status:** Open — fix planned (migrate to `run_stream` + `event_stream_handler`)
**Severity:** UX (not a bug)
**Affected Provider:** Gemini (Ollama unaffected)
**pydantic-ai version:** 1.52+

## Problem

When using Gemini as the LLM provider, shell command output is summarized instead of displayed directly.

**Example:**
```
Co > list files
Co is thinking...
Execute command: ls -la? [y/n]: y
Here are the files.   <-- Expected: actual file listing
```

## Root Cause

This is an **architecture problem**, not just a model quirk. The current `chat_loop()` uses `agent.run()` which gives the LLM full control over what it shows the user. Gemini's training objective ("be helpful and conversational") causes it to summarize tool output rather than relay it verbatim.

### Why System Prompts Don't Fix It

The system prompt says "show tool output directly — don't summarize", but:
1. **System prompts are suggestions** — models can and do ignore them
2. **Gemini optimizes for "helpfulness"** which it interprets as summarizing
3. **No amount of prompt engineering reliably prevents this** across all queries

### Evidence the Tool Itself Works

```python
# Direct sandbox test — full output returned:
>>> from co_cli.sandbox import Sandbox
>>> s = Sandbox()
>>> s.run_command('ls -la')
'total 580\ndrwxr-xr-x 23 root root...'  # Full listing
```

The model receives the full output but chooses to say "Here are the files." instead.

### Why Ollama Is Unaffected

Ollama models (GLM-4.7-Flash) follow instructions more literally, with less "personality" overlay. But this is luck, not a guarantee — any model could summarize. The fix should be architectural.

---

## Fix: `run_stream()` with `event_stream_handler` (Recommended)

### Principle

Don't rely on the model to relay tool output. Print tool results directly to the console as they arrive, then stream the model's final response separately.

```
Tool executes → print result directly to console (bypass LLM)
                              ↓
LLM sees tool result → generates commentary/next step
                              ↓
Stream LLM's final text to console token-by-token
```

### API Choice

pydantic-ai v1.52 offers three streaming APIs:

| Method | Description | Best For |
|--------|-------------|----------|
| `run_stream()` | Stream final text + optional event callback | **This use case** — simple, clean |
| `run_stream_events()` | Flat async iterator of all events | Custom UIs, logging |
| `iter()` | Full graph-node control, manual stepping | Complex agent orchestration |

**`run_stream()` with `event_stream_handler`** is the right choice because:
- Simplest API — one context manager, one async for loop
- Event handler fires for tool calls/results without interrupting the stream
- Passes `model_settings` and `message_history` through naturally
- `stream_text()` gives us token-by-token output for the final response

### Implementation Plan

#### Current code (`main.py:chat_loop`, lines 146-152):

```python
result = await agent.run(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
)
message_history = result.all_messages()
console.print(Markdown(result.output))
```

#### Target code:

```python
from collections.abc import AsyncIterable

from pydantic_ai import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    RunContext,
)

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
            # Print tool output directly — bypasses LLM summarization
            if isinstance(content, dict) and "display" in content:
                console.print(content["display"])
            else:
                console.print(content)


async def chat_loop():
    agent, model_settings = get_agent()
    deps = create_deps()
    # ...
    async with agent.run_stream(
        user_input,
        deps=deps,
        message_history=message_history,
        model_settings=model_settings,
        event_stream_handler=_print_tool_events,
    ) as run:
        # Stream the model's final text token-by-token.
        # IMPORTANT: do NOT use delta=True here — it prevents the final
        # result message from being included in all_messages(), which
        # breaks conversation memory. Instead, stream_text() without
        # delta yields accumulated text; we track what we've printed.
        printed = 0
        async for text in run.stream_text():
            new = text[printed:]
            if new:
                console.print(new, end="")
                printed = len(text)
        console.print()  # newline after streaming

    # all_messages() works correctly because we consumed stream_text()
    # without delta=True — pydantic-ai builds the complete result message.
    message_history = run.result.all_messages()
```

**`stream_text()` caveat:** `stream_text(delta=True)` is simpler to print (each yield is just the new text) but it does **not** populate `all_messages()` with the final result message — pydantic-ai skips building the complete result string. Since we need `all_messages()` for conversation memory, we must use `stream_text()` (accumulated mode) and track what we've already printed.

### What Changes

| Aspect | Before (`agent.run`) | After (`run_stream`) |
|--------|---------------------|---------------------|
| Tool output | LLM decides what to show | Printed directly via event handler |
| Final response | Rendered all at once | Streamed token-by-token |
| UX feel | "Co is thinking..." then wall of text | Progressive output as it happens |
| Gemini summarization | Breaks tool output | Irrelevant — tool output bypasses LLM |
| Ollama behavior | No change | Faster perceived response (streaming) |

### Files to Modify

| File | Change |
|------|--------|
| `co_cli/main.py` | Replace `agent.run()` with `run_stream()` + event handler |
| `co_cli/main.py` | Add `_print_tool_events()` handler function |
| `co_cli/main.py` | Update `_patch_dangling_tool_calls` — verify it works with `run_stream` message history |

### Considerations

**Conversation memory:** `run_stream` returns a `StreamedRunResult` which has `.all_messages()` just like `run`. Memory accumulation is unchanged.

**Ctrl+C handling:** `KeyboardInterrupt` during streaming needs the same dangling-tool-call patch. The `async with` block handles cleanup; we just need to catch the interrupt and patch history.

**Markdown rendering:** With `agent.run()` we render the full response as Markdown. With streaming, we get deltas. Two options:
1. Print deltas as plain text (simplest, good enough for most responses)
2. Accumulate deltas into a buffer, render full Markdown at the end (preserves formatting)

Option 1 is recommended for initial implementation. The model's commentary is usually short when tool output is already displayed.

**Structured tool output:** Tools returning `dict[str, Any]` with a `display` field — the event handler should check for this and print `result["display"]` when present, falling back to `str(result)`.

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
            # Tool execution events
            async with node.stream(run.ctx) as handle_stream:
                async for event in handle_stream:
                    if isinstance(event, FunctionToolCallEvent):
                        console.print(f"[dim]→ calling {event.part.tool_name}...[/dim]")
                    elif isinstance(event, FunctionToolResultEvent):
                        console.print(event.result.content)
        elif Agent.is_model_request_node(node):
            # Stream model's text response
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

**Rejected:** More code, same outcome. `run_stream` with event handler is strictly simpler for this use case. `iter()` is better suited for complex orchestration (e.g., injecting user approval mid-graph via `DeferredToolRequests`).

### Post-process `result.new_messages()` (No Streaming)

```python
from pydantic_ai.messages import ToolReturnPart

result = await agent.run(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
)
# Print tool results that the model already saw
for msg in result.new_messages():
    if hasattr(msg, "parts"):
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                console.print(part.content)
# Then print the model's final response
console.print(Markdown(result.output))
message_history = result.all_messages()
```

**Rejected:** Tool output only appears after the full run completes. User waits for both tool execution AND model inference before seeing anything. No streaming benefit. The model still summarizes — we just also show the raw output above it, creating duplicated/confusing output.

### `run_stream_events()` — Flat Event Iterator

```python
from pydantic_ai import AgentRunResultEvent

async for event in agent.run_stream_events(
    user_input, deps=deps, message_history=message_history,
    model_settings=model_settings,
):
    if isinstance(event, FunctionToolCallEvent):
        console.print(f"[dim]→ calling {event.part.tool_name}...[/dim]")
    elif isinstance(event, FunctionToolResultEvent):
        console.print(event.result.content)
    elif isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, TextPartDelta):
            console.print(event.delta.content_delta, end="")
    elif isinstance(event, AgentRunResultEvent):
        message_history = event.result.all_messages()
```

**Viable but less clean:** Requires manually tracking text deltas and assembling output. `run_stream` separates concerns better — events go to the handler, final text goes to `stream_text()`.

### `GoogleModelSettings` Tuning

pydantic-ai v1.52 supports `GoogleModelSettings` with Gemini-specific options (thinking budget, safety settings). While useful for other purposes, **no `GoogleModelSettings` option prevents summarization** — it's a training behavior, not a configurable parameter.

---

## References

- [pydantic-ai Agents docs — streaming APIs](https://ai.pydantic.dev/agent/)
- [pydantic-ai Google model settings](https://ai.pydantic.dev/models/google/)
- [GitHub issue #640 — Streaming tool calls](https://github.com/pydantic/pydantic-ai/issues/640)
- [GitHub issue #2356 — Stream progress from tool agent calls](https://github.com/pydantic/pydantic-ai/issues/2356)
