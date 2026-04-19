# Plan: Clarify Tool (request_user_input) Parity

**Task type:** `code-feature`
**Slug:** `request-user-input`

---

## Context

Research:
- `docs/reference/RESEARCH-peer-tool-surface-survey.md` notes that `co-cli` currently relies on the REPL's persistent transcript where the model asks questions at turn boundaries. It specifically points out that Hermes uses a `clarify` tool to pause execution mid-sequence for structured input.
- `docs/exec-plans/active/2026-04-13-110355-tui-deferred-interactions.md` lays out an intention to build a native `request_user_input` tool that mirrors Hermes's `clarify` capabilities using the existing deferred-approval system.

Current state validation:
- The `request_user_input` or `clarify` tool is absent from `co_cli/tools/`.
- The deferred interaction seam exists in `co_cli/context/orchestrate.py` and `co_cli/display/_core.py`, but it's currently hardcoded for `y/n/a` approval prompts.
- `tui-deferred-interactions.md` is an active plan but has not been implemented.

This plan focuses purely on implementing the `request_user_input` tool into the agent loop natively, effectively executing the specific `request_user_input` tasks outlined in the existing TUI plan but narrowing it to just adding the tool to `co-cli`'s ecosystem for immediate parity with `hermes-agent`.

## Problem & Outcome

**Problem:** `co` can pause for mutating-tool approval, but it cannot pause for a structured clarifying question during a sequence of tool calls. The model burns extra turns on ambiguity that could be resolved in-band, or makes assumptions without asking.

**Failure cost:**
- The model wastes turns on ambiguity that could be resolved immediately in-band.
- The user is forced to wait for the model to stop, read a conversational response, and reply, instead of just answering a quick prompt mid-execution.
- Background tasks or subagents (in the future) will lack a way to synchronously request guidance.

**Outcome:** Extend the current deferred-interaction seam so the same turn can pause for a structured user-input question (`request_user_input`) with validated options and same-call answer injection, mirroring the utility of the `clarify` tool in Hermes.

## Scope

**In scope:**
- Add a typed deferred-question interaction path for the frontend and orchestration layer.
- Implement `request_user_input` tool in `co_cli/tools/user_input.py` with same-turn resume via `override_args`.
- Add focused tests for the frontend contract, deferred-question resume path, and tool behavior.

**Out of scope:**
- Richer `y/n/a` approval UX (this remains in the TUI deferred interactions plan).
- Cross-session approval persistence.
- `patch_stdout()`, ANSI bridge layers, or other Hermes-specific rendering infrastructure.

## Behavioral Constraints

- Stay on the current `PromptSession` + Rich frontend architecture in `co_cli/main.py` and `co_cli/display/_core.py`.
- `request_user_input` must validate its arguments before deferring. Do not use agent-layer `requires_approval=True` for that tool.
- `request_user_input` must resume the same pending tool call through `ToolApproved(override_args=...)`; it must not require a second user turn.
- Input-phase terminal ownership must remain clean. Any new prompt flow has to respect `TerminalFrontend._input_active`, flush behavior, and cleanup guarantees in `co_cli/display/_core.py`.
- LLM escape hatch: the tool must detect a model-supplied `user_answer` on the first call (before `QuestionRequired` has fired) and treat it as if `user_answer` were absent — re-raise `QuestionRequired` so the user always sees the question. A self-supplied answer from the model must never bypass the prompt.
- Tool shape:
  - `question: str`
  - `options: list[str] | None`
  - `user_answer: str | None` (used during resume to inject the answer; ignored on first call)

## High-Level Design

### 1. Promote deferred interaction into a typed frontend contract
The frontend currently only knows how to ask for approval. We introduce an explicit interaction model for structured user-input prompts. The contract stays small and synchronous from the orchestrator's point of view.

