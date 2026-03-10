# Tools — Delegation

Sub-agent spawning tools: coder (code analysis), research (web synthesis), and analysis (knowledge-base synthesis). Part of the [Tools index](DESIGN-tools.md).

## Coder Sub-Agent Delegation

### 1. What & How

`delegate_coder` is a tool that spawns a read-only sub-agent to perform code analysis tasks. The sub-agent has access to `list_directory`, `read_file`, and `find_in_files` only — no write tools, no shell. It returns a structured `CoderResult` with summary, diff preview, files touched, and confidence score.

```
delegate_coder(ctx, task, max_requests=10)
  ├── registry = ctx.deps.services.model_registry
  ├── not registry or not registry.is_configured("coding")? → return error dict (disabled)
  ├── rm = registry.get("coding", ResolvedModel(model=ctx.model, settings=None))
  └── make_coder_agent(rm) → agent.run(task, deps=sub_deps, UsageLimits(request_limit),
                                         model_settings=rm.settings)
           └── CoderResult {summary, diff_preview, files_touched, confidence}
```

### 2. Core Logic

**`delegate_coder(ctx, task, max_requests) → dict`** — When `ctx.deps.services.model_registry` is absent or `registry.is_configured("coding")` is false, returns an error dict without raising (clean disable-by-config). Otherwise resolves `rm = registry.get("coding", ResolvedModel(model=ctx.model, settings=None))`, spawns `make_coder_agent(rm)`, and runs it with `UsageLimits(request_limit=max_requests)` and `model_settings=rm.settings`. Returns `display`, `summary`, `diff_preview`, `files_touched`, `confidence`.

**`make_coder_agent(resolved_model: ResolvedModel) → Agent[CoDeps, CoderResult]`** — Constructs a fresh `Agent` with the pre-built `resolved_model.model`, `output_type=CoderResult`. Registers only the three read-only file tools. No write tools, no shell — strict read-only delegation.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `role_models["coding"]` | `CO_MODEL_ROLE_CODING` | `[]` | Coder sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `ResolvedModel`, `ModelRegistry` — session-scoped model registry; `build_model()` — provider-aware model factory |
| `co_cli/agents/coder.py` | `CoderResult` schema, `make_coder_agent(resolved_model: ResolvedModel)` factory |
| `co_cli/tools/delegation.py` | `delegate_coder` tool |
| `co_cli/agent.py` | Registration: `_register(delegate_coder, False)` |
| `co_cli/config.py` | `role_models` setting |
| `co_cli/deps.py` | `role_models`, `ollama_host` in `CoConfig`; `model_registry` in `CoServices` |

---

## Research Sub-Agent Delegation

### 1. What & How

`delegate_research` is a tool that spawns a read-only research sub-agent to perform web research and synthesis tasks. The sub-agent has access to `web_search` and `web_fetch` only — no write tools, no shell, no file access. It returns a structured `ResearchResult` with summary, sources, and confidence score.

```
delegate_research(ctx, query, domains?, max_requests=8)
  ├── registry = ctx.deps.services.model_registry
  ├── not registry or not registry.is_configured("research")? → return error dict (disabled)
  ├── rm = registry.get("research", ResolvedModel(model=ctx.model, settings=None))
  └── make_research_agent(rm) → agent.run(query, deps=sub_deps, UsageLimits(request_limit),
                                           model_settings=rm.settings)
           └── ResearchResult {summary, sources, confidence}
```

### 2. Core Logic

**`delegate_research(ctx, query, domains, max_requests) → dict`** — When `ctx.deps.services.model_registry` is absent or `registry.is_configured("research")` is false, returns an error dict without raising (clean disable-by-config). No fallback to the coding role — research is independently gated. Otherwise resolves `rm = registry.get("research", ResolvedModel(model=ctx.model, settings=None))`, creates isolated deps via `make_subagent_deps(ctx.deps)`, spawns `make_research_agent(rm)`, and runs it with `UsageLimits(request_limit=max_requests)` and `model_settings=rm.settings`. Returns `display`, `summary`, `sources`, `confidence`.

**`make_research_agent(resolved_model: ResolvedModel) → Agent[CoDeps, ResearchResult]`** — Constructs a fresh `Agent` with `resolved_model.model`, `output_type=ResearchResult`. Registers only `web_search` and `web_fetch`. No write tools, no shell, no file access — strict read-only delegation. Caller passes isolated deps via `make_subagent_deps(ctx.deps)` at run time.

