# TODO: TUI Deferred Interactions

**Slug:** `tui-deferred-interactions`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-tui-compare-hermes-and-co.md](reference/RESEARCH-tui-compare-hermes-and-co.md) §4, [RESEARCH-peer-capability-surface.md](reference/RESEARCH-peer-capability-surface.md), [RESEARCH-tools-codex.md](reference/RESEARCH-tools-codex.md) §workflow-control

Current-state validation against the latest code:
- The REPL is still a linear `PromptSession.prompt_async()` loop in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py), not a custom prompt-toolkit `Application`.
- The frontend contract in [co_cli/display/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/_core.py) only exposes one blocking interaction primitive today: `prompt_approval(description) -> "y" | "n" | "a"`.
- Deferred tool pauses already exist in [_collect_deferred_tool_approvals() in co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py); resume state is already narrowed through `deps.runtime.resume_tool_names`.
- Approval prompts already have a structured subject model in [co_cli/tools/approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py): `ApprovalSubject` resolves scope, display text, and session-rememberability.
- Session approval persistence is already intentionally limited to `deps.session.session_approval_rules` in [co_cli/deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py); there is no cross-session approval store today.
- No current native tool provides structured deferred clarification. `request_user_input` is still absent from the active tool surface.

Workflow artifact hygiene:
- [TODO-tier1-workflow-tools.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tier1-workflow-tools.md) already overlaps this surface for `request_user_input`.
- This TODO is the TUI/operator-interaction slice: deferred clarification UX plus richer approval UX.
- If this TODO is adopted, `TODO-tier1-workflow-tools.md` should retain plan-mode ownership and stop duplicating the frontend/orchestration details captured here.

Recommended order:
- Implement this TODO before [TODO-tui-status-surface.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tui-status-surface.md).
- Reason: this work changes the frontend interaction contract and blocking prompt behavior that the passive footer/status work should layer on top of, not precede.

---

## Problem & Outcome

Problem: `co` can pause for mutating-tool approval, but it cannot pause for a structured clarifying question, and its approval UI is still compressed to a single low-context `y/n/a` prompt.

Failure cost:
- the model burns extra turns on ambiguity that could be resolved in-band
- high-impact approvals expose too little context at the decision point
- the frontend/orchestration seam already exists, but only one interaction type can use it

Outcome: extend the current deferred-interaction seam so the same turn can pause for either:
- a structured user-input question with validated options and same-call answer injection
- a richer approval prompt with better preview and clearer operator choices

The outcome should preserve `co`'s current architecture:
- no fullscreen TUI rewrite
- no new infra
- no permanent approval database

---

## Scope

In scope:
- add a typed deferred-question interaction path for the frontend and orchestration layer
- implement `request_user_input` with same-turn resume via `override_args`
- upgrade approval prompting to show clearer action context and affordances
- keep session-scoped remembered approval semantics, but improve the UX around them
- add focused tests for the frontend contract, deferred-question resume path, and approval behavior

Out of scope:
- plan mode itself
- tool-registry mutability filtering beyond what is needed to keep this interaction surface compatible with later plan-mode work
- converting `co` into a prompt-toolkit `Application`
- `patch_stdout()`, ANSI bridge layers, or other Hermes-specific rendering infrastructure
- multi-question bundles, preview panes, or other UI that current `PromptSession` ownership cannot support cleanly
- cross-session approval persistence

---

## Behavioral Constraints

- Stay on the current `PromptSession` + Rich frontend architecture in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py) and [co_cli/display/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/_core.py).
- `request_user_input` must validate its arguments before deferring. Do not use agent-layer `requires_approval=True` for that tool.
- `request_user_input` must resume the same pending tool call through `ToolApproved(override_args=...)`; it must not require a second user turn.
- Approval memory remains session-scoped only. Do not add a project or user-global allowlist in this task.
- Richer approval UX may add a details/view path, but the underlying choice model must remain compatible with current semantics:
  - `y` = once
  - `a` = remember for this session when the subject is rememberable
  - `n` = deny
- Approval detail rendering must reuse the canonical approval-subject data in [co_cli/tools/approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py); do not fork parallel description logic inside the frontend.
- Input-phase terminal ownership must remain clean. Any new prompt flow has to respect `TerminalFrontend._input_active`, flush behavior, and cleanup guarantees in [co_cli/display/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/_core.py).
- Keep the first version narrow:
  - one question per `request_user_input` call
  - concise mutually exclusive options
  - optional free-text answer when no options are provided

---

## High-Level Design

### 1. Promote deferred interaction into a typed frontend contract

Today the frontend only knows how to ask for approval. This task should introduce an explicit interaction model for:
- approval prompts
- structured user-input prompts

That contract should stay small and synchronous from the orchestrator's point of view. The orchestrator owns when the turn pauses; the frontend owns how the user is asked.

### 2. Implement `request_user_input` as a tool-level deferred interaction

Follow the same deferral pattern already used by mutating tools, but with different resume payload semantics:
- tool validates `question` and `options`
- first pass raises `ApprovalRequired(metadata=...)`
- orchestrator recognizes this as a question prompt rather than a mutation approval
- frontend collects the answer
- orchestrator writes `ToolApproved(override_args={"user_answer": ...})`
- resumed tool call returns the chosen answer as structured tool output

