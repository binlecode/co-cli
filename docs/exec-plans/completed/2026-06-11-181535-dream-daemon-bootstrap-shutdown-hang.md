# Dream daemon — unresponsive to SIGTERM during cold bootstrap (blocking embed on the event loop)

Task type: bug fix (daemon lifecycle + bootstrap I/O)

> Found during `/review-impl skills-office` while RCA-ing why the two daemon integration
> tests run long (`test_stop_daemon_terminates_process` ~13s, `test_queued_kick_processed_after_daemon_restart`
> ~27s). The slow time is **not** test overhead — it is a real daemon defect. Captured via an
> in-process `faulthandler` watchdog (external profilers py-spy / `sample` can't attach on this
> hardened-runtime macOS). **Out of scope for skills-office**; filed as its own plan.

## Context

The dream daemon runs its whole lifecycle on a single asyncio event loop in `_run_foreground`
(`co_cli/daemons/dream/process.py:162`): it installs a SIGTERM handler (`process.py:190` —
`loop.add_signal_handler(signal.SIGTERM, shutdown.set)`), writes the PID file (`process.py:193`),
then `await create_deps(...)` (`process.py:203`) and `await main_loop(...)` (`process.py:204`).

**This is not `--foreground`-specific.** The default `co dream start` (no flag) spawns a *detached
child* that re-enters the same code: `start_daemon` (`process.py:59-72`) calls
`spawn_detached(["co", "dream", "start", "--foreground", ...])`, so the background daemon process
runs `_run_foreground` too. The defect fires whenever `stop_daemon` sends SIGTERM to the daemon PID
during the cold-bootstrap window — independent of how the daemon was launched. The `--foreground`
path is just the simplest reproduction, not the boundary of the bug.

`create_deps` (`co_cli/bootstrap/core.py:create_deps`) eagerly indexes canon (and memory) into the
shared `IndexStore` during bootstrap: `_sync_canon_store` (`core.py:420`) → `_sync_canon_dir`
(`core.py:196`) → `tx.index_chunks(...)` (`core.py:242`) → the embedding provider
(`co_cli/index/_embedding.py:74` → `co_cli/index/_providers.py:49`). That provider call is a
**synchronous, blocking** `httpx.post(..., timeout=30.0)` to the embedding backend (TEI/Ollama), run
**directly on the event-loop thread** (it is a sync call chain `await`ed inside `run_until_complete`).

On a **cold** embedding backend (fresh process, model not yet loaded — the exact state at the start
of a test-suite run, where the daemon tests execute *before* anything warms embeddings), that POST
blocks for ~10s while the embedding model cold-loads. While the loop is blocked in a C-level socket
read, **asyncio cannot deliver the SIGTERM callback** (signal handlers run between loop iterations).
So the daemon is deaf to SIGTERM for the entire cold-bootstrap embed. `stop_daemon`
(`process.py:77`) then sends SIGTERM, waits its **10s grace** (`process.py:105-111`,
`for _ in range(20): time.sleep(0.5)`), and finally escalates to SIGKILL (`process.py:112`).

The in-source comment at `process.py:165-166` — *"signal handlers install first so SIGTERM during
the (potentially several-second) create_deps call still triggers clean shutdown"* — is **incorrect**:
the synchronous embed blocks the loop, so the handler cannot run, and the daemon is force-killed
rather than shutting down cleanly.

### Evidence (RCA 2026-06-11)

Hung-state stack (in-process `faulthandler` watchdog, captured ~2s after a clean SIGTERM):

```
httpcore/_backends/sync.py:128 in read              ← blocked on socket read
  ... httpx SYNC .post() ...
co_cli/index/_providers.py:49  in _embed            (httpx.post, timeout=30.0)
co_cli/index/_embedding.py:74  in embed
co_cli/index/store.py:328      in _index_chunks_no_commit
co_cli/index/store.py:109      in index_chunks
co_cli/bootstrap/core.py:242   in _sync_canon_dir
co_cli/bootstrap/core.py:196   in _sync_canon_store
co_cli/bootstrap/core.py:420   in create_deps        ← still bootstrapping, NOT teardown
co_cli/daemons/dream/process.py:203 in _run_foreground
  asyncio … run_until_complete                       ← event-loop thread, blocked
```

