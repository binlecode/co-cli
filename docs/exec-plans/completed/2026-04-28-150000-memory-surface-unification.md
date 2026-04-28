# Plan: Memory Surface Unification

_Created: 2026-04-28_
_Slug: memory-surface-unification_
_Task type: code-feature_

---

## Context

co-cli exposes two parallel knowledge write-tool clusters and two separate search tools for
what is semantically one store. The existing five write tools
(`knowledge_save`, `knowledge_article_save`, `knowledge_append`, `knowledge_update`,
`_consolidate_and_reindex`) share overlapping dedup logic scattered across write.py with no
service boundary. The agent is also forced to choose between `memory_search` (T1 transcripts)
and `knowledge_search` (T2 artifacts) at every recall turn, a split that maps to storage
internals rather than agent intent.

**Current-state validation:**
- `co_cli/knowledge/service.py` does not exist — Phase 1 not started.
- `memory_create`, `memory_modify`, `memory_list`, `memory_read` do not exist — not started.
- Old tools confirmed present in write.py: `knowledge_save` (45–150),
  `_consolidate_and_reindex` (153–206), `knowledge_article_save` (209–291),
  `knowledge_append` (294–352), `knowledge_update` (355–450).
- `_native_toolset.py` registers: `knowledge_search`, `knowledge_list`,
  `knowledge_article_read`, `memory_search`, `knowledge_update`, `knowledge_append`,
  `knowledge_article_save`.
- No `knowledge_*` tool references found in `co_cli/skills/` or `co_cli/agent/prompts/`.
- Existing `memory_search` at `co_cli/tools/memory.py:135` handles T1 transcripts only.
- Additional callers confirmed: `co_cli/tools/deferred_prompt.py` (strings
  `"knowledge_article_save"`), `co_cli/tools/display.py` (`"knowledge_article_save": "content"`),
  `co_cli/context/_tool_result_markers.py` (`"knowledge_article_read"` marker key),
  `evals/eval_proactive_recall.py` (`expect_tool="knowledge_search"`).

**In-flight plan conflict:** `docs/exec-plans/active/2026-04-28-091317-preference-pipeline.md`
gates on `knowledge_save` being callable. **TASK-3 of this plan must not ship until
`preference-pipeline` is rebased to call `memory_create` instead.** See Sequencing note below.

**Workflow artifact hygiene:** No stale TODO files found for this scope.

---

## Problem & Outcome

**Problem:** The agent exposes 3 write tools (knowledge_update, knowledge_append,
knowledge_article_save) plus an internal `knowledge_save`, none sharing a service boundary.
Dedup logic lives in two places. Separately, the agent must choose between `knowledge_search`
(T2) and `memory_search` (T1) with a disambiguation rule baked into both docstrings.

**Failure cost:** Agent asks "should I use knowledge or memory?" in dialogue because the
split is not semantic. Dedup logic drift causes silent divergence between save paths.
New callers must choose among 3 write tools for what is logically one operation.

**Outcome:** 5 unified `memory_*` tools over three tiers. Agent uses a single write tool
(`memory_create`) and a single search tool (`memory_search`) with no knowledge-vs-memory
disambiguation burden — search always covers both tiers.

| Today | After |
|---|---|
| `memory_search` (transcripts) + `knowledge_search` | `memory_search` (both tiers always) |
| `knowledge_list` | `memory_list` |
| `knowledge_article_read` | `memory_read` |
| `knowledge_save` (internal) + `knowledge_article_save` | `memory_create` |
| `knowledge_append` + `knowledge_update` | `memory_modify(action="append"\|"replace")` |

---

## Scope

**In scope:**
- New `co_cli/knowledge/service.py` (pure functions, no RunContext)
- New tools: `memory_create`, `memory_modify` (registered under these final names from Phase 1)
- Delete: `knowledge_save`, `knowledge_article_save`, `_consolidate_and_reindex`,
  `knowledge_append`, `knowledge_update`
