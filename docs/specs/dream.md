# Co CLI — Dream

This spec owns the dream subsystem — co's self-learning path. It covers two coupled layers:

1. **In-session reviewer (daemon layer)** — a per-`CO_HOME` daemon that processes KICK payloads queued by the REPL. It runs domain-specific review agents (memory + skill) against recent session transcripts.
2. **Clock-driven housekeeping** — merge → decay against the full memory corpus, fired on a 24h scheduled tick inside the same daemon loop, or on demand via `co dream run`. Lives in `co_cli/daemons/dream/_housekeeping.py`. There is no transcript mining outside the reviewer — housekeeping operates only on the durable memory item store + recall metrics.

The broader persistent cognition model lives in [memory.md](memory.md). Startup and shutdown sequencing live in [bootstrap.md](bootstrap.md) and [01-system.md](01-system.md). Prompt injection and recall scoring live in [prompt-assembly.md](prompt-assembly.md). Model routing for daemon and batch review calls lives in [config.md](config.md).

---

## 1. In-Session Reviewer — Daemon Layer

### 1.1 Architecture Overview

```
   User ── turns ──> REPL (co chat)             dream daemon (co dream start)
                          ▲                                ▲
                          │ writes KICK files,             │ polls queue,
                          │ writes memory/skill            │ writes memory/skill,
                          ▼                                ▼ moves files → done/failed
                  ┌───────────────────────────────────────────────┐
                  │  $CO_HOME — sole cross-process bridge         │
                  │    daemons/dream/queue/<ts>-<uuid>.json       │
                  │    daemons/dream/{done,failed}/               │
                  │    daemons/dream.pid, dream.lock              │
                  │    sessions/<id>.jsonl                        │
                  │    memory/*.md     skills/<name>/SKILL.usage.json │
                  │    logs/co-dream.jsonl + co-dream-spans.jsonl │
                  └───────────────────────────────────────────────┘

   Ollama (off-diagram): shared serializer; no coordination API. REPL fires
   on demand; daemon wraps each call in asyncio.timeout + retry/backoff.
```

REPL behavior lives in §1.2 (counters, KICK dispatch, auto-spawn). Daemon behavior lives in §1.4 (main loop, drain, retries). Filesystem layout under `daemons/dream/` is detailed in §1.3. This diagram only shows the two processes and the single bridge between them.

Key properties:

- **No process-state coupling.** REPL never asks "is daemon busy?" Daemon never asks "is REPL busy?".
- **Filesystem is the sole cross-process bridge.** Producer (REPL) writes queue files; consumer (daemon) polls the queue directory. No socket, no realtime signaling, no side channels — the daemon discovers work on its next poll iteration.
- **Daemon control is POSIX-native.** Stop is `SIGTERM` with `SIGKILL` fallback. Status is direct PID-file + queue-directory inspection by the CLI — no daemon round-trip.
- **Ollama is the only shared external resource.** Daemon copes via timeout + retry + backoff; REPL copes by being interactive.
- **Two domain counters, two domain specs.** Memory and skill review are fully independent — own counters, own KICKs, own queue items, own agents.
- **Approval bypass via `build_task_agent`.** Daemon tools are registered with `requires_approval=False`. Dead REPL-side flags (`auto_approve_skill_ops`, `auto_approve_knowledge_ops`) were removed.

### 1.2 REPL-Side Counters and KICK Dispatch

`CoSessionState` carries two domain counters:

| Field | Unit | Increment source |
|---|---|---|
| `turns_since_memory_review: int` | turns (1/turn) | `_post_turn_hook` |
| `model_requests_since_skill_review: int` | model requests (`model_request_count`/turn) | `_post_turn_hook` |

**Unit rationale:** memory tracks user-intent signal (~1 per turn); skill tracks agent-action signal (~tool + reasoning steps per turn). Conflating the units would over-fire skill reviews on chatty users or under-fire memory reviews on tool-heavy turns.

Counter flow in `_post_turn_hook` (guarded by `review_enabled` and `deps.model is not None`):

```python
deps.session.turns_since_memory_review += 1
deps.session.model_requests_since_skill_review += model_request_count
_maybe_kick_memory_review(deps)
_maybe_kick_skill_review(deps)
```

Each `_maybe_kick_*` checks whether the counter has reached its nudge interval, resets the counter to 0, and calls `_send_review_kick(deps, domain=..., persisted_message_count=...)`.

**Inline tool-write resets** (domain-scoped):

| Tool call | Effect |
|---|---|
| `memory_create` / `memory_append` / `memory_replace` | `turns_since_memory_review = 0` |
| `skill_create` / `skill_edit` / `skill_patch` | `model_requests_since_skill_review = 0` |
| `memory_delete` / `skill_delete` | no reset |
| No crossover | memory tool never touches skill counter; skill tool never touches memory counter |

**Session-end always-fire** in `_drain_and_cleanup`: both KICKs (memory + skill) fire regardless of counter state at REPL shutdown.

**`_send_review_kick`** is fire-and-forget against the filesystem: atomic-write a KICK JSON file to `$CO_HOME/daemons/dream/queue/<ts>-<uuid>.json` (write to `<name>.tmp` sibling → fsync → `os.replace` into `<name>.json`) so the daemon never observes a torn file. The producer never touches the daemon's address space — daemon picks the file up on its next polling iteration (default 5 s).

### 1.3 KICK File Queue

Queue file payload:

```jsonc
{
  "domain": "memory" | "skill",
  "session_id": "<session-stem>",
  "persisted_message_count": 42,
  "created_at": "2026-05-22T...",
  "attempts": 0
}
```

