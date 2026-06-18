# Skill-Reviewer and Merge Cognition Evals

## Context

Follow-on to `dream-reviewer-cognition-eval` (W3.R, shipped 2026-06-18). That review of the
eval/pytest partition for the dream/memory subsystem surfaced two remaining cognition gaps —
both LLM-judgment surfaces with no real-model eval, structurally identical to the hole W3.R
just closed for memory-reviewer extraction.

Source verified for this plan (2026-06-18):

- `co_cli/daemons/dream/_reviewer.py:144` — `process_review(deps, domain, session_id,
  persisted_message_count, transcript_override=None)`; `domain="skill"` dispatches to
  `_run_skill_review` (`:178`). Same entry path W3.R drives with `domain="memory"`.
- `_run_skill_review` (`:125`) serializes with `include_tool_results=False`, forks deps via
  `fork_deps_for_reviewer` (`co_cli/deps.py` → `fork_deps`), calls `refresh_skills(child_deps)`
  so the reviewer sees current skill state, then runs `SKILL_REVIEW_SPEC`. The fork is the same
  one W3.R relies on to preserve `memory_dir`; whether it preserves `user_skills_dir` so a
  reviewer-created skill lands in the parent's dir is the load-bearing source fact TASK-1 must
  confirm before the structural gate can be trusted (mirror of the `:441` memory_dir check).
- `co_cli/daemons/dream/prompts/skill_review.md` — reviewer prompt. **Bias: "be ACTIVE — most
  sessions produce at least one update."** Preference order is UPDATE-loaded > UPDATE-umbrella >
  CREATE-new. First-class CREATE/PATCH signal is a user **correction/frustration** about HOW a
  task was handled ("stop doing X", "don't format like this") or a corrected workflow/step. The
  reviewer has create/edit/patch tools, **no delete**. Returns `SessionReviewOutput`
  (`_reviewer.py:22` — `skills_patched`, `skills_created`).
- `co_cli/daemons/dream/_housekeeping.py:184` — `merge_memory(deps, state)`. Two-stage:
  `_identify_mergeable_clusters` (deterministic Jaccard gate on
  `consolidation_similarity_threshold`, `:106`) → `_merge_cluster` (the **LLM** sub-agent that
  fuses a cluster). Clusters below the Jaccard threshold never reach the model.
- `evals/eval_daily_chat.py:280` — W1.D `_case_w1_d_dream_propagates_to_recall`: seeds two
  near-identical bodies (`_DREAM_A_BODY`/`_DREAM_B_BODY`, alpha/beta, `:103`) sharing
  `_DREAM_SHARED_TOKEN`, runs `merge_memory` under `DREAM_CYCLE_BUDGET_S`, asserts one archived +
  token-in-merged. Helpers `_seed_dream_pair`/`_purge_dream_pair` (`:110`,`:117`). W1.E
  (`_case_w1_e_tool_spill_summary`) already exists — next free id is **W1.F**.
- `evals/eval_skills.py` — skill-domain eval home (W4.A dispatch, W4.B selection). `main()`
  (`:372`) warms ollama once (`:378`) then iterates a case tuple. Has `judge_with_llm`,
  `judge_model_annotation`, `CaseResult`, `Verdict`, `refresh_skills`, `deps.user_skills_dir`
  already imported/in-scope.
- W3.R reference implementation: `evals/eval_memory.py:114` `_seed_reviewer_transcript`
  (real `ModelRequest`/`ModelResponse` via `append_messages`, per-run token, stale-fixture
  purge by deterministic uuid8) and `:338` the case (snapshot dir → seed → `process_review`
  under `DREAM_CYCLE_BUDGET_S` → set-diff structural gate on token → `judge_with_llm`
  faithfulness). Both new cases follow this shape.
- Centralized budget: `evals/_timeouts.py` `DREAM_CYCLE_BUDGET_S` (240s), `CALL_TIMEOUT_S`.

## Problem & Outcome

Two LLM-judgment surfaces of the dream system have **zero real-model coverage**:

1. **Skill-reviewer extraction** (`process_review(domain="skill")` → `_run_skill_review` → the
   `skill_reviewer` agent writing/patching a user skill). The exact symmetric blind spot W3.R
   closed for memory. Every existing test of the skill path forces the transcript-absent early
   return before any LLM call.

