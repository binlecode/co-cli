# TAKEAWAY: OpenCode Comparative Analysis

## 1. Executive Summary

OpenCode is a TypeScript-based coding agent CLI (anomalyco/opencode) that ships per-model system prompts, a dual-loop agent architecture with doom-loop detection and compaction-then-continue, and a sub-agent task system with typed agent modes. It is the most architecturally mature peer we have studied. The key strategic insight for co-cli: OpenCode's robustness comes from three things we lack -- per-model prompt routing, a structured inner retry / outer turn loop, and doom-loop detection -- all of which are implementable in pydantic-ai without major refactors.

## 2. Prompt Design: What OpenCode Does Differently

### 2.1 Per-Model Prompt Routing

**What it is.** `SystemPrompt.provider()` in `session/system.ts:19-27` selects an entirely different system prompt file based on model ID substring matching. Claude gets `anthropic.txt`, Gemini gets `gemini.txt`, GPT-5 gets `codex_header.txt`, GPT-3.5/4/o1/o3 get `beast.txt`, and everything else falls through to `qwen.txt`. Each file is 50-150 lines with model-specific tone, tool guidance, and examples.

**What co-cli does instead.** One prompt for all models. Model-specific quirks are limited to inference parameters (temperature, num_ctx) via `model_quirks.py`, not prompt content.

**Tradeoff.** Per-model prompts let OpenCode exploit each model's strengths (Claude handles TodoWrite well, Gemini needs explicit convention reminders, beast-mode GPT needs "keep going" pressure). Cost: 6 prompt files to maintain; drift risk if tool descriptions change. co-cli's single-prompt approach is simpler but leaves performance on the table for non-primary models.

### 2.2 Professional Objectivity as Explicit Directive

**What it is.** The Anthropic prompt (`anthropic.txt:21`) includes a dedicated section: "Prioritize technical accuracy and truthfulness over validating the user's beliefs... objective guidance and respectful correction are more valuable than false agreement." This is not in any other model's prompt.

**What co-cli does instead.** Rule 03 (reasoning) covers truthfulness but via tool-verification framing ("Always verify claims"), not as a social-interaction directive.

**Tradeoff.** The explicit anti-sycophancy directive counteracts a known Claude weakness. co-cli could add this to the identity rule at zero cost.

### 2.3 Agent-as-Mode (Plan/Build/Explore/General)

**What it is.** `Agent.Info` in `agent/agent.ts:24-48` defines agents with `mode` (primary/subagent/all), per-agent permissions, optional model override, step limits, and custom prompts. The "plan" agent is a read-only mode that forbids edits (`plan.txt` injects `STRICTLY FORBIDDEN: ANY file edits`), uses a plan-file workflow, and transitions to "build" via `build-switch.txt`. Sub-agents (explore, general) are launched via the Task tool into child sessions.

**What co-cli does instead.** Single agent, no mode concept, no sub-agents. All tool permissions are binary (requires_approval or not).

**Tradeoff.** OpenCode's agent system enables plan-then-execute workflows and parallel sub-agent exploration, but adds significant complexity (child sessions, permission inheritance, synthetic user messages for model compatibility). co-cli's single-agent design is simpler and adequate for conversational use, but lacks structured planning capability.

### 2.4 TodoWrite as Model-Specific Tool

**What it is.** The Anthropic prompt includes a long "Task Management" section with TodoWrite examples (`anthropic.txt:23-67`). The Gemini prompt omits TodoWrite entirely, relying instead on inline markdown checklists. The tool registry conditionally enables TodoWrite/TodoRead based on agent permissions (`registry.ts:109-110`). TodoRead is even commented out globally.

**What co-cli does instead.** No task-tracking tool. Memory tool serves a different purpose (cross-session knowledge).

**Tradeoff.** TodoWrite gives the LLM explicit progress tracking, which improves multi-step task completion rates. However, it is high-overhead for simple queries. co-cli should consider this only if it adds plan-mode capability.

### 2.5 Synthetic Part Injection

