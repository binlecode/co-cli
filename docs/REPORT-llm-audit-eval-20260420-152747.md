# REPORT: LLM Audit Eval from Pytest Run

**Date:** 2026-04-20
**Log Source:** `/Users/binle/workspace_genai/co-cli/.pytest-logs/20260420-152422-full.log`
**Trace Source:** `/Users/binle/.co-cli/co-cli-logs.db`

## 1. Scope

Audits LLM chat calls visible in the specified pytest log against OTel spans in the trace DB.
Spans are matched by duration (±150 ms tolerance). Only tests that
exceeded the harness slow threshold (2000 ms) emit per-span detail; tests faster than
this threshold are excluded from this report.

- Chat spans extracted from log: `28`
- DB spans found in time window: `178`
- DB spans matched: `28`
- Unmatched (log-only, no token data): `0`

## 2. Executive Summary

- Visible LLM call spans audited: `28`
- API correctness: `28/28` matched calls used api=`generativelanguage.googleapis.com, localhost:11434`, provider=`google-gla, ollama`
- Models observed: `gemini-3.1-pro-preview`, `qwen3.5:35b-a3b-think`
- Finish reasons:
  - `16` `stop`
  - `12` `tool_call`
- Confirmed output-cut anomalies (`finish_reason=length`): `0`
- Minimal-output warnings (content flows, ≤3 tokens): `0`
- Small `stop` outputs (≤3 tokens, non-content flows): `0`
- Slowest visible call: `12.747s` — tool calling: web

## 3. Per-Call Metrics

| # | Test / Flow | Duration | Finish | In Tokens | Out Tokens | In Chars | Out Chars | Verdict |
|---|---|---:|---|---:|---:|---:|---:|---|
| 1 | `test_approval_approve` / approval | 6.424s | `tool_call` | 4822 | 34 | 273 | 193 | OK |
| 2 | `test_approval_approve` / approval | 5.890s | `stop` | 4726 | 22 | 587 | 214 | OK |
| 3 | `test_approval_deny` / approval | 6.251s | `tool_call` | 4822 | 34 | 273 | 193 | OK |
| 4 | `test_approval_deny` / approval | 5.972s | `stop` | 4729 | 26 | 604 | 210 | OK |
| 5 | `test_circuit_breaker_probes_at_cadence` / history compaction | 7.322s | `stop` | 691 | 286 | 3173 | 1623 | OK |
| 6 | `test_compact_produces_two_message_history` / history | 7.229s | `stop` | 645 | 265 | 2916 | 1444 | OK |
| 7 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 7.877s | `tool_call` | 1210 | 276 | 191 | 1183 | OK |
| 8 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 2.773s | `stop` | 1520 | 72 | 1567 | 460 | OK |
| 9 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 1.254s | `stop` | 428 | 31 | 549 | 264 | OK |
| 10 | `test_gemini_noreason_returns_response` / llm gemini | 3.378s | `stop` | 8 | 157 | 94 | 95 | OK |
| 11 | `test_gemini_reasoning_returns_response` / llm gemini | 2.993s | `stop` | 8 | 110 | 94 | 95 | OK |
| 12 | `test_gemini_noreason_faster_than_reasoning` / llm gemini | 2.692s | `stop` | 8 | 70 | 94 | 95 | OK |
| 13 | `test_gemini_noreason_faster_than_reasoning` / llm gemini | 3.059s | `stop` | 8 | 120 | 94 | 95 | OK |
| 14 | `test_thinking_part_present_when_reasoning_enabled` / llm thinking | 5.297s | `stop` | 17 | 232 | 94 | 1024 | OK |
| 15 | `test_tool_selection_and_arg_extraction[shell_git_status]` / tool calling: shell | 6.105s | `tool_call` | 4815 | 27 | 248 | 168 | OK |
| 16 | `test_tool_selection_and_arg_extraction[shell_git_status]` / tool calling: shell | 4.479s | `stop` | 5389 | 147 | 2133 | 628 | OK |
| 17 | `test_tool_selection_and_arg_extraction[web_search_fastapi]` / tool calling: web | 6.443s | `tool_call` | 4801 | 41 | 197 | 214 | OK |
| 18 | `test_tool_selection_and_arg_extraction[web_search_fastapi]` / tool calling: web | 12.747s | `stop` | 5640 | 486 | 4013 | 2379 | OK |
| 19 | `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` / tool calling | 6.397s | `tool_call` | 4804 | 40 | 217 | 201 | OK |
| 20 | `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` / tool calling | 1.395s | `tool_call` | 4871 | 39 | 618 | 189 | OK |
| 21 | `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` / tool calling | 1.471s | `tool_call` | 4937 | 40 | 1007 | 215 | OK |
| 22 | `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` / tool calling | 1.505s | `tool_call` | 4998 | 39 | 1391 | 203 | OK |
| 23 | `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` / tool calling | 3.493s | `stop` | 5057 | 122 | 1751 | 673 | OK |
| 24 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | 5.933s | `stop` | 4802 | 13 | 166 | 111 | OK |
| 25 | `test_intent_routing_observation_no_tool` / tool calling: no-tool | 7.416s | `stop` | 4797 | 71 | 169 | 388 | OK |
| 26 | `test_clarify_handled_by_run_turn` / tool calling functional | 6.481s | `tool_call` | 4814 | 31 | 253 | 183 | OK |
| 27 | `test_clarify_handled_by_run_turn` / tool calling functional | 6.868s | `tool_call` | 4790 | 31 | 800 | 183 | OK |
| 28 | `test_clarify_handled_by_run_turn` / tool calling functional | 2.091s | `tool_call` | 4910 | 69 | 1347 | 406 | OK |

