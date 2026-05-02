# Plan: Knowledge Recall Path Cleanup

**Task type:** code-feature

## Context

This plan fixes functional bugs, implements the end-to-end `tags` flow, removes dead
`SearchResult.type`, and addresses two code clarity items across `co_cli/memory/` and
`co_cli/tools/memory/`. It is the recall-path counterpart of the shipped write-path
cleanup (`docs/exec-plans/completed/2026-05-01-094818-knowledge-write-path-cleanup.md`).

**Architecture note — write-path decoupling (shipped in write-path cleanup):**
`save_artifact()` is now a pure write function — it does not call any reindex function
internally. Reindexing is the tool layer's responsibility: `memory_create` and
`memory_modify` in `write.py` call the standalone `reindex()` in `service.py` after a
successful write. This changes two tasks from the original plan:
- **T3** (tags in FTS chunks): only the standalone `reindex()` needs updating — no
  `save_artifact` internal call sites exist.
- **T8** (O(n) URL scan): `_find_article_by_url` is called inside `save_artifact`, so
  `memory_store` must be added to `save_artifact`'s signature and threaded from
  `memory_create`.

**Workflow artifact hygiene:** Write-path plan archived; no stale open plans.

**Phase 2 findings — full recall-path retrace:** A subsequent retrace of the entire recall
path surfaced two logical errors and five anti-patterns not covered by the original task
list. They are bundled into this plan as TASK-12 through TASK-18 to keep the recall-path
cleanup self-contained:
- **E1** O(n) glob scan in `memory_read_session_turn` (read.py:75-79).
- **E2** Double `_build_fts_query` sanitization in `MemoryStore.search()` (memory_store.py:535/614/727).
- **A1** False parallelism — `asyncio.gather` over three coroutines with no `await` points (recall.py:318).
- **A2** `_browse_recent` returns `ToolReturn` while `_list_artifacts` returns `list[dict]` — asymmetric (recall.py:34, 76).
- **A3** `_list_artifacts` always disk-scans even when `MemoryStore` is available (recall.py:78).
- **A4** `_list_artifacts` dead `offset` parameter — never reachable from `memory_search` (recall.py:71).
- **A5** `_hybrid_merge` doc-level aggregation uses `max` not `sum` — unconventional RRF (memory_store.py:37).

**Phase 3 findings — over-design / over-implementation pass:** Bundled as TASK-20
through TASK-30 (TASK-19 / TASK-25 / TASK-29 spun out — see "Spun-out plans" below):
- **O1** `SearchResult.to_tool_output()` dead method — never called (memory_store.py:211).
- **O3** `_fetch_reranker_texts` doc-level branch (`chunk_index is None`) is unreachable —
  every code path producing `SearchResult` sets `chunk_index` from chunks-table rows.
  **User-confirmed dead.**
- **O4** `MemoryStore.index()` does manual DELETE + INSERT instead of `INSERT ... ON
  CONFLICT(...) DO UPDATE` UPSERT — verbose, brief inconsistent window, re-implements
  a SQLite primitive.
- **O5** `**_kwargs` catch-all in `index()` becomes pointless after TASK-2 + TASK-7 —
  silently swallows kwarg typos.
- **O6** Magic multipliers `limit * 20` (chunks) and `limit * 4` (rerank pool) compound
  in hybrid mode to `limit * 80` rows fetched.
- **O8** `sanitize_fts5_query` 6-step regex pipeline defends against typing errors that
  LLM-issued queries don't make in practice.
- **O9** `search_canon` has two layers of path-traversal defense for trusted-input
  (config-derived role string).
- **O10** `index_session` partial-write recovery (extra `SELECT COUNT(*)` after hash
  match) defends against torn writes that single-transaction wrapping would prevent.
- **O12** Three snippet-size constants (40 tokens FTS, 100 chars display, 200 chars
  rerank) — undocumented divergence.

**Spun-out plans (Gate 1 review):** Three originally-bundled tasks were extracted to
their own plans because each is a meaningful decision in its own right, not internal
cleanup. Sequence numbers in filenames indicate intended order of execution:
- **#1 — O7 (`pydantic_settings` env_prefix migration)** → `2026-05-02-104154-1-knowledge-settings-env-prefix.md` —
  smallest, mechanical config-layer refactor. Sets up future settings field additions.
- **#2 — O2 (TEI-only reranker)** → `2026-05-02-104154-2-reranker-retire-llm-listwise.md` —
  eval-gated feature retirement; removes the most code surface. Breaking config change.
- **#3 — O11 (split `memory_search` → `memory_search` + `memory_browse`)** → `2026-05-02-104154-3-memory-search-browse-split.md` —
  tool-surface change with behavioral eval gate. Explicit prerequisite: this recall-path
  cleanup must land first (its T14/T15/T16/T17 reshape the helpers the split consumes).

---

## Problem & Outcome

**Problem:** `tags` is validated by the frontmatter schema and referenced in two tool
docstrings, but no runtime path reads, writes, or searches tags.

**Failure cost:**
- `memory_create` has no `tags` parameter — agents cannot label artifacts at save time.
- `grep_recall` searches only `content`, missing the `title` field (tags are also absent).
- `memory_search` (empty-query listing) never shows tags.
- `_find_article_by_url` scans every file on every web-article save — O(n) degradation.
- `SearchResult.type` is a dead write that misleads future readers.
- `memory_read_session_turn` scans every JSONL on every call (E1) — O(n) in session count.
- `MemoryStore.search()` re-sanitizes the query in every backend (E2) — wasted work on the hot path.
- `memory_search` claims parallel channel search (A1) but executes sequentially — misleading docstring.
- `_browse_recent` / `_list_artifacts` return-type asymmetry (A2) couples `memory_search` to `ToolReturn` internals.
- `_list_artifacts` ignores the index and disk-scans (A3); has a dead `offset` parameter (A4).
- `_hybrid_merge` doc-level max aggregation (A5) underranks documents with broad query coverage.

