# RESEARCH — `/status` Command: Peer Survey (hermes, openclaw, opencode)

**Date:** 2026-06-14
**Status:** Reference / design research — not normative
**Scope:** How three peer agent CLIs implement a dedicated `/status` report, to inform whether and how co-cli should add one. Grounded in source read on 2026-06-14.

## 0. Motivating question

co-cli surfaces status in three places, none of which is a dedicated `/status` report:

- **Startup banner** (`co_cli/bootstrap/banner.py:58-120`) — version, model, memory line, dream line, tool/skill/MCP/command counts, ready/degraded. One-time, at launch.
- **Footer toolbar** (`co_cli/display/core.py:409`, `StatusSnapshot` at `:244`) — live: mode, context %, background-task count, approvals, input-queue depth. Continuous but compact.
- **Per-domain commands** — `/dream`, `/memory`, `/usage`, `/tools`, `/skills`, `/tasks`, `/background`, `/approvals`, `/sessions`, `/history` each own a slice.

There is no single consolidated "where am I right now" report. This survey checks what the peers do.

## 1. Headline finding

**None of the three peers put history into `/status`.** Every `/status` is an *instantaneous current-state snapshot*; all three delegate history to separate surfaces (`/sessions`, `/history`, `/usage`). What varies enormously is the **richness of the current snapshot** and its **presentation**.

| | hermes | openclaw | opencode |
|---|---|---|---|
| Has `/status`? | Yes | Yes | Yes |
| Richness | Minimal | **Richest** | Narrow (integration health) |
| History in it? | No | No | No |
| Format | Plain-text k/v | Plain-text, emoji-sectioned | TUI modal dialog, color-coded |
| Data source | Ad-hoc (attrs + DB) | Ad-hoc, fan-out over ~10 loaders | Central reactive sync store |
| Channel-portable? | Yes | Yes (designed for TUI + chat) | No (TUI-only modal) |

Three genuinely distinct design points, below.

## 2. hermes — minimal session-metadata peek

- **Registration:** `hermes_cli/commands.py:112` — `CommandDef("status", "Show session info", "Session")`.
- **Handler:** `cli.py:5392-5443` (`_show_session_status`), dispatched at `cli.py:7250`.
- **Fields:** session id, path, title (optional), model (provider), created, last-activity, total tokens, agent-running (Yes/No). That's all.
- **Format:** plain text, no color/markup (`_console_print(..., markup=False)`), one key-value per line. The least-formatted command in its suite.
- **Sourcing:** ad-hoc at call time — CLI instance attrs (`session_id`, `model`, `provider`), agent (`session_total_tokens`), and a SQLite session-record lookup (`title`, `started_at`, `updated_at`). No cached snapshot object.
- **History:** none. Richer current metrics live in `/usage` (input/output/cache tokens, API calls, context %, cost, duration); history in `/history` (messages) and `/sessions` (browse/resume with last-active).

Takeaway: hermes deliberately keeps `/status` tiny — a "which session/model am I on" peek — and pushes everything else to focused commands. Closest in spirit to co's current per-domain split.

## 3. openclaw — full live runtime dashboard

- **Registration:** `src/auto-reply/commands-registry.shared.ts:219-226`; TUI handler `src/tui/tui-command-handlers.ts:387-405`.
- **Builders:** `src/status/status-text.ts:222-496` (`buildStatusText`, orchestrator) → `src/status/status-message.ts:549-1047` (`buildStatusMessage`, formatter).
- **Fields (sectioned, in order):** version+commit; gateway uptime + system uptime; model + auth mode + channel override + fallback state/reason; tokens in/out + estimated cost + cache hit-rate (cached/new); context tokens / window (%); compaction count; media-understanding outcomes per capability; provider usage quota windows (e.g. "5h 91% left · Week 70% left"); session key + last activity; background tasks (active/total/focus); controlled subagent runs + pending descendants; execution sandbox mode; runtime harness label; options (think level, fast mode, verbosity, trace, reasoning, elevated); group activation; queue mode/depth/policy; voice/TTS config.
- **Format:** plain text, emoji-prefixed sections (🦞 🧮 🗄️ 📚 🧵 📌 ⏱️ …), `·`-joined sub-details. No boxes — chosen so the same output renders in a TUI *and* in chat channels (Discord/Slack).
- **Sourcing:** ad-hoc fan-out. `buildStatusText` lazily loads ~10 runtime loaders (status-message, harness selection, queue, subagents), reads the session config entry, task registry, subagent registry, system uptime, and a transcript-usage fallback. One async probe (provider quota) is budgeted at 3.5s; model resolution is sync-only (`allowAsyncLoad=false`) to avoid I/O on render.
- **History:** none — explicitly a "is the system healthy *now*" view. No timeline, no trend, no past turns.

