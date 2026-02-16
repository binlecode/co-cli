# FIX: Gateway-First Rule Gaps

Source: review against Section 6 rules in `docs/LEARN-openclaw-to-cli.md`.

## 1. High — Slack references remained after runtime removal (resolved)

- Rule(s): 3
- Evidence (pre-fix snapshot):
  - `README.md:3`
  - `README.md:89`
  - `docs/DESIGN-00-co-cli.md:99`
  - `docs/DESIGN-01-agent-chat-loop.md:87`
  - `docs/index.md:29`
  - `pyproject.toml:8`
- Resolution:
  - Removed Slack references from runtime/product docs and metadata (`README`, design docs, docs index, project description).
  - Removed broken Slack doc links to missing `DESIGN-12-tool-slack.md`.
  - Added a repo grep verification step to check active surfaces for Slack references.

## 2. Medium — `!command` bypass path in chat loop (resolved)

- Rule(s): 1, 6
- Evidence (pre-fix snapshot):
  - `co_cli/main.py:155`
  - `co_cli/main.py:160`
  - `co_cli/main.py:164`
  - `co_cli/main.py:168`
- Resolution:
  - Removed the `!command` dispatch branch from `chat_loop()`; user input now routes to slash commands or `run_turn()`.
  - Unified execution and rendering through orchestration + `FrontendProtocol` callbacks.
  - Updated design/roadmap docs that still described the `!cmd` bypass path.

## 3. Low — Provider retry/reflect policy is not fully centralized for summarization paths (resolved)

- Rule(s): 5
- Evidence:
  - `co_cli/_commands.py:104` — `/compact` calls `summarize_messages()` directly
  - `co_cli/_commands.py:120` — broad `except Exception` fallback
  - `co_cli/_history.py:275` — `truncate_history_window` calls `summarize_messages()` directly
  - `co_cli/_history.py:287` — broad `except Exception` fallback with static marker
- Problem:
  - Main chat turns use `_provider_errors.py` policy, but `/compact` and history summarization use direct calls with broad fallback handling.
  - Error behavior differs by call path: a 429 rate limit during `/compact` gets the same treatment as a 401 auth failure (immediate give-up), when it should retry.
- Fix:
  1. Introduce shared summarization runner that reuses provider error classification/backoff policy.
  2. Route `/compact` and history compaction through that runner.
  3. Add tests for 400/429/network behavior in summarization code paths.

### Detailed Implementation Plan

#### Title
Centralize provider retry/reflect policy for summarization paths (`/compact` + history compaction)

#### Summary
Align `/compact` and `truncate_history_window()` summarization behavior with the same provider error policy used by turn orchestration, so 400/429/network handling is consistent across all LLM call paths.

Default behaviors preserved:
- `/compact` terminal/exhausted failure: no-op + classified error message (history unchanged).
- Sliding-window compaction terminal/exhausted failure: static marker fallback.

#### Current Gap (code references)
- `/compact` directly calls `summarize_messages()` and catches broad `Exception` in `co_cli/_commands.py:120`.
- History compaction directly calls `summarize_messages()` and catches broad `Exception` in `co_cli/_history.py:287`.
- Main turn loop already uses centralized `classify_provider_error()` in `co_cli/_orchestrate.py:535`.

#### Implementation Steps

1. Add shared summarization policy runner in `co_cli/_history.py`
- Introduce internal async runner (e.g. `_run_summarization_with_policy`) used by both `/compact` and `truncate_history_window`.
- Signature: `async def _run_summarization_with_policy(messages, model, *, max_retries: int = 2, personality_active: bool = False) -> str | None`
- Returns summary text on success, `None` on terminal/exhausted failure (logs classified error internally).
- Runner behavior:
  - Execute summarization LLM call via `summarize_messages()`.
  - Catch `ModelHTTPError` and `ModelAPIError`.
  - Reuse `classify_provider_error()` from `co_cli/_provider_errors.py`.
  - Apply policy classes adapted for the no-tools summarization context:
    - `REFLECT` (400): treat as `BACKOFF_RETRY` — summarization has no tool calls to reformulate, so a 400 typically means input too large or malformed; retry with backoff delay (same as 429 path).
    - `BACKOFF_RETRY` (429/5xx/network): exponential backoff retry with inline math (`min(delay * backoff ** attempt, 30.0)`).
    - `ABORT` (401/403/404/terminal): log classified reason and return `None`.
  - On retries exhausted: log classified reason and return `None`.

