# TODO-background-execution.md

Background task execution system for long-running operations.

---

## Executive Summary

**Goal**: Enable long-running tasks to execute in background without blocking interactive chat.

**Problem**: Operations like test runs, research tasks, large file processing, and batch operations currently block the chat loop. User cannot issue new commands until completion. For tasks taking minutes or hours, this creates poor UX and forces users to open multiple terminal sessions.

**Solution**: Async task runner that spawns background processes, tracks status in persistent storage, provides status queries and cancellation, and integrates with OpenTelemetry tracing. Tasks inherit approval decisions from interactive prompts before starting.

**Scope**:
- Task lifecycle management (start, monitor, complete, fail, cancel)
- Persistent task storage (.co-cli/tasks/)
- Slash commands for control (/background, /tasks, /status, /cancel)
- Tools for agent-driven background operations
- Approval inheritance model (ask before starting, no mid-execution prompts)
- Cleanup policy for completed tasks

**Effort**: 10-12 hours
- Storage + runner: 3 hours
- Slash commands: 2 hours
- Tools: 2 hours
- Approval inheritance: 2 hours
- Testing: 3-4 hours

**Non-goals**:
- Distributed task queue (single machine only)
- Task scheduling/cron (immediate execution only)
- Inter-task dependencies (each task independent)
- Priority queues (FIFO execution)

---

## Use Cases

### 1. Long Test Runs

**Scenario**: User asks "Run the full test suite" which takes 5 minutes.

**Current behavior**:
- Agent runs `uv run pytest`
- Chat loop blocks for 5 minutes
- User cannot ask questions or issue commands
- Terminal shows streaming test output
- User opens second terminal for other work

**With background execution**:
- Agent asks: "Test suite will take ~5 minutes. Run in background? [y/n]"
- User confirms
- Task starts with ID `task_20260209_143022_pytest`
- Agent replies: "Started task task_20260209_143022_pytest. Use /status <id> to check progress"
- Chat loop immediately ready for next input
- User asks "What's the status of my tests?"
- Agent runs `check_task_status("task_20260209_143022_pytest")` → returns "Running, 47 tests passed so far..."
- When tests complete, next agent turn includes: "Background task task_20260209_143022_pytest completed: 89 passed, 2 failed. See .co-cli/tasks/task_20260209_143022_pytest/output.log"

**Value**: Parallelism, no context switching, status queries without blocking.

### 2. Large File Processing

**Scenario**: User asks "Convert all PNG images in /assets to WebP format".

**Current behavior**:
- Agent writes conversion script
- Runs script with shell tool
- Blocks for duration (could be 10+ minutes for hundreds of files)
- No progress visibility except streaming output

**With background execution**:
- Agent: "Processing 247 PNG files. Run in background? [y/n]"
- User confirms
- Task starts
- Agent: "Started task task_20260209_143155_convert. Check /status task_20260209_143155_convert for progress"
- User continues other work
- Periodically checks: "/status task_20260209_143155_convert" → "Running: 132/247 files processed (53%)"
- On completion: "Background task completed: 247 files converted, 0 errors. Output in .co-cli/tasks/task_20260209_143155_convert/"

**Value**: Non-blocking batch operations, progress tracking, parallel work.

### 3. Research Tasks

**Scenario**: User asks "Search for all TODOs in the codebase, categorize by priority, and generate a report".

**Current behavior**:
- Agent runs multiple grep commands
- Analyzes results
- Generates report
- Entire process blocks chat (2-3 minutes)

**With background execution**:
- Agent: "Research task requires searching 1200+ files. Run in background? [y/n]"
- User confirms
- Task starts with agent prompt + task definition stored in metadata
- Agent continues in background: grep → analysis → report generation
- User works on other tasks
- On completion: "Research task completed. Report at .co-cli/tasks/task_20260209_144530_research/report.md"
- User: "Show me the report" → agent reads and displays it

**Value**: Agent autonomy for long-running research, user not blocked.

### 4. Batch Operations

**Scenario**: User asks "Update copyright year in all Python files".

**Current behavior**:
- Agent finds files with glob
- Runs sed command for each file
- Blocks for duration (could be minutes for large codebase)

**With background execution**:
- Agent: "Updating 342 files. Run in background? [y/n]"
- User confirms
- Task starts
- Agent executes batch operation in background
- User continues other work
- Checks status: "/status task_20260209_145012_copyright" → "Running: 198/342 files updated"
- On completion: "Background task completed: 342 files updated. Git diff available at .co-cli/tasks/task_20260209_145012_copyright/changes.diff"

**Value**: Large-scale changes without blocking, progress visibility.

---

## Architecture Overview

### Task Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                         Task Lifecycle                           │
└─────────────────────────────────────────────────────────────────┘

User: "Run full test suite"
         │
         ▼
    ┌────────────────────┐
    │  Agent proposes    │
    │  background task   │
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │  User approves     │◄──── Approval inheritance point
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │   PENDING          │  Metadata written to .co-cli/tasks/<id>/
    │   (task created)   │  Status: pending, command stored
    └────────┬───────────┘
             │
             ▼
    ┌────────────────────┐
    │   RUNNING          │  Process spawned (asyncio.create_subprocess_exec)
    │   (executing)      │  Status: running, PID recorded, start_time set
    │                    │  Output streaming to output.log
    └─────┬──────┬───────┘
          │      │
          │      │ User: "/cancel <id>"
          │      └──────────────┐
          │                     ▼
          │            ┌────────────────────┐
          │            │   CANCELLING       │  SIGTERM sent, 5s grace period
          │            │   (terminating)    │  If not dead, SIGKILL
          │            └────────┬───────────┘
          │                     │
          ▼                     ▼
    ┌────────────────────┐    ┌────────────────────┐
    │   COMPLETED        │    │   CANCELLED        │
    │   (exit code 0)    │    │   (killed)         │
    └────────┬───────────┘    └────────┬───────────┘
             │                         │
             │                         │
             ▼                         ▼
    ┌────────────────────┐    ┌────────────────────┐
    │   FAILED           │    │   Cleanup after    │
    │   (exit code ≠ 0)  │    │   retention period │
    └────────────────────┘    │   (7 days default) │
                               └────────────────────┘