`persisted_message_count` is a JSONL record index, not a turn count. The daemon truncates the transcript at this index to get a consistent view even while the REPL is still appending. Naming this `turn_index` would invite truncation bugs.

Queue directories under `$CO_HOME/daemons/dream/`:

| Path | Content |
|---|---|
| `queue/*.json` | Pending KICK files |
| `queue/done/*.json` | Successfully processed (audit retention) |
| `queue/failed/*.json` | Exhausted `max_retry_attempts`; inspect via `co dream status` |

The daemon's `_queue.py` scanner skips any `*.tmp` files. Both producers — REPL writing a new KICK and the daemon updating the attempt counter in place — use tmp + `os.replace` so a crash mid-write never leaves a torn `*.json` file visible to the scanner.

### 1.4 Daemon Process Model

**Lifecycle:**

```text
co dream start
  → singleton check: read DREAM_PID_FILE
      if PID is live  → print "daemon already running" → SystemExit(1)
      if PID is stale → log "overwriting stale PID file" → unlink → proceed
  → acquire advisory flock on dream.lock (POSIX-only)
  → spawn_detached: subprocess.Popen(co dream start --foreground, start_new_session=True)
  → child installs SIGTERM/SIGINT handlers (set shutdown asyncio.Event) FIRST
  → child wires observability via setup_observability() → rotating JSONL
        $CO_HOME/logs/co-dream.jsonl (INFO+ app log) + co-dream-spans.jsonl (spans)
  → child writes DREAM_PID_FILE (pid, origin, session_id, started_at)
  → child calls create_deps(on_status=logger.info, stack=None)  [headless bootstrap]
  → child runs main_loop(deps, queue_dir, state, cfg, shutdown)
  → on shutdown.set(): main_loop exits; finally-block unlinks DREAM_PID_FILE

co dream stop          (default: graceful)
  → read DREAM_PID_FILE; if missing or PID is dead, clean up and print "not running"
  → send SIGTERM to PID
  → poll up to 10s (20 × 0.5s) for process death; on timeout, send SIGKILL
  → unlink DREAM_PID_FILE (covers both graceful exit and SIGKILL path —
    SIGKILL bypasses daemon's own finally cleanup)

co dream stop --force  (immediate)
  → SIGKILL directly, no SIGTERM grace period
  → poll up to 2s for exit, unlink DREAM_PID_FILE

Stale PID cleanup: every entry point (start / stop / status) probes the recorded PID
with os.kill(pid, 0). Dead PID → file is treated as stale and removed.
```

POSIX-only boundary: `fcntl.flock`, `start_new_session=True`, POSIX signals (`SIGTERM`/`SIGKILL`). Marked in `_process.py` module docstring. No Windows path.

**Worker loop:**

The main loop is signal-driven and polling-based — no IPC. On startup the daemon installs SIGTERM/SIGINT handlers (each sets a shared `asyncio.Event`); cold-start drain is implicit — the first iterations see pending files and process them before reaching any sleep. There is one loop, three branches per iteration: idle-poll, process-item, retry-backoff.

```python
while not shutdown.is_set():
    files = sorted(queue_dir.glob("*.json"))
    if not files:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(shutdown.wait(),
                                   timeout=poll_interval_seconds)
        continue                                        # idle-poll

    item = files[0]                                     # FIFO
    try:
        async with asyncio.timeout(cfg.review_timeout_seconds):
            await _process_kick_file(deps, item, state)
        move_to_done(item)
    except Exception as exc:
        payload["attempts"] += 1
        write_queue_item(item, payload)                 # persist counter across restarts
        if attempts >= cfg.max_retry_attempts:
            move_to_failed(item, last_error=str(exc))
        else:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(),
                                       timeout=retry_backoff_seconds)
```

Skip-sleep-when-busy falls out of the structure: as long as the queue keeps refilling, the loop never enters either sleep branch.

