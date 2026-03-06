# Tools — Integrations

External service and knowledge persistence tools: Obsidian vault, Google (Drive, Gmail, Calendar), web search and fetch, and memory. Part of the [Tools index](DESIGN-tools.md).

## Memory Tools

### 1. What & How

Memory tools provide cross-session knowledge persistence. The agent proactively saves preferences, corrections, decisions, and research findings as markdown files with YAML frontmatter. On recall, FTS5 BM25 search (primary) or substring grep (fallback) retrieves matches, with gravity (recency touch) and one-hop link traversal for connected knowledge.

```
save_memory(content, tags?, related?)
  ├── Load all memories from .co-cli/knowledge/
  ├── Fuzzy-dedup check (token_sort_ratio, last N days)
  │     dup found? → update existing (consolidate in-place)
  │     no dup?   → LLM consolidation (extract_facts → resolve)
  │                  → apply plan (ADD/UPDATE/DELETE/NONE)
  ├── write new {id:03d}-{slug}.md (if ADD action)
  └── total > memory_max_count? → cut oldest non-protected

recall_memory(query, max_results=5)
  ├── FTS5 BM25 search (primary) or substring grep (fallback)
  ├── Rank direct matches (FTS: temporal-decay weight, grep: recency)
  ├── Dedup pulled results (pairwise fuzzy similarity)
  ├── One-hop related traversal (up to 5 linked entries)
  └── Gravity: touch pulled entries (refresh updated timestamp)

list_memories(offset=0, limit=20, kind=None)
  └── Paginated inventory sorted by ID, with lifecycle indicators
```

### 2. Core Logic

Memory lifecycle internals (signal detection, dedup, decay, certainty classification, file format) are documented in [DESIGN-knowledge.md](DESIGN-knowledge.md).

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory_max_count` | `CO_CLI_MEMORY_MAX_COUNT` | `200` | Max memories before retention cut triggers |
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Lookback window for duplicate detection |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold (0–100) for dedup |
| `memory_consolidation_top_k` | `CO_MEMORY_CONSOLIDATION_TOP_K` | `5` | Recent memories considered for LLM consolidation |
| `memory_consolidation_timeout_seconds` | `CO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS` | `20` | Per-call timeout for consolidation LLM calls |
| `memory_recall_half_life_days` | `CO_MEMORY_RECALL_HALF_LIFE_DAYS` | `30` | Half-life (days) for temporal decay scoring in FTS5 recall; decay-protected entries are exempt |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/memory_lifecycle.py` | Write entrypoint: dedup → consolidation → write → retention |
| `co_cli/memory_consolidator.py` | LLM-driven fact extraction and contradiction resolution |
| `co_cli/memory_retention.py` | Cut-only retention enforcement |
| `co_cli/tools/memory.py` | `save_memory`, `recall_memory`, `list_memories`, `update_memory`, `append_memory` + shared helpers |
| `co_cli/tools/personality.py` | Private helper: `_load_personality_memories()` for system prompt injection |
| `co_cli/_frontmatter.py` | YAML frontmatter parser and validator for memory files |
| `co_cli/config.py` | Memory settings with env var mappings |
| `co_cli/deps.py` | `CoDeps` memory scalar fields |
| `tests/test_memory_lifecycle.py` | Functional tests: consolidation, dedup, retention, on_failure fallback |
| `tests/test_memory.py` | Functional tests: save, recall, dedup, gravity |

---

## Obsidian Tools

### 1. What & How

The Obsidian tools provide read-only access to a local Obsidian vault. Two agent-registered tools: `list_notes` and `read_note`. Obsidian content is searchable via `search_knowledge(source="obsidian")` when `KnowledgeIndex` is available; the call syncs the vault before indexed search.

```
User: "find notes about project X"
  │
  ▼
Agent → search_knowledge(query="project X", source="obsidian")
          ├── Syncs vault into KnowledgeIndex (hash-based incremental)
          ├── FTS5 MATCH + BM25 ranking (primary path when index available)
          └── If index unavailable: returns no obsidian results (source requires FTS)

User: "list notes tagged #project"
  │
  ▼
Agent → list_notes(tag="#project") → read_note(filename)
```

`search_notes` is an internal adapter (not agent-registered) that performs Obsidian-specific keyword search — either via KnowledgeIndex FTS5 or regex fallback. It is not called by `search_knowledge`; both independently sync the vault via `KnowledgeIndex.sync_dir("obsidian", ...)`.

### 2. Core Logic

**`list_notes(tag=None, offset=0, limit=20) → dict`** — Browse vault structure, optionally filter by tag. Paginated (default 20 per page). Returns `{"display": "...", "count": N, "total": N, "offset": N, "limit": N, "has_more": bool}`.

