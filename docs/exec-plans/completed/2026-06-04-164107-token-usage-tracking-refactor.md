# Token usage tracking — durable per-turn ledger + `/usage` reporting command

## Context

This plan builds on the just-delivered `docs/exec-plans/completed/2026-06-04-130800-drop-reported-realtime-trigger.md`
(delivered 2026-06-04), which removed `TokenTrackingCapability`, `last_reported_input_tokens`, `turn_usage`,
and `_merge_segment_usage`. After that change, token-usage data is **transient and scattered**:

- `current_request_tokens_estimate` (`deps.runtime`, `chars/4` realtime estimate) → status-line context-%
  (`main.py:448`). Live snapshot only; never persisted.
- `latest_usage` (provider `RunUsage` on `_TurnState`, `orchestrate.py:152`) → forwarded opaquely to spans
  and `TurnResult.usage`; **never read by the REPL, never persisted, lost at turn end**.
- Direct provider reads in `llm/call.py:71-76` and `observability/capability.py:199-204` → emitted as span
  attributes into the **rotating** `co-cli-spans.jsonl` (CLI-only, ages out via backup rotation).

**There is no durable record of how many tokens a session — or the user across days — has spent.** The user
wants a `/usage` command that reports token usage for the **current session** and for **rolling time
windows** (`week` = last 7 days, `month` = last 30 days, `total` = all time).

**Source-verified findings (2026-06-04 HEAD):**
- Two model-call paths, no single chokepoint. Agent runs (chat agent + delegation/task subagents) are
  observed by `ObservabilityCapability.after_model_request` (`capability.py:192`, already reads
  `response.usage`); both agents register it first (`build.py:54`,`:111`). Direct calls — the compaction
  summarizer and dream merges — go through `llm_call` (`call.py:63-71`) which **bypasses capabilities**.
  Capturing *all* model calls requires hooking **both** sites.
- `fork_deps` (`deps.py:359-398`) gives each delegation/task subagent a **fresh `CoRuntimeState`** and a
  session state **without the parent's `session_path`**. So a counter on `deps.runtime` would not capture
  subagent tokens. But `fork_deps` deliberately **shares mutable coordination objects by reference**
  (`file_tracker`, `resource_locks`, `tool_dispatch_sem`, `deps.py:366-370`). A fork-shared accumulator on
  `CoDeps` is the established pattern for cross-agent capture.
- Transcripts cannot be the usage source: compaction **rewrites the file in place** and discards
  pre-compaction `ModelResponse`s (`persistence.py:36-43,58-59`), so summing usage from transcripts would
  undercount any compacted session. Spans rotate and are CLI-only. A dedicated durable store is required.
- The per-turn durable boundary already exists: `persist_session_history` is called once per turn from the
  REPL (`main.py:135`, `:294`), with `deps.session.session_path` in hand — the natural flush point.
- Session files embed their creation timestamp (`filename.py`), but windowing should follow **activity
  time** (when tokens were spent), not session creation — so a per-turn timestamped record is needed for
  precise rolling windows, not a per-session cumulative date.

## Problem & Outcome

**Problem.** Provider-reported token usage is captured at the model-call boundary but immediately discarded
(forwarded to ephemeral spans, then dropped at turn end). There is no way for the user to see what a session
or a time window has cost in tokens.

**Outcome.** Every model response across **all** agent paths (chat, delegation/task subagents, the
compaction summarizer, direct `llm_call`s) is accumulated into a **fork-shared, turn-scoped accumulator** on
`CoDeps`, fed by provider-reported `RunUsage` (ground truth, not `chars/4`). At each turn boundary the
turn's totals are appended as one line to a durable append-only ledger
(`~/.co-cli/usage.jsonl`: `{turn_ended_at, origin, session_id, input_tokens, output_tokens}`). The dream
daemon — a separate process — captures its own model spend the same way and appends `origin="daemon"` lines
(no session). A new `/usage` command reports:
- `/usage` (no arg) → the **current session**'s totals (sum `origin="session"` ledger lines for the active
  `session_id`; daemon excluded).
- `/usage week|month|total` → rolling-window totals split into **Session / Daemon / Total** (daemon counted
  toward the total but never folded into the session figure), summing ledger lines whose `turn_ended_at`
  falls in the last 7 / 30 days / all time.

**Failure cost:** without this, token spend is unobservable after the fact — the user cannot see per-session
or per-period token consumption at all, and the provider's ground-truth usage (already in hand at every
model response) is thrown away every turn.

