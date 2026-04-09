# TODO: Tier 1 Workflow Tools

**Slug:** `tier1-workflow-tools`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-peer-capability-surface.md](reference/RESEARCH-peer-capability-surface.md) §1, [RESEARCH-fork-claude-code-core-tools.md](reference/RESEARCH-fork-claude-code-core-tools.md) §3.1, [RESEARCH-tools-fork-cc.md](reference/RESEARCH-tools-fork-cc.md), [RESEARCH-tools-codex.md](reference/RESEARCH-tools-codex.md), [RESEARCH-tools-gemini-cli.md](reference/RESEARCH-tools-gemini-cli.md), [RESEARCH-tools-opencode.md](reference/RESEARCH-tools-opencode.md)

Current-state validation against the latest code:
- `co` still has no structured user-input tool and no plan-mode enter/exit tools in [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).
- The current approval loop in [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py) already supports deferred approval resume, which is the right seam for structured user input.
- The installed SDK supports `ToolApproved(override_args=...)` in [.venv/lib/python3.12/site-packages/pydantic_ai/tools.py](/Users/binle/workspace_genai/co-cli/.venv/lib/python3.12/site-packages/pydantic_ai/tools.py), so answer injection should use that path directly.
- The current registry metadata in [co_cli/deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py) does not model read-onlyness. Any plan-mode design that equates `approval=False` with read-only is incorrect because non-read-only tools such as `run_shell_command` are still registered with `approval=False`.
- Section 3.1 of the fork parity research is still tier-1 relevant for `AskUserQuestion` and `EnterPlanMode` / `ExitPlanMode`.
- Direct fork-cc source review adds prompt-level constraints missing from the earlier split TODOs:
  - `AskUserQuestion` is for clarifying requirements and choosing among approaches during execution, not for plan approval.
  - In plan mode, fork-cc explicitly forbids asking "is the plan okay?" through the question tool and routes that decision through `ExitPlanMode`.
  - `EnterPlanMode` has explicit "when to use" and "when not to use" prompt guidance, rather than a bare state switch.
  - `ExitPlanMode` is explicitly framed as "present the finished plan for approval and start coding", not just "leave plan mode".
- Section 3.1 is stale on `Glob`: `co` already has pathname-pattern search through `list_directory(path, pattern=..., max_entries=...)`, including recursive `**` traversal, so `Glob` is not a remaining tier-1 gap.

Workflow artifact hygiene:
- The former split TODOs for structured user input and plan mode overlapped the same workflow-control surface and are replaced by this single TODO.

Shipped-work check:
- No current file in the planned surface already implements `request_user_input`, `enter_plan_mode`, or `exit_plan_mode`.
- No current registry field can safely drive plan-mode mutability filtering.

---

## Problem & Outcome

Problem: `co` lacks explicit workflow-control tools for two common agent behaviors that peers now converge on: pausing to ask the user a constrained question, and entering an enforced read-only planning state before mutating work begins.

Failure cost: the model can jump from exploration into mutation as soon as it discovers write tools, and it can only ask clarifying questions as free-text assistant output that the orchestrator cannot distinguish from ordinary narration or resume from as structured input.

Outcome: `co` should have one tier-1 workflow-control bundle with:
- an always-available `request_user_input` tool that pauses the turn, collects a validated answer, and resumes the same tool call with `override_args`
- always-available `enter_plan_mode` / `exit_plan_mode` tools that enforce a real plan-only state
- explicit registry metadata for plan-mode eligibility so the filter is based on mutability semantics, not approval semantics
- prompt text and tool descriptions that teach the model the intended workflow:
  - `request_user_input` is for clarification and constrained choices
  - `enter_plan_mode` is for high-impact work where planning should precede action
  - `exit_plan_mode` is the approval boundary for the plan itself

---

## Scope

In scope:
- add `request_user_input`
- add `enter_plan_mode` and `exit_plan_mode`
- add explicit tool metadata for plan-mode eligibility
- extend orchestration and frontend interaction to capture structured answers through deferred approval resume
- inject plan-mode runtime guidance and filter the tool surface correctly while plan mode is active

