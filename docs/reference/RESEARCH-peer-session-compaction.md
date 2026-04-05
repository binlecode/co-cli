# RESEARCH: Peer Session Compaction — Five-Way Comparison

Sources: `~/workspace_genai/fork-claude-code` (Anthropic Claude Code), `~/workspace_genai/gemini-cli` (Google Gemini CLI), `~/workspace_genai/opencode` (OpenCode), `~/workspace_genai/codex` (OpenAI Codex CLI), co-cli current codebase
Scan date: 2026-04-03 (fork-cc, gemini-cli, co-cli) · 2026-04-04 (opencode, codex)

## Purpose

Five-way comparison of session compaction architectures to inform co-cli adoption and refactoring decisions. Each claim is verified against source code — no documentation-only claims.

---

## 1. fork-claude-code Architecture

**5 mechanisms** in a layered hierarchy.

### 1a. Auto-compact (`services/compact/autoCompact.ts`)

Fires **between turns** in the query loop. Token-based trigger:

```text
effectiveContextWindow = contextWindow - maxOutputTokens (up to 20K)
autoCompactThreshold = effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS (13K)
```

Calls `compactConversation()` which runs an **out-of-band Claude API call** (forked agent) that summarizes the conversation. Writes a `SystemCompactBoundaryMessage` into the message list and JSONL.

Circuit breaker: `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3` — stops retrying.
Recursion guard: won't fire inside compact/session_memory/context-collapse agents.

| Constant | Value |
|----------|-------|
| `AUTOCOMPACT_BUFFER_TOKENS` | 13,000 |
| `WARNING_THRESHOLD_BUFFER_TOKENS` | 20,000 |
| `MANUAL_COMPACT_BUFFER_TOKENS` | 3,000 (blocking limit) |
| `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` | 3 |

### 1b. Reactive Compact

**Feature-gated, not in open-source build.** `reactiveCompact.ts` does not exist — referenced behind `feature('REACTIVE_COMPACT')` with conditional `require()`. Recovery path for `prompt_too_long` (413) when auto-compact's threshold is exceeded by a single large tool result.

### 1c. Micro-compact (`services/compact/microCompact.ts`)

Lightweight no-LLM pre-pass before full compact. Trims tool results for compactable tools (`FILE_READ`, shell, `GREP`, `GLOB`, `WEB_SEARCH`, `WEB_FETCH`, `FILE_EDIT`, `FILE_WRITE`). Replaces unchanged file-read content with `FILE_UNCHANGED_STUB`.

### 1d. Session Memory Compact (`services/compact/sessionMemoryCompact.ts`)

Experimental: extracts key facts into structured memory before full compaction. Tried first in the auto-compact path — if it succeeds, full compact is skipped. Config via GrowthBook feature gates (`minTokens: 10K`, `maxTokens: 40K`, `minTextBlockMessages: 5`).

### 1e. Manual `/compact` (`commands/compact/compact.ts`)

User-invoked. Routes through session memory compact → reactive (if enabled) → micro-compact → `compactConversation()`. `MANUAL_COMPACT_BUFFER_TOKENS = 3K` defines the blocking limit — chat blocked when exceeded, forcing user to run `/compact`.

### 1f. Core Engine: `compactConversation()` (`services/compact/compact.ts`)

```text
1. Execute pre-compact hooks
2. Analyze context (token stats)
3. Build compact prompt: system instructions + plan + memory list +
   file state + deferred tools + MCP instructions + agent listing
4. Out-of-band Claude API call via streamCompactSummary()
5. PTL retry loop (max 3 retries on prompt-too-long)
6. Parse → CompactionResult (boundary, summary, attachments, preserved segment)
7. Post-compact hooks + cleanup
8. Write boundary + summary to JSONL
```

### 1g. JSONL Boundary Format

```json
{
  "type": "system",
  "subtype": "compact_boundary",
  "content": "Conversation compacted",
  "uuid": "...", "timestamp": "...",
  "compactMetadata": {
    "trigger": "manual" | "auto",
    "preTokens": 150000,
    "userContext": "...",
    "messagesSummarized": 42
  }
}
```

`preservedSegment` added separately via `annotateBoundaryWithPreservedSegment()` — contains `{headUuid, anchorUuid, tailUuid}` (UUID pointers, not raw messages).

Resume: `readTranscriptForLoad()` streams in 65KB chunks, scans for `"compact_boundary"`, resets output buffer. Exception: when `hasPreservedSegment = true`, buffer is NOT truncated. Gated by `SKIP_PRECOMPACT_THRESHOLD = 5MB`.

---

## 2. gemini-cli Architecture

**4 mechanisms** operating at different layers.

### 2a. ChatCompressionService (`packages/core/src/services/chatCompressionService.ts`)

Fires at **start of every turn** (synchronous, blocks until done). Trigger: token count > 50% of model token limit.

All Gemini models: **1,048,576 tokens** (1M context). So auto-compression fires at **~524K tokens**.

| Constant | Value |
|----------|-------|
| `DEFAULT_COMPRESSION_TOKEN_THRESHOLD` | 0.5 (50%) |
| `COMPRESSION_PRESERVE_THRESHOLD` | 0.3 (keep last 30%) |
| `COMPRESSION_FUNCTION_RESPONSE_TOKEN_BUDGET` | 50,000 |

