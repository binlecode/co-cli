# ROADMAP: Co Evolution

This is the strategic roadmap for co-cli: from capable tool-calling assistant to personal companion for knowledge work, and beyond to a multi-agent cultivation system where personalized agent instances grow knowledge together. Part I defines what co is. Part II charts single-agent maturation (Phases A-K). Part III extends to mutual cultivation (Phases L-S). Part IV unifies the memory system evolution path. Part V captures learnings from reference systems. Part VI provides reference material.

---

# Part I: Mission, Vision & Core Functionality

## 1. Mission

Co is a personal companion for knowledge work, running in the user's terminal.

It connects the tools a knowledge worker already uses — email, calendar, documents, notes, web — into a single conversational interface backed by LLM reasoning. It remembers what matters, develops personality, and forms a lasting working relationship with its user.

Co is not a code editor. It is not an IDE plugin. It is a general-purpose CLI companion that happens to be good at technical tasks because it has a shell, a memory, and access to the user's information surfaces.

## 2. Vision: Personalized AI Assistant

Co's north star is a personalized, autonomous AI assistant — CLI-primary, with shell utilities for voice — that accumulates real working context about its user over time and expresses a consistent, grounded character across every interaction.

**Three character modes, each grounded in source material:**

| Role | Source | Epistemic stance | Primary register |
|------|--------|-----------------|-----------------|
| **finch** | *Finch* (2021, Apple TV+) | Mentor — curates, prepares, names hard truths, teaches the "why" | Protective, load-bearing sentences |
| **jeff** | *Finch* (2021, Apple TV+) | Learner — genuine curiosity, admits uncertainty, "we" framing | Open, hopeful, encounter-driven |
| **tars** | *Interstellar* (2014) | Operator — volunteers before asked, holds constraints, humor front-loaded and flat | Tactical, reliable, sincerity breaks register |

These are not cosmetic personas. Each is sourced from observed character behavior — base memories in `.co-cli/knowledge/` carry the felt layer (scenes, speech patterns, relationship dynamics). The three roles cover the full interaction envelope: finch for depth and guidance, jeff for exploration and honest uncertainty, tars for operational efficiency and constraint-holding.

**Five pillars of co's character:**

| Pillar | Description |
|--------|-------------|
| **Soul** | Identity, personality, interaction style (user-selectable roles). 3 roles (file-driven), per-turn personality injection |
| **Internal Knowledge** | Learned context, patterns, user habits (persists across sessions). Memory lifecycle with save/recall/list |
| **External Knowledge** | Tools for accessing data (Google, Obsidian, web, MCP servers). 16 tools + 3 MCP servers |
| **Emotion** | Tone, empathy, context-aware communication. Personality modulates tone; no emotion engine yet |
| **Habit** | Workflow preferences, approval patterns, personalization. Memory captures preferences; no proactive habits yet |

**Differentiator:** No peer system (0/5 studied) attempts the companion vision. Claude Code, Codex, Gemini CLI, and Aider are code-first tools. Co is relationship-first — it builds a working partnership through memory, personality, and accumulated context.

## 3. What Co Does Today (v0.4.0)

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

Two-part structural prompt — static base assembled once, personality injected every turn:

```
Static prompt  (assembled once at agent creation — assemble_prompt()):
  1. soul seed                  (identity declaration — "You are X…")
  2. rules/*.md 01-05           (behavioral policy)
  3. counter-steering           (model quirk corrections, if file exists)

Per-turn layers  (@agent.system_prompt functions in agent.py):
  add_personality              (## Soul block: full soul + 5 behaviors)
  add_current_date             (today's date)
  add_shell_guidance           (shell approval hint)
  add_project_instructions     (.co-cli/instructions.md)
  add_personality_memories     (## Learned Context: top 5 personality-context memories)
```

First principle: **personality is structural — injected every turn, never tool-gated.** The LLM does not decide when to load personality. The soul seed anchors identity at the top of every static prompt; the full soul + behaviors reinforce it per turn. See `DESIGN-personality.md`.

### 3.3 Personality System

3 roles — `finch`, `jeff`, `tars` — each defined by two file layers and a base memory set. No Python dicts — the folder structure is the schema.

**Two file layers per role:**

| Layer | Files | Purpose |
|-------|-------|---------|
| **Soul** | `souls/{role}/seed.md` — identity declaration + Core trait essence + Never constraints | Static anchor; loaded once, present in every context window |
| | `souls/{role}/critique.md` — always-on self-eval lens | Injected every turn as `## Review lens` |
| | `souls/{role}/examples.md` — trigger→response patterns (optional) | Trailing the behavioral rules; closest to the task |
| **Mindsets** | `mindsets/{role}/{task_type}.md` — 6 task-specific files per role | Classified before Turn 1, injected every subsequent turn as `## Active mindset` |

**6 mindset task types:** `technical`, `exploration`, `debugging`, `teaching`, `emotional`, `memory`

**Base memories:** planted entries in `.co-cli/knowledge/` tagged `[role, "character"]` — sourced from observed behavior in the source material. Loaded deterministically by `get_agent()`, inserted between seed and behavioral rules. Decay-protected.

Role is selected at session start and immutable thereafter. Personality modulates HOW rules are expressed but NEVER overrides safety, approval gates, or factual accuracy. See `DESIGN-personality.md`.

### 3.4 Infrastructure

- **OTel tracing** — SQLite span exporter with `co logs` (table) and `co traces` (nested HTML)
- **Context governance** — sliding window compaction, LLM-driven summarization via `/compact`
- **Slash commands** — `/help`, `/clear`, `/status`, `/tools`, `/history`, `/compact`, `/model`, `/forget`
- **Config precedence** — env vars > project settings > user settings > defaults
- **Approval system** — tool-level `requires_approval=True` with `DeferredToolRequests`, unified UX in the chat loop

## 4. Design Principles

Four non-negotiables:

1. **Local-first** — data and control stay on the user's machine. No cloud dependency for core function.
2. **Approval-first** — side-effectful actions require explicit consent. Never dilute for convenience. Scoped approval (user pre-approves a category of actions for a bounded session) is acceptable; blanket auto-approval is not.
3. **Incremental delivery** — ship the smallest thing that solves the user problem. Use protocols/abstractions so enhancements require zero caller changes.
4. **Single primitive, multiple orchestration patterns** — `run_turn()` is the universal execution unit. Single-agent chat, sub-agent delegation, and multi-agent cultivation all compose `run_turn()` calls into different orchestration patterns. No pattern builds a parallel execution engine — they all reuse the same primitive, tools, CoDeps, approval gates, and safety rules. This prevents feature islands while allowing the system to grow beyond single-agent interaction.

---

