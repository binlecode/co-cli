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
