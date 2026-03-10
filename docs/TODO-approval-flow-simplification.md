# TODO: Approval Flow Simplification

**Slug:** `approval-flow-simplification`
**Type:** Refactor (behavior-preserving)

---

## Context

Review target: `docs/DESIGN-core-loop.md` and the current approval-processing code in:

- `co_cli/_orchestrate.py`
- `co_cli/tools/shell.py`
- `co_cli/_shell_policy.py`
- `co_cli/_exec_approvals.py`
- `co_cli/deps.py`

### Assessment of the reported over-design points

#### 1. `_handle_approvals()` mixes approval decisions with run resumption

**Verdict:** Agree

Current `_handle_approvals()` does two distinct jobs:

1. iterates deferred tool calls, applies skill/session/user approval rules, and builds
   `DeferredToolResults`
2. immediately resumes the agent run by calling `_stream_events(...)`

That is not a correctness bug, but it compresses two separate responsibilities into one
orchestration helper. It makes the approval loop harder to read, harder to test in isolation,
and harder to extend if approval collection and continuation need different behavior later.

The correct refactor is structural, not behavioral:
- approval collection becomes a pure-ish step that returns `DeferredToolResults`
- turn resumption stays in a separate orchestration helper

#### 2. Shell approval has three conceptual layers

**Verdict:** Partially agree

The three layers are real:

1. deny policy (`_shell_policy.py`)
2. safe-prefix allow path (`_approval.py` via `_is_safe_command`)
3. persistent remembered approvals (`_exec_approvals.py`)

This is justified by the product requirements. The problem is not the existence of three
layers; the problem is that the logic is spread across multiple files with no single approval
facade that explains the combined behavior. Today, understanding shell approval requires
reading both `tools/shell.py` and `_orchestrate.py`, plus the policy/persistence helpers.

So the code is not fundamentally over-designed here, but it is under-encapsulated.

#### 3. Session auto-approval is by tool name, shell persistence is pattern-based

**Verdict:** Partially agree

This is intentionally asymmetric:

- non-shell tools are coarse-grained and session-local
- shell commands are high-risk and need narrower, persistent command-pattern approval

That asymmetry is pragmatic and should remain unless product requirements change.
What is uneven today is the implementation shape:

- shell persistence is handled ad hoc in `run_shell_command()` and prompt handling
- non-shell persistence is handled ad hoc in `_handle_approvals()`

The fix is not to force both into one storage model. The fix is to make the differing
strategies explicit behind one approval helper layer.

---

## Problem & Outcome

**Problem:** Approval behavior is spread across orchestration and tool code with weak
separation between:

- approval decision collection
- approval persistence strategy
- deferred-run resumption

This makes the approval path denser than it needs to be and leaves shell-specific persistence
logic leaking into multiple layers.

**Outcome:** Keep current user-visible behavior, but refactor approval handling into explicit
layers:

- orchestration collects approval answers
- orchestration resumes deferred runs separately
- a single helper module owns approval argument decoding, description formatting, session
  auto-approval checks, and persistence strategy dispatch
- shell tools consult that helper for remembered approvals instead of open-coding JSON-store
  lookups

---

## Scope

**In scope:**
- split approval collection from deferred-run resumption in `co_cli/_orchestrate.py`
- add a centralized approval helper module for strategy and persistence glue
- route `run_shell_command()` through that helper for remembered-approval checks
- update tests to match the new helper boundaries
- update `docs/DESIGN-core-loop.md` so the flow matches the refactored orchestration

**Out of scope:**
- changing shell policy semantics
- changing approval storage format
- changing skill-grant semantics
- changing non-shell approvals from session-by-tool-name to pattern-based storage

---

## High-Level Design

### 1. Split orchestration responsibilities

Replace the current `_handle_approvals()` shape with two helpers:

- `_collect_deferred_tool_approvals(...) -> DeferredToolResults`
- `_resume_deferred_tool_requests(...) -> tuple[result, streamed_text]`