```python
# In co_cli/display/_core.py
@dataclass(frozen=True)
class QuestionPrompt:
    question: str
    options: list[str] | None = None

class Frontend(Protocol):
    ...
    async def prompt_question(self, prompt: QuestionPrompt) -> str: ...
```

### 2. Implement `request_user_input` as a tool-level deferred interaction
We follow the deferral pattern already used by mutating tools, but with different resume payload semantics:
- The tool validates `question` and `options`.
- If `user_answer` is not provided (first pass), raise a specific `QuestionRequired` exception (defined in `co_cli/context/tool_approvals.py`) that the orchestrator recognizes as a question prompt.
- The orchestrator prompts through the new frontend question path.
- The orchestrator writes `ToolApproved(override_args={"user_answer": ...})`.
- The resumed tool call receives `user_answer` and returns it as structured tool output.

## Implementation Plan

### ✓ DONE — TASK-1: Add typed deferred-interaction models and frontend methods
- **files:** `co_cli/display/_core.py`, `tests/_frontend.py`, `tests/test_display.py`
- **done_when:** The `Frontend` protocol can represent deferred questions; `tests/_frontend.py` can supply deterministic answers for question paths; `TerminalFrontend` implements `prompt_question`. Verified by `uv run pytest tests/test_display.py` (which must be created or updated).
- **success_signal:** The frontend contract natively supports structured question prompting.
- **prerequisites:** []

### ✓ DONE — TASK-2: Implement `request_user_input` and same-call resume injection
- **files:** `co_cli/tools/user_input.py`, `co_cli/agent/_native_toolset.py`, `co_cli/context/orchestrate.py`, `co_cli/context/tool_approvals.py`, `tests/test_user_input.py`, `tests/test_tool_calling_functional.py`
- **done_when:** `request_user_input` exists in the native registry; deferred question prompts resume the same pending tool call instead of requiring a second chat turn. `uv run pytest tests/test_user_input.py` passes, proving the tool raises the `QuestionRequired` exception when `user_answer` is missing, and returns the answer when it is provided. Integration boundary is verified by `uv run pytest tests/test_tool_calling_functional.py`, proving `run_turn()` successfully handles the prompt exception, invokes the frontend, and injects the `user_answer`.
- **success_signal:** `run_turn()` can complete a question-driven tool call end-to-end through the real deferred-tool loop.
- **prerequisites:** [TASK-1]

## Testing

All tests use `tmp_path` and real subprocess invocations where needed. No fakes for domain objects. Tests are async (`pytest-asyncio`). `tests/test_user_input.py` is created by TASK-2.

## Open Questions
- None.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | Added concrete test verification to TASK-1 | Added `uv run pytest tests/test_display.py` to TASK-1 `done_when` |
| CD-M-2   | adopt    | Added integration boundary test requirement to TASK-2 | Added `uv run pytest tests/test_tool_calling_functional.py` to TASK-2 `done_when` |
| CD-m-1   | adopt    | Explicitly defined exception location to prevent hidden coupling | Specified `QuestionRequired` exception will live in `co_cli/context/tool_approvals.py` |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev request-user-input`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/tools/user_input.py` | Correct agent_tool decorator, tool_output/tool_error helpers, no fakes | clean | TASK-2 |
| `co_cli/context/tool_approvals.py` | QuestionRequired subclasses ApprovalRequired correctly, metadata populated | clean | TASK-2 |
| `co_cli/display/_core.py` | QuestionPrompt frozen dataclass, TerminalFrontend.prompt_question with SIGINT swap | clean | TASK-1 |
| `co_cli/context/orchestrate.py` | Question-path routing via metadata._kind, ToolApproved override_args, frontend null-check | clean | TASK-2 |
| `co_cli/agent/_native_toolset.py` | request_user_input registered first with correct decorator | clean | TASK-2 |
| `tests/_frontend.py` | Missing clear_status() and set_input_active() no-op stubs (pre-existing gap) | blocking (fixed) | TASK-1 |
| `tests/test_user_input.py` | 8 unit tests covering all paths; no mocks; real CoDeps | clean | TASK-2 |
| `tests/test_display.py` | 3 behavioral tests for SilentFrontend.prompt_question | clean | TASK-1 |
| `tests/test_tool_calling_functional.py` | Full integration path: SilentFrontend → run_turn → LLM → deferred → injected answer | clean | TASK-2 |

