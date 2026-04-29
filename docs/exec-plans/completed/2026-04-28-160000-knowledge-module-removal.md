# Exec Plan: Knowledge Module Merge into Memory (Impl-Level)

_Created: 2026-04-28_
_Slug: knowledge-module-removal_
_Revised: 2026-04-28 — confirmed scope: implementation-level merge only. Agent
tool surface stays unified at `memory_*` (already done in `co_cli/tools/memory.py`).
Inside `co_cli/memory/` package, session and knowledge are co-equal kinds of memory._

## Problem

`co_cli/knowledge/` (T2 reusable artifacts) and `co_cli/memory/` (T1 session
transcripts + FTS5 session index) are two parallel implementation packages
with overlapping vocabulary. The agent surface is already unified (`memory_*`
tools), but the implementation is split. Goal: collapse to a single
`co_cli/memory/` package that contains both session and knowledge submodules,
and a single `co_cli/tools/memory/` package that contains both tool surfaces.

## Outcome

| Today | After |
|---|---|
| `co_cli/memory/store.py` (class `MemoryIndex`, indexes `~/.co-cli/session-index.db`) | `co_cli/memory/session_store.py` (class `SessionStore`) |
| `co_cli/knowledge/store.py` (class `KnowledgeStore`, indexes `~/.co-cli/co-cli-search.db`) | `co_cli/memory/knowledge_store.py` (class `KnowledgeStore` unchanged) |
| `co_cli/knowledge/{artifact,dream,archive,decay,frontmatter,mutator,chunker,query,ranking,similarity,service,search_util,_embedder,_reranker,_stopwords,_window}.py` | `co_cli/memory/{same names}` |
| `co_cli/tools/memory.py` (file) | `co_cli/tools/memory/` (package) |
| `co_cli/tools/knowledge/{read,write,helpers}.py` | `co_cli/tools/memory/{read,write,_helpers}.py` |
| `tests/knowledge/test_*.py` (12 files) | `tests/memory/test_*.py` (filenames preserved) |

## Out of Scope (preserved as-is)

- Class names: `KnowledgeStore`, `KnowledgeArtifact`, `KnowledgeSettings` (T2 keeps "knowledge" identity inside merged package)
- Config module: `co_cli/config/knowledge.py`
- Config keys / variables: `knowledge_dir`, `knowledge_store`
- Storage paths: `~/.co-cli/knowledge/*.md` (artifact files), `~/.co-cli/co-cli-search.db`, `~/.co-cli/session-index.db`
- Env var prefixes: `CO_KNOWLEDGE_*`
- CLI subcommand: `co knowledge` (`co_cli/commands/knowledge.py`)
- OpenTelemetry tracer name: `"co.knowledge"`
- Test filenames: `test_knowledge_*.py` keep their names under `tests/memory/`
- ArtifactKindEnum, FTS5 schema, frontmatter format
- The two SQLite databases (`session-index.db` for sessions, `co-cli-search.db` for artifacts) — they index different content with different schemas

## Sequencing

Sequential, single-threaded (TL handles all tasks — the renames and sweeps are
cross-cutting). The tree breaks between TASK-1 and TASK-3; meaningful test
runs only resume after TASK-3.

---

## ✓ DONE — TASK-1: Rename T1 session-store file + class

`co_cli/memory/store.py` (8.5k, class `MemoryIndex`) is the T1 session FTS5
index over `~/.co-cli/session-index.db`. Rename file to `session_store.py`
(matching parallel naming with `knowledge_store.py` post-merge) and class to
`SessionStore` (matching the file).

Sub-steps:
1. `git mv co_cli/memory/store.py co_cli/memory/session_store.py`
2. Inside the file: rename class `MemoryIndex` → `SessionStore`
3. Update all 8 importers: `from co_cli.memory.store import MemoryIndex` → `from co_cli.memory.session_store import SessionStore`

files:
- `co_cli/memory/store.py` (rename target)
- `co_cli/deps.py`
- `co_cli/bootstrap/core.py`
- `co_cli/tools/memory.py`
- `tests/bootstrap/test_bootstrap.py`
- `tests/knowledge/test_memory_index.py`
- `tests/memory/test_memory_search_browse.py`
- `tests/memory/test_session_summary.py`
- `tests/memory/test_session_search_tool.py`

done_when: `grep -rn "MemoryIndex\|co_cli\.memory\.store" co_cli/ tests/` returns 0 AND `uv run pytest tests/memory/ tests/bootstrap/test_bootstrap.py -x` passes.

---

## ✓ DONE — TASK-2: Move `co_cli/knowledge/*` into `co_cli/memory/`

