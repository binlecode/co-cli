# Plan: Agentic / Functional LLM Split

**Task type:** code-refactor

## Context

co-cli currently conflates two distinct LLM use patterns behind a single abstraction (pydantic-ai `Agent`):

1. **Agentic calls** — tool loop, decision-making, needs reasoning (e.g. knowledge extractor, dream miner, `research_web`, `analyze_knowledge`).
2. **Functional calls** — single prompt→response, no tools, no loop (e.g. summarization, dream merge).

All four module-level singleton agents pass `model_settings=NOREASON_SETTINGS` at their `.run()` call sites, forcing `reasoning_effort: "none"` via a **system-wide constant** that bakes Ollama-specific `extra_body` keys into the default and has no path for provider-specific noreason quirks (e.g. Gemini `thinking_config`). The reason/noreason decision is scattered across 14 references (9 call sites + 5 imports) in 6 files, and the constant's existence forces noreason to look like an override rather than a first-class model-interface property.

### Peer-repo convergence (2026 frontier practice)

| Pattern | hermes-agent | fork-claude-code | codex | co-cli today |
|---|---|---|---|---|
| Module-level singleton agents | none | none | none | **4** |
| Dedicated functional LLM primitive | `async_call_llm()` | `queryHaiku()` | `compact_input()` | **none** |
| Reason = default for agentic | ✓ | ✓ | ✓ | ✗ |
| Noreason = default for functional | ✓ | ✓ | ✓ | inconsistent |
| Noreason is a model-interface property (per-provider) | ✓ | ✓ | ✓ | ✗ (system-wide constant) |
| Reasoning is per-call config | ✓ | ✓ | ✓ | ✓ |

**Workflow artifact hygiene:** This plan supersedes `docs/exec-plans/active/2026-04-18-141500-subagent-thinking.md` (narrower scope — removed).

**Current-state validation:** Codebase map confirmed against source — 4 singletons, 14 `NOREASON_SETTINGS` references (9 runtime + 5 imports) across 5 co_cli/ files plus `config/_llm.py` definition, 3 delegation agents. OTel span surface is safe: explicit spans (`co.dream.mine`, `co.memory.extraction`, `research_web`, `analyze_knowledge`, `reason_about`) are preserved; implicit pydantic-ai `invoke_agent {name}` spans for summarizer/merge disappear, and nothing in the codebase parses them. Dead scaffolding inventory: `LlmSettings.noreason_model_settings()` and `build_noreason_model_settings()` at `_llm.py:179,242` already exist with zero runtime callers — ready to be re-wired and populated onto `LlmModel`.

## Problem & Outcome

**Problem:**
1. Four module-level singleton agents bake tools at import time — test isolation debt, flexibility debt.
2. `_summarizer_agent` and `_dream_merge_agent` are functional LLM calls wrapped unnecessarily in an agent shell.
3. Reason/noreason is a per-call-site decision tangled into `model_settings` overrides rather than an architectural property of the call type.
4. Agentic sub-agents (miner, extractor, research_web, analyze_knowledge) are forced noreason despite performing semantic synthesis work that benefits from thinking.
5. `NOREASON_SETTINGS` is a system-wide constant that hardcodes Ollama `extra_body` quirks. It cannot express per-provider noreason semantics (e.g. Gemini `thinking_config`) and positions noreason as an exceptional override instead of a legitimate model-interface mode.

**Failure cost:** Architectural debt; the agent abstraction is overloaded; reasoning decisions are opaque; sub-agent quality cannot be introspected when thinking is suppressed; provider portability is blocked by a constant that only works for Ollama.

**Outcome:**
1. `LlmModel` gains `settings_noreason: ModelSettings | None` — symmetric with `settings` (reasoning). Both are pre-resolved at `build_model()` time with provider-specific defaults. Noreason becomes a first-class model-interface property.
2. New `llm_call()` primitive in `co_cli/llm/_call.py` — single prompt→response, no tools; uses `deps.model.settings_noreason` as its default settings.
3. Functional call sites (summarizer, merge) use `llm_call()` directly; their agent shells are deleted.
4. Agentic singletons are replaced by per-call factory functions (`build_*_agent()`).
5. Agentic delegation call sites (`research_web`, `analyze_knowledge`) pass `ctx.deps.model.settings` — matching `reason_about`. Reasoning is the default for delegation.
6. `NOREASON_SETTINGS` **constant is deleted**. Noreason resolution lives in `config/_llm.py` via a new `_PROVIDER_NOREASON_DEFAULTS` table and `resolve_noreason_inference(llm)` — symmetric with the reasoning resolution path.

## Scope

