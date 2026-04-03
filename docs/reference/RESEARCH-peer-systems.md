# RESEARCH: peer repos and code-grounded assistant patterns
_Date: 2026-03-11_

This doc replaces the earlier file-level note sheet with a design review grounded in:

- local peer repos cloned under `~/workspace_genai/`
- current co implementation and design docs

Claims in this doc are intentionally limited to code-observable facts checked in the cloned repos: named packages, modules, exported surfaces, approval states, and concrete runtime hooks.

The question is not "what features exist?" The question is "what patterns are actually converging for personalized, autonomous assistant systems, and where does co sit relative to them?"

This review is explicitly **tradeoff-driven and best-practice-driven**. No peer system should be treated as a template for wholesale adoption. The goal is to cherry-pick strong patterns, reject poor-fit assumptions, and stay disciplined about co's MVP constraints: local-first operation, explicit approvals, inspectable state, and product-shaped simplicity.

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
- JSONL session transcript persistence with compact-boundary resume
- model-spec-aware compaction engine (`resolve_compaction_budget()` resolves context_window from model quirks, subtracts output reserve, respects Ollama Modelfile overrides — follows fork-cc's `effectiveContextWindow` pattern)
- clean context module separation: history processors (`_history.py`), compaction engine (`_compaction.py`), transcript I/O (`_transcript.py`), session browser (`_session_browser.py`)

So the relevant comparison set is no longer "basic CLI copilots." It is "persistent personal operators."

---

# 2. Local peer repo review (code-observable only)

This section is constrained to what is directly visible in the local repositories as checked on 2026-04-03. Each comparison is intentionally anchored to concrete files or named code surfaces, not inferred product intent.

| System | Observed code facts | Matching co code facts | Signal | Practical takeaway |
|---|---|---|---|---|
| `Codex` | - Separate CLI entrypoint in `codex-rs/cli/src/main.rs`<br>- Dedicated exec-policy module in `codex-rs/core/src/exec_policy.rs`<br>- Dedicated sandbox modules in `codex-rs/core/src/sandboxing/`<br>- Repo references SQLite-backed state/logging surfaces such as `state_db` and `sqlite` | - Approval and shell-policy surfaces in `co_cli/tools/_shell_policy.py`, `co_cli/tools/_shell_backend.py`, and `co_cli/tools/_tool_approvals.py`<br>- SQLite-backed telemetry in `co_cli/observability/_telemetry.py`<br>- SQLite-backed knowledge storage in `co_cli/knowledge/_store.py` | - Policy, sandbox, and persistence are split into named modules<br>- State and logs are persisted explicitly | - Study stricter separation between command policy, sandbox plumbing, and UI/server entrypoints |
| `fork-claude-code` | - `AGENTS.md` declares a React/Ink CLI<br>- `AGENTS.md` enforces strict ESM import rules<br>- `ink.ts` centralizes render wrapping<br>- `interactiveHelpers.tsx` and `remote/sdkMessageAdapter.ts` separate rendering from message adaptation<br>- 5-layer compaction: auto-compact, reactive, micro-compact, session memory compact, manual `/compact` (see `RESEARCH-peer-session-compaction.md`) | - Typer CLI entrypoint in `co_cli/main.py`<br>- Rich/prompt-toolkit display centralized in `co_cli/display/_core.py`<br>- Compaction budget resolution via `resolve_compaction_budget()` follows fork-cc's `effectiveContextWindow = contextWindow - maxOutputTokens` pattern<br>- Single-pass summarization (sufficient — fork-cc also uses single-pass)<br>- Circuit breaker (3 failures → static marker) matches fork-cc's `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3` | - Rendering is isolated behind dedicated UI modules<br>- Compaction budget is derived from model spec, not hardcoded defaults | - Study explicit UI/render boundary discipline<br>- Compaction architecture now converges on budget resolution and failure handling<br>- Do not adopt fork-cc's reactive/session-memory layers (feature-gated, experimental) |
| `Gemini CLI` | - `package.json` builds separate `build`, `build:sandbox`, and `build:vscode` artifacts<br>- `packages/core/src/policy/policy-engine.ts` carries approval-mode logic<br>- `packages/a2a-server/src/agent/task.ts` and tests encode `awaiting_approval` task states | - Deferred approval handling through the tool/turn loop<br>- Approval machinery documented around `DeferredToolRequests` and implemented in `co_cli/tools/_tool_approvals.py`<br>- Single main CLI surface rather than split companion surfaces | - Approval state is represented explicitly in code<br>- CLI, sandbox, and companion surfaces are built as separate artifacts | - Study the explicit approval-state machine<br>- Study the separation between CLI, sandbox, and companion surfaces |
| `Aider` | - `aider/args.py` exposes git and repo-map controls such as `--auto-commits`<br>- `aider/repo.py` wraps commit behavior directly<br>- `aider/commands.py` implements undo, diff, and repo-map flows against git state | - co exposes shell, file, and subagent workflows<br>- co is not organized around a git commit/revert control plane | - Reversibility is wired directly to git operations<br>- Repo state is a first-class runtime input | - Study explicit git-based reversibility as a workflow simplifier |
| `OpenClaw` | - Hard plugin boundaries in `src/plugin-sdk/` and `src/plugins/`<br>- `src/tasks/task-registry.types.ts` encodes runtime labels including `subagent` and `acp`<br>- Package exports expose the plugin SDK as a public surface | - Native tools, MCP integration, and skills already exist<br>- No ACP-labeled runtime taxonomy at the same granularity<br>- No equivalent public plugin SDK boundary | - Runtime kinds are named and typed in the task layer<br>- Plugin surfaces are treated as explicit public contracts | - Study explicit runtime typing and plugin boundary definition |
| `Letta` | - `letta/functions/function_sets/base.py` implements typed memory block operations: `create`, `str_replace`, `insert`, `delete`, `rename`<br>- `letta/agent.py` contains explicit summarizer retry and overflow handling<br>- `letta/settings.py` exposes summarizer controls | - Markdown memory files plus SQLite knowledge index<br>- Memory tools are flatter (no block-level ops)<br>- Dedicated compaction engine in `co_cli/context/_compaction.py` with `resolve_compaction_budget()` (model-spec-aware budget), `summarize_messages()`, circuit breaker (3 failures → static marker), and `estimate_message_tokens()` fallback<br>- Summarizer prompt includes adversarial-content security rule (converges with fork-cc and gemini-cli) | - Memory editing is modeled as explicit operations<br>- Summarizer behavior is controlled through named settings and retry logic | - Study typed memory-edit operations<br>- co's summarizer control surfaces now converge with Letta's pattern |
| `Mem0` | - `mem0/__init__.py` exports `MemoryClient` and `Memory`<br>- `mem0/client/main.py` implements `update`, `delete`, and `history`<br>- `mem0/utils/factory.py` maps vector backends including `mongodb`, `pgvector`, `qdrant`, and `faiss` | - Local memory lifecycle exists<br>- SQLite-backed search exists<br>- No similarly explicit mutation/history API<br>- No comparable pluggable vector-store factory surface | - Memory is exposed as a mutable API with explicit lifecycle verbs<br>- Storage backends are selected through a registry/factory layer | - Study explicit memory mutation/history APIs<br>- Study sharper separation between memory API and backend registry |
| `OpenCode` | - Repo is split into `packages/app`, `packages/desktop-electron`, `packages/sdk`, and `packages/console`<br>- `packages/sdk/openapi.json` exposes workspace CRUD endpoints<br>- `packages/console/function/src/auth.ts` binds auth flows to workspace records | - co is primarily a single local CLI/runtime<br>- No parallel app/desktop/sdk packaging<br>- No separate workspace service API | - App, desktop, SDK, and service layers are separated at package boundaries<br>- Workspace operations are exposed as an API surface | - Study package-boundary clarity between app, desktop, SDK, and workspace service layers |

---
# 3. Convergences that matter

| Convergence | Adoption planning |
|---|---|
| - **Memory APIs are becoming more typed and mutable**<br>- Letta exposes named memory block operations such as `create`, `str_replace`, `insert`, `delete`, and `rename`<br>- Mem0 exposes explicit memory lifecycle verbs such as `update`, `delete`, and `history` | - Treat flat markdown memory files as an intermediate form, not the final shape<br>- Add clearer typed memory operations<br>- Add stronger inspection and editing flows for stored memory |
| - **Approval and execution policy are becoming explicit runtime layers**<br>- Codex splits exec policy and sandboxing into dedicated modules<br>- Gemini CLI represents approval state explicitly and keeps policy logic in a dedicated engine | - Keep co’s approval-first design<br>- Sharpen the separation between policy evaluation, sandbox plumbing, and user-facing execution flow<br>- Make approval state easier to inspect in the runtime model |
| - **Reversibility and task state are becoming first-class runtime concepts**<br>- Aider wires editing workflow directly to git operations such as auto-commit, diff, undo, and repo-map refresh<br>- Gemini CLI task handling encodes `awaiting_approval` states in code<br>- OpenClaw task records encode runtime, status, delivery status, and event history | - Evolve beyond simple background subprocesses<br>- Introduce more explicit long-running task state<br>- Add clearer rollback and recovery surfaces where actions are stateful or multi-step |
| - **Package, plugin, and UI boundaries are becoming more explicit**<br>- OpenClaw exposes a public `plugin-sdk` boundary and separate plugin/runtime layers<br>- OpenCode separates app, desktop, SDK, and console/service packages<br>- fork-claude-code separates rendering from message adaptation | - Preserve co’s modularity as the surface grows<br>- Keep UI concerns, runtime concerns, and extension concerns separated<br>- Prefer clearer public boundaries before adding more surface area |
| - **Agent runtime taxonomy is becoming explicit in code**<br>- OpenClaw names runtime kinds such as `subagent`, `acp`, and `cli`<br>- Gemini CLI policy matching accepts subagent-specific rules | - Make child-agent, task, and runtime categories more explicit in co’s own runtime model<br>- Improve inspectability of delegation and task routing behavior |

---

# 4. Where co is strong

co is already well-positioned in six areas:

- **local-first control**: stronger than most frontier cloud assistants
- **approval boundary**: clearer than many open-source agent stacks
- **composability**: files, shell, skills, MCP, delegation all compose cleanly
- **project-local memory**: better than stateless CLI peers
- **inspectable implementation**: design docs and code structure are unusually legible
- **context governance**: model-spec-aware compaction budget (`resolve_compaction_budget()` with context_window from quirks, output reserve subtraction, Ollama Modelfile override), dedicated compaction engine separated from history processors, circuit breaker on summarization failures, JSONL transcript with compact-boundary resume. Converges with fork-cc's layered compaction architecture while staying simpler (single-pass summary, no forked agents).

These are real advantages. They should shape the roadmap rather than be treated as incidental.

---

# 5. Where co lags the peer-code patterns

## 5.0 Self/personality model quality

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

## 5.1 User model quality

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

## 5.2 Asynchronous autonomy

co has background subprocesses, but not yet recurring plans, deferred follow-up, or task graphs that combine tools, memory updates, and approvals over time.

## 5.3 Memory legibility

Users can store and recall memories, but the system still needs stronger inspectability, editability, and scope separation.

## 5.4 Multimodal continuity

co remains mostly text-and-file centric. The frontier is shifting toward screenshots, documents, voice, camera, browser context, and cross-device state.

## 5.5 Source freshness and personal-context fusion

co has good retrieval primitives, but still weaker fusion between private sources, learned user context, and current tasks than the best frontier systems are targeting.

---

# 6. Adoption method

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

- from Codex: study the split between CLI entrypoints, exec policy, and sandbox modules, not a sandbox-first identity shift
- from Gemini CLI: study the approval-state machine and surface separation, not the whole multi-surface product
- from Aider: study explicit git-based reversibility, not a requirement that co become git-centric
- from Letta: study typed memory-edit operations and summarizer controls, not framework adoption
- from Mem0: study explicit memory mutation/history APIs and backend registries, not graph-heavy architecture by default
- from OpenCode: study package-boundary clarity between app, desktop, SDK, and workspace service layers, not unnecessary surface expansion
---

# 7. Recommended strategic direction for co

## 7.1 Double down on "trusted local operator"

Do not compete by breadth. Compete by:

- user-owned state
- inspectable memory
- explicit approvals
- reversible actions
- project-aware continuity

## 7.2 Evolve memory into a typed personal state layer

Recommended next moves:

- separate user profile, project memory, task memory, and relationship memory
- add canonical preference/habit records
- expose edit/review tools for the user model
- move more extraction/consolidation into background flows

## 7.3 Turn background execution into bounded agent workflows

Recommended next moves:

- recurring schedules
- resumable multi-step tasks
- approval checkpoints inside long plans
- delegated specialists that can run under task control

## 7.4 Treat multimodal/cross-surface work as a medium-term requirement

Recommended next moves:

- better voice/notification surfaces
- document and screenshot ingestion
- tighter browser or browser-adjacent action loops

## 7.5 Reframe personality as working style, not product thesis

Personality can still matter, but frontier systems win by:

- memory quality
- trust
- completion
- continuity

co should preserve style and warmth while making usefulness the center of gravity.

---

# 8. Bottom line

The 2026 frontier for personalized autonomous assistants is defined by:

- durable memory
- explicit user controls
- asynchronous task execution
- connector-rich context access
- scoped specialist execution
- multimodal continuity

co already has much of the runtime substrate required to compete in that category.

Its main opportunity is not to become "more agentic." It is to become **more personal, more inspectable, and more trustworthy while extending autonomy carefully over time**.
