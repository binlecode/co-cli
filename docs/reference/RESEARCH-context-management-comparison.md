# Research Report: Context Compaction Design — Hermes vs. Openclaw

Source-grounded comparison across two repos (updated to latest HEAD: 2026-05-23). All numeric constants and prompt text are quoted directly from source.

> **Opencode omitted.** Opencode's defining contribution (disk-spill of oversized tool output with a content-addressed placeholder) is already implemented in co as L1 spill (`co_cli/tools/tool_io.py`: `spill_if_oversized` → `tool-results/<sha16>.txt`). The remaining opencode-specific patterns (reserved-buffer trigger, `splitTurn`, Task-tool delegation prompts, plugin hooks) are either alternative trigger models co has consciously not adopted or JS-ecosystem details that don't map. See `docs/specs/compaction.md` §2.2.

> **Openclaw architecture note:** Openclaw is a multi-channel LLM agent gateway built on `@earendil-works/pi-coding-agent` v0.75.1 (PI). Supports 9+ provider plugins (Anthropic, OpenAI, Bedrock, Ollama, DeepSeek, Groq, Cerebras). The PI layer owns the core agentic loop; openclaw wraps it with compaction orchestration, multi-provider failover, channel routing, and plugin lifecycle.

---

## 1. Compaction Trigger

### Hermes — `agent/context_compressor.py`

- **Condition:** `should_compress(prompt_tokens)` fires when `prompt_tokens >= threshold_tokens`.
- **Threshold:** `max(int(context_length * 0.50), MINIMUM_CONTEXT_LENGTH)` — default **50% of model context window** (line 411).
- **Anti-thrashing:** If the last 2+ compressions each saved <10% of tokens, further compression is skipped with a warning (lines 476–484).
- **Timing:** Post-response only — checks token count after the LLM reply arrives.

### Openclaw — `src/agents/compaction.ts`, `src/agents/pi-compaction-constants.ts`

- **Condition:** Invoked explicitly by the runner when PI signals context overflow — no internal threshold constant.
- **Adaptive chunking ratios:** `BASE_CHUNK_RATIO = 0.4` (compress 40% of messages by default), `MIN_CHUNK_RATIO = 0.15` (floor). When average message size exceeds 10% of the context window, the ratio is reduced dynamically: `max(MIN_CHUNK_RATIO, BASE_CHUNK_RATIO − min(avgRatio * 2, 0.25))` (lines 284–303).
- **Safety margin:** `SAFETY_MARGIN = 1.2` — all token estimates are divided by 1.2 to compensate for estimation inaccuracy (line 22).
- **Mid-turn precheck:** Optional `cfg.agents.defaults.compaction.midTurnPrecheck.enabled` — can trigger compaction before a turn completes if context is already near limit (`attempt.ts:2453`).

---

## 2. History Window / Message Selection

### Hermes — `agent/context_compressor.py`

