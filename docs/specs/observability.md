# Observability — Tracing and Viewers


## 1. What & How

co-cli emits structured JSON-line trace records to a local log file. No OpenTelemetry SDK, no external collector, no embedded database — span data is appended to `~/.co-cli/logs/co-cli-spans.jsonl` one record per line. Two viewers consume it: a live `tail -f`-style stream and a snapshot tree of one trace.

```
┌──────────────────────────────────────────────────────────────┐
│                         co CLI                                │
│                                                                │
│   Agent.run() ──▶ Model Call ──▶ Tool Execution                │
│        │               │               │                       │
│        ▼               ▼               ▼                       │
│   ObservabilityCapability  +  CoToolLifecycle  +  @trace(...)  │
│                           (push/pop spans)                     │
│                              │                                 │
│                              ▼                                 │
│             logging.getLogger("co_cli.observability.spans")    │
│              propagate=False · RotatingFileHandler             │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
            ~/.co-cli/logs/co-cli-spans.jsonl
                               │
                ┌──────────────┴─────────────┐
                ▼                            ▼
            co tail                  co trace <trace_id>
        (live append-only)            (snapshot tree)
```

Run `co chat` in one terminal and `co tail` in another to watch the agent→model→tool flow live:

```
┌──────────────────────┐       ┌──────────────────────────┐
│  Terminal A           │       │  Terminal B               │
│  $ co chat            │       │  $ co tail --detail       │
│  co > search my notes │  ───▶ │  14:23:05  model  chat    │
│                       │       │  14:23:06  tool   search  │
│  Found 3 notes...     │       │  14:23:07  model  chat    │
└──────────────────────┘       └──────────────────────────┘
                                       ▲ polls the JSONL log
                              ~/.co-cli/logs/co-cli-spans.jsonl
```

## 2. Core Logic

### Instrumentation Setup (`main.py`)

Telemetry is bootstrapped at module load time, before any agent is created. Two separate rotating JSONL streams are configured: one for application log records, one for spans.

```
setup_file_logging(LOGS_DIR, level, max_size_mb, backup_count)        # co-cli.jsonl (app log)
setup_spans_log(LOGS_DIR / "co-cli-spans.jsonl",                       # co-cli-spans.jsonl
                max_size_mb=spans_log_max_size_mb,                     # spans only
                backup_count=spans_log_backup_count,
                redact_patterns=settings.observability.redact_patterns)
for logger_name in ["openai", "httpx", "anthropic", "hpack"]:          # co_cli.* loggers unaffected
    logging.getLogger(logger_name).setLevel(WARNING)
```

`setup_spans_log()` (in `tracing.py`) installs a `RotatingFileHandler` on the dedicated `co_cli.observability.spans` logger with `propagate=False`, so span output never appears in the application log. Application logging (`co-cli.jsonl`) and span logging (`co-cli-spans.jsonl`) are two disjoint streams.

### Span lifecycle (`tracing.py`)

`tracing.py` is the single observability primitive used across the codebase. Three ContextVars hold per-task state: `_SESSION_ID`, `_TRACE_ID`, and `_SPAN_STACK` (a tuple of in-flight span dicts). Spans are pushed on entry, mutated via a proxy, and emitted as one JSON record on pop.

```
push_span(name, kind, attributes)         # capability hooks + @trace
    ↓ appends span dict to _SPAN_STACK
    ↓ parent_span_id = previous top (None for root)
    ↓ trace_id from _TRACE_ID (lazy-generated on first push)

current_span().set_attribute(k, v)        # mutates top-of-stack
current_span().add_event(name, attrs)     # appends event to top-of-stack

pop_span(status, status_msg, attributes)  # capability hooks + @trace
    ↓ pops top-of-stack
    ↓ computes duration_ms from start_perf
    ↓ redacts string values (attributes, events, status_msg)
    ↓ emits one JSON line via logging.getLogger("co_cli.observability.spans")
```

When the stack is empty, `current_span()` returns a `_NoOpSpan` that debug-logs each call but never raises — observability code must never break business logic.

### The `@trace` decorator

