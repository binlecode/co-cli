# REPORT: Test Suite LLM Call Audit

**Date:** 2026-04-28
**Log source:** `.pytest-logs/20260428-161324-review-impl.log`
**Suite result:** 725 passed, 0 failed in 335.51s (0:05:35)
**Prior run (baseline):** `20260428-160329-full-anchor-fix.log` — 718 passed, 0 failed in 337.36s
**Scope change vs baseline:** +7 tests; Gemini calls excluded from this report (not part of workflow logic).

---

## 1. Scope

Full pytest suite run as part of `/review-impl dream-pipeline-fixes`. This report covers all
Ollama LLM calls observed via the pytest-harness span emitter, grouped by subsystem.

- **Total tests:** 725
- **Tests with LLM calls (Ollama):** 27
- **Total LLM chat spans (Ollama):** ~44
- **Model:** `qwen3.5:35b-a3b-agentic` (Ollama, `localhost:11434`)
- **Total LLM wall time:** ~297s of 335s suite — LLM calls still dominate

---

## 2. LLM Calls by Subsystem

### 2.1 Context Compaction (`tests/context/test_context_compaction.py`)

Ten tests exercise the summarizer prompt directly via `summarize_messages()`.

| Test | Use case | Result | Duration | Spans | in_tok | out_tok |
|------|----------|--------|----------|-------|--------|---------|
| `test_summarize_messages_iterative_branch_preserves_previous_content` | Iterative summarizer carries forward previous summary without loss | PASS | 5.31s | 2 | 1163 | 156 |
| `test_previous_summary_written_back_after_successful_compaction` | `previous_compaction_summary` field populated after successful compaction | PASS | 6.13s | 2 | 1480 | 189 |
| `test_summarizer_verbatim_anchor_in_next_step` | `## Next Step` or `## Active Task` contains ≥20-char verbatim substring | PASS | 8.71s | 2 | 1027 | 322 |
| `test_summarizer_user_correction_captured` | Explicit user correction survives compaction | PASS | 9.26s | 2 | 988 | 350 |
| `test_summarizer_errors_and_fixes_retained` | Failed attempt + user redirect preserved in `## Errors & Fixes` | PASS | 12.26s | 2 | 1063 | 477 |
| `test_summarizer_pending_user_asks` | Unanswered question lands in `## Pending User Asks` verbatim | PASS | 9.45s | 2 | 1315 | 344 |
| `test_summarizer_resolved_questions` | Answered question present in `## Resolved Questions` | PASS | 8.39s | 2 | 1058 | 305 |
| `test_summarizer_pending_migrates_to_resolved` | Pending question becomes resolved when answered in subsequent turn | PASS | 9.25s | 2 | 1464 | 327 |
| `test_full_chain_p1_to_p5_llm` | Full P1→P5 compaction pipeline end-to-end | PASS | 21.39s | 2 | 5620 | 634 |
| `test_iterative_summary_3_pass_preservation` | 3-round iterative; each pass builds on prior summary | PASS | 55.55s | 6 | 2639+5656+6288 | 460+546+633 |

**Subtotal: ~146s, 24 spans**

`test_iterative_summary_3_pass_preservation` is the heaviest (55s, 3 sequential LLM calls). Token
growth across passes confirms the accumulated summary is fed back in as expected. Slightly faster
than baseline (65s) — non-deterministic response length variation.

---

### 2.2 Context History (`tests/context/test_history.py`)

Three tests exercise the full agent turn loop via `run_turn()`.

| Test | Use case | Result | Duration | Spans | in_tok | out_tok |
|------|----------|--------|----------|-------|--------|---------|
| `test_circuit_breaker_probes_at_cadence` | Circuit breaker issues summarizer probe at cadence | PASS | 5.45s | 2 | 992 | 181 |
| `test_compact_produces_two_message_history` | `/compact` reduces history to `[summary_msg, ack_msg]` | PASS | 7.96s | 2 | 992 | 263 |
| `test_compact_command_inserts_todo_snapshot_between_summary_and_ack` | TODO snapshot injected between summary and ack after `/compact` | PASS | 9.96s | 2 | 1008 | 350 |

