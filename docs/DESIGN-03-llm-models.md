---
title: "03 — LLM Models"
parent: Core
nav_order: 3
---

# Design: LLM Models

## 1. What & How

Co CLI supports multiple LLM backends through pydantic-ai's model abstraction. The `get_agent()` factory in `co_cli/agent.py` selects the model based on `settings.llm_provider` and returns the agent with provider-specific `ModelSettings`.

```
co_cli/agent.py: get_agent()
    ├── provider == "gemini"
    │   └── model = "google-gla:{gemini_model}"
    │       └── Google GenAI API (cloud)
    │
    └── provider == "ollama"
        ├── OpenAIProvider(base_url="http://localhost:11434/v1")
        └── OpenAIChatModel(model_name, provider)
            └── Ollama /v1/chat/completions (OpenAI-compatible)
```

| Provider | Model String / Class | API | ModelSettings |
|----------|---------------------|-----|---------------|
| Gemini | `"google-gla:{model_name}"` | Google GenAI | `None` (defaults fine) |
| Ollama | `OpenAIChatModel` via `OpenAIProvider` | OpenAI-compatible `/v1` | `ModelSettings(temperature=0.7, top_p=1.0, max_tokens=16384)` |

## 2. Core Logic

### Ollama: GLM-4.7-Flash (31B MoE)

| Field | Value |
|-------|-------|
| Model family | GLM-4.7 (Z.ai / formerly THUDM) |
| Parameter count | 31B total, ~3B active (MoE) |
| Default tag | `glm-4.7-flash:q4_k_m` |
| Context window | 128K tokens |
| License | MIT |

**Inference parameters** — from the [HuggingFace model card](https://huggingface.co/zai-org/GLM-4.7-Flash) Terminal / SWE-Bench Verified profile:

| Parameter | Value | Ollama Default | Rationale |
|-----------|-------|----------------|-----------|
| `temperature` | `0.7` | `0.8` | Lower entropy for deterministic tool-call JSON |
| `top_p` | `1.0` | `0.9` | Full distribution — let temperature alone shape sampling |
| `max_tokens` | `16384` | `2048` | Prevents truncated responses in multi-step tool chains |

**Why hardcoded, not in settings:** These parameters are model-level inference tuning, not user-facing configuration. They're coupled to the specific model and live next to the model instantiation in `agent.py` for maintenance locality.

**Settings flow:**

```
agent.py: get_agent() → (agent, model_settings)
    │
    ▼
main.py: chat_loop()
    └── run_turn(agent, ..., model_settings=model_settings)
            └── _stream_events() → pydantic-ai OpenAI API call with temperature=0.7, top_p=1.0, max_tokens=16384
```

### Gemini

| Field | Value |
|-------|-------|
| Default model | `gemini-2.0-flash` |
| Provider | Google GenAI (via `google-gla:` model string) |
| API key | `settings.gemini_api_key` / `GEMINI_API_KEY` env var |

No custom `ModelSettings` — Gemini's API defaults are well-suited for tool-calling workloads.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"gemini"` | `"gemini"` or `"ollama"` |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Google GenAI API key |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-2.0-flash"` | Gemini model name |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server URL |
| `ollama_model` | `OLLAMA_MODEL` | `"glm-4.7-flash:q4_k_m"` | Ollama model tag |

**Not configurable:** `temperature`, `top_p`, `max_tokens` — hardcoded in `agent.py` (model-specific tuning).

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agent.py` | `get_agent()` factory — model selection + `ModelSettings` |
| `co_cli/main.py` | Unpacks `(agent, model_settings)`, passes to `run_turn()` |
| `co_cli/config.py` | `Settings` with LLM provider fields |
| `tests/test_llm_e2e.py` | LLM E2E tests for both providers |
