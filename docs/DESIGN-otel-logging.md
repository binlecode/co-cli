# Design: OpenTelemetry Logging for Co CLI

**Status:** Implemented (OTel GenAI v1.37.0 compliant)
**Last Updated:** 2026-02-04

## Overview

Co CLI uses OpenTelemetry (OTel) to trace agent operations. All data stays local in SQLite. Two viewers available: Datasette (table) and nested HTML (like Logfire).

## Architecture

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
           ┌───────────┴───────────┐
           ▼                       ▼
    co logs (Datasette)    co traces (HTML)
```

---

## Detailed Design

### 1. Instrumentation Setup (`main.py`)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

# Resource attributes identify the service (OTel best practice)
resource = Resource.create({
    "service.name": "co-cli",
    "service.version": "0.1.0",
})

# TracerProvider is the entry point for all tracing
tracer_provider = TracerProvider(resource=resource)

# BatchSpanProcessor batches spans before export (efficient)
tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

# Set as global tracer provider
trace.set_tracer_provider(tracer_provider)

# Enable pydantic-ai instrumentation with full OTel GenAI spec compliance
Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,  # Full spec: gen_ai.tool.call.arguments, invoke_agent spans, etc.
))
```

**Key Design Decisions:**
- `Resource` provides service metadata on every span
- `BatchSpanProcessor` buffers spans (vs `SimpleSpanProcessor` which exports immediately)
- `Agent.instrument_all()` must be called AFTER tracer setup
- `version=3` for full OTel GenAI spec compliance (v1.37.0)
- Pass `tracer_provider` explicitly for predictable behavior

### 2. SQLite Span Exporter (`telemetry.py`)

Custom exporter that writes spans to SQLite instead of sending to a remote collector.

```
┌─────────────────────────────────────────────────────────────────┐
│                    BatchSpanProcessor                            │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                         │
│  │ Span 1  │  │ Span 2  │  │ Span 3  │  ... (buffered)         │
│  └─────────┘  └─────────┘  └─────────┘                         │
└─────────────────────────┬───────────────────────────────────────┘
                          │ flush (on interval or shutdown)
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                   SQLiteSpanExporter.export()                    │
│                                                                  │
│  For each span:                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ReadableSpan                                             │    │
│  │  .context.span_id    ──▶  format(id, '016x')  ──▶ "abc.."│    │
│  │  .context.trace_id   ──▶  format(id, '032x')  ──▶ "def.."│    │
│  │  .parent.span_id     ──▶  format or None      ──▶ "123.."│    │
│  │  .start_time/.end_time ──▶ duration_ms calc   ──▶ 1234.5 │    │
│  │  .attributes         ──▶  json.dumps()        ──▶ "{...}"│    │
│  │  .events             ──▶  json.dumps()        ──▶ "[...]"│    │
│  │  .resource           ──▶  json.dumps()        ──▶ "{...}"│    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────────┘
                          │ INSERT OR REPLACE
                          ▼
              ┌───────────────────────┐
              │   co-cli.db (SQLite)  │
              │   ┌───────────────┐   │
              │   │ spans table   │   │
              │   └───────────────┘   │
              └───────────────────────┘
```