**Subtotal: ~23s, 6 spans**

---

### 2.3 LLM Layer (`tests/llm/`)

#### 2.3.1 Generic LLM call (`test_llm_call.py`)

| Test | Use case | Result | Duration | Spans | in_tok | out_tok |
|------|----------|--------|----------|-------|--------|---------|
| `test_llm_call_with_message_history_forwards_context` | Multi-turn history forwarded correctly to the model | PASS | 0.58s | 2 | 277 | 2 |
| `test_llm_call_output_type_returns_structured_output` | Structured output type extraction via `output_type=` | PASS | 2.33s | 3 | 357 | 26 |

#### 2.3.2 Tool calling (`test_tool_calling_functional.py`)

| Test | Use case | Result | Duration | Spans | Tools exercised |
|------|----------|--------|----------|-------|-----------------|
| `test_tool_selection_and_arg_extraction[shell_git_status]` | Agent selects `shell` tool with correct `git status` args | PASS | 12.70s | 6 | `shell` |
| `test_tool_selection_and_arg_extraction[web_search_fastapi]` | Agent selects `web_search` and executes search | PASS | 20.39s | 6 | `web_search` |
| `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` | Agent searches memory, escalates to `memory_list` on repeated misses | PASS | 21.98s | 14 | `memory_search`, `memory_list` |
| `test_refusal_no_tool_for_simple_math` | Agent answers `17×23` directly without tool call | PASS | 7.19s | 4 | — |
| `test_intent_routing_observation_no_tool` | Observation-only turn routes without tool call | PASS | 8.39s | 4 | — |
| `test_clarify_handled_by_run_turn` | `clarify` tool approval flow — 3 approval cycles | PASS | 40.21s | 31 | `clarify` |

**Subtotal: ~110s, 62 spans**

`test_tool_selection_and_arg_extraction[memory_search_past_sessions]` now uses 14 spans (up from 12)
and invokes `memory_list` as a 5th tool call after 4 failed `memory_search` attempts. The agent
browses the artifact list as a final fallback before reporting no results.

`test_clarify_handled_by_run_turn` uses 31 spans (up from 24 in baseline). The increase is
attributable to the new memory-surface tool instrumentation adding `co.knowledge.*` spans for
tool invocations inside the approval state machine path.

---

### 2.4 Memory (`tests/memory/`)

| Test | Use case | Result | Duration | Spans | Tools/agents |
|------|----------|--------|----------|-------|--------------|
| `test_full_cycle_executes_all_phases_with_live_llm` | Dream cycle: mine (LLM extracts facts) → merge (LLM consolidates artifact) → decay | PASS | 13.92s | 12 | `memory_create`, `miner_agent` |
| `test_memory_search_summarizes_matching_session` | `memory_search` hit triggers session summarizer LLM call | PASS | 6.92s | 2 | — |
| `test_memory_search_returns_session_id_and_when` | Session search returns `id` and `when` metadata alongside summary | PASS | 7.72s | 2 | — |

**Subtotal: ~29s, 16 spans**

Dream cycle mine phase: chat 8.18s (in=1504, out=271, finish=tool_call → `memory_create`), then
chat 4.36s (in=1807, out=112, finish=stop). Merge: chat 1.27s (in=428, out=29, finish=stop).
All finish reasons are `stop`/`tool_call` — no `length` truncation observed.

---

## 3. Token Summary

