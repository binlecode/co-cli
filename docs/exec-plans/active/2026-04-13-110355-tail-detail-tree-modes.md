# `co tail` Detail and Tree Modes

**Slug:** `tail-detail-tree-modes`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Source references:
- CLI entry point: `co_cli/main.py` — `tail()` command at line 630
- Tail implementation: `co_cli/observability/tail.py`
- Telemetry exporter: `co_cli/observability/telemetry.py`
- HTML viewer (tree reference): `co_cli/observability/viewer.py` — `build_span_tree()` at line 457

Current-state validation:
- `co tail` exposes `--trace`, `--tools-only`, `--models-only`, `--poll`, `--no-follow`, `--last`, `--verbose` in `co_cli/main.py`.
- The tail implementation is flat: `_format_span_line()` renders one summary line per span; `_print_spans()` groups by `trace_id` separator only; `parent_id` is present in rows but not used.
- `--verbose` expands per-span detail via `_verbose_detail_lines()` — covers agent `final_result`, tool args/result, model system/user/thinking/response.
- The SQLite exporter stores `id`, `parent_id`, `trace_id`, `attributes`, `events` — all data needed for a tree view is present. `parent_id` is indexed at `idx_spans_parent`.
- `build_span_tree()` in `viewer.py` (line 457) reconstructs parent/child relationships from flat rows — reusable as a reference, not imported directly.
- No test file exists for tail rendering today.
- `--tools-only` and `--models-only` are not mutually exclusive at the CLI boundary; `tools_only` silently wins if both are passed.

---

## Problem & Outcome

`co tail` has two display concerns conflated into one surface:
- default flat summary tailing
- a `--verbose` expansion path that is narrowly named and underspecified

The result is a gap between the fast summary view and the full HTML trace viewer. Operators can see span summaries but cannot ask the terminal tail for either a principled expanded detail view or a bounded structural view of the trace.

Failure cost:
- Operators escalate to `co traces` (browser, heavier) for problems that still fit in a terminal.
- `--verbose` is undiscoverable and its contract is implicit — it only expands model spans usefully; agent and tool behavior is secondary.
- Parent/child span relationships stored in OTel are invisible in the tail.
- `--tools-only` + `--models-only` silently resolves to tools-only instead of failing fast.

Outcome: keep the current implementation as the summary default, add:
- `--detail` for expanded per-span payload inspection in the terminal
- `--tree` for a bounded nested trace view in the terminal

Remove `--verbose` outright (project zero-backward-compat policy: no aliases).

The live tail has three clearly separated display modes after this work:
- **summary** — fast scan, low noise (current default, unchanged)
- **detail** — richer payload inspection per span
- **tree** — shallow trace structure, indented by parent/child relationship

---

## Scope

In scope:
- Keep current summary tailing as the default behavior, unchanged
- Add `--detail` as the explicit expanded terminal mode
- Add `--tree` as a bounded nested terminal mode
- Remove `--verbose` (not aliased — hard removal per zero-backward-compat)
- Reject mutually exclusive flag pairs at the CLI boundary
- Add tests for summary/detail/tree formatting and filtering

Out of scope:
- Full HTML trace viewer parity in the terminal
- Arbitrary unlimited-depth tree rendering
- Changing the OTel exporter schema
- Adding a log pipeline separate from spans
- In-flight partial span rendering before span end
- Any terminal UI framework (no curses, no live re-render of past output)

---

## Behavioral Constraints

- Summary mode must remain the default; no flag changes required for current users other than the removal of `--verbose`.
- `--detail` and `--tree` are mutually exclusive at the CLI boundary — passing both must exit with a clear error message.
- `--tools-only` and `--models-only` are mutually exclusive at the CLI boundary — passing both must exit with a clear error message.
- `--verbose` must be removed entirely. Passing `--verbose` must produce an "unknown option" error from the CLI parser — no alias, no deprecation warning.
- `--tree` depth is capped at 3 visible levels. Descendants at depth > 3 are represented by a single elision marker (`… N more`) per parent, not expanded recursively.
- Tree mode, when a filter removes an ancestor from the result set, must fetch the minimal ancestor chain from the DB and render those ancestors as dim context rows. Context-only rows are visually distinct from matching rows. Children must not be silently re-parented under a different visible node.
- Tree mode in follow mode uses append-only output. Each poll cycle fetches newly-arrived spans, groups by `trace_id`, fetches the ancestor chain for each visible span (one query per trace_id in the batch), and renders a mini-tree per trace group. Previously-printed parent rows may re-appear as context ancestors in subsequent poll cycles — this is acceptable for a live tail.
- Detail mode truncates individual output lines at 120 characters. Long JSON payloads are pretty-printed but each line is capped. The cap applies per line, not to the total block.
- Detail mode remains type-aware: agent spans expand `final_result`; tool spans expand args and result; model spans expand system/user input and response/thinking. Unknown span types emit no detail block.
- All existing filters (`--trace`, `--tools-only`, `--models-only`, `--last`, follow mode) must work correctly in both detail and tree modes.
- Tree mode summary lines (the non-detail part of each span row) have no explicit width cap. They reuse `_format_span_line()` summary rendering, which is compact by design. This is intentional — no truncation added to tree mode summary rows.

