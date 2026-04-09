#!/usr/bin/env python3
"""Benchmark custom Ollama coding models across context sizes."""

import argparse
import datetime
import json
import logging
import os
import sys
import time

from evals._timeouts import EVAL_BENCHMARK_TIMEOUT_SECS, EVAL_PROBE_TIMEOUT_SECS

try:
    import httpx
except ImportError:
    print(
        "Error: httpx is required. Run 'pip install httpx' or use 'uv run python'.",
        file=sys.stderr,
    )
    sys.exit(1)

# Community best practice set of coding tasks
TASKS = {
    "Python_Algo": "Write a highly optimized Python function to solve the N-Queens problem. Include detailed type hints, docstrings, and comments explaining the backtracking logic.",
    "React_UI": "Create a functional React component for a sortable and filterable data table using React Hooks and Tailwind CSS. It should handle thousands of rows efficiently using virtualization.",
    "Rust_Systems": "Write a thread-safe connection pool in Rust using Arc and Mutex. It should support timeouts, graceful shutdown, and handle connection errors. Include detailed comments.",
}

logger = logging.getLogger("benchmark")


def run_benchmark(
    host: str, model: str, ctx_size: int, task_name: str, prompt: str
) -> dict | None:
    logger.info(f"Evaluating {model} (ctx={ctx_size}) on {task_name}...")
    url = f"{host.rstrip('/')}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "5m",
        "options": {"num_ctx": ctx_size},
    }

    start_time = time.time()
    ttft = None

    try:
        with httpx.stream(
            "POST", url, json=payload, timeout=EVAL_BENCHMARK_TIMEOUT_SECS
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)

                if ttft is None and "response" in data and data["response"]:
                    ttft = time.time() - start_time
                    logger.debug(f"[{model} ctx={ctx_size}] First token at {ttft:.4f}s")

                if data.get("done"):
                    total_time = time.time() - start_time
                    eval_count = data.get("eval_count", 0)
                    eval_duration_ns = data.get("eval_duration", 0)

                    tps = 0.0
                    if eval_duration_ns > 0:
                        tps = eval_count / (eval_duration_ns / 1e9)
                    elif ttft is not None and total_time > ttft:
                        tps = eval_count / (total_time - ttft)

                    logger.info(
                        f"[{model} ctx={ctx_size} | {task_name}] Finished in {total_time:.2f}s | {tps:.2f} tok/s"
                    )

                    return {
                        "model": model,
                        "ctx_size": ctx_size,
                        "task": task_name,
                        "ttft": ttft or 0.0,
                        "tps": tps,
                        "total_time": total_time,
                        "eval_count": eval_count,
                    }
    except Exception as e:
        logger.error(f"Error benchmarking {model} ctx={ctx_size}: {e}", exc_info=True)
        return None


def get_vram_usage(host: str, model: str) -> float:
    """Returns the VRAM usage in GB for the specified model."""
    try:
        url = f"{host.rstrip('/')}/api/ps"
        resp = httpx.get(url, timeout=EVAL_PROBE_TIMEOUT_SECS)
        resp.raise_for_status()
        data = resp.json()
        for m in data.get("models", []):
            if m.get("model") == model or m.get("name") == model:
                # size_vram is in bytes
                vram_bytes = m.get("size_vram", 0)
                return vram_bytes / (1024**3)
    except Exception as e:
        logger.warning(f"Failed to get VRAM usage: {e}")
    return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama coding models with different ctx sizes."
    )
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument("--model", default="qwen3.5:35b-a3b-code", help="Model to test")
    parser.add_argument("--debug", action="store_true", help="Enable debug tracelogging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        httpx.get(
            f"{args.host.rstrip('/')}/api/tags", timeout=EVAL_PROBE_TIMEOUT_SECS
        ).raise_for_status()
    except Exception as e:
        print(f"SKIP: Ollama unavailable at {args.host} - {e}")
        sys.exit(0)

    ctx_sizes = [65536, 131072]  # 64k vs 128k
    results = []

    model = args.model

    for ctx_size in ctx_sizes:
        logger.info(f"\n--- Starting suite for ctx={ctx_size} ---")
        logger.info(f"Warming up {model} with ctx={ctx_size} (loading to VRAM)...")
        try:
            resp = httpx.post(
                f"{args.host.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": "Warmup",
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {"num_ctx": ctx_size},
                },
                timeout=EVAL_BENCHMARK_TIMEOUT_SECS,
            )
            resp.raise_for_status()
            logger.info("Warmup successful. Memory fully loaded.")
        except Exception as e:
            logger.error(f"Warmup failed for {model} ctx={ctx_size}. Error: {e}")
            sys.exit(1)

        vram_gb = get_vram_usage(args.host, model)
        logger.info(f"Memory pressure: {vram_gb:.2f} GB for ctx={ctx_size}")

        for task_name, prompt in TASKS.items():
            res = run_benchmark(args.host, model, ctx_size, task_name, prompt)
            if res:
                res["vram_gb"] = vram_gb
                results.append(res)

    # Generate partial Report (to be fleshed out manually after analysis)
    report_path = "docs/REPORT-benchmark-qwen35-ctx-sizes.md"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_content = "# REPORT: benchmark-qwen35-ctx-sizes\n\n"
    report_content += f"**Model tested**: `{model}`\n"
    report_content += "**Context Sizes**: 64k (65536) vs 128k (131072)\n"
    report_content += f"**Date**: {date_str}\n\n"
    report_content += "## Objective\n\n"
    report_content += "Evaluate the memory efficiency and generation performance (TTFT, throughput) of the `qwen3.5:35b-a3b-code` model under different context window sizes (64k vs 128k). Given the constraint of sharing 128GB unified RAM between a coder, a thinker, and a non-thinker (instruct) model, we must find the optimal context window tuning to maximize performance while preventing out-of-memory overhead during KV cache allocation.\n\n"
    report_content += "## 1. Quantitative Performance (Hardware Inference)\n\n"
    report_content += "### Results Summary\n\n"
    report_content += "| Context Size | VRAM Usage (GB) | Task | Throughput (tok/s) | Total Time (s) | TTFT (s) | Tokens |\n"
    report_content += "|--------------|-----------------|------|--------------------|----------------|----------|--------|\n"

    for r in results:
        ctx_label = "64k" if r["ctx_size"] == 65536 else "128k"
        report_content += f"| {ctx_label} | {r.get('vram_gb', 0):.2f} GB | {r['task']} | {r['tps']:.2f} | {r['total_time']:.2f} | {r['ttft']:.2f} | {r['eval_count']} |\n"

    report_content += "\n## 2. Qualitative Findings\n\n(To be analyzed based on evals)\n\n"
    report_content += "\n## Final Recommendations\n\n(To be analyzed based on evals)\n"

    # Make docs dir if not exists
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_content)

    logger.info(f"Report skeleton generated at {report_path}")


if __name__ == "__main__":
    main()
