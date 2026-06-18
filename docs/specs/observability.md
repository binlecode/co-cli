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
│   run-call-site    SurrogateRecovery   _CallSeamToolset        │
│   agent span    +  Model chat span  +  tool span  +  @trace()  │
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

Telemetry is bootstrapped at module load time, before any agent is created. Both the main app and the dream daemon route through one shared coordinator, `setup_observability()` (in `observability/setup.py`), so the two processes can never drift in how they wire logging. Each passes its own filenames — the main app uses `co-cli*`, the dream daemon uses `co-dream*` — because `RotatingFileHandler` is not multi-process safe and the two processes run concurrently.

```
setup_observability(LOGS_DIR,                              # shared coordinator (observability/setup.py)
                    app_log_name="co-cli.jsonl",           # co-cli.jsonl (app log, INFO+)
                    spans_log_name="co-cli-spans.jsonl",   # co-cli-spans.jsonl (spans)
                    errors_log_name="errors.jsonl",        # errors.jsonl (WARNING+); None to skip
                    settings=settings)
# coordinator internally:
#   setup_file_logging(...)   → app log + (optional) errors log on root
#   setup_spans_log(...)      → span stream on the dedicated spans logger
#   for name in SUPPRESS_LOGGERS: getLogger(name).setLevel(WARNING)
#   SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]   (co_cli.* unaffected)
```

The dream daemon calls the same coordinator from `_run_foreground` with `app_log_name="co-dream.jsonl"`, `spans_log_name="co-dream-spans.jsonl"`, and `errors_log_name=None` (no dedicated dream errors file — WARNING+ records still land in the INFO+ `co-dream.jsonl`). `co tail` / `co trace` read only `co-cli-spans.jsonl`, so dream spans are `jq`-inspectable over `co-dream-spans.jsonl` rather than visible in the live viewers. See [dream.md](dream.md).

`setup_spans_log()` (in `tracing.py`) installs a `RotatingFileHandler` on the dedicated `co_cli.observability.spans` logger with `propagate=False`, so span output never appears in the application log. Application logging (`co-cli.jsonl`) and span logging (`co-cli-spans.jsonl`) are two disjoint streams.

### Span lifecycle (`tracing.py`)

`tracing.py` is the single observability primitive used across the codebase. Three ContextVars hold per-task state: `_SESSION_ID`, `_TRACE_ID`, and `_SPAN_STACK` (a tuple of in-flight span dicts). Spans are pushed on entry, mutated via a proxy, and emitted as one JSON record on pop.

