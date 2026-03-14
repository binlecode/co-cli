# REPORT: benchmark-qwen35-ctx-sizes

**Model tested**: `qwen3.5:35b-a3b-code`
**Context Sizes**: 64k (65536) vs 128k (131072)
**Date**: 2026-03-14 15:02:10

## Objective

Evaluate the memory efficiency and generation performance (TTFT, throughput) of the `qwen3.5:35b-a3b-code` model under different context window sizes (64k vs 128k). Given the constraint of sharing 128GB unified RAM between a coder, a thinker, and a non-thinker (instruct) model, we must find the optimal context window tuning to maximize performance while preventing out-of-memory overhead during KV cache allocation.

## 1. Quantitative Performance (Hardware Inference)

### Results Summary

| Context Size | VRAM Usage (GB) | Task | Throughput (tok/s) | Total Time (s) | TTFT (s) | Tokens |
|--------------|-----------------|------|--------------------|----------------|----------|--------|
| 64k | 25.48 GB | Python_Algo | 45.67 | 43.44 | 2.86 | 1953 |
| 64k | 25.48 GB | React_UI | 45.69 | 68.34 | 7.15 | 3079 |
| 64k | 25.48 GB | Rust_Systems | 45.85 | 45.57 | 6.30 | 2057 |
| 128k | 26.89 GB | Python_Algo | 45.37 | 49.40 | 4.89 | 2207 |
| 128k | 26.89 GB | React_UI | 44.49 | 65.72 | 14.91 | 2885 |
| 128k | 26.89 GB | Rust_Systems | 45.43 | 64.81 | 3.27 | 2903 |

## 2. Qualitative Findings

1. **Generation Quality**: Both the 64k and 128k context versions produced highly optimized, idiomatic, and robust code across all three languages (Python, React/TypeScript, Rust). 
    *   **Python (N-Queens)**: Both used bitwise manipulation and sets effectively.
    *   **React (Data Table)**: Both correctly identified the need for a virtualization library. The 64k version used `@tanstack/react-virtual` (modern standard), while the 128k version used `react-window`.
    *   **Rust (Connection Pool)**: Both correctly implemented thread safety using `Arc` and `Mutex`. Interestingly, the 64k version proactively implemented an async-first pool using `tokio::sync::Mutex` (which is generally preferred for I/O bounds in modern Rust), whereas the 128k version stuck to standard library synchronous primitives (`std::sync::Mutex`).

2. **Verbosity and Hallucination**: Neither context size exhibited the "endless generation" or verbosity bug seen in untuned models. Both models stopped cleanly after completing the task (ranging from 1900 to 3000 tokens depending on the task's complexity).

3. **Memory Pressure Analysis**: 
    *   **64k Context**: Consumes **25.48 GB** of VRAM.
    *   **128k Context**: Consumes **26.89 GB** of VRAM.
    *   *Finding*: The memory delta between 64k and 128k context windows for this MoE architecture is extremely minimal (**~1.4 GB**). This indicates that Ollama/llama.cpp handles the KV cache for this model very efficiently, likely due to Grouped Query Attention (GQA) and the Sparse Mixture-of-Experts (MoE) architecture.

## Final Recommendations

- **Use the 128k Context Window (`num_ctx: 131072`) as the default for the Coder agent**. 
- The penalty for doubling the context window from 64k to 128k is practically non-existent:
  - **VRAM Cost**: Only a ~1.4 GB increase in memory pressure. On a 128GB unified memory system, 26.89 GB leaves over 100 GB for the OS, Thinker, and Instruct models to share comfortably.
  - **Throughput Cost**: No measurable drop in token generation speed (both average ~45 tokens/sec).
  - **Quality Cost**: No degradation in code quality or adherence to instructions.
- The 128k context will allow the Coder agent to ingest significantly larger codebase chunks, PR diffs, and API documentation without hitting context limits, making it substantially more capable for repository-scale tasks.
