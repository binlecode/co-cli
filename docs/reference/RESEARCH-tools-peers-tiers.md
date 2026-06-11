# RESEARCH: Tool Tiering Across Peers — hermes · openclaw · opencode · codex

Cross-peer synthesis of the `RESEARCH-tools-*.md` files and a direct survey of
the openclaw repo. Establishes a three-tier classification of tool capabilities
based on convergence frequency across peers, with full per-peer inventories,
lifecycle architecture comparison, co-cli coverage mapping, and prioritized gap
analysis. **Part 5 absorbs the former `RESEARCH-tools-gaps-co-vs-hermes.md`** —
the co-cli↔hermes deep parity + architecture audit (code-verified, file:line
cited, last refreshed to v0.8.342).

**Peers reviewed:** hermes-agent, openclaw, opencode, codex.  
**Reference system:** co-cli (36 native tools as of v0.8.342).

**Sources:**
- `RESEARCH-tools-hermes-agent.md` — hermes tool registry
- `RESEARCH-tools-opencode.md` — opencode built-in registry
- `RESEARCH-tools-codex.md` — codex Rust tool spec
- `~/workspace_genai/openclaw/src/agents/openclaw-tools.ts` — openclaw factory registry (surveyed directly; no standalone research file)
- co-cli `TOOL_REGISTRY` runtime dump + source (Part 5 audit, code-verified to v0.8.342)

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
| Foreground shell execution | terminal | nodes | bash | exec_command | 4 | `shell_exec` ✓ |
| Background process start | terminal(background=True) | — | — | (via code/wait) | 2 | `task_start` ✓ |
| Background process status / output | process(poll/log) | — | — | — | 1 | `task_status` ✓ |
| Background process kill | process(kill) | — | — | — | 1 | `task_cancel` ✓ |
| Background process list | process(list) | — | — | — | 1 | `task_list` ✓ |
| Stdin write to running process | process(write/submit/close) | — | — | write_stdin | 2 | ✗ gap |
| PTY mode (interactive CLI) | terminal(pty=True) | — | — | — | 1 | ✗ gap |
| Output watch patterns (notify) | terminal(watch_patterns) | — | — | — | 1 | ✗ gap |
| Code / sandboxed execution | execute_code | — | — | js_repl | 2 | ✗ removed (`6390d73c`) — routes through `shell_exec` |

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
| Persistent memory store (CRUD) | memory | — | — | — | 1 | `memory_create/append/replace/delete` ✓ |
| Paginated memory inventory | — | — | — | — | 0 | ✗ deliberate gap — search-driven recall, see docs/specs/memory.md |
| Full memory artifact read by slug | — | — | — | — | 0 | `memory_view(name)` ✓ (unique) |
| Session / history search | session_search | sessions_history | — | — | 2 | `session_search` ✓ (ripgrep) |
| T2 artifact search (FTS5 BM25) | — | — | — | — | 0 | `memory_search` (T2) ✓ (unique) |
| Role-filtered session search | session_search(role_filter) | — | — | — | 1 | ✗ gap |

### 2.6 Skills System

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Model-callable skill list | skills_list | — | skill (inject) | — | 2 | N/A — skills surfaced via `<available_skills>` manifest, not a list tool |
| Model-callable skill read | skill_view | — | skill (inject) | — | 2 | `skill_view` ✓ (ALWAYS) |
| Model-callable skill write / manage | skill_manage | — | — | — | 1 | `skill_create/edit/patch/delete` ✓ (DEFERRED) |

### 2.7 Subagents and Delegation

| Capability | hermes | openclaw | opencode | codex | Score | co-cli |
|---|---|---|---|---|---|---|
| Named-goal subagent / delegate | delegate_task | sessions_spawn | task | spawn_agent | 4 | N/A — co-cli removed all mid-turn delegation (the `web_research`/`knowledge_analyze`/`reason` triad, v0.8.280–v0.8.x); the only sub-agent path is the daemon dream-reviewer, never model-callable |
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
| Image analysis / vision | vision_analyze | image | — | view_image | 3 | `image_view` ✓ (capability-gated to agent model) |
| Image generation | image_generate | image_generate | — | ImageGeneration | 3 | ✗ gap |
| Text-to-speech | text_to_speech | tts | — | — | 2 | ✗ gap |
| Browser screenshot + vision | browser_vision | — | — | — | 1 | ✗ gap |
| Video generation | — | video_generate | — | — | 1 | ✗ gap |
| Audio / music generation | — | music_generate | — | — | 1 | ✗ gap |
| PDF document understanding | — | pdf | — | — | 1 | `documents` skill ✓ (text); scanned → `image_view` |

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
| Obsidian notes | — | — | — | — | 0 | ✗ removed (`6390d73c`) — folder reads route through `file_search`/`file_read` |
| Google Workspace (Drive/Gmail/Calendar) | — | — | — | — | 0 | `google_drive_*`, `google_gmail_*`, `google_calendar_*` ✓ (unique) |
| RL training control | rl_* (10 tools) | — | — | — | 1 | ✗ gap |

### 2.15 Agentic Meta-Tools (unique to one peer)

