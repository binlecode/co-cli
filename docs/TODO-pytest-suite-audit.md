# Pytest Suite Audit

Date: 2026-03-30

Scope: audit the current full pytest suite against the repository testing policy, then trace from tests into production code to identify coverage holes and functional gaps.

## Findings

- [ ] Policy concern: several tests drift into internal/spec-style assertions rather than critical functional bug-detection.
  Evidence:
  `tests/test_display.py` asserts `TerminalFrontend.active_surface()` and `active_status_text()` directly.
  `tests/test_agent.py` hard-codes exact tool inventories and approval maps.
  `tests/test_background.py` directly exercises `TaskStorage` CRUD internals.
  `tests/test_subagent_tools.py` constructs `CoDeps`, `CoCapabilityState`, `CoSessionState`, and `CoRuntimeState` directly.
  Note:
  This item needs nuance: lower-layer functional tests are valid when they exercise real code paths and detect critical bugs. The audit concern is specifically tests that overfit implementation structure or internal state instead of behavior contracts.

- [x] Audit finding: `ensure_ollama_warm(...)` calls were flagged as missing `asyncio.timeout`.
  Resolution: NOT a policy violation. `ensure_ollama_warm` is infrastructure prep — it loads and
  primes the model before the test timeout window begins. Wrapping it in a tight timeout causes
  false failures unrelated to the behavior under test (the priming inference can take 15–30s for
  large models even when already loaded, depending on server state after prior tests).
  Correct pattern: call `ensure_ollama_warm` unconstrained before the `asyncio.timeout` block.
  The timeout window must cover only the actual LLM call(s) under test.
  Enhancement: added elapsed-time reporting to `ensure_ollama_warm` output for visibility.

- [x] Coverage gap: deferred approval scoping and remember/auto-approve behavior are not covered end-to-end.
  Production paths:
  `co_cli/context/_orchestrate.py::_collect_deferred_tool_approvals`
  `co_cli/tools/_tool_approvals.py::resolve_approval_subject`
  `co_cli/tools/_tool_approvals.py::is_auto_approved`
  `co_cli/tools/_tool_approvals.py::record_approval_choice`
  Fixed: added 6 tests in `tests/test_commands.py` covering shell/path/domain/tool subject resolution,
  remember semantics (idempotent), auto-approval after remember, and deny-no-persist behavior.

- [~] Coverage gap: `run_turn()` retry, interruption, and output-limit branches are largely untested.
  Production paths:
  `co_cli/context/_orchestrate.py::_build_interrupted_turn_result`
  `co_cli/context/_orchestrate.py::_check_output_limits`
  `co_cli/context/_orchestrate.py::run_turn` HTTP 400 reflection branch
  `co_cli/context/_orchestrate.py::run_turn` 429/5xx retry branch
  `co_cli/context/_orchestrate.py::run_turn` `ModelAPIError` retry branch
  Partial fix: `_build_interrupted_turn_result` covered by 2 new unit tests in `tests/test_commands.py`
  (dangling-tool-call drop + clean-history retention).
  Remaining: `_check_output_limits` (requires real AgentRunResult — needs live LLM call to produce
  finish_reason="length"), HTTP retry branches (no test infrastructure for injecting HTTP errors
  without mocks — deferred).

- [x] Coverage gap: `web_fetch` success-path behavior is not verified.
  Production paths:
  final redirect safety check
  content-type allowlist behavior
  HTML to Markdown conversion
  truncation behavior
  Fixed: added `test_web_fetch_html_to_markdown` (Wikipedia page, verifies no raw HTML tags, all
  result keys present); added 6 unit tests for `_html_to_markdown` and `_is_content_type_allowed`
  covering tags/links conversion and the full allow/deny MIME matrix.
  Note: truncation path (>100K chars) not directly exercised — Wikipedia page is well under limit.

- [x] Coverage gap: shell deny-policy coverage is incomplete.
  Production paths in `co_cli/tools/_shell_policy.py` not directly covered:
  control characters
  heredoc denial
  `VAR=$(...)` env-injection denial
  Fixed: added `test_shell_deny_control_character`, `test_shell_deny_heredoc`,
  `test_shell_deny_env_injection` to `tests/test_shell.py`. All 4 denial rules now have coverage.

- [ ] Coverage gap: sub-agent tests only cover unavailable-guard branches and result-model validation, not actual delegated execution behavior.
  Production paths missing meaningful coverage:
  request-budget accounting
  usage merge into parent turn
  research sub-agent empty-result retry path
  structured successful result paths for coder/research/analysis/thinking tools

- [ ] Coverage gap: CLI loop behavior in `co_cli/main.py` is only lightly covered.
  Current direct CLI regression coverage is mainly startup failure handling.
  Thin or absent coverage:
  MCP init degradation
  completer refresh after session capability init
  slash-command integration inside `_chat_loop`
  skill env cleanup
  foreground turn finalization path

## Current State

- [ ] Current full regression log is green: `.pytest-logs/20260330-003807-full-regression.log`
- [ ] No active `skip`, `xfail`, `monkeypatch`, or `unittest.mock` usage was found in `tests/`
- [ ] `pyproject.toml` enforces `-x --durations=0`

## Next Actions

- [x] `ensure_ollama_warm` timeout question resolved — warmup is intentionally outside the
  timeout window; added elapsed-time reporting to the helper for visibility.
- [x] Add approval-memory coverage for shell/path/domain/tool subjects — 6 tests added.
- [x] Add deny-branch tests for the remaining shell policy rules — 3 tests added (control chars, heredoc, env-injection).
- [x] Add at least one successful `web_fetch` test that validates conversion — Wikipedia HTML→markdown test added; `_is_content_type_allowed` MIME matrix tests added.
- [~] Add `run_turn()` failure-path tests for interruption and provider retry behavior.
  `_build_interrupted_turn_result` covered (2 unit tests). HTTP retry and `_check_output_limits`
  branches deferred — require real AgentRunResult or live LLM call to produce the trigger condition.
- [ ] Reframe the “functional tests only” concern: narrow `EXPECTED_TOOLS_CORE` concern in
  `test_agent.py` — the inventory set pins tool names as a spec rather than testing behavioral
  contracts. `test_display.py` and `test_subagent_tools.py` are NOT spec-style violations.
- [ ] Decide whether `TaskStorage` and similar low-level tests are kept as critical functional component tests or rewritten through higher-level tools.
