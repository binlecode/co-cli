# RESEARCH: Tool Parity & Lifecycle Gaps — `co-cli` vs Hermes

> **Last refreshed:** 2026-05-28 (co-cli v0.8.264). Tool inventories re-counted
> directly from each repo (co-cli `TOOL_REGISTRY` dump via runtime import; hermes
> `registry.register()` AST scan). Active in-flight items linked to exec-plans
> where applicable.
>
> **Changes since 2026-05-27 refresh:** co-cli at 37 tools (inventory unchanged).
> Tool Gap B1 shipped at v0.8.262 (`tool_output_raw` bypass removed by deleting
> the helper; article URL-dedup restored via `memory_manage(source_url=…)`).
> Tool Gap B2 (document handling) pivoted from a `document_extract` tool to a
> `documents` skill that drives `shell_exec` + a `scripts/extract_pdf.py` helper
> — see §2.1. The §3.4/§4.5 "delegation profile field" verdict has been
> downgraded after re-reading the code: the active mechanism is
> `TaskAgentSpec.tool_names=(…)` tuples on each delegation spec, not a
> `delegation` field on `@agent_tool` (no such field exists; no
> `discover_delegation_tools()` either). Hermes inventory unchanged at 64.
>
> **Changes since 2026-05-28 (v0.8.262) refresh:** co-cli now at v0.8.264 (two
> REPL queue commits; no tool surface impact). `COMPACTABLE_TOOLS` was removed
> from `categories.py` at v0.8.254 (stale reference corrected in §1 sources).
> The two dedicated documents-skill plans (`2026-05-27-171910-documents-skill.md`,
> `2026-05-27-172717-skill-documents.md`) were withdrawn and deleted; the
> `documents` skill (TASK-4) is now tracked under the consolidated
> `2026-05-27-133104-skill-porting-mission-converged.md` plan — §2.1 and §5
> updated accordingly. Prompt-static-trim plan (in-flight): `_skill_manifest_provider`
> and `_category_awareness_provider` are being moved from static instruction
> builders to per-turn instructions (`orchestrator.py` + `_instructions.py`) to
> stabilize the Ollama cache prefix — no tool surface change.

Code-verified cross-review of co-cli's tool surface and lifecycle against hermes-agent.
Per-tool parity matrix first (what each co tool looks like in hermes, porting implications),
then architecture-level gaps and anti-patterns.

## Sources

### `co-cli` (37 native tools)

**Tool inventory (37, sorted)**: `capabilities_check`, `clarify`, `code_execute`, `file_find`, `file_patch`, `file_read`, `file_search`, `file_write`, `google_calendar_list`, `google_calendar_search`, `google_drive_read`, `google_drive_search`, `google_gmail_draft`, `google_gmail_list`, `google_gmail_search`, `knowledge_analyze`, `memory_manage`, `memory_search`, `memory_view`, `obsidian_list`, `obsidian_read`, `obsidian_search`, `reason`, `session_search`, `session_view`, `shell_exec`, `skill_manage`, `skill_view`, `task_cancel`, `task_list`, `task_start`, `task_status`, `todo_read`, `todo_write`, `web_fetch`, `web_research`, `web_search`.

- [`co_cli/agent/toolset.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/toolset.py) — `_build_native_toolset()` iterates `TOOL_REGISTRY`, `_make_prepare(check_fn)`, `_approval_resume_filter`, `_config_requirement_met` gating
- [`co_cli/agent/mcp.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/mcp.py) — `_SanitizingMCPServer`, concurrent `_discover_one` + `asyncio.gather`
- [`co_cli/tools/mcp_schema.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/mcp_schema.py) — `sanitize_mcp_schema()` (type-array collapse, nullable-union collapse, object-shape fix, recursion)
- [`co_cli/agent/core.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/core.py)
- [`co_cli/tools/lifecycle.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/lifecycle.py)
- [`co_cli/tools/categories.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/categories.py) — `PATH_NORMALIZATION_TOOLS`, `FILE_TOOLS` (`COMPACTABLE_TOOLS` removed at v0.8.254)
- [`co_cli/tools/approvals.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py)
- [`co_cli/tools/agent_tool.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/agent_tool.py)
- [`co_cli/tools/tool_io.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py) — `tool_output`/`tool_output_raw`, `spill_if_oversized`, `SPILL_THRESHOLD_CHARS`
- [`co_cli/tools/background.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/background.py) — `BackgroundTaskState`, `output_lines` ring buffer
- Tool implementations (each in its own subpackage):
  `co_cli/tools/system/{user_input,capabilities,skills}.py`, `co_cli/tools/todo/rw.py`,
  `co_cli/tools/shell/execute.py`, `co_cli/tools/code/execute.py`,
  `co_cli/tools/tasks/control.py`, `co_cli/tools/agents/delegation.py`,
  `co_cli/tools/{files,memory,session,web,google,obsidian}/*.py`
