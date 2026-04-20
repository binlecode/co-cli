# Co CLI — Tools

## Product Intent

**Goal:** Define tool registration, visibility policy, approval model, and the built-in tool surface plus MCP discovery model.
**Functional areas:**
- Config-gated tool registration
- Visibility tiers (always-registered vs deferred/discoverable)
- Approval handling (session auto-approve rules, explicit prompts, shell policy-driven prompts)
- Shell policy and resource locks
- MCP integration and native tool catalog (37 built-in tools)

**Non-goals:**
- Parallel MCP execution across servers
- Tool-level retry (handled at turn level)

**Success criteria:** All tools registered at agent construction; deferred tools discoverable via `search_tools`; approval resume narrows toolset uniformly.
**Status:** Stable

---

> For system overview and approval boundary: [system.md](system.md). For the agent loop, orchestration, and approval flow: [core-loop.md](core-loop.md). For skill loading and slash-command dispatch: [skills.md](skills.md).

## 1. Tool Infrastructure

Section 2 owns the execution flow. This section only names the files that own the tool system.

```text
co_cli/agent/_core.py
  -> build_tool_registry(), build_agent()
co_cli/agent/_native_toolset.py
  -> NATIVE_TOOLS, _build_native_toolset(), _approval_resume_filter()
co_cli/agent/_mcp.py
  -> _build_mcp_toolsets(), discover_mcp_tools()
co_cli/context/_tool_lifecycle.py
  -> before_tool_execute(), after_tool_execute()
co_cli/context/_deferred_tool_prompt.py
  -> category awareness prompt for DEFERRED tools
co_cli/context/tool_approvals.py
  -> approval subject resolution and remembered session rules
co_cli/tools/agent_tool.py
  -> @agent_tool metadata attachment
co_cli/tools/tool_io.py
  -> tool_output(), tool_output_raw(), tool_error()
co_cli/tools/_shell_policy.py
  -> shell/code_execute approval policy
co_cli/tools/files/read.py
co_cli/tools/files/write.py
co_cli/tools/knowledge/read.py
co_cli/tools/knowledge/write.py
```

## 2. Tool Lifecycle, Approval, & Concurrency

### Lifecycle Hooks & Execution Flow

```text
run_turn()
  -> _execute_stream_segment(initial)
     -> agent.run_stream_events(...)
     -> turn_state.latest_result = SessionRunResult
  -> latest_result.output
     -> str
        -> turn complete
     -> DeferredToolRequests
        -> _run_approval_loop()
           -> deps.runtime.resume_tool_names =
              frozenset(call.tool_name for call in output.approvals)
           -> _collect_deferred_tool_approvals(latest_result, deps, frontend)
              -> for call in output.approvals
                 -> meta = output.metadata[tool_call_id]
                 -> if "question" in meta
                    -> frontend.prompt_question(...)
                    -> approvals[id] =
                       ToolApproved(override_args={"user_answer": answer})
                 -> else
                    -> decode_tool_args(call.args)
                    -> resolve_approval_subject(...)
                    -> is_auto_approved(subject, deps)
                       -> yes: approvals[id] = True
                       -> no
                          -> frontend.prompt_approval(subject)
                          -> record_approval_choice(...)
                             -> approved: approvals[id] = True
                             -> denied: approvals[id] = ToolDenied(...)
                             -> choice == "a": remember session rule
           -> turn_state.current_input = None
           -> turn_state.current_history = latest_result.all_messages()
           -> turn_state.tool_approval_decisions = approvals
           -> _execute_stream_segment(resume, deferred_tool_results=approvals)
           -> latest_result.output
              -> DeferredToolRequests: loop again
              -> anything else
                 -> deps.runtime.resume_tool_names = None
                 -> exit approval loop
```

Resume segments execute on the main agent with `deferred_tool_results=...`; on that path the SDK skips `ModelRequestNode`, so the approval-resume loop does not send a new model prompt just to execute approved tools.

**Execution Pipeline via CoToolLifecycle:**
1. **`before_tool_execute`:** Intercepts invocations to resolve relative paths to absolute system paths.
2. **`after_tool_execute`:** Enriches OpenTelemetry traces with custom tags (`co.tool.source`, `co.tool.requires_approval`, `co.tool.result_size`).

