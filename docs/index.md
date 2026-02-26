---
title: Home
nav_order: 0
---

# Co CLI — Design Docs

Welcome to the Co CLI system design documentation.

Start with the [System Design](DESIGN-core.md) overview, then explore component docs in the sidebar.

## Layers

### Core (01–04)
- [System Design](DESIGN-core.md) — Agent factory, `CoDeps`, orchestration, streaming, approval, interrupts, cross-cutting concerns
- [02 — Personality System](DESIGN-02-personality.md) — File-driven roles, trait composition, per-turn injection, reasoning depth
- [LLM Models](DESIGN-llm-models.md) — Gemini/Ollama model selection + Ollama local setup guide
- [04 — Streaming Event Ordering](DESIGN-04-streaming-event-ordering.md) — Boundary-safe stream rendering, event semantics
- [16 — Agentic Loop & Prompting](DESIGN-16-prompt-design.md) — `run_turn` lifecycle, approval re-entry, safety mechanisms, prompt layering

### Infrastructure (05–08)
- [Logging & Tracking](DESIGN-logging-and-tracking.md) — SQLite span exporter, trace viewers, real-time `co tail`
- [07 — Context Governance](DESIGN-07-context-governance.md) — History processors, summarisation
- [08 — Theming & ASCII Art](DESIGN-08-theming-ascii.md) — Light/dark themes, semantic styles

### Tools
- [Tools](DESIGN-tools.md) — Memory, Shell, Obsidian, Google (Drive/Gmail/Calendar), Web (search + fetch)
