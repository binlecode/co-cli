---
title: Tools
nav_order: 4
has_children: true
---

# Tools

Shell, memory, Obsidian vault, Google services, and web intelligence — agent tool implementations. See [DESIGN-15-mcp-client.md](DESIGN-15-mcp-client.md) for external MCP tool servers.

## Common Conventions

**Registration:** All native tools use `agent.tool()` with `RunContext[CoDeps]`. Zero `tool_plain()` remaining. Side-effectful tools are registered with `requires_approval=True` — the chat loop handles the `[y/n/a]` prompt via `DeferredToolRequests`; tools contain only business logic.

**Return shape:** Tools returning data for the user return `dict[str, Any]` with a `display` field (pre-formatted string, shown verbatim) and metadata fields (`count`, `has_more`, etc.). The LLM is instructed to show `display` directly. Error results use `{"display": "...", "error": True}`.

**Error classification:** Two strategies:
- `ModelRetry(msg)` — pre-request validation failures or transient errors. pydantic-ai retries the tool call. Use for: wrong parameters, missing query, network blip.
- `terminal_error(msg)` — non-retryable config/auth failures (missing credentials, API not enabled). Returns `{"display": ..., "error": True}` so the model stops looping and routes to an alternative.

**Approval table:**

| Tool | Approval | Rationale |
|------|----------|-----------|
| `run_shell_command` | Yes | Arbitrary code execution. Safe-prefix commands auto-approved. |
| `create_email_draft` | Yes | Creates Gmail draft on user's behalf |
| `save_memory` | Yes | Writes to `.co-cli/knowledge/memories/` |
| `todo_write`, `todo_read` | No | In-memory session state only — no external side effects |
| All other native tools | No | Read-only operations |

---

## Memory Tools

### 1. What & How

Memory tools provide cross-session knowledge persistence. The agent proactively saves preferences, corrections, decisions, and research findings as markdown files with YAML frontmatter. On recall, substring grep + tag filtering retrieves matches, with gravity (recency touch) and one-hop link traversal for connected knowledge.

```
save_memory(content, tags?, related?)
  ├── Load all memories from .co-cli/knowledge/memories/
  ├── Fuzzy-dedup check (token_sort_ratio, last N days)
  │     dup found? → update existing (consolidate)
  │     no dup?   → write new {id:03d}-{slug}.md
  └── total > memory_max_count? → trigger decay strategy

recall_memory(query, max_results=5)
  ├── Substring match on content + tags (case-insensitive)
  ├── Sort by recency (updated or created)
  ├── Dedup pulled results (pairwise fuzzy similarity)
  ├── One-hop related traversal (up to 5 linked entries)
  └── Gravity: touch pulled entries (refresh updated timestamp)

list_memories(offset=0, limit=20)
  └── Paginated inventory sorted by ID, with lifecycle indicators
```

### 2. Core Logic

**`save_memory(content, tags, related) → dict`** — Proactive save. Checks for duplicates in recent memories (within `memory_dedup_window_days`) using `rapidfuzz.fuzz.token_sort_ratio`. On similarity ≥ `memory_dedup_threshold` (default 85%), updates the existing entry (merges tags, overwrites content). Otherwise creates a new `{id:03d}-{slug}.md` file. After save, if total count exceeds `memory_max_count`, triggers decay. Returns `action: "saved"` or `action: "consolidated"`.

**`recall_memory(query, max_results) → dict`** — Case-insensitive substring search on content and tags. Pulls up to `max_results` direct matches sorted by recency. Deduplicates pulled results via pairwise fuzzy similarity (merges near-duplicates on the fly, deletes the older file). Traverses one-hop `related` links, adding up to 5 connected entries. Touches all pulled entries (updates `updated` timestamp = gravity: frequently-recalled memories stay accessible).

**`list_memories(offset, limit) → dict`** — Full inventory, sorted by ID. Paginated with `has_more`. Shows lifecycle indicators: `[category]` tag, decay-protected flag.

**Decay:** Triggered inside `save_memory` when `total > memory_max_count`. Selects the oldest unprotected entries (excluding `decay_protected: true`). Two strategies:
- `summarize` — concatenates selected entries into a new summary memory, deletes originals. Creates 1 file, net reduction = decay_count - 1.
- `cut` — deletes originals permanently.

`decay_count` = max(`total * memory_decay_percentage`, enough to get below the limit).