`@trace(name=None, *, new_trace=False)` wraps any sync or async function. It detects coroutine functions via `inspect.iscoroutinefunction(func)` and dispatches to the right wrapper. On entry it pushes a span; on exit it pops and emits, with `status="ERROR"` and `status_msg=str(exc)` on exception (then re-raises).

The `new_trace=True` flag resets `trace_id` BEFORE pushing — used at the top of each user turn (`@trace("co.turn", new_trace=True)`) so the `co.turn` span itself carries the fresh trace_id and all its children inherit it.

`run_with_context(fn, *args, **kwargs)` captures the current `contextvars.Context` and returns a 0-arg callable. Use it to bridge `loop.run_in_executor` calls so the worker thread sees the parent task's span stack and session_id.

### `ObservabilityCapability` (`capability.py`)

`ObservabilityCapability(AbstractCapability[CoDeps])` hooks into pydantic-ai's agent/model/tool lifecycle. Replaces `Agent.instrument_all(InstrumentationSettings(tracer_provider=...))`. Wired into `agent/build.py`'s `capabilities=[ObservabilityCapability(), CoToolLifecycle()]` list.

| Hook | Action |
|------|--------|
| `before_run` | `push_span("invoke_agent {name}", kind="agent", attrs={"co.agent.role", "co.agent.model", "co.agent.request_limit"})` |
| `after_run` | `pop_span(attrs={"co.agent.requests_used", "co.agent.final_result"})` |
| `on_run_error` | `pop_span(status="ERROR", status_msg=str(error))`, then re-raise |
| `before_model_request` | `push_span("chat {model}", kind="model", attrs={"co.model.name", "co.model.input"})` |
| `after_model_request` | `pop_span(attrs={"co.model.output", "co.model.tokens.input/output", "co.model.name", "co.model.finish_reason"})` |
| `on_model_request_error` | `pop_span(status="ERROR", ...)`, then re-raise |
| `before_tool_execute` | `push_span("tool {name}", kind="tool", attrs={"co.tool.name", "co.tool.args"})` |
| `after_tool_execute` | `pop_span(attrs={"co.tool.result"})` |
| `on_tool_execute_error` | `pop_span(status="ERROR", ...)`, then re-raise |

**Capability ordering invariant.** pydantic-ai's `CombinedCapability` calls `before_*` in forward declaration order and `after_*` / `on_*_error` in reverse (LIFO). With `[ObservabilityCapability(), CoToolLifecycle()]`:

- `before_tool_execute`: Observability pushes span first; `CoToolLifecycle` runs inside it.
- `after_tool_execute`: `CoToolLifecycle` runs FIRST, attaching `co.tool.source` / `co.tool.requires_approval` / `co.tool.result_size` via `current_span().set_attribute(...)` while the tool span is still active; THEN Observability closes the span.

If this order were reversed, `CoToolLifecycle.after_tool_execute` would land attribute writes on a no-op proxy. The wiring site in `agent/build.py` is commented to preserve this invariant.

### Record schema

One JSON object per closed span, one line per record. Schema version 1:

```json
{
  "ts": "2026-05-17T19:30:00.123456Z",
  "schema_version": 1,
  "session_id": "a1b2c3d4",
  "trace_id": "t_e5f6g7h8...",
  "span_id": "s_i9j0k1l2...",
  "parent_span_id": "s_xxxx",
  "name": "co.turn",
  "kind": "agent",
  "start_ts": "2026-05-17T19:30:00.000123Z",
  "duration_ms": 123.456,
  "status": "OK",
  "status_msg": null,
  "attributes": { ... },
  "events": [ { "ts": "...", "name": "...", "attributes": {...} } ]
}
```

| Field | Use |
|-------|-----|
| `ts` | Emission timestamp; used by `co tail` for ordering across rotation |
| `schema_version` | Forward compatibility |
| `session_id` | Filter / correlation; matches `co chat` session boundary |
| `trace_id` | Group records into a trace for `co trace <id>` and `--trace` filter |
| `span_id` | Parent linkage target |
| `parent_span_id` | Tree assembly; null for trace roots |
| `name` | Display + filter |
| `kind` | Display color, `--tools-only` / `--models-only` filter — one of `agent`, `model`, `tool`, `co` |
| `start_ts` | Sibling ordering in tree view (record `ts` is close-time) |
| `duration_ms` | Summary line, performance triage |
| `status` | `OK` / `ERROR` |
| `status_msg` | Error context; null on OK; populated by `on_*_error` hooks. Distinct from Python exception tracebacks (those go to the app log) |
| `attributes` | Per-record payload |
| `events` | Nested mini-records for "thing happened with attributes but no duration to measure" (converted from former zero-duration spans) |