- Memory + session libraries: `co_cli/memory/` (knowledge items) and `co_cli/session/` (transcripts) sit over the shared `co_cli/index/` FTS5 (BM25) facade
- [`docs/specs/tools.md`](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md), [`docs/specs/memory.md`](/Users/binle/workspace_genai/co-cli/docs/specs/memory.md)

### Hermes (64 registered tools, as of 2026-05-27)

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py` — singleton `ToolRegistry`, AST auto-discovery, `check_fn`, 30 s `check_fn` TTL cache
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py` + `tools/schema_sanitizer.py` (MCP `inputSchema` normalization at registration time)
- `/Users/binle/workspace_genai/hermes-agent/toolsets.py` — named toolset profiles (web, file, terminal, skills, browser, browser-cdp, …) plus per-platform supersets (`hermes-cli`, `hermes-discord`, `hermes-telegram`, …)
- Core tool implementations: `tools/{clarify,memory,todo,session_search,file,terminal,process_registry,code_execution,delegate,skills,skill_manager,web,vision,image_generation,video_generation,tts,browser,browser_cdp,browser_dialog,cronjob,send_message,homeassistant,mixture_of_agents,computer_use,x_search,kanban}_tool*.py`
- Platform-specific tool implementations: `tools/{discord,feishu_doc,feishu_drive,yuanbao}_tool*.py`
- `/Users/binle/workspace_genai/hermes-agent/run_agent.py`

**Tool inventory (64, sorted)**: `browser_back`, `browser_cdp`, `browser_click`, `browser_console`, `browser_dialog`, `browser_get_images`, `browser_navigate`, `browser_press`, `browser_scroll`, `browser_snapshot`, `browser_type`, `browser_vision`, `clarify`, `computer_use`, `cronjob`, `delegate_task`, `discord`, `discord_admin`, `execute_code`, `feishu_doc_read`, `feishu_drive_add_comment`, `feishu_drive_list_comment_replies`, `feishu_drive_list_comments`, `feishu_drive_reply_comment`, `ha_call_service`, `ha_get_state`, `ha_list_entities`, `ha_list_services`, `image_generate`, `kanban_block`, `kanban_comment`, `kanban_complete`, `kanban_create`, `kanban_heartbeat`, `kanban_link`, `kanban_list`, `kanban_show`, `kanban_unblock`, `memory`, `mixture_of_agents`, `patch`, `process`, `read_file`, `search_files`, `send_message`, `session_search`, `skill_manage`, `skill_view`, `skills_list`, `terminal`, `text_to_speech`, `todo`, `video_analyze`, `video_generate`, `vision_analyze`, `web_extract`, `web_search`, `write_file`, `x_search`, `yb_query_group_info`, `yb_query_group_members`, `yb_search_sticker`, `yb_send_dm`, `yb_send_sticker`.

**Removed since 2026-05-08:** the 10 `rl_*` Tinker-Atropos training tools.
**Added since 2026-05-08:** `computer_use` (macOS desktop via cua-driver), `x_search` (X/Twitter via xAI), `video_analyze`, `video_generate`, and the 9-tool `kanban_*` multi-agent coordination set.

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with an `@agent_tool(...)` decorator that
attaches `ToolInfo` (visibility, approval, concurrency, integration, `requires_config`,
`spill_threshold_chars`, optional `check_fn`, optional `delegation` profile tags) at
definition site. Each decorated function self-registers into the module-level
`TOOL_REGISTRY` at import time (`register=True` default); `_build_native_toolset()`
iterates that registry, with `defer_loading` derived from visibility, a per-turn
`prepare` callback wrapping `check_fn`, and `_config_requirement_met` excluding
unconfigured integrations. Deferred approval is handled by the SDK and the
`_approval_resume_filter`; runtime path rewriting and JSON-arg repair live in
`CoToolLifecycle`.

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
| `todo_write(items, merge=False)` / `todo_read()` (`todo/rw.py`) | `todo` (`todo_tool.py`, single op-based) | ✓ | co-cli keeps read/write split where hermes collapses to one op-based `todo`. The `merge` gap is **closed** — `todo_write(merge=True)` updates items by `id` without re-submitting the full list (`_run_merge`, `rw.py`). co-cli's schema retains the `priority` field (high/medium/low) per item that hermes lacks, plus a one-in-progress enforcement guard. State is session-scoped in `CoSessionState`; hermes persists. |

