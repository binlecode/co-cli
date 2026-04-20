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
- Empty `stop` outputs (0 tokens): `0`
- Minimal-output warnings (content flows, ≤3 tokens): `0`
- Small `stop` outputs (≤3 tokens, non-content flows): `0`
- Max call depth (LLM calls per test): `5`
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

| Flow | Calls | Median Duration | Max Duration | Mean Duration | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens | Models |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| approval | 4 | 6.112s | 6.424s | 6.135s | 4776 | 4822 | 30 | 34 | `qwen3.5:35b-a3b-think` |
| history | 1 | 7.229s | 7.229s | 7.229s | 645 | 645 | 265 | 265 | `qwen3.5:35b-a3b-think` |
| history compaction | 1 | 7.322s | 7.322s | 7.322s | 691 | 691 | 286 | 286 | `qwen3.5:35b-a3b-think` |
| knowledge dream cycle | 3 | 2.773s | 7.877s | 3.968s | 1210 | 1520 | 72 | 276 | `qwen3.5:35b-a3b-think` |
| llm gemini | 4 | 3.026s | 3.378s | 3.031s | 8 | 8 | 115 | 157 | `gemini-3.1-pro-preview` |
| llm thinking | 1 | 5.297s | 5.297s | 5.297s | 17 | 17 | 232 | 232 | `qwen3.5:35b-a3b-think` |
| tool calling | 5 | 1.505s | 6.397s | 2.852s | 4937 | 5057 | 40 | 122 | `qwen3.5:35b-a3b-think` |
| tool calling functional | 3 | 6.481s | 6.868s | 5.147s | 4814 | 4910 | 31 | 69 | `qwen3.5:35b-a3b-think` |
| tool calling: no-tool | 2 | 6.675s | 7.416s | 6.675s | 4800 | 4802 | 42 | 71 | `qwen3.5:35b-a3b-think` |
| tool calling: shell | 2 | 5.292s | 6.105s | 5.292s | 5102 | 5389 | 87 | 147 | `qwen3.5:35b-a3b-think` |
| tool calling: web | 2 | 9.595s | 12.747s | 9.595s | 5220 | 5640 | 264 | 486 | `qwen3.5:35b-a3b-think` |

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


### 5.5 Call Depth per Test

- Tests with >1 LLM call: `8` / `15`
- Max depth: `5`

> WARNING: high call depth — `test_tool_selection_and_arg_extraction[memory_search_past_sessions]` (5 calls) — possible retry spiral.

| Test | Calls | Finish Sequence | Input Token Range |
|---|---:|---|---|
| `test_tool_selection_and_arg_extraction[m` | 5 | tool_call → tool_call → tool_call → tool_call → stop | 4804 → 5057 (+253) |
| `test_full_cycle_executes_all_phases_with` | 3 | tool_call → stop → stop | 1210 → 428 (+-782) |
| `test_clarify_handled_by_run_turn` | 3 | tool_call → tool_call → tool_call | 4814 → 4910 (+96) |
| `test_approval_approve` | 2 | tool_call → stop | 4822 → 4726 (+-96) |
| `test_approval_deny` | 2 | tool_call → stop | 4822 → 4729 (+-93) |
| `test_gemini_noreason_faster_than_reasoni` | 2 | stop → stop | 8 → 8 (+0) |
| `test_tool_selection_and_arg_extraction[s` | 2 | tool_call → stop | 4815 → 5389 (+574) |
| `test_tool_selection_and_arg_extraction[w` | 2 | tool_call → stop | 4801 → 5640 (+839) |
| `test_circuit_breaker_probes_at_cadence` | 1 | stop | 691 |
| `test_compact_produces_two_message_histor` | 1 | stop | 645 |
| `test_gemini_noreason_returns_response` | 1 | stop | 8 |
| `test_gemini_reasoning_returns_response` | 1 | stop | 8 |
| `test_thinking_part_present_when_reasonin` | 1 | stop | 17 |
| `test_intent_routing_observation_no_tool` | 1 | stop | 4797 |
| `test_refusal_no_tool_for_simple_math` | 1 | stop | 4802 |

### 5.6 Per-Flow Thinking Distribution


> WARNING: flows expected to use reasoning had 0 thinking blocks: `history compaction` — verify model reasoning settings.

| Flow | Matched Spans | With Thinking | Presence % | Thinking Expected |
|---|---:|---:|---:|---|
| approval | 4 | 0 | 0% | — |
| history | 1 | 0 | 0% | — |
| history compaction | 1 | 0 | 0% | yes ⚠ |
| knowledge dream cycle | 3 | 2 | 67% | yes |
| llm gemini | 4 | 0 | 0% | — |
| llm thinking | 1 | 1 | 100% | yes |
| tool calling | 5 | 0 | 0% | — |
| tool calling functional | 3 | 0 | 0% | — |
| tool calling: no-tool | 2 | 0 | 0% | — |
| tool calling: shell | 2 | 0 | 0% | — |
| tool calling: web | 2 | 0 | 0% | — |

## 6. Semantic Evaluation

> Proxy signals — not verified verdicts. Known biases: length, self-evaluation, non-determinism.

Evaluated 28/28 spans (spans without output_msgs or finish_reason are skipped).

