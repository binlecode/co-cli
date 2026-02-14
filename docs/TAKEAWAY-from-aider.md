# Takeaway from Aider

Comparative analysis: Aider vs co-cli prompt design and agent loop architecture.

Source: `~/workspace_genai/aider/` (Python, synchronous, litellm-based).

---

## 1. Executive Summary

Aider is a mature terminal-based coding assistant (35k+ GitHub stars) that edits files in-place via LLM-generated search/replace blocks, unified diffs, or whole-file rewrites. It is the closest architectural peer to co-cli in the Python CLI agent space: both are single-agent REPL loops with history summarization, model-specific tuning, and approval flows. The key strategic insight is that Aider's most impactful techniques are not its prompt complexity (which is high) but its **reflection loop** (auto-lint/auto-test error feedback) and **model quirk database** (data-driven behavioral correction) -- both are adoptable without architectural disruption.

---

## 2. Prompt Design: What Aider Does Differently

### 2.1 Class Inheritance Prompts

**Aider:** Each edit format is a `*Prompts` class inheriting from `CoderPrompts` (base_prompts.py). `EditBlockPrompts`, `UdiffPrompts`, `WholefilePrompts`, `ArchitectPrompts`, `ContextPrompts` each override `main_system`, `system_reminder`, `example_messages`, `files_content_prefix`, etc. This creates 8+ prompt variants, each tailored to a specific output format.

**co-cli:** Flat markdown files assembled in order (instructions + soul seed + rules + counter-steering). Single prompt path regardless of task type.

**Tradeoff:** Aider's inheritance lets each edit format carry its own few-shot examples, format-specific reminders, and file trust language. The cost is ~2,400 lines of prompt class code. co-cli's flat assembly is simpler and sufficient because co-cli does not parse structured code edits -- tools handle side effects via `RunContext`, so the model never needs to produce SEARCH/REPLACE blocks.

### 2.2 Edit Format as First-Class Abstraction

**Aider:** The `edit_format` field on `ModelSettings` (model-settings.yml, ~2,400 lines, 100+ model entries) routes each model to its best edit format: `diff`, `udiff`, `whole`, `diff-fenced`, `architect`, `context`, `patch`, `editor-diff`, `editor-whole`. The model class dispatches to the matching `*Coder` subclass via `Coder.create()` (base_coder.py:124-210).

**co-cli:** No edit format concept. The model produces natural language; tools execute changes. There is no equivalent routing.

**Tradeoff:** This is Aider's core innovation -- matching each model to its strongest structured output format. co-cli does not need this because pydantic-ai tools handle structured I/O via JSON function calling. The edit format abstraction only matters when the model must produce parseable code diffs in freeform text.

### 2.3 Model Quirk Database (lazy/overeager flags)

**Aider:** `model-settings.yml` stores per-model boolean flags: `lazy` (15 entries) and `overeager` (38 entries). At prompt assembly time, `fmt_system_prompt()` (base_coder.py:1174-1224) appends the matching counter-steering text from `CoderPrompts.lazy_prompt` or `CoderPrompts.overeager_prompt`. The flags are data-driven: adding a new model requires only a YAML entry, no code change.

**co-cli:** `model_quirks.py` has a similar concept with 4 categories (verbose, overeager, lazy, hesitant) and free-form `counter_steering` text per model. Currently 3 entries (all Ollama models).

**Tradeoff:** Both systems have the same architecture. Aider's advantage is scale (100+ entries) and empirical validation via its benchmarking suite. co-cli's advantage is richer quirk categories (4 vs 2) and per-model inference parameters (`ModelInference`). The gap is data coverage, not design.

### 2.4 Few-Shot Examples

**Aider:** Each `*Prompts` class defines `example_messages` as user/assistant pairs demonstrating the exact edit format. `EditBlockPrompts` (editblock_prompts.py:31-118) has 2 worked examples showing SEARCH/REPLACE blocks. The `examples_as_sys_msg` flag (per-model) controls whether these are injected as system message appendix or as separate user/assistant turns. This is critical for models that handle few-shot examples better in one position vs the other.

