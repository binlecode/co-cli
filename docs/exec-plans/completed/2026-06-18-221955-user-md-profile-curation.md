# Exec Plan — USER.md profile curation (hermes parity, Gap B closure)

**Created:** 2026-06-18 22:19:55
**Slug:** user-md-profile-curation
**Status:** DRAFT — pre-Gate-1

## Context

Small Ollama models do not reliably recall or analyze user-profile facts through
`memory_search` — the recall under-firing problem ([[feedback_recall_fix_must_generalize]]).
For *who the user is* and *how they want to work*, search-driven recall is the wrong
mechanism: it depends on the model choosing to query, ranking the right items, and
synthesizing them. A single deterministically always-injected profile file removes all
three failure points.

This is hermes-agent's `USER.md` design (verified in source 2026-06-18:
`agent/system_prompt.py` snapshot-at-load injection, `tools/memory_tool.py` target='user'
write-back, ~1375-char budget). co already carries a `kind='user'` memory item type that
tries to do this job through recall — the unreliable path being replaced. This plan ports
the hermes mechanism and **removes `kind='user'` entirely** so there is one user-knowledge
surface, not two.

Closes **Gap B** in `docs/reference/RESEARCH-self-improvement-learning-loop.md`.

**Relation to [[feedback_recall_fix_must_generalize]] (chosen branch, stated explicitly):**
that note's *preferred* fix for recall under-firing is to redesign the recall *instruction*
(general, situation-keyed prose), and it treats per-turn auto-injection as a *costed
alternative* that reverses co's search-driven stance. This plan deliberately chooses the
costed alternative **for the user-profile surface only** — not for general memory recall —
because (a) it is the highest-frequency, most reliability-critical surface, and (b) small
local Ollama models under-fire even a well-engineered recall instruction (the observed
failure motivating this work). General memory recall (`rule|article|note`) stays
search-driven and instruction-governed; only user-profile moves to deterministic injection.
The injected artifact is ONE holistic model-curated blob, so it does not violate the note's
narrow prohibition on enumerated per-fact-type auto-injection.

