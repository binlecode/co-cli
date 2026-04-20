# REPORT: LLM Runtime Audit

**Date:** 2026-04-20
**Source:** `/Users/binle/.co-cli/co-cli-logs.db`
**Filter:** `all time` → `now` (production service only; excludes pytest and eval)

## 1. Scope

- Span time range: `2026-04-09 18:53 UTC` → `2026-04-20 02:07 UTC`
- Chat spans: `3307`
- Session spans — co.turn: `69` · restore_session: `29` · ctx_overflow_check: `64`
- Tool spans: `1671`
- Role spans: `0`

## 2. LLM Performance

### 2.1 Summary

- Total LLM calls: `3307`
- Models: `gemini-2.5-flash`, `qwen3.5:35b-a3b-think`
- Providers: `google-gla`, `openai`
- APIs: `generativelanguage.googleapis.com`, `localhost:11434`
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

### 2.2 Per-Model Breakdown

| Model | Calls | % | In Tokens | Out Tokens | I/O Ratio | p50 Latency | p95 Latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen3.5:35b-a3b-think` | 3271 | 98.9% | 11332752 | 280803 | 40.4 | 5.778s | 13.098s |
| `gemini-2.5-flash` | 36 | 1.1% | 288 | 431 | 0.7 | 0.619s | 0.807s |

### 2.3 Latency Profile

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

### 2.4 Throughput

- Median tokens/s: `8.8`
- Max tokens/s: `49.1`
- Mean tokens/s: `15.8`

## 3. Session Health

### 3.1 Provider Reliability

- Turns (co.turn): `69`
- Sessions (restore_session + 1): `30`
- Turns with error indicators: `0` (0.0%)
- HTTP error status breakdown:
  - (none detected)
- Turn outcome breakdown:
  - `continue`: `69`

### 3.2 Context Pressure

- ctx_overflow_check spans: `64`
- Overflow checks per session: `2.13`

### 3.3 Session Depth

> Warning: span count mismatch — 29 restore_session span(s) imply 30 session(s) but only 69 co.turn span(s) found.
- Sessions with turn data: `23`
- p50 turns/session: `3.0`
- p95 turns/session: `5.9`
- Max turns/session: `8`
- Min turns/session: `1`

### 3.4 Token Accumulation

- Input tokens per turn (n=69):
  - p50: `8990`
  - p95: `37068`
  - Max: `93048`
- Output tokens per turn (n=69):
  - p50: `105`
  - p95: `515`
  - Max: `728`

## 4. Tool Usage

- Total tool calls: `1671`
- Distinct tools: `18`
- Tools seen: `check_capabilities`, `clarify`, `list_knowledge`, `list_memories`, `read_article`, `read_todos`, `request_user_input`, `run_shell_command`, `save_knowledge`, `save_memory`, `search_knowledge`, `search_memories`, `search_memory`, `search_tools`, `session_search`, `shell`, `web_fetch`, `web_search`

### 4.1 Error Rate by Tool

- Total errors: `0` (0.0%)

| Tool | Calls | Errors | Error Rate |
|---|---:|---:|---:|
| `check_capabilities` | 3 | 0 | 0.0% |
| `clarify` | 120 | 0 | 0.0% |
| `list_knowledge` | 55 | 0 | 0.0% |
| `list_memories` | 24 | 0 | 0.0% |
| `read_article` | 1 | 0 | 0.0% |
| `read_todos` | 1 | 0 | 0.0% |
| `request_user_input` | 32 | 0 | 0.0% |
| `run_shell_command` | 505 | 0 | 0.0% |
| `save_knowledge` | 372 | 0 | 0.0% |
| `save_memory` | 54 | 0 | 0.0% |
| `search_knowledge` | 131 | 0 | 0.0% |
| `search_memories` | 86 | 0 | 0.0% |
| `search_memory` | 50 | 0 | 0.0% |
| `search_tools` | 1 | 0 | 0.0% |
| `session_search` | 1 | 0 | 0.0% |
| `shell` | 104 | 0 | 0.0% |
| `web_fetch` | 1 | 0 | 0.0% |
| `web_search` | 130 | 0 | 0.0% |

### 4.2 Latency by Tool

| Tool | Calls | p50 | p95 | Max |
|---|---:|---:|---:|---:|
| `run_shell_command` | 505 | 0.005s | 0.030s | 0.244s |
| `save_knowledge` | 372 | 0.015s | 0.022s | 0.031s |
| `search_knowledge` | 131 | 0.002s | 0.002s | 0.003s |
| `web_search` | 130 | 0.729s | 1.189s | 2.675s |
| `clarify` | 120 | 0.001s | 0.001s | 0.003s |
| `shell` | 104 | 0.004s | 0.027s | 0.071s |
| `search_memories` | 86 | 0.000s | 0.000s | 0.068s |
| `list_knowledge` | 55 | 0.002s | 0.002s | 0.003s |
| `save_memory` | 54 | 0.009s | 0.013s | 0.017s |
| `search_memory` | 50 | 0.000s | 0.000s | 0.000s |
| `request_user_input` | 32 | 0.000s | 0.001s | 0.001s |
| `list_memories` | 24 | 0.003s | 0.006s | 0.011s |
| `check_capabilities` | 3 | 0.011s | 0.012s | 0.012s |
| `web_fetch` | 1 | 0.669s | 0.669s | 0.669s |
| `read_todos` | 1 | 0.001s | 0.001s | 0.001s |

### 4.3 Result Size Distribution

- Spans with result_size: `22`
- p50: `1256` bytes
- p95: `7763` bytes
- Max: `10426` bytes

| Tool | n | p50 (bytes) | p95 (bytes) | Max (bytes) |
|---|---:|---:|---:|---:|
| `run_shell_command` | 7 | 42 | 107 | 107 |
| `web_search` | 5 | 4073 | 4315 | 4341 |
| `check_capabilities` | 3 | 1264 | 1267 | 1268 |
| `list_memories` | 2 | 4942 | 7643 | 7944 |
| `web_fetch` | 1 | 10426 | 10426 | 10426 |
| `read_todos` | 1 | 132 | 132 | 132 |
| `search_memories` | 1 | 1920 | 1920 | 1920 |
| `search_knowledge` | 1 | 118 | 118 | 118 |
| `session_search` | 1 | 122 | 122 | 122 |

### 4.4 Approval & Source Profile

- Spans with requires_approval attribute: `22`
- Requires approval: `0` (0.0%)
- MCP tools: `0` (0.0%)
- Native tools: `1671` (100.0%)

### 4.5 RAG Backend Distribution

- Total RAG spans: `132`

| Backend | Calls | Share |
|---|---:|---:|
| `grep` | 130 | 98.5% |
| `hybrid` | 1 | 0.8% |
| `fts5` | 1 | 0.8% |

## 5. Role Delegation

_No role spans found._

## 6. Orchestration Events

| Event | Count |
|---|---:|
| `invoke_agent agent` | 2416 |
| `co.turn` | 69 |
| `ctx_overflow_check` | 64 |
| `sync_knowledge` | 29 |
| `restore_session` | 29 |

## 7. Per-Flow Latency

| Flow | Calls | p50 | p95 | Max |
|---|---:|---:|---:|---:|
| approval | 4 | 6.112s | 6.398s | 6.424s |
| history | 1 | 7.229s | 7.229s | 7.229s |
| history compaction | 1 | 7.322s | 7.322s | 7.322s |
| knowledge dream cycle | 3 | 2.773s | 7.367s | 7.877s |
| llm gemini | 4 | 3.026s | 3.330s | 3.378s |
| llm thinking | 1 | 5.297s | 5.297s | 5.297s |
| tool calling | 5 | 1.505s | 5.816s | 6.397s |
| tool calling functional | 3 | 6.481s | 6.829s | 6.868s |
| tool calling: no-tool | 2 | 6.675s | 7.342s | 7.416s |
| tool calling: shell | 2 | 5.292s | 6.023s | 6.105s |
| tool calling: web | 2 | 9.595s | 12.432s | 12.747s |

## 8. Per-Flow Cost

| Flow | Calls | Total In | Total Out | Median In | Median Out | Out/In |
|---|---:|---:|---:|---:|---:|---:|
| approval | 4 | 19099 | 116 | 4776 | 30 | 0.006 |
| history | 1 | 645 | 265 | 645 | 265 | 0.411 |
| history compaction | 1 | 691 | 286 | 691 | 286 | 0.414 |
| knowledge dream cycle | 3 | 3158 | 379 | 1210 | 72 | 0.120 |
| llm gemini | 4 | 32 | 457 | 8 | 115 | 14.281 |
| llm thinking | 1 | 17 | 232 | 17 | 232 | 13.647 |
| tool calling | 5 | 24667 | 280 | 4937 | 40 | 0.011 |
| tool calling functional | 3 | 14514 | 131 | 4814 | 31 | 0.009 |
| tool calling: no-tool | 2 | 9599 | 84 | 4800 | 42 | 0.009 |
| tool calling: shell | 2 | 10204 | 174 | 5102 | 87 | 0.017 |
| tool calling: web | 2 | 10441 | 527 | 5220 | 264 | 0.050 |
