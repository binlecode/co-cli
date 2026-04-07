# RESEARCH: Peer Session Compaction — Five-Way Comparison

Sources: `~/workspace_genai/fork-claude-code`, `~/workspace_genai/gemini-cli`, `~/workspace_genai/opencode`, `~/workspace_genai/codex`, co-cli codebase
Scan date: 2026-04-03 (fork-cc, gemini-cli, co-cli) · 2026-04-04 (opencode, codex)

---

## 1. Turn-by-Turn Compaction Pipelines

Each system runs a multi-stage pipeline per turn. The critical design question is: **what does the LLM summarizer see as input, and what has already been stripped?**

### 1a. fork-cc — Out-of-Band Summarizer with Rich Context

```text
TURN ENTRY (query loop)
  ├─ 1. Snip:        structural trimming (system messages, old directives)
  ├─ 2. Micro-compact: no-LLM pre-pass — content-clear compactable tool results
  │     (FILE_READ→FILE_UNCHANGED_STUB, shell, grep, glob, web_*, file_edit/write)
  ├─ 3. Context-collapse: structural collapse of redundant context
  ├─ 4. Token check:  tokenCount >= contextWindow − maxOutput − 13K?
  │     └─ NO → proceed to model call
  │     └─ YES ↓
  ├─ 5. compactConversation() — OUT-OF-BAND Claude API call (forked agent)
  │     ├─ INPUT: full remaining history (post-micro-compact)
  │     │         + RICH CONTEXT: system prompt, plan state, memory list,
  │     │           file state, deferred tools, MCP instructions, agent listing
  │     ├─ OUTPUT: CompactionResult (boundary, summary, attachments, preserved segment)
  │     ├─ PTL retry: if prompt-too-long, drop oldest turns, retry (max 3)
  │     └─ Circuit breaker: 3 consecutive failures → stop
  └─ 6. Write SystemCompactBoundaryMessage to JSONL + message list
```

**Key insight**: The LLM summarizer sees the post-micro-compact history (tool results already cleared) **BUT** receives rich side-channel context (plan, memory, file state, tools) that compensates for the cleared content. The summarizer knows what files were touched even if the tool outputs are gone.

### 1b. gemini-cli — Two Independent Mechanisms (A + B)

**Mechanism A: ChatCompressionService** (fires at start of every turn)

```text
TURN ENTRY
  ├─ 1. truncateHistoryToBudget(): reverse-scan function responses,
  │     truncate older ones exceeding 50K budget (save full to temp file)
  │     ⚠ THIS IS PRE-COMPACTION TRIMMING — raw outputs saved to disk first
  ├─ 2. Token check: tokenCount >= 50% of model limit (~524K)?
  │     └─ NO → proceed
  │     └─ YES ↓
  ├─ 3. findCompressSplitPoint(): 70/30 char split at user-turn boundary
  ├─ 4. HIGH FIDELITY DECISION: send original (UN-truncated) to-compress
  │     segment when it fits within model limit
  │     ⚠ THE SUMMARIZER GETS THE ORIGINAL CONTENT, NOT THE TRUNCATED VERSION
  ├─ 5. LLM call #1: generate <state_snapshot> XML summary
  │     Sections: scratchpad, task_state, key_knowledge, active_constraints,
  │               working_set, active_blockers
  │     Prior integration: new snapshot explicitly integrates prior <state_snapshot>
  ├─ 6. LLM call #2: "Probe" verification — self-correction pass
  ├─ 7. Inflation guard: reject if new token count > original
  └─ 8. Build: [summary, ack, ...kept tail (last 30%)]
```

**Mechanism B: AgentHistoryProvider** (fires before every sendMessageStream)

```text
  ├─ 1. enforceMessageSizeLimits(): two-tier per-message normalization
  │     Grace zone (recent, within 40K tail): 12K/msg max
  │     Older messages: 2.5K/msg max
  │     Proportional truncation: head + tail preserved per message
  ├─ 2. Token check: totalTokens > 150K?
  │     └─ NO → proceed
  │     └─ YES ↓
  ├─ 3. splitHistoryForTruncation(): backward scan, retain 40K tokens
  │     Boundary adjusted for functionCall/functionResponse integrity
  ├─ 4. generateIntentSummary() via LLM (or static marker fallback)
  │     Format: <intent_summary> with action path, goal, facts, working set,
  │             blockers + "contextual bridge" (first 5 retained messages)
  └─ 5. Summary merged into first retained user message (role-alternation)
```

