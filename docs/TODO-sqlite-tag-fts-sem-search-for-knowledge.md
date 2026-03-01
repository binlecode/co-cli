# TODO: Knowledge System — Phase 3 Reranker

**Scope:** All text sources co-cli touches — knowledge files (memories, articles), Obsidian notes, Google Drive docs
**Reference:** [OpenClaw memory system](~/workspace_genai/openclaw/src/memory/)

---

## Current State

All three phases delivered:

1. **Flat storage + articles as first-class kind** — ✅ Shipped
2. **FTS5 BM25 search** — ✅ Shipped. See `docs/DESIGN-knowledge.md`.
3. **Hybrid semantic search (Phase 2)** — ✅ Shipped. `KnowledgeIndex` hybrid backend with sqlite-vec + embedding cache + weighted merge. Config: `CO_KNOWLEDGE_SEARCH_BACKEND=hybrid`.

---

## Phase 3 — LLM Listwise Reranking

**When:** Hybrid search (Phase 2) returns relevant-but-misordered results — e.g. the right answer sits at position 3–5 because BM25 and vector signals agree on the wrong document for an ambiguous query. Reranking fixes the final ordering with semantic understanding of the (query, doc) relationship.

**Why LLM, not a dedicated reranker model:** Ollama's `/api/rerank` endpoint is not implemented as of April 2025 ([issue #10467](https://github.com/ollama/ollama/issues/10467)) — models exist in the library but the API doesn't work. `llama-cpp-python` with a GGUF cross-encoder is the alternative, but adds a ~500MB Python dep and a ~600MB model download. The LLM listwise approach uses the generation model already configured — zero new deps, works today. **Important caveat:** dedicated cross-encoders (BGE-Reranker, ms-marco-MiniLM) score ~0.78 NDCG@10 vs ~0.70 for LLM-based rerankers in 2025–2026 benchmarks — they are the production best practice. LLM listwise is a pragmatic Phase 3.1 choice given zero-dep constraints and a small candidate pool (≤ 40 docs in a personal CLI). Phase 3.2 upgrades to a proper cross-encoder.

**Listwise vs. pointwise:** Listwise sends all candidates in one context and asks the LLM to return a ranked ordering — one inference call, O(1) cost, no calibration issues. Pointwise (score each doc separately) is 9× more expensive and 35× slower per benchmarks, and A/B data (fin.ai) shows listwise adds +40% latency vs pointwise for the same resolution rate. Listwise is the right choice for Phase 3.1 given our constraints.

**Phase 3.1 (this TODO):** LLM listwise reranking using existing Gemini or Ollama generation model. No new Python deps.

---

### Design decisions (TL-resolved)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approach | LLM listwise | No dedicated reranker endpoint in Ollama; one inference call; comparable quality for ≤ 40 docs |
| Providers | `"none"` \| `"ollama"` \| `"gemini"` | Mirrors embedding provider naming; Ollama uses generation model; Gemini uses `generate_content` |
| Reranker model | Configurable; defaults `""` (= use a fast model per provider) | Ollama default `"qwen2.5:3b"`, Gemini default `"gemini-2.0-flash"` |
| Score handling | Rank-position score: `1.0 - rank / len(candidates)` | Listwise returns ordering, not calibrated scores; rank-position is transparent and consistent |
| Text sent to reranker | `title + content[:200]` per candidate, all in one prompt | 40 docs × 200 chars ≈ 8k chars — fits Gemini Flash and most Ollama models |
| Quality expectation | Beats no-reranking and FTS-only order; below dedicated cross-encoders (~0.70 vs ~0.78 NDCG@10) | Acceptable for personal assistant CLI; Phase 3.2 addresses the gap with a GGUF cross-encoder |
| Candidate pool | All `merged` from `_hybrid_merge()` (up to `limit*4`) | Reranker picks top `limit` from full pool |
| Integration point | `_hybrid_search()` after `_hybrid_merge()`, before `[:limit]` slice | One-line change to existing return |
| Fallback | Any exception → log warning → `merged[:limit]` (hybrid order) | Matches existing embedding fallback pattern |
| Score caching | None | Query-specific ordering; can't cache |
| CoDeps changes | None | Config lives entirely inside `KnowledgeIndex` |
| New deps | None | `httpx` and `google-genai` already required |

