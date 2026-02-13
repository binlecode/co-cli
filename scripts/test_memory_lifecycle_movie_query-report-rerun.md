# REVIEW: Co Learns About a Movie (Qwen Agentic Rerun)

One E2E test. No mocks. Live model, live web, live memory tools. This rerun documents the same memory lifecycle scenario using the Qwen agentic Ollama model with explicit env vars.

**Date:** 2026-02-12  
**Test:** `test_ollama_web_research_and_save`  
**Model:** `qwen3:30b-a3b-thinking-2507-q8_0-agentic`  
**Runner script:** `scripts/test_memory_lifecycle_movie_query.py`
**Env vars in script:**
- `LLM_PROVIDER=ollama`
- `OLLAMA_MODEL=qwen3:30b-a3b-thinking-2507-q8_0-agentic`
- `OLLAMA_NUM_CTX=262144`

---

## Test Result

The E2E test passed with the Qwen agentic profile:

```text
.
1 passed in 39.36s
```

This confirms the test executes against the intended model/context configuration and still reaches the expected deferred `save_memory` approval step.

---

## The Conversation

### Turn 0 — Setup

The run starts with system prompt + user prompt:

> Go online and learn from Wikipedia about the movie Finch. Save a short summary of what you learn to memory.

### Turn 1 — Model plans and searches

Co calls:

```text
web_search({"query":"Finch movie Wikipedia"})
```

### Turn 2 — Search returns Wikipedia snippet

`web_search` returns results including:

- **Finch (film) - Wikipedia**
- URL: `https://en.wikipedia.org/wiki/Finch_(film)`

### Turn 3 — Model tries direct fetch

Co calls:

```text
web_fetch({"url":"https://en.wikipedia.org/wiki/Finch_(film)"})
```

### Turn 4 — Fetch fails with 403

The tool returns retry feedback:

```text
web_fetch error (HTTP 403) for https://en.wikipedia.org/wiki/Finch_(film).
```

This is emitted to the model as `RetryPromptPart`.

### Turn 5 — Model adapts and saves

Co continues from search snippet knowledge and calls:

```text
save_memory(
  content="Finch (2021) is a post-apocalyptic film directed by Miguel Sapochnik ...",
  tags=["movie","summary"]
)
```

`save_memory` is approval-gated, so output is `DeferredToolRequests` with pending `save_memory`.

---

## Full Message Sequence

```text
Message 0  [ModelRequest]
  SystemPromptPart    -> system instructions + tool menu
  UserPromptPart      -> "Go online and learn from Wikipedia about the movie Finch..."

Message 1  [ModelResponse]
  ThinkingPart        -> plans tool usage
  ToolCallPart        -> web_search({"query":"Finch movie Wikipedia"})

Message 2  [ModelRequest]
  ToolReturnPart      -> search results (includes Wikipedia page + snippet)

Message 3  [ModelResponse]
  ThinkingPart        -> decides to fetch Wikipedia URL
  ToolCallPart        -> web_fetch({"url":"https://en.wikipedia.org/wiki/Finch_(film)"})

Message 4  [ModelRequest]
  RetryPromptPart     -> web_fetch error (HTTP 403) ...

Message 5  [ModelResponse]
  ThinkingPart        -> adapts using snippets already available
  ToolCallPart        -> save_memory({... tags=["movie","summary"]})
```

---

## Observations

1. The run is now pinned to the Qwen agentic model via script-level env vars.
2. The memory lifecycle behavior is unchanged and correct: research first, then deferred save.
3. In the pre-fix run, `web_fetch` received HTTP 403 from Wikipedia.
4. The model recovered by summarizing from search results and proceeding to `save_memory`.

---

## Post-fix Update (same day)

After updating `web_fetch` request headers for Wikimedia policy compliance, direct `web_fetch` against `https://en.wikipedia.org/wiki/Finch_(film)` succeeds (`200`) and returns page content.

A fresh agent trace also showed:

- `CALL web_search`
- `CALL web_fetch`
- `FETCH_RETURN` content from Wikipedia
- No `RetryPromptPart` for `web_fetch`

This confirms the 403 path is mitigated by the header fix in `co_cli/tools/web.py`.
