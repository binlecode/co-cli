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

## Finding 2: `_TurnState` Dataclass — Over-Formalization of Ephemeral Locals

### Severity: MEDIUM | Migration effort: LOW

### What exists today

`_TurnState` (`_orchestrate.py:81-116`) is a dataclass with 8 fields used only within `run_turn()` and its two private callees (`_execute_stream_segment`, `_run_approval_loop`):

```python
@dataclass
class _TurnState:
    # pre-turn
    current_input: str | None
    current_history: list[ModelMessage]
    tool_reformat_budget: int = 2
    # in-turn
    latest_result: AgentRunResult | None = None
    latest_streamed_text: bool = False
    latest_usage: Any = None
    tool_approval_decisions: ToolApprovalDecisions | None = None
    # cross-turn
    outcome: TurnOutcome = "continue"
    interrupted: bool = False
```

The 20-line docstring documents "Phase ownership" and claims the class makes "invariants explicit and mutation paths auditable". In practice, every field is freely mutated by all three functions with no validation or guarding.

### Peer practice

| System | Turn state |
|--------|-----------|
| claude-code | Local variables in `AgentLoop.run()` — `let result`, `let messages`, etc. |
| codex | Local variables in `runTurn()` |
| opencode | Local variables in the session loop function |
| letta | Local variables in `Agent.step()` |

**Converged pattern**: ephemeral turn state lives in local variables. No formalization.

### Cost

- **Cognitive overhead**: a reader must understand the `_TurnState` class before reading `run_turn()`.
- **False precision**: the docstring implies lifecycle contracts ("pre-turn", "in-turn") that aren't enforced by the dataclass.
- **Indirection**: `_execute_stream_segment` takes `turn_state` and mutates 4 fields; the caller then reads them back. With locals, the data flow would be explicit via return values.

### Simplification path

`_execute_stream_segment` returns a named tuple `(result, streamed_text, usage)`. `_run_approval_loop` takes and returns `(result, history)`. `run_turn()` uses plain locals. The function signatures become slightly longer, but the data flow is explicit and there's no mutable bag to reason about.

---

## Finding 3: `ToolResult` TypedDict + `_kind` Discriminator

### Severity: MEDIUM | Migration effort: LOW

### What exists today

`_result.py:17-35` defines:

```python
class ToolResult(TypedDict, total=False):
    _kind: Required[Literal["tool_result"]]
    display: str

def make_result(display: str, **metadata: Any) -> ToolResult:
    return ToolResult(_kind="tool_result", display=display, **metadata)
```

Every user-facing tool must call `make_result()`. The `_kind` discriminator exists because "pydantic-ai serializes tool returns to dict before `_run_stream_segment()` sees them, so isinstance(content, ToolResult) would never be True" (docstring, `_result.py:5-6`).

The discriminator is consumed in `_display_hints.py`'s `format_tool_result_for_display()`:

```python
def format_tool_result_for_display(content: Any) -> ToolResultPayload:
    if isinstance(content, dict) and content.get("_kind") == "tool_result":
        return content  # type: ignore
    if isinstance(content, str):
        return content
    return str(content)
```

### Peer practice

| System | Tool return type |
|--------|-----------------|
| claude-code | `string` (content block text) — model and user both see the same string |
| codex | `{ type, data }` simple convention, no TypedDict |
| letta | `ToolExecutionResult(status, output_str)` — plain dataclass |
| opencode | `string` — tool output is always text |

**Converged pattern**: tools return strings or simple status+string pairs. No runtime discriminator tags.

### Cost

- **Ceremony**: every tool must import `make_result` and wrap its output. A tool that naturally returns a string must be wrapped.
- **Fragile detection**: the `_kind` check is a stringly-typed runtime dispatch. If someone returns a dict with `_kind="tool_result"` accidentally, it's treated as a ToolResult.
- **Metadata goes nowhere useful**: the `**metadata` kwargs (count, sources, confidence, etc.) are stuffed into the dict but the model just sees the `display` string. The metadata is only consumed by the parent agent's tool-calling code (subagent tools), where a structured return type on the specific tool would be cleaner.

### Simplification path

Tools return `str`. The display layer receives `ToolReturnPart.content` which is always the string. Metadata needed by parent orchestration (subagent results) uses a structured pydantic-ai `output_type` on the subagent, not a universal TypedDict. Delete `_result.py`, `make_result`, and the `_kind` dispatch.

---

## Finding 4: Four-Class Grouped Deps Hierarchy

### Severity: MEDIUM | Migration effort: MEDIUM

### What exists today

`CoDeps` (`deps.py:373-397`) has this shape:

```python
@dataclass
class CoDeps:
    shell: ShellBackend              # service handle
    config: CoConfig                 # read-only after bootstrap (~70 fields)
    knowledge_store: KnowledgeStore  # optional service
    model_registry: ModelRegistry    # optional service
    tool_index: dict[str, ToolConfig]
    task_agents: dict[str, Agent]
    skill_commands: dict[str, SkillConfig]
    session: CoSessionState          # grouped mutable state (~10 fields)
    runtime: CoRuntimeState          # grouped transient state (~6 fields)
```