2. **Merge over-merge (false-positive direction).** W1.D proves the merge agent *fuses a genuine
   duplicate pair* (the items-that-should-merge-do direction). It does **not** prove the agent
   *preserves distinct facts* when handed a lexically-similar-but-semantically-distinct cluster.
   The deterministic Jaccard clustering gate is pytest-covered
   (`test_consolidated_write_honors_configured_threshold`); what is uncovered is the **model's**
   judgment once a cluster passes that gate.

**Outcome:** two judged eval cases — one in `evals/eval_skills.py` driving
`process_review(domain="skill")` end-to-end against a seeded correction transcript, one in
`evals/eval_daily_chat.py` (W1.F) driving `merge_memory` against a lexically-similar /
semantically-distinct pair — each with a structural gate plus an LLM-judged rubric.

**Failure cost:** a skill-reviewer regression (failing to encode a clear user correction, or
fabricating a procedure the user never gave) ships undetected — the self-improving-skills feature
silently stops learning. A merge regression that conflates distinct facts into one item
(dropping a fact, or citing a fused item as if it were one source) silently corrupts memory; W1.D
stays green because it only tests the merge-succeeds direction, giving false "merge is covered"
confidence.

## Scope

In scope:
- Skill-reviewer case in `evals/eval_skills.py`: seed a transcript carrying one clear, reusable
  user correction keyed by a per-run token; run the real skill reviewer; gate that a user skill
  was created or patched carrying the token; LLM-judge faithfulness (encodes the correction; does
  not fabricate).
- Merge over-merge case (W1.F) in `evals/eval_daily_chat.py`: seed a pair that **passes the
  Jaccard gate** (lexically similar enough to cluster) but carries **two distinct facts**; run
  `merge_memory`; assert the model does not silently drop a fact; LLM-judge that both facts
  survive (whether kept as two items or fused without loss).

Out of scope:
- Any production source change — eval-layer only.
- Daemon/queue/lifecycle mechanics and the deterministic Jaccard clustering gate (already
  pytest-covered; do not duplicate).
