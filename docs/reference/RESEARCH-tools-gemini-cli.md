# RESEARCH: gemini-cli Tool Lifecycle vs co-cli

> Source-code-driven comparison only.
> This version mirrors the standalone peer lifecycle notes.
> It records only what is present in `~/workspace_genai/gemini-cli/`, what is present in current `co-cli`, and the direct differences observed in source.

## 1. Scope

Compared code:

- `co-cli`
- `~/workspace_genai/gemini-cli/`

Main gemini-cli files checked:

- `../gemini-cli/packages/core/src/tools/tools.ts`
- `../gemini-cli/packages/core/src/tools/tool-registry.ts`
- `../gemini-cli/packages/core/src/scheduler/tool-executor.ts`
- `../gemini-cli/packages/core/src/scheduler/confirmation.ts`
- `../gemini-cli/packages/core/src/services/toolDistillationService.ts`
- `../gemini-cli/packages/core/src/hooks/types.ts`
- `../gemini-cli/packages/core/src/hooks/hookEventHandler.ts`
- `../gemini-cli/packages/core/src/confirmation-bus/types.ts`
- `../gemini-cli/packages/core/src/policy/types.ts`
- `../gemini-cli/packages/core/src/core/turn.ts`

Main co-cli files checked:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py)
- [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
- [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)

---

## 2. Verified Facts About gemini-cli

### 2.1 Typed tool contract

`gemini-cli` defines its main tool contract in `../gemini-cli/packages/core/src/tools/tools.ts`.

Observed in `ToolInvocation<TParams, TResult>`:

- `params`
- `getDescription()`
- optional `getDisplayTitle()`
- optional `getExplanation()`
- `toolLocations()`
- `shouldConfirmExecute(...)`
- `execute(...)`
- optional `getPolicyUpdateOptions(...)`

Observed in `ToolBuilder<TParams, TResult>`:

- `name`
- `displayName`
- `description`
- `kind`
- `getSchema(...)`
- `schema`
- `isOutputMarkdown`
- `canUpdateOutput`
- `isReadOnly`
- `build(...)`

Observed class hierarchy:

- `DeclarativeTool`
- `BaseDeclarativeTool`
- `BaseToolInvocation`

Observed validation flow:

- `BaseDeclarativeTool.build(...)` validates parameters before creating the invocation
- `SchemaValidator.validate(...)` is used in `validateToolParams(...)`
- `validateBuildAndExecute(...)` converts validation and execution failures into `ToolResult`

### 2.2 Tool kinds and read-only classification

`gemini-cli` defines tool categories in `Kind` in `../gemini-cli/packages/core/src/tools/tools.ts`.

Observed kinds include:

- `Read`
- `Edit`
- `Delete`
- `Move`
- `Search`
- `Execute`
- `Think`
- `Agent`
- `Fetch`
- `Communicate`
- `Plan`
- `SwitchMode`
- `Other`

Observed classification helpers:

- `MUTATOR_KINDS`
- `READ_ONLY_KINDS`
- `DeclarativeTool.isReadOnly` returns true when the tool kind is in `READ_ONLY_KINDS`

### 2.3 Registry and schema exposure

`../gemini-cli/packages/core/src/tools/tool-registry.ts` contains the registry layer.

Observed registry behavior:

- `registerTool(...)`
- `unregisterTool(...)`
- `sortTools(...)`
- `discoverAllTools(...)`
- `discoverAndRegisterToolsFromCommand(...)`
- `getActiveTools()`
- `getFunctionDeclarations(...)`
- `getFunctionDeclarationsFiltered(...)`

Observed tool ordering:

- built-ins first
- discovered command tools second
- discovered MCP tools third

Observed filtering and schema behavior:

- `getActiveTools()` applies exclusion rules from config
- legacy aliases are expanded through `expandExcludeToolsWithAliases(...)`
- `getFunctionDeclarations(...)` emits `FunctionDeclaration[]`
- in plan mode, write/edit tool descriptions are rewritten to plan-only descriptions
- MCP tool schemas are renamed to their fully qualified names before exposure

### 2.4 Confirmation and policy flow

`gemini-cli` routes tool confirmation through invocation methods, a message bus, and a scheduler confirmation loop.

Observed in `BaseToolInvocation.shouldConfirmExecute(...)`:

- auto-edit mode can bypass confirmation for tools that respect auto-edit
- policy decisions resolve to `allow`, `deny`, or `ask_user`
- `ask_user` returns tool-specific confirmation details

Observed in `../gemini-cli/packages/core/src/confirmation-bus/types.ts`:

- `TOOL_CONFIRMATION_REQUEST`
- `TOOL_CONFIRMATION_RESPONSE`
- `TOOL_POLICY_REJECTION`
- `UPDATE_POLICY`
- `ASK_USER_REQUEST`
- `ASK_USER_RESPONSE`

Observed in `../gemini-cli/packages/core/src/scheduler/confirmation.ts`:

- `resolveConfirmation(...)` runs the interactive confirmation loop
- `awaitConfirmation(...)` waits for a matching confirmation response by `correlationId`
- `ModifyWithEditor` can loop back through edited parameters

Observed confirmation detail shapes:

- `sandbox_expansion`
- `info`
- `edit`
- `exec`
- `mcp`
- `ask_user`
- `exit_plan_mode`

Observed policy types in `../gemini-cli/packages/core/src/policy/types.ts`:

- `PolicyDecision`: `ALLOW`, `DENY`, `ASK_USER`
- `ApprovalMode`: `DEFAULT`, `AUTO_EDIT`, `YOLO`, `PLAN`
- `PolicyRule` supports `toolName`, optional `mcpName`, optional `argsPattern`, optional `toolAnnotations`, `decision`, `priority`, `modes`, and optional `denyMessage`

### 2.5 Hook system

`gemini-cli` contains a dedicated hook system.

Observed hook config sources in `../gemini-cli/packages/core/src/hooks/types.ts`:

- `Runtime`
- `Project`
- `User`
- `System`
- `Extensions`

Observed hook events:

- `BeforeTool`
- `AfterTool`
- `BeforeToolSelection`
- `BeforeModel`
- `AfterModel`
- `BeforeAgent`
- `AfterAgent`
- `SessionStart`
- `SessionEnd`
- `PreCompress`
- `Notification`

Observed in `../gemini-cli/packages/core/src/hooks/hookEventHandler.ts`:

- `fireBeforeToolEvent(...)`
- `fireAfterToolEvent(...)`
- `fireBeforeToolSelectionEvent(...)`
- hooks execute through an execution plan and aggregation path

Observed hook outputs:

- `BeforeToolHookOutput.getModifiedToolInput()`
- `DefaultHookOutput.getAdditionalContext()`
- `DefaultHookOutput.getTailToolCallRequest()`
- `BeforeToolSelectionHookOutput.applyToolConfigModifications(...)`

### 2.6 Tool results and output distillation

`gemini-cli` defines tool results in `../gemini-cli/packages/core/src/tools/tools.ts`.

Observed in `ToolResult`:

- `llmContent`
- `returnDisplay`
- optional `error`
- optional `data`
- optional `tailToolCallRequest`

Observed in `ToolResultDisplay`:

- plain strings
- `FileDiff`
- `AnsiOutput`
- `TodoList`
- structured search/list results
- `SubagentProgress`

Observed in `../gemini-cli/packages/core/src/scheduler/tool-executor.ts`:

- `ToolExecutor.execute(...)` executes a tool call
- `truncateOutputIfNeeded(...)` truncates oversized output before replay
- shell string output and single-part MCP text output are persisted via `saveTruncatedToolOutput(...)`

Observed in `../gemini-cli/packages/core/src/services/toolDistillationService.ts`:

- `distill(...)` is driven by a global token threshold
- `read_file` and `read_many_files` are exempt from distillation
- oversized output is saved to disk
- `truncateContentStructurally(...)` preserves `PartListUnion` structure
- `generateIntentSummary(...)` may run a secondary summarizer model with a 15s timeout

---

## 3. Verified Facts About co-cli Relevant To The Comparison

### 3.1 Tool registration and visibility

In `co-cli`, native tools are registered through `_reg()` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

The current native metadata type is `ToolInfo` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py).

