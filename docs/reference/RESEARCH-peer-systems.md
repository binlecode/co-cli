# RESEARCH: peer repos and 2026 frontier assistant patterns
_Date: 2026-03-11_

This doc replaces the earlier file-level note sheet with a design review grounded in:

- local peer repos cloned under `~/workspace_genai/`
- current co implementation and design docs
- 2025-2026 primary-source product/docs material from OpenAI, Anthropic, Google, Letta, and Mem0

The question is not "what features exist?" The question is "what patterns are actually converging for personalized, autonomous assistant systems, and where does co sit relative to them?"

This review is explicitly **tradeoff-driven and best-practice-driven**. No peer or frontier system should be treated as a template for wholesale adoption. The goal is to cherry-pick strong patterns, reject poor-fit assumptions, and stay disciplined about co's MVP constraints: local-first operation, explicit approvals, inspectable state, and product-shaped simplicity.

---

# 1. co baseline

Before comparing peers, co's actual current baseline matters.

co already has:

- local-first CLI runtime with explicit approvals
- workspace file tools and shell execution
- background subprocess tasks
- project-local memory lifecycle with proactive injection
- knowledge/article retrieval with FTS5 or hybrid search
- skills as markdown overlays
- read-only delegated sub-agents
- Google, Obsidian, web, and MCP integrations

So the relevant comparison set is no longer "basic CLI copilots." It is "persistent personal operators."

---

# 2. Local peer repo review

| System | What stands out | co today | Implication for co |
|---|---|---|---|
| `Codex` | Strongest shell-safety and sandboxing discipline; explicit execution boundaries; coding-first product shape | co already has approval-first execution and shell policy, but is less formal on execution hardening and more personal-operator oriented | Borrow command-policy rigor and execution safety patterns. Do not drift into a sandbox-first product identity. |
| `Claude Code` | Memory hierarchy via `CLAUDE.md`; first-class subagents; mature permission surfaces | co is already aligned on scoped delegation, markdown-defined behavior, and approvals, but has a flatter memory model | Borrow scoped memory visibility and specialist-context patterns. Do not recenter the product around coding. |
| `Gemini CLI` | Event-driven subagent task execution (`a2a-server`, `agent/executor`), deep browser agent integration (`browserAgentInvocation`), policy engine enhancements, and web fetching (`webfetch-stage-1`) | co has a stronger local-memory and approval story, but weaker cross-surface continuity and event-driven delegation | Borrow permission ergonomics, event-driven task models, and browser execution ideas selectively. Do not copy broad product-surface ambition. |
| `Aider` | Very simple approval model; git-centric reversibility; trust through explicitness | co has richer workflows and tools, but also more complexity and more trust UX burden | Borrow simplicity and reversibility discipline. Do not assume co should collapse into a purely git-centric workflow. |
| `OpenClaw` | Agentic Control Protocol (ACP) support, subagent scoping/spawn limits, embedded runner compaction/failover, talk mode, and `cluster` concepts | co is closer on knowledge-work orientation and local operator feel, but narrower in scope and lacks explicit inter-agent protocols like ACP | Borrow ACP standard integration and subagent boundary (spawn limits) ideas. Do not absorb the full surface-area sprawl. |
| `Letta` | Memory is product-central; typed in-context vs archival memory; async memory maintenance | co has a strong local memory substrate, but it is still flatter and less visibly typed | Borrow typed memory tiers and async maintenance ideas. Do not adopt a framework dependency wholesale. |
| `Mem0` | Maturation into SQLite vector stores, official OpenClaw integration, explicit mutation semantics, graph/multimodal memory | co already has useful local memory lifecycle primitives, but weaker typed mutation semantics and SQLite/vector maturity | Borrow explicit memory mutation semantics, robust SQLite vector handling, and cross-assistant integrations. Do not default to graph-heavy infrastructure. |
| `OpenCode` | Refined TUI with new dialogs, workspace routing middleware, and state-model cleanup | co is directionally aligned but can improve workflow polish, TUI dialogues, and clean state routing | Borrow lightweight workflow routing, state management discipline, and TUI polish. Do not add orchestration machinery for its own sake. |
| `nanobot` | Broad chat-app integration (added WeCom), native heartbeat/cron tasks, new memory consolidation token logic, config migration tests | co has background tasks and CLI focus, but narrower external channel integration and consolidation logic | Borrow memory consolidation logic and broad channel connectivity ideas. Do not abandon CLI-first origins for a purely chat-bot focus. |

