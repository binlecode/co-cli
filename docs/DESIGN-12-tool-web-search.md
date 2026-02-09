---
title: "12 — Web Tools"
parent: Tools
nav_order: 5
---

# Design: Web Intelligence Tools

## 1. What & How

The web tools give the agent external perception — `web_search` queries the Brave Search API for structured results, and `web_fetch` retrieves a URL and converts it to markdown. Both are read-only by default (`web_policy.search=allow`, `web_policy.fetch=allow`); set each tool to `ask` for approval prompts or `deny` to disable. The Brave API key is injected via `CoDeps`; `web_fetch` needs no API key.

```
Tool Execution:

  web_search(ctx, query, max_results, domains?)
    ├── web_policy.search == "deny" ?  ← policy gate
    ├── api_key = ctx.deps.brave_search_api_key
    ├── prepend site: operators        ← if domains provided
    └── httpx.get(BRAVE_SEARCH_URL, params, headers)
          │
          ▼
      Brave Search API ──▶ structured results ──▶ display + metadata

  web_fetch(ctx, url)
    ├── web_policy.fetch == "deny" ?   ← policy gate
    ├── _is_domain_allowed()          ← domain allow/block check
    ├── is_url_safe(url)              ← SSRF pre-request check
    ├── httpx.get(url, follow_redirects=True)
    ├── is_url_safe(final_url)        ← SSRF post-redirect check
    ├── _is_content_type_allowed()    ← reject binary
    ├── resp.content[:1 MB]           ← byte-level truncate
    ├── decode(encoding, errors=replace)
    ├── html2text (if HTML)           ← markdown conversion
    └── char-level truncate (100k)    ← secondary limit
          │
          ▼
      display + metadata (uses final_url after redirects)
```

## 2. Core Logic

### Tools

**`web_search(query, max_results=5, domains=None) → dict`** — Query Brave Search. Returns `{"display": "1. **Title** — snippet\n   URL\n\n...", "results": [...], "count": N}`. Empty query raises `ModelRetry`. `max_results` capped at `_MAX_RESULTS` (8). Optional `domains` list prepends `site:` operators to scope the search to specific sites. `web_policy.search="deny"` raises `ModelRetry` before any work.

**`web_fetch(url) → dict`** — Fetch a URL and return content as markdown. Returns `{"display": "Content from {final_url}:\n\n...", "url": str, "content_type": str, "truncated": bool}`. The `url` field reflects the actual URL after redirects. Non-http/https URLs raise `ModelRetry`. `web_policy.fetch="deny"` raises `ModelRetry` before any work. The hostname is checked against the domain allow/blocklist before the SSRF check. Before the request, the URL is validated against the SSRF blocklist; after redirects, the final URL is re-checked. Binary content types are rejected. The response body is byte-truncated at 1 MB before decoding, then char-truncated at 100k after markdown conversion.

### Helpers

**`_get_api_key(ctx)`** — Extracts and validates `brave_search_api_key` from context. Raises `ModelRetry` if not configured.

**`_html_to_markdown(html)`** — Converts HTML to markdown via `html2text`. Links preserved, images ignored, no line wrapping (`body_width=0`).

**`_is_content_type_allowed(content_type)`** — Splits on `;`, lowercases the MIME type, and prefix-matches against `_ALLOWED_CONTENT_TYPES`. Empty Content-Type returns `True` (servers often omit it for text).

**`_is_domain_allowed(hostname, allowed, blocked)`** — Domain policy check. If hostname matches any entry in `blocked` (exact or subdomain via `.endswith("." + domain)`) → `False`. If `allowed` is non-empty and hostname doesn't match any entry → `False`. Otherwise → `True`. Hostname is lowercased before comparison.

