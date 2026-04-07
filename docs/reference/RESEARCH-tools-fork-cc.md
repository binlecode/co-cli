# RESEARCH: fork-claude-code Tool Lifecycle vs co-cli

> Source-code-driven comparison only.
> This version removes the earlier lifecycle diagrams and avoids design judgments.
> It records only what is present in the two codebases and what the earlier draft got wrong.

## 1. Scope

Compared code:

- `co-cli`
- `~/workspace_genai/fork-claude-code/`

Main co-cli files checked:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)
- [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)
- [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
- [DESIGN-tools.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-tools.md)
- [DESIGN-core-loop.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-core-loop.md)

Main fork-cc files checked:

- `../fork-claude-code/Tool.ts`
- `../fork-claude-code/services/tools/toolHooks.ts`
- `../fork-claude-code/services/tools/toolExecution.ts`
- `../fork-claude-code/services/tools/toolOrchestration.ts`
- `../fork-claude-code/services/tools/StreamingToolExecutor.ts`
- `../fork-claude-code/utils/permissions/permissions.ts`
- `../fork-claude-code/types/permissions.ts`
- `../fork-claude-code/tools/BashTool/UI.tsx`

---

## 2. Verified Facts About co-cli

### 2.1 Tool registration and visibility

In `co-cli`, native tools are registered through `_reg()` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

The current native metadata type is `ToolInfo` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py).

Observed `ToolInfo` fields populated by `_reg()` include:

- `approval`
- `load` (type: `LoadPolicy` enum — `ALWAYS` or `DEFERRED`)
- `search_hint`
- `integration`
- `max_result_size`

The native filtered toolset uses:

- `session.discovered_tools`
- `runtime.resume_tool_names`
- `ToolInfo.load` with `LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`

Sources:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [DESIGN-tools.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-tools.md)

### 2.2 Approval handling

In `co-cli`, deferred approval handling is implemented in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py) with:

- `_collect_deferred_tool_approvals()`
- `_run_approval_loop()`

Approval subjects are resolved in [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py) into these categories:

- `shell`
- `path`
- `domain`
- `tool`

Remembered approval rules are session-scoped and stored in:

- `deps.session.session_approval_rules`

Sources:

- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [DESIGN-core-loop.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-core-loop.md)

### 2.3 Shell policy

`run_shell_command()` in [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py) uses `evaluate_shell_command()` from [\_shell_policy.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/_shell_policy.py).

The shell policy returns one of:

- `DENY`
- `ALLOW`
- `REQUIRE_APPROVAL`

### 2.4 Concurrency safety

`co-cli` contains `ResourceLockStore` in [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py).

`edit_file()` in [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py) acquires a resource lock keyed by resolved path.

The design docs also state that resource locks are used for some memory mutation tools.

Sources:

- [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)
- [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)
- [DESIGN-tools.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-tools.md)

### 2.5 Tool results

`tool_output(display, *, ctx, **metadata)` in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py):

- requires `ctx: RunContext[CoDeps]` as a keyword arg
- returns `ToolReturn`
- sends a string in `return_value`
- stores extra fields in `metadata`
- `tool_output_raw(...)` is the ctx-free variant for helper functions

Oversized results are persisted through `persist_if_oversized()` in [tool_result_storage.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_result_storage.py).

The current threshold is not global-only in the registry layer:

- `_reg(..., max_result_size=...)` stores a per-tool value
- `ToolInfo.max_result_size` carries that value
- `tool_output()` reads that tool-specific threshold

Sources:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

### 2.6 Sub-agent dependency inheritance

`make_subagent_deps()` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py):

- copies `session_approval_rules`
- shares `resource_locks`
- resets runtime state

The current code therefore does inherit remembered approval rules into sub-agents by copy.

### 2.7 Tool display

`co-cli` has centralized display helpers in [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py):

- `get_tool_start_args_display()`
- `format_for_display()`

The current formatting path:

- passes strings through directly
- summarizes dict payloads into compact key/value text

---

## 3. Verified Facts About fork-cc

### 3.1 Typed tool contract