```python
class SQLiteSpanExporter(SpanExporter):
    def __init__(self, db_path: str = str(DATA_DIR / "co-cli.db")):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with WAL mode and busy_timeout."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self):
        """Create spans table with OTel-compliant schema."""
        # Schema follows OTel data model
        with self._connect() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                id TEXT PRIMARY KEY,           -- 16-char hex span ID
                trace_id TEXT NOT NULL,        -- 32-char hex trace ID
                parent_id TEXT,                -- Parent span (null=root)
                name TEXT NOT NULL,            -- Operation name
                kind TEXT,                     -- INTERNAL/CLIENT/SERVER
                start_time INTEGER NOT NULL,   -- Nanoseconds epoch
                end_time INTEGER,
                duration_ms REAL,              -- Calculated
                status_code TEXT,              -- OK/ERROR/UNSET
                status_description TEXT,
                attributes TEXT,               -- JSON
                events TEXT,                   -- JSON array
                resource TEXT                  -- JSON (service.name, etc)
            )
        """)
        # Indexes for common query patterns
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_start ON spans(start_time DESC)")

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        """Pre-serialize all spans, then write in a single executemany + retry."""
        rows = []
        for span in spans:
            span_id = format(span.context.span_id, '016x')
            trace_id = format(span.context.trace_id, '032x')
            parent_id = format(span.parent.span_id, '016x') if span.parent else None
            duration_ms = (span.end_time - span.start_time) / 1_000_000
            # ... serialize status, events, resource ...
            rows.append((span_id, trace_id, parent_id, ...))

        for attempt in range(3):
            try:
                with self._connect() as conn:
                    conn.executemany("INSERT OR REPLACE INTO spans VALUES (...)", rows)
                return SpanExportResult.SUCCESS
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) and attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))  # exponential backoff
                else:
                    return SpanExportResult.FAILURE
        return SpanExportResult.FAILURE
```

**Key Design Decisions:**
- Hex string IDs (not raw bytes) for readability in SQL queries
- Pre-calculated `duration_ms` for easy querying
- JSON serialization for attributes/events/resource
- `INSERT OR REPLACE` handles duplicate span IDs gracefully

#### Concurrent Access (WAL Mode)

##### The Problem

Three processes touch the same `co-cli.db` file:

| Process | Role | Lifetime |
|---------|------|----------|
| `co chat` (BatchSpanProcessor → SQLiteSpanExporter) | Writer | Long-running — flushes batches every few seconds for the duration of the chat session |
| `co tail` | Reader | Long-running — polls the DB in a loop to display new spans in real-time |
| `co logs` (Datasette) | Reader | On-demand — serves HTTP queries against the DB while the user browses |

SQLite's default journal mode (`delete` / rollback journal) takes an **exclusive lock** on the entire database file during writes. Any reader that queries at the same moment receives an immediate `sqlite3.OperationalError: database is locked` failure. Because the chat session writes continuously and the tail viewer polls continuously, lock collisions are frequent under normal usage.

##### Design: WAL + Busy Timeout + Retry

The fix applies three layers of defense, each addressing a different failure window:

**Layer 1 — WAL journal mode.** Every connection sets `PRAGMA journal_mode = WAL` before any other operation. WAL (Write-Ahead Logging) separates reads from writes: the writer appends to a WAL file while readers continue to see a consistent snapshot of the main database. This eliminates the primary source of contention — readers no longer block on the writer, and the writer no longer blocks on readers. WAL mode is a persistent, database-level setting (stored in the DB header), but we set it on every connection for robustness — if the file is recreated or the header is corrupted, it gets re-applied.

**Layer 2 — Busy timeout.** Every connection sets `PRAGMA busy_timeout = 5000` (5 seconds). Even under WAL, brief exclusive locks occur during automatic **WAL checkpoints** (when SQLite transfers accumulated WAL pages back into the main DB). The busy timeout tells SQLite to retry internally with small sleeps for up to 5 seconds before surfacing an error. This handles the vast majority of transient checkpoint conflicts without any application-level logic.

**Layer 3 — Export-level retry with exponential backoff.** If a write still fails after the 5-second busy timeout (e.g. an unusually long checkpoint, or Datasette holding a long read transaction that blocks checkpoint), the exporter retries the entire batch up to 3 times with exponential backoff: 100 ms, 200 ms, 400 ms. Only `OperationalError` with "locked" in the message triggers a retry — all other errors (corrupt DB, schema mismatch, disk full) fail immediately. On persistent failure, the exporter returns `SpanExportResult.FAILURE` and logs the error; the BatchSpanProcessor will discard the batch. This is acceptable — telemetry is best-effort, and dropping a batch under extreme contention is preferable to blocking the chat session.

##### Connection Lifecycle

The exporter uses **short-lived connections** — a new connection is opened for each `export()` call and closed when the context manager exits. This is deliberate:

- `BatchSpanProcessor` calls `export()` from a background thread on a timer (default ~5 s interval). Between calls, there is no reason to hold a connection open.
- Short-lived connections avoid stale WAL readers that can prevent checkpoint progress.
- The `_connect()` helper centralizes pragma setup so both `_init_db()` and `export()` get identical connection configuration.

