# Plan: UAT Workflow Evals — Phase 1 (Functional Smoke) — COMPLETE

> **Status:** SHIPPED. 6 eval files, 26 cases, covering the operational + thin-functional layer of co's user-facing workflows. Phase 2 (behavioral fidelity) lives in a separate plan — see `docs/exec-plans/active/2026-05-17-205955-uat-workflow-evals-phase2.md`.

## Context

Phase 1 is the UAT smoke layer. Each eval drives one user-facing workflow distilled from `docs/specs/`: REPL turn, session continuity, memory, skills, background tasks, trust/visibility. Cases assert on observable side effects — slash commands dispatch, FTS rows land, sessions rotate, subprocesses die cleanly, tools fire by name. The verdict model is structural: a passing case proves the wiring works, not that the agent acts in line with co's mission claims.

Behavioral fidelity (groundedness, approval discipline, durable user model, multi-step planning, persona stress) is out of scope here and lives in phase 2.

## Outcome — what shipped

| File | Workflow | Cases |
|---|---|---|
| `evals/eval_daily_chat.py` | W1 default `co chat` (multi-turn) | W1.A `multi_turn_coherence`, W1.B `tool_chain`, W1.C `recall_reuse`, W1.D `dream_smoke` |
| `evals/eval_session_continuity.py` | W2 `/new` `/clear` `/sessions` `/resume` `/compact` | W2.A–F (6) |
| `evals/eval_memory.py` | W3 `knowledge_*` + `/memory` | W3.A–F (6) |
| `evals/eval_skills.py` | W4 `/<skill>` + `skill_manage` | W4.A–D (4) |
| `evals/eval_background.py` | W5 `/background` `/tasks` `/cancel` | W5.A–D (4) |
| `evals/eval_trust_visibility.py` | W6 `/approvals` + unknown-slash safety | W6.A `approvals_list_clear`, W6.B `unknown_slash_local_only` (2) |

**Shared infrastructure:**
- `evals/_deps.py` — production `CoDeps` bootstrap (no `CO_HOME` overrides; real `~/.co-cli/`).
- `evals/_ollama.py` — `ensure_ollama_warm()` called outside `asyncio.timeout`.
- `evals/_timeouts.py` — per-call budget constants distinct from `tests/`.
- `evals/_observability.py` — `EvalRun` JSONL writer; `CaseResult` dataclass (4-state `Verdict` enum landed in phase 2's T-A-1 migration).
- `evals/_trace.py` — `TurnTrace` per `run_turn` (full message history, tool calls, token usage, model latency, co trace id).
- `evals/_judge.py` — `judge_with_llm(rubric, transcript, deps=...)`. Used by 2 of 26 cases (W1.A voice, W4.A skill body).
- `evals/_report.py` — `prepend_report(...)` writes per-run REPORT block.

**Persistent outputs:**
- `docs/REPORT-eval-<workflow>.md` per eval; one dated `## Run <ISO8601>` section prepended per run.
- `evals/_outputs/<eval>-<ts>/` per run: `run.jsonl` (one `CaseResult` per line) + `case_<id>.jsonl` (one `TurnTrace` per turn).

## Behavioral constraints honored

1. Real `~/.co-cli/` workspace — no temp dirs, no env overrides.
2. Real LLM via `deps.model` — never inline model/temperature overrides.
3. `ensure_ollama_warm` called outside `asyncio.timeout` (infrastructure prep, not behavior under test).
4. Per-call `asyncio.timeout(N)` wraps each external `await`; `N` comes from `_timeouts.py` constants.
5. No `monkeypatch`/`mock`/`patch` anywhere in `evals/`.
6. Sub-case failure does not abort the run; script exits non-zero iff any case failed.
7. Every eval has ≥ 1 case exercising a boundary / degradation / failure path.
8. Per-case latency budgets enforced via `model_call_seconds <= TURN_BUDGET_S * turn_count`.
9. Full trace persisted for every `run_turn` (assembled-prompt hash, message history, tool calls/returns, token usage, model latency, co trace id).
10. Deterministic eval-seeded artifact names — reruns overwrite in place, no accumulation.

## Phase 1 detail in git

The original C1/C2/grilling/C3 case-by-case design (per-case PASS criteria, regression caught, fixture seeding rules, cycle decisions, behavioral constraints #1–#14) lives in the prior version of this plan. See `git log docs/exec-plans/completed/2026-05-16-142644-uat-workflow-evals-phase1.md` for the C2-Final body if you need the per-case re-derivation. REPORT files at `docs/REPORT-eval-<workflow>.md` carry per-run verdicts.

## W1 multi-turn rewrite (post-ship)

`eval_daily_chat.py` was rewritten from 4 single-turn cases to 4 multi-turn cases in the phase-2 planning cycle. Old case names (`happy_path_qualified_response`, `tool_choice_quality`, `recall_used_in_response`) are gone; new names appear in the table above and in `evals/eval_daily_chat.py` as of HEAD. Helper `_drive_turns` + `_TurnSlice` added so checks can target per-turn assistant text and tool calls rather than the cumulative history. No other phase-1 file required behavioral rewrites.

## Verdict migration (post-ship, via phase 2 T-A-1)

After phase 1 shipped, `CaseResult.passed: bool` + `soft_fail: bool` was replaced by `CaseResult.verdict: Verdict` (4-state StrEnum: PASS / FAIL / SOFT_PASS / SOFT_FAIL). `passed: bool` retained as `@property` so existing read sites compile unchanged. ~68 `passed=` write sites across the 6 phase-1 evals migrated to `verdict=Verdict.<state>`. W3.F's three-state outcome now maps cleanly to `Verdict.PASS` / `Verdict.SOFT_PASS` / `Verdict.FAIL`. Migration detail is in the phase 2 plan's T-A-1 task body.

## Validation

| Gate | Status |
|---|---|
| Each eval runnable end-to-end | `uv run python evals/eval_<workflow>.py` exits 0; REPORT updated. |
| Pytest suite unaffected | `uv run pytest -x` continues to pass — `evals/` not imported anywhere under `tests/` or `co_cli/`. |
| Quality gate clean | `scripts/quality-gate.sh full` passes (lint + pytest). |

Phase 1 evals are NOT in any CI gate — UAT smoke runs invoked manually before ship.

## Closure

Phase 1 → SHIPPED. Phase 2 plan (behavioral fidelity) carries the remaining work; refer to it for the mission tenet → coverage gap mapping and the 5 new behavioral evals' design.
