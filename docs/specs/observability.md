# Observability — Tracing and Viewers

## Product Intent

**Goal:** Trace every agent operation to local SQLite; provide two first-party viewer modes.
**Functional areas:**
- OTel instrumentation and `SQLiteSpanExporter`
- WAL mode for concurrent read access
- HTML tree viewer (nested span hierarchy)
- Live tail viewer (real-time terminal)

**Non-goals:**
- External telemetry endpoints
- Distributed tracing
- Retention/pruning automation

**Success criteria:** Every turn produces spans in `co-cli-logs.db` and `co-cli.jsonl`; WAL mode enables concurrent read; both viewers work on the same DB.
**Status:** Stable
**Known gaps:** No retention/pruning policy — DB grows unbounded.

---

## 1. What & How

Co CLI uses OpenTelemetry (OTel) to trace every agent operation. All data stays local in a SQLite database — no external telemetry endpoints. Two viewers are available: a static HTML tree viewer and a real-time terminal tail.

```
┌───────────────────────────────────────────────────────────┐
│                         Co CLI                            │
│                                                           │
│   Agent.run() ──▶ Model Call ──▶ Tool Execution           │
│        │               │               │                  │
│        └───────────────┴───────────────┘                  │
│                         │                                 │
│        ┌────────────────┴────────────────┐                │
│        ▼                                 ▼                │
│   Agent.instrument_all()        setup_file_logging()      │
│   setup_tracer_provider()              │                  │
│        │                               ▼                  │
│        ▼                    RotatingFileHandler            │
│   TracerProvider                co-cli.jsonl              │
│   ├──▶ SQLiteSpanExporter           ▲                     │
│   └──▶ JsonSpanExporter ────────────┘ (propagating logger)│
└──────────────┬────────────────────────────────────────────┘
               │
   ┌───────────┴──────────────┐
   ▼                          ▼
~/.co-cli/co-cli-logs.db    ~/.co-cli/logs/co-cli.jsonl
               │
   ┌───────────┴───────────┐
   ▼                       ▼
co traces               co tail
  (HTML)              (terminal)
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
                              ~/.co-cli/co-cli-logs.db
```

## 2. Core Logic

### Instrumentation Setup (`main.py`)

Telemetry is bootstrapped at module load time, before any agent is created. All write targets — the JSONL file handler, the SQLite exporter, and the JSONL span exporter — are initialised at this step:

```
setup_file_logging(LOGS_DIR, level, max_size_mb, backup_count)   # co-cli.jsonl
tracer_provider = setup_tracer_provider(                          # co-cli-logs.db + co-cli.jsonl
    service_name, service_version,
    redact_patterns=settings.observability.redact_patterns
)
Agent.instrument_all(InstrumentationSettings(tracer_provider, version=3))
for logger_name in ["openai", "httpx", "anthropic", "hpack"]:    # co_cli.* loggers unaffected
    logging.getLogger(logger_name).setLevel(WARNING)
```

`setup_tracer_provider()` (in `_telemetry.py`) creates a `TracerProvider` with two `SimpleSpanProcessor`s: one wrapping `SQLiteSpanExporter` and one wrapping `JsonSpanExporter`. Both receive every span.

### File Logging (`_file_logging.py`)

`setup_file_logging()` attaches a single `RotatingFileHandler` to the Python root logger. Every `logging.*` call anywhere in the process is captured without per-module configuration.

**File written under `~/.co-cli/logs/`:**

| File | Level filter | Max size | Backups |
|------|-------------|----------|---------|
| `co-cli.jsonl` | INFO+ | `log_max_size_mb` MB | `log_backup_count` |
| `errors.jsonl` | WARNING+ | 2 MB (hardcoded) | 2 (hardcoded) |

**Format:** one JSON object per line. Python logging records use `"kind": "log"`:
```json
{"ts": "2026-04-15T10:04:17.000Z", "kind": "log", "level": "INFO", "logger": "co_cli.context.orchestrate", "msg": "..."}
```
OTel span records (from `JsonSpanExporter`) use `"kind": "span"` and are passed through as-is.