`fork-cc` defines a typed `Tool<...>` contract in `../fork-claude-code/Tool.ts`.

Methods and properties visible in that contract include:

- `inputSchema`
- `inputJSONSchema`
- `isConcurrencySafe`
- `isReadOnly`
- `isDestructive`
- `interruptBehavior`
- `validateInput`
- `checkPermissions`
- `backfillObservableInput`
- `mapToolResultToToolResultBlockParam`
- `maxResultSizeChars`
- `aliases`

### 3.2 Hook surfaces

`fork-cc` contains these hook runners in `../fork-claude-code/services/tools/toolHooks.ts`:

- `runPreToolUseHooks`
- `runPostToolUseHooks`
- `runPostToolUseFailureHooks`

The pre-tool hook runner yields hook results including:

- permission results
- updated input
- prevent-continuation signals
- additional context

### 3.3 Permission system

`fork-cc` contains permission rule logic in `../fork-claude-code/utils/permissions/permissions.ts`.

`../fork-claude-code/types/permissions.ts` defines:

- permission behaviors: `allow`, `deny`, `ask`
- rule sources including `userSettings`, `projectSettings`, `localSettings`, `flagSettings`, `policySettings`, `cliArg`, `command`, `session`
- permission modes including `acceptEdits`, `bypassPermissions`, `default`, `dontAsk`, `plan`, and internal modes `auto`, `bubble`

### 3.4 Tool orchestration

`fork-cc` contains `partitionToolCalls()` in `../fork-claude-code/services/tools/toolOrchestration.ts`.

That code partitions tool calls into batches based on `tool.isConcurrencySafe(...)`.

### 3.5 Rendering

`fork-cc` contains per-tool UI code, for example:

- `../fork-claude-code/tools/BashTool/UI.tsx`

---

## 4. Verified Differences Present In fork-cc And Not Observed In co-cli

The items below were checked directly in the current `co-cli` source files listed above.

### 4.1 Tool-level lifecycle methods not present in co-cli registry metadata

Observed in `fork-cc` tool contract:

- `validateInput`
- `checkPermissions`
- `isConcurrencySafe`
- `isReadOnly`
- `isDestructive`
- `interruptBehavior`
- `backfillObservableInput`
- `mapToolResultToToolResultBlockParam`
- `aliases`

Not observed in current `co-cli` native registration metadata (`ToolInfo` plus `_reg()`):

- `_reg()` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- `ToolInfo` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)

### 4.2 Hook execution layer not observed in co-cli tool lifecycle

Observed in `fork-cc`:

- `runPreToolUseHooks`
- `runPostToolUseHooks`
- `runPostToolUseFailureHooks`

Not observed in checked `co-cli` tool lifecycle files:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

### 4.3 Multi-source persistent permission rule system not observed in co-cli

Observed in `fork-cc`:

- rule sources from multiple settings and runtime layers
- explicit permission modes in `types/permissions.ts`

Observed in `co-cli`:

- session-scoped remembered approval rules in `deps.session.session_approval_rules`
- shell inline policy with `DENY` / `ALLOW` / `REQUIRE_APPROVAL`

Not observed in checked `co-cli` files:

- config-backed allow rules
- config-backed deny rules
- config-backed ask rules
- named permission modes matching the `fork-cc` mode set

### 4.4 Dispatch partitioning by concurrency safety not observed in co-cli

Observed in `fork-cc`:

- `partitionToolCalls()` in `toolOrchestration.ts`

Observed in `co-cli`:

- per-resource locking through `ResourceLockStore`

Not observed in checked `co-cli` orchestration and registration files:

- dispatch-time batching by read-only vs mutating classes
- tool metadata field equivalent to `isConcurrencySafe`

### 4.5 Structured native result mapping surface not observed in co-cli

Observed in `fork-cc`:

- `mapToolResultToToolResultBlockParam`

Observed in `co-cli`:

- `tool_output(display: str, *, ctx: RunContext[CoDeps], **metadata)`
- model-facing native payload is string content

Not observed in checked `co-cli` native tool result path:

- native per-tool mapping API for non-string structured content blocks

### 4.6 Per-tool UI component layer not observed in co-cli