**Approval & Errors:**
- **Auto-Approve:** `_collect_deferred_tool_approvals()` writes `True` into `DeferredToolResults`; the actual tool call runs only after the resumed segment starts.
- **Requires-Approval:** Deferred calls are collected first, then resumed with `deferred_tool_results=...`. If denied, the resume payload carries `ToolDenied(...)` rather than crashing the turn.
- **Clarify:** `clarify` uses the same deferred-resume mechanism, but stores `ToolApproved(override_args={"user_answer": ...})` instead of a plain boolean approval.
- **Failures:** Hard failures trigger `tool_error(msg)` (no retry), while bad parameters return `ModelRetry(msg)` to let the model self-correct.

### Concurrency Safety

Approval controls human permission; locks ensure structural correctness.

- **Sequential Forced Flow:** Mutating actions (`file_write`, `file_patch`, `code_execute`) are registered with `is_concurrent_safe=False`. If they're in a multi-tool batch, the agent forces sequential execution.
- **Cross-agent Path Locking:** Using `CoDeps.resource_locks`, attempts to write to a path locked by another background agent result in an immediate fail-fast `tool_error()`.
- **Read-before-Write:** `file_patch` enforces that a file has been read in full prior to replacement. `CoDeps.file_partial_reads` prevents patching if the model only read a snippet.
- **Staleness Tracking:** `CoDeps.file_read_mtimes` snapshots disk modification times at read. `file_write` and `file_patch` fail if the file on disk was modified before the write was committed.

## 3. Tool Catalog

Legend: **Tool** shows the callable signature · **V** = Visibility (A=ALWAYS, D=DEFERRED) · **Appr** = requires user approval · **Lock** = sequential (non-concurrent-safe) · **Gate** = config field required

The catalog below is the native tool list from `co_cli/agent/_native_toolset.py::NATIVE_TOOLS`. MCP tools are discovered at runtime via `co_cli/agent/_mcp.py`, are always DEFERRED, and are not included in the 37-tool native total. `shell` and `code_execute` are the two runtime-approval special cases: neither is decorator-marked `approval=True`, but both can raise `ApprovalRequired` during execution based on the command path. After the naming refactor, the public surface uses domain-prefix names everywhere ambiguity exists; older suffix-style names are no longer part of the runtime tool surface.

### User Interaction

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `clarify(question, options=None, user_answer=None)` | A | — | — | — | Pause mid-execution to ask the user a clarifying question; resume with injected answer |

### Introspection & Flow Tracking

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `capabilities_check()` | A | — | — | — | Runtime doctor: binary probes, auth states, config |
| `todo_write(todos)` | A | — | — | — | Replace in-session multi-turn checklist |
| `todo_read()` | A | — | — | — | Fetch current checklist |

### Cognition & Knowledge — Read

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `knowledge_search(query, *, kind=None, source=None, limit=10, tags=None, tag_match_mode="any", created_after=None, created_before=None)` | A | — | — | — | Unified search across local knowledge, Obsidian, and Drive; `kind="article"` returns article slugs for `knowledge_article_read` |
| `knowledge_list(offset=0, limit=20, kind=None)` | A | — | — | — | Paginate knowledge artifact metadata |
| `knowledge_article_read(slug)` | A | — | — | — | Fetch full markdown for a cached article by slug |
| `memory_search(query, *, limit=5)` | A | — | — | — | Keyword search across historic session transcripts |

### Workspace & Files — Read

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `file_glob(path=".", pattern="*", max_entries=200)` | A | — | — | — | List directory or find files by pattern |
| `file_read(path, start_line=None, end_line=None)` | A | — | — | — | Read workspace file; pagination hint + fuzzy name suggestions |
| `file_grep(pattern, path=".", glob="**/*", case_insensitive=False, output_mode="content", context_lines=0, head_limit=250, offset=0)` | A | — | — | — | Regex content search across workspace |

### Web

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `web_search(query, max_results=5, domains=None)` | A | — | — | — | Brave API search with optional domain filter |
| `web_fetch(url)` | A | — | — | — | Fetch URL and convert to markdown |

### Shell Execution

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `shell(cmd, timeout=120)` | A | hybrid | — | — | Run blocking shell command; safe-prefix auto-approves, mutations prompt, destructive denied |

### Workspace & Files — Write

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `file_write(path, content)` | D | ✓ | ✓ | — | Create or overwrite a file |
| `file_patch(path, old_string, new_string, replace_all=False, show_diff=False)` | D | ✓ | ✓ | — | Targeted replacement with fuzzy fallback; `show_diff` for verification; auto-lints `.py` files |

