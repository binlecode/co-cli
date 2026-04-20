# REPORT: Hermes-Agent Tool Registry

**Date:** April 2026
**Target:** `hermes-agent` source code
**Scope:** Complete listing of all tools, organized by core flows and functions.

---

## Index

### [Core System & File Operations](#core-system-file-operations)
- [clarify](#clarify)
- [memory](#memory)
- [patch](#patch)
- [process](#process)
- [read_file](#read_file)
- [search_files](#search_files)
- [session_search](#session_search)
- [terminal](#terminal)
- [todo](#todo)
- [write_file](#write_file)

### [Browser Automation](#browser-automation)
- [browser_back](#browser_back)
- [browser_click](#browser_click)
- [browser_console](#browser_console)
- [browser_get_images](#browser_get_images)
- [browser_navigate](#browser_navigate)
- [browser_press](#browser_press)
- [browser_scroll](#browser_scroll)
- [browser_snapshot](#browser_snapshot)
- [browser_type](#browser_type)
- [browser_vision](#browser_vision)

### [Execution & Delegation](#execution-delegation)
- [cronjob](#cronjob)
- [delegate_task](#delegate_task)
- [execute_code](#execute_code)
- [mixture_of_agents](#mixture_of_agents)

### [Web & Media](#web-media)
- [image_generate](#image_generate)
- [text_to_speech](#text_to_speech)
- [vision_analyze](#vision_analyze)
- [web_extract](#web_extract)
- [web_search](#web_search)

### [Skill Management](#skill-management)
- [skill_manage](#skill_manage)
- [skill_view](#skill_view)
- [skills_list](#skills_list)

### [Home Assistant (Smart Home)](#home-assistant-smart-home)
- [ha_call_service](#ha_call_service)
- [ha_get_state](#ha_get_state)
- [ha_list_entities](#ha_list_entities)
- [ha_list_services](#ha_list_services)

### [Reinforcement Learning (RL)](#reinforcement-learning-rl)
- [rl_check_status](#rl_check_status)
- [rl_edit_config](#rl_edit_config)
- [rl_get_current_config](#rl_get_current_config)
- [rl_get_results](#rl_get_results)
- [rl_list_environments](#rl_list_environments)
- [rl_list_runs](#rl_list_runs)
- [rl_select_environment](#rl_select_environment)
- [rl_start_training](#rl_start_training)
- [rl_stop_training](#rl_stop_training)
- [rl_test_inference](#rl_test_inference)

### [Messaging](#messaging)
- [send_message](#send_message)

---

## Core System & File Operations

### `clarify`

#### Core Functionality
 Ask the user a question when you need clarification, feedback, or a decision before proceeding. Supports two modes:

1. **Multiple choice** — provide up to 4 choices. The user picks one or types their own answer via a 5th 'Other' option.
2. **Open-ended** — omit choices entirely. The user types a free-form response.

Use this tool when:
- The task is ambiguous and you need the user to choose an approach
- You want post-task feedback ('How did that work out?')
- You want to offer to save a skill or update memory
- A decision has meaningful trade-offs the user should weigh in on

Do NOT use this tool for simple yes/no confirmation of dangerous commands (the terminal tool handles that). Prefer making a reasonable default choice yourself when the decision is low-stakes.

#### API Shape

- `question` (string) (Required): The question to present to the user.
- `choices` (array): Up to 4 answer choices. Omit this parameter entirely to ask an open-ended question. When provided, the UI automatically appends an 'Other (type your answer)' option.


### `memory`

#### Core Functionality
 Save durable information to persistent memory that survives across sessions. Memory is injected into future turns, so keep it compact and focused on facts that will still matter later.

WHEN TO SAVE (do this proactively, don't wait to be asked):
- User corrects you or says 'remember this' / 'don't do that again'
- User shares a preference, habit, or personal detail (name, role, timezone, coding style)
- You discover something about the environment (OS, installed tools, project structure)
- You learn a convention, API quirk, or workflow specific to this user's setup
- You identify a stable fact that will be useful again in future sessions

PRIORITY: User preferences and corrections > environment facts > procedural knowledge. The most valuable memory prevents the user from having to repeat themselves.

Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO state to memory; use session_search to recall those from past transcripts.
If you've discovered a new way to do something, solved a problem that could be necessary later, save it as a skill with the skill tool.

TWO TARGETS:
- 'user': who the user is -- name, role, preferences, communication style, pet peeves
- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned

ACTIONS: add (new entry), replace (update existing -- old_text identifies it), remove (delete -- old_text identifies it).

SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state.

#### API Shape

- `action` (string) (Required): The action to perform.
- `target` (string) (Required): Which memory store: 'memory' for personal notes, 'user' for user profile.
- `content` (string): The entry content. Required for 'add' and 'replace'.
- `old_text` (string): Short unique substring identifying the entry to replace or remove.


### `patch`

#### Core Functionality
 Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. Uses fuzzy matching (9 strategies) so minor whitespace/indentation differences won't break it. Returns a unified diff. Auto-runs syntax checks after editing.

Replace mode (default): find a unique string and replace it.
Patch mode: apply V4A multi-file patches for bulk changes.

#### API Shape

- `mode` (string) (Required): Edit mode: 'replace' for targeted find-and-replace, 'patch' for V4A multi-file patches
- `path` (string): File path to edit (required for 'replace' mode)
- `old_string` (string): Text to find in the file (required for 'replace' mode). Must be unique in the file unless replace_all=true. Include enough surrounding context to ensure uniqueness.
- `new_string` (string): Replacement text (required for 'replace' mode). Can be empty string to delete the matched text.
- `replace_all` (boolean): Replace all occurrences instead of requiring a unique match (default: false)
- `patch` (string): V4A format patch content (required for 'patch' mode). Format:
*** Begin Patch
*** Update File: path/to/file
@@ context hint @@
 context line
-removed line
+added line
*** End Patch


### `process`

#### Core Functionality
 Manage background processes started with terminal(background=true). Actions: 'list' (show all), 'poll' (check status + new output), 'log' (full output with pagination), 'wait' (block until done or timeout), 'kill' (terminate), 'write' (send raw stdin data without newline), 'submit' (send data + Enter, for answering prompts), 'close' (close stdin/send EOF).

#### API Shape

- `action` (string) (Required): Action to perform on background processes
- `session_id` (string): Process session ID (from terminal background output). Required for all actions except 'list'.
- `data` (string): Text to send to process stdin (for 'write' and 'submit' actions)
- `timeout` (integer): Max seconds to block for 'wait' action. Returns partial output on timeout.
- `offset` (integer): Line offset for 'log' action (default: last 200 lines)
- `limit` (integer): Max lines to return for 'log' action


### `read_file`

#### Core Functionality
 Read a text file with line numbers and pagination. Use this instead of cat/head/tail in terminal. Output format: 'LINE_NUM|CONTENT'. Suggests similar filenames if not found. Use offset and limit for large files. Reads exceeding ~100K characters are rejected; use offset and limit to read specific sections of large files. NOTE: Cannot read images or binary files — use vision_analyze for images.

#### API Shape

- `path` (string) (Required): Path to the file to read (absolute, relative, or ~/path)
- `offset` (integer): Line number to start reading from (1-indexed, default: 1)
- `limit` (integer): Maximum number of lines to read (default: 500, max: 2000)


### `search_files`

#### Core Functionality
 Search file contents or find files by name. Use this instead of grep/rg/find/ls in terminal. Ripgrep-backed, faster than shell equivalents.

Content search (target='content'): Regex search inside files. Output modes: full matches with line numbers, file paths only, or match counts.

File search (target='files'): Find files by glob pattern (e.g., '*.py', '*config*'). Also use this instead of ls — results sorted by modification time.

#### API Shape

- `pattern` (string) (Required): Regex pattern for content search, or glob pattern (e.g., '*.py') for file search
- `target` (string): 'content' searches inside file contents, 'files' searches for files by name
- `path` (string): Directory or file to search in (default: current working directory)
- `file_glob` (string): Filter files by pattern in grep mode (e.g., '*.py' to only search Python files)
- `limit` (integer): Maximum number of results to return (default: 50)
- `offset` (integer): Skip first N results for pagination (default: 0)
- `output_mode` (string): Output format for grep mode: 'content' shows matching lines with line numbers, 'files_only' lists file paths, 'count' shows match counts per file
- `context` (integer): Number of context lines before and after each match (grep mode only)


### `session_search`

#### Core Functionality
 Search your long-term memory of past conversations, or browse recent sessions. This is your recall -- every past session is searchable, and this tool summarizes what happened.

TWO MODES:
1. Recent sessions (no query): Call with no arguments to see what was worked on recently. Returns titles, previews, and timestamps. Zero LLM cost, instant. Start here when the user asks what were we working on or what did we do recently.
2. Keyword search (with query): Search for specific topics across all past sessions. Returns LLM-generated summaries of matching sessions.

USE THIS PROACTIVELY when:
- The user says 'we did this before', 'remember when', 'last time', 'as I mentioned'
- The user asks about a topic you worked on before but don't have in current context
- The user references a project, person, or concept that seems familiar but isn't in memory
- You want to check if you've solved a similar problem before
- The user asks 'what did we do about X?' or 'how did we fix Y?'

Don't hesitate to search when it is actually cross-session -- it's fast and cheap. Better to search and confirm than to guess or ask the user to repeat themselves.

Search syntax: keywords joined with OR for broad recall (elevenlabs OR baseten OR funding), phrases for exact match ("docker networking"), boolean (python NOT java), prefix (deploy*). IMPORTANT: Use OR between keywords for best results — FTS5 defaults to AND which misses sessions that only mention some terms. If a broad OR query returns nothing, try individual keyword searches in parallel. Returns summaries of the top matching sessions.

#### API Shape

- `query` (string): Search query — keywords, phrases, or boolean expressions to find in past sessions. Omit this parameter entirely to browse recent sessions instead (returns titles, previews, timestamps with no LLM cost).
- `role_filter` (string): Optional: only search messages from specific roles (comma-separated). E.g. 'user,assistant' to skip tool outputs.
- `limit` (integer): Max sessions to summarize (default: 3, max: 5).


### `terminal`

#### Core Functionality
 Execute shell commands on a Linux environment. Filesystem usually persists between calls.

Do NOT use cat/head/tail to read files — use read_file instead.
Do NOT use grep/rg/find to search — use search_files instead.
Do NOT use ls to list directories — use search_files(target='files') instead.
Do NOT use sed/awk to edit files — use patch instead.
Do NOT use echo/cat heredoc to create files — use write_file instead.
Reserve terminal for: builds, installs, git, processes, scripts, network, package managers, and anything that needs a shell.

Foreground (default): Commands return INSTANTLY when done, even if the timeout is high. Set timeout=300 for long builds/scripts — you'll still get the result in seconds if it's fast. Prefer foreground for short commands.
Background: Set background=true to get a session_id. Two patterns:
  (1) Long-lived processes that never exit (servers, watchers).
  (2) Long-running tasks with notify_on_complete=true — you can keep working on other things and the system auto-notifies you when the task finishes. Great for test suites, builds, deployments, or anything that takes more than a minute.
Use process(action="poll") for progress checks, process(action="wait") to block until done.
Working directory: Use 'workdir' for per-command cwd.
PTY mode: Set pty=true for interactive CLI tools (Codex, Claude Code, Python REPL).

Do NOT use vim/nano/interactive tools without pty=true — they hang without a pseudo-terminal. Pipe git output to cat if it might page.


#### API Shape

- `command` (string) (Required): The command to execute on the VM
- `background` (boolean): Run the command in the background. Two patterns: (1) Long-lived processes that never exit (servers, watchers). (2) Long-running tasks paired with notify_on_complete=true — you can keep working and get notified when the task finishes. For short commands, prefer foreground with a generous timeout instead.
- `timeout` (integer): Max seconds to wait (default: 180, foreground max: 600). Returns INSTANTLY when command finishes — set high for long tasks, you won't wait unnecessarily. Foreground timeout above 600s is rejected; use background=true for longer commands.
- `workdir` (string): Working directory for this command (absolute path). Defaults to the session working directory.
- `pty` (boolean): Run in pseudo-terminal (PTY) mode for interactive CLI tools like Codex, Claude Code, or Python REPL. Only works with local and SSH backends. Default: false.
- `notify_on_complete` (boolean): When true (and background=true), you'll be automatically notified when the process finishes — no polling needed. Use this for tasks that take a while (tests, builds, deployments) so you can keep working on other things in the meantime.
- `watch_patterns` (array): List of strings to watch for in background process output. When any pattern matches a line of output, you'll be notified with the matching text — like notify_on_complete but triggers mid-process on specific output. Use for monitoring logs, watching for errors, or waiting for specific events (e.g. ["ERROR", "FAIL", "listening on port"]).


### `todo`

#### Core Functionality
 Manage your task list for the current session. Use for complex tasks with 3+ steps or when the user provides multiple tasks. Call with no parameters to read the current list.

Writing:
- Provide 'todos' array to create/update items
- merge=false (default): replace the entire list with a fresh plan
- merge=true: update existing items by id, add any new ones

Each item: {id: string, content: string, status: pending|in_progress|completed|cancelled}
List order is priority. Only ONE item in_progress at a time.
Mark items completed immediately when done. If something fails, cancel it and add a revised item.

Always returns the full current list.

#### API Shape

- `todos` (array): Task items to write. Omit to read current list.
- `merge` (boolean): true: update existing items by id, add new ones. false (default): replace the entire list.


### `write_file`

#### Core Functionality
 Write content to a file, completely replacing existing content. Use this instead of echo/cat heredoc in terminal. Creates parent directories automatically. OVERWRITES the entire file — use 'patch' for targeted edits.

#### API Shape

- `path` (string) (Required): Path to the file to write (will be created if it doesn't exist, overwritten if it does)
- `content` (string) (Required): Complete content to write to the file

## Browser Automation

### `browser_back`

#### Core Functionality
 Navigate back to the previous page in browser history. Requires browser_navigate to be called first.

#### API Shape



### `browser_click`

#### Core Functionality
 Click on an element identified by its ref ID from the snapshot (e.g., '@e5'). The ref IDs are shown in square brackets in the snapshot output. Requires browser_navigate and browser_snapshot to be called first.

#### API Shape

- `ref` (string) (Required): The element reference from the snapshot (e.g., '@e5', '@e12')


### `browser_console`

#### Core Functionality
 Get browser console output and JavaScript errors from the current page. Returns console.log/warn/error/info messages and uncaught JS exceptions. Use this to detect silent JavaScript errors, failed API calls, and application warnings. Requires browser_navigate to be called first. When 'expression' is provided, evaluates JavaScript in the page context and returns the result — use this for DOM inspection, reading page state, or extracting data programmatically.

#### API Shape

- `clear` (boolean): If true, clear the message buffers after reading
- `expression` (string): JavaScript expression to evaluate in the page context. Runs in the browser like DevTools console — full access to DOM, window, document. Return values are serialized to JSON. Example: 'document.title' or 'document.querySelectorAll("a").length'


### `browser_get_images`

#### Core Functionality
 Get a list of all images on the current page with their URLs and alt text. Useful for finding images to analyze with the vision tool. Requires browser_navigate to be called first.

#### API Shape



### `browser_navigate`

#### Core Functionality
 Navigate to a URL in the browser. Initializes the session and loads the page. Must be called before other browser tools. For simple information retrieval, prefer web_search or web_extract (faster, cheaper). Use browser tools when you need to interact with a page (click, fill forms, dynamic content). Returns a compact page snapshot with interactive elements and ref IDs — no need to call browser_snapshot separately after navigating.

#### API Shape

- `url` (string) (Required): The URL to navigate to (e.g., 'https://example.com')


### `browser_press`

#### Core Functionality
 Press a keyboard key. Useful for submitting forms (Enter), navigating (Tab), or keyboard shortcuts. Requires browser_navigate to be called first.

#### API Shape

- `key` (string) (Required): Key to press (e.g., 'Enter', 'Tab', 'Escape', 'ArrowDown')


### `browser_scroll`

#### Core Functionality
 Scroll the page in a direction. Use this to reveal more content that may be below or above the current viewport. Requires browser_navigate to be called first.

#### API Shape

- `direction` (string) (Required): Direction to scroll


### `browser_snapshot`

#### Core Functionality
 Get a text-based snapshot of the current page's accessibility tree. Returns interactive elements with ref IDs (like @e1, @e2) for browser_click and browser_type. full=false (default): compact view with interactive elements. full=true: complete page content. Snapshots over 8000 chars are truncated or LLM-summarized. Requires browser_navigate first. Note: browser_navigate already returns a compact snapshot — use this to refresh after interactions that change the page, or with full=true for complete content.

#### API Shape

- `full` (boolean): If true, returns complete page content. If false (default), returns compact view with interactive elements only.


### `browser_type`

#### Core Functionality
 Type text into an input field identified by its ref ID. Clears the field first, then types the new text. Requires browser_navigate and browser_snapshot to be called first.

#### API Shape

- `ref` (string) (Required): The element reference from the snapshot (e.g., '@e3')
- `text` (string) (Required): The text to type into the field


### `browser_vision`

#### Core Functionality
 Take a screenshot of the current page and analyze it with vision AI. Use this when you need to visually understand what's on the page - especially useful for CAPTCHAs, visual verification challenges, complex layouts, or when the text snapshot doesn't capture important visual information. Returns both the AI analysis and a screenshot_path that you can share with the user by including MEDIA:<screenshot_path> in your response. Requires browser_navigate to be called first.

#### API Shape

- `question` (string) (Required): What you want to know about the page visually. Be specific about what you're looking for.
- `annotate` (boolean): If true, overlay numbered [N] labels on interactive elements. Each [N] maps to ref @eN for subsequent browser commands. Useful for QA and spatial reasoning about page layout.


## Execution & Delegation

### `cronjob`

#### Core Functionality
 Manage scheduled cron jobs with a single compressed tool.

Use action='create' to schedule a new job from a prompt or one or more skills.
Use action='list' to inspect jobs.
Use action='update', 'pause', 'resume', 'remove', or 'run' to manage an existing job.

To stop a job the user no longer wants: first action='list' to find the job_id, then action='remove' with that job_id. Never guess job IDs — always list first.

Jobs run in a fresh session with no current-chat context, so prompts must be self-contained.
If skills are provided on create, the future cron run loads those skills in order, then follows the prompt as the task instruction.
On update, passing skills=[] clears attached skills.

NOTE: The agent's final response is auto-delivered to the target. Put the primary
user-facing content in the final response. Cron jobs run autonomously with no user
present — they cannot ask questions or request clarification.

Important safety rule: cron-run sessions should not recursively schedule more cron jobs.

#### API Shape

- `action` (string) (Required): One of: create, list, update, pause, resume, remove, run
- `job_id` (string): Required for update/pause/resume/remove/run
- `prompt` (string): For create: the full self-contained prompt. If skills are also provided, this becomes the task instruction paired with those skills.
- `schedule` (string): For create/update: '30m', 'every 2h', '0 9 * * *', or ISO timestamp
- `name` (string): Optional human-friendly name
- `repeat` (integer): Optional repeat count. Omit for defaults (once for one-shot, forever for recurring).
- `deliver` (string): Omit this parameter to auto-deliver back to the current chat and topic (recommended). Auto-detection preserves thread/topic context. Only set explicitly when the user asks to deliver somewhere OTHER than the current conversation. Values: 'origin' (same as omitting), 'local' (no delivery, save only), or platform:chat_id:thread_id for a specific destination. Examples: 'telegram:-1001234567890:17585', 'discord:#engineering', 'sms:+15551234567'. WARNING: 'platform:chat_id' without :thread_id loses topic targeting.
- `skills` (array): Optional ordered list of skill names to load before executing the cron prompt. On update, pass an empty array to clear attached skills.
- `model` (object): Optional per-job model override. If provider is omitted, the current main provider is pinned at creation time so the job stays stable.
- `script` (string): Optional path to a Python script that runs before each cron job execution. Its stdout is injected into the prompt as context. Use for data collection and change detection. Relative paths resolve under ~/.hermes/scripts/. On update, pass empty string to clear.


### `delegate_task`

#### Core Functionality
 Spawn one or more subagents to work on tasks in isolated contexts. Each subagent gets its own conversation, terminal session, and toolset. Only the final summary is returned -- intermediate tool results never enter your context window.

TWO MODES (one of 'goal' or 'tasks' is required):
1. Single task: provide 'goal' (+ optional context, toolsets)
2. Batch (parallel): provide 'tasks' array with up to 3 items. All run concurrently and results are returned together.

WHEN TO USE delegate_task:
- Reasoning-heavy subtasks (debugging, code review, research synthesis)
- Tasks that would flood your context with intermediate data
- Parallel independent workstreams (research A and B simultaneously)

WHEN NOT TO USE (use these instead):
- Mechanical multi-step work with no reasoning needed -> use execute_code
- Single tool call -> just call the tool directly
- Tasks needing user interaction -> subagents cannot use clarify

IMPORTANT:
- Subagents have NO memory of your conversation. Pass all relevant info (file paths, error messages, constraints) via the 'context' field.
- Subagents CANNOT call: delegate_task, clarify, memory, send_message, execute_code.
- Each subagent gets its own terminal session (separate working directory and state).
- Results are always returned as an array, one entry per task.

#### API Shape

- `goal` (string): What the subagent should accomplish. Be specific and self-contained -- the subagent knows nothing about your conversation history.
- `context` (string): Background information the subagent needs: file paths, error messages, project structure, constraints. The more specific you are, the better the subagent performs.
- `toolsets` (array): Toolsets to enable for this subagent. Default: inherits your enabled toolsets. Available toolsets: 'browser', 'cronjob', 'file', 'homeassistant', 'image_gen', 'search', 'session_search', 'skills', 'terminal', 'todo', 'tts', 'vision', 'web'. Common patterns: ['terminal', 'file'] for code work, ['web'] for research, ['browser'] for web interaction, ['terminal', 'file', 'web'] for full-stack tasks.
- `tasks` (array): Batch mode: tasks to run in parallel (limit configurable via delegation.max_concurrent_children, default 3). Each gets its own subagent with isolated context and terminal session. When provided, top-level goal/context/toolsets are ignored.
- `max_iterations` (integer): Max tool-calling turns per subagent (default: 50). Only set lower for simple tasks.
- `acp_command` (string): Override ACP command for child agents (e.g. 'claude', 'copilot'). When set, children use ACP subprocess transport instead of inheriting the parent's transport. Enables spawning Claude Code (claude --acp --stdio) or other ACP-capable agents from any parent, including Discord/Telegram/CLI.
- `acp_args` (array): Arguments for the ACP command (default: ['--acp', '--stdio']). Only used when acp_command is set. Example: ['--acp', '--stdio', '--model', 'claude-opus-4-6']


### `execute_code`

#### Core Functionality
 Run a Python script that can call Hermes tools programmatically. Use this when you need 3+ tool calls with processing logic between them, need to filter/reduce large tool outputs before they enter your context, need conditional branching (if X then Y else Z), or need to loop (fetch N pages, process N files, retry on failure).

Use normal tool calls instead when: single tool call with no processing, you need to see the full result and apply complex reasoning, or the task requires interactive user input.

Available via `from hermes_tools import ...`:

  web_search(query: str, limit: int = 5) -> dict
    Returns {"data": {"web": [{"url", "title", "description"}, ...]}}
  web_extract(urls: list[str]) -> dict
    Returns {"results": [{"url", "title", "content", "error"}, ...]} where content is markdown
  read_file(path: str, offset: int = 1, limit: int = 500) -> dict
    Lines are 1-indexed. Returns {"content": "...", "total_lines": N}
  write_file(path: str, content: str) -> dict
    Always overwrites the entire file.
  search_files(pattern: str, target="content", path=".", file_glob=None, limit=50) -> dict
    target: "content" (search inside files) or "files" (find files by name). Returns {"matches": [...]}
  patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> dict
    Replaces old_string with new_string in the file.
  terminal(command: str, timeout=None, workdir=None) -> dict
    Foreground only (no background/pty). Returns {"output": "...", "exit_code": N}

Limits: 5-minute timeout, 50KB stdout cap, max 50 tool calls per script. terminal() is foreground-only (no background or pty).

Print your final result to stdout. Use Python stdlib (json, re, math, csv, datetime, collections, etc.) for processing between tool calls.

Also available (no import needed — built into hermes_tools):
  json_parse(text: str) — json.loads with strict=False; use for terminal() output with control chars
  shell_quote(s: str) — shlex.quote(); use when interpolating dynamic strings into shell commands
  retry(fn, max_attempts=3, delay=2) — retry with exponential backoff for transient failures

#### API Shape

- `code` (string) (Required): Python code to execute. Import tools with `from hermes_tools import web_search, terminal, ...` and print your final result to stdout.


### `mixture_of_agents`

#### Core Functionality
 Route a hard problem through multiple frontier LLMs collaboratively. Makes 5 API calls (4 reference models + 1 aggregator) with maximum reasoning effort — use sparingly for genuinely difficult problems. Best for: complex math, advanced algorithms, multi-step analytical reasoning, problems benefiting from diverse perspectives.

#### API Shape

- `user_prompt` (string) (Required): The complex query or problem to solve using multiple AI models. Should be a challenging problem that benefits from diverse perspectives and collaborative reasoning.


## Web & Media

### `image_generate`

#### Core Functionality
 Generate high-quality images from text prompts using FAL.ai. The underlying model is user-configured (default: FLUX 2 Klein 9B, sub-1s generation) and is not selectable by the agent. Returns a single image URL. Display it using markdown: ![description](URL)

#### API Shape

- `prompt` (string) (Required): The text prompt describing the desired image. Be detailed and descriptive.
- `aspect_ratio` (string): The aspect ratio of the generated image. 'landscape' is 16:9 wide, 'portrait' is 16:9 tall, 'square' is 1:1.


### `text_to_speech`

#### Core Functionality
 Convert text to speech audio. Returns a MEDIA: path that the platform delivers as a voice message. On Telegram it plays as a voice bubble, on Discord/WhatsApp as an audio attachment. In CLI mode, saves to ~/voice-memos/. Voice and provider are user-configured, not model-selected.

#### API Shape

- `text` (string) (Required): The text to convert to speech. Keep under 4000 characters.
- `output_path` (string): Optional custom file path to save the audio. Defaults to ~/.hermes/audio_cache/<timestamp>.mp3


### `vision_analyze`

#### Core Functionality
 Analyze images using AI vision. Provides a comprehensive description and answers a specific question about the image content.

#### API Shape

- `image_url` (string) (Required): Image URL (http/https) or local file path to analyze.
- `question` (string) (Required): Your specific question or request about the image to resolve. The AI will automatically provide a complete image description AND answer your specific question.


### `web_extract`

#### Core Functionality
 Extract content from web page URLs. Returns page content in markdown format. Also works with PDF URLs (arxiv papers, documents, etc.) — pass the PDF link directly and it converts to markdown text. Pages under 5000 chars return full markdown; larger pages are LLM-summarized and capped at ~5000 chars per page. Pages over 2M chars are refused. If a URL fails or times out, use the browser tool to access it instead.

#### API Shape

- `urls` (array) (Required): List of URLs to extract content from (max 5 URLs per call)


### `web_search`

#### Core Functionality
 Search the web for information on any topic. Returns up to 5 relevant results with titles, URLs, and descriptions.

#### API Shape

- `query` (string) (Required): The search query to look up on the web


## Skill Management

### `skill_manage`

#### Core Functionality
 Manage skills (create, update, delete). Skills are your procedural memory — reusable approaches for recurring task types. New skills go to ~/.hermes/skills/; existing skills can be modified wherever they live.

Actions: create (full SKILL.md + optional category), patch (old_string/new_string — preferred for fixes), edit (full SKILL.md rewrite — major overhauls only), delete, write_file, remove_file.

Create when: complex task succeeded (5+ calls), errors overcome, user-corrected approach worked, non-trivial workflow discovered, or user asks you to remember a procedure.
Update when: instructions stale/wrong, OS-specific failures, missing steps or pitfalls found during use. If you used a skill and hit issues not covered by it, patch it immediately.

After difficult/iterative tasks, offer to save as a skill. Skip for simple one-offs. Confirm with user before creating/deleting.

Good skills: trigger conditions, numbered steps with exact commands, pitfalls section, verification steps. Use skill_view() to see format examples.

#### API Shape

- `action` (string) (Required): The action to perform.
- `name` (string) (Required): Skill name (lowercase, hyphens/underscores, max 64 chars). Must match an existing skill for patch/edit/delete/write_file/remove_file.
- `content` (string): Full SKILL.md content (YAML frontmatter + markdown body). Required for 'create' and 'edit'. For 'edit', read the skill first with skill_view() and provide the complete updated text.
- `old_string` (string): Text to find in the file (required for 'patch'). Must be unique unless replace_all=true. Include enough surrounding context to ensure uniqueness.
- `new_string` (string): Replacement text (required for 'patch'). Can be empty string to delete the matched text.
- `replace_all` (boolean): For 'patch': replace all occurrences instead of requiring a unique match (default: false).
- `category` (string): Optional category/domain for organizing the skill (e.g., 'devops', 'data-science', 'mlops'). Creates a subdirectory grouping. Only used with 'create'.
- `file_path` (string): Path to a supporting file within the skill directory. For 'write_file'/'remove_file': required, must be under references/, templates/, scripts/, or assets/. For 'patch': optional, defaults to SKILL.md if omitted.
- `file_content` (string): Content for the file. Required for 'write_file'.


### `skill_view`

#### Core Functionality
 Skills allow for loading information about specific tasks and workflows, as well as scripts and templates. Load a skill's full content or access its linked files (references, templates, scripts). First call returns SKILL.md content plus a 'linked_files' dict showing available references/templates/scripts. To access those, call again with file_path parameter.

#### API Shape

- `name` (string) (Required): The skill name (use skills_list to see available skills). For plugin-provided skills, use the qualified form 'plugin:skill' (e.g. 'superpowers:writing-plans').
- `file_path` (string): OPTIONAL: Path to a linked file within the skill (e.g., 'references/api.md', 'templates/config.yaml', 'scripts/validate.py'). Omit to get the main SKILL.md content.


### `skills_list`

#### Core Functionality
 List available skills (name + description). Use skill_view(name) to load full content.

#### API Shape

- `category` (string): Optional category filter to narrow results


## Home Assistant (Smart Home)

### `ha_call_service`

#### Core Functionality
 Call a Home Assistant service to control a device. Use ha_list_services to discover available services and their parameters for each domain.

#### API Shape

- `domain` (string) (Required): Service domain (e.g. 'light', 'switch', 'climate', 'cover', 'media_player', 'fan', 'scene', 'script').
- `service` (string) (Required): Service name (e.g. 'turn_on', 'turn_off', 'toggle', 'set_temperature', 'set_hvac_mode', 'open_cover', 'close_cover', 'set_volume_level').
- `entity_id` (string): Target entity ID (e.g. 'light.living_room'). Some services (like scene.turn_on) may not need this.
- `data` (string): Additional service data as a JSON string. Examples: {"brightness": 255, "color_name": "blue"} for lights, {"temperature": 22, "hvac_mode": "heat"} for climate, {"volume_level": 0.5} for media players.


### `ha_get_state`

#### Core Functionality
 Get the detailed state of a single Home Assistant entity, including all attributes (brightness, color, temperature setpoint, sensor readings, etc.).

#### API Shape

- `entity_id` (string) (Required): The entity ID to query (e.g. 'light.living_room', 'climate.thermostat', 'sensor.temperature').


### `ha_list_entities`

#### Core Functionality
 List Home Assistant entities. Optionally filter by domain (light, switch, climate, sensor, binary_sensor, cover, fan, etc.) or by area name (living room, kitchen, bedroom, etc.).

#### API Shape

- `domain` (string): Entity domain to filter by (e.g. 'light', 'switch', 'climate', 'sensor', 'binary_sensor', 'cover', 'fan', 'media_player'). Omit to list all entities.
- `area` (string): Area/room name to filter by (e.g. 'living room', 'kitchen'). Matches against entity friendly names. Omit to list all.


### `ha_list_services`

#### Core Functionality
 List available Home Assistant services (actions) for device control. Shows what actions can be performed on each device type and what parameters they accept. Use this to discover how to control devices found via ha_list_entities.

#### API Shape

- `domain` (string): Filter by domain (e.g. 'light', 'climate', 'switch'). Omit to list services for all domains.


## Reinforcement Learning (RL)

### `rl_check_status`

#### Core Functionality
 Get status and metrics for a training run. RATE LIMITED: enforces 30-minute minimum between checks for the same run. Returns WandB metrics: step, state, reward_mean, loss, percent_correct.

#### API Shape

- `run_id` (string) (Required): The run ID from rl_start_training()


### `rl_edit_config`

#### Core Functionality
 Update a configuration field. Use rl_get_current_config() first to see all available fields for the selected environment. Each environment has different configurable options. Infrastructure settings (tokenizer, URLs, lora_rank, learning_rate) are locked.

#### API Shape

- `field` (string) (Required): Name of the field to update (get available fields from rl_get_current_config)
- `value` (any) (Required): New value for the field


### `rl_get_current_config`

#### Core Functionality
 Get the current environment configuration. Returns only fields that can be modified: group_size, max_token_length, total_steps, steps_per_eval, use_wandb, wandb_name, max_num_workers.

#### API Shape



### `rl_get_results`

#### Core Functionality
 Get final results and metrics for a completed training run. Returns final metrics and path to trained weights.

#### API Shape

- `run_id` (string) (Required): The run ID to get results for


### `rl_list_environments`

#### Core Functionality
 List all available RL environments. Returns environment names, paths, and descriptions. TIP: Read the file_path with file tools to understand how each environment works (verifiers, data loading, rewards).

#### API Shape



### `rl_list_runs`

#### Core Functionality
 List all training runs (active and completed) with their status.

#### API Shape



### `rl_select_environment`

#### Core Functionality
 Select an RL environment for training. Loads the environment's default configuration. After selecting, use rl_get_current_config() to see settings and rl_edit_config() to modify them.

#### API Shape

- `name` (string) (Required): Name of the environment to select (from rl_list_environments)


### `rl_start_training`

#### Core Functionality
 Start a new RL training run with the current environment and config. Most training parameters (lora_rank, learning_rate, etc.) are fixed. Use rl_edit_config() to set group_size, batch_size, wandb_project before starting. WARNING: Training takes hours.

#### API Shape



### `rl_stop_training`

#### Core Functionality
 Stop a running training job. Use if metrics look bad, training is stagnant, or you want to try different settings.

#### API Shape

- `run_id` (string) (Required): The run ID to stop


### `rl_test_inference`

#### Core Functionality
 Quick inference test for any environment. Runs a few steps of inference + scoring using OpenRouter. Default: 3 steps x 16 completions = 48 rollouts per model, testing 3 models = 144 total. Tests environment loading, prompt construction, inference parsing, and verifier logic. Use BEFORE training to catch issues.

#### API Shape

- `num_steps` (integer): Number of steps to run (default: 3, recommended max for testing)
- `group_size` (integer): Completions per step (default: 16, like training)
- `models` (array): Optional list of OpenRouter model IDs. Default: qwen/qwen3-8b, z-ai/glm-4.7-flash, minimax/minimax-m2.7


## Messaging

### `send_message`

#### Core Functionality
 Send a message to a connected messaging platform, or list available targets.

IMPORTANT: When the user asks to send to a specific channel or person (not just a bare platform name), call send_message(action='list') FIRST to see available targets, then send to the correct one.
If the user just says a platform name like 'send to telegram', send directly to the home channel without listing first.

#### API Shape

- `action` (string): Action to perform. 'send' (default) sends a message. 'list' returns all available channels/contacts across connected platforms.
- `target` (string): Delivery target. Format: 'platform' (uses home channel), 'platform:#channel-name', 'platform:chat_id', or 'platform:chat_id:thread_id' for Telegram topics and Discord threads. Examples: 'telegram', 'telegram:-1001234567890:17585', 'discord:999888777:555444333', 'discord:#bot-home', 'slack:#engineering', 'signal:+155****4567', 'matrix:!roomid:server.org', 'matrix:@user:server.org'
- `message` (string): The message text to send