2. Route both call paths through shared runner
- `co_cli/_commands.py` (`_cmd_compact`):
  - Replace direct `summarize_messages(...)` call with `_run_summarization_with_policy(...)`.
  - On `None` return: print `Compact failed` with classified error and return `None` (history unchanged).
- `co_cli/_history.py` (`truncate_history_window`):
  - Replace direct `summarize_messages(...)` call with `_run_summarization_with_policy(...)`.
  - On `None` return: keep existing `_static_marker(...)` fallback path.

3. Wire retry budget as a parameter (consistent with `run_turn()`)
- The shared runner accepts `max_retries` as a direct parameter, matching how `run_turn()` receives `http_retries` from the chat loop.
- `truncate_history_window`: reads `settings.model_http_retries` via the existing import path in `_history.py`, or receives it from its caller.
- `/compact` path: `CommandContext` already has access to settings; passes `max_retries` to the runner.
- No new field on `CoDeps` — avoids creating a second source of truth for `model_http_retries` (which `run_turn()` already receives as a plain parameter at `main.py:220`).

#### Design Decisions

**Why REFLECT maps to BACKOFF_RETRY for summarization:**
In `run_turn()`, REFLECT injects "your previous tool call was rejected" and re-runs the agent with tools. The summarizer agent has no tools — it's a plain prompt-to-text completion. A 400 on a summarization call typically means the input is too large or the request is malformed, not a bad tool call. Reflecting with tool-call instructions is meaningless. Retrying with backoff (giving the provider a moment) is the only useful action.

**Why no typed exception:**
The runner has exactly two callers with simple, already-different fallback behaviors (print error vs. static marker). Returning `str | None` is sufficient — `None` means failure, callers branch on it. The runner logs the classified error details internally. If a caller later needs the error classification, promote to a typed exception then.

**Why no shared retry-delay helper:**
The backoff math is 3 lines (`min(delay * backoff ** attempt, cap)`). Extracting a shared utility for two callers is premature — if a third caller appears, extract then.

#### Public API / Interface Changes
- Internal-only addition in `co_cli/_history.py`:
  - `_run_summarization_with_policy()` — private async runner

No changes to `CoDeps`, `create_deps()`, CLI surface, or settings keys.

#### Detailed Behavior Spec

1. `/compact`
- Success: unchanged (2-message compacted history).
- 400/429/5xx/network with retries left: backoff retry.
- Terminal (401/403/404) or retries exhausted: `Compact failed: <classified reason>`; history unchanged.

2. `truncate_history_window`
- Success: unchanged (summary marker inserted).
- 400/429/5xx/network with retries left: backoff retry.
- Terminal (401/403/404) or retries exhausted: static marker fallback.

#### Test Plan

1. Policy-runner tests in `tests/test_history.py`
- 429 -> backoff retry -> success.
- 400 -> backoff retry -> success (verifies REFLECT-to-BACKOFF mapping).
- `ModelAPIError` network -> backoff retry -> success.
- 401/403/404 -> immediate `None` (abort).
- Retries exhausted -> `None`.

2. Caller behavior tests
- `/compact` preserves history on `None` return from runner.
- `truncate_history_window` uses static marker on `None` return from runner.

3. Keep existing live-provider tests
- Existing summarization and `/compact` functional tests remain to validate end-to-end behavior.

#### Acceptance Criteria
- Summarization paths no longer rely on broad exception fallback for provider errors.
- Both `/compact` and history compaction run through `classify_provider_error()` policy.
- 400/429/network behavior covered by tests.
- Existing success behavior and fallback UX are preserved.

#### Assumptions / Defaults
- `/compact` failure mode fixed as: no-op + classified error.
- Retry budget source: `settings.model_http_retries` passed as parameter (not via `CoDeps`).
- 400 in summarization context treated as retryable (backoff), not reflectable (no tools to reformulate).
- Referenced source doc `docs/LEARN-openclaw-to-cli.md` is not present in current repo snapshot; plan is grounded in current code and this fix doc.
