# Tighten the turn-level model-request cap (the only doom-loop guard for in-cap loops)

## Context

co's agent loop is a three-level containment — `turn ⊇ run ⊇ model request` (tool calls under a
request) — documented in [core-loop.md](../../specs/core-loop.md) §1. This plan began as a
"peer-aligned caps at every layer" refactor; **Core Dev review collapsed it** to a single,
correct change. Two facts decided it:

1. **There is only one meaningful axis, not two (CD-M-1).** `_execute_run` threads
   `usage=turn_state.latest_usage` (orchestrate.py:386) — a *cumulative* `RunUsage` carried
   across every run in the turn (`turn_state.latest_usage = result.usage()`, :425). pydantic-ai
   checks `usage.requests >= request_limit` (usage.py:381). So an SDK `request_limit` fires on
   the **turn-cumulative** request count — the *same axis* as the manual
   `max_model_requests_per_turn` accumulator. A "per-run backstop" via `request_limit` is not
   independent of the turn cap; it is a redundant second enforcement of the same dimension (and
   `UsageLimitExceeded` is caught nowhere today, so it would crash — CD-M-2). The per-run cap
   idea is therefore dropped.
2. **The existing per-request guard cannot stop an in-cap doom-loop (PO).**
   `TOOL_CAP_HARD_STOP_CONSECUTIVE` (3) only fires on *consecutive over-cap* (>3 tool-calls)
   requests, and the streak resets on any ≤3-call request (orchestrate.py:442). A doom-loop that
   issues 1–3 calls every request never trips it — its only guard is the turn-cumulative cap,
   `max_model_requests_per_turn`, currently **90**.

Peer survey (corroborating, not the driver): opencode hard-caps 25 steps/activity; hermes ~10
per prompt; codex/openclaw have no per-turn count cap. co's 90 is far looser, and observed
legitimate usage maxes at **7 model requests/turn**.

## Problem & Outcome

**Problem:** The only guard against an *in-cap* doom-loop (model re-issuing 1–3 tool calls per
request indefinitely) is the turn-cumulative `max_model_requests_per_turn`, set at **90** — so
such a loop runs ~90 model requests × multi-second latency (minutes of wasted compute on the
local model, a wedged-looking session) before anything stops it.

**Outcome:** `max_model_requests_per_turn` is tightened to a value with healthy headroom over
real usage (~7) but far below 90, so an in-cap doom-loop is stopped an order of magnitude sooner
with the existing clear status message, while every legitimate turn — including multi-run
approval/retry turns — is untouched.

**Failure cost:** Today an in-cap doom-loop silently burns up to ~90 local-model requests before
the cap fires; the user sees a hung session for minutes.

## Scope

**In scope**
- Lower `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` from 90 to **40** (Gate-1 decision; see Cap-Value Decision).
- Update the one test that hardcodes 90.
- Merge the cap-value reasoning into `docs/specs/core-loop.md` §1 (see TASK-3).

**Out of scope**
- Any new per-run cap / SDK `request_limit` change — invalid per CD-M-1; `_execute_run` keeps
  `request_limit=None` and the manual accumulator stays the sole enforcement.
- The per-request tool-call caps (`MAX_TOOL_CALLS_PER_MODEL_REQUEST`=3,
  `TOOL_CAP_HARD_STOP_CONSECUTIVE`=3) — already stricter than peers; unchanged.
- RC1 latency / eval-budget recalibration — separate eval-layer decision; this change does not
  affect it (legitimate turns use ≤7 requests, far under any chosen cap).
- Subagent caps (`agent/run.py` sets its own `request_limit=budget`; unaffected).
- The broader `max_ctx` → `max_context_tokens` naming-family rename — its own plan
  (`2026-06-16-122223-rename-max-context-tokens`). This plan's
  `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN → MAX_MODEL_REQUESTS_PER_TURN` rename applies the same
  shared principle — **limit constants drop the redundant `DEFAULT_` prefix; fallback-selection
  constants (`DEFAULT_LLM_PROVIDER`/`_HOST`/`_MODELS`) keep it** — but only to the constant this
  plan already edits for the value change.

## Behavioral Constraints

- The cap value must sit **well above** observed legitimate usage (max 7/turn) so normal
  recon-heavy and multi-run approval turns never trip it — it is a doom-loop guard, not a work
  limiter. Note: the cap counts cumulatively across approval-resume **and** overflow-recovery
  retries within a turn (CD-m-4), so the headroom must cover a turn that legitimately resumes
  several times.
- Hitting the cap keeps the existing `_check_turn_caps` behavior: clear user-facing status
  ("Model-request cap reached (N LLM calls this turn) — stopping.") and a clean error
  `TurnResult` — no crash, no silent truncation.
- `0` continues to disable the cap (existing parity); power users can opt out.
- Config-driven; only the default constant changes (no new env wiring).

## High-Level Design

Single change: lower the default of the existing, already-enforced turn-cumulative cap.

