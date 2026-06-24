# Eval Trace Capture — Fresh-History Loop Drops Tool Calls

Fix the `record_turn` message-slicing bug that makes per-prompt eval loops (fresh `message_history=[]` under one `case_id` with an incrementing `turn_index`) silently drop the turn's own tool calls, producing false-negative verdicts. Surfaced by `eval_skills.py` W4.B during `/review-impl skill-prompt-device-adoption`.

## Context

`/review-impl skill-prompt-device-adoption` ran `eval_skills.py` as an existing-eval functional check. **W4.B (`skill_selection_mutual_exclusivity`) failed deterministically across two runs** — `[pptx] prompt: selected none; expected to include ['office']` — even though the live transcript showed the model selecting `office` correctly (loaded the office `SKILL.md` body via `skill_view`, then ran `co-extract-office`). The reported verdict and the actual behavior disagree: the model is right, the harness mis-captured.

Root cause is in the eval-trace helper, not the skill or the model:

- `evals/_trace.py:36` defines a module-level `_PRIOR_MSG_COUNT: dict[str, int]` keyed by `case_id`. Its job: `run_turn` returns cumulative `all_messages()` (turns 0..N for a continuation), so to record only *this* turn's new messages, `record_turn` slices off the prior count.
- `evals/_trace.py:246-249`:
  ```python
  msgs = getattr(turn_result, "messages", None) or []
  prior = 0 if turn_index == 0 else _PRIOR_MSG_COUNT.get(case_id, 0)
  new_msgs = msgs[prior:]
  _PRIOR_MSG_COUNT[case_id] = len(msgs)
  ```
  This is **correct for continuation cases** (turn N's `messages` is a superset of turn N-1's) but **wrong when each prompt runs with fresh `message_history=[]`**.
- `evals/eval_skills.py:341-357` (`case_w4_b_skill_selection`) loops four independent prompts, each with `message_history=[]`, under one `case_id="W4.B"` and `turn_index=index` (0,1,2,3). So `turn_result.messages` is a **fresh, non-cumulative** list each iteration, but the slice still subtracts the previous prompt's message count.

Deterministic failure walk:
- index 0 (`pdf`): `turn_index==0` forces `prior=0` → `new_msgs = all of pdf's messages` → `skill_view(name="documents")` captured → PASS. Sets `_PRIOR_MSG_COUNT["W4.B"]` to pdf's message count (≈8).
- index 1 (`pptx`): `prior=8` (stale, from pdf), but pptx's fresh `messages` list is only ≈7 long → `new_msgs = msgs[8:] = []` → `tool_calls = []` → `_selected_skills` returns `set()` → "selected none" → FAIL. The loop breaks here, so `xlsx`/`url` never run.

`_selected_skills` (`evals/eval_skills.py:268`) is faultless — it reads `turn_trace.tool_calls`, which was already emptied by the bad slice. The frontend rendered the `skill_view` call live; only the harness's recorded `tool_calls` lost it.

This is a latent footgun in shared infra: ~10 evals call `record_turn(case_id=…, turn_index=i)` in loops (`grep` for `turn_index=i`). The bug bites **only** loops that reset history to `[]` per iteration while incrementing `turn_index` under one `case_id`. Continuation loops (most multi-turn cases — they thread `message_history` forward) are correct and must stay correct.

## Problem & Outcome

**Problem:** `record_turn`'s cumulative-message slicing silently corrupts the recorded `tool_calls`/`assistant_text` for any eval loop that runs independent fresh-history prompts under one `case_id`. The corruption is invisible (no error) and produces false-negative verdicts that look like real behavioral regressions — exactly the trap that cost investigation time during the skill-prompt review.

**Outcome:** `eval_skills.py` W4.B passes when the model selects correctly (verdict tracks behavior). Every sibling loop with the same fresh-history pattern is identified and corrected. The cumulative-slice contract is made robust so a fresh-history loop can no longer silently drop a turn's own messages.

**Failure cost:** Without this, any future eval authored as an independent-prompt loop will mis-report, and reviewers will chase phantom regressions (or — worse — dismiss a *real* one as "that eval is just broken"). The harness silently lies about what the model did.

## Scope

**In scope:**
- Fix `case_w4_b_skill_selection` so each fresh-history prompt's tool calls are captured (the prompts are independent turn-0s, not a continuation).
- Audit every `record_turn` caller for the same fresh-history-under-one-case_id pattern; fix any found.
- Harden `record_turn` / `_PRIOR_MSG_COUNT` so the cumulative-slice assumption cannot silently corrupt a non-cumulative turn (make the footgun impossible or loud, without breaking legitimate continuation slicing).

