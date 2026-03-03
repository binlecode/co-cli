# TODO: Background Execution

**Goal**: Enable long-running tasks to execute in background without blocking interactive chat.

**Problem**: Operations like test runs, batch file processing, and research tasks block the chat loop for minutes. Users cannot issue new commands until completion, forcing multiple terminal sessions.

**Non-goals**: Distributed task queue, cron scheduling, inter-task dependencies, priority queues.

---

## Use Cases

- Long test runs (`uv run pytest` taking 5+ minutes)
- Batch file processing (convert/transform hundreds of files)
- Multi-step research (grep + analysis + report generation across large codebase)
- Bulk code modifications (update copyright year in 300+ files)

---

## Task Lifecycle

```
pending --> running --> completed (exit 0)
                   --> failed    (exit != 0)
                   --> cancelled (SIGTERM -> SIGKILL)
```

**Cancellation:** call `co_cli._shell_env.kill_process_tree(proc)` — sends SIGTERM, waits 200ms, then SIGKILL if still alive. `kill_process_tree` takes an `asyncio.subprocess.Process` object, so `TaskRunner` must keep an in-memory `_live: dict[str, asyncio.subprocess.Process]` mapping task_id → Process for the duration of execution. This map is not persisted — it is only valid for the current session. Spawn with `start_new_session=True` so the child gets its own process group; `kill_process_tree` kills via `os.killpg` on that group.

**Inactivity timeout:** if `background_task_inactivity_timeout > 0` and the process emits no output for that duration, auto-cancel it. Implemented as an `asyncio.Task` watcher created when the task starts; polls `output.log` size every 1s; resets deadline on file growth; fires cancellation when the deadline expires. Watcher is cancelled when the task completes (success, failure, or user cancel) — use `asyncio.gather(monitor_task, watcher_task, return_exceptions=True)` so watcher failure doesn't kill the monitor. Default: 0 (disabled). Prevents zombie tasks that stall silently.

**Graceful shutdown:** on co-cli exit (`TaskRunner.shutdown()`), kill all running tasks in parallel (not sequentially) using `asyncio.gather()`. Each task gets the standard `kill_process_tree()` call (SIGTERM → 200ms → SIGKILL). The total shutdown deadline is 5s — after which any tasks still alive are logged as unrecoverable and left to OS cleanup (zombies are reaped on parent exit). Set final status to `cancelled` and write `result.json` for each. Called from `main.py` cleanup path via `try/finally` and SIGINT handler.

---

## Storage

**Location**: `.co-cli/tasks/<task_id>/`

```
.co-cli/tasks/task_20260209_143022_pytest/
  metadata.json   -- task_id, status, command, cwd, pid, exit_code, timestamps, approval record, span_id, is_binary
  output.log      -- combined stdout/stderr stream
  result.json     -- written on completion: exit_code, duration, summary
```

