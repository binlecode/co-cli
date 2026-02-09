---
title: "05 — OpenTelemetry Logging"
parent: Infrastructure
nav_order: 1
---

# Design: OpenTelemetry Logging

## 1. What & How

Co CLI uses OpenTelemetry (OTel) to trace agent operations. All data stays local in SQLite. Three viewers available: Datasette (table), nested HTML (like Logfire), and real-time tail (see [DESIGN-06-tail-viewer.md](DESIGN-06-tail-viewer.md)).

```
┌────────────────────────────────────────────────────────────┐
│                        Co CLI                               │
│                                                             │
│  Agent.run() ──▶ Model Call ──▶ Tool Execution             │
│       │              │               │                      │
│       └──────────────┴───────────────┘                      │
│                      │                                      │
│                      ▼                                      │
│           Agent.instrument_all()                            │
│                      │                                      │
│                      ▼                                      │
│    TracerProvider(resource=service.name, version)          │
│                      │                                      │
│                      ▼                                      │
│           BatchSpanProcessor                                │
│                      │                                      │
│                      ▼                                      │
│           SQLiteSpanExporter                                │
└──────────────────────┼──────────────────────────────────────┘
                       │
                       ▼
             ~/.local/share/co-cli/co-cli.db
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
    co logs       co traces    co tail
   (Datasette)     (HTML)    (terminal)
```

## 2. Core Logic

### Instrumentation Setup (`main.py`)

```python
version = read_version_from_pyproject()
resource = Resource.create({"service.name": "co-cli", "service.version": version})
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(tracer_provider)

Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,  # Full OTel GenAI spec compliance (v1.37.0)
))
```

We use `InstrumentationSettings(version=3)` for full spec compliance:

| Version | Span Names | Attributes |
|---------|------------|------------|
| 1 (legacy) | `agent run` | `logfire.*` style |
| 2 (default) | `agent run`, `running tool` | `gen_ai.*` + `tool_arguments` |
| **3 (spec)** | `invoke_agent {name}`, `execute_tool {name}` | `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |

### SQLite Span Exporter (`telemetry.py`)

Custom exporter that writes spans to SQLite. Uses two-phase export: serialise spans into tuples (no DB lock), then write all rows in a single `executemany` (minimal lock window).

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
    duration_ms REAL,              -- Calculated
    status_code TEXT,              -- OK, ERROR, UNSET
    status_description TEXT,
    attributes TEXT,               -- JSON
    events TEXT,                   -- JSON array
    resource TEXT                  -- JSON (service.name, version)
);
```

### Concurrent Access (WAL Mode)

Three processes touch the same DB: `co chat` (writer), `co tail` (reader), `co logs`/Datasette (reader). Three layers of defense:

1. **WAL journal mode** — separates reads from writes. Readers see a consistent snapshot while the writer appends.
2. **Busy timeout (5s)** — handles brief exclusive locks during WAL checkpoints.
3. **Export-level retry (3×, exponential backoff)** — catches persistent checkpoint conflicts.

Short-lived connections (opened per `export()` call) avoid stale WAL readers that can prevent checkpoint progress. Span loss is tolerable — telemetry is best-effort.

### What Gets Traced

| Span Name (v3) | Kind | Key Attributes |
|-----------|------|----------------|
| `invoke_agent {name}` | INTERNAL | model_name, gen_ai.usage.input/output_tokens, pydantic_ai.all_messages |
| `chat {model}` | CLIENT | gen_ai.request.model, gen_ai.response.finish_reasons, gen_ai.input/output.messages |
| `running tools` | INTERNAL | tools (list of tool names) |
| `execute_tool {name}` | INTERNAL | gen_ai.tool.name, gen_ai.tool.call.arguments, gen_ai.tool.call.result |

### Trace Viewer (`trace_viewer.py`)

Generates static HTML with nested, collapsible spans. Builds a span tree from flat DB rows via `build_span_tree()`, then renders recursively with waterfall timing bars showing relative duration.

### CLI Commands

| Command | Mode | Best For |
|---------|------|----------|
| `co logs` | Datasette (browser) | SQL queries, post-hoc deep dives |
| `co traces` | Static HTML (browser) | Visual span tree, waterfall timing |
| `co tail` | Terminal (live) | Real-time monitoring (see [DESIGN-06-tail-viewer.md](DESIGN-06-tail-viewer.md)) |

### Example SQL Queries

```sql
-- Recent root spans
SELECT trace_id, name, duration_ms, status_code
FROM spans WHERE parent_id IS NULL ORDER BY start_time DESC LIMIT 10;

-- Tool calls with responses (v3)
SELECT datetime(start_time/1e9, 'unixepoch', 'localtime') as time,
    json_extract(attributes, '$.gen_ai.tool.name') as tool,
    json_extract(attributes, '$.gen_ai.tool.call.arguments') as args,
    duration_ms
FROM spans WHERE name LIKE 'execute_tool%' ORDER BY start_time DESC;

-- Token usage by model
SELECT json_extract(attributes, '$.model_name') as model, COUNT(*) as runs,
    SUM(json_extract(attributes, '$.gen_ai.usage.input_tokens')) as input_tokens,
    SUM(json_extract(attributes, '$.gen_ai.usage.output_tokens')) as output_tokens
FROM spans WHERE name LIKE 'invoke_agent%' GROUP BY model;
```

### Privacy

All data stays local (no external telemetry endpoints). Tool responses and full conversation history are captured. To clear: `rm ~/.local/share/co-cli/co-cli.db`.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|--------|---------|-------------|
| DB path | — | `~/.local/share/co-cli/co-cli.db` | Span storage location (XDG data dir) |
| Instrumentation version | — | `3` | Hardcoded in `main.py` for OTel GenAI spec compliance |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/telemetry.py` | SQLiteSpanExporter (OTel → SQLite) |
| `co_cli/trace_viewer.py` | HTML generator (collapsible nested view) |
| `co_cli/datasette_metadata.json` | Datasette UI config |
| `~/.local/share/co-cli/co-cli.db` | Span storage |
| `~/.local/share/co-cli/traces.html` | Generated viewer (static, no server) |