## Scope

**In scope:**
- New module `co_cli/session/usage.py`: a turn-scoped `UsageAccumulator` (in/out token ints, `add`/`reset`),
  `record_usage(deps, usage)` (best-effort bump), append-only ledger primitives (`append_turn`, `aggregate`).
- Fork-shared `usage_accumulator` field on `CoDeps`; built in `bootstrap/core.py:create_deps`, shared
  **by reference** in `fork_deps` (alongside `file_tracker`).
- Capture hooks at **both** model-call chokepoints: `ObservabilityCapability.after_model_request`
  (`capability.py`) and `llm_call` (`call.py`) call `record_usage(deps, response.usage)`.
- Per-turn flush: append one ledger line at the persist boundary (`main.py` post-turn lifecycle), then reset
  the accumulator for the next turn. Accumulator reset at turn start is owned by the main loop.
- Durable ledger path: a `USER_DIR`-derived constant in `config/core.py` and a `usage_log_path` on `CoDeps`
  (mirroring `tool_results_dir` path wiring — never hardcode `~/.co-cli`).
- `/usage` slash command (`commands/usage.py`) + registration in `commands/core.py`; renders a small table
  of input / output / total tokens. Reserved name so a skill cannot shadow it.
- Lifecycle: `/new` (session rotation) → fresh `session_id`, so current-session totals naturally start at
  zero for the new session. `/clear` (history wipe, same session) → does **not** zero the ledger (the tokens
  were really spent; same `session_id` continues).

**Out of scope:**
- Dollar/cost estimation (local Ollama; tokens are the unit).
- Changing the status-line context-% (`current_request_tokens_estimate`) — untouched.
- Per-tool or per-model breakdown; only aggregate input/output/total tokens.
- A ledger TTL / pruning / `/usage clear` (ledger is append-only and permanent, matching transcripts'
  no-TTL policy; one tiny line per turn). Noted as a possible follow-up.

**In scope — daemon usage as a distinct origin (OQ-1, user-directed at Gate 1):**
- The dream daemon's model spend (memory/skill merges via `llm_call`, reviewer runs via
  `fork_deps_for_reviewer`) is **captured and counted toward the combined total / windows**, but recorded
  with `origin="daemon"` and **never mixed into any session's figure**. Because the daemon is a separate
  process with its own deps + accumulator, this separation is structural, not a filter applied after the
  fact. See TASK-2b.

## Behavioral Constraints

- The usage accumulator and ledger are **write-only durable accounting**. They MUST NOT feed compaction
  triggers or the status-line context-% — those stay on the realtime `current_request_tokens_estimate`.
  This deliberately avoids reintroducing the provider-reported-usage-as-status-var anti-pattern that the
  preceding `drop-reported-realtime-trigger` plan removed (see memory
  `feedback_reported_usage_not_status_var`). Usage capture is observational, never a control input.
- Usage values are **provider-reported** (`response.usage` / `RunUsage`), ground truth — never `chars/4`.
- All ledger and accumulator I/O is **best-effort** (mirror `skills/usage.py`): exceptions are logged and
  swallowed; usage tracking must never block or fail a turn.
- The accumulator is fork-shared **by reference** so subagent and summarizer tokens roll into the active
  session's turn total. Resetting it is the main loop's responsibility at the turn boundary, never a fork's.

## High-Level Design

```
model response (any path)
   ├─ agent loop  → ObservabilityCapability.after_model_request ─┐
   └─ direct call → llm_call                                     ─┤→ record_usage(deps, usage)
                                                                    → deps.usage_accumulator.add(in, out)
turn boundary (main.py persist) → append_turn(ledger, session_id, acc.in, acc.out, now)
                                → deps.usage_accumulator.reset()
/usage            → aggregate(ledger, session_id=current)        → table(in/out/total)
/usage week|month → aggregate(ledger, since=now-7d|30d)
/usage total      → aggregate(ledger, all)
```

- `UsageAccumulator` is a tiny mutable dataclass shared by reference across forks (like `file_tracker`).
  Forks only `add`; the main loop owns `reset` at the turn boundary. Because forks live within a turn, no
  mid-fork reset race exists.
- Ledger record (one JSON object per line): `{"turn_ended_at": ISO8601, "session_id": str,
  "input_tokens": int, "output_tokens": int}`. Append-only line writes (no read-modify-write); flushed once
  per turn. `session_id` is `session_path.stem[-8:]` (the canonical short ID).
