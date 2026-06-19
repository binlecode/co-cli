# Behavioral Rules — Redesign as Low-Inference Reflexes (whole-set audit + authoring rubric; act on the consolidation plan's sanctioned G2 stop-condition + a new anti-thrash reflex, using opencode's prompt-design patterns)

Task type: evidence-driven core-prompt redesign (rewrite, not consolidate) — establish a low-inference-reflex authoring standard, audit all 28 rule sections against it (read-only), and rewrite the two evidenced sections (stop-condition, method-switch) as reflexes; sized token-neutral-or-negative, gated by floor guards + behavioral smoke (+ ablation where a probe fits).

## Program intent (charter)

This plan is **increment 1** of an evidence-gated migration of co's entire behavioral rule set from high-inference judgment-call prose to low-inference reflexes, motivated by **small-model rule-following reliability** (the configured weak model under-executes judgment-call rules — the recall miss, redundant re-answer, and fetch thrash are symptoms of one general defect). It is explicitly **NOT a one-shot whole-set rewrite** — that is the rejected over-build (rewriting sections with no evidence). The program runs in three durable parts:

- **Standard** — the Authoring Standard rubric, codified in `.agent_docs/` by TASK-0, governs every future rule edit. This is the anti-erosion lever (`project_architecture_erosion_tension`): without a written standard, rule style is re-litigated every edit.
- **Map** — TASK-0's read-only audit classifies all 28 sections reflex-vs-judgment-call, producing the backlog of judgment-call sections that are candidate future increments.
- **Increments** — each subsequent section rewrite is its own evidence-gated step, drafted **only after** the audit and re-entering Gate 1 with a demonstrated failure or a clean ablation signal. No section is rewritten on faith; the follow-up plan(s) are drafted from TASK-0's findings, not before.

This increment lands the two sections that already have live evidence (stop-condition, method-switch) plus the standard and the map. The whole-set trajectory is real, but it is traversed one evidence-gated increment at a time — never a bulk faith-based rewrite.

## Context

Two live failures in a single weather session exposed a general defect in co's behavioral rules:

1. **Recall under-fires.** "what's the weather tomorrow" → the model asked the user for a location that was already saved in memory (`user-location-malvern-pa-19355-...md`), violating `07_memory_protocol.md ## Recall`.
2. **Redundant re-answer.** The model answered, interleaved a `memory_create`, then re-emitted the same answer (with `Thought for 0s`) — no stop-condition existed to halt it.

The decisive reframe (user-directed): **instructions must be designed to counter model limitations.** "The model ignores prose" is, in almost every case, a verdict on the *instruction's design*, not proof prose can't steer. co's rules are written as **high-inference judgment calls** — they ask the model to make metacognitive leaps it cannot. The recall rule is the clearest case:

> *"when you suspect relevant cross-session context exists, or you recognize the topic but lack context for this user's specific setup or preferences — search before answering"* (`07:11-15`)

That is four inference hops, including "know what you don't know" — exactly the capability this model lacks. A rule **designed to counter the limitation** removes the inference: tie the behavior to an *observable cue self-evident at the moment it fires*.