**Secret redaction:** `_JsonRedactingFormatter` applies regex substitutions to each message before serialisation. Patterns covered: bearer tokens, `sk-*` / `sk-ant-*` API keys, GitHub `ghp_` tokens, `AIza*` Google tokens, JSON fields named `api_key`, `token`, `secret`, `password`, or `credential`, and PEM private key blocks.

**Idempotent:** calling `setup_file_logging()` more than once with the same log directory is safe — duplicate handlers are not added.

### JSON Span Exporter (`_telemetry.py`)

`JsonSpanExporter` writes every OTel span to `~/.co-cli/logs/co-cli.jsonl` as a JSON line containing 100% of span content — enabling `jq` queries without a DB query. Uses a dedicated `co_cli.observability.spans` logger with `propagate=True` and no own handler, so records flow to the root logger's `RotatingFileHandler` that owns `co-cli.jsonl`.

**Format:** one JSON object per completed span:
```json
{"ts": "2026-04-15T10:04:19.123Z", "kind": "span", "span_id": "...", "trace_id": "...", "parent_id": "...", "name": "execute_tool web_search", "attributes": {"gen_ai.tool.name": "web_search", ...}, "events": [], "duration_ms": 890.0, "status": "OK", "status_description": null}
```

Full attributes and events are included with no truncation. String attribute values are redacted using the same patterns as `SQLiteSpanExporter`.

**Querying with jq:** `jq 'select(.kind=="span" and .name=="co.turn")' ~/.co-cli/logs/co-cli.jsonl`

**Advanced JSONL Tailing (jq + perl):**
To parse nested JSON strings (like `pydantic_ai.all_messages`) and format literal `\n` characters for terminal readability, use the `scripts/tail-jsonl.sh` utility or this command:

```bash
tail -f ~/.co-cli/logs/co-cli.jsonl | jq --unbuffered '
  walk(
    if type == "string" and (startswith("{") or startswith("[")) 
    then (fromjson? // .) 
    else . 
    end
  )
' | perl -pe 's/\\n/\n/g'
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

Two processes touch the same DB simultaneously: `co chat` (writer) and `co tail` (reader, long-lived connection). Three layers of defense:

1. **WAL journal mode** — separates reads from writes; readers see a consistent snapshot while the writer appends.
2. **Busy timeout (5 s)** — handles brief exclusive locks during WAL checkpoints.
3. **Export-level retry (3×, exponential backoff starting at 0.1 s)** — catches persistent checkpoint conflicts.

The `SQLiteSpanExporter` opens a fresh connection per `export()` call and closes it immediately after. This avoids stale WAL readers that can prevent checkpoint progress. Span loss is tolerable — telemetry is best-effort.

### What Gets Traced

| Span Name (v3) | Kind | Key Attributes |
|----------------|------|----------------|
| `co.turn` | INTERNAL | `turn.outcome` (`continue`/`error`), `turn.interrupted` (bool), `turn.input_tokens`, `turn.output_tokens` — root span for every user turn; emitted by `co-cli.orchestrate` tracer in `run_turn()`; all pydantic-ai child spans attach under this root automatically. On terminal `ModelHTTPError` (429/5xx or budget-exhausted 400), adds a `provider_error` event with `http.status_code` (int) and `error.body` (str, capped at 500 chars). |
| `invoke_agent {name}` | INTERNAL | `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `pydantic_ai.all_messages`, `final_result` |
| `chat {model}` | CLIENT | `gen_ai.request.model`, `gen_ai.response.finish_reasons`, `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| `running tools` | INTERNAL | list of tool names |
| `execute_tool {name}` | INTERNAL | `gen_ai.tool.name`, `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`; enriched by `CoToolLifecycle.after_tool_execute` with `co.tool.source` (`native`/`mcp`), `co.tool.requires_approval` (bool), `co.tool.result_size` (int); `rag.backend` (`fts5`, `hybrid`, or `grep`) stamped by `search_memory` and `search_knowledge` to identify the active retrieval path |
| `{role}` | INTERNAL | `agent.role`, `agent.model`, `agent.request_limit`, `agent.requests_used` — emitted by `co-cli.agents` tracer (`_delegate_agent` in `agents.py`); covers one sub-agent run including optional retry (e.g. `research_web`, `analyze_knowledge`, `reason_about`) |
| `background_task_execute` | INTERNAL | `task.command`, `task.description`, `task.cwd` — span ID passed to `spawn_task()` for cross-session task linkage |

### Trace HTML Viewer (`co traces`)

`co traces` generates a static HTML file with nested, collapsible spans — similar to Logfire. It reads the last 20 traces from the DB, builds a span tree from flat rows via `build_span_tree()`, then renders each span recursively with waterfall timing bars showing relative duration.

**Stats panel** shows total traces, total spans, and tool call count.

**Attribute rendering:** tool, model, message, and token fields are rendered first, then the remaining attributes are appended. Long values are truncated at 200 chars with an `[expand]` toggle that pretty-prints JSON in-place. `logfire.*` internal attributes are suppressed.

**Color scheme** — consistent across both viewers:

| Type | HTML class | Hex |
|------|-----------|-----|
| agent | `.agent` | `#00d4ff` (cyan) |
| model | `.model` | `#9b59b6` (purple) |
| tool | `.tool` | `#f39c12` (orange) |
| error | `.ERROR` | `#e74c3c` (red) |

