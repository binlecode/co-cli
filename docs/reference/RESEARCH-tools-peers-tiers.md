# RESEARCH: Tool Tiering Across Peers — fork-cc · hermes · opencode · codex

Cross-peer synthesis of all four `RESEARCH-tools-*.md` files plus
`RESEARCH-tools-gaps-co-vs-hermes.md`. Establishes a three-tier classification
of tool capabilities based on convergence frequency across peers, with
full per-peer inventories, lifecycle architecture comparison, co-cli coverage
mapping, and prioritized gap analysis.

**Peers reviewed:** fork-claude-code (fork-cc), hermes-agent, opencode, codex.  
**Reference system:** co-cli (~35 native tools as of v0.8.x).

**Sources:**
- `RESEARCH-tools-fork-cc.md` — fork-claude-code source diff
- `RESEARCH-tools-hermes-agent.md` — hermes tool registry
- `RESEARCH-tools-opencode.md` — opencode built-in registry
- `RESEARCH-tools-codex.md` — codex Rust tool spec
- `RESEARCH-tools-gaps-co-vs-hermes.md` — per-tool parity + architecture gaps

**Tiering method:** Each capability is scored by how many of the four peers
implement it as a native first-class tool. **Tier 1 = 3–4 peers** (universal),
**Tier 2 = 2–3 peers** (converged capability), **Tier 3 = 1–2 peers**
(specialized / differentiated).

---

## Part 1: Per-Peer Tool Inventories

### 1.1 fork-claude-code

Source: `getAllBaseTools()` in `fork-claude-code/tools.ts` + built-in tool directories.

#### Core file and shell

| Tool | Class |
|---|---|
| Shell execution | `BashTool` |
| File read | `ReadTool` |
| File write | `WriteTool` |
| File edit (string replacement) | `EditTool` |
| Glob / file find | `GlobTool` |
| Content search (ripgrep) | `GrepTool` |
| PowerShell (Windows) | `PowerShellTool` |

#### Interaction and mode control

| Tool | Class |
|---|---|
| Ask user a question | `AskUserQuestionTool` |
| Enter plan mode | `EnterPlanModeTool` |
| Exit plan mode v2 | `ExitPlanModeV2Tool` |
| Enter git worktree | `EnterWorktreeTool` |
| Exit git worktree | `ExitWorktreeTool` |

#### Web

| Tool | Class |
|---|---|
| Web fetch | `WebFetchTool` |
| Web search | `WebSearchTool` |

#### Task tracking

| Tool | Class |
|---|---|
| Structured to-do list | `TodoWriteTool` |

#### Code intelligence

| Tool | Class |
|---|---|
| Language Server Protocol (diagnostics, hover, go-to-def) | `LSPTool` |
| Jupyter notebook cell editing | `NotebookEditTool` |

#### Multi-agent and task orchestration

| Tool | Class |
|---|---|
| Spawn agent / run subagent | `AgentTool` |
| Create tracked task | `TaskCreateTool` |
| Get task state | `TaskGetTool` |
| List tasks | `TaskListTool` |
| Update task | `TaskUpdateTool` |
| Get task output | `TaskOutputTool` |
| Stop task | `TaskStopTool` |
| Send message to agent or user | `SendMessageTool` |
| Create team | `TeamCreateTool` |
| Delete team | `TeamDeleteTool` |

#### MCP resources

| Tool | Class |
|---|---|
| List MCP resources | `ListMcpResourcesTool` |
| Read MCP resource | `ReadMcpResourceTool` |
| Generic MCP tool wrapper | `MCPTool` |
| MCP authentication flow | `McpAuthTool` |

#### Configuration and metadata

| Tool | Class |
|---|---|
| Read / write Claude Code config | `ConfigTool` |
| Read project brief (CLAUDE.md) | `BriefTool` |

#### Scheduling and remote execution

| Tool | Class |
|---|---|
| Create cron job | `CronCreateTool` |
| Delete cron job | `CronDeleteTool` |
| List cron jobs | `CronListTool` |
| Trigger remote agent run | `RemoteTriggerTool` |

**fork-cc total: ~38 built-in tools**

#### fork-cc Lifecycle Architecture

| Aspect | Detail |
|---|---|
| Tool contract | `Tool<T>`: `inputSchema`, `isConcurrencySafe`, `isReadOnly`, `isDestructive`, `interruptBehavior`, `validateInput`, `checkPermissions`, `backfillObservableInput`, `mapToolResultToToolResultBlockParam`, `maxResultSizeChars`, `aliases` |
| Hook runners | `runPreToolUseHooks` (yields: permission result, updated input, prevent-continuation signal, additional context), `runPostToolUseHooks`, `runPostToolUseFailureHooks` |
| Permission sources | `userSettings`, `projectSettings`, `localSettings`, `flagSettings`, `policySettings`, `cliArg`, `command`, `session` |
| Permission modes | `acceptEdits`, `bypassPermissions`, `default`, `dontAsk`, `plan` |
| Concurrency partitioning | `partitionToolCalls()` batches calls by `isConcurrencySafe` before dispatch |
| Per-tool rendering | `tools/*/UI.tsx` — each tool has its own React component |

---

### 1.2 hermes-agent

Source: `hermes-agent/tools/` implementations + `toolsets.py`.

#### Core system and file

| Tool | Key parameters / behavior |
|---|---|
| `clarify` | Multi-choice (≤4) or open-ended; blocking gateway pattern; dual-call-safe |
| `memory` | `action`: add / replace / remove; `target`: memory (notes) or user (profile); persistent `MEMORY.md` + `USER.md` |
| `patch` | `mode`: replace (old/new string, fuzzy 9-strategy) or patch (V4A multi-hunk); auto-syntax-check |
| `process` | Background process lifecycle: list / poll / log / wait / kill / write / submit / close; `session_id`-keyed |
| `read_file` | `offset` + `limit` line pagination; 100K char cap; suggests similar filenames |
| `search_files` | Unified: `target=content` (ripgrep regex) or `target=files` (glob); output_mode: content / files_only / count |
| `session_search` | FTS5 boolean syntax across all past sessions; no-query = recent sessions browse (zero LLM cost) |
| `terminal` | `background`, `timeout`, `workdir`, `pty`, `notify_on_complete`, `watch_patterns` (mid-process regex notifier) |
| `todo` | `todos` array with merge / replace; single `in_progress` rule |
| `write_file` | Full-file overwrite; creates parent dirs |

