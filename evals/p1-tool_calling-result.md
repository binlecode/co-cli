# Eval: tool-calling — FAIL

**Model**: ollama-qwen3:30b-a3b-thinking-2507-q8_0-agentic  
**Date**: 2026-02-15 12:42:10
**Runs per case**: 3  
**Threshold**: 80%  
**Elapsed**: 552.0s  
**Overall accuracy**: 73.7% (14/19)

## Per-Case Results

| Case | Dim | Expected Tool | Result | Runs |
|------|-----|---------------|--------|------|
| p1-sel-memory-recall | tool_selection | `recall_memory` | **PASS** | 3/3 |
| p1-sel-memory-save | tool_selection | `save_memory` | **FAIL** | 0/3 |
| p1-sel-shell | tool_selection | `run_shell_command` | **PASS** | 3/3 |
| p1-sel-web-search | tool_selection | `web_search` | **PASS** | 3/3 |
| p1-sel-web-fetch | tool_selection | `web_fetch` | **FAIL** | 1/3 |
| p1-sel-calendar | tool_selection | `list_calendar_events` | **PASS** | 3/3 |
| p1-sel-email | tool_selection | `search_emails` | **PASS** | 3/3 |
| p1-sel-notes | tool_selection | `search_notes` | **PASS** | 3/3 |
| p1-sel-drive | tool_selection | `search_drive_files` | **PASS** | 3/3 |
| p1-sel-personality | tool_selection | `load_personality` | **PASS** | 3/3 |
| p1-arg-recall | arg_extraction | `recall_memory` | **FAIL** | 0/3 |
| p1-arg-shell | arg_extraction | `run_shell_command` | **FAIL** | 0/3 |
| p1-arg-web-search | arg_extraction | `web_search` | **PASS** | 3/3 |
| p1-arg-email | arg_extraction | `search_emails` | **PASS** | 3/3 |
| p1-ref-math | refusal | `(none)` | **PASS** | 3/3 |
| p1-ref-explain | refusal | `(none)` | **FAIL** | 1/3 |
| p1-ref-greeting | refusal | `(none)` | **PASS** | 3/3 |
| p1-rec-notes | error_recovery | `search_notes` | **PASS** | 3/3 |
| p1-rec-drive | error_recovery | `search_drive_files` | **PASS** | 3/3 |

## Per-Dimension Summary

| Dimension | Cases | Passed | Accuracy | Errors |
|-----------|-------|--------|----------|--------|
| arg_extraction | 4 | 2 | 50.0% | - |
| error_recovery | 2 | 2 | 100.0% | - |
| refusal | 3 | 2 | 66.7% | - |
| tool_selection | 10 | 8 | 80.0% | - |
| **OVERALL** | **19** | **14** | **73.7%** | - |

## Gates

- **Absolute gate**: FAIL (73.7% < 80.0%)

## Failed / Error Cases

### p1-sel-memory-save — FAIL
- **Prompt**: 'Remember that I prefer dark mode in all my editors.'
- **Expected tool**: `save_memory`
- Run 1: FAIL — tool=`recall_memory`, args=`{"query": "dark mode"}`
- Run 2: FAIL — tool=`recall_memory`, args=`{"query": "dark mode"}`
- Run 3: FAIL — tool=`recall_memory`, args=`{"query": "dark mode"}`

### p1-sel-web-fetch — FAIL
- **Prompt**: 'Fetch and summarize the content at https://example.com'
- **Expected tool**: `web_fetch`
- Run 1: PASS — tool=`web_fetch`, args=`{"url": "https://example.com"}`
- Run 2: FAIL — tool=`None`, args=`null`
- Run 3: FAIL — tool=`None`, args=`null`

### p1-arg-recall — FAIL
- **Prompt**: 'Do I have any memories about database preferences?'
- **Expected tool**: `recall_memory`
- **Expected args**: `{"query": "database preferences"}`
- Run 1: FAIL — tool=`recall_memory`, args=`{"query": "database preference"}`
- Run 2: FAIL — tool=`recall_memory`, args=`{"query": "database preference"}`
- Run 3: FAIL — tool=`recall_memory`, args=`{"query": "database preference"}`

### p1-arg-shell — FAIL
- **Prompt**: 'Run git status to see what files have changed.'
- **Expected tool**: `run_shell_command`
- **Expected args**: `{"command": "git status"}`
- Run 1: FAIL — tool=`run_shell_command`, args=`{"cmd": "git status"}`
- Run 2: FAIL — tool=`run_shell_command`, args=`{"cmd": "git status"}`
- Run 3: FAIL — tool=`run_shell_command`, args=`{"cmd": "git status"}`

### p1-ref-explain — FAIL
- **Prompt**: 'Explain the difference between TCP and UDP.'
- **Expected tool**: `None`
- Run 1: PASS — tool=`None`, args=`null`
- Run 2: FAIL — tool=`web_search`, args=`{"query": "TCP UDP difference", "max_results": 3}`
- Run 3: FAIL — tool=`web_search`, args=`{"max_results": 3, "query": "TCP UDP difference"}`
