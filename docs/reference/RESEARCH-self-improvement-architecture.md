# RESEARCH — Self-improvement Architecture: Dream Scope and the Per-Layer vs Unified Question

**Date:** 2026-05-18
**Status:** Reference / design research — not normative
**Scope:** Design comparison of two architectural options for co-cli's self-improvement subsystems
**Sources:** `co_cli/skills/session_review.py`, `co_cli/skills/curator.py`, `co_cli/memory/dream.py`, `co_cli/main.py`, `docs/specs/dream.md`, `docs/specs/skills.md`, `docs/specs/memory.md`, `docs/specs/agents.md`. Hermes-side cross-reference: `hermes-agent/run_agent.py`, `hermes-agent/agent/curator.py`, `hermes-agent/cron/scheduler.py`.

## 1. Question

Co-cli today has three distinct background runners that contribute to self-improvement:

1. `session_review` — combined per-session memory + skill harvest
2. `skill_curator` — skill-only lifecycle (state transitions + LLM-driven consolidation)
3. `dream` — memory-only lifecycle (transcript mining + LLM merge + decay)

The architectural question: should `dream` be expanded into a **unified self-improvement daemon** that handles all background self-improvement (option A), or should it stay **memory-only** with `skill_curator` and any future per-domain managers remaining separate (option B, current state)?

This file captures the analysis end-to-end so the decision can be revisited without re-tracing the code.

## 2. Current architecture, source-grounded

### 2.1 Three runners, three roles, three lifecycles

| Runner | Module | Scope | Trigger | Tools (per spec) |
|---|---|---|---|---|
| `session_review` | `co_cli/skills/session_review.py:56` | **memory + skills** | every `review_nudge_interval` turns (post-turn, background task) | `memory_view`, `memory_search`, `memory_manage`, `skill_view`, `skill_manage` |
| `skill_curator` | `co_cli/skills/curator.py:208` | **skills only** | time-gated weekly (post-turn, after session_review) | `skill_view`, `skill_manage` |
| `dream` | `co_cli/memory/dream.py:418` | **memory only** | session teardown, gated on `consolidation_enabled` | (no tools — orchestrator wires miner sub-agent) |

Each runner is wired into `co_cli/main.py`:

- `_post_turn_hook` (`main.py:269`) — bumps the iteration counter; when threshold tripped, spawns `_maybe_run_session_review` as a background task. Single in-flight on `deps.session.background_review_task`.
- `_maybe_run_session_review` (`main.py:191`) — runs the review, then cascades to `_maybe_run_curator` synchronously after the review completes.
- `_maybe_run_curator` (`main.py:243`) — runs the curator if `curator_enabled` is true, model is configured, and the time gate passes (`_curator_gate_passes` at line 227 checks `last_run_at` against `curator_interval_hours`, default 168 hours).
- `_maybe_run_dream_cycle` (`main.py:303`) — fires only on session teardown, gated on `memory.consolidation_enabled` and `memory.consolidation_trigger == "session_end"`.

### 2.2 Spec ownership

Per `docs/specs/agents.md:46`:

```
| `SESSION_REVIEW_SPEC` | `co_cli/skills/session_review.py` | `_maybe_run_session_review` (post-turn) | `run_standalone` |
| `CURATOR_SPEC` | `co_cli/skills/curator.py` | `_maybe_run_curator` (post-turn) | `run_standalone` |
```

The curation rule (`agents.md:50`): "Specs live with the caller that owns the agent's purpose — delegation specs sit alongside their tool wrappers; daemon specs sit alongside their daemon orchestration."

Dream is not modelled as a TaskAgentSpec — `dream.py` builds the miner sub-agent directly (`build_dream_miner_agent`, `dream.py:110`) and uses a non-agent `llm_call` for the merge phase (`_merge_cluster`, `dream.py:305`). Dream owns three sub-passes:

- **Mine** (`_mine_transcripts`, `dream.py:140`): builds a per-session `dream_miner` agent equipped with `memory_manage`, runs it over chunked transcript windows. Cap: `_MAX_MINE_SAVES_PER_SESSION = 5` (line 59).
- **Merge** (`_merge_similar_artifacts`, `dream.py:340`): groups artifacts by `memory_kind`, clusters by token-Jaccard (`_cluster_by_similarity`, line 235), invokes `llm_call` with the merge prompt for each cluster, writes a consolidated artifact, archives originals. Caps: `_MAX_MERGES_PER_CYCLE = 10`, `_MAX_CLUSTER_SIZE = 5`, `_MERGED_BODY_MIN_CHARS = 20`.
- **Decay** (`_decay_sweep`, `dream.py:372`): pure code (no LLM), archives candidates older than `memory.decay_after_days`. Cap: `_MAX_DECAY_PER_CYCLE = 20`.