- `aggregate(path, *, now, since=None, session_id=None)` streams the ledger, filters by `since`
  (rolling-window cutoff) and/or `session_id`, sums input/output. Returns a small totals struct. `now` is
  passed in by the caller (real time in the command handler; fixed time in tests) — no hidden clock.
- Windows are **rolling** from `now`: `week` = `now - 7d`, `month` = `now - 30d`, `total` = no cutoff.
  `/usage` with no arg filters by the active `session_id` (no time cutoff).

## Tasks

### ✓ DONE — TASK-1 — Usage module: accumulator + append-only ledger primitives
- **files:** `co_cli/session/usage.py` (new), `tests/test_session_usage.py` (new).
- **action:** Create `co_cli/session/usage.py` mirroring the best-effort idiom of `skills/usage.py`.
  (Naming note — CD-m-4: this is a *different package* from the existing `co_cli/skills/usage.py`
  (skill-usage sidecars); no import collision, but use the full module path in import lines for clarity.)
  - `UsageAccumulator` dataclass: `input_tokens: int = 0`, `output_tokens: int = 0`; `add(input, output)`
    and `reset()`.
  - `record_usage(deps, usage)`: best-effort; reads `getattr(usage, "input_tokens", 0)` /
    `output_tokens` and calls `deps.usage_accumulator.add(...)`. Swallows+logs exceptions.
  - `append_turn(ledger_path, *, origin, session_id, input_tokens, output_tokens, turn_ended_at)`:
    best-effort append of one JSON line; no-op when both token counts are 0; `mkdir -p` parent;
    swallows+logs. `origin` is `"session"` or `"daemon"` (see OQ-1) — it determines how the line is
    bucketed in windowed reporting; `session_id` is the active short id for session lines, `null` for
    daemon lines.
  - `aggregate(ledger_path, *, now, since=None, session_id=None, origin=None) -> UsageWindow`: stream
    lines, filter by `since` (datetime cutoff), `session_id`, and/or `origin`; tolerate malformed/absent
    lines (skip). Returns a `UsageWindow` that splits totals by origin: `session` totals, `daemon` totals,
    and a combined `total` (each an `{input_tokens, output_tokens, total}`), plus a derived
    distinct-`session_id` count for windowed views (PO-m-1). Daemon usage is counted in the combined total
    but never folded into the session subtotal.
- **done_when:** `tests/test_session_usage.py` writes a real ledger file with `origin:"session"` lines
  stamped `now`, `now-3d`, and `now-40d` **and** an `origin:"daemon"` line stamped `now`; asserts
  `aggregate(since=now-7d)` sums only the in-window lines, `aggregate(since=now-30d)` adds the 3-day line,
  `aggregate()` (no cutoff) sums all, and the daemon line appears in the **combined total but not in the
  session subtotal** in every window; `aggregate(session_id=X, origin="session")` sums only that session's
  lines; the distinct-session count reflects only session-origin lines. `append_turn` with both counts 0
  writes nothing.
  Malformed lines are skipped without error. `uv run pytest tests/test_session_usage.py -x` passes.
- **success_signal:** N/A (pure module — behavior verified by the windowing/session-filter assertions).
- **prerequisites:** none.

### ✓ DONE — TASK-2 — Fork-shared accumulator on CoDeps + capture at both model-call chokepoints + per-turn flush
- **files:** `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/config/core.py`,
  `co_cli/observability/capability.py`, `co_cli/llm/call.py`, `co_cli/context/orchestrate.py`,
  `co_cli/main.py`, `co_cli/commands/new.py`, `tests/test_flow_usage_tracking.py` (new).
  (`co_cli/commands/resume.py` is **read-only verification only**, not edited — see the resume bullet.)
