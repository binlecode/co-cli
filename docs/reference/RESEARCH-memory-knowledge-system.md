# RESEARCH: memory-knowledge-system — Reference System Tradeoff Analysis
_Date: 2026-03-06_

## What Was Reviewed

- **DESIGN docs read:** `docs/DESIGN-knowledge.md`, `docs/DESIGN-tools-integrations.md`
- **Reference repos read:** `openclaw` (primary), `letta` (primary), `mem0` (primary), `codex` (primary), `claude-code` (secondary), `gemini-cli` (secondary)
- **Existing RESEARCH/REVIEW docs consulted:** none relevant

---

## Tradeoff Analysis

| Decision | co-cli approach | Peer pattern | Verdict |
|----------|----------------|-------------|---------|
| Storage architecture | Two-layer flat: markdown files + SQLite FTS5/sqlite-vec. Single tier — memories and articles coexist, distinguished by `kind` frontmatter. | letta: 3-tier (in-context blocks, archival pgvector, message store). mem0: vector store + optional graph + SQLite history. openclaw: SQLite FTS5 + sqlite-vec chunks. codex: file-backed MEMORY.md + rollout summaries. | aligned |
| Search strategy | Default FTS5 BM25; optional hybrid (BM25 + sqlite-vec cosine, weighted merge). Reranker: none / fastembed cross-encoder / ollama / gemini. Fallback chain: hybrid → fts5 → grep. | openclaw: FTS5 + sqlite-vec + temporal decay + optional MMR. letta: hybrid text + semantic. mem0: embedding-first + optional reranker. codex: FTS-only. | aligned |
| Memory kinds / taxonomy | Two kinds: `memory` (lifecycle-managed user facts) and `article` (saved references, no lifecycle). External namespaces: `obsidian`, `drive`. No always-in-context block tier. | letta: 3-tier — core blocks (always in context), archival passages (retrieval), conversation recall. codex: evergreen MEMORY.md (always loaded) + dated rollout summaries. mem0: semantic / episodic / procedural types. openclaw: flat chunks, no kind distinction. Note: mem0's architecture collapses this dimension entirely — no external/memory distinction; everything is a fact via the same `add()` pipeline. | divergent |
| Memory lifecycle | Full lifecycle: fuzzy dedup (rapidfuzz token_sort_ratio), LLM consolidation (two-phase extract_facts + resolve), retention cut (oldest non-protected deleted at cap), temporal decay rescoring, touch-on-read gravity. Protected entries exempt from cut and dedup deletion. | mem0: LLM-driven ADD/UPDATE/DELETE/NONE on every `add()` call. codex: Phase 1 per-rollout extraction; Phase 2 periodic consolidation into MEMORY.md; explicit forgetting via thread-id removal. letta: agent-driven only — no auto-lifecycle. openclaw: file-sync only, no dedup or decay. | aligned |
| External knowledge lifecycle | `kind: article` has save/retrieve-only lifecycle — URL dedup on save, no decay, no consolidation, no automatic re-sync. Obsidian and Drive sources sync on-demand at search time (triggered by `search_knowledge` and `read_drive_file`), not continuously. No stale eviction for external sources outside of startup `sync_dir`. | openclaw: chokidar file watcher on memory dirs; session transcript delta tracking (bytes+messages thresholds); hash-based change detection; stale row cleanup after every sync; safe atomic reindex (temp-db swap) on model/chunk config change. mem0: no distinction — all content (web pages, docs, messages) goes through same `add()` pipeline (LLM fact extraction → contradiction resolution → vector+graph write in parallel); no separate external lifecycle. letta: archival passages have no auto-lifecycle; cascade-delete when source data source deleted. codex: batch post-rollout — trace files → LLM summary → periodic consolidation; no real-time sync. | **gap** |
| Signal detection | Post-turn mini-agent (`_signal_analyzer`) analyzes last 10 lines. Output: found/candidate/tag/confidence/inject. High confidence → auto-save. Low confidence → user approval. No-op for low-signal turns. | mem0: LLM extraction on every `add()` call — always runs, no gate. letta: no auto-detection — agent explicitly saves. codex: Phase 1 LLM with explicit no-op gate ("will future agent act better?"). openclaw: none — manual or file-sync. | aligned |
| Recall / injection strategy | Two proactive paths: (1) `inject_opening_context` — history processor injects top-3 matched memories as `SystemPromptPart` before every model request; (2) `add_personality_memories` — `@agent.instructions` injects top-5 `personality-context` tagged memories per turn. On-demand retrieval also available via agent tools. | letta: core blocks always in context; archival retrieval is agent-initiated. codex: `memory_summary.md` always in system prompt; MEMORY.md queried on demand. mem0: pure on-demand — caller invokes `search()` before each LLM call. openclaw: on-demand via tool; no proactive injection. | aligned |
| Chunking | No chunking — full content indexed as a single FTS5 row and a single vector per file/memory. | openclaw: explicit chunking with overlap (chunks table, start_line/end_line per chunk, model-aware size limits). letta: archival passages are individual atomic text passages — each its own vector. mem0: LLM extracts atomic fact strings (implicit chunking). codex: no chunking (MEMORY.md sections are natural units). | **gap** |
| Embedding model / provider | Ollama (default), Gemini, or `none`. 256-dimension default. Embedding cache in `embedding_cache` SQLite table keyed by `(provider, model, content_hash)`. | openclaw: multi-provider (openai, gemini, voyage, mistral, ollama, local GGUF), fallback chain, implicit cache. mem0: pluggable embedder factory (20+ providers). letta: OpenAI embeddings + Redis cache. codex: no embeddings. | aligned |
| Contradiction resolution | Two layers: (1) write-time LLM consolidation generates ADD/UPDATE/DELETE/NONE plan before write; (2) retrieval-time heuristic in `search_knowledge` — 5-token window negation check on same-category pairs, flags conflicts with `⚠ Conflict:` prefix. Known limitation: heuristic false positives; LLM-based detection flagged as Phase 2 in §2.10. | mem0: LLM-driven resolution on every `add()`. codex: consolidation phase overwrites/deprecates conflicting older entries. letta: no auto-resolution — agent resolves manually. openclaw: none. | aligned |
| Agent vs. auto-save | Hybrid: post-turn auto-save pipeline (signal detection → auto-save or approval) PLUS explicit agent `save_memory` tool (with approval). User can also trigger `/new` checkpoint. | letta: fully agent-driven — agent decides when to save. mem0: caller-driven with LLM extraction automatic within the call. codex: batch after each rollout; no in-task auto-save. openclaw: manual / file-sync. | aligned |
| Cross-session persistence | Fully persistent — markdown files in `.co-cli/knowledge/` are durable. `/new` session checkpoint creates a session-summary memory. Opening-context injection proactively loads relevant memories from prior sessions. Touch-on-read updates timestamps across sessions. | mem0, letta, codex, openclaw: all persist across sessions. | aligned |