**File format:**
```
---
id: 42
created: 2026-01-15T10:00:00+00:00
updated: 2026-02-01T14:30:00+00:00
tags: [preference, python]
source: detected        # "detected" if signal tags present, "user-told" otherwise
auto_category: preference
decay_protected: false
related: ["003-user-prefers-pytest"]
---

User prefers pytest over unittest for Python testing.
```

**Signal tags for proactive save:** `preference`, `correction`, `decision`, `context`, `pattern`.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `memory_max_count` | `CO_CLI_MEMORY_MAX_COUNT` | `200` | Max memories before decay triggers |
| `memory_dedup_window_days` | `CO_CLI_MEMORY_DEDUP_WINDOW_DAYS` | `7` | Lookback window for duplicate detection |
| `memory_dedup_threshold` | `CO_CLI_MEMORY_DEDUP_THRESHOLD` | `85` | Fuzzy similarity threshold (0–100) for dedup |
| `memory_decay_strategy` | `CO_CLI_MEMORY_DECAY_STRATEGY` | `summarize` | `summarize` (consolidate) or `cut` (delete) |
| `memory_decay_percentage` | `CO_CLI_MEMORY_DECAY_PERCENTAGE` | `0.2` | Fraction of total to decay when limit exceeded |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/memory.py` | `save_memory`, `recall_memory`, `list_memories` + internal helpers |
| `co_cli/tools/personality.py` | Private helper: `_load_personality_memories()` for system prompt injection |
| `co_cli/_frontmatter.py` | YAML frontmatter parser and validator for memory files |
| `co_cli/config.py` | Memory settings with env var mappings |
| `co_cli/deps.py` | `CoDeps` memory scalar fields |
| `tests/test_memory.py` | Functional tests: save, recall, dedup, decay |

---

## Todo Tools

### 1. What & How

`todo_write` / `todo_read` give the model a session-scoped task list for multi-step directives. State lives in `CoDeps.session_todos` (in-memory, not persisted). The model replaces the full list to update status, then reads it back to verify completeness before ending a turn. Rule 05 mandates this check — the model must not respond as done while any `pending` or `in_progress` items remain.

```
todo_write(todos)
  ├── Validate each item: content (str), status, priority
  ├── status ∈ {pending, in_progress, completed, cancelled}
  ├── priority ∈ {high, medium, low} (default: medium)
  ├── Validation error? → return error dict, do not write
  └── Replace ctx.deps.session_todos → return counts

todo_read()
  └── Return current session_todos
        └── pending > 0 or in_progress > 0?
              → signal "work is not complete" in display
```

### 2. Core Logic

**`todo_write(todos) → dict`** — Replaces the entire list (idempotent; the model rewrites all items to update any one). Validates `status` and `priority` enums before writing — returns an error dict on invalid input without touching stored state. Returns `pending` and `in_progress` counts so the model knows remaining work without a follow-up read.

**`todo_read() → dict`** — Returns current list. When `pending > 0` or `in_progress > 0`, the `display` field contains an explicit "work is not complete" message so the model knows to continue rather than close the turn.

**Completeness enforcement (Rule 05):** `prompts/rules/05_workflow.md` contains a `## Completeness` section directing the model to call `todo_read` and confirm no `pending`/`in_progress` items remain before ending a turn. No orchestration-layer scanning — task state lives in the model's tool calls.

**Why full-list replacement:** Follows the OpenCode/Claude Code TodoWrite pattern. Partial updates (patch-by-id) require the model to track IDs across turns and are error-prone. Rewriting the full list is simpler, stateless from the model's perspective, and equally expressive.

### 3. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/todo.py` | `todo_write`, `todo_read` |
| `co_cli/deps.py` | `CoDeps.session_todos` — session list field, default empty |
| `co_cli/agent.py` | Registration: both tools, `requires_approval=False` |
| `co_cli/prompts/rules/05_workflow.md` | `## Completeness` directive |

---

## Shell Tool

### 1. What & How

The shell tool executes host subprocess commands with approval as the explicit security boundary. No Docker, no container — approval-first replaces OS-level isolation. Read-only commands matching a configurable safe-prefix list are auto-approved; everything else requires user consent via `[y/n/a]`.

```
User: "list files"
       │
       ▼
┌─────────────────┐
│   Agent.run()   │
│   deps=CoDeps   │
└────────┬────────┘
         │ tool call: run_shell_command(cmd="ls -la")
         ▼
┌──────────────────────────────────────────────────┐
│                Approval Gate                      │
│  safe-prefix match? ──yes──▶ auto-approve        │
│                      ──no──▶ [y/n/a] prompt      │
└────────┬─────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│             Subprocess Execution                  │
│  sh -c '{cmd}'                                   │
│  env: restricted_env() (allowlist, PAGER=cat)    │
│  cwd: host working directory                     │
│  timeout: asyncio.wait_for + kill_process_tree   │
└──────────────────────────────────────────────────┘
```

