# inline-curation-discipline

## Problem

Co's memory grows in volume — articles fetched, sessions archived, recall counts incremented — but **volume is not understanding**. Articles sit as raw substrate until they decay. Understanding only accumulates when the agent distills raw content into derivative artifacts (`kind=note`, `kind=rule`).

The agent *can* write notes (`memory_manage(create, kind=note)` exists) but nothing disciplines it to — growth in understanding is opportunistic and rare. The existing memory reviewer (Plan 1) extracts durable facts at session end; the existing skill reviewer extracts procedural updates. Together those mine each session's transcript for generalizable residue. What is missing is an **inline** curation discipline the agent applies *during* research — distilling articles into notes while context is hot, correcting recalled items the user contradicts, and replacing items that have drifted.

This plan owns one curation surface plus two source_type fixups:

1. **Inline agent-driven curation** — a numbered doctrine rule disciplining promotion (article → note/rule), correction (replace on contradiction), and drift (replace/delete on staleness).
2. **`SESSION_REVIEW` source_type wiring** — the enum is declared but never consumed; the memory reviewer should tag its saves so the source-type contract is honest.
3. **`DETECTED` source_type cleanup** — vestigial enum value with zero writers and zero readers; default is silently `DETECTED` so every agent create lands mislabeled. Default → `MANUAL`; enum value removed.

## Position

**Memory growth = raw-substrate accumulation + agent-curated derivative artifacts, driven by usage signal.**

The substrate accumulates passively (fetch → persist → index). The derivative tier accumulates only through deliberate agent curation. Without an inline curation discipline, the agent's memory is a search index, not a learning system.

Sessions are workspaces where curation happens, not memory artifacts in their own right — their boundaries are user-defined and arbitrary (lunch, Ctrl+C, task-switch, context-limit). Session content is mined by `memory_review` (durable facts) and `skill_review` (procedural updates); the session itself is not promoted to a first-class memory object.

## Growth pipeline — end-to-end

```
                            ┌────────────────────────────┐
   web_fetch ──────────────▶│ kind=article (raw)         │──┐
                            │  source_type=web_fetch     │  │
                            └────────────────────────────┘  │
                                                            ▼
                              ┌──────────────────────────────────┐
   chunk + embed + index ────▶│ FTS5 BM25 + sqlite-vec + RRF +   │
                              │ optional reranker                │
                              └──────────────────────────────────┘
                                                            │
                              ┌─────────────────────────────┘
                              ▼
   memory_search ─────────▶ recall_count++, last_recalled_at, recall_days
                              │
                              │  (usage signal)
                              ▼
              ┌───────────────────────────────────┐
              │ INLINE AGENT CURATION             │
              │ ─ promote: article → note/rule    │
              │ ─ correct: replace on contradict- │ ◀──── this plan owns
              │   ion                             │       (T2 — doctrine rule)
              │ ─ drift:   replace/delete on      │
              │   staleness                       │
              │ (memory_manage create / replace)  │
              └───────────────────────────────────┘
                              │
   session_end ──────────────▶│
                              ▼
              ┌───────────────────────────────────┐
              │ DAEMON SESSION-END WORK           │
              │ ─ memory_review (Plan 1, shipped) │
              │   extracts durable facts → tags   │  ◀──── this plan owns
              │   source_type=session_review (T3) │       (T3 — prompt wiring)
              │ ─ skill_review (Plan 1, shipped)  │
              │   extracts procedural updates     │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │ dream merge (note/rule only;      │
              │   articles excluded) — shipped    │  ◀── Plan 2a (shipped)
              │ dream decay (all kinds, recall-   │
              │   protected) — shipped            │
              └───────────────────────────────────┘
                              │
                              ▼
                       hybrid retrieval over union of articles +
                       notes + rules → informs future agent turns
```

## Components

