# REPORT: Retrieval Flow Validation
_Date: 2026-03-11_

## Scope

Validate the real retrieval flow after switching knowledge defaults to TEI-backed hybrid retrieval, then document the outcomes, blockers found, fixes applied, and test/runtime observations.

## Config Changes Applied

Updated [co_cli/config.py](/Users/binle/workspace_genai/co-cli/co_cli/config.py):

- `DEFAULT_KNOWLEDGE_SEARCH_BACKEND`: `fts5` -> `hybrid`
- `DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER`: `ollama` -> `tei`
- `DEFAULT_KNOWLEDGE_RERANKER_PROVIDER`: `local` -> `tei`

Verified loaded runtime settings:

- `knowledge_search_backend = hybrid`
- `knowledge_embedding_provider = tei`
- `knowledge_embed_api_url = http://127.0.0.1:8283`
- `knowledge_reranker_provider = tei`
- `knowledge_rerank_api_url = http://127.0.0.1:8282`

## Live Service Checks

### TEI

Validated with escalated local HTTP checks:

- `http://127.0.0.1:8283/` -> `HTTP/1.1 200 OK`
- `http://127.0.0.1:8282/` -> `HTTP/1.1 200 OK`

Conclusion: TEI embedder and reranker were live and reachable from the unrestricted host environment.

### Ollama

Observed two different states during the session:

- Direct sandboxed checks could not reliably reach Ollama.
- Escalated check to `http://localhost:11434/api/tags` returned `HTTP/1.1 200 OK`.

Conclusion: Ollama was available from the host environment, but not reliably reachable from the sandboxed test/process context used earlier in the session.

## Eval / Seeding Findings

To seed the real knowledge base, the existing eval [evals/eval_knowledge_pipeline.py](/Users/binle/workspace_genai/co-cli/evals/eval_knowledge_pipeline.py) was inspected and run.

### Bug Found

The eval failed immediately with:

- `NameError: name 'TURN1_EXPECTED_CHAIN' is not defined`

### Fix Applied

Added missing constants to [evals/eval_knowledge_pipeline.py](/Users/binle/workspace_genai/co-cli/evals/eval_knowledge_pipeline.py):

- `TURN1_EXPECTED_CHAIN = ["web_search", "web_fetch", "save_article"]`
- `TURN2_EXPECTED_TOOLS = {"search_knowledge", "recall_article"}`

### Seeding Outcome

After running the eval path, the real knowledge base contained:

- `.co-cli/library/001-asyncio-event-loop-best-practices.md`

And the real index contained one `library` document:

- title: `Asyncio Event Loop Best Practices`

Conclusion: the real knowledge base was successfully populated before final retrieval validation.

## New Whole-Flow Tests

Replaced the earlier mocked TEI whole-flow tests with real provider-dependent tests in [tests/test_save_article.py](/Users/binle/workspace_genai/co-cli/tests/test_save_article.py):

- `test_search_knowledge_hybrid_whole_flow_real_embedder_populates_vec_rows`
- `test_search_knowledge_hybrid_whole_flow_real_reranker_changes_scores`

These tests:

- use real runtime settings from `co_cli.config.settings`
- instantiate a real `KnowledgeIndex` in `backend="hybrid"`
- save real articles through `save_article(...)`
- run retrieval through `search_knowledge(...)`
- fail if embeddings are not actually written to `docs_vec` / `chunks_vec`
- fail if reranking does not actually change final retrieval scores relative to a no-reranker baseline

## Retrieval Regression Found and Fixed

After changing the default backend to `hybrid`, the existing test
[tests/test_knowledge_index.py::test_search_stopword_only_returns_empty](/Users/binle/workspace_genai/co-cli/tests/test_knowledge_index.py)
started failing.

### Cause

`KnowledgeIndex.search()` delegated directly to `_hybrid_search()` when backend was `hybrid`, so stopword-only queries could fall through to semantic retrieval instead of respecting the existing contract that such queries return `[]`.

### Fix Applied

Patched [co_cli/_knowledge_index.py](/Users/binle/workspace_genai/co-cli/co_cli/_knowledge_index.py) so `search()` now short-circuits before backend dispatch:

- `if self._build_fts_query(query) is None: return []`

This preserves the public contract for empty / stopword-only input across both `fts5` and `hybrid`.

## Validation Results

### Real Whole-Flow Tests

Ran with live TEI access:

