# Plan: file_read Safety Caps

Task type: code-feature

## Context

`file_read` is registered with `max_result_size=math.inf` — no emit-time cap — and has no
in-tool bounds on lines returned, per-line length, or file size. The model must voluntarily
paginate via `start_line`/`end_line`; if it doesn't, an entire large file enters context at
full size with no backstop.

Hermes comparison (from `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md`) shows four
layered constraints in its `read_file` tool:
1. `MAX_LINES = 2000` — hard ceiling per read (clamped via `limit` parameter)
2. `MAX_LINE_LENGTH = 2000` — per-line char truncation
3. `MAX_FILE_SIZE = 50 KB` — file size check (dead code in hermes but the intent is sound)
4. `limit=500` default — default lines per call when no range is requested

No existing plan or prior work addresses this. No spec files are touched — `sync-doc` handles
those post-delivery.

## Problem & Outcome

**Problem:** `file_read` has no in-tool size limits. A single unpaginated call on a large file
loads the full content into memory, produces a result potentially hundreds of KB in size, and
injects it into context with no backstop.

**Failure cost:** Context budget can be blown by a single tool call. M1 (emit-time cap) does
not trigger because `max_result_size=math.inf` is intentional (recursion safeguard — see
Behavioral Constraint 6). M2a eventually clears old results but does nothing to prevent the
initial blowup.

**Outcome:** Four in-tool safety caps enforced within `file_read`:
1. Default pagination: return max 500 lines when no range is given; emit continuation hint if
   more lines exist.
2. Hard line ceiling: cap any explicit range at 2000 lines; emit truncation note if capped.
3. Per-line truncation: lines exceeding 2000 chars are cut with `...[truncated]` marker.
4. File size gate: files larger than 500 KB block a full-file read (no `start_line`/`end_line`)
   with an error instructing the caller to use `start_line`/`end_line` to keep the result
   within context budget. Explicit ranges still proceed (file is still loaded fully — this gate
   protects context budget only, not process memory).

`max_result_size` remains `math.inf` — the in-tool caps are the primary defence.

## Scope

**In:** `co_cli/tools/files/read.py` (implementation + tests in same task).

**Out:** No config settings added — constants are hardcoded (same approach as hermes). No
changes to `file_grep`, `file_glob`, or write tools. No line-by-line file reading
optimisation (file is still loaded fully when `start_line`/`end_line` are given).
`max_result_size` is not changed.

## Behavioral Constraints

1. Small files (≤ `_READ_DEFAULT_LIMIT` lines) must be returned in full — no pagination
   imposed when unnecessary.
2. Continuation hint format must be identical to the existing hint so the model can chain
   calls: `[N more lines — use start_line=X to continue reading]`.
3. `file_partial_reads` tracking must be set correctly after any capped read:
   - A default-capped read (no range given, file exceeds `_READ_DEFAULT_LIMIT`) is treated as
     partial — `file_partial_reads.add(path_key)` — which blocks `file_patch`.
   - A full read (file ≤ `_READ_DEFAULT_LIMIT`) discards the partial flag.
   - The `file_partial_reads` write is deferred until after all slice arithmetic is resolved.
4. `file_read_mtimes` recording must be unaffected.
5. Binary file and workspace-escape errors must remain unchanged.
6. `max_result_size` remains `math.inf`. This is an intentional recursion safeguard:
   lowering it would cause persist→read_file→persist cycles. The in-tool line and per-line
   caps are the primary size defence.
7. `end_line < start_line` produces an empty slice — no error. Model is expected to correct
   its arguments.

## High-Level Design

### Constants (module-level in `read.py`)

```
_READ_DEFAULT_LIMIT  = 500      # lines returned when no range given; matches hermes limit=500
_READ_MAX_LINES      = 2000     # hard ceiling on any single read
_READ_MAX_LINE_CHARS = 2000     # per-line truncation threshold
_READ_MAX_FILE_BYTES = 500_000  # 500 KB — full-file read blocked above this (context budget)
```

### `file_read` logic changes

