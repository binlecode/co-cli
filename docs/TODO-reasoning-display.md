# TODO — Reasoning Display Best Practice

## Goal

Adopt the converged CLI pattern for reasoning/progress display in Co:
- persistent config sets the default behavior
- CLI flags override config for the current session
- reasoning display uses modes, not a boolean only

## Recommended Product Decision

Co should adopt:

```text
reasoning_display = "off" | "summary" | "full"
```

Behavior:
- `off`: no reasoning/thinking content is shown; only status/progress and final output
- `summary`: stream compact reasoning progress suitable for default interactive use
- `full`: stream full model thinking/reasoning content, equivalent to current `--verbose` intent

Compatibility:
- keep `co chat --verbose` as an alias for `co chat --reasoning-display full`
- add a persistent config path so users do not need to pass `--verbose` every session

## Why This Is The Best Practice

This matches the common pattern in modern agentic CLIs:
- users expect a saved default for display verbosity
- one-off CLI flags should override the saved default
- reasoning display usually needs more than on/off because default UX and debugging UX are different

Canonical rationale:
- `docs/RESEARCH-progressive-output-best-practice.md`
- `docs/reference/RESEARCH-peer-systems.md`

Why Co should not stop at `verbose = true|false`:
- boolean config is acceptable as a short-term bridge but is too coarse
- the product needs a safe default interactive mode and a debugging mode
- `/doctor` and future long-running tools also need progressive display that does not imply full raw reasoning

Important distinction:
- the config + override + multi-mode model is converged best practice
- the exact behavior of `summary` mode is not converged across reference systems
- `summary` below is Co's proposed v1 product policy, not an ecosystem-standard reasoning format

## Proposed Co Design

### 1. Config

Add to `Settings`:

```text
reasoning_display: Literal["off", "summary", "full"] = "summary"
```

Config precedence remains:
- env vars
- project `.co-cli/settings.json`
- user `~/.config/co-cli/settings.json`
- built-in default

Suggested env var:

```text
CO_CLI_REASONING_DISPLAY
```

### 2. CLI

Add:

```text
co chat --reasoning-display off|summary|full
```

Compatibility:
- `--verbose` maps to `full`
- explicit `--reasoning-display ...` should take precedence over `--verbose`

### 3. Rendering Contract

Keep these concerns separate:
- persistent status messages
- progress/probe updates
- streamed reasoning/thinking
- tool-call annotations
- tool-result panels

Reasoning display policy:
- `off`: suppress model thinking stream
- `summary`: stream concise reasoning/progress to the main CLI view
- `full`: stream raw thinking content in the existing detailed style

### 3.1 `summary` Mode — Implementation Contract Aligned To Peer-System Convergence

This section defines **Co's v1 summary policy** using the patterns that do converge in:
- `docs/RESEARCH-progressive-output-best-practice.md`
- `docs/reference/RESEARCH-peer-systems.md`
- prefer product-semantic progress over raw debug output
- preserve Co's trusted local operator posture
- keep state inspectable and bounded
- avoid framework-heavy or model-heavy transformation layers

`summary` is not raw chain-of-thought. It is a lightweight progress surface that exposes what the system is doing in operator terms.

Input sources, in priority order:
1. tool-owned progress signals
2. model thinking stream (`ThinkingPart`, `ThinkingPartDelta`)
3. generic waiting fallback (`Co is thinking...`) only before real progress exists

Output shape:
- exactly one live progress line at a time
- wording should describe current action or next immediate step, not inner monologue
- examples:
  - `Checking provider and model availability...`
  - `Reviewing context before choosing a tool...`
  - `Comparing likely failure points...`

Transformation rules:
- consume the model thinking stream incrementally
- reduce it into short operator-style progress text rather than exposing raw reasoning
- prefer the latest actionable/stateful fragment over preserving every intermediate thought
- keep the rendered text short enough to work as a single live terminal line

Boundaries:
- no second model call
- no LLM-based summarization pass
- no attempt to preserve full reasoning fidelity
- `full` remains the only mode that shows raw thinking content

Rendering rules:
- one live progress region only
- replace the current live progress line in place
- preserve the last meaningful progress line in scrollback when the system transitions to tool output or final answer
- do not display raw tool-call annotations when the tool already owns the progress region

Tool interaction:
- if a tool exposes progress, tool progress takes priority over model reasoning progress
- if a tool does not expose progress, the system may fall back to normal tool-call annotation
- tool-owned progress should be phrased in product/task terms, not implementation terms where avoidable