| Capability | Peer | co-cli |
|---|---|---|
| Mixture of agents (4 LLMs + aggregator) | hermes | ✗ no analogue — the in-turn delegation triad was removed |
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
| T1-1 | Shell / terminal execution | hermes, openclaw, opencode, codex (4) | `shell_exec` ✓ |
| T1-2 | File read (paginated, line-ranged) | hermes, opencode, (codex via API) (2–3) | `file_read` ✓ |
| T1-3 | File write (full overwrite) | hermes, opencode, (codex via API) (2–3) | `file_write` ✓ |
| T1-4 | File edit (string-replace) | hermes, opencode, codex (3) | `file_patch` ✓ |
| T1-5 | Web search | hermes, openclaw, opencode, codex (4) | `web_search` ✓ |
| T1-6 | Web fetch / extract | hermes, openclaw, opencode, (codex via API) (3–4) | `web_fetch` ✓ (one-URL; see §4.2) |
| T1-7 | Ask user / clarify (interactive question) | hermes, opencode, codex (3) | `clarify` ✓ (one-shot fragility; see §4.1) |
| T1-8 | Named-goal subagent delegation | hermes, openclaw, opencode, codex (4) | N/A — co-cli does not surface delegation to the model; mid-turn delegation (the `web_research`/`knowledge_analyze`/`reason` triad) was removed, leaving only the daemon dream-reviewer sub-agent path |
| T1-9 | Image analysis / vision | hermes, openclaw, codex (3) | `image_view` ✓ (v0.8.342, capability-gated) |
| T1-10 | Image generation | hermes, openclaw, codex (3) | ✗ gap (Out of Scope — no delivery channel) |

**co-cli Tier 1 coverage: 8 / 9 applicable; the 1 remaining (image generation) is a deliberate scope-out.**  
T1-8 is N/A by design: co-cli does not expose delegation as a model-visible primitive — the model only sees tools, and subagent dispatch is encapsulated inside them.  
**Vision (T1-9) closed** by `image_view` (v0.8.342): pixels attach via `ToolReturn.content` for a vision-capable agent model, self-hiding on text-only models — there is no separate vision model or describe-fallback. The only standing Tier-1 item co-cli lacks is image generation (T1-10), which is scoped out (no co-cli delivery channel). File/glob search, content/grep search, and structured to-do sit in Tier 2 (only 2 peers each).  
Residual quality items: `clarify` one-shot fragility (§5.3), `web_fetch` single-URL (rejected as parity-cosmetic — §5.1).

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
| T2-6 | Code / sandboxed execution | hermes `execute_code`, codex `js_repl` (2) | ✗ removed (`6390d73c`) — `shell_exec` only, host-shell stance |
| T2-7 | Code-specific search | opencode `CodeSearchTool`, codex `tool_search` (2) | ✗ gap |
| T2-8 | Model-callable skill list | hermes `skills_list`, opencode `skill` inject (2) | N/A — `<available_skills>` manifest in prompt, not a list tool |
| T2-9 | Model-callable skill read / load | hermes `skill_view`, opencode `skill` inject (2) | `skill_view` ✓ |
| T2-10 | Parallel batch subagent dispatch | hermes `tasks=[…]`, codex `spawn_agents_on_csv` (2) | ✗ gap |
| T2-11 | Session / history search | hermes `session_search`, openclaw `sessions_history` (2) | `session_search` ✓ (ripgrep) + `session_view` |
| T2-12 | Tracked task create / update / stop | openclaw `sessions_spawn/status/yield`, codex `assign_task/wait_agent` (2) | partial — `task_*` covers status/cancel/list |
| T2-13 | Agent-to-agent messaging | openclaw `sessions_send`, codex `send_message/send_input` (2) | ✗ gap |
| T2-14 | Agent lifecycle (list / status / resume / close) | openclaw `sessions_list/session_status`, codex `resume/close/list_agent` (2) | ✗ gap |
| T2-15 | Enter / update plan mode | openclaw `update_plan`, codex `update_plan` (2) | ✗ gap |
| T2-16 | Cron / scheduled job CRUD | hermes `cronjob`, openclaw `cron` (2) | ✗ gap |
| T2-17 | Remote agent trigger | hermes `cronjob(deliver)`, openclaw `cron` (2) | ✗ gap |
| T2-18 | Text-to-speech | hermes `text_to_speech`, openclaw `tts` (2) | ✗ gap |
| T2-19 | Cross-platform messaging | hermes `send_message`, openclaw `message` (2) | ✗ gap |
| T2-20 | Dynamic MCP tool refresh | hermes (deregister/re-register), openclaw (per-session runtime) (2) | ✗ gap |

**co-cli Tier 2 coverage: 6 / 20 fully covered (T2-1, T2-2, T2-3, T2-9, T2-11; plus `skill_view`/skill-write shipped closing the T2-8/9 cluster); T2-4 partial; T2-6 dropped by design.**  
Skill model-callability (T2-8/9) is now closed — `skill_view` (ALWAYS) + `skill_create/edit/patch/delete` (DEFERRED). The openclaw peer pulls a large cluster into Tier 2 — session/history search (T2-11, covered), agent lifecycle and messaging (T2-12/13/14), plan mode (T2-15), scheduling (T2-16/17), TTS (T2-18), and cross-platform messaging (T2-19). Remaining genuine gaps that are neither rejected-by-design nor out-of-scope: stdin write (T2-5), code-specific search (T2-7), plan mode (T2-15), MCP dynamic refresh (T2-20). The T2-12/13/14 messaging/lifecycle cluster is N/A — co-cli has no model-visible delegation surface.

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
persistent CRUD (`memory_create` / `memory_append` / `memory_replace` / `memory_delete`);
role-filtered recall is WITHDRAWN as an intentional divergence (see §5.3).
(Plain session/history search is Tier 2 — T2-11.)

