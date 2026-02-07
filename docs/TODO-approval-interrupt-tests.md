# TODO: Approval & Interrupt Regression Tests

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (§5.3 regression tests)

---

## Gap Analysis

### What's untested

Three critical code paths in the chat loop have zero test coverage:

| Code path | Location | Risk |
|---|---|---|
| `_patch_dangling_tool_calls()` | main.py:118–145 | Silent history corruption if broken — next `agent.run()` fails with opaque model errors |
| `_handle_approvals()` | main.py:151–208 | Approval bypass, YOLO mode escalation, safe-command auto-approval |
| `_display_tool_outputs()` | main.py:211–233 | Invisible to the user if broken — tool results silently dropped |

### Why they're untested

All three are entangled with console I/O (`Prompt.ask`, `console.print`, `signal.signal`). The testing policy forbids mocks, so the standard approach (mock stdin/console) isn't available.

However:
- `_patch_dangling_tool_calls` is a **pure function** — takes a message list, returns a patched list. No I/O. Directly testable today.
- `_display_tool_outputs` can be tested by capturing console output via `console.file` (Rich supports `StringIO` as output target). This is functional — real Rich rendering to a real stream.
- `_handle_approvals` requires extraction first (see `TODO-approval-flow-extraction.md`) to separate the approval logic from the interactive prompt.

### Existing test patterns

The codebase tests tools functionally using a minimal `Context` dataclass (see `tests/test_shell.py:22`):

```python
@dataclass
class Context:
    deps: CoDeps

def _make_ctx(sandbox, **overrides):
    return Context(deps=CoDeps(sandbox=sandbox, auto_confirm=True, session_id="test", **overrides))
```

For message-history tests, `tests/test_history.py` constructs synthetic `ModelRequest`/`ModelResponse` objects. The same approach works for the approval/interrupt tests.

---

## Design

### Test file: `tests/test_approval.py`

#### Group 1: `_patch_dangling_tool_calls` (pure function, no deps)

Tests construct synthetic pydantic-ai message lists and verify the patching logic.

```python
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart,
    TextPart, UserPromptPart,
)
from co_cli.main import _patch_dangling_tool_calls
```

| Test | Input | Expected |
|---|---|---|
| `test_patch_empty_history` | `[]` | `[]` — no crash |
| `test_patch_no_dangling_calls` | History ending with `ModelRequest` (user prompt) | Unchanged — nothing to patch |
| `test_patch_single_dangling_call` | Response with 1 `ToolCallPart`, no following `ToolReturnPart` | Appended `ModelRequest` with matching `ToolReturnPart(content="Interrupted by user.")` |
| `test_patch_multiple_dangling_calls` | Response with 3 `ToolCallPart`s | Appended `ModelRequest` with 3 matching `ToolReturnPart`s, correct `tool_call_id` and `tool_name` for each |
| `test_patch_already_answered_calls` | Response with `ToolCallPart` followed by `ModelRequest` containing its `ToolReturnPart` | Unchanged — calls already answered |
| `test_patch_custom_error_message` | Dangling call + `error_message="Cancelled"` | Return part has `content="Cancelled"` |
| `test_patch_preserves_prior_history` | 10 messages + dangling call at end | First 10 messages untouched, 11th is the patch |
| `test_patch_response_with_text_and_tool_call` | Response has both `TextPart` and `ToolCallPart` | Tool call patched, text part untouched |

#### Group 2: `_display_tool_outputs` (Rich rendering)

Tests use `Console(file=StringIO())` to capture rendered output.

```python
from io import StringIO
from rich.console import Console

def _capture_tool_outputs(old_len, messages):
    buf = StringIO()
    test_console = Console(file=buf, force_terminal=True)
    # Temporarily patch co_cli.display.console
    ...
    return buf.getvalue()
```

| Test | Input | Expected in captured output |
|---|---|---|
| `test_display_shell_output` | Messages with `run_shell_command` ToolCallPart + ToolReturnPart(content="file.txt") | "file.txt" appears in output |
| `test_display_dict_output` | ToolReturnPart with `content={"display": "Found 3 files"}` | "Found 3 files" appears |
| `test_display_skips_empty` | ToolReturnPart with `content=""` | No panel rendered |
| `test_display_only_new_messages` | `old_len=5`, 8 total messages | Only messages 5–7 processed |