**Task ID format**: `task_YYYYMMDD_HHMMSS_<command_name>` where `command_name` is the basename of the first word of the command, truncated to 32 chars, with unsafe filesystem chars (`/`, `\`, `*`, `?`) replaced by underscores. Examples: `uv run pytest` → `uv`, `grep -r pattern` → `grep`.

**Cleanup**: completed/failed/cancelled tasks deleted after retention period (default 7 days). Scan on startup. Actively running tasks are never deleted by cleanup; orphaned `running` entries are recovered separately (see below).

**Orphan recovery**: `TaskRunner` is a singleton — only one instance per co-cli session. On init, any task whose `metadata.json` shows `status=running` is a crash orphan (process no longer exists; not in `_live`). Mark it `failed`, write `result.json: {"exit_code": -1, "duration": null, "summary": "[crash orphan recovered at startup]"}`. Log a warning per orphan so users know a task was lost. Runs before any new tasks start.

---

## Slash Commands

Add to `co_cli/_commands.py` using the existing `SlashCommand` / `COMMANDS` registry:

| Command | Description |
|---------|-------------|
| `/background <cmd>` | Run command in background (approval prompt if destructive); immediately prints `[task_id] started` and returns to prompt |
| `/tasks [status]` | List tasks, optionally filtered by status |
| `/status <id>` | Show task metadata + last 20 lines of output. `/status` with no arg retains existing system health behavior (`_cmd_status`); with an `<id>` arg routes to task lookup. Branch on `args.strip()` in the handler. |
| `/cancel <id>` | Cancel running task |

`CommandContext` already has `ctx.deps` — add `task_runner: TaskRunner | None = None` to `CoDeps` (flat field, not a config object) so handlers can access it via `ctx.deps.task_runner`.

---

## Agent Tools

File: `co_cli/tools/task_control.py`

Follow the existing tool pattern: `RunContext[CoDeps]`, return `dict[str, Any]` with `display` field.

| Tool | Approval | Description |
|------|----------|-------------|
| `start_background_task(ctx, command, description, working_directory?)` | Yes (`requires_approval=True`) | Start command in background, return task_id |
| `check_task_status(ctx, task_id, tail_lines?)` | No | Return status, duration, exit_code, last N lines of output (default 20); returns `[binary output — N bytes]` if `is_binary` |
| `cancel_background_task(ctx, task_id)` | No | Cancel running task |
| `list_background_tasks(ctx, status_filter?)` | No | List tasks with optional status filter |

Access runner via `ctx.deps.task_runner`. All tools return `dict` with `display` field per project convention. Access runner via `ctx.deps.task_runner`. Raise `ModelRetry` if spawn fails (command not found, permission denied). Return `display='Task already completed'` if cancel is called on a finished task. Return `display='[output log not found]'` if `output.log` is missing.

**Return schemas:**
- `start_background_task` → `{display, task_id, status}`
- `check_task_status` → `{display, task_id, status, duration, exit_code, output_lines, is_binary}`
- `cancel_background_task` → `{display, task_id, status}`
- `list_background_tasks` → `{display, tasks: list[{task_id, status, command, started_at}], count}`

---

## Approval Inheritance

All approval decisions happen before task starts — background tasks cannot prompt mid-execution (no interactive stdin). The approval gate is at task creation, not execution. Same safety rules as the shell tool (`co_cli/tools/shell.py`). Approval record stored in `metadata.json` for audit trail.

---

## Core Processing Logic

**Task start:**
- Generate task_id from timestamp + command name
- Write `metadata.json` with `status=pending`
- If `len(_live) < max_concurrent`: spawn via `asyncio.create_subprocess_exec` with `start_new_session=True`; else queue
- Redirect stdout+stderr to `output.log` via open fd (not PIPE) — writes directly to disk, avoids Python buffer backpressure
- Store `proc` in `_live[task_id]`
- Update metadata: `status=running`, `pid`, `started_at`
- `start_background_task` tool creates a `background_task_execute` OTel span (inside the tool's `RunContext`); `span_id` stored in `metadata.json` for UI linking via `co traces --span <id>`; the subprocess does not inherit the span context (separate process)
- If inactivity timeout enabled: spawn asyncio watcher that polls `output.log` size; resets deadline on growth; fires cancellation on expiry
- Sniff first 4096 bytes of `output.log` after first write; if contains null byte (`\x00`) or >30% non-printable chars, set `is_binary=true` in `metadata.json`; `check_task_status` then returns `[binary output — NNN bytes]` instead of tail lines

**Task monitor:**
- `await proc.wait()`
- Remove from `_live`
- Update metadata: `status=completed/failed`, `exit_code`, `completed_at`
- Write `result.json`: `exit_code`, `duration`, `summary` (last 10 lines of `output.log` — not LLM-generated)
- Start next pending task if any

**Startup (`TaskRunner.__init__`):**
- Run retention cleanup first (delete stale completed/failed/cancelled dirs)
- Scan remaining task dirs: any `status=running` entry is an orphan — mark `failed`, `exit_code=-1`, write `result.json`
- `TaskRunner` must be fully initialized before `main.py` registers slash command handlers that reference it

---

## OTel Integration

Background tasks create a `background_task_execute` span linked to the originating `chat_turn` span. Span ID stored in `metadata.json`. Attributes: `task.id`, `task.command`, `task.status`, `task.pid`, `task.exit_code`. User can view trace via `co traces --span <id>`.

---

## Config

Add to `co_cli/config.py`:

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to keep completed task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Clean up old tasks on startup |
| `background_task_inactivity_timeout` | `CO_BACKGROUND_TASK_INACTIVITY_TIMEOUT` | `0` | Auto-cancel task if no output for N seconds; 0 = disabled |

**TaskRunner lifecycle:** `TaskRunner` is created once in `main.py` before `chat_loop()` starts, stored as a module-level variable, and passed to `create_deps()` as an argument. On clean exit and on SIGINT, `main.py`'s `try/finally` block calls `task_runner.shutdown()`. Slash command handlers and tools access it via `ctx.deps.task_runner`.

Add to `co_cli/deps.py`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `task_runner` | `Any \| None` | `None` | `TaskRunner` singleton; created in `main.py`, injected here, accessed by tools and slash commands |

---

## Success Criteria

- [ ] `TaskStorage`: create, update, read, list, delete, cleanup old tasks
- [ ] `TaskRunner`: spawn via asyncio, capture output, track status transitions, maintain `_live` dict
- [ ] Cancellation: `kill_process_tree()` called on the live `Process` object (SIGTERM → 200ms → SIGKILL via process group)
- [ ] Inactivity timeout: auto-cancel stalled tasks when configured
- [ ] Graceful shutdown: `TaskRunner.shutdown()` waits up to 5s for clean exits, then force-kills survivors
- [ ] Orphan recovery: tasks with `status=running` at startup with no live process marked `failed`
- [ ] Concurrency limit enforced (queue excess tasks as pending)
- [ ] Slash commands: `/background`, `/tasks`, `/status <id>`, `/cancel` integrated in `_commands.py`; `/status` with no arg retains system health behavior; `/status <id>` routes to task lookup (branch on `args.strip()` in `_cmd_status`)
- [ ] Agent tools: all four tools registered in `agent.py`, return `dict` with `display` field
- [ ] Binary output: detection on first 4KB, safe display in `check_task_status`
- [ ] Approval: pre-execution gate using same rules as shell tool
- [ ] OTel: spans created and linked for background tasks
- [ ] Cleanup: old tasks purged on startup per retention policy
- [ ] Tests: storage, runner, slash commands, tools, OTel integration — all functional (no mocks); spawn real processes (e.g. `sleep 1`) to verify kill behavior; full suite target <10s

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `co_cli/background.py` | Create | `TaskStatus` enum, `TaskMetadata`, `TaskStorage`, `TaskRunner` |
| `co_cli/tools/task_control.py` | Create | Agent tools: `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks` |
| `co_cli/agent.py` | Modify | Import and register task_control tools |
| `co_cli/config.py` | Modify | Add background settings fields |
| `co_cli/deps.py` | Modify | Add `task_runner: Any | None = None` field |
| `co_cli/main.py` | Modify | Initialize `TaskRunner`, inject into `CoDeps`, register slash commands |
| `co_cli/_commands.py` | Modify | Add `/background`, `/tasks`, `/cancel` handlers; update `/status` to branch on arg |
| `tests/test_background.py` | Create | Functional tests for storage, runner, commands, tools |

---

## Related Documents

- `DESIGN-core.md` — slash commands, `CoDeps`, orchestration
- `DESIGN-logging-and-tracking.md` — telemetry integration
- `DESIGN-tools.md` — shell tool approval logic (reused for background tasks)
- `TODO-subagent-delegation.md` — next layer: AI agent delegation (implement after this)