| Subsystem | Approx. total in_tokens | Approx. total out_tokens | Notes |
|-----------|------------------------|--------------------------|-------|
| Compaction summarizer | ~29k | ~3.6k | Grows per iterative pass; 3rd pass at 6288 |
| Context history | ~3k | ~0.8k | Compact loop uses small contexts (~992 in) |
| LLM layer — generic | ~0.6k | ~0.03k | Direct llm_call, no system prompt |
| LLM layer — tool calling | ~45k | ~1.1k | Memory search now iterates 5× on cold store |
| Memory | ~4k | ~0.8k | Dream mine prompt ~1500 tokens |

---

## 4. LLM Call Tree

Span hierarchy per test. `chat` = LLM request; `invoke_agent` = agent result wrapper;
`execute_tool` = tool execution; `co.*` = internal pipeline stage.

```
── tests/context/test_context_compaction.py ─────────────────────────────────

  test_summarize_messages_iterative_branch_preserves_previous_content (5.31s)
  └── summarize_messages
      ├── chat  5.06s  in=1163  out=156  finish=stop
      └── invoke_agent agent

  test_previous_summary_written_back_after_successful_compaction (6.13s)
  └── summarize_messages
      ├── chat  5.91s  in=1480  out=189  finish=stop
      └── invoke_agent agent

  test_summarizer_verbatim_anchor_in_next_step (8.71s)
  └── summarize_messages
      ├── chat  8.50s  in=1027  out=322  finish=stop
      └── invoke_agent agent

  test_summarizer_user_correction_captured (9.26s)
  └── summarize_messages
      ├── chat  9.04s  in=988   out=350  finish=stop
      └── invoke_agent agent

  test_summarizer_errors_and_fixes_retained (12.26s)
  └── summarize_messages
      ├── chat  12.04s in=1063  out=477  finish=stop
      └── invoke_agent agent

  test_summarizer_pending_user_asks (9.45s)
  └── summarize_messages
      ├── chat  9.24s  in=1315  out=344  finish=stop
      └── invoke_agent agent

  test_summarizer_resolved_questions (8.39s)
  └── summarize_messages
      ├── chat  8.17s  in=1058  out=305  finish=stop
      └── invoke_agent agent

  test_summarizer_pending_migrates_to_resolved (9.25s)
  └── summarize_messages
      ├── chat  9.04s  in=1464  out=327  finish=stop
      └── invoke_agent agent

  test_full_chain_p1_to_p5_llm (21.39s)
  └── compaction pipeline P1→P5
      ├── chat  21.18s in=5620  out=634  finish=stop
      └── invoke_agent agent

  test_iterative_summary_3_pass_preservation (55.55s)
  └── summarize_messages  ×3 (sequential; each pass feeds prior output back)
      ├── [pass 1]
      │   ├── chat  13.49s in=2639  out=460  finish=stop
      │   └── invoke_agent agent
      ├── [pass 2]
      │   ├── chat  19.27s in=5656  out=546  finish=stop   ← context grows
      │   └── invoke_agent agent
      └── [pass 3]
          ├── chat  22.55s in=6288  out=633  finish=stop   ← context grows
          └── invoke_agent agent

── tests/context/test_history.py ────────────────────────────────────────────

  test_circuit_breaker_probes_at_cadence (5.45s)
  └── run_turn → compaction probe fires at cadence
      ├── chat  5.24s  in=992   out=181  finish=stop
      └── invoke_agent agent

  test_compact_produces_two_message_history (7.96s)
  └── run_turn (/compact command)
      ├── chat  7.75s  in=992   out=263  finish=stop
      └── invoke_agent agent → [summary_msg, ack_msg]

  test_compact_command_inserts_todo_snapshot_between_summary_and_ack (9.96s)
  └── run_turn (/compact command)
      ├── chat  9.75s  in=1008  out=350  finish=stop
      └── invoke_agent agent → [summary_msg, todo_snapshot, ack_msg]

── tests/llm/test_llm_call.py ───────────────────────────────────────────────

  test_llm_call_with_message_history_forwards_context (0.58s)
  └── chat  1.03s  in=277  out=2   finish=stop

  test_llm_call_output_type_returns_structured_output (2.33s)
  └── chat  1.10s  in=357  out=26  finish=tool_call

── tests/llm/test_tool_calling_functional.py ────────────────────────────────

  test_tool_selection_and_arg_extraction[shell_git_status] (12.70s)
  └── co.turn
      ├── chat  5.87s  in=4608  out=27   finish=tool_call
      │   └── execute_tool shell  0.03s  args={"cmd":"git status"}
      ├── chat  6.53s  in=4861  out=159  finish=stop
      ├── invoke_agent agent → "The `git status` command shows..."
      └── ctx_overflow_check

  test_tool_selection_and_arg_extraction[web_search_fastapi] (20.39s)
  └── co.turn
      ├── chat  7.10s  in=4594  out=41   finish=tool_call
      │   └── execute_tool web_search  0.47s  args={"query":"FastAPI authentication..."}
      ├── chat  12.59s in=5519  out=360  finish=stop
      ├── invoke_agent agent → "Here are some excellent FastAPI..."
      └── ctx_overflow_check

  test_tool_selection_and_arg_extraction[memory_search_past_sessions] (21.98s)
  └── co.turn
      ├── chat  7.27s  finish=tool_call  → memory_search "database preferences"
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  4.05s  finish=tool_call  → memory_search "database"
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  1.51s  finish=tool_call  → memory_search (broadened query)
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  1.36s  finish=tool_call  → memory_search (broadened further)
      │   └── execute_tool memory_search  0.00s  → 0 results      ← NEW vs baseline
      ├── chat  2.31s  finish=tool_call  → memory_list limit=20   ← NEW vs baseline
      │   └── execute_tool memory_list   0.01s  → artifact list
      ├── chat  5.21s  finish=stop       → reports no relevant results found
      ├── invoke_agent agent
      └── ctx_overflow_check + co.turn

  test_refusal_no_tool_for_simple_math (7.19s)
  └── co.turn
      ├── chat  6.98s  in=4595  out=13   finish=stop  [no tool call]
      ├── invoke_agent agent → "17 times 23 is 391."
      └── ctx_overflow_check

  test_intent_routing_observation_no_tool (8.39s)
  └── co.turn
      ├── chat  8.17s  in=4590  out=67   finish=stop  [no tool call]
      ├── invoke_agent agent → "I'd be happy to help... could you share..."
      └── ctx_overflow_check

  test_clarify_handled_by_run_turn (40.21s)            ← 31 spans total (+7 vs baseline)
  └── co.turn  (3 approval cycles)
      ├── [cycle 1]
      │   ├── chat  7.15s  in=4607  out=31   finish=tool_call  → clarify
      │   │   └── execute_tool clarify  0.00s  (pending)
      │   └── invoke_agent → approval returned; execute_tool clarify (approved)
      ├── [cycle 2]
      │   ├── chat  7.81s  in=4583  out=57   finish=tool_call  → clarify
      │   │   └── execute_tool clarify  0.00s
      │   └── invoke_agent → approval; execute_tool clarify
      ├── [cycle 3]
      │   ├── chat  4.78s  in=4729  out=60   finish=tool_call  → clarify
      │   │   └── execute_tool clarify  0.00s
      │   └── invoke_agent → approval; execute_tool clarify
      └── ... 19 more spans (approval state machine + memory-surface tool hooks)

── tests/memory/ ────────────────────────────────────────────────────────────

  test_full_cycle_executes_all_phases_with_live_llm (13.92s)
  └── co.dream.cycle  13.87s
      ├── co.dream.mine
      │   ├── invoke_agent _dream_miner_agent
      │   ├── chat  8.18s  in=1504  out=271  finish=tool_call
      │   │   └── co.knowledge.memory_create  0.01s
      │   │   └── execute_tool memory_create  0.01s
      │   │       args={content: "User always prefers ruff for linting...", artifact_kind: "preference"}
      │   ├── chat  4.36s  in=1807  out=112  finish=stop
      │   └── invoke_agent miner_agent → "Done"
      ├── co.dream.merge  1.28s
      │   ├── chat  1.27s  in=428   out=29   finish=stop
      │   └── invoke_agent agent → "pytest is the preferred testing framework..."
      └── co.dream.decay  0.00s

  test_memory_search_summarizes_matching_session (6.92s)
  └── memory_search → session hit → summarizer
      ├── chat  6.63s  in=232   out=276  finish=stop
      └── invoke_agent agent → "### Conversation Summary: Docker Networking..."

  test_memory_search_returns_session_id_and_when (7.72s)
  └── memory_search → session hit → summarizer
      ├── chat  7.69s  in=219   out=322  finish=stop
      └── invoke_agent agent → "**Summary of Conversation: Docker Compose..."
```

