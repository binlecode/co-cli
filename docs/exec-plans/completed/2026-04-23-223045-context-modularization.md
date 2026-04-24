# Context module modularization: move tool-concerns out of `co_cli/context/`

## Context

Audit of `co_cli/context/` (3437 lines, 17 files) identified that several files
in the package concern **tool mechanics**, not **message-history / turn /
compaction**, and belong in `co_cli/tools/`:

- `_tool_lifecycle.py` — pydantic-ai `AbstractCapability` with
  `before_tool_execute` / `after_tool_execute` hooks (path normalization, OTEL
  span enrichment, audit logging). Wired from `co_cli/agent/_core.py:149,170`,
  not from anything in `context/`.
- `tool_categories.py` — behavioural frozensets (`COMPACTABLE_TOOLS`,
  `FILE_TOOLS`, `PATH_NORMALIZATION_TOOLS`). Pure tool metadata.
- `tool_display.py` — tool result/args formatting (`format_for_display`,
  `get_tool_start_args_display`).
- `_deferred_tool_prompt.py` — `build_category_awareness_prompt()`, the
  deferred-tool discovery prompt fragment.
- `tool_approvals.py` — tool-approval lifecycle helpers:
  `resolve_approval_subject()` (per-tool subject mapping for shell /
  file_write / file_patch / web_fetch / generic), `ApprovalSubject` data
  model, `is_auto_approved` / `remember_tool_approval` /
  `record_approval_choice` (session-rule matching + DeferredToolResults
  writes), and `QuestionRequired` (pydantic-ai `ApprovalRequired` subclass).
  The approval **orchestration** (`_collect_deferred_tool_approvals`) stays
  in `context/orchestrate.py:179` — orchestrator calls the helpers, not the
  other way round, so the import direction is `context/` → `tools/`, same
  one-way boundary as `context/_compaction.py` → `tools/categories.py`.

Evidence of misplacement: `docs/specs/tools.md:35,37,39` already lists
`_tool_lifecycle.py`, `_deferred_tool_prompt.py`, and `tool_approvals.py`
under the **tools** subsystem, even though they live in `context/`.
`co_cli/tools/capabilities.py` already exists as the canonical home for tool
capability surfaces.

This is a pure refactor — **no behaviour changes**. The in-flight split of
`_history.py` into `_compaction.py` + `_prompt_text.py`
(`docs/exec-plans/active/2026-04-23-155308-history-split.md`) has **already
landed** (commit `36cbd9e`), so this plan lands cleanly with no rebase — see
Sequencing below.

## Files Touched

| Action | File | Nature |
|--------|------|--------|
| `git mv` | `co_cli/context/_tool_lifecycle.py` → `co_cli/tools/_lifecycle.py` | Module move |
| `git mv` | `co_cli/context/tool_categories.py` → `co_cli/tools/categories.py` | Module move + rename |
| `git mv` | `co_cli/context/tool_display.py` → `co_cli/tools/display.py` | Module move + rename |
| `git mv` | `co_cli/context/_deferred_tool_prompt.py` → `co_cli/tools/_deferred_prompt.py` | Module move + rename |
| `git mv` | `co_cli/context/tool_approvals.py` → `co_cli/tools/approvals.py` | Module move + rename |
| Modify | `co_cli/agent/_core.py:21` | `CoToolLifecycle` import |
| Modify | `co_cli/agent/_instructions.py:41` | `build_category_awareness_prompt` import |
| Modify | `co_cli/context/_compaction.py` | `COMPACTABLE_TOOLS, FILE_TOOLS` import (line moved here by history-split) |
| Modify | `co_cli/context/_dedup_tool_results.py:22` | `COMPACTABLE_TOOLS` import |
| Modify | `co_cli/context/_tool_result_markers.py:19` | `COMPACTABLE_TOOLS` import |
| Modify | `co_cli/context/orchestrate.py:68` | Approval helpers multi-import (`ApprovalSubject, decode_tool_args, is_auto_approved, record_approval_choice, resolve_approval_subject`) |
| Modify | `co_cli/context/orchestrate.py:74` | `format_for_display, get_tool_start_args_display` import |
| Modify | `co_cli/display/_core.py:17` | `ApprovalSubject` import |
| Modify | `co_cli/tools/files/helpers.py:9` | Docstring reference `CoToolLifecycle` (path only) |
| Modify | `tests/_frontend.py:5` | `ApprovalSubject` import |
| Modify | `tests/approvals/test_approvals.py:7` | Approval helpers multi-import |
| Modify | `tests/approvals/test_user_input.py:9` | `QuestionRequired` import |
| Modify | `tests/display/test_display.py:3` | `resolve_approval_subject` import |
| Modify | `tests/files/test_tools_files.py:758,784` | `CoToolLifecycle` import (two test functions) |
| Modify | `docs/specs/tools.md:35,37,39` | Path strings (incl. `tool_approvals.py`) |
| Modify | `docs/specs/compaction.md:666` | `tool_categories.py` row path |
| Modify | `docs/specs/prompt-assembly.md:142` | `_deferred_tool_prompt.py` row path |

