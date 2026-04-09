# TODO: Tier 2 Tool Surface Gaps

**Slug:** `tier2-tool-surface`
**Task type:** `code-feature`
**Post-ship:** `/sync-doc`

---

## Context

Research: [RESEARCH-peer-capability-surface.md](reference/RESEARCH-peer-capability-surface.md), [RESEARCH-tools-fork-cc.md](reference/RESEARCH-tools-fork-cc.md), [RESEARCH-tools-codex.md](reference/RESEARCH-tools-codex.md), [RESEARCH-tools-gemini-cli.md](reference/RESEARCH-tools-gemini-cli.md), [RESEARCH-tools-opencode.md](reference/RESEARCH-tools-opencode.md)

Tier 1 (4/4 convergence) tracked separately: [TODO-tier1-workflow-tools.md](TODO-tier1-workflow-tools.md).
Tier 1.5 generic delegation harness tracked separately: [TODO-tier15-agent-harness.md](TODO-tier15-agent-harness.md).

This file is ordered by implementation confidence and local leverage, not just by peer-count parity.

| Gap | Peers (of 4 CLI peers) | co-cli today | Why tier 2 |
|---|---|---|---|
| Structured task tracker | 3/4 (fork-cc, gemini-cli, codex) | Session todos are structured but replace-all | Clear local workflow win; low architectural risk |
| MCP resource surface | 3/4 (fork-cc, codex, opencode) | MCP tools only; no resource/runtime handle exposure | Real gap, but needs runtime plumbing first |
| Agent-callable skill activation | 3/4 (fork-cc, gemini-cli, opencode) | Skills are slash-command prompt overlays only | Useful, but must fit `co`'s existing skill model rather than cargo-cult peer tools |

---

## Problem

1. **Task tracking is structured but not targetable** — `write_todos`/`read_todos` already validate structured items (`content`, `status`, `priority`), but updates replace the full list every time. The model cannot address one task by stable ID, query one task, or update status without rewriting unrelated items.
2. **MCP resources are still invisible** — co-cli discovers MCP tools, but not MCP resources or templates. The installed SDK supports resource listing and reading, yet current deps do not retain connected MCP server handles for tool-time access.
3. **Skills are model-visible in metadata but not agent-callable** — `get_skill_registry()` exposes a model-facing list of visible skills, but skills still execute only through slash-command dispatch into `DelegateToAgent`. There is no tool path the model can invoke mid-run.

---

## TASK-1: Structured task tracker

files: `co_cli/tools/tasks.py` (new), `co_cli/tools/todo.py`, `co_cli/deps.py`, `co_cli/agent.py`, `tests/test_tools_tasks.py` (new)

**Reason to do this first:**

- It upgrades an existing always-loaded workflow surface instead of introducing a new capability class.
- It is fully session-local and does not depend on MCP, frontend changes, or prompt-injection mechanics.
- It removes real friction in long multi-step turns without changing the broader architecture.

**Peer reference:**

| Peer | Tools | State model |
|------|-------|-------------|
| fork-cc | `TaskCreate`, `TaskGet`, `TaskUpdate`, `TaskList` | Per-task ID, status, description |
| gemini-cli | `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`, `tracker_add_dependency`, `tracker_visualize` | Per-task ID, status, dependencies, visualization |
| codex | `assign_task`, `wait_agent` | Task assignment to agents with completion tracking |

Common pattern: stable task identity plus targeted update/query. Dependencies and visualization are optional tier-3 follow-ons, not required for this tier.

**State** — add `TaskInfo` dataclass to deps:

```python
@dataclass
class TaskInfo:
    id: str
    description: str
    status: str       # "pending" | "in_progress" | "completed" | "cancelled"
    priority: str     # "high" | "medium" | "low"
    note: str = ""
```

Add `tasks: dict[str, TaskInfo] = field(default_factory=dict)` to `CoSessionState`.

**Tools**:

`create_task(ctx, description: str, priority: str = "medium") -> ToolReturn`
- Auto-assign next ID (`t1`, `t2`, ...)
- Validate priority
- Create `TaskInfo(..., status="pending")`
- Return `tool_output(f"Created {id}: {description}", ctx=ctx, task_id=id)`
- Register: `_reg(create_task, approval=False, load=LoadPolicy.ALWAYS, search_hint="task create add todo")`

`update_task(ctx, task_id: str, status: str, note: str = "") -> ToolReturn`
- Validate task_id exists
- Validate status in allowed set
- Update only the addressed task
- Return `tool_output(f"{task_id} -> {status}", ctx=ctx, task_id=task_id)`
- Register: `_reg(update_task, approval=False, load=LoadPolicy.ALWAYS, search_hint="task update status progress")`

`get_task(ctx, task_id: str) -> ToolReturn`
- Validate task_id exists
- Return formatted task details
- Register: `_reg(get_task, approval=False, load=LoadPolicy.ALWAYS, search_hint="task get detail")`

`list_tasks(ctx) -> ToolReturn`
- Return formatted tasks grouped by status
- Register: `_reg(list_tasks, approval=False, load=LoadPolicy.ALWAYS, search_hint="task list all todos")`

**Compatibility path**:

- Keep `write_todos`/`read_todos` for one minor version.
- Mark both descriptions as deprecated and point to the task tools.
- Implement `read_todos` as a compatibility view over the new session task state if practical; otherwise keep the old list in parallel only during migration.
- Do not remove the old tools in the same change that introduces the new ones.

done_when:
- `uv run pytest tests/test_tools_tasks.py`:
  - create_task assigns ID and stores status=`pending`
  - update_task updates only one task
  - invalid task ID returns `ModelRetry`
  - invalid status returns `ModelRetry`
  - list_tasks groups by status
  - empty state returns a no-tasks message
- All 4 tools are registered in the agent

---

## TASK-2: MCP resource surface

files: `co_cli/tools/mcp_resources.py` (new), `co_cli/deps.py`, `co_cli/bootstrap/core.py`, `co_cli/agent.py`, `tests/test_tools_mcp_resources.py` (new)

**Reason this stays tier 2:**

- The SDK already supports the resource APIs, so this is a real product gap.
- But current `co` runtime only retains discovered MCP tool metadata, not connected MCP server objects.
- That makes this a plumbing task first and a tool task second.

**Peer reference:**