**Outcome:** Tags round-trip end-to-end — `memory_create(tags=[...])` → stored on disk →
FTS5 chunks include tag tokens → `memory_search` lists them → `grep_recall` matches them.
`_find_article_by_url` uses the index for O(1) lookup when the store is available.
Dead `SearchResult.type` removed. Recall hot paths (`memory_read_session_turn` lookup,
FTS sanitization, list-artifact listing) are O(1) or index-backed. `memory_search`
honestly reflects its execution model. `_hybrid_merge` doc-level aggregation is justified
by eval data and documented.

---

## Scope

In scope:
- `co_cli/memory/artifact.py` — add `tags` field to `KnowledgeArtifact`
- `co_cli/memory/frontmatter.py` — serialize `tags` in `artifact_to_frontmatter`
- `co_cli/memory/memory_store.py` — add `tags` param to `store.index()`, remove `SearchResult.type`, add `find_by_source_ref`, pass-through sanitized FTS query (E2), evaluate doc-level RRF aggregation (A5), delete dead `to_tool_output` (O1), drop unreachable doc-level branch in `_fetch_reranker_texts` (O3), UPSERT in `index()` (O4), drop `**_kwargs` (O5), name retrieval-pool constants (O6), single-transaction `index_session` (O10), document snippet-size constants (O12)
- `co_cli/memory/service.py` — prepend tags to chunk body in `reindex()`; fix `_find_article_by_url`; add `tags` and `memory_store` to `save_artifact`
- `co_cli/memory/_canon_recall.py` — drop redundant traversal defense (O9)
- `co_cli/memory/search_util.py` — eval-driven trim of `sanitize_fts5_query` (O8)
- `co_cli/tools/memory/read.py` — fix `grep_recall` to search title + tags; targeted glob in `memory_read_session_turn` (E1)
- `co_cli/tools/memory/recall.py` — fix `_list_artifacts` to include tags, rename `uuid8`; convert helpers to sync (A1); make `_browse_recent` return `list[dict]` (A2); use `MemoryStore` in `_list_artifacts` (A3); drop dead `offset` (A4); document snippet sizes (O12)
- `co_cli/tools/memory/write.py` — add `tags` param to `memory_create`; thread `memory_store` to `save_artifact`

Out of scope:
- Obsidian/Drive source indexing
- Adding `tags` to `SearchResult` or the FTS SELECT query (search path returns snippet-based results; listing path already has full artifact objects)
- Tag UI beyond the existing `memory_search` formatted output
- Genuine concurrent SQLite via `aiosqlite` or thread-local connections — A1 is fixed by aligning the helpers' sync nature with the call site (no false parallelism), not by introducing real concurrency.
- Reranker-stack simplification, `pydantic_settings` env_prefix migration, `memory_search` tool split — see "Spun-out plans" above.
- Making `_SESSIONS_CHANNEL_CAP` configurable — the constant is fine; deferred until a real demand exists. (Will become free if/when the env_prefix spin-out lands.)

---

## Behavioral Constraints

- `save_artifact` must remain RunContext-free; `memory_store` is an optional parameter defaulting to `None`.
- When `memory_store is None`, `_find_article_by_url` must fall back to the existing file-scan. When `memory_store is not None` and the index doesn't contain a URL that exists on disk (index-stale edge case), a duplicate article may be created — this is an accepted tradeoff given that `reindex()` is always called after every save in the tool layer.
- `reindex()` derives tags from the frontmatter dict (same source as all other index fields); no separate tags argument needed — tags are already passed via `frontmatter.get("tags", [])` at `service.py:114`.
- Removing `SearchResult.type` from the dataclass must not affect `to_tool_output()` (type is not in its output dict).
- `type TEXT` schema column is retained in `_SCHEMA_SQL` — existing user DBs have it; no migration needed for the column.
- Jaccard dedup path in `save_artifact` does not construct a new `KnowledgeArtifact` — incoming `tags` are not merged into the existing artifact. Existing tags from disk are preserved via raw frontmatter. This is accepted; Jaccard path is dedup, not a fresh save.
- A1 fix is sequential, not concurrent: `_search_artifacts`, `_search_sessions`, `_search_canon_channel` become sync `def`s; `memory_search` calls them in order. The previous `await asyncio.gather(...)` was a no-op (no `await` points inside any callee), so the change does not regress real performance — it removes false advertising.
- A5 doc-level aggregation must be decided by eval data, not by aesthetic preference. If `sum` and `max` produce indistinguishable rankings on the existing eval suite (`evals/eval_*recall*.py`), keep `max` and document the choice; if `sum` measurably improves recall@k, switch.
- E2 sanitization pass-through must not change FTS5 query semantics — only avoid the redundant compute. The existing `_build_fts_query` callers must accept either a raw query (legacy) or a pre-sanitized query (preferred); a clear seam (`fts_query: str` parameter) is acceptable.
- O8 (`sanitize_fts5_query` simplification) carries behavior risk and is eval-gated before adoption.
- TASK-22 UPSERT and TASK-28 single-transaction wrapping must preserve the hash-skip semantics (no-op write when content unchanged) — tested at the call sites in `reindex` and `index_session`.

---

## High-Level Design

### Tags end-to-end
1. `memory_create` accepts `tags: list[str] | None = None` → passes to `save_artifact`.
2. `save_artifact` accepts `tags` → passes `tags=list(tags or [])` to `KnowledgeArtifact(...)` in URL-keyed and straight-create paths.
3. `artifact_to_frontmatter` serializes `tags` via the `if value:` guard (empty list suppressed).
4. `reindex()` reads `tags` from the frontmatter dict (already done at service.py:114 for `store.index()`); additionally prepends tag tokens to `chunk_body` before `chunk_text()`.
5. `store.index()` accepts `tags` explicitly (was absorbed by `**_kwargs` before).
6. `grep_recall` searches title + tags; `_list_artifacts` includes `tags` in returned dicts.

