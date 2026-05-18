# TUI Status Surface

**Slug:** `tui-status-surface`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Current-state validation against the latest code:

- The chat loop hands terminal ownership to `PromptSession.prompt_async()` in `co_cli/main.py`. `PromptSession` accepts a `bottom_toolbar` callable — this is the integration point for the prompt-idle footer.
- `TerminalFrontend` in `co_cli/display/core.py` owns four transient surfaces: `_status_live`, `_tool_live`, `_thinking_live`, `_live`. None persist across the prompt boundary.
- `on_status(...)` is the only display path from `co_cli/context/orchestrate.py` to the frontend; it is fully transient.
- The `Frontend` protocol has no method for pushing structured runtime state. Adding one is the core contract change in this plan.
- `TerminalFrontend._input_active` already gates rendering during the prompt phase. `set_input_active(True/False)` is called by the REPL loop around every `prompt_async()` call.
- Relevant `CoDeps` state:
  - `deps.runtime.turn_usage: RunUsage | None` — reset by `reset_for_turn()` each turn; holds last-completed-turn token counts after the turn returns
  - `deps.model_max_ctx: int` — model context ceiling, set at bootstrap
  - `deps.session.session_path: Path` — initialized to `Path()` (empty), set after first `persist_session_history()` call; short label is `session_path.stem[-8:]`
  - `deps.session.background_tasks: dict[str, BackgroundTaskState]`
  - `deps.session.session_approval_rules: list[SessionApprovalRule]`
- `HeadlessFrontend` in `co_cli/display/headless.py` implements the full `Frontend` protocol. Any new protocol method must be added here as a no-op (with optional inspection state for tests).

Prerequisite re-evaluation:

The original plan listed `tui-deferred-interactions` as a prerequisite, on the grounds that it "changes the frontend interaction contract." The current code already contains `QuestionPrompt`, `prompt_question`, `prompt_confirm`, and the approval panel with preview — the interaction seam is stable. This plan does not depend on `tui-deferred-interactions` proceeding first.

---

## Problem & Outcome

Problem: co has no stable status surface. At the prompt-idle phase — the exact moment when the operator is deciding what to ask next — there is no ambient information about where the session stands.

Failure cost:
- The operator cannot see how close to the context window limit the last turn ran without issuing a command to check.
- Background tasks and remembered approval rules accumulate silently; there is no ambient count.
- The session label is not visible at the prompt, making it easy to lose track of which session is active in a long or multi-session workflow.

Note on active-turn visibility: the existing `_tool_live` and `_status_live` surfaces cover visibility during active tool calls and thinking. This plan targets the prompt-idle gap — the period between turn completion and the next user input. It does not extend into active-turn display, which remains owned by the existing transient surfaces.

Outcome: add a compact persistent status line in the `PromptSession` bottom toolbar that keeps session identity, last-turn context usage, background task count, and approval rule count visible at the prompt. No architecture changes to the REPL loop, no new infrastructure.

The surface answers:
- Which session am I in?
- How close to the context limit did the last turn run?
- Are background tasks active?
- Do I have session-scoped approval rules in effect?

---

## Scope

In scope:
- `StatusSnapshot` frozen dataclass in `co_cli/display/core.py` — the typed contract for footer content
- `_build_status_snapshot(deps, mode)` assembly helper in `co_cli/main.py`
- `Frontend.update_status(snapshot)` push method added to the protocol
- `TerminalFrontend.update_status(snapshot)` — stores snapshot, feeds `bottom_toolbar`
- `HeadlessFrontend.update_status(snapshot)` — no-op, stores snapshot for test inspection
- `PromptSession(bottom_toolbar=frontend.render_footer_toolbar)` wired in `_chat_loop`
- REPL loop push points: after startup banner clear, before each `prompt_async` call, at turn start, after turn completion
- Focused tests in `tests/test_display.py` (new file)

