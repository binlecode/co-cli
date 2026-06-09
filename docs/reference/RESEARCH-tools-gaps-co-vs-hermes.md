# RESEARCH: Tool Parity & Lifecycle Gaps — `co-cli` vs Hermes

> **Last refreshed:** 2026-06-08 (co-cli v0.8.323). Tool inventories re-counted
> directly from each repo (co-cli `TOOL_REGISTRY` dump via runtime import; hermes
> `registry.register()` AST scan). Every claim in §1–§5 re-verified against current
> source with file:line cites. Active in-flight items linked to exec-plans.
>
> **Major changes since the 2026-05-28 refresh (v0.8.264 → v0.8.323):**
> - **co-cli surface: 37 → 36 tools**, with a large composition change (below).
> - **Capability API dropped (v0.8.312, `e8e18bf7`):** the pydantic-ai capability
>   SDK coupling was removed. `co_cli/tools/lifecycle.py` and `CoToolLifecycle` are
>   **deleted**. The call-seam logic (MCP-result spill) moved to a `WrapperToolset`
>   subclass `_CallSeamToolset` in `agent/toolset.py`; JSON-arg repair moved to the
>   LLM layer (`co_cli/llm/surrogate_recovery_model.py`); path enforcement moved to
>   explicit boundary guards in `co_cli/tools/files/fs_guards.py`.
> - **New ALWAYS/DEFERRED visibility mechanism (`tool_view`, v0.8.310 + v0.8.314):**
>   co-cli no longer relies on the SDK's `defer_loading`. Tools carry a static
>   `visibility` (`ALWAYS` | `DEFERRED`); DEFERRED tools are hidden every turn by
>   `_tool_visibility_filter()` until the model calls `tool_view(name=…)` to unlock
>   them. Today **19 ALWAYS / 17 DEFERRED**. This is the biggest new architectural
>   divergence from hermes (which gates by runtime `check_fn` + named toolset
>   profiles) — see §1.0 and §3.4.
> - **Removed tools:** `obsidian_list`/`obsidian_search`/`obsidian_read` (`6390d73c`),
>   `code_execute` (`6390d73c`), `file_find` (merged into `file_search`, `6390d73c`),
>   and the **entire mid-turn delegation triad** `reason` (`6390d73c`),
>   `knowledge_analyze` (`0b749cf3`), `web_research` (`00dcdf3c`, v0.8.280).
> - **Split tools:** `memory_manage` → `memory_create` / `memory_append` /
>   `memory_replace` / `memory_delete`; `skill_manage` → `skill_create` /
>   `skill_edit` / `skill_patch` / `skill_delete`.
> - **session_search went file-based ripgrep (v0.8.298):** no FTS5 index over
>   transcripts anymore (`co_cli/session/_search.py`). Memory items still use FTS5/BM25.
> - **Hermes inventory unchanged at 64.**

Code-verified cross-review of co-cli's tool surface and lifecycle against hermes-agent.
Per-tool parity matrix first (what each co tool looks like in hermes, porting implications),
then architecture-level gaps and anti-patterns.

## Sources

### `co-cli` (36 native tools)

**Tool inventory (36, sorted)**: `capabilities_check`, `clarify`, `file_patch`, `file_read`, `file_search`, `file_write`, `google_calendar_list`, `google_calendar_search`, `google_drive_read`, `google_drive_search`, `google_gmail_draft`, `google_gmail_list`, `google_gmail_search`, `memory_append`, `memory_create`, `memory_delete`, `memory_replace`, `memory_search`, `memory_view`, `session_search`, `session_view`, `shell_exec`, `skill_create`, `skill_delete`, `skill_edit`, `skill_patch`, `skill_view`, `task_cancel`, `task_list`, `task_start`, `task_status`, `todo_read`, `todo_write`, `tool_view`, `web_fetch`, `web_search`.

- [`co_cli/agent/toolset.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/toolset.py) — `_build_native_toolset()` iterates `TOOL_REGISTRY` into a pydantic-ai `FunctionToolset`; `_make_prepare(check_fn)` wraps per-tool `check_fn`; `_tool_visibility_filter()` (per-turn ALWAYS/DEFERRED + approval-resume gate); `_CallSeamToolset` (`WrapperToolset`, MCP-result spill at the `call_tool` boundary, lines 140–212)
- [`co_cli/tools/agent_tool.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/agent_tool.py) — `@agent_tool(...)` decorator; `TOOL_REGISTRY` (list) + `TOOL_REGISTRY_BY_NAME` (dict) self-registration at import
- [`co_cli/tools/system/tool_view.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/system/tool_view.py) — name-addressed DEFERRED-tool unlock loader (replaces SDK `defer_loading`)
- [`co_cli/agent/mcp.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/mcp.py) — `_SanitizingMCPServer`, concurrent `_discover_one` + `asyncio.gather`
- [`co_cli/tools/mcp_schema.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/mcp_schema.py) — `sanitize_mcp_schema()` (type-array collapse, nullable-union collapse, object-shape fix, recursion)
- [`co_cli/llm/surrogate_recovery_model.py`](/Users/binle/workspace_genai/co-cli/co_cli/llm/surrogate_recovery_model.py) — JSON tool-arg repair (moved here from the deleted `lifecycle.py`)
- [`co_cli/tools/files/fs_guards.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/files/fs_guards.py) — `enforce_read_boundary` / `enforce_write_boundary` (explicit path resolution + containment, called inside the file tools)
- [`co_cli/tools/categories.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/categories.py) — `FILE_TOOLS` only (`PATH_NORMALIZATION_TOOLS` and `COMPACTABLE_TOOLS` both removed)
- [`co_cli/tools/approvals.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/approvals.py)
- [`co_cli/tools/tool_io.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/tool_io.py) — `tool_output`/`tool_error`, `spill_if_oversized`, `spill_with_span`, `SPILL_THRESHOLD_CHARS=4_000` (`tool_output_raw` **deleted**)
- [`co_cli/tools/background.py`](/Users/binle/workspace_genai/co-cli/co_cli/tools/background.py) — file-only per-task log (`bg-{task_id}.log`), `tail_log` helper, no in-memory ring buffer
- [`co_cli/agent/spec.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/spec.py) + [`co_cli/agent/build.py`](/Users/binle/workspace_genai/co-cli/co_cli/agent/build.py) — `TaskAgentSpec.tool_names` resolved against `TOOL_REGISTRY_BY_NAME` (daemon task agents only; no mid-turn delegation)
- [`co_cli/daemons/dream/_reviewer.py`](/Users/binle/workspace_genai/co-cli/co_cli/daemons/dream/_reviewer.py) — `MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC` (the only `TaskAgentSpec`s; daemon-launched, never mid-turn)
- Tool implementations:
  `co_cli/tools/system/{user_input,capabilities,skills,tool_view}.py`, `co_cli/tools/todo/rw.py`,
  `co_cli/tools/shell/execute.py`, `co_cli/tools/tasks/control.py`,
  `co_cli/tools/{files,memory,session,web,google}/*.py`
