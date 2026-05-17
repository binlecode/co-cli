# Co CLI — Agents

> For tool registration, approval flow, and lifecycle hooks: [tools.md](tools.md). For the orchestration loop and segment/turn semantics: [core-loop.md](core-loop.md). For orchestrator static-instruction composition: [prompt-assembly.md](prompt-assembly.md). For daemon callers and curation hooks: [skills.md](skills.md). For span record shape and the `ObservabilityCapability`: [observability.md](observability.md).

## 1. Functional Architecture

```mermaid
graph TD
    subgraph Records["Declarative specs (frozen dataclasses)"]
        OS["OrchestratorSpec\nstatic_instruction_builders\nper_turn_instructions\nhistory_processors"]
        TS["TaskAgentSpec\ntool_names\ninstructions\noutput_type\ndefault_budget\nerror_message\ninclude_skill_manifest"]
    end
    subgraph Orchestrator["Singleton primary"]
        OS --> BO["build_orchestrator(spec, deps)"]
        BO -->|"deps.toolset"| OA["Orchestrator Agent"]
    end
    subgraph TaskPath["Per-call task agents"]
        TS --> BT["build_task_agent(spec, deps, model)"]
        BT -->|"TOOL_REGISTRY_BY_NAME[name]\nfor name in spec.tool_names"| TA["Task Agent"]
        TA --> RIT["run_in_turn\n(in-turn delegation)"]
        TA --> RST["run_standalone\n(daemon)"]
    end
    RIT -.usage merge.-> Parent["parent turn_usage"]
    RST -.no merge.-> Solo["caller-managed"]
```

### Spec types

| Type | Role | Lifecycle | Tools field | Key fields |
|------|------|-----------|-------------|------------|
| `OrchestratorSpec` | Always-present primary agent | Built once per chat session | None (`deps.toolset` injected directly) | `static_instruction_builders`, `per_turn_instructions`, `history_processors` |
| `TaskAgentSpec` | Focused task agent (delegation or daemon) | Built per call | `tool_names: tuple[str, ...]` | `instructions`, `output_type`, `default_budget`, `error_message`, `include_skill_manifest` |

No shared base. The two specs do not feed a polymorphic dispatcher — inheritance would be decorative. Runner choice (`run_in_turn` vs `run_standalone`) selects lifecycle, not spec shape.

### Concrete specs

| Spec | Owner module | Caller | Runner |
|------|--------------|--------|--------|
| `ORCHESTRATOR_SPEC` | `co_cli/agent/orchestrator.py` | `_chat_loop` in `main.py` | `build_orchestrator` directly |
| `WEB_RESEARCH_SPEC` | `co_cli/tools/agents/delegation.py` | `web_research` tool | `_run_attempt` ×2 in own span (retry-on-empty) |
| `KNOWLEDGE_ANALYZE_SPEC` | `co_cli/tools/agents/delegation.py` | `knowledge_analyze` tool | `run_in_turn` |
| `REASON_SPEC` | `co_cli/tools/agents/delegation.py` | `reason` tool | `run_in_turn` |
| `SESSION_REVIEW_SPEC` | `co_cli/skills/session_review.py` | `_maybe_run_session_review` (post-turn) | `run_standalone` |
| `CURATOR_SPEC` | `co_cli/skills/curator.py` | `_maybe_run_curator` (post-turn) | `run_standalone` |

**Curation rule.** Specs live with the caller that owns the agent's purpose — delegation specs sit alongside their tool wrappers; daemon specs sit alongside their daemon orchestration. The `co_cli/agent/` package owns lifecycle (build + run) and the orchestrator spec only.

### Shared entry points

`build_orchestrator(spec, deps)` (`co_cli/agent/build.py`) composes the orchestrator. Static instructions are assembled by calling each `spec.static_instruction_builders` closure in order and joining with double newlines; per-turn instructions are registered via `agent.instructions(...)`; history processors are attached as a list. Output type is fixed `[str, DeferredToolRequests]`; capabilities `[ObservabilityCapability(), CoToolLifecycle()]` — Observability first so it brackets `CoToolLifecycle`'s `after_*` hooks (see [observability.md](observability.md) for the ordering invariant); retries from `deps.config.tool_retries`. Toolset comes from `deps.toolset` directly — orchestrator is a singleton, no factory abstraction.