- Enforcement is unchanged — `_check_turn_caps` (orchestrate.py:493) already reads
  `deps.config.llm.max_model_requests_per_turn`, compares the cross-run `turn_state.model_requests`
  accumulator, and returns an error `TurnResult` with a status message when reached.
- Only `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` (config/llm.py:31) moves. The `Field(ge=0)` and
  `LLM_ENV_MAP` wiring already exist; no new config surface.
- `_execute_run` keeps `UsageLimits(request_limit=None)` — the SDK per-run ceiling stays disabled
  on purpose; the manual turn accumulator is the clean, message-bearing enforcement.

The value is the one real decision (Open Questions). Driver is doom-loop protection sized by
co's own legitimate-usage headroom; peer numbers (opencode 25) inform but do not dictate, because
co's approval-split turns can legitimately span more cumulative requests than opencode's single
activity loop — so co's cap should be **higher than opencode's 25**.

## Tasks

### ✓ DONE TASK-1 — Tighten the turn cap and rename the constant
- **files:** `co_cli/config/llm.py`, `tests/test_flow_model_request_cap.py`
- **done_when:** the constant is **renamed** `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` →
  `MAX_MODEL_REQUESTS_PER_TURN` (it names a control limit, not a fallback default; the
  `Field(default=…)` use site already conveys the default role, and `UPPER_CASE` vs the
  `max_model_requests_per_turn` field already disambiguates — no collision) across all 3
  references (definition, `Field(default=…)`, test import); set to **40**; the hardcoded
  assertion in `tests/test_flow_model_request_cap.py::test_max_model_requests_default_is_90`
  (line ~34) updated and the test renamed to match the new value; the existing cap-enforcement
  test (which drives the accumulator past the cap and asserts the stop) passes against the new
  value.
- **success_signal:** an in-cap doom-loop is stopped at the new cap with the existing status
  message, an order of magnitude sooner than 90.
- **prerequisites:** none

### ✓ DONE TASK-2 — Verify legitimate turns are untouched
- **files:** (verification only)
- **done_when:** `uv run python evals/eval_multistep_plan.py` runs with **no** "Model-request cap
  reached" status in the logs (legitimate turns use ≤7 requests, far under the new cap) — the
  change alters only doom-loop behavior, never normal turns.
- **success_signal:** grep of the eval logs for the cap message returns nothing.
- **prerequisites:** TASK-1

