# RESEARCH: Memory Recall Design Families and Peer Architecture

**Scan date:** 2026-04-29
**Scope:** Long-term memory recall in agent assistants — session search and knowledge search. Peer-by-peer evidence, design family taxonomy, implications for co-cli's T1/T2/canon work.

Companion to [`RESEARCH-memory-peer-for-co-second-brain.md`](RESEARCH-memory-peer-for-co-second-brain.md). That doc evaluates peers as products; this doc evaluates them as **retrieval architectures** — the mechanisms, indexes, and LLM patterns each chose, and why those choices shape the surrounding system.

---

## 0. Executive Summary

Three structurally distinct design families exist for long-term memory recall in agent CLIs. Each family makes a different choice about **where the smart work happens**, and that single choice cascades through storage, lifecycle, latency, token economics, and failure modes.

| Family | Retrieval | LLM role at recall | Persistent index | Representative peers |
|---|---|---|---|---|
| **A. FTS+summarize** | FTS5 picks containers | summarizer per container | message-level FTS5 | hermes, co-cli T1 |
| **B. Chunked semantic** | hybrid (BM25+vec) ranks chunks | optional reranker | chunks + FTS5 + sqlite-vec | openclaw, co-cli T2, ReMe file-based |
| **C. Manifest/grep + LLM-select** | filesystem scan / `String.includes` | selector (binary in/out) | none | fork-claude-code |

Closest peers to co-cli's product (general AI assistant + memory as load-bearing core feature): **openclaw and hermes only**. Specialized agents (fork-claude-code, codex, aider, gemini-cli) treat memory as supplementary; memory frameworks (Letta, mem0, ReMe-vector) aren't packaged assistants.

---

## 1. Family A: FTS+summarize

### Mechanism

1. FTS5 virtual table at message-row granularity (one row per message in `messages_fts`).
2. `MATCH ? ORDER BY rank LIMIT 50` with BM25 scoring, joined back to `messages` and `sessions`.
3. Group hits by parent session, dedup keeping best per session, cap at N=3 sessions.
4. For each surviving session: load full transcript, format as text, **truncate to 100K-char window** centered on regex-recomputed match positions (three-tier strategy: phrase → proximity-200 → individual terms; 25%/75% before/after bias).
5. **Parallel LLM summarization** of each window (60s timeout, 3-attempt retry with linear backoff).
6. Return per-session prose summaries.

### Key constants (hermes / co-cli T1)

- `MAX_SESSION_CHARS = 100_000`, `MAX_SUMMARY_TOKENS = 10000`
- Session cap = 3 (hermes default; co-cli `_T1_SESSION_CAP = 3`)
- Outer timeout = 60s
- Tokenizer: `tokenize='porter unicode61'` (co-cli) or default unicode61 (hermes — no stemming)

### Long/short session handling

- **Short (≤100K chars)**: whole session sent verbatim, no truncation, no markers.
- **Long (>100K chars)**: single contiguous window selected via match-position-count maximization. Other clusters of matches outside the chosen window are discarded before the summarizer ever sees them.
- **Long, no regex match found** (FTS hit but no literal substring): fallback to first 100K chars + suffix marker. Common when porter stemming makes the FTS match but the unstemmed regex doesn't find it.

### Properties

- ✅ Zero indexing infrastructure beyond FTS5 (built into SQLite stdlib).
- ✅ Cold-start = 0 (synchronous trigger on message insert; or sync-pass on bootstrap for co-cli).
- ✅ Storage ~1.2× raw transcripts.
- ❌ 8–30s wall-clock per recall (LLM-bound).
- ❌ One contiguous window per session — dispersed matches in long sessions lose information.
- ❌ FTS-vs-regex mismatch silently degrades to first-100K fallback.
- ❌ Returns prose; can't compose at result-list level with chunked-semantic results.

### Design rationale (inferred — not documented in hermes)

The cleanest reading of hermes' design intent is **summarization for SNR**: the goal is high-SNR recall context for the main model. Once an LLM summarization step is in the recall path, several "obvious" retrieval engineering choices become redundant:

