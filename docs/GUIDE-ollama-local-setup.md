# Ollama Local Setup for Agentic Systems

Best practices for configuring Ollama as a local LLM backend for agentic CLI tools like Co.

---

## Why This Matters

Ollama ships models with a **4096-token default context window**. That's fine for single-turn chat, but agentic systems need much more: system prompts, tool schemas, conversation history, and tool outputs compete for context space. A 4K window causes silent prompt truncation — Ollama drops input without error, degrading tool calling and instruction following.

**Critical:** Ollama's OpenAI-compatible API (`/v1/chat/completions`) **silently ignores `num_ctx`** from request parameters. The Modelfile is the only reliable way to set context window size. The `-agentic` variants in this repo ship with the correct `num_ctx`. Without it, GLM-4.7-Flash defaults to 2048 tokens and **loses multi-turn conversation history** even for short conversations.

---

## 1. Server-Level: Modelfile Configuration

### Create an agentic model profile

Check your model's native context length first:

```bash
ollama show glm-4.7-flash:q4_k_m
# Look for "context length" — GLM-4.7-Flash supports 202K
```

Create a Modelfile that sets `num_ctx` to the model's native context length. An empty large window costs no more than an empty small one — inference speed depends on how much context is actually *filled*, not the limit. Let Co's context governance (sliding window + summarization) manage what fills the window.

Pre-built Modelfiles are in the `ollama/` directory of this repo. Choose a quantisation:

| Tag | Quantisation | Size | Tradeoff |
|-----|-------------|------|----------|
| `q4_k_m-agentic` | Q4_K_M | ~19 GB | Faster inference, lower RAM; minor quality loss |
| `q8_0-agentic` | Q8_0 | ~31 GB | Near-original quality; needs 64GB+ RAM |

Build both (or whichever you need):

```bash
ollama create glm-4.7-flash:q4_k_m-agentic -f ollama/Modelfile.glm-4.7-flash
ollama create glm-4.7-flash:q8_0-agentic   -f ollama/Modelfile.glm-4.7-flash-q8
```

Verify parameters:

```bash
ollama show glm-4.7-flash:q4_k_m-agentic
ollama show glm-4.7-flash:q8_0-agentic
```

Update Co settings to use your preferred tag:

```json
{ "ollama_model": "glm-4.7-flash:q8_0-agentic" }
```

### Key Modelfile parameters for agentic use

| Parameter | Default | Recommendation | Why |
|-----------|---------|----------------|-----|
| `num_ctx` | 4096 | Model native (e.g. 202752) | Use full training context; cost scales with fill, not limit |
| `num_predict` | 128 | 16384 | Official agentic profile value; prevents runaway generation |
| `temperature` | model-dependent | 0.7 | GLM-4.7 Terminal/SWE-Bench profile; balanced for tool calling |
| `top_p` | 0.9 | 1.0 | Agentic profile disables nucleus sampling; temperature controls randomness |
| `repeat_penalty` | 1.1 | 1.0 (disabled) | GLM docs explicitly warn to disable; avoids penalising structured/JSON output |

### Example: full agentic Modelfile

```dockerfile
FROM glm-4.7-flash:q4_k_m

PARAMETER num_ctx 202752
PARAMETER num_predict 16384
PARAMETER temperature 0.7
PARAMETER top_p 1.0
PARAMETER repeat_penalty 1.0
```

---

## 2. Client-Level: Co Configuration

Co sends `num_ctx` with every Ollama request via `extra_body`. **However**, Ollama's OpenAI-compatible API currently ignores this parameter (see [ollama#5356](https://github.com/ollama/ollama/issues/5356)). The Modelfile `PARAMETER num_ctx` is the only reliable mechanism. Co's client-side setting serves as documentation and future-proofing for when Ollama adds support.

