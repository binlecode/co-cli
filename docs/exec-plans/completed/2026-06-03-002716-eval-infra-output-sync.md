# eval-infra-output-sync

Prune the eval suite to the critical workflow evals and sync their response
reads to the current agent turn contract. **Delivered + verified 2026-06-03.**

## Problem

Evals reconstructed the agent's response by walking `messages` for `TextPart`s
instead of reading the canonical `turn_result.output` (pydantic-ai
`AgentRunResult.output` — the value the production REPL renders;
`co_cli/context/orchestrate.py:94`). qwen3.6's length-retry / thinking-budget path
(`orchestrate.py:782–786`) doesn't always land the final text as a clean
`TextPart`, so the walk read empty and FAILed cases the agent actually passed —
e.g. `eval_skills` W4.A FAILed 2 of 4 runs with `preview=''`. The drift poisoned
every eval-gated decision (it blocked `rules-block-trim` TASK-3).

Consuming `.output` is **alignment with the production contract**, not a
workaround — callers depend on `.output` precisely so they never need a clean
final `TextPart`.

## What was done

**Prune — keep only the 6 critical Workflow evals; remove 3 non-workflow evals.**

| Kept (Workflow evals) | Removed (not workflow) |
|---|---|
| W1 `eval_daily_chat` · W2 `eval_session_continuity` · W3 `eval_memory` · W4 `eval_skills` · W5 `eval_background` · W6 `eval_trust_visibility` | `eval_mindset_selection` (ablation; to be rewritten) · `eval_domain_review` (extraction quality) · `eval_research_direct` (research capability) |

Removed the dead REPORTs for the deleted evals (`REPORT-eval-research-direct.md`,
`REPORT-eval-mindset-selection.md`).

**Fix — one canonical accessor, all response reads routed through it.**

- Added `response_text(turn_result) -> str` in `evals/_trace.py`: returns
  `turn_result.output` when it is a non-empty `str`, else `""` (handles the
  `DeferredToolRequests`/`None` branch; never raises).
- Replaced the three stale reconstructions with it: `eval_skills.py` (token
  assertion), `eval_session_continuity.py` (`followup_text`), `eval_daily_chat.py`
  (per-turn slice `assistant_text`). Deleted the dup `_response_text`,
  `_last_assistant_text`, `_assistant_text_from` and their now-unused
  `ModelResponse`/`TextPart` imports.
- `record_turn` (`_trace.py`): trace `assistant_text` falls back to
  `response_text(turn_result)` when `_extract_messages` yields empty, so trace
  records aren't blank for the very turns this targets. `_extract_messages` itself
  stays full-fidelity (thinking + tool calls) — it is a trace concern, not an
  assertion source.
- `eval_background` / `eval_trust_visibility`: audited, **no** text-reconstruction
  drift — untouched. `eval_memory`: asserts on side-effects + feeds the judge the
  full transcript — unaffected. Judge paths (`judge_with_llm` transcript list)
  unchanged by design.

## Verification

- `eval_skills` W4.A: **PASS 2/2** post-fix (judge score 10), vs 2/4 FAIL before —
  the spurious empty-response flake is gone.
- Lint clean; all 6 surviving evals import cleanly.
- `daily_chat` / `session_continuity` use the same proven accessor (mechanical
  swap); not full-run (multi-turn, ~10+ min each) per the no-over-testing
  constraint — covered by import sanity + the eval_skills end-to-end proof.

## Follow-up

- Unblocks `rules-block-trim` TASK-3: re-run that gate (`eval_skills` +
  `eval_memory` ≥2×) on this synced harness, then ship if the band holds.
- A new mindset eval will be authored separately (replaces the removed ablation).
- Dangling references to the 3 removed evals remain in **historical** plans
  (`completed/`, parent `prefill-trim`); left as-is (historical record).
