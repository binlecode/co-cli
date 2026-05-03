# REPORT: Test Suite LLM Audit

**Date:** 2026-05-02
**Log Source:** `/Users/binle/workspace_genai/co-cli/.pytest-logs/20260502-174234-full.log`
**Trace Source:** `/Users/binle/.co-cli/co-cli-logs.db`

## 1. Scope

Audits LLM chat calls visible in the specified pytest log against OTel spans in the trace DB.
Spans are matched by duration (±150 ms tolerance). Only tests that
exceeded the harness slow threshold (2000 ms) emit per-span detail; tests below this
threshold appear in summary lines only and are excluded from this report.

- Chat spans extracted from log: `11`
- DB spans found in time window: `216`
- DB spans matched: `11`
- Unmatched (log-only, no token data): `0`

## 2. Executive Summary

- Visible LLM call spans audited: `11`
- API correctness: `11/11` matched calls used api=`localhost:11434`, provider=`ollama`
- Models observed: `qwen3.5:35b-a3b-agentic`
- Finish reasons:
  - `8` `stop`
  - `3` `tool_call`
- Output anomalies: `0` warnings (`0` length, `0` minimal, `0` empty)
- Small `stop` outputs (≤3 tokens, non-content flows): `0`
- Slowest visible call: `15.541s` — tool calling: denied

## 3. Per-Call Metrics

| # | Test / Flow | Duration | Finish | In Tokens | Out Tokens | Verdict |
|---|---|---:|---|---:|---:|---|
| 1 | `test_processor_applies_compaction_when_above_threshold` / compaction proactive | 4.790s | `stop` | 1061 | 141 | OK |
| 2 | `test_successful_compaction_resets_skip_count` / compaction proactive | 4.984s | `stop` | 1061 | 137 | OK |
| 3 | `test_summarize_messages_from_scratch_returns_structured_text` / compaction summarization | 8.015s | `stop` | 982 | 297 | OK |
| 4 | `test_summarize_messages_iterative_incorporates_new_turns` / compaction summarization | 6.227s | `stop` | 1212 | 183 | OK |
| 5 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | 13.696s | `stop` | 10169 | 4 | OK |
| 6 | `test_tool_selection_shell_git_status` / tool calling: shell | 14.189s | `tool_call` | 10182 | 27 | OK |
| 7 | `test_tool_selection_shell_git_status` / tool calling: shell | 3.389s | `stop` | 10551 | 34 | OK |
| 8 | `test_denied_tool_does_not_execute` / tool calling: denied | 15.541s | `tool_call` | 10236 | 102 | OK |
| 9 | `test_denied_tool_does_not_execute` / tool calling: denied | 14.312s | `stop` | 10211 | 38 | OK |
| 10 | `test_auto_approval_skips_prompt_for_remembered_session_rule` / tool calling: approval | 14.462s | `tool_call` | 10182 | 27 | OK |
| 11 | `test_auto_approval_skips_prompt_for_remembered_session_rule` / tool calling: approval | 14.538s | `stop` | 10079 | 6 | OK |

## 4. Workflow Breakdown

| Flow | Calls | Median Duration | Max Duration | Median In Tokens | Max In Tokens | Median Out Tokens | Max Out Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| compaction proactive | 2 | 4.887s | 4.984s | 1061 | 1061 | 139 | 141 |
| compaction summarization | 2 | 7.121s | 8.015s | 1097 | 1212 | 240 | 297 |
| tool calling: approval | 2 | 14.500s | 14.538s | 10130 | 10182 | 16 | 27 |
| tool calling: denied | 2 | 14.926s | 15.541s | 10224 | 10236 | 70 | 102 |
| tool calling: no-tool | 1 | 13.696s | 13.696s | 10169 | 10169 | 4 | 4 |
| tool calling: shell | 2 | 8.789s | 14.189s | 10366 | 10551 | 30 | 34 |

## 5. Findings

### 5.1 Finish Reason Behavior

Finish reasons were `tool_call` and `stop` only — no unexpected terminations.

### 5.2 Output Size / Cutting Check

No `length` terminations or suspiciously small `stop` outputs detected.

### 5.3 Latency Hotspots (top 5 by max duration)

- **tool calling: denied**: max `15.541s`, median `14.926s`
- **tool calling: approval**: max `14.538s`, median `14.500s`
- **tool calling: shell**: max `14.189s`, median `8.789s`
- **tool calling: no-tool**: max `13.696s`, median `13.696s`
- **compaction summarization**: max `8.015s`, median `7.121s`

## 6. Semantic Evaluation

> Proxy signals — not verified verdicts. Known biases: length, self-evaluation, non-determinism.

Evaluated 11/11 spans (spans without output_msgs or finish_reason skipped).

| # | Test Fragment / Flow | Finish | Tool Score | Response Score | Thinking Score | Notes |
|---|---|---|---:|---:|---:|---|
| 1 | `test_processor_applies_compaction_when_a` / compaction proactive | `stop` | N/A | 2 | N/A | Response fully addresses the handoff summary request with correct format |
| 2 | `test_successful_compaction_resets_skip_c` / compaction proactive | `stop` | N/A | 1 | 0 | Active Task should have quoted user's request verbatim, not "None"; thinking is  |
| 3 | `test_summarize_messages_from_scratch_ret` / compaction summarization | `stop` | N/A | 2 | N/A | All sections present, quotes user verbatim, complete structured summary |
| 4 | `test_summarize_messages_iterative_incorp` / compaction summarization | `stop` | N/A | 2 | N/A | Model correctly summarized conversation turn, added test as completed action wit |
| 5 | `test_refusal_no_tool_for_simple_math` / tool calling: no-tool | `stop` | N/A | 2 | N/A | Response correct and complete - 17×23=391 |
| 6 | `test_tool_selection_shell_git_status` / tool calling: shell | `tool_call` | 2 | N/A | N/A | Correct tool 'shell' with valid arguments matching user request |
| 7 | `test_tool_selection_shell_git_status` / tool calling: shell | `stop` | N/A | 2 | N/A | All applicable scores are 2; no deficiencies. |
| 8 | `test_denied_tool_does_not_execute` / tool calling: denied | `tool_call` | 2 | N/A | N/A | Correct tool (file_write) with matching path and content arguments |
| 9 | `test_denied_tool_does_not_execute` / tool calling: denied | `stop` | N/A | 2 | N/A | Response handled denied file_write appropriately, explains blocking constraint |
| 10 | `test_auto_approval_skips_prompt_for_reme` / tool calling: approval | `tool_call` | 2 | N/A | N/A | Correct tool name 'shell' and arguments matching user request |
| 11 | `test_auto_approval_skips_prompt_for_reme` / tool calling: approval | `stop` | N/A | 2 | N/A | response correctly displays git remote output 'origin' as requested |

### 6.1 Per-Flow Score Summary

| Flow | Spans Evaluated | Mean Tool Score | Mean Response Score | Mean Thinking Score |
|---|---:|---:|---:|---:|
| compaction proactive | 2 | N/A | 1.50 | 0.00 |
| compaction summarization | 2 | N/A | 2.00 | N/A |
| tool calling: approval | 2 | 2.00 | 2.00 | N/A |
| tool calling: denied | 2 | 2.00 | 2.00 | N/A |
| tool calling: no-tool | 1 | N/A | 2.00 | N/A |
| tool calling: shell | 2 | 2.00 | 2.00 | N/A |

### 6.2 Key Findings

No flows with mean tool_score ≤ 1.0. Tool selection appears consistent.
