---
title: "06 — Conversation Memory"
parent: Infrastructure
nav_order: 3
---

# Design: Conversation Memory

## 1. What & How

Context governance and session persistence for co-cli's conversation history. Two `history_processors` are registered on the agent to prevent silent context overflow: tool output trimming and sliding-window summarisation.

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
    P1->>P1: Truncate old ToolReturnPart.content > 2000 chars
    P1->>Agt: trimmed messages

    Agt->>P2: trimmed messages (async)
    alt len(messages) > 40
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

### Processor 1 — Tool Output Trimming

**`truncate_tool_returns(messages) → list[ModelMessage]`** (sync, no I/O)

Walks older messages (all except the last 2 — the current turn) and truncates `ToolReturnPart.content` exceeding `tool_output_trim_chars` (default 2000 chars). Handles both `str` and `dict` content (JSON-serialises dicts before measuring). Preserves tool name and call ID. Threshold 0 disables.

### Processor 2 — Sliding Window with LLM Summarisation

**`truncate_history_window(ctx, messages) → list[ModelMessage]`** (async, LLM call)

Triggers when `len(messages)` exceeds `max_history_messages` (default 40). Splits history into three zones:

```
[  head  ] [ -------- dropped middle -------- ] [    tail    ]
  first      summarised via LLM → 1 marker msg    recent msgs
   run                                             (most relevant)
```

**Head boundary:** `_find_first_run_end()` — anchors on the first `ModelResponse` containing a `TextPart`. Does not assume a fixed count of 2: first run may span 4+ messages if it includes tool calls.

**Tail size:** `max(4, max_history_messages // 2)` — at least 4 messages for usable context.

**Dropped middle:** Summarised via `summarize_messages()`, injected as a valid `ModelRequest` with `UserPromptPart`. On failure, falls back to a static marker.

**Why message-count, not token-count:** Per-message token counting requires a model call or tokenizer. Message count is a reliable proxy — the tool-output trimmer caps per-message worst case.

### Summarisation Agent

**`summarize_messages(messages, model, prompt) → str`** (`co_cli/_history.py`, async)

Creates a fresh `Agent(model, output_type=str)` with zero tools registered — prevents tool execution during summarisation. The dropped messages are passed as `message_history` so the model sees them as prior conversation context.

| Callsite | Model | Rationale |
|----------|-------|-----------|
| `truncate_history_window` processor | `settings.summarization_model` or primary | Automatic — cheaper model preferred |
| `/compact` command | Primary model always | User-initiated — quality matters |

### `/compact` Command

Calls `summarize_messages()` with the primary model and builds a minimal 2-message compacted history (summary request + ack response). Does not trigger tools.

<details>
<summary>Peer landscape</summary>

| Capability | Aider | Codex | Gemini CLI | co-cli |
|-----------|-------|-------|------------|--------|
| Trigger mechanism | Token threshold | Token threshold | Token threshold (50%) | Message count |
| Tool output trimming | Omit old outputs | Truncate | Mask to disk + truncate | Char truncation |
| LLM summarisation | Yes (primary) | Yes (primary) | Yes (dedicated, 2-pass) | Yes (configurable) |
| Manual trigger | `/clear` | — | `/compress` | `/compact` |

**Adopted:** keep-recent/compress-old, LLM summarisation, tool output trimming, configurable model.
**Deferred:** token-based triggering, output offloading to disk, compression inflation guard.

</details>

### Session Persistence (Future)

Goal: resume conversations across `co chat` invocations using existing SQLite infrastructure. New `sessions` table, `ModelMessagesTypeAdapter` for serialisation, `--resume` flag, `/sessions` command. See the source doc for full schema and UX design.

## 3. Config

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Max chars per ToolReturnPart in older messages. `0` disables |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Sliding window threshold. `0` disables compaction |
| `summarization_model` | `CO_CLI_SUMMARIZATION_MODEL` | `""` (primary) | Model for auto-summarisation. Empty falls back to primary |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_history.py` | History processors (`truncate_tool_returns`, `truncate_history_window`), `summarize_messages()` |
| `co_cli/_commands.py` | `/compact` command handler |
| `co_cli/agent.py` | Registers `history_processors` on the agent |
| `co_cli/config.py` | `tool_output_trim_chars`, `max_history_messages`, `summarization_model` settings |
| `tests/test_history.py` | Tests for processors and summarisation |