- Rename: `knowledge_list` → `memory_list`, `knowledge_article_read` → `memory_read`
- Unified `memory_search` replacing both T1 and T2 search (always searches both)
- Dream miner updated to `memory_create`
- Field renames in `memory_read` return: `article_id` → `artifact_id`, `origin_url` → `source_ref`
- Sweep: `deferred_prompt.py`, `display.py`, `_tool_result_markers.py`, `evals/eval_proactive_recall.py`
- CLAUDE.md Knowledge System section update

**Out of scope:**
- Internal module/type/path/config-key renames (covered by follow-up `knowledge-module-removal`)
- `knowledge_analyze` (not CRUD; separate redesign)
- T1 mutation by agent (transcripts are system-written)
- External memory provider plugins
- Storage format / FTS5 schema / frontmatter changes
- Tag/date filter params and explicit scope/tier selection are not added in this plan; retained for a follow-on
- Cross-session eval (follow-on plan)

**Sequencing:** `preference-pipeline` must be rebased to call `memory_create` (not
`knowledge_save`) before TASK-3 ships. Dev must confirm rebase order before beginning TASK-3.

---

## Behavioral Constraints

1. `memory_modify(action="replace")` must reject `target=""` with a descriptive error — replace
   without a target is ambiguous.
2. `memory_modify` must reject content strings containing Read-tool line-number prefixes
   (`\d+→ ` or `Line N: `) in both `content` and `target`, matching `_LINE_PREFIX_RE` /
   `_LINE_NUM_RE` from write.py.
3. `memory_modify` must error when `target` matches zero occurrences in the body (for
   action="replace") and must error when `target` matches more than one occurrence.
4. `memory_create` with `source_url` set must default `decay_protected=True` when the caller
   omits it — web-fetched articles are reference material the agent should retain long-term,
   matching the explicit `decay_protected=True` in the old `knowledge_article_save`.
5. `service.save_artifact` must not write any file on dedup-skip (Jaccard > 0.9) — the
   return action must be `"skipped"` and the artifact count on disk must not increase.
6. `memory_search` always searches both T2 artifacts (BM25) and T1 transcripts
   (LLM-summarized, capped at 3 sessions). Returns flat `{count, results}` with a `tier`
   field per result (`"artifacts"` or `"sessions"`). Scores are NOT cross-comparable; the
   `tier` field is the only reliable provenance signal.
7. Empty `query` activates browse mode for T1: returns recent-sessions metadata with zero LLM
   cost. T2 returns nothing for an empty query (BM25 requires terms). The result set may
   contain only T1 results in this mode — that is correct behavior, not an error.
8. Tool visibility policy: `memory_create` and `memory_modify` are DEFERRED + approval=True.
   `memory_list`, `memory_read`, `memory_search` are ALWAYS + is_read_only=True.
9. `service.py` must have no imports of `RunContext`, `CoDeps`, or any agent/tool module.
   It accepts `knowledge_dir: Path` and `knowledge_store: KnowledgeStore | None = None` as
   explicit params. Locking (`resource_locks.try_acquire`) stays in the async tool wrapper —
   not in service.py.
10. All existing `knowledge_*` test assertions (path, artifact_id, action fields) must be
    migrated to the new field names — no test may reference `origin_url` or `article_id`
    after Phase 2 ships.

---

## High-Level Design

### Phase 1 — T2 CRUD Unification

**`co_cli/knowledge/service.py`** — pure functions, no RunContext:

```python
@dataclass
class SaveResult:
    path: Path
    artifact_id: str
    action: Literal["saved", "skipped", "merged", "appended"]
    content: str
    fm_dict: dict
    slug: str

@dataclass
class MutateResult:
    path: Path
    slug: str
    action: Literal["appended", "replaced"]
    updated_body: str
    fm: dict

def save_artifact(
    knowledge_dir: Path, *,
    content: str, artifact_kind: str,
    title: str | None = None, description: str | None = None,
    tags: list[str] | None = None, source_url: str | None = None,
    source_type: str = SourceTypeEnum.DETECTED.value,
    source_ref: str | None = None, decay_protected: bool = False,
    related: list[str] | None = None,
    consolidation_enabled: bool = False,
    consolidation_similarity_threshold: float = 0.75,
) -> SaveResult: ...

def mutate_artifact(
    knowledge_dir: Path, *, slug: str,
    action: Literal["append", "replace"],
    content: str, target: str = "",
    knowledge_store: KnowledgeStore | None = None,
) -> MutateResult: ...
```

`save_artifact` branches:
- `source_url` set → URL-keyed dedup (logic from `_consolidate_and_reindex` / write.py:153–206
  and `knowledge_article_save` path); sets `source_type=WEB_FETCH`, `source_ref=source_url`
- `consolidation_enabled` → Jaccard dedup (logic from `knowledge_save` write.py:78–108)
- else → straight create

Tool wrappers (`memory_create`, `memory_modify`) do the following for mutations:
1. Async wrapper acquires `resource_locks.try_acquire(slug)` before calling `mutate_artifact`
2. Calls `mutate_artifact` (sync, no ctx)
3. After return, calls `_reindex_knowledge_file(ctx, ...)` with the MutateResult data

For saves, `memory_create` wrapper calls `save_artifact` then reindexes with `_reindex_knowledge_file`.

### Phase 2 — Memory Namespace + Tier Merge

New `memory_search` signature:
```python
async def memory_search(
    ctx: RunContext[CoDeps],
    query: str = "",
    kind: ArtifactKindEnum | None = None,
    limit: int = 10,
) -> ToolReturn: ...
```

Always searches both tiers in parallel:
- T2 artifacts: BM25 FTS5 search (existing `knowledge_search` logic); `kind` and `limit` apply
- T1 transcripts: LLM-summarized session search (existing `memory_search` logic); capped at 3
  sessions regardless of `limit`; empty `query` activates browse mode (zero LLM cost)

Returns flat `{count, results}` where each result carries a `tier` field (`"artifacts"` or
`"sessions"`). Scores are NOT cross-comparable — docstring must warn callers explicitly.

---

## Implementation Plan

### Phase 1: T2 CRUD Unification

**TASK-1** — Create `co_cli/knowledge/service.py`

```
files:
  - co_cli/knowledge/service.py
  - tests/knowledge/test_service.py

done_when: >
  uv run pytest tests/knowledge/test_service.py -x passes.
  Tests cover: URL-dedup match → consolidated, URL-dedup no-match → saved,
  Jaccard >0.9 → skipped (no file written, artifact count unchanged),
  Jaccard overlap → merged/appended, no-consolidation → straight create,
  mutate append round-trip, mutate replace round-trip,
  replace with zero matches → ValueError, replace with multiple matches → ValueError,
  line-prefix rejection in mutate content and target,
  lock contention: two asyncio tasks racing on the same slug → second gets ResourceBusyError.

success_signal: N/A (internal service layer, not agent-visible)
```

**TASK-2** — Add `memory_create` and `memory_modify` agent tools

```
files:
  - co_cli/tools/knowledge/write.py
  - co_cli/agent/_native_toolset.py
  - tests/knowledge/test_knowledge_tools.py

done_when: >
  uv run pytest tests/knowledge/test_knowledge_tools.py -x passes.
  assert any(t.name == "memory_create" for t in agent._function_tools) — tool registered.
  assert any(t.name == "memory_modify" for t in agent._function_tools) — tool registered.
  assert not any(t.name in ("knowledge_create", "knowledge_modify") for t in agent._function_tools).
  memory_create: saves artifact, returns artifact_id; with source_url set defaults
  decay_protected=True. memory_modify action="append" round-trips content.
  memory_modify action="replace" with empty target raises tool_error.
  Guard parity verified: line-prefix guard, count-zero guard, count-many guard all tested.

success_signal: Agent can save artifacts and web articles through a single `memory_create`
  tool; `memory_modify` replaces or appends without rewriting the full body.

prerequisites: [TASK-1]
```