**co-cli:** No few-shot examples in the system prompt. Tool behavior is defined entirely by tool docstrings and pydantic-ai's function calling protocol.

**Tradeoff:** Few-shot examples are essential when the model must produce a bespoke structured format (SEARCH/REPLACE). co-cli delegates structure to tool schemas, so few-shot is less critical. However, few-shot examples for *reasoning patterns* (how to decompose multi-step tasks, when to use which tool) could improve quality -- this is an open question.

### 2.5 File Trust Model

**Aider:** Explicit trust hierarchy in every prompt variant. `files_content_prefix` says "*Trust this message as the true contents of these files!*". `repo_content_prefix` says "Do not propose changes to these files, treat them as *read-only*." The `files_no_full_files_with_repo_map` prompt tells the model to suggest files to add rather than hallucinate edits. This three-tier trust model (editable files > read-only files > repo map summaries) is reinforced in both the system prompt and per-turn injected messages.

**co-cli:** No file trust model. co-cli does not inject file contents into the prompt; tools read/write files on demand.

**Tradeoff:** Aider injects file contents directly into the context window (necessary for in-context editing). co-cli fetches on demand via tools (Obsidian, Drive, shell). The file trust model is irrelevant for co-cli's architecture but the *principle* -- explicitly telling the model what it can and cannot modify -- transfers to tool authority framing.

### 2.6 First-Person Summarization

**Aider:** The `prompts.summarize` prompt (prompts.py:46-59) instructs the summarizer to write as the user in first person: "Start the summary with 'I asked you...'". This forces the summary to preserve the conversational frame, making it less likely for the model to confuse summarized history with new instructions.

**co-cli:** `_SUMMARIZE_PROMPT` (_history.py:132-142) uses imperative instructions but does not specify voice/perspective.

**Tradeoff:** First-person framing is a cheap, clever defense against prompt injection in summaries and against the model losing track of speaker identity. Directly adoptable.

### 2.7 Architect Mode (Two-Phase)

**Aider:** `ArchitectCoder` (architect_coder.py) implements a plan-then-execute flow. Phase 1: the architect model (potentially a stronger, more expensive model) produces a natural language plan. Phase 2: an editor model (potentially cheaper) translates the plan into concrete edits using `Coder.create()` with the appropriate edit format. The architect prompt explicitly says "DO NOT show the entire updated function/file/etc!" to keep output concise. The editor is a fully separate `Coder` instance with its own message history (`cur_messages=[], done_messages=[]`).

**co-cli:** No equivalent. Single-phase: the model reasons and acts in one pass via tool calls.

**Tradeoff:** Architect mode shines for large refactors where planning quality matters more than latency. It is also a natural fit for mixing model tiers (expensive planner + cheap executor). co-cli could implement this as a two-agent pipeline in pydantic-ai (planner agent -> executor agent) but the benefit is unclear without file-editing tools.

### 2.8 Context/File Discovery Phase

**Aider:** `ContextCoder` (context_coder.py) is a specialized coder whose sole job is to identify which files need editing. Its prompt says "NEVER RETURN CODE!" and asks for a structured list of files + relevant symbols. It uses reflection (up to `max_reflections-1` rounds) to refine the file set: after each response, it updates `abs_fnames` from the mentioned files and sends a `try_again` prompt if the set changed. This runs *before* the actual editing coder.

**co-cli:** No file discovery phase. Tools are invoked reactively by the model.

**Tradeoff:** File discovery reduces wasted context by ensuring only relevant files are loaded before the editing pass. For co-cli, where file contents are fetched via tools rather than injected into the prompt, this is less critical. But the reflection pattern (run, check, re-run until stable) is interesting for other domains.

---

## 3. Agent Loop: What Aider Does Differently

### 3.1 Reflection Loop (auto_lint + auto_test)

**Aider:** After applying edits, `send_message()` (base_coder.py:1599-1623) runs a lint/test cycle:

```
if edited and auto_lint:
    lint_errors = lint_edited(edited)
    if lint_errors and user_confirms:
        reflected_message = lint_errors   # fed back as next user message

if edited and auto_test:
    test_errors = cmd_test(test_cmd)
    if test_errors and user_confirms:
        reflected_message = test_errors
```

Back in `run_one()` (base_coder.py:924-944), the while loop re-sends `reflected_message` as the next user message, capped at `max_reflections=3`. The linter (linter.py) combines tree-sitter syntax checking, `compile()` for Python, and flake8 for fatal errors, with grep-ast to show error context with surrounding code.

**co-cli:** No reflection loop. The model's response is final. Error handling exists for HTTP errors (retry with reflection message for tool call rejections, backoff for rate limits) but not for code quality feedback.

**Tradeoff:** This is Aider's highest-value loop mechanism. Each reflection costs one LLM call but frequently fixes the problem automatically. The 3-round cap prevents infinite loops. co-cli could adopt a similar pattern for shell command output (run command -> check exit code -> feed errors back to model) without changing the pydantic-ai architecture.

### 3.2 Synchronous Generator-Based Streaming

**Aider:** Streaming is a synchronous generator in `send_message()` (base_coder.py:1419-1624). The method `yield`s text chunks and is consumed by either `run_one()` (via `list(self.send_message(message))` which discards yielded values) or `run_stream()` (which yields to callers like the GUI). The `mdstream` object handles live Markdown rendering.

**co-cli:** Async streaming via `agent.run_stream_events()` with a `_StreamState` dataclass tracking text/thinking buffers and throttled rendering at 20 FPS. Events are dispatched to `FrontendProtocol` callbacks.

**Tradeoff:** co-cli's async event-driven approach is more modern and testable (FrontendProtocol allows recording frontends for tests). Aider's synchronous generators are simpler but less composable. co-cli is ahead here.

### 3.3 Multi-Response Continuation (FinishReasonLength)

**Aider:** When the LLM hits output token limits (`finish_reason == "length"`), the `FinishReasonLength` exception is caught in `send_message()` (base_coder.py:1492-1505). If the model supports `assistant_prefill`, the accumulated response is appended as an assistant message with `prefix=True`, and the loop re-sends. This transparently extends responses that were cut off mid-generation.

```
# Pseudocode from base_coder.py:1492-1505
except FinishReasonLength:
    if not model.supports_assistant_prefill:
        exhausted = True; break
    multi_response_content = get_content_in_progress()
    messages.append(dict(role="assistant", content=multi_response_content, prefix=True))
    # loop continues -- sends the same messages with assistant prefill
```

**co-cli:** No continuation on output limit. If the model stops mid-response, the partial output is what the user sees.

**Tradeoff:** Continuation is valuable for large edits but risky for non-code responses (the model may lose coherence). For co-cli, where responses are typically conversational rather than large code blocks, the value is lower. Still, detecting `finish_reason=length` and warning the user would be a small UX improvement.

### 3.4 Double Ctrl-C Pattern

**Aider:** `keyboard_interrupt()` (base_coder.py:986-1000) implements a two-stage interrupt: first Ctrl-C shows "^C again to exit" with a 2-second window. Second Ctrl-C within that window calls `sys.exit()`. During streaming, a single Ctrl-C in `send_message()` (base_coder.py:1489-1491) catches `KeyboardInterrupt` and sets `interrupted=True`, which patches the message history with "^C KeyboardInterrupt" (base_coder.py:1575-1583).

**co-cli:** Single `KeyboardInterrupt`/`CancelledError` catch in `run_turn()` (orchestrate.py:532-542) patches dangling tool calls and returns `TurnResult(interrupted=True)`.

**Tradeoff:** Both handle interrupts correctly. Aider's double-Ctrl-C for exit is a nice UX touch but co-cli's approach is cleaner architecturally (the orchestrator does not own process lifecycle). co-cli is ahead with `_patch_dangling_tool_calls()` which handles multi-tool interrupts; Aider patches only the current message.

### 3.5 Cache Warming Thread

