# TODO: Daemon Utility 3 — Generalized Async Poller & Watchdog

**Status:** Planned
**Target:** An on-demand background watchdog that polls a generic condition and notifies the user upon completion, freeing up the interactive REPL.
**Pydantic-AI Patterns:** Strict condition-evaluation prompts, background loop iteration, headless tool execution.

## 1. Context & Motivation
Users often wait for long-running processes (a data download, an API rate limit to reset, an email reply, a build to pass) and manually hit "Refresh" or re-run a bash command.
The `co daemon` can take over this polling chore. The user explicitly requests `/daemon watch "until localhost:8080 returns 200"`. The daemon checks the condition periodically and alerts the user when it's met.

## 2. Dev Implementation Details

### 2.1 The Agent Tool
Create `daemon_watch(ctx: RunContext[CoDeps], condition_prompt: str, check_interval_minutes: int)` in `co_cli/tools/daemon_control.py`.
- **Database Insert:** 
  The tool inserts a row into `co-cli.db` table `daemon_jobs`:
  ```sql
  INSERT INTO daemon_jobs (id, prompt, scheduled_for_epoch, status, type, interval_seconds)
  VALUES (?, ?, ?, 'pending', 'watchdog', ?);
  ```
- **Returns:** A `ToolResult` like "Watchdog started: 'until localhost:8080 returns 200' every 5 minutes".

### 2.2 Execution Logic (`co_cli/daemon/jobs/async_poller.py`)
In the `co daemon` polling loop (`co_cli/daemon/_worker.py`), select jobs where `type='watchdog'` and `status='pending'` and `scheduled_for_epoch <= ?`.
- **Pydantic-AI System Prompt Override:** 
  The daemon executes the `condition_prompt` using the headless agent runner, but the prompt must be incredibly strict:
  *"You are a read-only watchdog evaluating a condition. Use your available read-only tools (like Bash or Gmail) to check the state. Reply ONLY with the exact string 'CONDITION_MET' or 'CONDITION_NOT_MET'. Do not provide conversational filler or explanations."*
- **Execution:** 
  ```python
  deps = CoDeps(...) 
  deps.runtime.is_headless = True
  result = await agent.run(watchdog_prompt, deps=deps)
  ```
- **Evaluation:** 
  - If `result.data.strip() == "CONDITION_MET"`: 
    Update `daemon_jobs`: `status='completed'`, `completed_at=now()`.
  - If `result.data.strip() == "CONDITION_NOT_MET"`: 
    Update `daemon_jobs`: `scheduled_for_epoch = now() + (interval_seconds * 60)`. Keep `status='pending'`. The daemon loop will check it again next cycle.

### 2.3 User Notification
When the condition is met, the user needs an alert.
- **Status Integration:** Update `co_cli/bootstrap/_render_status.py`. In `get_status()`, query `daemon_jobs` for any row where `status='completed'` and `type='watchdog'` and `acknowledged=0`.
- **Banner Display:** The interactive terminal banner will display a green alert:
  `[Daemon] Watchdog met condition: "localhost:8080 returns 200"`
- **Acknowledge Tool:** Use `/daemon clear` to mark `acknowledged=1`.

### 2.4 Guardrails & Security
- **Security Rule:** Watchdog conditions cannot mutate state. If `deps.runtime.is_headless == True` and the watchdog attempts to invoke a mutating tool, `_tool_approvals.py` MUST intercept it and return `ModelRetry("Headless daemon cannot execute mutating tools.")`.
- **Test Mandate:** Write `tests/test_daemon_watchdog.py`. Create a mock server that returns 404. Schedule watchdog. Assert status remains `pending`. Update mock server to return 200. Schedule watchdog again. Assert status updates to `completed`.