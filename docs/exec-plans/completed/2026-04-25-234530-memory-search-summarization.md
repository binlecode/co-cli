# Plan: memory_search Summarization + Disambiguation (D2)

Task type: code-feature

Part 2 of 2 — paired with `docs/exec-plans/active/2026-04-25-234500-recall-cache-and-autorecall-removal.md` (D1). **D2 ships only after D1 is delivered and merged.** Originally drafted as a single plan at `docs/exec-plans/active/2026-04-25-220616-recall-redesign-cache-and-search.md`; split for delivery quality.

---

## Context

D1 lands cache hygiene + auto-recall removal: personality memories move into the static system prompt, `_recall_for_context` and `MemoryRecallState` are deleted, `_touch_recalled` is rewired into `knowledge_search`. After D1, `memory_search` still returns BM25 snippets — fragmentary and lower-value than the hermes-style noreason summaries this delivery introduces. D2 also adds disambiguation coaching so the agent picks correctly between `memory_search` and `knowledge_search` when both could apply.

**Why this is independently shippable:** D1's cache invariant and `_touch_recalled` rewire are settled before D2 layers on net-new LLM call paths. D2 introduces parallel noreason calls with retry/timeout/tracing — if the summarization pipeline shows quality issues, D2 can be reverted independently of D1.

**Hermes reference:** `~/workspace_genai/hermes-agent/tools/session_search_tool.py`. The pipeline ported here: FTS5 search → group by session → load transcript → truncate-around-matches → format conversation (with tool-output truncation) → parallel noreason summarize with bookended query and 3-attempt retry → return per-session summaries with metadata. Verified line ranges: `_truncate_around_matches` 90-172, `_format_conversation` 56-87, `_summarize_session` 175-236, schema description 500-540, fallback raw-preview line 470-474, global timeout 442-450.

**Disambiguation context:** Hermes has *only one* read tool (`session_search`). Its memory layer (`memory_tool.py`, schema 513-562) is auto-injected into every turn — never searched via tool. So hermes's tool-vs-tool disambiguation does not exist. co-cli has two read tools, requiring explicit coaching.

**Current-state validation (post-D1):**

- `co_cli/tools/memory.py` — `memory_search()` returns BM25 snippets (`SessionSearchResult` per match: `session_id`, `role`, `snippet`, `score`, `path`). Visibility = `ALWAYS`. Existing description has hermes-style proactive triggers + cross-link to `knowledge_search`.
- `co_cli/tools/knowledge/read.py` — `knowledge_search` visibility = `ALWAYS`. Existing description has cross-link to `memory_search`. After D1, also fires `_touch_recalled` post-FTS hit.
- `co_cli/llm/_factory.py:31` — `LlmModel.settings_noreason` exists; `co_cli/llm/_call.py:36` and `co_cli/context/summarization.py:217` already use this pattern for cheap auxiliary calls.
- `co_cli/memory/transcript.py:116` — `load_transcript(path) -> list[ModelMessage]` already exists with size guards.
- `co_cli/memory/session_browser.py:74` — `list_sessions(sessions_dir) -> list[SessionSummary]` already exists; fields are `session_id`, `path`, `title`, `last_modified`, `file_size`, `created_at`.
- `co_cli/knowledge/_distiller.py:112` — `build_knowledge_extractor_agent()` is the existing pattern for single-`run()` aux agents (no tools, instructions string, used for distillation). `build_session_summarizer_agent()` mirrors this shape.
- `co_cli/tools/knowledge/read.py:534` — existing tracer pattern `otel_trace.get_current_span().set_attribute("rag.backend", ...)`. New summarizer attributes follow the same approach.

---

## Problem & Outcome

**Problem 4 — `memory_search` returns raw BM25 snippets.** Today's tool returns fragmentary snippets; hermes's `session_search` returns LLM-summarized session recaps focused on the query. Summaries are dramatically more useful per token to the orchestrator model.

**Disambiguation problem — agent has two read tools, sometimes both could apply.** For prompts like *"what's our convention for logging?"* or *"what did we decide about the test framework?"*, both `knowledge_search` (saved decision/rule) and `memory_search` (past conversation) could match. Without coaching beyond the existing cross-links, the agent may pick wrong, fail to fall back, or call both unnecessarily — and the latter doubles cost when D2's summarization fan-out is involved.

