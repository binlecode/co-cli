# TODO: TUI Deferred Interactions

**Slug:** `tui-deferred-interactions`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-tui-compare-hermes-and-co.md](reference/RESEARCH-tui-compare-hermes-and-co.md) §4, [RESEARCH-peer-capability-surface.md](reference/RESEARCH-peer-capability-surface.md), [RESEARCH-tools-codex.md](reference/RESEARCH-tools-codex.md) §workflow-control

Current state (validated against code):
- REPL is a linear `PromptSession.prompt_async()` loop in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py), not a custom prompt-toolkit `Application`.
- `Frontend` protocol in [co_cli/display/core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/core.py) exposes `prompt_approval`, `prompt_question`, and `prompt_confirm`. `QuestionPrompt` is defined there.
- `clarify` tool lives at [co_cli/tools/system/user_input.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/system/user_input.py), registered `ALWAYS`. It raises `QuestionRequired` on the unapproved call; the orchestrator injects answers via `ToolApproved(override_args={"user_answers": [...]})`.
- `QuestionRequired(ApprovalRequired)` is defined in [co_cli/tools/approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py). `_collect_deferred_tool_approvals()` in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py) discriminates it by the `"questions"` key in metadata and routes through `frontend.prompt_question()`.
- `HeadlessFrontend` in [co_cli/display/headless.py](/Users/binle/workspace_genai/co-cli/co_cli/display/headless.py) implements the full `Frontend` protocol including `prompt_question` with configurable `question_answer` and recorded `last_question` / `question_call_count`.
- Approval prompting renders a Rich panel via `_build_approval_panel(subject)` — shows `subject.display` and `subject.preview` when present. Session-memory semantics (`can_remember`, `session_approval_rules`) are intact.
- `clarify` multi-question batching: one call takes `questions: list[dict]` and returns `user_answers: list[str]` — richer than the original single-question v1 design.

What remains: regression coverage for the `clarify` deferred resume path and the `prompt_question` frontend contract.

---

## Problem & Outcome

Problem: `co` can pause for mutating-tool approval, but it cannot pause for a structured clarifying question, and its approval UI is still compressed to a single low-context `y/n/a` prompt.

Outcome: extend the current deferred-interaction seam so the same turn can pause for either:
- a structured user-input question with validated options and same-call answer injection
- a richer approval prompt with better preview and clearer operator choices

The outcome preserves `co`'s current architecture:
- no fullscreen TUI rewrite
- no new infra
- no permanent approval database

---

## Scope

In scope:
- ~~add a typed deferred-question interaction path for the frontend and orchestration layer~~ ✓ DONE
- ~~implement `clarify` with same-turn resume via `override_args`~~ ✓ DONE
- ~~upgrade approval prompting to show clearer action context and affordances~~ ✓ DONE
- ~~keep session-scoped remembered approval semantics, but improve the UX around them~~ ✓ DONE
- ~~add focused tests for the frontend contract and deferred-question resume path~~ ✓ DONE

Out of scope:
- plan mode itself
- tool-registry mutability filtering
- converting `co` into a prompt-toolkit `Application`
- `patch_stdout()`, ANSI bridge layers, or other Hermes-specific rendering infrastructure
- multi-question bundles, preview panes, or other UI that current `PromptSession` ownership cannot support cleanly
- cross-session approval persistence
- approval details/view branch — descoped; "deny and ask" covers the use case at lower complexity cost

---

## Implementation Plan

## TASK-1: Add focused regression coverage for deferred interactions

files: `tests/test_flow_tool_call_functional.py`, `tests/test_flow_approval_subject.py`

Coverage must include:
- `clarify` end-to-end: model calls `clarify`, orchestrator routes through `prompt_question`, answer is injected via `override_args`, tool returns the answer as structured output
- `prompt_question` frontend contract: `HeadlessFrontend.question_answer` is returned, `last_question` is recorded, `question_call_count` increments

Already covered (do not duplicate):
- approval subject resolution and session-rule scoping — `test_flow_approval_subject.py`
- auto-approval skips prompt for remembered session rule — `test_flow_tool_call_functional.py`
- denied approval short-circuits tool execution — `test_flow_tool_call_functional.py::test_denied_tool_does_not_execute`

done_when: |
  clarify end-to-end deferred resume path is covered by a real LLM test through the
  full orchestration seam; prompt_question contract is locked at the frontend level
success_signal: `clarify` call → deferred pause → answer injection → structured tool output works end-to-end
prerequisites: []

---

## Testing

```
mkdir -p .pytest-logs && uv run pytest tests/test_flow_tool_call_functional.py tests/test_flow_approval_subject.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tui-deferred-interactions.log
```

Before shipping:

```
scripts/quality-gate.sh types
```

---

## Open Questions

None — all resolved.

- Approval details/view branch: descoped. "Deny and ask in the next turn" covers the use case without adding a loop, a new `ApprovalSubject` field, and uncapped content logic in resolvers.
