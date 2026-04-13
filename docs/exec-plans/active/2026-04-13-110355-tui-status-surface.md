# TODO: TUI Status Surface

**Slug:** `tui-status-surface`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-tui-compare-hermes-and-co.md](reference/RESEARCH-tui-compare-hermes-and-co.md) §1 and §ROI Adoptions for Co

Current-state validation against the latest code:
- The chat loop still hands terminal ownership to `PromptSession.prompt_async()` in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py), so there is no persistent prompt-toolkit status bar today.
- `TerminalFrontend` in [co_cli/display/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/_core.py) owns only transient surfaces:
  - `_status_live`
  - `_tool_live`
  - `_thinking_live`
  - `_live`
- Generic status text is currently pushed through `frontend.on_status(...)` from [co_cli/context/orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py), but it is ephemeral and yields ownership to other live surfaces.
- Existing runtime/session state already contains useful footer inputs in [co_cli/deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py):
  - `deps.runtime.turn_usage`
  - `deps.session.session_id`
  - `deps.session.background_tasks`
  - `deps.session.session_approval_rules`
- The current architecture already buffers display while input is active via `TerminalFrontend._input_active`; it just does not render a stable operator snapshot during the idle prompt phase.

Artifact hygiene:
- No current TODO owns a persistent runtime/footer surface for the chat UI.
- This task is intentionally separate from [TODO-tui-deferred-interactions.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tui-deferred-interactions.md) because it is primarily passive display/state plumbing, not an interaction-flow change.

Recommended order:
- Implement this TODO after [TODO-tui-deferred-interactions.md](/Users/binle/workspace_genai/co-cli/docs/TODO-tui-deferred-interactions.md).
- Reason: the deferred-interactions work is the higher-churn change to frontend contracts and blocking input flow, while the footer/status work is a passive layer that should settle on top of that surface.

---

## Problem & Outcome

Problem: `co` has status messages, but no stable status surface. The operator loses context about runtime state between transient messages and during long-running turns.

Failure cost:
- context pressure is easy to miss until overflow handling is already in flight
- long turns provide weak visibility into whether tools, background work, or approvals are still active
- the prompt phase has no compact snapshot of the current session/runtime state

Outcome: add a compact persistent status/footer surface that keeps high-value runtime context visible without replacing the current `PromptSession` architecture.

The surface should answer practical operator questions such as:
- which session am I in?
- is the model busy or idle?
- is context usage climbing?
- are background tasks active?
- do I have session-scoped approval remembers in effect?

---

## Scope

In scope:
- define a compact status snapshot derived from existing runtime/session state
- render that snapshot consistently in the terminal frontend during prompt-idle and long-turn states
- improve status ownership rules so transient messages and the passive footer do not fight each other
- add focused tests around snapshot assembly and frontend rendering behavior

Out of scope:
- converting the REPL into a fullscreen app
- mouse support, alternate screen, or other prompt-toolkit layout features
- remote liveness probes or new backend health checks
- a large observability dashboard
- per-tool detailed logs beyond the existing transient tool live surface

---

## Behavioral Constraints

- Do not replace `PromptSession` with a custom prompt-toolkit `Application` in this task.
- Do not add network or provider probes just to populate the status surface. The footer must derive from state the runtime already has.
- The footer must stay compact enough for ordinary terminal widths and must degrade gracefully when fields are absent.
- Higher-priority live surfaces still win when they need the line:
  - tool progress
  - streamed output
  - blocking prompts
- The passive footer must restore cleanly after other live surfaces stop.
- Any context-usage indicator must reflect actual runtime values already tracked by the app. Do not invent synthetic percentages without a real source.
- Keep the design implementation-friendly for the current Rich-based frontend. Do not pull in Hermes-style `patch_stdout()` or thread-coordinated widget management.

---

## High-Level Design

### 1. Introduce a compact status snapshot model

The frontend currently receives raw status strings. That is too weak for a persistent footer because footer content needs stable fields and predictable truncation rules.

Add a small typed snapshot assembled from existing state, for example:
- session identifier or short suffix
- mode: idle, thinking, running tool, waiting approval
- compact context-usage hint when available
- background-task count
- remembered-approval count

The exact field list should stay narrow. This is a footer, not a dashboard.

### 2. Separate passive footer state from transient status messages