---

### Step 1 — Config additions (`co_cli/config.py`)

Add two fields to `Settings` after the embedding settings:

```python
knowledge_reranker_provider: Literal["none", "ollama", "gemini"] = "none"
knowledge_reranker_model: str = ""  # "" = provider default (qwen2.5:3b / gemini-2.0-flash)
```

Add two env var mappings in `fill_from_env`:

```python
"knowledge_reranker_provider": "CO_KNOWLEDGE_RERANKER_PROVIDER",
"knowledge_reranker_model":    "CO_KNOWLEDGE_RERANKER_MODEL",
```

---

### Step 2 — KnowledgeIndex changes (`co_cli/knowledge_index.py`)

#### 2a. Constructor — add two params

```python
def __init__(
    self,
    db_path: Path,
    *,
    backend: str = "fts5",
    embedding_provider: str = "ollama",
    embedding_model: str = "embeddinggemma",
    embedding_dims: int = 256,
    ollama_host: str = "http://localhost:11434",
    gemini_api_key: str | None = None,
    hybrid_vector_weight: float = 0.7,
    hybrid_text_weight: float = 0.3,
    reranker_provider: str = "none",   # NEW: "none" | "ollama" | "gemini"
    reranker_model: str = "",          # NEW: "" = provider default
) -> None:
```

Store as `self._reranker_provider` and `self._reranker_model`.

Resolve the default model in `__init__` immediately after storing:

```python
if not self._reranker_model:
    self._reranker_model = (
        "gemini-2.0-flash" if self._reranker_provider == "gemini" else "qwen2.5:3b"
    )
```

#### 2b. `_hybrid_search()` — replace final return

Current (line 300):
```python
return merged[:limit]
```

Replace with:
```python
return self._rerank_results(query, merged, limit)
```

The fallback path at line 303 (`return fts_results[:limit]`) stays unchanged — FTS-only fallback is not reranked.

#### 2c. New method `_rerank_results()`

```python
def _rerank_results(
    self,
    query: str,
    candidates: list[SearchResult],
    limit: int,
) -> list[SearchResult]:
    """Re-rank hybrid candidates via cross-encoder. Returns top limit results.

    Falls back to hybrid order on any provider failure.
    Short-circuits when reranker_provider is 'none' or candidates is empty.
    """
    if self._reranker_provider == "none" or not candidates:
        return candidates[:limit]
    try:
        texts = self._fetch_reranker_texts(candidates)
        scores = self._generate_rerank_scores(query, texts)
        reranked = [
            SearchResult(
                source=r.source,
                kind=r.kind,
                path=r.path,
                title=r.title,
                snippet=r.snippet,
                score=scores[i],
                tags=r.tags,
                category=r.category,
                created=r.created,
                updated=r.updated,
            )
            for i, r in enumerate(candidates)
        ]
        reranked.sort(key=lambda r: r.score, reverse=True)
        return reranked[:limit]
    except Exception as e:
        logger.warning(f"Reranking failed, falling back to hybrid order: {e}")
        return candidates[:limit]
```

#### 2d. New method `_fetch_reranker_texts()`

Batch-fetch `title + content[:200]` for all candidates in one SQL query. 200 chars × 40 docs ≈ 8k chars — fits Gemini Flash and Qwen2.5:3b comfortably.

```python
def _fetch_reranker_texts(self, candidates: list[SearchResult]) -> list[str]:
    """Batch-fetch title + content excerpt for each candidate from docs table."""
    paths = [r.path for r in candidates]
    placeholders = ",".join("?" * len(paths))
    rows = self._conn.execute(
        f"SELECT path, title, content FROM docs WHERE path IN ({placeholders})",
        paths,
    ).fetchall()
    by_path = {row["path"]: row for row in rows}
    texts = []
    for r in candidates:
        row = by_path.get(r.path)
        if row:
            title = (row["title"] or "").strip()
            content = (row["content"] or "")[:200].strip()
            texts.append(f"{title}\n{content}".strip() if title else content)
        else:
            # DB miss — fall back to whatever's already on the result
            texts.append(r.title or "")
    return texts
```

