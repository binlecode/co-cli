# RESEARCH: Tool Tiering Across Peers — hermes · openclaw · opencode · codex

Cross-peer synthesis of the `RESEARCH-tools-*.md` files plus
`RESEARCH-tools-gaps-co-vs-hermes.md` and a direct survey of the openclaw repo.
Establishes a three-tier classification of tool capabilities based on
convergence frequency across peers, with full per-peer inventories, lifecycle
architecture comparison, co-cli coverage mapping, and prioritized gap analysis.

**Peers reviewed:** hermes-agent, openclaw, opencode, codex.  
**Reference system:** co-cli (~35 native tools as of v0.8.x).

**Sources:**
- `RESEARCH-tools-hermes-agent.md` — hermes tool registry
- `RESEARCH-tools-opencode.md` — opencode built-in registry
- `RESEARCH-tools-codex.md` — codex Rust tool spec
- `RESEARCH-tools-gaps-co-vs-hermes.md` — per-tool parity + architecture gaps
- `~/workspace_genai/openclaw/src/agents/openclaw-tools.ts` — openclaw factory registry (surveyed directly; no standalone research file)

**Tiering method:** Each capability is scored by how many of the four peers
implement it as a native first-class tool. **Tier 1 = 3–4 peers** (universal /
near-universal), **Tier 2 = 2 peers** (converged capability), **Tier 3 = 1 peer**
(specialized / differentiated). A capability covered only via a peer's harness
or API surface (not a discrete tool) is marked "(via API)" and counts as a soft
presence.

---

## Part 1: Per-Peer Tool Inventories

### 1.1 hermes-agent

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

### 1.2 openclaw

Source: `openclaw/src/agents/openclaw-tools.ts` (factory composition) +
`openclaw-tools.registration.ts` + `src/agents/tools/` (24 implementation files).

#### Session coordination and lifecycle

| Tool | Key parameters / behavior |
|---|---|
| `sessions_spawn` | Spawn subagents; async nested agent execution with workspace inheritance; `runtime` selects identity |
| `sessions_send` | Send messages to other sessions; inter-session (agent-to-agent) communication |
| `sessions_list` | Query active / historical sessions; filter by kind, agent, status |
| `sessions_history` | Fetch transcript history for a session with message filtering |
| `session_status` | Query run status, model info, loop-detection state |
| `sessions_yield` | End current turn; collect subagent results asynchronously |
| `subagents` | List available subagent runtime identities for spawn |
| `agents_list` | List available agent IDs for `sessions_spawn runtime="subagent"`, with scope restrictions |

#### Message and interaction

| Tool | Key parameters / behavior |
|---|---|
| `message` | Send / receive / edit / react across Slack, Discord, Teams, Telegram, iMessage, Matrix; thread routing; channel-capability gated |
| `heartbeat` | Record structured heartbeat outcomes (done / failed / check-back); notify preference; priority |

#### Web

| Tool | Key parameters / behavior |
|---|---|
| `web_search` | Brave / Perplexity / DuckDuckGo / Jina; domain filters, date ranges, country / language |
| `web_fetch` | Fetch URL as markdown / text; readability extraction; caching; 750KB default / 10MB cap |

#### Process and scheduling

| Tool | Key parameters / behavior |
|---|---|
| `cron` | Manage Gateway cron jobs: reminders, delayed follow-ups, recurring work; no emulation via exec/polling |
| `nodes` | Execute shell commands / scripts; run hosted HTTP servers; invoke media-returning commands; workspace isolation |

#### Media generation and understanding (optional, auth/config-gated)

| Tool | Key parameters / behavior |
|---|---|
| `image_generate` | Generate images (DALL-E, Flux, …); requires auth + agent directory |
| `image` | Describe / analyze images; vision LLM call; path / URL / base64 input; up to 20 images |
| `video_generate` | Generate videos; provider-specific (e.g. Runway) |
| `music_generate` | Generate audio / music |
| `pdf` | Understand PDF documents; multimodal extraction |
| `tts` | Synthesize text to speech; provider selection; voice-list discovery |

#### Planning and gateway

| Tool | Key parameters / behavior |
|---|---|
| `update_plan` | Update exec-plan (Codex strict-agentic mode); write back structured tasks |
| `gateway` | Internal gateway calls for late-bound plugin tool binding; not exposed to normal agents |

**openclaw total: ~22 native tools (5 media tools auth/config-gated) + plugin tools + per-session MCP tools.**  
Note: openclaw exposes **no dedicated file read/write/edit/find/grep tools** — file work is done through the `nodes` shell tool.

#### openclaw Lifecycle Architecture