The whole cycle runs under `asyncio.timeout(_DREAM_CYCLE_TIMEOUT_SECS=60)` (line 60). Each phase is independently try/except'd (`run_dream_cycle`, lines 439-455). Cross-cycle state in `~/.co-cli/memory/_dream_state.json` (lines 80-102): `DreamState(last_dream_at, processed_sessions, stats)`.

### 2.3 Triggering cadence — actual values

From `co_cli/config/skills.py` (referenced from `docs/specs/skills.md:282`):

| Setting | Default |
|---|---|
| `skills.review_enabled` | `false` |
| `skills.review_nudge_interval` | `5` tool calls |
| `skills.curator_enabled` | `false` |
| `skills.curator_interval_hours` | `168` (7 days) |
| `REVIEW_MAX_ITERATIONS` | `8` |
| `REVIEW_TIMEOUT_SECONDS` | `120` |
| `CURATOR_MAX_ITERATIONS` | `100` |
| `CURATOR_TIMEOUT_SECONDS` | `600` |
| `CURATOR_STALE_AFTER_DAYS` | `30` |
| `CURATOR_ARCHIVE_AFTER_DAYS` | `90` |

From `docs/specs/memory.md:69`:

| Setting | Default |
|---|---|
| `memory.consolidation_enabled` | `false` |
| `memory.consolidation_trigger` | `session_end` |
| `memory.consolidation_lookback_sessions` | `5` |
| `memory.consolidation_similarity_threshold` | `0.75` |
| `memory.decay_after_days` | `90` |

The runners have **wildly different cadences**: session_review every ~5 tool calls (minutes), curator every 168 hours (week), dream once per session teardown. All three are off by default — opt-in via env vars.

### 2.4 Per-run artifacts on disk

| Runner | Output dir | Files |
|---|---|---|
| `session_review` | `~/.co-cli/session-reviews/<ts>-<run_id_suffix>/` | `run.json`, `run.md` |
| `skill_curator` | `~/.co-cli/curator-runs/<ts>-<run_id_suffix>/` | `run.json`, `run.md` |
| `dream` | none (state in `~/.co-cli/memory/_dream_state.json`) | none — phase results returned in `DreamResult` and logged |

Session review report fields (`session_review.py:97-105`): `summary, skills_patched, skills_created, knowledge_created, knowledge_updated, transcript_length, usage`.

Curator report fields (`curator.py:264-269`): `summary, skills_merged, skills_created, skills_updated, usage`.

### 2.5 Prompt scope

`SESSION_REVIEW_INSTRUCTIONS` (`co_cli/skills/session_review_prompts.py:5`) explicitly tells the agent it is a "skill and knowledge maintainer." Scope blocks at lines 13-17:

> Scope:
> - Skills: procedural knowledge (how to do tasks). Update loaded skills that had stale steps, create new skills for multi-step procedures that succeeded and are likely to recur.
> - Knowledge: user preferences, corrections, rules, decisions. Create or update memory items for anything the user explicitly corrected or that reflects a reusable insight.

Forbids: `skill_manage(action='delete')`, `memory_manage(action='delete')`, session-specific skills, duplicates (lines 18-21).

`CURATOR_INSTRUCTIONS` (referenced via `_curator_instructions`, `curator.py:202`) and dream miner prompt (`co_cli/memory/prompts/dream_miner.md`) are each scoped to their domain.

The dream miner prompt at line 7-14 lists artifact kinds: `preference`, `feedback`, `rule`, `reference` — same vocabulary as the per-turn memory extractor. It is explicitly retrospective: "looks for cross-turn patterns, implicit preferences, corrections that only made sense several turns later, stable decisions."

### 2.6 Layer mapping summary

```
                    PER-SESSION HARVEST              PER-LAYER LIFECYCLE
                    (every N turns)                   (low-frequency)
                    -------------------               -----------------------
SKILLS    →  session_review (skill_*) ──cascade──→  skill_curator (state machine + LLM consolidation)
MEMORY    →  session_review (memory_*)              dream (mine + merge + decay)
DOCTRINE                       — none —                         — none —
```

