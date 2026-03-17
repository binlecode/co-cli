import os
import time
from openai import OpenAI

def test_disable_reasoning():
    """
    Test script to validate disabling reasoning on Ollama's OpenAI-compatible endpoint.
    This streams the response and fails fast if any reasoning is detected to save time.
    """
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama", # Required but arbitrary
    )
    
    model = "qwen3.5:35b-a3b-think"
    prompt = "Explain transformers in 2 sentences. Take your time to think step by step."
    
    tests = [
        ("Default (Should have reasoning)", {}),
        ("reasoning_effort='none'", {"reasoning_effort": "none"}),
        ("think=False", {"think": False}),
        ("enable_thinking=False", {"enable_thinking": False})
    ]

    print(f"Testing reasoning toggles on {model} via OpenAI API compatibility layer...\n")

    for test_name, extra_body in tests:
        print(f"--- Test: {test_name} ---")
        start = time.time()
        try:
            kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True
            }
            if extra_body:
                kwargs["extra_body"] = extra_body
                
            stream = client.chat.completions.create(**kwargs)
            
            reasoning_detected = False
            for chunk in stream:
                delta = chunk.choices[0].delta
                
                # Check for reasoning fields in OpenAI chunk
                if getattr(delta, 'reasoning_content', None):
                    reasoning_detected = True
                    break
                if getattr(delta, 'reasoning', None):
                    reasoning_detected = True
                    break
                    
                # Check if reasoning leaked into content
                if delta.content and ("<think>" in delta.content or "Thinking Process" in delta.content):
                    reasoning_detected = True
                    break
            
            stream.close()
            duration = time.time() - start
            
            if reasoning_detected:
                print(f"Result: ❌ FAIL (Reasoning DETECTED) - Aborted early at {duration:.2f}s")
            else:
                print(f"Result: ✅ SUCCESS (Reasoning DISABLED) - Completed in {duration:.2f}s")
                
        except Exception as e:
            print(f"Result: ⚠️ Error: {e}")
        print()

if __name__ == "__main__":
    test_disable_reasoning()