**The recall failure (#1) is NOT fixed here — it goes to a mechanism, which proves the principle.** The location case is a user-profile fact, now owned by the concurrently-decided USER.md always-injection plan (`project_user_md_profile`, plan `docs/exec-plans/active/2026-06-18-221955-user-md-profile-curation.md`) — profile recall is too unreliable for a weak model to do via search, so it becomes a deterministic injected block, not a rule. This plan therefore does **not** rewrite `07 ## Recall`. The recall observation stays here only as the evidence that motivated the general principle and the audit; whether any *non-profile* recall defect is worth a reflex is a question for TASK-0's audit to answer with evidence, not assumed now. (See memory `feedback_recall_fix_must_generalize`, `feedback_instructions_counter_model_limits`.)

This plan is a deliberate sibling to the delivered-but-unshipped `behavioral-rules-consolidation-cleanup` (`docs/exec-plans/active/2026-06-18-151602-...`). It executes the **G2 stop-condition** path that plan's TASK-5 hand-off explicitly *sanctioned* for a future plan ("a zero-net-floor-growth tightening of existing `04`/`05` prose") — this is that plan, not a correction of it.

**Inherited design catalog (opencode prompt survey, this session — do not re-derive).** Verbatim patterns saved at `scratchpad/opencode_prompt_analysis.md`. The reusable techniques:
- Trigger-based, not judgment-based: `"Before X, always Y"` / `"When X, do Y"` — not `"if you suspect…"`.
- Name the concrete tool, not the category (`memory_search`, not "search").
- Show the anti-pattern in quotes so it's detectable (`preambles ('Okay, I will now…')`).
- Enumerate exceptions exhaustively, then the default is unambiguous.
- Consequence framing for the rare hard rule.
- opencode tailors prompt *style to model strength* — weaker-model variants (beast/qwen) are terser and more imperative. co's configured model is weak; its rules should follow the weak-model style.

## Problem & Outcome

**Problem.** co's behavioral rules are correct in intent but engineered as judgment calls a weak model under-executes. Rewriting them as low-inference reflexes is the limitation-countering fix the consolidation plan's evidence (model ignores heavy prose) actually argues *for* — not against.

**Failure cost — what breaks if this plan is skipped (given USER.md + consolidation both ship).** The residual cost is real but modest: (a) the answer→save→re-answer loop (the stop-condition gap) keeps producing duplicate final answers; (b) the consumer-site thrash (no method-switch reflex) keeps burning the per-run budget into turn-errors; (c) the rule-authoring standard stays uncodified, so every future rule edit re-litigates style and the high-inference judgment-call drift keeps accreting (`project_architecture_erosion_tension`) and "DEAD-WEIGHT" verdicts keep getting misread as "prose can't help" rather than "this prose is mis-designed." The over-correction risk is the inverse: import opencode's coding-first terseness wholesale (co is knowledge-work, deliberately thorough), grow the floor with net-new prose, or rewrite sections the audit has no evidence for.

**Outcome.** Rewrite the smallest set of rules tied to *observed* failures as low-inference reflexes, each **token-neutral-or-negative** (replace verbose judgment prose; never append), each preserving co's thoroughness stance, each gated honestly. No new sections unless unavoidable (keeps the eval `_INVENTORY`/count untouched).

**Shippable contract:** the reflex rewrites that clear core-level review + floor guards + their stated gate. A rewrite that fails its gate keeps the original prose and is reported, not forced.

## Behavioral Constraints
- Rule prose is core-prompt change → **core-level review** on every edit (memory: souls/personality/rules are built-in platform core).
- Editing any rule `.md` trips the **instruction-floor guards** — run `test_instruction_floor_coupling` (F5 no-deferred-tool-signature) and `test_instruction_budget` (`INSTRUCTION_BLOCK_CEILING = 25_000`) on every edit. **Keep `tool_name(` call syntax out of rule prose** (`feedback_instruction_floor_guards_on_rule_edits`). Refer to tools as plain words (`memory_search`) without the trailing `(`. NOTE (CD-m-1): `test_orchestrator_schema_budget` guards the *tool-schema* prefill (per-tool 2600 / ALWAYS-bucket 20100), driven by tool docstrings — NOT rule files; it is not a rule-edit guard (include it only if a task also edits a tool docstring, which this plan scopes out).
- **Net static-floor token delta should be ≤ 0 per edit — discipline, not guard-forced (re-measured 2026-06-19).** Default config (personality OFF — co's runtime default, what `test_instruction_budget` measures in CI) floor = **18,198 / 25,000, headroom 6,802** — so small reflex additions will NOT trip the guard; ≤0 is anti-bloat discipline, not a hard wall. CAVEAT: with personality ENABLED the floor jumps to ~24,694 (only ~306 headroom, per the test docstring), so keep reflexes net ≤0 to stay safe across configs. USER.md (shipped) did not change this floor — the profile is a separate injected block, not part of the three measured builders.
- **No section add/remove, and preserve every `##` heading text verbatim.** In-place body rewrites keep the section set at 28 → no `eval_rule_compliance.py _INVENTORY`/count edit. `_INVENTORY` is keyed `(stem, title)` with a hard `len(_INVENTORY) == len(sections) == 28` assert and per-key existence check — **a retitled heading breaks the eval just as an add/remove does.** Any task that renames/adds/removes a heading must update `_INVENTORY` + the count assert in the same task and run `uv run python evals/eval_rule_compliance.py --inventory`. R2/R3 rewrite bodies only and must not retitle their host headings.
- **Preserve co's thoroughness stance.** The stop-condition brakes *redundant re-emission and no-op steps*, NOT thoroughness-during-work. It must not read as "be brief" or contradict `01 ## Thoroughness over speed` / `04 ## Follow through` / `05 ## Execution`.
- Gates, never conflated: **ablation-gated** where a single-turn probe in `eval_rule_compliance.py` fits the behavior; **behavioral-smoke-gated** where it does not — drive the real failure repro via a smoke script following the `tmp/weather_smoke.py` pattern and observe the corrected behavior; **review-gated** for pure wording.
- All eval data real; centralized eval settings; `ensure_ollama_warm` outside `asyncio.timeout`; tail the log + RCA-first on slow calls (`feedback_*` memories).

## Authoring Standard — the Low-Inference Reflex Rubric

The yardstick TASK-0 classifies against and every rewrite must satisfy. Derived from the opencode prompt survey (`scratchpad/opencode_prompt_analysis.md`), adapted to co's weak configured model and knowledge-work (not coding-first) stance. A rule is a **low-inference reflex** when it meets these; a **judgment call** when it fails one or more:

1. **Observable cue, self-evident at fire time.** The trigger is a condition the model can recognize in the moment without metacognition — "before you ask the user X", "when a tool returns an error", "after two same-method failures". NOT "when you suspect…", "if it seems relevant…", "when you recognize you lack context" (these require the model to know what it doesn't know).
2. **Imperative, single action.** "Do Y" / "Before X, do Y" — one concrete next action, not a paragraph of considerations to weigh.
3. **Names the concrete tool**, in plain words (`memory_search`), never the category ("search tools") and never with `tool_name(` call syntax (F5 floor guard).
4. **Anti-pattern shown, not just named.** Where a wrong behavior is common, quote it so it's detectable ("don't restate an answer you already gave").
5. **Exceptions enumerated, then the default is unambiguous.** Exhaust the "unless" cases up front so the residual default needs no judgment.
6. **co-fit, not coding-fit.** Borrow the reflex *form*, not opencode's terseness targets (e.g. NOT "< 4 lines"); preserve co's thoroughness stance — reflexes brake redundant/no-op steps, never thoroughness-during-work.

This rubric is a durable artifact and the anti-erosion lever — without a written standard, rule style gets re-litigated every edit (`project_architecture_erosion_tension`). TASK-0 records it as a **one-paragraph note in `.agent_docs/`** (a firm deliverable, not an optional hand-off) so future rule edits have a yardstick.

## Scope

### In scope
- **Full-set diagnosis (TASK-0, read-only).** Classify all 28 sections reflex-vs-judgment-call against the rubric; produce the whole-set map; flag any egregious-and-cheaply-gateable rules beyond R2/R3 (including any *non-profile* recall defect with real evidence) and any concrete structural defect. No edits.
- **The authoring-standard rubric** (above) — recorded as a `.agent_docs/` note (TASK-0 deliverable).
- **R2 — stop-condition reflex (the consolidation plan's sanctioned G2 path).** Fold a low-inference "stop when done; don't restate" reflex into an existing section (candidate home `04 ## Execute, don't promise` or `05 ## Completeness`) — in-place, no new section, no heading retitle.
- **R3 — method-switch anti-thrash reflex.** The weather thrash was 5 fetches across *different* consumer sites — `04 ## Error recovery` only catches *identical* repeated calls, so it missed it. Add a low-inference reflex: after two failures at the same information goal by the same *method*, switch method (e.g. shell curl), don't try a third variant of the same method. In-place in `04 ## Error recovery`, no heading retitle.

### Out of scope (deferred with rationale)
- **`07 ## Recall` rewrite — NOT done here.** The only observed recall failure is a user-profile fact owned by the USER.md always-injection plan. Any non-profile recall reflex waits on TASK-0 evidence; the recall rule is not edited in this plan.
- The consolidation plan's C2 persistence cluster, C7, G1, G3 — owned by that plan / its hand-off; not re-opened here. (Note for the C2 Gate-1 plan: consolidating persistence must not remove the R2 stop-condition.)
- Importing opencode's numeric line caps ("< 4 lines") — co is knowledge-work, not coding-first; a hard length cap contradicts the thoroughness stance. Borrow the *reflex form*, not the terseness target.
- Per-model prompt variants (own plan: `per-model-prompt-calibration`).
- Tool docstrings (the `web_fetch`/`shell_exec` curl-fallback wording was already fixed this session) — except where R3 prose references method-switching.
- `docs/specs/` edits as tasks (sync-doc post-delivery).

## Coordination — USER.md profile (SHIPPED) — Gate-1 rescan 2026-06-19

The USER.md profile **shipped** as `402203e1 feat: always-injected USER.md profile, remove kind='user' (v0.8.418)` (plan archived to `completed/`). Current state (rescanned):
- `MemoryKindEnum` is now `rule | article | note` — `kind='user'` removed (`co_cli/memory/item.py:24-27`).
- Profile mechanism live: `co_cli/tools/user_profile/{view,write}.py`, `co_cli/memory/user_profile.py`, dream write-back (`co_cli/daemons/dream/_reviewer.py`).
- **The ship DID rewrite `07_memory_protocol.md`** (the C1 Core Dev prediction that it "never touches the rule file" was wrong against the actual implementation): `## Explicit saves` now carries the `user_profile_view`/`user_profile_write` reveal + profile-vs-memory disambiguation, and `## Kind selection` dropped the `user` bullet. Section count unchanged at **28** (`eval_rule_compliance.py:646` still asserts 28).

**Consequence for this plan — the orphaned-bullet hand-off is now MOOT** (the ship already removed it). One real residual remains: **`07 ## Recall` (`07:12-14`) still says "when you suspect relevant cross-session context exists, or you recognize the topic but lack context for this user's specific setup or preferences"** — but "this user's specific setup or preferences" is now the always-injected profile, NOT something to search memory for. The ship rewrote `## Explicit saves` but left this stale trigger in `## Recall`. **TASK-0 flags this** as a sync-doc/accuracy fix candidate (not a reflex rewrite — still no evidence of a non-profile recall failure). It is an in-section body phrasing fix; no count change.

## High-Level Design

Audit-then-act: a read-only whole-set classification (TASK-0) against the rubric produces the full-scope map, codifies the rubric in `.agent_docs/`, and confirms/expands the evidenced action set; then two in-place reflex rewrites (stop-condition, method-switch), each a before/after on existing prose, body-only (no heading retitle), sized ≤ 0 tokens, no eval-inventory ripple.

### R2 — Stop-condition reflex (fold into `04 ## Execute, don't promise` or `05 ## Completeness`)
**Add (low-inference, names the anti-pattern):**
> Once you have delivered the answer or met every sub-goal, stop. Do not restate an answer you already gave, and do not take another step that adds nothing new.

Brakes the observed answer→save→re-answer loop without touching thoroughness-during-work. Folded in-place (offset by trimming any overlapping "continue until met" restatement so net tokens ≤ 0).

### R3 — Method-switch reflex (`04 ## Error recovery`)
**Add (low-inference, observable count-based cue):**
> If the same information goal has failed twice by the same method, switch method (a different tool — e.g. shell `curl` for a page that web fetch can't render), not a third variant of the same method.

Counters distinct-URL thrash the identical-call rule misses. Offset by tightening the existing two paragraphs.

## Tasks

**✓ DONE — TASK-0 — Whole-set reflex audit + codify the rubric (ASSESS; read-only)**
- files: `docs/exec-plans/active/2026-06-18-220445-rules-low-inference-reflexes.md` (append the audit table); a new `.agent_docs/` rubric note
- done_when: every one of the 28 rule sections is classified against the Authoring Standard rubric as **reflex** or **judgment-call**, each row naming its observable cue (or "no observable cue — requires inference: <which hop>") and a one-line verdict; the table is appended to this plan; R2/R3 are confirmed as the highest-value evidenced targets OR additional egregious-and-cheaply-gateable sections (incl. any *non-profile* recall defect with concrete evidence) are flagged for a scope decision; the stale `07 ## Recall` trigger phrasing "this user's specific setup or preferences" (`07:12-14`, left behind by the shipped USER.md rewrite — now the injected profile's job) is flagged as a sync-doc/accuracy fix; any concrete structural defect is noted for hand-off to the consolidation plan (structure is NOT edited here); the rubric is written as a one-paragraph note under `.agent_docs/`. No rule edit. Any section the audit flags beyond R2/R3 is recorded for a **Gate-1 scope decision and re-enters review — it is NOT actioned within this plan's delivery.**
- success_signal: a full-set map + a codified standard exist; action stays evidence-gated, not full-rewrite-on-faith.
- prerequisites: none (runs first; informs whether R2/R3 expand)

**✓ DONE — TASK-1 — R2: stop-condition reflex (the consolidation plan's sanctioned G2 path; ablation-gated if a probe fits, else smoke + review)**
- files: `co_cli/context/rules/04_tool_protocol.md` OR `co_cli/context/rules/05_workflow.md` (pick the natural home during dev; in-place, body-only, no new section, no heading retitle)
- done_when: a low-inference "stop when done; don't restate; don't take a no-op step" reflex is folded into an existing section without contradicting `01 ## Thoroughness over speed` / `04 ## Follow through` / `05 ## Execution` (it brakes redundant re-emission, not thoroughness-during-work); net static-floor token delta ≤ 0 (offset by trimming overlapping "continue until met" restatement); `test_instruction_floor_coupling` + `test_instruction_budget` pass; host `##` heading text unchanged so **no `_INVENTORY` edit** (count stays 28); repo-wide grep finds no stale anchor; full suite passes. **Gate:** if a single-turn `eval_rule_compliance.py` probe cleanly exercises "answered → does the model stop vs re-emit", ablation-gate at N=40 (≥ STEER_DELTA+1SE ≈ 0.59 per the consolidation plan's C4 noise floor), adding the `--samples` override only if not already present; otherwise behavioral-smoke-gate via a NEW smoke script (following the `tmp/weather_smoke.py` pattern) that reproduces the answer→save→re-answer double-answer and confirm a single final answer. Core-level review either way.
- success_signal: the answer→save→re-answer loop no longer produces a duplicate final answer.
- prerequisites: R2's home defaults to `04` (OQ-1), so single-owner serialization with TASK-2 on `04_tool_protocol.md` is the expected path; the only carve-out is if OQ-1 instead picks `05` (then TASK-1 is disjoint from TASK-2)

**✓ DONE — TASK-2 — R3: method-switch anti-thrash reflex (review-gated + smoke)**
- files: `co_cli/context/rules/04_tool_protocol.md`
- done_when: `## Error recovery` (or `## Strategy`) carries a low-inference reflex — after two failures at the same information goal by the same method, switch method, not a third same-method variant — phrased without `tool_name(` syntax; body-only, host `##` heading unchanged; net static-floor token delta ≤ 0 (offset by tightening the two existing paragraphs); `test_instruction_floor_coupling` + `test_instruction_budget` pass; **no `_INVENTORY` edit** (count stays 28); repo-wide grep finds no stale anchor; full suite passes. **Smoke:** a NEW smoke script (or the existing `tmp/weather_smoke.py`, location given) shows the model reach for shell `curl` after consumer-site fetches return nothing usable, instead of trying a 5th consumer site. Core-level review.
- success_signal: the weather query stops thrashing consumer sites and switches method.
- prerequisites: **single-owner sequencing with TASK-1 on `04`** — TASK-1 and TASK-2 both write `04_tool_protocol.md` (TASK-1 if OQ-1 picks `04`); serialize, do not parallelize, and floor-account `04` once after both.

## Testing
- Floor guards on every rule edit: `test_instruction_floor_coupling` (F5) + `test_instruction_budget` (budget ceiling). NOT `test_orchestrator_schema_budget` (tool-schema scoped, not rule files — CD-m-1).
- TASK-1/TASK-2 smoke uses a smoke script following the `tmp/weather_smoke.py` pattern (UAT smoke; tail the log; RCA-first on slow calls). TASK-1 ablation (if a probe fits) is N=40/arm ≈ 80 turns, short.
- No new behavioral pytest (no gap-fill adds a required behavior). No structural/fitness-function tests on rule files (`.agent_docs/review.md` Code Regulation Model).
- `--inventory` only if a task renames/adds/removes a `##` heading (none planned to — R2/R3 are body-only).

## Open Questions
1. **R2 home.** `04 ## Execute, don't promise` (turn-shape reflex, natural fit) vs `05 ## Completeness` (end-of-turn checklist, also fits). Default: `04 ## Execute, don't promise` — it already governs "what a response must be" (and keeps both reflexes in `04`, so the single-owner sequencing with TASK-2 is the expected path). Resolve in TASK-1.
2. **R2 gate.** Whether a single-turn probe can cleanly isolate "stop vs re-emit." Default: attempt a probe; if it can't separate from noise, fall back to smoke + review (do not fabricate a probe artifact). Resolve in TASK-1.

## Decisions

C1 inverted the original draft on two converging blockers: the "both plans edit `07`" premise was false (CD-M-1) and R1 had no live evidence once the profile case moved to USER.md (PO-M-1) — so R1 was cut and the plan reduced to audit + rubric + the two evidenced reflexes. C2 confirmed all resolutions with no new blockers.

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | Verified: USER.md plan edits memory code + `memory_review.md`, never `07`. No write collision; real item is the orphaned `user`-kind bullet. | Rewrote `## Coordination` to drop the false collision; TASK-0 flags the orphaned `07:48`/`07:13` prose for hand-off to the USER.md plan; removed OQ-4. |
| PO-M-1 | adopt | Converges with CD-M-1: R1 had no evidence once the profile case goes to USER.md; its gate was a fabricated scenario. | **Cut R1.** Renumbered R2→TASK-1, R3→TASK-2; removed the R1 design block + cascade-depth OQ; `07 ## Recall` rewrite moved to Out-of-scope pending TASK-0 evidence. |
| CD-m-1 | adopt | `test_orchestrator_schema_budget` guards tool-schema prefill, not rule files. | Dropped from the rule-edit guard list (Behavioral Constraints + Testing); kept `test_instruction_floor_coupling` + `test_instruction_budget`. |
| CD-m-2 | adopt | ~7.8k headroom; stale docstring figure. | Reframed ≤0 as anti-bloat discipline, not guard-forced; noted current ~17,186/25,000 floor. |
| CD-m-3 | adopt (moot via PO-M-1) | R1 cut → seeded-store harness concern moot; surviving smokes target R2/R3 with the right repros. | Smoke wording updated on TASK-1/TASK-2. |
| CD-m-4 | adopt | R2's default home is `04`, so the `04` collision with R3 is the expected path. | Single-owner `04` serialization made a firm prerequisite on both TASK-1 and TASK-2. |
| CD-m-5 | adopt | `_INVENTORY` keyed `(stem,title)` + hard count assert — a retitle breaks the eval. | Added "preserve `##` heading text verbatim; retitle ⇒ `_INVENTORY` update + `--inventory`"; TASK-1/TASK-2 marked body-only. |
| PO-m-1 | adopt | Consolidation TASK-5 sanctioned G2 as a future zero-growth tightening. | Reframed R2 as *executing* the sanctioned G2 path, not correcting an error (Context, Scope, TASK-1). |
| PO-m-2 | adopt | R1 cut. | Rewrote Failure cost around R2/R3/rubric. |
| PO-m-3 | adopt | Uncodified standard gets re-litigated. | TASK-0 writes the rubric as a firm `.agent_docs/` deliverable. |
| CD-m-1 (C2) | adopt | TASK-1's serialization note read softer than TASK-2's firm one. | Aligned TASK-1 prereq: `04` single-owner serialization is the expected path, `05` home the only carve-out. |
| PO-m-1 (C2) | adopt | Audit may flag sections beyond R2/R3. | TASK-0 done_when: flagged expansions re-enter review at Gate 1, not actioned within this delivery. |

## Final — Team Lead

Plan approved (C1 reduced the scope on two converging blockers; C2 confirmed, both `Blocking: none`).

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev 2026-06-18-220445-rules-low-inference-reflexes`

## Gate 1 — APPROVED 2026-06-19

PO + TL approved. Right problem (counter-the-limitation reflex redesign for small-model reliability), correct scope (increment 1: audit + rubric + 2 evidenced reflexes; whole-set rewrite explicitly deferred to evidence-gated follow-ups). Program intent charter added. Cleared to proceed.

→ Next: `/orchestrate-dev 2026-06-18-220445-rules-low-inference-reflexes`

## Gate-1 Rescan — 2026-06-19 (latest USER.md + prompt-refactoring state)

Re-validated the plan against the shipped USER.md work (`402203e1`, v0.8.418). **Verdict: plan still correctly scoped; two stale references fixed, no scope change, core action (TASK-0 audit + rubric, TASK-1 stop-condition, TASK-2 method-switch) unaffected.**

What changed since the plan was written, and the fix:
- **USER.md shipped** (kind enum now `rule|article|note`; `user_profile_{view,write}` + `co_cli/memory/user_profile.py` + dream write-back live). The ship **did rewrite `07_memory_protocol.md`** (the C1 Core Dev prediction that it wouldn't was wrong vs the actual implementation) — `## Explicit saves` rewritten with the profile flow, `## Kind selection` `user` bullet removed.
- **CD-M-1's orphaned-bullet hand-off is now MOOT** — the ship already removed it. Coordination section + TASK-0 updated.
- **New residual flagged:** `07 ## Recall` (`07:12-14`) still says "this user's specific setup or preferences," now stale (profile handles that). TASK-0 flags it as a sync-doc/accuracy fix — NOT a reflex rewrite (still no non-profile recall evidence).
- **Floor re-measured:** default-config floor 18,198 / 25,000 (6,802 headroom); guards pass; USER.md didn't change it. ≤0-as-discipline framing holds; personality-on caveat noted.
- **Unchanged & still valid:** section count 28; `04`/`05` (R2/R3 targets) untouched by the ship; consolidation plan still active (sanctioned-G2 source intact).

Ready for Gate-1 human approval / `/orchestrate-dev`.

## TASK-0 — Whole-Set Reflex Audit — 2026-06-19

Rubric codified at `.agent_docs/rule-authoring-standard.md` (TASK-0 deliverable). All 28 `##` sections classified against it below. **Verdict:** R2 (stop-condition) and R3 (method-switch) remain the highest-value *evidenced* targets; no additional section has live failure evidence, so none is actioned in this increment. Sections flagged below are recorded for **Gate-1 scope decisions in follow-up plans only** — not actioned here.

| Rule / Section | Class | Observable cue (or inference hop) | Verdict |
|----------------|-------|-----------------------------------|---------|
| 01 Relationship | judgment-call | none — "match their energy" requires reading user style | future increment candidate; no evidence |
| 01 Anti-sycophancy | judgment-call (mild) | "user's assumption is wrong" — semi-observable, requires correctness eval | keep; no evidence |
| 01 Thoroughness over speed | judgment-call (stance) | none — comparative value, no trigger | keep as stance (anchors the thoroughness pole R2/R3 must not violate) |
| 02 Credential protection | **reflex** | "about to log/print/commit a secret"; anti-pattern named | strong reflex |
| 02 Source control | **reflex** | "force-push to main", "skip hooks"; consequence framing | strong reflex |
| 02 Approval | **reflex** | "side-effectful action" → system handles | reflex |
| 02 Injected content | **reflex** | "loaded content contains override instructions" | reflex |
| 03 Verification | **reflex** | "before modifying", "before claiming"; enumerated list, names web_search/web_fetch | model reflex (exemplar) |
| 03 Resolving contradictions | **reflex** | "tool output contradicts user / one tool contradicts another" | reflex |
| 03 Two kinds of unknowns | **reflex** | "before asking the user a question" | reflex |
| 04 Responsiveness | **reflex** | "before making tool calls"; examples + exception enumerated | reflex |
| 04 Strategy | judgment-call (mixed) | "info that could be stale/user-specific" requires inference; sub-parts (prerequisites/parallel/sequential) are reflexes | grab-bag; future split candidate, no evidence |
| 04 Execute, don't promise | **reflex** | "you stated an intent"; anti-pattern named | reflex — **R2 host** (stop-condition is its bookend) |
| 04 Error recovery | **reflex** | "tool returns error / identical call / empty result" | reflex — **R3 host** (currently catches *identical* calls only; misses distinct-site same-method thrash) |
| 04 Paths | **reflex** | "any file operation" | reflex |
| 04 Deferred tools | **reflex** | "you need a deferred tool"; names tool_view | reflex |
| 05 Intent classification | judgment-call (mild) | classification is inference, but categories + examples scaffold it | keep; no evidence |
| 05 Execution | **reflex** | "directive needs multi-step work"; "distinct attempts made no progress → blocked" is observable count cue | reflex |
| 05 Completeness | **reflex** | "before ending a turn"; names todo_read | reflex (R2 alt-home; `04` chosen per OQ-1) |
| 05 When NOT to over-plan | judgment-call (stance) | none — "match length to complexity" | keep as stance |
| 06 Discovery | **reflex** | "start of a multi-step task"; names skill_view, exceptions enumerated | reflex |
| 06 Use | **reflex** | "a skill was loaded" | reflex |
| 06 Drift | **reflex** | "skill has stale steps/wrong commands"; enumerated | reflex |
| 06 Create | judgment-call (mixed) | "3+ steps" observable but "is it reusable" is the inference hop | future candidate, no evidence |
| 07 Recall | **judgment-call** | "when you suspect relevant context exists / recognize the topic but lack context" — 3-4 inference hops incl. know-what-you-don't-know | canonical judgment-call; **profile case owned by USER.md (shipped)**; no non-profile recall evidence → not rewritten. **Stale-phrasing flag below.** |
| 07 Explicit saves | **reflex** | "user explicitly asks to remember"; names memory_create/user_profile_*, disambiguation enumerated | reflex |
| 07 Curation | judgment-call (mixed) | correction/drift are cue-triggered reflexes; promotion's "useful finding" is the inference hop | mostly reflex; promotion is the soft part, no evidence |
| 07 Anti-patterns | **reflex** | "about to save X"; never-save list enumerated | reflex |

**Tally:** 19 reflex / 9 judgment-call (incl. mild/mixed). Reflex sections are well-designed already; the judgment-call set is the future-increment backlog — **none carries live failure evidence today**, so per the program charter none is rewritten in this increment.

**Stale-phrasing flag (sync-doc / accuracy, NOT a reflex rewrite):** `07 ## Recall` (`07:12-14`) still reads "or you recognize the topic but lack context for **this user's specific setup or preferences**" — but a user's setup/preferences are now the always-injected USER.md profile (shipped `402203e1`), not something to `memory_search` for. The shipped USER.md work rewrote `## Explicit saves` but left this trigger stale. Recommend a follow-up `/sync-doc`-style accuracy fix to drop "this user's specific setup or preferences" from the Recall trigger. In-section body phrasing only; no `##` retitle, no count change. **Not actioned in this plan** (no reflex rewrite of `07 ## Recall` per scope).

**Structural defects for consolidation-plan hand-off (NOT edited here):** `04 ## Strategy` is a grab-bag mixing a judgment-call stance ("bias toward action", "depth over breadth") with three crisp reflexes (prerequisites / parallel / sequential / follow-through) — a candidate split, hand off to the consolidation plan. The continue-until-met theme is stated three times (`04` Follow through, `05` Execution, `05` Completeness) — a dedup candidate for the consolidation plan's persistence cluster (C2). Structure is not edited in this increment.

## Delivery Summary — 2026-06-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | all 28 sections classified vs rubric; table appended; rubric codified in `.agent_docs/`; R2/R3 confirmed highest-value; stale `07 Recall` flagged; structural defects handed off; no rule edit | ✓ pass |
| TASK-1 (R2) | stop-condition reflex folded into `04 ## Execute, don't promise`; net floor delta ≤0; floor guards pass; heading unchanged (count 28); no stale anchor; smoke shows single final answer | ✓ pass |
| TASK-2 (R3) | method-switch reflex in `04 ## Error recovery`; no `tool_name(` syntax; net floor delta ≤0; floor guards pass; heading unchanged (count 28); no stale anchor; smoke shows curl method-switch | ✓ pass |

**Implementation notes:**
- **Rubric** codified at `.agent_docs/rule-authoring-standard.md` (the durable anti-erosion lever).
- **Audit:** 19 reflex / 9 judgment-call across 28 sections. No judgment-call section carries live failure evidence today → none rewritten beyond R2/R3, per the program charter. Backlog (Relationship, Strategy, Intent classification, Create, Recall, Curation) recorded for future evidence-gated increments.
- **R2** added to `04 ## Execute, don't promise`: *"Once you deliver that final result, stop. Do not restate an answer you already gave or take another step that adds nothing new."* Offset by deduping the triple-stated "continue until met" in `## Strategy` Follow-through.
- **R3** added to `04 ## Error recovery`: *"When the same goal fails twice by the same method, switch method — a different kind of tool, e.g. shell curl for a page web fetch cannot render — not a third same-method variant."* Offset by tightening the two existing recovery paragraphs.
- **Gate selection:** the eval `_INVENTORY` marks both `04 ## Execute, don't promise` and `04 ## Error recovery` OUT-OF-REACH for single-turn probes ("response shape" / "multi-turn retry"). Per OQ-2 default, both fell to **behavioral-smoke gating** — no fabricated ablation probe.

**Floor accounting (single owner, `04` accounted once after both edits):** base instruction floor net delta = **−2 chars** (≤0 ✓). `04_tool_protocol.md` 3085 → 3085 chars. Default-config floor 18,376 / 25,000 (CI guard, headroom ~6,624). All 6 `##` headings in `04` preserved verbatim; section count stays 28.

**Tests:**
- `test_instruction_budget.py::test_instruction_floor_within_budget` — ✓ pass
- `test_instruction_floor_coupling.py::test_no_deferred_tool_signature_on_floor` (F5) — ✓ pass
- `eval_rule_compliance.py --inventory` (parser self-test, 28-count + span uniqueness/reassembly) — ✓ pass
- Lint (`ruff check --fix` + format) — ✓ clean
- Repo-wide stale-anchor grep — ✓ no stale references

**Behavioral smoke (UAT, live Ollama, qwen3.6:35b-a3b-agentic):**
- **R3** (`tmp/weather_smoke.py`): after 4 consumer-site `web_fetch` attempts (AccuWeather/weather.com/wunderground/weatherapi) returned nothing usable, the model **switched method to shell `curl`** (wttr.in) and delivered a single clean forecast — never tried a 5th consumer site. ✓
- **R2** (`tmp/stop_condition_smoke.py`): a save-triggering query produced `memory_create` then **exactly one** final-answer text block — no re-emission, no duplicate. ✓

**Doc Sync:** none required for the reflex deliverable itself — only rule `.md` + a new `.agent_docs/` note touched; no shared module, API rename, or schema change. The stale `07 ## Recall` "this user's specific setup or preferences" phrasing was flagged (TASK-0) as a *future* sync-doc/accuracy fix, deliberately **not** actioned in the reflex increment.

**Pre-ship sync-doc (actioned 2026-06-19, before ship):** the two deferred spec-doc items were completed as the pre-ship sync pass:
1. **`07 ## Recall` stale phrasing fixed** — dropped "or you recognize the topic but lack context for this user's specific setup or preferences" from the Recall trigger (`07_memory_protocol.md:12-14`); that case is now the always-injected USER.md profile's job (shipped `402203e1`), not a `memory_search` target. Body-only, `## Recall` heading verbatim, net floor delta < 0. Floor guards (`test_instruction_floor_within_budget` + F5) ✓; `--inventory` 28-count held ✓; stale-anchor grep clean (remaining hits are historical references inside exec-plan docs) ✓.
2. **Reflex-standard cross-reference added** to `docs/specs/prompt-assembly.md` §2.1 — a coherence pointer noting the numbered rule files are authored as low-inference reflexes per `.agent_docs/rule-authoring-standard.md`.

This subsumes the `07 ## Recall` stale-phrasing task carried in the `rules-reflex-migration-backlog` plan (`2026-06-19-093706-...`:50,81) — that task is now done here; remove it from the backlog when that plan next re-enters review.

**Overall: DELIVERED**
Audit + rubric + both evidenced reflexes landed token-neutral (−2 chars), floor guards green, headings/count intact, and both reflexes behaviorally verified on live Ollama. Whole-set rewrite remains correctly deferred to evidence-gated follow-ups.

**Next step:** `/review-impl 2026-06-18-220445-rules-low-inference-reflexes` — full suite + evidence scan + behavioral verification → verdict appended to plan.

## Implementation Review — 2026-06-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | 28 sections classified vs rubric; table appended; rubric codified in `.agent_docs/`; R2/R3 confirmed; stale `07 Recall` flagged; structural defects handed off; no rule edit | ✓ pass | `.agent_docs/rule-authoring-standard.md:3` — one-paragraph 6-criterion rubric; plan audit table (19 reflex / 9 judgment-call = 28, matches `eval_rule_compliance.py` inventory count of 28); `04_tool_protocol.md` is the only rule file in this plan's diff (no audit-phase rule edit) |
| TASK-1 (R2) | stop-condition reflex in `04 ## Execute, don't promise`; net floor delta ≤0; floor guards pass; heading unchanged (count 28); no stale anchor | ✓ pass | `04_tool_protocol.md:41-42` — "Once you deliver that final result, stop. Do not restate an answer you already gave or take another step that adds nothing new." Low-inference, anti-pattern shown. Heading `## Execute, don't promise` verbatim. Offset by tightening `## Strategy` Follow-through (`04:32-33`) + the intro paragraph |
| TASK-2 (R3) | method-switch reflex in `04 ## Error recovery`; no `tool_name(` syntax; net floor delta ≤0; floor guards pass; heading unchanged; no stale anchor | ✓ pass | `04_tool_protocol.md:53-55` — "When the same goal fails twice by the same method, switch method … not a third same-method variant." Observable count cue, no `tool_name(` syntax. Heading `## Error recovery` verbatim. Offset by tightening the two existing recovery paragraphs |

### Issues Found & Fixed
No issues found.

_Scope note (non-blocking): `git diff HEAD` shows ~13 files beyond this plan's `files:` (orchestrate.py, assembly.py, 01/03/06 rule files, display/*, specs, etc.). These are uncommitted work from the other active plans in flight, not this delivery — left untouched. This plan's surface is exactly `co_cli/context/rules/04_tool_protocol.md` + the new `.agent_docs/rule-authoring-standard.md`._

### Tests
- Command: `uv run pytest -q`
- Result: 790 passed, 0 failed
- Floor guards: `test_instruction_floor_within_budget` ✓, `test_no_deferred_tool_signature_on_floor` (F5) ✓
- Inventory self-test: `eval_rule_compliance.py --inventory` ✓ (28-count + span uniqueness held)
- Lint: `scripts/quality-gate.sh lint` ✓ clean
- Stale-anchor grep: ✓ no references to removed "continue until all are met" / "identical arguments" phrasing
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads — edited rule files parse and assemble)
- R2/R3 reflexes are LLM-mediated (prompt-injected behavioral rules); verified in delivery via live-Ollama smokes (`tmp/stop_condition_smoke.py` — single final answer; `tmp/weather_smoke.py` — curl method-switch after 4 consumer-site fetches). `success_signal` for both confirmed there; chat interaction non-gating.

### Overall: PASS
Audit + rubric + both evidenced reflexes landed token-neutral, floor guards green, headings/count intact at 28, no stale anchors, full suite passes. PASS — ready for Gate 2 / `/ship`.