---

### Significant Untracked Gaps

#### GAP-1: Chunking for long-document sources

| Attribute | Detail |
|-----------|--------|
| **Decision** | Chunking |
| **co-cli status** | Single embedding per file — no sub-document chunking |
| **Peer evidence** | openclaw: `chunks` table with `id, path, source, start_line, end_line, hash, model, text, embedding, updated_at`; `chunks_fts` FTS5 virtual table; `chunks_vec` sqlite-vec table; model-aware token limits via `embedding-chunk-limits.ts`; overlap between chunks. letta: archival passages are atomic per-passage vectors. mem0: implicit chunking via LLM fact extraction — each extracted fact becomes an independent embedding. (3 independent convergences) |
| **User-facing failure mode** | Long `kind: article` content (web-fetched pages, Drive docs, Obsidian notes) produces a single embedding averaging across the whole document. A specific fact buried in a long article will rank poorly or be missed in hybrid search because its embedding signal is diluted. Short `kind: memory` entries are unaffected — memories are already atomic. |
| **Already tracked** | No |
| **Estimated effort** | Medium: ~3-5 files. Add a chunker module, update `KnowledgeIndex.index_document()` to emit multiple `chunks` rows per article, update `hybrid_search()` to deduplicate chunks back to document level before returning. Memory entries skip chunking (already atomic). |
| **Significance score** | 3/4 criteria met: (1) 3 peers converge, (2) user-facing failure mode for article retrieval, (3) not yet tracked. |

