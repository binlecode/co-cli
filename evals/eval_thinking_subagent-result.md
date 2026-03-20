# Eval Result: Thinking Subagent (delegate_think)
Date: 2026-03-19
Model: `qwen3.5:35b-a3b-think` via Ollama (localhost:11434)
Eval: `evals/eval_thinking_subagent.py`

## API Parameters
- Provider: `ollama` (OpenAI-compatible endpoint)
- Temperature: per model quirks (thinking model default)
- max_request_limit: 15 per turn
- Per-case timeout: 180s

## Pass/Fail Gates

| Gate | Threshold | Result | Status |
|------|-----------|--------|--------|
| tool_call_rate | 1.00 | 1.00 | PASS |
| plan_nonempty | 1.00 | 1.00 | PASS |
| steps_sufficient (≥2) | 1.00 | 1.00 | PASS |
| conclusion_nonempty | 1.00 | 1.00 | PASS |
| final_text_rate | 1.00 | 1.00 | PASS |

**Overall: PASS — all gates at 1.00**

---

## Per-Case Results

| Case | Steps | Elapsed | delegate_think called | Final text | Status |
|------|-------|---------|----------------------|------------|--------|
| rca-ci-timeout | 8 | 103.8s | yes | yes | PASS |
| architecture-tradeoff | 5 | 91.1s | yes | yes | PASS |
| context-window-degradation | 5 | 155.9s | yes | yes | PASS |

---

## Step-by-Step Subagent Behavior Observation

### Case 1 — `rca-ci-timeout`

**Prompt:** CI pipeline: `docker exec` into a healthy container times out at 30s on the second test run within the same CI job, never the first. Produce a ranked diagnosis plan.

**Turn flow:**

1. **Parent agent (qwen3.5 think) — tool selection**
   - Thinking: recognizes this as a timing/race condition requiring structured root cause analysis; decides to delegate rather than answer directly
   - Tool call: `delegate_think(problem="**CI Pipeline Docker Exec Timeout on Second Run...**", max_requests=5)`
   - Note: parent agent enriched the problem with formatting before delegating

2. **Sub-agent (qwen3.5 think) — reasoning pass**
   - Receives isolated `CoDeps` via `make_subagent_deps`; no tools registered
   - Produces `ThinkingResult` with 8 ordered steps:
     1. Verify Docker daemon resource limits (file descriptors, connections, socket pool) between first and second exec calls
     2. Check container-specific resource state (CPU throttling, memory pressure, file locks) after first docker exec completes
     3. Investigate Docker socket connection state and process counts on CI runner between test runs
     4. Capture detailed docker daemon logs and container exec state at time of failure
     5. Check for stale PID files, lock files, or network namespace issues specific to second execution
     6. Compare Docker CLI environment variables and timeout configurations between runs
     7. Test isolation: run two consecutive docker exec commands manually on CI runner to reproduce
     8. Review CI infrastructure resources (memory, CPU) for exhaustion after first test suite completion
   - Plan: *"Analyze the CI pipeline issue by identifying resource contention, Docker daemon state changes, and environment-specific factors that could cause timeout on second execution. Structure a diagnostic approach starting from most likely causes (Docker resource exhaustion, socket connection limits, state carryover) to less likely (environment differences)."*
   - Conclusion (ranked hypotheses):
     1. Docker daemon socket connection exhaustion — most likely
     2. Container state/resource contention — second exec hits throttling or IPC limits not cleared
     3. CI runner resource exhaustion — memory/CPU/IO after first test suite
     4. Docker exec session leak — previous exec not fully cleaned up

3. **Parent agent — final synthesis**
   - Receives `ThinkingResult` via `ToolReturnPart`
   - Produces structured markdown response with ranked hypotheses table and immediate diagnostic actions
   - Correct root cause prioritization: socket exhaustion > resource contention > CI exhaustion > session leak

**Observations:**
- Sub-agent correctly identified the "second run only" constraint as the key discriminator — eliminates config and code issues, points to stateful Docker daemon behavior
- 8 steps is appropriate depth for an RCA; no padding or circular reasoning
- Parent agent synthesis added value: reformatted ThinkingResult into an actionable table with immediate next steps not in the sub-agent output

---

### Case 2 — `architecture-tradeoff`

**Prompt:** co-cli stores knowledge as flat markdown + FTS5/SQLite. Should it migrate to a vector DB? What does each do well, and when is migration worth the cost?

**Turn flow:**

1. **Parent agent — tool selection**
   - Thinking: recognizes multi-dimensional tradeoff requiring systematic decomposition
   - Tool call: `delegate_think(problem="Analyze the tradeoffs between FTS5 full-text search in SQLite and vector database semantic search...")`
   - Note: problem statement was condensed from the original prompt

2. **Sub-agent — reasoning pass**
   - Produces `ThinkingResult` with 5 steps:
     1. Identify FTS5's unique strengths that vector search cannot replicate (precision keyword matching, query complexity, low overhead)
     2. Identify vector search's unique strengths FTS5 cannot provide (semantic understanding, fuzzy matching, concept-based queries)
     3. Map usage patterns and constraints to determine when migration costs are justified
     4. Synthesize recommendations with concrete scenarios for each approach
     5. Provide a decision framework for ongoing operation
   - Plan: *"Analyze FTS5 and vector search capabilities across the specified dimensions, then map usage scenarios to each approach's strengths to provide concrete migration recommendations."*
   - Conclusion: quantified thresholds — stay FTS5 under 50k docs; consider vector at 100k+; explicit vocabulary-mismatch trigger; hybrid as recommended middle path

