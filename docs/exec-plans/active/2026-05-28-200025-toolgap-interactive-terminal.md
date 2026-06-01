# Tool Gap — interactive terminal: `shell_exec` pty + `task_*` stdin drive

Task type: code

## Context

Two related steps toward driving interactive command-line programs, sourced from
the cross-review against hermes-agent
(`docs/reference/RESEARCH-tools-gaps-co-vs-hermes.md` §1.5, §5). They are
sequenced as phases of one plan because they are the two halves of the same
capability — making the child process *believe* it has a terminal, then giving
co a channel to *talk back* to it:

- **Phase 1 — `shell_exec` `pty` (output fidelity).** co's `shell_exec` is
  one-shot blocking with **no stdin channel**, so pty buys output *fidelity*
  (isatty/ANSI/line-buffering), not interactive *drive*. Small, self-contained,
  ships regardless.
- **Phase 2 — interactive task drive (`task_write` / `task_close`).** co's
  background-task subsystem is fire-and-forget only — it can launch a process and
  tail its output but cannot **answer** a process that prompts for input (a REPL,
  `gh auth login`, `Continue? [y/N]`). This is the *write* path Phase 1 explicitly
  defers; it belongs on the persistent `task_*` background path, never on the
  one-shot `shell_exec`.

Phase 1 ships standalone. Phase 2 rides proven background-subsystem plumbing and
can land or slip without blocking Phase 1. The two together are the "interactive
CLI" capability hermes exposes via `terminal(pty=True)` + `process(write/close)`.

### Hermes parity reference (grounded, not copied)

- **pty** (`hermes-agent/tools/process_registry.py:543`): hermes spawns
  `ptyprocess.PtyProcess.spawn([shell, "-lic", cmd], dimensions=(30,120))` with
  a daemon **reader thread**, falls back to pipe mode on `ImportError`, and
  disables pty for stdin-piped commands (`_command_requires_pipe_stdin`, e.g.
  `gh auth login --with-token`). Deps: `ptyprocess` (unix) / `pywinpty` (win).
  This machinery exists because hermes has a **persistent interactive process
  registry** with `process.write`/`submit`/`close`. **co has none of that** for
  `shell_exec` — it is async one-shot. So Phase 1 uses **stdlib `pty.openpty()` +
  asyncio**, no `ptyprocess` dep, no reader thread, no interactive write path.
- **interactive drive** (`hermes-agent/tools/process_registry.py`): hermes
  supports `process(action="write"/"submit"/"close")`. co will not copy the
  single-tool op-dispatch shape (co splits task ops into one-tool-per-op);
  instead Phase 2 adds **`task_write`** (write + optional newline = the "submit"
  case) and **`task_close`** (close stdin / EOF).

### Verified current state (2026-05-28)

**Phase 1 (pty):**
- `shell_exec` (`co_cli/tools/shell/execute.py:17`) → `ctx.deps.shell.run_command`
  (`co_cli/tools/shell_backend.py:18`): `asyncio.create_subprocess_exec("sh","-c",
  cmd, stdout=PIPE, stderr=STDOUT, start_new_session=True)`, `proc.communicate()`
  with `asyncio.wait_for(timeout)`, `kill_process_tree` on timeout. **No stdin,
  no pty.** Docstring states "No interactive input — commands that prompt for
  stdin will hang and timeout."
- Signature is `shell_exec(ctx, cmd, timeout=120, workdir=None)` — no `pty` param.
- `run_command(cmd, timeout, cwd, extra_env)` — no `pty` param, no pty branch.
- No `pty` import anywhere in `co_cli/`. No existing `tests/test_flow_shell.py`.

**Phase 2 (interactive drive):**
- `task_start` (`co_cli/tools/background.py:78`) spawns via
  `create_subprocess_shell` with `stdout=PIPE, stderr=STDOUT` and **omits
  `stdin`** — the input mouth is sealed. Docstring: "No interactive input is
  possible — commands that prompt for stdin will stall."
- `BackgroundTaskState` (`background.py:28`) retains `process`; `proc.stdin` is
  reachable once the pipe is opened — no new state field needed.
- `_monitor` (`background.py:97`) drains stdout to the log; `kill_task`
  (`background.py:153`) SIGTERM→SIGKILL + drains the monitor.
- The background subsystem is **dual-entry and fully used today**: model tools
  (`task_start`/`task_status`/`task_cancel`/`task_list`) *and* human slash
  commands (`/background`, `/tasks`, `/cancel`), with session-lifecycle cleanup
  in `main.py:_drain_and_cleanup`. The expensive groundwork (spawn, monitor,
  log-tail, kill, cleanup) already exists; Phase 2 is `stdin=PIPE` on spawn + two
  small tools.

## Problem & Outcome

**Problem.** Some CLIs change output when stdout isn't a TTY (no color, full
buffering, "not a terminal" refusals). And any process that prompts for input
stalls until it times out and is killed, because co can see the prompt in the log
but cannot answer it.

