# RESEARCH: agentic tool loading and tool search implementation inventory
_Date: 2026-04-01_

This document is a code-grounded inventory of tool-loading, tool-exposure, tool-search, skill-loading, and related execution paths in the local reference repositories under `~/workspace_genai/`.

Scope rules for this pass:

- only inspected code paths are described
- no design recommendations are included
- where the local checkout does not contain runtime code, that limitation is stated explicitly

Inspected systems for this pass:

- `codex`
- `gemini-cli`
- `opencode`
- `fork-claude-code`
- `openclaw`

---

# 0. Impl matrix

This matrix summarizes only the systems listed above for this pass and focuses on tool-loading, tool-search, and tool-dispatch behavior.

| System | Registry / loading path | Model-visible tool surface | Ranked tool search in inspected code | MCP tool path in inspected code | Execution / dispatch in inspected code | Evidence sections |
|---|---|---|---|---|---|---|
| Codex | `built_tools(...)` assembles `ToolRouter` and `ToolRegistry`; regular MCP tools are separated from app/connector tools during spec assembly | `model_visible_specs()` is further narrowed by `build_prompt(...)`, which removes deferred dynamic tools from the prompt-visible set | `create_tool_search_tool(...)` defines `tool_search`, and its description text states that it searches app/connector tool metadata with BM25; `tool_suggest` is exposed separately | MCP tools are normalized into function-tool specs and registered behind `mcp_handler`; when `tool_search` is enabled, app/connector tools are represented through the search-tool path instead of direct model-visible tool specs | `ToolPayload` routes calls to dynamic-tool, MCP, or search handlers | `2.1.1` to `2.1.5` |
| Gemini CLI | `ToolRegistry` stores known tools; additional tools are discovered from an external command and from MCP servers via the client manager | `getActiveTools()` filters the registry contents before function declarations are built for the model | No function was found in the inspected runtime files that takes a free-text query and returns ranked tool matches | MCP startup, `listTools()`, naming, filtering, and confirmation are implemented in the MCP client and manager path | discovered tools execute through the configured subprocess command; MCP tools execute through the MCP client callable wrapper | `2.2.1` to `2.2.6` |
| OpenCode | registry is assembled from built-ins, local custom tool modules, and plugin-provided tools | `tools(model, agent?)` filters tool infos by provider and model conditions, then initializes the selected set | No function was found in the inspected runtime files that takes a free-text query and returns ranked tool matches | MCP tools are wrapped with `dynamicTool(...)`; the same path also exposes MCP prompts and resources | selected tools are materialized via `tool.init(...)`; MCP calls dispatch through `client.callTool(...)` | `2.3.1` to `2.3.5` |
| fork-claude-code | `getAllBaseTools()`, `getTools()`, and `assembleToolPool(...)` assemble built-in and MCP-backed tools; `getMcpToolsCommandsAndResources(...)` materializes MCP tools from connected servers | built-in tools are filtered by deny rules and `isEnabled()`; MCP tools are merged through `assembleToolPool(...)` | Yes. `ToolSearchTool` scores deferred tools using tool-name parts, `searchHint`, and prompt text | MCP `tools/list` results are converted into `Tool` objects with `mcpInfo`, optional `searchHint`, and optional resource helper tools | built-in tools are defined with `buildTool(...)`; MCP tools reuse `MCPTool` and are looked up by name in the assembled tool pool | `2.4.1` to `2.4.4` |
| OpenClaw | plugin discovery, manifest loading, registry construction, loader orchestration, and active-registry pinning define the tool-loading path | registry tools are materialized only after enablement, allowlist, and name-conflict checks in `plugins/tools.ts` | No function was found in the inspected tool-loading files that takes a free-text query and returns ranked tool matches | `channel-server.ts` constructs an MCP server and `registerChannelMcpTools(...)` registers concrete conversation and permission tools on it | plugin tool factories materialize callable tools; channel MCP tools are dispatched through the channel MCP server path | `2.5.1` to `2.5.8` |

---

# 1. Converged practice analysis

