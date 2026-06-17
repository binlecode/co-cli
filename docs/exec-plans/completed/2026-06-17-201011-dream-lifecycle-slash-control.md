# Dream Lifecycle Slash Control

## Context

The dream daemon is a per-`CO_HOME` background process, deliberately decoupled from
the REPL: the filesystem queue is the sole cross-process bridge, control is
POSIX-native (`setsid` spawn / `SIGTERM` stop / PID-file status), and the producer
never gates on consumer liveness. This decoupling is correct and rejected-by-design
to break — see `docs/specs/dream.md §1.1`.

Lifecycle control today is **asymmetric across surfaces**:

- `co dream {start,stop,status,run}` (shell CLI, `co_cli/commands/dream.py:54-110`) —
  full lifecycle. `start_daemon`/`stop_daemon`/`status_daemon` live in
  `co_cli/daemons/dream/process.py`; `run` writes the `DREAM_RUN_TAG` sentinel.
- `/dream` (in-REPL slash, `handle_dream_slash`, `co_cli/commands/dream.py:23-44`) —
  **read-only status only**. Registered at `co_cli/commands/core.py:66`.

Verified facts (source-grounded):
- Auto-spawn `maybe_autospawn_dream` (`co_cli/bootstrap/core.py:494-512`) no-ops when
  `dream.enabled` is False or `CO_DREAM_NO_AUTOSPAWN` is set. `dream.enabled`
  (default False, `co_cli/config/dream.py:24`) gates **auto-spawn only**.
- The KICK producer `write_review_kick` (`co_cli/session/review_kick.py`) is
  unconditional; the REPL's `_post_turn_hook` call site (`co_cli/main.py:191`) is
  gated on `skills.review_enabled`, **not** `dream.enabled`. So kicks queue to disk
  regardless of whether the daemon is running — no drop-on-disabled bug exists.
  The decoupling invariant holds.

Consequence: a user inside a chat can *see* "disabled / not running, queue depth N"
via `/dream` but cannot act without dropping to a shell. Accumulated kicks drain only
once the daemon is started — and there's no in-REPL affordance to start it. This
directly produced the observed "recall isn't working" confusion: the daemon was off,
session facts were never promoted to curated memory, and the user had no in-session
signal that it was actionable.

## Problem & Outcome

**Problem:** `/dream` is inspection-only; the daemon lifecycle can only be driven from
a separate shell. Users have no in-REPL way to start/stop/trigger the curator they can
already observe is down.

**Outcome:** `/dream` gains `start | stop | tidy` subcommands that wrap the existing
detached `process.py` control surface; the housekeeping verb is renamed `run` → `tidy`
on both surfaces; and `stop` carries a static "shared daemon" warning requiring an
explicit force token (the daemon is a per-`CO_HOME` singleton). Bare `/dream` keeps its
current status output. The daemon stays a detached, disowned process — the slash command
*controls* it, never *owns* it as a child.

**Failure cost:** Without this, the curation daemon silently stays off across sessions
whenever auto-spawn didn't fire (`dream.enabled=False`, or a prior crash). Session-stated
durable facts are never promoted to memory, cross-session recall degrades to lexical luck
over volatile transcripts, and the user gets no actionable in-session signal — exactly the
failure that prompted this plan.

## Scope

**In scope:**
- Extend `handle_dream_slash` to parse a subcommand arg (`start`, `stop`, `tidy`, or
  empty → existing status).
- Each subcommand delegates to the already-existing `process.py` functions /
  housekeeping sentinel — no new lifecycle mechanism.
- Update the `/dream` registration help string to reflect the new verbs.
- Rename the internal sentinel for consistency: `DREAM_RUN_TAG` → `DREAM_TIDY_TAG` and the
  marker filename `run.tag` → `tidy.tag` (`config/core.py:49`, consumed by `_loop.py`,
  written by `commands/dream.py`). Ephemeral self-consuming marker — no migration needed;
  any stale `run.tag` is simply never read again.
