# RESEARCH: Cron Scheduler — Peer Review & Adoption Design

**Scope:** OpenClaw's cron system reviewed as primary reference; adoption analysis and design
proposal for co-cli.

---

## 1. OpenClaw Cron System Review

### 1.1 Architecture Overview

OpenClaw implements a full production-grade cron system across roughly 20 TypeScript files:

```
src/cron/
├── types.ts                  # CronJob, CronSchedule, CronPayload, CronDelivery
├── store.ts                  # JSON5 persistence (jobs.json, atomic write + backup)
├── schedule.ts               # Next-run computation (at / every / cron via croner)
├── run-log.ts                # Per-job JSONL execution log with pruning
├── delivery.ts               # Output dispatch (announce / webhook / none)
├── service.ts                # Public CronService API
├── service/
│   ├── state.ts              # CronServiceDeps, event types
│   ├── ops.ts                # RPC handler implementations
│   ├── timer.ts              # Async timer loop, execution engine, retry backoff
│   ├── jobs.ts               # Job creation, patching, stagger, next-run
│   └── store.ts              # Internal load/persist/lock
└── isolated-agent/           # Per-job isolated agent harness
```

Three schedule kinds:

| Kind   | Semantics                                | Storage format             |
|--------|------------------------------------------|----------------------------|
| `at`   | One-shot, fire at absolute timestamp     | ISO string or `+duration`  |
| `every`| Interval relative to anchor time         | milliseconds               |
| `cron` | Standard cron expression + timezone      | string (croner library)    |

Two execution targets:

| Target     | What fires                                               |
|------------|----------------------------------------------------------|
| `main`     | Enqueues a `systemEvent` into the main chat session      |
| `isolated` | Spins up a fully isolated agent process per job          |

### 1.2 Scheduling Engine

`schedule.ts → computeNextRunAtMs(schedule, nowMs)`:

- **`at`**: parses timestamp once; returns `undefined` after it fires (one-shot).
- **`every`**: deterministic formula anchored to job creation time:
  `next = anchor + ceil((now - anchor) / interval) * interval`
  Catch-up on restart: computes directly from anchor, no missed-run queue.
- **`cron`**: delegates to `croner` library with timezone from
  `Intl.DateTimeFormat().resolvedOptions().timeZone`. LRU cache (512 entries) of
  compiled expressions keyed by `expr:tz`. Workaround for a croner bug where
  `nextRun()` can return a time before `now`; retries from `now + 1s` or
  `tomorrow UTC midnight`.
- **Stagger**: for cron kind, adds a deterministic per-job offset (0..staggerMs)
  computed as `SHA256(jobId) % staggerMs` to avoid thundering herd across many jobs.

### 1.3 Timer Loop

`service/timer.ts`:

```
armTimer()
  → compute minNextRunAtMs across all enabled jobs
  → setTimeout(onTimer, delay)   # min delay 1ms, max 60s (drift recovery cap)

onTimer()
  → arm watchdog recheck at 60s (handles stuck jobs)
  → loadStore()
  → collectRunnableJobs()        # jobs where nextRunAtMs <= now
  → mark each as runningAtMs = now, persist (crash recovery marker)
  → execute up to maxConcurrentRuns in parallel
  → applyJobResult() for each    # updates state, writes run log
  → rearmTimer()
```

**Stale run detection**: at service start, jobs with `runningAtMs` older than 2 hours
are cleared to recover from hard crashes.

**Concurrency**: default 1 concurrent job. Configurable via `maxConcurrentRuns`.

### 1.4 Execution Engine — Isolated Agent Path

For `sessionTarget="isolated"` + `payload.kind="agentTurn"`:

1. Resolve full agent config (model, fallbacks, auth profiles, workspace, sandbox skills)
2. Run agent via `runCliAgent(message, ...)` with timeout AbortController
3. Extract text output from agent response
4. Dispatch delivery (announce / webhook) if configured
5. Return `CronRunOutcome` with status, summary, session IDs, usage telemetry

### 1.5 Retry & Error Handling

- **Transient detection**: patterns matched — rate_limit, overloaded, network, timeout, server_error.
- **One-shot jobs** (`at`): exponential backoff (30 s → 60 s → 5 min → 15 min → 60 min) up to `maxAttempts`; after max retries → job disabled.
- **Recurring jobs**: backoff for one cycle, then resume normal schedule.
- **Failure alerts**: after N consecutive errors (default 2), sends notification to configured channel/webhook with 1-hour cooldown.
- **Schedule errors**: `croner` throws on bad expression; caught, increments error counter; job auto-disabled after threshold.

### 1.6 Persistence