**Token-usage flush.** The daemon runs in its own process with its own `create_deps`, so it gets its own fork-shared `usage_accumulator` and both model-call capture paths (`run_standalone`'s run-boundary `record_usage` for reviewer agent loops, `llm_call` for housekeeping merges) fire there with no extra wiring. At each cycle boundary — after a housekeeping pass in the idle branch and after a reviewer item is moved to `done/` — `_flush_daemon_usage(deps)` appends one ledger line with `origin="daemon"` and a null `session_id`, then resets the accumulator. Because the daemon has no `session_path`, its spend is counted toward the combined/windowed totals but never attributed to any session (`/usage` current-session excludes it). Cross-process appends to `~/.co-cli/usage.jsonl` are atomic (line < `PIPE_BUF`, `O_APPEND`). See [sessions.md](sessions.md) and [tui.md](tui.md).

**Clean-shutdown bound.** Both sleep points — idle poll and retry backoff — are `asyncio.wait_for(shutdown.wait(), timeout=...)`, so SIGTERM wakes the loop immediately rather than after the timeout. The in-flight item is allowed to finish (its `asyncio.timeout` runs to completion or its own timeout fires). Remaining queue files stay in `queue/` and are picked up by the next daemon start. Worst-case shutdown latency is one reviewer call, bounded by `review_timeout_seconds` — inside the 10 s SIGTERM → SIGKILL budget when `review_timeout_seconds ≤ 10`. With the current default (`120`), an in-flight reviewer can exceed the SIGKILL window — that is the timeout's intrinsic cost, not a loop-structure issue. `CancelledError` is `BaseException` and is not caught by `except Exception`, so task-cancel propagates cleanly.

### 1.5 Domain Reviewers

Two specs in `co_cli/daemons/dream/_reviewer.py`:

| Spec | Tool surface | Prompt |
|---|---|---|
| `MEMORY_REVIEW_SPEC` | `memory_search`, `memory_create`, `memory_append`, `memory_replace` | `daemons/dream/prompts/memory_review.md` |
| `SKILL_REVIEW_SPEC` | `skill_view`, `skill_create`, `skill_edit`, `skill_patch`, `memory_search`; `include_skill_manifest=True` | `daemons/dream/prompts/skill_review.md` |

**Memory review** — focused on persona, preferences, and references extracted from the transcript.

**Skill review** — focused on corrections, techniques, and umbrella discipline patterns extracted from the transcript. The skill manifest is injected so the reviewer can reference and patch existing skills by name.

Both specs route through `run_standalone(SPEC, child_deps, prompt)` which uses `build_task_agent` with `requires_approval=False`. **Daemon code must never call a REPL-toolset-built agent** — it would block waiting for an approval that no frontend can answer.

**Deps bootstrap is shared with the REPL.** `_run_foreground` calls `create_deps(on_status=logger.info, stack=None)` — the same bootstrap path used by `co chat`. The two daemon-specific differences are: status messages route to the daemon log instead of a terminal, and no MCP servers are connected (reviewer tools are all native). All stores (`index_store`, `memory_store`, `session_store`, `skill_catalog`) are built identically to the REPL.

**Transcript loading:** `load_transcript(path, max_message_count=N)` truncates the JSONL at record index N — consistent view even while REPL appends. Default `max_message_count=None` returns the full list unchanged (existing callers unaffected).

### 1.6 Recall Metrics

Recall signals flow back into items at query time, providing data for Plan 2's housekeeping.

**Memory items** — three fields on `MemoryItem`:

| Field | Status | Type | Semantics |
|---|---|---|---|
| `recall_count: int` | existing | int | Total hit count |
| `last_recalled_at: str \| None` | existing | ISO-8601 string | Most recent recall timestamp |
| `recall_days: list[str]` | new | deduped ISO-date strings | Cadence signal; more robust to lost-update than raw count |

Side-effect in `memory_search` after building results, before returning `ToolReturn`:

```python
for each returned hit:
    item = load_memory_item(path)
    item.recall_count += 1
    item.last_recalled_at = now.isoformat()
    if today_iso not in item.recall_days:
        item.recall_days.append(today_iso)
    atomic_write_text(path, render_memory_item_file(item))
```

Lazy-default on load: items without `recall_days` in frontmatter read back `[]`.

**Skill items** — extend the existing `co_cli/skills/usage.py` sidecar:

```jsonc
{
  "version": 1,
  "skills": {
    "<name>": {
      "use_count": 0,
      "view_count": 0,
      "patch_count": 0,
      "recall_days": ["2026-05-20"],   // new field
      "last_used_at": null,
      "last_viewed_at": null,
      "last_patched_at": null
    }
  }
}
```

`bump_recall(deps, name)` appends today's ISO date to `recall_days` (deduped), without touching existing counters. Called from:
- `skill_view` — alongside existing `bump_view`
- `/skill-name` slash dispatch (`commands/core.py`) — before `DelegateToAgent`

Lazy migration: sidecars without `recall_days` default to `[]` on `setdefault` read.

**Concurrency model:** recall writes are best-effort with possible lost updates under concurrent REPL sessions or REPL + daemon. `recall_days` deduplication (day strings collide rather than increment) makes lost-update degrade gracefully. Torn writes are prevented by `atomic_write_text`; lost updates are accepted. Plan 2 housekeeping consumes `recall_count`/`recall_days` as order-of-magnitude signals, not exact ledgers.

### 1.7 REPL Auto-Spawn

`maybe_autospawn_dream(deps, frontend)` in `co_cli/bootstrap/core.py`:

```text
if dream.enabled is False: return
if CO_DREAM_NO_AUTOSPAWN is set: return
acquire advisory flock on DREAM_LOCK (POSIX-only)
if pid_live(read_pid(DREAM_PID_FILE)): return   # already running
Popen(co dream start --origin=repl-autospawn --session-id=<id>)
if first spawn for this CO_HOME:
    frontend.on_status("[dream] daemon started in background. ...")
```

The `--origin` and `--session-id` are persisted to `dream.pid` so `co dream status` can report provenance. Concurrent REPL bootstraps serialize via `fcntl.flock` — exactly one daemon spawns.

Current default: `dream.enabled = false`. Opt-in via `CO_DREAM_ENABLED=true` or settings file.

---

## 2. Clock-Driven Housekeeping

Housekeeping (`co_cli/daemons/dream/_housekeeping.py`) runs merge → decay against the full memory corpus AND the user skill library. It is fired from inside the daemon's polling main loop on either a 24h scheduled tick or a manual sentinel-file trigger (`co dream run`). It reads the memory item store, the per-skill recall sidecars, and skill markdown bodies — never transcripts (that is the reviewer's job).