The inspected code shows these recurring implementation facts across the listed systems:

- every inspected system has an explicit tool-loading path rather than relying on prompt text alone
- four inspected systems load tools from more than one source:
  - `codex` loads regular MCP tools, app/connector tools, and dynamic tools
  - `gemini-cli` loads built-in registry tools, externally discovered tools, and MCP tools
  - `opencode` loads built-ins, local custom modules, and plugin tools
  - `fork-claude-code` assembles built-in tools and MCP tools
  - `openclaw` loads plugin-registered tools and separately registers channel MCP tools
- every inspected system applies a second-stage narrowing step before or during model-visible tool exposure:
  - `codex` narrows with `model_visible_specs()` plus deferred-tool filtering in `build_prompt(...)`
  - `gemini-cli` narrows with `getActiveTools()`
  - `opencode` narrows in `tools(model, agent?)`
  - `fork-claude-code` narrows built-in tools with deny rules and `isEnabled()`, then merges MCP tools through `assembleToolPool(...)`
  - `openclaw` narrows in `resolvePluginTools(...)` through plugin enablement, allowlist, and name-conflict checks
- two inspected systems contain free-text ranked tool-search code in the inspected files:
  - `codex` defines `tool_search`
  - `fork-claude-code` defines `ToolSearchTool`
- the other three inspected systems contain tool-loading and MCP code in the inspected files, but no inspected function was found that takes a free-text query and returns ranked tool matches:
  - `gemini-cli`
  - `opencode`
  - `openclaw`
- every inspected system contains an adapter or materialization step between external/discovered tool metadata and runtime-callable tool objects:
  - `codex` converts MCP and dynamic tools into tool specs and handlers
  - `gemini-cli` wraps discovered tools and MCP tools in runtime tool classes
  - `opencode` initializes `Tool.Info` records into executable tool definitions
  - `fork-claude-code` converts MCP `tools/list` output into local `Tool` objects
  - `openclaw` materializes registered plugin tool factories into runtime tools
- execution dispatch is explicit in every inspected system, but the dispatch object differs by codebase:
  - `codex` dispatches through `ToolPayload`
  - `gemini-cli` dispatches through tool invocation classes and MCP client calls
  - `opencode` dispatches through initialized tool definitions and `client.callTool(...)`
  - `fork-claude-code` dispatches through assembled `Tool` objects resolved by name
  - `openclaw` dispatches through plugin tool factories and `server.tool(...)` registrations on the channel MCP server

---

# 2. Peer systems

## 2.1 Codex

Inspected files:

- `~/workspace_genai/codex/codex-rs/core/src/codex.rs`
- `~/workspace_genai/codex/codex-rs/core/src/tools/registry.rs`
- `~/workspace_genai/codex/codex-rs/core/src/tools/router.rs`
- `~/workspace_genai/codex/codex-rs/core/src/tools/spec.rs`
- `~/workspace_genai/codex/codex-rs/core/src/tools/handlers/dynamic.rs`
- `~/workspace_genai/codex/codex-rs/core/src/tools/handlers/mcp.rs`
- `~/workspace_genai/codex/codex-rs/protocol/src/dynamic_tools.rs`
- `~/workspace_genai/codex/codex-rs/tools/src/tool_discovery.rs`
- `~/workspace_genai/codex/codex-rs/tools/src/dynamic_tool.rs`
- `~/workspace_genai/codex/codex-rs/tools/src/mcp_tool.rs`
- `~/workspace_genai/codex/codex-rs/tools/src/responses_api.rs`
- `~/workspace_genai/codex/codex-rs/core/tests/suite/search_tool.rs`
- `~/workspace_genai/codex/codex-rs/core/tests/suite/tool_suggest.rs`

### 2.1.1 Registry and assembly

`core/src/codex.rs` assembles the tool surface in `built_tools(...)`.

- it calls `mcp_connection_manager.list_all_tools()`
- it partitions MCP tools into regular MCP tools and app/connector tools
- it creates a `ToolRouter` with `ToolRouter::from_config(...)`