- **Primary store**: `~/.config/openclaw/cron/jobs.json` (JSON5). Atomic: write to
  `.json.tmp`, then rename. Backup: `.json.bak` on every save.
- **Run log**: `~/.config/openclaw/cron/runs/<jobId>.jsonl`. JSONL, per-job, auto-pruned
  at 2 MB / 2000 lines. Contains ts, status, error, summary, sessionId, durationMs,
  model/provider/token usage.

### 1.7 Job Lifecycle

```
add → next_run computed → enabled = true → timer arms
  → fires: runningAtMs = now (crash marker)
  → executes
  → result applied: lastRun*, consecutiveErrors updated, next_run computed
  → [at + deleteAfterRun] → job removed after success
  → [error] → retry schedule or disable + alert
```

### 1.8 Delivery System

Three delivery modes: `announce` (messaging channel), `webhook` (HTTP POST), `none`.
`bestEffort=true` — delivery failures don't fail the job or trigger alerts.
Multi-account: delivery can target a specific `accountId` for multi-bot setups.

### 1.9 Agent Tool & CLI

- **`cron-tool.ts`**: agent-facing tool with flattened schema. Supports "context extraction"
  — captures recent chat context to populate job message automatically.
- **CLI**: `cron add|list|run|update|remove|status|runs` via Typer-equivalent.
- **Gateway RPC**: all ops exposed as RPC handlers; authenticated, validated, locked.

### 1.10 What OpenClaw Gets Right

| Strength | Notes |
|---|---|
| Three schedule kinds | `at` / `every` / `cron` covers all user patterns from reminders to recurring digests |
| Isolated agent per job | Each job gets fresh deps, model, auth — no session contamination |
| Crash recovery markers | `runningAtMs` written before execution; cleared 2h later — safe across restarts |
| Stagger | SHA256-based deterministic offset prevents thundering herd |
| Run log with telemetry | Per-job JSONL log with model/token usage is excellent for debugging |
| Retry with backoff | Distinguishes transient vs permanent errors; right failure semantics |
| Failure alerts | N-consecutive-errors + cooldown is a clean contract for operators |
| Atomic + backup persistence | Never corrupts jobs on crash or disk full |
| LRU cron expression cache | Croner parsing is expensive; caching is correct here |

### 1.11 What OpenClaw Overdoes (for co's scope)

| Feature | Why co can skip or simplify |
|---|---|
| `systemEvent` / main-session target | co is single-session CLI; no second session to inject into |
| `wakeMode: "now" \| "next-heartbeat"` | Depends on heartbeat concept that co doesn't have |
| Multi-account delivery routing | co has no messaging platform integrations |
| `announce` delivery channel | co outputs to terminal only (for now) |
| Webhook delivery | Out of scope for CLI tool |
| Stagger (thundering herd) | Single-user CLI; rarely runs >1 job simultaneously |
| Gateway RPC layer | co is local CLI, no gateway process |
| Session retention pruning inside cron | co doesn't have long-lived sessions per job |
| Full agent config resolution per job (auth profiles, sandbox overrides) | co agents share single config; no per-job auth profiles |
| JSON5 format | co uses standard JSON everywhere; no benefit from JSON5 |
| croner external library | Python's `croniter` or manual interval math covers co's needs |

---

## 2. TinyClaw Heartbeat System Review

### 2.1 Architecture Overview

Unlike OpenClaw's complex cron engine, TinyClaw takes a hyper-minimalist approach to recurring tasks using a "heartbeat" mechanism:

- **Loop Engine**: A simple bash script (`lib/heartbeat-cron.sh`) runs inside a `tmux` pane. It loops indefinitely, sleeping for `heartbeat_interval` seconds (default 3600).
- **Queue Injection**: On each tick, the bash script iterates over all configured agents and injects a new message directly into the SQLite queue (`tinyclaw.db`) with `channel="heartbeat"` and the contents of a `heartbeat.md` file.
- **Execution Payload**: There is no schedule parser or RPC. The payload is literally the text from `<agent_workspace>/heartbeat.md` (e.g., "Check for pending tasks. Check for errors. Take action if needed.").
- **Smart Pruning**: To prevent the queue from backing up if an agent is busy, the script checks the `messages` table and skips enqueuing a new heartbeat if one is already pending or processing for that agent.

### 2.2 What TinyClaw Gets Right

- **Extreme Transparency**: The schedule is just a `sleep` loop. The payload is just a markdown file. Users can easily understand and modify `heartbeat.md` to change what the agent checks periodically.
- **Queue Backpressure**: Skipping heartbeat injection when the agent is already processing one prevents queue flooding.
- **No Complex Math**: No drift formulas, no timezone parsing, no caching expressions.