### O(1) URL dedup
`_find_article_by_url` gains an optional `memory_store` parameter. When present, delegates
to a new `MemoryStore.find_by_source_ref(source_ref, source)` public method that encapsulates
the `docs` lookup; returns `Path(result)` or `None`. File-scan loop kept as `else` fallback.
`save_artifact` threads `memory_store` from caller; `memory_create` passes
`ctx.deps.memory_store`.

### Dead-field removal
`SearchResult.type` and all write-side counterparts stripped. `type TEXT` column kept
in schema SQL for DB compatibility.

### Hot-path lookups (E1, E2)
- `memory_read_session_turn` replaces full-dir glob + filename parse with targeted glob
  `f"*-{session_id}.jsonl"` — single-shot O(1) filesystem operation.
- `MemoryStore.search()` runs `_build_fts_query(query)` once and threads the sanitized
  string through to `_fts_search`, `_hybrid_search`, `_fts_chunks_raw`. Backend functions
  accept the sanitized form directly.

### Recall helper alignment (A1, A2)
- `_search_artifacts`, `_search_sessions`, `_search_canon_channel` become sync `def`s —
  none has any internal `await`, so the `async def` was decorative.
- `_browse_recent` returns `list[dict]` like the others; `memory_search` performs the
  final `tool_output(...)` wrapping using a uniform shape.
- `memory_search` drops `await asyncio.gather(...)` and calls the three helpers in
  sequence; tool docstring updated to remove the "in parallel" claim.

### Listing path (A3, A4)
- `_list_artifacts` uses `MemoryStore` when available — queries `docs` for source='knowledge',
  chunk_id=0, sorted by `created DESC` — falls back to the disk scan when `memory_store is None`.
- `offset` parameter removed from `_list_artifacts` (always called with `offset=0`).

### Hybrid merge aggregation (A5)
- Today: `doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)` — winner-chunk-takes-all.
- Standard: `doc_rrf[path] = doc_rrf.get(path, 0.0) + score` — broad-coverage rewarded.
- Decision deferred to eval data: run a representative recall eval with both
  aggregation rules; pick the rule with higher recall@k or, if indistinguishable,
  keep `max` and document the choice as intentional.

### Reranker dead-branch removal (O3)
- `_fetch_reranker_texts` doc-level branch (`chunk_index is None`) is unreachable in
  practice — every backend produces chunk-level results. Branch deleted; helper
  simplifies to chunk-level only. (Full reranker-stack simplification — TEI-only —
  spun out to its own plan.)

### Storage-write hardening (O4, O5, O10)
- `index()` becomes one statement: `INSERT ... ON CONFLICT DO UPDATE WHERE excluded.hash IS NOT docs.hash` — atomic, idempotent, hash-skip preserved.
- `**_kwargs` removed from `index()` — strict signature catches typos.
- `index_session` wraps `index()` + `index_chunks()` in a single transaction; partial-write recovery query removed.

### Constants hygiene (O6, O12)
- `_CHUNK_DEDUP_FETCH_MULTIPLIER = 20` and `_RERANKER_CANDIDATE_MULTIPLIER = 4` named, with the hidden compounding to `limit*80` in hybrid mode either fixed or documented.
- Snippet-size constants named: `_FTS_SNIPPET_TOKENS = 40`, `_RERANKER_PREAMBLE_CHARS = 200`, `_SNIPPET_DISPLAY_CHARS = 100` (existing).

### Eval-gated simplification (O8)
- O8 (`sanitize_fts5_query` trim) is gated on a search-quality eval comparing the 6-step pipeline against a 3-step variant. Either branch produces a REPORT.

---

## Implementation Plan

### TASK-1 — Add `tags` to `KnowledgeArtifact` and frontmatter serialization

**files:**
- `co_cli/memory/artifact.py`
- `co_cli/memory/frontmatter.py`

**Changes:**
- `artifact.py` — add `tags: list[str] = field(default_factory=list)` to `KnowledgeArtifact` after `recall_count`.
- `artifact.py:_coerce_fields` — add `tags=list(fm.get("tags") or [])` to the `KnowledgeArtifact(...)` constructor.
- `frontmatter.py:artifact_to_frontmatter` optional list — add `("tags", list(artifact.tags))`. The `if value:` guard at the iteration suppresses empty lists automatically.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; and a new test in
`tests/test_flow_memory_write.py` verifies that `load_knowledge_artifact` on a file with
`tags: [python, testing]` in frontmatter returns `artifact.tags == ["python", "testing"]`,
and that `artifact_to_frontmatter(artifact)` includes `"tags": ["python", "testing"]`.

**success_signal:** Artifacts saved with tags in their frontmatter expose `tags` on the in-memory model.

---

### TASK-2 — Fix `store.index()` to accept and persist `tags`

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `index()` signature — add explicit `tags: str | None = None` keyword parameter before `**_kwargs` (it was silently absorbed by `**_kwargs`).
- `index()` INSERT statement — add `tags` to the column list and values tuple.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; and a new test calls
`store.index(..., tags="foo bar")` and asserts
`conn.execute("SELECT tags FROM docs WHERE path=? AND chunk_id=0", (path,)).fetchone()["tags"] == "foo bar"`.

**success_signal:** `tags` column in the `docs` table is populated for newly indexed artifacts.

---

### TASK-3 — Fix `reindex()` to prepend tags to chunk body

**files:**
- `co_cli/memory/service.py`

**Changes:**
- In `reindex()`, before the `chunk_text(...)` call (service.py:121), build:
  ```python
  tags_list = list(frontmatter.get("tags") or [])
  chunk_body = body.strip()
  if tags_list:
      chunk_body = " ".join(tags_list) + "\n" + chunk_body
  ```
  Pass `chunk_body` instead of `body.strip()` to `chunk_text`.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; and a new test calls
`reindex()` with `frontmatter={"tags": ["pytest"], ...}` and a body that does not contain
`"pytest"`, then asserts `assert any(r.path == str(artifact_path) for r in store.search("pytest"))`.

