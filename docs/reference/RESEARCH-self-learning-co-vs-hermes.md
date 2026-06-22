# RESEARCH — Self-Learning: co vs hermes (Architecture, Parity, Gaps)

**Status:** Reference / design research — not normative
**Scope:** Single source of truth for co-cli's self-improvement subsystem — the as-built dream daemon, how it compares to `hermes-agent`'s "self-improving" pitch, the verified gaps, the live open proposals, and the cross-peer triggering survey. Runtime spec of record: `docs/specs/dream.md`.

---

## 1. The subsystem at a glance

co-cli's learning loop has two halves, both owned by a single out-of-process background daemon (`co_cli/daemons/dream/`):

1. **Review** (event-driven, per-session): forked sub-agents mine a session transcript for durable memory and for skill updates.
2. **Housekeeping** (scheduled, low-frequency): merge near-duplicate memory items and skills, decay stale ones, prune queue artifacts.

Layer map (replaces the old three-runner table):

```
                  REVIEW (per session, queue-kicked)        HOUSEKEEPING (scheduled, ~daily)
                  -----------------------------------        --------------------------------
MEMORY   →  MEMORY_REVIEW_SPEC (forked agent)        →   merge_memory  (no age decay — by design)
SKILLS   →  SKILL_REVIEW_SPEC  (forked agent)        →   merge_skills + decay_skills
DOCTRINE →                — none (author-curated, never learned) —
```

Both review specs are *separate* forked agents (not one combined reviewer), and both halves run inside one daemon process.

---

## 2. Current architecture, source-grounded

### 2.1 One daemon, queue-driven

- Package: `co_cli/daemons/dream/` (`__init__.py` is docstring-only per repo rules).
- Process lifecycle: `process.py` (public CLI surface: `co dream start/stop/status`) + `_process.py` (internal); main loop in `_loop.py`.
- Trigger model: **filesystem queue + polling**, not the old per-turn in-process `asyncio.Task`. The REPL writes KICK JSON files; the detached daemon polls `queue/` every `tick_interval_seconds` (default 5s) and consumes FIFO.
- Auto-spawn: `maybe_autospawn_dream(deps, frontend)` at REPL startup (`co_cli/main.py`), guarded by `bootstrap/core.py` advisory flock; spawns a detached `co dream start --foreground` subprocess when `dream.enabled` and `CO_DREAM_NO_AUTOSPAWN` is unset.

This matches the durable-queue invariants in memory: producer never gated on consumer liveness (`feedback_queue_decoupling_invariant`), queue is the sole cross-process bridge with no side-channel wake-ups (`feedback_queue_sole_bridge`).

### 2.2 The KICK queue

Producer: `co_cli/dream_queue.py` (no `kick.py` module — the KICK enqueue logic lives at the package top level) — shared by REPL post-turn, session-end, and compaction paths. Atomic JSON writes. Payload (a plain dict, not a dataclass): `{domain: "memory"|"skill", session_id, persisted_message_count, created_at, transcript_override?}` (`transcript_override` omitted when `None`).

Directories (constants in `co_cli/config/core.py`):
- `queue/` — pending KICKs
- `queue/done/` — processed (pruned past `done_retention_days`)
- `queue/failed/` — retries exhausted (diagnostic, never auto-pruned)

REPL-side counters (`co_cli/main.py` `_post_turn_hook`):
- `turns_since_memory_review` → kicks a memory review every `review_memory_nudge_interval` turns
- `model_requests_since_skill_review` → kicks a skill review every `review_skill_nudge_interval` model requests
- Inline use of `memory_*` / `skill_*` tools resets the respective counter
- Session end always fires both kicks (`_fire_session_end_kicks`)

### 2.3 The reviewers (`_reviewer.py`)

`process_review(domain, session_id, …)` loads the transcript (`load_transcript`) and dispatches to one of two forked agents via `fork_deps_for_reviewer(deps)` + `run_standalone`:

| Spec | Tool surface | Prompt |
|---|---|---|
| `MEMORY_REVIEW_SPEC` | `memory_search`, `memory_create`, `memory_append`, `memory_replace`, `user_profile_view`, `user_profile_write` | `prompts/memory_review.md` |
| `SKILL_REVIEW_SPEC` | `skill_view`, `skill_create`, `skill_edit`, `skill_patch`, `memory_search` (+ skill manifest) | `prompts/skill_review.md` |