**Key insight**: Mechanism A **preserves original content for the summarizer** — the pre-truncation is for the working history, but the LLM compress pass gets the full un-truncated segment when it fits. The summarizer never works with degraded input. Mechanism B's per-message normalization runs independently as a continuous pre-pass.

### 1c. opencode — Async Prune + Overflow-Triggered Compact

```text
TURN END (async, non-blocking, failures ignored)
  └─ PRUNE PHASE: backward scan from latest message
      ├─ Skip first 2 turns (protected)
      ├─ Stop at previous compaction boundary (assistant msg with summary: true)
      ├─ Accumulate tokens until PRUNE_PROTECT (40K) → all subsequent → toPrune
      ├─ Protected tools: ["skill"] exempt from pruning
      ├─ NON-DESTRUCTIVE: mark time.compacted, don't delete
      │   Display: "[Old tool result content cleared]"
      └─ Commit only if pruned > PRUNE_MINIMUM (20K tokens freed)

NEXT TURN ENTRY (or mid-turn on ContextOverflowError)
  ├─ Overflow check: tokens >= modelContextSize − min(20K, maxOutput)?
  │     └─ NO → proceed
  │     └─ YES ↓
  ├─ COMPACT PHASE: all previous messages → compaction agent
  │     INPUT: all messages, media stripped
  │     ⚠ INCLUDES PRUNED MESSAGES (still in history, just marked)
  │     Template sections: Goal, Instructions, Discoveries, Accomplished,
  │                       Relevant files/directories
  │     Overridable via plugin hook (context injection point)
  ├─ Result:
  │     Success + replay: recreate triggering user message, auto-continue
  │     Success + no replay: inject synthetic continuation
  │     Failure (overflow during compaction): ContextOverflowError → stop
  └─ CompactionPart marker stored on message
```

