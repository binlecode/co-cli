# TODO: vLLM-MLX Provider Support

**Slug:** `vllm-mlx-provider`
**Task type:** `code-feature` — new LLM provider

---

## Context

`co` currently supports two LLM providers: `ollama-openai` (default) and `gemini`. On Apple Silicon, `vllm-mlx` offers meaningfully faster inference and **continuous batching** compared to Ollama (llama.cpp). This solves test timeouts and parallel agent latency under load, as it achieves much higher throughput natively on Apple's Metal GPU.

`vllm-mlx` exposes an OpenAI-compatible API (default port 12434) with the same `/v1/chat/completions` endpoint as Ollama's OpenAI-compat path. This makes `vllm-mlx` a minimal addition — no new HTTP client patterns, no new pydantic-ai model types.

Current-state mismatch to fix in the same delivery: `docs/DESIGN-llm-models.md` describes `role_models` as `dict[str, ModelConfig]`, but `co_cli/config.py` still injects some provider defaults and env role overrides as plain strings instead of dict-shaped `ModelConfig` payloads. This plan must normalize the config source shape while adding vLLM-MLX.

## Problem & Outcome

**Problem:** `qwen3.5:35b-a3b-think` under Ollama times out on the post-tool-call turn in `test_tool_calling_functional.py` when other tests are contending for the same Ollama process. The 20s `LLM_TOOL_CONTEXT_TIMEOUT_SECS` is hard to meet under load on Apple Silicon because `llama.cpp` handles concurrent requests poorly.
**Failure cost:** Spurious test failures in the functional test suite and slower autonomous tool execution loops for end users on Mac.
**Outcome:** Faster per-call latency, Time-To-First-Token (TTFT), and true concurrent throughput by introducing `vllm-mlx` as a first-class provider choice, featuring Gemma 4 as the default reasoning model.

## Scope

In scope:
- `"vllm-mlx"` as a valid `llm_provider` value.
- Normalize provider-default and env-injected `role_models` entries so the merged config shape is consistently dict-based before `Settings.role_models` validation.
- `build_model()` path for vllm-mlx (reuses `OpenAIChatModel` + `OpenAIProvider`).
- Health check for vllm-mlx provider using `/v1/models` endpoint.
- `uses_vllm_mlx()` predicate and `supports_context_ratio_tracking()` update.
- Model quirks directory `co_cli/prompts/model_quirks/vllm-mlx/` with at least one model file for `Google/Gemma-4-26B-A4B`.
- Dynamic `llm_host` injection defaulting to `http://localhost:12434` when `vllm-mlx` is selected.
- Refactoring `co_cli/context/_history.py` to use `supports_context_ratio_tracking()` instead of hardcoded checks.
- Status rendering so `co status` identifies vLLM-MLX as vLLM-MLX, not Ollama.
- Deterministic degradation of `knowledge_llm_reranker` when it resolves to `vllm-mlx`.

Out of scope:
- Installing or managing `vllm-mlx` itself (assumes it runs externally).
- Modifying the knowledge embedding provider.
- Providing a vLLM-MLX `coding` role model. Reduced role coverage is acceptable for this provider.
- Supporting vLLM-MLX as a knowledge LLM reranker backend.

## Behavioral Constraints