| Claim | Verified | Cite |
|---|---|---|
| Bootstrap embed is a sync blocking `httpx.post(timeout=30.0)` | ✓ | `co_cli/index/_providers.py:49` |
| It runs on the event loop inside `create_deps` (canon sync) | ✓ | `core.py:420 → 196 → 242`; stack above |
| SIGTERM handler installed before `create_deps` but only sets an event checked in `main_loop` | ✓ | `process.py:190,203,204` |
| `stop_daemon` SIGTERM→SIGKILL grace is 10s | ✓ | `process.py:105-112` |
| Warm backend → `create_deps` ~1s, shutdown prompt; cold backend → blocks ~10s | ✓ | direct timing: warm `create_deps`=1.05s; cold suite tests=13s/27s |
| `main_loop` idle/retry sleeps already wake on shutdown (loop logic is fine) | ✓ | `_loop.py:117,141` (`asyncio.wait_for(shutdown.wait(), …)`) |

## Problem & Outcome

**Problem.** The daemon cannot be stopped promptly while it is cold-bootstrapping: a SIGTERM issued
during the canon/memory embed is ignored until the blocking embed returns, so every such stop costs
~10s (one SIGKILL grace window) per daemon process. This is invisible in normal warm operation but
shows up as (a) ~13s/~27s daemon integration tests, and (b) a real operational hazard — `co dream
stop` (or a supervisor restart) right after start hangs and force-kills, bypassing the daemon's own
`finally` cleanup (`process.py:206`).

**Failure cost:** slow/forced daemon restarts; force-kill skips clean teardown; misleading
"force-killed (did not respond to SIGTERM in 10s)" logs that read like a daemon crash.

**Outcome.**
1. The event loop stays responsive to SIGTERM throughout bootstrap — blocking embed I/O no longer
   runs on the loop thread.
2. A stop requested during bootstrap exits promptly (cooperative shutdown), not after the full 10s
   grace + SIGKILL.
3. The `process.py:165-166` comment is corrected to match real behavior.
4. The two daemon integration tests assert prompt shutdown (bounded well under the 10s grace) and no
   longer rely on the SIGKILL escalation as the exit path.

## Scope

### In scope
- `co_cli/daemons/dream/process.py` — `_run_foreground` bootstrap/shutdown ordering (race
  `create_deps` against `shutdown`, `os._exit(0)` on bootstrap-interrupt); correct the stale comment;
  tighten the `stop_daemon` grace once shutdown is prompt.
- `co_cli/bootstrap/core.py` — offload the blocking canon/memory index-sync onto a worker thread
  (Route C — see High-Level Design) that opens its own short-lived `IndexStore`.
- `tests/integration/test_daemon_lifecycle.py`, `tests/integration/test_daemon_crash_recovery.py` —
  assert prompt clean shutdown.

### Out of scope
- **`co_cli/index/` internals (`_providers.py` / `_embedding.py` / `store.py`)** — Route C wraps the
  *existing* sync index API in a thread with its own connection; the embed timeout stays `30.0`. No
  change to the blocking-vs-async seam. (Earlier-draft routes (a)/(b) that touched these are rejected
  at Gate 1 — see Open Questions.)
- **Embedding-backend cold-start latency itself** — pre-warming the embedding model is a separate
  concern; this plan makes the daemon *responsive during* that latency, not faster at it.
- **A broad sync→async refactor of `IndexStore`** — rejected as the fix route (Gate 1, OQ1).
- **skills-office** — unrelated; unaffected.

### Gate 1 source findings (why Route C, not (a)/(b))
- **sqlite is thread-affine.** `store.py:149` opens the connection with `check_same_thread` defaulting
  to **True**, created on the loop thread in `create_deps`. A naive `asyncio.to_thread(_sync_canon_store,…)`
  would call `_conn.execute` from the worker thread → `sqlite3.ProgrammingError`. The worker must open
  its **own** connection (Route C).
- **The blocking embed is interleaved with sqlite writes on one connection.** `store.py:321-334`:
  `INSERT chunks` → `self._embedding.embed(…)` (the ~10s blocker) → `INSERT …_vec`, all on `self._conn`.
  "Offload just the embed" would mean restructuring `_index_chunks_no_commit` (out of scope), and the
  async-client route (b) would mean awaiting callers all the way up the sync chain — the full
  IndexStore async refactor (out of scope). Both original routes are therefore rejected.