### 1.2 Workspace & File Operations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `file_find(path, pattern, max_entries)` (`files/read.py`) | `search_files` (glob branch, `file_tools.py`) | ~ | Hermes unifies glob + grep in one tool. co-cli keeps file discovery separate from content search, which is the cleaner abstraction. |
| `file_read(path, start_line, end_line)` (`files/read.py`) | `read_file` (`file_tools.py`) | ✓ | Both support line ranges; both cap size. Hermes's cap is character-based (`_DEFAULT_MAX_READ_CHARS=100_000`) and configurable; co-cli uses line count (default 500, max 2000) + a 500 KB full-read gate + 2000-char per-line truncation, and opts out of `tool_output()` spilling via `spill_threshold_chars=math.inf` (the per-line caps already enforce shape). **Port:** co-cli's 2000-char per-line truncation is a real practical guard hermes does not have — keep. |
| `file_search(pattern, path, glob, case_insensitive, output_mode, context_lines, head_limit, offset)` | `search_files` (grep branch) | ~ | co-cli has richer output modes (count/files/content) and shell-rg fast path via `_grep_shell`. `glob` already covers the "grep within matching files" case in one call. Hermes's `search_files` is simpler. **No port needed.** |
| `file_write(path, content)` | `write_file` | ✓ | Both deferred-approval; both write atomically. co-cli adds `resource_locks` + `file_read_mtimes` staleness check — **hermes lacks both**. |
| `file_patch(path, old_string, new_string, replace_all, show_diff)` | `patch` | ~ | V4A multi-file support **removed** — V4A is the OpenAI-Codex-native patch format, incompatible with co's small local models (opencode `registry.ts:322-325` and openclaw `pi-tools.ts:266-292` both gate `apply_patch` to OpenAI models only). co-cli's `file_patch` is now a pure single-file fuzzy `old_string`/`new_string` edit with four-strategy fallback (exact, line-trimmed, indent-stripped, escape-expanded); whole-file delete moves to `shell_exec` (`rm`), in-file deletion via `new_string=""`. Hermes's `patch` stays V4A-only via `patch_parser.py` (targets large frontier models). co-cli's auto-lint on `.py` + read-before-write enforcement (`file_partial_reads`) are **co-cli-only** guardrails worth keeping. |

### 1.3 Memory & Session Recall

