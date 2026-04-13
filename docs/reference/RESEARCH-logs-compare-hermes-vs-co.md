# Research: Hermes vs Co Logging and OTel

This note compares source-verified logging, OpenTelemetry, and turn-processing code in the local `hermes-agent` and `co-cli` repos. It is intentionally limited to what is present in checked-in source and repository search results.

## Scope and Method

- Hermes repo: `/Users/binle/workspace_genai/hermes-agent`
- Co repo: `/Users/binle/workspace_genai/co-cli`
- Hermes OTel search used: `rg -n "opentelemetry|TracerProvider|get_tracer|start_as_current_span|SpanExporter|OTLP|otlp|trace\." /Users/binle/workspace_genai/hermes-agent --glob '*.py'`
- Result of that Hermes search: no OpenTelemetry runtime import/call sites were found in Python source.

## Sources Scanned

### Hermes

- `hermes_logging.py`
- `run_agent.py`
- `hermes_cli/logs.py`
- `pyproject.toml`
- `uv.lock`

### Co

- `pyproject.toml`
- `co_cli/main.py`
- `co_cli/observability/_telemetry.py`
- `co_cli/context/orchestrate.py`
- `co_cli/context/_tool_lifecycle.py`
- `co_cli/observability/_tail.py`

## Hermes: Logging Design

Hermes has an explicit centralized Python logging setup in `hermes_logging.py:1-142`.

- `setup_logging()` creates `~/.hermes/logs/agent.log` and `~/.hermes/logs/errors.log` via `RotatingFileHandler`, using `RedactingFormatter` for both handlers (`hermes_logging.py:7-12`, `hermes_logging.py:51-142`).
- `setup_logging()` is idempotent through `_logging_initialized` and `_add_rotating_handler()` path deduplication (`hermes_logging.py:23-26`, `hermes_logging.py:91-94`, `hermes_logging.py:213-239`).
- `setup_verbose_logging()` adds a DEBUG-level `StreamHandler` for console output but leaves rotating file handlers in place (`hermes_logging.py:145-174`).
- Managed-mode rollover/file creation permissions are handled by `_ManagedRotatingFileHandler`, which chmods files to `0660` after open and rollover (`hermes_logging.py:181-210`).

`AIAgent.__init__()` wires that centralized logging in early and then changes console logger levels in quiet mode without removing file handlers (`run_agent.py:716-738`).

- Quiet mode raises selected logger levels (`tools`, `run_agent`, `trajectory_compressor`, `cron`, `hermes_cli`) to `ERROR` on the console (`run_agent.py:726-738`).
- The inline comment states that `agent.log` and `errors.log` still capture everything (`run_agent.py:727-730`).

Hermes also has a separate session-persistence path outside the rotating log files.

- Session JSON logs live under `~/.hermes/sessions/session_<session_id>.json` (`run_agent.py:952-969`).
- `_persist_session()` writes both the JSON session log and the SQLite session store on exit paths (`run_agent.py:1931-1942`).
- `_save_session_log()` writes full message history, system prompt snapshot, and tool definitions into the JSON file (`run_agent.py:2474-2534`).
- `_flush_messages_to_session_db()` appends per-message records into the SQLite session DB and tracks `_last_flushed_db_idx` to avoid duplicate writes (`run_agent.py:1944-1990`).

Hermes exposes line-oriented log inspection through `hermes logs`.

- `hermes_cli/logs.py` maps logical names to `agent.log`, `errors.log`, and `gateway.log` (`hermes_cli/logs.py:27-32`).
- Filters are regex/line based: timestamp prefix parsing, log-level regex extraction, substring session matching, and relative-time cutoff (`hermes_cli/logs.py:34-105`).
- Tail/follow reads raw files directly and applies filters client-side (`hermes_cli/logs.py:108-222`).

## Hermes: OTel Status

In the scanned Hermes Python source, no OpenTelemetry runtime bootstrap or span creation code was found.