`core/src/tools/spec.rs` converts configuration plus discovered artifacts into tool specs and handlers.

- regular MCP tools are converted to direct function tools and registered with `mcp_handler`
- dynamic tools are converted to function tools and registered with `dynamic_tool_handler`
- if `config.search_tool` is enabled and app tools exist, `tool_search` is registered and app-tool handlers are kept hidden behind namespaces
- if `config.tool_suggest` is enabled and discoverable tools exist, `tool_suggest` is registered

`core/src/tools/registry.rs` stores handlers in `ToolRegistry`.

- handlers are keyed by `tool_handler_key(name, namespace)`
- dispatch is by `ToolPayload`
- supported payload variants are `Function`, `ToolSearch`, and `Mcp`

### 2.1.2 Tool schema construction and normalization

`protocol/src/dynamic_tools.rs` defines `DynamicToolSpec`.

- fields include `name`, `description`, `input_schema`, and `defer_loading`
- legacy `exposeToContext` is inverted into `defer_loading`

`tools/src/mcp_tool.rs` normalizes MCP schemas in `parse_mcp_tool(...)`.

- if the incoming schema omits `properties` or sets it to `null`, the code inserts an empty object
- the returned output schema includes `content`, `structuredContent`, `isError`, and `_meta`

`tools/src/tool_discovery.rs` defines the synthetic discovery tools.

- `create_tool_search_tool(...)` creates a `ToolSpec::ToolSearch`
- its description text states that it searches app/connector tool metadata with BM25 and exposes matching tools for the next model call
- `create_tool_suggest_tool(...)` creates a separate function tool named `tool_suggest`

### 2.1.3 Model-visible surface

`core/src/tools/router.rs` keeps two lists.

- `specs`
- `model_visible_specs`

`core/src/codex.rs` removes deferred dynamic tools from the prompt-visible set in `build_prompt(...)`.

- it filters `turn_context.dynamic_tools`
- any dynamic tool with `defer_loading = true` is excluded from `router.model_visible_specs()`

`core/src/tools/router.rs` also maps model response items back to executable payloads.

- `ResponseItem::ToolSearchCall` with `execution == "client"` becomes `ToolPayload::ToolSearch`

### 2.1.4 Execution path

Dynamic tool execution is implemented in `core/src/tools/handlers/dynamic.rs`.

- `DynamicToolHandler.handle(...)` parses JSON arguments
- it calls `request_dynamic_tool(...)`
- that function inserts a pending oneshot responder into active turn state
- it emits `EventMsg::DynamicToolCallRequest`
- it waits for a client response
- it emits `DynamicToolCallResponseEvent`

MCP execution is implemented in `core/src/tools/handlers/mcp.rs`.

- `McpHandler.handle(...)` unwraps `ToolPayload::Mcp`
- it calls `handle_mcp_tool_call(...)`

### 2.1.5 Tests covering staged exposure

The inspected tests verify the registry/router split.

- `core/tests/suite/search_tool.rs` checks that `tool_search` appears instead of direct app tools when the feature is enabled
- the same test file covers the no-search path where app tools are directly exposed
- `core/tests/suite/tool_suggest.rs` checks that `tool_suggest` can be present even when `tool_search` is unavailable

---

## 2.2 Gemini CLI

Inspected files:

- `~/workspace_genai/gemini-cli/packages/core/src/tools/tool-registry.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/tools/tools.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/tools/mcp-client-manager.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/tools/mcp-client.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/tools/mcp-tool.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/tools/activate-skill.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/skills/skillManager.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/prompts/promptProvider.ts`
- `~/workspace_genai/gemini-cli/packages/core/src/prompts/snippets.ts`

### 2.2.1 Registry and discovery

`packages/core/src/tools/tool-registry.ts` stores all tools in `allKnownTools: Map<string, AnyDeclarativeTool>`.

- `registerTool(...)` inserts tools into the registry even if policy later excludes them
- `getActiveTools()` applies exclusion policy to the registry contents
- `getFunctionDeclarations()` converts the active set into model-visible declarations

`discoverAllTools()` handles command-based dynamic discovery.

