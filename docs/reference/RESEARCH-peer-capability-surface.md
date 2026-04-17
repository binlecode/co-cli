# RESEARCH: Peer Capability Surface — Five-Way Comparison

Sources: `~/workspace_genai/fork-claude-code`, `~/workspace_genai/gemini-cli`, `~/workspace_genai/codex`, `~/workspace_genai/opencode`, co-cli codebase
Scan date: 2026-04-05

## 1. Tool Inventory

### fork-cc (`tools.ts` → `getAllBaseTools()`)

19 always-available + 38+ feature-gated tools.

**Always-available (19):**
| Category | Tools | Source |
|----------|-------|--------|
| File I/O | `Read`, `Edit`, `Write`, `Glob`, `Grep`, `NotebookEdit` | `/tools/{FileReadTool,FileEditTool,FileWriteTool,GlobTool,GrepTool,NotebookEditTool}/` |
| Shell | `Bash` | `/tools/BashTool/` |
| Web | `WebFetch`, `WebSearch` | `/tools/{WebFetchTool,WebSearchTool}/` |
| Delegation | `Agent`, `Skill`, `SendMessage` | `/tools/{AgentTool,SkillTool,SendMessageTool}/` |
| Task control | `TaskStop`, `TodoWrite` | `/tools/{TaskStopTool,TodoWriteTool}/` |
| User interaction | `AskUserQuestion` | `/tools/AskUserQuestionTool/` |
| Plan mode | `EnterPlanMode`, `ExitPlanMode` | `/tools/{EnterPlanModeTool,ExitPlanModeTool}/` |
| Output | `TaskOutput`, `SendUserMessage`, `StructuredOutput` | `/tools/{TaskOutputTool,BriefTool,SyntheticOutputTool}/` |
| MCP resources | `ListMcpResourcesTool`, `ReadMcpResourceTool` | `/tools/{ListMcpResourcesTool,ReadMcpResourceTool}/` |

**Feature-gated (notable):**
| Tool | Gate | Source |
|------|------|--------|
| `ToolSearch` | `TOOL_SEARCH_ENABLED` | `/tools/ToolSearchTool/` |
| `TeamCreate`, `TeamDelete` | `AGENT_SWARMS` | `/tools/{TeamCreateTool,TeamDeleteTool}/` |
| `EnterWorktree`, `ExitWorktree` | `WORKTREE_MODE` | `/tools/{EnterWorktreeTool,ExitWorktreeTool}/` |
| `CronCreate`, `CronDelete`, `CronList` | `AGENT_TRIGGERS` | `/tools/ScheduleCronTool/` |
| `RemoteTrigger` | `AGENT_TRIGGERS_REMOTE` | `/tools/RemoteTriggerTool/` |
| `TaskCreate`, `TaskGet`, `TaskUpdate`, `TaskList` | `TODO_V2` | `/tools/{TaskCreateTool,TaskGetTool,TaskUpdateTool,TaskListTool}/` |
| `LSP` | `ENABLE_LSP_TOOL` env | `/tools/LSPTool/` |
| `WebBrowserTool` | `WEB_BROWSER_TOOL` | Lazy-loaded |
| `Sleep` | `PROACTIVE`/`KAIROS` | `/tools/SleepTool/` |
| `Config` | `USER_TYPE=ant` | `/tools/ConfigTool/` |
| `REPL` | `USER_TYPE=ant` | `/tools/REPLTool/` |

### gemini-cli (`packages/core/src/tools/`)

27 tools registered via `ToolRegistry` in `config.ts:3437–3534`.

| Category | Tools | Source |
|----------|-------|--------|
| File I/O | `read_file`, `read_many_files`, `write_file`, `replace` (edit), `glob`, `list_directory` | `/tools/{read-file,read-many-files,write-file,edit,glob,ls}.ts` |
| Search | `grep_search` (or `grep_search_ripgrep`), `google_web_search`, `web_fetch` | `/tools/{grep,ripGrep,web-search,web-fetch}.ts` |
| Shell | `run_shell_command` | `/tools/shell.ts` |
| Memory | `save_memory`, `update_topic` | `/tools/{memoryTool,topicTool}.ts` |
| User interaction | `ask_user` | `/tools/ask-user.ts` |
| Skills | `activate_skill` | `/tools/activate-skill.ts` |
| Docs | `get_internal_docs` | `/tools/get-internal-docs.ts` |
| Plan mode | `enter_plan_mode`, `exit_plan_mode` | `/tools/{enter-plan-mode,exit-plan-mode}.ts` |
| Task tracker | `tracker_create_task`, `tracker_update_task`, `tracker_get_task`, `tracker_list_tasks`, `tracker_add_dependency`, `tracker_visualize` | `/tools/trackerTools.ts` (gated: `isTrackerEnabled()`) |
| Todo | `write_todos` | `/tools/write-todos.ts` (gated: `getUseWriteTodos()`) |

