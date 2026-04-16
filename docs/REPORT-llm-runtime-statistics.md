# REPORT: LLM Runtime Statistics & Analysis

**Date:** 2026-04-15
**Source:** Analyzed from OTel trace logs (`~/.co-cli/co-cli-logs.db`)

## 1. Executive Summary

This report aggregates the runtime behavior of the `co-cli` agent system based on OpenTelemetry (OTel) spans. The data reflects the real-world execution characteristics of the agent loop, model interactions, tool preferences, and token consumption patterns.

## 2. Model Invocations and Token Usage

### 2.1 Invocation Counts
The agent heavily relies on the configured reasoning model for its core loop, with guardrail intercepts handling specific failure injection tests.

- **Total LLM Calls (`chat` spans):** 3,329
- **Model specific (`qwen3.5:35b-a3b-think`):** 3,287 (98.7% of all calls)
- **Guardrail / Fallback Intercepts:** 42 calls
  - `_always_raise_400`: 18
  - `_400_then_text`: 12
  - `_raise_429`: 6
  - `_raise_long_body`: 6

### 2.2 Token Consumption
The system's token footprint heavily skews towards reading context rather than generating output, validating the "read-first, act concisely" design philosophy.

- **Total Input Tokens:** 12,474,430
- **Total Output Tokens:** 275,122
- **Input to Output Ratio:** ~45.3 to 1

### 2.3 Output Characteristics
- **Tool calls initiated by model (`finish_reason: tool_call`):** 1,794 (~54.6% of responses result in a tool execution).
- **Reasoning block presence (`type: thinking`):** 387 occurrences natively captured in traces for the reasoning model.

### 2.4 Execution Timing (Latency)
Latency statistics for LLM execution calls reveal the impact of different operational modes on response generation time (metrics in seconds).

- **Overall LLM Calls (`chat` spans) (n=3,355)**:
  - **Min**: 0.00s *(Intercepts/Guardrails)*
  - **Max**: 77.34s
  - **Average**: 5.78s
  - **StdDev**: 4.11s

- **Reasoning Mode (Core Turn Execution) (n=2,673)**:
  - **Min**: 0.83s
  - **Max**: 77.34s
  - **Average**: 6.49s
  - **StdDev**: 4.17s

- **No-Reason Mode (Headless Extraction/Summarization) (n=632)**:
  - **Min**: 0.07s
  - **Max**: 17.46s
  - **Average**: 3.22s
  - **StdDev**: 2.29s

*Observation:* Context extraction and summarization runs without `function_tools` overhead or active reasoning capabilities significantly decrease latency. The standard deviation for reasoning bounds reflects high-variance think cycles common in iterative tasks.

## 3. Orchestration & Subsystems

The agent orchestration loop metrics show the distribution of lifecycle events.

| Event | Count | Description |
|---|---|---|
| `invoke_agent agent` | 2,034 | Discrete agent run sessions. |
| `co.turn` | 1,189 | Full user-to-assistant turn lifecycles. |
| `ctx_overflow_check` | 1,145 | Context window boundary safety checks. |
| `restore_session` | 940 | Session resumptions across CLI invocations. |
| `sync_knowledge` | 599 | Background knowledge synchronization runs. |

## 4. Tool Execution Profile

The tool surface usage reveals `run_shell_command` as the dominant execution primitive, followed by memory/knowledge interactions. 

| Tool Name | Execution Count | Category |
|---|---|---|
| `run_shell_command` | 758 | Shell / Execution |
| `search_memories` | 272 | Knowledge / Memory |
| `test` (Mock) | 201 | Testing |
| `web_search` | 160 | Web / Knowledge |
| `save_memory` | 130 | Knowledge / Memory |
| `save_insight` | 85 | Knowledge / Memory |
| `list_memories` | 56 | Knowledge / Memory |
| `search_knowledge` | 18 | Knowledge / Memory |
| `read_file` | 15 | Filesystem |
| `write_file` | 10 | Filesystem |
| `patch` | 10 | Filesystem |
| `glob` | 10 | Filesystem |
| `grep` | 5 | Filesystem |
| `write_todos` | 4 | Workflow / Tasks |
| `search_tools` | 2 | Workflow / Discovery |

### 4.1 Tool Insights
1. **Shell Dominance:** `run_shell_command` accounts for the vast majority of side-effect operations (758 calls), far outpacing native filesystem tools like `write_file` and `patch`. This indicates the model prefers standard terminal commands (like `git`, `cat`, `ls`) over native tool wrappers when permitted.
2. **Memory is Active:** The memory system (`search_memories`, `save_memory`, `list_memories`) is highly active, validating the architecture's focus on personal context retention.
3. **Low Deferred Discovery Use:** `search_tools` was only executed twice, indicating that the category awareness prompt is generally sufficient, or the model rarely ventures outside its always-loaded `ALWAYS` visible toolset.