- it removes prior discovered tools
- it runs `discoverAndRegisterToolsFromCommand()`

`discoverAndRegisterToolsFromCommand()` loads tools from an external command.

- it runs configured `toolDiscoveryCommand`
- it parses stdout as JSON
- it expects an array of function or tool declarations
- each result is wrapped as a `DiscoveredTool`
- discovered tool names are prefixed with `DISCOVERED_TOOL_PREFIX`
- stdout and stderr are capped at 10 MB

### 2.2.2 Discovered-tool execution

`DiscoveredToolInvocation.execute()` runs configured `toolCallCommand`.

- it passes the original tool name
- it sends JSON parameters on stdin
- execution happens through the external command configured for discovered tools

### 2.2.3 MCP lifecycle

`packages/core/src/tools/mcp-client-manager.ts` manages MCP startup.

- `startConfiguredMcpServers()` starts configured servers
- `maybeDiscoverMcpServer(...)` applies allow, block, trust, and disabled-by-user checks before discovery

Workspace trust is part of startup gating.

- untrusted workspaces block some MCP startup paths

`packages/core/src/tools/mcp-client.ts` performs server discovery in `discoverInto(...)`.

- it fetches prompts
- it fetches tools
- it fetches resources
- it updates registries
- it sorts tools
- it validates policy rules against discovered tool names

`discoverTools(...)` calls `mcpClient.listTools()`.

- the returned list is filtered by config `isEnabled`
- each tool is wrapped as `DiscoveredMCPTool`
- MCP annotations such as `readOnlyHint` are preserved

### 2.2.4 MCP naming and execution

`packages/core/src/tools/mcp-tool.ts` defines naming helpers.

- `MCP_TOOL_PREFIX = "mcp_"`
- `formatMcpToolName(...)`
- `parseMcpToolName(...)`

`DiscoveredMCPTool` builds multiple names.

- display name format: `serverToolName (serverName MCP Server)`
- fully qualified name format: sanitized `serverName + "_" + serverToolName`

`DiscoveredMCPToolInvocation.execute()` calls the MCP callable tool.

- it forwards arguments to the MCP client
- it checks `isError`
- it transforms MCP content blocks into model parts
- it returns display text for the terminal surface

Confirmation behavior is also implemented there.

- trusted folder plus trusted server skips confirmation
- otherwise the invocation asks for confirmation with server and tool details

### 2.2.5 Prompt-visible skill surface and activation

`packages/core/src/skills/skillManager.ts` discovers skills from multiple roots.

- built-in
- extension
- user
- user agent alias
- workspace
- workspace agent alias

Workspace skill discovery is trust-gated.

- `discoverSkills(...)` only loads workspace skills when `isTrusted = true`

`packages/core/src/prompts/promptProvider.ts` reads `config.getSkillManager().getSkills()`.

- it injects only skill metadata into the prompt

`packages/core/src/prompts/snippets.ts` contains the `<available_skills>` block.

- prompt text instructs the model to call `activate_skill`

`packages/core/src/tools/activate-skill.ts` implements activation.

- it validates that the named skill exists
- built-in skills skip confirmation
- non-built-in skills ask confirmation and preview folder structure
- on success it calls `skillManager.activateSkill(name)`
- it adds the skill directory to workspace context
- it returns `<activated_skill name="...">`
- it includes `<instructions>` and `<available_resources>` in the tool result

### 2.2.6 Search

No function was found in the inspected Gemini CLI runtime files that takes a free-text query and returns ranked tool matches.

The inspected code paths provide:

- registry assembly
- dynamic discovery via external command
- exact-name skill activation
- MCP discovery
- policy filtering

---

## 2.3 OpenCode

Inspected files:

- `~/workspace_genai/opencode/packages/opencode/src/tool/registry.ts`
- `~/workspace_genai/opencode/packages/opencode/src/tool/skill.ts`
- `~/workspace_genai/opencode/packages/opencode/src/skill/discovery.ts`
- `~/workspace_genai/opencode/packages/opencode/src/session/system.ts`
- `~/workspace_genai/opencode/packages/opencode/src/mcp/index.ts`