Both apply the personality **curation lens** when the active soul defines one. Each reviewer sees **one session's transcript** — there is no cross-session view at review time (see Gap D, §5.4).

> **New since the rewrite:** the memory reviewer now carries the two `user_profile_*` tools, so USER.md write-back happens through a dedicated profile surface (`co_cli/tools/user_profile/{view,write}.py`), not through generic memory writes. `process_review(deps, domain, session_id, persisted_message_count, transcript_override=None)` loads the live transcript (capped by `persisted_message_count`) or, on compaction, an uncapped snapshot from `transcript_override`. Both reviewers emit a `SessionReviewOutput`. The session reviewer can also write `kind='canon'` items (internal-only kind, excluded from the model-callable memory tools — see §5.5).

### 2.4 Housekeeping (`_housekeeping.py`)

`run_housekeeping(deps, cfg, state)` runs the LLM/code phases below, scheduled (not per-session). "Reviewer is the sole transcript reader" — housekeeping never reads transcripts; it operates on already-saved memory items and skills.

1. **merge_memory** — union-find cluster same-kind, non-immune, non-article items by token-Jaccard ≥ `memory.consolidation_similarity_threshold`; LLM-consolidate (`prompts/memory_merge.md`); archive originals. Caps: `_MAX_CLUSTER_SIZE=5`, `_MAX_MERGES_PER_CYCLE=10`, `_MERGED_BODY_MIN_CHARS=20`.
2. **merge_skills** — cluster similar **non-pinned user** skills (bundled skills never considered) by `skills.consolidation_similarity_threshold`, LLM-consolidate into a class-level umbrella (`prompts/skill_merge.md`), archive originals (to `.archive/`), then `refresh_skills(deps)`. **This absorbs the former skill curator's consolidation.**
3. **decay_skills** — archive aged user skills with no recent recall (cap `_MAX_DECAY_PER_CYCLE=20`).
4. **prune_done_and_snapshots** — delete `queue/done/` items and orphaned compaction snapshots past `done_retention_days`.
5. **prune_sessions** — delete session transcripts past `dream.session_retention_days` (0 = retain forever, the default).

> **No `decay_memory` phase.** Memory is never decayed by age/recall — storage is treated as unconstrained, so the only memory-side hygiene is merge. Only *skills* decay; `memory.decay_after_days` governs skills, not memory.

State: `HousekeepingState` (in `state.py`, not `_state.py`) at `$CO_HOME/daemons/dream/_dream_state.json` (`last_housekeeping_at`, cumulative `stats`). Schedule: at least `run_interval_hours` since last run, fired at the next `run_start_at` time-of-day.

### 2.5 Config (current)

`co_cli/config/dream.py` — all daemon timing/lifecycle:

| Field | Default |
|---|---|
| `enabled` | `False` |
| `tick_interval_seconds` | `5` |
| `review_timeout_seconds` | `120` |
| `retry_backoff_seconds` / `max_retry_attempts` | `30` / `3` |
| `run_interval_hours` | `24` |
| `run_start_at` | `"03:00"` |
| `max_pass_seconds` | `600` |
| `done_retention_days` | `7` |
| `session_retention_days` | `0` (retain forever) |

`co_cli/config/skills.py` — review nudges + skill lifecycle thresholds:

| Field | Default |
|---|---|
| `review_enabled` | `False` |
| `review_memory_nudge_interval` | `10` turns |
| `review_skill_nudge_interval` | `10` model requests |
| `usage_tracking_enabled` | `True` |
| `recall_protection_days` | `30` |
| `decay_after_days` | `90` |
| `consolidation_similarity_threshold` | `0.75` |
| `REVIEW_MAX_ITERATIONS` (module constant) | `8` |

`consolidation_similarity_threshold` is defined on **both** `MemorySettings` (memory-merge) and `SkillsSettings` (skill-merge), each defaulting `0.75`. USER.md sizing lives on `MemorySettings`: `user_profile_enabled` (default `True`), `user_profile_char_budget` (default `1500`). The whole loop ships **off by default** (`dream.enabled=False`, `review_enabled=False`).

---

## 3. The architecture decision (settled)

The as-built design is:

- **Unified on the process axis**: one out-of-process daemon, one queue, one housekeeping pass spanning memory and skills, shared `_dream_state.json`.
- **Split on the review axis**: two domain reviewers (memory, skill) rather than one combined reviewer.
- **No curator state machine**: skill upkeep is LLM merge + age-based decay on the daily housekeeping cadence (no `active→stale→archived` lifecycle, no `.curator_state.json`).

Settled — do not re-litigate without new evidence. The one open analysis item is the cross-session skill-mining gap (Gap D, §5.4): the design reorganized around it but did not close it.

---

## 4. co vs hermes — parity map

hermes-agent markets itself as "the self-improving AI agent" on five claims:

| hermes claim | hermes mechanism | co mechanism | Verdict |
|---|---|---|---|
| #1 "Creates skills from experience" | skill-nudge reviewer (`run_agent.py`) → `skill_manage` | `SKILL_REVIEW_SPEC` → `skill_create/edit/patch` | **PARITY** (mechanism); hermes richer prompt |
| #2 "Improves them during use" | same reviewer, patch-loaded-first | same posture in `skill_review.md` | **PARITY** (mechanism) |
| #3 "Nudges itself to persist knowledge" | memory-nudge reviewer + skill-nudge reviewer, **separate** | `MEMORY_REVIEW_SPEC` + `SKILL_REVIEW_SPEC`, **separate** | **PARITY in shape** |
| #4 "Searches its own past conversations" | FTS5 + windowing, no LLM summarization | lexical/regex over raw JSONL transcripts, no summarization | **PARITY** (§5.1) |
| #5 "Builds a deepening model of who you are" | always-injected `USER.md` + Honcho dialectic backend | always-injected `~/.co-cli/USER.md` (dream-reviewer write-back) | **PARITY on USER.md (§5.2)**; Honcho backend gap remains (§5.3) |

Notes:

- **Claim #4.** hermes's `tools/session_search_tool.py` is pure FTS5 + message-window retrieval ("zero LLM cost") across four modes: discovery (FTS5 + bookends + ±window), scroll (window around a message id), browse (recent sessions), read (full session head+tail). co's `session_search` is lexical/regex over raw JSONL transcripts (no index). Both return raw windows, neither summarizes — parity on the no-summarization axis. The retrieval *substrate* differs: hermes FTS5-indexes transcripts; co does not index sessions at all.
- **Claim #3.** co **splits** memory+skill review into two domain reviewers; hermes keeps them separable (`review_memory` / `review_skills` flags in `agent/background_review.py`, with a `_COMBINED_REVIEW_PROMPT` when both fire).
- **Skill curator (co↔hermes divergence).** co has no curator state machine — skill upkeep is merge+decay in housekeeping. hermes ships the curator (`agent/curator.py` + `hermes_cli/curator.py`): a 7d-interval / 30d-stale / 90d-archive state machine persisted to `.curator_state`, deterministic transitions via `apply_automatic_transitions()`, with its **LLM consolidation (umbrella-building) pass OFF by default** (`curator.consolidate=False`). So co runs LLM skill-merge by default; hermes runs deterministic decay by default and consolidates only when enabled. (`recall_protection_days=30`, `decay_after_days=90` are co housekeeping thresholds, not curator transitions.)

> co-unique surfaces (not "self-improving" per se, included for an honest map): **personality doctrine** (soul/mindsets/critique injected statically, author-curated, never learned — `docs/specs/personality.md`); **the housekeeping merge pass** as deeper offline memory hygiene than hermes's per-turn nudge; **atomic-write discipline** on every memory/skill/state write (hermes at parity via `atomic_replace`).

---

## 5. Gaps

### 5.1 Gap A — LLM-summarized cross-session recall — **CLOSED**

- **hermes:** `tools/session_search_tool.py` does not summarize — pure FTS5 + message-window retrieval across four modes (discovery / scroll / browse / read), "zero LLM cost".
- **co:** `co_cli/tools/session/recall.py` is **lexical/regex** (literal substring or regex over the raw JSONL transcripts via `SessionStore.search()`, deduped to `_SESSIONS_CHANNEL_CAP=3` sessions per query) — *not* BM25 (BM25 is the `memory_search` substrate, not session search). Returns `(session_id, when, source, chunk_text, start_line, end_line, score)` for pairing with `session_view`. No summarization.
- **Net:** both return raw windows; no parity gap. Any future summarization on co (§6.1) would be a co-unique enhancement, not catch-up.

