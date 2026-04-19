# LLM Models

## Product Intent

**Goal:** Define the single-model architecture, provider abstraction, and per-call settings resolution.
**Functional areas:**
- Model factory (`build_model`) and provider selection
- Explicit reasoning settings resolution plus provider-aware noreason settings resolved at build time
- Provider-specific runtime settings (Ollama `num_ctx`, Gemini defaults)
- Model dependency checks at startup

**Non-goals:**
- Multi-model routing
- Model-level caching
- Streaming token budgets

**Success criteria:** Single model used for all tasks; reasoning suppressed on non-reasoning calls; Ollama `num_ctx` baked at Modelfile level.
**Status:** Stable

---

## 1. What & How

Co CLI supports two providers (`ollama-openai`, `gemini`) and a single-model architecture:
`llm.model` names the one model used for all tasks. `create_deps()` calls
`build_model(config.llm)` once to produce an `LlmModel` (model object + reasoning settings +
noreason settings + context window), stored on `CoDeps.model`. The main agent uses
config-derived reasoning settings from `deps.model.settings`. Functional (non-tool-loop) calls
— summarizer, dream merge — use `deps.model.settings_noreason` via the `llm_call()` primitive.

```
build_model(config.llm) → LlmModel
    ├── provider == "ollama-openai"
    │   └── OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama") + OpenAIChatModel
    │       (num_ctx baked from resolved model settings when available)
    └── provider == "gemini"
        └── GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
    → LlmModel(model, settings, settings_noreason, context_window)

deps.model → LlmModel (session-scoped, read-only)
```


## 2. Core Logic

### Single Model, Per-Call Settings

`llm.model` is a single string naming the model used for all tasks — reasoning, subagents, compaction, and memory extraction.

Example:

```json
{
  "llm": {
    "provider": "ollama-openai",
    "model": "qwen3.5:35b-a3b-think"
  }
}
```

All subagent tools are always registered.

### LlmModel and build_model

`LlmModel` is a dataclass pairing a pre-built model object (`Any`) with its reasoning `ModelSettings | None` (`settings`), its noreason `ModelSettings | None` (`settings_noreason`), and an optional `context_window: int | None` from resolved model settings. It is built once at session start via `build_model(config.llm)` and stored on `deps.model`.

`build_model()` in `co_cli/llm/_factory.py` is provider-aware:

- `ollama-openai` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama"))`
- `gemini` → `GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))` — key injected directly, no env mutation
- Any other provider → `ValueError` raised.

`config._llm` provides provider and model defaults for inference settings (temperature, top_p, max_tokens, extra_body, context_window). `resolve_reasoning_inference()` layers explicit `llm.reasoning` fields on top of those defaults, and `LlmSettings.reasoning_model_settings()` converts the resolved values into the base `ModelSettings` stored on `LlmModel.settings`. `resolve_compaction_budget(config, context_window)` reads `context_window` from `LlmModel` to set the compaction token budget.

### Per-Call Settings

The main agent uses the config-derived base `ModelSettings` from `deps.model.settings`. Functional (non-tool-loop) calls use `deps.model.settings_noreason` — resolved at build time from provider defaults, model-specific overrides, and user explicit config via `resolve_noreason_inference()`. `noreason_model_settings()` branches by provider: Ollama gets a standard `ModelSettings` with `extra_body.reasoning_effort="none"` (and other Ollama-specific keys); Gemini gets a `GoogleModelSettings` with `google_thinking_config` (model-specific thinking level or budget). Functional callers (summarizer, dream merge) use `llm_call()` in `co_cli/llm/_call.py`, which defaults to `deps.model.settings_noreason`.

### Model Dependency Checks

`check_agent_llm()` runs inside `create_deps()` (before `build_agent()`) as a single fail-fast gate. Error status raises `ValueError` immediately — session never starts.

- `gemini` + key absent → `status="error"` (cannot proceed)
- `gemini` + key present → `status="ok"` (no HTTP call)
- Ollama: one `GET {llm_host}/api/tags` (5s timeout) checks both reachability and model availability:
  - server unreachable → `status="warn"` (soft fail, session continues)
  - configured model not installed → `status="error"` → `create_deps()` raises `ValueError` (fail-fast)
  - model present → `status="ok"`

## 3. Config