Observed `ToolInfo` fields populated by `_reg()`:

- `approval`
- `load` (type: `LoadPolicy` enum — `ALWAYS` or `DEFERRED`)
- `search_hint`
- `integration`
- `max_result_size`

Observed visibility controls:

- `session.discovered_tools`
- `runtime.resume_tool_names`
- `ToolInfo.load` with `LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`

### 3.2 Approval handling

Observed in `co-cli`:

- deferred approval collection in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- approval subject resolution in [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- remembered session approvals in `deps.session.session_approval_rules`

Observed approval subject categories:

- `shell`
- `path`
- `domain`
- `tool`

Observed shell-specific policy in [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py):

- `DENY`
- `ALLOW`
- `REQUIRE_APPROVAL`

### 3.3 Tool results and display

Observed in `co-cli`:

- native tool results are produced through `tool_output(...)` in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- `tool_output(...)` returns `ToolReturn`
- `return_value` carries the native model-visible string
- `metadata` carries extra app-side fields
- `tool_output(...)` requires `ctx: RunContext[CoDeps]` as a keyword arg and reads the current tool's `max_result_size` from `ToolInfo` to persist oversized results
- `tool_output_raw(...)` is the ctx-free variant for helper functions that lack a RunContext

Observed display handling in [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py):

- string results are passed through directly
- dict payloads are summarized into compact key/value text

### 3.4 Concurrency safety

`co-cli` contains a per-resource lock store in [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py).

Observed in `ResourceLockStore`:

- locks are keyed by resource identifier
- `try_acquire(...)` is non-blocking and raises `ResourceBusyError` if the key is already held

Observed in [files.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/files.py):

- `edit_file(...)` acquires a lock keyed by the resolved file path before read-modify-write

---

## 4. Verified Differences Present In gemini-cli And Not Observed In co-cli

The items below were checked directly in the current source files listed above.

### 4.1 Explicit tool-builder and invocation abstraction

Observed in `gemini-cli`:

- `ToolBuilder`
- `ToolInvocation`
- `DeclarativeTool`
- `BaseDeclarativeTool`
- `BaseToolInvocation`
- `Kind`
- `isReadOnly`

Not observed in checked `co-cli` native registration and execution files:

- native registration in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) stores `ToolInfo` metadata and hands execution to pydantic-ai tool functions directly
- the checked `co-cli` files do not define a native builder/invocation abstraction matching `ToolBuilder` plus `ToolInvocation`