### Redaction

`settings.observability.redact_patterns` continues to work. Patterns apply to string values inside `attributes`, `events[*].attributes`, AND `status_msg` (exception messages can echo tool args back). Algorithm: for each string value, apply each compiled pattern's `.sub("[REDACTED]", ...)`. If a string value `json.loads()` cleanly, walk the parsed Python structure fully recursively (a JSON tree is bounded) applying per-leaf regex, then re-serialize. This catches secrets nested inside `co.model.input` / `co.model.output` where content sits multiple levels deep.

### Rotation safety

`RotatingFileHandler.doRollover` is synchronous and holds the handler's lock — concurrent writes from other threads queue on the lock. Records in flight at rollover are not lost. `co tail`'s follow loop detects rotation via inode change (`os.stat().st_ino`) and re-opens the new file from offset 0.

### What gets traced

| Span name | Kind | Key attributes |
|-----------|------|----------------|
| `co.turn` | `agent` (wrapping) | `co.user_prompt.chars`, `turn.outcome` (`continue`/`error`), `turn.interrupted` (bool), `turn.input_tokens`, `turn.output_tokens`, `turn.model_requests` (int) — root span for every user turn; `@trace("co.turn", new_trace=True)` on `run_turn()`. On terminal `ModelHTTPError`, adds a `provider_error` event with `http.status_code` and `error.body` (capped at 500 chars). |
| `invoke_agent {name}` | `agent` | `co.agent.role`, `co.agent.model`, `co.agent.request_limit`, `co.agent.requests_used`, `co.agent.final_result` — emitted by `ObservabilityCapability.before_run`/`after_run`. |
| `chat {model}` | `model` | `co.model.name`, `co.model.input` (JSON list of message dicts preserving role + part types incl. `thinking`), `co.model.output` (same shape), `co.model.tokens.input`, `co.model.tokens.output`, `co.model.finish_reason` — emitted by `ObservabilityCapability.before_model_request`/`after_model_request`. |
| `tool {name}` | `tool` | `co.tool.name`, `co.tool.args` (JSON string), `co.tool.result` (JSON string, size-capped), `co.tool.result_size`, `co.tool.source` (`native`/`mcp`), `co.tool.requires_approval` (bool), `co.tool.args_chars` — `co.tool.{name,args,result}` from `ObservabilityCapability`, the rest from `CoToolLifecycle` via `current_span().set_attribute()`. |
| `background_task_execute` | `co` | `task.command`, `task.description`, `task.cwd` — `@trace("background_task_execute")` on `task_start`. |
| `tool_budget.resolved` | `co` | `budget.context_window_tokens`, `budget.spill_ratio`, `budget.tool_call_limit`, `budget.spill_threshold_chars`, `budget.spill_threshold_tokens` — emitted once at bootstrap by `@trace("tool_budget.resolved")` on `_emit_tool_budget_span()`. |
| `sync_memory` | `co` | `count`, `backend`, `status` — `@trace("sync_memory")` on `_sync_memory_domain()`. |
| `restore_session` | `co` | `status` (`restored`/`new`), `session_id` — `@trace("restore_session")` on `restore_session()`. |
| `co.housekeeping.pass` | `co` | whole-pass envelope — `@trace("co.housekeeping.pass")` on `run_housekeeping()`. Wraps merge + decay under `asyncio.timeout(max_pass_seconds)`. |
| `co.housekeeping.merge` | `co` | merge phase — `@trace("co.housekeeping.merge")` on `merge_memory()`. |
| `co.housekeeping.decay` | `co` | decay phase — `@trace("co.housekeeping.decay")` on `decay_memory()`. |
| `co.memory.{memory_create,memory_mutate,memory_delete}` | `co` | `memory.memory_kind`, `memory.filename_stem`, `memory.action` — `@trace(...)` on `_handle_{create,mutate,delete}()`. |
| `co.web_research.retry_loop` | `co` | `agent.role`, `agent.model`, `agent.request_limit`, `agent.requests_used` — `@trace("co.web_research.retry_loop")` on `web_research()` retry-on-empty wrapper. |
| `compaction.proactive_check` | `co` | `compaction.msgs`, `compaction.token_count`, `compaction.threshold`, `compaction.budget`, `compaction.fired` (bool), `compaction.skip_reason`, `compaction.tokens_after`, `compaction.savings_pct`, etc. — `@trace("compaction.proactive_check")` on `proactive_window_processor()`. |

