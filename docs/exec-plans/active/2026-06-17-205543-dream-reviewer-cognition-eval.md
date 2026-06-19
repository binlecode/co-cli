# Dream Reviewer Cognition Eval

## Context

The dream system has three behavioral surfaces:

- **Daemon mechanics** (queue, drain, lifecycle, retry, KICK bridge) — well covered by
  pytest under `tests/daemons/dream/` and `tests/integration/`. Correct home, documented
  at `evals/eval_memory.py:7` ("daemon mechanics … covered by pytest").
- **Housekeeping cognition** (`merge_memory` merge + decay) — covered by evals:
  `eval_daily_chat.py` W1.D (merge→recall) and `eval_user_model.py` W10.C (age→decay).
  Both call `merge_memory()` directly with a real model.
- **Reviewer cognition** (`process_review` → `_run_memory_review` → the `memory_reviewer`
  task agent producing a memory item from a transcript) — exercised by **no test with a
  real LLM**. Every test touching `process_review` uses a nonexistent `session_id` to
  force the transcript-absent early return *before any LLM call*
  (`tests/daemons/dream/test_loop.py`, `tests/integration/test_daemon_crash_recovery.py:113`).

Source verified for this plan (2026-06-17):
- `co_cli/daemons/dream/_reviewer.py:144` — `process_review(deps, domain, session_id,
  persisted_message_count, transcript_override=None)`. Runs unconditionally when called;
  reads no `review_enabled` flag. Missing transcript → benign no-op return.
- `_run_memory_review` (`:109`) serializes with `include_tool_results=False` and forks
  deps via `fork_deps_for_reviewer`. The fork preserves `memory_dir`
  (`co_cli/deps.py:441`), so reviewer-written items land in the parent's `deps.memory_dir`.
- `_with_curation_lens` (`:40`) appends the active soul's curation lens to the reviewer
  prompt, gated on `deps.config.personality`.
- Seed pattern + idempotent stale-purge precedent: `eval_session_recall.py:_seed_parcel_session`.
- Centralized budget: `evals/_timeouts.py:71` `DREAM_CYCLE_BUDGET_S = 240`.
- Eval dispatch shape: `evals/eval_memory.py:266` `main()` iterates a `case_fn` tuple.

## Problem & Outcome

Reviewer extraction judgment — *does the agent learn the right things from a
conversation?* — is the single most important behavioral question about the self-learning
feature, and it has zero behavioral coverage. It is structurally eval-shaped (real model,
real transcript, judge the produced artifact against a rubric — the exact W1.D pattern),
so pytest is the wrong home.

**Outcome:** one eval case in `evals/eval_memory.py` drives `process_review(domain="memory")`
end-to-end with a real model against a seeded transcript, gates that a memory item was
written, and LLM-judges its faithfulness.

**Failure cost:** a reviewer regression — extracting transient chatter as durable memory,
missing a clear stated preference, hallucinating facts, or writing the wrong kind — ships
undetected. The merge/decay evals stay green (they bypass the reviewer entirely), giving
false confidence that "dream cognition is covered."

## Scope

In scope:
- Seed a real session JSONL transcript carrying one clear durable user preference plus
  transient noise (the discriminator: durable kept, noise dropped).
- Invoke the real reviewer path (`process_review`), not a reconstructed agent call.
- Structural gate (a new memory item was written) + LLM-judged faithfulness rubric.

Out of scope:
- Skill-reviewer cognition eval (separate plan).
- Any production code change — test-layer only.
- Daemon-process / queue mechanics (already pytest-covered; do not duplicate).
- Per-domain `memory.review_enabled` gating — `process_review` does not read the flag (the
  flag gates KICK *production* in the REPL). The eval calls `process_review` directly and
  needs no flag set.

## Behavioral Constraints

- **No flag needed.** Drive `process_review` directly, same as W1.D drives `merge_memory`.
  Do not set `review_enabled` or route through a daemon.