### codex (`codex-rs/core/src/tools/spec.rs:480–940`)

30+ tools across native + multi-agent + MCP.

| Category | Tools | Source |
|----------|-------|--------|
| Shell/exec | `shell` (aliases: `container.exec`, `local_shell`), `exec_command`, `write_stdin`, `shell_command` | `/codex-rs/tools/src/lib.rs` |
| File I/O | `apply_patch` (freeform or JSON), `list_dir` (experimental), `view_image` | `/codex-rs/core/src/tools/handlers/{patch,list_dir,view_image}.rs` |
| Code mode | `public` (code mode execute), `wait` (yield), `js_repl`, `js_repl_reset` | `/codex-rs/core/src/tools/code_mode/` |
| Web | `web_search` (built-in Responses API), `image_generation` | `ToolSpec::WebSearch`, `ToolSpec::ImageGeneration` |
| Planning | `update_plan` | `/codex-rs/core/src/tools/handlers/plan.rs` |
| User interaction | `request_user_input`, `request_permissions` | `/codex-rs/core/src/tools/handlers/{user_input,network_approval}.rs` |
| Tool discovery | `tool_search` (BM25 FTS), `tool_suggest` (semantic) | `/codex-rs/core/src/tools/handlers/{tool_search,tool_suggest}.rs` |
| Multi-agent v2 | `spawn_agent`, `send_message`, `assign_task`, `wait_agent`, `close_agent`, `list_agents` | `/codex-rs/core/src/tools/handlers/multi_agents_v2/` |
| Batch | `spawn_agents_on_csv`, `report_agent_job_result` | `/codex-rs/core/src/tools/handlers/agent_jobs.rs` |
| MCP resources | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` | `/codex-rs/core/src/tools/handlers/mcp_resource.rs` |

### opencode (`packages/opencode/src/tool/registry.ts:118–138`)

18+ built-in tools + plugin-loaded custom tools.

| Category | Tools | Source |
|----------|-------|--------|
| File I/O | `read`, `write`, `edit`, `multiedit`, `glob`, `list` (ls), `apply_patch` (GPT only, mutually exclusive with edit/write) | `/tool/{read,write,edit,multiedit,glob,ls,apply_patch}.ts` |
| Search | `grep`, `codesearch` (opencode provider or Exa), `websearch` (opencode/Exa) | `/tool/{grep,codesearch,websearch}.ts` |
| Shell | `bash` | `/tool/bash.ts` |
| Web | `webfetch` | `/tool/webfetch.ts` |
| Task/todo | `task`, `todowrite` | `/tool/{task,todo}.ts` |
| User interaction | `question` (app/cli/desktop only) | `/tool/question.ts` |
| Skills | `skill` | `/tool/skill.ts` |
| LSP | `lsp` (experimental: 9 operations) | `/tool/lsp.ts` |
| Plan mode | `plan_exit`, `plan_enter` | `/tool/plan.ts` |
| Batch | `batch` (experimental) | `/tool/batch.ts` |
| Error | `invalid` (placeholder) | `/tool/invalid.ts` |

### co-cli (`co_cli/tools/`, `agent.py:89`)

39 tools across 15 tool files.

| Category | Tools | Source |
|----------|-------|--------|
| File I/O | `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file` | `tools/files.py` |
| Shell | `run_shell_command` | `tools/shell.py` |
| Web | `web_search` (Brave), `web_fetch` | `tools/web.py` |
| Memory | `save_memory`, `recall_memory`, `search_memory`, `list_memories`, `update_memory`, `append_memory` | `tools/memory.py` |
| Knowledge | `search_knowledge`, `save_article`, `search_articles`, `read_article` | `tools/articles.py` |
| Subagents | `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent` | `tools/subagent.py` |
| Task control | `start_background_task`, `check_task_status`, `cancel_background_task`, `list_background_tasks` | `tools/task_control.py` |
| Todo | `write_todos`, `read_todos` | `tools/todo.py` |
| Capabilities | `check_capabilities` | `tools/capabilities.py` |
| Tool discovery | `search_tools` | `tools/tool_search.py` |
| Obsidian | `search_notes`, `list_notes`, `read_note` | `tools/obsidian.py` |
| Google Drive | `search_drive_files`, `read_drive_file` | `tools/google_drive.py` |
| Google Calendar | `list_calendar_events`, `search_calendar_events` | `tools/google_calendar.py` |
| Google Gmail | `list_gmail_emails`, `search_gmail_emails`, `create_gmail_draft` | `tools/google_gmail.py` |

---

## 2. MCP Integration

| Aspect | fork-cc | gemini-cli | codex | opencode | co-cli |
|--------|---------|-----------|-------|----------|--------|
| **Server types** | stdio, sse, sse-ide, http, ws, ws-ide, sdk, claudeai-proxy (`services/mcp/types.ts`) | stdio, sse, http (`McpClient` in `mcp-client.ts`) | stdio, http (via `RmcpClient` in `rmcp-client/src/`) | stdio, remote http/sse with OAuth (`mcp/index.ts`) | stdio, sse, streamable-http via pydantic-ai (`agent.py:58–84`) |
| **Config scope** | local (`.mcp.json`), user (`~/.config/mcp.json`), project, dynamic, enterprise, claudeai, managed | `settings.json` `mcpServers` | Config-driven + dynamic discovery | `config.mcp.{name}` with type/url/command | `settings.json` `mcp_servers` per name |
| **Tool naming** | `mcp__<server>__<tool>`. Built-ins win dedup via `assembleToolPool()` | `mcp_<serverName>_<toolName>` prefix | `{namespace}:{tool_name}` or bare | `{sanitized_client}_{sanitized_tool}` | `<prefix>_<tool>` via pydantic-ai `tool_prefix` |
| **Resource support** | `ListMcpResourcesTool`, `ReadMcpResourceTool` | Via `McpClient` discovery | `list_mcp_resources`, `list_mcp_resource_templates`, `read_mcp_resource` | `readResource(clientName, uri)` | Not observed |
| **OAuth** | Per-server OAuth + `McpAuthTool` pseudo-tool | Per-server auth | `perform_oauth_login()`, `determine_streamable_http_auth_status()` | OAuth with CSRF, browser callback | Not observed |

---

## 3. Slash Commands

| System | Count | Source | Notable commands |
|--------|-------|--------|-----------------|
| **fork-cc** | 60+ (20 core + 40+ feature-gated/internal) | `commands.ts:258+`, `commands/*/` | `/compact`, `/fork`, `/agents`, `/skills`, `/mcp`, `/review`, `/security-review`, `/voice`, `/workflows`, `/buddy` |
| **gemini-cli** | 30+ | `packages/cli/src/ui/constants/tips.ts` | `/compress`, `/memory`, `/directory`, `/tools`, `/extensions`, `/mcp`, `/settings`, `/model`, `/resume`, `/restore` |
| **codex** | 40+ (interactive) + 18 CLI subcommands | `tui/src/slash_command.rs`, `cli/src/main.rs` | `/compact`, `/model`, `/skills`, `/mcp`, `/review`, `/fork`, `/plan`, `/agent`, `/diff`, `/realtime` |
| **opencode** | 3 built-in + dynamic (MCP prompts, skills, config) | `command/index.ts:86–104` | `/init`, `/review`, user-defined via `cfg.command.*` |
| **co-cli** | 15 built-in + dynamic skills | `commands/_commands.py` BUILTIN_COMMANDS | `/compact`, `/help`, `/status`, `/tools`, `/skills`, `/approvals`, `/new`, `/resume`, `/sessions`, `/forget`, `/background`, `/tasks`, `/cancel`, `/history`, `/clear` |

---

## 4. Subagent / Delegation

| Aspect | fork-cc | gemini-cli | codex | opencode | co-cli |
|--------|---------|-----------|-------|----------|--------|
| **Agent types** | Built-in: `Explore`, `Plan`, `GeneralPurpose`, `ClaudeCodeGuide`, `Verification`. User-defined: `~/.claude/agents/`. Fork: implicit via `FORK_SUBAGENT` | `GeneralistAgent`, `CliHelpAgent`, `MemoryManagerAgent`, `CodebaseInvestigator` | Multi-agent v2: `spawn_agent` → `send_message` / `assign_task` / `wait_agent` / `close_agent` / `list_agents`. Batch: `spawn_agents_on_csv` | `build` (primary), `plan`, `general` (subagent), `explore` (subagent), `compaction`, `title`, `summary` (hidden). Config-defined | `run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent` (`tools/subagent.py`) |
| **Inter-agent comms** | `SendMessage` tool (named agents) | A2A protocol (`a2a-client-manager.ts`) + local invocation | `send_message` tool (v2), `send_input` (v1) | Not observed | Not observed |
| **Background exec** | `run_in_background` flag on Agent tool | Not observed | Parallel execution via `ToolCallRuntime` | Not observed | `start_background_task` tool → `BackgroundTaskState` on `CoSessionState` |
| **Worktree isolation** | `EnterWorktree`/`ExitWorktree` (gated: `WORKTREE_MODE`) | Not observed | Sandbox isolation per session | Not observed | Not observed |

---

## 5. Skill / Prompt Overlay System

| Aspect | fork-cc | gemini-cli | codex | opencode | co-cli |
|--------|---------|-----------|-------|----------|--------|
| **Format** | TypeScript skill files in `/skills/bundled/`. User skills from `~/.config/claude-code/skills/` or project `.claude/skills/` | `GEMINI.md` per project + `activate_skill` tool for runtime injection | Markdown `AGENTS.md` files (`/init` command creates) | `SKILL.md` files with YAML frontmatter. Global (`~/.claude/skills/`), project (`.claude/skills/`), config dirs, remote URLs | Markdown `SKILL.md` files with YAML frontmatter. Project `.co-cli/skills/`, user `~/.config/co-cli/skills/` |
| **Count** | 17 bundled + user-defined | N/A (doc-based) | N/A (doc-based) | Dynamic via file discovery | Dynamic via file discovery |
| **Invocation** | `/skill-name` via `SkillTool` | `/memory`, `activate_skill` tool | `/skills` command | `/skill-name` via `skill` tool | `/skill-name` via `Skill` tool (agent dispatches) |
| **Discovery** | Bundled → built-in plugins → skill dir → plugin skills → workflow scripts | Project `GEMINI.md` loaded at session start | `AGENTS.md` loaded at session start | Global → project → config dirs → remote URLs | Project → user dir. `search_tools` returns skill hints |

---

## 6. Capability Discovery

| Aspect | fork-cc | gemini-cli | codex | opencode | co-cli |
|--------|---------|-----------|-------|----------|--------|
| **Deferred tools** | `defer_loading` flag on tool. `ToolSearchTool` discovers. `assembleToolPool()` sorts for cache stability | `discoverAndRegisterToolsFromCommand()` — external discovery command. MCP discovery via `McpClientManager` | `defer_loading: Option<bool>` on `ResponsesApiTool`. `tool_search` (BM25) + `tool_suggest` (semantic) | Plugin tools + MCP tools loaded dynamically. No explicit deferred flag | `should_defer` on `ToolConfig`. `search_tools` discovers. `discovered_tools` tracked on `CoSessionState` |
| **Model awareness** | System prompt schema + deferred tool listing | Tool schemas via `getFunctionDeclarations()`. Feature-gated tools conditional | `create_tools_json_for_responses_api()` serializes visible tools | `ProviderTransform.schema()` converts per provider | pydantic-ai auto-generates schema. `add_deferred_tool_prompt()` lists deferred tools in system prompt |

---

## 7. External Integrations

| Integration | fork-cc | gemini-cli | codex | opencode | co-cli |
|-------------|---------|-----------|-------|----------|--------|
| **Web search** | `WebSearch` (API) | `google_web_search` (Gemini grounding) | `web_search` (Responses API) | `websearch` (Exa/opencode) | `web_search` (Brave API) |
| **Web fetch** | `WebFetch` | `web_fetch` | Via shell | `webfetch` | `web_fetch` |
| **Git** | Via `Bash` | Via `run_shell_command` | Via shell tools | Via `bash` | Via `run_shell_command` |
| **LSP** | `LSP` (gated) | Not observed | Not observed | `lsp` (experimental, 9 ops) | Not observed |
| **Image gen** | Not observed | Not observed | `image_generation` (Responses API) | Not observed | Not observed |
| **Browser** | `WebBrowserTool` (gated) | Playwright via `packages/core/src/agents/browser/` | Not observed | Not observed | Not observed |
| **Google services** | Not observed | Google Search via Gemini API | Not observed | Not observed | Google Drive, Calendar, Gmail (`tools/google_*.py`) |
| **Obsidian** | Not observed | Not observed | Not observed | Not observed | `search_notes`, `list_notes`, `read_note` (`tools/obsidian.py`) |
| **MCP** | 8 transport types | stdio/sse/http | stdio/http + MCP server mode | stdio/http/sse + OAuth | stdio/sse/streamable-http |
| **Scheduling** | `CronCreate`/`CronDelete`/`CronList`, `RemoteTrigger` (gated) | Not observed | Not observed | Not observed | Not observed |
