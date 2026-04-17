# Co CLI — Tools

## Product Intent

**Goal:** Define tool registration, visibility policy, approval model, and the complete tool surface.
**Functional areas:**
- Config-gated tool registration
- Visibility tiers (always-registered vs deferred/discoverable)
- Three approval classes (auto-approve, requires-approval, deferred)
- Shell policy and resource locks
- MCP integration and tool catalog (37 tools)

**Non-goals:**
- Parallel MCP execution across servers
- Tool-level retry (handled at turn level)

**Success criteria:** All tools registered at agent construction; deferred tools discoverable via `search_tools`; approval resume narrows toolset uniformly.
**Status:** Stable

---

> For system overview and approval boundary: [system.md](system.md). For the agent loop, orchestration, and approval flow: [core-loop.md](core-loop.md). For skill loading and slash-command dispatch: [skills.md](skills.md).

## 1. Tool Tree & Architecture

The tool ecosystem is composed of core infrastructure for execution, lifecycle and approval, along with a suite of domain tools.

### Core Infrastructure
- `co_cli/tools/tool_io.py` — `tool_output()`, `tool_output_raw()`, `tool_error()`, `ToolResultPayload`, HTTP error helpers, oversized-result persistence (>50k chars → content-addressed file)
- `co_cli/tools/resource_lock.py` — in-process `ResourceLockStore` for async cross-agent concurrency
- `co_cli/tools/background.py` — process-group management for long-running tasks
- `co_cli/tools/shell_backend.py` — subprocess execution with output streaming
- `co_cli/tools/_shell_policy.py`, `_shell_env.py` — command classification (ALLOW/DENY/APPROVE)
- `co_cli/tools/google/_auth.py` — Google OAuth credential resolution; shared `_get_google_service()` factory (package-private)
- `co_cli/tools/_agent_outputs.py` — typed `BaseModel` outputs for delegation agents
- `co_cli/context/tool_display.py` — console rendering and truncation logic
- `co_cli/context/tool_approvals.py` — approval subject resolution and loop logic

### Domain Tools
- `co_cli/tools/files.py` — `glob`, `read_file`, `grep`, `write_file`, `patch`
- `co_cli/tools/shell.py` — `run_shell_command`
- `co_cli/tools/memory.py` — `search_memories` (deprecated — delegates to `session_search` for episodic recall), `list_memories` (deprecated alias for `list_knowledge`); `update_memory`, `append_memory` (approval-required)
- `co_cli/tools/knowledge.py` — `save_knowledge` (extractor-only), `list_knowledge`, `search_knowledge`, `save_article`, `search_articles`, `read_article`
- `co_cli/tools/web.py` — `web_search`, `web_fetch`
- `co_cli/tools/task_control.py` — `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`
- `co_cli/tools/todo.py` — `write_todos`, `read_todos`
- `co_cli/tools/capabilities.py` — `check_capabilities`
- `co_cli/tools/session_search.py` — `session_search` (transcript FTS search)
- `co_cli/tools/agents.py` — delegation: `research_web`, `analyze_knowledge`, `reason_about`
- `co_cli/tools/execute_code.py` — `execute_code`
- `co_cli/tools/obsidian.py` — `list_notes`, `search_notes`, `read_note`
- `co_cli/tools/google/drive.py` — `search_drive_files`, `read_drive_file`
- `co_cli/tools/google/gmail.py` — `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft`
- `co_cli/tools/google/calendar.py` — `list_calendar_events`, `search_calendar_events`

## 2. Tool Lifecycle & Concurrency

### Lifecycle Hooks & Execution