Tools access deps via two-level paths: `ctx.deps.config.memory_dir`, `ctx.deps.session.session_id`, `ctx.deps.runtime.turn_usage`.

`CoSessionState` (`deps.py:302-321`) has 10 fields. `CoRuntimeState` (`deps.py:325-369`) has 6 fields plus `reset_for_turn()`.

### Peer practice

| System | Deps structure |
|--------|---------------|
| claude-code | Flat `AgentContext` — no sub-grouping |
| codex | `AppContext` with `config` + `services` — two levels max |
| letta | Flat `AgentState` (Pydantic model) |
| opencode | Flat `Session` struct |

**Converged pattern**: two levels max (context + services or context + config). No further nesting.

### Cost

- **Verbosity**: every tool access pays `ctx.deps.config.X` or `ctx.deps.session.Y` instead of `ctx.deps.X`.
- **Sub-agent isolation didn't get simpler**: `make_subagent_deps()` (`deps.py:400-429`) manually copies fields across the groups anyway — the grouping doesn't automate the isolation.
- **Four classes to understand**: `CoDeps`, `CoConfig`, `CoSessionState`, `CoRuntimeState` each have their own file-level documentation and lifecycle rules.

### Simplification path

Flatten all frequently-accessed fields into `CoDeps` directly. Keep service handles (`shell`, `knowledge_store`, `model_registry`) as top-level fields (they already are). Move config scalars (`memory_dir`, `personality`, `doom_loop_threshold`) to top-level. Move session state fields (`session_id`, `session_approval_rules`, `discovered_tools`) to top-level. `reset_for_turn()` becomes a module function operating on `CoDeps`.

Sub-agent isolation: `make_subagent_deps(base)` copies the 5-6 fields that need resetting — same as today, just without the `.config.` / `.session.` indirection.

If grouping is still desired for readability, use prefixed names (`config_memory_dir`, `session_id`) rather than nested objects. This eliminates the extra indirection without losing clarity.

### Note

This finding compounds with Finding 1 — if `CoConfig` is eliminated (Finding 1), the four-class hierarchy naturally collapses to two (CoDeps + mutable state).

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

## Finding 6: History Processors Mutating Deps — Fighting the Pure-Transformer Contract

### Severity: MEDIUM | Migration effort: MEDIUM

### What exists today

Two of the four history processors mutate `ctx.deps` state:

**`inject_opening_context`** (`_history.py:353-411`) mutates `ctx.deps.session.memory_recall_state`:
```python
# _history.py:374-375
state: MemoryRecallState = ctx.deps.session.memory_recall_state
state.model_request_count += 1  # mutation
# ...
state.recall_count += 1          # mutation
state.last_recall_user_turn = user_turn_count  # mutation
```

**`detect_safety_issues`** (`_history.py:419-545`) mutates `ctx.deps.runtime.safety_state`:
```python
# _history.py:527-528
state.doom_loop_injected = True   # mutation
# _history.py:540-541
state.reflection_injected = True  # mutation
```

Both have prominent `# INTENTIONAL DEVIATION` comments (`_history.py:365-370` and `_history.py:429-436`) explaining why they violate pydantic-ai's pure-transformer contract.

The deviation is necessary because pydantic-ai creates a fresh processor call per model request — local variables don't survive across segments within a turn. The state must live somewhere persistent (deps).

Supporting types exist solely for this: `MemoryRecallState` (`_types.py:12-20`) and `SafetyState` (`_types.py:23-31`), extracted to `_types.py` to break a circular import between `deps.py` and `_history.py`.

### Peer practice

| System | Pre-turn processing |
|--------|-------------------|
| claude-code | Memory injection and safety checks run as explicit pre-turn steps before calling the model. Not inside a message transformer. |
| codex | Message preprocessing happens in the turn orchestrator, before `createChatCompletion()`. |
| letta | Pre-step hooks run before the agent step. Post-step hooks run after. Neither is a message transformer. |
| opencode | Context assembly happens before the model call in the session loop. |

**Converged pattern**: stateful pre-processing runs as explicit steps in the orchestration loop, not inside a "pure transformer" pipeline.

### Cost

- **Contract violation**: the `# INTENTIONAL DEVIATION` comments are a permanent code smell. Any reader must understand why the contract is broken.
- **Circular import**: `_types.py` exists solely to break the `deps.py` ↔ `_history.py` import cycle caused by putting state types in deps that are mutated by processors.
- **Hidden side effects**: a reader of `run_turn()` sees `history_processors=[..., inject_opening_context, detect_safety_issues]` and reasonably assumes they're pure transforms. The mutations are hidden.

### Simplification path

Move memory injection and safety detection to explicit async steps in `run_turn()`, called before `_execute_stream_segment()`:

```python
# In run_turn(), before the segment call:
messages = await inject_memories(deps, messages)     # explicit, stateful, returns new messages
messages = check_safety(deps, messages)              # explicit, stateful, returns new messages + injections
await _execute_stream_segment(...)
```