```

### Storage Strategy

**Location**: `.co-cli/tasks/<task_id>/`

**Per-task directory**:
```
.co-cli/tasks/task_20260209_143022_pytest/
├── metadata.json      # Task metadata (see schema below)
├── output.log         # Stdout/stderr combined
├── span_id.txt        # OpenTelemetry span ID (for linking to traces)
└── result.json        # Final result (exit code, duration, summary) — written on completion
```

**metadata.json schema**:
```json
{
  "task_id": "task_20260209_143022_pytest",
  "created_at": "2026-02-09T14:30:22.123456Z",
  "started_at": "2026-02-09T14:30:22.234567Z",  // null if pending
  "completed_at": "2026-02-09T14:35:18.345678Z", // null if running/pending
  "status": "completed",  // pending|running|completed|failed|cancelled
  "command": ["uv", "run", "pytest"],
  "cwd": "/Users/binle/workspace_genai/co-cli",
  "env": {"LLM_PROVIDER": "gemini"},  // Additional env vars (merged with parent)
  "description": "Run full test suite",  // Human-readable description
  "pid": 12345,  // null if not running
  "exit_code": 0,  // null if not completed
  "span_id": "a1b2c3d4e5f6g7h8",  // OpenTelemetry span ID
  "approved_by": "user",  // "user" | "auto" (if no approval needed)
  "approved_at": "2026-02-09T14:30:21.123456Z"
}
```

**result.json schema** (written on completion):
```json
{
  "exit_code": 0,
  "duration_seconds": 296.2,
  "stdout_lines": 1247,
  "stderr_lines": 0,
  "summary": "89 tests passed, 2 failed",  // Optional: parsed from output
  "completed_at": "2026-02-09T14:35:18.345678Z"
}
```

**Cleanup policy**:
- Completed/failed/cancelled tasks retained for 7 days (configurable: `background_task_retention_days`)
- On startup, `TaskRunner` scans `.co-cli/tasks/` and deletes directories older than retention period
- Running tasks never deleted (even if started >7 days ago)

### Approval Inheritance Model

**Problem**: Background tasks cannot prompt for approval mid-execution (no interactive stdin).

**Solution**: Approval happens before task starts.

**Flow**:
1. Agent proposes background task
2. If command requires approval (destructive, side-effectful):
   - Show approval prompt: "Command `rm -rf /tmp/cache` requires approval. Start in background? [y/n]"
   - User decision recorded in `metadata.json` (`approved_by`, `approved_at`)
3. Task starts only after approval
4. No further approval prompts during execution

**Approval rules**:
- Shell commands: Check against `codex`-style safety rules (destructive patterns)
- File writes: Require approval if outside project directory
- Network requests: Auto-approve for idempotent APIs, require approval for mutations
- Tool-specific: Each tool declares if it needs approval (`requires_approval=True`)

**Security**: Background tasks inherit the approval decision but cannot bypass approval by running in background. The approval gate is at task creation, not execution.

### Integration with OpenTelemetry

**Span structure**:
```
chat_turn
  └─ background_task_start (span_id=a1b2c3d4e5f6g7h8)
       └─ background_task_execute (linked via span_id in metadata.json)
            └─ tool_shell (or other tools)
```

**Linking**:
- When task starts, create OpenTelemetry span `background_task_execute`
- Record span ID in `metadata.json` and `span_id.txt`
- When user queries task status, include span ID in response
- User can view detailed trace: `co traces --span a1b2c3d4e5f6g7h8`

**Attributes**:
- `task.id`: Task ID
- `task.command`: Command string
- `task.status`: current status
- `task.pid`: Process ID
- `task.exit_code`: Exit code (when completed)

---

## Implementation Plan

### Phase 1: Task Storage (3 hours)

**Goal**: Persistent task metadata storage and retrieval.

**Tasks**:
1. Define `TaskMetadata` dataclass in `co_cli/background.py`:
   - Fields: `task_id`, `created_at`, `started_at`, `completed_at`, `status`, `command`, `cwd`, `env`, `description`, `pid`, `exit_code`, `span_id`, `approved_by`, `approved_at`
   - Methods: `to_dict()`, `from_dict()`
   - Validation: `status` enum, timestamps as `datetime` objects

2. Implement `TaskStorage` class:
   - `create_task(task_id: str, metadata: TaskMetadata) -> Path`: Create task directory, write metadata.json
   - `update_task(task_id: str, **updates) -> None`: Update metadata fields, write to disk
   - `get_task(task_id: str) -> TaskMetadata | None`: Read metadata.json
   - `list_tasks(status: str | None = None) -> list[TaskMetadata]`: List all tasks (optionally filtered by status)
   - `delete_task(task_id: str) -> None`: Delete task directory
   - `cleanup_old_tasks(retention_days: int) -> int`: Delete completed/failed/cancelled tasks older than retention period, return count

3. Task directory structure:
   - Base path: `.co-cli/tasks/`
   - Per-task: `.co-cli/tasks/<task_id>/`
   - Files: `metadata.json`, `output.log`, `span_id.txt`, `result.json`

4. Error handling:
   - Handle missing `.co-cli/tasks/` directory (create on first use)
   - Handle corrupted metadata.json (log warning, skip task)
   - Handle concurrent writes (file locking not needed — single process)

**Test coverage**:
- Create task, read back metadata
- Update task status, verify persistence
- List tasks filtered by status
- Cleanup old tasks
- Corrupted metadata handling

### Phase 2: Async Task Runner (3 hours)

**Goal**: Execute commands in background, track status, capture output.

**Tasks**:
1. Implement `TaskRunner` class in `co_cli/background.py`:
   - `__init__(storage: TaskStorage, max_concurrent: int = 5)`: Initialize with storage backend
   - `start_task(command: list[str], cwd: str, env: dict, description: str, approved_by: str) -> str`: Create task, spawn process, return task_id
   - `get_status(task_id: str) -> TaskMetadata`: Get current status
   - `cancel_task(task_id: str) -> bool`: Send SIGTERM, wait 5s, SIGKILL if needed
   - `_monitor_task(task_id: str, process: asyncio.subprocess.Process) -> None`: Monitor process, update status, write output
   - `cleanup() -> int`: Call `storage.cleanup_old_tasks()`

2. Process spawning:
   - Use `asyncio.create_subprocess_exec()` for non-blocking execution
   - Redirect stdout/stderr to `output.log` (combined stream)
   - Record PID in metadata
   - Update status to `running`

3. Process monitoring:
   - Async loop: `await process.wait()`
   - On completion: update metadata (status, exit_code, completed_at), write result.json
   - On error: update status to `failed`, record exit code

4. Output capture:
   - Stream stdout/stderr to `output.log` in real-time
   - Use `asyncio.StreamReader` to avoid blocking
   - Flush on every line (for tail-like viewing)

5. Concurrency control:
   - Track running tasks in `_running_tasks: dict[str, asyncio.Task]`
   - If `len(_running_tasks) >= max_concurrent`, queue task (status=`pending`)
   - When task completes, start next pending task

6. Cancellation:
   - Send SIGTERM to process
   - Wait 5 seconds for graceful shutdown
   - Send SIGKILL if still running
   - Update status to `cancelled`

**Test coverage**:
- Start task, verify PID recorded
- Monitor task completion, verify exit code
- Cancel running task, verify SIGTERM → SIGKILL
- Output streaming to log file
- Concurrency limit (start 6 tasks, verify 5 running + 1 pending)

### Phase 3: Slash Commands (2 hours)

**Goal**: User-facing commands for task control.

**Commands**:

#### `/background <command>`

Run command in background.

**Usage**:
```
/background uv run pytest
/background uv run python scripts/large_batch.py
```

**Implementation**:
- Parse command string (shell-like splitting)
- Check if command requires approval (same logic as interactive shell tool)
- If approval needed, prompt user
- Create task via `TaskRunner.start_task()`
- Display: "Started background task <task_id>. Use /status <id> to check progress."

**Approval prompt**:
```
Command requires approval:
  uv run pytest --cov=co_cli

This will execute tests with coverage, modifying .coverage file.

