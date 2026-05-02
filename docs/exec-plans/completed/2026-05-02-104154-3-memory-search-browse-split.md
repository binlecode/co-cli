# Plan: Clean up memory_search docstring — remove two-mode framing

**Task type:** docstring cleanup

**Status:** ready to execute.

## Problem

`memory_search` advertises "TWO MODES" in its docstring — search mode vs. empty-query browse
mode. This trains the LLM to pass empty queries deliberately to trigger browse, which is a
hidden coupling. The empty-query path is an internal fallback, not a published interface.

## Outcome

`memory_search` docstring describes one behavior: keyword search across artifacts, sessions,
and canon. The empty-query fallback remains as an internal safety net (`_browse_recent` stays
private) but is not advertised. The LLM picks this tool because it wants to search, not
because it knows about an empty-query shortcut.

## Scope

**In scope:**
- `co_cli/tools/memory/recall.py` — rewrite the `memory_search` docstring: drop the
  "TWO MODES" block, drop the empty-query examples, keep the kind taxonomy, search
  syntax, result field docs, and proactive-use guidance.

**Out of scope:**
- Any change to `_browse_recent`, `_list_artifacts`, or the empty-query branch logic —
  they stay as-is as internal utilities.
- New tools, behavioral evals, spec updates.

## Implementation

1. ✓ DONE Rewrite `memory_search` docstring: single-behavior description, no empty-query mention.
2. ✓ DONE Run `scripts/quality-gate.sh lint` to verify.

## Dependencies

None.

## Delivery Summary

- `co_cli/tools/memory/recall.py`: rewrote `memory_search` docstring — removed "TWO MODES" block
  and empty-query examples; single-behavior description retained with kind taxonomy,
  proactive-use guidance, and result field docs intact.
- `tests/test_flow_memory_write.py`: removed dead `try/finally: pass` wrapper from
  `test_save_artifact_url_keyed_dedup_updates_existing`.
- 112 tests passed. v0.8.93.
