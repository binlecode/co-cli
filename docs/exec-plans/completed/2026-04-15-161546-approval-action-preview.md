# Plan: Approval Action Preview

**Slug:** `approval-action-preview`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

UAT blocker: Stage 1 goal "action previews" is unmet. The approval prompt is a single
compressed `Allow [bold]{description}[/bold]?` inline line — no Panel, no content preview,
no visual hierarchy.

Current-state validation:

- `ApprovalSubject` (`co_cli/context/tool_approvals.py:22`) is a frozen dataclass with
  `tool_name`, `kind`, `value`, `display`, `can_remember`. No `preview` field exists.
- `resolve_approval_subject` (`tool_approvals.py:39`) already builds multi-line `display`
  for `patch` (path + 60-char old/new snippets + replace_all + hint) and byte-count-only
  `display` for `write_file`. The 60-char snippet limit is hardcoded at lines 84–85.
- `TerminalFrontend.prompt_approval(description: str)` (`_core.py:446`) renders:
  `Allow [bold]{description}[/bold]?` + `_CHOICES_HINT` inline, then calls `Prompt.ask`.
  No Panel, no preview block.
- `Frontend` protocol (`_core.py:217`) declares `prompt_approval(description: str) -> str`.
- `_collect_deferred_tool_approvals` (`orchestrate.py:206`) calls
  `frontend.prompt_approval(subject.display)` — passes only the display string.
- `tests/_frontend.py:46` and `evals/_frontend.py:49,75` implement
  `prompt_approval(self, description: str) -> str`.
- `tests/test_approvals.py:54` has `test_patch_display_truncates_long_old_string` asserting
  the 60-char truncation limit — this test must be updated when the limit changes.

No existing plan covers this gap. `tui-deferred-interactions` overlaps in TASK-3 but has
a much broader scope (request_user_input, frontend contract rewrite). This plan is
intentionally standalone and narrower.

Workflow artifact hygiene: clean.

---

## Problem & Outcome

**Problem:** The approval prompt gives the user insufficient context to make an informed
decision. For `write_file`, only the byte count is shown — the actual content is invisible.
For `patch`, old and new strings are truncated to 60 chars, hiding the real change.
All approval prompts are rendered as inline compressed text with no visual separation.

**Failure cost:** UAT users approving `write_file` or `patch` calls have no way to inspect
what is about to be written before typing `y`. This directly contradicts the "trusted"
positioning in the mission and the Stage 1 "action previews" reliability goal.

**Outcome:** Approval prompts render as a Rich Panel with:
- clear operation header
- structured action details (already in `display`, now visually separated)
- for `write_file`: first N lines of the content to be written
- for `patch`: full old/new strings up to a meaningful limit (not 60 chars)

The `y/n/a` semantics, session-memory behavior, and `ApprovalSubject` scoping model are
unchanged.

---

## Scope

In scope:
- add `preview: str | None` to `ApprovalSubject`
- populate `preview` for `write_file` (content lines) and extend patch snippet limit from
  60 → 400 chars
- change `Frontend.prompt_approval` to accept `ApprovalSubject` instead of `description: str`
- update `TerminalFrontend.prompt_approval` to render a Rich Panel
- update all call sites and Frontend implementations (orchestrate.py, test/eval frontends)
- update affected tests

Out of scope:
- `request_user_input` or any other deferred interaction type
- cross-session approval persistence
- fullscreen TUI rewrite or prompt-toolkit layout changes
- approval policy changes (y=once, a=session, n=deny semantics unchanged)
- richer display for `run_shell_command` or `web_fetch` (their `display` is already
  self-contained and readable)
- a "view full content" interactive branch — flat panel is the v1 surface

---

## Behavioral Constraints

- `y/n/a` semantics and session-memory rules must not change.
- `subject.can_remember` still gates whether `a` offers a session rule.
- `preview` content for `write_file` is capped at 30 lines OR 4000 chars, whichever is
  reached first; a `... (+N more lines)` suffix is appended when truncated.
- `patch` old/new snippets extend from 60 → 400 chars; truncation marker `…` preserved.
- Panel must degrade gracefully when `preview` is `None` (no empty block shown).
- `TerminalFrontend._input_active` ownership rules and SIGINT handler swap are preserved
  without change.
- The `Frontend` protocol change is a source-level breaking change affecting
  `tests/_frontend.py`, `evals/_frontend.py`, and `TerminalFrontend` — all three must be
  updated atomically in TASK-2.

---

## High-Level Design

### 1. Extend `ApprovalSubject` with a `preview` field

Add `preview: str | None = None` to the frozen dataclass. Populate it in
`resolve_approval_subject`:

- `write_file`: extract `content` from args, take the first 30 lines, cap at 4000 chars,
  append `... (+N more lines)` if truncated.