- Memory + session libraries: `co_cli/memory/` (knowledge items, FTS5/BM25 via `co_cli/index/`) and `co_cli/session/` (transcripts, **ripgrep** — no index, see `session/_search.py`)
- [`docs/specs/tools.md`](/Users/binle/workspace_genai/co-cli/docs/specs/tools.md), [`docs/specs/memory.md`](/Users/binle/workspace_genai/co-cli/docs/specs/memory.md), [`docs/specs/agents.md`](/Users/binle/workspace_genai/co-cli/docs/specs/agents.md)

### Hermes (64 registered tools, as of 2026-06-08)

- `/Users/binle/workspace_genai/hermes-agent/tools/registry.py` — singleton `ToolRegistry`, AST auto-discovery, `check_fn`, 30 s `check_fn` TTL cache
- `/Users/binle/workspace_genai/hermes-agent/tools/approval.py`
- `/Users/binle/workspace_genai/hermes-agent/tools/mcp_tool.py` + `tools/schema_sanitizer.py` (MCP `inputSchema` normalization at registration time)
- `/Users/binle/workspace_genai/hermes-agent/toolsets.py` — named toolset profiles (web, file, terminal, skills, browser, browser-cdp, …) plus per-platform supersets (`hermes-cli`, `hermes-discord`, `hermes-telegram`, …)
- Core tool implementations: `tools/{clarify,memory,todo,session_search,file,terminal,process_registry,code_execution,delegate,skills,skill_manager,web,vision,image_generation,video_generation,tts,browser,browser_cdp,browser_dialog,cronjob,send_message,homeassistant,mixture_of_agents,computer_use,x_search,kanban}_tool*.py`
- Platform-specific tool implementations: `tools/{discord,feishu_doc,feishu_drive,yuanbao}_tool*.py`

**Tool inventory (64, sorted)**: `browser_back`, `browser_cdp`, `browser_click`, `browser_console`, `browser_dialog`, `browser_get_images`, `browser_navigate`, `browser_press`, `browser_scroll`, `browser_snapshot`, `browser_type`, `browser_vision`, `clarify`, `computer_use`, `cronjob`, `delegate_task`, `discord`, `discord_admin`, `execute_code`, `feishu_doc_read`, `feishu_drive_add_comment`, `feishu_drive_list_comment_replies`, `feishu_drive_list_comments`, `feishu_drive_reply_comment`, `ha_call_service`, `ha_get_state`, `ha_list_entities`, `ha_list_services`, `image_generate`, `kanban_block`, `kanban_comment`, `kanban_complete`, `kanban_create`, `kanban_heartbeat`, `kanban_link`, `kanban_list`, `kanban_show`, `kanban_unblock`, `memory`, `mixture_of_agents`, `patch`, `process`, `read_file`, `search_files`, `send_message`, `session_search`, `skill_manage`, `skill_view`, `skills_list`, `terminal`, `text_to_speech`, `todo`, `video_analyze`, `video_generate`, `vision_analyze`, `web_extract`, `web_search`, `write_file`, `x_search`, `yb_query_group_info`, `yb_query_group_members`, `yb_search_sticker`, `yb_send_dm`, `yb_send_sticker`.

Hermes surface is unchanged vs the prior refresh (last 30 commits are desktop/docker fixes, MCP OAuth, web-endpoint config, UI — no tool registrations or removals).

## Methodology

co-cli uses pydantic-ai's `FunctionToolset` with an `@agent_tool(...)` decorator
(`tools/agent_tool.py:25–38`) that attaches `ToolInfo` (visibility, approval,
`is_concurrent_safe`, `integration`, `requires_config`, `retries`,
`spill_threshold_chars`, optional `check_fn`, optional `approval_subject_fn`) at
definition site. Each decorated function self-registers into the module-level
`TOOL_REGISTRY` (list) and `TOOL_REGISTRY_BY_NAME` (dict) at import time
(`register=True` default). `_build_native_toolset()` (`agent/toolset.py:101–137`)
imports every tool module up front to populate the registry, then iterates it into a
`FunctionToolset`, attaching a per-turn `prepare` callback (`_make_prepare` wrapping
`check_fn`) and excluding unconfigured integrations.