# Part II: Single-Agent Evolution (Phases A-K)

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
| Shell safety | 4/5 peers (divergent approaches) | Approval-gated subprocess — no Docker. Approval is the security boundary (design principle #2) |
| MCP as extension protocol | 3/4 agent systems | Shipped. Universal standard for tool extensibility |
| Background execution | 3/5 peers | Emerging pattern. Fire-and-forget tasks that survive terminal closure |
| Doom loop detection | All systems address, none solve definitively | P0 safety. Hash-based repetition detection + hard turn limits |

**What is NOT converging** (avoid premature investment):
- Sandbox strictness (OS-level vs approval-based — no consensus; co chose approval-based, no Docker)
- Compaction strategy (server-side vs client-side vs avoidance-through-large-context)
- Multi-agent coordination models (each system fundamentally different; co's chosen approach is mutual cultivation via arena sessions — see Part III)
- Plugin distribution format (no standard marketplace)

## 6. Evolution Phases

Sequenced by peer convergence strength and dependency order. Security before autonomy. File tools before undo. Safety before delegation.

### Phase A: Agentic Loop + Safety Foundations — HIGH

**Why first:** Every peer system has loop detection, turn limits, and injection protection. These are P0 safety — the absence is a correctness and cost risk, not a feature gap.

**Shipped:**
- Turn limit per user message (default 50, configurable) — `_orchestrate.py:416`
- Typed loop return values (`continue | stop | error | compact`) — `_orchestrate.py:25`
- Abort marker in history for interrupted turns — `_orchestrate.py:609-615`
- Intent classification: three-way directive / deep inquiry / shallow inquiry — Rule 05
- Anti-sycophancy directive — Rule 01
- Preamble messages before tool calls — Rule 04
- Memory tool constraints — Rule 02
- Handoff-style compaction prompt with anti-injection — `_history.py`

**Remaining:**
- None in the Phase A safety baseline. Outstanding loop/prompt work is tracked under Phase I and future quality enhancements.

**Design docs:** `DESIGN-prompt-design.md`, `DESIGN-core.md`

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
- Subprocess + approval is the sole execution model (no Docker sandbox)
- Chat loop has no `!cmd` bypass path — all commands go through approval
- Tighten safe-command classification — safe-prefix auto-approval active universally (no `isolation_level` gate)

**Design docs:** `DESIGN-tools.md`

### Phase D: Context Compaction — HIGH

**Why:** 3/5 peers have LLM-driven auto-compaction. Co has manual `/compact` but no auto-trigger. This is a correctness issue — without it, long sessions silently lose context.

**Scope:**
- Auto-trigger compaction at context threshold (85% of usable tokens — from OpenCode)
- Anti-injection hardening in compaction prompt
- First-person summarization framing ("I asked you..." — from Aider)
- Background pre-compaction during user idle time (optimization)
- Plans and key decisions persist across compaction events

**Design docs:** `DESIGN-prompt-design.md`

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

**Why:** 3/5 peers have multi-agent capability. The baseline design lives in `DESIGN-prompt-design.md`; remaining implementation work is tracked in `TODO-subagent-delegation.md`.

**Priority inside Phase I TODO set:** P1

**Implementation timing (dependency-aware):**
- After baseline `TODO-background-execution.md` is in place (long-running delegated work can evolve toward non-blocking execution paths).
- Before confidence-scored advisory outputs and voice overlays.

**Scope:**
- Research sub-agent (search → fetch → synthesize, returns `ResearchResult`)
- Analysis sub-agent (compare → evaluate, returns `AnalysisResult`)
- Shared `CoDeps` and `UsageLimits` budget (sub-agent consumption counts toward parent)
- Structured `output_type` enforces completion (prevents early-exit problem)

**Orchestration pattern:** Phase I introduces multi-`run_turn()` orchestration — a parent agent dispatching sub-agent turns and collecting results. Whichever of Phase I or Phase M (arena controller) ships first defines this pattern; the second phase reuses it. Both are compositions of the same `run_turn()` primitive (Design Principle #4).

**Gating:** Phase 1 prompt improvements may solve the early-exit problem without sub-agents. The TODO doc specifies a test gate: if 80%+ of research prompts complete full tool chains after prompt rewrite, sub-agents are deferred.

**Other remaining prompt-loop items (from TODO-co-agentic-loop-and-prompting):**
- Personality prompt-budget optimization — **P1**, recommended before sub-agent rollout to reclaim context headroom.
- Confidence-scored advisory outputs — **P2**, recommended after search-quality upgrades (`TODO-sqlite-tag-fts-sem-search-for-knowledge.md` and knowledge/article evolution) so scores are signal-backed instead of guessy.

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

## 7. Single-Agent Success Metrics

### Technical

- Loop detection triggers on 3 consecutive identical tool calls
- Turn limit prevents runaway beyond 50 turns per user message
- Auto-compaction triggers before context overflow
- File tools handle 95%+ of read/write operations without shell fallback
- Background tasks survive terminal closure

### Behavioral

- Co remembers user preferences across sessions (memory lifecycle)
- Personality adapts communication style consistently (file-driven roles with 5 traits)
- Long research tasks complete full tool chains (search → fetch → synthesize → save)
- Interrupted turns resume cleanly with abort markers

### Quality

- All features have functional tests (no mocks)
- Prompt rules stay under 1100 tokens (measured, not estimated)
- Zero false-positive approval prompts for read-only operations
- Peer parity on safety: loop detection, turn limits, injection protection

## 8. Single-Agent Risk Assessment

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

# Part III: Mutual Cultivation (Phases L-S)

Mutual cultivation extends co from a single-agent companion to a multi-agent knowledge-building system. Two personalized agent instances grow each other's persistent knowledge through structured debate, mutual challenge, and goal-directed research.

**Production prerequisites:** Phase A (agentic loop safety), Phase D (context compaction), Phase H (session persistence). These must be stable before cultivation sessions run unsupervised. However, implementation of Phases L-P can begin in parallel with single-agent phases — the dependency is on production readiness, not on code ordering.

## 9. The Finch-Jeff Dynamic as a Learning Primitive

Co already ships two complementary characters:

| Character | Epistemic stance | Natural role in debate |
|-----------|-----------------|----------------------|
| **Finch** | Mentor — curates, warns, explains tradeoffs, teaches the "why" | Validates claims, identifies risks, synthesizes with experience |
| **Jeff** | Learner — questions, narrates thinking, celebrates discoveries, admits confusion | Challenges assumptions, asks "why?", surfaces gaps through honest uncertainty |

These are not cosmetic personas. They encode fundamentally different approaches to knowledge: Finch *curates* (strategic teaching, protective preparation), Jeff *explores* (genuine curiosity, literal interpretation, stubborn questioning). That asymmetry is the engine of mutual cultivation — a mentor who never questions and a learner who never teaches both stagnate. The dynamic requires role reversal: Jeff challenges Finch's assumptions, Finch learns from Jeff's fresh perspective.

The multi-agent debate literature (A-HMAD, 2025) confirms this: balanced heterogeneous teams with genuine diversity in reasoning stance outperform homogeneous teams by 4-6% accuracy with 30% fewer factual errors. The value comes from *different epistemic stances toward the same problem*, not from different underlying models.

## 10. What Mutual Cultivation Means

**Mutual cultivation** is two agents growing each other's persistent knowledge through structured interaction. The term borrows from both gardening (you cultivate over time, prune, feed, protect) and the AI safety community's "AI cultivation" framing. In the multi-agent literature, the closest formal term is "co-evolution" — agents that evolve through mutual interaction.

Concretely, mutual cultivation means:

1. **Two personalized co instances** (Finch and Jeff, or any two character agents) share a research session
2. **Each agent maintains private persistent memory** — its own understanding, metacognitive reflections, models of the other agent
3. **Agents share external knowledge resources** — Obsidian vault, web search, Google tools, MCP servers
4. **Validated findings graduate to shared knowledge** through a peer-validation protocol — neither agent unilaterally writes shared knowledge
5. **Research is goal-directed** — scoped by topic, depth, and explicit success criteria, not free exploration
6. **Each session produces auditable artifacts** — transcript, private memories, shared findings, gap map

**What it is not:** This is not autonomous background learning. The user initiates a research goal, observes (or reviews) the session, and the system produces structured knowledge artifacts. Design Principle #2 (approval-first) still applies — agents do not learn unsupervised by default.

**Single-agent applicability:** Several cultivation components are independently valuable for single-agent mode. The knowledge audit tool (Section 15) is useful for a single agent to self-assess before answering a user's question. The gap map (Section 13) can drive single-agent goal-directed research. Shared findings storage can hold user-validated facts even without multi-agent debate. Phases L and O deliver value to single-agent `co chat` immediately, not just to `co research`.

### 10.1 Terminology

| Concept | Term | Definition |
|---------|------|------------|
| Two agents growing together | **Mutual cultivation** | Structured multi-agent knowledge building through debate and research |
| A research debate session | **Arena session** | A bounded sequence of alternating agent turns pursuing a research goal |
| The gap analysis + goal-setting | **Curriculum** | Goal specification + gap map that drives what agents research (Voyager's term) |
| Agent knowing what it knows | **Knowledge audit** | Structured self-assessment: known, uncertain, unknown per subtopic |
| Private-to-shared knowledge promotion | **Peer validation** | Protocol requiring both agents to agree before findings enter shared knowledge |
| The Finch + Jeff pair (or any two) | **Cultivation pair** | Two agents configured with complementary epistemic stances |
| Post-session knowledge extraction | **Harvest** | Consolidation of session-derived memories and findings into persistent knowledge |

## 11. Cultivation Architecture

```
User
  │
  ├── co chat (single-agent, existing)
  │     └── Agent(personality=finch) → CoDeps → tools → memory
  │
  └── co research <topic> (cultivation, new)
        │
        Arena Controller (~150 lines, no LLM reasoning)
        ├── Turn management (alternating, round-typed)
        ├── Termination criteria enforcement
        ├── Transcript logging
        └── Harvest orchestration
        │
        ├── Finch Agent
        │     ├── personality: finch (mentor stance)
        │     ├── CoDeps_finch (own session_id, own memory_dir)
        │     ├── tools: recall_memory, save_memory, save_shared_finding,
        │     │          knowledge_audit, web_search, web_fetch, obsidian_search,
        │     │          read_file, list_directory
        │     └── system prompt: static (instructions + rules + quirks) + per-turn personality + arena injection
        │
        ├── Jeff Agent
        │     ├── personality: jeff (learner stance)
        │     ├── CoDeps_jeff (own session_id, own memory_dir)
        │     ├── tools: recall_memory, save_memory, save_shared_finding,
        │     │          knowledge_audit, web_search, web_fetch, obsidian_search,
        │     │          read_file, list_directory
        │     └── system prompt: static (instructions + rules + quirks) + per-turn personality + arena injection
        │
        └── Shared Resources
              ├── Obsidian vault (read-only during session)
              ├── Web search / web fetch (both agents can use)
              ├── File tools: read_file, list_directory (read-only, no approval)
              ├── Shared findings store (.co-cli/knowledge/shared/findings/)
              └── Session transcript log (.co-cli/knowledge/shared/sessions/)
```

**Design Principle #4 compliance:** The arena controller does not build a parallel execution engine. It calls `run_turn()` in alternation — the same primitive used by `co chat` and sub-agent delegation (Phase I). Tools, CoDeps, approval gates, safety rules (doom loop detection, turn limits), and context compaction all remain active per agent. The arena is an orchestration pattern over `run_turn()`, not a feature island.

**Why not AutoGen, CrewAI, or LangGraph?** Co already has the agent primitive (pydantic-ai Agent), the orchestration engine (`run_turn()`), and the frontend abstraction (`FrontendProtocol`). The arena controller is ~150 lines of async Python. Adding a multi-agent framework buys abstractions that conflict with co's existing patterns: CrewAI's role/goal/backstory model duplicates souls/traits, AutoGen's actor model is heavyweight for two agents, LangGraph's graph-based control flow is unnecessary when turn order is simply alternating. The right amount of complexity is the minimum needed for the current task.

**Why not OpenAI Swarm / Agents SDK handoff pattern?** Handoffs transfer control from one agent to another within a single conversation. Cultivation requires *parallel persistent state* — each agent maintains its own memory across turns. Handoffs lose this isolation. The blackboard pattern (shared transcript, isolated state) is the correct fit.

## 12. Three-Tier Knowledge Architecture

Knowledge is scoped by visibility and validation level:

| Tier | Scope | Storage | Lifecycle | Examples |
|------|-------|---------|-----------|----------|
| **Private memory** | Per-agent | `.co-cli/knowledge/agents/{name}/memories/` | Standard memory lifecycle: dedup, gravity, decay, protection | "Jeff struggles with async patterns — start with sync analogies" |
| **Shared findings** | Cross-agent | `.co-cli/knowledge/shared/findings/` | Peer-validated, protected from decay | "Python 3.13 JIT compiler improves hot-loop performance 2-5x (source: PEP 744)" |
| **External knowledge** | Read-only, shared | Obsidian vault, web, Google Drive, MCP servers | Not owned by co — user's existing knowledge surfaces | Papers, notes, articles, documentation |

### 12.1 Private Memory

Each agent's private memory uses the existing memory lifecycle system (save/recall/list with dedup, gravity, decay, protection) scoped to a per-agent directory. This requires one change: adding `memory_dir` as a field on `CoDeps` (currently the memory directory is implicit from the project root).

Private memory stores:
- **Domain knowledge** — what the agent has learned about topics
- **Metacognitive reflections** — "I was wrong about X because Y", "My understanding of Z is incomplete"
- **Peer models** — "Finch tends to over-index on safety concerns", "Jeff asks good questions about edge cases"
- **Session context** — "Last session we covered tokio basics, this session should go deeper into the executor"

### 12.2 Shared Findings

Shared findings are the validated research outputs. A finding enters shared storage only through the peer validation protocol (Section 14). Shared findings have richer frontmatter than private memories:

```yaml
---
id: 42
slug: python-313-jit-hot-loop-perf
created: 2026-02-16T10:30:00Z
contributors: [finch, jeff]
confidence: high
topic: python-performance
evidence:
  - "PEP 744 — JIT compilation"
  - "https://docs.python.org/3.13/whatsnew/3.13.html#jit-compiler"
validated_round: 7
---
Python 3.13's experimental JIT compiler (PEP 744) improves hot-loop performance
by 2-5x in micro-benchmarks. The JIT uses a copy-and-patch strategy rather than
traditional tiered compilation. Currently opt-in via --enable-experimental-jit
build flag. Production readiness is expected in 3.14.
```

Key differences from private memories:
- `contributors` — which agents validated the finding
- `confidence` — high / medium / low (both agents must agree on the level)
- `evidence` — source links and references
- `validated_round` — which arena round produced the validation
- **Protected from decay by default** — shared findings represent validated knowledge

### 12.3 Relationship to Existing Knowledge Tiers

The existing roadmap plans two knowledge kinds: Memory (shipped) and Articles (planned in `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`). Shared findings are a third kind — between memories (conversation-derived, single-agent) and articles (curated, multimodal). The three kinds form a knowledge maturity pipeline:

```
Private memories (raw, per-agent)
    ↓ peer validation
Shared findings (validated, cross-agent)
    ↓ user curation / article authoring
Articles (polished, publishable)
```

## 13. Goal-Directed Curriculum System

Cultivation is not free exploration. Every arena session is bounded by a goal specification that constrains what agents research and when they stop.

### 13.1 Goal Specification

```yaml
topic: "Rust async runtime internals"
scope: "tokio vs async-std architecture, executor design, waker mechanics"
depth: curious | practitioner | expert
existing_knowledge_query: "async rust"
success_criteria:
  - "Can explain tokio's work-stealing scheduler"
  - "Can compare cooperative vs preemptive task yielding"
  - "Can identify when to use tokio vs async-std"
max_rounds: 20
max_web_searches: 30
```

**Depth levels** control the granularity of the gap map and the expected evidence quality:

| Depth | Gap map granularity | Evidence standard | Typical rounds |
|-------|-------------------|-------------------|----------------|
| **curious** | Top-level subtopics only | "We found that..." (summary-level) | 5-10 |
| **practitioner** | Subtopics + key details | Specific sources, code examples, tradeoff analysis | 10-20 |
| **expert** | Exhaustive breakdown | Primary sources, benchmarks, edge cases, counterarguments | 15-30 |

### 13.2 Gap Analysis Loop

Before the debate starts, the arena controller runs a gap analysis to produce the session agenda:

```
1. Both agents recall_memory(topic) independently
2. Each agent produces a self-assessment:
   "What I know / What I'm uncertain about / What I don't know"
3. Arena controller maps assessments against success_criteria
4. Produce a GAP MAP:
   ├── Known by both (green)    → validate only, do not re-research
   ├── Known by one (yellow)    → teaching opportunity (knower teaches other)
   ├── Unknown by both (red)    → external research needed (web, obsidian, fetch)
   └── Contradicted (orange)    → debate to resolve disagreement
5. Gap map becomes the round-by-round agenda
```

**Grounding in research:** The strategic self-improvement paper (arXiv 2512.04988) found metacognition — accurate self-assessment of capabilities — had the highest correlation (r=0.744) with agent performance of any factor studied. The gap map operationalizes metacognition: agents don't just know things, they know what they don't know and have a plan to fill those gaps.

**Grounding in practice (Voyager pattern):** Voyager's automatic curriculum proposes tasks at the frontier of what the agent can almost do. The gap map serves the same purpose — it focuses debate on the boundary between known and unknown, preventing both trivial repetition and impossible leaps.

### 13.3 Termination Criteria

An arena session ends when any of:

1. **All success criteria met** — each criterion has at least one shared finding with evidence
2. **Max rounds reached** — hard limit prevents runaway
3. **Diminishing returns** — last 3 rounds produced no new shared findings (the frontier has stopped advancing)
4. **Budget exhausted** — web search limit or token budget reached
5. **User interrupt** — always available; partial results are preserved

### 13.4 Anti-Patterns in Goal-Directed Learning

These are failure modes identified across the literature. The curriculum system is designed to prevent each one:

| Anti-pattern | Prevention mechanism |
|-------------|---------------------|
| Free exploration without constraints | Goal spec with explicit scope and success criteria |
| Learning everything at once | Depth levels + gap map focus each round on one subtopic |
| Ignoring existing knowledge | Gap analysis recalls memories before any research; green items are skipped |
| No termination criteria | Five explicit termination conditions (Section 13.3) |
| No decay/pruning | Post-session harvest consolidates session memories; standard decay applies |
| Goal drift in long sessions | Round types (Section 16) are selected from the gap map, not generated freely |

## 14. Peer Validation Protocol

The mechanism by which private knowledge becomes shared knowledge. This is the mutual-learning core — neither agent unilaterally writes shared findings.

```
1. Agent proposes a finding:
   save_shared_finding(claim, evidence, confidence)
   → Finding enters PENDING state in shared store

2. Arena controller injects prompt to the other agent:
   "{Agent} claims: {claim}. Evidence: {evidence}.
    Do you AGREE, CHALLENGE, or REFINE this finding?"

3a. AGREE → Finding promoted to VALIDATED, saved to shared findings
3b. CHALLENGE → Enters focused debate (max 3 exchanges on this specific claim)
    → If resolved: VALIDATED with updated claim/evidence
    → If unresolved after 3 exchanges: saved with confidence=contested
3c. REFINE → Proposing agent sees the refinement
    → Refined version re-enters step 2
    → Max 2 refinement rounds before forced resolution
```

**Grounding (Mem0):** Mem0's knowledge graph uses a two-phase pipeline: extraction (entities + relationships) → conflict detection + resolution (add, merge, invalidate, skip). The peer validation protocol is the same pattern applied to agent-to-agent knowledge building instead of text-to-graph extraction. The conflict resolver is the other agent rather than an LLM classifier.

**Grounding (A-HMAD):** The adaptive heterogeneous multi-agent debate framework shows that diverse agents converging through structured rounds produces higher accuracy than any single agent. But the key finding is that *intrinsic reasoning strength matters more than debate structure*. This means the protocol should be lightweight — the value comes from the epistemic stance difference (Finch vs Jeff), not from elaborate debate choreography.

## 15. Knowledge Audit

A capability that enables agent self-inspection — the metacognitive primitive that drives gap analysis. Valuable for both single-agent and multi-agent modes.

```
knowledge_audit(topic, depth="practitioner") → {
    display: "...",
    known: [
        {subtopic: "tokio scheduler", confidence: "high", memory_ids: [12, 34]},
        ...
    ],
    uncertain: [
        {subtopic: "waker mechanics", confidence: "low", memory_ids: [56]},
        ...
    ],
    unknown: [
        {subtopic: "async-std executor design"},
        ...
    ],
    coverage: 0.45
}
```

**Implementation as a two-step workflow, not a single tool:**

The knowledge audit is a *workflow* the agent executes, not a monolithic tool. Keeping tools as pure data operations (co's pattern) means the LLM generates the topic outline, and a tool does the matching:

1. **Step 1 (agent reasoning):** The agent generates a topic outline at the specified depth. This is a natural LLM output, not a tool call. The arena controller (or user) prompts: "What should a {depth}-level person know about {topic}? List subtopics."
2. **Step 2 (tool call):** `match_memories_to_outline(outline, topic)` — searches private memories for each subtopic, classifies coverage (known/uncertain/unknown based on recency, touch count, confidence), computes coverage score. This is a pure data operation: grep/search over memories, no internal LLM call.

This preserves the pattern that tools return data for the LLM, not the other way around.

**Grounding (MUSE):** The MUSE framework (Metacognition for Unknown Situations) models agent competence boundaries — what the agent can handle vs where it's operating outside known territory. The knowledge audit is the same principle applied to declarative knowledge rather than procedural competence.

**Grounding (MemSkill):** MemSkill's designer component reviews failure cases and proposes skill refinements. The knowledge audit serves the same purpose — it identifies where the agent's knowledge is weak so the curriculum system can target those areas.

## 16. Arena Debate Protocol

Each round in the arena has a type selected by the arena controller based on the gap map:

| Round type | When selected | Format | Expected output |
|-----------|---------------|--------|-----------------|
| **Teach** | One agent knows (yellow gap) | Knower explains → learner questions → learner restates to confirm understanding | Private memory for learner, teaching log for knower |
| **Research** | Neither agent knows (red gap) | Both search independently → share raw findings → synthesize | Web search/fetch tool calls, candidate shared findings |
| **Challenge** | Contradiction detected (orange gap) | Claim + counter-claim + evidence → resolution vote | Resolved shared finding or contested finding |
| **Verify** | Checking existing knowledge (green gap) | Agent states knowledge → other validates against sources | Confirmed finding or corrected understanding |
| **Synthesize** | Enough raw findings accumulated | Both propose synthesis → merge → peer validation | Shared finding with consolidated evidence |

### 16.1 Turn Structure

Each agent turn within a round follows this structure:

```
1. Arena controller injects: round type + context + specific question/task
2. Agent reads its own private memories relevant to the subtopic
3. Agent optionally uses external tools (web search, obsidian, read_file, etc.)
4. Agent produces a response (text + optional tool calls)
5. Agent optionally saves private memory or proposes a shared finding
6. Arena controller captures the response and routes to the other agent
```

### 16.2 Prompt Injection for Arena Mode

Each agent's system prompt is extended with an arena-mode addendum. This follows the existing pattern of `@agent.system_prompt` functions in `agent.py` — an `add_arena_context` function injects the arena addendum per turn:

**Finch (arena mode):**
```
You are in a research session with Jeff. Your goal: {goal.topic}.
Scope: {goal.scope}. Depth: {goal.depth}.

Your role: Share what you know. Challenge Jeff's assumptions when they lack evidence.
Teach when he's confused. Save validated findings to shared memory.
When you don't know something, say so — don't fabricate.
```

**Jeff (arena mode):**
```
You are in a research session with Finch. Your goal: {goal.topic}.
Scope: {goal.scope}. Depth: {goal.depth}.

Your role: Ask questions. Verify claims — don't accept without evidence.
Celebrate when you learn something genuinely new. Challenge anything that
doesn't make sense. When confused, say so honestly.
```

These addenda leverage the existing character traits. Finch already "explains risks without blocking" and "shares the why behind decisions." Jeff already "asks questions, narrates thinking, and celebrates discoveries." The arena prompt channels these traits toward a research goal rather than a user's task.

## 17. Cultivation Phases (L-S)

These phases extend the single-agent roadmap (Phases A-K). They are sequenced by dependency order: isolated knowledge before shared knowledge, shared knowledge before structured debate, structured debate before self-directed curriculum.

### Phase L: Agent Isolation & Private Knowledge — HIGH

**Why first:** Before agents can talk to each other, each agent must have its own isolated knowledge store. This is the foundation — everything else depends on per-agent memory scoping.

**Scope:**
- Add `memory_dir` field to `CoDeps`
- Refactor memory tools to use `ctx.deps.memory_dir`
- Directory layout for per-agent and shared knowledge
- CLI: `co research --agents finch,jeff <topic>` creates two isolated CoDeps instances

**Depends on:** None (can start in parallel with single-agent Phases A-K)

**Single-agent benefit:** `memory_dir` on CoDeps enables project-scoped vs user-scoped memory directories even without multi-agent mode.

### Phase M: Arena Controller — HIGH

**Why:** The arena controller is the coordination primitive. Without it, agents cannot take turns.

**Scope:**
- `co_cli/arena.py` — arena controller (~150 lines)
- `GoalSpec` dataclass — topic, scope, depth, success criteria, limits
- `ArenaResult` dataclass — transcript, shared findings, gap map, metrics
- `run_arena()` — creates two agents, alternates `run_turn()`, enforces termination
- Headless frontend for non-interactive agent turns (extend `RecordingFrontend`)
- Session transcript logging to `.co-cli/knowledge/shared/sessions/`
- Per-agent compaction: summarize each agent's history every N rounds (arena-specific, ensures long sessions don't hit context limits even if Phase D auto-compaction hasn't shipped yet)

**Depends on:** Phase L (agent isolation). Phase A (safety: doom loop detection and turn limits must be active per agent).

**Orchestration pattern:** Phase M introduces the same multi-`run_turn()` orchestration as Phase I (sub-agent delegation). Whichever ships first defines the shared pattern; the second reuses it. See Phase I note.

**Relationship to Phase E (background execution):** Arena sessions at `depth=expert` can run 15-30 rounds and take significant time. Once Phase E ships, `co research` should support running as a background task (`co research --background <topic>`), producing results that the user reviews later. This is not required for MVP but is the natural integration point.

### Phase N: Peer Validation & Shared Findings — HIGH

**Why:** Without peer validation, agents can write contradictory shared knowledge. The validation protocol is the quality gate.

**Scope:**
- `save_shared_finding` tool — proposes a finding to shared store (PENDING state), returns `dict[str, Any]` with `display` field per co's tool return convention
- Validation prompt injection — arena controller routes proposals to the other agent
- AGREE / CHALLENGE / REFINE protocol (Section 14)
- Shared findings storage with enriched frontmatter
- Protected-from-decay flag on validated findings

**Depends on:** Phase M (arena controller)

**Relationship to Phase F (undo/revert):** If a cultivation session produces bad shared findings, the user should be able to revert. Once Phase F ships, `/rewind` should support reverting a cultivation session's shared findings (delete findings created in session X). This requires findings to record their session ID in frontmatter. Not required for MVP but the frontmatter should include `session_id` from the start.

### Phase O: Gap Analysis & Curriculum — MEDIUM

**Why:** Without gap analysis, agents debate randomly. The curriculum focuses debate on the knowledge frontier.

**Scope:**
- `match_memories_to_outline` tool — pure data matching for the knowledge audit workflow (Section 15)
- Gap map computation from dual knowledge audits
- Round-type selection from gap map
- Success criteria tracking and termination logic

**Depends on:** Phase N (shared findings provide the "known" baseline)

**Single-agent benefit:** The knowledge audit workflow and `match_memories_to_outline` tool are useful for single-agent `co chat` — the agent can self-assess before answering a user's question about a topic it has memories on.

### Phase P: Hybrid Search for Memory — MEDIUM

**Why:** As memories accumulate, grep-based recall becomes inadequate. Semantic search is required for accurate knowledge audit (the audit needs to find relevant memories even when terminology doesn't match exactly).

**Scope:** Implemented via the unified `KnowledgeIndex` from `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`, which is the authoritative design for co's search infrastructure. Phase P adds agent-scoping to that design:
- `KnowledgeIndex` gets an `agent` column alongside the existing `source` column
- Single-agent mode: `agent=NULL` (backward-compatible)
- Arena mode: each agent's memories indexed with `agent="finch"` or `agent="jeff"`
- Shared findings indexed with `agent=NULL, source="finding"`
- All agents share one `search.db` — no per-agent databases

The `TODO-sqlite-tag-fts-sem-search-for-knowledge.md` design covers: FTS5 BM25 ranking, hybrid search with embeddings (sqlite-vec), weighted merge (0.7 vector / 0.3 text), embedding cache with hash-based dedup, graceful degradation (vector unavailable → FTS5-only), and cross-source search. Phase P's only addition is the `agent` column for multi-agent scoping.

**Depends on:** Phase L (per-agent memory directories). Can run in parallel with Phases M-O.

**Design doc:** `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`

### Phase Q: Harvest & Consolidation — MEDIUM

**Why:** Arena sessions produce many memories per agent. Without post-session consolidation, memory bloat degrades recall quality.

**Scope:**
- Post-session harvest: deduplicate session-derived memories against existing store
- Consolidation: merge related findings into higher-level summaries
- Session summary: auto-generated summary of what was learned, saved as a protected memory
- Budget enforcement: per-agent memory limits apply; decay runs after harvest
- Transcript archival: session transcripts compressed and archived after harvest

**Depends on:** Phase N (shared findings are harvest inputs)

### Phase R: Knowledge Graph & Cross-Agent Analysis — LOW

**Why:** Enables structural gap detection and cross-agent knowledge comparison. Powerful but not essential for the core cultivation loop.

**Scope:**
- Entity and relationship extraction on memory save (LLM call, can be batched)
- Relationship storage in SQLite (simple edge table: `source_id, target_id, relation_label`)
- Multi-hop traversal in recall: if memory A references entity X, and memory B also references entity X, B surfaces as a related result
- Cross-agent knowledge graph: unified graph spanning both agents' private memories and shared findings
- Agent-scoped nodes: each entity tagged with source agent
- Differential query: "what does Finch know that Jeff doesn't about X?"
- Graph topology analysis for knowledge island detection — isolated clusters indicate underdeveloped subtopics

**Depends on:** Phase P (requires search index for entity extraction)

### Phase S: Multi-Pair & Team Cultivation — LOW

**Why:** Two agents is a pair. Three or more enables richer dynamics — a researcher, a critic, and a synthesizer. But the pair is the MVP; team dynamics are a stretch goal.

**Scope:**
- Arena controller supports N agents (generalize alternating turns to round-robin or supervisor-directed)
- New personality roles (e.g., "skeptic" — challenges everything, demands primary sources)
- Supervisor agent that manages turn allocation based on expertise (LangGraph supervisor pattern)
- Shared findings require majority validation (not just pairwise agreement)

**Depends on:** Phases L-Q stable

## 18. Cultivation Infrastructure

### 18.1 CoDeps Changes

```python
@dataclass
class CoDeps:
    # ... existing fields ...

    # New: per-agent memory scoping
    memory_dir: Path           # Default: .co-cli/knowledge/memories/
    agent_name: str | None     # None for single-agent mode, "finch"/"jeff" for arena

    # New: shared knowledge access
    shared_findings_dir: Path  # Default: .co-cli/knowledge/shared/findings/
```

CoDeps remains flat fields. No nested config objects. `memory_dir` and `shared_findings_dir` are paths (consistent with the existing `obsidian_vault_path: Path | None` precedent).

**Directory layout:**
```
.co-cli/knowledge/
├── memories/               # Default (single-agent, backward-compatible)
├── agents/
│   ├── finch/memories/     # Finch's private memories
│   └── jeff/memories/      # Jeff's private memories
└── shared/
    ├── findings/           # Peer-validated shared findings
    └── sessions/           # Arena session transcripts
```

### 18.2 Agent Factory Changes

```
get_agent() currently returns: (Agent, ModelSettings, tool_names)

Personality is NOT a get_agent() parameter — it is set on CoDeps.personality
and read per turn by add_personality() in @agent.system_prompt.

For arena mode, call get_agent() twice. Each agent gets:
  - CoDeps with personality="finch" (or "jeff"), memory_dir scoped per-agent
  - An additional @agent.system_prompt function (add_arena_context)
    that injects the arena addendum per turn

Arena mode affects:
  - Per-turn prompt: adds arena-mode addendum via @agent.system_prompt
  - Tools: adds save_shared_finding, match_memories_to_outline
  - File tools (read_file, list_directory) available for research
  - Does NOT change: model, provider, static prompt, safety rules, tool approval
```

### 18.3 Frontend for Arena Mode

The existing `FrontendProtocol` supports non-interactive operation via `RecordingFrontend` (used in tests). Arena mode extends this:

```python
class ArenaFrontend(FrontendProtocol):
    """Frontend that displays both agents' output interleaved."""

    def on_text_delta(self, agent_name: str, accumulated: str) -> None: ...
    def on_tool_call(self, agent_name: str, name: str, args: str) -> None: ...
    def on_round_start(self, round_num: int, round_type: str, subtopic: str) -> None: ...
    def on_finding_proposed(self, agent_name: str, claim: str) -> None: ...
    def on_finding_validated(self, claim: str, status: str) -> None: ...
    def prompt_approval(self, agent_name: str, description: str) -> str: ...
```

**Approval model in arena mode (Design Principle #2 compliance):** The user explicitly opts in to scoped auto-approval at session start. When `co research` launches, it prompts: "This research session will search the web and fetch URLs automatically. Approve for this session? [y/n]". If approved, web searches and fetches are auto-approved for the session duration. Shell commands, if any, still require per-command approval. Read-only tools (`recall_memory`, `search_notes`, `read_file`, `list_directory`) require no approval (consistent with single-agent behavior). This is scoped approval, not blanket auto-approval — it applies to one session and one category of tools.

### 18.4 New CLI Command

```
co research <topic> [--scope SCOPE] [--depth DEPTH] [--rounds MAX_ROUNDS]
                     [--agents AGENT1,AGENT2] [--criteria "criterion 1" "criterion 2"]
                     [--auto-approve-web] [--background]
```

- `topic` — required, the research subject
- `--scope` — optional, narrows the research area
- `--depth` — `curious` (default), `practitioner`, `expert`
- `--rounds` — maximum rounds (default: 15)
- `--agents` — comma-separated agent names (default: `finch,jeff`)
- `--criteria` — explicit success criteria (optional; if omitted, generated from topic + depth)
- `--auto-approve-web` — pre-approve web searches/fetches for the session (skips interactive confirmation)
- `--background` — run as background task (requires Phase E); streams results to file, user reviews later

Output: streams the debate to terminal in real-time, followed by a summary of shared findings and remaining gaps.

**Relationship to Phase G (skills system):** If Phase G ships before Phase M, `co research` could be implemented as a built-in skill rather than a hardcoded CLI command. The skill would bundle the arena controller, goal spec parsing, and output formatting. This preserves extensibility — users could create custom cultivation skills with different debate protocols.

## 19. Cultivation Success Metrics

### Knowledge Quality

- Shared findings have source links 90%+ of the time
- Peer validation catches at least 1 factual error per 10-round session (proving the challenge mechanism works)
- Knowledge audit coverage score improves session-over-session for repeated topics

### Session Efficiency

- 80%+ of success criteria met within max_rounds for practitioner-depth goals
- Diminishing-returns termination triggers before max_rounds in 50%+ of sessions (meaning the system self-stops when done, not when time runs out)
- Gap map correctly identifies yellow (teaching) opportunities — agent that "knows" produces relevant content 80%+ of the time

### System Health

- Per-agent memory stays under budget (memory_max_count) after harvest
- Hybrid search returns relevant results for 90%+ of knowledge audit queries
- Arena sessions complete without doom-loop detection triggering (each agent advances the conversation, not loops)

### Eval Coverage

These metrics require dedicated eval scripts (extending the existing `evals/` suite):
- **Factual challenge eval:** inject known-incorrect claims into an arena session; measure whether peer validation catches them
- **Coverage progression eval:** run two arena sessions on the same topic; measure knowledge audit coverage delta
- **Termination eval:** run sessions at varying depths; measure what fraction self-terminate via diminishing returns vs max_rounds
- **Doom loop eval:** extend existing `eval_safety_doom_loop.py` to cover arena mode (two agents, alternating turns)

## 20. Cultivation Risk Assessment

| Risk | Phases | Severity | Mitigation |
|------|--------|----------|------------|
| Memory bloat from arena sessions | M, N, Q | HIGH | Per-agent budget enforcement, post-session harvest with aggressive consolidation, standard decay applies |
| Agents agreeing on wrong information (confirmation bias) | N | HIGH | Challenge round type explicitly seeks counter-evidence; confidence=contested flag preserves disagreement rather than forcing false consensus |
| Goal drift in long arena sessions | O | MEDIUM | Gap map constrains each round; diminishing-returns termination at 3 rounds with no new findings |
| Token cost of dual-agent sessions | M | MEDIUM | Budget limits in goal spec (max_rounds, max_web_searches); depth=curious for quick exploration |
| Shared findings contradicting each other over time | N, Q | MEDIUM | Peer validation prevents within-session contradictions; cross-session contradiction detection is a Phase R capability |
| Embedding cost compounding across agents | P | MEDIUM | Local-first embeddings (embeddinggemma-300M); cache seeding during reindex (openclaw pattern); batch processing for remote providers |
| Long sessions hitting context limits before Phase D ships | M | MEDIUM | Arena controller implements per-agent history summarization every N rounds (arena-specific compaction) as part of Phase M scope |
| Agents developing unhelpful models of each other | L | LOW | Peer model memories decay normally; no protected flag on peer-model memories |
| Arena mode bypassing safety controls | M | LOW | Safety rules are in system prompt, not arena addendum; doom loop detection, turn limits, and approval gates remain active per-agent |

---

# Part IV: Memory System Evolution

## 21. Unified Evolution Path

Co's current memory system (grep + frontmatter over markdown files) works for <200 items. Both single-agent search quality and multi-agent cultivation need better retrieval. The evolution path is phased, with each stage independently valuable and backward-compatible.

**Authoritative design:** `TODO-sqlite-tag-fts-sem-search-for-knowledge.md` contains the detailed schema, API, acceptance criteria, and config for the unified `KnowledgeIndex`. This section provides the strategic overview; the TODO doc is the implementation spec.

### Stage 1: Directory-Scoped Memories (Phase L)

**What:** Add `memory_dir` to `CoDeps`. Each agent gets its own memory directory. Shared findings get their own directory.

**Changes:**
- `CoDeps.memory_dir: Path` — new field, defaults to `.co-cli/knowledge/memories/` (preserving current behavior for single-agent mode)
- Memory tools (`save_memory`, `recall_memory`, `list_memories`) read from `ctx.deps.memory_dir` instead of hardcoded path
- Arena controller creates two CoDeps instances with different `memory_dir` values

**Scale:** Handles ~200 memories per agent + ~100 shared findings. Sufficient for early cultivation sessions.

### Stage 2: FTS5 Ranked Search (Phase P, part 1)

**What:** SQLite full-text search index over memory content. Markdown files remain source of truth; SQLite is a read index that rebuilds on change.

**Implementation:** `KnowledgeIndex` class from `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`, Phase 1. Single `search.db` at `~/.local/share/co-cli/search.db`. All sources (memory, obsidian, drive, articles) write to the same `docs` table. An `agent` column (nullable) scopes per-agent queries in multi-agent mode.

**Schema addition for multi-agent:**
```sql
ALTER TABLE docs ADD COLUMN agent TEXT;
-- NULL for single-agent and shared findings
-- 'finch' or 'jeff' for per-agent memories
-- Scoped query: WHERE source = 'memory' AND agent = 'finch'
-- Cross-agent query: WHERE source = 'memory' (all agents)
```

**Grounding (openclaw):** Openclaw's `chunks_fts` virtual table uses FTS5 with BM25 ranking. Keyword search catches exact term matches that embeddings miss. This is the first half of hybrid search.

**Scale:** Handles ~2,000 memories per agent with sub-second search.

### Stage 3: Hybrid Search — FTS5 + Embeddings (Phase P, part 2)

**What:** Add vector embeddings alongside FTS5. Weighted merge of keyword and semantic results.

**Implementation:** `KnowledgeIndex` Phase 2 from the TODO doc. Embedding provider (local-first: embeddinggemma-300M via Ollama; API fallback: Gemini). `sqlite-vec` extension for cosine similarity, with in-memory fallback if extension unavailable. Embedding cache with hash-based dedup. Cache seeding during reindex (openclaw pattern).

**Merge algorithm:**
```
score = (vec_score * vector_weight) + (bm25_score * text_weight)
where bm25_score = 1 / (1 + bm25_rank)
default weights: vector=0.7, text=0.3
```

**Grounding (openclaw):** Openclaw's hybrid search uses configurable weights — default `0.7 * vector_score + 0.3 * bm25_score`. The weighted merge covers both retrieval modes: keywords catch exact terms, vectors catch semantic intent. Openclaw normalizes BM25 rank to a 0-1 score via `1/(1 + bm25_rank)` before merging. Graceful degradation: vector extension optional → keyword-only; FTS optional → vector-only.

**Scale:** Handles ~10,000+ memories per agent. Semantic search enables the knowledge audit to assess coverage even when terminology doesn't match exactly.

### Stage 4: Relationship Extraction (Phase R)

**What:** Extract entity-relationship triples from memories and findings. Enables multi-hop reasoning and knowledge graph visualization.

**Grounding (Mem0):** Mem0's knowledge graph uses LLM-powered entity extraction + relation generation, with a conflict detector that flags overlapping or contradictory nodes/edges. An update resolver decides whether to add, merge, invalidate, or skip. This produced 26% accuracy improvement over baselines.

**Grounding (A-Mem):** A-Mem's Zettelkasten approach treats each memory as a structured note with contextual descriptions, keywords, and tags. New memories trigger updates to existing memories' contextual representations. This doubles performance on complex multi-hop reasoning.

**Changes:**
- Entity and relationship extraction on memory save (LLM call, can be batched)
- Relationship storage in SQLite (simple edge table: `source_id, target_id, relation_label`)
- Multi-hop traversal in recall: if memory A references entity X, and memory B also references entity X, B surfaces as a related result
- Knowledge graph topology analysis for gap detection: isolated clusters indicate knowledge islands, missing bridges indicate potential gaps

### Stage 5: Cross-Agent Knowledge Graph (Phase R, part 2)

**What:** Unified graph spanning both agents' private memories and shared findings. Enables questions like "what does Finch know that Jeff doesn't about topic X?"

**Changes:**
- Agent-scoped nodes: each entity is tagged with its source agent
- Cross-agent edges: shared findings create edges between agents' knowledge subgraphs
- Differential query: "entities known by agent A but not agent B for topic X"
- This is the data structure that makes the gap map computable without LLM calls

---

# Part V: Learnings from Reference Systems

Peer CLI adoption decisions (what co takes, what it declines, and why) are consolidated in [`TAKEAWAY-converged-adoptions.md`](TAKEAWAY-converged-adoptions.md). This section covers research that informed cultivation design specifically — topics not covered by the CLI peer analysis.

## 22. Cultivation-Specific Research

### 22.1 Openclaw — Memory Architecture

Openclaw's `src/memory/` is the most production-ready hybrid search implementation in the reference set. Patterns adopted for co's search evolution (Stages 2-3): FTS5 BM25 + sqlite-vec cosine with weighted merge (0.7 vector / 0.3 text), hash-based embedding cache with LRU eviction, cache seeding during reindex, graceful degradation (vector unavailable → keyword-only), and atomic swap for safe reindex. Per-agent isolation via agent-scoped index key informs Phase L's `agent` column design.

**What we do NOT take:** Openclaw's QMD (query/memory daemon) external backend. Co stays local-first.

### 22.2 Multi-Agent Debate Research

| Finding | Source | Implication for co |
|---------|--------|-------------------|
| Heterogeneous teams outperform homogeneous by 4-6% | A-HMAD (2025) | Finch/Jeff's different epistemic stances are the value driver, not model diversity |
| MAD does not consistently beat single-agent with equal compute | "Can LLM Agents Really Debate?" (2025) | Keep debate protocol lightweight; value comes from epistemic stance difference |
| Metacognition (r=0.744) is the strongest performance predictor | arXiv 2512.04988 | Knowledge audit is the highest-value new capability |

### 22.3 Self-Learning Agent Research

| Pattern | Source | Co adoption |
|---------|--------|-------------|
| **Automatic curriculum** | Voyager (MineDojo/NVIDIA) | Gap map focuses debate on the knowledge frontier |
| **Verbal self-reflection** | Reflexion | Private memories include metacognitive reflections |
| **Controller + Executor + Designer** | MemSkill (2025) | Maps to arena controller + agents + curriculum system |

### 22.4 Persistent Memory Research

| System | Key insight for co |
|--------|-------------------|
| **Mem0** | Conflict detection + resolution before writing to knowledge store → peer validation protocol |
| **A-Mem** | Zettelkasten-style notes with dynamic cross-linking → memory frontmatter + Phase R knowledge graph |
| **Letta/MemGPT** | Two-tier memory (working context + archive) with self-editing → context governance + memory tier |

---

# Part VI: Reference

## 23. Boundaries and Non-Goals

1. No default-on autonomous background execution. Arena sessions are user-initiated and observable; they are not autonomous learning.
2. No implicit sensitive-memory ingestion.
3. No broad browser/desktop automation outside isolated, explicitly approved runs.
4. No replacement of text UX as the primary control surface.
5. No OS-level sandbox enforcement (approval-based model is co's chosen tradeoff).
6. No wake word, voice cloning, or telephony.

## 24. Parallel Workstreams

All phases (A-S) sequenced by dependency, with concurrent streams:

```
Stream 1 (Safety):        A ──────────────── C ── J
                            \                 \
Stream 2 (Capability):       B (read-only) ── B (write) ── F
                                                             \
Stream 3 (UX):               D ── E ── H                      G
                                   \
Stream 4 (Intelligence):            I
                                     \
Stream 5 (Cultivation):  L ── M ── N ── O ── Q ── S
                                              \
Stream 6 (Search):             P ──────────────── R
```

**Single-agent streams (Part II):**
- **A** (agentic loop safety) has no dependencies — start immediately
- **B read-only** (list_directory, read_file) can start in parallel with A
- **B write** (write_file, edit_file) depends on **C** (shell hardening)
- **D** (compaction) can start after A ships (uses typed loop returns)
- **F** (undo/revert) depends on B write
- **E** (background) benefits from A + C but is independently implementable
- **I** (sub-agents) gated on A's prompt improvements test results

**Cultivation streams (Part III):**
- **L** (agent isolation) has no single-agent dependencies — can start immediately
- **M** (arena controller) depends on L + A (safety must be active per agent)
- **N** (peer validation) depends on M
- **O** (curriculum) depends on N
- **P** (hybrid search) depends on L, independent of M-O — can run in parallel
- **Q** (harvest) depends on N
- **R** (knowledge graph) depends on P
- **S** (multi-pair) depends on L-Q being stable

**Cross-stream dependencies:**
- Phase H (session persistence) benefits the arena — session transcripts should use the same persistence layer
- Phase I and Phase M share the multi-`run_turn()` orchestration pattern — whichever ships first defines it
- Phase D (context compaction) is essential for long arena sessions; Phase M includes arena-specific compaction as a stopgap if D hasn't shipped
- Phase E (background execution) enables `co research --background` for long expert-depth sessions
- Phase F (undo/revert) enables reverting cultivation session findings
- Phase G (skills system) could host `co research` as a built-in skill rather than a hardcoded command

## 25. Design & TODO Doc Index

All paths verified against `docs/` contents.

### Design Documents (architecture, kept in sync with code)

| Doc | Description |
|-----|-------------|
| `DESIGN-core.md` | System overview, agent loop: factory, CoDeps, bootstrap, session lifecycle, skills, orchestration, streaming, four-tier approval, cross-cutting concerns |
| `DESIGN-llm-models.md` | LLM model configuration (Gemini, Ollama) + Ollama local setup |
| `DESIGN-logging-and-tracking.md` | Telemetry architecture, SQLite schema, viewers, real-time tail |
| `DESIGN-theming-ascii.md` | Theming, ASCII art banner, display helpers |
| `DESIGN-tools.md` | Native tools: shell (four-tier approval + persistent approvals), memory, Obsidian, Google, web, capabilities, bundled skills |
| `DESIGN-knowledge.md` | Knowledge system: flat store, FTS5/hybrid search, tool surface, memory lifecycle (signal detection, precision edits, dedup, decay) |
| `DESIGN-mcp-client.md` | MCP client: external tool servers via stdio transport |
| `DESIGN-prompt-design.md` | Agentic loop + prompting: run_turn, four-tier approval re-entry, tool preamble, safety policy, prompt composition, context governance, history processors, compaction |
| `DESIGN-personality.md` | Prompt & personality system: static/per-turn split, 4 file-driven roles, 5 traits, structural delivery, reasoning_depth override |

### TODO Documents (remaining work)

| Doc | Description | Related Phase |
|-----|-------------|---------------|
| `TODO-subagent-delegation.md` | Remaining loop/prompt work: sub-agent delegation, confidence-scored advisory outputs | I |
| `TODO-background-execution.md` | Background task execution for long-running operations | E |
| `TODO-voice.md` | Voice-to-voice round trip | K |
| `TODO-sqlite-tag-fts-sem-search-for-knowledge.md` | All knowledge system work: flat migration, articles + tools, multimodal assets, learn mode, FTS5 + semantic search | P (+ Stage 2-3 of memory evolution) |

Recommended cross-TODO sequence (single-agent track):
1. `TODO-background-execution.md`
2. `TODO-subagent-delegation.md` — sub-agent delegation (P1)
3. `TODO-sqlite-tag-fts-sem-search-for-knowledge.md`
4. `TODO-subagent-delegation.md` — confidence-scored advisory outputs (P2)

### Research & Review Documents

| Doc | Description |
|-----|-------------|
| `TAKEAWAY-converged-adoptions.md` | Converged peer research: adopted/declined/deferred patterns with rationale |
| `RESEARCH-obsidian-lakehouse-2026-best-practices.md` | Obsidian lakehouse best practices |

## 26. Peer System Reference

Five peer systems studied for architecture patterns, convergent best practices, and anti-patterns:

| System | Language | Key strengths relevant to co |
|--------|----------|------------------------------|
| **Claude Code** (Anthropic) | TypeScript | Deepest sub-agent architecture (3 built-in + custom), unified skills/hooks/plugins, /rewind with file snapshots, auto-compaction at 95%, background tasks via Ctrl+B |
| **Codex CLI** (OpenAI) | Rust | Strongest sandboxing (Seatbelt/seccomp/Landlock), server-side compaction, AGENTS.md, CLI-as-MCP-server pattern |
| **Gemini CLI** (Google) | TypeScript | Best rewind system (conversation + files), Agent Skills enabled by default, event-driven scheduler, 1M token context, /introspect for prompt debugging |
| **Aider** | Python | Pioneer of repo-aware coding, Repository Map (tree-sitter AST + PageRank), proven reflection loop (35k+ users), simplest security model (confirm_ask for everything) |
| **OpenCode** | Go | Clean typed loop returns, 90% compaction threshold, doom loop detection (threshold 3), multi-provider model switching |

Additional reference for memory architecture:

| System | Language | Key strengths relevant to cultivation |
|--------|----------|--------------------------------------|
| **Openclaw** | TypeScript | Production hybrid search: FTS5 + sqlite-vec + embedding cache, weighted merge, multi-provider embeddings, chunking with overlap, per-agent isolation |

## 27. Glossary

| Term | Definition |
|------|-----------|
| **Arena** | The orchestration environment where two agents take alternating turns |
| **Arena controller** | Lightweight coordinator that manages turn order, round types, and termination — does not use LLM reasoning |
| **Cultivation pair** | Two agents with complementary epistemic stances configured for mutual knowledge building |
| **Curriculum** | Goal specification + gap map that constrains what agents research |
| **Gap map** | Structured diff of two agents' knowledge assessments against success criteria |
| **Harvest** | Post-session consolidation: deduplicate, merge, archive, and enforce memory budgets |
| **Knowledge audit** | Two-step agent workflow: generate topic outline (LLM reasoning) + match memories to outline (tool) |
| **Mutual cultivation** | Structured multi-agent knowledge building through debate and research |
| **Peer validation** | Protocol requiring both agents to agree (or explicitly contest) before findings enter shared storage |
| **Round type** | Category of debate turn: teach, research, challenge, verify, synthesize |
| **Shared finding** | A factual claim validated by both agents, stored with evidence and confidence level |

## 28. Research Sources

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

### Multi-Agent Debate & Collaboration
9. A-HMAD — Adaptive Heterogeneous Multi-Agent Debate (Springer, 2025)
10. "Can LLM Agents Really Debate?" (arXiv 2511.07784, 2025) — critical finding that MAD does not consistently outperform single-agent
11. AutoGen v0.4 — actor model architecture (Microsoft Research, 2025)
12. LangGraph multi-agent supervisor pattern (LangChain, 2025)
13. CrewAI collaboration + delegation (2025)
14. OpenAI Agents SDK — successor to Swarm (March 2025)

### Self-Learning & Self-Evolving Agents
15. Voyager — open-ended embodied agent with automatic curriculum + skill library (MineDojo/NVIDIA)
16. Reflexion — verbal self-reflection for LLM agents (LangGraph tutorial)
17. "Survey of Self-Evolving Agents" (arXiv 2507.21046, 2025)
18. "Comprehensive Survey of Self-Evolving AI Agents" (arXiv 2508.07407, 2025)
19. MemSkill — learning and evolving memory skills with controller/executor/designer (arXiv 2602.02474, 2025)
20. MemRL — self-evolving agents via non-parametric reinforcement learning (arXiv 2601.03192, 2025)

### Persistent Memory Systems
21. Mem0 — graph memory with conflict detection + resolution (arXiv 2504.19413)
22. A-Mem — Zettelkasten-based agentic memory (NeurIPS 2025, arXiv 2502.12110)
23. Zep/Graphiti — temporal knowledge graph with bi-temporal model (arXiv 2501.13956)
24. Letta/MemGPT — OS-inspired two-tier memory with self-editing (arXiv 2310.08560)

### Metacognition & Self-Assessment
25. "Strategic Self-Improvement for Competitive Agents" (arXiv 2512.04988) — metacognition r=0.744
26. "Truly Self-Improving Agents Require Intrinsic Metacognitive Learning" (arXiv 2506.05109)
27. MUSE — competence-aware AI agents with metacognition (arXiv 2411.13537)
28. "LLMs Lack Essential Metacognition" (Nature Communications, 2024)

### Reference Implementations
29. Openclaw `src/memory/` — production hybrid search: FTS5 + sqlite-vec + embedding cache, weighted merge, multi-provider embeddings, chunking with overlap
30. Co-cli memory system — `co_cli/tools/memory.py`, `CLAUDE.md` (Knowledge System section), `DESIGN-personality.md`