#### 2e. New method `_generate_rerank_scores()`

Dispatch to provider. Returns rank-position scores: `1.0 - rank / len(texts)`, aligned to input order.

```python
def _generate_rerank_scores(self, query: str, texts: list[str]) -> list[float]:
    """Call the configured reranker and return rank-position scores."""
    if self._reranker_provider in ("ollama", "gemini"):
        return self._llm_rerank(query, texts)
    return [0.0] * len(texts)
```

#### 2f. New method `_llm_rerank()` and helpers

`_llm_rerank()` builds a numbered list prompt and converts the LLM's ranked-index response into positional scores:

```python
def _llm_rerank(self, query: str, texts: list[str]) -> list[float]:
    """Listwise LLM reranking. Returns rank-position scores aligned to texts.

    Prompts the LLM with all candidates numbered 1..N and asks it to return a
    JSON array of 1-based indices ordered from most to least relevant.
    Score formula: scores[idx] = 1.0 - rank / len(texts)
    """
    n = len(texts)
    numbered = "\n\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts))
    prompt = (
        f"Rank these {n} documents by relevance to the query.\n"
        f"Query: {query}\n\n"
        f"Documents:\n{numbered}\n\n"
        f"Return ONLY a JSON array of document numbers (1-based) ordered from most to least relevant.\n"
        f"Example: [2, {n}, 1]"
    )
    ranked_1based = self._call_reranker_llm(prompt, n)
    scores = [0.0] * n
    for rank, idx_1based in enumerate(ranked_1based):
        idx = int(idx_1based) - 1
        if 0 <= idx < n:
            scores[idx] = 1.0 - rank / n
    return scores
```

`_call_reranker_llm()` dispatches to the correct generation API:

```python
def _call_reranker_llm(self, prompt: str, n: int) -> list[int]:
    """Dispatch to Ollama /api/generate or Gemini generate_content. Returns 1-based indices."""
    if self._reranker_provider == "ollama":
        return self._ollama_generate_ranked(prompt, n)
    if self._reranker_provider == "gemini":
        return self._gemini_generate_ranked(prompt, n)
    return list(range(1, n + 1))

def _ollama_generate_ranked(self, prompt: str, n: int) -> list[int]:
    import json
    import httpx
    resp = httpx.post(
        f"{self._ollama_host}/api/generate",
        json={"model": self._reranker_model, "prompt": prompt, "format": "json", "stream": False},
        timeout=30.0,
    )
    resp.raise_for_status()
    return json.loads(resp.json().get("response", "[]"))

def _gemini_generate_ranked(self, prompt: str, n: int) -> list[int]:
    import json
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=self._gemini_api_key)
    response = client.models.generate_content(
        model=self._reranker_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)
```

---

### Step 3 — Wire in `main.py`

In `create_deps()`, extend the `KnowledgeIndex` constructor call:

```python
knowledge_index = KnowledgeIndex(
    DATA_DIR / "search.db",
    backend=settings.knowledge_search_backend,
    embedding_provider=settings.knowledge_embedding_provider,
    embedding_model=settings.knowledge_embedding_model,
    embedding_dims=settings.knowledge_embedding_dims,
    ollama_host=settings.ollama_host,
    gemini_api_key=settings.gemini_api_key,
    hybrid_vector_weight=settings.knowledge_hybrid_vector_weight,
    hybrid_text_weight=settings.knowledge_hybrid_text_weight,
    reranker_provider=settings.knowledge_reranker_provider,   # NEW
    reranker_model=settings.knowledge_reranker_model,         # NEW
)
```

---

### Step 4 — Tests (`tests/test_knowledge_index.py`)

Three tests. Tests 1 and 2 require no external services. Test 3 requires Ollama reachable with a generation model pulled (skip otherwise).