### 5.2 Gap B — always-injected user profile — **CLOSED**

- **hermes:** dedicated `USER.md`, injected into the system prompt every turn when `user_profile_enabled`; the memory reviewer writes back to it (volatile tier, rebuilt on compression).
- **co:** user preferences live in an always-injected `~/.co-cli/USER.md` profile (sized by `memory.user_profile_char_budget`, default 1500), written back by the dream **memory** reviewer through dedicated `user_profile_view` / `user_profile_write` tools (`co_cli/tools/user_profile/`) — a wholesale rewrite, not a targeted edit, not reliant on the model choosing to `memory_search`. There is no `kind='user'` memory kind; model-facing kinds are `rule | article | note`.

### 5.3 Gap C — dialectic / backend user-modeling — **OPEN**

- **hermes:** Honcho plugin (5 tools, per-turn prefetch, dialectic Q&A); the user model is synthesized backend-side from observed conversation.
- **co:** no honcho/dialectic/peer-card/`MemoryProvider` of any kind. co's model of the user is exactly the union of memory items it chose to save — no second-order behavioral model.
- **Cost:** no cross-session behavioral/contradiction modeling. Mission tension: "local, inspectable, reversible" argues against an external backend as default (§6). The local closure is the planned Gap-C profile-synthesis pass.

### 5.4 Gap D — cross-session skill recurrence signal — **OPEN**

- **What's missing:** no component proactively recognizes "this 4-step procedure has recurred across N sessions, make it an umbrella skill." The skill reviewer sees **one** transcript per KICK; skill **merge** in housekeeping clusters only *already-saved* skills (it never reads transcripts).
- **Net behavior:** per-session reviewer creates near-duplicate skills, housekeeping merge later collapses them. The recurrence signal exists only as after-the-fact dedup, never as up-front recognition.

### 5.5 Note — the internal `canon` kind (co-side, not a gap)

`MemoryKindEnum` carries a fourth value, `canon`, beyond the three model-facing kinds (`rule | article | note`). It is **internal-only**: excluded from the model-callable memory tools (`MemoryKind = Literal[rule, article, note]`), it is the kind the dream reviewer uses for personality/canon material. Recorded here so the "memory kinds are rule|article|note" claim in §4/§5.2 is understood as the *model-facing* surface, not the full enum.

---

## 6. Open proposals

### 6.1 Minimum-viable gap closures

- **Gap A (closed — optional enhancement only)** — an optional summarization stage on `session_search`: aux-LLM digest per session window, gated behind a config flag (`sessions.summarize_results: bool = False`) with a token cap and single-flight semaphore. Pursue only on its own context-spend ROI, not as peer catch-up. *Small.*
- **Gap B (closed)** — shipped as the always-injected `~/.co-cli/USER.md` profile (§5.2).
- **Gap C** — three increasing scopes: (1) embedded periodic "re-derive the profile from recent transcripts" pass (no new dependency); (2) honcho-style plugin parity (adds a network dep — disfavored by mission); (3) a `MemoryProvider` ABC with the embedded option as default (the load-bearing answer only if co ever wants Mem0/Letta/etc.). Prefer (1) unless plugin-able user-modeling becomes a stated goal.
- **Gap D** — give the skill reviewer (or a housekeeping sub-pass) a small recent-session window so it can propose umbrella skills from recurrence *before* duplicates accumulate, rather than relying solely on after-the-fact merge. Reconcile with §6.2 so there is one skill-creation path, not two.

### 6.2 Skill promotion vs the daemon reviewer (reconciliation)

The original proposal to "promote a successful work-prompt into a reusable skill" now **overlaps** the daemon's `SKILL_REVIEW_SPEC`, which already auto-creates and patches skills. Any synthesize-then-promote feature must route through (or explicitly defer to) the reviewer to avoid two competing skill-creation mechanisms with divergent naming/dedup discipline. Retained boundary: draft generation may be automatic; durable save stays explicit and inspectable; no autonomous rewriting of soul/rule/spec files.

### 6.3 Project-local skills tier

Still unbuilt. `co_cli/skills/loader.py` loads only **two** tiers — co-bundled (`co_cli/skills/`) and user-global (`~/.co-cli/skills/`, security-scanned, overriding bundled on name collision). A third **project-local** tier (`.co-cli/skills/`, loaded last) remains the right default for repo-specific generated skills, and is the natural promotion target for any §6.2 work. Adding it touches the loader, the security-scan scope, and the override order (`docs/specs/skills.md`).

