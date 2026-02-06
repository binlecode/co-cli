# Design: `co tail` — Real-Time Span Tail Viewer

**Status:** Implemented
**Last Updated:** 2026-02-06

## Overview

`co tail` is a real-time terminal viewer that polls the OTel SQLite database and prints completed spans as they arrive — like `tail -f` for agent traces. Run `co chat` in one terminal, `co tail` in another, and watch the agent→model→tool flow live.

```
┌─────────────────────────┐       ┌─────────────────────────┐
│  Terminal A              │       │  Terminal B              │
│                          │       │                          │
│  $ co chat               │       │  $ co tail -v            │
│  Co > search my notes    │       │                          │
│  Co is thinking...       │  ───▶ │  14:23:05  model  chat   │
│                          │       │     │ Let me search...   │
│                          │       │  14:23:06  tool   search │
│                          │       │  14:23:07  model  chat   │
│  Found 3 notes...        │       │     │ I found 3 notes.  │
└─────────────────────────┘       └─────────────────────────┘
                                        ▲
                                        │ polls SQLite
                                        │
                             ~/.local/share/co-cli/co-cli.db
```

### Relationship to Other Viewers

| Command | Mode | Best For |
|---------|------|----------|
| `co logs` | Datasette (browser) | SQL queries, post-hoc deep dives |
| `co traces` | Static HTML (browser) | Visual span tree, waterfall timing |
| **`co tail`** | **Terminal (live)** | **Real-time monitoring, troubleshooting** |

---

## Architecture

### Polling Mechanism

```
run_tail()
    │
    ├─▶ _query_recent(limit=20)          # Startup: show last N spans
    │       SELECT * FROM spans
    │       ORDER BY start_time DESC
    │       LIMIT 20
    │       │
    │       └─▶ high_water_mark = max(start_time)
    │
    └─▶ loop:                             # Follow mode
            time.sleep(poll_interval)
            │
            ├─▶ _query_new(high_water)
            │       SELECT * FROM spans
            │       WHERE start_time > ?
            │       ORDER BY start_time ASC
            │
            └─▶ print + update high_water_mark
```

- Default poll interval: **1 second** (`--poll` flag)
- On startup, show the N most recent spans (default 20 via `--last`), then follow
- Synchronous `time.sleep()` loop — no async needed
- Single SQLite connection, `row_factory = sqlite3.Row`

### Output Format

One line per completed span, with optional verbose content below model spans:

```
14:23:05  model  chat glm-4.7-flash:q8_0       in=3745 out=25  5.26s
           │ [thinking] Let me search the notes for that topic.
14:23:06  tool   execute_tool search_notes      tool=search_notes  args={"query":"test"}  120ms
14:23:07  model  chat glm-4.7-flash:q8_0       in=4084 out=37  976ms
           │ I found 3 matching notes...
14:23:08  agent  invoke_agent agent             model=glm-4.7-flash:q8_0  tokens=78776→4502  255.72s
```

| Column | Source | Format |
|--------|--------|--------|
| Timestamp | `start_time` (ns epoch) | Local `HH:MM:SS` |
| Type tag | `get_span_type(name)` | Left-padded 6 chars |
| Span name | `spans.name` | Left-padded 30 chars |
| Key attrs | JSON `attributes` column | Type-specific extraction |
| Duration | `duration_ms` | Via `format_duration()` |
| Status | `status_code` | Only shown if `ERROR` (red) |
| Verbose | `gen_ai.output.messages` | Indented `│` lines (model spans only, `-v` flag) |

### Color Scheme

Matches `trace_viewer.py` for consistency across all three viewers:

| Type | Rich Style | Hex (HTML viewer) |
|------|------------|-------------------|
| agent | `cyan` | `#00d4ff` |
| model | `magenta` | `#9b59b6` |
| tool | `yellow` | `#f39c12` |
| error | `bold red` | `#e74c3c` |

### Reused Code

| Import | Source | Purpose |
|--------|--------|---------|
| `get_span_type()` | `co_cli/trace_viewer.py` | Classify span as agent/model/tool |
| `format_duration()` | `co_cli/trace_viewer.py` | Human-readable ms/s formatting |
| `DATA_DIR` | `co_cli/config.py` | Database path resolution |
| `Console`, `Text` | `rich` | Colorized terminal output |

---

## CLI Interface

```bash
co tail [OPTIONS]
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--trace` | `-t` | None | Filter to a specific trace ID |
| `--tools-only` | | `False` | Only show tool spans |
| `--models-only` | | `False` | Only show model/chat spans |
| `--poll` | `-p` | `1.0` | Poll interval in seconds |
| `--no-follow` | `-n` | `False` | Print recent spans and exit (no polling) |
| `--last` | `-l` | `20` | Number of recent spans to show on startup |
| `--verbose` | `-v` | `False` | Show LLM output content below model spans |

### Examples

```bash
# Live follow all spans
co tail

# Quick look at recent activity
co tail -n -l 10

# Watch only LLM calls with full output
co tail --models-only -v

# Watch only tool executions
co tail --tools-only

# Debug a specific trace
co tail -t abc123def456... -v

# Faster polling for near-real-time
co tail -p 0.5 -v
```

