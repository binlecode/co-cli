# REPORT: LLM Call Audit from Pytest Run

**Date:** 2026-04-19
**Log Source:** `/Users/binle/workspace_genai/co-cli/.pytest-logs/20260418-224904-full.log`
**Trace Source:** `/Users/binle/.co-cli/co-cli-logs.db`

## 1. Scope

Audits LLM chat calls visible in the specified pytest log against OTel spans in the trace DB.
Spans are matched by duration (±150 ms tolerance). Only tests that
exceeded the harness slow threshold (2000 ms) emit per-span detail; tests faster than
this threshold are excluded from this report.

- Chat spans extracted from log: `30`
- DB spans found in time window: `26`
- DB spans matched: `30`
- Unmatched (log-only, no token data): `0`

## 2. Executive Summary

- Visible LLM call spans audited: `30`
- API correctness: `30/30` matched calls used api=`localhost:11434`, provider=`openai`
- Models observed: `qwen3.5:35b-a3b-think`
- Finish reasons:
  - `14` `stop`
  - `16` `tool_call`
- Confirmed output-cut anomalies (`finish_reason=length`): `0`
- Small `stop` outputs (≤3 tokens): `4`
- Slowest visible call: `11.010s` — tool calling: web

## 3. Per-Call Metrics

| # | Test / Flow | Duration | Finish | In Tokens | Out Tokens | In Chars | Out Chars | Verdict |
|---|---|---:|---|---:|---:|---:|---:|---|
| 1 | `test_approval_approve` / approval | 6.677s | `tool_call` | 5008 | 36 | 201 | 205 | OK |
| 2 | `test_approval_approve` / approval | 6.077s | `stop` | 4914 | 22 | 539 | 214 | OK |
| 3 | `test_approval_deny` / approval | 6.563s | `tool_call` | 4985 | 41 | 113 | 214 | OK |
| 4 | `test_approval_deny` / approval | 6.489s | `tool_call` | 5001 | 29 | 176 | 180 | OK |
| 5 | `test_run_extraction_async_indexes_memory_and_advances_cursor` / memory extraction | 6.074s | `stop` | 4986 | 13 | 82 | 111 | OK |
| 6 | `test_run_extraction_async_indexes_memory_and_advances_cursor` / memory extraction | 2.247s | `stop` | 2464 | 2 | — | — | OK, minimal |
| 7 | `test_circuit_breaker_probes_at_cadence` / history compaction | 3.986s | `stop` | 640 | 140 | — | — | OK |
| 8 | `test_compact_produces_two_message_history` / history | 8.344s | `stop` | 594 | 309 | — | — | OK |
| 9 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 4.470s | `tool_call` | 5954 | 117 | 5608 | 664 | OK |
| 10 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 1.055s | `stop` | 1342 | 2 | — | — | OK, minimal |
| 11 | `test_full_cycle_executes_all_phases_with_live_llm` / knowledge dream cycle | 1.144s | `stop` | 428 | 26 | — | — | OK |
| 12 | `test_mine_extracts_artifacts_and_marks_sessions_processed` / knowledge dream mine | 4.390s | `tool_call` | 6160 | 101 | 6647 | 568 | OK |
| 13 | `test_mine_extracts_artifacts_and_marks_sessions_processed` / knowledge dream mine | 1.055s | `stop` | 1343 | 2 | — | — | OK, minimal |
| 14 | `test_mine_extracts_artifacts_and_marks_sessions_processed` / knowledge dream mine | 4.534s | `tool_call` | 1226 | 162 | — | — | OK |
| 15 | `test_mine_extracts_artifacts_and_marks_sessions_processed` / knowledge dream mine | 1.136s | `stop` | 1418 | 2 | — | — | OK, minimal |
| 16 | `test_thinking_part_present_when_reasoning_enabled` / thinking capture | 3.222s | `stop` | 17 | 137 | — | — | OK |
| 17 | `test_tool_selection_and_arg_extraction[shell_git_status]` / tool calling: shell | 6.346s | `tool_call` | 5001 | 29 | — | — | OK |
| 18 | `test_tool_selection_and_arg_extraction[shell_git_status]` / tool calling: shell | 5.751s | `stop` | 5519 | 193 | — | — | OK |
| 19 | `test_tool_selection_and_arg_extraction[web_search_fastapi]` / tool calling: web | 6.526s | `tool_call` | 4985 | 39 | 112 | 201 | OK |
| 20 | `test_tool_selection_and_arg_extraction[web_search_fastapi]` / tool calling: web | 11.010s | `stop` | 5824 | 388 | — | — | OK |
| 21 | `test_tool_selection_and_arg_extraction[search_knowledge_db]` / tool calling: knowledge | 6.670s | `tool_call` | 5008 | 36 | 201 | 205 | OK |
| 22 | `test_tool_selection_and_arg_extraction[search_knowledge_db]` / tool calling: knowledge | 1.571s | `tool_call` | 5051 | 40 | 513 | 204 | OK |
| 23 | `test_tool_selection_and_arg_extraction[search_knowledge_db]` / tool calling: knowledge | 1.609s | `tool_call` | 5112 | 39 | — | — | OK |
| 24 | `test_tool_selection_and_arg_extraction[search_knowledge_db]` / tool calling: knowledge | 1.921s | `tool_call` | 5096 | 53 | 1318 | 324 | OK |
| 25 | `test_tool_selection_and_arg_extraction[search_knowledge_db]` / tool calling: knowledge | 2.403s | `tool_call` | 5238 | 63 | 2017 | 389 | OK |
| 26 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | 6.771s | `tool_call` | 5000 | 31 | 180 | 194 | OK |
| 27 | `test_intent_routing_observation_no_tool` / tool calling: no-tool | 8.688s | `stop` | 4981 | 82 | — | — | OK |
| 28 | `test_request_user_input_handled_by_run_turn` / tool calling functional | 7.960s | `stop` | 4981 | 85 | 85 | 469 | OK |
| 29 | `test_request_user_input_handled_by_run_turn` / tool calling functional | 8.253s | `tool_call` | 4976 | 46 | — | — | OK |
| 30 | `test_request_user_input_handled_by_run_turn` / tool calling functional | 2.523s | `tool_call` | 5111 | 71 | — | — | OK |

