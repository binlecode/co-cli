# TODO: Unified Model Build — ModelRegistry

**Task type:** refactor

## Context

`build_model()` and `resolve_role_model()` were added to `_factory.py` as the unified model
construction path. Every call to `build_model()` returns `(model_object, ModelSettings)` — a
two-value tuple that callers must unpack and thread separately through their call chains.

A partial implementation attempted to fix `resolve_role_model()` discarding `ModelSettings` by
threading `(model, model_settings)` as paired parameters through every intermediate function:
sub-agent factory return signatures, delegation tool calls, the full summarization chain in
`_history.py`, and the signal analyzer. This produced signature leakage at every layer.

**Conversation-confirmed design direction:** a `ModelRegistry` pattern (analogous to the
tools registry and skill registry already in the codebase) eliminates the leakage. The registry
is built once from config at session start, stored in `CoServices`, and provides pre-built
`ResolvedModel` objects to any in-session component by role lookup. `model_settings` never
appears in intermediate function signatures.

**DELIVERY-model-roles-schema.md** (the previous delivery) established `role_models`,
`ModelEntry`, and `resolve_role_model`. Those foundations are kept. The registry is the next
layer above them.

**WIP partial changes currently in working tree (wrong direction — to be replaced):**
- `coder.py`, `research.py`, `analysis.py` returning `(agent, model_settings)` tuples
- `delegation.py` threading `model_settings` through delegation calls
- `_history.py` threading `model_settings` through summarization signatures
- `_signal_analyzer.py` partial tuple unpack

**Already shipped and kept:**
- `build_model()` + Gemini `api_params` warning in `_factory.py`
- `agent.py` using `build_model()` for the reasoning model at agent construction time
- `test_build_model_api_params_in_extra_body`

`agent.py`'s direct use of `build_model()` for the reasoning model is kept as-is. The registry
serves in-session callers (history processors, delegation tools, signal analyzer) that have
access to `ctx.deps.services`. Agent construction precedes the session loop and has no `deps`.

---

## Problem & Outcome

**Problem:** `build_model()` returns a two-value tuple `(model, ModelSettings)`. Without a
registry, callers must thread both values through every intermediate function signature between
the resolution point and the final `agent.run()` call. This leaks `model_settings` into
`_history.py`'s summarization chain (4 functions deep), sub-agent factories (return tuple),
delegation calls, and the signal analyzer — code that should not need to know about inference
parameters.

**Outcome:** After these tasks, in-session components ask the registry by role and receive a
`ResolvedModel` (model + settings as one object). No intermediate function carries
`model_settings` as a separate parameter. `build_model()` remains as the single construction
point; the registry owns the lifecycle from config to component.

---

## Scope

In scope:
- `ResolvedModel` dataclass and `ModelRegistry` class in `_factory.py`
- `CoServices.model_registry` field and registry build in `main.py`
- Sub-agent factories (`coder.py`, `research.py`, `analysis.py`) accepting `ResolvedModel`
- Delegation tool using registry for role lookup
- History chain using registry; `_resolve_summarization_model` removed
- Signal analyzer using registry
- Tests for `ModelRegistry`

Out of scope:
- `agent.py` reasoning-model construction (uses `build_model()` directly — intentional)
- `role_models` config schema (already shipped)
- Model fallback/rotation logic in `main.py` (`role_models["reasoning"].pop(0)`)

---

## High-Level Design

```
CoServices
  model_registry: ModelRegistry
    _entries: dict[str, ResolvedModel]
      "reasoning":     ResolvedModel(model, settings)
      "summarization": ResolvedModel(model, settings)
      "coding":        ResolvedModel(model, settings)
      "research":      ResolvedModel(model, settings)
      "analysis":      ResolvedModel(model, settings)

ModelRegistry.from_config(config: CoConfig) -> ModelRegistry
  for each role in config.role_models:
    entry = config.role_models[role][0]
    model, settings = build_model(entry, config.llm_provider, config.ollama_host, config.ollama_num_ctx)
    _entries[role] = ResolvedModel(model, settings)

ModelRegistry.get(role, fallback: ResolvedModel) -> ResolvedModel
  return _entries.get(role, fallback)
```

**`ResolvedModel`** is a simple dataclass (not a wrapper class with `run()`) — it carries
`model: Any` and `settings: ModelSettings | None`. Callers still call `agent.run()` directly
with `model_settings=rm.settings`. This preserves pydantic-ai's API transparency and avoids
a proxy layer.

