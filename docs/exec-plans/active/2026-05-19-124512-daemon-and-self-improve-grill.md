# daemon-and-self-improve-grill

## Problem

Stress-test co-cli's current daemon agent design and self-improvement runtime against the hermes findings:

- **No cross-session skill mining** in either repo. Both `session_review` (co) and `bg-review` (hermes) see one session at a time. Curator only consolidates existing skills, never discovers new ones from transcripts.
- **bg-review is an ad-hoc fork** in both repos. Hermes spawns a fresh `AIAgent` per nudge; co spawns a `TaskAgentSpec` fork per nudge. Neither maintains a long-lived review agent.
- **Curator is the only persistent-state self-improvement agent** in hermes. State file (`~/.hermes/logs/curator/`) gates re-runs across process restarts. Co's curator gate (`_curator_gate_passes`) also reads `last_run_at` from disk, but is only triggered after a session_review fires.
- **Hermes has a 60s cron-ticker daemon** in the gateway process that polls curator hourly + handles user-defined cron jobs as AIAgent forks. Co has no process-level ticker — all triggers are turn-driven.

The question to grill: given these findings, what's the right shape for co's background/self-improvement design? Which gaps are worth closing, which are intentional, and what's the architectural decision tree?

## Status
Grilling in progress — core architecture resolved; open sub-decisions remain.

---

## Open Decisions Resolved — 2026-05-19

### D1 + D5 + D6. RESOLVED — Dream's scope: offline-specific only, symmetric over memory and skill

- **Question:** Should dream be a superset of the in-session reviewer (do everything reviewer does plus offline), or only the offline-specific parts (mining, merging, decaying)?
- **Recommended:** Offline-specific only — keep the in-session reviewer untouched for current-session signal extraction; dream owns cross-session lifecycle.
- **Chosen:** Offline-specific only.
- **Why:** Avoids double-extraction; reviewer has hot context (live `message_history`), dream has cold context (transcripts on disk) — different inputs, different code paths. Aligns with hermes's bg-review + curator split while filling the cross-session mining gap hermes lacks.
- **Constraint:**
  - Curator's standalone module gets absorbed into dream (lifecycle state transitions + LLM consolidation move into dream's merge/decay phases). The asymmetric "memory has cross-session mining, skills don't" gap is closed by giving dream **symmetric phases over both domains**: memory-mine + memory-merge + memory-decay AND skill-mine + skill-merge + skill-decay.
  - In-session reviewer remains turn/iteration-nudged with separate prompts and counters (memory by turn, skill by iteration).

### D11.a. RESOLVED — `co dream` with no subcommand → `start`
- **Chosen:** `co dream` (bare) is an alias for `co dream start`.
- **Why:** Matches `co chat` ergonomic; start is the most common action.

### D11.b. RESOLVED — PID file + Unix socket IPC
- **Question:** PID-file alone is discovery, not IPC. What channel does the CLI use to talk to a running daemon?
- **Chosen:** PID file for liveness/ownership + Unix socket (`~/.co-cli/daemons/dream.sock`) for status/stop/pause/resume/kick. SIGTERM is the fallback stop path.
- **Why:** `co dream status` needs to return live state (uptime, current phase, next-decay ETA); signals can't return data. Socket is the natural pair; the PID file is the doorbell label, the socket is the door.

### D11.c. RESOLVED — REPL auto-spawn on startup
- **Chosen:** REPL bootstrap checks the PID file; if dream isn't running and `consolidation_enabled=true`, REPL forks `co dream start --detached`. User can opt out via `CO_DREAM_NO_AUTOSPAWN=1`.
- **Why:** Ergonomic; daemons come for free with the main process (hermes precedent: cron ticker auto-runs with the gateway).

### D11.d. RESOLVED — Per-CO_HOME daemon scoping
- **Chosen:** One dream daemon per `CO_HOME`. All daemon paths derive from `USER_DIR` (`$CO_HOME/daemons/dream.{pid,sock}`, `$CO_HOME/logs/dream/`).
- **Why:** Falls out of co's existing isolation model for free. `CO_HOME` is the single env var that re-roots everything; no new design needed. A "shared user-level daemon multiplexing CO_HOMEs" would go against the grain.

### D12. RESOLVED — Trigger model: KICK is primary, beat is fallback (later reframed in D14)
- **Question:** How does dream learn about new sessions?
- **Chosen:** REPL sends `KICK mine` over `dream.sock` on session end (best-effort, ignore failure). Daemon also has a fallback periodic check.
- **Why:** Kick is one-line in REPL exit path; daemon's existing `processed_sessions` idempotency makes the dual-trigger safe. Filesystem watcher rejected — adds platform-specific code + dependency without solving anything kick+poll doesn't.

