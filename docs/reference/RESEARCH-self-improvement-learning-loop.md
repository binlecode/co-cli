# RESEARCH — Self-Improvement / Learning Loop: Architecture, Parity, and Gaps

**Date:** 2026-06-14
**Status:** Reference / design research — not normative
**Scope:** Single source of truth for co-cli's self-improvement subsystem — the as-built dream daemon, how it compares to `hermes-agent`'s "self-improving" pitch, the verified gaps, and the live open proposals.

**Supersedes and replaces** (deleted on creation of this file):
- `RESEARCH-self-improvement-architecture.md` (2026-05-18) — per-layer vs unified daemon decision. Overtaken by events; outcome folded into §3.
- `RESEARCH-self-learning-co-vs-hermes.md` (2026-05-18) — co↔hermes parity audit. Co-side inventory rotted; corrected in §4–§5.
- `RESEARCH-self-generated-work-prompt-skill.md` (2026-04-16) — prompt-synthesis proposal. Live items only carried into §6.

> Why a rewrite: the three predecessors all described an **in-process, three-runner** architecture (`co_cli/skills/session_review.py`, `co_cli/skills/curator.py`, `co_cli/memory/dream.py`) that has since been refactored out of existence. Every module path, config field, and several headline conclusions in those docs were stale. This file is grounded in source read on 2026-06-14; the runtime spec of record is `docs/specs/dream.md`.

---

## 1. The subsystem at a glance

co-cli's learning loop has two halves, both owned by a single out-of-process background daemon (`co_cli/daemons/dream/`):

1. **Review** (event-driven, per-session): forked sub-agents mine a session transcript for durable memory and for skill updates.
2. **Housekeeping** (scheduled, low-frequency): merge near-duplicate memory items and skills, decay stale ones, prune queue artifacts.

Layer map (replaces the old three-runner table):

```
                  REVIEW (per session, queue-kicked)        HOUSEKEEPING (scheduled, ~daily)
                  -----------------------------------        --------------------------------
MEMORY   →  MEMORY_REVIEW_SPEC (forked agent)        →   merge_memory + decay_memory
SKILLS   →  SKILL_REVIEW_SPEC  (forked agent)        →   merge_skill  + decay_skill
DOCTRINE →                — none (author-curated, never learned) —
```

Both review specs are *separate* forked agents (not one combined reviewer), and both halves run inside one daemon process.

---

## 2. Current architecture, source-grounded

### 2.1 One daemon, queue-driven

- Package: `co_cli/daemons/dream/` (`__init__.py` is docstring-only per repo rules).
- Process lifecycle: `process.py` (`co dream start/stop/status`, foreground execution); main loop in `_loop.py`.
- Trigger model: **filesystem queue + polling**, not the old per-turn in-process `asyncio.Task`. The REPL writes KICK JSON files; the detached daemon polls `queue/` every `tick_interval_seconds` (default 5s) and consumes FIFO.
- Auto-spawn: `maybe_autospawn_dream(deps, frontend)` at REPL startup (`co_cli/main.py`), guarded by `bootstrap/core.py` advisory flock; spawns a detached `co dream start --foreground` subprocess when `dream.enabled` and `CO_DREAM_NO_AUTOSPAWN` is unset.

This matches the durable-queue invariants in memory: producer never gated on consumer liveness (`feedback_queue_decoupling_invariant`), queue is the sole cross-process bridge with no side-channel wake-ups (`feedback_queue_sole_bridge`).

### 2.2 The KICK queue

Producer: `co_cli/daemons/dream/kick.py` — shared by REPL post-turn, session-end, and compaction paths. Atomic JSON writes. Payload: `{domain: "memory"|"skill", session_id, persisted_message_count, created_at, transcript_override?}`.

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
| `MEMORY_REVIEW_SPEC` | `memory_search`, `memory_create`, `memory_append`, `memory_replace` | `prompts/memory_review.md` |
| `SKILL_REVIEW_SPEC` | `skill_view`, `skill_create`, `skill_edit`, `skill_patch`, `memory_search` (+ skill manifest) | `prompts/skill_review.md` |

Both apply the personality **curation lens** when the active soul defines one. Each reviewer sees **one session's transcript** — there is no cross-session view at review time (see Gap D, §5.4).

### 2.4 Housekeeping (`_housekeeping.py`)

