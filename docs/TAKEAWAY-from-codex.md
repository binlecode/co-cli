# Takeaway from Codex

Comparative analysis of OpenAI Codex CLI (Rust/TypeScript) vs co-cli (Python/pydantic-ai), covering prompt design and agent loop architecture.

## 1. Executive Summary

Codex is OpenAI's open-source agentic coding CLI, built in Rust with a channel-based event loop, polymorphic task system, and a multi-axis prompt composition engine. It represents the most thoroughly-engineered agent shell in the peer set. The key strategic insight for co-cli: Codex proves that personality-as-module and display-only planning tools are high-ROI features, while its Rust-level concurrency machinery (RwLock-gated parallelism, PTY pools, FuturesOrdered) is over-engineering for a Python single-provider agent.

## 2. Prompt Design: What Codex Does Differently

### 2.1 Multi-Axis Layered Composition

**Codex:** The base prompt (`prompt.md`, ~276 lines) is the foundation, but the actual instructions sent to the model are assembled from multiple axes. In `model_info.rs:174-228`, each model slug maps to a `ModelInfo` struct that selects: (a) a base instructions string, (b) an optional `ModelMessages` containing an instructions template and personality variables. The `gpt-5.2-codex_instructions_template.md` uses a `{{ personality }}` placeholder that gets substituted at assembly time. The `SessionConfiguration` (in `codex.rs:368-386`) combines `base_instructions`, `personality`, `approval_policy`, `sandbox_policy`, `developer_instructions`, and `user_instructions` into the final prompt context.

**co-cli:** Assembly is: `instructions.md` + soul seed + 5 rule files + model quirks, concatenated linearly. No template substitution, no axis-specific slots.

**Tradeoff:** Codex's approach allows runtime recombination without rewriting prompts. co-cli's linear concatenation is simpler and auditable, but adding a new axis (e.g., plan mode) requires restructuring assembly logic. A modest template system (even `str.format()` with named slots) would bridge this gap.

### 2.2 Personality as Swappable Module

**Codex:** Defines personality as a first-class config axis (`Personality` enum). Two concrete personalities ship as standalone markdown files: `gpt-5.2-codex_friendly.md` (warm, team-morale-oriented, "NEVER curt or dismissive") and `gpt-5.2-codex_pragmatic.md` (efficient, rigor-focused, "no cheerleading"). Each defines Values, Tone, and Escalation sections. The personality is injected via template variable into the model instructions (`model_info.rs:182-183`), and can be swapped per-turn via `Op::OverrideTurnContext` in the submission loop (`codex.rs:2692`).

**co-cli:** Has a single soul seed (~240 chars) embedded in the assembly. No runtime swapping, no personality enum, no separate personality files.

**Tradeoff:** Personality swapping is low-effort and high-value. It lets teams experiment with tone without touching behavioral rules. co-cli could implement this by extracting the soul seed into a `personalities/` directory and adding a `personality` field to `CoDeps`.

### 2.3 Preamble Messages Spec

**Codex:** The prompt (`prompt.md:32-49`) specifies a precise preamble protocol: before making tool calls, send a brief message explaining what you are about to do. Constraints: 8-12 words, logically group related actions, build on prior context, light and curious tone. Eight concrete examples are provided in the prompt itself.

**co-cli:** No preamble spec. The LLM decides whether and how to narrate tool calls.

**Tradeoff:** Preambles dramatically improve perceived responsiveness in long-running agentic loops. They cost ~zero implementation effort (it is purely a prompt addition). Worth adopting verbatim.

### 2.4 Plan Mode and the `update_plan` Tool

**Codex:** The `update_plan` tool (`tools/handlers/plan.rs`) is a display-only function: it accepts a list of `{step, status}` items and an explanation, emits a `PlanUpdate` event to the UI, and returns the string `"Plan updated"`. Critically, the plan is NOT re-injected into model context and has NO enforcement mechanism (`plan.rs:98-100` comment: "it's the _inputs_ to this function that are useful to clients, not the outputs"). The prompt instructs the model on when to use plans (non-trivial tasks, multiple steps) and when not to (straightforward tasks, single-step).

