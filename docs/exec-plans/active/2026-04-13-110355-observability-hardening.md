# TODO: Observability Hardening

**Slug:** `observability-hardening`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

> **UAT note:** TASK-1 (span redaction) is a UAT blocker — credentials stored unredacted
> in `~/.co-cli/co-cli-logs.db` contradicts the "trusted" and "local" mission positioning.
> TASK-2 (logger suppression) and TASK-3 (provider error events) are hardening improvements
> and are not UAT blockers. Ship TASK-1 first if the full plan is not ready.

---

## Context

Research: [research-logs-compare-hermes-vs-co.md](reference/research-logs-compare-hermes-vs-co.md) §Gap ROI Assessment

Current-state validation against checked-in source:

- `DESIGN-observability.md §Privacy` already flags the risk: "Tool responses and full conversation history are captured in span attributes." No redaction layer exists — confirmed by grepping `co_cli/` for `redact`, `sanitize`, `scrub`: zero hits in the observability and tool paths (`co_cli/tools/_shell_env.py` has an unrelated subprocess env allowlist).
- `SQLiteSpanExporter.export()` (`co_cli/observability/_telemetry.py`) serializes all span attributes and events to JSON with no sanitization step before insert.
- `co_cli/main.py` has no root logging configuration and no third-party logger suppression. 23 modules call `logging.getLogger(__name__)`. No `_NOISY_LOGGERS` list, `logging.disable`, or `addFilter` call exists anywhere in `co_cli/`.
- In `co_cli/context/orchestrate.py`, terminal `ModelHTTPError` paths set `turn_state.outcome = "error"` and call `_build_error_turn_result()` but do not record the HTTP status code or error body on the `co.turn` span. The error body is only shown transiently via `frontend.on_status()`.
- `Settings` in `co_cli/config/_core.py` has sub-model groups for llm, knowledge, web, subagent, memory, shell — no observability group. Pattern: one file per group under `co_cli/config/`.
- `SQLiteSpanExporter` is instantiated in `main.py` as `exporter = SQLiteSpanExporter()` before the tracer is wired. Settings are loaded lazily via `get_settings()` and can be called at that point.
- No existing TODO covers these concerns. `TODO-tail-detail-tree-modes.md` is active and orthogonal.

Artifact hygiene: clean.

---

## Problem & Outcome

Problem: Three observability gaps from the Hermes→Co adoption assessment remain unaddressed:

1. Secrets in span storage — tool arguments, tool results, and model messages that contain API keys, Bearer tokens, or file contents with credentials are stored unredacted in `~/.co-cli/co-cli-logs.db`. Any process with read access can extract them. `DESIGN-observability.md` already documents this as a known gap.
2. Noisy third-party loggers — 23 co_cli modules use `logging` with no suppression policy for upstream libraries (openai, httpx, anthropic). In verbose sessions, third-party DEBUG/INFO lines mix with co output and produce confusing noise.
3. Provider error details not persisted — when a terminal `ModelHTTPError` occurs, the HTTP code and error body are displayed to the user via `frontend.on_status()` but not stored in the span store. Debugging a recurring provider failure leaves no queryable record.

Failure cost:
1. A user who asks co to read a file containing credentials silently leaks those values into the local trace DB with no expiry and no indication.
2. Third-party library noise surfaces in verbose output, making co-specific log lines hard to isolate.
3. Intermittent provider errors leave no trace in `co logs` or `co tail`; operators cannot correlate failure patterns after the fact.

Outcome: Three targeted, independent hardening changes:
1. Value-level redaction of sensitive patterns applied in the exporter before SQLite insertion
2. Third-party logger suppression at bootstrap
3. Provider error HTTP code + body recorded as a span event on `co.turn`

---

## Scope