```mermaid
flowchart TD
    subgraph Entry["Entry Points"]
        Manual["co dream run\n(writes ~/.co-cli/daemons/dream/run.tag)"]
        Auto["scheduled tick\n(now ≥ last + run_interval_hours,\nclamped to next run_at)"]
    end

    subgraph Pass["Housekeeping pass — merge → decay"]
        MemMerge["Phase 1a: Memory merge\nsame-kind similar clusters (≥ threshold)\nkind=article excluded\nrecall-aware canonical (highest recall_count)\n→ llm_call() consolidates body\n→ archive originals"]
        SkillMerge["Phase 1b: Skill merge\ntoken-Jaccard clusters of user-skill bodies\npinned skills excluded\ncanonical = max (len(recall_days), use_count)\n→ llm_call() consolidates body\n→ archive non-anchor originals to skills/.archive/"]
        MemDecay["Phase 2a: Memory decay\naged > decay_after_days\nAND no recall in recall_protection_days\nAND not decay_protected\n→ archive to memory/_archive/"]
        SkillDecay["Phase 2b: Skill decay\nsidecar age > skills.decay_after_days\nAND no recall in skills.recall_protection_days\nAND not pinned\n→ archive to user_skills_dir/.archive/"]
        State["Persist HousekeepingState\n(last_housekeeping_at,\n memory_merged/decayed,\n skill_merged/decayed)"]
    end

    Manual --> MemMerge
    Auto --> MemMerge
    MemMerge --> SkillMerge
    SkillMerge --> MemDecay
    MemDecay --> SkillDecay
    SkillDecay --> State
```

### 2.1 Entry Points

Inside the daemon's polling main loop, on every empty-queue iteration (before the idle sleep), two checks fire in order:

```python
if DREAM_RUN_TAG.exists():
    DREAM_RUN_TAG.unlink(missing_ok=True)
    await run_housekeeping(deps, cfg)
elif scheduled_tick_due(state, cfg):
    await run_housekeeping(deps, cfg)
```

Manual trigger:

```text
co dream run
  → checks daemon liveness via PID file
  → if daemon down: stderr "dream daemon not running; start with `co dream start`." + exit 1
  → atomic-write empty sentinel at ~/.co-cli/daemons/dream/run.tag
  → print "Housekeeping requested. Check `co dream status` for results."
```

Worst-case latency from `co dream run` to housekeeping start is `dream.poll_interval_seconds` (default 5 s). There is no ad-hoc spawn — the daemon must be running.

Scheduled tick:

```text
scheduled_tick_due(state, cfg):
  if state.last_housekeeping_at is None: return True
  earliest = last + run_interval_hours
  if now < earliest: return False
  target = earliest.replace(hour=run_at_hh, minute=run_at_mm)
  if target < earliest: target += one day
  return now ≥ target
```

Never-run state returns `True` so a freshly-installed daemon does a baseline pass on its first idle tick.

### 2.2 State

`HousekeepingState` persists at `~/.co-cli/daemons/dream/_dream_state.json` (distinct from the in-memory `DaemonState` in `_state.py`).

| Field | Meaning |
|---|---|
| `last_housekeeping_at` | ISO timestamp for the most recent pass (set after timeout too) |
| `stats.memory_merged` | Cumulative memory-merge clusters completed |
| `stats.memory_decayed` | Cumulative memory items archived by decay |
| `stats.skill_merged` | Cumulative skill-merge clusters completed |
| `stats.skill_decayed` | Cumulative skills archived by decay |

Load is forgiving: missing or corrupt state returns a fresh state object. The schema is additive — counters default to `0` so older payloads stay readable.

### 2.3 Phase 1a: Memory Merge

Merge reduces duplication by clustering same-kind, non-pinned items above a token-Jaccard threshold and consolidating each cluster into one canonical artifact.

```text
load active memory items
discard decay_protected items (pins)
discard kind=article items (RAG-integrity — articles decay or stay)
group by memory_kind
cluster by token-Jaccard ≥ memory.consolidation_similarity_threshold
truncate each cluster to ≤ MAX_CLUSTER_SIZE (5)
keep ≤ MAX_MERGES_PER_CYCLE (10) clusters

for each cluster:
  anchor = max(cluster, key=(recall_count, created_at))   # recall-aware
  prompt = render(anchor first, then siblings)
  body = llm_call(prompt, instructions=memory_merge.md)
  if len(body) < MERGED_BODY_MIN_CHARS (20): skip
  write consolidated item (source_type=consolidated)
  archive originals into memory/_archive/
  state.stats.memory_merged += 1
```

The merge call is a **direct `llm_call`** (no tool access, body text only). Originals are archived only after the consolidated artifact is durably written. The anchor's `recall_count` ties to the canonical body — high-recall items survive merge intact; LLM-driven consolidation pulls in the siblings' distinct facts.

**Article exclusion.** `kind=article` items are external source content. LLM-merging two articles produces synthesized text that mixes two sources, violating RAG integrity. Article redundancy is handled via decay (recall-driven drop) plus agent curation distilling important articles into `kind=note` / `kind=rule` items — those derived items merge normally.

### 2.4 Phase 2a: Memory Decay

Decay removes stale, low-use knowledge from active recall while preserving it for restore.

```text
for each active item:
  skip if decay_protected (pin)
  skip if (now - created_at) < memory.decay_after_days
  skip if last_recalled_at AND (now - last_recalled_at) < memory.recall_protection_days
include candidate
sort by created_at ascending (oldest first)
archive up to MAX_DECAY_PER_CYCLE (20) per pass
state.stats.memory_decayed += archived
```

The `recall_protection_days` window is **separate from** `decay_after_days`. Default: an item must be >90 days old AND not recalled within the last 30 days to decay. Items that never recalled (`last_recalled_at is None`) fall straight through to decay once past the age cutoff.

### 2.5 Skill Housekeeping Phases

Skill merge and decay run inside the same `run_housekeeping` pass: skill merge follows memory merge under the shared `asyncio.timeout(cfg.max_pass_seconds)` cap; skill decay follows memory decay outside the timeout. Both operate on user-installed skills only — bundled skills under `co_cli/skills/` are upstream-managed and never considered.