Right now `on_status(...)` and tool/thinking surfaces compete for the same transient rendering primitives. A persistent footer needs its own ownership model:
- passive footer: always eligible when nothing higher-priority owns the line
- transient status/tool/thinking: temporarily take precedence
- prompt phase: footer returns when prompt control is stable again

This is primarily a frontend-state problem in [co_cli/display/_core.py](/Users/binle/workspace_genai/co-cli/co_cli/display/_core.py), not an orchestrator rewrite.

### 3. Source footer inputs from current runtime/session state

Prefer existing information over new plumbing:
- `turn_usage` for context/runtime usage when available
- `background_tasks` count for async work
- `session_approval_rules` count for in-session remembered permissions
- `session_id` for operator orientation

If one field is unavailable, omit or degrade it. The footer should remain stable without hard dependencies on every signal.

### 4. Keep the first version passive and low-risk

The first shipped version should not try to replicate Hermes's always-on widget bar. The right scope is:
- compact
- text-oriented
- Rich-compatible
- resumable after transient surfaces

That delivers most of the operator value without destabilizing the current REPL loop.

---

## Implementation Plan

## TASK-1: Add a typed status snapshot and assembly helper

files: `co_cli/deps.py`, `co_cli/display/_core.py`, `tests/test_display.py`

Implementation:
- Add a small typed snapshot model or helper-return contract for persistent status fields.
- Keep field ownership explicit and derived from current runtime/session state.
- Add helper logic that converts `CoDeps` or frontend-fed state into compact render-ready pieces.

done_when: |
  the footer content is assembled from a stable typed contract rather than ad hoc status strings;
  the contract can represent empty or degraded fields without special-case rendering everywhere
success_signal: there is one source of truth for passive status/footer content
prerequisites: []

## TASK-2: Teach TerminalFrontend to render and restore a passive footer

files: `co_cli/display/_core.py`, `tests/test_display.py`

Implementation:
- Add dedicated footer state separate from `_status_live`.
- Render the footer when:
  - the frontend is idle
  - the prompt phase is active and the footer can be shown safely
  - no higher-priority live surface owns the line
- Restore the footer after transient status/tool/thinking surfaces end.
- Keep cleanup and SIGINT behavior correct.

done_when: |
  TerminalFrontend can show a passive footer without breaking prompt input or transient live surfaces;
  footer state survives ordinary status churn and restores cleanly
success_signal: runtime context remains visible between transient status events
prerequisites: [TASK-1]

## TASK-3: Feed the footer from current orchestration/runtime state

files: `co_cli/main.py`, `co_cli/context/orchestrate.py`, `co_cli/display/_core.py`, `tests/test_commands.py`, `tests/test_display.py`

Implementation:
- Decide where footer refreshes should happen in the existing loop:
  - after startup/banner
  - before prompt entry
  - after turn completion
  - on meaningful runtime-state changes during long turns
- Reuse current status calls where possible, but do not overload `on_status(...)` with permanent footer responsibilities.
- Surface compact indicators for:
  - active session
  - busy vs idle state
  - background-task presence
  - remembered approval count
  - context-use hint when available

done_when: |
  the footer updates from real runtime/session state through normal turn execution;
  prompt-idle and long-turn states both show useful operator context
success_signal: the footer reflects actual chat/session state instead of static text
prerequisites: [TASK-2]

## TASK-4: Add focused regression coverage for footer ownership and truncation

files: `tests/test_display.py`, `tests/test_commands.py`

Coverage must include:
- footer shows when the frontend is idle
- tool progress takes precedence over the footer, then footer returns
- generic status does not permanently erase the footer
- missing optional fields produce a shorter footer rather than broken output
- long footer content truncates or compacts safely

done_when: |
  test coverage proves the footer coexists with existing live surfaces instead of fighting them;
  compact rendering remains stable under short and long content
success_signal: the passive footer is resilient to the status churn of normal agent turns
prerequisites: [TASK-3]

---

## Testing

During implementation, scope to the affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_display.py tests/test_commands.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tui-status-surface.log`

Before shipping:

- `scripts/quality-gate.sh types`

---

## Open Questions

- Whether the first version should use a prompt bottom-toolbar if prompt-toolkit exposes it cleanly through `PromptSession`, or whether the safer v1 is a Rich-managed passive line that yields during prompt entry. The lower-risk choice is the latter unless prompt-toolkit integration stays local and testable.
