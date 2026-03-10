# REVIEW: delivery/unified-model-build — Delivery Audit
_Date: 2026-03-10_

## What Was Scanned

Source modules:
- `co_cli/agents/_factory.py`
- `co_cli/deps.py`
- `co_cli/tools/delegation.py`
- `co_cli/_history.py`
- `co_cli/_commands.py`
- `co_cli/_signal_analyzer.py`
- `co_cli/agents/coder.py`
- `co_cli/agents/research.py`
- `co_cli/agents/analysis.py`

DESIGN docs: all `docs/DESIGN-*.md`

---

## Phase 2 — Feature Inventory

### 2.1 New Public APIs in `_factory.py`

| Item | Description |
|------|-------------|
| `ResolvedModel` | Dataclass pairing a pre-built model object with `ModelSettings | None` |
| `ModelRegistry` | Session-scoped registry of `ResolvedModel` objects keyed by role. Built via `ModelRegistry.from_config(config)`, stored on `CoServices.model_registry`. Methods: `get(role, fallback)`, `is_configured(role)` |
| `build_model(model_entry, provider, ollama_host, ollama_num_ctx)` | Constructs a provider-aware model object and merged `ModelSettings`. Ollama → `OpenAIChatModel` + `OpenAIProvider`. Gemini → `"google-gla:{model_name}"` string. Merge precedence: quirks defaults → quirks extra_body → model_entry.api_params |

### 2.2 New CoServices/CoConfig fields in `deps.py`

| Field | Location | Description |
|-------|----------|-------------|
| `model_registry: ModelRegistry | None` | `CoServices` | Session-scoped registry of pre-built models; shared (not copied) by `make_subagent_deps` |
| `role_models: dict[str, list[ModelEntry]]` | `CoConfig` | Role-to-model-chain mapping (reasoning, coding, research, analysis, summarization) |
| `ollama_host: str` | `CoConfig` | Ollama server base URL, default `"http://localhost:11434"` |
| `llm_provider: str` | `CoConfig` | Provider selector, default `"ollama"` |
| `ollama_num_ctx: int` | `CoConfig` | Context size hint, default `262144` |
| `ctx_warn_threshold: float` | `CoConfig` | Warn threshold for context ratio, default `0.85` |
| `ctx_overflow_threshold: float` | `CoConfig` | Overflow threshold, default `1.0` |
| `model_http_retries: int` | `CoConfig` | Provider retry budget per turn, default `2` |
| `mcp_count: int` | `CoConfig` | Count of configured MCP servers for capability introspection |
| `web_http_max_retries: int` | `CoConfig` | Max HTTP retries for web_fetch, default `2` |
| `web_http_backoff_base_seconds: float` | `CoConfig` | Base backoff interval for web_fetch retries, default `1.0` |
| `web_http_backoff_max_seconds: float` | `CoConfig` | Max backoff cap for web_fetch retries, default `8.0` |
| `web_http_jitter_ratio: float` | `CoConfig` | Jitter fraction for web_fetch backoff, default `0.2` |

### 2.3 Delegation Tools in `delegation.py`

| Tool | Role | Registered |
|------|------|-----------|
| `delegate_coder(ctx, task, max_requests=10)` | Spawns read-only coder sub-agent (list_directory, read_file, find_in_files) | `_register(delegate_coder, False)` in `agent.py` |
| `delegate_research(ctx, query, domains?, max_requests=8)` | Spawns read-only research sub-agent (web_search, web_fetch). Has empty-result retry and ModelRetry guard | `_register(delegate_research, False)` in `agent.py` |
| `delegate_analysis(ctx, question, inputs?, max_requests=8)` | Spawns read-only analysis sub-agent (search_knowledge, search_drive_files). Has ModelRetry guard | `_register(delegate_analysis, False)` in `agent.py` |

### 2.4 History processor and signal analyzer model registry usage