In scope:
- Regex-pattern-based redaction of span attribute string values and string values in event attribute dicts
- `ObservabilityConfig` sub-model in `co_cli/config/` with `redact_patterns: list[str]` field and sensible defaults
- Passing compiled patterns from `main.py` into `SQLiteSpanExporter` at init time
- `_SUPPRESS_LOGGERS` list + loop in `main.py` after `Agent.instrument_all()`
- Span event recording on terminal `ModelHTTPError` paths in `orchestrate.py`
- Tests for redaction logic, logger levels, and error event recording

Out of scope:
- Redaction of span names, trace IDs, span IDs, parent IDs, or resource attributes
- Key-based attribute removal (value-pattern scanning is simpler and more targeted)
- Retention or pruning policy for the trace DB
- Per-message SQLite session DB (deferred per gap ROI assessment)
- Tool progress callbacks (skipped — pydantic-ai streaming covers it)
- Changes to `co tail` display modes (tracked in `TODO-tail-detail-tree-modes.md`)

---

## Behavioral Constraints

- Redaction must never remove a span attribute or event entry — only replace matching string values with `[REDACTED]`. Removal changes the schema observed by `co logs` SQL queries.
- Redaction must not alter span names, trace IDs, span IDs, or parent IDs.
- Default redaction patterns must not false-positive on OTel numeric fields serialized as strings (e.g. `"input_tokens": "3745"` must not be redacted).
- Logger suppression must target only third-party namespaces — `co_cli.*` loggers must not be affected.
- Logger suppression level must be `WARNING`, not `ERROR`, to preserve upstream warnings.
- Provider error body stored in span events must be capped at 500 characters.
- Error span events must be added only on terminal `ModelHTTPError` exits — not on the HTTP 400 reformulation retry (`continue`) and not on the context-overflow compaction retry (`continue`). Terminal returns from these same branches (budget exhausted, overflow unrecoverable) do receive the event.
- Values exceeding `_MAX_REDACT_LEN` (64 KB) are stored unredacted; this is a deliberate performance trade-off, not a guarantee of full secret coverage.

---

## High-Level Design

### 1. Span attribute redaction

Add `_redact(value: str, patterns: list[re.Pattern]) -> str` as a module-level helper in `co_cli/observability/_telemetry.py`. For each compiled pattern, replace all matches in `value` with `[REDACTED]`. Return the modified string.

In `SQLiteSpanExporter.__init__()`, accept `redact_patterns: list[str]` and compile them into `list[re.Pattern]` once. In `export()`, apply `_redact` to:
- Every string attribute value in each span's `attributes` dict
- Every string value in each event's `attributes` dict

Non-string attribute values (int, bool, float, list) are not processed.

Default patterns (shipped in `ObservabilityConfig.redact_patterns`):
- `sk-[A-Za-z0-9]{20,}` — OpenAI/Anthropic API key prefixes
- `Bearer\s+[A-Za-z0-9\-._~+/]{20,}` — HTTP Authorization Bearer tokens
- `ghp_[A-Za-z0-9]{36}` — GitHub personal access tokens
- `[Aa][Pp][Ii][_-][Kk][Ee][Yy]\s*[:=]\s*\S{8,}` — generic `api_key: value` pairs
- `AKIA[0-9A-Z]{16}` — AWS access key IDs
- `-----BEGIN [A-Z ]+PRIVATE KEY-----` — PEM-encoded private keys

The `ObservabilityConfig` docstring must note that this list is not exhaustive; users with custom secret formats should extend it via `settings.json`.

Add a module-level constant `_MAX_REDACT_LEN = 65536` in `_telemetry.py`. In `_redact()`, if `len(value) > _MAX_REDACT_LEN` return the value unchanged — the cost of scanning multi-hundred-KB JSON blobs is not justified for the default pattern set.

Add `ObservabilityConfig` in `co_cli/config/_observability.py` with `redact_patterns: list[str]` and the defaults above. Wire it into `Settings` in `_core.py` as `observability: ObservabilityConfig`.

In `main.py`, load settings before constructing the exporter and pass `settings.observability.redact_patterns` in:
```python
settings = get_settings()
exporter = SQLiteSpanExporter(redact_patterns=settings.observability.redact_patterns)
```

