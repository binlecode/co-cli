# Agentic Context & History Management — Peer Survey

Breadth survey across peer repos in `~/workspace_genai/` of how each manages the agent's **working context** — the messages, tool calls, and tool results that flow through the model each turn, how that history is windowed and compacted, and how context is kept bounded across long-running and background agentic loops.

**Companion doc.** `RESEARCH-context-management-comparison.md` is the depth dive — line-level compaction mechanics for hermes vs. openclaw (trigger thresholds, tail budgets, tool-pair repair, summarizer prompts). This doc is the breadth map across all peers; defer to the comparison doc for hermes/openclaw compaction internals.

**Scope.** In: agent loop / turn accumulation, tool-call result lifecycle in context, conversation-history window selection, compaction/summarization, long-running and background-loop context. Out (separate subsystems, separate research): long-term declarative memory (semantic stores, decay), personality/character schema, tool approval / permissions / sandboxing, model routing / provider extensibility, skill & plugin lifecycle.

Every claim below is grounded in source at these HEADs (verified 2026-05-27): `hermes-agent` `bb4703c76`, `openclaw` `323c9760d3` (built on `@earendil-works/pi-coding-agent` v0.75.1 — PI owns the core loop), `opencode` `a78605f8e`, `codex` `9fe55d68e6`.

## Peer Survey — Context & History Lens

| Repo | Context & history mechanisms |
|------|------------------------------|
| `hermes-agent` | • **agent loop**: extracted `conversation_loop.py` (callback-driven streaming, `IterationBudget`, `_try_activate_fallback()` model chaining)<br>• **tool-call context**: tool outputs preserved in full during live turns; pruned only when compaction fires (no live cap) — `conversation_loop.py:3429`<br>• **compaction** (`context_compressor.py`): fires at 50% of context window (`threshold_percent=0.50`, :515), protected head = first 3 (`protect_first_n=3`, :516), tail budget = 20% of threshold (`summary_target_ratio=0.20`, :518), forward/backward tool-pair boundary alignment (:1299, :1329), orphan repair stubbing summarized tool_calls (:1278), dedup + content-stub + arg-truncation prepass (:718/:744/:780), **13-section** summary template (:961-1014) on a dedicated aux model with fallback to main (:887)<br>• **long-loop**: `trajectory_compressor.py` distills post-session JSONL; croniter cron scheduler (`cron/scheduler.py`, 60s tick) — jobs as JSON in `~/.hermes/cron/jobs.json`, per-job timestamped `.md` output under `output/<job_id>/` (`cron/jobs.py:39,45,1103`)<br>HEAD `bb4703c76` |
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
| Compaction trigger & summarization | • `hermes-agent`, `openclaw` — both, depth in companion doc<br>• `codex` — dual-layer schema-trim + checkpoint<br>• `opencode` — event-based (`Started`/`Ended`), sequential |
| Long-running / background-loop context | • `hermes-agent` — cron jobs as isolated loops, `trajectory_compressor`<br>• `openclaw` — transcript rotation, grace period, handoff snapshot |
| Multi-agent history provenance | • `codex` — `AgentThread`/`AgentOrigin` lineage + `RolloutTrace` interaction-edge graph |

## Co's Current Position

Co already runs a layered budget stack (see `docs/specs/compaction.md` §1.2) that covers most of what the peers do per-turn:

- **L0** — `MAX_TOOL_CALLS_PER_MODEL_REQUEST=3` admission cap (bounds worst-case fan-out per response; sized for small-model coherence).
- **L1** — `spill_if_oversized` emit-time persistence to `tool-results/<sha16>.txt` behind a placeholder + preview. Note: **no surveyed peer persists tool output this way** — opencode and openclaw both *truncate* oversized output in place (`truncateToolOutput`, `TOOL_RESULT_MAX_CHARS`) rather than spilling it to a content-addressed store, so the recoverable-spill design is co-specific.
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
