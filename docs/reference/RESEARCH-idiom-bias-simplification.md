# RESEARCH: Pydantic-AI Idiom Bias — Simplification Opportunities

Date: 2026-04-03
Scope: System-wide diagnosis of over-engineering driven by pydantic-ai SDK alignment
Method: Deep scan of co-cli source cross-referenced against converged practice in claude-code (fork-cc), codex, opencode, and letta

## Context

co-cli has emphasised pydantic-ai idiomatic alignment throughout its development. While this keeps the codebase consistent with the SDK's intended patterns, it has introduced complexity in several areas where simpler, more common agentic CLI patterns — converged upon independently by 2+ peer systems — would achieve the same result with less code, fewer abstractions, and in one case (approval flow) better runtime performance.

This document catalogues each finding with code-grounded evidence, peer system references, and a severity/effort assessment to drive future task-specific TODOs.

---

## Finding 1: Settings → CoConfig Double-Barrel Config Transcription

### Severity: HIGH | Migration effort: MEDIUM

### What exists today

`Settings` (`config.py`) is a Pydantic `BaseModel` loaded from `settings.json` + env vars. `CoConfig` (`deps.py:102-277`) is a plain `dataclass` that receives every `Settings` field via a 70-line manual transcription in `CoConfig.from_settings()`:

```python
# deps.py:218-277 — every field copied 1:1
return cls(
    workspace_root=cwd,
    obsidian_vault_path=Path(s.obsidian_vault_path) if s.obsidian_vault_path else None,
    google_credentials_path=s.google_credentials_path,
    shell_safe_commands=list(s.shell_safe_commands),
    shell_max_timeout=s.shell_max_timeout,
    memory_dir=cwd / ".co-cli" / "memory",
    ...
    # 50+ more fields
)
```

Both classes carry the same ~70 fields. `CoConfig` also imports 30+ `DEFAULT_*` constants from `config.py` (`deps.py:9-52`) to set defaults that `Settings` already defines.

### Why it exists

The CLAUDE.md rule: "Tools access ctx.deps.config.field_name. No tool should import Settings." Combined with pydantic-ai's convention that `deps_type` should be a plain dataclass, this created a two-layer system: `Settings` (Pydantic, handles validation/loading) → `CoConfig` (dataclass, carried in `CoDeps`).

### Peer practice

| System | Config approach |
|--------|----------------|
| claude-code | Single `AgentConfig` passed through context — no transcription layer |
| codex | Single `AppConfig` (TypeScript interface) frozen at load, passed directly |
| letta | Single `LettaConfig` (Pydantic model) — agents receive it directly |
| opencode | Single Go `Config` struct read from TOML, passed to session |

**Converged pattern**: one config object, loaded once, passed directly. No transcription.

### Cost

- **Boilerplate**: ~100 lines in `deps.py` (the `from_settings` method + 30 default imports).
- **Two-file tax**: every new setting requires adding it to `Settings` (config.py), then copying it to `CoConfig` (deps.py), then wiring the `from_settings()` line. Three touch points for one config field.
- **Divergence risk**: if a default changes in `Settings` but the `CoConfig` default isn't updated (or vice versa), silent drift.

### Simplification path

Let tools access `Settings` directly (read-only by convention, same guarantee `CoConfig` has today). Delete `CoConfig` and `from_settings()`. `CoDeps.config` becomes `CoDeps.config: Settings`. The "no tool imports Settings" rule becomes "tools access settings via `ctx.deps.config`, not by importing the module" — same isolation, no transcription.