##### Two-Phase Export

The export method separates **serialization** from **writing**:

1. **Phase 1 (no DB lock):** Iterate over all `ReadableSpan` objects and serialize them into a list of tuples — hex IDs, duration calculation, JSON-encoded attributes/events/resource. This is pure CPU work that requires no database access.
2. **Phase 2 (minimal lock window):** Open a connection, write all pre-built rows in a single `executemany` call, and commit. The write lock is held only for the actual INSERT duration, not for serialization.

This minimizes the time the database file is locked, reducing the chance of collision with concurrent readers.

##### Failure Semantics

| Scenario | Outcome |
|----------|---------|
| Reader queries during writer's INSERT | WAL mode: reader succeeds (sees pre-write snapshot) |
| Writer INSERTs during WAL checkpoint | busy_timeout: writer retries internally for up to 5 s |
| Checkpoint blocked by long-running reader | Export retry: up to 3 attempts with backoff, then FAILURE |
| Non-lock error (disk full, corruption) | Immediate FAILURE, logged — no retry |
| Persistent FAILURE | BatchSpanProcessor drops the batch; subsequent batches retry independently |

Span loss is tolerable — telemetry is observational. The system never blocks the chat loop or crashes on export failure.

##### Alternatives Considered

- **Shared-cache mode (`cache=shared`):** Would allow connection-level locking instead of file-level, but adds complexity and is unnecessary once WAL handles the read/write split.
- **Separate DB per session, merge on read:** Eliminates write contention entirely, but complicates the reader queries (union across files) and the tail viewer (which file to poll). Overkill for our single-writer workload.
- **`wal_autocheckpoint = 0` (manual checkpoints):** Would prevent automatic checkpoints from blocking the writer mid-export, but risks unbounded WAL growth during long chat sessions. The default auto-checkpoint (every 1000 pages) is a reasonable trade-off.
- **Persistent connection on the exporter:** Would avoid per-export connection overhead, but a long-lived WAL reader can prevent checkpoint progress and grow the WAL file indefinitely. Short-lived connections are safer for our fire-and-forget export pattern.

### 3. Trace Viewer (`trace_viewer.py`)

Generates static HTML with nested, collapsible spans.

#### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    generate_trace_html()                         │
└─────────────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│ Query recent  │ │ Query span    │ │ Query tool    │
│ trace_ids     │ │ count         │ │ count         │
│ (LIMIT 20)    │ │               │ │               │
└───────┬───────┘ └───────┬───────┘ └───────┬───────┘
        │                 │                 │
        ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ For each trace_id:                                               │
│  1. SELECT * FROM spans WHERE trace_id = ?                      │
│  2. build_span_tree(spans)  ──▶  nested structure               │
│  3. render_span(root, ...)  ──▶  HTML string (recursive)        │
│  4. Wrap in TRACE_TEMPLATE                                      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ HTML_TEMPLATE.format(                                            │
│   trace_count = 2,                                               │
│   span_count = 79,                                               │
│   tool_count = 50,                                               │
│   traces_html = "<div class='trace'>...</div>..."               │
│ )                                                                │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │ ~/.local/share/co-cli │
              │    /traces.html       │
              └───────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │   webbrowser.open()   │
              └───────────────────────┘
```

#### Span Tree Building

```
Flat spans from DB:
┌────────────────────────────────────────────────────────────────┐
│ id=A, parent=null, name="agent run"                            │
│ id=B, parent=A,    name="chat model"                           │
│ id=C, parent=A,    name="running tools"                        │
│ id=D, parent=C,    name="running tool"                         │
│ id=E, parent=A,    name="chat model"                           │
└────────────────────────────────────────────────────────────────┘
                          │
                          │ build_span_tree()
                          │
                          │ 1. Index by ID: {A: span, B: span, ...}
                          │ 2. Link children to parents
                          │ 3. Collect roots (parent=null)
                          │ 4. Sort by start_time
                          ▼
