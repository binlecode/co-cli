# Co CLI — Memory: Knowledge Channel

> Foundation: [memory.md](memory.md). Dream-cycle mining, merge, decay, archive: [dream.md](dream.md). Tool registration and approval: [tools.md](tools.md). Prompt assembly: [prompt-assembly.md](prompt-assembly.md).

This doc owns the knowledge channel — declarative knowledge stored as flat-file markdown with YAML frontmatter, indexed in `chunks_fts`, and mutated through `knowledge_manage`.

## 1. Storage

- Path: `~/.co-cli/knowledge/*.md` (the `knowledge_dir` workspace path on `CoDeps`).
- Format: YAML frontmatter + markdown body, single artifact per file.
- Mutation: `knowledge_manage(action=...)` — the only write surface.
- Indexing: chunked into `chunks_fts` under `source='knowledge'`; optional vec embedding under hybrid backend.
- Lifecycle: created by the model on demand, mined offline by the dream cycle, decayed and archived per [dream.md](dream.md).

## 2. Kind Taxonomy

Every artifact carries a `kind` (stored on disk as `artifact_kind` in the frontmatter — the on-disk field name is unchanged for backward compatibility). The taxonomy is hard — the loader rejects unknown kinds.

| `kind` | Purpose | Typical content |
| --- | --- | --- |
| `user` | Identity, preferences, corrections, feedback | Stable personal facts; "I prefer X"; behavioral corrections |
| `rule` | Prescriptive guidance | Mandates, decisions with rationale, conventions |
| `article` | Synthesized content | Analysis, summaries, research notes, saved URLs |
| `note` | Catch-all | Free-form notes that don't fit a sharper kind |

The recall pipeline prioritizes `user` in the first pass; `rule`, `article`, and `note` flow through the waterfall pass. See the recall path in [memory.md §3](memory.md).

Canon is intentionally absent — canon is doctrine, auto-injected into the static prompt by the personality system; it is not a memory kind.

## 3. Frontmatter Schema

Every artifact's YAML frontmatter contains:

| Field | Purpose |
| --- | --- |
| `id` | Stable UUID |
| `artifact_kind` | `user`, `rule`, `article`, or `note` (on-disk field name; tool surface uses `kind`) |
| `title` | Human-readable label |
| `description` | Short retrieval summary |
| `created` | ISO8601 creation timestamp |
| `updated` | ISO8601 last-modified timestamp |
| `related` | Soft links to related artifacts |
| `source_type` | `detected`, `web_fetch`, `manual`, `obsidian`, `drive`, or `consolidated` |
| `source_ref` | Pointer to source session, URL, file path, or artifact ID |
| `decay_protected` | Lifecycle protection flag; decay semantics in [dream.md](dream.md) |
| `last_recalled` | Most recent recall timestamp |
| `recall_count` | Recall hit counter |

`description` and `id` are required for indexing; the rest are optional but stable.

## 4. `knowledge_manage(action, ...)`

Single write surface for the knowledge channel. Replaces the former `memory_create` and `memory_modify` tools. The tool lives at `co_cli/tools/memory/manage.py`.

Tool args: `action`, `name`, `content`, `kind`, `section`. The `kind` arg is the model-visible name for what is stored on disk as `artifact_kind` (the on-disk field is unchanged).

| Action | Behaviour |
| --- | --- |
| `create` | Dispatched through `save_artifact()`. `source_url` set → URL-keyed dedup (web articles), `decay_protected` forced True. `consolidation_enabled` → Jaccard dedup; >0.9 near-identical skipped, overlapping merged. Else → straight create. Rejects `kind='canon'`. |
| `append` | Append content to an existing artifact body. Guards: rejects Read-tool line-number prefixes. |
| `replace` | Surgically replace a passage in an existing artifact body. Target must appear exactly once. |
| `delete` | Remove an artifact file and its `chunks_fts` rows; returns confirmation. Hard-delete; archival is a separate future feature. |

Writes use `atomic_write()` (temp-file + `os.replace`). `reindex()` is called at the tool layer with config-sourced `chunk_size`/`chunk_overlap` so the next `memory_search` reflects the change.

**Approval.** `knowledge_manage` is `approval=True`. The approval subject is scoped per-action and per-name (`tool:knowledge_manage:<action>:<name>`).

### Passage edits