**`read_note(filename) → str`** — Read full content of a note. Path traversal protection prevents reading outside the vault.

**`search_notes(query, limit=10, folder=None, tag=None) → dict`** (internal, not agent-registered) — Multi-keyword AND search with word boundaries (`\bproject\b`). Dual path: FTS5 via `KnowledgeIndex.sync_dir("obsidian", search_root)` when `ctx.deps.knowledge_index` is set (falls through to regex on exception); regex scan of `*.md` otherwise. Optional `folder` narrows search root; optional `tag` checks YAML frontmatter tags and inline content. Returns `{"display": "...", "count": N, "has_more": bool}`. Empty results return `count: 0` (not `ModelRetry`).

**Error handling (`list_notes`, `read_note`):**

| Scenario | Response |
|----------|----------|
| Vault not configured | `ModelRetry("vault not configured or not found")` |
| Note not found | `ModelRetry("Available notes: [...]. Use exact path")` |
| Path traversal attempt | `ModelRetry("access denied — path is outside the vault")` |

**Security — path traversal protection (`read_note`):**

```
safe_path = (vault / filename).resolve()
if not safe_path.is_relative_to(vault.resolve()):
    raise ModelRetry("Obsidian: access denied — path is outside the vault.")
```

All tools are read-only — no write or delete operations.

### 3. Config

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `None` | Path to Obsidian vault directory |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/obsidian.py` | `search_notes`, `list_notes`, `read_note` |
| `co_cli/deps.py` | `CoDeps` with `obsidian_vault_path` |
| `tests/test_obsidian.py` | Functional tests |

---

## Google Tools

### 1. What & How

Three Google Cloud services: Drive (search + read files), Gmail (list, search, draft emails), Calendar (list + search events). All use `RunContext[CoDeps]` with lazy credential resolution via `get_cached_google_creds(ctx.deps)` — cached on `CoDeps` for the session lifecycle.

```
tool(ctx: RunContext[CoDeps], ...)
  ├── creds = get_cached_google_creds(ctx.deps)
  │       # resolved once, cached on CoDeps instance
  ├── if not creds: return terminal_error("Not configured")
  ├── service = build("drive"/"gmail"/"calendar", ...)
  └── service.files().list(...).execute()
        │
        ▼
    Google Cloud APIs
    ├── Drive API v3    (drive.readonly)
    ├── Gmail API v1    (gmail.modify)
    └── Calendar API v3 (calendar.readonly)
```

### 2. Core Logic

**Authentication (`_google_auth.py`):**

```
ensure_google_credentials(credentials_path, scopes):
  1. Explicit credentials_path exists? → Use it
  2. ~/.config/co-cli/google_token.json exists? → Use it
  3. ~/.config/gcloud/application_default_credentials.json? → Copy + use
  4. gcloud installed? → Run gcloud auth application-default login
  5. None of the above → Return None
