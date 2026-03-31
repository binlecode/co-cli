# Co CLI — Tools

> For system overview and approval boundary: [DESIGN-system.md](DESIGN-system.md). For shell inline policy and approval decision chain: see §Approval below and [DESIGN-core-loop.md](DESIGN-core-loop.md). For skill loading, file format, and slash-command dispatch, see [DESIGN-skills.md](DESIGN-skills.md).

## 1. What & How

Native tools are Python functions registered on the pydantic-ai `Agent` via `_register()` in `agent.py`. Every tool receives a `RunContext[CoDeps]` as its first argument and returns a `ToolResult` (from `co_cli/tools/_result.py`) via `make_result(display, **metadata)` — a `TypedDict` with a `_kind="tool_result"` discriminator, a `display` field (pre-formatted string shown to the user), and optional metadata fields.

This doc owns callable tool capabilities only. Skills are a separate layer built on slash-command dispatch plus prompt expansion; they are documented in [DESIGN-skills.md](DESIGN-skills.md).

Tool progress reporting is opt-in. Tools do not write to the terminal directly. When a tool has meaningful multi-phase latency, it may emit progress through turn-scoped runtime state (`ctx.deps.runtime.tool_progress_callback`) and let the frontend render those messages through the `on_tool_progress` lifecycle callback.

Tools are grouped into ten families:

```
tools/
  files.py          — workspace filesystem (list, read, find, write, edit)
  shell.py          — conditionally approved subprocess execution
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
  subagent.py       — sub-agent tools
  _subagent_agents.py — CoderResult, ResearchResult, AnalysisResult, ThinkingResult + agent factories
```

## 2. Core Logic

### Registration

All native tools are registered via `_register(fn, requires_approval, retries=None)` in `agent.py:build_agent()`. This wraps each function with `agent.tool()` and records the `(name, requires_approval)` pair in `tool_approvals`. Most tools map directly to a tool-wide approval flag. `run_shell_command` is the exception: it is registered with `requires_approval=False`, then applies command-level DENY / ALLOW / REQUIRE_APPROVAL policy inside the tool body. Sub-agent tools are registered directly via `agent.tool()` inside each sub-agent factory.

Per-tool retry budget: tools are annotated at registration by tier. Write-once tools (`write_file`, `edit_file`, `save_memory`, `save_article`, `update_memory`, `append_memory`, `create_email_draft`) use `retries=1` — a second mutation attempt on transient failure is safe but more than one is not. Network read tools (`web_search`, `web_fetch`, `list_emails`, `search_emails`, `search_drive_files`, `read_drive_file`, `list_calendar_events`, `search_calendar_events`) use `retries=3`. All other tools inherit the agent-level default (`config.tool_retries`).

Conditional registration: `run_*_subagent` tools are registered only when the matching role model chain is configured. If `reasoning` only is set, all three sub-agent tools may be unavailable.

### Tool Lifecycle — Call Stack

Four phases: discovery at session startup, then per-turn: request, approval (deferred path only), execution and return.

**Phase 1 — Tool Discovery** (`main.py` → `agent.py`)

```
main.py:build_chat_app()
  ├─ build_agent(config, resolved)                          # agent.py:84
  │    ├─ MCPServerStdio/StreamableHTTP/SSE(...)            # one per config.mcp_servers entry
  │    │    └─ .approval_required()                         # wraps server when cfg.approval=="ask"
  │    ├─ Agent(resolved.model, deps_type=CoDeps,           # pydantic-ai agent construction
  │    │        output_type=[str, DeferredToolRequests],
  │    │        toolsets=mcp_toolsets)
  │    ├─ _register(fn, requires_approval, retries?)        # agent.py:146; called per native tool
  │    │    ├─ agent.tool(fn, requires_approval=..., [retries=...])  # pydantic-ai registration
  │    │    └─ tool_approvals[fn.__name__] = requires_approval
  │    ├─ [conditional] _register(delegate_*, False)        # only when role_models has matching role
  │    └─ [web policy]  _register(web_search/fetch, policy.search/fetch == "ask")
  │    └─ return AgentCapabilityResult(agent, tool_names, tool_approvals)
  ├─ deps.capabilities.tool_names = agent_result.tool_names       # native names only
  ├─ deps.capabilities.tool_approvals = agent_result.tool_approvals
  ├─ await stack.enter_async_context(agent)                 # starts MCP server subprocesses
  └─ initialize_session_capabilities(agent, deps, frontend, mcp_init_ok)   # bootstrap/_bootstrap.py
       └─ discover_mcp_tools(agent, exclude=set(tool_names))     # agent.py:264
            └─ for toolset in agent.toolsets:
                 ├─ inner.list_tools()                      # MCPServer: enumerate via stdio/HTTP
                 └─ name = f"{prefix}_{t.name}" if prefix else t.name
       ├─ deps.capabilities.tool_names += mcp_tool_names          # native + MCP names
       └─ for prefix, err in discovery_errors:             # surface failed servers to user
            └─ frontend.on_status("MCP server {prefix!r} failed...")  # empty on happy path
```

