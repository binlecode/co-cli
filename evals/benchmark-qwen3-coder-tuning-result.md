# Eval: benchmark-qwen3-coder-tuning

**Models tested**: 
1. `qwen3.5:35b-a3b-code` (New: Unsloth Precise Coding Parameters)
2. `qwen3-coder-next:q4_k_m-code` (Baseline: NEWLY TUNED to Unsloth Qwen3-Coder parameters)

**Date**: 2026-03-14 12:53:00  
**Prompt**: "Write a highly optimized Python function to solve the N-Queens problem. Include detailed type hints, docstrings, and comments explaining the backtracking logic."  
**API Parameters Forced**: None (Strictly using compiled Modelfile defaults)

## Objective

Evaluate the impact of the newly applied Unsloth parameter tuning (Temp 1.0, Top_K 40, Repeat_Penalty 1.0) on the `qwen3-coder-next` model. We want to measure if the official tuning resolves the "endless generation/verbosity" bug we observed in the previous run, and do a final comparison against the `qwen3.5:35b-a3b-code` model.

## Results Summary

| Metric | `qwen3.5:35b-a3b-code` | `qwen3-coder-next:q4_k_m-code` | Delta |
|--------|------------------------|--------------------------------|-------|
| **Throughput (Tokens/s)** | **45.22 tok/s** | 37.12 tok/s | **+21.8%** (Faster) |
| **Total Generation Time** | **41.00s** | 50.72s | **-19.1%** (Faster) |
| **TTFT (Time to First Token)** | 3.09s | **0.27s** | +2.82s (Slower) |
| **Total Tokens Emitted** | **1825** | 1862 | Parity (Resolved Bug) |

## Detailed Findings

1. **Successful Tuning (Verbosity Bug Fixed)**: 
   In the previous untuned benchmark, the older Qwen3-Coder model hallucinated over **3,100 tokens** and took nearly 1.5 minutes to finish. After applying the official Unsloth tuning (`repeat_penalty: 1.0`, `temperature: 1.0`), the model successfully capped itself at **1,862 tokens** and finished in just 50 seconds. This proves that the old "generic" parameters were artificially suppressing the model's ability to stop generating code naturally.

2. **Throughput Stays Stable**: 
   Even perfectly tuned, the older `qwen3-coder-next:q4_k_m` architecture stays capped at roughly **~37 tokens per second**. The newer `qwen3.5:35b-a3b` architecture consistently punches ~20% higher throughput, hitting **~45 tokens per second** on the exact same hardware and payload. 

3. **TTFT Reality**: 
   The TTFT gap remains unchanged. The older 30B model with a 64K context window starts generating code in **0.27s**. The newer 35B model with a massive 128K context window takes **~3 seconds** to allocate memory before typing.

## Recommendations
- **Final Verdict:** The tuning on `qwen3-coder-next` was a massive success, cutting its response time by 40% simply by fixing the parameters.
- **Role Assignment:** `qwen3.5:35b-a3b-code` remains the objectively better model for the `coding` role due to its raw throughput (+20% tokens/s) and more advanced MoE architecture, despite the slightly longer context-window load time.