Out of scope:
- Converting the REPL to a fullscreen prompt-toolkit `Application`
- Active-turn footer visibility (blocked by Rich's single-Live constraint without `patch_stdout()`; v2 concern)
- Live within-turn context % (requires per-request hooks not yet exposed by pydantic-ai)
- Granular busy states beyond `idle` / `active`
- Approval-pending mode indicator (blocking approval prompt already renders in the terminal)
- Mouse support, alternate screen, or other prompt-toolkit layout features
- Remote liveness probes or new backend health checks

---

## Behavioral Constraints

- Do not replace `PromptSession` with a custom prompt-toolkit `Application`.
- Do not add network or provider probes to populate the footer. Every field must derive from state `CoDeps` already tracks.
- `StatusSnapshot` is a display type. It lives in `co_cli/display/core.py`. `co_cli/deps.py` is not modified.
- Every new `Frontend` protocol method must be implemented in both `TerminalFrontend` and `HeadlessFrontend`. A protocol addition with no `HeadlessFrontend` impl breaks the full eval and test suite.
- Context % is post-turn only: `turn_usage.request_tokens / model_max_ctx` from the last completed turn. It is `None` before any turn completes and stale during an active turn. This is by design and documented in the snapshot contract, not papered over.
- `session_label` is `"—"` when `session_path` is empty (before first turn persists). The footer must not crash or show a partial stem on an empty path.
- `model_max_ctx == 0` (not yet probed at bootstrap, or degraded startup): `context_pct` is `None`.
- The footer text returned by `render_footer_toolbar` must be plain text (no Rich markup). Prompt-toolkit renders it, not Rich.
- Footer content must fit comfortably in 80 columns. Fields degrade by dropping rightmost optional fields when content is long.

---

## Design

### StatusSnapshot

```python
@dataclass(frozen=True)
class StatusSnapshot:
    session_label: str           # session_path.stem[-8:] or "—"
    mode: Literal["idle", "active"]
    context_pct: float | None    # turn_usage.request_tokens / model_max_ctx; None until first turn completes
    background_task_count: int   # len(session.background_tasks)
    approval_count: int          # len(session.session_approval_rules)
```

### Assembly helper (`co_cli/main.py`)

```python
def _build_status_snapshot(deps: CoDeps, mode: Literal["idle", "active"]) -> StatusSnapshot:
    stem = deps.session.session_path.stem
    session_label = stem[-8:] if stem else "—"
    context_pct: float | None = None
    if deps.runtime.turn_usage is not None and deps.model_max_ctx > 0:
        context_pct = deps.runtime.turn_usage.request_tokens / deps.model_max_ctx
    return StatusSnapshot(
        session_label=session_label,
        mode=mode,
        context_pct=context_pct,
        background_task_count=len(deps.session.background_tasks),
        approval_count=len(deps.session.session_approval_rules),
    )
```

### Compact footer text (`TerminalFrontend.render_footer_toolbar`)

Produces a plain-text string consumed by `PromptSession`'s `bottom_toolbar`. Fields are joined with ` · `. Optional fields are dropped when their value is zero or `None`:

```
a1b2c3d4 · idle · ctx 47% · 2 bg · 1 approval
a1b2c3d4 · active · ctx 47%
a1b2c3d4 · idle
— · idle
```

Rules:
- `session_label` and `mode` are always present
- `ctx N%` shown when `context_pct` is not `None`; formatted as `int(context_pct * 100)%`
- `N bg` shown when `background_task_count > 0`
- `N approval` / `N approvals` shown when `approval_count > 0`

### Push call sites (`co_cli/main.py`, `_chat_loop`)

Four push points cover the full prompt-idle lifecycle:

| Point | Code location | Mode | Notes |
|---|---|---|---|
| After startup banner | after `frontend.clear_status()` at startup | `idle` | Establishes initial toolbar before first prompt |
| Before prompt entry | just before each `session.prompt_async()` | `idle` | Reflects post-turn state: fresh context_pct, updated counts |
| At turn start | after `frontend.set_input_active(False)`, before `_run_foreground_turn()` | `active` | context_pct is None here (turn_usage was reset) |
| After turn completion | after `_run_foreground_turn()` returns | `idle` | context_pct now available from turn_usage |

The before-prompt push (point 2) is the highest-value push — it fires with post-turn `turn_usage` already populated, giving an accurate context % at the moment the operator reads the footer.

The at-turn-start push (point 3) sets `mode="active"` so the toolbar reflects the busy state if the operator glances at it after submitting input. `context_pct` will be `None` here because `reset_for_turn()` cleared `turn_usage`; this is correct and expected.

After-turn-completion push (point 4) and before-prompt push (point 2) overlap — both fire in close succession at turn end. Point 4 is the "turn is done" signal; point 2 is the "entering prompt, final snapshot" signal. Keeping both is intentional: point 4 provides a logical boundary even if future code inserts steps between turn completion and prompt re-entry.

### `PromptSession` wiring (`co_cli/main.py`, `_chat_loop`)

```python
session = PromptSession(
    history=FileHistory(str(USER_DIR / "history.txt")),
    completer=completer,
    complete_while_typing=True,
    style=_COMPLETION_STYLE,
    bottom_toolbar=frontend.render_footer_toolbar,
)
```

`render_footer_toolbar` is a bound method; prompt-toolkit calls it synchronously before each render cycle. It returns `""` when `_footer_snapshot` is `None` (no toolbar rendered until first push).

---

## Implementation Plan

### ✓ DONE TASK-1: Add `StatusSnapshot` and `update_status` to the Frontend contract

files: `co_cli/display/core.py`, `co_cli/display/headless.py`

Implementation:
- Add `StatusSnapshot` frozen dataclass to `co_cli/display/core.py` with fields: `session_label`, `mode`, `context_pct`, `background_task_count`, `approval_count` as specified in the design.
- Add `update_status(snapshot: StatusSnapshot) -> None` to the `Frontend` Protocol.
- Add `render_footer_toolbar() -> str` to `TerminalFrontend` — returns `""` until a snapshot is pushed.
- Implement `TerminalFrontend.update_status(snapshot)` — stores to `_footer_snapshot: StatusSnapshot | None`.
- Implement `HeadlessFrontend.update_status(snapshot)` — no-op; also store to `self.last_status_snapshot: StatusSnapshot | None` for test inspection.

done_when: |
  `StatusSnapshot` is the single typed contract for footer content;
  both `TerminalFrontend` and `HeadlessFrontend` implement `update_status`;
  `render_footer_toolbar` returns `""` with no snapshot and a compact plain-text string with one
success_signal: the Frontend protocol is extended without breaking existing implementations
prerequisites: []

### ✓ DONE TASK-2: Implement compact footer text and `PromptSession` wiring

files: `co_cli/display/core.py`, `co_cli/main.py`

Implementation:
- Implement `TerminalFrontend.render_footer_toolbar()` returning the compact plain-text string per the format rules in the design. No Rich markup. Gracefully handles `None` `context_pct`, zero counts, and empty `session_label`.
- Wire `bottom_toolbar=frontend.render_footer_toolbar` into the `PromptSession` constructor in `_chat_loop`.
- Add `_build_status_snapshot(deps, mode)` helper to `co_cli/main.py` per the design.

done_when: |
  `PromptSession` shows the footer toolbar when a snapshot has been pushed;
  footer text is plain text, compact, and degrades gracefully for missing fields;
  the helper assembles correctly from CoDeps without modifying deps.py
success_signal: footer is visible at the prompt with real session state
prerequisites: [TASK-1]

### ✓ DONE TASK-3: Wire the four push call sites in `_chat_loop`

files: `co_cli/main.py`

Implementation:
- After `frontend.clear_status()` at startup: call `frontend.update_status(_build_status_snapshot(deps, "idle"))`.
- Before each `session.prompt_async()`: call `frontend.update_status(_build_status_snapshot(deps, "idle"))`. This is the primary post-turn refresh.
- After `frontend.set_input_active(False)` and before dispatching to `_handle_one_input` (which may call `_run_foreground_turn`): call `frontend.update_status(_build_status_snapshot(deps, "active"))`.
- After `_run_foreground_turn` returns (inside `_handle_one_input` or its callers): call `frontend.update_status(_build_status_snapshot(deps, "idle"))`.

Note: `orchestrate.py` is not modified. The push model keeps the display concern in `main.py`, which already owns the REPL lifecycle. The orchestration layer is untouched.

done_when: |
  the toolbar reflects idle/active mode at the correct lifecycle points;
  context_pct in the idle snapshot reflects the just-completed turn's token usage;
  the footer shows "—" for session_label before the first turn persists
success_signal: the footer accurately mirrors session state across the full turn lifecycle
prerequisites: [TASK-2]

### ✓ DONE TASK-4: Focused tests

files: `tests/test_display.py` (new)

Coverage must include:
- `render_footer_toolbar` returns `""` when no snapshot has been pushed
- snapshot with all fields populated produces the expected compact string
- snapshot with `context_pct=None` omits the ctx field
- snapshot with `background_task_count=0` omits the bg field
- snapshot with `approval_count=0` omits the approval field
- snapshot with `session_label="—"` (empty session path) renders without error
- `HeadlessFrontend.update_status` stores the snapshot on `last_status_snapshot`
- `_build_status_snapshot` with empty `session_path` produces `session_label="—"`
- `_build_status_snapshot` with `turn_usage=None` produces `context_pct=None`
- `_build_status_snapshot` with `model_max_ctx=0` produces `context_pct=None`

done_when: |
  all snapshot assembly and render paths are covered;
  edge cases (empty session, no turn yet, zero ctx ceiling) are verified
success_signal: footer content is correct and stable across all degenerate inputs
prerequisites: [TASK-3]

---

## Testing

During implementation, scope to the affected test first:

```
mkdir -p .pytest-logs && uv run pytest tests/test_display.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tui-status-surface.log
```

`tests/test_display.py` is a new file created in TASK-4.

Before shipping:

```
scripts/quality-gate.sh types
```

---

## Delivery Summary — 2026-05-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `StatusSnapshot` typed contract; both frontends implement `update_status`; `render_footer_toolbar` returns `""` with no snapshot | ✓ pass |
| TASK-2 | `PromptSession` wired with `bottom_toolbar`; footer degrades gracefully; `_build_status_snapshot` assembled from `CoDeps` | ✓ pass |
| TASK-3 | Toolbar reflects idle/active at all four lifecycle points; `context_pct` populated post-turn; `"—"` shown before first persist | ✓ pass |
| TASK-4 | All snapshot assembly and render paths covered; edge cases (empty session, no turn, zero ctx ceiling) verified | ✓ pass |

**Tests:** scoped — 15 passed, 0 failed
**Doc Sync:** fixed (`docs/specs/tui.md` — added `StatusSnapshot`, `update_status`, `HeadlessFrontend`, `render_footer_toolbar`, `_build_status_snapshot` to Frontend surface table and Files table)

**Overall: DELIVERED**
All four tasks passed. The `PromptSession` footer toolbar now shows session label, idle/active mode, context %, background task count, and approval rule count at the prompt-idle phase. Note: plan spec used `turn_usage.request_tokens` but pydantic_ai's `RunUsage` exposes `input_tokens` — implemented with the correct field name.

---

## Implementation Review — 2026-05-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `StatusSnapshot` typed contract; both frontends implement `update_status`; `render_footer_toolbar` returns `""` with no snapshot | ✓ pass | core.py:190-199 — frozen dataclass, 5 fields; core.py:257-259 — Protocol method; core.py:327-339 — returns `""` until snapshot pushed |
| TASK-2 | `PromptSession` wired with `bottom_toolbar`; footer degrades gracefully; `_build_status_snapshot` assembled from CoDeps | ✓ pass | main.py:520 — `bottom_toolbar=frontend.render_footer_toolbar` (callable ref); main.py:480-492 — helper, uses `input_tokens` (correct; `request_tokens` is deprecated in pydantic-ai) |
| TASK-3 | Toolbar reflects idle/active at all 4 lifecycle points; `context_pct` populated post-turn; `"—"` shown before first persist | ✓ pass | main.py:555-556 (startup idle), 562-564 (pre-prompt idle), 565-566 (post-input active), 456/472 (post-turn idle both branches); orchestrate.py untouched |
| TASK-4 | All snapshot assembly and render paths covered; edge cases verified | ✓ pass | test_display.py:23-157 — 15 tests, all passing; zero mocks/patches; real `CoDeps` constructed via `ShellBackend()` + `load_config()` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `HeadlessFrontend` docstring "Recorded state" list omitted `last_status_snapshot` | headless.py:27 | minor | Added entry: `last_status_snapshot: most recent StatusSnapshot pushed via update_status` |

### Tests
- Command: `uv run pytest -x -v`
- Result: 483 passed, 0 failed
- Log: `.pytest-logs/20260518-184105-review-impl.log`

### Behavioral Verification
- `uv run co chat`: the `bottom_toolbar` is wired at `PromptSession` construction (main.py:520) and `render_footer_toolbar` is the callback. The toolbar is a terminal-interactive surface — it renders only during `prompt_async`. Non-interactive shell cannot exercise it. Functional correctness verified via test_display.py (all 15 tests pass including full-field render, all degenerate inputs, and `_build_status_snapshot` assembly from real `CoDeps`).
- `success_signal` for TASK-1–3 ("protocol extended without breaking existing implementations", "footer visible at prompt", "footer accurately mirrors session state"): verified by 483-test green suite — all existing tests pass with the new protocol method in place.

### Overall: PASS
All four tasks deliver as specified; the one minor docstring gap was fixed in-review; 483 tests green; lint clean.
