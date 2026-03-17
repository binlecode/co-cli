# Co CLI — Tools

> For system overview and approval boundary: [DESIGN-system.md](DESIGN-system.md). For shell inline policy and approval decision chain: see §Approval below and [DESIGN-core-loop.md](DESIGN-core-loop.md).

## 1. What & How

Native tools are Python functions registered on the pydantic-ai `Agent` via `_register()` in `agent.py`. Every tool receives a `RunContext[CoDeps]` as its first argument and returns `dict[str, Any]` with a `display` field (pre-formatted string shown to the user) plus metadata fields.

Tools are grouped into ten families:

```
tools/
  files.py          — workspace filesystem (list, read, find, write, edit)
  shell.py          — approval-gated subprocess execution
  memory.py         — memory write/recall/edit
  articles.py       — knowledge article save and search
  obsidian.py       — Obsidian vault notes search and read
  google_drive.py   — Google Drive search and read
  google_gmail.py   — Gmail list, search, draft
  google_calendar.py — Calendar list and search
  web.py            — Brave Search + direct HTTP fetch
  task_control.py   — background task lifecycle
  todo.py           — session-scoped task list
  capabilities.py   — integration health introspection
  delegation.py     — sub-agent delegation
  _delegation_agents.py — CoderResult, ResearchResult, AnalysisResult + agent factories
```

## 2. Core Logic

### Registration

All native tools are registered via `_register(fn, requires_approval)` in `agent.py:get_agent()`. This wraps each function with `agent.tool()` and records the `(name, requires_approval)` pair in `tool_registry`. Sub-agent tools are registered directly via `agent.tool()` inside each sub-agent factory.

Conditional registration: `delegate_*` tools are registered only when the matching role model chain is configured. If `reasoning` only is set, all three delegation tools may be unavailable.

### Approval Classes

| Class | Condition | Examples |
|-------|-----------|---------|
| Always deferred | `requires_approval=True`, unconditional | `write_file`, `edit_file`, `save_memory`, `save_article`, `create_email_draft`, `start_background_task`, `update_memory`, `append_memory` |
| Shell inline policy | `requires_approval` depends on `evaluate_shell_command()` | `run_shell_command` |
| Always auto | `requires_approval=False` | `list_directory`, `read_file`, `find_in_files`, `check_capabilities`, `delegate_*`, task status/list/cancel, `todo_write`, `todo_read`, all read-only personal-data tools |
| Web policy | Depends on `web_policy.search` / `web_policy.fetch` setting | `web_search`, `web_fetch` |

### Return Shape

Every user-facing tool returns `dict[str, Any]` with:
- `display` — pre-formatted string, shown directly to the user
- metadata fields (`count`, `path`, `task_id`, `article_id`, etc.)

Tools returning raw strings (e.g. `read_note`) are the exception — legacy; avoid this pattern.

### Error Classes

| Class | When to use |
|-------|------------|
| `terminal_error(msg)` | Unrecoverable user-facing failure (bad path, missing config). Returns `dict` so the model surfaces it without retrying. |
| `ModelRetry(msg)` | LLM-fixable error — bad params, transient service failure. Triggers pydantic-ai retry up to `tool_retries`. |
| `ApprovalRequired(metadata=...)` | Tool needs user confirmation before proceeding (deferred approval path). |

---

### Tool Families

#### Workspace and Files (`tools/files.py`)

Path validation: all paths are resolved through `_resolve_workspace_path(raw, workspace_root)` and verified to stay within `workspace_root`. Path traversal raises `ValueError` → `terminal_error`.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_directory` | auto | `path="."`, `pattern="*"`, `max_entries=200` | Lists dir contents filtered by glob; entries prefixed with `[dir]` or `[file]` |
| `read_file` | auto | `path`, `start_line?`, `end_line?` | Reads file content; optional 1-indexed inclusive line range |
| `find_in_files` | auto | `pattern` (regex), `glob="**/*"`, `max_matches=50` | Regex search across workspace; skips binary files; returns `file:line: text` |
| `write_file` | deferred | `path`, `content` | Overwrites file; creates parent dirs; returns byte count |
| `edit_file` | deferred | `path`, `search`, `replacement`, `replace_all=False` | Exact-string replace; `ModelRetry` if search not found or ambiguous (multiple matches without `replace_all`) |

#### Shell (`tools/shell.py`)

Shell execution runs through a three-stage policy check before reaching `ShellBackend.run_command()`:

```
evaluate_shell_command(cmd, safe_commands)
    → DENY      → terminal_error (blocked command class)
    → ALLOW     → execute immediately (safe prefix match)
    → REQUIRE_APPROVAL
        → is_shell_command_persistently_approved? → execute
        → ctx.tool_call_approved?                 → execute
        → else                                    → ApprovalRequired
