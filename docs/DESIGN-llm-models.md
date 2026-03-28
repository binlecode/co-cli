# LLM Models

## 1. What & How

Co CLI supports two providers (`ollama-openai`, `gemini`) and one model-selection contract:
`role_models` — one `ModelEntry` per role. The `reasoning` model is resolved once in `_chat_loop()` at
session start via `ModelRegistry`; `build_agent()` is called once with the resolved model. `run_turn()`
executes turns against that pre-built agent.
Sub-agent tools use a pre-built `ResolvedModel` looked up from `ModelRegistry` by role.

```
ModelRegistry.from_config(config) → session-scoped registry
  for each role in config.role_models:
    build_model(role_models[role], provider, llm_host)
      ├── provider == "ollama-openai"
      │   └── OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama") + OpenAIChatModel
      │       (num_ctx baked from model inference metadata when available)
      └── provider == "gemini"
          └── GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
    → ResolvedModel(model, settings) stored in registry

registry.get(role, fallback) → ResolvedModel
registry.is_configured(role) → bool
```

There is no separate primary/fallback settings tier.

## 2. Core Logic

### Role Models

`role_models` is `dict[str, ModelEntry]` — one model per role:

- Mandatory role: `reasoning` (a single `ModelEntry` required — raises `ValueError` at startup if absent).
- Optional roles: `summarization`, `coding`, `research`, `analysis` (absent/missing disables that role).
- Each entry is a `ModelEntry(model, api_params, provider?)` — plain model name strings are coerced to `ModelEntry` by `_parse_role_models`. When supplied as a list in JSON, only the first element is used (backwards-compat).

Example:

```json
{
  "role_models": {
    "reasoning": "qwen3.5:35b-a3b-agentic",
    "coding": "qwen3-coder-next:code",
    "research": {"model": "qwen3.5:35b-a3b-think", "provider": "ollama-openai", "api_params": {"reasoning_effort": "none"}},
    "summarization": {"model": "qwen3.5:35b-a3b-think", "provider": "ollama-openai", "api_params": {"reasoning_effort": "none"}},
    "analysis": {"model": "qwen3.5:35b-a3b-think", "provider": "ollama-openai", "api_params": {"reasoning_effort": "none"}}
  }
}
```

When `role_models` is not configured, provider defaults are injected for all roles:
- `gemini`: reasoning → `gemini-3-flash-preview`; all other roles empty (disabled)
- `ollama-openai`: all five roles populated — reasoning → `qwen3.5:35b-a3b-think`; summarization/analysis/research → `qwen3.5:35b-a3b-think` with `reasoning_effort="none"`; coding → `qwen3.5:35b-a3b-code`

### ModelRegistry and ResolvedModel

`ModelRegistry` is a session-scoped registry of pre-built `ResolvedModel` objects keyed by role. It is built once from `CoConfig` at session start via `ModelRegistry.from_config(config)` and stored on `CoServices.model_registry`. All components look up models by role using `registry.get(role, fallback)` at runtime.

`ResolvedModel` is a dataclass pairing a pre-built model object (`Any`) with its `ModelSettings | None`. Agent factories and summarization functions receive a `ResolvedModel` directly.

Sub-agent model construction is provider-aware via `build_model()` in `co_cli/_model_factory.py`:

- `ollama-openai` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama"))`
- `gemini` → `GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))` — key injected directly, no env mutation
- Any other provider → `ValueError` raised.

Roles are resolved at registry build time by iterating `role_models`. Empty or absent roles are skipped (not registered). Delegation tools guard with `registry.is_configured(role)` before calling `registry.get(role, fallback)`. Summarization and compaction pass the main agent's model as the `fallback` argument so absent roles degrade gracefully.

### Model Dependency Checks

`check_agent_llm()` runs inside `create_deps()` (before `build_agent()`) as a single fail-fast gate. Error status raises `ValueError` immediately — session never starts.

- `gemini` + key absent → `status="error"` (cannot proceed)
- `gemini` + key present → `status="ok"` (no HTTP call)
- Ollama: one `GET {llm_host}/api/tags` (5s timeout) checks both reachability and model availability:
  - server unreachable → `status="warn"` (soft fail, session continues)
  - `reasoning` model not installed → `status="error"` → `create_deps()` raises `ValueError` (fail-fast)
  - optional role model not installed → `status="warn"`, session continues
  - all models present → `status="ok"`

## 3. Config