#### T3-C: Skills authoring (hermes)

`skill_manage` (create/edit/patch/delete). **Closed** — co-cli split this into four
addressable DEFERRED tools `skill_create` / `skill_edit` / `skill_patch` /
`skill_delete` (`tools/system/skills.py`), plus `skill_view` (ALWAYS) for read.

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
modal, singularity, SSH). co-cli **removed** its `code_execute` (`6390d73c`) — code
invocation now routes through `shell_exec` only (host shell + shell-policy gate). The
multi-backend sandbox is a deliberate scope-out, not a gap. (The base code-execution
*capability* is Tier 2 / T2-6.)

#### T3-K: Extended media generation (openclaw)

`video_generate`, `music_generate`, `pdf` (document understanding). openclaw-unique
across the four peers. co-cli covers PDF **document understanding** via the `documents`
skill (local text extraction through `shell_exec` + `scripts/extract_pdf.py`) and the
`office` skill, with scanned PDFs rendered to images and fed back through `image_view`;
`video_generate` / `music_generate` remain gaps. (Image generation and TTS reach Tier 1 /
Tier 2 respectively — see T1-10, T2-18.)

#### T3-L: Multi-model reasoning (hermes)

`mixture_of_agents`: 4 frontier LLMs + aggregator, max reasoning effort, 5 API
calls per invoke. co-cli has no analogue now that the in-turn delegation triad
(`reason` included) is removed; re-adding would require a delegation surface co-cli
deliberately dropped. Out of scope.

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
| Memory artifact CRUD | `memory_create`, `memory_append`, `memory_replace`, `memory_delete` |
| Full memory artifact read by slug | `memory_view(name)` |
| FTS5 BM25 knowledge search | `memory_search` (T2 tier) |
| Runtime capability introspection | `capabilities_check` (codex `tool_search`/`tool_suggest` is the nearest analog) |
| Google Drive | `google_drive_search`, `google_drive_read` |
| Google Gmail | `google_gmail_list`, `google_gmail_search`, `google_gmail_draft` |
| Google Calendar | `google_calendar_list`, `google_calendar_search` |

---

## Part 4: Tool Lifecycle Architecture Comparison

How each system handles the meta-layer: registration, execution hooks, approval, result sizing, concurrency, and extensibility.

| Aspect | hermes | openclaw | opencode | codex | co-cli |
|---|---|---|---|---|---|
| **Registration** | AST auto-discovery of `registry.register()` | Factory composition in `createOpenClawTools()`; no manifest; conditional inclusion by config/auth/mode/policy | `ToolRegistry.all()` + dynamic repo-local + plugin | `ToolRegistryBuilder.push_spec()` manual | Self-registering `TOOL_REGISTRY` (list) + `TOOL_REGISTRY_BY_NAME` (dict) via `@agent_tool(register=True)` at import; no manual tuple |
| **Runtime availability gate** | `check_fn` per tool (runtime) | `availability.ts` expressions (`always`/`auth`/`config`/`env`/`plugin-enabled`/`context`, `allOf`/`anyOf`) | Plugin `tool.definition` hook | `is_mutating()` / `turn.tool_call_gate` | Per-turn `check_fn` via `_make_prepare` (Google tools use `check_fn=_google_available`) + build-time `requires_config`; no TTL cache (§5.2) |
| **Pre/post-tool hooks** | — (gateway pattern) | `before_tool_call` (plugin veto, approval, loop detection); post = diagnostic emit | `tool.execute.before` / `tool.execute.after` | `run_pre_tool_use_hooks` / `run_post_tool_use_hooks` | `CoToolLifecycle` **deleted** (capability-API drop, v0.8.312); call-seam logic moved to `_CallSeamToolset.call_tool` (MCP-result spill) |
| **Approval model** | Blocking gateway; `_permanent_approved` persisted | Plugin approval `ALLOW_ONCE`/`ALLOW_ALWAYS`/`DENY` (120s+10s); config `allow`/`deny`/`alsoAllow`; `ownerOnly` | `Permission.ask()` / `.reply()`; `"always"` persists | `request_permissions`; `SandboxPermissions` | Deferred approval loop; semantic `ApprovalSubject` (shell/path/domain/tool); session-scoped only |
| **Concurrency partitioning** | — (toolset profiles) | Per-call `AbortSignal`; no tool-level cap; one approval at a time | Plugin hooks | `turn.tool_call_gate` for mutating | `ResourceLockStore` per-path; no class-level concurrency gate |
| **Result size** | `max_result_size_chars` uniform (all tools incl. MCP) | Per-tool caps (web_fetch 750KB/10MB, search 10, image 20, history 20); no uniform gate | `Truncate.output()` wrapper | `truncated_output()` token-based | Per-tool `spill_threshold_chars` (default 4_000) via `@agent_tool`; MCP string results now spill-gated in `_CallSeamToolset.call_tool` (§5.2) |
| **MCP integration** | `mcp_tool.py` with deregister/re-register on refresh | Per-session MCP runtime, materialized + name-sanitized per session, disposed after run | — | `mcp_tool_to_responses_api_tool()`; `list_tools()` with timeout (implied) | `discover_mcp_tools()` once at startup, **concurrent** `asyncio.gather` (delay = max not sum); `_SanitizingMCPServer` normalizes `inputSchema`; no dynamic refresh (§5.2) |
| **Toolset profiles / grouping** | `toolsets.py` named profiles (`web`, `file`, …) | No explicit profiles; implicit groupings (embedded, sandboxed, media-capable, plugin-augmented, owner-only) | Plugin tool.definition hook | `model_visible_specs` filtering | No named profiles; only two daemon `TaskAgentSpec`s hand-enumerate `tool_names` (no model-facing delegation) |
| **Tool context** | — | `HookContext` (agentId, config, sessionKey, sessionId, runId, channelId, loopDetection, sandbox) | `Tool.Context` (sessionID, messageID, agent, abort, callID, messages, metadata(), ask()) | `ToolInvocation` (session, turn, tracker, call_id, tool_name, namespace, payload) | `RunContext[CoDeps]` passed to tool body |
| **Typed output** | — | `AgentToolResult{content[], details}`; helpers `textResult`/`jsonResult`; no schema narrowing | `Truncate.output()` wrapper | `ToolOutput` trait; `FunctionToolOutput`; `ExecCommandToolOutput` | `ToolReturn(return_value, metadata)`; `image_view` attaches pixels via `ToolReturn.content` (BinaryContent) for vision-capable agent models |