```mermaid
flowchart TD
    TURN([Per Turn]) --> FILTER["_approval_resume_filter"]
    FILTER --> SEG[_execute_stream_segment]
    SEG --> T{tool called?}

    T -->|auto| EX["Execute → tool_output() → ToolReturn"]
    T -->|approval-required| PAUSE["Pause — DeferredToolRequests"]

    PAUSE --> COLLECT["_collect_deferred_tool_approvals:
1. decode_tool_args → resolve_approval_subject
2. is_auto_approved? → approve silently
3. prompt user: y / n / a"]
    COLLECT --> NARROW["resume_tool_names = approved names;
one-shot allowlist:
only those + ALWAYS visible"]
    NARROW --> AP{approved?}
    AP -->|yes| RESUME["_execute_stream_segment
(main agent)"]
    AP -->|no| DENY["ToolDenied → model notified"]
    RESUME --> MORE{more pending?}
    DENY --> MORE
    MORE -->|yes| COLLECT
    MORE -->|no| DONE

    EX --> DONE([Turn complete])
```

**CoToolLifecycle** (registered via `capabilities=[CoToolLifecycle()]` in `build_agent()`) intercepts execution:
- `before_tool_execute` — resolves relative `path` args to absolute for file tools.
- `after_tool_execute` — enriches the SDK's `execute_tool` OTel span with `co.tool.source`, `co.tool.requires_approval`, `co.tool.result_size`.