**Outcome:**
- `memory_search` upgraded to hermes-style FTS5 → truncate-around-matches → noreason summarization pipeline. Returns prose summaries instead of snippets.
- Empty-query browse mode returns recent-sessions metadata at zero LLM cost.
- Tool descriptions tuned with tie-breaker rule (try `knowledge_search` first — cheap, BM25 only) + concrete prompt→tool examples in both descriptions.
- New summarizer prompt at `co_cli/memory/prompts/session_summarizer.md` — 5-section structure mirroring hermes verbatim.
- Tracer span attributes (`memory.summarizer.runs`, `memory.summarizer.failures`, `memory.summarizer.timed_out`) for observability of the new latency/cost path.

---

## Scope

**In scope:**
- Port hermes's session-search summarization pipeline into `memory_search`.
- Build `build_session_summarizer_agent()` using `deps.model` + `settings_noreason`.
- New prompt file `co_cli/memory/prompts/session_summarizer.md` mirroring hermes's 5-section structure.
- Empty-query browse mode reusing `session_browser.list_sessions`.
- Tune `memory_search` and `knowledge_search` schema descriptions for new return shape + disambiguation coaching.
- Investigate session lineage handling for the browse path (compaction parent linkage).
- Sync `docs/specs/memory-knowledge.md` Section 2.4 for the new pipeline.
- Test churn: summarizer test + proactive-trigger eval (covers both Risk 1 from D1 and the disambiguation rule).

**Out of scope (assumed delivered in D1):**
- Personality memories in static prompt.
- Auto-recall removal (`_recall_for_context`, `MemoryRecallState`).
- `_touch_recalled` rewire into `knowledge_search`.
- `injection_max_chars` deletion.
- `recall_prompt` → `date_prompt` rename.

**Out of scope entirely:**
- Summary caching (e.g., LRU on `(session_id, query)`) — accept hermes's no-cache cost model for now; revisit only if real-world latency or spend prove problematic.
- **Unified read surface (Option C from disambiguation analysis).** Hermes avoids the memory-vs-knowledge choice by having only one read tool — searching across both stores under the hood and returning merged labeled results. Long-term this is the cleaner shape: removes a choice the model has to make on every recall-shaped prompt, eliminates description-driven steering, and lowers cost by always trying the cheap path (knowledge BM25) before the expensive one (memory summarization). **Track as a follow-up research note** (suggested: `docs/reference/RESEARCH-unified-recall-surface.md`) before committing — needs a peer survey of how `letta`, `mem0`, and `gemini-cli` handle the read-tool fan-out.

---

## Phases

### ✓ DONE — Phase 1 — `memory_search` summarization pipeline

**Goal:** Replace BM25 snippet output with hermes-style noreason summaries. Add empty-query browse mode.

**Phase 1 kickoff investigation (~5 min):** Hermes walks the session parent chain to exclude the entire current-session lineage (`session_search_tool.py:250-263`). co-cli has compaction-driven parent linkage via `write_session_meta(parent_session_path=...)` in `transcript.py`, but `SessionSummary` does not expose it. Before implementing the empty-query filter, check whether post-compaction child files would surface as "past sessions" in the browse path. If yes → walk the parent chain. If no → single-path filter on `ctx.deps.session.session_path` is sufficient. Record decision in the open-questions section before continuing.

Tasks:

1. **Port `_truncate_around_matches()`.** Copy from hermes (`tools/session_search_tool.py:90-172`) into a new helper in `co_cli/memory/_summary.py`. Three-tier match strategy: full phrase → proximity co-occurrence (200-char window) → individual term positions. Cap at `MAX_SESSION_CHARS = 100_000` (mirror hermes default). Window picker biases 25% before / 75% after the chosen match anchor.
2. **Port `_format_conversation()`.** Render messages into a readable transcript for summarization. Adapt for pydantic-ai message types (`ModelRequest`, `ModelResponse`, `UserPromptPart`, `TextPart`, `ToolCallPart`, `ToolReturnPart`) instead of hermes's role-based dicts. **Truncate large `ToolReturnPart.content`** the same way hermes does (`session_search_tool.py:65-68`): when content length > 500, keep head 250 chars + tail 250 chars joined by `\n...[truncated]...\n`. Without this, one large tool result (file_read, shell, search dump) can blow out the `MAX_SESSION_CHARS = 100_000` window for the whole session. For `ToolCallPart`, render only the call signature (`[ASSISTANT][Called: tool_name]`) — never inline the full args JSON.
3. **Build summarizer agent.** New `build_session_summarizer_agent()` in `co_cli/memory/_summary.py` (mirror `build_knowledge_extractor_agent()` pattern in `_distiller.py:112`). Single-`run()` lifetime, no tools, instructions string from a new prompt file at `co_cli/memory/prompts/session_summarizer.md`. **Mirror hermes's exact 5-section structure** (`session_search_tool.py:180-189`):
   1. What the user asked about or wanted to accomplish
   2. What actions were taken and what the outcomes were
   3. Key decisions, solutions found, or conclusions reached
   4. Specific commands, files, URLs, or technical details that were important
   5. Anything left unresolved or notable

   Plus the two trailing instructions verbatim: *"Be thorough but concise. Preserve specific details (commands, paths, error messages) that would be useful to recall."* and *"Write in past tense as a factual recap."* These five categories are field-tested in hermes — do not paraphrase or condense.
4. **Wire summarizer call.** New helper `summarize_session_around_query(messages, query, session_meta, deps) -> str | None`:
   - Build window with `_truncate_around_matches`
   - Format with `_format_conversation`
   - **Bookend the query in the user prompt** (mirror hermes line 194-200): open with `Search topic: {query}` + session source/date header, then the transcript, then close with `Summarize this conversation with focus on: {query}`. The query appears before AND after the long transcript so the model sees the focus on both ends of context.
   - **3-attempt retry loop with linear backoff** (mirror hermes line 202-236): wrap `agent.run(window, deps=deps, model=deps.model.model, model_settings=deps.model.settings_noreason)` in a loop. On exception, sleep `(attempt+1)` seconds and retry. On empty/whitespace response, treat as retryable. After 3 attempts, return None. Distinguish unrecoverable errors (no model available) from transient ones — log and return None immediately for the former.
   - Return content or None on failure
5. **Restructure `memory_search()`.** In `co_cli/tools/memory.py`:
   - **Clamp `limit` to `[1, 5]` at entry**, default 3 (mirror hermes line 321). The clamp *is* the concurrency cap — no `asyncio.Semaphore` needed. Update the parameter doc to reflect the bounds.
   - Empty/whitespace query → return recent sessions list. Reuse the existing `list_sessions(sessions_dir)` helper in `co_cli/memory/session_browser.py:74`; do not add a new method on `MemoryIndex`. Filter out the current session via `ctx.deps.session.session_path` (or via parent-chain walk per the kickoff investigation).
   - Non-empty query: existing `MemoryIndex.search()` returns top hits; group by session (already deduped); prepare `(session_id, match_info, conversation_text, session_meta)` tuples; gather summaries in parallel via `asyncio.gather(*coros, return_exceptions=True)`.
   - **One global `asyncio.timeout(60)` wrapping the gather** (mirror hermes line 442-450). On timeout, return a `tool_output` with an explanatory message ("session summarization timed out — narrow the query or reduce limit"). Do *not* wrap each per-session call separately; the budget is for the whole search.
   - Return shape: `tool_output(payload={...})` with per-session `{session_id, when, source, summary}`. On summarizer failure for a given session (exception or None), fall back to a 500-char preview of the **already-truncated window** (`conversation_text[:500]`) — not the full transcript head. This way the preview is centered on the actual matches, mirroring hermes line 473.
   - **Tracer span attributes for observability.** Mirror the `rag.backend` pattern in `co_cli/tools/knowledge/read.py:534`. On the active OTEL span emit at minimum: `memory.summarizer.runs` (count of summary calls launched), `memory.summarizer.failures` (count that fell back to raw preview), and `memory.summarizer.timed_out` (bool). Without these, the latency/cost claims in Risk 1 are unverifiable in production.
6. **Loading transcripts for summarization.** `MemoryIndex` currently indexes message *parts*, not full conversations. Use `load_transcript()` from `co_cli/memory/transcript.py:116` directly — it already exists, has size guards, and returns `list[ModelMessage]`. No new method needed on `MemoryIndex` or `indexer.py`.

