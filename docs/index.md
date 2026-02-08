---
title: Home
nav_order: 0
---

# Co CLI — Design Docs

Welcome to the Co CLI system design documentation.

Start with the [System Design](DESIGN-00-co-cli.md) overview, then explore component docs in the sidebar.

## Layers

### Core (01–03)
- [01 — Agent & Dependencies](DESIGN-01-agent.md) — `get_agent()` factory, `CoDeps`, tool registration
- [02 — Chat Loop](DESIGN-02-chat-loop.md) — Streaming, approval, slash commands, interrupts
- [03 — LLM Models](DESIGN-03-llm-models.md) — Gemini/Ollama model selection
- [13 — Streaming Event Ordering](DESIGN-13-streaming-event-ordering.md) — First-principles RCA and boundary-safe stream rendering

### Infrastructure (04–07)
- [04 — OpenTelemetry Logging](DESIGN-04-otel-logging.md) — SQLite span exporter, trace viewers
- [05 — Tail Viewer](DESIGN-05-tail-viewer.md) — Real-time span viewer (`co tail`)
- [06 — Conversation Memory](DESIGN-06-conversation-memory.md) — History processors, summarisation
- [07 — Theming & ASCII Art](DESIGN-07-theming-ascii.md) — Light/dark themes, semantic styles

### Tools (08–12)
- [08 — Shell Tool & Sandbox](DESIGN-08-tool-shell.md) — Docker sandbox, subprocess fallback
- [09 — Obsidian Vault Tools](DESIGN-09-tool-obsidian.md) — Vault search, path traversal protection
- [10 — Google Tools](DESIGN-10-tool-google.md) — Drive, Gmail, Calendar
- [11 — Slack Tool](DESIGN-11-tool-slack.md) — Channel/message/user tools
- [12 — Web Tools](DESIGN-12-tool-web-search.md) — Brave Search + URL fetch