- Chunking is wasted precision — the LLM already finds signal in a wider window
- Semantic/vector search loses its main differentiator (synonym/paraphrase handling) — the LLM handles those at the output layer

Module docstring (`hermes-agent/tools/session_search_tool.py:7-8`): *"Returns focused summaries of past conversations rather than raw transcripts, keeping the main model's context window clean."* That's the whole position. No comparison with alternatives is documented in any hermes commit message, README, or architecture doc.

The **infrastructure amortization argument** sharpens this: hermes' core has no local RAG infrastructure (no chunks table, no embeddings, no vector store). Building chunked semantic search just for sessions would mean hosting an entire RAG pipeline (~1000 LOC + 2-3 deps) for one feature. FTS+summarize avoids that build cost — SQLite FTS5 + a Gemini Flash call vs ~1000 LOC of new subsystems. (Hermes did once build a chunked+hybrid stack in `agent/workspace.py` for codebase RAG, commit `81d44670`, but later refactored it out of the core. Session_search remained.)

---

## 2. Family B: Chunked semantic + hybrid

### Mechanism

1. Content chunked at write time (markdown-heading-aware, code-symbol-aware, or generic ~600-token windows with overlap).
2. Each chunk written to `chunks` table + `chunks_fts` (FTS5 BM25) + `chunks_vec` (sqlite-vec, dense embedding).
3. Query path:
   - **Keyword leg**: BM25 over `chunks_fts` with porter stemmer
   - **Vector leg**: cosine KNN over `chunks_vec` (oversample factor 8× to fill candidate pool)
   - **Merge**: weighted score (typical: 0.7 vector + 0.3 text) or RRF
   - **Optional reranker**: LLM or cross-encoder (TEI/Cohere/Voyage)
   - **Optional MMR**: diversity-aware reorder
   - **Optional temporal decay**: recency boost
4. Return ranked chunk snippets with metadata (`{path, score, snippet, source, start_line, end_line, ...}`).

### Storage cost

- Per-chunk overhead: ~4KB embedding (1024-dim float32) + chunk text + metadata
- Total: ~5–10× raw content size

### Properties

- ✅ Sub-second query latency (no LLM in path unless reranker enabled).
- ✅ Compositional: source filter (`source=sessions` or `source=memory`), date filter, kind filter, MMR, temporal decay all on one pipeline.
- ✅ Multiple graceful-degradation paths: vec down → BM25; reranker fails → unranked merge; tokenizer edge case → LIKE fallback.
- ✅ Recency built into ranking (temporal decay).
- ❌ Embedding pipeline is a real subsystem: chunker, embedder client, cache, sync, retry, model-upgrade reindex.
- ❌ Cold-start latency 100ms–10s (async embed).
- ❌ Schema migrations or embedding-model swap require full corpus re-embed.

### Where peers diverged within this family