### 2.3 Why Co Should Stick to the OpenClaw Model

While TinyClaw's approach is brilliantly simple for a 24/7 background chatbot, it is too blunt for a precise CLI tool:
- Co needs to support one-shot reminders ("remind me in 30m"). TinyClaw's heartbeat cannot do deferred one-shot execution.
- Co needs to support specific intervals per job ("run script X daily", "run script Y hourly"). TinyClaw fires the same `heartbeat.md` payload at a single global interval.
- Therefore, the proposed SQLite-backed `ScheduledJob` table inspired by OpenClaw remains the correct target architecture for `co-cli`.

---

## 3. Co-CLI Capability Inventory

### 3.1 What Already Exists

| Component | Status | Location |
|---|---|---|
| TaskRunner (async subprocess manager) | Complete | `_background.py` |
| TaskStorage (filesystem, metadata.json, output.log) | Complete | `_background.py` |
| Task tools (start / check / cancel / list) | Complete | `tools/task_control.py` |
| Slash commands (`/background`, `/tasks`, `/cancel`, `/status`) | Complete | `_commands.py` |
| Approval gate for side-effecting tools | Complete | `_approval.py`, `_orchestrate.py` |
| XDG paths (.co-cli/ project, ~/.config/co-cli/ user) | Complete | `config.py` |
| SQLite WAL (OTel traces) | Complete | `_telemetry.py` |
| Config layering (env > project > user > defaults) | Complete | `config.py` |
| Sub-agent / delegation | Complete | `tools/delegation.py` |

### 3.2 What Is Missing

- No recurring execution model
- No schedule expression parsing
- No "fire at time T" deferred execution
- No persistent schedule store
- No per-job run history
- No retry/backoff for scheduled jobs
- No failure notification path

---

## 4. Adoption Analysis

### 4.1 What to Adopt Directly from OpenClaw

**Three schedule kinds — adopt as-is:**

`at / every / cron` is the minimal complete set. Every real scheduling need maps to one of
these. Do not collapse them or invent a custom DSL.

**`at`-job auto-delete flag — adopt:**

`delete_after_run: bool` on one-shot jobs is the right ergonomic. Reminders and delayed
commands should vanish after firing unless the user explicitly wants history.

**Crash recovery via `running_at` marker — adopt:**

Write `running_at = now` to the DB before execution begins. On process restart, any job
with `running_at` not cleared within a grace period (e.g., 2x `shell_max_timeout`) is
marked failed with `error = "process restarted during execution"`. Essential for
correctness.

**Per-job run log — adopt in simplified form:**

OpenClaw writes a `.jsonl` file per job. For co, store run history as rows in a
`schedule_runs` SQLite table (co already has SQLite; no need for a second per-job file).
Columns: `id, job_id, started_at, ended_at, status, exit_code, error, output_tail`.

**Failure alert semantics (N consecutive errors + cooldown) — adopt logic, not delivery:**

Track `consecutive_errors` per job. After `failure_alert_after` (default 3) consecutive
failures, emit a formatted warning in the next chat session (banner or tool output). No
channel/webhook needed — terminal output is sufficient for a CLI tool.

**Retry-on-transient for shell commands — adopt:**

For jobs running shell commands, attempt retry (up to `job.max_retries`, default 0) on
transient-looking exits (timeout exit code 124, connection refused). No retry on
permanent failures unless specifically transient pattern.

**Interval anchor-relative formula — adopt:**

For `every` schedules: `next = anchor + ceil((now - anchor) / interval) * interval`.
This guarantees drift-free scheduling across restarts without a missed-run queue.

### 4.2 What to Adapt

**Isolated agent execution → co sub-agent delegation:**

OpenClaw spawns a full isolated agent process for `agentTurn` jobs. Co should not
replicate this at MVP. For jobs that need LLM processing, the job payload is a shell
command. A future Phase 3 enhancement can add `payload_kind: "agent_turn"` that invokes
a sub-agent via the existing `tools/delegation.py` infrastructure.

**Job store (JSON5 file) → SQLite table:**

OpenClaw persists to `jobs.json` with atomic write + backup. Co already has an SQLite
database (`co-cli.db`) in WAL mode. Adding a `scheduled_jobs` table is simpler, gives
indexed queries, and reuses existing connection infrastructure. No new file type, no
backup logic needed (WAL handles durability).

**croner (TypeScript) → croniter (Python) or manual interval math:**

For `cron` expressions, `croniter` is a pure-Python library with no heavy deps.
At MVP, skip cron expressions — support only `at` and `every` since they cover 90% of
real use cases without a parser dependency.

**CLI `cron add --cron "0 9 * * *"` → slash command + agent tool:**