### ✓ DONE TASK-3 — Merge cap-value reasoning into the spec
- **files:** `docs/specs/core-loop.md`
- **done_when:** the Cap-Value Decision rationale (circuit-breaker sizing — floor = cumulative
  multi-resume worst-case ~20–25 not single-pass 7; ceiling driver vs old 90; why co's cap exceeds
  opencode's 25; chosen **40**) is folded into §1 alongside the three-level containment and
  `max_model_requests_per_turn` documentation, and the stale **90** is corrected to **40** there.
  This is runtime rationale for the shipped value — it belongs in the spec, not only the plan.
- **success_signal:** core-loop.md §1 explains *why* the cap is 40, and no longer says 90.
- **prerequisites:** TASK-1

## Testing

- **Unit:** existing `tests/test_flow_model_request_cap.py` cap-enforcement test with the new
  threshold; pipe to `.pytest-logs/`.
- **Eval (no-regression):** `eval_multistep_plan.py` — confirm no cap message fires on
  legitimate turns (real Ollama + MCP).
- Do not touch eval latency budgets (RC1 is separate).

## Cap-Value Decision (Gate-1, resolved)

**Chosen value: `40`.**

The cap is a circuit breaker, not a work limit, so it is sized by two opposing bounds:

- **Floor (must clear it).** Observed legitimate usage maxes at **7 model requests/turn**, but the
  cap counts *cumulatively across approval-resume and overflow-recovery retries* within a single turn
  (CD-m-4). A recon-heavy turn that hits several approval gates and resumes each time can stack
  several 7-ish passes, so the realistic worst-case legitimate ceiling is ~20–25 — not 7. The cap
  must sit clearly above this so it never bites real work.
- **Ceiling (the point).** 90 lets a 1–3-call-per-request loop burn ~90 multi-second local-model
  requests (minutes of a wedged-looking session) before firing. Anything an order of magnitude below
  90 restores the guard.
- **Why above opencode's 25.** opencode caps a single activity loop; co's turn spans approval-split
  *and* overflow-recovery resumes that opencode's loop does not, so 25 sits too close to co's
  worst-case legitimate ceiling (~20–25) and risks false trips. This is why the value must exceed 25.

That brackets **35–40**. Within the range, bias high: the cost of too-low is a **false trip that
kills legitimate user work** (user-visible, erodes trust); the cost of too-high is a doom-loop runs
~5 extra cheap requests before stopping (invisible, still 50+ requests better than today). The
asymmetry favors headroom → **40** (≈5–6× typical real usage, >2× margin over the multi-resume
ceiling, ~2.5× tighter than 90). Floor is **35**; do not go below.

> **MERGE INTO SPEC ON DELIVERY:** fold this reasoning (circuit-breaker sizing: floor = cumulative
> multi-resume worst-case ~20–25, not single-pass 7; ceiling driver vs 90; why co's cap exceeds
> opencode's 25; chosen 40) into [core-loop.md](../../specs/core-loop.md) §1 where the
> three-level containment and `max_model_requests_per_turn` are documented. This is the runtime
> rationale for the shipped cap value, not build-time plan detail — it belongs in the spec.

## Delivery Summary — 2026-06-16

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | Constant renamed `MAX_MODEL_REQUESTS_PER_TURN` (3 refs), set to 40, test renamed + cap test passes | ✓ pass |
| TASK-2 | `eval_multistep_plan.py` runs with no "Model-request cap reached" in logs | ✓ pass |
| TASK-3 | Cap-value rationale folded into core-loop.md §1; stale 90 → 40 there | ✓ pass |

**Tests:** scoped — `tests/test_flow_model_request_cap.py` 6 passed, 0 failed. Eval `eval_multistep_plan.py` exit 0; W11.C PASS (judge 10), W11.B FAIL — **unrelated latency interruption** (`[slow] 144.2s vs budget 105s`, turn interrupted before any plan; RC1 eval-budget, explicitly out of scope; the 90→40 change is causally uninvolved — the turn timed out far below 40 requests).

**Doc Sync:** fixed — `docs/specs/core-loop.md` §1 (containment table 90→40 + new circuit-breaker rationale paragraph; config table 90→40) and `docs/specs/config.md` (default 90→40 + pointer to core-loop §1). ⚠ Extra file beyond plan `files:`: `docs/specs/config.md` — it carried the same stale default value; correcting it is required doc-sync.

**Overall: DELIVERED**
All three tasks passed done_when; lint clean; scoped tests green; specs synced. The sole eval FAIL is a pre-existing latency-budget interruption unrelated to this change.

## Implementation Review — 2026-06-16

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | Constant renamed → `MAX_MODEL_REQUESTS_PER_TURN` (3 refs), =40, test renamed, cap test passes | ✓ pass | `config/llm.py:31` (def `= 40`), `:238` (`Field(default=MAX_MODEL_REQUESTS_PER_TURN, ge=0)`), `tests/test_flow_model_request_cap.py:21` (import) + `:33` (`test_max_model_requests_default_is_40` asserts `== 40`); no `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` left in source/tests (only historical plan docs). Cap-enforcement test `test_model_request_cap_fires_after_approval_loop` passes. |
| TASK-2 | `eval_multistep_plan.py` runs with no "Model-request cap reached" in logs | ✓ pass | Verification-only, no source change; delivery summary records exit 0 with no cap message. Change touches only the default constant; normal turns use ≤7 requests, far under 40. Config loads `max_model_requests_per_turn = 40` live. |
| TASK-3 | Cap-value rationale folded into core-loop.md §1; stale 90 → 40 | ✓ pass | `core-loop.md:22` — circuit-breaker rationale (floor ~20–25 multi-resume, ceiling vs 90, why > opencode's 25, chosen 40); containment table `:18` and config table `:399` both show 40. Doc-sync `config.md:165` = 40 with §1 pointer. No stale 90 remains for this cap. |

### Issues Found & Fixed
No issues found.

### Tests
- Command: `uv run pytest -v`
- Result: 746 passed, 0 failed
- Scoped: `tests/test_flow_model_request_cap.py` — 6 passed
- Logs: `.pytest-logs/*-review-impl.log`, `.pytest-logs/*-review-impl-scoped.log`

### Behavioral Verification
- No new CLI surface — only a default constant value changed. `load_config().llm.max_model_requests_per_turn == 40` confirmed live.
- `success_signal` verified: cap-enforcement test confirms the stop fires with the "Model-request cap" status (TASK-1); core-loop.md §1 explains why the cap is 40 (TASK-3).

### Scope Note
Diff carries pre-existing uncommitted edits (`.agent_docs/*`, `.claude/skills/*`, `co_cli/commands/*`, `uv.lock`) from prior session work, unrelated to this plan — not introduced here. Plan-relevant files only: `config/llm.py`, `test_flow_model_request_cap.py`, `core-loop.md`, plus `config.md` (declared doc-sync extra). Verify staging before ship.

### Overall: PASS
Single-constant value+rename change; all three tasks confirmed in source, full suite green, spec rationale merged. The lone eval FAIL noted in delivery is a pre-existing RC1 latency-budget interruption, causally unrelated to this change.

## Final — Team Lead

Plan approved.

> Gate 1 — PASSED. PO review confirmed: right problem, correct scope, mechanism claims grounded in
> source (orchestrate.py:386/421/442/474/493). Cap value resolved to **40** (see Cap-Value Decision).
> Reasoning to be merged into the spec on delivery (TASK-3).
> Next: `/orchestrate-dev peer-aligned-hierarchy-caps`