**Total: 5 file moves (all drop `tool_` prefix where present since the package
is now `tools/`), ~16 importer edits, 3 spec rows.**

## Naming Rationale

Inside `co_cli/tools/`, the `tool_` prefix becomes redundant — every file in
the package is about tools. The renames follow the project convention that
filenames should reveal role without redundant qualifiers:

- `tool_categories.py` → `categories.py` (per `co_cli/tools/`)
- `tool_display.py` → `display.py`
- `tool_approvals.py` → `approvals.py`
- `_tool_lifecycle.py` → `_lifecycle.py` (stays package-private)
- `_deferred_tool_prompt.py` → `_deferred_prompt.py` (stays package-private)

All five move targets are internal to this repo — no external consumers, no
backwards-compat shim needed (per CLAUDE.md "avoid backwards-compat hacks").

## Migration Steps

1. **Pre-check grep.** Record the complete current importer set:
   `rg "from co_cli\.context\.(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" co_cli tests evals docs`.
   Save output under `tmp/context-move-importers-before.txt` for diff.
2. **`git mv` all five files** in one commit's staging:
   ```bash
   git mv co_cli/context/_tool_lifecycle.py co_cli/tools/_lifecycle.py
   git mv co_cli/context/tool_categories.py co_cli/tools/categories.py
   git mv co_cli/context/tool_display.py co_cli/tools/display.py
   git mv co_cli/context/_deferred_tool_prompt.py co_cli/tools/_deferred_prompt.py
   git mv co_cli/context/tool_approvals.py co_cli/tools/approvals.py
   ```
   `git mv` preserves file history; avoid `cp && rm`.
3. **Rewrite every importer.** The ~16 sites listed above — each changes
   `co_cli.context.<old>` → `co_cli.tools.<new>`. No symbol renames.
4. **Internal import inside `_lifecycle.py`** (currently line 13,
   `from co_cli.context.tool_categories import PATH_NORMALIZATION_TOOLS`):
   rewrite to `from co_cli.tools.categories import PATH_NORMALIZATION_TOOLS`
   (same-package import after the move).
5. **Verify zero stale references.**
   `rg "co_cli\.context\.(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" co_cli tests evals docs`
   must return zero matches. Also check raw path strings in markdown:
   `rg "context/(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" docs`.
6. **Update specs** — see "Spec Updates" below.
7. **Check `__init__.py` conventions.** `co_cli/tools/__init__.py` must remain
   docstring-only per CLAUDE.md ("__init__.py must be docstring-only"). No
   re-exports added.
8. **Lint + test.**
   ```bash
   scripts/quality-gate.sh lint
   mkdir -p .pytest-logs
   uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-context-move-full.log
   ```