| Setting | Env Var | Default | Purpose |
|---------|---------|---------|---------|
| `ollama_host` | `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `OLLAMA_MODEL` | `glm-4.7-flash:q4_k_m-agentic` | Model tag (must use `-agentic` variant) |
| `ollama_num_ctx` | `OLLAMA_NUM_CTX` | `202752` | Context window sent per request |

Example `settings.json`:

```json
{
  "llm_provider": "ollama",
  "ollama_model": "glm-4.7-flash:q4_k_m-agentic",
  "ollama_num_ctx": 202752
}
```

**The Modelfile is the single source of truth** for `num_ctx`. Always use `-agentic` tags which have `num_ctx` baked in. Base tags (e.g. `glm-4.7-flash:q4_k_m` without `-agentic`) default to 2048 tokens and will break multi-turn conversations.

---

## 3. Sizing Guide

Context window sizing depends on RAM (for CPU inference) or VRAM (for GPU offload). KV cache grows linearly with `num_ctx`.

| System RAM | Recommended `num_ctx` | Notes |
|------------|----------------------|-------|
| 16 GB | 8192–16384 | Tight — monitor with `ollama ps` |
| 32 GB | 16384–32768 | Comfortable for most models <14B |
| 64 GB | 32768–65536 | Good headroom for 30B models |
| 128 GB | Model native (e.g. 202752) | Use full training context — KV cache ~12-18 GB |

Check actual memory usage after loading:

```bash
ollama ps
# Shows loaded models, their size, and memory allocated
```

### How to tell if your context is too small

Look for this Ollama warning in server logs:

```
level=WARN source=runner.go msg="truncating input prompt" limit=4096 prompt=9383
```

This means Ollama silently dropped input. Your model is flying blind on the truncated portion — system prompt fragments, tool definitions, or early conversation context.

---

## 4. Model Recommendations for Agentic Use

Models must support **tool calling** for Co's agentic workflow. Not all Ollama models do.

| Model | Parameters | Context | Tool Calling | Quantisation Sweet Spot |
|-------|-----------|---------|-------------|------------------------|
| GLM-4.7-Flash | 29.9B (MoE) | 202K | Yes | q4_k_m (19GB) or q8_0 (31GB) |
| Qwen3 30B-A3B | 30.5B (MoE) | 262K | Yes | q8_0 (32GB) |
| Qwen2.5-Coder 32B | 32B | 128K | Yes | q4_k_m or q8_0 |
| Llama 3.3 70B | 70B | 128K | Yes | q4_k_m (needs 64GB+) |

### Verify tool calling works

```bash
# GLM-4.7-Flash
curl http://localhost:11434/api/chat -d '{
  "model": "glm-4.7-flash:q4_k_m-agentic",
  "messages": [{"role": "user", "content": "What time is it?"}],
  "tools": [{"type": "function", "function": {"name": "get_time", "description": "Get current time", "parameters": {"type": "object", "properties": {}}}}]
}'

# GLM-4.7-Flash q8_0
curl http://localhost:11434/api/chat -d '{
  "model": "glm-4.7-flash:q8_0-agentic",
  "messages": [{"role": "user", "content": "What time is it?"}],
  "tools": [{"type": "function", "function": {"name": "get_time", "description": "Get current time", "parameters": {"type": "object", "properties": {}}}}]
}'

# Qwen3 30B-A3B
curl http://localhost:11434/api/chat -d '{
  "model": "qwen3:30b-a3b-thinking-2507-q8_0-agentic",
  "messages": [{"role": "user", "content": "What time is it?"}],
  "tools": [{"type": "function", "function": {"name": "get_time", "description": "Get current time", "parameters": {"type": "object", "properties": {}}}}]
}'
```

Each response should contain a `tool_calls` array, not a text answer.

---

## 5. Ollama Server Tuning

### Environment variables

Set these before starting the Ollama server (e.g. in `~/.zshrc` or a launchd plist):

| Variable | Purpose | Example |
|----------|---------|---------|
| `OLLAMA_NUM_PARALLEL` | Concurrent request slots | `2` (default 1) |
| `OLLAMA_MAX_LOADED_MODELS` | Models kept in memory | `1` for large models (see tip below) |
| `OLLAMA_KEEP_ALIVE` | Time to keep model loaded | `24h` (avoid reload latency) |
| `OLLAMA_FLASH_ATTENTION` | Enable flash attention | `1` (faster, less memory) |

### Memory management

Large agentic models (30B+ at full context) use 40-60 GB each. If you test multiple models without unloading, RAM fills up fast. Set `OLLAMA_MAX_LOADED_MODELS=1` so Ollama auto-evicts the previous model when loading a new one. To manually free memory:

```bash
ollama ps                    # see what's loaded and how much RAM
ollama stop <model:tag>      # unload a specific model
ollama stop --all            # unload everything
```

### macOS (Apple Silicon) specifics

- Ollama uses Metal for GPU acceleration automatically on Apple Silicon
- Unified memory means RAM = VRAM — a 128GB MacBook Pro can load large models fully on GPU
- Set `OLLAMA_KEEP_ALIVE=24h` to avoid 10-30s cold-start on each chat session
- Monitor with `ollama ps` — the "Processor" column shows `100% GPU` for full Metal offload

### Start with optimised settings

```bash
export OLLAMA_KEEP_ALIVE=24h
export OLLAMA_FLASH_ATTENTION=1
ollama serve
```

---

## References

- [Ollama Modelfile Reference](https://docs.ollama.com/modelfile)
- [Ollama num_ctx behaviour](https://github.com/ollama/ollama/issues/2714)
- [OpenAI API ignores num_ctx](https://github.com/ollama/ollama/issues/5356) — `num_ctx` must be in Modelfile, not API request
- [GLM-4.7-Flash chat template issues](https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/discussions/15) — Ollama template incompatibility
- [GLM-4.7-Flash model card](https://huggingface.co/zai-org/GLM-4.7-Flash)