**In scope:**
- Elevate noreason to a first-class `LlmModel` interface property (`_PROVIDER_NOREASON_DEFAULTS`, `resolve_noreason_inference`, `LlmModel.settings_noreason`).
- Add `llm_call()` primitive in `co_cli/llm/_call.py`.
- Migrate `_summarizer_agent` → `llm_call()`; delete shell.
- Migrate `_dream_merge_agent` → `llm_call()`; delete shell.
- Convert `_dream_miner_agent` → `build_dream_miner_agent()` factory; enable reason.
- Convert `_knowledge_extractor_agent` → `build_knowledge_extractor_agent()` factory; enable reason.
- Migrate `research_web` and `analyze_knowledge` delegation call sites to pass `ctx.deps.model.settings` (reasoning), matching `reason_about`.
- Delete `NOREASON_SETTINGS` constant and migrate all consumers (co_cli, tests, evals).
- Auto-sync specs (`compaction.md`, `cognition.md`, `tools.md`, `llm-models.md`) via `sync-doc` after delivery.

**Out of scope:**
- `reason_about` delegation agent — already uses `ctx.deps.model.settings`; no change needed.
- Main orchestrator agent — already uses reason.
- `_delegate_agent()` signature — unchanged; it's a DRY utility whose `model_settings` parameter still flows explicit per-role settings.
- `build_agent()` factory in `agent/_core.py` — unchanged.
- Gemini-live integration testing — Gemini noreason settings are wired from Google's official docs (Gemini 3 Flash → `thinking_level="minimal"`; Gemini 3 Pro → `thinking_level="low"`; Gemini 2.5 Flash → `thinking_budget=0`), but no CI test exercises a live Gemini call. A future follow-up can add a `@pytest.mark.gemini` integration test analogous to `test_thinking_capture.py` once Gemini-backed noreason is a production path.
- Thinking-token count reporting — deferred per `llm-call-audit-gaps` Gap 1.
- Delete the superseded `2026-04-18-141500-subagent-thinking.md` plan file (done as prep).

## Behavioral Constraints

- Noreason resolution produces the same final `ModelSettings` shape as today for Ollama users (same keys, same values) — the Ollama `extra_body` payload moves from a hand-built constant into the provider-defaults table. User `settings.json` overrides under `llm.noreason.*` continue to win.
- Gemini noreason settings follow Google's official thinking docs (`ai.google.dev/gemini-api/docs/thinking`): Gemini 3 Flash uses `thinking_level="minimal"`; Gemini 3 Pro uses `thinking_level="low"` (the minimum supported level — "minimal" is not supported for Pro); Gemini 2.5 Flash / Flash-Lite use `thinking_budget=0`. Values are wired through pydantic-ai's `GoogleModelSettings.google_thinking_config` (the native Gemini path), not `ModelSettings.extra_body` (the OpenAI-compat channel).
- No tool behavior changes (`save_knowledge` unchanged).
- Each TASK must ship independently with its own test gate (phased, not big-bang).
- Agentic calls incur latency from thinking — acceptable: dream runs at session end, extraction is fire-and-forget, delegation agents are async.
- Functional calls stay noreason — summarization is latency-critical, merge is template-driven text consolidation.
- OTel span preservation: explicit co-cli spans unchanged; implicit pydantic-ai `invoke_agent _summarizer_agent` and `invoke_agent _dream_merge_agent` spans disappear (unreferenced).
- Naming: agent identifiers keep `_agent` suffix (`*_agent` for instances, `build_*_agent()` for factories); functional primitive is `llm_call()`; task-specific wrappers remain verb-named (`summarize_messages`, `_merge_cluster`). Model-interface noreason slot is `LlmModel.settings_noreason` (symmetric with `settings`).

## High-Level Design

### Noreason as a first-class `LlmModel` interface property

`config/_llm.py` — provider-aware noreason resolution (symmetric with existing reasoning path). Gemini values sourced from Google's official thinking docs (`ai.google.dev/gemini-api/docs/thinking`):

```python
_PROVIDER_NOREASON_DEFAULTS: dict[str, ModelInference] = {
    "ollama-openai": {
        "temperature": DEFAULT_NOREASON_TEMPERATURE,
        "top_p": DEFAULT_NOREASON_TOP_P,
        "max_tokens": DEFAULT_NOREASON_MAX_TOKENS,
        "extra_body": {
            "reasoning_effort": "none",
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
            "num_ctx": 131072,
            "num_predict": 16384,
        },
    },
    "gemini": {
        "temperature": DEFAULT_NOREASON_TEMPERATURE,
        "top_p": DEFAULT_NOREASON_TOP_P,
        "max_tokens": DEFAULT_NOREASON_MAX_TOKENS,
        # Gemini 3 Flash-class default. Goes into GoogleModelSettings
        # google_thinking_config (not ModelSettings.extra_body — Gemini
        # uses the native path, not the OpenAI-compat channel).
        "thinking_config": {"thinking_level": "minimal"},
    },
}

# Model-specific noreason overrides — Gemini 3 Pro does not support
# "minimal"; "low" is the minimum reasoning setting (per Google docs).
# Gemini 2.5 Flash/Flash-Lite use thinkingBudget=0 instead of thinkingLevel.
_MODEL_NOREASON_DEFAULTS: dict[tuple[str, str], ModelInference] = {
    ("gemini", "gemini-3-pro-preview"): {
        "thinking_config": {"thinking_level": "low"},
    },
    ("gemini", "gemini-2.5-flash"): {
        "thinking_config": {"thinking_budget": 0},
    },
    ("gemini", "gemini-2.5-flash-lite"): {
        "thinking_config": {"thinking_budget": 0},
    },
}

def resolve_noreason_inference(llm: LlmSettings) -> ModelInference:
    normalized_model = normalize_model_name(llm.model)
    provider_defaults = _PROVIDER_NOREASON_DEFAULTS.get(llm.provider, {})
    model_defaults = _MODEL_NOREASON_DEFAULTS.get((llm.provider, normalized_model), {})
    resolved = _merge_inference(provider_defaults, model_defaults)
    explicit = llm.noreason.model_dump(exclude_defaults=True, exclude_none=True)
    return _merge_inference(resolved, explicit)
```