Out of scope:
- generic task CRUD
- generic resumable agent orchestration
- MCP resource helper tools
- notebook editing
- a new dedicated `Glob` tool

---

## Behavioral Constraints

- Plan mode must be enforced from explicit tool metadata, not from `ToolInfo.approval`.
- `request_user_input` must resume the same tool call through `ToolApproved(override_args=...)`; it must not rely on a second user turn.
- `request_user_input` must validate its question and options before deferring. Do not register it with agent-layer `requires_approval=True`, because that would defer before the tool can reject bad arguments.
- `request_user_input` prompt guidance must explicitly forbid using it for "is the plan okay?" or "should I proceed?" approvals while in plan mode. That approval belongs to `exit_plan_mode`.
- Existing deferred-tool discovery semantics stay intact. Plan mode narrows which tools are eligible; it does not auto-discover deferred read-only tools.
- `exit_plan_mode` must stay callable while plan mode is active even though it mutates session state.
- Existing shell approval behavior and `resume_tool_names` narrowing must remain correct outside this feature.
- Do not cargo-cult fork-cc prompt text that depends on fork-only product mechanics:
  - no plan file requirement in `co`
  - no interview-phase attachment system
  - no preview panes or HTML preview schema in v1
  - no 1-4 multi-question bundle in v1 unless `co` later grows UI support for it

---

## Prompt Design Principles

Apply these fork-cc prompt-design patterns, but tune them to `co`:

1. **Decision boundaries must be explicit**
   - `request_user_input` clarifies requirements, preferences, and approach choices.
   - `enter_plan_mode` starts a read-only planning phase.
   - `exit_plan_mode` is the approval boundary for the completed plan.
   - The prompts must teach these boundaries directly so the model does not blur them.

2. **Use concrete "when to use / when not to use" guidance**
   - fork-cc does this in `EnterPlanMode`.
   - `co` should keep the same pattern, but bias slightly more conservative than fork-cc external mode so normal straightforward tasks do not over-trigger plan mode.
   - The positive cases should include not only non-trivial implementation work, but also other high-impact or hard-to-undo transactional tasks.

3. **State the user-value of the workflow, not just the mechanism**
   - fork-cc explains that plan mode prevents wasted effort and gets sign-off before coding.
   - `co` should keep that value framing, but broaden it to "reduce rework and avoid premature side effects on consequential tasks" so the guidance fits a general assistant rather than only a coding assistant.

4. **Repeat the read-only constraint at both entry and active-mode layers**
   - fork-cc reinforces read-only behavior in both the tool prompt and the tool result.
   - `co` should do the same through:
     - `enter_plan_mode` tool guidance
     - the runtime plan-mode instruction
     - the tool result returned when plan mode begins

5. **Keep the first version narrow where the UI is narrow**
   - fork-cc supports multi-question flows, previews, annotations, and plan-file review.
   - `co` should not pretend to support those in prompt text until the UI and orchestration actually do.

6. **Recommended choices should be easy for the model to express**
   - keep fork-cc’s best practice that a recommended option goes first and is labeled `"(Recommended)"`.

7. **Approval prompts shown to the user should be short and action-specific**
   - fork-cc uses focused prompts such as `"Answer questions?"`, `"Enter plan mode?"`, and `"Exit plan mode?"`.
   - `co` should follow the same pattern instead of verbose approval copy in the blocking prompt itself.

---

## Failure Modes

- Current prompt shape: "Inspect the repo, make a plan, then patch the bug." Current behavior: the model can discover `edit_file` / `write_file` and mutate immediately because there is no enforced planning state.
- Current prompt shape: "Ask me which migration strategy to use before you continue." Current behavior: the model can only ask in plain assistant text; the orchestrator cannot pause, validate choices, or inject the answer back into the pending tool call.
- Naive plan-mode implementation failure: filtering by `approval=False` would still expose non-read-only tools such as `run_shell_command`, which silently breaks the safety claim of plan mode.
- Naive user-input implementation failure: registering the tool with agent-layer approval would skip tool-body validation and prompt the user even for malformed empty questions or invalid option lists.

---

## High-Level Design

