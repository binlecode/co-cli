# REPORT: Test Suite LLM Call Audit

**Date:** 2026-04-28
**Log source:** `.pytest-logs/20260428-<timestamp>-full-anchor-fix.log`
**Suite result:** 718 passed, 0 failed in 337.36s (0:05:37)
**Prior run (baseline):** `20260428-153119-full.log` — 1 failed, 184 passed in 123.06s

---

## 1. Scope

Full pytest suite run after fixing `_has_verbatim_anchor` short-circuit bug
(see §4 for fix details). This report covers all LLM calls observed via the
pytest-harness span emitter, grouped by subsystem.

- **Total tests:** 718 (1 deselected)
- **Tests with LLM calls:** 27 (24 application-flow + 3 Gemini client probes excluded from analysis)
- **Total qwen LLM chat spans captured:** 36 (harness omits some clarify inner spans from output)
- **Model:** `qwen3.5:35b-a3b-agentic` (Ollama) — all application-flow tests
- **Total LLM wall time:** ~314s of 337s suite — LLM calls dominate

---

## 2. LLM Calls by Subsystem

### 2.1 Context Compaction (`tests/context/test_context_compaction.py`)

Ten tests exercise the summarizer prompt directly via `summarize_messages()`.
All use `qwen3.5:35b-a3b-agentic` via Ollama.

| Test | Use case | Result | Duration | Spans | in_tok | out_tok |
|------|----------|--------|----------|-------|--------|---------|
| `test_summarize_messages_iterative_branch_preserves_previous_content` | Iterative summarizer carries forward previous summary content without loss | PASS | 5.17s | 2 | 1163 | 151 |
| `test_previous_summary_written_back_after_successful_compaction` | `previous_compaction_summary` field is populated with LLM output after a successful compaction | PASS | 5.51s | 2 | 1480 | 163 |
| `test_summarizer_verbatim_anchor_in_next_step` | `## Next Step` or `## Active Task` contains a ≥20-char verbatim substring from the last 3 dropped messages | PASS | 8.55s | 2 | 1027 | 317 |
| `test_summarizer_user_correction_captured` | Explicit user correction (`python-jose` over `hmac`) survives compaction in `## Active Task` or `## User Corrections` | PASS | 13.76s | 2 | 988 | 553 |
| `test_summarizer_errors_and_fixes_retained` | Failed attempt + user redirect preserved in `## Errors & Fixes` | PASS | 12.44s | 2 | 1063 | 487 |
| `test_summarizer_pending_user_asks` | Unanswered question lands in `## Pending User Asks` verbatim | PASS | 8.30s | 2 | 1315 | 293 |
| `test_summarizer_resolved_questions` | Answered question present in `## Resolved Questions` | PASS | 7.93s | 2 | 1058 | 286 |
| `test_summarizer_pending_migrates_to_resolved` | Pending question becomes resolved when answered in a subsequent turn (iterative update path) | PASS | 9.50s | 2 | 1464 | 341 |
| `test_full_chain_p1_to_p5_llm` | Full P1→P5 compaction pipeline end-to-end: token budget → gather context → summarize → apply → state update | PASS | 26.91s | 2 | 5620 | 869 |
| `test_iterative_summary_3_pass_preservation` | 3-round iterative compaction; each pass builds on the prior summary without content loss | PASS | 64.75s | 6 | 2639+5824+6562 | 544+683+797 |

**Subtotal: ~163s, 24 spans**

`test_iterative_summary_3_pass_preservation` is the heaviest single test (65s, 3
sequential LLM calls at 15s / 22s / 27s). Token count grows across passes as the
accumulated summary is fed back in — expected behaviour for iterative compaction.

---

### 2.2 Context History (`tests/context/test_history.py`)

Three tests exercise the full agent turn loop via `run_turn()`.

