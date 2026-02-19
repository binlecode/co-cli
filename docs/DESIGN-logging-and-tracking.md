---
title: "Logging & Tracking"
parent: Infrastructure
nav_order: 1
---

# Logging & Tracking

## 1. What & How

Co CLI uses OpenTelemetry (OTel) to trace every agent operation. All data stays local in a SQLite database — no external telemetry endpoints. Three viewers are available: Datasette for ad-hoc SQL queries, a static HTML tree viewer, and a real-time terminal tail.

```
┌───────────────────────────────────────────────────────────┐
│                         Co CLI                            │
│                                                           │
│   Agent.run() ──▶ Model Call ──▶ Tool Execution           │
│        │               │               │                  │
│        └───────────────┴───────────────┘                  │
│                         │                                 │
│                         ▼                                 │
│            Agent.instrument_all()                         │
│                         │                                 │
│                         ▼                                 │
│     TracerProvider(resource=service.name, version)        │
│                         │                                 │
│                         ▼                                 │
│            BatchSpanProcessor                             │
│                         │                                 │
│                         ▼                                 │
│            SQLiteSpanExporter                             │
└─────────────────────────┼─────────────────────────────────┘
                          │
                          ▼
            ~/.local/share/co-cli/co-cli.db
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         co logs      co traces   co tail
        (Datasette)    (HTML)    (terminal)
```

Run `co chat` in one terminal and `co tail` in another to watch the agent→model→tool flow live:

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

## 2. Core Logic

### Instrumentation Setup (`main.py`)

Telemetry is bootstrapped at module load time, before any agent is created:

```python
resource = Resource.create({"service.name": "co-cli", "service.version": version})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(tracer_provider)

Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,  # OTel GenAI spec compliance (pydantic-ai ≥ 0.0.29)
))
```

`InstrumentationSettings(version=3)` selects the latest OTel GenAI semantic conventions:

| Version | Span Names | Attribute Style |
|---------|------------|-----------------|
| 1 (legacy) | `agent run` | `logfire.*` |
| 2 (default) | `agent run`, `running tool` | `gen_ai.*` + `tool_arguments` |
| **3 (spec)** | `invoke_agent {name}`, `execute_tool {name}` | `gen_ai.*` per spec |

### SQLite Span Exporter (`_telemetry.py`)

Custom `SpanExporter` that writes spans to SQLite. Uses two-phase export: serialise spans into row tuples (no DB lock held), then write all rows in a single `executemany` (minimal lock window).

**Schema:**

```sql
CREATE TABLE spans (
    id TEXT PRIMARY KEY,           -- 16-char hex span ID
    trace_id TEXT NOT NULL,        -- 32-char hex trace ID
    parent_id TEXT,                -- Parent span ID (null for root)
    name TEXT NOT NULL,            -- Operation name
    kind TEXT,                     -- INTERNAL, CLIENT, SERVER
    start_time INTEGER NOT NULL,   -- Nanoseconds epoch
    end_time INTEGER,
    duration_ms REAL,              -- Calculated: (end_time - start_time) / 1_000_000
    status_code TEXT,              -- OK, ERROR, UNSET
    status_description TEXT,
    attributes TEXT,               -- JSON object
    events TEXT,                   -- JSON array of {name, timestamp, attributes}
    resource TEXT                  -- JSON (service.name, service.version)
);
-- Indexes: trace_id, parent_id, start_time DESC
```

### Concurrent Access (WAL Mode)

Three processes touch the same DB simultaneously: `co chat` (writer), `co tail` (reader, long-lived connection), and `co logs`/Datasette (reader). Three layers of defense:

1. **WAL journal mode** — separates reads from writes; readers see a consistent snapshot while the writer appends.
2. **Busy timeout (5 s)** — handles brief exclusive locks during WAL checkpoints.
3. **Export-level retry (3×, exponential backoff starting at 0.1 s)** — catches persistent checkpoint conflicts.

The `SQLiteSpanExporter` opens a fresh connection per `export()` call and closes it immediately after. This avoids stale WAL readers that can prevent checkpoint progress. Span loss is tolerable — telemetry is best-effort.

### What Gets Traced

| Span Name (v3) | Kind | Key Attributes |
|----------------|------|----------------|
| `invoke_agent {name}` | INTERNAL | `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `pydantic_ai.all_messages` |
| `chat {model}` | CLIENT | `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| `running tools` | INTERNAL | list of tool names |
| `execute_tool {name}` | INTERNAL | `gen_ai.tool.name`, `tool_arguments`, `tool_response` |

### Trace HTML Viewer (`co traces`)

`co traces` generates a static HTML file with nested, collapsible spans — similar to Logfire. It reads the last 20 traces from the DB, builds a span tree from flat rows via `build_span_tree()`, then renders each span recursively with waterfall timing bars showing relative duration.

**Stats panel** shows total traces, total spans, and tool call count.

**Attribute rendering:** priority keys (`gen_ai.tool.name`, `tool_arguments`, `gen_ai.request.model`, `gen_ai.input/output.messages`, token counts) appear first. Long values are truncated at 200 chars with an `[expand]` toggle that pretty-prints JSON in-place. `logfire.*` internal attributes are suppressed.

**Color scheme** — consistent across all three viewers:

| Type | HTML class | Hex |
|------|-----------|-----|
| agent | `.agent` | `#00d4ff` (cyan) |
| model | `.model` | `#9b59b6` (purple) |
| tool | `.tool` | `#f39c12` (orange) |
| error | `.ERROR` | `#e74c3c` (red) |

`get_span_type(name)` classifies spans by substring: names containing `agent` → agent, `tool` → tool, `model`/`chat` → model.

The HTML file is written to `~/.local/share/co-cli/traces.html` and auto-opened in the browser. It requires no server — purely static.

