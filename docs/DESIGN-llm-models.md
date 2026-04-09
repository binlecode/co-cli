# LLM Models

## 1. What & How

Co CLI supports two providers (`ollama-openai`, `gemini`) and a single-model architecture:
`llm.model` names the one model used for all tasks. `create_deps()` calls
`build_model(config.llm)` once to produce an `LlmModel` (model object + base settings +
context window), stored on `CoDeps.model`. The main agent uses the model's quirks-derived
base `ModelSettings`; all non-reasoning calls (subagents, compaction, memory extraction) use
a static `NOREASON_SETTINGS` constant that suppresses reasoning via per-call settings.

```
build_model(config.llm) → LlmModel
    ├── provider == "ollama-openai"
    │   └── OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama") + OpenAIChatModel
    │       (num_ctx baked from model inference metadata when available)
    └── provider == "gemini"
        └── GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))
    → LlmModel(model, settings, context_window)

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

`LlmModel` is a dataclass pairing a pre-built model object (`Any`) with its base `ModelSettings | None` and an optional `context_window: int | None` from model quirks. It is built once at session start via `build_model(config.llm)` and stored on `deps.model`. Agent factories and summarization functions receive the raw model object and settings separately.

`build_model()` in `co_cli/_model_factory.py` is provider-aware:

- `ollama-openai` → `OpenAIChatModel(model_name, OpenAIProvider(base_url="{llm_host}/v1", api_key="ollama"))`
- `gemini` → `GoogleModel(model_name, provider=GoogleProvider(api_key=api_key))` — key injected directly, no env mutation
- Any other provider → `ValueError` raised.

Model quirks (from `prompts/model_quirks/`) provide inference defaults (temperature, top_p, max_tokens, extra_body, context_window). The base `ModelSettings` stored on `LlmModel.settings` reflects these quirks. `resolve_compaction_budget(config, context_window)` reads `context_window` from `LlmModel` to set the compaction token budget.

### Per-Call Settings

The main agent uses the quirks-derived base `ModelSettings` from `deps.model.settings`. All non-reasoning calls use `NOREASON_SETTINGS` (from `co_cli/_model_settings.py`) — a static `ModelSettings` constant that suppresses reasoning via `reasoning_effort="none"` in `extra_body`. Callers pass `model=deps.model.model` and `model_settings=NOREASON_SETTINGS` separately. Provider-specific extra_body keys (Ollama) are silently ignored by Gemini's GoogleProvider.

### Model Dependency Checks

`check_agent_llm()` runs inside `create_deps()` (before `build_agent()`) as a single fail-fast gate. Error status raises `ValueError` immediately — session never starts.

- `gemini` + key absent → `status="error"` (cannot proceed)
- `gemini` + key present → `status="ok"` (no HTTP call)
- Ollama: one `GET {llm_host}/api/tags` (5s timeout) checks both reachability and model availability:
  - server unreachable → `status="warn"` (soft fail, session continues)
  - configured model not installed → `status="error"` → `create_deps()` raises `ValueError` (fail-fast)
  - model present → `status="ok"`

## 3. Config

Settings load order is `env > .co-cli/settings.json > ~/.co-cli/settings.json > defaults`.

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `llm.provider` | `LLM_PROVIDER` | `"ollama-openai"` | Provider selection: `ollama-openai` or `gemini` |
| `llm.host` | `LLM_HOST` | `"http://localhost:11434"` | LLM server base URL for Ollama OpenAI-compatible requests |
| `llm.model` | `CO_LLM_MODEL` | provider default | Single model name used for all tasks (reasoning, subagents, compaction, memory) |
| `llm.num_ctx` | `LLM_NUM_CTX` | `262144` | Context size hint (ignored by Ollama API — set in Modelfile) |
| `llm.ctx_warn_threshold` | `CO_CTX_WARN_THRESHOLD` | `0.85` | Warn threshold for context ratio |
| `llm.ctx_overflow_threshold` | `CO_CTX_OVERFLOW_THRESHOLD` | `1.0` | Overflow threshold for context ratio |
| `llm.api_key` | `LLM_API_KEY` | `None` | LLM API key (required when `llm.provider=gemini`) |

## 4. Provider Quirks

### Thinking-capable Models — `api_params` Override

Some Ollama MoE models, including `qwen3.5:35b-a3b-think`, default to reasoning mode. For
non-reasoning work such as compaction, subagents, and memory extraction, the repo does not swap
to a second `-instruct` model. It keeps the same `-think` weights resident and changes the
request body via per-call `ModelSettings`.

Supported non-reason path for this repo:

- provider: `ollama-openai`
- model: `qwen3.5:35b-a3b-think`
- per-call override: `NOREASON_SETTINGS` with `reasoning_effort = "none"` in `extra_body`

`NOREASON_SETTINGS` shape (from `co_cli/_model_settings.py`):

```yaml
temperature: 0.7
top_p: 0.8
max_tokens: 16384
extra_body:
  reasoning_effort: none
  top_k: 20
  min_p: 0.0
  presence_penalty: 1.5
  repeat_penalty: 1.0
  num_ctx: 131072
  num_predict: 16384
```

Application flow:

```text
build_model(config.llm)
  quirks defaults → base ModelSettings
  → LlmModel(model, settings, context_window)
  → stored on deps.model

Non-reasoning call (subagent, compaction, memory extraction):
  model=deps.model.model, model_settings=NOREASON_SETTINGS
  → pydantic-ai request
  → POST {llm_host}/v1/chat/completions with extra_body merged into the API call

Reasoning call (main agent turn):
  model=deps.model.model, model_settings=deps.model.settings (quirks-derived base)
```

Practical rule:

- The main agent uses quirks-derived base settings from `build_model()`.
- All non-reasoning calls pass `NOREASON_SETTINGS` as the `model_settings` argument.
- `NOREASON_SETTINGS` includes all Ollama-specific extra_body keys; provider-specific keys
  are silently ignored by Gemini's GoogleProvider.

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
| `co_cli/agent.py` | `build_agent()` factory — model selection, tool registration, system prompt assembly |
| `co_cli/commands/_commands.py` | Uses `deps.model.model` + `NOREASON_SETTINGS` for `/compact` and `/new` |
| `co_cli/context/summarization.py` | `summarize_messages(messages, model, model_settings, ...)` — bare Agent summariser; `resolve_compaction_budget(config, context_window)` — reads `context_window` from `LlmModel` to set compaction token budget |
| `co_cli/_model_factory.py` | `LlmModel` — pre-built model + settings + context_window container; `build_model(llm: LlmSettings)` — builds provider-aware model from flat settings |
| `co_cli/_model_settings.py` | `NOREASON_SETTINGS` — static `ModelSettings` for all non-reasoning calls (subagents, compaction, memory extraction) |
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
