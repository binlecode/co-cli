# TODO: `co tail` Detail and Tree Modes

**Slug:** `tail-detail-tree-modes`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-otel-compare-fork-cc-and-co.md](reference/RESEARCH-otel-compare-fork-cc-and-co.md), [DESIGN-observability.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-observability.md) §Live Tail Viewer

Current-state validation against the latest code:
- `co tail` currently exposes `--trace`, `--tools-only`, `--models-only`, `--poll`, `--no-follow`, `--last`, and `--verbose` in [co_cli/main.py](/Users/binle/workspace_genai/co-cli/co_cli/main.py#L377).
- The tail path in [co_cli/observability/_tail.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_tail.py) is intentionally flat:
  - `_format_span_line()` renders one summary line per completed span
  - `_print_spans()` groups only by `trace_id` separator
  - `parent_id` is not used for nesting or indentation
- Current verbose mode is narrow, not a general detail mode. `_verbose_detail_lines()` in [co_cli/observability/_tail.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_tail.py) expands:
  - agent `final_result`
  - tool args/result
  - model system line, last user message, thinking, and response
- The SQLite exporter already stores the data needed for a tree view in [co_cli/observability/_telemetry.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_telemetry.py):
  - `trace_id`
  - `parent_id`
  - JSON `attributes`
  - JSON `events`
- The HTML trace viewer already proves tree assembly is possible. [build_span_tree() in co_cli/observability/_viewer.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_viewer.py#L370) reconstructs parent/child relationships from flat rows.
- There is no dedicated test file for tail rendering today.

Artifact hygiene:
- No current TODO owns terminal tail rendering modes.
- This task is specifically for the live terminal tail. It does not replace or redesign `co traces`.

---

## Problem & Outcome

Problem: `co tail` currently mixes two concerns into one surface:
- default live summary tailing
- a narrow `--verbose` expansion path

That leaves a gap between the fast summary view and the full HTML trace viewer. Users can see recent span summaries, but they cannot ask the terminal tail for either:
- a principled expanded detail mode
- a bounded nested tree view

Failure cost:
- operators have to jump to `co traces` too early for problems that still fit in a terminal
- `--verbose` is overloaded and underspecified
- parent/child span relationships already stored in OTel are invisible in the tail

Outcome: keep the current implementation as the summary default, add:
- `--detail` for expanded per-span detail output
- `--tree` for a bounded nested tail view

The live tail should then have three clearly separated display modes:
- summary: fast scan, low noise
- detail: richer payload inspection in the terminal
- tree: shallow trace structure in the terminal

---

## Scope

In scope:
- keep current summary tailing as the default behavior
- add `--detail` as the explicit expanded terminal mode
- add `--tree` as a bounded nested terminal mode
- decide the compatibility story for existing `--verbose`
- add tests for summary/detail/tree formatting and filtering behavior

Out of scope:
- full HTML trace viewer parity in the terminal
- arbitrary unlimited-depth tree rendering
- changing the OTel exporter schema
- adding a log pipeline separate from spans
- in-flight partial span rendering before span end

---

## Behavioral Constraints

- Summary mode must remain the default with no flag changes required for current users.
- `--detail` and `--tree` must be mutually exclusive in the CLI.
- `--tools-only` and `--models-only` must also be mutually exclusive in the CLI.
- `--tree` should stay shallow and scan-friendly:
  - recommended max visible depth: 3 levels
  - deeper descendants should be collapsed, elided, or summarized rather than dumped recursively without bound
- Tree mode must still honor existing filters:
  - `--trace`
  - `--tools-only`
  - `--models-only`
  - `--last`
  - follow mode
- Tree filtering must preserve real lineage:
  - do not silently re-parent visible children when a filtered-out ancestor is missing
  - include the minimal ancestor chain as dim context rows or equivalent structural placeholders
  - clearly mark non-matching ancestors as context, not as matching rows
- This task must not turn `co tail` into a second `co traces`. The terminal output should stay optimized for live reading.
- Detail mode should expand targeted fields, not blindly dump every attribute and event for every span.
- If `--verbose` is retained for compatibility, it should become a compatibility alias with explicit semantics rather than an overlapping second concept.

---

## High-Level Design

### 1. Promote tail rendering to an internal mode enum

User-facing flags can remain simple:
- default summary
- `--detail`
- `--tree`

But the implementation should normalize them into one internal tail-view mode so the renderer and query logic do not branch on multiple booleans everywhere.

Suggested internal modes:
- `summary`
- `detail`
- `tree`

### 2. Define `--detail` as expanded payload inspection

`--detail` should subsume the useful parts of the current `--verbose` behavior while making the contract clearer:
- summary line still prints first
- detail block follows with selected per-span payloads
- formatting stays type-aware and bounded

Recommended detail behavior:
- agent spans: final result plus a few key run attributes
- model spans: current system/user/output detail, as today
- tool spans: pretty-printed args/result plus selected error/status context

This is an expanded detail mode, not a raw attribute dump.

### 3. Define `--tree` as a bounded nested live tail

Tree mode should use `parent_id` and `trace_id` to show shallow causal structure in the terminal:
- root/trace boundary
- immediate children
- high-value grandchildren

When filters remove some nodes:
- preserve the real parent/child lineage
- include the minimal hidden ancestor chain as structural context
- never promote a matching child to a fake root or silently re-parent it under the next visible node

Recommended terminal policy:
- indent by depth with compact prefixes
- print child spans in start-time order
- cap visible depth at 3
- when deeper descendants exist, emit a short elision marker rather than full recursion

That gives structural visibility without trying to replicate the HTML viewer's full recursive inspector.

### 4. Migrate `--verbose` cleanly

The current `--verbose` name is not ideal once there are multiple richer modes.

Recommended compatibility plan:
- add `--detail`
- keep `--verbose` temporarily as an alias for `--detail`
- reject `--verbose` combined with `--tree`
- document `--detail` as the preferred flag

That avoids a breaking UX change while giving the CLI a better long-term vocabulary.

### 5. Reject conflicting exclusive filters

The current flag set uses `--tools-only` and `--models-only` vocabulary. Best-practice behavior for v1 is to reject that combination at the CLI boundary rather than silently picking one side.

Recommended behavior:
- reject `--tools-only` combined with `--models-only`
- reject `--tree` combined with `--detail`
- reject `--tree` combined with `--verbose` if `--verbose` remains as a compatibility alias

This keeps the CLI honest. If a future version needs multi-select filtering, it should use a different flag shape such as repeated `--type` values rather than overloading `*only` flags with union semantics.

---

## Implementation Plan

## TASK-1: Add explicit tail view modes and CLI flag normalization

files: `co_cli/main.py`, `co_cli/observability/_tail.py`

Implementation:
- Add user-facing `--detail` and `--tree` flags to `co tail`.
- Normalize flags into one internal mode passed to `run_tail()`.
- Keep summary as the default when neither flag is present.
- Reject conflicting filter combinations at the CLI boundary:
  - `--detail` + `--tree`
  - `--tools-only` + `--models-only`
- Decide and implement the compatibility behavior for `--verbose`:
  - preferred: alias to detail
  - reject conflicting combinations

done_when: |
  co tail has explicit summary/detail/tree semantics at the CLI layer;
  invalid flag combinations fail fast with clear messaging
success_signal: there is one canonical internal tail mode rather than loosely overlapping booleans
prerequisites: []

## TASK-2: Refactor summary/detail rendering into explicit mode-specific formatters

files: `co_cli/observability/_tail.py`, `tests/test_tail.py`

Implementation:
- Keep the current summary format as the `summary` renderer.
- Rename or reshape the current verbose helpers into detail-mode helpers.
- Ensure detail mode remains type-aware and bounded.
- Add a small formatting seam so tests can assert rendered lines without requiring a live DB poll loop.

Coverage targets:
- summary mode keeps current compact single-line behavior
- detail mode appends extra lines for model/tool/agent spans
- detail mode remains readable for long JSON payloads

done_when: |
  summary and detail rendering are explicit code paths with stable tests;
  detail mode is no longer conceptually tied to a vague verbose boolean
success_signal: the terminal tail has a clear expanded mode that is more principled than the current --verbose path
prerequisites: [TASK-1]

## TASK-3: Add bounded tree rendering to the live tail

files: `co_cli/observability/_tail.py`, `tests/test_tail.py`

Implementation:
- Reconstruct parent/child relationships from `id`, `trace_id`, and `parent_id` for the currently displayed row set.
- When the visible row set is insufficient to preserve lineage, fetch or synthesize the minimal ancestor chain needed for correct tree structure.
- Render a bounded tree in terminal-friendly form:
  - trace separator
  - indented span rows
  - capped depth
  - elision marker for deeper descendants
- Reuse tree-building ideas from [co_cli/observability/_viewer.py](/Users/binle/workspace_genai/co-cli/co_cli/observability/_viewer.py#L370) without importing full HTML rendering concerns.
- Keep filtering behavior coherent when only a subset of spans is shown:
  - preserve ancestor context for matching descendants
  - do not silently re-parent descendants under a different visible node
  - make context-only ancestor rows visually distinct from matching rows

Coverage targets:
- children render under the right parent
- sibling ordering follows `start_time`
- filtered modes preserve lineage via context ancestors instead of fake roots
- filtered modes do not produce broken indentation or orphaned garbage rows
- deeper-than-cap trees elide cleanly

done_when: |
  co tail --tree shows shallow parent/child structure in the terminal without HTML-viewer complexity;
  filters and follow mode still behave correctly
success_signal: operators can inspect trace structure from the terminal before escalating to co traces
prerequisites: [TASK-1]

## TASK-4: Add dedicated tail rendering tests

files: `tests/test_tail.py`

Implementation:
- Add a new focused test file for tail formatting and mode selection.
- Prefer pure formatting/query-shape tests over polling-loop integration for most coverage.
- Use realistic sqlite rows or row-like fixtures derived from actual span fields.

Coverage must include:
- default summary mode
- `--detail` expanded output
- `--tree` bounded indentation
- `--verbose` compatibility behavior
- mutually exclusive flag rejection
- `--tools-only` + `--models-only` rejection
- filtered tree mode retains ancestor context for matching descendants
- `ERROR` status retention across all modes

done_when: |
  tail rendering has direct automated coverage instead of being exercised only manually;
  the new flags and compatibility behavior are locked by tests
success_signal: summary/detail/tree output semantics can evolve safely without regressions
prerequisites: [TASK-2, TASK-3]

---

## Testing

During implementation, scope to the affected tests first:

- `mkdir -p .pytest-logs && uv run pytest tests/test_tail.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tail-detail-tree.log`

Before shipping:

- `scripts/quality-gate.sh full`

Manual validation after implementation:
- run `co chat` in one terminal and `co tail`, `co tail --detail`, and `co tail --tree` in another against the same live DB
- verify that summary stays compact, detail expands targeted payloads, and tree mode remains readable on ordinary terminal widths

---

## Open Questions

- Whether `--tree` should imply detail lines for leaf spans, or remain structure-only unless combined with future per-node expansion controls. Recommended v1: tree should remain structure-first and not automatically inherit full detail blocks.