**`test_reranker_provider_none_is_passthrough`**
- `reranker_provider="none"` — `_rerank_results()` must return candidates unchanged (same objects, same order)

```python
def test_reranker_provider_none_is_passthrough(tmp_path):
    """provider='none' must return hybrid order with hybrid scores unchanged."""
    idx = KnowledgeIndex(tmp_path / "search.db", backend="fts5", reranker_provider="none")
    idx.index(source="memory", kind="memory", path="/a.md",
              title="Alpha", content="reranker passthrough test alpha unique", hash="a", mtime=0.0)
    idx.index(source="memory", kind="memory", path="/b.md",
              title="Beta", content="reranker passthrough test beta unique", hash="b", mtime=0.0)
    candidates = idx._fts_search(
        "reranker passthrough test",
        source=None, kind=None, tags=None,
        tag_match_mode="any", created_after=None, created_before=None,
        limit=10,
    )
    reranked = idx._rerank_results("reranker passthrough test", candidates, limit=10)
    assert reranked == candidates
    idx.close()
```

**`test_rerank_falls_back_on_error`**
- `reranker_provider="ollama"`, `ollama_host` pointing to a dead port
- `/api/generate` call fails → `_rerank_results()` catches exception, returns `candidates[:limit]` unchanged

```python
def test_rerank_falls_back_on_error(tmp_path):
    """Reranker network failure must fall back to input order silently."""
    idx = KnowledgeIndex(
        tmp_path / "search.db",
        backend="fts5",
        reranker_provider="ollama",
        reranker_model="qwen2.5:3b",
        ollama_host="http://localhost:19999",  # dead port
    )
    idx.index(source="memory", kind="memory", path="/c.md",
              title="Gamma", content="reranker fallback error test unique", hash="c", mtime=0.0)
    candidates = idx._fts_search(
        "reranker fallback error test",
        source=None, kind=None, tags=None,
        tag_match_mode="any", created_after=None, created_before=None,
        limit=10,
    )
    result = idx._rerank_results("reranker fallback error test", candidates, limit=10)
    assert result == candidates[:10]
    idx.close()
```

**`test_ollama_listwise_rerank_reorders_results`**
- Skip if Ollama not reachable
- Index two docs: one highly relevant, one off-topic
- After reranking, the relevant doc must appear first; all scores in [0, 1]

```python
def test_ollama_listwise_rerank_reorders_results(tmp_path):
    """Ollama listwise reranker assigns rank-position scores and reorders results."""
    import os
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        import httpx
        httpx.get(f"{ollama_host}/api/tags", timeout=2.0).raise_for_status()
    except Exception:
        pytest.skip("Ollama not reachable")

    idx = KnowledgeIndex(
        tmp_path / "search.db",
        backend="fts5",
        reranker_provider="ollama",
        reranker_model="qwen2.5:3b",  # default; any pulled generation model works
        ollama_host=ollama_host,
    )
    idx.index(source="memory", kind="memory", path="/rel.md",
              title="Python async patterns",
              content="asyncio event loop coroutine await async def gather",
              hash="rel", mtime=0.0)
    idx.index(source="memory", kind="memory", path="/irr.md",
              title="Gardening tips",
              content="water your plants in the morning avoid direct sunlight",
              hash="irr", mtime=0.0)

    candidates = idx._fts_search(
        "async",
        source=None, kind=None, tags=None,
        tag_match_mode="any", created_after=None, created_before=None,
        limit=10,
    )
    try:
        reranked = idx._rerank_results("how does asyncio work", candidates, limit=10)
    except Exception as exc:
        pytest.skip(f"Reranker call failed (model not pulled?): {exc}")

    assert all(0.0 <= r.score <= 1.0 for r in reranked)
    assert reranked[0].path == "/rel.md", "Most relevant doc must rank first"
    idx.close()
```

---

### Implementation order

1. `config.py` — 2 fields + 2 env vars
2. `knowledge_index.py` — constructor params, 4 new methods, 1-line change to `_hybrid_search()`
3. `main.py` — 2 new params in KnowledgeIndex constructor call
4. Tests — 3 tests in `test_knowledge_index.py`