**What it is.** Throughout `prompt.ts`, OpenCode injects synthetic text parts that the model sees but the user does not (flagged `synthetic: true`). Examples: wrapping mid-loop user messages in `<system-reminder>` tags (lines 592-607), adding "Continue if you have next steps" after compaction (compaction.ts:178-189), injecting "Summarize the task tool output above" after subtask completion (prompt.ts:490). These steer the model without polluting user-visible history.

**What co-cli does instead.** No synthetic message injection. History processors trim but do not augment.

**Tradeoff.** Synthetic parts are a powerful steering mechanism, especially for multi-turn continuation. The `<system-reminder>` wrap for queued user messages during agentic loops is particularly clever -- it prevents the model from treating a mid-loop user message as a new top-level request.

### 2.6 Instruction File Discovery

**What it is.** `InstructionPrompt` in `instruction.ts` searches upward from the working directory for `AGENTS.md`, `CLAUDE.md`, or `CONTEXT.md`, plus global instruction files from `~/.claude/CLAUDE.md` and config-specified paths (including HTTP URLs). When the Read tool opens a file, `resolve()` walks parent directories looking for unclaimed instruction files to inject. This gives hierarchical project context.

**What co-cli does instead.** Static `instructions.md` bundled in the package. No project-level instruction discovery.

**Tradeoff.** OpenCode's approach is more flexible for multi-project use, but co-cli's design philosophy is that all context should come through tools (memory, knowledge), not baked into prompts. The hierarchical discovery pattern is worth noting but may conflict with co-cli's dynamic-knowledge architecture.

## 3. Agent Loop: What OpenCode Does Differently

### 3.1 Dual-Loop Structure (Outer Turns + Inner Retry)

**What it is.** The outer loop lives in `SessionPrompt.loop()` (`prompt.ts:267-653`). It is a `while(true)` that processes turns: check for pending subtasks, check for compaction needs, then call `processor.process()` for normal LLM interaction. The inner loop lives in `SessionProcessor.create().process()` (`processor.ts:45-402`), which is also a `while(true)` that handles streaming, tool execution, error catching, and retry. The inner loop returns one of three values: `"stop"`, `"continue"`, or `"compact"`.

The outer loop uses these return values as its control flow: `"stop"` breaks, `"continue"` proceeds to the next iteration, `"compact"` triggers compaction and continues.

**What co-cli does instead.** `run_turn()` is a flat state machine. There is no explicit inner retry loop -- pydantic-ai handles tool call retries internally, and error retry is ad-hoc.

**Tradeoff.** The dual-loop gives OpenCode clean separation of concerns: the inner loop handles a single LLM call with retry, the outer loop manages multi-turn orchestration. co-cli conflates these, which makes adding features like compaction or subtask execution harder.

### 3.2 Doom Loop Detection (3 Identical Calls -> Permission Gate)

**What it is.** In `processor.ts:143-168`, on every `tool-call` event, the processor checks the last `DOOM_LOOP_THRESHOLD` (3) tool parts. If all three are the same tool with identical input (JSON-stringified comparison), it triggers a permission gate via `PermissionNext.ask()` with permission key `doom_loop`. This pauses execution and asks the user to confirm. The agent's default permission for `doom_loop` is `"ask"` (`agent.ts:57`).

**What co-cli does instead.** No doom loop detection. A misbehaving model can call the same tool indefinitely until the sliding window kicks in or the user interrupts.

**Tradeoff.** This is cheap to implement and high-value. It catches the most common failure mode in agentic coding (model repeatedly trying the same edit or search that keeps failing). co-cli should adopt this.

### 3.3 Compaction Flow (Compact-Then-Continue)

**What it is.** When `SessionCompaction.isOverflow()` detects the context exceeds usable input tokens (`compaction.ts:30-39`), the outer loop creates a compaction marker and re-enters. A dedicated "compaction" agent with no tools generates a detailed summary. After compaction, a synthetic "Continue if you have next steps" user message is injected, and the loop continues with the summarized context.

Additionally, `SessionCompaction.prune()` (`compaction.ts:49-90`) runs at loop exit: it walks backwards through tool outputs, keeping the last 40K tokens worth of tool results intact and marking older ones as compacted (their output is stripped from future context).