- **action:**
  - **CoDeps fields with defaults (CD-M-2 — required, else ~40 constructors break).** Add to `CoDeps`:
    `usage_accumulator: UsageAccumulator = field(default_factory=UsageAccumulator)` and
    `usage_log_path: Path = field(default_factory=lambda: _DEFAULT_USAGE_LOG)`, mirroring the existing
    default-factory path fields (`tool_results_dir`, `deps.py:305`). Both MUST carry defaults — bare
    annotations would (a) break the ~40 ad-hoc `CoDeps(shell=…, config=…)` constructors in tests/evals
    (e.g. `tests/test_display.py:19`, `tests/test_flow_llm_call.py:22`) and (b) violate dataclass
    non-default-after-default ordering.
  - Add a `USER_DIR`-derived ledger-path constant (`_DEFAULT_USAGE_LOG` / `USAGE_LOG`) in `config/core.py`,
    and wire `usage_log_path` through the `paths` dict in `create_deps` (mirror `tool_results_dir`). Never
    hardcode `~/.co-cli`. `create_deps` (`bootstrap/core.py:417`) builds a fresh `UsageAccumulator`.
  - Share `usage_accumulator` **by reference** in `fork_deps` (`deps.py:384`) so subagent tokens roll into
    the parent accumulator. Add it to the "intentionally shared by reference" docstring list
    (`deps.py:366-370`) — **and** while there, fix the pre-existing gap (CD-m-5): `tool_dispatch_sem` is
    already shared in the constructor (`deps.py:389`) but missing from that docstring list; add it too.
  - In `ObservabilityCapability.after_model_request` (`capability.py:199`) and `llm_call` (`call.py:71`),
    after reading `usage = response.usage`, call `record_usage(ctx.deps, usage)` / `record_usage(deps,
    usage)`. (Both already read `response.usage` per-`ModelResponse` — Core Dev verified no double-count
    and no over-count; this is a one-line best-effort addition at each site.)
  - **Flush sites — there are TWO distinct post-turn paths (CD-M-1).** A real agent turn finalizes in
    `_finalize_turn` (`main.py:135`); slash commands that replace the transcript (`/compact`, `/resume`)
    take the **early-return path** in `_apply_command_outcome` (`main.py:294-301`) and **never reach
    `_finalize_turn`** (its own docstring, `main.py:130`, says it does not handle `/compact` or
    slash-command persistence). So flush at **both**:
    - In `_finalize_turn`, after the successful `persist_session_history` (`main.py:135-141`):
      `append_turn(deps.usage_log_path, origin="session", session_id=deps.session.session_path.stem[-8:],
      input_tokens=acc.input_tokens, output_tokens=acc.output_tokens, turn_ended_at=<now>)` then
      `deps.usage_accumulator.reset()`.
    - In the `ReplaceTranscript` branch of `_apply_command_outcome` (`main.py:294-301`): in the
      `compaction_applied` true-branch (the `/compact` summarizer ran via `llm_call`, so the accumulator
      holds its tokens), do the same `append_turn` (`origin="session"`) + `reset` after the persist; in the
      `else` branch (`/resume` and other no-LLM transcript swaps) just `reset()` defensively (accumulator is
      normally 0 there). Without this, `/compact`'s summarizer tokens are silently mis-attributed to the
      *next* real turn's ledger line.
  - **Turn-start reset (CD-m-2).** Also reset the accumulator at `run_turn` entry — a **separate call at
    `orchestrate.py:715`** (immediately after `deps.runtime.reset_for_turn()`), NOT inside
    `reset_for_turn()` itself: that is a method on `CoRuntimeState` (`deps.py:222`) and cannot reach the
    parent `CoDeps.usage_accumulator`. This guards against a partial/aborted turn leaking into the next.
  - `/new` (`commands/new.py`) rotates `session_path` → the new `session_id` makes current-session `/usage`
    start fresh automatically; also `reset()` the accumulator there for immediacy. `/clear`
    (`commands/clear.py`) does **not** touch the accumulator or ledger (tokens were spent; same session).
  - `/resume` (`commands/resume.py`) — **read-only verification, no edit**: current-session totals derive
    from the ledger by `session_id`; resume sets `session_path` to the resumed file (`resume.py:91`) so
    `stem[-8:]` yields the resumed id and ledger aggregation already covers its prior turns. Confirm; touch
    nothing.
- **done_when (split into THREE deterministic checks — CD-M-3; no flaky real-agent-turn).**
  `tests/test_flow_usage_tracking.py` (CO_HOME-overridden temp, real ledger I/O):
  1. **Hook wiring:** invoke `ObservabilityCapability.after_model_request` and `llm_call`'s post-response
     path with a synthesized `ModelResponse` carrying a known `usage` (input/output) and assert each call
     bumps `deps.usage_accumulator` by exactly that response's tokens (proves both chokepoints call
     `record_usage`).
  2. **Fork-sharing:** build deps, derive a child via `fork_deps`, call `record_usage` on **both** parent
     and child, and assert a single shared accumulator reflects the sum (proves subagent tokens roll up).
  3. **Flush + reset:** with a non-zero accumulator, run the `_finalize_turn` flush and assert
     `~/.co-cli/usage.jsonl` gains exactly one line with the provider-reported totals for the active
     `session_id` and the accumulator is reset to 0; separately, exercise the `/compact`
     (`compaction_applied`) flush branch and assert it appends its own line + resets (proving `/compact`
     tokens are not folded into the next turn).
  `uv run pytest tests/test_flow_usage_tracking.py -x` passes.
