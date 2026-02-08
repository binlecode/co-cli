# Conversation Memory — Design

Context governance and session persistence for co-cli's conversation history.

Covers two concerns: **automatic context governance** (preventing silent context overflow during a session) and **session persistence** (resuming conversations across `co chat` invocations). Context governance is implemented; session persistence is future work.

Parent: `docs/DESIGN-co-cli.md` §7 (Conversation Memory).

---

## 1. Problem

`message_history` accumulates every user prompt, assistant response, tool call, and tool return across the session. Two scaling problems emerge:

1. **Tool output bloat** — file contents, search results, and command output dominate token usage. A single `search_drive_files` or `run_shell_command` return can be 10k+ chars.
2. **Unbounded growth** — without governance, history eventually exceeds the model's context window and the API call fails silently or returns degraded output.

---

## 2. Peer Landscape

### 2.1 Context Governance (Aider, Codex, Claude Code, Gemini CLI)

All four systems that ship automatic context governance converge on:

1. **Token-threshold trigger** — track cumulative input tokens, compact when approaching model limit (not on error). Aider: `max_chat_history_tokens`; Codex: `model_auto_compact_token_limit`; Gemini CLI: `model.compressionThreshold` (default 50% of 1M context).
2. **Keep recent, compress old** — recent messages are most relevant. Aider keeps `cur_messages` untouched; Codex preserves last 20k tokens of user messages; Gemini CLI preserves last 30% of history verbatim (`COMPRESSION_PRESERVE_THRESHOLD = 0.3`).
3. **LLM summarisation** — use the model (or a cheaper one) to distill dropped messages into a summary injected at the trim point. Aider, Codex, and Gemini CLI all do this. Gemini CLI uses dedicated compression model aliases (e.g. `chat-compression-2.5-flash`) and a two-pass verify approach.
4. **Tool output is the #1 bloat** — all systems truncate or omit old tool outputs before summarising. Gemini CLI has two separate mechanisms: a real-time masking service (`toolOutputMaskingService` — offloads large outputs to temp files, keeps a head+tail preview in-history) and a compression-time truncation pass with a 50k-token budget for function responses.
5. **Safety margin** — reserve a buffer below the model's `max_input_tokens` (Aider: 512, Codex: model-dependent). Gemini CLI performs a pre-flight overflow check before every turn: estimates request tokens, emits `ContextWindowWillOverflow` if it would exceed the limit.

**Gemini CLI-specific techniques not yet adopted:**

6. **Tool output offloading** — `toolOutputMaskingService` writes full tool output to disk (`~/.gemini/temp/tool-outputs/session-{id}/`) and replaces in-history content with a preview + file path. Preserves full output for user inspection without context cost. Protection window: newest 50k tokens kept verbatim, masking triggers when prunable tokens exceed 30k.
7. **Compression inflation guard** — after summarising, Gemini CLI checks whether the new history is actually shorter. If compression *increased* token count, it rolls back to the uncompressed state and sets `hasFailedCompressionAttempt` to avoid retry loops.
8. **Two-pass summary verification** — the compression agent generates a `<state_snapshot>` summary, then self-verifies in a second pass to catch omissions. Anchors on prior snapshots when available.
9. **Loop detection** — `loopDetectionService` detects repetitive tool-call patterns via content hashing and breaks infinite loops before they consume context. Orthogonal to memory governance but reduces waste.
10. **Thought stripping** — removes internal model reasoning metadata (`thoughtSignature`) from history to reduce token overhead.

### 2.2 Gap Analysis — co-cli vs. Peers

| Capability | Aider | Codex | Gemini CLI | co-cli |
|-----------|-------|-------|------------|--------|
| Trigger mechanism | Token threshold | Token threshold | Token threshold (50%) | Message count |
| Tool output trimming | Omit old outputs | Truncate | Mask to disk + truncate (50k budget) | Char truncation in-place |
| LLM summarisation | Yes (primary model) | Yes (primary) | Yes (dedicated model, 2-pass verify) | Yes (configurable model) |
| Pre-flight overflow check | Safety margin | Safety margin | Token estimate before send | No |
| Compression rollback | No | No | Yes (inflation guard) | No |
| Tool output offloading | No | No | Yes (temp files + preview) | No |
| Manual trigger | `/clear` | — | `/compress` | `/compact` |

**Adopted patterns:** token-agnostic threshold, keep-recent/compress-old, LLM summarisation, tool output trimming, configurable summarisation model.

**Deferred (post-MVP):** token-based triggering (requires per-message token counting), tool output offloading to disk, compression inflation guard, two-pass verification, pre-flight overflow check. Message-count threshold with char-level tool trimming is a sufficient proxy — see §4.3 rationale.

