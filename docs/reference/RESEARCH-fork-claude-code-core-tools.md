# RESEARCH: fork-claude-code core tool surface
_Date: 2026-04-02_

This note captures the core tool surface in `~/workspace_genai/fork-claude-code` after a direct code scan of the tool registry and the main tool implementations.

The source of truth is:

- `~/workspace_genai/fork-claude-code/tools.ts`
- `~/workspace_genai/fork-claude-code/Tool.ts`
- `~/workspace_genai/fork-claude-code/tools/*`

The relevant registry path is:

- `getAllBaseTools()` in `tools.ts` for built-in tools
- `getTools(...)` in `tools.ts` for mode-filtered built-ins
- `assembleToolPool(...)` in `tools.ts` for built-ins plus MCP tools

## 1. Core findings

- `fork-claude-code` has a strict `Tool` interface with first-class fields for `searchHint`, `isReadOnly`, `isDestructive`, `requiresUserInteraction`, `shouldDefer`, `validateInput`, and `checkPermissions`.
- The built-in tool set is centrally registered in `getAllBaseTools()`, then filtered by deny rules and `isEnabled()`.
- MCP tools are not hardcoded one-by-one. They are merged into the active pool through `assembleToolPool(...)` and use the generic `MCPTool` wrapper.
- The system separates foundational coding tools from workflow tools, task tools, user-interaction tools, and optional gated surfaces.
- Tool discovery is explicit: deferred tools require `ToolSearch` before invocation.

## 2. Tool contract patterns

The `Tool` contract in `Tool.ts` shows the main design choices:

- tools expose a JSON/Zod input schema and optional output schema
- tools self-declare concurrency safety and read-only/destructive behavior
- tools own validation and tool-specific permission logic
- tools can be deferred from the initial prompt
- tools can advertise a short `searchHint` for deferred-tool discovery
- tools can mark themselves as MCP-backed or LSP-backed

This is a mature tool system rather than a thin function-call wrapper.

## 3. Core built-in tools

The parity table below focuses on the built-in tools that shape the main coding and operator loop. Names are the real invocation names from the codebase, not folder names.

### 3.1 `co` parity matrix

| `fork-claude-code` tool | Purpose | Typical use case | `co` status | Adoption | Notes |
|---|---|---|---|---|---|
| `Agent` | Spawn or fork a subagent, optionally isolated or backgrounded | Delegate review, research, implementation, or verification work | partial | `Adapt` | `co` has role-specific subagent tools (`run_coding_subagent`, `run_research_subagent`, `run_analysis_subagent`, `run_reasoning_subagent`) rather than a generic spawn/fork agent |
| `Bash` | Execute shell commands with validation, permission checks, sandboxing, and background-task support | Run `git`, tests, build commands, package managers, and repo inspection | partial | `Adapt` | `co` has `run_shell_command`, but it is much thinner: no structured description field, no built-in progress UI semantics, no shell-level backgrounding integration, no PowerShell analogue |
| `Read` | Read files with offsets, limits, image/PDF handling, and read tracking | Inspect source files, configs, logs, screenshots, and selected file ranges | partial | `Adapt` | `co` has `read_file`, but only for UTF-8 text files with simple line slicing |
| `Edit` | Make in-place string edits with stale-file protection | Apply targeted edits to an existing file | partial | `Adapt` | `co` has `edit_file`, but without read-before-write enforcement or stale-file protection |
| `Write` | Create or overwrite files with diff-aware output | Create a file or replace an existing file wholesale | partial | `Adapt` | `co` has `write_file`, but without prior-read enforcement, patch/diff output, or overwrite-safety checks |
| `Glob` | Find files by pathname pattern | Locate `*.ts`, `**/*.md`, or files under a subtree | missing | `Adopt` | `co` has `list_directory` and shell access, but no dedicated pathname-pattern search tool |
| `Grep` | Search file contents with regex and ripgrep-like options | Find symbols, strings, patterns, or candidate edit sites | partial | `Adapt` | `co` has `find_in_files`, but it is a narrower regex search without the richer result modes and context controls |
| `NotebookEdit` | Edit Jupyter notebook cells structurally | Replace, insert, or delete `.ipynb` cells | missing | `Defer` | no notebook-structural edit tool in `co` |
| `WebFetch` | Fetch a URL and run a prompt over the page content | Extract structured facts from a known page | partial | `Adapt` | `co` fetches and converts pages, but does not support fork-style `URL + extraction prompt` behavior |
| `WebSearch` | Search the live web for current information | Look up docs, release notes, or recent external facts | present | `N/A` | close conceptual parity |
| `TodoWrite` | Maintain the legacy in-session todo list | Keep a lightweight task checklist during a turn | present | `N/A` | `co` has `write_todos` / `read_todos` |
| `AskUserQuestion` | Ask structured multiple-choice questions | Resolve ambiguity with constrained user input | missing | `Adopt` | no structured multi-choice user-interaction tool in `co` |
| `Skill` | Execute a skill/prompt-command, often via a forked agent | Reuse bundled, local, or marketplace workflows | partial | `Defer` | `co` has skills, but they are slash/prompt workflows rather than an agent-callable `Skill` tool surface |
| `EnterPlanMode` / `ExitPlanMode` | Switch into read-only planning mode; present a plan for approval and resume implementation | Force exploration and design before editing; hand off the plan before coding starts | missing | `Adopt` | `co` has todo tracking and approvals, but no explicit plan-mode state machine |
| `TaskStop` | Stop a running background task | Kill a long-running shell or agent task | present-ish | `N/A` | `cancel_background_task` covers cancellation for shell background tasks |
| `SendUserMessage` | Deliver a user-facing message, optionally with attachments | Surface status, blockers, or completion messages | not directly applicable | `Skip` | `co`'s foreground agent writes directly to the user; it does not need a dedicated outbound user-message tool in the same way |
| `ListMcpResourcesTool` / `ReadMcpResourceTool` | List resources exposed by connected MCP servers; read a specific MCP resource by URI | Discover remote documents, datasets, or handles; pull the contents of a discovered MCP resource | missing | `Defer` | `co` discovers MCP callable tools, but does not expose MCP resources as first-class helper tools |
| `ToolSearch` | Search deferred tools by keywords or explicit selection | Discover tools whose schemas were not initially loaded | present, narrower | `Adapt` | `co` has `search_tools`, but ranking and selection are simpler |