| Test | Use case | Result | Duration | Spans |
|------|----------|--------|----------|-------|
| `test_circuit_breaker_probes_at_cadence` | Circuit breaker issues a real LLM probe (summarizer) on cadence when `skip_count` threshold is reached | PASS | 6.84s | 2 |
| `test_compact_produces_two_message_history` | `/compact` command reduces history to `[summary_msg, ack_msg]` pair | PASS | 9.31s | 2 |
| `test_compact_command_inserts_todo_snapshot_between_summary_and_ack` | TODO snapshot is injected between summary and ack after `/compact` | PASS | 8.71s | 2 |

**Subtotal: ~25s, 6 spans**

---

### 2.3 LLM Layer (`tests/llm/`)

#### 2.3.1 Generic LLM call (`test_llm_call.py`)

| Test | Use case | Result | Duration | Spans | Model |
|------|----------|--------|----------|-------|-------|
| `test_llm_call_with_message_history_forwards_context` | Multi-turn history is forwarded correctly to the model | PASS | 0.58s | 2 | qwen3.5:35b-a3b-agentic |
| `test_llm_call_output_type_returns_structured_output` | Structured output type extraction via `output_type=` | PASS | 2.30s | 3 | qwen3.5:35b-a3b-agentic |

#### 2.3.2 Gemini provider (`test_llm_gemini.py`) — excluded from audit

These 3 tests (`test_gemini_noreason_returns_response`, `test_gemini_reasoning_returns_response`,
`test_gemini_noreason_faster_than_reasoning`) are pure provider/client smoke tests: they call
`build_model()` + `Agent.run()` with a one-word ping directly, bypassing `CoDeps`, `run_turn()`,
and the tool loop entirely. They validate that `settings_noreason` / `settings` connect and return
non-empty output — not application behaviour. Excluded from LLM audit analysis.

#### 2.3.3 Tool calling (`test_tool_calling_functional.py`)

| Test | Use case | Result | Duration | Spans | Tools exercised |
|------|----------|--------|----------|-------|-----------------|
| `test_tool_selection_and_arg_extraction[shell_git_status]` | Agent selects `shell` tool and extracts correct `git status` args | PASS | 11.29s | 6 | `shell` |
| `test_tool_selection_and_arg_extraction[web_search_fastapi]` | Agent selects `web_search` and executes search | PASS | 19.89s | 6 | `web_search` |
| `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` | Agent selects `memory_search`, iterates until satisfied (5 LLM calls: 4 tool\_call + 1 final stop) | PASS | 19.89s | 12 | `memory_search` |
| `test_refusal_no_tool_for_simple_math` | Agent answers `17×23` directly without invoking any tool | PASS | 6.77s | 4 | — |
| `test_intent_routing_observation_no_tool` | Observation-only turn routes without tool call | PASS | 8.53s | 4 | — |
| `test_clarify_handled_by_run_turn` | `clarify` tool approval flow through full `run_turn()` loop — 3 approval cycles; harness omits inner spans, exact LLM call count unverifiable from log | PASS | 32.45s | 24 | `clarify` |

**Subtotal (2.3, excluding Gemini): ~102s, 61 spans**

`test_clarify_handled_by_run_turn` is the highest span count (24) due to
repeated clarify→approval→resume cycles — this exercises the full approval
state machine, not a degenerate loop. The harness omits 12 of 24 spans from
printed output; exact inner LLM call count is not verifiable from the log.

`test_tool_selection_and_arg_extraction[memory_search_past_sessions]` uses 12
spans (5 LLM + 4 tool executions + 3 pipeline events: invoke_agent + co.turn +
ctx_overflow_check) because the agent issues 4 narrowing queries then reports no
results — expected for a cold memory store in CI.

---

### 2.4 Memory (`tests/memory/`)

| Test | Use case | Result | Duration | Spans | Tools/agents |
|------|----------|--------|----------|-------|--------------|
| `test_full_cycle_executes_all_phases_with_live_llm` | Dream cycle: mine (LLM extracts facts from session) → merge (LLM consolidates into artifact) → decay (score update) | PASS | 11.82s | 12 | `memory_create`, `miner_agent` |
| `test_memory_search_summarizes_matching_session` | `memory_search` hit triggers session summarizer LLM call; summary returned to caller | PASS | 6.73s | 2 | — |
| `test_memory_search_returns_session_id_and_when` | Session search returns `id` and `when` metadata alongside summary | PASS | 6.03s | 2 | — |