---

## Span Attribute Reference

These are the actual OTel attributes emitted by pydantic-ai (`InstrumentationSettings(version=3)`) and stored in the `attributes` JSON column. This is the definitive reference for what data is available when troubleshooting.

### `invoke_agent agent` — Agent Run Span

The root span for each `agent.run()` call. Contains the full conversation and aggregate token usage.

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `model_name` | string | `"glm-4.7-flash:q8_0"` | Model used for this run |
| `agent_name` | string | `"agent"` | Agent name |
| `gen_ai.agent.name` | string | `"agent"` | OTel GenAI spec agent name |
| `gen_ai.usage.input_tokens` | int | `78776` | **Total** input tokens across all model calls in this run |
| `gen_ai.usage.output_tokens` | int | `4502` | **Total** output tokens across all model calls in this run |
| `pydantic_ai.all_messages` | JSON array | `[{"role":"system",...},...]` | Full conversation history (system prompt + all turns) |
| `pydantic_ai.new_message_index` | int | `86` | Index into `all_messages` where new messages from this run start |

**Troubleshooting use:** Check `all_messages` for prompt drift, token bloat (growing context), or unexpected conversation state. Compare `input_tokens` across runs to spot context growth.

### `chat {model}` — Model/LLM Call Span

One span per LLM API call. An agent run may have multiple chat spans (tool loops).

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `gen_ai.operation.name` | string | `"chat"` | Always `"chat"` |
| `gen_ai.request.model` | string | `"glm-4.7-flash:q8_0"` | Requested model |
| `gen_ai.response.model` | string | `"glm-4.7-flash:q8_0"` | Actual model used (may differ) |
| `gen_ai.provider.name` | string | `"openai"` | Provider (Ollama uses OpenAI-compat API) |
| `gen_ai.system` | string | `"openai"` | System identifier |
| `server.address` | string | `"localhost"` | API server address |
| `server.port` | int | `11434` | API server port |
| `gen_ai.request.temperature` | float | `1.0` | Temperature setting |
| `gen_ai.request.top_p` | float | `0.95` | Top-p setting |
| `gen_ai.request.max_tokens` | int | `16384` | Max output tokens |
| `gen_ai.usage.input_tokens` | int | `3745` | Input tokens for **this** call |
| `gen_ai.usage.output_tokens` | int | `25` | Output tokens for **this** call |
| `gen_ai.response.id` | string | `"chatcmpl-808"` | Provider response ID |
| `gen_ai.response.finish_reasons` | list | `['tool_call']` | Why the model stopped: `stop`, `tool_call`, `length` |
| `gen_ai.input.messages` | JSON array | `[{"role":"system",...}]` | Full input to the model (system + user + history) |
| `gen_ai.output.messages` | JSON array | `[{"role":"assistant",...}]` | Model response (text, thinking, tool_calls) |
| `gen_ai.tool.definitions` | JSON array | `[{"type":"function","name":"run_shell_command",...}]` | Tool schemas sent to the model |
| `model_request_parameters` | JSON | `{"function_tools":[...]}` | pydantic-ai internal request params |

**Output message parts** (`gen_ai.output.messages[].parts[]`):

| Part Type | Fields | Example |
|-----------|--------|---------|
| `text` | `content` | `"Here are the results..."` |
| `thinking` | `content` | `"Let me search for that."` |
| `tool_call` | `id`, `name`, `arguments` | `{"id":"call_abc","name":"search_notes","arguments":{"query":"test"}}` |

**Troubleshooting use:** Check `finish_reasons` for unexpected `length` (output truncated). Compare `gen_ai.input.messages` across calls to see how context grows in a tool loop. Check `gen_ai.output.messages` for malformed tool calls or hallucinated responses.

### `execute_tool {name}` — Tool Execution Span

