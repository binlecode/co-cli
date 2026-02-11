---
title: "06 — Tail Viewer"
parent: Infrastructure
nav_order: 2
---

# Design: `co tail` — Real-Time Span Tail Viewer

## 1. What & How

`co tail` is a real-time terminal viewer that polls the OTel SQLite database and prints completed spans as they arrive — like `tail -f` for agent traces. Run `co chat` in one terminal, `co tail` in another, and watch the agent→model→tool flow live.

```
┌──────────────────────┐       ┌──────────────────────────┐
│  Terminal A           │       │  Terminal B               │
│  $ co chat            │       │  $ co tail -v             │
│  Co > search my notes │  ───▶ │  14:23:05  model  chat    │
│                       │       │  14:23:06  tool   search  │
│  Found 3 notes...     │       │  14:23:07  model  chat    │
└──────────────────────┘       └──────────────────────────┘
                                      ▲ polls SQLite
                             ~/.local/share/co-cli/co-cli.db
```

**Polling mechanism:** On startup, show the N most recent spans (default 20), then follow with `time.sleep(poll_interval)`. Uses a high-water mark on `start_time` to query only new spans.

## 2. Core Logic

### Output Format

One line per completed span, with optional verbose content below model spans:

```
14:23:05  model  chat glm-4.7-flash:q4_k_m     in=3745 out=25  5.26s
           │ [thinking] Let me search the notes for that topic.
14:23:06  tool   execute_tool search_notes      tool=search_notes  args={"query":"test"}  120ms
14:23:08  agent  invoke_agent agent             model=glm-4.7-flash:q4_k_m  tokens=78776→4502  255.72s
```

| Column | Source | Format |
|--------|--------|--------|
| Timestamp | `start_time` (ns epoch) | Local `HH:MM:SS` |
| Type tag | `get_span_type(name)` | Left-padded 6 chars |
| Span name | `spans.name` | Left-padded 30 chars |
| Key attrs | JSON `attributes` column | Type-specific extraction |
| Duration | `duration_ms` | Via `format_duration()` |
| Status | `status_code` | Only shown if `ERROR` (red) |
| Verbose | `gen_ai.output.messages` | Indented `│` lines (model spans only, `-v`) |

### Color Scheme

Matches `_trace_viewer.py` for consistency across all three viewers:

| Type | Rich Style |
|------|------------|
| agent | `cyan` |
| model | `magenta` |
| tool | `yellow` |
| error | `bold red` |

### Span Attribute Reference

These OTel attributes (from `InstrumentationSettings(version=3)`) are available in the `attributes` JSON column — see [DESIGN-05-otel-logging.md](DESIGN-05-otel-logging.md) for the full schema.

**`invoke_agent agent`:** `model_name`, `gen_ai.usage.input_tokens`/`output_tokens`, `pydantic_ai.all_messages`

**`chat {model}`:** `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.input/output.messages`, `gen_ai.usage.input_tokens`/`output_tokens`

**`execute_tool {name}`:** `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`

### Troubleshooting

| Issue | Command | What to look for |
|-------|---------|-----------------|
| Agent stuck in tool loop | `co tail -v` | Repeating `chat → tool` without `stop` finish reason |
| Context growing too large | `co tail --models-only` | `in=` token count growing per model call |
| Tool returning errors | `co tail --tools-only` | `ERROR` status on tool spans |
| Spans not appearing | Wait 5s | `BatchSpanProcessor` buffers up to 5s |

## 3. Config

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--trace` | `-i` | None | Filter to a specific trace ID |
| `--tools-only` | `-T` | `False` | Only show tool spans |
| `--models-only` | `-m` | `False` | Only show model/chat spans |
| `--poll` | `-p` | `1.0` | Poll interval in seconds |
| `--no-follow` | `-n` | `False` | Print recent spans and exit |
| `--last` | `-l` | `20` | Number of recent spans on startup |
| `--verbose` | `-v` | `False` | Show LLM output content |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_tail.py` | Polling loop, span formatting, `run_tail()` entry point |
| `co_cli/main.py` | `@app.command() def tail(...)` — CLI wrapper |
| `co_cli/_trace_viewer.py` | Shared: `get_span_type()`, `format_duration()` |
| `co_cli/config.py` | Shared: `DATA_DIR` |
| `~/.local/share/co-cli/co-cli.db` | SQLite span storage |
