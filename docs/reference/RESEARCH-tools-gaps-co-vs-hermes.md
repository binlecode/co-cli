# RESEARCH: Tool Parity & Lifecycle Gaps — `co-cli` vs Hermes

Code-verified cross-review of co-cli's tool surface and lifecycle against hermes-agent.
Per-tool parity matrix first (what each co tool looks like in hermes, porting implications),
then architecture-level gaps and anti-patterns.

## Sources

### `co-cli` (~33 native tools)

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

### Hermes (~50 registered tools)

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py` — singleton `ToolRegistry`, AST auto-discovery, `check_fn`
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/toolsets.py` — named toolset profiles (web, file, terminal, skills, …)
- Tool implementations: `tools/{clarify,memory,todo,session_search,file,terminal,process,code_execution,delegate,skills,skill_manager,web,vision,image_generation,tts,browser,cronjob,send_message,homeassistant,mixture_of_agents,rl_training}_tool.py`
- `/Users/binle/workspace_genai/hermes-agent/run_agent.py`

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with an `@agent_tool(...)` decorator that
attaches `ToolInfo` (visibility, approval, concurrency, integration, `requires_config`,
`spill_threshold_chars`, optional `check_fn`) at definition site. Tools are a flat tuple
(`NATIVE_TOOLS`) registered through `_build_native_toolset()`, with `defer_loading`
derived from visibility and a per-turn `prepare` callback wrapping `check_fn` when present.
Deferred approval is handled by the SDK and the `_approval_resume_filter`; runtime path
rewriting and JSON-arg repair live in `CoToolLifecycle`.

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
| `web_fetch(url, format="markdown", timeout=15)` (`web/fetch.py`) | `web_extract(urls: list[str])` | ~ | Hermes accepts up to 5 URLs per call — **real latency advantage**. co-cli accepts a single URL but exposes `format` (markdown/html/text) and per-call `timeout` knobs hermes lacks. **Port candidate:** add `urls: list[str]` with a parallel fetch + per-URL timeout. |

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
| `google_drive_search`, `google_drive_read`, `google_gmail_list`, `google_gmail_search`, `google_calendar_list`, `google_calendar_search`, `google_gmail_draft` (`google/*.py`) | *(none)* | ✗ | Co-cli-only. Gated on `google_credentials_path`. Hermes has `homeassistant` as its domain integration instead. |

---

## 2. Hermes Tools Without co-cli Equivalents

Capabilities co-cli does not have, grouped by porting recommendation.

### 2.1 Worth Considering

| hermes tool | File | Why it might matter for co-cli |
|---|---|---|
| `skills_list`, `skill_view`, `skill_manage(action: create\|edit\|patch\|delete\|write_file\|remove_file)` | `skills_tool.py`, `skill_manager_tool.py` | co-cli has a skill system (`co_cli/skills/`) but exposes skill discovery through slash commands and static prompt layers, not through model-callable tools. A read-only `skills_list` + `skill_view` would let the model discover and self-load skills mid-turn without user-issued slash commands. `skill_manage` is more invasive (writes to `~/.claude/skills`) and overlaps with the knowledge-artifact system. |
| `vision_analyze(image_path or url, question)` | `vision_tools.py` | co-cli has no vision tool. Adding one is cheap — the model wrapper already exists in pydantic-ai — and unlocks screenshot analysis, diagram reading, PDF-page inspection. |
| `session_search` `role_filter` (assistant vs user messages) | `session_search_tool.py` | The boolean-syntax + no-query-mode parity is shipped (see §1.3); `role_filter` is the only remaining differentiator and would let callers narrow recall to "what did *I* say last time" vs "what did *the assistant* say last time". |
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

- Named toolset profiles (`toolsets.py`) — see §3.4.
- Uniform spill enforcement across native + MCP tools — see §3.6.
- AST-based auto-discovery of `registry.register()` calls — see §3.2.

---

## 3. Architecture-Level Gaps

### 3.1 No spill enforcement on MCP tool results
The spill gate fires only inside `tool_io.py:tool_output()`. MCP returns flow back
from the SDK directly — a runaway MCP server can still flood the context. **Fix:**
coerce MCP-source results through `spill_if_oversized()` in `CoToolLifecycle.after_tool_execute`.