### 2.3 Session Persistence (OpenCode, Codex, Gemini CLI, Aider)

Systems that ship session persistence converge on:

1. **SQLite or append-only files** — OpenCode: SQLite with typed queries; Codex: JSONL rollout files; Aider: markdown append; Gemini CLI: SQLite via `chatRecordingService`. SQLite is the best fit for co-cli (already used for telemetry).
2. **Lazy session creation** — OpenCode creates the session record on the first user message, not on startup. Avoids empty session clutter.
3. **Title from first prompt** — OpenCode auto-generates titles (80-token LLM call, async background). Aider uses filename. For MVP: truncate first user prompt to 100 chars.
4. **Per-turn save** — OpenCode saves after every agent response. Codex appends to JSONL on each turn. Both avoid data loss on crash.
5. **Token/cost tracking on session** — OpenCode tracks `prompt_tokens`, `completion_tokens`, and accumulated cost per session.
6. **Session management UX** — OpenCode: `Ctrl+A` session switcher, export/import. Codex: `resume <id>`, `fork <id>`, archive. Gemini CLI: `ResumedSessionData` with compression event logging.

---

## 3. pydantic-ai API Surface

Relevant APIs from pydantic-ai v1.52.0:

| API | Purpose |
|-----|---------|
| `Agent(history_processors=[...])` | Chained callables, run before each model request, replace history in-place |
| Processor signature | `(RunContext[DepsT], list[ModelMessage]) -> list[ModelMessage]` (sync or async, `RunContext` optional — auto-detected) |
| `RunContext.usage.input_tokens` | Cumulative token count across runs |
| `ModelResponse.usage: RequestUsage` | Per-response token counts on each message |
| `ModelMessagesTypeAdapter` + `to_json()` / `validate_json()` | Serialise/deserialise message history for persistence |
| `UsageLimits(count_tokens_before_request=True)` | Pre-send token counting (Anthropic, Google, Bedrock) |

---

## 4. Context Governance — Architecture

### 4.1 Overview

Two `history_processors` are registered on the agent, chained in order:

```python
# co_cli/agent.py — get_agent()
agent = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    retries=settings.tool_retries,
    output_type=[str, DeferredToolRequests],
    history_processors=[truncate_tool_returns, truncate_history_window],
)
```

All processor logic lives in `co_cli/_history.py` (internal helper, underscore-prefixed per convention).

### 4.2 Processor 1 — Tool Output Trimming

**Function:** `truncate_tool_returns(messages) -> list[ModelMessage]` (sync, no I/O)

Walks older messages (all except the last 2 — the current turn) and truncates `ToolReturnPart.content` exceeding `tool_output_trim_chars` (default 2000 chars).

**Design details:**

- **`content` is `str | dict`** — tools return `dict[str, Any]` with a `display` field (per tool return convention). The processor JSON-serialises `dict` content before measuring length. Truncated form becomes a plain `str` with a `[…truncated, N chars total]` marker.
- **Preserves tool name and call ID** — the model can still reference the tool call by ID even after truncation.
- **Last-exchange protection** — the trailing 2 messages (current `ModelRequest` + `ModelResponse` pair) are never trimmed, so the model can reason about the latest tool results.
- **Threshold 0 disables** — setting `tool_output_trim_chars=0` skips all trimming.

```
messages[0..boundary-1]  →  scan and truncate ToolReturnPart.content
messages[boundary..]     →  protected (last 2 = current turn)
```

### 4.3 Processor 2 — Sliding Window with LLM Summarisation

**Function:** `truncate_history_window(ctx: RunContext[CoDeps], messages) -> list[ModelMessage]` (async, LLM call)

Triggers when `len(messages)` exceeds `max_history_messages` (default 40). Splits history into three zones:

```
[  head  ] [ -------- dropped middle -------- ] [    tail    ]
  first      summarised via LLM → 1 marker msg    recent msgs
   run                                             (most relevant)
```

**Head boundary — `_find_first_run_end()`:**