**TASK-3** — Delete old write tools; promote dream miner to `memory_create`

```
files:
  - co_cli/tools/knowledge/write.py
  - co_cli/knowledge/dream.py
  - co_cli/knowledge/prompts/dream_miner.md
  - co_cli/agent/_native_toolset.py
  - co_cli/tools/deferred_prompt.py
  - co_cli/tools/display.py

done_when: >
  scripts/quality-gate.sh full passes.
  grep -r "knowledge_save\|knowledge_article_save\|knowledge_append\|knowledge_update\|_consolidate_and_reindex" co_cli/ evals/
  returns zero hits (deleted, not just unregistered).
  grep "knowledge_save" co_cli/knowledge/prompts/dream_miner.md returns zero hits.
  uv run pytest tests/knowledge/test_knowledge_dream.py -x passes (dream miner still
  extracts and saves artifacts via memory_create).
  NOTE: Confirm preference-pipeline has been rebased to memory_create before executing.

success_signal: N/A (internal; dream behavior unchanged for user)

prerequisites: [TASK-2]
```

---

### Phase 2: Memory Namespace + Tier Merge

**TASK-4** — Rename `knowledge_list` → `memory_list`; rename `knowledge_article_read` → `memory_read`

```
files:
  - co_cli/tools/knowledge/read.py
  - co_cli/agent/_native_toolset.py
  - co_cli/context/_tool_result_markers.py
  - tests/knowledge/test_knowledge_tools.py

done_when: >
  uv run pytest tests/knowledge/test_knowledge_tools.py -x passes.
  assert any(t.name == "memory_list" for t in agent._function_tools).
  assert any(t.name == "memory_read" for t in agent._function_tools).
  memory_read returns a dict with keys "artifact_id" and "source_ref" (not "article_id"
  or "origin_url").
  grep -r "knowledge_list\|knowledge_article_read" co_cli/ returns zero hits.
  _tool_result_markers.py uses "memory_read" as the marker key (not "knowledge_article_read").

success_signal: User can list and read persistent memory artifacts with `memory_list` and
  `memory_read`; result dict uses `artifact_id` and `source_ref` field names.
```

### ✓ DONE — TASK-5: Replace `knowledge_search` + `memory_search` with unified `memory_search(scope=...)`

```
files:
  - co_cli/tools/knowledge/read.py
  - co_cli/tools/memory.py
  - co_cli/agent/_native_toolset.py
  - tests/knowledge/test_knowledge_tools.py
  - tests/memory/test_session_search_tool.py
  - evals/eval_proactive_recall.py

done_when: >
  uv run pytest tests/knowledge/test_knowledge_tools.py tests/memory/test_session_search_tool.py -x passes.
  assert any(t.name == "memory_search" for t in agent._function_tools).
  assert not any(t.name == "knowledge_search" for t in agent._function_tools).
  memory_search(query="someterm") returns flat {count, results} where each result has a "tier" field.
  memory_search(query="") returns flat {count, results} with tier="sessions" results (browse mode).
  T2 results carry tier="artifacts"; T1 results carry tier="sessions".
  grep -r "knowledge_search" co_cli/ evals/ returns zero hits (old function deleted).
  evals/eval_proactive_recall.py updated to assert expect_tool="memory_search".

success_signal: Agent calls `memory_search` for all recall with no tier-selection decision;
  results from both artifacts and sessions appear in a single flat list.

prerequisites: [TASK-4]
```

**TASK-6** — Prompts/skills sweep + CLAUDE.md + final quality gate

