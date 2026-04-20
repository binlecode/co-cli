# REPORT: LLM Runtime Statistics

**Date:** 2026-04-19
**Source:** `/Users/binle/.co-cli/co-cli-logs.db`
**Filter:** `2026-04-01` → `now` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: `2026-04-09 18:54 UTC` → `2026-04-20 02:07 UTC`
- Real model chat spans: `3307`
- Models: `gemini-2.5-flash`, `qwen3.5:35b-a3b-think`
- Providers: `google-gla`, `openai`
- APIs: `generativelanguage.googleapis.com`, `localhost:11434`

## 2. Executive Summary

- Total LLM calls: `3307`
- Total input tokens: `11333040`
- Total output tokens: `281234`
- Input/output ratio: `40.3` to 1
- Tool-call finish: `1426` (43.1%)
- Stop finish: `1845` (55.8%)
- Length-truncated: `0`
- Spans with thinking blocks: `191` (5.8%)
- Finish reasons:
  - `1845` × `stop`
  - `1426` × `tool_call`

## 3. Per-Model Breakdown

| Model | Calls | % | In Tokens | Out Tokens | I/O Ratio | p50 Latency | p95 Latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen3.5:35b-a3b-think` | 3271 | 98.9% | 11332752 | 280803 | 40.4 | 5.778s | 13.098s |
| `gemini-2.5-flash` | 36 | 1.1% | 288 | 431 | 0.7 | 0.619s | 0.807s |

## 4. Latency Profile

**All spans** (n=3307)
- Min: `0.075s`
- p50: `5.751s`
- p95: `13.043s`
- Max: `54.599s`
- Mean: `5.534s`
- StdDev: `4.213s`

**Reasoning mode (thinking blocks present)** (n=191)
- Min: `0.923s`
- p50: `7.868s`
- p95: `21.587s`
- Max: `33.043s`
- Mean: `8.792s`
- StdDev: `6.490s`

**No-reason mode (no thinking blocks)** (n=3116)
- Min: `0.075s`
- p50: `5.720s`
- p95: `12.003s`
- Max: `54.599s`
- Mean: `5.334s`
- StdDev: `3.947s`

## 5. Throughput

- Median tokens/s: `8.8`
- Max tokens/s: `49.1`
- Mean tokens/s: `15.8`

## 6. Orchestration Events

| Event | Count |
|---|---:|
| `invoke_agent agent` | 2416 |
| `co.turn` | 69 |
| `ctx_overflow_check` | 64 |
| `sync_knowledge` | 29 |
| `restore_session` | 29 |

## 7. Tool Execution Profile

| Tool | Calls |
|---|---:|
| `run_shell_command` | 505 |
| `save_knowledge` | 372 |
| `search_knowledge` | 131 |
| `web_search` | 130 |
| `clarify` | 120 |
| `shell` | 104 |
| `search_memories` | 86 |
| `list_knowledge` | 55 |
| `save_memory` | 54 |
| `search_memory` | 50 |
| `request_user_input` | 32 |
| `list_memories` | 24 |
| `check_capabilities` | 3 |
| `web_fetch` | 1 |
| `session_search` | 1 |
| `search_tools` | 1 |
| `read_todos` | 1 |
| `read_article` | 1 |
