# Dream Queue Review Fixes

Three logical defects in the dream daemon's queue/review path, found during a
cross-review of co's pre-compaction knowledge-preservation against hermes's
`on_pre_compress` hook. One is load-bearing (defeats cross-compaction memory
preservation); two are low-severity hygiene.

## Context

The dream daemon is a split-brain async curator (`feedback_dream_daemon_split_brain`):
the REPL writes KICK files to `$CO_HOME/daemons/dream/queue/`, the daemon polls
FIFO and runs domain review agents (`memory_reviewer`, `skill_reviewer`) that
extract durable facts from a session transcript into long-term memory. The
filesystem queue is the sole cross-process bridge (`feedback_queue_sole_bridge`);
the producer never blocks on the consumer (`feedback_queue_decoupling_invariant`).

Relevant source (read and verified for this plan):

- `co_cli/daemons/dream/_loop.py` — `main_loop` (FIFO dequeue, retry, terminal moves), `_process_kick_file`, `_process_review`, `_maybe_housekeep`.
- `co_cli/daemons/dream/_queue.py` — `write_queue_item` (atomic), `list_queue_files`, `move_to_done`, `move_to_failed`.
- `co_cli/daemons/dream/_reviewer.py` — `process_review` loads the transcript (`load_transcript`) and dispatches to the domain reviewer. Both `_run_memory_review:118` and `_run_skill_review:135` serialize with `include_tool_results=False` — **tool returns are never visible to either reviewer** (load-bearing for the Defect-B scope).
- `co_cli/daemons/dream/_housekeeping.py` — `run_housekeeping` (merge + decay phases); runs on the empty-queue branch when `scheduled_tick_due`.
- `co_cli/daemons/dream/process.py:185-191` — derives `done_dir`/`failed_dir` from constants and creates them; the `main_loop(deps, queue_dir, state, cfg, shutdown)` call at `:228` does **not** pass them (only caller of `main_loop`, verified).
- `co_cli/main.py:64-80` — `_send_review_kick` (the only current KICK producer); `:132-153` `_finalize_turn`; `:197-210` session-end kicks; `:243-291` counter-driven kicks; `:294-326` `/compact` persistence.
- `co_cli/context/compaction.py:285-330` — `compact_messages` (the single chokepoint where whole messages are dropped: `dropped = messages[head_end:tail_start]` at `:313`); `:333-344` `commit_compaction`; `:347-411` `_record_proactive_outcome`; `:414+` `recover_overflow_history` (PATH 1 strip-only `:441-445` drops no message; PATH 2 `:466` calls `compact_messages`). Three callers of `compact_messages`: proactive `:628`, overflow PATH 2 `:466`, and `/compact` (`co_cli/commands/compact.py:43`, bounds `(0, old_len, old_len)` → drops the entire history).
- `co_cli/session/persistence.py:24` `append_messages` (public JSONL writer — writes fresh to a new path); `:46-62` `persist_session_history` (rewrites in place when `history_compacted=True`); `:65-74` `load_transcript` (returns the **first** N messages when `max_message_count` is set).
- `co_cli/config/core.py:42-47` — `DREAM_DAEMON_DIR`, `DREAM_QUEUE_DIR`, `DREAM_QUEUE_DONE_DIR = DREAM_QUEUE_DIR/"done"`, `DREAM_QUEUE_FAILED_DIR = DREAM_QUEUE_DIR/"failed"`.
- `co_cli/config/dream.py` — `DreamSettings` + `DREAM_ENV_MAP`.
- `co_cli/fileio/atomic.py:8-25` — `atomic_write_text` creates parent dirs.

### The three defects (verified against source)

**Defect A — split `failed/` directory.** The canonical failed bin is
`DREAM_QUEUE_FAILED_DIR = DREAM_QUEUE_DIR / "failed"` (= `daemons/dream/queue/failed/`),
and `process.py:188-191` pre-creates exactly that. But the loop's two failure
paths disagree:
- `_loop.py:125` (unreadable/corrupt KICK): `move_to_failed(item_path, queue_dir.parent / "failed", …)` → `daemons/dream/failed/` — **a different, non-canonical, non-pre-created directory**.
- `_loop.py:138` (processing failure / retries exhausted): `move_to_failed(item_path, queue_dir / "failed", …)` → `daemons/dream/queue/failed/` — canonical.
- `_loop.py:131` (success): `move_to_done(item_path, queue_dir / "done")` — canonical.

Corrupt KICKs land in an orphan bin nothing else references. Root cause: the
loop hand-builds paths with literals instead of using the injected dirs; line
125 drifted.

**Defect B — reviewer reads the post-compaction (lossy) transcript (the crux).**
`process_review` reads the **live** session file `{session_id}.jsonl` at dequeue
time (`_reviewer.py:157-161`). Compaction rewrites that same file in place,
lossily, to `head | marker | [todo] | tail` (`persistence.py:46-62`,
`compaction.py:295,322-329`). Because `load_transcript` returns the **first** N
messages, a review that runs after a compaction reads the pinned head + the
**compaction marker itself** as its source material — so the memory reviewer
re-summarizes co's own summary instead of the original turns. The content that
compaction discarded is never seen at **full fidelity** by any reviewer.