- **success_signal:** tokens from chat, subagent, and summarizer/`/compact` calls all land in per-turn
  ledger lines attributed to the active session, with no cross-turn mis-attribution.
- **prerequisites:** TASK-1.

### ✓ DONE — TASK-2b — Daemon-origin usage capture + flush (OQ-1: counted toward total, never mixed with session)
- **files:** `co_cli/daemons/dream/_loop.py` (or `process.py` `main_loop`),
  `tests/test_flow_usage_tracking.py` (extend).
- **action:**
  - The dream daemon runs in a **separate process** with its own `create_deps` (`process.py:195`), so it
    already gets its own `usage_accumulator` and **both capture hooks fire for free** there: its memory/skill
    merges go through `llm_call` (`_housekeeping.py:159`,`:354`) and its reviewer runs go through
    `fork_deps_for_reviewer` agent loops (`_reviewer.py:99`,`:116`, observed by `ObservabilityCapability`).
    No new capture wiring is needed — only a flush.
  - Add a **daemon-side flush** at the dream-cycle boundary in the daemon loop (`_loop.py` / `main_loop`):
    after a housekeeping/review cycle completes, `append_turn(deps.usage_log_path, origin="daemon",
    session_id=None, input_tokens=acc.input_tokens, output_tokens=acc.output_tokens, turn_ended_at=<now>)`
    then `deps.usage_accumulator.reset()`. The daemon has no `session_path`, which is exactly why daemon
    lines carry `origin="daemon"` and a `null` session_id — they are counted in the combined total but never
    attributed to any session.
  - **Cross-process append safety:** the session process and the daemon process both append to the same
    `~/.co-cli/usage.jsonl`. Each line is well under `PIPE_BUF` (4096 B), and the writes use `O_APPEND`
    (`open(path, "a")`), so POSIX guarantees atomic, non-interleaved appends across processes — consistent
    with the append-only, no-read-modify-write design. State this rationale in the module docstring.
- **done_when:** extend `tests/test_flow_usage_tracking.py`: drive the daemon flush with a non-zero
  accumulator and assert it appends a line with `origin="daemon"` and `session_id` null; then
  `aggregate(total)` shows the daemon tokens in the **combined total** while the **session subtotal
  excludes** them, and `/usage` (current session) does **not** count the daemon line. `uv run pytest
  tests/test_flow_usage_tracking.py -x` passes.
- **success_signal:** dream-daemon token spend appears in `/usage week|month|total` (as a distinct daemon
  line / in the combined total) but never inflates any session's current-session figure.
- **prerequisites:** TASK-1, TASK-2.

### ✓ DONE — TASK-3 — `/usage` slash command + window aggregation
- **files:** `co_cli/commands/usage.py` (new), `co_cli/commands/core.py`,
  `tests/test_flow_usage_command.py` (new).
- **action:**
  - `_cmd_usage(ctx, args)`: parse `args.strip().lower()`. Empty → current session: `aggregate(ledger,
    now=datetime.now(UTC), session_id=current, origin="session")` — session-only, daemon excluded. `week` →
    `since=now-7d`; `month` → `since=now-30d`; `total` → no cutoff. Unknown token → error line listing valid
    args. Render a small table (rich, matching the console idiom of peer commands) showing input / output /
    total tokens and the window label.
  - **Windowed views show a Session / Daemon / Total breakdown (OQ-1).** For `week|month|total`, render the
    `UsageWindow`'s three rows: **Session** subtotal, **Daemon** subtotal, and **Total** (session + daemon).
    Daemon spend is counted toward the total but kept on its own row, never folded into the session figure.
    The no-arg current-session view shows the session figure only (no daemon row).
  - **Windowed-output session count (PO-m-1 — near-free readability, NOT a new tracked metric).** For the
    `week|month|total` views only, also show a distinct-session count, computed from `distinct(session_id)`
    while streaming the ledger the aggregate already reads (so the user can tell whether a window's tokens
    came from one heavy session or many light ones). The no-arg current-session view stays just the token
    table. Extend `aggregate`/`UsageTotals` with an optional `session_count` populated only for windowed
    calls; do not add any new *tracked* field — the count is derived at read time.
  - Register in `commands/core.py`:
    `BUILTIN_COMMANDS["usage"] = SlashCommand("usage", "Show token usage: /usage [week|month|total]",
    _cmd_usage)`. The name is then reserved (registry's `filter_namespace_conflicts` already drops shadowing
    skills).
