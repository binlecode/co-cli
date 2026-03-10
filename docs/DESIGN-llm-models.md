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
    "reasoning": ["qwen3.5:35b-a3b-q4_k_m-agentic", "qwen3:30b-a3b-thinking-2507-q4_k_m-agentic"],
    "coding": ["qwen3-coder-next:q4_k_m-code"],
    "research": ["qwen3.5:35b-a3b-q4_k_m-agentic"],
    "summarization": ["qwen3:30b-a3b-instruct-2507-q4_k_m"],
    "analysis": ["qwen3:30b-a3b-instruct-2507-q4_k_m"]
  }
}
```

When `role_models` is not configured, provider defaults are injected for all roles:
- `gemini`: reasoning → `gemini-3-flash-preview`; all other roles empty (disabled)
- `ollama`: all five roles populated — reasoning → `qwen3.5:35b-a3b-q4_k_m-agentic`, then `qwen3:30b-a3b-thinking-2507-q4_k_m-agentic`; summarization and analysis → `qwen3:30b-a3b-instruct-2507-q4_k_m`; coding → `qwen3-coder-next:q4_k_m-code`; research → `qwen3.5:35b-a3b-q4_k_m-agentic`

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

Three mechanisms exist to suppress thinking, in order of preference:

1. **Native instruct variant** — use a dedicated instruct model (e.g. `qwen3:30b-a3b-instruct-2507-q4_k_m`) where thinking is disabled at the model level. No config needed. Preferred for fixed non-thinking roles.
2. **`api_params: {think: false}`** — request-level override on any `ModelEntry`. Use when pointing a non-thinking role at a thinking model without a dedicated instruct variant:

```
role_models:
  summarization:
    - model: qwen3.5:35b-a3b-q4_k_m-agentic
      api_params: {think: false}
```

3. **`/no_think` SYSTEM directive** — baked into a Modelfile (e.g. the `-nothink` backup). Works for `qwen3` thinking models; `qwen3.5` ignores prompt-level directives and requires `api_params` instead.

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
Modelfile. All custom Modelfiles in `ollama/` bake this parameter. The `ollama_num_ctx` setting is
forwarded as a client-side hint for future-proofing but is not enforced by Ollama today.

### Modelfile Parameters Reference

All custom Ollama model tags in `ollama/` and their baked parameters:

| Modelfile | Role | num_ctx | num_predict | temp | top_p | top_k | presence_p | repeat_p |
|-----------|------|--------:|------------:|-----:|------:|------:|-----------:|---------:|
| `qwen3.5-35b-a3b-q4_k_m-agentic` | reasoning, research | 128K | 32K | 1.0 | 0.95 | 20 | 1.5 | 1.0 |
| `qwen3-30b-a3b-thinking-2507-q4_k_m-agentic` | reasoning (fallback) | 128K | 32K | 0.6 | 0.95 | 20 | — | 1.0 |
| `qwen3-30b-a3b-thinking-2507-q4_k_m-nothink` | backup only | 128K | 16K | 0.7 | 0.8 | 20 | — | 1.0 |
| `qwen3-30b-a3b-instruct-2507-q4_k_m` | summarization, analysis | 128K | 16K | 0.7 | 0.8 | 20 | — | 1.0 |
| `qwen3-coder-next-q4_k_m-code` | coding | 64K | 32K | 0.6 | 0.8 | 20 | — | 1.05 |

**num_ctx rationale:**
- Agentic/thinking models: 128K — sweet spot for long multi-turn tool workflows without the KV cache cost of 256K. Native context is 256K; reduce to 32K if OOM.
- Coder model: 64K — 51GB model weights leave limited headroom; 64K fits large multi-file edits without memory pressure.

**num_predict rationale:**
- Thinking models (32K): chain-of-thought `<think>` tokens consume output budget before the visible answer; 32K prevents truncation.
- Instruct/nothink models (16K): no thinking tokens, direct response only; 16K is ample and avoids wasteful allocation.
- Coder model (32K): 50/50 split with num_ctx — 32K output budget, 32K input headroom for prompt + tool context.

**Sampling params rationale:**
- `qwen3.5` thinking mode uses `temperature 1.0` (official Qwen3.5 guidance) — distinct from `qwen3` thinking mode which uses `0.6`.
- `presence_penalty 1.5` on the 35b model: official Qwen3.5 recommendation to suppress repetitions in long thinking traces.
- `repeat_penalty 1.05` on the coder: slightly above 1.0 to discourage repetitive boilerplate in generated code.

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/config.py` | `role_models` setting, `ModelEntry` class, `VALID_ROLE_NAMES`, provider selection, Ollama/Gemini env var mappings |
| `co_cli/deps.py` | `role_models`, `ollama_host`, `llm_provider` in `CoConfig` |
| `co_cli/_model_check.py` | `_check_llm_provider`, `_check_model_availability`, `run_model_check`, `PreflightResult` — pre-agent model dependency check gate |
| `co_cli/_commands.py` | `_swap_model_inplace`, `_switch_ollama_model` — in-place model swap used by error-recovery chain advancement in `main.py` |
| `co_cli/_history.py` | `summarize_messages`; `_resolve_summarization_model(config, fallback)` — thin wrapper around `resolve_role_model` for the summarization role |
| `co_cli/agents/_factory.py` | `make_subagent_model(model_entry, provider, ollama_host)` — builds `OpenAIChatModel` (baking `api_params` into construction-time `settings`) or bare string for Gemini; `resolve_role_model(config, role, fallback)` — looks up a role's head `ModelEntry` and constructs the model |
| `ollama/Modelfile.qwen3.5-35b-a3b-q4_k_m-agentic` | Primary reasoning/research model — thinking enabled, 128K ctx |
| `ollama/Modelfile.qwen3-30b-a3b-thinking-2507-q4_k_m-agentic` | Reasoning fallback — thinking enabled, 128K ctx |
| `ollama/Modelfile.qwen3-30b-a3b-thinking-2507-q4_k_m-nothink` | Backup nothink variant — `/no_think` SYSTEM directive, 128K ctx |
| `ollama/Modelfile.qwen3-30b-a3b-instruct-2507-q4_k_m` | Summarization/analysis — native instruct (thinking off at model level), 128K ctx |
| `ollama/Modelfile.qwen3-coder-next-q4_k_m-code` | Coding sub-agent — deterministic params, 64K ctx / 32K output |
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