Severity is **fidelity degradation, not total loss.** The dropped span is not
gone — `compact_messages` writes a summary of it into the marker, and a
counter/session-end KICK that reads head+marker can still extract from that
summary. So a memory item *can* still be created; it is just sourced from co's
own lossy summary rather than the original turns. Worth fixing (full-fidelity
extraction before loss is the goal), but this is the gap that scopes the fix
narrow — see High-Level Design.

**The single chokepoint.** Whole messages are dropped in exactly one place:
`compact_messages` slices `dropped = messages[head_end:tail_start]`
(`compaction.py:313`). All three loss paths route through it — proactive PATH 2
(`:628`), overflow PATH 2 (`:466`), and `/compact` (`commands/compact.py:43`,
bounds `(0, old_len, old_len)` → drops everything). `recover_overflow_history`
PATH 1 (`:441-445`) drops **no** message — it only collapses `ToolReturnPart`s.
And the reviewer serializes with `include_tool_results=False`
(`_reviewer.py:118,135`), so it never sees tool returns anyway: PATH 1 carries
**zero reviewer-visible loss** and needs no snapshot. The only content the
reviewer would miss is user/assistant *text* in the dropped slice — present only
on the PATH-2 / `/compact` paths through `compact_messages`.

Note: the "first-N" read is **correct** for the live-file path when no compaction
has occurred — it reads exactly the message prefix that existed when the KICK was
enqueued; later-appended turns are covered by their own subsequent KICK. The
defect is solely the interaction with in-place compaction. (This is why the fix
is snapshot-before-rewrite, **not** switching to tail-N — see Open Questions.)

**Defect C — `queue/done/` grows unbounded.** Every processed KICK is moved to
`done/` (`_loop.py:131`) and nothing ever prunes it (verified: no `unlink`/
`rmtree`/prune anywhere in `co_cli/daemons/dream/`). A daily-driver REPL firing
review KICKs every N turns accumulates thousands of tiny JSON files indefinitely.

## Problem & Outcome

**Problem.** (A) Corrupt KICKs are misfiled to a non-canonical directory. (B)
Durable knowledge in the compacted-away span is never reviewed at full fidelity —
the reviewer reads the lossy marker after an in-place compaction. (C) `done/`
leaks disk without bound.

**Outcome.**
- (A) All three terminal transitions (done / failed-corrupt / failed-exhausted) use one consistent, injected set of directories.
- (B) At the `compact_messages` chokepoint — the one place whole messages are dropped — the full pre-compaction message list is snapshotted to an immutable file once per logical compaction and a memory review KICK is fired against that snapshot, so the dropped content is reviewed at full fidelity before it is gone. The live-file read path (first-N) is unchanged for non-compaction KICKs.
- (C) `done/` (and orphaned snapshots) are pruned by age during the daily housekeeping pass.

**Failure cost.** Without (B), for any session long enough to cross the compaction
threshold, every fact, decision, constraint, or learning that lives only in the
compacted-away middle is captured — if at all — only at **summary fidelity**: the
reviewer re-summarizes co's own marker rather than the original turns, so nuance,
exact wording, and detail the summary dropped are unrecoverable. The session
continues and the degradation only surfaces later as the agent "remembering" a
blurred version of something the user said earlier in the same long session. (It
bites only sessions that actually compact — but for those, the original turns are
gone the moment the file is rewritten in place.) Without (A), corrupt-KICK
diagnostics are scattered and easy to miss. Without (C), long-lived `$CO_HOME`
installs accrue unbounded small files.

## Scope

**In scope.**
- `_loop.py` terminal-move directory consistency; thread `done_dir`/`failed_dir` from `process.py` into `main_loop`.
- A snapshot-before-rewrite path at the **`compact_messages` chokepoint** (the one place whole messages are dropped): capture the full pre-drop message list there, once per logical compaction, write it to a new `snapshots/` dir, fire a **memory** review KICK referencing it; extend the KICK schema with an optional `transcript_override`; extend `process_review` to read an override snapshot (uncapped) instead of the live file; delete the snapshot on terminal KICK transition. One capture point covers all three loss paths (proactive PATH 2, overflow PATH 2, `/compact`) uniformly.
- A shared KICK-producer primitive (`co_cli/daemons/dream/kick.py`) so both `main.py` and `compaction.py` produce KICKs without `main.py`-private duplication or a context→dream import cycle.
- `done/` + orphaned-snapshot age prune as a housekeeping phase; `done_retention_days` config + env-map entry.

**Out of scope.**
- `recover_overflow_history` PATH 1 (strip-only-fits). It drops no message — only `ToolReturnPart`s, which the reviewer excludes (`include_tool_results=False`). Snapshotting there would capture nothing the reviewer could see. No capture on this path by design.
- Skill review on compaction (memory-only; see Open Question 1). The skill reviewer also excludes tool results, and a dropped mid-conversation span rarely holds net-new procedural learnings; deferred behind telemetry.
- Changing the live-file first-N read semantics (it is correct; see Open Questions).
- Note: the **`/compact` command** is now covered *for free* — it routes through `compact_messages`, so the chokepoint capture fires there too. Excluding it would cost *more* code (a caller-discriminating flag) than including it, and full-fidelity extraction is just as desirable for a user-invoked compaction that drops the entire history. The previous "out of scope" carve-out dissolves.
- De-duplicating overlapping review coverage (counter KICK + snapshot KICK may re-review the same span; relies on existing housekeeping merge — see Open Question 4).
- Any change to compaction boundary planning, marker shape, or the summarizer.
- `docs/specs/` edits (handled by `sync-doc` post-delivery).