Takeaway: **this is the model that matches the request** — a single consolidated current-state report assembled on demand from existing subsystems. Notable discipline: render does no blocking I/O except one time-boxed probe.

## 4. opencode — integration-health diagnostic panel

- **Registration:** `packages/tui/src/app.tsx:750-757` — command `opencode.status`, slash `status`, category System; opens `<DialogStatus/>`.
- **View:** `packages/tui/src/component/dialog-status.tsx:10-168` — a dedicated TUI **modal dialog** (header "Status" + `esc` to dismiss).
- **Fields — only four sections, all about external integrations:** MCP servers (count + per-server connected/failed/disabled/needs-auth + error msgs), LSP servers (id, root, connected/error), formatters (enabled list), plugins (name + version). Color-coded bullets.
- **Sourcing:** **central reactive sync store** — reads `sync.data.mcp / .lsp / .formatter / .config.plugin` directly. No ad-hoc assembly; data is pre-synchronized.
- **History:** none. No session metrics, tokens, cost, or uptime at all — those are out of scope for this command. A separate `GET /session/status` returns only `{idle|busy}`.
- There is also a **CLI** `status` (`packages/cli/src/commands/handlers/service/status.ts`) reporting daemon running/stopped + URL — unrelated to session state.

Takeaway: opencode treats `/status` as "are my tool integrations healthy", backed by a central store and shown as a modal. A different axis entirely from session/usage state.

## 5. Synthesis for co-cli

The thing the user wants — "detailed current status **and** history" — is, in every peer, **two surfaces**, not one:

- **Current snapshot** → `/status`. Richness is a choice: hermes-minimal, openclaw-rich, opencode-integration-only.
- **History** → `/sessions`, `/history`, `/usage`. No peer folds history into `/status`.

co already has the *history* half (`/sessions`, `/history`, `/usage`) and is not behind there. co's actual gap is the **consolidated current snapshot** — today it is fragmented across banner + footer + ~6 per-domain commands, with no single view.

**openclaw is the template to follow:**
- One `/status` that fans out over existing subsystems into emoji/section plain text (works in the TUI; survives copy-paste; no new central store needed).
- Assembled on demand — co would extend the existing ad-hoc `_build_status_snapshot` path (`co_cli/main.py`) rather than introduce a reactive store (opencode's model is heavier than co needs).
- Render discipline: no blocking I/O. Dream daemon state via the already-cheap `status_daemon(USER_DIR)` (filesystem read); cumulative usage from whatever `/usage` already reads.

**Candidate co `/status` field set** (current snapshot only; history stays in `/sessions` `/history` `/usage`):
- session id + working dir + git branch; personality/soul; model (provider/model)
- context %; cumulative session tokens + cost (from `/usage` source)
- dream daemon: enabled / running / queue depth + last housekeeping (from `status_daemon` + `_dream_state.json`)
- background tasks; pending approvals; input-queue depth
- memory backend + item/session counts; loaded tools / skills / MCP / commands counts (banner already computes these)
- degraded flags

This is a superset of the banner's one-time view, made queryable mid-session, in one place — i.e. it closes the gap the footer and banner can't (deep, on-demand, copy-pasteable) without inventing a history mechanism no peer has.

## 6. References

### Peer source
- hermes: `hermes_cli/commands.py:112`, `cli.py:5392-5443`, `cli.py:7250`
- openclaw: `src/status/status-text.ts:222-496`, `src/status/status-message.ts:549-1047`, `src/auto-reply/commands-registry.shared.ts:219-226`, `src/tui/tui-command-handlers.ts:387-405`
- opencode: `packages/tui/src/app.tsx:750-757`, `packages/tui/src/component/dialog-status.tsx:10-168`, `packages/cli/src/commands/handlers/service/status.ts`

### co-cli current surfaces
- `co_cli/bootstrap/banner.py:58-120` (banner), `co_cli/display/core.py:409` + `:244` (footer + `StatusSnapshot`)
- `co_cli/main.py` `_build_status_snapshot` (snapshot assembly), `co_cli/commands/core.py:45-96` (slash registry)
- `co_cli/daemons/dream/process.py` `status_daemon`; `co_cli/commands/dream.py` (`/dream`, `co dream status`)
- See also `docs/reference/RESEARCH-self-improvement-learning-loop.md` for dream daemon detail.
