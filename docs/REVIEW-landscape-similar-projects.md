# REVIEW: Landscape Analysis — Similar Projects to co-cli

**Date**: 2026-02-11
**Context**: Survey of projects sharing co-cli's vision of a personal AI companion for knowledge work.

---

## co-cli's Vision Summary

co-cli aims to evolve from a tool-calling CLI into a **personal companion for knowledge work** (the "Finch" vision) — with persistent memory lifecycle, personality/soul, multi-service tool integration (Google, Slack, Obsidian, web), local-first architecture, and MCP extensibility.

---

## Tier 1: Closest to co-cli's "Growing Companion" Vision

### 1. Khoj

- **URL:** https://github.com/khoj-ai/khoj
- **Description:** Self-hostable "AI second brain." Indexes your documents (PDF, markdown, org-mode, Notion, etc.), provides search + chat, schedules automations, and does deep research. Accessible from browser, Obsidian, Emacs, desktop, phone, or WhatsApp.
- **Comparison to co-cli:**
  - **Similar:** Personal knowledge companion, Obsidian integration, custom agent personas, local-first option, learns from your documents over time. Closest in *spirit* to the Finch vision of a growing, adapting companion.
  - **Different:** Primarily web/app-based rather than CLI-native. Server architecture (Django/FastAPI) vs. co-cli's lightweight terminal-first approach. Much larger scope — enterprise-scale features. No pydantic-ai. No explicit "personality/soul" framing.
- **Memory/Personality/Tools:** Strong memory (document indexing + chat history), custom agent personas, tool integration (web search, automations), but memory is document-centric rather than behavioral/preference-centric like co-cli's memory lifecycle.

### 2. OpenClaw / Clawdbot

- **URL:** https://github.com/yksanjo/clawdbot-deepseek (DeepSeek variant)
- **Description:** Open-source, self-hosted personal AI assistant that runs locally, has persistent memory across sessions, customizable personality, and integrates with messaging platforms (Telegram, WhatsApp, Discord, Signal, Slack). Went viral in early 2026. "Your agent introduces itself, learns about you, and becomes genuinely useful over time."
- **Comparison to co-cli:**
  - **Similar:** This is the closest match to co-cli's Finch vision. Persistent memory stored as local Markdown files, customizable personality, learns user preferences, multi-platform integrations, local-first, open source, "growing companion" framing.
  - **Different:** Not CLI-native — primarily a background daemon + messaging platform integrations. TypeScript-based. Much more focused on messaging/chat platforms than terminal workflows. No pydantic-ai. Security concerns flagged by Cisco due to broad system access.
- **Memory/Personality/Tools:** Strong persistent memory (markdown files), explicit personality customization, proactive task execution, multi-platform tool integration.

### 3. Letta (formerly MemGPT)

