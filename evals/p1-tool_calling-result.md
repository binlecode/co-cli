# Eval: tool-calling — PASS

**Model**: ollama-qwen3:30b-a3b-thinking-2507-q8_0-agentic  
**Date**: 2026-02-16 19:34:03
**Runs per case**: 3  
**Threshold**: 80%  
**Elapsed**: 725.8s  
**Overall accuracy**: 85.2% (23/27)

## Per-Case Results

| Case | Dim | Expected Tool | Result | Runs |
|------|-----|---------------|--------|------|
| p1-sel-memory-recall | tool_selection | `recall_memory` | **PASS** | 3/3 |
| p1-sel-memory-save | tool_selection | `save_memory` | **PASS** | 3/3 |
| p1-sel-shell | tool_selection | `run_shell_command` | **FAIL** | 0/3 |
| p1-sel-web-search | tool_selection | `web_search` | **PASS** | 3/3 |
| p1-sel-web-fetch | tool_selection | `web_fetch` | **PASS** | 3/3 |
| p1-sel-calendar | tool_selection | `list_calendar_events` | **PASS** | 3/3 |
| p1-sel-email | tool_selection | `search_emails` | **PASS** | 3/3 |
| p1-sel-notes | tool_selection | `search_notes` | **PASS** | 3/3 |
| p1-sel-drive | tool_selection | `search_drive_files` | **PASS** | 3/3 |
| p1-sel-personality | tool_selection | `load_personality` | **PASS** | 3/3 |
| p1-arg-recall | arg_extraction | `recall_memory` | **PASS** | 3/3 |
| p1-arg-shell | arg_extraction | `run_shell_command` | **PASS** | 3/3 |
| p1-arg-web-search | arg_extraction | `web_search` | **PASS** | 3/3 |
| p1-arg-email | arg_extraction | `search_emails` | **PASS** | 3/3 |
| p1-ref-math | refusal | `(none)` | **PASS** | 3/3 |
| p1-ref-explain | refusal | `(none)` | **PASS** | 3/3 |
| p1-ref-greeting | refusal | `(none)` | **PASS** | 3/3 |
| p1-rec-notes | error_recovery | `search_notes` | **PASS** | 3/3 |
| p1-rec-drive | error_recovery | `search_drive_files` | **PASS** | 3/3 |
| p1-int-obs-bug | intent | `(none)` | **PASS** | 3/3 |
| p1-int-obs-slow | intent | `(none)` | **PASS** | 3/3 |
| p1-int-hypo-cache | intent | `(none)` | **FAIL** | 0/3 |
| p1-int-ambig-schema | intent | `(none)` | **FAIL** | 0/3 |
| p1-int-dir-check | intent | `run_shell_command` | **PASS** | 3/3 |
| p1-int-dir-fix | intent | `run_shell_command` | **PASS** | 3/3 |
| p1-int-inquiry-why | intent | `(none)` | **FAIL** | 1/3 |
| p1-int-stress-messy | intent | `(none)` | **PASS** | 3/3 |

## Per-Dimension Summary

| Dimension | Cases | Passed | Accuracy | Errors |
|-----------|-------|--------|----------|--------|
| arg_extraction | 4 | 4 | 100.0% | - |
| error_recovery | 2 | 2 | 100.0% | - |
| intent | 8 | 5 | 62.5% | - |
| refusal | 3 | 3 | 100.0% | - |
| tool_selection | 10 | 9 | 90.0% | - |
| **OVERALL** | **27** | **23** | **85.2%** | - |

## Gates

- **Absolute gate**: PASS (85.2% ≥ 80.0%)

## Failed / Error Cases

### p1-sel-shell — FAIL
- **Prompt**: 'Show me the disk usage of the current directory.'
- **Expected tool**: `run_shell_command`
- Run 1: FAIL — tool=`None`, args=`null`
- Run 2: FAIL — tool=`None`, args=`null`
- Run 3: FAIL — tool=`None`, args=`null`

### p1-int-hypo-cache — FAIL
- **Prompt**: 'What if we added caching?'
- **Expected tool**: `None`
- Run 1: FAIL — tool=`recall_memory`, args=`{"query": "caching"}`
- Run 2: FAIL — tool=`recall_memory`, args=`{"query": "caching"}`
- Run 3: FAIL — tool=`recall_memory`, args=`{"query": "caching"}`

### p1-int-ambig-schema — FAIL
- **Prompt**: 'The database schema needs work'
- **Expected tool**: `None`
- Run 1: FAIL — tool=`recall_memory`, args=`{"query": "database schema"}`
- Run 2: FAIL — tool=`recall_memory`, args=`{"query": "database schema"}`
- Run 3: FAIL — tool=`recall_memory`, args=`{"query": "database schema"}`

### p1-int-inquiry-why — FAIL
- **Prompt**: 'Why does login fail?'
- **Expected tool**: `None`
- Run 1: PASS — tool=`None`, args=`null`
- Run 2: FAIL — tool=`recall_memory`, args=`{"query": "login failure"}`
- Run 3: FAIL — tool=`recall_memory`, args=`{"query": "login failure"}`