- **done_when:** `tests/test_flow_usage_command.py` seeds a real ledger (CO_HOME temp) with `origin:"session"`
  lines for the current session and for a session dated 40 days ago, plus an `origin:"daemon"` line dated
  now, then drives the command through `dispatch("/usage ...", ctx)`: `/usage` reports only the current
  session's totals (daemon excluded); `/usage week` excludes the 40-day-old line and shows the daemon row in
  the Total but not in Session; `/usage total` includes the 40-day-old line and the daemon row; an unknown
  arg prints the valid-args error and changes nothing. `uv run pytest tests/test_flow_usage_command.py -x`
  passes.
- **success_signal:** the user types `/usage week` and sees correct windowed Session / Daemon / Total token
  counts; `/usage` shows the current session's spend (no daemon).
- **prerequisites:** TASK-1, TASK-2.

### Spec sync (post-delivery — NOT a task; reconciled by the auto `/sync-doc` after `/orchestrate-dev`)
Per project rule, `docs/specs/` is never listed in a task's `files:`. After delivery, reconcile:
- `docs/specs/sessions.md` — add the durable usage ledger (`~/.co-cli/usage.jsonl`) and its per-turn append
  lifecycle to the session-persistence model.
- `docs/specs/core-loop.md` — the new turn-boundary usage flush in the post-turn lifecycle, and the
  fork-shared `usage_accumulator` on `CoDeps`.
- `docs/specs/dream.md` — the daemon's `origin="daemon"` usage capture + cycle-boundary flush.
- The spec section that enumerates built-in slash commands — add `/usage`.

## Testing

- TASK-2's all-call-coverage assertion is the behavioral heart: prove subagent (`fork_deps`) and direct
  `llm_call` tokens land in the **same** accumulator as the chat turn — assert the observed total, not field
  presence (mirror `done_when`; per memory `feedback_functional_tests_only`).
- Window aggregation tests pass a fixed `now` and stamp ledger lines relative to it — no hidden clock, no
  flakiness.
- All tests override `CO_HOME` to a temp dir (never hardcode `~/.co-cli`; per CLAUDE.md known pitfall) and
  use real files / real ledger I/O (no mocks of the store — UAT-grade, per memory `feedback_eval_real_world_data`).
- Per project policy: run with `-x`, pipe to a timestamped `.pytest-logs/` file, tail the log for LLM-call
  timing. The usage-coverage flow test should avoid a real summarizer LLM call where a direct `record_usage`
  on a synthesized `ModelResponse.usage` proves the same coverage deterministically — keep LLM calls in the
  flow test minimal and watch their duration.

## Open Questions

1. **Dream-daemon / background usage attribution — RESOLVED (user, Gate 1): captured as a distinct
   origin, counted toward the total, never mixed with session.** Verified at source: the dream daemon runs
   in a **separate process** with its own `create_deps` (`daemons/dream/process.py:195`), so it has its own
   `usage_accumulator` and both capture hooks fire there automatically (merges via `llm_call`
   `_housekeeping.py:159`/`:354`; reviewer runs via `fork_deps_for_reviewer` `_reviewer.py:99`/`:116`). The
   daemon flushes `origin="daemon"`, `session_id=null` ledger lines at its cycle boundary (TASK-2b). `/usage`
   (current session) excludes daemon; `/usage week|month|total` shows a Session / Daemon / Total split with
   daemon counted in the total but never folded into the session figure. The earlier "out of scope" proposal
   is withdrawn.
2. **Ledger growth / no TTL.** The ledger is append-only and permanent (one small line per turn), matching
   the transcripts' no-TTL policy. **Proposed resolution:** accept unbounded growth for now; a `/usage clear`
   or size-based rotation is a possible follow-up, out of scope here.
3. **Window basis = activity time (per-turn `turn_ended_at`), not session creation.** A turn is attributed
   to the window in which it *ended*, so a long-lived session contributes to multiple windows correctly
   (this is why a per-turn ledger beats a per-session cumulative sidecar). Confirmed as the chosen design;
   listed here so reviewers weigh the per-turn-line cost against the windowing precision it buys.