**Overall: 1 blocking (fixed — added clear_status/set_input_active no-op stubs to SilentFrontend)**

## Delivery Summary — 2026-04-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_display.py` passes | ✓ pass |
| TASK-2 | `uv run pytest tests/test_user_input.py` + `tests/test_tool_calling_functional.py` pass | ✓ pass |

**Tests:** full suite — 646 passed, 0 failed
**Independent Review:** 1 blocking (fixed inline — `SilentFrontend` missing `clear_status`/`set_input_active` no-ops, pre-existing gap)
**Doc Sync:** fixed (`tools.md` — new tool entry + count update; `core-loop.md` — question-path step in 2.3, file descriptions updated)

**Overall: DELIVERED**
`request_user_input` tool added with same-turn resume via `QuestionRequired`→`ToolApproved(override_args=...)` path; `Frontend` protocol extended with `QuestionPrompt` + `prompt_question`; orchestrator routes `_kind=="question"` metadata to the new frontend method.

## Implementation Review — 2026-04-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `uv run pytest tests/test_display.py` passes | ✓ pass | `_core.py:173` — `QuestionPrompt` frozen dataclass; `_core.py:229` — `Frontend.prompt_question` protocol method; `_core.py:497` — `TerminalFrontend.prompt_question` with SIGINT swap and `_clear_status_live()`; `_frontend.py:64,71,74` — `SilentFrontend.prompt_question`, `clear_status`, `set_input_active` stubs |
| TASK-2 | `uv run pytest tests/test_user_input.py` + `tests/test_tool_calling_functional.py` pass | ✓ pass | `tool_approvals.py:21` — `QuestionRequired(ApprovalRequired)` with `_kind=question` metadata; `user_input.py:32` — raises on `not ctx.tool_call_approved` (covers LLM escape-hatch); `_native_toolset.py:49` — registered first; `orchestrate.py:201-209` — `_kind==question` routes to `frontend.prompt_question` → `ToolApproved(override_args={"user_answer": ...})` |

Call path verified: `run_turn()` → `_execute_stream_segment()` → tool raises `QuestionRequired` → pydantic-ai defers with metadata → `_run_approval_loop()` → `_collect_deferred_tool_approvals()` checks `_kind=="question"` → `frontend.prompt_question()` → `ToolApproved(override_args={"user_answer": answer})` → resume segment → `ctx.tool_call_approved=True` → `tool_output(user_answer)`.

### Issues Found & Fixed

No issues found.

### Tests
- Unit: `uv run pytest tests/test_display.py tests/test_user_input.py -v` — 18 passed, 0 failed
- Full (non-LLM): `uv run pytest -m "not local"` — 627 passed, 0 failed
- Log: `.pytest-logs/` (timestamped)

### Doc Sync
- Scope: verified (already fixed in delivery) — `tools.md:101,249` has `request_user_input` entry; `core-loop.md:179-181,370,373` has question-path and file table entries
- Result: clean

### Behavioral Verification
- `uv run co config`: ✓ healthy — LLM online, all components nominal
- `success_signal` TASK-1: `Frontend` protocol natively carries `prompt_question`; `TerminalFrontend` implements it — verified at `_core.py:229,497`
- `success_signal` TASK-2: `run_turn()` deferred-question loop confirmed via `test_request_user_input_handled_by_run_turn` (integration test) and orchestrator code trace

### Overall: PASS
All spec requirements implemented with evidence, no issues found, 627 tests green, doc sync confirmed, system healthy.