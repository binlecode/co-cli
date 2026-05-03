# Co CLI — Tools


> For system overview and approval boundary: [system.md](system.md). For the agent loop, orchestration, and approval flow: [core-loop.md](core-loop.md). For skill loading and slash-command dispatch: [skills.md](skills.md).

## 1. Tool Infrastructure

Section 2 owns the execution flow. This section only names the files that own the tool system.

```text
co_cli/agent/core.py
  -> build_tool_registry(), build_agent()
co_cli/agent/_native_toolset.py
  -> NATIVE_TOOLS, _build_native_toolset(), _approval_resume_filter()
co_cli/agent/mcp.py
  -> _build_mcp_toolsets(), discover_mcp_tools()
co_cli/tools/lifecycle.py
  -> before_tool_execute(), after_tool_execute()
co_cli/tools/deferred_prompt.py
  -> category awareness prompt for DEFERRED tools
co_cli/tools/approvals.py
  -> approval subject resolution and remembered session rules
co_cli/tools/agent_tool.py
  -> @agent_tool metadata attachment
co_cli/tools/tool_io.py
  -> tool_output(), tool_output_raw(), tool_error()
co_cli/tools/_shell_policy.py
  -> shell/code_execute approval policy
co_cli/tools/files/read.py
co_cli/tools/files/write.py
co_cli/tools/memory/recall.py
co_cli/tools/memory/read.py
co_cli/tools/memory/write.py
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
                 -> if "questions" in meta
                    -> for q in meta["questions"]
                       -> frontend.prompt_question(...)
                    -> approvals[id] =
                       ToolApproved(override_args={"user_answers": answers})
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
2. **`after_tool_execute`:** Enriches OpenTelemetry traces: `co.tool.result_size` (all tool spans, including delegation); `co.tool.source` and `co.tool.requires_approval` set for native tools only (present in `tool_index`).

**Approval & Errors:**
- **Auto-Approve:** `_collect_deferred_tool_approvals()` writes `True` into `DeferredToolResults`; the actual tool call runs only after the resumed segment starts.
- **Requires-Approval:** Deferred calls are collected first, then resumed with `deferred_tool_results=...`. If denied, the resume payload carries `ToolDenied(...)` rather than crashing the turn.
- **Clarify:** `clarify` uses the same deferred-resume mechanism, but iterates `meta["questions"]`, prompts each one, and stores `ToolApproved(override_args={"user_answers": [...]})` — a list aligned to the input questions.
- **Failures:** Hard failures trigger `tool_error(msg)` (no retry), while bad parameters return `ModelRetry(msg)` to let the model self-correct.

### Concurrency Safety

Approval controls human permission; locks ensure structural correctness.

- **Sequential Forced Flow:** Mutating actions (`file_write`, `file_patch`, `code_execute`) are registered with `is_concurrent_safe=False`. If they're in a multi-tool batch, the agent forces sequential execution.
- **Cross-agent Path Locking:** Using `CoDeps.resource_locks`, attempts to write to a path locked by another background agent result in an immediate fail-fast `tool_error()`.
- **Read-before-Write:** `file_patch` enforces that a file has been read in full prior to replacement. `CoDeps.file_partial_reads` prevents patching if the model only read a snippet.
- **Staleness Tracking:** `CoDeps.file_read_mtimes` snapshots disk modification times at read. `file_write` and `file_patch` fail if the file on disk was modified before the write was committed.

## 3. Tool Catalog

Legend: **Tool** shows the callable signature · **V** = Visibility (A=ALWAYS, D=DEFERRED) · **Appr** = requires user approval · **Lock** = sequential (non-concurrent-safe) · **Gate** = config field required

The catalog below is the native tool list from `co_cli/agent/_native_toolset.py::NATIVE_TOOLS`. MCP tools are discovered at runtime via `co_cli/agent/mcp.py`, are always DEFERRED, and are not included in the native total. `shell` and `code_execute` are the two runtime-approval special cases: neither is decorator-marked `approval=True`, but both can raise `ApprovalRequired` during execution based on the command path. After the naming refactor, the public surface uses domain-prefix names everywhere ambiguity exists; older suffix-style names are no longer part of the runtime tool surface. The grouping below is capability-oriented so it can be compared directly with peer inventories such as Hermes without changing the underlying visibility or approval semantics.

### Interaction & Session Control

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `clarify(questions, user_answers=None)` | A | — | — | — | Pause mid-execution to ask a batch of questions; each question dict has `{question, options?, multiple?}`; returns JSON list[str] positionally aligned to questions |
| `capabilities_check()` | A | — | — | — | Canonical self-check surface: grouped tool visibility, approval-gating, unavailable or limited integrations, bootstrap-recorded fallbacks |
| `todo_write(todos)` | A | — | — | — | Replace in-session multi-turn checklist |
| `todo_read()` | A | — | — | — | Fetch current checklist |

### Workspace & File Operations

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `file_find(path=".", pattern="*", max_entries=200)` | A | — | — | — | List directory or find files by path/name pattern |
| `file_read(path, start_line=None, end_line=None)` | A | — | — | — | Read workspace file; 500-line default cap with continuation hint; 500 KB full-read gate; 2000-char per-line truncation; fuzzy name suggestions on not-found |
| `file_search(pattern, path=".", glob="**/*", case_insensitive=False, output_mode="content", context_lines=0, head_limit=250, offset=0)` | A | — | — | — | Regex content search across workspace; use `glob` to limit the searched file set |
| `file_write(path, content)` | D | ✓ | ✓ | — | Create or overwrite a file |
| `file_patch(path, old_string, new_string, replace_all=False, show_diff=False)` | D | ✓ | ✓ | — | Targeted replacement with fuzzy fallback; `show_diff` for verification; auto-lints `.py` files |

### Knowledge, Memory & Skills

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `memory_search(query="", kinds=None, limit=10)` | A | — | — | — | Unified recall across saved artifacts (including `kind='canon'` scenes) and indexed past sessions; empty query browses recent sessions plus artifact inventory; keyword query returns chunk/snippet hits with no session summarizer LLM |
| `memory_create(content, artifact_kind, title=None, description=None, source_url=None, decay_protected=False)` | A | ✓ | — | — | Persist a knowledge artifact; URL saves dedupe by `source_ref`; optional consolidation can skip, merge, or append near-duplicates |
| `memory_modify(filename_stem, action, content, target="")` | A | ✓ | — | — | Append to or surgically replace text in an existing knowledge artifact, then reindex |

### Web, Browser & Media

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `web_search(query, max_results=5, domains=None)` | A | — | — | — | Brave API search with optional domain filter |
| `web_fetch(url, format="markdown", timeout=15)` | A | — | — | — | Fetch URL; `format` controls output (`markdown`, `html`, `text`); `timeout` overrides the default 15 s limit |

### Execution, Jobs & Delegation

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `shell(cmd, timeout=120, workdir=None)` | A | hybrid | — | — | Run blocking shell command; `workdir` executes in a workspace-relative subdirectory (traversal blocked); safe-prefix auto-approves, mutations prompt, destructive denied |
| `task_start(command, description, working_directory=None)` | D | ✓ | — | — | Spawn unblocked process group; returns `task_id` |
| `task_status(task_id, tail_lines=20)` | D | — | — | — | Poll stdout/stderr and completion state of a task |
| `task_cancel(task_id)` | D | — | — | — | SIGTERM → SIGKILL a background task |
| `task_list(status_filter=None)` | D | — | — | — | Enumerate active/completed tasks |
| `code_execute(cmd, timeout=60)` | D | hybrid | ✓ | — | Run an interpreter command; command policy denies unsafe forms and otherwise prompts before execution |
| `web_research(query, domains=None, max_requests=0)` | D | — | — | — | Deep web retrieval subagent |
| `knowledge_analyze(question, inputs=None, max_requests=0)` | D | — | — | — | Cross-index deduction on internal + Drive knowledge |
| `reason(problem, max_requests=0)` | D | — | — | — | Pure inference subagent; no external access |

### External Service Integrations

#### Obsidian *(gate: `obsidian_vault_path`)*

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `obsidian_list(tag=None, offset=0, limit=20)` | D | — | — | ✓ | Paginate Obsidian note paths |
| `obsidian_search(query, limit=10, folder=None, tag=None)` | D | — | — | ✓ | Search note content by keyword, optionally narrowed by folder or tag |
| `obsidian_read(filename)` | D | — | — | ✓ | Read raw Obsidian markdown note |

#### Google *(gate: `google_credentials_path`)*

| Tool | V | Appr | Lock | Gate | Purpose |
|------|---|------|------|------|---------|
| `google_drive_search(query, page=1)` | D | — | — | ✓ | Search Google Drive by filename or indexed text |
| `google_drive_read(file_id)` | D | — | — | ✓ | Read a Google Drive file as text |
| `google_gmail_list(max_results=5)` | D | — | — | ✓ | Fetch recent inbox messages |
| `google_gmail_search(query, max_results=5)` | D | — | — | ✓ | Search Gmail with advanced operators |
| `google_calendar_list(days_back=0, days_ahead=1, max_results=25)` | D | — | — | ✓ | List primary-calendar events in a bounded window around today |
| `google_calendar_search(query, days_back=0, days_ahead=30, max_results=25)` | D | — | — | ✓ | Search primary-calendar events by keyword |
| `google_gmail_draft(to, subject, body)` | D | ✓ | — | ✓ | Draft an outgoing message (does not send) |

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
| `max_requests` tool arg | — | 10 / 8 / 3 | Per-call override for delegation request caps (research / analysis / thinking); defaults are function-local |
