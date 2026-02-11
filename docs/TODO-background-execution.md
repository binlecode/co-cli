# TODO: Background Execution (Phase 2c)

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

Cancellation: send SIGTERM, wait 5s grace period, then SIGKILL if still alive. Use process group (`start_new_session=True`) to kill child processes.

---

## Storage

**Location**: `.co-cli/tasks/<task_id>/`

```
.co-cli/tasks/task_20260209_143022_pytest/
  metadata.json   -- task_id, status, command, cwd, pid, exit_code, timestamps, approval record, span_id
  output.log      -- combined stdout/stderr stream
  result.json     -- written on completion: exit_code, duration, summary
```

**Task ID format**: `task_YYYYMMDD_HHMMSS_<command_name>`

**Cleanup**: completed/failed/cancelled tasks deleted after retention period (default 7 days). Scan on startup. Running tasks never deleted.

---

## Slash Commands

| Command | Description |
|---------|-------------|
| `/background <cmd>` | Run command in background (approval prompt if destructive) |
| `/tasks [status]` | List tasks, optionally filtered by status |
| `/status <id>` | Show task metadata + last 20 lines of output |
| `/cancel <id>` | Cancel running task |

---

## Agent Tools

File: `co_cli/tools/task_control.py`

| Tool | Approval | Description |
|------|----------|-------------|
| `start_background_task(command, description, working_directory?)` | Yes | Start command in background, return task_id |
| `check_task_status(task_id, include_output?)` | No | Return status, duration, exit_code, recent output |
| `cancel_background_task(task_id)` | No | Cancel running task |
| `list_background_tasks(status_filter?)` | No | List tasks with optional status filter |

All tools return `dict` with `display` field per project convention. Access runner via `ctx.deps.task_runner`.

---

## Approval Inheritance

All approval decisions happen before task starts -- background tasks cannot prompt mid-execution (no interactive stdin). The approval gate is at task creation, not execution. Same safety rules as interactive shell tool. Approval record stored in `metadata.json` for audit trail.

---

## Core Processing Logic

Task start:
- generate task_id from timestamp + command name
- write metadata.json with status=pending
- if running_count < max_concurrent: spawn via asyncio.create_subprocess_exec, else queue
- redirect stdout/stderr to output.log
- update metadata: status=running, pid, started_at
- create OTel span, record span_id

Task monitor:
- await process.wait()
- update metadata: status=completed/failed, exit_code, completed_at
- write result.json
- remove from running set, start next pending if any

---

## OTel Integration

Background tasks create a `background_task_execute` span linked to the originating `chat_turn` span. Span ID stored in `metadata.json`. Attributes: `task.id`, `task.command`, `task.status`, `task.pid`, `task.exit_code`. User can view trace via `co traces --span <id>`.

---

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `background_max_concurrent` | `CO_BACKGROUND_MAX_CONCURRENT` | `5` | Max concurrent background tasks |
| `background_task_retention_days` | `CO_BACKGROUND_TASK_RETENTION_DAYS` | `7` | Days to keep completed task data |
| `background_auto_cleanup` | `CO_BACKGROUND_AUTO_CLEANUP` | `true` | Clean up old tasks on startup |

---

## Success Criteria

- [ ] `TaskStorage`: create, update, read, list, delete, cleanup old tasks
- [ ] `TaskRunner`: spawn via asyncio, capture output, track status transitions
- [ ] Cancellation: SIGTERM with SIGKILL fallback, process group kill
- [ ] Concurrency limit enforced (queue excess tasks as pending)
- [ ] Slash commands: `/background`, `/tasks`, `/status`, `/cancel` integrated in chat loop
- [ ] Agent tools: all four tools registered, return `dict` with `display` field
- [ ] Approval: pre-execution gate using same rules as shell tool
- [ ] OTel: spans created and linked for background tasks
- [ ] Cleanup: old tasks purged on startup per retention policy
- [ ] Tests: storage, runner, slash commands, tools, OTel integration

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `co_cli/background.py` | Create | `TaskStatus` enum, `TaskMetadata`, `TaskStorage`, `TaskRunner` |
| `co_cli/tools/task_control.py` | Create | Agent tools for background task control |
| `co_cli/agent.py` | Modify | Import task_control tools, add `task_runner` fields to `CoDeps` |
| `co_cli/config.py` | Modify | Add background settings fields |
| `co_cli/main.py` | Modify | Add slash command handlers, initialize `TaskRunner` |
| `tests/test_background.py` | Create | Functional tests for storage, runner, commands, tools |

---

## Related Documents

- `DESIGN-01-agent-chat-loop.md` -- slash commands, `CoDeps`, orchestration
- `DESIGN-05-otel-logging.md` -- telemetry integration
- `DESIGN-09-tool-shell.md` -- shell tool approval logic (reused for background tasks)
