# deferred-discovery-diagnostic

> **Standalone** — NOT part of the `prefill-trim` family. That family shrinks the always-loaded
> path (prefill *size*); this plan probes whether the **deferred path is reachable at all**
> (capability *effectiveness*) on a 3B-active model. Different problem, different owner.
>
> **Falsify before building.** This is a diagnostic spike, not a build plan. It isolates *which*
> precondition (if any) binds before anyone commits to a loader-UX engineering effort. No
> production behavior ships from it except, conditionally, a cheap stub enrichment (TASK-2).

## Implementation ordering vs. the `prefill-trim` family

Independent path — **runs concurrently with the family**, with one soft-sequencing point.

- **No file overlap with children 2 or 3.** This plan touches `deferred_prompt.py` (stub rendering)
  and `eval_skills.py` (W4.E); children 2/3 touch ALWAYS-tool docstrings and `tests/`. No conflict,
  no dependency either direction. The family's size wins and this diagnostic can proceed in parallel.
- **Soft-sequence BEFORE child 4 (`rules-block-trim`).** Child 4 trims rule `06`. The skill-vs-tool
  **namespace confusion** this diagnostic isolates (the model treating `skill_manage` as a manifest
  *skill* rather than a *tool*) is rooted partly in rule `06`'s text, which mixes skill-manifest
  scanning with `skill_manage` tool mentions. So this diagnostic's verdict (TASK-3) should land
  **before child 4 finalizes its rule-`06` edit** — otherwise child 4 may thin the very signal the
  diagnostic might recommend reinforcing. (Child 4 already preserves the `skill_manage` injunctions
  on its must-survive list, so this is a "read the verdict first," not a hard block.)
- **Recommended slot:** run alongside **child 3** (ships first, low-risk) and complete **before
  child 4**. Net family order becomes: **3 → [diagnostic, parallel] → 2 → 4**, with the diagnostic's
  TASK-3 verdict gating child 4's rule-`06` trim.
- **Token-budget interaction (record it).** If TASK-2 ships op-explicit stubs, the **per-turn**
  deferred-awareness block grows (richer than `name + one-liner`). That is a per-turn (uncached) add,
  separate from the family's ALWAYS-schema trims and outside child 2's schema-budget guard (which
  measures tool *schemas*, not per-turn instruction text). Small, but log it so the family's
  projected prefill reduction stays honest.

## Context

`completed/2026-05-28-164327-deferred-tool-stubs.md` re-tested `skill_manage` DEFERRED with complete
per-tool awareness stubs live and got **0/3** discovery: the model either never fired `search_tools`
(t0, t1) or thrashed then fell back to `file_write` polluting cwd (t2). Its conclusion named the
binding constraint as the `search_tools→load→call` **loader UX** (FM-2/3), recorded a **Standing
Decision** (`skill_manage` stays ALWAYS), and explicitly deferred the loader as "a separate problem"
— which never became a plan.

An external audit sharpened this into two preconditions for viable progressive loading:
1. **Op-explicit stubs** — the stub should surface resource + operation semantics, not a truncated
   prose line.
2. **Effective loader** — the `search_tools→load→call` mechanic must reliably convert awareness
   into a load on a 3B model.

Both are currently unowned. **Verified facts grounding the audit:**
- The stub (`build_deferred_tool_awareness_prompt`, `co_cli/tools/deferred_prompt.py:33-60`) emits
  only `` - `name`: <first non-empty desc line, ≤100 chars> ``.
- `ToolInfo` (`co_cli/deps.py:99`) **already carries** `approval`, `is_read_only`,
  `is_concurrent_safe`, `integration` — the builder surfaces none of them. So op-explicit stubs are
  a data-free change (no new metadata needed), only a rendering change.

**Two reasons not to jump to the audit's build plan (what this diagnostic resolves):**
- **The 0/3 may be skill_manage-specific.** `skill_manage` uniquely collides with the
  `<available_skills>` manifest — the model's verbatim failure was *"skill_manage is listed in the
  available_skills manifest but not in my available tools."* That is a **skill-vs-tool namespace
  confusion**, not necessarily a general loader failure. The clean deferred tools (`task_*`,
  `web_research`, `knowledge_analyze`) have no such collision and have **never been tested** for
  discovery. We must not generalize one pathological tool to "the loader is broken."