#### Browser automation

| Tool | Key parameters / behavior |
|---|---|
| `browser_navigate` | Load URL; returns accessibility tree snapshot; initializes session |
| `browser_snapshot` | Refresh accessibility tree; `full=true` for complete content |
| `browser_click` | Click element by ref ID (`@eN`) from snapshot |
| `browser_type` | Clear + type into field by ref ID |
| `browser_scroll` | Scroll page in direction |
| `browser_back` | Browser history back |
| `browser_press` | Press keyboard key (Enter, Tab, Escape, ArrowDown, …) |
| `browser_get_images` | List all page images with URLs and alt text |
| `browser_console` | Read console log / JS errors; optional JS `expression` eval |
| `browser_vision` | Screenshot + vision AI analysis; returns `MEDIA:` path for delivery |

#### Execution and delegation

| Tool | Key parameters / behavior |
|---|---|
| `cronjob` | `action`: create / list / update / pause / resume / remove / run; schedule as cron expr, interval, or ISO timestamp; `deliver` target; `skills`; `script` for context injection |
| `delegate_task` | `goal` (single) or `tasks` (parallel batch ≤3); `context`, `toolsets`, `max_iterations`; `acp_command` to spawn Claude Code or other ACP agents as children |
| `execute_code` | Python sandbox with `hermes_tools` import surface (web_search, web_extract, read_file, write_file, search_files, patch, terminal); 50KB stdout cap, 50-call budget, 5-min timeout |
| `mixture_of_agents` | Routes to 4 frontier LLMs + aggregator at max reasoning effort |

#### Web and media

| Tool | Key parameters / behavior |
|---|---|
| `image_generate` | FAL.ai; `aspect_ratio`: landscape / portrait / square |
| `text_to_speech` | Provider + voice user-configured; returns `MEDIA:` path |
| `vision_analyze` | `image_url` (http/https or local path) + `question`; full description + answer |
| `web_extract` | Up to 5 URLs per call in parallel; markdown output; PDF support; LLM-summarizes pages >5K chars |
| `web_search` | Brave-backed; `query` only |

#### Skills management

| Tool | Key parameters / behavior |
|---|---|
| `skills_list` | Optional `category` filter; returns name + description |
| `skill_view` | `name` + optional `file_path` for linked files (references/, templates/, scripts/) |
| `skill_manage` | `action`: create / edit / patch / delete / write_file / remove_file; YAML frontmatter + markdown body |

#### Home Assistant

| Tool | Key parameters / behavior |
|---|---|
| `ha_list_entities` | Filter by `domain` or `area` |
| `ha_get_state` | Full state + attributes for one entity |
| `ha_list_services` | Filter by `domain` |
| `ha_call_service` | `domain`, `service`, `entity_id`, JSON `data` |

#### Reinforcement learning training

| Tool | Key parameters / behavior |
|---|---|
| `rl_list_environments` | Returns env names, paths, descriptions |
| `rl_select_environment` | Loads default config for environment |
| `rl_get_current_config` | Returns modifiable fields only |
| `rl_edit_config` | Update `field` to `value` |
| `rl_start_training` | Starts run with current env + config |
| `rl_stop_training` | Stops run by `run_id` |
| `rl_check_status` | Returns WandB metrics; 30-min rate limit per run |
| `rl_get_results` | Final metrics + weights path for completed run |
| `rl_list_runs` | All runs (active + completed) |
| `rl_test_inference` | Quick sanity check before training; `num_steps`, `group_size`, `models` |

#### Messaging

| Tool | Key parameters / behavior |
|---|---|
| `send_message` | `action`: send / list; `target`: platform / platform:#channel / platform:chat_id:thread_id; supports Telegram, Discord, Slack, Signal, Matrix, SMS |

**hermes total: ~48 registered tools**

#### hermes Lifecycle Architecture

| Aspect | Detail |
|---|---|
| Registry | Singleton `ToolRegistry`; AST auto-discovery scans `tools/*.py` for `registry.register()` — no manual list |
| Runtime availability | Per-tool `check_fn` evaluated before each invocation (credential expiry, feature flags) |
| Named toolset profiles | `toolsets.py` + `resolve_toolset()`: `web`, `file`, `terminal`, `skills`, `session_search`, `browser`, `vision`, `image_gen`, `tts`, `todo`, `cronjob`, `homeassistant` |
| Approval model | Blocking gateway pattern; answer channel independent of tool call identity; `_permanent_approved` for cross-session persistence |
| Result size | `max_result_size_chars` enforced uniformly by registry on all tools including MCP |

---

### 1.3 opencode

Source: `ToolRegistry.all(...)` in `opencode/src/tool/registry.ts`.

#### Built-in tools

| Tool | Class | Condition |
|---|---|---|
| Invalid tool call handler | `InvalidTool` | Always |
| Shell execution | `BashTool` | Always |
| File read | `ReadTool` | Always |
| Glob / file find | `GlobTool` | Always |
| Content search (ripgrep) | `GrepTool` | Always |
| File edit (string replacement) | `EditTool` | Always; swapped for `patch` on older GPT models |
| File write | `WriteTool` | Always; swapped for `patch` on older GPT models |
| Subagent task orchestration | `TaskTool` | Always; description dynamically injected with available subagents |
| Web fetch | `WebFetchTool` | Always |
| Structured to-do list | `TodoWriteTool` | Always |
| Web search (Exa) | `WebSearchTool` | Provider-conditional or `OPENCODE_ENABLE_EXA` |
| Code-specific search (Exa) | `CodeSearchTool` | Provider-conditional or `OPENCODE_ENABLE_EXA` |
| Load skill instructions | `SkillTool` | Always; description dynamically injected with available skills |
| Apply unified diff patch | `ApplyPatchTool` | Older GPT models only (replaces `edit`/`write`) |
| Ask user a question | `QuestionTool` | `OPENCODE_CLIENT` or `OPENCODE_ENABLE_QUESTION_TOOL` |
| Language Server Protocol | `LspTool` | `OPENCODE_EXPERIMENTAL_LSP_TOOL` |
| Exit plan mode | `PlanExitTool` | `OPENCODE_EXPERIMENTAL_PLAN_MODE` + CLI client |