`session_review` is the **cross-layer harvester**; `skill_curator` and `dream` are **per-layer lifecycle managers** at different cadences, with different semantics, on different state.

## 3. The two design options

### Option A — Unified self-improvement daemon

Reshape `dream` (or invent a new top-level `co_cli/cognition/` package) into a single background daemon that owns:

- Cross-session mining for both knowledge AND skills (currently only knowledge is mined cross-session)
- Memory consolidation and decay (currently dream's mine/merge/decay)
- Skill lifecycle transitions and consolidation (currently `skill_curator`)
- Future expansion: doctrine evolution, tool preference learning, conversation pattern recognition

One agent, one transcript-read, multiple writes. Branching prompts per phase or per domain. `skill_curator` deprecated and absorbed.

### Option B — Per-layer subsystems (current state)

Keep the current split:

- `co_cli/memory/dream.py` owns memory lifecycle (mine + merge + decay)
- `co_cli/skills/curator.py` owns skill lifecycle (transitions + consolidation)
- `co_cli/skills/session_review.py` is the per-session cross-layer harvester
- Any new domain (doctrine, tool prefs) adds its own subsystem next to its capability layer

## 4. Detailed comparison

### 4.1 Lifecycle semantics differ

The memory and skill lifecycles share rough shape (extract → consolidate → archive) but differ in every concrete detail:

| Dimension | Memory (dream) | Skills (curator) |
|---|---|---|
| Identification of consolidation candidates | Same-`memory_kind` clustering by token-Jaccard ≥ 0.75 (`dream.py:235-264, 318-336`) | LLM-driven prefix/topic clustering — no static similarity threshold |
| Consolidation engine | Single `llm_call` (no tools) per cluster, body-only (`dream.py:305-315`) | Tool-using agent with `skill_view` + `skill_manage`, multi-step (`curator.py:341-360`) |
| Cluster cap | 10 clusters × 5 members (`dream.py:55-56`) | n/a — agent decides |
| Output validation | Min length 20 chars; reject empty (`dream.py:309-313`) | Structured `CuratorOutput` BaseModel with field validation |
| Decay trigger | Age + last-recall cutoff (`dream.py:372-378`, via `find_decay_candidates`) | Idle-time state machine `active → stale → archived` (`curator.py:59-118`) |
| Decay execution | Archive top N candidates per cycle (cap 20) | Per-skill threshold crossings; transitions emitted as `StateTransition` records |
| Restore | `memory/_archive/` filesystem move | `user_skills_dir/.archive/` move + sidecar state revert |
| Immunity flag | `MemoryItem.decay_protected` boolean (`dream.py:231`) | `pinned: true` in usage sidecar (`curator.py:84-87`) |
| State persistence | `DreamState` (Pydantic) at `memory/_dream_state.json` | `dict` at `user_skills_dir/.curator_state.json` |

These are not surface-level differences. Forcing them into one runner means a lot of `if domain == "memory" ... elif domain == "skills"` inside what is meant to be a unified daemon.

### 4.2 Cadence differs by an order of magnitude

| Mechanism | Effective frequency |
|---|---|
| `session_review` | every ~5 tool calls — multiple per session, minute-scale |
| `dream` | once per session at most — hour-scale |
| `curator` | once per 168 hours minimum — week-scale |

A unified daemon either runs on the slowest cadence (loses memory consolidation responsiveness) or runs on every trigger (wastes work on the larger-cadence domain) or branches internally (back to per-domain logic, just in one file).

### 4.3 Spec/doc structure favors per-layer ownership

Co-cli's spec topology in `docs/specs/`:

- `memory.md` — owns memory tier, references dream.md for lifecycle
- `dream.md` — owns dream lifecycle, sits under memory
- `skills.md` — owns skills tier including curator subsection (§ "Curation & Self-Improvement", lines 234-278)
- `agents.md` — owns spec/runner ontology

Option B aligns: each capability layer's spec doc owns its lifecycle. Option A would require either:

- Moving curator content out of `skills.md` into expanded `dream.md` (cross-layer spec, violates the existing layout)
- Or introducing a new `cognition.md` / `self_improvement.md` that subsumes both — multiple-source-of-truth risk

CLAUDE.md memory item `feedback_no_util_modules.md`: "no util/helpers modules; cross-package imports from memory are intentional public surface." The same philosophy says no shared "self-improvement utility" daemon — each layer owns itself.

### 4.4 Failure isolation

Option B has natural blast-radius limits. If `skill_curator` is buggy or the curator agent's LLM call hangs, dream still runs and memory still gets consolidated. Memory failures don't taint skills.

Option A has one composite failure surface. The current `dream.run_dream_cycle` already isolates phases (`dream.py:439-455`), but those are sub-phases of one domain. Cross-domain isolation in a unified runner means deeper branching.

`asyncio.timeout` is already separate per runner: 60s for dream, 120s for session_review, 600s for curator. A unified runner needs a single ceiling that satisfies the slowest domain (600s) but starves memory consolidation if memory's mine+merge gets stuck behind a slow skill consolidation pass.

### 4.5 Hermes precedent

Hermes ships parallel three-runner architecture (`hermes-agent/run_agent.py:3500-3520` background review + `hermes-agent/agent/curator.py` weekly curator + external memory provider for memory). Of 83 bundled hermes skills, **only 2 use the phase-structured authoring contract**; the rest are free-form. Hermes background review fires every 10 turns (default); curator fires weekly when idle. Memory consolidation is delegated to an **external provider** — hermes itself does not own memory lifecycle code.

This is closer to Option B than Option A:

- Cross-layer harvester (background review) — analogous to co's `session_review`
- Skill-only weekly lifecycle (curator) — analogous to co's `skill_curator`
- Memory lifecycle externalized — co keeps it in-process via `dream`, but it is still a separate runner

Hermes has not unified these despite shipping at scale. The unification temptation has not been worth paying in their codebase.

### 4.6 Shared transcript-read cost

The strongest practical argument for Option A: both dream and any cross-session skill mining want to read recent session transcripts. Option B duplicates that read (or requires shared infrastructure).

Reality check:

- Dream loads transcripts via `load_transcript(session_path)` (`dream.py:165`) on session_end only — at most once per session, at most `consolidation_lookback_sessions=5` transcripts read.
- Session_review reads the **in-memory message_history**, not transcript files (`run_session_review(deps, message_history)`, `session_review.py:138`). No file I/O, no duplication.
- `skill_curator` reads the **skill inventory** (`_summarize_skill_inventory`, `curator.py:218-244`), not transcripts.

Today's runners do not duplicate transcript reads. The shared-read argument for Option A is hypothetical (would require *adding* cross-session skill mining to curator, which doesn't exist yet).

