# Eval: personality-behavior — PASS

**Model**: ollama-qwen3:30b-a3b-thinking-2507-q8_0-agentic  
**Date**: 2026-02-27 23:47:13
**Runs per case**: 1  
**Threshold**: 80%  
**Elapsed**: 269.2s  
**Overall accuracy**: 100.0% (4/4)

## Per-Case Results

| Case | Personality | Turns | Result | Runs |
|------|-------------|-------|--------|------|
| finch-no-reassurance | finch | 1 | **PASS** | 1/1 |
| jeff-uncertainty | jeff | 1 | **PASS** | 1/1 |
| finch-db-tradeoffs | finch | 3 | **PASS** | 1/1 |
| jeff-codebase-structure | jeff | 3 | **PASS** | 1/1 |

## Per-Personality Summary

| Personality | Cases | Passed | Accuracy | Drift | Errors |
|-------------|-------|--------|----------|-------|--------|
| finch | 2 | 2 | 100.0% | - | - |
| jeff | 2 | 2 | 100.0% | - | - |
| **OVERALL** | **4** | **4** | **100.0%** | - | - |

## Gates

- **Absolute gate**: PASS (100.0% ≥ 80.0%)
