---
title: LLM Models
nav_order: 3
parent: Core
---

# LLM Models

## 1. What & How

Co CLI supports two LLM backends through pydantic-ai's model abstraction. The `get_agent()` factory in `agent.py` selects the model based on `settings.llm_provider`.

```
get_agent()
  ├── provider == "ollama"   (default)
  │   ├── OpenAIProvider(base_url="http://localhost:11434/v1")
  │   └── OpenAIChatModel(model_name, provider)
  │         └── Ollama /v1/chat/completions (OpenAI-compatible)
  │
  └── provider == "gemini"
      └── model = "google-gla:{gemini_model}"
            └── Google GenAI API (cloud)
```

| Provider | Model class | API | ModelSettings |
|----------|-------------|-----|---------------|
| Ollama | `OpenAIChatModel` via `OpenAIProvider` | OpenAI-compatible `/v1` | `temperature=0.6`, `top_p=0.95`, `max_tokens=32768` (Qwen3) |
| Gemini | `"google-gla:{model_name}"` string | Google GenAI | `None` (defaults fine) |

## 2. Core Logic

### Ollama: Qwen3-30B-A3B (default)

| Field | Value |
|-------|-------|
| Model family | Qwen3 (Alibaba) |
| Parameter count | 30.5B total, ~3B active (MoE) |
| Default tag | `qwen3:30b-a3b-thinking-2507-q8_0-agentic` |
| Context window | 262K tokens |
| License | Apache 2.0 |

**Inference parameters** — from the Qwen3 model card (thinking-mode profile):

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `temperature` | `0.6` | Thinking-mode official value; greedy (0.0) causes degenerate loops |
| `top_p` | `0.95` | Thinking-mode official value |
| `max_tokens` | `32768` | Official max recommended for most queries |

**Why hardcoded, not in settings:** These parameters are model-level inference tuning coupled to the specific model. They live next to the model instantiation in `agent.py` for maintenance locality — not user-facing config.

**Settings flow:**

```
agent.py: get_agent() → (agent, model_settings)
    ▼
main.py: chat_loop()
    └── run_turn(agent, ..., model_settings=model_settings)
            └── pydantic-ai → Ollama /v1/chat/completions
                    temperature=0.6, top_p=0.95, max_tokens=32768
```

### Ollama: GLM-4.7-Flash (alternative)

| Field | Value |
|-------|-------|
| Model family | GLM-4.7 (Z.ai) |
| Parameter count | 29.9B total, ~3B active (MoE) |
| Context window | 128K–202K tokens |

**Inference parameters** (SWE-Bench / tool-calling profile):

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `temperature` | `0.7` | Tool-calling profile; greedy (0.0) not used interactively |
| `top_p` | `1.0` | Nucleus sampling disabled when temperature is the active axis |
| `max_tokens` | `16384` | Tool-calling profile |
| `repeat_penalty` | `1.0` | **Critical:** any value > 1.0 causes degenerate loops in GGUF quants |

### Gemini

| Field | Value |
|-------|-------|
| Default model | `gemini-2.5-flash` |
| Provider | Google GenAI (`google-gla:` model string) |
| API key | `settings.gemini_api_key` / `GEMINI_API_KEY` |