OpenClaw has a dedicated CLI subcommand. Co uses slash commands + agent tools. The agent
tool pattern is cleaner for co because users interact through chat. Slash commands are
the secondary control plane. No new top-level CLI subcommand needed.

**Delivery (announce / webhook) → terminal banner + tool output:**

Drop delivery entirely for MVP. Job results are readable via `get_scheduled_task_runs()`
tool or `/schedule history <id>` command. Post-MVP could add a "banner on next chat
start" mode for jobs that completed while the user was away.

**Gateway RPC → direct service calls:**

Co has no gateway process. The `TaskScheduler` service is created in `main.py` at startup
and injected into `CoDeps.services`. Tools call it directly via `ctx.deps.services.scheduler`.

### 4.3 What to Drop for MVP

| OpenClaw Feature | Drop Reason |
|---|---|
| `systemEvent` / main-session injection | No heartbeat in co |
| wakeMode | No main session concept |
| Multi-account delivery | No channels |
| `announce` / webhook delivery | CLI tool only |
| Stagger (hash-based jitter) | Single user, rarely >2 concurrent |
| JSON5 format | co uses standard JSON; SQLite preferred |
| Session retention pruning in cron | co session is per-REPL, not per-job |
| Per-job auth profiles | co has a single credentials model |
| Sub-agent full config resolution per job | Delegate to delegation.py when ready |
| Agent-turn payloads | Phase 3 enhancement |

---

## 5. Proposed Design for Co

### 5.1 Data Model

```python
# co_cli/_scheduler.py

@dataclass
class ScheduleSpec:
    kind: Literal["at", "every", "cron"]
    # kind="at": ISO-8601 string or "+duration" (e.g. "+30m", "+2h")
    at: str | None = None
    # kind="every": interval in seconds
    every_seconds: int | None = None
    # kind="cron": cron expression string (5-field, standard)
    cron_expr: str | None = None
    # cron only: IANA timezone (default: local)
    timezone: str | None = None

@dataclass
class ScheduledJob:
    id: str                          # UUID
    name: str
    description: str | None
    command: str                     # Shell command to run
    schedule: ScheduleSpec
    enabled: bool = True
    delete_after_run: bool = False   # Auto-remove after first successful run
    max_retries: int = 0             # Retry count on transient failure (default: none)
    timeout_seconds: int = 300       # Per-run timeout
    created_at: float = 0.0          # epoch seconds
    updated_at: float = 0.0
    # Runtime state (persisted)
    next_run_at: float | None = None
    running_at: float | None = None  # Crash recovery marker
    last_run_at: float | None = None
    last_run_status: Literal["ok", "error", "skipped"] | None = None
    last_error: str | None = None
    last_duration_seconds: float | None = None
    consecutive_errors: int = 0

@dataclass
class ScheduleRun:
    id: str                          # UUID
    job_id: str
    started_at: float
    ended_at: float | None
    status: Literal["running", "ok", "error", "timed_out"]
    exit_code: int | None = None
    error: str | None = None
    output_tail: str | None = None   # Last ~50 lines of output
    duration_seconds: float | None = None
```

### 5.2 Storage — SQLite Tables

New tables in `~/.local/share/co-cli/co-cli.db`:

```sql
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    description          TEXT,
    command              TEXT NOT NULL,
    schedule_json        TEXT NOT NULL,   -- JSON-serialized ScheduleSpec
    enabled              INTEGER NOT NULL DEFAULT 1,
    delete_after_run     INTEGER NOT NULL DEFAULT 0,
    max_retries          INTEGER NOT NULL DEFAULT 0,
    timeout_seconds      INTEGER NOT NULL DEFAULT 300,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL,
    next_run_at          REAL,
    running_at           REAL,            -- crash recovery marker
    last_run_at          REAL,
    last_run_status      TEXT,
    last_error           TEXT,
    last_duration_seconds REAL,
    consecutive_errors   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id               TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    started_at       REAL NOT NULL,
    ended_at         REAL,
    status           TEXT NOT NULL,   -- running | ok | error | timed_out
    exit_code        INTEGER,
    error            TEXT,
    output_tail      TEXT,            -- last ~50 lines of stdout+stderr
    duration_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_next_run
    ON scheduled_jobs(next_run_at) WHERE enabled = 1;
CREATE INDEX IF NOT EXISTS idx_schedule_runs_job_id
    ON schedule_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_started_at
    ON schedule_runs(started_at DESC);
```

Rationale: single DB, no new file type, atomic via WAL, indexed query for next-due job
lookup, CASCADE delete keeps runs clean when a job is removed.

### 5.3 Scheduler Service