`SQLiteSpanExporter.__init__()` signature: `redact_patterns: list[str] | None = None`. Inside init: `self._patterns = [re.compile(p) for p in (redact_patterns or [])]`.

### 2. Third-party logger suppression

After `Agent.instrument_all()` in `co_cli/main.py`, add:
```python
_SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]
for _logger_name in _SUPPRESS_LOGGERS:
    logging.getLogger(_logger_name).setLevel(logging.WARNING)
```

`asyncio` is excluded — it is a stdlib namespace and its WARNING output may surface genuine event-loop errors. No config needed for v1 — the list is narrow, well-justified, and stable.

### 3. Provider error span events

In `co_cli/context/orchestrate.py`, in the terminal `ModelHTTPError` branch (the "All other HTTP errors" path — not the 400 reformulation retry, not overflow recovery). This branch handles 429/5xx and budget-exhausted 400 (when `tool_reformat_budget == 0` and the outer `if code == 400 and turn_state.tool_reformat_budget > 0` guard is false). Both cases reach the same code path and both should receive the event. Add in this order: set `turn_state.outcome = "error"`, then add the span event, then return:
```python
turn_state.outcome = "error"
span.add_event("provider_error", {
    "http.status_code": code,
    "error.body": str(e.body)[:500],
})
return _build_error_turn_result(turn_state)
```

This records the failure context in the existing SQLite store, queryable via:
```sql
SELECT json_extract(events, '$[0].name'), json_extract(events, '$[0].attributes')
FROM spans WHERE name = 'co.turn' AND status_code = 'ERROR';
```

---

## Implementation Plan

### ✓ DONE TASK-1: Span attribute redaction

files: `co_cli/config/_observability.py` (new), `co_cli/config/_core.py`, `co_cli/observability/_telemetry.py`, `co_cli/main.py`, `tests/test_telemetry_redaction.py` (new)

Implementation:
- Create `co_cli/config/_observability.py` with `ObservabilityConfig(BaseModel)` containing `redact_patterns: list[str]` with the six default patterns. Include a docstring note that the list is not exhaustive and users with custom secret formats should extend it via `settings.json`.
- Add `observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)` to `Settings` in `_core.py`, and import `ObservabilityConfig`.
- Add `_MAX_REDACT_LEN = 65536` and `_redact(value: str, patterns: list[re.Pattern]) -> str` in `_telemetry.py`. If `len(value) > _MAX_REDACT_LEN`, return `value` unchanged.
- Update `SQLiteSpanExporter.__init__()` to accept `redact_patterns: list[str] | None = None` and compile: `self._patterns = [re.compile(p) for p in (redact_patterns or [])]`.
- In `export()`, apply `_redact` to string attribute values and string values in event attribute dicts before serialization.
- Update `main.py`: call `get_settings()` before constructing `SQLiteSpanExporter`, pass `settings.observability.redact_patterns`.
- Write `tests/test_telemetry_redaction.py`:
  - A span attribute `test.secret_value` containing `sk-abc123abc123abc123abc` is stored as `[REDACTED]` (asserted via SQLite `json_extract(attributes, '$.test.secret_value')`)
  - A span event attribute containing `Bearer abc123abc123abc123abc` is stored as `[REDACTED]`
  - A span with no sensitive values passes through unchanged
  - Span `name`, `trace_id`, `parent_id` are not modified
  - A value exceeding `_MAX_REDACT_LEN` characters is returned unchanged

done_when: |
  `uv run pytest tests/test_telemetry_redaction.py -x` passes;
  test asserts via SQLite `json_extract(attributes, '$.test.secret_value')` that an `sk-` prefixed value is stored as `[REDACTED]`
success_signal: tool args or file reads containing API keys appear as `[REDACTED]` in `co-cli-logs.db` span attributes
prerequisites: []

### TASK-2: Third-party logger suppression

files: `co_cli/main.py`, `tests/test_logger_suppression.py` (new)