## 4. Task and workflow tools

These tools extend the core loop into structured task tracking and workflow control.

### 4.1 `co` parity matrix

| `fork-claude-code` tool | Purpose | Typical use case | `co` status | Adoption | Notes |
|---|---|---|---|---|---|
| `TaskCreate` / `TaskGet` / `TaskUpdate` / `TaskList` | Create, retrieve, update, and list tasks in task-list mode | Add tracked work items, inspect status and dependencies, mark lifecycle state, inspect the current queue | missing | `Adapt` | `co` has session todos and background tasks, but no persistent task object model with IDs, owners, and dependency graph |
| `TaskOutput` | Read output from a background task; marked deprecated in favor of direct file reads | Inspect task logs or results from async shells/agents | partial | `Adapt` | `check_task_status` returns task output, but only for `co`'s in-memory shell tasks |
| `EnterWorktree` / `ExitWorktree` | Create and switch into an isolated git worktree; leave or remove the active session worktree | Perform work in a disposable or parallel checkout; return to the main repo or clean up the isolated branch | missing | `Defer` | no isolated worktree workflow tool pair |
| `SendMessage` | Send messages among agents, teammates, peers, or bridge sessions | Coordinate a swarm or continue a spawned agent | missing | `Defer` | no inter-agent messaging or peer mailbox surface |
| `TeamCreate` / `TeamDelete` | Create or tear down a multi-agent team/swarm context | Start a coordinated multi-agent workflow; clean up after swarm work completes | missing | `Defer` | no swarm/team abstraction |
| `LSP` | Run language-server code intelligence operations | Go to definition, find references, hover, symbols, call hierarchy | missing | `Defer` | no code-intelligence tool for definitions, references, hover, or symbols |
| `Config` | Read or set Claude Code configuration | Change model, theme, or permission defaults | missing | `Defer` | no tool-visible runtime config get/set surface |

## 5. Dynamic MCP tool surface

`fork-claude-code` also supports a dynamic tool surface through MCP:

Important detail:

- built-in resource helper tools are always modeled separately as `ListMcpResourcesTool` and `ReadMcpResourceTool`
- dynamic MCP callable tools are materialized later and merged into the tool pool
- deny rules can hide entire MCP server prefixes before the model sees them

### 5.1 `co` parity matrix