---

## High-Level Design

### 1. Internal mode enum

Normalize `--detail` / `--tree` / (neither) into a single `TailMode` enum value passed through `run_tail()`. The renderer and query logic branch on one value, not multiple overlapping booleans.

```
TailMode.SUMMARY  — default
TailMode.DETAIL   — --detail flag
TailMode.TREE     — --tree flag
```

`run_tail()` signature changes from `verbose: bool` to `mode: TailMode`. All internal helpers receive `mode` rather than `verbose`.

### 2. Detail mode

Detail mode keeps the summary line and appends a bounded detail block per span. Implementation reuses the existing `_render_agent_detail`, `_render_tool_detail`, `_render_model_detail` helpers (currently called from `_verbose_detail_lines`). Rename `_verbose_detail_lines` → `_detail_lines` to match the new vocabulary. Add 120-char line truncation to each rendered detail line.

### 3. Tree mode

Tree mode groups the current row set by `trace_id`, reconstructs parent/child relationships using `id` and `parent_id`, and renders a bounded indented tree per trace.

When filtering reduces the visible set (e.g., `--tools-only`), the tree builder fetches the full ancestor chain for each visible span using a single `WITH RECURSIVE` CTE query per distinct `trace_id` in the batch — one round-trip per trace, not one per ancestor hop. The CTE walks up via `parent_id` from the matching span IDs to the root. The `IN` list of seed IDs must use `?` placeholders (`f"IN ({','.join(['?'] * len(ids))})"`) — never string-interpolated — matching the existing codebase pattern. Ancestors not in the filtered result set are rendered as dim context rows.

Rendering policy:
- Trace separator row at the top of each trace group
- Each span indented by depth (2 spaces per level), prefixed with `├─` / `└─`
- Children sorted by `start_time or 0` ascending (handles `None` without `TypeError`, matches `viewer.py`)
- Depth capped at 3; beyond cap, emit `… N more` per parent
- Context-only ancestor rows rendered in dim style with a `[ctx]` prefix

Tree mode in follow mode: each poll cycle renders its batch as a standalone tree with ancestor context fetched per `trace_id`. No in-place rewrite of prior output.

### 4. Flag validation

Add explicit guard in `tail()` before calling `run_tail()`:
```python
if detail and tree:
    raise typer.BadParameter("--detail and --tree are mutually exclusive")
if tools_only and models_only:
    raise typer.BadParameter("--tools-only and --models-only are mutually exclusive")
```

Remove `--verbose` / `-v` parameter from the `tail()` command entirely.

---

## Tasks

## TASK-1: Add mode enum, CLI flags, and flag validation

files: `co_cli/main.py`, `co_cli/observability/tail.py`

Implementation:
- Define `TailMode` enum in `tail.py` with values `SUMMARY`, `DETAIL`, `TREE`.
- Add `--detail` and `--tree` boolean flags to `tail()` in `main.py`. Remove `--verbose` / `-v` entirely.
- Normalize flags into `TailMode` and pass as `mode: TailMode` to `run_tail()`.
- Add mutual exclusion guards for `--detail`+`--tree` and `--tools-only`+`--models-only` — fail with `typer.BadParameter`.
- Update `run_tail()` signature: replace `verbose: bool` with `mode: TailMode`.
- Update `_print_spans()` and `_format_span_line()` to accept `mode: TailMode` instead of `verbose: bool`.
- Run `grep -r -- '--verbose' docs/` and remove any hits referring to the removed flag.

done_when: |
  `uv run co tail --detail --tree` exits with a non-zero status and an error message containing
  "mutually exclusive"; `uv run co tail --tools-only --models-only` likewise; `uv run co tail
  --verbose` exits with "No such option" from the CLI parser.
success_signal: one canonical internal tail mode instead of loosely overlapping booleans
prerequisites: []

## TASK-2: Refactor detail rendering under the new mode vocabulary

files: `co_cli/observability/tail.py`

Implementation:
- Rename `_verbose_detail_lines` → `_detail_lines`. Update all call sites.
- Add 120-char per-line truncation in `_vline()` (single enforcement point). Remove the existing `[:120]` inline slice in `_render_model_input_lines` — it becomes redundant once `_vline()` truncates.
- Wire `_detail_lines` into `_format_span_line` under `mode == TailMode.DETAIL` (replacing the `verbose` branch).
- Confirm detail mode still handles agent/tool/model span types correctly; unknown types emit no detail block.