| # | Component | Owner | Status |
|---|---|---|---|
| 1 | Fetch — `web_fetch` returns content | existing tool | shipped |
| 2 | Persist — `memory_manage(create, kind=article)` | existing tool | shipped |
| 3 | Chunk + embed + index — `co_cli/index/` | existing infra | shipped |
| 4 | Recall signal — `recall_count` / `last_recalled_at` / `recall_days` populated by `memory_search` | Plan 1 | shipped |
| 5 | Dream merge with article exclusion | Plan 2a | shipped |
| 6 | Dream decay with recall protection | Plan 2a | shipped |
| 7 | Hybrid retrieval over all kinds | `co_cli/index/` | shipped |
| 8 | Session-end review producer — `_fire_session_end_kicks` writes `domain=memory` + `domain=skill` queue items | Plan 1 + Plan 1.5 | shipped |
| 9 | **Inline curation discipline — promote / correct / drift** | **this plan (T2)** | **open** |
| 10 | **Source_type cleanup — default `manual`, remove `detected`** | **this plan (T2)** | **open** |
| 11 | **`SESSION_REVIEW` source_type wiring — memory reviewer tags its saves** | **this plan (T3)** | **open** |
| 12 | End-to-end audit | this plan (T1) | unblocked |
| 13 | Position spec in `docs/specs/memory.md` | this plan (T4) | open |

## Current state — verified against source

### Memory model
- `co_cli/memory/item.py:24-29` — `MemoryKindEnum = USER | RULE | ARTICLE | NOTE | CANON`. The agent-callable surface is `MemoryKind = Literal[USER, RULE, ARTICLE, NOTE]` (line 32-37); CANON is doctrine, never agent-curated.
- `co_cli/memory/item.py:40-47` — `SourceTypeEnum = DETECTED | WEB_FETCH | MANUAL | OBSIDIAN | DRIVE | CONSOLIDATED | SESSION_REVIEW`. Two problems (both fixed in T2/T3):
  - `SESSION_REVIEW` is declared but never consumed by any writer — T3 wires the existing reviewer to tag its saves.
  - `DETECTED` is the **default** in `save_memory_item` (`service.py:143`), so every agent-initiated `memory_manage(create)` lands as `source_type="detected"`. Original meaning ("background reviewer detected this pattern") no longer matches the de-facto writer (agent deliberately curating). Vestigial from the `provenance→source_type` rename in plan-1-memory-surface-unification — no explicit writers, zero readers.
- `co_cli/memory/item.py:74-76` — `last_recalled_at`, `recall_count`, `recall_days` populated by recall tracking
- `co_cli/memory/decay.py:21` — recall-protected decay applies uniformly to all kinds
- `co_cli/daemons/dream/_housekeeping.py:87-104` — `_identify_mergeable_clusters` already skips `kind=article` (RAG-integrity rationale: substrate is not fusable).

### Tool surface
- `co_cli/tools/memory/manage.py:62` — `memory_manage(action='create', kind='article'|'note'|'rule')` is the persistence path
- `co_cli/tools/memory/recall.py:70` — `memory_search` searches `[user, rule, article, note]` by default
- `co_cli/tools/web/fetch.py:116` — `web_fetch` returns content; persistence is a separate explicit agent decision

### Daemon — current architecture (post Plan 1.5)
- `co_cli/daemons/dream/_queue.py` — file-based queue at `DREAM_QUEUE_DIR/*.json`; payload is `{domain, session_id, persisted_message_count, attempts}`
- `co_cli/daemons/dream/_loop.py:96-126` — `main_loop` reads queue files, dispatches via `_process_review(deps, domain, session_id, persisted_message_count)`
- `co_cli/daemons/dream/_reviewer.py:118-141` — `process_review` routes by `domain` to `_run_memory_review` or `_run_skill_review`; raises `ValueError` on unknown domain
- `co_cli/daemons/dream/_loop.py:57-67` — housekeeping runs on the **idle branch** (queue empty), not as a queue payload
- `co_cli/main.py:179-192` — `_fire_session_end_kicks` already fires unconditional `domain=memory` and `domain=skill` kicks at session end via `_send_review_kick`

## Resolved decisions

### Where does the inline curation discipline live? → numbered doctrine rule

