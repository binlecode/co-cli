# TODO: Eval Suite Cleanup & Gap Fill

**Task type: quality** (eval correctness + behavioral coverage)

## Context

Audit completed 2026-03-30 as part of the pydantic-ai upgrade cycle. All 22 evals inspected.
Six runtime-breaking bugs were found and fixed prior to this TODO. This tracks the remaining
gap-fill work plus two newly found blocking bugs.

---

## Problem & Outcome

**Problem:** Nine evals have missing failure modes (policy: every eval must test at least one
failure/degradation/boundary path). Benchmark evals hang or crash instead of reporting SKIP
when a model is unavailable. Two evals have pre-existing bugs that cause `TypeError` on every
run (stale `run_turn()` kwargs from the 0.6.4 refactor).

**Outcome:** Every behavioral/infra eval has at least one failure mode or degradation path.
Benchmark evals exit gracefully when the model is not loaded. Full suite runs as a smoke check
with no hangs or TypeError crashes.

---

## Scope

**Behavioral — logical/functional issues:**
- Fix runtime bugs in knowledge pipeline chain evals (BUG-A, BUG-B)
- Add persistence-failure case to knowledge pipeline eval (TASK-2)
- Add mid-chain error-recovery case to tool chains eval (TASK-3)
- Add history-overflow boundary to conversation history eval (TASK-4)

**Infra — resource management:**
- Add graceful model-unavailable exit to benchmark evals (TASK-1)
- Add empty-input and missing-role failure cases to summarization eval (TASK-5)

**Out of scope:**
- Rewriting evals that are already well-structured
- Adding new evals for uncovered surfaces (separate TODO)
- Changing eval scoring/gate thresholds

---

## Behavioral Constraints

- Evals run against the real configured system — no config overrides, no fake defaults injected
- Each eval must remain a standalone program (`uv run python evals/eval_<name>.py`)
- Pass/fail gates must not be loosened — only add cases, don't change thresholds
- Benchmark evals: graceful SKIP (exit 0) when model not available, not crash

---

## Implementation Plan

### ✓ DONE BUG-A: Fix `run_turn()` signature mismatch

**prerequisites:** none

`eval_knowledge_pipeline.py` and `eval_tool_chains.py` both call `run_turn()` with
`max_request_limit=` and `verbose=` kwargs removed in the 0.6.4 "foreground turn contract
tightening" refactor. Both evals raise `TypeError` on first call. Fix: remove the two stale
kwargs from both call sites.

**files:**
- `evals/eval_knowledge_pipeline.py`
- `evals/eval_tool_chains.py`

**done_when:** Both evals run past the first `run_turn()` call without TypeError.

---

### ✓ DONE BUG-B: Fix credential check dep path in `eval_tool_chains.py`

**prerequisites:** none

`eval_tool_chains.py:178` uses `getattr(deps, case.requires, None)` but `brave_search_api_key`
lives at `deps.config.brave_search_api_key`. `getattr` on `CoDeps` always returns `None`, so
`chain-web-search-fetch` and `research-and-save` are permanently silently skipped even when
the key is configured. Fix: `getattr(deps.config, case.requires, None)`.

**files:**
- `evals/eval_tool_chains.py`

**done_when:** Web-requiring cases run when `BRAVE_SEARCH_API_KEY` is set.

---

### ✓ DONE TASK-1: Add graceful model-unavailable exit to benchmark evals

**prerequisites:** none

Three benchmark evals have no reachability probe. They hang or `sys.exit(1)` on warmup
failure instead of printing SKIP and exiting 0.

Add an `httpx.get(f"{args.host}/api/tags", timeout=5)` probe at startup. On any exception
(connection refused, timeout, HTTP error), print `SKIP: <reason>` and `sys.exit(0)`.
Note: all three evals are fully synchronous (`def main()`) — `asyncio.timeout` and
`ensure_ollama_warm()` (async) cannot be used here.

**files:**
- `evals/eval_benchmark_coding_models.py`
- `evals/eval_benchmark_instruct_models.py`
- `evals/eval_benchmark_thinking_models.py`

