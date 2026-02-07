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
| Convention adherence | **A** | 17/17 tools conform. No private-API usage. All semantic styles registered (10 roles). |
| Competitive readiness | **B** | Strong foundations (sandbox, tracing, Google/Slack tools). Automatic context management and MCP client support are table-stakes gaps. Streaming planned in `TODO-streaming-tool-output.md`. |

---

## 2. What Is Solid

1. **Core agent loop:** `agent.run()` → `DeferredToolRequests` → `while isinstance(...)` approval loop → `result.all_messages()`. Clean separation of approval UX from tool logic.

2. **Tool design consistency:** All 17 tools use `RunContext[CoDeps]`. Zero `tool_plain()`. Data tools return `dict[str, Any]` with `display` field. `ModelRetry` for config/param errors.

3. **Sandbox hardening:** `cap_drop=ALL`, `no-new-privileges`, `pids_limit=256`, `user=1000:1000`, configurable `network_mode`, `mem_limit`, `nano_cpus`. Dual timeout layering.

4. **Telemetry differentiator:** OTel v3 → SQLite (WAL + busy timeout + retry) → `co tail` real-time viewer + static HTML trace viewer. No peer has this level of local observability.

5. **Signal handling:** Dual interrupt handling at inner/outer levels. SIGINT handler swap during `Prompt.ask()`. Dangling tool call patching.

6. **Google/Slack/Obsidian integrations:** Proper OAuth, pagination, error hint dicts. Unique among open-source agentic CLIs.

---

## 3. Remaining Work

### Product Competitiveness

1. **Automatic context governance** — `message_history` grows unbounded; only mitigation is manual `/compact`. Register a `history_processor` with pydantic-ai — even a simple message-count cap prevents silent context overflow. Tracked in `TODO-conversation-memory.md`.

2. **MCP client support** — Not tracked in any TODO. Would allow co-cli to integrate with external tool servers without custom tool code. Becoming table-stakes.

3. **Session persistence** — Resume conversations across `co chat` invocations. Tracked in `TODO-conversation-memory.md`.

---

## 4. Decisions

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Review baseline | Future reviews should target working tree, not just HEAD | Both reviews missed significant uncommitted work |

---

## 5. Completed

**P0:** Doc realignment — Google auth, CoDeps, defaults, OTel v3, CLI flags, test refs, new features, CLAUDE.md, traceability links, TODO-approval-flow status.

**P1:** Google credential cache moved to CoDeps. `--theme` flag fixed via `set_theme()`/`push_theme()`. Private `_function_toolset` access replaced with `tool_names` from `get_agent()`. Safe-command checker already hardened (was stale in review). Status table extracted to `render_status_table()` with semantic styles; `warning` style added to theme.