**Phase 2 — Execution Request** (`_orchestrate.py` → pydantic-ai)

```
run_turn(agent, user_input, deps, ...)                       # _orchestrate.py
  └─ _execute_stream_segment(_TurnState, agent, deps, ...)    # _orchestrate.py
       └─ agent.run_stream_events(user_input, deps,
                                  message_history, ...)      # pydantic-ai streams model output
            ├─ [auto tool]     FunctionToolCallEvent         # requires_approval=False
            │    └─ pydantic-ai calls fn(ctx, **args)        # executes immediately → Phase 4
            └─ [deferred tool] result.output =               # requires_approval=True (or MCP auto)
                               DeferredToolRequests          # stream paused → Phase 3
```

**Phase 3 — Approval** (deferred path only, `_orchestrate.py`)

```
run_turn(): isinstance(result.output, DeferredToolRequests)  # _orchestrate.py:464
  └─ _collect_deferred_tool_approvals(result, deps, frontend) # _orchestrate.py:370
       └─ for call in result.output.approvals:
            ├─ Step 1: resolve_approval_subject(call.tool_name, args)  # → ApprovalSubject
            ├─ Step 2: is_auto_approved(subject, deps)
            │    └─ SessionApprovalRule match → approvals[call.tool_call_id] = True; continue
            └─ Step 3: frontend.prompt_approval(subject.display)  # user: y / n / a
                 └─ record_approval_choice(approvals, tool_call_id,
                                           approved=choice in ("y","a"),
                                           subject=subject,
                                           remember=choice=="a" and subject.can_remember)
  └─ return DeferredToolResults
```

**Phase 4 — Execution and Return**

```
_execute_stream_segment(_TurnState, agent, deferred_tool_results=approvals, ...)  # _orchestrate.py
  └─ agent.run_stream_events(user_input=None,
                              deferred_tool_results=approvals, ...)
       ├─ [approved] fn(ctx: RunContext[CoDeps], **args)
       │    ├─ ctx.deps.services.*   (KnowledgeIndex, ShellBackend, ...)
       │    ├─ ctx.deps.config.*     (read-only settings scalars)
       │    ├─ ctx.deps.session.*    (session_approval_rules, todos, skill_commands)
       │    └─ ctx.deps.runtime.*    (tool_progress_callback, safety_state)
       │    └─ return ToolResult     (make_result(display, **metadata) from _result.py)
       │         ├─ "_kind"          "tool_result" discriminator
       │         ├─ "display"        pre-formatted string shown to the user
       │         └─ metadata fields  (count, task_id, article_id, ...)
       │    └─ FunctionToolResultEvent → frontend.on_tool_complete(tool_id, result)
       └─ [rejected] pydantic-ai notifies model that tool_call_id was denied
```

---

### Approval Classes

| Class | Condition | Examples |
|-------|-----------|---------|
| Always deferred | `requires_approval=True`, unconditional | `write_file`, `edit_file`, `save_memory`, `save_article`, `create_email_draft`, `start_background_task`, `update_memory`, `append_memory` |
| Shell inline policy | Tool registered auto; command payload classified inside tool as DENY / ALLOW / REQUIRE_APPROVAL | `run_shell_command` |
| Always auto | `requires_approval=False` | `list_directory`, `read_file`, `find_in_files`, `check_capabilities`, `run_coder_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_thinking_subagent`, task status/list/cancel, `todo_write`, `todo_read`, all read-only personal-data tools |
| Web policy | Depends on `web_policy.search` / `web_policy.fetch` setting | `web_search`, `web_fetch` |

### Approval Tier Ordering

When a deferred tool call arrives, `_collect_deferred_tool_approvals()` in `co_cli/context/_orchestrate.py` resolves each tool's approval state in this order:

```
Step 1 — auto-approval check:
  resolve_approval_subject() → ApprovalSubject(kind, value, display, can_remember)
  is_auto_approved() → SessionApprovalRule(kind, value) in deps.session.session_approval_rules
  if match: approve silently and continue

Step 2 — user prompt (y / n / a)
  choice = frontend.prompt_approval(subject.display)
  if "a" and subject.can_remember: remember_tool_approval() stores SessionApprovalRule
```