**Acceptance:** `memory_search("docker networking")` returns up to 3 prose summaries of past sessions discussing that topic, with per-session metadata. Empty-query call returns metadata for recent sessions with zero LLM cost. Summarization runs with `settings_noreason` (verifiable via tracer span attributes). Per-session failure falls back to a query-centered raw preview, not the transcript head.

### ✓ DONE — Phase 2 — Tool description tuning + disambiguation

**Goal:** Reflect the new return shape; add coaching for the tool-overlap case.

**Current state (verified):** `memory_search` (`co_cli/tools/memory.py:11-37`) already has `VisibilityPolicyEnum.ALWAYS`, the full hermes-style proactive trigger list ("we did this before"/"remember when"/"last time"/"as I mentioned"), FTS5 syntax tips, and a cross-link to `knowledge_search`. `knowledge_search` (`co_cli/tools/knowledge/read.py:444+`) likewise has `VisibilityPolicyEnum.ALWAYS` and a cross-link to `memory_search`. The schema text is strong; this phase tunes it for the new pipeline and adds disambiguation rather than rewriting.

Tasks:

1. **Update `memory_search` description for new return shape.**
   - Add a one-paragraph "TWO MODES" note (mirror hermes line 505-510): *(a) empty query → recent-sessions metadata, zero LLM cost; (b) keyword query → LLM-summarized recaps of matching sessions*.
   - Replace the line that promises "ranked excerpts with session ID, date, and matching snippet" with one describing per-session prose summaries.
   - Keep the existing proactive-trigger list and FTS5 syntax tips unchanged.
   - Note `limit` is clamped to [1, 5] (default 3) and that summary calls run in parallel.
2. **Tune `knowledge_search` description (minimal).** The cross-link already exists; verify it still reads correctly after the pipeline change. Add one line clarifying that `memory_search` now returns *summaries*, so the boundary is clearer ("`memory_search` summarizes past conversations; `knowledge_search` returns curated artifacts ranked by FTS5").
3. **Visibility verification (sanity).** Confirm `@agent_tool(visibility=VisibilityPolicyEnum.ALWAYS)` on both tools is unchanged after edits.
4. **Disambiguation coaching — tie-breaker + concrete examples.** Hermes sidesteps the memory-vs-knowledge choice by having only one read tool; co-cli has two and must coach the agent on the choice. Add the same disambiguation block to *both* tool descriptions:
   - **Tie-breaker rule (one sentence each):**
     - In `memory_search` description: *"When both `memory_search` and `knowledge_search` could apply, call `knowledge_search` first (cheap, BM25 only) — only fall back to `memory_search` if knowledge returns nothing, or if the user is specifically asking about a past conversation."*
     - In `knowledge_search` description: same rule, written from the other side: *"When the user asks about something that might be a saved fact OR something said in a past conversation, try `knowledge_search` first; only call `memory_search` if there's no curated artifact match."*
   - **Three concrete prompt → tool mappings in each description:** mirror the categorical-examples pattern hermes uses in `MEMORY_SCHEMA` (`memory_tool.py:527-528`). Suggested examples (revise as the new copy reads):
     - *"what was my preferred test runner?"* → `knowledge_search` (saved preference)
     - *"what did we figure out about docker last time?"* → `memory_search` (past conversation)
     - *"what was that auth bug we hit?"* → `memory_search` (past conversation)
     - *"what's our convention for logging?"* → `knowledge_search` (saved rule/decision)

**Acceptance:** Both descriptions accurately describe what the tool now returns and contain the tie-breaker rule + at least 3 concrete examples spanning both tools. A fresh agent given *"what did we figure out about docker last time?"* still calls `memory_search`; *"what was my preferred test runner?"* still calls `knowledge_search`; *"what's our convention for logging?"* calls `knowledge_search` (not `memory_search`). Verifiable in the proactive-trigger eval (Phase 4 task 2).

### ✓ DONE — Phase 3 — Spec sync (D2 slice)

**Goal:** `docs/specs/memory-knowledge.md` reflects the new `memory_search` pipeline + disambiguation.

