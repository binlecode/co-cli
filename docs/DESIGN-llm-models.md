# LLM Models

## 1. What & How

Co CLI supports two providers (`ollama`, `gemini`) and one model-selection contract:
`role_models` role chains. Main agent always uses `role_models["reasoning"][0]`. On terminal
model error, the chat loop advances the chain (drops failed head, retries with next). Sub-agent
tools take the head model from their role-specific chain.

```
get_agent(model_name=role_models["reasoning"][0])
  ├── provider == "ollama"
  │   └── OpenAIProvider(base_url="{ollama_host}/v1", api_key="ollama") + OpenAIChatModel(model_name)
  └── provider == "gemini"
      └── model = "google-gla:{model_name}"
```

There is no separate primary/fallback settings tier.

## 2. Core Logic

### Role Chains

`role_models` is `dict[str, list[ModelEntry]]`:

- Mandatory role: `reasoning` (`len >= 1` required by settings validation — raises `ValueError` at startup if absent or empty).
- Optional roles: `summarization`, `coding`, `research`, `analysis` (empty/missing disables that role).
- Order is preference order within the active provider.
- Each entry is a `ModelEntry(model, api_params)` — plain model name strings are coerced to `ModelEntry` by `_parse_role_models`.

Example:

```json
{
  "role_models": {
    "reasoning": ["qwen3:30b-q4_k_m-agentic", "qwen3.5:35b-a3b-q4_k_m-agentic"],
    "coding": ["qwen3-coder-next:q4_k_m-code"],
    "research": ["qwen3.5:35b-a3b-q4_k_m-research"],
    "summarization": ["qwen3.5:35b-a3b-q4_k_m-nothink"],
    "analysis": ["qwen3.5:35b-a3b-q4_k_m-nothink"]
  }
}
```

When `role_models` is not configured, provider defaults are injected for all roles:
- `gemini`: reasoning → `gemini-3-flash-preview`; all other roles empty (disabled)
- `ollama`: all five roles populated — reasoning → `qwen3:30b-q4_k_m-agentic`, then `qwen3.5:35b-a3b-q4_k_m-agentic`; summarization and analysis → `qwen3.5:35b-a3b-q4_k_m-nothink`; coding → `qwen3-coder-next:q4_k_m-code`; research → `qwen3.5:35b-a3b-q4_k_m-research` (with `think: false`)

### Sub-agent Construction

Sub-agent model construction is provider-aware via `co_cli/agents/_factory.py`:

- `ollama` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{ollama_host}/v1", api_key="ollama"))`
- `gemini` → `"google-gla:{model_name}"`
- Any other provider → `ValueError` raised.

Role head is resolved by indexing `role_models[role][0].model` — the first `ModelEntry` in the chain. Summarization falls back to the primary agent model when the role is absent or empty. The `_resolve_summarization_model(config, fallback)` helper in `_history.py` implements this fallback for the summarization role specifically.

### Model Dependency Checks

`_check_model_availability()` and `_check_llm_provider()` run via `run_model_check()` before the agent is created (after `create_deps()`, before `get_agent()`):

- `_check_llm_provider`: early-return guard structure (priority order):
  1. `gemini` + key absent → `status="error"` (cannot proceed)
  2. `gemini` + key present → `status="ok"`
  3. `ollama` + server unreachable (`/api/tags`, 5s timeout) → `status="warning"` (soft fail)
  4. non-Gemini provider + Gemini key absent → `status="warning"` (Gemini-dependent features unavailable)
  5. all checks pass → `status="ok"`
