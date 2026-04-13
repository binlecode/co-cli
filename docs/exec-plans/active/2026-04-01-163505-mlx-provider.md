# TODO: MLX-LM Provider Support

**Slug:** `mlx-provider`
**Task type:** `code-feature` — new LLM provider

---

## Context

`co` currently supports two LLM providers: `ollama-openai` (default) and `gemini`. On Apple Silicon, `mlx-lm` offers meaningfully faster inference than Ollama (llama.cpp + Metal) because it uses Apple's native MLX framework, which has tighter ANE/GPU kernel utilization and better KV cache reuse on unified memory.

`mlx_lm.server` exposes an OpenAI-compatible API (default port 11435 per `llm_mlx` setup) with the same `/v1/chat/completions` endpoint as Ollama's OpenAI-compat path. This makes `mlx` a minimal addition — no new HTTP client patterns, no new pydantic-ai model types.

Current-state mismatch to fix in the same delivery: `docs/DESIGN-llm-models.md` describes `role_models` as `dict[str, ModelConfig]`, but `co_cli/config.py` still injects some provider defaults and env role overrides as plain strings instead of dict-shaped `ModelConfig` payloads. This plan must normalize the config source shape while adding MLX.

## Problem & Outcome

**Problem:** `qwen3.5:35b-a3b-think` under Ollama times out on the post-tool-call turn in `test_tool_calling_functional.py` when other tests are contending for the same Ollama process. The 20s `LLM_TOOL_CONTEXT_TIMEOUT_SECS` is hard to meet under load on Apple Silicon.
**Failure cost:** Spurious test failures in the functional test suite and slower autonomous tool execution loops for end users on Mac.
**Outcome:** Faster per-call latency and TTFT (Time-To-First-Token) by introducing `mlx` as a first-class provider choice, allowing users to leverage the highly optimized Apple MLX framework directly.

## Scope

In scope:
- `"mlx"` as a valid `llm_provider` value.
- Normalize provider-default and env-injected `role_models` entries so the merged config shape is consistently dict-based before `Settings.role_models` validation.
- `build_model()` path for mlx (reuses `OpenAIChatModel` + `OpenAIProvider`).
- Health check for mlx provider using `/v1/models` endpoint.
- `uses_mlx()` predicate and `supports_context_ratio_tracking()` update.
- Model quirks directory `co_cli/prompts/model_quirks/mlx/` with at least one model file for `Qwen/Qwen3.5-35B-A3B` (mlx_lm naming convention).
- Dynamic `llm_host` injection defaulting to `http://localhost:11435` when `mlx` is selected.
- Refactoring `co_cli/context/_history.py` to use `supports_context_ratio_tracking()` instead of hardcoded checks.
- Status rendering so `co status` identifies MLX as MLX, not Ollama.
- Deterministic degradation of `knowledge_llm_reranker` when it resolves to `mlx`.

Out of scope:
- Installing or managing `mlx-lm` itself (assumes it runs externally).
- Streaming or multi-modal mlx-specific API features.
- Modifying the knowledge embedding provider.
- Providing an MLX `coding` role model. Reduced role coverage is acceptable for this provider.
- Supporting MLX as a knowledge LLM reranker backend.

## Behavioral Constraints

1. **Reduced Role Injection:** If `llm_provider="mlx"`, `fill_from_env()` MUST inject defaults only for `reasoning`, `summarization`, `analysis`, `research`, and `task`. It MUST NOT invent an MLX `coding` default.
2. **Dict-Shaped Role Entries:** Provider defaults and `CO_MODEL_ROLE_*` env overrides MUST be merged as dict-shaped entries (`{"model": ..., "provider": ..., "api_params": ...}` with optional keys omitted when unused). The merge step MUST NOT inject plain string entries into `data["role_models"]`.
3. **Host Resolution:** If `llm_provider="mlx"` and `llm_host` is not explicitly provided by the user, the system MUST default to `http://localhost:11435`. It MUST NEVER attempt to connect to the Ollama default port (`11434`) when mlx is the provider.
4. **No Context Limits for MLX:** `supports_context_ratio_tracking()` MUST return `False` for `mlx` because `mlx-lm` bakes context windows at load time and ignores the request-side `num_ctx` parameter.
5. **Health Check Probing:** The `mlx` health check MUST probe `/v1/models` and tolerate substring matches of the configured model name against the returned HuggingFace repository paths (e.g. matching `qwen3.5` inside `Qwen/Qwen3.5-35B-A3B`). It MUST NOT probe `/api/tags` which is Ollama-specific.
6. **Status Surface:** `co status` MUST render the active provider as `MLX (...)` when `llm_provider="mlx"`. It MUST NOT label MLX sessions as Ollama.
7. **Reranker Degradation:** If `knowledge_llm_reranker.provider == "mlx"` or `knowledge_llm_reranker.provider is None` while `llm_provider="mlx"`, bootstrap MUST degrade `knowledge_llm_reranker` to `None` with an explicit startup status. It MUST NOT silently treat MLX as an unknown reranker provider and continue without a degradation message.