- **A bounded short embed timeout regresses normal cold start.** A 2–3s timeout fails the *normal*
  cold-start embed (model loads in ~10s) → circuit breaker opens → vectors not indexed. Violates
  Constraint #3. Timeout stays `30.0`; the prompt-exit lever is `os._exit`, not the timeout.

## Behavioral Constraints
1. **Loop responsiveness (hard).** No blocking network/disk I/O on the daemon event-loop thread
   during bootstrap. The SIGTERM handler must be able to fire while embeddings are in flight.
2. **Prompt cooperative shutdown.** A SIGTERM during bootstrap must terminate the process well under
   `stop_daemon`'s grace window (target: ≤ ~2s), via the daemon's own clean path — not via SIGKILL.
3. **No functional regression to canon/memory indexing.** Canon and memory are still indexed; only
   *where/how* the work runs changes. A shutdown mid-bootstrap may legitimately leave indexing
   partial (it resumes next start via `needs_reindex`, `core.py:218`).
4. **Honest comment.** `process.py:165-166` must describe what actually happens.
5. **Real-service tests.** Daemon tests stay real-process, real-signal, no mocks (per test policy).

## High-Level Design

Two independent fixes; #1 is the core defect, #2 is the latency masker.

Getting the blocking embed off the loop has **three** sub-requirements, not one. `asyncio.to_thread`
alone satisfies only the first:

- **(i) Loop responsiveness** — the SIGTERM handler must be able to fire while an embed is in flight.
  `asyncio.to_thread(...)` delivers this: the canonical wrapper for blocking sync I/O. This alone
  ends the force-kill (the daemon's `finally` runs) — but *not* the latency.
- **(ii) Cooperative race against shutdown** — `await create_deps(...)` (`process.py:203`) is the
  *only* place bootstrap is awaited, and nothing awaits `shutdown` there; `shutdown` is checked only
  in `main_loop` (`process.py:204`). So even with `to_thread`, `await create_deps` blocks until the
  embed thread finishes (~10s), *then* `main_loop` exits. To hit the ≤2s target, bootstrap must be
  *raced* against `shutdown` and cancelled:
  ```python
  bootstrap = asyncio.create_task(create_deps(...))
  done, _ = await asyncio.wait(
      {bootstrap, asyncio.create_task(shutdown.wait())},
      return_when=asyncio.FIRST_COMPLETED,
  )
  if not bootstrap.done():
      bootstrap.cancel()
      logger.info("shutdown during bootstrap — exiting")
      pid_file.unlink(missing_ok=True)  # explicit: os._exit skips `finally`
      os._exit(0)                        # skip the asyncio.run executor join (uncancellable worker)
  deps = bootstrap.result()              # bootstrap won the race — proceed to main_loop
  ```
- **(iii) The thread must not wedge process exit** — `cancel()` unwinds only the awaiting coroutine;
  the `to_thread` worker keeps running the C-level socket read, and `asyncio.run` *joins* the default
  executor at teardown. A single wedged cold embed therefore keeps the process alive past the
  cooperative return, re-burning part of `stop_daemon`'s grace. The cold-start cost lands on the
  *first* embed call (model load), so a "shutdown check between files" in `_sync_canon_dir`
  (`core.py:212`) does **not** bound the cold-start hang — it only helps a batch of many *warm*
  embeds. This sub-requirement is what forces the choice in OQ1.

**Resolved route (Gate 1) — Route C: offload-with-own-connection + race + `os._exit` on interrupt.**
Routes (a) offload-with-bounded-timeout and (b) async-client are both **rejected** — see the Gate 1
source findings in Scope (sqlite thread-affinity, interleaved embed/sqlite writes, cold-start timeout
regression). Route C:
- **(i) Loop responsiveness** — wrap the whole bootstrap index-sync (canon + memory) in a single
  `asyncio.to_thread` worker that opens its **own** short-lived `IndexStore`/connection, created and
  used wholly within that thread (so `check_same_thread=True` is satisfied). The existing sync index
  API and the `timeout=30.0` embed are unchanged. The same DB file is fine — sqlite file-locking
  covers the transient second connection, and the loop's connection isn't writing during bootstrap.
- **(ii) Cooperative race against shutdown** — race `create_deps` against `shutdown` and cancel on
  stop (the `asyncio.wait(..., FIRST_COMPLETED)` construct above). With the embed now off the loop,
  `await create_deps` yields, so the race actually fires.
- **(iii) No wedge at teardown** — a `to_thread` worker is uncancellable and `asyncio.run` joins the
  default executor at teardown, so a cold embed would still pin exit to ~10s. On a shutdown that wins
  the race during bootstrap, the daemon therefore: logs the interrupt, unlinks the PID file (its
  `finally`/explicit cleanup), then calls **`os._exit(0)`** to skip the executor join. This is safe
  here — the PID file is already removed and per-embed DB writes are committed in
  `EmbeddingService.embed`, so nothing critical is lost; a partial index resumes next start via
  `needs_reindex` (`core.py:218`). The worker's connection + socket die with the process.
2. **Tighten `stop_daemon` grace (`process.py:105-111`).** Once shutdown is prompt, the 10s SIGTERM
   grace is overlong for the common case. Keep a grace (a genuinely wedged embed past the cooperative
   check still needs SIGKILL) but shorten it / make it config-driven. Decision in OQ2.

The daemon loop itself is already correct — `_loop.py:117,141` wake on `shutdown` immediately — so no
change there.

## Tasks

### ✓ DONE TASK-1 — Make bootstrap embed loop-responsive, raced against shutdown, and non-wedging
- **files:** `co_cli/daemons/dream/process.py`, `co_cli/bootstrap/core.py` (Route C — `co_cli/index/`
  internals stay untouched)
- **prerequisites:** OQ1 resolved → Route C (Gate 1)
- **scope note:** Route C must meet all three sub-requirements — (i) the index-sync runs in an
  `asyncio.to_thread` worker with its **own** `IndexStore` connection (sqlite thread-affinity), (ii)
  `create_deps` raced against `shutdown` and cancelled on stop (`process.py:203`), (iii) on
  shutdown-during-bootstrap, unlink the PID file then `os._exit(0)` so the uncancellable worker can't
  pin `asyncio.run` teardown. Embed `timeout=30.0` is unchanged — do **not** introduce a short
  bootstrap timeout (regresses normal cold-start indexing).
- **done_when:** with a cold embedding backend, a SIGTERM sent immediately after the PID file appears
  causes the daemon to exit cleanly in ≤ ~2s — the PID file is removed (explicitly on the
  bootstrap-interrupt branch before `os._exit`; via the normal `finally` when shutdown arrives after
  bootstrap) — with no "force-killed … did not respond to SIGTERM in 10s" log AND no process lingering
  past the cooperative return; canon + memory still index on a normal start; a stop mid-bootstrap
  leaves indexing resumable (next start re-indexes via `needs_reindex`).
- **success_signal:** `co dream start` then an immediate `co dream stop` returns "daemon stopped"
  (not "force-killed") within a couple of seconds even on a cold embedding backend.

### ✓ DONE TASK-2 — Correct the bootstrap-shutdown comment + tighten stop grace
- **files:** `co_cli/daemons/dream/process.py`
- **prerequisites:** TASK-1
- **done_when:** the `process.py:165-166` comment states the real behavior (loop stays responsive
  because the blocking index-sync is offloaded to a worker thread and `create_deps` is raced against
  shutdown); `stop_daemon`'s SIGTERM→SIGKILL grace is shortened to a fixed **3s constant** (OQ2 — not
  config), with the SIGKILL fallback retained for a genuinely wedged process.
- **success_signal:** N/A (comment + constant).

### ✓ DONE TASK-3 — Daemon tests assert prompt clean shutdown
- **files:** `tests/integration/test_daemon_lifecycle.py`, `tests/integration/test_daemon_crash_recovery.py`
- **prerequisites:** TASK-1
- **cold-path requirement:** these tests must exercise the **cold embedding backend** — do NOT add
  embedder warm-up. `ensure_ollama_warm` (`tests/_ollama.py:32`) warms only the agent LLM, not the
  embed model/service, so the cold path is the realistic default; warming the embedder here would
  mask the very regression these tests lock. (Agent-LLM warm-up, if needed for the review job, still
  goes outside any `asyncio.timeout` per policy.)
- **done_when:** `test_stop_daemon_terminates_process` asserts the daemon exits via its own clean path
  (PID file gone without `stop_daemon` having to escalate to SIGKILL — i.e. the "daemon stopped"
  branch, not "force-killed") within a bound comfortably under the grace window; both tests'
  wall-clock drops materially from the ~13s/~27s baseline. Real-process, real-signal, no mocks.