#### GAP-2: No real-time sync for external sources

| Attribute | Detail |
|-----------|--------|
| **Decision** | External knowledge lifecycle / sync |
| **co-cli status** | Obsidian sync fires only when `search_knowledge` is called with source=obsidian. Drive content indexed only when `read_drive_file` is called. Between calls, edits to vault files or Drive docs are invisible to the index. |
| **Peer evidence** | openclaw: chokidar file watcher on memory dirs, debounced sync on add/change/unlink. Session transcript delta tracking with configurable byte+message thresholds. Stale row cleanup after every sync. (1 direct convergence on continuous sync) |
| **User-facing failure mode** | User edits an Obsidian note, asks a question about it — co-cli retrieves the stale pre-edit version from index. No staleness indicator is shown. Stale articles (web-fetched, now outdated) also have no TTL or re-fetch signal. |
| **Already tracked** | No |
| **Estimated effort** | Small-medium: Obsidian watcher (~2-3 files: optional background sync + debounce to `sync_dir`). Drive TTL is harder (requires re-fetch). Minimum viable: staleness timestamp + warn on old Drive/article entries. |
| **Significance score** | 3/4 criteria: (1) 1 peer converges on continuous sync (borderline — only openclaw), (2) user-facing failure mode (stale vault search results), (3) not yet tracked. Flagged at P2. |

---

### Already Tracked / Consciously Deferred

| Item | Status |
|------|--------|
| LLM-based retrieval-time contradiction detection (Phase 2) | Conscious deferral per DESIGN-knowledge.md §2.10 |
| Vector store backends beyond sqlite-vec | Deliberate MVP choice — local CLI doesn't need cloud vector stores |
| Graph memory store | Deliberate non-adoption — co-cli targets single-user local use |

---

### Minor Notes (< 2 significance criteria — no escalation needed)

- **MMR re-ranking**: openclaw has opt-in Maximal Marginal Relevance for diversity control. co-cli uses cross-encoder reranker for quality control instead. Different problems; only 1 peer.
- **Multi-language FTS query expansion**: openclaw removes CJK stop-words and expands bigrams. co-cli handles English only. Real gap for non-English users but only 1 peer and not in any active TODO.
- **Always-in-context memory tier**: letta's core blocks and codex's `memory_summary.md` guarantee certain facts are always in context. co-cli's personality-context injection (top-5 by recency) is retrieved, not guaranteed. Intentional — personality profile injection serves this role.
- **Principled forgetting + heuristic contradiction limits**: codex prunes by thread-ID removal (co-cli retention cut is size-based only, §2.10). Heuristic 5-token negation check has known false positives; LLM-based detection is Phase 2. Both are conscious deferrals, single-peer evidence only.

---

## TL Verdict

**Overall: GAPS_FOUND**

| Priority | Action | Source | Status |
|----------|--------|--------|--------|
| P1 | Add tracking for article chunking before planning next knowledge iteration | GAP-1: Chunking for long-document sources | DONE — `docs/TODO-chunking-rrf.md` |
| P2 | Consider Obsidian background watch before next Obsidian-facing feature | GAP-2: No real-time sync for external sources | open |

**Recommended next step:** P1 (chunking) is tracked and planned — see `docs/TODO-chunking-rrf.md`. P2 (external sync) remains open — borderline significant (1 peer converges) but the user-facing failure mode is real: stale Obsidian results with no staleness indicator.
