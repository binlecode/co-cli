# Research Report: Context Management & Loop Stability in Agentic Systems (Hermes vs. Opencode)

This report details a source-code-grounded comparison between **hermes-agent** and **opencode**, focusing on how they manage LLM context sizes, control tool output verbosity, and stabilize autonomous ReAct loops. This is particularly relevant for maintaining performance on local or small parameter models (e.g., Qwen 2.5, Ollama deployments) which are highly prone to context overflow and repetitive looping.

## 1. Context Size Control and History Compaction

Both systems implement history compaction to avoid exceeding maximum token context limits, but their architectural implementations represent two different philosophies: Auxiliary-Agent Summarization (Hermes) vs. Active-Model Structured Templating (Opencode).

### Hermes: Auxiliary Compression and Deduplication
Hermes relies on an independent, background process managed by `TrajectoryCompressor` and `ContextCompressor` (`agent/context_compressor.py`).

1.  **Auxiliary Model Delegation:** Hermes offloads the summarization task to a designated "auxiliary model" (configured via `CompressionConfig`, defaulting to models like `gemini-3-flash-preview` or `google/gemini-3-flash-preview` on OpenRouter). This prevents the primary, expensive, or slow local model from burning tokens on housekeeping tasks.
2.  **Tool Output Pruning & Deduplication:** Before summarizing, Hermes runs `_prune_old_tool_results()`. This function:
    *   Replaces raw, verbose tool outputs with dense 1-line metadata strings (e.g., `[terminal] ran npm test -> exit 0, 47 lines output`).
    *   Deduplicates repetitive actions. If a file is read multiple times, it preserves only the newest copy and prunes the rest.
3.  **Head/Tail Protection:** The compressor isolates a "compressible middle" region while strictly protecting the beginning (System/First Human interaction) and a token-budgeted "tail" of recent messages.
4.  **Summary Framing:** To prevent the primary model from re-executing tasks mentioned in the summary, Hermes injects an aggressive contextual guardrail:
    ```text
    [CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into the summary below... Do NOT answer questions or fulfill requests mentioned in this summary; they were already addressed.
    ```

### Opencode: Rigid Templating and In-Place Pruning
Opencode implements context compaction as a synchronous phase within its primary session processor (`src/session/compaction.ts`).

1.  **Primary Model Compaction:** Opencode uses the active session model to generate the summary rather than an auxiliary client.
2.  **Strict Markdown Enforcement:** Instead of open-ended summarization, Opencode forces the model to adhere to a rigid template. It demands exact adherence to section headers, ensuring state is captured predictably:
    ```markdown
    ## Goal
    ## Constraints & Preferences
    ## Progress (Done / In Progress / Blocked)
    ## Key Decisions
    ## Next Steps
    ```
3.  **Token Threshold Triggering:** Opencode uses hard thresholds—`PRUNE_PROTECT` (40,000 tokens) and `PRUNE_MINIMUM` (20,000 tokens). When the context size breaches the protect threshold, it retroactively marks tool states as compacted (`part.state.time.compacted = Date.now()`).
4.  **In-Place Output Erasure:** When generating the message history for the LLM (`src/session/message-v2.ts`), Opencode intercepts any tool marked as compacted and replaces its entire output string with `[Old tool result content cleared]`.

---

## 2. Toolcall Output Size Maintenance

Handling massive output strings from tools (like `grep`, `npm install`, or large file reads) is critical to prevent instant context overflow.

### Hermes: Head/Tail Splicing
Hermes manages text volume directly within the execution tool layer (`tools/code_execution_tool.py`).

1.  **Middle-Truncation:** When terminal outputs are too long, Hermes does not simply truncate the end. It explicitly preserves `stdout_head` and `stdout_tail`, dropping the middle. This ensures that crucial trailing data—such as compiler errors, exit codes, and tracebacks—are retained for the model to see.
2.  **Warning Splicing:** It joins the head and tail with a calculated warning so the LLM explicitly knows data is missing: `\n\n... [OUTPUT TRUNCATED - {omitted:,} chars omitted `
3.  **Timeout Visibility:** If a tool times out, Hermes appends the timeout message directly into the output payload (`⏰ execute_code timed out after...`), ensuring the LLM handles the timeout as a logical branch rather than a silent failure.