Step 1 short-circuits the chain; Step 2 is only reached when Step 1 finds no matching rule. All approval rules are session-scoped — cleared when the session ends.

### Return Shape

Every user-facing tool returns a `ToolResult` via `make_result(display, **metadata)` (from `co_cli/tools/_result.py`):
- `_kind` — `"tool_result"` discriminator (used by `_execute_stream_segment()` to route to `on_tool_complete`)
- `display` — pre-formatted string, shown directly to the user
- metadata fields (`count`, `path`, `task_id`, `article_id`, etc.)

All tools must return `make_result()`. Never return a raw `str`, bare `dict`, or `None`.

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
        → ctx.tool_call_approved?  → execute
        → else                     → ApprovalRequired
```

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `run_shell_command` | conditional | `cmd`, `timeout=120` | Tool is registered with `requires_approval=False`; command string is classified inline as DENY, ALLOW, or REQUIRE_APPROVAL. Runs in project cwd; stdout+stderr combined; `timeout` capped by `shell_max_timeout`; `ModelRetry` on timeout or failure |

DENY patterns include: `rm -rf /`, `dd if=`, `mkfs`, process kill with broad scope, and other destructive commands. See `tools/_shell_policy.py` for the full list.

#### Memory (`tools/memory.py`)

Memories are YAML-frontmatter markdown files stored in `.co-cli/memory/`. All reads use either FTS5 (when `knowledge_index` available) or grep fallback.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `save_memory` | deferred | `content`, `tags?`, `category?`, `provenance?`, `always_on?` | Writes new memory file; triggers dedup check via `memory/_lifecycle.py` write path; `always_on=True` pins entry as standing context injected every turn |
| `update_memory` | deferred | `memory_id`, `content` | Replaces body of existing memory; `terminal_error` if ID not found |
| `append_memory` | deferred | `memory_id`, `content` | Appends to existing memory body |
| `list_memories` | auto | `tags?`, `category?`, `limit=20`, `offset=0` | Paginated list of memories; sorted by recency |
| `search_memories` | auto | `query`, `tags?`, `limit=10`, ... | FTS5/hybrid search over memory files; fallback to grep; includes confidence scoring |

#### Knowledge / Articles (`tools/articles.py`)

Articles are decay-protected markdown files in the user-global library (`library_dir`). They differ from memories in that they come from external sources (URLs) and are never pruned by retention.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `save_article` | deferred | `content`, `title`, `origin_url`, `tags?`, `related?` | Saves article to library; deduplicates by `origin_url` exact match (consolidates on repeat) |
| `recall_article` | auto | `query`, `max_results=5`, `tags?`, `tag_match_mode?`, `created_after?`, `created_before?` | Article-scoped keyword search returning summary index only (title, URL, tags, first paragraph); sorted by recency; use `read_article_detail` to load full body |
| `search_knowledge` | auto | `query`, `kind?`, `source?`, `limit=10`, `tags?`, `tag_match_mode?`, `created_after?`, `created_before?` | Unified cross-source search (library + obsidian + drive by default); excludes memories unless `source="memory"`; post-retrieval confidence scoring and contradiction detection |
| `read_article_detail` | auto | `slug` | Loads full article body by file stem (from `search_knowledge` or `recall_article` result); two-step pattern: search → detail |

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
| `list_notes` | auto | `tag?`, `offset=0`, `limit=20` | Paginated vault listing; sorts alphabetically; `has_more` pagination flag |
| `search_notes` | auto | `query`, `limit=10`, `folder?`, `tag?` | AND-logic keyword search; syncs vault into FTS index on call; regex fallback; returns snippets |
| `read_note` | auto | `filename` | Reads full note markdown; path traversal blocked; `ModelRetry` with available-notes hint on miss |

#### Google Integration

Requires Google credentials (OAuth token or ADC). Resolved automatically via `tools/_google_auth.py`. Returns `terminal_error` or `ModelRetry` when credentials are absent.

**Drive (`tools/google_drive.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `search_drive_files` | auto | `query`, `max_results=10`, `page_token?` | Searches My Drive; supports cursor-based pagination via `next_page_token` |
| `read_drive_file` | auto | `file_id` | Reads file content (text/markdown export for Docs/Sheets) |