---

## Part 5: co-cli Gap Priority & Architecture Audit

This part absorbs the former `RESEARCH-tools-gaps-co-vs-hermes.md` — the deep,
code-verified co-cli↔hermes parity + architecture audit (last refreshed to v0.8.342).
It covers the current surface and visibility floor (§5.1), architecture-level gaps with
resolution status (§5.2), anti-patterns with status (§5.3), and the consolidated gap
priority table (§5.4). Convergence scores reference the §2 matrix; architecture items are
code-verified against current source.

### 5.1 Current surface, visibility floor, and co-specific guardrails

**Inventory (36 tools, sorted):** `capabilities_check`, `clarify`, `file_patch`,
`file_read`, `file_search`, `file_write`, `google_calendar_list`, `google_calendar_search`,
`google_drive_read`, `google_drive_search`, `google_gmail_draft`, `google_gmail_list`,
`google_gmail_search`, `image_view`, `memory_append`, `memory_create`, `memory_delete`,
`memory_replace`, `memory_search`, `memory_view`, `session_search`, `session_view`,
`shell_exec`, `skill_create`, `skill_delete`, `skill_edit`, `skill_patch`, `skill_view`,
`task_cancel`, `task_list`, `task_start`, `task_status`, `todo_read`, `todo_write`,
`tool_view`, `web_fetch`, `web_search`.

co-cli ships a small static **ALWAYS** floor (always-paid schema cost, relevant for the
Ollama cache prefix) and gates the rest behind a model-driven `tool_view(name=…)` reveal.
This is the biggest architectural divergence from hermes, which keeps everything registered
and gates by runtime `check_fn` + per-platform named toolset profiles.

| Bucket | Count | Tools |
|---|---|---|
| **ALWAYS** | 19 | `memory_search`, `memory_view`, `memory_create`, `memory_append`, `memory_replace`, `memory_delete`, `file_read`, `file_search`, `file_write`, `file_patch`, `web_search`, `web_fetch`, `shell_exec`, `skill_view`, `tool_view`, `capabilities_check`, `clarify`, `todo_write`, `todo_read` |
| **DEFERRED** | 17 | `skill_create`, `skill_edit`, `skill_patch`, `skill_delete`, `session_search`, `session_view`, `task_start`, `task_status`, `task_cancel`, `task_list`, `image_view`, `google_gmail_list`, `google_gmail_search`, `google_gmail_draft`, `google_drive_search`, `google_drive_read`, `google_calendar_list`, `google_calendar_search` |

Visibility mechanics (capability-API drop, v0.8.312): co-cli does **not** pass
`defer_loading` to pydantic-ai. Each tool carries `visibility ∈ {ALWAYS, DEFERRED}`;
`_tool_visibility_filter()` (`agent/toolset.py`) hides DEFERRED tools each turn unless the
canonical name is in `ctx.deps.runtime.revealed_tools`, and narrows to approved + ALWAYS on
approval-resume turns. `tool_view(name=…)` reveals by normalized-exact match (difflib
suggestions on near-miss, cutoff 0.6; "do not retry" on no match). The deleted
`CoToolLifecycle`'s MCP-result spill moved to `_CallSeamToolset.call_tool`; JSON tool-arg
repair moved to `llm/surrogate_recovery_model.py`; path containment moved to explicit
`fs_guards.py` boundary checks inside the file tools.

**co-specific guardrails worth keeping (vs hermes), code-verified:**