1. **Reduced Role Injection:** If `llm_provider="vllm-mlx"`, `fill_from_env()` MUST inject defaults only for `reasoning`, `summarization`, `analysis`, `research`, and `task`. It MUST NOT invent a `coding` default.
2. **Dict-Shaped Role Entries:** Provider defaults and `CO_MODEL_ROLE_*` env overrides MUST be merged as dict-shaped entries (`{"model": ..., "provider": ..., "api_params": ...}` with optional keys omitted when unused). The merge step MUST NOT inject plain string entries into `data["role_models"]`.
3. **Host Resolution:** If `llm_provider="vllm-mlx"` and `llm_host` is not explicitly provided by the user, the system MUST default to `http://localhost:12434`. It MUST NEVER attempt to connect to the Ollama default port (`11434`).
4. **No Context Limits for vLLM-MLX:** `supports_context_ratio_tracking()` MUST return `False` for `vllm-mlx` because vLLM handles PagedAttention and max tokens internally, ignoring the request-side `num_ctx` parameter.
5. **Health Check Probing:** The `vllm-mlx` health check MUST probe `/v1/models` and tolerate substring matches of the configured model name against the returned HuggingFace repository paths. It MUST NOT probe `/api/tags` which is Ollama-specific.
6. **Status Surface:** `co status` MUST render the active provider as `vLLM-MLX (...)` when `llm_provider="vllm-mlx"`. It MUST NOT label vLLM-MLX sessions as Ollama.
7. **Reranker Degradation:** If `knowledge_llm_reranker.provider == "vllm-mlx"` or `knowledge_llm_reranker.provider is None` while `llm_provider="vllm-mlx"`, bootstrap MUST degrade `knowledge_llm_reranker` to `None` with an explicit startup status.

## Failure Modes

- **Unreachable Server:** If the `vllm` server is not running, the system will warn but MUST NOT crash, matching Ollama's behavior.
- **Missing Reasoning Model:** If the `reasoning` role model is not present in the `/v1/models` payload, the system MUST hard fail at startup.
- **Unsupported Reranker Surface:** If a user configures the knowledge LLM reranker under vLLM-MLX, startup must degrade it with a visible status rather than silently leaving the field active.

## High-Level Design

The implementation adds `"vllm-mlx"` to the shared provider surface in `co_cli/config.py` and `ModelConfig.provider`, dynamically setting `llm_host` to `http://localhost:12434` and applying vLLM-MLX defaults only for the supported roles: `reasoning`, `summarization`, `analysis`, `research`, and `task`. All provider defaults and env-injected role overrides are normalized to dict-shaped `ModelConfig` payloads before validation. `co_cli/deps.py` gets a new `uses_vllm_mlx()` predicate.

In `co_cli/_model_factory.py`, the `build_model()` path branches for `"vllm-mlx"` identically to `"ollama-openai"`, generating an `OpenAIChatModel` with `api_key="vllm-mlx"`. Context tracking is bypassed by returning `False` in `supports_context_ratio_tracking()`, and we refactor `_history.py` to decouple it from Ollama-specific logic.

A new quirks folder `co_cli/prompts/model_quirks/vllm-mlx/` stores inference parameters. Startup checks in `co_cli/bootstrap/check.py` get a new `_check_vllm_mlx_models` that hits `GET {config.llm_host}/v1/models` for validation, and `check_reranker_llm()` treats vLLM-MLX as unsupported so bootstrap can degrade it. `co_cli/bootstrap/render_status.py` is updated so the user-facing provider label matches the active backend.

---

## Tasks

### TASK-1: Add "vllm-mlx" provider to config with dynamic defaults
**files:** `co_cli/config.py`, `tests/test_config.py`
**success_signal:** A user can select `LLM_PROVIDER=vllm-mlx` and get the supported vLLM-MLX role defaults plus the host without hand-editing every role, and the merged config shape is consistent.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_config.py -k "vllm_mlx or role_models" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-config.log` passes.
**What to do:**
- Add `DEFAULT_VLLM_MLX_HOST = "http://localhost:12434"` in `config.py`.
- Add `"vllm-mlx"` to `ModelConfig.provider` Literal.
- Convert existing provider defaults that are still plain strings into dict-shaped entries.
- Wrap `CO_MODEL_ROLE_*` env overrides as dict-shaped entries before merging them.
- In `fill_from_env()`, add an `elif provider == "vllm-mlx":` block that injects defaults for `reasoning`, `summarization`, `analysis`, `research`, and `task`. Do not inject a `coding` default.
- For reasoning, use `Google/Gemma-4-26B-A4B`. For others, use the same with `reasoning_effort="none"`.
- If `provider == "vllm-mlx"` and `llm_host` was not provided, set `data["llm_host"] = DEFAULT_VLLM_MLX_HOST`.
- Add tests that prove the vLLM-MLX provider is accepted, the host is injected, supported roles are injected, and dict-shaped defaults work.

### TASK-2: Add `uses_vllm_mlx()` and fix context tracking coupling
**prerequisites:** [TASK-1]
**files:** `co_cli/deps.py`, `co_cli/context/_history.py`, `tests/test_context_compaction.py`
**success_signal:** vLLM-MLX sessions use the generic token-budget path and never apply Ollama-specific context-budget logic.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_context_compaction.py -k "vllm_mlx or ollama_budget or real_input_tokens" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-context.log` passes.
**What to do:**
- Add `uses_vllm_mlx(self) -> bool` to `CoConfig` in `deps.py`.
- Keep `supports_context_ratio_tracking()` as the single policy entrypoint for Ollama-only context-ratio logic.
- **Refactor `co_cli/context/_history.py`**: Replace hardcoded `config.uses_ollama_openai()` logic with `config.supports_context_ratio_tracking()`.
- Add a compaction test that proves vLLM-MLX follows the non-Ollama budget branch.

