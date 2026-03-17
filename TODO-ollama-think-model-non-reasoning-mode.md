# TODO: Remove `ollama-native` Provider and Use `ollama-openai` for Summarization

## Overview
Currently, the `co-cli` system maintains a separate `ollama-native` provider implementation. The primary reason for this was to access the top-level `think: false` parameter in the native Ollama `/api/chat` API, which allowed us to run the "think" model (e.g., `qwen3.5:35b-a3b-think`) for summarization without paying the penalty of generating reasoning tokens. This avoided a costly model-swap eviction (loading the `instruct` variant into VRAM).

After extensive testing, we have confirmed that **the OpenAI-compatible API (`/v1/chat/completions`) fully supports disabling reasoning** via the `reasoning_effort` parameter passed in the `extra_body`. 

Because of this, the `ollama-native` provider and its boilerplate are obsolete and can be safely removed.

## API Specification for Disabling Reasoning
When calling Ollama's OpenAI-compatible `/v1/chat/completions` endpoint, you must pass `reasoning_effort="none"` in the `extra_body` of the OpenAI client. 

*Note: Neither `think: False` nor Unsloth's `enable_thinking: False` work via the OpenAI API compatibility layer. It MUST be `reasoning_effort`.*

### Example Python OpenAI Client Usage
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama", 
)

response = client.chat.completions.create(
    model="qwen3.5:35b-a3b-think",
    messages=[{"role": "user", "content": "Summarize this text..."}],
    # This completely disables the reasoning/thinking phase
    extra_body={"reasoning_effort": "none"}
)
```

## Validation & Confidence
We ran rigorous validation testing against the `qwen3.5:35b-a3b-think` model using the `reasoning_effort="none"` parameter over the OpenAI compatibility layer. The tests included:
1. Math word problems
2. Logic puzzles
3. Prompt injection ("You MUST think step by step and output your thought process")
4. Coding with constraints
5. Summarization (our actual use case)

**Results:** In 100% of the test cases, the model bypassed the reasoning step entirely and immediately streamed standard response tokens, even when explicitly instructed by the user prompt to output a thought process.

## Implementation Steps for Dev Team

1. **Update `co_cli/config.py`**
   - Update `DEFAULT_OLLAMA_SUMMARIZATION_MODEL` to use the `ollama-openai` provider instead of `ollama-native`.
   - Change `think: False` to `reasoning_effort: "none"` in the `api_params`.
   ```python
   DEFAULT_OLLAMA_SUMMARIZATION_MODEL = {
       "model": "qwen3.5:35b-a3b-think",
       "provider": "ollama-openai",
       "api_params": {"temperature": 0.7, "top_p": 0.8, "max_tokens": 16384, "reasoning_effort": "none"},
   }
   ```
   - Review other default configurations (like `DEFAULT_OLLAMA_RESEARCH_MODEL`) to ensure they don't reference `think: False` or `ollama-native`.
   - Remove `"ollama-native"` from the allowed provider checks (e.g., `if provider in ("ollama-openai", "ollama-native"):`).

2. **Remove `OllamaNativeModel` from `co_cli/_model_factory.py`**
   - Delete the `OllamaNativeModel` class and its implementation.
   - Delete the helper function `_messages_to_ollama`.
   - Remove the `if effective_provider == "ollama-native":` block in the `get_model` function.

3. **Cleanup Provider References Across the Codebase**
   - Search for the string `"ollama-native"` and remove all references, error checks, and documentation mentions.
   - Files to check:
     - `co_cli/context/_orchestrate.py`
     - `co_cli/bootstrap/_check.py`
     - `co_cli/prompts/model_quirks/_loader.py`
     - `co_cli/prompts/__init__.py`

4. **Testing**
   - Run the full test suite (`pytest`).
   - Run a live summarization task locally to verify that the `qwen3.5:35b-a3b-think` model generates the summary without outputting `<think>` tags and without invoking a model swap.