Observed in `fork-cc`:

- per-tool UI components under `tools/*/UI.tsx`

Observed in `co-cli`:

- centralized display helpers in [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- generic frontend callbacks in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)

---

## 5. Verified Facts Missing From The Earlier Draft

The earlier draft should not be used as-is because these points were inaccurate against the current `co-cli` source.

### 5.1 "Global 50KB only" was inaccurate

The current code has per-tool `max_result_size` in:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

### 5.2 "Subagents do not inherit approval rules" was inaccurate

The current code copies `session_approval_rules` in `make_subagent_deps()` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py).

### 5.3 "Binary approval only" was incomplete

The current code contains:

- session remembered approvals by semantic scope in [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- shell `DENY` / `ALLOW` / `REQUIRE_APPROVAL` in [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)

This does not match the full `fork-cc` permission system, but it is more than a bare `requires_approval` boolean in runtime behavior.

---

## 6. Minimal Fact Summary

Directly observed in `fork-cc` and not observed in checked `co-cli` lifecycle code:

1. typed tool-level lifecycle methods such as `validateInput`, `backfillObservableInput`, `isConcurrencySafe`, `interruptBehavior`
2. pre-tool and post-tool hook runners
3. multi-source permission rule types and named permission modes
4. dispatch-time concurrency partitioning based on tool metadata
5. native per-tool structured result mapping
6. per-tool UI component rendering

Directly observed in current `co-cli` and misstated in the earlier draft:

1. per-tool result-size thresholds exist
2. sub-agents inherit remembered approval rules by copy
3. approval handling includes semantic scope memory plus shell tri-state policy

---

## 7. Tool Surface Gap

This section is based on the native tool assembly in `../fork-claude-code/tools.ts` and the concrete built-in tool directories under `../fork-claude-code/tools/`. The `co-cli` side is based on native registrations in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

### 7.1 Tools provided by fork-cc and not observed in current co-cli native tools

Both systems provide native file, search, shell, web-fetch, web-search, and todo tools. The main native-tool gaps visible in the checked source are additional `fork-cc` tools for:

- explicit user interaction and mode control: `AskUserQuestionTool`, `EnterPlanModeTool`, `ExitPlanModeV2Tool`
- code-intelligence and notebook editing: `LSPTool`, `NotebookEditTool`
- alternate shell and workspace modes: `PowerShellTool`, `EnterWorktreeTool`, `ExitWorktreeTool`
- richer multi-agent and task orchestration: `AgentTool`, `TaskCreateTool`, `TaskGetTool`, `TaskListTool`, `TaskUpdateTool`, `TaskOutputTool`, `TaskStopTool`, `SendMessageTool`, `TeamCreateTool`, `TeamDeleteTool`
- MCP-native resource tooling: `ListMcpResourcesTool`, `ReadMcpResourceTool`, `MCPTool`, `McpAuthTool`
- native config, brief, and scheduling surfaces: `ConfigTool`, `BriefTool`, `CronCreateTool`, `CronDeleteTool`, `CronListTool`, `RemoteTriggerTool`

Those tool names were observed directly in `getAllBaseTools()` in `../fork-claude-code/tools.ts` or in adjacent built-in tool directories loaded by that file.

### 7.2 Tools provided by co-cli and not observed in the checked fork-cc native tool assembly

Observed in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) and not observed in `../fork-claude-code/tools.ts` base-tool assembly:

- knowledge and article tools: `search_knowledge`, `search_articles`, `read_article`, `save_article`
- persistent memory mutation tools: `list_memories`, `save_memory`, `update_memory`, `append_memory`
- background task controls: `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`
- built-in subagent entrypoints by role: `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent`
- native Obsidian integration: `list_notes`, `search_notes`, `read_note`
- native Google integration tools: `search_drive_files`, `read_drive_file`, `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft`, `list_calendar_events`, `search_calendar_events`

The checked `fork-cc` sources do contain memory-related subsystems elsewhere, but the native built-in tool assembly scanned for this section does not expose co-cli-style article, Google, Obsidian, or background-task tools as first-class tools.