Two co-specific mechanisms replace SDK machinery the capability-API drop (v0.8.312)
removed:

- **Static visibility + `tool_view` unlock.** co-cli does **not** pass `defer_loading`
  to pydantic-ai. Instead every tool carries `visibility ∈ {ALWAYS, DEFERRED}`
  (`deps.py:85–89`). `_tool_visibility_filter()` (`toolset.py:62–85`) hides DEFERRED
  tools each turn unless their canonical name is in `ctx.deps.runtime.unlocked_tools`,
  and on approval-resume turns narrows to approved + ALWAYS tools. The model calls
  `tool_view(name=…)` (`system/tool_view.py:52–104`) — normalized-exact match unlocks
  the tool; a near-miss returns difflib suggestions (cutoff 0.6) without unlocking;
  no match returns a "do not retry" error.
- **Call-seam `WrapperToolset`.** `_CallSeamToolset` (`toolset.py:140–212`) wraps
  `call_tool` to coerce oversized MCP string results through `spill_with_span()`.
  Deferred-approval is still handled by the SDK + `_tool_visibility_filter`'s
  resume gate; JSON tool-arg repair is now an LLM-layer concern
  (`llm/surrogate_recovery_model.py`); file-path resolution/containment is enforced
  explicitly inside the file tools via `fs_guards.py`.

Hermes uses a thread-safe singleton `ToolRegistry` with AST-based auto-discovery
(`registry.py` scans `tools/*.py` for module-level `registry.register()`), per-tool
`check_fn` for runtime availability with a 30 s TTL cache so probes against external
state (Docker daemon, Modal SDK, Playwright binary) don't fire every
`get_definitions()` call. `toolset` strings group tools into named profiles
(`toolsets.py`); approval flows through a blocking gateway; schemas are supplied as
raw JSON-Schema dicts and normalized via `schema_sanitizer.py` at MCP-ingestion time.

The defining difference is **how each manages model-visible surface area**: co-cli
ships a small static ALWAYS floor and gates the rest behind a model-driven
`tool_view` unlock; hermes keeps everything registered and gates by runtime `check_fn`
+ static named toolset profiles per platform.

---

## 1.0 ALWAYS vs DEFERRED — co-cli's visibility floor

Code-verified (`toolset.py:19–137`, per-tool `@agent_tool(visibility=…)`):

| Bucket | Count | Tools |
|---|---|---|
| **ALWAYS** | 19 | `memory_search`, `memory_view`, `memory_create`, `memory_append`, `memory_replace`, `memory_delete`, `file_read`, `file_search`, `file_write`, `file_patch`, `web_search`, `web_fetch`, `shell_exec`, `skill_view`, `tool_view`, `capabilities_check`, `clarify`, `todo_write`, `todo_read` |
| **DEFERRED** | 17 | `skill_create`, `skill_edit`, `skill_patch`, `skill_delete`, `session_search`, `session_view`, `task_start`, `task_status`, `task_cancel`, `task_list`, `google_gmail_list`, `google_gmail_search`, `google_gmail_draft`, `google_drive_search`, `google_drive_read`, `google_calendar_list`, `google_calendar_search` |

The ALWAYS floor is the always-paid schema cost in every request (relevant for the
Ollama cache prefix). DEFERRED tools cost one `tool_view(name=…)` round-trip the first
time the model needs them in a session. The episodic/low-frequency surfaces (skill
authoring, session recall, background tasks, all Google integrations) are DEFERRED; the
recall-read + file + web + shell + todo + clarify core is ALWAYS. Hermes has no
equivalent static floor — its analogue is `check_fn` runtime gating plus per-platform
toolset profiles in `toolsets.py`.

---

## 1. Per-Tool Parity Matrix

Columns: **co-cli tool** · **hermes equivalent** · **Parity** (✓ = same semantics,
~ = partial / different shape, ✗ = no equivalent) · **Gap & porting notes**.

### 1.1 Interaction & Session Control

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `clarify(questions: list[dict], user_answers: list[str] \| None)` (`system/user_input.py`) | `clarify(question, choices, callback)` (`clarify_tool.py`) | ~ | co-cli batches *all related* questions in one call — `questions[]` is a list of `{question, options, multiple}` dicts; `user_answers[]` is system-injected on resume via `ToolApproved(override_args=...)`. Hermes asks one `question` with optional `choices` and a blocking-gateway `callback`. ALWAYS in both. |
| `capabilities_check()` (`system/capabilities.py`) | *(none — hermes exposes `registry.get_available_toolsets()` to the CLI, not the model)* | ✗ | co-cli uniquely exposes the runtime capability surface to the model: ALWAYS vs DEFERRED tools, degraded integrations, MCP server health, reasoning-model status. `/doctor` wraps it but the model also calls it directly for "what can you do / why is X broken". ALWAYS. No port — hermes's `check_fn` registry *is* its introspection surface. |
| `todo_write(items, merge=False)` / `todo_read()` (`todo/rw.py`) | `todo(todos, merge)` (`todo_tool.py`, single op-based) | ✓ | co-cli keeps read/write split (both ALWAYS) where hermes collapses to one op-based `todo`. `merge=True` updates items by `id` without re-submitting the full list. co-cli retains a per-item `priority` field (high/medium/low) hermes lacks, plus a one-in-progress guard. State session-scoped in `CoSessionState`; hermes persists. |