**Phase 1b: skill merge.** Recall-informed, cluster-scoped:

```text
load user_skills_dir/*/SKILL.md (frontmatter stripped, body retained)
discard pinned skills (sidecar.pinned == True)
cluster by token-Jaccard ≥ skills.consolidation_similarity_threshold (body text)
truncate each cluster to ≤ MAX_CLUSTER_SIZE (5)
keep ≤ MAX_MERGES_PER_CYCLE (10) clusters

for each cluster:
  anchor = max(cluster, key=(len(sidecar.recall_days), sidecar.use_count))
  prompt = render(anchor first, then siblings)
  body = llm_call(prompt, instructions=skill_merge.md)
  if len(body) < MERGED_BODY_MIN_CHARS (20): skip
  rewrite anchor's SKILL.md with merged body (frontmatter preserved)
  archive each non-anchor sibling folder into user_skills_dir/.archive/<name>/
  state.stats.skill_merged += 1

if any clusters merged: refresh_skills(deps)
```

Cluster-scoped, NOT full-library. The LLM sees at most five similar skills per call, so prompts stay tractable and merge decisions are auditable. Skills without a sidecar (never invoked) score `(0, 0)` and lose the canonical pick to anything tracked.

**Phase 2b: skill decay.** Sync, bounded — same shape as memory decay:

```text
for each user-skill candidate with a sidecar:
  skip if sidecar.pinned
  skip if not sidecar.created_at (no anchor for age)
  age_days = now - parse(sidecar.created_at)
  skip if age_days < skills.decay_after_days
  recall_days = sidecar.recall_days  (list of ISO date strings)
  if recall_days:
    last_recall_date = parse(recall_days[-1])
    skip if (today - last_recall_date).days < skills.recall_protection_days
  candidate
sort candidates implicitly (filesystem order); archive up to MAX_DECAY_PER_CYCLE (20)
state.stats.skill_decayed += archived
if any archived: refresh_skills(deps)
```

Skills without a sidecar are never decay candidates — bundled skills don't have one, and an agent-created skill without a sidecar means usage tracking is disabled, so `created_at` and `recall_days` are unknown. Recall age is read from `recall_days[-1]` (the most recent ISO date the skill was invoked), not from a separate `last_recalled` field — the sidecar deliberately does not carry one because cadence (distinct days) is the load-bearing signal, not the most recent moment.

Both phases call `refresh_skills(deps)` after writes so `deps.skill_catalog` stays in sync with disk.

### 2.6 Failure and Timeout Semantics

`run_housekeeping` wraps the **merge phases** in `asyncio.timeout(cfg.max_pass_seconds)` (default 600 s). Decay phases are synchronous and bounded by `MAX_DECAY_PER_CYCLE` filesystem moves — wrapping them in the same timeout would let a slow merge starve decay, so both decay phases run unconditionally after merge regardless of whether merge completed or timed out. On merge timeout, partial merge counters are still persisted, decay still runs, and `last_housekeeping_at` is set to now — the next tick fires on schedule rather than stacking missed passes. Individual merge clusters that raise are logged and skipped without aborting the pass.

### 2.7 User Inspection and Recovery

| Command | Purpose |
|---|---|
| `co dream run` | Request a one-shot housekeeping pass from the running daemon |
| `co dream status` | Daemon state + queue/failed counts (post-pass effects show via `/memory stats`) |
| `/memory stats` | Active counts, archive count, last housekeeping timestamp + cumulative stats, decay candidates |
| `/memory restore [slug]` | List archived artifacts or restore one by unambiguous filename prefix |
| `/memory decay-review --dry` | Preview decay candidates |
| `/memory decay-review` | Archive decay candidates after confirmation |

### 2.8 Observability

| Span | Source | Purpose |
|---|---|---|
| `co.housekeeping.pass` | `@trace` on `run_housekeeping` | Whole-pass envelope; phase counters and timeout outcome |
| `co.housekeeping.merge` | `@trace` on `merge_memory` | Memory merge phase count |
| `co.housekeeping.decay` | `@trace` on `decay_memory` | Memory decay phase count |
| `co.housekeeping.skill_merge` | `@trace` on `merge_skills` | Skill merge phase count |
| `co.housekeeping.skill_decay` | `@trace` on `decay_skills` | Skill decay phase count |

---

## 3. Inspectability

Auto-spawn and daemon existence are visible across four surfaces (mission §"Trusted"):

| Surface | Description |
|---|---|
| **First-spawn notice** | On first auto-spawn of a `CO_HOME`, REPL prints: `[dream] daemon started in background. 'co dream status' to inspect; 'co dream stop' to stop.` |
| **Welcome banner** | `Dream:` row alongside `Memory:` / `Tools:` / `Dir:`. Three states: `✓ running  queue: N` (accent), `disabled` (dim), `enabled but daemon not running  queue: N (on disk)` (yellow). Built from local PID-file + queue-directory reads — instantaneous, never stalls startup. |
| **`/dream` slash** | Read-only inspection in the REPL. Calls `status_daemon` (file-based; no daemon round-trip). When daemon is down: prints state + on-disk queue depth + guidance. |
| **`co dream status`** | Full JSON: `running`, `pid`, `uptime_seconds`, `queue_depth`, `failed_count`, `spawn_origin`, `spawn_session_id`. Authoritative source of truth — read directly from PID file + queue directory. |

CLI subcommands:

```text
co dream start [--foreground] [--origin=<str>] [--session-id=<str>]
co dream status
co dream stop [--force]
co dream run                # request a one-shot housekeeping pass
```

