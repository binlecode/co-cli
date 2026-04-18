# RESEARCH: opencode Tool Lifecycle vs co-cli

> Source-code-driven comparison only.
> This version removes the earlier diagrams and avoids design judgments.
> It records only what is present in `~/workspace_genai/opencode/`, what is present in current `co-cli`, and the direct differences observed in source.

## 1. Scope

Compared code:

- `co-cli`
- `~/workspace_genai/opencode/`

Main opencode files checked:

- `../opencode/packages/opencode/src/tool/tool.ts`
- `../opencode/packages/opencode/src/tool/registry.ts`
- `../opencode/packages/opencode/src/session/prompt.ts`
- `../opencode/packages/opencode/src/session/llm.ts`
- `../opencode/packages/opencode/src/session/processor.ts`
- `../opencode/packages/opencode/src/session/message-v2.ts`
- `../opencode/packages/opencode/src/permission/index.ts`

Main co-cli files checked:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [deps.py](/Users/binle/workspace_genai/co-cli/co_cli/deps.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)
- [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)
- [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)
- [DESIGN-tools.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-tools.md)
- [DESIGN-core-loop.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-core-loop.md)

---

## 2. Verified Facts About opencode

### 2.1 Common tool contract

`opencode` defines a common tool namespace in `../opencode/packages/opencode/src/tool/tool.ts`.

Observed in `Tool.Context`:

- `sessionID`
- `messageID`
- `agent`
- `abort`
- `callID`
- `messages`
- `metadata(...)`
- `ask(...)`

Observed in `Tool.Def`:

- `description`
- `parameters`
- `execute(...)`
- optional `formatValidationError(...)`

Observed in `Tool.define(...)`:

- runtime Zod validation through `toolInfo.parameters.parse(args)`
- common output truncation through `Truncate.output(...)` unless `metadata.truncated` is already set

### 2.2 Tool registry

`../opencode/packages/opencode/src/tool/registry.ts` assembles tools from:

- built-ins
- repo-local custom tools loaded from `tool/*.ts` or `tools/*.ts`
- plugin-provided tools

Observed registry behavior:

- model-aware filtering for `codesearch` and `websearch`
- model-aware choice between `apply_patch` and `edit` / `write`
- plugin `tool.definition` hook after tool initialization
- tool initialization performed per call to `ToolRegistry.tools(...)`

### 2.3 Session prompt tool wrapping

`resolveTools` in `../opencode/packages/opencode/src/session/prompt.ts` wraps registry tools into AI SDK tool objects.

Observed in the native-tool wrapper:

- builds `Tool.Context`
- `ctx.metadata(...)` updates the currently running transcript tool part
- `ctx.ask(...)` forwards to `permission.ask(...)`
- `plugin.trigger("tool.execute.before", ...)`
- `plugin.trigger("tool.execute.after", ...)`

Observed in the MCP wrapper:

- schema conversion to provider-compatible JSON Schema
- generic permission request through `ctx.ask(...)`
- flattening of heterogeneous MCP `content` into `{ title, metadata, output, attachments }`
- truncation through `truncate.output(...)`

### 2.4 LLM request assembly

`../opencode/packages/opencode/src/session/llm.ts` contains `LLM.stream(...)`.

Observed behaviors:

- merges model, agent, variant, and provider options
- plugin hooks for system text, params, and headers
- resolves active tools through a permission-based filter
- injects `_noop` for LiteLLM-style compatibility when history contains tool calls but the active tool set is empty
- implements `experimental_repairToolCall(...)` to lowercase tool names or reroute to `invalid`
- passes tools directly to `streamText(...)`

### 2.5 Transcript persistence of tool state

`../opencode/packages/opencode/src/session/processor.ts` contains `SessionProcessor.handleEvent(...)`.

Observed tool-part state flow:

- `tool-input-start` creates a tool part with `status: "pending"`
- `tool-call` updates that part to `status: "running"`
- `tool-result` updates that part to `status: "completed"`
- `tool-error` updates that part to `status: "error"`

Observed additional behavior:

- doom-loop permission check after repeated identical recent tool calls
- transcript cleanup converts unfinished tool parts to `status: "error"` with `"Tool execution aborted"`

### 2.6 Replay of tool results into next model call

`../opencode/packages/opencode/src/session/message-v2.ts` contains `toModelMessages(...)`.

Observed replay behavior:

- completed tool parts become output-available tool results
- errored tool parts become output-error tool results
- pending/running tool parts are converted into interrupted tool-result errors for provider compatibility
- media attachments from tool results may be extracted into synthetic user messages for providers that do not support media in tool results

### 2.7 Permission system

`../opencode/packages/opencode/src/permission/index.ts` contains `Permission.ask(...)` and `Permission.reply(...)`.

Observed behavior:

- each requested pattern is evaluated against the ruleset and approved rules
- `deny` throws immediately
- `allow` returns immediately
- otherwise a pending request is created and awaited
- `reply === "always"` persists allow rules into the approved list and may resolve other pending requests in the same session

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