## 4. Workflow Breakdown

| Flow | Calls | Median Duration | Max Duration | Mean Duration | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| approval | 4 | 6.526s | 6.677s | 6.451s | 4993 | 5008 | 32 | 41 |
| history | 1 | 8.344s | 8.344s | 8.344s | 594 | 594 | 309 | 309 |
| history compaction | 1 | 3.986s | 3.986s | 3.986s | 640 | 640 | 140 | 140 |
| knowledge dream cycle | 3 | 1.144s | 4.470s | 2.223s | 1342 | 5954 | 26 | 117 |
| knowledge dream mine | 4 | 2.763s | 4.534s | 2.779s | 1380 | 6160 | 52 | 162 |
| memory extraction | 2 | 4.161s | 6.074s | 4.161s | 3725 | 4986 | 8 | 13 |
| thinking capture | 1 | 3.222s | 3.222s | 3.222s | 17 | 17 | 137 | 137 |
| tool calling functional | 3 | 7.960s | 8.253s | 6.245s | 4981 | 5111 | 71 | 85 |
| tool calling: knowledge | 5 | 1.921s | 6.670s | 2.835s | 5096 | 5238 | 40 | 63 |
| tool calling: no-tool | 2 | 7.729s | 8.688s | 7.729s | 4990 | 5000 | 56 | 82 |
| tool calling: shell | 2 | 6.048s | 6.346s | 6.048s | 5260 | 5519 | 111 | 193 |
| tool calling: web | 2 | 8.768s | 11.010s | 8.768s | 5404 | 5824 | 214 | 388 |

