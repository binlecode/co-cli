# ROADMAP: Co Evolution (Frontier-Grounded)

**Status**: Phases 1a-1d Complete ✅ | Phases 2a-2c Ready for Implementation ⏳ | Phase 2.5 (S0+S1) + Phase 1e-FOLLOW-ON Deferred 📅

This is the unified strategic + tactical roadmap for co-cli evolution: from capable tool executor to personal companion for knowledge work. Part I provides strategic context (why we're building, where the frontier is, where co is heading). Part II provides tactical execution detail (what to build, when to build it, how to implement).

---

# Part I: Strategic Context

## 1. Vision

`co-cli` should evolve from a capable tool-calling assistant into a personal companion for knowledge work, while preserving its identity:

1. Local-first runtime and storage.
2. Approval-first for side effects.
3. Incremental, testable delivery.

The target product shape is text-first, automation-capable, and safe by default.

### 1.1 The "Finch" Vision

Co aspires to be the CLI version of the robot companion from "Finch" (2021): a helpful assistant that learns, develops personality, and forms lasting relationships with its user.

**Core traits:**
- **Helpful:** Completes tasks efficiently and accurately
- **Curious:** Asks clarifying questions, seeks to understand context
- **Adaptive:** Learns user preferences and patterns over time
- **Empathetic:** Understands emotional context, adjusts tone appropriately
- **Loyal:** Remembers past interactions, maintains continuity across sessions
- **Growing:** Evolves from simple command executor to thoughtful partner

**Five pillars of co's character:**
1. **Soul:** Identity, personality, interaction style (selected by user from templates)
2. **Internal Knowledge:** Learned context, patterns, user habits (persists across sessions)
3. **External Knowledge:** Tools for accessing data (Obsidian, web, Google, Slack, MCP)
4. **Emotion:** Tone, empathy, context-aware communication (adapts to situation)
5. **Habit:** Workflow preferences, approval patterns, personalization (user-configurable)

Unlike a pure tool executor, co should anticipate needs, remember preferences, and develop a working relationship with its user over weeks and months of use.

## 2. Frontier Snapshot (as of February 9, 2026)

The current frontier is no longer "single-turn tool calls." It is end-to-end agents with planning, tool orchestration, asynchronous execution, and explicit safety controls.

### 2.1 Unified agent surfaces (research + tools + action)

1. OpenAI launched `ChatGPT agent` on July 17, 2025, combining capabilities from Operator and deep research into a single mode with connectors and tool execution.
2. Anthropic launched Claude 4 on May 22, 2025, with extended thinking + tool use, parallel tools, and stronger long-horizon agent behavior.
3. Google announced Gemini app `Agent Mode`, Project Mariner, and Jules at I/O 2025, converging on the same "plan + act + user oversight" interaction pattern.

Implication for Co:

1. Keep one primary loop: observe -> plan -> execute tools -> ask approval when needed -> summarize with citations.
2. Avoid fragmented "feature islands" (separate research mode, separate automation mode, separate planning mode).

### 2.2 Asynchronous, long-running execution is now baseline

1. OpenAI's agent stack includes background execution modes for longer tasks.
2. Anthropic's Claude Code supports background tasks (for example via GitHub Actions integration).
3. Google Jules moved from beta to broad availability in 2025 and added proactive/scheduled workflows.

Implication for Co:

1. Add resumable background runs as a first-class primitive.
2. Treat foreground chat as control plane and background jobs as execution plane.

### 2.3 Protocol convergence: MCP now matters

1. OpenAI added remote MCP support in the Responses API tool stack.
2. Anthropic added MCP connector capabilities and a broader agent-tooling surface (skills, memory tool, tool search).
3. Google announced A2A protocol support and MCP support in Gemini API/SDK tooling.

Implication for Co:

1. MCP client support should move from TODO to core roadmap.
2. Keep native tools for critical local/safety paths AND high-value, frequently-used platforms (Google, Slack, Obsidian, Web).
3. Use MCP to expand breadth beyond what co-cli maintainers can realistically maintain:
   - **Long tail** (Discord, Notion, Jira, Postgres, 100+ other services)
   - **User-specific** (company APIs, personal databases, custom workflows)
   - **Specialized** (vector search, PDF processing, audio transcription)
4. **Native tools = core senses**; **MCP = extended senses**. Co needs both to achieve the "Finch" companion vision.

### 2.4 Safety posture converges on human-in-the-loop for consequential actions

1. OpenAI agent mode requests confirmation before high-impact actions.
2. Anthropic computer-use guidance explicitly recommends VM isolation, domain restrictions, and human confirmation for meaningful real-world consequences.
3. Google Project Mariner UX keeps users in control, with stop/takeover affordances.

Implication for Co:

1. Double down on approval-first rather than diluting it for convenience.
2. Keep strict network/sandbox policies and explicit user control boundaries.

## 3. Co CLI Ground Truth (Current State)

Based on the current repository:

1. Web intelligence already exists: `web_search` and `web_fetch` are implemented and wired into the agent.
2. Web fetch already includes domain policy + private-network blocking + redirect revalidation.
3. Google/Slack/Obsidian/Shell tools exist; this is already a multi-surface assistant.
4. Explicit persistent personal memory tools (`save_memory`, `recall_memory`, `list_memories`) are implemented and shipped (Phase 1c ✅).
5. MCP client support is planned but not yet shipped (`docs/TODO-co-evolution-phase2a-mcp-client.md`).
6. No built-in background job runner for long agent tasks yet.
7. No voice runtime yet.

This means Co has strong foundations for core platforms. The largest gaps are MCP extensibility (enables long tail + user-specific integrations beyond native tools) and async execution.

## 4. Strategic Roadmap

*This strategic roadmap outlines the high-level phases. See Part II for detailed tactical execution.*

### Phase 1: Consolidate the core operator loop

**Core capabilities (task execution):**

1. Add explicit local memory tools with manual writes only (no hidden ingestion).
2. Add a planner/result contract that always returns:
   - planned steps,
   - executed tools,
   - citations/evidence links,
   - pending approvals or blocked actions.
3. Add task checkpoints so a turn can pause/resume safely.

**Identity layer (personality foundation):**

4. Add personality system with pre-set templates:
   - Fixed set of personality options (professional, friendly, terse, inquisitive)
   - User-selectable via config or runtime command
   - Personality injected at prompt assembly time
   - Templates are bounded config space (explicit, not implicit)
   - Starting point for evolution toward "Finch"-like companion

5. Design internal knowledge system (distinct from external knowledge):
   - **External knowledge:** Tools (Obsidian, web_search, Google, Slack, MCP servers)
   - **Internal knowledge:** Co's learned context, patterns, user preferences
   - Boundary: External = queried on demand, Internal = always available in context
   - Storage: `.co-cli/knowledge/` directory for persistent learned knowledge
   - Access: Agent SDK memory handling for session memory, file-based for cross-session

Exit criteria:

1. Users can run multi-step tasks with clear traceability and deterministic approval points.
2. Memory behavior is explainable and auditable.
3. Users can select personality that shapes co's interaction style.
4. Internal knowledge persists across sessions without manual memory tool calls.

### Phase 2: Ship MCP + background execution + user preferences

**Extensibility:**

1. Implement MCP client Phase 1 (stdio) from `docs/TODO-co-evolution-phase2a-mcp-client.md`.
2. Add background job execution with:
   - explicit start command,
   - status inspection,
   - cancellation,
   - persisted logs/traces.
3. Require approval policy inheritance for every MCP tool call.

**Personalization:**

4. Add user workflow preferences system:
   - **Need:** One-size-fits-all preference won't work for different job contexts
   - **Design approach:** Research peer systems (Codex, Gemini CLI, Claude Code, Aider) + 2026 best practices
   - **Implementation:** Simple, explicit templating solution (code or LLM, explicit > implicit)
   - **Injection point:** Prompt assembly time, after personality, before project instructions
   - **Settings examples:**
     - `auto_approve_tools`: list of trusted tools
     - `verbosity_level`: minimal/normal/verbose
     - `proactive_suggestions`: whether to offer unsolicited ideas
     - `output_format_preferences`: preferred formats by data type
   - **Storage:** `.co-cli/preferences.json` with template selection or explicit overrides

Exit criteria:

1. Co can run long tasks without blocking the chat loop.
2. External tools are extensible via MCP without weakening approvals.
3. Users can configure workflow preferences that adapt co's behavior to their work style.

### Phase 3: Selective autonomy and richer I/O

1. Add optional scheduling for approved recurring tasks.
2. Pilot controlled computer-use style actions only in isolated environments.
3. Add voice-to-voice round trip as an overlay on the text loop (see §4.1).

Exit criteria:

1. Unattended tasks are opt-in, bounded, and reversible.
2. Voice/computer-use do not bypass approval or audit trails.

### 4.1 Voice-to-Voice Round Trip (Phase 3)

**Architecture**: Cascading pipeline (STT → LLM → TTS) as overlay on existing text loop. Voice feeds transcribed text to `run_turn()`, synthesizes text response to audio. No changes to agent, tools, or approval flow.

**Components** (local-first): Silero VAD, faster-whisper (STT), Kokoro-82M (TTS), sounddevice (I/O). Total ~500MB models, <800ms latency target.

**Activation**: Push-to-talk only (`co chat --voice` or `/voice`). Continuous listening deferred (requires echo cancellation).

**Key Features**: Streaming at all stages, barge-in/interruption (<200ms), silence-based turn detection, OTel logging.

**Boundaries**: No wake word, no voice cloning, no telephony, no speech-to-speech. Text remains primary — voice is convenience overlay.

**Status**: Research complete, implementation deferred until Phase 2c ships. Design will be refreshed before execution to validate component choices against 2026+ frontier.

**Full Design**: See `docs/TODO-co-evolution-phase3-voice.md` for detailed architecture, component rationale, latency analysis, and external research sources.

## 5. Boundaries and Non-Goals (Near Term)

1. No default-on autonomous background execution.
2. No implicit sensitive-memory ingestion.
3. No broad browser/desktop automation outside isolated, explicitly approved runs.
4. No replacement of text UX as the primary control surface.

## 6. Principle

Adopt frontier patterns where they improve outcomes, but keep Co's design contract intact:

1. Local-first data/control.
2. Approval-first side effects.
3. Tooling that remains composable, inspectable, and testable.

---

# Part II: Tactical Execution

## Phase Status Overview

| Phase | Name | Status | Effort | Documentation | Priority |
|-------|------|--------|--------|---------------|----------|
| **1a** | Model Conditionals | ✅ COMPLETE | - | DESIGN-co-evolution.md | - |
| **1b** | Personality Templates | ✅ COMPLETE | - | DESIGN-co-evolution.md | - |
| **1c** | Internal Knowledge | ✅ COMPLETE | 8-10h | TODO-co-evolution-phase1c-COMPLETE.md | - |
| **1d** | Prompt Improvements | ✅ COMPLETE | 3-4h | TODO-co-evolution-phase1d-COMPLETE.md | - |
| **1e-FOLLOW-ON** | Portable Identity | 📅 DEFERRED | 9h | TODO-co-evolution-phase1e-FOLLOW-ON.md | LOW |
| **2a** | MCP Client (stdio) | 📝 DOCUMENTED | 6-8h | TODO-co-evolution-phase2a-mcp-client.md | HIGH |
| **2b** | User Preferences | 📝 DOCUMENTED | 10-12h | TODO-co-evolution-phase2b.md | MEDIUM |
| **2c** | Background Execution | 📝 DOCUMENTED | 10-12h | TODO-co-evolution-phase2c-background-execution.md | MEDIUM |
| **2.5** | Shell Security (S0+S1) | 📅 DEFERRED | 6-9d | TODO-co-evolution-phase2.5-critical-tools.md | HIGH |
| **2d** | File Tools (C1) | 📅 DEFERRED | 3-4h | TODO-co-evolution-phase2.5-critical-tools.md | LOW |

**Total Remaining Work (Active)**: 26-32 hours (4 days)
**Deferred Work (Phase 2.5+ and follow-ons)**: 6-9 days + 12-13 hours

---

## Architecture Review (2026-02-10): Deferral Decision

Before proceeding with Phase 1e, we conducted a comprehensive architecture review to assess if shell security issues identified in `TODO-co-evolution-phase2.5-critical-tools.md` (Phase S0) represent fundamental architectural problems requiring large-scale refactoring.

### Review Findings: ✅ **Architecture is Fundamentally Sound (9.9/10)**

**Three-part deep dive**:
1. **Tool Registration**: 9.9/10 - Centralized, zero global state, clear separation of side-effectful vs read-only
2. **Approval System**: 9.8/10 - Unified system, no LLM bypass paths, robust interrupt handling
3. **Tool Contracts**: 9.9/10 - Uniform signatures, consistent return types, minimal friction for new tools

**Key Conclusions**:
- ✅ No architectural debt found - system is production-ready
- ✅ S0's concerns are **policy gaps**, not architecture flaws
- ✅ The `!cmd` bypass is intentional (escape hatch), not a bug
- ✅ Adding Phase 1e/2a tools poses no structural risk

### Deferral Rationale

**Why defer S0 (Shell Security) to Phase 2.5?**
1. **No incidents** - `!` bypass hasn't caused problems in practice
2. **Policy work** - S0 is policy refinement, not architecture repair
3. **User value waiting** - Phase 2a (MCP Client) and 2b (User Preferences) deliver visible benefits
4. **No compounding risk** - Tool architecture is solid, expansion is safe

**Why defer Phase 1e (Portable Identity) to follow-on?**
1. **Not core logic** - Portability is polish (export/import/sync), not essential functionality
2. **Let Phase 1c stabilize** - Knowledge system just shipped, needs production validation first
3. **Symlinks work today** - Users can already achieve portability via `ln -s ~/Dropbox/co-knowledge ~/.config/co-cli/knowledge`
4. **Co should have a soul before making it portable** - First use knowledge system in production, then worry about portability

**When to execute Phase 2.5 (S0+S1)?**
- After Phase 2c ships (background execution complete)
- Before Phase 3 expansion (next major capability layer)
- Immediately if incidents occur

**When to execute Phase 1e-FOLLOW-ON?**
- After Phase 1c knowledge system stabilizes in production
- When users request export/import/sync features
- No earlier than Phase 3+ timeframe

### Revised Sequence

```
Phase 2a (MCP Client, 6-8h) → Phase 2b (User Preferences, 10-12h) → Phase 2c (Background Execution, 10-12h)
  ↓
Phase 2.5: Shell Security Hardening (S0+S1, 6-9 days)
  ↓
Phase 2d: File Tools (C1, 3-4h)
  ↓
Phase 3+: Advanced capabilities
  ↓
Phase 1e-FOLLOW-ON: Portable Identity (9h) - when knowledge system stabilizes
```

**Reference**: Full architecture review findings in `/Users/binle/.claude/projects/-Users-binle-workspace-genai-co-cli/5d9ef30e-563b-46d3-9290-968c7b6db268.jsonl`

---

## Documentation Summary

### Phase 1c: Internal Knowledge System ✅ COMPLETE
**File**: `docs/TODO-co-evolution-phase1c-COMPLETE.md` (2,594 lines)
**Verification**: `docs/VERIFICATION-phase1c-demo-results.md`
**Design**: `docs/DESIGN-14-memory-lifecycle-system.md`

**Goal**: Load co's learned context from markdown files - facts about user, project patterns, learned preferences that persist across sessions.

**Delivered**:
- ✅ Markdown lakehouse pattern (files as source of truth)
- ✅ Always-loaded context (global + project, 10KB soft / 20KB hard limit)
- ✅ Three memory tools: `save_memory`, `recall_memory`, `list_memories`
- ✅ Grep-based search (Phase 1c MVP, <200 memories)
- ✅ 42 tests passing, 89% coverage
- ✅ Agent integration complete
- ✅ Prompt injection after personality, before instructions

**Success Criteria**: All 20+ checkboxes verified ✅

---

### Phase 1d: Prompt Improvements (Peer Learnings) ✅ COMPLETE
**File**: `docs/TODO-co-evolution-phase1d-COMPLETE.md` (1,500 lines)

**Goal**: Apply 5 high-impact techniques from peer system analysis to improve system.md without adding complexity.

**Delivered**:
1. ✅ **System Reminder** (Aider pattern) - Recency bias exploitation
2. ✅ **Escape Hatches** (Codex pattern) - Prevent stuck states
3. ✅ **Contrast Examples** (Codex pattern) - Good vs bad responses
4. ✅ **Model Quirk Counter-Steering** (Aider pattern) - Database-driven
5. ✅ **Commentary in Examples** (Claude Code pattern) - Teach principles

**Impact**: Improved small model compliance, reduced stuck states

**Success Criteria**: 5 new tests, behavioral validation ✅

---

### Phase 2a: MCP Client Integration
**File**: `docs/TODO-co-evolution-phase2a-mcp-client.md` (1,850 lines) ✅

**Goal**: Integrate MCP servers as external tool sources via stdio transport.

#### Why MCP? (Value Proposition Clarification)

**Co already has excellent native tool coverage**: 21 tools across 6 platforms (Google Suite, Slack, Obsidian, Web, Memory, Shell).

**MCP doesn't replace native tools — it extends them beyond what's practical to maintain natively.**

| Category | Native Tools (Co Built-in) | MCP Unlocks |
|----------|----------------------------|-------------|
| **Communication** | Gmail, Slack | Discord, Teams, WhatsApp, Telegram, IRC, Matrix |
| **Files** | Google Drive | Dropbox, OneDrive, Box, S3, MinIO, Azure Blob |
| **Tasks** | None | Jira, Linear, Asana, Trello, GitHub Issues, GitLab Issues |
| **Notes** | Obsidian | Notion, Roam, LogSeq, Evernote, Bear, Confluence |
| **Data** | None | PostgreSQL, MySQL, MongoDB, Redis, Elasticsearch |
| **Specialized** | None | Vector DBs, PDF processing, audio transcription, LSP servers |

**Three key benefits**:
1. **Long tail** — 100+ MCP servers exist, community-maintained
2. **User-specific** — Company APIs, personal databases, custom workflows
3. **Maintenance shift** — Co-cli maintainers don't write/maintain integration code

**When to add native vs MCP**:
- **Native**: Top 3 user requests, stable API, complex OAuth, tight integration with co's internal state
- **MCP**: Long tail, niche, user-specific, evolving APIs, simple auth

**Key Features**:
- Config schema: `mcp_servers` in settings.json
- Dynamic tool discovery via MCP protocol
- Async lifecycle management (`async with agent`)
- Automatic tool name collision handling (prefixing)
- Approval inheritance (MCP tools = native tools)
- 13+ functional tests

**Success Criteria**: 25 checkboxes across functional, approval, config, status, testing, docs

---

### Phase 2b: User Preferences System
**Files**:
- `docs/TODO-co-evolution-phase2b.md` (2,000 lines) ✅

**Note**: Original research (`RESEARCH-user-preferences.md`) superseded by co's knowledge system approach - preferences learned dynamically through interaction rather than static configuration files.

**Goal**: Workflow preferences that adapt co's behavior to user's work style, separate from personality.

**Research Findings**:
- Analyzed 4 peer systems (Codex, Gemini CLI, Claude Code, Aider)
- 10 MVP preferences identified (approval, sandbox, output, UI, telemetry, updates)
- JSON with comments storage pattern
- Hierarchical precedence model

**Key Features**:
- `UserPreferences` dataclass with 10 core preferences
- Conflict resolution: command > preference > personality > base
- Runtime overrides: `/verbose`, `/terse`, `/explain`, `/cautious`, `/yolo`
- Progressive disclosure (only show non-default)
- 15+ comprehensive tests

**Success Criteria**: 31 checkboxes (code, test, behavioral, quality)

---

### Phase 2c: Background Execution
**File**: `docs/TODO-co-evolution-phase2c-background-execution.md` (1,900 lines) ✅

**Goal**: Long-running tasks run in background without blocking chat. User can start, check status, cancel, and view results asynchronously.

**Use Cases**:
- Long test runs (5+ minutes)
- Large file processing (hundreds of files)
- Research tasks (codebase analysis)
- Batch operations (mass file updates)

**Key Features**:
- Task lifecycle: pending → running → completed/failed/cancelled
- Storage: `.co-cli/tasks/{task-id}.json` (metadata) + `.co-cli/tasks/{task-id}.log` (output)
- Slash commands: `/background`, `/tasks`, `/status`, `/cancel`
- Three agent tools: `start_background_task`, `check_task_status`, `cancel_task`
- Approval inheritance (pre-execution gate, no mid-execution prompts)
- OTel integration (trace linking)
- 25+ functional tests

**Success Criteria**: Phase-specific completion gates for storage, execution, commands, tools, integration

---

### Phase 2.5: Shell Security Hardening (S0+S1) 📅 DEFERRED
**File**: `docs/TODO-co-evolution-phase2.5-critical-tools.md` (S0: Shell Boundary Hardening, S1: Policy Engine Upgrade)

**Goal**: Harden shell/sandbox approval boundary and establish structured command-policy evaluation.

**Key Work**:
- **S0 (3-5 days)**: Remove policy mismatches, unify `!cmd` with approval system, define `sandbox_fallback` policy, tighten safe-command classification
- **S1 (3-4 days)**: Introduce policy table for shell decisions, parser-assisted command evaluation, explicit deny patterns

**Success Criteria**:
- ✅ No approval bypass for shell execution
- ✅ Fallback behavior explicit and tested
- ✅ Shell policy decisions deterministic and tested
- ✅ Unsandboxed risk state persistently visible

**Deferral Reason**: Policy refinement work, not architectural blocker. Execute after Phase 2c, before Phase 3 expansion.

---

### Phase 2d: File Tools (C1) 📅 DEFERRED
**File**: `docs/TODO-co-evolution-phase2.5-critical-tools.md` (Phase C1)

**Goal**: Stop overusing shell for standard read/write/edit/list operations.

**Tools**: `list_directory`, `read_file`, `write_file`, `edit_file`

**Security**: Path resolution bounded to workspace root, traversal/symlink escape blocked

**Success Criteria**:
- ✅ Default file workflows use file tools, not shell
- ✅ No path escape in functional tests

**Deferral Reason**: Not critical for current roadmap. Execute after Phase 2.5 (shell security) complete.

---

## Documentation Lifecycle Pattern

### Phase Completion Workflow

**Standard pattern for completing implementation phases:**

1. Execute work following TODO implementation guide
2. Create `-COMPLETE.md` file documenting outcomes, test results, time tracking
3. Delete TODO file once COMPLETE file is committed and verified
4. Move on to next phase

**Example (Phase 1a, 1b completed 2026-02-09):**
```
TODO-co-evolution-phase1a.md (66KB, 1,800 lines implementation guide)
  ↓ (work completed)
TODO-co-evolution-phase1a-COMPLETE.md (6KB, 200 lines summary)
  ↓ (cleanup)
DELETE TODO-co-evolution-phase1a.md (scaffolding no longer needed)
```

**File size impact:**
- Phase 1a TODO: 66KB → COMPLETE: 6KB (90% reduction)
- Phase 1b TODO: 13KB → COMPLETE: 10KB (23% reduction)
- **Rationale**: COMPLETE files capture all essential outcomes (what was delivered, test results, lessons learned, time tracking). TODO files are verbose implementation scaffolding only needed during execution.

**Exception**: If implementation guide has significant historical value (novel architecture, complex decisions), consider renaming to `IMPLEMENTATION-phase*.md` instead of deleting. Not typically needed for straightforward implementations.

### Anti-Pattern Note

**Observed naming issue**: Phase TODO files (1,500-2,000 lines) are actually full implementation guides with architecture, code specs, and tests — not just "work items" as CLAUDE.md convention suggests.

**Better naming for future phases** (post-Phase 2c):
- `SPEC-phase*.md` or `IMPLEMENTATION-phase*.md` (implementation guide)
- `TODO-phase*-items.md` (just the checklist/work items)
- `COMPLETE-phase*.md` (completion report)

**Current approach**: Keep `TODO-phase*.md` naming for consistency with existing phases 1c-2c.

---

## Implementation Sequence

### Recommended Order (Updated 2026-02-10)

1. ✅ **Phase 1d** (3-4 hours) - QUICK WIN - COMPLETE
   - Apply peer learnings immediately
   - Low risk, high impact
   - No dependencies

2. ✅ **Phase 1c** (8-10 hours) - FOUNDATIONAL - COMPLETE
   - Internal knowledge foundational for companion vision
   - Memory tools enable learning
   - Markdown lakehouse pattern
   - No dependencies

3. **Phase 2a** (6-8 hours) - ECOSYSTEM ENABLER - NEXT
   - MCP extensibility unlocks tool ecosystem
   - Architecture ready (tool registration is solid)
   - **Add security advisory**: Document `!` bypass, recommend explicit `sandbox_backend: docker`
   - No dependencies, ready to start immediately

4. **Phase 2b** (10-12 hours) - PERSONALIZATION
   - Research complete, ready to implement
   - Depends on 1c for learned preferences
   - Integrates with personality system (1b)

5. **Phase 2c** (10-12 hours) - ADVANCED UX
   - Most complex, goes last
   - Benefits from 2a completion (MCP tools in background)
   - Independent core implementation

6. **Phase 2.5** (6-9 days) - SHELL SECURITY HARDENING - DEFERRED
   - S0 (3-5 days): Shell boundary hardening, approval unification
   - S1 (3-4 days): Policy engine upgrade, parser-assisted evaluation
   - Execute after Phase 2c, before Phase 3 expansion
   - **Rationale**: Policy refinement, not architectural blocker

7. **Phase 2d** (3-4 hours) - FILE TOOLS - DEFERRED
   - Workspace file tools (list/read/write/edit)
   - Execute after Phase 2.5 (shell security complete)
   - Low priority for current roadmap

8. **Phase 1e-FOLLOW-ON** (9 hours) - PORTABILITY - DEFERRED (NON-CORE)
   - Identity separation (portable vs machine-local)
   - Export/import/sync commands
   - **Rationale**: Let Phase 1c knowledge system stabilize in production first
   - Execute when users request portability features (Phase 3+ timeframe)
   - Symlinks work today: `ln -s ~/Dropbox/co-knowledge ~/.config/co-cli/knowledge`

### Parallel Workstreams

If multiple implementers available:

- **Stream A**: Phase 1d + 1c (prompt system improvements)
- **Stream B**: Phase 2a (MCP client)
- **Stream C**: Phase 2b research → implementation
- **Stream D**: Phase 2c (after Stream B complete)

---

## Future Work ROI Ranking

Future enhancements beyond current Phase 1-2 roadmap, ranked by return on investment:

| TODO | Effort | Impact | ROI | Status |
| --- | --- | --- | --- | --- |
| **Model Fallback Chain** | Medium | High (graceful degradation) | **Best** | Planned |
| MCP Client Support — Phase 1 | Medium | High (extensibility + ecosystem) | **Best** | ✅ Phase 2a |
| Critical Tools S0 (Shell Security) | Small-Medium | High (safety + trust) | **Best** | Phase 2.5 |
| **Context Window Guard** | Small | Medium (prevents truncation) | **High** | Planned |
| **Session Persistence** | Medium | Medium-High (resume, audit) | Medium-High | Planned |
| User Workflow Preferences | Small-Medium | High (personalization) | Medium-High | Phase 2b |
| **Skills System** | Small-Medium | High (zero-code extensibility) | Medium-High | Planned |
| Slack Tooling — Phase 2/3 | Small-Medium | Medium | Medium-High | Planned |
| Cross-Tool RAG | Large | High (at scale) | Low | Deferred |

**Next priorities** (post-Phase 2c): Model Fallback Chain → Context Window Guard → Session Persistence

---

## Unified Prompt Assembly Order

System prompt assembles as (recency = precedence):

```
1. Base system.md (identity, principles, tool guidance)     ← Phase 1a ✅
2. Model quirk counter-steering (if model_name provided)    ← Phase 1d ✅
3. Personality template (if specified)                       ← Phase 1b ✅
4. Internal knowledge (.co-cli/knowledge/context.md)         ← Phase 1c ✅
5. User preferences (computed from settings)                 ← Phase 2b
6. Project instructions (.co-cli/instructions.md)            ← Phase 1a ✅
```

Total context budget: ~15-20KB (manageable within LLM context window)

---

## Success Metrics

### Technical Metrics

- **Test Coverage**: >90% for all new code
- **Performance**: <100ms overhead per phase at session start
- **Memory**: <10KB per phase (internal knowledge, preferences)
- **Reliability**: No regressions in existing test suite

### Behavioral Metrics

- **Companion Behavior**: Co remembers user context across sessions
- **Adaptive Communication**: Personality + preferences work together without conflict
- **Extensible Tooling**: MCP servers integrate seamlessly with native tools
- **Async Capable**: Long tasks run in background without blocking interaction

### Quality Metrics

- **Peer Parity**: System prompt quality matches/exceeds Codex, Gemini CLI, Claude Code, Aider
- **Testability**: All features have functional tests, no mocks
- **Maintainability**: Clear documentation, explicit over implicit, simple over complex

---

## Risk Assessment

### Phase 1c Risks
- **Context size bloat** → Mitigation: Hard 20KB limit, validation
- **Performance impact** → Mitigation: Lazy loading, caching, benchmarks
- **Schema versioning** → Mitigation: Version field, migration path

### Phase 1d Risks
- **System reminder too repetitive** → Mitigation: Keep to 3-4 rules only
- **Model quirk maintenance** → Mitigation: Document process, community contributions

### Phase 2a Risks
- **Zombie MCP processes** → Mitigation: Proper async context manager
- **Tool name collisions** → Mitigation: Automatic prefixing
- **Approval bypass** → Mitigation: Strict inheritance of host approval model

### Phase 2b Risks
- **Preference explosion** → Mitigation: Start with 10 core preferences, grow slowly
- **Personality vs preference conflicts** → Mitigation: Clear precedence rules, tests

### Phase 2c Risks
- **Resource leaks** → Mitigation: Task cleanup policy, monitoring
- **Silent failures** → Mitigation: Status tracking, error logging
- **Approval gaps** → Mitigation: Inherit approval decisions, no mid-execution prompts

---

## Next Steps

### Immediate Actions (Post Architecture Review)

1. ✅ **COMPLETE**: Create all 6 implementation guides
2. ✅ **COMPLETE**: Phase 1d implementation (quick win)
3. ✅ **COMPLETE**: Phase 1c implementation (foundational)
4. ✅ **COMPLETE**: Architecture review (tool registration, approval, contracts)
5. ✅ **COMPLETE**: Defer Phase 1e to follow-on status
6. ⏳ **NEXT**: Begin Phase 2a implementation (MCP client) - **Recommended first**

**Architecture Review Verdict**: System is 9.9/10 consistent, no refactoring needed. Proceed with revised roadmap (2a → 2b → 2c), defer shell security (S0+S1) to Phase 2.5, defer Phase 1e to follow-on.

**Phase 1e Deferral Rationale**: Co should first *have* and *use* its soul (Phase 1c knowledge system) in production before worrying about portability (export/import/sync). Let Phase 1c stabilize first.

### Phase-Specific Next Steps

**Phase 2a** (Ready to start - NEXT):
- Verify pydantic-ai v1.52+ MCP support
- Implement MCPServerConfig
- Integrate with agent factory
- Add security advisory re: `!` bypass
- Write 13+ functional tests

**Phase 2b** (After research integration):
- Implement UserPreferences dataclass
- Create preference loading logic
- Integrate with prompt assembly
- Write 15+ tests

**Phase 2c** (Last):
- Design task storage system
- Implement async runner
- Create slash commands
- Write 25+ tests

---

# Part III: Reference

## Design & Implementation Docs

### Design Documents
- `docs/DESIGN-00-co-cli.md` - Architecture overview
- `docs/DESIGN-01-agent.md` - Agent factory, CoDeps
- `docs/DESIGN-02-chat-loop.md` - Chat loop, streaming, commands
- `docs/DESIGN-14-memory-lifecycle-system.md` - Knowledge system architecture
- `docs/REVIEW-compare-four.md` - Peer system analysis (prompt techniques)

### Implementation Guides
- `docs/TODO-co-evolution-phase1c-COMPLETE.md` (2,594 lines) - Internal knowledge ✅
- `docs/TODO-co-evolution-phase1d-COMPLETE.md` (1,500 lines) - Prompt improvements ✅
- `docs/TODO-co-evolution-phase2a-mcp-client.md` (1,850 lines) - MCP client
- `docs/TODO-co-evolution-phase2b.md` (2,000 lines) - User preferences
- `docs/TODO-co-evolution-phase2c-background-execution.md` (1,900 lines) - Background execution
- `docs/TODO-co-evolution-phase2.5-critical-tools.md` - Shell security + file tools (Phase 2.5+2d)
- `docs/TODO-co-evolution-phase1e-FOLLOW-ON.md` - Portable identity (deferred)
- `docs/TODO-co-evolution-phase3-voice.md` - Voice-to-voice round trip (Phase 3, deferred)

### Verification & Review
- `docs/VERIFICATION-phase1c-demo-results.md` - Phase 1c verification

---

## External Sources (Frontier Research)

### Industry Research (AI Agents & Assistants)
1. OpenAI, "Introducing ChatGPT agent" (July 17, 2025): https://openai.com/index/introducing-chatgpt-agent/
2. OpenAI, "New tools for building agents" (March 11, 2025): https://openai.com/index/new-tools-for-building-agents/
3. OpenAI platform changelog (Responses API / MCP updates): https://platform.openai.com/docs/changelog
4. OpenAI Help, "ChatGPT agent" (updated 2026): https://help.openai.com/en/articles/11752874-chatgpt-agent
5. Anthropic, "Introducing Claude 4" (May 22, 2025): https://www.anthropic.com/news/claude-4
6. Anthropic Claude docs, "Computer use tool": https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
7. Anthropic release notes (2025-2026 API/tooling timeline): https://platform.claude.com/docs/en/release-notes/overview
8. Google I/O 2025 updates (Agent Mode, Project Mariner, Jules, MCP/A2A): https://blog.google/technology/google-io/gemini-updates-io-2025/
9. Google DeepMind, "Project Mariner": https://deepmind.google/models/project-mariner/
10. Google Labs, "Jules now available" (July 23, 2025): https://blog.google/technology/google-labs/jules-now-available/
11. Google Labs, "New ways to build with Jules" (October 2, 2025): https://blog.google/technology/google-labs/jules-tools-jules-api/
12. Google Developers, "Jules proactive updates" (December 10, 2025): https://blog.google/technology/developers/jules-proactive-updates/

### Voice & Audio Processing
13. OpenAI, "Realtime API VAD guide": https://platform.openai.com/docs/guides/realtime-vad
14. OpenAI, "Developer notes on the Realtime API": https://developers.openai.com/blog/realtime-api/
15. Google, "Gemini Live API overview": https://ai.google.dev/gemini-api/docs/live
16. Pipecat (Daily.co), voice AI framework: https://github.com/pipecat-ai/pipecat
17. LiveKit Agents: https://github.com/livekit/agents
18. Silero VAD: https://github.com/snakers4/silero-vad
19. faster-whisper: https://github.com/SYSTRAN/faster-whisper
20. Kokoro-82M (ONNX): https://github.com/thewh1teagle/kokoro-onnx
21. Piper TTS: https://github.com/rhasspy/piper
22. "Cracking the <1-second voice loop" (30+ stack benchmarks): https://dev.to/cloudx/cracking-the-1-second-voice-loop-what-we-learned-after-30-stack-benchmarks-427
23. "Real-Time vs Turn-Based Voice Agent Architecture" (Softcery): https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture
24. "The voice AI stack for building agents in 2026" (AssemblyAI): https://www.assemblyai.com/blog/the-voice-ai-stack-for-building-agents

---

## Version History

- **2026-02-10**: Phase 1c complete (internal knowledge, markdown lakehouse, memory tools)
- **2026-02-10**: Phase 1d complete (prompt improvements, peer learnings)
- **2026-02-10**: Architecture review complete - 9.9/10 score, defer Phase 2.5 and Phase 1e
- **2026-02-10**: Documentation consolidation - merged DESIGN-co-evolution.md + ROADMAP-phases1c-2c.md → ROADMAP-co-evolution.md
- **2026-02-10**: Added documentation lifecycle pattern from REVIEW-phase-documentation-cleanup.md
- **2026-02-10**: Extracted voice detail to TODO-co-evolution-phase3-voice.md, replaced with 10-line summary (audit recommendation)
- **2026-02-09**: Documentation phase complete (6 guides, ~10,100 lines)
- **2026-02-09**: Phase 1a, 1b complete (model conditionals, personalities)

---

**Current Status**: Phases 1a-1d complete ✅. Next: Phase 2a (MCP client). Phase 1e deferred to follow-on (non-core portability). Phase 2.5 (shell security) deferred until after Phase 2c.
