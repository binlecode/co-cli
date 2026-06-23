# RESEARCH — Self-Learning: co vs hermes (Architecture, Parity, Gaps)

**Status:** Reference / design research — not normative
**Scope:** Single source of truth for co-cli's self-improvement subsystem — the as-built dream daemon, how it compares to `hermes-agent`'s "self-improving" pitch, the verified gaps, the live open proposals, and the cross-peer triggering survey. Runtime spec of record: `docs/specs/dream.md`.

---

## 1. The subsystem at a glance

co-cli's learning loop has two halves, both owned by a single out-of-process background daemon (`co_cli/daemons/dream/`):

1. **Review** (event-driven, per-session): forked sub-agents mine a *single* session transcript for durable memory and for skill updates.
2. **Housekeeping** (scheduled, low-frequency): merge near-duplicate memory items and skills, decay stale ones, **synthesize the cross-session user profile**, and prune queue artifacts.

Layer map (current, three transcript-touching paths + the offline hygiene passes):

```
                  REVIEW (per session, queue-kicked)        HOUSEKEEPING (scheduled, ~daily, idle-gated)
                  -----------------------------------        --------------------------------------------
MEMORY   →  MEMORY_REVIEW_SPEC (forked agent)        →   merge_memory  (no age decay — by design)
SKILLS   →  SKILL_REVIEW_SPEC  (forked agent)        →   merge_skills + decay_skills
PROFILE  →  (per-session write-back via the          →   synthesize_user_profile (forked agent,
             memory reviewer's user_profile_* tools)      cross-session reconciler; off by default)
DOCTRINE →                — none (author-curated, never learned) —
```