The daemon wires the same observability stack as the main app via `setup_observability()`: a rotating JSONL app log `co-dream.jsonl` (INFO+; WARNING+ records land here too — there is no separate dream errors file) and a span stream `co-dream-spans.jsonl`, both directly under `$CO_HOME/logs/`. For app-log streaming: `tail -f $CO_HOME/logs/co-dream.jsonl`. Note: `co tail` / `co trace` read only `co-cli-spans.jsonl`, so dream spans are inspectable via `jq` over `co-dream-spans.jsonl`, not the live viewers.

---

## 4. Config

### Daemon settings (`dream.*`)

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `dream.enabled` | `CO_DREAM_ENABLED` | `false` | Master switch; REPL auto-spawn only fires when true |
| `dream.review_timeout_seconds` | `CO_DREAM_REVIEW_TIMEOUT_SECONDS` | `120` | Per-review LLM call timeout; `asyncio.timeout` in worker loop |
| `dream.retry_backoff_seconds` | `CO_DREAM_RETRY_BACKOFF_SECONDS` | `30` | Sleep between retry attempts on timeout or error |
| `dream.max_retry_attempts` | `CO_DREAM_MAX_RETRY_ATTEMPTS` | `3` | After this many failures, move queue file to `failed/` |
| `dream.poll_interval_seconds` | `CO_DREAM_POLL_INTERVAL_SECONDS` | `5` | Idle queue-scan interval (range 1–60); only fires when queue is empty |
| `dream.run_interval_hours` | `CO_DREAM_RUN_INTERVAL_HOURS` | `24` | Minimum hours between housekeeping passes (range 1–720) |
| `dream.run_at` | `CO_DREAM_RUN_AT` | `"03:00"` | Preferred local time-of-day boundary for the scheduled tick (`HH:MM`) |
| `dream.max_pass_seconds` | `CO_DREAM_MAX_PASS_SECONDS` | `600` | Wall-clock cap on the merge phase of a housekeeping pass (≥ 60); decay runs unconditionally after merge |

### Reviewer trigger settings (`skills.*`)

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `skills.review_enabled` | `CO_SKILLS_REVIEW_ENABLED` | `false` | Master switch for the reviewer subsystem |
| `skills.review_memory_nudge_interval` | `CO_SKILLS_REVIEW_MEMORY_NUDGE_INTERVAL` | `10` | Turns between mid-session memory KICKs |
| `skills.review_skill_nudge_interval` | `CO_SKILLS_REVIEW_SKILL_NUDGE_INTERVAL` | `10` | Iterations between mid-session skill KICKs |

### Housekeeping settings (`memory.*`)

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `memory.consolidation_similarity_threshold` | `CO_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.75` | Token-Jaccard threshold for memory merge clusters and write-time dedup |
| `memory.decay_after_days` | `CO_MEMORY_DECAY_AFTER_DAYS` | `90` | Minimum age before a memory item is eligible for decay |
| `memory.recall_protection_days` | `CO_MEMORY_RECALL_PROTECTION_DAYS` | `30` | Recent-recall window that protects an aged memory item from decay |

### Skill housekeeping settings (`skills.*`)

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `skills.consolidation_similarity_threshold` | `CO_SKILLS_CONSOLIDATION_SIMILARITY_THRESHOLD` | `0.75` | Token-Jaccard threshold for skill merge clusters |
| `skills.decay_after_days` | `CO_SKILLS_DECAY_AFTER_DAYS` | `90` | Minimum sidecar `created_at` age before a skill is eligible for decay |
| `skills.recall_protection_days` | `CO_SKILLS_RECALL_PROTECTION_DAYS` | `30` | Recent-recall window that protects an aged skill from decay |

Internal caps (housekeeping — apply to both domains):

| Constant | Value | Purpose |
|---|---|---|
| `MAX_CLUSTER_SIZE` | 5 | Cap on items/skills per merge cluster |
| `MAX_MERGES_PER_CYCLE` | 10 | Cap on clusters merged per pass per domain |
| `MERGED_BODY_MIN_CHARS` | 20 chars | Guard against empty/degenerate merge outputs |
| `MAX_DECAY_PER_CYCLE` | 20 | Cap on items/skills archived per decay phase |

---

## 5. Public Interface

### Daemon layer

| Symbol | Source | Contract |
|---|---|---|
| `start_daemon(co_home, *, foreground, origin, session_id)` | `co_cli/daemons/dream/process.py` | Start daemon; live PID → `SystemExit(1)`; stale PID → overwrite |
| `stop_daemon(co_home, *, force=False)` | `co_cli/daemons/dream/process.py` | force=False: SIGTERM, poll 10 s for exit, SIGKILL fallback. force=True: SIGKILL directly. Always unlinks DREAM_PID_FILE. |
| `status_daemon(co_home) -> dict` | `co_cli/daemons/dream/process.py` | File-based status: reads PID file + probes liveness + scans queue directory |
| `spawn_detached(cmd, env=None) -> int` | `co_cli/daemons/dream/_process.py` | Popen with start_new_session=True (setsid). Returns child PID. Not a classic POSIX double-fork — setsid alone gives the needed detachment on modern Linux/macOS. |
| `create_deps(*, on_status, stack=None, theme_override=None) -> CoDeps` | `co_cli/bootstrap/core.py` | Shared bootstrap for REPL and daemon; daemon passes `stack=None` to skip MCP |
| `MEMORY_REVIEW_SPEC` / `SKILL_REVIEW_SPEC` | `co_cli/daemons/dream/_reviewer.py` | Domain reviewer task specs |
| `process_review(deps, domain, session_id, persisted_message_count)` | `co_cli/daemons/dream/_reviewer.py` | Load transcript + dispatch to domain reviewer. Raises `ValueError` on unknown domain (corrupt kick → `failed/`). Missing transcript is a benign no-op. |
| `maybe_autospawn_dream(deps, frontend)` | `co_cli/bootstrap/core.py` | REPL auto-spawn hook |
| `build_dream_line(deps) -> str` | `co_cli/bootstrap/banner.py` | Banner `Dream:` line builder |
| `handle_dream_slash(ctx, args)` | `co_cli/commands/dream.py` | `/dream` slash handler |

