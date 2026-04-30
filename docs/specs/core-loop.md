# Co CLI Core Loop Design

## Product Intent

**Goal:** Own foreground-turn execution from prompt input to post-turn finalization.
**Functional areas:**
- Turn orchestration and stream segment execution
- Approval flow and approval-resume narrowing
- History-processor orchestration and trigger points
- Inline recovery and degradation routing for compaction-related failures
- Retries and interrupt handling

**Non-goals:**
- Multi-turn planning graphs
- Persistent approval memory across sessions
- Multi-agent coordination

**Success criteria:** Turn contract is stateless (state on CoDeps only); approval resume narrows toolset; compaction circuit breaker prevents cascade.
**Status:** Stable

---

For top-level architecture and startup sequencing, see [system.md](system.md) and [bootstrap.md](bootstrap.md). This doc owns foreground-turn execution, approval resumes, retries, interrupts, and the orchestration points where history processors and compaction recovery are invoked. Instruction-layer construction and per-request assembly live in [prompt-assembly.md](prompt-assembly.md); memory/session persistence and recall live in [memory.md](memory.md); compaction mechanics in [compaction.md](compaction.md).

## 1. Foreground Turn Flow

This doc describes one complete foreground turn, from prompt input to post-turn finalization.

Cross-subsystem overview — one full turn crosses every subsystem; detailed behavior at each step lives in the linked specs:

```mermaid
flowchart TD
    A["REPL input"] --> B{"slash command?"}
    B -->|built-in local| A
    B -->|skill dispatch| C["expand skill body → user_input"]
    B -->|plain text| D["user_input"]
    C --> E["run_turn()"]
    D --> E

    E --> F["agent.run_stream_events"]
    F --> G["SDK appends ModelRequest + resolves instruction parts<br/>(static + @agent.instructions)"]
    G --> H["history processors 1..5<br/>(truncate → compact → safety → recall → summarize)"]
    H --> I["provider HTTP"]
    I --> J{"tool approvals needed?"}
    J -->|yes| K["_run_approval_loop → deferred_tool_results"]
    K --> F
    J -->|no| L["final result"]

    L --> M["_finalize_turn()"]
    M --> N["transcript append<br/>(or child-session branch after history replacement)"]
```

