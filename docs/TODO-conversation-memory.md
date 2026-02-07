# TODO: Conversation Memory Improvements

Currently `message_history` is unbounded and in-process only. See `docs/DESIGN-co-cli.md` §7 for architecture.

## Peer Landscape (Aider, Codex, Claude Code)

All three systems that ship automatic context governance converge on:

1. **Token-threshold trigger** — track cumulative input tokens, compact when approaching model limit (not on error). Aider: `max_chat_history_tokens`; Codex: `model_auto_compact_token_limit`.
2. **Keep recent, compress old** — recent messages are most relevant. Aider keeps `cur_messages` untouched; Codex preserves last 20k tokens of user messages.
3. **LLM summarization** — use the model (or a cheaper one) to distill dropped messages into a summary injected at the trim point. Both Aider and Codex do this.
4. **Tool output is the #1 bloat** — file contents, search results, and command output dominate token usage. All systems truncate or omit old tool outputs before summarizing.
5. **Safety margin** — reserve a buffer below the model's `max_input_tokens` (Aider: 512, Codex: model-dependent).

**pydantic-ai v1.52.0 API available for this:**
- `Agent(history_processors=[...])` — chained callables, run before each model request, replace history in-place.
- Processor signature: `(RunContext[DepsT], list[ModelMessage]) -> list[ModelMessage]` (sync or async, RunContext optional).
- `RunContext.usage.input_tokens` — cumulative token count across runs.
- `ModelResponse.usage: RequestUsage` — per-response token counts on each message.
- `ModelMessagesTypeAdapter` + `to_json()` / `validate_json()` — serialize/deserialize for persistence.
- `UsageLimits(count_tokens_before_request=True)` — pre-send token counting (Anthropic, Google, Bedrock).

---

## Phase 1 — Automatic Context Governance (MVP)

Goal: prevent silent context overflow without extra LLM calls. Ship the cheapest thing that works.

### 1a. Tool output trimming processor

Register as first `history_processor`. Walks older messages (all except last exchange), finds `ToolReturnPart` with `content` exceeding a threshold (e.g. 2000 chars), and replaces with a truncated version + `[…truncated, N chars]` marker.

- Cheap — string truncation, no LLM call
- Preserves tool name and call ID (model can still reference it)
- Only trims messages older than the current turn (recent tool output stays intact)
- Threshold configurable via `settings.tool_output_trim_chars` (default 2000)

### 1b. Message-count sliding window processor

Register as second `history_processor`. When `len(messages)` exceeds a threshold:

- Keep **first 2 messages** (initial user prompt + assistant response — establishes session context)
- Keep **last N messages** (recent conversation — most relevant)
- Drop the middle
- Insert a synthetic `ModelRequest` at the splice point: `"[Earlier conversation trimmed — {dropped} messages removed to stay within context budget]"`
- Threshold configurable via `settings.max_history_messages` (default 40)

**Why message-count, not token-count for MVP:** Token estimation from `ModelResponse.usage` is cumulative across runs, not per-message. Accurate per-message token counting requires a model call (`count_tokens`) or a tokenizer. Message count is a reliable proxy — the tool-output trimmer in 1a caps the per-message worst case, so message count becomes a reasonable budget.

### Integration point

```python
# co_cli/agent.py — get_agent()
agent = Agent(
    model,
    deps_type=CoDeps,
    system_prompt=system_prompt,
    retries=settings.tool_retries,
    output_type=[str, DeferredToolRequests],
    history_processors=[trim_old_tool_output, sliding_window],
)
```

### New file

`co_cli/_history.py` — both processors live here (internal helper, underscore-prefixed per convention).

### Config additions

```python
# co_cli/config.py — Settings
tool_output_trim_chars: int = 2000    # max chars per ToolReturnPart in older messages
max_history_messages: int = 40        # sliding window threshold
```

### Items

- [ ] Implement `trim_old_tool_output` processor in `co_cli/_history.py`
- [ ] Implement `sliding_window` processor in `co_cli/_history.py`
- [ ] Add `tool_output_trim_chars` and `max_history_messages` to `Settings`
- [ ] Register both processors on agent in `get_agent()`
- [ ] Add functional tests (long conversation with large tool outputs → verify trimming)
- [ ] Update `DESIGN-co-cli.md` §7.4 to reflect new processors

---

## Phase 2 — LLM Summarization (post-MVP)

Goal: replace the blunt "drop middle" strategy with an LLM-generated summary of dropped messages. Matches Aider/Codex best practice.

When the sliding window triggers and drops messages, instead of a static marker:

1. Collect the dropped messages
2. Call a cheap/fast model (e.g. `gemini-2.0-flash-lite`) with a summarization prompt
3. Inject the summary as the splice-point message

Design considerations:
- Summarization model should be configurable (may differ from primary model)
- Async — should not block the UI noticeably
- Summary should preserve: file paths, tool names, key decisions, error resolutions
- `/compact` command can reuse the same summarization logic (deduplicate with `_cmd_compact`)

### Items

- [ ] Implement `summarize_dropped_messages()` in `co_cli/_history.py`
- [ ] Make summarization model configurable (`settings.summarization_model`)
- [ ] Refactor `/compact` to use shared summarization logic
- [ ] Add functional test (verify summary preserves key context)

---

## Phase 3 — Session Persistence (post-MVP)

Goal: resume conversations across `co chat` invocations. Use existing SQLite infrastructure.

### Schema

New `sessions` table in `co-cli.db` (alongside `spans`):

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- UUID
    created_at INTEGER NOT NULL,   -- epoch seconds
    updated_at INTEGER NOT NULL,   -- epoch seconds
    title TEXT,                    -- first user prompt (truncated), for display
    messages TEXT NOT NULL          -- JSON via ModelMessagesTypeAdapter
);
CREATE INDEX idx_sessions_updated ON sessions (updated_at DESC);
```

### Serialization

```python
from pydantic_core import to_json
from pydantic_ai import ModelMessagesTypeAdapter

# Save
json_bytes = to_json(message_history)
# Load
message_history = ModelMessagesTypeAdapter.validate_json(json_bytes)
```

### UX

- `co chat` — starts a new session (current behavior)
- `co chat --resume` — resumes most recent session
- `co chat --resume <id>` — resumes specific session
- `/sessions` command — lists recent sessions with title and message count

### Items

- [ ] Add `sessions` table to `telemetry.py` schema init
- [ ] Implement save/load in `co_cli/_history.py` using `ModelMessagesTypeAdapter`
- [ ] Auto-save session on each turn (debounced — every 5 turns or on exit)
- [ ] Add `--resume` flag to `co chat` CLI command
- [ ] Add `/sessions` slash command
- [ ] Add functional tests (save, resume, list)
- [ ] Update `DESIGN-co-cli.md` §7.3 to reflect persistence