```python
# co_cli/_scheduler.py

class TaskScheduler:
    """Background cron/interval/one-shot job scheduler.

    Lifecycle: start() at process startup, stop() at process exit.
    Runs an asyncio ticker loop that polls due jobs and dispatches
    them through ShellBackend (reusing existing execution + timeout infrastructure).
    """

    def __init__(
        self,
        db_path: Path,
        shell: ShellBackend,
        check_interval_seconds: float = 10.0,
        failure_alert_after: int = 3,
        run_retention_days: int = 30,
    ) -> None: ...

    async def start(self) -> None:
        """Recover stale running_at markers, prune old runs, arm ticker."""

    async def stop(self) -> None:
        """Cancel ticker gracefully."""

    async def add_job(self, job: ScheduledJob) -> ScheduledJob:
        """Persist + compute next_run_at + rearm ticker."""

    async def update_job(self, id: str, **kwargs: Any) -> ScheduledJob:
        """Patch fields + recompute next_run_at if schedule changed."""

    async def remove_job(self, id: str) -> None: ...

    async def get_job(self, id: str) -> ScheduledJob | None: ...

    async def list_jobs(self) -> list[ScheduledJob]: ...

    async def run_now(self, id: str) -> str:
        """Force-trigger a job immediately; return run_id."""

    async def list_runs(
        self, job_id: str, limit: int = 20
    ) -> list[ScheduleRun]: ...

    async def get_alerts(self) -> list[ScheduledJob]:
        """Jobs with consecutive_errors >= failure_alert_after."""

    # --- internal ---

    async def _tick(self) -> None:
        """Poll loop body: collect due jobs, execute each."""

    async def _collect_due(self) -> list[ScheduledJob]:
        """SELECT WHERE enabled=1 AND next_run_at <= now."""

    async def _execute_job(self, job: ScheduledJob) -> None:
        """
        1. Write running_at = now to DB (crash marker).
        2. Run shell command via ShellBackend with asyncio.timeout(job.timeout_seconds).
        3. On transient failure + max_retries > 0: retry with exponential backoff.
        4. Write run row to schedule_runs.
        5. Clear running_at, update last_run_*, consecutive_errors.
        6. Compute and write next_run_at (None for at-jobs after success).
        7. If delete_after_run and status=ok: remove job row.
        """

    def _compute_next_run(self, spec: ScheduleSpec, now: float) -> float | None:
        """
        at:    parse ISO-8601 or +duration; return None if already past.
        every: anchor + ceil((now - anchor) / every_seconds) * every_seconds.
        cron:  croniter.get_next(float) with optional timezone (Phase 2).
        """

    async def _recover_stale(self) -> None:
        """
        On startup: for jobs with running_at set, check age.
        If age > 2 * max(timeout_seconds across jobs, 600): clear running_at,
        set last_run_status='error', last_error='process restarted during execution'.
        """

    async def _prune_old_runs(self) -> None:
        """DELETE schedule_runs WHERE started_at < now - run_retention_days * 86400."""
```

**Note on TaskRunner**: the scheduler uses `ShellBackend` directly (already in
`CoServices`) rather than `TaskRunner`. `TaskRunner` is designed for user-initiated
long-running background processes that the user monitors. Scheduled jobs are
system-managed, shorter-lived, and don't need `TaskRunner`'s output-tailing UX. This
keeps the two systems independent with a clean separation of concerns.

### 5.4 CoDeps Integration

```python
# co_cli/deps.py — extend CoServices

@dataclass
class CoServices:
    shell: ShellBackend
    knowledge_index: KnowledgeIndex
    task_runner: TaskRunner | None = None
    scheduler: TaskScheduler | None = None     # NEW
    model_registry: ModelRegistry | None = None
```

```python
# co_cli/config.py — new Settings fields

scheduler_enabled: bool = True
scheduler_check_interval_seconds: float = 10.0
scheduler_failure_alert_after: int = 3
scheduler_run_retention_days: int = 30
```

Env var overrides follow the existing pattern:
`CO_SCHEDULER_ENABLED`, `CO_SCHEDULER_CHECK_INTERVAL_SECONDS`, etc.

### 5.5 Main.py Wiring

```python
# After ShellBackend init, before TaskRunner (main.py):

if settings.scheduler_enabled:
    scheduler = TaskScheduler(
        db_path=get_db_path(),           # same co-cli.db
        shell=shell_backend,
        check_interval_seconds=settings.scheduler_check_interval_seconds,
        failure_alert_after=settings.scheduler_failure_alert_after,
        run_retention_days=settings.scheduler_run_retention_days,
    )
    await scheduler.start()
    deps.services.scheduler = scheduler

# In cleanup (finally block or signal handler):
if deps.services.scheduler:
    await deps.services.scheduler.stop()
```

