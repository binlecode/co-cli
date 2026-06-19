# Support Gemini 3.1 Pro as an additional backend (selectable alongside the default Ollama/qwen)

Task type: backend enablement — add a large cloud reasoning model as a *selectable* provider/model option. Config-tier addition on existing plumbing + one real design decision (context-window budget) + live validation. Ollama/qwen stays the default; the weak-model reflex program continues for the local path.

## Context — research/scope pass (2026-06-19)

**Existing Gemini support.** co already has a Gemini provider: `provider: Literal["ollama","gemini"]` (`co_cli/config/llm.py:225`), `_gemini_settings()` → `GoogleModelSettings`, `GEMINI_API_KEY` resolution, `validate_config()`, and reasoning/noreason mode mapping. But `_LLM_SETTINGS["gemini"]` only carries **flash-tier** models: `gemini-3-flash-preview` (default), `gemini-2.5-flash`, `gemini-2.5-flash-lite`. No pro/large entry exists. So this milestone is a **config-tier addition on working plumbing**, not new architecture.

**Target model (verified live, not assumed).** The user said "gemini-3-pro"; the current pro-tier API id is **`gemini-3.1-pro-preview`** (the dev guide lists `gemini-3-pro` only as the image variant `gemini-3-pro-image-preview`). Specs:
- **Input context: 1,000,000 tokens.** Output: **up to 64k** (65,536).
- `thinking_level` ∈ `minimal | low | medium | high` (default `high`). Mirrors the `gemini-3-flash-preview` knob (which co sets to `MINIMAL` for noreason).
- Multimodal (image/video/audio input).
- Long-context **pricing cliff at >200K tokens** (all tokens billed at long-context rate above that); GA pricing effective 2026-07-01.
- Sources: [Gemini 3 dev guide](https://ai.google.dev/gemini-api/docs/gemini-3), [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing).

**Integration facts (grounded in source).**
- Model-key resolution is `self.model.split(":")[0]` (`llm.py:255,279`). Gemini ids have no `:`, so the `_LLM_SETTINGS` key must be the **exact** full id `gemini-3.1-pro-preview`. No variant-stripping needed.
- `validate_config` requires the model-key to exist in `_LLM_SETTINGS` AND to have a `reasoning` entry to be usable as the main agent (`llm.py:281-284`). gemini-3.1-pro supports reasoning (thinking_level high) → it gets both `reasoning` + `noreason` blocks, so it is a full main-agent backend (unlike 2.5-flash, which is noreason-only).
- The `num_ctx` floor/ceiling probe checks are **Ollama-only** (`ollama_num_ctx()` returns None for Gemini; the Modelfile probe is skipped). For Gemini, `max_context_tokens` is purely the compaction reference, not a probed ceiling.

**The one real design decision — the context-window mismatch.** co's global `MAX_CONTEXT_TOKENS = 65_536` (`llm.py:29`) is tuned for qwen's 64k Modelfile. gemini-3.1-pro offers **1M** — a ~15× gap. Compaction thresholds (`proactive_check` threshold/budget) key off `max_context_tokens`, so leaving it at 65,536 makes co compact aggressively and **waste gemini-pro's headroom** (correct and safe, just suboptimal). This is the only non-mechanical scope item (see OQ-1).

## Problem & Outcome

**Problem.** Users who want a strong cloud reasoning backend can only select flash-tier Gemini today; there is no large/pro option, and the context budget is hard-tuned to the local model.

**Failure cost.** Without it: co is locked to the weak-local model or fast-but-shallow flash for any task needing a strong reasoner; the recently-diagnosed weak-model failures (recall, thrash) have no strong-model escape hatch. Over-scoping risk: rebuild context/compaction to be fully model-aware, or wire vision/multimodal, when the milestone is "make pro selectable."

**Outcome.** `provider=gemini, model=gemini-3.1-pro-preview` is a working, validated main-agent backend: boots, passes config validation, drives reasoning + noreason turns with tool calls, on a context budget that is at least safe (and ideally tuned to its window). Ollama/qwen remains the untouched default.

**Shippable contract:** the config entry + the context-budget handling for the Gemini-pro selection + a live smoke proving a real turn (reasoning + tool call) — clearing validation with no regression to the Ollama default path.

## Behavioral Constraints
- **Do not change the default backend.** `DEFAULT_LLM_PROVIDER`/`DEFAULT_LLM_MODELS` stay Ollama/qwen. gemini-3.1-pro is opt-in via config/env only.
- **No hardcoded `~/.co-cli`**; use config constants. Centralized settings only — knobs live in `_LLM_SETTINGS`, never coined inline (`feedback_evals_centralized_settings`, `feedback_tests_use_config_model_settings`).
- **Exact model id** `gemini-3.1-pro-preview` as the `_LLM_SETTINGS` key (matches `model.split(":")[0]`).
- **Vision is OUT of scope** — gemini-3.1-pro is multimodal, but co's image plumbing is unbuilt (`reference_configured_model_vision`); vision is its own plan. Backend = text + tool-calling.
- Tests/repros hit `llm.host`/provider from config and use `reasoning_model_settings()` / `noreason_model_settings()` — never coin `ModelSettings`/`GoogleModelSettings` inline.
- Live Gemini calls need `GEMINI_API_KEY` (in `~/env-secrets/`, never the repo). Watch call timing; RCA-first on slow/stalled calls.

## Scope

### In scope
- `_LLM_SETTINGS["gemini"]["gemini-3.1-pro-preview"]` — `reasoning` block (thinking_level for depth; temp/top_p; max_tokens ≤ 65,536) + `noreason` block (thinking_level `minimal`; lower max_tokens), mirroring `gemini-3-flash-preview`.
- Context-budget handling for the gemini-pro selection (OQ-1): at minimum keep the safe global default working; ideally allow/raise `max_context_tokens` for the larger window without breaking the Ollama path's ceiling checks.
- `validate_config` confirmation (api_key present + reasoning entry → usable as main model); no Ollama probe on the Gemini path.
- Live smoke: boot with `provider=gemini, model=gemini-3.1-pro-preview`, drive a reasoning turn + a noreason helper call, each making a tool call.

### Out of scope (deferred with rationale)
- **Vision/multimodal** — image plumbing unbuilt; separate plan.
- **Making gemini-pro the default** — explicitly an *additional option* (user choice).
- **Model-aware auto-derivation of `max_context_tokens`** beyond what OQ-1 settles — a general refactor; only do the minimum the selection needs.
- **Per-model prompt calibration** (strong-Gemini vs weak-local prompt) — that is the `per-model-prompt-calibration` plan's job; this milestone is backend enablement, not prompt tuning. (The reflex program continues independently for the local path.)
- **Cost guardrails / long-context-pricing alerts** — noted (>200K cliff), not built here.
- Eval runs against Gemini — evals stay on the configured model via centralized settings; no eval change unless a later decision routes evals to Gemini.

## High-Level Design

One config addition + one budget decision + validation/smoke. No new modules; `build_model`/`_gemini_settings`/`llm_call` already handle the Gemini path generically.

```python
# _LLM_SETTINGS["gemini"], new entry (illustrative — exact knobs pinned in TASK-1)
"gemini-3.1-pro-preview": {
    "reasoning": {            # main agent
        "temperature": 1.0, "top_p": 0.95, "max_tokens": 65536,
        "thinking_config": {"thinking_level": "high"},   # or "medium" for cost/latency — OQ-2
    },
    "noreason": {             # helper calls (summarizer, judge, memory-merge)
        "temperature": 0.7, "top_p": 0.8, "max_tokens": 16384,
        "thinking_config": {"thinking_level": "minimal"},
    },
},
```

## Tasks

**TASK-1 — Add the `gemini-3.1-pro-preview` inference entry + validate**
- files: `co_cli/config/llm.py`
- done_when: `_LLM_SETTINGS["gemini"]["gemini-3.1-pro-preview"]` has `reasoning` + `noreason` blocks (thinking_level + temp/top_p/max_tokens, output ≤ 65,536), mirroring the flash-preview shape; with `provider=gemini, model=gemini-3.1-pro-preview, GEMINI_API_KEY` set, `validate_config()` returns no error and `reasoning_model_settings()` / `noreason_model_settings()` produce valid `GoogleModelSettings` (asserted in a config-level test using the real settings constructors, not inline-coined); the Ollama default path is unchanged (existing config tests still pass); repo-wide grep confirms no other site hardcodes the gemini model list; full suite passes.
- success_signal: selecting gemini-3.1-pro as the main model passes validation and yields correct settings for both modes.
- prerequisites: none

**TASK-2 — Context-budget handling for the gemini-pro selection (OQ-1 resolution)**
- files: `co_cli/config/llm.py` (+ wherever `max_context_tokens` feeds compaction, read-only confirm)
- done_when: the resolution chosen in OQ-1 is implemented — at minimum, selecting gemini-3.1-pro with the default `max_context_tokens` boots and runs without tripping the Ollama-only ceiling/floor checks (confirmed: those checks are gated on `uses_ollama()`); if OQ-1 raises the budget for the Gemini window, the new value is set centrally (not inline) and a config test asserts compaction thresholds resolve from it; the Ollama path's `num_ctx` ceiling/floor checks remain unaffected; full suite passes.
- success_signal: gemini-pro runs on a context budget that is safe and (per OQ-1) appropriate to its window, with zero impact on the Ollama path.
- prerequisites: TASK-1

**TASK-3 — Live backend smoke (UAT)**
- files: `tmp/gemini_pro_smoke.py` (scratch, not shipped)
- done_when: with `GEMINI_API_KEY` set, a smoke following the `tmp/weather_smoke.py` pattern boots co with `provider=gemini, model=gemini-3.1-pro-preview` and drives (a) a reasoning-mode turn that makes a tool call and returns a final answer, and (b) a noreason helper call (e.g. via `llm_call`) — both succeed against the live API; tail the log, RCA-first on slow calls; observed tool-call order + timings recorded in the delivery summary. (Smoke is the gate; no pytest hits the live API.)
- success_signal: gemini-3.1-pro actually drives a real co turn end-to-end, reasoning + tool-calling.
- prerequisites: TASK-1, TASK-2

## Testing
- Config-level pytest: validation + settings construction for gemini-3.1-pro (real `GoogleModelSettings`, centralized knobs); Ollama default-path config tests unchanged. Pipe to `.pytest-logs/`.
- No pytest against the live Gemini API; TASK-3 smoke is the live gate (needs `GEMINI_API_KEY`).
- Watch LLM call timing live; RCA-first on slow/stalled calls; never fold cold-start into a call budget.

## Open Questions
1. **`max_context_tokens` for the gemini-pro selection.** Options: (a) keep the global 65,536 (safe, wastes the 1M window, compacts early); (b) raise it for the Gemini selection — to ~200,000 (just under the long-context pricing cliff, cost-aware) or higher; (c) make it model-aware (defer as a general refactor). Default: **(b) at ~200K**, set centrally, leaving the Ollama path untouched — captures most of the headroom while staying under the pricing cliff. Resolve at Gate 1 / TASK-2.
2. **Reasoning thinking_level: `high` vs `medium`.** `high` is the model default (best quality, highest cost/latency); `medium` (added in 3.1 Pro) trades depth for speed/cost. Default: `high` for the main agent (it's the "strong reasoner" use case), `minimal` for noreason helpers. Resolve at TASK-1.
3. **Model id confirmation.** Target is `gemini-3.1-pro-preview` (verified current pro id). Confirm this is the intended "large" model and not a pinned older `gemini-2.5-pro` or a GA id once GA pricing lands 2026-07-01. Resolve at Gate 1.

## Final — Team Lead
> Gate 1 — PO + TL review required before proceeding.
> Right problem (selectable strong cloud backend, default unchanged)? Correct scope (config entry + context-budget decision + live smoke; vision/calibration/default-swap excluded)?
> Once approved, run: `/orchestrate-plan 2026-06-19-105738-gemini-pro-backend` (critique) then `/orchestrate-dev`.