Settings load order is `env > .co-cli/settings.json > ~/.config/co-cli/settings.json > defaults`.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm_provider` | `LLM_PROVIDER` | `"ollama-openai"` | Provider selection: `ollama-openai` or `gemini` |
| `llm_host` | `LLM_HOST` | `"http://localhost:11434"` | LLM server base URL (Ollama or compatible) |
| `llm_num_ctx` | `LLM_NUM_CTX` | `262144` | Context size hint (ignored by Ollama API — set in Modelfile) |
| `ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn threshold for context ratio |
| `ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow threshold for context ratio |
| `llm_api_key` | `LLM_API_KEY` | `None` | LLM API key (required when `llm_provider=gemini`) |
| `role_models["reasoning"]` | `CO_MODEL_ROLE_REASONING` | provider default injected when absent | Mandatory main-agent model chain (comma-separated) |
| `role_models["summarization"]` | `CO_MODEL_ROLE_SUMMARIZATION` | provider default when absent | Optional dedicated summarization model chain for `/compact` and history compaction |
| `role_models["coding"]` | `CO_MODEL_ROLE_CODING` | provider default when absent | Optional coder sub-agent model chain |
| `role_models["research"]` | `CO_MODEL_ROLE_RESEARCH` | provider default when absent | Optional research sub-agent model chain |
| `role_models["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | provider default when absent | Optional analysis sub-agent model chain |

## 4. Provider Quirks

### Thinking-capable Models — `api_params` Override

Some Ollama MoE models, including `qwen3.5:35b-a3b-think`, default to reasoning mode. For
non-reasoning work such as summarization, analysis, and research, the repo does not swap to a
second `-instruct` model. It keeps the same `-think` weights resident and changes the request body.

Supported non-reason path for this repo:

- provider: `ollama-openai`
- model: `qwen3.5:35b-a3b-think`
- request override: `api_params.reasoning_effort = "none"`

Effective default shape:

```yaml
role_models:
  summarization:
    model: qwen3.5:35b-a3b-think
    provider: ollama-openai
    api_params:
      reasoning_effort: none
      temperature: 0.7
      top_p: 0.8
      max_tokens: 16384
      top_k: 20
      min_p: 0.0
      presence_penalty: 1.5
      repeat_penalty: 1.0
      num_ctx: 131072
      num_predict: 16384
```

Application flow:

```text
Settings/config.py
  default summarization/analysis/research entry
    -> ModelEntry(model="qwen3.5:35b-a3b-think", api_params={... reasoning_effort="none" ...})

ModelRegistry.from_config(config)
  -> build_model(model_entry, provider="ollama-openai", llm_host)

build_model()
  quirks defaults + quirks extra_body + model_entry.api_params
    -> ModelSettings fields:
         temperature / top_p / max_tokens
    -> ModelSettings.extra_body:
         reasoning_effort / top_k / min_p / presence_penalty /
         repeat_penalty / num_ctx / num_predict

ResolvedModel(model, settings)
  -> registry.get("summarization", fallback)
  -> pydantic-ai request
  -> POST {llm_host}/v1/chat/completions with extra_body merged into the API call
```

Practical rule:

- Use `api_params` when a role should stay on the `-think` model but behave like a direct-answer,
  non-reasoning call.
- Put request-body controls such as `reasoning_effort`, `top_k`, `presence_penalty`, or
  `num_predict` in `api_params`; for Ollama OpenAI-compatible models they are forwarded through
  `ModelSettings.extra_body`.
- Put standard sampling controls `temperature`, `top_p`, and `max_tokens` in `api_params` when a
  role needs different values from the model-quirk defaults; `build_model()` lifts only those keys
  into first-class `ModelSettings` fields.

Observed behavior behind this design:

- default `qwen3.5:35b-a3b-think` can spend its output budget on reasoning and return empty visible
  `content`
- `qwen3.5:35b-a3b-think` with `reasoning_effort="none"` returns direct-answer content on the same
  resident model

Important backend distinction:

- For the active Ollama OpenAI-compatible path in this repo, `reasoning_effort="none"` is the
  supported request-level control for suppressing reasoning on `qwen3.5:35b-a3b-think`.
- For generic OpenAI-compatible Qwen3.5 servers outside Ollama, upstream Qwen documents a different
  control path: `chat_template_kwargs.enable_thinking=false` in the extra request body.
- These control surfaces are not interchangeable; this repo documents and relies on the Ollama one.

Validate with: `uv run python scripts/validate_ollama_models.py`

### Ollama `num_ctx` — Must Be Baked Into Modelfile

Ollama's OpenAI-compatible API silently ignores `num_ctx` sent in request parameters (upstream
issue ollama/ollama#5356). Base model tags default to 4096 tokens and silently truncate multi-turn
conversation history.

For models requiring a larger context window, `num_ctx` must be set via `PARAMETER num_ctx` in the
Modelfile. All custom Modelfiles in `ollama/` bake this parameter via `PARAMETER num_ctx`. The `llm_num_ctx` setting is
forwarded as a client-side hint for future-proofing but is not enforced by Ollama today.

### Modelfile Parameters Reference

All custom Ollama model tags in `ollama/` and their baked parameters:

| Modelfile | Role | num_ctx | num_predict | temp | top_p | top_k | presence_p | repeat_p |
|-----------|------|--------:|------------:|-----:|------:|------:|-----------:|---------:|
| `qwen3.5-35b-a3b-think` | reasoning | 128K | 32K | 1.0 | 0.95 | 20 | 1.5 | 1.0 |
| `qwen3.5-35b-a3b-code` | coding | 64K | 32K | 0.6 | 0.8 | 20 | — | 1.05 |

**num_ctx rationale:**
- Agentic/thinking models: 128K — sweet spot for long multi-turn tool workflows without the KV cache cost of 256K. Native context is 256K; reduce to 32K if OOM.
- Coder model: 64K — 51GB model weights leave limited headroom; 64K fits large multi-file edits without memory pressure.

**num_predict rationale:**
- Thinking models (32K): chain-of-thought `<think>` tokens consume output budget before the visible answer; 32K prevents truncation.
- Non-reason calls on the same think weights use request-level `reasoning_effort="none"` instead of a separate instruct tag.
- Coder model (32K): 50/50 split with num_ctx — 32K output budget, 32K input headroom for prompt + tool context.

**Sampling params rationale:**
- `qwen3.5` thinking mode uses `temperature 1.0` (official Qwen3.5 guidance).
- `presence_penalty 1.5` on the think model: official Qwen3.5 recommendation to suppress repetitions in long thinking traces.
- `repeat_penalty 1.05` on the coder: slightly above 1.0 to discourage repetitive boilerplate in generated code.

## 5. Files

| File | Purpose |
|------|---------|
| `co_cli/config.py` | `role_models` setting, `ModelEntry` class, `VALID_ROLE_NAMES`, provider selection, Ollama/Gemini env var mappings |
| `co_cli/deps.py` | `role_models`, `llm_host`, `llm_provider`, `model_http_retries` in `CoConfig`; `model_registry` in `CoServices` |
| `co_cli/bootstrap/_check.py` | `check_agent_llm` (provider credentials + model availability) and other integration probes — shared factual probe layer |
| `co_cli/agent.py` | `build_agent()` factory — model selection, tool registration, system prompt assembly |
| `co_cli/commands/_commands.py` | Uses `registry.get(ROLE_SUMMARIZATION, fallback)` for `/compact` and `/new` |
| `co_cli/context/_history.py` | `summarize_messages(messages, resolved_model, ...)` — bare Agent summariser; `truncate_history_window` uses `registry.get("summarization", fallback)` for inline compaction; `precompute_compaction` does the same for background pre-computation |
| `co_cli/_model_factory.py` | `ResolvedModel` — pre-built model + settings pair; `ModelRegistry` — session-scoped registry built via `ModelRegistry.from_config(config)`; `build_model(model_entry, provider, llm_host, api_key)` — builds provider-aware model and `ModelSettings` |
| `ollama/Modelfile.qwen3.5-35b-a3b-think` | Primary reasoning model — thinking enabled |
| `ollama/Modelfile.qwen3.5-35b-a3b-code` | Coding sub-agent — deterministic params |
| `scripts/validate_ollama_models.py` | Standalone dev tool: validates shipped custom Ollama model tags against their baked Modelfile params and directives; not invoked at startup |

## 6. Testing Boundary

Model validation and config-role validation are intentionally split:

- `scripts/validate_ollama_models.py` is the runtime/deployment check for custom Ollama models.
  Its source of truth is the baked `ollama/Modelfile.*` files. It verifies that installed local
  Ollama tags exist and that their baked parameters and baked `/no_think` directives match the
  corresponding Modelfiles.
- pytest covers application behavior. Tests validate config parsing, default role injection,
  `Settings -> CoConfig` transfer, role resolution, summarization fallback,
  and delegation role selection.
- pytest is not the primary mechanism for checking whether a local Ollama tag is installed or whether
  a Modelfile was built with the expected parameters.
- Conversely, the validator script is not the source of truth for `role_models` defaults or role
  resolution logic. That contract belongs to `co_cli/config.py` and the pytest suite.