`ModelInference` TypedDict gains one optional field: `thinking_config: dict[str, Any]` (provider-neutral name). Ollama entries never populate it; Gemini entries always do.

### Settings-object construction — provider branching

`LlmSettings.noreason_model_settings()` branches by provider because the two backends use different settings types:

```python
def noreason_model_settings(self) -> ModelSettings:
    inference = resolve_noreason_inference(self)
    if self.uses_gemini():
        from pydantic_ai.models.google import GoogleModelSettings
        kwargs: dict[str, Any] = {
            "temperature": inference["temperature"],
            "top_p": inference["top_p"],
            "max_tokens": inference["max_tokens"],
        }
        if "thinking_config" in inference:
            kwargs["google_thinking_config"] = dict(inference["thinking_config"])
        return GoogleModelSettings(**kwargs)
    # ollama-openai (and future OpenAI-compat providers)
    return ModelSettings(
        temperature=inference["temperature"],
        top_p=inference["top_p"],
        max_tokens=inference["max_tokens"],
        extra_body=dict(inference.get("extra_body", {})),
    )
```

`GoogleModelSettings` is a `TypedDict` that extends `ModelSettings`, so the return type stays `ModelSettings` — no change to `LlmModel.settings_noreason: ModelSettings | None`. The symmetric `reasoning_model_settings()` stays unchanged (Gemini reasoning uses model defaults today — no explicit thinking config).

`NoReasonSettings` class strips its Ollama-specific field defaults — it becomes a pure-override pydantic model symmetric with `ReasoningSettings`:

```python
class NoReasonSettings(BaseModel):
    """Optional explicit overrides for non-reasoning helper calls."""
    model_config = ConfigDict(extra="ignore")
    temperature: float | None = Field(default=None)
    top_p: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    extra_body: dict[str, Any] = Field(default_factory=dict)
```

`LlmSettings.noreason_model_settings()` is rewritten to use `resolve_noreason_inference(self)` instead of today's Ollama-only `build_noreason_model_settings(self)` (which is deleted).

### `LlmModel` standard interface

`co_cli/llm/_factory.py`:

```python
@dataclass
class LlmModel:
    model: Any
    settings: ModelSettings | None           # reasoning — agentic call default
    settings_noreason: ModelSettings | None  # noreason — functional call default
    context_window: int | None = None
```

`build_model()` populates both slots in each provider branch via `llm.reasoning_model_settings()` and `llm.noreason_model_settings()`. Provider-specific noreason quirks flow out of the config table; the factory stays provider-agnostic in its plumbing.

### New primitive: `llm_call()`

`co_cli/llm/_call.py`:

```python
from typing import TypeVar
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.settings import ModelSettings

from co_cli.deps import CoDeps

T = TypeVar("T", default=str)

async def llm_call(
    deps: CoDeps,
    prompt: str,
    *,
    instructions: str | None = None,
    message_history: list[ModelMessage] | None = None,
    output_type: type[T] = str,
    model_settings: ModelSettings | None = None,
) -> T:
    """Single prompt→response LLM call. No tools, no agent loop.

    Defaults to deps.model.settings_noreason (per-provider noreason config).
    Callers that need reasoning should build an agent via build_agent() instead.
    """
    agent = Agent(
        deps.model.model,
        output_type=output_type,
        instructions=instructions,
    )
    result = await agent.run(
        prompt,
        message_history=message_history,
        model_settings=model_settings or deps.model.settings_noreason,
    )
    return result.output
```

No `NOREASON_SETTINGS` import. Noreason comes from the model instance — provider-specific quirks baked in at `build_model()` time. `deps.model` is a bootstrap invariant; fail-fast `AttributeError` if absent (matches CLAUDE.md "fail-fast over redundant fallbacks").

Uses pydantic-ai `Agent` as the underlying transport (consistent with project stack), but the signature and defaults express functional intent. No tools, no processors. Constructed per call — no module-level state.

### Factory functions for agentic singletons

`co_cli/knowledge/_dream.py`:

```python
def build_dream_miner_agent() -> Agent[CoDeps, str]:
    return Agent[CoDeps, str](
        instructions=_DREAM_PROMPT_PATH.read_text(),
        tools=[save_knowledge],
    )
```

`co_cli/knowledge/_distiller.py`:

```python
def build_knowledge_extractor_agent() -> Agent[CoDeps, str]:
    return Agent[CoDeps, str](
        instructions=_PROMPT_PATH.read_text(),
        tools=[save_knowledge],
    )
```

Factories take no arguments (instructions/tools are fixed); they exist to defer construction to call time, matching the hermes per-call pattern. Instance lifetime = one `.run()` invocation.

### Call-site migration summary

| Call site | Old | New |
|---|---|---|
| `summarization.py:215` | `_summarizer_agent.run(prompt, message_history=msgs, model=m, model_settings=NOREASON_SETTINGS)` | `llm_call(deps, prompt, instructions=_SUMMARIZER_SYSTEM_PROMPT, message_history=msgs)` |
| `_dream.py:368` | `_dream_merge_agent.run(prompt, deps=deps, model=m, model_settings=NOREASON_SETTINGS)` | `llm_call(deps, prompt, instructions=_DREAM_MERGE_PROMPT_PATH.read_text())` |
| `_dream.py:201` | `_dream_miner_agent.run(chunk, deps=deps, model=m, model_settings=NOREASON_SETTINGS)` | `build_dream_miner_agent().run(chunk, deps=deps, model=m)` |
| `_distiller.py:150` | `_knowledge_extractor_agent.run(window, deps=deps, model=m, model_settings=NOREASON_SETTINGS)` | `build_knowledge_extractor_agent().run(window, deps=deps, model=m)` |
| `tools/agents.py:229,247,263` (research_web) | `NOREASON_SETTINGS` (positional) | `ctx.deps.model.settings` (matches `reason_about`) |
| `tools/agents.py:318` (analyze_knowledge) | `NOREASON_SETTINGS` (positional) | `ctx.deps.model.settings` (matches `reason_about`) |

All `llm_call(deps, ...)` invocations use `deps.model.settings_noreason` implicitly — no explicit noreason plumbing at any call site. `_delegate_agent()` itself is unchanged — it remains a DRY utility for agent delegation (OTel span, `fork_deps`, usage merge). Its `model_settings` parameter is still required; only the call-site values change.

## Implementation Plan

### ✓ DONE — TASK-0: Elevate noreason to `LlmModel` standard interface

```
files:
  - co_cli/config/_llm.py
  - co_cli/llm/_factory.py
  - tests/test_llm_inference.py
```

