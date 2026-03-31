# TODO — 6. Trust UX Action Previews

**Task type: feature**

## Goal

Improve approval clarity, previews, and reversibility for risky actions.

## Current-State Validation

- Approval subjects already exist for shell/path/domain/tool scopes (`co_cli/tools/_tool_approvals.py:resolve_approval_subject`).
- Shell already classifies DENY / ALLOW / REQUIRE_APPROVAL (`co_cli/tools/_shell_policy.py`).
- Remembered approvals are session-scoped (`deps.session.session_approval_rules`).
- Approval prompts expose developer-legible hints like `[always → session: git *]` — these are not user-legible.
- `_collect_deferred_tool_approvals` and `_run_approval_loop` exist in `co_cli/context/_orchestrate.py` and own approval orchestration for file and web tools.
- `write_file` and `edit_file` are registered `requires_approval=True` and are deferred before execution. The approval loop already has the full deferred args (path, content, search, replacement, replace_all) at `_orchestrate.py:168-169` via `decode_tool_args(call.args)`. The tools have not executed at approval time — preview must be derived from deferred args in the approval loop, not populated inside the tools.
- Current `resolve_approval_subject` for file tools only surfaces `path` in the display — `content`, `search`, `replacement`, and `replace_all` are discarded. The real gap is richer arg formatting, not a new transport layer.
- Shell approval is a hybrid model: `run_shell_command` is registered `requires_approval=False` and raises `ApprovalRequired` inside the tool for REQUIRE_APPROVAL commands (`shell.py:43-45`). File and web tools use agent-layer deferral. These are two distinct approval paths.
- `TerminalFrontend.prompt_approval` renders `Allow {description}?` (`_core.py:385`) — `subject.display` must be a noun phrase, not a full question.
- `ToolApproved` (pydantic-ai 1.73) not adopted: local approval surface returns y/n/a only; no caller consumes richer metadata; editable approvals are out of scope.

## Behavioral Constraints

- File/web approval ownership stays in `_collect_deferred_tool_approvals` / `_run_approval_loop` — never inside those tools.
- Shell approval is hybrid: `run_shell_command` raises `ApprovalRequired` inside the tool for policy-required commands; this is intentional and must not change.
- Shell DENY / ALLOW / REQUIRE_APPROVAL classification in `_shell_policy.py` must not change.
- Remembered approval matching (`is_auto_approved`) must use exact kind+value semantics — no wildcard expansion at match time.
- Preview must not replace raw args visibility — the approval prompt must display raw args alongside the structured summary.
- The session approval rule store (`deps.session.session_approval_rules`) remains session-scoped; no cross-session persistence in this delivery.
- `subject.display` must be a noun phrase — the frontend already wraps it as `Allow {display}?`. Full questions in display cause doubled output.

## Implementation Target

Enrich `resolve_approval_subject` in `_tool_approvals.py` to show more arg detail for file write tools, and replace developer-legible scope hints with user-legible noun phrases across all four scope types.

## Primary Entry Points

- `co_cli/tools/_tool_approvals.py` — `resolve_approval_subject` (enrich file display + fix scope wording)
- `co_cli/context/_orchestrate.py` — `_collect_deferred_tool_approvals` (already has full deferred args; no structural change needed)
- `co_cli/display/_core.py` — `TerminalFrontend.prompt_approval` (renders `Allow {subject.display}?`)

## Code Changes

### ✓ DONE — TASK-1: Enrich file-write approval display from deferred args

Enrich `resolve_approval_subject` in `_tool_approvals.py` to surface more arg detail for
`write_file` and `edit_file` approvals. All args are already available in the deferred call
at approval time — no new module, no changes to `files.py` or orchestration structure needed.

- For `write_file`: show path + byte count (derived from `len(content.encode())`) in the display.
- For `edit_file`: show path + `search` snippet + `replacement` snippet (truncated if long) + `replace_all` flag.

**files:**
- `co_cli/tools/_tool_approvals.py` (enrich `resolve_approval_subject` file branch)

**done_when:**
1. `write_file` approval display shows path and byte count.
2. `edit_file` approval display shows path, search snippet, and replacement snippet.
3. Raw args remain visible alongside the structured summary — both present in the same prompt output.
4. A test in `tests/` asserts that file-write and file-edit approval display strings contain the enriched fields.

**success_signal:**
- Manually trigger a `write_file` approval prompt and confirm enriched display and raw args are both visible.
- `uv run pytest tests/ -x -k approval` — all approval-path tests pass.

---

### ✓ DONE — TASK-2: Tighten approval scope wording in code and UI