`get_span_type(name)` classifies spans by substring: names containing `agent` → agent, `tool` → tool, `model`/`chat` → model.

The HTML file is written to `~/.co-cli/traces.html` and auto-opened in the browser. It requires no server — purely static.

### Live Tail Viewer (`co tail`)

`co tail` polls the OTel SQLite database and prints completed spans as they arrive — like `tail -f` for agent traces.

**Startup:** fetch the N most recent spans (`--last`, default 20) ordered by `start_time`, print them, record the highest `start_time` as the high-water mark.

**Follow loop:** sleep `--poll` seconds (default 1.0), query spans with `start_time > high_water_mark`, print any new rows, advance the high-water mark. Uses one long-lived connection for the whole session (read-only; WAL mode ensures it doesn't block the writer).

**Output format** — one line per span:

```
14:23:05  model  chat qwen3:30b-a3b-thinking-2507-q8_0     in=3745 out=25  5.26s
           │ [thinking] Let me search the notes for that topic.
14:23:06  tool   execute_tool search_notes      tool=search_notes  args={"query":"test"}…  120ms
14:23:08  agent  invoke_agent agent             model=qwen3:30b-a3b-thinking-2507-q8_0  tokens=7877→4502  255.72s
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
| tool | `gen_ai.tool.name` → `tool=…`, `gen_ai.tool.call.arguments` → `args=…` (truncated to 80 chars) |

**Verbose mode (`-v`):** expands each span with detailed content:
- **agent**: shows the final output result via `[final]`.
- **model**: shows the first line of the system prompt, the last user message, and the full response. Parsed from `gen_ai.input.messages` and `gen_ai.output.messages`. Thinking blocks are prefixed with `[thinking]` in dim italic.
- **tool**: shows full arguments (pretty-printed JSON) and the full tool result.

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
    json_extract(attributes, '$.gen_ai.tool.call.arguments') AS args,
    duration_ms
FROM spans WHERE name LIKE 'execute_tool%' ORDER BY start_time DESC;

-- Token usage by model
SELECT json_extract(attributes, '$.gen_ai.request.model') AS model,
    COUNT(*) AS runs,
    SUM(json_extract(attributes, '$.gen_ai.usage.input_tokens')) AS input_tokens,
    SUM(json_extract(attributes, '$.gen_ai.usage.output_tokens')) AS output_tokens
FROM spans WHERE name LIKE 'invoke_agent%' GROUP BY model;

-- Provider errors (429/5xx/budget-exhausted 400) with status code and body
SELECT datetime(start_time/1e9, 'unixepoch', 'localtime') AS time,
    json_extract(events, '$[0].attributes."http.status_code"') AS status,
    json_extract(events, '$[0].attributes."error.body"') AS body
FROM spans WHERE name = 'co.turn' AND status_code = 'ERROR'
    AND json_extract(events, '$[0].name') = 'provider_error'
ORDER BY start_time DESC;
```

### Troubleshooting

| Issue | Command | What to look for |
|-------|---------|-----------------|
| Agent stuck in tool loop | `co tail -v` | Repeating `chat → tool` without `stop` finish reason |
| Context growing too large | `co tail --models-only` | `in=` token count growing each model call |
| Tool returning errors | `co tail --tools-only` | `ERROR` status on tool spans |
| Spans not appearing | Check for an active run | `co tail` only shows completed spans after they are exported |

### Privacy

All data stays local. Tool responses and full conversation history are captured in span attributes. Before any span is written to SQLite, `SQLiteSpanExporter` applies regex redaction to every string attribute value and every string value in event attribute dicts — replacing matches with `[REDACTED]`. The default pattern set covers common secret formats (OpenAI/Anthropic `sk-*` keys, Bearer tokens, GitHub `ghp_` tokens, generic `api_key=` pairs, AWS AKIA IDs, PEM private key headers). Values exceeding 64 KB bypass redaction as a performance guard. There is no built-in retention or pruning policy — the DB grows unbounded. To clear all traces: `rm ~/.co-cli/co-cli-logs.db`.

## 3. Config

### Exporter / DB

| Setting | Env Var | Default | Description |
|---------|--------|---------|-------------|
| DB path | — | `~/.co-cli/co-cli-logs.db` | Span storage (user dotdir) |
| Instrumentation version | — | `3` | Hardcoded in `main.py` for OTel GenAI spec compliance |

### File Logging (`observability` settings group)

| Setting | Env Var | Default | Description |
|---------|--------|---------|-------------|
| `observability.log_level` | `CO_CLI_LOG_LEVEL` | `INFO` | Minimum level written to `co-cli.jsonl` (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `observability.log_max_size_mb` | `CO_CLI_LOG_MAX_SIZE_MB` | `5` | Max file size in MB before rotation (1–500) |
| `observability.log_backup_count` | `CO_CLI_LOG_BACKUP_COUNT` | `3` | Rotated backup files to keep per log file (0–20) |
| `observability.redact_patterns` | — | 6 default patterns | Regex list applied to span attribute strings before SQLite storage; extend via `settings.json` for custom secret formats |

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
| `co_cli/observability/_telemetry.py` | `SQLiteSpanExporter` (spans → SQLite), `JsonSpanExporter` (spans → `co-cli.jsonl` via propagating logger), `setup_tracer_provider()` (provider factory for both exporters) |
| `co_cli/observability/_file_logging.py` | `setup_file_logging()` — attaches two rotating JSONL handlers to root logger: `co-cli.jsonl` (INFO+) and `errors.jsonl` (WARNING+, 2 MB/2 backups hardcoded) |
| `co_cli/observability/_viewer.py` | HTML generator — collapsible nested span tree, waterfall bars; shared `get_span_type()`, `format_duration()`, `extract_span_attrs()` |
| `co_cli/observability/_tail.py` | Polling loop, per-type attribute extraction, verbose LLM output, `run_tail()` entry point |
| `co_cli/main.py` | `@app.command()` wrappers for `traces` and `tail`; module-level OTel + file logging bootstrap |
| `co_cli/config/_core.py` | `USER_DIR`, `LOGS_DIR` — user-global path constants |
| `co_cli/config/_observability.py` | `ObservabilitySettings` — file logging settings (`log_level`, `log_max_size_mb`, `log_backup_count`) and span redaction (`redact_patterns`) |
| `~/.co-cli/co-cli-logs.db` | SQLite span storage |
| `~/.co-cli/logs/co-cli.jsonl` | Master rotating JSONL log — INFO+ Python logging records (`"kind": "log"`) and OTel span records (`"kind": "span"`) |
| `~/.co-cli/logs/errors.jsonl` | Dedicated rotating JSONL log — WARNING+ only; 2 MB / 2 backups; for fast error triage without parsing span JSON |
| `~/.co-cli/traces.html` | Generated static HTML viewer (written by `co traces`) |