- **Force-gated stop.** The daemon is a per-`CO_HOME` singleton shared by every attached
  session, so `stop` (both surfaces) carries a static warning that stopping pauses curation
  for any other attached session and requires an explicit force token to proceed. No presence
  registry, no live-session count — by the decoupling invariant a stop only *pauses* curation
  (kicks keep queuing to disk and drain on next start), so the guard is a flat confirmation,
  not a count-driven gate.
- Rename the housekeeping verb `run` → `tidy` **consistently across both surfaces**:
  the new `/dream tidy` and the existing `co dream run` shell subcommand. Zero-backward-
  compat — no `run` alias retained. `run` mis-reads as "run the daemon" (what `start`
  does); the pass is merge → decay over the memory corpus, so `tidy` names the goal.

**Out of scope (explicitly):**
- Any change to the existing spawn / stop / queue *mechanism* or the decoupling model. The
  stop gate is a force-token check in the command handlers that decides whether to call the
  unchanged `stop_daemon`; the SIGTERM/SIGKILL/pidfile path itself is untouched.
- A per-REPL presence registry / live-session count (rejected — a stop only pauses curation,
  which self-heals on next start; a flat force-gated warning satisfies the intent without the
  marker-lifecycle machinery. Revisit as a separate plan if a real count-driven need appears).
- Auto-spawn behavior and the `dream.enabled` gate (unchanged — manual `/dream start`
  is the intended override when auto-spawn is off, mirroring `co dream start`).
- Binding daemon lifetime to the REPL (rejected — breaks decoupling).
- Renaming any unrelated `run`-named identifier — `run_interval_hours`,
  `run_housekeeping`, `run_standalone`, `_run_foreground`, `uv run`. Only the user-facing
  `run` *verb* and its `DREAM_RUN_TAG`/`run.tag` sentinel are renamed.
- Spec edits (handled by `sync-doc` post-delivery).

## Behavioral Constraints

- **Spawn-and-disown only.** `/dream start` must produce a process whose lifetime is
  independent of the REPL — reuse `start_daemon` (which already spawns detached via
  `setsid`). The REPL must not hold the daemon as a child.
- **No new cross-process channel.** Control stays PID-file + signals + filesystem
  sentinel. No sockets, no in-memory handles to the daemon.
- **`/dream start` works regardless of `dream.enabled`.** It is an explicit manual
  action; the enabled gate governs *auto*-spawn, not manual start (parity with
  `co dream start`).
- **Idempotent / safe re-entry.** `start` when already running, `stop` when not running,
  and `tidy` when not running must each report clearly and not error the REPL. Because
  `start_daemon` raises `SystemExit(1)` on the already-running path, the slash handler MUST
  not let that escape (status pre-check + `SystemExit` catch); `stop` and `tidy` are
  return-only and already safe.
- **Stop pauses, never breaks, other sessions.** Stopping the daemon does not corrupt or
  block concurrent REPLs — by the decoupling invariant their review KICKs keep queuing to
  disk and drain on the next start. So the risk is *surprise paused curation*, not data loss;
  the correct guard is a **warn + explicit confirmation** (not a hard block, not a
  presence-conditional gate). `co dream stop` requires `--force`; `/dream stop` requires
  `/dream stop force`. Without the force token, stop prints the warning and does nothing.

## High-Level Design

`handle_dream_slash(ctx, args)` becomes a thin dispatcher:

```
sub = args.strip().split(maxsplit=1)[0].lower() if args.strip() else ""
match sub:
  "" or "status" → existing status block (unchanged)
  "start"        → if status_daemon(...).running: console "already running (pid …)"; return
                   else: try start_daemon(USER_DIR, origin="slash")
                         except SystemExit: pass   # TOCTOU: spawned between check and call
  "stop"         → if "force" not in args.split()[1:]: console warning; return
                   else: stop_daemon(USER_DIR)
  "tidy"         → if not status_daemon(...).running: console hint; return
                   else atomic_write_text(DREAM_TIDY_TAG, ""); console confirmation
  other          → console usage line (valid: start | stop | tidy)
```