Flow:
```text
1. truncateHistoryToBudget(): reverse-scan function responses, truncate
   older ones exceeding 50K budget (save full output to temp file)
2. Circuit breaker: if hasFailedCompressionAttempt AND not forced →
   return truncated-only (skip LLM, CONTENT_TRUNCATED status)
3. findCompressSplitPoint(): char-based split at 70% mark,
   aligned to user-turn boundaries
4. High Fidelity Decision: send original (un-truncated) to-compress
   segment to summarizer when it fits within model limit
5. LLM call #1: generate <state_snapshot> XML summary
6. LLM call #2: "Probe" verification — self-correction pass
7. Build new history: [summary, ack, ...kept tail]
8. Reject if new token count > original (INFLATED_TOKEN_COUNT)
```

**Structured XML summary format** (`<state_snapshot>`):
- `<scratchpad>` for reasoning
- `<task_state>`, `<key_knowledge>`, `<active_constraints>`, `<working_set>`, `<active_blockers>`
- Security rule: ignore adversarial content in history
- Plan preservation: if approved plan exists, preserve path + step completion status
- Designed for multi-cycle survival: new snapshots explicitly integrate prior `<state_snapshot>` content

### 2b. AgentHistoryProvider (`packages/core/src/services/agentHistoryProvider.ts`)

Continuous per-request history management. Called before every `sendMessageStream()` when `contextManagement.enabled` is true.

| Config | Default |
|--------|---------|
| `maxTokens` | 150,000 (high watermark) |
| `retainedTokens` | 40,000 (tail budget) |
| `normalMaxTokens` | 2,500 (per-msg limit, older) |
| `retainedMaxTokens` | 12,000 (per-msg limit, grace zone) |

Flow:
```text
1. enforceMessageSizeLimits(): two-tier per-message normalization
   - Grace zone (recent, within retainedTokens budget): 12K/msg max
   - Older messages: 2.5K/msg max
   - Proportional truncation preserving head + tail of each message
2. If total tokens > maxTokens (150K):
   - splitHistoryForTruncation(): backward scan, retain 40K tokens
   - Boundary adjusted for functionCall/functionResponse integrity
   - generateIntentSummary() via LLM (or fallback static marker)
   - Summary merged into first retained user message (Gemini role-alternation constraint)
```

**`<intent_summary>` format**: action path (tool chain), primary goal, verified facts, working set, active blockers. Includes "contextual bridge" (first 5 retained messages as lookahead).

### 2c. ToolOutputDistillationService (`packages/core/src/services/toolDistillationService.ts`)

Per-tool-call output distillation at return time. Exempt: `read_file`, `read_many_files`.

| Config | Default |
|--------|---------|
| `maxOutputTokens` | 10,000 |
| `summarizationThresholdTokens` | (from config) |
| `MAX_DISTILLATION_SIZE` | 1,000,000 chars |

For outputs exceeding threshold: save raw to disk, optionally generate LLM "intent summary" (15s timeout), then proportional head/tail truncation with saved-path reference.

### 2d. Tool-Level Summarizers (`packages/core/src/utils/summarizer.ts`)

Generic framework — tools can provide custom summarizers or use `llmSummarizer`. The `defaultSummarizer` just serializes content as-is.

### 2e. Session Persistence

Single JSON file per session (NOT JSONL), full rewrite per update, in-memory cache + dirty-check. Path: `~/.gemini/tmp/<project_hash>/chats/session-YYYY-MM-DDTHH-MM-XXXXXXXX.json`. No compact boundary markers — compression is in-memory only. Resume loads full JSON and reconstructs `Content[]` from `MessageRecord[]`.

Session summary: fire-and-forget on startup for previous session, 80-char one-liner for listing UI via `SessionSummaryService`.

---

## 3. OpenCode Architecture

**2 mechanisms** in a dual-phase pipeline, plus manual API.

### 3a. Prune Phase (`packages/opencode/src/session/compaction.ts`)

Lightweight no-LLM pre-pass that clears old tool outputs. Forked asynchronously at session loop end (`prompt.ts:1548`) — non-blocking, failures ignored via `Effect.ignore`.

Backwards scan from latest message:
- Skips first 2 conversation turns (protected)
- Stops at first assistant message with `summary: true` (previous compaction boundary)
- Collects completed tool outputs, skipping protected tools (`PRUNE_PROTECTED_TOOLS = ["skill"]`) and already-pruned outputs (those with `time.compacted` timestamp)
- Accumulates token count until `PRUNE_PROTECT` (40K) reached, then all subsequent tool outputs go into `toPrune`
- Commits only if `pruned > PRUNE_MINIMUM` (20K tokens freed)

Pruned outputs are **not deleted** — marked with `time.compacted = Date.now()`. Display converts to `"[Old tool result content cleared]"` in model messages (`message-v2.ts:718`).

| Constant | Value |
|----------|-------|
| `PRUNE_MINIMUM` | 20,000 tokens |
| `PRUNE_PROTECT` | 40,000 tokens |
| `PRUNE_PROTECTED_TOOLS` | `["skill"]` |

### 3b. Compact Phase (`packages/opencode/src/session/compaction.ts`)

Full LLM summarization, triggered by overflow detection.

**Overflow detection** (`overflow.ts`): `count >= usable` where `count` = total tokens (input + output + cache), `usable` = model context size − reserved buffer. Reserved buffer: `Math.min(COMPACTION_BUFFER, maxOutputTokens)` where `COMPACTION_BUFFER = 20,000`. Disabled when `config.compaction.auto === false`.

