# TODO: Startup banner — rename Knowledge to Memory, add knowledge/session counts

Task type: ux

## Context

Current startup banner has a single `Knowledge:` line derived from the active backend (`hybrid`, `fts5`, or `grep`) plus degradation text. The label is infrastructure-centric rather than user-centric.

Inline current-state validation:
- [co_cli/bootstrap/banner.py](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/banner.py#L61) renders `Knowledge:` from `deps.config.knowledge.search_backend` and `deps.degradations.get("knowledge")`.
- [co_cli/bootstrap/core.py](/Users/binle/workspace_genai/co-cli/co_cli/bootstrap/core.py) — `_sync_memory_store()` returns the knowledge item count; `init_session_index()` syncs past sessions into the store. Both run before the banner is displayed.
- The `MemoryStore` has two user-relevant sources: `knowledge` (personal artifacts in `~/.co-cli/knowledge/`) and `session` (past session transcripts). Obsidian, Drive, and web search are tool-shaped, not knowledge-shaped — they are not pre-loaded at startup and do not belong in the banner.

## Problem & Outcome

**Problem:** `Knowledge: fts5` is backend jargon. It hides the two things users actually care about: how many knowledge artifacts are indexed, and how many past sessions are available for recall.

**Outcome:** replace the `Knowledge:` line with a `Memory:` line that shows the backend and item counts for both sources:

```text
Memory: fts5  knowledge: 42  sessions: 8
```

With degradation:

```text
Memory: fts5  (hybrid → fts5 ...)  knowledge: 42  sessions: 8
```

When the store is unavailable (grep backend):

```text
Memory: grep (no index)
```

## Scope

In scope:
- `co_cli/bootstrap/banner.py`
- threading knowledge count and session count from bootstrap into the banner
- tests covering the new Memory row rendering

Out of scope:
- changing memory or library retrieval behavior
- external knowledge sources (Obsidian, Drive, web search) — tool-shaped, not knowledge-shaped
- redesigning `/status` output
- adding new startup network checks

## Behavioral Constraints

- Do not add new startup IO. Counts must come from data already available at banner time. A lightweight `SELECT COUNT(*)` from the already-open `MemoryStore` database is acceptable — it is not file I/O or a network call.
- Do not scan files just to show counts — use what bootstrap already produces.
- Keep the panel narrow enough to fit the current banner shape in standard terminal widths.
- When the store is None (grep backend), omit counts entirely — there is nothing to count.

## High-Level Design

### Before

```text
Model: ollama-openai / qwen3.5:35b-a3b-think
Knowledge: fts5  (hybrid -> fts5 ...)
Tools: 30  Skills: 1  MCP: 1  Commands: 16
```

### After

```text
Model: ollama-openai / qwen3.5:35b-a3b-think
Memory: fts5  (hybrid → fts5 ...)  knowledge: 42  sessions: 8
Tools: 30  Skills: 1  MCP: 1  Commands: 16
```

## Implementation Plan

### ✓ DONE TASK-1 — Thread counts into the banner and replace the Knowledge row

Bootstrap already has the knowledge item count from `_sync_memory_store()` (return value of `store.sync_dir("knowledge", ...)`) and session count is queryable from the store after `init_session_index()` runs. Thread these into `display_welcome_banner()` and replace the `Knowledge:` line with `Memory:`.

Steps:
1. Add `knowledge_count: int` and `session_count: int` parameters to `display_welcome_banner()` — or pass them via a small `MemoryStats` dataclass if cleaner.
2. Add `count_docs(source: str) -> int` to `MemoryStore` (`SELECT COUNT(*) FROM docs WHERE source = ?`). `MemoryStore` has no existing count method — `list_titles_by_source` returns a full set and is wasteful for a count. Use `count_docs` for both sources.
3. Knowledge count: already returned by `_sync_memory_store()` (return value of `store.sync_dir("knowledge", ...)`). Pass it through directly — no second query needed.
4. Session count: call `store.count_docs("session")` after `init_session_index()` runs. This counts distinct indexed session documents (rows in `docs` where `source='session'`), not chunks — matching the `sessions: N` user-visible label.
5. Build the `Memory:` line: backend label + optional degradation suffix + counts (omit counts when store is None).
6. Remove the `Knowledge:` line. No other banner rows change.

files:
- `co_cli/bootstrap/banner.py`
- `co_cli/bootstrap/core.py` or call site that invokes `display_welcome_banner()`
- `co_cli/memory/memory_store.py` — add `count_docs(source: str) -> int`

done_when: |
  grep -n 'Knowledge:' co_cli/bootstrap/banner.py returns no banner-row hit;
  grep -n 'Memory:' co_cli/bootstrap/banner.py returns the new memory row
success_signal: banner shows Memory: with backend + knowledge count + session count
prerequisites: []

### ✓ DONE TASK-2 — Add banner rendering tests for the Memory row

Add focused tests for the new Memory row. Test a pure helper or a list-of-lines builder rather than snapshotting Rich panel output.

Coverage must include:
- indexed backend (fts5 or hybrid) with non-zero knowledge and session counts
- degradation suffix present alongside counts
- grep backend — no counts shown
- zero counts (empty knowledge dir, no past sessions)

files:
- `tests/test_flow_bootstrap_banner.py` (new) or nearest existing bootstrap test file

done_when: |
  banner tests assert Memory row output under indexed and degraded scenarios;
  uv run pytest <affected file> -x passes
success_signal: Memory row semantics are locked by tests
prerequisites: [TASK-1]

## Testing

```
mkdir -p .pytest-logs && uv run pytest tests/test_flow_bootstrap_banner.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-banner-memory-row.log
```

Before shipping:

```
scripts/quality-gate.sh types
```

## Delivery Summary — 2026-05-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | no `Knowledge:` banner-row hit; `Memory:` row present in banner.py | ✓ pass |
| TASK-2 | banner tests assert Memory row under indexed and degraded scenarios; pytest passes | ✓ pass |

**Tests:** scoped — 4 passed, 0 failed (`tests/test_flow_bootstrap_banner.py`)
**Doc Sync:** fixed — `docs/specs/bootstrap.md` (banner description + `display_welcome_banner` signature); `docs/specs/memory.md` (added `count_docs` to MemoryStore methods table)

**Overall: DELIVERED**
`Knowledge:` banner row replaced with `Memory:` showing backend, optional degradation, and live knowledge/session counts; `MemoryStore.count_docs()` added; all four banner rendering scenarios locked by tests.

## Implementation Review — 2026-05-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | no `Knowledge:` banner-row hit; `Memory:` row present in banner.py | ✓ pass | `banner.py:42` — `display_welcome_banner(deps, *, knowledge_count: int = 0, session_count: int = 0)`; `banner.py:33` — `Memory:` line built; `memory_store.py:1353` — `count_docs(source) -> int` with parameterized SQL |
| TASK-2 | banner tests assert Memory row under indexed and degraded scenarios; pytest passes | ✓ pass | `tests/test_flow_bootstrap_banner.py:6,18,33,47` — all four scenarios (indexed, degradation, grep, zero counts); 4 passed in 0.03s |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `_build_memory_line` imported outside `co_cli/bootstrap` package — `_prefix` visibility violation | `tests/test_flow_bootstrap_banner.py:3` | blocking | Renamed to `build_memory_line` in `banner.py` and updated import in test file |
| Import block unsorted in `evals/_report.py` (pre-existing, in diff) | `evals/_report.py:12` | blocking | Fixed via `ruff check --fix` |

### Tests
- Command: `uv run pytest -x -v`
- Result: 461 passed, 0 failed
- Log: `.pytest-logs/20260517-*-review-impl.log`

### Behavioral Verification
- `uv run co chat` (piped exit): ✓ banner renders `Memory: hybrid · tei/embeddinggemma 1024d  knowledge: 48  sessions: 8` — no `Knowledge:` row present
- `success_signal` verified: user sees `Memory:` with backend, knowledge count, and session count in the startup banner

### Overall: PASS
Two blocking issues found and fixed (private function name visibility violation + stale import sort); all spec requirements met, 461 tests green, banner behavioral output confirmed.