### 4.7 Cross-layer signal — the real gap

Option B's only honest cost: cross-session skill mining is **not done by anyone**. The matrix:

| Domain | Per-session extraction | Cross-session mining | Consolidation | Decay |
|---|---|---|---|---|
| Memory | session_review (in-memory) | **dream mine** (`_mine_transcripts`) | dream merge | dream decay |
| Skills | session_review (in-memory) | **— missing —** | curator consolidation | curator archive |

Concrete consequence: a user runs 7 sessions in a week, each containing a similar 4-step procedure. Each session, `session_review` sees only that session and creates a skill. After the week, the user has 7 near-duplicate skills. Curator's consolidation can later collapse them, but **the original "recognize this is a recurring procedure" signal exists only at the cross-session level**, and only dream sees that level today — and dream looks at memory items, not at session transcripts for skill patterns.

Option A naturally fills this. Option B has two sub-options:

- **B1.** Extend `skill_curator`'s prompt to include "scan recent session transcripts and propose new umbrella skills from recurring procedures." Add `recent_sessions` to its input alongside the existing skill inventory. The curator becomes a miner + consolidator instead of a consolidator-only. Estimated cost: ~50 LOC in `curator.py` + prompt extension. Spec note in `skills.md` §Curation.
- **B2.** Add a third skill mechanism — `skill_miner` parallel to `dream` but for skills. Worst of both worlds: more infrastructure, more configs, more reports.

**B1 is the right closure** of the gap. The curator already runs an LLM pass with a 100-iteration budget (`CURATOR_MAX_ITERATIONS=100`, `CURATOR_TIMEOUT_SECONDS=600`); asking it to do cross-session pattern recognition before consolidation is a natural extension, not a new responsibility.

### 4.8 Naming and metaphor

"Dream" is a strong, specific metaphor: sleep-time consolidation of declarative memory. The neuroscience parallel is precise (slow-wave sleep, hippocampal replay → cortical consolidation). Repurposing "dream" to cover skill umbrella-building, lifecycle archival, doctrine evolution, etc. dilutes the metaphor.

