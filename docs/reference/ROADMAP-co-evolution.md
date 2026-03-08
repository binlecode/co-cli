# ROADMAP: co evolution

This roadmap replaces the earlier phase plan that assumed co still lacked core agentic infrastructure. That assumption is no longer true. The current codebase already ships a capable local agent runtime with files, shell, memory, knowledge retrieval, background tasks, skills, MCP, and read-only delegation. The strategic question is no longer "how do we become agentic?" It is "what kind of personal agent should co become, and what must improve for that to be meaningfully better than the frontier?"

---

# 1. What co is now

co is a local-first terminal agent for personal knowledge work.

Today it is not just a chat wrapper around tool calls. The implementation already includes:

- native workspace file tools: `list_directory`, `read_file`, `find_in_files`, `write_file`, `edit_file`
- approval-gated shell execution with policy classification
- background subprocess execution with persisted task state
- project-local memory lifecycle with dedup, consolidation, retention, decay, and proactive injection
- article and knowledge retrieval with FTS5 or hybrid search
- Google, Obsidian, web, and MCP integrations
- skills as markdown-defined command overlays with tool grants
- read-only delegated sub-agents for coding, research, and analysis
- session restore, history compaction, telemetry, doctor/capability inspection

The roadmap must therefore start from the shipped system, not from peer-tool parity checklists that are already satisfied.

---

# 2. Mission assessment

## 2.1 Original mission

The original roadmap framed co as a "personal companion for knowledge work" and as a relationship-first system rather than a code-first CLI.

That instinct is directionally right, but the wording is too soft and too broad for product strategy.

## 2.2 What is strong in the current mission

- It correctly identifies continuity, memory, and personalization as the core differentiator.
- It correctly treats tool access as necessary but insufficient.
- It correctly optimizes for user-owned context instead of a generic cloud agent.

## 2.3 What is weak in the current mission

- "Companion" can drift into theatrical persona work that does not improve task completion.
- The roadmap over-indexed on personality framing relative to operational reliability.
- The old document treated many basic execution capabilities as future work even after the implementation had moved past that point.
- "General-purpose CLI companion" is too vague to define hard product boundaries.

## 2.4 Refined mission

co should be defined as:

**a trusted local personal operator for knowledge work**

Meaning:

- **trusted**: explicit approval boundary, inspectable state, reversible actions, grounded output
- **local**: user-controlled files, memory, configuration, integrations, and history
- **personal**: durable user model, preferences, habits, projects, relationships, and working context
- **operator**: able to research, plan, execute, monitor, and follow up across tools
- **for knowledge work**: optimized for information synthesis, coordination, writing, planning, and technical execution, not only coding

This framing is sharper than "companion" while still preserving the long-term personalization goal.

## 2.5 Ultimate goal

The ultimate goal is not a terminal chatbot with a better personality.

The ultimate goal is a **persistent personal operating layer** that can:

1. understand the user, their projects, and their norms
2. combine local, private, and web context into one working model
3. execute bounded actions asynchronously and safely
4. maintain continuity across sessions, devices, and time
5. become more useful through long-term adaptation rather than prompt growth

If co reaches that point, terminal is the control surface, not the product boundary.

---

# 3. Reality check against the current code

## 3.1 What has already shipped

The previous roadmap listed several items as future phases that are now present in the codebase:

| Previously framed as future | Current status in repo |
|---|---|
| File tools | shipped in `co_cli/tools/files.py` |
| Background execution | shipped in `co_cli/_background.py` and `co_cli/tools/task_control.py` |
| Skills system | shipped in `co_cli/_commands.py` and `docs/DESIGN-skills.md` |
| Session persistence | shipped in `co_cli/_session.py` and bootstrap flow |
| Sub-agent delegation | shipped in `co_cli/tools/delegation.py` and `co_cli/agents/` |
| Capability/health inspection | shipped in `co_cli/_doctor.py` and `co_cli/tools/capabilities.py` |
| Knowledge/article system | shipped in `co_cli/tools/articles.py` and `_knowledge_index.py` |

## 3.2 What is partially shipped but not yet frontier-grade

- compaction exists, but auto-governance quality still matters more than existence
- memory exists, but the user model is still mostly fact storage rather than a durable profile/habit system
- background tasks exist, but they are still subprocess-centric rather than full delegated workflows
- delegation exists, but specialists are read-only and do not yet coordinate long-running plans
- approvals exist, but trust still depends on undoability, previews, and action-scoped policy clarity

## 3.3 Strategic conclusion

co is already in the "agent runtime" category. The next leap is not more tools. The next leap is **better personalization plus safer autonomy**.

---

# 4. 2026 frontier reading

Across frontier systems in 2025-2026, the strongest convergence is no longer on basic tool calling. It is on five higher-order properties:

1. **Persistent memory with user controls**
2. **Asynchronous or scheduled task execution**
3. **Connectors/apps that unify private data with web research**
4. **Specialized sub-agents or scoped execution contexts**
5. **Multimodal and cross-surface continuity**