Replace developer-legible scope hints with user-legible noun phrases across all four scope types.
The frontend renders `Allow {display}?` — display must be a noun phrase, not a full question.

| Scope | Current display | Target display (noun phrase) |
|-------|----------------|------------------------------|
| shell | `run_shell_command(cmd='git ...')\n  [always → session: git *]` | `run_shell_command(cmd='git ...')\n  (allow all git commands this session?)` |
| path | `write_file(path='...')\n  [always → session: /dir/**]` | `write_file(path='...')\n  (allow all writes to /dir/ this session?)` |
| domain | `web_fetch(url='...')\n  [always → session: example.com]` | `web_fetch(url='...')\n  (allow all fetches to example.com this session?)` |
| tool | `tool_name\n  [always → session: tool_name]` | `tool_name\n  (always allow tool_name this session?)` |

**prerequisite:** none (can run parallel to TASK-1)

**files:**
- `co_cli/tools/_tool_approvals.py` (`resolve_approval_subject` — rewrite hint strings)

**done_when:**
1. `grep "always → session" co_cli/tools/_tool_approvals.py` returns zero matches.
2. Hint strings read as parenthetical natural-language scope questions, not bracket notation.
3. Existing session-approval matching tests pass (`is_auto_approved` behavior is unchanged).

**success_signal:**
- Trigger a shell approval prompt: confirm the hint reads as a natural-language scope question, not bracket notation.
- `uv run pytest tests/ -x -k approval` — all tests pass.

---

## Implementation Sequence

1. Enrich `resolve_approval_subject` file branch in `_tool_approvals.py` (TASK-1) — derive byte count / search+replacement snippets from deferred args already in the approval loop.
2. Run TASK-2 (scope wording fix) in parallel with TASK-1 — it touches the same function but is independent of preview shape.

## Implementation Notes

- Do not create a new `_preview.py` module or change `files.py`. The deferred args are available in `_collect_deferred_tool_approvals` — enrich the display string directly in `resolve_approval_subject`.
- `write_file`/`edit_file` execute only after approval resolves. Any preview must be derived at approval time from deferred args, not populated inside the tools at execution time.
- Shell approval is hybrid by design: `run_shell_command` raises `ApprovalRequired` for policy-required commands; file/web tools use agent-layer deferral. Do not conflate or merge these paths.
- Keep raw args visible even when enriched display exists — both must appear in the same approval prompt.
- `subject.display` must be a noun phrase — `TerminalFrontend.prompt_approval` already wraps it as `Allow {display}?`. Full questions in display produce doubled output.

## Review Checklist

- File/web approval ownership still lives in orchestration (`_collect_deferred_tool_approvals`), not inside those tools.
- Shell approval hybrid model is preserved — `run_shell_command` still raises `ApprovalRequired` for policy-required commands.
- Enriched display derives from deferred args already in the approval loop — no tool-side changes.
- `subject.display` is a noun phrase throughout — no full questions in display strings.
- Remembered approval wording matches actual subject-matching semantics in `_tool_approvals.py`.
- `is_auto_approved` exact-match semantics are unchanged.

## Testing

- Approval display coverage — tests for `resolve_approval_subject` output after TASK-1 (enriched fields) and TASK-2 (noun-phrase hints).
- Run targeted tests first, then full suite before ship:
  - `uv run pytest tests/ -x -k approval`
  - `uv run pytest tests/ -x` (full suite before ship)
- Verify:
  - Ordinary approval flow still works.
  - Shell policy classification (DENY/ALLOW/REQUIRE_APPROVAL) is unchanged.
  - Write tools remain approval-gated as before.

## Delivery Summary — 2026-03-30

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | write_file shows path+byte count; edit_file shows path+search+replacement+replace_all; test asserts enriched fields | ✓ pass |
| TASK-2 | `grep "always → session"` returns zero matches; hint strings are noun phrases; is_auto_approved tests pass | ✓ pass |

**Tests:** full suite — 106 passed, 1 pre-existing flaky timeout (test_history::test_compact_produces_two_message_history — unrelated LLM contention, passes in isolation)
**Independent Review:** clean — 0 blocking, 0 minor
**Doc Sync:** clean — DESIGN-core-loop.md, no inaccuracies found

**Overall: DELIVERED**
Both tasks shipped in a single file (`co_cli/tools/_tool_approvals.py`). File-write approval prompts now show enriched arg detail (path + byte count for writes, path + search/replacement snippets for edits). All four scope-hint types replaced with user-legible noun phrases compatible with the `Allow ...?` frontend wrapper.