- **Real side effects, no cleanup** (`feedback_eval_real_world_data`): the produced item is
  left in the real store. Use a per-run unique token + stale-fixture purge so reruns are
  idempotent without a cleanup pass.
- **Curation lens is live.** `_run_memory_review` appends the active soul's curation lens.
  The rubric judges faithfulness, not voice — keep it lens-tolerant (retention judgment may
  legitimately scope what's kept).
- **Tool-result fidelity.** Serialization uses `include_tool_results=False`; the durable
  signal must live in user/assistant text, not tool results, or the reviewer won't see it.
- **Timeout discipline** (`feedback_long_llm_call_rca_first`): wrap the reviewer call in
  `DREAM_CYCLE_BUDGET_S` (existing 240s constant). Do not coin a new budget. A stall is an
  RCA target, never a widen. Call `ensure_ollama_warm()` outside the timeout
  (`feedback_ensure_ollama_warm`).
- Centralized settings/deps/timeouts (`feedback_evals_centralized_settings`): reuse
  `evals/_settings.py`, `_deps.py`, `_timeouts.py` — no inline model settings or budgets.

## High-Level Design

A single judged case in `evals/eval_memory.py` following the W1.D artifact-quality shape:
seed transcript → snapshot `deps.memory_dir` → run real reviewer under budget → set-diff
to find the new item → structural gate on the durable token → `judge_with_llm` on the
item body against a faithfulness rubric. A seed helper modeled on
`_seed_parcel_session` writes the transcript and returns a per-run unique durable token.

## Tasks

### ✓ DONE TASK-1 — Transcript fixture seeder

- files: `evals/eval_memory.py`
- Add `_seed_reviewer_transcript(sessions_dir) -> tuple[Path, str]`. Borrow **only** the
  stale-purge + per-run-token discipline from `eval_session_recall.py:_seed_parcel_session`
  (deterministic fixture uuid8, purge prior fixture, timestamped now) — do **NOT** copy its
  line format. That helper writes a raw `{"parts": [...], "part_kind": "user-prompt"}` line
  with no `kind` discriminator; it is only read by ripgrep `session_search` and fails
  `ModelMessagesTypeAdapter.validate_json`. This transcript IS read by `load_transcript`
  (`_reviewer.py:174`), which skips malformed lines (`persistence.py:106`) and would hand
  the reviewer an empty transcript — silent no-op, gate fails for the wrong reason.
- Build real `ModelRequest`/`ModelResponse` objects
  (`ModelRequest(parts=[UserPromptPart(content=...)])`,
  `ModelResponse(parts=[TextPart(content=...)])`) and serialize via `append_messages`
  (`persistence.py:25`) or `ModelMessagesTypeAdapter.dump_json`. Content carries **one
  unambiguous durable preference** keyed by a per-run unique token, embedded among 2–3
  transient/non-durable turns (logistics, one-off chit-chat) a good reviewer should NOT
  memorialize. Durable signal in user/assistant text only (not tool results).
- done_when: `load_transcript(path)` returns the seeded turns (load-bearing — proves the
  format parses) and the unique token appears exactly once; rerun purges the prior fixture
  before writing.
- success_signal: helper returns a valid transcript path + token; `load_transcript`
  yields the seeded turns.
- prerequisites: none

### ✓ DONE TASK-2 — Reviewer-cognition case

- files: `evals/eval_memory.py`
- Add `case_<id>_reviewer_extracts_durable_memory(deps, agent, frontend, run)` — keep the
  4-arg signature for tuple-dispatch parity even though `agent`/`frontend` are unused (the
  case drives `process_review`, not `run_turn`); note the unused params.
  1. Snapshot `deps.memory_dir` `*.md` file set; seed the transcript (TASK-1). Derive
     `session_id = path.stem` — `process_review` looks up `deps.sessions_dir /
     f"{session_id}.jsonl"` (`_reviewer.py:170`), so `session_id` is the full filename stem
     (`YYYY-MM-DD-THHMMSSZ-uuid8`), not a bare uuid.
  2. `async with asyncio.timeout(DREAM_CYCLE_BUDGET_S):` call
     `process_review(deps, "memory", session_id, persisted_message_count=None)` importing
     it from `co_cli.daemons.dream._reviewer`. Do not add a second `ensure_ollama_warm()`
     here — `main()` already warms once at the entrypoint.
  3. **Structural gate:** a new `*.md` appeared in `deps.memory_dir` (set difference)
     whose body contains the per-run durable token. FAIL fast if none created.
  4. **Judged faithfulness:** `judge_with_llm(rubric, [{"role": "assistant", "content":
     item_body}], model=deps.judge_model)` — wrap the body as a single-element role-dict
     list (`judge_with_llm` iterates its `transcript` arg; a bare str iterates per-char,
     `_judge.py:101,176`). Emit `judge_model_annotation(deps)` in the reason per the W3.G
     precedent (`eval_memory.py:230`). PASS only if it captures the durable preference
     faithfully; FAIL if it memorializes a transient/noise turn, hallucinates a fact absent
     from the transcript, or distorts the preference. Judge the artifact text, not an agent
     turn. Keep the rubric lens-tolerant.
- done_when: `uv run python evals/eval_memory.py` runs the case to a verdict; a new memory
  item carrying the durable token exists in `deps.memory_dir` after the run.
- success_signal: case reaches a PASS/FAIL verdict with a judge score in run output.
- prerequisites: TASK-1

### ✓ DONE TASK-3 — Register + smoke

- files: `evals/eval_memory.py`
- Add the new case to the `case_fn` tuple in `main()` alongside
  `case_w3_g_forget_propagates_to_recall`.
- done_when: the case appears in the eval's run summary and emits its `-case_<id>` JSONL
  output record under `evals/_outputs/`.
- success_signal: one full `uv run python evals/eval_memory.py` shows the new case with a
  verdict and produces its per-case output record.
- prerequisites: TASK-2

## Testing

- `uv run python evals/eval_memory.py` completes; the new case reaches a verdict.
- A new memory `*.md` carrying the per-run durable token exists in `deps.memory_dir`.
- Rerun is idempotent (stale fixture purge + unique-token scoping).
- No production source touched; `scripts/quality-gate.sh lint` clean.
- Tail the run log to watch reviewer LLM-call timing (`feedback_tail_log_every_test_run`).

## Open Questions

- **Right discriminator?** — RESOLVED (Gate 1): keep the combined "durable-kept /
  noise-dropped" assertion as a single first case. The failure-cost paragraph names
  over-memorialization as a target regression an extract-at-all case would not catch, and
  splitting doubles real-model cost against eval-as-smoke discipline. Residual: the
  noise-rejection assertion stays lens-tolerant (Behavioral Constraints) so it does not
  FAIL a reviewer that legitimately keeps a borderline turn.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev dream-reviewer-cognition-eval`

## Delivery Summary — 2026-06-18

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `load_transcript(path)` returns seeded turns; token appears once; rerun purges prior fixture | ✓ pass |
| TASK-2 | full eval runs the real reviewer to a verdict; new memory item with durable token in `deps.memory_dir` | ✓ pass |
| TASK-3 | case registered in `main()`; appears in run output with a verdict | ✓ pass |

**Tests:** eval-only (no pytest test files touched). `uv run python evals/eval_memory.py` → W3.G PASS, **W3.R PASS** (new). W3.R created `deploy-note-tagging-marker-<run>.md` carrying the durable token; judge.score=10 — faithfully captured the deploy-note preference, dropped the sync/sports noise. TASK-1 seeder verified standalone: 6 turns round-trip via `load_transcript`, token once, rerun idempotent.
**Doc Sync:** clean — test-layer change only; no shared module, public API, or schema touched.

**Note on TASK-3 done_when:** the plan expected a `-case_W3.R.jsonl` trace artifact. That file is produced only by `record_turn` (agent-turn cases); W3.R drives `process_review` directly with no agent turns to trace, so its output record is correctly the verdict line in `memory-<ts>-run.jsonl`. Case is registered, runs to a verdict, and is recorded — intent satisfied.

**Overall: DELIVERED**
All three tasks pass `done_when`, lint clean, the new reviewer-cognition case (W3.R) runs the real `process_review` path end-to-end and PASSes its structural gate + judged faithfulness rubric.

## Implementation Review — 2026-06-18

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `load_transcript(path)` returns seeded turns; token once; rerun purges prior fixture | ✓ pass | `eval_memory.py:114` `_seed_reviewer_transcript` builds real `ModelRequest`/`ModelResponse` via `append_messages` (sig matches `persistence.py:25`); re-executed standalone → 6 turns round-trip through `load_transcript`, token count=1, rerun leaves 1 file with per-run-unique token |
| TASK-2 | full eval drives real reviewer to a verdict; new memory item with durable token in `deps.memory_dir` | ✓ pass | `eval_memory.py:338` case calls `process_review(deps, "memory", seed_path.stem, persisted_message_count=None)` under `DREAM_CYCLE_BUDGET_S`; sig matches `_reviewer.py:144`; set-diff structural gate (`:377`) + `judge_with_llm(... deps=deps, model=deps.judge_model)` matches `_judge.py:157`. Live run created `deploy-note-tagging-preference-331b236b.md`, judge.score=10 |
| TASK-3 | case registered in `main()`; appears in run output with a verdict | ✓ pass | `eval_memory.py:453` adds `case_reviewer_extracts_durable_memory` to the `case_fn` tuple; run record `memory-20260618T184917Z-run.jsonl` shows `W3.R pass` |

### Issues Found & Fixed
No issues found. Convention checks clean: real model (no mocks/fakes); centralized `DREAM_CYCLE_BUDGET_S`/`CALL_TIMEOUT_S` + `make_eval_deps` (no inline settings); `ensure_ollama_warm` warmed once at entrypoint, outside the timeout; no production source touched. The judge-call outer `asyncio.timeout(CALL_TIMEOUT_S)` and `SOFT_FAIL`-on-judge-not-passed match the established W3.G pattern in the same file (`:303`, `:310`), so they are consistent convention, not drift.

### Tests
- Eval-only change — no pytest test files touched (plan scope: test-layer, additive to `evals/eval_memory.py`). The eval IS the behavioral gate here, per the eval-vs-pytest split in CLAUDE.md. Full pytest suite intentionally not run: the working tree carries unrelated uncommitted changes from other active plans (canon-injection, per-model-prompt-calibration, summarizer-input-fit-guard) whose failures would be out of scope for this review.
- Command: `uv run python evals/eval_memory.py`
- Result: W3.G PASS, W3.R PASS (judge.score=10)
- Log: `.pytest-logs/20260618-*-review-impl-eval.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads)
- `uv run python evals/eval_memory.py`: ✓ W3.R drives the real `process_review` reviewer end-to-end, writes a faithful memory item carrying the per-run durable token, judge.score=10 (drops the sync/sports noise). `success_signal` verified: case reaches a PASS verdict with a judge score in run output and a per-case record in `evals/_outputs/`.

### Scope note
`git diff` shows files outside this plan's `files:` (`co_cli/config/memory.py`, `co_cli/context/compaction.py`, `co_cli/main.py`, several `docs/specs/*`, `tests/*`). These belong to other in-flight plans present in `docs/exec-plans/active/`, not this delivery — this plan touched only `evals/eval_memory.py`. Flagged, not blocking; confirm only `evals/eval_memory.py` is staged at ship.

### Overall: PASS
