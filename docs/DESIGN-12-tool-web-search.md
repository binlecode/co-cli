---
title: "12 — Web Tools"
parent: Tools
nav_order: 5
---

# Design: Web Intelligence Tools

**Synced:** v0.3.4

## 1. What & How

The web tools give the agent external perception — `web_search` queries the Brave Search API for structured results, and `web_fetch` retrieves a URL and converts it to markdown. Both are read-only (no approval required). The Brave API key is injected via `CoDeps`; `web_fetch` needs no API key.

```
Tool Execution:

  web_search(ctx, query, max_results)
    ├── api_key = ctx.deps.brave_search_api_key
    └── httpx.get(BRAVE_SEARCH_URL, params, headers)
          │
          ▼
      Brave Search API ──▶ structured results ──▶ display + metadata

  web_fetch(ctx, url)
    └── httpx.get(url, follow_redirects=True)
          │
          ▼
      HTML ──▶ html2text ──▶ markdown ──▶ truncate ──▶ display + metadata
```

## 2. Core Logic

### Tools

**`web_search(query, max_results=5) → dict`** — Query Brave Search. Returns `{"display": "1. **Title** — snippet\n   URL\n\n...", "results": [...], "count": N}`. Empty query raises `ModelRetry`. `max_results` capped at `_MAX_RESULTS` (8).

**`web_fetch(url) → dict`** — Fetch a URL and return content as markdown. Returns `{"display": "Content from URL:\n\n...", "url": str, "content_type": str, "truncated": bool}`. Non-http/https URLs raise `ModelRetry`.

### Shared Helpers

**`_get_api_key(ctx)`** — Extracts and validates `brave_search_api_key` from context. Raises `ModelRetry` if not configured.

**`_html_to_markdown(html)`** — Converts HTML to markdown via `html2text`. Links preserved, images ignored, no line wrapping (`body_width=0`).

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MAX_RESULTS` | 8 | Hard cap on search results |
| `_SEARCH_TIMEOUT` | 12s | Brave API request timeout |
| `_FETCH_TIMEOUT` | 15s | URL fetch request timeout |
| `_MAX_FETCH_CHARS` | 100,000 | Truncation limit for fetched content |

### Error Handling

All errors use `ModelRetry` with actionable messages:

| Condition | Message |
|-----------|---------|
| Missing API key | `Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env.` |
| Empty query | `Query is required for web_search.` |
| Invalid URL scheme | `web_fetch requires an http:// or https:// URL.` |
| Search timeout | `Web search timed out. Retry with a shorter query.` |
| Search HTTP error | `Web search error (HTTP {status}). Retry later.` |
| Fetch timeout | `web_fetch timed out fetching {url}. Try a different URL.` |
| Fetch HTTP error | `web_fetch error (HTTP {status}) for {url}.` |

Content exceeding `_MAX_FETCH_CHARS` is silently truncated with `truncated: true` in the return value (not an error).

### Security

1. Both tools are read-only — no approval prompt needed.
2. Hardcoded timeouts and size limits prevent resource exhaustion.
3. API key accessed only via `ctx.deps` — never imported from settings.
4. Key absence = tool disabled (no separate "enabled" flag).
5. `web_fetch` follows up to 5 redirects (`max_redirects=5`).
6. **Known limitation:** No private IP / SSRF protection. `web_fetch` will follow any http/https URL including internal/private addresses. Acceptable because co-cli runs locally as a single-user tool, not as a public service. Post-MVP hardening should add private IP filtering.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `None` | Brave Search API key. Presence enables `web_search`; `web_fetch` works without it |

### Setup

1. Get a Brave Search API key at https://brave.com/search/api/
2. Configure: `settings.json` → `{"brave_search_api_key": "BSA..."}` or set `BRAVE_SEARCH_API_KEY` env var
3. Verify: `uv run co status` — web tools row shows key status

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/web.py` | Both tools: `web_search`, `web_fetch`, helpers, constants |
| `co_cli/config.py` | `brave_search_api_key` setting + env mapping |
| `co_cli/deps.py` | `CoDeps.brave_search_api_key` field |
| `co_cli/main.py` | Wires `brave_search_api_key` in `create_deps()` |
| `co_cli/agent.py` | Registers both tools as read-only |
| `co_cli/status.py` | Web tools status row |
| `tests/test_web.py` | Validation + functional tests |
