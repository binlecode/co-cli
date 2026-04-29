# Memory Recall Gap Backlog

**Created:** 2026-04-29
**Source:** `docs/reference/RESEARCH-memory-recall-design-families.md` §8 (T1 porting gaps) + §11 (cheap-borrow candidates), distilled after architecture settled on T0/T1/T2.
**Scope:** Co-cli T1 session recall and T2 knowledge recall — open gaps vs. peer implementations.

Already resolved (do not re-implement):
- FTS5 query sanitization (`sanitize_fts5_query` in `search_util.py`, wired in both T1 and T2 — commit `59e45b4`)
- LIKE fallback when FTS5 MATCH throws (`run_fts()` with `like_fallback=` in both `session_store.py` and `knowledge_store.py` — commit `58325cf`)

---

## T1 — Session Recall Gaps

Source: hermes-agent porting completeness audit. Each item is 5–30 LOC; none requires architectural change.

### G1: Bounded summarizer concurrency
**From:** hermes commit `6ab78401` — `asyncio.Semaphore(N)` with `N` from config (`auxiliary.session_search.max_concurrency`, default 3, range 1–5).
**Where:** `co_cli/tools/memory/recall.py` — the `asyncio.gather(*summarize_tasks)` call.
**Why:** On Ollama with `OLLAMA_NUM_PARALLEL=1`, three "concurrent" summarization calls queue server-side and serialize. Unbounded fan-out can also cause 429 bursts on cloud providers. A semaphore caps actual concurrent LLM calls without changing parallelism logic.
**Effort:** ~10 LOC.

### G2: Tool-metadata in FTS index
**From:** hermes commits `cfcad80e` + `8d76d69d` — triggers concat `content || tool_name || tool_calls` into the FTS index.
**Where:** `co_cli/memory/indexer.py` — `extract_messages()` currently indexes message content only.
**Why:** Tool-invocation history (e.g. "when did co last call memory_create?") is not searchable in T1.
**Effort:** ~15 LOC in `extract_messages()` + trigger schema update in `session_store.py`.

### G3: Parent-session lineage resolution
**Where:** `co_cli/memory/session_store.py` — `sync_sessions()` exclusion logic.
**Why:** Current exclusion is path-equality only (skips the active session). When delegation lands, a forked sub-agent session shares parent-session lineage but a different path — it will appear in T1 results even when it should be excluded as part of the current logical session.
**Effort:** ~20 LOC — needs `session_meta` parent-chain traversal during sync or at query time.
**Dependency:** Blocker lands when delegation is active; low urgency until then.

### G4: CJK trigram sidecar
**From:** hermes commit `1fa76607` — `messages_fts_trigram` virtual table alongside `messages_fts`.
**Where:** `co_cli/memory/session_store.py` schema + `search_sessions()`.
**Why:** Porter tokenizer (`tokenize='porter unicode61'`) fragments CJK characters. A trigram sidecar enables substring search for Chinese/Japanese/Korean content without replacing the porter index.
**Effort:** ~25 LOC (schema + dual-query path). Low-impact for current English-only users; defer until non-Latin content is a real use case.

### G5: Live-session search
**Where:** `co_cli/memory/session_store.py` — `sync_sessions()` skips the active session path during bootstrap.
**Why:** In-progress session turns are not searchable via T1. A user asking "what did I say earlier about X?" within a long session gets no T1 results for the current session.
**Effort:** ~30 LOC — needs either a shutdown reindex pass or an in-memory overlay of the active session's messages at query time.
**Note:** The in-memory overlay approach avoids writes and is safer for the append-only transcript invariant.

---

## T2 — Knowledge Recall Improvements

Source: openclaw cheap-borrow candidates from peer audit.

### G6: Component score exposure for hybrid search diagnostics
**From:** openclaw commit `66e66f19c6` — `vectorScore` and `textScore` exposed alongside merged score in results.
**Where:** `co_cli/memory/knowledge_store.py` — `_hybrid_search()` return rows.
**Why:** Currently only the merged RRF score is visible. Exposing individual BM25 and vector scores allows tuning hybrid weights without guesswork.
**Effort:** ~10 LOC — add fields to the result dataclass and pass through in `_hybrid_search`.

### G7: Pre-rank source filtering in hybrid search
**From:** openclaw commit `2c716f5677` — `sources: [...]` filter pushed into FTS/vector SQL before the top-N slice.
**Where:** `co_cli/memory/knowledge_store.py` — `_hybrid_search()`.
**Why:** Without source filtering, non-knowledge hits (Obsidian, Drive) can fill the top-N window before knowledge artifacts appear. Relevant if/when T1 sessions are unified into the T2 chunks pipeline.
**Effort:** ~15 LOC — add `source` predicate to both FTS and vector SQL arms.
**Dependency:** More useful if T1 ever migrates to a Family-B chunks pipeline sharing `co-cli-search.db`.

---

## Priority Order

| Priority | Item | Effort | Rationale |
| --- | --- | --- | --- |
| 1 | G1 bounded summarizer concurrency | ~10 LOC | Directly improves T1 reliability on local Ollama; pure win |
| 2 | G2 tool-metadata in FTS | ~15 LOC | Expands T1 recall coverage meaningfully |
| 3 | G6 component score exposure | ~10 LOC | Low-risk diagnostic improvement for T2 tuning |
| 4 | G3 parent-session lineage | ~20 LOC | Correctness fix; only urgent when delegation is active |
| 5 | G7 pre-rank source filtering | ~15 LOC | Nice-to-have; only load-bearing if T1/T2 unify |
| 6 | G5 live-session search | ~30 LOC | Real UX gap but safe to defer |
| 7 | G4 CJK trigram sidecar | ~25 LOC | Defer until non-Latin content is a real use case |