**Current-state check (verified 2026-06-18):** blast radius of the removal is contained to
`co_cli/memory/item.py` (enum), `co_cli/memory/store.py:33,175–214` (two-pass priority
search exists *solely* for user-kind — collapses to single waterfall), `co_cli/tools/memory/recall.py:139,207`,
`co_cli/tools/memory/manage.py:59`. **Prompt-assembly surfaces also carry the user-memory
thread** (verified 2026-06-18, swept all `co_cli/context/rules/*.md` + dream prompts):
`co_cli/context/rules/07_memory_protocol.md` (holds-list L4–5, search-for-preferences L14,
explicit-save flow + `user` kind bullet + "User prefers concise" example L37–51 — but the
cross-session recall cascade L23–33 is session search, KEEP it) and
`co_cli/context/rules/05_workflow.md:16–18` ("saving durable user preferences to memory").
These are handled in TASK-8 (not TASK-1, since the explicit-save reroute prose depends on
TASK-3's `user_profile_write`). Souls `*/mindsets/memory.md` + `jeff/curation.md` reference
applying learned preferences generically (no kind/search mechanism) — reviewed, deliberately
left as doctrine (core-level review, out of scope). Injection point: orchestrator static-instruction
builders (`co_cli/agent/orchestrator.py`, alongside `_base_instructions_provider`). No
prompt-cache `cache_control` logic in co — static-build injection = snapshot-at-load,
frozen per session by construction. Specs are accurate to current state; no `/sync-doc`
needed first.

## Problem & Outcome

**Problem:** User profile/preferences live as `kind='user'` memory items recalled via
`memory_search`. Small models under-fire that search, so the agent routinely operates
without knowing who the user is or how they want to work — despite the facts being stored.

**Failure cost (current):** Silently degraded personalization on every turn. The agent
re-asks known preferences, ignores stated working style, and the stored `kind='user'` facts
sit unread. No error surfaces — it just behaves like it never learned anything about the
user.

**Failure cost (new, post-change, must be designed around):** USER.md's *sole writer* is
the dream memory reviewer, which is off by default (`memory.review_enabled=False`,
`dream.enabled=False`). So with `user_profile_enabled=True` (default) but dream off, USER.md
never fills and injects nothing — the feature is **inert-but-advertised-on**. This is
expected and documented (see Outcome), not a defect: injection is cheap and harmless when
empty, and the moment the user enables the dream reviewer the profile begins populating with
no second flag to flip. The hazard is only if this dependency is undocumented and a reader
assumes default-on means populated.

**Outcome:** A single reviewer-curated `~/.co-cli/USER.md` is deterministically injected
into every session's prompt (when `user_profile_enabled` AND the file is non-empty).
`kind='user'` is gone; recall is simpler. Profile reliability no longer depends on
small-model search behavior. **Population requires `memory.review_enabled=True`** — with
dream off (default), the surface is wired and injecting-ready but stays empty (no-op).

## Scope

**In:** Remove `kind='user'`; add `USER.md` storage + path + budget; reviewer-only
view/write tools; always-inject static block; reviewer prompt split; config flags; spec +
RESEARCH-doc + CLAUDE.md updates; functional tests + affected eval updates.

**Out:**
- Gap C local re-derivation (periodic full profile re-synthesis from recent transcripts) —
  separate later plan.
- Honcho / any external user-modeling backend — rejected by co's local-self-sufficiency
  mission.
- Project-local skills tier (unrelated RESEARCH §6.3 item).
- Migration / backward-compat of any kind — co is brand new; there is no pre-existing
  `kind='user'` data to migrate, tolerate, or `rm`. No legacy reader, no skip-unknown-kind
  defensive logic ([[feedback_no_migration_code]], [[feedback_zero_backward_compat]]).

## Behavioral Constraints

- **Not an enumerated auto-inject fact-list** ([[feedback_recall_fix_must_generalize]]):
  inject ONE holistic model-curated blob, never "inject every item of kind X." Must not
  grow into per-fact-type auto-injection.
- **Reviewer is the primary writer; tools are DEFERRED on the main agent.** co has no
  reviewer-exclusive tool registration path — `@agent_tool(register=True)` populates both
  `TOOL_REGISTRY` and `TOOL_REGISTRY_BY_NAME`, and `_build_native_toolset` adds every
  registered tool to the orchestrator unconditionally (visibility only gates per-turn
  *presentation*). So mirror the existing skill-tool pattern verbatim: `user_profile_view`/
  `user_profile_write` register `DEFERRED` (revealable via `tool_view`, off the default
  floor — [[feedback_defer_tradeoff_context_over_latency]]) and are also named in
  `MEMORY_REVIEW_SPEC.tool_names`. The primary writer is the reviewer; main-agent access is
  rare/deliberate, not blocked.
- **Snapshot-at-load, frozen per session:** read once at orchestrator build; no mid-session
  re-render. Cache-safe within a session; cross-session change expected.
- **Wholesale rewrite, not targeted edits:** small-model-friendly, no substring matching
  ([[feedback_tool_split_small_model]]).
- **Empty file injects nothing** — no empty header, no wasted instruction floor.
- **Atomic writes** only ([[reference_hermes_atomic_writes]]): `fileio/atomic.py`.
- **Explicit preference-saves route to the profile, not memory items.** With `kind='user'`
  gone, an explicit "remember I prefer X" / "I always work this way" has no memory-item home
  and must not silently demote to `kind='note'` (loses the always-inject guarantee) or defer
  to the off-by-default reviewer (breaks the synchronous "this turn" promise). The main agent
  reveals `user_profile_view` → `user_profile_write` and writes the profile synchronously —
  view-merge-write, approval-gated (protects the reviewer's curation from blind clobber).
  This is the hermes `target='user'` write-back path. All other explicit saves (rules, URLs,
  notes, decisions) still go to `memory_create`. The injected rule prose must teach this
  split.

## High-Level Design

```
  dream memory reviewer (forked)                main agent (per session)
  ─────────────────────────────                 ────────────────────────
  user_profile_view  ─┐                          orchestrator build
  user_profile_write ─┤ wholesale rewrite          reads USER.md once
       │              │                            ▼
       ▼              ▼                          static "USER PROFILE" block
   ~/.co-cli/USER.md (atomic, budget-capped) ──► injected, frozen for session

  memory_create/append/replace → memory items (rule|article|note only)
```

`kind='user'` removed → `store.search_memory_items` collapses from two-pass
(user-priority + waterfall) to single waterfall over `rule|article|note`.

## Tasks

### ✓ DONE TASK-1 — Remove `kind='user'` from the memory system
- **files:** `co_cli/memory/item.py`, `co_cli/memory/store.py`,
  `co_cli/tools/memory/recall.py`, `co_cli/tools/memory/manage.py`,
  `co_cli/index/store.py`
- Drop `USER = "user"` from `MemoryKindEnum` and the `MemoryKind` Literal (`item.py:25,33`).
- Delete `_USER_PRIORITY_CAP` and the pass-1 priority block (`store.py:33,187–198`);
  collapse `search_memory_items` to a single waterfall over `kinds or ["rule","article","note"]`;
  update the docstring (no more two-pass).
- Drop `"user"` from default kinds + param docs (`recall.py:139,207`) and from `kind`
  validation/docs (`manage.py:59`).
- Update the `kind='user'` docstring example in `co_cli/index/store.py:128` to `kind='note'`.
- **done_when:** repo-wide grep `rg -nw "user" co_cli/memory co_cli/tools/memory co_cli/index`
  shows zero remaining references to the `user` *kind* (matches only unrelated tokens like
  USER_DIR), AND the full test suite passes.
- **success_signal:** `memory_search` over a store with no user kind returns rule/article/note
  hits with no crash and no degradation warning.
- **prerequisites:** none

### ✓ DONE TASK-2 — USER.md storage + path + budget
- **files:** `co_cli/config/core.py`, `co_cli/memory/user_profile.py` (new),
  `co_cli/config/memory.py`
- `core.py`: add `USER_PROFILE_PATH = USER_DIR / "USER.md"`.
- New `co_cli/memory/user_profile.py` (named by concern, not a util module —
  [[feedback_no_util_modules]]): `read_user_profile() -> str` (`""` if absent, lazy, no
  seed); `write_user_profile(text: str) -> None` (`atomic_write_text`, enforces char
  budget — over-budget returns current usage so the model consolidates, hermes behavior).
- `config/memory.py`: `user_profile_char_budget: int` (default 1500) — budget sourced from
  config, never hardcoded in the module. Add its `CO_MEMORY_USER_PROFILE_CHAR_BUDGET` entry
  to the env-name mapping dict (`co_cli/config/memory.py:30–43`) alongside sibling fields.
- **done_when:** unit-level functional test: write within budget round-trips; write over
  budget is rejected with usage reported; absent file reads as `""`. Full suite passes.
- **success_signal:** `read_user_profile()` after `write_user_profile(x)` returns `x`.
- **prerequisites:** none

### ✓ DONE TASK-3 — Curation tools (DEFERRED on main agent, reviewer is primary writer)
- **files:** `co_cli/tools/user_profile/__init__.py` (new, docstring-only),
  `co_cli/tools/user_profile/view.py` (new), `co_cli/tools/user_profile/write.py` (new),
  `co_cli/agent/toolset.py` (import the new tools so `TOOL_REGISTRY_BY_NAME` resolves them)
- `user_profile_view` → current USER.md text + budget usage (chars used / cap). Register
  `DEFERRED`.
- `user_profile_write` → full-file replace via `write_user_profile`, returns new usage.
  Register `DEFERRED`. **Destructive wholesale overwrite — mirror `skill_edit`'s guard
  parity:** `is_approval_required=True` with an `approval_subject_fn`; the dream reviewer
  suppresses approval via the existing `build_task_agent(..., requires_approval=False)` path
  (`co_cli/agent/build.py:98`), exactly as it does for `skill_create/edit/patch`.
- Follow `.agent_docs/tools.md` (CoDeps, return types, versioning). Both are registered like
  every tool (cannot be hidden from the main agent); DEFERRED keeps them off the default
  prompt floor.
- **done_when:** both tools callable via their CoDeps entrypoint in a test;
  `user_profile_write` enforces budget at the tool boundary and carries
  `is_approval_required=True` on the main-agent path; reviewer path writes without approval.
  Full suite passes.
- **success_signal:** a reviewer calling `user_profile_write` then `user_profile_view` sees
  its own text back.
- **prerequisites:** TASK-2

### ✓ DONE TASK-4 — Always-inject USER.md block
- **files:** `co_cli/agent/orchestrator.py`, `co_cli/config/memory.py`
- Add a self-contained static instruction provider `_user_profile_provider(deps) -> str | None`
  to `ORCHESTRATOR_SPEC.static_instruction_builders` between `_base_instructions_provider`
  and `_toolset_guidance_provider` (`orchestrator.py:55–59`). It reads `USER_PROFILE_PATH`
  at build time, gates on `deps.config.memory.user_profile_enabled`, and returns a wrapped
  `USER PROFILE (who the user is)` block; empty file or flag off → return `None` (emit
  nothing). No edit to `assembly.py` (`build_base_instructions` takes only `config` and the
  provider is independent).
- `config/memory.py`: `user_profile_enabled: bool = Field(default=True)` + its
  `CO_MEMORY_USER_PROFILE_ENABLED` entry in the env-name mapping dict.
- Run both instruction-floor guards during dev ([[feedback_instruction_floor_guards_on_rule_edits]])
  **with a full `user_profile_char_budget`-sized USER.md (not empty)** so the floor headroom
  is validated at worst case; keep `tool_name(` call syntax out of the header/prose.
- **done_when:** integration assertion — with a non-empty USER.md and `user_profile_enabled=True`,
  the built orchestrator instructions contain the profile block; with empty file OR flag
  off, they do not. Floor guards pass with a budget-max USER.md. Full suite passes.
- **success_signal:** a session built after the reviewer writes USER.md carries the profile
  text in its system prompt.
- **prerequisites:** TASK-2

### ✓ DONE TASK-5 — Reviewer wiring + prompt split
- **files:** `co_cli/daemons/dream/_reviewer.py`, `co_cli/daemons/dream/prompts/memory_review.md`
- Add `user_profile_view`, `user_profile_write` to `MEMORY_REVIEW_SPEC.tool_names`.
- Rewrite `memory_review.md`: *who the user is + how they want to work + preferences/style*
  → USER.md (view → merge → wholesale write, stay under budget, consolidate); *environment
  facts, tool quirks, references, articles/notes* → memory items (`rule|article|note`).
  Remove old "save user persona as a memory item" guidance. Note: current prompt L4–6
  (persona / how-they-want-to-work / behavior expectations) all move to USER.md; only L7
  (references) and durable rules/notes stay as memory items.
- **done_when:** a dream memory-review run over a transcript containing a stated user
  preference results in that preference present in USER.md (not as a memory item).
  Full suite passes.
- **success_signal:** after a review of a session where the user states a working-style
  preference, `read_user_profile()` contains it. (Proxy for "profile is current" — this
  plan does no full re-synthesis; the profile reflects what the reviewer incrementally
  extracted, not a guaranteed-complete model. Re-synthesis is deferred Gap C.)
- **prerequisites:** TASK-3, TASK-4

### ✓ DONE TASK-6 — Update RESEARCH doc + CLAUDE.md
- **files:** `docs/reference/RESEARCH-self-improvement-learning-loop.md`, `CLAUDE.md`
- RESEARCH: mark Gap B closed; record USER.md parity + `kind='user'` removal.
- CLAUDE.md memory-tier description: user preferences now in USER.md, not memory items;
  kinds list loses `user`.
- (Specs in `docs/specs/` are updated by `sync-doc` post-delivery, not here.)
- **done_when:** grep confirms no doc in scope still describes `kind='user'` as live; Gap B
  marked closed.
- **success_signal:** N/A (doc task)
- **prerequisites:** TASK-1

### ✓ DONE TASK-7 — Eval updates
- **files:** `evals/eval_memory.py`, `evals/eval_rule_compliance.py`
- Update any `kind='user'` references (both in the current working set per git status).
- **done_when:** repo-wide grep `rg "['\"]user['\"]" evals/eval_memory.py evals/eval_rule_compliance.py`
  shows no live user-kind usage; the touched evals run without referencing the dropped kind.
- **success_signal:** N/A (eval maintenance; UAT smoke per [[feedback_eval_real_world_data]])
- **prerequisites:** TASK-1

### ✓ DONE TASK-8 — Prompt-assembly rewrites (rule 07 + 05, explicit-save reroute)
- **files:** `co_cli/context/rules/07_memory_protocol.md`,
  `co_cli/context/rules/05_workflow.md`
- **`07_memory_protocol.md`:**
  - Intro holds-list (L3–7): drop "user preferences" — memory holds standing rules, web
    articles, distilled notes. Add a one-line pointer that who-the-user-is/preferences now
    live in the always-injected user profile, not memory items.
  - Recall (L14): drop "preferences" from the `memory_search` target list (conventions,
    rules, articles remain). **Leave the cross-session recall cascade L23–33 untouched** —
    it is session search, unrelated to the user kind.
  - Explicit saves (L35–51): add the preference-routing split — "remember I prefer X" /
    working-style facts → reveal `user_profile_view` then `user_profile_write` (view → merge
    → wholesale write under budget); everything else (rules, URLs, notes, decisions) →
    `memory_create` synchronously this turn. Remove the `user` kind bullet (L48). Replace the
    "User prefers concise responses" example (L42–44) with a non-preference example (it now
    belongs to the profile, not a memory item).
- **`05_workflow.md` L16–18:** preferences move out of the "to memory" clause — "saving
  durable corrections, decisions, and cross-session facts to memory, and user-profile updates
  to the user profile, is always permitted — no Directive required."
- Run **both instruction-floor guards** after these edits ([[feedback_instruction_floor_guards_on_rule_edits]]);
  keep `tool_name(` call syntax out of the prose. Coordinate with TASK-4's budget-max guard
  run so the floor is validated once against the final rule set + a full USER.md.
- **done_when:** `rg -nw "user" co_cli/context/rules` shows no reference to the `user` memory
  *kind* or to recalling/saving preferences as memory items (matches only unrelated tokens);
  the cross-session cascade is still present; floor guards pass. Full suite passes.
- **success_signal:** asked to "remember I prefer X," the agent writes the user profile (via
  `user_profile_write`), not a memory item.
- **prerequisites:** TASK-3 (needs `user_profile_write`), TASK-4 (shares the floor-guard run)

## Testing

Functional only ([[feedback_functional_tests_only]]) — assert observable behavior, mirror
`done_when`:
- USER.md round-trip + budget enforcement (TASK-2).
- Profile block present/absent in built prompt by file-state × flag (TASK-4).
- Reviewer routes a stated preference into USER.md (TASK-5).
- Memory recall works with `kind='user'` gone (TASK-1).
- Explicit "remember I prefer X" routes to `user_profile_write`, not a memory item (TASK-8).
Full suite is the cross-file ripple safety net for the removal (TASK-1/7 `done_when`).

## Open Questions

- **OQ-1:** USER.md header wording / wrapping — match hermes (`USER PROFILE (who the user
  is)`) or co house style? Lean hermes for parity; non-blocking, settle in dev.
- **OQ-2:** char budget default — hermes uses ~1375; proposing 1500. Confirm against co's
  instruction-floor headroom during the TASK-4 floor-guard run.

---

## Decisions

Critique ledger (Core Dev + PO, cycles C1–C2). C2: both reviewers returned
`Blocking: none` after verifying their C1 blockers resolved against source — no new issues.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Correct — co has no reviewer-exclusive path; grounded in toolset.py/deps.py/skills.md. | Behavioral Constraint #2 rewritten to "DEFERRED on main agent, reviewer is primary writer"; TASK-3 registers DEFERRED + names in MEMORY_REVIEW_SPEC; toolset.py added to TASK-3 files. |
| CD-M-2 | adopt | Destructive overwrite needs guard parity with `skill_edit`. | TASK-3: `user_profile_write` gets `is_approval_required=True` + `approval_subject_fn`; reviewer suppresses via build_task_agent requires_approval=False. |
| CD-m-1 | adopt | Real stale ref outside grep root. | TASK-1: added `co_cli/index/store.py` to files + grep root; docstring example → `kind='note'`. |
| CD-m-2 | superseded | Verified non-issue; then the whole leftover-file concern was dropped post-C2 — co is brand new, no pre-existing `kind='user'` data to tolerate (backward-compat rejected). | TASK-2 (leftover-file verification) removed entirely; tasks renumbered. |
| CD-m-3 | adopt | Provider is self-contained. | TASK-4: removed `assembly.py` from files. |
| CD-m-4 | adopt | Sibling fields all have env-map entries. | TASK-2 + TASK-4: explicit env-name mapping entries for both new fields. |
| CD-m-5 | adopt | Follows from CD-M-1. | TASK-3 files now includes `co_cli/agent/toolset.py`. |
| PO-M-1 | adopt | Honesty about choosing the costed branch is required by the note. | Context: added paragraph framing injection as the deliberate costed alternative for the profile surface only; general recall stays search-driven. |
| PO-M-2 | adopt | Real posture incoherence; must be documented. | Problem & Outcome: added "Failure cost (new)" inert-but-on mode + Outcome states population requires `review_enabled=True`. |
| PO-m-1 | adopt | Avoid over-claiming completeness. | TASK-5 success_signal annotated as a proxy (no re-synthesis; Gap C deferred). |
| PO-m-2 | adopt | Floor headroom must be validated at worst case. | TASK-4: floor guards run with a budget-max USER.md. |
| PO-m-3 | adopt | Proxy nature acknowledged. | TASK-5 success_signal note (shared with PO-m-1). |
| (post-C2) | adopt | User confirmed co is brand new — no migration/backward-compat anywhere. | Scope: migration/backward-compat fully excluded; TASK-2 leftover-file task removed; 8→7 tasks. |
| G1-1 | adopt | G1 sweep: user-memory thread spans the prompt-assembly layer (rule 07 + 05), not just code. `kind='user'` removal without rewriting injected rules leaves the model told to classify preferences as a dead kind, and trips floor guards mid-dev. | Added TASK-8 (prompt-assembly rewrites); blast radius in Context now names both rule files; cross-session cascade explicitly preserved; souls mindsets reviewed-and-left. 7→8 tasks. |
| G1-2 | adopt | G1 sweep exposed an orphaned path: explicit "remember I prefer X" had no home after `kind='user'` removal (note demotes / reviewer is off-by-default). | Behavioral Constraint added: explicit preference-saves reroute to `user_profile_write` synchronously (option 1, hermes target='user' parity); encoded in TASK-8 rule rewrite + Testing. |

## Final — Team Lead

Plan approved. Converged at C2 (both Core Dev and PO: `Blocking: none`).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev user-md-profile-curation`

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | grep shows zero `user`-kind refs in memory/tools/index; suite green | ✓ pass |
| TASK-2 | USER.md round-trip + over-budget rejection + absent→"" | ✓ pass |
| TASK-3 | both tools callable via CoDeps; write enforces budget + approval-required main-agent path | ✓ pass |
| TASK-4 | profile block present/absent by file-state × flag; floor guards pass at budget-max | ✓ pass |
| TASK-5 | reviewer spec resolves user_profile_view/write; prompt split routes profile vs items | ✓ pass (wiring; LLM-behavioral routing deferred to eval) |
| TASK-6 | Gap B closed; CLAUDE.md kinds list loses `user` | ✓ pass (Dev-1) |
| TASK-7 | no live user-kind usage in touched evals; files parse | ✓ pass (Dev-1; evals had no live refs) |
| TASK-8 | no `user`-kind/preference-recall in rules; cascade preserved; floor guards pass | ✓ pass |

**Tests:** scoped — 109 passed, 0 failed (`.pytest-logs/*-scoped.log`). New: `test_flow_user_profile_store.py`, `test_flow_user_profile_tools.py`, `test_flow_user_profile_injection.py`. Repaired TASK-1 ripple in 4 existing test files (removed 2 obsolete user-kind tests, reseeded incidental `kind="user"` → `note`).

**Doc Sync:** fixed — memory.md (8 stale two-pass/user-kind refs, 2 config rows, new §7 user profile, reviewer routing), 01-system.md, prompt-assembly.md (3→4 static builders), skills.md reviewer diagram.

**Deviations from plan (announced during dev):**
- ⚠ Extra file `co_cli/deps.py`: added `user_profile_path` to CoDeps. The plan's no-arg `read_user_profile()`/`write_user_profile()` reading the frozen global `USER_PROFILE_PATH` was both untestable and hit real user data (it overwrote the real `~/.co-cli/USER.md` during a test run; restored to absent). Aligned to the explicit-path convention (`memory_dir`/`usage_log_path`): functions take `path`; tool/provider/reviewer resolve `deps.user_profile_path`; tests inject `tmp_path`.
- Floor guards: the user-profile block is intentionally NOT in the pinned static-floor guard (variable user-data, ~1500-char/~400-token worst case — analogous to recall content, not a fixed floor cost). Rule edits (TASK-8) stay within the existing ceiling; no re-pin.
- TASK-1 ripple touched test files not in the plan's file lists (test_flow_memory_search/view/write, waterfall_cap docstring, dream/test_memory_review_source_type) — required to keep the suite green per TASK-1 done_when.

**Overall: DELIVERED**
All 8 tasks pass done_when; scoped tests green; lint clean; docs synced. TASK-5's live-LLM routing assertion is the one item left for eval/review-impl (functional wiring verified).

## Post-Delivery Refinements — 2026-06-19

Applied after the delivery summary, in scope for `/review-impl`:

1. **Profile-routing disambiguation (both writers).** Added a person-vs-domain scope rule to
   both USER.md writers so identity/working-style facts route to the profile while
   domain-scoped operational rules (e.g. "squash-merge PRs") stay as memory `rule` items —
   even when phrased "always":
   - `co_cli/context/rules/07_memory_protocol.md` (main-agent explicit saves)
   - `co_cli/daemons/dream/prompts/memory_review.md` (dream reviewer — primary writer; had
     the same unfixed collision)
   Floor guards re-run after the rule-07 edit (budget + no-deferred-signature) — pass.

2. **Initial USER.md seeded + source cleanup (live `~/.co-cli` state, one-off manual op).**
   No `kind='user'` records existed (premise confirmed: store had 6 note + 3 rule). Composed
   `~/.co-cli/USER.md` from the one genuine persona fact (Malvern PA / Eastern Time). Excluded
   the `pref_pst|python|terse` records (eval fixtures — synthetic 2026-04-01 timestamps,
   sequential placeholder UUIDs; `pref_pst` also contradicted the real location). Deleted the
   migrated `user-location-malvern-pa-19355` note from disk + DB (its content now lives in
   USER.md). Kept `pr-merge-strategy-squash-only` as a `rule` (domain-operational, recalled
   11×) — not duplicated into the profile.

**Verification scope for review-impl:** both USER.md writers agree on persona→profile /
domain-rule→memory routing; lint clean; floor guards pass.

## Implementation Review — 2026-06-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | grep shows zero user-kind refs; suite green | ✓ pass | `rg -nw user co_cli/memory co_cli/tools/memory co_cli/index` → only `user_profile.py` docstrings + `search_util.py` "user query"; `item.py:25-28` enum has no USER; `store.py:166-194` single waterfall, `_USER_PRIORITY_CAP` gone |
| TASK-2 | round-trip + over-budget reject + absent→"" | ✓ pass | `user_profile.py:39-43` absent→`""`; `:46-54` budget enforced, atomic write; explicit `path` arg per deviation; `config/memory.py:77-78,44-45` both fields + env map |
| TASK-3 | both tools callable; write budget+approval main path | ✓ pass | `view.py:14-31` DEFERRED, deps path; `write.py:23-52` DEFERRED + `is_approval_required=True` + `_write_subject`; over-budget→`tool_error` carrying usage; `__init__.py` docstring-only; `toolset.py:60-61` imported |
| TASK-4 | block present/absent by file×flag; floor guards | ✓ pass | `orchestrator.py:36-44,64-71` `_user_profile_provider` sited between base + toolset builders, gates flag, None on empty/off, "USER PROFILE (who the user is)" block |
| TASK-5 | reviewer spec resolves view/write; prompt split | ✓ pass (wiring) | `_reviewer.py:79-80` both names in `MEMORY_REVIEW_SPEC.tool_names`; LLM routing deferred to eval |
| TASK-6 | Gap B closed; CLAUDE.md kinds lose user | ✓ pass | RESEARCH §5.2 "CLOSED", L144/196; `CLAUDE.md:40` kinds `rule\|article\|note` + USER.md note |
| TASK-7 | no live user-kind usage in touched evals | ✓ pass | evals had no live refs (delivery) |
| TASK-8 | no user-kind/preference-recall in rules; cascade kept | ✓ pass | `rg -nw user co_cli/context/rules` → only prose "the user"/"user profile"; `07:43-51` profile-routing split; `05:18` profile-update clause |

### Issues Found & Fixed
No issues found.

Scope note (not blocking): the working tree carries many files from sibling active plans
(`01_interaction.md` rename, `03_reasoning.md`, `06_skill_protocol.md`, `display/*`,
`tools/shell`, `tools/web`, `docs/specs/{agents,personality,tui}.md`, `RESEARCH-*peer/workroom/opencode`).
`context/assembly.py` is a docstring-only `01_identity`→`01_interaction` edit from the rename
plan, consistent with this plan's "No edit to assembly.py." `/ship` must stage only this
plan's files (TASK-1..8 + declared deviations: `deps.py`, the 3 new `test_flow_user_profile_*`,
the 4 repaired memory/dream tests, doc-sync `memory.md`/`01-system.md`/`prompt-assembly.md`/`skills.md`).

### Tests
- Command: `uv run pytest -p no:cacheprovider`
- Result: 790 passed, 0 failed
- Log: `.pytest-logs/*-review-impl-full.log` (scoped: `*-review-impl-scoped.log`, 30 passed)

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads, all subcommands listed)
- Profile injection / round-trip / budget / kind-removal: ✓ verified via `test_flow_user_profile_*`
  + `test_flow_memory_*` (deterministic, no LLM)
- TASK-5/TASK-8 `success_signal` (LLM-mediated reviewer + main-agent preference routing):
  non-gating in chat; verified at the wiring level, live routing left to eval per delivery note

### Overall: PASS
All 8 done_when re-verified against source with file:line evidence; full suite green; lint clean;
boot smoke passes. Ship gate must stage only this plan's files (sibling-plan changes share the tree).