Nested tree structure:
┌────────────────────────────────────────────────────────────────┐
│ A: agent run                                                    │
│ ├── children: [                                                │
│ │   B: chat model                                              │
│ │   C: running tools                                           │
│ │   │   └── children: [                                        │
│ │   │       D: running tool                                    │
│ │   │   ]                                                      │
│ │   E: chat model                                              │
│ ]                                                              │
└────────────────────────────────────────────────────────────────┘
```

```python
def build_span_tree(spans: list[dict]) -> list[dict]:
    """Convert flat span list to nested tree using parent_id."""
    by_id = {s["id"]: s for s in spans}
    roots = []

    for span in spans:
        span["children"] = []
        parent_id = span.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(span)
        else:
            roots.append(span)

    # Sort children by start_time
    def sort_children(span):
        span["children"].sort(key=lambda s: s["start_time"] or 0)
        for child in span["children"]:
            sort_children(child)

    for root in roots:
        sort_children(root)

    return sorted(roots, key=lambda s: s["start_time"] or 0)
```

#### Recursive Span Rendering

```
render_span(A, depth=0)
│
├─▶ Render A's row: [▼] agent run         114s  UNSET
│   Render A's waterfall: [████████████████████████████████]
│
├─▶ Render children:
│   │
│   ├─▶ render_span(B, depth=1)
│   │   └─▶ [·] chat model        4s   UNSET
│   │       [███                              ]
│   │
│   ├─▶ render_span(C, depth=1)
│   │   ├─▶ [▼] running tools     5s   UNSET
│   │   │   [   ████                          ]
│   │   │
│   │   └─▶ Render children:
│   │       └─▶ render_span(D, depth=2)
│   │           └─▶ [·] running tool  5s  UNSET
│   │               [   ████                      ]
│   │
│   └─▶ render_span(E, depth=1)
│       └─▶ [·] chat model        3s   UNSET
│           [        ██                       ]
│
└─▶ Return combined HTML
```

**Waterfall Bar Calculation:**

```
Trace timeline (0-100%):
|─────────────────────────────────────────────────────────────────|
0%                            50%                               100%

Span A (root): starts at 0, duration = 100% of trace
|█████████████████████████████████████████████████████████████████|
 bar_left=0%                                          bar_width=100%

Span B: starts at 5%, duration = 10% of trace
     |███████|
     bar_left=5%  bar_width=10%

Span D (nested): starts at 20%, duration = 15% of trace
                    |██████████████|
                    bar_left=20%  bar_width=15%

Formula:
  bar_left  = (span.start_time - trace_start) / trace_duration * 100
  bar_width = span.duration_ms / trace_duration * 100
```

```python
def render_span(span, depth, trace_start, trace_duration) -> str:
    """Render span and recursively render children."""
    span_type = get_span_type(span["name"])  # agent/tool/model

    # Calculate waterfall bar position (percentage of trace duration)
    bar_left = ((span["start_time"] - trace_start) / 1e6) / trace_duration * 100
    bar_width = (span["duration_ms"] or 0) / trace_duration * 100

    # Render children recursively
    children_html = ""
    if span["children"]:
        children_parts = [render_span(c, depth+1, ...) for c in span["children"]]
        children_html = f'<div class="span-children">{...}</div>'

    return SPAN_TEMPLATE.format(
        toggle_icon="▼" if children else "·",
        span_type=span_type,
        name=span["name"],
        duration=format_duration(span["duration_ms"]),
        status=span["status_code"],
        bar_left=bar_left,
        bar_width=bar_width,
        attributes_html=format_attributes(attrs),
        children_html=children_html,
    )
```

#### Collapsible UI (JavaScript)

```javascript
// Toggle trace spans
document.querySelectorAll('.trace-header').forEach(header => {
    header.addEventListener('click', () => {
        header.nextElementSibling.classList.toggle('collapsed');
    });
});

// Toggle span children
document.querySelectorAll('.span-toggle.has-children').forEach(toggle => {
    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const children = toggle.closest('.span').querySelector('.span-children');
        if (children) {
            children.classList.toggle('collapsed');
            toggle.textContent = children.classList.contains('collapsed') ? '▶' : '▼';
        }
    });
});

