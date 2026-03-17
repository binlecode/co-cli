import os
from openai import OpenAI
import time


def test_ollama_openai_disable_thinking():
    # Configure client to use local Ollama OpenAI-compatible endpoint
    client = OpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",  # Required but arbitrary
    )

    model = "qwen3.5:35b-a3b-think"
    prompt = "I have 3 apples, I eat one, and I buy 5 more, then I give half of what I have to my friend. Explain step by step."

    print(f"Testing {model} via OpenAI API compatibility layer...")

    # --- Test 1: With reasoning (Default) ---
    print("\n[Test 1] Default behavior (Reasoning ENABLED)")
    start_time = time.time()
    try:
        response_default = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}]
        )
        content_default = response_default.choices[0].message.content
        duration = time.time() - start_time
        print(f"Time taken: {duration:.2f}s")
        print(f"Response:\n{response_default}")
        if content_default and "<think>" in content_default:
            print("-> Successfully detected <think> tags in default response.")
        else:
            print(
                "-> No <think> tags detected. Model might not have reasoned, or tag format changed."
            )
    except Exception as e:
        print(f"Error in Test 1: {e}")

    # --- Test 2: Disable reasoning using reasoning_effort="none" ---
    # Unsloth/Ollama PR 14821 documented that reasoning_effort="none" disables thinking.
    print("\n[Test 2] Disabling reasoning (reasoning_effort='none' via extra_body)")
    start_time = time.time()
    try:
        response_disabled = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            # Test documented reasoning_effort="none" or reasoning.effort="none"
            extra_body={"reasoning_effort": "none"},
        )
        content_disabled = response_disabled.choices[0].message.content
        duration = time.time() - start_time
        print(f"Time taken: {duration:.2f}s")
        print(f"Response:\n{response_disabled}")
        if content_disabled and "<think>" in content_disabled:
            print("-> ERROR: <think> tags still present! Reasoning was NOT disabled.")
        else:
            print(
                "-> SUCCESS: No <think> tags detected. Reasoning was successfully disabled."
            )
    except Exception as e:
        print(f"Error in Test 2: {e}")

    # --- Test 3: Disable reasoning using reasoning={"effort": "none"} ---
    print(
        "\n[Test 3] Disabling reasoning (reasoning={'effort': 'none'} via extra_body)"
    )
    start_time = time.time()
    try:
        response_disabled_3 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"reasoning": {"effort": "none"}},
        )
        content_disabled_3 = response_disabled_3.choices[0].message.content
        duration = time.time() - start_time
        print(f"Time taken: {duration:.2f}s")
        print(f"Response:\n{response_disabled_3}")
        if content_disabled_3 and "<think>" in content_disabled_3:
            print("-> ERROR: <think> tags still present! Reasoning was NOT disabled.")
        else:
            print(
                "-> SUCCESS: No <think> tags detected. Reasoning was successfully disabled."
            )
    except Exception as e:
        print(f"Error in Test 3: {e}")

    # --- Test 4: Disable reasoning using think=False (old modelfile parameter) ---
    print("\n[Test 4] Disabling reasoning (think=False via extra_body)")
    start_time = time.time()
    try:
        response_disabled_4 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"think": False},
        )
        content_disabled_4 = response_disabled_4.choices[0].message.content
        duration = time.time() - start_time
        print(f"Time taken: {duration:.2f}s")
        print(f"Response:\n{response_disabled_4}")
        if content_disabled_4 and "<think>" in content_disabled_4:
            print("-> ERROR: <think> tags still present! Reasoning was NOT disabled.")
        else:
            print(
                "-> SUCCESS: No <think> tags detected. Reasoning was successfully disabled."
            )
    except Exception as e:
        print(f"Error in Test 4: {e}")


if __name__ == "__main__":
    test_ollama_openai_disable_thinking()