- `pyproject.toml` direct dependencies do not list any OpenTelemetry package (`pyproject.toml:13-37`).
- `uv.lock` does contain OpenTelemetry packages, and the `daytona` lock entry depends on `opentelemetry-api`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-aiohttp-client`, and `opentelemetry-sdk` (`uv.lock:932-945`).
- The lockfile also contains concrete package records for those OTel packages (`uv.lock:3133-3245`).

So, in checked-in Hermes source:

- OpenTelemetry packages are present in the lockfile.
- Direct project dependencies do not declare them.
- No runtime OTel bootstrap/import/call sites were found in Python source.

## Hermes: Processing Logic Relevant to Logging

Hermes keeps most turn-processing logic in one method: `AIAgent.run_conversation()` (`run_agent.py:7124-9678`).

- Turn start is logged once with session, model, provider, platform, history length, and a message preview (`run_agent.py:7206-7214`).
- System prompt reuse, preflight compression, plugin hook invocation, and external memory prefetch all happen before the main tool-calling loop (`run_agent.py:7265-7408`, `run_agent.py:7424-7435`).
- The main loop increments `api_call_count`, tracks an iteration budget, and can emit gateway step callbacks before each API call (`run_agent.py:7437-7483`).
- Ephemeral plugin and memory context are injected into the current turn's user message at API-call time rather than mutating the persisted `messages` list (`run_agent.py:7496-7516`).

Hermes logs tool execution in both concurrent and sequential paths.

- Concurrent path: `_run_tool()` logs completion or failure per tool, and the outer loop appends `role="tool"` messages after result handling (`run_agent.py:6445-6459`, `run_agent.py:6485-6549`).
- Sequential path: per-tool logging occurs after `handle_function_call()` / memory-manager execution, and the tool result is appended as a `role="tool"` message (`run_agent.py:6575-6845`).
- Hermes also injects budget warnings into the last tool result content when threshold logic fires (`run_agent.py:6557-6573`).

Turn shutdown has explicit diagnostic logging.

- `run_conversation()` always persists trajectory/session state before exit diagnostics (`run_agent.py:9508-9515`).
- It then logs a normalized turn-end diagnostic at INFO, or WARNING when the last message is still a tool result (`run_agent.py:9517-9559`).
- Post-turn plugin hooks and background review scheduling happen after that diagnostic block (`run_agent.py:9561-9676`).

## Co: Logging and OTel Design

Co’s checked-in observability bootstrap is OpenTelemetry-first, not rotating-file-logging-first.

- `pyproject.toml` directly depends on `opentelemetry-sdk` (`pyproject.toml:11-18`).
- `co_cli/main.py` creates a `TracerProvider`, attaches `SimpleSpanProcessor(SQLiteSpanExporter())`, sets the global provider, and enables `pydantic-ai` instrumentation with `InstrumentationSettings(version=3)` (`co_cli/main.py:57-80`).

The exporter writes spans into SQLite.

- `SQLiteSpanExporter` creates a `spans` table with `trace_id`, `parent_id`, `attributes`, `events`, and `resource` columns, plus indexes on `trace_id`, `parent_id`, and `start_time` (`co_cli/observability/_telemetry.py:21-57`).
- The exporter serializes span IDs, status, attributes, events, and resource attributes, then inserts them into SQLite (`co_cli/observability/_telemetry.py:59-122`).
- It enables WAL mode, applies a busy timeout, and retries locked exports with exponential backoff (`co_cli/observability/_telemetry.py:27-32`, `co_cli/observability/_telemetry.py:114-136`).

Co’s inspection commands are trace-oriented.

- `co logs` launches Datasette on the SQLite trace DB (`co_cli/main.py:326-355`).
- `co traces` generates a static HTML trace viewer (`co_cli/main.py:359-373`).
- `co tail` prints spans from the SQLite DB in the terminal (`co_cli/main.py:376-401`).

`co tail` works from span rows rather than raw log lines.

- It classifies rows as `agent`, `model`, or `tool` spans and extracts summary attributes from OTel fields such as `gen_ai.request.model`, `gen_ai.usage.*`, and `gen_ai.tool.call.arguments` (`co_cli/observability/_tail.py:45-81`).
- In verbose mode it also renders tool args/results plus model input/output content from the stored JSON attributes (`co_cli/observability/_tail.py:91-161`).
- Filtering is SQL-based with exact `trace_id = ?` matching plus name-pattern filters for tool/model views (`co_cli/observability/_tail.py:236-256`).

Co also enriches tool spans after execution.

- `CoToolLifecycle.after_tool_execute()` sets `co.tool.source`, `co.tool.requires_approval`, and `co.tool.result_size` on the current span (`co_cli/context/_tool_lifecycle.py:36-52`).

## Co: Processing Logic Relevant to Logging and OTel

Co’s main turn orchestrator is `run_turn()` in `co_cli/context/orchestrate.py`, and it is narrower in scope than Hermes `run_conversation()`.

- `run_turn()` starts a root `co.turn` span and resets per-turn runtime state (`co_cli/context/orchestrate.py:509-518`).
- It delegates one stream segment to `_execute_stream_segment()`, then runs a separate approval loop for deferred tool requests, and returns a typed `TurnResult` (`co_cli/context/orchestrate.py:520-546`).
- Error handling is separated by exception type: context overflow compaction, HTTP 400 tool-call reformulation, provider/network/malformed-output handling, and interruption (`co_cli/context/orchestrate.py:548-613`).
- In the `finally` block it writes turn outcome, interruption flag, and token counts onto the `co.turn` span (`co_cli/context/orchestrate.py:615-628`).

Co’s bootstrap and tool-lifecycle logic are split into separate modules instead of living inside one large turn method.

- Telemetry bootstrap is in `co_cli/main.py:57-80`.
- Span export/storage is in `co_cli/observability/_telemetry.py:21-139`.
- Turn orchestration is in `co_cli/context/orchestrate.py:488-628`.
- Tool span enrichment is in `co_cli/context/_tool_lifecycle.py:27-65`.

## Direct Comparison

| Dimension | Hermes | Co |
| --- | --- | --- |
| Primary observability bootstrap | Centralized Python logging setup in `hermes_logging.py` | OpenTelemetry bootstrap in `co_cli/main.py` |
| Primary durable store | Rotating text log files under `~/.hermes/logs/` plus separate session JSON/SQLite persistence | SQLite `spans` table written by `SQLiteSpanExporter` |
| Main inspection surface | `hermes logs` reads and filters raw log files | `co logs`, `co traces`, and `co tail` read the SQLite span store |
| Filter model | Regex/time/substring filtering over text lines (`hermes_cli/logs.py`) | SQL filtering over structured span columns (`co_cli/observability/_tail.py`) |
| Runtime OTel code found in scanned source | None found | Present in bootstrap, exporter, orchestrator, and tool-lifecycle code |
| Turn processing ownership | Mostly centralized in `run_agent.py` `run_conversation()` | Split across orchestrator, bootstrap, exporter, and lifecycle modules |
| Tool execution observability | Explicit `logger.info` / `logger.warning` calls around tool execution and failures in sequential and concurrent paths | Tool execution appears in OTel spans; `CoToolLifecycle.after_tool_execute()` adds co-specific span attributes |
| Turn-end observability | Explicit INFO/WARNING diagnostic log line at turn end | Root `co.turn` span gets turn outcome/interruption/token attributes |

## Bottom Line

From checked-in source, Hermes has a file-logging-centered design with separate session persistence, while Co has a trace-centered design backed by local SQLite OpenTelemetry storage.

From checked-in source, Hermes does not currently show OpenTelemetry runtime bootstrap or span emission in Python code, while Co does.

---

## Source-Verified Gaps (Feb 2026 scan)

The following features exist in current checked-in source but are absent from the sections above. Each gap is assessed for adoption ROI — either Co adopting from Hermes, or vice versa.

### Hermes features not covered in this doc

**1. `RedactingFormatter` applied to all log output (`hermes_logging.py:7-12`, `hermes_logging.py:51-142`)**
All rotating file handlers and verbose console handlers run log records through `RedactingFormatter` before writing. The research doc notes the handler types but omits this redaction layer.

**2. Third-party logger suppression via `_NOISY_LOGGERS` (`hermes_logging.py:33-48`, `hermes_logging.py:138-139`, `hermes_logging.py:171-172`)**
Hermes maintains an explicit list of noisy third-party loggers (`openai`, `httpx`, `asyncio`, `grpc`, `modal`, etc.) and forces them to WARNING or above. The doc covers handler configuration but misses this suppression mechanism.

**3. SQLite per-message session database (`run_agent.py:1944-1991` `_flush_messages_to_session_db()`)**
In addition to the JSON session log file already documented, Hermes maintains a SQLite session DB with per-message rows including tool metadata, reasoning details, and finish reasons. The doc mentions SQLite in passing but does not distinguish it from the JSON store.

**4. API request/response debug dumps (`run_agent.py:~2447`)**
When verbose logging is active, Hermes writes full API request and response payloads to `request_dump_<session_id>_<timestamp>.json` on disk. This is separate from both the rotating log and session persistence paths.

**5. Atomic JSON writes for session logs (`run_agent.py:~2529`)**
`_save_session_log()` uses an `atomic_json_write()` helper to prevent file corruption on concurrent writes or abrupt process termination. The doc describes what is written but not the write safety mechanism.

**6. Tool progress callbacks (`run_agent.py:~491`)**
Hermes exposes pluggable `tool_progress_callback`, `tool_start_callback`, and `tool_complete_callback` hooks for real-time progress reporting to platform layers (CLI, gateway). Not covered in the turn-processing section.

**7. Save-trajectories JSONL path**
Hermes supports a `save_trajectories=True` parameter that persists full conversation JSONL records separately from session logs. Not mentioned in the logging design section.

---

### Co features not covered in this doc

**1. Python standard logging in observability and lifecycle modules**
Co is described as OTel-first, which is accurate for the primary data path. However, `co_cli/observability/_telemetry.py` and `co_cli/context/_tool_lifecycle.py` also use Python `logging` for warning/error/debug output (e.g. export retries, tool execution traces). There is no `logging`-level handler configuration equivalent to Hermes's `setup_logging()` — these log lines go to wherever the root logger is configured, which is unspecified in checked-in source.

**2. Domain-specific span attributes beyond the turn and tool lifecycle**
The doc covers `co.turn.*` and `co.tool.*` attributes from orchestrate and tool lifecycle. Current source also sets:
- `memory.tags`, `memory.action`, `memory.memory_id` on memory tool spans (`co_cli/tools/memory.py`)
- `subagent.role`, `subagent.model`, `subagent.request_limit`, `subagent.requests_used` on subagent spans (`co_cli/tools/subagent.py`)
- `ctx.*` overflow context attributes in the orchestrator (`co_cli/context/orchestrate.py:444-446`)

**3. `co tail` follow mode with high-water mark polling**
The doc describes `co tail` as printing spans from SQLite. In practice it also has a streaming follow mode that polls the database at a configurable interval using a high-water mark to emit only new spans (`co_cli/observability/_tail.py:312`, `co_cli/main.py:384`). The filtering path also extends to line 279, not 256.

**4. No payload size limits or rate limiting on span export**
Current source has no caps on attribute or event payload size and no per-second rate limiting. The only back-pressure mechanism is the SQLite WAL busy-timeout (5 s) and the 3-attempt exponential backoff on locked exports. Not addressed in the doc.

**5. `_viewer.py` HTML trace renderer**
`co_cli/observability/_viewer.py` implements the static HTML trace viewer invoked by `co traces`. The doc notes the `co traces` command but does not identify the module responsible for rendering.

---

## Gap ROI Assessment

Each gap is rated on two axes: **impact** (how much the missing capability improves debuggability, security, or operations) and **cost** (implementation effort given current architecture). Ratings are H/M/L.

### Adoption candidates (Hermes → Co)

| Gap | Impact | Cost | ROI verdict |
| --- | --- | --- | --- |
| Attribute redaction in span storage | H — co's SQLite stores raw tool args/results; secrets in tool inputs currently land in the trace DB in plaintext | M — requires a sanitizer pass in `SQLiteSpanExporter.export()` or a span processor | **Adopt.** Secret leakage risk is concrete. A span-attribute redaction processor inserted before `SQLiteSpanExporter` mirrors Hermes's `RedactingFormatter` approach. |
| Third-party logger suppression | M — co's unmanaged root logger may emit noisy lines from openai/httpx in verbose sessions | L — two lines in `main.py` or a `logging.config` dict | **Adopt quickly.** Low cost, prevents confusing noise in verbose output. |
| Per-message SQLite session DB | M — co's span DB supports trace-level queries but not per-message history queries (e.g. retrieve last N user messages) | H — requires new schema, flush path, and query API | **Defer.** Span DB already covers most inspection needs. Add only if a concrete use case (e.g. session replay, per-message search) is prioritized. |
| Tool progress callbacks | L — co's pydantic-ai streaming events and `CoToolLifecycle` hooks already cover real-time tool reporting | H — adding a separate callback layer duplicates existing mechanisms | **Skip.** Architecture already handles this. |
| Atomic JSON session writes | L — co does not write session JSON files; OTel span writes go through SQLite which handles atomicity natively | — | **N/A.** No analog in co's write path. |
| API debug dumps | M — full request/response dumps are useful for provider debugging | L — log the raw API payload in verbose mode inside `_execute_stream_segment()` | **Adopt if needed.** Low cost; worth adding behind `--verbose` flag when provider debugging is a recurring need. |

