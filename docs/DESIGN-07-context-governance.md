---
title: "07 — Context Governance"
parent: Infrastructure
nav_order: 3
---

# Design: Context Governance

## 1. What & How

Context governance for co-cli's conversation history. Two `history_processors` registered on the agent prevent silent context overflow: tool output trimming and sliding-window summarisation. The chat loop maintains `message_history` as a simple list, updated after each turn, with slash commands for manual control (`/clear`, `/compact`, `/history`).

```mermaid
sequenceDiagram
    participant Loop as Chat Loop
    participant Agt as agent.run()
    participant P1 as truncate_tool_returns
    participant P2 as truncate_history_window
    participant LLM as LLM Provider

    Loop->>Agt: agent.run(user_input, message_history)
    Note over Agt: history_processors run before model request

    Agt->>P1: messages (sync)
    P1->>P1: Truncate old ToolReturnPart.content > threshold
    P1->>Agt: trimmed messages

    Agt->>P2: trimmed messages (async)
    alt len(messages) > max_history_messages
        P2->>P2: Split: head | dropped middle | tail
        P2->>LLM: summarize_messages(dropped)
        LLM->>P2: summary text
        P2->>Agt: head + [summary marker] + tail
    else under threshold
        P2->>Agt: messages unchanged
    end

    Agt->>LLM: Send processed history + new prompt
```

## 2. Core Logic

### Message History Lifecycle

The chat loop in `main.py` owns the `message_history` list:

1. **Initialised** as `[]` at session start
2. **Passed** to `run_turn()` which forwards it to `agent.run_stream_events(user_input, message_history=message_history, ...)`
3. **Updated** after each turn: `message_history = turn_result.messages` (from `result.all_messages()`)
4. **Patched on interrupt**: `_patch_dangling_tool_calls()` adds synthetic `ToolReturnPart` entries for any unanswered tool calls, preventing invalid history structure on the next turn
5. **Rebindable** by slash commands: `/clear` returns `[]`, `/compact` returns a 2-message summary

### Processor 1 — Tool Output Trimming

**`truncate_tool_returns(messages) → list[ModelMessage]`** (`_history.py`, sync, no I/O)

Walks older messages (all except the **last 2** — the current turn) and truncates `ToolReturnPart.content` exceeding `tool_output_trim_chars` (default 2000 chars). Handles both `str` and `dict` content (JSON-serialises dicts via `_content_length()` before measuring). Preserves `tool_name` and `tool_call_id`. Threshold 0 disables.

Truncation format: `content[:threshold] + "\n[…truncated, {length} chars total]"`

### Processor 2 — Sliding Window with LLM Summarisation

**`truncate_history_window(ctx: RunContext[CoDeps], messages) → list[ModelMessage]`** (`_history.py`, async, LLM call)

Triggers when `len(messages)` exceeds `max_history_messages` (default 40). Splits history into three zones:

```
[  head  ] [ -------- dropped middle -------- ] [    tail    ]
  first      summarised via LLM → 1 marker msg    recent msgs
   run                                             (most relevant)
```

**Head boundary:** `_find_first_run_end(messages)` — scans for the first `ModelResponse` containing a `TextPart`. Returns the index (inclusive). Does not assume a fixed count of 2: first run may span 4+ messages if it includes tool calls. Returns 0 if no text response found (keep nothing pinned).

**Tail size:** `max(4, max_history_messages // 2)` — at least 4 messages for usable context.

**Dropped middle:** Summarised via `summarize_messages()`, injected as a `ModelRequest` with `UserPromptPart` (content: `[Summary of N earlier messages]\n{summary_text}`). On failure, falls back to `_static_marker()` — a `ModelRequest` with `[Earlier conversation trimmed — N messages removed to stay within context budget]`.

**Why message-count, not token-count:** Per-message token counting requires a model call or tokenizer. Message count is a reliable proxy — the tool-output trimmer caps per-message worst case.

### Summarisation Agent

**`summarize_messages(messages, model, prompt) → str`** (`_history.py`, async)