## Behavioral Constraints

- **Producer never blocks on consumer** (`feedback_queue_decoupling_invariant`). The snapshot write + KICK write are local filesystem writes on the foreground path; the daemon processes them out of band. No socket nudges (`feedback_queue_sole_bridge`).
- **No backward-compat shims** (`feedback_zero_backward_compat`, `feedback_no_migration_code`). The KICK schema gains an optional field; old in-flight KICKs without it read the live file as before — that is the natural default, not a compat shim. No reader for a legacy snapshot format.
- **CO_HOME override safety** (Known Pitfall). `main_loop` must operate on **injected** dirs, never module-level `DREAM_QUEUE_*` constants, so tests overriding `CO_HOME` are honored. The snapshot dir is derived from `deps`-reachable config, not hardcoded.
- **`__init__.py` docstring-only**; the new `kick.py` is a named submodule, not re-exported from `__init__`.
- **Surgical changes** — touch only the queue/review/compaction-snapshot path.

## High-Level Design

### Snapshot-before-rewrite (Defect B)

**Single capture point — `compact_messages`.** Capture at the top of
`compact_messages`, once per logical compaction, via a shared helper. This is the
sole place whole messages are dropped, and all three loss paths (proactive PATH 2
`:628`, overflow PATH 2 `:466`, `/compact` `commands/compact.py:43`) route
through it — so one capture point covers them uniformly. PATH 1 (strip-only) does
not call `compact_messages` and carries no reviewer-visible loss, so it is
correctly *not* captured. Fire **memory review only** (Open Question 1).

```python
def _snapshot_and_kick_review(ctx, messages):
    deps = ctx.deps
    if deps.runtime.compaction_applied_this_turn:   # already snapshotted this compaction
        return
    if not messages or not deps.config.skills.review_enabled or deps.model is None:
        return
    session_id = deps.session.session_path.stem if deps.session.session_path else ""
    if not session_id:                              # skip when no real session
        return
    try:
        DREAM_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S.%f")   # same pattern as _send_review_kick
        snapshot_path = DREAM_SNAPSHOTS_DIR / f"{session_id}-{ts}-{uuid4()}.jsonl"
        append_messages(snapshot_path, messages)        # full pre-drop list — head/tail continuity
        write_review_kick(domain="memory", session_id=session_id,
                          persisted_message_count=None,
                          transcript_override=str(snapshot_path))
    except Exception:
        log.warning("compaction review snapshot/kick failed", exc_info=True)  # best-effort
```

**Once-per-compaction guard (replaces the `snapshot: bool` param).** The helper
self-guards on `compaction_applied_this_turn`. The proactive no-progress
escalation (`:655`) re-enters `recover_overflow_history` → `compact_messages`
*after* `_record_proactive_outcome` already ran `commit_compaction` (flag now
True), so the escalation's second `compact_messages` call is suppressed with **no
new state and no `snapshot` param**. The first compaction of a turn always finds
the flag False (`reset_for_turn`), so proactive / overflow / `/compact` each
snapshot exactly once. Trade-off: a rare *second independent* compaction within
one turn (a separate history-processor pass) is not full-fidelity snapshotted;
that span falls back to the existing counter/session-end KICK at summary
fidelity — no regression versus today. Capturing the full pre-drop `messages`
(not just the dropped slice) gives the reviewer head/tail continuity.

**Wiring.** One line at the top of `compact_messages`, before the slice:
`_snapshot_and_kick_review(ctx, messages)`. `recover_overflow_history` keeps its
existing signature; no per-trigger wiring, no PATH 1 capture. `compact_messages`'
docstring gains a note that it best-effort-snapshots the pre-drop list for review
(it still does not write runtime — the commit invariant is untouched).

**Shared KICK producer.** Extract
`write_review_kick(*, domain, session_id, persisted_message_count, transcript_override=None)`
into a new module `co_cli/daemons/dream/kick.py` depending only on config
constants + `_queue.write_queue_item` + json/uuid/datetime. `main.py`'s
`_send_review_kick` is replaced at its call sites by `write_review_kick`.
`compaction.py` imports `write_review_kick` from `kick.py`. Import cycle verified
clean (Core Dev CD-m-5): `kick.py` imports nothing from `context/`,
`daemons/dream/__init__.py` is docstring-only, and nothing in `kick.py`'s chain
imports `context.compaction`.

**Consumer side.** `_process_kick_file` reads `payload.get("transcript_override")`
(**`.get`, not subscript** — existing non-override KICKs omit the key; CD-m-6) and
passes it through `_process_review` → `process_review(..., transcript_override=None)`.
When present, `process_review` reads `load_transcript(Path(override))` **uncapped**;
when absent, the existing live-file + `max_message_count` path is unchanged
(including the missing-file benign no-op). `persisted_message_count` is set to
`None` (present, not omitted) on snapshot KICKs, so the existing
`payload["persisted_message_count"]` subscript at `_loop.py:152` does not KeyError.