### 5.6 Agent Tools

New file: `co_cli/tools/scheduled_tasks.py`

```python
@agent.tool(requires_approval=True)
async def schedule_task(
    ctx: RunContext[CoDeps],
    name: str,
    command: str,
    schedule_kind: Literal["at", "every", "cron"],
    at: str | None = None,
    every_seconds: int | None = None,
    cron_expr: str | None = None,
    timezone: str | None = None,
    description: str | None = None,
    delete_after_run: bool = False,
    max_retries: int = 0,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Schedule a recurring or one-time task. Requires approval."""
    # requires_approval=True: persists across sessions — user must confirm

@agent.tool(requires_approval=False)
async def list_scheduled_tasks(
    ctx: RunContext[CoDeps],
) -> dict[str, Any]:
    """List all scheduled tasks with name, schedule, status, and next run time."""

@agent.tool(requires_approval=False)
async def get_scheduled_task_runs(
    ctx: RunContext[CoDeps],
    job_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Get recent execution history for a scheduled task."""

@agent.tool(requires_approval=True)
async def update_scheduled_task(
    ctx: RunContext[CoDeps],
    job_id: str,
    enabled: bool | None = None,
    command: str | None = None,
    schedule_kind: Literal["at", "every", "cron"] | None = None,
    at: str | None = None,
    every_seconds: int | None = None,
    cron_expr: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Update a scheduled task's schedule, command, or enabled state."""

@agent.tool(requires_approval=True)
async def delete_scheduled_task(
    ctx: RunContext[CoDeps],
    job_id: str,
) -> dict[str, Any]:
    """Permanently delete a scheduled task and its run history."""

@agent.tool(requires_approval=False)
async def run_scheduled_task_now(
    ctx: RunContext[CoDeps],
    job_id: str,
) -> dict[str, Any]:
    """Force-trigger a scheduled task immediately regardless of schedule."""
```

**Return shapes** (per CLAUDE.md `dict[str, Any]` + `display` field convention):

```python
# schedule_task:
{
    "display": "Scheduled: 'daily standup digest'\n  Schedule: every 86400s\n  Next run: 2026-03-11 09:00:00\n  ID: abc123",
    "job_id": "abc123",
    "next_run_at": "2026-03-11T09:00:00",
}

# list_scheduled_tasks:
{
    "display": "3 scheduled tasks:\n  [abc123] daily standup digest — every 24h — next: 09:00 — ok\n  ...",
    "count": 3,
    "jobs": [...],   # structured list for agent reasoning
}

# get_scheduled_task_runs:
{
    "display": "Last 5 runs for 'daily standup digest':\n  2026-03-10 09:00:01 — ok (2.3s)\n  ...",
    "count": 5,
    "runs": [...],
}
```

### 5.7 Slash Commands

New entries in `_commands.py`:

```
/schedule                  → show list of all scheduled jobs (same as list_scheduled_tasks)
/schedule list             → table: name / schedule / status / next_run / last_status
/schedule run <id|name>    → force-trigger by id or name prefix
/schedule disable <id>     → set enabled=false (no approval needed from slash cmd)
/schedule enable <id>      → set enabled=true
/schedule remove <id>      → delete job (prompts "y/n" confirmation inline)
/schedule history <id>     → last 10 runs: started_at / status / duration / exit_code
```

All slash command handlers call `ctx.deps.services.scheduler` directly — no LLM turn,
no tool approval flow. Slash commands operate at the control plane level.

### 5.8 Schedule Expression Handling

**MVP (no external library):**

Support `at` and `every` only. Covers all common use cases:

```python
# Reminders
schedule_task(at="+30m", command="osascript -e 'display notification \"standup\" with title \"co\"'")

# Daily digest
schedule_task(every_seconds=86400, command="python ~/scripts/summarize_mail.py")

# Hourly check
schedule_task(every_seconds=3600, command="./check_deploys.sh")

# One-shot delayed command
schedule_task(at="2026-03-11T14:00:00", command="git push origin main", delete_after_run=True)
```

`+duration` parser (no deps):
```python
_DURATION_RE = re.compile(r"^\+(\d+)(s|m|h|d)$")

def parse_at_spec(spec: str) -> float:
    """Parse ISO-8601 or +duration into epoch seconds."""
    if m := _DURATION_RE.match(spec):
        n, unit = int(m.group(1)), m.group(2)
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return time.time() + n * multipliers[unit]
    return datetime.fromisoformat(spec).timestamp()
```

**Phase 2 (cron expressions) — `croniter` dependency:**

```toml
# pyproject.toml
croniter = ">=2.0"
```