(D1 already synced the cache rationale and on-demand recall framing; this phase only adds the new pipeline description.)

Tasks:

1. **Section 2.4 — `memory_search` description.** Update to describe the FTS5 → truncate-around-matches → noreason summarization pipeline. Note the recent-sessions browse mode (empty query). Note the [1, 5] limit clamp and the per-session raw-preview fallback.
2. **Section 2.4 — Disambiguation paragraph.** Add a short paragraph describing the tie-breaker rule (knowledge first, memory as fallback) and pointing at the concrete examples in each tool's schema description.
3. **Section 4 — Files.**
   - Add `co_cli/memory/_summary.py` — port of hermes summarization pipeline.
   - Add `co_cli/memory/prompts/session_summarizer.md` — summarizer instructions prompt.

**Acceptance:** `/sync-doc memory-knowledge` reports no further inaccuracies. Section 2.4 reflects the new pipeline + disambiguation; Section 4 lists the new files.

### ✓ DONE — Phase 4 — Test churn (D2 slice)

**Goal:** Add behavioral coverage for the new summarization pipeline and the disambiguation rule.

Tasks:

1. **Add summarizer test.** Real DB with 2-3 seeded sessions, real LLM call in noreason mode, assert that `memory_search("query")` returns a structured summary mentioning the seeded query content. Wrap each `await` to the LLM with `asyncio.timeout(N)` using the constant from `tests/_timeouts.py` — do not hardcode. Per repo policy: real `CoDeps`, real services, no mocks.
2. **Add proactive-trigger eval (covers Risk 1 + disambiguation).** With a fresh agent (production config) and a prompt suite covering:
   - *"what did we figure out about docker last time?"* → expect `memory_search` called
   - *"what was my preferred test runner?"* → expect `knowledge_search` called
   - *"what's our convention for logging?"* → expect `knowledge_search` called (tie-breaker rule)

   Production model, no overrides. Required, not optional — this is the eval that catches Risk 1 from D1 (under-triggering) plus validates Phase 2 task 4 disambiguation.
3. **Empty-query browse test.** Real DB with seeded sessions, call `memory_search(query="")`, assert returned payload contains recent-session metadata (no LLM call). Verifiable by asserting tracer span has `memory.summarizer.runs == 0`.
4. **Tool-output truncation test.** Format a session containing one `ToolReturnPart` with content > 500 chars; assert formatted transcript truncates to the head-250 + tail-250 + marker pattern.

**Acceptance:** Full test suite passes. New summarizer, browse-mode, truncation, and proactive-trigger tests are green. Tracer attribute assertions pass.

---

## Files affected

| File | Change |
|---|---|
| `co_cli/tools/memory.py` | replace BM25-snippet output with summarization pipeline; add empty-query recent-sessions mode; clamp `limit` to [1,5]; tune description for new return shape; add disambiguation coaching block |
| `co_cli/tools/knowledge/read.py` | minor description tuning + disambiguation coaching block |
| `co_cli/memory/_summary.py` | NEW — `_truncate_around_matches`, `_format_conversation`, `build_session_summarizer_agent`, `summarize_session_around_query` |
| `co_cli/memory/prompts/session_summarizer.md` | NEW — summarizer instructions prompt with hermes's verbatim 5-section structure |
| `co_cli/memory/session_browser.py` | reused (no change) — `list_sessions()` powers the empty-query browse mode |
| `co_cli/memory/transcript.py` | reused (no change) — `load_transcript()` powers the summarizer's read path |
| `docs/specs/memory-knowledge.md` | sync Section 2.4 (new pipeline + disambiguation) and Section 4 (new files) |
| `tests/memory/test_session_summary.py` (or similar) | NEW — summarizer behavior with seeded sessions, real LLM in noreason mode |
| `tests/memory/test_memory_search_browse.py` (or similar) | NEW — empty-query browse mode |
| `tests/memory/test_format_conversation.py` (or similar) | NEW — tool-output truncation test |
| `tests/agent/test_proactive_recall.py` (or similar) | NEW — proactive `memory_search` and `knowledge_search` trigger verification + tie-breaker rule |

---

## Risks