**Snapshot lifecycle.** The snapshot must survive retries (a transient LLM failure
re-reads it). It is deleted only on a **terminal** transition — after
`move_to_done` and after `move_to_failed`-on-exhaustion — by a
`_cleanup_override(payload)` helper that unlinks `payload.get("transcript_override")`
(`missing_ok=True`) using the **in-memory `payload` the loop already holds** (not a
re-read). For the **corrupt-KICK** terminal path (`_loop.py:123-126`), the payload
is unparseable, so its override is unknown and cannot be cleaned here — those
snapshots are reclaimed only by the housekeeping orphan sweep (Defect C). This is
acceptable (corrupt KICKs are rare given atomic writes) and is stated so the
implementer does not try to read an override at the corrupt site (CD-m-1).

```
compact_messages(ctx, messages, bounds)          dream daemon (out of band)
  _snapshot_and_kick_review(ctx, messages) ──┐      poll FIFO
    guard: compaction_applied_this_turn      │      payload.get("transcript_override")
    append_messages(snapshot, messages)      │      load_transcript(override)  # full fidelity
    write_review_kick(memory, override)  ────┘      run memory_reviewer
  dropped = messages[head_end:tail_start]           on terminal move: _cleanup_override → unlink
  ... assemble head|marker|tail ...

callers (all route through compact_messages):
  proactive_window_processor PATH 2 (:628)   ─┐
  recover_overflow_history    PATH 2 (:466)   ─┼─→ snapshot fires once (guard suppresses escalation re-entry)
  /compact command            (:43)           ─┘
  recover_overflow_history    PATH 1 (strip-only) → no compact_messages, no snapshot (no visible loss)
```

### Directory consistency (Defect A)

Thread the already-computed `done_dir` and `failed_dir` from `process.py` into
`main_loop(deps, queue_dir, done_dir, failed_dir, state, cfg, shutdown)` and use
them at all three terminal-move sites. This removes the `queue_dir.parent` drift
and the literal `"done"`/`"failed"` strings, and keeps the dirs injected (CO_HOME-safe).

### Retention prune (Defect C)

Add a final phase to `run_housekeeping` that deletes files in `done_dir` older
than `cfg.done_retention_days` (default 7) by mtime, and sweeps orphaned snapshot
files in `DREAM_SNAPSHOTS_DIR` older than the same window. `failed/` is left
intact (rare, diagnostic). Runs on the daily housekeeping cadence — `done/` does
not need sub-daily pruning.

## Tasks

### ✓ DONE TASK-1 — Consistent terminal-move directories
- **files:** `co_cli/daemons/dream/_loop.py`, `co_cli/daemons/dream/process.py`
- Change `main_loop` signature to accept `done_dir: Path` and `failed_dir: Path`; replace the three inline path derivations (`queue_dir.parent / "failed"` at :125, `queue_dir / "done"` at :131, `queue_dir / "failed"` at :138) with the injected `failed_dir` / `done_dir`. Update the `process.py:228` call to pass the `done_dir`/`failed_dir` it already computes.
- **done_when:** a unit test drives `main_loop` (one iteration) with a corrupt/unparseable queue file and an injected `failed_dir`, and asserts the file lands in that single injected `failed_dir` (not `queue_dir.parent / "failed"`); a second case asserts a successful KICK lands in the injected `done_dir`.
- **success_signal:** corrupt and exhausted-retry KICKs both arrive in the same canonical `failed/` directory.
- **prerequisites:** none.

### ✓ DONE TASK-2a — Shared KICK producer
- **files:** `co_cli/daemons/dream/kick.py` (new), `co_cli/main.py`
- Create `kick.py` with `write_review_kick(*, domain, session_id, persisted_message_count, transcript_override=None)`, moving the body of `main.py:_send_review_kick` (timestamped `{ts}-{uuid}.json` filename, `created_at`, atomic write to `DREAM_QUEUE_DIR`). Add the optional `transcript_override` field to the payload (omit the key when `None`). Repoint `main.py`'s call sites at `write_review_kick` (passing `session_id=deps.session.session_path.stem`); delete or thin `_send_review_kick`.
- **done_when:** existing session-end / counter KICK behavior is exercised by a test that calls the `main.py` producer path and asserts a well-formed payload (`domain`, `session_id`, `persisted_message_count`, `created_at`, no `transcript_override` key) lands in the queue dir.
- **success_signal:** N/A (refactor — no behavior change for the existing producer).
- **prerequisites:** none.