> **Refactor note (current, 2026-05-27):** `co_cli/knowledge/` was unified into
> `co_cli/memory/`, then memory and session split into two domain modules over the
> shared `co_cli/index/` FTS5 facade. The recall surface is **search-driven** — there
> is no `memory_list`/`memory_read`. Browse with `memory_search` (empty/kind-filtered
> query); full-body reads now have a dedicated **`memory_view(name)`** tool (keyed by
> the `filename_stem` a search hit surfaces — no longer the generic `file_read`).
> The earlier unregistered `memory_read_session_turn` reader is gone; its job is now
> `session_view(session_id, start_line, end_line)`.
>
> The active model-callable surface is **five** tools across two domains: memory
> (`memory_search`, `memory_view`, `memory_manage`) and session (`session_search`,
> `session_view`). Canon hits ship full body inline; session hits ship chunk citations
> with line ranges (no LLM summarization in the search path).

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `memory_search(query, kinds, limit)` (`tools/memory/recall.py`) | *(no direct equivalent — hermes splits to `memory` injection + `session_search`)* | ~ | Searches memory items only (BM25 FTS5 — three-pass: canon priority → user priority → waterfall over rule/article/note with combined count + size cap). Empty query browses recent items. **No `role_filter`** (N/A — items, not transcripts). |
| `memory_view(name)` (`tools/memory/view.py`) | *(none)* | ✗ | Full-body memory-item read by `filename_stem`. `spill_threshold_chars=inf` (no spill). Hermes has no addressable per-item read — its memory is a single injected `MEMORY.md` snapshot. |
| `memory_manage(action, name, content, kind, section, source_type)` (`tools/memory/manage.py`) | `memory(action, target, content, old_text)` (`memory_tool.py`) | ~ | co-cli unifies create/append/replace/delete on addressable items (`user`/`rule`/`article`/`note`) with approval + exact-match guard on replace. Hermes's `memory` does add/replace/remove against a frozen `MEMORY.md`/`USER.md` injected into the system prompt — **different architecture, not a missing tool.** |
| `session_search(query, role_filter?, ...)` (`tools/session/recall.py`) | `session_search(query, role_filter, limit, session_id, around_message_id, window)` (`session_search_tool.py`) | ~ | Chunk-level FTS5 hits with `start_line`/`end_line`; FTS5 boolean syntax (`OR`, `NOT`, `"phrase"`, `prefix*`); empty query → no-LLM recent-sessions browse (excludes active session); ≤3 unique sessions per call. **Remaining gaps vs hermes:** no `role_filter` (assistant vs user) and no anchored-scroll shape (hermes's `session_id`+`around_message_id`+`window` re-reads a window without FTS5). |
| `session_view(session_id, start_line, end_line)` (`tools/session/view.py`) | *(subsumed by `session_search` scroll shape)* | ~ | Verbatim transcript slice by line range. Hermes folds the same need into `session_search`'s anchored-scroll branch rather than a separate tool. |

### 1.4 Web

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `web_search(query, max_results=5, domains=None)` (`web/search.py`) | `web_search(query, limit=5)` (`web_tools.py`) | ✓ | Both Brave-backed. co-cli adds an SSRF-hardened `web/_ssrf.py` + domain filter; hermes uses a generic `url_safety.py`. **Port:** domains filter is a co-cli-specific policy knob; no porting needed. |
| `web_fetch(url, format="markdown", timeout=15)` (`web/fetch.py`) | `web_extract(urls: list[str])` | ~ | Hermes accepts up to 5 URLs per call. co-cli accepts a single URL but exposes `format` (markdown/html/text) and per-call `timeout` knobs hermes lacks. **Multi-URL port rejected as parity-cosmetic** (B1 plan audit, 2026-05-27): `web_fetch` is `is_read_only=True` + `is_concurrent_safe=True`, so pydantic-ai's `sequential=False` already dispatches parallel `web_fetch(url=…)` calls concurrently and isolates per-call errors. A `urls: list[str]` argument would add list validation, dual return shape, multi-domain approval, and batched formatting for **no capability gain**. |

### 1.5 Execution, Jobs & Delegation

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `shell_exec(cmd, timeout=120, workdir=None)` (`shell/execute.py`) | `terminal(command, background, timeout, workdir, pty, notify_on_complete, watch_patterns)` (`terminal_tool.py`) | ~ | Hermes's `terminal` is a superset: built-in background mode with `notify_on_complete` and `watch_patterns` (regex pattern notifier mid-process) and **PTY support** for interactive CLIs. co-cli keeps `shell_exec` blocking-only (with `workdir` confined to workspace) and splits background into `task_*`. **Port candidates (ranked):** (a) `pty=True` for interactive tool use (Codex, Claude Code, Python REPL invocation) — high value, small surface; (b) `watch_patterns` for long-running `task_*` — real utility for build/test watching; (c) unified `shell_exec`+`task_start` is a larger redesign. |
| `task_start(command, description, working_directory)` (`tasks/control.py`) | `terminal(background=True, notify_on_complete, watch_patterns)` + `process(action, session_id)` (`process_registry.py`) | ~ | Hermes collapses task lifecycle into two tools. Hermes's `process` supports `list`, `poll`, `log`, `wait`, `kill`, `write`, `submit`, `close` — **`write`/`submit`/`close` were co-cli gaps** (can't send stdin to a running task, no clean shutdown channel). **Now planned:** `task_write` + `task_close` in Phase 2 of `2026-05-28-200025-toolgap-interactive-terminal.md` — opens `stdin=PIPE` on the background spawn and adds the write/EOF tools. This is the interactive-drive enhancement sequenced alongside (Phase 1) the `shell_exec` pty fidelity step in the same plan. |
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
| *(none)* | `send_message` (`send_message_tool.py`) | — | **Hermes-only.** Cross-platform messaging dispatcher (Telegram/Discord/Slack/SMS/WeChat/Email). Out of scope (no co-cli use case; large auth surface). |
| *(none)* | `computer_use` (`computer_use_tool.py`) | — | **Hermes-only (new).** macOS desktop control via cua-driver (screenshots, mouse, keyboard); `check_fn`-gated on driver install. Out of scope for co-cli — different interaction model than a terminal assistant. |
| *(none)* | `x_search` (`x_search_tool.py`) | — | **Hermes-only (new).** X/Twitter post search via xAI's `/v1/responses` tool. Out of scope (no co-cli use case; xAI-credential surface). |
| *(none)* | `video_analyze`, `video_generate` (`vision_tools.py`, `video_generation_tool.py`) | — | **Hermes-only (new).** Video understanding + text/image→video generation. Out of scope. |
| *(none)* | `kanban_*` (9 tools: `show`, `list`, `complete`, `block`, `heartbeat`, `comment`, `create`, `unblock`, `link`) (`kanban_tools.py`) | — | **Hermes-only (new).** Multi-agent coordination board (structured handoffs, human-input blocking, orchestrator-only ops). co-cli covers in-process coordination through its `delegate`-style subagents (`web_research`/`knowledge_analyze`/`reason`) — no cross-agent board. Out of scope unless co commits to persistent multi-agent orchestration. |

