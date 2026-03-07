# LLM Models

## 1. What & How

Co CLI supports two providers (`ollama`, `gemini`) and one model-selection contract:
`model_roles` role chains. Main agent always uses `model_roles["reasoning"][0]`. On terminal
model error, the chat loop advances the chain (drops failed head, retries with next). Sub-agent
tools take the head model from their role-specific chain.

```
get_agent(model_name=model_roles["reasoning"][0])
  ├── provider == "ollama"
  │   └── OpenAIProvider(base_url="{ollama_host}/v1", api_key="ollama") + OpenAIChatModel(model_name)
  └── provider == "gemini"
      └── model = "google-gla:{model_name}"
```

There is no separate primary/fallback settings tier.

## 2. Core Logic

### Role Chains

`model_roles` is `dict[str, list[str]]`:

- Mandatory role: `reasoning` (`len >= 1` required by settings validation — raises `ValueError` at startup if absent or empty).
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

When `model_roles` is not configured, the default reasoning chain is set from the active provider:
- `ollama`: `qwen3:30b-a3b-thinking-2507-q8_0-agentic`
- `gemini`: `gemini-3-flash-preview`

### Sub-agent Construction

Sub-agent model construction is provider-aware via `co_cli/agents/_factory.py`:

- `ollama` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{ollama_host}/v1", api_key="ollama"))`
- `gemini` → `"google-gla:{model_name}"`
- Any other provider → `ValueError` raised.

Role head is resolved via `get_role_head(model_roles, role)` — returns the first element of the chain, or empty string if the role is absent or empty. Summarization falls back to the primary agent model when the role is empty.

### Preflight Checks

`_check_model_availability()` and `_check_llm_provider()` run via `run_preflight()` before the agent is created (after `create_deps()`, before `get_agent()`):

- `_check_llm_provider`: early-return guard structure (priority order):
  1. `gemini` + key absent → `status="error"` (cannot proceed)
  2. `gemini` + key present → `status="ok"`
  3. `ollama` + server unreachable (`/api/tags`, 5s timeout) → `status="warning"` (soft fail)
  4. non-Gemini provider + Gemini key absent → `status="warning"` (Gemini-dependent features unavailable)
  5. all checks pass → `status="ok"`
- `_check_model_availability`: Ollama-only. Queries `{ollama_host}/api/tags` (5s timeout). On any network error, returns `status="warning"` — soft fail.
- `reasoning` chain: if configured and no model in it is installed, returns `status="error"` → `run_preflight` raises `RuntimeError` (fail-fast, agent never created).
- If some reasoning models missing, filters chain to installed models (preserving order) → `status="warning"`, updated chains returned in `PreflightResult.model_roles`; `run_preflight` applies mutation to `deps.model_roles`.
- Optional roles (`summarization`, `coding`, `research`, `analysis`): filtered to installed models (preserving order). Role disabled (set to `[]`) if none found. No error raised.
- Non-Ollama provider (e.g. Gemini): returns `status="ok"` immediately — no model list probe.

## 3. Config

Settings load order is `env > .co-cli/settings.json > ~/.config/co-cli/settings.json > defaults`.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"ollama"` | Provider selection: `ollama` or `gemini` |
| `ollama_host` | `OLLAMA_HOST` | `"http://localhost:11434"` | Ollama server base URL |
| `ollama_num_ctx` | `OLLAMA_NUM_CTX` | `262144` | Context size hint (ignored by Ollama API — set in Modelfile) |
| `ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn threshold for context ratio |
| `ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow threshold for context ratio |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Gemini API key (required when `llm_provider=gemini`) |
| `model_roles["reasoning"]` | `CO_MODEL_ROLE_REASONING` | `{}` then provider default injected when absent | Mandatory main-agent model chain (comma-separated) |
| `model_roles["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | `[]` | Optional dedicated summarization model chain for `/compact` and history compaction |
| `model_roles["coding"]` | `CO_MODEL_ROLE_CODING` | `[]` | Optional coder sub-agent model chain |
| `model_roles["research"]` | `CO_MODEL_ROLE_RESEARCH` | `[]` | Optional research sub-agent model chain |
| `model_roles["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | `[]` | Optional analysis sub-agent model chain |

## 4. Provider Quirks

### qwen3.5 Summarization — Mandatory `think=False`

`qwen3.5` MoE models have native thinking capability (Ollama reports `capabilities: thinking`). By
default they enter thinking mode and exhaust `num_predict` on reasoning tokens before emitting
visible output. Prompt-level directives (`/no_think`) are ignored by this architecture.

`summarize_messages` in `_history.py` detects model instances where `isinstance(model, OpenAIChatModel)`
and **both** `"qwen3.5"` and `"summarize"` appear in `model.model_name.lower()` (the `.model_name`
string attribute of the `OpenAIChatModel` object), and passes `extra_body={"think": False}` via
Ollama's OpenAI-compat endpoint — the only reliable disable mechanism. Other Ollama models are not
affected.

Validate with: `uv run python scripts/validate_ollama_models.py`

### Ollama `num_ctx` — Must Be Baked Into Modelfile

Ollama's OpenAI-compatible API silently ignores `num_ctx` sent in request parameters (upstream
issue ollama/ollama#5356). Base model tags default to 4096 tokens and silently truncate multi-turn
conversation history.

For models requiring a larger context window, `num_ctx` must be set via `PARAMETER num_ctx` in the
Modelfile. The `-agentic` Modelfile variants in `ollama/` include this parameter. The `ollama_num_ctx`
setting is forwarded as a client-side hint for future-proofing but is not enforced by Ollama today.

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/config.py` | `model_roles` setting, provider selection, Ollama/Gemini env var mappings, `get_role_head()` |
| `co_cli/deps.py` | `CoDeps` fields: `model_roles`, `ollama_host`, `llm_provider` |
| `co_cli/_preflight.py` | `_check_llm_provider`, `_check_model_availability`, `run_preflight`, `PreflightResult` — pre-agent resource gate and preflight checks |
| `co_cli/_commands.py` | `_swap_model_inplace`, `_switch_ollama_model` — in-place model swap used by error-recovery chain advancement in `main.py` |
| `co_cli/_history.py` | `summarize_messages` — passes `think=False` for qwen3.5 summarize models |
| `co_cli/agents/_factory.py` | `make_subagent_model` — builds `OpenAIChatModel` for Ollama or bare string for Gemini |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-summarize` | Summarization model: `top_k 20`, `num_predict 2048`, `num_ctx 32768`, `/no_think` in SYSTEM |
| `scripts/validate_ollama_models.py` | Standalone dev tool: validates all role model params (reasoning, summarization, coding, research) + `/no_think` presence in baked system prompt; not invoked at startup |
