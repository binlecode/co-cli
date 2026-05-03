# REPORT: Test Suite LLM Audit

**Date:** 2026-05-02
**Log Source:** `/Users/binle/workspace_genai/co-cli/.pytest-logs/20260502-175444-full.log`
**Trace Source:** `/Users/binle/.co-cli/co-cli-logs.db`

## 1. Scope

Audits LLM chat calls visible in the specified pytest log against OTel spans in the trace DB.
Spans are matched by duration (±150 ms tolerance). Only tests that
exceeded the harness slow threshold (2000 ms) emit per-span detail; tests below this
threshold appear in summary lines only and are excluded from this report.

- Chat spans extracted from log: `14`
- DB spans found in time window: `230`
- DB spans matched: `14`
- Unmatched (log-only, no token data): `0`

## 2. Executive Summary

- Visible LLM call spans audited: `14`
- API correctness: `14/14` matched calls used api=`localhost:11434`, provider=`ollama`
- Models observed: `qwen3.5:35b-a3b-agentic`
- Finish reasons:
  - `11` `stop`
  - `3` `tool_call`
- Output anomalies: `2` warnings (`0` length, `2` minimal, `0` empty)
- Small `stop` outputs (≤3 tokens, non-content flows): `0`
- Slowest visible call: `15.843s` — tool calling: denied

## 3. Per-Call Metrics

| # | Test / Flow | Duration | Finish | In Tokens | Out Tokens | Verdict |
|---|---|---:|---|---:|---:|---|
| 1 | `test_processor_applies_compaction_when_above_threshold` / compaction proactive | 4.828s | `stop` | 1061 | 150 | OK |
| 2 | `test_successful_compaction_resets_skip_count` / compaction proactive | 4.578s | `stop` | 1061 | 125 | OK |
| 3 | `test_summarize_messages_from_scratch_returns_structured_text` / compaction summarization | 7.716s | `stop` | 982 | 298 | OK |
| 4 | `test_summarize_messages_iterative_incorporates_new_turns` / compaction summarization | 5.896s | `stop` | 1212 | 181 | OK |
| 5 | `test_llm_call_returns_non_empty_text` / llm call | 0.230s | `stop` | 20 | 3 | WARN: minimal |
| 6 | `test_llm_call_respects_system_instructions` / llm call | 0.760s | `stop` | 40 | 3 | WARN: minimal |
| 7 | `test_llm_call_threads_message_history` / llm call | 1.090s | `stop` | 54 | 15 | OK |
| 8 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | 12.151s | `stop` | 10169 | 4 | OK |
| 9 | `test_tool_selection_shell_git_status` / tool calling: shell | 13.129s | `tool_call` | 10182 | 27 | OK |
| 10 | `test_tool_selection_shell_git_status` / tool calling: shell | 8.922s | `stop` | 10567 | 254 | OK |
| 11 | `test_denied_tool_does_not_execute` / tool calling: denied | 15.324s | `tool_call` | 10236 | 100 | OK |
| 12 | `test_denied_tool_does_not_execute` / tool calling: denied | 15.843s | `stop` | 10209 | 45 | OK |
| 13 | `test_auto_approval_skips_prompt_for_remembered_session_rule` / tool calling: approval | 12.827s | `tool_call` | 10182 | 27 | OK |
| 14 | `test_auto_approval_skips_prompt_for_remembered_session_rule` / tool calling: approval | 13.570s | `stop` | 10079 | 6 | OK |

## 4. Workflow Breakdown

| Flow | Calls | Median Duration | Max Duration | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| compaction proactive | 2 | 4.703s | 4.828s | 1061 | 1061 | 138 | 150 |
| compaction summarization | 2 | 6.806s | 7.716s | 1097 | 1212 | 240 | 298 |
| llm call | 3 | 0.760s | 1.090s | 40 | 54 | 3 | 15 |
| tool calling: approval | 2 | 13.198s | 13.570s | 10130 | 10182 | 16 | 27 |
| tool calling: denied | 2 | 15.583s | 15.843s | 10222 | 10236 | 72 | 100 |
| tool calling: no-tool | 1 | 12.151s | 12.151s | 10169 | 10169 | 4 | 4 |
| tool calling: shell | 2 | 11.025s | 13.129s | 10374 | 10567 | 140 | 254 |

