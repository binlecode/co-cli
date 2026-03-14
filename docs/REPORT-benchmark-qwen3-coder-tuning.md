# REPORT: benchmark-qwen3-coder-tuning

**Models tested**: 
1. `qwen3.5:35b-a3b-code` (New Default: Unsloth Precise Coding Parameters applied)
2. `qwen3-coder-next:q4_k_m-code` (Baseline: NEWLY TUNED to Unsloth Qwen3-Coder parameters)

**Date**: 2026-03-14 12:53:00  
**Prompt**: "Write a highly optimized Python function to solve the N-Queens problem. Include detailed type hints, docstrings, and comments explaining the backtracking logic."  
**API Parameters Forced**: None (Strictly using compiled Modelfile defaults)

## Objective

Evaluate the impact of the newly applied Unsloth parameter tuning (`temperature: 1.0`, `top_k: 40`, `repeat_penalty: 1.0`) on the baseline `qwen3-coder-next` model. We want to measure if the official tuning resolves the "endless generation/verbosity" bug observed in previous runs, and do a final comprehensive comparison (Quantitative hardware speed + Qualitative code generation) against the newly introduced `qwen3.5:35b-a3b-code` model.

---

## 1. Quantitative Performance (Hardware Inference)

### Results Summary

| Metric | `qwen3.5:35b-a3b-code` | `qwen3-coder-next:q4_k_m-code` | Delta |
|--------|------------------------|--------------------------------|-------|
| **Throughput (Tokens/s)** | **45.22 tok/s** | 37.12 tok/s | **+21.8%** (Faster) |
| **Total Generation Time** | **41.00s** | 50.72s | **-19.1%** (Faster) |
| **TTFT (Time to First Token)** | 3.09s | **0.27s** | +2.82s (Slower) |
| **Total Tokens Emitted** | **1825** | 1862 | Parity (Resolved Bug) |

### Detailed Findings

1. **Successful Tuning (Verbosity Bug Fixed)**: 
   In previous untuned benchmarks, the older Qwen3-Coder model hallucinated over **3,100 tokens** and took nearly 1.5 minutes to finish. After applying the official Unsloth tuning (disabling `repeat_penalty` and increasing `temperature`), the model successfully capped itself at **1,862 tokens** and finished in just 50 seconds. This proves that the old "generic" parameters were artificially suppressing the model's ability to stop generating code naturally.

2. **Throughput Stays Stable**: 
   Even perfectly tuned, the older `qwen3-coder-next:q4_k_m` architecture stays capped at roughly **~37 tokens per second**. The newer `qwen3.5:35b-a3b` architecture consistently punches ~20% higher throughput, hitting **~45 tokens per second** on the exact same hardware and payload. 

3. **TTFT Reality**: 
   The TTFT gap remains unchanged. The older 30B model with a 64K context window starts generating code in **0.27s**. The newer 35B model with a massive 128K context window takes **~3 seconds** to allocate memory before typing.

---

## 2. Qualitative Performance (Code Generation Quality)
*(Extracted from generated sample files `evals/sample_qwen3.5_35b-a3b-code.md` and `evals/sample_qwen3-coder-next_q4_k_m-code.md` using the exact same N-Queens prompt)*

### `qwen3.5:35b-a3b-code` (The New Default)
- **Algorithmic Sophistication**: Exceeded expectations by providing a complete module with four distinct implementations scaling in optimization:
  1. Standard set-based backtracking.
  2. Hyper-optimized bitwise version. It correctly utilized true bitwise shifting to avoid index calculations (`available = ((1 << n) - 1) & ~(cols | diag1 | diag2)`).
  3. Memory-efficient `yield`-based generator.
  4. Counter-only version.
- **Architectural Efficiency**: Kept the board state as a flat 1D integer array during deep recursion (e.g. `[1, 3, 0, 2]`), only converting it to formatted strings afterward via a separate `print_board` helper function. This prevents string concatenation overhead during the backtracking phase.
- **Documentation**: Production-grade module docstrings, Google-style function docstrings, `Raises:` blocks with error handling bounds (`n > 15`), and `Example:` doctests.

### `qwen3-coder-next:q4_k_m-code` (The Older Baseline)
- **Algorithmic Flaw**: Provided a standard and bitmask version. However, in its bitmask version, it calculated the diagonal integer index (`d1 = row - col_idx + n - 1`), bit-shifted that specific index, and checked it against a mask. This defeats the primary CPU speed advantage of bitwise N-Queens (which relies on shifting aggregate masks per row without index calculations).
- **Architectural Inefficiency**: Formatted the board as a list of strings (`['.Q..', '...Q']`) directly inside the recursion loop, creating significant string concatenation overhead during the backtracking phase.
- **Documentation**: Good docstrings, but slightly messier inline comments.

---

## Final Recommendations

- **The tuning on `qwen3-coder-next` was a massive success**, cutting its response time by 40% by fixing the parameters, stopping its hallucination loop.
- **Deprecate `qwen3-coder-next:q4_k_m-code`**. Despite the successful tuning, `qwen3.5:35b-a3b-code` is the objectively superior model for the `coding` role. 
- The new default (`qwen3.5`) provides a massive **+20% tokens/s throughput advantage**, understands advanced Python idioms (Generators), correctly implements algorithmic mathematics (true bitwise shifting), separates data representation from view representation for speed, and writes production-grade documentation. The ~3s context-window load time (TTFT) is a worthy trade-off.