// Toggle span details on row click
document.querySelectorAll('.span-row').forEach(row => {
    row.addEventListener('click', () => {
        const details = row.parentElement.querySelector('.span-details');
        if (details) details.classList.toggle('expanded');
    });
});

// Toggle truncated attribute values (expand/collapse)
document.querySelectorAll('.show-more').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const attrValue = btn.closest('.span-attr-value');
        attrValue.classList.toggle('expanded');
        btn.textContent = attrValue.classList.contains('expanded') ? '[collapse]' : '[expand]';
    });
});
```

---

## Database Schema

**Location:** `~/.local/share/co-cli/co-cli.db`

```sql
CREATE TABLE spans (
    id TEXT PRIMARY KEY,           -- 16-char hex span ID
    trace_id TEXT NOT NULL,        -- 32-char hex trace ID
    parent_id TEXT,                -- Parent span ID (null for root)
    name TEXT NOT NULL,            -- Operation name
    kind TEXT,                     -- INTERNAL, CLIENT, SERVER, etc.
    start_time INTEGER NOT NULL,   -- Nanoseconds since epoch
    end_time INTEGER,              -- Nanoseconds since epoch
    duration_ms REAL,              -- Calculated duration
    status_code TEXT,              -- OK, ERROR, UNSET
    status_description TEXT,       -- Error message if failed
    attributes TEXT,               -- JSON: tool args, tokens, etc.
    events TEXT,                   -- JSON: span events
    resource TEXT                  -- JSON: service.name, version
);

CREATE INDEX idx_spans_trace ON spans(trace_id);
CREATE INDEX idx_spans_parent ON spans(parent_id);
CREATE INDEX idx_spans_start ON spans(start_time DESC);
```

---

## Instrumentation Version

We use `InstrumentationSettings(version=3)` for full **OTel GenAI semantic conventions v1.37.0** compliance:

```python
from pydantic_ai.models.instrumented import InstrumentationSettings

Agent.instrument_all(InstrumentationSettings(
    tracer_provider=tracer_provider,
    version=3,  # Full OTel GenAI spec compliance
))
```

| Version | Status | Span Names | Attributes |
|---------|--------|------------|------------|
| 1 | Legacy (deprecated) | `agent run` | `logfire.*` style, event-based |
| 2 | Default | `agent run`, `running tool` | `gen_ai.*` + `tool_arguments`, `tool_response` |
| 3 | **Spec compliant** | `invoke_agent {name}`, `execute_tool {name}` | `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |

Version 3 attributes on spans:
- `gen_ai.input.messages` / `gen_ai.output.messages` - model request/response
- `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result` - tool calls (spec compliant names)
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` - token usage
- `pydantic_ai.all_messages` - full conversation on agent run spans

**Note:** pydantic-ai still emits `logfire.msg` and `logfire.json_schema` metadata attributes internally. These are filtered out in the trace viewer display (not critical data - just schema metadata for Logfire UI).

---

## What Gets Traced

Pydantic-ai automatically emits spans following [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/):

| Span Name (v3) | Kind | Key Attributes |
|-----------|------|----------------|
| `invoke_agent {name}` | INTERNAL | model_name, agent_name, gen_ai.usage.input/output_tokens, pydantic_ai.all_messages |
| `chat {model}` | CLIENT | gen_ai.request.model, gen_ai.response.finish_reasons, gen_ai.input/output.messages |
| `running tools` | INTERNAL | tools (list of tool names in batch) |
| `execute_tool {name}` | INTERNAL | gen_ai.tool.name, gen_ai.tool.call.arguments, gen_ai.tool.call.result |

### Example Span Hierarchy

```
invoke_agent agent (114s)
├── chat glm-4.7-flash:q8_0 (4s)
├── running tools (5s)
│   └── execute_tool run_shell_command (5s)
├── chat glm-4.7-flash:q8_0 (3s)
├── running tools (2s)
│   └── execute_tool list_notes (2s)
└── chat glm-4.7-flash:q8_0 (2s)
```

---

## CLI Commands

### `co logs` - Datasette (Table View)

```bash
uv run co logs
# Opens http://127.0.0.1:8001
```

- Raw table view with JSON columns
- `datasette-pretty-json` plugin for formatting
- SQL editor for custom queries
- Auto-opens browser

### `co traces` - Nested HTML View

```bash
uv run co traces
# Opens ~/.local/share/co-cli/traces.html
```

- Visual nested span hierarchy (like Logfire)
- **Collapsible traces** - click header to expand/collapse
- **Collapsible spans** - click ▼ to expand/collapse children
- **Click span row** - show/hide attributes
- **Expandable long values** - click [expand] to see full JSON (pretty-printed)
- Waterfall timing bars showing relative duration
- Color-coded: agent (cyan), tool (orange), model (purple)
- No server required (static HTML)

#### Attribute Display

Long attribute values (>200 chars) are truncated with an [expand] button:

```
tool_response: {"files": ["note1.md", "note2.md"...  [expand]
```

Clicking [expand] reveals the full value, pretty-printed if valid JSON:

```json
{
  "files": [
    "note1.md",
    "note2.md",
    "design-patterns.md"
  ]
}
```

**Design rationale:** OTel spec defaults to no attribute size limit. Since we store locally (no network/backend constraints), we keep full data in SQLite and truncate only in the UI with expand option. This follows the principle: store everything, display smartly.

---

## Example SQL Queries

```sql
-- Recent root spans (one per agent.run())
SELECT trace_id, name, duration_ms, status_code
FROM spans WHERE parent_id IS NULL
ORDER BY start_time DESC LIMIT 10;

-- Tool calls with responses (v3 span naming)
SELECT
    datetime(start_time/1e9, 'unixepoch', 'localtime') as time,
    json_extract(attributes, '$.gen_ai.tool.name') as tool,
    json_extract(attributes, '$.gen_ai.tool.call.arguments') as args,
    json_extract(attributes, '$.gen_ai.tool.call.result') as response,
    duration_ms
FROM spans
WHERE name LIKE 'execute_tool%'
ORDER BY start_time DESC;

-- Token usage by model (v3 span naming)
SELECT
    json_extract(attributes, '$.model_name') as model,
    COUNT(*) as runs,
    SUM(json_extract(attributes, '$.gen_ai.usage.input_tokens')) as input_tokens,
    SUM(json_extract(attributes, '$.gen_ai.usage.output_tokens')) as output_tokens
FROM spans
WHERE name LIKE 'invoke_agent%'
GROUP BY model;

-- Slowest operations
SELECT name, duration_ms, trace_id
FROM spans
ORDER BY duration_ms DESC
LIMIT 20;

-- Errors
SELECT * FROM spans WHERE status_code = 'ERROR';

-- Full trace reconstruction
SELECT * FROM spans
WHERE trace_id = 'abc123...'
ORDER BY start_time;
```

---

## Files

| Path | Purpose |
|------|---------|
| `co_cli/telemetry.py` | SQLiteSpanExporter (OTel → SQLite) |
| `co_cli/trace_viewer.py` | HTML generator (collapsible nested view) |
| `co_cli/datasette_metadata.json` | Datasette UI config |
| `~/.local/share/co-cli/co-cli.db` | Span storage |
| `~/.local/share/co-cli/traces.html` | Generated viewer (static, no server) |

---

## Privacy

- All data stays local (no external telemetry endpoints)
- Tool responses captured (may include sensitive command output)
- Full conversation history in `pydantic_ai.all_messages` attribute
- To clear all traces: `rm ~/.local/share/co-cli/co-cli.db`

---

## References

- [Pydantic AI Instrumentation](https://ai.pydantic.dev/logfire/)
- [Pydantic AI InstrumentationSettings API](https://ai.pydantic.dev/api/models/instrumented)
- [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) - Status: Development
- [OTel Python SDK](https://opentelemetry.io/docs/languages/python/)
- [OTel Data Model](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [OTel Attribute Limits Spec](https://opentelemetry.io/docs/specs/otel/common/) - Default `AttributeValueLengthLimit=Infinity`