| # | Test Fragment / Flow | Finish | Tool Score | Response Score | Thinking Score | Notes |
|---|---|---|---:|---:|---:|---|
| 1 | `test_approval_approve` / approval | `tool_call` | 2 | N/A | N/A | Correct tool (shell) with matching arguments (git rev-parse --is-inside-work-tre |
| 2 | `test_approval_approve` / approval | `stop` | N/A | 2 | N/A | Response directly reports tool result to user as requested |
| 3 | `test_approval_deny` / approval | `tool_call` | 2 | N/A | N/A | None - tool name correct, arguments match user request exactly |
| 4 | `test_approval_deny` / approval | `stop` | N/A | 2 | N/A | Stop finish - tool call already made correctly, model handled denied action appr |
| 5 | `test_circuit_breaker_probes_at_cadence` / history compaction | `stop` | N/A | 2 | N/A | All sections included, first sentence starts "I asked you...", user perspective  |
| 6 | `test_compact_produces_two_message_histor` / history | `stop` | N/A | 2 | N/A | response follows format, notes history lacks substantive content |
| 7 | `test_full_cycle_executes_all_phases_with` / knowledge dream cycle | `tool_call` | 2 | N/A | 2 | Correct tool (knowledge_save) with appropriate arguments extracting user's linti |
| 8 | `test_full_cycle_executes_all_phases_with` / knowledge dream cycle | `stop` | N/A | 2 | 2 | response appropriately signals task completion with minimal acknowledgment; reas |
| 9 | `test_full_cycle_executes_all_phases_with` / knowledge dream cycle | `stop` | N/A | 2 | N/A | None - response correctly summarizes user's preference entries |
| 10 | `test_gemini_noreason_returns_response` / llm gemini | `stop` | N/A | 2 | N/A | Model correctly provided exactly one word response as requested |
| 11 | `test_gemini_reasoning_returns_response` / llm gemini | `stop` | N/A | 2 | N/A | Model correctly followed instruction - exactly one word response as requested. |
| 12 | `test_gemini_noreason_faster_than_reasoni` / llm gemini | `stop` | N/A | 2 | N/A | Response matched request - single word reply |
| 13 | `test_gemini_noreason_faster_than_reasoni` / llm gemini | `stop` | N/A | 2 | N/A | N/A - response score is 2, no deficiencies |
| 14 | `test_thinking_part_present_when_reasonin` / llm thinking | `stop` | N/A | 2 | 2 | Response met constraint exactly - 'yes' is one word as requested. Thinking coher |
| 15 | `test_tool_selection_and_arg_extraction[s` / tool calling: shell | `tool_call` | 2 | N/A | N/A | Correct tool 'shell' with matching args. No thinking blocks present (null). |
| 16 | `test_tool_selection_and_arg_extraction[s` / tool calling: shell | `stop` | N/A | 2 | N/A | All correct - shell tool with proper cmd argument, response summarizes git outpu |
| 17 | `test_tool_selection_and_arg_extraction[w` / tool calling: web | `tool_call` | 2 | N/A | N/A | web_search tool correct, args match request |
| 18 | `test_tool_selection_and_arg_extraction[w` / tool calling: web | `stop` | N/A | 2 | N/A | Response comprehensively answers user's search request with relevant tutorials a |
| 19 | `test_tool_selection_and_arg_extraction[m` / tool calling | `tool_call` | 2 | N/A | N/A | memory_search correctly used for past conversation query |
| 20 | `test_tool_selection_and_arg_extraction[m` / tool calling | `tool_call` | 1 | N/A | N/A | Repeated memory_search with query change only, no adaptation after empty result |
| 21 | `test_tool_selection_and_arg_extraction[m` / tool calling | `tool_call` | 1 | N/A | N/A | Used knowledge_search, not memory_search - user asked about past session history |
| 22 | `test_tool_selection_and_arg_extraction[m` / tool calling | `tool_call` | 2 | N/A | N/A | Valid tool name, reasonable adaptation with broader query after specific query f |
| 23 | `test_tool_selection_and_arg_extraction[m` / tool calling | `stop` | N/A | 2 | N/A | No thinking blocks present; response directly addresses user's question explaini |
| 24 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | `stop` | N/A | 2 | N/A | Correct math answer (17×23=391), directly addresses user intent |
| 25 | `test_intent_routing_observation_no_tool` / tool calling: no-tool | `stop` | N/A | 2 | N/A | User input was vague; model appropriately asked clarifying questions to understa |
| 26 | `test_clarify_handled_by_run_turn` / tool calling functional | `tool_call` | 2 | N/A | N/A | Tool name correct, arguments match user request exactly |
| 27 | `test_clarify_handled_by_run_turn` / tool calling functional | `tool_call` | 1 | N/A | N/A | Repeated same tool call after validation error, arguments don't match tool schem |
| 28 | `test_clarify_handled_by_run_turn` / tool calling functional | `tool_call` | 0 | N/A | N/A | 3 identical failing tool calls without adapting after validation errors - kept u |

### 6.1 Per-Flow Score Summary

| Flow | Spans Evaluated | Mean Tool Score | Mean Response Score | Mean Thinking Score |
|---|---:|---:|---:|---:|
| approval | 4 | 2.00 | 2.00 | N/A |
| history | 1 | N/A | 2.00 | N/A |
| history compaction | 1 | N/A | 2.00 | N/A |
| knowledge dream cycle | 3 | 2.00 | 2.00 | 2.00 |
| llm gemini | 4 | N/A | 2.00 | N/A |
| llm thinking | 1 | N/A | 2.00 | 2.00 |
| tool calling | 5 | 1.50 | 2.00 | N/A |
| tool calling functional | 3 | 1.00 | N/A | N/A |
| tool calling: no-tool | 2 | N/A | 2.00 | N/A |
| tool calling: shell | 2 | 2.00 | 2.00 | N/A |
| tool calling: web | 2 | 2.00 | 2.00 | N/A |

### 6.2 Key Findings

- **FLAGGED — tool calling functional**: mean tool_score = 1.00 ≤ 1.0 — see per-span notes for specific failure mode.