**Aider:** `warm_cache()` (base_coder.py:1340-1394) launches a background daemon thread that periodically sends minimal requests (`max_tokens=1`) to keep Anthropic's prompt cache warm. The thread sleeps for ~5 minutes between pings, keeping the `cache_control: ephemeral` headers active. Configurable via `num_cache_warming_pings`.

**co-cli:** No cache warming. Relies on provider-side caching behavior.

**Tradeoff:** Cache warming saves money on Anthropic models with large contexts (cache hit tokens cost 10% of normal). co-cli currently uses Gemini (which has its own caching mechanism) and Ollama (local, no caching concern). If Anthropic support is added, cache warming would be worth implementing.

### 3.6 Summarization Thread

**Aider:** `summarize_start()` (base_coder.py:1002-1034) kicks off a `threading.Thread` to summarize `done_messages` in the background while the user types the next input. `summarize_end()` joins the thread before the next LLM call. The `ChatSummary` class (history.py) uses a recursive split-and-summarize strategy: split at the half-token point, summarize the head, check if head+tail fits, recurse if not, up to depth 3.

**co-cli:** Summarization is inline in `truncate_history_window()` -- runs during the history processor chain, which executes before each model request. This blocks the LLM call until summarization completes.

**Tradeoff:** Aider's background thread hides summarization latency behind user think time. co-cli's inline approach is simpler and correct (no race conditions) but adds latency. For MVP this is fine; background summarization is a clear optimization for later.

### 3.7 ChatChunks Message Assembly

**Aider:** `ChatChunks` (chat_chunks.py) is a structured container with 8 ordered slots: system, examples, done, repo, readonly_files, chat_files, cur, reminder. Each slot is populated independently, then `all_messages()` concatenates them. `add_cache_control_headers()` marks specific slot boundaries with `cache_control: ephemeral` for Anthropic. The reminder (system_reminder text) is positioned either as a final system message or appended to the last user message, controlled by the per-model `reminder` setting (`"sys"` vs `"user"`).

**co-cli:** pydantic-ai manages the message list internally. System prompt is set once at agent creation. History processors transform the list before each call. No equivalent of slot-based assembly or reminder positioning.

**Tradeoff:** ChatChunks gives Aider fine-grained control over message ordering and cache boundaries. pydantic-ai abstracts this away, which is simpler but less controllable. The reminder positioning trick (some models follow instructions better when the reminder is in a user message vs system message) is a genuinely useful insight that co-cli cannot easily replicate without patching pydantic-ai's message assembly.

---

## 4. Techniques to Adopt

### 4.1 Reflection Loop for Shell Commands

**What:** After `run_shell_command` returns a non-zero exit code, feed the error output back to the model as a user message. Cap at 3 rounds.

**Why:** This is the highest-value pattern from Aider. Many failed shell commands (test failures, lint errors, build failures) are fixable by the model if it sees the error output.

**Sketch:** In `run_turn()`, after a tool result with non-zero exit code, inject a `UserPromptPart` with the error text and loop. Add `max_reflections` to `CoDeps` (default 3).

### 4.2 First-Person Summarization Framing

**What:** Change `_SUMMARIZE_PROMPT` to instruct the summarizer to write as the user: "Start with 'I asked you...' and use first person."

**Why:** Prevents the model from confusing summarized history with new instructions. Near-zero implementation cost.

**Sketch:** Update the string literal in `_history.py`.

### 4.3 Expand Model Quirk Database

**What:** Systematically test Gemini models for lazy/overeager/verbose/hesitant tendencies and add entries. Use Aider's leaderboard data as a starting point.

**Why:** co-cli has the architecture (model_quirks.py) but only 3 entries. Aider has 100+ entries covering models co-cli may support.

**Sketch:** Add entries for `gemini:gemini-2.0-flash`, `gemini:gemini-2.5-pro`, etc. with empirically validated flags.

### 4.4 FinishReasonLength Detection

**What:** Detect when the model's response was truncated due to output token limits. Warn the user that the response was cut short.