---

# 3. Primary-source frontier signals

| System | Topic | Sources | Signal |
|---|---|---|---|
| OpenAI | Memory as a default personalization primitive | https://openai.com/index/memory-and-new-controls-for-chatgpt/<br>https://help.openai.com/en/articles/8983136-what-is-memory | Personalization is now part of the default assistant stack, with explicit user controls to inspect, disable, and forget memory. |
| OpenAI | Agent mode unifies research and action | https://help.openai.com/en/articles/11752874-chatgpt-agent<br>https://help.openai.com/en/articles/11794368-chatgpt-agent-release-notes<br>https://openai.com/index/introducing-deep-research/<br>https://openai.com/index/introducing-operator/ | The frontier product shape is unified: research, action, connectors, and recurring execution live in one assistant surface. |
| OpenAI | Browser-native continuity | https://openai.com/index/introducing-chatgpt-atlas/<br>https://help.openai.com/en/articles/12625059-web-browsing-settings-on-chatgpt-atlas | Assistants are moving from chat windows with tools toward persistent operating layers across browsing activity. |
| Anthropic | Project-scoped, user-editable memory | https://www.anthropic.com/news/memory<br>https://docs.anthropic.com/en/docs/claude-code/memory | Memory is converging toward visible scope boundaries such as project, user, and incognito, with editability as a first-class requirement. |
| Anthropic | Subagents as a first-class abstraction | https://docs.anthropic.com/en/docs/claude-code/sub-agents<br>https://docs.anthropic.com/en/docs/claude-code/settings | Isolated specialist contexts with separate prompts and permissions are stabilizing as a durable agent pattern. |
| Google | Personalization fused with user apps/history | https://blog.google/products-and-platforms/products/gemini/gemini-personalization/<br>https://blog.google/products/gemini/new-gemini-app-features-march-2025/ | The frontier is not generic memory alone; it is personalization fused with the user's ambient app and history context. |
| Google | Multimodal, cross-device assistance | https://deepmind.google/technologies/project-astra/<br>https://deepmind.google/en/models/project-astra/ | The assistant frontier is becoming multimodal, proactive, and persistent across devices. |
| Letta | Typed memory blocks and async memory upkeep | https://docs.letta.com/letta-code/memory | Typed, inspectable memory blocks are stronger than opaque flat recall, and asynchronous memory maintenance is becoming standard. |
| Mem0 | Structured memory operations | https://docs.mem0.ai/overview<br>https://docs.mem0.ai/core-concepts/memory-operations/add<br>https://docs.mem0.ai/platform/features/graph-memory<br>https://docs.mem0.ai/open-source/features/multimodal-support<br>https://docs.mem0.ai/open-source/features/custom-update-memory-prompt | Frontier memory systems increasingly treat memory as a structured evolving knowledge layer, not a bag of notes. |
| nanobot | Transparent async scheduling and broad channel continuity | https://github.com/HKUDS/nanobot | Simple, LLM-driven background tasks (heartbeat via markdown) and multi-channel messaging are achievable with very little code overhead. |

# 4. Convergences that matter

The strongest 2026 convergences are:

## 4.1 Memory must be durable, scoped, and user-controllable

Strong evidence:

- OpenAI memory and memory controls
- Anthropic project-scoped memory
- Letta memory blocks
- Mem0 explicit memory mutations

Design implication for co:

- current memory lifecycle is a strong base, but flat memory files are not the final form
- co needs clearer user/project/profile/habit scopes and direct inspection/editing flows

## 4.2 The assistant is becoming asynchronous

Strong evidence:

- OpenAI agent supports tasks that run 5-30 minutes and recurring schedules
- Letta sleeptime agents
- nanobot native heartbeat loop (wakes up via reading a `HEARTBEAT.md` file) and robust cron capabilities

Design implication for co:

- background subprocesses are only stage one
- co should evolve toward scheduled, resumable, multi-step agent workflows

## 4.3 Connectors/apps are now core, not optional

Strong evidence:

- OpenAI apps/connectors and MCP support in deep research
- Google personalization through app context
- Anthropic memory/project surfaces