`build_task_agent(spec, deps, model)` (`co_cli/agent/build.py`) resolves `spec.tool_names` against `TOOL_REGISTRY_BY_NAME` (populated by `@agent_tool` at import time), filters through `_config_requirement_met` to drop integration tools whose credentials are absent, and registers each resolved tool with `requires_approval=False`. Unknown names raise `ValueError` at build time. When `spec.include_skill_manifest=True`, the rendered skill manifest is prepended to `spec.instructions(deps)`.

`run_in_turn(spec, ctx, prompt, budget)` and `run_standalone(spec, deps, prompt, budget, model_settings)` (`co_cli/agent/run.py`) are the two task-agent runners. `_run_attempt` is the inner primitive both share — `web_research` calls it twice inside a single outer span via `@trace("co.web_research.retry_loop")` so the two attempts share one parent retry-envelope span.

## 2. Core Logic

### Adding a new task agent

```
1. Pick the caller module that owns the agent's purpose:
     delegation tool → co_cli/tools/agents/delegation.py
     daemon          → co_cli/skills/<domain>.py
2. Define the spec record next to the caller:
     SPEC = TaskAgentSpec(
       name="my_agent",                # span name + role tag (carried via agent.run metadata)
       instructions=_my_instructions,  # callable: (deps) -> str
       tool_names=("tool_a", "tool_b"),# must exist in TOOL_REGISTRY_BY_NAME
       output_type=MyOutput,           # pydantic BaseModel
       default_budget=N,               # UsageLimits.request_limit fallback
       error_message="...",            # raised in ModelRetry on in-turn failure
       include_skill_manifest=False,   # True only when the agent reads/edits skills
     )
3. Wire the runner:
     in-turn delegation → return await run_in_turn(SPEC, ctx, prompt, budget=...)
     daemon             → output, usage, run_id = await run_standalone(
                            SPEC, child_deps, prompt, budget=..., model_settings=...)
```

No decorator advertisement, no profile registry. `tool_names` is the source of truth; mistypes fail loud at build time.

### `build_task_agent` — tool resolution

```
tool_fns = []
for name in spec.tool_names:
    fn = TOOL_REGISTRY_BY_NAME.get(name)
    if fn is None:
        raise ValueError(f"{spec.name}: unknown tool {name!r}")
    info = fn.<agent-tool-metadata>
    if not _config_requirement_met(info, deps.config):
        continue                              # drop Google/Obsidian tools without creds
    tool_fns.append(fn)

instructions = spec.instructions(deps)
if spec.include_skill_manifest:
    instructions = render_skill_manifest(...) + "\n\n" + instructions

agent = Agent(
    model, deps_type=CoDeps,
    output_type=spec.output_type,
    instructions=instructions,
    retries=deps.config.tool_retries,
    capabilities=[CoToolLifecycle()],
)
for fn in tool_fns:
    agent.tool(fn, requires_approval=False)   # task agents auto-approve own calls
return agent
```

`requires_approval=False` for every resolved tool — task agents do not prompt the user. The orchestrator's `_approval_resume_filter` and `DeferredToolRequests` flow stay on the orchestrator path only.

### `run_in_turn` — in-turn delegation

```
if ctx.deps.runtime.agent_depth >= MAX_AGENT_DEPTH:        # always depth-checks
    raise ModelRetry("Delegation depth limit reached ...")
if not ctx.deps.model:
    raise ModelRetry(f"{spec.name} agent is unavailable ...")

request_limit = budget or spec.default_budget
child_deps    = fork_deps(ctx.deps)                        # increments agent_depth
                                                           # forwards tool_index, excludes toolset
otel_span(spec.name, role=spec.name, model=..., request_limit=...):
    output, usage, run_id = _run_attempt(spec, ctx, prompt, request_limit, child_deps)
    _merge_turn_usage(ctx, usage)                          # writes ctx.deps.runtime.turn_usage

return tool_output(
    f"{output.result}\n[{spec.name} · {model} · {used}/{limit} req]",
    role=spec.name, run_id=run_id,
)
```

`_run_attempt` builds the agent and runs one attempt; on any exception it raises `ModelRetry(spec.error_message)`, surfaced back to the orchestrator's retry budget.

### `run_standalone` — daemon

```
if deps.model is None:
    raise ValueError(...)                                  # caller bug, not ModelRetry

request_limit = budget or spec.default_budget
settings      = model_settings or deps.model.settings
agent         = build_task_agent(spec, deps, deps.model.model)

otel_span(spec.name, role=spec.name, request_limit=...):
    result = await agent.run(prompt, deps=deps,
                             usage_limits=UsageLimits(request_limit=request_limit),
                             model_settings=settings,
                             metadata={"role": spec.name, ...})
    return result.output, copy(result.usage()), result.run_id
```