**Key insight**: Prune is non-destructive (marks, doesn't delete) — the compaction agent still has access to all messages, including pruned ones. The pruning only affects the model's working context, not the summarizer's input.

### 1d. codex — Dual-Mode with User Message Preservation

**Inline compaction (non-OpenAI providers)**

```text
PRE-SAMPLING (before user input recorded)
  ├─ Token check: total_usage >= 90% of context window?
  │     Also: model switch check (switching to smaller model)
  │     └─ NO → proceed
  │     └─ YES ↓
  ├─ COMPACT: entire history → model with compaction prompt
  │     Template: "CONTEXT CHECKPOINT COMPACTION: Create a handoff summary"
  │     Sections: current progress, key decisions, constraints/preferences,
  │               what remains, critical data/references
  │     No pre-trimming of tool outputs before sending to summarizer
  ├─ Summary assembly:
  │     1. Extract last assistant message as summary body
  │     2. Prepend SUMMARY_PREFIX ("Another LLM started this; use its summary")
  │     3. Collect user messages by 20K token budget (newest-first)
  │     4. Build compacted history = [summary + retained user messages]
  ├─ Overflow during compaction: remove oldest item, retry (exponential backoff)
  └─ GhostSnapshot preservation: undo snapshots carried through compaction

POST-SAMPLING (mid-turn, after model response)
  ├─ Fires when: token_limit_reached AND needs_follow_up
  ├─ Same compaction logic, different context injection:
  │   BeforeLastUserMessage — injects summary before last user message
  └─ (Match model training expectations: summary must be last item seen)
```

**Remote compaction (OpenAI only)**

```text
  ├─ Pre-compaction trim: remove codex-generated items (tool outputs,
  │   developer messages) newest→oldest until estimated tokens fit
  │   ⚠ TRIMS GENERATED CONTENT, PRESERVES USER MESSAGES
  └─ Remote call: provider API handles distillation
```

**Key insight**: Inline mode sends **entire unmodified history** to the summarizer — no pre-trimming. Remote mode trims generated content but preserves user messages. Both ensure the summarizer has maximum context.

### 1e. co-cli — Inline Processor Chain (Current)

```text
BEFORE EVERY MODEL API CALL (history processor chain)
  ├─ 1. truncate_tool_results [sync, no LLM]
  │     Compactable tools: read_file, run_shell_command, find_in_files,
  │                        list_directory, web_search, web_fetch
  │     Per-tool-type recency: keep 5 most recent per tool, clear rest
  │     Protected: last turn (from last UserPromptPart onward)
  │     DESTRUCTIVE: replaces content with placeholder in-place
  │
  ├─ 2. compact_assistant_responses [sync, no LLM]
  │     Caps TextPart/ThinkingPart in older ModelResponse at 2.5K chars
  │     Proportional head(20%)/tail(80%) truncation (aligned with gemini-cli)
  │     Protected: last turn (same boundary as #1)
  │     Does NOT touch ToolCallPart args (critical for context enrichment)
  │
  ├─ 3. detect_safety_issues [sync, no LLM]
  │     Doom-loop detection + shell error streak detection
  │
  ├─ 4. inject_opening_context [async, no LLM]
  │     Memory recall (FTS5/BM25) on each new user turn
  │
  ├─ 5. summarize_history_window [async, LLM call]
  │     Token check: estimated tokens > 85% of budget?
  │     └─ NO → proceed to model call
  │     └─ YES ↓
  │     Boundary: head_end = first model output + 1
  │               tail_start = snap to turn boundary at len - max(4, len//2)
  │     Dropped = messages[head_end:tail_start]
  │     Context enrichment via _gather_compaction_context():
  │       - File working set from ToolCallPart.args (never truncated)
  │       - Pending session todos
  │       - Always-on memories
  │       - Prior-summary text from dropped messages
  │       - Capped at 4K chars
  │     LLM call: summarize_messages(dropped, resolved_model, context=enrichment)
  │       Structured template: Goal, Key Decisions, Working Set, Progress, Next Steps
  │       Prior-summary integration instruction in template
  │     Fallback: static marker after 3 consecutive failures
  │     Result: [head messages] + [summary marker] + [tail messages]
  │
  └─ MODEL API CALL with compacted history

OVERFLOW RECOVERY (in run_turn() error handler)
  ├─ _is_context_overflow(): status 400/413 + context-length body pattern
  ├─ emergency_compact(): first group + static marker + last group (no LLM)
  ├─ One-shot retry; terminal on second overflow or ≤2 groups
  └─ Never falls through to 400 reformulation handler
```

---

## 2. The Input Quality Problem

The critical difference between co-cli and all four peers:

| System | What the LLM summarizer sees | How it compensates for cleared tool outputs |
|--------|----------------------------|--------------------------------------------|
| **fork-cc** | Post-micro-compact history (tool results cleared) | **Rich side-channel**: plan, memory, file state, tools, MCP, agents injected into summarizer prompt |
| **gemini-cli A** | **Original un-truncated content** (high-fidelity decision) | N/A — summarizer gets full content when it fits |
| **gemini-cli B** | Per-message normalized content (head+tail preserved) | `<intent_summary>` format with contextual bridge (5 retained messages as lookahead) |
| **opencode** | All messages including pruned ones (non-destructive marking) | N/A — pruning is display-only, summarizer sees full history |
| **codex** (inline) | **Entire unmodified history** | N/A — no pre-trimming |
| **codex** (remote) | Trimmed generated content, preserved user messages | Provider API handles distillation with full context |
| **co-cli** | Post-truncation messages (tool results cleared, assistant text capped) | **Rich side-channel context**: file working set from ToolCallPart.args, session todos, always-on memories, prior-summary text (fork-cc strategy C) |

**Peer strategies for preserving summarizer input quality:**

1. **Don't trim before summarizing** (codex inline, opencode): send full history to summarizer. Simplest but requires model to handle large input.
2. **Trim but save originals** (gemini-cli A): trim for working context, but feed originals to summarizer when they fit. Best quality.
3. **Trim but compensate with side-channel** (fork-cc): accept trimmed input but inject plan/file/tool context that reconstructs what was lost. Good quality, requires rich context gathering.
4. **Non-destructive marking** (opencode): prune phase marks content as cleared for the model's working context, but the full content remains in the message list for the compaction agent.

co-cli uses **strategy C** (rich side-channel context via `_gather_compaction_context()`). The summarizer sees post-truncation messages but receives file working set, session todos, always-on memories, and prior-summary text as compensating context.

---

## 3. Pre-Compaction Mechanisms Compared

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| **Pre-pass name** | micro-compact | truncateHistoryToBudget (A) / enforceMessageSizeLimits (B) | prune phase | trim function calls (remote only) | truncate_tool_results |
| **When** | Before full compact (3-stage pipeline) | Start of every turn (A) / Before every send (B) | Async at turn end (non-blocking) | Before remote compact only | Before every model API call |
| **LLM involved** | No | No | No | No | No |
| **Compactable set** | FILE_READ, shell, grep, glob, web_*, file_edit/write | All function responses (50K budget) | All tool outputs except `["skill"]` | All generated items (tool outputs, developer msgs) | read_file, shell, find_in_files, list_directory, web_search, web_fetch |
| **Recency protection** | Implicit (runs before compact, not per-call) | A: 50K token budget protects recent. B: 40K retained tokens | First 2 turns protected + 40K token window | Trims newest→oldest (preserves user msgs) | Per-tool-type: keep 5 most recent per tool; protect last turn (from last UserPromptPart) |
| **Destructive?** | Yes — content replaced | Yes — truncated (full saved to disk) | **No** — marks `time.compacted`, content remains | Yes — items removed | Yes — content replaced with placeholder |
| **Disk save** | FILE_UNCHANGED_STUB (stub, not full save) | **Yes** — full output saved to temp file | No | No | No |
| **Per-message caps** | No | **Yes** — 12K recent / 2.5K older (Mechanism B) | No per-message (token threshold) | No | **Yes** — 2.5K older (compact_assistant_responses) |

---

## 4. LLM Summarization Compared

| Aspect | fork-cc | gemini-cli A | gemini-cli B | opencode | codex | co-cli |
|--------|---------|-------------|-------------|----------|-------|--------|
| **Execution model** | Out-of-band (forked agent) | In-process, synchronous | In-process, synchronous | In-process, dedicated agent | In-process (same model or remote API) | Inline (blocks model request) |
| **Summary format** | Free-form text | Structured XML (`<state_snapshot>`: scratchpad, task_state, key_knowledge, constraints, working_set, blockers) | `<intent_summary>`: action path, goal, facts, working set, blockers + contextual bridge | Structured sections (Goal, Instructions, Discoveries, Accomplished, Files) | Free-form ("handoff summary") | **Structured sections** (Goal, Key Decisions, Working Set, Progress, Next Steps) |
| **Prompt context** | Rich: plan + memory + file state + tools + MCP + agents | History + compression prompt + plan path | History + intent summary prompt | History + template (overridable via hook) | Compaction prompt template | **Rich side-channel**: file working set, session todos, always-on memories, prior-summary text (4K cap) |
| **Prior-summary integration** | Implicit (prior in post-boundary messages) | **Explicit** (new snapshot integrates prior `<state_snapshot>`) | Implicit (summary in first retained msg) | Implicit (summary as `assistant` msg) | **Explicit** (`SUMMARY_PREFIX` tells model to build on prior) | **Explicit** (template instruction + prior-summary text in context enrichment) |
| **Passes** | 1 | **2** (generate + verification probe) | 1 | 1 | 1 | 1 |
| **Inflation guard** | No | **Yes** — reject if new tokens > original | No | No | No | No |
| **What it summarizes** | Full remaining history | 70% of history (un-truncated when fits) | Retained head after per-msg normalization | All previous messages (including pruned) | Entire history (inline) / trimmed history (remote) | Dropped middle messages (post-truncation) + side-channel context enrichment |

---

## 5. Overflow/413 Recovery Compared

| Aspect | fork-cc | gemini-cli | opencode | codex | co-cli |
|--------|---------|-----------|----------|-------|--------|
| **Detection** | `prompt_too_long` error (413) | Mechanism B proactive prevention (150K ceiling) | `ContextOverflowError` mid-turn | `token_limit_reached AND needs_follow_up` | `_is_context_overflow()`: status 400/413 + context-length body pattern |
| **Recovery action** | Reactive compact (feature-gated) + PTL retry loop (drop oldest turns, max 3) | Mechanism B fires before send, prevents overflow | Sets `needsCompaction` flag → compact + auto-replay of triggering user message | Post-sampling compact + `BeforeLastUserMessage` injection. Overflow during compaction → remove oldest, retry | `emergency_compact()`: first + last turn group + static marker (no LLM). Terminal on second overflow or ≤2 groups |
| **LLM during recovery** | Yes (full compact) | Yes (if Mechanism B triggers) | Yes (compaction agent) | Yes (full compact) | **No** — static marker only |
| **Retry** | Max 3 with oldest-turn removal | N/A (proactive) | One attempt, then `ContextOverflowError` → stop | Exponential backoff up to `stream_max_retries()` | One-shot retry after emergency compact |

---

## 6. Convergence Analysis

### 6a. All Five Agree (5/5) — co-cli Implemented

| Pattern | Status |
|---------|--------|
| Auto-compaction via LLM summary | Implemented |
| Token-based trigger (not message count) | Implemented |
| No-LLM tool output pre-pass | Implemented |
| ~4 chars/token heuristic | Implemented |
| Threshold in 85–90% range | Implemented |

### 6b. Gaps Ranked by Impact on Summarizer Quality

| # | Gap | Peer evidence | co-cli status |
|---|-----|---------------|---------------|
| 1 | **Summarizer sees degraded input** — tool results cleared before LLM summarization | 4/4 peers compensate: gemini-cli feeds originals, opencode marks non-destructively, fork-cc injects rich context, codex sends full history | **Implemented** — fork-cc strategy C: `_gather_compaction_context()` injects file working set, todos, always-on memories, prior-summary text |
| 2 | **Structured summary template** | 4/5 use structured sections (gemini-cli XML, opencode Goal/Instructions/Discoveries/Accomplished/Files, codex handoff, fork-cc rich prompt) | **Implemented** — Goal, Key Decisions, Working Set, Progress, Next Steps |
| 3 | **Rich compaction prompt** (plan/tool/file context beyond bare history) | 3/5 inject side-channel context (fork-cc plan+memory+files+tools, gemini-cli plan path, opencode plugin hook) | **Implemented** — context enrichment via `_gather_compaction_context()` (4K cap) |
| 4 | **Overflow/413 recovery** | 4/5 have recovery (fork-cc reactive compact, opencode overflow→compact, codex post-sampling compact, gemini-cli proactive prevention) | **Implemented** — `emergency_compact()` + one-shot retry (no LLM, static marker) |
| 5 | **Protected/exempt tools in pruning** | 4/5 distinguish tools (fork-cc compactable set, gemini-cli exempt read_file, opencode `["skill"]`, codex preserve user over generated) | **Implemented** (commit 6191bfd — `COMPACTABLE_TOOLS` set) |
| 6 | **Two-tier per-message normalization** | 3/5 (gemini-cli 12K/2.5K, opencode 40K/20K thresholds, fork-cc micro-compact) | **Implemented** — `compact_assistant_responses` caps older TextPart/ThinkingPart at 2.5K |
| 7 | **Prior-snapshot explicit integration** | 2/5 explicit (gemini-cli integrates prior `<state_snapshot>`, codex `SUMMARY_PREFIX`) | **Implemented** — template instruction + prior-summary detection in context enrichment |

### 6c. Strategy Choice for Gap #1 — Implemented

Four peer strategies were evaluated. **Strategy C was chosen and shipped:**

| Strategy | Complexity | Quality | Peer | co-cli |
|----------|-----------|---------|------|--------|
| **A. Feed originals to summarizer** — save pre-cleared content, pass originals to `summarize_messages()` | Low | Highest | gemini-cli A | Not chosen — selective truncation (6 tools, keep 5 recent) already preserves most content |
| **B. Non-destructive marking** — mark tool results as cleared for the model but keep full content in message list | Medium | High | opencode | Not chosen — adds architectural complexity |
| **C. Rich side-channel context** — accept degraded input but inject file/todo/memory context into summarizer prompt | Medium | Medium-High | fork-cc | **Chosen** — `_gather_compaction_context()` |
| **D. Don't trim before summarizing** — run tool output clearing only after summarization | Low | High | codex (inline) | Not chosen — processor ordering has other constraints (safety scanner needs full history) |

Rationale: selective truncation already preserves tool call args (file paths, commands) and the 5 most recent results per tool type. Adding structured context enrichment addresses the quality gap without architectural changes to the processor pipeline.

### 6d. Remaining Peer-Only Patterns (2/5 or lower — not planned)

| Pattern | Who | Why not |
|---------|-----|---------|
| Feed originals to summarizer | gemini-cli A | Strategy C compensates; selective truncation preserves most content |
| Two-pass summary verification | gemini-cli | 2× LLM cost for marginal gain |
| Non-destructive marking | opencode | Strategy C achieves same goal more simply |
| Summary inflation guard | gemini-cli | No observed issue — summary is always shorter than dropped content |
| Provider-delegated compaction | codex (OpenAI only) | Not available for non-OpenAI providers |
| Two-tier per-message grace zone (12K recent / 2.5K older) | gemini-cli | co-cli uses single-tier 2.5K; grace zone adds complexity for marginal benefit in shorter contexts |
| Token-based tail sizing | codex 20K, gemini-cli 30%/40K | Message-count heuristic (`max(4, len//2)`) is sufficient |