---

## 2. Hermes Tools Without co-cli Equivalents

Capabilities co-cli does not have, grouped by porting recommendation.

### 2.1 Worth Considering

> **Shipped since 2026-05-08:** `skill_view` + `skill_manage` (`tools/system/skills.py`) —
> both the read and the write surface ported (read via `2026-05-07-125538-skill-tools-hermes-port.md`,
> write via `2026-05-09-154112-skill-manage-hermes-port.md`). co-cli has **no `skills_list`**
> by design: skill discovery is the static `<available_skills>` manifest injected into the
> prompt, not a model-callable list tool.

| hermes tool | File | Why it might matter for co-cli |
|---|---|---|
| `session_search` `role_filter` + anchored scroll | `session_search_tool.py` | The boolean-syntax + no-query-mode parity is shipped (see §1.3). **`role_filter` WITHDRAWN (2026-05-28)** as an intentional architecture divergence, not a defect — co's multi-message chunking makes role a sub-span property; clean filtering would need per-message re-indexing for marginal value (see priority table). The `session_id`+`around_message_id`+`window` anchored-scroll shape remains a differentiator but is covered today by `session_view` (verbatim slice). |
| `vision_analyze` / local document handling | `vision_tools.py` | **Both halves now planned.** Document half — `documents` skill (TASK-4 in `2026-05-27-133104-skill-porting-mission-converged.md`) drives `shell_exec` + `scripts/extract_pdf.py` (pymupdf4llm). The earlier `document_extract` tool design was rejected: in-process `asyncio.to_thread` gives no RSS isolation for large PDFs; a subprocess does. Two earlier dedicated plans (`2026-05-27-171910-documents-skill.md`, `2026-05-27-172717-skill-documents.md`) were withdrawn and consolidated. Vision half — `image_view` tool in `2026-05-28-150239-vision-input.md`: `BinaryContent` + a `build_vision_model` helper (Gemini native multimodal; Ollama needs optional `llm.vision_model`), single model request, `check_fn` capability gate. |
| `terminal.watch_patterns` / `terminal.pty` | `terminal_tool.py` | See §1.5 for the feature-level port. |
| `web_extract` multi-URL | `web_tools.py` | See §1.4 — co-cli's `web_fetch` is still single-URL; hermes takes up to 5 per call. |

### 2.2 Probably Out of Scope