**success_signal:** Tag tokens are searchable via FTS5 even when not present in the artifact body.

**prerequisites:** [TASK-1, TASK-2]

---

### TASK-4 — Fix `grep_recall` to search title and tags

**files:**
- `co_cli/tools/memory/read.py`
- `tests/test_flow_memory_recall.py` (create)

**Changes:**
- Create `tests/test_flow_memory_recall.py` with shared fixture helpers (e.g. `_make_artifact`)
  used by TASK-4 and TASK-5 tests. No mocks — real filesystem only.
- `grep_recall` filter — replace the single `content` check with:
  ```python
  matches = [
      m for m in artifacts
      if query_lower in m.content.lower()
      or query_lower in (m.title or "").lower()
      or any(query_lower in t.lower() for t in m.tags)
  ]
  ```
- Docstring — update to: "Case-insensitive substring search across title, content, and tags."

**done_when:** `uv run pytest` passes; and a test in `tests/test_flow_memory_recall.py`
verifies `grep_recall` returns an artifact matched by title-only (body doesn't contain query)
and another matched by tag-only (body and title don't contain query).

**success_signal:** Agents in FTS-fallback mode can discover artifacts by title or tag.

**prerequisites:** [TASK-1]

---

### TASK-5 — Fix `_list_artifacts` to include `tags` in return dict and display

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `_list_artifacts` return dict (recall.py:82-91) — add `"tags": a.tags` to the dict.
- `memory_search` empty-query artifact display loop (recall.py:299-307) — add tag suffix after `kind_str`:
  ```python
  tag_str = f" [{', '.join(r['tags'])}]" if r.get("tags") else ""
  artifact_lines.append(
      f"  **{r['title']}**{kind_str}{tag_str}{path_str}: ..."
  )
  ```

**done_when:** `uv run pytest` passes; and a new test in `tests/test_flow_memory_recall.py`
calls `_list_artifacts` on a knowledge dir containing a tagged artifact and asserts the
returned dict contains `"tags": ["python", "testing"]`.

**success_signal:** `memory_search` (empty query) shows tags inline next to artifact titles.

**prerequisites:** [TASK-1]

---

### TASK-6 — Add `tags` parameter to `memory_create` and `save_artifact`

**files:**
- `co_cli/tools/memory/write.py`
- `co_cli/memory/service.py`

**Changes:**
- `save_artifact` signature (service.py:125) — add `tags: list[str] | None = None` after `related`.
- `save_artifact` — pass `tags=list(tags or [])` to `KnowledgeArtifact(...)` in:
  - URL-keyed existing-article update path (KnowledgeArtifact at ~service.py:159)
  - URL-keyed new-article path (KnowledgeArtifact at ~service.py:189)
  - Straight-create path (KnowledgeArtifact at ~service.py:261)
- `memory_create` — add `tags: list[str] | None = None` parameter after `decay_protected`.
- `memory_create` call to `save_artifact` (write.py:72) — add `tags=tags`.
- `memory_create` Args docstring — add:
  `tags: Optional list of keyword labels (e.g. ["python", "testing"]).`

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; and a new test calls
`save_artifact(..., tags=["pytest"])` and asserts
`load_knowledge_artifact(result.path).tags == ["pytest"]`.

**success_signal:** Agent can call `memory_create(content=..., artifact_kind=..., tags=["python"])` and the artifact is saved with those tags and searchable by them.

**prerequisites:** [TASK-1]

---

### TASK-7 — Kill `SearchResult.type` dead field

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/memory/service.py`

**Changes:**
- `SearchResult` dataclass (memory_store.py:206) — remove `type: str | None = None` field.
- `store.index()` signature (memory_store.py:384) — remove `type: str | None = None` parameter.
- `store.index()` INSERT (memory_store.py:410) — remove `type` from column list and values tuple.
- `service.py:reindex()` call to `store.index()` (line 116) — remove `type=artifact_kind`.
- `memory_store.py:sync_dir` `self.index(...)` call (line 1177) — remove `type=artifact_kind`.
- Three `SearchResult(...)` constructors (memory_store.py lines 652, 760, 871) — remove `type=None`.
- Leave `type TEXT` in `_SCHEMA_SQL` — existing user DBs have the column; no migration needed.

**done_when:** `uv run pytest` passes; `grep -rn "type=None\|type=artifact_kind\|\.type\b" co_cli/memory/memory_store.py co_cli/memory/service.py` returns no hits outside the schema SQL and the `type TEXT` column definition.

**success_signal:** N/A (internal dead-code removal; no user-visible change).

---

### TASK-8 — Fix `_find_article_by_url` O(n) scan

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/memory/service.py`
- `co_cli/tools/memory/write.py`

**Changes:**
- `MemoryStore` — add public method:
  ```python
  def find_by_source_ref(self, source_ref: str, source: str) -> str | None:
      """Return the path of the doc with the given source_ref, or None."""
      row = self._conn.execute(
          "SELECT path FROM docs WHERE source = ? AND source_ref = ? AND chunk_id = 0",
          (source, source_ref),
      ).fetchone()
      return row["path"] if row else None
  ```
- `_find_article_by_url` signature — add `memory_store: "MemoryStore | None" = None`.
- When `memory_store is not None`:
  ```python
  result = memory_store.find_by_source_ref(origin_url, IndexSourceEnum.KNOWLEDGE)
  return Path(result) if result else None
  ```
  Keep existing file-scan loop as `else` fallback.
- `save_artifact` signature — add `memory_store: "MemoryStore | None" = None` parameter.
- `save_artifact` URL-keyed path (~service.py:152) — update to:
  `existing_path = _find_article_by_url(knowledge_dir, source_url, memory_store=memory_store)`.
- `memory_create` call to `save_artifact` (write.py:72) — add `memory_store=ctx.deps.memory_store`.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; extended tests verify:
1. A second `save_artifact(source_url=URL, memory_store=store)` call (after the first was indexed)
   returns `result.action == "merged"` (O(1) index path exercised).
2. `_find_article_by_url(knowledge_dir, url, memory_store=None)` when a file exists on disk
   returns the correct path (file-scan fallback exercised).

**success_signal:** Repeated `memory_create` calls with the same `source_url` do not degrade as the knowledge store grows.

---

### TASK-10 — Rename `uuid8` → `session_uuid8` in `_search_sessions`

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `recall.py:175` — `session_uuid8 = r.path`
- All subsequent references in the function (lines 176, 179, 180, 186, 188) — rename `uuid8` → `session_uuid8` (excluding `current_uuid8` which is unrelated).

**done_when:** `uv run pytest` passes; `grep -n "uuid8 = r.path" co_cli/tools/memory/recall.py` returns no hits.

**success_signal:** N/A (clarity rename; no user-visible change).

---

### TASK-12 — Fix `memory_read_session_turn` O(n) glob (E1)

**files:**
- `co_cli/tools/memory/read.py`

**Changes:**
- In `memory_read_session_turn`, replace the full-dir glob loop:
  ```python
  for candidate in sessions_dir.glob("*.jsonl"):
      parsed = parse_session_filename(candidate.name)
      if parsed is not None and parsed[0] == session_id:
          jsonl_path = candidate
          break
  ```
  with a targeted glob:
  ```python
  candidates = list(sessions_dir.glob(f"*-{session_id}.jsonl"))
  jsonl_path = candidates[0] if candidates else None
  ```
  (Filename pattern is `YYYY-MM-DD-THHMMSSZ-{uuid8}.jsonl`; the suffix is uniquely keyed.)

**done_when:** `uv run pytest tests/test_flow_memory_lifecycle.py` (or any session-turn test)
passes; new test in `tests/test_flow_memory_recall.py` creates two session JSONL files in
`tmp_path` and verifies that `memory_read_session_turn(session_id=<known uuid8>, ...)`
locates the correct file via the targeted glob (no `parse_session_filename` calls on the
non-matching file — verified by patching the function with a counter or by file count).

**success_signal:** `memory_read_session_turn` lookup latency stays constant as session
count grows.

---

### TASK-13 — Pass sanitized FTS query through `MemoryStore.search()` (E2)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `search()` (memory_store.py:512) — sanitize once, pass through:
  ```python
  fts_query = self._build_fts_query(query)
  if fts_query is None:
      return []
  if self._backend == "hybrid":
      return self._hybrid_search(fts_query, ...)
  ...
  results = self._fts_search(fts_query, ...)
  ```
- `_fts_search` (memory_store.py:603) — accept pre-sanitized `fts_query: str`; drop the
  internal `_build_fts_query(query)` call.
- `_hybrid_search` (memory_store.py:560) — accept pre-sanitized `fts_query`; pass through
  to `_fts_chunks_raw`.
- `_fts_chunks_raw` (memory_store.py:716) — accept pre-sanitized `fts_query: str`;
  drop the internal `_build_fts_query(query)` call.
- Rename the parameter on the affected backends from `query` to `fts_query` for clarity.

**done_when:** `uv run pytest tests/test_flow_memory_search.py` passes; `grep -c
"_build_fts_query" co_cli/memory/memory_store.py` returns 2 (one definition, one call site
in `search()`) — was 4 before.

**success_signal:** N/A (internal optimization; no user-visible change).

---

### TASK-14 — Drop false `asyncio.gather` parallelism in `memory_search` (A1)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- Convert helpers to sync `def`:
  - `_search_artifacts` (recall.py:96) — `def` not `async def`.
  - `_search_sessions` (recall.py:141) — `def` not `async def`.
  - `_search_canon_channel` (recall.py:203) — `def` not `async def`.
- `memory_search` (recall.py:220) — replace:
  ```python
  knowledge_results, session_results_raw, canon_results = await asyncio.gather(
      _search_artifacts(ctx, query, kinds, limit),
      _search_sessions(ctx, query, span),
      _search_canon_channel(ctx, query),
  )
  ```
  with sequential calls:
  ```python
  knowledge_results = _search_artifacts(ctx, query, kinds, limit)
  session_results_raw = _search_sessions(ctx, query, span)
  canon_results = _search_canon_channel(ctx, query)
  ```
- Remove the `import asyncio` line if unused after the change.
- `memory_search` docstring (recall.py:226-) — replace "searches the artifacts channel,
  the sessions channel, and the canon channel in parallel" with "searches the artifacts
  channel, the sessions channel, and the canon channel".

**done_when:** `uv run pytest` passes; `grep -n "asyncio" co_cli/tools/memory/recall.py`
returns no hits (or only legitimate ones); `memory_search` docstring no longer claims "in
parallel".

**success_signal:** Tool documentation accurately reflects execution model — no false
performance claim.

---

### TASK-15 — Make `_browse_recent` return `list[dict]` for symmetry (A2)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `_browse_recent` (recall.py:30) — return `list[dict]` shaped like `_list_artifacts`,
  `_search_sessions`. Drop `tool_output(...)` wrapping.
- `memory_search` empty-query branch (recall.py:292-314) — drop the awkward
  `(sessions_result.metadata or {}).get("results", [])` unpacking; concatenate the two
  `list[dict]`s and call `tool_output(...)` once at the end.
- The session-result dict shape produced by `_browse_recent` keeps the same fields it
  produced as `metadata["results"]`: `channel`, `session_id`, `when`, `title`, `file_size`.
- Update the empty-query display path: build the formatted string in `memory_search` from
  the unified `list[dict]`s (sessions then artifacts).

**done_when:** `uv run pytest` passes; `_browse_recent` signature returns `list[dict]`
(verified by `grep -n "def _browse_recent" co_cli/tools/memory/recall.py`); `memory_search`
no longer references `.metadata` or `.return_value` on a helper return.

**success_signal:** Helpers are composable — empty-query and keyword-query paths share the
same dict shape and formatting logic.

**prerequisites:** [TASK-14]

---

### TASK-16 — `_list_artifacts` uses MemoryStore when available (A3)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- `_list_artifacts` (recall.py:70) — when `ctx.deps.memory_store is not None`, query the
  index instead of disk-scanning:
  ```python
  store = ctx.deps.memory_store
  if store is not None:
      kind_sql = ""
      kind_params: list = []
      if kinds is not None:
          ph = ",".join("?" * len(kinds))
          kind_sql = f" AND kind IN ({ph})"
          kind_params = list(kinds)
      rows = store._conn.execute(
          f"SELECT path, kind, title, content, created, tags "
          f"FROM docs WHERE source = ? AND chunk_id = 0{kind_sql} "
          f"ORDER BY created DESC LIMIT ?",
          ["knowledge", *kind_params, limit],
      ).fetchall()
      return [
          {
              "channel": "artifacts",
              "kind": row["kind"],
              "title": row["title"] or Path(row["path"]).stem,
              "snippet": (row["content"] or "")[:_SNIPPET_DISPLAY_CHARS],
              "score": 0.0,
              "path": row["path"],
              "filename_stem": Path(row["path"]).stem,
              "tags": (row["tags"] or "").split() if row["tags"] else [],
          }
          for row in rows
      ]
  # fallback: disk scan (existing implementation)
  ```
- For consistency with TASK-8's encapsulation principle, prefer adding a thin
  `MemoryStore.list_artifacts(kinds, limit)` public method — implementation owns the SQL,
  caller stays clean. The `_conn.execute(...)` snippet above belongs inside that method.
- The fallback path keeps the existing `load_knowledge_artifacts` + sort-by-created
  behavior unchanged.

**done_when:** `uv run pytest` passes; new test in `tests/test_flow_memory_recall.py`
seeds the knowledge dir + index with three artifacts, calls `_list_artifacts` with
`memory_store` set, and verifies returned dicts match the index (sorted by created desc,
limited correctly). A second test with `memory_store=None` exercises the disk-scan
fallback.

**success_signal:** Empty-query `memory_search` no longer reads every `.md` file from disk
when the index is warm.

**prerequisites:** [TASK-1, TASK-2]

---

### TASK-17 — Drop dead `offset` parameter from `_list_artifacts` (A4)

**files:**
- `co_cli/tools/memory/recall.py`

**Changes:**
- Remove `offset: int = 0` from `_list_artifacts` signature.
- Remove the `[offset : offset + limit]` slice; replace with `[:limit]` (or trust the
  index `LIMIT` clause from TASK-16).
- No callers pass `offset`, so this is a non-breaking removal.

**done_when:** `uv run pytest` passes; `grep -n "offset" co_cli/tools/memory/recall.py`
returns no hits.

**success_signal:** N/A (dead-code removal; no user-visible change).

**prerequisites:** [TASK-16]

---

### TASK-18 — Evaluate `_hybrid_merge` doc-level aggregation: max vs sum (A5)

**files:**
- `co_cli/memory/memory_store.py`
- `evals/` (existing recall eval scripts)

**Changes:**
- Run an existing recall eval (`ls evals/eval_*recall*.py` to identify the right script)
  twice on the same dataset:
  1. Baseline — current `doc_rrf[path] = max(doc_rrf.get(path, 0.0), score)`.
  2. Variant — change to `doc_rrf[path] = doc_rrf.get(path, 0.0) + score` (sum).
- Compare recall@k (and any other ranking metrics the eval reports) between the two runs.
- Decision rule:
  - If `sum` improves recall@k by ≥ 5% on the headline metric → adopt `sum` and update the
    `_hybrid_merge` docstring to reflect the change and rationale.
  - If results are within ±5% (noise) → keep `max` and add an inline comment in
    `_hybrid_merge` documenting that the choice is intentional after eval comparison.
  - If `sum` regresses → keep `max`, add the same comment.
- Either branch produces an artifact: a `docs/REPORT-rrf-aggregation-<date>.md` summary
  with the eval numbers, the chosen rule, and the rationale.

**done_when:** Eval comparison run; `docs/REPORT-rrf-aggregation-*.md` exists with the
comparison numbers; either the code is updated to `sum` and tests pass, or `_hybrid_merge`
gains a comment documenting the `max` choice as intentional. `uv run pytest` passes.

**success_signal:** Document-level RRF aggregation is justified by data, not by historical
accident.

**prerequisites:** [] (independent — can run in parallel with other tasks)

---

### TASK-20 — Delete dead `SearchResult.to_tool_output` method (O1)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- Remove the `to_tool_output(self, *, conflict: bool = False) -> dict` method from `SearchResult` (memory_store.py:211).

**done_when:** `uv run pytest` passes; `grep -rn "to_tool_output" co_cli/ tests/` returns no hits.

**success_signal:** N/A (dead-method removal).

---

### TASK-21 — Remove unreachable doc-level branch in `_fetch_reranker_texts` (O3)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- User-confirmed dead: every `SearchResult` constructed by the search backends sets `chunk_index=row["chunk_index"]` from a chunks-table row; `chunk_index is None` is unreachable.
- In `_fetch_reranker_texts` (memory_store.py:1004), drop the `doc_level` branch (the `chunk_index is None` filter, the batched `SELECT path, title, content FROM docs` query, the `doc_texts` dict). Keep only the chunk-level path.
- Final loop simplifies to: `texts = [...for r in candidates: f"{r.title or ''}\n{chunk_texts.get((r.source, r.path, r.chunk_index), '')}".strip() or r.title or ""]`.

**done_when:** `uv run pytest` passes; `grep -n "doc_level\|chunk_index is None" co_cli/memory/memory_store.py` returns no hits in `_fetch_reranker_texts`.

**success_signal:** N/A (dead-branch removal; reranking still works for chunk-level results — the only kind that exists).

---

### TASK-22 — Replace DELETE+INSERT with `INSERT ON CONFLICT DO UPDATE` (O4)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `index()` (memory_store.py:371-429) — replace the SELECT-then-DELETE-then-INSERT sequence with:
  ```python
  self._conn.execute(
      """INSERT INTO docs
             (source, kind, path, title, content, mtime, hash, tags, category,
              created, updated, description, source_ref, artifact_id, chunk_id)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
         ON CONFLICT(source, path, chunk_id) DO UPDATE SET
             kind=excluded.kind, title=excluded.title, content=excluded.content,
             mtime=excluded.mtime, hash=excluded.hash, tags=excluded.tags,
             category=excluded.category, created=excluded.created,
             updated=excluded.updated, description=excluded.description,
             source_ref=excluded.source_ref, artifact_id=excluded.artifact_id
         WHERE excluded.hash IS NOT docs.hash""",
      (...),
  )
  self._conn.commit()
  ```
  The `WHERE excluded.hash IS NOT docs.hash` clause preserves the hash-skip behavior (no-op write when content unchanged).
- The unique constraint `UNIQUE(source, path, chunk_id)` already exists at memory_store.py:64 — required by `ON CONFLICT`.

**done_when:** `uv run pytest tests/test_flow_memory_write.py` passes; `grep "DELETE FROM docs WHERE source = ? AND path = ?" co_cli/memory/memory_store.py` returns no hits. Two assertions on hash-skip behavior:
  1. Re-indexing identical content does NOT change the `mtime` value in the `docs` row (the `WHERE excluded.hash IS NOT docs.hash` clause must short-circuit the UPDATE — verify by reading mtime before and after a no-op re-index).
  2. Re-indexing changed content DOES update the row (regression check on the active path).

**success_signal:** N/A (atomic upsert; no inconsistent intermediate state visible to readers).

**prerequisites:** [TASK-2, TASK-7] (column list must be final before rewriting INSERT)

**Gate 1 note:** The `WHERE` clause with both `excluded` and table-name references is supported by SQLite ≥3.24 (UPSERT was introduced there). Confirm minimum SQLite version is met before merging.

---

### TASK-23 — Remove `**_kwargs` catch-all from `index()` (O5)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- `index()` signature (memory_store.py:371-388) — remove `**_kwargs: object`.
- After T2/T7/T22, all callers pass only declared keyword arguments; the catch-all silently swallows typos.

**done_when:** `uv run pytest` passes; `grep -n "_kwargs" co_cli/memory/memory_store.py` returns no hits in `index()`.

**success_signal:** A misspelled kwarg in a future call site fails immediately with `TypeError` instead of silently being ignored.

**prerequisites:** [TASK-2, TASK-7]

---

### TASK-24 — Name the retrieval-pool constants and fix compounding (O6)

**files:**
- `co_cli/memory/memory_store.py`

**Changes:**
- Promote `limit * 20` and `limit * 4` to module-level named constants with rationale comments:
  ```python
  _CHUNK_DEDUP_FETCH_MULTIPLIER = 20
  """Chunks fetched per requested doc — dedup by path collapses many chunks per doc."""
  _RERANKER_CANDIDATE_MULTIPLIER = 4
  """Reranker pool size — gives the reranker meaningful signal to reorder."""
  ```
- `_fts_search`: `chunks_fetch_limit = limit * _CHUNK_DEDUP_FETCH_MULTIPLIER`.
- `_fts_chunks_raw`: same.
- `_hybrid_search`: `_fts_chunks_raw(query, ..., limit=limit * _RERANKER_CANDIDATE_MULTIPLIER)`.
- **Fix the compounding bug:** today the hybrid path asks `_fts_chunks_raw` for `limit*4`, and `_fts_chunks_raw` internally inflates to `(limit*4)*20 = limit*80` chunks. Either:
  - Pass `pool_limit` directly into `_fts_chunks_raw` (caller decides), OR
  - Have `_fts_chunks_raw` not apply the chunk-dedup multiplier when the caller has already inflated.
  Pick one and document the choice with a comment.

**done_when:** `uv run pytest tests/test_flow_memory_search.py` passes (no regression on rank quality); `grep "limit \* 20\|limit \* 4" co_cli/memory/memory_store.py` returns no hits — only the named constants.

**success_signal:** Pool-size choices are documented and traceable; hybrid-mode chunk fetch no longer secretly inflates 80×.

---

### TASK-26 — Eval-driven trim of `sanitize_fts5_query` (O8)

**files:**
- `co_cli/memory/search_util.py`
- `evals/` (existing FTS / search eval scripts)

**Changes:**
- Run an existing search eval that exercises diverse LLM-issued queries against the current `sanitize_fts5_query` (6-step pipeline). Capture baseline metrics.
- Try a stripped variant: keep step 1 (quote protection), step 2 (operator stripping), step 6 (quote restoration). Drop step 3 (star collapse), step 4 (dangling boolean removal), step 5 (auto-quote compound terms) — the failure modes these handle are rare in LLM-issued queries.
- Compare metrics. Decision rule:
  - If stripped variant matches or improves quality → adopt and remove the dropped steps.
  - If quality regresses on representative queries → keep current pipeline and add a comment in the function noting that the steps are eval-validated.
- Either branch produces `docs/REPORT-fts-sanitize-<date>.md` with the comparison.

**done_when:** REPORT exists; either the function is trimmed (and tests still pass) or a justification comment is added. `uv run pytest` passes.

**success_signal:** N/A (internal hardening / simplification — no user-visible change either way).

**prerequisites:** []

---

### TASK-27 — Drop redundant traversal defense in `search_canon` (O9)

**files:**
- `co_cli/tools/memory/_canon_recall.py`
- `CHANGELOG.md`

**Changes:**
- Remove the `if ".." in role or "/" in role or "\\" in role: return []` early-return at `_canon_recall.py:46-47`.
- Keep the `try: role_dir.relative_to(base) except ValueError: return []` defense at lines 55-58 — that one check is sufficient and catches the same attack class.
- `CHANGELOG.md` — note the change under "internal cleanup": removed redundant string-level traversal check from `search_canon`; the path-resolution check (`relative_to(base)`) remains and catches the same attack class. Trusted-input boundary (config-derived role string).

**done_when:** `uv run pytest` passes; an existing canon test (or new one) confirms `search_canon(query, role="../escape", limit=...)` still returns `[]` (because the resolved path is outside `base`); CHANGELOG entry exists.

**success_signal:** N/A (defense-in-depth pruned for trusted-input boundary).

**Gate 1 note:** Security-adjacent change. CHANGELOG entry ensures the simplification is visible at review time, not buried in a 25-task list.

---

### TASK-28 — Single-transaction `index_session`; remove partial-write recovery (O10)

**files:**
- `co_cli/memory/memory_store.py`

**Approach (preferred — keeps outer API intact):**
- Refactor `index()` and `index_chunks()` so the SQL execution lives in private `_index_no_commit(...)` and `_index_chunks_no_commit(...)` helpers. The public `index()` / `index_chunks()` keep their current behavior — they call the helper, then commit. `index_session` calls the no-commit helpers under a single `with self._conn:` block.
- This avoids changing the commit semantics of `index()` / `index_chunks()` for any other caller.

**Caller audit before changing inner commits:** Run `grep -n "self\.index(\|store\.index(\|self\.index_chunks(\|store\.index_chunks(" co_cli/` and confirm the public callers are:
- `service.py:reindex()` — calls `store.index()` then `store.index_chunks()` (knowledge artifacts).
- `memory_store.py:sync_dir()` — calls `self.index()` per file (Obsidian sync).
- `memory_store.py:index_session()` — the function being modified.

If any other caller exists and depends on the implicit commit boundary, the no-commit-helper approach above leaves them untouched.

**Changes:**
- `index_session` (memory_store.py:1197-1255) — wrap the calls in one transaction:
  ```python
  with self._conn:  # SQLite implicit BEGIN/COMMIT
      self._index_no_commit(...)
      self._index_chunks_no_commit(...)
  ```
- After the atomic write, the `chunk_count` recovery query at memory_store.py:1227-1233 is no longer needed (torn writes are impossible). Delete the `if chunk_count > 0: return` check; keep only `if not self.needs_reindex(...): return`.

**done_when:** `uv run pytest` passes; `grep "SELECT COUNT.*chunks WHERE source='session'" co_cli/memory/memory_store.py` returns no hits in `index_session`. The caller audit above is included in the delivery summary.

**success_signal:** Session reindex on warm cache is one SQL query (`needs_reindex`) instead of two; no recovery branch.

**prerequisites:** []

---

### TASK-30 — Document or unify snippet-size constants (O12)

**files:**
- `co_cli/memory/memory_store.py`
- `co_cli/tools/memory/recall.py`

**Changes:**
- Add a module-level constant block in `memory_store.py` (or a small `_constants.py`) with named values:
  ```python
  _FTS_SNIPPET_TOKENS = 40       # passed to FTS5 snippet() — context window for highlighting
  _RERANKER_PREAMBLE_CHARS = 200 # truncation in _fetch_reranker_texts — rerank input length
  ```
- `recall.py` — `_SNIPPET_DISPLAY_CHARS = 100` keeps its current name and adds a one-line comment: `# user-facing snippet truncation in tool output`.
- Replace the inline `40` in `snippet(chunks_fts, 0, '>', '<', '...', 40)` with the named constant. Replace the inline `[:200]` in `_fetch_reranker_texts` with the named constant.

**done_when:** `uv run pytest` passes; `grep -n "snippet(chunks_fts" co_cli/memory/memory_store.py` shows the named constant in place of `40`; `grep -n "\[:200\]" co_cli/memory/memory_store.py` shows the named constant.

**success_signal:** N/A (constants are documented; future tweaks have a single rationale to update).

---

### TASK-11 — Full test suite gate

**files:** (no new files)

Run the full suite; fix any failures before marking done:
```bash
uv run pytest -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-recall-cleanup.log
```

**done_when:** `uv run pytest` exits 0.

**success_signal:** N/A (gate task).

**prerequisites:** [TASK-1, TASK-2, TASK-3, TASK-4, TASK-5, TASK-6, TASK-7, TASK-8, TASK-10, TASK-12, TASK-13, TASK-14, TASK-15, TASK-16, TASK-17, TASK-18, TASK-20, TASK-21, TASK-22, TASK-23, TASK-24, TASK-26, TASK-27, TASK-28, TASK-30]

---

## Testing

New tests go in `tests/test_flow_memory_write.py` (extended, for TASK-1/2/3/6/8/22) and a
new `tests/test_flow_memory_recall.py` (for TASK-4/5/12/14/15/16/27). No mocks — real
filesystem + real MemoryStore (SQLite FTS5) only.

`test_flow_memory_lifecycle.py` and `test_flow_memory_search.py` require no changes unless
a regression is caught by the suite gate.

Tasks without dedicated tests (covered by suite gate only):
- TASK-13 (sanitization pass-through) — verified by `test_flow_memory_search.py`.
- TASK-17, TASK-20, TASK-21, TASK-23 — dead-code/dead-branch removal.
- TASK-24, TASK-30 — constants refactor.
- TASK-28 — single-transaction wrap; existing session-index test covers correctness.

Tasks producing REPORT artifacts (not unit tests):
- TASK-18 — `docs/REPORT-rrf-aggregation-<date>.md`.
- TASK-26 — `docs/REPORT-fts-sanitize-<date>.md` (eval gate).

---

## Open Questions

None — all questions answered by code inspection.

## Final — Team Lead

Plan approved (post-Gate-1 trim — three tasks spun out to their own plans; see
"Spun-out plans" in the Phase 3 findings block).

> Gate 1 — PO review complete. Trim applied: TASK-9 dropped, TASK-19 / TASK-25 / TASK-29
> spun out to separate plans. Remaining 25 tasks are recall-path cleanup.
> Once approved, run: `/orchestrate-dev knowledge-recall-path-cleanup`