## Failure Modes

- **Unreachable Server:** If the `mlx-lm.server` is not running, the system will warn but MUST NOT crash, matching Ollama's behavior.
- **Missing Reasoning Model:** If the `reasoning` role model is not present in the `/v1/models` payload, the system MUST hard fail at startup, matching Ollama's behavior.
- **Quirks Over-injection:** `mlx-lm` only supports `temperature`, `top_p`, `max_tokens`, `repetition_penalty`. If `extra_body` parameters like `num_ctx` or `top_k` are injected, `mlx_lm.server` might reject the request. The MLX quirks loader MUST only emit supported subsets.
- **Unsupported Reranker Surface:** If a user configures the knowledge LLM reranker under MLX, startup must degrade it with a visible status rather than silently leaving the field active.

## High-Level Design

The implementation adds `"mlx"` to the shared provider surface in `co_cli/config.py` and `ModelConfig.provider`, dynamically setting `llm_host` to `http://localhost:11435` and applying MLX defaults only for the supported roles: `reasoning`, `summarization`, `analysis`, `research`, and `task`. As part of the same config cleanup, all provider defaults and env-injected role overrides are normalized to dict-shaped `ModelConfig` payloads before validation. `co_cli/deps.py` gets a new `uses_mlx()` predicate.

In `co_cli/_model_factory.py`, the `build_model()` path branches for `"mlx"` identically to `"ollama-openai"`, generating an `OpenAIChatModel` with `api_key="mlx"`. The context tracking is bypassed by returning `False` in `supports_context_ratio_tracking()`, and we refactor `_history.py` to decouple it from Ollama-specific logic.

A new quirks folder `co_cli/prompts/model_quirks/mlx/` stores the inference parameters. The startup checks in `co_cli/bootstrap/check.py` are updated with a new `_check_mlx_models` that hits `GET {config.llm_host}/v1/models` for model validation, and `check_reranker_llm()` treats MLX as explicitly unsupported so bootstrap can degrade it deterministically. `co_cli/bootstrap/render_status.py` is updated so the user-facing provider label matches the active backend.

---

## Tasks

### TASK-1: Add "mlx" provider to config with dynamic defaults
**files:** `co_cli/config.py`, `tests/test_config.py`
**success_signal:** A user can select `LLM_PROVIDER=mlx` and get the supported MLX role defaults plus the MLX host without hand-editing every role, and the merged role-model config shape is consistent across providers.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_config.py -k "mlx or role_models" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-config.log` passes.
**What to do:**
- Add `DEFAULT_MLX_HOST = "http://localhost:11435"` in `config.py`.
- Add `"mlx"` to `ModelConfig.provider` Literal.
- Convert existing provider defaults that are still plain strings into dict-shaped entries. At minimum this includes the current Gemini reasoning default and Ollama coding default; the MLX defaults must also be dict-shaped from the start.
- Wrap `CO_MODEL_ROLE_*` env overrides as dict-shaped entries before merging them into `data["role_models"]`.
- In `fill_from_env()`, add an `elif provider == "mlx":` block that injects defaults for `reasoning`, `summarization`, `analysis`, `research`, and `task`. Do not inject a `coding` default for MLX.
- For reasoning, use `Qwen/Qwen3.5-35B-A3B`. For `summarization`, `analysis`, `research`, and `task`, use `Qwen/Qwen3.5-35B-A3B` with `reasoning_effort="none"`.
- In `fill_from_env()`, if `provider == "mlx"` and `llm_host` was not provided in `data`, set `data["llm_host"] = DEFAULT_MLX_HOST`.
- Add tests that prove the MLX provider is accepted, the MLX host is injected, the supported roles are injected, `coding` remains absent by default, and env/provider default role entries round-trip as dict-shaped `ModelConfig` data rather than mixed string/dict inputs.

### TASK-2: Add `uses_mlx()` and fix context tracking coupling
**prerequisites:** [TASK-1]
**files:** `co_cli/deps.py`, `co_cli/context/_history.py`, `tests/test_context_compaction.py`
**success_signal:** MLX sessions use the generic token-budget path and never apply Ollama-specific context-budget logic.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_context_compaction.py -k "mlx or ollama_budget or real_input_tokens" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-context.log` passes.
**What to do:**
- Add `uses_mlx(self) -> bool` to `CoConfig` in `deps.py`.
- Keep `supports_context_ratio_tracking()` as the single policy entrypoint for Ollama-only context-ratio logic.
- **Refactor `co_cli/context/_history.py`**: Replace any hardcoded `config.uses_ollama_openai() and config.llm_num_ctx > 0` logic with `config.supports_context_ratio_tracking()`.
- Add a compaction test that proves MLX follows the non-Ollama budget branch.