- deferred approval handling in [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- approval subject resolution in [tool_approvals.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_approvals.py)
- session-scoped remembered approvals in `deps.session.session_approval_rules`
- shell-specific `DENY` / `ALLOW` / `REQUIRE_APPROVAL` in [shell.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/shell.py)

### 3.3 Tool results and display

Observed in `co-cli`:

- native tool results produced through `tool_output(display: str, *, ctx: RunContext[CoDeps], **metadata)` in [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py); `tool_output_raw(...)` is the ctx-free variant for helpers
- centralized display formatting in [tool_display.py](/Users/binle/workspace_genai/co-cli/co_cli/context/tool_display.py)
- per-resource lock store in [resource_lock.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/resource_lock.py)

### 3.4 History processors

Observed in `co-cli` docs and source:

- `truncate_tool_results`
- `compact_assistant_responses`
- `detect_safety_issues`
- `inject_opening_context`
- `summarize_history_window`

Sources:

- [DESIGN-core-loop.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-core-loop.md)
- [DESIGN-tools.md](/Users/binle/workspace_genai/co-cli/docs/DESIGN-tools.md)

---

## 4. Verified Differences Present In opencode And Not Observed In co-cli

The items below were checked directly in the current source files listed above.

### 4.1 Shared tool execution context with `ask()` and `metadata()`

Observed in `opencode`:

- every tool executes with a common `Tool.Context`
- that context includes both `ask(...)` and `metadata(...)`

Not observed in checked `co-cli` native tool contract:

- `co-cli` native tools receive `RunContext[CoDeps]`
- approval and progress/display callbacks are accessed through different runtime paths rather than a common tool-specific context interface matching `opencode`'s `Tool.Context`

### 4.2 Common tool wrapper for validation and truncation

Observed in `opencode`:

- `Tool.define(...)` wraps every tool with Zod validation
- `Tool.define(...)` applies common truncation unless the tool marks itself truncated

Not observed as a shared native wrapper in checked `co-cli` files:

- `co-cli` has `tool_output(...)` for result construction and persistence
- semantic validation remains tool-specific

### 4.3 Plugin hooks around tool definition and execution

Observed in `opencode`:

- `tool.definition`
- `tool.execute.before`
- `tool.execute.after`

Not observed in checked `co-cli` tool registration and execution files:

- [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py)
- [orchestrate.py](/Users/binle/workspace_genai/co-cli/co_cli/context/orchestrate.py)
- [tool_output.py](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_output.py)

### 4.4 Transcript-persisted tool state machine

Observed in `opencode`:

- tool lifecycle is persisted as transcript parts in `SessionProcessor.handleEvent(...)`
- replay back into model history happens in `MessageV2.toModelMessages(...)`

Not observed in checked `co-cli` source:

- `co-cli` tool execution is surfaced through stream events and message history processors
- the checked `co-cli` files do not define a transcript-part state machine equivalent to opencode's `pending` / `running` / `completed` / `error` tool parts

### 4.5 Permission queue with pending-request reply handling

Observed in `opencode`:

- `Permission.ask(...)` creates pending requests
- `Permission.reply(...)` resolves or rejects them
- `"always"` persists allow rules in approved state and may auto-resolve related pending requests

Not observed in checked `co-cli` approval path:

- `co-cli` uses `DeferredToolRequests` / `DeferredToolResults`
- the checked `co-cli` files do not define a separate pending permission queue service matching `Permission.ask(...)` / `Permission.reply(...)`

### 4.6 Provider-compatibility replay shims for tool-result media

Observed in `opencode`:

- `MessageV2.toModelMessages(...)` may extract tool-result media into synthetic user messages depending on provider support

Not observed in checked `co-cli` native tool replay path:

- `co-cli` native results are string content plus metadata in `ToolReturn`

---

## 5. Verified Differences Present In co-cli And Not Observed In opencode

### 5.1 Filtered native tool exposure through discovery and resume state

Observed in `co-cli`:

- native tool visibility is filtered by `ToolInfo.load` (`LoadPolicy.ALWAYS` / `LoadPolicy.DEFERRED`), `session.discovered_tools`, and `runtime.resume_tool_names`

Not observed in the checked `opencode` tool-registry and session-prompt files:

- no equivalent deferred native-tool discovery mechanism based on per-session discovered tool names

### 5.2 Session approval scopes by semantic subject type

Observed in `co-cli`:

- approval scopes by `shell`, `path`, `domain`, and `tool`

Observed in checked `opencode` permission code:

- rules are evaluated by permission name and pattern

The checked `opencode` files do not define an approval-subject abstraction matching `co-cli`'s `ApprovalSubject`.

### 5.3 Per-resource mutation locks

Observed in `co-cli`:

- `ResourceLockStore`
- file mutation lock in `edit_file()`

Not observed in the checked `opencode` files listed in this note:

- no equivalent per-resource lock store in the reviewed lifecycle files

---

## 6. Minimal Fact Summary

Directly observed in `opencode` and not observed in the checked `co-cli` lifecycle files:

1. common `Tool.Context` with `ask()` and `metadata()`
2. shared tool wrapper that applies validation and truncation
3. plugin hooks at tool-definition and tool-execution phases
4. transcript-persisted tool-part lifecycle with replay into next model call
5. pending permission queue service with explicit reply handling
6. provider-specific replay logic for tool-result media

Directly observed in `co-cli` and not observed in the checked `opencode` lifecycle files:

1. filtered native tool exposure via discovery and approval-resume narrowing
2. semantic approval subjects (`shell`, `path`, `domain`, `tool`)
3. per-resource mutation locks in the reviewed tool path

---

## 7. Tool Surface Gap

This section is based on the native built-in registry in `../opencode/packages/opencode/src/tool/registry.ts`. The `co-cli` side is based on native registrations in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py).