**What co-cli does instead.** `truncate_history_window` uses a sliding window with LLM summarization, which is architecturally similar but triggered differently (as a history processor on every call rather than threshold-based).

**Tradeoff.** OpenCode's approach is more sophisticated -- it separates pruning (cheap, always runs) from compaction (expensive, threshold-triggered), and the compact-then-continue pattern preserves task momentum. co-cli's history processor approach is adequate but lacks the "continue" re-entry that keeps multi-step tasks alive.

### 3.4 Task Queue for Subtasks

**What it is.** The outer loop in `prompt.ts:291-301` scans recent messages for `compaction` and `subtask` parts. When it finds a pending subtask (`prompt.ts:329-496`), it creates a child session via the Task tool, runs it to completion, then injects a synthetic user message ("Summarize the task tool output above and continue") to keep the parent loop going. This enables parallel sub-agent execution through multiple tool calls in a single response.

**What co-cli does instead.** No sub-agent or task delegation capability.

**Tradeoff.** Sub-agents are powerful for large tasks (explore codebase in parallel, then synthesize) but add session management complexity. co-cli should not adopt this until it has a clear use case beyond the conversational pattern.

### 3.5 Abortable Retry Sleep

**What it is.** `SessionRetry.sleep()` (`retry.ts:11-26`) uses an AbortSignal-aware timer. When the user cancels during a retry wait, the abort handler fires immediately. The retry delay uses exponential backoff with `Retry-After` header parsing (both `retry-after-ms` and `retry-after` formats). The session status is set to a `"retry"` state with attempt count, message, and next retry timestamp, giving the UI real-time visibility.

**What co-cli does instead.** No retry sleep mechanism. Failed LLM calls surface as errors.

**Tradeoff.** The abortable retry with status visibility is a clearly better UX. co-cli should adopt this, especially for rate-limited APIs.

### 3.6 State Machine Return Values

**What it is.** `processor.process()` returns a typed union: `"stop"`, `"continue"`, or `"compact"`. The outer loop pattern-matches on these to decide next action. This is explicit and self-documenting.

**What co-cli does instead.** `run_turn()` returns None and relies on state in the chat loop closure.

**Tradeoff.** Typed return values make the control flow testable and composable. co-cli should adopt this pattern when refactoring the agent loop.

### 3.7 Tool-Specific Model Adaptation

**What it is.** `ToolRegistry.tools()` in `registry.ts:126-159` conditionally swaps tools based on model ID. GPT-5 gets `apply_patch` instead of `edit`/`write`. WebSearch and CodeSearch are gated behind provider ID checks. `ProviderTransform.schema()` normalizes JSON schemas per provider (e.g., Anthropic rejects empty content).

**What co-cli does instead.** All tools are registered uniformly regardless of model.