**done_when:**
1. `uv run python evals/eval_benchmark_coding_models.py` with Ollama stopped → prints `SKIP:` and exits 0, no hang.
2. Same for the other two.

---

### ✓ DONE TASK-2: Add persistence-failure case to `eval_knowledge_pipeline.py`

**prerequisites:** BUG-A

Currently happy-path only. Add one case inside `main()`:

**Persistence failure**: pass a read-only `knowledge_db_path` (chmod 444 temp dir) —
`save_article` should fail gracefully with a tool error, not crash the agent.

Note: a second proposed case ("override `brave_search_api_key=None`") was removed — it
injects a fake dep, violating the no-fake-config eval constraint. The brave-key-absent path
is already covered by the SKIP guard at the top of the eval.

**files:**
- `evals/eval_knowledge_pipeline.py`

**done_when:** Persistence-failure case runs and passes regardless of whether
`BRAVE_SEARCH_API_KEY` is set.

---

### ✓ DONE TASK-3: Add mid-chain failure case to `eval_tool_chains.py`

**prerequisites:** BUG-A, BUG-B

Add one failure-recovery case: a `run_shell_command` call with a command that exits non-zero.
The agent must acknowledge the failure — not silently drop the chain. `run_shell_command`
raises a `ModelRetry` for non-zero exits, which the agent receives as a `RetryPromptPart`
telling it to fix the command. The model must respond with text acknowledging the failure 
instead of exhausting the retry loop with identical failing tool calls.

Scoring: keyword match on agent response acknowledging the failure ("error", "failed",
"exit code", "non-zero", or similar).

**files:**
- `evals/eval_tool_chains.py`

**done_when:** New case appears in output; agent response acknowledges the failed step.

---

### ✓ DONE TASK-4: Add history-overflow boundary to `eval_conversation_history.py`

**prerequisites:** none

Add a Tier 4 case. `truncate_history_window` is registered on the agent at `build_agent()`
time (`agent.py:230`) and fires during `agent.run()` — the existing eval pattern applies.
No need to switch to `run_turn()`.

Implementation:
- Build synthetic history of exactly `deps.config.max_history_messages + 2`
  `ModelRequest`/`ModelResponse` pairs. Encode a recalled fact in the last 2 messages (tail).
- Call `agent.run(scored_prompt, message_history=synthetic_history)` — same pattern as Tier 1–3.
- Verify truncation fired: inspect `result.all_messages()` for the static trim marker —
  a `UserPromptPart` whose content starts with `"[Earlier conversation trimmed — "`
  (injected by `truncate_history_window` when no pre-computed summary is available).
- Verify the tail fact is still recalled in the agent's response.
- Read `max_history_messages` from `deps.config` — do not hardcode.

**files:**
- `evals/eval_conversation_history.py`

**done_when:** Tier 4 case passes; at least one `UserPromptPart` in `result.all_messages()`
contains the prefix `"[Earlier conversation trimmed — "`; agent correctly recalls the tail fact.

---

### ✓ DONE TASK-5: Add empty-input and missing-role cases to `eval_ollama_openai_summarization.py`

**prerequisites:** none

Currently 4 checkpoints test the happy path. Add:
1. **Empty message list** → `_run_summarization_with_policy([], resolved_model)` does not crash
   (pass `resolved_model` from `_replacement_resolved()`; the empty history goes to the
   summarizer agent which returns a response or None — either is acceptable, crash is not).
2. **Model registry missing summarization role** → `registry.get(ROLE_SUMMARIZATION, fallback)`
   returns the fallback `ResolvedModel(model=None, settings=None)`; calling
   `_run_summarization_with_policy(messages, fallback)` returns `None` or raises a caught
   exception — does not raise `KeyError`. Use `dataclasses.replace()` to build a config with
   `ROLE_SUMMARIZATION` absent from `role_models` — consistent with how the eval already
   constructs modified configs.

Both new cases require Ollama to be up (a valid live model is needed for the empty-input call;
the missing-role case needs a real resolved model to construct the fallback path). Both are
covered by the existing `_require_ollama_provider()` guard — they run only when Ollama is
configured and SKIP otherwise.