`run_housekeeping(deps, state)` runs four LLM/code phases plus a prune, scheduled (not per-session). "Reviewer is the sole transcript reader" — housekeeping never reads transcripts; it operates on already-saved memory items and skills.

1. **merge_memory** — union-find cluster same-kind, non-immune, non-article items by token-Jaccard ≥ `consolidation_similarity_threshold`; LLM-consolidate (`prompts/memory_merge.md`); archive originals. Caps: `_MAX_CLUSTER_SIZE`, `_MAX_MERGES_PER_CYCLE`, `_MERGED_BODY_MIN_CHARS`.
2. **decay_memory** — archive items past `memory.decay_after_days` with no recent recall.
3. **merge_skill** — cluster similar user skills, LLM-consolidate into a class-level umbrella (`prompts/skill_merge.md`), archive originals, then `refresh_skills(deps)`. **This absorbs the former skill curator's consolidation.**
4. **decay_skill** — archive aged user skills with no recent recall.
5. **prune** — delete `queue/done/` items and orphaned compaction snapshots past `done_retention_days`.

State: `HousekeepingState` at `$CO_HOME/daemons/dream/_dream_state.json` (`last_housekeeping_at`, cumulative `stats`: `memory_merged/memory_decayed/skill_merged/skill_decayed/done_pruned`). Schedule: at least `run_interval_hours` since last run, fired at the next `run_start_at` time-of-day.

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

Gone since the 05-18 docs: `skills.curator_enabled`, `skills.curator_interval_hours`, `memory.consolidation_enabled/_trigger/_lookback_sessions`, and the `memory_manage` tool (split into `memory_create/append/replace/delete`). The whole loop ships **off by default** (`dream.enabled=False`, `review_enabled=False`).

---

## 3. The settled architecture decision (record)

The predecessor `RESEARCH-self-improvement-architecture.md` debated **Option A** (unify the runners into one self-improvement daemon) vs **Option B** (keep per-layer runners; close the cross-session skill-mining gap by extending the curator — "variant B1"). It recommended **B**.

What actually shipped is **a third design**, not cleanly either:

- **Unified on the process axis** (Option-A-flavored): one out-of-process daemon, one queue, one housekeeping pass spanning memory and skills, shared `_dream_state.json`. The §4.1/§4.4 arguments against cross-domain coupling (differing lifecycle semantics, failure isolation) were not treated as decisive.
- **Split on the review axis**: the old *combined* `session_review` agent became **two** domain reviewers — more granular than before.
- **Curator deleted, not extended**: B1's premise (extend the curator) is moot. The lifecycle state machine (`active→stale→archived`, `.curator_state.json`, 7-day interval) was removed; skill upkeep is now LLM merge + age-based decay on the daily housekeeping cadence.

Decision is settled — do not re-litigate without new evidence. The one analysis item that outlived the rewrite is the cross-session skill-mining gap (now Gap D, §5.4): the refactor reorganized everything around it but did not close it.

---

## 4. co vs hermes — corrected parity map

hermes-agent markets itself as "the self-improving AI agent" on five claims. Corrected verdicts after the co refactor:

| hermes claim | hermes mechanism | co mechanism (current) | Verdict |
|---|---|---|---|
| #1 "Creates skills from experience" | skill-nudge reviewer (`run_agent.py`) → `skill_manage` | `SKILL_REVIEW_SPEC` → `skill_create/edit/patch` | **PARITY** (mechanism); hermes richer prompt |
| #2 "Improves them during use" | same reviewer, patch-loaded-first | same posture in `skill_review.md` | **PARITY** (mechanism) |
| #3 "Nudges itself to persist knowledge" | memory-nudge reviewer (H1) + skill-nudge reviewer (H2), **separate** | `MEMORY_REVIEW_SPEC` + `SKILL_REVIEW_SPEC`, **separate** | **PARITY in shape** — see note below |
| #4 "Searches its own past conversations" | FTS5 + per-result LLM summarization | FTS5 BM25 chunks, **no summarization** | **GAP A** (§5.1) |
| #5 "Builds a deepening model of who you are" | always-injected `USER.md` + Honcho dialectic backend | flat `kind='user'` memory items, recalled via `memory_search` | **GAP B + GAP C** (§5.2–§5.3) |

Two verdicts the refactor **flipped** vs the 05-18 audit:

- **Claim #3 shape.** The old audit logged co as *combining* memory+skill in one fork vs hermes's two separate reviewers, and called that a co/hermes difference. Co now **splits** them too — the delta is erased; co moved *toward* hermes's shape.
- **Skill curator.** The old audit declared **"PARITY — numbers and behavior align"** on a 7d/30d/90d state machine. Co's state machine is **deleted**; skill upkeep is merge+decay in housekeeping. Co has **diverged** from hermes here — and it is no longer a state machine to compare. (Skill `recall_protection_days=30`, `decay_after_days=90` survive as housekeeping thresholds, not curator transitions.)

> co-unique surfaces (not "self-improving" per se, included for an honest map): **personality doctrine** (soul/mindsets/critique injected statically, author-curated, never learned — `docs/specs/personality.md`); **the housekeeping merge pass** as deeper offline memory hygiene than hermes's per-turn nudge; **atomic-write discipline** on every memory/skill/state write (hermes at parity via `atomic_replace`).

---

## 5. Verified gaps (confirmed against source, 2026-06-14)

### 5.1 Gap A — LLM-summarized cross-session recall

- **hermes:** `session_search` runs FTS5 → query-aware windowing → per-session auxiliary-LLM digest; the agent receives synthesized recaps, not raw chunks.
- **co:** `co_cli/tools/session/recall.py` is strictly BM25. It returns `(session_id, when, source, chunk_text, start_line, end_line, score)` for pairing with `session_view`. The `memory.summarizer.{runs,failures,timed_out}` span attributes are hardcoded to `0`/`False` — the absence is structural, not unwired.
- **Cost:** higher main-agent context spend on long-history queries (model reads chunks or chases `session_view`); a wash for short queries.
- **Status:** confirmed present.

### 5.2 Gap B — always-injected user profile

- **hermes:** dedicated `USER.md`, injected into the system prompt every turn when `user_profile_enabled`; the memory reviewer writes back to it.
- **co:** no `user_profile`/`USER.md`/`format_for_system_prompt` anywhere in `co_cli/`. User facts are flat `kind='user'` memory items recalled via `memory_search`. Doctrine is statically injected but author-curated, not user-learned.
- **Cost:** higher tool-call burden for user-context turns; reliance on the model choosing to search; no canonical "who the user is" surface the reviewer writes back to.
- **Status:** confirmed present.

### 5.3 Gap C — dialectic / backend user-modeling

- **hermes:** Honcho plugin (5 tools, per-turn prefetch, dialectic Q&A); the user model is synthesized backend-side from observed conversation.
- **co:** no honcho/dialectic/peer-card/`MemoryProvider` of any kind. co's model of the user is exactly the union of memory items it chose to save — no second-order behavioral model.
- **Cost:** no cross-session behavioral/contradiction modeling.
- **Status:** confirmed present. (Mission tension: "local, inspectable, reversible" argues against an external backend as default — see §6.)

### 5.4 Gap D — cross-session skill recurrence signal

The one substantive item that survived the architecture rewrite.

- **What's missing:** no component proactively recognizes "this 4-step procedure has recurred across N sessions, make it an umbrella skill." The skill reviewer sees **one** transcript per KICK; skill **merge** in housekeeping clusters only *already-saved* skills (it never reads transcripts).
- **Net behavior:** unchanged from the old design's fallback — per-session reviewer creates near-duplicate skills, housekeeping merge later collapses them. The recurrence signal exists only as after-the-fact dedup, never as up-front recognition.
- **Status:** confirmed present (relocated from curator-consolidation to housekeeping skill-merge, same substance).

---

## 6. Open proposals (live items only)

Trimmed from the prompt-synthesis proposal and the gap-closure sketches; obsolete detail (the `WorkPromptArtifact` schema, the 4-phase rollout, the old module layout) dropped.

### 6.1 Minimum-viable gap closures

