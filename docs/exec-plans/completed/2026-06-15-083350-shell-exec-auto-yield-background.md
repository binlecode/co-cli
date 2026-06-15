# shell_exec auto-yield to background

## Context

`shell_exec` (`co_cli/tools/shell/execute.py`) runs a command in the foreground and
`await`s `ShellBackend.run_command` (`co_cli/tools/shell_backend.py`), which spawns via
`asyncio.create_subprocess_exec("sh","-c",cmd, …, start_new_session=True)` and consumes
output with `proc.communicate()` under an `asyncio.wait_for(timeout)` (default 120 s,
capped by `shell.max_timeout`). The whole turn blocks until the command exits or times out.

co already has detached background execution: `task_start` (`co_cli/tools/tasks/control.py:40`)
builds a `BackgroundTaskState` (`co_cli/tools/background.py:28`) and calls `spawn_task`, which
spawns its **own** process via `create_subprocess_shell` and attaches `_monitor` to stream
stdout+stderr line-by-line into `~/.co-cli/logs/bg-<task_id>.log`. Reads tail that file
(`task_status`, `/tasks`). `kill_task` does process-group SIGTERM→200ms→SIGKILL.

The REPL queues input typed while a turn is active and drains one item per turn boundary
(`co_cli/main.py` accept_handler → `_enqueue`; `_app.py` `_drain_next`). So a long foreground
command makes the prompt look frozen: input accumulates but nothing runs until the turn ends.

Esc-cancel now kills the foreground process group (shipped v0.8.359 — `_kill_process_group_on_cancel`
in `shell_backend.py`), so the manual escape hatch is reliable. This plan removes the need to
use it for the common case.

Peer-validated: openclaw auto-promotes a foreground command that exceeds a "yield window" to
background and returns control (`bash-tools.exec.ts` yieldWindow); opencode has this on its
roadmap (explicit TODOs in `tool/bash.ts`); hermes exposes an explicit `terminal(background=true)`
mode.

## Problem & Outcome

**Problem.** An unbounded or slow foreground command (`mpv <url>`, `vlc`, a dev server,
`tail -f`, `watch`, an interactive REPL) holds the turn open up to the timeout. During that
window user input queues and the REPL appears blocked. The model cannot reliably predict at
call-time whether a command will return — `mpv <url>` looks like any other command.

**Outcome.** `shell_exec` never holds the turn open for an unbounded command. A command still
running after a short **yield window** is adopted into the existing background-task machinery
and `shell_exec` returns immediately with the `task_id` + partial output, freeing the turn.
(A proactive curated-command nudge was considered and deferred — see `## Deferred`.)

**Failure cost:** without this, every time the agent runs an unbounded command in the
foreground (a recurring, model-dependent mistake) the REPL silently locks for up to 120 s,
queued input is stranded, and the user must know to press Esc — which most will read as a hang.

## Scope

**In:**
- Auto-yield in the non-pty `shell_exec` path: bounded incremental read, adopt-live-process
  hand-off into a `BackgroundTaskState`, immediate return with `task_id` + partial output.
- A `adopt_running_process(...)` entry point in `background.py` that registers an
  already-running `asyncio.subprocess.Process` (the foreground proc) and attaches `_monitor`
  to continue draining its live stdout, seeding the log with bytes already read pre-yield.
- `shell.yield_window_seconds` config (default per Open Question 1) and a `shell_exec` behaviour
  that the model is taught about via the tool docstring.
- Tests: real-subprocess functional coverage of yield→adopt and fast-exit-no-yield.

**Out:**
- The proactive curated-unbounded-command heuristic — deferred this cycle (recommend not building;
  full design + curation model retained in `## Deferred`).
- The pty path (`_run_command_pty`): pty mode is output-fidelity only (`isatty`/ANSI), not the
  channel a long-runner is launched on, and its master-fd drain model (no `proc.stdout`) makes
  live hand-off a separate problem. Auto-yield does **not** apply to `pty=True`; it keeps the
  current timeout behaviour. Documented as a known limitation, not a silent gap.
- Re-running / re-spawning the command (rejected — non-idempotent commands would double-execute).
- Background task persistence across sessions (pre-existing limitation, unchanged).

## Behavioral Constraints

- **No double execution.** Yield must hand off the *same* running process, never kill+re-spawn.
- **Fast commands are unchanged.** A command that exits within the yield window returns its full
  output through the existing path with identical shape — no task_id, no behavior change.
- **Output continuity.** Bytes read from the foreground proc before yield must appear in the
  background log (no lost prefix); the monitor continues from the live stream with no dup/gap.
- **Cancellation still works.** A yielded task is a normal `BackgroundTaskState` killable via
  `task_cancel` / session shutdown; the pre-yield Esc-kill path remains valid before yield.