- **success_signal:** N/A (tests).

## Testing
Real-process integration: start the daemon against a **cold** embedding backend, stop it, assert
prompt clean exit (no SIGKILL escalation) and bounded wall-clock. Reproduce the original hang first
(pre-fix) to lock the regression. Run fail-fast, tee'd:
`uv run pytest -x tests/integration/test_daemon_lifecycle.py tests/integration/test_daemon_crash_recovery.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-daemon.log`.

## Open Questions — RESOLVED (Gate 1, 2026-06-11)
1. **Async embed client (b) vs offload+race+bounded-timeout (a)?** **A (lead): neither — Route C.**
   Both original routes were rejected at Gate 1 against the source: (a)'s `to_thread` breaks on sqlite
   thread-affinity (`store.py:149`, `check_same_thread=True`) and its bounded timeout regresses normal
   cold-start indexing; (b) requires the full IndexStore sync→async refactor (out of scope) because
   the embed is interleaved with sqlite writes on one connection (`store.py:321-334`). Route C =
   offload the whole index-sync into a `to_thread` worker with its **own** connection + race
   `create_deps` against shutdown + `os._exit(0)` on bootstrap-interrupt, embed `timeout=30.0`
   unchanged. See High-Level Design + Scope findings.
2. **`stop_daemon` grace — fixed value or config?** **A (lead): fixed 3s constant, not config.**
   Cooperative exit is now ≤2s; 3s covers it with margin. A config knob is unjustified sprawl (co
   favors explicit constants; nothing suggests anyone tunes this). SIGKILL fallback retained.
3. **Move canon/memory bootstrap sync out of `create_deps`?** **A (lead): no — rejected.** Relocating
   the sync to `main_loop`'s first iteration does not remove blocking I/O from the loop thread (the
   embed is still sync) — it just moves the same hang. It also breaks `create_deps`'s "deps fully
   ready before loop" contract for *all* consumers (CLI included), far more blast radius than the bug
   warrants. Fix in place via Route C.

## Final — Team Lead

Root cause is code-grounded (faulthandler stack + line citations). The daemon loop logic itself is
already correct — this is a bootstrap-I/O-on-the-loop defect plus an overlong stop grace that masks
it as a force-kill, and it affects the default detached daemon, not just `--foreground`.

### Gate 1 — PASSED (PO + TL, 2026-06-11)

Right problem ✓ (RCA re-verified against source — sqlite, embed, signal-handler, grace all confirmed).
Correct scope ✓ (bootstrap responsiveness, not embed-latency). OQ1/OQ2/OQ3 resolved.

Gate-1 source review **rejected both originally-proposed routes** and selected **Route C**:
- Route (a) `to_thread`-the-sync-call breaks on **sqlite thread-affinity** (`store.py:149`,
  `check_same_thread=True`), and its bounded-timeout escape **regresses normal cold-start indexing**.
- Route (b) async-client requires the **full IndexStore sync→async refactor** (out of scope) because
  the embed is interleaved with sqlite writes on one connection (`store.py:321-334`).
- **Route C** = offload the whole index-sync into an `asyncio.to_thread` worker with its **own**
  `IndexStore` connection + race `create_deps` against `shutdown` + **`os._exit(0)`** on
  bootstrap-interrupt; embed `timeout=30.0` unchanged. Touches only `process.py` + `core.py` (scope
  shrank — `co_cli/index/` internals untouched). Ambition: **full ≤2s fix** (user decision).
- OQ2 → fixed **3s** grace constant (not config). OQ3 → **rejected** (relocating the sync doesn't
  remove the blocking I/O).

> **Approved — ready for dev.** Run: `/orchestrate-dev dream-daemon-bootstrap-shutdown-hang`.

## Delivery Summary — 2026-06-11

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | cold-bootstrap SIGTERM → cooperative clean exit ≤~2s, no force-kill, indexing resumable | ✓ pass |
| TASK-2 | `process.py:165` comment matches real behavior; stop grace = `STOP_GRACE_SECONDS` 3s constant; SIGKILL fallback retained | ✓ pass |
| TASK-3 | both tests assert cooperative shutdown (not SIGKILL); wall-clock drops materially | ✓ pass |