Settings load order is `env > ~/.co-cli/settings.json > defaults`.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm.provider` | `LLM_PROVIDER` | `"ollama-openai"` | Provider selection: `ollama-openai` or `gemini` |
| `llm.host` | `LLM_HOST` | `"http://localhost:11434"` | LLM server base URL for Ollama OpenAI-compatible requests |
| `llm.model` | `CO_LLM_MODEL` | provider default | Single model name used for all tasks (reasoning, subagents, compaction, memory) |
| `llm.num_ctx` | `LLM_NUM_CTX` | `262144` | Context size hint (ignored by Ollama API — set in Modelfile) |
| `llm.ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn threshold for context ratio |
| `llm.ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow threshold for context ratio |
| `llm.api_key` | `GEMINI_API_KEY` (when `provider=gemini`), else `LLM_API_KEY` | `None` | Provider API key; `GEMINI_API_KEY` takes precedence for Gemini, `LLM_API_KEY` is the generic fallback |
| `llm.reasoning.temperature/top_p/max_tokens/num_ctx/context_window/extra_body` | — | unset | Optional explicit overrides for the main agent's reasoning model settings; merged over provider/model defaults |
| `llm.noreason.temperature/top_p/max_tokens/extra_body` | — | `None` / `{}` (resolved from provider defaults at build time) | Pure-override noreason settings model; all fields default to `None`/`{}` — non-`None` values win over provider/model defaults in `resolve_noreason_inference()` |

## 4. Provider Runtime Settings

### Thinking-capable Models — Noreason Settings Resolution

Some Ollama MoE models, including `qwen3.5:35b-a3b-think`, default to reasoning mode. For
functional (non-tool-loop) work — compaction, memory extraction, dream merge — the repo does not
swap to a second `-instruct` model. It keeps the same `-think` weights resident and suppresses
reasoning via per-provider `ModelSettings` resolved at session start.

`build_model()` calls `llm.noreason_model_settings()` which calls `resolve_noreason_inference()`.
The resolution layers three tiers:

```
1. provider defaults (_PROVIDER_NOREASON_DEFAULTS) — e.g. ollama: extra_body.reasoning_effort="none", top_k, min_p; gemini: thinking_config.thinking_level="minimal"
2. model-specific overrides (_MODEL_NOREASON_DEFAULTS) — e.g. gemini-3-pro: thinking_level="low"; gemini-2.5-flash: thinking_budget=0
3. user explicit overrides (llm.noreason settings) — any non-None field wins over tiers 1+2
```

Provider branching in `noreason_model_settings()`:

- **Ollama** → `ModelSettings(temperature, top_p, max_tokens, extra_body={reasoning_effort="none", top_k, min_p, presence_penalty, repeat_penalty, num_ctx, num_predict})`
- **Gemini flash** → `GoogleModelSettings(google_thinking_config={"thinking_level": "minimal"})`
- **Gemini pro** → `GoogleModelSettings(google_thinking_config={"thinking_level": "low"})`
- **Gemini 2.5 flash / flash-lite** → `GoogleModelSettings(google_thinking_config={"thinking_budget": 0})`

Application flow:

```text
build_model(config.llm)
  → resolve_reasoning_inference() → settings (reasoning)
  → resolve_noreason_inference() → settings_noreason (functional)
  → LlmModel(model, settings, settings_noreason, context_window)
  → stored on deps.model

Functional call (compaction, dream merge, extraction):
  llm_call(deps, prompt, ...) → agent.run(..., model_settings=deps.model.settings_noreason)

Reasoning call (main agent turn):
  agent.run(..., model_settings=deps.model.settings)