3. **Parent agent — final synthesis**
   - Produced a markdown table comparing FTS5 vs vector across 6 capability dimensions
   - Added concrete size thresholds (50k/100k documents) not explicitly requested
   - Recommended hybrid approach: FTS5 primary + optional vector index for semantic queries

**Observations:**
- Sub-agent correctly framed the question as "when does migration ROI justify cost" rather than "which is better" — appropriate framing for an operational tradeoff
- 5 steps is concise and well-structured; no redundancy between steps
- Conclusion included concrete numeric thresholds (50k/100k docs, 50ms latency) which make the recommendation actionable
- Parent agent synthesis was additive: the markdown table format and explicit hybrid recommendation went beyond the ThinkingResult content

---

### Case 3 — `context-window-degradation`

**Prompt:** After ~40 turns, co-cli responses become slower, less accurate, repetitive, miss earlier context, and call wrong tools. Model unchanged. Identify root causes and propose remediation ordered by effort.

**Turn flow:**

1. **Parent agent — tool selection**
   - Thinking: multi-symptom system diagnosis requiring structured analysis
   - Tool call: `delegate_think(problem="A user reports that after approximately 40 turns...")`

2. **Sub-agent — reasoning pass** (longest case at ~100s)
   - Produces `ThinkingResult` with 5 deeply nested steps (each step contains multi-part sub-analysis):

     **Step 1 — Root cause probability mapping:**
     - Context Window Saturation (~80%): token accumulation toward model max, attention matrix growth, "lost in the middle" phenomenon, tool parameter noise
     - State Management Corruption (~70%): tool call history not deduplicated, conversation state objects not reset
     - Context Window Degradation (~60%): attention dilution before hard limit
     - Latency/Caching Issues (~30%): connection pooling memory leaks

     **Step 2 — Remediation by effort tier:**
     - Low effort (1–4h): token count tracking, auto-summarization at 70% context, per-turn state reset, session monitoring
     - Medium effort (1–3 days): context compression via secondary LLM every 10 turns, tool state isolation, registry validation
     - High effort (1–2 weeks): rolling window architecture, session checkpointing, context quality scoring, session timeout at 40 turns

     **Step 3 — Priority rationale:** token tracking + summarization highest ROI; state reset medium effort high impact

     **Step 4 — Verification strategy:** 50+ turn test sessions, token growth rate monitoring, accuracy metrics, latency per turn

     **Step 5 — Expected outcome:** latency stabilizes, accuracy >90%, repetition decreases, tool calls remain accurate

   - Conclusion: *"Context window saturation combined with state corruption over 40+ turns. Immediate fix: token tracking with summarization at 70% capacity + per-turn state resets. Long-term: rolling window architecture with session checkpointing."*

3. **Parent agent — final synthesis**
   - Produced root cause table with likelihood percentages
   - Organized remediation by effort tier, matching the ThinkingResult structure

**Observations:**
- Most complex case; sub-agent steps were substantially longer (embedded multi-point analysis within each step)
- Probability estimates (80%/70%/60%/30%) show calibrated uncertainty rather than false precision
- Remediation tiers correctly ordered: quick wins first, architectural changes last
- Verification strategy is concrete and testable — not generic advice
- 155.9s elapsed: the nested step structure required more token generation than the other cases

---

## Drift/Error Tracing

No failures in this run. No drift observed.

**Potential failure modes identified during analysis:**

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Model answers directly without calling `delegate_think` | Medium — smaller/faster models may skip delegation for "easy" questions | Prompt explicitly instructs tool use; gate enforces 1.00 tool_call_rate |
| Sub-agent returns empty `steps` list | Low — structured output enforces list type but not length | Gate enforces `steps_sufficient >= 2` |
| `ToolReturnPart.content` is string not dict | Low — pydantic-ai version-dependent | Extraction uses `isinstance(part, ToolReturnPart)` with dict check |
| Per-case timeout hit | Medium for complex cases | 155.9s observed for case 3; 180s limit gives ~15% headroom |

---

## Timeout Analysis

**Why 120s was insufficient → 180s chosen:**

A single `qwen3.5:35b-a3b-instruct` call on local Ollama takes **~115s** (measured: `eval_ollama_native.py::test_openai_compatible_summarization_pipeline_returns_summary` = 114.65s). The thinking model is heavier.

Each eval turn makes **2–3 sequential LLM calls**:
1. Parent agent thinking pass + tool selection: ~60–90s
2. Sub-agent (thinking model) full reasoning: ~60–90s
3. Parent agent final response synthesis: ~30–60s

Total realistic range: **150–240s per case**. 120s covers at most one LLM call. 180s covers typical cases (cases 1–2: 91–104s) but is tight for complex nested reasoning (case 3: 155.9s).

**Root cause:** not slow inference — the model runs at **~45 tok/s** (measured: 44.9 tok/s on a 500-token generation). The long elapsed time is **thinking token volume**. `qwen3.5:35b-a3b-think` is a native thinking model that generates internal chain-of-thought tokens before producing visible output. For a complex problem this can be 2000–4000 thinking tokens per call, invisible in the final response but fully generated. At 45 tok/s, 3500 tokens/call × 3 calls = ~233s of generation. Not a code issue. Timeout must account for thinking token budget, not just output length.

---

## Summary

`delegate_think` is functioning correctly end-to-end:
- Parent agent consistently routes reasoning tasks to the sub-agent
- Sub-agent produces well-structured `ThinkingResult` with non-trivial plans, ≥5 steps, and substantive conclusions
- `ToolReturnPart` extraction is reliable via `isinstance(part, ToolReturnPart)` check
- Parent agent synthesis adds value beyond the raw ThinkingResult (formatting, tables, additional detail)
- `model_registry` must be passed explicitly to `make_eval_deps` for delegation evals — added to `evals/_deps.py`