### D14. RESOLVED — Drop the general-purpose beat; events drive everything except decay
- **Question:** Why is dream mechanically clocked with fixed-window beats?
- **Chosen:**
  - **Mine** = event-driven (KICK from REPL session-end; cold-start backlog scan at daemon startup).
  - **Merge** = event-driven, cascaded from mine (runs iff ≥1 item was added during the just-completed mine).
  - **Decay** = clock-driven (single 24h timer — only time passing changes "is this item stale?").
- **Why:** Conflated three rhythms into one beat. Real triggers: state changes for mine/merge, time for decay. Daemon's main loop is now `await any-of(kick_event, decay_timer, shutdown)` — idle by default, no general-purpose polling.
- **Constraint:** Drop `dream.beat_interval_seconds` from config. Keep `dream.decay_interval_hours` (default 24), add `dream.idle_threshold_seconds` (default 300 — defer dream work if REPL was active in last N sec).

### D13. RESOLVED — Auto-catchup via FIFO queue, no opt-in command, no forgetting horizon
- **Question:** How does dream catch historical session debt accumulated before daemon was running?
- **Chosen:**
  - **Single FIFO queue, two producers, one consumer.**
    - Producer 1 (one-shot): history scanner at daemon startup/resume — walks `sessions_dir`, diffs against `processed_sessions`, enqueues each unprocessed `session_id`.
    - Producer 2 (continuous): KICK listener — on session-end socket message, enqueues that `session_id`.
    - Consumer: worker loop pops one item, mines it (memory + skill), cascades merge if items added, repeats.
  - **Pure FIFO** — no priority queue. Cold-start is a one-shot event; queue depth ≤1 in steady state; priority and FIFO behave identically except during catchup, where FIFO's no-starvation guarantee is preferable.
  - **No fixed cool-down between queue items.** Idle-threshold gate is the only pause mechanism (defer next item if REPL was active in last `idle_threshold_seconds`).
  - **`consolidation_lookback_sessions` removed** — it was a hard cap that created a forgetting horizon. Daemon now mines all unprocessed sessions, paced by the idle gate. No `max_sessions_per_pass` either — the queue is the natural pacing layer.
- **Why:** Lookback was an artifact of inline-post-session dream. As a daemon, the right semantic is "process every unprocessed session, eventually, paced by user activity". User-driven `co dream catchup` rejected — daemon is event-driven; users don't manually trigger phases.
- **Constraint:** `processed_sessions` remains the dedup source of truth; idempotent re-queueing is safe.

### D14 (mine/merge core logic). RESOLVED — Domain-separated agents, per-session reuse across chunks

**MINE phase:**

For each session popped from the queue:

```
build memory_miner agent        (once per session, reused across chunks)
  tools: memory_search, memory_save
  prompt: dream_memory_miner.md
for each chunk in chunked_window(session):
    memory_miner.run(chunk)     # extract/save memory items only

build skill_miner agent         (once per session, reused across chunks)
  tools: skills_list, skill_view, skill_manage, memory_search
  prompt: dream_skill_miner.md
for each chunk in chunked_window(session):
    skill_miner.run(chunk)      # extract/save skill candidates only

state.processed_sessions.append(session_id)
save_dream_state(...)

if any_items_added: cascade to MERGE
```

- **Two domain agents per session**, each reused across chunks. Sequential (memory first, then skill).
- **Two separate prompts** (`dream_memory_miner.md`, `dream_skill_miner.md`) — same separation principle as hermes `_MEMORY_REVIEW_PROMPT` vs `_SKILL_REVIEW_PROMPT`. No combined prompt.
- **No nudge counters in dream** — counters are an in-session reviewer concern (memory by turn, skill by iteration). Dream is queue-driven; every queued session gets fully mined in both domains.
- **LLM calls per session = `2 × num_chunks`** — bounded but real; cost paid for in extraction quality and design consistency.

**MERGE phase:**

Cascaded inline after mine when ≥1 item was added in either domain.

```
if memory_items_added:
    clusters = identify_mergeable_clusters(memory_dir, similarity_threshold)
    for cluster in clusters[:MAX_MERGES_PER_CYCLE]:
        merge_cluster(cluster, prompt=dream_memory_merge.md)
        archive_originals(cluster)

if skill_items_added:
    clusters = identify_mergeable_skill_clusters(skills_dir, similarity_threshold)
    for cluster in clusters[:MAX_MERGES_PER_CYCLE]:
        merge_skill_cluster(cluster, prompt=dream_skill_merge.md)
        archive_originals(cluster)
```

- **Same domain separation as mine** — `dream_memory_merge.md` for memory, `dream_skill_merge.md` for skills.
- **Merge is the cross-session intelligence layer** — per-session mining produces per-session candidates; merge looks at the accumulated global store and consolidates similar items. "User keeps mentioning X across sessions" → 3 similar memory items → merge consolidates into one canonical item. No multi-session LLM calls needed.
- **For skills**, merge handles hermes's "patch loaded → patch umbrella → add support file → create new umbrella" preference order in reverse: clusters of narrow session-named skills fold into class-level umbrellas.