`co_cli/context/rules/07_memory_protocol.md` — soul-agnostic, universal capability discipline. Mirrors the `06_skill_protocol.md` precedent. Lightest mechanism; no new code path; the static prompt assembly already loads numbered rules.

Rejected: session-review prompt addition (weaker signal — only fires on review cadence); dedicated skill (heaviest, lowest discoverability).

### When does promotion happen? → inline during research

Inline distillation while the article is fresh in context. Best signal-to-noise, highest context fidelity.

Rejected: end-of-session sweep (context already degraded); dream-cycle promotion (decouples LLM-mediated transformation from agent's lived context — explicitly out of scope).

### What gates promotion? → agent judgment

The agent decides what's worth distilling. Recall-count gating has a chicken-and-egg problem (the agent can't recall what it hasn't distilled). User signals don't scale. Default-everything dilutes the note tier into article duplicates.

### Are sessions first-class memory objects? → no

Session boundaries are user-defined and arbitrary (lunch, Ctrl+C, task-switch, context-limit) — not semantic. Pre-computing a recap of an arbitrary chunk produces an artifact whose value is bounded by the arbitrariness of its scope. Memory grows through *generalization* (memory_review extracts durable facts) and *procedural extraction* (skill_review extracts skill updates) — both operate over transcript content regardless of where any one session ends.

A "session arc" recap was considered (and originally drafted as T3 of this plan) and rejected on this principle. Cheaper alternatives if session-browsing UX becomes a real complaint: smarter `_extract_title` heuristic (zero LLM cost), or lazy on-browse summarization. Neither belongs in this plan.

### Why no inline-curation effectiveness measurement?

Doctrine-level instruction relies on the agent internalizing the rule. Measurement is deliberately out of scope here — the discipline ships first, instrumentation follows if the doctrine fails. After ~20 real sessions, if promotion still feels rare, file a follow-up plan to instrument note-creation / replace-call counts and consider escalating to a stronger mechanism.

## Dependencies

All blockers shipped:

- Plan 1 (`completed/2026-05-20-010811-plan1-online-reviewer-and-daemon-mvp.md`) — daemon process, queue, session-end producer, memory reviewer
- Plan 1.5 (`completed/2026-05-22-105500-plan1.5-dream-daemon-decouple.md`) — file-based queue model
- Plan 2a (`completed/2026-05-20-010811-plan2a-dream-housekeeping.md`) — article-skip merge, recall-protected decay
- Plan 2b (`completed/2026-05-22-104835-plan2b-skill-lifecycle-absorption.md`) — skill housekeeping consolidated

**Recommended ship order:** T1 → T2 + T3 (parallel) → T4.

- T1 first: validates the Plan 2a substrate before doctrine lands on top. Failures route into Plan 2a, not T2.
- T2 parallel with T3: T2 touches rules-file + a one-line default + one enum value; T3 touches one prompt file. No code overlap.
- T4 last: documents the shipped state, including the post-T2 source_type taxonomy.

## Tasks

- [x] ✓ DONE **T1.** End-to-end audit.
  - Persist a representative article via `memory_manage(create, kind=article)`
  - Confirm chunk + embed + index lands in `co_cli/index/`
  - Recall via `memory_search`; verify `recall_count` increments and `last_recalled_at` updates
  - Place two near-duplicate articles + two near-duplicate notes; trigger `merge_memory`; confirm merge skips articles, merges notes
  - Persist an article older than `decay_after_days` with zero recall; trigger `decay_memory`; confirm article archived
  - Persist an article with recall within `recall_protection_days`; trigger `decay_memory`; confirm article protected
  - Write audit notes; if any step fails, file the fix into the relevant shipped plan (not this one)
  - **PASS criterion:** all 6 verifications below observed in a single audit run; partial pass → write up which step(s) failed, file a fix-task against the owning plan, do not mark T1 done.
    1. Persist: `memory_manage(create, kind=article)` writes a file under `memory_dir/`.
    2. Index: chunks for that file appear in the FTS5 index (visible via `memory_search` returning a hit on a body-unique phrase).
    3. Recall signal: a second `memory_search` hit increments `recall_count` and updates `last_recalled_at`.
    4. Merge discrimination: with two near-duplicate articles + two near-duplicate notes, `merge_memory` collapses the notes and leaves the articles intact.
    5. Decay (unprotected): an article older than `decay_after_days` with zero recall is archived by `decay_memory`.
    6. Decay (protected): an article with a recent recall inside `recall_protection_days` is left in place by `decay_memory`.
  - **Tests:** audit is observational; no new test files. If a bug is found, the test for it lands with the fix in the owning plan.

- [x] ✓ DONE **T2.** Implement the inline curation discipline + truth up the source_type surface.
  - Create `co_cli/context/rules/07_memory_protocol.md`. Migrate the existing `## Memory` section from `04_tool_protocol.md` (Recall / Explicit saves / Kind selection / Anti-patterns).
  - **Fix the stale kind-selection table during migration.** The current table in `04_tool_protocol.md` lists `preference / feedback / decision / reference` as memory kinds — none of which are in `MemoryKindEnum`. The agent-callable surface is exactly four kinds (CANON is doctrine, not agent-curated). Use this mapping:

    | User intent | kind |
    |---|---|
    | Stable personal preference / "I prefer X" | `user` |
    | Forward-acting standing rule / "always / never / stop" | `rule` |
    | Web article / fetched substrate | `article` |
    | Free-form note / distilled finding / recorded decision / saved URL | `note` |

    Old labels collapse: `preference→user`, `feedback→rule`, `decision→note`, `reference→note` (or `article` if URL-bearing and large enough to warrant the substrate tier).

    **Why `feedback→rule`:** the legacy label meant "behavioral correction the user issued" ("stop summarizing", "don't mock the DB"). In the four-kind surface, those are forward-acting standing constraints — exactly what `rule` represents. A correction *is* a rule once internalized; the legacy split between "correction-as-history" and "rule-as-instruction" was a distinction without a behavioral difference.

  - Add new `### Curation` section covering:
    - **Promotion**: when research yields a useful finding, distill into a `kind=note` (or `kind=rule` when high-confidence) alongside saving the raw `kind=article`. The article is the substrate; the note is what the future self reasons over.
    - **Correction**: when the user states something that contradicts a recalled memory item, propose `memory_manage(action='replace')` on that item before continuing. Don't silently override; surface the change.
    - **Drift**: when a recalled note has visibly drifted from current truth (cited URL stale, named tool replaced), propose `replace` or `delete` rather than working around it.
    - **Dedup awareness**: `memory_manage(action='create')` dedups against existing items of the same kind. Read `SaveResult.action` on the return:
      - `saved` — new file written.
      - `skipped` — a near-duplicate already exists; nothing written. The existing note already carries the finding; move on.
      - `merged` / `appended` — your content was folded into the existing item; it now also carries your additions.
      Don't fight the dedup by retrying with slight rephrasings.
  - Adopt value-criterion framing in the intro: "Prioritize what reduces future user steering — the most valuable memory is one that prevents the user from having to correct or remind you again."
  - Remove `## Memory` section from `04_tool_protocol.md`; leave a one-line cross-reference (`See 07_memory_protocol.md`).
  - **Source_type cleanup** (same task because the doctrine T2 writes would otherwise lie about source_type semantics):
    - Change the default in `co_cli/memory/service.py:143` from `SourceTypeEnum.DETECTED.value` to `SourceTypeEnum.MANUAL.value`. Agent-initiated `memory_manage(create)` saves are inherently manual; the default should reflect that.
    - Remove `DETECTED = "detected"` from `SourceTypeEnum` in `co_cli/memory/item.py:41`. Zero writers (after the default change), zero readers, vestigial from the `provenance→source_type` rename. Per the zero-backward-compat invariant, no alias.
    - Grep verification: `rg "DETECTED|\"detected\"|'detected'"` after the change should return zero hits in `co_cli/` (matches in `docs/exec-plans/completed/` are historical and stay put).
    - **Loader tolerance:** `MemoryItem` types `source_type: str | None` populated by `frontmatter.get` (`item.py:71,91`) — existing on-disk files with `source_type: detected` load fine post-removal as off-enum strings. No migration needed.
    - **Web articles unaffected:** the `source_url is not None` branch in `save_memory_item` (`service.py:156-219`) hardcodes `source_type=WEB_FETCH` before the default applies. The default change touches only no-URL agent creates.
  - **Tests:**
    - `tests/memory/test_save_default_source_type.py` — call `save_memory_item(...)` without `source_type`, load the resulting file, assert frontmatter `source_type == 'manual'`. (Behavioral; subsumes the enum-membership check — if `DETECTED` were still the default, this assert fails.)
    - `tests/context/test_memory_protocol_rule.py` — call `build_static_instructions(config)` and assert the rendered prompt contains a known unique phrase from `07_memory_protocol.md` (e.g., the "Promotion / Correction / Drift" header). Asserts the rule is *wired into the prompt assembly path*, not merely that the file exists. Separately assert the rendered prompt no longer contains the migrated `## Memory` subheader from `04_tool_protocol.md` (the cross-reference line is fine).

- [x] ✓ DONE **T3.** Wire `SESSION_REVIEW` source_type into the memory reviewer.
  - Update `co_cli/daemons/dream/prompts/memory_review.md`: instruct the reviewer to set `source_type='session_review'` on every item it creates via `memory_manage`.
  - **Tests:**
    - `tests/daemons/dream/test_memory_review_source_type.py` — assert the prompt file contains an explicit directive to set `source_type='session_review'` (string match).
    - End-to-end behavioral verification deferred to manual run during ship.

- [x] ✓ DONE **T4.** Position note in `docs/specs/memory.md`. Cross-link `dream.md`, `sessions.md`, `core-loop.md`. Describe:
  - The growth pipeline end-to-end
  - The inline curation surface (agent-driven via doctrine)
  - How `memory_review` and `skill_review` mine the session transcript as substrate (not as a session-arc producer — sessions are not first-class memory objects)
  - How merge/decay close the loop
  - The full `source_type` taxonomy after T2/T3 cleanups land — 6 values:

    | source_type | Producer | Meaning |
    |---|---|---|
    | `web_fetch` | `web_fetch` tool / `save_memory_item` URL branch | raw article substrate from URL fetch |
    | `manual` | agent inline saves via `memory_manage(create)` | default for agent-curated notes/rules/articles without URL |
    | `obsidian` | Obsidian vault sync | external read-only source |
    | `drive` | Google Drive sync | external read-only source |
    | `consolidated` | dream merge (`_housekeeping.merge_memory`) | output of duplicate-collapse pass |
    | `session_review` | memory reviewer (`_run_memory_review` after T3) | reviewer-extracted durable facts |

## Risks

- **Inline curation is soft and easy to ignore.** Doctrine-level instruction relies on the agent internalizing the rule. Effectiveness measurement is explicitly out of scope here. After ~20 real sessions, if promotion still feels rare, file a follow-up plan to instrument note-creation / replace-call counts and consider escalating to a stronger mechanism (session-review prompt, dedicated skill).
- **Inline curation bloats research turns.** Distillation adds LLM cost per finding worth keeping. Mitigated by agent judgment gating what gets distilled (resolved decision: agent decides).
- **Self-detected memory corruption is out of scope.** This plan handles user-feedback-driven correction only. Daemon-side retrospect that flags wrong memory without user input is deferred — it has a consent problem the architecture should not bypass on first pass.
- **Spec drift.** T4's spec note must stay in sync with `dream.md` and `sessions.md`. Cross-linking helps; the discipline is reading linked sections during spec updates.

## Delivery Summary — 2026-05-24

| Task | done_when | Status |
|------|-----------|--------|
| T1 | All 6 PASS criteria observed (persist, index, recall signal, merge discrimination, decay unprotected, decay protected) | ✓ pass — 12 existing tests cover all 6 criteria |
| T2 | `07_memory_protocol.md` rendered in static prompt; default `source_type='manual'`; `DETECTED` removed from `co_cli/` (rg=0 hits) | ✓ pass |
| T3 | `memory_review.md` prompt directs reviewer to set `source_type='session_review'`; scoped test asserts directive | ✓ pass |
| T4 | Section 6 added to `docs/specs/memory.md` covering growth pipeline, inline curation, reviewers, merge/decay, source_type taxonomy; cross-links present | ✓ pass |

**Files changed:**
- `co_cli/context/rules/07_memory_protocol.md` (new) — full memory doctrine: recall, explicit saves (fixed 4-kind table), curation (promote/correct/drift/dedup), anti-patterns
- `co_cli/context/rules/04_tool_protocol.md` — Memory section replaced with one-line cross-reference
- `co_cli/memory/service.py` — default `source_type` from `DETECTED` → `MANUAL`
- `co_cli/memory/item.py` — `DETECTED` enum value removed (zero-backward-compat per `feedback_zero_backward_compat`)
- `co_cli/daemons/dream/prompts/memory_review.md` — reviewer directed to tag saves `source_type='session_review'`
- `docs/specs/memory.md` — section 6 (Growth pipeline and curation discipline) added
- `tests/test_save_default_source_type.py` (new) — asserts default save lands as `manual`
- `tests/test_memory_protocol_rule.py` (new) — asserts doctrine wired into prompt assembly + migration left only cross-reference in 04
- `tests/daemons/dream/test_memory_review_source_type.py` (new) — asserts session_review directive in reviewer prompt

**Tests:** scoped — 41 passed, 0 failed (4 new + 37 regression checks across prompt assembly, memory write, housekeeping, recall metrics).
**Doc Sync:** clean — T4 already updated the only affected spec; no other docs referenced the changed surface (verified via grep for stale kind labels and `DETECTED` refs in `docs/specs/`).

**Plan vs. actual deviation:** plan specified tests at `tests/memory/` and `tests/context/` subdirs that don't exist in the repo; followed existing flat-layout convention (`tests/test_save_default_source_type.py`, `tests/test_memory_protocol_rule.py`). Test intent and assertions unchanged.

**Overall: DELIVERED**
All four tasks pass `done_when`. Lint clean, scoped tests green, doctrine rule rendered in the static prompt, source_type taxonomy honest end-to-end.

**Next step:** `/review-impl plan3` — full suite + evidence scan + auto-fix → verdict appended to plan.

## Implementation Review — 2026-05-24

### Evidence

| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| T1 | All 6 PASS criteria observable | ✓ pass | 12 existing tests cover all 6 criteria. Persist+index: `tests/test_flow_memory_write.py:32-71`. Recall signal: `tests/tools/memory/test_recall_metrics.py:50-113`. Merge discrimination: `tests/daemons/dream/test_housekeeping.py:145-166`. Decay (unprot/prot): `tests/daemons/dream/test_housekeeping.py:178-200`. |
| T2 | 07 rule rendered; default=MANUAL; DETECTED gone | ✓ pass | `co_cli/context/rules/07_memory_protocol.md:1-78` (intro value-criterion at L1-7, 4-kind table at L37-42, Curation at L44-76, SaveResult.action values at L70-74). `04_tool_protocol.md:96-98` cross-ref only. `co_cli/memory/service.py:143` default = `SourceTypeEnum.MANUAL.value`. `co_cli/memory/item.py:40-46` DETECTED removed; SESSION_REVIEW present. `rg "DETECTED\|detected" co_cli/` = 0 hits. |
| T3 | Reviewer prompt directs `source_type='session_review'` | ✓ pass *(after fix — see Issues)* | `co_cli/daemons/dream/prompts/memory_review.md:12` — explicit directive. After fix: `co_cli/tools/memory/manage.py:46,72,95,119` plumbs `source_type` through to `save_memory_item`. |
| T4 | Section 6 added; 6-row source_type table; cross-links present | ✓ pass | `docs/specs/memory.md:169-235` — pipeline diagram + inline-curation + reviewers + merge/decay + 6-row source_type table. Cross-links to `dream.md`, `sessions.md`, `core-loop.md` at L3, L213, L217, L221. |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| **T3 integration broken**: `memory_review.md:12` directs reviewer LLM to call `memory_manage(..., source_type='session_review')`, but `memory_manage` tool signature (`co_cli/tools/memory/manage.py:39-46`) did not accept `source_type`. The string-match test passed while the actual integration was unexecutable — reviewer's saves would land as `source_type='manual'`, leaving `SESSION_REVIEW` enum value dead. | `co_cli/tools/memory/manage.py:39-46` + prompt | **blocking** | Threaded `source_type: str \| None = None` through `memory_manage` → `_handle_create` → `save_memory_item` (`manage.py:46,72,95,119`). Added behavioral regression test `tests/daemons/dream/test_memory_review_source_type.py::test_memory_manage_persists_source_type_session_review` that exercises the tool end-to-end and asserts persisted frontmatter `source_type == "session_review"`. |
| Scope-creep: 2 RESEARCH docs modified outside any T's `files:` | `docs/reference/RESEARCH-context-management-comparison.md`, `docs/reference/RESEARCH-peer-repos.md` | minor | Recorded only — research docs are out-of-scope edits but harmless. TL should decide whether to stage them with this plan's commit or split. |
| Scope-creep: 2 unrelated untracked plans in `active/` | `docs/exec-plans/active/2026-05-23-151807-repl-input-queue.md`, `docs/exec-plans/active/2026-05-23-202305-remove-compactable-tools-whitelist.md` | minor | Recorded only — separate plans, must not be staged with plan3's ship commit. |

### Tests

- Command: `uv run pytest -x`
- Result: **581 passed, 0 failed** in 4:53
- Log: `.pytest-logs/20260524-154931-review-impl-full.log`
- Regression-test verification log: `.pytest-logs/20260524-154925-regression-test.log` (2 tests in `test_memory_review_source_type.py` — prompt string + behavioral integration)
- Final lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format clean across 314 files).