---

## 5. Prompt Parity Gap Observations

These are LLM-call-level observations that map to known gaps in `docs/exec-plans/active/2026-04-28-081359-main-flow-prompt-parity.md`.

| Observation | Gap | Evidence | Status |
|-------------|-----|----------|--------|
| `memory_search_past_sessions` escalates to `memory_list` after 4 failed searches | G2 — tool guidance ships unconditionally | spans=14, tools=`memory_search,memory_list`; `memory_list` invoked at 5th iteration (in=4962) | Unaddressed — Phase 2 of parity plan gates memory guidance on tool availability |
| System prompt first-call tokens stable at ~4594-4608 for all `run_turn` tests | G1 — static content in per-turn callback (cache smell) | `shell_git_status` in=4608, `web_search_fastapi` in=4594, `memory_search` in=4597, `refusal` in=4595, `intent_routing` in=4590, `clarify` in=4607 | Unaddressed — Phase 1 of parity plan moves static shell guidance out of `@agent.instructions` callback |
| `clarify` span count 31 vs 24 baseline | Memory-surface tool hooks now emit `co.knowledge.*` spans | 19 omitted spans include approval state machine + new tool invocation spans | Not a parity gap — instrumentation expansion from memory-surface unification |
| All `finish_reason=stop` (no `length` truncation observed) | — | All chat calls end at `finish=stop` or `finish=tool_call` | Good |
| Compaction 3-pass test 55s vs 65s baseline | — | Same 3-pass structure; response token counts slightly lower (460/546/633 vs 544/683/797) | Non-deterministic variation; no regression |

