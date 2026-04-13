# Research: OpenTelemetry Implementation Comparison (`fork-claude-code` vs `co-cli`)

This note compares source-verified OpenTelemetry code in the local checkouts of `fork-claude-code` and `co-cli`. It intentionally avoids runtime claims that are not directly supported by code.

## Sources Scanned

### `fork-claude-code`
- `entrypoints/init.ts`
- `services/analytics/index.ts`
- `services/analytics/sink.ts`
- `services/analytics/firstPartyEventLogger.ts`
- `services/analytics/firstPartyEventLoggingExporter.ts`
- `utils/telemetry/instrumentation.ts`
- `utils/telemetry/sessionTracing.ts`
- `utils/telemetry/betaSessionTracing.ts`
- `utils/telemetry/perfettoTracing.ts`
- `utils/telemetry/events.ts`
- `utils/processUserInput/processTextPrompt.ts`
- `services/tools/toolExecution.ts`
- `services/api/claude.ts`
- `services/api/logging.ts`
- `services/analytics/metadata.ts`

### `co-cli`
- `co_cli/main.py`
- `co_cli/observability/_telemetry.py`
- `co_cli/context/orchestrate.py`
- `co_cli/memory/_lifecycle.py`
- `co_cli/tools/memory.py`
- `co_cli/tools/subagent.py`
- `co_cli/bootstrap/core.py`
- `co_cli/tools/task_control.py`
- `co_cli/observability/_tail.py`
- local installed `pydantic_ai`:
  - `.venv/lib/python3.12/site-packages/pydantic_ai/_instrumentation.py`
  - `.venv/lib/python3.12/site-packages/pydantic_ai/models/instrumented.py`
  - `.venv/lib/python3.12/site-packages/pydantic_ai/_tool_manager.py`

## 1. Bootstrap and Enablement

### `fork-claude-code`

`fork-cc` does not initialize telemetry at module import time. `main.tsx` calls `initializeTelemetryAfterTrust()`, which lives in `entrypoints/init.ts`. That path lazy-loads `utils/telemetry/instrumentation.ts` and then initializes providers based on environment gates.

There are three distinct gates in the checked-in code:

- `CLAUDE_CODE_ENABLE_TELEMETRY` gates the standard OTLP customer telemetry path in `instrumentation.ts`.
- `isEnhancedTelemetryEnabled()` gates trace-provider setup for the manual session-tracing spans in `sessionTracing.ts`.
- `ENABLE_BETA_TRACING_DETAILED=1` plus `BETA_TRACING_ENDPOINT` gates the separate beta-tracing path in `betaSessionTracing.ts` and `initializeBetaTracing()`.

So `fork-cc` is not a single telemetry mode. It has separate standard OTLP telemetry, enhanced session tracing, and beta detailed tracing paths.

### `co-cli`

`co` initializes telemetry eagerly in `co_cli/main.py`:

- creates a `TracerProvider`
- attaches `SimpleSpanProcessor(SQLiteSpanExporter())`
- sets the global tracer provider
- enables global `pydantic-ai` instrumentation with `Agent.instrument_all(InstrumentationSettings(..., version=3))`

There is no corresponding environment gate in the scanned bootstrap code. The checked-in path is always local and always SQLite-backed.

## 2. Span Creation and Context Tracking

### `fork-claude-code`

`fork-cc` uses manual tracing APIs in `utils/telemetry/sessionTracing.ts`. The span lifecycle code is custom, not framework-delegated.

Verified span names in that file:

- `claude_code.interaction`
- `claude_code.llm_request`
- `claude_code.tool`
- `claude_code.tool.blocked_on_user`
- `claude_code.tool.execution`
- `claude_code.hook`

Context and span tracking are also custom:

- `AsyncLocalStorage<SpanContext | undefined>` for `interactionContext`
- `AsyncLocalStorage<SpanContext | undefined>` for `toolContext`
- `activeSpans: Map<string, WeakRef<SpanContext>>`
- `strongSpans: Map<string, SpanContext>`