### 1.2 Workspace & File Operations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `file_search(path="**/*", content=None, case_insensitive, files_only, limit=50, offset=0)` (`files/read.py:478`) | `search_files(pattern, target, path, file_glob, limit, offset, output_mode, context)` (`file_tools.py`) | ~ | **`file_find` was removed (`6390d73c`) and merged here.** co-cli's `file_search` is presence-based: `content=None` → path-glob discovery (replaces `find`/`ls`); `content="regex"` → content search (replaces `grep`/`rg`). Docstring: *"Find files by path glob, or regex-search inside them. Replaces grep/rg/find/ls."* Hermes's `search_files` dispatches on a `target` arg (`content`/`files`). Functionally equivalent shapes; **no port needed.** |
| `file_read(path, start_line, end_line)` (`files/read.py:392`) | `read_file(path, offset, limit)` (`file_tools.py`) | ✓ | Both support line ranges; both cap size. Hermes's cap is char-based (`_DEFAULT_MAX_READ_CHARS=100_000`); co-cli uses line count (default 500, max 2000) + a full-read gate + 2000-char per-line truncation, and opts out of spilling via `spill_threshold_chars=math.inf` (the per-line caps already enforce shape). co-cli's 2000-char per-line truncation is a practical guard hermes lacks — keep. |
| `file_write(path, content)` (`files/write.py:264`) | `write_file(path, content, cross_profile)` | ✓ | Both deferred-approval; both write atomically. co-cli adds `resource_locks` + a `FileReadTracker` staleness check via `fs_guards.enforce_write_boundary` (workspace containment) — **hermes lacks both**; hermes instead has a `cross_profile` escape hatch co-cli does not. |
| `file_patch(path, old_string, new_string, replace_all, show_diff)` (`files/write.py:316`) | `patch(mode, path, old_string, new_string, replace_all, patch)` | ~ | co-cli's `file_patch` is a single-file fuzzy `old_string`/`new_string` edit with four-strategy fallback (exact, line-trimmed, indent-stripped, escape-expanded); in-file deletion via `new_string=""`; whole-file delete moves to `shell_exec` (`rm`). Hermes's `patch` carries a V4A `mode` branch (`patch_parser.py`, targets large frontier models) alongside a plain `replace` mode. V4A is OpenAI-Codex-native and incompatible with co's small local models. co-cli's auto-lint on `.py` + read-before-write enforcement (via `FileReadTracker`) are **co-cli-only** guardrails worth keeping. |

### 1.3 Memory & Session Recall

> **Architecture (current, v0.8.323):** Memory items live in flat `~/.co-cli/memory/*.md`
> and are searched via FTS5/BM25 over the shared `co_cli/index/` facade. **Session
> transcripts are searched with ripgrep over the raw JSONL files — there is no index**
> (v0.8.298, `session/_search.py`). The recall surface is search-driven (no
> `memory_list`/`memory_read`/`memory_manage`): browse with `memory_search`
> (empty/kind-filtered query); full-body reads via `memory_view(name)` keyed by the
> `filename_stem` a hit surfaces; verbatim transcript slices via
> `session_view(session_id, start_line, end_line)`.
>
> Memory write was **split** from the old single `memory_manage` into four addressable
> tools (`memory_create`/`append`/`replace`/`delete`), all ALWAYS, all approval-gated.
> Memory kinds: `user`, `rule`, `article`, `note` (CANON is system-reserved, excluded
> from write ops). The model-callable surface is now **eight** tools: six memory
> (`memory_search`, `memory_view`, `memory_create`, `memory_append`, `memory_replace`,
> `memory_delete`) and two session (`session_search`, `session_view`, both DEFERRED).

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `memory_search(query="", kinds, limit=10)` (`memory/recall.py`) | *(no direct equivalent — hermes splits to `memory` injection + `session_search`)* | ~ | FTS5/BM25 over memory items only; supports `OR`/`NOT`/phrase/prefix*; empty query browses recent. Falls back to grep if the store is unavailable. ALWAYS. |
| `memory_view(name)` (`memory/view.py`) | *(none)* | ✗ | Full-body memory-item read by `filename_stem`; `spill_threshold_chars=inf`. Hermes has no addressable per-item read — its memory is a single injected `MEMORY.md`/`USER.md` snapshot. ALWAYS. |
| `memory_create` / `memory_append` / `memory_replace` / `memory_delete` (`memory/manage.py`) | `memory(action, target, content, old_text)` (`memory_tool.py`) | ~ | co-cli unifies create/append/replace/delete as **four separate addressable tools** over `user`/`rule`/`article`/`note` items, each approval-gated with a per-tool `approval_subject_fn`; `memory_replace` has an exact-match-once guard on `section`; `memory_create` takes `source_url` for article dedup. Hermes's single op-based `memory` does add/replace/remove against a frozen `MEMORY.md`/`USER.md` injected into the system prompt — **different architecture, not a missing tool.** All four ALWAYS. |
| `session_search(query="", limit, ...)` (`session/recall.py`) | `session_search(query, role_filter, limit, session_id, around_message_id, window, sort)` (`session_search_tool.py`) | ~ | **co-cli now ripgrep-based** (no index): line-cited lexical hits with `start_line`/`end_line`; empty query → recent-sessions browse (excludes active session); ≤3 unique sessions per call. DEFERRED. **Remaining gaps vs hermes:** no `role_filter` (assistant vs user) and no anchored-scroll shape (`session_id`+`around_message_id`+`window`) — covered today by `session_view`. |
| `session_view(session_id, start_line, end_line)` (`session/view.py`) | *(subsumed by `session_search` scroll shape)* | ~ | Verbatim transcript slice by line range. DEFERRED. Hermes folds the same need into `session_search`'s anchored-scroll branch. |