If the Pydantic-model-as-deps concern is real (it isn't — pydantic-ai accepts any type as `deps_type`), wrap `Settings` in a `@dataclass` with a single field. Still eliminates the 1:1 copy.

---

## ~~Finding 2: `_TurnState` Dataclass~~ — REMOVED

Removed: style preference, not a value-add. Dissolving `_TurnState` into locals trades a documented dataclass with lifecycle annotations for 8-element return tuples and longer function signatures. Peers use locals because their turn functions are simpler (no approval loop, no multi-error-handler recovery). co-cli's `run_turn()` has approval chaining, 400/413/timeout/cancellation handlers, and segment looping — the dataclass groups related state and documents the lifecycle, which is net-positive for this complexity level.

---

## ~~Finding 3: `ToolResult` TypedDict + `_kind` Discriminator~~ — SHIPPED

Shipped: replaced with pydantic-ai native `ToolReturn(return_value, metadata)` in commit 6191bfd. `_kind` discriminator and `make_result()` deleted. Tools now return `ToolReturn` via `tool_output()`, with `return_value` → model content and `metadata` → app-side data.

---

## ~~Finding 4: Four-Class Grouped Deps Hierarchy~~ — REMOVED

Removed: style preference, not a value-add. Flattening saves `.config.` / `.session.` typing but loses semantic grouping that documents lifecycle boundaries (read-only config vs mutable session vs per-turn runtime). The grouping makes `make_subagent_deps()` isolation intent explicit — which fields are shared vs reset. A flat 90-field `CoDeps` with prefixed names (`config_memory_dir`, `session_id`) is harder to reason about than three small grouped classes. Peers use flat contexts because they have fewer fields (~20-30 vs co-cli's ~90).

---

## Finding 5: Approval Resume Loop — Extra LLM Calls per Approval

### Severity: HIGH | Migration effort: HIGH

### What exists today

The approval flow uses pydantic-ai's `DeferredToolRequests` → `DeferredToolResults` cycle:

1. Agent calls a tool with `requires_approval=True`
2. pydantic-ai returns `DeferredToolRequests` instead of executing
3. `_run_approval_loop()` (`_orchestrate.py:302-332`) collects user decisions
4. A **new model request** is made via `_execute_stream_segment()` with `deferred_tool_results=approvals`
5. The model processes the approval result and the tool executes
6. Steps 2-5 repeat if the model calls more deferred tools

The resume segment runs on a separate `task_agent` (`agent.py:353-375`) with `reasoning_effort=none` to minimize the cost of this extra round-trip. This task agent is built at bootstrap (`agent.py:353-375`), stored in `deps.task_agents`, and selected at resume time:

```python
# _orchestrate.py:327-328
resume_agent = deps.task_agents.get(ROLE_TASK, agent)
resume_settings = _resolve_task_model_settings(deps) or model_settings
```

Supporting infrastructure:
- `resume_tool_names: frozenset[str] | None` on `CoRuntimeState` (`deps.py:357`) — narrows tool visibility during resume
- `_resolve_task_model_settings()` (`_orchestrate.py:294-299`) — resolves task model settings
- `ROLE_TASK` model config in `config.py` — dedicated model role for resume turns
- `build_task_agent()` (`agent.py:353-375`) — agent factory for the lightweight resume agent
- Tool filter narrowing in `_filter()` (`agent.py:209-214`) — `resume_tool_names` gate

### Peer practice

| System | Approval flow |
|--------|--------------|
| claude-code | Tool execution is intercepted pre-execution. Permission check → user prompt → execute or skip. **Zero extra LLM calls**. The tool runs in the same agent turn. |
| codex | `apply_patch` and shell tools check permission before execution. Approved → execute inline. Denied → skip with message. **Zero extra LLM calls**. |
| opencode | Permission checked before tool execution in the session loop. **Zero extra LLM calls**. |
| letta | Tools execute directly; sandbox policy is enforcement, not approval. |

**Converged pattern**: intercept tool execution before it runs, ask user, execute or skip inline. The model never needs to "process" an approval decision — it already made the tool call, it just needs the result.

### Cost

- **Performance**: every approved tool call requires an extra LLM round-trip. Even with `reasoning_effort=none`, this adds latency (network + inference time) and token cost.
- **Infrastructure**: `task_agent`, `ROLE_TASK` model config, `resume_tool_names`, `_resolve_task_model_settings()`, the while-loop in `_run_approval_loop()` — all exist solely to support the deferred/resume cycle.
- **Complexity**: the `_filter` function in `agent.py:205-222` has a special `resume` branch because the tool set must be narrowed during resume segments.
- **Behavioral risk**: the resume model may add unwanted commentary or change behavior between the tool call and its execution.

### Simplification path

Replace pydantic-ai's `requires_approval` / `DeferredToolRequests` with a pre-execution hook pattern:

1. Register all tools without `requires_approval` (they always execute when called)
2. Add a tool execution wrapper that checks an approval policy before running the tool body
3. If approval needed: pause execution, prompt user, execute or raise `ModelRetry("User denied")`
4. The tool result returns to the model in the same segment — no resume, no extra LLM call

This requires either:
- A pydantic-ai `tool_prepare` callback that intercepts before execution (pydantic-ai supports this via `prepare` parameter on tools)
- Or wrapping each approval-requiring tool function with an approval check

Delete: `build_task_agent()`, `ROLE_TASK` config, `resume_tool_names`, `_resolve_task_model_settings()`, `_run_approval_loop()`, the deferred branch in `_filter()`.

### Caveat

This is the highest-ROI change but also the most invasive. It fundamentally changes the approval architecture from SDK-native deferred to middleware-style interception. The `tool_prepare` approach is still pydantic-ai idiomatic (it's a supported hook), just not using the deferred flow.

---

## ~~Finding 6: History Processors Mutating Deps~~ — REMOVED

Removed: impl preference mismatch, not a value-add. Moving `inject_opening_context` and `detect_safety_issues` from the processor chain to explicit pre-turn steps reshuffles where code lives without improving behavior, reducing complexity, or enabling new capabilities. The pydantic-ai processor chain is a valid pattern — the deps mutations are documented (`# INTENTIONAL DEVIATION`), scoped per-turn, and the supporting types (`MemoryRecallState`, `SafetyState`) are minimal (31 lines in `_types.py`).

---

## Finding 7: Subagent Tool Boilerplate — 4x Copy-Paste

### Severity: LOW-MEDIUM | Migration effort: LOW

### What exists today

`subagent.py` (`subagent.py:66-347`) contains four nearly identical tool functions:

| Function | Lines | Role | Unique logic |
|----------|-------|------|-------------|
| `run_coding_subagent` | 66-123 (58 lines) | ROLE_CODING | None |
| `run_research_subagent` | 126-208 (83 lines) | ROLE_RESEARCH | Empty-result retry (lines 177-188) |
| `run_analysis_subagent` | 211-282 (72 lines) | ROLE_ANALYSIS | Optional `inputs` context prepend |
| `run_reasoning_subagent` | 285-347 (63 lines) | ROLE_REASONING | None |

Common structure repeated in each (~50 lines per function):
1. Resolve max_requests from config default (3 lines)
2. Import agent factory (1 line)
3. Check registry + get model (4 lines)
4. Set local variables: model_name, role, request_limit (3 lines)
5. Create agent from factory (1 line)
6. Open tracing span + set attributes (3 lines)
7. Call `_run_subagent_attempt()` (4 lines)
8. Read usage + set span attribute (2 lines)
9. Format display string (2 lines)
10. Return `make_result()` with ~10 kwargs (8 lines)

The `_subagent_agents.py` file has the same pattern — four factory functions (`make_coder_agent`, `make_research_agent`, `make_analysis_agent`, `make_thinking_agent`) with identical structure differing only in tools registered and output type.

### Peer practice

| System | Sub-agent dispatch |
|--------|-------------------|
| claude-code | `spawnAgent(role, prompt, tools)` — one function, role determines config |
| letta | `agent.run_sub_agent(config)` — single dispatch with config parameter |
| codex | No sub-agents (single agent model) |

**Converged pattern**: one dispatch function with a role/config parameter.

### Cost

- **~280 lines** that could be ~80 with a dispatch table.
- **Maintenance**: adding a new subagent role requires copy-pasting one of the existing functions and tweaking 3-4 lines.
- **Inconsistency risk**: the research subagent has a retry loop (lines 177-188) that the others don't — easy to miss in a copy-paste audit.

### Simplification path

Define a `SubagentRole` config:
```python
SUBAGENT_ROLES = {
    ROLE_CODING: SubagentRoleConfig(factory=make_coder_agent, max_requests_key="subagent_max_requests_coder"),
    ROLE_RESEARCH: SubagentRoleConfig(factory=make_research_agent, max_requests_key="subagent_max_requests_research", retry_on_empty=True),
    ...
}
```

One `run_subagent(ctx, role, prompt, **kwargs) -> ToolResult` function dispatches via the table. The research retry logic is controlled by `retry_on_empty=True` in the config. Register one tool per role (for discoverability) but they all delegate to the same function.

---

## Finding 8: Dynamic Tool Filtering (Deferred Discovery)

### Severity: LOW | Migration effort: LOW

### What exists today

Every tool has `always_load: bool` or `should_defer: bool` in `ToolConfig` (`deps.py:280-298`). A filter function runs per model request (`agent.py:205-222`):

```python
def _filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
    entry = ctx.deps.tool_index.get(tool_def.name)
    resume = ctx.deps.runtime.resume_tool_names
    if resume is not None:
        if tool_def.name in resume:
            return True
        if entry is not None and entry.always_load:
            return True
        return False
    if entry is None:
        return True
    if entry.always_load:
        return True
    return tool_def.name in ctx.deps.session.discovered_tools
```

The `search_tools` tool (`tool_search.py`) acts as the discovery mechanism — when the model calls `search_tools("write file")`, matching deferred tools are added to `session.discovered_tools`.

### Assessment

**This is justified for the Ollama use case.** Local models with 32K-131K context windows benefit from smaller tool schemas. The ~25 deferred tools would add ~15K tokens of schema to every request.

However, for API-hosted models (Gemini, future cloud providers) with 1M+ context, this is pure overhead — the filter runs every request, the discovery mechanism adds a tool call, and the model must learn the "search for tools" workflow.

### Peer practice

All four peer systems (claude-code, codex, opencode, letta) show all tools always. None have dynamic per-request filtering.

### Simplification path

Make filtering opt-in: add a `deferred_tool_discovery: bool` config flag (default: True for ollama-openai, False for gemini and other cloud providers). When disabled, all tools are `always_load=True` and `search_tools` is not registered. The filter function becomes a no-op.

---

## ~~Finding 9: `ModelRegistry` Class Wrapper~~ — REMOVED

Removed: minimal value. The class is 30 lines, provides a `from_config` factory and typed `get()` with fallback. Replacing with a plain dict saves one small class but loses the factory encapsulation and typed access pattern. The "awkward fallback" is a style issue, not a complexity issue. 8 callsites is not enough to justify the churn.

---

## Priority Matrix (active findings only)

| # | Finding | Severity | Effort | ROI | Key metric |
|---|---------|----------|--------|-----|-----------|
| 5 | Approval resume loop | HIGH | HIGH | Highest | Eliminates extra LLM call per approval |
| 1 | Settings→CoConfig transcription | HIGH | MEDIUM | High | Eliminates ~100 lines boilerplate + 2-file tax |
| 7 | Subagent 4x copy-paste | LOW-MED | LOW | Less code | ~280 lines → ~80 lines |
| 8 | Deferred tool filtering | LOW | LOW | Justified | Make opt-in, not default |

Removed: Finding 2 (style preference), Finding 3 (shipped), Finding 4 (style preference), Finding 6 (impl mismatch), Finding 9 (minimal value).

## Recommended TODO Sequencing

1. **Phase 1 — Config simplification** (medium risk, high LOC reduction):
   - Finding 1: Eliminate `CoConfig`, use `Settings` directly in deps

2. **Phase 2 — Code consolidation** (low risk, less code):
   - Finding 7: Consolidate subagent tools into dispatch table
   - Finding 8: Make deferred tool filtering opt-in per provider

3. **Phase 3 — Approval rewrite** (high risk, highest ROI):
   - Finding 5: Replace deferred approval with pre-execution interception

Each phase is independently shippable.