| Peer | Tools | Source |
|------|-------|--------|
| fork-cc | `ListMcpResourcesTool`, `ReadMcpResourceTool` | `/tools/{ListMcpResourcesTool,ReadMcpResourceTool}/` |
| codex | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` | `/tools/handlers/mcp_resource.rs` |
| opencode | `readResource(clientName, uri)` | Direct client call |

Common pattern: list available resources across connected servers, optionally list templates, read a resource by URI.

**Runtime prerequisite**:

- Add an MCP runtime handle map to deps, for example `mcp_servers: dict[str, MCPServer]`.
- Populate it from the connected toolsets in `create_deps()`.
- Do not try to reconstruct server handles from `tool_index`; it only stores metadata.

**Tools**:

`list_mcp_resources(ctx) -> ToolReturn`
- Iterate connected MCP servers from `ctx.deps`
- Call `server.list_resources()`
- Format rows with server name, URI, and description/title
- Return `tool_output(formatted, ctx=ctx, count=total)`
- Register: `_reg(list_mcp_resources, approval=False, load=LoadPolicy.DEFERRED, search_hint="mcp resources list available")`

`list_mcp_resource_templates(ctx) -> ToolReturn`
- Iterate connected MCP servers
- Call `server.list_resource_templates()`
- Format rows with server name, template URI, and description/title
- Return `tool_output(formatted, ctx=ctx, count=total)`
- Register: `_reg(list_mcp_resource_templates, approval=False, load=LoadPolicy.DEFERRED, search_hint="mcp resource templates list")`

`read_mcp_resource(ctx, uri: str, server_name: str | None = None) -> ToolReturn`
- If `server_name` is provided, read from that server only
- Otherwise resolve by matching listed resources by URI
- Return `ModelRetry` on no match or ambiguous match
- Call `server.read_resource(uri)`
- Return `tool_output(content, ctx=ctx, uri=uri, server_name=resolved_server)`
- Register: `_reg(read_mcp_resource, approval=False, load=LoadPolicy.DEFERRED, search_hint="mcp resource read fetch")`

**Scope boundary**:

- This task is read-only resource access only.
- Do not add MCP prompt execution, MCP write operations, or prompt-template instantiation in the same change.

done_when:
- `uv run pytest tests/test_tools_mcp_resources.py`:
  - no connected servers returns a no-resources message
  - templates listing works when supported
  - invalid URI returns `ModelRetry`
  - ambiguous URI without `server_name` returns `ModelRetry`
  - server-scoped read succeeds when the resource exists
- All 3 tools are registered in the agent

---

## TASK-3: Agent-callable skill activation

files: `co_cli/tools/skills.py` (new), `co_cli/commands/_commands.py`, `co_cli/deps.py`, `co_cli/agent.py`, `tests/test_tools_skills.py` (new)

**Reason this is last in tier 2:**

- The user-visible need is real, but the current skill system is intentionally not a tool system.
- A naive `run_skill = return markdown blob` implementation would bypass important `co` semantics and create a parallel skill architecture.
- This task is only worth doing if it reuses the existing skill registry, argument expansion rules, and model-visibility gates.

**Peer reference:**

| Peer | Tool | Behavior |
|------|------|----------|
| fork-cc | `Skill` | Looks up bundled/user skill by name, injects prompt into conversation |
| gemini-cli | `activate_skill` | Loads skill file, injects as system context |
| opencode | `SkillTool` | Resolves skill from global/project dirs, injects prompt |

Common pattern: name lookup, model-visible filtering, load prompt content, inject instructions into the active run.

**`co`-specific constraints**:

- Resolve skills from `ctx.deps.skill_commands`, not a new capability registry.
- Respect existing model-visibility rules: hidden skills (`disable_model_invocation=True` or blank description) are not callable.
- Reuse shared argument-expansion logic from slash dispatch instead of duplicating `$ARGUMENTS` / `$1` / `$2` handling.
- Do not claim `search_tools` covers skills; it currently does not.

**Tool**:

`run_skill(ctx, skill_name: str, arguments: str = "") -> ToolReturn`
- Look up visible skill by name in `ctx.deps.skill_commands`
- Unknown or hidden skill -> `ModelRetry`
- Expand arguments using shared helper extracted from `dispatch()`
- Return wrapped skill content as tool output so the model can follow it in the same turn
- Register: `_reg(run_skill, approval=False, load=LoadPolicy.DEFERRED, search_hint="skill activate invoke prompt")`

**Gate on skill-env**:

- Current skill env application is wired through REPL slash-command dispatch in `main.py`.
- V1 agent-callable skill activation should either:
  - reject skills that require `skill_env`, or
  - add a general turn-scoped skill-env mechanism usable from both slash dispatch and tool invocation.
- Do not silently ignore `skill_env`; that would make tool-invoked skills behave differently from slash-invoked skills.

**Discovery gate**:

- Because `search_tools` does not search skills, implementation must explicitly decide how the model learns callable skills:
  - either expose the visible skill registry in prompt/instructions, or
  - add a small companion discovery tool in the same change.
- Do not leave skill invocation discoverability implicit.

done_when:
- `uv run pytest tests/test_tools_skills.py`:
  - valid visible skill returns expanded skill content
  - unknown skill returns `ModelRetry`
  - hidden skill returns `ModelRetry`
  - argument placeholders expand correctly
  - skill with `skill_env` follows the chosen v1 policy explicitly
- `"run_skill"` is registered in the agent