- `_check_model_availability`: Ollama-only. Queries `{ollama_host}/api/tags` (5s timeout). On any network error, returns `status="warning"` — soft fail.
- `reasoning` chain: if configured and no model in it is installed, returns `status="error"` → `run_model_check` raises `RuntimeError` (fail-fast, agent never created).
- If some reasoning models missing, filters chain to installed models (preserving order) → `status="warning"`, updated chains returned in `PreflightResult.role_models`; `run_model_check` applies mutation to `deps.config.role_models`.
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
| `role_models["reasoning"]` | `CO_MODEL_ROLE_REASONING` | provider default injected when absent | Mandatory main-agent model chain (comma-separated) |
| `role_models["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | `[]` | Optional dedicated summarization model chain for `/compact` and history compaction |
| `role_models["coding"]` | `CO_MODEL_ROLE_CODING` | `[]` | Optional coder sub-agent model chain |
| `role_models["research"]` | `CO_MODEL_ROLE_RESEARCH` | `[]` | Optional research sub-agent model chain |
| `role_models["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | `[]` | Optional analysis sub-agent model chain |

## 4. Provider Quirks

### Thinking-capable Models — `api_params` Override

Some Ollama MoE models (e.g. `qwen3.5`) have native thinking capability. By default they enter
thinking mode and exhaust `num_predict` on reasoning tokens before emitting visible output.
Prompt-level directives (`/no_think`) are ignored by this architecture.

To disable thinking, set `api_params: {think: false}` on the `ModelEntry` in `role_models`:

```
role_models:
  research:
    - model: qwen3.5:35b-a3b-q4_k_m-research
      api_params: {think: false}
```

For fixed non-thinking roles on Ollama, co-cli can also use baked `-nothink` Modelfile variants.
This avoids repeating `api_params: {think: false}` in config when the role should never think.

`make_subagent_model` in `_factory.py` bakes non-empty `api_params` into the `OpenAIChatModel`
constructor as `settings={"extra_body": api_params}` — applied to every request through that
model instance without any call-site changes. `resolve_role_model(config, role, fallback)` is
the single helper for looking up a role's head entry and constructing the model.

Important backend distinction:

- For Ollama-backed thinking models, the native request control is `think: false`. This is the
  correct setting for `role_models[*].api_params` in co-cli when `llm_provider=ollama`.
- For generic OpenAI-compatible Qwen3.5 servers outside Ollama, upstream Qwen documents a
  different control path: `chat_template_kwargs.enable_thinking=false` in the extra request body.
- So `api_params: {think: false}` in this repo is an Ollama-specific contract, not a universal
  Qwen/OpenAI-compatible API parameter.

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
| `co_cli/config.py` | `role_models` setting, `ModelEntry` class, `VALID_ROLE_NAMES`, provider selection, Ollama/Gemini env var mappings |
| `co_cli/deps.py` | `role_models`, `ollama_host`, `llm_provider` in `CoConfig` |
| `co_cli/_model_check.py` | `_check_llm_provider`, `_check_model_availability`, `run_model_check`, `PreflightResult` — pre-agent model dependency check gate |
| `co_cli/_commands.py` | `_swap_model_inplace`, `_switch_ollama_model` — in-place model swap used by error-recovery chain advancement in `main.py` |
| `co_cli/_history.py` | `summarize_messages`; `_resolve_summarization_model(config, fallback)` — thin wrapper around `resolve_role_model` for the summarization role |
| `co_cli/agents/_factory.py` | `make_subagent_model(model_entry, provider, ollama_host)` — builds `OpenAIChatModel` (baking `api_params` into construction-time `settings`) or bare string for Gemini; `resolve_role_model(config, role, fallback)` — looks up a role's head `ModelEntry` and constructs the model |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-nothink` | Shared general non-thinking model for fixed non-thinking roles such as summarization and analysis |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-research` | Focused research model with baked `/no_think` system prompt and bounded output |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-summarize` | Focused summarization model retained as a specialized non-thinking profile |
| `scripts/validate_ollama_models.py` | Standalone dev tool: validates shipped custom Ollama model tags against their baked Modelfile params and `/no_think` directives; not invoked at startup |

## 6. Testing Boundary

Model validation and config-role validation are intentionally split:

- `scripts/validate_ollama_models.py` is the runtime/deployment check for custom Ollama models.
  Its source of truth is the baked `ollama/Modelfile.*` files. It verifies that installed local
  Ollama tags exist and that their baked parameters and baked `/no_think` directives match the
  corresponding Modelfiles.
- pytest covers application behavior. Tests validate config parsing, default role injection,
  `Settings -> CoConfig` transfer, role resolution, reasoning-chain advance, summarization fallback,
  and delegation role selection.
- pytest is not the primary mechanism for checking whether a local Ollama tag is installed or whether
  a Modelfile was built with the expected parameters.
- Conversely, the validator script is not the source of truth for `role_models` defaults or role
  resolution logic. That contract belongs to `co_cli/config.py` and the pytest suite.