### 3.2 `NATIVE_TOOLS` is a manual tuple
Decorating with `@agent_tool` is not enough — a function must also be appended to
`agent/_native_toolset.py:44`. The reverse-direction guard exists (listed-but-undecorated
raises `TypeError`); decorated-but-unlisted is silent. `memory_read_session_turn` is the
intentional case. **Fix:** module scan for `@agent_tool`-decorated functions at import,
with an explicit opt-out marker.

### 3.3 Sequential MCP `list_tools()` discovery
Per-server `asyncio.timeout(entry.timeout)` is in (`agent/mcp.py:96`); the loop is still
sequential. Worst-case startup delay is N × timeout. **Fix:** `asyncio.gather(..., return_exceptions=True)`.

### 3.4 No named toolset profiles for delegation
`web_research` / `knowledge_analyze` / `reason` (`tools/agents/delegation.py:214,317,365`)
take explicit `tool_fns=[...]` lists — a new web tool stays invisible until threaded in
by hand. Hermes uses `toolsets.py:resolve_toolset()` for group-level access.

### 3.5 No MCP dynamic tool refresh
`discover_mcp_tools()` runs once at bootstrap; ignores `notifications/tools/list_changed`.
Tool index goes stale if an MCP server adds/removes/renames mid-session.

### 3.6 Background task output is lossy and in-memory only
`BackgroundTaskState.output_lines = deque(maxlen=500)` (`tools/background.py:23`). Older
lines silently dropped; session crash → all lost. Hermes tees stdout to a file per session.

### 3.7 No persistent approval rules
`session_approval_rules` clears at session end by design. Hermes persists
`_permanent_approved` to config. Deliberate security tradeoff, not oversight.

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
Symptom of §3.4. New tool → invisible to `web_research` / `knowledge_analyze` until the
`tool_fns=` list is updated.

---

## 5. Priority Ordering

| Priority | Item | Risk | Effort |
|---|---|---|---|
| **High** | MCP tool results not spill-gated (§3.1) | Context overflow via runaway MCP | Medium — coerce MCP-source results through `spill_if_oversized()` in `after_tool_execute` |
| **High** | `tool_output_raw()` bypasses spill gate (§4.1) | Silent context overflow | Medium — audit callsites, enforce ctx-less restriction |
| **Medium** | Concurrent MCP `list_tools()` discovery (§3.3) | Cumulative startup delay = N × timeout | Low — wrap loop in `asyncio.gather(..., return_exceptions=True)` |
| **Medium** | Port `terminal.pty` + `terminal.watch_patterns` to `shell` / `task_*` (§1.5) | Missing capability for interactive CLIs and long-running watch | Medium — add `pty` flag to `shell`, `watch_patterns` to `task_start` |
| **Medium** | Port `web_fetch` to accept `urls: list[str]` with parallel fetch (§1.4) | Sequential latency | Low — `asyncio.gather` over existing fetch |
| **Medium** | `ModelRetry` / `tool_error` unenforced (§4.2) | Retry-budget waste | Medium — ruff rule or base-class signal |
| **Medium** | `NATIVE_TOOLS` manual tuple (§3.2) | Tool silently omitted | Low — module scan for `@agent_tool`-decorated functions at import |
| **Medium** | Add model-callable `skills_list` / `skill_view` (§2.1) | Skill discovery requires user slash commands | Medium — read-only tools over existing registry |
| **Low** | Add `vision_analyze` tool (§2.1) | No vision capability | Medium — new tool + model wrapper |
| **Low** | Named toolset profiles for delegation (§3.4, §4.5) | Tool-list drift in delegation | Medium — `toolsets.py`-style registry |
| **Low** | `file_read_mtimes` unbounded (§4.3) | Memory in very long sessions | Low — cap dict or evict on turn reset |
| **Low** | No permanent approval persistence (§3.7) | UX friction | Medium — security tradeoff |
| **Low** | Background task ring-buffer lossy (§3.6) | Output loss for long commands | Medium — optional file sink on spawn |
| **Low** | MCP dynamic tool refresh (§3.5) | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed` |
