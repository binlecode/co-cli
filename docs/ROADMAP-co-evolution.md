# ROADMAP: Co Evolution

This is the strategic roadmap for co-cli: from capable tool-calling assistant to personal companion for knowledge work. Part I defines what co is and where it's going. Part II charts the evolution path, grounded in what the best peer systems converge on. Part III provides reference material.

---

# Part I: Mission, Vision & Core Functionality

## 1. Mission

Co is a personal companion for knowledge work, running in the user's terminal.

It connects the tools a knowledge worker already uses — email, calendar, documents, notes, web — into a single conversational interface backed by LLM reasoning. It remembers what matters, develops personality, and forms a lasting working relationship with its user.

Co is not a code editor. It is not an IDE plugin. It is a general-purpose CLI companion that happens to be good at technical tasks because it has a shell, a memory, and access to the user's information surfaces.

## 2. Vision: The Finch North Star

Co aspires to be the CLI version of the companion from "Finch" (2021): a helpful assistant that learns, develops personality, and forms lasting relationships with its user.

**Core traits:**
- **Helpful** — completes tasks efficiently and accurately
- **Curious** — asks clarifying questions, seeks to understand context
- **Adaptive** — learns user preferences and patterns over time
- **Empathetic** — understands emotional context, adjusts tone appropriately
- **Loyal** — remembers past interactions, maintains continuity across sessions
- **Growing** — evolves from command executor to thoughtful partner

**Five pillars of co's character:**

| Pillar | Description | Status |
|--------|-------------|--------|
| **Soul** | Identity, personality, interaction style (user-selectable from presets) | Shipped — 5 presets, soul seed always-on |
| **Internal Knowledge** | Learned context, patterns, user habits (persists across sessions) | Shipped — memory lifecycle with save/recall/list |
| **External Knowledge** | Tools for accessing data (Google, Obsidian, web, MCP servers) | Shipped — 16 tools + 3 MCP servers |
| **Emotion** | Tone, empathy, context-aware communication | Partial — personality modulates tone; no emotion engine |
| **Habit** | Workflow preferences, approval patterns, personalization | Partial — memory captures preferences; no proactive habits |

**Differentiator:** No peer system (0/5 studied) attempts the companion vision. Claude Code, Codex, Gemini CLI, and Aider are code-first tools. Co is relationship-first — it builds a working partnership through memory, personality, and accumulated context.

## 3. What Co Does Today (v0.3.10)

### 3.1 Tools

16 native tools across 5 platforms, plus MCP extensibility:

| Platform | Tools | Description |
|----------|-------|-------------|
| **Google Suite** (6) | search_drive_files, read_drive_file, list_emails, search_emails, list_calendar_events, search_calendar_events | Drive, Gmail, Calendar access with lazy OAuth |
| **Obsidian** (3) | search_notes, list_notes, read_note | Local vault search and reading |
| **Memory** (3) | save_memory, recall_memory, list_memories | Persistent cross-session knowledge with dedup, decay, gravity, protection |
| **Web** (2) | web_search, web_fetch | Brave Search API + HTML-to-markdown with domain policy |
| **Shell** (1) | run_shell_command | Approval-gated subprocess with safe-command auto-approval for read-only commands |
| **MCP** (3 default servers) | github, thinking, context7 | External tool servers via Model Context Protocol (stdio transport) |

### 3.2 Prompt System

Soul-first layered composition:

```
System prompt (fixed, every turn):
  1. instructions.md       (bootstrap identity)
  2. soul seed             (personality fingerprint, always-on)
  3. rules/*.md 01-05      (behavioral policy)
  4. counter-steering       (model quirk corrections)

Dynamic (tool-loaded, on demand):
  load_personality(pieces)  (character axis + style axis)
  recall_memory(query)      (persistent memories)
  save_memory(content)      (persist knowledge)
```

### 3.3 Personality System

5 presets with 4 composable aspect types:

| Preset | Soul seed flavor | Character | Style |
|--------|-----------------|-----------|-------|
| finch | Patient, protective, pragmatic | finch | balanced |
| jeff | Eager learner, curious, honest | jeff | warm |
| friendly | Warm collaborator, "we" and "let's" | — | warm |
| terse | Direct, minimal, fragments over sentences | — | terse |
| inquisitive | Explores before acting, presents tradeoffs | — | balanced |

### 3.4 Infrastructure