Start in background? [y/n]
```

#### `/tasks [status]`

List background tasks.

**Usage**:
```
/tasks              # All tasks
/tasks running      # Running tasks only
/tasks completed    # Completed tasks only
```

**Implementation**:
- Call `TaskRunner.get_status()` for each task
- Display table:
  ```
  ID                               Status      Started             Duration  Description
  ──────────────────────────────── ─────────── ─────────────────── ───────── ────────────────────────
  task_20260209_143022_pytest      completed   2026-02-09 14:30:22 4m 56s    Run full test suite
  task_20260209_144530_research    running     2026-02-09 14:45:30 1m 12s    Research task: TODO analysis
  task_20260209_145012_copyright   pending     -                   -         Update copyright year
  ```

#### `/status <task_id>`

Show detailed task status.

**Usage**:
```
/status task_20260209_143022_pytest
```

**Implementation**:
- Call `TaskRunner.get_status(task_id)`
- Display metadata + last 20 lines of output.log:
  ```
  Task: task_20260209_143022_pytest
  Status: completed
  Command: uv run pytest
  Started: 2026-02-09 14:30:22
  Completed: 2026-02-09 14:35:18
  Duration: 4m 56s
  Exit code: 0

  Recent output (last 20 lines):
  ────────────────────────────────────────────────────────────
  tests/test_agent.py::test_create_agent PASSED
  tests/test_chat.py::test_chat_loop PASSED
  ...
  ====== 89 passed, 2 failed in 296.2s ======

  Full output: .co-cli/tasks/task_20260209_143022_pytest/output.log
  Trace: co traces --span a1b2c3d4e5f6g7h8
  ```

#### `/cancel <task_id>`

Cancel running task.

**Usage**:
```
/cancel task_20260209_144530_research
```

**Implementation**:
- Call `TaskRunner.cancel_task(task_id)`
- Display: "Cancelling task <task_id>..."
- Wait for cancellation to complete (SIGTERM → SIGKILL)
- Display: "Task <task_id> cancelled."

**Error handling**:
- Task not found: "Task <task_id> not found."
- Task not running: "Task <task_id> is not running (status: completed)."

**Integration**:
- Add slash command handlers in `main.py` (chat loop)
- Use `match input.split()` pattern for command parsing
- Call `TaskRunner` methods via `ctx.deps.task_runner` (add to `CoDeps`)

**Test coverage**:
- `/background` with safe command (no approval)
- `/background` with dangerous command (approval required)
- `/tasks` with no tasks
- `/tasks` with mixed statuses
- `/status` for running task
- `/status` for completed task
- `/cancel` running task

### Phase 4: Tool Integration (2 hours)

**Goal**: Agent-driven background operations via tools.

**Tools** (in `co_cli/tools/task_control.py`):

#### `start_background_task`

Start a command in background.

**Signature**:
```python
@agent.tool(requires_approval=True)
async def start_background_task(
    ctx: RunContext[CoDeps],
    command: str,
    description: str,
    working_directory: str | None = None,
) -> dict[str, Any]:
    """
    Start a long-running command in background.

    Args:
        command: Shell command to execute (e.g., "uv run pytest")
        description: Human-readable task description
        working_directory: Working directory (defaults to current)

    Returns:
        dict with "task_id", "status", "display"
    """
```

**Implementation**:
- Parse command string
- Approval check: If `requires_approval=True`, approval handled by chat loop before tool executes
- Call `ctx.deps.task_runner.start_task()`
- Return:
  ```python
  {
      "task_id": "task_20260209_143022_pytest",
      "status": "running",
      "display": "Started background task task_20260209_143022_pytest: Run full test suite\nCheck status: /status task_20260209_143022_pytest"
  }
  ```

#### `check_task_status`

Check status of background task.

**Signature**:
```python
@agent.tool()
async def check_task_status(
    ctx: RunContext[CoDeps],
    task_id: str,
    include_output: bool = True,
) -> dict[str, Any]:
    """
    Check status of a background task.

    Args:
        task_id: Task ID
        include_output: Include recent output lines (last 20)

    Returns:
        dict with "task_id", "status", "duration", "exit_code", "output", "display"
    """
```

**Implementation**:
- Call `ctx.deps.task_runner.get_status(task_id)`
- Read last 20 lines of `output.log` if `include_output=True`
- Return:
  ```python
  {
      "task_id": "task_20260209_143022_pytest",
      "status": "completed",
      "duration_seconds": 296.2,
      "exit_code": 0,
      "output": "tests/test_agent.py::test_create_agent PASSED\n...\n====== 89 passed, 2 failed in 296.2s ======",
      "display": "Task: task_20260209_143022_pytest\nStatus: completed\nDuration: 4m 56s\nExit code: 0\n\nRecent output:\n...\n\nFull output: .co-cli/tasks/task_20260209_143022_pytest/output.log"
  }
  ```

#### `cancel_background_task`

Cancel a running background task.

**Signature**:
```python
@agent.tool()
async def cancel_background_task(
    ctx: RunContext[CoDeps],
    task_id: str,
) -> dict[str, Any]:
    """
    Cancel a running background task.

    Args:
        task_id: Task ID

    Returns:
        dict with "task_id", "status", "display"
    """
```

**Implementation**:
- Call `ctx.deps.task_runner.cancel_task(task_id)`
- Return:
  ```python
  {
      "task_id": "task_20260209_143022_pytest",
      "status": "cancelled",
      "display": "Task task_20260209_143022_pytest cancelled."
  }
  ```

#### `list_background_tasks`

List all background tasks.

**Signature**:
```python
@agent.tool()
async def list_background_tasks(
    ctx: RunContext[CoDeps],
    status_filter: str | None = None,
) -> dict[str, Any]:
    """
    List all background tasks.

    Args:
        status_filter: Filter by status (pending|running|completed|failed|cancelled)

    Returns:
        dict with "tasks" (list of task summaries), "count", "display"
    """
```

**Implementation**:
- Call `ctx.deps.task_runner.list_tasks(status=status_filter)`
- Format as table
- Return:
  ```python
  {
      "tasks": [
          {"task_id": "task_20260209_143022_pytest", "status": "completed", "duration": 296.2, ...},
          ...
      ],
      "count": 3,
      "display": "ID                               Status      Started             Duration  Description\n..."
  }
  ```

**Registration**:
- Import tools in `co_cli/agent.py`
- Register with `agent.tool()` decorator (already done via decorator)

**CoDeps integration**:
- Add `task_runner: TaskRunner` field to `CoDeps` dataclass
- Initialize in `get_agent()`:
  ```python
  task_storage = TaskStorage(base_path=Path.home() / ".co-cli" / "tasks")
  task_runner = TaskRunner(storage=task_storage, max_concurrent=5)
  deps = CoDeps(..., task_runner=task_runner)
  ```

**Test coverage**:
- Start task via tool, verify task_id returned
- Check status via tool, verify metadata
- Cancel task via tool, verify cancellation
- List tasks via tool, verify filtering

### Phase 5: Testing (3-4 hours)

**Goal**: Comprehensive functional tests for all components.

**Test file**: `tests/test_background.py`

**Test cases**:

#### Storage tests

1. `test_create_task_metadata`: Create task, verify metadata.json written
2. `test_update_task_metadata`: Update status, verify persistence
3. `test_list_tasks_all`: List all tasks
4. `test_list_tasks_filtered`: List tasks by status
5. `test_delete_task`: Delete task directory
6. `test_cleanup_old_tasks`: Create old completed tasks, run cleanup, verify deletion

#### Runner tests

7. `test_start_task_simple`: Start simple command (`echo hello`), verify completion
8. `test_start_task_with_output`: Start command with output, verify output.log
9. `test_task_failure`: Start failing command, verify exit_code != 0
10. `test_cancel_running_task`: Start long command, cancel, verify SIGTERM sent
11. `test_cancel_with_sigkill`: Start command that ignores SIGTERM, verify SIGKILL
12. `test_concurrent_tasks`: Start 6 tasks with max_concurrent=5, verify 5 running + 1 pending
13. `test_monitor_task_completion`: Start task, verify status updates (pending → running → completed)

#### Slash command tests

14. `test_slash_background`: Run `/background echo hello`, verify task created
15. `test_slash_background_approval`: Run `/background rm -rf /tmp`, verify approval prompt
16. `test_slash_tasks_list`: Run `/tasks`, verify table output
17. `test_slash_status`: Run `/status <id>`, verify detailed output
18. `test_slash_cancel`: Run `/cancel <id>`, verify cancellation

#### Tool tests

19. `test_start_background_task_tool`: Call tool, verify task started
20. `test_check_task_status_tool`: Call tool, verify status returned
21. `test_cancel_background_task_tool`: Call tool, verify cancellation
22. `test_list_background_tasks_tool`: Call tool, verify task list

#### Integration tests

23. `test_agent_proposes_background`: User asks to run tests, agent proposes background execution
24. `test_agent_checks_status`: User asks "What's the status of my task?", agent calls check_task_status
25. `test_background_task_with_otel`: Start task, verify span created and linked

**Test infrastructure**:
- Use `pytest-asyncio` for async tests
- Use `tmp_path` fixture for isolated task directories
- Mock approval prompts for automated testing
- Use short-running commands (`sleep 1`, `echo hello`) to avoid slow tests

**Test execution**:
```bash
uv run pytest tests/test_background.py -v
```

---

## Code Specifications

### Module: `co_cli/background.py`

**Purpose**: Task storage, lifecycle management, async execution.

**Classes**:

#### `TaskStatus` (enum)

```python
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