### 2.3.1 Tool registry assembly

`packages/opencode/src/tool/registry.ts` assembles the registry from three sources.

- built-in tool infos
- local custom tool modules under `tool/tools/*.js|ts`
- plugin-provided tools

Custom tool loading is filesystem-based.

- the registry scans the tool directory
- it imports matching modules
- exported values are converted into `Tool.Info`

Plugin tools are loaded from the plugin registry.

- the registry iterates `plugin.list()`
- it reads each plugin `p.tool`

`tools(model, agent?)` constructs the active tool set.

- it filters by provider and model conditions
- examples in the code include `codesearch`, `websearch`, and `apply_patch` versus `edit` and `write`
- it initializes each selected tool via `tool.init(...)`

### 2.3.2 Skill discovery and activation

`packages/opencode/src/session/system.ts` injects skill metadata into the system prompt.

- `SystemPrompt.skills(agent)` calls `Skill.available(agent)`
- prompt text instructs the model to use the skill tool when a task matches a skill description

`packages/opencode/src/tool/skill.ts` defines the skill tool.

- the tool description enumerates available skills
- tool output includes `<skill_content name="...">`

Skill execution path:

- it resolves the requested skill with `Skill.get(name)`
- it requests permission with `ctx.ask({ permission: "skill", patterns: [name], always: [name], ... })`
- it returns full skill content
- it returns the base directory URL
- it includes sampled `<skill_files>`

### 2.3.3 Remote skill catalog

`packages/opencode/src/skill/discovery.ts` loads remote skill metadata from a catalog.

- it fetches `index.json` from a configured base URL
- it validates that entries contain `SKILL.md`
- it downloads referenced files into cache under `Global.Path.cache/skills/<skill>`

### 2.3.4 MCP path

`packages/opencode/src/mcp/index.ts` wraps MCP tools with AI SDK `dynamicTool(...)`.

- `defs(key, client, timeout)` calls MCP `list_tools`
- tool definitions are keyed by sanitized client and tool names
- execution calls `client.callTool(...)`

The inspected file also exposes prompt and resource surfaces.

- `tools()`
- `prompts()`
- `resources()`
- `add()`
- `connect()`

### 2.3.5 Search

No function was found in the inspected OpenCode runtime files that takes a free-text query and returns ranked tool matches.

The inspected code paths provide:

- registry assembly from built-ins, local modules, and plugins
- metadata-first skill prompting
- explicit skill loading
- MCP wrapping

---

## 2.4 fork-claude-code

Inspected files:

- `~/workspace_genai/fork-claude-code/tools.ts`
- `~/workspace_genai/fork-claude-code/Tool.ts`
- `~/workspace_genai/fork-claude-code/tools/ToolSearchTool/ToolSearchTool.ts`
- `~/workspace_genai/fork-claude-code/tools/MCPTool/MCPTool.ts`
- `~/workspace_genai/fork-claude-code/services/mcp/client.ts`
- `~/workspace_genai/fork-claude-code/commands.ts`

### 2.4.1 Tool assembly

`fork-claude-code/tools.ts` defines the built-in tool set and the assembly path.

- `getAllBaseTools()` returns the built-in tool list
- the built-in list includes `ListMcpResourcesTool`, `ReadMcpResourceTool`, and conditionally `ToolSearchTool`
- `getTools(permissionContext)` filters tools by deny rules, REPL mode, and each tool's `isEnabled()` result
- `assembleToolPool(...)` combines built-in tools with MCP tools, sorts each partition by name, and deduplicates by tool name

`fork-claude-code/Tool.ts` defines the shared tool contract.

- `ToolUseContext.options.tools` holds the current tool list
- `findToolByName(...)` and `toolMatchesName(...)` resolve tools by name
- tool definitions include optional fields such as `searchHint`, `mcpInfo`, and `mapToolResultToToolResultBlockParam(...)`

### 2.4.2 Tool search

`fork-claude-code/tools/ToolSearchTool/ToolSearchTool.ts` implements deferred-tool search.