**Out of scope (rejected by design — do not add):**
- Re-running or re-tuning any LLM eval for *content* outcomes — this is a capture-correctness fix, not a behavior change.
- Touching the four skills from the `skill-prompt-device-adoption` plan — that delivery is correct; this is purely the harness.
- A structural fitness-function test asserting harness internals (`testing.md` functional-only) — verification is the eval producing a behavior-tracking verdict plus a functional test of the deterministic slicing logic over real recorded messages.
- Reworking the `_PRIOR_MSG_COUNT` continuation-slice for multi-turn continuation cases — they are correct; do not regress them.

## Behavioral Constraints

- Continuation cases (multi-turn evals that thread `message_history` forward, e.g. W11 in `eval_multistep_plan.py`) must keep recording only the new turn's messages — the fix must not reintroduce the cumulative-double-count it was added to prevent.
- The fix must be deterministic and LLM-free to verify: the slicing logic is pure given a recorded message list and a cut point.
- No inferred reset signals. The cut point is supplied by the caller (which knows the history it threaded in), not guessed from message-count shape. The fix removes the `_PRIOR_MSG_COUNT` module-level mutable state rather than re-keying it (`feedback_clarity_by_subtraction` — kill state a parameter can carry exactly).

## High-Level Design

**The root defect.** `run_turn` returns the cumulative conversation (`all_messages()` — turns 0..N), so to record only this turn's new messages, `record_turn` chops off the messages that existed before. Today it remembers "how many existed last time" in a module-level `_PRIOR_MSG_COUNT[case_id]` and slices that many. That count is the right cut point *only* when each turn builds on the last. A fresh-history prompt (`message_history=[]`) starts a brand-new, non-cumulative list, but `record_turn` can't tell — it still subtracts the previous prompt's count and discards messages that belong to *this* prompt.

**Why the count-shape heuristic was rejected.** An earlier draft proposed inferring the reset: "if `len(msgs) <= _PRIOR_MSG_COUNT[case_id]` the list didn't grow, so it was reset → `prior=0`." This is unsound. A turn's message count is a function of how many tool-call rounds the model takes — non-deterministic and **not monotonic** across the fresh prompts in one loop. If a later fresh prompt produces a *longer* list than its predecessor (e.g. W4.B's `xlsx` takes one more round than `pptx`), `len(msgs) > prior` holds, the reset goes undetected, and the front of `xlsx`'s own list is sliced off — the identical silent bug, now gated on a coin flip. It can fail W4.B's own later prompts. So it makes the footgun neither impossible nor loud, contrary to Scope.

**The fix — caller-supplied cut point.** The code that launches each turn already knows whether it passed a fresh `message_history=[]` or a threaded continuation, so it knows exactly how many leading messages to drop. Make that explicit: `record_turn` takes a `prior_message_count: int` and slices `msgs[prior_message_count:]` — no remembered state, no inference.
- Fresh-history caller (W4.B) passes `0` → captures the turn's own messages, exactly.
- Continuation caller passes `len(message_history)` (the history it threaded in) → identical to the value `_PRIOR_MSG_COUNT` stored today, exactly — continuation slicing is unchanged.
- `_PRIOR_MSG_COUNT` and the `turn_index == 0` reset branch are **deleted**. `turn_index` survives only as a trace label, not a slicing input.

This is strictly more correct (exact cut, no monotonicity assumption), strictly simpler (removes module-level mutable state), and makes verification a pure function of `(messages, prior_message_count)`. It applies uniformly to every caller — there is no bespoke W4.B path plus a separate infra guard; the audit (TASK-1) already enumerates every call site, so updating each to pass its real cut point is the whole fix.

## TASK-1 Audit Findings