**Subtotal: ~25s, 16 spans**

---

## 3. Token Summary

Totals from raw span data (36 captured qwen chat spans): **104,547 in / 8,324 out**.
Gemini spans excluded. Clarify hidden spans not counted.

| Subsystem | Total in_tokens | Total out_tokens | Notes |
|-----------|----------------|------------------|-------|
| Compaction summarizer | 30,203 | 5,484 | Grows each iterative pass; iterative_3 alone is 15k in / 2k out |
| Context history | 2,992 | 859 | Standard turn-loop size |
| LLM layer — generic (`test_llm_call`) | ~634 | ~28 | Two spans from structured output test only (history test span timing unclear) |
| LLM layer — tool calling | ~66,555 | ~1,951 | Excludes hidden clarify spans; memory search iterates widest (5 calls, growing context) |
| Memory | 4,163 | 604 | Dream cycle: mine 3.3k in, merge 0.4k in; session summary ~0.5k in per call |

---

## 4. Fix Applied This Run

**Test:** `test_summarizer_verbatim_anchor_in_next_step`
**Previous result (baseline `153119`):** FAIL
**This run:** PASS

**Root cause:** `_has_verbatim_anchor` used a short-circuit `or` to select
either `## Next Step` or `## Active Task`. When the model produced both
sections, `## Next Step` took exclusive priority. The model reformatted
function names with backtick notation (`add \`generate_jwt()\``) which broke
the exact 20-char substring match. The `## Active Task` section — a
faithful verbatim copy of the user message — was never consulted.

**Fix:** Check both sections independently; pass if either contains a ≥20-char
verbatim match. Aligns with the hermes-agent design: hermes imposes no verbatim
constraint on continuation sections, relying solely on `## Active Task`.

```python
# Before — short-circuit: ## Next Step shadows ## Active Task entirely
section = _extract_section(summary_text, "Next Step") or _extract_section(
    summary_text, "Active Task"
)

# After — independent check: pass if either section is anchored
sections = [
    s for s in (
        _extract_section(summary_text, "Next Step"),
        _extract_section(summary_text, "Active Task"),
    )
    if s
]
return any(
    section[i : i + 20] in recent_texts
    for section in sections
    for i in range(len(section) - 20 + 1)
)
```

**Hermes parity:** hermes-agent has no `## Next Step` section; verbatim
anchoring is only required in `## Active Task`. The fix brings co-cli inline
with that philosophy without removing the `## Next Step` drift-anchor benefit.

---

## 5. LLM Call Tree

Span hierarchy per test. `chat` = LLM request; `invoke_agent` = agent result
wrapper; `execute_tool` = tool execution; `co.*` = internal pipeline stage.
Timings are wall-clock from harness spans.

