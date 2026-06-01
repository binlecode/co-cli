# skill-manage-defer

> **Child 1 of** `2026-05-28-141854-prefill-trim.md` (canonical reference — measured
> baseline, governing principle, behavioral constraints). Ship **first**: this is the
> highest-ROI / lowest-risk item and a hypothesis test for DEFERRED-on-a-small-model that
> de-risks the later children.

## Context

The parent plan measured the runtime prefill at ~13.9k tok, of which **tool schemas are ~47%
(6,403 tok ALWAYS)**. `skill_manage` is 2,230 chars / ~560 tok of that bucket, paid on *every*
turn for a tool that is invoked rarely (skill creation/edit/delete). The backend is
`qwen3.6:35b-a3b-agentic` (35B MoE, 3B active); DEFERRED removes a tool's entire schema from the
prefill (it loads lazily via `search_tools`), making it the most token-efficient lever — but it
bets the small model can *discover* the tool when needed.

`skill_manage` is the one clean candidate (parent §"Why skill_manage"): rare + large +
**self-priming discovery** (the `<available_skills>` manifest is in every prompt and rule `06`
literally names `skill_manage(action='create', …)`) + non-reflexive + approval-gated regardless
of visibility. It joins the 6 tools already DEFERRED in the TARS bootstrap (`task_start`,
`task_status`, `task_list`, `task_cancel`, `web_research`, `knowledge_analyze`) — same
"heavyweight + occasional" profile. `skill_view` (the read half, hot during skill execution)
stays ALWAYS.

**Risk framing — this is the lowest-risk deferral, not a coin-flip.** Those 6 tools already run
DEFERRED in production on this exact model, so DEFERRED-discovery on qwen3.6 is *already*
validated in principle. `skill_manage` is, if anything, the easiest of the set to discover: it is
the only one whose exact call syntax is named in a rule file (`06`, 3×) *and* whose domain
(skills) has a `<available_skills>` manifest in every prompt. The "hypothesis test that de-risks
the children" framing therefore overstates the novelty — the stronger evidence is the *existing*
deferred set's observed discovery behavior, not this one smoke. Treat the smoke as a confirmation
gate, not a from-scratch experiment.

**Cost of the trade (state it as a decision).** Deferral adds one `search_tools` round-trip
before the (already-required) approval prompt on the rare creation path. We trade ~560 tok of
prefill *every* turn for +1 LLM hop on an infrequent action — a clear win, but a deliberate one.

## Problem & Outcome

**Problem.** `skill_manage`'s 2,230-char schema is in every cold prefill (~560 tok × 333 tok/s
cold ≈ 1.7s/call) for an occasional-use tool.

**Outcome.** Flip `skill_manage` to DEFERRED. ALWAYS bucket 25,612 → ~23,400 chars. Gated on a
live discovery smoke — if qwen reliably fails to find it via `search_tools`, revert (one line).
The result also validates whether further DEFERRED promotions are viable on this model.

## Behavioral Constraints

- `skill_view` stays ALWAYS — do not touch.
- `approval=True` + `_skill_manage_approval_subject` are independent of visibility — approval
  still fires after the flip.
- No handler/signature changes — visibility flag only.
- **Rule `06` is now the deferral's discovery anchor.** Once `skill_manage` is DEFERRED, the
  literal `skill_manage(action='create'/'edit'/'patch', …)` mentions in
  `06_skill_protocol.md` (lines 39/41/50/67) are what primes the `search_tools` reflex. Child 2
  (tool-guidance-dedup) trims rules — those call-syntax mentions MUST NOT be trimmed away. Flagged
  here and in the parent's Behavioral Constraints.

## High-Level Design

Single-line change in `co_cli/tools/system/skills.py`: the `skill_manage` decorator
`visibility=VisibilityPolicyEnum.ALWAYS` → `VisibilityPolicyEnum.DEFERRED`.

## Tasks

### ✗ REVERTED TASK-1 — skill_manage ALWAYS → DEFERRED

**Files:** `co_cli/tools/system/skills.py` (`skill_manage` decorator only).

**done_when:**
- `uv run python tmp/audit_tool_schemas.py` shows `skill_manage` in the DEFERRED bucket; ALWAYS
  bucket drops ~2,230 chars (25,612 → ~23,400).
