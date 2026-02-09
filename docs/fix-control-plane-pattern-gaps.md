# FIX: Gateway-First Rule Gaps

Source: review against Section 6 rules in `docs/LEARN-openclaw-to-cli.md`.

## 1. High — Slack tool error path can return `None` (contract break)

- Rule(s): 3
- Evidence:
  - `co_cli/tools/slack.py:103`
  - `co_cli/tools/slack.py:151`
  - `co_cli/tools/slack.py:191`
  - `co_cli/tools/slack.py:235`
  - `co_cli/tools/slack.py:291`
  - `co_cli/tools/_errors.py:25`
- Problem:
  - `handle_tool_error()` returns a terminal error dict for `TERMINAL`, but Slack callers do not `return` that value.
  - This can fall through and return `None`, violating the tool return contract.
- Fix:
  1. Replace `handle_tool_error(kind, message)` with `return handle_tool_error(kind, message)` in all Slack tool `except SlackApiError` blocks.
  2. Add regression tests that simulate terminal Slack errors and assert returned dict contains `display` and `error=True`.
  3. Add static return-type guard (lint/mypy/pytest assertion) to prevent future `None` returns.

## 2. Medium — `!command` path bypasses orchestration boundary

- Rule(s): 1, 6
- Evidence:
  - `co_cli/main.py:155`
  - `co_cli/main.py:160`
  - `co_cli/main.py:164`
  - `co_cli/main.py:168`
- Problem:
  - Direct shell path executes and renders in `main.py`, not through orchestration/frontend boundary used for LLM turns.
  - This splits control-plane behavior and duplicates display/error handling patterns.
- Fix:
  1. Move `!command` execution into a dedicated orchestration helper (for example in `_orchestrate.py`).
  2. Emit output/errors through `FrontendProtocol` callbacks instead of direct `console.print`.
  3. Keep `main.py` focused on dispatch only.

## 3. Medium — Gemini key is injected via process-global env mutation

- Rule(s): 4 (future multi-session risk)
- Evidence:
  - `co_cli/agent.py:53`
- Problem:
  - `os.environ["GEMINI_API_KEY"] = ...` mutates global process state at agent construction.
  - Works today, but weakens isolation assumptions for daemon/multi-session evolution.
- Fix:
  1. Prefer provider construction that accepts API key directly without process-wide env mutation.
  2. If env injection is unavoidable, isolate it behind a narrow adapter and document single-process implications explicitly.
  3. Add test coverage for provider initialization behavior to avoid accidental regressions.

## 4. Low — Provider retry/reflect policy is not fully centralized for summarization paths

- Rule(s): 5
- Evidence:
  - `co_cli/_commands.py:95`
  - `co_cli/_commands.py:99`
  - `co_cli/_history.py:143`
  - `co_cli/_history.py:149`
  - `co_cli/_history.py:200`
- Problem:
  - Main chat turns use `_provider_errors.py` policy, but `/compact` and history summarization use direct calls with broad fallback handling.
  - Error behavior differs by call path.
- Fix:
  1. Introduce shared summarization runner that reuses provider error classification/backoff policy.
  2. Route `/compact` and history compaction through that runner.
  3. Add tests for 400/429/network behavior in summarization code paths.