Fallback behavior:
- show `Co is thinking...` only until the first real reasoning or tool progress update arrives
- once real progress exists, replace the generic waiting line
- never show generic waiting and live progress as parallel active states

Completion behavior:
- when final assistant text starts, stop the live progress region
- leave the last meaningful progress line in scrollback so the user can reconstruct what happened
- the final answer remains the primary artifact; progress is supporting context

Why this aligns with the peer-systems research:
- it favors inspectable, user-facing operator progress over raw verbose traces
- it keeps the implementation simple and local-first
- it avoids adding an extra summarization model or hidden transformation layer
- it treats progress as a product-semantic UX surface, which matches the research recommendation to prefer product-semantic improvements over infrastructure expansion

### 4. Progress UX

Do not overload `on_status` for all progressive rendering.

Co should introduce a dedicated progress path for long-running work:
- model reasoning progress
- tool-owned progress such as `/doctor`
- background checks and similar multi-phase flows

The generic `Co is thinking...` line should be replaced or superseded as soon as real streamed progress is available.

Frontend ownership rules:
- one live progress region only
- priority order:
  1. tool-owned progress
  2. reasoning summary progress
  3. generic `Co is thinking...`
- lower-priority progress must yield immediately when a higher-priority progress source becomes active

### 5. `/doctor`

`/doctor` should follow the general progress model, not a doctor-only rendering rule.

Target behavior:
- initial generic waiting state only until first real progress arrives
- once the model/tool starts producing meaningful progress, switch to that live progress view
- suppress redundant display layers when progress is already active
  - avoid showing generic `Co is thinking...`
  - avoid noisy `check_capabilities()` annotation when tool-owned progress is visible

## Remaining Work

### TASK-R1 — Fix Progress Ownership Handoff For `/doctor` And Other Tool-Progress Flows

Current code still starts every turn with generic `Co is thinking...`, then suppresses
`on_tool_start()` in `summary` mode. When a tool begins emitting progress without a prior
reasoning-progress update, the generic status line is not explicitly cleared before the
tool-progress live region starts. That leaves the implementation short of the intended
single-region priority model.

Done when:
- tool progress takes ownership of the live progress surface immediately
- generic `Co is thinking...` is cleared as soon as tool progress becomes active
- `summary` mode never shows stacked generic status + tool progress for `/doctor`
- behavior is enforced by a deterministic regression test, not just by code inspection

Implementation constraint:
- keep one live progress region only; do not add a second manager or parallel status surface

Likely touchpoints:
- `co_cli/context/_orchestrate.py` — `FunctionToolCallEvent` handling
- `co_cli/display/_core.py` — `on_tool_progress()`, `on_tool_start()`, `_clear_status_live()`
- `tests/test_orchestrate.py` or `tests/test_capabilities.py` — regression coverage for status-to-tool-progress handoff

### TASK-R2 — Tighten `summary` Reducer To Match The Stated Contract

The current reducer uses the whole buffer when no sentence boundary exists yet. The TODO
contract is stricter: extract the last complete sentence and emit nothing until meaningful
progress text exists.

Done when:
- `summary` mode does not expose partial trailing fragments as progress lines
- reducer emits only the last complete sentence, truncated to 80 chars
- whitespace-only and no-boundary cases emit nothing
- behavior is covered by focused reducer or stream-segment tests

Constraints:
- no second model call
- no LLM summarization
- heuristic extraction only

Likely touchpoints:
- `co_cli/display/_stream_renderer.py` — `_reduce_thinking()`, `append_thinking()`, `_flush_thinking()`
- `tests/test_orchestrate.py` — summary-mode stream behavior tests

### TASK-R3 — Align Tests With The Current Mode API And Cover The Real Gaps

The current test suite proves some of the behavior, but not the main unresolved handoff.
It also still contains at least one non-semantic `False` argument where an explicit
reasoning-display mode should be passed.

Done when:
- every direct `_execute_stream_segment()` test callsite passes an explicit reasoning-display mode string
- there is regression coverage for generic status yielding to tool progress in `summary` mode
- there is regression coverage for the stricter reducer behavior from TASK-R2
- `/doctor` progress behavior is validated through the real `check_capabilities()` path or an equivalent deterministic stream test

Likely touchpoints:
- `tests/test_orchestrate.py`
- `tests/test_capabilities.py`

## Remaining Acceptance Criteria

- `/doctor` no longer sits on `Co is thinking...` for the whole delay if reasoning/progress exists
- `/doctor` no longer shows stacked redundant layers of status + tool call + progress
- `summary` mode shows only bounded progress text derived from completed sentences, not partial raw fragments
- the remaining behavior is guarded by deterministic tests

