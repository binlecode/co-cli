# Tools — Delegation

Sub-agent spawning tools: coder (code analysis), research (web synthesis), and analysis (knowledge-base synthesis). Part of the [Tools index](DESIGN-tools.md).

## Coder Sub-Agent Delegation

### 1. What & How

`delegate_coder` is a tool that spawns a read-only sub-agent to perform code analysis tasks. The sub-agent has access to `list_directory`, `read_file`, and `find_in_files` only — no write tools, no shell. It returns a structured `CoderResult` with summary, diff preview, files touched, and confidence score.

```
delegate_coder(ctx, task, max_requests=10)
  ├── ctx.deps.model_roles.get("coding") empty? → return error dict (disabled)
  ├── model_name = model_roles["coding"][0]
  └── make_coder_agent(model_name, provider, ollama_host) → agent.run(task, UsageLimits(request_limit))
           └── CoderResult {summary, diff_preview, files_touched, confidence}
```

### 2. Core Logic

**`delegate_coder(ctx, task, max_requests) → dict`** — When `ctx.deps.model_roles.get("coding")` is empty, returns an error dict without raising (clean disable-by-config). Otherwise selects `model_roles["coding"][0]`, spawns `make_coder_agent(model_name, provider, ollama_host)`, and runs it with `UsageLimits(request_limit=max_requests)`. Returns `display`, `summary`, `diff_preview`, `files_touched`, `confidence`.

**`make_coder_agent(model_name, provider, ollama_host) → Agent[CoDeps, CoderResult]`** — Calls `make_subagent_model(model_name, provider, ollama_host)` to build the provider-aware model object, then creates a fresh `Agent` with `output_type=CoderResult`. Registers only the three read-only file tools. No write tools, no shell — strict read-only delegation.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `model_roles["coding"]` | `CO_MODEL_ROLE_CODING` | `[]` | Coder sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `make_subagent_model(model_name, provider, ollama_host)` — provider-aware model factory |
| `co_cli/agents/coder.py` | `CoderResult` schema, `make_coder_agent(model_name, provider, ollama_host)` factory |
| `co_cli/tools/delegation.py` | `delegate_coder` tool |
| `co_cli/agent.py` | Registration: `_register(delegate_coder, False)` |
| `co_cli/config.py` | `model_roles` setting |
| `co_cli/deps.py` | `model_roles` dict field, `ollama_host` field |

---

## Research Sub-Agent Delegation

### 1. What & How

`delegate_research` is a tool that spawns a read-only research sub-agent to perform web research and synthesis tasks. The sub-agent has access to `web_search` and `web_fetch` only — no write tools, no shell, no file access. It returns a structured `ResearchResult` with summary, sources, and confidence score.

```
delegate_research(ctx, query, domains?, max_requests=8)
  ├── ctx.deps.model_roles.get("research") empty? → return error dict (disabled)
  ├── model_name = model_roles["research"][0]
  └── make_research_agent(model_name, provider, ollama_host) → agent.run(query, deps=sub_deps, UsageLimits(request_limit))
           └── ResearchResult {summary, sources, confidence}
```

### 2. Core Logic

**`delegate_research(ctx, query, domains, max_requests) → dict`** — When `ctx.deps.model_roles.get("research")` is empty, returns an error dict without raising (clean disable-by-config). No fallback to the coding role — research is independently gated. Otherwise selects `model_roles["research"][0]`, creates isolated deps via `make_subagent_deps(ctx.deps)`, spawns `make_research_agent(model_name, provider, ollama_host)`, and runs it with `UsageLimits(request_limit=max_requests)`. Returns `display`, `summary`, `sources`, `confidence`.

**`make_research_agent(model_name, provider, ollama_host) → Agent[CoDeps, ResearchResult]`** — Calls `make_subagent_model(model_name, provider, ollama_host)` to build the provider-aware model object, then creates a fresh `Agent` with `output_type=ResearchResult`. Registers only `web_search` and `web_fetch`. No write tools, no shell, no file access — strict read-only delegation. Caller passes isolated deps via `make_subagent_deps(ctx.deps)` at run time.