`git mv` every tracked file from `co_cli/knowledge/` to `co_cli/memory/`. For
untracked files (`co_cli/knowledge/service.py`), use plain `mv`. Rename T2's
`store.py` → `knowledge_store.py` during the move (parallel naming with
`session_store.py`). Merge `prompts/` (no filename collisions: T1 has
`session_summarizer.md`; T2 has `dream_merge.md`, `dream_miner.md`). Delete
empty `co_cli/knowledge/` directory.

This task only does file moves — imports inside the moved files still
reference `co_cli.knowledge.*` and are broken until TASK-3. Pytest is not
expected to pass between TASK-2 and TASK-3.

files:
- All 17+ files under `co_cli/knowledge/` (move targets)
- `co_cli/memory/` (merge target — will gain ~17 new files)

done_when: `co_cli/knowledge/` directory does not exist AND `co_cli/memory/knowledge_store.py` exists AND `co_cli/memory/artifact.py` exists AND `co_cli/memory/prompts/dream_miner.md` exists AND `co_cli/memory/service.py` exists.

---

## ✓ DONE — TASK-3: Sweep imports `co_cli.knowledge.*` → `co_cli.memory.*`

Mechanical replacement across all source. Special case for the renamed file:
`co_cli.knowledge.store` → `co_cli.memory.knowledge_store` (not `co_cli.memory.store`).

files: all files matching `rg -l "co_cli\.knowledge" co_cli/ tests/` (~30 files at start of task).

done_when: `grep -rn "co_cli\.knowledge\." co_cli/ tests/` returns 0 AND `uv run pytest tests/ -x --co -q 2>&1 | tail -3` shows successful collection (no ImportError) AND `uv run pytest tests/memory/ tests/knowledge/ tests/bootstrap/ -x` passes.

---

## ✓ DONE — TASK-4: Convert `co_cli/tools/memory.py` → package; fold `co_cli/tools/knowledge/*` into it

Sequence:
1. `git mv co_cli/tools/memory.py co_cli/tools/_memory_tmp.py` (free the name)
2. `mkdir co_cli/tools/memory`
3. `git mv co_cli/tools/_memory_tmp.py co_cli/tools/memory/recall.py` (search/browse/summarize tools)
4. `git mv co_cli/tools/knowledge/read.py co_cli/tools/memory/read.py`
5. `git mv co_cli/tools/knowledge/write.py co_cli/tools/memory/write.py`
6. `git mv co_cli/tools/knowledge/helpers.py co_cli/tools/memory/_helpers.py` (private)
7. `git rm co_cli/tools/knowledge/__init__.py` (or `git mv` to a deleted state)
8. Write `co_cli/tools/memory/__init__.py` — docstring only per project rule
9. Update agent registry and other importers:
   - `co_cli/agent/_native_toolset.py` (imports `memory_search` from `co_cli.tools.memory`; imports `memory_list, memory_read` from `co_cli.tools.knowledge.read`; imports from `co_cli.tools.knowledge.write`)
   - `co_cli/tools/agent_delegate.py`
   - `co_cli/tools/agents.py`
   - `co_cli/commands/knowledge.py` (CLI — imports `grep_recall` from `co_cli.tools.knowledge.read`)
   - `co_cli/memory/dream.py` (was `knowledge/dream.py` — imports `memory_create` and `_slugify`)
   - Test files in `tests/knowledge/test_articles.py`, `tests/knowledge/test_knowledge_tools.py`, `tests/memory/test_memory_search_browse.py`, etc.

files:
- `co_cli/tools/memory.py` (move)
- `co_cli/tools/knowledge/{__init__,read,write,helpers}.py` (move/delete)
- `co_cli/agent/_native_toolset.py`
- `co_cli/tools/agent_delegate.py`
- `co_cli/tools/agents.py`
- `co_cli/commands/knowledge.py`
- `co_cli/memory/dream.py`
- `tests/knowledge/test_articles.py`, `tests/knowledge/test_knowledge_tools.py`
- `tests/memory/test_memory_search_browse.py` (and any other test importing `co_cli.tools.memory` or `co_cli.tools.knowledge.*`)

done_when: `co_cli/tools/knowledge/` does not exist AND `co_cli/tools/memory/__init__.py` exists AND `co_cli/tools/memory/recall.py` exists AND `co_cli/tools/memory/write.py` exists AND `grep -rn "co_cli\.tools\.knowledge" co_cli/ tests/` returns 0 AND `uv run pytest tests/memory/test_memory_search_browse.py tests/knowledge/test_articles.py tests/knowledge/test_knowledge_tools.py -x` passes.

---

## ✓ DONE — TASK-5: Move `tests/knowledge/*` → `tests/memory/`; sync docs