- `file_write` / `file_patch` enforce read-before-write + workspace containment via
  `FileReadTracker` + `fs_guards.enforce_write_boundary`; `file_patch` auto-lints `.py` and
  uses a four-strategy fuzzy match (exact, line-trimmed, indent-stripped, escape-expanded).
  Hermes lacks both guards (it has a `cross_profile` escape hatch co-cli does not).
- `file_read` truncates per-line at 2000 chars (a practical shape guard hermes lacks) and
  opts out of spill (`spill_threshold_chars=inf`) since the per-line caps already bound shape.
- `web_fetch` is single-URL by design with trafilatura main-article extraction (fail-open to
  `html2text`). **Multi-URL is rejected as parity-cosmetic** — `web_fetch` is
  `is_concurrent_safe=True`, so pydantic-ai already dispatches parallel `web_fetch(url=…)`
  calls concurrently with per-call error isolation.
- `session_search` is ripgrep-based (no index over transcripts, v0.8.298); memory items still
  use FTS5/BM25. `session_view` covers the verbatim-slice / anchored-scroll need.

### 5.2 Architecture-level gaps (code-verified to v0.8.342)

Status legend: ✅ done · 🟢 in flight (active exec-plan) · 🟠 open · ⚪ deferred / by design.

- **✅ Spill enforcement on MCP results.** Logic moved out of the deleted `lifecycle.py` into
  `_CallSeamToolset.call_tool` (`agent/toolset.py`): plain-string MCP results are coerced
  through `spill_with_span()` (guard: `isinstance(result, str) and info.source == MCP`),
  per-tool `spill_threshold_chars` override, fallback `SPILL_THRESHOLD_CHARS=4_000`.
- **✅ Tool registration self-populating.** `@agent_tool(register=True)` self-registers into
  `TOOL_REGISTRY` + `TOOL_REGISTRY_BY_NAME` at import; no manual tuple. The name-keyed dict
  is what `build_task_agent()` resolves daemon `TaskAgentSpec.tool_names` against.
- **✅ Concurrent MCP `list_tools()` discovery.** `discover_mcp_tools()` (`agent/mcp.py`)
  fans out all `list_tools()` calls via `asyncio.gather` over `_discover_one`; startup delay
  is `max(timeouts)`, not `N × timeout`.