#### Group 3: DeferredToolRequests flow (E2E, requires LLM)

Conditional on `LLM_PROVIDER` being set. Uses a real agent with `requires_approval=True` tools.

```python
@pytest.mark.asyncio
async def test_deferred_approval_approve_all():
    """Full approval flow: agent triggers tool, auto-approve, get result."""
    if not os.getenv("LLM_PROVIDER"):
        return

    agent, model_settings, _ = get_agent()
    deps = create_deps()
    deps.auto_confirm = True

    result = await agent.run(
        "List files in the current directory",
        deps=deps,
        model_settings=model_settings,
    )

    # With auto_confirm=True, DeferredToolRequests should be auto-handled
    # by _handle_approvals. But since we're calling agent.run() directly
    # (not chat_loop), we test the raw output type.
    if isinstance(result.output, DeferredToolRequests):
        # Manually approve all
        approvals = DeferredToolResults()
        for call in result.output.approvals:
            approvals.approvals[call.tool_call_id] = True
        result = await agent.run(
            None, deps=deps,
            message_history=result.all_messages(),
            deferred_tool_results=approvals,
            model_settings=model_settings,
        )

    assert isinstance(result.output, str)
    assert len(result.all_messages()) > 0
```

| Test | Scenario |
|---|---|
| `test_deferred_approval_approve_all` | Approve all deferred tools, verify final output is `str` |
| `test_deferred_approval_deny_all` | Deny all deferred tools via `ToolDenied`, verify agent handles gracefully |
| `test_deferred_approval_mixed` | Approve first tool, deny second — if agent triggers multiple |
| `test_message_history_intact_after_approval` | Verify `result.all_messages()` includes all tool calls and returns |

#### Group 4: Safe-command auto-approval (pure function)

`_is_safe_command` is already in `_approval.py` but has no dedicated test file.

| Test | Input | Expected |
|---|---|---|
| `test_safe_exact_match` | `"ls"` | `True` |
| `test_safe_prefix_with_args` | `"ls -la"` | `True` |
| `test_safe_git_multiword` | `"git status"` | `True` |
| `test_unsafe_git_push` | `"git push"` | `False` (not in safe list) |
| `test_unsafe_chaining_semicolon` | `"ls; rm -rf /"` | `False` |
| `test_unsafe_chaining_pipe` | `"ls \| xargs rm"` | `False` |
| `test_unsafe_chaining_ampersand` | `"cmd && evil"` | `False` |
| `test_unsafe_redirect` | `"echo x > /etc/passwd"` | `False` |
| `test_unsafe_backtick` | `` "echo `whoami`" `` | `False` |
| `test_unsafe_subshell` | `"echo $(whoami)"` | `False` |
| `test_custom_safe_list` | `"npm test"` with custom `["npm"]` | `True` |
| `test_empty_command` | `""` | `False` |

---

## Implementation Plan

### Items

- [ ] Create `tests/test_approval.py`
- [ ] Implement Group 1: `_patch_dangling_tool_calls` tests (8 tests, pure function)
- [ ] Implement Group 4: `_is_safe_command` tests (12 tests, pure function)
- [ ] Implement Group 2: `_display_tool_outputs` tests (4 tests, Rich capture)
- [ ] Implement Group 3: DeferredToolRequests E2E tests (4 tests, conditional on `LLM_PROVIDER`)
- [ ] Run full test suite to verify no regressions

### Dependencies

- Groups 1 and 4: no dependencies, can land immediately
- Group 2: depends on `_display_tool_outputs` being importable (currently in `main.py`, may move to `_orchestrate.py` per `TODO-approval-flow-extraction.md`)
- Group 3: requires `LLM_PROVIDER` and Docker for meaningful coverage

### File changes

| File | Change |
|---|---|
| `tests/test_approval.py` | New — all 4 test groups |

### Note on testing policy

All tests are functional — no mocks:
- Groups 1, 4: pure function tests with real pydantic-ai message objects
- Group 2: real Rich console rendering to a `StringIO` buffer
- Group 3: real LLM + real sandbox round-trips