**Outcome.**
1. **`shell_exec(pty=True)`** runs the one-shot command under a pseudo-terminal so
   the child sees a TTY — **output fidelity only**, clearly documented as not
   interactive drive.
2. **`task_write(task_id, input, newline=True)`** writes to a running task's
   stdin and drains; **`task_close(task_id)`** closes stdin (EOF). The model
   drives the loop manually: `task_write` → `task_status` (read response) →
   `task_write` again.

## Scope

### In scope — Phase 1 (pty)
- `co_cli/tools/shell/execute.py` + `co_cli/tools/shell_backend.py` — `pty`
  flag plumbed to a pty-backed `run_command` path.
- Tests: `tests/test_flow_shell.py` (new).

### In scope — Phase 2 (interactive drive)
- `co_cli/tools/background.py` — `spawn_task` opens `stdin=PIPE`; helpers
  `write_to_task(state, data, newline)` and `close_task_stdin(state)`;
  `kill_task` closes stdin defensively during cleanup.
- `co_cli/tools/tasks/control.py` — new `task_write` + `task_close` tools
  (`DEFERRED`, concurrency-safe).
- `co_cli/commands/` + `core.py` — human `/write <id> <input>` slash command for
  symmetry with `/background` (Open Q-B1).
- Docstring updates: `task_start` (`tasks/control.py`) and `shell_exec`
  (`shell/execute.py`) point interactive needs at the write channel rather than
  claiming interactive input is impossible.
- Tests: `tests/test_flow_background_tasks.py` (extended).

### In scope — shared
- `docs/specs/tools.md` — both surfaces documented with their caveats.

### Out of scope
- **`shell_exec` stdin** — one-shot; the write channel is background-only.
- **`ptyprocess`/`pywinpty` dependency** — not needed for co's one-shot model.
- **pty-backed background tasks** (TTY + write channel combined) — a follow-up
  once both phases land.
- **`watch_patterns`** mid-process regex notifier; **binary stdin**; **auto
  prompt-detection / synchronous request-response framing** — all follow-ups
  (see Deferred).

## Behavioural Constraints

**Phase 1:**
1. **`pty=False` default is byte-for-byte unchanged** — current `run_command`
   path untouched when pty is off.
2. **pty stays blocking + one-shot** — same timeout/workdir/policy gate;
   `kill_process_tree` on timeout still works (pty child in its own session).
   No stdin write path is added on `shell_exec`.

**Phase 2:**
3. **`stdin=PIPE` must not change non-interactive behavior.** Existing background
   tasks that never read stdin behave byte-for-byte as today; an unused pipe is
   inert. (Existing 6 `test_flow_background_tasks.py` tests pass unchanged.)
4. **Write/close to a dead task is a clean `tool_error`, not a crash.**
   `status != "running"` or `process is None` → typed error caught at the tool
   layer.
5. **BrokenPipe is handled** — writing to a process that closed stdin or exited
   surfaces as `tool_error` ("task no longer accepting input").
6. **Kill still works after writes** — `kill_task` SIGTERM→SIGKILL + drains
   cleanly after any number of writes; closes stdin if still open.

**Shared:**
7. **No new dependency** — stdlib `pty`/`os`/`asyncio` only.

## High-Level Design

### Phase 1 — `shell_exec` pty (output fidelity)
- Decorator/signature: add `pty: bool = False` to `shell_exec`; pass through to
  `run_command(cmd, timeout, cwd, extra_env, pty=…)`.
- `ShellBackend.run_command` pty branch:
```python
if pty:
    master, slave = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        "sh", "-c", cmd, cwd=…, env=…, start_new_session=True,
        stdin=slave, stdout=slave, stderr=slave,
    )
    os.close(slave)
    # drain master fd via loop.add_reader / asyncio.to_thread os.read until EOF,
    # bounded by asyncio.wait_for(timeout); kill_process_tree on timeout.
    return proc.returncode, decoded_output
```
- Output is combined (the TTY merges stdout/stderr); decode with
  `errors="replace"`. Document that ANSI escapes may appear (the point of pty).

### Phase 2 — interactive task drive

**B1 — spawn change + helpers** (`co_cli/tools/background.py`):
```python
# spawn_task:
proc = await asyncio.create_subprocess_shell(
    state.command, stdin=asyncio.subprocess.PIPE,     # NEW
    stdout=PIPE, stderr=STDOUT, cwd=…, env=…, start_new_session=True)

async def write_to_task(state, data: str, newline: bool) -> None:
    proc = state.process
    if state.status != "running" or proc is None or proc.stdin is None:
        raise <not-running error>
    try:
        proc.stdin.write(((data + "\n") if newline else data).encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError) as e:
        raise <not-accepting-input error> from e

async def close_task_stdin(state) -> None:
    proc = state.process
    if proc and proc.stdin and not proc.stdin.is_closing():
        proc.stdin.close()
```

**B2 — tools + human command** (`co_cli/tools/tasks/control.py`, `co_cli/commands/`):
- `task_write(ctx, task_id, input: str, newline: bool = True)` and
  `task_close(ctx, task_id)` — resolve state, call helper, map errors to
  `tool_error`.