- `patch`: extend snippet limit from 60 → 400 chars for both `old_string` and `new_string`.
  Truncation marker `…` preserved.
- `run_shell_command`, `web_fetch`, generic: `preview=None` — their `display` is already
  readable.

### 2. Change `prompt_approval` to accept `ApprovalSubject`

Change `Frontend.prompt_approval(description: str) -> str` to
`prompt_approval(subject: ApprovalSubject) -> str`.

`TerminalFrontend.prompt_approval(subject)` renders:

```
┌─ write_file ──────────────────────────────────────────────┐
│ write_file(path='src/foo.py', 1234 bytes)                  │
│   (allow all writes to src/ this session?)                 │
│                                                            │
│ ── preview ─────────────────────────────────────────────── │
│ def hello():                                               │
│     return "world"                                         │
└────────────────────────────────────────────────────────────┘
Allow? [y=once  a=session  n=deny]
```

Implementation:
- Build a Rich `Text` or `Group` renderable from `subject.display` and (if present)
  a separator + `subject.preview` block.
- Wrap in `Panel(renderable, title=subject.tool_name, border_style="warning")`.
- Print the Panel, then call `Prompt.ask` for the choice exactly as today.
- SIGINT handler swap is unchanged.

`_collect_deferred_tool_approvals` passes `subject` instead of `subject.display`.

Test and eval frontends store `subject` (or extract `.display` for backward-compatible
assertions) in their approval call log.

---

## Implementation Plan

## ✓ DONE — TASK-1: Add `preview` to `ApprovalSubject`; extend patch snippets and add write_file content preview

files: `co_cli/context/tool_approvals.py`, `tests/test_approvals.py`

Guard condition parity:
- `preview` is `None` for `run_shell_command`, `web_fetch`, and generic tools — their
  `display` field is already sufficient. No behavioral change for those subjects.
- `write_file` preview must never fail when `content` is missing or not a string; default
  to `preview=None` gracefully.

Implementation:
- Add `preview: str | None = None` to `ApprovalSubject` frozen dataclass.
- In `resolve_approval_subject` for `write_file`:
  - Extract `content = args.get("content", "")`. If content is not a `str`, assign
    `preview = None` and skip further processing.
  - If content is empty string, assign `preview = None` (no empty block shown).
  - Otherwise split into lines; take up to 30 lines; join; cap at 4000 chars.
  - If original line count > 30 or char count was capped, append `\n... (+N more lines)`.
  - Assign the result to `preview`.
- In `resolve_approval_subject` for `patch`:
  - Change snippet limit constant from 60 → 400 for both `old_string` and `new_string`.
  - Keep the `…` truncation suffix.
  - `preview` stays `None` for patch (the extended snippets in `display` are sufficient).
- Update `tests/test_approvals.py`:
  - Update `test_patch_display_truncates_long_old_string` — old assertion checks 60-char
    limit; new assertion checks 400-char limit.
  - Add `test_write_file_preview_populated` — assert `subject.preview` contains the first
    lines of `content` when content is non-empty.
  - Add `test_write_file_preview_truncated` — assert preview is capped and truncation
    marker is present for content > 30 lines.
  - Add `test_write_file_preview_none_for_empty_content` — assert `preview is None` when
    content is empty string.

done_when:
- `uv run pytest tests/test_approvals.py -x` passes with updated truncation limit and new
  preview assertions.
- `resolve_approval_subject("write_file", {"path": "f.py", "content": "line\n" * 50}).preview`
  contains `"... ("` and is not `None`.

success_signal: N/A — data-model change; display is exercised in TASK-2.

prerequisites: []

---

## ✓ DONE — TASK-2: Update `Frontend` protocol + `TerminalFrontend` to render a Panel; update all call sites

files: `co_cli/display/_core.py`, `co_cli/context/orchestrate.py`, `tests/_frontend.py`, `evals/_frontend.py`

Guard condition parity:
- SIGINT handler swap in `TerminalFrontend.prompt_approval` must be preserved — it wraps
  `Prompt.ask` exactly as today.
- `_input_active` state and `_clear_status_live()` call must remain before the Panel render.
- `Prompt.ask` choices (`["y", "n", "a"]`, default `"n"`) are unchanged.