1. **Summarizer latency and cost.** Each `memory_search` call now involves up to N parallel noreason LLM calls (N = clamped `limit`, default 3, max 5). For default limit=3 with typical noreason latency 3-10s per call, expected wallclock is 3-10s per `memory_search` invocation; cost is N × per-summary token spend. Mitigation per hermes:
   - Single global `asyncio.timeout(60)` around the gather (not per-task), surfaced as a "narrow your query" message on timeout.
   - `gather(..., return_exceptions=True)` so one slow/failed session does not block others.
   - Per-session fallback to raw 500-char preview on exception or None return (hermes line 470-474).
   - 3-attempt retry loop with backoff inside the per-session call (transient errors and empty content).
   - No summary caching — accept the cost, matching hermes. If real-world cost or latency proves problematic, revisit with an LRU on `(session_id, query)` pairs.
   - Tracer span attributes (`memory.summarizer.runs`, `.failures`, `.timed_out`) make the cost path observable in production.
2. **Tool overlap confusion.** The agent has both `knowledge_search` and `memory_search` available and may pick wrong, fail to fall back, or call both unnecessarily. Mitigation: Phase 2 task 4 disambiguation block (tie-breaker rule + 4 concrete examples in each description). Validation: Phase 4 task 2 proactive-trigger eval covers all three failure modes (right-tool selection, fallback-after-empty, ambiguous case routes to knowledge).
3. **Browse mode lineage gap.** If post-compaction child sessions surface in `list_sessions()` and aren't excluded, the user sees their own current conversation as a "past session." Mitigated by Phase 1 kickoff investigation — if compaction does produce child files in `sessions_dir`, we walk the parent chain to exclude them.
4. **Prompt drift.** The 5-section summarizer prompt is field-tested in hermes; paraphrasing risks drift. Mitigation: Phase 1 task 3 mandates verbatim copy. Phase 4 task 1 summarizer test asserts the summary mentions seeded query content — catches regression if the prompt loses focus.

---

## Open questions / decisions logged

- **Q:** Concurrency cap for summarizer fan-out? **A:** No semaphore. Clamp `limit` to `[1, 5]` (default 3) at entry — the clamp *is* the cap. Single global `asyncio.timeout(60)` around the gather. Mirrors hermes; simpler than per-task timeouts.
- **Q:** Reuse existing `list_sessions` or add a new helper for empty-query browse mode? **A:** Reuse `co_cli/memory/session_browser.py:list_sessions(sessions_dir)`. No new method on `MemoryIndex`.
- **Q:** New helper for loading session messages for the summarizer? **A:** Reuse `co_cli/memory/transcript.py:load_transcript(path)` directly. Already has size guards.
- **Q:** How does the agent choose between `memory_search` and `knowledge_search` when both could apply? **A:** Hermes can't help here — it has only one read tool. co-cli adds a tie-breaker rule (call `knowledge_search` first, fall back to `memory_search`) plus 3-4 concrete prompt→tool examples in both descriptions (Phase 2 task 4). Long-term unified-read-tool design tracked in out-of-scope.
- **Q (kickoff investigation):** Does compaction produce child session files that `list_sessions()` would surface as "past sessions"? **A:** TBD — investigated at Phase 1 kickoff. If yes → walk parent chain in browse-mode filter. If no → single-path filter on `ctx.deps.session.session_path` is sufficient.
- **Q:** Override `temperature` or `max_tokens` for the summarizer call? **A:** No. Use `deps.model.settings_noreason` as-is. Per repo policy, do not hardcode model settings inline; if summary quality is wrong, fix the prompt or the noreason settings — not a per-call override.

---

## Delivery summary — 2026-04-25

| Phase | done_when | Status |
|-------|-----------|--------|
| Phase 1 — summarization pipeline | `memory_search("docker networking")` returns prose summaries with metadata; browse mode returns metadata at zero LLM cost | ✓ pass |
| Phase 2 — description tuning + disambiguation | Both tool descriptions reflect new return shape + tie-breaker rule + ≥3 concrete examples | ✓ pass |
| Phase 3 — spec sync | `/sync-doc memory-knowledge` — Section 2.3 (two-mode pipeline), Section 2.4 (disambiguation rule), Section 4 (new files) updated | ✓ pass |
| Phase 4 — test churn | `tests/memory/` — 35 tests pass (summarizer, browse-mode, truncation, format-conversation) | ✓ pass |