### ✓ DONE TASK-2b — Snapshot capture + memory KICK at the `compact_messages` chokepoint
- **files:** `co_cli/context/compaction.py`, `co_cli/config/core.py`
- Add `DREAM_SNAPSHOTS_DIR = DREAM_DAEMON_DIR / "snapshots"` to `config/core.py`.
- Add the `_snapshot_and_kick_review(ctx, messages)` helper (see High-Level Design) to `compaction.py`: self-guards on `compaction_applied_this_turn` (once per logical compaction), `messages` non-empty, `review_enabled`, `model is not None`, and non-empty `session_id`; mkdirs the snapshots dir; `append_messages(snapshot_path, messages)`; fires **one** memory `write_review_kick` with `transcript_override=str(snapshot_path)`, `persisted_message_count=None`. Best-effort: wrapped in try/except, never aborts compaction.
- Call it **once at the top of `compact_messages`** (before the `dropped` slice), with the `messages` arg. This covers proactive PATH 2, overflow PATH 2, and `/compact` uniformly; no per-trigger wiring, no `snapshot` param on `recover_overflow_history`. Add a docstring note to `compact_messages`.
- **done_when:** (1) a test invokes `compact_messages` on a multi-turn history (`compaction_applied_this_turn` False, review enabled, stub model present, injected snapshots dir); asserts a snapshot file exists containing the **full original** message set and **exactly one** memory KICK carries `transcript_override` to it (and **no** skill KICK). (2) a test invokes `compact_messages` with `compaction_applied_this_turn` already True (the escalation re-entry condition) and asserts **no** snapshot/KICK is produced (guard suppression). (3) a test drives `recover_overflow_history` down PATH 1 (strip-only-fits) and asserts **no** snapshot/KICK is produced (PATH 1 has no reviewer-visible loss).
- **success_signal:** after a whole-message-dropping compaction (proactive / overflow PATH 2 / `/compact`), the original pre-drop content is captured verbatim in one snapshot referenced by exactly one memory review KICK — no double-fire on no-progress escalation, nothing on PATH 1.
- **prerequisites:** TASK-2a.

### ✓ DONE TASK-2c — Reviewer reads the override snapshot; loop cleans it up
- **files:** `co_cli/daemons/dream/_loop.py`, `co_cli/daemons/dream/_reviewer.py`
- `_process_kick_file`: read `payload.get("transcript_override")` (**`.get`, not subscript**) and pass through `_process_review`. `process_review(deps, domain, session_id, persisted_message_count, transcript_override=None)`: when set, `messages = load_transcript(Path(transcript_override))` (uncapped); else the existing live-file + `max_message_count` path (unchanged, incl. missing-file benign no-op). The consumer stays domain-agnostic — override snapshots currently only ride memory KICKs, but no consumer code special-cases the domain.
- Add `_cleanup_override(payload)` in `_loop.py` that unlinks `payload.get("transcript_override")` with `missing_ok=True`, operating on the **in-memory `payload`** the loop holds. Call it after `move_to_done` (`:131`) and after the exhausted-retry `move_to_failed` (`:138`) — **not** on the retry/backoff branch and **not** at the corrupt-payload `move_to_failed` (`:125`, payload unreadable; orphan sweep reclaims those).
- **done_when:** a test runs one `main_loop` iteration over a memory KICK whose `transcript_override` points at a snapshot file containing a known durable fact; with `run_standalone` stubbed to capture its prompt, asserts the captured transcript is **non-empty and contains that fact** (proves the snapshot was actually read, not a silent missing-file no-op), and that the snapshot file is unlinked after the successful terminal move.
- **success_signal:** the memory reviewer sees the original dropped turns at full fidelity, and the snapshot is removed once the KICK is done.
- **prerequisites:** TASK-2a, TASK-2b.

### ✓ DONE TASK-3 — Retention prune for `done/` + orphaned snapshots
- **files:** `co_cli/daemons/dream/_housekeeping.py`, `co_cli/config/dream.py`
- Add `done_retention_days: int = Field(default=7, ge=1)` to `DreamSettings` and `"done_retention_days": "CO_DREAM_DONE_RETENTION_DAYS"` to `DREAM_ENV_MAP`. Add a final phase to `run_housekeeping` that deletes files in `done_dir` with mtime older than `cfg.done_retention_days`, and sweeps `DREAM_SNAPSHOTS_DIR` files older than the same window (orphan backstop). Record counts on `HousekeepingState.stats` consistent with existing phase stats.
- **done_when:** a test seeds `done_dir` with two files (one with an old mtime, one fresh) and a stale snapshot, runs the prune phase, and asserts only the aged files are deleted and the fresh one remains.
- **success_signal:** `done/` and orphaned snapshots stay bounded across daily housekeeping passes.
- **prerequisites:** none (snapshot sweep is a no-op until TASK-2b ships, which is fine).

## Testing

- Functional only, asserting observable behavior (`feedback_functional_tests_only`): file lands in dir X; reviewer invoked with content Y; file unlinked; aged file pruned. No assertions on internal field/struct shape beyond the KICK payload contract a consumer depends on.
- All dream tests inject `queue_dir`/`done_dir`/`failed_dir`/snapshot dir from a temp `CO_HOME` — never module constants.
- The reviewer-content assertion in TASK-2c stubs the review agent run (`run_standalone`) to capture the transcript it receives; this is an integration-boundary assertion, not a live LLM call. Per `feedback_no_eval_test_driven_api`, keep the stub seam in the test layer, not in production signatures.
- Run scoped: `uv run pytest tests/<dream paths> -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-dream-queue.log`. Fail-fast; RCA any slow/stalled call rather than widening timeouts.

## Open Questions

