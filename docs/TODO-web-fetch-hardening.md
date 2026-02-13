# TODO: Web Fetch Hardening (Retry/Backoff + Error Policy)

**Date:** 2026-02-12
**Owner:** co-cli core
**Status:** Planned (MVP-first)
**Primary references:**
- `co_cli/tools/web.py`
- `co_cli/_provider_errors.py`
- `co_cli/tools/_errors.py`
- Peer patterns (local repos): Codex, Gemini CLI, OpenCode

---

## Objective

Make `web_fetch` and `web_search` resilient to transient web failures (rate limit, 5xx, network jitter) without causing model/tool retry thrash, while keeping policy behavior explicit and safe.

---

## Converged Best Practice (Peers)

Across Codex, Gemini CLI, and OpenCode, the common pattern is:

1. Classify errors first (retryable vs terminal), do not retry blindly.
2. Respect `Retry-After` (and `Retry-After-Ms` when present).
3. Use bounded exponential backoff with jitter.
4. Keep retries budgeted (attempt cap + delay cap), then fail with actionable error.
5. Surface retry state to users/telemetry so behavior is explainable.

This TODO implements that converged baseline for co-cli web tools.

---

## Non-Negotiable Invariants

1. No unbounded retry loops in tools.
2. No retry on clearly terminal classes (`400`, `401`, `403`, `404`, validation errors).
3. Retry only idempotent fetch operations (`GET`) and only for retryable conditions.
4. Backoff must be jittered and capped.
5. Error message to model must be actionable (retry later, switch source, or fix input).

---

## Scope

This TODO covers:
- `web_fetch`/`web_search` HTTP retry/backoff policy and implementation
- error classification shared helper(s)
- retry observability and tests

This TODO does not cover:
- non-web tools (Slack/Google/shell)
- provider (LLM API) retry logic in `_orchestrate.py`
- broad circuit-breaker infrastructure across all tools

---

## Phase W0: Baseline Header/Profile Hardening

Goal: reduce avoidable 403/anti-bot blocks from strict hosts.

### Tasks

- [ ] Keep explicit `User-Agent` for `web_fetch` requests.
- [ ] Keep host-aware headers for Wikimedia (`Api-User-Agent`).
- [ ] Add tests that verify header builder behavior by hostname.
- [ ] Document host-profile behavior in `docs/DESIGN-13-tool-web-search.md`.

### Target Files

- `co_cli/tools/web.py`
- `tests/test_web.py`
- `docs/DESIGN-13-tool-web-search.md`

---

## Phase W1: Shared Web Retry Classifier + Policy

Goal: centralize retry decisions and avoid ad hoc per-exception handling.

### Tasks

- [ ] Add shared helper module for web retry policy (for example `co_cli/tools/_http_retry.py`).
- [ ] Implement `classify_web_http_error(...)` returning:
- [ ] retryable flag
- [ ] user/model-safe message
- [ ] suggested delay (from headers/body when available)
- [ ] Parse and normalize:
- [ ] `Retry-After` seconds
- [ ] HTTP-date `Retry-After`
- [ ] optional `Retry-After-Ms`
- [ ] Default retryable status set: `408`, `409`, `425`, `429`, `500`, `502`, `503`, `504`.
- [ ] Default terminal status set: `400`, `401`, `403`, `404`, `422` + all other non-listed `4xx`.
- [ ] Keep all constants in one place for maintainability.

### Target Files

- `co_cli/tools/_http_retry.py` (new)
- `co_cli/tools/web.py`
- `tests/test_web_retry_policy.py` (new)

---

## Phase W2: Bounded Backoff + Jitter in Web Tools

Goal: retry transient failures inside the tool before surfacing failure to the model.

### Tasks

- [ ] Add small retry loop for `web_fetch` and `web_search` network calls.
- [ ] Use bounded exponential backoff with jitter (full jitter preferred).
- [ ] Respect `Retry-After`/`Retry-After-Ms` when greater than computed backoff.
- [ ] Cap by both max attempts and max sleep.
- [ ] Keep retries for idempotent `GET` only.
- [ ] Return actionable terminal failures after budget exhaustion.
- [ ] Prevent duplicate nested retries (tool layer only retries transport; model sees one final outcome).

### Config (MVP)

- [ ] Add settings for:
- [ ] `web_http_max_retries` (default small, e.g. `2`)
- [ ] `web_http_backoff_base_seconds`
- [ ] `web_http_backoff_max_seconds`
- [ ] `web_http_jitter_ratio` (if not using full jitter)
- [ ] Wire env vars + defaults + status visibility.

### Target Files

- `co_cli/config.py`
- `co_cli/main.py`
- `co_cli/deps.py`
- `co_cli/status.py`
- `co_cli/tools/web.py`
- `settings.reference.json`

---

## Phase W3: Error Contract Hardening (ModelRetry vs terminal_error)

Goal: avoid retry storms and improve model recovery choices.

### Tasks

- [ ] Define explicit decision rules:
- [ ] Use `ModelRetry` for transient/recoverable errors only.
- [ ] Use `terminal_error(...)` for terminal tool outcomes where retry is not useful.
- [ ] Standardize user-facing error wording for terminal classes:
- [ ] `403`: blocked by origin policy; suggest alternate source.
- [ ] `404`: page not found; suggest another URL/query.
- [ ] `401`: auth required; not fetchable anonymously.
- [ ] Ensure error text is short and deterministic for tests.

### Target Files

- `co_cli/tools/web.py`
- `co_cli/tools/_errors.py`
- `tests/test_web.py`

---

## Phase W4: Observability + Regression Coverage

Goal: make retry behavior inspectable and prevent regressions.

### Tasks

- [ ] Emit retry attempt metadata in telemetry/status:
- [ ] hostname
- [ ] status/error class
- [ ] attempt index
- [ ] chosen delay
- [ ] Add regression tests for:
- [ ] retryable `429` with `Retry-After`
- [ ] retryable `5xx` with capped backoff
- [ ] terminal `403` no internal retry
- [ ] network timeout retry path
- [ ] Add an E2E guard for the Wikipedia flow to ensure no repeated `RetryPromptPart` thrash when fetch is terminal.

### Target Files

- `co_cli/_telemetry.py`
- `co_cli/tools/web.py`
- `tests/test_web_retry_policy.py` (new)
- `tests/test_llm_e2e.py`

---

## Exit Criteria

- [ ] Web tools have one shared retry classifier and no ad hoc retry branches.
- [ ] Retries are bounded, jittered, and `Retry-After` aware.
- [ ] Terminal errors do not trigger unnecessary model retry loops.
- [ ] New tests cover retryable/terminal classes and backoff parsing.
- [ ] Design doc reflects final runtime behavior.