**Additional loading paths:**
- Repo-local custom tools from `tool/*.ts` or `tools/*.ts` in working directory
- Plugin-provided tools injected via `tool.definition` plugin hook

**opencode total: 17 built-in tools + dynamic custom/plugin tools**

#### opencode Lifecycle Architecture

| Aspect | Detail |
|---|---|
| Tool contract | `Tool.Def`: `description`, `parameters`, `execute()`, optional `formatValidationError()` |
| Tool execution context | `Tool.Context`: `sessionID`, `messageID`, `agent`, `abort`, `callID`, `messages`, `metadata()`, `ask()` |
| Validation + truncation | `Tool.define(...)` wrapper applies Zod runtime validation + `Truncate.output()` unless `metadata.truncated` already set |
| Plugin hooks | `tool.definition` (post-init schema rewrite), `tool.execute.before`, `tool.execute.after` |
| Permission system | `Permission.ask()` creates pending request; `Permission.reply()` resolves/rejects; `"always"` persists allow rules and may auto-resolve related pending requests |
| Doom-loop detection | `SessionProcessor` checks for repeated identical recent tool calls before execution |
| Tool-part state machine | `pending → running → completed / error` persisted to transcript in `SessionProcessor.handleEvent()` |
| History replay | Completed → output-available; errored → output-error; pending/running → interrupted tool-result error; media extracted into synthetic user messages for providers lacking tool-result media support |
| Model-aware filtering | `codesearch`/`websearch` conditional on model; `apply_patch` vs `edit`/`write` conditional on model version |

---

### 1.4 codex

Source: `codex-rs/core/src/tools/spec.rs` + `codex-rs/tools/src/lib.rs`.

#### Wire schema (tool spec variants)

| Variant | Usage |
|---|---|
| `Function(ResponsesApiTool)` | Standard function-call tool |
| `ToolSearch` | Model-native tool discovery (deferred) |
| `LocalShell` | Local shell execution |
| `ImageGeneration` | Image generation (feature-gated) |
| `WebSearch` | Web search |
| `Freeform` | Custom / freeform tools |

`ResponsesApiTool` fields: `name`, `description`, `strict`, optional `defer_loading`, `parameters`, `output_schema`.  
Assembly: `create_tools_json_for_responses_api()`, `mcp_tool_to_responses_api_tool()`, `mcp_tool_to_deferred_responses_api_tool()`.

#### Shell and process I/O

| Tool | Description |
|---|---|
| `exec_command` | Execute command; returns `raw_output`, `wall_time`, `exit_code`, `process_id` |
| `write_stdin` | Write raw stdin data to a running process |
| `shell_command` | Alternative shell command surface |

#### Interaction and planning

| Tool | Description |
|---|---|
| `update_plan` | Update current plan artifact |
| `request_permissions` | Request elevated `SandboxPermissions` |
| `request_user_input` | Block and collect user input |

#### Patching and tool discovery

| Tool | Description |
|---|---|
| `apply_patch` | Apply unified diff / V4A multi-hunk patch |
| `tool_search` | Search available tools by query |
| `tool_suggest` | Suggest tools suited to a task |

#### MCP resources

| Tool | Description |
|---|---|
| `list_mcp_resources` | List resources exposed by MCP servers |
| `list_mcp_resource_templates` | List MCP resource URI templates |
| `read_mcp_resource` | Read a specific MCP resource by URI |

#### Media and REPL

| Tool | Description |
|---|---|
| `view_image` | Display and analyze an image |
| `js_repl` | Execute JavaScript in a persistent REPL |
| `js_repl_reset` | Reset REPL state |
| `ImageGeneration` | Generate images (feature-gated via `ToolsConfig`) |

#### Mode control

| Tool | Description |
|---|---|
| `code` | Enter code mode |
| `wait` | Wait for an async result |

#### Multi-agent lifecycle

| Tool | Description |
|---|---|
| `spawn_agent` | Spawn a new agent |
| `send_input` / `send_message` | Send input or message to an agent |
| `assign_task` | Assign a task to an agent |
| `resume_agent` | Resume a paused agent |
| `wait_agent` | Block until agent completes |
| `close_agent` | Terminate an agent |
| `list_agents` | List running agents |
| `spawn_agents_on_csv` | Spawn one agent per row of CSV data |
| `report_agent_job_result` | Report job result back to parent agent |

#### Test utilities

| Tool | Description |
|---|---|
| `test_sync_tool` | Synchronous test fixture tool |

**codex total: ~28 built-in tools (some feature-gated via `ToolsConfig`)**

#### codex Lifecycle Architecture

| Aspect | Detail |
|---|---|
| Handler trait | `ToolHandler`: `kind()`, async `is_mutating()`, `pre_tool_use_payload()`, `post_tool_use_payload()`, async `handle()` |
| Registry | `ToolRegistryBuilder.push_spec()` + `push_spec_with_parallel_support()` + `register_handler()` → `build()` yields `(Vec<ConfiguredToolSpec>, ToolRegistry)` |
| Router | `ToolRouter.from_config()`: stores `specs` + `model_visible_specs`; filters nested code-mode tools when `code_mode_only_enabled` |
| Dispatch | `dispatch_any()`: checks handler, checks payload kind, runs pre-tool hooks, evaluates `is_mutating()`, gates mutating tools on `turn.tool_call_gate`, runs post-tool hooks |
| Hook runtime | `run_pre_tool_use_hooks(session_id, turn_id, cwd, transcript_path, model, permission_mode, tool_name, tool_use_id, command)` + `run_post_tool_use_hooks(..., tool_response)` in `hook_runtime.rs` |
| Payload variants | `ToolPayload`: `Function`, `ToolSearch`, `Custom`, `LocalShell`, `Mcp` |
| Output trait | `ToolOutput`: `log_preview()`, `success_for_logging()`, `to_response_item()`, optional `post_tool_use_response()`, `code_mode_result()` |
| Typed output impls | `FunctionToolOutput` (body, success, post_tool_use_response); `ExecCommandToolOutput` (raw_output, wall_time, max_output_tokens, process_id, exit_code, `truncated_output()` via `formatted_truncate_text()`) |
| Sandbox permissions | `SandboxPermissions::UseDefault`, `RequireEscalated`, `WithAdditionalPermissions`; helper methods `requires_escalated_permissions()`, `requests_sandbox_override()` |
| Namespace dispatch | `tool_handler_key(tool_name)` or `{namespace}:{tool_name}` |