Implementation:
- After `Agent.instrument_all()` in `main.py`, add `_SUPPRESS_LOGGERS = ["openai", "httpx", "anthropic", "hpack"]` list and suppression loop. `asyncio` is intentionally excluded.
- Write `tests/test_logger_suppression.py`. The test must redirect DB creation using `monkeypatch.setenv("CO_CLI_HOME", str(tmp_path))` before importing `co_cli.main` to avoid writing to `~/.co-cli/` during the test run. Assert `logging.getLogger("openai").level == logging.WARNING` and `logging.getLogger("httpx").level == logging.WARNING`.

done_when: |
  `uv run pytest tests/test_logger_suppression.py -x` passes;
  `python -c "import co_cli.main, logging; assert logging.getLogger('openai').level == logging.WARNING"` exits 0
success_signal: openai/httpx/anthropic log lines no longer appear in verbose co output
prerequisites: []

### TASK-3: Provider error span events

files: `co_cli/context/orchestrate.py`, `tests/test_orchestrate_error_event.py` (new)

Implementation:
- Locate the terminal `ModelHTTPError` branch in `run_turn()`. Set `turn_state.outcome = "error"` first, then add the span event, then return — in that order. Both 429/5xx and budget-exhausted 400 reach this branch and both must receive the event. Only the `continue` iteration of the 400 reformulation retry is excluded.
- Write `tests/test_orchestrate_error_event.py`:
  - Trigger a terminal 429 `ModelHTTPError` through `run_turn()` and assert the resulting `co.turn` span has a `provider_error` event with `http.status_code = 429`.
  - Assert an error body longer than 500 chars is truncated to exactly 500.
  - Assert the HTTP 400 reformulation retry path (budget > 0, `continue`) does NOT add a `provider_error` event.
  - Assert a budget-exhausted 400 (budget == 0) DOES add a `provider_error` event.

done_when: |
  `uv run pytest tests/test_orchestrate_error_event.py -x` passes;
  test asserts a `provider_error` span event is present on a `co.turn` span produced by a terminal 429 error
success_signal: after a provider 429 failure, `co logs` SQL returns a `provider_error` event with the HTTP status code on the corresponding `co.turn` span
prerequisites: []

---

## Testing

During implementation, scope to affected test files:
```
mkdir -p .pytest-logs && uv run pytest tests/test_telemetry_redaction.py tests/test_logger_suppression.py tests/test_orchestrate_error_event.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-observability-hardening.log
```

Before shipping:
```
scripts/quality-gate.sh full
```

---

## Open Questions

- Whether `redact_patterns` should be user-overridable via `settings.json`. Recommended v1: yes — expose it in `ObservabilityConfig` so users with unusual secret formats can extend the default list. Shipping with defaults is sufficient; no forced configuration.


## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev observability-hardening`

---

## Delivery Summary — TASK-1

**Shipped:** v0.7.152

**Changes:**
- `co_cli/config/_observability.py` — added `redact_patterns: list[str]` to `ObservabilitySettings` with 6 default patterns (OpenAI/Anthropic `sk-*`, Bearer tokens, GitHub `ghp_`, generic `api_key=`, AWS AKIA IDs, PEM private key headers); module-level `_DEFAULT_REDACT_PATTERNS` constant
- `co_cli/observability/_telemetry.py` — added `_MAX_REDACT_LEN = 65536`, `_redact()` helper, updated `SQLiteSpanExporter.__init__()` to accept `redact_patterns: list[str] | None`; redaction applied in `export()` to all span attribute string values and all event attribute string values before serialisation; `setup_tracer_provider()` accepts and forwards `redact_patterns`
- `co_cli/main.py` — passes `settings.observability.redact_patterns` to `setup_tracer_provider()`
- `tests/test_telemetry_redaction.py` — 7 new tests covering: `sk-` key redaction (SQLite round-trip), Bearer token in event attrs, clean value pass-through, identity field integrity, oversized value bypass, `_redact()` multi-match, `_redact()` size guard

**Test result:** 478/478 passed