Keep only `truncate_tool_returns` and `truncate_history_window` as history processors — they are genuinely stateless (or state-free from deps' perspective: `truncate_history_window` reads deps but only mutates `compaction_failure_count`, which could also be moved out).

Delete `_types.py` (inline the two small dataclasses into their sole consumers). Remove `MemoryRecallState` from `CoSessionState` and `SafetyState` from `CoRuntimeState` — they become local state in the pre-turn functions.

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

## Finding 9: `ModelRegistry` Class Wrapper

### Severity: LOW | Migration effort: LOW

### What exists today

`ModelRegistry` (`_model_factory.py:35-68`) wraps a `dict[str, ResolvedModel]`:

```python
class ModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, ResolvedModel] = {}

    @classmethod
    def from_config(cls, config: Any) -> "ModelRegistry":
        registry = cls()
        for role, entry in config.role_models.items():
            if not entry:
                continue
            model, settings, ctx_window = build_model(entry, config.llm_provider, config.llm_host, api_key=config.llm_api_key)
            registry._models[role] = ResolvedModel(model=model, settings=settings, context_window=ctx_window)
        return registry

    def get(self, role: str, fallback: ResolvedModel) -> ResolvedModel:
        return self._models.get(role, fallback)

    def is_configured(self, role: str) -> bool:
        return role in self._models
```

Three methods, all trivial dict operations. Used in ~8 callsites across the codebase.

### Peer practice

| System | Model management |
|--------|-----------------|
| claude-code | Direct model client construction, no registry |
| codex | `ModelProvider.create(config)` factory function, no registry class |
| letta | Model set directly on agent, no registry |
| opencode | Model resolved from config at session start, stored in session struct |

### Cost

- **Indirection**: callers write `registry.get(ROLE_X, ResolvedModel(model=None, settings=None))` instead of `role_models.get(ROLE_X)` or `role_models[ROLE_X]`.
- **The fallback pattern is awkward**: every callsite must construct a `ResolvedModel(model=None, settings=None)` as fallback, because `get()` has no default-default.

### Simplification path

Replace with `build_role_models(config) -> dict[str, ResolvedModel]` (the `from_config` body, as a module function). Store the dict directly in `CoDeps`. Callsites use `deps.role_models.get(ROLE_X)` with standard dict semantics. Delete the `ModelRegistry` class.

---

## Priority Matrix

| # | Finding | Severity | Effort | ROI | Key metric |
|---|---------|----------|--------|-----|-----------|
| 5 | Approval resume loop | HIGH | HIGH | Highest | Eliminates extra LLM call per approval |
| 1 | Settings→CoConfig transcription | HIGH | MEDIUM | High | Eliminates ~100 lines boilerplate + 2-file tax |
| 2 | `_TurnState` dataclass | MEDIUM | LOW | Quick win | Clearer data flow in run_turn() |
| 6 | History processors mutating deps | MEDIUM | MEDIUM | Cleaner arch | Removes contract violations + _types.py |
| 4 | Four-class grouped deps | MEDIUM | MEDIUM | Simpler API | Flatter tool access paths |
| 3 | `ToolResult` TypedDict | MEDIUM | LOW | Simpler tools | Tools return strings directly |
| 7 | Subagent 4x copy-paste | LOW-MED | LOW | Less code | ~280 lines → ~80 lines |
| 9 | `ModelRegistry` class | LOW | LOW | Quick win | Delete class, use plain dict |
| 8 | Deferred tool filtering | LOW | LOW | Justified | Make opt-in, not default |

## Dependencies Between Findings

```
Finding 1 (delete CoConfig) ──► Finding 4 (flatten deps) naturally follows
Finding 5 (approval rewrite) ──► Finding 2 (_TurnState simplification) — approval loop removal simplifies turn state
Finding 6 (move processors out) ──► Finding 4 (flatten deps) — removes SafetyState/MemoryRecallState from deps
```

## Recommended TODO Sequencing

1. **Phase 1 — Quick wins** (low risk, immediate clarity):
   - Finding 9: Replace `ModelRegistry` with plain dict
   - Finding 2: Dissolve `_TurnState` into locals + return values
   - Finding 3: Tools return `str`, delete `ToolResult` / `make_result`

2. **Phase 2 — Config simplification** (medium risk, high LOC reduction):
   - Finding 1: Eliminate `CoConfig`, use `Settings` directly in deps
   - Finding 4: Flatten `CoDeps` (follows naturally from Finding 1)

3. **Phase 3 — Architecture alignment** (medium risk, cleaner design):
   - Finding 6: Move stateful processors to explicit pre-turn steps
   - Finding 7: Consolidate subagent tools into dispatch table

4. **Phase 4 — Approval rewrite** (high risk, highest ROI):
   - Finding 5: Replace deferred approval with pre-execution interception

Each phase is independently shippable. Phase 4 is the most impactful but can be deferred until the simpler phases prove out the simplification direction.