## Final — Team Lead

Plan approved. Converged over two review cycles. C1: PO approved with no blockers; Core Dev raised three
blockers — CD-M-1 (the `/compact`//resume early-return flush gap → token mis-attribution), CD-M-2 (missing
`field(default_factory=…)` defaults on the two new `CoDeps` fields → broken constructors), and CD-M-3 (a
self-contradicting TASK-2 done_when) — all adopted, plus four minors (CD-m-2 reset-site, CD-m-3 resume.py
read-only, CD-m-4 module-name note, CD-m-5 fork_deps docstring; CD-m-1 initially rejected). PO's PO-m-1
(windowed session count) adopted. C2: Core Dev confirmed all three blockers resolved (Blocking: none) and
correctly caught that the CD-m-1 rejection was based on a stale path — reversed to adopt and the predecessor
citation corrected to `completed/`. Both reviewers at `Blocking: none`.

**Gate-1 amendment (user-directed).** OQ-1 reversed: daemon token usage is now **in scope** — captured with
`origin="daemon"` and counted toward the windowed/total figures, but never mixed into any session's number.
Added TASK-2b (daemon-side cycle-boundary flush; the daemon's separate process already inherits both capture
hooks), an `origin` field on the ledger record, an origin-split `UsageWindow` (Session / Daemon / Total) in
TASK-1/TASK-3, and cross-process append-safety rationale (`O_APPEND` < `PIPE_BUF`). Scope, Outcome, and
spec-sync (`dream.md`) updated accordingly.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev token-usage-tracking-refactor`

## Delivery Summary — 2026-06-04

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `tests/test_session_usage.py` windowing/session-filter/daemon-split/zero-noop/malformed-skip pass | ✓ pass (8) |
| TASK-2 | `tests/test_flow_usage_tracking.py` hook-wiring + fork-sharing + flush/reset (incl. /compact) pass | ✓ pass (5) |
| TASK-2b | daemon flush writes `origin="daemon"` line; counted in total, excluded from session subtotal & current-session | ✓ pass (1, extends TASK-2 file → 6) |
| TASK-3 | `tests/test_flow_usage_command.py` no-arg/week/total/unknown-arg via dispatch pass | ✓ pass (4) |

**Tests:** scoped — 18 passed, 0 failed (`tests/test_session_usage.py` + `tests/test_flow_usage_tracking.py` + `tests/test_flow_usage_command.py`).
**Doc Sync:** fixed — `sessions.md` (§3 ledger subsection + Domain-API/Files rows), `core-loop.md` (§2.6 turn-boundary flush + /compact + turn-start reset), `dream.md` (daemon cycle-boundary flush), `tui.md` (`/usage` command row). Cross-doc index clean.

**Implementation notes:**
- New module `co_cli/session/usage.py`: `UsageAccumulator`, `record_usage`, `append_turn`, `aggregate` → `UsageWindow`/`UsageTotals`. Minor deviation from plan: `aggregate` omits the unused `now` param (cutoff carried by `since`, computed by callers) — behaviorally identical, matches TASK-1 `done_when` call shapes, avoids a dead parameter.
- `CoDeps` gained `usage_accumulator` (fork-shared by reference) + `usage_log_path`, both with `field(default_factory=...)` defaults (CD-M-2). `fork_deps` docstring updated to list `usage_accumulator` and the previously-undocumented `tool_dispatch_sem` (CD-m-5 fix).
- Capture at both chokepoints (`ObservabilityCapability.after_model_request`, `llm_call`); flush at both post-turn paths (`_finalize_turn` + `/compact` branch of `_apply_command_outcome`, CD-M-1); turn-start reset in `run_turn` (CD-m-2); accumulator reset in `/new`. `/resume` verified read-only (CD-m-3).
- `USAGE_LOG` constant in `config/core.py`; `usage_log_path` wired through `resolve_workspace_paths` (never hardcoded).

**Out-of-scope observation (not fixed):** `scripts/quality-gate.sh lint` (full repo) reports an F821 in `tests/observability/test_setup_observability.py` — an untracked file from a separate in-flight plan, not touched by this work. Flagged for that plan's owner.

**Overall: DELIVERED**
All four tasks pass their `done_when`; lint clean across the 14 plan files; scoped tests green (18); doc sync reconciled.

## Implementation Review — 2026-06-04

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|--------------|
| TASK-1 | usage windowing/filter/daemon-split/zero-noop/malformed-skip | ✓ pass | `session/usage.py:43-59` accumulator; `:103-132` append_turn (no-op on 0/0 at `:118`); `:135-192` aggregate (daemon→total not session `:180-191`; distinct count `:192`) — 8/8 |
| TASK-2 | both chokepoints bump; fork-shares; flush+reset (incl /compact) | ✓ pass | `capability.py:200-201` + `call.py:72-73` record_usage; `deps.py:419` fork by-ref (test asserts identity); `main.py:159` finalize flush, `:319-321` /compact flush, `:325` else reset; `orchestrate.py:716` turn-start reset (separate from reset_for_turn) — 6/6 |
| TASK-2b | daemon line origin="daemon"/null session; in total not session | ✓ pass | `_loop.py:34-55` flush helper; called at `:115` (housekeep) + `:132` (after move_to_done); flush-only (no new capture wiring) — passes |
| TASK-3 | no-arg session-only; week/total split; unknown errors | ✓ pass | `commands/usage.py:36-62` handler; `core.py:36,92-94` registration; rendered output verified via real dispatch — 4/4 |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| **Concurrent-edit clobber:** a parallel session's `fix-dream-logging` edit rewrote `main.py` and `config/core.py` mid-review, reverting this delivery's usage-flush wiring (`_flush_turn_usage`, the import, `_finalize_turn` flush, `/compact` flush+reset) and deleting `USAGE_LOG`. Left the app un-importable (`ImportError: cannot import name 'USAGE_LOG'`). Caught in Phase 5 (first full-suite pass predated the clobber). | `co_cli/main.py`, `co_cli/config/core.py` | blocking | Re-applied all four `main.py` edits + `USAGE_LOG` on top of the coworker's current observability wiring; preserved their changes. Re-verified app imports + 642-pass full suite + scoped 18-pass. |

### Adversarial verification (Phase 3)
All four high-risk concerns confirmed safe by construction: **no double-count** (`llm_call` direct path vs `after_model_request` agent path are mutually exclusive per ModelResponse); **no double-flush / no token loss** (slash commands hit the early return at the dispatch boundary and never reach `_finalize_turn`; `/compact` flushes its own summarizer tokens); **no mid-turn reset wipe** (`run_turn` has a single caller `main.py`; `fork_deps` exists only in the separate-process dream daemon); **naive-datetime skip** accepted-minor (both `append_turn` callers pass `datetime.now(UTC)`). Harmless idempotent double-reset on `/new` (`new.py` + ReplaceTranscript else-branch) left as-is (plan-specified).

### Tests
- Command: `uv run pytest -x -q` (full suite, re-run after clobber re-apply)
- Result: **642 passed, 0 failed** in 152.86s; pre/post-run clobber markers intact
- Scoped: `test_session_usage.py` + `test_flow_usage_tracking.py` + `test_flow_usage_command.py` → 18 passed
- Logs: `.pytest-logs/20260604-210137-review-impl-rerun.log`

### Behavioral Verification
- `/usage` (real dispatch, seeded ledger): current-session only `1,200/340/1,540` — daemon + 40-day-old session excluded ✓
- `/usage week`: Session `1,200`, Daemon `5,000` in Total `6,200` (not in Session), 1 session ✓
- `/usage total`: Session `9,200`, Daemon `5,000`, Total `14,200`, 2 sessions ✓
- `/usage bogus`: valid-args error, no mutation ✓
- `success_signal` verified: user sees correct windowed Session/Daemon/Total counts. (`co status`/`co logs` not applicable — no such subcommands in this project.)

### Overall: PASS
All four `done_when` met with cited evidence; full suite green (642); lint clean; behavioral verification confirms the user-visible `/usage` output. **One operational caveat for Gate 2 / `/ship`:** the working tree carries a *concurrently-edited* parallel plan (`fix-dream-logging`: `observability/setup.py`, `tracing.py`, `process.py`, `file_logging.py`, `main.py` observability wiring, `tests/observability/`). `main.py`/`config/core.py` were actively rewritten by that other session during this review. **Before `/ship`, re-verify these two files still contain the usage-flush wiring (`grep _flush_turn_usage co_cli/main.py`, `grep USAGE_LOG co_cli/config/core.py`) and stage only this plan's files** — do not let the clobber recur, and coordinate with the parallel plan's owner on the shared `main.py`.