#### `TaskMetadata` (dataclass)

```python
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

@dataclass
class TaskMetadata:
    task_id: str
    created_at: datetime
    status: TaskStatus
    command: list[str]
    cwd: str
    description: str
    approved_by: str  # "user" | "auto"
    approved_at: datetime
    env: dict[str, str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pid: int | None = None
    exit_code: int | None = None
    span_id: str | None = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        # Implementation: convert datetime to ISO strings, enum to value

    @classmethod
    def from_dict(cls, data: dict) -> "TaskMetadata":
        """Load from dict."""
        # Implementation: parse ISO strings to datetime, string to enum
```

#### `TaskStorage` (class)

```python
class TaskStorage:
    """Persistent storage for task metadata."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.base_path.mkdir(parents=True, exist_ok=True)

    def create_task(self, task_id: str, metadata: TaskMetadata) -> Path:
        """Create task directory and write metadata."""
        # Implementation:
        # - Create .co-cli/tasks/<task_id>/
        # - Write metadata.json
        # - Return task directory path

    def update_task(self, task_id: str, **updates) -> None:
        """Update task metadata fields."""
        # Implementation:
        # - Read metadata.json
        # - Update fields from **updates
        # - Write back to disk

    def get_task(self, task_id: str) -> TaskMetadata | None:
        """Read task metadata."""
        # Implementation:
        # - Read metadata.json
        # - Return TaskMetadata or None if not found

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskMetadata]:
        """List all tasks, optionally filtered by status."""
        # Implementation:
        # - Scan .co-cli/tasks/
        # - Read metadata.json for each task
        # - Filter by status if provided
        # - Sort by created_at (newest first)

    def delete_task(self, task_id: str) -> None:
        """Delete task directory."""
        # Implementation:
        # - Remove .co-cli/tasks/<task_id>/

    def cleanup_old_tasks(self, retention_days: int) -> int:
        """Delete completed/failed/cancelled tasks older than retention period."""
        # Implementation:
        # - List all tasks
        # - Filter: status in [completed, failed, cancelled] AND age > retention_days
        # - Delete task directories
        # - Return count of deleted tasks
```

#### `TaskRunner` (class)

```python
import asyncio
import signal
from datetime import datetime

class TaskRunner:
    """Async task executor and monitor."""

    def __init__(self, storage: TaskStorage, max_concurrent: int = 5):
        self.storage = storage
        self.max_concurrent = max_concurrent
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._pending_queue: list[str] = []

    async def start_task(
        self,
        command: list[str],
        cwd: str,
        description: str,
        approved_by: str,
        env: dict[str, str] | None = None,
    ) -> str:
        """Start a background task."""
        # Implementation:
        # - Generate task_id: f"task_{datetime.now():%Y%m%d_%H%M%S}_{command[0]}"
        # - Create TaskMetadata with status=PENDING
        # - Call storage.create_task()
        # - If len(_running_tasks) < max_concurrent:
        #     - Spawn process via _spawn_task()
        # - Else:
        #     - Add to _pending_queue
        # - Return task_id

    async def _spawn_task(self, task_id: str) -> None:
        """Spawn process for task."""
        # Implementation:
        # - Read task metadata
        # - Create output.log file
        # - Spawn: asyncio.create_subprocess_exec(
        #     *command,
        #     stdout=output_log,
        #     stderr=asyncio.subprocess.STDOUT,
        #     cwd=cwd,
        #     env={**os.environ, **env}
        # )
        # - Update metadata: status=RUNNING, pid=process.pid, started_at=now
        # - Create OpenTelemetry span, record span_id
        # - Start monitoring: asyncio.create_task(_monitor_task(task_id, process))

    async def _monitor_task(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Monitor process until completion."""
        # Implementation:
        # - await process.wait()
        # - exit_code = process.returncode
        # - Update metadata: status=COMPLETED/FAILED, exit_code, completed_at=now
        # - Write result.json
        # - Remove from _running_tasks
        # - Start next pending task if any

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        # Implementation:
        # - Get task metadata
        # - If status != RUNNING: return False
        # - Get PID from metadata
        # - Send SIGTERM
        # - Wait 5 seconds
        # - If still alive: send SIGKILL
        # - Update metadata: status=CANCELLED
        # - Return True

    def get_status(self, task_id: str) -> TaskMetadata:
        """Get current task status."""
        # Implementation:
        # - Call storage.get_task(task_id)
        # - Return metadata

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskMetadata]:
        """List all tasks."""
        # Implementation:
        # - Call storage.list_tasks(status)

    async def cleanup(self, retention_days: int) -> int:
        """Clean up old tasks."""
        # Implementation:
        # - Call storage.cleanup_old_tasks(retention_days)
```

**Helper functions**:

```python
def generate_task_id(command: list[str]) -> str:
    """Generate unique task ID."""
    # Format: task_YYYYMMDD_HHMMSS_<command_name>
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    command_name = Path(command[0]).stem  # Extract command name
    return f"task_{timestamp}_{command_name}"

def parse_command(command_str: str) -> list[str]:
    """Parse command string into argv list."""
    # Use shlex.split() for shell-like parsing
    import shlex
    return shlex.split(command_str)
```

### Slash Command Handlers in `main.py`

**Location**: Inside `async def chat()` function.

**Pattern**:
```python
# Inside chat loop, after reading user input
if input_text.startswith("/"):
    parts = input_text.split(maxsplit=1)
    command = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    match command:
        case "/background":
            await handle_background(args, ctx)
            continue
        case "/tasks":
            await handle_tasks(args, ctx)
            continue
        case "/status":
            await handle_status(args, ctx)
            continue
        case "/cancel":
            await handle_cancel(args, ctx)
            continue
        # ... other commands
```