**Trigger points**:
- After agent step completes (`processor.ts:305`): checks `isOverflow()` on response usage
- Before starting new turn (`prompt.ts:1399`): if last finished turn is not a summary AND overflow detected
- On `ContextOverflowError` during turn (`processor.ts:417-420`): sets `ctx.needsCompaction = true`, interrupts stream via `takeUntil()`

**What gets summarized**: All previous messages, media stripped (`stripMedia: true`). For overflow cases, finds last user message without a CompactionPart, uses it as replay target, compacts everything before it.

**Summary template** (structured sections):
```text
## Goal — what the user is trying to accomplish
## Instructions — important instructions + plan/spec info
## Discoveries — notable technical learnings
## Accomplished — completed/in-progress/remaining work
## Relevant files / directories — files read/edited/created
```

**Result handling**:
- **Success with replay**: recreates user message from earlier turn, auto-continues
- **Success without replay**: injects synthetic continuation message
- **Failure** (context overflow during compaction itself): returns `"stop"` with `ContextOverflowError`

### 3c. Message Schema

`CompactionPart` stored per message: `{ type: "compaction", auto: boolean, overflow?: boolean }`. In model messages, converts to text: `"What did we do so far?"`.

Prune state on tool outputs: `state.time.compacted` timestamp marks cleared outputs. `filterCompacted()` traverses forward to first summary+compaction pair, returns all messages before that boundary for UI display.

### 3d. Manual Compaction

HTTP API endpoint `POST /:sessionID/summarize` — no slash command in interactive mode. Calls `SessionCompaction.create()` with `auto: false`, then invokes the standard prompt loop.

### 3e. Session Persistence

Messages stored in database with `PartTable` containing serialized schema per part. CompactionPart and `time.compacted` timestamps persist across restarts. No JSONL — structured DB storage.

---

## 4. Codex Architecture

**2 compaction modes** (inline LLM + remote provider API) with **3 trigger points** in the turn lifecycle.

### 4a. Trigger Points

**Pre-sampling** (`codex.rs:6147`): fires before user input is recorded. Two sub-checks:
- Model switch: `total_usage_tokens > new_auto_compact_limit AND old_context_window > new_context_window` — proactively compacts when switching to a smaller model
- Token threshold: `total_usage_tokens >= auto_compact_token_limit` (90% of context window, `openai_models.rs:297-304`)
- Uses `InitialContextInjection::DoNotInject` — clears context baseline, next turn reinjects fresh

**Post-sampling** (`codex.rs:5979`): fires after model response completes mid-turn when `token_limit_reached AND needs_follow_up`. Uses `InitialContextInjection::BeforeLastUserMessage` — injects context before last user message to match model training expectations (summary must be last item seen).

**Manual** (`Op::Compact`): dispatched via protocol (`codex.rs:4511`). Uses `DoNotInject`.

| Constant | Value |
|----------|-------|
| `auto_compact_token_limit` | 90% of model context window |
| `COMPACT_USER_MESSAGE_MAX_TOKENS` | 20,000 tokens |
| Configurable override | `model_auto_compact_token_limit` per model |

### 4b. Inline Compaction (Non-OpenAI Providers)

`run_inline_auto_compact_task()` in `compact.rs:54`. Sends full history to model with compaction prompt template:

```text
CONTEXT CHECKPOINT COMPACTION: Create a handoff summary for another LLM.
Include: current progress, key decisions, constraints/preferences,
what remains, critical data/references.
```

**Summary assembly** (`compact.rs:191-212`):
1. Extract last assistant message as summary body
2. Prepend `SUMMARY_PREFIX` (tells next model: "Another LLM started this; use its summary")
3. Collect all non-summary user messages via `collect_user_messages()`
4. Build compacted history with token budget

**User message retention** (`build_compacted_history_with_limit`, `compact.rs:337-390`):
- Iterate user messages **in reverse** (newest first)
- Accumulate tokens until `COMPACT_USER_MESSAGE_MAX_TOKENS` (20K) exhausted
- Truncate last included message if it exceeds remaining budget
- Reverse selected messages to restore chronological order

**Context window overflow during compaction**: removes oldest history item via `remove_first_item()`, retries with exponential backoff up to `stream_max_retries()`.

**Ghost snapshot preservation**: `GhostSnapshot` items (for `/undo`) are collected from original history and appended to compacted history — not sent to model but preserved for rollback.

### 4c. Remote Compaction (OpenAI Only)

`run_inline_remote_auto_compact_task()` in `compact_remote.rs:28`. Dispatched when `provider.is_openai()`.

**Pre-compaction trimming** (`trim_function_call_history_to_fit_context_window`, `compact_remote.rs:273-300`):
- Removes codex-generated items (tool outputs, developer messages) from newest→oldest
- Stops when hitting user input or when estimated tokens fit context window
- Trims generated content first, preserves user intent

**Remote call**: `model_client.compact_conversation_history()` — sends prompt + tools + instructions to OpenAI's native compaction endpoint.

**Post-compaction filtering** (`should_keep_compacted_history_item`, `compact_remote.rs:205-230`):
- Drops `developer` role messages (stale instructions from provider)
- Drops `user` messages without real user content (wrapper/prefix messages)
- Keeps assistant messages, real user messages, hook prompts

### 4d. Initial Context Injection

| Mode | When | Behavior |
|------|------|----------|
| `DoNotInject` | Pre-turn, manual `/compact` | Clears `reference_context_item`; next turn fully reinjects context |
| `BeforeLastUserMessage` | Post-sampling (mid-turn) | Injects initial context before last real user message; falls back to before last summary → before last compaction item → append |

