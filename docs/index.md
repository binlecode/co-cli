---
title: Home
nav_order: 0
---

# Co CLI — Design Docs

Welcome to the Co CLI system design documentation.

Start with the [System Design](DESIGN-00-co-cli.md) overview, then explore component docs in the sidebar.

## Layers

### Core (01–04)
- [01 — Agent & Dependencies](DESIGN-01-agent.md) — `get_agent()` factory, `CoDeps`, tool registration
- [02 — Chat Loop](DESIGN-02-chat-loop.md) — Streaming, approval, slash commands, interrupts
- [03 — LLM Models](DESIGN-03-llm-models.md) — Gemini/Ollama model selection
- [04 — Streaming Event Ordering](DESIGN-04-streaming-event-ordering.md) — Boundary-safe stream rendering, event semantics

### Infrastructure (05–08)
- [05 — OpenTelemetry Logging](DESIGN-05-otel-logging.md) — SQLite span exporter, trace viewers
- [06 — Tail Viewer](DESIGN-06-tail-viewer.md) — Real-time span viewer (`co tail`)
- [07 — Conversation Memory](DESIGN-07-conversation-memory.md) — History processors, summarisation
- [08 — Theming & ASCII Art](DESIGN-08-theming-ascii.md) — Light/dark themes, semantic styles

### Tools (09–13)
- [09 — Shell Tool & Sandbox](DESIGN-09-tool-shell.md) — Docker sandbox, subprocess fallback
- [10 — Obsidian Vault Tools](DESIGN-10-tool-obsidian.md) — Vault search, path traversal protection
- [11 — Google Tools](DESIGN-11-tool-google.md) — Drive, Gmail, Calendar
- [12 — Slack Tool](DESIGN-12-tool-slack.md) — Channel/message/user tools
- [13 — Web Tools](DESIGN-13-tool-web-search.md) — Brave Search + URL fetch
