# TODO: 24x7 Background Daemon

**Status:** Planned
**Target:** A rock-solid, 24x7 background execution engine for `co-cli`.
**Pydantic-AI Patterns:** Headless `RunContext`, state-driven tool gating, durable SQLite IPC.

## 1. Context & Motivation

`co-cli` is currently an interactive, ephemeral CLI (`uv run co chat`). Tasks running in `_background.py` terminate when the user closes the REPL. 
To fill the gap for true 24x7 background execution (e.g., checking logs overnight, periodic health checks, scheduled digests), `co` needs a persistent daemon. 

Crucially, **we will not over-engineer an internal cron framework or pull in heavyweight message brokers.** 
We will use the existing robust SQLite WAL infrastructure for IPC (Inter-Process Communication) and rely on the host OS (`launchd` on macOS, `systemd` on Linux) to keep the daemon process alive "like a rock" across crashes and reboots.

## 2. Architecture & Design

The system consists of three planes:
1. **Control Plane (`co chat`):** The ephemeral interactive REPL. The user (or the agent) inserts jobs into a SQLite queue.
2. **Data Plane (SQLite WAL):** A durable queue table safely handling concurrent reads/writes between the chat process and the daemon process.
3. **Execution Plane (`co daemon`):** A headless worker loop managed by the OS that polls the SQLite queue and executes tasks.

### Pydantic-AI & Co-CLI Idiom Alignment
* **Dependency Injection:** The daemon will construct a `CoDeps` instance but set a new flag: `ctx.deps.runtime.is_headless = True`.
* **Security & Approvals:** Background daemons cannot prompt the user for `[y/N]` tool approvals. In `_approval.py` or `_orchestrate.py`, if `is_headless` is true and a tool has `requires_approval=True`, it must immediately fail with a `ToolResult` (or `ModelRetry`) explaining that mutating actions are forbidden in the background unless pre-approved.
* **Execution:** Instead of the interactive `prompt_toolkit` loop, the daemon will use a stripped-down headless invocation (e.g., `await agent.run(prompt, deps=deps)`).

---

## 3. Implementation Plan

### Step 1: Database & Data Access Layer
- [ ] Create a `daemon_jobs` table in SQLite (WAL mode).
  ```sql
  CREATE TABLE IF NOT EXISTS daemon_jobs (
      id TEXT PRIMARY KEY,
      prompt TEXT NOT NULL,
      status TEXT DEFAULT 'pending', -- pending, running, completed, failed
      created_at REAL NOT NULL,
      scheduled_for REAL NOT NULL,   -- Epoch timestamp
      result_summary TEXT,           -- Agent's final response
      error_msg TEXT
  );
  ```
- [ ] Implement a repository class (`DaemonQueue`) with methods: `enqueue()`, `dequeue_next()`, `mark_completed()`, `mark_failed()`, `list_jobs()`.
- [ ] Add `DaemonQueue` to `CoServices` in `co_cli/deps.py`.

### Step 2: Headless Security & Runtime State
- [ ] Add `is_headless: bool = False` to `CoRuntimeState` in `co_cli/deps.py`.
- [ ] Modify `co_cli/tools/_tool_approvals.py` (or where approval logic resides) to check `ctx.deps.runtime.is_headless`. 
- [ ] If headless and `requires_approval=True`, auto-reject the tool call: `return make_result("Error: Cannot execute mutating tool in headless background mode.", status="rejected")`.

### Step 3: The `co daemon` Polling Loop
- [ ] Create `co_cli/daemon/_worker.py` containing the infinite `asyncio` polling loop.
  - Polls `dequeue_next()` every `N` seconds.
  - On job acquisition: Sets status to `running`.
  - Constructs `CoDeps` with `is_headless=True`.
  - Invokes `await agent.run(job.prompt, deps=deps)`.
  - On completion, writes the final output to `result_summary` and sets status to `completed`.
- [ ] Register a new Typer command in `co_cli/main.py`:
  ```python
  @app.command()
  def daemon():
      """Start the 24x7 headless background worker."""
      # run asyncio worker loop
  ```

### Step 4: Agent Tools (The Control Plane)
- [ ] Create `co_cli/tools/daemon_control.py` with the following tools (injected with `RunContext[CoDeps]`):
  - `schedule_daemon_task(prompt: str, delay_minutes: float = 0)`: Inserts a job.
  - `list_daemon_tasks(status: str = None)`: Views pending/completed jobs.
  - `cancel_daemon_task(job_id: str)`: Removes or marks a pending job as cancelled.
- [ ] Register these tools with the main agent.

### Step 5: OS Integration ("Like a Rock")
- [ ] Add `co service install` and `co service uninstall` Typer commands to `co_cli/main.py` (or a dedicated `co_cli/_service.py`).
- [ ] **macOS:** Generate `~/Library/LaunchAgents/com.co-cli.daemon.plist` with `KeepAlive=true` and `RunAtLoad=true`. Execute `launchctl load`.
- [ ] **Linux:** Generate `~/.config/systemd/user/co-daemon.service`. Execute `systemctl --user enable --now co-daemon`.

---

## 4. Testing Strategy
* **No Mocks:** Use a real temporary SQLite database for all queue tests.
* **Timeouts:** Ensure the headless worker loop execution paths are wrapped in appropriate `asyncio.timeout()` blocks as mandated by `CLAUDE.md`.
* **Security Validation:** Write a specific test where a headless `CoDeps` attempts to call a `requires_approval=True` tool (like `Bash`) and assert it is automatically rejected without blocking/hanging.