1. Extend `ToolInfo` with explicit plan-mode eligibility metadata.
   The minimal shape is a boolean field such as `read_only` or `plan_mode_allowed`, populated centrally in `_reg(...)` in [co_cli/agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py). The feature only works if the registry can distinguish:
   - read-only tools allowed during plan mode
   - mutating tools hidden during plan mode
   - explicit workflow exceptions such as `exit_plan_mode`

2. Implement `request_user_input` as a tool-level deferred interaction, not an agent-layer approval tool.
   The tool should:
   - validate `question` and `options`
   - if `user_answer is None`, raise `ApprovalRequired(metadata=...)`
   - if `user_answer` is present, return it as `ToolReturn`

   Then `_collect_deferred_tool_approvals()` special-cases this tool:
   - render a dedicated question prompt through the frontend
   - collect the answer
   - store `ToolApproved(override_args={... "user_answer": answer})`

   Prompt-engineering requirements borrowed from fork-cc:
   - description should teach the model to use the tool for ambiguity, preferences, and implementation choices
   - guidance should say that if one option is preferred, it should be listed first and labeled `"(Recommended)"`
   - guidance should explicitly say not to use this tool for plan approval while in plan mode; `exit_plan_mode` is the approval boundary
   - keep `co` v1 simpler than fork-cc by starting with one question per call rather than 1-4 questions, but preserve the same intent guidance

3. Add session-scoped plan-mode state and filter the visible tool surface by two axes:
   - existing visibility axis: always vs deferred/discovered
   - new plan-mode axis: allowed vs blocked while planning

4. Add explicit prompt guidance for `enter_plan_mode` and `exit_plan_mode`, not just a state flag.
   - `enter_plan_mode` should tell the model when to prefer planning and when to skip it, across both implementation tasks and other high-impact execution tasks
   - `exit_plan_mode` should tell the model it is the mechanism for presenting the finished plan for approval before side effects begin
   - the runtime plan-mode instruction should reinforce that this is a read-only planning phase until `exit_plan_mode` succeeds
   - `co` should intentionally diverge from fork-cc’s plan-file wording: the approval boundary is still `exit_plan_mode`, but the plan artifact can be chat-visible or tool-result-visible rather than file-backed

---

## Implementation Plan

## TASK-1: Add explicit plan-mode eligibility metadata to the tool registry

files: `co_cli/deps.py`, `co_cli/agent.py`, `tests/test_agent.py`

Guard condition parity:
- This task introduces registry metadata only. It must not change the current approval meaning of `ToolInfo.approval`.
- Do not infer the new field from `approval`; populate it explicitly per tool at registration.

Implementation:
- Add a dedicated `ToolInfo` field for plan-mode eligibility.
- Extend `_reg(...)` in `co_cli/agent.py` to require the new flag explicitly for native tools.
- Mark current native tools conservatively:
  - read-only/search/display tools allowed in plan mode
  - mutating tools blocked in plan mode
  - `exit_plan_mode` reserved as an explicit exception once added
- Keep the field available in `tool_index` so tests and later filter logic can rely on one source of truth.

done_when:
- `ToolInfo` carries an explicit plan-mode eligibility field
- `_reg(...)` in `co_cli/agent.py` populates that field for every native tool
- `uv run pytest tests/test_agent.py` includes assertions proving `run_shell_command` is not treated as plan-mode-safe merely because `approval=False`

success_signal: the registry can distinguish plan-mode-safe tools from merely auto-approved tools.

---

## TASK-2: Add `request_user_input` with same-turn deferred answer injection

files: `co_cli/tools/user_input.py`, `co_cli/context/orchestrate.py`, `co_cli/display/_core.py`, `co_cli/agent.py`, `tests/test_tools_user_input.py`

Guard condition parity:
- Mirror the existing `run_shell_command` pattern in [co_cli/tools/shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py): validate inside the tool, then raise `ApprovalRequired` only when deferral is actually needed.
- Intentional divergence from write/edit tools: do not register `request_user_input` with agent-layer `requires_approval=True`, because malformed arguments must be rejected before any user prompt appears.

