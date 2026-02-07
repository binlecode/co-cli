# TODO: Web Search Tool (MVP)

**Status:** Proposed
**Date:** 2026-02-07
**Goal:** Add a minimal, reliable `web_search` tool to co-cli using patterns validated in top CLI systems' source code.

---

## 1. Synthesis from Top CLI Source Code

### 1.1 Observed implementation patterns

| System | Source signal | Pattern to adopt |
|---|---|---|
| Gemini CLI | `packages/core/src/tools/web-search.ts` validates non-empty query, returns structured tool output, and appends sources/citations | Validate inputs aggressively and return structured results with sources |
| Gemini CLI | `packages/core/src/tools/web-search.test.ts` covers empty query, no-result, error typing, and citation formatting | Add focused behavior tests for empty query, no-result, error, and source formatting |
| OpenCode | `packages/opencode/src/tool/websearch.ts` enforces timeout + cancellation + permission metadata | Enforce timeout/cancellation and include metadata for observability |
| Codex | `codex-rs/core/src/config/mod.rs` + `core/tests/suite/web_search.rs` model `web_search_mode` (`disabled`/`cached`/`live`) and policy-aware resolution | Keep a small mode switch in config, default to conservative mode |
| Aider | `aider/commands.py` + `aider/scrape.py` keeps `/web` as a simple, explicit command path | Keep MVP simple: one tool, one provider, clear UX/errors |

### 1.2 Cross-system best practices (synthesized)

1. Strong parameter validation (`query` required, bounded length).
2. Deterministic timeout and cancellation path.
3. Structured output with source URLs and a display-ready summary.
4. Actionable, typed errors (`ModelRetry` in co-cli).
5. Explicit config switch to disable web search in restricted setups.
6. Start with one provider; defer provider abstraction until needed.

---

## 2. MVP Scope

### In scope

1. New read-only tool: `web_search(query: str, max_results: int = 5) -> dict[str, Any]`.
2. Single provider integration (Brave Search API).
3. Result normalization to:
   - `title`
   - `url`
   - `snippet`
4. Return object with:
   - `display` (formatted text for user)
   - `results` (normalized list)
   - `count`
   - `has_more`
   - `provider`
5. Config flags and limits:
   - enable/disable
   - API key
   - timeout seconds
   - default/max result count

### Out of scope (post-MVP)

1. Multi-provider routing/fallback.
2. Fetching page contents (`web_fetch`) and summarization pipeline.
3. Caching/indexing.
4. Advanced filtering (domain include/exclude, recency windows, locale tuning).
5. MCP-based search provider integration.

---

## 3. Proposed co-cli API + Config (MVP)

### 3.1 Tool contract

```python
async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    ...
```

Rules:

1. Reject empty/whitespace query.
2. Cap `max_results` to config max.
3. Timeout per request (config-bound).
4. Raise `ModelRetry` with actionable messages on configuration/network/API errors.

### 3.2 Config additions (`co_cli/config.py`)

1. `web_search_enabled: bool = False`
2. `web_search_provider: Literal["brave"] = "brave"`
3. `brave_search_api_key: Optional[str] = None`
4. `web_search_timeout_sec: int = 12`
5. `web_search_max_results: int = 8`

Env mapping:

1. `CO_CLI_WEB_SEARCH_ENABLED`
2. `BRAVE_SEARCH_API_KEY`
3. `CO_CLI_WEB_SEARCH_TIMEOUT_SEC`
4. `CO_CLI_WEB_SEARCH_MAX_RESULTS`

### 3.3 Deps additions (`co_cli/deps.py`)

1. `web_search_enabled: bool = False`
2. `web_search_provider: str = "brave"`
3. `brave_search_api_key: str | None = None`
4. `web_search_timeout_sec: int = 12`
5. `web_search_max_results: int = 8`

Wire from `create_deps()` in `co_cli/main.py`.

---

## 4. Implementation Plan (MVP)

1. Add `co_cli/tools/web.py`.
2. Implement `web_search` with `httpx.AsyncClient` and timeout.
3. Normalize Brave response into canonical result records.
4. Build compact `display` output (top N with title + URL + snippet).
5. Register tool in `co_cli/agent.py` as read-only (`agent.tool(web_search)`).
6. Add settings + env support and wire deps.
7. Add tests for:
   - query validation
   - missing API key
   - result normalization
   - no-results behavior
   - timeout/error mapping

---

## 5. Error Handling Contract

Use `ModelRetry` messages that instruct the model/user what to do next.

Examples:

1. Missing config: `Web search not configured. Set BRAVE_SEARCH_API_KEY and enable web_search_enabled.`
2. Empty query: `Query is required for web_search.`
3. Timeout: `Web search timed out. Retry with a shorter query.`
4. Provider failure: `Web search provider error (HTTP 429). Retry later.`

---

## 6. Security + Safety (MVP)

1. Treat as read-only tool (no approval prompt required).
2. Do not execute any URLs/content in browser during search.
3. Keep strict request timeout and result count cap.
4. Redact API key from logs/spans.
5. Add explicit disable switch (`web_search_enabled=false`) for locked-down environments.

---

## 7. Acceptance Criteria

1. `web_search` is callable by the agent and returns stable structured output.
2. Missing/misconfigured API key yields actionable `ModelRetry`.
3. Tool honors timeout and max result cap.
4. Tool output includes URLs for attribution.
5. No changes to approval flow needed for this read-only tool.
6. `uv run pytest` passes.

---

## 8. File Checklist

1. `co_cli/tools/web.py` (new)
2. `co_cli/agent.py` (register tool)
3. `co_cli/config.py` (settings + env mapping)
4. `co_cli/deps.py` (runtime fields)
5. `co_cli/main.py` (`create_deps` wiring)
6. `tests/test_web_search.py` (new)

---

## 9. Source Code References Consulted

1. `/Users/binle/workspace_genai/gemini-cli/packages/core/src/tools/web-search.ts`
2. `/Users/binle/workspace_genai/gemini-cli/packages/core/src/tools/web-search.test.ts`
3. `/Users/binle/workspace_genai/gemini-cli/packages/core/src/tools/web-fetch.ts`
4. `/Users/binle/workspace_genai/opencode/packages/opencode/src/tool/websearch.ts`
5. `/Users/binle/workspace_genai/opencode/packages/opencode/src/tool/registry.ts`
6. `/Users/binle/workspace_genai/codex/codex-rs/core/src/config/mod.rs`
7. `/Users/binle/workspace_genai/codex/codex-rs/core/tests/suite/web_search.rs`
8. `/Users/binle/workspace_genai/codex/codex-rs/core/src/client_common.rs`
9. `/Users/binle/workspace_genai/aider/aider/commands.py`
10. `/Users/binle/workspace_genai/aider/aider/scrape.py`