---

## Part 2: Cross-Peer Convergence Matrix

Score = number of peers (fork-cc, hermes, opencode, codex) with a native first-class implementation.
co-cli column shows current coverage status.

### 2.1 File and Workspace Operations

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| File read (paginated) | ReadTool | read_file | read | (via API) | 3–4 | `file_read` ✓ |
| File write (full overwrite) | WriteTool | write_file | write | (via API) | 3–4 | `file_write` ✓ |
| File edit (string replace) | EditTool | patch (replace mode) | edit | apply_patch | 4 | `file_patch` ✓ |
| Multi-hunk patch (V4A/unified diff) | — | patch (patch mode) | ApplyPatchTool | apply_patch | 3 | `file_patch` (`_v4a.py`) ✓ |
| File / glob search | GlobTool | search_files(files) | glob | — | 3 | `file_find` ✓ |
| Content / grep search | GrepTool | search_files(content) | grep | — | 3 | `file_search` ✓ |

### 2.2 Shell and Process Execution

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Foreground shell execution | BashTool | terminal | bash | exec_command | 4 | `shell` ✓ |
| Background process start | — | terminal(background=True) | — | (via code/wait) | 2 | `task_start` ✓ |
| Background process status / output | — | process(poll/log) | — | — | 1 | `task_status` ✓ |
| Background process kill | — | process(kill) | — | — | 1 | `task_cancel` ✓ |
| Background process list | — | process(list) | — | — | 1 | `task_list` ✓ |
| Stdin write to running process | — | process(write/submit/close) | — | write_stdin | 2 | ✗ gap |
| PTY mode (interactive CLI) | — | terminal(pty=True) | — | — | 1 | ✗ gap |
| Output watch patterns (notify) | — | terminal(watch_patterns) | — | — | 1 | ✗ gap |
| Code / sandboxed execution | — | execute_code | — | js_repl | 2 | `code_execute` (host-only) |

### 2.3 Web

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Web search | WebSearchTool | web_search | search (conditional) | WebSearch | 4 | `web_search` ✓ |
| Web fetch / extract (single URL) | WebFetchTool | web_extract | fetch | (via API) | 3–4 | `web_fetch` ✓ |
| Batch URL fetch (multi-URL parallel) | — | web_extract(urls=[…]) | — | — | 1 | ✗ gap |
| Code-specific search | — | — | CodeSearchTool | tool_search | 2 | ✗ gap |

### 2.4 Interaction and Session Control

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Ask user a question / clarify | AskUserQuestion | clarify | QuestionTool | request_user_input | 4 | `clarify` ✓ (one-shot; see §3.1) |
| Structured to-do / task list | TodoWriteTool | todo | TodoWriteTool | — | 3 | `todo_write` / `todo_read` ✓ |
| Runtime capability / tool introspection | — | (registry check_fn only) | — | tool_search / tool_suggest | 1 | `capabilities_check` ✓ (unique) |

### 2.5 Memory and Recall

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Persistent memory store (CRUD) | (subsystem) | memory | — | — | 2 | `memory_create/modify` ✓ |
| Paginated memory inventory | — | — | — | — | 0 | `memory_list` ✓ (unique) |
| Full memory artifact read by slug | — | — | — | — | 0 | `memory_read` ✓ (unique) |
| Session / history search | — | session_search | — | — | 1 | `memory_search` (T1) ✓ |
| T2 artifact search (FTS5 BM25) | — | — | — | — | 0 | `memory_search` (T2) ✓ (unique) |
| Role-filtered session search | — | session_search(role_filter) | — | — | 1 | ✗ gap |

### 2.6 Skills System

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Model-callable skill list | — | skills_list | skill (inject) | — | 2 | ✗ gap |
| Model-callable skill read | — | skill_view | skill (inject) | — | 2 | ✗ gap |
| Model-callable skill write / manage | — | skill_manage | — | — | 1 | ✗ gap |

### 2.7 Subagents and Delegation

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Named-goal subagent / delegate | AgentTool | delegate_task | task | spawn_agent | 4 | N/A — co-cli does not expose delegation as a model-visible primitive; every capability is a tool call; `web_research`, `knowledge_analyze`, `reason` encapsulate subagent dispatch internally |
| Named toolset profile for delegate | — | delegate_task(toolsets=[…]) | — | — | 1 | ✗ gap |
| Parallel batch subagent dispatch | — | delegate_task(tasks=[…]) | — | spawn_agents_on_csv | 2 | ✗ gap |
| Tracked task create/update/stop | TaskCreate/Get/Update/Stop/Output | — | — | assign_task/wait_agent/close_agent | 2 | (partial via task_*) |
| Agent-to-agent messaging | SendMessageTool | — | — | send_message/send_input | 2 | ✗ gap |
| Agent lifecycle (resume/close/list) | — | — | — | resume/close/list_agent | 1 | ✗ gap |

### 2.8 Planning and Mode Control

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Enter plan mode | EnterPlanModeTool | — | — | update_plan | 2 | ✗ gap |
| Exit plan mode | ExitPlanModeV2Tool | — | PlanExitTool | — | 2 | ✗ gap |
| Worktree management | EnterWorktreeTool / ExitWorktreeTool | — | — | — | 1 | ✗ gap |

### 2.9 Scheduling and Automation

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Cron / scheduled job CRUD | CronCreate/Delete/List | cronjob | — | — | 2 | ✗ gap |
| Remote agent trigger | RemoteTriggerTool | cronjob(deliver=…) | — | — | 2 | ✗ gap |