```python
from croniter import croniter
from datetime import datetime, timezone
import zoneinfo

def _cron_next(expr: str, tz_name: str | None, now: float) -> float:
    tz = zoneinfo.ZoneInfo(tz_name) if tz_name else None
    start = datetime.fromtimestamp(now, tz=tz or timezone.utc)
    return croniter(expr, start).get_next(float)
```

### 5.9 Failure Alert Path

No external channel needed. Alerts surface in the next chat session via the existing
banner/bootstrap machinery:

1. `_bootstrap.py` runs at session start (already calls `get_status()` via `_status.py`).
2. Add `get_scheduler_alerts() -> list[ScheduledJob]` to `_scheduler.py`.
3. `_status.py` calls it; include alert count in `StatusInfo`.
4. Startup banner in `main.py` renders alert if `status.scheduler_alerts > 0`:

```
⚠  2 scheduled tasks need attention:
   • "daily standup digest" — 3 consecutive failures (last: connection refused)
   • "weekly backup" — 4 consecutive failures (last: exit code 1)
   Run /schedule history <id> to inspect. Run /schedule disable <id> to silence.
```

This is co-idiomatic: zero new infrastructure, reuses existing `StatusInfo` + banner
display path.

### 5.10 Retry Logic

```python
# In _execute_job:
retries_left = job.max_retries
attempt = 0

while True:
    result = await _run_shell_with_timeout(job.command, job.timeout_seconds)
    attempt += 1
    if result.exit_code == 0:
        break
    if retries_left > 0 and _is_transient_failure(result):
        retries_left -= 1
        backoff = 30 * (2 ** (attempt - 1))  # 30s, 60s, 120s, ...
        await asyncio.sleep(backoff)
        continue
    break  # permanent error or retries exhausted

def _is_transient_failure(result: ShellResult) -> bool:
    return (
        result.exit_code == 124  # timeout
        or "connection refused" in (result.stderr or "").lower()
        or "temporary failure" in (result.stderr or "").lower()
    )
```

Default `max_retries=0` — no retry by default. Users opt in per-job. Keeps
behavior predictable.

---

## 6. Phased Rollout

### Phase 1 — MVP

Scope: `at` + `every` schedule kinds only. Shell command payload only. No cron
expressions. No agent-turn payloads. No delivery. SQLite-backed.

Deliverables:
- `co_cli/_scheduler.py` — `TaskScheduler`, `ScheduledJob`, `ScheduleRun`, `ScheduleSpec`, DDL
- `co_cli/tools/scheduled_tasks.py` — 6 agent tools
- `co_cli/_commands.py` — `/schedule` family (7 subcommands)
- `co_cli/config.py` — 4 scheduler settings
- `co_cli/deps.py` — `scheduler: TaskScheduler | None` in `CoServices`
- `co_cli/main.py` — startup/teardown wiring
- `co_cli/_status.py` — `get_scheduler_alerts()`
- `tests/test_scheduler.py` — functional tests
- `docs/DESIGN-scheduler.md` — component design doc

### Phase 2 — Cron Expressions

Add `croniter` dependency. Implement `ScheduleSpec.kind="cron"` with timezone support.
Update `schedule_task` tool to accept `cron_expr` and `timezone` params.

### Phase 3 — Agent-Turn Payloads

Add `payload_kind: "agent_turn"` on `ScheduledJob`. When kind is `agent_turn`, invoke
a sub-agent via `tools/delegation.py` with `make_subagent_deps(base)` for isolation.
Output stored in `schedule_runs.output_tail`. Model/token usage stored for traceability.

### Phase 4 — Output Delivery

"Banner on next session start" for jobs that produced notable output while the user was
away. Optional webhook delivery for power users.

---

## 7. Key Design Decisions

| Decision | Rationale |
|---|---|
| SQLite over JSON file | Co already has SQLite in WAL mode. No backup logic, no atomic-write boilerplate, indexed queries for next-due job. |
| No croniter at MVP | `at` + `every` covers most use cases. Add dep only when cron expressions are validated as needed. |
| `requires_approval=True` on schedule / update / delete | Scheduling a task is a persistent side effect. `run_now` is read-only in state so no approval needed. |
| ShellBackend, not TaskRunner, as execution engine | TaskRunner is for user-monitored long-running processes. Scheduled jobs are system-managed, short-lived, and need different UX (history table, not live output). |
| Failure alert via startup banner | No channel/webhook complexity. Single-user CLI; user will see the banner at next session start. |
| No isolated agent per job at MVP | Adds significant complexity without clear user demand. Phase 3 when agent-turn jobs are validated against real workflows. |
| `check_interval_seconds=10` default | Low enough for second-granularity `at` jobs, cheap for `every` jobs measured in minutes to days. Configurable. |
| Stagger skipped | Single-user tool. Thundering herd is not a real concern. |
| Anchor-relative `every` formula | Matches OpenClaw's proven approach. Drift-free across restarts. Correct for intervals from minutes to days. |
| `delete_after_run` on `at` jobs | Ergonomically correct: a reminder fires once and is gone. Opt-out with explicit flag if history desired. |
| Crash recovery via `running_at` DB column | Same pattern as OpenClaw, adapted to SQLite. Essential for correctness on process restart. |

