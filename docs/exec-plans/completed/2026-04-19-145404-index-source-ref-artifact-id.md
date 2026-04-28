# Plan: Index `source_ref` and `artifact_id` in the Knowledge Store

**Task type:** `refactor` — additive DB schema change + code-path simplification. Public surface (`search_knowledge(kind="article")`) is behavior-preserving.

## Context

**Current state (verified against the live codebase):**

- `co_cli/knowledge/_store.py:45` — `docs` table columns: `source, kind, path, title, content, mtime, hash, tags, category, created, updated, provenance, certainty, chunk_id, type, description`. **Missing:** `source_ref` and `artifact_id`.
- `co_cli/knowledge/_store.py:157` — `SearchResult` dataclass mirrors the `docs` columns. Same fields missing.
- `co_cli/knowledge/_store.py:327` — `KnowledgeStore.index()` signature accepts the existing columns only; takes a `**_kwargs: object` swallow that silently drops `source_ref`/`id` if a caller tries to pass them.
- `co_cli/knowledge/_store.py:362` — INSERT statement projects existing columns only.
- `co_cli/knowledge/_store.py:134` — `_CHUNKS_FTS_SQL` and the `_fts_search` SELECT (around line 588) project existing columns only into `SearchResult`.
- `co_cli/knowledge/_store.py:844` — vec-search path's doc-meta SELECT also projects existing columns only.
- `co_cli/knowledge/_store.py` — `sync_dir()` (definition at line 1145, `self.index(...)` call at line 1190) parses frontmatter (`fm`) and calls `index()` with existing fields. `fm["source_ref"]` and `fm["id"]` are read into the local `fm` dict but never forwarded.
- `co_cli/knowledge/mutator.py:46` — `_reindex_knowledge_file` calls `index()` with existing fields. Has the full `fm` dict — does not forward `source_ref`/`id`.
- `co_cli/knowledge/_dream.py` — `_write_consolidated_artifact` (definition at line 300, `store.index(...)` call at line 335) calls `index()` with existing fields. Has access to `merged_artifact.id`; consolidated artifacts have `source_ref=None`.
- `co_cli/tools/google/drive.py:172` — Drive indexing calls `index()` with existing fields. Drive docs have no `source_ref`/`artifact_id` semantics.

**Workaround in `co_cli/tools/knowledge.py`:**