```

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `run_shell_command` | policy | `cmd`, `timeout=120` | Runs in project cwd; stdout+stderr combined; `timeout` capped by `shell_max_timeout`; `ModelRetry` on timeout or failure |

DENY patterns include: `rm -rf /`, `dd if=`, `mkfs`, process kill with broad scope, and other destructive commands. See `tools/_shell_policy.py` for the full list.

#### Memory (`tools/memory.py`)

Memories are YAML-frontmatter markdown files stored in `.co-cli/memory/`. All reads use either FTS5 (when `knowledge_index` available) or grep fallback.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `save_memory` | deferred | `content`, `tags?`, `category?`, `provenance?` | Writes new memory file; triggers dedup check via `memory/_lifecycle.py` write path |
| `update_memory` | conditional | `memory_id`, `content` | Replaces body of existing memory; `terminal_error` if ID not found |
| `append_memory` | conditional | `memory_id`, `content` | Appends to existing memory body |
| `list_memories` | conditional | `tags?`, `category?`, `limit=20`, `offset=0` | Paginated list of memories; sorted by recency |
| `search_memories` | conditional | `query`, `tags?`, `limit=10`, ... | FTS5/hybrid search over memory files; fallback to grep; includes confidence scoring |

#### Knowledge / Articles (`tools/articles.py`)

Articles are decay-protected markdown files in the user-global library (`library_dir`). They differ from memories in that they come from external sources (URLs) and are never pruned by retention.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `save_article` | deferred | `content`, `title`, `origin_url`, `tags?`, `related?` | Saves article to library; deduplicates by `origin_url` exact match (consolidates on repeat) |
| `recall_article` | conditional | `query`, `max_results=5`, `tags?`, `tag_match_mode?`, `created_after?`, `created_before?` | Article-scoped keyword search returning summary index only (title, URL, tags, first paragraph); sorted by recency; use `read_article_detail` to load full body |
| `search_knowledge` | conditional | `query`, `kind?`, `source?`, `limit=10`, `tags?`, `tag_match_mode?`, `created_after?`, `created_before?` | Unified cross-source search (library + obsidian + drive by default); excludes memories unless `source="memory"`; post-retrieval confidence scoring and contradiction detection |
| `read_article_detail` | conditional | `slug` | Loads full article body by file stem (from `search_knowledge` or `recall_article` result); two-step pattern: search → detail |

**`search_knowledge` source routing:**

| `source` param | Searches |
|---------------|---------|
| `None` (default) | library + obsidian + drive |
| `"library"` | local articles only |
| `"memory"` | memories only (escape hatch) |
| `"obsidian"` | Obsidian vault only |

#### Obsidian (`tools/obsidian.py`)

Requires `obsidian_vault_path` configured. All paths are validated against vault root.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_notes` | conditional | `tag?`, `offset=0`, `limit=20` | Paginated vault listing; sorts alphabetically; `has_more` pagination flag |
| `search_notes` | conditional | `query`, `limit=10`, `folder?`, `tag?` | AND-logic keyword search; syncs vault into FTS index on call; regex fallback; returns snippets |
| `read_note` | conditional | `filename` | Reads full note markdown; path traversal blocked; `ModelRetry` with available-notes hint on miss |

#### Google Integration

Requires Google credentials (OAuth token or ADC). Resolved automatically via `tools/_google_auth.py`. Returns `terminal_error` or `ModelRetry` when credentials are absent.