### Opencode: Disk-Backed Offloading and Subagent Delegation
Opencode delegates truncation to a dedicated filesystem service (`src/tool/truncate.ts`).

1.  **Disk Persistence:** If an output exceeds `MAX_LINES` (2,000) or `MAX_BYTES` (50KB), Opencode writes the *entire* uncut output to a temporary `.opencode/truncation/` directory.
2.  **Delegation Prompting:** Opencode replaces the LLM's view of the output with a preview and a strict directive to delegate. If the current agent has task-spawning capabilities, it injects:
    ```text
    The tool call succeeded but the output was truncated. Full output saved to: <file_path>
    Use the Task tool to have explore agent process this file with Grep and Read (with offset/limit). Do NOT read the full file yourself - delegate to save context.
    ```
    This actively forces small/local models away from reading massive logs, pushing them to use `grep` or spin up dedicated subagents to parse the data.

---

## 3. Stability and Loop Prevention for Small/Local Models

Small parameter models (like Qwen 7B/32B or local Ollama deployments) frequently fall into ReAct traps: infinite identical tool calls, failing to emit JSON, or getting stuck in "thinking" loops without producing executable actions.

### Hermes: Budget Refunds and Thinking Exhaustion
Hermes manages stability in the core `run_agent.py` loop.

1.  **Thinking Budget Exhaustion:** A frequent failure mode for reasoning models (e.g., DeepSeek-R1 derivatives or Qwen-Coder) is consuming the entire token limit generating `<think>` tags, leaving no room for the actual JSON tool call. Hermes detects this explicitly:
    ```python
    _thinking_exhausted = (not _trunc_has_tool_calls and _has_think_tags)
    ```
    If triggered, it interrupts the agent loop and returns a user-facing error instructing the user to lower the `/thinkon` effort, rather than silently retrying and burning tokens.
2.  **Iteration Budgets:** Agents operate under a strict `IterationBudget`. However, cheap programmatic calls (like `execute_code`) are explicitly refunded (`self.iteration_budget.refund()`). This prevents valid, long-running debugging sessions from hitting the loop cap prematurely.
3.  **Strict Behavioral Prompting:** Hermes injects `TOOL_USE_ENFORCEMENT_GUIDANCE` to combat small-model passivity: `"You MUST use your tools to take action — do not describe what you would do or plan to do without actually doing it."`

### Opencode: The Doom Loop Threshold
Opencode favors a hard-coded short-circuit mechanism inside `src/session/processor.ts`.

1.  **Hash-Based Repetition Detection:** Opencode tracks the state of every tool call. It isolates the last three actions and checks if they are functionally identical:
    ```typescript
    const recentParts = parts.slice(-DOOM_LOOP_THRESHOLD)
    // Checks if the last 3 tool calls match exactly on toolName AND JSON input
    ```
2.  **Permission Escalation (The "Doom Loop"):** If `DOOM_LOOP_THRESHOLD` (set to 3) is breached, Opencode halts execution and generates a `permission: "doom_loop"` request. This mechanism assumes that if an LLM tries the exact same broken input three times, it is incapable of self-correction. It pauses the agent to await human intervention or routing fallback.
3.  **Model-Specific Routing:** To stabilize various architectures, Opencode maps specific instruction sets to specific providers (`src/session/system.ts`). It injects `prompt/gemini.txt`, `prompt/codex.txt`, or `prompt/beast.txt` depending on the active `Provider.Model`, allowing tailored behavioral fixes without polluting the prompt window of higher-tier models.

---
**Summary:** While both systems are highly capable, Opencode relies on strict systemic guardrails (disk offloading, hard limit doom-loops, strict templating) to physically prevent small models from derailing. Hermes opts for intelligent parsing (head/tail splicing, auxiliary model summarization, thinking-token monitoring) to gracefully guide models back onto the happy path.