1. **Fire both memory + skill review on compaction, or memory only?** **RESOLVED (Gate 1, revised): memory only.** A prior round resolved "both," but two facts narrow it to memory: (a) the skill reviewer *also* serializes with `include_tool_results=False` (`_reviewer.py:135`), so it cannot see the tool-return bodies that were the main argument for covering the strip path; and (b) a dropped mid-conversation span rarely holds net-new *procedural* learnings, while it commonly holds facts/decisions/constraints. Memory-only halves the per-compaction LLM cost (the single biggest cost lever) on a path that can fire repeatedly in a long session. Skill-review-on-compaction stays as a telemetry-gated add: if compaction-frequency × skill-capture-yield data later justifies it, add a second `write_review_kick(domain="skill", ...)` to the helper — the override consumer is already domain-agnostic.
2. **Why snapshot-before-rewrite and not tail-N read?** Switching `load_transcript` to tail-N would make counter/session-end KICKs read the *latest* N including post-enqueue turns, causing double-review and still reading the lossy file after compaction. Snapshot-before-rewrite is the only option that preserves the dropped content at full fidelity. Recorded as a rejected alternative; confirm the team agrees first-N stays for the live path.
3. **`done_retention_days` default (7)** — arbitrary; confirm acceptable, or prefer a count-based cap (keep last N) over age-based.
4. **Overlapping review coverage (punted — confirm acceptable).** A snapshot KICK references the *full* original list (head+tail+dropped), which overlaps with what a same-window counter/session-end KICK already reviewed, so the same span can be reviewed twice and produce near-duplicate memory items. Forcing dedup into the producer would couple producer to consumer state (violates the decoupling invariant), so it is punted to the existing housekeeping merge. **Verification required during dev (PO-m-2):** confirm the housekeeping merge dedupes *content-equivalent* items (token-Jaccard clustering in `_identify_mergeable_clusters` / `_cluster_by_similarity`), not just exact-name collisions. If it only dedupes by name, the duplicate-memory risk is real and warrants a follow-up note. → **RESOLVED (verified in source):** `merge_memory` → `_identify_mergeable_clusters` → `_cluster_by_similarity` clusters by `token_jaccard(a.content, b.content) ≥ consolidation_similarity_threshold` (`_housekeeping.py:78`) — content-equivalent, not name-based. Near-duplicate memory items from overlapping reviews cluster and merge. Producer-side dedup deliberately NOT added (would couple producer to consumer, violating `feedback_queue_decoupling_invariant`).

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev dream-queue-review-fixes`

---

## Delivery Summary — 2026-06-13

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | corrupt KICK → injected `failed_dir` (not `queue_dir.parent`); success → injected `done_dir` | ✓ pass |
| TASK-2a | producer path writes well-formed payload, no `transcript_override` key when unset | ✓ pass |
| TASK-2b | `compact_messages` snapshots full pre-drop history + one memory KICK; guard suppresses escalation; PATH-1 no snapshot | ✓ pass |
| TASK-2c | override-snapshot KICK read at full fidelity (fact present in reviewer prompt) + snapshot unlinked on terminal move | ✓ pass |
| TASK-3 | aged `done/` + stale snapshot pruned, fresh `done/` kept | ✓ pass |

**Team:** TL — TASK-2a, TASK-1, TASK-2b, TASK-2c. Dev-1 — TASK-3 (parallel, disjoint files).

**Extra files touched (beyond task `files:`):**
- `co_cli/daemons/dream/_state.py` — `done_pruned` counter on `HousekeepingStats` (TASK-3; required for the prune phase to record counts).
- `tests/test_flow_exit_cleanup_review.py`, `tests/integration/test_review_kick_end_to_end.py`, `tests/integration/test_multi_repl_kick.py` — repaired by the TASK-2a producer move: the producer relocated from `main.py` to `co_cli/daemons/dream/kick.py`, so the `CO_HOME` reload fixtures now also reload `kick`, and stale `main._send_review_kick` / `main.DREAM_QUEUE_DIR` references were repointed.

**Tests:** scoped — 74 passed, 0 failed (`tests/daemons/dream/`, compaction snapshot + recovery, exit-cleanup, both kick integration tests). Fail-fast, all logs under `.pytest-logs/`. One RCA mid-run: the producer-move broke `CO_HOME`-reload tests (producer's `DREAM_QUEUE_DIR` binding lived in the un-reloaded `kick` module) — fixed by adding `kick` to the reload set.

**Doc Sync:** fixed — `dream.md` (producer rename, KICK schema `transcript_override`, `main_loop`/`process_review` signatures, `snapshots/` dir, retention-prune phase + `done_pruned` + `done_retention_days`, §5/§6/§7 entries), `compaction.md` (pre-compaction snapshot side-effect), `config.md` (`done_retention_days`). `sessions.md` + `01-system.md` index clean.

**Overall: DELIVERED**
All five tasks pass `done_when`; lint clean; 74 scoped tests green; docs synced. The leaner Defect-B design (single chokepoint capture, memory-only, guard-based escalation suppression) is implemented as planned.

### Post-delivery trace fixes — 2026-06-13

A flow trace surfaced two issues; both fixed:

1. **Snapshot guard over-suppressed genuine in-turn compactions (correctness).** The escalation-suppression guard keyed on `compaction_applied_this_turn` (turn-scoped), so only the *first* compaction per turn snapshotted — a second independent compaction later in the same turn (heavy tool-use turns) silently dropped to summary-fidelity review. Replaced with a one-shot `runtime.skip_compaction_snapshot` flag set by `proactive_window_processor` only around its no-progress escalation call (try/finally bounded). Now every distinct logical compaction snapshots; only the escalation re-entry of the *same* compaction is suppressed. Added `test_second_in_turn_compaction_still_snapshots` (regression guard). Files: `co_cli/deps.py` (flag + reset), `co_cli/context/compaction.py`, `tests/test_flow_compaction_review_snapshot.py`.
2. **Test-hygiene fragility (latent).** The no-leak property rests on `review_enabled=False` default + the `Path(".")` session-id guard; a future compaction test enabling review without monkeypatching the snapshot/queue dirs would silently write to real `~/.co-cli` with no failing assertion. Documented as a caveat in `dream.md` §7.

Resolved Open Question 4 in source: housekeeping merge dedupes by `token_jaccard` on content (`_housekeeping.py:78`), so overlapping-review duplicates collapse; no producer-side dedup added (decoupling invariant).

Intentionally **not** changed: snapshot stores full messages incl. tool returns (reviewer strips them, but full capture preserves fidelity for any future reviewer); `_snapshot_and_kick_review` kept at the **top** of `compact_messages` (captures the pristine pre-partition list — moving it later risks lower fidelity; the rare spurious-review-on-throw is harmless and idempotent).

---

## For /review-impl — verification checklist

Scoped tests passed during dev, but these need the full-suite + behavioral pass to confirm. Each item names what to check and why it's not already nailed by a unit test.

- [ ] **Full suite green.** Run the whole suite (not just scoped). Confirm the refactored producer move (`kick.py`) and the `main_loop` signature change didn't break any test outside the touched set.
- [ ] **No test leaks to real `~/.co-cli`.** During the full run, assert `~/.co-cli/daemons/dream/queue/` and `~/.co-cli/daemons/dream/snapshots/` gain no files. This is the latent risk from the `dream.md` §7 caveat — a compaction test enabling review without monkeypatching the dirs would write there silently (no assertion catches it). Spot-check: `ls ~/.co-cli/daemons/dream/{queue,snapshots}/ 2>/dev/null` before vs after the run.
- [ ] **Proactive no-progress escalation path (NOT unit-covered).** The one-shot `skip_compaction_snapshot` suppression is only unit-tested by setting the flag directly. Behaviorally verify the real path: a proactive compaction that makes no progress → escalates to `recover_overflow_history` → fires **exactly one** snapshot/KICK for that logical compaction (escalation re-entry suppressed), and the flag is cleared afterward (try/finally). `tests/test_flow_compaction_proactive.py` exercises proactive with a real model — confirm it still passes and add/observe an escalation case if absent.
- [ ] **Finding-1 fix behavior.** Confirm `test_second_in_turn_compaction_still_snapshots` passes and that a genuine second compaction in one turn produces a distinct snapshot+KICK (the prior turn-scoped guard would have suppressed it).
- [ ] **Repaired integration tests under full suite.** `tests/integration/test_review_kick_end_to_end.py` and `tests/integration/test_multi_repl_kick.py` were repointed to `kick.write_review_kick` + reload `kick`; confirm green in the full run (they passed scoped).
- [ ] **Defect-A regression.** Confirm corrupt and exhausted-retry KICKs both land in the single injected `failed_dir` and `done_dir` (no `queue_dir.parent/"failed"` drift) — `tests/daemons/dream/test_loop.py`.
- [ ] **Defect-C prune.** Confirm aged `done/` + orphaned `snapshots/` are pruned and `failed/` is untouched — `tests/daemons/dream/test_housekeeping.py`.
- [ ] **Snapshot lifecycle.** Confirm a successful override-KICK unlinks its snapshot on terminal move, and a corrupt-payload KICK's snapshot is left for the housekeeping orphan sweep (not cleaned at the corrupt site) — `tests/daemons/dream/test_override_snapshot.py` covers the success path; the corrupt-orphan path is sweep-only by design.
- [ ] **Open Q4 (already source-verified).** Optional: confirm housekeeping merge collapses content-equivalent duplicate memory items (token-Jaccard), so overlapping review coverage doesn't accumulate near-dupes.

---

## Implementation Review — 2026-06-14

Stance: issues exist — PASS is earned. Five `✓ DONE` tasks reviewed: TASK-1, TASK-2a, TASK-2b, TASK-2c, TASK-3. Per-task evidence subagents (parallel) → adversarial cold re-read → auto-fix → full suite + leak guard → behavioral verification.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | corrupt→injected `failed_dir` (not `queue_dir.parent`); success→injected `done_dir` | ✓ pass | `_loop.py:95-103` sig takes `done_dir`/`failed_dir`; `:127`/`:133`/`:141` all use injected dirs (grep: no `queue_dir.parent`, no `"done"`/`"failed"` literals); `process.py:186-191` derives+mkdirs, `:228` passes them. Test `test_loop.py:123-156` asserts corrupt→injected & NOT `drifted_failed`; `:159-188` success→`done_dir`. 4 passed. |
| TASK-2a | producer writes well-formed payload, no `transcript_override` key when unset | ✓ pass | `kick.py:24-30` keyword-only sig; payload `:44-49`; key omitted when None `:50-51`; atomic write `:52`. `main.py:187-246` call sites repointed; `_send_review_kick` deleted (grep: 0 hits). Import cycle clean (live import check ok). Test `test_flow_exit_cleanup_review.py:116-132` asserts no `transcript_override` key. |
| TASK-2b | full-history snapshot + exactly one memory KICK; guard suppresses escalation re-entry; PATH-1 no snapshot | ✓ pass | helper `compaction.py:290-326` (all guards `:307-313`, full `messages` `:318`, one memory KICK `:319-324`, logged best-effort `:325-326`); called at top of `compact_messages:357` pre-slice. One-shot flag `deps.py:216`, reset `:243`, set only around escalation `compaction.py:704-708` try/finally. PATH 1 returns at `:492` w/o `compact_messages`. 4 tests pass incl. `test_second_in_turn_compaction_still_snapshots`. |
| TASK-2c | override snapshot read at full fidelity (fact in reviewer prompt) + unlinked on terminal move | ✓ pass | `.get` (not subscript) read `_loop.py:172`; `process_review` override branch uncapped `_reviewer.py:163-168`, domain-agnostic; `_cleanup_override` `_loop.py:150-162` called after `move_to_done:134` & exhausted `move_to_failed:142`, NOT on retry `:143-145` nor corrupt `:125-128`. `persisted_message_count` key always present (`kick.py:47`). Test `test_override_snapshot.py` asserts fact in prompt + unlink. 1 passed. |
| TASK-3 | aged `done/` + stale snapshot pruned, fresh kept; `failed/` untouched | ✓ pass | `done_retention_days: Field(default=7, ge=1)` `dream.py:31` + env-map `:14`; prune phase `_housekeeping.py:494-531` mtime cutoff, both dirs same window, `failed/` never referenced; `done_pruned` counter `_state.py:49` (naming consistent). Test `test_housekeeping.py:353-385` real `os.utime`. 15 passed. |

Adversarial pass re-read every high-risk cited line cold and **confirmed all five PASS** (defeating lines: `compaction.py:308` early-return + `:708` finally; `kick.py:47` unconditional key; `_loop.py:134`/`:142` cleanup-after-move + `:143-145` retry keeps snapshot; `:492` unconditional PATH-1 return; `:317`/`:323` same-path snapshot+kick).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Test leak to real `~/.co-cli`: `_make_deps`/teardown reloaded only `core_mod`, so threshold-crossing KICKs wrote to real `kick.DREAM_QUEUE_DIR` (the §7 latent risk, made real by the TASK-2a producer move) | `tests/test_flow_post_turn_hook.py:21-77` | blocking | Added `kick_mod`+`main_mod` reload to `_make_deps` and teardown, mirroring the corrected sibling `test_flow_exit_cleanup_review.py`. Removed the two leaked artifact KICKs from real `~/.co-cli`. Re-verified: 0 new files in real queue across full suite. |

Two adversarial observations classified **non-blocking** (working-as-specified, not fixed):
- **Cross-layer double snapshot/KICK** (proactive + L3 overflow-recovery `orchestrate.py:721` in one turn): the post-Finding-1 design *intends* every distinct logical compaction to snapshot; an L3 recovery is a distinct trigger. Duplicate review coverage is explicitly accepted (Scope out-of-scope + Open Q4 — housekeeping merge dedupes content-equivalent items by token-Jaccard). Adding cross-layer suppression would risk reintroducing the exact over-suppression bug Finding-1 fixed. Recorded as a minor efficiency follow-up only.
- **Manual `/compact` fires a review KICK**: explicitly specified ("`/compact` covered for free … full-fidelity extraction just as desirable"). Designed feature, not a surprise.

### Tests
- Command: `uv run pytest -x -q` (full suite, after fix)
- Result: **700 passed, 0 failed**
- Log: `.pytest-logs/20260614-001238-review-impl-final.log`
- **No-leak guard**: file-list fingerprint of `~/.co-cli/daemons/dream/{queue,snapshots}` before vs after the full run → **zero new files** (latent §7 risk closed).

### Behavioral Verification
- `co status`: N/A — no such command in this project.
- `co dream status`: ✓ healthy — runs cleanly, reads the injected queue/failed dirs (TASK-1 surface), returns `{"running": false, "queue_depth": 122, "failed_count": 0}`. The 122 is real pre-existing `done/` accumulation — exactly the Defect-C condition TASK-3's daily prune now bounds.
- `success_signal`s: TASK-1 (corrupt+exhausted → one canonical `failed/`), TASK-2b (one snapshot+memory KICK per logical compaction, none on PATH 1), TASK-2c (reviewer sees dropped turns at full fidelity + snapshot removed), TASK-3 (`done/`+snapshots bounded) — each verified by its green functional test. TASK-2a is N/A (refactor).

### Checklist disposition
Full suite green ✓; no leaks ✓ (one leak found+fixed); Finding-1 regression test passes ✓; repaired integration tests green ✓; Defect-A/C regressions green ✓; snapshot lifecycle ✓; Open Q4 source-verified ✓. Proactive no-progress *escalation* path is covered at the unit level (flag-set + PATH routing) and by the green `test_flow_compaction_proactive.py` in the full run; the dedicated escalation-emits-exactly-one behavioral case remains flag-driven (acceptable — the suppression mechanism and PATH-1/PATH-2 routing are both directly tested).

### Overall: PASS
All five tasks satisfy `done_when` with cited evidence; adversarial re-read confirmed every guard; one real test-isolation leak found and fixed (with cleanup of the leaked artifacts); full suite 700 green with a verified zero-leak guard; lint clean; behavioral check healthy. Ready for Gate 2.
