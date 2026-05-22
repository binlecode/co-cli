# online-reviewer-and-daemon-mvp

## Context

Co's in-session reviewer fires LLM work as `asyncio.create_task` forks from `_post_turn_hook` in the REPL process (`co_cli/main.py:269`). The current `_drain_and_cleanup` (`co_cli/main.py:149`) also calls `_maybe_run_dream_cycle` inline at session end (`memory/dream.py:run_dream_cycle`). Three structural problems on local-Ollama-single-GPU setups (and to a lesser extent on rate-limited cloud setups):

1. **GPU contention with the main agent.** Reviewer's LLM call hits the same Ollama instance the main agent uses. Ollama serializes requests at the model-server level. When reviewer is in-flight and the user starts the next turn, main-agent latency spikes user-visibly.

2. **Skip-and-preserve-counter destroys windowing.** The single-in-flight guard at `main.py:293-300` skips fires when a previous task is still in flight, and the counter is preserved for the next eligible turn. The "every N turns of fresh signal" window is lost — the next fire processes turns 0..M where M can be much larger than N.

3. **Task duration vs window duration is a structural mismatch.** If a review takes longer than the time between threshold trips, reviews back up. Raising thresholds delays the mismatch but doesn't eliminate it. The only structural fix is a queue with deferred execution (yield to main agent's activity, drain when idle).

**Pivot:** reviewer logic moves out of REPL into a daemon process with a disk-backed queue. Background-worker pattern: REPL fires LLM calls on demand without coordination; daemon dequeues work, calls LLM with per-call timeout, retries with backoff on timeout. Neither process knows the other exists — both compete for Ollama as an opaque shared resource, and the daemon's retry/backoff handles contention without process-state coupling.

- No GPU contention as a structural failure → daemon's timeout + retry-with-backoff makes review calls best-effort; foreground REPL calls are never gated on background state
- No window loss → each threshold trip queues a discrete work item with its `persisted_message_count`
- No structural mismatch → queue absorbs back-pressure; failed retries move to `dream/queue/failed/` for inspection

This plan ships **both** the reviewer counter/prompt/recall-metric refactor AND the daemon process MVP — they're coupled (problems 1 and 3 are *structural* failures that only the out-of-process daemon resolves; reverting to in-process keeps them unfixed). The counter split and recall metrics on their own would address problem 2 but not 1 or 3. Internal task ordering keeps the recall metrics + counter split landable behind the existing spawn until the daemon tasks land, so incremental rollout *within* the plan is possible — one plan, ordered tasks, single ship.

**Builds on** `docs/exec-plans/completed/2026-05-19-080633-session-review-counter-simplify.md` (already shipped), which dropped the `ToolCallPart` filter from the counter (verified: `_post_turn_hook` at `main.py:289` already bumps by `turn_iteration_count`), renamed `tool_iterations` → `llm_iterations`, and raised the default `review_nudge_interval` from 5 → 10. This plan's two-counter split is the per-tier follow-up the simplify plan flagged as deferred.

**Companion:** `docs/exec-plans/active/2026-05-20-010811-dream-housekeeping.md` (Plan 2) extends this daemon with merge/decay/curator absorption and the 24h scheduled tick.

**Predecessor daemon plans withdrawn (2026-05-21).** The earlier SQLite-jobs daemon design (`2026-03-31-171615-24x7-daemon.md` + `2026-04-01-163505-daemon-util-{1,2,3,4}*.md`) was deleted per `feedback_plans_withdrawn_delete`. Util-1 (knowledge compaction) is genuinely superseded by Plan 2 (`2026-05-20-010811-dream-housekeeping.md`). 24x7-daemon + util-2/3/4 (deferred-task, async-poller, heavy-batch) were user-facing job-execution features and are now actively withdrawn — not deferred. The dream daemon is single-purpose (self-improvement reviewer + housekeeping) and does not subsume user-submitted background jobs.

### Current state — verified against source

- `co_cli/main.py:_post_turn_hook` (line 269) increments `iterations_since_review += turn_iteration_count`; single-in-flight guard on `background_review_task`.
- `co_cli/main.py:_drain_and_cleanup` (line 149) cancels `background_review_task` AND calls `_maybe_run_dream_cycle` (`memory/dream.py:run_dream_cycle`).
- `co_cli/main.py:_maybe_run_session_review` (line 191) calls `_maybe_run_curator(deps)` at line 224.
- `co_cli/main.py:_curator_gate_passes` (line 227) and `_maybe_run_curator` (line 243) handle interval-gated curator dispatch.
- `co_cli/deps.py:CoSessionState` has `iterations_since_review: int` (line 158) and `background_review_task: asyncio.Task | None` (line 162). Also `auto_approve_skill_ops` / `auto_approve_knowledge_ops` flags set by `fork_deps_for_reviewer` (lines 323-324) — **dead flags**: never read by `is_auto_approved` (`tools/approvals.py:166`).
- `co_cli/memory/item.py:MemoryItem` **already has** `last_recalled: str | None` (line 73) and `recall_count: int = 0` (line 74). Only `recall_days: list[str]` is new (stored as ISO-date strings).
- `co_cli/memory/frontmatter.py:memory_item_to_frontmatter` already round-trips `last_recalled` + `recall_count`. New field needs an entry there + lazy default in `_coerce_fields`. Note: `frontmatter.py:77` drops empty-list fields (`if value:`), which naturally suppresses `recall_days=[]` from serialization.
- `co_cli/tools/memory/manage.py:memory_manage` action enum: `Literal["create", "append", "replace", "delete"]` (line 41).
- `co_cli/tools/system/skills.py:skill_manage` action enum: `Literal["create", "edit", "patch", "delete", "write_file", "remove_file"]` (line 308).
- `co_cli/tools/memory/recall.py:memory_search` is at line 151; tool returns `ToolReturn` with `results` list — recall side-effect would wrap inside this function before return.
- **Skill usage tracking already exists** at `co_cli/skills/usage.py` as a per-`user_skills_dir` sidecar (`.usage.json`) with `use_count`, `view_count`, `patch_count`, `last_used_at`, `last_viewed_at`, `last_patched_at`. `skill_view` and the slash dispatch already call `bump_view`/`bump_use`. **Plan extends the sidecar; does not create a parallel frontmatter system.**
- `co_cli/commands/core.py:107` is the slash-skill name dispatch site (`skill = ctx.deps.skill_index.get(name); ... return DelegateToAgent(...)`). `co_cli/commands/skills.py` is the `/skills <subcmd>` dispatcher (list/check/lint/curator/review) — a different surface.
- `co_cli/skills/session_review.py:SESSION_REVIEW_SPEC` (line 56) tool surface: `memory_view, memory_search, memory_manage, skill_view, skill_manage`.
- `co_cli/agent/build.py:build_task_agent` (line 63) is the daemon-side agent constructor. Line 113 registers tools with `requires_approval=False` — **this is how daemon writes bypass approval**, not the dead `auto_approve_*` flags.
- `co_cli/skills/skill_types.py:SkillInfo` is a frozen runtime descriptor — recall tracking lives in the sidecar, not on `SkillInfo`.
- `co_cli/config/skills.py` has `review_enabled`, `review_nudge_interval`, `usage_tracking_enabled`, `curator_enabled`, `curator_interval_hours`. No `dream` config module exists yet.
- `co_cli/bootstrap/core.py` is the system-startup orchestrator. Auto-spawn hook fits here.
- **Stale tests:** `tests/test_flow_post_turn_hook.py`, `tests/test_flow_review_background.py`, `tests/test_flow_session_review.py`, `tests/test_flow_session_review_counter.py`, `tests/test_flow_exit_cleanup_review.py` all reference symbols this plan deletes. They are addressed in TASK-24 below.
- `co_cli/commands/memory.py:131` has a `/memory dream` subcommand that imports `run_dream_cycle` directly — survives this plan untouched.

## Problem & Outcome

**Problem.** The in-process reviewer model produces three structural failures: (1) GPU contention degrades main-agent latency, (2) skip-and-preserve-counter destroys the every-N-turns information window, (3) task duration vs threshold-trip interval can back up reviews unboundedly.

**Outcome.** Reviewer LLM work runs out-of-process in a per-`CO_HOME` daemon, gated by REPL idle, fed by a durable disk-backed queue. Two domain counters (memory + skill) trigger independent KICKs; session end always fires both. Recall metrics flow back into items at query time, providing the signal Plan 2's housekeeping will consume.

**Failure cost.** Without this fix, every REPL session on Ollama experiences user-visible latency spikes during review windows; reviews silently back up or miss windows when sessions are dense; and the move to recall-informed housekeeping (Plan 2) cannot proceed because there is no recall signal on items. On cloud/rate-limited setups the latency cost is smaller but the window-loss and back-pressure failures persist.

## Scope

### In scope

1. **Two-counter split** in `CoSessionState`: `turns_since_memory_review` (bumped +1/turn), `iters_since_skill_review` (bumped +`turn_iteration_count`/turn). Both updates in `_post_turn_hook`. **Rationale for unit asymmetry:** memory tracks user-intent signal (~1 per turn — what the user asked for); skill tracks agent-action signal (~tools + reasoning steps per turn — what the agent *did*). Conflating the units would either over-fire skill reviews on chatty users or under-fire memory reviews on tool-heavy turns.
2. **Two domain-scoped nudge intervals** with peer-aligned defaults: `skills.review_memory_nudge_interval` = 10 turns; `skills.review_skill_nudge_interval` = 10 iterations.
3. **KICK-based reviewer dispatch.** Threshold trips and session end send `REVIEW <domain> <session_id> <persisted_message_count>` to the dream daemon over Unix socket, backed by a durable disk-based queue file (file is authoritative; socket is wake-up nudge).
4. **Session-end always-fire** at REPL shutdown: two KICKs (memory + skill) regardless of counter state.
5. **Two separate review prompts** (memory + skill). No combined prompt. Each domain has its own agent spec inside the daemon.
6. **Inline-tool-use resets** — domain-scoped:
   - `memory_manage(action ∈ {create, append, replace})` → `turns_since_memory_review = 0`
   - `skill_manage(action ∈ {create, edit, patch})` → `iters_since_skill_review = 0`
   No crossover. `delete` does not reset (delete is not a harvest signal). `write_file`/`remove_file` are co-located-file ops, not skill-content changes — they do not reset.