Both review specs are *separate* forked agents (not one combined reviewer). **co now has two transcript readers, not one:** the per-session memory/skill reviewers (one transcript per KICK) and the cross-session profile synthesizer (a *window* of recent transcripts in one pass). All three forked agents and both halves run inside the one daemon process. The profile synthesizer is the structural answer to what was Gap C (now §5.1's closed Layer 1) — a *local* cross-session reconciler, no external backend.

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

Both apply the personality **curation lens** when the active soul defines one (`_with_curation_lens`, gated on `deps.config.personality`). Each reviewer sees **one session's transcript** — there is no cross-session view *at review time*. The cross-session view exists only in housekeeping: the profile synthesizer (§2.6) reconciles the user profile across a window, but **no equivalent cross-session pass exists for skills** (Gap D, §5.2, still open).

Notes on the dispatch surface:
- The memory reviewer carries the two `user_profile_*` tools, so USER.md write-back happens through a dedicated profile surface (`co_cli/tools/user_profile/{view,write}.py`), not through generic memory writes. The memory reviewer's prompt (`prompts/memory_review.md`) routes by **scope**: facts *about the person* (timezone, persona, communication style) → USER.md via `user_profile_write`; forward-acting operational rules scoped to a domain ("squash-merge PRs") → memory items via `memory_create`, even when phrased "always".
- `process_review(deps, domain, session_id, persisted_message_count, transcript_override=None)` loads the live transcript (capped by `persisted_message_count`) or, on compaction, an uncapped snapshot from `transcript_override` (`_reviewer.py:144`). The skill reviewer calls `refresh_skills(child_deps)` before running so it sees up-to-date skill state (`_reviewer.py:140`).
- Both reviewers emit a `SessionReviewOutput` (`summary`, `skills_patched`, `skills_created`, `knowledge_created`, `knowledge_updated`) — the profile synthesizer reuses the same type. The session reviewer can also write `kind='canon'` items (internal-only kind, excluded from the model-callable memory tools — see §5.3).

### 2.4 Housekeeping (`_housekeeping.py`)

`run_housekeeping(deps, cfg, state)` runs the phases below, scheduled (not per-session). Phase ordering and timeout discipline (`_housekeeping.py:669`):

1. **merge_memory** + **merge_skills** — both run *inside one* `asyncio.timeout(cfg.max_pass_seconds)` block (default 600s). On timeout, partial counters persist and the rest of the pass still runs.
   - *merge_memory* — union-find cluster same-kind, non-`decay_protected`, non-article items by token-Jaccard ≥ `memory.consolidation_similarity_threshold`; LLM-consolidate (`prompts/memory_merge.md`); archive originals. Canonical anchor = highest `recall_count`, recency tiebreak. Caps: `_MAX_CLUSTER_SIZE=5`, `_MAX_MERGES_PER_CYCLE=10`, `_MERGED_BODY_MIN_CHARS=20`. Articles excluded (LLM-merging fetched RAG substrate breaks source integrity).
   - *merge_skills* — cluster similar **non-pinned user** skills (bundled skills never considered) by `skills.consolidation_similarity_threshold`, LLM-consolidate into a class-level umbrella (`prompts/skill_merge.md`), archive non-anchor originals (to `.archive/`), then `refresh_skills(deps)`. Canonical anchor = highest `(distinct recall days, use_count)`. **This absorbs the former skill curator's consolidation.**
2. **synthesize_user_profile** — the cross-session profile reconciler, in its own `asyncio.timeout(_PROFILE_SYNTHESIS_MAX_SECONDS=180)` block (between skill merge and skill decay), wrapped in a broad `except` so a failure is best-effort and never aborts the pass. Off by default. Detailed in §2.6.
3. **decay_skills** — archive aged user skills past `decay_after_days` with no recall inside `recall_protection_days` (cap `_MAX_DECAY_PER_CYCLE=20`). Synchronous, outside the merge timeout so a slow merge can't starve it.
4. **memory_count_over_cap warn** — a warn-only tripwire: if active item count > `MEMORY_ITEM_COUNT_WARN` (10 000, ~500× current real usage) it logs + emits a `co.housekeeping.memory_count_warn` span event and **archives nothing**. Signals a write loop / runaway agent / fixture pollution, not normal growth.
5. **prune_done_and_snapshots** — delete `queue/done/` items and orphaned compaction snapshots past `done_retention_days`. `queue/failed/` is intentionally left intact (rare, diagnostic).
6. **prune_sessions** — delete canonically-named session transcripts past `dream.session_retention_days` (0 = retain forever, the default); the live session's recent mtime keeps it from ever being selected.

> **No `decay_memory` phase.** Memory is never decayed by age/recall — storage is treated as unconstrained, so the only memory-side hygiene is merge (plus the warn-only count tripwire). Only *skills* decay; `memory.decay_after_days`-style aging governs skills, not memory.

State: `HousekeepingState` (in `state.py`) at `$CO_HOME/daemons/dream/_dream_state.json`: `last_housekeeping_at`, cumulative `stats` (`memory_merged`, `skill_merged`, `skill_decayed`, `profile_synthesized`), and `last_synthesized_session` (a `SessionMarker`, see §2.6). Schedule (`scheduled_tick_due`, `_loop.py:58`): at least `run_interval_hours` since last run, clamped to the next `run_start_at` time-of-day; never-run state returns due so a fresh daemon gets a baseline pass. Manual trigger: a `DREAM_TIDY_TAG` sentinel file (`co dream tidy`) jumps the queue ahead of the scheduled check (`_loop.py:82`).

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

`consolidation_similarity_threshold` is defined on **both** `MemorySettings` (memory-merge) and `SkillsSettings` (skill-merge), each defaulting `0.75`. The profile surface lives on `MemorySettings`:

| Field | Default | Role |
|---|---|---|
| `user_profile_enabled` | `True` | inject `~/.co-cli/USER.md` every turn + allow reviewer write-back |
| `user_profile_char_budget` | `1500` | USER.md size ceiling (enforced on write) |
| `profile_synthesis_enabled` | `False` | enable the cross-session synthesis sub-pass (§2.6) |
| `profile_synthesis_lookback_sessions` | `10` (`ge=2`) | window size; synthesis fires at `lookback // 2` accumulated sessions |
| `MEMORY_ITEM_COUNT_WARN` (module constant) | `10_000` | warn-only count tripwire; not a settings.json knob |

The whole loop ships **off by default** at three independent gates: `dream.enabled=False` (no daemon), `review_enabled=False` (no per-session review), and `profile_synthesis_enabled=False` (no cross-session synthesis even when the daemon and profile are on).

### 2.6 Cross-session profile synthesis (`synthesize_user_profile`) — the Gap-C closure

This is the load-bearing addition since the last refresh: the long-planned "embedded periodic re-derive the profile from recent transcripts" pass, **built**. It is co's *local* cross-session user-modeling pass — the second transcript reader, layered over the per-session memory reviewer.

**Design — what it is.** A forked agent (`_profile_synthesis_spec()`, tools `user_profile_view` / `user_profile_write` / `memory_search`) that reads *several* recent session transcripts at once plus the current `USER.md` and rewrites the whole profile. Where the per-session reviewer writes USER.md one transcript at a time (and so can only react to the session in front of it), the synthesizer is the reconciler that catches what no single session can — durable patterns, contradictions, and facts gone stale. It is gated off by default and is a no-op when `user_profile_enabled=False`. It deliberately does **not** graft the soul curation lens (different layer; `_profile_synthesis_instructions` is the bare prompt).

**Processing logic — the trigger is session-accumulation, not wall-clock.** The housekeeping tick is the *clock*, but synthesis fires on *session count* (`_housekeeping.py:597`):

- Each tick recomputes the **un-settled** sessions — those newer than the persisted `last_synthesized_session` marker — from `list_sessions(...)` (on-disk ground truth). Because the trigger is recomputed from disk every tick, it survives daemon restarts with **no counter to persist** — only the marker.
- Synthesis fires only when `len(unsettled) >= lookback // 2` (default ≥5); otherwise it logs and no-ops.
- The window is **anchored at the marker** — the oldest `lookback` un-settled sessions, drained **oldest-first** — *not* at the newest session. So a backlog larger than `lookback` is never skipped; it drains `lookback // 2` per tick.

**Marker advance — success-gated, 50% overlap.** On a *successful write only*, the marker advances forward by `lookback // 2` (settling the oldest half of the window; the newer half stays un-settled and reappears next window), and `stats.profile_synthesized` increments. Window `N` with step `N/2` overlaps consecutive runs 50%, so **every session lands in ≥2 windows** and none is processed in isolation. A timed-out / errored / cancelled run leaves the marker untouched, so the same sessions are re-counted and retried next tick (`_PROFILE_SYNTHESIS_MAX_SECONDS=180`, wrapped in best-effort `except`).

**The reasoning model (the prompt is where the Honcho-derived technique lives).** `prompts/profile_synthesis.md` encodes four reasoning moves co lifted from Honcho's dialectic design (memory: borrow the *reasoning*, reject the backend):
1. **Prior + evidence (Bayesian framing).** Treat the current profile as the *prior*, transcripts as *evidence that refines it* — not a fresh start that replaces it. Keep durable facts unless contradicted.
2. **Value-change vs. genuine contradiction.** A changed attribute value ("uses pytest" → "custom runner") *replaces* the old value; two statements that *cannot both be true* are resolved by trusting the more recent, consistent evidence and dropping the loser.
3. **Two-session pattern bar.** A working-style trait is promoted only when **≥2 sessions** show it; a single-session behavior is provisional and left out. Recurrence is what separates a pattern from a one-off.
4. **The volatility test.** Before keeping a fact, ask "would this plausibly change within six months without the user announcing it?" — if yes it is session-local, not durable identity.

Output is a wholesale rewrite under the char budget (consolidate, never blind-truncate), returning a `SessionReviewOutput` whose `summary` describes what was reconciled.

> **Why this matters for the parity story:** hermes-core has **no** cross-session synthesis — its memory reviewer writes USER.md one transcript at a time, and the only cross-session user model it offers is the external **Honcho** backend (§5.1). co now does the cross-session reconciliation *locally, inspectably, with no network dependency*. This inverts the old Gap-C framing (§5.1, §4 claim #5).

---

## 3. The architecture decision (settled)

The as-built design is:

- **Unified on the process axis**: one out-of-process daemon, one queue, one housekeeping pass spanning memory and skills, shared `_dream_state.json`.
- **Split on the review axis**: two domain reviewers (memory, skill) rather than one combined reviewer.
- **No curator state machine**: skill upkeep is LLM merge + age-based decay on the daily housekeeping cadence (no `active→stale→archived` lifecycle, no `.curator_state.json`).

- **Cross-session reconciliation lives in housekeeping, not review.** The per-session reviewers stay single-transcript; the cross-session view is a *housekeeping* sub-pass (profile synthesis, §2.6). This keeps the per-turn KICK path cheap and pushes the expensive multi-transcript read onto the idle clock.

Settled — do not re-litigate without new evidence. With profile synthesis shipped (§2.6), the cross-session *profile* gap is closed locally; the one remaining open analysis item is the cross-session *skill*-mining gap (Gap D, §5.2): the design reorganized around it (and proved the cross-session-window pattern works, via profile synthesis) but did not extend it to skills.

---

## 4. co vs hermes — parity map

hermes-agent markets itself as "the self-improving AI agent" on five claims:

| hermes claim | hermes mechanism | co mechanism | Verdict |
|---|---|---|---|
| #1 "Creates skills from experience" | skill-nudge reviewer (`run_agent.py`) → `skill_manage` | `SKILL_REVIEW_SPEC` → `skill_create/edit/patch` | **PARITY** (mechanism); hermes richer prompt |
| #2 "Improves them during use" | same reviewer, patch-loaded-first | same posture in `skill_review.md` | **PARITY** (mechanism) |
| #3 "Nudges itself to persist knowledge" | memory-nudge reviewer + skill-nudge reviewer, **separate** | `MEMORY_REVIEW_SPEC` + `SKILL_REVIEW_SPEC`, **separate** | **PARITY in shape** |
| #4 "Searches its own past conversations" | FTS5 + windowing, no LLM summarization, **3 modes** | lexical/regex over raw JSONL transcripts, no summarization | **PARITY** (§5, closed) |
| #5 "Builds a deepening model of who you are" | per-session `USER.md` write-back (default **off**) + Honcho dialectic backend (external) | always-injected `~/.co-cli/USER.md` write-back **+ local cross-session synthesis** (§2.6) | **co LEADS on local cross-session modeling**; only the backend *behavioral* model remains hermes-unique (§5.1) |

Notes:

- **Claim #4.** hermes's `tools/session_search_tool.py` is pure FTS5 + message-window retrieval ("zero LLM cost"). It is now **3 modes** (was 4): discovery (FTS5 + lineage dedup + ±window + first/last-3 bookends), scroll (window around a message id), browse (recent sessions). The former separate "read" mode is folded into discovery's bookends (`session_search_tool.py:8-29`). co's `session_search` is lexical/regex over raw JSONL transcripts (no index). Both return raw windows, neither summarizes — parity on the no-summarization axis. The retrieval *substrate* differs: hermes FTS5-indexes transcripts; co does not index sessions at all.
- **Claim #5 (inverted since last refresh).** hermes's `_user_profile_enabled` now defaults **False** (`agent/agent_init.py:1141`) and its USER.md is written back **only per-session**, one transcript at a time (`tools/memory_tool.py`, `target="user"`); there is **no** cross-session synthesis pass in hermes-core — cross-session user modeling exists only if you enable the external Honcho backend. co ships USER.md on by default (`user_profile_enabled=True`) *and* a local cross-session reconciler (§2.6, opt-in but in-tree, no network). So co now does *locally* what hermes only does *via an external backend*. The honest residual is that Honcho synthesizes a second-order **behavioral/dialectic** model (peer cards, Q&A); co's synthesis re-derives a first-order **declarative** profile. Different ambition, but co's is local-inspectable-reversible by construction.
- **Claim #3.** co **splits** memory+skill review into two domain reviewers; hermes keeps them separable (`review_memory` / `review_skills` flags in `agent/background_review.py`, with a `_COMBINED_REVIEW_PROMPT` when both fire on the same turn).
- **Skill curator (co↔hermes divergence).** co has no curator state machine — skill upkeep is merge+decay in housekeeping. hermes ships the curator (`agent/curator.py` + `hermes_cli/curator.py`): a 7d-interval / 30d-stale / 90d-archive state machine persisted to `.curator_state`, deterministic transitions via `apply_automatic_transitions()`, with its **LLM consolidation (umbrella-building) pass OFF by default** (`curator.consolidate=False`). So co runs LLM skill-merge by default; hermes runs deterministic decay by default and consolidates only when enabled. (`recall_protection_days=30`, `decay_after_days=90` are co housekeeping thresholds, not curator transitions.)

> co-unique surfaces (not "self-improving" per se, included for an honest map): **local cross-session profile synthesis** (§2.6 — hermes gets cross-session user modeling only from the external Honcho backend, never locally); **personality doctrine** (soul/mindsets/critique injected statically, author-curated, never learned — `docs/specs/personality.md`); **the housekeeping merge pass** as deeper offline memory hygiene than hermes's per-turn nudge; **atomic-write discipline** on every memory/skill/state write (hermes at parity via `atomic_replace`).

---

## 5. Gaps

**Closed since earlier refreshes** (no longer gaps; kept as one-line pointers so the history isn't lost):

- **Gap A — cross-session recall.** Both co (lexical/regex over raw JSONL via `SessionStore.search()`) and hermes (FTS5, 3-mode) return raw windows and neither summarizes — parity, no gap (§4 claim #4).
- **Gap B — always-injected user profile.** Shipped: `~/.co-cli/USER.md`, written back by the memory reviewer via `user_profile_view`/`user_profile_write` (§4 claim #5). No `kind='user'`; model-facing kinds are `rule | article | note`.
- **Gap C Layer 1 — local cross-session profile reconciliation.** Shipped: `synthesize_user_profile` (§2.6), which hermes-core lacks entirely. **co leads** on this layer.

Only the items below remain open.

### 5.1 Gap C (residual) — second-order behavioral / dialectic user model — **OPEN, deliberately declined**

hermes's Honcho plugin (5 tools, per-turn prefetch, dialectic Q&A) synthesizes a *behavioral* model backend-side (peer cards, conclusions). co has no honcho/dialectic/peer-card/`MemoryProvider` of any kind; its model of the user is the union of declarative facts in USER.md (now cross-session-reconciled, §2.6) + memory items it chose to save. This is **not a capability hole but a scope line**: the mission tension ("local, inspectable, reversible") argues against an external backend as default. co's answer is to deepen the *local* synthesis (§2.6), not adopt a backend.

### 5.2 Gap D — cross-session skill recurrence signal — **OPEN**

- **What's missing:** no component proactively recognizes "this 4-step procedure has recurred across N sessions, make it an umbrella skill." The skill reviewer sees **one** transcript per KICK; skill **merge** in housekeeping clusters only *already-saved* skills (it never reads transcripts). The cross-session-window machinery now exists (profile synthesis, §2.6) but is not applied to skills.
- **Net behavior:** per-session reviewer creates near-duplicate skills, housekeeping merge later collapses them. The recurrence signal exists only as after-the-fact dedup, never as up-front recognition.

### 5.3 Note — the internal `canon` kind (co-side, not a gap)

`MemoryKindEnum` carries a fourth value, `canon`, beyond the three model-facing kinds (`rule | article | note`). It is **internal-only**: excluded from the model-callable memory tools (`MemoryKind = Literal[rule, article, note]`), it is the kind the dream reviewer uses for personality/canon material. Recorded here so the "memory kinds are rule|article|note" claim in §4 is understood as the *model-facing* surface, not the full enum.

---

## 6. Open proposals

### 6.1 Remaining gap-closure work

- **Gap D (§5.2)** — give the skill reviewer (or a housekeeping sub-pass) a small recent-session window so it can propose umbrella skills from recurrence *before* duplicates accumulate, rather than relying solely on after-the-fact merge. The profile synthesizer (§2.6) already proves the cross-session-window pattern works; this extends it to skills. Reconcile with §6.2 so there is one skill-creation path, not two.
- **Gap C residual (§5.1) — forward, not a closure** — deepen the *local* synthesis (e.g. carry lightweight cross-session behavioral signal into the synthesis prompt), not adopt a backend.
- **Optional, not a gap** — a summarization stage on `session_search` (aux-LLM digest per window, config-gated `sessions.summarize_results=False`, token-capped, single-flight) is a co-unique enhancement to weigh on its own context-spend ROI, never peer catch-up. *Small.*

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
| **co** | file-queue KICKs — per-turn nudge counters + session-end + pre-compaction snapshot (`co_cli/dream_queue.py`, `main.py:_post_turn_hook`) | in-loop `scheduled_tick_due` (`_loop.py:58`), 24h + `run_start_at`, **idle-gated** (empty-queue branch only, `_loop.py:115`); cross-session profile synthesis gates on session-count within it (§2.6) | **out-of-process detached daemon** (only peer to do this) | **filesystem** (queue dir + `_dream_state.json` + session marker; per-cycle token ledger in `usage.jsonl`) |
| **hermes** | per-turn **daemon thread**, fires after ≥N tool iterations (default 10) on a non-empty turn (`agent/background_review.py`, `conversation_loop.py:620`) | 60s **ticker daemon thread** at gateway boot (`gateway/run.py` `_start_cron_ticker`) | in-process threads | `~/.hermes/cron/jobs.json` + `.tick.lock` |
| **letta** | **sleep-time agents** — post-step, gated by `sleeptime_agent_frequency` (every Nth turn), fire-and-forget `safe_create_task` (`groups/sleeptime_multi_agent_v4.py:80,140`) | APScheduler `IntervalTrigger` + jitter, Postgres advisory-lock leader election (`jobs/scheduler.py`; batch-poll only) | in-process async tasks (+ leader node) | database (Postgres) |
| **elizaos** | per-message **evaluators** (`shouldRun`/`evaluate`, `types/evaluator.ts:44`) | DB-backed repeat-task queue — `TaskDrain` rows tagged `queue/repeat`, interval in metadata (`utils/batch-queue/task-drain.ts:35`) | in-process; DB-polled tasks | database |
| **mem0 / memU / ReMe** | **none** — consolidation runs inline-synchronously inside `add()`/`memorize()`; decay is query-time ranking | none | the foreground request (library you call) | n/a (caller owns triggering) |

Three structural facts fall out:

1. **Frequency-counter gating is the convergent capture trigger.** co's `review_*_nudge_interval`, letta's `sleeptime_agent_frequency` (every Nth turn), and hermes's ≥N-tool-iterations are the same mechanism — fire the learning pass every N units of activity. co is squarely on this pattern, and notably uses it **twice on different axes**: per-*turn* counters gate the episodic reviewers, while the cross-session profile synthesizer (§2.6) gates on accumulated *sessions* (`lookback // 2`) recomputed from disk each clock tick. The latter is a frequency counter that needs no persisted counter — the session marker on disk *is* the count — so it survives restarts for free, closer in spirit to letta's every-Nth-turn gate than to a wall-clock cron.

2. **co is the lone out-of-process design — and the lone filesystem-durable one.** hermes runs daemon threads, letta/elizaos run in-process async tasks; all die with the host process (mitigated by catch-up / DB persistence). co's detached daemon + durable file queue is the only crash-decoupled producer/consumer — its standout robustness property, consistent with `feedback_queue_decoupling_invariant` / `feedback_queue_sole_bridge`.

3. **"Only one runner" is universal, by three different mechanisms.** co's bootstrap **flock** (autospawn), hermes's **`.tick.lock`** (fcntl/msvcrt), and letta's **Postgres advisory lock** (`pg_try_advisory_lock`) all enforce single-runner; co's is the only one that needs no shared DB or host-process coordination.

### 7.2 The one axis where peers diverge *from* co: idle-gating the clock

No kept peer subordinates its periodic path to load. hermes runs its 60s ticker on an **independent thread**; letta's APScheduler fires on its **own interval**; elizaos drains repeat-tasks on a **DB-driven cadence**. All fire periodic work regardless of conversation load. co alone runs housekeeping **only on the empty-queue branch** (`_loop.py:115`) — so under sustained KICK load the daily pass (now including the shipped profile-synthesis sub-pass, §2.6) can be starved indefinitely. Multiple independent self-learners converging on "periodic work gets its own un-preemptable cadence" is the strongest signal in this survey that co's idle-subordination is the outlier. (For *episodic* reviewers, idle-gating is fine; the divergence only bites the clock-driven phases.)

### 7.3 Durability / observability ladder

Ranked richest → thinnest on the learning-upkeep path:

- **letta / elizaos — richest (DB-backed).** A database carries the durable job/task state, so they get **queryable run history for free** — letta persists batch jobs + leader state in Postgres; elizaos persists repeat-tasks as DB rows (with `maxFailures:-1` infinite retry). Survives restart, inspectable by query.
- **hermes — middle.** `jobs.json` atomic writes; **cross-process advisory lock** + in-process RLock; **at-most-once** (advance `next_run_at` under lock *before* submit); in-flight dedup + stale-run catch-up; per-job `last_run_at`/`last_status` only (**no append-only audit**); **no automatic retry** — manual CLI re-run.
- **co — durable transport, improving observability.** File queue FIFO + `done/`/`failed/` dirs + **flat 3× retry × 30s backoff** (notably, the *only* kept peer with automatic retry of the learning path — hermes has none) + single `_dream_state.json` (cumulative `stats`: `memory_merged`, `skill_merged`, `skill_decayed`, `profile_synthesized`). Two observability surfaces have landed since the last refresh:
  - **Daemon token ledger.** Each completed cycle flushes a `usage.jsonl` line with `origin="daemon"` and null `session_id` (`_loop.py:_flush_daemon_usage`, `session/usage.py:ORIGIN_DAEMON`) — POSIX-atomic cross-process appends shared with the REPL. So learning-path *token spend* is now queryable (summed into the combined total, never attributed to a session). This is the cost half of "did a run earn its keep?".
  - **Status surface.** `co dream status` (`process.py:status_daemon`) reports `running`, `pid`, `uptime_seconds`, `queue_depth`, and **`failed_count`** — so `failed/` is no longer *silently* invisible; it is surfaced on demand.
- The remaining deficits vs the DB-backed cohort: no **per-phase outcome audit** (the `stats` are cumulative, not per-run — you can't see *which* run merged what), no **error classification** (every failure burns all 3 retries identically), and no **proactive** consecutive-failure alert (`failed_count` is pull-only via `status`, not a log-warn that fires when it climbs).

So co *out-retries* hermes (automatic vs manual) and now logs its token spend + surfaces failure counts, but still *under-observes* the DB-backed cohort on the per-run outcome audit.

### 7.4 Transferable to co's dreamer (ranked, filtered for co's flat-file minimalism)

1. **Structured per-phase run-log / outcome audit (still the highest ROI).** *Partially addressed:* the `usage.jsonl` daemon-origin line (§7.3) now captures per-cycle token *spend*, and `_dream_state.json` carries cumulative phase counters — but neither answers "what did *this* run do?" (merged which clusters, dropped which contradiction, synthesized the profile or no-op'd). The remaining work is a **per-pass JSONL run-log** (mirroring `usage.jsonl`), not a DB — one record per pass with the per-phase outcome. The token ledger is the cost side; this is the missing *outcome* side. Buys queryable run history without abandoning the file-queue's decoupling.
2. **Consecutive-failure surfacing.** *Partially addressed:* `co dream status` now reports `failed_count` (§7.3), so the blind spot is no longer total — but it is **pull-only**. The remaining work is a **proactive log-warn threshold** that fires when `failed/` climbs N in a row, so a persistently-broken reviewer/synthesis is visible without someone running `status`.
3. **Transient-vs-permanent error classification on retry.** Unchanged — co still burns all 3 retries on permanent errors; classifying to skip non-transient failures is cheap and aligned. (General best practice — none of the kept self-learning peers implements this richly, so lowest peer-evidence of the three; openclaw's `retry-hint.ts` in §7.5 is the concrete model.)
4. **Independent clock timing (biggest design change).** Unchanged and now higher-stakes: with profile synthesis (§2.6) added to the idle-gated housekeeping path, *two* clock-driven learning passes (merge/decay + synthesis) are subordinate to queue drain. Under sustained KICK load both can be starved (§7.2). Give the clock-driven phases a cadence not subordinate to queue drain. Weigh against co's single-loop simplicity.

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
- **External memory provider:** offloading consolidation/decay/user-modeling to a provider (hermes/Honcho style) would free housekeeping to focus on mining, but adds a dependency and cuts against co's local-self-sufficiency mission. Now even less compelling: the local profile synthesizer (§2.6) already does the first-order cross-session reconciliation a backend would. The only thing a backend adds is the *second-order behavioral* model — which §5.1 deliberately declines. Probably not desirable as default.
- **Behavioral signal in synthesis:** should the profile synthesizer (§2.6) be fed lightweight behavioral telemetry (e.g. which tools the user reaches for, correction frequency) as additional evidence, narrowing the §5.1 residual gap *locally* — or does that over-reach the "declarative profile" scope it was built for? Needs sessions data.
- **Daemon "split-brain" framing:** the `feedback_dream_daemon_split_brain` memory note predates the out-of-process refactor; verify it still reads correctly against the current detached-subprocess + file-queue design before citing it.

---

## 9. References

### co-cli source (current)
- `co_cli/daemons/dream/` — `_loop.py` (`main_loop`, `scheduled_tick_due`, `_maybe_housekeep`, **`_flush_daemon_usage`**), `process.py` + `_process.py` (`status_daemon` now reports `failed_count` + `queue_depth`), `_reviewer.py` (`MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`, `process_review`, `SessionReviewOutput`), `_housekeeping.py` (`run_housekeeping`, `merge_memory`, `merge_skills`, **`synthesize_user_profile`**, `decay_skills`, `memory_count_over_cap`, `prune_done_and_snapshots`, `prune_sessions` — note: **no `decay_memory`**), `_queue.py`, `state.py` (`HousekeepingState`, `HousekeepingStats` incl. `profile_synthesized`, **`SessionMarker`** + `last_synthesized_session`)
- `co_cli/dream_queue.py` — KICK enqueue + `write_dream_snapshot` (no `kick.py` module under `daemons/dream/`)
- `co_cli/daemons/dream/prompts/` — `memory_review.md`, `skill_review.md`, `memory_merge.md`, `skill_merge.md`, **`profile_synthesis.md`** (the four Honcho-derived reasoning moves)
- `co_cli/config/dream.py` (note `run_interval_hours` daily-grid validator), `co_cli/config/skills.py`, `co_cli/config/core.py` (DREAM_* path constants incl. `DREAM_SNAPSHOTS_DIR`, `DREAM_TIDY_TAG`)
- `co_cli/main.py` — `_post_turn_hook`, `_maybe_kick_memory_review`, `_maybe_kick_skill_review`, `_fire_session_end_kicks`, `maybe_autospawn_dream`
- `co_cli/bootstrap/core.py` — autospawn flock
- `co_cli/session/usage.py` — `ORIGIN_DAEMON`, `append_turn` (daemon-origin token ledger line, null `session_id`)
- `co_cli/tools/session/recall.py` — **lexical/regex** `session_search` (not BM25); `co_cli/tools/memory/` — `view.py` (`memory_view`) + `manage.py` (`memory_create/append/replace/delete`) + `recall.py` (`memory_search`, BM25)
- `co_cli/tools/user_profile/` — `view.py` (`user_profile_view`) + `write.py` (`user_profile_write`); `co_cli/memory/user_profile.py` (read); `co_cli/memory/item.py` — `MemoryKindEnum` incl. internal-only `canon`
- `co_cli/config/memory.py` — `user_profile_enabled`, `user_profile_char_budget`, **`profile_synthesis_enabled`**, **`profile_synthesis_lookback_sessions`**, `consolidation_similarity_threshold`, `MEMORY_ITEM_COUNT_WARN`
- `co_cli/skills/loader.py` — two-tier skill loading (bundled + user-global; no project-local tier)

### co-cli specs
- `docs/specs/dream.md` — runtime spec of record for the daemon
- `docs/specs/skills.md` — skills tier (curator section pending update to reflect housekeeping absorption)
- `docs/specs/memory.md`, `docs/specs/personality.md`, `docs/specs/prompt-assembly.md`

### hermes cross-reference
- `hermes-agent/agent/background_review.py` + `run_agent.py` (`_spawn_background_review`) — memory/skill nudge reviewers, separable via `review_memory`/`review_skills` flags (+ `_COMBINED_REVIEW_PROMPT`); review fork runs with `skip_memory=True` (external memory plugins bypassed, writes land on disk)
- `hermes-agent/tools/skill_manager_tool.py` — `skill_manage` (6 ops: create/edit/patch/delete/write_file/remove_file), atomic writes (`_atomic_write_text` + `atomic_replace`)
- `hermes-agent/agent/curator.py` (`:56-64` intervals/`DEFAULT_CONSOLIDATE=False`, `:276` `apply_automatic_transitions()`) + `hermes_cli/curator.py:81` — skill curator state machine, STILL PRESENT (7d interval / 30d stale / 90d archive, `.curator_state`); LLM consolidation **OFF by default** (`curator.consolidate=False`); `agent/curator_backup.py` handles state backup
- `hermes-agent/agent/{insights,trajectory,error_classifier,memory_manager}.py` — checked for new learning machinery; **none self-learns**: `insights.py` is analytics/reporting over the state DB, `trajectory.py` is ShareGPT training-data capture, `error_classifier.py` is deterministic failover taxonomy, `memory_manager.py` is the provider-orchestration framework. hermes's durable learning remains exactly: per-session background review + curator + file-backed MEMORY.md/USER.md (no cross-session synthesis)
- `hermes-agent/tools/session_search_tool.py` — FTS5 + window retrieval, **no LLM summarization** ("zero LLM cost"); now **3 modes** (discovery/scroll/browse), "read" folded into discovery bookends (`:8-29`)
- `hermes-agent/agent/system_prompt.py:431` + `agent/agent_init.py:1141` — `USER.md` injection (volatile tier); `_user_profile_enabled` now **defaults False** (was True). Write-back is **per-session only** (`tools/memory_tool.py:902-971`, `target="user"`); **no cross-session synthesis pass** in hermes-core — this is what co's §2.6 now does locally
- `hermes-agent/plugins/memory/honcho/__init__.py` — dialectic backend, **5 tools** (`honcho_profile`, `honcho_search`, `honcho_reasoning`, `honcho_context`, `honcho_conclude`); per-turn `prefetch()` (`agent/memory_provider.py:94`); synthesizes a backend-side behavioral model (peer cards, conclusions); review fork skips it via `skip_memory=True`. (Pluggable via `agent/memory_manager.py` + `agent/memory_provider.py` ABC — Honcho/Hindsight/Mem0; none default-on.)
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
