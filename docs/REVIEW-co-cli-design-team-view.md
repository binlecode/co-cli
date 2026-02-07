# Team View Review: co-cli Design vs Current System

**Date:** 2026-02-07
**Scope:** Unified view from Claude Opus accuracy audit, Codex severity-ordered findings, and cross-review synthesis — all validated against current working tree.

**P0 and P1 are complete.** This document now tracks remaining work.

---

## 1. Scorecard

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| Runtime architecture quality | **A** | DeferredToolRequests, sandbox hardening, OTel tracing are above industry average. |
| Design doc fidelity to code | **A** | All identified drift resolved. Design docs match implementation. |
| Convention adherence | **A** | 16/16 tools conform. No private-API usage. All semantic styles registered (9 roles). |
| Competitive readiness | **B** | Strong foundations (sandbox, tracing, Google/Slack tools). Automatic context management and MCP client support are table-stakes gaps. Streaming planned in `TODO-streaming-tool-output.md`. |

---

## 2. What Is Solid

1. **Core agent loop:** `agent.run()` → `DeferredToolRequests` → `while isinstance(...)` approval loop → `result.all_messages()`. Clean separation of approval UX from tool logic.

2. **Tool design consistency:** All 16 tools use `RunContext[CoDeps]`. Zero `tool_plain()`. Data tools return `dict[str, Any]` with `display` field. `ModelRetry` for config/param errors.

3. **Sandbox hardening:** `cap_drop=ALL`, `no-new-privileges`, `pids_limit=256`, `user=1000:1000`, configurable `network_mode`, `mem_limit`, `nano_cpus`. Dual timeout layering. Subprocess fallback with allowlist-based environment sanitization when Docker is unavailable.

4. **Shell safe-command whitelist:** 29 default safe commands (read-only fs, git read ops, text utils) auto-approved when sandbox has isolation. Rejects shell chaining operators (`;`, `&`, `|`, `>`, `` ` ``, `$(`). Multi-word prefix matching with longest-first precedence.

5. **Telemetry differentiator:** OTel v3 → SQLite (WAL + busy timeout + retry) → `co tail` real-time viewer + static HTML trace viewer. No peer has this level of local observability.

6. **Signal handling:** Dual interrupt handling at inner/outer levels. SIGINT handler swap during `Prompt.ask()`. Dangling tool call patching.

7. **Google/Slack/Obsidian integrations:** Proper OAuth, pagination, error hint dicts. Unique among open-source agentic CLIs.

---

## 3. Remaining Work

### Product Competitiveness

1. **Automatic context governance** — ~~`message_history` grows unbounded; only mitigation is manual `/compact`.~~ **Done.** Two `history_processors` registered: tool-output trimming + sliding window with LLM summarisation. See `DESIGN-conversation-memory.md`.

2. **MCP client support** — Would allow co-cli to integrate with external tool servers without custom tool code. Table-stakes gap. Tracked in `TODO-mcp-client.md`.

3. **Session persistence** — Resume conversations across `co chat` invocations. See `DESIGN-conversation-memory.md` §7.

---

## 4. Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Review baseline | Future reviews should target working tree, not just HEAD | Both reviews missed significant uncommitted work |

---

## 5. Completed

**P0:** Doc realignment — Google auth, CoDeps, defaults, OTel v3, CLI flags, test refs, new features, CLAUDE.md, traceability links, TODO-approval-flow status.

**P1:** Google credential cache moved to CoDeps. `--theme` flag fixed via `set_theme()`/`push_theme()`. Private `_function_toolset` access replaced with `tool_names` from `get_agent()`. Safe-command checker already hardened (was stale in review). Status table extracted to `render_status_table()` with semantic styles; `warning` style added to theme.

**P2 (post-review):** Subprocess sandbox fallback (`SubprocessBackend`) with allowlist-based environment sanitization (`_sandbox_env.py`), auto-detection in `_create_sandbox()`. Shell safe-command whitelist (29 defaults) with chaining rejection and sandbox-gated auto-approval (`_approval.py`). Approval flow and sandbox hardening functional tests added.