**Why the registry lives in `CoServices`:** It is a session-scoped runtime handle (built once,
shared by reference via `make_subagent_deps`), not a config scalar. It sits alongside
`knowledge_index` and `task_runner` — the same pattern.

**Fallback contract:** `ModelRegistry.get(role, fallback)` accepts a `ResolvedModel` fallback.
Callers construct `ResolvedModel(model=agent.model, settings=None)` as the fallback when the
role is unconfigured — the same `agent.model` they currently pass as the plain fallback.

---

## Implementation Plan

### TASK-1: Add `ResolvedModel` and `ModelRegistry` to `_factory.py`

**files:**
- `co_cli/agents/_factory.py`

**done_when:**
- `grep -n "class ResolvedModel" co_cli/agents/_factory.py` shows the dataclass definition
- `grep -n "class ModelRegistry" co_cli/agents/_factory.py` shows the class definition
- `grep -n "def from_config" co_cli/agents/_factory.py` shows the classmethod
- `grep -n "def get" co_cli/agents/_factory.py` shows the instance method
- `grep -n "def is_configured" co_cli/agents/_factory.py` shows the method

**Spec:**

Add `ResolvedModel` as a `dataclass` with two fields:
```
model: Any
settings: ModelSettings | None
```

Add `ModelRegistry` class:
- `_entries: dict[str, ResolvedModel]` (private, set by `from_config`)
- `from_config(cls, config) -> ModelRegistry` — classmethod; iterates
  `config.role_models`, calls `build_model()` for each role's head entry, stores
  `ResolvedModel` per role. Roles missing from `config.role_models` are absent from
  `_entries` (not pre-populated with None). Accept `config` as untyped to avoid
  importing `CoConfig` (no circular import risk but keeps `_factory.py` dep-free of `deps.py`).
- `get(self, role: str, fallback: ResolvedModel) -> ResolvedModel` — returns
  `_entries.get(role, fallback)`.
- `is_configured(self, role: str) -> bool` — returns `role in self._entries`. Used by
  delegation callers to check role availability without object-identity comparison.

Remove `resolve_role_model()` — it is replaced by `ModelRegistry.get()`. Update its only
remaining callers (`_history.py`, `_signal_analyzer.py`) in later tasks.

`build_model()` is kept unchanged — it is the internal builder used by `ModelRegistry.from_config()`.

---

### TASK-2: Add `model_registry` to `CoServices`, `model_http_retries` to `CoConfig`, and build registry in `main.py`

**files:**
- `co_cli/deps.py`
- `co_cli/main.py`

**prerequisites:** [TASK-1]

**done_when:**
- `grep -n "model_registry" co_cli/deps.py` shows the field on `CoServices`
- `grep -c "ModelRegistry" co_cli/deps.py` outputs `0` (no module-scope import of `ModelRegistry` in `deps.py`)
- `grep -n "model_http_retries" co_cli/deps.py` shows the field on `CoConfig`
- `grep -n "ModelRegistry.from_config" co_cli/main.py` shows the registry build after config construction

**Spec:**

`CoServices` in `deps.py`: add `model_registry: Any | None = field(default=None, repr=False)`.
Use `Any` annotation — same pattern as `knowledge_index` and `task_runner`. No import of
`ModelRegistry` in `deps.py` at module scope.

`CoConfig` in `deps.py`: add `model_http_retries: int = 2`. This removes the last residual
`from co_cli.config import settings` import from `_history.py` (addressed in TASK-4).

`main.py`: add top-level import `from co_cli.agents._factory import ModelRegistry` (no circular
risk). In `create_deps()`, after `config = CoConfig(...)` is fully constructed, add:
```python
services.model_registry = ModelRegistry.from_config(config)
```
Also add `model_http_retries=settings.model_http_retries` to the `CoConfig(...)` constructor call.

`make_subagent_deps(base)` already shares `services` by reference — sub-agents receive the
same registry with no change needed.

---

### TASK-3: Update sub-agent factories to accept `ResolvedModel`; update delegation to use registry

**files:**
- `co_cli/agents/coder.py`
- `co_cli/agents/research.py`
- `co_cli/agents/analysis.py`
- `co_cli/tools/delegation.py`

**prerequisites:** [TASK-1, TASK-2]