| `fork-claude-code` tool category | Purpose | Typical use case | `co` status | Adoption | Notes |
|---|---|---|---|---|---|
| dynamic MCP callable tools via `MCPTool` | Generic wrapper for dynamically loaded MCP tools | Invoke connector tools from GitHub, Slack, browsers, docs systems, and other MCP servers | present | `N/A` | `co` builds MCP toolsets, discovers tool names into `tool_index`, and defers them by default |
| MCP resource helper tools | List MCP resources and read a specific MCP resource by URI | Discover remote documents, datasets, or handles; pull the contents of a discovered MCP resource | missing | `Defer` | `co` has no equivalent to `ListMcpResourcesTool` or `ReadMcpResourceTool` |

## 6. Optional and gated tools

The registry references a wider product surface behind feature flags, platform gates, or user type checks. These are present in the codebase but are not part of the stable core loop described above.

Examples include:

- `PowerShell`
- `REPLTool`
- cron trigger tools
- `RemoteTriggerTool`
- `SleepTool`
- `WorkflowTool`
- `TungstenTool`
- `WebBrowserTool`
- `TerminalCaptureTool`
- `CtxInspectTool`
- notification-oriented tools

These are implementation-relevant, but they should be treated as optional extensions rather than the canonical peer-system baseline.

### 6.1 `co` parity matrix

| `fork-claude-code` tool category | `co` status | Adoption | Notes |
|---|---|---|---|
| `PowerShell` | missing | `Defer` | `co` only exposes a shell tool, not a Windows-specific parallel surface |
| `REPLTool` / `TungstenTool` / terminal-capture-style tools | missing | `Skip` | `co` is a REPL app, but it does not expose these as model-callable tools |
| cron / remote-trigger / sleep / workflow-script tools | missing | `Defer` | no equivalent scheduled-trigger or workflow-script tool surface |
| browser / terminal inspection tools | missing | `Skip` | no `WebBrowserTool` / `TerminalCaptureTool` equivalent |

## 7. Design takeaways for co

- `fork-claude-code` converges on a strong typed tool contract, not ad hoc function calls.
- It separates core coding primitives from structured workflow primitives.
- It treats tool discovery as a first-class problem through `ToolSearch` and deferred loading.
- It cleanly distinguishes built-in tools from dynamic MCP tools while still unifying them under one runtime contract.
- It pushes permission, validation, and destructive/read-only semantics into the tool layer itself.

For co, the strongest borrowable patterns are:

- a strict tool contract with explicit capability metadata
- deferred-tool discovery instead of overloading the initial prompt
- central registry plus pool assembly as the single source of truth
- clean separation between core primitives, workflow tools, and dynamic connector tools

## 8. Gap analysis against `co-cli` current implementation

This section compares the `fork-claude-code` tool surface above against `co`'s current native tool registry and documented tool behavior.

The detailed parity tables now live inline with each `fork-claude-code` tool category:

- section 3.1 for core built-in tools
- section 4.1 for task and workflow tools
- section 5.1 for dynamic MCP tools
- section 6.1 for optional and gated tools

Primary `co` evidence:

- `co_cli/agent.py`
- `co_cli/tools/files.py`
- `co_cli/tools/shell.py`
- `co_cli/tools/tool_search.py`
- `co_cli/tools/subagent.py`
- `co_cli/tools/_subagent_agents.py`
- `co_cli/tools/task_control.py`
- `co_cli/tools/todo.py`
- `co_cli/tools/google_calendar.py`
- `co_cli/tools/capabilities.py`
- `docs/DESIGN-tools.md`

### 8.1 Summary

`co` is already aligned with the fork in three important ways:

- it has a centralized native tool registry plus MCP discovery
- it uses deferred-tool discovery as a first-class mechanism
- it has the core coding primitives of read, edit, write, shell, web search, and web fetch

But `co` is still materially behind `fork-claude-code` in four areas:

- structured workflow control
- file/tool execution semantics
- agent and task orchestration
- MCP resource ergonomics

At the same time, `co` is ahead in one area:

- explicit local knowledge and personal-operator tools such as memory, articles, unified knowledge search, Obsidian, Drive, Gmail, and Calendar

### 8.2 Where `co` is materially narrower

#### A. Generic agent orchestration is missing

The fork's `Agent` tool is a general orchestration primitive. It supports fresh agents, forked agents, optional backgrounding, optional isolation, and later continuation through `SendMessage`.

`co` does not have an equivalent general primitive. Instead it has four narrow delegation tools:

- `run_coding_subagent`
- `run_research_subagent`
- `run_analysis_subagent`
- `run_reasoning_subagent`

These are valuable, but they are structurally weaker:

- they are one-shot
- they are role-bound rather than task-shaped
- they do not persist as resumable agents
- they do not expose background execution as an agent primitive
- they do not expose messaging or coordination
- three of the four are read-only by construction, and the fourth has no tools at all