**Handler functions**:

```python
async def handle_background(args: str, ctx: CoDeps) -> None:
    """Handle /background command."""
    # Pseudocode:
    # - Parse command string
    # - Check approval requirements
    # - If needs approval: prompt user
    # - Call ctx.task_runner.start_task()
    # - Display: "Started background task <task_id>"

async def handle_tasks(args: str, ctx: CoDeps) -> None:
    """Handle /tasks command."""
    # Pseudocode:
    # - Parse status filter (if any)
    # - Call ctx.task_runner.list_tasks(status_filter)
    # - Format as table
    # - Display via console.print()

async def handle_status(args: str, ctx: CoDeps) -> None:
    """Handle /status command."""
    # Pseudocode:
    # - Parse task_id from args
    # - Call ctx.task_runner.get_status(task_id)
    # - Read last 20 lines from output.log
    # - Display metadata + output

async def handle_cancel(args: str, ctx: CoDeps) -> None:
    """Handle /cancel command."""
    # Pseudocode:
    # - Parse task_id from args
    # - Call ctx.task_runner.cancel_task(task_id)
    # - Display: "Cancelling task..." → "Task cancelled."
```

### Tools in `co_cli/tools/task_control.py`

**Structure**:
```python
"""Background task control tools."""

from pydantic_ai import RunContext
from co_cli.agent import CoDeps, agent

@agent.tool(requires_approval=True)
async def start_background_task(
    ctx: RunContext[CoDeps],
    command: str,
    description: str,
    working_directory: str | None = None,
) -> dict[str, Any]:
    """Start a long-running command in background."""
    # Implementation (see Phase 4)

@agent.tool()
async def check_task_status(
    ctx: RunContext[CoDeps],
    task_id: str,
    include_output: bool = True,
) -> dict[str, Any]:
    """Check status of a background task."""
    # Implementation (see Phase 4)

@agent.tool()
async def cancel_background_task(
    ctx: RunContext[CoDeps],
    task_id: str,
) -> dict[str, Any]:
    """Cancel a running background task."""
    # Implementation (see Phase 4)

@agent.tool()
async def list_background_tasks(
    ctx: RunContext[CoDeps],
    status_filter: str | None = None,
) -> dict[str, Any]:
    """List all background tasks."""
    # Implementation (see Phase 4)
```

**Registration**: Tools auto-register via `@agent.tool()` decorator. Import in `co_cli/agent.py` to ensure registration:

```python
# In co_cli/agent.py
from co_cli.tools import task_control  # Import to trigger registration
```

### CoDeps Integration

**Location**: `co_cli/agent.py`

**Changes**:

1. Add `task_runner` field to `CoDeps`:
   ```python
   @dataclass
   class CoDeps:
       # ... existing fields
       task_runner: TaskRunner
   ```

2. Initialize in `get_agent()`:
   ```python
   def get_agent(...) -> Agent:
       # ... existing setup

       # Background task runner
       task_storage = TaskStorage(base_path=Path.home() / ".co-cli" / "tasks")
       task_runner = TaskRunner(
           storage=task_storage,
           max_concurrent=settings.background_max_concurrent,  # Default: 5
       )

       deps = CoDeps(
           # ... existing fields
           task_runner=task_runner,
       )

       # ... rest of function
   ```

### Settings

**Location**: `co_cli/settings.py`

**New fields**:
```python
@dataclass
class Settings:
    # ... existing fields

    # Background task settings
    background_max_concurrent: int = 5
    """Maximum number of concurrent background tasks."""

    background_task_retention_days: int = 7
    """How long to keep completed/failed/cancelled task data (days)."""

    background_auto_cleanup: bool = True
    """Automatically clean up old tasks on startup."""
```

**Environment variables**:
- `CO_BACKGROUND_MAX_CONCURRENT`: Override max concurrent tasks
- `CO_BACKGROUND_TASK_RETENTION_DAYS`: Override retention period
- `CO_BACKGROUND_AUTO_CLEANUP`: Set to `false` to disable auto cleanup

---

## Approval Inheritance Design

### Problem Statement

Background tasks cannot prompt for approval mid-execution because:
1. No interactive stdin (process runs detached from terminal)
2. User may not be at terminal when approval needed
3. Blocking on approval defeats purpose of background execution

**Security risk**: If tasks bypass approval by running in background, destructive commands could execute without user consent.

### Solution: Pre-execution Approval

**Principle**: All approval decisions happen before task starts.

**Flow**:

1. **User requests background task**: "/background rm -rf /tmp/cache"

2. **Agent evaluates command safety**:
   - Parse command: `["rm", "-rf", "/tmp/cache"]`
   - Check against safety rules (same logic as interactive shell tool):
     - Destructive patterns: `rm -rf`, `rm -r`, `dd`, `mkfs`, `fdisk`
     - File writes outside project directory
     - Network mutations (POST/PUT/DELETE)
   - Result: Requires approval

3. **Pre-execution approval prompt**:
   ```
   Command requires approval:
     rm -rf /tmp/cache

   This command will:
     - Delete directory /tmp/cache recursively
     - Cannot be undone

   Start in background? [y/n]
   ```

4. **User decision**:
   - User types `y`: Approval granted → record in metadata (`approved_by="user"`, `approved_at=<timestamp>`)
   - User types `n`: Approval denied → task not created
   - User cancels (Ctrl-C): Abort

5. **Task execution**:
   - Task starts with approval already granted
   - No further prompts during execution
   - Metadata includes approval record for audit trail

6. **Audit trail**:
   - `metadata.json` includes `approved_by` and `approved_at`
   - OpenTelemetry span includes approval decision
   - Output log includes header:
     ```
     # Background task: task_20260209_143022_rm
     # Command: rm -rf /tmp/cache
     # Approved by: user at 2026-02-09T14:30:21Z
     # Started: 2026-02-09T14:30:22Z
     ```

### Approval Rules

**Safe commands** (auto-approve):
- Read-only operations: `ls`, `cat`, `grep`, `find`, `stat`
- Idempotent queries: `git status`, `git log`, `git diff`
- Test runs: `pytest`, `uv run pytest` (no destructive side effects)
- Safe scripts inside project directory

**Requires approval**:
- File deletion: `rm`, `unlink`
- File writes outside project directory
- Destructive operations: `dd`, `mkfs`, `fdisk`, `parted`
- Network mutations: `curl -X POST`, `curl -X DELETE`
- Shell scripts with unknown content

**Tool-level approval**:
- Tools declare `requires_approval=True` in decorator
- Approval logic in chat loop checks tool metadata before execution
- Background task inherits tool's approval requirement

### Edge Cases

**1. Command changes after approval**

**Scenario**: User approves "pytest", but command stored in metadata is "pytest --cov=co_cli".

**Prevention**: Show exact command in approval prompt, including all arguments. Store command in metadata before prompting.

**2. Approval for multi-step tasks**

**Scenario**: Agent wants to run 3 commands in sequence in background.

**Solution**: Approve entire sequence, not individual commands. Show all commands in approval prompt:
```
This task will execute:
  1. uv run pytest
  2. python scripts/analyze.py
  3. rm -rf /tmp/results

Start in background? [y/n]
```

**3. Agent autonomously starts background task**

**Scenario**: User asks "Run tests", agent proposes background execution.

