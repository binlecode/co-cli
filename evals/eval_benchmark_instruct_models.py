#!/usr/bin/env python3
"""Benchmark to compare Qwen 30b instruct vs Qwen 3.5 35b instruct models."""

import argparse
from evals._timeouts import EVAL_PROBE_TIMEOUT_SECS, EVAL_BENCHMARK_TIMEOUT_SECS

import json
import sys
import time
import logging
import datetime
import os

try:
    import httpx
except ImportError:
    print(
        "Error: httpx is required. Run 'pip install httpx' or use 'uv run python'.",
        file=sys.stderr,
    )
    sys.exit(1)

# Community best practice set of instruct tasks (non-coding, general reasoning/writing)
TASKS = {
    "Email_Draft": "Draft a professional but firm email to a vendor explaining that their recent software update broke our production environment. Request an immediate rollback plan and an RCA (Root Cause Analysis).",
    "Logic_Puzzle": "A farmer has a wolf, a goat, and a cabbage. He needs to cross a river in a boat that can only hold himself and one other item. If left alone, the wolf will eat the goat, and the goat will eat the cabbage. How can he get everything across safely? Provide just the step-by-step solution without extra fluff.",
    "Summarization": "Explain the core differences between optimistic concurrency control and pessimistic concurrency control in database systems. Use a simple analogy to a real-world scenario to make it easy to understand for a junior developer.",
}

logger = logging.getLogger("benchmark")


def run_benchmark(host: str, model: str, task_name: str, prompt: str) -> dict | None:
    logger.info(f"Evaluating {model} on {task_name}...")
    url = f"{host.rstrip('/')}/api/generate"

    # Default ctx size 131072
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "5m",
        "options": {"num_ctx": 131072},
    }

    start_time = time.time()
    ttft = None

    try:
        with httpx.stream("POST", url, json=payload, timeout=EVAL_BENCHMARK_TIMEOUT_SECS) as response:
            response.raise_for_status()

            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)

                if ttft is None and "response" in data and data["response"]:
                    ttft = time.time() - start_time
                    logger.debug(f"[{model}] First token at {ttft:.4f}s")

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
                        f"[{model} | {task_name}] Finished in {total_time:.2f}s | {tps:.2f} tok/s"
                    )

                    return {
                        "model": model,
                        "task": task_name,
                        "ttft": ttft or 0.0,
                        "tps": tps,
                        "total_time": total_time,
                        "eval_count": eval_count,
                    }
    except Exception as e:
        logger.error(f"Error benchmarking {model}: {e}", exc_info=True)
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
                vram_bytes = m.get("size_vram", 0)
                return vram_bytes / (1024**3)
    except Exception as e:
        logger.warning(f"Failed to get VRAM usage: {e}")
    return 0.0


def main():
    parser = argparse.ArgumentParser(description="Benchmark Ollama instruct models.")
    parser.add_argument(
        "--host", default="http://localhost:11434", help="Ollama host URL"
    )
    parser.add_argument(
        "--model1", default="qwen3:30b-a3b-instruct-2507", help="First model to test"
    )
    parser.add_argument(
        "--model2", default="qwen3.5:35b-a3b-instruct", help="Second model to test"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug tracelogging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        httpx.get(f"{args.host.rstrip('/')}/api/tags", timeout=EVAL_PROBE_TIMEOUT_SECS).raise_for_status()
    except Exception as e:
        print(f"SKIP: Ollama unavailable at {args.host} - {e}")
        sys.exit(0)

    models = [args.model1, args.model2]
    results = []

    for model in models:
        logger.info(f"\n--- Starting suite for {model} ---")
        logger.info(f"Warming up {model} (loading to VRAM)...")
        try:
            resp = httpx.post(
                f"{args.host.rstrip('/')}/api/generate",
                json={
                    "model": model,
                    "prompt": "Warmup",
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {"num_ctx": 131072},
                },
                timeout=EVAL_BENCHMARK_TIMEOUT_SECS,
            )
            resp.raise_for_status()
            logger.info("Warmup successful. Memory fully loaded.")
        except Exception as e:
            logger.error(f"Warmup failed for {model}. Error: {e}")
            sys.exit(1)

        vram_gb = get_vram_usage(args.host, model)
        logger.info(f"Memory pressure: {vram_gb:.2f} GB for {model}")

        for task_name, prompt in TASKS.items():
            res = run_benchmark(args.host, model, task_name, prompt)
            if res:
                res["vram_gb"] = vram_gb
                results.append(res)

    report_path = "docs/REPORT-benchmark-instruct-30b-vs-35b.md"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_content = f"# REPORT: benchmark-instruct-30b-vs-35b\n\n"
    report_content += f"**Models tested**:\n"
    report_content += f"1. `{args.model1}` (Baseline - Qwen3 30B Instruct)\n"
    report_content += f"2. `{args.model2}` (New - Qwen3.5 35B Instruct)\n"
    report_content += f"**Date**: {date_str}\n\n"
    report_content += f"## Objective\n\n"
    report_content += "Evaluate the generation performance (Throughput, TTFT, Token Efficiency) and memory footprint between the previous Qwen3 30B Instruct model and the newly released Qwen3.5 35B Instruct model. Both models are configured using their respective Unsloth officially recommended parameter tunings for `no-think/instruct` workloads (`temperature: 0.7`, `top_p: 0.8`, `top_k: 20`, `presence_penalty: 1.5`, `repeat_penalty: 1.0`, `num_ctx: 131072`). This report establishes the baseline for upgrading the agent's primary 'non-thinker' routing and instruction model.\n\n"
    report_content += f"## 1. Quantitative Performance (Hardware Inference)\n\n"
    report_content += f"### Results Summary\n\n"
    report_content += f"| Model | VRAM Usage (GB) | Task | Throughput (tok/s) | Total Time (s) | TTFT (s) | Tokens |\n"
    report_content += f"|-------|-----------------|------|--------------------|----------------|----------|--------|\n"

    for r in results:
        # short name for table
        short_name = "30B" if "30b" in r["model"] else "35B"
        report_content += f"| {short_name} | {r.get('vram_gb', 0):.2f} GB | {r['task']} | {r['tps']:.2f} | {r['total_time']:.2f} | {r['ttft']:.2f} | {r['eval_count']} |\n"

    report_content += (
        f"\n## 2. Qualitative Findings\n\n(To be analyzed based on evals)\n\n"
    )
    report_content += f"\n## Final Recommendations\n\n(To be analyzed based on evals)\n"

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_content)

    logger.info(f"Report skeleton generated at {report_path}")


if __name__ == "__main__":
    main()