- the tool is enabled by `isToolSearchEnabledOptimistic()`
- it searches only deferred tools
- `parseToolName(...)` splits MCP names like `mcp__server__tool` and regular tool names into searchable parts
- `searchToolsWithKeywords(...)` scores matches from tool-name parts, `searchHint`, and tool prompt text
- results are sorted by descending score and truncated to `max_results`
- `select:<tool_name>` bypasses keyword scoring for direct tool selection

### 2.4.3 MCP tool materialization

`fork-claude-code/services/mcp/client.ts` materializes MCP tools and resource helpers.

- `fetchToolsForClient(...)` requests MCP `tools/list`
- each returned MCP tool is converted into a local `Tool` object
- the converted tool name is built with `buildMcpToolName(client.name, tool.name)` unless SDK prefix-skipping is enabled
- converted MCP tools carry `mcpInfo`, `isMcp`, optional `_meta['anthropic/searchHint']` as `searchHint`, and prompt/description methods
- `getMcpToolsCommandsAndResources(...)` fetches tools, commands, skills, and resources from connected servers
- if a connected server supports resources, `ListMcpResourcesTool` and `ReadMcpResourceTool` are added once

### 2.4.4 Execution path

The inspected runtime uses concrete tool objects rather than a separate registry payload type.

- built-in tools are defined through `buildTool(...)`
- MCP tools are created by spreading `MCPTool` into each materialized MCP tool object
- the assembled tool pool is passed through `ToolUseContext.options.tools`
- the inspected lookup path resolves tools by name

---

## 2.5 OpenClaw

Inspected files:

- `~/workspace_genai/openclaw/src/plugins/discovery.ts`
- `~/workspace_genai/openclaw/src/plugins/loader.ts`
- `~/workspace_genai/openclaw/src/plugins/runtime.ts`
- `~/workspace_genai/openclaw/src/plugins/registry.ts`
- `~/workspace_genai/openclaw/src/plugins/registry-empty.ts`
- `~/workspace_genai/openclaw/src/plugins/tools.ts`
- `~/workspace_genai/openclaw/src/plugins/manifest.ts`
- `~/workspace_genai/openclaw/src/auto-reply/skill-commands.ts`
- `~/workspace_genai/openclaw/src/auto-reply/skill-commands.runtime.ts`
- `~/workspace_genai/openclaw/src/mcp/channel-tools.ts`
- `~/workspace_genai/openclaw/src/mcp/channel-server.ts`

### 2.5.1 Discovery

`openclaw/src/plugins/discovery.ts` discovers plugin candidates from configured roots.

- it resolves workspace, global, and bundled roots
- it caches discovery results in `discoveryCache`
- cache keys include workspace path, ownership uid, config root, bundled root, and load paths
- it checks path escape, stat failures, world-writable paths, and suspicious ownership before accepting a candidate
- it loads plugin manifests and package metadata to build `PluginCandidate`

`openclaw/src/plugins/manifest.ts` defines the manifest schema and loader.

- manifest file name is `openclaw.plugin.json`
- manifest fields include `id`, `configSchema`, `kind`, `channels`, `providers`, `skills`, `contracts`, and `channelConfigs`
- `loadPluginManifest(...)` reads the manifest through boundary-checked file access

### 2.5.2 Registry construction and plugin registration

`openclaw/src/plugins/registry.ts` defines the registry shape.

`PluginRegistry` stores:

- `plugins`
- `tools`
- `hooks`
- `typedHooks`
- `channels`
- `channelSetups`
- `providers`
- `cliBackends`
- `speechProviders`
- `mediaUnderstandingProviders`
- `imageGenerationProviders`
- `webSearchProviders`
- `gatewayHandlers`
- `httpRoutes`
- `cliRegistrars`
- `services`
- `commands`
- `conversationBindingResolvedHandlers`
- `diagnostics`

`createPluginRegistry(...)` returns both the registry and a plugin API builder.

`registerTool(...)`:

- accepts either a concrete tool or a factory
- normalizes tool names
- records names on the owning `PluginRecord`
- pushes `PluginToolRegistration` entries into `registry.tools`

`createApi(...)` exposes plugin registration handlers through `buildPluginApi(...)`.

- `registerTool`
- `registerHook`
- `registerHttpRoute`
- `registerProvider`
- `registerSpeechProvider`
- `registerMediaUnderstandingProvider`
- `registerImageGenerationProvider`
- `registerWebSearchProvider`
- `registerGatewayMethod`
- `registerCli`
- `registerService`
- `registerCliBackend`
- `registerInteractiveHandler`
- `onConversationBindingResolved`
- `registerCommand`
- `registerContextEngine`

### 2.5.3 Loader path

`openclaw/src/plugins/loader.ts` orchestrates runtime loading.

- it creates a registry with `createPluginRegistry(...)`
- it runs `discoverOpenClawPlugins(...)`
- it builds a manifest registry with `loadPluginManifestRegistry(...)`
- it sorts candidates with `compareDuplicateCandidateOrder(...)`
- it filters by configured plugin scope
- it creates plugin records and loads plugin modules

The loader maintains a registry cache.

- cache entries include the registry plus memory-plugin runtime state
- cache keys encode plugin config, installs, load paths, startup mode, subagent mode, SDK resolution mode, and gateway method names

### 2.5.4 Active registry state

`openclaw/src/plugins/runtime.ts` keeps runtime-global registry state on `globalThis`.

- `setActivePluginRegistry(...)` installs the active registry and cache key
- separate tracked surfaces exist for `httpRoute` and `channel`
- each surface can be pinned independently
- `runtimeSubagentMode` values are `default`, `explicit`, and `gateway-bindable`

### 2.5.5 Materializing plugin tools for agent use

`openclaw/src/plugins/tools.ts` converts registry entries into usable tools.

- it first applies plugin enablement defaults and auto-enable rules
- if plugins are effectively disabled, it returns an empty list
- it resolves a plugin registry, optionally reusing the active registry in gateway-bindable mode
- it iterates `registry.tools`
- it calls each entry factory with `OpenClawPluginToolContext`
- optional tools are only kept if they match the normalized allowlist
- it blocks plugin ids that conflict with existing core tool names
- it drops per-tool name conflicts
- it attaches plugin metadata in a `WeakMap`

### 2.5.6 Skill-command surface

`openclaw/src/auto-reply/skill-commands.ts` builds workspace and agent skill command lists.

`listSkillCommandsForWorkspace(...)`:

- calls `buildWorkspaceSkillCommandSpecs(...)`
- passes remote-skill eligibility and reserved command names

`listSkillCommandsForAgents(...)`:

- resolves agent workspaces
- merges per-agent skill filters
- deduplicates by canonical workspace path
- builds workspace skill command specs
- deduplicates final commands by skill name

`skill-commands.runtime.ts` re-exports the runtime entrypoints from `skill-commands.js`.

### 2.5.7 OpenClaw channel MCP server

`openclaw/src/mcp/channel-server.ts` creates a dedicated MCP server named `openclaw`.

- it constructs `McpServer`
- it creates `OpenClawChannelBridge`
- it installs permission-request notification handling
- it registers channel tools through `registerChannelMcpTools(...)`
- it supports stdio serving through `serveOpenClawChannelMcp(...)`

`openclaw/src/mcp/channel-tools.ts` defines concrete MCP tools on that server.

- `conversations_list`
- `conversation_get`
- `messages_read`
- `attachments_fetch`
- `events_poll`
- `events_wait`
- `messages_send`
- `permissions_list_open`
- `permissions_respond`

Each tool is registered with `server.tool(...)` and returns either `content` plus `structuredContent` or an error-shaped MCP result.

### 2.5.8 Search

No function was found in the inspected OpenClaw tool-loading files that takes a free-text query and returns ranked tool matches.

The inspected OpenClaw code provides:

- plugin candidate discovery
- manifest loading
- registry construction
- active-registry pinning
- plugin-tool materialization
- skill command generation
- a separate MCP server surface

---