**Memory retry proliferation (G2):** The agent escalates `memory_list` only after exhausting all
`memory_search` variants (4 calls). This is worse than the baseline (which used 4 `memory_search`
calls, 12 spans) — the agent now makes an additional 5th tool call + LLM turn (14 spans). The
parity plan's bounded empty-result retry rule (Phase 1, `memory_search` section of `04_tool_protocol.md`)
would cap this at 2 retries before surfacing "no results found."

---

## 6. Observations & Risks

| Observation | Severity | Notes |
|-------------|----------|-------|
| `test_iterative_summary_3_pass_preservation` is 55s | Low | 3 sequential LLM calls on growing context; no parallelism possible by design. Faster than baseline (65s) |
| `test_clarify_handled_by_run_turn` is 40s, 31 spans | Low | +7 spans vs baseline from new memory-surface instrumentation hooks; approval logic unchanged |
| `memory_search_past_sessions` now invokes `memory_list` as 6th tool call | Watch | Agent uses an additional LLM turn to browse artifacts after 4 search misses — G2 parity gap. Not a failure, but increases cold-store loop length. Parity plan Phase 1 bounds this. |
| All `finish_reason=stop` (no `length` truncations) | Good | No output-cut anomalies |
| Dream cycle `memory_create` tool call correct | Good | Span confirms `co.knowledge.memory_create` + `execute_tool memory_create` fire correctly with `artifact_kind=preference` — post-unification tool wiring verified |