| Stage | Owned by |
| --- | --- |
| Slash-command dispatch, skill expansion | [tui.md](tui.md), [skills.md](skills.md) |
| `run_turn` / approval loop / retries | [core-loop.md](core-loop.md) |
| Instruction parts + history processors | [prompt-assembly.md](prompt-assembly.md) |
| Compaction trigger (processor #5) | [compaction.md](compaction.md) |
| Turn-time recall (processor #4) | [memory.md](memory.md) |
| Transcript append / child-session branching | [memory.md](memory.md) |

Detailed foreground turn flow:

```mermaid
flowchart TD
    A["PromptSession.prompt_async()"] --> B{"blank or exit?"}
    B -->|exit/quit| Z["exit REPL"]
    B -->|blank| A
    B -->|non-empty| C{"starts with '/'?"}

    C -->|yes| D["dispatch_command()"]
    C -->|no| H["_run_foreground_turn()"]

    D -->|LocalOnly| A
    D -->|ReplaceTranscript| E["adopt new history; if compaction replaced history, branch transcript to a child session and persist full compacted state"]
    E --> A
    D -->|DelegateToAgent| F["set active_skill_name; snapshot/apply skill_env; delegated_input becomes user_input"]

    F --> H
    H --> I["run_turn(): deps.runtime.reset_for_turn(); frontend.on_status('Co is thinking...'); init _TurnState; start co.turn span"]
    I --> J["_execute_stream_segment() via agent.run_stream_events(...)"]
    J --> K{"segment result / exception"}

    K -->|DeferredToolRequests| L["_run_approval_loop()"]
    L --> M["set resume_tool_names; collect approvals; current_input=None; current_history=latest_result.all_messages(); deferred_tool_results=approvals"]
    M --> J

    K -->|final result| N["adopt latest_result.all_messages(); fallback render if no text streamed; run _check_output_limits()"]
    N --> O["return TurnResult(outcome='continue')"]

    K -->|first context overflow 400/413 + compactable history| P["materialize pending user input into history; recover_overflow_history(...); current_input=None; status banner; retry"]
    P --> J
    K -->|second overflow or no compaction boundary| R
    K -->|HTTP 400 with reformulation budget| Q["append provider-error reflection request; current_input=None; sleep 0.5s; retry"]
    Q --> J
    K -->|terminal ModelHTTPError / ModelAPIError / UnexpectedModelBehavior / TimeoutError| R["return TurnResult(outcome='error')"]
    K -->|KeyboardInterrupt / CancelledError| S["_build_interrupted_turn_result(): drop trailing unanswered tool-call response; append abort marker"]

    O --> T["cleanup_skill_run_state() in finally"]
    R --> T
    S --> T
    T --> U["_finalize_turn(): append transcript tail or branch child transcript after compaction; optional error banner"]
    U --> A
```

Execution owners:

| Owner | Responsibility |
| --- | --- |
| `_chat_loop()` | prompt input, blank/exit handling, slash dispatch, transcript replacement, and skill-env setup |
| `_run_foreground_turn()` | `run_turn()`, guaranteed skill-env cleanup, and post-turn finalization |
| `run_turn()` | one orchestrated LLM turn, including status updates, retries, approval resumes, output checks, and interrupt handling |
| `_execute_stream_segment()` | one `agent.run_stream_events(...)` segment plus frontend event delivery and usage merge |
| `_run_approval_loop()` | same-turn approval-resume cycle until output is no longer `DeferredToolRequests` |
| `_finalize_turn()` | transcript persistence/branching and generic error banner |

Two boundary rules keep the loop legible:

- REPL-owned transcript state lives in `message_history` inside `main.py`
- orchestration never mutates REPL history in place; it returns a `TurnResult` with the next transcript snapshot
- transcript durability is tracked separately via `persisted_message_count` and `compaction_applied_this_turn`

## 2. Core Logic

### 2.1 Turn Contract And State Ownership

`run_turn()` is the only public one-turn orchestration entrypoint. It returns:

| Field | Meaning |
| --- | --- |
| `outcome` | `"continue"` or `"error"` |
| `interrupted` | whether the turn ended due to interrupt/cancellation |
| `messages` | next transcript snapshot for the REPL |
| `output` | final model output object |
| `usage` | latest segment usage payload |
| `streamed_text` | whether visible assistant text was streamed live |

Turn-scoped mutable state is explicit in `_TurnState`:

| `_TurnState` field | Owner |
| --- | --- |
| `current_input` | current prompt text, or `None` for resume/retry hops |
| `current_history` | message list for the next segment call |
| `tool_reformat_budget` | HTTP 400 reformulation budget (app logic, not transport retry) |
| `latest_result` | most recent `AgentRunResult` from a completed segment |
| `latest_streamed_text` | last-segment streaming signal |
| `latest_usage` | last-segment usage payload |
| `tool_approval_decisions` | `DeferredToolResults` consumed by the next resume hop |
| `outcome` / `interrupted` | final turn outcome flags |

Cross-cutting turn state that lives on `deps.runtime` instead:

| `deps.runtime` field | Why it is not in `_TurnState` |
| --- | --- |
| `turn_usage` | authoritative per-turn accumulator shared across foreground and sub-agent tool calls |
| `safety_state` | updated by the `safety_prompt` dynamic instruction before each model-bound segment |
| `tool_progress_callback` | owned by `StreamRenderer` and active tool surfaces |
| `resume_tool_names` | set by `_run_approval_loop()` before each approval-resume segment; cleared after the loop exits; read by `_approval_resume_filter` |
| `compaction_skip_count` | cross-turn circuit breaker for inline compaction (>= 3 trips breaker; every 10 skips a probe is attempted) |
| `active_skill_name` | cross-function skill dispatch marker cleared after the turn |

### 2.2 Stream Segment Contract

`_execute_stream_segment()` owns exactly one `agent.run_stream_events(...)` call.

Inputs:

- `turn_state.current_input`
- `turn_state.current_history`
- `turn_state.tool_approval_decisions`
- `turn_state.latest_usage`
- selected agent surface: main agent for all passes (SDK skips `ModelRequestNode` on resume, so zero additional tokens)

Per-event handling:

| Event type | Behavior |
| --- | --- |
| `PartStartEvent` with `TextPart` / `ThinkingPart` | append buffered content into `StreamRenderer` |
| `PartDeltaEvent` with `TextPartDelta` / `ThinkingPartDelta` | append streamed deltas |
| `FinalResultEvent` / `PartEndEvent` | ignored for rendering; completion is defined by `AgentRunResultEvent` |
| `FunctionToolCallEvent` | flush buffered text/thinking, optionally show tool-start annotation, install progress callback |
| `FunctionToolResultEvent` | flush buffers, clear progress callback, render tool result panel when a `ToolReturnPart` exists |
| `AgentRunResultEvent` | store the final `AgentRunResult` object |

The event loop is wrapped in `asyncio.timeout(_LLM_SEGMENT_HANG_TIMEOUT_SECS)`. A `TimeoutError` from the guard propagates to `run_turn()`, which returns `TurnResult(outcome='error')` — no retry is attempted.

Normal-exit contract:

1. `renderer.finish()` flushes remaining thinking/text buffers.
2. `frontend.cleanup()` always runs in `finally`.
3. `turn_state.latest_result` must be non-`None`, otherwise `_execute_stream_segment()` raises `RuntimeError`.
4. `turn_state.latest_usage = result.usage()`
5. `turn_state.tool_approval_decisions = None`
6. `_merge_turn_usage()` adds the segment usage into `deps.runtime.turn_usage`

Reasoning display is purely a frontend concern:

| Mode | Behavior |
| --- | --- |
| `off` | thinking is discarded |
| `summary` | thinking is reduced to short status lines via `on_reasoning_progress()` |
| `full` | raw thinking is streamed and committed through the thinking surface |

### 2.3 Approval Flow

Approval deferral uses the native Pydantic-AI objects directly:

- `DeferredToolRequests` pauses a segment on approval-gated tool calls
- `_collect_deferred_tool_approvals()` turns those pending calls into `DeferredToolResults`
- `_run_approval_loop()` feeds that decision payload into the next segment

Approval collection sequence (per pending call):

0. read `output.metadata[tool_call_id]`; if `"questions" in metadata`, take the clarify path:
   - for each question dict in `metadata["questions"]`, construct `QuestionPrompt(question, options, multiple)` and call `frontend.prompt_question(prompt)`; collect answers into a list
   - encode `ToolApproved(override_args={"user_answers": answers})` — tool resumes with the injected answer list
   - skip steps 1–7
1. decode tool arguments with `decode_tool_args()`
2. resolve one `ApprovalSubject`
3. check `deps.session.session_approval_rules` for an exact `kind + value` match
4. otherwise prompt the user for `y`, `n`, or `a`
5. encode the decision into `DeferredToolResults`
6. optionally remember the scope when the user chose `a`
7. if denied, emit `logger.debug("tool_denied", tool_name, subject_kind, subject_value)`

Approval subject scopes:

| Tool shape | Subject kind | Remembered value |
| --- | --- | --- |
| `run_shell_command` | `shell` | first token of `cmd` |
| `file_write`, `file_patch` | `path` | parent directory |
| `web_fetch` | `domain` | parsed hostname |
| everything else, including MCP tools | `tool` | tool name |

Resume-loop behavior:

```text
# run_turn() — before first segment
# No explicit filter setup needed — _approval_resume_filter passes all
# during normal turns; SDK ToolSearchToolset handles deferred visibility.

# _run_approval_loop() — each resume hop
while latest_result.output is DeferredToolRequests:
  deps.runtime.resume_tool_names = frozenset(
      call.tool_name for call in approvals
  )
  approvals = _collect_deferred_tool_approvals(...)
  current_input = None
  current_history = latest_result.all_messages()
  tool_approval_decisions = approvals
  run next segment with main agent
clear deps.runtime.resume_tool_names
```

Important precision:

- `_approval_resume_filter` passes all during normal turns; narrows to `resume_tool_names` + `ALWAYS` tools during resume
- applies uniformly to native and MCP tools (all combined under one filter)
- approval resumes happen inside the same user turn; they are not a new REPL iteration

Shell approval remains split correctly:

- `run_shell_command()` decides `DENY`, `ALLOW`, or `REQUIRE_APPROVAL` from command shape
- only the `REQUIRE_APPROVAL` path reaches deferred approval handling
- denied shell commands never enter `_collect_deferred_tool_approvals()`

### 2.4 History Processors, Preflight, And Inline Compaction

The main agent is built with three registered history processors (pure transformers) in this exact order:

1. `truncate_tool_results`
2. `enforce_batch_budget`
3. `proactive_window_processor`

One function is registered via `agent.instructions()` and runs before every model request as a dynamic instruction:

- `safety_prompt` — doom-loop detection + shell reflection cap; active warnings returned as plain text

Processor roles:

| Processor | Role |
| --- | --- |
| `truncate_tool_results` | content-clears compactable tool results by per-tool-type recency (keep 5 most recent per type); protects the last turn (from last `UserPromptPart` onward) |
| `enforce_batch_budget` | spills largest non-persisted `ToolReturnPart`s in the current batch when aggregate size exceeds `config.tools.batch_spill_chars`; fails open |
| `proactive_window_processor` | replaces the middle of long histories with an inline LLM summary (with context enrichment) or static marker (circuit-breaker fallback) |

Preflight is called before every model-bound segment but not on approval-resume segments (SDK skips `ModelRequestNode` on resume, so preflight would inject into a non-model path). Preflight injections are ephemeral — they are not stored back to `turn_state.current_history`, so retry iterations always start from the clean history without accumulated injections.

Ordering rationale:

- **#1–2 before #3**: truncation runs before summarization. The summarizer sees partially cleared content but receives rich side-channel context (file working set from `ToolCallPart.args`, session todos) to compensate.
- **Dynamic instructions before model request**: `safety_prompt` runs via the SDK's `agent.instructions()` mechanism before every model-bound request. Its output is ephemeral context — not stored back to `turn_state.current_history`.

Compaction behavior:

- `proactive_window_processor()` gathers side-channel context via `gather_compaction_context()` (file working set, todos, prior summaries — capped at 4K chars), then calls `summarize_messages()` inline with a structured template when compaction triggers
- it compacts when token count exceeds `cfg.compaction_ratio` (0.65) of the budget
- token count is `max(estimate, reported)` — the local char-based estimate from `estimate_message_tokens()` (which counts `ToolCallPart.args` and `(dict, list)` content) floored against the provider-reported `input_tokens` from the latest `ModelResponse`; the max-floor ensures a stale or missing provider report cannot suppress the trigger
- the budget is resolved by `resolve_compaction_budget()` in `context/summarization.py`: model's resolved `context_window` (Ollama config overrides the spec), then `llm.num_ctx` when Ollama OpenAI-compat is active, then `100,000` tokens
- when `deps.model` is absent (sub-agents, tests), it uses a static marker directly without incrementing the failure counter
- a circuit breaker (`deps.runtime.compaction_skip_count`) trips at 3 consecutive failures; tripped state uses static markers but probes the LLM once every `_CIRCUIT_BREAKER_PROBE_EVERY` (10) skips — probe success resets the counter to 0
- a `[dim]Compacting conversation...[/dim]` indicator is shown before the LLM call
- successful history replacement sets `deps.runtime.compaction_applied_this_turn`, which later tells `_finalize_turn()` to persist into a child transcript instead of appending into the parent transcript

Knowledge recall is on-demand, not injected per-turn:

- the session-start date is frozen into the static instructions in `build_agent()` — stable for the entire session
- personality memories live in the static system prompt (injected once at agent construction)
- the agent calls `memory_search()` proactively when past context is relevant

### 2.5 Retries, Output Limits, Errors, And Interrupts

`run_turn()` owns app-level error handling. Transport-level retries (HTTP 429, 5xx, network errors) are delegated to the OpenAI SDK's built-in retry machinery and are not managed by `run_turn()`.

Error matrix:

| Condition | Behavior |
| --- | --- |
| HTTP 413 unconditionally, or HTTP 400 with explicit overflow evidence (`is_context_overflow` in `_http_error_classifier`) | one-shot `recover_overflow_history()` — first materializes the pending user input into history, then calls the shared `plan_compaction_boundaries()` (same `TAIL_FRACTION`, `min_groups_tail=1`) and replaces the dropped middle with an LLM summary when available or a static marker otherwise. Retry on success; terminal when the planner cannot find a boundary or on second overflow. Never falls through to 400 reformulation. |
| HTTP 400 with reformat budget left (not context overflow) | append a reflection request describing the rejected tool call, set `current_input=None`, retry (app-level reformulation, not transport retry) |
| HTTP 400 with budget exhausted, or other terminal HTTP errors | set `outcome='error'`; record `provider_error` span event (`http.status_code`, `error.body` capped at 500 chars) on the `co.turn` span; return `_build_error_turn_result()` |
| `ModelAPIError` (network errors exhausted by SDK) | set `outcome='error'` and return `_build_error_turn_result()` |
| `TimeoutError` (segment hang guard) | no retry; set `outcome='error'` and return `_build_error_turn_result()` |
| `UnexpectedModelBehavior` | no retry; surface as a user-facing status message, set `outcome='error'` and return `_build_error_turn_result()` |
| `KeyboardInterrupt` / `CancelledError` | return `_build_interrupted_turn_result()` |

Output-limit diagnostics happen only after a successful final segment:

1. if `latest_result.response.finish_reason == "length"`, show a truncation status message
2. if the provider supports context-ratio tracking, compare `deps.runtime.turn_usage.input_tokens / deps.config.llm.num_ctx`
3. emit either a warning or overflow message based on `ctx_warn_threshold` and `ctx_overflow_threshold`

Interrupt handling is conservative:

- `KeyboardInterrupt` or `asyncio.CancelledError` returns `_build_interrupted_turn_result()`
- if the transcript ends with a `ModelResponse` containing unanswered `ToolCallPart`s, that response is dropped
- an abort marker `ModelRequest` is appended so the next turn knows the previous turn was interrupted and must verify state

### 2.6 Post-Turn Finalization In `main.py`

`_run_foreground_turn()` sequences the full wrapper around `run_turn()`:

1. `run_turn(...)`
2. `cleanup_skill_run_state(saved_env, deps)` in `finally`
3. `_finalize_turn(...)`

`_finalize_turn()` then performs the remaining non-orchestration work:

1. `append_messages()` — positional tail slice of new messages written to `deps.session.session_path`
2. print a generic error banner when `turn_result.outcome == "error"`

Skill dispatch is intentionally scoped to one delegated turn:

- `_chat_loop()` applies `skill_env` only for the delegated skill run
- `_cleanup_skill_run_state()` restores prior environment values and clears `deps.runtime.active_skill_name`
- finalization happens only after that restoration

### 2.7 Comparison Against Common Peer Patterns

The foreground loop still matches the common 2026 CLI-agent shape more than it diverges from it.

| Common pattern | `co` today | Design read |
| --- | --- | --- |
| one owned foreground turn executor | `run_turn()` | aligned |
| event-stream-driven rendering | `_execute_stream_segment()` + `StreamRenderer` | aligned |
| approvals outside most tool bodies | `_collect_deferred_tool_approvals()` / `_run_approval_loop()` | aligned |
| command-specific shell trust boundary | shell tool classifies allow/deny/ask itself | aligned and strong |
| error handling and interrupts owned by the loop | `run_turn()` | aligned |
| compaction as an inline concern with circuit breaker | `proactive_window_processor()` with `compaction_skip_count` | aligned |
| isolated specialist contexts | delegation agents use `fork_deps()` and stay outside the foreground loop | aligned |

The intentional simplification remains:

- no planner graph in the foreground turn
- no multi-turn queue inside the loop
- no approval memory persisted across sessions

## 3. Config

These settings most directly shape one-turn orchestration behavior. Instruction and recall settings live in [prompt-assembly.md](prompt-assembly.md); memory/session and knowledge settings live in [memory.md](memory.md).

| Setting | Env Var | Default | Description |
| --- | --- | --- | --- |
| `tool_retries` | `CO_TOOL_RETRIES` | `3` | Per-tool retry count baked into agent/tool registration |
| `doom_loop_threshold` | `CO_DOOM_LOOP_THRESHOLD` | `3` | Identical tool-call streak threshold for doom-loop intervention |
| `max_reflections` | `CO_MAX_REFLECTIONS` | `3` | Consecutive shell-error streak threshold for reflection guardrail |
| `ctx_warn_threshold` | `CO_LLM_CTX_WARN_THRESHOLD` | `0.85` | Context-ratio warning threshold |
| `ctx_overflow_threshold` | `CO_LLM_CTX_OVERFLOW_THRESHOLD` | `1.0` | Context-ratio overflow threshold |
| `reasoning_display` | `CO_REASONING_DISPLAY` | `summary` | Thinking display mode for streamed turns |

## 4. Files

| File | Purpose |
| --- | --- |
| `co_cli/main.py` | REPL loop, slash routing, skill-env lifecycle, foreground-turn wrapper, and teardown |
| `co_cli/context/orchestrate.py` | `TurnResult`, `_TurnState`, stream execution, approval loop, error handling, output checks, and interrupt/error builders |
| `co_cli/context/compaction.py` | public entry points (three registered history processors, `proactive_window_processor` for M3; overflow-recovery entry points `recover_overflow_history` and `emergency_recover_overflow_history`); backed by private submodules `_compaction_boundaries.py` (planner), `_compaction_markers.py` (marker builders and enrichment), and `_history_processors.py` (dedup / truncate / batch-budget transformers) |
| `co_cli/context/prompt_text.py` | `safety_prompt_text` — called via `agent.instructions()` wrapper in `_instructions.py` |
| `co_cli/context/summarization.py` | `summarize_messages`, `resolve_compaction_budget`, and token-estimation helpers — shared by history processor and `/compact` |
| `co_cli/agent/core.py` | main agent factory |
| `co_cli/agent/_native_toolset.py` | native filtered toolset construction with per-tool loading policy |
| `co_cli/tools/approvals.py` | approval-subject resolution, remembered rule matching, decision recording, and `QuestionRequired` exception |
| `co_cli/tools/shell.py` | command-shape shell allow/deny/approval logic |
| `co_cli/display/stream_renderer.py` | text/thinking buffering, reasoning reduction, and progress callback wiring |
| `co_cli/display/core.py` | terminal frontend surfaces, tool panels, status rendering, approval prompts, and question prompting (`QuestionPrompt`, `prompt_question`) |
| `co_cli/memory/session.py` | session filename generation and latest-session discovery |
| `co_cli/skills/lifecycle.py` | skill-run environment save/restore and active-skill-name cleanup |