**Contract note (CD-M-1/CD-M-2):** `start_daemon`/`stop_daemon` return `None` and emit
their own status lines via bare `print()` (`process.py:54,76,98,108,116,124`); they are
NOT result-returning, and `start_daemon` raises `SystemExit(1)` when the daemon is already
running (`process.py:55`). The slash dispatcher therefore (a) does NOT attempt to re-render
their output via `console` — it lets the primitives print their own one-shot lifecycle line
to stdout, reserving `console` only for the dispatcher's own messages (status pre-check
results, hints, usage, run-confirmation); and (b) guards `start` with a status pre-check
plus a `SystemExit` catch so the already-running path never propagates `SystemExit` out of
the slash handler (`dispatch` at `co_cli/commands/core.py:125` has no exception guard, so an
unguarded `SystemExit` would abort the REPL turn). The existing `process.py` lifecycle
functions (`start_daemon`/`stop_daemon`/`status_daemon`) are unchanged by the slash routing;
the stop force-gate lives entirely in the command handlers (`commands/dream.py`), not in
`process.py`.

All called functions already exist in `co_cli/daemons/dream/process.py` and are used by
the shell CLI; this task only routes the slash surface to them. Arg-parsing idiom matches
peers (`co_cli/commands/approvals.py:24`, `co_cli/commands/queue_control.py:37`).

## Tasks

### ✓ DONE TASK-1 — Add start/stop/tidy dispatch to the `/dream` slash handler
- **files:** `co_cli/commands/dream.py`
- **done_when:** Invoking `handle_dream_slash` against a temp `CO_HOME`: `/dream start`
  results in `status_daemon().running is True`; `/dream start` again (already running) does
  NOT raise `SystemExit` out of the handler and reports already-running; `/dream stop`
  results in `status_daemon().running is False`; `/dream tidy` writes the `DREAM_TIDY_TAG`
  sentinel when running and emits the not-running hint (and writes no sentinel) when down;
  `/dream <unknown>` emits the usage line; bare `/dream` and `/dream status` produce the
  unchanged status output. (Integration-style real-spawn assertions, consistent with the
  existing `tests/daemons/dream/` suite.)
- **success_signal:** A user can start, trigger, and stop the dream daemon entirely from
  inside the REPL without aborting the turn, and the daemon's lifetime is independent of
  the REPL process.
- **prerequisites:** none

### ✓ DONE TASK-1b — Update the in-REPL not-running status hint to name `/dream start`
- **files:** `co_cli/commands/dream.py`
- **done_when:** The `handle_dream_slash` status branch that currently hints
  `'co dream start' to start manually` instead names the in-REPL verb `/dream start`
  (the daemon-disabled branch likewise points at `/dream start` as the manual override).
- **success_signal:** When the daemon is down, the status output tells the user the
  in-session action (`/dream start`) rather than directing them to a shell — the exact
  signal that was missing in the originating incident.
- **prerequisites:** TASK-1

### ✓ DONE TASK-2 — Update the `/dream` registration help string
- **files:** `co_cli/commands/core.py`
- **done_when:** The `SlashCommand("dream", ...)` help text names the new verbs (e.g.
  "Manage the dream daemon (status | start | stop | tidy)") and `/help` lists it.
- **success_signal:** N/A
- **prerequisites:** TASK-1