## 5. Findings

### 5.1 API Correctness

All 30 matched calls used: api=`localhost:11434`. No provider drift observed.

### 5.2 Finish Reason Behavior

Finish reasons were `tool_call` and `stop` only — no unexpected terminations or length clipping.

### 5.3 Output Size / Cutting Check

4 `stop` call(s) returned ≤3 output tokens. These appear to be intentional minimal acknowledgements, not truncation.

### 5.4 Latency Hotspots (top 5 by max duration)

- **tool calling: web**: max `11.010s`, median `8.768s`
- **tool calling: no-tool**: max `8.688s`, median `7.729s`
- **history**: max `8.344s`, median `8.344s`
- **tool calling functional**: max `8.253s`, median `7.960s`
- **approval**: max `6.677s`, median `6.526s`

## 5.5 Cost & Throughput

> Dollar cost: N/A — local Ollama only. Throughput (tokens/s) is the cost proxy.

- Total input tokens: `118363`
- Total output tokens: `2336`

| Flow | Calls | Total In Tokens | Total Out Tokens | Median Tokens/s | Max Tokens/s | Output/Input Ratio |
|---|---:|---:|---:|---:|---:|---:|
| approval | 4 | 19908 | 128 | 4.9 | 6.2 | 0.006 |
| history | 1 | 594 | 309 | 37.0 | 37.0 | 0.520 |
| history compaction | 1 | 640 | 140 | 35.1 | 35.1 | 0.219 |
| knowledge dream cycle | 3 | 7724 | 145 | 22.7 | 26.2 | 0.019 |
| knowledge dream mine | 4 | 10147 | 267 | 12.5 | 35.7 | 0.026 |
| memory extraction | 2 | 7450 | 15 | 1.5 | 2.1 | 0.002 |
| thinking capture | 1 | 17 | 137 | 42.5 | 42.5 | 8.059 |
| tool calling functional | 3 | 15068 | 202 | 10.7 | 28.1 | 0.013 |
| tool calling: knowledge | 5 | 25505 | 231 | 25.5 | 27.6 | 0.009 |
| tool calling: no-tool | 2 | 9981 | 113 | 7.0 | 9.4 | 0.011 |
| tool calling: shell | 2 | 10520 | 222 | 19.1 | 33.6 | 0.021 |
| tool calling: web | 2 | 10809 | 427 | 20.6 | 35.2 | 0.040 |

## 5.6 Reasoning Signals

> Proxy signals — not semantic verdicts.

- Thinking presence: `0.0%` (0/30 DB-matched spans)
- Mean thinking-char ratio: `—`

> WARNING: 0% thinking presence. Verify that reasoning model settings are active in the spans being audited (TASK-1 prerequisite).

| Flow | DB-Matched Spans | Tool-Call Finish | Stop Finish | Tool-Call Depth |
|---|---:|---:|---:|---:|
| approval | 4 | 3 | 1 | 0.75 |
| history | 1 | 0 | 1 | 0.00 |
| history compaction | 1 | 0 | 1 | 0.00 |
| knowledge dream cycle | 3 | 1 | 2 | 0.33 |
| knowledge dream mine | 4 | 2 | 2 | 0.50 |
| memory extraction | 2 | 0 | 2 | 0.00 |
| thinking capture | 1 | 0 | 1 | 0.00 |
| tool calling functional | 3 | 2 | 1 | 0.67 |
| tool calling: knowledge | 5 | 5 | 0 | 1.00 |
| tool calling: no-tool | 2 | 1 | 1 | 0.50 |
| tool calling: shell | 2 | 1 | 1 | 0.50 |
| tool calling: web | 2 | 1 | 1 | 0.50 |

## 5.7 Semantic Evaluation