The main frontier systems differ in style, but they are moving toward the same destination: assistants that retain context, act over time, and operate across many surfaces while keeping the user in control.

For co, this means the bar has moved. Matching "CLI tool agent" features is no longer enough.

---

# 5. Strategic thesis for co

co should not try to beat cloud products by being broader.

co should try to beat them by being:

- more inspectable
- more user-owned
- more composable
- more project-aware
- more privacy-preserving
- more faithful to the user's real working context

That suggests a strategy built around **local memory + bounded autonomy + explicit operator control**, not "full auto agent" demos.

---

# 6. The next stages

## Stage 1: Reliability and trustworthiness

Goal: make co safe and dependable enough that users willingly hand it longer workflows.

Priority work:

- strengthen workspace checkpoints and `rewind` semantics around all write-capable actions
- add better action previews before destructive or broad edits
- tighten approval ergonomics around patterns, scopes, and expiry
- harden task observability: richer task events, live progress, resumable status views
- improve compaction correctness and handoff quality under long sessions

Why this matters:
without trust, autonomy does not compound; it just increases risk.

## Stage 2: Real personalization

Goal: evolve from memory recall to a durable user model.

Priority work:

- separate user profile, project profile, relationship/context, and ephemeral task memory
- add first-class preference and habit representations instead of treating everything as the same memory shape
- improve contradiction resolution and canonicalization of preferences, decisions, and standing instructions
- add passive/background memory formation for lower-latency conversations
- create explicit "what co knows about me / this project" inspection and editing surfaces

Why this matters:
today co remembers facts; the frontier is assistants that build an editable model of the user.

## Stage 3: Bounded autonomy

Goal: let co own longer-lived work without pretending it should act fully unsupervised.

Priority work:

- scheduled and recurring tasks beyond one-shot background subprocesses
- plan execution that can pause for approvals, credentials, or ambiguous decisions
- follow-up behaviors: reminders, stale-task checks, deferred summaries, re-research
- task graphs that combine delegation, tools, and memory updates
- scoped policy modes such as "research-only", "draft-only", "workspace-only"

Why this matters:
the frontier is asynchronous assistance, not only synchronous chat turns.

## Stage 4: Cross-surface continuity

Goal: make co a persistent operator across contexts, not just inside one terminal session.

Priority work:

- better voice and notification surfaces where they improve task flow
- cross-device or cross-surface session continuity
- richer multimodal memory inputs for screenshots, documents, and visual context
- tighter browser/web action story, whether via MCP, external surfaces, or future native integration

Why this matters:
the best personal assistants are becoming ambient and multimodal, not just text terminals.

## Stage 5: Personal operating system layer

Goal: co becomes the user's inspectable orchestration layer across tools, memory, and ongoing work.

This is the long-term destination:

- user-owned memory and knowledge graph
- agent-managed but user-auditable workflows
- specialized operator roles that share a common personal state
- local policy, approval, and provenance as first-class primitives

At this stage, co is no longer "a CLI app with tools." It is a personal operating layer with a terminal-first UX.

---

# 7. Immediate roadmap priorities

These are the highest-value next moves given the current repo state.

## P1. Upgrade memory from store-and-recall to profile-and-habits

Concrete direction:

- introduce first-class memory classes for user profile, project norms, and working preferences
- add explicit inspection/editing flows
- move more extraction and consolidation off the hot path

## P1. Strengthen bounded autonomy on top of background tasks

Concrete direction:

- recurring tasks
- richer task state transitions
- delegation into backgroundable workflows
- approval checkpoints within multi-step plans

## P1. Improve reversibility and trust UX

Concrete direction:

- stronger checkpoints before write batches
- clearer previews and diffs
- better approval scope language

## P2. Improve knowledge quality for long-form sources

Concrete direction:

- article/document chunking
- stale-source handling
- better hybrid retrieval and conflict handling

## P2. Evolve from "personality" to "working style"

Concrete direction:

- keep tone/style customization
- reduce emphasis on cinematic personas as the main product story
- increase emphasis on reliability, preferences, and learned operating norms

The issue is not that personality is wrong. The issue is that personality should be downstream of trust and usefulness, not upstream of them.

---

# 8. Non-goals

co should avoid several traps:

- chasing generic IDE-agent parity as the main strategy
- overbuilding multi-agent systems before single-agent continuity is excellent
- turning memory into opaque autonomous state the user cannot inspect or correct
- prioritizing theatrical persona behavior over completion quality
- adopting heavy infrastructure that breaks the local-first advantage without a clear win

---

# 9. Bottom line

The old roadmap described a system on its way to becoming agentic. The current repo is already there.

The new challenge is harder and more valuable:

**make co the most trustworthy local personal operator for knowledge work**

If co can combine durable personalization, bounded autonomy, explicit controls, and strong local context, it can occupy a differentiated position that frontier cloud assistants and code-first CLIs still do not fully own.