"Curator" is equally specific: librarian for a skill collection. "Dream covers skill curation" reads wrong.

Option A also forces a new top-level name (`co_cli/cognition/`? `co_cli/self_improvement/`?) — and any single name for "everything self-improvement" will be either vague or unimaginative.

### 4.9 Future expansion

Option A's expansion argument: if you anticipate adding 3+ new self-improvement domains, a uniform shell is cheaper than N parallel subsystems.

Realistic expansion candidates for co-cli:

| Candidate | Likelihood | Where it belongs in B |
|---|---|---|
| Doctrine evolution | Very low — explicitly out of scope per `personality.md` (doctrine is hand-authored canon) | Wouldn't be added |
| Prompt self-editing | Very low — hardcoded prompts are governance boundaries | Wouldn't be added |
| Tool preference learning | Plausible | Lives next to `tools.md` — new `tool_curator` if ever |
| Conversation pattern recognition | Plausible | Could fold into `session_review` or `dream` mining |
| Skill discovery from external sources (hub) | Possible | Lives in `skills/` — new `skill_hub` subsystem |

The 3+ unknowns to motivate a unified shell aren't there. Option B's per-layer pattern handles the plausible candidates locally.

## 5. Recommendation

**Option B (per-layer subsystems) is the right design**, with the cross-session skill mining gap closed by extending `skill_curator` (variant B1, §4.7) rather than collapsing the runners.

Reasons, ranked:

1. **Lifecycle semantics differ at every dimension** (§4.1). Memory merge ≠ skill umbrella. Forcing one runner means branching everywhere — the apparent simplicity of "one daemon" is a façade over interior complexity.
2. **Cadences differ by order of magnitude** (§4.2). 5 turns / 1 session / 1 week. A unified runner either runs too often or too rarely for some domain.
3. **Spec topology already encodes per-layer ownership** (§4.3). Each capability layer's spec owns its lifecycle. Disrupting this is a doc rewrite cost, not a code cost.
4. **Failure isolation** (§4.4). Per-runner timeouts, per-runner `try/except`, per-runner forked deps. Cross-domain bugs stay local.
5. **Hermes ships parallel three-runner architecture at scale** (§4.5) — the unification temptation hasn't been worth paying in the reference codebase either.
6. **The shared-read cost doesn't exist today** (§4.6). `session_review` reads in-memory history, `curator` reads inventory, `dream` reads transcripts. No duplication.
7. **The cross-session skill mining gap is local and small** (§4.7). It can be closed in `skill_curator` with ~50 LOC, not a re-architecture.
8. **Metaphor clarity** (§4.8). "Dream covers everything" reads worse than "dream consolidates memory, curator tends skills."
9. **Expansion candidates don't motivate a unified shell** (§4.9).

The one architectural advantage Option A genuinely has — cross-layer promotion ("this knowledge artifact should become a skill") — is real, but rare enough that on-demand promotion via `session_review` (which already has both `memory_manage` and `skill_manage` in its toolset) is sufficient. If cross-layer promotion ever becomes the dominant pattern, revisit Option A; today it does not.

## 6. Implementation cost — if a switch were chosen

### Cost of Option B + cross-session mining (recommended path)

| Change | LOC | Files |
|---|---|---|
| Extend `_summarize_skill_inventory` to include `recent_sessions` window | ~20 | `curator.py` |
| Extend `CURATOR_INSTRUCTIONS` prompt: add "before consolidating, scan recent session transcripts and propose new umbrella skills for recurring procedures" | ~10 prompt text | `curator_prompts.py` |
| Extend `CuratorOutput` to track `skills_proposed_from_sessions: list[str]` | ~3 | `curator.py` |
| Spec update in `skills.md` § Curation describing the new responsibility | ~10 lines | `docs/specs/skills.md` |
| Tests covering cross-session mining behavior in curator | ~50 | `tests/test_flow_skill_curator.py` (new) |
| **Total** | **~100 LOC + 60 lines of spec/tests** | **5 files** |

### Cost of Option A (pivot to unified)