### Events on existing spans

These small attribute-only blocks were previously zero-duration spans; they are now events attached to whatever span is active when they fire:

| Event name | Attached to | Attributes |
|------------|-------------|-----------|
| `ctx_overflow_check` | active `co.turn` span | `ctx.input_tokens`, `ctx.max_ctx`, `ctx.ratio` |
| `tool_budget.dedup_tool_calls` | active span | `dedup.parts_before`, `dedup.parts_after`, `dedup.dropped` |
| `tool_budget.enforce_tool_call_limit` | active span | `tool_calls.limit`, `tool_calls.issued`, `tool_calls.allowed`, `tool_calls.rejected`, `tool_calls.limit_exceeded` |
| `tool_budget.spill_tool_result` | active span | `tool.name`, `spill.threshold_chars`, `spill.content_chars`, `spill.fired`, `spill.forced`, `spill.savings_chars` |
| `tool_budget.enforce_request_size` | active model span | `request.threshold_tokens`, `request.tokens_before`, `request.tokens_after`, `request.spilled_count`, `request.spill_fired`, `request.skip_reason` (one of `""`, `below_threshold`, `no_candidates`, `all_spilled`, `fallback_to_summarize`) |
| `provider_error` | active `co.turn` span | `http.status_code`, `error.body` (capped at 500 chars) |

### Live Tail Viewer (`co tail`)

`co tail` follows the JSONL spans log — like `tail -f` for agent traces. No DB query, no per-record SQL.

**Startup:** read the last N lines (`--last`, default 20), apply filters, render.

**Follow loop:** sleep `--poll` seconds (default 0.1), re-stat the file. If the inode changed (rotation), open the new file from offset 0. Read available new lines from the current byte offset, parse each as JSON, render. Each rendered line carries timestamp, kind, name, key attributes, and duration; `ERROR` status appends a red marker.

**`--detail` mode** (replaces the old `--verbose`):
- **agent**: shows `co.agent.final_result` via `[final]`.
- **model**: shows the last user message from `co.model.input`, and renders `co.model.output` parts — `thinking` blocks dim italic, `text` parts as `[response]`, `tool_call` parts as `[tool_call] <name>`.
- **tool**: pretty-prints `co.tool.args` and `co.tool.result` JSON.

**Rich color scheme:**

| Type | Rich style |
|------|-----------|
| agent | `cyan` |
| model | `magenta` |
| tool | `yellow` |
| co | `white` |
| error | `bold red` |

### Snapshot Tree (`co trace <trace_id>`)

`co trace <trace_id>` reads all records matching one `trace_id` from the live spans log plus any rotated backups (glob `co-cli-spans.jsonl*`), groups by `parent_span_id`, sorts siblings by `start_ts`, and renders a depth-uncapped indented tree. One-shot snapshot — no follow.

Distinct from `co tail`: tail is append-only / live; `trace` is a tree question over completed records.

### Querying with jq

```bash
# Recent root spans (trace roots — parent_span_id null)
jq 'select(.parent_span_id == null)' ~/.co-cli/logs/co-cli-spans.jsonl | tail -10

# Tool calls with name and duration
jq 'select(.kind == "tool") | {time: .start_ts, tool: .attributes."co.tool.name", duration_ms}' \
    ~/.co-cli/logs/co-cli-spans.jsonl

# Token usage by model
jq -s '[.[] | select(.kind == "agent")] | group_by(.attributes."co.agent.model") |
    map({model: .[0].attributes."co.agent.model",
         runs: length,
         requests: (map(.attributes."co.agent.requests_used") | add)})' \
    ~/.co-cli/logs/co-cli-spans.jsonl

# Provider errors on co.turn
jq 'select(.name == "co.turn" and .status == "ERROR") |
    {time: .start_ts, error: .events[] | select(.name == "provider_error") | .attributes}' \
    ~/.co-cli/logs/co-cli-spans.jsonl
```