7. **Dream daemon process MVP** — minimum surface to execute reviewer KICKs:
   - Per-`CO_HOME` daemon (PID file, Unix socket, advisory startup flock)
   - REPL auto-spawn on bootstrap when `dream.enabled=true`, with **first-spawn user-visible notice and provenance in `co dream status`** (mission-alignment, see Behavioral Constraints).
   - CLI subcommands: `co dream [start|status|stop]` — minimal MVP surface. `tail` and `config` deferred to Plan 2 or whenever an operational case appears.
   - IPC: `STATUS`, `STOP`, `REVIEW`
   - Disk-backed queue at `$CO_HOME/daemons/dream/queue/`
   - Worker loop: dequeue → process with per-call timeout → on timeout, increment attempt count and sleep `retry_backoff_seconds` → after `max_retry_attempts`, move file to `dream/queue/failed/`
   - Reviewer execution: load transcript up to `persisted_message_count`, run domain agent constructed via `build_task_agent` / `run_standalone` (which is what bypasses approval), write items
8. **Recall metric fields** — domain-routed by existing storage shape:
   - **Memory items** keep frontmatter as the recall store. Existing: `recall_count: int`, `last_recalled: str | None` (kept; not renamed). **New:** `recall_days: list[str]` (deduped ISO-date strings so cadence can be measured, not just total hits). Side-effect on `memory_search` updates these and round-trips through `render_memory_item_file` + `atomic_write_text`. Lazy default `recall_days = []` for backward-compat loads.
   - **Skill items** extend the existing `co_cli/skills/usage.py` sidecar with `recall_days: list[str]` per record. `skill_view` (already calls `bump_view`/`bump_use`) and the slash dispatch update `recall_days`. **No parallel frontmatter system on skills.** This preserves the single-source-of-truth invariant; Plan 2 housekeeping consumes the sidecar.
9. **Decouple curator from session_review** call chain (call-site removal only; `curator.py` + `curator_prompts.py` module deletion deferred to Plan 2). **Why this can't wait:** TASK-7 deletes the entire `_maybe_run_session_review` function — the call to `_maybe_run_curator` lives inside it. The decouple is load-bearing for Plan 1, not gratuitous prep.
10. **Remove session-end inline dream cycle** (`_maybe_run_dream_cycle` call at `main.py:167`). The session-end always-fire KICKs replace it. `co_cli/memory/dream.py` module stays (Plan 2 absorbs); `_maybe_run_dream_cycle` *wrapper* in `main.py` is also deleted as part of TASK-4 since it has no remaining caller (the direct call from `commands/memory.py:131` to `run_dream_cycle` is unaffected).
11. **Delete dead approval flags.** `CoSessionState.auto_approve_skill_ops` and `auto_approve_knowledge_ops` are never read by `is_auto_approved`. They survive only because `fork_deps_for_reviewer` writes them. Delete the flags and the `fork_deps_for_reviewer` assignments. Daemon approval bypass is via `build_task_agent`'s `requires_approval=False`, not via runtime flags.
12. **Migrate or retire stale flow tests** (`tests/test_flow_post_turn_hook.py`, `test_flow_review_background.py`, `test_flow_session_review.py`, `test_flow_session_review_counter.py`, `test_flow_exit_cleanup_review.py`).

### Out of scope (Plan 2 territory or later)

- **No merge phase.** Plan 2.
- **No decay phase.** Plan 2.
- **No 24h scheduled timer.** Daemon's only trigger source in this plan is the socket-driven KICK + initial queue drain on startup. Plan 2 adds the timer.
- **No `co dream run` subcommand.** Plan 2 adds it once there's merge/decay to manually trigger.
- **No `co dream tail` / `co dream config`.** Deferred until an operational case appears; `tail -f $CO_HOME/logs/dream/*.log` covers the immediate need.
- **No transcript reading beyond per-KICK queued items.** Daemon scans no directories on its own initiative; it only processes work items REPL explicitly queued.
- **No historical backfill.** Pre-enable sessions accepted as not-extracted. Aligned with hermes.
- **No two-tier store.** One-tier durable; recall metrics are usage signals, not a promotion gate.
- **No removal of curator module.** Only the call-site chain is removed; module stays untouched until Plan 2 absorbs.
- **No removal of `co_cli/memory/dream.py`.** Only the session-end call site and the `_maybe_run_dream_cycle` wrapper are removed; the actual `run_dream_cycle` implementation in `memory/dream.py` stays (Plan 2 absorbs merge/decay into the daemon, and `commands/memory.py` still uses it).
- **No combined memory+skill prompts** anywhere.
- **No `co dream pause` / `resume` / `restart`** subcommands. Start/stop is enough.
- **No daemon-side queue coalescing** (e.g., "if a newer REVIEW arrives for the same session+domain, drop the older one"). Each KICK becomes one work item. Optimization deferred.
- **No rename of existing `MemoryItem.last_recalled` → `last_recalled_at`** (zero-backward-compat rule means rename costs a frontmatter migration; deferred unless Open Question Q3 says otherwise).
- **No lock-mediated recall-counter writes.** Recall metrics are accepted as best-effort with possible lost updates under concurrency — see Behavioral Constraint. Plan 2 housekeeping consumes recall as an order-of-magnitude signal, not exact counts.
- **No in-memory recall accumulation with periodic flush.** Recall persistence is per-search-synchronous; the disk cost (5-10 markdown rewrites per query) is accepted as a deliberate design choice. Re-evaluation only if TASK-20 benchmarks show a regression.
- **No spec writes from plan tasks.** `/sync-doc` is auto-invoked by `/orchestrate-dev` and handles spec updates post-delivery.

## Behavioral Constraints

- **Recall counter writes are best-effort with possible lost updates under concurrency.** Read-modify-write on a memory item's frontmatter (or on the skill usage sidecar) is *not* serialized: two concurrent REPLs or one REPL + daemon both invoking `memory_search`/`skill_view` on the same item can lose one increment. We accept this as deliberate. Plan 2 housekeeping consumes `recall_count` as a "did this get used in the last N days" signal, not an exact ledger; `recall_days` (deduped) is the cadence signal and is more robust to lost-update because day strings collide rather than increment. Document at the call site; do not introduce file locks. If Plan 2 housekeeping later needs exact counts, switch the projection to an `IndexStore`-backed `UPDATE … SET recall_count = recall_count + 1` (SQLite transaction semantics make that race-free); out of scope here.
- **Recall persistence is per-search-synchronous.** Every `memory_search` hit triggers a markdown rewrite; every `skill_view`/`/skill-name` invocation triggers a sidecar rewrite. Acceptance criterion: 5-10 markdown writes per query is acceptable cost for the value of recall signal. No in-memory accumulation, no batched flush.
- **`persisted_message_count` is a message count, not a turn count.** Each JSONL record in `sessions/<id>.jsonl` is one message; a single user turn produces multiple messages (ModelRequest + ModelResponse + tool outputs). KICK payloads carry `persisted_message_count`, IPC `REVIEW` line carries the same int, and `load_transcript(path, max_message_count=...)` truncates at that record. Naming this `turn_index` would invite truncation bugs.
- **Daemon-side review agents must run inside `run_standalone` / `build_task_agent`.** That is the only documented seam where tool registrations carry `requires_approval=False`. Daemon code paths must never call a REPL-toolset-built agent: doing so would block waiting for an approval that no frontend can ever answer.
- **Memory item save side-effect must use `atomic_write_text` at the rename step.** Already the project primitive. Lost updates from concurrent writers are accepted (see above); torn writes are not.
- **`persisted_message_count` is authoritative.** KICK payload's `persisted_message_count` = `deps.runtime.persisted_message_count` at the moment of dispatch. Daemon reads the transcript JSONL but truncates at the matching record so view is consistent even while REPL is still appending.
- **File durability > socket reachability.** REPL writes the KICK file first (atomic rename), then nudges the socket. If socket is down, file remains; daemon picks it up at next startup via initial queue scan.
- **Counter resets immediately on KICK fire.** The queue is the back-pressure layer; the REPL does not wait. If the daemon backs up, the queue grows; no in-process gating.
- **Background-worker contention model.** REPL and daemon are independent processes with no cross-process coordination. Both compete for Ollama as an opaque shared resource (Ollama serializes per model; no priority API). Foreground REPL calls fire on demand and surface latency to the user as normal interactive variance. Daemon calls run with `dream.review_timeout_seconds` per-call timeout — on timeout, the queue file's attempt counter increments and the worker sleeps `dream.retry_backoff_seconds` before trying the next file. After `dream.max_retry_attempts`, the file moves to `dream/queue/failed/` for inspection. No heartbeat file, no process-state coupling, no idle gate.
- **Auto-spawn is inspectable across four surfaces.** Mission §"Trusted" — explicit approval boundary, inspectable state. The daemon's existence surfaces at:
  1. **First-spawn notice (one-shot).** On first auto-spawn of a `CO_HOME` (no prior `dream.pid`), REPL prints: `[dream] daemon started in background. 'co dream status' to inspect; 'co dream stop' to stop.`
  2. **REPL welcome banner (every startup).** One-line `Dream:` row alongside `Memory:` / `Tools:` / `Dir:`. Three states: `✓ running  queue: N` (accent color), `disabled` (dim), `enabled but daemon not running` (yellow). Banner reads queue dir directly + best-effort socket `STATUS` with ~200ms timeout — must never stall startup.
  3. **`/dream` slash command (on demand).** Read-only inspection in the REPL. Queries the existing daemon over the socket; never spawns a process. When daemon is down, reports state + on-disk queue depth + guidance.
  4. **`co dream status` (bash).** Full JSON: `pid`, `uptime_seconds`, `queue_depth`, `current_item`, `attempts_pending`, `failed_count`, `spawn_origin` (`"repl-autospawn"` / `"manual"`), `spawn_session_id`. Authoritative source of truth.