### 2.10 MCP and Extensibility

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| List MCP resources | ListMcpResourcesTool | — | — | list_mcp_resources | 2 | ✗ gap |
| Read MCP resource | ReadMcpResourceTool | — | — | read_mcp_resource | 2 | ✗ gap |
| MCP resource templates | — | — | — | list_mcp_resource_templates | 1 | ✗ gap |
| Dynamic MCP tool refresh | — | (deregister/re-register) | — | — | 1 | ✗ gap |
| MCP result size-gated | — | (registry uniform) | — | — | 1 | ✗ gap |

### 2.11 Vision and Media

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Image analysis / vision | — | vision_analyze | — | view_image | 2 | ✗ gap |
| Browser screenshot + vision | — | browser_vision | — | — | 1 | ✗ gap |
| Image generation | — | image_generate | — | ImageGeneration | 2 | ✗ gap |
| Text-to-speech | — | text_to_speech | — | — | 1 | ✗ gap |

### 2.12 Code Intelligence

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Language Server Protocol | LSPTool | — | LspTool | — | 2 | ✗ gap |
| Notebook (Jupyter) editing | NotebookEditTool | — | — | — | 1 | ✗ gap |

### 2.13 Browser Automation

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Browser navigate + snapshot | — | browser_navigate | — | — | 1 | ✗ gap |
| Browser click / type / scroll | — | browser_click/type/scroll | — | — | 1 | ✗ gap |
| Browser keyboard / back | — | browser_press/back | — | — | 1 | ✗ gap |
| Browser JS console | — | browser_console | — | — | 1 | ✗ gap |
| Browser image list | — | browser_get_images | — | — | 1 | ✗ gap |

### 2.14 External Service Integrations

| Capability | fork-cc | hermes | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Cross-platform messaging | SendMessageTool | send_message | — | — | 2 | ✗ gap |
| Smart home (Home Assistant) | — | ha_* (4 tools) | — | — | 1 | ✗ gap |
| Obsidian notes | — | — | — | — | 0 | `obsidian_*` ✓ (unique) |
| Google Workspace (Drive/Gmail/Calendar) | — | — | — | — | 0 | `drive_*`, `gmail_*`, `calendar_*` ✓ (unique) |
| RL training control | — | rl_* (10 tools) | — | — | 1 | ✗ gap |

### 2.15 Agentic Meta-Tools (unique to one peer)

| Capability | Peer | co-cli |
|---|---|---|
| Mixture of agents (4 LLMs + aggregator) | hermes | `reason` subagent covers some of this |
| Tool suggest by task | codex (tool_suggest) | ✗ gap |
| Tool search (model discovers tools) | codex (tool_search), fork-cc (ToolSearch spec) | ✗ gap |
| Config read/write | fork-cc (ConfigTool) | ✗ gap |
| Project brief (CLAUDE.md) read | fork-cc (BriefTool) | ✗ gap |
| Invalid tool call handler | opencode (InvalidTool) | implicit (pydantic-ai error path) |

---

## Part 3: Three-Tier Classification

### Tier 1 — System / Core

**Definition:** Present in 3–4 peers as a native first-class tool. Every general-purpose
AI agent CLI that reaches production independently converged on these.

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T1-1 | Shell / terminal execution | fork-cc, hermes, opencode, codex (4) | `shell` ✓ |
| T1-2 | File read (paginated, line-ranged) | fork-cc, hermes, opencode, (codex via API) (3–4) | `file_read` ✓ |
| T1-3 | File write (full overwrite) | fork-cc, hermes, opencode, (codex via API) (3–4) | `file_write` ✓ |
| T1-4 | File edit (string-replace) | fork-cc, hermes, opencode, codex (4) | `file_patch` ✓ |
| T1-5 | File / glob search | fork-cc, hermes, opencode (3) | `file_find` ✓ |
| T1-6 | Content / grep search | fork-cc, hermes, opencode (3) | `file_search` ✓ |
| T1-7 | Web search | fork-cc, hermes, opencode, codex (4) | `web_search` ✓ |
| T1-8 | Web fetch / extract | fork-cc, hermes, opencode, (codex via API) (3–4) | `web_fetch` ✓ |
| T1-9 | Structured to-do / task list | fork-cc, hermes, opencode (3) | `todo_write` / `todo_read` ✓ |
| T1-10 | Ask user / clarify (interactive question) | fork-cc, hermes, opencode, codex (4) | `clarify` ✓ (one-shot fragility; see §4.1) |
| T1-11 | Named-goal subagent delegation | fork-cc, hermes, opencode, codex (4) | N/A — co-cli's architecture does not surface delegation to the model; every capability is a tool; subagent dispatch is an internal implementation detail of tools like `web_research`, `knowledge_analyze`, `reason` |

**co-cli Tier 1 coverage: 10 / 11 applicable.**  
T1-11 is N/A by design: co-cli does not expose delegation as a model-visible primitive — the model only sees tools, and subagent dispatch is encapsulated inside them.  
Residual quality gaps: `clarify` one-shot fragility (§4.1), `web_fetch` single-URL only (§4.2).

---

### Tier 2 — Capabilities

**Definition:** Present in 2–3 peers. Not universal, but multiple mature systems
independently decided these are worth building as native tools.

#### T2-A: Memory and Recall

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-A1 | Persistent memory CRUD (user/agent notes) | fork-cc (subsystem), hermes (2) | `memory_create`, `memory_modify` ✓ |
| T2-A2 | Session / history search | hermes (1 native, strong case) | `memory_search` (T1) ✓ |
| T2-A3 | Role-filtered session recall | hermes `session_search(role_filter)` (1) | ✗ gap |

#### T2-B: Skills System

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-B1 | Model-callable skills list | hermes `skills_list`, opencode `skill` inject (2) | ✗ gap — model cannot discover skills without a user slash command |
| T2-B2 | Model-callable skill read / load | hermes `skill_view`, opencode `skill` inject (2) | ✗ gap |
| T2-B3 | Model-callable skill write (create/edit) | hermes `skill_manage` (1) | ✗ gap |