### Troubleshooting

| Issue | Command | What to look for |
|-------|---------|-----------------|
| Agent stuck in tool loop | `co tail --detail` | Repeating `chat → tool` without `stop` finish reason |
| Context growing too large | `co tail --models-only` | `in=` token count growing each model call |
| Tool returning errors | `co tail --tools-only` | `ERROR` on tool records |
| Records not appearing | Check for an active run | Spans emit on close; running operations not yet visible |

### Privacy

All data stays local. Tool responses and full conversation history are captured in span attributes. Before any record is written, the redaction pipeline applies regex substitutions to every string value in `attributes`, `events[*].attributes`, and `status_msg` — including string-encoded JSON values, which are parsed and walked recursively. The default pattern set covers common secret formats (OpenAI/Anthropic `sk-*` keys, Bearer tokens, GitHub `ghp_` tokens, generic `api_key=` pairs, AWS AKIA IDs, PEM private key headers). There is no built-in retention or pruning policy beyond the rotating handler's backup count — to clear all spans: `rm ~/.co-cli/logs/co-cli-spans.jsonl*`.

## 3. Config

### File Logging (`observability` settings group)

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `observability.log_level` | `CO_LOG_LEVEL` | `INFO` | Minimum level written to `co-cli.jsonl` (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `observability.log_max_size_mb` | `CO_LOG_MAX_SIZE_MB` | `5` | Max app-log file size in MB before rotation (1–500) |
| `observability.log_backup_count` | `CO_LOG_BACKUP_COUNT` | `3` | Rotated app-log backups to keep (0–20) |
| `observability.spans_log_max_size_mb` | `CO_SPANS_LOG_MAX_SIZE_MB` | `50` | Max spans-log file size in MB before rotation (1–2000); defaults higher than the app log because span volume is higher |
| `observability.spans_log_backup_count` | `CO_SPANS_LOG_BACKUP_COUNT` | `5` | Rotated spans-log backups to keep (0–50) |
| `observability.redact_patterns` | — | 6 default patterns | Regex list applied to string values before write; extend via `settings.json` for custom secret formats |

### `co tail` Flags

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--trace` | `-i` | None | Filter to a specific trace ID |
| `--tools-only` | `-T` | `False` | Only show tool spans |
| `--models-only` | `-m` | `False` | Only show model spans |
| `--poll` | `-p` | `0.1` | Poll interval in seconds |
| `--no-follow` | `-n` | `False` | Print recent spans and exit |
| `--last` | `-l` | `20` | Number of recent records shown on startup |
| `--detail` | `-d` | `False` | Append per-record detail block (input/output/args/result) |

### `co trace` Args

| Arg | Default | Description |
|-----|---------|-------------|
| `trace_id` | required | The trace ID to render as a snapshot tree |

## 4. Public Interface

### Bootstrap setup

| Symbol | Source | Contract |
|--------|--------|---------|
| `setup_log(log_path, *, max_size_mb, backup_count, redact_patterns) -> None` | `co_cli/observability/tracing.py` | Configures the `co_cli.observability.spans` rotating JSONL handler with `propagate=False`; compiles and stores redact patterns; idempotent |
| `setup_file_logging(logs_dir, log_level, log_max_size_mb, log_backup_count) -> None` | `co_cli/observability/file_logging.py` | Attaches two `RotatingFileHandler`s to root logger (`co-cli.jsonl` INFO+, `errors.jsonl` WARNING+); idempotent |

### Tracing primitives

| Symbol | Source | Contract |
|--------|--------|---------|
| `@trace(name=None, *, new_trace=False)` | `co_cli/observability/tracing.py` | Decorator for sync or async functions; emits one span per call. `new_trace=True` resets `trace_id` before push so the decorated function's span carries the fresh id |
| `current_span() -> _Span \| _NoOpSpan` | `co_cli/observability/tracing.py` | Proxy over the top-of-stack span; `.set_attribute(k, v)`, `.add_event(name, attrs)`, `.set_status(status, msg)`; no-op proxy when stack empty (debug-logs each call) |
| `push_span(name, *, kind="co", attributes) -> dict` | `co_cli/observability/tracing.py` | Explicit span management for capability hooks; returns the span dict for identity-based cleanup |
| `pop_span(*, status="OK", status_msg=None, attributes=None) -> None` | `co_cli/observability/tracing.py` | Pops top-of-stack span, applies attributes/status, redacts, emits one JSON record |
| `new_trace() -> str` | `co_cli/observability/tracing.py` | Generates a fresh 16-hex `trace_id` and binds it to the contextvar; existing spans on the stack keep their own id |
| `set_session_context(session_id)` / `clear_session_context()` | `co_cli/observability/tracing.py` | Bind/clear the `session_id` contextvar |
| `run_with_context(fn, *args, **kwargs) -> Callable[[], Any]` | `co_cli/observability/tracing.py` | Captures the current Context and returns a 0-arg callable; pass to `loop.run_in_executor` to carry the span/trace/session context across the thread boundary |

### Capability

| Symbol | Source | Contract |
|--------|--------|---------|
| `ObservabilityCapability(AbstractCapability[CoDeps])` | `co_cli/observability/capability.py` | Hooks `before_run`/`after_run`/`on_run_error`, `before_model_request`/`after_model_request`/`on_model_request_error`, `before_tool_execute`/`after_tool_execute`/`on_tool_execute_error` — pushes/pops spans and emits agent/model/tool records. Must be placed FIRST in `capabilities=[...]` so `CoToolLifecycle` can attach attributes via `current_span()` before the tool span closes |

### Viewers (CLI entrypoints)

| Symbol | Source | Contract |
|--------|--------|---------|
| `co tail` | `co_cli/main.py` → `co_cli/observability/tail.py:run_tail` | Reads and follows the JSONL spans log; rotation-safe via inode tracking; filters by trace/tool/model kind; summary or `--detail` |
| `co trace <trace_id>` | `co_cli/main.py` → `co_cli/observability/trace_view.py:render_trace` | Reads all records for one trace from the live log and rotated backups; builds parent/child tree; renders indented snapshot |

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/observability/tracing.py` | `setup_log`, `@trace` decorator, `current_span`, `push_span`/`pop_span`, `new_trace`, `set_session_context`/`clear_session_context`, `run_with_context`; contextvars-based span stack; redaction pipeline; JSON-line emit via dedicated logger |
| `co_cli/observability/capability.py` | `ObservabilityCapability` — pydantic-ai capability emitting agent/model/tool records on lifecycle hooks; replaces `Agent.instrument_all(...)` |
| `co_cli/observability/file_logging.py` | `setup_file_logging()` — attaches two rotating JSONL handlers to root logger: `co-cli.jsonl` (INFO+) and `errors.jsonl` (WARNING+, 2 MB/2 backups hardcoded) |
| `co_cli/observability/tail.py` | `run_tail()` — JSONL follow loop, per-kind attribute extraction, `--detail` rendering for agent/model/tool |
| `co_cli/observability/trace_view.py` | `render_trace()` — snapshot tree builder; reads live log + rotated backups; sorts siblings by `start_ts`; depth-uncapped indented render |
| `co_cli/main.py` | `@app.command()` wrappers for `tail` and `trace`; module-level `_setup_observability()` bootstraps both file logging and spans logging |
| `co_cli/config/core.py` | `USER_DIR`, `LOGS_DIR` — user-global path constants |
| `co_cli/config/observability.py` | `ObservabilitySettings` — file-logging settings, spans-log settings, redaction patterns |
| `co_cli/agent/build.py` | Wires `[ObservabilityCapability(), CoToolLifecycle()]` into agent construction; ordering invariant documented at the wiring site |
| `~/.co-cli/logs/co-cli.jsonl` | Rotating app log — INFO+ Python `logging` records (`"kind": "log"`); independent stream from spans |
| `~/.co-cli/logs/co-cli-spans.jsonl` | Rotating spans log — one JSON line per closed span |
| `~/.co-cli/logs/errors.jsonl` | Rotating WARNING+ app log — 2 MB / 2 backups; for fast error triage |