`sessionTracing.ts` also starts a background cleanup interval on first interaction span. Every 60 seconds it removes GC'd or stale entries from `activeSpans`; stale spans use a 30 minute TTL. The code comments explicitly describe this as a safety net for spans that were never ended, including aborted streams and uncaught exceptions.

Relevant call sites found in the repo:

- user prompt starts the interaction span in `utils/processUserInput/processTextPrompt.ts`
- LLM request spans are started in `services/api/claude.ts` and ended in `services/api/logging.ts`
- tool, blocked-on-user, and tool-execution spans are used in `services/tools/toolExecution.ts`

### `co-cli`

`co` mixes a small set of manual spans with `pydantic-ai` instrumentation.

Verified manual span names in checked-in application code:

- `co.turn`
- `ctx_overflow_check`
- `co.memory.write`
- `co.memory.save`
- `co.memory.update`
- `co.memory.append`
- `subagent_<role>`
- `sync_knowledge`
- `restore_session`
- `background_task_execute`

These manual spans use standard `with tracer.start_as_current_span(...)` / `with _TRACER.start_as_current_span(...)` blocks. In the scanned `co` telemetry code, there is no custom span registry, `AsyncLocalStorage` equivalent, or cleanup timer.

For agent/model/tool tracing, `co` delegates to `pydantic-ai`. In the local installed version (`pydantic_ai 1.77.0`), instrumentation version `3` maps names and attributes as follows:

- agent run spans: `invoke_agent {agent_name}`
- tool spans: `execute_tool {tool_name}`
- tool argument attribute: `gen_ai.tool.call.arguments`
- tool result attribute: `gen_ai.tool.call.result`
- model request/response content attributes: `gen_ai.input.messages`, `gen_ai.output.messages`

That mapping comes from `pydantic_ai/_instrumentation.py`, `pydantic_ai/models/instrumented.py`, and `pydantic_ai/_tool_manager.py`.

## 3. Exporters, Providers, and Shutdown

### `fork-claude-code`

`fork-cc`'s `utils/telemetry/instrumentation.ts` configures more than traces:

- `MeterProvider`
- `LoggerProvider`
- optionally `BasicTracerProvider`

Exporter loading is dynamic. OTLP exporters are imported lazily per signal and protocol:

- metrics exporter modules are loaded in `getOtlpReaders()`
- log exporter modules are loaded in `getOtlpLogExporters()`
- trace exporter modules are loaded in `getOtlpTraceExporters()`

The standard OTLP path uses batch processors:

- `BatchLogRecordProcessor`
- `BatchSpanProcessor`

The code also includes:

- optional BigQuery metrics export via `BigQueryMetricsExporter`
- shutdown/flush paths guarded by `Promise.race([... , telemetryTimeout(...)])`
- a separate beta tracing initialization path that writes logs and traces to `BETA_TRACING_ENDPOINT`

So the current code is broader than "trace export". It includes metrics, logs, traces, and separate beta tracing setup.

### `co-cli`

`co` uses a single custom exporter: `SQLiteSpanExporter` in `co_cli/observability/_telemetry.py`.

Verified behavior in that exporter:

- writes to `LOGS_DB`, which resolves to `~/.co-cli/co-cli-logs.db` unless `CO_CLI_HOME` overrides the root
- stores span rows in a `spans` table with JSON-serialized `attributes`, `events`, and `resource`
- enables `PRAGMA journal_mode = WAL`
- enables `PRAGMA busy_timeout = 5000`
- retries `sqlite3.OperationalError` lock failures up to 3 times with exponential backoff starting at 0.1 seconds

Unlike `fork-cc`, `co` does not configure OTLP exporters in the scanned code. Its viewers read the same local SQLite store:

- `co logs`
- `co traces`
- `co tail`

In the scanned `co` code, there is no parallel OTel log pipeline, no `LoggerProvider`, no `MeterProvider`, and no analytics event sink comparable to `fork-cc`'s internal analytics path.

## 4. Event Logging and Routing

### `fork-claude-code`