**done_when:**
- `grep -n "ResolvedModel" co_cli/agents/coder.py` shows it in the factory function signature
- `grep -n "ResolvedModel" co_cli/agents/research.py` shows it in the factory function signature
- `grep -n "ResolvedModel" co_cli/agents/analysis.py` shows it in the factory function signature
- `grep -n "is_configured" co_cli/tools/delegation.py` shows guard calls in all three delegation functions
- `uv run pytest tests/test_delegate_coder.py -v` passes

**Spec:**

`make_coder_agent(resolved_model: ResolvedModel) -> Agent[CoDeps, CoderResult]`:
- Remove `(model_entry, provider, ollama_host, ollama_num_ctx)` params; accept one `ResolvedModel`
- Construct `Agent(resolved_model.model, ...)` — return the `Agent` only (not a tuple)
- Caller passes `model_settings=resolved_model.settings` to `agent.run()` at the delegation site

Same change for `make_research_agent` and `make_analysis_agent`.

`delegation.py` — in each of the three delegation functions:
```python
# after (using is_configured for readable guard):
if not ctx.deps.services.model_registry.is_configured("coding"):
    return {"display": "Coder delegation is not configured...", "error": True}
rm = ctx.deps.services.model_registry.get("coding", ResolvedModel(ctx.model, None))
agent = make_coder_agent(rm)
result = await agent.run(task, ..., model_settings=rm.settings)
```

Guard condition parity: `is_configured(role)` replaces the old `not model_pref_list` check and
the `rm is fallback` identity comparison — one readable call, no dual code path. When
`model_registry` is `None` (test contexts without session bootstrap), treat as not configured.

`tests/test_delegate_coder.py` — the three `test_make_*_agent_*` tests call factories with
`(ModelEntry, provider, host)` signature. Update each to pass a `ResolvedModel` instead:
`make_coder_agent(ResolvedModel(model=model_entry.model, settings=None))`.

---

### TASK-4: Collapse `_history.py` summarization chain; update `_commands.py`

**files:**
- `co_cli/_history.py`
- `co_cli/_commands.py`

**prerequisites:** [TASK-1, TASK-2]

**done_when:**
- `grep -n "_resolve_summarization_model" co_cli/_history.py` returns empty (function removed)
- `grep -n "model_registry" co_cli/_history.py` shows registry lookups at `truncate_history_window` and `precompute_compaction` call sites
- `grep -n "ResolvedModel" co_cli/_history.py` shows it in `_run_summarization_with_policy` and `summarize_messages` signatures
- `grep -n "_resolve_summarization_model" co_cli/_commands.py` returns empty (removed from both call sites)
- `grep -n "model_registry" co_cli/_commands.py` shows registry lookups in `_cmd_compact` and `_cmd_new`
- `grep -n "from co_cli.config import settings" co_cli/_history.py` returns empty (direct settings import removed)

**Spec:**

Remove `_resolve_summarization_model()` entirely.

`summarize_messages(messages, resolved_model: ResolvedModel, ...)`:
- Replace `model: str | Any` + separate `model_settings` with one `resolved_model: ResolvedModel`
- `Agent(resolved_model.model, ...)` + `summariser.run(..., model_settings=resolved_model.settings)`

`_run_summarization_with_policy(messages, resolved_model: ResolvedModel, ...)`:
- Same replacement; pass `resolved_model` through to `summarize_messages()`

`_index_session_summary(messages, resolved_model: ResolvedModel, ...)`:
- Same; pass through to `_run_summarization_with_policy()`

`truncate_history_window()`:
```python
fallback = ResolvedModel(model=ctx.model, settings=None)
rm = ctx.deps.services.model_registry.get("summarization", fallback)
summary_text = await _run_summarization_with_policy(
    dropped, rm, max_retries=ctx.deps.config.model_http_retries,
)
```
Replace `from co_cli.config import settings as _settings` with `ctx.deps.config.model_http_retries`.

`precompute_compaction(messages, deps: CoDeps, model)`:
- `model` param kept as fallback only
- `rm = deps.services.model_registry.get("summarization", ResolvedModel(model, None))`
- Replace `_settings.model_http_retries` with `deps.config.model_http_retries`

`_commands.py` — `_cmd_compact` and `_cmd_new`:
```python
from co_cli._history import _run_summarization_with_policy
from co_cli.agents._factory import ResolvedModel
fallback = ResolvedModel(model=ctx.agent.model, settings=None)
rm = ctx.deps.services.model_registry.get("summarization", fallback)
summary = await _run_summarization_with_policy(
    ctx.message_history, rm, max_retries=ctx.deps.config.model_http_retries,
)
```
Remove the existing `from co_cli._history import _resolve_summarization_model` imports and the
`settings.model_http_retries` reference in `_cmd_compact`.

