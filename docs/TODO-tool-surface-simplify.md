# TODO: Flat Per-Tool Loading Policy with Progressive Injection

**Slug:** `tool-surface-simplify`
**Task type:** `refactor`
**Supersedes:** `docs/TODO-agent-toolset.md`, `docs/TODO-agent-toolset-bm25.md`
**Replaces:** the earlier `tool-surface-simplify` draft

---

## Context

`co-cli` needs one critical behavior: dynamic and progressive tool injection into the LLM
context. That behavior exists today, but the policy is spread across multiple layers:

- hard-coded global sets in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py#L42)
  and [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py#L52)
- transient runtime filter state in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py#L342)
- orchestration-owned filter computation in
  [context/_orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/_orchestrate.py#L63)
- session unlock state in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py#L320)
- taxonomy metadata (`family`) in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py#L285)
  that currently leaks into search behavior in
  [tool_search.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_search.py#L9)

Local peer-system research on `fork-claude-code` points to a simpler shape:

- the core runtime type is `Tool`, not `ToolConfig` or `ToolInfo`
- progressive visibility is controlled by flat per-tool flags such as
  `alwaysLoad`, `shouldDefer`, and `searchHint`
- there is no first-class tool-family abstraction in the tool-loading path

For `co-cli`, the design implication is:

- keep progressive injection
- remove family as a design axis
- move loading policy onto each registered tool definition when the SDK surface allows it
- collapse visibility policy into the toolset filter itself

---

## Problem & Outcome

**Problem:** the current system represents tool-loading policy in too many places:

- `ALWAYS_ON_TOOL_NAMES`
- `CORE_TOOL_NAMES`
- `CoSessionState.granted_tools`
- `CoRuntimeState.active_tool_filter`
- `compute_segment_filter()`
- `ToolConfig.family` as part of search matching and search display

**Failure cost:** adding or recategorizing a tool requires coordinated changes across 4+ files
and 3 conceptual layers (metadata, filter policy, orchestration state). A missed site causes
silent tool-visibility bugs that are hard to diagnose.

**Outcome:** redesign tool loading around flat per-tool policy:

- always load
- should defer
- search hint
- discovered in this session

The model-visible surface then becomes:

- all tools where `always_load` is true
- plus all tools whose names are in `session.discovered_tools`

No family-aware routing. No orchestration-owned filter state. No hard-coded core/always-on
sets outside per-tool loading policy.

**Token budget impact:** the always-loaded set (15 tools) matches the current `CORE_TOOL_NAMES`
set plus `list_memories`. The deferred set (~15 native + MCP tools) saves ~3-4K schema tokens
per turn. The deferred-tool prompt (`build_deferred_tool_prompt`) adds ~300-500 tokens per turn
listing undiscovered deferred tool names and descriptions — still a net win over sending full
schemas. This refactor preserves the existing token savings while simplifying the policy path.

---

## Scope

**In scope:**

- replace global native-tool loading sets with per-tool loading flags on `ToolConfig`
- remove `family` from `ToolConfig` and all loading/discovery behavior
- remove `active_tool_filter` and `compute_segment_filter()`
- rename `granted_tools` to `discovered_tools`
- rename `tool_catalog` to `tool_index`
- remove parallel `tool_names` / `tool_approvals` from `CoCapabilityState`
- simplify `AgentCapabilityResult` to not return tool index
- add `resume_tool_names` to `CoRuntimeState` for approval-resume narrowing
- add `build_deferred_tool_prompt()` for prompt-visible deferred-tool awareness
- add dynamic `@agent.instructions` hook for deferred-tool prompt injection
- update status, banner, and slash-command code to derive from `tool_index`
- update `search_tools()` to rank deferred tools only, remove family
- normalize MCP tools into `tool_index` for unified visibility policy
- update all tests and evals that reference removed APIs

**Out of scope:**

- changing approval semantics
- changing MCP transport setup or MCP execution dispatch (follow-on `TODO-mcp-unified-dispatch.md`)
- replacing `search_tools` with a different user-facing primitive
- introducing family-specific toolsets
- introducing `PreparedToolset` unless `FunctionToolset.filtered()` cannot express the policy
- rewriting sub-agent construction via shared `_build_agent_core()` (follow-on TODO)
- `UnifiedToolset(AbstractToolset)` custom toolset class (follow-on with MCP dispatch)

---

## Behavioral Constraints

1. **Load-state invariant enforced at registration time:** every tool must be exactly one of
   `(always_load=True, should_defer=False)` or `(always_load=False, should_defer=True)`.
   An `AssertionError` must be raised if any other combination is passed to the registration
   path. This is checked at agent-construction time, not deferred to runtime filtering.

2. **Deferred tools become callable no earlier than the next `get_tools()` evaluation:** when
   `search_tools()` adds a name to `session.discovered_tools`, the tool schema must not appear
   in the same segment's tool response. It becomes callable only on the next segment boundary.
   This is a contract on the visibility rule, not an implementation-specific timing assumption.

3. **Approval-resume narrowing must expose all approved tools, not just one:** when a segment
   yields N approved deferred tool calls, the filter must expose all N approved deferred tool
   names plus all always-loaded tools on the immediate resume hop. The mechanism is:
   `runtime.resume_tool_names` holds the N approved names; the `_filter` function checks
   `resume_tool_names` first, then `entry.always_load` separately. No other discovered (but
   unapproved) deferred tools are visible on the immediate resume hop.

4. **MCP tools must participate in the same visibility policy as native tools:** MCP tools
   are normalized into `tool_index` with the same `always_load`/`should_defer` flags and
   filtered by the same `_filter` function. Raw `MCPServer` toolsets remain attached to the
   agent for execution dispatch until the MCP unification delivery ships separately.

5. **Native wins on name collision in `tool_index`:** if a normalized MCP tool name collides
   with a native tool name, the native tool wins unconditionally. Between MCP servers, the
   earlier configured server wins. Collisions are logged, never merged.

6. **No transcript-owned deferred-tool state:** the source of truth for which deferred tools
   exist and which are discovered is always `capabilities.tool_index` and
   `session.discovered_tools`. Compaction, message history, and transcript deltas must never
   own or replicate this state.

7. **Tool execution errors are model-visible, not turn-fatal:** approval denial, broken MCP
   calls, and native tool failures return structured tool errors to the model. They do not
   crash the orchestration loop unless the framework itself cannot recover.

---

## Target Design

### 1. One flat per-tool loading policy

Replace the current `ToolConfig` fields:

```python
@dataclass(frozen=True)
class ToolConfig:
    name: str
    description: str
    approval: bool
    source: str               # "native" | "mcp"
    integration: str | None = None
    always_load: bool = False
    should_defer: bool = False
    search_hint: str | None = None
```

Notes:

- remove `family`
- `always_load` and `should_defer` encode the progressive-injection policy
- `search_hint` is the only extra search-specific field needed
- `source` stays because native vs MCP is a real execution boundary
- `integration` stays because it is concrete user-facing identity, unlike `family`
- load-state invariant:
  - allowed state 1: `always_load=True` and `should_defer=False`
    - tool is callable on turn one
  - allowed state 2: `always_load=False` and `should_defer=True`
    - tool is deferred and must be discovered before it becomes callable
  - forbidden state 1: `always_load=False` and `should_defer=False`
    - this would create a tool that is neither always-loaded nor deferred-searchable
  - forbidden state 2: `always_load=True` and `should_defer=True`
    - `always_load` already means no deferral is needed; this combination is invalid

MCP tool normalization into `tool_index`:

- after `discover_mcp_tools()` runs, normalize discovered MCP tools into `tool_index` entries
  with `source="mcp"`, `should_defer=True` by default
- MCP tools in `tool_index` participate in the same visibility policy as native tools
- MCP toolsets remain attached to the agent for execution (dispatch unification is a follow-on)
- the `_filter` function applies the same `always_load`/`should_defer`/`discovered_tools` rule
  to MCP tools that are in `tool_index`
- MCP tools not yet in `tool_index` (because discovery hasn't run or failed) remain accessible
  only through the raw MCP toolset attachment

### 2. One session discovery/unlock set

Replace `CoSessionState.granted_tools` with:

```python
discovered_tools: set[str]
```

Semantics:

- session-scoped names of deferred tools already exposed to the model
- persists across turns
- main agents start empty
- sub-agents inherit from parent session (existing behavior, no change)

### 3. One visibility rule inside the toolset filter

The filter in `agent.py` stops reading runtime state and instead uses per-tool loading policy
plus the session discovery set:

```python
def _filter(ctx: RunContext[CoDeps], tool_def: ToolDefinition) -> bool:
    entry = ctx.deps.capabilities.tool_index.get(tool_def.name)
    resume = ctx.deps.runtime.resume_tool_names

    if resume is not None:
        # Approval-resume: only approved deferred tools + always-loaded tools
        if tool_def.name in resume:
            return True
        if entry is not None and entry.always_load:
            return True
        return False  # hide un-indexed MCP tools and non-approved deferred tools

    # Normal turn
    if entry is None:
        return True  # MCP tools not yet in tool_index pass through (attached toolset)
    if entry.always_load:
        return True
    return tool_def.name in ctx.deps.session.discovered_tools
```

Implications:

- remove `ALWAYS_ON_TOOL_NAMES`
- remove `CORE_TOOL_NAMES`
- remove `active_tool_filter`
- remove `compute_segment_filter()`
- MCP tools in `tool_index` are filtered by the same rule
- MCP tools NOT in `tool_index` (raw toolset) pass through the filter (entry is None → True)
- the `FunctionToolset.filtered()` wrapper remains; no `UnifiedToolset` in this delivery

### 4. Deferred-tool index is prompt-visible but not callable

Deferred tools need two different representations:

- a prompt-visible index entry so the model knows they exist
- a callable tool schema only after unlock

Rule:

- deferred tools are not sent as callable schemas until unlocked
- deferred tools must still be announced to the model by name and description
- the deferred-tool index is dynamic, rebuilt from current state each turn

Concrete implementation:

- add `co_cli/context/_deferred_tool_prompt.py` (new) with:
  - `build_deferred_tool_prompt(tool_index, discovered_tools) -> str | None`
  - input: `tool_index`, `discovered_tools`
  - output: formatted prompt text listing undiscovered deferred tools, or `None` if empty
  - pure function: no reads of message history, compaction state, or runtime mutation
- one dynamic `@agent.instructions` hook in `co_cli/agent.py` calls it
- this is the only deferred-tool prompt injection path

State ownership:

- `capabilities.tool_index` owns tool existence, source, loading policy, search metadata
- `session.discovered_tools` owns session-scoped unlock state
- `runtime.resume_tool_names` owns temporary post-approval narrowing
- no transcript/message-history object owns tool availability state

The deferred-tool prompt should include: `name`, `description`, `integration` when present,
`search_hint` when present.

### 5. `search_tools()` becomes a deferred-tool discoverer/unlocker

`search_tools()` remains, but its job becomes narrower and cleaner:

- keyword-rank deferred tools only
- search over `name + description + integration + search_hint`
- support exact-name lookup across all tools so already-loaded tools can be reported as
  `already available` without polluting deferred-tool ranking
- add matches to `session.discovered_tools`
- report already-loaded tools as already available
- discovered tools become callable on the next model step only

Do not search over `family`. Do not present results as `[family]`.

Representative result format:

```text
Found 2 tool(s):
  edit_file unlocked: Edit a file by replacing text
  create_gmail_draft unlocked (google_gmail): Create a Gmail draft
```

### 6. Boolean-only internal tool state

Stored tool policy/state stays boolean-only:

- `always_load`
- `should_defer`
- `approval`

Runtime state stays purpose-specific:

- `session.discovered_tools`
- `runtime.resume_tool_names`

Rules:

- `tool_index` stores boolean policy/state fields, not ad-hoc prose labels
- capability/status rendering derives user-facing wording at the display edge
- prompt builders and tests assert on boolean/runtime-owned state, not prose fragments

### 7. Capability surface is the source of truth

Single-source rule:

- tool capability state comes from `tool_index` (replaces parallel `tool_names` / `tool_approvals`)
- skill capability state comes from `skill_commands` / `skill_registry`
- do not duplicate tool names/approvals as separate parallel state

Derive when needed:

- tool count from `len(tool_index)`
- tool names from `tool_index.keys()`
- approval lookup from `tool_index[name].approval`

---

## Loading Policy

### Always-loaded tools

These should be visible on turn one without a search round-trip:

- `check_capabilities`
- `read_todos`
- `write_todos`
- `search_tools`
- `search_memories`
- `search_knowledge`
- `search_articles`
- `read_article`
- `read_file`
- `list_directory`
- `find_in_files`
- `web_search`
- `web_fetch`
- `run_shell_command`
- `list_memories`

For these tools: `always_load=True`, `should_defer=False`

### Deferred tools

These should be progressively injected only after `search_tools()`:

- file-write tools (`edit_file`, `write_file`)
- knowledge-write tools (`save_memory`, `update_memory`, `append_memory`, `save_article`)
- background-task tools (`start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`)
- sub-agent tools (`run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent`)
- optional connector tools (obsidian, google)
- MCP tools by default unless explicitly always-loaded

For these tools: `always_load=False`, `should_defer=True`

Examples of `search_hint`:

- `edit_file`: `search_hint="modify patch update file"`
- `save_memory`: `search_hint="remember save note memory"`
- `start_background_task`: `search_hint="background async long running task"`
- `create_gmail_draft`: `search_hint="gmail email draft compose"`

---

## Implementation Plan

### TASK-1a: Replace taxonomy metadata with loading-policy metadata

**prerequisites:** none

**What to do:**

- `co_cli/deps.py`
  - replace `family: str` with `always_load: bool`, `should_defer: bool`, `search_hint: str | None`
    on `ToolConfig`
  - rename `tool_catalog` to `tool_index` on `CoCapabilityState`
  - remove `tool_names` and `tool_approvals` from `CoCapabilityState`
  - rename `granted_tools` to `discovered_tools` on `CoSessionState`
  - add `resume_tool_names: frozenset[str] | None = None` to `CoRuntimeState`
  - remove `active_tool_filter` from `CoRuntimeState`
  - update `reset_for_turn()` to clear `resume_tool_names` instead of `active_tool_filter`
  - enforce load-state invariant in `ToolConfig.__post_init__` or equivalent
- `co_cli/agent.py`
  - update `_reg()` to accept `always_load`, `should_defer`, `search_hint` instead of `family`
  - set per-tool loading policy explicitly at every registration site
  - populate `native_catalog` → `tool_index` entries with new fields
  - normalize MCP-discovered tools into `tool_index` entries in `discover_mcp_tools()`
  - update `AgentCapabilityResult`: remove `tool_names` and `tool_approvals`; keep `tool_catalog`
    renamed to `tool_index` so `main.py` can assign it to `deps.capabilities.tool_index` during
    bootstrap. `_build_filtered_toolset()` returns the native catalog (renamed `tool_index`) and
    `main.py` assigns it directly.
  - normalize MCP-discovered tools into `tool_index` entries in `discover_mcp_tools()`
- `co_cli/bootstrap/_bootstrap.py`
  - update any bootstrap code that reads `tool_catalog` → `tool_index`
  - update code that writes `tool_names` / `tool_approvals` → derive from `tool_index`

**files:**

- `co_cli/deps.py`
- `co_cli/agent.py`
- `co_cli/bootstrap/_bootstrap.py`

**done_when:**

```bash
uv run python -c "
from pathlib import Path
from co_cli.deps import ToolConfig, CoCapabilityState, CoSessionState, CoRuntimeState

# ToolConfig has loading-policy fields, no family
tc = ToolConfig(name='test', description='t', approval=False, source='native', always_load=True, should_defer=False)
assert hasattr(tc, 'always_load')
assert hasattr(tc, 'should_defer')
assert hasattr(tc, 'search_hint')
assert not hasattr(tc, 'family')

# Load-state invariant
try:
    ToolConfig(name='bad', description='t', approval=False, source='native', always_load=True, should_defer=True)
    assert False, 'should have raised'
except (ValueError, AssertionError):
    pass

# CoCapabilityState uses tool_index, no parallel structures
caps = CoCapabilityState(tool_index={'test': tc})
assert hasattr(caps, 'tool_index')
assert not hasattr(caps, 'tool_names')
assert not hasattr(caps, 'tool_approvals')
assert not hasattr(caps, 'tool_catalog')

# Session uses discovered_tools
session = CoSessionState()
assert hasattr(session, 'discovered_tools')
assert not hasattr(session, 'granted_tools')

# Runtime uses resume_tool_names, no active_tool_filter
runtime = CoRuntimeState()
assert hasattr(runtime, 'resume_tool_names')
assert not hasattr(runtime, 'active_tool_filter')

# AgentCapabilityResult has tool_index, not tool_names/tool_approvals
import dataclasses
from co_cli.agent import AgentCapabilityResult
field_names = {f.name for f in dataclasses.fields(AgentCapabilityResult)}
assert 'tool_index' in field_names
assert 'tool_names' not in field_names
assert 'tool_approvals' not in field_names

print('PASS: metadata is loading-policy-first, family-free, single-source')
"
```

Integration boundary check (run after `_reg()` sites are updated):

```bash
uv run python -c "
from co_cli.agent import _build_filtered_toolset
from co_cli.deps import CoConfig
from co_cli.config import settings

config = CoConfig.from_settings(settings)
_, _, native_catalog = _build_filtered_toolset(config)

# Representative always-loaded tool
assert native_catalog['read_file'].always_load is True
assert native_catalog['read_file'].should_defer is False

# Representative deferred tool
assert native_catalog['edit_file'].always_load is False
assert native_catalog['edit_file'].should_defer is True

# list_memories is always-loaded
assert native_catalog['list_memories'].always_load is True

print('PASS: real agent build produces correct loading-policy flags')
"
```

**success_signal:** `ToolConfig` carries `always_load`/`should_defer`/`search_hint` instead
of `family`. `CoCapabilityState` uses `tool_index` as the single source. Session and runtime
state use the new field names.

---

### TASK-1b: Move visibility policy into the toolset filter

**prerequisites:** [TASK-1a]

**What to do:**

- `co_cli/agent.py`
  - remove `ALWAYS_ON_TOOL_NAMES`
  - remove `CORE_TOOL_NAMES`
  - rewrite `_filter` to use per-tool `always_load` / `should_defer` + `session.discovered_tools`
    + `runtime.resume_tool_names` (see Target Design §3)
  - for MCP tools not in `tool_index`: pass through (return True)
  - for MCP tools in `tool_index`: apply the same visibility rule
- `co_cli/context/_orchestrate.py`
  - delete `compute_segment_filter()`
  - remove all reads/writes of `active_tool_filter`
  - on approval resume, set `deps.runtime.resume_tool_names` to the approved deferred tool-name
    set before the next segment and clear it afterward
  - ensure all approved deferred tools in a multi-approval segment are included in
    `resume_tool_names`

**files:**

- `co_cli/agent.py`
- `co_cli/context/_orchestrate.py`

**done_when:**

```bash
uv run python -c "
import inspect
from pathlib import Path

agent_src = Path('co_cli/agent.py').read_text(encoding='utf-8')
assert 'CORE_TOOL_NAMES' not in agent_src
assert 'ALWAYS_ON_TOOL_NAMES' not in agent_src
assert 'active_tool_filter' not in agent_src

import co_cli.context._orchestrate as orch
assert not hasattr(orch, 'compute_segment_filter')
orch_src = inspect.getsource(orch)
assert 'active_tool_filter' not in orch_src
assert 'resume_tool_names' in orch_src

print('PASS: visibility is derived from tool_index + discovered_tools, no global sets or orchestration filter')
"
```

**success_signal:** no global native-loading sets and no orchestration-owned filter state remain.
Visibility is entirely driven by per-tool `always_load`/`should_defer` plus `session.discovered_tools`.

---

### TASK-2: Rebuild `search_tools()` and add deferred-tool prompt

**prerequisites:** [TASK-1a, TASK-1b]

**What to do:**

- `co_cli/tools/tool_search.py`
  - keyword-rank only tools with `should_defer=True`
  - build search text from `name`, `description`, `integration`, `search_hint`
  - remove all use of `family`
  - replace `ctx.deps.session.granted_tools` with `ctx.deps.session.discovered_tools`
  - report always-loaded tools as `already available`
  - support exact-name lookup across all tools before deferred keyword ranking
  - keep next-step unlock semantics
- `co_cli/context/_deferred_tool_prompt.py` (new)
  - `build_deferred_tool_prompt(tool_index, discovered_tools) -> str | None`
  - pure function: reads `tool_index` and `discovered_tools`, returns formatted text
  - lists undiscovered deferred tools by name + description + integration + search_hint
  - returns `None` when no deferred tools remain undiscovered
- `co_cli/agent.py`
  - add one dynamic `@agent.instructions` hook that calls `build_deferred_tool_prompt()`
  - remove the existing `add_tool_surface_hint` instruction hook — `build_deferred_tool_prompt()`
    subsumes its purpose with dynamic, data-driven content
  - keep `search_tools` description generic (not an authoritative list of deferred tool names)

**files:**

- `co_cli/tools/tool_search.py`
- `co_cli/context/_deferred_tool_prompt.py`
- `co_cli/agent.py`

**done_when:**

```bash
uv run python -c "
from pathlib import Path

search_src = Path('co_cli/tools/tool_search.py').read_text(encoding='utf-8')
assert 'family' not in search_src
assert 'discovered_tools' in search_src
assert 'should_defer' in search_src or 'always_load' in search_src

assert Path('co_cli/context/_deferred_tool_prompt.py').is_file()
prompt_src = Path('co_cli/context/_deferred_tool_prompt.py').read_text(encoding='utf-8')
assert 'def build_deferred_tool_prompt' in prompt_src
assert 'tool_index' in prompt_src
assert 'discovered_tools' in prompt_src

agent_src = Path('co_cli/agent.py').read_text(encoding='utf-8')
assert '@agent.instructions' in agent_src or 'agent.instructions' in agent_src
assert 'build_deferred_tool_prompt' in agent_src

print('PASS: search_tools ranks deferred tools, deferred-tool prompt is rebuilt from runtime state')
"
```

**success_signal:** progressive injection works via per-tool flags, prompt-visible deferred-tool
indexing, and session discovery only. Deferred-tool awareness is single-source runtime/session state.

---

### TASK-3: Collapse parallel capability structures

**prerequisites:** [TASK-1a, TASK-1b, TASK-2]

**What to do:**

- `co_cli/main.py`
  - stop assigning `tool_names` / `tool_approvals` from `AgentCapabilityResult`
  - derive tool state from `deps.capabilities.tool_index`
  - normalize MCP-discovered tools into `tool_index` after `discover_mcp_tools()` completes
- `co_cli/bootstrap/_bootstrap.py`
  - stop building parallel `tool_names` list
  - derive from `tool_index`
- `co_cli/bootstrap/_check.py`
  - derive `tool_names`, `tool_count`, `tool_approvals`, and `source_counts` from `tool_index`
  - remove `family_counts`
  - render user-facing status text from boolean state at the display edge
- `co_cli/tools/capabilities.py`
  - remove "Tools by family"
  - report by source and native/MCP counts only
- `co_cli/bootstrap/_banner.py`
  - derive `tool_count` from `tool_index`
- `co_cli/commands/_commands.py`
  - remove `tool_names` from `CommandContext`
  - derive visible tool names from `deps.capabilities.tool_index`

**files:**

- `co_cli/main.py`
- `co_cli/bootstrap/_bootstrap.py`
- `co_cli/bootstrap/_check.py`
- `co_cli/tools/capabilities.py`
- `co_cli/bootstrap/_banner.py`
- `co_cli/commands/_commands.py`

**done_when:**

```bash
uv run python -c "
from pathlib import Path

for rel in [
    'co_cli/main.py',
    'co_cli/bootstrap/_bootstrap.py',
    'co_cli/bootstrap/_check.py',
    'co_cli/tools/capabilities.py',
    'co_cli/bootstrap/_banner.py',
    'co_cli/commands/_commands.py',
]:
    src = Path(rel).read_text(encoding='utf-8')
    assert 'tool_index' in src, f'{rel} missing tool_index'
    assert 'tool_catalog' not in src, f'{rel} still has tool_catalog'

check_src = Path('co_cli/bootstrap/_check.py').read_text(encoding='utf-8')
assert 'family_counts' not in check_src

cap_src = Path('co_cli/tools/capabilities.py').read_text(encoding='utf-8')
assert 'family' not in cap_src.lower() or 'family' not in cap_src

print('PASS: capability surface derives tool state from tool_index, no parallel structures')
"
```

**success_signal:** capability surface is single-source; tool names and approvals are derived
from `tool_index`. Skills remain separate capability entries.

---

### TASK-4: Update tests and evals for new APIs

**prerequisites:** [TASK-1a, TASK-1b, TASK-2, TASK-3]

**What to do:**

- Grep scan across `tests/` and `evals/` for all references to removed/renamed APIs:
  `tool_catalog`, `tool_names`, `tool_approvals`, `granted_tools`, `active_tool_filter`,
  `compute_segment_filter`, `CORE_TOOL_NAMES`, `ALWAYS_ON_TOOL_NAMES`, `family`
- Update all callers to use `tool_index`, `discovered_tools`, `resume_tool_names`
- `tests/test_agent.py`
  - remove family assertions
  - assert loading-policy fields on representative tools
- `tests/test_tool_search.py`
  - assert unlock behavior through `session.discovered_tools`
  - assert always-loaded tools are reported as `already available`
  - assert deferred keyword ranking excludes always-loaded tools
- `tests/test_orchestration_filter.py`
  - delete; it only tests removed APIs (`compute_segment_filter`)
- Add behavioral test coverage (new or expanded test files):
  - deferred tools are not callable before discovery
  - deferred tools become callable after `search_tools()` discovery
  - approval-resume exposes only approved deferred tools + always-loaded
  - MCP tools in `tool_index` obey the same visibility rule
  - deferred-tool prompt is rebuilt from `tool_index` each turn
- Update `tests/test_commands.py`, `tests/test_tool_calling_functional.py`,
  `tests/test_skills_loader.py`, and any other files found by grep scan
- Update affected eval files in `evals/` (`_common.py`, `eval_tool_chains.py`,
  `eval_knowledge_pipeline.py`, etc.)

**files:**

- `tests/test_agent.py`
- `tests/test_tool_search.py`
- `tests/test_orchestration_filter.py` (delete)
- `tests/test_commands.py`
- `tests/test_tool_calling_functional.py`
- `tests/test_skills_loader.py`
- `evals/_common.py`
- additional files found by grep scan

**done_when:**

```bash
mkdir -p .pytest-logs
uv run pytest tests/ -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-tool-surface-simplify.log
```

All tests pass. No references to removed APIs remain in `tests/` or `evals/`.

**success_signal:** tests describe the actual design: flat per-tool loading policy plus
progressive injection. No stale API references remain.

---

## Dependency Order

```text
TASK-1a  replace taxonomy metadata with loading-policy metadata
  └─> TASK-1b  move visibility policy into the toolset filter
        └─> TASK-2  rebuild search_tools and add deferred-tool prompt
              └─> TASK-3  collapse parallel capability structures
                    └─> TASK-4  update tests and evals for new APIs
```

---

## Follow-on Work (out of scope for this delivery)

1. **MCP unified dispatch** (`TODO-mcp-unified-dispatch.md`): `UnifiedToolset(AbstractToolset)`,
   `co_cli/mcp/_lifecycle.py`, `co_cli/mcp/_refresh.py`, atomic MCP slice replacement,
   collision policy enforcement, turn-boundary refresh, notification handlers. Removes raw
   `MCPServer` toolset attachment entirely.

2. **Sub-agent unified construction**: shared `_build_agent_core()`, `discovered_tools_seed`,
   elimination of `@agent.tool` in `_subagent_agents.py`.

3. **Weighted keyword scoring** (adopt fork-cc's `searchToolsWithKeywords` pattern):
   replace flat token-overlap scoring in `search_tools()` with weighted tiers —
   name-part exact match (10), substring match (5), `search_hint` word-boundary (4),
   description word-boundary (2). Add `\b`-regex matching to avoid false positives
   (e.g. "create" matching "recreate"). Add MCP name parsing (`mcp__server__action` →
   `[server, action]` parts) with +2 MCP bonus. Add required-term syntax (`+slack send`
   = must contain "slack", rank by "send"). Revisit when MCP tool count exceeds ~20 or
   users report wrong search results. Reference implementation:
   `~/workspace_genai/fork-claude-code/tools/ToolSearchTool/ToolSearchTool.ts`.

---

## Non-Goals

This refactor does not try to make `co-cli` mimic the peer repo's names.

The useful peer-system lesson is structural:

- flat tool object
- per-tool loading flags
- progressive injection without tool families

It is not necessary to copy the exact TypeScript naming.

---

## Final — Team Lead

Plan approved.

C3 review cycle — issues addressed:
- CD-M-1 (adopt): added `list_memories` to always-loaded tools; updated tool count 14→15; updated token budget note.
- CD-m-1 (modify): added integration boundary check to TASK-1a done_when — verifies real `_build_filtered_toolset()` produces correct flags.
- CD-m-3 (adopt): added `_banner.py` to TASK-3 done_when verification loop.
- CD-m-6 / PO-m-5 (adopt): clarified BC-3 wording to describe filter behavior (check `resume_tool_names` then `always_load` separately), not set contents.
- PO-m-2 (modify): added token budget note for deferred-tool prompt (~300-500 tokens/turn, net win vs 3-4K schema savings).
- PO-m-3 (adopt): added explicit `add_tool_surface_hint` removal to TASK-2 — `build_deferred_tool_prompt` subsumes it.

Previous cycle issues (retained):
- CD-m-7: `AgentCapabilityResult` keeps `tool_index` (renamed from `tool_catalog`) for bootstrap path.
- CD-m-8: `_filter` pseudocode resume branch returns `False` for un-indexed MCP tools.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev tool-surface-simplify`