### 2. Core Logic

**`run_shell_command(cmd, timeout=120) → str | dict[str, Any]`** — Delegates to `ShellBackend.run_command()`. Returns a string on success, or a `terminal_error` dict on permission denied. Raises `ModelRetry` on most other errors so the LLM can self-correct. Confirmation is NOT a tool responsibility — handled by the orchestration layer.

```
run_shell_command(ctx, cmd, timeout=120):
    effective = min(timeout, ctx.deps.shell_max_timeout)
    try:
        return ctx.deps.shell.run_command(cmd, effective)
    on timeout         → ModelRetry("timed out, use shorter command or increase timeout")
    on permission denied → terminal_error dict (no retry — model sees error, picks different tool)
    on other RuntimeError → ModelRetry("command failed, try different approach")
    on any other exception → ModelRetry("unexpected error, try different approach")
```

**Safe-prefix auto-approval:** Runs in `_handle_approvals()` inside `_orchestrate.py`. Commands matching the configurable safe list skip the `[y/n/a]` prompt.

```
_is_safe_command(cmd, safe_commands):
    reject if cmd contains shell chaining operators: ; & | > < ` $( \n
    match cmd against safe_commands (longest prefix first)
    return True if prefix matches, else False
```

Multi-word prefixes (e.g. `git status`) are matched before single-word ones to prevent `git` from matching `git push`.

**Default safe commands:** `ls`, `tree`, `find`, `fd`, `cat`, `head`, `tail`, `grep`, `rg`, `ag`, `wc`, `sort`, `uniq`, `cut`, `tr`, `jq`, `echo`, `printf`, `pwd`, `whoami`, `hostname`, `uname`, `date`, `env`, `which`, `file`, `stat`, `id`, `du`, `df`, `git status`, `git diff`, `git log`, `git show`, `git branch`, `git tag`, `git blame`.

**Shell backend — `ShellBackend.run_command(cmd, timeout)`:**

```
spawn sh -c cmd
    cwd = workspace_dir
    env = restricted_env()
    start_new_session = True  (enables process group kill)
    stdout + stderr merged

wait with asyncio.wait_for(timeout)
on timeout → kill_process_tree(proc), read partial output (1s grace), raise RuntimeError
on non-zero exit → raise RuntimeError with exit code + decoded output
return decoded stdout
```

**Environment sanitization — `restricted_env()`:** Allowlist-only (not blocklist) to prevent pager/editor hijacking.

- **Allowed:** `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_ALL`, `TERM`, `SHELL`, `TMPDIR`, `XDG_RUNTIME_DIR`
- **Forced:** `PYTHONUNBUFFERED=1`, `PAGER=cat`, `GIT_PAGER=cat`
- **Stripped:** `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, `MANPAGER`, `EDITOR`, everything else

**Process cleanup — `kill_process_tree(proc)`:**

```
if already exited → return
SIGTERM to process group (os.killpg)
wait 200ms
if still alive → SIGKILL to process group
```

`start_new_session=True` on the subprocess enables killing the entire process tree.

**Timeout control:**

| Layer | Controls | Default |
|-------|----------|---------|
| Tool parameter (`timeout`) | LLM chooses per call | 120s |
| Hard ceiling (`shell_max_timeout`) | Settings cap, LLM cannot exceed | 600s |

**Error scenarios:**

| Scenario | Detection | Handling |
|----------|-----------|----------|
| Command fails | Non-zero exit code | `ModelRetry` |
| Command timeout | `asyncio.TimeoutError` | `kill_process_tree`, partial output → `ModelRetry` |
| Permission denied | `"permission denied"` in error | `terminal_error()` dict (no retry) |
| Unexpected error | Catch-all `Exception` | `ModelRetry` |

**Security layers:**

```
Layer 1: Approval gate
  Safe-prefix → auto-approve silently
  Chaining operators → force approval
  Everything else → [y/n/a] prompt

Layer 2: Environment sanitization
  Allowlist-only env vars
  PAGER + GIT_PAGER forced to cat
  Blocks LD_PRELOAD, DYLD_INSERT_LIBRARIES, etc.

Layer 3: Process isolation
  start_new_session=True (own process group)
  kill_process_tree on timeout (SIGTERM → SIGKILL)

Layer 4: Timeout enforcement
  LLM-controlled timeout capped by shell_max_timeout
  asyncio.wait_for + kill_process_tree as safety net
```

The subprocess runs as the user with read-write access to local files. This is a deliberate tradeoff — co is a single-user CLI companion, not a CI pipeline. Approval is the security boundary.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | `["ls", "cat", ...]` | Auto-approved prefixes (comma-separated in env) |
| `shell_max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard ceiling for per-command timeout (seconds) |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/shell.py` | Tool function — delegates to shell backend, `ModelRetry` on error |
| `co_cli/shell_backend.py` | `ShellBackend` — subprocess execution with `restricted_env()` |
| `co_cli/_approval.py` | `_is_safe_command()` — safe-prefix classification for auto-approval |
| `co_cli/_shell_env.py` | `restricted_env()` and `kill_process_tree()` |
| `co_cli/deps.py` | `CoDeps` — holds `shell` instance, `shell_safe_commands`, `shell_max_timeout` |
| `co_cli/config.py` | Shell settings with env var mappings |
| `co_cli/_orchestrate.py` | `_handle_approvals()` — safe-command check + `[y/n/a]` prompt loop |
| `co_cli/agent.py` | Tool registration (`requires_approval=True`) + shell system prompt injection |
| `tests/test_shell.py` | Functional tests — subprocess execution, env sanitization, timeout, cwd |
| `tests/test_commands.py` | Safe-command classification tests — prefix matching, chaining rejection |

---

## Obsidian Tools

### 1. What & How

The Obsidian tools provide read-only access to a local Obsidian vault for knowledge retrieval (RAG). Three tools: search, list, and read.

```
User: "find notes about project X"
  │
  ▼
Agent → search_notes(query="project X")
  ├── Get vault path from ctx.deps.obsidian_vault_path
  ├── Glob all *.md files (optionally scoped by folder)
  ├── Regex search with word boundaries (AND logic)
  └── Return matches with snippets
```

### 2. Core Logic

**`search_notes(query, limit=10, folder=None, tag=None) → dict`** — Multi-keyword AND search with word boundaries (`\bproject\b`). Optional `folder` narrows the search root; optional `tag` checks YAML frontmatter tags and inline content tags. Returns `{"display": "...", "count": N, "has_more": bool}`. Empty results return `count: 0` (not `ModelRetry`).

**`list_notes(tag=None, offset=0, limit=20) → dict`** — Browse vault structure, optionally filter by tag. Paginated (default 20 per page). Returns `{"display": "...", "count": N, "total": N, "offset": N, "limit": N, "has_more": bool}`.

**`read_note(filename) → str`** — Read full content of a note. Path traversal protection prevents reading outside the vault.

**Error handling:**

| Scenario | Response |
|----------|----------|
| Vault not configured | `ModelRetry("Ask user to set obsidian_vault_path")` |
| Empty query | `ModelRetry("Provide keywords to search")` |
| No search results | `{"count": 0}` |
| Note not found | `ModelRetry("Available notes: [...]. Use exact path")` |
| Path traversal attempt | `ModelRetry("Access denied: path is outside the vault")` |

**Security — path traversal protection:**

```
safe_path = (vault / filename).resolve()
if not safe_path.is_relative_to(vault.resolve()):
    raise ModelRetry("Access denied: path is outside the vault.")
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
- **`read_drive_file(file_id) → str`** — Auto-detects Google Docs (export as `text/plain`) vs. regular files (download raw). Returns text content or `terminal_error` dict on failure.

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
| API not enabled | `terminal_error()` | Error dict → routes to alternative |
| Network/quota error | `ModelRetry` | Error fed back for self-correction |
| Empty results | Normal return | `{"count": 0}` |

Terminal errors (missing credentials, API not enabled) use `terminal_error()` — not `ModelRetry` — because no parameter change can fix them. The model stops looping and can inform the user or try a different approach.

Shared error helpers in `_errors.py`:
- `terminal_error(message)` → `{"display": message, "error": True}`
- `http_status_code(exception)` → extracts HTTP status int from Google API exceptions (checks `status_code`, `resp.status`)

Each Google tool module has its own `_handle_*_error(e)` function that calls `http_status_code()` to classify the error and returns either `terminal_error(...)` (for 4xx config/auth errors) or raises `ModelRetry` (for transient failures).

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

`co chat` runs the gcloud auth command automatically if no token exists and `gcloud` is installed.

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
| `co_cli/tools/_errors.py` | Shared helpers: `terminal_error()`, `classify_google_error()`, `handle_tool_error()` |
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
