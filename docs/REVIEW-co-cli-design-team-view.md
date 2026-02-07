# Team View Review: co-cli Design vs Current System

**Date:** 2026-02-07
**Scope:** Unified view from Claude Opus accuracy audit, Codex severity-ordered findings, and cross-review synthesis — all validated against current working tree.

**P0 (doc realignment) is complete.** This document now focuses on remaining work: P1 code integrity fixes and P2 product competitiveness.

---

## 1. Scorecard

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| Runtime architecture quality | **A-** | DeferredToolRequests, sandbox hardening, OTel tracing are above industry average. Knock-downs: module-global cred cache, private API usage, theme binding bug. |
| Design doc fidelity to code | **A-** | After P0 doc realignment. All identified drift resolved (Google auth, CoDeps, defaults, OTel, flags, test refs, new features). |
| Convention adherence | **A-** | 17/17 tools conform. One module-global violation, three private-API callsites, a few hardcoded color styles. |
| Competitive readiness | **B** | Strong foundations (sandbox, tracing, Google/Slack tools). Streaming and automatic context management are table-stakes gaps. |

---

## 2. What Is Solid

1. **Core agent loop:** `agent.run()` → `DeferredToolRequests` → `while isinstance(...)` approval loop → `result.all_messages()`. Clean separation of approval UX from tool logic.

2. **Tool design consistency:** All 17 tools use `RunContext[CoDeps]`. Zero `tool_plain()`. Data tools return `dict[str, Any]` with `display` field. `ModelRetry` for config/param errors.

3. **Sandbox hardening:** `cap_drop=ALL`, `no-new-privileges`, `pids_limit=256`, `user=1000:1000`, configurable `network_mode`, `mem_limit`, `nano_cpus`. Dual timeout layering.

4. **Telemetry differentiator:** OTel v3 → SQLite (WAL + busy timeout + retry) → `co tail` real-time viewer + static HTML trace viewer. No peer has this level of local observability.

5. **Signal handling:** Dual interrupt handling at inner/outer levels. SIGINT handler swap during `Prompt.ask()`. Dangling tool call patching.

6. **Google/Slack/Obsidian integrations:** Proper OAuth, pagination, error hint dicts. Unique among open-source agentic CLIs.

---

## 3. Remaining Gaps

### 3.1 Design Invariant Violation — Google Credential Cache (Medium — Architecture)

`google_auth.py:111-112` uses module-level `_cached_creds` / `_cached_creds_loaded` globals. The design doc's stated invariant: "mutable per-session state belongs in CoDeps, never in module globals." Both reviewers independently flagged this.

### 3.2 Safe-Command Auto-Approval Bypass Surface (Medium — UX)