### TASK-3: Add mlx build path and quirks
**prerequisites:** [TASK-1]
**files:** `co_cli/_model_factory.py`, `co_cli/prompts/model_quirks/_loader.py`, `co_cli/prompts/model_quirks/mlx/Qwen_Qwen3.5-35B-A3B.md`, `tests/test_model_factory.py`
**success_signal:** MLX requests use the OpenAI-compatible backend with MLX-safe inference params and no Ollama-only request body fields.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_model_factory.py -k "mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-model-factory.log` passes.
**What to do:**
- In `co_cli/prompts/model_quirks/_loader.py`, update `normalize_model_name()` to replace `/` with `_` to handle MLX's HuggingFace repository paths (e.g. `return model_name.split(":")[0].replace("/", "_")`).
- In `_model_factory.py::build_model()`, add `elif effective_provider == "mlx":` branch. Identical to `ollama-openai` but uses `get_model_inference("mlx", normalized)`, no `num_ctx` in `extra_body`, and `api_key="mlx"`.
- Create directory `co_cli/prompts/model_quirks/mlx/`.
- Add `Qwen_Qwen3.5-35B-A3B.md` with `temperature: 1.0`, `top_p: 0.95`, `max_tokens: 32768`. Do NOT add `extra_body` parameters. This will serve as the thinking model default on MLX.
- Add a model-factory test that proves the MLX branch builds an `OpenAIChatModel` and does not inject `num_ctx` or Ollama-only `extra_body` fields.

### TASK-4: Add MLX health checks, reranker degradation, and status rendering
**prerequisites:** [TASK-1, TASK-2]
**files:** `co_cli/bootstrap/check.py`, `co_cli/bootstrap/render_status.py`, `tests/test_model_check.py`, `tests/test_status.py`, `tests/test_bootstrap.py`
**success_signal:** `co status` identifies the backend as MLX and reports an unreachable MLX host as offline, while bootstrap degrades unsupported MLX rerankers with a visible status.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_model_check.py tests/test_status.py tests/test_bootstrap.py -k "mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-bootstrap-status.log` passes.
**What to do:**
- Add `_check_mlx_models(config)` helper: Probes `GET {config.llm_host}/v1/models` (5s timeout). Parses `{"data": [{"id": "<model-id>"}]}`. Substring match for model name. Unreachable -> warn. Reasoning model missing -> error. Optional role missing -> warn.
- Extract Ollama block to `_check_ollama_models(config)`.
- Update `check_agent_llm()` to route to `_check_gemini_key`, `_check_mlx_models`, or `_check_ollama_models`.
- Update `check_reranker_llm()` so MLX rerankers return a non-`ok` result with an explicit unsupported detail, allowing `resolve_reranker()` to degrade them to `None`.
- Update `co_cli/bootstrap/render_status.py` so MLX is rendered as `MLX (<model>)` instead of falling into the Ollama label path.
- Add tests for MLX-unreachable agent checks, MLX reranker degradation, and MLX status labeling.

---

## Testing

1. `mkdir -p .pytest-logs`
2. `uv run pytest tests/test_config.py -k "mlx or role_models" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-config.log`
3. `uv run pytest tests/test_context_compaction.py -k "mlx or ollama_budget or real_input_tokens" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-context.log`
4. `uv run pytest tests/test_model_factory.py -k "mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-model-factory.log`
5. `uv run pytest tests/test_model_check.py tests/test_status.py tests/test_bootstrap.py -k "mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-bootstrap-status.log`
6. On Apple Silicon with a running `mlx_lm.server`, run `uv run pytest tests/test_tool_calling_functional.py 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-mlx-tool-calling.log` to verify the end-to-end tool-call turn against the MLX backend.

## Open Questions

- None.


## Final — Team Lead

Revised for concrete delivery.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev mlx-provider`