1. **`config/_llm.py`:**
   - Extend `ModelInference` TypedDict with `thinking_config: dict[str, Any]` (optional, provider-neutral).
   - Add `_PROVIDER_NOREASON_DEFAULTS: dict[str, ModelInference]` with the Ollama entry (today's `DEFAULT_NOREASON_EXTRA_BODY` contents) and the Gemini entry (`thinking_config={"thinking_level": "minimal"}` — Gemini 3 Flash default per Google docs).
   - Add `_MODEL_NOREASON_DEFAULTS: dict[tuple[str, str], ModelInference]` — Gemini 3 Pro → `"low"`, Gemini 2.5 Flash / Flash-Lite → `thinking_budget=0` (per Google docs).
   - Add `resolve_noreason_inference(llm: LlmSettings) -> ModelInference` — symmetric with `resolve_reasoning_inference`: provider defaults → model-specific overrides → user-explicit `llm.noreason`.
   - Rewrite `NoReasonSettings` class: all fields become `Optional[...] = None` (temperature/top_p/max_tokens) and `extra_body: dict[str, Any] = {}`. No more Ollama defaults at the config-class layer.
   - Rewrite `LlmSettings.noreason_model_settings()` with provider branching: Gemini returns `GoogleModelSettings` with `google_thinking_config` populated from the resolved `thinking_config`; Ollama returns `ModelSettings` with `extra_body`.
   - Delete `build_noreason_model_settings(llm)` function (superseded).
   - **Retain** `NOREASON_SETTINGS` constant for this task — it's deleted in TASK-6 after all call sites migrate.

2. **`llm/_factory.py`:**
   - Add `settings_noreason: ModelSettings | None = None` field to `LlmModel` dataclass.
   - In `build_model()` Ollama branch: `settings_noreason = llm.noreason_model_settings()`; pass to `LlmModel(...)`.
   - In `build_model()` Gemini branch: same. (`GoogleModelSettings` is assignable to `ModelSettings | None`.)

3. **`tests/test_llm_inference.py`:**
   - Add test: `resolve_noreason_inference("ollama-openai", any-model)` yields `extra_body.reasoning_effort == "none"` and the full Ollama key set.
   - Add test: `resolve_noreason_inference("gemini", "gemini-3-flash-preview")` yields `thinking_config == {"thinking_level": "minimal"}`.
   - Add test: `resolve_noreason_inference("gemini", "gemini-3-pro-preview")` yields `thinking_config == {"thinking_level": "low"}` (model-specific override).
   - Add test: `resolve_noreason_inference("gemini", "gemini-2.5-flash")` yields `thinking_config == {"thinking_budget": 0}`.
   - Add test: `LlmSettings(provider="gemini", ...).noreason_model_settings()` returns a `GoogleModelSettings` with `google_thinking_config` populated.
   - Add test: `LlmSettings(provider="ollama-openai", ...).noreason_model_settings()` returns a `ModelSettings` with `extra_body.reasoning_effort == "none"` and no `google_thinking_config` key.
   - Add test: user-explicit `llm.noreason.extra_body = {"custom": 1}` wins; provider defaults still layer under.
   - Add test: user-explicit `llm.noreason.temperature = 0.3` wins over provider default.
   - Keep existing `test_noreason_settings_match_constants` and `test_noreason_settings_model_defaults_match_constants` — they still validate the constant and the Ollama-default contract until TASK-6 deletes them.

```
done_when: uv run pytest tests/test_llm_inference.py -x passes.
success_signal: LlmModel.settings_noreason is populated for both providers; resolve_noreason_inference layers provider → model → user correctly; Ollama path yields identical final ModelSettings to the legacy NOREASON_SETTINGS constant; Gemini path yields a GoogleModelSettings with the correct google_thinking_config per model.
```

### ✓ DONE — TASK-1: Add `llm_call()` primitive

```
files:
  - co_cli/llm/_call.py (new)
  - tests/test_llm_call.py (new)
prerequisites: [TASK-0]
```

1. Create `co_cli/llm/_call.py` with `llm_call()` function per design above. No `NOREASON_SETTINGS` import — default comes from `deps.model.settings_noreason`.
2. Add a pytest in `tests/test_llm_call.py`:
   - Test single prompt→response with default string output (noreason default via `deps.model.settings_noreason`).
   - Test with `message_history` forwarded correctly.
   - Test `output_type` override returns structured output.
   - Test explicit `model_settings=` override wins over the `deps.model.settings_noreason` default.
   - Use real `CoDeps` per project testing rules — no mocks.

```
done_when: uv run pytest tests/test_llm_call.py -x passes.
success_signal: llm_call() importable from co_cli.llm; callable produces text from prompt; default settings come from deps.model.settings_noreason (no system-wide constant reference).
```

### ✓ DONE — TASK-2: Migrate `_summarizer_agent` to `llm_call()`

```
files:
  - co_cli/context/summarization.py
  - co_cli/context/_history.py
  - co_cli/commands/_commands.py
  - evals/eval_compaction_quality.py
  - evals/eval_ollama_openai_noreason_summarize.py
prerequisites: [TASK-1]
```

**Signature change (decided, not conditional):** `summarize_messages()` new signature is `(deps: CoDeps, messages: list[ModelMessage], *, prompt: str = _SUMMARIZE_PROMPT, personality_active: bool = False, context: str | None = None) -> str`. Rationale: `deps.model.model` is the authoritative model handle and `NOREASON_SETTINGS` is the architectural default for this functional call — every runtime caller passes exactly those two values today, and allowing overrides forces every call site to re-make a decision with one correct answer (evals drifted for this reason). A caller that needs a different model builds a different `deps.model`, per the factory pattern.

1. In `summarization.py`: remove module-level `_summarizer_agent`; rewrite `summarize_messages()` to call `llm_call()` with `_SUMMARIZER_SYSTEM_PROMPT` as instructions. Apply the new signature above.
2. In `_history.py:554-558`: update call site to new signature (pass `deps`); remove `NOREASON_SETTINGS` import if no longer used.
3. In `_commands.py:350`: update call site; remove `NOREASON_SETTINGS` import if no longer used.
4. In `evals/eval_compaction_quality.py`: migrate all 6 `summarize_messages(...)` call sites (lines 1989, 2000, 2465, 2606, 2637, 2678) to the new `(deps, messages, ...)` signature. Replace the direct `_summarizer_agent` import (line 2341) and `_summarizer_agent._instructions` read (line 2432) with equivalent access via `_SUMMARIZER_SYSTEM_PROMPT` (importable from `co_cli.context.summarization`). Remove `NOREASON_SETTINGS` import. This restores CLAUDE.md compliance ("Evals never create their own model or agent settings").
5. In `evals/eval_ollama_openai_noreason_summarize.py`: migrate the call site (line 282); remove `NOREASON_SETTINGS` import.
6. Grep check: no stale `_summarizer_agent` references anywhere; no stale `NOREASON_SETTINGS` imports in touched files.

```
done_when: uv run pytest tests/test_context_compaction.py tests/test_commands.py -x passes; evals smoke-run (import + signature check via python -c) passes.
success_signal: Compaction triggers correctly; /compact command produces summary; no _summarizer_agent identifier remains in co_cli/ or evals/; eval call sites use deps-driven pattern.
```

### ✓ DONE — TASK-3: Migrate `_dream_merge_agent` to `llm_call()`

```
files:
  - co_cli/knowledge/_dream.py
prerequisites: [TASK-1]
```

1. Remove module-level `_dream_merge_agent` definition.
2. Rewrite `_merge_cluster()` to call `llm_call()` with `_DREAM_MERGE_PROMPT_PATH.read_text()` as instructions.
3. Remove `model_settings=NOREASON_SETTINGS` at the call site.
4. Do not yet remove the `NOREASON_SETTINGS` import — still used by `_dream_miner_agent` call site (TASK-4).

```
done_when: uv run pytest tests/test_knowledge_dream_merge.py -x passes.
success_signal: Cluster merging still writes consolidated artifacts; no _dream_merge_agent identifier remains.
```

### ✓ DONE — TASK-4: Convert `_dream_miner_agent` to per-call factory; enable reason

```
files:
  - co_cli/knowledge/_dream.py
prerequisites: [TASK-3]
```

1. Replace module-level `_dream_miner_agent` with `build_dream_miner_agent()` factory function.
2. In `_mine_transcripts()`: call the factory (inside or outside the chunk loop — hoist outside for efficiency; each `.run()` is independent).
3. Remove `model_settings=NOREASON_SETTINGS` at the `.run()` call site.
4. Remove `NOREASON_SETTINGS` import from `_dream.py` — no longer used.

```
done_when: uv run pytest tests/test_knowledge_dream_mine.py -x passes.
success_signal: Dream cycle extracts artifacts from transcripts; OTel spans from miner contain thinking content (manual DB verify after llm-call-audit-gaps TASK-1 ships).
```

### ✓ DONE — TASK-5: Convert `_knowledge_extractor_agent` to per-call factory; enable reason

```
files:
  - co_cli/knowledge/_distiller.py
prerequisites: [TASK-1]
```

1. Replace module-level `_knowledge_extractor_agent` with `build_knowledge_extractor_agent()` factory.
2. In `_run_extraction_async()`: call the factory.
3. Remove `model_settings=NOREASON_SETTINGS` at the `.run()` call site.
4. Remove `NOREASON_SETTINGS` import from `_distiller.py`.

```
done_when: uv run pytest tests/test_distiller_integration.py -x passes.
success_signal: Post-turn extraction writes memory file and advances cursor; thinking content present in extractor OTel spans.
```

### ✓ DONE — TASK-6: Enable reason on delegation agents; delete `NOREASON_SETTINGS` constant

```
files:
  - co_cli/tools/agents.py
  - co_cli/config/_llm.py
  - tests/test_thinking_capture.py
  - tests/test_llm_inference.py
  - tests/test_commands.py
  - tests/test_tool_calling_functional.py
prerequisites: [TASK-1, TASK-2, TASK-3, TASK-4, TASK-5]
```

**Scope clarifier:** `_delegate_agent()` stays unchanged — it's a DRY utility for agent delegation (OTel span, `fork_deps`, usage merge, retry). Its `model_settings: ModelSettings | None` parameter is still required. Only the *values passed at call sites* change.

1. **Delegation call-site migration** (`co_cli/tools/agents.py`):
   - `research_web` primary attempt (line 229): `NOREASON_SETTINGS` → `ctx.deps.model.settings`.
   - `research_web` retry (line 247): same.
   - `research_web` forward to `_delegate_agent` (line 263): same.
   - `analyze_knowledge` (line 318): same.
   - Leave `reason_about` unchanged (already uses `ctx.deps.model.settings`).
   - Remove `NOREASON_SETTINGS` import from `tools/agents.py`.

2. **Delete the constant** (`config/_llm.py`):
   - Remove `NOREASON_SETTINGS` definition at line 52.
   - Remove the leading comment block that references the constant (the "Static constant for callers that don't have LlmSettings at import time..." note).
   - Keep `DEFAULT_NOREASON_*` constants and `DEFAULT_NOREASON_EXTRA_BODY` — they are now consumed by `_PROVIDER_NOREASON_DEFAULTS` (from TASK-0).

3. **Migrate remaining test consumers:**
   - `tests/test_thinking_capture.py:17,57`: replace `from co_cli.config._llm import NOREASON_SETTINGS` with `_NOREASON_SETTINGS = _CONFIG.llm.noreason_model_settings()` (module-level, next to `_REASON_SETTINGS`); update `agent.run(..., model_settings=NOREASON_SETTINGS)` to use the local.
   - `tests/test_llm_inference.py`:
     - Delete `test_noreason_settings_match_constants` (constant gone).
     - Rewrite `test_noreason_settings_model_defaults_match_constants` to validate the new pure-override shape: `NoReasonSettings()` returns all-None fields and empty `extra_body`.
     - Remove `NOREASON_SETTINGS` from the import list.
     - The TASK-0 provider-resolution tests remain — they are the replacement coverage.
   - `tests/test_commands.py:25,47` and `tests/test_tool_calling_functional.py:24,45`: grep the `NOREASON_SETTINGS` usage. These pass it as `deps.model.settings` on a test CoDeps instance — migrate to `deps.model.settings_noreason` or to a fixture helper that builds settings via `llm.reasoning_model_settings()` / `llm.noreason_model_settings()`. Read each test first, confirm the semantics match the new surface.

4. **Final grep checks:**
   - `NOREASON_SETTINGS` returns zero matches across `co_cli/`, `tests/`, `evals/`.
   - `build_noreason_model_settings` returns zero matches (deleted in TASK-0).
   - `from co_cli.config._llm import` lines no longer reference `NOREASON_SETTINGS`.

```
done_when: uv run pytest tests/test_tool_calling_functional.py tests/test_commands.py tests/test_thinking_capture.py tests/test_llm_inference.py -x passes; scripts/quality-gate.sh full green.
success_signal: Delegation agents run with reasoning enabled by default; NOREASON_SETTINGS constant does not exist anywhere in the repo; noreason is fully expressed as LlmModel.settings_noreason (per-provider); existing behavior preserved for Ollama users (final ModelSettings payload byte-identical to pre-refactor).
```

### ✓ DONE — TASK-7: Spec sync (auto via sync-doc after delivery)

```
files:
  - docs/specs/compaction.md
  - docs/specs/cognition.md
  - docs/specs/tools.md
  - docs/specs/llm-models.md
```

`sync-doc` updates:
- `compaction.md:497,590` — `_summarizer_agent` references → `llm_call()` via `summarize_messages()`.
- `cognition.md:285-286` — dream merge/miner/extractor sections reflect functional-call vs per-call-factory split.
- `tools.md:176-232` — delegation agents note reasoning enabled by default; `_delegate_agent()` still carries `model_settings` forward per-role.
- `llm-models.md`:
  - `LlmModel` now has `settings` (reasoning) **and** `settings_noreason` (noreason), both pre-resolved at `build_model()` time.
  - `NoReasonSettings` class is pure-override (no Ollama defaults baked in); provider-specific noreason quirks live in `_PROVIDER_NOREASON_DEFAULTS` and `_MODEL_NOREASON_DEFAULTS`.
  - `NOREASON_SETTINGS` constant no longer exists; references to it must be removed.
  - `summarize_messages()` signature drops `model`/`model_settings` params and takes `deps` instead.
  - New row for Gemini noreason — cite per-model thinking_config matrix from Google docs (Gemini 3 Flash → `thinking_level="minimal"`, Gemini 3 Pro → `thinking_level="low"`, Gemini 2.5 Flash → `thinking_budget=0`). Reference pydantic-ai's `GoogleModelSettings.google_thinking_config` as the wiring path.
  - Current-state rows (lines ~105, 120, 122, 147, 158-159, 234, 237) update to reflect the new model-interface surface.

No structural doc changes; `## Product Intent` sections untouched.

## Testing

- Each TASK ships with its test gate in `done_when`.
- Full suite after TASK-6: `scripts/quality-gate.sh full`.
- Post-delivery manual verification (requires `llm-call-audit-gaps` TASK-1 shipped):
  ```python
  import sqlite3
  db = sqlite3.connect("/Users/binle/.co-cli/co-cli-logs.db")
  rows = db.execute(
      "SELECT DISTINCT p.name FROM spans c JOIN spans p ON c.parent_id = p.span_id"
      " WHERE c.name LIKE 'chat %'"
      "   AND c.attributes LIKE '%\"type\": \"thinking\"%'"
      "   AND c.resource LIKE '%co-cli%'"
      " ORDER BY c.start_time DESC LIMIT 20"
  ).fetchall()
  print(rows)
  ```
  Expected: knowledge extractor, dream miner, research_web, analyze_knowledge appear; summarizer and merge do not (functional calls don't produce thinking).

## Open Questions

None — architecture validated against hermes-agent, fork-claude-code, and codex. All three peers converge on the same pattern implemented here: noreason is a model-interface property resolved per provider, not a system-wide constant.

## Final — Team Lead

Plan approved.

> Gate 1: TL + PO approved — aligned with 2026 frontier practice; noreason elevated to first-class `LlmModel` interface; scope staged for independent shipping (TASK-0 foundation, TASK-1 primitive, TASK-2–5 migrations, TASK-6 constant deletion + test migration, TASK-7 spec sync).
> Run: `/orchestrate-dev agentic-functional-llm-split`

## Implementation Review — 2026-04-19

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-0 | `test_llm_inference.py -x` passes | ✓ pass | `_llm.py:135` `_PROVIDER_NOREASON_DEFAULTS`; `_llm.py:156` `_MODEL_NOREASON_DEFAULTS`; `_llm.py:202` `resolve_noreason_inference()`; `_llm.py:74-82` `NoReasonSettings` pure-override; `_llm.py:265-284` provider-branching `noreason_model_settings()`; `_factory.py:35` `settings_noreason` field |
| TASK-1 | `test_llm_call.py -x` passes | ✓ pass | `_call.py:14` `llm_call()` defined; `_call.py:36` `deps.model.settings_noreason` default |
| TASK-2 | `test_context_compaction.py test_commands.py -x` passes | ✓ pass | `summarization.py` no `_summarizer_agent`; `_history.py` no `NOREASON_SETTINGS` import |
| TASK-3 | `test_knowledge_dream_merge.py -x` passes | ✓ pass | `_dream.py` `_merge_cluster()` calls `llm_call()`; no `_dream_merge_agent` |
| TASK-4 | `test_knowledge_dream_mine.py -x` passes | ✓ pass | `_dream.py:118` `build_dream_miner_agent()` factory; `_dream.py:201` called per session |
| TASK-5 | `test_distiller_integration.py -x` passes | ✓ pass | `_distiller.py:112` `build_knowledge_extractor_agent()` factory; `_distiller.py:152` per-extraction call |
| TASK-6 | full suite + quality gate green | ✓ pass | `agents.py` delegation uses `ctx.deps.model.settings`; grep confirms zero `NOREASON_SETTINGS` matches |
| TASK-7 | spec sync | ✓ pass | `llm-models.md`, `compaction.md` fixed; expanded to `tools.md` for tool rename coverage |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Stale import `run_shell_command` from tool rename | `test_tool_prompt_discovery.py:14` | blocking | Updated to `shell` |
| Stale background task assertions | `test_tool_prompt_discovery.py:45-51` | blocking | Updated to `task_start/task_status/task_cancel/task_list` |
| Stale docstring redirect mentions `start_background_task` | `test_tool_prompt_discovery.py:142` | blocking | Updated to `task_start` |
| Structured output schema field `name` conflicts with prompt "name a color" | `test_llm_call.py:99` | blocking | Changed field to `value`; model reliably produces correct JSON |
| Stale docstring `NOREASON_SETTINGS` reference | `test_thinking_capture.py:5` | minor | Updated to "Noreason settings" |
| tools.md: 8 stale tool names throughout | `docs/specs/tools.md` | blocking (spec accuracy) | Updated `run_shell_command→shell`, `request_user_input→clarify`, `write_todos→todo_write`, `read_todos→todo_read`, `start_background_task→task_start`, `check_task_status→task_status`, `cancel_background_task→task_cancel`, `list_background_tasks→task_list` |

### Tests
- Command: `uv run pytest -x`
- Result: **667 passed, 0 failed**
- Log: `.pytest-logs/*-review-final2.log`
- Note: 1 transient failure on first run (`test_tool_selection_and_arg_extraction[shell_git_status]`) — model saturated after 517 prior tests; passed on targeted retry and on full re-run.

### Doc Sync
- Scope: full — tool rename is a cross-cutting public API change affecting `tools.md`; LLM split touches shared modules (`_llm.py`, `_factory.py`, `summarization.py`)
- Result: fixed — `tools.md` (8 renamed tools); `llm-models.md` and `compaction.md` (from delivery run)

### Behavioral Verification
No user-facing CLI command behavior changed — tool names visible to the LLM changed (model-facing), and llm_call/noreason settings are internal. `uv run co status` not run — no user-facing output surface changed by either delivery. Structural: `shell`, `clarify`, `todo_write`, `todo_read`, `task_start`, `task_status`, `task_cancel`, `task_list` all confirmed registered via `test_tool_prompt_discovery.py` suite (passes in full run).

### Overall: PASS
Both `agentic-functional-llm-split` and `tool-naming-rename` deliveries are clean. 667 tests pass. All stale references resolved. Specs in sync. Ready to ship.

---

## Delivery Summary — 2026-04-19

| Task | done_when | Status |
|------|-----------|--------|
| TASK-0 | `uv run pytest tests/test_llm_inference.py -x` passes | ✓ pass |
| TASK-1 | `uv run pytest tests/test_llm_call.py -x` passes | ✓ pass |
| TASK-2 | `uv run pytest tests/test_context_compaction.py tests/test_commands.py -x` passes | ✓ pass |
| TASK-3 | `uv run pytest tests/test_knowledge_dream_merge.py -x` passes | ✓ pass |
| TASK-4 | `uv run pytest tests/test_knowledge_dream_mine.py -x` passes | ✓ pass |
| TASK-5 | `uv run pytest tests/test_distiller_integration.py -x` passes | ✓ pass |
| TASK-6 | full suite + quality gate green; `NOREASON_SETTINGS` zero matches | ✓ pass |
| TASK-7 | spec sync — `compaction.md`, `llm-models.md` fixed; `cognition.md`, `tools.md` clean | ✓ pass |

**Tests:** full suite — 666 passed, 0 failed (`uv run pytest` run during TASK-6)
**Independent Review:** 1 blocking fixed (missing `None` guard in `_cmd_compact`); 1 minor fixed (`UP037` lint annotation); overall clean after fixes
**Doc Sync:** fixed — `llm-models.md` (NOREASON_SETTINGS removed, LlmModel shape updated, Per-Call Settings rewritten, Section 4 rewritten, Files table updated); `compaction.md` (summarize_messages signature, 2.6 summarizer description, Files table); `cognition.md` clean; `tools.md` clean

**Overall: DELIVERED**
All 7 implementation tasks and spec sync shipped. `NOREASON_SETTINGS` constant deleted from the entire repo; `LlmModel` now carries both `settings` (reasoning) and `settings_noreason` (noreason, provider-resolved at build time); `llm_call()` primitive added; functional agents (summarizer, merge) migrated to `llm_call()`; agentic agents (miner, extractor) converted to per-call factories; delegation agents (`research_web`, `analyze_knowledge`) elevated to reasoning settings.