- **ROI ceiling.** Per the Standing Decision: context window is not the constraint (14k of 64k);
  DEFERRED buys *cold-prefill latency only* and the schema is KV-cached after turn 1. Even a
  perfectly fixed loader yields a modest, cold-only, cached-away payoff. Any follow-up build must be
  right-sized against that ceiling — which is exactly why we measure before building.

## Problem & Outcome

**Problem.** It is unknown whether progressive loading is broadly broken on this model or only
fails for `skill_manage`, and unknown whether op-explicit stubs would change the outcome. Without
that, any loader-UX investment is on faith.

**Outcome.** Two cheap, harness-reusing experiments that isolate the binding constraint and produce
a go/no-go on a follow-up plan:
- **Test A** — do *clean* deferred tools discover reliably? (general-vs-specific)
- **Test B** — does an op-explicit stub move `skill_manage`'s 0/3? (precondition #1 falsification)

The deliverable is **evidence + a decision**, not a loader fix. (If Test B is clearly positive, the
stub enrichment — a localized, low-risk rendering change that helps the *whole* deferred bucket — may
ship from TASK-2; otherwise it reverts.)

## Behavioral Constraints

- **Diagnostic only; revert experimental toggles.** Re-flipping `skill_manage` to DEFERRED and the
  op-explicit stub rendering are EXPERIMENT scaffolding. They are reverted at each task's end unless
  TASK-2 explicitly green-lights shipping the stub enrichment. `skill_manage` ends this plan ALWAYS
  (`skills.py:306`) regardless of results.
- **Do not reopen the Standing Decision here.** This plan gathers evidence. Permanently re-flipping
  `skill_manage` requires a separate, explicit decision that also clears the ROI argument — out of
  scope.
- **Real-data UAT eval** (`feedback_eval_real_world_data.md`): real deps, real model, no mocks. The
  result is stochastic on a 3B-active model — run N≥3 per arm and compare against the recorded 0/3
  baseline, not an absolute bar (`Verdict.SOFT_PASS`, never reddens CI — mirror W4.E).
- **Reuse the existing harness** — `case_w4_e_discovery` (`evals/eval_skills.py:527+`) already does
  fresh-session-per-trial discovery probing with auto-approval. Generalize it; don't rebuild it.
- **Coworker-maintained assets** (`souls/`, `_profiles/`, `evals/judges/`, `memories/`) untouched.

## High-Level Design

Two experiments over the W4.E harness, each producing a recorded discovery rate.

### Test A — clean-deferred-tool discovery (general vs. skill_manage-specific)
Parameterize the W4.E probe over deferred tools that have **no `<available_skills>` collision**:
- `task_start` (background work; weak substitute = shell `&`),
- `web_research` (multi-source sub-agent; partial substitute = web_search + web_fetch),
- `knowledge_analyze` (partial substitute = reasoning inline).

For each: N≥3 fresh sessions with a prompt where that tool is the right choice. Record per-tool
discovery rate (model fires `search_tools` → loads → calls the deferred tool) and the substitution
fallback when it misses. These tools are ALREADY DEFERRED — no visibility toggle needed; this is a
pure measurement.

### Test B — op-explicit stub effect on skill_manage (precondition #1)
Temporarily (a) re-flip `skill_manage` to DEFERRED and (b) render op-explicit stubs from the
existing `ToolInfo` fields, e.g.:
```
- skill_manage [resource=skill · ops=create/edit/patch/delete · write · approval] — <purpose>
```
Re-run the W4.E discovery probe (N≥3). Compare to the recorded 0/3 baseline. Both toggles revert at
task end unless the result green-lights shipping the stub enrichment.

## Tasks

### TASK-1 — Test A: clean-deferred discovery baseline