### Live Tail Viewer (`co tail`)

`co tail` polls the OTel SQLite database and prints completed spans as they arrive — like `tail -f` for agent traces.

**Startup:** fetch the N most recent spans (`--last`, default 20) ordered by `start_time`, print them, record the highest `start_time` as the high-water mark.

**Follow loop:** sleep `--poll` seconds (default 1.0), query spans with `start_time > high_water_mark`, print any new rows, advance the high-water mark. Uses one long-lived connection for the whole session (read-only; WAL mode ensures it doesn't block the writer).

**Output format** — one line per span:

```
14:23:05  model  chat glm-4.7-flash:q4_k_m     in=3745 out=25  5.26s
           │ [thinking] Let me search the notes for that topic.
14:23:06  tool   execute_tool search_notes      tool=search_notes  args={"query":"test"}…  120ms
14:23:08  agent  invoke_agent agent             model=glm-4.7-flash:q4_k_m  tokens=7877→4502  255.72s
```

| Column | Source | Format |
|--------|--------|--------|
| Timestamp | `start_time` (ns epoch) | Local `HH:MM:SS` |
| Type tag | `get_span_type(name)` | Left-padded 6 chars |
| Span name | `spans.name` | Left-padded 30 chars |
| Key attrs | JSON `attributes` column | Type-specific (see below) |
| Duration | `duration_ms` | `format_duration()` — µs/ms/s |
| Status | `status_code` | Only shown for `ERROR` (bold red) |

**Per-type attribute extraction:**

| Span type | Extracted fields |
|-----------|-----------------|
| agent | `gen_ai.request.model` → `model=…`, `gen_ai.usage.input_tokens` + `output_tokens` → `tokens=in→out` |
| model | `gen_ai.usage.input_tokens` → `in=…`, `gen_ai.usage.output_tokens` → `out=…` |
| tool | `gen_ai.tool.name` → `tool=…`, `tool_arguments` → `args=…` (truncated to 80 chars) |

**Verbose mode (`-v`):** after each model span, indented lines show the LLM's output content. Parsed from `gen_ai.output.messages` JSON (list of messages, each with `parts` of type `text` or `thinking`). Thinking blocks are prefixed with `[thinking]` in dim italic.

**Rich color scheme:**

| Type | Rich style |
|------|-----------|
| agent | `cyan` |
| model | `magenta` |
| tool | `yellow` |
| error | `bold red` |

### Example SQL Queries

```sql
-- Recent root spans
SELECT trace_id, name, duration_ms, status_code
FROM spans WHERE parent_id IS NULL ORDER BY start_time DESC LIMIT 10;

-- Tool calls with arguments and duration (v3 attribute names)
SELECT datetime(start_time/1e9, 'unixepoch', 'localtime') AS time,
    json_extract(attributes, '$.gen_ai.tool.name') AS tool,
    json_extract(attributes, '$.tool_arguments') AS args,
    duration_ms
FROM spans WHERE name LIKE 'execute_tool%' ORDER BY start_time DESC;

-- Token usage by model
SELECT json_extract(attributes, '$.gen_ai.request.model') AS model,
    COUNT(*) AS runs,
    SUM(json_extract(attributes, '$.gen_ai.usage.input_tokens')) AS input_tokens,
    SUM(json_extract(attributes, '$.gen_ai.usage.output_tokens')) AS output_tokens
FROM spans WHERE name LIKE 'invoke_agent%' GROUP BY model;
```

### Troubleshooting

| Issue | Command | What to look for |
|-------|---------|-----------------|
| Agent stuck in tool loop | `co tail -v` | Repeating `chat → tool` without `stop` finish reason |
| Context growing too large | `co tail --models-only` | `in=` token count growing each model call |
| Tool returning errors | `co tail --tools-only` | `ERROR` status on tool spans |
| Spans not appearing | Wait up to 5 s | `BatchSpanProcessor` buffers before flushing |

### Privacy

All data stays local. Tool responses and full conversation history are captured in span attributes. To clear all traces: `rm ~/.local/share/co-cli/co-cli.db`.

## 3. Config

### Exporter / DB

| Setting | Env Var | Default | Description |
|---------|--------|---------|-------------|
| DB path | — | `~/.local/share/co-cli/co-cli.db` | Span storage (XDG data dir) |
| Instrumentation version | — | `3` | Hardcoded in `main.py` for OTel GenAI spec compliance |

### `co tail` Flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--trace` | `-i` | None | Filter to a specific trace ID |
| `--tools-only` | `-T` | `False` | Only show tool spans |
| `--models-only` | `-m` | `False` | Only show model/chat spans |
| `--poll` | `-p` | `1.0` | Poll interval in seconds |
| `--no-follow` | `-n` | `False` | Print recent spans and exit |
| `--last` | `-l` | `20` | Number of recent spans shown on startup |
| `--verbose` | `-v` | `False` | Show LLM output content for model spans |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/_telemetry.py` | `SQLiteSpanExporter` — serialises OTel spans to SQLite with WAL + retry |
| `co_cli/_trace_viewer.py` | HTML generator — collapsible nested span tree, waterfall bars; shared `get_span_type()` and `format_duration()` |
| `co_cli/_tail.py` | Polling loop, per-type attribute extraction, verbose LLM output, `run_tail()` entry point |
| `co_cli/datasette_metadata.json` | Datasette UI config for `co logs` |
| `co_cli/main.py` | `@app.command()` wrappers for `logs`, `traces`, `tail`; module-level OTel bootstrap |
| `co_cli/config.py` | `DATA_DIR` — shared XDG data path |
| `~/.local/share/co-cli/co-cli.db` | SQLite span storage |
| `~/.local/share/co-cli/traces.html` | Generated static HTML viewer (written by `co traces`) |