### Recall metrics

| Symbol | Source | Contract |
|---|---|---|
| `bump_recall(deps, name)` | `co_cli/skills/usage.py` | Append today's ISO date to `recall_days` in sidecar (deduped, best-effort) |
| `MemoryItem.recall_days` | `co_cli/memory/item.py` | `list[str]` — deduped ISO-date strings; lazy-default `[]` on load |

### Housekeeping

| Symbol | Source | Contract |
|---|---|---|
| `run_housekeeping(deps, cfg, state) -> HousekeepingState` | `co_cli/daemons/dream/_housekeeping.py` | Async — merge under `asyncio.timeout(cfg.max_pass_seconds)`, then decay unconditionally; caller owns the `HousekeepingState` load, this function mutates + persists it |
| `merge_memory(deps, state) -> int` | `co_cli/daemons/dream/_housekeeping.py` | Async — recall-anchored merge of same-kind memory clusters; articles excluded; returns clusters merged |
| `decay_memory(deps, state) -> int` | `co_cli/daemons/dream/_housekeeping.py` | Sync — archive aged + unrecalled memory candidates; returns count archived |
| `merge_skills(deps, state) -> int` | `co_cli/daemons/dream/_housekeeping.py` | Async — recall-anchored merge of user-skill clusters; pinned excluded; rewrites anchor in-place, archives siblings to `user_skills_dir/.archive/`; returns clusters merged |
| `decay_skills(deps, state) -> int` | `co_cli/daemons/dream/_housekeeping.py` | Sync — archive aged + unrecalled user skills with a sidecar; pinned and sidecar-less skills protected; returns count archived |
| `scheduled_tick_due(state, cfg) -> bool` | `co_cli/daemons/dream/_loop.py` | Returns True when ≥ `run_interval_hours` since last pass AND past today's `run_at` boundary |
| `HousekeepingState` / `HousekeepingStats` | `co_cli/daemons/dream/_state.py` | Pydantic models for housekeeping state persistence |
| `load_housekeeping_state(daemon_dir)` | `co_cli/daemons/dream/_state.py` | Forgiving loader — fresh state on missing/corrupt |
| `save_housekeeping_state(daemon_dir, state)` | `co_cli/daemons/dream/_state.py` | Atomic write of `_dream_state.json` under `daemons/dream/` |
| `find_decay_candidates(memory_dir, config)` | `co_cli/memory/decay.py` | Pure candidate filter — applies `decay_after_days`, `recall_protection_days`, and `decay_protected` |
| `archive_artifacts(entries, memory_dir, memory_store)` | `co_cli/memory/archive.py` | Move items into `memory/_archive/` |
| `restore_artifact(slug, memory_dir, memory_store)` | `co_cli/memory/archive.py` | Restore archived item by unambiguous filename prefix |
| `DREAM_RUN_TAG` | `co_cli/config/core.py` | Path to the sentinel file written by `co dream run` |

### Transcript loading (reviewer only)

| Symbol | Source | Contract |
|---|---|---|
| `load_transcript(path, *, max_message_count=None)` | `co_cli/session/persistence.py` | Load JSONL; truncate at `max_message_count` when provided. Housekeeping never reads transcripts. |

---

## 6. Files

### Daemon layer

| File | Purpose |
|---|---|
| `co_cli/daemons/dream/__init__.py` | Docstring-only package marker |
| `co_cli/daemons/dream/_queue.py` | Queue file read/write/move helpers |
| `co_cli/daemons/dream/_loop.py` | Polling main loop and queue drain logic |
| `co_cli/daemons/dream/_reviewer.py` | `MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`, `process_review` |
| `co_cli/daemons/dream/_process.py` | PID-file helpers, advisory flock, `spawn_detached` (Popen + setsid, POSIX-only) |
| `co_cli/daemons/dream/_state.py` | `DaemonState` runtime struct + PID-file loader + `HousekeepingState` / `HousekeepingStats` |
| `co_cli/daemons/dream/_housekeeping.py` | Memory + skill merge and decay phases; `run_housekeeping`, `merge_memory`, `decay_memory`, `merge_skills`, `decay_skills` |
| `co_cli/daemons/dream/prompts/memory_merge.md` | Same-kind memory consolidation prompt |
| `co_cli/daemons/dream/prompts/skill_merge.md` | Cluster-scoped skill umbrella consolidation prompt |
| `co_cli/daemons/dream/process.py` | Public surface: `start_daemon`, `stop_daemon`, `status_daemon`, `_run_foreground` |
| `co_cli/daemons/dream/prompts/memory_review.md` | Memory reviewer instructions |
| `co_cli/daemons/dream/prompts/skill_review.md` | Skill reviewer instructions |
| `co_cli/commands/dream.py` | `co dream` CLI group + `handle_dream_slash` |
| `co_cli/config/dream.py` | `DreamSettings` Pydantic model + `DREAM_ENV_MAP` |
| `co_cli/bootstrap/banner.py` | `build_dream_line` — `Dream:` banner row |
| `co_cli/bootstrap/core.py` | `maybe_autospawn_dream` — REPL auto-spawn hook |
| `co_cli/main.py` | `_send_review_kick`, `_maybe_kick_memory_review`, `_maybe_kick_skill_review`, `_fire_session_end_kicks` |
| `co_cli/deps.py` | `CoSessionState.turns_since_memory_review` / `model_requests_since_skill_review` |
| `co_cli/skills/usage.py` | `bump_recall` + `recall_days` sidecar field |
| `co_cli/memory/item.py` | `MemoryItem.recall_days` field |

