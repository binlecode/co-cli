# Agentic Context & History Management — Peer Survey

Breadth survey across peer repos in `~/workspace_genai/` of how each manages the agent's **working context** — the messages, tool calls, and tool results that flow through the model each turn, how that history is windowed and compacted, and how context is kept bounded across long-running and background agentic loops.

**Structure.** Sections "Peer Survey" through "Dimension Alignment" are the breadth map across all peers. The "Hermes vs. Openclaw — Compaction Depth Dive" section is the line-level mechanics for the two richest compaction implementations (trigger thresholds, tail budgets, tool-pair repair, summarizer prompts) — every constant and line number there re-verified against source HEAD.

**Scope.** In: agent loop / turn accumulation, tool-call result lifecycle in context, conversation-history window selection, compaction/summarization, long-running and background-loop context. Out (separate subsystems, separate research): long-term declarative memory (semantic stores, decay), personality/character schema, tool approval / permissions / sandboxing, model routing / provider extensibility, skill & plugin lifecycle.

Every claim below is grounded in source at these HEADs (verified 2026-05-27): `hermes-agent` `bb4703c76`, `openclaw` `323c9760d3` (built on `@earendil-works/pi-coding-agent` v0.75.1 — PI owns the core loop), `opencode` `a78605f8e`, `codex` `9fe55d68e6`.

## Peer Survey — Context & History Lens