| Component | Registry usage |
|-----------|---------------|
| `_history.py` — `truncate_history_window` | `registry.get("summarization", fallback)` — inline compaction |
| `_history.py` — `precompute_compaction` | `registry.get("summarization", fallback)` — background pre-computation |
| `_signal_analyzer.py` — `analyze_for_signals` | `services.model_registry.get("analysis", ResolvedModel(model, None))` |
| `_commands.py` — `/compact`, `/new` handlers | `registry.get("summarization", fallback)` via `ResolvedModel` |

### 2.5 Sub-agent factories in `agents/*.py`

| Factory | Output type | Tools registered |
|---------|-------------|-----------------|
| `make_coder_agent(resolved_model)` | `CoderResult` (summary, diff_preview, files_touched, confidence) | list_directory, read_file, find_in_files — `requires_approval=False` |
| `make_research_agent(resolved_model)` | `ResearchResult` (summary, sources, confidence) | web_search, web_fetch — `requires_approval=False` |
| `make_analysis_agent(resolved_model)` | `AnalysisResult` (conclusion, evidence, reasoning) | search_knowledge, search_drive_files — `requires_approval=False` |

---

## Phase 3 — Coverage Check

### 3.1 `ResolvedModel`, `ModelRegistry`, `build_model()` — `_factory.py`

**DESIGN-llm-models.md:**
- `ResolvedModel`: Full — dedicated subsection with what it is, how it is built, how it is used.
- `ModelRegistry`: Full — `from_config`, `get(role, fallback)`, `is_configured(role)` all documented. Session-scope, storage on `CoServices.model_registry`, build-once pattern documented.
- `build_model()`: Full — provider branches (ollama, gemini), merge precedence, parameter sources, timeout config, fallback behavior documented.

**DESIGN-tools-delegation.md:**
- `ResolvedModel`/`ModelRegistry` referenced correctly in each of the three delegation sections' files tables.

**DESIGN-core.md:**
- `model_registry: ModelRegistry | None` listed in `CoServices` table. ✓
- `role_models` listed in `CoConfig` table. ✓

**Verdict: Full coverage.**

### 3.2 Delegation tools — `delegation.py`

**DESIGN-tools-delegation.md:**
- `delegate_coder`: Full — dedicated section with What/How diagram, core logic (registry guard, fallback, UsageLimits, return fields), config table, files table.
- `delegate_research`: Full — dedicated section with What/How diagram, core logic including empty-result retry, ModelRetry guard, confidence scoring, config table, files table.
- `delegate_analysis`: Full — dedicated section with What/How diagram, core logic (inputs scoping, no-retry rationale), config table, files table.

**DESIGN-tools.md — approval table:**
- `delegate_coder`, `delegate_research`, `delegate_analysis` listed: `No` approval, rationale: "Spawn isolated sub-agents; `requires_approval=False` — sub-agent tool calls are governed by their own tool registrations". ✓

**DESIGN-core.md §3.1 Native Tool Inventory:**
- All three delegation tools appear in the Delegation row. ✓

**DESIGN-core.md §3.2 Delegated Sub-Agents table:**
- Coder, Research, Analysis all present with correct tool surface. ✓

**Verdict: Full coverage.**

### 3.3 Sub-agent factories — `agents/coder.py`, `agents/research.py`, `agents/analysis.py`

**DESIGN-tools-delegation.md:**
- `make_coder_agent`: Full — tool surface enumerated, output type described, `requires_approval=False` on sub-agent tools noted.
- `make_research_agent`: Full — same treatment; tool surface enumerated.
- `make_analysis_agent`: Full — same treatment; tool surface enumerated.

**Verdict: Full coverage.**

### 3.4 `CoServices.model_registry` field

**DESIGN-core.md:** Listed in CoServices table and mermaid diagram. ✓
**DESIGN-llm-models.md:** Documented in Files table (`model_registry in CoServices`). ✓
**DESIGN-tools-delegation.md:** Referenced in files table for all three delegation sections. ✓