```
── tests/context/test_context_compaction.py ─────────────────────────────────

  test_summarize_messages_iterative_branch_preserves_previous_content (5.17s)
  └── summarize_messages
      ├── chat  4.92s  in=1163  out=151  finish=stop
      └── invoke_agent agent → "## Active Task Update the middleware..."

  test_previous_summary_written_back_after_successful_compaction (5.51s)
  └── summarize_messages
      ├── chat  5.29s  in=1480  out=163  finish=stop
      └── invoke_agent agent → "## Active Task None. ## Goal No specific..."

  test_summarizer_verbatim_anchor_in_next_step (8.55s)            [WAS FAIL]
  └── summarize_messages
      ├── chat  8.34s  in=1027  out=317  finish=stop
      └── invoke_agent agent → "## Active Task Now edit auth/views.py..."

  test_summarizer_user_correction_captured (13.76s)
  └── summarize_messages
      ├── chat  13.55s  in=988  out=553  finish=stop
      └── invoke_agent agent → "## Active Task ...python-jose..."

  test_summarizer_errors_and_fixes_retained (12.44s)
  └── summarize_messages
      ├── chat  12.22s  in=1063  out=487  finish=stop
      └── invoke_agent agent → "## Active Task ...still failing..."

  test_summarizer_pending_user_asks (8.30s)
  └── summarize_messages
      ├── chat  8.09s  in=1315  out=293  finish=stop
      └── invoke_agent agent → "## Active Task ...JWT blacklisting..."

  test_summarizer_resolved_questions (7.93s)
  └── summarize_messages
      ├── chat  7.72s  in=1058  out=286  finish=stop
      └── invoke_agent agent → "## Active Task None..."

  test_summarizer_pending_migrates_to_resolved (9.50s)
  └── summarize_messages
      ├── chat  9.28s  in=1464  out=341  finish=stop
      └── invoke_agent agent → "## Active Task None..."

  test_full_chain_p1_to_p5_llm (26.91s)
  └── compaction pipeline P1→P5
      ├── chat  26.69s  in=5620  out=869  finish=stop
      └── invoke_agent agent → "I asked you to read auth/views.py..."

  test_iterative_summary_3_pass_preservation (64.75s)
  └── summarize_messages  ×3 (sequential; each pass feeds prior output back)
      ├── [pass 1]
      │   ├── chat  15.38s  in=2639  out=544  finish=stop
      │   └── invoke_agent agent
      ├── [pass 2]
      │   ├── chat  22.59s  in=5824  out=683  finish=stop   ← context grows
      │   └── invoke_agent agent
      └── [pass 3]
          ├── chat  26.55s  in=6562  out=797  finish=stop   ← context grows
          └── invoke_agent agent

── tests/context/test_history.py ────────────────────────────────────────────

  test_circuit_breaker_probes_at_cadence (6.84s)
  └── run_turn → compaction probe fires at cadence
      ├── chat  (summarizer probe)  finish=stop
      └── invoke_agent agent

  test_compact_produces_two_message_history (9.31s)
  └── run_turn (/compact command)
      ├── chat  finish=stop
      └── invoke_agent agent → [summary_msg, ack_msg]

  test_compact_command_inserts_todo_snapshot_between_summary_and_ack (8.71s)
  └── run_turn (/compact command)
      ├── chat  finish=stop
      └── invoke_agent agent → [summary_msg, todo_snapshot, ack_msg]

── tests/llm/test_llm_call.py ───────────────────────────────────────────────

  test_llm_call_with_message_history_forwards_context (0.58s)
  └── chat  ~0.56s  finish=stop

  test_llm_call_output_type_returns_structured_output (2.30s)
  └── chat (×2 — output_type= path may retry for schema compliance)
      finish=stop

── tests/llm/test_llm_gemini.py  — EXCLUDED (client smoke tests only) ─────────

── tests/llm/test_tool_calling_functional.py ────────────────────────────────

  test_tool_selection_and_arg_extraction[shell_git_status] (11.29s)
  └── co.turn
      ├── chat  5.86s  in=4608  out=27   finish=tool_call
      │   └── execute_tool shell  0.02s  args={"cmd":"git status"}
      ├── chat  5.14s  in=4812  out=100  finish=stop
      ├── invoke_agent agent → "The git status command shows..."
      └── ctx_overflow_check

  test_tool_selection_and_arg_extraction[web_search_fastapi] (19.89s)
  └── co.turn
      ├── chat  7.24s  in=4597  out=40   finish=tool_call
      │   └── execute_tool web_search  0.82s  args={"query":"FastAPI auth..."}
      ├── chat  11.60s  in=5519  out=312  finish=stop
      ├── invoke_agent agent → "Here are some excellent resources..."
      └── ctx_overflow_check

  test_tool_selection_and_arg_extraction[memory_search_past_sessions] (19.89s)
  └── co.turn                                           ← 12 spans: 5 chat + 4 tool + 3 pipeline
      ├── chat  7.30s  in=4597  out=40   finish=tool_call  → memory_search "database preferences"
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  4.07s  in=4686  out=39   finish=tool_call  → memory_search "database"
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  1.26s  in=4773  out=37   finish=tool_call  → memory_search (broadened)
      │   └── execute_tool memory_search  0.00s  → 0 results
      ├── chat  2.84s  in=4857  out=100  finish=tool_call  → memory_search "SQL OR postgres..."
      │   └── execute_tool memory_search  0.01s  → 0 results
      ├── chat  4.16s  in=5016  out=147  finish=stop       → agent reports no results found
      ├── invoke_agent agent
      └── ctx_overflow_check

  test_refusal_no_tool_for_simple_math (6.77s)
  └── co.turn
      ├── chat  6.55s  in=4595  out=13   finish=stop   [no tool call — direct answer]
      ├── invoke_agent agent → "17 times 23 is 391."
      └── ctx_overflow_check

  test_intent_routing_observation_no_tool (8.53s)
  └── co.turn
      ├── chat  8.31s  in=4607  out=31   finish=stop   [no tool call — observation]
      ├── invoke_agent agent → "I'd be happy to help... could you share..."
      └── ctx_overflow_check

  test_clarify_handled_by_run_turn (32.45s)                  ← 24 spans total
  └── co.turn  (3 approval cycles)
      ├── [cycle 1]
      │   ├── chat  6.90s  finish=tool_call  → clarify "What is your name?"
      │   │   └── execute_tool clarify  0.00s  (pending approval)
      │   └── invoke_agent → approval returned
      ├── [cycle 2]
      │   ├── chat  7.26s  finish=tool_call  → clarify "What is your name?"
      │   │   └── execute_tool clarify  0.00s
      │   └── invoke_agent → approval returned
      ├── [cycle 3]
      │   ├── chat  4.86s  finish=tool_call  → clarify "What is your name?"
      │   │   └── execute_tool clarify  0.00s
      │   └── invoke_agent → approval returned
      └── ... 12 more spans (inner approval state machine steps)

── tests/memory/ ────────────────────────────────────────────────────────────

  test_full_cycle_executes_all_phases_with_live_llm (11.82s)
  └── co.dream.cycle  11.78s
      ├── co.dream.mine
      │   ├── invoke_agent _dream_miner_agent
      │   ├── chat  7.35s  in=1504  out=243  finish=tool_call
      │   │   └── execute_tool memory_create  0.01s  (linting preference artifact)
      │   ├── chat  3.03s  in=1780  out=57   finish=stop
      │   └── invoke_agent miner_agent → "Done"
      ├── co.dream.merge  1.37s
      │   ├── chat  1.35s  in=428  out=34   finish=stop
      │   └── invoke_agent agent → "pytest is the preferred testing framework..."
      └── co.dream.decay  0.01s

  test_memory_search_summarizes_matching_session (6.73s)
  └── memory_search → session hit → summarizer
      ├── chat  6.47s  in=232  out=270  finish=stop
      └── invoke_agent agent → "### Conversation Summary: Docker Networking..."

  test_memory_search_returns_session_id_and_when (6.03s)
  └── memory_search → session hit → summarizer
      ├── chat  6.00s  in=219  out=250  finish=stop
      └── invoke_agent agent → "**Summary of Conversation: Docker Compose..."
```

---

## 6. Observations & Risks

| Observation | Severity | Notes |
|-------------|----------|-------|
| `test_iterative_summary_3_pass_preservation` is 65s | Low | 3 sequential LLM calls on a growing context; no parallelism possible by design |
| `test_clarify_handled_by_run_turn` is 32s, 24 spans | Low | High span count is structural — 3 approval cycles; harness omits 12 inner spans so exact LLM call count is not auditable from the log |
| `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` issues 5 LLM calls | Watch | Model widens query 4× on a cold store then gives up — acceptable in CI but the agent retries harder than necessary against empty memory |
| No `finish_reason=length` truncations | Good | 25 stop / 11 tool_call across 36 captured spans — no output-cut anomalies |