#### T2-C: Background and Process Control

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-C1 | Background process lifecycle (full API) | hermes `process`, fork-cc `Task*` (2) | `task_*` ✓ (partial: missing stdin write, PTY) |
| T2-C2 | Stdin write to running process | hermes `process(write/submit/close)`, codex `write_stdin` (2) | ✗ gap |
| T2-C3 | Output watch patterns / notify | hermes `terminal(watch_patterns)` (1) | ✗ gap |
| T2-C4 | PTY mode for interactive CLIs | hermes `terminal(pty=True)` (1) | ✗ gap |
| T2-C5 | Task output persistence (file-backed) | hermes (file-tee), codex `ExecCommandToolOutput` (2) | ✗ gap — in-memory deque(500) only |

#### T2-D: Scheduling and Remote Execution

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-D1 | Cron / scheduled job CRUD | fork-cc `CronCreate/Delete/List`, hermes `cronjob` (2) | ✗ gap |
| T2-D2 | Remote agent trigger | fork-cc `RemoteTriggerTool`, hermes `cronjob(deliver)` (2) | ✗ gap |

#### T2-E: Subagent and Multi-Agent Patterns

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-E1 | Toolset-parameterized delegation | hermes `delegate_task(toolsets=[…])` (1, strong) | ✗ gap — delegation agents have hard-coded tool lists |
| T2-E2 | Parallel batch subagent dispatch | hermes `tasks=[…]`, codex `spawn_agents_on_csv` (2) | ✗ gap |
| T2-E3 | Tracked task create/update/output | fork-cc `Task*`, codex `assign_task`/`wait_agent` (2) | partial — `task_*` covers status/cancel/list |
| T2-E4 | Agent-to-agent messaging | fork-cc `SendMessageTool`, codex `send_message` (2) | ✗ gap |

#### T2-F: Planning Mode

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-F1 | Enter plan mode | fork-cc, codex `update_plan` (2) | ✗ gap |
| T2-F2 | Exit plan mode | fork-cc, opencode `PlanExitTool` (2) | ✗ gap |

#### T2-G: MCP Resources

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-G1 | List MCP resources | fork-cc `ListMcpResourcesTool`, codex (2) | ✗ gap |
| T2-G2 | Read MCP resource | fork-cc `ReadMcpResourceTool`, codex (2) | ✗ gap |

#### T2-H: Vision

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-H1 | Image analysis / vision (URL or path) | hermes `vision_analyze`, codex `view_image` (2) | ✗ gap |
| T2-H2 | Image generation | hermes `image_generate`, codex `ImageGeneration` (2) | ✗ gap |

#### T2-I: Code Intelligence

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-I1 | Language Server Protocol integration | fork-cc `LSPTool`, opencode `LspTool` (2) | ✗ gap |

#### T2-J: Web Ergonomics

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-J1 | Parallel multi-URL fetch | hermes `web_extract(urls=[…])` (1, strong case) | ✗ gap — `web_fetch` accepts one URL |
| T2-J2 | Code-specific search | opencode `CodeSearchTool`, codex `tool_search` (2) | ✗ gap |

**co-cli Tier 2 coverage: 3 / ~22 capabilities fully covered.**  
Significant gaps: skills model-callability (T2-B), stdin write to running process (T2-C2), scheduling (T2-D), toolset-parameterized delegation (T2-E1), parallel delegation (T2-E2), planning mode (T2-F), MCP resource tools (T2-G), vision (T2-H1), LSP (T2-I1), multi-URL fetch (T2-J1).

---

### Tier 3 — Extensions / Integrations

**Definition:** Present in 1–2 peers; specialized, domain-bound, or requiring
significant external infrastructure.

#### T3-A: Browser Automation (hermes only)