### Behavioral Verification

- `uv run co dream --help`: ✓ daemon CLI loads (start/status/stop/run subcommands present).
- Static prompt smoke (real `SETTINGS`):
  - ✓ `# Memory protocol` header rendered
  - ✓ `## Curation` section rendered
  - ✓ One-line cross-reference from `04_tool_protocol.md` present
  - ✓ `memory_manage` signature includes `source_type` parameter
- `success_signal` checks:
  - **T2**: doctrine rule reaches the agent's system prompt — verified via real `build_static_instructions(SETTINGS)` rendering.
  - **T3**: reviewer can persist `source_type='session_review'` end-to-end — verified via behavioral regression test calling `memory_manage(action='create', source_type='session_review')` and reading back the frontmatter.
  - **T4**: spec doc — no runtime behavior; verified by reading section 6 in `docs/specs/memory.md`.
- *Note*: `uv run co status` does not exist in this CLI; the comparable smoke is `uv run co dream --help` + the static-prompt smoke above. Full reviewer LLM run deferred to manual smoke at `/ship` time per plan T3 line 219.

### Files Changed (this review)

- `co_cli/tools/memory/manage.py` — added `source_type` parameter to `memory_manage` and `_handle_create`; imports `SourceTypeEnum`; passes through to `save_memory_item` with `MANUAL` as the resolved default.
- `tests/daemons/dream/test_memory_review_source_type.py` — added behavioral regression test that exercises `memory_manage(source_type='session_review')` against real filesystem and asserts persisted frontmatter.

### Overall: PASS

T1–T4 delivered; the one blocking gap (T3 prompt-vs-tool surface mismatch) was found and auto-fixed during review. Suite green (581/581), lint clean, behavior verified. Ready for `/ship`. TL should review the two scope-creep findings before staging.