`fork-cc` has an additional logging design beyond traces:

- a public analytics API in `services/analytics/index.ts`
- queueing of events until a sink attaches
- a sink in `services/analytics/sink.ts` that routes events to Datadog and 1P logging
- sampling via `shouldSampleEvent(...)`

Its internal 1P event logging path is separate from customer OTLP telemetry:

- `initialize1PEventLogging()` creates a dedicated `LoggerProvider`
- that provider is intentionally not registered as the global logger provider
- it uses `FirstPartyEventLoggingExporter`
- comments in the code explicitly state this separation is to keep internal events from leaking to customer endpoints and vice versa

The 1P exporter itself implements additional resilience and delivery logic:

- append-only failed-event files under the Claude config home
- startup retry of previous failed batch files for the same session
- quadratic backoff for retries
- chunked POST batching
- short-circuit on first failed batch, queueing the remaining unsent batches
- auth fallback by retrying without auth on HTTP 401

This means `fork-cc`'s logging design is multi-channel:

- customer OTLP metrics/logs/traces
- internal 1P event logging over a separate logger/exporter pipeline
- Datadog fanout via the analytics sink

There is also a separate non-OTel Perfetto trace path in `utils/telemetry/perfettoTracing.ts`, but that is a trace-file channel rather than part of the OTel log/export pipeline.

### `co-cli`

In the scanned `co` code, observability is materially simpler:

- one trace pipeline
- one exporter (`SQLiteSpanExporter`)
- one storage backend (local SQLite)
- local viewers over that same DB

I did not find a separate analytics event API, event queue, Datadog-style fanout, dedicated OTel log provider, or metrics provider in the scanned `co` code.

## 5. Content Capture, Redaction, and Truncation

### `fork-claude-code`

The current doc needs a narrower statement here because the content rules differ by telemetry path.

Verified content controls:

- user prompt text is redacted to `<REDACTED>` unless `OTEL_LOG_USER_PROMPTS` is truthy (`utils/telemetry/events.ts`, `utils/processUserInput/processTextPrompt.ts`, `utils/telemetry/sessionTracing.ts`)
- tool content events are only emitted when `OTEL_LOG_TOOL_CONTENT` is truthy (`utils/telemetry/sessionTracing.ts`)
- detailed tool parameters and MCP details are gated by `OTEL_LOG_TOOL_DETAILS` (`services/analytics/metadata.ts`, `services/tools/toolExecution.ts`)

Verified truncation and deduplication behavior:

- `MAX_CONTENT_SIZE = 60 * 1024` lives in `utils/telemetry/betaSessionTracing.ts`
- truncation helper `truncateContent(...)` also lives there
- hash-based deduplication of system prompts and tool definitions also lives there
- full system prompts and full tool JSON are logged once per unique hash per session in the beta tracing path

That means 60 KB truncation and hash-based deduplication are beta-tracing behaviors, not universal behaviors across every telemetry path in `fork-cc`.

### `co-cli`

In the scanned `co` code, telemetry storage does not implement redaction or size-based truncation:

- `SQLiteSpanExporter` serializes span attributes/events/resources with `json.dumps(...)`
- no exporter-side redaction branch was found
- no exporter-side content-size cap was found

The local viewers do truncate for display:

- `co_cli/observability/_tail.py` truncates some rendered argument previews
- `co_cli/observability/_viewer.py` truncates long attribute display values behind expand/collapse UI

But those are viewer concerns. They do not change what the exporter stores in SQLite.

## 6. Corrected Comparison