Sub-steps:
1. `git mv tests/knowledge/*.py tests/memory/` (12 files; filenames preserved — `test_knowledge_archive.py` stays as-is)
2. Plain `mv` for any untracked test files in `tests/knowledge/`
3. Delete empty `tests/knowledge/` directory
4. Sync docs:
   - `CLAUDE.md` — Knowledge System section: update to reflect that T1 session memory and T2 knowledge artifacts both live under `co_cli/memory/` package
   - `docs/specs/memory.md` — update package layout description
   - Other specs: search for `co_cli/knowledge` and `co_cli/tools/knowledge` references; update to new paths
5. Storage paths in docs (`~/.co-cli/knowledge/*.md`) stay as-is — out of scope per "Out of Scope"

files:
- All files under `tests/knowledge/` (move targets)
- `CLAUDE.md`
- `docs/specs/memory.md`
- Other docs matched by `rg -l "co_cli/knowledge\|co_cli\.knowledge\|co_cli/tools/knowledge\|tests/knowledge" docs/ CLAUDE.md`

done_when: `tests/knowledge/` directory does not exist AND `uv run pytest tests/memory/ -v` passes (all merged tests run cleanly under the unified tree) AND `grep -rn "co_cli/knowledge\|co_cli\.knowledge\|co_cli/tools/knowledge" docs/ CLAUDE.md` returns 0.

---

## References

- `co_cli/{memory,knowledge,tools/memory.py,tools/knowledge}` — pre-merge layout
- `co_cli/config/core.py` — `SEARCH_DB` constant (unchanged)
- `co_cli/bootstrap/core.py:320` — inline `session-index.db` path (unchanged)
- `~/.co-cli/co-cli-search.db` (T2 — unchanged)
- `~/.co-cli/session-index.db` (T1 — unchanged)

---

## Delivery Summary — 2026-04-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep clean + scoped pytest passes | ✓ pass (52 tests) |
| TASK-2 | knowledge/ removed; key files at new paths | ✓ pass |
| TASK-3 | grep clean + scoped pytest passes | ✓ pass (180 tests) |
| TASK-4 | tools/knowledge/ removed; tools/memory/ package + scoped pytest passes | ✓ pass (39 tests) |
| TASK-5 | tests/knowledge/ removed; full tests/memory/ passes; docs/specs clean | ✓ pass (168 tests) |

**Tests:** scoped (touched files) — 217 passed, 0 failed, 3 deselected (`@pytest.mark.local` LLM tests skipped during refactor).
**Lint:** `scripts/quality-gate.sh lint --fix` — 7 auto-fixed (import ordering after class/file renames), 0 remaining.
**Doc Sync:** clean — `docs/specs/{tools,dream,bootstrap,skills,memory,system}.md` rewritten in-place; CLAUDE.md unaffected. Research docs (`docs/reference/RESEARCH-*.md`) intentionally left as historical snapshots.

**Scope expansions noted during execution (folded in):**
- Renamed `init_memory_index` → `init_session_store` (function in `co_cli/bootstrap/core.py`) for naming consistency with the new `SessionStore` class
- Renamed attribute `deps.memory_index` → `deps.session_store` (in `co_cli/deps.py`) for the same reason
- ⚠ Extra file: `co_cli/main.py` — picked up the renamed import + call site (touched only via mechanical sweep)

**Pre-existing in-flight state folded in (per user override):**
- ~40 modified files inherited at task start, including coworker untracked work (`co_cli/knowledge/service.py`, `tests/knowledge/test_service.py`, `tests/memory/test_memory_search_browse.py`, `tests/memory/test_session_search_tool.py`) — preserved through `git mv` for tracked files and plain `mv` for untracked. The dependency plan `2026-04-28-150000-memory-surface-unification` was already partially in flight (unified `tools/memory.py` surface) and absorbed into this delivery.

**Behavior:** Agent tool surface unchanged — `memory_search`, `memory_list`, `memory_read`, `memory_create`, `memory_modify`. Internal package layout collapsed: `co_cli/memory/` now houses both T1 session memory (`session_store.py`, `session.py`, `session_browser.py`, `transcript.py`, `summary.py`, `indexer.py`) and T2 knowledge artifacts (`knowledge_store.py`, `artifact.py`, `service.py`, `dream.py`, `archive.py`, `decay.py`, `frontmatter.py`, `mutator.py`, `chunker.py`, `query.py`, `ranking.py`, `similarity.py`, `search_util.py`, `_embedder.py`, `_reranker.py`, `_stopwords.py`, `_window.py`). Tool surface collapsed similarly: `co_cli/tools/memory/` package with `recall.py`, `read.py`, `write.py`, `_helpers.py`. T2's "knowledge" identity preserved internally (class names `KnowledgeStore`/`KnowledgeArtifact`, config module `co_cli/config/knowledge.py`, env vars `CO_KNOWLEDGE_*`, storage path `~/.co-cli/knowledge/`, CLI subcommand `co knowledge`). DBs unchanged: `~/.co-cli/session-index.db` (T1 sessions) and `~/.co-cli/co-cli-search.db` (T2 artifacts).