**Flow**:
- Agent: "Running tests in background to avoid blocking. This will execute: uv run pytest"
- If command requires approval: agent calls `start_background_task` tool with `requires_approval=True` → chat loop prompts user
- If command safe: agent calls tool → task starts immediately
- User sees: "Started background task <task_id>"

**4. Background task spawns child processes**

**Scenario**: Background task runs a script that spawns child processes.

**Solution**: Use process group termination (PGID) for cancellation:
- When spawning: `process = await asyncio.create_subprocess_exec(..., start_new_session=True)`
- When cancelling: `os.killpg(os.getpgid(pid), signal.SIGTERM)`
- This kills parent + all children

**5. User edits command after approval**

**Scenario**: User approves task, then tries to edit command before it starts.

**Prevention**: Task starts immediately after approval (no edit window). If user wants to change command, they must cancel and start new task.

### Security Guarantees

1. **No approval bypass**: Background execution does not reduce security. Approval gate is at task creation, not execution.

2. **Audit trail**: All approvals recorded in metadata and OpenTelemetry spans. Can trace who approved what and when.

3. **Same rules as interactive**: Background tasks use identical approval logic to interactive shell tool. No separate code paths.

4. **Approval cannot be forged**: Approval prompt is in main process (chat loop), not spawned task. Task cannot fake approval.

5. **Cancellation available**: User can cancel task at any time, even after approval.

---

## Test Specifications

### Test File: `tests/test_background.py`

#### Storage Tests

**1. `test_create_task_metadata`**

Test task creation and metadata persistence.

```python
def test_create_task_metadata(tmp_path):
    """Create task, verify metadata.json written."""
    storage = TaskStorage(base_path=tmp_path)
    metadata = TaskMetadata(
        task_id="task_test_001",
        created_at=datetime.now(),
        status=TaskStatus.PENDING,
        command=["echo", "hello"],
        cwd="/tmp",
        description="Test task",
        approved_by="user",
        approved_at=datetime.now(),
    )
    task_dir = storage.create_task("task_test_001", metadata)

    # Assertions
    assert task_dir.exists()
    assert (task_dir / "metadata.json").exists()

    # Read back metadata
    loaded = storage.get_task("task_test_001")
    assert loaded.task_id == "task_test_001"
    assert loaded.status == TaskStatus.PENDING
    assert loaded.command == ["echo", "hello"]
```

**2. `test_update_task_metadata`**

Test metadata updates.

```python
def test_update_task_metadata(tmp_path):
    """Update status, verify persistence."""
    storage = TaskStorage(base_path=tmp_path)
    metadata = TaskMetadata(
        task_id="task_test_002",
        created_at=datetime.now(),
        status=TaskStatus.PENDING,
        command=["sleep", "1"],
        cwd="/tmp",
        description="Test task",
        approved_by="user",
        approved_at=datetime.now(),
    )
    storage.create_task("task_test_002", metadata)

    # Update status
    storage.update_task("task_test_002", status=TaskStatus.RUNNING, pid=12345)

    # Read back
    loaded = storage.get_task("task_test_002")
    assert loaded.status == TaskStatus.RUNNING
    assert loaded.pid == 12345
```

**3. `test_list_tasks_all`**

Test listing all tasks.

```python
def test_list_tasks_all(tmp_path):
    """List all tasks."""
    storage = TaskStorage(base_path=tmp_path)

    # Create 3 tasks
    for i in range(3):
        metadata = TaskMetadata(
            task_id=f"task_test_{i:03d}",
            created_at=datetime.now(),
            status=TaskStatus.PENDING,
            command=["echo", str(i)],
            cwd="/tmp",
            description=f"Test task {i}",
            approved_by="user",
            approved_at=datetime.now(),
        )
        storage.create_task(f"task_test_{i:03d}", metadata)

    # List all
    tasks = storage.list_tasks()
    assert len(tasks) == 3
```

**4. `test_list_tasks_filtered`**

Test filtering by status.

```python
def test_list_tasks_filtered(tmp_path):
    """List tasks by status."""
    storage = TaskStorage(base_path=tmp_path)

    # Create tasks with different statuses
    for i, status in enumerate([TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.COMPLETED]):
        metadata = TaskMetadata(
            task_id=f"task_test_{i:03d}",
            created_at=datetime.now(),
            status=status,
            command=["echo", str(i)],
            cwd="/tmp",
            description=f"Test task {i}",
            approved_by="user",
            approved_at=datetime.now(),
        )
        storage.create_task(f"task_test_{i:03d}", metadata)

    # Filter by running
    running = storage.list_tasks(status=TaskStatus.RUNNING)
    assert len(running) == 1
    assert running[0].status == TaskStatus.RUNNING
```

**5. `test_delete_task`**

Test task deletion.

```python
def test_delete_task(tmp_path):
    """Delete task directory."""
    storage = TaskStorage(base_path=tmp_path)
    metadata = TaskMetadata(
        task_id="task_test_005",
        created_at=datetime.now(),
        status=TaskStatus.COMPLETED,
        command=["echo", "hello"],
        cwd="/tmp",
        description="Test task",
        approved_by="user",
        approved_at=datetime.now(),
    )
    task_dir = storage.create_task("task_test_005", metadata)
    assert task_dir.exists()

    # Delete
    storage.delete_task("task_test_005")
    assert not task_dir.exists()
```

**6. `test_cleanup_old_tasks`**

Test cleanup of old tasks.

```python
def test_cleanup_old_tasks(tmp_path):
    """Create old completed tasks, run cleanup, verify deletion."""
    storage = TaskStorage(base_path=tmp_path)

    # Create task completed 10 days ago
    old_time = datetime.now() - timedelta(days=10)
    metadata = TaskMetadata(
        task_id="task_old",
        created_at=old_time,
        status=TaskStatus.COMPLETED,
        command=["echo", "old"],
        cwd="/tmp",
        description="Old task",
        approved_by="user",
        approved_at=old_time,
        completed_at=old_time,
    )
    storage.create_task("task_old", metadata)

    # Create recent task
    metadata = TaskMetadata(
        task_id="task_new",
        created_at=datetime.now(),
        status=TaskStatus.COMPLETED,
        command=["echo", "new"],
        cwd="/tmp",
        description="New task",
        approved_by="user",
        approved_at=datetime.now(),
        completed_at=datetime.now(),
    )
    storage.create_task("task_new", metadata)

    # Cleanup (retention: 7 days)
    deleted_count = storage.cleanup_old_tasks(retention_days=7)
    assert deleted_count == 1

    # Verify old task deleted, new task kept
    assert storage.get_task("task_old") is None
    assert storage.get_task("task_new") is not None
```

#### Runner Tests

**7. `test_start_task_simple`**

Test starting and completing a simple task.

```python
@pytest.mark.asyncio
async def test_start_task_simple(tmp_path):
    """Start simple command, verify completion."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(
        command=["echo", "hello"],
        cwd="/tmp",
        description="Simple task",
        approved_by="user",
    )

    # Wait for completion (should be fast)
    await asyncio.sleep(0.5)

    # Check status
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.COMPLETED
    assert metadata.exit_code == 0
```

**8. `test_start_task_with_output`**

Test output capture.

```python
@pytest.mark.asyncio
async def test_start_task_with_output(tmp_path):
    """Start command with output, verify output.log."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(
        command=["echo", "test output"],
        cwd="/tmp",
        description="Task with output",
        approved_by="user",
    )

    # Wait for completion
    await asyncio.sleep(0.5)

    # Read output.log
    task_dir = tmp_path / task_id
    output = (task_dir / "output.log").read_text()
    assert "test output" in output
```

