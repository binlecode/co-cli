# REVIEW: Agent Loop Architectures Across Peer CLI Systems

Long-running agent loop analysis across all 5 peer systems. Focus: orchestration model, concurrency, background execution, retry/backoff, sub-agents, loop detection.

**Related review:** [`REVIEW-prompts-peer-systems.md`](REVIEW-prompts-peer-systems.md) — system prompts, composition strategies, landscape positioning

---

## Table of Contents

1. [Codex (Rust/Tokio) — Most Sophisticated](#1-codex)
2. [Claude Code (TypeScript) — Hook-Driven](#2-claude-code)
3. [OpenCode (TypeScript/Bun) — Clean State Machine](#3-opencode)
4. [Gemini CLI (TypeScript) — Scheduler + Loop Detection](#4-gemini-cli)
5. [Aider (Python) — Simplest, Synchronous](#5-aider)
6. [Cross-System Comparison](#6-cross-system-comparison)
7. [Key Design Patterns Across Systems](#7-key-design-patterns-across-systems)
8. [Techniques Worth Adopting for co-cli](#8-techniques-worth-adopting-for-co-cli)

---

## 1. Codex

**Repo:** `~/workspace_genai/codex` (Rust/Tokio) — Most Sophisticated

**Key files:** `codex.rs` (6787 lines), `tasks/mod.rs`, `tools/parallel.rs`, `tools/orchestrator.rs`, `unified_exec/mod.rs`

**Architecture:**

```
User Input (Op) → submission_loop() [infinite async channel receiver]
    → Session::spawn_task() [tokio::spawn with CancellationToken]
        → SessionTask::run() [RegularTask | ReviewTask | CompactTask | GhostSnapshotTask | UndoTask]
            → run_turn() [multi-round loop]
                → run_sampling_request() [retry wrapper with exponential backoff]
                    → try_run_sampling_request() [stream loop]
                        → ResponseStream.next() yields events:
                            OutputTextDelta → stream to UI
                            OutputItemDone → dispatch tool calls
                            Completed → exit
                        → ToolCallRuntime::handle_tool_call()
                            → tokio::spawn per call
                            → FuturesOrdered collects results
                → needs_follow_up? → loop back or exit
```

**Design patterns:**

- **Channel-based event loop:** `submission_loop()` receives `Op` variants (UserInput, Interrupt, ExecApproval, Shutdown) via async channel. Decouples UI from execution
- **Task polymorphism:** `SessionTask` trait with 5 implementations. Uniform lifecycle: spawn → run → abort → cleanup
- **Parallel tool execution:** `RwLock::read()` for parallel-safe tools, `RwLock::write()` for serial-exclusive tools. Results collected in `FuturesOrdered` to preserve call order
- **Multi-layer retry:** Stream retries with exponential backoff per provider. Transport fallback: WebSocket → HTTPS. Sandbox retry on denial (re-run without sandbox, cached approval avoids re-prompt)
- **Graceful cancellation:** `CancellationToken` threaded through all async work. `tokio::select!` for abort-aware operations. 100ms timeout for cleanup

**Background execution:**

- **Ghost snapshots:** Git snapshots via `spawn_blocking()` — main turn continues while snapshot runs. Watchdog task warns after 240s
- **PTY process pool:** Max 64 concurrent processes. Background reader via `async_watcher::spawn_background_reader()`. Processes reused across tool calls
- **MCP connections:** Persistent connections to external servers, lazy init, background health monitoring

**Sub-agents:** Hierarchical agents via `collab.rs`, max depth 3

**Concurrency primitives:** `tokio::spawn`, `CancellationToken`, `tokio::sync::Mutex`, `tokio::sync::RwLock`, `async_channel`, `FuturesOrdered`, `tokio::select!`

---

## 2. Claude Code

**Repo:** `~/workspace_genai/claude-code` (TypeScript) — Hook-Driven

**Key files:** Closed-source core. Observable via plugin API: `plugins/ralph-wiggum/hooks/stop-hook.sh`, `plugins/feature-dev/`, `plugins/hookify/`

**Architecture (inferred from plugin API and hook events):**

```
User Input → SessionStart hooks
    → LLM streaming (tool calls generated)
        → PreToolUse hooks [validation/permission]
        → Tool execution (Bash, Read, Write, Grep, etc.)
        → PostToolUse hooks [feedback/logging]
        → Results → LLM → more tool calls or Stop
    → Stop hooks [completion validation]
    → SessionEnd hooks
```

**Design patterns:**

- **Event-driven architecture:** 8 hook events expose the loop structure: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PreCompact, Stop, SubagentStop, SessionEnd, Notification
- **Hook-based permission:** Hooks return `"decision": "allow"|"deny"|"ask"`. Can modify `tool_input` fields. Exit code 2 = deny with feedback to LLM
- **Ralph loop pattern:** Self-referential agent loop via Stop hook. Stop hook intercepts exit, checks transcript for completion promise (`<promise>COMPLETE</promise>`), feeds same prompt back via `"reason": $prompt` if not done. Iterates until max iterations or promise detected. No external bash loop needed — loop lives inside the session
- **Sub-agent spawning:** `Task` tool with agent definitions in `agents/*.md` frontmatter (name, model, tools). Per-agent model selection (haiku/sonnet/opus) for cost optimization

**Background execution:** Agent teams research preview (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`). Background agents prompt for permissions before launching

**Concurrency:** Parallel sub-agent spawning via Task tool. Async/promises (TypeScript, inferred)

---

## 3. OpenCode

**Repo:** `~/workspace_genai/opencode` (TypeScript/Bun) — Clean State Machine

**Key files:** `session/prompt.ts` (1867 lines), `session/processor.ts` (408 lines), `session/retry.ts`, `session/llm.ts`, `agent/agent.ts`, `tool/task.ts`

**Architecture:**

```
User Input → SessionPrompt.prompt()
    → create user message
    → SessionPrompt.loop() [while true]:
        1. Load & filter message history (exclude compacted)
        2. Check finish conditions (end_turn/stop → break)
        3. Handle pending subtasks → child session via SessionPrompt.prompt()
        4. Handle pending compaction → SessionCompaction.process()
        5. Check context overflow → auto-compact if > 90% model limit
        6. Resolve tools for agent
        7. SessionProcessor.create() → processor.process():
            → LLM.stream() → fullStream loop:
                text-delta → update part, publish
                tool-call → create part, doom loop check (3x identical → ask)
                tool-result → update part
                finish-step → calculate usage, snapshot, overflow check
                error → throw for retry
            → return "continue" | "stop" | "compact"
        8. Loop back or exit
```

**Design patterns:**

- **Dual-loop structure:** Outer loop = agent turns (prompt.ts). Inner loop = retry attempts (processor.ts). State machine return values control flow
- **Doom loop detection:** 3 consecutive identical tool calls (name + args) → ask user permission before continuing
- **Task queue:** Subtasks and compaction requests queued as message parts, processed FIFO in main loop
- **Retry with Retry-After:** Exponential backoff (2s → 4s → 8s → 16s → 30s cap). Respects `Retry-After` and `Retry-After-Ms` headers from provider. Status published with `{ type: "retry", attempt, next }` for UI countdown
- **Abortable sleep:** Retry delays interruptible via `AbortSignal`

**Background execution:** Fire-and-forget for title generation, session sharing, summary generation. No worker pool or job queue

**Sub-agents:** `task.ts` creates child sessions. `explore` and `general` agent modes. Sequential execution (no parallel sub-agents)

**Agent modes:** `build` (primary, full tools), `plan` (primary, no edit tools), `explore` (subagent, read-only), `general` (subagent, full tools). Permission system: ruleset of `{ permission, pattern, action: allow|deny|ask }`

**Concurrency:** Single-threaded async/await. `AbortController` for cancellation. AI SDK handles `Promise.all` for parallel tool calls within a single LLM response

---

## 4. Gemini CLI

**Repo:** `~/workspace_genai/gemini-cli` (TypeScript) — Scheduler + Loop Detection

**Key files:** `core/turn.ts`, `scheduler/scheduler.ts` (517 lines), `agents/local-executor.ts` (1197 lines), `services/loopDetectionService.ts` (605 lines), `core/geminiChat.ts` (1008 lines)

**Architecture:**

```
User Input → Turn.run() [async generator yielding events]
    → LLM stream → events: Content, ToolCallRequest, ToolCallResponse, Thought, Error, Finished
    → Scheduler [queue-based, sequential]:
        → State machine: validating → scheduled → executing → success/error/cancelled
        → Policy check → ask user if needed → execute → update policy
    → Loop continues until LLM stops calling tools

Sub-agent path:
    Agent Start → executeTurn() loop
        → Tool Calls → complete_task tool → Result
        → Time limit + max turns enforced
        → Grace period recovery on timeout
```

**Design patterns:**

- **Queue-based scheduler:** Sequential tool execution (not parallel). State machine tracks each tool call. Policy-based approval at tool invocation time
- **Async generator streaming:** `Turn.run()` is `async *run()` yielding typed events. Callers consume with `for await`
- **Grace period recovery:** After timeout or max-turns, agent gets one extra turn to call `complete_task` and return partial results
- **Model fallback routing:** Availability checks → fallback to alternate model on failure
- **Content retry:** Up to 2 attempts for invalid LLM responses (empty content, missing finish reason)

**3-tier loop detection (unique among peers):**

1. **Tool call loop (hash-based):** Detects 5+ consecutive identical tool calls (name + args hash). Immediate termination
2. **Content loop (sliding window):** Tracks 50-char chunks of streaming text. Detects 10+ repetitions of same chunk within 5×chunk-size distance. Code-block aware (disabled inside ```)
3. **LLM-based loop detection:** After 30 turns, queries loop-detection model every 3-15 turns using last 20 turns of history. Dual-model confidence check (Flash + main model). Confidence threshold: 0.9. Adaptive interval based on confidence

**Background execution:** None — all synchronous within event loop

**Sub-agents:** Full recursive agent system with isolated tool registries. Local executor with time limits and max turns. Mandatory `complete_task` tool to signal completion

---

## 5. Aider

**Repo:** `~/workspace_genai/aider` (Python) — Simplest, Synchronous

**Key files:** `coders/base_coder.py` (2485 lines), `commands.py`, `waiting.py`, `sendchat.py`, `io.py`

**Architecture:**

```
main() → Coder.create()
    → while True:                              # run()
        user_message = get_input()             # blocking prompt_toolkit
        run_one(user_message)
            → while message:                   # reflection loop (max 3)
                send_message(message)
                    → while True:              # retry loop
                        send() → yield from show_send_output_stream()
                        break on success
                      except LiteLLMException:
                        retry with exponential backoff (0.125s doubling → RETRY_TIMEOUT)
                      except FinishReasonLength:
                        multi-response continuation with assistant prefill
                      except KeyboardInterrupt:
                        add interrupt message to history, return to prompt
                apply_updates()                # file edits
                auto_commit()
                auto_lint()                    # reflection trigger
                auto_test()                    # reflection trigger
                if reflected_message:
                    message = reflected_message
                    continue                   # loop back for another LLM call
                else:
                    break
```

**Design patterns:**

- **Generator-based streaming:** `yield from send()` allows streaming while maintaining synchronous control flow
- **Reflection loop:** Lint/test failures → error message becomes next prompt. Max 3 reflections. Self-correction without user intervention
- **Multi-response continuation:** On `FinishReasonLength`, appends partial response as assistant prefill message, re-sends. Accumulates across multiple continuations
- **Double Ctrl-C:** First interrupt stops current LLM call, returns to prompt. Second within 2 seconds exits application
- **Retry with exponential backoff:** 0.125s → 0.25s → 0.5s → ... → `RETRY_TIMEOUT` (model-specific). Handles rate limits and transient errors

**Background execution:**

- **Cache warming thread:** Daemon thread sends 1-token keepalive requests every ~5 minutes to warm prompt cache. Runs while `ok_to_warm_cache=True`
- **Summarization thread:** Background thread summarizes old chat history using weak model. Joined before next message send
- **WaitingSpinner:** Daemon thread displays animated spinner during LLM calls

**Sub-agents:** None. Single sequential coder

**Concurrency:** Pure synchronous. No async/await. Daemon threads for UI and cache warming only

---

## 6. Cross-System Comparison

| Feature | Codex | Claude Code | OpenCode | Gemini CLI | Aider |
|---------|-------|-------------|----------|------------|-------|
| **Language** | Rust | TypeScript | TypeScript | TypeScript | Python |
| **Async runtime** | Tokio | Node.js (inferred) | Bun/Node | Node.js | None (sync) |
| **Loop model** | Channel receiver + task spawn | Hook-driven streaming | While-true + state machine | Async generator + scheduler | While-true + reflection |
| **Parallel tool calls** | Yes (RwLock) | Yes (Task tool) | Yes (AI SDK) | No (queue) | No |
| **Sub-agent depth** | 3 levels | Unlimited (inferred) | 1 level | Recursive | None |
| **Background tasks** | Snapshots, PTY pool, MCP | Agent teams (experimental) | Fire-and-forget | None | Daemon threads |
| **Retry/backoff** | Multi-layer + transport fallback | Hook-based | Exp backoff + Retry-After headers | Multi-layer + model fallback | Exp backoff |
| **Loop detection** | None found | None found | Doom loop (3x identical) | 3-tier (hash + window + LLM) | None |
| **Cancellation** | `CancellationToken` | `AbortController` (inferred) | `AbortController` | `AbortSignal` | `KeyboardInterrupt` |
| **Context compaction** | Auto-compact task type | Auto + PreCompact hook | Auto (90% threshold) | Not found | Summarization thread |
| **Process reuse** | PTY pool (max 64) | Unknown | None | None | None |
| **State persistence** | In-memory session | JSONL transcripts | JSON files on disk | Not found | In-memory only |

---

## 7. Key Design Patterns Across Systems

**Universal pattern — while-true agentic loop:** All 5 systems use a variant of `while true: call LLM → execute tools → check if done → loop`. The variation is in what wraps the loop (channel receiver, hook system, state machine, async generator, reflection).

**Retry strategies:**

| System | Initial Delay | Backoff | Max Delay | Special |
|--------|--------------|---------|-----------|---------|
| Codex | Provider-specific | Exponential | Provider-specific | WebSocket → HTTPS fallback |
| OpenCode | 2s | 2× exponential | 30s | Respects `Retry-After` headers |
| Gemini CLI | Not specified | Exponential | Not specified | Model fallback routing |
| Aider | 0.125s | 2× exponential | Model-specific `RETRY_TIMEOUT` | Assistant prefill on length exceeded |

**Cancellation approaches:**

- **Structured (Codex):** `CancellationToken` threaded through all spawned tasks, `tokio::select!` for abort-aware operations, 100ms cleanup timeout
- **Signal-based (OpenCode, Gemini CLI):** `AbortController`/`AbortSignal` propagated through call stack. Abortable sleep for retry delays
- **Interrupt-based (Aider):** `KeyboardInterrupt` caught at send boundary. Partial response preserved in history. Double-interrupt for exit

**Tool execution models:**

- **Parallel with ordering (Codex):** `FuturesOrdered` — all calls spawn immediately, results returned in order
- **AI SDK parallel (OpenCode):** Model returns multiple tool calls, SDK runs `Promise.all` internally
- **Sequential queue (Gemini CLI):** One tool at a time, state machine per call
- **Blocking inline (Aider):** Tools are slash commands executed synchronously

---

## 8. Techniques Worth Adopting for co-cli

**Priority 1 — Loop safety:**

1. **Doom loop detection (OpenCode):** 3 identical consecutive tool calls → ask user. Cheap, zero false positives. Minimum viable loop guard
2. **LLM-based loop detection (Gemini CLI):** After 30 turns, use fast model to classify "stuck vs making progress." Catches cognitive loops that hash-based detection misses. Adaptive check interval based on confidence

**Priority 2 — Resilience:**

3. **Retry-After header respect (OpenCode):** Parse `Retry-After` and `Retry-After-Ms` from provider response headers. Better than blind exponential backoff for rate limits
4. **Grace period recovery (Gemini CLI):** On timeout/max-turns, give agent one extra turn to return partial results. Prevents total loss of long-running work
5. **Multi-response continuation (Aider):** On output token limit, append partial response as assistant prefill and re-send. Handles long code generation gracefully

**Priority 3 — Architecture:**

6. **State machine return values (OpenCode):** Processor returns `continue | stop | compact` — clean separation between "what happened" and "what to do next"
7. **Reflection loop (Aider):** Auto-lint and auto-test failures become the next prompt. Self-correction without user intervention, bounded by max reflections
8. **Abortable retry sleep (OpenCode):** Retry delays interruptible by abort signal. User doesn't wait for backoff timer when they want to cancel

**Research items (observe, don't implement yet):**

9. **Parallel tool execution with RwLock (Codex):** Requires tool safety classification (parallel-safe vs serial-exclusive). High implementation cost, high throughput benefit
10. **PTY process pool (Codex):** Reuse shell sessions across tool calls. Performance win for shell-heavy workflows. Complexity: process lifecycle, max pool, cleanup
11. **Ralph loop pattern (Claude Code):** Self-referential agent via completion hooks. Elegant for autonomous long-running tasks. Requires hook system first

---

**Research completed:** 2026-02-13
**Systems analyzed:** Codex, Gemini CLI, OpenCode, Aider, Claude Code
**Source repos:** All 5 cloned in `~/workspace_genai/`

**Related documents:**
- [REVIEW-prompts-peer-systems.md](REVIEW-prompts-peer-systems.md) — system prompts, composition, landscape