Full browser stack: `browser_navigate`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_scroll`, `browser_back`, `browser_press`,
`browser_get_images`, `browser_console`, `browser_vision`.  
Requires: Camofox / Browserbase / Firecrawl backend. 10+ tools, large dependency surface.  
co-cli gap: all 10 tools. Port requires a browser backend commitment.

#### T3-B: Cross-Platform Messaging (fork-cc + hermes)

| Tool | Peer |
|---|---|
| `send_message` (Telegram, Discord, Slack, Signal, Matrix, SMS) | hermes |
| `SendMessageTool` (agent-to-user or agent-to-agent) | fork-cc |

co-cli gap: no messaging delivery. fork-cc's `SendMessageTool` is narrower (agent-to-user notification); hermes's is full platform routing.

#### T3-C: Code / REPL Sandboxes (hermes + codex)

| Tool | Peer |
|---|---|
| `execute_code` (Python sandbox with hermes_tools import; multi-environment backends: daytona, docker, local, modal, singularity, SSH) | hermes |
| `js_repl` / `js_repl_reset` | codex |

co-cli has `code_execute` but runs on host with shell-policy gate only — no sandboxed multi-environment backend.

#### T3-D: Text-to-Speech (hermes only)

`text_to_speech` with `MEDIA:` path delivery. Platform-specific (Telegram voice bubble, Discord audio attachment, CLI file). No analogous co-cli workflow.

#### T3-E: Multi-Model Reasoning (hermes only)

`mixture_of_agents`: 4 frontier LLMs + aggregator, max reasoning effort, 5 API calls per invoke.  
co-cli covers similar use cases via `reason` subagent; not a tool-level gap.

#### T3-F: Smart Home (hermes only)

`ha_list_entities`, `ha_get_state`, `ha_list_services`, `ha_call_service`.  
Domain-specific (Home Assistant). co-cli's equivalent domain integration is Google Workspace.

#### T3-G: Reinforcement Learning Training (hermes only)

10 `rl_*` tools covering environment selection, config, training lifecycle, WandB metrics, inference testing.  
Highly domain-specific (Tinker-Atropos).

#### T3-H: Git Worktree Management (fork-cc only)

`EnterWorktreeTool`, `ExitWorktreeTool`. Co-cli's orchestrate-dev skill uses worktrees via bash commands.

#### T3-I: Config and Project Brief (fork-cc only)

`ConfigTool` (read/write Claude Code settings), `BriefTool` (read CLAUDE.md).  
co-cli equivalent: user reads CLAUDE.md directly; no model-callable config surface.

#### T3-J: Tool Discovery by Model (codex)

`tool_search` (search available tools by query), `tool_suggest` (suggest tools for a task), `ToolSearch` (deferred model-native tool discovery). co-cli has `capabilities_check` which exposes the runtime tool surface but does not support query-based tool lookup.

#### T3-K: Notebook Editing (fork-cc only)

`NotebookEditTool` for Jupyter notebook cell editing. No co-cli equivalent.

#### T3-L: Platform-Specific Shell (fork-cc only)

`PowerShellTool`. macOS/Linux-only co-cli has no Windows shell surface.

#### T3-M: co-cli-Unique Extensions (no peer equivalent)

| Capability | co-cli tool(s) |
|---|---|
| T2 artifact inventory (paginated) | `memory_list` |
| T2 artifact full read by slug | `memory_read` |
| T2 artifact CRUD (create / modify) | `memory_create`, `memory_modify` |
| FTS5 BM25 knowledge search | `memory_search` (T2 tier) |
| Runtime capability introspection | `capabilities_check` |
| Obsidian vault integration | `obsidian_list`, `obsidian_search`, `obsidian_read` |
| Google Drive | `drive_search`, `drive_read` |
| Google Gmail | `gmail_list`, `gmail_search`, `gmail_draft` |
| Google Calendar | `calendar_list`, `calendar_search` |

---

## Part 4: Tool Lifecycle Architecture Comparison

How each system handles the meta-layer: registration, execution hooks, approval, result sizing, concurrency, and extensibility.

| Aspect | fork-cc | hermes | opencode | codex | co-cli |
|---|---|---|---|---|---|
| **Registration** | Manual list in `getAllBaseTools()` | AST auto-discovery of `registry.register()` | `ToolRegistry.all()` + dynamic repo-local + plugin | `ToolRegistryBuilder.push_spec()` manual | Manual `NATIVE_TOOLS` tuple + `@agent_tool` decorator — decorator-only path is silently omitted |
| **Runtime availability gate** | `checkPermissions()` per tool | `check_fn` per tool (runtime) | Plugin `tool.definition` hook | `is_mutating()` / `turn.tool_call_gate` | Build-time `requires_config` only — credential expiry not caught mid-session |
| **Pre/post-tool hooks** | `runPreToolUseHooks` / `runPostToolUseHooks` / `runPostToolUseFailureHooks` | — (gateway pattern) | `tool.execute.before` / `tool.execute.after` | `run_pre_tool_use_hooks` / `run_post_tool_use_hooks` | `CoToolLifecycle.before_tool_execute` / `after_tool_execute` ✓ |
| **Approval model** | Multi-source rules; named modes; persisted deny/allow | Blocking gateway; `_permanent_approved` persisted | `Permission.ask()` / `.reply()`; `"always"` persists | `request_permissions`; `SandboxPermissions` | Deferred approval loop; semantic `ApprovalSubject` (shell/path/domain/tool); session-scoped only |
| **Concurrency partitioning** | `partitionToolCalls()` by `isConcurrencySafe` | — (toolset profiles) | Plugin hooks | `turn.tool_call_gate` for mutating | `ResourceLockStore` per-path; no class-level concurrency gate |
| **Result size** | `maxResultSizeChars` per tool | `max_result_size_chars` uniform (all tools incl. MCP) | `Truncate.output()` wrapper | `truncated_output()` token-based | Per-tool `max_result_size` via `@agent_tool`; MCP results ungated — runaway MCP can flood context |
| **MCP integration** | `ListMcpResourcesTool`, `ReadMcpResourceTool`, `McpAuthTool`; dynamic refresh | `mcp_tool.py` with deregister/re-register on refresh | — | `mcp_tool_to_responses_api_tool()`; `list_tools()` with timeout (implied) | `discover_mcp_tools()` once at startup; no timeout wrapper; no `list_tools` timeout; no dynamic refresh |
| **Toolset profiles / grouping** | — | `toolsets.py` named profiles (`web`, `file`, …) | Plugin tool.definition hook | `model_visible_specs` filtering | No named profiles; delegation agents have explicit hard-coded tool lists |
| **Tool context** | — | — | `Tool.Context` (sessionID, messageID, agent, abort, callID, messages, metadata(), ask()) | `ToolInvocation` (session, turn, tracker, call_id, tool_name, namespace, payload) | `RunContext[CoDeps]` passed to tool body |
| **Typed output** | `mapToolResultToToolResultBlockParam` | — | `Truncate.output()` wrapper | `ToolOutput` trait; `FunctionToolOutput`; `ExecCommandToolOutput` | `ToolReturn(return_value, metadata)` — string only; no structured content block |

---

## Part 5: co-cli Gap Priority

Derived from convergence scores, parity matrix (§1 of `RESEARCH-tools-gaps-co-vs-hermes.md`), and architecture analysis (§§3-4 of same).

### Priority High

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| MCP `list_tools()` no timeout at startup (§3.8 gaps doc) | Architecture flaw | Startup hang if MCP server stalls | Low — `asyncio.timeout()` + `asyncio.gather()` |
| `tool_output_raw()` bypasses size gate and telemetry (§4.2 gaps doc) | Architecture flaw | Silent context overflow | Medium — audit callsites, restrict to ctx-less helpers |
| MCP tool results not size-gated (§3.7 gaps doc) | Architecture flaw | Context overflow via runaway MCP | Medium — extend `CoToolLifecycle.after_tool_execute` to MCP spans |
| `web_fetch` single-URL only | hermes `web_extract(urls=[…])` — score 1, high value | Sequential latency on multi-URL research | Low — `asyncio.gather` over existing fetch |
| `ModelRetry` vs `tool_error()` unenforced (§4.3 gaps doc) | Architecture flaw | Retry-budget exhaustion on non-recoverable errors | Medium — ruff rule or base-class signal |

### Priority Medium

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| Model-callable `skills_list` + `skill_view` (T2-B1, T2-B2) | hermes + opencode — score 2 | Skill discovery requires user slash commands; model cannot self-load skills | Medium — read-only tools over existing `co_cli/skills/` registry |
| Runtime `check_fn` for tool availability (§3.1 gaps doc) | hermes pattern | Stale credentials look like transient failures | Medium — optional `check_fn` field on `@agent_tool` + visibility filter hook |
| `vision_analyze` tool (T2-H1) | hermes + codex — score 2 | No vision capability | Medium — pydantic-ai model wrapper already available |
| `task_start` / `shell`: stdin write to running process (T2-C2) | hermes + codex — score 2 | Cannot send input to interactive background processes | Medium — extend `task_*` or add `process(action=write/submit/close)` |
| Background task output file-backed (T2-C5) | hermes + codex — score 2 | In-memory `deque(500)` drops output; crash loses all | Medium — optional file sink on task spawn |
| `NATIVE_TOOLS` manual tuple (§3.3 gaps doc) | hermes AST auto-discovery | Tool silently omitted if decorator without tuple entry | Low — module scan for `@agent_tool`-decorated functions at import |
| `shell` PTY mode (T2-C4) | hermes score 1, high value | Cannot run interactive CLIs (Codex, Python REPL, etc.) | Medium — add `pty` flag to `shell` |
| Named toolset profiles for delegation (§3.5 gaps doc, T2-E1) | hermes — score 1, strong | New tools invisible to delegation agents; manual list drift | Medium — `toolsets.py`-style registry |

### Priority Low

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| `terminal.watch_patterns` for background tasks (T2-C3) | hermes — score 1 | Long-running tasks cannot notify on specific output | Medium — extend `task_start` with regex notifier |
| MCP dynamic tool refresh (§3.2 gaps doc) | hermes — score 1 | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed` |
| MCP resource tools: list + read (T2-G1, T2-G2) | fork-cc + codex — score 2 | Cannot access MCP server resources (only tools) | Medium — two new tools over existing MCP client |
| `clarify` one-shot fragility (§4.1 gaps doc) | Architecture flaw | Model confusion if called twice in one step | Hard — approval-loop dedup |
| `file_read_mtimes` unbounded (§4.4 gaps doc) | Architecture flaw | Memory growth in very long sessions | Low — cap dict or evict at turn reset |
| Session search `role_filter` (T2-A3) | hermes — score 1 | Cannot filter assistant vs. user messages in recall | Low — extend T1 tier query |
| Parallel batch delegation (T2-E2) | hermes + codex — score 2 | Sequential research; no batch fan-out | Medium — `delegate_task(tasks=[…])` pattern |
| Planning mode tools (T2-F1, T2-F2) | fork-cc + opencode — score 2 | No model-gated plan mode | Large — requires new REPL mode + tool pair |
| LSP integration (T2-I1) | fork-cc + opencode — score 2 | No IDE-class code intelligence | Large — requires LSP client infrastructure |