**`is_url_safe(url)`** *(in `_url_safety.py`)* — SSRF guard. Processing:
1. Parse URL, extract hostname
2. Reject if hostname is in `_BLOCKED_HOSTNAMES` (cloud metadata)
3. `socket.getaddrinfo(hostname)` → resolve to IP list
4. Check each IP against `_BLOCKED_NETWORKS` (loopback, RFC 1918, link-local, carrier-grade NAT, IPv6 private)
5. Fail-closed: DNS failure, missing hostname, or unparseable IP → `False`

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MAX_RESULTS` | 8 | Hard cap on search results |
| `_SEARCH_TIMEOUT` | 12s | Brave API request timeout |
| `_FETCH_TIMEOUT` | 15s | URL fetch request timeout |
| `_MAX_FETCH_CHARS` | 100,000 | Char-level truncation limit for fetched content |
| `_MAX_FETCH_BYTES` | 1,048,576 (1 MB) | Byte-level pre-decode limit for response body |
| `_ALLOWED_CONTENT_TYPES` | `text/*`, `application/json`, `application/xml`, `application/xhtml+xml`, `application/x-yaml`, `application/yaml` | Content-type allowlist for `web_fetch` |

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
| SSRF: private/internal URL | `web_fetch blocked: URL resolves to a private or internal address.` |
| SSRF: redirect to private | `web_fetch blocked: redirect target resolves to a private or internal address.` |
| Unsupported content type | `web_fetch blocked: unsupported content type '{type}'.` |
| Domain not allowed | `web_fetch blocked: domain '{hostname}' not allowed by policy.` |
| Web access denied (fetch) | `web_fetch: web access disabled by policy.` |
| Web access denied (search) | `web_search: web access disabled by policy.` |

Content exceeding `_MAX_FETCH_BYTES` is truncated at the byte level before decoding. Decoded text exceeding `_MAX_FETCH_CHARS` is further truncated with `truncated: true` in the return value (not an error).

### Security

1. Both tools are read-only. Default `web_policy` is `allow` (no prompt), while `ask` routes through deferred approval.
2. Hardcoded timeouts and size limits prevent resource exhaustion.
3. API key accessed only via `ctx.deps` — never imported from settings.
4. Key absence = tool disabled (no separate "enabled" flag).
5. `web_fetch` follows up to 5 redirects (`max_redirects=5`).
6. **SSRF protection** — `is_url_safe()` in `_url_safety.py` resolves the hostname via DNS and checks every returned IP against a blocked-network list (loopback, RFC 1918, link-local/metadata, carrier-grade NAT, IPv6 private). Cloud metadata hostnames (`metadata.google.internal`, `metadata.internal`) are also blocked. Fail-closed: DNS failure or unparseable IP → blocked. Checked both pre-request and post-redirect.
7. **Content-type guard** — `_is_content_type_allowed()` rejects binary responses (images, PDFs, etc.) before processing. Only `text/*` and structured data MIME types (`application/json`, `application/xml`, etc.) are allowed. Empty Content-Type is permitted (servers often omit it for text).
8. **Two-tier body limit** — Response body is first truncated at `_MAX_FETCH_BYTES` (1 MB) before decoding, preventing memory exhaustion from large binary payloads. Decoded text is then truncated at `_MAX_FETCH_CHARS` (100k chars) as a secondary limit.
9. **Domain policy** — `_is_domain_allowed()` checks hostnames against user-configured allow/blocklists before the SSRF check. Blocklist takes precedence over allowlist. Subdomain matching via `.endswith("." + domain)`.
10. **Per-tool web policy** — `web_policy` controls web tool access independently: `search` and `fetch` each support `allow` (default, no approval), `ask` (deferred approval via `requires_approval=True` in agent registration), and `deny` (tools raise `ModelRetry` immediately). Checked at the top of each tool before any work.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `None` | Brave Search API key. Presence enables `web_search`; `web_fetch` works without it |
| `web_fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | If non-empty, only these domains (and subdomains) are permitted for `web_fetch` |
| `web_fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Always blocked, even if allowlist is empty. Comma-separated in env var |
| `web_policy.search` | `CO_CLI_WEB_POLICY_SEARCH` | `"allow"` | Web search decision: `allow` = no approval, `ask` = deferred approval, `deny` = disabled |
| `web_policy.fetch` | `CO_CLI_WEB_POLICY_FETCH` | `"allow"` | Web fetch decision: `allow` = no approval, `ask` = deferred approval, `deny` = disabled |

### Setup

1. Get a Brave Search API key at https://brave.com/search/api/
2. Configure: `settings.json` → `{"brave_search_api_key": "BSA..."}` or set `BRAVE_SEARCH_API_KEY` env var
3. Verify: `uv run co status` — web tools row shows key status

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/web.py` | Both tools: `web_search`, `web_fetch`, helpers, constants |
| `co_cli/tools/_url_safety.py` | SSRF protection: blocked networks/hostnames, `is_url_safe()` |
| `co_cli/config.py` | `brave_search_api_key`, domain lists, `web_policy` settings + env mappings |
| `co_cli/deps.py` | `CoDeps` fields: `brave_search_api_key`, domain lists, `web_policy` |
| `co_cli/main.py` | Wires web settings in `create_deps()`, passes `web_policy` to `get_agent()` |
| `co_cli/agent.py` | Registers web tools with conditional `requires_approval` based on `web_policy.search/fetch` |
| `co_cli/status.py` | Web tools status row |
| `tests/test_web.py` | Validation + functional tests |
