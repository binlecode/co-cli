# Eval: tool-calling — PASS

**Date**: 2026-02-08 11:43:05
**Runs per case**: 1  
**Threshold**: 80%  
**Elapsed**: 20.2s  
**Overall accuracy**: 100.0% (26/26)

## Per-Case Results

| Case | Dim | Expected Tool | Result | Runs |
|------|-----|---------------|--------|------|
| ts-shell-01 | tool_selection | `run_shell_command` | **PASS** | 1/1 |
| ts-shell-02 | tool_selection | `run_shell_command` | **PASS** | 1/1 |
| ts-notes-01 | tool_selection | `search_notes` | **PASS** | 1/1 |
| ts-notes-02 | tool_selection | `read_note` | **PASS** | 1/1 |
| ts-notes-03 | tool_selection | `list_notes` | **PASS** | 1/1 |
| ts-drive-01 | tool_selection | `search_drive_files` | **PASS** | 1/1 |
| ts-drive-02 | tool_selection | `read_drive_file` | **PASS** | 1/1 |
| ts-email-01 | tool_selection | `list_emails` | **PASS** | 1/1 |
| ts-email-02 | tool_selection | `create_email_draft` | **PASS** | 1/1 |
| ts-email-03 | tool_selection | `search_emails` | **PASS** | 1/1 |
| ts-cal-01 | tool_selection | `list_calendar_events` | **PASS** | 1/1 |
| ts-cal-02 | tool_selection | `search_calendar_events` | **PASS** | 1/1 |
| ae-shell-01 | arg_extraction | `run_shell_command` | **PASS** | 1/1 |
| ae-shell-02 | arg_extraction | `run_shell_command` | **PASS** | 1/1 |
| ae-email-01 | arg_extraction | `create_email_draft` | **PASS** | 1/1 |
| ae-drive-01 | arg_extraction | `search_drive_files` | **PASS** | 1/1 |
| ae-notes-01 | arg_extraction | `search_notes` | **PASS** | 1/1 |
| ae-cal-01 | arg_extraction | `search_calendar_events` | **PASS** | 1/1 |
| rf-social-01 | refusal | `(none)` | **PASS** | 1/1 |
| rf-summary-01 | refusal | `(none)` | **PASS** | 1/1 |
| rf-rephrase-01 | refusal | `(none)` | **PASS** | 1/1 |
| rf-greeting-01 | refusal | `(none)` | **PASS** | 1/1 |
| rf-preference-01 | refusal | `(none)` | **PASS** | 1/1 |
| er-notes-01 | error_recovery | `search_notes` | **PASS** | 1/1 |
| er-drive-01 | error_recovery | `search_drive_files` | **PASS** | 1/1 |
| er-email-01 | error_recovery | `list_emails` | **PASS** | 1/1 |

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
- **Relative gate**: PASS (no dimension dropped > 10.0%)

## Model Comparison vs Baseline

Baseline saved: 2026-02-08T11:42:39  
Baseline file: `evals/baseline-ollama.json`

| Dimension | Baseline | Current | Delta |
|-----------|----------|---------|-------|
| arg_extraction | 100.0% | 100.0% | 0.0% |
| error_recovery | 100.0% | 100.0% | 0.0% |
| refusal | 100.0% | 100.0% | 0.0% |
| tool_selection | 100.0% | 100.0% | 0.0% |
| **OVERALL** | **100.0%** | **100.0%** | **0.0%** |

No case status changes vs baseline.