**Verdict: Full coverage.**

### 3.5 `CoConfig` new fields — `deps.py`

Fields added/confirmed in `CoConfig`:

| Field | DESIGN-llm-models.md | DESIGN-core.md CoConfig table | DESIGN-index.md |
|-------|---------------------|-------------------------------|-----------------|
| `role_models` | Full — all 5 env vars, defaults, descriptions | ✓ listed | ✓ listed |
| `ollama_host` | Full — env var, default, description | ✓ | ✓ |
| `llm_provider` | Full | ✓ | ✓ |
| `ollama_num_ctx` | Full — with Modelfile caveat | ✓ | ✓ |
| `ctx_warn_threshold` | Full | ✓ | ✓ |
| `ctx_overflow_threshold` | Full | ✓ | ✓ |
| `model_http_retries` | ✓ in Files table | ✓ | ✓ (multiple docs) |
| `mcp_count` | Not in llm-models (correct — belongs to MCP doc) | ✓ via core.md | ✓ in DESIGN-mcp-client.md |
| `web_http_max_retries` | Not in llm-models (correct — belongs to integrations doc) | ✓ | ✓ in DESIGN-tools-integrations.md |
| `web_http_backoff_base_seconds` | — | ✓ | ✓ |
| `web_http_backoff_max_seconds` | — | ✓ | ✓ |
| `web_http_jitter_ratio` | — | ✓ | ✓ |

All fields are documented in their owning DESIGN doc. Placement is appropriate (web_http_* in DESIGN-tools-integrations.md, mcp_count in DESIGN-mcp-client.md).

**Verdict: Full coverage.**

### 3.6 History processor changes — `_history.py`

Key changes: `summarize_messages` now receives `ResolvedModel`, `truncate_history_window` and `precompute_compaction` use `registry.get("summarization", fallback)`.

**DESIGN-flow-context-governance.md:**
- Processor 4 (`truncate_history_window`) documented including pre-computed compaction path. ✓
- Summarization model selection table present: `truncate_history_window` → `role_models["summarization"]` head, fallback primary. ✓
- Background pre-computation trigger thresholds documented. ✓
- `precompute_compaction` referenced in the message history lifecycle cycle. ✓

**DESIGN-llm-models.md Files table:**
- `co_cli/_history.py` listed with: "summarize_messages(messages, resolved_model, ...) — bare Agent summariser; truncate_history_window uses registry.get('summarization', fallback)..." ✓

**Verdict: Full coverage.**

### 3.7 Signal analyzer changes — `_signal_analyzer.py`

Change: `analyze_for_signals` now uses `services.model_registry.get("analysis", ResolvedModel(model, None))` and passes `model_settings=rm.settings` to `agent.run()`.

**DESIGN-flow-memory-lifecycle.md:**
- Signal detection sequence pseudocode explicitly documents `services.model_registry.get("analysis", ResolvedModel(model, None))`. ✓
- `rm` (ResolvedModel) used in mini-agent run documented. ✓

**Verdict: Full coverage.**

### 3.8 Registration check — `agent.py` `_register(` calls for delegation tools

All three delegation tools registered with `requires_approval=False`:
```
_register(delegate_coder, False)
_register(delegate_research, False)
_register(delegate_analysis, False)
```

**DESIGN-tools.md approval table:** All three listed with `No` approval. ✓
**DESIGN-core.md §3.1:** Delegation row in native tool inventory. ✓

**Verdict: No gap.**

---

## Phase 4 — Second Pass

### 4.1 "Full coverage" items — behavioral descriptions, not just naming