`run_turn()` keeps the loop:

```text
while result.output is DeferredToolRequests:
    approvals = _collect_deferred_tool_approvals(...)
    result = _resume_deferred_tool_requests(..., approvals)
```

This keeps the turn-level control flow in `run_turn()` and makes approval collection directly
testable.

### 2. Introduce one approval helper module

Add a helper module responsible for approval-shape normalization:

- decode deferred call args from `str | dict | None`
- format the prompt description
- expose the "remember this" hint for shell calls
- check session-level auto-approval for non-shell tools
- persist approval choices using the correct strategy
- check whether a shell command matches a remembered persistent approval

This does not replace `_shell_policy.py` or `_exec_approvals.py`; it composes them behind a
single interface used by orchestration and the shell tool.

### 3. Make strategy differences explicit

Approval persistence stays asymmetric, but the asymmetry becomes explicit:

- `run_shell_command` => persistent command-pattern approval
- all other deferred tools => session tool-name approval

That strategy choice should live in one helper module, not be scattered across orchestration
branches.

### 4. Keep shell policy layering, improve readability

Do not collapse deny / safe-prefix / persistent-approval into one giant function.
Instead, make the order easy to trace:

1. policy classifies command: `DENY | ALLOW | REQUIRE_APPROVAL`
2. if approval is required, consult remembered shell approvals
3. if not remembered and not already tool-call-approved, defer to orchestration

The design goal is fewer cross-file jumps, not fewer security tiers.

---

## Implementation Plan

### TASK-1: Add centralized approval helper module

**files:** `co_cli/_tool_approvals.py`

**done_when:** The module exists and owns:

- tool-arg decoding
- approval description formatting
- shell "always remember" hint formatting
- session auto-approval check
- remembered-approval persistence dispatch
- remembered shell-command lookup/update

`co_cli/_orchestrate.py` and `co_cli/tools/shell.py` no longer open-code these behaviors.

**prerequisites:** none

---

### TASK-2: Split approval collection from resumption in orchestration

**files:** `co_cli/_orchestrate.py`

**done_when:** `_handle_approvals()` no longer exists as the single combined helper.
Approval collection and resumed streaming are separate helpers, and `run_turn()` clearly shows
the two-step approval loop.

**prerequisites:** TASK-1

---

### TASK-3: Route shell remembered-approval checks through the helper module

**files:** `co_cli/tools/shell.py`

**done_when:** `run_shell_command()` no longer directly calls `load_approvals()`,
`find_approved()`, or `update_last_used()`. It delegates to the centralized approval helper
for remembered-shell-approval checks while preserving current behavior.

**prerequisites:** TASK-1

---

### TASK-4: Update tests to the new approval boundaries

**files:** `tests/test_approval.py`, `tests/test_orchestrate.py`, plus any approval-path tests
that need helper-level coverage

**done_when:** Tests cover:

- session auto-approval behavior
- shell remembered-approval behavior
- approval description / hint formatting where useful
- orchestration still resumes deferred tool requests correctly after approval decisions

`uv run pytest tests/test_approval.py tests/test_orchestrate.py tests/test_shell.py tests/test_exec_approvals.py`
passes.

**prerequisites:** TASK-1, TASK-2, TASK-3

---

### TASK-5: Sync `DESIGN-core-loop.md`

**files:** `docs/DESIGN-core-loop.md`

**done_when:** The design doc no longer describes `_handle_approvals()` as the single approval
step. The approval flow and ordered phases match the refactored orchestration helpers and
explicitly describe the helper-layer split without changing behavior claims.

**prerequisites:** TASK-2

---

## Non-Goals / Rejected Follow-ups

- Do not replace shell pattern approvals with session tool-name approvals
- Do not replace non-shell session approvals with persistent pattern approvals
- Do not merge shell deny policy and safe-prefix allow logic into orchestration
- Do not change the JSON approval store format in this refactor