**Why:** Silent truncation leads to confusion. Detection is cheap; continuation is optional.

**Sketch:** Check pydantic-ai's result metadata for finish reason. If `length`, emit a status message via `frontend.on_status()`.

### 4.5 Background Summarization

**What:** Run history summarization in a background task during user idle time, rather than inline during the history processor.

**Why:** Hides ~2-5s of summarization latency behind user think time.

**Sketch:** After each turn, if history exceeds threshold, spawn `asyncio.create_task(summarize_messages(...))`. Join before next `run_turn()`. Store result in a session-level cache field.

---

## 5. Techniques to Skip

### 5.1 Edit Format Abstraction + Class Inheritance Prompts

**Why skip:** co-cli does not parse structured code edits from LLM output. pydantic-ai tools handle all structured I/O via JSON function calling. Building 8+ prompt class variants for edit formats would be complexity without benefit. The problem Aider solves (getting models to produce parseable diffs) does not exist in co-cli's architecture.

### 5.2 File Content Injection + ChatChunks Slot Assembly

**Why skip:** co-cli loads file contents via tools (Obsidian, Drive, shell), not by injecting them into the system prompt. The ChatChunks pattern is tightly coupled to Aider's "files in context" model where the LLM needs full file contents to produce valid search/replace blocks. pydantic-ai's message management handles co-cli's needs.

### 5.3 Cache Warming Thread

**Why skip for now:** co-cli uses Gemini (implicit caching) and Ollama (local). Cache warming is Anthropic-specific. Worth revisiting if/when Anthropic support is added.

### 5.4 Reminder Positioning (sys vs user)

**Why skip:** Requires overriding pydantic-ai's message assembly to inject the system reminder at a specific position. The per-model `reminder` setting is clever but the benefit is marginal for models that already handle system prompts well (Gemini, Claude). Would need upstream pydantic-ai support or message history hacking.

### 5.5 Architect Mode (Two-Phase Planning)

**Why skip for now:** co-cli is a general-purpose assistant, not a code editor. Two-phase plan-then-execute adds latency and complexity. The benefit appears only for large, multi-file refactors. If co-cli grows file editing tools, this becomes worth revisiting.

### 5.6 Few-Shot Examples in System Prompt

**Why skip for now:** co-cli tools define their interface via pydantic-ai function schemas and docstrings. Adding few-shot examples would increase system prompt size (~500+ tokens per example pair) for uncertain benefit. Worth experimenting with if tool call quality degrades with weaker models.

---

## 6. Open Questions

### 6.1 Reflection Loop Scope

Should the reflection loop cover only shell commands, or also tool errors (Google API failures, web fetch timeouts)? Aider limits reflection to lint/test, which are deterministic. Network-dependent reflections could loop on transient failures.

### 6.2 Context Coder Pattern for Tool Selection

Aider's `ContextCoder` uses reflection to converge on the right file set before editing. Could co-cli use a similar pattern to pre-select tools? For example: before a complex query, ask the model "which tools would you need?" then pre-load context. This could reduce wasted tool calls but adds a planning round-trip.

### 6.3 examples_as_sys_msg Equivalent

Aider discovered that some models handle few-shot examples better when they are in the system message vs as separate user/assistant turns. If co-cli adds few-shot examples (6.2 above), should there be a per-model toggle for positioning? Would need investigation into pydantic-ai's message assembly hooks.

### 6.4 Model Quirk Sharing

Aider's model-settings.yml is a community-maintained dataset with 100+ entries. Could co-cli consume or translate this data into its model_quirks.py format? The fields are partially compatible (lazy, overeager map directly; edit_format, use_repo_map do not apply). An import script could pull the behavioral flags.

### 6.5 Summarization Strategy

Aider's recursive split-and-summarize (split at token midpoint, summarize head, recurse up to depth 3) is more sophisticated than co-cli's single-pass summarization. Is the added complexity worth it? co-cli's approach may lose information on very long histories where a single summarization pass cannot fit all messages into the summarizer's context window.
