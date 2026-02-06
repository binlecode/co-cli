# Design: LLM Models

**Status:** Active
**Last Updated:** 2026-02-06

## Overview

Co CLI supports multiple LLM backends through pydantic-ai's model abstraction. The `get_agent()` factory in `co_cli/agent.py` selects the model based on `settings.llm_provider` and returns both the agent and provider-specific `ModelSettings`.

```python
def get_agent() -> tuple[Agent[CoDeps, str], ModelSettings | None]:
```

The second element is `None` when the provider's defaults are acceptable (Gemini), or a `ModelSettings` dict when custom inference parameters are needed (Ollama).

---

## Provider Matrix

| Provider | Model String / Class | API | Settings |
|----------|---------------------|-----|----------|
| Gemini | `"google-gla:{model_name}"` | Google GenAI | `None` (defaults are fine) |
| Ollama | `OpenAIChatModel` via `OpenAIProvider` | OpenAI-compatible (`/v1`) | `ModelSettings(temperature=0.7, top_p=1.0, max_tokens=16384)` |

---

## Ollama: GLM-4.7-Flash (31B MoE)

### Model Identity

| Field | Value |
|-------|-------|
| Model family | GLM-4.7 |
| Developer | Z.ai (formerly THUDM/Zhipu AI) |
| Parameter count | 31B total, ~3B active (MoE) |
| Ollama tag | `glm-4.7-flash:q8_0` (or configured via `ollama_model`) |
| HuggingFace | `zai-org/GLM-4.7-Flash` |
| Context window | 128K tokens |
| Quantization | Q8_0 (31 GB on disk) |
| License | MIT |
| Requires | Ollama 0.14.3+ |

### Connection Architecture

Ollama exposes an OpenAI-compatible API. Co CLI connects via pydantic-ai's `OpenAIChatModel`:

```
co_cli/agent.py
    │
    ├── OpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama")
    │
    └── OpenAIChatModel(model_name=settings.ollama_model, provider=provider)
            │
            ▼
        Ollama server
            │
            └── /v1/chat/completions  (OpenAI-compatible endpoint)
```

The `api_key="ollama"` is a placeholder — Ollama ignores authentication on localhost.

### Inference Parameters