- **stdin parity with `task_start`.** The non-pty foreground spawn opens `stdin=PIPE` (matching
  `spawn_task`), so an adopted task is stdin-drivable via `task_write`/`task_close` — no silent
  divergence from a `task_start` task. (Consistent with the existing "no interactive stdin in the
  foreground" docstring: a prompting command gets a controlled pipe rather than the inherited tty.)

## High-Level Design

**Layering (CD-M-1).** `ShellBackend` is stateless and returns `tuple[int,str]` — it cannot reach
`session.background_tasks` or surface a `task_id`. So the backend only *detects* the yield and
hands the live process back; *adoption* happens in `shell_exec`, the sole holder of `ctx.deps.session`.

**1. Bounded incremental read instead of `communicate()` (non-pty path).**
Replace `await asyncio.wait_for(proc.communicate(), timeout)` with a read loop that accumulates
`proc.stdout` chunks and races them against (a) process exit, (b) the yield window, (c) the hard
timeout. Concretely: `await asyncio.wait_for(eof_or_yield, timeout=min(yield_window, remaining))`.
The non-pty foreground spawn also opens `stdin=PIPE` (CD-M-2) for adopt/`task_write` parity.
- If the process exits before the yield window → identical return to today (`exit_code, output`).
- If the yield window elapses while the process is alive → the backend returns a typed
  `YieldedProcess(proc, prefix_bytes)` sentinel (where `prefix_bytes` = the bytes the foreground
  loop has already **consumed and decoded** — never the StreamReader's still-buffered bytes).
- The existing CancelledError → `_kill_process_group_on_cancel` and TimeoutError → kill paths
  are preserved.

**2. Adopt-live-process hand-off (`background.py`), called from `shell_exec`.**
New `adopt_running_process(proc, command, cwd, session, prefix_bytes, skill_env, logs_dir=LOGS_DIR)
-> BackgroundTaskState`:
- `make_task_id()`, build `BackgroundTaskState(status="running", process=proc, …)`, set both
  `log_path` and `_monitor_task` (so `_drain_and_cleanup` and `kill_task`, which key off exactly
  those, clean up an adopted task identically — CD-M-3).
- Write `prefix_bytes` (consumed-decoded foreground output) to the log first.
- Start a monitor that **reuses the same `proc.stdout` StreamReader** and continues draining from
  where the foreground loop stopped — exactly one reader at a time (the foreground loop must fully
  exit before the monitor's `async for` begins; no intervening `read()` strands buffered bytes, so
  no dup/gap). It then runs the same EOF/`proc.wait()`/`_close_process_transport`/state-finalize
  tail as `_monitor`. Factor that shared tail so `_monitor` and the adopt-monitor cannot diverge.
- Register in `session.background_tasks[task_id]`; return the state.

**3. `shell_exec` yield return.**
On `YieldedProcess`, `shell_exec` calls `adopt_running_process` and returns `tool_output` with the
task_id and a message: command still running after N s, promoted to background task `<id>`; use
`task_status <id>` / `task_cancel <id>`; partial output included. Mirrors the `task_start` return
shape (`task_id=…, status="running"`). The command already cleared the DENY/approval gate before
spawn (execute.py:67–78); adoption does **not** re-gate (CD-m-3).

(The proactive curated-heuristic layer is deferred — see `## Deferred` below.)

**4. Config.** Add `shell.yield_window_seconds` (default proposed ~30 s — see Open Question 1; a
normal `pytest`/build must still return inline, so the window sits above typical bounded-command
durations and only genuinely-stuck/unbounded commands yield). `0` disables auto-yield (opt-out).
Declared field on `ShellSettings` with a `model_validator` capping it below `max_timeout`.

## Tasks

### ✓ DONE TASK-1 — `adopt_running_process` + shared monitor tail in `background.py`
- files: `co_cli/tools/background.py`, `tests/test_flow_background_tasks.py` (or existing bg test)
- done_when: a unit-level async test spawns a long `asyncio` subprocess directly, calls
  `adopt_running_process(proc, …, prefix_bytes=b"early\n")`, writes more output from the child,
  then asserts the task's log file contains both the seeded prefix line and the later output, and
  the state finalizes to `completed`/`failed` on child exit (status + exit_code set).
- success_signal: an externally-spawned running process becomes a tracked BackgroundTaskState
  whose log captures pre- and post-adoption output and whose final state is published on exit.
- prerequisites: none

### ✓ DONE TASK-2 — bounded incremental read + `YieldedProcess` sentinel in `ShellBackend.run_command` (non-pty)
- files: `co_cli/tools/shell_backend.py`, `tests/test_flow_shell.py`
- done_when: the non-pty `run_command` reads `proc.stdout` incrementally (no `communicate()`),
  opens `stdin=PIPE`, and — given a low `yield_window` — returns a `YieldedProcess(proc, prefix_bytes)`
  for a command that outlives the window while a fast command still returns `(exit_code, output)`
  unchanged. A test with `yield_window≈1s` asserts: a `sleep`-long command yields a `YieldedProcess`
  whose `proc.returncode is None` (alive, not killed); `echo hi` returns normally. Existing
  CancelledError/TimeoutError kill-group tests still pass.
- success_signal: a command exceeding the yield window hands its live process back to the caller; a
  fast command is unaffected.
- prerequisites: TASK-1

### ✓ DONE TASK-3 — adopt on yield in `shell_exec`, return task handle
- files: `co_cli/tools/shell/execute.py`, `tests/test_flow_shell_exec.py`
- done_when: an integration test invokes `shell_exec` (through its RunContext, real subprocess,
  `yield_window≈1s`) with a command that outlives the window and asserts the `ToolReturn` carries a
  running `task_id` + partial output, and `task_status(task_id)` reports the task running. The
  command passed approval pre-spawn; adoption does not re-gate (note in code comment).
- success_signal: the model gets a task handle back from `shell_exec` instead of a blocked turn.
- prerequisites: TASK-2

### ✓ DONE TASK-4 — config knob + tool docstring
- files: `co_cli/config/shell.py`, `co_cli/tools/shell/execute.py`, `tests/test_flow_shell.py`
- done_when: `shell.yield_window_seconds` is a declared `ShellSettings` field (added to
  `SHELL_ENV_MAP`) with a `model_validator` capping it below `max_timeout`; a test sets the window
  to `0` and asserts a long command runs to the hard timeout (no yield). The `shell_exec` docstring
  documents the yield-to-background behavior and that `pty=True` is exempt.
- success_signal: operators can tune/disable the window; the model is told a `shell_exec` result may
  be a task handle.
- prerequisites: TASK-2

## Testing

- All shell/background tests run real subprocesses, real log files, no mocks (per testing policy).
- Yield timing uses generous margins (window ≪ command duration ≪ hard timeout) to avoid flakiness.
- Reuse the `sleep N; touch marker` and SIGTERM-ignoring-child patterns already in
  `tests/test_flow_shell.py` (v0.8.359) for liveness/kill assertions.
- Tests set `yield_window_seconds≈1s` via config (window ≪ command duration ≪ hard timeout) for
  speed; liveness asserted via `proc.returncode is None` (reusing v0.8.359 patterns).
- No timeout/window value is widened to make a test pass without RCA (per project rule).

## Open Questions

1. **Yield window default (propose ~30 s).** A normal `pytest`/build can legitimately run 8–30 s;
   backgrounding it would change the agent's "wait for output" flow for a very common *bounded*
   command. The window must sit above typical bounded-command durations so only genuinely-stuck /
   unbounded commands yield. Esc-kill (v0.8.359) makes the longer apparent freeze tolerable. Final
   value is a Gate-1 product call — sanity-check against local build/test durations.

## Deferred — proactive curated-unbounded heuristic (recommend NOT building now)

**Recommendation (TL, adopting PO-M-1):** do not build this in this cycle. Once auto-yield ships,
the silent 120 s lock the outcome targets is gone — the residual cost for a known-unbounded command
is a bounded ~window-length wait, not a hang. A curated `ModelRetry` nudge would only shave that one
wait for a hand-enumerated set, at the cost of a new module, curation governance, a config-asymmetry
question, and a false-positive mode rated *worse* than a miss. Revisit only if telemetry later shows
the yield wait is a recurring annoyance for a small, stable command set. The full curation design is
retained below (the clarify ask) so a future cycle starts from a settled answer, not a blank page.

**Design, if revived:** new `co_cli/tools/shell_heuristics.py`, `is_known_unbounded(cmd) -> str | None`,
called by `shell_exec` after the DENY/approval gate; on a hit, raise `ModelRetry` pointing at
`task_start`. The only consultation site for the list. Never blocks or kills — a routing nudge, not a
boundary (approval stays the boundary).

**Curation model:**
- **Source of truth & location:** a single hardcoded structure in `shell_heuristics.py`, package
  source, version-controlled. Not generated, not fetched, not in user config.
- **Shape (CD-m-1):** *not* a flat prefix list — unboundedness is flag-conditional. A small set of
  `(predicate, reason)` rules where `predicate` runs over the **tokenized argv** (not a string
  prefix): (a) bare-binary-unbounded (`mpv`, `vlc`, `ffplay`, `mplayer`); (b) flag-conditional
  (`tail` only with `-f`/`-F`, `journalctl` only with `-f`); (c) always-unbounded utilities
  (`watch`, `top`, `htop`). Negative cases (`tail file`, `ls`) must run through the same predicate.
- **Entry criterion:** a command whose *default*/matched invocation does not terminate on its own.
  Each entry carries a one-line comment stating why. Conservative: when in doubt, omit — a miss is
  harmless (auto-yield catches it); a false positive wrongly nudges a finite command, which is worse.
- **Review/maintenance:** ordinary source under normal change discipline. Adding an entry = edit the
  structure + comment + a test row. No special lifecycle.
- **Config-overridable? No.** It is a non-load-bearing optimization on top of auto-yield (the real,
  command-agnostic mechanism), not a policy/security boundary — so no config surface (contrast
  `shell.safe_commands`, which *is* a boundary the user must widen). Hardcoded is correct *if* revived.
- **Boundary vs auto-yield:** explicitly redundant with auto-yield by design — auto-yield is the
  catch-all for everything the list misses and will never enumerate.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev shell-exec-auto-yield-background`

## Delivery Summary — 2026-06-15

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | adopt_running_process seeds log w/ prefix + live output, finalizes on exit | ✓ pass |
| TASK-2 | non-pty run_command incremental read, stdin=PIPE, YieldedProcess on yield; fast cmd unchanged; cancel/timeout preserved | ✓ pass |
| TASK-3 | shell_exec adopts on yield, returns running task_id + partial; task_status reports running | ✓ pass |
| TASK-4 | shell.yield_window_seconds field (default 20, env, model_validator < max_timeout); window=0 disables; docstring updated | ✓ pass |

**Tests:** scoped — 48 passed, 0 failed (3 touched files)
**Doc Sync:** fixed — config.md (added `shell.yield_window_seconds`), tools.md (corrected stale `proc.communicate()` claim, added auto-yield subsection)

**Overall: DELIVERED**
Auto-yield ships: a foreground shell_exec command still alive after the 20s window is adopted (same live process, no re-spawn, no re-gate) into a background task; the turn is freed with a task handle.

## Implementation Review — 2026-06-15

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | adopt seeds prefix + live output, finalizes | ✓ pass | background.py:151-196 adopt_running_process (same proc, no re-spawn); _drain_to_log:122-148 shared by _monitor (mode w) + _adopt_monitor (mode a) — cannot diverge; log_path+_monitor_task+process all set → kill_task/task_status parity |
| TASK-2 | incremental read + YieldedProcess; sole-reader hand-off | ✓ pass | shell_backend.py:114-152 incremental loop (no communicate), stdin=PIPE; :140-141 yields only while alive (returncode is None); wait_for cancels+awaits drain_task before yield → lossless hand-off (StreamReader buffer intact for adopter) |
| TASK-3 | adopt on yield, task handle | ✓ pass | execute.py:115 isinstance check; :121-128 adopt call; :129/:137 task_id+status=running+partial; :116-120 no-re-gate comment |
| TASK-4 | config field + validator + docstring | ✓ pass | shell.py:5-12 DEFAULT_SHELL_YIELD_WINDOW_SECONDS=20 (named, commented); :17 SHELL_ENV_MAP; :86-95 model_validator (<0 and >=max_timeout); execute.py:105 pty exempt |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Non-yield success path decoded strict utf-8 while all other paths use errors="replace" (clean-exit invalid-UTF-8 cmd would crash) | shell_backend.py:151 | blocking | Changed to errors="replace" for consistency |
| shell_exec docstring growth blew ALWAYS schema budget (2854/2500 per-tool, 18108/17700 bucket) | execute.py docstring | blocking | Trimmed note to one essential sentence (return contract); re-pinned ceilings consciously (per-tool 2500→2600, bucket 17700→17900) with dated justification, per pty precedent |
| Mid-line prefix-join correct-by-construction but only newline-terminated case tested | test_flow_background_tasks.py | minor | Noted — correct by design; not expanded |

### Tests
- Command: `uv run pytest -q -p no:cacheprovider`
- Result: 738 passed, 0 failed
- Log: `.pytest-logs/20260615-100718-review-impl.log`

### Behavioral Verification
- End-to-end real-tool smoke: `shell_exec("echo booting; sleep 30; echo done")` with a 2s window returned a running task handle (turn freed, not blocked), the background task captured live output to its real on-disk log, and `task_cancel` killed it cleanly.
- `success_signal` verified: the model gets a task handle back from shell_exec instead of a blocked turn.
- `co status` N/A (no such CLI subcommand; `/status` is a TUI slash command).

### Overall: PASS
Auto-yield is implemented to spec across all four tasks, both blocking findings fixed, full suite green, behavior verified end-to-end.