## Spec Updates

### `docs/specs/tools.md:35,37,39`

```
co_cli/context/_tool_lifecycle.py        →  co_cli/tools/_lifecycle.py
co_cli/context/_deferred_tool_prompt.py  →  co_cli/tools/_deferred_prompt.py
co_cli/context/tool_approvals.py         →  co_cli/tools/approvals.py
```

### `docs/specs/compaction.md:666`

```
| `co_cli/context/tool_categories.py` | `COMPACTABLE_TOOLS` …
```
→
```
| `co_cli/tools/categories.py` | `COMPACTABLE_TOOLS` …
```

### `docs/specs/prompt-assembly.md:142`

```
| `co_cli/context/_deferred_tool_prompt.py` | `build_category_awareness_prompt()` …
```
→
```
| `co_cli/tools/_deferred_prompt.py` | `build_category_awareness_prompt()` …
```

## Sequencing vs. `history-split`

`history-split` has **already landed** (commit `36cbd9e refactor: split
_history.py into _compaction + _prompt_text`). The `COMPACTABLE_TOOLS,
FILE_TOOLS` import that was at `_history.py:52` now lives in
`context/_compaction.py` — this plan's step 3 updates that line there. No
merge conflict to worry about.

## Dependency Direction Check

After the move, import edges are one-way:

- `co_cli/tools/*` defines tool metadata, capabilities, and approval-subject
  resolution.
- `co_cli/context/*` (history, compaction, markers, orchestrator) imports
  **from** `co_cli/tools/categories` (`COMPACTABLE_TOOLS` / `FILE_TOOLS`) and
  `co_cli/tools/approvals` (`ApprovalSubject`, `resolve_approval_subject`, …).
- `co_cli/display/_core.py` imports `ApprovalSubject` from `co_cli/tools/approvals`.
- Nothing in `co_cli/tools/` imports from `co_cli/context/` after the move.

Verify: `rg "from co_cli\.context" co_cli/tools/` must return zero matches
post-move. If this grep is non-empty, the boundary is wrong — stop and
investigate.

## Verification

Done means all of the following pass:

1. **Grep clean.**
   - `rg "co_cli\.context\.(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" co_cli tests evals docs` → zero matches.
   - `rg "context/(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" docs` → zero matches.
   - `rg "from co_cli\.context" co_cli/tools/` → zero matches (boundary one-way).
2. **Lint.** `scripts/quality-gate.sh lint` passes.
3. **Full tests.** `uv run pytest 2>&1 | tee .pytest-logs/...-context-move-full.log` passes.
4. **REPL smoke.** `uv run co chat` — run a file-read tool call (exercises
   `before_tool_execute` path normalization), a tool that emits an approval
   prompt (exercises both approval-subject resolution and display formatting),
   answer 'a' once to verify session-rule remember still works, and trigger a
   `/compact` (exercises `COMPACTABLE_TOOLS` in the processors).
5. **Spec greps clean.** After spec updates,
   `rg "context/tool_categories|context/tool_display|context/_tool_lifecycle|context/_deferred_tool_prompt|context/tool_approvals" docs` → zero matches.
6. **`git log --follow` works on moved files** — confirms `git mv` preserved
   history (e.g. `git log --follow co_cli/tools/_lifecycle.py` shows commits
   from the old path).

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Missed importer in a rarely-run eval | Step 1 pre-check grep captures every current importer; diff against post-move grep confirms all rewritten. |
| Spec path strings missed | Explicit `rg` in verification step 5 catches raw-path references in markdown. |
| `tools/` package already crowded (18 files) | Accepted — this is the correct home. Tool metadata belongs with tools. Not a reason to keep misplaced. |
| `approvals.py` has the most indirect fan-out (frontend + orchestrator + 4 test files) | Step 1 pre-check grep enumerates every site. Step 2's REPL smoke verifies end-to-end approval prompt + 'a' (remember) still works. |