- **openclaw** (`extensions/memory-core/src/memory/`):
  - Sessions = just another `source` in unified `chunks` table (`manager-sync-ops.ts:858`)
  - MMR + temporal decay both built into `mergeHybridResults` (`hybrid.ts:140-151`)
  - LIKE fallback added when FTS5 MATCH throws on tokenizer edge cases (commit `2aa6abddbe`)
  - Component scores (`vectorScore`, `textScore`) exposed alongside merged score for diagnostics (commit `66e66f19c6`)
  - Visibility filter for cross-agent session leakage (commit `2c716f5677`, PR #70761)
- **co-cli T2** (`co_cli/memory/knowledge_store.py`):
  - Two-table FTS5: `docs_fts` (file-level) + `chunks_fts` (chunk-level)
  - Hybrid via RRF in `_hybrid_search`
  - Optional LLM/TEI reranker in `_rerank_results`
  - Default: 600-token chunks, 80-token overlap (`config/knowledge.py:13`)
- **ReMe file-based** (`reme/core/file_store/sqlite_file_store.py`):
  - Single chunks table per store with FTS5 + sqlite-vec triplet
  - **Trigram tokenizer** instead of porter (`:169`) — supports CJK substring matching natively
  - LIKE fallback when query tokens are <3 chars (CJK or short Latin)
  - Weighted merge (`vector_weight=0.7` default), no MMR, no temporal decay
  - **`MemorySource.SESSIONS` enum is defined but never wired** — file-based subsystem doesn't actually have session search, despite tool description claiming it does
- **ReMe vector-based** (`reme/memory/vector_based/`):
  - Different system entirely — agent-orchestrated retrieval via `PersonalRetriever` ReActAgent
  - Two-stage: `RetrieveMemory` (vector similarity over memory nodes) → `ReadHistoryV2` (block-level cosine over a specific history's messages)
  - **Memory types** `PERSONAL`, `PROCEDURAL`, `TOOL_CALL` (this is what corresponds to co-cli's `artifact_kind` taxonomy, not the file-based subsystem)

---

## 3. Family C: Manifest/grep + LLM-select

### Mechanism (fork-claude-code)

Two distinct subsystems, both with **no persistent recall index**.

**Memory recall** (`memdir/findRelevantMemories.ts`, 141 LOC):
1. Walk memory dir, read **frontmatter only** (first 30 lines) of each `.md` (`MAX_MEMORY_FILES = 200`).
2. Build manifest: `[type] filename (timestamp): description`.
3. Send manifest + query to Sonnet via `sideQuery` with JSON schema asking for `selected_memories: string[]` (max 5).
4. Return file paths. Content read later by agent via `FileReadTool`.

Content is **never scanned at recall time**. Selection is purely on filename + frontmatter description.

**Session search** (`utils/agenticSessionSearch.ts`, 307 LOC):
1. Pre-filter via JS `String.includes()` over multiple fields (title, tag, branch, summary, firstPrompt, transcript head+tail joined, capped at 2000 chars).
2. Pad with recent non-matching sessions if pre-filter yields <100.
3. Send up to 100 session metadata blocks to small/fast Claude model with system prompt: *"Be VERY inclusive in your matching... When in doubt, INCLUDE the session."*
4. Return `LogOption[]` ordered by relevance — **whole session metadata, no summarization**. Powers `/resume` interactive picker.

### Properties

- ✅ Zero infrastructure: no schema, no migrations, no index lifecycle.
- ✅ Cold-start immediate.
- ✅ Storage 1× raw content (no derived data).
- ✅ Agentic flexibility — LLM can apply judgment beyond keyword matching.
- ❌ O(N) filesystem scan per query.
- ❌ LLM cost per recall (small/fast model, but still nonzero).
- ❌ Bounded by N (200 memory files, 100 sessions in fork-claude-code's caps).
- ❌ No ranking signal beyond LLM judgment.
- ❌ No fail-soft: if the LLM judges poorly, recall is poor.

### Why Anthropic's team chose this

The Anthropic team that built Claude Code itself **deliberately avoided building a persistent recall index for either memory or sessions**, despite owning the LLM stack and having Claude Code's main job be long-running coding tasks. They settled on filesystem-scan + LLM-select.

Calibration signal: **for agent CLIs at this scale (≤200 memory files, ≤100 sessions), the persistent-index family is over-engineered for the recall workload.** The cost of building, maintaining, and migrating an FTS or vector index exceeds the cost of one small/fast LLM call per recall.

This isn't a recommendation — co-cli's T2 chunked-hybrid stack is justified for its specific commitments (Obsidian/Drive multi-source, evals demanding repeatable scores). It's a calibration point: a sophisticated recall stack isn't a baseline requirement for an agent CLI.

---

## 4. System-level comparison

The foundational difference: where the smart work happens. Cascading implications:

| | A: FTS+summarize | B: chunked semantic | C: manifest+LLM |
|---|---|---|---|
| Storage vs raw | ~1.2× | ~5–10× | 1× |
| Cold-start | 0 | 100ms–10s | 0 |
| Update path | SQL trigger / sync pass | chunker → embedder → 3 writes | n/a |
| Query DB latency | <1ms | <100ms | n/a (filesystem scan) |
| LLM per query | N parallel summarize (N=3) | 0 (or 1 reranker batch) | 1 selector |
| Wall-clock | 8–30s | <1s | 1–5s |
| Token cost timing | **Query time, repeats per query** | **Write time, amortized over recalls** | **Query time, smaller (selector only)** |
| Schema migration | trivial | full re-embed on model swap | n/a |
| Failure modes | 1 LLM cliff | 5 graceful-degradation paths | LLM judgment |
| Result shape | prose summary | structured rows | metadata list |
| Composable across sources? | No (different prose payloads) | Yes (uniform schema) | No (LLM-driven) |
| Recency in ranking | none | temporal decay | implicit (mtime sort) |
| Diversity | none (best-per-session dedup) | MMR | implicit |

### Token economics — the most consequential crossover

```
Family A:  total = recalls × N × ~25K input tokens × cost_per_summarize
Family B:  total = chunks × embed_cost  +  recalls × ~10ms compute
Family C:  total = recalls × ~5K input tokens × cost_per_select
```

Family A pays bursty per-query cost; Family B amortizes a one-time embedding cost over all future queries. Family C is in between: smaller per-query cost than A, but no amortization like B.

Crossover depends on per-content-piece recall frequency:
- 1× lifetime → A or C wins (no upfront cost to amortize)
- 10× → roughly even
- 100× → B wins easily

---

## 5. Workload affinity

| Workload trait | Best family |
|---|---|
| Conversational / narrative content | A — LLM gist works |
| Structured / declarative content (code, schemas) | B — precise spans matter |
| Exploratory queries ("what did we discuss about X?") | A — gist suffices |
| Exact-span queries ("function signature for X") | B — gist destroys precision |
| Multiple heterogeneous sources, unified ranking | B — composable |
| Storage-constrained | A or C — no embeddings |
| Latency-sensitive (<1s SLO) | B — no LLM in path |
| Recall-rare content | A or C — no upfront cost |
| Recall-frequent content | B — amortizes |
| Cold-start matters | A or C — no indexing pipeline |
| Cross-lingual / paraphrase-heavy | B — vectors handle it |
| Small corpus (≤200 items) | C — infrastructure-zero |
| Large corpus (10K+ items) | B — scaling matters |
| Existing RAG infrastructure to reuse | B — marginal cost low |
| No existing infrastructure | A or C — minimize build cost |

---

## 6. Peer-by-peer reference

### Hermes-agent (Family A)

Direct ancestor of co-cli T1. Module docstring (`co_cli/memory/summary.py:4`): *"Ported from hermes-agent session_search_tool.py with adaptations for pydantic-ai message types."*

**Mechanism preserved verbatim**: 100K-char window, three-tier match strategy, 25%/75% bias, 60s timeout, 3-attempt retry, parallel `asyncio.gather`. Only adaptation: Gemini Flash → local noreason model.

**Recent hermes additions** (since co-cli's port date `6ea7386a`):
- **Trigram FTS5 sidecar table** (`messages_fts_trigram`) for CJK queries — commit `1fa76607`
- **Tool-metadata in FTS index**: triggers concat `content || tool_name || tool_calls` — commits `cfcad80e` + `8d76d69d`
- **Bounded summarizer concurrency**: `asyncio.Semaphore(N)` with N from `auxiliary.session_search.max_concurrency` (default 3, 1-5 range) — commit `6ab78401`. Stated rationale: avoid 429 bursts on small providers.
- **FTS5 query sanitization** (`_sanitize_fts5_query` at `hermes_state.py:938-989`) — strips unmatched FTS special chars, quotes hyphenated/dotted terms.

### Openclaw (Family B — strongest reference)

Per `RESEARCH-memory-peer-for-co-second-brain.md` §4.2, the strongest reference for retrieval mechanics. Sessions are indexed as `source="sessions"` in the same `chunks` triplet as code, files, wiki — no separate session DB.

**Pipeline**: `searchKeyword` (BM25 over `chunks_fts`) ‖ `searchVector` (sqlite-vec KNN, 8× oversample) → `mergeHybridResults` (weighted) → `applyTemporalDecayToHybridResults` → `applyMMRToHybridResults` → return chunks.

**Recent additions** (since prior scan at `b4d1992338`, now at `4e4f9204d7`):
- LIKE fallback when FTS5 MATCH throws (commit `2aa6abddbe`, 2026-04-29)
- Component score exposure for diagnostics (commit `66e66f19c6`)
- Pre-rank source filtering (`sources: [...]` pushed into FTS/vector SQL) — commit `2c716f5677`. Rationale: *"non-session hits could fill the window"* before top-N slice.
- Bounded fallback vector chunk scoring via SQLite `iterate()` streaming (commit `864c4f7ff4`)

### ReMe (Family B, file-based subsystem)

Per `RESEARCH-memory-peer-for-co-second-brain.md` §4.3, strongest reference for product shape. Two architecturally independent subsystems:

- **`reme.memory.file_based`** — SQLite hybrid: `chunks` + `chunks_vec` (sqlite-vec) + `chunks_fts` (FTS5 with **trigram tokenizer**, not porter). LIKE fallback for short tokens. `MemorySource = MEMORY | SESSIONS` enum exists but `SESSIONS` is never wired (verified via grep across the codebase).
- **`reme.memory.vector_based`** — agent-orchestrated retrieval via `PersonalRetriever` ReActAgent. Memory types `PERSONAL`/`PROCEDURAL`/`TOOL_CALL`. Two-stage: vector retrieve → block-level cosine inside selected histories. Returns benchmark numbers in HaluMem-Medium docstring (85.37% with this approach).

**Lineage attribution correction**: co-cli's `artifact_kind` taxonomy (`preference`/`decision`/`rule`/...) is closer to ReMe's vector-based `MemoryType`, not file-based `MemorySource`. The two ReMe subsystems are independent.

### fork-claude-code (Family C — sole representative in workspace)

Anthropic's official Claude Code. Two production subsystems doing grep-based recall:

- **Memory recall** (`memdir/findRelevantMemories.ts`): frontmatter manifest + Sonnet selector, max 5 from up to 200 files. Content never scanned at recall time.
- **Session search** (`utils/agenticSessionSearch.ts`): `String.includes()` pre-filter + small/fast model selector with "be VERY inclusive" prompt, max 100 sessions. Powers `/resume` picker.

Selection prompt is calibrated for high recall: *"It's better to return too many results than too few. The user can easily scan through results, but missing relevant sessions is frustrating."*

### Other peers (no recall search)

- **codex** — `~/.codex/history.jsonl` is sequential append-only. No search, only offset-based replay.
- **gemini-cli** — list/delete sessions, no recall.
- **aider** — repomap of code, in-session only, no persistent session memory.

---

## 7. Closeness lens — which peers are co-cli's reference class

| Peer | General assistant? | Memory load-bearing? | Closeness |
|---|---|---|---|
| **hermes-agent** | Yes | Yes — *"closed learning loop"* | **High** |
| **openclaw** | Yes | Yes — memory-core first-class | **High** |
| **fork-claude-code** | No (specialized: coding) | No (Family C, deliberately minimal) | Low–medium |
| **codex / gemini-cli / aider** | Mostly no (coding-focused) | No | Low |
| **ReMe** | No (research / framework) | Yes — but framework-shaped | Medium |
| **Letta / mem0 / honcho** | No (memory-as-a-service) | Yes — but they *are* the memory | Low |
| **ElizaOS** | Mixed (character framework) | Yes — per-character | Medium |

**Only hermes and openclaw share both axes** with co-cli (general assistant + memory load-bearing). This is structural, not stylistic — specialized agents make different tradeoffs because their products allow it (e.g., fork-claude-code can use Family C because coding-assistant workflows tolerate filesystem-scan latency); memory frameworks aren't packaged assistants and don't have to make assistant-shape decisions.

**Implication**: when the question is *"what shape should this feature take in our product?"*, openclaw and hermes are the right peers. When it's *"what's the best implementation of vector hybrid?"*, openclaw and ReMe lead. When it's *"how do we scope memory minimalism?"*, fork-claude-code becomes informative as contrast.

---

## 8. Co-cli's position in the taxonomy

| Channel | Family | Lineage |
|---|---|---|
| T1 sessions | A (FTS+summarize) | Direct port from hermes-agent |
| T2 artifacts | B (chunked semantic) | Retrieval mechanics from openclaw, product shape from ReMe file-based |
| Canon (proposed) | A subset of B (in-memory FTS5 over whole files) | Reuses T2's `tokenize='porter unicode61'` recipe at minimal scale |

Co-cli inherits both Family A and Family B at appropriate channels. The split is internally consistent and matches workload affinity:

- Sessions are narrative + exploratory + recall-rare per session → A is the right family
- Artifacts are structured + multi-source + recall-frequent for hot artifacts → B is the right family
- Canon is structured + curated + small corpus → either A's index-cheap approach or B's whole-file FTS5 fits

### Porting completeness (T1 from hermes)

**Faithful**: window selection, three-tier match strategy, 25%/75% bias, 60s timeout, 3-attempt retry, parallel summarization, two-mode tool, `_T1_SESSION_CAP=3`, `MAX_SESSION_CHARS=100_000`, raw-preview fallback (`recall.py:125-126`).

**Improvement over hermes original**: `tokenize='porter unicode61'` adds stemming hermes lacked.

**Adaptations**: local noreason model (vs Gemini Flash), JSONL transcripts + offline sync (vs SQL + live triggers).

**Gaps not yet addressed in co-cli**:

1. **No FTS5 query sanitization** — silent fail on `()*"` etc. `session_store.py:217` returns `[]` on `OperationalError`.
2. **No parent-session lineage resolution** — only path-equality exclusion of current session. Will leak when delegation lands.
3. **No tool-metadata in FTS index** — co-cli indexes message content only.
4. **No CJK trigram sidecar** — porter-only. Low-impact for current users.
5. **No bounded summarizer concurrency** — unbounded fan-out. On Ollama with `OLLAMA_NUM_PARALLEL=1`, three "concurrent" calls queue server-side.
6. **No live-session search** — offline sync at bootstrap means active session can't recall its own earlier turns.

Each gap is 5–30 LOC; none requires architectural change.

**Cosmetic difference, not a gap**: 10-token vs 40-token FTS snippet width. The `SessionSearchResult.snippet` field is set in co-cli but never consumed downstream — fallback preview comes from the truncated 100K window head, not the FTS snippet.

---

## 9. Latency analysis (T1)

**Mechanics**: at Python level, summarization is concurrent via `asyncio.gather`. At the LLM-server level, Ollama's `OLLAMA_NUM_PARALLEL` env var controls actual concurrency; when unset or =1, "concurrent" calls queue server-side and effectively serialize.

**Wall-clock budget**:
- Best case (warm, parallel slots, no retry): 10–15s
- Typical (warm, mild contention): 15–30s
- Worst case (cold start, slot contention, one retry): hits 60s ceiling, returns empty

**Mitigations available**:

1. Cache summaries by `(session_id, query)` — first call pays, repeats free
2. Lazy summarization — return raw FTS snippets immediately, summarize in background
3. Reduce default cap from 3 to 1
4. Verify `OLLAMA_NUM_PARALLEL ≥ 3` at bootstrap, warn if not
5. **Bounded summarizer concurrency** (matching hermes' `auxiliary.session_search.max_concurrency`) — semaphore on the gather, ~10 LOC

**Structural alternative**: migrate T1 to Family B (chunked semantic), reusing T2's existing pipeline with `source="sessions"`. Estimated cost: ~150–200 LOC of glue (session-aware chunker + sync hook + recall-path adjustment), no new dependencies, no new failure modes. Trade-off: lose summarization (LLM-distilled gist) for raw chunk snippets; gain sub-second wall-clock and cross-source ranking.

The infrastructure-amortization argument applies in reverse here: hermes' Family A choice was rational because hermes had no preexisting RAG; co-cli has T2's RAG already paid for, so the migration is glue work, not greenfield build.

---

## 10. Implications for the canon channel

The proposed canon channel (per active exec plan) sits closer to T2 than T1:
- Structured curated content (single-claim canon files)
- Small corpus (~18 files / ~10KB)
- Read-only at runtime
- Synonym/paraphrase queries are a real concern (e.g. "is TARS funny?" matching a "humor" file)

**Family A (FTS+summarize) is wrong** for canon — adding an LLM to canon recall would be pointless overhead for short curated files.

**Family B is right** — but the corpus is small enough that an in-memory FTS5 (`:memory:` SQLite, ~30 LOC, same `tokenize='porter unicode61'` as T2) is a sufficient subset. Porter stemming gives "humor"/"humorous" matching for free, addressing the synonym/stemming risk identified in TASK-5 of the plan.

**Family C is technically viable but not preferred** — for 18 files, the LLM-as-selector overhead (~500ms-1s cloud, ~3-5s local) isn't justified when a local FTS5 gives <50ms recall. Family C becomes attractive at 200+ canon files where LLM judgment beats stemming alone.

---

## 11. Cheap-borrow candidates for co-cli

From the peer survey, items each in the 5–30 LOC range, no architectural change:

| Borrow | From | Target | Why |
|---|---|---|---|
| FTS5 query sanitization | hermes `_sanitize_fts5_query` | T1 `session_store.py` | Currently silent-fail on common special chars |
| Bounded summarizer concurrency (semaphore) | hermes `6ab78401` | T1 `recall.py:_search_sessions` | Prevents server-side queuing on local Ollama |
| LIKE fallback when FTS5 MATCH throws | openclaw `2aa6abddbe` / ReMe `_like_search` | T1 + T2 search paths | Handles tokenizer edge cases with diagnostics |
| Tool-metadata in FTS index | hermes `cfcad80e` | T1 `extract_messages` | Surface tool-invocation history |
| CJK trigram sidecar | hermes `1fa76607` / ReMe trigram | T1 `session_store.py` | If/when non-Latin content matters |
| Pre-rank source filtering | openclaw `2c716f5677` | T2 `_hybrid_search` | If T1/T2 ever unify into one chunks pipeline |
| Component score exposure | openclaw `66e66f19c6` | T2 `_hybrid_search` | Tuning hybrid weights with diagnostics |

All are non-blocking improvements. None addresses the core T1 latency tax — that's a structural issue requiring either Family-B migration or summarization-lazy redesign.

---

## 12. Open questions

1. **Should T1 migrate to Family B?** The infrastructure-amortization argument favors yes (~150-200 LOC of glue vs hermes' ~1000 LOC absolute cost), and latency improvements are large. The cost is losing summarization-as-synthesis. Worth a separate plan before any such move.
2. **Should the porter-vs-trigram tokenizer choice be revisited?** Porter handles English stemming well but fragments CJK. Trigram handles CJK but loses stemming. ReMe's file-based system uses trigram + LIKE fallback as a compromise; openclaw added LIKE fallback on top of porter. Co-cli currently uses porter only.
3. **Is canon's small corpus a permanent property?** The plan caps at "~50 files per role" before scale-gate review. If canon grows past 200 files, Family C (manifest+LLM-select) becomes more attractive than Family B.
4. **Should `MemorySource`/source-discriminator filtering replace `tier` field?** Openclaw's pre-rank `sources: [...]` filter is cleaner than co-cli's post-hoc `tier` interpretation, but only relevant if T1/T2 ever unify into one chunks pipeline.

---

## 13. References

- co-cli memory spec: `docs/specs/memory.md` (esp §2.7 Design Lineage)
- Existing peer survey: `docs/reference/RESEARCH-memory-peer-for-co-second-brain.md`
- Active canon plan: `docs/exec-plans/active/2026-04-28-205532-character-memory-to-search-channel.md`

Peer code paths verified during this scan:

- `hermes-agent/tools/session_search_tool.py` (HEAD `fe6c8662`, scanned 2026-04-29)
- `hermes-agent/hermes_state.py` (same)
- `openclaw/extensions/memory-core/src/memory/{hybrid,manager-search,manager-sync-ops}.ts` (HEAD `4e4f9204d7`, scanned 2026-04-29)
- `openclaw/extensions/memory-core/src/session-search-visibility.ts` (same)
- `ReMe/reme/memory/file_based/tools/memory_search.py` (HEAD `625d184c`)
- `ReMe/reme/core/file_store/sqlite_file_store.py` (same)
- `ReMe/reme/memory/vector_tools/{record/retrieve_memory,history/read_history_v2}.py` (same)
- `fork-claude-code/memdir/{findRelevantMemories,memoryScan}.ts`
- `fork-claude-code/utils/agenticSessionSearch.ts`