### 4.2 Scheduler-managed confirmation loop over a message bus

Observed in `gemini-cli`:

- message-bus request/response types for tool confirmation
- `resolveConfirmation(...)`
- `awaitConfirmation(...)`
- `ModifyWithEditor` loop support
- multiple serializable confirmation detail variants

Not observed in checked `co-cli` approval path:

- `co-cli` uses deferred approvals in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- the checked `co-cli` files do not define a message-bus confirmation service matching Gemini's request/response loop

### 4.3 Configurable hook system across tool selection and tool execution

Observed in `gemini-cli`:

- hook config sources from runtime, project, user, system, and extensions
- `BeforeTool`
- `AfterTool`
- `BeforeToolSelection`
- hook outputs that can modify tool input, inject additional context, request tail tool calls, or modify tool config

Not observed in checked `co-cli` tool lifecycle files:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

### 4.4 Dual result surface for model content and user display

Observed in `gemini-cli`:

- `ToolResult.llmContent`
- `ToolResult.returnDisplay`
- optional `ToolResult.tailToolCallRequest`

Not observed in checked `co-cli` native tool result path:

- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py) returns `ToolReturn(return_value, metadata)`
- the checked `co-cli` files do not define a native result type with separate `llmContent` and `returnDisplay` fields

### 4.5 Scheduler-level output distillation with optional summarizer call

Observed in `gemini-cli`:

- `ToolExecutor.truncateOutputIfNeeded(...)`
- `ToolOutputDistillationService.distill(...)`
- `truncateContentStructurally(...)`
- `generateIntentSummary(...)`
- disk offload through `saveTruncatedToolOutput(...)`

Not observed in checked `co-cli` tool result path:

- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py) persists oversized output
- the checked `co-cli` files do not define a scheduler-level distillation service with secondary summarizer calls

### 4.6 Config-driven exclusion and discovered-tool registration from command output

Observed in `gemini-cli`:

- `getActiveTools()` filters via config exclusion rules
- legacy aliases are expanded for exclusions
- `discoverAndRegisterToolsFromCommand(...)` spawns a discovery command and registers returned `FunctionDeclaration` entries as tools

Not observed in checked `co-cli` files:

- native tool visibility in `co-cli` is driven by `ToolInfo.load` (`LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`), `session.discovered_tools`, and `runtime.resume_tool_names`
- the checked `co-cli` files do not define a command-output tool registration path matching Gemini's discovery command