This means `co` currently supports delegation, but not agent orchestration.

#### B. File semantics are much simpler than the fork

`co` has the core file primitives, but they are basic:

- `read_file` reads plain text and slices lines
- `write_file` overwrites immediately
- `edit_file` does direct string replacement

Compared with the fork, `co` is missing:

- read-before-write enforcement
- stale-file protection based on prior read state
- richer path validation and edit preconditions
- structured notebook editing
- binary/image/PDF-aware reading
- stronger patch-oriented write/edit results
- a dedicated filename-pattern search tool (`Glob`)

This is the biggest quality gap in the coding loop itself. `co` can do the operations, but with less safety and less structure.

#### C. Workflow control is under-modeled

The fork has explicit workflow tools for:

- entering and exiting plan mode
- structured user questioning
- task CRUD
- task output inspection
- team lifecycle
- worktree lifecycle

`co` instead has:

- session todos
- approval deferral
- background shell tasks

That is enough for an MVP loop, but it does not create explicit workflow states. The fork's model can deliberately switch into planning, present a plan for approval, spawn a coordinated worker, and keep task state in a structured graph. `co` cannot do that today.

#### D. Background work is narrower

`co` has `start_background_task`, `check_task_status`, `cancel_background_task`, and `list_background_tasks`.

This is useful, but it is narrower than the fork because:

- tasks are shell-only, not a shared abstraction for shell and agents
- task state is session memory, not a richer durable task framework
- output retention is intentionally lightweight and memory-backed
- there is no native “spawn background agent and later resume/message it” flow

So `co` has background execution, but not background multi-agent task orchestration.

#### E. MCP support exists, but MCP resource ergonomics are missing

`co` builds MCP toolsets and discovers MCP tool names into `tool_index`. That aligns with the fork at the callable-tool level.

But `co` does not currently expose fork-style helper tools for:

- listing MCP resources
- reading a specific MCP resource

That means MCP in `co` is presently closer to “call discovered tools” than to “treat MCP as a full discovery surface of tools plus resources”.

### 8.3 Where `co` is already stronger

The fork's core tool surface is coding-operator heavy. `co` is stronger in explicit personal knowledge and local context tooling:

- `search_memories`, `save_memory`, `update_memory`, `append_memory`, `list_memories`
- `save_article`, `search_articles`, `read_article`, `search_knowledge`
- first-class Obsidian tools
- first-class Drive, Gmail, and Calendar tools
- `check_capabilities` for runtime and integration introspection

This is not a small detail. It means `co` is already more of a personal operator than the fork in its native tool surface, even though it is behind on orchestration and coding-workflow rigor.

### 8.4 Highest-value gaps to close next

If the goal is to borrow the most valuable peer-system patterns from the fork without bloating `co`, the highest-value gaps are:

1. **Stronger file semantics**

   Add the safety behaviors before adding more surface area:

   - read-before-write enforcement
   - stale-file detection for `edit_file` and `write_file`
   - a dedicated `glob`/pathname-search tool
   - eventually notebook-aware editing if notebook workflows matter

2. **Explicit workflow state**

   `co` needs a lightweight plan-mode pair more than it needs swarm features:

   - enter planning state
   - keep write tools hidden or blocked during planning
   - present plan for approval
   - resume into implementation with a narrowed approved surface

3. **Better delegation primitive**

   Before adding teams or messaging, `co` likely needs a generic task-shaped subagent primitive:

   - one tool, not four top-level role tools
   - explicit role/permissions/tool-scope input
   - optional background execution
   - optional resumability

4. **Task model unification**

   `co` should eventually stop splitting “todos” and “background tasks” into unrelated systems. A single task abstraction would cover:

   - checklist tasks
   - background shells
   - subagent jobs
   - status and output inspection

5. **MCP resource helpers**

   This is lower priority than the items above, but still valuable:

   - list MCP resources
   - read MCP resources

### 8.5 Bottom line

`co` already has the foundations of the fork's tool architecture:

- central registry
- deferred discovery
- MCP discovery
- core file/web/shell tools

But `co` is still a simpler operator. Relative to the fork, it is:

- **ahead** on personal knowledge and first-class connector tools
- **behind** on coding-tool rigor
- **behind** on explicit workflow modeling
- **behind** on agent/task orchestration

The right borrowing strategy is not “copy the entire tool surface”. The right strategy is:

- strengthen file semantics first
- add explicit plan-mode workflow second
- generalize subagents and tasks third
- leave swarm/team surfaces for later unless they become immediately necessary