The [HuggingFace model card](https://huggingface.co/zai-org/GLM-4.7-Flash) documents four parameter profiles for different workloads:

| Profile | temp | top_p | max_tokens | Use Case |
|---------|------|-------|------------|----------|
| Default (most tasks) | 1.0 | 0.95 | 131072 | General conversation |
| Multi-turn Agentic (τ²-Bench, Terminal Bench 2) | 1.0 | 0.95 | 131072 | Agent + Preserved Thinking mode |
| **Terminal / SWE-Bench Verified** | **0.7** | **1.0** | **16384** | **Shell/code execution tasks** |
| τ²-Bench standalone | 0 | — | 16384 | Greedy decoding |

Co CLI uses the **Terminal / SWE-Bench Verified** profile — the closest match to our tool-calling pattern (shell commands in a Docker sandbox + Google/Slack API calls). The multi-turn agentic profile requires Preserved Thinking mode, which Co CLI does not enable.

```python
model_settings = ModelSettings(
    temperature=0.7,
    top_p=1.0,
    max_tokens=16384,
)
```

#### Parameter Details

| Parameter | Value | Ollama Default | Rationale |
|-----------|-------|----------------|-----------|
| `temperature` | `0.7` | `0.8` | From the Terminal / SWE-Bench Verified profile. Lower entropy makes tool-call JSON schemas more deterministic. |
| `top_p` | `1.0` | `0.9` | Full distribution — let temperature alone shape the sampling. Avoids double-clipping (temp + top_p both narrowing). |
| `max_tokens` | `16384` | `2048` | From the Terminal / SWE-Bench Verified profile. Prevents truncated responses during multi-step tool chains. |

#### Parameters Not Set

| Parameter | Why Not |
|-----------|---------|
| `top_k` (40) | The OpenAI-compatible API does not expose `top_k`. `top_p` provides equivalent tail-cutting behavior. |
| Stop sequences | Ollama handles these automatically via the chat template. Adding them manually risks conflicting with the template's built-in stop tokens. |
| `repeat_penalty` | GLM-4.7-Flash's reference config does not specify one. Ollama's default (1.1) is acceptable for conversational use. |

### How Settings Flow Through the System

```
co_cli/agent.py: get_agent()
    │
    ├── Returns: (agent, model_settings)
    │                        │
    │                        ▼
    │               ModelSettings(
    │                   temperature=0.7,
    │                   top_p=1.0,
    │                   max_tokens=16384,
    │               )
    │
    ▼
co_cli/main.py: chat_loop()
    │
    ├── agent, model_settings = get_agent()
    │
    └── result = await agent.run(
            user_input,
            deps=deps,
            message_history=message_history,
            model_settings=model_settings,    # <-- passed here
        )
            │
            ▼
        pydantic-ai internals
            │
            └── OpenAI API call with temperature=0.7, top_p=1.0, max_tokens=16384
```

### Why Hardcoded, Not in Settings

These parameters are **model-level inference tuning**, not user-facing configuration:

1. **Coupled to the model** — temperature=0.7 is correct for GLM-4.7-Flash's Terminal/SWE profile specifically. Exposing it in `settings.json` invites users to change it without understanding the model-specific calibration.
2. **Not provider-generic** — Gemini doesn't need them at all. A `settings.json` field would apply to both providers incorrectly.
3. **Maintenance locality** — When the Ollama model changes (e.g., upgrading to a new GLM version), the parameters live right next to the model instantiation in `agent.py`, making it obvious they need updating together.

If a future use case requires per-user override (e.g., creative vs. precise mode), the right approach is a `mode` setting that maps to pre-defined parameter bundles, not raw temperature/top_p knobs.

---

## Gemini

### Model Identity

| Field | Value |
|-------|-------|
| Default model | `gemini-2.0-flash` (configurable via `gemini_model`) |
| Provider | Google GenAI (via `google-gla:` model string) |
| API key | `settings.gemini_api_key` / `GEMINI_API_KEY` env var |

### Connection Architecture

Pydantic-ai resolves the `"google-gla:{model_name}"` string to a `GoogleModel` internally. The API key is set via environment variable:

```
co_cli/agent.py
    │
    ├── os.environ.setdefault("GEMINI_API_KEY", settings.gemini_api_key)
    │
    └── model = f"google-gla:{settings.gemini_model}"
            │
            ▼
        pydantic-ai GoogleModel
            │
            └── Google GenAI API (cloud)
```

### Inference Parameters

No custom `ModelSettings` — Gemini's API defaults are well-suited for tool-calling workloads. Returns `None` as the second element of `get_agent()`.

---

## Configuration Reference

### settings.json / Environment Variables

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"gemini"` | `"gemini"` or `"ollama"` |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Google GenAI API key |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-2.0-flash"` | Gemini model name |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server URL |
| `ollama_model` | `OLLAMA_MODEL` | `"glm-4.7-flash:q8_0"` | Ollama model tag |

### What Is NOT Configurable

| Parameter | Lives In | Why |
|-----------|----------|-----|
| `temperature` | `agent.py` (hardcoded) | Model-specific tuning, not user preference |
| `top_p` | `agent.py` (hardcoded) | Model-specific tuning |
| `max_tokens` | `agent.py` (hardcoded) | Prevents truncation; model-specific |

---

## Testing

Agent initialization is verified through LLM E2E tests in `tests/test_llm_e2e.py` which hit real Gemini/Ollama endpoints. No unit tests — per project policy (functional tests only).

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/agent.py` | `get_agent()` factory — model selection + `ModelSettings` |
| `co_cli/main.py` | Unpacks `(agent, model_settings)`, passes settings to `agent.run()` |
| `co_cli/config.py` | `Settings` with LLM provider fields |
| `tests/test_llm_e2e.py` | LLM E2E tests for both providers |

---

## Future Considerations

| Enhancement | Description | Status |
|-------------|-------------|--------|
| Mode presets | `"precise"` / `"creative"` mode mapping to parameter bundles | Not planned |
| Per-model settings map | Dict of `{model_name: ModelSettings}` for multi-model support | Not planned |
| Streaming | `agent.run_stream()` with `model_settings` passthrough | Not planned |
| Ollama model auto-detection | Query `/api/tags` to validate model exists before first call | Not planned |