One span per tool invocation. The tool function has already been called by the time this span completes.

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `gen_ai.tool.name` | string | `"run_shell_command"` | Tool function name |
| `gen_ai.tool.call.id` | string | `"call_yeqrqw57"` | Unique call ID (matches model's tool_call.id) |
| `gen_ai.tool.call.arguments` | JSON string | `{"cmd":"python3 greeting_bot.py"}` | Arguments passed to the tool |
| `gen_ai.tool.call.result` | string | _(tool output)_ | Tool return value (may be truncated in some versions) |

**Note:** Older spans (pre-version-3) use `tool_arguments` and `tool_response` instead.

**Troubleshooting use:** Check `gen_ai.tool.call.arguments` for malformed args from the LLM. Check `gen_ai.tool.call.result` for error responses. Match `gen_ai.tool.call.id` across tool and model spans to correlate.

### `running tools` — Tool Batch Span

Parent span that groups tool executions when the model requests multiple tools in one turn.

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `tools` | list | `['run_shell_command']` | Tool names in this batch |

---

## Troubleshooting Guide

### Quick Diagnostics

```bash
# "What just happened?" — last 10 spans
co tail -n -l 10

# "Why is it slow?" — watch model call durations
co tail --models-only

# "What did the LLM say?" — full output
co tail --models-only -v

# "What tools are being called?" — tool loop check
co tail --tools-only

# "Debug this specific conversation"
co tail -t <trace_id> -v
```

### Common Issues and What to Look For

#### 1. Agent stuck in a tool loop

**Symptom:** Agent keeps calling tools without producing a final answer.

**Diagnose with:**
```bash
co tail -v
```

**What to look for:**
- Repeating pattern: `chat → tool → chat → tool → ...` without a final `chat` ending in `stop`
- Check `gen_ai.response.finish_reasons` — stuck loops show endless `tool_call` finish reasons
- In verbose mode, read the model's thinking to see if it's confused about when to stop

**SQL deep dive:**
```sql
-- Find traces with many model calls (possible loops)
SELECT trace_id, COUNT(*) as chat_count
FROM spans WHERE name LIKE 'chat%'
GROUP BY trace_id
ORDER BY chat_count DESC LIMIT 10;
```

#### 2. Context window growing too large

**Symptom:** Agent runs getting slower over time, high token counts.

**Diagnose with:**
```bash
co tail --models-only
```

**What to look for:**
- `in=` token count growing with each model call in the same trace
- Agent span's `gen_ai.usage.input_tokens` much higher than expected

**SQL deep dive:**
```sql
-- Token usage over time for model calls
SELECT
    datetime(start_time/1e9, 'unixepoch', 'localtime') as time,
    json_extract(attributes, '$.gen_ai.usage.input_tokens') as input_tok,
    json_extract(attributes, '$.gen_ai.usage.output_tokens') as output_tok,
    duration_ms
FROM spans
WHERE name LIKE 'chat%'
ORDER BY start_time DESC LIMIT 20;
```

#### 3. Tool returning errors

**Symptom:** Agent retries tools or gives up.

**Diagnose with:**
```bash
co tail --tools-only
```

**What to look for:**
- `ERROR` status (red) on tool spans
- `status_description` in the database for error details
- Check if the same tool is called repeatedly with different args (LLM retrying)

**SQL deep dive:**
```sql
-- Failed tool calls
SELECT
    datetime(start_time/1e9, 'unixepoch', 'localtime') as time,
    name,
    status_code,
    status_description,
    json_extract(attributes, '$.gen_ai.tool.call.arguments') as args
FROM spans
WHERE name LIKE 'execute_tool%' AND status_code = 'ERROR'
ORDER BY start_time DESC;
```

#### 4. Model generating malformed tool calls

**Symptom:** Tool execution fails because arguments don't match the schema.

**Diagnose with:**
```bash
co tail -v
```

**What to look for:**
- In verbose output, check the model's `tool_call` parts for bad JSON or wrong parameter names
- Compare `gen_ai.tool.definitions` (what the model was told) vs `gen_ai.tool.call.arguments` (what it generated)

**SQL deep dive:**
```sql
-- Model output with tool calls for a specific trace
SELECT
    json_extract(attributes, '$.gen_ai.output.messages') as output
FROM spans
WHERE name LIKE 'chat%' AND trace_id = '<trace_id>'
ORDER BY start_time;
```

#### 5. Unexpected model behavior / hallucinations

**Symptom:** Agent gives wrong answers or ignores instructions.

**Diagnose with:**
```bash
co tail -t <trace_id> --models-only -v
```

**What to look for:**
- Read the full `[thinking]` content — does the model's reasoning make sense?
- Check `gen_ai.input.messages` — is the system prompt intact? Is conversation history correct?
- Look at `pydantic_ai.all_messages` on the agent span for the full conversation state

#### 6. Spans not appearing (flush delay)

**Symptom:** `co tail` shows nothing even though `co chat` is active.

**Cause:** `BatchSpanProcessor` buffers spans for up to **5 seconds** (OTel SDK default `schedule_delay_millis=5000`).

**Workaround:** Wait 5s. Spans flush on batch interval or when the agent run completes.

**Fix (optional):** In `main.py`, pass `schedule_delay_millis=1000` to `BatchSpanProcessor` for near-real-time:
```python
tracer_provider.add_span_processor(
    BatchSpanProcessor(exporter, schedule_delay_millis=1000)
)
```

### Finding a Trace ID

To get a trace ID for filtered tailing:

```bash
# From co tail output — trace IDs aren't shown, so use SQL:
sqlite3 ~/.local/share/co-cli/co-cli.db \
  "SELECT trace_id, datetime(start_time/1e9, 'unixepoch', 'localtime') as time
   FROM spans WHERE parent_id IS NULL
   ORDER BY start_time DESC LIMIT 5"

# Or from co traces (HTML viewer) — trace IDs shown in headers
co traces
```

---

## Files

| Path | Purpose |
|------|---------|
| `co_cli/tail.py` | Polling loop, span formatting, `run_tail()` entry point |
| `co_cli/main.py` | `@app.command() def tail(...)` — CLI wrapper |
| `co_cli/trace_viewer.py` | Shared: `get_span_type()`, `format_duration()` |
| `co_cli/config.py` | Shared: `DATA_DIR` |
| `~/.local/share/co-cli/co-cli.db` | SQLite span storage |