## 4. Workflow Breakdown

| Flow | Calls | Median Duration | Max Duration | Mean Duration | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| approval | 4 | 6.112s | 6.424s | 6.135s | 4776 | 4822 | 30 | 34 |
| history | 1 | 7.229s | 7.229s | 7.229s | 645 | 645 | 265 | 265 |
| history compaction | 1 | 7.322s | 7.322s | 7.322s | 691 | 691 | 286 | 286 |
| knowledge dream cycle | 3 | 2.773s | 7.877s | 3.968s | 1210 | 1520 | 72 | 276 |
| llm gemini | 4 | 3.026s | 3.378s | 3.031s | 8 | 8 | 115 | 157 |
| llm thinking | 1 | 5.297s | 5.297s | 5.297s | 17 | 17 | 232 | 232 |
| tool calling | 5 | 1.505s | 6.397s | 2.852s | 4937 | 5057 | 40 | 122 |
| tool calling functional | 3 | 6.481s | 6.868s | 5.147s | 4814 | 4910 | 31 | 69 |
| tool calling: no-tool | 2 | 6.675s | 7.416s | 6.675s | 4800 | 4802 | 42 | 71 |
| tool calling: shell | 2 | 5.292s | 6.105s | 5.292s | 5102 | 5389 | 87 | 147 |
| tool calling: web | 2 | 9.595s | 12.747s | 9.595s | 5220 | 5640 | 264 | 486 |

## 5. Findings

### 5.1 API Correctness

All 28 matched calls used: api=`generativelanguage.googleapis.com`, api=`localhost:11434`. No provider drift observed.

### 5.2 Finish Reason Behavior

Finish reasons were `tool_call` and `stop` only — no unexpected terminations or length clipping.

### 5.3 Output Size / Cutting Check

No suspiciously small `stop` outputs or `length` terminations detected.

### 5.4 Thinking Presence

> Proxy signals — not semantic verdicts.

- Thinking presence: `10.7%` (3/28 DB-matched spans)
- Mean thinking-char ratio: `0.682`


## 6. Semantic Evaluation

> Semantic evaluation skipped (--no-eval).