### 1.4 Web

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `web_search(query, max_results=5, domains=None)` (`web/search.py`) | `web_search(query, limit=5)` (`web_tools.py`) | ✓ | co-cli Brave-backed with an SSRF-hardened `web/_ssrf.py` + domain filter; hermes Parallel/Firecrawl-backed with generic `url_safety.py`. ALWAYS. Domains filter is a co-cli policy knob; no port. |
| `web_fetch(url, format="markdown", timeout=15)` (`web/fetch.py`) | `web_extract(urls: list[str], format, use_llm_processing, min_length, max_length)` | ~ | co-cli accepts a single URL with `format` (markdown/html/text) + per-call `timeout`; **v0.8.280 added trafilatura main-article extraction** (fail-open to full-page `html2text`). Hermes accepts up to 5 URLs and offers optional LLM summarization of the result. ALWAYS. **Multi-URL port rejected as parity-cosmetic:** `web_fetch` is `is_concurrent_safe=True`, so pydantic-ai's `sequential=False` already dispatches parallel `web_fetch(url=…)` calls concurrently with per-call error isolation. |

### 1.5 Execution, Jobs & Delegation

> **Major change:** co-cli **removed all mid-turn delegation.** The triad
> `web_research` (`00dcdf3c`), `knowledge_analyze` (`0b749cf3`), and `reason`
> (`6390d73c`) are gone, along with `code_execute` (`6390d73c`). The only sub-agent
> path left is **daemon-launched** task agents (`MEMORY_REVIEW_SPEC`,
> `SKILL_REVIEW_SPEC` in `daemons/dream/_reviewer.py`), built by `build_task_agent()`
> from `TaskAgentSpec.tool_names` — never invoked mid-turn. The foreground loop has no
> model-callable delegation tool. Hermes's `delegate_task` has no co-cli equivalent.

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| `shell_exec(cmd, timeout=120, workdir=None)` (`shell/execute.py`) | `terminal(command, background, timeout, workdir, pty, notify_on_complete, watch_patterns, force)` (`terminal_tool.py`) | ~ | Hermes's `terminal` is a superset: built-in background mode with `notify_on_complete` + `watch_patterns` (regex mid-process notifier) and **PTY support**. co-cli keeps `shell_exec` blocking-only (ALWAYS, `workdir` confined to workspace) and splits background into `task_*`. **Port candidates (in flight):** `pty` fidelity + interactive `task_write`/`task_close` are Phases 1–2 of the still-active `2026-05-28-200025-toolgap-interactive-terminal.md` plan (not yet shipped — no `pty` arg in `shell/execute.py`, no `task_write`/`task_close` tools). |
| `task_start(command, description, working_directory)` (`tasks/control.py`) | `terminal(background=True, …)` + `process(action, session_id)` (`process_registry.py`) | ~ | Hermes's `process` supports `list`/`poll`/`log`/`wait`/`kill`/`write`/`submit`/`close`. co-cli's `task_*` (all DEFERRED) cover start/status/cancel/list but **lack stdin write + clean shutdown** — the `task_write`/`task_close` gap is Phase 2 of the interactive-terminal plan (still open). |
| `task_status(task_id, tail_lines=20)` | `process(action="poll"/"log")` | ~ | Same surface; tails the per-task log file (§3.6). DEFERRED. |
| `task_cancel(task_id)` | `process(action="kill")` | ✓ | Both SIGTERM→SIGKILL via process group; co-cli drains the monitor task and reports `cleanup_incomplete` on timeout. DEFERRED. |
| `task_list(status_filter)` | `process(action="list")` | ✓ | Equivalent. DEFERRED. |
| *(removed — was `code_execute`)* | `execute_code(code, task_id, enabled_tools)` (`code_execution_tool.py`) | ✗ | **co-cli removed `code_execute` (`6390d73c`).** Code invocation now routes through `shell_exec` only. Hermes runs code in sandboxed backends (`environments/{daytona,docker,local,modal,singularity,ssh}.py`) with optional RPC back to a tool subset — a major architectural surface co-cli deliberately dropped (host shell + shell-policy gate only). |
| *(removed — was `web_research`)* | `delegate_task(goal, context, toolsets, tasks, max_iterations, acp_command, acp_args, role)` (`delegate_tool.py`) | ✗ | **co-cli removed mid-turn delegation entirely.** Hermes has one parameterized delegation tool where the model picks a toolset profile. co-cli's only sub-agent path is the daemon dream-reviewer (memory/skill review), not model-callable. |
| *(removed — was `knowledge_analyze`)* | `delegate_task(toolsets=["file","session_search"], …)` | ✗ | Removed (`0b749cf3`), along with the orphaned `run_in_turn` runner. |
| *(removed — was `reason`)* | `delegate_task(toolsets=[], …)` | ✗ | Tool-free reasoning agent removed (`6390d73c`). |

### 1.6 External Service Integrations