**co-cli:** No plan tool, no plan mode.

**Tradeoff:** This is a clever design: the plan tool creates structure for the user without polluting the model's context window. It costs almost nothing (a tool definition + an event emitter). co-cli should adopt this pattern. In pydantic-ai, it would be a tool that returns a display string and emits a structured event.

### 2.5 Two Kinds of Unknowns

**Codex:** The orchestrator prompt (`templates/agents/orchestrator.md`) distinguishes "discoverable facts" (things the agent can find by reading code or running commands) from "user preferences" (things only the human knows). The agent is instructed to resolve discoverable facts autonomously but pause and ask for preferences.

**co-cli:** No explicit taxonomy of unknowns. The soul seed says "ask clarifying questions" but does not distinguish discoverable vs preference unknowns.

**Tradeoff:** This distinction reduces unnecessary back-and-forth. The agent explores first, asks only when it genuinely cannot proceed. Worth adding as a rule.

### 2.6 Orchestrator Agent with Sub-Agent Parallelism

**Codex:** The orchestrator template (`templates/agents/orchestrator.md:87-106`) describes a sub-agent system: the main agent can `spawn_agent` to parallelize work, coordinate via `wait`/`send_input`, and the depth is limited to `MAX_THREAD_SPAWN_DEPTH = 1` (`agent/guards.rs:25`). The `Guards` struct tracks active sub-agents with atomic counters and a thread set.

**co-cli:** Single-agent, single-thread. No sub-agent support.

**Tradeoff:** Sub-agent parallelism is powerful for large-scope tasks but introduces significant complexity (resource tracking, context sharing, cost control). co-cli should skip this until it has a proven need. Pydantic-ai does not natively support sub-agent orchestration, so implementation would require a custom coordination layer.

### 2.7 Confidence-Scored Reviews

**Codex:** Reviews run as a separate `ReviewTask` (`tasks/review.rs`) that spawns a dedicated sub-codex conversation (`run_codex_thread_one_shot`). The review output is structured: `ReviewOutputEvent` with findings, each having title, body, code_location with line_range, and severity. The `review_format.rs` module renders findings with selectable checkboxes.

**co-cli:** No structured review system.

**Tradeoff:** Structured review output is useful for CI integration and actionable feedback. The sub-agent pattern is heavyweight; co-cli could get 80% of the value with a structured output schema on a single agent call.

## 3. Agent Loop: What Codex Does Differently

### 3.1 Channel-Based Event Loop

**Codex:** The core loop is `submission_loop` (`codex.rs:2671-2810`), which reads `Submission` messages from an `async_channel::Receiver` and dispatches on `Op` variants: `UserInput`, `Interrupt`, `ExecApproval`, `PatchApproval`, `Compact`, `Undo`, `Review`, `Shutdown`, and ~15 others. This is a message-passing architecture where the UI and agent communicate through typed channels, not direct function calls.