```
push_span(name, kind, attributes)         # model/tool/agent span sites + @trace
    ↓ appends span dict to _SPAN_STACK
    ↓ parent_span_id = previous top (None for root)
    ↓ trace_id from _TRACE_ID (lazy-generated on first push)

current_span().set_attribute(k, v)        # mutates top-of-stack
current_span().add_event(name, attrs)     # appends event to top-of-stack

pop_span(status, status_msg, attributes)  # model/tool/agent span sites + @trace
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

### Span emission on co-owned seams

There is no pydantic-ai capability middleware. The agent/model/tool spans are pushed and popped on three explicit seams co already owns, each as straight-line ordered code — no `capabilities=[...]` attachment, no inter-component ordering invariant, no silent `_NoOpSpan` failure on reorder.

| Span | Seam | Push / pop |
|------|------|-----------|
| `invoke_agent {name}` (`agent`) | the run call site — `_execute_run` (`agent/orchestrate.py`) for the orchestrator, `run_standalone` (`agent/run.py`) for task agents | `push_span` with `co.agent.role`/`co.agent.model`/`co.agent.request_limit` before the run; `pop_span` with `co.agent.requests_used`/`co.agent.final_result` after (ERROR + re-raise on exception) |
| `chat {model}` (`model`) | `SurrogateRecoveryModel` (`llm/surrogate_recovery_model.py`), covering BOTH `request` (non-stream) and `request_stream` (streaming) | `push_span` with `co.model.name`/`co.model.input` on entry; `pop_span` with `co.model.output`/`co.model.tokens.input/output`/`co.model.name`/`co.model.finish_reason` once the response (or assembled stream) is read. On the streaming path the final response/usage is only available after the stream is consumed, so the span closes on context-manager exit reading `StreamedResponse.get()`/`.usage()` |
| `tool {name}` (`tool`) | `_CallSeamToolset.call_tool` (`agent/toolset.py`) | `push_span` with `co.tool.name`/`co.tool.args`/`co.tool.args_chars`; on close sets `co.tool.result`/`co.tool.result_size`/`co.tool.source`/`co.tool.requires_approval`, then `pop_span` (ERROR + re-raise on tool error) |

The tool span body is linear and ordered: push span → per-model-request cap check (rejection payload past the cap) → `super().call_tool(...)` → MCP-result spill if oversized → set `co.tool.*` → pop span. All three concerns that must live at the `call_tool` boundary (span, cap, spill) sit there together; nothing depends on the ordering of a separate component's hooks. See [tools.md](tools.md) for the cap and spill detail.

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
| `invoke_agent {name}` | `agent` | `co.agent.role`, `co.agent.model`, `co.agent.request_limit`, `co.agent.requests_used`, `co.agent.final_result` — pushed at the run call site (`_execute_run` for the orchestrator, `run_standalone` for task agents). |
| `chat {model}` | `model` | `co.model.name`, `co.model.input` (JSON list of message dicts preserving role + part types incl. `thinking`), `co.model.output` (same shape), `co.model.tokens.input`, `co.model.tokens.output`, `co.model.finish_reason` — emitted by `SurrogateRecoveryModel` on both the streaming and non-stream request paths. |
| `llm_call {model}` | `model` | Same attribute keys as `chat {model}` (BC-1 parity — renders identically) but emitted by the direct-call primitive `llm_call()` (`co_cli/llm/call.py`) via explicit `push_span`/`pop_span`, NOT the agent loop. Covers the compaction summarizer, dream merges, and eval judge calls. The distinct name keeps direct calls separable from agent turns; the span nests under the active parent (e.g. `compaction.proactive_check`). Reuses `serialize_messages`/`serialize_response` from `observability/serialize.py`. |
| `tool {name}` | `tool` | `co.tool.name`, `co.tool.args` (JSON string), `co.tool.result` (JSON string, size-capped), `co.tool.result_size`, `co.tool.source` (`native`/`mcp`), `co.tool.requires_approval` (bool), `co.tool.args_chars` — all set in one place by `_CallSeamToolset.call_tool` (`co_cli/agent/toolset.py`). |
| `background_task_execute` | `co` | `task.command`, `task.description`, `task.cwd` — `@trace("background_task_execute")` on `task_start`. |
| `tool_budget.resolved` | `co` | `budget.context_window_tokens`, `budget.spill_ratio`, `budget.tool_call_limit`, `budget.spill_threshold_chars`, `budget.spill_threshold_tokens` — emitted once at bootstrap by `@trace("tool_budget.resolved")` on `_emit_tool_budget_span()`. |
| `sync_memory` | `co` | `count`, `backend`, `status` — `@trace("sync_memory")` on `_sync_memory_domain()`. |
| `restore_session` | `co` | `status` (`restored`/`new`), `session_id` — `@trace("restore_session")` on `restore_session()`. |
| `co.housekeeping.pass` | `co` | whole-pass envelope — `@trace("co.housekeeping.pass")` on `run_housekeeping()`. Wraps merge under `asyncio.timeout(max_pass_seconds)`; carries the `co.housekeeping.memory_count_warn` event when the active memory count exceeds `MEMORY_ITEM_COUNT_WARN`. |
| `co.housekeeping.merge` | `co` | merge phase — `@trace("co.housekeeping.merge")` on `merge_memory()`. |
| `co.memory.{memory_create,memory_mutate,memory_delete}` | `co` | `memory.memory_kind`, `memory.filename_stem`, `memory.action` — `@trace(...)` on `_handle_{create,mutate,delete}()`. |
| `compaction.proactive_check` | `co` | `compaction.msgs`, `compaction.token_count`, `compaction.threshold`, `compaction.budget`, `compaction.fired` (bool), `compaction.skip_reason`, `compaction.tokens_after`, `compaction.savings_pct`, etc. — `@trace("compaction.proactive_check")` on `proactive_window_processor()`. |
| `index.search` | `co` | `co.index.query_len`, `co.index.sources`, `co.index.kinds`, `co.index.limit`, `co.index.hits`, `co.index.degraded` (sorted recall-degradation modes for this query — `semantic_unavailable` / `rerank_unavailable`; empty = healthy) — emitted per `IndexStore.search()` invocation (`co_cli/index/store.py`) so recall work (FTS5/BM25 + embedding + hybrid merge) is attributable under the `memory_search`/`session_search` tool span. `co.index.hits` is THIS invocation's returned count: a kinds-filtered `memory_search` calls `search()` twice → one span each, neither being the tool's final merged/capped list. |

### Events on existing spans

These small attribute-only blocks were previously zero-duration spans; they are now events attached to whatever span is active when they fire:

| Event name | Attached to | Attributes |
|------------|-------------|-----------|
| `ctx_overflow_check` | active `co.turn` span | `ctx.input_tokens`, `ctx.max_context_tokens`, `ctx.ratio` |
| `tool_budget.spill_tool_result` | active `tool` span | `tool.name`, `spill.threshold_chars`, `spill.content_chars`, `spill.fired`, `spill.forced`, `spill.savings_chars` |
| `tool_budget.spill_largest_tool_results` | active model span | `request.threshold_tokens`, `request.tokens_before`, `request.tokens_after`, `request.spilled_count`, `request.spill_fired`, `request.skip_reason` (one of `""`, `below_threshold`, `no_candidates`, `all_spilled`, `fallback_to_summarize`) |
| `provider_error` | active `co.turn` span | `http.status_code`, `error.body` (capped at 500 chars) |
| `surrogate_recovery` | active model span (`chat` or `llm_call`) | `method` (`request` / `request_stream`) — emitted when `SurrogateRecoveryModel` catches a `UnicodeEncodeError`, re-sanitizes, and retries (`co_cli/llm/surrogate_recovery_model.py`). Makes recovery frequency visible in the trace. |
| `compaction_fallback` | active `compaction.proactive_check` span | `reason` (one of `model_absent`, `circuit_breaker_open`, `summarizer_error`, `empty_summary`) — emitted when a compaction pass degrades to a static marker instead of an LLM summary (`co_cli/context/compaction.py`). Distinct reason per cause so a silent degradation is separable at triage. |

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
| `setup_observability(log_dir, *, app_log_name, spans_log_name, settings, errors_log_name=None) -> None` | `co_cli/observability/setup.py` | Shared bootstrap for every process: calls `setup_file_logging` + `setup_log`, then raises `SUPPRESS_LOGGERS` (`openai`/`httpx`/`anthropic`/`hpack`) to WARNING. Main app passes `co-cli*` + `errors.jsonl`; dream daemon passes `co-dream*` + `errors_log_name=None`. Idempotent |
| `setup_log(log_path, *, max_size_mb, backup_count, redact_patterns) -> None` | `co_cli/observability/tracing.py` | Configures the `co_cli.observability.spans` rotating JSONL handler with `propagate=False`; compiles and stores redact patterns; idempotent |
| `setup_file_logging(log_dir, level="INFO", max_size_mb=5, backup_count=3, *, app_log_name="co-cli.jsonl", errors_log_name="errors.jsonl") -> None` | `co_cli/observability/file_logging.py` | Attaches rotating JSONL handlers to root logger: app log (INFO+) at `app_log_name`, errors log (WARNING+, 2 MB/2 backups) at `errors_log_name`; errors handler skipped when `errors_log_name=None`; idempotent |

### Tracing primitives

| Symbol | Source | Contract |
|--------|--------|---------|
| `@trace(name=None, *, new_trace=False)` | `co_cli/observability/tracing.py` | Decorator for sync or async functions; emits one span per call. `new_trace=True` resets `trace_id` before push so the decorated function's span carries the fresh id |
| `current_span() -> _Span \| _NoOpSpan` | `co_cli/observability/tracing.py` | Proxy over the top-of-stack span; `.set_attribute(k, v)`, `.add_event(name, attrs)`, `.set_status(status, msg)`; no-op proxy when stack empty (debug-logs each call) |
| `push_span(name, *, kind="co", attributes) -> dict` | `co_cli/observability/tracing.py` | Explicit span management for the agent/model/tool span seams; returns the span dict for identity-based cleanup |
| `pop_span(*, status="OK", status_msg=None, attributes=None) -> None` | `co_cli/observability/tracing.py` | Pops top-of-stack span, applies attributes/status, redacts, emits one JSON record |
| `new_trace() -> str` | `co_cli/observability/tracing.py` | Generates a fresh 16-hex `trace_id` and binds it to the contextvar; existing spans on the stack keep their own id |
| `set_session_context(session_id)` / `clear_session_context()` | `co_cli/observability/tracing.py` | Bind/clear the `session_id` contextvar |
| `run_with_context(fn, *args, **kwargs) -> Callable[[], Any]` | `co_cli/observability/tracing.py` | Captures the current Context and returns a 0-arg callable; pass to `loop.run_in_executor` to carry the span/trace/session context across the thread boundary |

### Span payload serialization

| Symbol | Source | Contract |
|--------|--------|---------|
| `serialize_messages(messages) -> str` / `serialize_response(response) -> str` | `co_cli/observability/serialize.py` | Compact JSON for `co.model.input` / `co.model.output`; shared by the `chat` span (`SurrogateRecoveryModel`) and the direct-call `llm_call` span |
| `serialize_tool_args(args) -> str` / `truncate_tool_result(value) -> str` | `co_cli/observability/serialize.py` | Compact JSON for `co.tool.args` and a length-bounded render for `co.tool.result`; used by `_CallSeamToolset.call_tool` |

### Viewers (CLI entrypoints)

| Symbol | Source | Contract |
|--------|--------|---------|
| `co tail` | `co_cli/main.py` → `co_cli/observability/tail.py:run_tail` | Reads and follows the JSONL spans log; rotation-safe via inode tracking; filters by trace/tool/model kind; summary or `--detail` |
| `co trace <trace_id>` | `co_cli/main.py` → `co_cli/observability/trace_view.py:render_trace` | Reads all records for one trace from the live log and rotated backups; builds parent/child tree; renders indented snapshot |

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/observability/tracing.py` | `setup_log`, `@trace` decorator, `current_span`, `push_span`/`pop_span`, `new_trace`, `set_session_context`/`clear_session_context`, `run_with_context`; contextvars-based span stack; redaction pipeline; JSON-line emit via dedicated logger |
| `co_cli/observability/serialize.py` | `serialize_messages`/`serialize_response`/`serialize_tool_args`/`truncate_tool_result` — compact-JSON span-payload helpers shared by the `chat`/`llm_call` model spans and the `tool` span |
| `co_cli/observability/setup.py` | `setup_observability()` shared coordinator + `SUPPRESS_LOGGERS`; the single wiring path for both the main app and the dream daemon |
| `co_cli/observability/file_logging.py` | `setup_file_logging()` — attaches rotating JSONL handlers to root logger: app log (INFO+, caller-named, default `co-cli.jsonl`) and optional errors log (WARNING+, 2 MB/2 backups hardcoded, default `errors.jsonl`, skipped when `errors_log_name=None`) |
| `co_cli/observability/tail.py` | `run_tail()` — JSONL follow loop, per-kind attribute extraction, `--detail` rendering for agent/model/tool |
| `co_cli/observability/trace_view.py` | `render_trace()` — snapshot tree builder; reads live log + rotated backups; sorts siblings by `start_ts`; depth-uncapped indented render |
| `co_cli/main.py` | `@app.command()` wrappers for `tail` and `trace`; module-level `_setup_observability()` calls the `setup_observability()` coordinator with the `co-cli*` filenames |
| `co_cli/config/core.py` | `USER_DIR`, `LOGS_DIR` — user-global path constants |
| `co_cli/config/observability.py` | `ObservabilitySettings` — file-logging settings, spans-log settings, redaction patterns |
| `co_cli/agent/build.py` | Builds orchestrator and task agents; no capability attachment — tracing/cap/spill ride the toolset wrapper and model wrapper |
| `co_cli/agent/toolset.py` | `_CallSeamToolset.call_tool` — the single `tool`-span / cap / MCP-spill seam |
| `co_cli/llm/surrogate_recovery_model.py` | `SurrogateRecoveryModel` — the `chat`-span seam (both request paths) plus surrogate recovery and gated tool-arg repair |
| `~/.co-cli/logs/co-cli.jsonl` | Rotating app log — INFO+ Python `logging` records (`"kind": "log"`); independent stream from spans |
| `~/.co-cli/logs/co-cli-spans.jsonl` | Rotating spans log — one JSON line per closed span |
| `~/.co-cli/logs/errors.jsonl` | Rotating WARNING+ app log — 2 MB / 2 backups; for fast error triage |
| `~/.co-cli/logs/co-dream.jsonl` | Dream daemon's rotating app log (INFO+, includes WARNING+); written via the same coordinator, no separate errors file |
| `~/.co-cli/logs/co-dream-spans.jsonl` | Dream daemon's rotating span stream — `jq`-inspectable; not read by `co tail` / `co trace` |