`append` and `replace` operate on `filename_stem` (the artifact's filename without `.md`), not the title. Use `memory_search` to find a hit, then take the `filename_stem` field from the result for follow-up edits.

`replace` requires the target string to appear exactly once in the body — fewer matches return an error directing to refine the target; multiple matches return an error directing to narrow it.

## 5. Indexing

The knowledge channel uses the shared `chunks_fts` index under `source='knowledge'`.

| Property | Value |
| --- | --- |
| Source value | `'knowledge'` |
| Sync entry point | `MemoryStore.sync_dir(source='knowledge', directory=knowledge_dir)` |
| Chunk strategy | sliding-window over body; chunk size + overlap from config |
| Hash skip | SHA256 over raw file contents; unchanged files are not re-indexed |
| Stale removal | `remove_stale(source, current_paths, directory=knowledge_dir)` |
| Hybrid path | embeddings cached per `(provider, model, content_hash)` in `embedding_cache`; vec rows in `chunks_vec_{dims}` |

### Recall passes (knowledge channel)

`_search_artifacts(ctx, query, kinds, limit)` runs a two-pass structure when `memory_store` is available:

1. **User priority** (if `kinds=None` or `'user' in kinds`): `store.search(sources=['knowledge'], kinds=['user'], limit=_ARTIFACTS_USER_CAP=3)`.
2. **Waterfall** (`rule`/`article`/`note`, or caller-specified non-priority kinds): `store.search(..., kinds=waterfall_kinds, limit=_ARTIFACTS_WATERFALL_CHUNK_CAP=5)`, dual-capped by count and `_ARTIFACTS_WATERFALL_SIZE_CAP=2000` cumulative full-chunk chars.

When `memory_store` is `None`, the grep fallback (`_grep_artifacts_fallback`) walks `knowledge_dir` and matches in-memory.

Result shape:

```python
{
    "channel": "knowledge",
    "kind": <user|rule|article|note>,
    "title": <frontmatter title or filename stem>,
    "snippet": <FTS5 snippet>,
    "score": <BM25>,
    "path": <absolute path>,
    "filename_stem": <stem>,
}
```

## 6. Backward-Compat Notes

| Removed/renamed | Replacement |
| --- | --- |
| `memory_create` | `knowledge_manage(action='create', ...)` |
| `memory_modify` | `knowledge_manage(action='append', ...)` or `knowledge_manage(action='replace', ...)` |
| `artifact_manage` | `knowledge_manage` (rename; on-disk frontmatter field `artifact_kind` and internal `KnowledgeArtifact` class are unchanged) |
| tool arg `artifact_kind` | `kind` |

There are no aliases — the renames are hard. Internal callers were updated in the four-tier decomposition commit.

## 7. Files

| File | Purpose |
| --- | --- |
| `co_cli/memory/artifact.py` | `KnowledgeArtifact` schema, kind enums, artifact loaders |
| `co_cli/memory/service.py` | pure-function write layer: `save_artifact()`, `mutate_artifact()`, `reindex()` |
| `co_cli/memory/_mutator.py` | `atomic_write()` — temp-file + `os.replace` write helper |
| `co_cli/memory/archive.py` | `archive_artifacts()`, `restore_artifact()` |
| `co_cli/memory/text_chunker.py` | knowledge artifact text chunking |
| `co_cli/memory/frontmatter.py` | frontmatter parse, validate, render |
| `co_cli/memory/similarity.py` | Jaccard similarity and content-superset helpers |
| `co_cli/memory/decay.py` | artifact decay scoring and eligibility |
| `co_cli/memory/dream.py` | dream-cycle orchestration (see [dream.md](dream.md)) |
| `co_cli/tools/memory/manage.py` | `knowledge_manage()` — knowledge write surface |
| `co_cli/tools/memory/read.py:grep_recall` | grep fallback when `memory_store` is `None` |

## 8. Config

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.chunk_size` | `CO_KNOWLEDGE_CHUNK_SIZE` | `600` | artifact chunk size in chars during indexing |
| `knowledge.chunk_overlap` | `CO_KNOWLEDGE_CHUNK_OVERLAP` | `80` | artifact chunk overlap in chars |
| `knowledge.consolidation_enabled` | `CO_KNOWLEDGE_CONSOLIDATION_ENABLED` | `false` | enable Jaccard dedup on artifact writes |
| `knowledge.consolidation_trigger` | *(no env var)* | `session_end` | when consolidation runs: `session_end` or `manual` |
| `knowledge.consolidation_lookback_sessions` | *(no env var)* | `5` | past sessions to mine during consolidation |
| `knowledge.consolidation_similarity_threshold` | *(no env var)* | `0.75` | Jaccard score threshold for artifact dedup/merge |
| `knowledge.max_artifact_count` | *(no env var)* | `300` | soft cap on total artifact count |
| `knowledge.decay_after_days` | `CO_KNOWLEDGE_DECAY_AFTER_DAYS` | `90` | days before decay eligibility |

Backend, embedding, and retrieval settings (shared with other channels) live in [memory.md §6](memory.md).

### Paths

| Path | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | knowledge artifact source-of-truth directory |

## 9. Test Gates

| Property | Test file |
| --- | --- |
| FTS5 search finds an indexed artifact entry | `tests/test_flow_memory_store.py` |
| `knowledge_manage` replace preserves frontmatter | `tests/test_flow_memory_write.py` |
| `knowledge_manage` append adds to body | `tests/test_flow_memory_write.py` |
| `knowledge_manage` delete removes file and `chunks_fts` row | `tests/test_flow_artifact_manage.py` |
| `grep_recall` returns artifact matched by title only | `tests/test_flow_memory_recall.py` |
| `_list_artifacts` delegates to index when store is available | `tests/test_flow_memory_recall.py` |
| `save_artifact` URL dedup uses O(1) index when `memory_store` set | `tests/test_flow_memory_write.py` |
| Waterfall pass count cap stops at `_ARTIFACTS_WATERFALL_CHUNK_CAP`; size cap stops before count cap when chunks are large | `tests/test_flow_artifacts_waterfall_cap.py` |