**co-cli:** Direct function calls: `run_turn()` is a method that processes one turn synchronously (from the caller's perspective), with approval handled via `DeferredToolRequests` in the same call stack.

**Tradeoff:** Channel-based decoupling enables headless operation, multiple frontends, and true async interrupts. But it adds indirection that makes debugging harder. co-cli's direct approach is appropriate for a single-frontend CLI. If co-cli ever needs a web UI or LSP mode, channels would be the right refactor.

### 3.2 Task Polymorphism

**Codex:** The `SessionTask` trait (`tasks/mod.rs:83-113`) defines `kind()`, `run()`, and `abort()`. Six concrete implementations: `RegularTask` (normal chat), `ReviewTask` (sub-codex review), `CompactTask` (context compaction), `GhostSnapshotTask` (git state capture for undo), `UndoTask` (restore from ghost snapshot), and `UserShellCommandTask` (user-initiated shell commands). Each task type has its own lifecycle, telemetry category, and cancellation behavior.

**co-cli:** Single task type: `run_turn()` handles everything. No undo, no compaction task, no review task.

**Tradeoff:** Task polymorphism is valuable when different operations need fundamentally different lifecycles (a ghost snapshot runs in the background during a regular task). co-cli could benefit from a lightweight version: a `Task` protocol with `Regular` and `Compact` variants, without the full trait hierarchy.

### 3.3 Parallel Tool Execution with RwLock

**Codex:** The `ToolCallRuntime` (`tools/parallel.rs:25-138`) uses a `RwLock<()>` to gate tool execution. Tools that declare `supports_parallel` acquire a read lock (concurrent with other parallel-safe tools). Tools that do not acquire a write lock (exclusive). Execution uses `tokio::select!` with a `CancellationToken` for abort. Results accumulate in a `FuturesOrdered` (`codex.rs:4658-4659`) to preserve ordering.

**co-cli:** Sequential tool execution via pydantic-ai's built-in tool calling. No parallel dispatch.

**Tradeoff:** Parallel tool execution matters when the model requests multiple independent reads (e.g., reading 5 files simultaneously). pydantic-ai does not expose a hook for parallel dispatch, so this would require wrapping tool execution. Worth considering only if profiling shows tool execution as a bottleneck.

### 3.4 Multi-Layer Retry with Transport Fallback

**Codex:** In `run_sampling_request` (`codex.rs:4115-4184`), retries operate at two layers: (1) stream-level retries with exponential backoff up to `stream_max_retries()`, and (2) transport-level fallback from WebSocket to HTTPS via `client_session.try_switch_fallback_transport()`. When stream retries exhaust, the system switches transports and resets the retry counter. The user is notified via `WarningEvent`.

**co-cli:** Single-layer retry in pydantic-ai. No transport switching (using standard HTTP only).

**Tradeoff:** Transport fallback is relevant for OpenAI's WebSocket API, which co-cli does not use. The stream retry pattern with user notification is worth adopting: surface reconnection status to the user instead of appearing frozen.

### 3.5 PTY Process Pool

**Codex:** The `UnifiedExecProcessManager` (`unified_exec/mod.rs:54`) manages up to `MAX_UNIFIED_EXEC_PROCESSES = 64` persistent PTY sessions. Processes are reused across tool calls (identified by `process_id`), have configurable yield times (`MIN_YIELD_TIME_MS = 250`, `MAX_YIELD_TIME_MS = 30_000`), and output is captured via a `HeadTailBuffer` that keeps head and tail of large outputs. Processes are terminated when a turn completes (`close_unified_exec_processes`).

**co-cli:** Spawns a fresh subprocess for each shell tool call via Docker or subprocess fallback. No reuse, no PTY.

**Tradeoff:** Process reuse reduces startup latency for multi-step shell workflows. PTY enables interactive programs. For co-cli's Docker-first sandbox, process reuse is harder (each `docker exec` is ephemeral). A session-scoped container that persists across tool calls would get similar benefits with less complexity.

### 3.6 Graceful Cancellation

**Codex:** Every task receives a `CancellationToken` (`tasks/mod.rs:130`). Cancellation flows: (1) `cancellation_token.cancel()` signals the task, (2) `tokio::select!` in every async path checks cancellation, (3) a 100ms grace period (`GRACEFULL_INTERRUPTION_TIMEOUT_MS`) waits for orderly shutdown, (4) `handle.abort()` force-kills if grace expires, (5) the task's `abort()` method runs cleanup, (6) a `<turn_aborted>` marker is inserted into conversation history. Aborted tool calls report wall time and "aborted by user" in their output.

**co-cli:** Interrupt via KeyboardInterrupt in the REPL, which kills the current turn. No grace period, no abort marker in history, no structured cleanup.

**Tradeoff:** Graceful cancellation with history markers is important: without the abort marker, the model on the next turn does not know the previous turn was interrupted and may repeat work or miss partial state. co-cli should adopt the abort marker pattern.

### 3.7 Context Compaction (vs co-cli's Summarization)

**Codex:** Context compaction runs as a `CompactTask` using a dedicated prompt (`templates/compact/prompt.md`) that asks for a "handoff summary for another LLM." The summary is prefixed with `summary_prefix.md` that frames it as continuation. Auto-compaction triggers when `total_usage_tokens >= auto_compact_token_limit`. User messages are truncated to `COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000` during compaction. There is also a remote compaction path for OpenAI's API.

**co-cli:** Summarization via a history processor that runs an LLM call when the sliding window is exceeded. Inlined in the history processor pipeline, not a separate task.

**Tradeoff:** Both approaches solve the same problem. Codex's task-based approach is cleaner (isolated lifecycle, dedicated prompt), while co-cli's inline approach is simpler. co-cli's current design is adequate; the main improvement would be adopting the "handoff summary" framing which produces more actionable summaries.

### 3.8 Sub-Agent Depth Limit

**Codex:** `MAX_THREAD_SPAWN_DEPTH = 1` (`agent/guards.rs:25`), meaning the root agent can spawn children, but children cannot spawn grandchildren. The `Guards` struct enforces this via `reserve_spawn_slot` with atomic counters. At depth >= MAX, collaboration features are disabled (`codex.rs:287-289`).

**co-cli:** No sub-agent support, so depth limits are not applicable.

**Tradeoff:** The depth limit is a safety mechanism against recursive agent spawning. If co-cli ever adds sub-agents, a similar guard (even a simple integer check) is essential.

## 4. Techniques to Adopt

### 4.1 Preamble Messages Spec

**What:** Add a rule instructing the model to send 8-12 word updates before tool calls.
**Why:** Eliminates "frozen screen" perception during multi-tool sequences. Zero implementation cost.
**Sketch:** Add a rule file `prompts/rules/XX_responsiveness.md` with the preamble spec and examples, adapted from Codex's `prompt.md:32-49`.

### 4.2 Display-Only Plan Tool

**What:** An `update_plan` tool that accepts `[{step, status}]`, emits a display event, and returns "Plan updated."
**Why:** Gives users visibility into multi-step work without consuming context window. The plan is never re-injected.
**Sketch:** Register a `plan` tool via `agent.tool()` that validates the schema, emits structured output via `display`, and returns a confirmation string. Add a prompt rule for when to use plans (multi-step tasks) and when not to (single-step tasks).

### 4.3 Personality as Swappable Module

**What:** Extract the soul seed into `prompts/personalities/default.md`, add `friendly.md` and `pragmatic.md` variants. Add a `personality` field to `CoDeps`.
**Why:** Enables team experimentation with tone. Users who want a warmer or more concise assistant can switch without touching behavioral rules.
**Sketch:** Assembly reads `prompts/personalities/{personality}.md` and substitutes into a slot in the instructions. The personality name comes from settings or a `/personality` slash command.

### 4.4 Abort Marker in History

**What:** When a turn is interrupted, inject a `<turn_aborted>` message into conversation history before the next turn.
**Why:** Without this, the model does not know the previous turn was interrupted. It may repeat completed work or fail to verify partial state.
**Sketch:** In the interrupt handler, append a user-role message: `"<turn_aborted>\nThe user interrupted the previous turn. Verify current state before retrying.\n</turn_aborted>"`. This is a history-only message, not displayed to the user.

### 4.5 Two-Category Unknown Taxonomy

**What:** Add a rule distinguishing "discoverable facts" (read code, run commands) from "user preferences" (only the human knows).
**Why:** Reduces unnecessary questions. The agent explores autonomously for discoverable facts and only asks for genuine preferences.
**Sketch:** Add to the identity or workflow rule: "Before asking the user a question, determine if the answer is discoverable through your tools. If so, discover it. Only ask the user for decisions that depend on their preferences, priorities, or constraints."

### 4.6 Handoff-Style Compaction Prompt

**What:** Reframe the summarization prompt as a "handoff summary for another LLM that will resume."
**Why:** Produces more actionable summaries that preserve next steps, decisions, and constraints.
**Sketch:** Update the summarization prompt in the sliding-window history processor to use Codex's framing: include current progress, key decisions, remaining work, and critical references.

### 4.7 Retry with User Notification

**What:** Surface reconnection/retry status to the user during streaming failures.
**Why:** A frozen screen with no feedback is worse than a "Reconnecting... 2/3" message.
**Sketch:** In the streaming loop's error handler, emit a warning via `console` before retrying. pydantic-ai exposes error callbacks that could be hooked for this.

## 5. Techniques to Skip

### 5.1 RwLock-Gated Parallel Tool Execution

**Why skip:** pydantic-ai manages tool execution internally. Wrapping it with an RwLock would require either forking the framework or building a custom dispatch layer. The performance gain is marginal for a Python CLI that talks to one LLM provider over HTTP.

### 5.2 Channel-Based Event Loop

**Why skip:** co-cli has one frontend (terminal REPL). Channel decoupling adds indirection without a second consumer. If a web UI is added later, this becomes relevant; until then, direct function calls are simpler and more debuggable.

### 5.3 PTY Process Pool

**Why skip:** co-cli uses Docker containers for sandboxing. PTY pools are a Rust-native optimization for persistent shell sessions. In Python with Docker, the equivalent is a persistent container with `docker exec`, which co-cli could adopt independently without PTY machinery.

### 5.4 Sub-Agent Orchestration

**Why skip:** Requires a coordination layer, resource tracking, and cost controls. pydantic-ai has no native multi-agent orchestration. The engineering cost is high and the benefit is limited for co-cli's current scope (single-user CLI for personal productivity).

### 5.5 Ghost Snapshot / Undo System

**Why skip:** Codex's undo system (`GhostSnapshotTask` + `UndoTask`) requires deep git integration (ghost commits, worktree restore). It is a substantial feature with significant edge cases. co-cli should recommend users rely on git directly rather than building a parallel undo system.

### 5.6 Structured Review Output

**Why skip for now:** The sub-agent review system with `ReviewOutputEvent`, severity-scored findings, and checkbox selection is a substantial feature. co-cli can get review functionality through prompt instructions alone. If CI integration becomes a goal, revisit this.

## 6. Open Questions

**Q1: Template-based prompt assembly.** Codex uses `{{ personality }}` template substitution in model instruction templates. Should co-cli adopt a template engine (even `str.format`) for prompt assembly, or keep the current concatenation approach? Template substitution is more flexible but adds a layer of indirection.

**Q2: Plan tool scope.** Codex's `update_plan` is display-only and never re-injected. Some systems re-inject the plan as context to improve adherence. Which approach produces better outcomes in practice? This needs A/B testing.

**Q3: Per-model prompt variants.** Codex maintains separate prompt files per model family (`prompt.md`, `gpt_5_1_prompt.md`, `gpt_5_2_prompt.md`, `gpt-5.2-codex_prompt.md`). co-cli currently has model quirks appended. As co-cli supports more models (Gemini, Ollama, Claude), should it adopt per-model prompt files or keep the single-prompt-with-quirks approach?

**Q4: Compaction as a separate task vs inline.** Codex runs compaction as a dedicated `CompactTask` with its own lifecycle. co-cli does it inline in a history processor. Does the task-based approach improve reliability or observability enough to justify the refactor?

**Q5: WebSocket streaming.** Codex uses WebSocket with HTTPS fallback for streaming. If co-cli moves to providers that offer WebSocket APIs, should it adopt dual-transport with fallback? This is provider-dependent and premature until co-cli has a WebSocket provider.