**Implementation (Route C):**
- `co_cli/bootstrap/core.py` — new private `_sync_indexes_offthread`; `create_deps` now `await asyncio.to_thread(...)`s the canon+memory index-sync on a worker thread that opens its **own** short-lived `IndexStore` (sqlite thread-affinity). `co_cli/index/` internals untouched; embed `timeout=30.0` unchanged (no cold-start regression).
- `co_cli/daemons/dream/process.py` — `_run_foreground` races `create_deps` against `shutdown` (`asyncio.wait(FIRST_COMPLETED)`); a mid-bootstrap stop cancels bootstrap, unlinks PID, and `os._exit(0)` (skips the executor join on the uncancellable embed worker). `_run_foreground` docstring corrected. Added `STOP_GRACE_SECONDS = 3.0`; `stop_daemon` grace 10s→3s; force-kill message updated.
- Shared with REPL: `create_deps`'s offload applies to both callers; `on_status` is thread-safe in both bootstrap contexts (daemon→`logger.info`; REPL bootstrap→`console.print`, `_app is None`). Verified.

**Discovery during dev (RCA, resolved):** the first test run force-killed even though the daemon's own log proved it `os._exit`'d cleanly in ~1s. Root cause was a **test harness artifact**, not a code bug: the `--foreground` Popen was the daemon's parent and never reaped it, so the cleanly-exited process lingered as a **zombie** that `is_pid_live` (`os.kill(pid, 0)`) reads as alive. Production spawns the daemon *detached* (launcher exits → daemon reparents to init, reaped immediately). Fix: both tests now spawn via the detached launcher (production path) — no zombie, and a more faithful harness. No production code touched for this; `stop_daemon`'s liveness check is correct for production (init reaps).

**Tests:** scoped — 21 passed, 0 failed (5 daemon integration + 16 bootstrap/canon-recall). Wall-clock: `test_stop_daemon_terminates_process` 13s→2.7s; `test_queued_kick_processed_after_daemon_restart` 27s→6.5s. Manual cold-start repro: clean exit ~0.9s, log confirms `"shutdown during bootstrap — exiting"`.
**Doc Sync:** fixed — `docs/specs/dream.md` (stop grace 10s→3s in 3 places; startup sequence + new "Bootstrap responsiveness" paragraph; `create_deps` daemon-path row).

**Overall: DELIVERED**
All three tasks pass `done_when`, lint clean, scoped tests green, doc sync clean. Note for review-impl: the daemon tests are real-process/real-signal and exercise the cold embedding path (no embedder warm-up by design).

## Implementation Review — 2026-06-11

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | cold-bootstrap SIGTERM → cooperative clean exit ≤~2s, no force-kill, indexing resumable | ✓ pass | `process.py:216-223` races `create_deps` against `shutdown` via `asyncio.wait(FIRST_COMPLETED)`, cancels bootstrap, unlinks PID, `os._exit(0)`; `core.py:447-449` `await asyncio.to_thread(_sync_indexes_offthread, …)`; `core.py:251-280` worker opens its **own** `IndexStore` (sqlite thread-affinity) — embed `timeout=30.0` untouched. Manual cold repro: `daemon stopped` in 1.6s wall-clock (incl. `uv run` startup; grace loop sub-second). |
| TASK-2 | comment matches real behavior; grace = 3s constant; SIGKILL retained | ✓ pass | `process.py:171-181` docstring corrected (offload + race + os._exit); `process.py:38` `STOP_GRACE_SECONDS = 3.0`; `process.py:112` grace loop `range(int(STOP_GRACE/0.5))`; `process.py:118-125` SIGKILL fallback + message `…in {STOP_GRACE_SECONDS:g}s`. |
| TASK-3 | both tests assert cooperative shutdown (not SIGKILL); wall-clock drops materially | ✓ pass | `test_daemon_lifecycle.py` asserts `"daemon stopped" in out` and `stop_elapsed < STOP_GRACE_SECONDS`; both tests switch to the detached launcher (production path) to avoid the zombie-reaping artifact. Measured: stop 13s→2.8s, crash-recovery 27s→6.5s. |

### Issues Found & Fixed
No blocking issues found. No code changed during review (no auto-fix loop needed).