### ✓ DONE TASK-2b — Rename the shell CLI verb `co dream run` → `co dream tidy` and the sentinel
- **files:** `co_cli/commands/dream.py`, `co_cli/commands/memory.py`, `co_cli/config/core.py`, `co_cli/daemons/dream/_loop.py`
- **done_when:** `co dream tidy` runs the one-shot housekeeping request (same behavior as
  the old `run`: errors-with-hint when daemon down, writes the sentinel when up);
  `co dream run` no longer exists (zero-backward-compat, no alias); the hint string in
  `memory.py:240` names `co dream tidy`; the sentinel constant is `DREAM_TIDY_TAG`
  (file `tidy.tag`) with no `DREAM_RUN_TAG`/`run.tag` reference remaining in any of
  producer (`commands/dream.py`), consumer (`_loop.py`), or definition (`config/core.py`).
- **success_signal:** Both surfaces (`/dream tidy` and `co dream tidy`) expose the same
  verb; no `run` verb or `run.tag` sentinel remains anywhere.
- **prerequisites:** none

### ✓ DONE TASK-3 — Force-gate `stop` on both surfaces with a static shared-daemon warning
- **files:** `co_cli/commands/dream.py`
- **rationale:** The daemon is a per-`CO_HOME` singleton; a stop pauses curation for any other
  attached session. Because the decoupling invariant guarantees a stop only *pauses* (KICKs
  keep queuing and drain on next start — no data loss), the guard is a flat warn-and-require-
  force, not a presence-conditional gate. No marker registry, no live-session count.
- **call-site semantics (RESOLVED — see Open Questions):** the confirmation axis and the
  SIGKILL axis are kept separate. `--force` keeps its existing SIGKILL-immediately meaning and
  *also* implies confirmation (it is the stronger intent). A new `--yes` option means "proceed
  with a graceful stop." So: `co dream stop` (no flag) → warning, no-op; `co dream stop --yes`
  → confirmed SIGTERM stop; `co dream stop --force` → confirmed SIGKILL stop. `/dream stop`
  → warning, no-op; `/dream stop force` → confirmed graceful stop (the slash surface has no
  SIGKILL distinction).