### Housekeeping support modules

| File | Purpose |
|---|---|
| `co_cli/memory/similarity.py` | Token-Jaccard similarity and clustering |
| `co_cli/memory/decay.py` | Decay candidate selection — applies `decay_after_days` + `recall_protection_days` |
| `co_cli/memory/archive.py` | Archive and restore mechanics |
| `co_cli/session/persistence.py` | `load_transcript` — reviewer-only |
| `co_cli/commands/memory.py` | `/memory restore`, `/memory decay-review`, `/memory stats` |
| `co_cli/commands/dream.py` | `co dream run` subcommand — writes the sentinel file |
| `co_cli/commands/memory.py` | `/memory stats` (now reports both memory + skill housekeeping counters) |

---

## 7. Test Gates

### Daemon layer

| Property | Test file |
|---|---|
| memory write reset — `memory_create`/`append`/`replace` reset; `memory_delete` does not | `tests/tools/memory/test_manage_resets.py` |
| skill-write reset — `skill_create`/`skill_edit`/`skill_patch` reset; `skill_delete` does not | `tests/tools/system/test_skill_manage_resets.py` |
| Memory recall updates on `memory_search`; backward-compat load | `tests/tools/memory/test_recall_metrics.py` |
| Skill recall sidecar `recall_days`; deduplication; backward-compat | `tests/skills/test_usage_recall_days.py` |
| `.tmp` skip filter and FIFO drain order; `last_error` injection on `failed/` move | `tests/daemons/dream/test_queue.py` |
| Polling drain: multi-item drain; between-items shutdown bound | `tests/daemons/dream/test_loop.py` |
| Retry exhaustion → `failed/`; attempt counter persists across restarts | `tests/daemons/dream/test_timeout_retry.py` |
| `acquire_start_lock` contention; file-based `status_daemon` (no/stale/live PID); `stop_daemon` stale-PID cleanup | `tests/daemons/dream/test_process.py` |
| Full daemon process lifecycle: start, SIGTERM stop, singleton, stale-PID overwrite | `tests/integration/test_daemon_lifecycle.py` |
| Session-end always-fires both KICKs; end-to-end KICK → queue file | `tests/integration/test_review_kick_end_to_end.py` |
| Auto-spawn race: exactly one daemon spawns under concurrent REPL bootstraps | `tests/integration/test_auto_spawn_race.py` |
| First-spawn notice + `co dream status` provenance fields | `tests/integration/test_autospawn_notice.py` |
| Multi-REPL: N + M KICKs → N + M queue files (no coalescing); UUID-distinct filenames | `tests/integration/test_multi_repl_kick.py` |
| Crash mid-process → restart re-processes file (idempotent) | `tests/integration/test_daemon_crash_recovery.py` |
| Per-prompt extraction quality (real model + real stores) | `evals/eval_domain_review.py` |

### Housekeeping

| Property | Test file |
|---|---|
| Scheduled tick boundaries: never-run, within interval, past interval ± run_at | `tests/daemons/dream/test_housekeeping.py` |
| Memory merge canonical pick: highest `recall_count` wins; tiebreaker by recency | `tests/daemons/dream/test_housekeeping.py` |
| Memory merge article exclusion: only notes/rules cluster | `tests/daemons/dream/test_housekeeping.py` |
| Memory decay candidacy: aged + never-recalled → archive | `tests/daemons/dream/test_housekeeping.py` |
| Memory decay recall protection: aged but recalled within `recall_protection_days` → protect | `tests/daemons/dream/test_housekeeping.py` |
| Memory `decay_protected` pin overrides age + recall | `tests/daemons/dream/test_housekeeping.py` |
| Decay phases run after merge timeout (memory + skill) | `tests/daemons/dream/test_housekeeping.py` |
| Skill canonical pick: highest `(len(recall_days), use_count)` wins | `tests/daemons/dream/test_skill_housekeeping.py` |
| Skill cluster: similar bodies grouped; pinned excluded | `tests/daemons/dream/test_skill_housekeeping.py` |
| Skill decay candidacy: aged + never-recalled → archive; recent recall protects; pinned protects; no sidecar → not eligible | `tests/daemons/dream/test_skill_housekeeping.py` |
| Skill archive move increments counter and lands in `user_skills_dir/.archive/` | `tests/daemons/dream/test_skill_housekeeping.py` |
| Manifest assembly does NOT mutate sidecar `recall_days` | `tests/daemons/dream/test_skill_housekeeping.py` |
| `HousekeepingStats` carries skill counters; state JSON round-trips | `tests/daemons/dream/test_skill_housekeeping.py` |
| Curator modules / `CURATOR_RUNS_DIR` / curator config fields are gone | `tests/daemons/dream/test_skill_housekeeping.py` |
| `co dream run` errors cleanly when daemon down; no sentinel written | `tests/daemons/dream/test_housekeeping.py` |
| End-to-end memory merge against real memory + real LLM | `evals/eval_memory.py` (W3.F), `evals/eval_daily_chat.py` (W1.D) |
