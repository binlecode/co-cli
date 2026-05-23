# Terminology Rename — `llm_iteration` / `model_turn` → `model_request`

## Context

Co-cli currently uses three overlapping words for the same model-level concept (one LLM call / one `ModelResponse`):
- `llm_iteration` / `llm_iterations` (orchestrate, config, span, CHANGELOG)
- `model_turn` / `MODEL_TURN` (tool-call limit, runtime counter)
- bare `iteration` / `iterations` (config field, env var, doc prose)

Meanwhile the word `turn` *also* names the user-level loop (`run_turn`, `TurnResult`, `_TurnState`, `turn_usage`, `per_turn_instructions`). The collision is most visible in `co_cli/tools/tool_call_limit.py`, where the constant says `MODEL_TURN`, the error literal says `per_turn`, and the guidance string says `llm_iteration` — three names for one thing in 18 lines.

Pydantic-ai's own vocabulary anchors the fix: an `agent.run()` is a *run*; one LLM call is a `ModelRequestNode` (the docs occasionally call it a "model turn" in `UsageLimits.request_limit`, but the SDK's own type is **`ModelRequest`**). Standardising on **`model_request`** matches the SDK and removes the synonym pile-up. We keep `turn` exclusively for the user-level loop.

## Scope

**In:** every identifier, error literal, env var, config key, span attribute, spec/doc/CHANGELOG reference for the model-level concept.

**Out:** user-level `turn` names (`run_turn`, `TurnResult`, `_TurnState`, `turn_usage`, `_merge_turn_usage`, `per_turn_instructions`, `turn.outcome`, `turn.interrupted`, `turns_since_memory_review`, `reset_for_turn`). These stay.

**Out:** loop-iteration prose that genuinely means a Python loop (e.g. `_compaction_boundaries.py` "first iteration" comments). Leave as-is.

## Naming Table

| Old | New | Locations (representative) |
|---|---|---|
| `llm_iterations` (field on `TurnResult`, `_TurnState`) | `model_requests` | `co_cli/context/orchestrate.py` (~110, 161, accumulator, cap check, span, `TurnResult` build sites) |
| `MAX_TOOL_CALLS_PER_MODEL_TURN` (constant) | `MAX_TOOL_CALLS_PER_MODEL_REQUEST` | `co_cli/tools/tool_call_limit.py`, `co_cli/tools/lifecycle.py`, tests |
| `tool_calls_in_model_turn` (CoRuntimeState field) | `tool_calls_in_model_request` | `co_cli/deps.py:185`, `co_cli/tools/lifecycle.py` (reset/increment/cap-check sites) |
| `max_iterations_per_turn` (config field) | `max_model_requests_per_turn` | `co_cli/config/llm.py` (field, default, env map), `co_cli/context/orchestrate.py:475` |
| `DEFAULT_MAX_ITERATIONS_PER_TURN` | `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` | `co_cli/config/llm.py:31` |
| `CO_LLM_MAX_ITERATIONS_PER_TURN` (env var) | `CO_LLM_MAX_MODEL_REQUESTS_PER_TURN` | `co_cli/config/llm.py` env map; doc tables |
| `max_tool_calls_per_turn_exceeded` (error literal in JSON payload) | `max_tool_calls_per_model_request_exceeded` | `co_cli/tools/tool_call_limit.py` (TypedDict Literal + factory), `tests/test_flow_tool_call_limit.py`, `docs/specs/compaction.md` |
| `iters_since_skill_review` (CoSessionState field) | `model_requests_since_skill_review` | `co_cli/deps.py:162`, `co_cli/main.py:240,241,268`, `tests/test_flow_post_turn_hook.py` (all uses) |
| `turn_iteration_count` (parameter on `_post_turn_hook`) | `model_request_count` | `co_cli/main.py:252,268`, `tests/test_flow_post_turn_hook.py` (all call sites) |
| Span attribute `turn.llm_iterations` | `turn.model_requests` | `co_cli/context/orchestrate.py:828`, `docs/specs/observability.md:169`, any tests asserting span keys |
| Comment/docstring uses of "llm_iteration" / "one llm_iteration" / "per llm" | "model request" | Throughout — replace prose to match identifiers |

