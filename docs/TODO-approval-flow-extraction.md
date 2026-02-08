# TODO: Approval Flow Extraction

**Priority:** Low — code quality refactor, not a bug fix or feature. Current code works. Do this when a second consumer (CI, headless, API) needs the orchestration without Rich, or when testability becomes a blocker.

**Trigger:** Need to test approval logic without a terminal, or add a non-interactive approval backend.

---

## Problem

`chat_loop()` in `main.py` mixes orchestration with UI. `_stream_agent_run` (main.py:155) and `_handle_approvals` (main.py:243) are coupled to Rich (`Live`, `Markdown`, `Panel`, `Prompt.ask`), making them untestable without a terminal.

## Approach

Extract into `co_cli/_orchestrate.py` with two injected callback protocols:

- **`DisplayCallback`** — `on_text_delta`, `on_text_commit`, `on_tool_call`, `on_tool_result`
- **`ApprovalCallback`** — `async __call__(tool_name, args, *, auto_approved) -> ApprovalDecision`

The orchestrator never imports Rich. CLI creates `RichDisplay` + `CliApprovalCallback`; tests create `BufferDisplay` + auto-approve.

## Items

- [ ] Create `co_cli/_orchestrate.py` with protocols + `run_with_approvals()`
- [ ] Move `_patch_dangling_tool_calls()`, `_stream_agent_run()` to `_orchestrate.py`
- [ ] Move safe-command auto-approval logic into `run_with_approvals()`
- [ ] Create `RichDisplay` in `main.py` (owns `Live`, `Markdown`, `Panel`, throttle)
- [ ] Create `CliApprovalCallback` in `main.py` (owns `Prompt.ask`, SIGINT swap)
- [ ] Refactor `chat_loop()` to use `run_with_approvals()`
- [ ] Add functional tests using `BufferDisplay` + auto-approve (see `TODO-approval-interrupt-tests.md`)

## File changes

| File | Change |
|---|---|
| `co_cli/_orchestrate.py` | New — protocols, `run_with_approvals`, `_stream_agent_run`, `_patch_dangling_tool_calls` |
| `co_cli/main.py` | Remove extracted functions; add `RichDisplay`, `CliApprovalCallback` |
| `tests/test_orchestrate.py` | New — `BufferDisplay`, auto-approve callback, functional tests |
| `co_cli/_approval.py` | No change |