- Decay cognition (W10.C already covers it, SOFT-only by design).
- `test_override_snapshot.py` monkeypatch grey-zone (review observation #3) — a pytest-hygiene
  note, not an eval gap; not addressed here.

## Behavioral Constraints

- **No flag needed.** Drive `process_review` / `merge_memory` directly, same as W3.R / W1.D. Do
  not set `review_enabled` or route through a daemon.
- **Real side effects, no cleanup** (`feedback_eval_real_world_data`): produced skills/items are
  left in the real store. Per-run unique token + deterministic stale-fixture purge so reruns are
  idempotent without a cleanup pass (W3.R / W1.D pattern).
- **Curation lens is live** for the skill reviewer too. Rubric judges faithfulness, not voice.
- **Tool-result fidelity.** `include_tool_results=False` — the durable signal must live in
  user/assistant text, not tool results.
- **Skill-reviewer structural gate must tolerate CREATE *or* PATCH.** The prompt prefers updating
  an existing skill; the real store has skills. Gate on the per-run token appearing in any user
  skill body (new file OR modified existing), not strictly on a new file. The token must be the
  *substance* of the correction (a procedural step/preference keyed by the token) so the reviewer
  embeds it when encoding — a bare opaque marker the reviewer would paraphrase away fails the gate
  for the wrong reason. This is the skill analog of W3.R's "durable signal IS the token content."
- **Merge case must clear the Jaccard gate *by construction*, or it tests nothing new.** If the
  seeded pair is too dissimilar it never clusters and `merge_memory` returns 0 deterministically —
  that path is already pytest-covered and the case would be redundant. The pair must be lexically
  similar enough to cluster (model judgment invoked) yet carry two distinct facts (the thing under
  test). **The "cluster reached the model" property is established by construction, not by a
  runtime signal**: `merge_memory` returns only an `int` count of *committed* merges
  (`_housekeeping.py:184`,`:213`), and `_merge_cluster` returning `None` for a too-short body
  (`:174`–`:179`) collapses into the same `merged_count == 0` as "never clustered" — so the public
  return cannot distinguish "clustered but model declined" from "never clustered." Therefore the
  seed pair must be built **exactly like the W1.D pair** (`eval_daily_chat.py:103`–`:104`, shared
  lexical stem, Jaccard ≈0.78 ≥ the 0.75 threshold, with a token-intersection comment) so the
  deterministic Jaccard gate *guarantees* clustering. Clustering being guaranteed by construction,
  any outcome (`merged_count ≥ 1` = model fused; `merged_count == 0` = model judged keep-distinct)
  means the model was reached — and the fact-preservation judge over surviving bodies is the real
  test.
- **Timeout discipline** (`feedback_long_llm_call_rca_first`): wrap reviewer/merge calls in
  `DREAM_CYCLE_BUDGET_S`, judge calls in `CALL_TIMEOUT_S`. Do not coin new budgets. A stall is an
  RCA target, never a widen. `ensure_ollama_warm()` is called once at each eval's `main()`
  entrypoint — do not add a second call inside a case (`feedback_ensure_ollama_warm`).
- Centralized settings/deps/timeouts (`feedback_evals_centralized_settings`).

## High-Level Design

Two judged cases, each mirroring the W3.R artifact-quality shape (seed → snapshot → run real path
under budget → set-diff/token structural gate → `judge_with_llm` against a rubric):

- **Skill case (eval_skills.py):** `_seed_skill_reviewer_transcript` writes a transcript where the
  user corrects HOW a class of task is done, keyed by a per-run token embedded as the substance of
  the correction (e.g. "from now on always run `<token>` before deploying — stop skipping it").
  Snapshot `deps.user_skills_dir` skill-body set → `process_review(deps,"skill",stem,None)` under
  budget → gate: token appears in some user skill body (created or patched) → judge faithfulness.
- **Merge case (eval_daily_chat.py, W1.F):** `_seed_distinct_pair` writes two items lexically
  similar enough to clear the Jaccard gate but carrying two distinct facts (each keyed by its own
  per-run sub-token, sharing a lexical stem). Run `merge_memory` under budget → assert merge was
  *attempted* (cluster reached model) → judge that **both** distinct facts survive the pass
  (whether as two items or one lossless fused item). Reuse `_purge`-style idempotency.

## Tasks

### ✓ DONE TASK-1 — Skill-reviewer cognition case

- files: `evals/eval_skills.py`
- Add the imports the W3.R pattern requires (none are present in `eval_skills.py` today):
  `from co_cli.daemons.dream._reviewer import process_review`,
  `from co_cli.session.filename import session_filename`,
  `from co_cli.session.persistence import append_messages`, the four
  `pydantic_ai.messages` part types (`ModelMessage`, `ModelRequest`, `ModelResponse`,
  `UserPromptPart`, `TextPart`), and add `DREAM_CYCLE_BUDGET_S` to the existing
  `evals._timeouts` import (currently `CALL_TIMEOUT_S, TOOL_TURN_BUDGET_S`, `eval_skills.py:41`).
- First confirm in source that `fork_deps_for_reviewer` preserves `user_skills_dir` so a
  reviewer-created/patched skill lands in the parent `deps.user_skills_dir` (the dir the case
  snapshots). If it does not, STOP and escalate — the structural gate is untrustworthy and the
  plan needs revision (do not work around it).
- Add `_seed_skill_reviewer_transcript(sessions_dir) -> tuple[Path, str]` modeled on
  `eval_memory.py:_seed_reviewer_transcript` (real `ModelRequest`/`ModelResponse` via
  `append_messages`, deterministic fixture uuid8, stale-purge, per-run token). Transcript carries
  one clear, reusable user **correction** about how a class of task is handled, with the per-run
  token as the substance of the corrected step (so the reviewer embeds it). Signal in
  user/assistant text only.
- Add `case_skill_reviewer_encodes_correction(deps, agent, frontend, run)` (keep 4-arg
  tuple-dispatch signature; note `agent`/`frontend` unused — drives `process_review`). Snapshot
  the set of `(skill_path, body)` under `deps.user_skills_dir`; seed; derive `session_id =
  path.stem`; `async with asyncio.timeout(DREAM_CYCLE_BUDGET_S): await process_review(deps,
  "skill", session_id, persisted_message_count=None)`. Structural gate: the per-run token appears
  in a user skill body that did not contain it before (created OR patched); FAIL fast otherwise.
  Judge faithfulness via `judge_with_llm(rubric, [{"role":"assistant","content": skill_body}],
  deps=deps, model=deps.judge_model)` — PASS if it faithfully encodes the correction, FAIL if it
  fabricates a procedure absent from the transcript or distorts it. Emit `judge_model_annotation`.
  Map judge-not-passed to `SOFT_FAIL`, structural-gate-miss to hard `FAIL` (W3.R convention).
- done_when: `uv run python evals/eval_skills.py` runs the case to a verdict AND a user skill body
  carrying the per-run token (created or patched) exists under `deps.user_skills_dir` after the
  run. Re-run attribution is guaranteed by the per-run unique token; note that a PATCH into a
  pre-existing real skill is **not** reverted by the stale-fixture purge (only CREATE-into-the-
  fixture-file is), so a patched body may accumulate prior runs' tokens — acceptable because the
  current run's token is uniquely attributable; the gate keys on this run's token only.
- success_signal: case reaches a PASS/FAIL verdict with a judge score in run output; the encoded
  skill body carrying the token is on disk.
- prerequisites: none

### ✓ DONE TASK-2 — Merge over-merge (false-positive) case

- files: `evals/eval_daily_chat.py`
- Add `_seed_distinct_pair`/`_purge_distinct_pair` (modeled on `_seed_dream_pair`,
  `eval_daily_chat.py:110`,`:117`). Build the pair **exactly like the W1.D pair**
  (`:103`–`:104`): shared lexical stem giving Jaccard ≈0.78 ≥ the 0.75 threshold (carry a
  token-intersection comment proving the ratio), same kind — so the deterministic Jaccard gate
  *guarantees* clustering by construction. Diverge from W1.D only in payload: each item carries a
  **distinct fact** keyed by its own per-run sub-token (the two facts must be genuinely different,
  not alpha/beta variants of one fact).
- `HousekeepingState` needs no new import — it is already imported in `eval_daily_chat.py:54`
  (defined in `co_cli/daemons/dream/_state.py`, not `_housekeeping.py`).
- Add `_case_w1_f_merge_preserves_distinct_facts(deps, agent, frontend, run)`. Purge + seed;
  snapshot active `*.md`; `async with asyncio.timeout(DREAM_CYCLE_BUDGET_S): merged =
  await merge_memory(deps, HousekeepingState())`. Clustering is guaranteed by the seed
  construction (above), so the model was reached regardless of `merged` count — do **not** add an
  unobservable runtime "cluster reached" guard (`merge_memory`'s `int` return cannot distinguish
  declined-merge from never-clustered). Judge: across the surviving active item bodies, **both**
  distinct facts are still present (two items kept, OR one losslessly-fused item retaining both —
  neither dropped, neither invented). `judge_with_llm` over the surviving bodies; PASS if both
  facts survive, FAIL if a fact was dropped or fabricated. Emit `judge_model_annotation`;
  SOFT_FAIL on judge-not-passed, hard FAIL on the seed/snapshot setup failing.
- done_when: `uv run python evals/eval_daily_chat.py` runs W1.F to a verdict; the run reason
  records `merged=<count>` and the judge's fact-preservation score.
- success_signal: W1.F reaches a PASS/FAIL verdict with a judge score and the `merged=` count in
  run output.
- prerequisites: none

### ✓ DONE TASK-3 — Register both cases + smoke

- files: `evals/eval_skills.py`, `evals/eval_daily_chat.py`
- Add `case_skill_reviewer_encodes_correction` to the `eval_skills.py` `main()` case tuple and
  `_case_w1_f_merge_preserves_distinct_facts` to the `eval_daily_chat.py` `main()` case tuple.
- done_when: each new case appears in its eval's run summary with a verdict and emits its run
  output record under `evals/_outputs/` (per the W3.R TASK-3 note: a `-case_<id>.jsonl` trace
  artifact is produced only for agent-turn cases via `record_turn`; a `process_review`/
  `merge_memory` case with no agent turns is correctly recorded as its verdict line in the
  `<scenario>-<ts>-run.jsonl` — that satisfies "recorded").
- success_signal: one full `uv run python evals/eval_skills.py` and one
  `uv run python evals/eval_daily_chat.py` each show the new case with a verdict.
- prerequisites: TASK-1, TASK-2

## Testing

- `uv run python evals/eval_skills.py` and `uv run python evals/eval_daily_chat.py` complete; the
  new cases reach a verdict.
- A user skill carrying the per-run token exists under `deps.user_skills_dir` after the skill run;
  the merge run reason shows the cluster reached the model and both facts survived.
- Reruns are idempotent (stale-fixture purge + unique-token scoping).
- No production source touched; `scripts/quality-gate.sh lint` clean.
- Tail each run log to watch reviewer/merge LLM-call timing (`feedback_tail_log_every_test_run`).

## Open Questions

- **Will the skill reviewer reliably CREATE/PATCH from a single short transcript?** The prompt is
  CREATE-biased ("most sessions produce at least one update") and a correction is a first-class
  signal, so a clear correction transcript should reliably trip it — but real-model variance is a
  known risk. If the case proves flaky in delivery, the seed transcript (correction clarity), not
  the gate or a timeout, is the RCA target. Resolve in dev, not here.
- **Is the over-merge direction better tested at the cluster boundary or post-merge bodies?** —
  RESOLVED (C1, PO-m-1): judge the surviving bodies for fact-preservation, agnostic to whether the
  model kept two items or fused losslessly. The user-observable invariant is "no fact dropped." A
  stricter "must keep distinct items separate" was rejected — the merge agent is explicitly allowed
  to fuse, so that assertion would produce false failures.

## Decisions

| Issue ID | Decision | Rationale | Change |
|----------|----------|-----------|--------|
| CD-M-1 | adopt | `merge_memory`'s `int` return cannot distinguish declined-merge from never-clustered, so a runtime "cluster reached" guard is unsatisfiable. | Behavioral Constraints + TASK-2: reframed to guarantee clustering *by construction* (W1.D-style Jaccard ≈0.78 seed); removed the unobservable runtime guard; `done_when` now records `merged=<count>`. |
| CD-M-2 | adopt | `eval_skills.py` imports none of the W3.R-pattern symbols today — silent `NameError`/`ImportError` risk. | TASK-1: added explicit import-addition step (`process_review`, `session_filename`, `append_messages`, four `pydantic_ai.messages` part types, `DREAM_CYCLE_BUDGET_S`). |
| CD-m-1 | adopt | `HousekeepingState` lives in `dream/_state.py` and is already imported in the target eval. | TASK-2: noted no new import needed (`eval_daily_chat.py:54`). |
| CD-m-2 | adopt | PATCH into a pre-existing real skill is not reverted by stale-purge; per-run token still guarantees attribution. | TASK-1 `done_when`: documented PATCH-body non-purge as acceptable; gate keys on this run's token only. |
| PO-m-1 | adopt | Stricter "keep distinct items separate" would test a behavior the merge agent is allowed to do (fuse). | Open Q2 resolved affirmatively: judge surviving bodies for fact-preservation, lossless-fuse-or-keep-distinct. |
| PO-m-2 | reject | — (no change requested; confirms two-case scope is one-case-per-behavior, consistent with eval-as-smoke). |
| PO-m-3 | reject | — (no change requested; confirms TASK-1 is the keeper if delivery forces a cut). |

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `eval_skills.py` runs the case to a verdict + a user skill carrying the per-run token exists | ✓ pass — **W4.R PASS** (judge score 10); `~/.co-cli/skills/deploy/SKILL.md` carries `make verify-3e35882aefc1` |
| TASK-2 | `eval_daily_chat.py` runs W1.F to a verdict recording `merged=` + judge score | ✓ pass — **W1.F PASS** (re-verified 2026-06-18 after the merge refactor landed): `merged=1 surviving_bodies=1 judge.score=10`, both distinct facts preserved in the fused note |
| TASK-3 | both cases registered + appear in run summary with a verdict | ✓ pass — both registered; W4.R and W1.F both green |

**Tests (re-verified 2026-06-18, post-refactor):** scoped evals — `eval_skills`: W4.A PASS, **W4.R PASS** (new, judge score 10), W4.B FAIL (pre-existing model-selection variance, untouched by this change). `eval_daily_chat`: **W1.A PASS, W1.D PASS, W1.F PASS** (W1.E retired by the merge refactor — commit `c26e33c0`). The "all FAIL" line below was the *original* mid-refactor delivery state, now superseded — see the Re-verification addendum.
**Lint:** clean.
**Doc Sync:** not run (eval-only change, no shared module / schema / public API touched).

**Pre-existing breakage fixed (in scope + 1 announced extra file):**
- `evals/eval_daily_chat.py` and `⚠ evals/eval_user_model.py`: stale import `from co_cli.daemons.dream._state import HousekeepingState` → `...dream.state import...`. The module was renamed `_state.py` → `state.py` by the in-flight working-tree refactor (correct per the underscore-visibility contract — it is imported across packages) but the eval imports were left pointing at the old name, a guaranteed `ModuleNotFoundError`. (Note: the plan's CD-m-1 citation said `_state.py`; that was wrong — the live module is `state.py`.)

**RCA — why TASK-2 is blocked (environmental, not the case):**
The `eval_daily_chat` merge/compaction subsystem has **uncommitted, mid-refactor edits** (`co_cli/daemons/dream/_housekeeping.py`, `state.py`, `process.py`) that independently break **three sibling cases I did not touch**: W1.D (`one_archived` assumption — merge now archives *all* cluster members + writes a fresh consolidated item, so both seeds archive), W1.E (spill not triggered), and W1.A (model produced no third turn this run). My diff is purely additive (verified: sibling logic untouched).

W1.F drives `merge_memory`, so it inherits that instability. Crucially, **the behavior under test is actually correct** — on disk the merge preserved *both* distinct facts losslessly: `eval_w1f_distinct_b-1.md` body = *"…certificate<run> march and baseline backups<run> sunday"*. The case still reports `surviving_bodies=0` because the refactored **write-then-archive + `-1` collision-suffix** file ordering (consolidated item written to a collision-suffixed name while the seeds are concurrently archived) defeats a clean post-merge read, compounded by cross-run store pollution.

Two genuine gate-robustness fixes were already applied to the case during dev (both correct, both kept): (1) the per-run token is now folded into the *substance* of each fact (`backups<run>`, `certificate<run>`) so the merge — which drops filler but preserves facts — cannot paraphrase it away; (2) candidate location is now agnostic to where the merge writes (scan all active bodies for the run token, not a `before`/`after` set-diff). These fixed the original "filler token paraphrased away" miss, but cannot compensate for the merge subsystem itself being unstable.

**Overall (original delivery state): BLOCKED.** TASK-1 delivered and verified. TASK-2's case is authored to plan and lint-clean, but **cannot reach a trustworthy verdict until the in-flight merge/compaction refactor lands and W1.D/W1.E/W1.A go green again** — this is an environment block, not a plan or code-authoring defect. Recommended next step: re-run `eval_daily_chat` once that refactor is committed and its own eval cases pass; no plan revision needed.

### Re-verification addendum — 2026-06-18 (block resolved)

The environment block is resolved. The merge/compaction refactor landed and was committed (`c26e33c0 test(evals): fix W1.D merge gate, retire W1.E spill case`; `2e38bdbd` adds the skill-reviewer case); `dream/_state.py` → `dream/state.py` is committed and the eval imports point at it. W1.E was retired as part of the new merge/spill semantics, so the plan's "next free id is W1.F" framing stands but its W1.E references are now historical.

Re-ran both evals against committed `main`:

- `eval_skills`: **W4.A PASS**, **W4.R PASS** (judge score 10, skill=`deploy`), W4.B FAIL (pre-existing model-selection variance — unrelated, untouched).
- `eval_daily_chat`: **W1.A PASS**, **W1.D PASS** (`merged=1 archived_a=True archived_b=True token_in_merged=True` score 10), **W1.F PASS** (`merged=1 surviving_bodies=1` score 10 — the model fused the pair into one lossless note keeping both the certificate-in-March and backups-on-Sunday facts).

The original RCA's prediction held exactly: the behavior under test was always correct; only the mid-refactor file-ordering instability (and the pre-fix `surviving_bodies=0` read miss) blocked a clean verdict. Both gate-robustness fixes (token folded into fact substance; location-agnostic surviving-body scan) are in the shipped case and carried W1.F to a clean PASS.

**Overall: DONE.** All three tasks delivered and verified. Ready for Gate 2 / ship.

> Note: the Decisions-table row **CD-m-1 still cites `_state.py`** — the live module is `dream/state.py` (the Delivery Summary's "Pre-existing breakage fixed" note already flagged this). A spec-fidelity nit, not a behavior issue.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev skill-reviewer-and-merge-cognition-evals`