### TASK-3: Add vllm-mlx build path and quirks
**prerequisites:** [TASK-1]
**files:** `co_cli/_model_factory.py`, `co_cli/prompts/model_quirks/_loader.py`, `co_cli/prompts/model_quirks/vllm-mlx/Google_Gemma-4-26B-A4B.md`, `tests/test_model_factory.py`
**success_signal:** vLLM-MLX requests use the OpenAI-compatible backend with vLLM-safe inference params and no Ollama-only request body fields.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_model_factory.py -k "vllm_mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-model-factory.log` passes.
**What to do:**
- In `co_cli/prompts/model_quirks/_loader.py`, update `normalize_model_name()` to handle `/`.
- In `_model_factory.py::build_model()`, add `elif effective_provider == "vllm-mlx":` branch. Identical to `ollama-openai` but uses `api_key="vllm-mlx"`.
- Create directory `co_cli/prompts/model_quirks/vllm-mlx/`.
- Add `Google_Gemma-4-26B-A4B.md` with `temperature: 1.0`, `top_p: 0.95`. Do NOT add `extra_body` parameters like `num_ctx`.
- Add a model-factory test.

### TASK-4: Add vLLM-MLX health checks, reranker degradation, and status rendering
**prerequisites:** [TASK-1, TASK-2]
**files:** `co_cli/bootstrap/check.py`, `co_cli/bootstrap/render_status.py`, `tests/test_model_check.py`, `tests/test_status.py`, `tests/test_bootstrap.py`
**success_signal:** `co status` identifies the backend as vLLM-MLX.
**done_when:** `mkdir -p .pytest-logs && uv run pytest tests/test_model_check.py tests/test_status.py tests/test_bootstrap.py -k "vllm_mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-bootstrap-status.log` passes.
**What to do:**
- Add `_check_vllm_mlx_models(config)` helper: Probes `GET {config.llm_host}/v1/models`. Substring match for model name.
- Update `check_agent_llm()` to route appropriately.
- Update `check_reranker_llm()` so vLLM-MLX rerankers return a non-`ok` result.
- Update `co_cli/bootstrap/render_status.py` so vLLM-MLX is rendered as `vLLM-MLX (<model>)`.
- Add tests.

---

## Testing

1. `mkdir -p .pytest-logs`
2. `uv run pytest tests/test_config.py -k "vllm_mlx or role_models" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-config.log`
3. `uv run pytest tests/test_context_compaction.py -k "vllm_mlx or ollama_budget or real_input_tokens" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-context.log`
4. `uv run pytest tests/test_model_factory.py -k "vllm_mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-model-factory.log`
5. `uv run pytest tests/test_model_check.py tests/test_status.py tests/test_bootstrap.py -k "vllm_mlx" -v 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-vllm-mlx-bootstrap-status.log`

## Final — Team Lead

Revised for concrete delivery using vLLM-MLX and Gemma 4.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev vllm-mlx-provider`