`_is_safe_command()` rejects `;`, `&&`, `||`, `|`, `` ` ``, `$(` but not:

| Not blocked |
|-------------|
| `&` (background), `>`, `>>`, `<`, `<<` (redirection), `\n` (newline), `$HOME`-style expansion |

Commands like `echo secret > /workspace/leak` or `cat file &` would be auto-approved. Not a sandbox escape (Docker isolation holds), but weakens the "read-like commands get silent approval" model.

### 3.3 Broken `--theme` Flag (Medium — Bug)

`display.py:18` constructs `console = Console(theme=...)` at import time. `main.py:322-323` sets `settings.theme = theme` after import. The console's theme is never reconstructed. The `--theme` CLI flag is effectively a no-op.

### 3.4 Private API Usage (Low — Fragility)

`agent._function_toolset.tools` accessed at three callsites (`main.py:229,266`, `_commands.py:86`). Underscore-prefixed private API of pydantic-ai. Upstream refactors will break these silently.

### 3.5 No Streaming Output (High — Product)

All responses render after `agent.run()` completes. Every peer streams tokens as they arrive. Tracked in `TODO-streaming-tool-output.md`.

### 3.6 No Automatic Context Governance (High — Product)

`message_history` grows unbounded. Only mitigation is manual `/compact`. Tracked in `TODO-conversation-memory.md`.

---

## 4. Priority Plan

### P1: Design Integrity Fixes (Code Changes)

1. **Move Google credential cache into CoDeps**
   - Add `google_creds: Any | None = None` and `_google_creds_resolved: bool = False` to CoDeps
   - Add a method or helper that resolves lazily on first call: `deps.get_google_creds()` → calls `ensure_google_credentials()` once, caches in `self.google_creds`
   - Remove module globals from `google_auth.py`
   - Update all Google tool files to use `ctx.deps.get_google_creds()` instead of `get_cached_google_creds(ctx.deps.google_credentials_path)`

2. **Fix `--theme` flag**
   - Add `set_theme(name: str)` to `display.py` that reconstructs `console.theme`
   - Call it in `chat()` before `asyncio.run(chat_loop())` when `--theme` is supplied

3. **Replace private toolset access**
   - Have `get_agent()` return the tool count (or tool name list) as a third element of its return tuple
   - Or maintain a module-level `TOOL_NAMES: list[str]` in `agent.py` derived from the registration calls
   - Update `main.py:229,266` and `_commands.py:86` to use the public value

4. **Harden safe-command checker**
   - Add `&`, `>`, `>>`, `<`, `<<`, `\n` to the rejection set in `_is_safe_command()`
   - Longer term: consider `shlex.split()` for token-based classification instead of substring checks
   - Document explicitly that this is UX-only, not a security boundary

5. **Extract status table rendering**
   - Create `render_status_table(info: StatusInfo) -> Table` in `status.py`
   - Use semantic styles (`[accent]`, `[info]`) instead of hardcoded `style="cyan"/"magenta"/"green"`
   - Call from both `main.py:status()` and `_commands.py:_cmd_status()`

### P2: Product Competitiveness (Larger Efforts)

1. **Streaming** — Migrate chat loop to `agent.run_stream()` + `event_stream_handler` per `TODO-streaming-tool-output.md`. Highest-impact UX improvement.

2. **Automatic context governance** — Register a `history_processor` with pydantic-ai. Even a simple message-count cap or token-budget policy prevents silent context overflow. `/compact` remains as manual override.

3. **MCP client support** — Not tracked in any TODO. Would allow co-cli to integrate with external tool servers without custom tool code. Becoming table-stakes.

4. **Session persistence** — Resume conversations across `co chat` invocations. Tracked in `TODO-conversation-memory.md`.

---

## 5. Decisions Reached

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Google credential cache | Move into `CoDeps` with lazy resolution method | Aligns with stated invariant; unblocks future multi-session |
| 2 | Theme handling | Add `set_theme()` in `display.py`, call before chat loop | Minimal change, fixes a real bug |
| 3 | Tool registry metadata | Return tool names from `get_agent()` or maintain explicit list in `agent.py` | Removes private API dependency |
| 4 | Safe-command checker | Add `&`, `>`, `>>`, `<`, `<<`, `\n` to rejection set; keep as UX-only | Reduces surprise without over-engineering; `shlex` rewrite deferred |
| 5 | Status table rendering | Extract to `status.py` helper with semantic styles | DRY + convention compliance in one change |
| 6 | Review baseline | Future reviews should target working tree, not just HEAD | Both reviews missed significant uncommitted work |

---

## 6. Completed Work (P0)

P0 documentation realignment is done. All design doc drift identified during the review has been fixed:

- Google auth architecture rewritten to lazy credential resolution in `DESIGN-co-cli.md` and `DESIGN-tool-google.md`
- CoDeps class diagram and dataclass updated with correct fields
- All stale defaults corrected (`docker_image`, `ollama_model`, default provider)
- OTel doc updated to v3 span naming; SQL examples fixed
- Tail viewer CLI flags corrected (`-i`, `-T`, `-m`)
- Test file references fixed in Slack and Google docs
- CLAUDE.md TODO list cleaned up; traceability links added
- New features documented: `!command`, `_display_tool_outputs()`, `UsageLimits`, `tool_retries`, safe-command auto-approval
- `TODO-approval-flow.md` updated with implementation status