Implementation:
- Add `request_user_input(ctx, question: str, options: list[str] | None = None, user_answer: str | None = None) -> ToolReturn`.
- Validation:
  - empty `question` -> `ModelRetry`
  - if `options` is provided, require 2-4 choices
  - if `user_answer` is provided for a choice question, require it to match one of the options
- Behavior:
  - `user_answer is None` -> raise `ApprovalRequired(metadata={question, options})`
  - `user_answer is not None` -> return `tool_output(user_answer, ctx=ctx, question=question, options=options)`
- Register the tool as always-loaded and plan-mode-safe.
- Give the tool a description/search hint and prompt guidance that teach:
  - use for clarifying requirements, preferences, and implementation choices
  - keep options concise and mutually exclusive
  - put the recommended option first and mark it `"(Recommended)"`
  - do not use this tool for plan approval; use `exit_plan_mode` instead
  - do not mention unsupported fork-cc features such as multi-question bundles, previews, annotations, or automatic "Other" UI unless `co` actually implements them
- Approval UX text shown to the user should stay short and action-specific, matching the fork-cc pattern:
  - permission prompt equivalent of `"Answer questions?"`
  - question renderer copy focused on the question itself, not meta-explaining the whole workflow
- In `_collect_deferred_tool_approvals()`, special-case `request_user_input` before normal approval handling:
  - prompt through a dedicated frontend method
  - return `ToolApproved(override_args={... "user_answer": answer})`
  - do not record session approval rules for this path
- Extend `TerminalFrontend` with a dedicated structured question renderer instead of reusing `prompt_approval()`.

done_when:
- `uv run pytest tests/test_tools_user_input.py` passes and covers:
  - empty question -> `ModelRetry`
  - invalid option count -> `ModelRetry`
  - free-text answer path returns `ToolReturn`
  - choice answer path returns `ToolReturn`
  - orchestration writes `ToolApproved(override_args=...)` for `request_user_input`
- `build_tool_registry(...).tool_index` contains `request_user_input` as an always-loaded tool
- the tool description/prompt text explicitly teaches the model not to use `request_user_input` for plan approval

success_signal: the model can pause mid-turn, ask a constrained question, and continue with the chosen answer without requiring a second user message.

---

## TASK-3: Add frontends and test/eval stubs for structured user input

files: `tests/_frontend.py`, `evals/_frontend.py`, `tests/test_commands.py`

Guard condition parity:
- Keep the test/eval frontends behaviorally aligned with the production `Frontend` contract.
- Do not reuse `prompt_approval()` for question-answer capture in stubs; structured user input is a separate interaction path from approval.

Implementation:
- Add the structured question method introduced in TASK-2 to the shared test/eval frontends.
- Make stub implementations deterministic and easy to override per test case.
- Add one orchestration-level regression in `tests/test_commands.py` or equivalent existing approval-loop coverage so the special-case path is exercised through `run_turn()`.

done_when:
- `tests/_frontend.py` and `evals/_frontend.py` implement the new structured question method
- scoped regression coverage exercises the `request_user_input` resume path through the real approval loop

success_signal: tests and eval harnesses can exercise the same structured question path as the terminal frontend.

prerequisites: [TASK-2]

---

## TASK-4: Add enforced plan mode with enter/exit tools and runtime prompt guidance

files: `co_cli/tools/plan_mode.py`, `co_cli/deps.py`, `co_cli/agent.py`, `co_cli/context/orchestrate.py`, `tests/test_tools_plan_mode.py`

Guard condition parity:
- Keep the idempotence guards from the earlier split design:
  - enter while already active -> `ModelRetry`
  - exit while inactive -> `ModelRetry`
- Intentional divergence from the old TODO: plan mode must filter on explicit plan-mode metadata from TASK-1, not on `approval=False`.

Implementation:
- Add `plan_mode: bool = False` to `CoSessionState`.
- Add:
  - `enter_plan_mode(ctx, reason: str) -> ToolReturn`
  - `exit_plan_mode(ctx, summary: str) -> ToolReturn`