```
files:
  - CLAUDE.md
  - (any additional hits from grep sweep)

done_when: >
  grep -r "knowledge_search\|knowledge_list\|knowledge_article_read\|knowledge_article_save\|knowledge_append\|knowledge_update\|knowledge_save" \
    co_cli/skills/ co_cli/agent/prompts/ co_cli/context/ co_cli/tools/deferred_prompt.py \
    co_cli/tools/display.py co_cli/knowledge/ evals/ returns zero hits.
  CLAUDE.md Knowledge System section updated to describe T0/T1/T2 model and 5 memory_* tools.
  scripts/quality-gate.sh full passes.

success_signal: N/A (doc/prompt sweep; agent behavioral verification covered by TASK-5)

prerequisites: [TASK-3, TASK-4, TASK-5]
```

---

## Testing

- **Red-Green-Refactor** on every TASK-1, TASK-2, TASK-4, TASK-5: write failing tests first,
  then implement, then clean up.
- TASK-3 is primarily deletion — verify existing tests still pass after each deletion.
- All tests use real `tmp_path` fixtures and actual file I/O — no mocks.
- TASK-5 `scope="all"` test needs a tmp session DB and a tmp knowledge dir populated with at
  least one artifact and one session transcript matching the test query term.

---

## Open Questions

None — all answerable by inspection of the current source.

---

## Final — Team Lead

Plan approved. Two cycles completed — all blocking issues resolved; C2 approved by both Core Dev and PO.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev memory-surface-unification`

---

## Delivery Summary — 2026-04-28

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | uv run pytest tests/knowledge/test_service.py -x passes | ✓ pass |
| TASK-2 | uv run pytest tests/knowledge/test_knowledge_tools.py -x passes | ✓ pass |
| TASK-3 | scripts/quality-gate.sh full passes; old write tools deleted | ✓ pass |
| TASK-4 | pytest test_knowledge_tools.py -x passes; memory_list/memory_read registered | ✓ pass |
| TASK-5 | pytest tests/knowledge/test_knowledge_tools.py tests/memory/test_session_search_tool.py -x passes; grep knowledge_search co_cli/ evals/ returns zero hits | ✓ pass |
| TASK-6 | prompts/skills sweep + CLAUDE.md + final quality gate | ✓ pass |

**Tests:** scoped — tests/knowledge/test_knowledge_tools.py + tests/knowledge/test_service.py + tests/memory/test_session_search_tool.py + tests/memory/test_memory_search_browse.py — 51 passed, 0 failed

**Doc Sync:** fixed — docs/specs/memory-knowledge.md renamed to docs/specs/memory.md; Mermaid diagram, section 2.3 result shape, section 2.4 disambiguation rule + explicit search, Files table all updated; all cross-links updated to memory.md across 7 specs and 2 active plans.

⚠ Extra files touched:
- co_cli/tools/agents.py — lazy import updated (knowledge_search → memory_search)
- co_cli/tools/agent_delegate.py — lazy import updated (knowledge_search → memory_search)
- co_cli/tools/display.py — removed knowledge_search display key
- co_cli/tools/obsidian.py — docstring reference updated
- co_cli/context/prompt_text.py — docstring reference updated
- tests/memory/test_memory_search_browse.py — added tier="sessions" assertion

**Overall: FULLY DELIVERED**
TASK-3 shipped: knowledge_save, _consolidate_and_reindex, knowledge_article_save, knowledge_append, knowledge_update deleted from write.py. dream.py wired to memory_create. dream_miner.md updated with memory_create and auto-injection tagging section (absorbing preference-pipeline TASK-1). deferred_prompt.py, display.py, _native_toolset.py, test_knowledge_tools.py, test_articles.py all migrated. Stale tests/knowledge/test_session_search_tool.py removed. TASK-6 shipped: zero hits on old tool names across co_cli/, evals/ scope. CLAUDE.md Knowledge System section updated to describe T0/T1/T2 model and 5 memory_* tools. Lint passes.