---

## 7. Triggering & scheduling architecture — self-learning peers

How the background learning work is *triggered* is a distinct axis from what it does. Scope note: §7.1–7.4 keep only peers that **actually self-learn** (durable behavior change from experience — memory/skill/profile): **hermes** (strong: memory+skill review, curator, USER.md), **letta** (sleep-time agents), **elizaos** (per-message evaluators), and the inline-sync memory libraries **mem0 / memU / ReMe** (passive fact accumulation). Cut as no self-learning: **codex** (no background system; inline compaction only) and **opencode** (background loops are pure infra-hygiene). **openclaw** is also not a self-learning peer (its cron is a general user-scheduler), but it is the most mature *scheduler design* surveyed, so it is retained separately in §7.5 as a design reference for the transfers in §7.4. Headline: **co is the only self-learning peer that runs its learning work in a detached out-of-process daemon over a file queue** — every other keeps it in the host process; the durable ones back it with a database.

### 7.1 The recurring shape: episodic capture vs periodic upkeep

The runtime self-learners separate **episodic capture** (event-driven, post-turn/post-step) from **periodic upkeep** (clock-driven). They differ in process model and durability substrate:

| | Event / learning trigger | Periodic-upkeep trigger | Process model | Durability substrate |
|---|---|---|---|---|
| **co** | file-queue KICKs — per-turn nudge counters + session-end + pre-compaction snapshot (`co_cli/dream_queue.py`, `main.py:_post_turn_hook`) | in-loop `scheduled_tick_due` (`_loop.py:58`), 24h + `run_start_at`, **idle-gated** (empty-queue branch only, `_loop.py:115`) | **out-of-process detached daemon** (only peer to do this) | **filesystem** (queue dir + `_dream_state.json`) |
| **hermes** | per-turn **daemon thread**, fires after ≥N tool iterations (default 10) on a non-empty turn (`agent/background_review.py`, `conversation_loop.py:620`) | 60s **ticker daemon thread** at gateway boot (`gateway/run.py` `_start_cron_ticker`) | in-process threads | `~/.hermes/cron/jobs.json` + `.tick.lock` |
| **letta** | **sleep-time agents** — post-step, gated by `sleeptime_agent_frequency` (every Nth turn), fire-and-forget `safe_create_task` (`groups/sleeptime_multi_agent_v4.py:80,140`) | APScheduler `IntervalTrigger` + jitter, Postgres advisory-lock leader election (`jobs/scheduler.py`; batch-poll only) | in-process async tasks (+ leader node) | database (Postgres) |
| **elizaos** | per-message **evaluators** (`shouldRun`/`evaluate`, `types/evaluator.ts:44`) | DB-backed repeat-task queue — `TaskDrain` rows tagged `queue/repeat`, interval in metadata (`utils/batch-queue/task-drain.ts:35`) | in-process; DB-polled tasks | database |
| **mem0 / memU / ReMe** | **none** — consolidation runs inline-synchronously inside `add()`/`memorize()`; decay is query-time ranking | none | the foreground request (library you call) | n/a (caller owns triggering) |

Three structural facts fall out:

1. **Frequency-counter gating is the convergent capture trigger.** co's `review_*_nudge_interval`, letta's `sleeptime_agent_frequency` (every Nth turn), and hermes's ≥N-tool-iterations are the same mechanism — fire the learning pass every N units of activity. co is squarely on this pattern.

2. **co is the lone out-of-process design — and the lone filesystem-durable one.** hermes runs daemon threads, letta/elizaos run in-process async tasks; all die with the host process (mitigated by catch-up / DB persistence). co's detached daemon + durable file queue is the only crash-decoupled producer/consumer — its standout robustness property, consistent with `feedback_queue_decoupling_invariant` / `feedback_queue_sole_bridge`.

3. **"Only one runner" is universal, by three different mechanisms.** co's bootstrap **flock** (autospawn), hermes's **`.tick.lock`** (fcntl/msvcrt), and letta's **Postgres advisory lock** (`pg_try_advisory_lock`) all enforce single-runner; co's is the only one that needs no shared DB or host-process coordination.