```

Important backend distinction for Ollama:

- `reasoning_effort="none"` is the Ollama request-level control for suppressing reasoning on `qwen3.5:35b-a3b-think`.
- For generic OpenAI-compatible Qwen3.5 servers outside Ollama, a different control path exists: `chat_template_kwargs.enable_thinking=false`. Not interchangeable.

Validate with: `uv run python scripts/validate_ollama_models.py`

### Ollama Parallel Slots — Not Supported by `qwen35moe`

`OLLAMA_NUM_PARALLEL` controls how many concurrent requests Ollama serves per loaded model.
The `qwen35moe` architecture (used by all `qwen3.5:35b-a3b-*` tags) does not support parallel
slots. Ollama logs `"model architecture does not currently support parallel requests"` and falls
back to `Parallel:1` regardless of the env var setting. All concurrent requests to the same model
are queued serially.

Architectures known to support parallel slots: `llama`, `gemma`, `phi`, `command-r`.

Implication: sub-agent delegation (`coding`, `research`, `analysis`) cannot run truly concurrent
LLM calls against the same Ollama instance with `qwen3.5` MoE models. Calls are serialized by the
runner. This is an Ollama engine limitation, not a co-cli issue.

### Ollama `num_ctx` — Must Be Baked Into Modelfile

Ollama's OpenAI-compatible API silently ignores `num_ctx` sent in request parameters (upstream
issue ollama/ollama#5356). Base model tags default to 4096 tokens and silently truncate multi-turn
conversation history.

For models requiring a larger context window, `num_ctx` must be set via `PARAMETER num_ctx` in the
Modelfile. All custom Modelfiles in `ollama/` bake this parameter via `PARAMETER num_ctx`. The `llm.num_ctx` setting is
forwarded as a client-side hint for future-proofing but is not enforced by Ollama today.

### Modelfile Parameters Reference

All custom Ollama model tags in `ollama/` and their baked parameters:

| Modelfile | Use | num_ctx | num_predict | temp | top_p | top_k | presence_p | repeat_p |
|-----------|-----|--------:|------------:|-----:|------:|------:|-----------:|---------:|
| `qwen3.5-35b-a3b-think` | primary (reasoning + non-reasoning via per-call settings) | 128K | 32K | 1.0 | 0.95 | 20 | 1.5 | 1.0 |
| `qwen3.5-35b-a3b-code` | alternative coding-optimized tag | 64K | 32K | 0.6 | 0.8 | 20 | — | 1.05 |

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
| `co_cli/config/` | Settings package: `_llm.py` owns `LlmSettings` (provider, model, host, context settings); `_core.py` owns `Settings` with nested sub-models |
| `co_cli/deps.py` | `CoDeps` with `config: Settings`; `model: LlmModel \| None` as top-level field |
| `co_cli/bootstrap/check.py` | `check_agent_llm` (provider credentials + model availability) and other integration probes — shared factual probe layer |
| `co_cli/agent/_core.py` | `build_agent()` factory — model selection, tool registration, system prompt assembly |
| `co_cli/commands/_commands.py` | Uses `deps.model` for `/compact` and `/new` via `summarize_messages(deps, ...)` |
| `co_cli/context/summarization.py` | `summarize_messages(deps, messages, *, personality_active, context)` — calls `llm_call()`; `resolve_compaction_budget(config, context_window)` — reads `context_window` from `LlmModel` to set compaction token budget |
| `co_cli/llm/_factory.py` | `LlmModel` — pre-built model + reasoning settings + noreason settings + context_window; `build_model(llm: LlmSettings)` — builds provider-aware model with both settings resolved |
| `co_cli/llm/_call.py` | `llm_call(deps, prompt, *, instructions, message_history, output_type, model_settings)` — single-prompt functional LLM primitive; defaults to `deps.model.settings_noreason` |
| `co_cli/config/_llm.py` | `LlmSettings`, `NoReasonSettings`, `ReasoningSettings`; `resolve_reasoning_inference()`, `resolve_noreason_inference()`; `_PROVIDER_NOREASON_DEFAULTS`, `_MODEL_NOREASON_DEFAULTS`; `DEFAULT_NOREASON_*` constants |
| `ollama/Modelfile.qwen3.5-35b-a3b-think` | Primary reasoning model — thinking enabled |
| `ollama/Modelfile.qwen3.5-35b-a3b-code` | Coding sub-agent — deterministic params |
| `scripts/validate_ollama_models.py` | Standalone dev tool: validates shipped custom Ollama model tags against their baked Modelfile params and directives; not invoked at startup |

## 6. Testing Boundary

Model validation and config-role validation are intentionally split:

- `scripts/validate_ollama_models.py` is the runtime/deployment check for custom Ollama models.
  Its source of truth is the baked `ollama/Modelfile.*` files. It verifies that installed local
  Ollama tags exist and that their baked parameters and baked `/no_think` directives match the
  corresponding Modelfiles.
- pytest covers application behavior. Tests validate config parsing, model construction,
  summarization, and delegation.
- pytest is not the primary mechanism for checking whether a local Ollama tag is installed or whether
  a Modelfile was built with the expected parameters.
- Conversely, the validator script is not the source of truth for model defaults or
  factory logic. That contract belongs to `co_cli/config/_llm.py` and the pytest suite.