**Drive (`tools/google_drive.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `search_drive_files` | conditional | `query`, `max_results=10`, `page_token?` | Searches My Drive; supports cursor-based pagination via `next_page_token` |
| `read_drive_file` | conditional | `file_id` | Reads file content (text/markdown export for Docs/Sheets) |

**Gmail (`tools/google_gmail.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_emails` | conditional | `query?`, `max_results=10` | Lists recent emails; standard Gmail query syntax |
| `search_emails` | conditional | `query`, `max_results=10` | Full Gmail search (same syntax as Gmail search bar) |
| `create_email_draft` | deferred | `to`, `subject`, `body`, `cc?`, `bcc?` | Creates draft; does not send; deferred unconditionally |

**Calendar (`tools/google_calendar.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_calendar_events` | conditional | `max_results=10`, `time_min?`, `time_max?` | Lists upcoming events; ISO8601 datetime params |
| `search_calendar_events` | conditional | `query`, `max_results=10` | Full-text event search |

#### Web (`tools/web.py`)

**Domain policy** (applies to `web_fetch` only): `web_fetch_blocked_domains` blocks by exact or subdomain match; `web_fetch_allowed_domains` is an optional allowlist. Domain check runs before any HTTP request.

**Content-type filter**: `web_fetch` only fetches `text/*`, `application/json`, and related text MIME types. Binary responses return a `terminal_error`.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `web_search` | policy | `query`, `num_results=5` | Brave Search API; requires `BRAVE_SEARCH_API_KEY`; returns title+URL+snippet per result |
| `web_fetch` | policy | `url`, `max_chars=50000` | Direct HTTP GET; converts HTML → Markdown via `html2text`; exponential backoff retry; Cloudflare block detection |

#### Background Tasks (`tools/task_control.py`)

Task lifecycle: `start` → `running` → `completed` / `failed` / `cancelled`. Output is streamed to disk; `check_task_status` tails the file.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `start_background_task` | deferred | `command`, `description`, `working_directory?` | Spawns subprocess via `TaskRunner`; returns `task_id` immediately |
| `check_task_status` | auto | `task_id`, `tail_lines=20` | Returns status + exit code + last N output lines |
| `cancel_background_task` | auto | `task_id` | Kills process group; marks task cancelled |
| `list_background_tasks` | auto | `status_filter?` | Lists all tasks in session; optionally filtered by status |

Config: `background_max_concurrent` caps concurrent running tasks. `background_task_inactivity_timeout` auto-cancels tasks with no output for N seconds (0 = disabled).

#### Session Utilities (`tools/todo.py`, `tools/capabilities.py`)

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `todo_write` | conditional | `todos: list[dict]` | Replaces full session todo list; validates `status` (pending/in_progress/completed/cancelled) and `priority` (high/medium/low); state lives in `CoDeps.session.session_todos` — not persisted to disk |
| `todo_read` | conditional | — | Returns current todo list; model should call before ending multi-step turns |
| `check_capabilities` | auto | — | Runs `check_runtime(deps)`; returns probe results, active integrations, reasoning chain, skill grants, tool count |

#### Delegation (`tools/delegation.py`)

Delegation tools spawn isolated sub-agents using `make_subagent_deps(base)`. Sub-agents share `services` and `config` but get fresh `session` and `runtime`. They run to completion and return a structured result.

| Tool | Approval | Sub-agent tool surface | Behavior |
|------|----------|----------------------|---------|
| `delegate_coder` | auto | `list_directory`, `read_file`, `find_in_files` | Read-only workspace analysis; no shell, no web |
| `delegate_research` | auto | `web_search`, `web_fetch` | Web-only research; no memory writes, no filesystem. Raises `ModelRetry` when `web_policy.search` or `web_policy.fetch` is not `"allow"` — web policy gate checked before spawning the sub-agent |
| `delegate_analysis` | auto | `search_knowledge`, `search_drive_files` | Knowledge + Drive read; no shell, no direct web |

Conditional registration: each tool is registered only when its matching role model chain exists in `config.role_models`.

---

### MCP Tool Servers

MCP servers extend the native tool surface at session start. Each server is configured via `mcp_servers` in `settings.json`:

```
{
  "name": {
    "command": "npx",       // stdio transport — subprocess launched by pydantic-ai
    "url": "https://...",   // OR: HTTP transport (StreamableHTTP/SSE)
    "args": [...],
    "timeout": 5,
    "env": {...},
    "approval": "auto",     // "auto" = always deferred; "never" = always auto
    "prefix": "ns_"         // optional tool name prefix to avoid collisions
  }
}
```

**Transport:** `command`+`args` launches a stdio subprocess; `url` connects to a remote HTTP server. `command` and `url` are mutually exclusive.

**Approval inheritance:** MCP tools use the same `DeferredToolRequests` mechanism as native tools. `approval="auto"` defers all calls from that server; `approval="never"` auto-approves them.

**Default servers** (shipped, gracefully skipped when `npx` is absent):

| Server | Tool prefix | Approval |
|--------|-------------|---------|
| `github` | (none) | `auto` |
| `thinking` | (none) | `never` |
| `context7` | (none) | `never` |

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell_max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard cap for `run_shell_command` timeout (seconds) |
| `shell_safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | built-in list | Safe-prefix auto-approval allowlist for shell policy |
| `web_policy.search` | `CO_CLI_WEB_POLICY_SEARCH` | `"allow"` | `web_search` approval policy: `allow`, `ask`, `deny` |
| `web_policy.fetch` | `CO_CLI_WEB_POLICY_FETCH` | `"allow"` | `web_fetch` approval policy: `allow`, `ask`, `deny` |
| `web_fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | Optional domain allowlist for `web_fetch` |
| `web_fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Domain blocklist for `web_fetch` |
| `web_http_max_retries` | `CO_CLI_WEB_HTTP_MAX_RETRIES` | `2` | Max HTTP retries for `web_fetch` |
| `web_http_backoff_base_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_BASE_SECONDS` | `1.0` | Base backoff interval for `web_fetch` retries |
| `web_http_backoff_max_seconds` | `CO_CLI_WEB_HTTP_BACKOFF_MAX_SECONDS` | `8.0` | Max backoff cap for `web_fetch` retries |
| `web_http_jitter_ratio` | `CO_CLI_WEB_HTTP_JITTER_RATIO` | `0.2` | Jitter fraction applied to backoff (0–1) |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `null` | Required for `web_search` |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `null` | Required for Obsidian tools |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `null` | Explicit OAuth token path for Google tools |
| `library_path` | `CO_LIBRARY_PATH` | `null` | Article library directory override |
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to retain completed/failed/cancelled task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Auto-cleanup old tasks on startup |
| `background_task_inactivity_timeout` | `CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT` | `0` | Auto-cancel task after N seconds of no output (0 = disabled) |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 3 defaults | MCP server map (JSON) |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Shared tool retry budget (applies to all tools) |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/files.py` | `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file` — workspace filesystem tools |
| `co_cli/tools/shell.py` | `run_shell_command` — approval-gated subprocess execution |
| `co_cli/tools/memory.py` | `save_memory`, `update_memory`, `append_memory`, `list_memories`, `search_memories` |
| `co_cli/tools/articles.py` | `save_article`, `recall_article`, `search_knowledge`, `read_article_detail` — knowledge article tools |
| `co_cli/tools/obsidian.py` | `list_notes`, `search_notes`, `read_note` — Obsidian vault tools |
| `co_cli/tools/google_drive.py` | `search_drive_files`, `read_drive_file` |
| `co_cli/tools/google_gmail.py` | `list_emails`, `search_emails`, `create_email_draft` |
| `co_cli/tools/google_calendar.py` | `list_calendar_events`, `search_calendar_events` |
| `co_cli/tools/web.py` | `web_search`, `web_fetch` — Brave Search + HTTP fetch |
| `co_cli/tools/task_control.py` | `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks` |
| `co_cli/tools/todo.py` | `todo_write`, `todo_read` — session-scoped task list |
| `co_cli/tools/capabilities.py` | `check_capabilities` — integration health introspection |
| `co_cli/tools/delegation.py` | `delegate_coder`, `delegate_research`, `delegate_analysis` — sub-agent delegation |
| `co_cli/tools/_shell_policy.py` | `evaluate_shell_command()` — DENY / ALLOW / REQUIRE_APPROVAL classification |
| `co_cli/tools/_shell_backend.py` | `ShellBackend` — subprocess execution with process-group cleanup |
| `co_cli/tools/_shell_env.py` | `restricted_env()`, `kill_process_tree()` — env sanitizer and process-group kill |
| `co_cli/tools/_approval.py` | `_is_safe_command()` — safe-prefix classification helper |
| `co_cli/tools/_tool_approvals.py` | Deferred approval helpers: `is_shell_command_persistently_approved()`, `record_approval_choice()` |
| `co_cli/tools/_exec_approvals.py` | Persistent exec approvals: `derive_pattern()`, `find_approved()`, `add_approval()`, `prune_stale()` |
| `co_cli/tools/_background.py` | `TaskStatus`, `TaskStorage` (filesystem), `TaskRunner` (asyncio process manager) |
| `co_cli/tools/_errors.py` | `terminal_error()`, `http_status_code()` — shared error helpers |
| `co_cli/tools/_google_auth.py` | Google credential resolution (ensure/get/cached) |
| `co_cli/tools/_delegation_agents.py` | `CoderResult`, `make_coder_agent()`, `ResearchResult`, `make_research_agent()`, `AnalysisResult`, `make_analysis_agent()` — delegation agent helpers |
| `co_cli/_model_factory.py` | `ModelRegistry`, `ResolvedModel`, `build_model()` — provider-aware model factory |
| `co_cli/agent.py` | `get_agent()` — `_register()` helper and full tool registration sequence |