- **✅ MCP `inputSchema` sanitization.** `_SanitizingMCPServer` runs `sanitize_mcp_schema()`
  over every `inputSchema` on each `list_tools()`: collapses `["string","null"]` type arrays
  and nullable `anyOf`/`oneOf`, fixes bare object nodes, recurses. Narrower than hermes's
  `schema_sanitizer.py` (whose top-level-combinator strip + 400-triggered passes are
  xAI/Codex-specific, not needed for co's Ollama/Anthropic targets).
- **✅ Background task output file-backed.** Each task streams stdout+stderr to
  `LOGS_DIR/bg-{task_id}.log` (`tools/background.py`); reads tail via `tail_log` (64 KB
  seek-from-end). No in-memory ring buffer. Not ported: orphan reaper for logs from sessions
  that died before cleanup.
- **🟠 No `check_fn` result cache.** `_make_prepare(fn)` calls `info.check_fn(deps)` every
  prepare (once per turn), no cache. The seven Google tools register
  `check_fn=_google_available` (cheap in-memory `_creds_resolved`). Hermes caches `check_fn`
  for 30 s — worth copying only if an external-probe (network) `check_fn` is ever added.
- **⚪ No named toolset profiles for delegation.** With the mid-turn delegation triad removed,
  the only `TaskAgentSpec`s are the two daemon specs in `daemons/dream/_reviewer.py`
  (`MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`), each hand-enumerating `tool_names`. Persists,
  but confined to two background specs — no model-facing surface.
- **⚪ No MCP dynamic tool refresh** (T2-20). `discover_mcp_tools()` runs once at bootstrap;
  no `notifications/tools/list_changed` subscription. No install-set server emits it; failure
  is bounded (stale-but-additive); `/mcp restart` is the fix.
- **⚪ No persistent approval rules** (§3.7 of the old audit). `session_approval_rules` is
  in-memory per session; hermes persists `_permanent_approved`. Deliberate security tradeoff.

### 5.3 Anti-patterns (status)

- **✅ `tool_output_raw()` removed** — grep across `co_cli/` is zero matches. Helper-layer
  errors route through `tool_error` (which spills via `ctx`). Current `tool_io.py` surface:
  `tool_output`, `tool_error`, `spill_if_oversized`, `spill_with_span`,
  `SPILL_THRESHOLD_CHARS=4_000`, `TOOL_RESULT_PREVIEW_CHARS=1_500`.
- **✅ `file_read_mtimes` bounded** — replaced by `CoDeps.file_tracker: FileReadTracker`,
  shared by reference across daemon-only `fork_deps()`, bounded by its own structure.
- **✅ Path-normalization hidden rewrite removed** — `lifecycle.py` deleted; no
  `PATH_NORMALIZATION_TOOLS` frozenset. Resolution + containment is explicit inside the file
  tools via `fs_guards.enforce_read_boundary`/`enforce_write_boundary`; tests see production
  path-handling code.
- **🟠 `ModelRetry` vs `tool_error()` classification is convention-only.** Transient →
  `raise ModelRetry`; terminal → `return tool_error(...)`. `handle_google_api_error()` is the
  reference pattern; no static check, so misclassification still burns retry budget on
  non-recoverable errors.
- **🟠 `clarify` one-shot fragility.** Model confusion if called twice in one step;
  approval-loop dedup is the (hard) fix.
- **⚪ `role_filter` on `session_search` WITHDRAWN** — intentional divergence, not a defect.
  co's multi-line ripgrep hits make role a sub-span property, not a row property; clean
  filtering would need per-message indexing for marginal value. Do not re-propose from a
  signature diff alone.

### 5.4 Consolidated gap priority

Status legend: ✅ done · 🟢 in flight · 🟠 open · ⚪ deferred / out of scope.

| Priority | Status | Item | Convergence / source | Risk | Effort |
|---|---|---|---|---|---|
| **High** | ✅ | Vision / image analysis (T1-9) | hermes + openclaw + codex — score 3, Tier 1 | (was) no vision while 3/4 peers ship it | `image_view` shipped v0.8.342 (`tools/vision/view.py`) |
| **High** | ✅ | MCP results not spill-gated (§5.2) | architecture | context overflow via runaway MCP | `_CallSeamToolset.call_tool` |
| **High** | ✅ | `tool_output_raw()` bypasses gate (§5.3) | architecture | silent context overflow | `tool_output_raw` deleted |
| **High** | ✅ | MCP schema sanitization (§5.2) | architecture | MCP tools dropped on Ollama / Anthropic rejection | `_SanitizingMCPServer` + `mcp_schema.py` |
| **High** | ✅ | Concurrent MCP `list_tools()` (§5.2) | architecture | startup delay = N × timeout | `_discover_one` + `asyncio.gather` |
| **Medium** | ✅ | Model-callable `skill_view` + skill-write (T2-8/9, T3-C) | hermes + opencode — score 2 | skill discovery/authoring needed slash commands | `skill_view` (ALWAYS) + `skill_create/edit/patch/delete` (DEFERRED) |
| **Medium** | ✅ | Local document handling (T3-K) | hermes `vision_tools` / openclaw `pdf` | could not read local PDFs | `documents` + `office` skills (`scripts/extract_pdf.py`) |
| **Medium** | ✅ | Self-registering tool registry (§5.2) | hermes AST / openclaw factory | tool silently omitted | `TOOL_REGISTRY` + `TOOL_REGISTRY_BY_NAME` |
| **Medium** | 🟢 | `shell_exec` PTY + interactive `task_write`/`task_close` (T2-5, T3-A) | hermes + codex — score 2 | CLIs that gate on isatty; no stdin to running tasks | `2026-05-28-200025-toolgap-interactive-terminal.md` (active, not shipped) |
| **Medium** | ⚪ | `web_fetch` multi-URL (T3-G) | hermes — score 1 | sequential latency | rejected as parity-cosmetic — `is_concurrent_safe` already parallelizes |
| **Medium** | 🟠 | `ModelRetry` / `tool_error` unenforced (§5.3) | architecture | retry-budget waste | ruff rule or base-class signal |
| **Medium** | 🟠 | Code-specific search (T2-7) | opencode + codex — score 2 | no code-aware search beyond grep | wrap existing search or external provider |
| **Low** | 🟠 | `check_fn` result cache (§5.2) | hermes 30 s TTL | wasted work *if* external-probe `check_fn`s are added | copy hermes's TTL when needed |
| **Low** | 🟠 | Named toolset profiles for daemon specs (§5.2) | hermes | tool-list drift in two daemon specs | tag-and-filter; no model-facing surface |
| **Low** | 🟠 | `clarify` one-shot fragility (§5.3) | architecture | model confusion if called twice | hard — approval-loop dedup |
| **Low** | ⚪ | `role_filter` on `session_search` | hermes — score 1 | — | WITHDRAWN — intentional divergence, not a defect |
| **Low** | ⚪ | MCP dynamic refresh (T2-20) | hermes + openclaw — score 2 | stale tool index in long sessions | no install-set server emits `list_changed`; `/mcp restart` fixes |
| **Low** | ⚪ | MCP resource tools: list + read (T3-F) | codex — score 1 | cannot access MCP resources (only tools) | two new tools over existing MCP client |
| **Low** | ⚪ | No permanent approval persistence (§5.2) | hermes | UX friction | deliberate security tradeoff |
| **Large** | 🟠 | Enter / update plan mode (T2-15) | openclaw + codex — score 2 | no model-gated plan mode | new REPL mode + tool pair |
| **Large** | 🟠 | LSP integration (T3-H) | opencode — score 1 | no IDE-class code intelligence | LSP client infrastructure |

### 5.5 Out of Scope (peer tools not worth porting)

| Capability | Reason |
|---|---|
| Image generation (T1-10, score 3) | Near-universal among peers but co-cli scopes media generation out; direct Google/OpenAI API use is simpler — revisit if a delivery use case emerges |
| Code / sandboxed execution (T2-6, T3-J) | co-cli **removed** `code_execute` (`6390d73c`); re-adding means a sandbox backend — host-shell-only stance |
| Mid-turn delegation (`delegate_task`, T1-8) | co-cli deliberately removed mid-turn delegation; re-adding contradicts the single-loop design |
| Cross-platform messaging (T2-19, score 2) | Large auth/config surface; no current co-cli delivery workflow |
| Text-to-speech (T2-18, score 2) | No co-cli delivery channel for audio |
| Cron / scheduling (T2-16, T2-17, score 2) | co-cli is user-interactive; OS cron / Claude Code CronCreate is better suited |
| Extended media generation — video, music (T3-K) | openclaw-only; no co-cli delivery channel |
| Browser automation (T3-I, 10 tools) | Large browser stack dependency; `web_fetch` covers most read-only needs |
| Home Assistant (T3-M, 4 tools) | Domain-specific; co-cli's domain equivalent is Google Workspace |
| Reinforcement learning training (T3-N, 10 tools) | Highly domain-specific (Tinker-Atropos only) |
| `mixture_of_agents` (T3-L) | No co-cli analogue now the in-turn delegation triad is removed |
| Structured heartbeat reporting (T3-O) | openclaw-specific to its long-running scheduled-agent model |

---

## Part 6: co-cli External-Service Surfacing — Intended Design (Google Workspace)

Added 2026-06-02 from a focused follow-on survey of how hermes and openclaw surface
**external, auth-gated, optional service integrations** (hermes `send_message` /
Home Assistant; openclaw `message` / media tools). Neither peer ships Google
Workspace tools — Google/Drive/Gmail/Calendar remains co-cli-unique (§2.14, §3-unique)
— so the comparison is against their *general* external-service pattern, not a
like-for-like Google surface.

**Sources (this section):** `hermes-agent/tools/registry.py:358-364` (check_fn schema
omission), `hermes-agent/tools/homeassistant_tool.py:344-346,404-513`,
`hermes-agent/tools/send_message_tool.py:138,177,1649-1672`,
`hermes-agent/tools/toolsets.py:301-317`, `hermes-agent/hermes_cli/tools_config.py:97,1182-1183`;
`openclaw/src/tools/availability.ts:75-153`, `openclaw/src/agents/openclaw-tools.media-factory-plan.ts:166-258`,
`openclaw/src/agents/pi-tools.ts:661-668` (per-model V4A gate); co-cli
`co_cli/deps.py:84-114`, `co_cli/tools/deferred_prompt.py`, `co_cli/tools/google/*.py`.

### 6.1 Key finding — co's surfacing is already ahead of both peers for this case

Both peers gate external services to **binary present/absent** (absent when
uncredentialed; no stub, no schema). co does that **and** adds a **deferred-visibility
tier** that neither peer has: a one-line stub in the per-turn prompt
(`deferred_prompt.py`) with the full schema loaded on demand via `search_tools`. So
the small-model intended design is *not* a peer port — it is a confirmation of co's
existing architecture plus one new refinement.

### 6.2 Decision 1 — Tiering: keep Google at `DEFERRED`, double-gated

The tiering axis is **invocation frequency × credential dependence**, not "external vs
internal":

| Tier | Mechanism | Tools | Rationale |
|---|---|---|---|
| Always-visible | `VisibilityPolicyEnum.ALWAYS` | file_*, memory_*, web_*, shell, todo_*, session_* | Every-turn primitives — paying their schema cost always is correct |
| Deferred + config-gated | `DEFERRED` + `requires_config="google_credentials_path"` + `check_fn=_google_available` | gmail_*, calendar_*, drive_* | Episodic + auth-dependent — a session that never touches Google pays only a one-line stub, or nothing when unconfigured |

This mirrors hermes's hard `check_fn` credential gate (schema omitted entirely when the
gate fails, `registry.py:358-364`) and openclaw's fully-absent media gating
(`media-factory-plan.ts:166-258`). co must **not** copy hermes's "ship the full enabled
set every turn" assumption (`model_tools.py:392`) — that is a frontier-context luxury
that breaks a small local model.