**Gmail (`tools/google_gmail.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_emails` | auto | `query?`, `max_results=10` | Lists recent emails; standard Gmail query syntax |
| `search_emails` | auto | `query`, `max_results=10` | Full Gmail search (same syntax as Gmail search bar) |
| `create_email_draft` | deferred | `to`, `subject`, `body`, `cc?`, `bcc?` | Creates draft; does not send; deferred unconditionally |

**Calendar (`tools/google_calendar.py`):**

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `list_calendar_events` | auto | `max_results=10`, `time_min?`, `time_max?` | Lists upcoming events; ISO8601 datetime params |
| `search_calendar_events` | auto | `query`, `max_results=10` | Full-text event search |

#### Web (`tools/web.py`)

**Domain policy** (applies to `web_fetch` only): `web_fetch_blocked_domains` blocks by exact or subdomain match; `web_fetch_allowed_domains` is an optional allowlist. Domain check runs before any HTTP request.

**Content-type filter**: `web_fetch` only fetches `text/*`, `application/json`, and related text MIME types. Binary responses return a `terminal_error`.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `web_search` | policy | `query`, `num_results=5` | Brave Search API; requires `BRAVE_SEARCH_API_KEY`; returns title+URL+snippet per result |
| `web_fetch` | policy | `url`, `max_chars=50000` | Direct HTTP GET; converts HTML → Markdown via `html2text`; exponential backoff retry; Cloudflare block detection |

#### Background Tasks (`tools/task_control.py`)

Task lifecycle: `start` → `running` → `completed` / `failed` / `cancelled`. Task state is held in `ctx.deps.session.background_tasks` (in-memory only; not persisted to disk). Output is captured in memory; `check_task_status` returns the last N lines from the in-memory buffer.

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `start_background_task` | deferred | `command`, `description`, `working_directory?` | Spawns subprocess via `spawn_task()`; returns `task_id` immediately |
| `check_task_status` | auto | `task_id`, `tail_lines=20` | Returns status + exit code + last N output lines |
| `cancel_background_task` | auto | `task_id` | Kills process group; marks task cancelled |
| `list_background_tasks` | auto | `status_filter?` | Lists all tasks in session; optionally filtered by status |

#### Session Utilities (`tools/todo.py`, `tools/capabilities.py`)

| Tool | Approval | Key Parameters | Behavior |
|------|----------|---------------|---------|
| `todo_write` | auto | `todos: list[dict]` | Replaces full session todo list; validates `status` (pending/in_progress/completed/cancelled) and `priority` (high/medium/low); state lives in `CoDeps.session.session_todos` — not persisted to disk |
| `todo_read` | auto | — | Returns current todo list; model should call before ending multi-step turns |
| `check_capabilities` | auto | — | Runs `check_runtime(deps, progress=ctx.deps.runtime.tool_progress_callback)`; returns probe results, active integrations, reasoning chain, tool count, and MCP server health (`mcp_configured_server_count`, `mcp_tool_count`, `mcp_server_health`). When a turn-scoped progress callback is present, it emits staged `/doctor` progress messages such as provider, integration, knowledge, skills, and per-MCP-server checks through `on_tool_progress` |

`check_capabilities` is the runtime introspection tool used by the packaged `/doctor` skill. It is still a normal read-only tool call inside the agent loop, not a special skill execution path. Progressive doctor output is produced by an optional callback path:

```text
_execute_stream_segment() curries tool_progress_callback = frontend.on_tool_progress(tool_id, msg)
check_capabilities() reads ctx.deps.runtime.tool_progress_callback
check_runtime(progress=...) emits phase messages
TerminalFrontend renders those progress lines in the CLI
```

This pattern is intentionally optional:
- fast tools stay silent and return only their final `display`
- long-running or multi-phase tools may emit progress when the intermediate states are user-meaningful
- tools never import display code or print directly to the terminal
- plain string progress messages are passed directly through `tool_progress_callback`

#### Sub-Agent Tools (`tools/subagent.py`)

Sub-agent tools spawn isolated sub-agents using `make_subagent_deps(base)`. Sub-agents share `services` and `config` but get fresh `session` and `runtime`. They run to completion and return a structured result.

| Tool | Approval | Sub-agent tool surface | Behavior |
|------|----------|----------------------|---------|
| `run_coder_subagent` | auto | `list_directory`, `read_file`, `find_in_files` | Read-only workspace analysis; no shell, no web |
| `run_research_subagent` | auto | `web_search`, `web_fetch` | Web-only research; no memory writes, no filesystem. Raises `ModelRetry` when `web_policy.search` or `web_policy.fetch` is not `"allow"` — web policy gate checked before spawning the sub-agent |
| `run_analysis_subagent` | auto | `search_knowledge`, `search_drive_files` | Knowledge + Drive read; no shell, no direct web |
| `run_thinking_subagent` | auto | none | Structured problem decomposition via native reasoning model; no tools — pure extended thinking |

Conditional registration: each tool is registered only when its matching role model chain exists in `config.role_models`. `run_thinking_subagent` is gated on `ROLE_REASONING` (same as the primary model).

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
    "approval": "ask",      // "ask" = always deferred; "auto" = always auto
    "prefix": "ns_"         // tool name prefix; when omitted, server name is used as prefix
  }
}
```

**Transport:** `command`+`args` launches a stdio subprocess; `url` connects to a remote HTTP server. `command` and `url` are mutually exclusive.

**Approval inheritance:** MCP tools use the same `DeferredToolRequests` mechanism as native tools. `approval="ask"` defers all calls from that server (prompts the user); `approval="auto"` auto-approves them.

**Default servers** (shipped, gracefully skipped when `npx` is absent):

| Server | Tool prefix | Approval |
|--------|-------------|---------|
| `github` | `github` | `ask` |
| `context7` | `context7` | `auto` |

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
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 2 defaults | MCP server map (JSON) |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Agent-level default retry budget; write-once tools override to 1, network tools override to 3 at registration |
| `subagent_scope_chars` | `CO_CLI_SUBAGENT_SCOPE_CHARS` | `120` | Max chars of primary input captured as `scope` metadata in sub-agent tool results |
| `subagent_max_requests_coder` | `CO_CLI_SUBAGENT_MAX_REQUESTS_CODER` | `10` | Max LLM requests per coder sub-agent run |
| `subagent_max_requests_research` | `CO_CLI_SUBAGENT_MAX_REQUESTS_RESEARCH` | `10` | Max LLM requests per research sub-agent run (budget shared across retry attempts) |
| `subagent_max_requests_analysis` | `CO_CLI_SUBAGENT_MAX_REQUESTS_ANALYSIS` | `8` | Max LLM requests per analysis sub-agent run |
| `subagent_max_requests_thinking` | `CO_CLI_SUBAGENT_MAX_REQUESTS_THINKING` | `3` | Max LLM requests per thinking sub-agent run |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/files.py` | `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file` — workspace filesystem tools |
| `co_cli/tools/shell.py` | `run_shell_command` — conditionally approved subprocess execution |
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
| `co_cli/tools/subagent.py` | `run_coder_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_thinking_subagent` — sub-agent tools |
| `co_cli/tools/_shell_policy.py` | `evaluate_shell_command()` — DENY / ALLOW / REQUIRE_APPROVAL classification |
| `co_cli/tools/_shell_backend.py` | `ShellBackend` — subprocess execution with process-group cleanup |
| `co_cli/tools/_shell_env.py` | `restricted_env()`, `kill_process_tree()` — env sanitizer and process-group kill |
| `co_cli/tools/_approval.py` | `_is_safe_command()` — safe-prefix classification helper |
| `co_cli/tools/_display_hints.py` | Tool display metadata: `TOOL_START_DISPLAY_ARG`, `get_tool_start_args_display()`, `format_tool_result_for_display()` — maps tool names to display arg keys and formats tool results for the stream renderer |
| `co_cli/tools/_tool_approvals.py` | Deferred approval helpers: `ApprovalSubject`, `resolve_approval_subject()`, `is_auto_approved()`, `remember_tool_approval()`, `record_approval_choice()`, `decode_tool_args()` |
| `co_cli/tools/_background.py` | `BackgroundTaskState` dataclass, `spawn_task()`, `_monitor()`, `kill_task()` — in-memory asyncio process manager |
| `co_cli/tools/_result.py` | `ToolResult` TypedDict, `make_result()` factory, `ToolResultPayload` type alias — shared tool return contract |
| `co_cli/tools/_errors.py` | `terminal_error()`, `http_status_code()` — shared error helpers |
| `co_cli/tools/_google_auth.py` | Google credential resolution (ensure/get/cached) |
| `co_cli/tools/_subagent_agents.py` | `CoderResult`, `make_coder_agent()`, `ResearchResult`, `make_research_agent()`, `AnalysisResult`, `make_analysis_agent()`, `ThinkingResult`, `make_thinking_agent()` — sub-agent helpers |
| `co_cli/_model_factory.py` | `ModelRegistry`, `ResolvedModel`, `build_model()` — provider-aware model factory |
| `co_cli/agent.py` | `build_agent()` — `_register()` helper and full tool registration sequence |