| co-cli tool | hermes equivalent | Parity | Gap & porting notes |
|---|---|---|---|
| *(removed — was `obsidian_list`/`obsidian_search`/`obsidian_read`)* | *(none)* | — | **co-cli removed all Obsidian tools (`6390d73c`); the whole `co_cli/tools/obsidian/` dir and the `obsidian_vault_path` config key are gone.** No replacement — local-folder reads route through `file_search` / `file_read`. Stale `obsidian` mentions linger only in `docs/specs/memory.md` historical source-type lists. |
| `google_drive_search`, `google_drive_read`, `google_gmail_list`, `google_gmail_search`, `google_calendar_list`, `google_calendar_search`, `google_gmail_draft` (`google/*.py`) | *(none)* | ✗ | Co-cli-only. **All DEFERRED**, gated on `google_credentials_path` via `check_fn=_google_available` (probes `_creds_resolved` each turn — see §3.9). v0.8.280 added least-privilege auth + `co google auth setup`. |
| *(none)* | `ha_*` (4), `discord`/`discord_admin`, `feishu_*` (4), `yb_*` (5), `send_message`, `computer_use`, `x_search`, `video_analyze`/`video_generate`, `image_generate`, `text_to_speech`, `kanban_*` (9) | — | **Hermes-only.** Smart-home, platform messaging, desktop control, media gen, and the multi-agent kanban board. All out of scope for a local terminal assistant — see §2.2. |

---

## 2. Hermes Tools Without co-cli Equivalents

Capabilities co-cli does not have, grouped by porting recommendation.

### 2.1 Worth Considering

> **Shipped:** `skill_view` + the four skill-write tools (`skill_create`/`edit`/`patch`/`delete`,
> `tools/system/skills.py`). co-cli has **no `skills_list`** by design — skill discovery
> is the static `<available_skills>` manifest in the prompt, not a model-callable list
> tool. `skill_view` is ALWAYS; the four write tools are DEFERRED (v0.8.314).

| hermes tool | File | Why it might matter for co-cli |
|---|---|---|
| `session_search` `role_filter` + anchored scroll | `session_search_tool.py` | **`role_filter` WITHDRAWN** as an intentional architecture divergence — co's multi-line ripgrep hits make role a sub-span property, not a row property; clean filtering would need per-message indexing for marginal value. The `session_id`+`around_message_id`+`window` anchored-scroll shape is covered today by `session_view` (verbatim slice). |
| `vision_analyze` / local document handling | `vision_tools.py` | **Both halves planned but NOT yet shipped** (active plans). Document half — `documents` skill (TASK-4 of `2026-05-27-133104-skill-porting-mission-converged.md`) drives `shell_exec` + a `scripts/extract_pdf.py` helper (pymupdf4llm); subprocess chosen over in-process `asyncio.to_thread` for RSS isolation. Vision half — `image_view` tool in `2026-05-28-150239-vision-input.md` (`BinaryContent` + a `build_vision_model` helper, `check_fn` gate). Neither has landed: no `extract_pdf.py`, no `image_view`/`build_vision_model` in source. |
| `terminal.watch_patterns` / `terminal.pty` + interactive stdin (`task_write`/`task_close`) | `terminal_tool.py`, `process_registry.py` | In flight (`2026-05-28-200025-toolgap-interactive-terminal.md`), not shipped — see §1.5. |
| `web_extract` multi-URL | `web_tools.py` | See §1.4 — co-cli's `web_fetch` is single-URL by design; rejected as parity-cosmetic. |
| `execute_code` sandboxed | `code_execution_tool.py` | co-cli **removed** its `code_execute`; re-adding would mean a sandbox backend (out of scope — host-shell-only stance). |

### 2.2 Probably Out of Scope

| hermes tool | Why skip |
|---|---|
| `browser_*` (12) | Requires a browser stack (Camofox / Browserbase / Firecrawl). Large dependency surface; overlaps with `web_fetch` for read-only needs. |
| `delegate_task` | co-cli deliberately removed mid-turn delegation; re-adding contradicts the current single-loop design. |
| `image_generate`, `video_generate`, `video_analyze` | Niche; no co-cli media workflow. |
| `computer_use` | macOS desktop automation (cua-driver); different interaction model. |
| `x_search` | X/Twitter via xAI; no use case; xAI-credential surface. |
| `kanban_*` (9) | Cross-agent coordination board; co-cli has no persistent multi-agent orchestration. |
| `text_to_speech` | Niche. |
| `cronjob` | co-cli is user-interactive; cron belongs to OS cron / Claude Code CronCreate. |
| `send_message`, `discord`/`discord_admin`, `feishu_*` (4), `yb_*` (5) | Platform messaging bots; no co-cli use case; large auth surface. |
| `ha_*` (4) | HomeAssistant; co-cli's domain equivalent is Google integrations. |
| `mixture_of_agents` | No co-cli analogue now that the in-turn delegation triad is removed; would require a delegation surface co-cli deliberately dropped. |

### 2.3 Hermes-Only Patterns (not tools) Worth Adopting

These are architecture choices, not tool ports — covered in §3 and §4.

- Named toolset profiles (`toolsets.py`) — see §3.4.
- `check_fn` TTL cache — see §3.9.
- (Already adopted: uniform spill enforcement §3.1, registry auto-registration §3.2, MCP schema sanitization §3.8.)

---

## 3. Architecture-Level Gaps

### 3.1 Spill enforcement on MCP tool results
**Done (re-verified 2026-06-08).** Logic moved out of the deleted `lifecycle.py` into
`_CallSeamToolset.call_tool` (`agent/toolset.py:199–212`): plain-string MCP results are
coerced through `spill_with_span()` before returning. Guard:
`isinstance(result, str) and info and info.source == ToolSourceEnum.MCP`. Uses per-tool
`spill_threshold_chars` override, falls back to `SPILL_THRESHOLD_CHARS` (4_000).

### 3.2 `NATIVE_TOOLS` is a manual tuple
**Done (re-verified 2026-06-08).** `@agent_tool(register=True)` self-registers into
`TOOL_REGISTRY` (list) and `TOOL_REGISTRY_BY_NAME` (dict) at import (`tools/agent_tool.py`).
`agent/toolset.py` imports all tool modules up front to populate the registry, then
iterates it in `_build_native_toolset()`. The name-keyed dict is also what
`build_task_agent()` resolves daemon `TaskAgentSpec.tool_names` against.