**Error Contract:**
- `tool_error(msg)` — Terminal (won't fix itself). Shown to model as `error=True`. No retries.
- `ModelRetry(msg)` — Transient (bad params, rate limit). Retried up to `tool_retries`.
- `ApprovalRequired(...)` — Interrupts execution, triggers UI prompt.

### Concurrency Safety

Approval controls permission; resource locks control correctness. 

**Within-turn serialization:** `write_file`, `patch`, and `execute_code` are registered with `is_concurrent_safe=False`, which causes `_register_tool()` to derive `sequential=True`. The SDK serializes the entire batch if any tool in it is marked sequential.
**Cross-agent locking:** `ResourceLockStore` is an in-process `asyncio.Lock` shared via `CoDeps.resource_locks`. Fail-fast: if the lock is held, the tool returns `tool_error()` immediately.
- `write_file` & `patch` lock on the resolved absolute path to prevent read-modify-write races.
**Staleness Guard:** `CoDeps.file_read_mtimes` records the disk mtime at read. `write_file` and `patch` verify it hasn't changed before writing.
**Read-before-write enforcement:** `patch` requires a prior full `read_file` call — `CoDeps.file_partial_reads` tracks paths read with `start_line`/`end_line`; partial reads block patching until a full read clears the flag.

### Shell Policy

Three-stage classification inside `run_shell_command` (`_shell_policy.py`):
1. **DENY** — Control characters, heredoc injection (`<<`), env-injection (`VAR=$(...)`), absolute-path destruction.
2. **ALLOW** — `_is_safe_command()` matches safe-prefix allowlist (e.g. `ls`, `git status`) + arg validation.
3. **REQUIRE_APPROVAL** — Everything else.

## 3. Tool Registration & Catalog

Native tools are registered into a `FunctionToolset` via `_build_native_toolset()`. 
MCP tools are loaded via `DeferredLoadingToolset` wrappers and normalized into `tool_index`.

**Axes of Registration (ToolInfo):**
- **Visibility:** `ALWAYS` (visible on turn one) vs `DEFERRED` (discovered dynamically via `search_tools`).
- **Approval:** `auto` (silent execution) vs `deferred` (user must approve, interrupts execution).
- **is_read_only:** `True` — tool never mutates any state. Implies `is_concurrent_safe=True`.
- **is_concurrent_safe:** `True` — tool may run in parallel with others. Derived into SDK `sequential = not is_concurrent_safe`.
- **Retries:** `1` (write-once), `3` (network reads), or `default` (`config.tool_retries`).
- **Max Result Size:** Truncation threshold before spilling to storage (default 50k).
- **Integration:** Gate tied to specific credentials/configs.

### Tool Catalog

**Core tools (27 tools)**
| Tool | Visibility | Approval | Seq | Retries | Max Size | Notes |
|------|------------|----------|-----|---------|----------|-------|
| `check_capabilities` | ALWAYS | auto | no | default | 50k | Introspection for /doctor |
| `write_todos`, `read_todos` | ALWAYS | auto | no | default | 50k | Session task list |
| `search_knowledge` | ALWAYS | auto | no | default | 50k | Universal reusable-recall across all knowledge artifact kinds + Obsidian + Drive |
| `list_knowledge` | ALWAYS | auto | no | default | 50k | Paginated artifact inventory with `artifact_kind` column |
| `read_article` | ALWAYS | auto | no | default | 50k | Read full artifact body by slug |
| `session_search` | ALWAYS | auto | no | default | 50k | Episodic memory — FTS5 keyword search over past session transcripts |
| `search_memories` | ALWAYS | auto | no | default | 50k | Deprecated — delegates to `session_search` |
| `search_articles`, `list_memories` | ALWAYS | auto | no | default | 50k | Deprecated aliases: `search_articles` → `search_knowledge`; `list_memories` → `list_knowledge` |
| `glob`, `grep` | ALWAYS | auto | no | default | 50k | Workspace search |
| `read_file` | ALWAYS | auto | no | default | 80k | Workspace read |
| `web_search`, `web_fetch` | ALWAYS | auto | no | 3 | 50k | Network bounds/fetch |
| `run_shell_command` | ALWAYS | auto*| no | default | 30k | *Checks policy inside tool body |
| `write_file`, `patch` | DEFERRED | deferred | yes | 1 | 50k | Write with lock & staleness check |
| `save_article` | DEFERRED | deferred | no | 1 | 50k | URL dedup |
| `start_background_task` | DEFERRED | deferred | no | default | 50k | Async proc execution |
| `check_task_status`, `list_background_tasks` | DEFERRED | auto | no | default | 50k | Read background state |
| `cancel_background_task` | DEFERRED | auto | no | default | 50k | Kill proc tree |
| `execute_code` | DEFERRED | auto* | yes | default | 50k | *Always requires approval (inline guard, no safe-prefix bypass) |
| `research_web`, `analyze_knowledge`, `reason_about` | DEFERRED | auto | no | default | 50k | Isolated subagent spawning |

**Integration tools (10 tools)**
Excluded when the required credential or config path is absent.
| Tool | Visibility | Approval | Seq | Retries | Max Size | Gate |
|------|------------|----------|-----|---------|----------|------|
| `list_notes`, `search_notes`, `read_note` | DEFERRED | auto | no | default | 50k | `obsidian_vault_path` |
| `search_drive_files`, `read_drive_file` | DEFERRED | auto | no | 3 | 50k | `google_credentials_path` |
| `list_gmail_emails`, `search_gmail_emails` | DEFERRED | auto | no | 3 | 50k | `google_credentials_path` |
| `list_calendar_events`, `search_calendar_events`| DEFERRED | auto | no | 3 | 50k | `google_credentials_path` |
| `create_gmail_draft` | DEFERRED | deferred | no | 1 | 50k | `google_credentials_path` |

### Default MCP Servers
Configured in `settings.json`. MCP tools are normalized to `visibility=DEFERRED`.
- `context7`: Context7 documentation provider (auto approval).

## 4. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell.max_timeout` | `CO_CLI_SHELL_MAX_TIMEOUT` | `600` | Hard cap for shell timeout (sec) |
| `shell.safe_commands` | `CO_CLI_SHELL_SAFE_COMMANDS` | built-in list | Safe-prefix auto-approval allowlist |
| `web.fetch_allowed_domains` | `CO_CLI_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | Domain allowlist (optional) |
| `web.fetch_blocked_domains` | `CO_CLI_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Domain blocklist |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `null` | Required for `web_search` |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `null` | Registration gate for Obsidian |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `null` | Registration gate for Google |
| `knowledge_dir` | `CO_KNOWLEDGE_DIR` | `~/.co-cli/knowledge/` | Unified knowledge artifact directory |
| `mcp_servers` | `CO_CLI_MCP_SERVERS` | 2 defaults | MCP server definitions |
| `tool_retries` | `CO_CLI_TOOL_RETRIES` | `3` | Default agent retry budget |
| `subagent.max_requests_*` | `CO_CLI_SUBAGENT_MAX_REQUESTS_*` | var | Per-role request caps |

