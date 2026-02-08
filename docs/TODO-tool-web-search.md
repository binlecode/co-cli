# TODO: Web Intelligence Tools (MVP)

**Goal:** Add `web_search` and `web_fetch` as baseline perception tools — co-cli's "eyes" for external context. See `DESIGN-co-evolution.md` Phase 1.

---

## Converged Patterns

Two reference systems implement explicit search + fetch tools:

| System | Search | Fetch | Key patterns |
|---|---|---|---|
| Gemini CLI | `google_web_search` — structured results with citations | `web_fetch` — HTML-to-text fallback, 100KB limit, private IP detection | Separate tools, validate inputs, return structured output with source URLs |
| OpenCode | `websearch` — Exa AI via MCP, 25s timeout | `webfetch` — direct HTTP, TurndownService HTML→markdown, 5MB limit | Separate tools, format param, size limits, permission gate |

Codex delegates to Claude API's built-in search (not a separate tool — not applicable to co-cli's tool architecture). Aider has no agent-callable web tools.

**Synthesized best practices:**

1. Search and fetch are separate tools (both systems converge).
2. Strong parameter validation (query required, bounded length).
3. Hardcoded timeout and size limits (no per-tool config knobs).
4. Structured output with source URLs and display-ready summary.
5. HTML-to-markdown conversion for fetched pages.
6. One provider, no abstraction until a second is needed.
7. Actionable error messages (`ModelRetry` in co-cli).

---

## MVP Scope

### In scope

1. `web_search` — query Brave Search API, return structured results.
2. `web_fetch` — fetch a URL, convert HTML to markdown, return text.
3. Both read-only (no approval needed).

### Out of scope (post-MVP)

1. Multi-provider routing/fallback.
2. LLM-powered summarization of fetched content.
3. Caching/indexing.
4. Domain filtering, recency windows, locale tuning.
5. Private IP / SSRF protection (known limitation — document in security section).

---

## Tool Contracts

### `web_search`

```python
async def web_search(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search the web. Returns results with title, URL, and snippet."""
```

Rules:
1. Reject empty/whitespace query → `ModelRetry`.
2. Cap `max_results` to `_MAX_RESULTS = 8`.
3. Timeout: `_SEARCH_TIMEOUT = 12` seconds.
4. `ModelRetry` on missing key, timeout, HTTP errors.

Return shape:
```python
{
    "display": "1. **Title** — snippet\n   https://...\n\n2. ...",
    "results": [{"title": str, "url": str, "snippet": str}, ...],
    "count": int,
}
```

### `web_fetch`

```python
async def web_fetch(
    ctx: RunContext[CoDeps],
    url: str,
) -> dict[str, Any]:
    """Fetch a web page and return its content as markdown."""
```

Rules:
1. Reject non-http/https URLs → `ModelRetry`.
2. Fetch with httpx, timeout: `_FETCH_TIMEOUT = 15` seconds.
3. Convert HTML to markdown via `html2text`.
4. Truncate to `_MAX_FETCH_CHARS = 100_000` (matches gemini-cli).
5. `ModelRetry` on missing key (if needed for proxy), timeout, HTTP errors.

Return shape:
```python
{
    "display": "Content from https://...:\n\n<converted markdown, truncated>",
    "url": str,
    "content_type": str,
    "truncated": bool,
}
```

Note: `web_fetch` does not need the Brave API key. It uses direct HTTP. It's included in MVP because the agent needs to follow URLs from search results — search without fetch is half the "eyes" capability.

---

## Config

One field, following the Slack/Google pattern (key presence = enabled):

**`co_cli/config.py`:**
```python
brave_search_api_key: Optional[str] = Field(default=None)
```

**Env mapping:**
```python
"brave_search_api_key": "BRAVE_SEARCH_API_KEY",
```

**`co_cli/deps.py`:**
```python
brave_search_api_key: str | None = None
```

No `web_search_enabled` flag — if key is absent, `web_search` raises `ModelRetry` (same as Slack without token). `web_fetch` works without any API key.

---

## Error Handling

`ModelRetry` messages that tell the model what to do:

| Condition | Message |
|---|---|
| Missing API key | `Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env.` |
| Empty query | `Query is required for web_search.` |
| Invalid URL | `web_fetch requires an http:// or https:// URL.` |
| Timeout | `Web search timed out. Retry with a shorter query.` |
| HTTP error | `Web search error (HTTP {status}). Retry later.` |
| Fetch too large | Content truncated (not an error — `truncated: true` in return) |

---

## Security

1. Both tools are read-only — no approval prompt.
2. Hardcoded timeout + size limits prevent resource exhaustion.
3. API key redacted from OTel spans/logs.
4. Key absence = disabled (no separate "enabled" flag).
5. Known limitation: no private IP / SSRF protection in MVP. `web_fetch` will follow any http/https URL. Acceptable because co-cli runs locally, not as a public service.

---

## Implementation Plan

1. Add `httpx` and `html2text` to `pyproject.toml`.
2. Create `co_cli/tools/web.py` — both `web_search` and `web_fetch`.
3. Add `brave_search_api_key` to `config.py` (Settings + env mapping).
4. Add `brave_search_api_key` to `deps.py` (CoDeps).
5. Wire in `create_deps()` in `main.py`.
6. Register both tools in `agent.py` as read-only.
7. Add web tools status row to `status.py`.
8. Add tests.

---

## Acceptance Criteria

1. `web_search` returns structured results with URLs for attribution.
2. `web_fetch` returns HTML converted to markdown, truncated at limit.
3. Missing API key → actionable `ModelRetry`.
4. Both tools honor timeout and size limits.
5. No approval flow changes needed.
6. `uv run pytest` passes.

---

## File Checklist

| File | Change |
|---|---|
| `pyproject.toml` | Add `httpx`, `html2text` |
| `co_cli/tools/web.py` | New — `web_search`, `web_fetch` |
| `co_cli/config.py` | Add `brave_search_api_key` + env mapping |
| `co_cli/deps.py` | Add `brave_search_api_key` field |
| `co_cli/main.py` | Wire `brave_search_api_key` in `create_deps()` |
| `co_cli/agent.py` | Register both tools (read-only) |
| `co_cli/status.py` | Add web tools status row |
| `tests/test_web.py` | New — query validation, missing key, result normalization, fetch truncation |