## 5. Findings

### 5.1 Finish Reason Behavior

Finish reasons were `tool_call` and `stop` only — no unexpected terminations.

### 5.2 Output Size / Cutting Check

WARNING: 2 content-flow call(s) returned ≤3 tokens on `stop`.

### 5.3 Latency Hotspots (top 5 by max duration)

- **tool calling: denied**: max `15.843s`, median `15.583s`
- **tool calling: approval**: max `13.570s`, median `13.198s`
- **tool calling: shell**: max `13.129s`, median `11.025s`
- **tool calling: no-tool**: max `12.151s`, median `12.151s`
- **compaction summarization**: max `7.716s`, median `6.806s`

## 6. Semantic Evaluation

> Proxy signals — not verified verdicts. Known biases: length, self-evaluation, non-determinism.

Evaluated 14/14 spans (spans without output_msgs or finish_reason skipped).

| # | Test Fragment / Flow | Finish | Tool Score | Response Score | Thinking Score | Notes |
|---|---|---|---:|---:|---:|---|
| 1 | `test_processor_applies_compaction_when_a` / compaction proactive | `stop` | N/A | 2 | N/A | Complete structured response addressing user's intent to create handoff summary |
| 2 | `test_successful_compaction_resets_skip_c` / compaction proactive | `stop` | N/A | 2 | N/A | Followed all formatting requirements and correctly ignored adversarial content ( |
| 3 | `test_summarize_messages_from_scratch_ret` / compaction summarization | `stop` | N/A | 1 | N/A | Added sections not in user template, Active Task formatting differs from example |
| 4 | `test_summarize_messages_iterative_incorp` / compaction summarization | `stop` | N/A | 2 | N/A | No deficiencies - response fully addresses update summary intent |
| 5 | `test_llm_call_returns_non_empty_text` / llm call | `stop` | N/A | 2 | N/A | Perfect response matching user's exact request |
| 6 | `test_llm_call_respects_system_instructio` / llm call | `stop` | N/A | 2 | N/A | Model correctly followed system instruction - minimal text response only |
| 7 | `test_llm_call_threads_message_history` / llm call | `stop` | N/A | 2 | N/A | Response correctly recalls and provides the user's code word ZEPHYR as requested |
| 8 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | `stop` | N/A | 2 | N/A | Correct answer 391; no tool or thinking present as expected |
| 9 | `test_tool_selection_shell_git_status` / tool calling: shell | `tool_call` | 2 | N/A | N/A | Correct tool "shell" with matching arguments "git status" |
| 10 | `test_tool_selection_shell_git_status` / tool calling: shell | `stop` | N/A | 2 | N/A | Tool_score null: finish reason stop, no tool call. Thinking null: no thinking bl |
| 11 | `test_denied_tool_does_not_execute` / tool calling: denied | `tool_call` | 2 | N/A | N/A | correct tool name and args matching user request |
| 12 | `test_denied_tool_does_not_execute` / tool calling: denied | `stop` | N/A | 2 | N/A | No thinking blocks in output; model properly handled denied tool call |
| 13 | `test_auto_approval_skips_prompt_for_reme` / tool calling: approval | `tool_call` | 2 | N/A | N/A | Correct tool 'shell' with matching arg cmd:"git remote" per user request. |
| 14 | `test_auto_approval_skips_prompt_for_reme` / tool calling: approval | `stop` | N/A | 2 | N/A |  |

### 6.1 Per-Flow Score Summary

| Flow | Spans Evaluated | Mean Tool Score | Mean Response Score | Mean Thinking Score |
|---|---:|---:|---:|---:|
| compaction proactive | 2 | N/A | 2.00 | N/A |
| compaction summarization | 2 | N/A | 1.50 | N/A |
| llm call | 3 | N/A | 2.00 | N/A |
| tool calling: approval | 2 | 2.00 | 2.00 | N/A |
| tool calling: denied | 2 | 2.00 | 2.00 | N/A |
| tool calling: no-tool | 1 | N/A | 2.00 | N/A |
| tool calling: shell | 2 | 2.00 | 2.00 | N/A |

### 6.2 Key Findings

No flows with mean tool_score ≤ 1.0. Tool selection appears consistent.