**9. `test_task_failure`**

Test handling of failed tasks.

```python
@pytest.mark.asyncio
async def test_task_failure(tmp_path):
    """Start failing command, verify exit_code != 0."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(
        command=["false"],  # Always exits with 1
        cwd="/tmp",
        description="Failing task",
        approved_by="user",
    )

    # Wait for completion
    await asyncio.sleep(0.5)

    # Check status
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.FAILED
    assert metadata.exit_code != 0
```

**10. `test_cancel_running_task`**

Test task cancellation.

```python
@pytest.mark.asyncio
async def test_cancel_running_task(tmp_path):
    """Start long command, cancel, verify SIGTERM sent."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(
        command=["sleep", "10"],
        cwd="/tmp",
        description="Long task",
        approved_by="user",
    )

    # Wait for task to start
    await asyncio.sleep(0.5)

    # Cancel
    success = await runner.cancel_task(task_id)
    assert success

    # Check status
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.CANCELLED
```

**11. `test_cancel_with_sigkill`**

Test SIGKILL fallback for unresponsive tasks.

```python
@pytest.mark.asyncio
async def test_cancel_with_sigkill(tmp_path):
    """Start command that ignores SIGTERM, verify SIGKILL."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    # Script that ignores SIGTERM
    script = tmp_path / "ignore_sigterm.sh"
    script.write_text("""#!/bin/bash
trap '' SIGTERM  # Ignore SIGTERM
sleep 100
""")
    script.chmod(0o755)

    task_id = await runner.start_task(
        command=[str(script)],
        cwd="/tmp",
        description="Unresponsive task",
        approved_by="user",
    )

    # Wait for task to start
    await asyncio.sleep(0.5)

    # Cancel (should use SIGKILL after grace period)
    success = await runner.cancel_task(task_id)
    assert success

    # Check status
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.CANCELLED
```

**12. `test_concurrent_tasks`**

Test concurrency limit.

```python
@pytest.mark.asyncio
async def test_concurrent_tasks(tmp_path):
    """Start 6 tasks with max_concurrent=5, verify 5 running + 1 pending."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    # Start 6 tasks
    task_ids = []
    for i in range(6):
        task_id = await runner.start_task(
            command=["sleep", "2"],
            cwd="/tmp",
            description=f"Task {i}",
            approved_by="user",
        )
        task_ids.append(task_id)

    # Check status immediately
    await asyncio.sleep(0.5)
    running = [t for t in task_ids if runner.get_status(t).status == TaskStatus.RUNNING]
    pending = [t for t in task_ids if runner.get_status(t).status == TaskStatus.PENDING]

    assert len(running) == 5
    assert len(pending) == 1
```

**13. `test_monitor_task_completion`**

Test status transitions.

```python
@pytest.mark.asyncio
async def test_monitor_task_completion(tmp_path):
    """Start task, verify status updates (pending → running → completed)."""
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(
        command=["sleep", "1"],
        cwd="/tmp",
        description="Monitored task",
        approved_by="user",
    )

    # Check status progression
    # Immediately after start: pending or running
    metadata = runner.get_status(task_id)
    assert metadata.status in [TaskStatus.PENDING, TaskStatus.RUNNING]

    # After 0.5s: should be running
    await asyncio.sleep(0.5)
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.RUNNING

    # After 1.5s: should be completed
    await asyncio.sleep(1.5)
    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.COMPLETED
```

#### Slash Command Tests

**14. `test_slash_background`**

Test `/background` command.

```python
@pytest.mark.asyncio
async def test_slash_background(monkeypatch, tmp_path, capsys):
    """Run /background echo hello, verify task created."""
    # Setup
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)
    # ... (inject runner into chat context)

    # Simulate user input
    input_text = "/background echo hello"
    # ... (call handle_background())

    # Verify task created
    tasks = storage.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].command == ["echo", "hello"]

    # Verify output
    captured = capsys.readouterr()
    assert "Started background task" in captured.out
```

**15. `test_slash_background_approval`**

Test approval prompt for dangerous command.

```python
@pytest.mark.asyncio
async def test_slash_background_approval(monkeypatch, tmp_path, capsys):
    """Run /background rm -rf /tmp, verify approval prompt."""
    # Setup
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    # Mock user input (approval)
    inputs = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    # Simulate user input
    input_text = "/background rm -rf /tmp/test"
    # ... (call handle_background())

    # Verify approval prompt shown
    captured = capsys.readouterr()
    assert "requires approval" in captured.out.lower()

    # Verify task created with approval metadata
    tasks = storage.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].approved_by == "user"
```

**16. `test_slash_tasks_list`**

Test `/tasks` command.

```python
@pytest.mark.asyncio
async def test_slash_tasks_list(tmp_path, capsys):
    """Run /tasks, verify table output."""
    # Setup: create some tasks
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)
    await runner.start_task(["echo", "test1"], "/tmp", "Task 1", "user")
    await runner.start_task(["echo", "test2"], "/tmp", "Task 2", "user")

    # ... (call handle_tasks())

    # Verify table output
    captured = capsys.readouterr()
    assert "task_" in captured.out
    assert "Task 1" in captured.out
    assert "Task 2" in captured.out
```

**17. `test_slash_status`**

Test `/status` command.

```python
@pytest.mark.asyncio
async def test_slash_status(tmp_path, capsys):
    """Run /status <id>, verify detailed output."""
    # Setup: create task with output
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)
    task_id = await runner.start_task(["echo", "test output"], "/tmp", "Test task", "user")

    # Wait for completion
    await asyncio.sleep(0.5)

    # ... (call handle_status(task_id))

    # Verify detailed output
    captured = capsys.readouterr()
    assert task_id in captured.out
    assert "completed" in captured.out.lower()
    assert "test output" in captured.out
```

**18. `test_slash_cancel`**

Test `/cancel` command.

```python
@pytest.mark.asyncio
async def test_slash_cancel(tmp_path, capsys):
    """Run /cancel <id>, verify cancellation."""
    # Setup: start long task
    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)
    task_id = await runner.start_task(["sleep", "10"], "/tmp", "Long task", "user")

    # Wait for task to start
    await asyncio.sleep(0.5)

    # ... (call handle_cancel(task_id))

    # Verify cancellation
    captured = capsys.readouterr()
    assert "cancelled" in captured.out.lower()

    metadata = runner.get_status(task_id)
    assert metadata.status == TaskStatus.CANCELLED
```

#### Tool Tests

**19. `test_start_background_task_tool`**

Test `start_background_task` tool.

```python
@pytest.mark.asyncio
async def test_start_background_task_tool():
    """Call tool, verify task started."""
    # Setup: create agent with task runner
    # ... (initialize CoDeps with TaskRunner)

    # Call tool
    result = await start_background_task(
        ctx=ctx,  # RunContext[CoDeps]
        command="echo hello",
        description="Test task",
    )

    # Verify result
    assert "task_id" in result
    assert result["status"] == "running"
    assert "Started background task" in result["display"]
```

**20. `test_check_task_status_tool`**

Test `check_task_status` tool.

