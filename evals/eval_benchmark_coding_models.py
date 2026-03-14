#!/usr/bin/env python3
"""Benchmark custom Ollama coding models."""

import argparse
import json
import sys
import time
import logging

try:
    import httpx
except ImportError:
    print("Error: httpx is required. Run 'pip install httpx' or use 'uv run python'.", file=sys.stderr)
    sys.exit(1)

DEFAULT_PROMPT = (
    "Write a highly optimized Python function to solve the N-Queens problem. "
    "Include detailed type hints, docstrings, and comments explaining the backtracking logic."
)

logger = logging.getLogger("benchmark")

def run_benchmark(host: str, model: str, prompt: str) -> dict | None:
    logger.info(f"Evaluating {model}...")
    url = f"{host.rstrip('/')}/api/generate"
    
    # Intentionally omitted "options" to ensure Ollama purely uses the
    # Modelfile parameters (num_predict, temperature, presence_penalty, num_ctx, etc.)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True
    }
    
    logger.debug(f"POST {url} with payload: {json.dumps(payload)}")
    start_time = time.time()
    ttft = None
    
    try:
        with httpx.stream("POST", url, json=payload, timeout=300.0) as response:
            response.raise_for_status()
            logger.debug(f"Connection established. Status code: {response.status_code}")
            
            chunk_count = 0
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                chunk_count += 1
                
                if ttft is None and "response" in data and data["response"]:
                    ttft = time.time() - start_time
                    logger.debug(f"[{model}] First token received at {ttft:.4f}s: {data['response']!r}")
                    
                if chunk_count % 100 == 0:
                    logger.debug(f"[{model}] Received {chunk_count} chunks so far...")
                    
                if data.get("done"):
                    total_time = time.time() - start_time
                    eval_count = data.get("eval_count", 0)
                    eval_duration_ns = data.get("eval_duration", 0)
                    
                    logger.debug(f"[{model}] Generation done. Final payload eval metric: {eval_duration_ns}ns")
                    
                    tps = 0.0
                    if eval_duration_ns > 0:
                        tps = eval_count / (eval_duration_ns / 1e9)
                    elif total_time > ttft:
                        tps = eval_count / (total_time - ttft)
                        
                    logger.info(f"[{model}] Finished in {total_time:.2f}s | {tps:.2f} tok/s")
                    
                    return {
                        "model": model,
                        "ttft": ttft or 0.0,
                        "tps": tps,
                        "total_time": total_time,
                        "eval_count": eval_count
                    }
    except Exception as e:
        logger.error(f"Error benchmarking {model}: {e}", exc_info=True)
        return None

def main():
    parser = argparse.ArgumentParser(description="Benchmark Ollama coding models.")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument("--model1", default="qwen3.5:35b-a3b-code", help="First model to test")
    parser.add_argument("--model2", default="qwen3-coder-next:code", help="Second model to test")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="The prompt to test with")
    parser.add_argument("--debug", action="store_true", help="Enable debug tracelogging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S"
    )

    models = [args.model1, args.model2]
    results = []

    logger.info(f"Starting benchmark against {args.host}")
    logger.info(f"Prompt: {args.prompt[:80]}...")

    # Iterate over models: Warm up -> Benchmark
    # This ensures Model A doesn't get evicted from VRAM by Model B's warmup
    for m in models:
        logger.info(f"Warming up {m} (loading to VRAM)...")
        logger.debug(f"Sending warmup request for {m}")
        try:
            resp = httpx.post(
                f"{args.host.rstrip('/')}/api/generate", 
                json={"model": m, "prompt": "Warmup", "stream": False}, 
                timeout=300.0
            )
            resp.raise_for_status()
            logger.debug(f"Warmup successful for {m}: {resp.status_code}")
        except Exception as e:
            logger.error(f"Warmup failed for {m}. Is the model downloaded? Error: {e}")
            sys.exit(1)

        # Immediate Benchmark phase for the WARM model
        res = run_benchmark(args.host, m, args.prompt)
        if res:
            results.append(res)

    if not results:
        logger.error("No successful benchmarks completed.")
        sys.exit(1)

    print("\n" + "=" * 80)
    print(f"{'Model':<35} | {'TTFT (s)':<10} | {'Tokens/s':<10} | {'Total Time (s)':<15} | {'Tokens':<8}")
    print("-" * 80)
    for r in results:
        print(f"{r['model']:<35} | {r['ttft']:<10.2f} | {r['tps']:<10.2f} | {r['total_time']:<15.2f} | {r['eval_count']}")
    print("=" * 80)

if __name__ == "__main__":
    main()
