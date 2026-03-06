# Tools — Execution

Local host execution tools: todo session state, shell subprocess, workspace files, background processes, and capability introspection. Part of the [Tools index](DESIGN-tools.md).

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

**`run_shell_command(cmd, timeout=120) → str | dict[str, Any]`** — Policy check runs first inside the tool (before any deferral). Then delegates to `ShellBackend.run_command()`. Returns a string on success, or a `terminal_error` dict on permission denied or policy DENY. Raises `ModelRetry` on most other errors so the LLM can self-correct.

```
run_shell_command(ctx, cmd, timeout=120):
    policy = evaluate_shell_command(cmd, ctx.deps.shell_safe_commands)
    DENY  → return terminal_error(policy.reason)            # never deferred, never executed
    REQUIRE_APPROVAL:
        found = find_approved(cmd, load_approvals(exec_approvals_path))
        if found → update_last_used; fall through to execution
        elif not ctx.tool_call_approved → raise ApprovalRequired(metadata={"cmd": cmd})
    ALLOW / persistent approval / tool_call_approved → fall through
    effective = min(timeout, ctx.deps.shell_max_timeout)
    try:
        return ctx.deps.shell.run_command(cmd, effective)
    on timeout         → ModelRetry("timed out, use shorter command or increase timeout")
    on permission denied → terminal_error dict (no retry — model sees error, picks different tool)
    on other RuntimeError → ModelRetry("command failed, try different approach")
    on any other exception → ModelRetry("unexpected error, try different approach")
```

**Policy tiers** — DENY, ALLOW, and persistent approvals evaluated inside `run_shell_command`; tiers 4–7 in `_handle_approvals()`:

**Tier 1 — DENY** (`shell_policy.evaluate_shell_command()`): blocks control chars, heredoc `<<`, env-injection `VAR=$(...)`, absolute-path destruction `rm -rf /~`. Returns `terminal_error` immediately — no deferral, no prompt.