- `_fts_search_articles` (line 684) calls `store.search()`, then loops through results and **opens each frontmatter file** to extract `id` and `source_ref` because `SearchResult` doesn't carry them. This is the only reason the article-specific FTS helper exists as a distinct code path — the rest is just output-shape transformation.
- `_grep_search_articles` (line 754) already reads the full file (it's the no-FTS path), so the per-hit file read is intrinsic there — not a workaround.
- `_find_article_by_url` (line 881) does a directory scan reading every `*.md` to dedup by `source_ref`. Same root cause. Out of scope here (separate dedup concern).

**Why this matters:**

Per-hit file I/O during FTS search is a correctness smell — the DB indexed the file but cannot return its identity fields. With `source_ref` and `artifact_id` projected on `SearchResult`, the article-index FTS path collapses into "call `store.search()`, format results" with no I/O beyond the FTS query itself. The article-specific helper becomes optional (kept only if it improves readability).

## Problem & Outcome

**Problem:** `SearchResult` is missing `source_ref` and `artifact_id`, two general-purpose `KnowledgeArtifact` identity fields that the index already has access to at write time. The schema gap forces a kind-specific helper to exist solely to paper over the missing identity fields, fragmenting the FTS code path. Any future kind-specific output mode (`kind="reference"`, `kind="decision"`, etc.) will reconstruct the same workaround for the same reason.

**Failure cost:** Structural — every new kind-specific continuation schema rebuilds the per-hit-file-read pattern, accumulating duplicate helpers around the same root cause. Concrete manifestation today: each `search_knowledge(kind="article")` call performs N extra disk reads where N = result count (default 10), one stat + open + parse per hit on top of the FTS query.

**Outcome:** `source_ref` and `artifact_id` are persisted in the `docs` table for knowledge-source rows and exposed on `SearchResult`. The article-index FTS path uses these fields directly with zero per-hit file I/O. Existing DBs migrate transparently via additive `ALTER TABLE`. The public `search_knowledge(kind="article")` contract is unchanged.

## Scope

**In scope:**

1. Add nullable `source_ref TEXT` and `artifact_id TEXT` columns to the `docs` table.
2. Idempotent migration for existing DBs (`ALTER TABLE … ADD COLUMN IF NOT EXISTS` semantics via `PRAGMA table_info` introspection).
3. Extend `KnowledgeStore.index()` signature and INSERT to accept and persist the two fields.
4. Project the two fields in every SELECT that builds `SearchResult` (docs FTS, chunks FTS, vec doc-meta join).
5. Add the two fields to `SearchResult`.
6. Update knowledge-source upsert call sites to forward `fm["source_ref"]` and `fm["id"]`: `sync_dir`, `mutator._reindex_knowledge_file`, `_dream._write_consolidated_artifact`.
7. Drop the per-hit frontmatter read in `_fts_search_articles`; populate `article_id`/`origin_url` from `SearchResult` directly.
8. Tests covering: (a) FTS search returns populated `source_ref`/`artifact_id` for new rows; (b) the article-index continuation schema is unchanged for `search_knowledge(kind="article")`; (c) backwards compat — pre-migration rows with NULL columns still return valid output (article-index emits empty string / None for those fields).

**Out of scope:**

- Indexing `source_ref`/`artifact_id` for non-knowledge sources (Drive, Obsidian — they have no semantically meaningful values to populate). Drive's `file_id` already serves as its identity key via the existing `path` column, so NULL `source_ref`/`artifact_id` for Drive rows is semantically correct, not a gap.
- Replacing `_find_article_by_url` directory scan with a DB query (separate dedup concern; the dedup path is not on the hot search path).
- Removing or further consolidating `_grep_search_articles` (the grep fallback is intrinsically file-reading; no benefit from this change).
- Reranker / hybrid retrieval correctness changes.
- Spec updates as explicit plan tasks (`sync-doc` is an output of delivery, not an input).
- Repacking the FTS5 index — the new columns are not part of the FTS virtual table content, so no FTS rebuild is required.

## Behavioral Constraints

1. **Public contract preserved:** `search_knowledge(kind="article")` returns the same continuation schema as today (`{article_id, title, origin_url, tags, snippet, slug}`). Field semantics unchanged.
2. **Generic schema preserved:** `SearchResult.to_tool_output()` still returns the existing `{source, kind, title, snippet, score, path, confidence, conflict}` shape. The new fields do not leak into the generic output.
3. **Migration is non-destructive:** existing DBs gain the new columns via `ALTER TABLE ADD COLUMN`. No row deletion. No FTS rebuild. No version bump in the schema marker beyond what's needed.
4. **Backwards compat:** rows indexed before this change have NULL `source_ref` and NULL `artifact_id` until the file is re-indexed. The article-index path must tolerate NULL — falling back to `""` for `origin_url` and `None` for `article_id`, mirroring current grep-path behavior for missing values.
5. **No silent re-index on startup:** existing DB rows are not force-re-indexed; they update naturally on next file change (hash mismatch in `sync_dir`) or never if the file is unchanged. Acceptable because the article-index path tolerates NULL.
6. **Drive and Obsidian indexing unchanged:** call sites that don't have these fields keep passing the existing args; `index()` defaults the new params to `None`.

## High-Level Design

### Schema change

```sql
ALTER TABLE docs ADD COLUMN source_ref  TEXT;
ALTER TABLE docs ADD COLUMN artifact_id TEXT;
```

Applied idempotently in `KnowledgeStore.__init__` after `executescript(_SCHEMA_SQL)`. Schema introspection via `PRAGMA table_info(docs)` to skip already-present columns. The `_SCHEMA_SQL` `CREATE TABLE IF NOT EXISTS` block is updated to include the new columns for fresh DBs.

### `KnowledgeStore.index()` signature

Add two keyword-only params with `None` defaults:

```python
def index(
    self,
    *,
    source: str,
    ...,
    source_ref: str | None = None,
    artifact_id: str | None = None,
    **_kwargs: object,
) -> None:
```

INSERT statement adds the two columns. Defaulting to `None` preserves call-site compatibility for Drive (which doesn't pass them).

### `SearchResult`

Add two optional fields:

```python
@dataclass
class SearchResult:
    ...
    source_ref:  str | None = None
    artifact_id: str | None = None
```

`to_tool_output()` does **not** add these to the generic output dict. They are accessed directly by callers that need them (article-index formatter).

### SELECT projections

Three SELECTs build `SearchResult`:

- `_CHUNKS_FTS_SQL` (line 134) — joins `docs d`; add `d.source_ref, d.artifact_id` to the projection. Builders at lines 640 and 753 forward them.
- vec-search doc-meta join (line 844) — add the two columns to the SELECT and the `SearchResult(...)` construction at line 879.

### Article-index simplification

`_fts_search_articles`:

```python
for r in fts_results:
    article_id = r.artifact_id
    origin_url = r.source_ref or ""
    title = r.title or (Path(r.path).stem if r.path else "")
    tags_list = r.tags.split() if r.tags else []
    result_dicts.append({
        "article_id": article_id,
        "title": title,
        "origin_url": origin_url,
        "tags": tags_list,
        "snippet": r.snippet,
        "slug": Path(r.path).stem if r.path else "",
    })
```

No `Path(r.path).read_text()`. No `parse_frontmatter()`. `r.source_ref or ""` matches the grep-path behavior at `co_cli/tools/knowledge.py:788` and Behavioral Constraint #4.

The helper remains as a named function — this is a deliberate kind-specific output-shape seam. Future continuation schemas (e.g. `kind="reference"`) plug into the same shape: one helper per kind-specific output mode keeps `search_knowledge`'s body free of cascading conditionals.

### Upsert callers

| Call site | Has `source_ref`? | Has artifact `id`? | Action |
|-----------|-------------------|--------------------|--------|
| `sync_dir` (`_store.py:1145`, index call at line 1190) | Yes (`fm["source_ref"]`) | Yes (`fm["id"]`) | Forward both; stringify `id` |
| `_reindex_knowledge_file` (`mutator.py:46`) | Yes (`fm["source_ref"]`) | Yes (`fm["id"]`) | Forward both; stringify `id` |
| `_write_consolidated_artifact` (`_dream.py:300`, index call at line 335) | No (always None for consolidated) | Yes (`merged_artifact.id`) | Forward `artifact_id=str(merged_artifact.id)`; pass `source_ref=None` explicitly for clarity |
| Drive `index()` (`drive.py:172`) | N/A | N/A | No change — defaults handle it |

**Stringifying `fm["id"]`:** `co_cli/knowledge/_frontmatter.py:83` validates `fm["id"]` as `int | str`. Existing convention (`_artifact.py:79`, `tools/knowledge.py:912`) is `str(fm["id"])`. The plan keeps `artifact_id: str | None` on `index()` and stringifies at every call site: `str(fm["id"]) if fm.get("id") is not None else None`.

## Implementation Plan

### ✓ DONE — TASK-1 — Schema: add columns + idempotent migration

```text
files:
  - co_cli/knowledge/_store.py

done_when: >
  PRAGMA table_info(docs) on a freshly-opened DB returns rows including
  source_ref and artifact_id. AND opening an existing DB built without these
  columns adds them via ALTER TABLE on next __init__ without raising.

success_signal: N/A

prerequisites: []
```

**Implementation notes:**

- Update `_SCHEMA_SQL` `CREATE TABLE` to include `source_ref TEXT` and `artifact_id TEXT` (both nullable).
- After `executescript(_SCHEMA_SQL)` in `__init__`, run `PRAGMA table_info(docs)`, collect existing column names, and `ALTER TABLE docs ADD COLUMN <name> TEXT` for each missing one.
- When a column is actually added (not skipped), emit one INFO log line per added column: `logger.info("KnowledgeStore: migrated docs table — added column %s", name)`. Skip the log when the column already exists.
- Wrap the migration loop in a single try/except that logs and re-raises on failure (DB integrity issue should fail-fast).

### ✓ DONE — TASK-2 — Persist new fields in `KnowledgeStore.index()` + `sync_dir`

```text
files:
  - co_cli/knowledge/_store.py

done_when: >
  KnowledgeStore.index(..., source_ref="https://x", artifact_id="abc") followed by
  a SELECT source_ref, artifact_id FROM docs WHERE path=? returns ("https://x", "abc").
  AND sync_dir() called on a directory containing an article whose frontmatter has
  source_ref and id persists those values to docs.

success_signal: N/A

prerequisites: [TASK-1]
```

**Implementation notes:**

- Add `source_ref: str | None = None` and `artifact_id: str | None = None` to the `index()` signature (keyword-only).
- Update the INSERT statement and value tuple to include the two new columns.
- In `sync_dir` (call site line 1190), forward `source_ref=fm.get("source_ref")` and `artifact_id=str(fm["id"]) if fm.get("id") is not None else None`. The `str(...)` cast matches `_artifact.py:79` and `tools/knowledge.py:912` — `_frontmatter.py:83` allows `fm["id"]` to be `int | str`, but the DB column is `TEXT` and downstream readers expect a `str`.
- The existing `**_kwargs: object` swallow stays — anything we don't model still gets dropped silently, as before. **Trap:** under the new signature, a caller mistakenly passing `id=` (instead of `artifact_id=`) silently loses the value. This is unchanged behavior, but the new param name makes the trap easier to hit; tightening `**_kwargs` to reject unknowns is left as a follow-up.

### ✓ DONE — TASK-3 — Expose new fields on `SearchResult` + project in all SELECTs

```text
files:
  - co_cli/knowledge/_store.py

done_when: >
  SearchResult dataclass has source_ref and artifact_id fields (both Optional[str], default None).
  AND a store.search(...) call configured with the FTS5 backend against a DB row with
  populated source_ref/artifact_id returns a SearchResult whose .source_ref and
  .artifact_id match the persisted values.
  AND SearchResult.to_tool_output() does not include source_ref or artifact_id keys.

success_signal: N/A

prerequisites: [TASK-2]
```

**Implementation notes:**

- Add the two fields to the `SearchResult` dataclass at line 157.
- Update `_CHUNKS_FTS_SQL` projection (line 134) to include `d.source_ref, d.artifact_id`.
- Update the chunks-FTS `SearchResult(...)` constructor at line 640 and the second one at line 753 to pass the two new fields from the row.
- Update the vec-search doc-meta SELECT at line 844 to project `source_ref, artifact_id` and pass them in the `SearchResult(...)` constructor at line 879.
- `to_tool_output()` is **not** modified — generic output stays compatible.
- The `done_when` only verifies the FTS5 backend explicitly. The hybrid (vec) path is structurally updated by the same edit and is exercised end-to-end by the integration test in TASK-6 when a hybrid environment is available; otherwise the SQL/constructor parity is the structural guarantee.

### ✓ DONE — TASK-4 — Forward fields from non-`sync_dir` upsert callers

```text
files:
  - co_cli/knowledge/mutator.py
  - co_cli/knowledge/_dream.py

done_when: >
  After invoking _reindex_knowledge_file(ctx, path, body, md_content, fm, slug)
  with fm = {"id": "A", "source_ref": "https://x", "artifact_kind": "article",
  "title": "T", "tags": []},
  SELECT artifact_id, source_ref FROM docs WHERE path = ? returns ("A", "https://x").
  AND after _write_consolidated_artifact runs and writes a merged artifact,
  SELECT artifact_id, source_ref FROM docs WHERE path = <new path> returns
  (str(merged_artifact.id), NULL).

success_signal: N/A

prerequisites: [TASK-2]
```

**Implementation notes:**

- `mutator.py:46` — extend the `store.index(...)` call with `source_ref=fm.get("source_ref")` and `artifact_id=str(fm["id"]) if fm.get("id") is not None else None`.
- `_dream.py:335` — extend the `store.index(...)` call with `artifact_id=str(merged_artifact.id), source_ref=None`. Explicit `None` for `source_ref` documents the intent (consolidated artifacts have no origin URL).
- Drive call site (`tools/google/drive.py:172`) is intentionally untouched — see Out of scope.

### ✓ DONE — TASK-5 — Drop per-hit file read in `_fts_search_articles`

```text
files:
  - co_cli/tools/knowledge.py

done_when: >
  _fts_search_articles no longer calls Path(r.path).read_text() or
  parse_frontmatter() inside the per-hit loop.
  AND the result dicts populate article_id from r.artifact_id and
  origin_url from r.source_ref.
  AND search_knowledge(kind="article", source="knowledge") still returns the
  contracted continuation schema {article_id, title, origin_url, tags, snippet, slug}.

success_signal: >
  uv run pytest tests/test_articles.py -v

prerequisites: [TASK-3, TASK-4]
```

**Implementation notes:**

- Remove the `if r.path: try: raw = Path(r.path).read_text(...); fm_data, _ = parse_frontmatter(raw)` block.
- Replace the `fm_data.get("id")` / `fm_data.get("source_ref")` reads with `r.artifact_id` / `r.source_ref or ""`. The `or ""` for `origin_url` matches the grep-path behavior at `tools/knowledge.py:788` and Behavioral Constraint #4 — without it, FTS and grep paths return heterogeneous null shapes.
- For `tags_list`, use `r.tags.split() if r.tags else []` (the FTS row stores tags as space-separated strings; this matches `sync_dir`'s `tags_str = " ".join(tags_list)` at `_store.py:1187`). **Assumption:** no tag contains whitespace — project convention but not enforced; flagged here so a future tag-policy change knows to revisit.
- For `title`, keep `r.title or (Path(r.path).stem if r.path else "")` — no frontmatter fallback needed.
- Preserve NULL tolerance: `r.artifact_id` may be `None` (pre-migration row); the existing `read_article` flow handles `article_id=None` already.
- Do NOT remove the `parse_frontmatter` import from `tools/knowledge.py` — it remains in use at multiple other call sites in the same module (`read_article`, `save_article`, `_find_article_by_url`, etc.).

### ✓ DONE — TASK-6 — Tests

```text
files:
  - tests/test_knowledge_tools.py
  - tests/test_articles.py

done_when: >
  Test asserts store.search(...) returns SearchResult with non-None source_ref and
  artifact_id for an article indexed via save_article (FTS path).
  AND test asserts SearchResult.to_tool_output() output dict does NOT contain
  source_ref or artifact_id keys (generic schema unchanged).
  AND test asserts search_knowledge(kind="article", source="knowledge") returns the
  article-index continuation schema {article_id, title, origin_url, tags, snippet, slug}
  for an FTS-indexed article.
  AND test asserts the article-index path tolerates a row with NULL source_ref/
  artifact_id by manually inserting such a row and confirming the result emits
  origin_url="" and article_id=None without raising.
  AND test asserts the migration path: open a DB whose docs table was created without
  the new columns, re-open via KnowledgeStore, and confirm PRAGMA table_info(docs)
  now lists source_ref and artifact_id.

success_signal: >
  uv run pytest tests/test_knowledge_tools.py tests/test_articles.py -v

prerequisites: [TASK-5]
```

**Implementation notes:**

- Use real `KnowledgeStore` instances against `tmp_path`, no fakes.
- For the NULL-row backwards-compat test: open the same `tmp_path / "search.db"` directly via `sqlite3`, insert a `docs` row with NULL `source_ref`/`artifact_id` (and the FTS triggers will mirror it), then call `store.search()` and assert the `SearchResult` has those fields as None.
- For the schema-unchanged test on `to_tool_output()`: build a `SearchResult` with populated `source_ref`/`artifact_id` and assert the dict keys equal the existing set.
- For the migration-path test: create a SQLite DB file directly via `sqlite3.connect`, run a `CREATE TABLE docs (...)` with only the legacy columns, close the connection, then open a `KnowledgeStore` pointing at that path. Assert that `PRAGMA table_info(docs)` post-init includes both new column names. This is the only behavioral change pre-existing users will experience and deserves direct coverage.

## Testing

Focused dev test:

```bash
mkdir -p .pytest-logs
uv run pytest tests/test_articles.py tests/test_knowledge_tools.py tests/test_knowledge_archive.py tests/test_bootstrap.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-source-ref.log
```

Full regression gate before shipping:

```bash
mkdir -p .pytest-logs
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full.log
```

Targeted verification points:

- Fresh DB: `PRAGMA table_info(docs)` lists `source_ref` and `artifact_id`.
- Pre-existing DB without the columns: opens, gains them, no data loss.
- An article saved via `save_article()` produces a `SearchResult` with populated `source_ref` and `artifact_id`.
- `search_knowledge(kind="article", source="knowledge")` continuation schema is byte-identical to the pre-change shape for an article with all fields set.
- A row with NULL columns still produces a valid result dict (graceful fallback).

## Open Questions

None blocking. The migration approach (idempotent `ALTER TABLE ADD COLUMN`) is straightforward and the additive nature means rollback is non-destructive (the columns can be left in place if reverted; old code ignores them).

---

## Final — Team Lead

Plan approved.

## Gate 1 — PO Review (2026-04-19)

Right problem: Yes. Schema gap forces N per-hit disk reads as workaround; root cause is clear and the fix is structurally correct.

Correct scope: Yes. Additive schema change, public contract preserved, Drive/Obsidian exclusion principled, deferred items are genuinely orthogonal.

Non-blocking flag: `**_kwargs` silent-drop trap (caller passes `id=` instead of `artifact_id=`) is documented in TASK-2; acceptable since it's pre-existing. Tighten before adding more callers.

**Verdict: APPROVED.** Run `/orchestrate-dev 2026-04-19-145404-index-source-ref-artifact-id`

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `evals/eval_article_fetch_flow.py` | Imported deleted `search_articles`; two call sites used old function signature | blocking | TASK-5 |

**Overall: 1 blocking (fixed before delivery)**

Blocking finding resolved: eval updated to use `search_knowledge(..., kind="article", source="knowledge")` at all call sites.

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | PRAGMA table_info(docs) lists source_ref and artifact_id; existing DB migrates via ALTER TABLE | ✓ pass |
| TASK-2 | index(..., source_ref=, artifact_id=) persists to docs; sync_dir forwards from frontmatter | ✓ pass |
| TASK-3 | SearchResult has source_ref/artifact_id; all SELECTs project them; to_tool_output() excludes them | ✓ pass |
| TASK-4 | mutator._reindex_knowledge_file and _dream._write_consolidated_artifact forward identity fields | ✓ pass |
| TASK-5 | _fts_search_articles has no Path.read_text/parse_frontmatter per-hit; uses r.artifact_id/r.source_ref | ✓ pass |
| TASK-6 | 4 new tests in test_knowledge_tools.py; test_articles.py strengthened assertion | ✓ pass |

**Tests:** full suite — 541 passed, 1 failed (`test_web_fetch_plain_text` — transient network timeout, pre-existing, unrelated to this delivery; prior run shows `outcome=passed | duration=0.22s`)
**Independent Review:** 1 blocking (fixed) — eval ImportError for deleted `search_articles`
**Doc Sync:** fixed — removed stale `search_articles` from `tools.md` catalog + API definitions; updated count 38→37, ALWAYS 15→14; updated `cognition.md` Files section

**Overall: DELIVERED**
All 6 tasks shipped. Per-hit frontmatter reads eliminated from FTS article search path. `source_ref` and `artifact_id` propagate through schema, all write paths, all SELECT projections, and `SearchResult`. Existing DBs migrate transparently via idempotent `ALTER TABLE ADD COLUMN`.