```

`get_cached_google_creds()` wraps `ensure_google_credentials()`, caches the result on `deps.google_creds` — one resolution per session.

**Drive tools:**

- **`search_drive_files(query, page=1) → dict`** — Server-managed pagination; only `page: int` exposed to the LLM. Returns `display`, `page`, `has_more`.
- **`read_drive_file(file_id) → str`** — Auto-detects Google Docs (export as `text/plain`) vs. regular files (download raw). Returns text content on success; failures are classified by `_handle_drive_error` (`terminal_error` for auth 401, `ModelRetry` for most other API failures).

**Gmail tools:**

- **`list_emails(max_results=5) → dict`** — Recent inbox with From, Subject, Date, snippet, Gmail link.
- **`search_emails(query, max_results=5) → dict`** — Native Gmail query syntax (`from:alice`, `is:unread`, `newer_than:2d`).
- **`create_email_draft(to, subject, body) → str`** — Creates Gmail draft. Registered with `requires_approval=True`.

**Calendar tools:**

- **`list_calendar_events(days_back=0, days_ahead=1, max_results=25) → dict`** — Lists events with calendar/Meet links and attendees.
- **`search_calendar_events(query, days_back=0, days_ahead=30, max_results=25) → dict`** — Keyword search within a date range.

**Error handling:**

| Scenario | Strategy | Model sees |
|----------|----------|-----------|
| No credentials | `terminal_error()` | Error dict → routes to alternative |
| Missing credentials / auth 401 | `terminal_error()` | Error dict → routes to alternative |
| 403/404/429/5xx API errors | `ModelRetry` | Error fed back for guided retry |
| Network/quota/transient | `ModelRetry` | Error fed back for self-correction |
| Empty results | Normal return | `{"count": 0}` |

Google tools use `terminal_error()` for missing credentials and explicit auth failures (401). Most service/API errors (403/404/429/5xx) are raised as `ModelRetry` with service-specific guidance.

Shared error helpers in `_errors.py`:
- `terminal_error(message)` → `{"display": message, "error": True}`
- `http_status_code(exception)` → extracts HTTP status int from Google API exceptions (checks `status_code`, `resp.status`)

Each Google tool module has its own `_handle_*_error(e)` function that calls `http_status_code()` and applies module-specific policy: `terminal_error(...)` for auth/missing-credential paths, `ModelRetry` for most API and transient failures.

**Security:** Tools never import `settings` — credentials via `ctx.deps` only. Drive and Calendar are read-only. Gmail uses `gmail.modify` scope (minimum needed for draft creation).

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `None` | OAuth token path for Drive, Gmail, Calendar |

**OAuth scopes:**

| Service | Scope |
|---------|-------|
| Drive | `drive.readonly` |
| Gmail | `gmail.modify` (required for draft creation) |
| Calendar | `calendar.readonly` |

**Setup:**

1. `gcloud auth application-default login --scopes='https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar.readonly'`
2. `gcloud services enable drive.googleapis.com gmail.googleapis.com calendar-json.googleapis.com`
3. `uv run co status` — verify Google shows as configured

When a Google tool is first called in chat, auth bootstrap can run `gcloud auth application-default login` automatically if no token exists and `gcloud` is installed.

**Troubleshooting:**

| Symptom | Fix |
|---------|-----|
| "API is not enabled" | `gcloud services enable <api>.googleapis.com` |
| "Not configured" | Run gcloud auth, or `co chat` (auto-setup) |
| "Insufficient scopes" | Delete `~/.config/co-cli/google_token.json`, re-auth with all scopes |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/_google_auth.py` | `ensure_google_credentials()`, `get_google_credentials()`, `get_cached_google_creds()` |
| `co_cli/tools/google_drive.py` | `search_drive_files`, `read_drive_file` |
| `co_cli/tools/google_gmail.py` | `list_emails`, `search_emails`, `create_email_draft` |
| `co_cli/tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` |
| `co_cli/tools/_errors.py` | Shared helpers: `terminal_error()`, `http_status_code()` |
| `co_cli/deps.py` | `CoDeps` with `google_credentials_path`, `google_creds` fields |
| `tests/test_google_cloud.py` | Functional tests |

---

## Web Tools

### 1. What & How

Two tools: `web_search` queries Brave Search for structured results; `web_fetch` retrieves a URL and converts it to markdown. Both read-only. Default policy is `allow` (no approval prompt); set `ask` for deferred approval or `deny` to disable per tool. SSRF protection is applied before and after redirects.

```
web_search(ctx, query, max_results, domains?)
  ├── web_policy.search == "deny" → ModelRetry
  ├── api_key = ctx.deps.brave_search_api_key
  ├── prepend site: operators (if domains provided)
  └── httpx.get(BRAVE_SEARCH_URL) → display + metadata

web_fetch(ctx, url)
  ├── web_policy.fetch == "deny" → ModelRetry
  ├── _is_domain_allowed()          domain allow/blocklist
  ├── is_url_safe(url)              SSRF pre-request check
  ├── httpx.get(url, follow_redirects=True)
  │     └── 403 + cf-mitigated: challenge → retry with honest headers
  ├── is_url_safe(final_url)        SSRF post-redirect check
  ├── _is_content_type_allowed()    reject binary
  ├── resp.content[:1 MB]           byte-level truncate
  ├── decode + html2text (if HTML)  markdown conversion
  └── char-level truncate (100k)
```

### 2. Core Logic

**`web_search(query, max_results=5, domains=None) → dict`** — Queries Brave Search API. Returns `{"display": "...", "results": [...], "count": N}`. `max_results` capped at 8. Optional `domains` list prepends `site:` operators. Empty query → `ModelRetry`.

**`web_fetch(url) → dict`** — Fetches and markdown-converts a URL. Returns `{"display": "...", "url": final_url, "content_type": str, "truncated": bool}`. `url` field reflects the actual URL after redirects. Non-http/https → `ModelRetry`. Two-tier body limit: 1 MB byte truncation before decoding, then 100k char truncation after conversion.

**Key helpers:**