### 7.2 The one axis where peers diverge *from* co: idle-gating the clock

No kept peer subordinates its periodic path to load. hermes runs its 60s ticker on an **independent thread**; letta's APScheduler fires on its **own interval**; elizaos drains repeat-tasks on a **DB-driven cadence**. All fire periodic work regardless of conversation load. co alone runs housekeeping **only on the empty-queue branch** (`_loop.py:115`) — so under sustained KICK load the daily pass (and the planned Gap-C profile synthesis) can be starved indefinitely. Multiple independent self-learners converging on "periodic work gets its own un-preemptable cadence" is the strongest signal in this survey that co's idle-subordination is the outlier. (For *episodic* reviewers, idle-gating is fine; the divergence only bites the clock-driven phases.)

### 7.3 Durability / observability ladder

Ranked richest → thinnest on the learning-upkeep path:

- **letta / elizaos — richest (DB-backed).** A database carries the durable job/task state, so they get **queryable run history for free** — letta persists batch jobs + leader state in Postgres; elizaos persists repeat-tasks as DB rows (with `maxFailures:-1` infinite retry). Survives restart, inspectable by query.
- **hermes — middle.** `jobs.json` atomic writes; **cross-process advisory lock** + in-process RLock; **at-most-once** (advance `next_run_at` under lock *before* submit); in-flight dedup + stale-run catch-up; per-job `last_run_at`/`last_status` only (**no append-only audit**); **no automatic retry** — manual CLI re-run.
- **co — durable transport, thin observability.** File queue FIFO + `done/`/`failed/` dirs + **flat 3× retry × 30s backoff** (notably, the *only* kept peer with automatic retry of the learning path — hermes has none) + single `_dream_state.json` (cumulative counters). But: no queryable per-run audit, no error classification (every failure retried identically), and `failed/` accumulates silently (never surfaced).

So co *out-retries* hermes (automatic vs manual) but *under-observes* the DB-backed cohort (no run audit, no failure surfacing).

### 7.4 Transferable to co's dreamer (ranked, filtered for co's flat-file minimalism)