### 6.3 Decision 2 — Surface shape: dedicated monomorphic tools, NOT a uniform dispatch

Keep the 7 dedicated Google tools (`calendar_list`/`search`, `gmail_list`/`search`/`draft`,
`drive_search`/`read`). Do **not** collapse into a uniform `google(action=…)` surface.

- hermes has no single doctrine, and its split rule vindicates co's: it collapses to one
  action-dispatched tool **only when the operation is identical across backends**
  (`send_message` over ~18 platforms — platform is a `target` string param, not a tool
  boundary; `send_message_tool.py:138,177`), and keeps **dedicated per-op tools when the
  operations genuinely differ** (Home Assistant = 4 tools in a discover-then-act flow;
  `homeassistant_tool.py:404-513`).
- Google list ≠ search ≠ read ≠ draft, across different services — these are *not* one
  uniform operation over interchangeable backends. A `google(action=…)` surface would
  reintroduce exactly the action-dispatch hazard removed from `memory_manage`/`skill_manage`
  (Tasks 3a/3b) and violate `feedback_tool_split_small_model`.
- openclaw collapses everything into action-dispatched tools, but that is an explicit
  frontier-model choice (target `claude-sonnet-4-6`) — not to be copied.

**Group for gating, split for calling:** adopt hermes's grouping-for-gating (each external
service is its own small toolset of dedicated tools, individually toggled,
`toolsets.py:301-317`) — Workspace authenticates, opts in, and appears/disappears as one
unit, while the call surface stays dedicated-monomorphic.