Implementation:
- In `co_cli/display/_core.py`:
  - Add direct import: `from co_cli.context.tool_approvals import ApprovalSubject`
    (no TYPE_CHECKING guard needed — tool_approvals.py has no dependency on _core.py).
  - Change `Frontend.prompt_approval(self, description: str) -> str` to
    `prompt_approval(self, subject: ApprovalSubject) -> str`.
  - In `TerminalFrontend.prompt_approval(self, subject: ApprovalSubject) -> str`:
    - Call `self._clear_status_live()`.
    - Build a Rich renderable:
      - Start with `Text(subject.display)` for the operation details.
      - If `subject.preview` is not None, append a visual separator line and the preview
        text as a `Text` block (`style="dim"`). If `subject.preview` is None, render only
        `subject.display` — no separator, no empty block.
    - Wrap in `Panel(renderable, title=subject.tool_name, border_style="warning",
        title_align="left")`.
    - `console.print(panel)`.
    - Print the choices hint line: `console.print("Allow?" + _CHOICES_HINT, end=" ")`.
    - SIGINT handler swap + `Prompt.ask` unchanged.
- In `co_cli/context/orchestrate.py`:
  - Change `frontend.prompt_approval(subject.display)` → `frontend.prompt_approval(subject)`.
- In `tests/_frontend.py`:
  - Change `prompt_approval(self, description: str)` → `prompt_approval(self, subject: ApprovalSubject)`.
  - Store `subject.display` in any existing approval-call log to preserve backward-compatible
    assertions (or update assertions to use `subject` directly).
- In `evals/_frontend.py`:
  - Same signature update as `tests/_frontend.py`.

done_when:
- `uv run pytest tests/test_approvals.py tests/test_display.py -x` passes, including the
  TASK-3 approval-loop regression that asserts `isinstance(recorded_subject, ApprovalSubject)`.

success_signal: running `uv run co chat` and triggering a `write_file` approval shows a
bordered Panel with the file content preview before the `y/n/a` prompt.

prerequisites: [TASK-1]

---

## ✓ DONE — TASK-3: Add regression coverage for Panel rendering and approval-loop wiring

files: `tests/test_display.py`, `tests/test_approvals.py`

Implementation:
- In `tests/test_display.py` — add a test that constructs a `TerminalFrontend` and calls
  `prompt_approval` with a `write_file` subject that has a populated `preview`. Capture
  console output (use `Console(file=io.StringIO())` or `console.capture()`). Assert:
  - the tool name appears in output (Panel title)
  - the preview content appears in output
  - `Prompt.ask` is called with choices `["y", "n", "a"]` (use monkeypatch on `Prompt.ask`
    to inject a return value and avoid blocking input)
- In `tests/test_approvals.py` — add one approval-loop-level regression: extend
  `SilentFrontend` in `tests/_frontend.py` with a `last_approval_subject: ApprovalSubject | None`
  field that is set inside `prompt_approval`. Construct it with `_approval_response = "y"`,
  run a deferred approval through `_collect_deferred_tool_approvals` for a `write_file`
  call, and assert:
  - `isinstance(frontend.last_approval_subject, ApprovalSubject)` is True
  - the returned approval decision is `True` for the tool call ID

done_when:
- `uv run pytest tests/test_display.py tests/test_approvals.py -x` passes.
- The approval-loop regression asserts `isinstance(recorded_subject, ApprovalSubject)`.

success_signal: N/A — regression coverage.

prerequisites: [TASK-2]

---

## Testing

Scope during implementation:

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_approvals.py tests/test_display.py -x \
  2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-approval-action-preview.log
```

Full suite before shipping:

```bash
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

Required behavioral checks:
- `write_file` approval shows file content preview in Panel
- `patch` approval shows full snippets (400 chars) instead of 60-char stubs
- `y/n/a` semantics, session-memory, and SIGINT behavior are unchanged
- Panel degrades cleanly (no empty preview block) when `preview is None`

---

## Open Questions

None. All design decisions resolved by inspection of current source.

---

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev approval-action-preview`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| All changed files | No issues found — spec fidelity, code hygiene, test policy all clean | — | TASK-1/2/3 |

**Overall: clean / 0 blocking / 0 minor**

## Delivery Summary — 2026-04-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_approvals.py -x` passes with updated truncation limit and new preview assertions | ✓ pass |
| TASK-2 | `uv run pytest tests/test_approvals.py tests/test_display.py -x` passes, including approval-loop regression | ✓ pass |
| TASK-3 | `uv run pytest tests/test_display.py tests/test_approvals.py -x` passes | ✓ pass |

**Tests:** full suite — 490 passed, 0 failed
**Independent Review:** clean / 0 blocking / 0 minor
**Doc Sync:** clean (tools.md, tui.md — no inaccuracies found)

**Overall: DELIVERED**
All three tasks shipped. Approval prompt now renders a Rich Panel with tool_name as title, structured display body, and (for write_file) a dim preview of file content below a Rule separator. Patch snippet limit extended 60 → 400 chars. Note: TASK-3 plan specified `monkeypatch on Prompt.ask` which violates repo policy; replaced with direct `_build_approval_panel` testing via `console.capture()` and real `SilentFrontend` for loop wiring.