```
1. stat() the resolved path → file_bytes = resolved.stat().st_size
2. If file_bytes > _READ_MAX_FILE_BYTES AND start_line is None AND end_line is None:
       return tool_error(
           f"File too large for full-file read ({file_bytes // 1024} KB). "
           "Use start_line/end_line to keep this result within context budget."
       )
3. Read file (unchanged: encoding detection, binary guard, mtime recording)
4. Compute slice — ALL arithmetic first, then write flags:
   a. If start_line is None AND end_line is None (no range):
          lo = 0
          hi = min(total_line_count, _READ_DEFAULT_LIMIT)
          is_partial = (hi < total_line_count)
   b. If start_line or end_line given (explicit range):
          lo = (start_line - 1) if start_line is not None else 0
          requested_hi = end_line if end_line is not None else total_line_count
          hi = min(requested_hi, lo + _READ_MAX_LINES)
          is_partial = True   (caller is explicitly ranging — treat as partial)
   c. sliced = all_lines[lo:hi]
   d. Write file_partial_reads AFTER hi is resolved:
          if is_partial: file_partial_reads.add(path_key)
          else:          file_partial_reads.discard(path_key)
5. Build display with per-line truncation:
       for each line in sliced:
           if len(line.rstrip('\n')) > _READ_MAX_LINE_CHARS:
               line = line[:_READ_MAX_LINE_CHARS] + "...[truncated]\n"
       apply cat-n numbering (unchanged)
6. Continuation hint: show when hi < total_line_count
       (replaces existing narrower condition that only fired when end_line was given)
```

### `@agent_tool` decorator

No change — `max_result_size=math.inf` is preserved (Behavioral Constraint 6).

## Implementation Plan

### TASK-1 — Implement safety caps and tests ✓ DONE

**files:**
- `co_cli/tools/files/read.py`
- `tests/test_tools_files.py`

**done_when:** `uv run pytest tests/test_tools_files.py -x` passes including four new tests:
- `test_read_file_default_limit` — 600-line file, no range → 500 lines returned +
  continuation hint in `return_value`; `result.metadata["error"]` is falsy
- `test_read_file_hard_ceiling` — two sub-cases:
  (a) `start_line=1, end_line=3000` on a 3000-line file → ≤ 2000 lines in output
  (b) `start_line=1, end_line=None` on a 3000-line file → ≤ 2000 lines in output
- `test_read_file_line_truncation` — file with one line of 3000 chars → `"...[truncated]"`
  appears in `return_value`
- `test_read_file_size_gate` — file > 500 KB, no range → `result.metadata["error"] is True`
  AND `"start_line"` in `result.return_value`

**success_signal:** Reading a 600-line file with no `start_line`/`end_line` returns 500 lines
with a continuation hint; reading a >500 KB file with no range returns an error with
`start_line` in the message; long lines are truncated inline.

## Testing

Run: `uv run pytest tests/test_tools_files.py -x`

All existing `file_read` tests must continue to pass unchanged (small-file behavior is
unaffected). New tests cover the four failure modes. No mocks — tests use `tmp_path` and
real file content.

## Open Questions

None — all design decisions resolved by inspection of source and hermes reference.

---

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1   | adopt    | Recursion safeguard is a real invariant with existing tests; in-tool caps are sufficient | Dropped decorator change; Behavioral Constraint 6 updated; "Additionally…" removed from Problem & Outcome; Scope "Out" updated |
| CD-M-2   | adopt    | Sequencing ambiguity is real; partial-read semantics for default-cap reads must be explicit | Pseudocode step 4 rewritten with explicit ordering; Behavioral Constraint 3 expanded |
| CD-M-3   | adopt    | TASK-1 `done_when` referencing TASK-2 tests is not executable; merged into single task | TASK-1 and TASK-2 collapsed into TASK-1 |
| PO-M-1   | adopt    | Gate protects context budget, not memory — must be stated explicitly to prevent misleading callers and future devs | Gate error message updated; Outcome and Scope Out updated with clarification |
| PO-M-2   | adopt    | 500 matches hermes peer default and avoids spurious truncation on typical source files | `_READ_DEFAULT_LIMIT` changed from 200 → 500; test cases updated accordingly |
| PO-m-1   | adopt    | Subsumed by CD-M-3 merge | — |
| PO-m-2   | adopt    | Subsumed by CD-M-2 expansion of Behavioral Constraint 3 | — |
| CD-m-1   | adopt    | `start_line`-only case is a real uncovered path | Added sub-case (b) to `test_read_file_hard_ceiling` in TASK-1 |
| CD-m-2   | adopt    | Prevents future implementer confusion | Added Behavioral Constraint 7 |
| CD-m-3   | adopt    | Both assertions needed to match behavioral guarantee | `done_when` for size gate now requires both `error is True` and `"start_line" in return_value` |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev file-read-safety-caps`