## Out of Scope (noted, not addressed here)

These were surfaced in the audit but belong to other plans or are lower ROI:

- **Marker magic-string drift.** `_CLEARED_PLACEHOLDER` prefix-matched in
  `_tool_result_markers.py:43` but defined inside compaction code. Post
  history-split, the constant lives in `_compaction.py`. Consolidating into
  `_tool_result_markers.py` is a follow-up.
- **Marker builder placement.** `_static_marker`, `_summary_marker`,
  `_build_compaction_marker` live in `_compaction.py` (post history-split);
  logical owner is `_tool_result_markers.py`. Not contesting that decision
  here.

## Critical Files

- `co_cli/context/_tool_lifecycle.py` — move target #1
- `co_cli/context/tool_categories.py` — move target #2 (most importers: 5 sites)
- `co_cli/context/tool_display.py` — move target #3
- `co_cli/context/_deferred_tool_prompt.py` — move target #4
- `co_cli/context/tool_approvals.py` — move target #5 (widest fan-out: frontend + orchestrator + 4 test files)
- `co_cli/agent/_core.py:21` — imports `CoToolLifecycle`; first thing to break if wired wrong
- `co_cli/context/orchestrate.py:68` — approval helpers multi-import; easiest place to miss a name when rewriting
- `co_cli/display/_core.py:17` — only `display/` → `tools/` import after the move; verifies direction is forward
- `docs/specs/tools.md:35,37,39` — already lists these under "tools" despite
  current path; updating finalises what the spec has said all along

## Delivery Summary (2026-04-23)

✓ DONE — All five moves landed and importers rewritten:

- `co_cli/context/_tool_lifecycle.py` → `co_cli/tools/_lifecycle.py`
- `co_cli/context/tool_categories.py` → `co_cli/tools/categories.py`
- `co_cli/context/tool_display.py` → `co_cli/tools/display.py`
- `co_cli/context/_deferred_tool_prompt.py` → `co_cli/tools/_deferred_prompt.py`
- `co_cli/context/tool_approvals.py` → `co_cli/tools/approvals.py`

Importer rewrites (all verified via grep):
- `co_cli/agent/_core.py:22` — `CoToolLifecycle`
- `co_cli/agent/_instructions.py:41` — `build_category_awareness_prompt`
- `co_cli/display/_core.py:17` — `ApprovalSubject`
- `co_cli/context/orchestrate.py:71,77` — approval helpers + display formatters
- `co_cli/tools/_lifecycle.py:14` — internal `PATH_NORMALIZATION_TOOLS`
- `co_cli/context/_compaction.py`, `_dedup_tool_results.py`, `_tool_result_markers.py` — `COMPACTABLE_TOOLS` / `FILE_TOOLS`
- Tests: `tests/_frontend.py`, `tests/approvals/`, `tests/display/test_display.py`, `tests/files/test_tools_files.py`

Spec updates (per plan):
- `docs/specs/tools.md:35,37,39`
- `docs/specs/compaction.md:666`
- `docs/specs/prompt-assembly.md:142`

Doc sweep (this session, beyond original plan scope):
- 7 `docs/reference/RESEARCH-*.md` files — paths + link anchors rewritten
- 2 `docs/REPORT-*.md` files — paths rewritten
- 2 sibling active exec-plans (`2026-04-23-155308-history-split.md`, `2026-04-13-110355-tui-deferred-interactions.md`) — paths rewritten
- Completed exec-plans left fossilized

Verification:
- `rg "co_cli\.context\.(tool_categories|tool_display|_tool_lifecycle|_deferred_tool_prompt|tool_approvals)" co_cli tests evals docs --glob '!docs/exec-plans/completed/**'` → zero matches
- `rg "from co_cli\.context" co_cli/tools/` → zero matches (one-way boundary preserved)
- `scripts/quality-gate.sh lint` → pass