> Proxy signals — not verified verdicts. Known biases: length, self-evaluation, non-determinism.

Evaluated 14/30 spans (spans without output_msgs or finish_reason are skipped).

| # | Test Fragment / Flow | Finish | Tool Score | Response Score | Thinking Score | Notes |
|---|---|---|---:|---:|---:|---|
| 1 | `test_approval_approve` / approval | `tool_call` | 2 | N/A | N/A | Tool name and arguments match user request exactly |
| 2 | `test_approval_approve` / approval | `stop` | N/A | 2 | N/A | Response correctly interpreted tool result and addressed user intent |
| 3 | `test_approval_deny` / approval | `tool_call` | 2 | N/A | N/A | Correct tool 'web_search' with appropriate query argument matching user intent |
| 4 | `test_approval_deny` / approval | `tool_call` | 2 | N/A | N/A | Exact tool name matches available list; arguments correctly pass 'git status' as |
| 5 | `test_run_extraction_async_indexes_memory` / memory extraction | `stop` | N/A | 2 | N/A | Model correctly calculated 17×23=391 without tool use. |
| 9 | `test_full_cycle_executes_all_phases_with` / knowledge dream cycle | `tool_call` | 1 | N/A | N/A | model repeated same tool call 3+ times with identical args despite validation er |
| 12 | `test_mine_extracts_artifacts_and_marks_s` / knowledge dream mine | `tool_call` | 1 | N/A | 0 | repeated same tool call 3x after identical validation error; no reasoning shown |
| 19 | `test_tool_selection_and_arg_extraction[w` / tool calling: web | `tool_call` | 2 | N/A | N/A | Correct tool (search_memory) from available list with appropriate arguments matc |
| 21 | `test_tool_selection_and_arg_extraction[s` / tool calling: knowledge | `tool_call` | 2 | N/A | N/A | Correct tool and arguments matching user request |
| 22 | `test_tool_selection_and_arg_extraction[s` / tool calling: knowledge | `tool_call` | 2 | N/A | N/A | search_knowledge is valid tool with correct args |
| 24 | `test_tool_selection_and_arg_extraction[s` / tool calling: knowledge | `tool_call` | 0 | N/A | N/A | 3 identical failing calls without adapting after validation errors; always used  |
| 25 | `test_tool_selection_and_arg_extraction[s` / tool calling: knowledge | `tool_call` | 0 | N/A | N/A | 3+ identical failing tool calls with same args {question} after validation error |
| 26 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | `tool_call` | 2 | N/A | N/A | Tool name correct, arguments match user request exactly |
| 28 | `test_request_user_input_handled_by_run_t` / tool calling functional | `stop` | N/A | 2 | N/A | Response correctly identifies missing function code to address bug-fixing reques |

### Per-Flow Score Summary

| Flow | Spans Evaluated | Mean Tool Score | Mean Response Score | Mean Thinking Score |
|---|---:|---:|---:|---:|
| approval | 4 | 2.00 | 2.00 | N/A |
| knowledge dream cycle | 1 | 1.00 | N/A | N/A |
| knowledge dream mine | 1 | 1.00 | N/A | 0.00 |
| memory extraction | 1 | N/A | 2.00 | N/A |
| tool calling functional | 1 | N/A | 2.00 | N/A |
| tool calling: knowledge | 4 | 1.00 | N/A | N/A |
| tool calling: no-tool | 1 | 2.00 | N/A | N/A |
| tool calling: web | 1 | 2.00 | N/A | N/A |

### Key Findings

- **FLAGGED — knowledge dream cycle**: mean tool_score = 1.00 ≤ 1.0 — investigate for tool selection drift.
- **FLAGGED — knowledge dream mine**: mean tool_score = 1.00 ≤ 1.0 — investigate for tool selection drift.
- **FLAGGED — tool calling: knowledge**: mean tool_score = 1.00 ≤ 1.0 — investigate for tool selection drift.