**Files:** `evals/eval_skills.py` (generalize `case_w4_e_discovery` to parameterize over a deferred
tool + probe prompt + expected-tool/substitute set; add `task_start`, `web_research` arms — keep
`knowledge_analyze` optional).

**done_when:** Each arm runs N≥3 fresh-session trials; per-tool discovery rate and fallback-tool
recorded in the Delivery Summary, alongside the `skill_manage` 0/3 reference.

**success_signal:** A concrete answer to "is discovery broken for clean deferred tools, or only for
skill_manage?"

**prerequisites:** none.

### TASK-2 — Test B: op-explicit stub effect on skill_manage

**Files:** `co_cli/tools/deferred_prompt.py` (op-explicit rendering from `ToolInfo` fields —
EXPERIMENTAL), `co_cli/tools/system/skills.py:306` (temp DEFERRED), `evals/eval_skills.py` (reuse
the W4.E arm).

**done_when:** N≥3 trials with the op-explicit stub + `skill_manage` DEFERRED, discovery rate
recorded vs the 0/3 baseline. Toggles reverted **unless** the result is clearly positive (see
decision tree) — in which case the stub-rendering change (only) is kept and noted for ship, with
`skill_manage` still reverted to ALWAYS.

**success_signal:** A falsification verdict on precondition #1 (op-explicitness).

**prerequisites:** TASK-1 (so we know whether we're debugging a general or specific failure).

### TASK-3 — verdict + decision

**Files:** none (records the decision in the Delivery Summary; updates the parent family ref only if
a lever is found).

**Decision tree:**
| Test A (clean tools) | Test B (op-explicit skill_manage) | Conclusion → action |
|---|---|---|
| ≥2/3 discover | any | **Loader works; skill_manage is the pathological case** (namespace confusion). DEFERRED is viable for clean tools. `skill_manage` stays ALWAYS (Standing Decision holds; namespace fix is low-ROI, optional follow-up). |
| <2/3 discover | <2/3 | **Loader UX genuinely broken on 3B** (audit's #2 confirmed). A loader-UX plan is justified — scope it separately, weighed against the cold-only/cached ROI ceiling. |
| any | clearly > baseline | **Op-explicit stubs help (#1 has merit).** Ship the stub enrichment (helps the whole deferred bucket's discoverability) as a small standalone change, independent of the skill_manage re-flip. |

**done_when:** One row selected with the measured numbers; follow-up (if any) named with explicit
ROI framing; experimental toggles confirmed reverted (`skill_manage` ALWAYS, stub change kept only
if TASK-2 green-lit it).

**success_signal:** A right-sized go/no-go that avoids committing to loader-UX research on faith.

## Testing

- `evals/eval_skills.py` — the generalized W4.E arms (UAT diagnostics; stochastic, compare to
  baseline, `SOFT_PASS`-only).
- `scripts/quality-gate.sh full` — only if TASK-2 ships the stub-rendering change; otherwise the
  experimental toggles revert and no production code changes.

## Out of scope

- A loader-UX redesign (`search_tools→load→call` mechanic) — that is the *possible output* of this
  diagnostic (Test A-fails branch), not its content.
- Permanently re-flipping `skill_manage` to DEFERRED — reopening the Standing Decision needs a
  separate decision that also clears the ROI argument.
- The `prefill-trim` family (prefill *size*) — orthogonal.
- Fixing the skill-vs-tool namespace confusion — named as a candidate follow-up if Test A isolates
  it, not built here.

## Open Questions

- **Substitution confounds Test A.** Every deferred tool has *some* ALWAYS substitute (shell `&`,
  web_search), so a miss may be "preferred a substitute" rather than "couldn't discover." Mitigation:
  record the fallback tool per miss; a miss-to-substitute is still a discovery failure for the lever's
  purpose, but distinguishes "didn't know it existed" from "knew, chose otherwise."
- **Is the lever worth fixing even if broken?** Per the ROI ceiling, a working loader saves cold-only,
  cached-away tokens. TASK-3 must state this explicitly so a green light isn't mistaken for "worth any
  cost."

## Delivery Summary — TBD
