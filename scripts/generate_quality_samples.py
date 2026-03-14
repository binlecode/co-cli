import httpx
import json

prompt = "Write a highly optimized Python function to solve the N-Queens problem. Include detailed type hints, docstrings, and comments explaining the backtracking logic."
models = ["qwen3.5:35b-a3b-code", "qwen3-coder-next:q4_k_m-code"]

for m in models:
    print(f"Generating sample for {m}...")
    try:
        resp = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": m, "prompt": prompt, "stream": False},
            timeout=120.0
        )
        resp.raise_for_status()
        filename = f"evals/sample_{m.replace(':', '_')}.md"
        with open(filename, "w") as f:
            f.write(resp.json()["response"])
        print(f"Saved to {filename}")
    except Exception as e:
        print(f"Failed for {m}: {e}")
