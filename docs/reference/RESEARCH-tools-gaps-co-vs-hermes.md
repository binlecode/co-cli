# RESEARCH: Tool Parity & Lifecycle Gaps — `co-cli` vs Hermes

> **Last refreshed:** 2026-05-08. Tool inventories re-counted directly from each
> repo (co-cli `NATIVE_TOOLS` tuple; hermes `registry.register()` AST scan).
> Active in-flight items linked to exec-plans where applicable.

Code-verified cross-review of co-cli's tool surface and lifecycle against hermes-agent.
Per-tool parity matrix first (what each co tool looks like in hermes, porting implications),
then architecture-level gaps and anti-patterns.

## Sources

### `co-cli` (33 native tools)

- [`co_cli/agent/_native_toolset.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py) — `NATIVE_TOOLS` tuple, `@agent_tool` policy, `_make_prepare(check_fn)`, approval-resume filter
- [`co_cli/agent/mcp.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/mcp.py)
- [`co_cli/agent/core.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/core.py)
- [`co_cli/tools/lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/lifecycle.py)
- [`co_cli/tools/categories.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/categories.py) — `PATH_NORMALIZATION_TOOLS`, `FILE_TOOLS`, `COMPACTABLE_TOOLS`
- [`co_cli/tools/approvals.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py)
- [`co_cli/tools/agent_tool.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/agent_tool.py)
- [`co_cli/tools/tool_io.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py) — `tool_output`/`tool_output_raw`, `spill_if_oversized`, `SPILL_THRESHOLD_CHARS`
- [`co_cli/tools/background.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/background.py) — `BackgroundTaskState`, `output_lines` ring buffer
- Tool implementations (each in its own subpackage):
  `co_cli/tools/system/{user_input,capabilities}.py`, `co_cli/tools/todo/rw.py`,
  `co_cli/tools/shell/execute.py`, `co_cli/tools/code/execute.py`,
  `co_cli/tools/tasks/control.py`, `co_cli/tools/agents/delegation.py`,
  `co_cli/tools/{files,memory,web,google,obsidian}/*.py`
- Memory library: `co_cli/memory/` — unified knowledge artifacts + session transcripts; FTS5 (BM25) over both kinds via `MemoryStore.search(sources=...)`
- [`docs/specs/tools.md`](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md), [`docs/specs/memory.md`](/Users/binle/workspace_genai/co-cli/docs/specs/memory.md)

### Hermes (61 registered tools, as of 2026-05-08)

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py` — singleton `ToolRegistry`, AST auto-discovery, `check_fn`, 30 s `check_fn` TTL cache
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py` + `tools/schema_sanitizer.py` (MCP `inputSchema` normalization at registration time)
- `/Users/binle/workspace_genai/hermes-agent/toolsets.py` — named toolset profiles (web, file, terminal, skills, browser, browser-cdp, …) plus per-platform supersets (`hermes-cli`, `hermes-discord`, `hermes-telegram`, …)
- Core tool implementations: `tools/{clarify,memory,todo,session_search,file,terminal,process_registry,code_execution,delegate,skills,skill_manager,web,vision,image_generation,tts,browser,browser_cdp,browser_dialog,cronjob,send_message,homeassistant,mixture_of_agents,rl_training}_tool.py`
- Platform-specific tool implementations (new since prior survey): `tools/{discord,feishu_doc,feishu_drive,yuanbao}_tool*.py`
- `/Users/binle/workspace_genai/hermes-agent/run_agent.py`

**Tool inventory (61, sorted)**: `browser_back`, `browser_cdp`, `browser_click`, `browser_console`, `browser_dialog`, `browser_get_images`, `browser_navigate`, `browser_press`, `browser_scroll`, `browser_snapshot`, `browser_type`, `browser_vision`, `clarify`, `cronjob`, `delegate_task`, `discord`, `discord_admin`, `execute_code`, `feishu_doc_read`, `feishu_drive_add_comment`, `feishu_drive_list_comment_replies`, `feishu_drive_list_comments`, `feishu_drive_reply_comment`, `ha_call_service`, `ha_get_state`, `ha_list_entities`, `ha_list_services`, `image_generate`, `memory`, `mixture_of_agents`, `patch`, `process`, `read_file`, `rl_check_status`, `rl_edit_config`, `rl_get_current_config`, `rl_get_results`, `rl_list_environments`, `rl_list_runs`, `rl_select_environment`, `rl_start_training`, `rl_stop_training`, `rl_test_inference`, `search_files`, `send_message`, `session_search`, `skill_manage`, `skill_view`, `skills_list`, `terminal`, `text_to_speech`, `todo`, `vision_analyze`, `web_extract`, `web_search`, `write_file`, `yb_query_group_info`, `yb_query_group_members`, `yb_search_sticker`, `yb_send_dm`, `yb_send_sticker`.

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with an `@agent_tool(...)` decorator that
attaches `ToolInfo` (visibility, approval, concurrency, integration, `requires_config`,
`spill_threshold_chars`, optional `check_fn`) at definition site. Tools are a flat tuple
(`NATIVE_TOOLS`) registered through `_build_native_toolset()`, with `defer_loading`
derived from visibility and a per-turn `prepare` callback wrapping `check_fn` when present.
Deferred approval is handled by the SDK and the `_approval_resume_filter`; runtime path
rewriting and JSON-arg repair live in `CoToolLifecycle`.

Hermes uses a thread-safe singleton `ToolRegistry` with AST-based auto-discovery
(`registry.py:57–74` scans `tools/*.py` for module-level `registry.register()`),
per-tool `check_fn` for runtime availability with a 30 s TTL cache (`_check_fn_cached`,
`registry.py:113–134`) so probes against external state (Docker daemon, Modal SDK,
Playwright binary) don't fire every `get_definitions()` call. `toolset` strings group
tools into named profiles (`toolsets.py`); approval flows through a blocking gateway;
schemas are supplied as raw JSON-Schema dicts and normalized via
`schema_sanitizer.py` at MCP-ingestion time.

Both are production-grade — the difference is **what surface area each
exposes to the model** and **how each handles "tool not currently usable"**
(build-time gate vs runtime check_fn).

---

## 1. Per-Tool Parity Matrix

Columns: **co-cli tool** · **hermes equivalent** · **Parity** (✓ = same semantics,
~ = partial / different shape, ✗ = no equivalent) · **Gap & porting notes**.

### 1.1 Interaction & Session Control

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `clarify(questions: list[dict], user_answers: list[str] \| None)` (`system/user_input.py`) | `clarify` (`clarify_tool.py`) | ~ | Same user-facing intent. co-cli now batches *all related* questions in one call (see §4.1 update) — `questions[]` is a list of `{question, options, multiple}` dicts; `user_answers[]` is system-injected on resume via `ToolApproved(override_args=...)`. Hermes passes a `callback` kw to its blocking gateway. The signature change closes most of the duplicate-call pathology, but the underlying SDK answer channel is still tied to the tool-call identity. |
| `capabilities_check()` (`system/capabilities.py`) | *(none — hermes exposes `registry.get_available_toolsets()` to the CLI but not to the model)* | ✗ | co-cli uniquely exposes the runtime capability surface to the model: available tools (always/deferred/approval-gated), degraded integrations, MCP server health, and reasoning model status. `/doctor` wraps it with a triage format but is not the only consumer — the model calls it directly for any "what can you do / why is X broken" query. No port needed — hermes's `check_fn` model means the registry itself is the introspection surface. |
| `todo_write(todos)` / `todo_read()` (`todo/rw.py`) | `todo` (`todo_tool.py`, single op-based) | ~ | Hermes collapses read/write into one `todo` tool with a `merge` flag and persisted store. co-cli keeps them separate and stores state in `CoSessionState.session_todos` (plain `list[dict]`, session-scoped only). co-cli's schema adds a `priority` field (high/medium/low) per item that hermes's tool lacks. **Port consideration:** hermes's `merge` semantics are a useful addition for incremental updates without re-submitting the full list. |

### 1.2 Workspace & File Operations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `file_find(path, pattern, max_entries)` (`files/read.py`) | `search_files` (glob branch, `file_tools.py`) | ~ | Hermes unifies glob + grep in one tool. co-cli keeps file discovery separate from content search, which is the cleaner abstraction. |
| `file_read(path, start_line, end_line)` (`files/read.py`) | `read_file` (`file_tools.py`) | ✓ | Both support line ranges; both cap size. Hermes's cap is character-based (`_DEFAULT_MAX_READ_CHARS=100_000`) and configurable; co-cli uses line count (default 500, max 2000) + a 500 KB full-read gate + 2000-char per-line truncation, and opts out of `tool_output()` spilling via `spill_threshold_chars=math.inf` (the per-line caps already enforce shape). **Port:** co-cli's 2000-char per-line truncation is a real practical guard hermes does not have — keep. |
| `file_search(pattern, path, glob, case_insensitive, output_mode, context_lines, head_limit, offset)` | `search_files` (grep branch) | ~ | co-cli has richer output modes (count/files/content) and shell-rg fast path via `_grep_shell`. `glob` already covers the "grep within matching files" case in one call. Hermes's `search_files` is simpler. **No port needed.** |
| `file_write(path, content)` | `write_file` | ✓ | Both deferred-approval; both write atomically. co-cli adds `resource_locks` + `file_read_mtimes` staleness check — **hermes lacks both**. |
| `file_patch(mode, path, old_string, new_string, replace_all, show_diff, patch)` | `patch` | ✓ | Both support multi-file V4A patches. co-cli's `mode="replace"` (default) does single-file fuzzy `old_string`/`new_string` edits with four-strategy fallback (exact, line-trimmed, indent-stripped, escape-expanded); `mode="patch"` accepts a V4A multi-file string (`*** Update File:` / `*** Add File:` / `*** Delete File:`) parsed via `files/_v4a.py`. Hermes's `patch` is V4A-only via `patch_parser.py`. co-cli's auto-lint on `.py` + read-before-write enforcement (`file_partial_reads`) are **co-cli-only** guardrails worth keeping. |

### 1.3 Memory & Session Recall

> **Refactor note (2026-Q1, current):** `co_cli/knowledge/` was unified into `co_cli/memory/`.
> The recall surface is **search-driven** — there is no `memory_list` and no `memory_read`
> tool. Browsing is `memory_search` with an empty or kind-filtered query; full-body artifact
> reads use the generic `file_read` against the `path` field that `memory_search` surfaces.
> A `memory_read_session_turn(session_id, start_line, end_line)` reader exists in source
> (`tools/memory/read.py`) but is intentionally **not registered** — see `docs/specs/memory.md`.
>
> The active model-callable surface is three tools: `memory_search`, `memory_create`,
> `memory_modify`. Canon hits ship full body inline; session hits ship chunk citations
> with line ranges (no LLM summarization in the search path).

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `memory_search(query, kinds, limit)` (`tools/memory/recall.py`) | `session_search(query, role_filter, limit)` (`session_search_tool.py`) | ~ | One call covers artifacts (BM25 FTS5 — three-pass: canon priority → user priority → waterfall over rule/article/note with combined count + size cap) and session transcripts (chunk-level FTS5 hits with `start_line`/`end_line`). FTS5 boolean syntax (`OR`, `NOT`, `"phrase"`, `prefix*`) is in. Empty query triggers a no-LLM recent-sessions browse mode (excludes the active session). Up to 3 unique sessions returned regardless of `limit`. **Remaining gap vs hermes:** no `role_filter` (assistant vs user messages). |
| `memory_create(content, artifact_kind, title, description, tags, source_url, decay_protected)` (`tools/memory/write.py`) | *(none)* | ✗ | Unique to co-cli. Saves new artifacts (`user`/`rule`/`article`/`note`) with URL-keyed dedup and optional Jaccard consolidation. |
| `memory_modify(slug, action, content, target)` (`tools/memory/write.py`) | *(none)* | ✗ | Unique to co-cli. `action="append"` or `"replace"` with exact-match guard. Hermes's `memory` tool has `action=replace` with `old_text` but operates on a frozen `MEMORY.md`, not addressable artifacts. |
| *(none)* | `memory(action, target, content, old_text)` (`memory_tool.py`) | — | **Hermes-only.** Frozen-snapshot memory (MEMORY.md + USER.md injected into system prompt). co-cli covers the same use case through the *auto memory* prompt layer + `memory_create/modify` — different architecture, not a missing tool. |

### 1.4 Web

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `web_search(query, max_results=5, domains=None)` (`web/search.py`) | `web_search(query, limit=5)` (`web_tools.py`) | ✓ | Both Brave-backed. co-cli adds an SSRF-hardened `web/_ssrf.py` + domain filter; hermes uses a generic `url_safety.py`. **Port:** domains filter is a co-cli-specific policy knob; no porting needed. |
| `web_fetch(url, format="markdown", timeout=15)` (`web/fetch.py`) | `web_extract(urls: list[str])` | ~ | Hermes accepts up to 5 URLs per call — **real latency advantage**. co-cli accepts a single URL but exposes `format` (markdown/html/text) and per-call `timeout` knobs hermes lacks. **Port candidate (still open):** add `urls: list[str]` with a parallel fetch + per-URL timeout. |

### 1.5 Execution, Jobs & Delegation

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `shell(cmd, timeout=120, workdir=None)` (`shell/execute.py`) | `terminal(command, background, timeout, workdir, pty, notify_on_complete, watch_patterns)` (`terminal_tool.py`) | ~ | Hermes's `terminal` is a superset: built-in background mode with `notify_on_complete` and `watch_patterns` (regex pattern notifier mid-process) and **PTY support** for interactive CLIs. co-cli keeps `shell` blocking-only (with `workdir` confined to workspace) and splits background into `task_*`. **Port candidates (ranked):** (a) `pty=True` for interactive tool use (Codex, Claude Code, Python REPL invocation) — high value, small surface; (b) `watch_patterns` for long-running `task_*` — real utility for build/test watching; (c) unified `shell`+`task_start` is a larger redesign. |
| `task_start(command, description, working_directory)` (`tasks/control.py`) | `terminal(background=True, notify_on_complete, watch_patterns)` + `process(action, session_id)` (`process_registry.py`) | ~ | Hermes collapses task lifecycle into two tools. Hermes's `process` supports `list`, `poll`, `log`, `wait`, `kill`, `write`, `submit`, `close` — **`write`/`submit`/`close` are co-cli gaps** (can't send stdin to a running task, no clean shutdown channel). |
| `task_status(task_id, tail_lines=20)` | `process(action="poll"/"log")` | ~ | Same surface. See Gap 4 (in-memory ring buffer). |
| `task_cancel(task_id)` | `process(action="kill")` | ✓ | Both SIGTERM→SIGKILL via process group; co-cli additionally drains the monitor task and reports `cleanup_incomplete` on timeout. |
| `task_list(status_filter)` | `process(action="list")` | ✓ | Equivalent. |
| `code_execute(cmd, timeout=60)` (`code/execute.py`) | `execute_code(code, language, …)` (`code_execution_tool.py`) | ~ | Hermes runs code in sandboxed environments (`environments/{daytona,docker,local,modal,singularity,ssh}.py`) — **co-cli runs on host with a shell-policy gate only**. Porting a sandbox backend is a major architectural addition; out of scope for a tool-level port. |
| `web_research(query, domains, max_requests)` (`agents/delegation.py`) | `delegate_task(goal, context, toolsets, tasks, max_iterations, acp_command, acp_args)` (`delegate_tool.py`) | ~ | Hermes has **one** parameterized delegation tool where the model picks a toolset profile (`web`, `file`, `skills`, …); co-cli fixes three named subagents (`web_research`, `knowledge_analyze`, `reason`). co-cli's approach is clearer prompting; hermes's is more flexible. **Port consideration:** adding a toolset-selected `delegate` would ease extension but duplicates existing ergonomics — low priority. |
| `knowledge_analyze(question, inputs, max_requests)` | `delegate_task(toolsets=["file","session_search"], …)` | ~ | Same — subsumed by hermes's single `delegate_task`. Inner agent gets `[memory_search, google_drive_search]`. |
| `reason(problem, max_requests)` | `delegate_task(toolsets=[], …)` | ~ | Same. Tool-free reasoning agent (`tool_fns=None`). |

### 1.6 External Service Integrations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `obsidian_list`, `obsidian_search`, `obsidian_read` (`obsidian/tools.py`) | *(none)* | ✗ | Co-cli-only. Gated on `obsidian_vault_path`. |
| `google_drive_search`, `google_drive_read`, `google_gmail_list`, `google_gmail_search`, `google_calendar_list`, `google_calendar_search`, `google_gmail_draft` (`google/*.py`) | *(none)* | ✗ | Co-cli-only. Gated on `google_credentials_path`. Hermes's domain integrations are different in shape — see §2.2. |
| *(none)* | `ha_list_entities`, `ha_get_state`, `ha_list_services`, `ha_call_service` (`homeassistant_tool.py`) | — | **Hermes-only.** Smart-home control. Out of scope for co-cli (no current use case). |
| *(none)* | `discord`, `discord_admin` (`discord_tool.py`) | — | **Hermes-only.** Read/participate + server management for Discord bots. Out of scope. |
| *(none)* | `feishu_doc_read`, `feishu_drive_*` (`feishu_doc_tool.py`, `feishu_drive_tool.py`) | — | **Hermes-only.** Feishu/Lark doc + comment ops for enterprise messaging bots. Out of scope. |
| *(none)* | `yb_query_group_info`, `yb_query_group_members`, `yb_send_dm`, `yb_search_sticker`, `yb_send_sticker` (`yuanbao_tools.py`) | — | **Hermes-only.** Yuanbao platform messaging. Out of scope. |
| *(none)* | `send_message` (`send_message_tool.py`) | — | **Hermes-only.** Cross-platform messaging dispatcher (Telegram/Discord/Slack/SMS). Out of scope (no co-cli use case; large auth surface). |

---

## 2. Hermes Tools Without co-cli Equivalents

Capabilities co-cli does not have, grouped by porting recommendation.

### 2.1 Worth Considering

| hermes tool | File | Why it might matter for co-cli |
|---|---|---|
| `skills_list`, `skill_view` | `skills_tool.py` | **Port in flight** — see `docs/exec-plans/active/2026-05-07-125538-skill-tools-hermes-port.md`. co-cli already has a skill corpus (`co_cli/skills/`) but exposes discovery only via slash commands; a read-only `skills_list` + `skill_view` lets the model self-load skill guidance mid-turn. `skill_manage` (write surface) is intentionally **out of scope** — it overlaps with the memory-artifact system and writes to `~/.claude/skills`. |
| `vision_analyze(image_path or url, question)` | `vision_tools.py` | co-cli has no vision tool. Adding one is cheap — the model wrapper already exists in pydantic-ai — and unlocks screenshot analysis, diagram reading, PDF-page inspection. |
| `session_search` `role_filter` (assistant vs user messages) | `session_search_tool.py` | The boolean-syntax + no-query-mode parity is shipped (see §1.3); `role_filter` is the only remaining differentiator and would let callers narrow recall to "what did *I* say last time" vs "what did *the assistant* say last time". |
| `terminal.watch_patterns` / `terminal.pty` | `terminal_tool.py` | See §1.5 for the feature-level port. |

### 2.2 Probably Out of Scope

| hermes tool | Why skip |
|---|---|
| `browser_*` (12 tools: `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console`, plus `browser_cdp` + `browser_dialog` for low-level Chrome DevTools Protocol access) | Requires a browser stack (Camofox / Browserbase / Firecrawl). Large dependency surface; overlaps with `web_fetch` for most read-only needs. Only worth porting if co-cli commits to browser automation as a capability. |
| `skill_manage(action: create\|edit\|patch\|delete\|write_file\|remove_file)` | Hermes-side skill *write* surface. Overlaps with co-cli's memory-artifact system, writes outside the workspace, and is invasive. Read-only `skills_list` + `skill_view` ship in §2.1 instead. |
| `image_generate` | Niche; Google/OpenAI image APIs have simpler direct usage. |
| `text_to_speech` | Niche; overlaps no existing co-cli workflow. |
| `cronjob` | co-cli is a user-interactive CLI; cron-style scheduling is better handled by OS cron or Claude Code's CronCreate. |
| `send_message` (Telegram/Discord/Slack/SMS) | No current co-cli use case; large auth/config surface. |
| `discord`, `discord_admin` | Discord-bot read + admin tools. No co-cli use case. |
| `feishu_doc_read`, `feishu_drive_*` (4 tools) | Feishu/Lark enterprise messaging bots. No co-cli use case. |
| `yb_*` (5 tools: yuanbao group/member queries, DM, sticker search/send) | Yuanbao platform integration. No co-cli use case. |
| `ha_*` (HomeAssistant: list_entities, get_state, list_services, call_service) | Domain-specific; co-cli's equivalent is Google integrations. |
| `mixture_of_agents` | Already covered by co-cli's `reason` + `web_research` + `knowledge_analyze` triad. |
| `rl_*` (10 RL training tools) | Highly domain-specific (Tinker-Atropos). |

### 2.3 Hermes-Only Patterns (not tools) Worth Adopting

These are architecture choices, not tool ports — covered in §3 and §4.

- Named toolset profiles (`toolsets.py`) — see §3.4.
- Uniform spill enforcement across native + MCP tools — see §3.6.
- AST-based auto-discovery of `registry.register()` calls — see §3.2.

---

## 3. Architecture-Level Gaps

### 3.1 No spill enforcement on MCP tool results
**Done (code-verified 2026-05-08).** `CoToolLifecycle.after_tool_execute` (`lifecycle.py:249-257`)
coerces MCP-source string results through `spill_if_oversized()` before the OTel span block.
Guard condition: `isinstance(result, str) and info and info.source == ToolSourceEnum.MCP`.
Uses per-tool `spill_threshold_chars` override, falls back to global `SPILL_THRESHOLD_CHARS`.

### 3.2 `NATIVE_TOOLS` is a manual tuple
**Done (code-verified 2026-05-08).** `NATIVE_TOOLS` is gone. `@agent_tool(register=True)`
(default) self-registers the decorated function into `TOOL_REGISTRY` (`agent_tool.py:19`)
at module import time. `_native_toolset.py` imports all tool modules as a side effect to
populate the registry, then iterates it in `_build_native_toolset()`. `register=False`
opts out at the definition site (`memory_read_session_turn` is the only opt-out today).

The previous `_OPT_OUT_TOOLS` frozenset and `_assert_decorated_tools_listed()` guard
are removed — registration IS the decorator, so the listed-vs-decorated consistency
check is meaningless. The earlier "no module-level decorator registry" stance was
reversed: import-order coupling is bounded by `_native_toolset.py` importing every
tool module up front, and `discover_delegation_tools()` triggers the same import as a
guard for standalone callers.

### 3.3 Sequential MCP `list_tools()` discovery
**Fixed.** `discover_mcp_tools()` (`agent/mcp.py`) now fans out all `list_tools()`
calls concurrently via `asyncio.gather` through a `_discover_one` helper. Startup
delay is now `max(timeouts)` instead of `N × timeout`.

### 3.4 No named toolset profiles for delegation
**Done (code-verified 2026-05-08).** `@agent_tool` accepts a `delegation:
frozenset[str] | set[str] | None` field that tags a tool with one or more delegation
profile names. `discover_delegation_tools(profile, config)` (`agent/core.py`) iterates
`TOOL_REGISTRY`, filters by `info.delegation` membership and `info.requires_config`,
and returns the matching list. Delegation agents call this helper instead of hardcoded
lists. Tools currently tagged: `"web_research"` (web_search, web_fetch),
`"knowledge_analyze"` (memory_search, google_drive_search, obsidian_search,
obsidian_list). Adding a tool to a profile is one decorator edit; new optional
integrations (e.g. obsidian) are gated automatically via `requires_config`.

### 3.5 No MCP dynamic tool refresh
**Deferred.** `discover_mcp_tools()` runs once at bootstrap; ignores
`notifications/tools/list_changed`. Tool index goes stale if an MCP server
adds/removes/renames mid-session. In practice no MCP server in co-cli's typical
install set (filesystem, github, brave-search, atlassian, context7) emits
`list_changed`; failure mode is bounded (model lacks awareness of new tools but
existing tools stay callable on the live connection); `/mcp restart` is a one-line
user fix. Revisit if a concrete server in the install set starts emitting the
notification — port is ~half a day, copying hermes's lock-protected refresh
(`tools/mcp_tool.py:907+`).

### 3.6 Background task output is lossy and in-memory only
**Done (code-verified 2026-05-08).** `BackgroundTaskState.output_lines` deque is
removed. Each task streams stdout+stderr to a per-task log file at
`LOGS_DIR / f"bg-{task_id}.log"` (`tools/background.py`). `_monitor` writes
through a line-buffered `open(...)` inside a `with` block so the handle closes
on EOF, cancellation, or exception. Reads (`task_status`, `/tasks`) tail the
file via the new `tail_log(path, n)` helper (64 KB seek-from-end window).
`spawn_task` accepts an injectable `logs_dir` for test isolation. Files are
unlinked at session end by `_drain_and_cleanup` (`co_cli/main.py:137-153`).
Hermes-equivalent shape minus the redundant in-memory rolling buffer — the
file is the single source of truth, so full per-task history is retained for
the session and arbitrary slices are addressable via `file_read` / `shell grep`
(no longer locked to the most-recent N lines). Covered by
`tests/test_flow_background_tasks.py` (6 tests, including a 5000-line
oversized-run case that demonstrates lines previously evicted by the deque cap
are now recoverable).

**Not ported:** orphan reaper for files left by sessions that died before
`_drain_and_cleanup` ran. Out of scope until disk accumulation in `LOGS_DIR`
becomes a real complaint.

### 3.7 No persistent approval rules
`session_approval_rules` clears at session end by design. Hermes persists
`_permanent_approved` to config. Deliberate security tradeoff, not oversight.

### 3.8 No MCP `inputSchema` sanitization
**In flight** — see `docs/exec-plans/active/2026-05-07-112044-mcp-schema-sanitizer.md`.
pydantic-ai ingests MCP `inputSchema` dicts directly from each server's `list_tools()`
response and passes them to the model without normalization. Real-world servers emit
shapes that break Ollama / llama.cpp (`"type": ["string", "null"]` arrays, bare `"type": "object"`
strings, `anyOf` with null branches, missing `properties` on object nodes). Hermes
normalizes at registration time via `tools/schema_sanitizer.py`. Tool requests are
silently rejected and dropped today.

### 3.9 No `check_fn` result cache
co-cli's `_make_prepare(fn)` (`_native_toolset.py:111`) calls `info.check_fn(deps)` on
every prepare invocation. For tools whose `check_fn` probes external state (e.g.
integration credentials, file system), this is wasted work. Hermes caches `check_fn`
results for 30 s (`registry.py:113–134`). Low-impact today (co-cli `check_fn`s are
cheap — they read in-memory `deps`), but worth noting if external-probe `check_fn`s
are added.

---

## 4. Anti-Patterns (co-cli choices that cause subtle problems)

### 4.1 `tool_output_raw()` silently bypasses spill + telemetry
`tool_io.py:251-260` skips `spill_if_oversized()`, the `tool_budget.spill_tool_result`
OTel span, and the per-tool `spill_threshold_chars` lookup. Docstring restricts use to
ctx-less helpers; nothing enforces it.

### 4.2 `ModelRetry` vs `tool_error()` classification is convention-only
Transient → `raise ModelRetry`; terminal → `return tool_error(...)`. Misclassification
(e.g. `ModelRetry` on a 401) burns retry budget on a non-recoverable error.
`handle_google_api_error()` (`tool_io.py:310`) is the reference pattern; no static check.

### 4.3 `file_read_mtimes` grows unbounded
`CoDeps.file_read_mtimes` accumulates one entry per unique file read, shared across
forked child agents via `fork_deps()` (`deps.py:305`). No turn-boundary eviction.
Intentional for cross-agent staleness detection, but unbounded in long sessions.

### 4.4 Path normalization is a hidden arg rewrite
`tools/lifecycle.py:204-215` rewrites `path` from relative → absolute before tools in
`categories.py:PATH_NORMALIZATION_TOOLS` execute. Direct unit tests see different `path`
values than production runs.

### 4.5 Delegation agents re-enumerate tool lists by hand
**Done (code-verified 2026-05-08).** Resolved alongside §3.4 — the `delegation` profile
field on `@agent_tool` + `discover_delegation_tools()` removes the per-agent hardcoded
`tool_fns=[...]` enumeration.

---

## 5. Priority Ordering

Status legend: ✅ done · 🟢 in flight (active exec-plan) · 🟠 open · ⚪ deferred / out of scope.

| Priority | Status | Item | Risk | Effort |
|---|---|---|---|---|
| **High** | 🟢 | MCP `inputSchema` sanitization (§3.8) | MCP tools silently dropped on Ollama / llama.cpp; Anthropic schema rejection | Medium — see active plan `2026-05-07-112044-mcp-schema-sanitizer.md` |
| **High** | ✅ | MCP tool results not spill-gated (§3.1) | Context overflow via runaway MCP | Done — `lifecycle.py:249-257`, code-verified 2026-05-08 |
| **High** | 🟠 | `tool_output_raw()` bypasses spill gate (§4.1) | Silent context overflow | Medium — audit callsites, enforce ctx-less restriction |
| **Medium** | 🟢 | Add model-callable `skills_list` / `skill_view` (§2.1) | Skill discovery requires user slash commands | Medium — see active plan `2026-05-07-125538-skill-tools-hermes-port.md` |
| **Medium** | ✅ | Concurrent MCP `list_tools()` discovery (§3.3) | Cumulative startup delay = N × timeout | Done — `_discover_one` + `asyncio.gather` in `agent/mcp.py` |
| **Medium** | 🟠 | Port `terminal.pty` + `terminal.watch_patterns` to `shell` / `task_*` (§1.5) | Missing capability for interactive CLIs and long-running watch | Medium — add `pty` flag to `shell`, `watch_patterns` to `task_start` |
| **Medium** | 🟠 | Port `web_fetch` to accept `urls: list[str]` with parallel fetch (§1.4) | Sequential latency | Low — `asyncio.gather` over existing fetch |
| **Medium** | 🟠 | `ModelRetry` / `tool_error` unenforced (§4.2) | Retry-budget waste | Medium — ruff rule or base-class signal |
| **Medium** | ✅ | `NATIVE_TOOLS` manual tuple (§3.2) | Tool silently omitted | Done — `TOOL_REGISTRY` self-registration via `@agent_tool`, code-verified 2026-05-08 |
| **Low** | 🟠 | Add `vision_analyze` tool (§2.1) | No vision capability | Medium — new tool + model wrapper |
| **Low** | 🟠 | Add `role_filter` to `memory_search` (§1.3) | Cannot narrow recall to assistant-vs-user messages | Low — pass through to FTS5 query |
| **Low** | ✅ | Named toolset profiles for delegation (§3.4, §4.5) | Tool-list drift in delegation | Done — `delegation` field on `@agent_tool` + `discover_delegation_tools()`, code-verified 2026-05-08 |
| **Low** | 🟠 | `file_read_mtimes` unbounded (§4.3) | Memory in very long sessions | Low — cap dict or evict on turn reset |
| **Low** | ⚪ | No permanent approval persistence (§3.7) | UX friction | Medium — deliberate security tradeoff |
| **Low** | ✅ | Background task ring-buffer lossy (§3.6) | Output loss for long commands | Done — file-only per-task log under `LOGS_DIR`, ring buffer removed; `tail_log` helper for reads; `_drain_and_cleanup` unlinks at session end. Code-verified 2026-05-08. |
| **Low** | ⚪ | MCP dynamic tool refresh (§3.5) | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed`. Deferred: no observed MCP server in co-cli's install set emits `list_changed`; failure mode is bounded (stale-but-additive); `/mcp restart` is a one-line user fix. |
| **Low** | ⚪ | `check_fn` result cache (§3.9) | Wasted work *if* external-probe `check_fn`s are added | Low — copy hermes's 30 s TTL pattern when needed |