| Observation | Where | Severity | Resolution |
|---|---|---|---|
| `RuntimeError`-only catch around the offload still matches prior memory-sync semantics; canon sync swallows its own exceptions (`core.py:198-200`) so moving it into the worker changes no error path | `core.py:447-452` | non-issue | Verified, no change |
| Lingering `--origin=repro` foreground daemon (pid 93930) from the dev's manual cold-start repro; current PID file does not point to it (`co dream status` → `running: false`) | environment, not code | env note | Left as-is (coworker/dev repro artifact); safe to `kill` |
| review-impl Phase 7 lists `co status` — not a command in this project | skill, not code | doc-of-skill | Used `co dream status` instead |
| Full-suite emitted 1 `PytestUnraisableExceptionWarning` (`BaseSubprocessTransport.__del__` → "Event loop is closed") | not this plan's diff (tool subprocess paths) | pre-existing, benign | **RCA'd + fixed** (see below) |

#### Out-of-plan finding RCA'd & fixed: subprocess-transport teardown warning
- **Mechanism (verified against the real `ShellBackend`):** asyncio closes a subprocess transport in two steps — `close()` schedules `_call_connection_lost` via `loop.call_soon()`, and `_closed` only flips once that callback runs. After `run_command` (and the timeout/`kill_process_tree` path) the transport is `is_closing=True` but its close callback is still **pending**. pytest's function-scoped loop (`asyncio_default_fixture_loop_scope = "function"`) closes the instant the test coroutine returns; if GC collects the transport after that, `__del__` re-calls `close()` → `call_soon()` on a dead loop → the warning.
- **Production impact: none.** The production loop is long-lived (`asyncio.run` for the app), so the close callback always runs on a live loop. This is structurally a per-test-loop artifact.
- **Not from this plan:** the daemon diff spawns no asyncio subprocess; the daemon tests use sync `subprocess.Popen`. Transports originate in the tool paths (`shell_backend`, `files/read`, `files/write`, MCP stdio). `background.py` already hardened its own long-lived path (`_close_process_transport` + `await asyncio.sleep(0)`).
- **Non-deterministic:** fired once in full run 1 (681 tests), zero times in run 2 (tracemalloc, 421s), and not reproducible in three focused harnesses (default `ThreadedChildWatcher` usually reaps the transport off-thread before GC).
- **Fix (user-chosen):** a narrow `filterwarnings` ignore in `pyproject.toml` scoped to this exact `BaseSubprocessTransport.__del__` teardown warning — definitionally removes it without masking other unraisable exceptions and without editing production hot paths for a test-only race. Regex validated against the real message; pytest accepts the config; lint clean.

### Tests
- Command: `uv run pytest -x -q` (full suite)
- Result: **681 passed, 0 failed in 170.36s (2:50)**
- Scoped first: daemon integration (5 passed: stop 2.79s, crash-recovery 6.40s) + bootstrap/canon/memory (16 passed, 0.37s)
- Logs: `.pytest-logs/*-daemon.log`, `*-bootstrap.log`, full-suite via background task `b2rbni48k`

### Behavioral Verification
- `co dream status` (pre): `running: false` — clean start state
- `co dream start` → immediate `co dream stop`: returned **`daemon stopped`** (not "force-killed") in 1.6s wall-clock — `success_signal` verified
- `co dream status` (post): `running: false`, PID file removed
- (`co status` from the skill template does not exist here; `co dream status` is the relevant surface.)

### Suite run-time note (raised at review)
The daemon plan **did not slow** the suite — it made its own two tests ~31s faster (13s+27s → 2.8s+6.5s). The full-suite 170s/2:50 is dominated by ~12 pre-existing **real-LLM** integration tests unchanged by this plan (`test_real_turn_with_tool_call_populates_model_requests` 27s, `length_retry` 21s, `tool_selection_shell_git_status` 17s, compaction summarization 11s, two `tool_call_functional` ~10s each). The offthread change adds only a second cheap `IndexStore` open (sqlite connect + schema, no network/model-load — `store.py:134-152`) plus a `to_thread` dispatch per bootstrap: sub-10ms, negligible across the suite. Any apparent count/time growth vs an older baseline is from **concurrent in-flight work** present as untracked tests (`tests/test_flow_scanned_pdf.py`, `tests/test_flow_skill_office.py`), not from this plan.

### Overall: PASS
All three `done_when` met with file:line evidence, full suite green, behavioral `success_signal` confirmed, lint clean. No blocking findings.
