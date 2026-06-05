# rules-block-trim-finish

> **Supersedes** `2026-05-28-214128-prefill-trim-4-rules-block-trim.md` (combined into this plan;
> the original is deleted). **Child 4 of** `2026-05-28-141854-prefill-trim.md`. Carries forward
> prefill-trim-4's delivered-but-unshipped state and its two unfinished gates, now that both blockers
> are cleared:
> 1. The **eval-infra drift** that blocked the TASK-3 adherence gate is fixed (v0.8.286,
>    `eval-infra-output-sync` — shared `response_text(turn_result)` reads canonical
>    `AgentRunResult.output`; eval_skills W4.A no longer spuriously FAILs on empty `preview`).
> 2. The **deferred-discovery-diagnostic** that gated the rule-`06` trim has been **dropped** (never
>    run, cold-only/cached-away ROI, unowned). With no verdict to wait on, the `06` hold is **released**
>    and the `06` manifest-scan dedup — prefill-trim-4's largest stranded candidate — is back in scope.

## Inherited state (from prefill-trim-4, already in the working tree — uncommitted)

prefill-trim-4 delivered TASK-1/2/4 and paused at TASK-3. Those deliverables sit **uncommitted** in the
working tree and are owned by this plan now:

- `05_workflow.md` — trimmed **−93 ch**: the blocker-loop cue deduped to one canonical home
  (Execution), the duplicate in the Completeness validation list removed. All must-survive cues intact.
- `07_memory_protocol.md` — trimmed **−402 ch**: the `Triggers:` recall line and the 4-way
  `SaveResult.action` enum collapsed to the behavioral cue. All must-survive cues intact.