- **Gap A** — optional summarization stage on `session_search`: aux-LLM digest per session window, gated behind a config flag (`sessions.summarize_results: bool = False`) with a token cap and single-flight semaphore. Span attributes already exist as no-op placeholders, so the span schema is unchanged. *Small.*
- **Gap B** — a singleton user-profile artifact (e.g. `kind='user_profile'` or a named file), unconditionally injected at static-prompt build in `co_cli/context/assembly.py`, written back by the memory reviewer; gated by a flag to match hermes's off-by-default posture. *Small.*
- **Gap C** — three increasing scopes: (1) embedded periodic "re-derive the profile from recent transcripts" pass (no new dependency); (2) honcho-style plugin parity (adds a network dep — disfavored by mission); (3) a `MemoryProvider` ABC with the embedded option as default (the load-bearing answer only if co ever wants Mem0/Letta/etc.). Prefer (1) unless plugin-able user-modeling becomes a stated goal.
- **Gap D** — give the skill reviewer (or a housekeeping sub-pass) a small recent-session window so it can propose umbrella skills from recurrence *before* duplicates accumulate, rather than relying solely on after-the-fact merge. Reconcile with §6.2 so there is one skill-creation path, not two.

### 6.2 Skill promotion vs the daemon reviewer (reconciliation)

The original proposal to "promote a successful work-prompt into a reusable skill" now **overlaps** the daemon's `SKILL_REVIEW_SPEC`, which already auto-creates and patches skills. Any synthesize-then-promote feature must route through (or explicitly defer to) the reviewer to avoid two competing skill-creation mechanisms with divergent naming/dedup discipline. Retained boundary: draft generation may be automatic; durable save stays explicit and inspectable; no autonomous rewriting of soul/rule/spec files.

### 6.3 Project-local skills tier

Still unbuilt. `co_cli/skills/loader.py` loads only **two** tiers — co-bundled (`co_cli/skills/`) and user-global (`~/.co-cli/skills/`, security-scanned, overriding bundled on name collision). A third **project-local** tier (`.co-cli/skills/`, loaded last) remains the right default for repo-specific generated skills, and is the natural promotion target for any §6.2 work. Adding it touches the loader, the security-scan scope, and the override order (`docs/specs/skills.md`).

---

## 7. Open questions for future research

- **Cross-layer promotion:** when memory review identifies a recurring *procedure* (not a fact), should it hand off to skill creation, or always defer to a skill-side cross-session pass (Gap D)? Needs sessions data.
- **Doctrine evolution:** explicitly out of scope — doctrine is hand-authored canon (`docs/specs/personality.md`). Re-evaluate only if that constraint is lifted.
- **External memory provider:** offloading consolidation/decay to a provider (hermes/Honcho style) would free housekeeping to focus on mining, but adds a dependency and cuts against co's local-self-sufficiency mission. Probably not desirable as default.
- **Daemon "split-brain" framing:** the `feedback_dream_daemon_split_brain` memory note predates the out-of-process refactor; verify it still reads correctly against the current detached-subprocess + file-queue design before citing it.

---

## 8. References

### co-cli source (current)
- `co_cli/daemons/dream/` — `_loop.py`, `process.py`, `_reviewer.py` (`MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`, `process_review`), `_housekeeping.py` (`run_housekeeping`, `merge_memory`, `decay_memory`, `merge_skill`, `decay_skill`), `_queue.py`, `_state.py`, `kick.py`
- `co_cli/daemons/dream/prompts/` — `memory_review.md`, `skill_review.md`, `memory_merge.md`, `skill_merge.md`
- `co_cli/config/dream.py`, `co_cli/config/skills.py`, `co_cli/config/core.py` (DREAM_* path constants)
- `co_cli/main.py` — `_post_turn_hook`, `_maybe_kick_memory_review`, `_maybe_kick_skill_review`, `_fire_session_end_kicks`, `maybe_autospawn_dream`
- `co_cli/bootstrap/core.py` — autospawn flock
- `co_cli/tools/session/recall.py` — BM25 `session_search`; `co_cli/tools/memory/` — `memory_search/create/append/replace/delete`
- `co_cli/skills/loader.py` — two-tier skill loading

### co-cli specs
- `docs/specs/dream.md` — runtime spec of record for the daemon
- `docs/specs/skills.md` — skills tier (curator section pending update to reflect housekeeping absorption)
- `docs/specs/memory.md`, `docs/specs/personality.md`, `docs/specs/prompt-assembly.md`

### hermes cross-reference (context only, as of 2026-05-18)
- `hermes-agent/run_agent.py` — memory/skill nudge reviewers, `_spawn_background_review`, `USER.md` injection
- `hermes-agent/agent/curator.py` — skill curator (state machine, idle-triggered)
- `hermes-agent/tools/session_search_tool.py` — FTS5 + per-session LLM summarization
- `hermes-agent/plugins/memory/honcho/__init__.py` — dialectic backend (5 tools)