---

### TASK-5: Update `_signal_analyzer.py` to use registry

**files:**
- `co_cli/_signal_analyzer.py`
- `co_cli/main.py`

**prerequisites:** [TASK-1, TASK-2]

**done_when:**
- `grep -n "model_registry" co_cli/_signal_analyzer.py` shows registry lookup in `analyze_for_signals`
- `grep -n "resolve_role_model" co_cli/_signal_analyzer.py` returns empty (removed)
- `uv run pytest tests/test_signal_analyzer.py -v` passes

**Spec:**

`analyze_for_signals` currently takes `(messages, model, *, config: CoConfig)`. Change to
`(messages, model, *, services: CoServices)`:
- `fallback = ResolvedModel(model=model, settings=None)`
- `rm = services.model_registry.get("analysis", fallback)`
- `signal_agent.run(window, model_settings=rm.settings)`

`main.py` call site: change `config=deps.config` to `services=deps.services`. The `model`
positional argument remains `agent.model` (unchanged).

`TYPE_CHECKING` import: replace `CoConfig` with `CoServices` in the `if TYPE_CHECKING:` block
in `_signal_analyzer.py`. `CoConfig` is removed entirely from this file — it is no longer
referenced after the signature change.

---

### TASK-6: Tests for `ModelRegistry`

**files:**
- `tests/test_model_roles_config.py`

**prerequisites:** [TASK-1, TASK-2]

**done_when:**
- `uv run pytest tests/test_model_roles_config.py -v` passes with at least:
  - `test_model_registry_builds_from_config` — verifies `ModelRegistry.from_config()` populates expected roles
  - `test_model_registry_get_fallback_when_unconfigured` — verifies `.get()` returns the fallback `ResolvedModel` when role is absent

**Spec:**

`test_model_registry_builds_from_config`:
- Construct a `CoConfig` with at least one `role_models` entry (e.g. `reasoning` with
  a real model name from the Ollama defaults in `config.py`)
- Call `ModelRegistry.from_config(config)`
- Assert `registry.get("reasoning", fallback_rm) is not fallback_rm` (i.e. an entry exists)
- Assert the returned `ResolvedModel` has non-None `model`
- Assert `registry.is_configured("reasoning")` is True

`test_model_registry_get_fallback_when_unconfigured`:
- Construct `ModelRegistry` with empty `role_models`
- Assert `registry.get("analysis", fallback_rm) is fallback_rm`
- Assert `registry.is_configured("analysis")` is False

Both tests use real `CoConfig` with real `build_model()` (Ollama path). No mocks. Construction
only — no network call; `OpenAIChatModel` connects lazily, so no live Ollama required.

---

## Testing

Regression surface (refactor type):
- `tests/test_model_roles_config.py` — registry unit tests (TASK-6)
- `tests/test_delegate_coder.py` — sub-agent factory interface change (TASK-3)
- `tests/test_signal_analyzer.py` — signature change (TASK-5)
- Full suite: no behavior change expected; pre-existing Docker/LLM failures unaffected

---

## Open Questions

None — all design questions resolved through codebase inspection and design discussion.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev unified-model-build`

## PO Verdict

Approved for implementation.

Why this is the right problem:
- The current tuple-based `build_model()` contract is leaking infrastructure detail into user-invisible call paths.
- The proposed registry keeps model selection centralized and reduces refactor risk for future role additions.
- The scope is appropriately narrow: fix in-session model resolution without reopening the already-shipped config schema work.

PO acceptance conditions:
- No user-facing behavior regression in delegation, compaction/summarization, or signal analysis.
- Unconfigured role behavior stays graceful and readable for the user; no crashes from missing registry entries.
- `build_model()` remains the single construction path so provider-specific behavior does not fork again.
- The delivered implementation removes the current WIP tuple-threading direction rather than layering on top of it.

Scope guardrails:
- Do not expand this task into fallback strategy redesign, model rotation changes, or broader agent-construction cleanup.
- Keep `agent.py` reasoning-model bootstrap unchanged, as stated in the plan.

Ship recommendation:
- Proceed with `/orchestrate-dev unified-model-build`.