**Empty-result retry:** If the sub-agent returns an empty summary or empty sources list, `delegate_research` retries once with a refined prompt only when `remaining = max_requests - first_run.requests > 0`. If still empty after one retry (or when budget is exhausted), returns `confidence=0.0` with a sentinel summary. Total requests never exceed `max_requests`.

**`ModelRetry` guard:** `max_requests < 1` raises `ModelRetry("max_requests must be at least 1")` — invalid input that the caller can fix by adjusting the parameter.

**Confidence scoring:** `0.0` if summary or sources are empty after retry. Otherwise the sub-agent LLM self-assesses confidence on the `ResearchResult.confidence` field (0.0–1.0). The parent agent may re-delegate with a narrower query if `confidence < 0.4`.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `model_roles["research"]` | `CO_MODEL_ROLE_RESEARCH` | `[]` | Research sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `make_subagent_model(model_name, provider, ollama_host)` — provider-aware model factory |
| `co_cli/agents/research.py` | `ResearchResult` schema, `make_research_agent(model_name, provider, ollama_host)` factory |
| `co_cli/tools/delegation.py` | `delegate_research` tool (extends delegation module) |
| `co_cli/agent.py` | Registration: `_register(delegate_research, False)` |

---

## Analysis Sub-Agent Delegation

### 1. What & How

`delegate_analysis` is a tool that spawns a read-only analysis sub-agent to perform knowledge-base and Drive synthesis tasks. The sub-agent has access to `search_knowledge` and `search_drive_files` only — no write tools, no shell, no network. It returns a structured `AnalysisResult` with conclusion, evidence list, and reasoning chain.

```
delegate_analysis(ctx, question, inputs?, max_requests=8)
  ├── ctx.deps.model_roles.get("analysis") empty? → return error dict (disabled)
  ├── model_name = model_roles["analysis"][0]
  └── make_analysis_agent(model_name, provider, ollama_host) → agent.run(scoped_question, deps=sub_deps, UsageLimits(request_limit))
           └── AnalysisResult {conclusion, evidence, reasoning}
```

### 2. Core Logic

**`delegate_analysis(ctx, question, inputs, max_requests) → dict`** — When `ctx.deps.model_roles.get("analysis")` is empty, returns an error dict without raising (clean disable-by-config). `max_requests < 1` raises `ModelRetry("max_requests must be at least 1")`. If `inputs` is provided, prepends `"Context:\n" + "\n".join(inputs) + "\n\nQuestion: "` to `question` before running. Selects `model_roles["analysis"][0]`, creates isolated deps via `make_subagent_deps(ctx.deps)`, spawns `make_analysis_agent(model_name, provider, ollama_host)`, and runs it with `UsageLimits(request_limit=max_requests)`. Returns `display`, `conclusion`, `evidence`, `reasoning`.

**`make_analysis_agent(model_name, provider, ollama_host) → Agent[CoDeps, AnalysisResult]`** — Calls `make_subagent_model(model_name, provider, ollama_host)` to build the provider-aware model object, then creates a fresh `Agent` with `output_type=AnalysisResult`. Registers only `search_knowledge` and `search_drive_files`. No write tools, no shell, no network — strict read-only delegation. Caller passes isolated deps via `make_subagent_deps(ctx.deps)` at run time.

**No empty-result retry:** Analysis always produces a conclusion — unlike web search, there is no "no results" failure mode. The sub-agent synthesizes from whatever evidence is found.

### 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `model_roles["analysis"]` | `CO_MODEL_ROLE_ANALYSIS` | `[]` | Analysis sub-agent model chain within the active provider. Empty = disabled; head model is used |

### 4. Files

| File | Purpose |
|------|---------|
| `co_cli/agents/_factory.py` | `make_subagent_model(model_name, provider, ollama_host)` — provider-aware model factory |
| `co_cli/agents/analysis.py` | `AnalysisResult` schema, `make_analysis_agent(model_name, provider, ollama_host)` factory |
| `co_cli/tools/delegation.py` | `delegate_analysis` tool (extends delegation module) |
| `co_cli/agent.py` | Registration: `_register(delegate_analysis, False)` |
| `co_cli/config.py` | `model_roles` setting, `CO_MODEL_ROLE_ANALYSIS` env var |
| `co_cli/deps.py` | `model_roles` dict field, `ollama_host` field |