- **done_when:** `co dream stop` with neither `--yes` nor `--force` prints the static warning
  ("dream daemon is shared per-CO_HOME; stopping pauses curation for any other attached
  session — use --yes (graceful) or --force (SIGKILL)") and does NOT stop the daemon;
  `co dream stop --yes` stops it gracefully; `co dream stop --force` SIGKILLs it. `/dream stop`
  (no `force` token) prints the static warning and does not stop; `/dream stop force` stops it.
  Stop remains return-only (no `SystemExit` into the REPL).
- **success_signal:** A user cannot casually stop the shared daemon without an explicit force
  confirmation, with no per-session bookkeeping required.
- **prerequisites:** TASK-1, TASK-2b

## Testing

- Functional test for the dispatcher routing: assert observable behavior per subcommand
  against a temp `CO_HOME` — `start` then `status.running is True`; `stop` then
  `status.running is False`; `tidy` while down emits the not-running hint and writes no
  sentinel; unknown sub emits the usage line. Mirror `done_when`; assert behavior, not
  structure. Reuse existing dream-daemon test fixtures under `tests/daemons/dream/`.
- No test asserts daemon internals (those are covered by existing `process.py` tests);
  this suite covers only the slash-surface routing and its observable effects.
- Force-gated stop functional test (TASK-3): against a temp `CO_HOME` with the daemon running,
  assert `stop` without the force token is a no-op (daemon stays running) and the warning is
  emitted; assert `stop --force` / `/dream stop force` stops it. Behavior, not structure.

## Open Questions

- **`co dream stop --force` semantic collision (TASK-3) — RESOLVED.** `--force` today means
  "SIGKILL immediately, skip the SIGTERM grace" (`commands/dream.py:83-90` →
  `stop_daemon(force=True)`, `process.py:101-109`). Resolution: keep the two axes separate.
  `--force` retains its SIGKILL meaning and additionally implies confirmation; a new `--yes`
  option confirms a *graceful* (SIGTERM) stop. Unconfirmed `co dream stop` warns and no-ops.
  This avoids forcing SIGKILL on a user who only wants to stop, and leaves the `process.py`
  stop mechanism untouched (the gate lives entirely in the `commands/dream.py` handler).

## Final — Team Lead

Gate 1 PO review complete (2026-06-17). Approved with scope revisions:
- **Presence registry removed.** Original TASK-3 (per-PID JSON markers, lifecycle bracketing,
  PID-reuse detection) + count-bearing stop gate were ~half the plan and guarded a self-healing
  annoyance (a stop only *pauses* curation; it drains on next start). Replaced by a flat
  force-gated stop with a static shared-daemon warning (new TASK-3) — no marker subsystem,
  no live-session count. A count-driven gate, if ever needed, is a separate plan.
- **Rename kept in scope** (TASK-2b) — both surfaces gain `tidy` in one pass.
- Minor citation fix for dev: the session-end KICK gate is at `main.py:191-192`
  (`skills.review_enabled`), not the `_post_turn_hook` call site at `main.py:180`.
- New open question surfaced: `co dream stop --force` already means SIGKILL — don't overload
  it for the proceed-confirmation; use a separate token. Resolve in dev.

> Gate 1 — PO approved. Once you've confirmed the revised scope:
> run `/orchestrate-dev dream-lifecycle-slash-control`

## Delivery Summary — 2026-06-17

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `/dream start|stop|tidy|status|<unknown>` route correctly; start-twice no `SystemExit`; tidy hint+no-sentinel when down | ✓ pass |
| TASK-1b | down-state status hint names `/dream start` (both enabled + disabled branches) | ✓ pass |
| TASK-2 | `SlashCommand("dream", ...)` help names `status | start | stop | tidy` | ✓ pass |
| TASK-2b | `co dream tidy` replaces `co dream run`; `DREAM_TIDY_TAG`/`tidy.tag`; no `run`-verb/`run.tag` ref anywhere in source/tests | ✓ pass |
| TASK-3 | `co dream stop` (no flag) warns + no-op; `--yes` graceful / `--force` SIGKILL; `/dream stop` warns, `/dream stop force` stops | ✓ pass |

**Tests:** scoped — 27 passed, 0 failed (7 `test_slash_dispatch.py` real-spawn + 20 `test_housekeeping.py`).
One mid-run failure RCA'd and fixed: the tidy-while-running test originally asserted the
sentinel *file* persists, which races the daemon's self-consuming idle tick
(`_loop.py:_maybe_housekeep` unlinks it on sight). Rewrote to assert the observable routing
effect (confirmation message vs. not-running hint) — behavior, not a racy structural artifact.

**Doc Sync:** fixed — `docs/specs/dream.md` (`run`→`tidy` verb + `DREAM_TIDY_TAG` sentinel across
8 sites; `/dream` slash description rewritten from read-only to full control surface; `co dream
stop` signature → `[--yes] [--force]`; added `test_slash_dispatch.py` coverage row).

**Resolved open question (recorded in plan):** the `co dream stop --force` collision — `--force`
kept its SIGKILL meaning (and implies confirmation); a new `--yes` confirms a graceful stop.
The confirmation axis and the kill-mode axis are kept separate so a user wanting a graceful stop
is never forced into SIGKILL.

**Extra files touched (beyond `files:` lists, announced during dev):**
- `tests/daemons/dream/test_housekeeping.py` — renamed `test_dream_run_*` → `test_dream_tidy_*`
  and its section comment (the verb rename invalidated the old test).
- `tests/daemons/dream/test_slash_dispatch.py` — new functional test file for the slash routing
  (called for by the plan's Testing section).

**Overall: DELIVERED**
All five tasks pass `done_when`; lint clean; scoped tests green; docs synced.

**Next step:** `/review-impl dream-lifecycle-slash-control` — full suite + evidence scan + behavioral verification → verdict at Gate 2.

## Implementation Review — 2026-06-17

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | start/stop/tidy/status/unknown route; start-twice no `SystemExit`; tidy hint+no-sentinel when down | ✓ pass | `commands/dream.py:33-78` dispatcher; `start` guards with status pre-check + `except SystemExit: pass` (`:41-49`); `stop` force-gate (`:52-67`); `tidy` running-check then `atomic_write_text(DREAM_TIDY_TAG, "")` (`:69-76`); unknown → usage (`:78`). Verified by `test_slash_dispatch.py` (7 real-spawn tests, all green) |
| TASK-1b | down-state hint names `/dream start` (enabled + disabled branches) | ✓ pass | `commands/dream.py:95` (enabled-down) and `:98-100` (disabled) both name `/dream start` |
| TASK-2 | `SlashCommand("dream", ...)` help names verbs | ✓ pass | `commands/core.py:66-67` — "Manage the dream daemon (status \| start \| stop \| tidy)"; `co dream --help` lists start/status/stop/tidy |
| TASK-2b | `co dream tidy` replaces `run`; `DREAM_TIDY_TAG`/`tidy.tag`; no `run`-verb/`run.tag` ref in source/tests | ✓ pass | `config/core.py:49` `DREAM_TIDY_TAG = .../"tidy.tag"`; consumer `_loop.py:87-88`; producer `commands/dream.py:74,182`; hint `memory.py:240`; grep over `co_cli/` + `tests/` finds zero `DREAM_RUN_TAG`/`run.tag` (remaining hits are in completed/ plans + this active doc only); `co dream run` no longer a command |
| TASK-3 | `co dream stop` no-flag warns+no-op; `--yes` graceful / `--force` SIGKILL; `/dream stop` warns, `/dream stop force` stops | ✓ pass | CLI gate `commands/dream.py:155-164` (warn+return unless `yes or force`; `stop_daemon(force=force)`); slash gate `:52-67`; `process.py` stop mechanism untouched. Verified live: `co dream stop` → warning, exit 0, no-op |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `co_home` param of `start_daemon`/`stop_daemon`/`status_daemon` is unused — all derive paths from module constants (`DREAM_PID_FILE` etc.) | `daemons/dream/process.py:42,81,129` | minor (pre-existing, out of scope) | Not changed — `process.py` is explicitly the untouched mechanism (Scope/Behavioral Constraints). The dead param predates this plan and is why the slash handler passing a stale `USER_DIR` is harmless (callee ignores it). Flagged for a future `rules-conformance-cleanup` (one-sided member). |

### Tests
- Command: `uv run pytest tests/daemons/dream/test_slash_dispatch.py tests/daemons/dream/test_housekeeping.py`
- Result: 27 passed, 0 failed (no LLM-call stalls; slowest 6.05s real-spawn)
- Log: `.pytest-logs/<ts>-review-impl-scoped.log`
- Full real-LLM suite not run: this delivery is pure CLI/slash routing with no LLM, no shared mutable state, no prompt-assembly path — the affected dream suite is the complete behavioral coverage. (Conditional, per the project's disproportionate-suite guidance.)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `co dream --help`: ✓ lists `start | status | stop | tidy` (no `run`)
- `co dream stop` (no flag): ✓ prints shared-daemon warning, exit 0, no-op (`success_signal`: cannot casually stop shared daemon — verified)
- `co dream tidy` (daemon down): ✓ "dream daemon not running…", exit 1, no sentinel
- `co dream run`: ✓ no such command (zero-backward-compat)
- `/dream` slash routing (start→running, stop force→down, tidy confirmation, unknown→usage): ✓ via `test_slash_dispatch.py` real-spawn tests (`success_signal`: full lifecycle from inside REPL without aborting the turn — verified)

### Overall: PASS
All five tasks meet `done_when` with file:line evidence; lint clean; affected suite green; behavioral verification confirms every `success_signal`. The one finding (unused `co_home` param) is pre-existing, in the explicitly-untouched `process.py`, and routed to a future conformance cleanup — not blocking.