- **`ModelRegistry.from_config`**: DESIGN-llm-models.md §2 "ModelRegistry and ResolvedModel" says "Built once from CoConfig at session start via `ModelRegistry.from_config(config)` and stored on `CoServices.model_registry`." Behavioral. ✓
- **`build_model()` merge precedence**: Explicitly documented in DESIGN-llm-models.md §4. ✓
- **`delegate_research` empty-result retry**: DESIGN-tools-delegation.md Core Logic section describes the retry path, budget math, and fallback confidence=0.0. ✓
- **`delegate_analysis` inputs scoping**: `"Context:\n" + "\n".join(inputs) + "\n\nQuestion: "` behavior documented in core logic. ✓
- **`truncate_history_window` pre-computed compaction consumption**: DESIGN-flow-context-governance.md Processor 4 section and DESIGN-flow-context-governance.md §Part 4 message history lifecycle both describe the check and consume path. ✓

### 4.2 Config settings without env vars

All settings in `CoConfig` that were added by this delivery have env var mappings documented:
- `role_models.*` roles: `CO_MODEL_ROLE_*` env vars documented in DESIGN-llm-models.md §3.
- `model_http_retries`: `CO_CLI_MODEL_HTTP_RETRIES` documented in DESIGN-core-loop.md and DESIGN-index.md.
- `web_http_*`: All four `CO_CLI_WEB_HTTP_*` env vars documented in DESIGN-tools-integrations.md and DESIGN-index.md.
- `mcp_count`: No env var (set programmatically from `len(settings.mcp_servers)` at `create_deps()` time). DESIGN-mcp-client.md correctly omits an env var column entry for this field. ✓

### 4.3 Agent tools registered but missing from DESIGN-tools.md approval table

Check all `_register(` calls in `agent.py` for delegation tools:

| Tool | In approval table | Approval value |
|------|-----------------|----------------|
| `delegate_coder` | ✓ DESIGN-tools.md | No |
| `delegate_research` | ✓ DESIGN-tools.md | No |
| `delegate_analysis` | ✓ DESIGN-tools.md | No |

**No gaps found.**

### 4.4 Sub-agent tool registrations vs DESIGN docs

Sub-agent tools are registered directly on sub-agent agents (not on the main agent), so they do not appear in the main approval table. This is correct per architecture. DESIGN-core.md §3.2 documents the sub-agent tool surfaces explicitly. DESIGN-tools-delegation.md documents each sub-agent's tool surface in its respective Core Logic section.

No discrepancy found.

---

## Phase 5 — Verdict

**VERDICT: HEALTHY**

**Blocking issues: 0**
**Minor issues: 0**

### Summary

The unified-model-build delivery introduced a clean model factory architecture (`ResolvedModel`, `ModelRegistry`, `build_model()`), three delegation tools (`delegate_coder`, `delegate_research`, `delegate_analysis`), three sub-agent factories (`make_coder_agent`, `make_research_agent`, `make_analysis_agent`), registry-based model resolution in history processors and the signal analyzer, and several new `CoConfig`/`CoServices` fields.

All inventoried items have full coverage across their owning DESIGN docs:
- `DESIGN-llm-models.md` — complete spec for `ResolvedModel`, `ModelRegistry`, `build_model()`, all role model config settings and env vars.
- `DESIGN-tools-delegation.md` — complete spec for all three delegation tools and sub-agent factories, including non-obvious behaviors (empty-result retry in research, inputs scoping in analysis, ModelRetry guards).
- `DESIGN-tools.md` — approval table includes all three delegation tools with correct approval=No rationale.
- `DESIGN-core.md` — `CoServices.model_registry` and `CoConfig.role_models` appear in the CoDeps reference tables; native tool inventory and sub-agent surface tables are accurate.
- `DESIGN-flow-context-governance.md` — `truncate_history_window` and `precompute_compaction` registry usage documented with model selection table.
- `DESIGN-flow-memory-lifecycle.md` — `analyze_for_signals` registry usage (analysis role with fallback) correctly documented.

No tool registered without an approval table entry. No config field missing its env var mapping (the one field without an env var, `mcp_count`, is correctly documented as programmatically set). No behavioral description missing from any full-coverage item.