This is the highest-ROI piece because the pause/resume seam already exists in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py).

### 3. Upgrade approval UX without changing approval policy

The approval system already has most of the hard policy pieces:
- subject scoping
- session rememberability
- resume narrowing

What it lacks is operator-facing clarity. The improved prompt should surface:
- the action summary
- the scoped "remember this session" meaning when available
- an explicit way to inspect details before deciding, if the current summary is truncated or dense

This should remain a better blocking prompt, not a modal widget system.

### 4. Keep the architecture intentionally simpler than Hermes

Adopt the high-value behavior, not the low-value infrastructure:
- yes to structured clarify flows
- yes to better approvals
- no to `patch_stdout()`
- no to background-thread UI coordination
- no to a full prompt-toolkit layout tree

---

## Implementation Plan

## TASK-1: Add typed deferred-interaction models and frontend methods

files: `co_cli/display/_core.py`, `tests/_frontend.py`, `tests/test_display.py`

Implementation:
- Extend the `Frontend` protocol with explicit question-prompt and richer approval-return methods.
- Add a small typed result model for approval decisions and a separate typed question-prompt payload/result.
- Update `TerminalFrontend` to implement:
  - structured question prompting
  - richer approval prompting with a details path
- Update `tests/_frontend.py` so orchestration tests can drive both approval and question responses without mocks.

done_when: |
  the Frontend contract can represent both deferred questions and richer approval choices;
  tests/_frontend.py can supply deterministic answers for both paths
success_signal: the orchestration layer no longer has to treat every deferred interaction as a plain y/n/a approval prompt
prerequisites: []

## TASK-2: Implement `request_user_input` and same-call resume injection

files: `co_cli/tools/user_input.py`, `co_cli/agent.py`, `co_cli/context/orchestrate.py`, `tests/test_tool_registry.py`, `tests/test_tool_calling_functional.py`

Implementation:
- Add a native `request_user_input` tool with the narrow v1 shape:
  - `question: str`
  - `options: list[str] | None`
  - `user_answer: str | None`
- Validate before deferring:
  - non-empty question
  - either free text or a short mutually exclusive option list
  - if `user_answer` is present for an option list, it must match an allowed option
- Register the tool as always-visible.
- In `_collect_deferred_tool_approvals()`, special-case `request_user_input` so the orchestrator:
  - prompts through the new frontend question path
  - injects the answer through `override_args`
  - keeps the rest of the deferred approval loop unchanged

done_when: |
  request_user_input exists in the native registry;
  deferred question prompts resume the same pending tool call instead of requiring a second chat turn
success_signal: run_turn() can complete a question-driven tool call end-to-end through the real deferred-tool loop
prerequisites: [TASK-1]

## TASK-3: Upgrade approval prompting to preview-first UX

files: `co_cli/display/_core.py`, `co_cli/tools/approvals.py`, `tests/test_approvals.py`, `tests/test_display.py`, `tests/test_commands.py`

Implementation:
- Keep `ApprovalSubject` as the single source of approval description truth.
- Improve approval rendering so dense operations are easier to inspect before choosing.
- Add a details/view branch that lets the user inspect the full operation context, then return to the decision.
- Preserve current session-memory semantics:
  - only offer session remember when `subject.can_remember` is true
  - no new permanent approval state
- Keep the prompt short and action-specific at the top level.

done_when: |
  approval prompts expose clearer context than a single opaque y/n/a line;
  subject.can_remember still governs whether session memory is offered
success_signal: the approval UX improves without changing the underlying approval policy model
prerequisites: [TASK-1]

## TASK-4: Add focused regression coverage for deferred interactions

files: `tests/test_display.py`, `tests/test_approvals.py`, `tests/test_commands.py`, `tests/test_tool_calling_functional.py`

Coverage must include:
- structured question prompt with free-text answer
- structured question prompt with choice validation
- same-turn resume through `override_args`
- approval detail/view flow
- remembered session approvals still auto-approve the same scoped subject
- denied approvals still short-circuit the deferred tool request cleanly

done_when: |
  affected test files cover both question and approval interaction paths through real frontend/orchestration seams;
  no existing approval behavior regresses
success_signal: deferred interactions are locked by tests at the frontend, approval-subject, and end-to-end turn levels
prerequisites: [TASK-2, TASK-3]

---

## Testing

During implementation, scope to the affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_display.py tests/test_approvals.py tests/test_commands.py tests/test_tool_calling_functional.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tui-deferred-interactions.log`

Before shipping:

- `scripts/quality-gate.sh types`

If this TODO lands alongside plan-mode work from [TODO-tier1-workflow-tools.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tier1-workflow-tools.md), rerun the focused workflow-tool tests after integration to catch ownership drift between the two planning slices.

---

## Open Questions

- Whether the approval details path should reuse the same blocking prompt loop or temporarily render a richer Rich panel before returning to the choice prompt. The first option is lower risk and better aligned with the current frontend architecture.