- `fork-cc` uses env-gated, lazily loaded OTLP telemetry bootstrap plus a custom manual session-tracing layer. Its detailed content truncation and hash deduplication are part of the beta tracing path, not a global rule across all telemetry.
- `fork-cc` also has a separate internal event-logging system built on a dedicated `LoggerProvider`, an analytics sink, sampling, Datadog routing, and a disk-backed retrying exporter. That is a key part of its logging design.
- `co` uses eager local telemetry bootstrap, a custom SQLite span exporter, a small set of manual orchestration spans, and `pydantic-ai` instrumentation for agent/model/tool spans.
- `fork-cc` maintains explicit span context state (`AsyncLocalStorage`, `WeakRef`, strong-reference maps, cleanup timer). The scanned `co` telemetry code does not implement an equivalent custom span-lifecycle layer.
- `fork-cc` configures metrics, logs, optional traces, internal 1P event logging, and a separate Perfetto trace channel. `co` stores spans locally in SQLite and ships three local viewers over that DB.

## 7. `co` Gaps vs `fork-cc` Patterns

This section treats `fork-cc` as a source-available reference point, not as a universal standard. Each gap below is limited to design and logic visible in the scanned code.

### Gap 1: No Separate Event-Logging Channel in `co`

`fork-cc` has a distinct event-logging path in addition to traces:

- public analytics API in `services/analytics/index.ts`
- sink routing in `services/analytics/sink.ts`
- dedicated internal `LoggerProvider` in `services/analytics/firstPartyEventLogger.ts`
- separate delivery/export logic in `services/analytics/firstPartyEventLoggingExporter.ts`

In the scanned `co` code, observability is trace-only:

- `TracerProvider` + `SimpleSpanProcessor(SQLiteSpanExporter())` in `co_cli/main.py`
- no separate analytics event API
- no separate OTel log provider

Adoption verdict: `adapt`

Reason: the separation of high-volume event logging from trace storage is a strong pattern. `co` likely does not need Datadog/1P-style fanout, but if it grows beyond local trace inspection it would benefit from a second channel for structured product/runtime events that should not live as span attributes.

### Gap 2: No Export-Boundary Content Controls in `co`

`fork-cc` has explicit content gates:

- `OTEL_LOG_USER_PROMPTS`
- `OTEL_LOG_TOOL_CONTENT`
- `OTEL_LOG_TOOL_DETAILS`
- beta-path truncation/dedup in `utils/telemetry/betaSessionTracing.ts`

In the scanned `co` exporter, span data is serialized directly with `json.dumps(...)` and written to SQLite without exporter-side redaction or size capping.

Adoption verdict: `adapt`

Reason: `co` is local-first, so copying `fork-cc`'s default-redaction posture wholesale would lose useful debugging value. But exporter-side size limits and optional redaction knobs would still be good defensive design, especially for very large tool payloads or future non-local export modes.

### Gap 3: No Metrics / Logs / Traces Signal Split in `co`

`fork-cc`'s `utils/telemetry/instrumentation.ts` configures:

- `MeterProvider`
- `LoggerProvider`
- optional `BasicTracerProvider`

In the scanned `co` code, only tracing is configured.

Adoption verdict: `defer`

Reason: this is only a best-practice gap if `co` needs operational observability beyond local debugging. For the current local SQLite design, adding metrics and log providers would increase complexity without clear source-backed need.

### Gap 4: No Retrying Remote Delivery Path in `co`

`fork-cc`'s internal event exporter adds:

- append-only failed-batch files
- startup replay of previous failed files
- quadratic backoff
- chunking
- auth fallback on 401

`co` has retry logic only for local SQLite lock contention in `co_cli/observability/_telemetry.py`. It does not have any comparable remote-delivery reliability layer.

Adoption verdict: `defer`

Reason: this is a real gap only if `co` introduces remote logging. For the current local-only design, SQLite WAL + busy timeout + retry is the relevant robustness layer, and it already exists.

### Gap 5: No Explicit Channel Separation for Different Sensitivity / Audience Levels

`fork-cc` separates multiple observability channels:

- customer OTLP telemetry
- internal 1P event logging
- Perfetto trace files

`co` currently records to one local SQLite trace store and exposes local viewers over that store.

Adoption verdict: `adapt`

Reason: even in a local-first tool, separating "full-fidelity developer traces" from "lower-volume operational summaries" is a strong design pattern. `co` does not need `fork-cc`'s exact channel set, but the separation concept is worth adopting if the telemetry surface expands.