- **Rollout posture.** Ship at `dream.enabled = false`. Document in `CHANGELOG.md` (`/ship` auto-handles version bump + changelog). Plan 2's ship is the first time `enabled = true` is plausible; default-flip decision lives with Plan 2 (Q4).
- **POSIX-only daemon footprint.** `fcntl.flock`, Unix sockets, double-fork detach. co-cli is darwin/linux-first; no Windows path. Mark the boundary in `_process.py` with an explicit `# POSIX-only` comment block.
- **Package-private boundary at `co_cli/daemons/dream/`.** All `_*.py` modules are package-private. Any symbol consumed from `co_cli/commands/dream.py` must be imported via a non-underscore module or function (CLAUDE.md `_prefix.py` rule).
- **No `noqa`/`type: ignore` without justification** (CLAUDE.md).
- **Zero backward compatibility** (memory `feedback_zero_backward_compat`): no aliases for renamed fields, no compat shims. Lazy-default on load for new `recall_days` field is acceptable (it's a field addition, not a rename).
- **`__init__.py` is docstring-only** under the new `co_cli/daemons/dream/` package (CLAUDE.md).
- **No util/helpers modules** (memory `feedback_no_util_modules`): each daemon submodule has a single concern (`_process`, `_loop`, `_ipc`, `_queue`, `_state`, `_deps`, `_reviewer`).
- **Single-file kicks; no batching** in this plan (no coalescing).
- **REPL-side dispatch is non-blocking and best-effort on socket.** No await on the daemon side from REPL.

## High-Level Design

### Architecture overview

```
                  ┌──────────┐
                  │   User   │  ─── submits turns, sees output ───┐
                  └──────────┘                                     │
                                                                   ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │                  Ollama  (per-model serializer)                          │
   │           No coordination API. No priority. No queue introspection.      │
   └──▲───────────────────────────────────────────────────────────▲──────────┘
      │ fires on demand;                                          │ asyncio.timeout(review_timeout_s);
      │ surfaces latency to user as normal variance               │ TimeoutError → retry with backoff
      │                                                            │
┌─────┴────────────────────────────────┐         ┌─────────────────┴──────────────────────┐
│  REPL  (co chat)                      │         │  dream daemon  (co dream start)        │
│                                       │         │                                         │
│  _post_turn_hook                      │         │  main_loop                              │
│    turns_since_memory_review += 1     │         │    initial_drain(queue/)  (cold start)  │
│    iters_since_skill_review += iters  │         │    while not shutting_down:             │
│    if ≥ threshold:                    │         │      msg = socket.recv()                │
│      _send_review_kick(domain)        │         │      STATUS → return JSON {pid, uptime, │
│      counter = 0                      │         │        queue_depth, current_item,       │
│                                       │         │        attempts_pending, failed_count,  │
│  tool inline reset                    │         │        spawn_origin, spawn_session_id}  │
│    memory_manage(create|append|       │         │      STOP   → drain current, exit       │
│      replace) → memory ctr = 0        │         │      REVIEW → _drain_queue()            │
│    skill_manage(create|edit|patch)    │         │                                         │
│      → skill ctr = 0                  │         │  _drain_queue                           │
│                                       │         │    while files: pick oldest             │
│  _drain_and_cleanup (session end)     │         │      attempts = read_from_payload(f)    │
│    fire both KICKs unconditionally    │         │      try:                               │
│                                       │         │        async with asyncio.timeout(T):   │
│  memory_search / skill_view /         │         │          load_transcript(               │
│    /skill-name slash                  │         │            sessions/<id>.jsonl,         │
│    → side-effect: bump recall         │         │            max_message_count=N)         │
│      (memory: file rewrite;           │         │          run_standalone(domain_spec)    │
│       skill: usage sidecar)           │         │            requires_approval=False      │
│                                       │         │            source_type=SESSION_REVIEW   │
│  bootstrap.maybe_autospawn            │         │        os.replace(f → done/)            │
│    flock dream.lock                   │         │      except (TimeoutError, OSError,     │
│    if not pid_live:                   │         │              RuntimeError):             │
│      Popen("co dream start            │         │        attempts += 1                    │
│        --detached                     │         │        if attempts ≥ MAX_RETRY:         │
│        --origin=repl-autospawn        │         │          os.replace(f → failed/)        │
│        --session-id=<id>")            │         │        else:                            │
│      frontend.on_status(notice)       │         │          write_attempts(f, attempts)    │
│                                       │         │          await sleep(retry_backoff_s)   │
└────────────┬──────────────────────────┘         └──────────────────────┬─────────────────┘
             │                                                            │
             │  ─── all coupling is filesystem-mediated ───                │
             ▼                                                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│                                       $CO_HOME                                            │
│                                                                                            │
│   daemons/                                                                                 │
│     dream.pid       pid + spawn_origin + spawn_session_id   (daemon writes; REPL reads)    │
│     dream.sock      Unix domain socket                                                     │
│                     ──── REPL → daemon: best-effort nudge on each KICK                     │
│                          "REVIEW <domain> <session_id> <persisted_message_count>\n"        │
│                          (socket failure is non-fatal — file is durable)                   │
│                     ──── operator → daemon: STATUS, STOP                                   │
│     dream.lock      advisory flock — auto-spawn race guard (POSIX-only)                    │
│     dream/                                                                                 │
│       queue/                                                                               │
│         <ts>-<uuid>.json    payload: {domain, session_id, persisted_message_count,         │
│                                       created_at, attempts}                                │
│            ▲ REPL: atomic write (.tmp → os.replace into queue/)                            │
│            ▼ daemon: sort + per-file timeout → os.replace into done/ or failed/            │
│         done/<ts>-<uuid>.json       successful reviews   (audit retention)                 │
│         failed/<ts>-<uuid>.json     exhausted retries    (inspect via co dream status)     │
│                                                                                            │
│   sessions/<id>.jsonl    REPL appends one message per record;                              │
│                          daemon reads load_transcript(max_message_count=N)                 │
│                          (KICK's N pins a consistent view even while REPL appends)         │
│                                                                                            │
│   memory/*.md            REPL writes via memory_manage tool;                               │
│                          daemon writes via reviewer agent (source_type=SESSION_REVIEW);    │
│                          both update recall_count / last_recalled / recall_days            │
│                          on memory_search hits.   Lost updates accepted.                   │
│                                                                                            │
│   skills/<name>/         REPL writes via skill_manage; daemon writes via reviewer agent.   │
│   skills/.usage.json     Both bump use/view/recall_days on read paths.   Lost updates ok.  │
│                                                                                            │
│   logs/dream/<ts>.log    daemon stdout/stderr                                              │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Key properties:**

1. **No process-state coupling.** REPL never asks "is daemon busy?" Daemon never asks "is REPL busy?" Each side just talks to its own resources.
2. **Filesystem is the only durable channel.** Socket is a best-effort nudge — drop it, daemon picks up the file on next start.
3. **Ollama is the only shared external resource.** It serializes on its own; daemon copes via timeout+retry+backoff; REPL copes by being interactive (user already accepts variance).
4. **Two domain counters in REPL, two domain SPECs in daemon.** Memory and skill review are fully independent — own counters, own KICKs, own queue items, own agents.
5. **Daemon-only write provenance:** memory items the daemon creates carry `source_type=SESSION_REVIEW` so Plan 2 can tell auto-extracts from user writes.
6. **Auto-spawn is one-way.** Bootstrap fires-and-forgets a detached process. Daemon doesn't know which REPL spawned it — just records `spawn_origin` + `spawn_session_id` in `dream.pid` for inspection.

### Counter increment plumbing

```python
def _post_turn_hook(deps, message_history, turn_iteration_count):
    if deps is None:
        return
    settings = deps.config.skills
    if not settings.review_enabled or deps.model is None:
        return

    deps.session.turns_since_memory_review += 1
    deps.session.iters_since_skill_review += turn_iteration_count

    _maybe_kick_memory_review(deps)
    _maybe_kick_skill_review(deps)
```

### KICK dispatch (replaces in-process task spawn)

```python
def _maybe_kick_memory_review(deps):
    settings = deps.config.skills
    if deps.session.turns_since_memory_review < settings.review_memory_nudge_interval:
        return
    msg_count = deps.runtime.persisted_message_count
    deps.session.turns_since_memory_review = 0
    _send_review_kick(deps, domain="memory", persisted_message_count=msg_count)

def _maybe_kick_skill_review(deps):
    settings = deps.config.skills
    if deps.session.iters_since_skill_review < settings.review_skill_nudge_interval:
        return
    msg_count = deps.runtime.persisted_message_count
    deps.session.iters_since_skill_review = 0
    _send_review_kick(deps, domain="skill", persisted_message_count=msg_count)
```

No single-in-flight guard in REPL. The queue itself is the back-pressure layer.

`_send_review_kick` performs two steps:
1. Atomic-write a KICK JSON file to `$CO_HOME/daemons/dream/queue/<timestamp>-<uuid>.json` with payload `{domain, session_id, persisted_message_count, created_at}`
2. Best-effort send `REVIEW <domain> <session_id> <persisted_message_count>\n` over `dream.sock` (swallow socket errors — file is durable)

### Session-end always-fire

In `_drain_and_cleanup`:

```python
async def _fire_session_end_kicks(deps):
    if not deps.config.skills.review_enabled or deps.model is None:
        return
    final = deps.runtime.persisted_message_count
    _send_review_kick(deps, domain="memory", persisted_message_count=final)
    _send_review_kick(deps, domain="skill", persisted_message_count=final)
```

Both fire regardless of counter state. Counter resets after dispatch. This replaces both `background_review_task` cancellation and the `_maybe_run_dream_cycle` call currently at `main.py:161-167`. The `_maybe_run_dream_cycle` wrapper itself is deleted (no remaining caller; `commands/memory.py:131` calls `run_dream_cycle` directly).

### Inline-tool-use resets

```python
# co_cli/tools/memory/manage.py — at the end of successful create/append/replace
if action in ("create", "append", "replace"):
    ctx.deps.session.turns_since_memory_review = 0

# co_cli/tools/system/skills.py — at the end of successful create/edit/patch
if action in ("create", "edit", "patch"):
    ctx.deps.session.iters_since_skill_review = 0
```

Domain-scoped — memory tool only touches memory counter; skill tool only touches skill counter. `delete`/`write_file`/`remove_file` do NOT reset.

### Recall metric fields

Memory item state after this plan (in `MemoryItem`):

| Field | Status | Type |
|---|---|---|
| `recall_count: int = 0` | exists | int |
| `last_recalled: str \| None = None` | exists (kept) | ISO-8601 string |
| `recall_days: list[str]` | **new** | list of ISO-date strings (`"YYYY-MM-DD"`), deduped |

Side-effect at the query-tool layer (in `memory_search` after building `memory_results`, before returning `ToolReturn`):

```python
def _record_memory_recall(deps, item_paths: list[Path]) -> None:
    now = datetime.now(UTC)
    today_iso = now.date().isoformat()
    for path in item_paths:
        try:
            item = load_memory_item(path)
        except Exception:
            continue
        item.recall_count += 1
        item.last_recalled = now.isoformat().replace("+00:00", "Z")
        if today_iso not in item.recall_days:
            item.recall_days.append(today_iso)
        atomic_write_text(path, render_memory_item_file(item))
```

Skill item state — extend the existing `co_cli/skills/usage.py` sidecar:

```jsonc
{
  "version": 1,
  "skills": {
    "<name>": {
      "use_count": int,
      "view_count": int,
      "patch_count": int,
      "recall_days": ["2026-05-20", "2026-05-21"],   // NEW
      "created_at": ISO8601,
      "last_used_at": ISO8601 | null,
      "last_viewed_at": ISO8601 | null,
      "last_patched_at": ISO8601 | null,
      "state": "active",
      "pinned": bool
    }
  }
}
```

A new `bump_recall(deps, name)` helper in `usage.py` appends today's ISO date to `recall_days` (deduped) without touching the existing counters. Called from `skill_view`, the slash dispatch at `commands/core.py:107`, and any other recall-grade access point. Both `bump_use` and `bump_view` continue to fire on their existing paths; `recall_days` is the cadence signal layered on top, not a replacement.

Lazy migration: items without `recall_days` default to `[]` on load. Lost updates accepted per Behavioral Constraint.

### Dream daemon process model

Per-`CO_HOME` scoping. All paths under `USER_DIR`:

```
$CO_HOME/
  daemons/
    dream.pid                       # PID + liveness
    dream.sock                      # Unix socket
    dream.lock                      # advisory startup flock (POSIX-only)
    dream/
      queue/
        <timestamp>-<uuid>.json     # pending KICK
        done/
          <timestamp>-<uuid>.json   # processed (kept N days for audit)
        failed/
          <timestamp>-<uuid>.json   # exhausted retry attempts
  logs/
    dream/
      <timestamp>.log
```

**Lifecycle:**
- `co dream start` → check PID liveness → flock `dream.lock` → double-fork detach → write PID → bind socket → start worker loop
- `co dream stop` → connect to socket, send `STOP\n`; daemon drains current item and exits
- SIGTERM fallback if socket unreachable
- Stale PID cleanup on next start (`kill -0` check)

**REPL auto-spawn** in `co_cli/bootstrap/core.py`:

```python
def maybe_autospawn_dream(deps, frontend):
    if not deps.config.dream.enabled: return
    if os.environ.get("CO_DREAM_NO_AUTOSPAWN"): return
    with flock_or_skip(dream_lock_path):
        if dream_pid_is_live(): return
        subprocess.Popen([
            "co", "dream", "start", "--detached",
            "--origin=repl-autospawn",
            f"--session-id={deps.session.session_path.stem}",
        ], ...)
        frontend.on_status(
            "[dream] daemon started in background. "
            "'co dream status' to inspect; 'co dream stop' to stop."
        )
```

The `--origin` and `--session-id` are persisted to `dream.pid` (or a sibling `dream.meta.json`) so `co dream status` can report `spawn_origin` + `spawn_session_id` even across daemon restarts. Re-prompt on subsequent auto-spawn from the same session is suppressed by the live-PID check.

### IPC protocol

Line-based over Unix socket:

```
STATUS              → JSON: {pid, uptime_seconds, queue_depth, current_item, attempts_pending, failed_count, spawn_origin, spawn_session_id}
STOP                → ACK; daemon drains current item + exits
REVIEW <d> <s> <n>  → ACK; logical "wake up" signal — daemon scans queue/ for new files
                       (n = persisted_message_count)
```

`REVIEW` is a wake-up nudge, not the work itself — the work data lives on disk in the queue file. The line payload is informational (helps logging) but the file is authoritative.

### Worker loop (Plan 1 scope)

```python
async def main_loop(deps, queue_dir, socket):
    await _initial_drain(deps, queue_dir)
    while not _shutting_down:
        msg = await socket.receive_one()
        if msg.startswith("STOP"): break
        if msg.startswith("REVIEW"): await _drain_queue(deps, queue_dir)
        # Plan 2 adds: scheduled_tick branch

async def _drain_queue(deps, queue_dir):
    cfg = deps.config.dream
    while True:
        files = sorted(queue_dir.glob("*.json"))
        if not files: break
        item = files[0]
        attempts = _read_attempts(item)
        try:
            async with asyncio.timeout(cfg.review_timeout_seconds):
                await _process_kick_file(deps, item)
            _move_to_done(item)
        except (TimeoutError, OSError, RuntimeError) as exc:
            attempts += 1
            if attempts >= cfg.max_retry_attempts:
                _move_to_failed(item, last_error=str(exc))
            else:
                _write_attempts(item, attempts)
                await asyncio.sleep(cfg.retry_backoff_seconds)
```

`_read_attempts` / `_write_attempts` persist the attempt counter in the queue file's JSON payload (`attempts: int`, defaults 0) so retries survive daemon restart. `_move_to_done` and `_move_to_failed` are `os.replace` into sibling directories.

### Contention model — no idle gate

REPL and daemon never coordinate process state. REPL fires LLM calls on demand; daemon dequeues work, calls LLM with `dream.review_timeout_seconds` per-call timeout. If REPL and daemon happen to race for Ollama, Ollama serializes — REPL's foreground turn may see occasional latency, daemon's review may time out and retry later. Both are acceptable variance in MVP. No `repl.activity` file, no mtime polling, no `_wait_for_repl_idle`.

### Reviewer execution in the daemon

```python
async def _process_review(deps, domain, session_id, persisted_message_count):
    transcript_path = deps.sessions_dir / f"{session_id}.jsonl"
    if not transcript_path.exists():
        logger.warning("review: session file missing %s", session_id)
        return
    messages = load_transcript(transcript_path, max_message_count=persisted_message_count)
    if domain == "memory":
        await _run_memory_review(deps, messages)
    elif domain == "skill":
        await _run_skill_review(deps, messages)
```

Both `_run_memory_review` and `_run_skill_review` route through `run_standalone(SPEC, deps, prompt, ...)` from `co_cli.agent.run`, which uses `build_task_agent` (`co_cli/agent/build.py:113`) — that is the seam where tool registrations get `requires_approval=False`. **Daemon code must never call a REPL-toolset-built agent**; any future review tool that escapes this seam will block forever waiting for an approval that no frontend can answer (see Behavioral Constraints).

Two domain specs in `co_cli/daemons/dream/_reviewer.py`:

| Spec | Tool surface | Prompt source |
|---|---|---|
| `MEMORY_REVIEW_SPEC` | `memory_search`, `memory_manage` | `prompts/memory_review.md` |
| `SKILL_REVIEW_SPEC` | `skill_view`, `skill_manage`, `memory_search` (+ `include_skill_manifest=True` for catalog injection — matches existing `SESSION_REVIEW_SPEC` pattern) | `prompts/skill_review.md` |

`load_transcript(..., max_message_count=N)` truncates the parse at the JSONL line index N — guaranteed-consistent view even while REPL is still appending to the file.

### Daemon's CoDeps builder

```python
def build_codeps_for_daemon(co_home: Path) -> CoDeps:
    """Slimmer variant of REPL's deps builder for daemon process use."""
    # Loads: settings, memory_store, skill_store, index_store, model client
    # Excludes: UI/REPL state, display, frontend, session/conversation state
    ...
```

Lives in `co_cli/daemons/dream/_deps.py`. Risk note in Risks section about drift from REPL's deps builder.

### Decouple curator from session_review

TASK-7 deletes the entire `_maybe_run_session_review` function — the call to `_maybe_run_curator` at `main.py:224` lives inside it, so the decouple is forced, not optional. Also delete `_maybe_run_curator` and `_curator_gate_passes` (`main.py:227-266`). `co_cli/skills/curator.py` and `curator_prompts.py` remain in place untouched — Plan 2 absorbs them. The unreachable code is intentional and called out in this plan.

### Prompts move to daemon module

```
co_cli/daemons/dream/prompts/
  memory_review.md          # focused on persona/preferences/references
  skill_review.md           # focused on corrections/techniques/umbrella discipline
```

Memory prompt modeled after hermes `_MEMORY_REVIEW_PROMPT` (~10 lines, surgical). Skill prompt modeled after hermes `_SKILL_REVIEW_PROMPT` (~75 lines, umbrella discipline + preference order).

Delete `co_cli/skills/session_review_prompts.py` once content has moved.

### Delete dead approval flags

`CoSessionState.auto_approve_skill_ops` and `auto_approve_knowledge_ops` (`co_cli/deps.py:220-221` per current state — verify exact line during impl) and the corresponding assignments in `fork_deps_for_reviewer` (`co_cli/deps.py:323-324`) are dead: `is_auto_approved` (`tools/approvals.py:166`) never reads them. The approval bypass for the in-process reviewer comes from `build_task_agent` registering tools with `requires_approval=False`. Removing the dead flags as part of this plan prevents future readers from re-discovering the same bypass-flag confusion.

## Tasks

### REPL-side counter + KICK refactor

- [ ] **TASK-1.** Split `CoSessionState` review counters and delete dead approval flags.
  - files: `co_cli/deps.py`
  - done_when: `iterations_since_review` and `background_review_task` removed; `turns_since_memory_review: int = 0` and `iters_since_skill_review: int = 0` added with docstrings; `auto_approve_skill_ops` and `auto_approve_knowledge_ops` fields removed; corresponding assignments in `fork_deps_for_reviewer` removed.
  - success_signal: N/A (refactor).
  - prerequisites: none.

- [ ] **TASK-2.** Add `_send_review_kick` helper in `co_cli/main.py`.
  - files: `co_cli/main.py`
  - done_when: helper writes a JSON KICK file under `$CO_HOME/daemons/dream/queue/<ts>-<uuid>.json` via the project's atomic-write primitive `co_cli.fileio.atomic.atomic_write_text` (write to `<name>.tmp` sibling → fsync → `os.replace` into `<name>.json`) so the daemon never observes a torn file; payload: `domain`, `session_id`, `persisted_message_count`, `created_at`; then best-effort socket nudge `REVIEW <domain> <session_id> <persisted_message_count>\n` to `dream.sock`; socket errors are swallowed. The daemon-side queue scan in `_queue.py` (TASK-15) must skip any `.tmp` files left mid-write.
  - success_signal: KICK file is present on disk after fire; daemon (when up) picks it up; deliberately-interrupted write leaves only `<name>.tmp`, never partial `<name>.json`.
  - prerequisites: TASK-19 (DREAM_QUEUE_DIR constant).

- [ ] **TASK-3.** Refactor `_post_turn_hook` (`co_cli/main.py:269`) to two-counter + two-KICK dispatch.
  - files: `co_cli/main.py`
  - done_when: increments memory by +1 and skill by +`turn_iteration_count` after the `review_enabled`/`deps.model` early-return guards; calls `_maybe_kick_memory_review` and `_maybe_kick_skill_review`; no `background_review_task` spawn; old single-counter codepath removed. No heartbeat file write — REPL does not signal daemon process state.
  - success_signal: after 10 turns with both flags on, two KICK files (one memory, one skill) appear in the queue dir.
  - prerequisites: TASK-1, TASK-2.

- [ ] **TASK-4.** Add `_fire_session_end_kicks` and wire into `_drain_and_cleanup` (`co_cli/main.py:149`).
  - files: `co_cli/main.py`
  - done_when: at shutdown, two KICKs (memory + skill) fire regardless of counter state; `background_review_task` cancellation block and `_maybe_run_dream_cycle` call are removed; `_maybe_run_session_review`, `_maybe_run_curator`, `_curator_gate_passes`, and `_maybe_run_dream_cycle` (the wrapper) are deleted.
  - success_signal: `Ctrl-D` out of a REPL session and observe both KICK files in queue dir; `grep -n _maybe_run_dream_cycle co_cli/main.py` returns nothing.
  - prerequisites: TASK-2.

- [ ] **TASK-5.** Domain-scoped counter reset in `memory_manage`.
  - files: `co_cli/tools/memory/manage.py`
  - done_when: after a successful `create | append | replace`, sets `ctx.deps.session.turns_since_memory_review = 0`. `delete` does NOT reset. Skill counter NOT touched.
  - success_signal: unit test `memory_manage(action="create", ...)` leaves skill counter unchanged and memory counter at 0.
  - prerequisites: TASK-1.

- [ ] **TASK-6.** Domain-scoped counter reset in `skill_manage`.
  - files: `co_cli/tools/system/skills.py`
  - done_when: after a successful `create | edit | patch`, sets `ctx.deps.session.iters_since_skill_review = 0`. `delete | write_file | remove_file` do NOT reset. Memory counter NOT touched.
  - success_signal: unit test `skill_manage(action="edit", ...)` leaves memory counter unchanged and skill counter at 0.
  - prerequisites: TASK-1.

- [ ] **TASK-7.** Delete in-process review specs and prompts module.
  - files: `co_cli/skills/session_review.py`, `co_cli/skills/session_review_prompts.py`
  - done_when: `SESSION_REVIEW_SPEC`, `run_session_review`, `_write_review_report`, `SessionReviewOutput`, `SessionReviewResult` removed; module deleted if nothing remains. `session_review_prompts.py` deleted (content moves in TASK-17). All imports updated; `co_cli/main.py` no longer references these symbols.
  - success_signal: `uv run python -c "from co_cli.skills.session_review import run_session_review"` raises ImportError.
  - prerequisites: TASK-4, TASK-16 (specs move to daemon module first).

### REPL-side recall metrics

- [ ] **TASK-8.** Add `recall_days` to `MemoryItem` schema and round-trip via frontmatter.
  - files: `co_cli/memory/item.py`, `co_cli/memory/frontmatter.py`
  - done_when: `MemoryItem.recall_days: list[str] = field(default_factory=list)` (stored as ISO-date strings to stay yaml-clean); `_coerce_fields` lazy-defaults missing field to `[]`; `memory_item_to_frontmatter` writes the field only when non-empty (current `frontmatter.py:77` drop-empty-list behavior already gives this for free — no code change required there).
  - success_signal: unit test writes a `MemoryItem` with `recall_days=["2026-05-20", "2026-05-21"]`, reloads, gets the same list back; item without the field loads with `recall_days=[]`.
  - prerequisites: none.

- [ ] **TASK-9.** Extend `co_cli/skills/usage.py` sidecar with `recall_days` and add `bump_recall`.
  - files: `co_cli/skills/usage.py`
  - done_when: `_new_record` includes `"recall_days": []`; `bump_recall(deps, name)` helper appends today's ISO date to the list (deduped); sidecar round-trips the new field through `read_records`/`write_records` (no schema-version bump — the lazy-default-on-read pattern handles existing sidecars without the field).
  - success_signal: unit test calls `bump_recall(deps, "test_skill")` twice in one day; reloads sidecar; sees `recall_days == ["YYYY-MM-DD"]` (deduped to one entry).
  - prerequisites: none.

- [ ] **TASK-10.** Recall-tracking side-effects on query tools.
  - files: `co_cli/tools/memory/recall.py`, `co_cli/tools/system/skills.py`, `co_cli/commands/core.py`
  - done_when: `memory_search` (after building `memory_results`, before `return tool_output(...)`) updates `recall_count`/`last_recalled`/`recall_days` for each returned hit via `load_memory_item` → mutate → `atomic_write_text(path, render_memory_item_file(item))`. `skill_view` adds `bump_recall(deps, name)` alongside the existing `bump_view` call (lines 65-69). `/skill-name` slash dispatch (`commands/core.py:107`, immediately after `skill = ctx.deps.skill_index.get(name)` succeeds) calls `bump_recall(deps, name)` before `return DelegateToAgent(...)`.
  - success_signal: search for a known memory item, then re-load it: `recall_count` is 1, `last_recalled` is today, `recall_days` contains today's ISO date. View a known skill via `skill_view`, then `cat $CO_HOME/skills/.usage.json` shows the skill's `recall_days` populated. Invoke `/skill-name`, observe same sidecar update.
  - prerequisites: TASK-8, TASK-9.

> Note: TASK-11 was merged into TASK-3 during C1 (see CD-m-2 decision). The numbering gap is intentional.

### REPL-side config + bootstrap

- [ ] **TASK-12.** Update `SkillsSettings` for two-counter nudge intervals.
  - files: `co_cli/config/skills.py`
  - done_when: `review_nudge_interval` removed (along with its env var mapping); `review_memory_nudge_interval: int = Field(default=10, ge=1)` and `review_skill_nudge_interval: int = Field(default=10, ge=1)` added; `SKILLS_ENV_MAP` updated with `CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL` and `CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL`. No alias for `review_nudge_interval`.
  - success_signal: `CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL=3 uv run co chat` triggers a memory KICK after 3 turns.
  - prerequisites: none.

- [ ] **TASK-13.** Add `co_cli/config/dream.py` (new daemon settings).
  - files: `co_cli/config/dream.py`, `co_cli/config/core.py` (wire into `Settings`)
  - done_when: `DreamSettings` exposes `enabled: bool = False`, `review_timeout_seconds: int = 120`, `retry_backoff_seconds: int = 30`, `max_retry_attempts: int = 3`; env vars `CO_DREAM_*`; field validation via Pydantic; integrated into `Settings`. No `idle_*` knobs.
  - success_signal: `CO_DREAM_ENABLED=true uv run co chat` enables auto-spawn check at bootstrap.
  - prerequisites: none.

- [ ] **TASK-14.** REPL auto-spawn hook at bootstrap, with first-spawn notice and origin metadata.
  - files: `co_cli/bootstrap/core.py`, `co_cli/daemons/dream/_process.py` (origin metadata read)
  - done_when: bootstrap calls `maybe_autospawn_dream(deps, frontend)` after `CoDeps` is built; respects `dream.enabled` config and `CO_DREAM_NO_AUTOSPAWN` env opt-out; uses `fcntl.flock` (POSIX-only — `# POSIX-only` comment block in `_process.py` marks the boundary) on `dream.lock` to serialize with concurrent REPLs; forks `co dream start --detached --origin=repl-autospawn --session-id=<id>`; when the live-PID check shows no daemon was already running, calls `frontend.on_status("[dream] daemon started in background. ...")`; daemon persists `spawn_origin` + `spawn_session_id` so `co dream status` reports provenance.
  - success_signal: see TASK-22 `tests/integration/test_auto_spawn_race.py` and `tests/integration/test_autospawn_notice.py`.
  - prerequisites: TASK-13, TASK-18, TASK-19.

### Daemon-side new module

- [ ] **TASK-15.** Create `co_cli/daemons/dream/` package skeleton.
  - files: `co_cli/daemons/__init__.py`, `co_cli/daemons/dream/__init__.py`, `co_cli/daemons/dream/_process.py`, `co_cli/daemons/dream/_loop.py`, `co_cli/daemons/dream/_ipc.py`, `co_cli/daemons/dream/_state.py`, `co_cli/daemons/dream/_queue.py`, `co_cli/daemons/dream/_deps.py`, `co_cli/daemons/dream/process.py` (public re-export surface for `commands/dream.py`)
  - done_when: package layout matches; both `__init__.py` files are docstring-only (no imports per CLAUDE.md); each `_*.py` module has a single concern; daemon entry point in `_process.py` does double-fork detach, PID/lock management, SIGTERM handler; `process.py` (non-underscore) re-exports just the public surface consumed by `commands/dream.py` (e.g., `start_daemon`, `stop_daemon`, `status_daemon`) — per CLAUDE.md `_prefix.py` rule. `_queue.py` queue-scan must skip any `<name>.tmp` files (KICK in-flight write durability — see TASK-2).
  - success_signal: `uv run co dream start --foreground` exits cleanly on SIGTERM and writes a PID file during the run; `from co_cli.daemons.dream.process import start_daemon` works without touching underscore-prefixed modules.
  - prerequisites: TASK-13, TASK-19.

- [ ] **TASK-16.** Reviewer specs + transcript loader extension.
  - files: `co_cli/daemons/dream/_reviewer.py`, `co_cli/session/persistence.py`
  - done_when:
    - `MEMORY_REVIEW_SPEC` (tools: `memory_search`, `memory_manage`) and `SKILL_REVIEW_SPEC` (tools: `skill_view`, `skill_manage`, `memory_search`; `include_skill_manifest=True` for catalog injection — matches the existing `SESSION_REVIEW_SPEC` pattern at `skills/session_review.py:59-65`) defined.
    - `_process_review`, `_run_memory_review`, `_run_skill_review` implemented.
    - Both review runs go through `run_standalone(...)` (which uses `build_task_agent` → `requires_approval=False`) so daemon writes bypass approval.
    - Daemon-side memory writes set `MemoryItem.source_type = SourceTypeEnum.SESSION_REVIEW` (new enum value `"session_review"`) so Plan 2 housekeeping can identify reviewer-extracted items. Provenance is set by the daemon, not the LLM — do not expose `source_type` on the `memory_manage` tool surface.
    - Extend `load_transcript` signature in `co_cli/session/persistence.py` from `load_transcript(path: Path) -> list[ModelMessage]` to `load_transcript(path: Path, *, max_message_count: int | None = None) -> list[ModelMessage]`. When `max_message_count` is provided, truncate at the matching JSONL line index. Existing callers (`memory/dream.py:165`, `commands/resume.py:87`, `tests/test_flow_session_persistence.py`, `tests/test_flow_compaction_session_rewrite.py`) keep current behavior with default `None`.
    - Verify `persist_session_history` is atomic per-message before claiming done.
  - success_signal: feed a fixture transcript + KICK payload; observe one memory item written by the memory review, one skill change by the skill review; daemon never blocks on approval. Separately: `load_transcript(path, max_message_count=N)` returns exactly N messages for a fixture file with ≥N messages; default-`None` call returns the full list unchanged.
  - prerequisites: TASK-15, TASK-17.

- [ ] **TASK-17.** Move + split review prompts.
  - files: `co_cli/daemons/dream/prompts/memory_review.md` (new), `co_cli/daemons/dream/prompts/skill_review.md` (new)
  - done_when: memory prompt focused on persona/preferences/references (modeled after hermes `_MEMORY_REVIEW_PROMPT`); skill prompt focused on corrections/techniques/umbrella discipline (modeled after hermes `_SKILL_REVIEW_PROMPT`); combined `SESSION_REVIEW_INSTRUCTIONS` content is fully relocated; no combined prompt remains.
  - success_signal: `_run_memory_review` invoked with a transcript yields only memory-domain writes; `_run_skill_review` yields only skill-domain writes (verified in UAT eval, TASK-23).
  - prerequisites: TASK-15.

- [ ] **TASK-18.** CLI subcommands (MVP surface only) + reusable socket client.
  - files: `co_cli/commands/dream.py` (new), `co_cli/commands/registry.py` (or wherever co's top-level command dispatch lives — verify during impl)
  - done_when: `co dream start [--foreground] [--detached] [--origin=<str>] [--session-id=<str>]`, `co dream status`, `co dream stop [--force]` registered under `co dream` command group. `tail` and `config` NOT added. `co dream status` JSON includes `pid`, `uptime_seconds`, `queue_depth`, `current_item`, `attempts_pending` (count of queue files with `attempts > 0`), `failed_count` (count of files in `failed/`), `spawn_origin`, `spawn_session_id`. Socket client is a reusable helper (`_socket_status(timeout_ms: int) -> dict | None`) — banner (TASK-25) and `/dream` slash (TASK-26) both consume it; connect+read timeouts are caller-supplied; returns `None` if daemon unreachable (never raises into caller).
  - success_signal: `uv run co dream status` returns JSON with provenance fields when daemon is up; clear error when down.
  - prerequisites: TASK-15.

- [ ] **TASK-19.** Daemon path constants.
  - files: `co_cli/config/core.py`
  - done_when: `DREAM_DAEMON_DIR = USER_DIR / "daemons" / "dream"`, `DREAM_PID_FILE`, `DREAM_SOCK`, `DREAM_LOCK`, `DREAM_QUEUE_DIR`, `DREAM_QUEUE_DONE_DIR`, `DREAM_QUEUE_FAILED_DIR` defined; all driven from `USER_DIR` (CLAUDE.md known-pitfall rule). No `REPL_ACTIVITY_FILE`.
  - success_signal: `uv run python -c "from co_cli.config.core import DREAM_QUEUE_DIR; print(DREAM_QUEUE_DIR)"` prints the right path under `CO_HOME`.
  - prerequisites: none.

### Tests

- [ ] **TASK-20.** REPL-side unit tests (new files).
  - files: `tests/main/test_post_turn_hook.py`, `tests/tools/memory/test_manage_resets.py`, `tests/tools/system/test_skill_manage_resets.py`, `tests/main/test_send_review_kick.py`, `tests/tools/memory/test_recall_metrics.py`, `tests/skills/test_usage_recall_days.py`
  - done_when: tests cover — counter delta correctness; domain-independent KICK firing (memory KICK at threshold even if skill counter is 0); domain-scoped tool-write reset (no crossover); session-end always-fires both KICKs; `_send_review_kick` writes file + sends socket message; socket failure is non-fatal; memory recall metrics update on `memory_search`; skill recall (sidecar `recall_days`) updates on `skill_view` and `/skill-name` slash; backward-compat load of items without `recall_days` and sidecars without `recall_days`.
  - success_signal: `uv run pytest tests/main/test_post_turn_hook.py tests/tools/memory/test_recall_metrics.py tests/skills/test_usage_recall_days.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-newtests.log` — all green; `-x` flag used per memory fail-fast rule.
  - prerequisites: TASK-1 through TASK-12, TASK-24.

- [ ] **TASK-21.** Daemon-side unit tests.
  - files: `tests/daemons/dream/test_queue.py`, `tests/daemons/dream/test_loop.py`, `tests/daemons/dream/test_timeout_retry.py`, `tests/daemons/dream/test_process.py`, `tests/daemons/dream/test_reviewer.py`
  - done_when: tests cover — queue file write/read/atomic-move roundtrip; worker drains queue in chronological order; per-call timeout fires when `_process_kick_file` exceeds `review_timeout_seconds`; on timeout, attempt counter on the queue file increments and worker sleeps `retry_backoff_seconds`; after `max_retry_attempts`, file moves to `dream/queue/failed/`; attempt counter survives daemon restart; `_process_review` truncates transcript at `persisted_message_count`; memory review fires `memory_*` tools only; skill review fires `skill_*` + `memory_search`; daemon never blocks on tool approval (assert via captured stdin); daemon STOP via socket exits cleanly; SIGTERM fallback; stale PID cleanup (write fake live PID → kill it → start daemon → claim).
  - success_signal: `uv run pytest tests/daemons/dream/ -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-daemon.log` — all green.
  - prerequisites: TASK-15 through TASK-18.

- [ ] **TASK-22.** Integration tests.
  - files: `tests/integration/test_review_kick_end_to_end.py`, `tests/integration/test_daemon_lifecycle.py`, `tests/integration/test_auto_spawn_race.py`, `tests/integration/test_autospawn_notice.py`, `tests/integration/test_multi_repl_kick.py`, `tests/integration/test_daemon_crash_recovery.py`
  - done_when: end-to-end test runs REPL through 10 turns → KICK fired → daemon processes → memory item written; daemon-down test fires KICK → file written → daemon starts later → processes; auto-spawn race (two concurrent REPL bootstraps → exactly one daemon spawns); autospawn-notice test asserts the visible REPL status line on first auto-spawn and asserts `co dream status` includes `spawn_origin: "repl-autospawn"`; multi-REPL against same `CO_HOME` both fire (N + M) KICKs → daemon queue contains exactly N + M files before drain (no coalescing, no swallow — enforces "single-file kicks; no batching" behavioral constraint) → daemon serializes processing → both transcripts handled; crash mid-process (daemon killed during `_process_review`) → restart re-processes the file (idempotent); SIGTERM grace exits within timeout.
  - success_signal: `uv run pytest tests/integration/ -k "review_kick or daemon_lifecycle or auto_spawn or autospawn_notice or multi_repl or daemon_crash" -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-integration.log` — all green.
  - prerequisites: TASK-1 through TASK-19, TASK-24.

- [ ] **TASK-23.** UAT eval — per-prompt extraction quality.
  - files: `evals/eval_domain_review.py` (new)
  - done_when: eval runs each domain's review on a representative fixture transcript with real model + real stores (per memory `feedback_eval_real_world_data`); asserts memory review extracts persona/prefs (no skill writes); skill review extracts corrections/techniques (no memory persona writes); proposed assertions reflect critical functionality, not a count target (memory `feedback_no_test_count_rule`); follows memory `feedback_ensure_ollama_warm` (warmup outside `asyncio.timeout`) and `feedback_call_timeout_no_cold_start` (per-call budgets cover warm latency only).
  - success_signal: `uv run python evals/eval_domain_review.py` prints PASS summary with per-domain extraction counts within expected ranges.
  - prerequisites: TASK-16, TASK-17.

- [ ] **TASK-24.** Migrate or retire stale flow tests.
  - files: `tests/test_flow_post_turn_hook.py`, `tests/test_flow_review_background.py`, `tests/test_flow_session_review.py`, `tests/test_flow_session_review_counter.py`, `tests/test_flow_exit_cleanup_review.py`
  - done_when: per-file decision applied — (a) delete (coverage moved to new TASK-20 file); (b) port to two-counter / KICK assertions; (c) merge into one of the new files. After this task, `uv run pytest --collect-only tests/test_flow_*.py 2>&1` produces no import errors and no references to deleted symbols (`iterations_since_review`, `background_review_task`, `review_nudge_interval`, `SESSION_REVIEW_SPEC`, `SESSION_REVIEW_INSTRUCTIONS`, `SessionReviewOutput`, `_write_review_report`, `auto_approve_skill_ops`, `auto_approve_knowledge_ops`).
  - success_signal: `grep -rn "iterations_since_review\|background_review_task\|review_nudge_interval\|SESSION_REVIEW_SPEC\|SESSION_REVIEW_INSTRUCTIONS\|SessionReviewOutput\|_write_review_report\|auto_approve_skill_ops\|auto_approve_knowledge_ops" tests/ 2>&1` returns nothing.
  - prerequisites: TASK-1, TASK-3, TASK-4, TASK-7, TASK-12.

### Inspectability surfaces (banner + slash)

- [ ] **TASK-25.** Extend welcome banner with `Dream:` line.
  - files: `co_cli/bootstrap/banner.py`
  - done_when: banner adds a `Dream:` row between `Tools:` and `Dir:` showing one of three states: (a) `[accent]✓ running[/accent]  queue: N` when socket `STATUS` succeeds; (b) `[dim]disabled[/dim]` when `deps.config.dream.enabled is False`; (c) `[yellow]enabled but daemon not running[/yellow]  queue: N (on disk)` when enabled but socket unreachable. Banner uses TASK-18's `_socket_status(timeout_ms=200)` helper — must never block startup; on timeout or socket failure, fall through to state (c). Queue depth in states (a) and (c) comes from `len(list(DREAM_QUEUE_DIR.glob("*.json")))` (skip `.tmp` files) — best-effort, no error on missing dir.
  - success_signal: `CO_DREAM_ENABLED=false uv run co chat` shows `Dream: disabled`; with daemon up, shows `✓ running`; with daemon killed via `kill -9` mid-session and REPL restarted, shows `enabled but daemon not running`.
  - prerequisites: TASK-13, TASK-18, TASK-19.

- [ ] **TASK-26.** `/dream` slash command (read-only inspection).
  - files: `co_cli/commands/dream.py` (extend TASK-18's file), `co_cli/commands/registry.py` (register slash dispatch)
  - done_when: `/dream` (no args) registered as a slash command; invokes TASK-18's `_socket_status(timeout_ms=500)`; renders the full status dict in the REPL via `display.core.console` (formatted labeled lines, not raw JSON). When daemon is down: prints state, on-disk queue depth, and guidance (`'co dream start' to start manually`, or for `dream.enabled=false`: `'set dream.enabled=true and restart co chat'`). Never spawns a process. No `/dream <subcommand>` in Plan 1 — single command only. Slash mirrors bash `co dream status`, deliberately not `co dream start`/`stop` (lifecycle stays in bash).
  - success_signal: in REPL, `/dream` with daemon up renders status; with daemon down + `dream.enabled=true`, prints `daemon not running` + queue depth + start guidance; with `dream.enabled=false`, prints `disabled` + enable guidance.
  - prerequisites: TASK-18.

- [ ] **TASK-27.** Banner + slash test coverage.
  - files: `tests/bootstrap/test_banner_dream_line.py`, `tests/commands/test_dream_slash.py`
  - done_when: banner test asserts the three states (running / disabled / enabled-but-down) by stubbing `_socket_status` and toggling `dream.enabled`; asserts banner does not stall (deliberately-hung socket fixture → assert total render <500ms). Slash test asserts the three rendering paths and asserts no `subprocess.Popen` call is made (verifies "never spawns a process" contract).
  - success_signal: `uv run pytest tests/bootstrap/test_banner_dream_line.py tests/commands/test_dream_slash.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-inspect.log` — all green.
  - prerequisites: TASK-25, TASK-26.

## Testing

| Test | Scope | Type |
|---|---|---|
| Counter delta correctness | `_post_turn_hook` increments memory by +1, skill by +`turn_iteration_count` | Unit |
| Domain-independent KICK firing | Memory KICKs at 10 turns even if skill counter is 0; vice versa | Unit |
| Domain-scoped tool-write reset | `memory_manage(create\|append\|replace)` resets memory counter only; `skill_manage(create\|edit\|patch)` resets skill counter only; no crossover; `delete`/`write_file`/`remove_file` do NOT reset | Unit |
| Session-end always-fires both KICKs | Both KICKs sent at REPL shutdown regardless of counter state | Integration |
| Memory recall updates | `memory_search` updates `recall_count`/`last_recalled`/`recall_days` on returned items; deduped per-day | Unit |
| Skill recall updates | `skill_view` and `/skill-name` slash dispatch call `bump_recall` → sidecar `recall_days` populated, deduped | Unit |
| Backward-compat item load | Items without `recall_days` load with `[]`; new field written on next save; sidecars without `recall_days` get `[]` on read | Unit |
| KICK file durability | KICK written atomically; daemon restart picks up unprocessed files | Integration |
| Socket nudge optional | Socket-down at KICK time still results in eventual processing on daemon restart | Integration |
| Per-call timeout | Worker times out when `_process_kick_file` exceeds `review_timeout_seconds` | Unit |
| Retry-with-backoff | On timeout, attempt counter increments and worker sleeps `retry_backoff_seconds` before next file | Unit |
| Exhausted retries → failed/ | After `max_retry_attempts`, file moves to `dream/queue/failed/`; `co dream status` surfaces `failed_count` | Unit |
| Attempt counter durability | Attempt counter on queue file survives daemon restart | Unit |
| Daemon process lifecycle | start → status → stop; PID cleanup; SIGTERM grace | Integration |
| Auto-spawn race | Two REPLs racing on bootstrap → exactly one daemon spawns (flock works) | Integration |
| Auto-spawn notice + provenance | First spawn prints `[dream] daemon started ...`; `co dream status` includes `spawn_origin` + `spawn_session_id` | Integration |
| Daemon approval bypass | Daemon-side review never blocks on tool approval; assert via captured stdin | Unit |
| End-to-end review | REPL turn → KICK → daemon → item written; assert content matches expected | Integration |
| Banner Dream line | Three states (running / disabled / enabled-but-down); render does not stall on hung socket (<500ms) | Unit |
| `/dream` slash inspection | Three rendering paths; never invokes `subprocess.Popen` | Unit |
| Stale flow tests cleared | `grep -rn "<deleted symbol set>" tests/` returns nothing | Smoke |
| Per-prompt extraction quality | Memory prompt extracts persona/prefs; skill prompt extracts corrections; no crossover | UAT eval |

Tests run via the project quality gates: `scripts/quality-gate.sh full` (lint + pytest). All pytest runs pipe to a timestamped log under `.pytest-logs/` per CLAUDE.md. Per memory `feedback_no_test_count_rule`, tests are proposed by critical functionality, not by count. Per memory fail-fast rule, pytest uses `-x` so the suite stops at the first failure.

## Open Questions

- **Q1 — older daemon plans. RESOLVED 2026-05-21.** Owner ruling: option (i) — predecessor SQLite-jobs daemon plans deleted (`24x7-daemon.md` + `daemon-util-{1,2,3,4}*.md`). Util-1 superseded by Plan 2; 24x7-daemon + util-2/3/4 withdrawn as user-facing features. Dream daemon is the sole daemon design in flight.
- **Q2 — `co_cli/memory/dream.py` retirement.** Plan 1 removes the wrapper call site but keeps `memory/dream.py`; Plan 2 absorbs merge/decay. Is keeping the module for ~1 plan-cycle acceptable, or should we mark it explicitly with a "DEPRECATED — superseded by daemons/dream/" header until Plan 2 deletes? `commands/memory.py:131` still imports `run_dream_cycle` directly, so the module remains reachable through that surface — not orphaned in the strict sense.
- **Q3 — `last_recalled` field naming.** Project policy is zero-backward-compat. The existing `MemoryItem.last_recalled: str` is acceptable as-is but Plan 2's housekeeping may benefit from a typed `datetime`. Do we rename now (with a hard frontmatter migration in `_coerce_fields`) or leave the string for Plan 2 to address? Default: leave (out of scope).
- **Q4 — `dream.enabled` default flip timing.** Plan ships with `False`. When does it flip to `True` by default? After Plan 2 ships and N weeks of opt-in soak? After a specific install threshold? Decision belongs with Plan 2 ship; surfaced here for visibility.

## Risks

- **Daemon process management complexity.** Double-fork, signal handling, socket cleanup, stale PID are common failure modes. Mitigation: model on well-trodden Python daemon patterns; comprehensive integration tests (TASK-22) for start/stop/crash recovery.
- **CoDeps build divergence.** Daemon builds its own deps via `build_codeps_for_daemon`; REPL evolves separately. Mitigation: keep daemon variant a thin wrapper around the REPL builder with UI/REPL fields disabled; periodic parity test asserting capability sets agree where they should.
- **Transcript tail race.** Daemon reads `sessions/<id>.jsonl` while REPL is still appending. The `persisted_message_count` contract: REPL has persisted up to message N before sending KICK; daemon truncates at the matching record. Verify `persist_session_history` flush semantics during impl (TASK-16); if writes aren't atomic per-message, address before claiming TASK-16 done_when.
- **Multi-REPL concurrent writes to memory items.** Read-modify-write race accepted as best-effort per Behavioral Constraint. `recall_count` may lose increments; `recall_days` deduplication makes lost-update degrade gracefully (one entry per day is the worst case, not zero). If Plan 2 housekeeping needs exact counts, project to `IndexStore` with SQLite `UPDATE … SET col = col + 1` semantics.
- **Ollama serialization can stall daemon under chatty users.** If user is constantly active, REPL keeps Ollama busy and daemon's review calls repeatedly time out → queue grows, retries pile up, items eventually land in `failed/`. Mitigation: `review_timeout_seconds` defaults generously; `failed/` surfaces visibility; if real usage shows starvation, raise timeout or lower nudge intervals so fewer KICKs fire during dense usage.
- **Foreground latency spikes during contention.** When daemon and REPL race for Ollama, REPL's turn may queue behind a review call. User sees occasional latency. Mitigation: accepted as MVP variance — review calls are typically much shorter than main-turn calls, so the worst-case delay is bounded by `review_timeout_seconds`. If observed contention becomes a real complaint, add a model-call-level mutex (cross-process lock acquired before any Ollama call) in a follow-up; this is the cleaner long-term layer for coordination but out of scope for MVP.
- **Tool-write reset over-suppresses on heavy-save sessions.** A session where foreground agent saves many items keeps the counter reset, delaying KICK indefinitely until a turn passes without a save. Desired behavior (harvest already happened); if cadence feels under-eager in practice, raise nudge intervals rather than weaken the reset.
- **Per-search recall persistence disk cost.** 5-10 markdown writes per search; accepted per Behavioral Constraint. If TASK-20 benchmarks surface a regression, re-evaluate (e.g., debounce per-item writes).
- **Queue file proliferation.** If daemon stays down for weeks while REPL fires KICKs daily, queue grows. Mitigation: surface queue depth in `co dream status` (TASK-18); warning threshold deferred to Plan 2.
- **Auto-spawn inspectability gap if notice is missed.** User dismissing the REPL status line on first spawn could miss the daemon's existence. Mitigation: provenance is also queryable via `co dream status` indefinitely; no dependence on user catching the one-shot notice.

## Implementation Footprint Summary

**Added:**
- `co_cli/daemons/dream/` — new module (process, loop, IPC, queue, state, deps builder, reviewer)
- `co_cli/daemons/dream/prompts/memory_review.md`
- `co_cli/daemons/dream/prompts/skill_review.md`
- `co_cli/commands/dream.py` — CLI subcommands (start/status/stop only) + `/dream` slash + reusable socket-status helper (`_socket_status`)
- `co_cli/config/dream.py` — daemon config knobs
- `MemoryItem.recall_days` field
- `usage.py` sidecar `recall_days` per-record + `bump_recall` helper
- KICK file queue format + writer/reader helpers (includes per-file `attempts` counter for retry semantics)
- `dream/queue/failed/` sibling directory for exhausted-retry files
- Tests under `tests/daemons/dream/`, `tests/integration/`, `tests/main/`, `tests/tools/memory/`, `tests/tools/system/`, `tests/skills/`, `tests/bootstrap/`, `tests/commands/`
- `evals/eval_domain_review.py`

**Refactored:**
- `co_cli/deps.py:CoSessionState` — drop `iterations_since_review` + `background_review_task` + `auto_approve_skill_ops` + `auto_approve_knowledge_ops`; add two domain counters; remove flag assignments from `fork_deps_for_reviewer`
- `co_cli/main.py:_post_turn_hook` — two-counter increment + KICK dispatch; remove in-process spawn
- `co_cli/main.py:_drain_and_cleanup` — session-end KICK dispatch; remove `background_review_task` cancel + `_maybe_run_dream_cycle` call
- `co_cli/main.py` — delete `_maybe_run_session_review`, `_maybe_run_curator`, `_curator_gate_passes`, `_maybe_run_dream_cycle` (the wrapper)
- `co_cli/skills/session_review.py` — delete entire `SESSION_REVIEW_SPEC` + helpers
- `co_cli/skills/usage.py` — `_new_record` adds `recall_days: []`; new `bump_recall` helper
- `co_cli/config/skills.py` — replace `review_nudge_interval` with two domain intervals; update `SKILLS_ENV_MAP`
- `co_cli/memory/item.py` — `recall_days` field + lazy default in `_coerce_fields`
- `co_cli/tools/memory/recall.py` — recall-tracking side-effect on `memory_search`
- `co_cli/tools/memory/manage.py` — domain-scoped counter reset on `create | append | replace`
- `co_cli/tools/system/skills.py` — `bump_recall` call alongside `bump_view`/`bump_use`; domain-scoped counter reset on `skill_manage(create|edit|patch)`
- `co_cli/commands/core.py` — `bump_recall` at slash-skill dispatch (line ~107)
- `co_cli/config/core.py` — daemon path constants
- `co_cli/bootstrap/core.py` — auto-spawn check with first-spawn notice
- `co_cli/bootstrap/banner.py` — `Dream:` status line (running / disabled / enabled-but-down)
- Five stale flow test files migrated/deleted (TASK-24)

**Deleted:**
- `co_cli/skills/session_review_prompts.py` — content moves to daemon module prompts/
- `co_cli/main.py:_maybe_run_curator` and `_curator_gate_passes` — call chain removed; curator module itself untouched until Plan 2
- `co_cli/main.py:_maybe_run_dream_cycle` (the wrapper) — no remaining caller after TASK-4
- `CoSessionState.auto_approve_skill_ops` / `auto_approve_knowledge_ops` — dead flags (Scope item 11)

**Unchanged:**
- `co_cli/skills/curator.py` and `curator_prompts.py` — wait for Plan 2 to absorb
- `co_cli/memory/dream.py` — wait for Plan 2 (merge/decay refactor); `run_dream_cycle` still called by `commands/memory.py:131`
- `co_cli/commands/memory.py` — `/memory dream` subcommand keeps its direct `run_dream_cycle` import
- `co_cli/memory/frontmatter.py` — empty-list-drop behavior at line 77 already round-trips `recall_days` correctly; no code change required

**Config knobs (Plan 1 final):**

| Knob | Default | Purpose |
|---|---|---|
| `skills.review_enabled` | (existing) | Master switch for reviewer |
| `skills.review_memory_nudge_interval` | 10 (turns) | Mid-session memory KICK trigger |
| `skills.review_skill_nudge_interval` | 10 (iterations) | Mid-session skill KICK trigger |
| `dream.enabled` | false | Master switch for the daemon |
| `dream.review_timeout_seconds` | 120 | Per-review LLM timeout (worker raises `TimeoutError`) |
| `dream.retry_backoff_seconds` | 30 | Sleep between retry attempts on timeout |
| `dream.max_retry_attempts` | 3 | After this many timeouts, move file to `dream/queue/failed/` |

**Removed (from earlier plans):** `skills.review_nudge_interval` (replaced by two domain knobs); old single-counter task handle (`CoSessionState.background_review_task`); dead `auto_approve_*` flags.

## Final — Team Lead

Plan approved. Both Core Dev and PO returned `Blocking: none` at Cycle C3.

> **Gate 1: PASSED 2026-05-21.** Sign-off after review by owner.
>
> Resolutions captured:
> - **Q1 (Gate 0) — closed.** Predecessor SQLite-jobs daemon plans deleted; dream daemon is the sole design in flight.
> - **Producer/consumer decoupling — confirmed.** `_send_review_kick` does not gate on `dream.enabled`; durable file queue absorbs producer/consumer liveness mismatch by design.
> - **Inspectability — extended to four surfaces** (banner / `/dream` slash / `co dream status` / first-spawn notice) — see TASK-25/26/27 and the "Auto-spawn is inspectable" Behavioral Constraint.
>
> Next step: `/orchestrate-dev online-reviewer-and-daemon-mvp`