No custom `ModelSettings` — Gemini's API defaults are well-suited for tool-calling workloads.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"ollama"` | `"ollama"` or `"gemini"` |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server URL |
| `ollama_model` | `OLLAMA_MODEL` | `"qwen3:30b-a3b-thinking-2507-q8_0-agentic"` | Ollama model tag |
| `ollama_num_ctx` | `OLLAMA_NUM_CTX` | `262144` | Context window size sent per request (note: silently ignored by Ollama's OpenAI API — set via Modelfile instead) |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Google GenAI API key |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-2.5-flash"` | Gemini model name |

**Not configurable:** `temperature`, `top_p`, `max_tokens` — hardcoded in `agent.py` (model-specific tuning, not user-facing).

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agent.py` | `get_agent()` factory — model selection + `ModelSettings` |
| `co_cli/main.py` | Unpacks `(agent, model_settings)`, passes to `run_turn()` |
| `co_cli/config.py` | `Settings` with LLM provider fields |
| `ollama/Modelfile.qwen3-30b-a3b-q4` | Qwen3 Q4_K_M agentic profile |
| `ollama/Modelfile.qwen3-30b-a3b` | Qwen3 Q8_0 agentic profile |
| `ollama/Modelfile.glm-4.7-flash` | GLM-4.7 Q4_K_M agentic profile |
| `ollama/Modelfile.glm-4.7-flash-q8` | GLM-4.7 Q8_0 agentic profile |
| `tests/test_llm_e2e.py` | LLM E2E tests for both providers |

---

## Ollama Local Setup

### Why Modelfiles matter

Ollama ships models with a **4096-token default context window**. Agentic systems need far more — system prompts, tool schemas, conversation history, and tool outputs all compete for context space. A 4K window causes silent prompt truncation: Ollama drops input without warning, degrading tool calling and instruction following.

**Critical:** Ollama's OpenAI-compatible API (`/v1/chat/completions`) **silently ignores `num_ctx`** from request parameters ([ollama#5356](https://github.com/ollama/ollama/issues/5356)). The Modelfile is the only reliable way to set context window size. The `-agentic` model tags in this repo have `num_ctx` baked in. Base tags default to 4096 tokens.

Two constraints apply to thinking models:
- **Temperature must not be 0.** Greedy decoding causes degenerate repetition loops that exhaust the output budget. Qwen3's model card explicitly warns against it.
- **`repeat_penalty` must be exactly 1.0** for GGUF quants of Qwen3 and GLM-4.7-Flash. Any value above 1.0 causes repetition loops (known GGUF scoring bug, not a model property).

### Modelfile setup

`ollama create <tag> -f Modelfile` writes only a manifest and params blob. Weight files are **shared by content hash** — an `-agentic` tag costs essentially zero additional disk space.

Pre-built Modelfiles are in the `ollama/` directory. Pull the base model first, then create the `-agentic` tag:

#### Qwen3-30B-A3B

| Tag | Modelfile | Quant | Size |
|-----|-----------|-------|------|
| `qwen3:30b-a3b-thinking-2507-q4_k_m-agentic` | `Modelfile.qwen3-30b-a3b-q4` | Q4_K_M | ~20 GB |
| `qwen3:30b-a3b-thinking-2507-q8_0-agentic` | `Modelfile.qwen3-30b-a3b` | Q8_0 | ~32 GB |

```bash
ollama pull qwen3:30b-a3b-thinking-2507-q8_0
ollama create qwen3:30b-a3b-thinking-2507-q8_0-agentic -f ollama/Modelfile.qwen3-30b-a3b
```

#### GLM-4.7-Flash

| Tag | Modelfile | Quant | Size |
|-----|-----------|-------|------|
| `glm-4.7-flash:q4_k_m-agentic` | `Modelfile.glm-4.7-flash` | Q4_K_M | ~19 GB |
| `glm-4.7-flash:q8_0-agentic` | `Modelfile.glm-4.7-flash-q8` | Q8_0 | ~31 GB |

```bash
ollama pull glm-4.7-flash:q4_k_m
ollama create glm-4.7-flash:q4_k_m-agentic -f ollama/Modelfile.glm-4.7-flash
```

Verify parameters after building:

```bash
ollama show qwen3:30b-a3b-thinking-2507-q8_0-agentic
```

Update Co settings to use your preferred tag:

```json
{ "llm_provider": "ollama", "ollama_model": "qwen3:30b-a3b-thinking-2507-q8_0-agentic" }
```

### Modelfile parameter reference

| Parameter | Qwen3 (thinking) | GLM-4.7-Flash | Notes |
|-----------|-----------------|---------------|-------|
| `num_ctx` | **262144** | **202752** | Qwen3: 262K native. GLM: Unsloth GGUF with RoPE scaling (official HF card = 128K) |
| `num_predict` | **32768** | **16384** | Official max for most queries |
| `temperature` | **0.6** | **0.7** | Thinking-mode / tool-calling profile |
| `top_p` | **0.95** | **1.0** | Thinking-mode official. GLM: disabled when temperature is active axis |
| `top_k` | **20** | unset | Qwen3 official; no GLM recommendation |
| `repeat_penalty` | **1.0** | **1.0** | **Critical for both** — any value > 1.0 causes loops in GGUF quants |

### Modelfile examples

**Qwen3-30B-A3B (Q8_0):**

```dockerfile
FROM qwen3:30b-a3b-thinking-2507-q8_0

PARAMETER num_ctx 262144
PARAMETER num_predict 32768
PARAMETER temperature 0.6
PARAMETER top_p 0.95
PARAMETER top_k 20
PARAMETER repeat_penalty 1.0
```

**GLM-4.7-Flash (Q4_K_M):**

```dockerfile
FROM glm-4.7-flash:q4_k_m

PARAMETER num_ctx 202752
PARAMETER num_predict 16384
PARAMETER temperature 0.7
PARAMETER top_p 1.0
PARAMETER repeat_penalty 1.0
```

### Sizing guide

KV cache grows linearly with `num_ctx`. A 262K window with Qwen3 Q8 uses ~28 GB for KV cache alone on top of the 32 GB weights.

| System RAM | Recommended `num_ctx` | Notes |
|------------|----------------------|-------|
| 16 GB | 8192–16384 | Tight — monitor with `ollama ps` |
| 32 GB | 16384–32768 | Comfortable for models ≤14B |
| 64 GB | 32768–65536 | Good headroom for 30B models |
| 128 GB | Model native (262144 / 202752) | Full context; KV cache ~28 GB for Qwen3 Q8 at 262K |

Detect context truncation in Ollama server logs:

```
level=WARN source=runner.go msg="truncating input prompt" limit=4096 prompt=9383
```

### Model recommendations

Models must support **tool calling** for Co's agentic workflow.

| Model | Parameters | Context | Tool Calling | RAM (Q8) | Notes |
|-------|-----------|---------|-------------|----------|-------|
| Qwen3 30B-A3B | 30.5B (MoE) | 262K | Yes | ~60 GB | Default; thinking mode; temperature ≥ 0.6 required |
| GLM-4.7-Flash | 29.9B (MoE) | 128K–202K | Yes | ~58 GB | repeat_penalty must be 1.0 |
| Qwen2.5-Coder 32B | 32B | 128K | Yes | ~35 GB | Dense; strong at code |
| Llama 3.3 70B | 70B | 128K | Yes | ~75 GB | Q4_K_M recommended; needs 64 GB+ even quantised |

Verify tool calling works:

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
  "messages": [{"role": "user", "content": "What time is it?"}],
  "tools": [{"type": "function", "function": {"name": "get_time", "description": "Get current time", "parameters": {"type": "object", "properties": {}}}}]
}'
```

Response should contain a `tool_calls` array, not a text answer.

### Known issues and quirks

**Qwen3 thinking models:**
- Do not use `temperature=0`. Greedy decoding causes degenerate repetition in the thinking chain. Minimum safe: 0.6 (thinking mode).
- Thinking tokens are implicit in Ollama — no separate budget control. `num_predict` caps total output including thinking tokens.

**GLM-4.7-Flash:**
- `repeat_penalty` must be `1.0`. Any higher causes degenerate loops in GGUF quants. Unsloth re-uploaded all quants (Jan 21) after this was found.
- May output Chinese on the first turn. Counter-steering in `co_cli/prompts/quirks/ollama/glm-4.7-flash.md` addresses this.
- Responds better to imperative language — use MUST, REQUIRED, STRICTLY in system prompt constraints; avoid suggestive phrasing.
- `num_ctx` discrepancy: official HF card = 128K, Unsloth GGUF with RoPE scaling = 202,752. The `-agentic` Modelfiles use the Unsloth-extended value.

### Server tuning

Set before starting the Ollama server (e.g. `~/.zshrc` or launchd plist):

| Variable | Purpose | Example |
|----------|---------|---------|
| `OLLAMA_NUM_PARALLEL` | Concurrent request slots | `2` |
| `OLLAMA_MAX_LOADED_MODELS` | Models kept in memory | `1` for large models |
| `OLLAMA_KEEP_ALIVE` | Keep model loaded | `24h` (avoids 10–30s cold-start) |
| `OLLAMA_FLASH_ATTENTION` | Flash attention | `1` (faster, less memory) |

**macOS (Apple Silicon):** Metal is used automatically. `OLLAMA_KEEP_ALIVE=24h` eliminates cold-start latency. `ollama ps` shows `100% GPU` for full offload.

```bash
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_FLASH_ATTENTION=1
ollama serve
```

```bash
ollama ps                    # see loaded models and RAM usage
ollama stop <model:tag>      # unload a specific model
```

### References

- [Ollama Modelfile Reference](https://docs.ollama.com/modelfile)
- [Ollama num_ctx silently ignored in OpenAI API](https://github.com/ollama/ollama/issues/5356)
- [Qwen3-30B-A3B model card](https://huggingface.co/Qwen/Qwen3-30B-A3B)
- [GLM-4.7-Flash model card](https://huggingface.co/zai-org/GLM-4.7-Flash)
- [unsloth/GLM-4.7-Flash-GGUF — repeat penalty bug](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/discussions/13)
- [Cerebras GLM-4.7 migration guide](https://www.cerebras.ai/blog/glm-4-7-migration-guide)
