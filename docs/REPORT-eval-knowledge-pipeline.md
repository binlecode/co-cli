# REVIEW: eval_knowledge_pipeline — Run Report

**Date:** 2026-03-11
**Eval:** `evals/eval_knowledge_pipeline.py`
**Verdict:** PASS
**Total time:** 195.3s

---

## Pipeline Under Test

```
Turn 1: web_search → web_fetch → save_article
Turn 2: (history from T1) → search_knowledge → grounded answer
```

---

## Step-by-Step Trace

### Pre-flight

| Step | Outcome |
|------|---------|
| Import `co_cli._knowledge_index` | OK (after `uv sync` + pyc clear) |
| `get_agent()` | OK — model `qwen3:30b-a3b-thinking-2507-agentic` |
| `KnowledgeIndex(.co-cli/search.db)` | OK — fts5 backend |
| `make_eval_deps()` | OK — library `.co-cli/library`, brave key present |
| Ollama model state | Loaded at 262 144 ctx / 31 545 MB VRAM |

### Turn 1 — Search + Save (165.9s)

| Step | Detail |
|------|--------|
| LLM → `web_search` | query: `Python asyncio event loop best practices` |
| Brave API | Returned results; top hit: `docs.python.org/3/library/asyncio-eventloop.html` |
| LLM → `web_fetch` | URL: `https://docs.python.org/3/library/asyncio-eventloop.html` |
| HTTP fetch | Page downloaded, converted to markdown |
| LLM → `save_article` | title: `Asyncio Event Loop Best Practices`, tag: `python` |
| `SilentFrontend.prompt_approval` | Auto-approved (`"y"`) |
| Article written | `.co-cli/library/001-asyncio-event-loop-best-practices.md` |
| KnowledgeIndex | Indexed as `source=library`, 1 chunk (1 550 chars, lines 0–20) |
| LLM final response | Confirmed save — Turn 1 complete |

**Chain check:** `['web_search', 'web_fetch', 'save_article']` ✅

### Turn 2 — Retrieve + Answer (29.4s)

| Step | Detail |
|------|--------|
| Message history | T1 messages forwarded (agent remembers it saved the article) |
| LLM → `search_knowledge` | query: `asyncio`, source: `library` |
| FTS5 search | 1 result — `001-asyncio-event-loop-best-practices.md`, BM25 ranked |
| Reranker | `fastembed` not installed → skipped, unranked result returned |
| LLM final response | Cited article, summarised key best practices |

**Chain check:** `['search_knowledge']` ✅
**Answer quality:** response mentions "asyncio", length > 50 chars ✅

Answer preview:
```
The knowledge base contains one relevant article on asyncio best practices,
based on the official Python documentation:

### Asyncio Event Loop Best Practices
Source: Official Python Documentation (library)

Key Recommendations:
- Use asyncio.run() for simple entry points...
```

---

## Knowledge Store — Post-Eval State

```
search.db → docs table
  source=library | kind=article
  title=Asyncio Event Loop Best Practices
  path=.co-cli/library/001-asyncio-event-loop-best-practices.md
  content_len=1550 chars | created=2026-03-11T02:09:03Z

search.db → chunks table
  source=library | chunk_index=0 | lines 0–20 | 1550 chars
  (single chunk — article fits within chunk_size=600 threshold)

.co-cli/library/001-asyncio-event-loop-best-practices.md
  frontmatter: kind=article, provenance=web-fetch,
               origin_url=https://docs.python.org/3/library/asyncio-eventloop.html
               tags=[python], decay_protected=true
```

Article will deduplicate on rerun (same `origin_url` → `action=consolidated`).

---

## Issues Found During Run

### 1. Model context reload — cold start hang (RCA: config mismatch)

**What happened:** First run appeared to hang at `[1/1] knowledge-pipeline ...` for ~5 min.

**Root cause:** Ollama had the model loaded at `context_length=131 072` (128K). The model quirks DB sets `num_ctx=262 144` for the `qwen3` family. When the eval sent the first LLM request with `num_ctx=262 144`, Ollama unloaded and reloaded the model with a 256K KV cache (+6 GB VRAM). This reload took several minutes.

**Not a bug** — this is a one-time cold start cost. Subsequent runs are fast because the model stays loaded at 262K. The root issue is that `model_quirks.py` hardcodes `num_ctx=262 144` for qwen3, while Ollama defaults to 128K on model load.

**Recommendation:** On first use, warn if `ollama /api/ps` context_length < quirks `num_ctx` so users know to expect a reload.

---

### 2. `evals/_common.py` — stale `ollama_num_ctx` reference (bug, fixed)

**Error:**
```
AttributeError: 'Settings' object has no attribute 'ollama_num_ctx'.
Did you mean: 'llm_num_ctx'?
```

**Root cause:** `config.py` renamed `ollama_num_ctx` → `llm_num_ctx` as part of a magic-number refactor. `evals/_common.py` and `evals/eval_conversation_history.py` were not updated.

**Fix applied:**
```python
# evals/_common.py line 86
- "ollama_num_ctx": s.ollama_num_ctx,
+ "llm_num_ctx": s.llm_num_ctx,

# evals/eval_conversation_history.py line 439
- num_ctx = inf.get("num_ctx", settings.ollama_num_ctx)
+ num_ctx = inf.get("num_ctx", settings.llm_num_ctx)
```

---

### 3. `_knowledge_index.py` import error — stale `.pyc` cache (transient, fixed)

**Error:**
```
NameError: name 'DEFAULT_KNOWLEDGE_SEARCH_BACKEND' is not defined
```

**Root cause:** `_knowledge_index.py` had uncommitted changes adding the import of `DEFAULT_KNOWLEDGE_SEARCH_BACKEND` from `config.py`. Stale `.pyc` files were being executed instead of the working-tree source. `uv sync` + pyc clear resolved it.

**Not a persistent bug** — stale bytecode from in-progress working tree changes.

---

### 4. `fastembed` not installed — reranker degraded

**Warning:**
```
fastembed not installed; falling back to unranked results (uv sync --group reranker)
```

**Impact:** Turn 2 results were returned unranked. With only 1 article in the library this doesn't affect correctness, but reranking quality would matter at scale.

**Fix:** `uv sync --group reranker` installs fastembed.

---

## Score Summary

| Dimension | Expected | Actual | Result |
|-----------|----------|--------|--------|
| Turn 1 chain | `web_search → web_fetch → save_article` | `web_search → web_fetch → save_article` | ✅ |
| Turn 2 chain | any of `search_knowledge`, `recall_article` | `search_knowledge` | ✅ |
| Answer quality | mentions "asyncio", len > 50 | ✅ | ✅ |
| **Overall** | | | **PASS** |