- **URL:** https://github.com/letta-ai/letta
- **Description:** Platform for building stateful agents with advanced memory that can learn and self-improve over time. Born from the MemGPT research paper that introduced "LLM Operating System" for memory management. Also has Letta Code, a memory-first coding agent.
- **Comparison to co-cli:**
  - **Similar:** Memory lifecycle is the core differentiator (echoes co-cli's DESIGN-14 memory lifecycle). Self-editing memory, structured memory blocks, memory that persists across interactions. Letta Code brings this to terminal coding.
  - **Different:** Framework/platform for building agents, not an end-user CLI companion. Server-based architecture. More academic/research-oriented. No direct personality/soul framing. No multi-service tool integration (Google, Slack, etc.) built in.
- **Memory/Personality/Tools:** Best-in-class memory architecture (the research origin of structured agent memory). Self-editing memory blocks. No built-in personality. Tools are extensible but not pre-built for specific services.

### 4. Aetherius AI Assistant

- **URL:** https://github.com/libraryofcelsus/Aetherius_AI_Assistant
- **Description:** Completely private, locally-operated AI Assistant/Chatbot with realistic long-term memory and thought formation using open-source LLMs. Uses Qdrant for vector DB.
- **Comparison to co-cli:**
  - **Similar:** Local-first, persistent long-term memory, companion framing, open-source LLM focus, thought formation (similar to co-cli's reasoning approach).
  - **Different:** Desktop chatbot UI rather than CLI. Vector DB dependency (Qdrant) vs. co-cli's grep + frontmatter MVP approach. Smaller community. No multi-service tool integration.
- **Memory/Personality/Tools:** Realistic long-term memory with thought formation. Private and local. Limited tool integration compared to co-cli.

---

## Tier 2: Memory Infrastructure (Complement co-cli rather than compete)

### 5. Mem0

- **URL:** https://github.com/mem0ai/mem0
- **Description:** "Universal memory layer for AI agents." Adds intelligent persistent memory to any AI application with a single line of code. Memory scoping at user/agent/session/app levels. Raised $24M Series A.
- **Comparison to co-cli:**
  - **Similar:** Persistent memory across sessions, learns user preferences, adapts over time. Memory scoping concepts align with co-cli's user/project/session memory hierarchy.
  - **Different:** Library/service, not a CLI companion. Designed to be embedded into other tools. Co-cli builds its own memory lifecycle (DESIGN-14) rather than depending on an external memory layer.
- **Memory/Personality/Tools:** Pure memory infrastructure. No personality. No tools. 90% lower token usage and 91% faster responses vs. full-context approaches.

### 6. OpenMemory

- **URL:** https://github.com/CaviraOSS/OpenMemory
- **Description:** Local persistent memory store for LLM applications including Claude Desktop, GitHub Copilot, Codex, etc. Ships as an MCP server, so any MCP-aware client can use it as a tool.
- **Comparison to co-cli:**
  - **Similar:** Local-first, MCP-native, persistent memory across AI tools. The MCP server approach aligns with co-cli's planned MCP support.
  - **Different:** Memory infrastructure only, not a companion. No personality, no tool integration, no CLI interface. Could potentially be used *by* co-cli rather than competing with it.
- **Memory/Personality/Tools:** Memory only. MCP-native. Local SQLite storage.

### 7. AI CLI Memory System

- **URL:** https://github.com/heyfinal/ai-cli-memory-system
- **Description:** Unified contextual memory layer for AI-powered CLI tools (Claude Code, Copilot CLI, Gemini, Aider). Tracks every session, learns patterns, automatically provides relevant context when you return.
- **Comparison to co-cli:**
  - **Similar:** CLI-focused persistent memory, pattern learning, automatic context provision. Very close to co-cli's memory concept but as standalone infrastructure.
  - **Different:** Middleware/glue layer, not a companion. No personality, no tools, no agent loop.
- **Memory/Personality/Tools:** Memory only, CLI-focused.

---

## Tier 3: Major CLI Coding Agents (Code-first, not companion-first)

### 8. Claude Code (Anthropic)

- **URL:** https://github.com/anthropics/claude-code
- **Description:** Agentic coding tool that lives in your terminal, understands your codebase, and helps code faster. TypeScript-based. Now has memory (auto-records and recalls memories), skills system, MCP server support, PDF tools, and more.
- **Comparison to co-cli:**
  - **Similar:** CLI-native, memory system (user/project/local scopes with frontmatter), tool integration, MCP support, personality via CLAUDE.md instructions. co-cli's architecture is directly inspired by Claude Code patterns.
  - **Different:** Primarily a coding agent, not a knowledge work companion. Proprietary (Anthropic model only). No Obsidian/Google/Slack integration. Memory is coding-context focused, not behavioral/preference lifecycle. No "growing companion" framing. Much larger team and resources.
- **Memory/Personality/Tools:** Memory with scopes (added 2026), skills system, MCP servers, but no personality/soul concept, no multi-service tool integration beyond coding.

### 9. Codex CLI (OpenAI)

- **URL:** https://github.com/openai/codex
- **Description:** Open-source coding agent in Rust. Features code review (`/review`), web search, multi-agent support via MCP + OpenAI Agents SDK, sandboxed execution. Uses GPT-5.3-Codex models.
- **Comparison to co-cli:**
  - **Similar:** CLI-native, MCP support, tool calling, multi-model potential.
  - **Different:** Pure coding focus. Rust-based. No persistent memory system. No personality. No multi-service integration (Google/Slack/Obsidian). No companion framing. OpenAI model ecosystem only.
- **Memory/Personality/Tools:** No persistent memory. No personality. Web search tool only.

### 10. Gemini CLI (Google)

- **URL:** https://github.com/google-gemini/gemini-cli
- **Description:** Open-source AI agent with Gemini 3 models, 1M token context, Google Search grounding, MCP support, extensions ecosystem (Figma, Shopify, Stripe, etc.), hooks system.
- **Comparison to co-cli:**
  - **Similar:** CLI-native, tool integration, MCP support, extensions/hooks for customization, Google ecosystem integration.
  - **Different:** Code-focused agent, not a companion. TypeScript-based. No persistent memory system. No personality. Google model ecosystem only. Extensions are third-party, not built-in like co-cli's tools.
- **Memory/Personality/Tools:** No persistent memory. No personality. Rich extension ecosystem but no built-in multi-service tools.

### 11. Goose (Block)

- **URL:** https://github.com/block/goose
- **Description:** Open-source, extensible AI agent for automating development tasks. MCP-native (3,000+ MCP servers). Desktop app + CLI. Contributed to Linux Foundation's Agentic AI Foundation alongside MCP and AGENTS.md.
- **Comparison to co-cli:**
  - **Similar:** MCP-first extensibility, multi-model support, tool calling, open source. Goose's "on-machine" philosophy echoes co-cli's local-first approach.
  - **Different:** Broader scope (full project automation vs. personal companion). Rust/Go-based. No persistent memory. No personality. No built-in memory lifecycle. Desktop app focus alongside CLI.
- **Memory/Personality/Tools:** No persistent memory or personality. MCP-based tool extensibility is its strength.

### 12. OpenCode

- **URL:** https://github.com/opencode-ai/opencode
- **Description:** Go-based CLI with rich TUI (Bubble Tea), supports 75+ providers, MCP integration, LSP support, session management. GitHub Copilot partnership (Jan 2026).
- **Comparison to co-cli:**
  - **Similar:** Multi-provider support, MCP integration, session management, CLI-native.
  - **Different:** Go-based. Code-focused. No persistent memory beyond sessions. No personality. No multi-service tool integration. No companion framing.
- **Memory/Personality/Tools:** Session management only. No persistent memory or personality.

### 13. Aider

- **URL:** https://github.com/Aider-AI/aider
- **Description:** AI pair programming in your terminal. Deep Git integration, codebase mapping, 100+ language support, auto-commits with descriptive messages, lint/test integration.
- **Comparison to co-cli:**
  - **Similar:** Python-based, CLI-native, multi-model support.
  - **Different:** Purely code-focused. No persistent memory. No personality. No tool integration beyond Git/code. Simplest approval model (`io.confirm_ask()`). No MCP support. No companion framing.
- **Memory/Personality/Tools:** Codebase map provides contextual understanding but no persistent memory, personality, or multi-service tools.

### 14. Toad (Will McGugan)

- **URL:** https://github.com/batrachianai/toad
- **Description:** Universal TUI frontend for AI coding tools (OpenHands, Claude Code, Gemini CLI). Built on Textual. Blends traditional shell with agentic AI, Markdown editor, fuzzy file search, full mouse support.
- **Comparison to co-cli:**
  - **Similar:** Terminal-native, rich TUI, shell integration, Python ecosystem.
  - **Different:** Frontend/UI layer only — wraps other agents rather than being one. No memory, no personality, no tool integration, no companion concept. Uses Agent Client Protocol (ACP) rather than being an agent itself.
- **Memory/Personality/Tools:** None — it is a UI shell for other agents.

---

## Tier 4: CLI Utilities (Tool-focused, not companion-focused)

### 15. Fabric (Daniel Miessler)

- **URL:** https://github.com/danielmiessler/Fabric
- **Description:** Open-source framework for augmenting humans using AI with 200+ crowdsourced prompt patterns. CLI + REST API + web app. Daniel Miessler also created the Personal AI Infrastructure (PAI) concept.
- **Comparison to co-cli:**
  - **Similar:** "Augmenting humans" philosophy, multi-model support, prompt/pattern system (analogous to co-cli's prompt techniques). Miessler's PAI vision of personal AI infrastructure resonates with co-cli's companion vision.
  - **Different:** Pattern-execution focused (pipe in content, get structured output), not interactive companion. Go-based. No persistent memory. No tool calling. No personality. Patterns are stateless transforms, not agentic workflows.
- **Memory/Personality/Tools:** No memory or personality. Patterns are the core abstraction, not tools or agents. PAI concept is visionary but separate from Fabric itself.

### 16. Simon Willison's LLM

- **URL:** https://github.com/simonw/llm
- **Description:** CLI utility and Python library for interacting with LLMs. Plugin architecture for adding models and tools. Supports tool calling (v0.26+), multimodal inputs, schemas for structured extraction.
- **Comparison to co-cli:**
  - **Similar:** Python CLI, plugin extensibility, multi-model, tool calling, local model support via plugins. Conversation logging to SQLite (similar to co-cli's OTEL/SQLite approach).
  - **Different:** Unix-philosophy utility (one thing well), not a companion. No persistent memory across conversations. No personality. No multi-service integration. No agent loop — single prompt/response or simple conversations.
- **Memory/Personality/Tools:** Conversation logging to SQLite but no persistent memory retrieval. Tool calling via plugins. No personality.

### 17. Shell-GPT (SGPT)

- **URL:** https://github.com/TheR1D/shell_gpt
- **Description:** Command-line productivity tool translating natural language to shell commands. Chat sessions with named contexts. 11.7K GitHub stars.
- **Comparison to co-cli:**
  - **Similar:** Python CLI, shell integration, chat sessions.
  - **Different:** Shell command generation focused. No persistent memory beyond named sessions. No personality. No tool integration. No agent loop. No companion framing.
- **Memory/Personality/Tools:** Named chat sessions only. No persistent memory, personality, or tool integration.

### 18. AIChat

- **URL:** https://github.com/sigoden/aichat
- **Description:** All-in-one LLM CLI: Shell Assistant, Chat-REPL, RAG, AI Tools & Agents. Supports 20+ providers. Built-in HTTP server with API. Function calling.
- **Comparison to co-cli:**
  - **Similar:** Multi-provider, REPL mode, RAG capability, function calling, shell assistant.
  - **Different:** Rust-based. Utility-focused, not companion. No persistent memory lifecycle. No personality. RAG is document-based, not behavioral memory. No multi-service integration.
- **Memory/Personality/Tools:** RAG for document context. Function calling. No persistent memory or personality.

### 19. Open Interpreter

- **URL:** https://github.com/openinterpreter/open-interpreter
- **Description:** Natural language interface for computers. Runs code locally (Python, JS, Shell), controls GUI, vision capabilities. 50K+ GitHub stars.
- **Comparison to co-cli:**
  - **Similar:** Python-based, CLI-native, local code execution, multi-language.
  - **Different:** Code execution focused (no persistent memory, no personality, no multi-service tools). Moving toward GUI/app (`01` App) rather than staying CLI-native. No MCP support. No companion framing.
- **Memory/Personality/Tools:** No persistent memory. No personality. Code execution is the primary tool.

---

## Landscape Map

```
            Companion / Personal                        Code / Task
                   |                                         |
                   |  Khoj                                   |
                   |  OpenClaw/Clawdbot         Claude Code  |
          co-cli --+                            Codex CLI    |
          (Finch)  |  Aetherius                 Gemini CLI   |
                   |  Letta/MemGPT              Goose        |
                   |                            OpenCode     |
                   |                            Aider        |
                   |                                         |
            Memory-Rich                              Stateless
                   |                                         |
                   |  Mem0 (layer)              Fabric       |
                   |  OpenMemory (MCP)          Shell-GPT    |
                   |  AI CLI Memory System      AIChat       |
                   |                            LLM (Simon)  |
                   |                            Toad (UI)    |
```

---

## Comparison Summary Table

| Project | Memory | Personality | Multi-service Tools | MCP | CLI-native | Local-first |
|---------|--------|-------------|--------------------|----|------------|-------------|
| **co-cli** | Lifecycle (decay, dedup, consolidation) | Soul/personality templates | Google, Slack, Obsidian, web | Planned (Phase 2a) | Yes | Yes |
| Khoj | Document indexing | Custom personas | Web, automations | No | No (web/app) | Optional |
| OpenClaw | Markdown files | Customizable | Messaging platforms | No | No (daemon) | Yes |
| Letta | Self-editing blocks | No | Extensible | No | No (platform) | No (server) |
| Claude Code | Scoped (2026) | No (CLAUDE.md) | Coding only | Yes | Yes | Yes |
| Codex CLI | No | No | No | Yes | Yes | Yes |
| Gemini CLI | No | No | Extensions | Yes | Yes | No (cloud) |
| Goose | No | No | MCP-native (3000+) | Yes | Yes | Yes |
| Aider | Codebase map | No | No | No | Yes | Yes |
| Mem0 | Core product | No | No | No | No (library) | Optional |

---

## Key Differentiators for co-cli

1. **Memory Lifecycle Management** (DESIGN-14): No other CLI tool has a comparable memory lifecycle with proactive signal detection (preferences, corrections, decisions), dedup, consolidation, decay, and protection. Letta/MemGPT comes closest in memory sophistication but is a platform, not a companion.

2. **Personality/Soul Framing**: Only OpenClaw/Clawdbot shares the "growing companion" vision. Most tools are task executors. co-cli's personality concept is rare in the CLI space.

3. **Multi-Service Tool Integration in a CLI**: The combination of Google (Drive, Gmail, Calendar), Slack, Obsidian, web search, and shell tools in a single CLI agent is unique. Other tools either specialize (coding) or delegate to MCP servers.

4. **pydantic-ai Native**: co-cli is one of the few production CLI tools built on pydantic-ai, which gives it clean typing, structured tool patterns, and idiomatic Python agent patterns.

5. **Context Governance**: The sliding window + summarisation approach (DESIGN-07) with explicit budget management (3KB global, 7KB project) is unusually disciplined compared to peers.

6. **The Gap**: No existing tool combines CLI-native + persistent memory lifecycle + personality + multi-service tools + local-first + pydantic-ai in one package. That gap is co-cli's opportunity.

---

## Potential Integration Points

- **Mem0** or **OpenMemory MCP**: Could complement co-cli's memory system or serve as a future backend for cross-tool memory sharing.
- **Toad**: If co-cli ever wants a richer TUI frontend, Toad's Textual-based approach could be a reference.
- **Goose's MCP ecosystem**: When co-cli adds MCP client support (Phase 2a), it gains access to 3,000+ MCP servers.
- **Fabric Patterns**: co-cli could potentially consume Fabric patterns as prompt templates.
- **Letta's memory research**: The MemGPT paper's memory architecture concepts could inform co-cli's Phase 2+ memory evolution.

---

## Sources

- [Khoj AI](https://github.com/khoj-ai/khoj)
- [OpenClaw / Clawdbot](https://github.com/yksanjo/clawdbot-deepseek)
- [Letta (MemGPT)](https://github.com/letta-ai/letta)
- [Aetherius AI Assistant](https://github.com/libraryofcelsus/Aetherius_AI_Assistant)
- [Mem0](https://github.com/mem0ai/mem0)
- [OpenMemory](https://github.com/CaviraOSS/OpenMemory)
- [AI CLI Memory System](https://github.com/heyfinal/ai-cli-memory-system)
- [Claude Code](https://github.com/anthropics/claude-code)
- [Codex CLI](https://github.com/openai/codex)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli)
- [Goose](https://github.com/block/goose)
- [OpenCode](https://github.com/opencode-ai/opencode)
- [Aider](https://github.com/Aider-AI/aider)
- [Toad](https://github.com/batrachianai/toad)
- [Fabric](https://github.com/danielmiessler/Fabric)
- [LLM CLI](https://github.com/simonw/llm)
- [Shell-GPT](https://github.com/TheR1D/shell_gpt)
- [AIChat](https://github.com/sigoden/aichat)
- [Open Interpreter](https://github.com/openinterpreter/open-interpreter)
