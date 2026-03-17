import os
import time
from openai import OpenAI
import concurrent.futures

def test_diverse_cases():
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama", # Required but arbitrary
    )
    model = "qwen3.5:35b-a3b-think"
    
    # Diverse prompts designed to heavily trigger reasoning
    prompts = {
        "Math Word Problem": "If a train leaves New York at 60mph and another leaves Boston at 80mph, how long until they meet? Explain the math.",
        "Logic Puzzle": "A man is looking at a picture. Someone asks whose picture it is. He replies: 'Brothers and sisters, I have none. But that man's father is my father's son.' Who is in the picture?",
        "Explicit Instruction": "Solve the following logic puzzle. You MUST think step by step and output your thought process before answering. What weighs more, a pound of feathers or a pound of bricks?",
        "Coding with Constraints": "Write a Python function to compute the Fibonacci sequence, but do not use any loops or standard recursion. Explain how you did it.",
        "Summarization (Actual Use Case)": "Summarize the following text in exactly 3 bullet points: 'The history of artificial intelligence began in antiquity, with myths, stories and rumors of artificial beings endowed with intelligence or consciousness by master craftsmen. The seeds of modern AI were planted by classical philosophers who attempted to describe the process of human thinking as the mechanical manipulation of symbols. This work culminated in the invention of the programmable digital computer in the 1940s...'"
    }

    print(f"Running diverse confidence tests on {model}...\n")

    for name, prompt in prompts.items():
        print(f"=== Case: {name} ===")
        
        # Test 1: Control (Default)
        start = time.time()
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=True
            )
            reasoning_detected = False
            for chunk in stream:
                delta = chunk.choices[0].delta
                if getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None) or \
                   (delta.content and ("<think>" in delta.content or "Thinking Process" in delta.content)):
                    reasoning_detected = True
                    break
            stream.close()
            duration = time.time() - start
            print(f"  [Control] Reasoning detected: {'YES' if reasoning_detected else 'NO'} (Time to detect: {duration:.2f}s)")
        except Exception as e:
            print(f"  [Control] Error: {e}")

        # Test 2: reasoning_effort="none"
        start = time.time()
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                extra_body={"reasoning_effort": "none"}
            )
            reasoning_detected = False
            full_content = ""
            for chunk in stream:
                delta = chunk.choices[0].delta
                if getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None) or \
                   (delta.content and ("<think>" in delta.content or "Thinking Process" in delta.content)):
                    reasoning_detected = True
                    break
                if delta.content:
                    full_content += delta.content
            stream.close()
            duration = time.time() - start
            
            if reasoning_detected:
                print(f"  [Test]    Reasoning disabled? ❌ NO (Leaked reasoning! Aborted at {duration:.2f}s)")
            else:
                print(f"  [Test]    Reasoning disabled? ✅ YES (Completed full stream in {duration:.2f}s, Content Length: {len(full_content)} chars)")
                
        except Exception as e:
            print(f"  [Test]    Error: {e}")
        print()

if __name__ == "__main__":
    test_diverse_cases()