**Why per-session, not multi-session batched:**

- Multi-session LLM calls blow context budgets (10 sessions × 16K chars = 160K context).
- Failure isolation collapses with batching (one bad transcript kills the batch).
- Resume-from-failure becomes fine-grained per-session checkpointing inside a batch — i.e., per-session items with extra steps.
- Cross-session pattern detection doesn't need multi-session LLM calls; per-session mine + global merge achieves the same intelligence cleanly.

### D11. RESOLVED — Daemon lifecycle and CLI surface

**Co becomes a multi-process system: REPL + N daemons.** N=1 today (dream); architecture leaves room for more (scheduled-job runner, index-rebuild daemon, telemetry collector, platform listener) without locking in a supervisor process yet.

**`co dream` subcommand surface:**

| Command | Action |
|---|---|
| `co dream` (alias for `co dream start`) | Start daemon if not running |
| `co dream start [--foreground] [--once]` | Start daemon. `--foreground` for dev/debug; `--once` runs one queue-drain and exits |
| `co dream status` | PID, uptime, queue depth, current item, next-decay ETA, paused state |
| `co dream stop [--force]` | Graceful drain + exit; `--force` sends SIGKILL after grace |
| `co dream restart` | Stop + start |
| `co dream pause` / `co dream resume` | Toggle worker without killing the process |
| `co dream tail` | Stream daemon spans log |
| `co dream config` | Print effective config |

**State files (per CO_HOME):**

```
$CO_HOME/
  daemons/
    dream.pid    # PID + liveness
    dream.sock   # Unix socket for IPC
    dream.lock   # advisory startup lock
  logs/
    dream/
      <timestamp>.log
  memory/
    _dream_state.json   # processed_sessions, last_run_at, stats
```

**IPC protocol (line-based over Unix socket):**

```
STATUS         → JSON: {pid, uptime, queue_depth, current_item, next_decay_eta, paused}
STOP           → ACK; worker drains, daemon exits
PAUSE / RESUME → ACK; toggles worker gate
KICK <session_id> → ACK; enqueues session for mining
```

REPL session-end calls `KICK <session_id>` best-effort (ignore failure if daemon down).

---

## Open Branches (still to grill)

[OPEN] **D15. `co dream catchup` — still needed?**
Auto-catchup via FIFO queue now handles backlog. The only remaining use case for an explicit `catchup` command is to bypass the idle gate (force mining now). Worth a command, or is "pause REPL → daemon catches up naturally" enough?

[OPEN] **D16. Skill candidate write path during mine.**
Per-session skill miner produces candidates. Write directly to `skills/` (matches memory items, matches hermes), or write to a `candidates/` staging area where merge later promotes? Direct-write is simpler and matches hermes; staging adds a layer hermes doesn't have.

[OPEN] **D17. Decay placement.**
Decay operates on the global store like merge, but is time-driven while merge is event-driven. Keep them as separate phases (current resolution implies this), or fold decay into merge's cascade? Keeping separate respects the event/clock split established in D14.

[OPEN] **D14.a. Merge cascade — always or deferred?**
Cascade always from mine (recommended; bounded by `_MAX_MERGES_PER_CYCLE`), or defer until N pending or T elapsed? Always-cascade is simpler; deferred adds a queue inside a queue.

[OPEN] **D13.a (mooted by D13 resolution).** Originally about newest-first vs oldest-first ordering when backlog > batch size. The FIFO+history-scanner resolution in D13 makes this orthogonal — scanner enqueue order determines processing order. Probably newest-first (so the freshest session's signal lands in the store soonest), but not yet locked.

---

## Implementation note (not a decision — context for downstream impl plan)

The resolutions above imply concrete code changes:

- **New module:** `co_cli/daemons/dream/` — daemon process, CLI subcommands, IPC socket server, worker loop, queue.
- **Refactor:** `co_cli/memory/dream.py` — split miner agent into memory_miner + skill_miner; remove `consolidation_lookback_sessions`; keep `_dream_state.json` schema.
- **New prompts:** `co_cli/memory/prompts/dream_memory_miner.md` (rename existing `dream_miner.md`), `dream_skill_miner.md`, `dream_skill_merge.md`. Existing `dream_merge.md` becomes `dream_memory_merge.md`.
- **Absorb curator:** move skill state-transition + consolidation logic from `co_cli/skills/curator.py` into `co_cli/memory/dream.py` (or `co_cli/daemons/dream/`); delete the standalone curator chain in `_maybe_run_session_review`.
- **REPL integration:** session-end socket KICK in `co_cli/main.py:_session_shutdown`; auto-spawn check in `co_cli/bootstrap/`; remove `_maybe_run_curator` chained from `_maybe_run_session_review` (curator no longer in-band).
- **In-session reviewer**: keep, but split its single prompt into memory + skill prompts and gate by separate counters (turn-counter for memory, iter-counter for skill) — mirrors hermes design.
