import os
from openai import OpenAI
import time

def test_ollama():
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    model = "qwen3.5:35b-a3b-think"
    prompt = "Explain transformers in 2 sentences. Take your time to think step by step."
    
    start = time.time()
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}],
        extra_body={"reasoning_effort": "none"}
    )
    print(f"Time: {time.time()-start:.2f}s")
    print(f"Reasoning present: {hasattr(resp.choices[0].message, 'reasoning') and resp.choices[0].message.reasoning is not None}")
    print("Content:")
    print(resp.choices[0].message.content)

test_ollama()
