# RESEARCH: Tool Parity & Lifecycle Gaps — `co-cli` vs Hermes

Code-verified cross-review of co-cli's tool surface and lifecycle against hermes-agent.
Per-tool parity matrix first (what each co tool looks like in hermes, porting implications),
then architecture-level gaps and anti-patterns.

## Sources

### `co-cli` (37 native tools)

- [`co_cli/agent/_native_toolset.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_native_toolset.py) — `NATIVE_TOOLS` tuple, `@agent_tool` policy, approval-resume filter
- [`co_cli/agent/_mcp.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_mcp.py)
- [`co_cli/agent/_core.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/_core.py)
- [`co_cli/tools/_lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/_lifecycle.py)
- [`co_cli/tools/approvals.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py)
- [`co_cli/tools/agent_tool.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/agent_tool.py)
- [`co_cli/tools/tool_io.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py)
- Tool implementations: `co_cli/tools/{user_input,capabilities,todo,memory,shell,execute_code,task_control,background,obsidian,agents}.py`, `co_cli/tools/{files,knowledge,web,google}/*.py`
- [`docs/specs/tools.md`](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md)

### Hermes (~50 registered tools)

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py` — singleton `ToolRegistry`, AST auto-discovery, `check_fn`
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/toolsets.py` — named toolset profiles (web, file, terminal, skills, …)
- Tool implementations: `tools/{clarify,memory,todo,session_search,file,terminal,process,code_execution,delegate,skills,skill_manager,web,vision,image_generation,tts,browser,cronjob,send_message,homeassistant,mixture_of_agents,rl_training}_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/run_agent.py`

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with an `@agent_tool(...)` decorator that
attaches `ToolInfo` (visibility, approval, concurrency, config gate) at definition site.
Tools are a flat tuple (`NATIVE_TOOLS`) registered through `_build_native_toolset()`,
with `defer_loading` derived from visibility. Deferred approval is handled by the
SDK and the `_approval_resume_filter`; runtime path rewriting lives in
`CoToolLifecycle`.

Hermes uses a thread-safe singleton `ToolRegistry` with AST-based auto-discovery
(`registry.py:56–73` scans `tools/*.py` for module-level `registry.register()`),
per-tool `check_fn` for runtime availability, `toolset` strings grouped into
named profiles (`toolsets.py`), a blocking gateway pattern for approval, and
schemas supplied as raw JSON-Schema dicts.

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
| `clarify(question, options=None, user_answer=None)` (`user_input.py`) | `clarify` (`clarify_tool.py`) | ~ | Same user-facing intent. co resumes via `ToolApproved(override_args={"user_answer": ...})` inside the SDK's deferred-tool mechanism; hermes passes a `callback` kw to its blocking gateway. Hermes's model is robust to duplicate calls in one step (see Anti-Pattern 1) — co-cli's one-shot contract relies on prompt discipline. |
| `capabilities_check()` (`capabilities.py`) | *(none — hermes exposes `registry.get_available_toolsets()` to the CLI but not to the model)* | ✗ | co-cli uniquely exposes a doctor tool to the model for `/doctor`. No port needed — hermes's `check_fn` model means the registry itself is the introspection surface. |
| `todo_write(todos)` / `todo_read()` (`todo.py`) | `todo` (`todo_tool.py`, single op-based) | ~ | Hermes collapses read/write into one `todo` tool with a `merge` flag and persisted store. co-cli keeps them separate (simpler schema) and stores in-session state only (TodoState in CoDeps). **Port consideration:** hermes's `merge` semantics are a useful addition for incremental updates without re-submitting the full list. |

### 1.2 Workspace & File Operations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `file_find(path, pattern, max_entries)` (`files/read.py`) | `search_files` (glob branch, `file_tools.py`) | ~ | Hermes unifies glob + grep in one tool. co-cli keeps file discovery separate from content search, which is the cleaner abstraction, but now uses more intent-shaped names. |
| `file_read(path, start_line, end_line)` (`files/read.py`) | `read_file` (`file_tools.py`) | ✓ | Both support line ranges; both cap size. Hermes's cap is character-based (`_DEFAULT_MAX_READ_CHARS=100_000`) and configurable; co-cli uses line count + a 500 KB full-read gate + 2000-char per-line truncation. **Port:** co-cli's 2000-char per-line truncation is a real practical guard hermes does not have — keep. |
| `file_search(pattern, path, glob, case_insensitive, output_mode, context_lines, head_limit, offset)` | `search_files` (grep branch) | ~ | co-cli has richer output modes (count/files/content) and shell-rg fast path via `_grep_shell`. `glob` already covers the "grep within matching files" case in one call. Hermes's `search_files` is simpler. **No port needed.** |
| `file_write(path, content)` | `write_file` | ✓ | Both deferred-approval; both write atomically. co-cli adds `resource_locks` + `file_read_mtimes` staleness check — **hermes lacks both**. |
| `file_patch(path, old_string, new_string, replace_all, show_diff)` | `patch` | ~ | Both support fuzzy matching. Hermes's `patch` uses a v4a-style multi-hunk patch format via `patch_parser.py`; co-cli also has v4a support (`files/_v4a.py`) plus a simpler `old_string`/`new_string` fallback. co-cli's auto-lint on `.py` + read-before-write enforcement (`file_partial_reads`) are **co-cli-only** guardrails worth keeping. |

### 1.3 Knowledge, Memory & Session Recall

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `knowledge_search(query, kind, source, limit, tags, tag_match_mode, created_after, created_before)` (`knowledge/read.py`) | *(none — hermes has `session_search` only)* | ✗ | Unique to co-cli. Unified FTS5 search across local artifacts + Obsidian + Drive. No hermes equivalent because hermes has no knowledge-artifact concept. |
| `knowledge_list(offset, limit, kind)` | *(none)* | ✗ | Unique to co-cli. |
| `knowledge_article_read(slug)` | *(none)* | ✗ | Unique to co-cli. |
| `memory_search(query, limit=5)` (`memory.py`) | `session_search(query, role_filter, limit)` (`session_search_tool.py`) | ~ | Closest match, different scope: co's `memory_search` is a keyword scan over past transcripts; hermes's `session_search` returns LLM-summarized session snapshots plus supports FTS5 boolean syntax (`OR`, `NOT`, `"phrase"`, `prefix*`). **Port candidate:** add boolean operators and role filter to `memory_search`; optionally add "no-query → recent sessions" mode. |
| `knowledge_update(slug, old_content, new_content)` (`knowledge/write.py`) | *(none)* | ✗ | Unique to co-cli. Hermes's `memory` tool has `action=replace` with `old_text` but operates on a single frozen `MEMORY.md`, not on addressable artifacts. |
| `knowledge_append(slug, content)` | *(none)* | ✗ | Unique to co-cli. |
| `knowledge_article_save(content, title, origin_url, tags, related)` | *(none)* | ✗ | Unique to co-cli. |
| *(none)* | `memory(action, target, content, old_text)` (`memory_tool.py`) | — | **Hermes-only.** Single op-based tool for frozen-snapshot memory (MEMORY.md + USER.md injected into system prompt at session start). co-cli covers the same use case through the *auto memory* prompt layer + `knowledge_append/update` — different architecture, not a missing tool. |

### 1.4 Web

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `web_search(query, max_results=5, domains=None)` (`web/search.py`) | `web_search(query, limit=5)` (`web_tools.py`) | ✓ | Both Brave-backed. co-cli adds an SSRF-hardened `web/_ssrf.py` + domain filter; hermes uses a generic `url_safety.py`. **Port:** domains filter is a co-cli-specific policy knob; no porting needed. |
| `web_fetch(url)` (`web/fetch.py`) | `web_extract(urls: list[str])` | ~ | Hermes accepts up to 5 URLs per call — **real latency advantage**. co-cli accepts a single URL. **Port candidate:** add `urls: list[str]` with a parallel fetch + per-URL timeout. |

### 1.5 Execution, Jobs & Delegation

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `shell(cmd, timeout=120)` (`shell.py`) | `terminal(command, background, timeout, workdir, pty, notify_on_complete, watch_patterns)` (`terminal_tool.py`) | ~ | Hermes's `terminal` is a superset: built-in background mode with `notify_on_complete` and `watch_patterns` (regex pattern notifier mid-process) and **PTY support** for interactive CLIs. co-cli keeps `shell` blocking-only and splits background into `task_*`. **Port candidates (ranked):** (a) `pty=True` for interactive tool use (Codex, Claude Code, Python REPL invocation) — high value, small surface; (b) `watch_patterns` for long-running `task_*` — real utility for build/test watching; (c) unified `shell`+`task_start` is a larger redesign. |
| `task_start(command, description, working_directory)` (`task_control.py`) | `terminal(background=True, notify_on_complete, watch_patterns)` + `process(action, session_id)` (`process_registry.py`) | ~ | Hermes collapses task lifecycle into two tools. Hermes's `process` supports `list`, `poll`, `log`, `wait`, `kill`, `write`, `submit`, `close` — **`write`/`submit`/`close` are co-cli gaps** (can't send stdin to a running task, no clean shutdown channel). |
| `task_status(task_id, tail_lines=20)` | `process(action="poll"/"log")` | ~ | Same surface. See Gap 4 (in-memory ring buffer). |
| `task_cancel(task_id)` | `process(action="kill")` | ✓ | Both SIGTERM→SIGKILL. |
| `task_list(status_filter)` | `process(action="list")` | ✓ | Equivalent. |
| `code_execute(cmd, timeout=60)` (`execute_code.py`) | `execute_code(code, language, …)` (`code_execution_tool.py`) | ~ | Hermes runs code in sandboxed environments (`environments/{daytona,docker,local,modal,singularity,ssh}.py`) — **co-cli runs on host with a shell-policy gate only**. Porting a sandbox backend is a major architectural addition; out of scope for a tool-level port. |
| `web_research(query, domains, max_requests)` (`agents.py`) | `delegate_task(goal, context, toolsets, tasks, max_iterations, acp_command, acp_args)` (`delegate_tool.py`) | ~ | Hermes has **one** parameterized delegation tool where the model picks a toolset profile (`web`, `file`, `skills`, …); co-cli fixes three named subagents (`web_research`, `knowledge_analyze`, `reason`). co-cli's approach is clearer prompting; hermes's is more flexible. **Port consideration:** adding a toolset-selected `delegate` would ease extension but duplicates existing ergonomics — low priority. |
| `knowledge_analyze(question, inputs, max_requests)` | `delegate_task(toolsets=["file","session_search"], …)` | ~ | Same — subsumed by hermes's single `delegate_task`. |
| `reason(problem, max_requests)` | `delegate_task(toolsets=[], …)` | ~ | Same. |

### 1.6 External Service Integrations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `obsidian_list`, `obsidian_search`, `obsidian_read` (`obsidian.py`) | *(none)* | ✗ | Co-cli-only. Gated on `obsidian_vault_path`. |
| `drive_search`, `drive_read`, `gmail_list`, `gmail_search`, `calendar_list`, `calendar_search`, `gmail_draft` (`google/*.py`) | *(none)* | ✗ | Co-cli-only. Gated on `google_credentials_path`. Hermes has `homeassistant` as its domain integration instead. |

---

## 2. Hermes Tools Without co-cli Equivalents

Capabilities co-cli does not have, grouped by porting recommendation.

### 2.1 Worth Considering

| hermes tool | File | Why it might matter for co-cli |
|---|---|---|
| `skills_list`, `skill_view`, `skill_manage(action: create\|edit\|patch\|delete\|write_file\|remove_file)` | `skills_tool.py`, `skill_manager_tool.py` | co-cli has a skill system (`co_cli/skills/`) but exposes skill discovery through slash commands and static prompt layers, not through model-callable tools. A read-only `skills_list` + `skill_view` would let the model discover and self-load skills mid-turn without user-issued slash commands. `skill_manage` is more invasive (writes to `~/.claude/skills`) and overlaps with the knowledge-artifact system. |
| `vision_analyze(image_path or url, question)` | `vision_tools.py` | co-cli has no vision tool. Adding one is cheap — the model wrapper already exists in pydantic-ai — and unlocks screenshot analysis, diagram reading, PDF-page inspection. |
| `session_search` (as a replacement for `memory_search`) | `session_search_tool.py` | See §1.3. Adopting FTS5 boolean syntax + no-query recent-sessions mode is a cleaner UX than today's keyword-only `memory_search`. |
| `terminal.watch_patterns` / `terminal.pty` | `terminal_tool.py` | See §1.5 for the feature-level port. |

### 2.2 Probably Out of Scope

| hermes tool | Why skip |
|---|---|
| `browser_*` (11 tools: navigate, snapshot, click, type, scroll, back, press, get_images, vision, console) + `browser_providers/*` | Requires a browser stack (Camofox / Browserbase / Firecrawl). Large dependency surface; overlaps with `web_fetch` for most read-only needs. Only worth porting if co-cli commits to browser automation as a capability. |
| `image_generate` | Niche; Google/OpenAI image APIs have simpler direct usage. |
| `text_to_speech` | Niche; overlaps no existing co-cli workflow. |
| `cronjob` | co-cli is a user-interactive CLI; cron-style scheduling is better handled by OS cron or Claude Code's CronCreate. |
| `send_message` (Telegram/Discord/Slack/SMS) | No current co-cli use case; large auth/config surface. |
| `ha_*` (HomeAssistant: list_entities, get_state, list_services, call_service) | Domain-specific; co-cli's equivalent is Google integrations. |
| `mixture_of_agents` | Already covered by co-cli's `reason` + `web_research` + `knowledge_analyze` triad. |
| `rl_*` (10 RL training tools) | Highly domain-specific (Tinker-Atropos). |

### 2.3 Hermes-Only Patterns (not tools) Worth Adopting

These are architecture choices, not tool ports — covered in §3 and §4.

- `check_fn` per tool (runtime availability).
- Named toolset profiles (`toolsets.py`).
- Per-tool `max_result_size_chars` schema field.
- AST-based auto-discovery of `registry.register()` calls.

---

## 3. Architecture-Level Gaps

### 3.1 No runtime tool availability check (`check_fn` pattern)

Hermes registers a `check_fn` per tool (`registry.py:176`) evaluated before each
invocation. co-cli's `requires_config` (`_native_toolset.py:137`) is a build-time
gate only — if `google_credentials_path` is set at startup, all Google tools stay
registered for the session. If credentials expire mid-session, the tools remain
in the model's schema and appear callable but fail at the network layer.

**Impact:** Stale-credential errors look like retryable tool failures; the model
may spin on Google tools after an auth expiry. **Port:** add an optional
`check_fn` field to `@agent_tool` and hook it into the `ToolSearchToolset`
visibility filter.

### 3.2 No MCP dynamic tool refresh

`discover_mcp_tools()` (`_mcp.py:75`) runs once at bootstrap. No handling for
MCP's `notifications/tools/list_changed`. If an MCP server adds, removes, or
renames tools during a session, co-cli's index goes stale. Hermes supports
deregister/re-register on refresh (`mcp_tool.py:2362`).

### 3.3 `NATIVE_TOOLS` tuple is a manually-maintained list

Every native tool requires two edits: decorate it with `@agent_tool`, then add
it to the tuple in `_native_toolset.py:42`. Forgetting the second step silently
omits the tool. Hermes's AST discovery (`registry.py:56`) scans `tools/*.py`
for module-level `registry.register()` — forgetting the call is the only way
to miss registration, and the new file is auto-detected.

**Mitigation already in place:** the loop in `_build_native_toolset` raises
`TypeError` if a listed function is missing `@agent_tool`. The residual gap is
a decorated function that isn't in the tuple — still silent.

### 3.4 Background task output is in-memory only with lossy ring-buffer

`BackgroundTaskState.output_lines` is `deque(maxlen=500)` (`background.py:23`).
For long-running commands producing more than 500 lines, oldest output is
silently dropped with no on-disk fallback. Session crash → all output lost.
Hermes's `terminal`/`process` backends tee stdout to a file per session.

### 3.5 No named toolset profiles for delegation

Delegation agents (`web_research`, `knowledge_analyze`, `reason` in `agents.py`)
receive an explicit list of tool functions at agent-build time. Hermes's
`toolsets.py` + `resolve_toolset()` expresses access at group level
(`"web"`, `"file"`, …). A new web tool added to co-cli must also be threaded
into the delegation agent's explicit tool list.

### 3.6 Approval session rules have no persistence layer

`session_approval_rules` lives in `CoSessionState` and is cleared at session
end by design (security boundary). Hermes has `_permanent_approved` that can
be persisted to config. co-cli is yes/no/remember-for-session only — deliberate
tradeoff, not oversight.

### 3.7 No per-tool `max_result_size_chars` enforcement on MCP tools

co-cli's `@agent_tool(max_result_size=...)` applies to native tools via
`CoToolLifecycle.after_tool_execute`. MCP tool results are not gated at all —
a runaway MCP server can flood the context window. Hermes's registry applies
`max_result_size_chars` uniformly.

### 3.8 No per-server timeout on MCP `list_tools()` at startup

`discover_mcp_tools()` calls `entry.server.list_tools()` (`_mcp.py:93`) with no
explicit `asyncio.timeout()` wrapper. If an MCP server hangs, startup blocks
until the OS-level socket timeout. Servers are iterated sequentially — a stalled
server blocks discovery of all subsequent servers.

**Fix:** wrap each `list_tools()` in `asyncio.timeout(cfg.timeout)` and run
discovery with `asyncio.gather(..., return_exceptions=True)`.

---

## 4. Anti-Patterns (co-cli choices that cause subtle problems)

### 4.1 `clarify` one-shot injection is fragile under model confusion

`clarify` raises `QuestionRequired` on any call where `ctx.tool_call_approved`
is false (`user_input.py:51`). The answer is injected via `ToolApproved(override_args)`
in the approval loop. If the model calls `clarify` twice in the same step,
only the first is paired correctly; the second fails with `"No answer was
received from the user."` The docstring warns "CRITICAL — one call only" —
that's prompt discipline, not a framework guard. Hermes's blocking gateway
handles the answer channel independently of tool call identity.

### 4.2 `tool_output_raw()` silently bypasses size gate and telemetry

`tool_output_raw()` (`tool_io.py:174`) exists for ctx-less helpers but silently
omits: oversized-result persistence (per-tool `max_result_size` threshold),
OTel span enrichment from `CoToolLifecycle.after_tool_execute`, and telemetry
metadata. If a tool author uses `tool_output_raw()` where they have a `ctx`,
oversized output floods the context with no metric capturing it.

### 4.3 `ModelRetry` vs. `tool_error()` classification is convention with no guardrail

Transient failures (`raise ModelRetry(...)`) consume a retry budget; terminal
failures (`return tool_error(...)`) stop immediately. Classification is
tool-author responsibility with no static enforcement. `handle_google_api_error()`
(`tool_io.py:233`) shows the right pattern, but a tool that raises `ModelRetry`
on a 401 will exhaust the retry budget spinning on a non-recoverable error.

### 4.4 `file_read_mtimes` grows unbounded with no per-turn cleanup

`CoDeps.file_read_mtimes: dict[str, float]` accumulates one entry per unique
file read, shared across parent and forked child agents via `fork_deps()`
(`deps.py:262`). No eviction at turn boundary. Intentional for cross-agent
staleness detection, but unbounded.

### 4.5 `CoToolLifecycle.before_tool_execute` path normalization is a hidden arg rewrite

Path normalization in `tools/_lifecycle.py` rewrites `path` args from relative to
absolute before the tool body executes. A tool author testing their function
directly gets different `path` values than in production. Tool implementations
appear to accept relative paths, but never receive them in a running system.

### 4.6 Delegation agents re-enumerate tool lists manually

`web_research`, `knowledge_analyze`, `reason` in `agents.py` each pass an
explicit tool list to their inner agent. A new web tool added to co-cli is
invisible to `web_research` until someone edits the delegation definition.
Symptom of §3.5 (no named toolset profiles).

---

## 5. Priority Ordering

| Priority | Item | Risk | Effort |
|---|---|---|---|
| **High** | Per-server timeout on MCP `list_tools()` at startup (§3.8) | Startup hangs | Low — `asyncio.timeout()` + `gather` |
| **High** | `tool_output_raw()` bypasses size gate (§4.2) | Silent context overflow | Medium — audit callsites, restrict to ctx-less helpers |
| **High** | MCP tool results not size-gated (§3.7) | Context overflow via runaway MCP | Medium — extend `CoToolLifecycle.after_tool_execute` to MCP spans |
| **Medium** | Port `terminal.pty` + `terminal.watch_patterns` to `shell` / `task_*` (§1.5) | Missing capability for interactive CLIs and long-running watch | Medium — add `pty` flag to `shell`, `watch_patterns` to `task_start` |
| **Medium** | Port `web_fetch` to accept `urls: list[str]` with parallel fetch (§1.4) | Sequential latency | Low — `asyncio.gather` over existing fetch |
| **Medium** | `ModelRetry` / `tool_error` unenforced (§4.3) | Retry-budget waste | Medium — ruff rule or base-class signal |
| **Medium** | No `check_fn` runtime availability (§3.1) | Tools callable after credential expiry | Medium — hook into visibility filter |
| **Medium** | `NATIVE_TOOLS` manual tuple (§3.3) | Tool silently omitted | Low — module scan for `@agent_tool`-decorated functions at import |
| **Medium** | Add `session_search` boolean syntax + role filter to `memory_search` (§1.3) | UX gap for cross-session recall | Low — FTS5 already in place |
| **Medium** | Add model-callable `skills_list` / `skill_view` (§2.1) | Skill discovery requires user slash commands | Medium — read-only tools over existing registry |
| **Low** | Add `vision_analyze` tool (§2.1) | No vision capability | Medium — new tool + model wrapper |
| **Low** | Named toolset profiles for delegation (§3.5, §4.6) | Tool-list drift in delegation | Medium — `toolsets.py`-style registry |
| **Low** | `file_read_mtimes` unbounded (§4.4) | Memory in very long sessions | Low — cap dict or evict on turn reset |
| **Low** | Clarify one-shot fragility (§4.1) | Model confusion on duplicate calls | Hard — approval-loop dedup |
| **Low** | No permanent approval persistence (§3.6) | UX friction | Medium — security tradeoff |
| **Low** | Background task ring-buffer lossy (§3.4) | Output loss for long commands | Medium — optional file sink on spawn |
| **Low** | MCP dynamic tool refresh (§3.2) | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed` |