done_when: |
  `uv run pytest tests/test_tail.py -x -k "detail"` passes; tests assert that detail lines appear
  after the summary line for agent/tool/model spans and that no individual line exceeds 120 chars.
success_signal: detail mode is an explicit named path, not an implicit verbose boolean
prerequisites: [TASK-1]

## TASK-3: Add bounded tree rendering to the live tail

files: `co_cli/observability/tail.py`

Implementation:
- Add `_build_tree(rows, conn)` — groups rows by `trace_id`, fetches the full ancestor chain for each distinct `trace_id` in the batch using one `WITH RECURSIVE` CTE query per `trace_id` (single round-trip, not one per ancestor hop). Assembles parent/child from `id`/`parent_id`, marks fetched ancestors not in the original `rows` as context-only. Context-only status is determined by set membership against `rows` — `span_filter` is not re-applied inside this function.
  - The CTE seed uses a parameterized `IN` clause: `f"IN ({','.join(['?'] * len(ids))})"` with IDs passed as query parameters — never string-interpolated.
- Add `_render_tree(console, trees, last_trace_id)` — renders each trace group with a trace separator, indented span rows (2 spaces per depth level, `├─`/`└─` prefixes), and elision markers at depth > 3.
- Context-only ancestors are rendered dim with a `[ctx]` prefix.
- Children are sorted by `start_time or 0` ascending (handles `None` without `TypeError`).
- Plug `_render_tree` into `_print_spans` / the follow-mode loop under `mode == TailMode.TREE`.
- Follow mode: each poll batch calls `_build_tree` independently. Ancestor context rows for a `trace_id` may re-appear across poll cycles.

done_when: |
  `uv run pytest tests/test_tail.py -x -k "tree"` passes; tests assert: children render under
  the correct parent; sibling order follows `start_time`; filtered modes emit dim `[ctx]` ancestor
  rows for matching descendants; depth-4+ descendants produce an elision marker; orphaned spans
  (no parent in DB) render as roots without error.
success_signal: operators can inspect shallow trace structure from the terminal without opening co traces
prerequisites: [TASK-1]

## TASK-4: Add dedicated tail rendering tests

files: `tests/test_tail.py`

Implementation:
- Create `tests/test_tail.py`.
- Use `sqlite3` in-memory DB with the same schema as `telemetry.py` — no fixtures, no mocks.
- Populate rows via direct `INSERT` to control `id`, `parent_id`, `trace_id`, `name`, `start_time`, `attributes`, `status_code`, `duration_ms`.

Coverage required:
- Summary mode: single summary line per span, no detail block appended
- Detail mode: detail lines appear after summary line for agent/tool/model span types; no line exceeds 120 chars
- Tree mode: correct indentation depth; sibling order by `start_time`; `[ctx]` ancestor rows for filtered-out parents; elision marker at depth > 3
- Filtered tree: `--tools-only` with a tool span whose parent is an agent span — agent appears as `[ctx]` row
- `--verbose` flag removed: test that `tail()` CLI invocation with `--verbose` exits non-zero (optional — CLI-layer test)
- Flag exclusion: `detail=True` + `tree=True` raises `typer.BadParameter`; `tools_only=True` + `models_only=True` likewise
- `ERROR` status code renders in all three modes

done_when: |
  `uv run pytest tests/test_tail.py -x` passes with zero failures; all coverage targets above
  have at least one test case; `uv run co tail --detail --tree` exits non-zero confirming
  end-to-end flag wiring through `main.py`.
success_signal: summary/detail/tree rendering semantics are locked by tests and can evolve safely
prerequisites: [TASK-2, TASK-3]

---

## Testing

During implementation, scope to the affected tests first:

```
mkdir -p .pytest-logs && uv run pytest tests/test_tail.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tail-detail-tree.log
```

Before shipping:

```
scripts/quality-gate.sh full
```

Manual validation after implementation:
- Run `co chat` in one terminal, `co tail`, `co tail --detail`, and `co tail --tree` in another against the same live DB.
- Verify summary stays compact, detail expands targeted payloads with no lines exceeding 120 chars, and tree mode remains readable at an 80-column terminal width.
- Confirm `co tail --verbose` is rejected by the CLI parser.
- Confirm `co tail --detail --tree` and `co tail --tools-only --models-only` fail with clear error messages.

---

## Open Questions

None. All open questions from the prior draft have been resolved:

- **Follow mode + tree**: append-only per poll cycle; ancestor context re-fetched per cycle from DB. No in-place re-render.
- **Detail mode width**: 120-char per-line cap with `…` truncation.
- **`--verbose` compatibility**: none — hard removal per zero-backward-compat policy.
- **Tree + detail combination**: not supported in v1; both modes are mutually exclusive. Tree remains structure-first.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tail-detail-tree-modes`
