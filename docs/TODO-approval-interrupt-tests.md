# TODO: Approval & Interrupt Regression Tests

**Origin:** RESEARCH-PYDANTIC-AI-CLI-BEST-PRACTICES.md gap analysis (§5.3 regression tests)

---

## Remaining Work

One code path in the chat loop has zero test coverage:

| Code path | Location | Risk |
|---|---|---|
| `_patch_dangling_tool_calls()` | main.py:128–155 | Silent history corruption if broken — next `agent.run()` fails with opaque model errors |

### Why it's untested

The function is a **pure function** — takes a message list, returns a patched list. No I/O. Directly testable today. It was simply never written.

### Existing test patterns

For message-history tests, `tests/test_history.py` constructs synthetic `ModelRequest`/`ModelResponse` objects. The same approach works here.

---

## Design

### Test file: `tests/test_approval.py`

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

---

## Implementation Plan

### Items

- [ ] Create `tests/test_approval.py`
- [ ] Implement `_patch_dangling_tool_calls` tests (8 tests, pure function)
- [ ] Run full test suite to verify no regressions

### File changes

| File | Change |
|---|---|
| `tests/test_approval.py` | New — patch tests only |

### Note on testing policy

All tests are functional — no mocks. Pure function tests with real pydantic-ai message objects.

---

## Already Completed

The following groups from the original TODO are now covered in `tests/test_commands.py`:

| Group | Tests | Location |
|---|---|---|
| `_is_safe_command` (Group 4) | 7 tests covering exact match, prefix+args, multi-word, chaining, empty list, unknown, partial name | `test_commands.py:304–363` |
| DeferredToolRequests E2E (Group 3) | approve-all, deny-all, auto-confirm flows | `test_commands.py:210–295, 366+` |

The `_display_tool_outputs` group (Group 2) is **obsolete** — the function was never extracted. Display logic lives inline in `_stream_agent_run()` as part of the streaming event loop; the Rich `StringIO` capture approach from the original design doesn't apply to this architecture.