| hermes tool | Why skip |
|---|---|
| `browser_*` (12 tools: `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console`, plus `browser_cdp` + `browser_dialog` for low-level Chrome DevTools Protocol access) | Requires a browser stack (Camofox / Browserbase / Firecrawl). Large dependency surface; overlaps with `web_fetch` for most read-only needs. Only worth porting if co-cli commits to browser automation as a capability. |
| `image_generate`, `video_generate` | Niche; Google/OpenAI media APIs have simpler direct usage. No co-cli generation workflow. |
| `video_analyze` | Niche; co-cli has no vision/video pipeline (the in-flight document-handling port is text extraction, not media analysis). |
| `computer_use` | macOS desktop automation (cua-driver). Different interaction model than a terminal assistant; large surface. |
| `x_search` | X/Twitter search via xAI. No co-cli use case; xAI-credential surface. |
| `kanban_*` (9 tools) | Cross-agent coordination board. co-cli's in-process subagent triad covers delegation without a persistent board. Out of scope unless co commits to multi-agent orchestration. |
| `text_to_speech` | Niche; overlaps no existing co-cli workflow. |
| `cronjob` | co-cli is a user-interactive CLI; cron-style scheduling is better handled by OS cron or Claude Code's CronCreate. |
| `send_message` (Telegram/Discord/Slack/SMS) | No current co-cli use case; large auth/config surface. |
| `discord`, `discord_admin` | Discord-bot read + admin tools. No co-cli use case. |
| `feishu_doc_read`, `feishu_drive_*` (4 tools) | Feishu/Lark enterprise messaging bots. No co-cli use case. |
| `yb_*` (5 tools: yuanbao group/member queries, DM, sticker search/send) | Yuanbao platform integration. No co-cli use case. |
| `ha_*` (HomeAssistant: list_entities, get_state, list_services, call_service) | Domain-specific; co-cli's equivalent is Google integrations. |
| `mixture_of_agents` | Already covered by co-cli's `reason` + `web_research` + `knowledge_analyze` triad. |

### 2.3 Hermes-Only Patterns (not tools) Worth Adopting

These are architecture choices, not tool ports — covered in §3 and §4.

- Named toolset profiles (`toolsets.py`) — see §3.4.
- Uniform spill enforcement across native + MCP tools — see §3.6.
- AST-based auto-discovery of `registry.register()` calls — see §3.2.

---

## 3. Architecture-Level Gaps

### 3.1 No spill enforcement on MCP tool results
**Done (code-verified 2026-05-08; re-verified 2026-05-27).** `CoToolLifecycle.after_tool_execute`
(`lifecycle.py:249-273`) coerces MCP-source string results through `spill_with_span()` before the span block.
Guard condition: `isinstance(result, str) and info and info.source == ToolSourceEnum.MCP`.
Uses per-tool `spill_threshold_chars` override, falls back to global `SPILL_THRESHOLD_CHARS`.

### 3.2 `NATIVE_TOOLS` is a manual tuple
**Done (code-verified 2026-05-08; re-verified 2026-05-27).** `NATIVE_TOOLS` is gone.
`@agent_tool(register=True)` (default) self-registers the decorated function into
`TOOL_REGISTRY` (`tools/agent_tool.py`) at module import time. `agent/toolset.py` imports
all tool modules as a side effect to populate the registry, then iterates it in
`_build_native_toolset()`. `register=False` opts out at the definition site; no tool
opts out today (the registry dump returns all 37 decorated tools).

The previous `_OPT_OUT_TOOLS` frozenset and `_assert_decorated_tools_listed()` guard
are removed — registration IS the decorator, so the listed-vs-decorated consistency
check is meaningless. The earlier "no module-level decorator registry" stance was
reversed: import-order coupling is bounded by `agent/toolset.py` importing every
tool module up front, and `discover_delegation_tools()` triggers the same import as a
guard for standalone callers.

### 3.3 Sequential MCP `list_tools()` discovery
**Fixed.** `discover_mcp_tools()` (`agent/mcp.py`) now fans out all `list_tools()`
calls concurrently via `asyncio.gather` through a `_discover_one` helper. Startup
delay is now `max(timeouts)` instead of `N × timeout`.