| Aspect | Detail |
|---|---|
| Registry | Factory composition: `createOpenClawTools()` instantiates each `createXxxTool()`; no manifest. Tools conditionally included by config flags, auth/capability checks, embedded mode, and allow/deny policy |
| Runtime availability | `availability.ts` expressions — `kind`: `always` / `auth` / `config` / `env` / `plugin-enabled` / `context`; `allOf` (AND) + `anyOf` (OR) combinations |
| Pre/post hooks | `before_tool_call` hook (`pi-tools.before-tool-call.ts`): plugin veto + param adjust, approval request, tool-loop detection, trusted-tool policy; post = diagnostic event emission |
| Approval model | Plugin approval `ALLOW_ONCE` / `ALLOW_ALWAYS` / `DENY` (120s + 10s timeout); config `allow` / `deny` / `alsoAllow`; `ownerOnly` tools restricted to owner senders |
| Concurrency | Per-call `AbortSignal`; no tool-level concurrency cap; one approval at a time per call |
| Result size | Per-tool caps (web_fetch 750KB/10MB, search 10 results, image 20, history 20 msgs); no uniform registry gate |
| MCP integration | Per-session MCP runtime created on demand, catalog materialized per session, names sanitized vs native tools, disposed after run |
| Toolset profiles | No explicit profiles; implicit groupings — embedded mode, sandboxed exec, media-capable, plugin-augmented, owner-only |
| Tool context | `HookContext`: `agentId`, `config`, `sessionKey`, `sessionId`, `runId`, `channelId`, `loopDetection`, `sandbox{root,bridge}`, `onToolOutcome` |
| Typed output | `AgentToolResult{content[], details}`; helpers `textResult` / `jsonResult` / `payloadTextResult`; no schema-driven output narrowing |

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

Score = number of peers (hermes, openclaw, opencode, codex) with a native first-class implementation.
co-cli column shows current coverage status.

