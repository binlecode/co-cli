# LLM Models

## 1. What & How

Co CLI supports three providers (`ollama-openai`, `ollama-native`, `gemini`) and one model-selection contract:
`role_models` — one `ModelEntry` per role. The main agent is created with `model=None` (per-call model passing);
`run_turn_with_fallback()` resolves `role_models["reasoning"]` from `ModelRegistry` before each turn.
Sub-agent tools use a pre-built `ResolvedModel` looked up from `ModelRegistry` by role.

```
ModelRegistry.from_config(config) → session-scoped registry
  for each role in config.role_models:
    build_model(role_models[role], provider, llm_host)
      ├── provider == "ollama-openai"
      │   └── OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama") + OpenAIChatModel
      │       (num_ctx baked from model inference metadata when available)
      ├── provider == "ollama-native"
      │   └── OllamaNativeModel using Ollama's native /api/chat endpoint
      │       (exposes top-level `think` field for call-time reasoning control)
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
    "research": "qwen3.5:35b-a3b-instruct",
    "summarization": {"model": "qwen3.5:35b-a3b-think", "provider": "ollama-native", "api_params": {"think": false}},
    "analysis": "qwen3.5:35b-a3b-instruct"
  }
}
```

When `role_models` is not configured, provider defaults are injected for all roles:
- `gemini`: reasoning → `gemini-3-flash-preview`; all other roles empty (disabled)
- `ollama-openai` / `ollama-native`: all five roles populated — reasoning → `qwen3.5:35b-a3b-think`; summarization → `qwen3.5:35b-a3b-think` (via `ollama-native`, `think=False`); analysis → `qwen3.5:35b-a3b-instruct`; coding → `qwen3.5:35b-a3b-code`; research → `qwen3.5:35b-a3b-instruct` with `think=False`

### ModelRegistry and ResolvedModel

`ModelRegistry` is a session-scoped registry of pre-built `ResolvedModel` objects keyed by role. It is built once from `CoConfig` at session start via `ModelRegistry.from_config(config)` and stored on `CoServices.model_registry`. All components look up models by role using `registry.get(role, fallback)` at runtime.

`ResolvedModel` is a dataclass pairing a pre-built model object (`Any`) with its `ModelSettings | None`. Agent factories and summarization functions receive a `ResolvedModel` directly.

Sub-agent model construction is provider-aware via `build_model()` in `co_cli/_model_factory.py`:

- `ollama-openai` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama"))`
- `ollama-native` → `OllamaNativeModel(model_name, llm_host)` using Ollama's `/api/chat` endpoint
- `gemini` → `GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))` — key injected directly, no env mutation
- Any other provider → `ValueError` raised.

Roles are resolved at registry build time by iterating `role_models`. Empty or absent roles are skipped (not registered). Delegation tools guard with `registry.is_configured(role)` before calling `registry.get(role, fallback)`. Summarization and compaction pass the main agent's model as the `fallback` argument so absent roles degrade gracefully.

### Model Dependency Checks

`check_llm()` runs inside `create_deps()` (before `build_agent()`) as a single fail-fast gate. Error status raises `ValueError` immediately — session never starts.

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
| `llm_provider` | `LLM_PROVIDER` | `"ollama-openai"` | Provider selection: `ollama-openai`, `ollama-native`, or `gemini` |
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

Some Ollama MoE models (e.g. `qwen3.5`) have native thinking capability. By default they enter
thinking mode and exhaust `num_predict` on reasoning tokens before emitting visible output.

Three mechanisms exist to suppress thinking, in order of preference:

1. **Native instruct variant** — use a dedicated instruct model (e.g. `qwen3:30b-a3b-instruct-2507`) where thinking is disabled at the model level. No config needed. Preferred for fixed non-thinking roles.
2. **`api_params: {think: false}`** — request-level override on any `ModelEntry`. Use when pointing a non-thinking role at a thinking model without a dedicated instruct variant:

```
role_models:
  summarization:
    - model: qwen3.5:35b-a3b-agentic
      api_params: {think: false}
```