### 7.1 Tools provided by opencode and not observed in current co-cli native tools

Both systems provide core file-read/edit/write, grep/glob-style search, shell, web-fetch, web-search, and todo tools. The additional native `opencode` tools directly observed in `ToolRegistry.all(...)` and not observed as native `co-cli` registrations are:

- explicit user-question flow: `QuestionTool`
- patch-application tool choice: `ApplyPatchTool`
- batch orchestration: `BatchTool`
- task orchestration: `TaskTool`
- code-search tool separate from text grep: `CodeSearchTool`
- native skill tool: `SkillTool`
- optional plan-mode exit tool: `PlanExitTool`
- optional language-server tool: `LspTool`

The same registry file also shows a native path for repo-local custom tools and plugin-provided tools, which is broader than the current built-in `co-cli` native registry surface.

### 7.2 Tools provided by co-cli and not observed in the checked opencode built-in registry

Observed in [agent.py](/Users/binle/workspace_genai/co-cli/co_cli/agent.py) and not observed in `../opencode/packages/opencode/src/tool/registry.ts` built-ins:

- knowledge and article tools: `search_knowledge`, `search_articles`, `read_article`, `save_article`
- broader persistent memory tools: `list_memories`, `update_memory`, `append_memory`
- background task controls: `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks`
- role-specific subagent entrypoints: `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent`
- native Obsidian integration: `list_notes`, `search_notes`, `read_note`
- native Google integration tools: `search_drive_files`, `read_drive_file`, `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft`, `list_calendar_events`, `search_calendar_events`

`opencode` can load custom and plugin tools dynamically, but those are outside the fixed native built-in list compared in this section.


## 7.3 Tool Surface Gap

This section details the built-in native tools observed in opencode based on `../opencode/packages/opencode/src/tool/registry.ts`.

### Built-in Native opencode Tools

The following tools are initialized and provided as built-ins in the `ToolRegistry` state:

*   **`invalid` (`InvalidTool`)**: Handles invalid tool calls or unrecognized commands.
*   **`bash` (`BashTool`)**: Executes shell commands.
*   **`read` (`ReadTool`)**: Reads file contents.
*   **`glob` (`GlobTool`)**: Performs glob-based file searches.
*   **`grep` (`GrepTool`)**: Performs content searches using regular expressions (ripgrep).
*   **`edit` (`EditTool`)**: Modifies file contents (string replacement). Note: filtered out in favor of `patch` for certain older GPT models.
*   **`write` (`WriteTool`)**: Writes content to a file. Note: filtered out in favor of `patch` for certain older GPT models.
*   **`task` (`TaskTool`)**: Orchestrates subagents for complex tasks. Description is dynamically injected with available subagents.
*   **`fetch` (`WebFetchTool`)**: Fetches content from URLs.
*   **`todo` (`TodoWriteTool`)**: Manages the structured task/todo list.
*   **`search` (`WebSearchTool`)**: Performs web searches (conditional on provider or `OPENCODE_ENABLE_EXA`).
*   **`code` (`CodeSearchTool`)**: Performs code-specific searches (conditional on provider or `OPENCODE_ENABLE_EXA`).
*   **`skill` (`SkillTool`)**: Loads specialized domain instructions. Description is dynamically injected with available skills.
*   **`patch` (`ApplyPatchTool`)**: Applies unified diff patches. Note: only active for certain older GPT models in place of `edit`/`write`.
*   **`question` (`QuestionTool`)**: Asks the user explicit questions (conditional on `OPENCODE_CLIENT` or `OPENCODE_ENABLE_QUESTION_TOOL`).
*   **`lsp` (`LspTool`)**: Language Server Protocol integration (conditional on `OPENCODE_EXPERIMENTAL_LSP_TOOL`).
*   **`plan` (`PlanExitTool`)**: Exits plan mode (conditional on `OPENCODE_EXPERIMENTAL_PLAN_MODE` and CLI client).

### Comparison with co-cli Native Tools

*   **Common Tools:** Both systems implement `bash`, `read`, `glob`, `grep`, `edit`, `write`, `task` (subagents), `todo` (`todowrite`), `fetch` (`webfetch`), `question`, and `skill`.
*   **opencode Only (Built-in):** `invalid`, `search` (web search), `code` (code search), `patch` (diff application), `lsp` (language server), `plan` (plan mode exit).
*   **co-cli Only:** Knowledge/article tools, memory tools, background task controls, explicit role subagents (`run_coding_subagent`, etc.), Obsidian integration, and Google integration (Drive, Gmail, Calendar).