Every `record_turn` call site, classified. The correct cut point is uniformly `len(<the message_history handed to run_turn>)` — `0` for fresh, the threaded length for continuation. (`grep -rn "message_history=\[\]" evals/eval_*.py` cross-checked: groundedness:127 builds `history=[]`; session_recall:159, user_model:182, skills:184, skills:344, multistep_plan:683 are literal `[]`; rule_compliance:564 has no `record_turn` caller — it's a non-traced helper.)

| file:line | loop? | turn_index | message_history arg | class | prior_message_count |
|-----------|-------|-----------|---------------------|-------|---------------------|
| eval_agentic_loop.py:216 | yes | i | h (=history) | continuation | `prior_len` (exists) |
| eval_approval_discipline.py:121 | yes | i | h | continuation | `prior_len` (exists) |
| eval_bounded_autonomy.py:113 | yes | i | h | continuation | `prior_len` (exists) |
| eval_daily_chat.py:210 | yes | i | h | continuation | `prior_len` (exists) |
| eval_context_stability.py:424 | yes | i | h | continuation | `len(history)` |
| eval_context_stability.py:561 | no | _NUM_TURNS | history | continuation | `len(history)` |
| eval_context_stability.py:902 | yes | index | h | continuation | `len(history)` |
| eval_groundedness.py:127 | yes | i | h | fresh→cont (history starts []) | `prior_len` (exists) |
| eval_memory.py:219 | no | 0 | history (=[]) | fresh | `len(history)` |
| eval_memory.py:249 | no | 1 | history | continuation | `len(history)` |
| eval_memory.py:276 | no | 2 | history | continuation | `len(history)` |
| eval_multistep_plan.py:239 | yes | i | h | continuation | `prior_len` (exists) |
| eval_multistep_plan.py:708 | yes | i | h | continuation | `prior_len` (exists) |
| eval_session_continuity.py:124 | no | 0 | history (=[]) | fresh | `len(history)` |
| eval_session_continuity.py:180 | no | 1 | prior_messages | continuation | `len(prior_messages)` |
| eval_session_continuity.py:306 | no | 0 | history (=[]) | fresh | `len(history)` |
| eval_session_continuity.py:327 | yes | i+1 | h (=history) | continuation | `len(history)` |
| eval_session_continuity.py:382 | no | target_turns+1 | post_compact_history | continuation | `len(post_compact_history)` |
| eval_session_recall.py:159 | no | 0 | [] | fresh | `0` |
| eval_user_model.py:182 | yes | i | h | continuation | `prior_len` (exists) |
| eval_skills.py:184 | no | 0 | [] | fresh | `0` |
| eval_skills.py:344 | **yes** | **index** | **[]** | **FRESH-IN-LOOP (the bug)** | `0` |

**Fresh-history-in-a-loop (the bug pattern): `eval_skills.py:344` (W4.B) is the sole occurrence.** W4.A (line 184) is a single `turn_index=0` call (fresh but already correct). All other multi-turn loops thread `message_history` forward (continuation — slicing correct today). So TASK-4 collapses to "W4.B sole occurrence"; the remaining 21 sites are corrected only because `prior_message_count` becomes a required parameter (no silent default), each passing its real cut point.

## Tasks

### ✓ DONE TASK-1 — Audit every `record_turn` caller for the fresh-history pattern
- **files:** (read-only audit; findings recorded in this plan) `evals/eval_skills.py`, `evals/eval_bounded_autonomy.py`, `evals/eval_approval_discipline.py`, `evals/eval_agentic_loop.py`, `evals/eval_context_stability.py`, `evals/eval_memory.py`, `evals/eval_groundedness.py`, `evals/eval_daily_chat.py`, `evals/eval_multistep_plan.py`, `evals/eval_session_continuity.py`
- **done_when:** each `record_turn` call site that runs in a loop is classified **continuation** (threads prior `message_history` forward — slicing correct) or **fresh-history** (`message_history=[]` or equivalent per iteration with `turn_index>0` under one `case_id` — slicing buggy); the classification table with file:line is appended to this plan; `grep -rn "message_history=\[\]" evals/eval_*.py` cross-checked against the loop sites.
- **success_signal:** N/A (audit).

### ✓ DONE TASK-2 — Replace `_PRIOR_MSG_COUNT` with a caller-supplied cut point
- **files:** `evals/_trace.py`
- **prerequisites:** TASK-1
- **done_when:** `record_turn` takes an explicit `prior_message_count: int` parameter and slices `msgs[prior_message_count:]`; the `_PRIOR_MSG_COUNT` dict, its docstring, and the `turn_index == 0` reset branch are removed (`turn_index` remains only as a trace-record label); a functional test over real recorded message lists asserts that (a) a continuation turn given its real prior count records only its new messages and (b) a fresh-history turn given `prior_message_count=0` records its own tool calls (not an empty slice). `uv run pytest <the new/edited trace test> 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task2.log` passes.
- **success_signal:** A fresh-history `record_turn(prior_message_count=0)` call captures its `skill_view` tool call instead of an empty list.

### ✓ DONE TASK-3 — Update every `record_turn` caller to pass its real cut point
- **files:** `evals/eval_skills.py` plus every caller enumerated in TASK-1
- **prerequisites:** TASK-1, TASK-2
- **done_when:** each fresh-history call site passes `prior_message_count=0`; each continuation call site passes `prior_message_count=len(message_history)` (the history it threaded into that turn) — exactly reproducing today's `_PRIOR_MSG_COUNT` value for continuation cases; no caller relies on the removed global. `case_w4_b_skill_selection` passes `0` for all four prompts.
- **success_signal:** every caller compiles against the new signature; continuation traces are byte-identical to pre-change for the same recorded messages.

### ✓ DONE TASK-4 — Verify behavior-tracking verdicts on the affected evals
- **files:** (run-only verification of the evals touched in TASK-3)
- **prerequisites:** TASK-1, TASK-2, TASK-3
- **done_when:** `uv run python evals/eval_skills.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log` runs all four W4.B prompts (no break at index 1) with each prompt's `skill_view` selection captured in its trace line; W4.A/W4.R/W4.M still PASS; any sibling fresh-history eval identified in TASK-1 is re-run and its verdict tracks behavior. **W4.B's verdict reflects the model's actual selections — a real mis-selection on any prompt is reported as a finding, not massaged into PASS** (capture-correctness is verified by xlsx/url now running and being recorded, independent of their verdict).
- **success_signal:** No eval loop silently drops its turn's own tool calls; W4.B's verdict is now a true read of model behavior.

## Testing

- TASK-2 is the core regression gate: a functional pytest over `record_turn`'s slicing, fed a **real recorded message list** (e.g. a captured `case_<id>.jsonl` message sequence — not hand-built mock part objects that drift from pydantic-ai's real shape), asserting that `prior_message_count=0` captures the turn's own tool calls and a continuation cut point records only the new messages. The logic is now a pure function of `(messages, prior_message_count)` — deterministic, LLM-free, asserting observable captured output (not structure).
- TASK-4 verification is the eval itself producing a behavior-tracking verdict (`eval_skills.py` W4.B), per `testing.md` (evals are UAT smoke runs; all data real). The pass condition is that all four prompts run and are captured — not that all four prompts PASS; a genuine model mis-selection is a separate finding.
- Watch LLM call timing on the `eval_skills.py` run; W4.B drives four real turns. Fail-fast / RCA-first on any stalled call — never widen a timeout (`feedback_long_llm_call_rca_first`).
- No floor-guard exposure (no `co_cli/context/rules/*.md` edits).