---

## 8. Test Plan

All tests: functional, real SQLite, real `ShellBackend`, `asyncio.timeout` for I/O, no
mocks. Per CLAUDE.md policy.

```python
# tests/test_scheduler.py

async def test_at_job_fires_once_and_deletes():
    # create job: at=+2s, delete_after_run=True
    # asyncio.timeout(8): wait for scheduler to fire
    # assert: 1 run row with status=ok, job row deleted

async def test_every_job_repeats():
    # create job: every_seconds=2
    # asyncio.timeout(8): wait for 3 ticks
    # assert: >=3 run rows with status=ok

async def test_at_job_not_deleted_on_failure():
    # create job: command that exits 1, delete_after_run=True
    # asyncio.timeout(8)
    # assert: job row still present, run row status=error

async def test_crash_recovery_clears_stale_running_at():
    # inject running_at = time.time() - 7200 (2h ago) directly into DB
    # create fresh TaskScheduler, call start()
    # assert: running_at cleared, last_run_status="error"

async def test_failure_alert_threshold():
    # create job with command=exit 1, max_retries=0
    # tick scheduler failure_alert_after+1 times manually
    # assert: get_alerts() returns the job

async def test_run_now_force_triggers():
    # create job: every_seconds=86400 (won't fire naturally)
    # call run_now(job_id)
    # asyncio.timeout(10): wait for completion
    # assert: 1 run row created

async def test_delete_job_cascades_runs():
    # create job, run it, delete job
    # assert: no schedule_runs rows for that job_id

async def test_schedule_task_tool_creates_job():
    # call schedule_task tool via real RunContext + real CoDeps
    # assert: job persisted in DB, next_run_at set, display field non-empty

async def test_list_scheduled_tasks_tool_returns_display():
    # create 2 jobs, call list_scheduled_tasks
    # assert: display field present, count=2

async def test_consecutive_errors_reset_on_success():
    # inject consecutive_errors=2 into DB
    # job runs successfully
    # assert: consecutive_errors=0 after run
```

Timeouts per CLAUDE.md:
- Shell execution tests: `asyncio.timeout(15)`
- Timer-triggered fire tests: `asyncio.timeout(10)`
- SQLite-only operations: no timeout needed

---

## 9. Files Table (Projected Post-Implementation)

| File | Purpose |
|---|---|
| `co_cli/_scheduler.py` | `TaskScheduler` service, dataclasses, DDL, schedule math |
| `co_cli/tools/scheduled_tasks.py` | 6 agent tools for schedule management |
| `co_cli/config.py` | +4 scheduler settings |
| `co_cli/deps.py` | +`scheduler: TaskScheduler \| None` in `CoServices` |
| `co_cli/main.py` | +scheduler start/stop wiring |
| `co_cli/_status.py` | +`get_scheduler_alerts()` |
| `co_cli/_commands.py` | +`/schedule` family (7 subcommands) |
| `tests/test_scheduler.py` | Functional tests (10 cases) |
| `docs/DESIGN-scheduler.md` | Component design doc (post-implementation) |

---

## 10. TL Verdict

OpenClaw's cron system is the right reference: production-tested, well-structured, and
covers all scheduling semantics co will eventually need. The core concepts — three
schedule kinds, anchor-relative interval formula, crash-recovery markers, per-job run
log, N-consecutive-error alerts — are sound and should be adopted directly.

Co should not adopt OpenClaw's operational complexity (JSON5 store, isolated agent
harness, delivery channels, gateway RPC, stagger, wakeMode). Those are appropriate for
OpenClaw's multi-user, multi-channel product. Co is a single-user CLI and should start
with the minimal correct design.

**Phase 1 is unblocked.** The existing `ShellBackend`, `SQLite`, approval gates, and
`CoDeps` injection infrastructure are all in place. Phase 1 requires no new external
dependencies and no breaking changes to any existing interface. The scheduler lives
entirely in a new `_scheduler.py` module and a new `tools/scheduled_tasks.py`, with
surgical additions to `config.py`, `deps.py`, `main.py`, `_status.py`, and
`_commands.py`.