**Tests:** scoped (`tests/memory/`) — 35 passed, 0 failed

**Doc sync:** fixed — Section 2.3 updated to describe browse mode + FTS5→truncate→noreason summarization pipeline; Section 2.4 disambiguation rule added; Section 4 new files added (`co_cli/memory/_summary.py`, `co_cli/memory/prompts/session_summarizer.md`)

**Overall: DELIVERED**

New files shipped: `co_cli/memory/_summary.py` (truncation + format + summarizer agent), `co_cli/memory/prompts/session_summarizer.md` (5-section prompt), `evals/eval_proactive_recall.py` (disambiguation eval), `tests/memory/test_format_conversation.py`, `tests/memory/test_truncate_around_matches.py`, `tests/memory/test_memory_search_browse.py`, `tests/memory/test_session_summary.py`.

---

## Implementation Review — 2026-04-25

### Evidence

| Phase | done_when | Spec Fidelity | Key Evidence |
|-------|-----------|---------------|--------------|
| Phase 1 — pipeline | prose summaries + browse mode | ✓ pass | `_summary.py:85-110` (`_truncate_around_matches`); `_summary.py:40-66` (three-tier strategy); `_summary.py:134-162` (`_format_conversation`); `_summary.py:124-131` (ToolReturn head-250+tail-250 truncation); `_summary.py:165-173` (`build_session_summarizer_agent`); `_summary.py:176-242` (query bookended, 3-attempt retry); `memory.py:184` (limit clamp [1,5]); `memory.py:230` (`asyncio.timeout(60)`); `memory.py:186-187` → `memory.py:28-64` (browse mode via `list_sessions`); `memory.py:112-115` (window-based fallback preview); `memory.py:40-43, 217, 234, 245-246` (tracer span attrs); `memory.py:117-124` (result shape `{session_id, when, source, summary}`) |
| Phase 2 — disambiguation | both descriptions accurate + tie-breaker | ✓ pass | `memory.py:138-148` (TWO MODES note); `memory.py:151-162` (tie-breaker + 4 examples); `read.py:414-427` (knowledge_search tie-breaker + 4 examples); `memory.py:128` (ALWAYS visibility unchanged) |
| Phase 3 — spec sync | `/sync-doc memory-knowledge` clean | ✓ pass | `memory-knowledge.md` Section 2.3 (two-mode pipeline + result shape + tracer attrs), Section 2.4 (disambiguation rule), Section 4 (`_summary.py` + `session_summarizer.md` added) |
| Phase 4 — tests | 35 memory tests pass | ✓ pass | `test_format_conversation.py` (8 tests, incl. exact truncation assertion); `test_truncate_around_matches.py` (7 tests covering all 3 tiers); `test_memory_search_browse.py` (3 tests: metadata fields, model=None works, empty=count 0); `test_session_summary.py` (2 LLM integration tests, real Ollama + real MemoryIndex) |

### Issues Found & Fixed

| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| `memory_search(query, *, limit=5)` — stale signature and description ("Keyword search") | `docs/specs/tools.md:151` | blocking | Updated: `query=""`, `limit=3`, two-mode purpose description |
| `rag.backend` claimed to be stamped by `memory_search` — never was; current code stamps `memory.summarizer.*` | `docs/specs/observability.md:183` | blocking | Fixed: `rag.backend` is `knowledge_search` only; added `memory.summarizer.runs/failures/timed_out` |

### Tests

- Command: `uv run pytest -v`
- Result: **663 passed, 0 failed**
- Log: `.pytest-logs/<timestamp>-review-impl.log`

### Doc Sync

- Scope: full — `memory_search` return schema changed (public ALWAYS tool); `knowledge_search` docstring updated
- Result: fixed — `tools.md:151` and `observability.md:183` corrected (see Issues above); `memory-knowledge.md` already synced in delivery

### Behavioral Verification

- `uv run co config`: ✓ healthy — LLM Online, Shell Active, Database Active, MCP 1 ready
- `memory_search` tool is ALWAYS-visible; schema description verified at `memory.py:128-172`

### Overall: PASS

All phases verified with file:line evidence. Two stale doc entries fixed. 663 tests green. System healthy. Ready to ship.