- `uv run pytest tests/ -k "skill" -x` passes.
- **Discovery smoke (gating) — N≥3 trials, varied phrasings.** The model is stochastic and this
  gate carries the whole DEFERRED bet, so one turn cannot decide it. Run ≥3 `uv run co chat`
  sessions with *different* save requests (e.g. "save the steps we just did as a `/token-trim`
  skill", "turn this workflow into a reusable skill", "make a skill out of what we just did"). In
  each, the model must discover `skill_manage` via `search_tools`, prompt for approval, and create
  the skill. **PASS = ≥2/3 succeed.** Record per-trial outcomes in the Delivery Summary. If it
  fails the bar, REVERT (one line) and record the finding.

**success_signal:** Skill creation works end-to-end through the deferred path.

### ✗ REVERTED TASK-2 — full sweep

**Prerequisites:** TASK-1.

**Action:** `mkdir -p .pytest-logs && uv run pytest -x 2>&1 | tee
.pytest-logs/$(date +%Y%m%d-%H%M%S)-skill-manage-defer.log`

**done_when:** Full suite green (625 baseline from
`.pytest-logs/20260528-091448-review-impl-retry2.log`).

## Testing

- `scripts/quality-gate.sh full`.
- `co chat` skill-creation smoke (the TASK-1 gate — load-bearing for the whole DEFERRED bet).

## Out of scope

- Promoting any other tool to DEFERRED (reflexive tools must stay ALWAYS — parent §Out of scope).
- Docstring/params trimming (children B and C).

## Open Questions

- Does qwen3.6 reliably discover a deferred tool via `search_tools`? The 6 already-deferred tools
  say *yes in principle*; this task confirms it for the best-primed candidate (≥2/3 smoke). A
  clean PASS green-lights B/C; a fail (even for the most-primed tool) means the DEFERRED lever is
  off the table for this model and we lean harder on trimming.
- Are the existing 6 deferred tools observed to be discovered reliably in real sessions? If yes,
  cite it in the Delivery Summary — it is stronger evidence than the smoke and would let later
  children defer more aggressively.

## Delivery Summary — 2026-05-28

Single-line change: `co_cli/tools/system/skills.py` — `skill_manage` decorator
`visibility=VisibilityPolicyEnum.ALWAYS` → `DEFERRED`. No handler/signature/approval changes.
`skill_view` untouched (still ALWAYS).

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | audit: skill_manage DEFERRED, ALWAYS −2,230 chars | ✓ pass (25,612 → **23,382**, exact) |
| TASK-1 | `pytest -k skill -x` | ✓ pass (122 passed, 513 deselected) |
| TASK-1 | discovery smoke (N≥3, ≥2/3) | ⏳ **pending human gate** — interactive `co chat` + warm qwen3.6, not autonomously runnable |
| TASK-2 | full suite green | ✓ pass (**635 passed** in 351s; baseline was 625, suite has grown) |

**Schema impact (`tmp/audit_tool_schemas.py`):** ALWAYS bucket 25,612 → 23,382 chars (~6,403 → ~5,845 tok, **−558 tok/turn**); DEFERRED bucket 4,932 → 7,162 chars. Matches the parent's −560 projection.

**Doc Sync:** narrow — fixed `docs/specs/tools.md:39` count `19 ALWAYS · 16 DEFERRED` → `18 ALWAYS · 17 DEFERRED` (the only spec fact made stale; no other doc repeats it). No API/schema change, so no full sync.

**Tests:** scoped 122 passed; full suite 635 passed, 0 failed. Lint clean.

### Discovery smoke — RAN (autonomously, via `tmp/discovery_smoke.py`) → gate FAILED → REVERTED

The smoke *was* run after all (driven through `run_turn` + a real approval loop, not the
interactive REPL). Two harness bugs surfaced and were fixed en route:
- `evals/_deps.py` `EvalFrontend.prompt_approval`/`prompt_question`/`prompt_confirm` were **sync**
  overrides of production **async** methods — crashed the deferred-approval path
  (`await frontend.prompt_approval(...)`). **Fixed** (all three → `async def`). This is the first
  thing to route an approval-gated tool through the *deferred*-approval path; the bug had been
  latent because existing evals call handlers directly or never reach the approval prompt.

**Clean independent-discovery reads (only 2 valid; rest contaminated by shared session state or an
MCP-teardown crash):**

| Run | search_tools | skill_manage | created | time | outcome |
|---|---|---|---|---|---|
| isolated #1a | yes | yes | **yes** | 177s | clean PASS |
| isolated #1b | yes (×3) | yes | **no** | 180s (ceiling) | thrash → `file_write` to **cwd/repo root** → FAIL |

Effective independent discovery ≈ **1/2**, and even the success took 177s. Failure mode is ugly:
the model fires `search_tools` repeatedly, then falls back to `file_write` with a **bare relative
path**, leaving a stray skill `.md` in the launch directory.

**Root cause of the thrash (key finding):** `build_tool_category_awareness_prompt`
(`co_cli/tools/deferred_prompt.py`) — the static-prompt hint for deferred tools — is a **hardcoded
allowlist** (`_NATIVE_TOOL_CATEGORIES`) that **does not include `skill_manage`**. So once
`skill_manage` went DEFERRED it had **zero awareness stub** in the prefill; the only place its name
surfaced was `capabilities_check` (a tool the model must choose to call). The model knew skills
*could* be created (rule 06) but had no always-present signal that the capability sat behind
`search_tools`.

**Decision: gate not met (`PASS = ≥2/3` reliably) → REVERTED `skill_manage` to ALWAYS** (one line;
`tools.md` count restored to 19 ALWAYS · 16 DEFERRED). The async-approval fix in `evals/_deps.py`
is **kept** (it is a real bug fix, independent of this revert).

**Overall: WITHDRAWN — deliverable reverted, superseded.**
This plan answered the parent's open question empirically: **DEFERRED-discovery is not viable for
`skill_manage` on qwen3.6 *as currently built*** — but the root cause is the missing per-tool
awareness stub, not the model's inability to discover per se. Superseded by the
**`deferred-tool-stubs`** plan (seed name + one-liner for every deferred tool in the static prompt,
auto-generated from `tool_index`); if that lifts discovery past the bar, `skill_manage` re-flips to
DEFERRED there. Lifecycle note: this is a withdrawn (reverted, never-shipped) plan and currently
sits in `completed/` — per project convention withdrawn plans are deleted, not archived; left in
place pending the user's call since the finding above is its only lasting value (now also carried
into the parent + the stubs plan).