### 2.1 File and Workspace Operations

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| File read (paginated) | read_file | — (via nodes) | read | (via API) | 2–3 | `file_read` ✓ |
| File write (full overwrite) | write_file | — (via nodes) | write | (via API) | 2–3 | `file_write` ✓ |
| File edit (string replace) | patch (replace mode) | — | edit | apply_patch | 3 | `file_patch` ✓ |
| Multi-hunk patch (V4A/unified diff) | patch (patch mode) | — | ApplyPatchTool | apply_patch | 3 | — (removed — V4A is OpenAI-Codex format; opencode/openclaw gate it to OpenAI models, wrong fit for co's small local models) |
| File / glob search | search_files(files) | — | glob | — | 2 | `file_find` ✓ |
| Content / grep search | search_files(content) | — | grep | — | 2 | `file_search` ✓ |

### 2.2 Shell and Process Execution

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Foreground shell execution | terminal | nodes | bash | exec_command | 4 | `shell` ✓ |
| Background process start | terminal(background=True) | — | — | (via code/wait) | 2 | `task_start` ✓ |
| Background process status / output | process(poll/log) | — | — | — | 1 | `task_status` ✓ |
| Background process kill | process(kill) | — | — | — | 1 | `task_cancel` ✓ |
| Background process list | process(list) | — | — | — | 1 | `task_list` ✓ |
| Stdin write to running process | process(write/submit/close) | — | — | write_stdin | 2 | ✗ gap |
| PTY mode (interactive CLI) | terminal(pty=True) | — | — | — | 1 | ✗ gap |
| Output watch patterns (notify) | terminal(watch_patterns) | — | — | — | 1 | ✗ gap |
| Code / sandboxed execution | execute_code | — | — | js_repl | 2 | `code_execute` (host-only) |

### 2.3 Web

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Web search | web_search | web_search | search (conditional) | WebSearch | 4 | `web_search` ✓ |
| Web fetch / extract (single URL) | web_extract | web_fetch | fetch | (via API) | 3–4 | `web_fetch` ✓ |
| Batch URL fetch (multi-URL parallel) | web_extract(urls=[…]) | — | — | — | 1 | ✗ gap |
| Code-specific search | — | — | CodeSearchTool | tool_search | 2 | ✗ gap |

### 2.4 Interaction and Session Control

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Ask user a question / clarify | clarify | — | QuestionTool | request_user_input | 3 | `clarify` ✓ (one-shot; see §3.1) |
| Structured to-do / task list | todo | — | TodoWriteTool | — | 2 | `todo_write` / `todo_read` ✓ |
| Runtime capability / tool introspection | (registry check_fn only) | — | — | tool_search / tool_suggest | 1 | `capabilities_check` ✓ (unique) |

### 2.5 Memory and Recall

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Persistent memory store (CRUD) | memory | — | — | — | 1 | `memory_create/modify` ✓ |
| Paginated memory inventory | — | — | — | — | 0 | ✗ deliberate gap — see docs/specs/memory.md |
| Full memory artifact read by slug | — | — | — | — | 0 | ✗ deliberate gap — see docs/specs/memory.md |
| Session / history search | session_search | sessions_history | — | — | 2 | `memory_search` (T1) ✓ |
| T2 artifact search (FTS5 BM25) | — | — | — | — | 0 | `memory_search` (T2) ✓ (unique) |
| Role-filtered session search | session_search(role_filter) | — | — | — | 1 | ✗ gap |

### 2.6 Skills System

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Model-callable skill list | skills_list | — | skill (inject) | — | 2 | ✗ gap |
| Model-callable skill read | skill_view | — | skill (inject) | — | 2 | ✗ gap |
| Model-callable skill write / manage | skill_manage | — | — | — | 1 | ✗ gap |

### 2.7 Subagents and Delegation

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Named-goal subagent / delegate | delegate_task | sessions_spawn | task | spawn_agent | 4 | N/A — co-cli does not expose delegation as a model-visible primitive; every capability is a tool call; `web_research`, `knowledge_analyze`, `reason` encapsulate subagent dispatch internally |
| Named toolset profile for delegate | delegate_task(toolsets=[…]) | — | — | — | 1 | ✗ gap |
| Parallel batch subagent dispatch | delegate_task(tasks=[…]) | — | — | spawn_agents_on_csv | 2 | ✗ gap |
| Tracked task create/update/stop | — | sessions_spawn/status/yield | — | assign_task/wait_agent/close_agent | 2 | (partial via task_*) |
| Agent-to-agent messaging | — | sessions_send | — | send_message/send_input | 2 | ✗ gap |
| Agent lifecycle (list/status/resume/close) | — | sessions_list/session_status | — | resume/close/list_agent | 2 | ✗ gap |

### 2.8 Planning and Mode Control

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Enter / update plan mode | — | update_plan | — | update_plan | 2 | ✗ gap |
| Exit plan mode | — | — | PlanExitTool | — | 1 | ✗ gap |

### 2.9 Scheduling and Automation

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Cron / scheduled job CRUD | cronjob | cron | — | — | 2 | ✗ gap |
| Remote agent trigger | cronjob(deliver=…) | cron (reminders/follow-ups) | — | — | 2 | ✗ gap |

### 2.10 MCP and Extensibility

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| List MCP resources | — | — | — | list_mcp_resources | 1 | ✗ gap |
| Read MCP resource | — | — | — | read_mcp_resource | 1 | ✗ gap |
| MCP resource templates | — | — | — | list_mcp_resource_templates | 1 | ✗ gap |
| Dynamic MCP tool refresh | (deregister/re-register) | (per-session runtime) | — | — | 2 | ✗ gap |
| MCP result size-gated | (registry uniform) | — | — | — | 1 | ✗ gap |

### 2.11 Vision and Media

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Image analysis / vision | vision_analyze | image | — | view_image | 3 | ✗ gap |
| Image generation | image_generate | image_generate | — | ImageGeneration | 3 | ✗ gap |
| Text-to-speech | text_to_speech | tts | — | — | 2 | ✗ gap |
| Browser screenshot + vision | browser_vision | — | — | — | 1 | ✗ gap |
| Video generation | — | video_generate | — | — | 1 | ✗ gap |
| Audio / music generation | — | music_generate | — | — | 1 | ✗ gap |
| PDF document understanding | — | pdf | — | — | 1 | ✗ gap |

### 2.12 Code Intelligence

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Language Server Protocol | — | — | LspTool | — | 1 | ✗ gap |

### 2.13 Browser Automation

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Browser navigate + snapshot | browser_navigate | — | — | — | 1 | ✗ gap |
| Browser click / type / scroll | browser_click/type/scroll | — | — | — | 1 | ✗ gap |
| Browser keyboard / back | browser_press/back | — | — | — | 1 | ✗ gap |
| Browser JS console | browser_console | — | — | — | 1 | ✗ gap |
| Browser image list | browser_get_images | — | — | — | 1 | ✗ gap |

### 2.14 External Service Integrations

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Cross-platform messaging | send_message | message | — | — | 2 | ✗ gap |
| Structured heartbeat / status reporting | — | heartbeat | — | — | 1 | ✗ gap |
| Smart home (Home Assistant) | ha_* (4 tools) | — | — | — | 1 | ✗ gap |
| Obsidian notes | — | — | — | — | 0 | `obsidian_*` ✓ (unique) |
| Google Workspace (Drive/Gmail/Calendar) | — | — | — | — | 0 | `drive_*`, `gmail_*`, `calendar_*` ✓ (unique) |
| RL training control | rl_* (10 tools) | — | — | — | 1 | ✗ gap |

### 2.15 Agentic Meta-Tools (unique to one peer)

| Capability | Peer | co-cli |
|---|---|---|
| Mixture of agents (4 LLMs + aggregator) | hermes | `reason` subagent covers some of this |
| Tool suggest by task | codex (tool_suggest) | ✗ gap |
| Tool search (model discovers tools) | codex (tool_search / ToolSearch spec) | ✗ gap |
| Async turn yield / subagent result collection | openclaw (sessions_yield) | ✗ gap |
| Invalid tool call handler | opencode (InvalidTool) | implicit (pydantic-ai error path) |

---

## Part 3: Three-Tier Classification

Tiers by peer count: **Tier 1 = 3–4 peers** (universal / near-universal),
**Tier 2 = exactly 2** (converged), **Tier 3 = exactly 1** (specialized).
Capabilities present in 0 peers but native to co-cli are collected as
co-cli-unique extensions (§3-unique).

### Tier 1 — System / Core

**Definition:** Present in 3 or 4 peers as a native first-class tool (file
read/write/fetch count codex's API-level access as a soft presence). Every
general-purpose AI agent CLI that reaches production independently converged on
these.

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T1-1 | Shell / terminal execution | hermes, openclaw, opencode, codex (4) | `shell` ✓ |
| T1-2 | File read (paginated, line-ranged) | hermes, opencode, (codex via API) (2–3) | `file_read` ✓ |
| T1-3 | File write (full overwrite) | hermes, opencode, (codex via API) (2–3) | `file_write` ✓ |
| T1-4 | File edit (string-replace) | hermes, opencode, codex (3) | `file_patch` ✓ |
| T1-5 | Web search | hermes, openclaw, opencode, codex (4) | `web_search` ✓ |
| T1-6 | Web fetch / extract | hermes, openclaw, opencode, (codex via API) (3–4) | `web_fetch` ✓ (one-URL; see §4.2) |
| T1-7 | Ask user / clarify (interactive question) | hermes, opencode, codex (3) | `clarify` ✓ (one-shot fragility; see §4.1) |
| T1-8 | Named-goal subagent delegation | hermes, openclaw, opencode, codex (4) | N/A — co-cli's architecture does not surface delegation to the model; every capability is a tool; subagent dispatch is an internal implementation detail of tools like `web_research`, `knowledge_analyze`, `reason` |
| T1-9 | Image analysis / vision | hermes, openclaw, codex (3) | ✗ gap |
| T1-10 | Image generation | hermes, openclaw, codex (3) | ✗ gap |

**co-cli Tier 1 coverage: 7 / 9 applicable.**  
T1-8 is N/A by design: co-cli does not expose delegation as a model-visible primitive — the model only sees tools, and subagent dispatch is encapsulated inside them.  
**New from the openclaw peer:** vision (T1-9) and image generation (T1-10) cross into Tier 1 — three of four peers now ship them as native tools, and co-cli has neither. File/glob search, content/grep search, and structured to-do sit in Tier 2 (only 2 peers each).  
Residual quality gaps: `clarify` one-shot fragility (§4.1), `web_fetch` single-URL only (§4.2), no vision (T1-9), no image generation (T1-10).

---

### Tier 2 — Capabilities

**Definition:** Present in exactly 2 of the 4 peers. Not universal, but multiple
mature systems independently decided these are worth building as native tools.

| # | Capability | Peers (score) | co-cli status |
|---|---|---|---|
| T2-1 | File / glob search | hermes, opencode (2) | `file_find` ✓ |
| T2-2 | Content / grep search | hermes, opencode (2) | `file_search` ✓ |
| T2-3 | Structured to-do / task list | hermes, opencode (2) | `todo_write` / `todo_read` ✓ |
| T2-4 | Background process start | hermes, codex via code/wait (2) | `task_start` ✓ |
| T2-5 | Stdin write to running process | hermes `process(write/submit/close)`, codex `write_stdin` (2) | ✗ gap |
| T2-6 | Code / sandboxed execution | hermes `execute_code`, codex `js_repl` (2) | `code_execute` (host-only) |
| T2-7 | Code-specific search | opencode `CodeSearchTool`, codex `tool_search` (2) | ✗ gap |
| T2-8 | Model-callable skill list | hermes `skills_list`, opencode `skill` inject (2) | ✗ gap — model cannot discover skills without a user slash command |
| T2-9 | Model-callable skill read / load | hermes `skill_view`, opencode `skill` inject (2) | ✗ gap |
| T2-10 | Parallel batch subagent dispatch | hermes `tasks=[…]`, codex `spawn_agents_on_csv` (2) | ✗ gap |
| T2-11 | Session / history search | hermes `session_search`, openclaw `sessions_history` (2) | `memory_search` ✓ |
| T2-12 | Tracked task create / update / stop | openclaw `sessions_spawn/status/yield`, codex `assign_task/wait_agent` (2) | partial — `task_*` covers status/cancel/list |
| T2-13 | Agent-to-agent messaging | openclaw `sessions_send`, codex `send_message/send_input` (2) | ✗ gap |
| T2-14 | Agent lifecycle (list / status / resume / close) | openclaw `sessions_list/session_status`, codex `resume/close/list_agent` (2) | ✗ gap |
| T2-15 | Enter / update plan mode | openclaw `update_plan`, codex `update_plan` (2) | ✗ gap |
| T2-16 | Cron / scheduled job CRUD | hermes `cronjob`, openclaw `cron` (2) | ✗ gap |
| T2-17 | Remote agent trigger | hermes `cronjob(deliver)`, openclaw `cron` (2) | ✗ gap |
| T2-18 | Text-to-speech | hermes `text_to_speech`, openclaw `tts` (2) | ✗ gap |
| T2-19 | Cross-platform messaging | hermes `send_message`, openclaw `message` (2) | ✗ gap |
| T2-20 | Dynamic MCP tool refresh | hermes (deregister/re-register), openclaw (per-session runtime) (2) | ✗ gap |

**co-cli Tier 2 coverage: 4 / 20 fully covered (T2-1, T2-2, T2-3, T2-11); T2-4 and T2-6 partial.**  
The openclaw peer pulls a large cluster into Tier 2 — session/history search (T2-11), agent lifecycle and messaging (T2-12/13/14), plan mode (T2-15), scheduling (T2-16/17), TTS (T2-18), and cross-platform messaging (T2-19) all reach 2 peers. Significant gaps: skill model-callability (T2-8/9), stdin write (T2-5), code-specific search (T2-7), parallel delegation (T2-10), and the openclaw-driven cluster T2-12 through T2-19.

---

### Tier 3 — Specialized / Differentiated

**Definition:** Present in exactly 1 of the 4 peers; specialized, domain-bound,
or requiring significant external infrastructure.

#### T3-A: Fine-grained process control (hermes)

`process(poll/log)` status, `process(kill)`, `process(list)`, `terminal(pty=True)`,
`terminal(watch_patterns)`. co-cli covers status/kill/list via `task_status` /
`task_cancel` / `task_list`; PTY and watch-patterns are gaps.

#### T3-B: Memory CRUD and role-filtered recall (hermes)

`memory` (persistent CRUD) and `session_search(role_filter)`. co-cli covers
persistent CRUD (`memory_create` / `memory_modify`); role-filtered recall is a gap.
(Plain session/history search is Tier 2 — T2-11.)

#### T3-C: Skills authoring (hermes)

`skill_manage` (create/edit/patch/delete). co-cli gap — no model-callable skill write.

#### T3-D: Delegation extras

| Tool | Peer | co-cli |
|---|---|---|
| Named toolset profile for delegation (`delegate_task(toolsets=[…])`) | hermes | ✗ gap — delegation agents have hard-coded tool lists |

#### T3-E: Exit plan mode (opencode)

`PlanExitTool`. (Entering / updating a plan is Tier 2 — T2-15.) co-cli gap — no
model-gated plan mode.

#### T3-F: MCP resources (codex)

`list_mcp_resources`, `read_mcp_resource`, `list_mcp_resource_templates`; plus
hermes's uniform MCP result size-gating. co-cli gap on all — `discover_mcp_tools()`
runs once at startup with no resource surface and no size gate. (Dynamic refresh
is Tier 2 — T2-20.)

#### T3-G: Web ergonomics (hermes)

`web_extract(urls=[…])` — parallel multi-URL fetch. co-cli gap — `web_fetch`
accepts one URL.

#### T3-H: Code intelligence (opencode)

`LspTool` — Language Server Protocol integration (diagnostics, hover, go-to-def).
co-cli gap.

#### T3-I: Browser automation (hermes)

Full browser stack: `browser_navigate`, `browser_snapshot`, `browser_click`,
`browser_type`, `browser_scroll`, `browser_back`, `browser_press`,
`browser_get_images`, `browser_console`, `browser_vision`.  
Requires: Camofox / Browserbase / Firecrawl backend. 10+ tools, large dependency surface.  
co-cli gap: all 10 tools. Port requires a browser backend commitment.

#### T3-J: Code / REPL sandbox backends (hermes)

`execute_code` runs in a multi-environment sandbox (daytona, docker, local,
modal, singularity, SSH). co-cli has `code_execute` but runs on host with
shell-policy gate only — no sandboxed multi-environment backend. (The base
code-execution *capability* is Tier 2 / T2-6; the multi-backend sandbox is the
differentiated extension.)

#### T3-K: Extended media generation (openclaw)

`video_generate`, `music_generate`, `pdf` (document understanding). openclaw-unique
across the four peers. co-cli gap. (Image generation and TTS reach Tier 1 / Tier 2
respectively — see T1-10, T2-18.)

#### T3-L: Multi-model reasoning (hermes)

`mixture_of_agents`: 4 frontier LLMs + aggregator, max reasoning effort, 5 API
calls per invoke. co-cli covers similar use cases via `reason` subagent; not a
tool-level gap.

#### T3-M: Smart home (hermes)

`ha_list_entities`, `ha_get_state`, `ha_list_services`, `ha_call_service`.
Domain-specific (Home Assistant). co-cli's equivalent domain integration is
Google Workspace.

#### T3-N: Reinforcement learning training (hermes)

10 `rl_*` tools covering environment selection, config, training lifecycle, WandB
metrics, inference testing. Highly domain-specific (Tinker-Atropos).

#### T3-O: Structured heartbeat reporting (openclaw)

`heartbeat` records structured run outcomes (done / failed / check-back) with
notify preference and priority. openclaw-unique; serves its long-running /
scheduled-agent model. No co-cli equivalent.

#### T3-P: Tool discovery by model (codex)

`tool_search` (search available tools by query), `tool_suggest` (suggest tools
for a task), `ToolSearch` (deferred model-native tool discovery). co-cli has
`capabilities_check` which exposes the runtime tool surface but does not support
query-based tool lookup.

#### T3-unique: co-cli-Unique Extensions (no peer equivalent)

| Capability | co-cli tool(s) |
|---|---|
| T2 artifact CRUD (create / modify) | `memory_create`, `memory_modify` |
| FTS5 BM25 knowledge search | `memory_search` (T2 tier) |
| Runtime capability introspection | `capabilities_check` (codex `tool_search`/`tool_suggest` is the nearest analog) |
| Obsidian vault integration | `obsidian_list`, `obsidian_search`, `obsidian_read` |
| Google Drive | `drive_search`, `drive_read` |
| Google Gmail | `gmail_list`, `gmail_search`, `gmail_draft` |
| Google Calendar | `calendar_list`, `calendar_search` |

---

## Part 4: Tool Lifecycle Architecture Comparison

How each system handles the meta-layer: registration, execution hooks, approval, result sizing, concurrency, and extensibility.

| Aspect | hermes | openclaw | opencode | codex | co-cli |
|---|---|---|---|---|---|
| **Registration** | AST auto-discovery of `registry.register()` | Factory composition in `createOpenClawTools()`; no manifest; conditional inclusion by config/auth/mode/policy | `ToolRegistry.all()` + dynamic repo-local + plugin | `ToolRegistryBuilder.push_spec()` manual | Manual `NATIVE_TOOLS` tuple + `@agent_tool` decorator — decorator-only path is silently omitted |
| **Runtime availability gate** | `check_fn` per tool (runtime) | `availability.ts` expressions (`always`/`auth`/`config`/`env`/`plugin-enabled`/`context`, `allOf`/`anyOf`) | Plugin `tool.definition` hook | `is_mutating()` / `turn.tool_call_gate` | Build-time `requires_config` only — credential expiry not caught mid-session |
| **Pre/post-tool hooks** | — (gateway pattern) | `before_tool_call` (plugin veto, approval, loop detection); post = diagnostic emit | `tool.execute.before` / `tool.execute.after` | `run_pre_tool_use_hooks` / `run_post_tool_use_hooks` | `CoToolLifecycle.before_tool_execute` / `after_tool_execute` ✓ |
| **Approval model** | Blocking gateway; `_permanent_approved` persisted | Plugin approval `ALLOW_ONCE`/`ALLOW_ALWAYS`/`DENY` (120s+10s); config `allow`/`deny`/`alsoAllow`; `ownerOnly` | `Permission.ask()` / `.reply()`; `"always"` persists | `request_permissions`; `SandboxPermissions` | Deferred approval loop; semantic `ApprovalSubject` (shell/path/domain/tool); session-scoped only |
| **Concurrency partitioning** | — (toolset profiles) | Per-call `AbortSignal`; no tool-level cap; one approval at a time | Plugin hooks | `turn.tool_call_gate` for mutating | `ResourceLockStore` per-path; no class-level concurrency gate |
| **Result size** | `max_result_size_chars` uniform (all tools incl. MCP) | Per-tool caps (web_fetch 750KB/10MB, search 10, image 20, history 20); no uniform gate | `Truncate.output()` wrapper | `truncated_output()` token-based | Per-tool `max_result_size` via `@agent_tool`; MCP results ungated — runaway MCP can flood context |
| **MCP integration** | `mcp_tool.py` with deregister/re-register on refresh | Per-session MCP runtime, materialized + name-sanitized per session, disposed after run | — | `mcp_tool_to_responses_api_tool()`; `list_tools()` with timeout (implied) | `discover_mcp_tools()` once at startup; no timeout wrapper; no `list_tools` timeout; no dynamic refresh |
| **Toolset profiles / grouping** | `toolsets.py` named profiles (`web`, `file`, …) | No explicit profiles; implicit groupings (embedded, sandboxed, media-capable, plugin-augmented, owner-only) | Plugin tool.definition hook | `model_visible_specs` filtering | No named profiles; delegation agents have explicit hard-coded tool lists |
| **Tool context** | — | `HookContext` (agentId, config, sessionKey, sessionId, runId, channelId, loopDetection, sandbox) | `Tool.Context` (sessionID, messageID, agent, abort, callID, messages, metadata(), ask()) | `ToolInvocation` (session, turn, tracker, call_id, tool_name, namespace, payload) | `RunContext[CoDeps]` passed to tool body |
| **Typed output** | — | `AgentToolResult{content[], details}`; helpers `textResult`/`jsonResult`; no schema narrowing | `Truncate.output()` wrapper | `ToolOutput` trait; `FunctionToolOutput`; `ExecCommandToolOutput` | `ToolReturn(return_value, metadata)` — string only; no structured content block |

---

## Part 5: co-cli Gap Priority

Derived from convergence scores, parity matrix (§1 of `RESEARCH-tools-gaps-co-vs-hermes.md`), and architecture analysis (§§3-4 of same).

### Priority High

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| `vision` / image analysis (T1-9) | hermes + openclaw + codex — score 3, **Tier 1** | No vision capability while 3/4 peers ship it natively | Medium — pydantic-ai model wrapper already available |
| MCP `list_tools()` no timeout at startup (§3.8 gaps doc) | Architecture flaw | Startup hang if MCP server stalls | Low — `asyncio.timeout()` + `asyncio.gather()` |
| `tool_output_raw()` bypasses size gate and telemetry (§4.2 gaps doc) | Architecture flaw | Silent context overflow | Medium — audit callsites, restrict to ctx-less helpers |
| MCP tool results not size-gated (§3.7 gaps doc) | Architecture flaw | Context overflow via runaway MCP | Medium — extend `CoToolLifecycle.after_tool_execute` to MCP spans |
| `web_fetch` single-URL only | hermes `web_extract(urls=[…])` — score 1, high value | Sequential latency on multi-URL research | Low — `asyncio.gather` over existing fetch |
| `ModelRetry` vs `tool_error()` unenforced (§4.3 gaps doc) | Architecture flaw | Retry-budget exhaustion on non-recoverable errors | Medium — ruff rule or base-class signal |

### Priority Medium

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| Model-callable `skills_list` + `skill_view` (T2-8, T2-9) | hermes + opencode — score 2 | Skill discovery requires user slash commands; model cannot self-load skills | Medium — read-only tools over existing `co_cli/skills/` registry |
| Runtime `check_fn` for tool availability (§3.1 gaps doc) | hermes + openclaw availability gating | Stale credentials look like transient failures | Medium — optional `check_fn` field on `@agent_tool` + visibility filter hook |
| `task_start` / `shell`: stdin write to running process (T2-5) | hermes + codex — score 2 | Cannot send input to interactive background processes | Medium — extend `task_*` or add `process(action=write/submit/close)` |
| Background task output file-backed | hermes (file-tee) + codex `ExecCommandToolOutput` — score 2 | In-memory `deque(500)` drops output; crash loses all | Medium — optional file sink on task spawn |
| Code-specific search (T2-7) | opencode + codex — score 2 | No code-aware search surface beyond grep | Medium — wrap existing search or external provider |
| Parallel batch delegation (T2-10) | hermes + codex — score 2 | Sequential research; no batch fan-out | Medium — `delegate_task(tasks=[…])` pattern |
| `NATIVE_TOOLS` manual tuple (§3.3 gaps doc) | hermes AST auto-discovery; openclaw factory composition | Tool silently omitted if decorator without tuple entry | Low — module scan for `@agent_tool`-decorated functions at import |
| `shell` PTY mode (T3-A) | hermes — score 1, high value | Cannot run interactive CLIs (Codex, Python REPL, etc.) | Medium — add `pty` flag to `shell` |
| Named toolset profiles for delegation (§3.5 gaps doc, T3-D) | hermes — score 1, strong | New tools invisible to delegation agents; manual list drift | Medium — `toolsets.py`-style registry |

### Priority Low

| Gap | Convergence signal | Risk | Effort |
|---|---|---|---|
| `terminal.watch_patterns` for background tasks (T3-A) | hermes — score 1 | Long-running tasks cannot notify on specific output | Medium — extend `task_start` with regex notifier |
| MCP dynamic tool refresh (T2-20, §3.2 gaps doc) | hermes + openclaw — score 2 | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed` |
| MCP resource tools: list + read (T3-F) | codex — score 1 | Cannot access MCP server resources (only tools) | Medium — two new tools over existing MCP client |
| `clarify` one-shot fragility (§4.1 gaps doc) | Architecture flaw | Model confusion if called twice in one step | Hard — approval-loop dedup |
| `file_read_mtimes` unbounded (§4.4 gaps doc) | Architecture flaw | Memory growth in very long sessions | Low — cap dict or evict at turn reset |
| Session search `role_filter` (T3-B) | hermes — score 1 | Cannot filter assistant vs. user messages in recall | Low — extend search query |
| Enter / update plan mode (T2-15) | openclaw + codex — score 2 | No model-gated plan mode | Large — requires new REPL mode + tool pair |
| LSP integration (T3-H) | opencode — score 1 | No IDE-class code intelligence | Large — requires LSP client infrastructure |

### Out of Scope (peer tools not worth porting)

| Capability | Reason |
|---|---|
| Image generation (T1-10, score 3) | Near-universal among peers but co-cli scopes media generation out; direct Google/OpenAI API use is simpler — revisit if a delivery use case emerges |
| Cross-platform messaging (T2-19, score 2) | Large auth/config surface; no current co-cli delivery workflow |
| Text-to-speech (T2-18, score 2) | No co-cli delivery channel for audio |
| Cron / scheduling (T2-16, T2-17, score 2) | co-cli is user-interactive; OS cron is better suited |
| Extended media generation — video, music, PDF (T3-K) | openclaw-only; no co-cli delivery channel |
| Browser automation (T3-I, 10 tools) | Large browser stack dependency; `web_fetch` covers most read-only needs |
| Home Assistant (T3-M, 4 tools) | Domain-specific; not in co-cli's use case scope |
| Reinforcement learning training (T3-N, 10 tools) | Highly domain-specific (Tinker-Atropos only) |
| `mixture_of_agents` (T3-L) | `reason` subagent covers the use case without the 4-LLM cost |
| Structured heartbeat reporting (T3-O) | openclaw-specific to its long-running scheduled-agent model |

---

## Appendix: Tool Name Mapping Across Peers

Quick lookup for finding the equivalent tool across systems.

| Capability | co-cli | hermes | openclaw | opencode | codex |
|---|---|---|---|---|---|
| Shell exec | `shell` | `terminal` | `nodes` | `bash` | `exec_command` |
| File read | `file_read` | `read_file` | (via nodes) | `read` | (via API) |
| File write | `file_write` | `write_file` | (via nodes) | `write` | (via API) |
| File edit | `file_patch` | `patch(replace)` | — | `edit` | `apply_patch` |
| Multi-hunk patch | — (removed — V4A is OpenAI-Codex format) | `patch(patch)` | — | `ApplyPatchTool` | `apply_patch` |
| File find | `file_find` | `search_files(files)` | — | `glob` | — |
| Content search | `file_search` | `search_files(content)` | — | `grep` | — |
| Web search | `web_search` | `web_search` | `web_search` | `search` | `WebSearch` |
| Web fetch | `web_fetch` | `web_extract` | `web_fetch` | `fetch` | (via API) |
| Todo list | `todo_write/read` | `todo` | — | `todo` | — |
| Clarify / ask user | `clarify` | `clarify` | — | `question` | `request_user_input` |
| Delegate / subagent | — (no model-callable primitive) | `delegate_task` | `sessions_spawn` | `task` | `spawn_agent` |
| Agent-to-agent messaging | — | — | `sessions_send` | — | `send_message`/`send_input` |
| Agent lifecycle | — | — | `sessions_list`/`session_status` | — | `list_agents`/`resume_agent` |
| Session / history search | `memory_search` | `session_search` | `sessions_history` | — | — |
| Skills load | — | `skill_view` | — | `skill` | — |
| Skills list | — | `skills_list` | — | `skill` (dynamic desc) | — |
| Background task start | `task_start` | `terminal(background=True)` | — | — | `code` / `wait` |
| Background task status | `task_status` | `process(poll)` | `session_status` | — | `wait_agent` |
| Memory create | `memory_create` | `memory(add)` | — | — | — |
| Vision | — | `vision_analyze` | `image` | — | `view_image` |
| Image generation | — | `image_generate` | `image_generate` | — | `ImageGeneration` |
| Text-to-speech | — | `text_to_speech` | `tts` | — | — |
| LSP | — | — | — | `lsp` | — |
| Plan enter / update | — | — | `update_plan` | — | `update_plan` |
| Plan exit | — | — | — | `plan` | — |
| Cron create | — | `cronjob(create)` | `cron` | — | — |
| Cross-platform messaging | — | `send_message` | `message` | — | — |
| MCP resource read | — | — | — | — | `read_mcp_resource` |
| Capabilities check | `capabilities_check` | — | — | — | `tool_search` / `tool_suggest` |
| Obsidian | `obsidian_*` | — | — | — | — |
| Google Workspace | `drive_*`, `gmail_*`, `calendar_*` | — | — | — | — |
