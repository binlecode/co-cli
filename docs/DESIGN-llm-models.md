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
| Ollama | `OpenAIChatModel` via `OpenAIProvider` | OpenAI-compatible (`/v1`) | `ModelSettings(temperature=1.0, top_p=0.95, max_tokens=16384)` |

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

GLM-4.7-Flash was evaluated and tuned with specific generation parameters documented in the HuggingFace model card. These differ from Ollama's built-in defaults.

```python
model_settings = ModelSettings(
    temperature=1.0,
    top_p=0.95,
    max_tokens=16384,
)
```

#### Parameter Details

| Parameter | Value | Ollama Default | Rationale |
|-----------|-------|----------------|-----------|
| `temperature` | `1.0` | `0.8` | GLM-4.7-Flash was tuned/evaluated at temp=1.0. Ollama's default of 0.8 compresses the probability distribution tighter than intended, which hurts tool-call accuracy — the model under-samples valid tool schemas and over-commits to the most probable token at each position. |
| `top_p` | `0.95` | `0.9` | From GLM's recommended config. Trims the bottom 5% of unlikely tokens while preserving the model's full intended range at temp=1.0. The combination of temp=1.0 + top_p=0.95 gives the distribution the model was calibrated for. |
| `max_tokens` | `16384` | `2048` | From the HuggingFace agentic/tool-calling recommendation. Prevents truncated responses during multi-step tool chains where the model needs to emit several tool calls plus reasoning in a single turn. |

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
    │                   temperature=1.0,
    │                   top_p=0.95,
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
            └── OpenAI API call with temperature=1.0, top_p=0.95, max_tokens=16384
```

### Why Hardcoded, Not in Settings

These parameters are **model-level inference tuning**, not user-facing configuration:

1. **Coupled to the model** — temperature=1.0 is correct for GLM-4.7-Flash specifically. Exposing it in `settings.json` invites users to change it without understanding the model-specific calibration.
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

### `tests/test_agent.py`

| Test | What It Verifies |
|------|-----------------|
| `test_agent_initialization_gemini` | Returns `GoogleModel` with correct model name, `model_settings is None` |
| `test_agent_initialization_ollama` | Returns `OpenAIChatModel` with correct model name, `model_settings` has `temperature=1.0`, `top_p=0.95`, `max_tokens=16384` |

Both tests use `monkeypatch` to set env vars and reload settings — no mocks.

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/agent.py` | `get_agent()` factory — model selection + `ModelSettings` |
| `co_cli/main.py` | Unpacks `(agent, model_settings)`, passes settings to `agent.run()` |
| `co_cli/config.py` | `Settings` with LLM provider fields |
| `tests/test_agent.py` | Agent initialization tests for both providers |

---

## Future Considerations

| Enhancement | Description | Status |
|-------------|-------------|--------|
| Mode presets | `"precise"` / `"creative"` mode mapping to parameter bundles | Not planned |
| Per-model settings map | Dict of `{model_name: ModelSettings}` for multi-model support | Not planned |
| Streaming | `agent.run_stream()` with `model_settings` passthrough | Not planned |
| Ollama model auto-detection | Query `/api/tags` to validate model exists before first call | Not planned |
