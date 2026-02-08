# Eval: tool-calling — PASS

**Model**: ollama-glm-4.7-flash:q8_0  
**Date**: 2026-02-08 15:47:07
**Runs per case**: 3  
**Threshold**: 80%  
**Elapsed**: 206.2s  
**Overall accuracy**: 100.0% (26/26)

## Per-Case Results

| Case | Dim | Expected Tool | Result | Runs |
|------|-----|---------------|--------|------|
| ts-shell-01 | tool_selection | `run_shell_command` | **PASS** | 3/3 |
| ts-shell-02 | tool_selection | `run_shell_command` | **PASS** | 3/3 |
| ts-notes-01 | tool_selection | `search_notes` | **PASS** | 3/3 |
| ts-notes-02 | tool_selection | `read_note` | **PASS** | 3/3 |
| ts-notes-03 | tool_selection | `list_notes` | **PASS** | 3/3 |
| ts-drive-01 | tool_selection | `search_drive_files` | **PASS** | 3/3 |
| ts-drive-02 | tool_selection | `read_drive_file` | **PASS** | 3/3 |
| ts-email-01 | tool_selection | `list_emails` | **PASS** | 3/3 |
| ts-email-02 | tool_selection | `create_email_draft` | **PASS** | 3/3 |
| ts-email-03 | tool_selection | `search_emails` | **PASS** | 3/3 |
| ts-cal-01 | tool_selection | `list_calendar_events` | **PASS** | 3/3 |
| ts-cal-02 | tool_selection | `search_calendar_events` | **PASS** | 3/3 |
| ae-shell-01 | arg_extraction | `run_shell_command` | **PASS** | 3/3 |
| ae-shell-02 | arg_extraction | `run_shell_command` | **PASS** | 3/3 |
| ae-email-01 | arg_extraction | `create_email_draft` | **PASS** | 3/3 |
| ae-drive-01 | arg_extraction | `search_drive_files` | **PASS** | 3/3 |
| ae-notes-01 | arg_extraction | `search_notes` | **PASS** | 3/3 |
| ae-cal-01 | arg_extraction | `search_calendar_events` | **PASS** | 3/3 |
| rf-social-01 | refusal | `(none)` | **PASS** | 3/3 |
| rf-summary-01 | refusal | `(none)` | **PASS** | 3/3 |
| rf-rephrase-01 | refusal | `(none)` | **PASS** | 3/3 |
| rf-greeting-01 | refusal | `(none)` | **PASS** | 3/3 |
| rf-preference-01 | refusal | `(none)` | **PASS** | 3/3 |
| er-notes-01 | error_recovery | `search_notes` | **PASS** | 3/3 |
| er-drive-01 | error_recovery | `search_drive_files` | **PASS** | 3/3 |
| er-email-01 | error_recovery | `list_emails` | **PASS** | 3/3 |

## Per-Dimension Summary

| Dimension | Cases | Passed | Accuracy | Errors |
|-----------|-------|--------|----------|--------|
| arg_extraction | 6 | 6 | 100.0% | - |
| error_recovery | 3 | 3 | 100.0% | - |
| refusal | 5 | 5 | 100.0% | - |
| tool_selection | 12 | 12 | 100.0% | - |
| **OVERALL** | **26** | **26** | **100.0%** | - |

## Gates

- **Absolute gate**: PASS (100.0% ≥ 80.0%)

## Model Comparison

| Model | arg_extraction | error_recovery | refusal | tool_selection | OVERALL |
|-------|     ---|     ---|     ---|     ---|---------|
| gemini-2.0-flash | 100.0% (6/6) | 100.0% (3/3) | 100.0% (5/5) | 100.0% (12/12) | **100.0%** |
| ollama-glm-4.7-flash:q8_0 | 100.0% (6/6) | 100.0% (3/3) | 100.0% (5/5) | 100.0% (12/12) | **100.0%** |
| ollama-glm-4.7-flash:q8_0 (current) | 100.0% (6/6) | 100.0% (3/3) | 100.0% (5/5) | 100.0% (12/12) | **100.0%** |

No case status changes vs baseline.
