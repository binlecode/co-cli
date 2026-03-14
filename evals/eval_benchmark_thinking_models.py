#!/usr/bin/env python3
"""Benchmark to compare Qwen 30b think vs Qwen 3.5 35b think models."""

import argparse
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

# Community best practice set of complex thinking tasks
TASKS = {
    "Architecture_Design": "Design a highly available and scalable notification system for a social media application with 10M active daily users. Describe the component architecture, data flow, queueing mechanisms, and how to handle retry logic and rate limits. Output a structured markdown design document.",
    "Root_Cause_Analysis": "We have a production web application using a Node.js backend and a PostgreSQL database. During peak hours, the application experiences random 504 Gateway Timeout errors. The CPU on the Node instances is fine, memory is stable, and the database CPU is under 40%. The issue vanishes instantly when we restart the Node.js instances. What are the top 3 most likely root causes? Walk through your diagnostic reasoning for each.",
    "System_Prompt": "You are tasked with writing a complex system prompt for an autonomous AI agent that specializes in refactoring legacy Python code into clean, modern architectures. The prompt must instruct the agent to follow a specific methodology: 1) Read and analyze the file, 2) Write unit tests if missing, 3) Perform the refactoring, 4) Verify tests pass. Include strict rules about not deleting comments and handling unknown dependencies. Draft the full, highly detailed system prompt.",
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
        with httpx.stream("POST", url, json=payload, timeout=300.0) as response:
            response.raise_for_status()

            chunk_count = 0
            full_response = []
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                chunk_count += 1

                if "response" in data:
                    full_response.append(data["response"])

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
                        "response": "".join(full_response),
                    }
    except Exception as e:
        logger.error(f"Error benchmarking {model}: {e}", exc_info=True)
        return None


def get_vram_usage(host: str, model: str) -> float:
    """Returns the VRAM usage in GB for the specified model."""
    try:
        url = f"{host.rstrip('/')}/api/ps"
        resp = httpx.get(url, timeout=10.0)
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
    parser = argparse.ArgumentParser(description="Benchmark Ollama thinking models.")
    parser.add_argument(
        "--host", default="http://localhost:11434", help="Ollama host URL"
    )
    parser.add_argument(
        "--model1", default="qwen3:30b-a3b-think", help="First model to test"
    )
    parser.add_argument(
        "--model2", default="qwen3.5:35b-a3b-think", help="Second model to test"
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
                timeout=300.0,
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

    report_path = "docs/REPORT-benchmark-think-30b-vs-35b.md"
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_content = f"# REPORT: benchmark-think-30b-vs-35b\n\n"
    report_content += f"**Models tested**:\n"
    report_content += f"1. `{args.model1}` (Baseline - Qwen3 30B Thinker)\n"
    report_content += f"2. `{args.model2}` (New - Qwen3.5 35B Thinker)\n"
    report_content += f"**Date**: {date_str}\n\n"
    report_content += f"## Objective\n\n"
    report_content += "Evaluate the generation performance, reasoning quality, and memory footprint between the previous Qwen3 30B Thinker model and the newly released Qwen3.5 35B Thinker model on complex agentic planning and architectural tasks. Both models use the `131072` Context Window and follow Unsloth's official thinking-mode parameter tunings.\n\n"
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

    for r in results:
        short_name = "30b" if "30b" in r["model"] else "35b"
        sample_file = f"evals/sample_think_{short_name}_{r['task']}.md"
        with open(sample_file, "w") as f:
            f.write(f"# Prompt\n\n{TASKS[r['task']]}\n\n# Response\n\n{r['response']}")
        logger.info(f"Saved sample code to {sample_file}")

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_content)

    logger.info(f"Report skeleton generated at {report_path}")


if __name__ == "__main__":
    main()