Verify `_run_summarization_with_policy` behavior on both inputs before writing assertions.

**files:**
- `evals/eval_ollama_openai_summarization.py`

**done_when:** Both new cases pass; eval exits 0 with Ollama up; SKIP with Ollama down.

---

## Testing

All verification is done by running the individual eval:
```
uv run python evals/eval_<name>.py
```

No new pytest files. If a regression is found during a task, add a targeted functional test
to `tests/` as part of fixing it — not preemptively.

---

## Post-fix Eval Status

State of all 22 evals after every task in this TODO ships, grouped by target goal.

### Behavioral — logical/functional issues

| Goal | Evals | This-TODO fix | Remaining gap |
|------|-------|---------------|---------------|
| Turn safety | `eval_safety_abort_marker`, `eval_safety_grace_turn` | — | mid-tool-call abort; double grace turn |
| Memory signal pipeline | `eval_signal_analyzer` (classifier), `eval_signal_detector_approval` (dispatch), `eval_memory_signal_detection` (E2E) | — | double-negation edge; save failure during approval; known: contradiction dedup miss |
| Memory recall injection | `eval_memory_proactive_recall` | — | malformed YAML memory |
| Multi-turn context | `eval_conversation_history` | TASK-4: history-overflow Tier 4 | — |
| Knowledge pipeline chain | `eval_tool_chains`, `eval_knowledge_pipeline`, `eval_jeff_learns_finch` | BUG-A, BUG-B, TASK-2, TASK-3 | web_fetch error path (jeff only) |
| Personality and skill dispatch | `eval_skill_finch`, `eval_personality_behavior` | — | skill file missing/corrupt; personality-absent fallback |
| Subagent delegation | `eval_thinking_subagent`, `eval_coding_toolchain` | — | ✓ no gaps |
| Session compaction | `eval_real_co_compact` | — | ✓ no gaps |

### Infra — resource management

| Goal | Evals | This-TODO fix | Remaining gap |
|------|-------|---------------|---------------|
| Backend availability and degradation | `eval_bootstrap_e2e` | — | ✓ no gaps |
| Model transport and reasoning config | `eval_ollama_openai_noreason_equivalence`, `eval_ollama_openai_summarization` | TASK-5: empty-input + missing-role | — |
| Search pipeline quality | `eval_reranker_comparison` | — | reranker timeout → FTS5 fallback |
| Model throughput and capacity | `eval_benchmark_coding_models`, `eval_benchmark_instruct_models`, `eval_benchmark_thinking_models` | TASK-1: graceful SKIP (all three) | — |

Remaining gaps (8 goals with open items) are out of scope — tracked for a follow-up.

---

> Gate 1 — APPROVED 2026-03-31
> Re-reviewed 2026-03-31: all 7 items confirmed unshipped; 3 plan corrections applied
> (TASK-4 marker text, TASK-5 resolved_model arg, TASK-5 missing-role description).
> Re-reviewed 2026-03-31 (Skill run): 2 plan corrections applied
> (TASK-1 arg parsing, TASK-3 error propagation accuracy).
> Run: `/orchestrate-dev eval-cleanup-gap-fill`

## Delivery Summary

- BUG-A fixed by removing `max_request_limit` and `verbose` from `run_turn()` calls.
- BUG-B fixed by updating `getattr(deps, ...)` to `getattr(deps.config, ...)`.
- Verified that evals run without TypeError and correctly process credentials.
- TASK-1: Verified benchmark evals include httpx.get probes with timeout=5 and sys.exit(0) on exception.
- TASK-2: Implemented persistence-failure case in `eval_knowledge_pipeline.py` using a read-only temp directory.
- TASK-3: Implemented mid-chain error-recovery case in `eval_tool_chains.py` for shell command failures.
- TASK-4: Verified Tier 4 history-overflow case exists in `eval_conversation_history.py`.
- TASK-5: Verified empty-input and missing-role cases exist in `eval_ollama_openai_summarization.py`.