| Repo | Context & history mechanisms |
|------|------------------------------|
| `hermes-agent` | • **agent loop**: extracted `conversation_loop.py` (callback-driven streaming, `IterationBudget`, `_try_activate_fallback()` model chaining)<br>• **tool-call context**: tool outputs preserved in full during live turns; pruned only when compaction fires (no live cap) — `conversation_loop.py:3429`<br>• **compaction** (`context_compressor.py`): fires at 50% of context window (`threshold_percent=0.50`, :515), protected head = first 3 (`protect_first_n=3`, :516), tail budget = 20% of threshold (`summary_target_ratio=0.20`, :518), forward/backward tool-pair boundary alignment (:1299, :1329), orphan repair stubbing summarized tool_calls (`_sanitize_tool_pairs`, :1239), dedup + content-stub + arg-truncation prepass (:736/:773/:797), **13-section** summary template (:961-1013) on a dedicated aux model with fallback to main (:888)<br>• **long-loop**: `trajectory_compressor.py` distills post-session JSONL; croniter cron scheduler (`cron/scheduler.py`, 60s tick) — jobs as JSON in `~/.hermes/cron/jobs.json`, per-job timestamped `.md` output under `output/<job_id>/` (`cron/jobs.py:39,45,1103`)<br>HEAD `bb4703c76` |
| `openclaw` | • **compaction orchestration** (on top of PI's loop, `src/agents/compaction.ts`): adaptive chunk ratios `BASE_CHUNK_RATIO=0.4` → `MIN_CHUNK_RATIO=0.15` (:20-21), `SAFETY_MARGIN=1.2` (:22), mid-turn precheck (`preemptive-compaction.ts:41`), staged multi-phase summarization chunk→merge via `MERGE_SUMMARIES_INSTRUCTIONS` (:25-38, :466-530), 3-retry backoff (:339) + size-fallback chain (:402-464)<br>• **tool-call context**: live delivery cap `TOOL_RESULT_MAX_CHARS=8000` head-only (`pi-embedded-subscribe.tools.ts:17`), UTF-16-safe truncation (`truncateUtf16Safe`, :24)<br>• **loop detection**: `tool-loop-detection.ts:467` (`detectToolCallLoop`, circuit-breaker thresholds 10/20/30)<br>• **long-loop**: post-compaction transcript rotation (`compaction-successor-transcript.ts`) keeps the active file bounded; lane-queued compaction (`compact.queued.ts:125-130`); grace-period deadline extension for in-flight runs (`compaction-timeout.ts:16`); handoff snapshot (`compaction.ts:602`, 4K-token cap :619, leader/subordinate framing :43-57)<br>HEAD `323c9760d3` · PI v0.75.1 |
| `opencode` | • **agent loop**: FIFO prompt queue (`runtime.queue.ts:36,100` — `queue.shift()`), `AbortController` interrupts (:161,188), dual entry (remote-attach `runInteractiveMode` vs in-process `runInteractiveLocalMode`, `runtime.ts:6-7`)<br>• **tool-call context**: **in-place truncation only** — `truncateToolOutput()` (`message-v2.ts:281`), `TOOL_OUTPUT_MAX_CHARS=2000` on the compaction path (`compaction.ts:37,408`). No disk spill or content-addressed placeholder; oversized output is dropped inline (media goes to `FilePart` attachments)<br>• **compaction**: events `Compaction.Started`/`Ended` with `reason: auto\|manual` (`session-event.ts:332`, `compaction.ts:604`) — a `Compaction.Delta` type is declared but **never emitted**; compaction runs **sequentially** in the prompt loop (`prompt.ts:1310`), not concurrently with streaming<br>HEAD `a78605f8e` |
| `codex` | • **compaction**: dual-layer — `compact_large_tool_schema()` tool-schema trimming to ≤4KB (`tools/src/json_schema.rs:199`) + history-replacement checkpoint (`CompactionCheckpointTracePayload` / `CompactionInstalled` event, `rollout-trace/src/compaction.rs:78,138`)<br>• **multi-agent history provenance**: `AgentThread.origin = AgentOrigin::Spawned` upward lineage (`model/session.rs:33,55`) + `RolloutTrace.interaction_edges` directed graph (`model/mod.rs:87`, `runtime.rs:305`) with edge kinds SpawnAgent / AssignAgentTask / SendMessage / AgentResult / CloseAgent — long multi-agent runs keep auditable context lineage<br>HEAD `9fe55d68e6` |

## Dimension Alignment

Which peers to study for each context/history concern:

| Concern | Primary peers |
|---|---|
| Agent loop & turn accumulation | • `hermes-agent` — `conversation_loop.py`, iteration budget + fallback chaining<br>• `opencode` — FIFO queue, `AbortController` |
| Tool-call result lifecycle in context | • `openclaw` — live 8K head-only cap, UTF-16-safe<br>• `hermes-agent` — compaction-time dedup / content-stub / arg-truncation<br>• `opencode` — in-place truncation only (`truncateToolOutput`, 2K cap) |
| Conversation-history window selection | • `hermes-agent` — protected head + 20% tail budget, tool-pair boundary alignment, orphan repair<br>• `openclaw` — token-share chunking, tool-pair-aware boundary |
| Compaction trigger & summarization | • `hermes-agent`, `openclaw` — both, depth in the Compaction Depth Dive section below<br>• `codex` — dual-layer schema-trim + checkpoint<br>• `opencode` — event-based (`Started`/`Ended`), sequential |
| Long-running / background-loop context | • `hermes-agent` — cron jobs as isolated loops, `trajectory_compressor`<br>• `openclaw` — transcript rotation, grace period, handoff snapshot |
| Multi-agent history provenance | • `codex` — `AgentThread`/`AgentOrigin` lineage + `RolloutTrace` interaction-edge graph |

## Hermes vs. Openclaw — Compaction Depth Dive

Line-level compaction mechanics for the two richest implementations. Opencode is materially thinner (truncate-in-place only — `TOOL_OUTPUT_MAX_CHARS=2000`, `session/message-v2.ts:281`; sequential `Started`/`Ended` events) and codex is schema-trim + checkpoint; neither warrants a depth dive. All constants and line numbers below re-verified against `hermes-agent` `bb4703c76` (`agent/context_compressor.py` unless noted) and `openclaw` `323c9760d3` (`src/agents/` unless noted) on 2026-05-27.

### Trigger

- **Hermes:** `should_compress(prompt_tokens)` fires at `max(int(context_length * threshold_percent), MINIMUM_CONTEXT_LENGTH)` — default 50% of the window (`threshold_percent=0.50`, :515; computed :501/:554). Post-response only. **Anti-thrash:** if the last two compressions each saved <10%, skip to avoid a loop (:617-628).
- **Openclaw:** no internal threshold — invoked by the runner on PI overflow. Adaptive chunk ratio `BASE_CHUNK_RATIO=0.4` (`compaction.ts:20`) floored at `MIN_CHUNK_RATIO=0.15` (:21); when average message size is large the ratio drops via `reduction = min(avgRatio*2, BASE−MIN)`, `ratio = max(MIN, BASE−reduction)` (:298-299). `SAFETY_MARGIN=1.2` divides every token estimate (:22). Optional mid-turn precheck (`preemptive-compaction.ts:41`).

### History window / message selection

- **Hermes:** protected head = first `protect_first_n=3` (:516). Tail = `tail_token_budget = threshold_tokens × summary_target_ratio` (`summary_target_ratio=0.20`, :518; derived :506-507). Last-user-message safeguard pulls the boundary back when the most recent user turn would be compressed (`_ensure_last_user_message_in_tail`, :1366-1411, bug #10896). Tool-pair integrity: `_align_boundary_forward` (:1299) slides past orphaned results; `_align_boundary_backward` (:1329) keeps a tool_call/result group whole. Orphan repair `_sanitize_tool_pairs` (:1239) inserts the stub `"[Result from earlier conversation — see context summary above]"` (:1290) for orphaned tool_calls.
- **Openclaw:** token-share chunking into `DEFAULT_PARTS=2` (`compaction.ts:24`) by token budget, not message count. A pending-tool-call set prevents a chunk boundary mid tool-pair. Messages whose `tokens × SAFETY_MARGIN` exceeds 50% of the window are flagged non-summarizable. Handoff context is limited to `maxHistoryShare` (50%, or 20% for handoff); oldest chunks are dropped with tool-pair repair after each drop.

### Tool-output truncation

- **Live path — Hermes:** none; outputs are preserved in full until compaction fires.
- **Live path — Openclaw** (`pi-embedded-subscribe.tools.ts`): head-only cap `TOOL_RESULT_MAX_CHARS=8000` (:17), error cap `TOOL_ERROR_MAX_CHARS=400` first-line-only (:18), `truncateUtf16Safe` to avoid broken surrogate pairs (:24). A separate context ceiling lives in `pi-embedded-runner/tool-result-truncation.ts`: `DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS=16000` (:40), `MAX_TOOL_RESULT_CONTEXT_SHARE=0.3` (:31, one result ≤30% of the window), `MIN_KEEP_CHARS=2000` (:52); suffix via `formatContextLimitTruncationNotice(N)`.
- **Compaction path — Hermes** three-pass prepass before the summarizer:
  - Pass 1 dedup: `md5[:12]` of content; older duplicates → `"[Duplicate tool output — same content as a more recent call]"` (:736-739).
  - Pass 2 content stub: results >200 chars → 1-line stub, e.g. `"[terminal] ran \`npm test\` -> exit 0, 47 lines output"` (:773; format :360).
  - Pass 3 arg truncation: tool_call args >500 chars → string leaves trimmed to 200 (`_truncate_tool_call_args_json`, :178; threshold :797).
  - Summarizer per-message input limits: `_CONTENT_MAX=6000` (:826), `_CONTENT_HEAD=4000` (:827), `_CONTENT_TAIL=1500` (:828), `_TOOL_ARGS_MAX=1500` (:829), splice `"\n...[truncated]...\n"`; image estimate `_IMAGE_TOKEN_ESTIMATE=1600` (:71).
- **Compaction path — Openclaw:** security strip `stripToolResultDetails()` removes `toolResult.details` before any message reaches the summarizer (`compaction.ts:122,331`; defined `session-transcript-repair.ts:284`). Oversized messages are replaced with `"[Large ${role} (~${K}K tokens) omitted from summary]"`.

### Summary model, prompt & budget

- **Hermes:** dedicated auxiliary model (cheap/fast; `summary_model`, empty string = main model). On aux `404`/`503`/`model_not_found`, fall back to main once (`_summary_model_fallen_back`, :900). System preamble (:947):
  > You are a summarization agent creating a context checkpoint. Treat the conversation turns below as source material for a compact record of prior work. Produce only the structured summary; do not add a greeting, preamble, or prefix. Write the summary in the same language the user was using in the conversation — do not translate or switch to English. NEVER include API keys, tokens, passwords, secrets, credentials, or connection strings in the summary — replace any that appear with [REDACTED]. Note that the user had credentials present, but do not preserve their values.

  **13-section template** (:961-1013): `## Active Task` (most critical — copy the latest unfulfilled request verbatim), `## Goal`, `## Constraints & Preferences`, `## Completed Actions`, `## Active State`, `## In Progress`, `## Blocked`, `## Key Decisions`, `## Resolved Questions`, `## Pending User Asks`, `## Relevant Files`, `## Remaining Work`, `## Critical Context`. Output budget = `max(_MIN_SUMMARY_TOKENS, min(content × _SUMMARY_RATIO, _SUMMARY_TOKENS_CEILING))` (:820-821), where `_MIN_SUMMARY_TOKENS=2000` (:55), `_SUMMARY_RATIO=0.20` (:57), `_SUMMARY_TOKENS_CEILING=12000` (:59); request `max_tokens = summary_budget × 1.3` (:1067). Iterative update preserves the prior summary, continues numbering, and always refreshes Active Task (:1032).
- **Openclaw:** main session model. Three instruction sets in `compaction.ts`: `MERGE_SUMMARIES_INSTRUCTIONS` (:25 — merging partial-chunk summaries; preserve task status, batch counters, last request, decisions, TODOs, open questions), `IDENTIFIER_PRESERVATION_INSTRUCTIONS` (:39 — "preserve all opaque identifiers exactly": UUIDs/hashes/IPs/URLs/filenames; `strict`/`off`/`custom`), `HANDOFF_INSTRUCTIONS` (:43 — leader/subordinate framing for model-quota handoffs).

### Post-compaction & retry

- **Hermes post-compaction:** the summary is reinserted as a standalone message in whichever role preserves alternation, or prepended to the first tail message with the separator `"--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"` (:1693). The system message gets a one-time compaction note (:1638) that also asserts persistent memory (MEMORY.md, USER.md) stays authoritative regardless of compaction. No transcript rotation.
- **Hermes retry:** transient summary failures enter a **600s cooldown** (`_SUMMARY_FAILURE_COOLDOWN_SECONDS=600`, :76 — not 60s; middle turns drop without summary), static fallback `"{SUMMARY_PREFIX}\nSummary generation was unavailable. {n} message(s) were removed..."` (:1656). Aux→main retry once on model errors; anti-thrash skip after 2 sub-10% compressions.
- **Openclaw post-compaction:** lane-queued execution (session + global lane) prevents concurrent compaction; a grace period extends an in-flight run's deadline rather than cancelling (`compaction-timeout.ts:16`); staged multi-phase summarization chunk→summarize→merge (`compaction.ts:466-530`); post-compaction hooks (`runPostCompactionSideEffects`); transcript rotation keeps the active file bounded (`compaction-successor-transcript.ts`); handoff snapshot at a 4K-token cap with leader/subordinate framing (`compaction.ts:602`).
- **Openclaw retry:** 3-attempt backoff (`minDelayMs=500`, `maxDelayMs=5000`, `jitter=0.2`; `compaction.ts:339,352-355`), aborts immediately on abort/timeout. Fallback chain (:402-464): full summarize → extract oversized + summarize only the small messages → static `"Context contained N messages (M oversized)..."`.

### Dimension comparison

| Dimension | Hermes | Openclaw |
|---|---|---|
| **Trigger condition** | `prompt_tokens ≥ 50% of context` (:515) | Runner-explicit on PI overflow |
| **Anti-thrashing** | Skip if last 2 compressions <10% savings (:617-628) | N/A |
| **Mid-turn precheck** | No | Optional (`preemptive-compaction.ts:41`) |
| **Protected head** | First 3 messages (`protect_first_n=3`, :516) | First N per chunk |
| **Tail budget** | 20% of threshold tokens (`summary_target_ratio=0.20`, :518) | Adaptive chunking (0.4→0.15) |
| **Tail floor** | Min 3 messages | Tool-pair-aware boundary |
| **Tool-pair guard** | Forward + backward boundary alignment (:1299/:1329) | Pending-set tracked during chunking |
| **Orphan repair** | Stub inserted for orphaned tool_calls (:1239/:1290) | Tool-pair repair after chunk drop |
| **Live tool output cap** | None (preserved in full) | 8000 chars head-only (:17) |
| **Live truncation encoding** | N/A | UTF-16 safe (`truncateUtf16Safe`) |
| **Live disk offload** | No | No |
| **Compaction tool output cap** | 6K/msg = 4K head + 1.5K tail (:826-828) | 16K chars; ≤30% context-share (:40/:31) |
| **Pre-compaction tool dedup** | Yes — md5[:12], older copies stubbed (:736) | No |
| **Pre-compaction arg truncation** | Yes — args >500 chars → 200-char leaves (:797) | No |
| **Media / detail stripped for compaction** | Secrets redacted via preamble (:947) | `toolResult.details` stripped (:331) |
| **Summary model** | Aux model; fallback to main (:888/:900) | Main session model |
| **Summary template** | 13-section, Active Task most critical (:961-1013) | Instruction sets (merge / identifier / handoff) |
| **Identifier preservation** | Implicit (copy exact requests) | Explicit policy — `strict`/`off`/`custom` (:39) |
| **Iterative update** | Yes — preserve + continue numbering (:1032) | Yes — via `MERGE_SUMMARIES_INSTRUCTIONS` (:25) |
| **Summary reinsertion** | Standalone or prepended to tail with separator (:1693) | Staged merge output fed back to PI |
| **Transcript rotation** | No | Yes — post-compaction file rotation |
| **Lane queuing** | No | Yes — session lane + global lane |
| **Grace period** | No | Yes — deadline extension (`compaction-timeout.ts:16`) |
| **Retry on summary failure** | 600s cooldown; 1 aux→main retry (:76) | 3 retries, 500ms–5s backoff (:352-355) |
| **Static fallback** | `"{N} message(s) removed, summary unavailable"` (:1656) | `"Context contained N messages (M oversized)"` (:402-464) |
| **Handoff snapshot** | No | Yes — 4K-token cap, leader/subordinate framing (:602) |

## Co's Current Position

Co already runs a layered budget stack (see `docs/specs/compaction.md` §1.2) that covers most of what the peers do per-turn:

- **L0** — `MAX_TOOL_CALLS_PER_MODEL_REQUEST=3` admission cap (bounds worst-case fan-out per response; sized for small-model coherence).
- **L1** — `spill_if_oversized` emit-time persistence to `tool-results/<sha16>.txt` behind a placeholder + preview. Note: **no surveyed peer persists tool output this way** — opencode and openclaw both *truncate* oversized output in place (opencode `truncateToolOutput`, `TOOL_OUTPUT_MAX_CHARS=2000`; openclaw `TOOL_RESULT_MAX_CHARS=8000`) rather than spilling it to a content-addressed store, so the recoverable-spill design is co-specific.
- **L2** — `enforce_request_size` history processor: force-spills the largest unspilled tool returns until the upcoming request is under threshold.
- **L3** — `proactive_window_processor`: LLM compaction assembled as `head | marker | [todo_snapshot] | [deferred-tool discoveries] | tail`, with anti-thrash guard and static-marker fallback.
- **History processors** — `dedup_tool_results` (collapse identical returns) + `evict_old_tool_results` (keep 5 most recent per tool) run ahead of L2/L3.
- **Manual** — `/compact [focus]` over full-history bounds.

Where co has **no peer-parity mechanism**:
- No cross-session **trajectory compression** (hermes `trajectory_compressor.py`) — co compacts in-place within a session but does not distill a finished session's JSONL.
- No **transcript rotation** (openclaw) — co rewrites the transcript in place on compaction (`sessions.md`); the active JSONL is not rotated.
- No **multi-agent history provenance** (codex `AgentThread`/`AgentOrigin` lineage + `RolloutTrace` interaction-edge graph) — co has no subagent context lineage.

## Open Research Areas

Context/history gaps where peer patterns are the relevant prior art:

1. **Cross-session / long-loop trajectory compression** — hermes `trajectory_compressor.py` distills post-session JSONL into a compact trajectory. Co compacts *within* a live session but has no post-session distillation for recurring or background loops. Most relevant to the dream daemon and any future scheduled-agent loop, which accumulate transcript without an end-of-run summarization step.

2. **Transcript growth under long loops** — openclaw rotates the transcript after compaction to keep the active file bounded; co rewrites in place (`sessions.md`) and never rotates. Research whether unbounded session JSONL is a real cost for very long or background runs, or whether in-place rewrite is sufficient.

3. **Background / cron loop context isolation** — hermes runs cron jobs as independent agentic loops, each with its own JSONL and timestamped output, isolated from interactive sessions. Co's dream daemon is the closest analog. Study how a background loop should manage a bounded context separate from the interactive REPL's.

4. **Multi-agent history provenance** — codex's `AgentThread`/`AgentOrigin` lineage + `RolloutTrace` interaction-edge graph track subagent provenance across spawned threads. Co has no multi-agent context lineage today — relevant prior art if co ever spawns subagents and needs to reconstruct *which* agent produced *which* context.

5. **Compaction trigger model** — co fires L3 post-response at `compaction_ratio × budget`. Openclaw additionally supports a mid-turn precheck (`preemptive-compaction.ts:41`) that can compact before a turn completes when context is already near the limit. Open question: does mid-turn precheck buy anything for co's small-local-model target, or do L0 + L2 already cap per-turn growth tightly enough to make it redundant?