- **Protected head:** First `protect_first_n = 3` messages (system prompt + first user/assistant exchange), always retained (line 381).
- **Protected tail — token budget:** `tail_token_budget = int(threshold_tokens * 0.20)` — **20% of the trigger threshold** (lines 418–419). The tail accumulates backward from the end until the budget is spent, with a soft 1.5× overshoot allowed to avoid mid-message splits (line 1177). Hard minimum: 3 messages always kept (line 1176).
- **Last-user-message safeguard:** If the most recent user message falls inside the compressible region, the compaction boundary is pulled back to protect it (lines 1105–1150, fix for bug #10896).
- **Tool-pair integrity:** Two boundary-alignment helpers (lines 1058–1090):
  - `_align_boundary_forward()` — slides the compaction start past any orphaned tool results at the boundary.
  - `_align_boundary_backward()` — keeps an entire tool_call/tool_result group together when the boundary would split them.
- **Orphan repair after compaction:** `_sanitize_tool_pairs()` removes orphaned `tool_result` messages whose `tool_call` was summarized, and inserts stub results for orphaned `tool_call` messages: `"[Result from earlier conversation — see context summary above]"` (lines 1047–1051).

### Openclaw — `src/agents/compaction.ts`

- **Token-share chunking:** Divides message list into `DEFAULT_PARTS = 2` chunks by token budget, not message count (lines 137–231).
- **Tool-pair integrity during chunking:** Tracks outstanding tool calls per assistant message (lines 155–218). A chunk boundary is not placed when there are unresolved tool calls (`pendingChunkStartIndex`). The boundary only advances when the tool result arrives.
- **Oversized message detection:** Messages whose token count × SAFETY_MARGIN exceeds 50% of the context window are flagged non-summarizable (lines 309–312).
- **Context-share pruning:** When building handoff context, history is limited to `maxHistoryShare` (default 50%, or 20% for handoff) of context tokens. Oldest chunks are dropped until within budget; tool-use/result pairing is repaired after each drop (lines 532–597).

---

## 3. Tool Output Truncation — Live Path (During a Running Turn)

### Hermes
No explicit live truncation of tool output in `context_compressor.py`. Tool outputs are preserved in full during live turns; they are only pruned when compaction fires.

### Openclaw — `src/agents/pi-embedded-subscribe.tools.ts`

- **Live delivery cap:** `TOOL_RESULT_MAX_CHARS = 8,000` chars (line 17). Head-only: keeps first 8K, appends `…(truncated)…`.
- **Error text cap:** `TOOL_ERROR_MAX_CHARS = 400` chars (line 18). First line only.
- **Encoding:** `truncateUtf16Safe()` — truncates at UTF-16 code unit boundaries to prevent broken surrogate pairs, important for non-ASCII output (from `utils.ts`).

---

## 4. Tool Output Truncation — Compaction Path (During History Summarization)

### Hermes — `agent/context_compressor.py`

Three-pass pre-compression pruning before the summarizer LLM is called (lines 491–628):

**Pass 1 — Deduplication** (lines 560–580):
- MD5 hash of each tool result's content.
- Older copies of duplicate content replaced with: `"[Duplicate tool output — same content as a more recent call]"`.

**Pass 2 — Content stub** (lines 582–602):
- Applied to tool results older than the tail boundary whose content length > 200 chars.
- Replaced with a 1-line summary stub: e.g., `"[terminal] ran \`npm test\` -> exit 0, 47 lines output"`.
- No suffix — stub replaces the content entirely.

**Pass 3 — Argument truncation** (lines 604–628):
- Applied to `tool_call` argument JSONs longer than 500 chars.
- Parses JSON, trims each string leaf to `head_chars = 200`, appends `"...[truncated]"`.

**Summarizer input limits** (lines 656–709) — limits on what the compressor LLM sees per message:
| Constant | Value |
|---|---|
| `_CONTENT_MAX` | 6,000 chars total |
| `_CONTENT_HEAD` | 4,000 chars kept from start |
| `_CONTENT_TAIL` | 1,500 chars kept from end |
| `_TOOL_ARGS_MAX` | 1,500 chars for tool call arguments |
| Splice suffix | `"\n...[truncated]...\n"` between head and tail |
| Image estimate | `_IMAGE_TOKEN_ESTIMATE = 1,600` tokens; `_IMAGE_CHAR_EQUIVALENT = 6,400` chars |

### Openclaw — `src/agents/pi-embedded-runner/tool-result-truncation.ts`

- **Live context ceiling:** `DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS = 16,000` chars (line 40).
- **Context-share cap:** `MAX_TOOL_RESULT_CONTEXT_SHARE = 0.30` — one tool result cannot exceed 30% of the context window even if under the char cap.
- **Minimum keep:** `MIN_KEEP_CHARS = 2,000` chars always preserved.
- **Suffix:** `formatContextLimitTruncationNotice(truncatedChars)` — tells the model exactly how many chars were removed.
- **Security strip before compaction** (`compaction.ts:330`): `stripToolResultDetails()` removes `toolResult.details` before any message is passed to the summarizer — details are never exposed to an external model.
- **Oversized message fallback:** Messages exceeding 50% of context window are replaced with: `"[Large ${role} (~${K}K tokens) omitted from summary]"` (lines 432–442).

---

## 5. Compaction Model and Prompt

### Hermes — `agent/context_compressor.py`

**Model selection:**
- Primary: dedicated `summary_model` (aux model, e.g., `google/gemini-3-flash-preview` via OpenRouter).
- Fallback: if aux model returns 404/503/"model_not_found", retries immediately on main model (`_summary_model_fallen_back = True` prevents a second retry).
- Configurable per call via `summary_model_override` parameter.

**System preamble** (lines 743–756):
```
You are a summarization agent creating a context checkpoint.
Your output will be injected as reference material for a DIFFERENT
assistant that continues the conversation.
Do NOT respond to any questions or requests in the conversation —
only output the structured summary.
Do NOT include any preamble, greeting, or prefix.
Write the summary in the same language the user was using in the
conversation — do not translate or switch to English.
NEVER include API keys, tokens, passwords, secrets, credentials,
or connection strings in the summary — replace any that appear with [REDACTED].
```

**Template — 12 sections** (lines 759–816):
`## Active Task` (MOST CRITICAL — copy exact unfulfilled request), `## Goal`, `## Constraints & Preferences`, `## Completed Actions`, `## Active State`, `## In Progress`, `## Blocked`, `## Key Decisions`, `## Resolved Questions`, `## Pending User Asks`, `## Relevant Files`, `## Remaining Work`, `## Critical Context`.

**Token budget for summary output:**
`max(_MIN_SUMMARY_TOKENS, min(summary_budget * _SUMMARY_RATIO, _SUMMARY_TOKENS_CEILING))`
| Constant | Value |
|---|---|
| `_MIN_SUMMARY_TOKENS` | 2,000 tokens |
| `_SUMMARY_RATIO` | 0.20 (20% of compressed content) |
| `_SUMMARY_TOKENS_CEILING` | 12,000 tokens |
| Overhead multiplier | `max_tokens = summary_budget * 1.3` |

**Iterative update** (lines 818–832): if a prior summary exists, the new prompt instructs: `"PRESERVE all existing information that is still relevant. ADD new completed actions to the numbered list (continue numbering)."` Active Task field is always refreshed.

### Openclaw — `src/agents/compaction.ts`

**Model:** main session model (no dedicated aux model).

**Custom instruction sets** (lines 25–57):
- `MERGE_SUMMARIES_INSTRUCTIONS`: used when merging partial-chunk summaries. Mandates preserving: active tasks + status, batch progress counters (e.g., "5/17 items completed"), last user request, decisions + rationale, TODOs, open questions, constraints, commitments.
- `IDENTIFIER_PRESERVATION_INSTRUCTIONS`: `"Preserve all opaque identifiers exactly as written (no shortening or reconstruction), including UUIDs, hashes, IDs, hostnames, IPs, ports, URLs, and file names."` Configurable as `"strict"` (default), `"off"`, or `"custom"`.
- `HANDOFF_INSTRUCTIONS` (model-quota handoffs): reinforces leader/subordinate hierarchy: `"Explicitly state that the new model is the LEADER (Orchestrator). Identify any active autonomous units (like AutoClaw) as SUBORDINATES."` Handoff summary hard-capped at **4,000 tokens** (line 619).

---

## 6. Post-Compaction Mechanics

### Hermes — `agent/context_compressor.py`

**Summary reinsertion** (lines 1320–1387):
- Role selection: picks the role (user/assistant) that does not collide with the last head message or first tail message to maintain alternation.
- If standalone insertion would break alternation either way, the summary is prepended to the first tail message with a separator:
  ```
  {summary}

  --- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---

  {first tail message}
  ```
- System message updated once per session: appends `"[Note: Some earlier conversation turns have been compacted into a handoff summary to preserve context space. The current session state may still reflect earlier work, so build on that summary and state rather than re-doing work.]"` (line 1325), guarded by substring presence check.

**Resumption:** Last user turn is preserved in the protected tail; conversation continues from the next LLM response with no synthetic continuation.

**No transcript file rotation.**

### Openclaw — `src/agents/pi-embedded-runner/compact.queued.ts`, `compaction-hooks.ts`, `compaction-successor-transcript.ts`

**Lane-queued execution** (lines 52+): compaction enqueued on both session lane and global lane — prevents concurrent compaction attempts on the same session.

**Grace period** (`attempt.ts:3311`): if compaction fires mid-run, the run deadline is extended by `compactionTimeoutMs` rather than cancelling. The run resumes after compaction completes. Grace used only once per run (`compactionGraceUsed = true`).

**Staged multi-phase summarization** (`compaction.ts:466–530`):
1. Split messages into chunks by token budget.
2. Summarize each chunk independently.
3. Merge partial summaries via `MERGE_SUMMARIES_INSTRUCTIONS`.

**Post-compaction hooks** (`compaction-hooks.ts`): `runPostCompactionSideEffects()` — plugins can act after compaction.

**Transcript rotation** (`compaction-successor-transcript.ts`): `shouldRotateCompactionTranscript()` / `rotateTranscriptFileAfterCompaction()` — rotates the transcript file post-compaction when criteria are met, keeping the active file bounded.

**Handoff snapshot** (`compaction.ts:602–627`): separate path for model-quota transitions — 4K token cap, leader/subordinate framing, 4K overhead reserve.

---

## 7. Retry and Overflow Recovery

### Hermes

**Summary failure cooldown** (lines 963–975): 60-second cooldown on transient errors (timeout, etc.). Returns `None`; middle turns are dropped without summary.

**Static fallback** (lines 1341–1347):
```
{SUMMARY_PREFIX}
Summary generation was unavailable. {n_dropped} message(s) were removed to free context space but could not be summarized...
```

**Aux model failure recovery** (lines 896–960): on 404/503/"model_not_found" from the aux model, immediately retries on the main model once. Generic errors also attempt one retry before entering cooldown.

**Anti-thrashing:** after 2 consecutive compressions each saving <10%, future compressions are skipped.

### Openclaw

**Retry with backoff** (`compaction.ts:339–359`): 3 attempts, minDelay 500ms, maxDelay 5000ms, jitter 0.2. Aborts immediately on abort/timeout errors (no retry).

**Fallback chain** (lines 402–464):
1. Try full summarization.
2. On failure, extract oversized messages (flag each with `"[Large ${role} (~${K}K tokens) omitted from summary]"`).
3. Summarize only the small messages.
4. If still failing, return static: `"Context contained ${N} messages (${M} oversized). Summary unavailable due to size limits."`

---

## Summary Table

| Dimension | Hermes | Openclaw |
|---|---|---|
| **Trigger condition** | `prompt_tokens ≥ 50% of context` | Runner-explicit on PI overflow |
| **Anti-thrashing** | Skip if last 2 compressions <10% savings | N/A (manual) |
| **Mid-turn precheck** | No | Optional (`midTurnPrecheck`) |
| **Protected head** | First 3 messages | First N per chunk |
| **Tail budget** | 20% of threshold tokens | Adaptive chunking (40%→15%) |
| **Tail floor** | Min 3 messages | Tool-pair-aware boundary |
| **Tool-pair guard** | Yes — forward + backward boundary alignment | Yes — pending set tracked during chunking |
| **Orphan repair** | Yes — stubs inserted for orphaned tool_calls | Yes — tool-pair repair after chunk drop |
| **Live tool output cap** | None (preserved in full) | 8,000 chars, head-only |
| **Live truncation encoding** | N/A | UTF-16 safe (`truncateUtf16Safe`) |
| **Live disk offload** | No | No |
| **Compaction tool output cap** | 6K/msg (4K head + 1.5K tail) | 16K chars; ≤30% context-share cap |
| **Compaction truncation suffix** | `"\n...[truncated]...\n"` | `formatContextLimitTruncationNotice(N)` |
| **Compaction encoding** | N/A (Python string slice) | UTF-16 safe |
| **Pre-compaction tool dedup** | Yes — MD5 hash, older copies stubbed | No |
| **Pre-compaction arg truncation** | Yes — JSON args > 500 chars → 200-char leaves | No |
| **Media stripped for compaction** | Not explicit | Yes (`stripToolResultDetails`) |
| **Security strip** | Secrets redacted in summary prompt | `toolResult.details` always stripped |
| **Prune phase** | No (replace in-place pre-pass) | No (chunk-level drop) |
| **Summary model** | Aux model (Gemini Flash); fallback to main | Main session model |
| **Summary template** | 12-section open-ended (Active Task is MOST CRITICAL) | Custom instruction sets (merge, identifier preservation, handoff) |
| **Identifier preservation** | Implicit (preserve exact requests) | Explicit policy — `"strict"` / `"off"` / `"custom"` |
| **Iterative update** | Yes — preserve + add to numbered list | Yes — via MERGE_SUMMARIES_INSTRUCTIONS |
| **Summary reinsertion** | Standalone message or prepended to tail with separator | Staged merge output fed back to PI |
| **Resumption** | Implicit (tail preserved) | PI continues; grace-period extension for in-flight runs |
| **Transcript rotation** | No | Yes — post-compaction file rotation |
| **Plugin hooks** | No | Yes — `runPostCompactionSideEffects` |
| **Lane queuing** | No | Yes — session lane + global lane |
| **Retry on summary failure** | 60s cooldown; 1 aux→main retry | 3 retries, exponential backoff (500ms–5s) |
| **Static fallback** | Yes — `"{N} messages removed, summary unavailable"` | Yes — `"Context contained N messages (M oversized)"` |
| **Handoff snapshot** | No | Yes — 4K token cap, leader/subordinate framing |