### Gap 6: No Custom Span-Lifecycle Registry / Cleanup Layer in `co`

`fork-cc` uses:

- `AsyncLocalStorage`
- `WeakRef`
- `strongSpans`
- periodic cleanup of stale spans

The scanned `co` code does not implement an equivalent layer.

Adoption verdict: `do not adopt`

Reason: the checked-in `fork-cc` code is solving Node/Bun async span-lifecycle problems in a manual instrumentation system. The scanned `co` code uses Python context managers plus `pydantic-ai` instrumentation and does not show the same design pressure. Copying this machinery into `co` without a Python-specific failure mode would be cargo culting.

### Gap 7: No Dedicated Non-OTel Trace File Channel in `co`

`fork-cc` also maintains a separate Perfetto trace path in `utils/telemetry/perfettoTracing.ts`.

`co` does not have an equivalent parallel trace-file exporter; it relies on SQLite plus local viewers.

Adoption verdict: `defer`

Reason: this is useful for deep timing analysis and swarm hierarchy visualization, but it is not clearly a missing best practice for `co`'s current observability goals.

## 8. Gaps In This Note

The source comparison above is accurate, but it still has three decision-making gaps for `co`:

- it does not rank recommendations by implementation cost versus operator value in `co`'s current local-first architecture
- it does not call out telemetry retention and DB-growth control, even though `co` currently keeps one local SQLite store with no built-in pruning path
- it does not connect exporter-side size controls to `co`'s existing oversized tool-result spill pattern, which already provides a reusable mechanism for truncation-by-reference elsewhere in the product

Those omissions matter because the highest-value next steps for `co` are not the most feature-rich `fork-cc` subsystems. They are the changes that improve local inspection, payload safety, and long-session operability with minimal new architecture.

## 9. ROI-Ranked Adoptions For `co`

### 1. Adopt terminal tree/detail trace inspection first

Adoption verdict: `adopt now`

Why it is high ROI:

- `co` already stores `trace_id` and `parent_id` in SQLite via `SQLiteSpanExporter`
- `co traces` already reconstructs span trees in `co_cli/observability/_viewer.py`
- `co tail` is still flat, so the missing value is mostly presentation, not instrumentation

This is the cleanest borrowable pattern from `fork-cc`: better local trace consumption, not a larger telemetry backend.

### 2. Add exporter-side size controls by adapting the existing spill-to-disk pattern

Adoption verdict: `adapt soon`

Why it is high ROI:

- the current exporter writes span attributes directly with `json.dumps(...)`
- `co` already has per-tool `max_result_size` metadata and a persisted-preview path for oversized tool output
- the same design idea can cap or replace oversized telemetry payloads without forcing fork-style default redaction

Best first targets:

- `gen_ai.input.messages`
- `gen_ai.output.messages`
- very large tool argument/result attributes

### 3. Add local telemetry retention and pruning controls

Adoption verdict: `adapt soon`

Why it is high ROI:

- `co` uses one local SQLite store for all spans and viewers
- the current docs only describe full manual deletion of `~/.co-cli/co-cli-logs.db`
- this is a more immediate operational gap for `co` than extra OTel signal providers

Good local-first shapes would be:

- max-age pruning
- max-size pruning
- explicit maintenance command(s)

### 4. Add a second local event channel only after the above

Adoption verdict: `adapt later`

The `fork-cc` split between detailed traces and separate operational events is a strong pattern, but it is not the first thing `co` needs. If adopted, it should start as a simple local summary/event channel rather than remote fanout or analytics infrastructure.

## 10. Recommended Sequencing

If `co` wants the highest-value observability improvements from this comparison, the order should be:

1. improve `co tail` with explicit detail/tree modes
2. cap or spill oversized telemetry payloads at the export boundary
3. add retention/pruning for the local telemetry store
4. only then consider separate event channels, extra signals, or remote delivery

That sequence matches the current product shape: local-first, operator-facing, and intentionally lighter-weight than `fork-cc`'s multi-channel telemetry stack.