```bash
uv run pytest \
  tests/test_save_article.py::test_search_knowledge_hybrid_whole_flow_real_embedder_populates_vec_rows \
  tests/test_save_article.py::test_search_knowledge_hybrid_whole_flow_real_reranker_changes_scores \
  -x
```

Result:

- `2 passed in 2.55s`

### Full Retrieval Slice

Ran with live TEI access:

```bash
uv run pytest tests/test_chunker.py tests/test_knowledge_index.py tests/test_save_article.py -x
```

Result:

- `79 passed in 91.19s`

Conclusion: after the config switch and the stopword regression fix, the retrieval feature slice passed end to end under live TEI-backed hybrid defaults.

## Slow Test Analysis

Pytest duration output showed these dominant slow tests:

1. [tests/test_save_article.py:608](/Users/binle/workspace_genai/co-cli/tests/test_save_article.py#L608)
   `test_search_knowledge_default_excludes_memories` — `32.59s`

2. [tests/test_save_article.py:344](/Users/binle/workspace_genai/co-cli/tests/test_save_article.py#L344)
   `test_search_knowledge_fts_kind_filter` — `29.22s`

3. [tests/test_knowledge_index.py:304](/Users/binle/workspace_genai/co-cli/tests/test_knowledge_index.py#L304)
   `test_fts_roundtrip_save_and_recall` — `20.19s`

### Root Cause of Slowness

These are not slow because retrieval itself is slow.

They are slow because they call `save_memory()`, which goes through the real memory lifecycle and may invoke LLM-driven consolidation:

- [co_cli/tools/memory.py:499](/Users/binle/workspace_genai/co-cli/co_cli/tools/memory.py#L499)
- [co_cli/_memory_lifecycle.py:180](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L180)
- [co_cli/_memory_lifecycle.py:186](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L186)
- [co_cli/_memory_lifecycle.py:190](/Users/binle/workspace_genai/co-cli/co_cli/_memory_lifecycle.py#L190)

So the expensive part is test setup / memory persistence, not the retrieval assertion itself.

### Important Contrast

The new live TEI whole-flow retrieval tests were comparatively cheap:

- embedder whole-flow test — about `0.23s`
- reranker whole-flow test — about `0.24s`

Conclusion: TEI-backed retrieval was not the dominant runtime cost in this slice. The expensive path was memory consolidation via `save_memory()`.

## Clarification: Why `save_memory()` Appears in Retrieval Tests

`save_memory()` is not called during `save_article()`.

It appears in certain retrieval tests because those tests intentionally create both:

- one article via `save_article(...)`
- one memory via `save_memory(...)`

This is done so retrieval partition behavior can be tested honestly, for example:

- article-only filtering
- default search excluding memories
- explicit `source="memory"` escape hatch

So the sequence in those tests is:

1. save article
2. save memory
3. query retrieval path

The expensive part is step 2, not step 3.

## Clarification: Why No New Files Appeared in `.co-cli/memory`

Those tests use `tmp_path`, not the repo’s real `.co-cli/memory` directory.

Examples:

- [tests/test_save_article.py:353](/Users/binle/workspace_genai/co-cli/tests/test_save_article.py#L353)
- [tests/test_save_article.py:613](/Users/binle/workspace_genai/co-cli/tests/test_save_article.py#L613)

So any memory files written by those tests exist only under pytest-managed temp directories and are cleaned up after the run.

This is compliant with repo policy. `CLAUDE.md` requires:

- real functional code paths
- isolated filesystem writes via `tmp_path`

Therefore:

- using `tmp_path` is not a violation
- mocking provider boundaries in `tests/` is the actual policy violation

## Final Assessment

### Code / Runtime

- Retrieval defaults are now TEI-backed hybrid.
- Live TEI embedder and reranker were validated.
- Real knowledge was inserted into the knowledge base.
- Real whole-flow retrieval tests passed.
- Full retrieval regression slice passed after fixing the hybrid stopword regression.

### Follow-up Still Pending

- Docs are now stale where they still say retrieval defaults are `fts5` / `ollama` / `local`.
- The eval fix in [evals/eval_knowledge_pipeline.py](/Users/binle/workspace_genai/co-cli/evals/eval_knowledge_pipeline.py) should be retained.
- If test runtime becomes an issue, the main target should be memory-lifecycle-heavy retrieval tests that use `save_memory()` when simpler retrieval seeding would suffice.