1. **Structured run-log / outcome audit (highest ROI).** Closes the deferred Gap-C "did a run earn its keep?" question and the silent-`failed/` blind spot. The DB-backed peers get queryable run history implicitly; for co this should be a **JSONL run-log** (mirroring `usage.jsonl`), not a DB — record per pass what each phase did (merged N, dropped a contradiction, synthesized profile, no-op). Buys the observability without abandoning the file-queue's decoupling.
2. **Consecutive-failure surfacing.** A log-warn threshold on `failed/` accumulation makes a persistently-broken reviewer/synthesis visible (today it's silent).
3. **Transient-vs-permanent error classification on retry.** co burns all 3 retries on permanent errors; classifying to skip non-transient failures is cheap and aligned. (General best practice — none of the kept self-learning peers implements this richly, so lowest peer-evidence of the three.)
4. **Independent clock timing (biggest design change).** Give the clock-driven phases a cadence not subordinate to queue drain (§7.2). Weigh against co's single-loop simplicity.

**Do not borrow:** a jobs database, multi-node leader election, cron expressions, user-facing job CRUD — that is scope co deliberately lacks (single-user, local, flat-file). Take the observability layer (1–2); treat (3–4) as judgment calls.

**Net:** co's detached-daemon + file-queue is genuinely unique among self-learners (crash-survival no peer matches), its frequency-counter gating is convergent with letta/hermes, its single-runner flock matches letta/hermes by simpler means, and its one real deficit vs the DB-backed cohort is queryable run observability.

### 7.5 Scheduler-design reference: openclaw (not a self-learning peer)

openclaw does **not** self-learn — its `src/cron/` is a general-purpose user-facing scheduler (run-this-prompt-on-a-cron) plus infra hygiene. It is kept here only because it is the most mature *scheduler engineering* in the survey, and it is the concrete source for several §7.4 transfers. Design qualities worth lifting (file:line in §9):

- **Append-only run-log audit** (`store/schema.ts`, `run-log/sqlite-store.ts`): SQLite job store + a separate `cron_run_logs` table recording every run's outcome — the direct model for §7.4 item 1 (co would do this as JSONL, not SQLite).
- **Transient-vs-permanent error classification** (`retry-hint.ts`): classifies failures (rate_limit / overloaded / network / timeout / server_error) and only retries transient ones, with a `[30s, 60s, 5m, 15m, 60m]` backoff — the model for §7.4 item 3.
- **Consecutive-failure alerts with cooldown** (`service/failure-alerts.ts`): fires after N consecutive errors, then rate-limits — the richer form of §7.4 item 2 (co wants only the log-warn floor, not the webhook/announce machinery).
- **Independent wall-clock timer** (`service/timer.ts`: `setTimeout` wake ≤60s, `MIN_REFIRE_GAP_MS=2s`, `maxConcurrentRuns` pool) — periodic work fires on its own cadence, never idle-gated (§7.2).
- **Cold-start-safe watchdog** (`service/agent-watchdog.ts`, `timer.ts:225`): defers the timeout clock until agent setup completes — an independent convergence with co's own `feedback_call_timeout_no_cold_start` principle.
- **Startup catch-up with stagger** + **ephemeral isolated session per run** reaped after retention (`session-reaper.ts`) round out the design.

**Still do not borrow** its general-scheduler scope: cron expressions, tz/DST (Croner), delivery channels/webhooks, user-facing job CRUD, a jobs DB. The value to co is the durability/observability engineering (run-log, error classes, failure surfacing), not the scheduler generality.

---

## 8. Open questions for future research

- **Cross-layer promotion:** when memory review identifies a recurring *procedure* (not a fact), should it hand off to skill creation, or always defer to a skill-side cross-session pass (Gap D)? Needs sessions data.
- **Doctrine evolution:** explicitly out of scope — doctrine is hand-authored canon (`docs/specs/personality.md`). Re-evaluate only if that constraint is lifted.
- **External memory provider:** offloading consolidation/decay to a provider (hermes/Honcho style) would free housekeeping to focus on mining, but adds a dependency and cuts against co's local-self-sufficiency mission. Probably not desirable as default.
- **Daemon "split-brain" framing:** the `feedback_dream_daemon_split_brain` memory note predates the out-of-process refactor; verify it still reads correctly against the current detached-subprocess + file-queue design before citing it.

---

## 9. References

### co-cli source (current)
- `co_cli/daemons/dream/` — `_loop.py`, `process.py` + `_process.py`, `_reviewer.py` (`MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`, `process_review`), `_housekeeping.py` (`run_housekeeping`, `merge_memory`, `merge_skills`, `decay_skills`, `prune_done_and_snapshots`, `prune_sessions` — note: **no `decay_memory`**), `_queue.py`, `state.py`
- `co_cli/dream_queue.py` — KICK enqueue (no `kick.py` module under `daemons/dream/`)
- `co_cli/daemons/dream/prompts/` — `memory_review.md`, `skill_review.md`, `memory_merge.md`, `skill_merge.md`
- `co_cli/config/dream.py`, `co_cli/config/skills.py`, `co_cli/config/core.py` (DREAM_* path constants incl. `DREAM_SNAPSHOTS_DIR`)
- `co_cli/main.py` — `_post_turn_hook`, `_maybe_kick_memory_review`, `_maybe_kick_skill_review`, `_fire_session_end_kicks`, `maybe_autospawn_dream`
- `co_cli/bootstrap/core.py` — autospawn flock
- `co_cli/tools/session/recall.py` — **lexical/regex** `session_search` (not BM25); `co_cli/tools/memory/` — `view.py` (`memory_view`) + `manage.py` (`memory_create/append/replace/delete`) + `recall.py` (`memory_search`, BM25)
- `co_cli/tools/user_profile/` — `view.py` (`user_profile_view`) + `write.py` (`user_profile_write`); `co_cli/memory/user_profile.py` (read); `co_cli/memory/item.py` — `MemoryKindEnum` incl. internal-only `canon`
- `co_cli/config/memory.py` — `user_profile_enabled`, `user_profile_char_budget`, `consolidation_similarity_threshold`
- `co_cli/skills/loader.py` — two-tier skill loading (bundled + user-global; no project-local tier)

### co-cli specs
- `docs/specs/dream.md` — runtime spec of record for the daemon
- `docs/specs/skills.md` — skills tier (curator section pending update to reflect housekeeping absorption)
- `docs/specs/memory.md`, `docs/specs/personality.md`, `docs/specs/prompt-assembly.md`

### hermes cross-reference
- `hermes-agent/agent/background_review.py` + `run_agent.py` (`_spawn_background_review`) — memory/skill nudge reviewers, separable via `review_memory`/`review_skills` flags (+ `_COMBINED_REVIEW_PROMPT`); review fork runs with `skip_memory=True` (external memory plugins bypassed, writes land on disk)
- `hermes-agent/tools/skill_manager_tool.py` — `skill_manage` (6 ops: create/edit/patch/delete/write_file/remove_file), atomic writes (`_atomic_write_text` + `atomic_replace`)
- `hermes-agent/agent/curator.py` + `hermes_cli/curator.py` — skill curator state machine, STILL PRESENT (7d interval / 30d stale / 90d archive, `.curator_state`, `apply_automatic_transitions()`); LLM consolidation **OFF by default** (`curator.consolidate=False`)
- `hermes-agent/tools/session_search_tool.py` — FTS5 + window retrieval, **no LLM summarization** ("zero LLM cost"); 4 modes (discovery/scroll/browse/read)
- `hermes-agent/agent/system_prompt.py` + `hermes_cli/config.py` — `USER.md` injection (volatile tier), `_user_profile_enabled` (default True)
- `hermes-agent/plugins/memory/honcho/` — dialectic backend (`__init__.py`, `client.py`, `session.py`, `cli.py`); review fork skips it via `skip_memory=True`
- `hermes-agent/utils.py` — `atomic_replace` / `atomic_json_write`
- **Scheduling (§7):** `hermes-agent/cron/scheduler.py` + `cron/jobs.py` — file-based `~/.hermes/cron/jobs.json`, 60s ticker, fcntl/msvcrt `.tick.lock`, at-most-once, parallel + 1-worker sequential pools, no auto-retry; `gateway/run.py` `_start_cron_ticker` — ticker daemon thread at boot; `agent/conversation_loop.py:620` — per-turn review nudge counter

### self-learning peer cross-reference — triggering (§7)
- `letta/letta/groups/sleeptime_multi_agent_v4.py:80,140` — sleep-time agents, post-step + `sleeptime_agent_frequency` gate, fire-and-forget `safe_create_task` (`utils.py:1166`); `letta/jobs/scheduler.py:25,60` — APScheduler + Postgres `pg_try_advisory_lock` leader election (batch-poll only); `services/summarizer/thresholds.py:27` — inline compaction at 90% ctx
- `elizaos/packages/core/src/types/evaluator.ts:44` — per-message inline evaluators; `features/autonomy/service.ts:69` + `utils/batch-queue/task-drain.ts:35` — DB-backed repeat-task queue (tags `queue/repeat`, `maxFailures:-1`)
- `mem0/mem0/memory/main.py:595,684` — all consolidation inline in `add()`; decay = query-time ranking only (`client/project.py:401`)
- `memU/src/memu/app/memorize.py:65,1100` — async-inline workflow, summarization inside `memorize()`; `database/inmemory/vector.py:50` — query-time recency decay
- `ReMe/reme_ai/service/personal_memory_service.py:80` — inline `async_execute_flow` on `add_memory()`; no scheduler/threshold/background

### openclaw cross-reference — scheduler design only (§7.5, not a self-learning peer)
- `openclaw/src/cron/service.ts` + `service/timer.ts` — in-process `setTimeout` timer loop (wake ≤60s, `MIN_REFIRE_GAP_MS=2s`), `maxConcurrentRuns` pool
- `openclaw/src/cron/store/schema.ts` + `run-log/sqlite-store.ts` — SQLite job store + append-only run-log audit
- `openclaw/src/cron/retry-hint.ts` — transient-vs-permanent error classification + backoff; `service/failure-alerts.ts` — consecutive-failure alerts w/ cooldown
- `openclaw/src/cron/isolated-agent/run.ts` + `session-reaper.ts` — ephemeral isolated session per run, reaped after retention; `service/agent-watchdog.ts` (`timer.ts:225`) — timeout clock deferred until agent setup completes (cold-start-safe)
- `openclaw/src/cron/types.ts` + `schedule.ts` — `at`/`every`/cron-expr schedules, Croner tz/DST (general-scheduler scope co does **not** borrow)

> Cut entirely (no self-learning, no design relevance retained): **codex** (no background system; inline mid-turn compaction only), **opencode** (`Effect.repeat` loops are infra-hygiene only).