## Default Chosen

Recommended default for Co:

```text
reasoning_display = "summary"
```

Rationale:
- `off` hides too much for an agentic CLI
- `full` is too noisy for normal interactive use
- `summary` is the best default tradeoff for day-to-day use

## Current Gaps

| Area | Gap | Severity |
|------|-----|----------|
| Tool-progress rendering | Generic status does not clearly yield to tool progress in the `/doctor` summary-mode path | high |
| Summary reducer | Current implementation can emit incomplete trailing fragments instead of only the last complete sentence | medium |
| Tests | Remaining gaps are not fully guarded by deterministic regression tests; one test still passes `False` instead of an explicit mode | medium |

## Notes

- The shipped control plane changes are already in the codebase and are no longer tracked here as TODO items.
- This file now tracks only the unresolved behavior and verification gaps needed to make the reasoning-display work complete.

---

## Implementation Review — 2026-03-29

### Evidence
| Task | done_when criterion | Spec Fidelity | Key Evidence |
|------|---------------------|---------------|-------------|
| TASK-R1 | tool progress takes ownership immediately | ✓ pass | `_core.py:334` — `on_tool_progress` calls `_clear_status_live()` first, synchronously clearing `_status_live` before activating `_tool_live` |
| TASK-R1 | generic status cleared when tool progress active | ✓ pass | `_core.py:244-249` — `_clear_status_live` stops and nulls `_status_live`; called at `on_tool_progress` entry |
| TASK-R1 | summary mode never stacks generic status + tool progress | ✓ pass | `_orchestrate.py:238` — `on_tool_start` suppressed in summary mode; `_core.py:334` — `on_tool_progress` clears status before rendering tool surface |
| TASK-R1 | deterministic regression test | ✓ pass | `test_display.py:6-20` — `test_terminal_frontend_tool_progress_replaces_generic_status` |
| TASK-R2 | no partial trailing fragments | ✓ pass | `_stream_renderer.py:154` — returns `""` when `last_end < 0` (no sentence boundary) |
| TASK-R2 | emits last complete sentence, truncated to 80 chars | ✓ pass | `_stream_renderer.py:156-166` — backward scan finds prev boundary, extracts sentence, applies `_PROGRESS_MAX_CHARS` truncation |
| TASK-R2 | whitespace-only and no-boundary cases emit nothing | ✓ pass | `_stream_renderer.py:144-145, 154` — `buf = buffer.strip(); if not buf: return ""`; `if last_end < 0: return ""` |
| TASK-R2 | behavior covered by focused reducer tests | ✓ pass | `test_display.py:27-86` — four `StreamRenderer` tests added (no-boundary, last-sentence, truncation, whitespace-only) |
| TASK-R3 | no `_execute_stream_segment` callsite passes `False` | ✓ pass | `test_orchestrate.py` was removed; no such callsites remain in `tests/` |
| TASK-R3 | regression coverage for status yielding to tool progress | ✓ pass | `test_display.py:6-20` |
| TASK-R3 | regression coverage for stricter reducer behavior | ✓ pass | `test_display.py:27-86` |
| TASK-R3 | /doctor progress via real `check_capabilities()` path | ✓ pass | `test_capabilities.py:37-82` — two tests validate progress emission and frontend routing |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| No reducer test coverage (TASK-R2 criterion 4, TASK-R3 criterion 3) | `tests/test_display.py` | blocking | Added `TerminalFrontend.active_status_text()` inspection API (`_core.py:240-242`) and four `StreamRenderer` reducer tests in `test_display.py:27-86` |

### Tests
- Command: `uv run pytest -v`
- Result: 326 passed, 0 failed
- Log: `.pytest-logs/20260329-195219-full-3.log`

### Doc Sync
- Scope: narrow — inspection API added to `TerminalFrontend`; no DESIGN doc describes the `active_surface()` / `active_tool_messages()` family, so no update required
- Result: clean

### Behavioral Verification
- `uv run co config`: ✓ healthy — all integrations reported, system starts cleanly
- No chat-loop or tool-behavior surface changed by this delivery — behavioral verification of user-facing output skipped.

### Overall: PASS
All three tasks fully satisfy their `done_when` criteria. The single blocking finding (missing reducer test coverage) was auto-fixed by adding `active_status_text()` to `TerminalFrontend` and four focused `StreamRenderer` tests. Full suite green at 326/326.
