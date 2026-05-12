# Co CLI — Memory

> Channel sub-specs: [memory-knowledge.md](memory-knowledge.md) · [memory-sessions.md](memory-sessions.md). Sibling surface (own tier): [skill.md](skill.md). Doctrine (auto-injected into static prompt; never queried as memory): [personality.md](personality.md). Tool registration and approval: [tools.md](tools.md). Dream-cycle mining, merge, decay, archive: [dream.md](dream.md). Prompt assembly: [prompt-assembly.md](prompt-assembly.md). Startup sequencing: [bootstrap.md](bootstrap.md). Turn orchestration: [core-loop.md](core-loop.md). Compaction mechanics: [compaction.md](compaction.md).

Foundation spec for the memory surface — dynamic, declarative state accumulated by the agent through operation. Channel-specific lifecycle (storage, mutation, validation, indexing details, channel-specific test gates) lives in the two sub-specs.

Memory is one of four operational tiers in the agent loop: **doctrine** ([personality.md](personality.md), identity), **tools** ([tools.md](tools.md), capability), **skills** ([skill.md](skill.md), procedure), **memory** (this file — declarative state). Each tier is structurally distinct: doctrine is auto-injected, tools are registered, skills have their own search/view/manage surface, and memory is what the agent accumulates through operation.

## 1. Agentic-Loop Foundation

Memory contributes two channels — session and knowledge — both genuinely dynamic (accumulated by the agent during operation) and declarative (facts, not procedure or identity).

Memory is never injected wholesale into the system prompt. Static personality content (soul seed, mindsets, personality-context artifacts, bundled skill manifest) is injected once at agent construction. Everything else is loaded on-demand through the memory tool surface, keeping context bounded and recall purposeful.

```mermaid
flowchart TD
    Knowledge["knowledge\n(knowledge/*.md)"] -->|"source='knowledge'"| SearchDB["co-cli-search.db\n(chunks + FTS5 + optional vec)"]
    Sessions["sessions\n(sessions/*.jsonl)"] -->|"source='session'"| SearchDB
    SearchDB --> MemSearch["memory_search()"]
    KnowManage["knowledge_manage()"] --> Knowledge
```

## 2. Channel Ontology

| Channel | Sub-spec | Storage | Mutation | Indexing |
| --- | --- | --- | --- | --- |
| **knowledge** | [memory-knowledge.md](memory-knowledge.md) | `~/.co-cli/knowledge/*.md` | `knowledge_manage(action=...)` | FTS5 BM25 + optional hybrid; chunks body text |
| **sessions** | [memory-sessions.md](memory-sessions.md) | `~/.co-cli/sessions/*.jsonl` | append-only via `persist_session_history` | sliding-window token chunks |

Skills and canon are intentionally absent from this table — they live on their own tiers (see [skill.md](skill.md) and [personality.md](personality.md)). Canon is doctrine, auto-injected by the personality system; skills are procedural capability with their own search/view/manage surface.

## 3. Cross-Channel Primitive

### `memory_search(query, channel=None, kinds=None, limit=None)`

Single entry point for ranked recall across both memory channels. Dispatched in `co_cli/tools/memory/recall.py`.

**Browse mode** (empty query): returns recent-session metadata + recent knowledge artifacts (each capped). No FTS, no LLM. The current session is excluded.

**Search mode** (non-empty query): runs the relevant channel passes and merges results. The `channel` arg restricts to one channel (`'session'` or `'knowledge'`); `None` searches both.

Result format — flat list, each item carries a `channel` discriminator field:

| Channel | Result fields |
| --- | --- |
| `knowledge` | `{channel: "knowledge", kind, title, snippet, score, path, filename_stem}` |
| `session` | `{channel: "session", session_id, when, source, chunk_text, start_line, end_line, score}` |

Scores are not cross-comparable across channels. Channel caps: knowledge user priority `_ARTIFACTS_USER_CAP=3`, knowledge waterfall `_ARTIFACTS_WATERFALL_CHUNK_CAP=5` count / `_ARTIFACTS_WATERFALL_SIZE_CAP=2000` chars, sessions `_SESSIONS_CHANNEL_CAP=3`.

**Removed channels.** `channel='skills'` and `channel='canon'` raise `tool_error` directing to `skill_search` and the personality system respectively. There are no aliases.

Recall pipeline overview:

```
memory_search(ctx, query, channel, kinds, limit)        # tools/memory/recall.py
  ├─ _search_artifacts → user + waterfall passes          # see memory-knowledge.md
  └─ _search_sessions  → chunk-cited BM25                 # see memory-sessions.md
```

## 4. Write Surface

One model-callable write surface for the knowledge channel. Sessions are append-only via `persist_session_history` (no `*_manage` tool); skills have their own write surface in [skill.md](skill.md).

| Tool | Channel | Actions | Approval subject pattern |
| --- | --- | --- | --- |
| `knowledge_manage` | knowledge | `create`, `append`, `replace`, `delete` | `tool:knowledge_manage:<action>:<name>` |

Detailed semantics, validation, and approval flow: [memory-knowledge.md §4](memory-knowledge.md).

## 5. Channel-Specific Readers

`memory_search` is the cross-channel discovery surface; full-content reads happen through channel-specific readers.

| Tool | Channel | Status | Source |
| --- | --- | --- | --- |
| `memory_read_session_turn(session_id, start_line, end_line)` | session | source-only (not registered) | [memory-sessions.md §4](memory-sessions.md) |
| `file_read(path)` | knowledge | generic file tool | `co_cli/tools/files/read.py` |