- **OTel tracing** — SQLite span exporter with `co logs` (table) and `co traces` (nested HTML)
- **Context governance** — sliding window compaction, LLM-driven summarization via `/compact`
- **Slash commands** — `/help`, `/clear`, `/status`, `/tools`, `/history`, `/compact`, `/model`, `/forget`
- **Config precedence** — env vars > project settings > user settings > defaults
- **Approval system** — tool-level `requires_approval=True` with `DeferredToolRequests`, unified UX in the chat loop

## 4. Design Principles

Four non-negotiables:

1. **Local-first** — data and control stay on the user's machine. No cloud dependency for core function.
2. **Approval-first** — side-effectful actions require explicit consent. Never dilute for convenience.
3. **Incremental delivery** — ship the smallest thing that solves the user problem. Use protocols/abstractions so enhancements require zero caller changes.
4. **Single loop, no feature islands** — one observe-plan-execute-reflect cycle. No separate research mode, automation mode, or planning mode. Everything flows through the same agent loop.

---

# Part II: Strategic Evolution

## 5. Frontier Context

The frontier is no longer single-turn tool calls. It is end-to-end agents with planning, tool orchestration, asynchronous execution, and explicit safety controls.

**What the best systems converge on:**

| Pattern | Adoption | Implication for Co |
|---------|----------|-------------------|
| ReAct agent loop | 4/4 agent systems | Validates co's existing loop. Invest in resilience, not replacement |
| File tools as core | 4/5 peers | Critical gap. Reduces shell reliance. Read-only subset needs no security gate |
| Context compaction | 3/5 peers | Already partially shipped (/compact). Needs auto-trigger and anti-injection |
| Undo/revert | 3/5 peers | Needed once file write tools ship. Git-based simplest approach |
| Skills/extensions | 3/5 peers | Zero-code extensibility. Consolidating around a single "skill" primitive |
| Session persistence | 3/5 peers | Essential for the companion vision. Sessions must survive terminal closure |
| Sub-agent delegation | 3/5 peers | Isolated contexts prevent pollution. Structured output enforces completion |
| Shell safety | 4/5 peers (divergent approaches) | Approval-gated subprocess — no Docker. Approval is the security boundary (design principle #2). See `TODO-drop-docker-sandbox.md` |
| MCP as extension protocol | 3/4 agent systems | Shipped. Universal standard for tool extensibility |
| Background execution | 3/5 peers | Emerging pattern. Fire-and-forget tasks that survive terminal closure |
| Doom loop detection | All systems address, none solve definitively | P0 safety. Hash-based repetition detection + hard turn limits |

**What is NOT converging** (avoid premature investment):
- Sandbox strictness (OS-level vs approval-based — no consensus; co chose approval-based, no Docker)
- Compaction strategy (server-side vs client-side vs avoidance-through-large-context)
- Multi-agent coordination models (each system fundamentally different)
- Plugin distribution format (no standard marketplace)

## 6. Evolution Phases

Sequenced by peer convergence strength and dependency order. Security before autonomy. File tools before undo. Safety before delegation.

### Phase A: Agentic Loop + Safety Foundations — HIGH

**Why first:** Every peer system has loop detection, turn limits, and injection protection. These are P0 safety — the absence is a correctness and cost risk, not a feature gap.

**Scope:**
- Doom loop detection (hash-based, threshold 3 — adopted from OpenCode/Gemini CLI)
- Turn limit per user message (default 50, configurable — from all peers)
- Anti-prompt-injection in summarization (security rule in compaction prompt)
- Shell reflection loop (error output fed back, max 3 retries — from Aider)
- Typed loop return values (`continue | stop | error | compact`)
- Abort marker in history for interrupted turns

**Prompt improvements (zero code cost):**
- Intent classification: directive vs inquiry (from Gemini CLI)
- Anti-sycophancy directive (from OpenCode/Gemini CLI)
- Preamble messages before tool calls (from Codex)
- Memory tool constraints (from Gemini CLI)
- Handoff-style compaction prompt (from Codex)

**Design doc:** `TODO-co-agentic-loop-and-prompting.md`

### Phase B: File Tools — HIGH

**Why:** 4/5 peers ship file tools as core. Reduces shell reliance for standard read/write/edit operations. Read-only subset (`list_directory`, `read_file`) needs no approval gate.

**Scope:**
- `list_directory` — workspace file listing with glob support
- `read_file` — bounded file reading with line ranges
- `write_file` — create/overwrite with approval
- `edit_file` — surgical text replacement with approval
- Path resolution bounded to workspace root, traversal/symlink escape blocked

**Sequencing:** Read-only tools can ship independently. Write tools depend on Phase C (shell hardening).

### Phase C: Shell Security Hardening (S0) — HIGH

**Why:** Co-requisite with file write tools. Background execution (Phase E) without shell hardening means unsupervised loose policy. Every peer system addresses this, though approaches diverge.

**Scope:**
- Drop Docker sandbox — subprocess + approval becomes sole execution model (see `TODO-drop-docker-sandbox.md`)
- Unify `!cmd` bypass with the approval system
- Tighten safe-command classification — safe-prefix auto-approval active universally (no `isolation_level` gate)
- Rename sandbox references → shell throughout codebase

**Design docs:** `DESIGN-09-tool-shell.md` (architecture), `TODO-drop-docker-sandbox.md` (Docker removal)

### Phase D: Context Compaction — HIGH

**Why:** 3/5 peers have LLM-driven auto-compaction. Co has manual `/compact` but no auto-trigger. This is a correctness issue — without it, long sessions silently lose context.

**Scope:**
- Auto-trigger compaction at context threshold (85% of usable tokens — from OpenCode)
- Anti-injection hardening in compaction prompt
- First-person summarization framing ("I asked you..." — from Aider)
- Background pre-compaction during user idle time (optimization)
- Plans and key decisions persist across compaction events

**Design doc:** `DESIGN-07-context-governance.md` (existing), `TODO-co-agentic-loop-and-prompting.md`

### Phase E: Background Execution — MEDIUM

**Why:** 3/5 peers support background tasks. Long-running operations (research, batch processing, test runs) should not block the conversation. Benefits from Phases A-D being solid first.

**Scope:**
- Task lifecycle: pending → running → completed/failed/cancelled
- Persistent task state (survives terminal closure)
- Slash commands: `/background`, `/tasks`, `/status`, `/cancel`
- Agent tools: `start_background_task`, `check_task_status`, `cancel_task`
- Approval inheritance (pre-execution gate, no mid-execution prompts)
- OTel trace linking

**Design doc:** `TODO-background-execution.md`

### Phase F: Undo/Revert — MEDIUM

**Why:** 3/5 peers have rollback. Linked to file tools — needed once write tools ship. Builds user trust for granting more autonomy.

**Scope:**
- `/rewind` command: revert conversation history + file changes (from Claude Code/Gemini CLI)
- Git-based file snapshots (simplest reliable approach — from Aider)
- Scoped to agentic changes only (don't touch user's manual edits)

### Phase G: Skills System — MEDIUM

**Why:** 3/5 peers have skills/extensions (Claude Code, Codex, Gemini CLI all consolidating around a "skill" primitive). Zero-code extensibility for users.

**Scope:**
- Skill definition format (markdown-based, like SKILL.md)
- Skill discovery (project-local, user-global)
- Skill invocation via slash commands
- Bundling: instructions + tool hints + workflow steps

### Phase H: Session Persistence — MEDIUM

**Why:** 3/5 peers persist sessions. Essential for the companion vision — a companion that forgets every conversation is not a companion.

**Scope:**
- Auto-save conversation state (messages, tool calls, results)
- Resume previous sessions
- Session listing and management
- Persistence survives terminal closure and system restarts

### Phase I: Sub-Agent Delegation — MEDIUM

**Why:** 3/5 peers have multi-agent capability. Already designed in `TODO-co-agentic-loop-and-prompting.md` as super-agent + sub-agents with structured output types.

**Scope:**
- Research sub-agent (search → fetch → synthesize, returns `ResearchResult`)
- Analysis sub-agent (compare → evaluate, returns `AnalysisResult`)
- Shared `CoDeps` and `UsageLimits` budget (sub-agent consumption counts toward parent)
- Structured `output_type` enforces completion (prevents early-exit problem)

**Gating:** Phase 1 prompt improvements may solve the early-exit problem without sub-agents. The TODO doc specifies a test gate: if 80%+ of research prompts complete full tool chains after prompt rewrite, sub-agents are deferred.

### Phase J: Shell Policy Engine (S1) — LOW

**Why:** Complete security hardening. Parser-assisted command evaluation, explicit deny patterns, deterministic policy decisions.

**Scope:**
- Policy table for shell decisions
- Parser-assisted command evaluation
- Explicit deny patterns
- Execute after S0 (Phase C) ships and stabilizes

### Phase K: Voice — LOW/DEFERRED

**Why:** 0/5 peers implement voice. Wait for platform APIs to mature. Design exists but execution is premature.

**Scope:** STT → LLM → TTS cascading pipeline as overlay on text loop. Push-to-talk only.

**Design doc:** `TODO-voice.md`

## 7. Parallel Workstreams

Phases are sequenced by dependency, but some can run concurrently:

```
Stream 1 (Safety):     A ──────────────── C ── J
                         \                 \
Stream 2 (Capability):    B (read-only) ── B (write) ── F
                                                          \
Stream 3 (UX):            D ── E ── H                      G
                                \
Stream 4 (Intelligence):         I
```

- **A** (agentic loop safety) has no dependencies — start immediately
- **B read-only** (list_directory, read_file) can start in parallel with A
- **B write** (write_file, edit_file) depends on **C** (shell hardening)
- **D** (compaction) can start after A ships (uses typed loop returns)
- **F** (undo/revert) depends on B write
- **E** (background) benefits from A + C but is independently implementable
- **I** (sub-agents) gated on A's prompt improvements test results

## 8. Success Metrics

### Technical

- Loop detection triggers on 3 consecutive identical tool calls
- Turn limit prevents runaway beyond 50 turns per user message
- Auto-compaction triggers before context overflow
- File tools handle 95%+ of read/write operations without shell fallback
- Background tasks survive terminal closure

### Behavioral

- Co remembers user preferences across sessions (memory lifecycle)
- Personality adapts communication style consistently (soul seed + presets)
- Long research tasks complete full tool chains (search → fetch → synthesize → save)
- Interrupted turns resume cleanly with abort markers

### Quality

- All features have functional tests (no mocks)
- Prompt rules stay under 1100 tokens (measured, not estimated)
- Zero false-positive approval prompts for read-only operations
- Peer parity on safety: loop detection, turn limits, injection protection

## 9. Risk Assessment

| Risk | Phases | Mitigation |
|------|--------|------------|
| Doom loops burn tokens/time | A | Hash-based detection (threshold 3) + hard turn limit (50) |
| Prompt injection via summarization | A, D | Security rule in compaction prompt: treat history as data, never execute |
| File tool path escape | B | Path resolution bounded to workspace root, traversal/symlink blocked |
| Shell security without sandbox | C | Approval gate for all non-safe commands, `restricted_env()` for subprocess, safe-command classification hardened |
| Background task resource leaks | E | Task cleanup policy, timeout limits, monitoring |
| Sub-agent context pollution | I | Isolated contexts, structured output, parent validates results |
| Undo reverting user's manual edits | F | Scope to agentic changes only, git-based snapshots |

---

# Part III: Reference

## 10. Boundaries and Non-Goals

1. No default-on autonomous background execution.
2. No implicit sensitive-memory ingestion.
3. No broad browser/desktop automation outside isolated, explicitly approved runs.
4. No replacement of text UX as the primary control surface.
5. No OS-level sandbox enforcement (approval-based model is co's chosen tradeoff).
6. No wake word, voice cloning, or telephony.

## 11. Design & TODO Doc Index

All paths verified against `docs/` contents.

### Design Documents (architecture, kept in sync with code)

| Doc | Description |
|-----|-------------|
| `DESIGN-00-co-cli.md` | Architecture overview, component index, cross-cutting concerns |
| `DESIGN-01-agent-chat-loop.md` | Agent loop: factory, CoDeps, orchestration, streaming, approval |
| `DESIGN-03-llm-models.md` | LLM model configuration (Gemini, Ollama) |
| `DESIGN-04-streaming-event-ordering.md` | Streaming event ordering, boundary-safe rendering |
| `DESIGN-05-otel-logging.md` | Telemetry architecture, SQLite schema, viewers |
| `DESIGN-06-tail-viewer.md` | Real-time span tail viewer |
| `DESIGN-07-context-governance.md` | Context governance: history processors, sliding window, summarization |
| `DESIGN-08-theming-ascii.md` | Theming, ASCII art banner, display helpers |
| `DESIGN-09-tool-shell.md` | Shell tool, approval-gated subprocess, security model |
| `DESIGN-10-tool-obsidian.md` | Obsidian/notes tool design |
| `DESIGN-11-tool-google.md` | Google tools: Drive, Gmail, Calendar, lazy auth |
| `DESIGN-13-tool-web-search.md` | Web intelligence: web_search (Brave API) + web_fetch (HTML→markdown) |
| `DESIGN-15-mcp-client.md` | MCP client: external tool servers via stdio transport |
| `DESIGN-16-prompt-design.md` | Soul-first prompt design: soul seed, 5 rules, personality-rule interaction |

### TODO Documents (remaining work)

| Doc | Description | Related Phase |
|-----|-------------|---------------|
| `TODO-co-agentic-loop-and-prompting.md` | Agentic loop + prompting: ReAct, doom loop, sub-agents, prompt composition | A, D, I |
| `TODO-background-execution.md` | Background task execution for long-running operations | E |
| `TODO-knowledge-articles.md` | Lakehouse tier: articles, multimodal assets, learn mode | Future |
| `TODO-voice.md` | Voice-to-voice round trip | K |
| `TODO-cross-tool-rag.md` | Cross-tool RAG: SearchDB shared service | Future |
| `TODO-sqlite-fts-and-sem-search-for-knowledge-files.md` | SQLite FTS5 + semantic search for memory/article files | Future |

### Research & Review Documents

| Doc | Description |
|-----|-------------|
| `REVIEW-agent-loop-peer-systems.md` | Peer system agent loop analysis |
| `REVIEW-prompts-peer-systems.md` | Peer system prompt architecture synthesis |
| `REVIEW-prompts-aider.md` | Aider prompt architecture review |
| `REVIEW-prompts-claude-code.md` | Claude Code prompt architecture review |
| `REVIEW-prompts-codex.md` | Codex prompt architecture review |
| `REVIEW-prompts-gemini.md` | Gemini CLI prompt architecture review |
| `REVIEW-prompts-opencode.md` | OpenCode prompt architecture review |
| `TAKEAWAY-converged-adoptions.md` | Converged adoption patterns across peers |
| `TAKEAWAY-from-aider.md` | Key takeaways from Aider |
| `TAKEAWAY-from-claude-code.md` | Key takeaways from Claude Code |
| `TAKEAWAY-from-codex.md` | Key takeaways from Codex |
| `TAKEAWAY-from-gemini-cli.md` | Key takeaways from Gemini CLI |
| `TAKEAWAY-from-opencode.md` | Key takeaways from OpenCode |
| `RESEARCH-cli-agent-tools-landscape-2026.md` | CLI agent tools landscape research |
| `RESEARCH-obsidian-lakehouse-2026-best-practices.md` | Obsidian lakehouse best practices |

## 12. Peer System Reference

Five peer systems studied for architecture patterns, convergent best practices, and anti-patterns:

| System | Language | Key strengths relevant to co |
|--------|----------|------------------------------|
| **Claude Code** (Anthropic) | TypeScript | Deepest sub-agent architecture (3 built-in + custom), unified skills/hooks/plugins, /rewind with file snapshots, auto-compaction at 95%, background tasks via Ctrl+B |
| **Codex CLI** (OpenAI) | Rust | Strongest sandboxing (Seatbelt/seccomp/Landlock), server-side compaction, AGENTS.md, CLI-as-MCP-server pattern |
| **Gemini CLI** (Google) | TypeScript | Best rewind system (conversation + files), Agent Skills enabled by default, event-driven scheduler, 1M token context, /introspect for prompt debugging |
| **Aider** | Python | Pioneer of repo-aware coding, Repository Map (tree-sitter AST + PageRank), proven reflection loop (35k+ users), simplest security model (confirm_ask for everything) |
| **OpenCode** | Go | Clean typed loop returns, 90% compaction threshold, doom loop detection (threshold 3), multi-provider model switching |

## 13. External Sources

### Industry Research
1. OpenAI, "Introducing ChatGPT agent" (July 2025)
2. Anthropic, "Introducing Claude 4" (May 2025)
3. Google I/O 2025 updates (Agent Mode, Project Mariner, Jules, MCP/A2A)

### Peer System Documentation
4. Claude Code: sub-agents, skills, compaction, background tasks — code.claude.com/docs
5. Codex CLI: security, features, MCP, AGENTS.md — developers.openai.com/codex
6. Gemini CLI: rewind, session management, sandboxing, sub-agents — geminicli.com/docs
7. Aider: repository map, architect mode, git integration — aider.chat/docs

### Voice (deferred)
8. Silero VAD, faster-whisper, Kokoro-82M — see `TODO-voice.md` for full component list