**Tradeoff.** Model-specific tool adaptation is valuable for models with known strengths (GPT-5's patch format). But co-cli currently only supports two providers (Gemini, Ollama), so the payoff is low until provider diversity increases.

## 4. Techniques to Adopt

### 4.1 Doom Loop Detection

**Why.** Highest-value safety feature we are missing. Without it, a confused model can burn tokens and user patience.

**Sketch.** In `run_turn()`, before executing each tool call from `DeferredToolRequests`, check the last N tool calls in message history. If the same tool + same args appear 3 times, convert it to a `requires_approval=True` deferred request regardless of the tool's original setting. Log a warning. Implementation: ~30 lines in the chat loop, comparing `tool_name + json.dumps(args, sort_keys=True)`.

### 4.2 Abortable Retry with Status

**Why.** Rate limiting is common with Gemini free tier. Currently, errors just surface. A retry loop with backoff, Retry-After header support, and "retrying in Xs" status would dramatically improve UX.

**Sketch.** Wrap `agent.run_stream_events()` in a retry loop (3 attempts max). On retryable errors (429, 503, 529), parse `Retry-After` header, display countdown via `display.console`, and use `asyncio.sleep()` with cancellation support. Return a typed result: `"ok"`, `"error"`, `"cancelled"`.

### 4.3 Typed Loop Return Values

**Why.** Makes `run_turn()` testable and composable. Currently, the function's control flow is implicit.

**Sketch.** Define `TurnResult = Literal["continue", "stop", "error", "compact"]`. Have `run_turn()` return this. The outer chat loop pattern-matches: `"continue"` prompts for next input, `"stop"` exits, `"error"` displays and continues, `"compact"` triggers summarization then continues.

### 4.4 Anti-Sycophancy Directive

**Why.** Zero-cost prompt improvement. Claude (and Gemini) both tend toward agreement over correction.

**Sketch.** Add to rule 01 (identity): "Prioritize technical accuracy over agreement. If the user's assumption is wrong, say so directly with evidence. Respectful correction is more valuable than false validation."

### 4.5 Synthetic System-Reminder Wrapping

**Why.** During multi-tool-call turns, if the user sends a message mid-execution, the model may treat it as a new request rather than context for the current task. Wrapping mid-loop user messages in `<system-reminder>` tags prevents this.

**Sketch.** In `run_turn()`, when processing approval responses, if the user provided text alongside their approval, wrap it as: `<system-reminder>The user sent: {text}\nPlease address this and continue with your tasks.</system-reminder>`. Inject as a system message in the next LLM call.

## 5. Techniques to Skip

### 5.1 Per-Model System Prompts

**Why skip.** co-cli currently supports only Gemini and Ollama. Maintaining 6 prompt files for 2 providers is overkill. OpenCode needs this because it supports 10+ providers with wildly different model behaviors. Revisit if co-cli adds Claude or GPT-5 as providers.

### 5.2 Sub-Agent / Task Tool

**Why skip.** co-cli is a personal assistant, not a coding agent. The primary use case is conversational Q&A with tool augmentation, not multi-file codebase exploration. The Task tool requires child session management, permission inheritance, and synthetic message plumbing. The complexity-to-value ratio is poor for co-cli's scope.

### 5.3 Plan Mode

**Why skip.** Plan mode requires a dual-agent architecture (plan agent with read-only permissions, build agent with full permissions), plan-file management, and mode-switching UI. co-cli's prompt rules already encourage "think before acting." The structured plan mode is valuable for complex coding tasks but out of scope for co-cli's assistant pattern.

### 5.4 TodoWrite Tool

**Why skip.** co-cli's memory system serves the cross-session knowledge role. Within a single session, the LLM's native reasoning (and co-cli's reasoning rule) handles task planning. Adding a TodoWrite tool would be over-instrumentation for conversational use.

### 5.5 Instruction File Discovery

**Why skip.** co-cli's design principle is that all knowledge is dynamic and tool-loaded. Baking project-level instructions into the system prompt conflicts with this. The memory tool and future knowledge articles serve this role.

## 6. Open Questions

### 6.1 Compaction vs. Sliding Window

OpenCode's `isOverflow()` uses a hard token-count threshold to trigger compaction, while co-cli's sliding window triggers on every call with a configurable window size. Which approach produces better context quality over long conversations? Need to benchmark: does threshold-triggered compaction with explicit "continue" injection outperform rolling summarization?

### 6.2 Prune-Before-Compact

OpenCode's `prune()` strips old tool outputs (keeping last 40K tokens) at loop exit, *separately* from compaction. co-cli's `truncate_tool_returns` does this on every call as a history processor. Should co-cli separate these concerns (cheap pruning always, expensive summarization only when needed)?

### 6.3 Tool Schema Adaptation

OpenCode's `ProviderTransform.schema()` modifies tool JSON schemas per provider (e.g., removing unsupported fields for Anthropic). pydantic-ai handles some of this internally, but does it cover all cases? If co-cli adds more providers, do we need a schema normalization layer?

### 6.4 Synthetic Messages and pydantic-ai History

OpenCode freely injects synthetic user/assistant messages into the conversation. pydantic-ai's history model is more structured. Can we inject synthetic messages via `ModelRequest` / `ModelResponse` objects, or do we need a pre-processor?

### 6.5 Step Limits

OpenCode's agents can have `steps` limits (`agent.ts:44`). When the max is reached, a "MAXIMUM STEPS REACHED" message is injected as an assistant turn, forcing text-only response. Should co-cli implement a similar safety valve, especially for the sliding window to prevent runaway turns?