Knowledge hits carry a snippet; `file_read` on `path` returns the full body.

## 6. Indexer

The shared search index lives at `~/.co-cli/co-cli-search.db`. Both memory channels write through `MemoryStore` in `co_cli/memory/memory_store.py`. (The skill index also uses the same DB file via `SkillIndex` — see [skill.md](skill.md) — but it owns the `'skill'` source exclusively and has its own API.)

### `chunks_fts` table

FTS5 full-text index over all chunks. Sources owned by memory:

| Source value | Channel | Chunk strategy |
| --- | --- | --- |
| `'knowledge'` | knowledge | sliding-window body chunks |
| `'session'` | session | sliding-window token chunks via `session_chunker.py` |

Two other sources (`'skill'`, `'canon'`) coexist in the same table — `'skill'` is owned by `SkillIndex` (see [skill.md](skill.md)); `'canon'` is indexed at bootstrap for personality auto-injection only and is never returned by any model-callable tool.

### Write-time indexing

Indexing is write-time, not search-time. Channel-specific entry points: `sync_dir()` for knowledge, `index_session()` / `sync_sessions()` for sessions. See each channel sub-spec for chunking details.

### Retrieval backends

| Backend | Mechanism | When used |
| --- | --- | --- |
| `hybrid` | FTS5 BM25 + sqlite-vec cosine, RRF merge (k=60) | Configured, TEI reranker reachable, embedding provider configured/reachable, and sqlite-vec available |
| `fts5` | BM25 over chunked text only | Explicitly configured, or hybrid degrades before store construction |
| `grep` | In-memory substring over artifact title+content | `memory_store` is `None`; sessions return `[]` in this state |

Optional reranker (applied after merge, before limit): TEI cross-encoder (`cross_encoder_reranker_url`); unconfigured = pass-through.

## 7. Backward-Compat Notes

Removed (no aliases): `memory_create` / `memory_modify` → `knowledge_manage(...)`. `artifact_manage` renamed to `knowledge_manage` (tool arg `artifact_kind` → `kind`). `skills_list` and `memory_search(channel='skills')` → `skill_search`. `memory_search(channel='canon')` → not queryable; canon is auto-injected via personality. Channel renames: `artifacts` → `knowledge`, `sessions` → `session`.

## 8. Files

### Memory core (shared)

| File | Purpose |
| --- | --- |
| `co_cli/memory/memory_store.py` | `MemoryStore` — FTS5/hybrid search, `sync_dir()`, `index_session()`, `sync_sessions()`, generic helpers `list_titles_by_source()` / `get_path_by_title()` |
| `co_cli/memory/_embedder.py` | `build_embedder()` — embedding provider dispatch |
| `co_cli/memory/search_util.py` | `normalize_bm25()`, `run_fts()`, `sanitize_fts5_query()`, `snippet_around()` |
| `co_cli/memory/stopwords.py` | `STOPWORDS` frozenset |

### Memory tool surface

| File | Purpose |
| --- | --- |
| `co_cli/tools/memory/recall.py` | `memory_search()` — cross-channel recall tool |
| `co_cli/tools/memory/manage.py` | `knowledge_manage()` — knowledge write surface |
| `co_cli/tools/memory/read.py` | `grep_recall()` — knowledge fallback; `memory_read_session_turn()` — verbatim turn reader (source-only, not registered) |
| `co_cli/agent/_native_toolset.py` | foreground toolset registration |

### Bootstrap and runtime

| File | Purpose |
| --- | --- |
| `co_cli/bootstrap/core.py` | `restore_session()`, `init_session_index()`, `_sync_canon_store()` (personality-load-only), `create_deps()` |
| `co_cli/main.py` | `_finalize_turn()` — session persistence bridge and session-end dream trigger |
| `co_cli/tools/tool_io.py` | oversized tool-result spill, preview placeholders, size warnings |

Channel-specific files (e.g. `co_cli/memory/artifact.py`, `co_cli/memory/session_chunker.py`) are listed in the respective sub-specs.

## 9. Config

### Shared retrieval settings

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge.search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `hybrid` | preferred retrieval backend before runtime degradation |
| `knowledge.embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `tei` | embedding backend (`ollama`, `gemini`, `tei`, `none`) |
| `knowledge.embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `embeddinggemma` | embedding model name |
| `knowledge.embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `1024` | embedding vector dimensions |
| `knowledge.embed_api_url` | `CO_KNOWLEDGE_EMBED_API_URL` | `http://127.0.0.1:8283` | embedding service URL |
| `knowledge.cross_encoder_reranker_url` | `CO_KNOWLEDGE_CROSS_ENCODER_RERANKER_URL` | `http://127.0.0.1:8282` | TEI cross-encoder reranker URL |
| `knowledge.tei_rerank_batch_size` | *(no env var)* | `50` | batch size for TEI rerank HTTP requests |
| `memory.recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | defined lifecycle setting; not currently consumed by recall ranking |

Channel-specific settings (chunk sizes, consolidation, decay, session chunking) live in the respective sub-specs.

### Paths

| Path | Env Var | Default | Description |
| --- | --- | --- | --- |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | knowledge artifact source-of-truth directory |
| `sessions_dir` | — | `~/.co-cli/sessions/` | transcript directory |
| `tool_results_dir` | — | `~/.co-cli/tool-results/` | spill directory for oversized tool results |
| `memory_db_path` | — | `~/.co-cli/co-cli-search.db` | unified retrieval DB (sessions + knowledge; also hosts skill and canon sources owned by other tiers) |

Dream-cycle and lifecycle maintenance settings live in [dream.md](dream.md).
