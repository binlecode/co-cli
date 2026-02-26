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
| Ollama | `OpenAIChatModel` via `OpenAIProvider` | OpenAI-compatible `/v1` | Loaded per normalized model from `co_cli/prompts/quirks/ollama/*.md` |
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

### Ollama: Qwen3-Coder-Next (coding alternative)

| Field | Value |
|-------|-------|
| Model family | Qwen3-Coder-Next (Alibaba) |
| Parameter count | 32B (dense) |
| Recommended tag | `qwen3-coder-next:q4_k_m-agentic` |
| Context window | 262K tokens |
| License | Apache 2.0 |

**Inference parameters** (official profile + agentic sizing):

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `temperature` | `1.0` | Official recommended decoding profile |
| `top_p` | `0.95` | Official recommended decoding profile |
| `top_k` | `40` | Official recommended decoding profile |
| `max_tokens` | `65536` | Long coding output budget |
| `repeat_penalty` | `1.0` | Keep neutral to avoid GGUF repetition side-effects |

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
| `co_cli/prompts/quirks/ollama/qwen3-coder-next.md` | Qwen3-Coder-Next inference profile + counter-steering |
| `ollama/Modelfile.qwen3-30b-a3b-thinking-2507-q8_0-agentic` | Qwen3 Q8_0 agentic profile |
| `ollama/Modelfile.qwen3-coder-next-q4_k_m-agentic` | Qwen3-Coder-Next Q4_K_M agentic profile |
| `tests/test_llm_e2e.py` | LLM E2E tests for both providers |

---

## Ollama Local Setup

### Why Modelfiles matter

Ollama ships models with a **4096-token default context window**. Agentic systems need far more — system prompts, tool schemas, conversation history, and tool outputs all compete for context space. A 4K window causes silent prompt truncation: Ollama drops input without warning, degrading tool calling and instruction following.

**Critical:** Ollama's OpenAI-compatible API (`/v1/chat/completions`) **silently ignores `num_ctx`** from request parameters ([ollama#5356](https://github.com/ollama/ollama/issues/5356)). The Modelfile is the only reliable way to set context window size. The `-agentic` model tags in this repo have `num_ctx` baked in. Base tags default to 4096 tokens.

Two constraints apply to thinking models:
- **Temperature must not be 0.** Greedy decoding causes degenerate repetition loops that exhaust the output budget. Qwen3's model card explicitly warns against it.
- **`repeat_penalty` must be exactly 1.0** for Qwen GGUF quants in this repo to avoid repetition loops.

### Modelfile setup

`ollama create <tag> -f Modelfile` writes only a manifest and params blob. Weight files are **shared by content hash** — an `-agentic` tag costs essentially zero additional disk space.

Pre-built Modelfiles are in the `ollama/` directory. Pull the base model first, then create the `-agentic` tag:

#### Qwen3-30B-A3B

| Tag | Modelfile | Quant | Size |
|-----|-----------|-------|------|
| `qwen3:30b-a3b-thinking-2507-q8_0-agentic` | `Modelfile.qwen3-30b-a3b-thinking-2507-q8_0-agentic` | Q8_0 | ~32 GB |

```bash
ollama pull qwen3:30b-a3b-thinking-2507-q8_0
ollama create qwen3:30b-a3b-thinking-2507-q8_0-agentic -f ollama/Modelfile.qwen3-30b-a3b-thinking-2507-q8_0-agentic
```

#### Qwen3-Coder-Next

| Tag | Modelfile | Quant | Size |
|-----|-----------|-------|------|
| `qwen3-coder-next:q4_k_m-agentic` | `Modelfile.qwen3-coder-next-q4_k_m-agentic` | Q4_K_M | ~51 GB |

```bash
ollama pull qwen3-coder-next:q4_k_m
ollama create qwen3-coder-next:q4_k_m-agentic -f ollama/Modelfile.qwen3-coder-next-q4_k_m-agentic
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

| Parameter | Qwen3 (thinking) | Qwen3-Coder-Next | Notes |
|-----------|------------------|------------------|-------|
| `num_ctx` | **262144** | **262144** | 262K native context for both models |
| `num_predict` | **32768** | **65536** | Coder model gets larger output budget for code-heavy tasks |
| `temperature` | **0.6** | **1.0** | Model-card recommended decoding profiles |
| `top_p` | **0.95** | **0.95** | Model-card recommended decoding profiles |
| `top_k` | **20** | **40** | Model-card recommended decoding profiles |
| `repeat_penalty` | **1.0** | **1.0** | Fixed at 1.0 for GGUF stability |

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

**Qwen3-Coder-Next (Q4_K_M):**

```dockerfile
FROM qwen3-coder-next:q4_k_m

PARAMETER num_ctx 262144
PARAMETER num_predict 65536
PARAMETER temperature 1.0
PARAMETER top_p 0.95
PARAMETER top_k 40
PARAMETER repeat_penalty 1.0
```

### Sizing guide

KV cache grows linearly with `num_ctx`. A 262K window with Qwen3 Q8 uses ~28 GB for KV cache alone on top of the 32 GB weights.

| System RAM | Recommended `num_ctx` | Notes |
|------------|----------------------|-------|
| 16 GB | 8192–16384 | Tight — monitor with `ollama ps` |
| 32 GB | 16384–32768 | Comfortable for models ≤14B |
| 64 GB | 32768–65536 | Good headroom for 30B models |
| 128 GB | Model native (262144) | Full context; KV cache ~28 GB for Qwen3 Q8 at 262K |

Detect context truncation in Ollama server logs:

```
level=WARN source=runner.go msg="truncating input prompt" limit=4096 prompt=9383
```

### Model recommendations

Models must support **tool calling** for Co's agentic workflow.

| Model | Parameters | Context | Tool Calling | RAM (Q8) | Notes |
|-------|-----------|---------|-------------|----------|-------|
| Qwen3 30B-A3B | 30.5B (MoE) | 262K | Yes | ~60 GB | Default; thinking mode; temperature ≥ 0.6 required |
| Qwen3-Coder-Next | 32B | 262K | Yes | n/a (Q4_K_M ~51 GB) | Strong coding model; use `qwen3-coder-next:q4_k_m-agentic` |
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

**Qwen3-Coder-Next (community notes + implementation):**
- Use the official decoding profile (`temperature=1.0`, `top_p=0.95`, `top_k=40`) and set `num_predict` high for coding tasks.
- Tool-call reliability depends on chat template/parser alignment in serving stacks; for vLLM deployments, use the Qwen3 XML tool parser.
- Keep tool-call output strict (valid arguments JSON, no pseudo tool calls). Counter-steering and inference for this repo live in `co_cli/prompts/quirks/ollama/qwen3-coder-next.md`.
- Agentic Modelfile implementation is `ollama/Modelfile.qwen3-coder-next-q4_k_m-agentic`.

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
- [Qwen3-Coder-Next model card](https://huggingface.co/Qwen/Qwen3-Coder-Next)
- [Qwen3-Coder-Next community discussion: tool-calling reliability](https://huggingface.co/Qwen/Qwen3-Coder-Next/discussions/14)
- [Qwen3-Coder-Next community discussion: parser/template pitfalls](https://huggingface.co/Qwen/Qwen3-Coder-Next/discussions/17)
- [vLLM tool calling documentation](https://docs.vllm.ai/en/latest/features/tool_calling/)
