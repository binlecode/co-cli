# LLM Models

## 1. What & How

Co CLI supports two providers (`ollama`, `gemini`) and one model-selection contract:
`model_roles` role chains.

- Main agent always uses `model_roles["reasoning"][0]`.
- On terminal model error, chat loop advances the same `reasoning` chain (drops failed head, retries once).
- Sub-agent tools use role-specific chains (`coding`, `research`, `analysis`) and take the head model.

```
get_agent(model_name=model_roles["reasoning"][0])
  ├── provider == "ollama"
  │   └── OpenAIProvider(base_url="{ollama_host}/v1") + OpenAIChatModel(model_name)
  └── provider == "gemini"
      └── model = "google-gla:{model_name}"
```

There is no separate primary/fallback settings tier.

## 2. Core Logic

### Provider Notes

#### Ollama

- Provider transport is OpenAI-compatible API at `{ollama_host}/v1`.
- Inference settings come from quirk profiles (`prompts/quirks/ollama/*.md`) by normalized model family.
- `/model` interactive switching remains Ollama-only and updates the `reasoning` role head in-session.
- Custom model profiles — build once, reference by name in `model_roles`:

  **Summarization** (`qwen3.5:35b-a3b-q4_k_m` base — 23 GB, very low temperature, bounded output):
  ```bash
  ollama create qwen3.5:35b-a3b-q4_k_m-summarize -f ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-summarize
  ```
  Set `CO_MODEL_ROLE_SUMMARIZATION=qwen3.5:35b-a3b-q4_k_m-summarize` or add to `settings.json`.

  > **Thinking mode:** `qwen3.5:35b-a3b-q4_k_m` has native thinking capability (Ollama reports
  > `capabilities: thinking`). By default it enters thinking mode and exhausts `num_predict` on
  > reasoning tokens before emitting visible output. Prompt-level directives (`/no_think`) are
  > ignored by this architecture. `summarize_messages` detects model names matching `qwen3.5` +
  > `summarize` and passes `extra_body={"think": False}` via Ollama's OpenAI-compat endpoint,
  > which is the only reliable disable mechanism. Validate with:
  > `uv run python scripts/validate_ollama_models.py`

  **Research sub-agent** (`qwen3:30b-a3b-thinking-2507-q4_k_m` base — 18 GB, non-thinking, web synthesis):
  ```bash
  ollama create qwen3:30b-research -f ollama/Modelfile.qwen3-30b-research
  ```
  Set `CO_MODEL_ROLE_RESEARCH=qwen3:30b-research` or add to `settings.json`.

  **Main reasoning agent** (`qwen3:30b-a3b-thinking-2507-q8_0` base — 32 GB, thinking, 128k context):
  ```bash
  ollama create qwen3:30b-a3b-thinking-2507-q8_0-agentic -f ollama/Modelfile.qwen3-30b-a3b-thinking-2507-q8_0-agentic
  ```
  Set `CO_MODEL_ROLE_REASONING=qwen3:30b-a3b-thinking-2507-q8_0-agentic` or add to `settings.json`.

  **Coder** (`qwen3-coder-next:q4_k_m` base — 51 GB, deterministic tooling params, large context):
  ```bash
  ollama create qwen3-coder-next:q4_k_m-code -f ollama/Modelfile.qwen3-coder-next-q4_k_m-code
  ```
  Set `CO_MODEL_ROLE_CODING=qwen3-coder-next:q4_k_m-code` or add to `settings.json`.

#### Gemini

- Provider uses model string `google-gla:{model_name}`.
- `GEMINI_API_KEY` is required when `LLM_PROVIDER=gemini`.
- Inference settings come from quirk profiles (`prompts/quirks/gemini/*.md`) with safe defaults when no profile exists.

### 2.1 Role Chains

`model_roles` is `dict[str, list[str]]`:

- Mandatory role: `reasoning` (`len >= 1` required by settings validation).
- Optional roles: `summarization`, `coding`, `research`, `analysis` (empty/missing disables that role).
- Order is preference order within the active provider.

Example:

```json
{
  "model_roles": {
    "reasoning": ["qwen3:30b-a3b-thinking-2507-q8_0-agentic", "qwen3-coder-next:q4_k_m-code"],
    "coding": ["qwen3-coder-next:q4_k_m-code"],
    "research": ["qwen3:30b-a3b-thinking-2507-q8_0-agentic"],
    "analysis": ["qwen3:30b-a3b-thinking-2507-q8_0-agentic"]
  }
}
```

### 2.2 Sub-agent Construction

Sub-agent model construction is provider-aware via `co_cli/agents/_factory.py`:

- `ollama` -> `OpenAIChatModel(model_name, OpenAIProvider(base_url="{ollama_host}/v1"))`
- `gemini` -> `"google-gla:{model_name}"`

Summarization uses `model_roles["summarization"]` head (resolved via `get_role_head()`). Falls back to the primary agent model when the role is empty.

## 3. Config

Settings load order is `env > .co-cli/settings.json > ~/.config/co-cli/settings.json > defaults`.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"ollama"` | Provider selection: `ollama` or `gemini` |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server base URL |
| `ollama_num_ctx` | `OLLAMA_NUM_CTX` | `262144` | Context size sent in request body |
| `ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn threshold for context ratio |
| `ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow threshold for context ratio |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Gemini API key |
| `model_roles["reasoning"]` | `CO_MODEL_ROLE_REASONING` | provider default | Mandatory main-agent model chain (comma-separated) |
| `model_roles["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | `[]` | Optional dedicated summarization model chain for `/compact` and history compaction |
| `model_roles["coding"]` | `CO_MODEL_ROLE_CODING` | `[]` | Optional coder sub-agent model chain |
| `model_roles["research"]` | `CO_MODEL_ROLE_RESEARCH` | `[]` | Optional research sub-agent model chain |
| `model_roles["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | `[]` | Optional analysis sub-agent model chain |

## 4. Files

| File | Purpose |
|------|---------|
| `co_cli/config.py` | `model_roles` setting, provider selection, Ollama/Gemini env var mappings |
| `co_cli/deps.py` | `CoDeps` fields: `model_roles`, `ollama_host`, `llm_provider` |
| `co_cli/_history.py` | `summarize_messages` — passes `think=False` for qwen3.5 summarize models |
| `co_cli/agents/_factory.py` | `make_subagent_model` — builds `OpenAIChatModel` for Ollama or bare string for Gemini |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-summarize` | Summarization model: `top_k 20`, `/no_think` in SYSTEM, `num_predict 2048` |
| `scripts/validate_ollama_models.py` | Validates all co-cli custom models: params + `/no_think` presence in baked system prompt |