### Cognition & Knowledge — Write

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `knowledge_update(slug, old_content, new_content)` | D | ✓ | — | — | Surgical section replacement in a knowledge artifact |
| `knowledge_append(slug, content)` | D | ✓ | — | — | Append to a knowledge artifact |
| `knowledge_article_save(content, title, origin_url, tags=None, related=None)` | D | ✓ | — | — | Persist web content as a local markdown artifact |

### Background Tasks

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `task_start(command, description, working_directory=None)` | D | ✓ | — | — | Spawn unblocked process group; returns `task_id` |
| `task_status(task_id, tail_lines=20)` | D | — | — | — | Poll stdout/stderr and completion state of a task |
| `task_cancel(task_id)` | D | — | — | — | SIGTERM → SIGKILL a background task |
| `task_list(status_filter=None)` | D | — | — | — | Enumerate active/completed tasks |

### Code Execution

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `code_execute(cmd, timeout=60)` | D | hybrid | ✓ | — | Run an interpreter command; command policy denies unsafe forms and otherwise prompts before execution |

### Delegation (Subagents)

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `web_research(query, domains=None, max_requests=0)` | D | — | — | — | Deep web retrieval subagent |
| `knowledge_analyze(question, inputs=None, max_requests=0)` | D | — | — | — | Cross-index deduction on internal + Drive knowledge |
| `reason(problem, max_requests=0)` | D | — | — | — | Pure inference subagent; no external access |

### External — Obsidian *(gate: `obsidian_vault_path`)*

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `obsidian_list(tag=None, offset=0, limit=20)` | D | — | — | ✓ | Paginate Obsidian note paths |
| `obsidian_search(query, limit=10, folder=None, tag=None)` | D | — | — | ✓ | Search note content by keyword, optionally narrowed by folder or tag |
| `obsidian_read(filename)` | D | — | — | ✓ | Read raw Obsidian markdown note |

### External — Google *(gate: `google_credentials_path`)*

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `drive_search(query, page=1)` | D | — | — | ✓ | Search Google Drive by filename or indexed text |
| `drive_read(file_id)` | D | — | — | ✓ | Read a Google Drive file as text |
| `gmail_list(max_results=5)` | D | — | — | ✓ | Fetch recent inbox messages |
| `gmail_search(query, max_results=5)` | D | — | — | ✓ | Search Gmail with advanced operators |
| `calendar_list(days_back=0, days_ahead=1, max_results=25)` | D | — | — | ✓ | List primary-calendar events in a bounded window around today |
| `calendar_search(query, days_back=0, days_ahead=30, max_results=25)` | D | — | — | ✓ | Search primary-calendar events by keyword |
| `gmail_draft(to, subject, body)` | D | ✓ | — | ✓ | Draft an outgoing message (does not send) |

**Total: 37 native tools** (14 ALWAYS · 23 DEFERRED · 7 explicit approval-gated · 10 config-gated; `shell` and `code_execute` may also prompt dynamically)

## 4. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `shell.max_timeout` | `CO_SHELL_MAX_TIMEOUT` | `600` | Hard cap for shell timeout (sec) |
| `shell.safe_commands` | `CO_SHELL_SAFE_COMMANDS` | built-in list | Safe-prefix auto-approval allowlist |
| `web.fetch_allowed_domains` | `CO_WEB_FETCH_ALLOWED_DOMAINS` | `[]` | Domain allowlist (optional) |
| `web.fetch_blocked_domains` | `CO_WEB_FETCH_BLOCKED_DOMAINS` | `[]` | Domain blocklist |
| `brave_search_api_key` | `BRAVE_SEARCH_API_KEY` | `null` | Required for `web_search` |
| `obsidian_vault_path` | `OBSIDIAN_VAULT_PATH` | `null` | Registration gate for Obsidian |
| `google_credentials_path` | `GOOGLE_CREDENTIALS_PATH` | `null` | Registration gate for Google |
| `knowledge_path` | `CO_KNOWLEDGE_PATH` | `~/.co-cli/knowledge/` | Unified knowledge artifact directory |
| `mcp_servers` | `CO_MCP_SERVERS` | 2 defaults | MCP server definitions |
| `tool_retries` | `CO_TOOL_RETRIES` | `3` | Default agent retry budget |
| `subagent.max_requests_*` | `CO_SUBAGENT_MAX_REQUESTS_*` | var | Per-role request caps |