Anchors on the first `ModelResponse` containing a `TextPart` (the first run's final answer). Does not assume a fixed count of 2: if the first run includes tool calls, the first exchange may span 4+ messages (`request → response with tool calls → tool return request → final response`).

If no `TextPart` response exists (edge case — session with only tool calls), returns 0.

**Tail size:**

`max(4, max_history_messages // 2)` — at least 4 messages for usable context, otherwise half the budget.

**Dropped middle → summary:**

1. Extract `messages[head_end:tail_start]`
2. Call `summarize_messages(dropped, model)` with the configured summarisation model (or primary model as fallback)
3. Wrap the summary in a `ModelRequest` with `UserPromptPart`: `"[Summary of N earlier messages]\n{summary_text}"`
4. On failure (network error, rate limit, timeout), fall back to a static marker: `"[Earlier conversation trimmed — N messages removed to stay within context budget]"`

The splice-point message is always a structurally valid `ModelRequest` — bare strings would be rejected by the model.

**Why message-count, not token-count:**

Token estimation from `ModelResponse.usage` is cumulative across runs, not per-message. Accurate per-message token counting requires a model call (`count_tokens`) or a tokenizer. Message count is a reliable proxy — the tool-output trimmer in §4.2 caps the per-message worst case, so message count becomes a reasonable budget.

### 4.4 Summarisation Agent

**Function:** `summarize_messages(messages, model, prompt) -> str` (`co_cli/_history.py`, async)

```python
async def summarize_messages(
    messages: list[ModelMessage],
    model: str | Model,
    prompt: str = _SUMMARIZE_PROMPT,
) -> str:
```

**Disposable agent design:** Each call creates a fresh `Agent(model, output_type=str)` with zero tools registered. This is intentional — a summarisation call must never trigger tool execution (no shell commands, no API calls). The agent is garbage-collected after the call returns.

```python
summariser: Agent[None, str] = Agent(
    model,
    output_type=str,
    system_prompt="You are a conversation summariser. Return only the summary.",
)
result = await summariser.run(prompt, message_history=messages)
return result.output
```

**How it works:** The dropped messages are passed as `message_history`, so the model sees them as prior conversation context. The `prompt` is the user-turn that asks for the summary. The model reads the history and produces a condensed version as its `str` output.

**System prompt:**

```
You are a conversation summariser. Return only the summary.
```

Deliberately minimal — prevents the model from adding preamble, caveats, or asking follow-up questions.

**User prompt (default `_SUMMARIZE_PROMPT`):**

```
Summarize the following conversation in a concise form that preserves:
- Key decisions and outcomes
- File paths and tool names referenced
- Error resolutions and workarounds
- Any pending tasks or next steps

Be brief — this summary replaces the original messages to save context space.
```

The prompt is a parameter so callers can override it — `/compact` could use a different prompt in future (e.g. more detailed for user-initiated compaction).

**Model resolution by callsite:**

| Callsite | Model | Rationale |
|----------|-------|-----------|
| `truncate_history_window` processor | `settings.summarization_model` if set, else `ctx.model` (primary) | Automatic — cheaper/faster model preferred to minimise latency on every turn |
| `/compact` command | `ctx.agent.model` (always primary) | User-initiated — quality matters more than speed |

**Error handling:** `summarize_messages` raises on failure (network error, rate limit, model error). Callers handle fallback:
- `truncate_history_window` catches all exceptions and falls back to `_static_marker(dropped_count)`
- `/compact` catches all exceptions and prints the error, returns `None` (history unchanged)

### 4.5 `/compact` Command Integration

The `/compact` slash command (`_commands.py:_cmd_compact`) was refactored to use `summarize_messages()` instead of the previous `agent.run()` approach. The old implementation could trigger tools and didn't truly compact (it returned the full history plus a summary turn appended).

**New behaviour:**

1. Call `summarize_messages(message_history, agent.model)`
2. Build a minimal 2-message compacted history:
   - `ModelRequest` with `UserPromptPart`: `"[Compacted conversation summary]\n{summary}"`
   - `ModelResponse` with `TextPart`: `"Understood. I have the conversation context."`
3. Return the 2-message list as the new history

This produces a clean, minimal context that the model can build on without tool-call artifacts.

---

## 5. Configuration

### 5.1 Settings

All fields live in `co_cli/config.py:Settings` and `settings.reference.json`. Standard config precedence applies: env vars > project `.co-cli/settings.json` > user `~/.config/co-cli/settings.json` > built-in defaults.

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `tool_output_trim_chars` | `CO_CLI_TOOL_OUTPUT_TRIM_CHARS` | `2000` | Max chars per `ToolReturnPart` in older messages. Set to `0` to disable trimming entirely |
| `max_history_messages` | `CO_CLI_MAX_HISTORY_MESSAGES` | `40` | Sliding window threshold (message count). Set to `0` to disable automatic compaction |
| `summarization_model` | `CO_CLI_SUMMARIZATION_MODEL` | `""` | Model for auto-summarisation in `truncate_history_window`. Empty string falls back to primary model (`ctx.model`) |

### 5.2 Model Resolution

The `summarization_model` setting accepts a pydantic-ai model string (e.g. `"google-gla:gemini-2.0-flash-lite"`). When empty (the default), each callsite resolves the model differently:

| Callsite | Model used | Rationale |
|----------|-----------|-----------|
| `truncate_history_window` processor | `settings.summarization_model` if set, else `ctx.model` (primary) | Automatic — cheaper/faster model preferred to minimise latency on every turn |
| `/compact` command | `ctx.agent.model` (always primary) | User-initiated — quality matters more than speed |

The primary model is resolved from `llm_provider` + `gemini_model` / `ollama_model` in `co_cli/agent.py:get_agent()`. The `summarization_model` bypasses that chain entirely — it's a direct pydantic-ai model string passed to `Agent(model)`.

### 5.3 Derived Constants

These values are hardcoded in `co_cli/_history.py` and not user-configurable:

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `safe_tail` | `2` | `truncate_tool_returns` | Number of trailing messages protected from trimming (current turn) |
| `tail_count` | `max(4, max_history_messages // 2)` | `truncate_history_window` | Messages preserved at the end of the window. At least 4 for usable context, otherwise half the budget |
| Summariser system prompt | `"You are a conversation summariser. Return only the summary."` | `summarize_messages` | Deliberately minimal — prevents preamble, caveats, or follow-up questions |

### 5.4 Disable Semantics

| Setting | Disable value | Effect |
|---------|--------------|--------|
| `tool_output_trim_chars` | `0` | `truncate_tool_returns` returns messages unchanged — no truncation |
| `max_history_messages` | `0` | `truncate_history_window` returns messages unchanged — no compaction, no LLM call |
| `summarization_model` | `""` (default) | Not disabled — falls back to primary model. There is no way to disable summarisation independently of `max_history_messages` |

---

## 6. Processing Flow

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
    LLM->>Agt: Response
    Agt->>Loop: result
```

---

## 7. Session Persistence — Future Design

Goal: resume conversations across `co chat` invocations using existing SQLite infrastructure.

### 7.1 Schema

New `sessions` table in `co-cli.db` (alongside `spans`):

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- UUID
    created_at INTEGER NOT NULL,   -- epoch seconds
    updated_at INTEGER NOT NULL,   -- epoch seconds
    title TEXT,                    -- first user prompt (truncated to 100 chars)
    message_count INTEGER NOT NULL DEFAULT 0,  -- denormalised for fast listing
    messages TEXT NOT NULL          -- JSON via ModelMessagesTypeAdapter
);
CREATE INDEX idx_sessions_updated ON sessions (updated_at DESC);
```

### 7.2 Serialisation

```python
from pydantic_core import to_json
from pydantic_ai import ModelMessagesTypeAdapter

# Save
json_bytes = to_json(message_history)
# Load
message_history = ModelMessagesTypeAdapter.validate_json(json_bytes)
```

### 7.3 UX

- `co chat` — starts a new session (current behaviour, session created lazily on first message)
- `co chat --resume` — resumes most recent session
- `co chat --resume <id>` — resumes specific session
- `/sessions` command — lists recent sessions with title, message count, and age
- `/delete <id>` command — delete a session

### 7.4 Auto-Save Strategy

Save to SQLite after every 5 turns and on exit (SIGINT, `/clear`, EOF). Debouncing avoids excessive writes while crash safety is acceptable — at most 4 turns lost. SQLite writes are cheap with WAL mode (already enabled for telemetry).

### 7.5 Remaining Work

- [ ] Add `sessions` table to `telemetry.py` schema init
- [ ] Implement save/load in `co_cli/_history.py` using `ModelMessagesTypeAdapter`
- [ ] Lazy session creation on first user message (not on startup)
- [ ] Auto-save session (debounced — every 5 turns + on exit)
- [ ] Add `--resume` flag to `co chat` CLI command
- [ ] Add `/sessions` slash command (title, message count, age)
- [ ] Add `/delete` slash command for session cleanup
- [ ] Add functional tests (save, resume, list, delete)
- [ ] Update `DESIGN-co-cli.md` §7.3 to reflect persistence

---

## 8. Testing

Tests live in `tests/test_history.py`.

**Pure tests (no LLM required):**
- `_find_first_run_end` — simple, with tool calls, no text response
- `_static_marker` — valid `ModelRequest` structure
- `truncate_tool_returns` — short content unchanged, long string truncated, dict content truncated, last-exchange protection, threshold-0 disables

**LLM tests (require running provider):**
- `summarize_messages` — returns non-empty summary string
- `truncate_history_window` with threshold exceeded — compacts history, preserves head and tail, inserts summary marker