Daemons differ from in-turn delegation in three ways: (1) **no depth check** — daemons are top-level, never nested inside an orchestrator turn; (2) **no usage merge** — no parent turn exists; (3) **plain exceptions** — `run_standalone` does not consult `spec.error_message`, exceptions propagate to the daemon-specific handler (typically `asyncio.wait_for` timeout + report-on-fail). The caller is responsible for forking deps before invocation (`fork_deps_for_reviewer`, `fork_deps_for_curator`).

### `web_research` — single-span retry topology

```
depth-check
fork_deps
otel_span("web_research"):                                 # outer span owns both attempts
    output_1, usage_1, _ = _run_attempt(SPEC, ctx, prompt, budget, child_deps)
    _merge_turn_usage(ctx, usage_1)
    if output_1 is empty:
        output_2, usage_2, _ = _run_attempt(SPEC, ctx, rephrased(prompt), remaining_budget, child_deps)
        _merge_turn_usage(ctx, usage_2)
```

`web_research` is the only caller that reaches below `run_in_turn`. The reason is span topology: a retry should appear as one `co.web_research.retry_loop` span with two child agent runs, not two sibling spans. All other delegation tools call `run_in_turn` directly.

## 3. Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| `tool_retries` | `CO_TOOL_RETRIES` | `3` | `retries=` for orchestrator and task agents |
| `skills.review_enabled` | — | `True` | Gates `SESSION_REVIEW_SPEC` daemon |
| `skills.curator_enabled` | — | `True` | Gates `CURATOR_SPEC` daemon |
| `MAX_AGENT_DEPTH` | — | `2` | Hard cap on nesting depth enforced by `run_in_turn`; module constant |
| `REVIEW_MAX_ITERATIONS` | — | `8` | `SESSION_REVIEW_SPEC.default_budget` |
| `CURATOR_MAX_ITERATIONS` | — | `100` | `CURATOR_SPEC.default_budget` |
| `REVIEW_TIMEOUT_SECONDS` | — | configured in `co_cli/config/skills.py` | `asyncio.wait_for` wrapping `run_session_review` |
| `CURATOR_TIMEOUT_SECONDS` | — | configured in `co_cli/config/skills.py` | `asyncio.wait_for` wrapping `run_curator` |

## 4. Public Interface

### Spec types

| Symbol | Source | Contract |
|--------|--------|----------|
| `OrchestratorSpec` | `co_cli/agent/spec.py` | Frozen dataclass — fields: `name`, `static_instruction_builders`, `per_turn_instructions`, `history_processors` (all tuples for immutability) |
| `TaskAgentSpec` | `co_cli/agent/spec.py` | Frozen dataclass — fields: `name`, `instructions`, `tool_names`, `output_type`, `default_budget`, `error_message`, `include_skill_manifest=False` |
| `ORCHESTRATOR_SPEC` | `co_cli/agent/orchestrator.py` | Singleton — 5 static-instruction builders, 2 per-turn instructions, 5 history processors |
| `WEB_RESEARCH_SPEC`, `KNOWLEDGE_ANALYZE_SPEC`, `REASON_SPEC` | `co_cli/tools/agents/delegation.py` | In-turn task specs; budgets 10 / 8 / 3 |
| `SESSION_REVIEW_SPEC`, `CURATOR_SPEC` | `co_cli/skills/session_review.py`, `co_cli/skills/curator.py` | Daemon task specs; budgets `REVIEW_MAX_ITERATIONS` / `CURATOR_MAX_ITERATIONS` |

### Builders

| Symbol | Source | Contract |
|--------|--------|----------|
| `build_orchestrator(spec: OrchestratorSpec, deps: CoDeps) -> Agent[CoDeps, Any]` | `co_cli/agent/build.py` | Constructs the orchestrator from `deps.toolset`; raises `ValueError` if `deps.toolset` or `deps.model` is unset |
| `build_task_agent(spec: TaskAgentSpec, deps: CoDeps, model: Any) -> Agent[CoDeps, Any]` | `co_cli/agent/build.py` | Resolves `spec.tool_names` via `TOOL_REGISTRY_BY_NAME` filtered by `_config_requirement_met`; raises `ValueError` on unknown names; registers each tool with `requires_approval=False` |

### Runners