```python
@pytest.mark.asyncio
async def test_check_task_status_tool():
    """Call tool, verify status returned."""
    # Setup: create task
    # ... (start task via runner)

    # Call tool
    result = await check_task_status(
        ctx=ctx,
        task_id=task_id,
        include_output=True,
    )

    # Verify result
    assert result["task_id"] == task_id
    assert result["status"] in ["pending", "running", "completed"]
    assert "output" in result
```

**21. `test_cancel_background_task_tool`**

Test `cancel_background_task` tool.

```python
@pytest.mark.asyncio
async def test_cancel_background_task_tool():
    """Call tool, verify cancellation."""
    # Setup: start long task
    # ... (start task via runner)

    # Call tool
    result = await cancel_background_task(
        ctx=ctx,
        task_id=task_id,
    )

    # Verify result
    assert result["status"] == "cancelled"
    assert "cancelled" in result["display"].lower()
```

**22. `test_list_background_tasks_tool`**

Test `list_background_tasks` tool.

```python
@pytest.mark.asyncio
async def test_list_background_tasks_tool():
    """Call tool, verify task list."""
    # Setup: create multiple tasks
    # ... (start tasks via runner)

    # Call tool
    result = await list_background_tasks(
        ctx=ctx,
        status_filter=None,
    )

    # Verify result
    assert "tasks" in result
    assert result["count"] > 0
    assert "display" in result
```

#### Integration Tests

**23. `test_agent_proposes_background`**

Test agent proposing background execution.

```python
@pytest.mark.asyncio
async def test_agent_proposes_background():
    """User asks to run tests, agent proposes background execution."""
    # Setup: create agent with task runner
    # ... (initialize agent)

    # User input
    user_message = "Run the full test suite"

    # Run agent
    response = await agent.run(user_message, deps=deps)

    # Verify agent proposed background execution
    assert "background" in response.output.lower()
    # ... (verify task created if user approved)
```

**24. `test_agent_checks_status`**

Test agent checking task status.

```python
@pytest.mark.asyncio
async def test_agent_checks_status():
    """User asks 'What's the status of my task?', agent calls check_task_status."""
    # Setup: create task
    # ... (start task via runner)

    # User input
    user_message = f"What's the status of task {task_id}?"

    # Run agent
    response = await agent.run(user_message, deps=deps)

    # Verify agent called check_task_status tool
    # ... (check tool calls in response.tool_calls)
    assert any(call.tool_name == "check_task_status" for call in response.tool_calls)
```

**25. `test_background_task_with_otel`**

Test OpenTelemetry integration.

```python
@pytest.mark.asyncio
async def test_background_task_with_otel(tmp_path):
    """Start task, verify span created and linked."""
    # Setup: initialize with OTel exporter
    # ... (setup SQLiteSpanExporter)

    storage = TaskStorage(base_path=tmp_path)
    runner = TaskRunner(storage=storage, max_concurrent=5)

    task_id = await runner.start_task(["echo", "hello"], "/tmp", "OTel task", "user")

    # Wait for completion
    await asyncio.sleep(0.5)

    # Read span_id from metadata
    metadata = runner.get_status(task_id)
    span_id = metadata.span_id

    # Query OTel database for span
    # ... (query co-cli.db for span with span_id)
    # Verify span exists with correct attributes
```

---

## Success Criteria

**Phase 1 complete** when:
- Task metadata can be created, updated, read, listed, deleted
- Old tasks cleaned up based on retention policy
- All storage tests pass

**Phase 2 complete** when:
- Commands execute in background via asyncio
- Process output captured to log file
- Status tracked (pending → running → completed/failed)
- Cancellation works (SIGTERM → SIGKILL)
- Concurrency limit enforced
- All runner tests pass

**Phase 3 complete** when:
- `/background`, `/tasks`, `/status`, `/cancel` commands implemented
- Approval prompts shown for dangerous commands
- Commands integrated into chat loop
- All slash command tests pass

**Phase 4 complete** when:
- Tools registered with agent
- Tools accessible via `ctx.deps.task_runner`
- Tools return correct dict format with `display` field
- All tool tests pass

**Phase 5 complete** when:
- All 25+ tests pass
- Integration tests verify agent proposes background tasks
- OTel integration verified

**System complete** when:
- User can run long tasks in background without blocking chat
- Status queries work (both slash commands and agent tools)
- Cancellation works reliably
- Approval inheritance prevents security bypass
- Output logs retained for debugging
- Old tasks cleaned up automatically

**Documentation complete** when:
- DESIGN doc created (`DESIGN-14-background-execution.md`)
- DESIGN doc follows 4-section template (What & How, Core Logic, Config, Files)
- Tool docstrings complete with usage examples
- Settings documented (env vars, defaults)

---

## Open Questions

1. **Task naming**: Should task IDs include user-provided labels? Current: `task_YYYYMMDD_HHMMSS_<command>`. Alternative: `task_YYYYMMDD_HHMMSS_<user_label>`.

2. **Progress reporting**: Should tasks report progress (e.g., "42% complete")? Requires parsing output or custom progress protocol. Out of scope for MVP?

3. **Task dependencies**: Should tasks declare dependencies (task B waits for task A)? Out of scope for MVP, but reserve design space.

4. **Scheduled execution**: Should tasks support delayed start (run at specific time)? Out of scope for MVP.

5. **Task restart**: Should failed tasks be restartable? Add `/restart <task_id>` command? Useful for transient failures.

6. **Notification on completion**: Should system notify user when background task completes (e.g., desktop notification, banner in next chat turn)? How intrusive?

7. **Output streaming**: Should `/status` show live tail of output (like `tail -f`)? Requires terminal multiplexing or separate viewer.

8. **Task groups**: Should tasks be groupable (e.g., "all tests", "batch-2026-02-09")? Useful for bulk operations.

9. **Max output size**: Should output.log be truncated after N lines? Prevents disk fill for infinite loops. Configurable limit?

10. **Shell vs direct exec**: Should background commands support shell features (pipes, redirects)? Current: direct exec only. Shell support adds complexity and security risk.

---

## Future Enhancements (Post-MVP)

1. **Task prioritization**: Priority queue for task execution (high/normal/low).

2. **Task scheduling**: Cron-like scheduling for recurring tasks.

3. **Task templates**: Save common tasks as templates for reuse.

4. **Task chaining**: DAG-based dependencies (task C runs after A and B complete).

5. **Progress protocol**: Custom protocol for tasks to report progress (JSON messages on stdout).

6. **Live output viewer**: Terminal UI for watching task output in real-time.

7. **Distributed execution**: Run tasks on remote machines (SSH/Docker).

8. **Resource limits**: CPU/memory limits for tasks (via cgroups or Docker).

9. **Task hooks**: Pre/post task hooks for notifications, logging, cleanup.

10. **Web UI**: Browser-based task dashboard (alternative to CLI).

---

## Related Documents

- `docs/DESIGN-00-co-cli.md` — Architecture overview, tool registration
- `docs/DESIGN-01-agent.md` — `CoDeps` dataclass, agent factory
- `docs/DESIGN-02-chat-loop.md` — Slash commands, input dispatch
- `docs/DESIGN-05-otel-logging.md` — OpenTelemetry integration
- `docs/DESIGN-09-tool-shell.md` — Shell tool approval logic (reuse for background tasks)

---

**Status**: TODO (not started)

**Assignee**: TBD

**Target completion**: TBD

---

*This document describes planned work only. Design details may change during implementation. Create `DESIGN-14-background-execution.md` after implementation to document actual architecture.*