### 3.3 Sequential MCP `list_tools()` discovery
**Fixed (re-verified 2026-06-08).** `discover_mcp_tools()` (`agent/mcp.py:180`) fans out
all `list_tools()` calls concurrently via `asyncio.gather` over `_discover_one`. Startup
delay is `max(timeouts)`, not `N × timeout`.

### 3.4 No named toolset profiles for delegation
**Recontextualized (re-verified 2026-06-08).** With the mid-turn delegation triad
removed, the only `TaskAgentSpec`s are the two **daemon** specs in
`daemons/dream/_reviewer.py` (`MEMORY_REVIEW_SPEC`, `SKILL_REVIEW_SPEC`). Each hardcodes
a `tool_names=(…)` tuple resolved against `TOOL_REGISTRY_BY_NAME` at agent-build time
(`agent/build.py:58–113`). No `delegation` field on `@agent_tool`, no
`discover_delegation_tools()`. The "re-enumerate by hand" shape persists but is now
confined to two daemon specs, not a model-facing surface — far lower stakes than when it
gated three delegation tools.

### 3.5 No MCP dynamic tool refresh
**Deferred (re-verified 2026-06-08).** `discover_mcp_tools()` runs once at bootstrap;
no subscription to `notifications/tools/list_changed` (grep in `agent/mcp.py`: zero
matches). No MCP server in co-cli's typical install set emits `list_changed`; failure
mode is bounded (stale-but-additive); `/mcp restart` is the one-line fix.

### 3.6 Background task output is lossy and in-memory only
**Done (re-verified 2026-06-08).** Each task streams stdout+stderr to a per-task log file
at `LOGS_DIR / f"bg-{task_id}.log"` (`tools/background.py:92`); the file is the single
source of truth (no in-memory ring buffer). Reads tail via `tail_log(path, n)`
(64 KB seek-from-end window, `background.py:122–146`). `_drain_and_cleanup`
(`main.py:213–234`) kills running tasks and unlinks log files at session end.
**Not ported:** orphan reaper for files from sessions that died before cleanup.

### 3.7 No persistent approval rules
**By design (re-verified 2026-06-08).** `session_approval_rules` (`deps.py:150`) is
in-memory per session; `approvals.py` docstring states no cross-session persistence.
Hermes persists `_permanent_approved` to config. Deliberate security tradeoff.

### 3.8 MCP `inputSchema` sanitization
**Done (re-verified 2026-06-08).** `_SanitizingMCPServer` (`agent/mcp.py:18–40`) runs
`sanitize_mcp_schema()` (`tools/mcp_schema.py:9–86`) over every `inputSchema` on each
`list_tools()` call: collapses `["string","null"]` type arrays, collapses nullable
`anyOf`/`oneOf`, fixes bare object nodes missing `properties`, recurses children.
co-cli's sanitizer is narrower than hermes's `schema_sanitizer.py` (which also strips
top-level combinators for Codex and has reactive 400-triggered passes) — those are
xAI/Codex-specific and not needed for co's Ollama/Anthropic targets.

### 3.9 No `check_fn` result cache
**Holds; slightly more relevant now (re-verified 2026-06-08).** `_make_prepare(fn)`
(`toolset.py:92–98`) calls `info.check_fn(deps)` on every prepare (once per turn) with no
cache. The seven Google tools now register `check_fn=_google_available`, which resolves
credential state each turn — still cheap (in-memory `_creds_resolved`), but this is the
first batch of integration-gated `check_fn`s on the surface. Hermes caches `check_fn` for
30 s (`registry.py:113–134`). Worth copying if an external-probe `check_fn` is added.

---

## 4. Anti-Patterns (co-cli choices that cause subtle problems)

### 4.1 `tool_output_raw()` silently bypasses spill + telemetry
**Resolved (re-verified 2026-06-08).** `tool_output_raw` is **deleted** — grep across
`co_cli/` returns zero matches. Helper-layer errors route back through `tool_error`
(which spills via `ctx`). The current `tool_io.py` surface is `tool_output`, `tool_error`,
`spill_if_oversized`, `spill_with_span`, `SPILL_THRESHOLD_CHARS=4_000`,
`TOOL_RESULT_PREVIEW_CHARS=1_500`. Shipped via
`docs/exec-plans/completed/2026-05-27-172716-toolgap-b1-fetch-spill.md`.

### 4.2 `ModelRetry` vs `tool_error()` classification is convention-only
**Holds (re-verified 2026-06-08).** Transient → `raise ModelRetry`; terminal →
`return tool_error(...)`. `handle_google_api_error()` (`tool_io.py:315–352`) is the
reference pattern (401/RefreshError → `tool_error`; 403/404/429/5xx → `ModelRetry`); no
static check. Misclassification still burns retry budget on non-recoverable errors.

### 4.3 `file_read_mtimes` grows unbounded
**Fixed (re-verified 2026-06-08).** The unbounded `file_read_mtimes` dict is gone;
`CoDeps.file_tracker: FileReadTracker` (`deps.py:289`) replaces it, shared by reference
across `fork_deps()` (now daemon-only) and bounded by its own internal structure. The
old anti-pattern no longer applies.