| Symbol | Source | Contract |
|--------|--------|----------|
| `run_in_turn(spec: TaskAgentSpec, ctx: RunContext[CoDeps], prompt: str, budget: int \| None = None) -> ToolReturn` | `co_cli/agent/run.py` | In-turn runner; depth-checks, forks deps, opens span, merges usage, raises `ModelRetry(spec.error_message)` on failure; returns formatted `ToolReturn` |
| `run_standalone(spec: TaskAgentSpec, deps: CoDeps, prompt: str, budget: int \| None = None, model_settings: Any = None) -> tuple[Any, RunUsage, str]` | `co_cli/agent/run.py` | Daemon runner; takes already-forked deps, opens own span, never depth-checks, no usage merge, plain exceptions |
| `_run_attempt(spec, ctx, prompt, budget, child_deps) -> tuple[Any, RunUsage, str]` | `co_cli/agent/run.py` | Inner primitive — builds and runs the agent once; raises `ModelRetry(spec.error_message)` on any exception; used by `web_research` to drive single-span retry |
| `MAX_AGENT_DEPTH` | `co_cli/agent/run.py` | Module constant (`2`) enforced by `run_in_turn` |

## 5. Files

| File | Role |
|------|------|
| `co_cli/agent/spec.py` | `OrchestratorSpec`, `TaskAgentSpec` declarative records |
| `co_cli/agent/build.py` | `build_orchestrator`, `build_task_agent` |
| `co_cli/agent/orchestrator.py` | `ORCHESTRATOR_SPEC` + the 5 static-instruction provider closures |
| `co_cli/agent/run.py` | `run_in_turn`, `run_standalone`, `_run_attempt`, `_merge_turn_usage`, `MAX_AGENT_DEPTH` |
| `co_cli/agent/_instructions.py` | `safety_prompt`, `current_time_prompt` — orchestrator per-turn instructions |
| `co_cli/agent/core.py` | `build_native_toolset`, `build_mcp_entries`, `assemble_routing_toolset` (toolset helpers; see [tools.md](tools.md)) |
| `co_cli/tools/agent_tool.py` | `@agent_tool` decorator; `TOOL_REGISTRY`, `TOOL_REGISTRY_BY_NAME` |
| `co_cli/tools/agents/delegation.py` | In-turn task specs (`WEB_RESEARCH_SPEC`, `KNOWLEDGE_ANALYZE_SPEC`, `REASON_SPEC`) + tool wrappers |
| `co_cli/skills/session_review.py` | `SESSION_REVIEW_SPEC` + `run_session_review` daemon |
| `co_cli/skills/curator.py` | `CURATOR_SPEC` + `run_curator` daemon + skill state machinery |

## 6. Test Gates

| Property | Test file |
|----------|-----------|
| `TaskAgentSpec.tool_names` resolves to registered tools by exact name | `tests/test_agent_build_task_agent.py` |
| Unknown tool name in `tool_names` raises `ValueError` at build time | `tests/test_agent_build_task_agent.py` |
| Google tools drop out of resolved set when `google_credentials_path` is absent | `tests/test_agent_build_task_agent.py` |
| Obsidian tools drop out of resolved set when `obsidian_vault_path` is absent | `tests/test_agent_build_task_agent.py` |
| Task agents register all tools with `requires_approval=False` | `tests/test_agent_build_task_agent.py` |
| `fork_deps` increments `agent_depth` on each delegation | `tests/test_flow_delegation_agent.py` |
| `fork_deps` starts child with fresh `runtime` state | `tests/test_flow_delegation_agent.py` |
| `run_in_turn` raises `ModelRetry` at `MAX_AGENT_DEPTH` | `tests/test_flow_delegation_agent.py` |
| Orchestrator serves a real prompt-response turn end-to-end | `tests/test_flow_chat_loop.py::test_plain_text_routes_to_foreground_turn` |
| `SESSION_REVIEW_SPEC.tool_names` matches the documented set | `tests/test_flow_session_review.py::test_session_reviewer_spec_tool_names` |
| Session review writes `run.json` and `run.md` reports | `tests/test_flow_session_review.py::test_write_review_report_creates_json_and_md` |
| Session review instructions include the skill manifest | `tests/test_flow_session_review.py::test_session_review_instructions_include_skills_manifest` |
| `refresh_skills` makes pass-B see pass-A's skill writes | `tests/test_flow_review_background.py::test_child_deps_refresh_surfaces_disk_skill_when_parent_registry_stale` |
| Child-deps skill refresh does not mutate parent registry | `tests/test_flow_review_background.py::test_child_refresh_does_not_mutate_parent_registry` |
| Post-turn hook spawns the real session-review daemon and writes a report | `tests/test_flow_review_background.py::test_post_turn_hook_fires_real_review_writes_report` |
