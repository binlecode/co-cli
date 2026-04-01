# TODO: Daemon Utility 2 — Deferred Task Execution

**Status:** Planned
**Target:** A user-facing tool to explicitly schedule an action to execute hours or days from now, without leaving the terminal open.
**Pydantic-AI Patterns:** Tool invocation, headless execution queue, delayed job processing.

## 1. Context & Motivation
Users often need `co` to perform an action at a specific time in the future ("Draft a reply to Alice's email tomorrow at 9am" or "Remind me to update the sprint board in 3 hours"). In an ephemeral CLI environment (`co chat`), an `asyncio.sleep()` inside the interactive session will die if the laptop is closed. 
This requires an explicit, durable scheduling mechanism tied to the `co daemon` polling loop.

## 2. Dev Implementation Details

### 2.1 The Agent Tool
Create an `@agent.tool` in `co_cli/tools/daemon_control.py` named `schedule_deferred_task(ctx: RunContext[CoDeps], prompt: str, scheduled_for_epoch: float)`.
- **Logic:** The interactive agent receives a prompt like "Remind me at 5pm to...". The agent parses the time, converts to `time.time() + offset`, and invokes the tool.
- **Database Insert:** 
  The tool inserts a row into `co-cli.db` table `daemon_jobs`:
  ```sql
  INSERT INTO daemon_jobs (id, prompt, scheduled_for_epoch, status, created_at)
  VALUES (?, ?, ?, 'pending', ?);
  ```
- **Returns:** A `ToolResult` like "Task scheduled for <time> with ID <uuid>".

### 2.2 The Daemon Worker
In the `co daemon` polling loop (`co_cli/daemon/_worker.py`):
- **Query:** `SELECT id, prompt FROM daemon_jobs WHERE status='pending' AND scheduled_for_epoch <= ?` (passing `time.time()`).
- **Pydantic-AI Execution:**
  ```python
  from co_cli.deps import CoDeps

  # Initialize headless runtime dependencies
  deps = CoDeps(...) 
  deps.runtime.is_headless = True
  
  # Run standard agent (no streaming UI, no prompt_toolkit)
  result = await agent.run(job.prompt, deps=deps)
  ```
- **Store Result:** 
  Capture `result.data` (the agent's text response).
  Update SQLite: `UPDATE daemon_jobs SET status='completed', result_summary=?, completed_at=? WHERE id=?`.

### 2.3 User Notification
When the deferred task completes, the user needs to see the result next time they open their terminal.
- **Status Integration:** Update `co_cli/bootstrap/_render_status.py`. In `get_status()`, query `daemon_jobs` for any row where `status='completed'` and `acknowledged=0`.
- **Banner Display:** The interactive terminal banner will display:
  `[Daemon] Task completed: "Draft a reply to Alice's email"`
  `<result_summary excerpt>`
- **Acknowledge Tool:** Add a tool `/daemon clear` (or `dismiss_daemon_notification`) to update the row to `acknowledged=1`.

### 2.4 Guardrails & Security
- **Security Rule:** If `deps.runtime.is_headless == True` and the deferred prompt attempts to invoke a tool with `requires_approval=True` (like `bash` or `edit`), `_tool_approvals.py` MUST intercept it and return `ModelRetry("Headless daemon cannot execute tools requiring approval.")`.
- **Test Mandate:** Write `tests/test_daemon_deferred.py`. Insert a deferred job trying to run `rm -rf /tmp/test`, execute the daemon polling loop, and assert the job `status='failed'` or the response clearly indicates permission denied.