### Out of Scope (hermes-tier-3 tools not worth porting)

| Capability | Reason |
|---|---|
| Browser automation (T3-A, 10 tools) | Large browser stack dependency; `web_fetch` covers most read-only needs |
| Text-to-speech (T3-D) | No co-cli delivery channel for audio |
| Home Assistant (T3-F, 4 tools) | Domain-specific; not in co-cli's use case scope |
| Reinforcement learning training (T3-G, 10 tools) | Highly domain-specific (Tinker-Atropos only) |
| Notebook editing (T3-K) | No Jupyter infrastructure |
| Cron / scheduling (T2-D) | co-cli is user-interactive; OS cron or Claude Code `CronCreate` is better suited |
| `mixture_of_agents` (T3-E) | `reason` subagent covers the use case without the 4-LLM cost |
| Image generation (T2-H2) | Niche; direct Google/OpenAI API use is simpler |
| Cross-platform messaging (T3-B) | Large auth/config surface; no current co-cli delivery workflow |

---

## Appendix: Tool Name Mapping Across Peers

Quick lookup for finding the equivalent tool across systems.

| Capability | co-cli | fork-cc | hermes | opencode | codex |
|---|---|---|---|---|---|
| Shell exec | `shell` | `BashTool` | `terminal` | `bash` | `exec_command` |
| File read | `file_read` | `ReadTool` | `read_file` | `read` | — |
| File write | `file_write` | `WriteTool` | `write_file` | `write` | — |
| File edit | `file_patch` | `EditTool` | `patch(replace)` | `edit` | `apply_patch` |
| Multi-hunk patch | `file_patch(_v4a)` | — | `patch(patch)` | `ApplyPatchTool` | `apply_patch` |
| File find | `file_find` | `GlobTool` | `search_files(files)` | `glob` | — |
| Content search | `file_search` | `GrepTool` | `search_files(content)` | `grep` | — |
| Web search | `web_search` | `WebSearchTool` | `web_search` | `search` | `WebSearch` |
| Web fetch | `web_fetch` | `WebFetchTool` | `web_extract` | `fetch` | — |
| Todo list | `todo_write/read` | `TodoWriteTool` | `todo` | `todo` | — |
| Clarify / ask user | `clarify` | `AskUserQuestion` | `clarify` | `question` | `request_user_input` |
| Delegate / subagent | — (no model-callable primitive) | `AgentTool` | `delegate_task` | `task` | `spawn_agent` |
| Skills load | — | — | `skill_view` | `skill` | — |
| Skills list | — | — | `skills_list` | `skill` (dynamic desc) | — |
| Background task start | `task_start` | — | `terminal(background=True)` | — | `code` / `wait` |
| Background task status | `task_status` | `TaskGetTool` | `process(poll)` | — | `wait_agent` |
| Memory search | `memory_search` | — | `session_search` | — | — |
| Memory create | `memory_create` | — | `memory(add)` | — | — |
| Vision | — | — | `vision_analyze` | — | `view_image` |
| LSP | — | `LSPTool` | — | `lsp` | — |
| Plan enter | — | `EnterPlanMode` | — | — | `update_plan` |
| Plan exit | — | `ExitPlanModeV2` | — | `plan` | — |
| Cron create | — | `CronCreateTool` | `cronjob(create)` | — | — |
| MCP resource read | — | `ReadMcpResourceTool` | — | — | `read_mcp_resource` |
| Capabilities check | `capabilities_check` | — | — | — | `tool_search` / `tool_suggest` |
| Obsidian | `obsidian_*` | — | — | — | — |
| Google Workspace | `drive_*`, `gmail_*`, `calendar_*` | — | — | — | — |
