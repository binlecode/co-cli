# REPORT: benchmark-instruct-30b-vs-35b

**Models tested**:
1. `qwen3:30b-a3b-instruct-2507` (Baseline - Qwen3 30B Instruct)
2. `qwen3.5:35b-a3b-instruct` (New - Qwen3.5 35B Instruct)
**Date**: 2026-03-14 17:43:49

## Objective

Evaluate the generation performance (Throughput, TTFT, Token Efficiency) and memory footprint between the previous Qwen3 30B Instruct model and the newly released Qwen3.5 35B Instruct model. Both models are configured using their respective Unsloth officially recommended parameter tunings for `no-think/instruct` workloads (`temperature: 0.7`, `top_p: 0.8`, `top_k: 20`, `presence_penalty: 1.5`, `repeat_penalty: 1.0`, `num_ctx: 131072`). This report establishes the baseline for upgrading the agent's primary 'non-thinker' routing and instruction model.

## 1. Quantitative Performance (Hardware Inference)

### Results Summary

| Model | VRAM Usage (GB) | Task | Throughput (tok/s) | Total Time (s) | TTFT (s) | Tokens |
|-------|-----------------|------|--------------------|----------------|----------|--------|
| 30B | 24.06 GB | Email_Draft | 93.91 | 4.95 | 0.17 | 444 |
| 30B | 24.06 GB | Logic_Puzzle | 94.08 | 1.30 | 0.19 | 103 |
| 30B | 24.06 GB | Summarization | 91.36 | 8.71 | 0.17 | 770 |
| 35B | 26.89 GB | Email_Draft | 44.75 | 27.03 | 22.27 | 1180 |
| 35B | 26.89 GB | Logic_Puzzle | 44.95 | 16.28 | 14.70 | 709 |
| 35B | 26.89 GB | Summarization | 45.08 | 36.23 | 28.97 | 1602 |

## 2. Qualitative Findings

(To be analyzed based on evals)


## Final Recommendations

(To be analyzed based on evals)