### 3.4 No named toolset profiles for delegation
**Partial (code-verified 2026-05-28).** The 2026-05-08 verdict ("`delegation` field on
`@agent_tool` + `discover_delegation_tools()`") was wrong against the current code:
`@agent_tool` has no `delegation` parameter, and no `discover_delegation_tools()`
function exists. The actual mechanism today is `TaskAgentSpec.tool_names=(…)` tuples
on each delegation spec (`co_cli/tools/agents/delegation.py:73-108`): each spec
hardcodes the tool *names* it needs, and the runtime resolves those names against
`TOOL_REGISTRY` at agent construction. That keeps tools registered in one place
(`TOOL_REGISTRY`) so the §3.2 self-registration win still holds, but the §4.5
anti-pattern ("re-enumerate tool lists by hand") remains live — adding a tool to a
profile is still a tuple edit on the spec, not a decorator tag, and requires_config
gating is per-spec rather than automatic.

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
**Done (code-verified 2026-05-27).** `_SanitizingMCPServer` (`agent/mcp.py:18`) wraps
each MCP server and runs `sanitize_mcp_schema()` (`tools/mcp_schema.py`) over every
`inputSchema` on each `list_tools()` call before pydantic-ai ingests it. Normalizes the
shapes that break Ollama / llama.cpp: collapses `"type": ["string", "null"]` arrays,
collapses nullable `anyOf`/`oneOf` unions, fixes bare object nodes missing `properties`,
and recurses children. Shipped via `docs/exec-plans/completed/2026-05-07-112044-mcp-schema-sanitizer.md`.
co-cli's sanitizer is narrower than hermes's `tools/schema_sanitizer.py` (446 lines) —
hermes also strips top-level combinators for Codex, and has reactive
`strip_pattern_and_format` / `strip_slash_enum` passes triggered by backend 400s.
Those are backend-specific to xAI/Codex; not needed for co-cli's Ollama/Anthropic targets today.

### 3.9 No `check_fn` result cache
co-cli's `_make_prepare(fn)` (`agent/toolset.py`) calls `info.check_fn(deps)` on
every prepare invocation. For tools whose `check_fn` probes external state (e.g.
integration credentials, file system), this is wasted work. Hermes caches `check_fn`
results for 30 s (`registry.py:113–134`). Low-impact today (co-cli `check_fn`s are
cheap — they read in-memory `deps`), but worth noting if external-probe `check_fn`s
are added.

---

## 4. Anti-Patterns (co-cli choices that cause subtle problems)

### 4.1 `tool_output_raw()` silently bypasses spill + telemetry
`tool_io.py:261-270` skips `spill_if_oversized()`, the `tool_budget.spill_tool_result`
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
**Open (re-verified 2026-05-28).** The 2026-05-08 "done" verdict was retracted in
§3.4. Today each `TaskAgentSpec` carries its own hardcoded `tool_names=(…)` tuple
of registry-resolved names (`co_cli/tools/agents/delegation.py:73-108`). The shape
is one indirection cleaner than the old `tool_fns=[...]` import-and-list, and the
registry lookup catches typos at agent-construction time, but adding a tool to a
profile is still a per-spec edit and `requires_config` gating is repeated per-spec.

---

## 5. Priority Ordering

Status legend: ✅ done · 🟢 in flight (active exec-plan) · 🟠 open · ⚪ deferred / out of scope.

| Priority | Status | Item | Risk | Effort |
|---|---|---|---|---|
| **High** | ✅ | MCP `inputSchema` sanitization (§3.8) | MCP tools silently dropped on Ollama / llama.cpp; Anthropic schema rejection | Done — `_SanitizingMCPServer` + `tools/mcp_schema.py`, code-verified 2026-05-27 |
| **High** | ✅ | MCP tool results not spill-gated (§3.1) | Context overflow via runaway MCP | Done — `lifecycle.py:249-257`, code-verified 2026-05-08 |
| **High** | ✅ | `tool_output_raw()` bypasses spill gate (§4.1) | Silent context overflow | Done at v0.8.262 — `tool_output_raw` **deleted**; helper-layer errors now route back through `tool_error` (which spills via `ctx`). Shipped via `docs/exec-plans/completed/2026-05-27-172716-toolgap-b1-fetch-spill.md`. |
| **Medium** | 🟢 | Local document handling (PDF/text extraction), vision-adjacent (§2.1) | Cannot read local PDFs/documents (`file_read` rejects binary) | TASK-4 of `2026-05-27-133104-skill-porting-mission-converged.md` — `documents` skill drives `shell_exec` + `scripts/extract_pdf.py` (`pymupdf4llm`). Standalone `document_extract` tool design rejected: subprocess gives RSS isolation that in-process `asyncio.to_thread` cannot. Two dedicated plans withdrawn and deleted; now consolidated. |
| **Medium** | ✅ | Add model-callable `skill_view` / `skill_manage` (§2.1) | Skill discovery + authoring required user slash commands | Done — `tools/system/skills.py`; read + write surfaces both ported, code-verified 2026-05-27 |
| **Medium** | ✅ | Concurrent MCP `list_tools()` discovery (§3.3) | Cumulative startup delay = N × timeout | Done — `_discover_one` + `asyncio.gather` in `agent/mcp.py` |
| **Medium** | 🟢 | `shell_exec` `pty` flag (§1.5) | CLIs that gate on isatty | `2026-05-28-200025-toolgap-interactive-terminal.md` (Phase 1) — **output fidelity only** (stdlib `pty.openpty`, no dep); interactive stdin drive is Phase 2 of the same plan (`task_write`/`task_close`) |
| **Medium** | ⚪ | `web_fetch` `urls: list[str]` parallel fetch (§1.4) | Sequential latency | **Rejected as parity-cosmetic** (B1 plan audit 2026-05-27): `web_fetch` is `is_read_only=True` + `is_concurrent_safe=True`, so pydantic-ai's `sequential=False` already dispatches parallel `web_fetch(url=…)` calls concurrently with per-call error isolation. The `urls: list[str]` argument would add list validation, dual return shape, multi-domain approval, and batched formatting for no capability gain. |
| **Medium** | 🟠 | `ModelRetry` / `tool_error` unenforced (§4.2) | Retry-budget waste | Medium — ruff rule or base-class signal |
| **Medium** | ✅ | `NATIVE_TOOLS` manual tuple (§3.2) | Tool silently omitted | Done — `TOOL_REGISTRY` self-registration via `@agent_tool`, code-verified 2026-05-08 |
| **Low** | 🟢 | Add image/screenshot vision tool (§2.1) | No vision capability | `2026-05-28-150239-vision-input.md` — `image_view(path, prompt)` over `BinaryContent` + a `build_vision_model` helper (Gemini native; Ollama needs optional `llm.vision_model`). Honest capability gate via `check_fn`. |
| **Low** | 🟢 | Add `role_filter` to `session_search` (§1.3) | Cannot narrow recall to assistant-vs-user | **WITHDRAWN (2026-05-28) — not a defect.** This is an intentional architecture divergence, not a missing feature: hermes indexes 1 message/row with a `role` column (so `role_filter` is free); co deliberately indexes multi-message chunks for recall quality, so role is a sub-span property, not a unit property. Honest parity needs per-message re-indexing (chunker rewrite + recall-quality eval gate) for marginal value — no observed co need. Plan deleted; do **not** re-propose from a signature diff alone. Revisit only on a real, demonstrated recall need. |
| **Low** | 🟠 | Named toolset profiles for delegation (§3.4, §4.5) | Tool-list drift in delegation | **Partial** (re-verified 2026-05-28). 2026-05-08 "done" verdict retracted: no `delegation` field on `@agent_tool`, no `discover_delegation_tools()`. Current mechanism is `TaskAgentSpec.tool_names=(…)` tuple of registry-resolved names. Tools stay in one registry (§3.2 win holds) but each spec still hardcodes its list. Tag-and-filter design is a future option, not a shipped one. |
| **Low** | 🟠 | `file_read_mtimes` unbounded (§4.3) | Memory in very long sessions | Low — cap dict or evict on turn reset |
| **Low** | ⚪ | No permanent approval persistence (§3.7) | UX friction | Medium — deliberate security tradeoff |
| **Low** | ✅ | Background task ring-buffer lossy (§3.6) | Output loss for long commands | Done — file-only per-task log under `LOGS_DIR`, ring buffer removed; `tail_log` helper for reads; `_drain_and_cleanup` unlinks at session end. Code-verified 2026-05-08. |
| **Low** | ⚪ | MCP dynamic tool refresh (§3.5) | Stale tool index in long sessions | Medium — subscribe to `notifications/tools/list_changed`. Deferred: no observed MCP server in co-cli's install set emits `list_changed`; failure mode is bounded (stale-but-additive); `/mcp restart` is a one-line user fix. |
| **Low** | ⚪ | `check_fn` result cache (§3.9) | Wasted work *if* external-probe `check_fn`s are added | Low — copy hermes's 30 s TTL pattern when needed |