| Helper | Purpose |
|--------|---------|
| `is_url_safe(url)` in `_url_safety.py` | SSRF guard: DNS-resolves hostname, checks each IP against blocked networks (loopback, RFC 1918, link-local, carrier-grade NAT, IPv6 private). Fail-closed. |
| `_is_domain_allowed(hostname, allowed, blocked)` | Blocklist takes precedence over allowlist. Subdomain matching via `.endswith("." + domain)`. |
| `_is_content_type_allowed(content_type)` | Allows `text/*`, `application/json`, `application/xml`, `application/xhtml+xml`, `application/x-yaml`, `application/yaml`. Rejects binary. Empty Content-Type permitted. |
| `_http_get_with_retries(...)` | Shared retry loop: bounded exponential backoff with jitter, Retry-After aware. Classifies HTTP errors — terminal (4xx) return immediately, retryable (429, 5xx, timeout) retry up to budget. |
| `_is_cloudflare_challenge(resp)` | Detects HTTP 403 + `cf-mitigated: challenge`. Triggers one-shot retry with minimal honest headers (doesn't consume retry budget). |
| `_html_to_markdown(html)` | `html2text`: links preserved, images ignored, no line wrapping. |

**Error handling:**

`ModelRetry` for pre-request validation:

| Condition | Message |
|-----------|---------|
| Missing API key | `Web search not configured. Set BRAVE_SEARCH_API_KEY in settings or env.` |
| Empty query | `Query is required for web_search.` |
| Invalid URL scheme | `web_fetch requires an http:// or https:// URL.` |
| SSRF blocked (pre or post-redirect) | `web_fetch blocked: URL resolves to a private or internal address.` |
| Domain not allowed | `web_fetch blocked: domain '{hostname}' not allowed by policy.` |
| Policy deny | `web_fetch: web access disabled by policy.` |

`terminal_error` for HTTP failures:

| Condition | Pattern |
|-----------|---------|
| 401 | `blocked (HTTP 401): authentication required` |
| 403 | `blocked (HTTP 403): origin policy denied access` |
| 404 | `not found (HTTP 404)` |
| 429 / 5xx / timeout / network | `rate limited / transient / timed out / network error. Retries exhausted (N).` |
| Binary content type | `web_fetch blocked: unsupported content type '{type}'` |

**Security:**

1. Both tools are read-only. Default policy is `allow`; `ask` routes through deferred approval.
2. Hardcoded timeouts (12s search, 15s fetch) and size limits prevent resource exhaustion.
3. API key accessed only via `ctx.deps` — never imported from settings.
4. **SSRF:** `is_url_safe()` resolves DNS and checks every IP. Cloud metadata hostnames (`metadata.google.internal`, `metadata.internal`) also blocked. Checked pre-request and post-redirect.
5. **Content-type guard:** binary responses rejected before processing.
6. **Two-tier body limit:** 1 MB before decode, 100k chars after conversion.
7. **Domain policy:** user-configured allow/blocklists, subdomain-aware.
8. **Per-tool policy:** `search` and `fetch` independently support `allow`, `ask`, `deny`.
9. **Cloudflare recovery:** one-shot retry with honest-only headers on TLS fingerprint mismatch (403 + `cf-mitigated: challenge`).

**Constants:**

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MAX_RESULTS` | 8 | Hard cap on search results |
| `_SEARCH_TIMEOUT` | 12s | Brave API timeout |
| `_FETCH_TIMEOUT` | 15s | URL fetch timeout |
| `_MAX_FETCH_CHARS` | 100,000 | Char-level truncation |
| `_MAX_FETCH_BYTES` | 1,048,576 | Byte-level pre-decode limit |

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `None` | Brave Search API key. Enables `web_search`; `web_fetch` works without it |
| `web_fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | If non-empty, only these domains (+ subdomains) permitted for `web_fetch` |
| `web_fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Always blocked. Comma-separated in env var |
| `web_policy.search` | `CO_CLI_WEB_POLICY_SEARCH` | `"allow"` | `allow` = no approval, `ask` = deferred, `deny` = disabled |
| `web_policy.fetch` | `CO_CLI_WEB_POLICY_FETCH` | `"allow"` | `allow` = no approval, `ask` = deferred, `deny` = disabled |

**Setup:** Get a Brave Search API key, then set `brave_search_api_key` in `settings.json` or via `BRAVE_SEARCH_API_KEY` env var. Verify with `uv run co status`.

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/web.py` | `web_search`, `web_fetch`, helpers, constants |
| `co_cli/tools/_http_retry.py` | Shared HTTP retry: error classification, backoff, Retry-After parsing |
| `co_cli/tools/_url_safety.py` | SSRF protection: blocked networks/hostnames, `is_url_safe()` |
| `co_cli/config.py` | `brave_search_api_key`, domain lists, `web_policy` settings |
| `co_cli/deps.py` | `CoDeps` fields: `brave_search_api_key`, domain lists, `web_policy` |
| `co_cli/main.py` | Wires web settings in `create_deps()`, passes `web_policy` to `get_agent()` |
| `co_cli/agent.py` | Registers web tools with conditional `requires_approval` based on `web_policy` |
| `co_cli/status.py` | Web tools status row |
| `tests/test_web.py` | Functional tests: search, fetch, SSRF, policy gates, error handling |