| Change | LOC | Files |
|---|---|---|
| Merge `curator.py` state-machine logic + `dream.py` mine/merge/decay into a unified runner | ~600 LOC moved | new `co_cli/cognition/` or expanded `co_cli/memory/dream.py` |
| Build a multi-domain TaskAgentSpec with conditional toolset/prompt selection | ~150 LOC | new `cognition_spec.py` |
| Rewire `main.py` `_post_turn_hook` + `_maybe_run_session_review` cascade | ~50 LOC | `main.py` |
| Spec consolidation: move skill curator content from `skills.md` § Curation into expanded `dream.md`, update cross-references in `memory.md`, `skills.md`, `agents.md` | ~150 lines | 4 spec files |
| Delete `curator.py`, `curator_prompts.py`, `agents/skill_curator.py` (if standalone) — but state machine and archive/restore must remain accessible | tricky — pure-code helpers stay but caller relocates | several |
| Migration of `~/.co-cli/skills/.curator_state.json` and `~/.co-cli/curator-runs/` paths if renamed | tooling + data migration | infra |
| Tests rewritten for unified runner | ~300 LOC | tests |
| **Total** | **~1100+ LOC moved/rewritten + ~150 lines of spec rewrites + data migration** | **~12 files** |

Cost-benefit clearly favors Option B + B1.

## 7. Decision recorded

The recommendation in this file is for review by TL/PO. If accepted, the followup is:

- A separate exec-plan to implement variant B1 (cross-session skill mining in `skill_curator`).
- No change to `dream` scope.
- No change to `session_review` scope.
- Spec update in `skills.md` § "Curation & Self-Improvement" to describe the new curator responsibility.

If rejected (i.e., the team prefers Option A), the followup is a much larger exec-plan covering the migration in §6. This file should be retained either way as the rationale record so the question is not re-litigated without new evidence.

## 8. Open questions for future research

- **Cross-layer promotion**: when knowledge mining identifies a recurring *procedure* (not just a fact), should `dream` propose a skill creation via the curator's queue, or always defer to the curator's own cross-session pass? Empirical answer requires sessions data.
- **`dream` for sessions tier**: should there be a "session dream" that mines very old transcripts for cross-session-aggregate patterns the per-session reviewer missed? Today there is no session-tier maintenance beyond append-only writes.
- **Doctrine evolution**: explicitly out of scope today. Re-evaluate only if `personality.md` removes its hand-authored-canon constraint.
- **External memory provider plug-in (hermes-style)**: would offloading memory consolidation/decay to an external provider (like hermes) free dream to focus purely on cross-session mining? Adds dependency, removes local self-sufficiency. Probably not desirable given co-cli's "local, trusted, personal" mission (per `dream.md:10-16`).

## 9. References

### co-cli source
- `co_cli/skills/session_review.py` — `SESSION_REVIEW_SPEC`, `run_session_review`
- `co_cli/skills/session_review_prompts.py` — `SESSION_REVIEW_INSTRUCTIONS`, `SESSION_REVIEW_PROMPT`
- `co_cli/skills/curator.py` — `CURATOR_SPEC`, `run_curator`, `apply_state_transitions`, `archive_skill`, `restore_skill`
- `co_cli/skills/curator_prompts.py` — `CURATOR_INSTRUCTIONS`, `CURATOR_PROMPT`
- `co_cli/memory/dream.py` — `run_dream_cycle`, `_mine_transcripts`, `_merge_similar_artifacts`, `_decay_sweep`
- `co_cli/memory/prompts/dream_miner.md`, `co_cli/memory/prompts/dream_merge.md`
- `co_cli/main.py:150-320` — wiring of `_post_turn_hook`, `_maybe_run_session_review`, `_maybe_run_curator`, `_maybe_run_dream_cycle`

### co-cli specs
- `docs/specs/agents.md` — spec/runner ontology, curation rule
- `docs/specs/skills.md` § "Curation & Self-Improvement" (lines 234-278)
- `docs/specs/memory.md`
- `docs/specs/dream.md`

### hermes cross-reference (context only)
- `hermes-agent/run_agent.py:3237-3565` — background review prompts and spawn (`_MEMORY_REVIEW_PROMPT`, `_SKILL_REVIEW_PROMPT`, `_COMBINED_REVIEW_PROMPT`, `_spawn_background_review`)
- `hermes-agent/agent/curator.py` — 869 LOC skill curator (state machine + LLM consolidation, idle-triggered)
- `hermes-agent/hermes_cli/curator.py` — CLI controls
- `hermes-agent/cron/scheduler.py:677-716` — `context_from` chain primitive
- `hermes-agent/tools/skill_usage.py` — usage sidecar