### 6.4 Decision 3 — Small-model ergonomics

Neither peer optimizes for weak models; co's existing levers are the best practice:

1. **The `DEFERRED` tier is the primary lever** — the small model's always-present
   decision space stays ~14 core tools + compact stubs; Google schemas load only on demand.
2. **Monomorphic split beats dispatch for selection** — dedicated `calendar_search` is one
   name → one operation (one reasoning step), vs `google(action=…)` forcing tool-then-action
   (two steps, the second unconstrained).
3. **Sibling disambiguation steers** — for `DEFERRED` tools the one-line stub is the model's
   only always-visible signal, so reciprocal when-to-use lines (calendar_list↔search,
   gmail_list↔search) carry disproportionate weight for a weak model.

### 6.5 The one new refinement worth building

**Labeled grouping of deferred stubs** in `build_deferred_tool_awareness_prompt`
(`deferred_prompt.py`): emit the deferred stubs under integration sub-headers (e.g.
`Google Workspace (load before use): …`) so a weak model treats them as one coherent
cluster instead of N loose lines. This is the genuinely new idea (no peer has it); it is
the subject of a dedicated impl-ready plan. The Google docstring-correctness items
(calendar defaults/`days_back`, drive `page`, gmail `draft.to`) and the Google sibling
steers (gmail/calendar) are folded into that plan rather than the general tool-surface
audit, since they touch the same surface the grouping refinement reworks.

A soft opt-in layer (hermes `_DEFAULT_OFF_TOOLSETS` + credential-auto-enable,
`tools_config.py:97,1182-1183`) is a lower-priority option — `DEFERRED` already reduces the
uncredentialed cost to a single stub line, so the marginal benefit is small.

---

## Appendix: Tool Name Mapping Across Peers

Quick lookup for finding the equivalent tool across systems.

| Capability | co-cli | hermes | openclaw | opencode | codex |
|---|---|---|---|---|---|
| Shell exec | `shell_exec` | `terminal` | `nodes` | `bash` | `exec_command` |
| File read | `file_read` | `read_file` | (via nodes) | `read` | (via API) |
| File write | `file_write` | `write_file` | (via nodes) | `write` | (via API) |
| File edit | `file_patch` | `patch(replace)` | — | `edit` | `apply_patch` |
| Multi-hunk patch | — (removed — V4A is OpenAI-Codex format) | `patch(patch)` | — | `ApplyPatchTool` | `apply_patch` |
| File find | `file_search` (`content=None`) | `search_files(files)` | — | `glob` | — |
| Content search | `file_search` | `search_files(content)` | — | `grep` | — |
| Web search | `web_search` | `web_search` | `web_search` | `search` | `WebSearch` |
| Web fetch | `web_fetch` | `web_extract` | `web_fetch` | `fetch` | (via API) |
| Todo list | `todo_write/read` | `todo` | — | `todo` | — |
| Clarify / ask user | `clarify` | `clarify` | — | `question` | `request_user_input` |
| Delegate / subagent | — (no model-callable primitive) | `delegate_task` | `sessions_spawn` | `task` | `spawn_agent` |
| Agent-to-agent messaging | — | — | `sessions_send` | — | `send_message`/`send_input` |
| Agent lifecycle | — | — | `sessions_list`/`session_status` | — | `list_agents`/`resume_agent` |
| Session / history search | `session_search` / `memory_search` | `session_search` | `sessions_history` | — | — |
| Skills load | `skill_view` | `skill_view` | — | `skill` | — |
| Skills write / manage | `skill_create/edit/patch/delete` | `skill_manage` | — | — | — |
| Skills list | N/A (`<available_skills>` manifest) | `skills_list` | — | `skill` (dynamic desc) | — |
| Background task start | `task_start` | `terminal(background=True)` | — | — | `code` / `wait` |
| Background task status | `task_status` | `process(poll)` | `session_status` | — | `wait_agent` |
| Memory create | `memory_create` | `memory(add)` | — | — | — |
| Memory read by slug | `memory_view` | — | — | — | — |
| Vision | `image_view` | `vision_analyze` | `image` | — | `view_image` |
| PDF document understanding | `documents`/`office` skill | — | `pdf` | — | — |
| Image generation | — | `image_generate` | `image_generate` | — | `ImageGeneration` |
| Text-to-speech | — | `text_to_speech` | `tts` | — | — |
| LSP | — | — | — | `lsp` | — |
| Plan enter / update | — | — | `update_plan` | — | `update_plan` |
| Plan exit | — | — | — | `plan` | — |
| Cron create | — | `cronjob(create)` | `cron` | — | — |
| Cross-platform messaging | — | `send_message` | `message` | — | — |
| MCP resource read | — | — | — | — | `read_mcp_resource` |
| Capabilities check | `capabilities_check` | — | — | — | `tool_search` / `tool_suggest` |
| Google Workspace | `google_drive_*`, `google_gmail_*`, `google_calendar_*` | — | — | — | — |