---

## 5. Verified Differences Present In co-cli And Not Observed In gemini-cli

### 5.1 Semantic approval subjects with session-remembered scope keys

Observed in `co-cli`:

- `ApprovalSubject`
- subject kinds `shell`, `path`, `domain`, and `tool`
- remembered approvals stored in `session_approval_rules`

Not observed in the checked `gemini-cli` lifecycle files:

- no approval-subject value type matching `ApprovalSubject`

### 5.2 Deferred native tool exposure via session discovery and approval-resume narrowing

Observed in `co-cli`:

- `ToolInfo.load` with `LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`
- `session.discovered_tools`
- `runtime.resume_tool_names`

Not observed in the checked `gemini-cli` lifecycle files:

- no deferred native-tool visibility model matching `session.discovered_tools` plus `resume_tool_names`

### 5.3 Per-resource mutation locks in the native file-edit path

Observed in `co-cli`:

- `ResourceLockStore`
- path-keyed locking in `edit_file(...)`

Not observed in the checked `gemini-cli` lifecycle files:

- no per-resource lock store equivalent to [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)

### 5.4 Per-tool result-size threshold in native registration metadata

Observed in `co-cli`:

- `_reg(..., max_result_size=...)` in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- `ToolInfo.max_result_size` in [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- `tool_output(...)` reading that threshold in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

Not observed in the checked `gemini-cli` files:

- no native registry metadata field matching `max_result_size`

---

## 6. Minimal Fact Summary

Directly observed in `gemini-cli` and not observed in the checked `co-cli` lifecycle files:

1. explicit tool-builder and invocation abstractions with kind-based read-only metadata
2. a scheduler-managed message-bus confirmation loop
3. a configurable hook system spanning tool selection and tool execution
4. a dual result surface with separate `llmContent` and `returnDisplay`
5. scheduler-level output distillation with optional summarizer calls
6. command-based discovered-tool registration and config-driven exclusion filtering

Directly observed in `co-cli` and not observed in the checked `gemini-cli` lifecycle files:

1. semantic approval subjects with session-remembered scope keys
2. deferred native-tool exposure via session discovery and approval-resume narrowing
3. per-resource mutation locks in the native file-edit path
4. per-tool result-size thresholds stored in native registration metadata

---

## 7. Tool Surface Gap

This section is based on the built-in tool names and declarations in `../gemini-cli/packages/core/src/tools/tool-names.ts`, `../gemini-cli/packages/core/src/tools/definitions/coreTools.ts`, and the registry behavior in `../gemini-cli/packages/core/src/tools/tool-registry.ts`. The `co-cli` side is based on native registrations in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

### 7.1 Tools provided by gemini-cli and not observed in current co-cli native tools

Both systems provide core file read/write/edit, directory listing, shell, grep-style search, web-fetch, web-search, and todo tools. The additional native `gemini-cli` tools directly observed in the checked source and not observed as native `co-cli` registrations are:

- multi-file read and alternate grep surfaces: `read_many_files`, `ripGrep`
- explicit user and mode tools: `ask_user`, `enter_plan_mode`, `exit_plan_mode`
- native skill and internal-doc tools: `activate_skill`, `get_internal_docs`
- topic and tracker tools: `update_topic`, `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`, `tracker_add_dependency`, `tracker_visualize`
- MCP client and discovered-tool management surfaces under `mcp-client.ts`, `mcp-client-manager.ts`, and `mcp-tool.ts`

The checked registry also supports command-based tool discovery and MCP registration in a way that is broader than the fixed native `co-cli` tool list.

### 7.2 Tools provided by co-cli and not observed in the checked gemini-cli built-in surface

Observed in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) and not observed in the checked `gemini-cli` built-in tool names and declarations:

- knowledge and article tools: `search_knowledge`, `search_articles`, `read_article`, `save_article`
- broader persistent memory tools: `list_memories`, `update_memory`, `append_memory`
- background task controls: `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`
- role-specific subagent entrypoints: `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent`
- native Obsidian integration: `list_notes`, `search_notes`, `read_note`
- native Google integration tools: `search_drive_files`, `read_drive_file`, `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft`, `list_calendar_events`, `search_calendar_events`

`gemini-cli` has its own `memoryTool`, but the checked built-in surface does not expose co-cli-style article tools, Google tools, Obsidian tools, or role-specific subagent entrypoints.