The `MaxToolCallsExceededPayload` TypedDict **name** stays (it's accurate); only its `error` Literal value and the guidance string change.

## Critical Files

- `co_cli/tools/tool_call_limit.py` — constant, TypedDict literal, factory, guidance message.
- `co_cli/tools/lifecycle.py` — counter increment + cap-check + telemetry attribute names.
- `co_cli/deps.py` — `CoRuntimeState.tool_calls_in_model_turn`, comment at L186, `CoSessionState.iters_since_skill_review`, related docstrings.
- `co_cli/context/orchestrate.py` — `TurnResult.llm_iterations`, `_TurnState.llm_iterations`, accumulator at the segment-result merge, hard-stop cap check, span attribute, all `TurnResult(...)` build sites.
- `co_cli/config/llm.py` — `DEFAULT_*`, field, env-var map.
- `co_cli/main.py` — `_post_turn_hook` parameter, the bump line, `_maybe_kick_skill_review` (uses the renamed field).
- `co_cli/tools/agents/delegation.py` — only uses `_merge_turn_usage` (user-turn — no change), but verify the import is untouched.

**Tests (must update in lockstep):**
- `tests/test_flow_tool_call_limit.py` — error-literal assertion.
- `tests/test_flow_iteration_cap.py` — config field, env var, `turn.llm_iterations` span assertions, file name itself worth renaming to `test_flow_model_request_cap.py`.
- `tests/test_flow_turn_result_tool_iterations.py` — entire file, name worth renaming to `test_flow_turn_result_model_requests.py`.
- `tests/test_flow_bootstrap_budget_span.py` — `MAX_TOOL_CALLS_PER_MODEL_TURN` import + span assertions.
- `tests/test_flow_post_turn_hook.py` — `turn_iteration_count=` kwargs at all `_post_turn_hook` call sites + `iters_since_skill_review` assertions; module docstring at L5.

**Specs & docs:**
- `docs/specs/compaction.md:66,234,237` — L0 row, JSON example payload, guidance prose.
- `docs/specs/core-loop.md:118,132,~145,~378` — column names in the orchestrator-state table, config table, env-var name.
- `docs/specs/observability.md:169` — span attribute table.
- `docs/specs/config.md:~165` — config-key table.
- `CHANGELOG.md:177` (and any other "llm_iterations" prose) — paraphrase, do not rewrite history; add an entry in the new version section describing the rename.

**Completed exec-plans:** `docs/exec-plans/completed/2026-05-19-080633-session-review-counter-simplify.md` and `2026-05-20-002544-agent-loop-cap.md` carry the old terms. Per the project's "completed plans are historical" rule, **do not edit** — they document what was true at ship time.

## Approach

1. **Source changes** — work bottom-up: tool_call_limit → lifecycle → deps → orchestrate → main → config. Each file is a self-contained `Edit` pass with `replace_all` where the old token is unambiguous (e.g. `llm_iterations`, `tool_calls_in_model_turn`); explicit edits where context matters (e.g. comments mixing the old word with `turn`).
2. **Test updates** — mirror the source rename. Rename two test files (`test_flow_iteration_cap.py`, `test_flow_turn_result_tool_iterations.py`) — use `git mv` to keep history.
3. **Spec/doc updates** — patch the four spec files + CHANGELOG entry for the new version. No history rewriting.
4. **Env-var migration note** — `CO_LLM_MAX_ITERATIONS_PER_TURN` is deleted (zero-backward-compat per project rule). Anyone with the old var in their shell will silently fall back to the default; mention in CHANGELOG.
5. **Lint + scoped tests** — `scripts/quality-gate.sh lint --fix`; then run the four touched test files with `-x`.
6. **Full quality gate** — `scripts/quality-gate.sh full` (lint + full pytest) once scoped tests pass.

## Verification

- **Identifier sweep:** `rg -n 'llm_iteration|tool_calls_in_model_turn|MAX_TOOL_CALLS_PER_MODEL_TURN|max_iterations_per_turn|MAX_ITERATIONS_PER_TURN|CO_LLM_MAX_ITERATIONS_PER_TURN|max_tool_calls_per_turn_exceeded|iters_since_skill_review|turn_iteration_count|turn\.llm_iterations' co_cli/ tests/ docs/specs/ docs/exec-plans/active/ CHANGELOG.md` returns no hits.
- **Reverse sweep (no double-renames):** `rg -n 'model_request' co_cli/` shows only the new names; no `model_request_request` or `model_request_turn` typos.
- **Targeted test runs (must pass with `-x`):**
  - `uv run pytest tests/test_flow_tool_call_limit.py tests/test_flow_model_request_cap.py tests/test_flow_turn_result_model_requests.py tests/test_flow_bootstrap_budget_span.py tests/test_flow_post_turn_hook.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-rename-scoped.log`
- **Full quality gate:** `scripts/quality-gate.sh full 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-rename-full.log` — green.
- **Behavioral smoke:** `uv run co chat` → one turn that triggers any tool call → `uv run co tail` should show `turn.model_requests` attribute populated, and `uv run co trace <id>` round-trips.
- **Env-var smoke:** `CO_LLM_MAX_MODEL_REQUESTS_PER_TURN=3 uv run co chat` → confirm cap engages; `CO_LLM_MAX_ITERATIONS_PER_TURN=3 uv run co chat` → confirm it is **ignored** (no warning, falls back to default — that's the zero-back-compat behavior).
- **Spec spot-check:** open `docs/specs/compaction.md` and `core-loop.md`; payload JSON example matches actual emitted error.

## Promotion (after Gate-1 approval)

This plan-mode file lives in `~/.claude/plans/`. On approval, promote to the project's exec-plan home per CLAUDE.md: `docs/exec-plans/active/YYYY-MM-DD-HHMMSS-rename-llm-iteration-to-model-request.md` (creation datetime to the second), then run the standard `/orchestrate-dev` → `/review-impl` → `/ship` cycle.

## Implementation Review — 2026-05-23

> The plan never carried an explicit `✓ DONE` task list (skipped `/orchestrate-dev`). The review treats the entire rename as one composite delivery, with the Naming Table + Critical Files acting as the de-facto task list and the Verification section as `done_when`.

### Evidence
| Requirement | Verdict | Key Evidence |
|---|---|---|
| `MAX_TOOL_CALLS_PER_MODEL_REQUEST` constant | ✓ pass | `co_cli/tools/tool_call_limit.py:5` |
| Error literal `max_tool_calls_per_model_request_exceeded` | ✓ pass | `co_cli/tools/tool_call_limit.py:10,18`; matches `docs/specs/compaction.md:234` |
| `MaxToolCallsExceededPayload` TypedDict NAME preserved | ✓ pass | `co_cli/tools/tool_call_limit.py:9` |
| `tool_calls_in_model_request` on `CoRuntimeState` | ✓ pass | `co_cli/deps.py:185`; increment/reset/cap-check at `co_cli/tools/lifecycle.py:198–228` |
| `model_requests_since_skill_review` on `CoSessionState` | ✓ pass | `co_cli/deps.py:162` |
| `TurnResult.model_requests` + `_TurnState.model_requests` | ✓ pass | `co_cli/context/orchestrate.py:110,161`; accumulator/cap-check at L419–420, L475–476 |
| Cap check uses new field | ✓ pass | `co_cli/context/orchestrate.py:475` reads `turn_state.model_requests`; default 90 from `c.max_model_requests_per_turn` |
| Span attribute `turn.model_requests` | ✓ pass | `co_cli/context/orchestrate.py:850` |
| `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN` constant | ✓ pass | `co_cli/config/llm.py:31` (= 90) |
| Config field `max_model_requests_per_turn` | ✓ pass | `co_cli/config/llm.py:217` |
| Env var `CO_LLM_MAX_MODEL_REQUESTS_PER_TURN` | ✓ pass | `co_cli/config/llm.py:149` (env-map row) |
| `_post_turn_hook(model_request_count=...)` signature | ✓ pass | `co_cli/main.py:252–256`; bump at L271 |
| Call sites pass new kwarg | ✓ pass | `co_cli/main.py:175`; `tests/integration/test_review_kick_end_to_end.py:89,113,132,171`; `tests/test_flow_post_turn_hook.py:78,91,103,105,113,119,146,171,185,196` |
| `delegation.py` untouched (user-turn `_merge_turn_usage` import safe) | ✓ pass | no diff to `co_cli/tools/agents/delegation.py` |
| Test file renames preserve history (`git mv`) | ✓ pass | `tests/test_flow_model_request_cap.py` and `tests/test_flow_turn_result_model_requests.py` both trace back to commit `f7c7797` / `ac22de6` via `--follow` |
| Spec updates: compaction.md, core-loop.md, observability.md, config.md, dream.md | ✓ pass | `docs/specs/compaction.md:66,234,237`; `core-loop.md:118,132,378`; `observability.md:169`; `config.md:165`; `dream.md:54,62,74,614` |
| CHANGELOG entry under `[Unreleased]` | ✓ pass | `CHANGELOG.md:5–14` documents the rename; historical mentions in `[0.8.230]/[0.8.228]` preserved per plan (no history rewrite) |
| Completed exec-plans untouched | ✓ pass | `git diff --name-only HEAD docs/exec-plans/completed/` → empty |
| Identifier sweep (old names): zero hits in `co_cli/`, `tests/`, `docs/specs/` | ✓ pass | only legitimate quoted-historical hits remain in `CHANGELOG.md` |
| Double-rename sweep (`model_request_request`, `model_request_turn`): zero hits | ✓ pass | clean across `co_cli/`, `tests/`, `docs/specs/` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---|---|---|---|
| Scope creep: test refactor moves coverage from private `_find_decay_candidate_skills` to public `decay_skills` API, removes 3 historical "still-removed" guards (curator modules/constants). Unrelated to the rename — both `_find_decay_candidate_skills` and `HousekeepingStats` still exist in `co_cli/daemons/dream/_housekeeping.py:427` and `co_cli/daemons/dream/_state.py:42`. | `tests/daemons/dream/test_skill_housekeeping.py` (89 lines changed, mostly removals) | minor — scope-creep flag | Left in place. Tests pass and coverage of the public `decay_skills` path is preserved transitively. Note in commit message that this work hitched along; if undesired, split before `/ship`. |

No blocking issues — auto-fix loop not entered.

### Tests
- Scoped: `uv run pytest tests/test_flow_tool_call_limit.py tests/test_flow_model_request_cap.py tests/test_flow_turn_result_model_requests.py tests/test_flow_bootstrap_budget_span.py tests/test_flow_post_turn_hook.py tests/integration/test_review_kick_end_to_end.py tests/tools/system/test_skill_manage_resets.py -x` → 38/38 passed
- Full: `uv run pytest -x` → **576 passed, 0 failed** in 316.74s (`.pytest-logs/20260523-*-review-impl-full.log`)
- Lint (initial + final): `scripts/quality-gate.sh lint` → PASS (ruff check + format clean across 311 files)

### Behavioral Verification
- `uv run co status` n/a — co-cli has no `status` subcommand (chat/tail/trace/dream only); template default skipped.
- `uv run co tail --help`: ✓ subcommand boots without import errors after rename.
- Import + introspection smoke (`uv run python -c ...`): ✓ all renamed identifiers reachable with correct values (`MAX_TOOL_CALLS_PER_MODEL_REQUEST==6`, `DEFAULT_MAX_MODEL_REQUESTS_PER_TURN==90`, env-map row present, `_post_turn_hook` signature has `model_request_count`); all old attribute names absent on `LlmSettings` / `CoRuntimeState` / `CoSessionState` / `TurnResult`; error literal equals `max_tool_calls_per_model_request_exceeded`.
- Cap-engagement behavior is covered by `test_flow_model_request_cap.py::test_model_request_cap_fires_after_approval_loop` and `test_hard_stop_fires_after_consecutive_violations` (both in the green suite) — stronger than a manual `co chat` smoke.
- Env-var zero-back-compat: the old `CO_LLM_MAX_ITERATIONS_PER_TURN` name is gone from the env map (`co_cli/config/llm.py:149`); shells exporting it silently fall back to the default per the plan.

### Overall: PASS
Rename is complete and consistent across source, tests, specs, and CHANGELOG; 576 tests green; lint clean; behavioral smoke confirms the new identifiers wire correctly and the old ones are gone. One non-blocking scope-creep flag on `tests/daemons/dream/test_skill_housekeeping.py` — TL to decide whether to split it from the rename commit before `/ship`.