## Open Questions

- **Resolved at Gate 1.** The mechanism is the explicit caller-supplied `prior_message_count` (not the count-shape heuristic, which is unsound — a fresh prompt longer than its predecessor defeats it, so it can silently fail W4.B's own later prompts). An `int` cut point is more exact than a `fresh_history: bool` (continuation cases need the real prior length, not just "not fresh"), removes the `_PRIOR_MSG_COUNT` global, and the caller already holds the value. Caller churn is not a deciding factor at planning stage (`feedback_decisions_discount_dev_cost`), and the audit touches every caller regardless.

## Delivery Summary — 2026-06-24

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | classify every `record_turn` caller continuation/fresh + table appended | ✓ pass |
| TASK-2 | `record_turn` takes `prior_message_count`; `_PRIOR_MSG_COUNT` + `turn_index==0` branch removed; functional test green | ✓ pass |
| TASK-3 | all 22 callers pass real cut point (`0` fresh / `len(history)` continuation) | ✓ pass |
| TASK-4 | `eval_skills.py` runs all four W4.B prompts, captured; W4.A/R/M still PASS | ✓ pass |

**What changed:**
- `evals/_trace.py` — `record_turn` now takes a required `prior_message_count: int` and slices `messages[prior_message_count:]`. Deleted the module-level `_PRIOR_MSG_COUNT` dict and the `turn_index==0` reset branch. The cut point is the caller's exact knowledge, never inferred from message-count shape.
- 13 eval files (22 call sites) updated: fresh-history calls pass `0`; continuation calls pass `len(<threaded history>)` (most loops already had `prior_len = len(history)`), reproducing the prior continuation slice exactly.
- `tests/test_eval_trace_slicing.py` (new) — functional test over real `TurnResult` + pydantic-ai messages: fresh turn (`prior_message_count=0`) captures its own `skill_view` call; continuation turn records only its new tail.

**Audit result:** W4.B (`eval_skills.py:344`) was the **sole** fresh-history-in-loop bug site. W4.A (line 184) is a single `turn_index=0` call (already correct). The other 20 sites are continuations whose slicing was correct; they changed only to satisfy the now-required parameter.

**Tests:** scoped — `test_eval_trace_slicing.py` (2) + `test_eval_perf.py` (8) = 10 passed, 0 failed. Lint clean.
**Eval:** `eval_skills.py` — W4.A PASS, **W4.B PASS (all 4 prompts evaluated, no break at index 1)**, W4.R PASS, W4.M PASS. Exit 0.
**Doc Sync:** clean — change is eval-harness internal; no `docs/specs/` reference to `record_turn`.

**Overall: DELIVERED**
The false-negative is fixed at the contract boundary: a fresh-history loop can no longer silently drop a turn's own tool calls, and continuation slicing is unchanged. W4.B's verdict now tracks the model's real selection (which is correct).

## Implementation Review — 2026-06-24

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | classify every caller + table appended | ✓ pass | Audit table in plan; independently re-derived cold by reviewer — all 22 sites matched; W4.B (`eval_skills.py:344`) sole fresh-in-loop site, W4.A (`:184`) single `turn_index=0` |
| TASK-2 | `prior_message_count` param; `_PRIOR_MSG_COUNT` + `turn_index==0` branch removed; test green | ✓ pass | `_trace.py:196` required kw-only param; `:250` `msgs[prior_message_count:]`; `grep _PRIOR_MSG_COUNT` → none in source; `:266` `turn_index` still read (not one-sided) |
| TASK-3 | all 22 callers pass real cut point | ✓ pass | 22/22 `prior_message_count=` present; each equals `len(<its own message_history arg>)`; `:180`→`len(prior_messages)`, `:382`→`len(post_compact_history)` correctly paired (not `history`); 12 modules import OK |
| TASK-4 | eval runs all 4 W4.B prompts; W4.A/R/M PASS | ✓ pass | `eval_skills.py` exit 0: W4.A/B/R/M all PASS; W4.B "all 4 prompts selected the right skill" (no break at index 1) |

### Issues Found & Fixed
No issues found. Two parallel evidence reviewers (TASK-2 infra+test, TASK-3 callers) returned zero blocking and zero minor findings with file:line evidence. The TASK-2 reviewer empirically ran a mutation (sliced→no-op) and confirmed the continuation test fails — the slicing logic is genuinely exercised, not structural. The TASK-3 reviewer cold-re-derived the cut-point classification across all 22 sites (the adversarial cross-check) and it matched the audit table, including the two mismatch-risk continuation sites. No auto-fix loop required.

### Tests
- Command: `uv run pytest` (full suite) + `uv run pytest tests/test_eval_trace_slicing.py -v`
- Result: 845 passed, 0 failed (191s); slicing test 2 passed
- All durations ~0.01s — no LLM calls in suite, no stalls
- Log: `.pytest-logs/<ts>-review-impl.log`

### Behavioral Verification
- `uv run co --help`: ✓ boots (import + bootstrap graph loads clean)
- No user-facing surface changed — this is eval-harness internal. The behavioral effect (W4.B capturing each fresh prompt's `skill_view`) is LLM-mediated and verified via the TASK-4 eval run (all four prompts evaluated, verdict tracks behavior); chat interaction non-gating.
- `success_signal` verified: a fresh-history `record_turn(prior_message_count=0)` captures its `skill_view` call instead of an empty slice (TASK-2 test); W4.B false-negative gone (TASK-4 eval).

### Overall: PASS
Capture-correctness fix is complete, evidence-backed, and regression-safe: full suite green, the footgun is removed by construction (caller-supplied cut point, no inference), and continuation slicing is byte-identical to before. Ready to ship.

**Staging note for ship:** only `evals/_trace.py`, the 12 `evals/eval_*.py` files, and `tests/test_eval_trace_slicing.py` belong to this delivery. The other working-tree changes (`co_cli/skills/*`, `docs/reference/RESEARCH-*`, `tests/test_flow_skill_bundled_library.py`, `uv.lock`) pre-date this plan and must NOT be staged.