### 4e. Session Persistence

In-memory `ContextManager` (`history.rs`) tracks history items + token estimates. Token estimation: `approx_token_count()` heuristic (~4 bytes/token for text, special handling for reasoning items and images). `CompactedItem` emitted as `RolloutItem` event on successful compaction. No JSONL boundary markers — compacted history replaces in-memory state directly.

### 4f. Failure Handling

- Context window exceeded during compaction → remove oldest item, retry (exponential backoff)
- Remote compaction failure → `log_remote_compact_failure()` with detailed diagnostics (token counts, model window, request size)
- No circuit breaker — retries up to `stream_max_retries()` then fails the turn

---

## 5. co-cli Architecture (Current)

**3 mechanisms** built on pydantic-ai's history processor chain.

### 5a. Tool Output Trim (`truncate_tool_returns` — processor #1)

Sync processor, fires before every model API call. Truncates `ToolReturnPart.content` exceeding `tool_output_trim_chars` (default 2000) in all messages except the last 2 (current turn). No LLM call.

### 5b. Sliding Window (`truncate_history_window` — processor #4)

**Inline async** processor in `_history.py`, triggers when estimated tokens exceed 85% of `resolve_compaction_budget()` result. Budget resolved from model quirks `context_window - max_tokens` → Ollama `llm_num_ctx` → 100K fallback (in `_compaction.py`). Computes head/tail boundaries (head = first exchange including ThinkingPart, tail = max(4, len(messages)//2)), drops middle, calls `summarize_messages()` inline to generate a summary, and injects it immediately. In-memory only — JSONL unchanged. Both the boundary computation and the LLM summarisation call run inside pydantic-ai's history processor chain, blocking the API call. A `[dim]Compacting conversation...[/dim]` indicator is shown during the LLM call.

**Circuit breaker** (`compaction_failure_count` on `CoRuntimeState`): incremented on `ModelHTTPError`/`ModelAPIError`, reset to 0 on success. At >= 3 consecutive failures, the LLM call is skipped and a static marker `"[N messages trimmed]"` is used directly. When `model_registry` is absent (sub-agents, tests), the static marker is used without incrementing the counter.

### 5c. Manual `/compact`

Foreground `summarize_messages()` LLM call (from `_compaction.py`). Replaces in-memory history with 2 messages. Writes `{"type":"compact_boundary"}` to JSONL. Increments `compaction_count` in session metadata. Shows budget visibility: `Compacted: N → 2 messages (est. XK → YK of ZK budget)`. Only mechanism that affects the JSONL.

### 5d. Session Persistence

JSONL append-only (`_transcript.py`). Compact boundary markers for resume skip (>5MB threshold). Line-by-line scan, `messages.clear()` on boundary.

---

## 6. Five-Way Comparison

### 6a. Compaction Triggers and Thresholds

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| Auto trigger | `contextWindow - output - 13K` (~85% effective) | 50% of token limit (~524K of 1M) | `count >= contextSize - reserved` (reserved default 20K) | 90% of model context window | 85% of `resolve_compaction_budget()` result |
| Manual trigger | `/compact` command | None — auto only | HTTP API `POST /:sessionID/summarize` | `/compact` protocol op | `/compact` command (shows budget visibility) |
| Blocking limit | `effectiveContext - 3K` → forced `/compact` | None | None | None | None |
| Token limit | Model-dependent (typically 200K) | 1,048,576 (all Gemini models) | Model context size from provider config | Model-dependent, configurable per-model | `resolve_compaction_budget()`: model quirks → Ollama `llm_num_ctx` → 100K fallback |

#### 6a-detail. Auto-Trigger Deep Dive (Source-Level)

| Aspect | fork-cc | gemini-cli (A/B) | opencode | codex | co-cli |
|--------|---------|-------------------|----------|-------|--------|
| **Fires when** | Between turns, after 3 pre-passes (snip → micro-compact → collapse), before model call | **A**: start of every turn, synchronous. **B**: before every `sendMessageStream()`. Mutually exclusive | After agent step completes (`processor.ts:305`); before starting new turn (`prompt.ts:1399`); on `ContextOverflowError` mid-turn | **Pre-sampling**: before user input recorded. **Post-sampling**: after model response mid-turn when limit exceeded. **Manual**: `/compact` | Last in history processor chain, after `truncate_tool_returns` → `detect_safety_issues` → `inject_opening_context`, before every model API call |
| **Trigger formula** | `tokenCount >= contextWindow − min(maxOutputTokens, 20K) − 13K` | **A**: `tokenCount >= 0.5 × tokenLimit`. **B**: Phase 1 per-msg normalization → Phase 2 if `totalTokens > 150K` | `tokens >= modelContextSize − min(COMPACTION_BUFFER, maxOutputTokens)` where `COMPACTION_BUFFER = 20K` | `total_usage_tokens >= auto_compact_token_limit` (90% of context window). Model switch: also fires when switching to smaller model | `token_count > int(resolve_compaction_budget(config, registry) × 0.85)` |
| **Typical threshold** | ~167K for 200K model. Warning at 147K. Blocking at 177K | **A**: ~524K. **B**: 150K high watermark, 40K retained tail | Model context − 20K (e.g., 180K for 200K model) | 90% of context window (e.g., 180K for 200K model) | Gemini: 835K. Ollama: `llm_num_ctx × 0.85`. Fallback: 85K |
| **Token counting** | Hybrid exact + estimation. JSON 2 bytes/tok, images 2K flat. Subtracts `snipTokensFreed` | Char-based heuristic. ASCII 0.25 tok/char, CJK 1.3 tok/char, images 3K flat | Simple 4 chars/token estimate on total usage (input + output + cache) | Byte-based heuristic: `approx_token_count()` (~4 bytes/token). Reasoning items: special formula. Images: 1844 tokens flat | Provider-reported preferred. Fallback: `total_chars // 4` |
| **Pre-processing before check** | 3-stage: snip → micro-compact → context-collapse | **A**: `truncateHistoryToBudget()` (50K function response budget). **B**: per-msg normalization (grace 12K, older 2.5K) | Prune phase runs async independently (fork+ignore). Not subtracted from threshold | Pre-sampling: may compact for model switch first. No separate pre-pass trimming | `truncate_tool_returns`: trims content > 2000 chars in all-but-last-2 messages |
| **Split strategy** | Entire history → summarizer (out-of-band Claude API call). PTL retry drops oldest turns | **A**: 70/30 char split, compress 70% via LLM (2 passes). **B**: 40K token tail, single LLM pass | All previous messages → compaction agent. Replay: finds last user msg without CompactionPart, compacts everything before it | Inline: entire history → model with compaction prompt. User messages preserved by recency (20K token budget). Remote (OpenAI): provider API handles distillation | Head/tail boundary, drop middle. `head_end` = first model output + 1. `tail_count = max(4, len(messages) // 2)` |
| **Circuit breaker** | 3 consecutive failures → stop. Cross-turn persistent | **A**: single boolean, session-scoped. One failure → truncation-only | Catastrophic: if compaction itself overflows → `ContextOverflowError` → stop. Prune failures: `Effect.ignore` | No circuit breaker. Retries with exponential backoff up to `stream_max_retries()`. Context overflow during compaction → remove oldest item, retry | 3 consecutive failures → static marker. Cross-turn persistent. `model_registry is None` → static marker without counting |
| **Env/config overrides** | `DISABLE_AUTO_COMPACT`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, etc. | Config: `compressionThreshold`, `contextManagement.*` | `config.compaction.auto` (enable/disable), `config.compaction.reserved` (buffer override) | `model_auto_compact_token_limit` (per-model override), `model_context_window` (override) | `resolve_compaction_budget()` 3-tier: model quirks → Ollama `llm_num_ctx` → 100K fallback |

#### 6a-assessment. Comparison and Adoption Assessment

##### Trigger Timing: Where in the Turn Cycle

All five fire **before the model call**, but codex adds a second trigger point: **post-sampling** (mid-turn, after model response during tool-calling loops). opencode also fires **mid-turn** on `ContextOverflowError`. The remaining three (fork-cc, gemini-cli, co-cli) compact only between turns.

Mid-turn compaction is relevant for long tool-calling sequences where context grows within a single turn. co-cli's inline compaction runs inside the history processor chain, which fires before every model API call (including tool-resumption calls within a turn) — so co-cli already has implicit mid-turn coverage without an explicit second trigger point.

**Gap for co-cli**: None — the history processor architecture provides equivalent coverage. The explicit mid-turn triggers in codex and opencode solve a problem that co-cli's per-API-call processor already addresses.

##### Threshold Calibration

The five systems cluster into two approaches:

- **Percentage-based** (4/5): fork-cc ~85%, codex 90%, co-cli 85%, opencode ~90% (context − 20K buffer ≈ 90% for 200K models). All within the 85–90% range.
- **Absolute ceiling** (1/5): gemini-cli Mechanism B uses 150K absolute, regardless of 1M window.

**Assessment**: co-cli's 85% is well within the converged range. No change needed.

##### Token Counting Accuracy

| Strategy | Systems | Accuracy |
|----------|---------|----------|
| Provider-reported + heuristic fallback | co-cli, fork-cc | **Highest when available**, weakest on fallback |
| Pure byte/char heuristic (~4 chars/token) | opencode, codex | **Consistent** but no exact baseline |
| Char-based with script awareness | gemini-cli | **Medium** — CJK-aware but no exact baseline |

4/5 systems use a ~4 chars/token (or ~4 bytes/token) heuristic as baseline or fallback. co-cli's `chars // 4` is in line. The main gap remains: no JSON-density adjustment (fork-cc uses 2 bytes/token for JSON).

**Assessment**: co-cli's approach matches the majority. JSON-density ratio remains P3.

##### Pre-Processing Pipeline Depth

| Depth | Systems |
|-------|---------|
| Multi-stage pipeline before threshold check | fork-cc (3-stage), gemini-cli B (per-msg normalization) |
| Separate async prune phase (independent) | opencode (prune fork+ignore) |
| No pre-processing before threshold | codex, co-cli (tool trim runs but not subtracted) |

co-cli and codex share the simplest approach. opencode's async prune is interesting but not subtracted from the threshold either. **No change in priority** — the existing P2 recommendation for two-tier normalization (§8b #3) covers this gap.

##### Split Strategy and What Gets Summarized

All five summarize via LLM and preserve recent context. The split strategies:

- **Preserve recent user messages by token budget**: codex (20K), gemini-cli B (40K)
- **Preserve recent by message count**: co-cli (`max(4, len(messages) // 2)`), opencode (replay last user msg)
- **Preserve recent by percentage**: gemini-cli A (last 30%)
- **Send all to summarizer**: fork-cc

3/5 systems (codex, gemini-cli A, gemini-cli B) use a **token or character budget** to determine the tail, not a message count. co-cli's `max(4, len(messages) // 2)` is message-count-based, which can over- or under-preserve depending on message sizes.

**New gap**: Token-based tail sizing is the majority pattern. co-cli should consider converting `tail_count` from message count to a token budget. Low priority — current heuristic works but is less precise.

##### Circuit Breaker Design

| Design | Systems |
|--------|---------|
| Counter (3 failures → stop) | fork-cc, co-cli |
| Single boolean (1 failure → fallback) | gemini-cli A |
| No circuit breaker (retry with backoff) | codex |
| Catastrophic stop (overflow during compaction) | opencode |

3/5 have explicit circuit breakers. co-cli matches the strongest design. **No change needed.**

##### Summary of Adoption Priorities (Trigger-Specific)

| # | Item | Priority | Rationale |
|---|------|----------|-----------|
| 1 | ~~**Remove message count trigger**~~ | **DONE** | Token-only trigger, tail-count decoupled |
| — | Head-pinning in split | **Keep** | Preserves personality/context establishment. No peer does this — co-cli-specific value |
| — | Circuit breaker (3 failures + registry-absent path) | **Keep** | Matches strongest peer design (fork-cc) |
| 2 | JSON-density ratio in fallback estimator | **P3** | Add `chars // 2` for dict content. Matches fork-cc. Benefits Ollama users |
| 3 | Subtract freed tokens from threshold check | **P3** | After `truncate_tool_returns`, subtract delta. Reduces over-triggering |
| 4 | Two-tier per-message normalization | **P2** | Grace zone + older. 3/5 peers have granular per-message sizing (gemini-cli B, opencode prune thresholds, fork-cc micro-compact) |
| 5 | Warning tiers before compaction | **P3** | fork-cc has 4 tiers; others have none. Nice UX, not load-bearing |
| 6 | Token-based tail sizing | **P3** | 3/5 use token budget for tail (codex 20K, gemini-cli 30%/40K). co-cli uses message count. Less precise but functional |

### 6b. Compression Method

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| Summary engine | Out-of-band Claude API call (forked agent) | In-process LLM call (same client) | Dedicated compaction agent (in-process) | Same model or remote provider API | Inline `summarize_messages()` (blocks model request) |
| Summary passes | 1 | 2 (generate + verification probe) | 1 | 1 | 1 |
| Summary format | Free-form text | Structured XML (`<state_snapshot>`) | Structured sections (Goal, Instructions, Discoveries, Accomplished, Files) | Free-form text ("handoff summary") | Free-form text |
| Multi-cycle survival | Implicit (prior summary in post-boundary messages) | Explicit (new snapshot integrates prior `<state_snapshot>`) | Implicit (summary is `assistant` msg with `summary: true` flag) | Implicit (prior summary prefixed with `SUMMARY_PREFIX`) | Implicit (prior summary in messages that get re-summarized) |
| Prompt context | Rich: plan + memory + file state + tools + MCP + agents | History + compression system prompt + plan path | History + compaction template (overridable via plugin hook) | Compaction prompt template (overridable via config) | Bare: conversation history only |
| Split strategy | Entire history → summarizer | 70/30 char-based split, keep last 30% | All messages → compaction agent; replay last user msg on overflow | User messages preserved by 20K token budget + summary | Head/tail boundary, drop middle |
| Post-compaction continuation | N/A (boundary marker, resume naturally) | N/A (summary replaces history head) | Auto-replay of triggering user message; synthetic continuation message if no replay | N/A (summary is last item, next turn continues) | N/A (summary injected, model call proceeds) |
| Fallback on failure | Circuit breaker (3 failures → stop) | Circuit breaker (skip LLM, truncate-only) | `ContextOverflowError` → stop with user-facing error | Remove oldest item, retry with backoff | Circuit breaker (3 failures → static marker) |

### 6c. Tool Output Management

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| When it fires | Before full compact (micro-compact pre-pass) | At tool return time AND before compression | Async prune phase after turn completes (fork+ignore) | Pre-compaction: trim function calls newest→oldest | Before every model API call (always-on processor) |
| LLM-assisted | No | Yes — intent summary (15s timeout) | No | No (compaction itself is LLM, but tool trimming is mechanical) | No |
| File-read optimization | `FILE_UNCHANGED_STUB` for unchanged reads | Exempt `read_file`/`read_many_files` from distillation | None | None | None |
| Per-message normalization | No | Two-tier: grace 12K/msg, older 2.5K/msg | Token-based: `PRUNE_PROTECT` 40K threshold, `PRUNE_MINIMUM` 20K commit threshold | No per-message (trim generated items before user items) | Binary: trim all-but-last-2 at char threshold |
| Protected tools | Compactable set (file read, shell, grep, glob, web search/fetch, file edit/write) | Exempt `read_file`/`read_many_files` | `PRUNE_PROTECTED_TOOLS = ["skill"]` | User messages, ghost snapshots | None — uniform trim |
| Disk save of full output | No | Yes — temp file | No (pruned content marked, not saved) | No | No |
| Non-destructive | No (content replaced) | No (distilled/truncated) | **Yes** — marks `time.compacted`, display shows `[Old tool result content cleared]` | No (items removed from history) | No (content truncated in-place) |

### 6d. Persistence and Resume

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| Format | JSONL append-only | Single JSON, full rewrite | Structured DB (PartTable) | In-memory ContextManager | JSONL append-only |
| Boundary markers | Rich `SystemCompactBoundaryMessage` with metadata | None — compression is in-memory | `CompactionPart` per message (`auto`, `overflow` flags) + `summary: true` on assistant msg | `CompactedItem` emitted as event | Minimal `{"type":"compact_boundary"}` |
| Resume optimization | Stream 65KB chunks, skip to last boundary (>5MB) | Load full JSON | DB query (structured storage, no scan needed) | N/A (in-memory, no resume) | Line-by-line scan, skip to last boundary (>5MB) |
| Preserved segment | Explicit UUID pointers in boundary metadata | N/A | `filterCompacted()` traverses to boundary for UI | Ghost snapshots preserved for `/undo` | Head pinning (structural guarantee) |

### 6e. Safety Mechanisms

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| Circuit breaker | 3 consecutive failures → stop | `hasFailedCompressionAttempt` → truncate-only | Catastrophic stop on compaction overflow | No breaker — retry with backoff | 3 consecutive failures → static marker |
| Recursion guard | Won't fire inside forked agents | N/A | N/A | N/A | N/A (`model_registry is None` path) |
| 413/overflow recovery | Reactive compact (feature-gated) | `AgentHistoryProvider` prevents overflow proactively | Mid-turn: `ContextOverflowError` → `needsCompaction` flag → compact and retry | Post-sampling: compact when `token_limit_reached AND needs_follow_up`. Context overflow during compaction → remove oldest, retry | None (relies on 85% pre-emptive threshold) |
| Summary inflation guard | N/A | Reject if new tokens > original | N/A | N/A | N/A |
| Adversarial content guard | N/A | Security rule in compression prompt | N/A | N/A | Security rule in summarizer system prompt |
| Doom loop detection | N/A | N/A | `DOOM_LOOP_THRESHOLD = 3` identical tool calls → user approval | N/A | `doom_loop_threshold = 3` → warning injection |

---

## 7. Convergence Analysis

Patterns where **3+ systems** agree — strongest signal for adoption.

### 7a. All Five Agree (5/5)

| Pattern | fork-cc | gemini-cli | opencode | codex | co-cli | Status |
|---------|---------|-----------|----------|-------|--------|--------|
| Auto-compaction via LLM summary | ✓ | ✓ | ✓ | ✓ | ✓ | Implemented |
| Token-based trigger (not message count) | ✓ | ✓ | ✓ | ✓ | ✓ | Implemented |
| Tool output trimming (no-LLM pre-pass) | ✓ micro-compact | ✓ distillation | ✓ prune phase | ✓ trim function calls | ✓ `truncate_tool_returns` | Implemented |
| ~4 chars/token heuristic for estimation | ✓ (4 bytes/tok default) | ✓ (`length/4` fast path) | ✓ (4 chars = 1 token) | ✓ (`approx_token_count` ~4 bytes/tok) | ✓ (`chars // 4`) | Implemented |
| Threshold in 85–90% range | ✓ ~85% | ✓ (Mechanism B: 150K absolute) | ✓ ~90% | ✓ 90% | ✓ 85% | Implemented |

### 7b. Four of Five Agree (4/5)

| Pattern | Who has it | Who lacks it | co-cli status |
|---------|-----------|-------------|---------------|
| **Protect recent turns from trimming** (last N turns or last X% exempted from tool output pruning) | fork-cc, gemini-cli, opencode (skip first 2 turns), co-cli (last 2 messages) | codex (trims newest→oldest for remote, but preserves user messages for inline) | **Implemented** |
| **Circuit breaker for compaction failures** | fork-cc (3), gemini-cli (1), co-cli (3), opencode (catastrophic stop) | codex (retry with backoff, no breaker) | **Implemented** |
| **Structured or sectioned summary template** | gemini-cli (`<state_snapshot>` XML), opencode (Goal/Instructions/Discoveries/Accomplished/Files), codex ("handoff summary" with structured prompt), fork-cc (rich prompt context) | **co-cli** (bare free-form text) | **Gap — P1** |
| **Manual compact command** | fork-cc (`/compact`), co-cli (`/compact`), opencode (HTTP API), codex (`Op::Compact`) | gemini-cli (auto-only) | **Implemented** |
| **Configurable compaction threshold** | fork-cc (env vars), opencode (`config.compaction`), codex (`model_auto_compact_token_limit`), co-cli (3-tier budget resolution) | gemini-cli (config exists but limited) | **Implemented** |
| **Protected/exempt tools in pruning** | fork-cc (compactable set), gemini-cli (exempt read_file), opencode (`PRUNE_PROTECTED_TOOLS`), codex (preserve user messages over generated) | **co-cli** (uniform trim, no per-tool distinction) | **Gap — P2** |
| **Overflow/413 recovery** (compact-and-retry when context exceeded) | fork-cc (reactive compact), opencode (`ContextOverflowError` → compact), codex (post-sampling compact + remove-oldest retry), gemini-cli B (proactive prevention) | **co-cli** | **Gap — P2** |

### 7c. Three of Five Agree (3/5)

| Pattern | Who has it | Who lacks it | co-cli status |
|---------|-----------|-------------|---------------|
| **Rich compaction prompt** (plan, tools, file state — not just history) | fork-cc (plan + memory + file state + tools), gemini-cli (plan path + step status), opencode (overridable via plugin hook with context injection) | codex (generic template), **co-cli** (bare summarizer) | **Gap — P1** |
| **Token-based tail sizing** (tail budget in tokens, not message count) | codex (20K user message budget), gemini-cli A (30% chars), gemini-cli B (40K tokens) | fork-cc (all → summarizer), **co-cli** (`max(4, len(messages) // 2)` message count) | **Gap — P3** |
| **Doom loop detection** (repeated identical tool calls) | opencode (3 identical → approval), co-cli (3 identical → warning), fork-cc (implicit via micro-compact stability) | codex, gemini-cli | **Implemented** |

### 7d. Two of Five Agree (2/5) — Monitor

| Pattern | Who | Status |
|---------|-----|--------|
| Disk save of truncated tool output | gemini-cli (temp file), fork-cc (micro-compact file save) | Defer |
| Two-pass verification of summary | gemini-cli (probe pass) | Skip — cost too high |
| Non-destructive compaction (mark, don't delete) | opencode (`time.compacted` timestamp) | Monitor — interesting UX but adds complexity |
| Provider-delegated compaction | codex (OpenAI remote compact API) | Monitor — not available for non-OpenAI providers |
| Ghost snapshot / undo preservation through compaction | codex (`GhostSnapshot`) | Monitor — co-cli has no undo mechanism yet |
| `FILE_UNCHANGED_STUB` for repeated reads | fork-cc | Consider — simple, no LLM cost |
| Post-compaction auto-continuation | opencode (replay user msg), codex (summary is last item) | Monitor — co-cli's inline processor model doesn't need explicit replay |

---

## 8. Adoption Recommendations for co-cli

Ranked by convergence weight (peer count × production validation × impact).

### Priority 1 — Majority Converge (4/5+), Clear Gap

| # | Recommendation | Evidence | Effort | Risk |
|---|---------------|----------|--------|------|
| 1 | ~~**Circuit breaker**~~ | ~~4/5 have circuit breakers~~ | **DONE** — `compaction_failure_count` on `CoRuntimeState`, threshold 3 | — |
| 2 | **Structured summary template** | 4/5 use structured/sectioned summaries (gemini-cli XML, opencode Goal/Instructions/Discoveries/Accomplished/Files, codex handoff template, fork-cc rich prompt). co-cli: bare free-form text. Directly impacts multi-cycle summary quality and information retention. | Low — define markdown template with sections (Goal, Key Decisions, Working Set, Remaining Work, Files), add to `summarize_messages()` prompt | None — additive, degrades gracefully to free-form if model ignores structure |
| 3 | **Enrich compaction prompt with context** | 3/5 include plan/tool/file context beyond bare history (fork-cc, gemini-cli, opencode). co-cli: history only. | Medium — gather active plan, registered tools, recent file paths into summarization prompt | None — fallback to bare summary if context unavailable |
| 4 | **Protected/exempt tools in pruning** | 4/5 distinguish compactable vs non-compactable tools (fork-cc compactable set, gemini-cli exempt read_file, opencode `PRUNE_PROTECTED_TOOLS`, codex preserve user over generated). co-cli: uniform `tool_output_trim_chars = 2000` for all. | Medium — define `COMPACTABLE_TOOLS` set, per-tool-type recency in `truncate_tool_returns` | Low — already scoped in existing TODO-tool-output-compaction |
| 5 | **Overflow/413 recovery** | 4/5 have some form of recovery when context is exceeded (fork-cc reactive compact, opencode `ContextOverflowError` → compact, codex post-sampling compact + remove-oldest, gemini-cli B proactive prevention). co-cli relies solely on 85% pre-emptive threshold. | Medium — catch context-exceeded errors from provider, trigger emergency compaction (drop middle + static marker), retry the failed request | Low — fail-safe path, only fires when pre-emptive threshold is insufficient |

### Priority 2 — Strong Signal, Moderate Effort

| # | Recommendation | Evidence | Effort | Risk |
|---|---------------|----------|--------|------|
| 6 | **Two-tier per-message normalization** (grace zone + older) | gemini-cli: 12K/2.5K. opencode: 40K/20K prune thresholds. fork-cc: micro-compact compactable set. co-cli: binary trim all-but-last-2. | Medium — extend `truncate_tool_returns` with per-message token budgets and grace zone | Low — more granular, degrades gracefully |
| 7 | **Structured summary with prior-snapshot integration** | gemini-cli: new `<state_snapshot>` explicitly integrates prior snapshots. codex: `SUMMARY_PREFIX` tells next model to build on prior summary. 2/5 have explicit multi-cycle integration. | Medium — add "integrate prior summary" instruction to template, detect prior summary in history | Low — improves quality over repeated compaction cycles |

### Priority 3 — Monitor / Defer

| # | Recommendation | Trigger |
|---|---------------|---------|
| 8 | `FILE_UNCHANGED_STUB` for repeat file reads | When users report context bloat from repeated reads of same file |
| 9 | Token-based tail sizing (replace message count with token budget) | When message-count heuristic causes over/under-preservation in practice |
| 10 | JSON-density ratio in fallback estimator | Low effort. Add `chars // 2` for dict content. Benefits Ollama users |
| 11 | Subtract freed tokens from threshold check | Low effort. Reduces over-triggering |
| 12 | Warning tiers before compaction | Nice UX. fork-cc has 4 tiers; no other peer does. 1/5 signal |
| 13 | Disk save of full truncated tool output | When users need to review truncated content post-session |
| 14 | LLM-assisted tool output distillation | When tool output quality issues surface in long sessions |