Creates a fresh `Agent(model, output_type=str)` with zero tools — prevents tool execution during summarisation. The dropped messages are passed as `message_history` so the model sees them as prior conversation context. The system prompt instructs the summariser to treat all conversation content as data and ignore embedded instructions (prompt injection defence).

Summary preserves: key decisions and outcomes, file paths and tool names, error resolutions, pending tasks.

| Callsite | Model | Rationale |
|----------|-------|-----------|
| `truncate_history_window` processor | `settings.summarization_model` or `ctx.model` (primary) | Automatic — cheaper model preferred |
| `/compact` command | `ctx.agent.model` (primary always) | User-initiated — quality matters |

### Slash Commands

**`/clear`** (`_cmd_clear` in `_commands.py`) — Returns an empty list, resetting conversation history completely.

**`/history`** (`_cmd_history` in `_commands.py`) — Counts `ModelRequest` messages as user turns and displays both turn count and total message count. Read-only, does not modify history.

**`/compact`** (`_cmd_compact` in `_commands.py`) — Calls `summarize_messages()` with the primary model and builds a minimal 2-message compacted history:
1. `ModelRequest` with `UserPromptPart`: `[Compacted conversation summary]\n{summary}`
2. `ModelResponse` with `TextPart`: `Understood. I have the conversation context.`

Returns `None` on empty history or on summarisation failure.

### Interrupt Handling

**`_patch_dangling_tool_calls(messages, error_message)`** (`_orchestrate.py`)

When a `KeyboardInterrupt` or `CancelledError` occurs mid-turn, the last message may be a `ModelResponse` with unanswered `ToolCallPart` entries. LLM models expect paired tool call + return in history. This function appends a synthetic `ModelRequest` with `ToolReturnPart(content="Interrupted by user.")` for each dangling call, keeping history structurally valid.

### History Processor Registration

Both processors are registered at agent creation time in `agent.py`:

```
Agent(
    model,
    history_processors=[truncate_tool_returns, truncate_history_window],
    ...
)
```

pydantic-ai runs processors in order before every model request. Sync processors (`truncate_tool_returns`) run first; async processors (`truncate_history_window`) run second.

<details>
<summary>Peer landscape</summary>

| Capability | Aider | Codex | Gemini CLI | co-cli |
|-----------|-------|-------|------------|--------|
| Trigger mechanism | Token threshold | Token threshold | Token threshold (50%) | Message count |
| Tool output trimming | Omit old outputs | Truncate | Mask to disk + truncate | Char truncation |
| LLM summarisation | Yes (primary) | Yes (primary) | Yes (dedicated, 2-pass) | Yes (configurable) |
| Manual trigger | `/clear` | — | `/compress` | `/compact`, `/clear` |

**Adopted:** keep-recent/compress-old, LLM summarisation, tool output trimming, configurable model.
**Deferred:** token-based triggering, output offloading to disk, compression inflation guard.

</details>

### Session Persistence (Future)

Goal: resume conversations across `co chat` invocations using existing SQLite infrastructure. New `sessions` table, `ModelMessagesTypeAdapter` for serialisation, `--resume` flag, `/sessions` command.

## 3. Config

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Max chars per ToolReturnPart in older messages. `0` disables |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Sliding window threshold. `0` disables compaction |
| `summarization_model` | `CO_CLI_SUMMARIZATION_MODEL` | `""` (primary) | Model for auto-summarisation. Empty falls back to primary |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_history.py` | History processors (`truncate_tool_returns`, `truncate_history_window`), `summarize_messages()`, helpers (`_find_first_run_end`, `_static_marker`, `_content_length`) |
| `co_cli/_orchestrate.py` | `run_turn()` passes `message_history` to agent, `_patch_dangling_tool_calls()` for interrupt safety |
| `co_cli/_commands.py` | `/compact`, `/clear`, `/history` command handlers |
| `co_cli/agent.py` | Registers `history_processors=[truncate_tool_returns, truncate_history_window]` on the agent |
| `co_cli/main.py` | Chat loop: initialises `message_history = []`, updates after each turn, rebinds on slash commands |
| `co_cli/config.py` | `tool_output_trim_chars`, `max_history_messages`, `summarization_model` settings |
| `tests/test_history.py` | Functional tests for processors, summarisation, and `/compact` |