3. **`/no_think` SYSTEM directive** — baked into a Modelfile (e.g. the `-nothink` backup). Works for `qwen3` thinking models; `qwen3.5` ignores prompt-level directives and requires `api_params` instead.

`build_model()` in `_model_factory.py` bakes non-empty `api_params` into the `ModelSettings.extra_body`
returned alongside the model — applied to every request through the `ResolvedModel` at call sites.
`ModelRegistry.from_config(config)` is the single entry point for constructing all role models
at session startup. Individual consumers call `registry.get(role, fallback)` to retrieve a
`ResolvedModel` without any per-call construction overhead.

Important backend distinction:

- For Ollama-backed thinking models, the native request control is `think: false`. This is the
  correct setting for `role_models[*].api_params` in co-cli when `llm_provider=ollama-openai` or `llm_provider=ollama-native`.
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
Modelfile. All custom Modelfiles in `ollama/` bake this parameter via `PARAMETER num_ctx`. The `llm_num_ctx` setting is
forwarded as a client-side hint for future-proofing but is not enforced by Ollama today.

### Modelfile Parameters Reference

All custom Ollama model tags in `ollama/` and their baked parameters:

| Modelfile | Role | num_ctx | num_predict | temp | top_p | top_k | presence_p | repeat_p |
|-----------|------|--------:|------------:|-----:|------:|------:|-----------:|---------:|
| `qwen3.5-35b-a3b-think` | reasoning | 128K | 32K | 1.0 | 0.95 | 20 | 1.5 | 1.0 |
| `qwen3.5-35b-a3b-instruct` | summarization, analysis, research | 128K | 16K | 0.7 | 0.8 | 20 | — | 1.0 |
| `qwen3.5-35b-a3b-code` | coding | 64K | 32K | 0.6 | 0.8 | 20 | — | 1.05 |

**num_ctx rationale:**
- Agentic/thinking models: 128K — sweet spot for long multi-turn tool workflows without the KV cache cost of 256K. Native context is 256K; reduce to 32K if OOM.
- Coder model: 64K — 51GB model weights leave limited headroom; 64K fits large multi-file edits without memory pressure.

**num_predict rationale:**
- Thinking models (32K): chain-of-thought `<think>` tokens consume output budget before the visible answer; 32K prevents truncation.
- Instruct/nothink models (16K): no thinking tokens, direct response only; 16K is ample and avoids wasteful allocation.
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
| `co_cli/bootstrap/_check.py` | `check_llm` (provider credentials + model availability) and other integration probes — shared factual probe layer |
| `co_cli/agent.py` | `build_agent()` factory — model selection, tool registration, system prompt assembly |
| `co_cli/commands/_commands.py` | Uses `registry.get(ROLE_SUMMARIZATION, fallback)` for `/compact` and `/new` |
| `co_cli/context/_history.py` | `summarize_messages(messages, resolved_model, ...)` — bare Agent summariser; `truncate_history_window` uses `registry.get("summarization", fallback)` for inline compaction; `precompute_compaction` does the same for background pre-computation |
| `co_cli/_model_factory.py` | `ResolvedModel` — pre-built model + settings pair; `ModelRegistry` — session-scoped registry built via `ModelRegistry.from_config(config)`; `build_model(model_entry, provider, llm_host, api_key)` — builds provider-aware model and `ModelSettings`; `prepare_provider(provider, llm_api_key)` — provider-level credential validation (Gemini API key guard, no env mutation) called at `build_agent()` startup |
| `ollama/Modelfile.qwen3.5-35b-a3b-think` | Primary reasoning model — thinking enabled |
| `ollama/Modelfile.qwen3.5-35b-a3b-instruct` | Summarization/analysis/research — native instruct (thinking off at model level) |
| `ollama/Modelfile.qwen3.5-35b-a3b-code` | Coding sub-agent — deterministic params |
| `scripts/validate_ollama_models.py` | Standalone dev tool: validates shipped custom Ollama model tags against their baked Modelfile params and `/no_think` directives; not invoked at startup |

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