**Empty-result retry:** If the sub-agent returns an empty summary or empty sources list, `delegate_research` retries once with a refined prompt only when `remaining = max_requests - first_run.requests > 0`. If still empty after one retry (or when budget is exhausted), returns `confidence=0.0` with a sentinel summary. Total requests never exceed `max_requests`.

**`ModelRetry` guard:** `max_requests < 1` raises `ModelRetry("max_requests must be at least 1")` — invalid input that the caller can fix by adjusting the parameter.

**Confidence scoring:** `0.0` if summary or sources are empty after retry. Otherwise the sub-agent LLM self-assesses confidence on the `ResearchResult.confidence` field (0.0–1.0). The parent agent may re-delegate with a narrower query if `confidence < 0.4`.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `role_models["research"]` | `CO_MODEL_ROLE_RESEARCH` | `[]` | Research sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `ResolvedModel`, `ModelRegistry` — session-scoped model registry; `build_model()` — provider-aware model factory |
| `co_cli/agents/research.py` | `ResearchResult` schema, `make_research_agent(resolved_model: ResolvedModel)` factory |
| `co_cli/tools/delegation.py` | `delegate_research` tool (extends delegation module) |
| `co_cli/agent.py` | Registration: `_register(delegate_research, False)` |

---

## Analysis Sub-Agent Delegation

### 1. What & How

`delegate_analysis` is a tool that spawns a read-only analysis sub-agent to perform knowledge-base and Drive synthesis tasks. The sub-agent has access to `search_knowledge` and `search_drive_files` only — no write tools, no shell, no network. It returns a structured `AnalysisResult` with conclusion, evidence list, and reasoning chain.

```
delegate_analysis(ctx, question, inputs?, max_requests=8)
  ├── registry = ctx.deps.services.model_registry
  ├── not registry or not registry.is_configured("analysis")? → return error dict (disabled)
  ├── rm = registry.get("analysis", ResolvedModel(model=ctx.model, settings=None))
  └── make_analysis_agent(rm) → agent.run(scoped_question, deps=sub_deps, UsageLimits(request_limit),
                                           model_settings=rm.settings)
           └── AnalysisResult {conclusion, evidence, reasoning}
```

### 2. Core Logic

**`delegate_analysis(ctx, question, inputs, max_requests) → dict`** — When `ctx.deps.services.model_registry` is absent or `registry.is_configured("analysis")` is false, returns an error dict without raising (clean disable-by-config). `max_requests < 1` raises `ModelRetry("max_requests must be at least 1")`. If `inputs` is provided, prepends `"Context:\n" + "\n".join(inputs) + "\n\nQuestion: "` to `question` before running. Resolves `rm = registry.get("analysis", ResolvedModel(model=ctx.model, settings=None))`, creates isolated deps via `make_subagent_deps(ctx.deps)`, spawns `make_analysis_agent(rm)`, and runs it with `UsageLimits(request_limit=max_requests)` and `model_settings=rm.settings`. Returns `display`, `conclusion`, `evidence`, `reasoning`.

**`make_analysis_agent(resolved_model: ResolvedModel) → Agent[CoDeps, AnalysisResult]`** — Constructs a fresh `Agent` with `resolved_model.model`, `output_type=AnalysisResult`. Registers only `search_knowledge` and `search_drive_files`. No write tools, no shell, no network — strict read-only delegation. Caller passes isolated deps via `make_subagent_deps(ctx.deps)` at run time.

**No empty-result retry:** Analysis always produces a conclusion — unlike web search, there is no "no results" failure mode. The sub-agent synthesizes from whatever evidence is found.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `role_models["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | `[]` | Analysis sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `ResolvedModel`, `ModelRegistry` — session-scoped model registry; `build_model()` — provider-aware model factory |
| `co_cli/agents/analysis.py` | `AnalysisResult` schema, `make_analysis_agent(resolved_model: ResolvedModel)` factory |
| `co_cli/tools/delegation.py` | `delegate_analysis` tool (extends delegation module) |
| `co_cli/agent.py` | Registration: `_register(delegate_analysis, False)` |
| `co_cli/config.py` | `role_models` setting, `CO_MODEL_ROLE_ANALYSIS` env var |
| `co_cli/deps.py` | `role_models`, `ollama_host` in `CoConfig`; `model_registry` in `CoServices` |