- `/write <id> <input>` (`commands/write.py` + `core.py` registration) — human
  symmetry with `/background`.

The interactive loop: `task_start` → `task_status` (see prompt) → `task_write` →
`task_status` (see response) → … → `task_close` or `task_cancel`.

## Tasks

### TODO — TASK-1 — `shell_exec` `pty=True` (output fidelity)
Files: `co_cli/tools/shell/execute.py`, `co_cli/tools/shell_backend.py`.
Impl: add `pty` flag; pty branch in `run_command` using stdlib `pty.openpty()`
+ asyncio master-fd drain; preserve timeout/kill semantics.
**done_when:**
- `pty=False` path is unchanged (existing shell tests pass untouched).
- `shell_exec("python3 -c 'import sys;print(sys.stdout.isatty())'", pty=True)`
  returns `True`; with `pty=False` returns `False`.
- Timeout under pty still kills the process group and surfaces partial output
  (mirror the existing timeout test, with `pty=True`).
- No `ptyprocess`/`pywinpty` import; `uv pip list` unchanged.
- Docstring states pty = output fidelity, **not** interactive stdin drive (point
  interactive needs at `task_write`, see TASK-3).

### TODO — TASK-2 — stdin pipe on spawn + write/close helpers
Files: `co_cli/tools/background.py`.
Impl: `spawn_task` opens `stdin=PIPE`; add `write_to_task` / `close_task_stdin`;
`kill_task` closes stdin defensively during cleanup.
**done_when:**
- Existing 6 `test_flow_background_tasks.py` tests pass unchanged.
- A stdin-reading command can be written to and produces expected log output.
- Write to a completed/cancelled task raises a typed error.

### TODO — TASK-3 — `task_write` + `task_close` tools + `/write` command
Files: `co_cli/tools/tasks/control.py`, `co_cli/commands/write.py` (new),
`co_cli/commands/core.py`.
**done_when:**
- `task_write(id, "y")` on a `Continue? [y/N]` prompt advances the process;
  `task_status` shows post-prompt output.
- `task_close(id)` lets an EOF-reader complete with exit 0.
- Both tools clean-error on not-found / not-running; BrokenPipe → `tool_error`.
- `/write <id> <input>` works from the REPL.
- Registry count goes 37 → 39 (pty adds no tool; `task_write` + `task_close` add two).

### TODO — TASK-4 — docstrings + spec + gate
Files: `co_cli/tools/tasks/control.py` (`task_start`), `co_cli/tools/shell/execute.py`,
`docs/specs/tools.md`.
**done_when:** `task_start`/`shell_exec` docstrings point interactive needs at the
write channel (no longer claim interactive input is impossible / only available
nowhere); `shell_exec` entry documents `pty` (fidelity-only caveat); `task_write`
and `task_close` documented in the spec with their caveats;
`scripts/quality-gate.sh full` clean.

## Testing
- `tests/test_flow_shell.py` — real `pty=True` isatty assertion + pty timeout
  kill (real subprocess, no mocks).
- `tests/test_flow_background_tasks.py` — add: real interactive subprocess
  (`python3 -u -c "import sys; print('got:', input())"`) driven via `task_write`;
  `task_close` EOF-reader exit 0; write-to-completed → `tool_error`; BrokenPipe →
  `tool_error`. Real subprocesses, no mocks.

## Open Questions
1. **pty value vs effort** — given Phase 1 alone can't drive interactive CLIs (no
   stdin channel), is output-fidelity pty worth it standalone? **Rec:** yes —
   ship the cheap fidelity pty (Phase 1, small, self-contained); Phase 2 is the
   actual interactive-drive blocker and now lives in the same plan.
2. **pty output decoding** — strip ANSI escapes before returning, or keep them?
   **Rec:** keep raw (ANSI is the reason to use pty); note it in the docstring so
   callers can strip if needed.
3. **(Phase 2) Approval on `task_write`?** `task_start` already gates the
   *command* (the risk surface). **Rec:** no approval on `task_write`/`task_close`
   — the dangerous act was approved at launch; gating keystrokes is UX-hostile and
   adds nothing. Confirm at Gate 1.
4. **(Phase 2) Encoding.** v1 is UTF-8 text + optional newline; binary stdin out of
   scope (`input: str` only).

## Deferred items
- Interactive stdin drive on `shell_exec` — out of scope by design; the write
  channel is background-only.
- pty-backed background tasks (TTY + write channel combined) — a follow-up once
  Phase 1 + Phase 2 both land.
- `terminal.watch_patterns` — mid-process regex notifier; niche, follows the
  write channel.
- Binary stdin; auto prompt-detection / synchronous request-response framing.

## Shipping order
Phase 1 (pty: TASK-1) is self-contained and ships regardless. Phase 2 (interactive
drive: TASK-2 → TASK-3) rides the background subsystem; TASK-2 (spawn + helpers)
before TASK-3 (tools + command). TASK-4 (docs + gate) lands whatever shipped.
Does not block Batches 1–2 or the sibling `session_search role_filter` /
`vision-input` plans.