**Tier 2 — ALLOW** (`_approval._is_safe_command`): rejects shell chaining operators (`;`, `&`, `|`, `>`, `<`, `` ` ``, `$(`, `\n`), then prefix-matches against `safe_commands` (longest prefix first). Auto-executes without prompting.

**Tier 3 — Persistent cross-session approvals** (`_exec_approvals`), evaluated inside the tool:

```
found = find_approved(cmd, load_approvals(.co-cli/exec-approvals.json))  ← fnmatch pattern matching
if found → update_last_used; fall through to execution
```

`derive_pattern(cmd)` collects consecutive non-flag tokens from the start (up to 3), stopping at the first flag, then appends ` *` (e.g. `git commit -m "msg"` → `git commit *`). Bare `"*"` is never auto-approved.

**Tier 4 — Skill allowed-tools grants** (`deps.active_skill_allowed_tools`): auto-approves when the active skill's `allowed-tools` frontmatter grants this tool for the current turn.

**Tier 5 — Per-session auto-approve** (`deps.auto_approved_tools`, non-shell tools only).

**Tier 6 — Risk classifier** (optional, `deps.approval_risk_enabled`): `ApprovalRisk.LOW` auto-approves when `deps.approval_auto_low_risk` is set; `ApprovalRisk.HIGH` prepends `[HIGH RISK]` to the approval prompt.

**Tier 7 — User prompt** — `[y/n/a]`.

**`"a"` persistence:** for `run_shell_command`, `_handle_approvals` derives and saves a pattern to `.co-cli/exec-approvals.json` (cross-session). For other tools, adds to `deps.auto_approved_tools` (session-only).

`/approvals list` and `/approvals clear [id]` manage stored patterns at the REPL.

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
| `co_cli/shell_policy.py` | `evaluate_shell_command()` — DENY / ALLOW / REQUIRE_APPROVAL pre-screening |
| `co_cli/_approval.py` | `_is_safe_command()` — safe-prefix classification (called by `shell_policy`) |
| `co_cli/_exec_approvals.py` | Persistent exec approvals (tier 3, evaluated inside `run_shell_command`): `derive_pattern()`, `find_approved()`, `add_approval()`, `update_last_used()`, `prune_stale()` |
| `co_cli/_shell_env.py` | `restricted_env()` and `kill_process_tree()` |
| `co_cli/deps.py` | `CoDeps` — holds `shell`, `shell_safe_commands`, `shell_max_timeout`, `exec_approvals_path`, `auto_approved_tools` |
| `co_cli/config.py` | Shell settings with env var mappings |
| `co_cli/_orchestrate.py` | `_handle_approvals()` — four-tier approval chain (skill grants → per-session → risk → user prompt) |
| `co_cli/agent.py` | Tool registration (`requires_approval=False`) + shell system prompt injection |
| `tests/test_shell.py` | Functional tests — subprocess execution, env sanitization, timeout, cwd |
| `tests/test_commands.py` | Safe-command classification tests — prefix matching, chaining rejection |
| `tests/test_shell_policy.py` | Policy engine unit tests — DENY / ALLOW / REQUIRE_APPROVAL decision coverage |

---

## File Tools

### 1. What & How

Five native tools for reading and writing workspace files. Scope is bounded to the current working directory using a resolved-path guard in `_resolve_workspace_path`. Read tools (`list_directory`, `read_file`, `find_in_files`) are auto-approved; write tools (`write_file`, `edit_file`) require explicit approval.

```
list_directory(path=".", pattern="*", max_entries=200)
  └── List files/dirs matching glob, relative paths, capped at max_entries

read_file(path, start_line=None, end_line=None)
  └── Read file content (full or line range)

find_in_files(pattern, glob="**/*", max_matches=50)
  └── Grep-like regex search across workspace files

write_file(path, content)          [requires_approval=True]
  └── Write/overwrite a file

edit_file(path, search, replacement, replace_all=False)  [requires_approval=True]
  └── Exact-string replacement; raises ValueError if search not found
      or found >1 times when replace_all=False
```

### 2. Core Logic

**Path safety (`_resolve_workspace_path`):** Paths are resolved relative to `Path.cwd()`, then checked with a string-prefix guard; escape attempts usually return `terminal_error()` (not `ModelRetry`). Current implementation uses prefix matching rather than path-component matching.

**`list_directory(ctx, path, pattern, max_entries) → dict`** — Sorted listing of files and directories matching the glob pattern. Returns `display`, `path`, `count`, `entries`. Truncation is silent — the loop breaks at `max_entries` without a `truncated` flag.

**`read_file(ctx, path, start_line, end_line) → dict`** — Reads full content or a line range. Returns `display`, `lines`, `path`.

**`find_in_files(ctx, pattern, glob, max_matches) → dict`** — Regex search: compiles the pattern and calls `compiled.search(line)` per line across matched files. Returns `display`, `pattern`, `count`, `matches`. Truncation is silent — the loop breaks at `max_matches` without a `truncated` flag.

**`write_file(ctx, path, content) → dict`** — Creates parent directories as needed. Returns `display`, `path`, `bytes`.

**`edit_file(ctx, path, search, replacement, replace_all) → dict`** — Raises `ValueError` if `search` is not found, or if found >1 times when `replace_all=False` (prevents ambiguous edits). Returns `display`, `path`, `replacements`.

### 3. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/files.py` | All five file tools + `_resolve_workspace_path` |
| `co_cli/agent.py` | Registration: read tools `requires_approval=False`, write tools `requires_approval=True` |
| `tests/test_tools_files.py` | Functional tests: path safety, read/write/edit, workspace isolation |

---

## Background Tasks

### 1. What & How

Four tools for running shell commands in the background without blocking the chat session. Long-running operations (test suites, batch jobs, research scripts) run in subprocesses with combined stdout+stderr captured to `.co-cli/tasks/<task_id>/output.log`. The chat loop stays responsive while tasks run.

```
start_background_task(command, description, working_directory?)  [requires_approval=True]
  └── TaskRunner.start_task() → spawns asyncio subprocess, returns task_id

check_task_status(task_id, tail_lines=20)
  └── reads metadata.json + last N lines of output.log

cancel_background_task(task_id)
  └── TaskRunner.cancel_task() → SIGTERM → 200ms → SIGKILL via process group

list_background_tasks(status_filter?)
  └── TaskStorage.list_tasks() → filtered metadata list
```

### 2. Core Logic

**`start_background_task`** — Creates a `background_task_execute` OTel span, calls `runner.start_task(command, cwd, approval_record, span_id)`. Raises `ModelRetry` on spawn failure. Returns `{display, task_id, status}`.

**`check_task_status`** — Reads `metadata.json` and `result.json` (for duration). Returns last `tail_lines` lines of `output.log`. If `is_binary=true` in metadata, returns `[binary output — NNN bytes]` instead. Returns `[output log not found]` if log is missing. Return schema: `{display, task_id, status, duration, exit_code, output_lines, is_binary}`.

**`cancel_background_task`** — Returns `display='Task already completed'` if status is not `running`. Returns `{display, task_id, status}`.

**`list_background_tasks`** — Returns `{display, tasks: list[{task_id, status, command, started_at}], count}`.

**TaskRunner lifecycle:** Created once in `main.py` before `chat_loop()` starts. `TaskRunner.shutdown()` is called from the `try/finally` block on clean exit and SIGINT — kills all live tasks (SIGTERM → 200ms → SIGKILL), marks them `cancelled`, writes `result.json`. Timeout: 5s total.

**Orphan recovery:** On init, any task with `status=running` in storage is a crash orphan (process dead, not in `_live` dict). Marked `failed`, `exit_code=-1`, `result.json` written with sentinel summary.

**Binary detection:** First 4096 bytes of `output.log` are sniffed after process exit. If null byte (`\x00`) or >30% non-printable chars, `is_binary=True` is written to `metadata.json`.

**Inactivity timeout:** When `background_task_inactivity_timeout > 0`, an asyncio watcher polls `output.log` size every 1s. Deadline resets on growth; fires `cancel_task()` on expiry.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to retain completed/failed/cancelled task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Run retention cleanup on `TaskRunner` init |
| `background_task_inactivity_timeout` | `CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT` | `0` | Auto-cancel after N seconds of no output (0 = disabled) |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/background.py` | `TaskStatus` enum, `TaskStorage` (filesystem), `TaskRunner` (asyncio process manager, orphan recovery, shutdown) |
| `co_cli/tools/task_control.py` | Four agent tools: start, check, cancel, list |
| `co_cli/agent.py` | Registration: `start_background_task` with approval, others without |
| `co_cli/config.py` | Background settings fields |
| `co_cli/deps.py` | `task_runner: Any \| None` field |
| `co_cli/main.py` | `TaskRunner` init before `chat_loop()`, injected into `create_deps()`, `shutdown()` in `try/finally` |
| `co_cli/_commands.py` | `/background`, `/tasks`, `/cancel` handlers; `/status <task_id>` branch |
| `tests/test_background.py` | Functional tests: storage, runner lifecycle, cancellation, orphan recovery, slash commands |

---

## Capabilities Tool

### 1. What & How

`check_capabilities` is a read-only introspection tool used by the `/doctor` skill to surface the health of active integrations. It reads from `ctx.deps` — no network calls, no side effects. Registered with `requires_approval=False`.

### 2. Core Logic

**`check_capabilities(ctx) → dict`** — Returns a formatted summary of active integrations:

- `knowledge_backend`: active search backend (`"fts5"`, `"hybrid"`, or `"grep"` when index unavailable)
- `reranker`: active reranker provider name from `ctx.deps.knowledge_reranker_provider`
- `google`: `True` if `google_credentials_path` is set on `CoDeps`
- `obsidian`: `True` if `obsidian_vault_path` is set on `CoDeps`
- `brave`: `True` if `brave_search_api_key` is set on `CoDeps`
- `mcp_count`: count of configured MCP servers from `ctx.deps.mcp_count`
- `display`: human-readable formatted summary

### 3. Files

| File | Purpose |
|------|---------|
| `co_cli/tools/capabilities.py` | `check_capabilities` — capability introspection |
| `co_cli/agent.py` | Registration: `agent.tool(check_capabilities, requires_approval=False)` |