**Overall: DELIVERED**
Five-task implementation-level merge complete: `co_cli/knowledge/` collapsed into `co_cli/memory/`, `co_cli/tools/knowledge/` collapsed into `co_cli/tools/memory/`, `tests/knowledge/` collapsed into `tests/memory/`. Agent tool surface unchanged; storage paths unchanged; all 217 scoped tests pass.

---

## Implementation Review — 2026-04-28

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | grep clean + scoped pytest passes | ✓ pass | `co_cli/memory/session_store.py:80` (class `SessionStore`); `co_cli/deps.py:200` (`session_store: SessionStore` attr); `co_cli/bootstrap/core.py:306` (`def init_session_store`); 0 residual `MemoryIndex` / `co_cli.memory.store` |
| TASK-2 | knowledge/ removed; key files at new paths | ✓ pass | `co_cli/knowledge/` absent; `co_cli/memory/{knowledge_store,artifact,service,dream}.py` present; `co_cli/memory/prompts/{dream_miner,dream_merge,session_summarizer}.md` merged |
| TASK-3 | grep clean + full pytest collection ok | ✓ pass | 0 residual `co_cli.knowledge` imports; canonical T2 path is `co_cli.memory.knowledge_store` (8 importers verified) |
| TASK-4 | tools/knowledge/ gone; tools/memory/ package + tests pass | ✓ pass | `co_cli/tools/memory/{__init__,read,write,recall}.py`; 0 residual `co_cli.tools.knowledge`; 0 bare `from co_cli.tools.memory import ...` (all use submodule path) |
| TASK-5 | tests/knowledge/ gone; full tests/memory/ passes; docs/specs clean | ✓ pass | tests/memory/ has 18 files; 0 stale path references in docs/specs/ + CLAUDE.md |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Visibility violation: `co_cli.tools.memory._helpers` (private module) imported from `co_cli.memory.dream` (different package) | `co_cli/memory/dream.py:43` | blocking | Removed `_helpers.py` entirely; switched import to `from co_cli.memory.service import _slugify` (same-package, no boundary crossing) |
| Triple-duplicate definitions: `_slugify`, `_find_by_slug`, `_find_article_by_url` defined in BOTH `service.py` and `_helpers.py` | `co_cli/tools/memory/_helpers.py:17,22,54` | blocking | Deleted `_helpers.py`; `service.py` is sole canonical home |
| Dead code: `_touch_recalled` defined but never called anywhere | `co_cli/tools/memory/_helpers.py:27` | blocking | Removed (file deleted) |
| Stale docstring: "Shared helpers for knowledge tool modules" (file is in tools/memory/) | `co_cli/tools/memory/_helpers.py:1` | minor | Removed (file deleted) |
| Stale test assertions: `test_knowledge_article_read_*` reference old tool name; the underlying registry was updated to `memory_read` (in-flight unification) but tests were not | `tests/context/test_tool_result_markers.py:130-142,322` | blocking | Renamed test functions and updated tool-name strings + parametrize to `memory_read` |

### Tests
- Command: `uv run pytest -m "not local"`
- Result: **691 passed, 0 failed, 27 deselected** (`@pytest.mark.local` LLM-only tests skipped)
- Log: `.pytest-logs/<timestamp>-review-impl-2.log`

### Doc Sync
- Scope: full (cross-cutting package rename touches CLAUDE.md, multiple specs)
- Result: clean — already swept in TASK-5 (docs/specs/{tools,dream,bootstrap,skills,memory,system}.md + CLAUDE.md updated in-place); final re-scan returned 0 stale references

### Behavioral Verification
- `scripts/quality-gate.sh lint`: ✓ clean
- Import-resolution smoke test:
  - ✓ `co_cli.commands.knowledge` (slash-command module) imports cleanly
  - ✓ Tool surface: `memory_search`, `memory_list`, `memory_read`, `memory_create`, `memory_modify` all resolvable
  - ✓ Internal types: `KnowledgeStore`, `SessionStore`, `KnowledgeArtifact`, `save_artifact`, `mutate_artifact` resolvable
- Full interactive `co chat` not exercised — agent tool surface is unchanged (no user-facing API delta to test); merge is internal/structural

### Overall: PASS

Five-task implementation-level merge is sound. Five blocking findings discovered and fixed during review (one visibility violation, three duplicates/dead-code in `_helpers.py` removed, one stale-test-assertion bundle in `test_tool_result_markers.py`). 691 tests green, lint clean, docs synced. Ship-ready.