---

### File change summary

| File | Change |
|------|--------|
| `co_cli/config.py` | `knowledge_reranker_provider`, `knowledge_reranker_model` + env vars |
| `co_cli/knowledge_index.py` | Constructor params; `_rerank_results()`, `_fetch_reranker_texts()`, `_generate_rerank_scores()`, `_llm_rerank()`, `_call_reranker_llm()`, `_ollama_generate_ranked()`, `_gemini_generate_ranked()`; 1-line change in `_hybrid_search()` |
| `co_cli/main.py` | Pass 2 new params to `KnowledgeIndex` |
| `tests/test_knowledge_index.py` | 3 Phase 3 tests |

No new runtime deps. No CoDeps changes. No changes to tool layer or agent.

---

### Activation

```bash
# No model pull needed — uses the generation model already configured in Ollama
# (default: qwen2.5:3b; any generation model that can output JSON arrays works)

# Enable reranker in env
CO_KNOWLEDGE_SEARCH_BACKEND=hybrid CO_KNOWLEDGE_RERANKER_PROVIDER=ollama uv run co chat

# Or for Gemini:
CO_KNOWLEDGE_SEARCH_BACKEND=hybrid CO_KNOWLEDGE_RERANKER_PROVIDER=gemini uv run co chat

# Run targeted tests
uv run pytest tests/test_knowledge_index.py::test_reranker_provider_none_is_passthrough
uv run pytest tests/test_knowledge_index.py::test_rerank_falls_back_on_error
uv run pytest tests/test_knowledge_index.py::test_ollama_listwise_rerank_reorders_results  # requires Ollama
```

---

### Phase 3.2 (deferred)

- **`"local"` cross-encoder provider (recommended upgrade):** `llama-cpp-python` + a GGUF cross-encoder model (e.g., `bge-reranker-v2-m3-Q5_K_M.gguf`, ~600MB); no Ollama dependency; 3× faster than LLM listwise and reaches ~0.78 NDCG@10 — the 2026 production best practice. This is the correct upgrade path when zero-dep constraint is relaxed.
- Score blending: `0.3 * hybrid_score + 0.7 * reranker_score` as alternative to pure rank-position replacement — useful once calibrated cross-encoder scores are available
- MMR diversity pass: post-reranker Jaccard-based diversity (OpenClaw pattern); orthogonal to reranking

---

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `knowledge_search_backend` | `CO_KNOWLEDGE_SEARCH_BACKEND` | `"fts5"` | `"grep"` (legacy), `"fts5"`, `"hybrid"` |
| `knowledge_embedding_provider` | `CO_KNOWLEDGE_EMBEDDING_PROVIDER` | `"ollama"` | `"ollama"`, `"gemini"`, `"none"` |
| `knowledge_embedding_model` | `CO_KNOWLEDGE_EMBEDDING_MODEL` | `"embeddinggemma"` | Ollama model name or Gemini model ID |
| `knowledge_embedding_dims` | `CO_KNOWLEDGE_EMBEDDING_DIMS` | `256` | Embedding vector dimensions |
| `knowledge_hybrid_vector_weight` | — | `0.7` | Vector score weight in hybrid merge |
| `knowledge_hybrid_text_weight` | — | `0.3` | Text score weight in hybrid merge |

---

## References

- [OpenClaw memory/](~/workspace_genai/openclaw/src/memory/) — hybrid search, embedding cache, FTS5 query building, score normalization
- [SQLite FTS5](https://www.sqlite.org/fts5.html) — built-in full-text search
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — vector similarity extension
- [EmbeddingGemma-300M](https://ai.google.dev/gemma/docs/embeddinggemma/model_card) — sub-200MB embedding model
- [QMD](https://github.com/tobi/qmd) — FTS5 + sqlite-vec + reranker reference implementation
- [sqlite-vec Hybrid Search](https://alexgarcia.xyz/blog/2024/sqlite-vec-hybrid-search/index.html)
- [llama-stack Hybrid Search](https://github.com/llamastack/llama-stack/issues/1158)