- Register both as always-loaded tools.
- Prompt-engineering requirements:
  - `enter_plan_mode` description and search hint should frame it as "switch to planning before acting" for non-trivial implementation tasks and other high-impact transactional work
  - `enter_plan_mode` prompt guidance should include "when to use" and "when not to use" rules so the model does not over-enter plan mode for trivial work, casual Q&A, or low-risk read-only tasks
  - `enter_plan_mode` should explain the user value of planning first: reduce rework, get alignment before irreversible or costly actions, and explore before committing to an approach
  - `enter_plan_mode` should explicitly say that once inside plan mode, the model should clarify unresolved approach questions with `request_user_input` before finalizing the plan
  - `exit_plan_mode` description and search hint should frame it as "present plan for approval and begin execution"
  - `exit_plan_mode` guidance should explicitly say it is the approval boundary for the plan and that unresolved requirement questions should be asked earlier through `request_user_input`
  - `exit_plan_mode` should be described as valid for completed execution plans, not pure research/exploration
  - `exit_plan_mode` should not rely on fork-cc’s plan-file language; in `co`, it should refer to "your completed plan" rather than "the plan file"
- User-facing approval copy should mirror fork-cc’s focused pattern:
  - enter prompt equivalent of `"Enter plan mode?"`
  - exit prompt equivalent of `"Exit plan mode and begin execution?"` or `"Present plan for approval and begin execution?"`
- Update the tool filter in `co_cli/agent.py` so plan mode applies on top of the current visibility rules:
  - normal turn: existing always/deferred visibility still applies
  - if `session.plan_mode` is active: block tools not marked plan-mode-safe
  - keep `exit_plan_mode` callable regardless of the generic flag
  - preserve `resume_tool_names` behavior for approval resumes
- Add a short runtime instruction when `session.plan_mode` is active:
  - "You are in plan mode. This is a read-only exploration and planning phase. Clarify unresolved requirements with request_user_input before finalizing your approach. When your plan is complete, use exit_plan_mode to present it for approval. Do not write or edit files yet."
- Add tool-result instruction content when entering plan mode that reinforces:
  - explore the codebase thoroughly
  - identify patterns and relevant files
  - consider trade-offs
  - do not write or edit files yet
  - use `exit_plan_mode` only when the plan is complete

done_when:
- `uv run pytest tests/test_tools_plan_mode.py` passes and covers:
  - enter sets `session.plan_mode = True`
  - exit sets `session.plan_mode = False`
  - double-enter -> `ModelRetry`
  - exit when inactive -> `ModelRetry`
  - plan mode hides mutating tools even when they would otherwise be visible
  - `exit_plan_mode` remains callable while plan mode is active
- `build_tool_registry(...).tool_index` contains both plan-mode tools
- the plan-mode prompts and tool-result guidance distinguish:
  - clarification via `request_user_input`
  - planning via `enter_plan_mode`
  - approval/start-coding via `exit_plan_mode`

success_signal: the model can deliberately switch into a planning-only state and cannot reach mutating tools until it exits that state.

prerequisites: [TASK-1]

---

## Testing

All pytest commands below follow the repository policy: pipe full output to a timestamped file under `.pytest-logs/`.

- Scope during implementation:
  - `uv run pytest tests/test_agent.py`
  - `uv run pytest tests/test_tools_user_input.py`
  - `uv run pytest tests/test_tools_plan_mode.py`
  - one orchestration-level approval-loop regression covering `ToolApproved(override_args=...)`
- Full suite before shipping:
  - `uv run pytest`

Required behavioral checks:
- `request_user_input` validates before prompting
- the same-turn resume path injects `user_answer` through SDK override args
- plan mode blocks mutating tools by explicit metadata
- plan mode does not rely on approval semantics or tool discovery side effects
- tool descriptions and runtime guidance teach the model the intended workflow split:
  - clarify via `request_user_input`
  - plan via `enter_plan_mode`
  - approve/start coding via `exit_plan_mode`
- prompt text does not promise fork-cc-only capabilities that `co` does not ship in v1

---

## Open Questions

- None at planning time. The main design unknown from the split TODOs is now resolved: plan mode requires explicit registry metadata rather than approval-based inference.