### 4.4 Path normalization is a hidden arg rewrite
**Resolved (re-verified 2026-06-08).** With `lifecycle.py` deleted, there is no hidden
pre-exec `path` rewrite and no `PATH_NORMALIZATION_TOOLS` frozenset (gone from
`categories.py`, which now holds only `FILE_TOOLS`). Path resolution + workspace
containment is now **explicit inside the file tools** via
`fs_guards.enforce_read_boundary` / `enforce_write_boundary` (`files/write.py:287,346`).
Unit tests now see the same path-handling code production runs.

### 4.5 Delegation agents re-enumerate tool lists by hand
**Narrowed (re-verified 2026-06-08).** No longer a model-facing concern — the mid-turn
delegation triad is removed. The remaining hand-enumerated `tool_names=(…)` tuples are the
two daemon specs in `daemons/dream/_reviewer.py`, resolved against `TOOL_REGISTRY_BY_NAME`
at build time. Adding a tool to a daemon spec is still a tuple edit and `requires_config`
gating is per-spec, but the blast radius is two background specs, not three live tools.

---

## 5. Priority Ordering

Status legend: ✅ done · 🟢 in flight (active exec-plan) · 🟠 open · ⚪ deferred / out of scope.

| Priority | Status | Item | Risk | Effort |
|---|---|---|---|---|
| **High** | ✅ | MCP `inputSchema` sanitization (§3.8) | MCP tools silently dropped on Ollama / llama.cpp; Anthropic schema rejection | Done — `_SanitizingMCPServer` + `tools/mcp_schema.py` |
| **High** | ✅ | MCP tool results not spill-gated (§3.1) | Context overflow via runaway MCP | Done — moved to `_CallSeamToolset.call_tool` (`toolset.py:199–212`) after `lifecycle.py` deletion |
| **High** | ✅ | `tool_output_raw()` bypasses spill gate (§4.1) | Silent context overflow | Done — `tool_output_raw` deleted; errors route through `tool_error` |
| **Medium** | 🟢 | Local document handling (PDF/text extraction) (§2.1) | Cannot read local PDFs (`file_read` rejects binary) | TASK-4 of `2026-05-27-133104-skill-porting-mission-converged.md` — `documents` skill + `scripts/extract_pdf.py` (not yet shipped) |
| **Medium** | ✅ | Add model-callable `skill_view` / skill-write tools (§2.1) | Skill discovery + authoring required slash commands | Done — `skill_view` (ALWAYS) + `skill_create`/`edit`/`patch`/`delete` (DEFERRED) |
| **Medium** | ✅ | Concurrent MCP `list_tools()` discovery (§3.3) | Cumulative startup delay = N × timeout | Done — `_discover_one` + `asyncio.gather` (`mcp.py:180`) |
| **Medium** | 🟢 | `shell_exec` `pty` + interactive `task_write`/`task_close` (§1.5) | CLIs that gate on isatty; no stdin to running tasks | `2026-05-28-200025-toolgap-interactive-terminal.md` (active, not shipped — no `pty` arg, no `task_write`/`task_close` in source) |
| **Medium** | ⚪ | `web_fetch` `urls: list[str]` parallel fetch (§1.4) | Sequential latency | Rejected as parity-cosmetic — `is_concurrent_safe` already parallelizes per-call |
| **Medium** | 🟠 | `ModelRetry` / `tool_error` unenforced (§4.2) | Retry-budget waste | Medium — ruff rule or base-class signal |
| **Medium** | ✅ | `NATIVE_TOOLS` manual tuple (§3.2) | Tool silently omitted | Done — `TOOL_REGISTRY` + `TOOL_REGISTRY_BY_NAME` self-registration |
| **Medium** | ✅ | Capability-API SDK coupling (methodology) | pydantic-ai capability SDK lock-in | Done — dropped at v0.8.312; explicit `tool_view` + `_tool_visibility_filter` + `_CallSeamToolset` replace it |
| **Low** | 🟢 | Add image/screenshot vision tool (§2.1) | No vision capability | `2026-05-28-150239-vision-input.md` (active, not shipped — no `image_view`/`build_vision_model` in source) |
| **Low** | ⚪ | `role_filter` on `session_search` (§1.3) | Cannot narrow recall to assistant-vs-user | **WITHDRAWN — not a defect.** Intentional divergence: co's ripgrep hits make role a sub-span property. Do not re-propose from a signature diff alone. |
| **Low** | 🟠 | Named toolset profiles for daemon specs (§3.4, §4.5) | Tool-list drift in daemon specs | **Narrowed** — now only two daemon `TaskAgentSpec`s hand-enumerate tools; no model-facing surface. Tag-and-filter is a future option. |
| **Low** | ✅ | `file_read_mtimes` unbounded (§4.3) | Memory in long sessions | Done — replaced by `FileReadTracker` (`deps.py:289`) |
| **Low** | ✅ | Path-normalization hidden rewrite (§4.4) | Tests diverge from production | Done — `lifecycle.py` deleted; explicit `fs_guards` boundary checks in the tools |
| **Low** | ⚪ | No permanent approval persistence (§3.7) | UX friction | Deliberate security tradeoff |
| **Low** | ✅ | Background task ring-buffer lossy (§3.6) | Output loss for long commands | Done — file-only per-task log; `tail_log`; `_drain_and_cleanup` (`main.py:213–234`) |
| **Low** | ⚪ | MCP dynamic tool refresh (§3.5) | Stale tool index in long sessions | Deferred — no install-set server emits `list_changed`; `/mcp restart` fixes |
| **Low** | ⚪ | `check_fn` result cache (§3.9) | Wasted work *if* external-probe `check_fn`s are added | Low — copy hermes's 30 s TTL when the Google `check_fn`s start probing the network |