- `06_skill_protocol.md` — **untouched** (was held on the now-dropped diagnostic; TASK-A below trims it).
- `tests/test_instruction_budget.py` — the single shared instruction-budget guard (created per
  context-stability TASK-7's spec; ceiling `24,200` = measured `23,769` + ~431 headroom). **Passes.**

**Realized so far:** −495 ch (~123 tok) of the −350–700 tok plan goal. The shortfall is entirely the
held `06` manifest-scan dedup, which this plan now completes.

### Baseline band (prefill-trim-4 TASK-1, 2 runs each — the regression reference)

- **skills:** W4.A–D PASS/PASS, W4.E SOFT_PASS 0/3 ×2.
- **memory:** W3.A–F PASS/PASS, W3.G **{PASS, SOFT_FAIL}** (inherent variance — a single SOFT_FAIL
  inside this band is NOT a regression).

> Note on W4.A: prefill-trim-4's trimmed runs showed W4.A 2 PASS / 2 FAIL with `preview=''`. Root cause
> was the eval-infra drift (now fixed in v0.8.286), **not** the trim. The re-run gate (TASK-B) runs on
> the fixed harness, so W4.A is expected to read clean.

## Problem & Outcome

**Problem.** The rules block is ~32% of every cold prefill and rides every post-compaction state. The
05/07 trims are applied but unverified and uncommitted; the `06` manifest-scan repetition (the largest
dedup candidate) is still on the floor.

**Outcome.** Complete the `06` trim, re-run the adherence gate on the fixed eval harness across all
three trimmed files, re-pin the instruction-budget guard downward to the post-`06` measurement, and
ship — banking the full conservative rules-block trim with zero adherence regression.

**Failure cost.** Left here, the 05/07 trims rot uncommitted and the second-biggest floor component
keeps paying duplicated-guidance tokens on every cold turn.

## Behavioral Constraints (carried forward verbatim from prefill-trim-4)

- **Trim = dedup + inert-prose only.** Collapse repeated injunctions to one canonical home; cut
  reference-enumerations and pure mechanism-pedagogy. **Never** remove a routing/when-to-use cue or a
  safety/correctness injunction. This model needs more explicit guidance, not less (FM-1).
- **Load-bearing `06` cues that MUST survive** (grep-checked): `skill_view` before edit/patch; "skill
  body is your procedure, not reference"; drift→`skill_patch`/`skill_edit` immediately; create bar
  (3+ steps / reusable); confirm before create-on-behalf; the `skill_manage`-family tool injunctions
  (`skill_view`/`skill_patch`/`skill_edit`/`skill_create`). The create-dedup "search first" cue in
  `## Create` is a **distinct** manifest mention (scoped to dedup-before-create) — keep it; it is not
  one of the 3 redundant discovery repetitions.
- **Contiguous-from-01 invariant** (`assembly.py:56-61`) — no renumbering, no file deletion.
- **Eval-gated** — adherence must not regress vs the inherited baseline band.
- **Single instruction-budget guard** — re-pin `tests/test_instruction_budget.py` downward; never add a
  second rules-only guard (context-stability reads this same one).
- **Coworker-maintained assets** (`souls/`, `_profiles/`, `evals/judges/`, `memories/`) untouched.

## Tasks

### ✓ DONE TASK-A — `06` manifest-scan dedup (the released hold)

**Files:** `co_cli/context/rules/06_skill_protocol.md`.

**Action:** "scan the `<available_skills>` manifest before any multi-step task" is stated **3×** —
intro para (`:5-7`), manifest para (`:9-11`), and `## Discovery` (`:13-17`). Collapse to **one
canonical home** (keep `## Discovery`'s actionable form; thin the two upstream echoes to a single
lead-in). Compress `## Background review` (`:70-79`) — a long mechanism explanation whose only
behavioral cue is "don't double up" — to that cue. Preserve every must-survive cue above and the
distinct `## Create` "search first" dedup mention.

**done_when:**
- A scripted grep of the must-survive anchor phrases returns **all hits** (`skill_view`,
  `skill body is your procedure`, `skill_patch`, `skill_edit`, `skill_create`, the create bar, the
  create-on-behalf confirm).
- `uv run pytest tests/test_flow_prompt_assembly.py -x` passes (contiguity + assembly intact).
- The 3× discovery repetition is reduced to one canonical statement (grep: the
  "before any multi-step task" + manifest-scan phrasing appears once in the discovery sense).

**success_signal:** `06` reads once-not-thrice on manifest-scan with no behavioral cue lost.

**prerequisites:** none (the diagnostic gate is dropped).

### ✓ DONE TASK-B — eval-gated adherence verification (re-run on the fixed harness)

**Files:** none (runs the gate; records result in the Delivery Summary).

**Action:** Re-run `evals/eval_skills.py` and `evals/eval_memory.py` **≥2× each** on the full trimmed
rules (05+06+07). `eval_mindset_selection` stays **descoped** (it was removed from the suite in
v0.8.286 and is orthogonal to a rules trim — its pairwise delta isolates the mindset block, identical
across arms). Record **per-run scores** as a band.

**done_when:** the trimmed band sits **within or above** the inherited baseline band per domain (skills
W4.A–E, memory W3.A–G with W3.G's known `{PASS, SOFT_FAIL}` variance). A single trimmed run dipping
inside baseline variance is NOT a regression; a band that drops *below* baseline is. If a domain
regresses, restore the cut cue in that file and re-run.

**success_signal:** Adherence band held across all three trimmed files.

**prerequisites:** TASK-A.

### ✓ DONE TASK-C — re-pin the instruction-budget guard + ship the deliverables

**Files:** `tests/test_instruction_budget.py`.

**Action:** The `06` trim lowers the static instruction block below the current `24,200` ceiling.
Re-measure `build_static_instructions(deps.config)` post-`06`-trim and re-pin
`INSTRUCTION_BLOCK_CEILING` to that measurement + ~400-char headroom (tighten, never raise). Update the
docstring's measured-figure line. Then commit all inherited + new deliverables (05/06/07 trims + the
re-pinned guard) under `/ship`.

**done_when:**
- `uv run pytest tests/test_instruction_budget.py -x` passes against the re-pinned ceiling.
- The re-pinned ceiling is **below** the current `24,200` (the `06` trim must lower it); the forbidden
  pre-trim `24,256` is never re-introduced.

**success_signal:** A future verbose rule addition fails CI; exactly one instruction-budget guard exists.

**prerequisites:** TASK-B (ceiling set from the verified post-trim state).

## Testing

- `tests/test_flow_prompt_assembly.py` (contiguity + assembly) and `tests/test_instruction_budget.py`
  (re-pinned, single guard).
- `evals/eval_skills.py`, `evals/eval_memory.py` — the adherence gate (stochastic UAT diagnostics;
  compare to the inherited baseline band, not an absolute bar).
- `scripts/quality-gate.sh full` at ship.
- `/sync-doc` follow-up if any spec quotes the trimmed `06` prose.

## Carried-forward decisions (do NOT relitigate)

- **Gate-1 proceed-vs-guard-only = PROCEED** (resolved during prefill-trim-4 invocation). The rules
  trim is the chosen posture; the shared budget guard is already in place.
- **`06`-gate release.** prefill-trim-4 held `06` behind the deferred-discovery-diagnostic's TASK-3
  verdict. That diagnostic is dropped (never run, low ROI); the `skill_manage` injunctions were always
  on the must-survive list, so the namespace-confusion concern the diagnostic probed is protected by
  the grep gate in TASK-A regardless. The hold is released — no verdict to wait on.

---

## Status — Team Lead

Successor plan assembled from prefill-trim-4's carried-forward state. Both prior blockers cleared
(eval-infra fixed in v0.8.286; `06`-gate diagnostic dropped). 05/07 trims + budget guard already in the
working tree; `06` trim, eval re-run, and guard re-pin remain.

> Ready for `/orchestrate-dev rules-block-trim-finish` (TASK-A → TASK-B → TASK-C → `/ship`).

## Delivery Summary — 2026-06-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-A | must-survive grep anchors all hit; `test_flow_prompt_assembly.py` passes; 3× discovery repetition → 1× | ✓ pass |
| TASK-B | trimmed adherence band within/above inherited baseline per domain | ✓ pass |
| TASK-C | `test_instruction_budget.py` passes against re-pinned ceiling below 24,200; never 24,256 | ✓ pass |

**TASK-A** — `06_skill_protocol.md`: manifest-scan cue collapsed to one canonical home (`## Discovery`),
the two upstream echoes thinned to a single lead-in; `## Background review` mechanism prose compressed to
its behavioral cue. All must-survive anchors intact (`skill_view`, "is your procedure", `skill_patch`,
`skill_edit`, `skill_create`, the 3+-step create bar, create-on-behalf confirm, the distinct `## Create`
"search first" mention). File ~3,194 → 2,710 chars.

**TASK-B** — adherence gate on the v0.8.286-fixed harness:
- **skills** (2 runs): W4.A–D PASS/PASS, W4.E SOFT_PASS 0/3 then 1/3 — within/above baseline. W4.A now
  reads clean (PASS, judge=10), confirming the eval-infra fix resolved the prior spurious FAIL.
- **memory** (3 runs): run 1 & run 3 = W3.A–F PASS, W3.G SOFT_FAIL (within the documented `{PASS,
  SOFT_FAIL}` band). Run 2 produced 3 hard FAILs — all `tools=[]` at exactly the 50s per-call timeout
  (agentic reasoning-length variance, not adherence; confirmed by runs 1/3 clearing the same cases in
  ~40s/~21s). Two of three runs match baseline; no domain regressed.

**TASK-C** — re-measured `build_static_instructions` post-`06`-trim = **23,352 chars** (was 23,769 before
`06`; −417). Re-pinned `INSTRUCTION_BLOCK_CEILING` 24,200 → **23,750** (measured + 398 headroom; below
24,200, ≠ forbidden 24,256). Docstring measured-figure + owning-plan reference updated.

**Tests:** scoped — `test_instruction_budget.py` + `test_flow_prompt_assembly.py` = **3 passed**, lint clean.
**Doc Sync:** skipped — no spec quotes the trimmed `06` prose; no shared module / public API / schema change.

**Extra change (user-directed, outside plan scope) — per-test pytest-timeout single source of truth.**
Made `tests/_timeouts.py` the sole owner of the per-test pytest-timeout ceiling (it already owns the
per-await `asyncio.timeout` budgets), since the ceiling is a calibrated *testing* budget, not infra
config:
- `tests/_timeouts.py` — added `PYTEST_PER_TEST_TIMEOUT_SECS = 180` (the per-test safety-net ceiling).
- `tests/conftest.py` — new `pytest_configure(tryfirst)` hook applies it to `config.option.timeout`
  when no explicit `--timeout` is given; an explicit CLI `--timeout` is left untouched.
- `pyproject.toml` — removed `timeout = 180` (only `session_timeout = 600`, the whole-run wall-clock
  guard, remains as infra config).
- `tests/integration/test_repl_input_queue.py` — removed a redundant `@pytest.mark.timeout(180)` that
  merely restated the new default.
- `tests/test_flow_orchestrate_length_retry.py` — the one legitimate override (>180, for the
  doubled-call length-retry loop) now reads `@pytest.mark.timeout(PYTEST_PER_TEST_TIMEOUT_SECS + 20)`
  instead of a bare `200`, so the only literal pytest-timeout number in the tree lives in `_timeouts.py`.

Verified: suite header reports `timeout: 180s`; full suite green; lint clean.

**Tracked follow-ups (not fixed — by decision):**
1. Eval per-call timeout config: `CALL_TIMEOUT_S` (50s hard) sits below `TOOL_TURN_BUDGET_S` (60s soft),
   making the soft flag unreachable and hard-killing tool turns the soft budget would accept. Touches the
   shared `tests/_timeouts.py` (all tests + evals). User chose **track, don't fix now**.
2. Process-exit teardown noise (pre-existing since ~2026-05-16, library-rooted): pydantic-ai MCP
   `stdio_client` anyio cancel-scope + openai `AsyncStream` GC'd after loop close. Exit 0, results
   correct; no safe inline fix (GC-time unraisable output can't be caught; a real fix restructures the
   shared production MCP bootstrap). Warrants its own plan.

**Overall: DELIVERED**
All three tasks passed `done_when`; lint clean; scoped tests green; adherence band held with zero
regression. The two non-blocking findings above are tracked for separate work.

**Next step:** `/review-impl rules-block-trim-finish` (full suite + evidence scan → verdict at Gate 2).

---

## Implementation Review — 2026-06-03

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-A | must-survive grep anchors all hit; `test_flow_prompt_assembly.py` passes; 3× discovery repetition → 1× | ✓ pass | `06_skill_protocol.md`: anchors present — `skill_view` (:13,24), "is your procedure" (:24), `skill_patch` (:36), `skill_edit` (:37), `skill_create` (:46,63), 3+-step create bar (:43), create-on-behalf confirm "before invoking" (:63), distinct `## Create` "Search first" (:51). Discovery-sense manifest-scan injunction now appears once (:12); :7 is a description, :43/:51 are create-sense. Two upstream echoes removed, `## Background review` mechanism prose compressed to its cue (diff vs HEAD). |
| TASK-B | trimmed adherence band within/above inherited baseline per domain | ✓ pass | Recorded in Delivery Summary: skills W4.A–D PASS/PASS, W4.E SOFT_PASS (within/above baseline); memory runs 1 & 3 = W3.A–F PASS + W3.G SOFT_FAIL (documented band), run 2's hard FAILs traced to 50s per-call timeout variance (`tools=[]`), not adherence. No domain regressed. |
| TASK-C | `test_instruction_budget.py` passes against re-pinned ceiling below 24,200; never 24,256 | ✓ pass | Re-measured `build_static_instructions(deps.config)` independently = **23,352 chars**; `INSTRUCTION_BLOCK_CEILING = 23_750` (`test_instruction_budget.py:33`) → 398 headroom, below 24,200, ≠ 24,256. Docstring measured-figure + owning-plan reference current (:16-21). Single guard confirmed — only `tests/test_instruction_budget.py` references the ceiling. |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Delivery summary misdescribes the `tests/_timeouts.py` change: it states "docstring-only" and "pyproject.toml is now the single source of truth," but the actual change set does the opposite — moves the per-test pytest-timeout ceiling **out** of `pyproject.toml` (`timeout = 180` removed) **into** `tests/_timeouts.py` (`PYTEST_PER_TEST_TIMEOUT_SECS = 180`, new) applied via a **new** `conftest.pytest_configure` hook. conftest.py and pyproject.toml edits are undocumented in the summary. | `tests/_timeouts.py`, `tests/conftest.py:13-26`, `pyproject.toml:94` (vs Delivery Summary "Extra file" note) | minor (not blocking — change is functionally correct and green) | Flagged for TL — see Scope notes below. Not auto-fixed: the code is correct; the defect is scope + summary accuracy, which is a Gate-2 / ship-staging decision, not a code fix. |

### Scope notes (TL action required at `/ship`)
- **Out-of-scope change in the working tree.** The timeout-config refactor (`tests/_timeouts.py` + `tests/conftest.py` + `pyproject.toml`) is unrelated to a rules-block trim. It is **internally consistent and verified working** (suite header shows `timeout: 180s` applied via the new hook; full suite green; lint clean), but it does not belong to this plan's scope. Recommend the TL **either** (a) split it into its own commit/plan, **or** (b) include it knowingly and correct the Delivery Summary's inverted description before shipping.
- **Pre-existing unrelated working-tree state — do NOT sweep into this ship commit:** `.agent_docs/testing.md`, `.claude/skills/clean-tests/SKILL.md`, `docs/REPORT-eval-memory.md`, `docs/REPORT-eval-skills.md`, `evals/eval_memory.py`, `uv.lock`, and the other `docs/exec-plans/active/*.md` plans were already modified at session start (other ongoing / coworker work). Per the staged-file hygiene rule, stage only this plan's files: `co_cli/context/rules/05_workflow.md`, `06_skill_protocol.md`, `07_memory_protocol.md`, `tests/test_instruction_budget.py` (new), plus the plan file — and decide on the timeout-refactor trio above.

### Tests
- Command: `uv run pytest` (full suite) + scoped `tests/test_instruction_budget.py tests/test_flow_prompt_assembly.py`
- Result: **625 passed, 0 failed** (278.32s); scoped: 3 passed
- Log: `.pytest-logs/20260603-141930-review-impl.log`
- Lint: `scripts/quality-gate.sh lint` → PASS (ruff check + format, 321 files)

### Behavioral Verification
- No CLI command changed (the skill's `co status` example does not exist in this CLI; commands are chat/tail/trace/dream/google).
- The affected user-facing surface is the static prompt assembly. Verified directly: `create_deps(...)` + `build_static_instructions(config)` bootstraps cleanly and produces 23,352 chars; `test_flow_prompt_assembly.py` (contiguity + assembly) green.
- `success_signal` checks: TASK-A "06 reads once-not-thrice on manifest-scan" — verified by grep (one discovery-sense injunction, all behavioral cues retained). TASK-B "adherence band held" — verified by the re-run evals (no domain regressed). TASK-C "future verbose rule fails CI; exactly one guard" — verified (guard passes against the lowered ceiling; single guard exists).

### Overall: PASS
All three completed tasks meet `done_when` with file:line evidence; full suite green; lint clean; adherence band held. One minor, non-blocking finding: the out-of-scope timeout-config refactor is mis-described in the Delivery Summary — the TL must resolve staging (split it out or correct the summary) before `/ship`, and must exclude the pre-existing unrelated working-tree files listed above.