Design implication for co:

- co is right to treat external knowledge surfaces as first-class
- the next step is tighter continuity between these sources and personal memory, not just more connectors

## 4.4 Subagents and scoped contexts are stabilizing

Strong evidence:

- Anthropic subagents
- OpenAI unified agent workflows with multiple tool modes
- OpenClaw subagent scoping and explicit spawn depth limits

Design implication for co:

- current delegation work is aligned
- the gap is not existence of subagents, but making them useful for long-running bounded workflows with explicit queueing/observability, while enforcing strict spawn limits like OpenClaw

## 4.5 Multimodal and cross-surface continuity is rising fast

Strong evidence:

- Google Project Astra
- OpenAI Atlas and agent/browser integration
- Mem0 multimodal memory
- nanobot multi-channel chat-app messaging bus (Telegram, Discord, Feishu, QQ, etc.)

Design implication for co:

- terminal-first remains viable
- terminal-only is probably not the long-term ceiling if the goal is a true personal operator

## 4.6 Agent-to-agent communication is standardizing (ACP)

Strong evidence:

- OpenClaw's adoption of the Agentic Control Protocol (ACP), `acpx` plugins, and ACP translators/servers

Design implication for co:

- bespoke agent communication protocols will become a liability
- co should investigate standardizing its agent-to-agent and tool-to-agent interfaces using emerging standards like ACP instead of custom local IPC

---

# 5. Where co is strong

co is already well-positioned in five areas:

- **local-first control**: stronger than most frontier cloud assistants
- **approval boundary**: clearer than many open-source agent stacks
- **composability**: files, shell, skills, MCP, delegation all compose cleanly
- **project-local memory**: better than stateless CLI peers
- **inspectable implementation**: design docs and code structure are unusually legible

These are real advantages. They should shape the roadmap rather than be treated as incidental.

---

# 6. Where co is behind the frontier

## 6.0 Self/personality model quality

co has a recognizable working style, but not yet a strong explicit self model for how that style should be represented, adapted, and constrained over time.

Detailed follow-up: see `docs/REVIEW-self-model-working-style.md`.

This matters because "personality" is not just tone. In a durable assistant, the self model is the system's internal contract for how it should behave across tasks: how proactive it should be, how much uncertainty it should surface, how it should balance warmth vs brevity, when it should challenge the user, and what trust posture it should maintain around actions and memory.

High-quality self/personality modeling has five properties:

- **explicit**: the assistant's behavioral defaults are defined as stable dimensions, not left as diffuse prompt vibes
- **situational**: the model can adapt style by context such as coding, research, planning, or personal admin without losing identity
- **bounded**: personality does not override truthfulness, caution, approval policy, or task completion
- **consistent**: the user sees the same underlying operator across sessions instead of large swings caused by prompt locality or recent context
- **inspectable**: the behavior contract is understandable enough that maintainers can revise it intentionally rather than by accidental prompt drift

Today co appears stronger on voice than on self-model structure. It can present a coherent style, but the style is still more implicit than operationalized. That creates recurring risks:

- useful traits may not apply consistently across tools, tasks, and long-running workflows
- style can compete with task success if it is not clearly subordinated to utility and trust
- maintainers may find it hard to tune behavior precisely because the model is encoded mostly as prose guidance rather than explicit dimensions and policies

The frontier lesson is that self/personality quality matters, but it is not the product center of gravity. The strongest systems treat personality as a thin working-style layer on top of stronger foundations: memory quality, approval clarity, context continuity, and reliable task completion.

For co, the practical target is a self model that is:

- stable enough to feel intentional
- flexible enough to match the task
- subordinate to usefulness and trust
- simple enough to maintain without prompt sprawl

## 6.1 User model quality

co has memory, but not yet a strong explicit user model with clear classes like profile, habits, standing instructions, relationships, and project norms.

The gap is not "more memory volume." The gap is memory quality: whether the assistant can form a stable, accurate, current, and operationally useful picture of the user.

High-quality user modeling has at least six properties:

- **typed**: facts are separated into meaningful classes such as stable profile facts, durable preferences, working habits, role/relationship context, and project-specific norms
- **source-aware**: each remembered item should carry where it came from, how direct it was, and how confident the system should be
- **time-aware**: the model should distinguish evergreen facts from recency-sensitive ones like current priorities, travel, active projects, or temporary constraints
- **conflict-aware**: new evidence should update, supersede, or downgrade stale memories instead of accumulating contradictory fragments
- **operational**: the model should improve behavior at decision time, not just retrieval time; it should shape defaults, draft tone, planning choices, and tool use
- **user-legible**: the user should be able to inspect, edit, delete, and correct the model without digging through raw note files

Today co is still closer to a good memory store than to a strong user model. It can retain useful information, but it does not yet clearly answer questions like:

- what does this user generally prefer vs what is only true in this project?
- which instructions are standing rules vs one-off requests?
- which facts are stale, disputed, or low-confidence?
- which relationship or team norms should constrain current behavior?

That matters because poor user-model quality creates subtle trust failures. The assistant may technically "remember" something while still applying it too broadly, too narrowly, or long after it stopped being true.

For co, the practical target is a user model that is:

- small enough to inspect
- structured enough to drive behavior
- conservative about uncertain inference
- easy to repair when wrong

That is a better design center than maximizing recall count or extraction aggressiveness.

## 6.2 Asynchronous autonomy

co has background subprocesses, but not yet recurring plans, deferred follow-up, or task graphs that combine tools, memory updates, and approvals over time. The explicit, file-backed `HEARTBEAT.md` loop pattern seen in `nanobot` illustrates a highly transparent, local-first way to implement recurring tasks without rigid framework scheduling.

## 6.3 Memory legibility

Users can store and recall memories, but the system still needs stronger inspectability, editability, and scope separation.

## 6.4 Multimodal continuity

co remains mostly text-and-file centric. The frontier is shifting toward screenshots, documents, voice, camera, browser context, and cross-device state.

## 6.5 Source freshness and personal-context fusion

co has good retrieval primitives, but still weaker fusion between private sources, learned user context, and current tasks than the best frontier systems are targeting.

---

# 7. Adoption method

The right question for each reference system is:

- what practice is strong here?
- what tradeoff makes it strong?
- does that tradeoff fit co's current stage and product constraints?

That means:

- never adopt a system wholesale
- adopt only the part that improves co's MVP or near-term roadmap
- prefer product-semantic improvements over framework/infrastructure expansion
- preserve co's local-first, approval-first, inspectable design center

Examples:

- from Codex: adopt command-safety rigor, not sandbox-first identity
- from Claude Code: adopt scoped specialist contexts, not coding-first product scope
- from Letta: adopt typed memory visibility, not a framework dependency
- from Mem0: adopt explicit memory update semantics, not graph-heavy architecture by default
- from nanobot: adopt transparent heartbeat tasks and channel event bus, not a pure chatbot architecture
- from OpenAI/Google frontier products: adopt direction-of-travel signals, not cloud-scale breadth
---

# 8. Recommended strategic direction for co

## 8.1 Double down on "trusted local operator"

Do not compete by breadth. Compete by:

- user-owned state
- inspectable memory
- explicit approvals
- reversible actions
- project-aware continuity

## 8.2 Evolve memory into a typed personal state layer

Recommended next moves:

- separate user profile, project memory, task memory, and relationship memory
- add canonical preference/habit records
- expose edit/review tools for the user model
- move more extraction/consolidation into background flows

## 8.3 Turn background execution into bounded agent workflows

Recommended next moves:

- recurring schedules
- resumable multi-step tasks
- approval checkpoints inside long plans
- delegated specialists that can run under task control

## 8.4 Treat multimodal/cross-surface work as a medium-term requirement

Recommended next moves:

- better voice/notification surfaces
- document and screenshot ingestion
- tighter browser or browser-adjacent action loops

## 8.5 Reframe personality as working style, not product thesis

Personality can still matter, but frontier systems win by:

- memory quality
- trust
- completion
- continuity

co should preserve style and warmth while making usefulness the center of gravity.

---

# 9. Bottom line

The 2026 frontier for personalized autonomous assistants is defined by:

- durable memory
- explicit user controls
- asynchronous task execution
- connector-rich context access
- scoped specialist execution
- multimodal continuity

co already has much of the runtime substrate required to compete in that category.

Its main opportunity is not to become "more agentic." It is to become **more personal, more inspectable, and more trustworthy while extending autonomy carefully over time**.
