# RESEARCH: Peer Session Compaction — Three-Way Comparison

Sources: `~/workspace_genai/fork-claude-code` (Anthropic Claude Code), `~/workspace_genai/gemini-cli` (Google Gemini CLI), co-cli current codebase
Scan date: 2026-04-03

## Purpose

Three-way comparison of session compaction architectures to inform co-cli adoption and refactoring decisions. Each claim is verified against source code — no documentation-only claims.

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

## 3. co-cli Architecture (Current)

**3 mechanisms** built on pydantic-ai's history processor chain.

### 3a. Tool Output Trim (`truncate_tool_returns` — processor #1)

Sync processor, fires before every model API call. Truncates `ToolReturnPart.content` exceeding `tool_output_trim_chars` (default 2000) in all messages except the last 2 (current turn). No LLM call.

### 3b. Sliding Window (`truncate_history_window` — processor #4)

**Inline async** processor in `_history.py`, triggers when estimated tokens exceed 85% of `resolve_compaction_budget()` result. Budget resolved from model quirks `context_window - max_tokens` → Ollama `llm_num_ctx` → 100K fallback (in `_compaction.py`). Computes head/tail boundaries (head = first exchange including ThinkingPart, tail = max(4, len(messages)//2)), drops middle, calls `summarize_messages()` inline to generate a summary, and injects it immediately. In-memory only — JSONL unchanged. Both the boundary computation and the LLM summarisation call run inside pydantic-ai's history processor chain, blocking the API call. A `[dim]Compacting conversation...[/dim]` indicator is shown during the LLM call.

**Circuit breaker** (`compaction_failure_count` on `CoRuntimeState`): incremented on `ModelHTTPError`/`ModelAPIError`, reset to 0 on success. At >= 3 consecutive failures, the LLM call is skipped and a static marker `"[N messages trimmed]"` is used directly. When `model_registry` is absent (sub-agents, tests), the static marker is used without incrementing the counter.

### 3c. Manual `/compact`

Foreground `summarize_messages()` LLM call (from `_compaction.py`). Replaces in-memory history with 2 messages. Writes `{"type":"compact_boundary"}` to JSONL. Increments `compaction_count` in session metadata. Shows budget visibility: `Compacted: N → 2 messages (est. XK → YK of ZK budget)`. Only mechanism that affects the JSONL.

### 3d. Session Persistence

JSONL append-only (`_transcript.py`). Compact boundary markers for resume skip (>5MB threshold). Line-by-line scan, `messages.clear()` on boundary.

---

## 4. Three-Way Comparison

### 4a. Compaction Triggers and Thresholds

| Aspect | fork-cc | gemini-cli | co-cli |
|--------|---------|-----------|--------|
| Auto trigger | `contextWindow - output - 13K` (~85% effective) | 50% of token limit (~524K of 1M) | 85% of `resolve_compaction_budget()` result |
| Manual trigger | `/compact` command | None — auto only | `/compact` command (shows budget visibility) |
| Blocking limit | `effectiveContext - 3K` → forced `/compact` | None | None |
| Token limit | Model-dependent (typically 200K) | 1,048,576 (all Gemini models) | `resolve_compaction_budget()`: model quirks `context_window - max_tokens` → Ollama `llm_num_ctx` override → 100K fallback |

#### 4a-detail. Auto-Trigger Deep Dive (Source-Level)

| Aspect | fork-cc | gemini-cli (A: ChatCompressionService / B: AgentHistoryProvider) | co-cli |
|--------|---------|----------------------------------------------------------------|--------|
| **Fires when** | - Between turns in query loop (`query.ts:453–468`)<br>- After three pre-passes (snip → micro-compact → context-collapse)<br>- Before model call | - **A** (`contextManagement.enabled=false`): start of every turn in `client.ts` → `processTurn()`, synchronous blocking<br>- **B** (`enabled=true`): before every `sendMessageStream()` call<br>- Mutually exclusive — config toggle selects one | - Last in history processor chain (`_history.py`)<br>- After `truncate_tool_returns` → `detect_safety_issues` → `inject_opening_context`<br>- Before every model API call |
| **Trigger formula** | - `tokenCount >= contextWindow − min(maxOutputTokens, 20K) − 13K`<br>- Env overrides: `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | - **A**: `originalTokenCount >= threshold × tokenLimit` where `threshold = 0.5`<br>- **B**: Three-phase pipeline: Phase 1 (per-msg normalization) → Phase 2 trigger if `totalTokens > maxTokens` → Phase 3 split+summarize | - `token_count > int(resolve_compaction_budget(config, registry) × 0.85)`<br>- Budget via 3-tier: model quirks `context_window - max_tokens` → Ollama `llm_num_ctx` → 100K fallback<br>- Token-only trigger (message count removed — 0/2 peer convergence) |
| **Typical threshold** | - ~167K for 200K model: `200K − 20K − 13K`<br>- Warning tier at 147K (`threshold − 20K`)<br>- Blocking limit at 177K (`effectiveWindow − 3K` → forced `/compact`) | - **A**: ~524K (`0.5 × 1,048,576` for all Gemini 1M models)<br>- **B**: 150K high watermark, 40K retained tail budget | - Gemini: `int((1,048,576 − 65,536) × 0.85)` = 835K<br>- Ollama: `int(llm_num_ctx × 0.85)` (overrides spec)<br>- No registry: `int(100K × 0.85)` = 85K fallback |
| **Token counting** | - **Hybrid exact + estimation** (`utils/tokens.ts`)<br>- (1) Walk backwards for last `ModelResponse` with usage → extract `input_tokens + cache_creation + cache_read + output_tokens`<br>- (2) Estimate new messages: 4 bytes/tok default, JSON 2 bytes/tok, images 2K flat<br>- (3) No usage data → estimate entire array<br>- (4) Subtract `snipTokensFreed` from pre-pass | - **A+B shared** (`utils/tokenCalculation.ts`): `estimateTokenCountSync()` heuristic<br>- ASCII 0.25 tok/char, non-ASCII/CJK 1.3 tok/char, images 3K flat, PDFs 25.8K flat<br>- Text >100K chars: fast-path `length/4`<br>- **A additionally**: `countTokens()` API for media, falls back to heuristic | - **Provider-reported preferred** (`_compaction.py`)<br>- (1) `latest_response_input_tokens()`: scan reverse for last `ModelResponse` with `usage.input_tokens > 0`<br>- (2) Fallback when 0: `estimate_message_tokens()` — `total_chars // 4`<br>- No media-specific handling |
| **Pre-processing before check** | - Three-stage pipeline before threshold check:<br>- (1) **Snip** — drop oldest below tail budget, track `snipTokensFreed`<br>- (2) **Micro-compact** — trim tool results, replace unchanged file-reads with `FILE_UNCHANGED_STUB`<br>- (3) **Context collapse** (if enabled) — collapses at 90% full | - **A**: `truncateHistoryToBudget()` before threshold check<br>- Iterates backwards, accumulates function response tokens<br>- Truncates older responses to last 30 lines when 50K budget exceeded<br>- Saves raw output to temp file<br>- **B** Phase 1 (always-on): `enforceMessageSizeLimits()`<br>- Grace zone (40K from tail): 12K/msg cap<br>- Older msgs: 2.5K/msg cap<br>- Proportional truncation 25% head + 75% tail | - `truncate_tool_returns` (processor #1): trims `ToolReturnPart.content` > 2000 chars in all messages except last 2<br>- No LLM call<br>- No per-message normalization tiers<br>- No pre-pass token subtraction from threshold |
| **Split strategy** | - Summarize **ALL** messages via forked agent (out-of-band Claude API call)<br>- (1) Try session memory compaction first — pruning within configurable keep window<br>- (2) Fall back to `compactConversation()` — entire history to summarizer<br>- PTL retry loop (max 3 retries) drops oldest turns on `prompt-too-long` | - **A**: 70/30 char-based split (`findCompressSplitPoint()`)<br>- Split at first user message after ≥ 70% of total chars<br>- Compress first 70% via LLM (2 passes: `<state_snapshot>` + verification probe)<br>- Keep last 30% verbatim<br>- Reject if new tokens > original<br>- **B**: Backward token scan → split at 40K retained tail<br>- Single LLM pass → `<intent_summary>` (max 15 lines)<br>- Merge summary into first retained user message | - Head/tail boundary, drop middle (`_compute_compaction_boundaries()`)<br>- `head_end` = first `ModelResponse` with `TextPart`/`ThinkingPart` + 1<br>- `tail_count = max(4, max_history_messages // 2)`<br>- `_align_tail_start()` walks past orphaned `ToolReturnPart`<br>- Invalid boundary → skip compaction<br>- Dropped messages → LLM summary (`summarize_messages()`) or static marker |
| **Circuit breaker** | - `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`<br>- State: `tracking.consecutiveFailures` (persists across turns)<br>- On success: reset to 0<br>- On failure: increment; at ≥ 3 skip LLM<br>- Motivated by BQ: 1,279 sessions had 50+ consecutive failures | - **A**: Single boolean `hasFailedCompressionAttempt`<br>- Set `true` on empty summary or inflated token count<br>- On retry: skip LLM, truncation-only<br>- One failure disables LLM for session<br>- **B**: No circuit breaker; static note on failure | - `CoRuntimeState.compaction_failure_count`<br>- Cross-turn persistent (NOT reset by `reset_for_turn()`)<br>- On `ModelHTTPError`/`ModelAPIError`: increment<br>- On success: reset to 0<br>- At ≥ 3: skip LLM, static marker `"[N messages trimmed]"`<br>- `model_registry is None`: static marker without counting |
| **Recursion guards** | - 5 query-source guards in `shouldAutoCompact()`<br>- Return `false` for `{session_memory, compact, marble_origami, reactive_compact, collapse}`<br>- Also suppressed by feature gates: `REACTIVE_COMPACT`, `CONTEXT_COLLAPSE` | - N/A — no forked agents, no recursion risk<br>- Mechanism selection is config-driven (`contextManagement.enabled`) | - N/A — no forked agents<br>- `model_registry is None` handles sub-agent/test paths (not a recursion guard) |
| **Env/config overrides** | - `DISABLE_AUTO_COMPACT`, `DISABLE_COMPACT` (kill switches)<br>- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (override effective window)<br>- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (threshold as %)<br>- `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE`<br>- `CLAUDE_CODE_MAX_CONTEXT_TOKENS` (cap)<br>- `CLAUDE_CODE_DISABLE_1M_CONTEXT` (HIPAA) | - Config-driven only: `config.getCompressionThreshold()` overrides 0.5 default (A)<br>- `contextManagement.*` hierarchy controls all B thresholds<br>- No env var escape hatches | - `resolve_compaction_budget()` 3-tier:<br>- (1) Model quirks `context_window` (spec)<br>- (2) Ollama `llm_num_ctx` (Modelfile override)<br>- (3) 100K fallback<br>- `LLM_PROVIDER` selects Ollama override path<br>- Settings JSON precedence: env > project > user > built-in |
| **Warning/blocking tiers** | - 4 tiers via `calculateTokenWarningState()`:<br>- (1) Warning at `threshold − 20K` (147K)<br>- (2) Error at same<br>- (3) Auto-compact at `threshold` (167K)<br>- (4) Blocking at `effectiveWindow − 3K` (177K) → forced `/compact` | - None — no graduated warnings<br>- A triggers at 50%, B at 150K high watermark<br>- No blocking limit | - Single threshold at 85%<br>- No warning tiers<br>- No blocking limit |

#### 4a-assessment. Comparison and Adoption Assessment

##### Trigger Timing: Where in the Turn Cycle

All three fire **before the model call**, but at different granularities. fork-cc runs after a three-stage pre-processing pipeline (snip → micro-compact → collapse) that shrinks context before the threshold check — meaning the trigger sees a smaller, already-optimized message set. gemini-cli's Mechanism B runs per-message normalization as Phase 1 (always-on, regardless of whether compaction triggers), ensuring every model call gets size-limited messages. co-cli runs `truncate_tool_returns` first but does not subtract freed tokens from the threshold check — the trigger sees the raw post-trim message set.

**Gap for co-cli**: The lack of freed-token subtraction means the threshold can over-trigger when tool trimming has already reduced effective context. Low priority — the 85% threshold provides sufficient margin, and over-triggering is safer than under-triggering.

##### Threshold Calibration

The three systems reflect fundamentally different context window economics:

- **fork-cc** (200K window): threshold at ~83% effective (`167K / 200K`), tight — compacts aggressively to preserve response quality in a constrained window. Multi-tier warnings (147K → 167K → 177K blocking) give progressive feedback.
- **gemini-cli** (1M window): threshold at 50% (`524K / 1M`), conservative — can afford to compress early because the window is enormous. The newer Mechanism B uses a much tighter 150K absolute ceiling, suggesting Google found 524K too late for quality.
- **co-cli** (model-spec-aware): Gemini threshold at 835K (`(1M − 65K) × 0.85`), now proportionate to actual window. Ollama threshold at `llm_num_ctx × 0.85` (user's Modelfile is truth). Fallback 85K for unknown models (`100K × 0.85`).

**Assessment**: co-cli's message count trigger (`max_history_messages = 40`) is over-design — 0/2 peer convergence. Neither fork-cc nor gemini-cli uses message count; both rely purely on token thresholds. The theoretical scenario (40+ short messages staying under 85K tokens) is impractical: each turn includes system prompt tokens in the provider-reported count, so even short exchanges accumulate quickly. The message count adds a second code path, a config surface (`CO_CLI_MAX_HISTORY_MESSAGES`), and couples the tail-size computation to `max_history_messages // 2`. **Simplify to token-only trigger.** See `TODO-compaction-trigger-simplify.md`.

##### Token Counting Accuracy

| Strategy | Accuracy | Latency | When wrong |
|----------|----------|---------|------------|
| fork-cc hybrid (exact base + estimate delta) | **High** — exact for bulk, estimate only for new messages since last response | Near-zero — reads cached usage, estimates only the delta | Under-counts when many new tool results arrive between API calls (estimated portion grows) |
| gemini-cli heuristic (char-based with script awareness) | **Medium** — CJK-aware ratios are more accurate for multilingual content, but no exact baseline | Zero — pure arithmetic | Over-counts for dense JSON/code (no JSON-specific ratio like fork-cc's 2 bytes/token) |
| co-cli provider-reported fallback | **High when available** — uses provider's own count. **Low when falling back** — uniform `chars//4` ignores JSON density, CJK, media | Zero | Falls back to estimate for: (1) local models with no usage reporting (Ollama common case), (2) first turn before any API response exists. Uniform `chars//4` over-counts JSON, under-counts CJK |

**Gap for co-cli**: The fallback estimator (`chars // 4`) is the weakest of the three — no JSON-specific ratio, no CJK awareness, no media handling. This matters most for Ollama (where provider-reported tokens are often unavailable) and for multilingual sessions. However, the impact is limited because: (1) the threshold has sufficient margin (85%), (2) over-estimation triggers compaction earlier (safe direction), (3) media content is rare in CLI sessions.

**Adoption suggestion** (Priority 3 — low): Add JSON-density ratio (`chars // 2` for dict content) to `estimate_message_tokens()` in `_compaction.py`. Minimal effort, directly matches fork-cc's approach. CJK awareness is higher effort for marginal benefit in current user base.

##### Pre-Processing Pipeline Depth

fork-cc's three-stage pipeline (snip + micro-compact + context-collapse) is the most sophisticated — each stage reduces context before the threshold check, so compaction fires less often and operates on a smaller input. gemini-cli's Mechanism B achieves similar efficiency through always-on per-message normalization (Phase 1) that caps message sizes proactively. co-cli's `truncate_tool_returns` is the simplest — binary trim on a char threshold, no per-message budgeting, no freed-token subtraction.

**Gap for co-cli**: The single-threshold binary trim (`tool_output_trim_chars = 2000` for all-but-last-2 messages) is coarser than both peers. This is already identified in §4c and §5b/Priority 2 recommendation #3 (two-tier per-message normalization). No new recommendation needed here — the existing Priority 2 item covers it.

##### Split Strategy and What Gets Summarized

The three approaches represent fundamentally different trade-offs:

- **fork-cc** sends ALL messages to the summarizer (or prunes to a keep-window in session memory mode). The summarizer sees complete history, producing the most context-aware summary. Cost: largest LLM input per compaction.
- **gemini-cli Mechanism A** splits 70/30 by characters — only the first 70% is summarized, last 30% kept verbatim. Cost-efficient, preserves recent context perfectly. Weakness: char-based split can misalign with semantic boundaries.
- **gemini-cli Mechanism B** keeps a fixed 40K token tail, summarizes everything before it. Token-based split is more semantically meaningful than char-based.
- **co-cli** drops the middle, keeps head (first model output) + tail. Summarizes only the dropped middle. The head-pinning preserves the initial context/personality establishment. Currently `tail_count = max(4, max_history_messages // 2)` — after message-count removal, derive tail_count from token budget (e.g. fixed fraction of context budget) instead.

**Assessment**: co-cli's head-pinning is unique and valuable — it preserves the initial exchange that establishes personality and task context. The split strategy is sound. Tail-count derivation needs updating after message-count removal (see `TODO-compaction-trigger-simplify.md`).

##### Circuit Breaker Design

All three implement failure protection, but with different sophistication:

- **fork-cc**: Counting (3 failures), cross-turn persistent. Production-proven — the BQ finding (250K wasted API calls/day) validates the need.
- **gemini-cli A**: Single boolean, session-scoped. Simpler but effective — one failure is enough to stop wasting calls for the session.
- **co-cli**: Counting (3 failures), cross-turn persistent, with `model_registry is None` escape hatch for sub-agents.

**Assessment**: co-cli's circuit breaker already matches fork-cc's design (the strongest). The `model_registry is None` path is a clean addition that neither peer has. No change needed.

##### Summary of Adoption Priorities (Trigger-Specific)

| # | Item | Priority | Rationale |
|---|------|----------|-----------|
| 1 | **Remove message count trigger** — simplify to token-only | **P1** | Over-design: 0/2 peer convergence. Adds second code path + config surface for a theoretical scenario. Also decouples tail-count from `max_history_messages`. See `TODO-compaction-trigger-simplify.md` |
| — | Head-pinning in split | **Keep** | Unique to co-cli — preserves personality/context establishment |
| — | Circuit breaker (3 failures + registry-absent path) | **Keep** | Already matches or exceeds both peers |
| 2 | JSON-density ratio in fallback estimator | **P3** | Low effort. Add `chars // 2` for dict content in `_estimate_message_tokens()`. Matches fork-cc. Benefits Ollama users most |
| 3 | Subtract freed tokens from threshold check | **P3** | Low effort. After `truncate_tool_returns`, subtract the delta from `token_count` before threshold comparison. Reduces over-triggering |
| 4 | Two-tier per-message normalization | **P2** | Already in §5b recommendation #3. Grace zone (recent msgs: higher limit) + older msgs (lower limit). Both peers converge on this |
| 5 | Warning tiers before compaction | **P3** | fork-cc has 4 tiers; co-cli has none. Low effort to add a `[dim]Context: N% used[/dim]` indicator. Nice UX but not load-bearing |

### 4b. Compression Method

| Aspect | fork-cc | gemini-cli | co-cli |
|--------|---------|-----------|--------|
| Summary engine | Out-of-band Claude API call (forked agent) | In-process LLM call (same client) | Inline `summarize_messages()` call inside history processor (blocks the model request) |
| Summary passes | 1 (single generation) | 2 (generate + verification probe) | 1 (single generation) |
| Summary format | Free-form text | Structured XML (`<state_snapshot>`) | Free-form text |
| Multi-cycle survival | Implicit (prior summary in post-boundary messages) | Explicit (new snapshot integrates prior `<state_snapshot>`) | Implicit (prior summary in messages that get re-summarized) |
| Prompt context | Rich: plan + memory + file state + tools + MCP + agents | History + compression system prompt + plan path | Bare: conversation history only |
| Split strategy | Entire history → summarizer | 70/30 char-based split, keep last 30% | Head/tail boundary, drop middle |
| Fallback on failure | Circuit breaker (3 failures → stop) | Circuit breaker (skip LLM, truncate-only) | Circuit breaker (3 failures → static marker; `model_registry` absent → static marker without counting) |

### 4c. Tool Output Management

| Aspect | fork-cc | gemini-cli | co-cli |
|--------|---------|-----------|--------|
| When it fires | Before full compact (micro-compact pre-pass) | At tool return time (per-call) AND before compression (budget pre-pass) | Before every model API call (always-on processor) |
| LLM-assisted | No | Yes — intent summary for large outputs (15s timeout) | No |
| File-read optimization | `FILE_UNCHANGED_STUB` for unchanged reads | Exempt `read_file`/`read_many_files` from distillation | None |
| Per-message normalization | No | Two-tier: grace zone 12K/msg, older 2.5K/msg, proportional | Binary: trim all-but-last-2 at char threshold |
| Disk save of full output | No | Yes — raw output saved to temp file before truncation | No |

### 4d. Persistence and Resume

| Aspect | fork-cc | gemini-cli | co-cli |
|--------|---------|-----------|--------|
| Format | JSONL append-only | Single JSON, full rewrite per update | JSONL append-only |
| Boundary markers | Rich `SystemCompactBoundaryMessage` with `compactMetadata` + `preservedSegment` | None — compression is in-memory only | Minimal `{"type":"compact_boundary"}` |
| Resume optimization | Stream 65KB chunks, skip to last boundary (>5MB) | Load full JSON | Line-by-line scan, skip to last boundary (>5MB) |
| Preserved segment | Explicit UUID pointers in boundary metadata | N/A | Head pinning (structural guarantee) |

### 4e. Safety Mechanisms

| Aspect | fork-cc | gemini-cli | co-cli |
|--------|---------|-----------|--------|
| Circuit breaker | 3 consecutive failures → stop | `hasFailedCompressionAttempt` → truncate-only | 3 consecutive failures → static marker (`compaction_failure_count` on `CoRuntimeState`) |
| Recursion guard | Won't fire inside forked agents | N/A (no forked agents) | N/A (no forked agents) |
| 413 recovery | Reactive compact (feature-gated) | `AgentHistoryProvider` prevents overflow proactively | None (relies on 85% pre-emptive threshold + inline compaction) |
| Summary inflation guard | N/A | Reject if new tokens > original | N/A |
| Adversarial content in summary | N/A | Security rule in compression prompt | Security rule in summarizer system prompt |

---

## 5. Convergence Analysis

Patterns where **2+ systems** agree — strongest signal for adoption.

### 5a. All Three Agree (3/3)

| Pattern | fork-cc | gemini-cli | co-cli | Status |
|---------|---------|-----------|--------|--------|
| Auto-compaction (LLM summary replaces old history) | ✓ | ✓ | ✓ | Implemented |
| Tool output trimming (no-LLM pre-pass on large outputs) | ✓ micro-compact | ✓ distillation service | ✓ `truncate_tool_returns` | Implemented |
| Adversarial content security rule in summarizer prompt | ✓ | ✓ | ✓ | Implemented |
| Circuit breaker for compression failures | ✓ (3 failures → stop) | ✓ (`hasFailedCompressionAttempt` → truncate-only) | ✓ (3 failures → static marker) | Implemented |

### 5b. Two of Three Agree (2/3)

| Pattern | Who has it | Who lacks it | Evidence weight |
|---------|-----------|-------------|----------------|
| **Rich compact prompt** (plan, tools, file state) | fork-cc (forked agent with full context), gemini-cli (plan path + step status in compression prompt) | **co-cli** (bare summarizer) | **High** — both peers include plan/tool context. Directly impacts summary quality. |
| **Structured summary format** | gemini-cli (`<state_snapshot>` XML), fork-cc (free-form but with rich structured prompt) | **co-cli** (free-form text) | **Medium** — gemini-cli is explicit; fork-cc achieves structure through prompt context rather than output format. |
| **Per-message size normalization** (granular, not binary) | gemini-cli (two-tier: 12K/2.5K), fork-cc (micro-compact: `FILE_UNCHANGED_STUB`) | **co-cli** (binary: trim all-but-last-2) | **Medium** — approaches differ but both are more granular than co-cli's single threshold. |
| **JSONL append-only persistence** | fork-cc, co-cli | gemini-cli (JSON rewrite) | Already implemented |
| **Boundary skip on resume** (>5MB threshold) | fork-cc, co-cli | gemini-cli (no boundaries) | Already implemented |
| **Manual `/compact` command** | fork-cc, co-cli | gemini-cli (auto-only) | Already implemented |
| **Disk save of truncated tool output** | gemini-cli (temp file), fork-cc (micro-compact saves to file in `truncateHistoryToBudget`) | **co-cli** | **Low** — useful for debugging but adds disk I/O complexity. |

### 5c. Only One System Has (1/3)

| Pattern | Who | Adopt? |
|---------|-----|--------|
| Two-pass verification (self-correction probe) | gemini-cli | No — cost/latency too high for small-context frequent compression |
| Session memory compact (fact extraction pre-pass) | fork-cc | No — experimental, GrowthBook-gated, co-cli knowledge system serves different purpose |
| Reactive compact (413 recovery) | fork-cc (feature-gated) | Defer — Ollama silently truncates (no trigger signal); revisit when Gemini provider matures |
| LLM-assisted tool output distillation | gemini-cli | Defer — adds LLM call per large tool output; justified for 1M context, expensive for small context |
| ~~Summary pre-computation (background idle-time LLM call)~~ | ~~co-cli~~ | **Removed** — replaced by inline synchronous summarisation. The pre-computation pattern added staleness complexity (boundary mismatch between turns) and silent quality degradation (static marker fallback was the common case during tool-heavy turns). Inline summarisation eliminates both problems at the cost of one blocking LLM call per compaction event. |
| `FILE_UNCHANGED_STUB` for repeat reads | fork-cc | **Consider** — simple optimization, no LLM cost, reduces redundant file content in history |

---

## 6. Adoption Recommendations for co-cli

Ranked by evidence weight (peer convergence × impact).

### Priority 1 — Both Peers Converge, Clear Gap

| # | Recommendation | Evidence | Effort | Risk |
|---|---------------|----------|--------|------|
| 1 | ~~**Add circuit breaker to `HistoryCompactionState`**~~ | ~~fork-cc: 3 failures → stop. gemini-cli: `hasFailedCompressionAttempt` → truncate-only.~~ | **DONE** — `compaction_failure_count` on `CoRuntimeState`, threshold 3, inline in `truncate_history_window`. Pre-computation machinery (`HistoryCompactionState`, `precompute_compaction`, `Compaction` dataclass) removed entirely. | — |
| 2 | **Enrich compaction prompt with plan/tool context** | fork-cc: forked agent receives plan + memory + file state + tools + MCP. gemini-cli: compression prompt includes plan path + step status. co-cli: bare summarizer with history only. | Medium — gather active plan, registered tools, recent file paths into summarization prompt | None — additive change, fallback to bare summary if context unavailable |

### Priority 2 — Strong Signal, Moderate Effort

| # | Recommendation | Evidence | Effort | Risk |
|---|---------------|----------|--------|------|
| 3 | **Two-tier per-message normalization** (grace zone + older) | gemini-cli: 12K recent / 2.5K older per message. fork-cc: micro-compact `FILE_UNCHANGED_STUB`. co-cli: binary trim all-but-last-2. | Medium — extend `truncate_tool_returns` with per-message token budgets and grace zone logic | Low — more granular control, degrades gracefully |
| 4 | **Structured summary format for multi-cycle compaction** | gemini-cli: `<state_snapshot>` XML with explicit prior-snapshot integration. fork-cc: rich structured prompt. co-cli: free-form text. | Medium — define XML/markdown template for summaries, add "integrate prior summary" instruction | Low — improves quality over compression cycles |

### Priority 3 — Monitor / Defer

| # | Recommendation | Trigger |
|---|---------------|---------|
| 5 | `FILE_UNCHANGED_STUB` for repeat file reads | When users report context bloat from repeated reads of same file |
| 6 | Reactive compact (413 recovery) | When Gemini provider matures and returns 413 errors |
| 7 | Disk save of full truncated tool output | When users report needing to review truncated content post-session |
| 8 | LLM-assisted tool output distillation | When tool output quality issues surface in long sessions with large outputs |
