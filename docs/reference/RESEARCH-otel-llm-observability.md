# RESEARCH: OTel LLM Observability Best Practices
_Date: 2026-03-20_

This document captures best practices for LLM Observability using OpenTelemetry (OTel), based on the FreeCodeCamp article ["How to Build End-to-End LLM Observability in FastAPI with OpenTelemetry"](https://www.freecodecamp.org/news/build-end-to-end-llm-observability-in-fastapi-with-opentelemetry/) and cross-referenced with peer reference systems and current `co-cli` implementation.

The goal is to identify gaps in `co`'s current telemetry and propose concrete, code-first adoptions to improve observability without relying on vendor-specific opaque SDKs.

---

## 1. Converged Best Practices from Literature

The article advocates for a **code-first, explicit span design** over black-box agents. The core principles are:

1. **One Trace Per Request**: A single end-to-end trace per user interaction, serving as the root for all subsequent logical LLM operations.
2. **Semantic Span Taxonomy**: Instead of monolithic spans, use granular logical stages:
   - `http.request` (or `cli.command` / `co.turn`)
   - `rag.retrieval`
   - `llm.call`
   - `llm.postprocess`
   - `llm.eval` (optional)
3. **Privacy-Preserving Prompt Tracing**: Avoid dumping raw prompts and responses directly into traces if privacy is a concern. Instead, use `llm.prompt_hash` and `llm.response_hash` combined with length metrics (`llm.prompt_length`).
4. **Explicit Token & Cost Tracking**: Attach `llm.usage.*` tokens and, crucially, derived `llm.cost_estimated_usd` directly to the span. Runaway costs are a major operational blindspot.
5. **RAG-Specific Attributes**: The retrieval span should explicitly record constraints and outcomes, e.g., `rag.top_k`, `rag.similarity_threshold`, and `rag.documents_returned`.
6. **Evaluation Signals**: Attach quality/evaluation signals (`llm.eval.passed`, `llm.eval.hallucination_detected`) directly to the trace to correlate quality drops with specific prompt or retrieval changes.

---

## 2. Current State of `co-cli` Observability

`co-cli` has a robust foundational OTel setup but leans heavily on auto-instrumentation for the agent layer:

- **Infrastructure**: Uses a local SQLite database (`co-cli.db`) with a custom `SQLiteSpanExporter` supporting WAL mode concurrency.
- **LLM Tracing**: Achieved entirely via `pydantic-ai`'s `Agent.instrument_all(version=3)`. This gives spec-compliant GenAI semantic attributes (`gen_ai.system`, `gen_ai.usage.input_tokens`, etc.) automatically.
- **Custom Spans**: `co-cli` explicitly instruments specific infrastructure/memory bottlenecks: `co.memory.save`, `co.memory.update`, `sync_knowledge`, `restore_session`, `ctx_overflow_check`, and `background_task_execute`.

### Identified Gaps

1. **Missing Root "Turn" Span**: 
   - *Current*: Traces start when `Agent.run` is called by `pydantic-ai`, or disjointedly for `bootstrap` tasks.
   - *Gap*: There is no overarching `co.turn` or `co.interaction` span wrapping `run_turn` or `run_turn_with_fallback` in `_orchestrate.py`. This means pre-agent retrieval, post-processing, and the agent run itself are not cleanly bound under a single interaction trace.
2. **Opaque RAG/Retrieval Spans**: 
   - *Current*: Tools like `search_knowledge`, `search_memories`, `web_search`, and `search_drive_files` execute as generic `pydantic-ai` tools.
   - *Gap*: They lack explicit `rag.retrieval` attributes. We do not currently record `rag.documents_returned`, FTS vs. grep backend usage, or search latency in a dedicated retrieval span.
3. **No Cost Attribution**: 
   - *Current*: Pydantic-AI captures `input_tokens` and `output_tokens` via the `gen_ai` specification.
   - *Gap*: `co-cli` does not calculate or attach an estimated USD cost to the spans, making it hard to audit the cost of specific workflows or sub-agent delegations.
4. **Privacy vs. Debuggability**: 
   - *Current*: Pydantic-AI captures full prompt and response strings in events/attributes by default.
   - *Gap*: No mechanism to hash prompts (`llm.prompt_hash`) or restrict raw logging for sensitive sessions or specific LLM providers.
5. **Lack of Evaluation Signals**: 
   - *Current*: Errors are caught, but there are no qualitative evaluations.
   - *Gap*: No span attributes capturing if a turn resulted in a user cancellation, approval, or subsequent correction.

---

## 3. Proposed Adoptions for `co-cli`

To align with converged best practices while maintaining our local-first, MVP-constrained design philosophy, we should adopt the following:

### 3.1 Overarching Turn Trace
Wrap the `run_turn` (and `run_turn_with_fallback`) function in `co_cli/context/_orchestrate.py` with an explicit root span:
```python
with _TRACER.start_as_current_span("co.turn") as turn_span:
    turn_span.set_attribute("co.turn.user_input_length", len(user_input))
    # execute agent and post-processing
```
*Why:* Unifies bootstrap, retrieval, LLM inference, and UI rendering under a single trace ID per user command.

### 3.2 Explicit RAG Span Attributes
In functions like `search_knowledge`, `search_memories`, and `web_search` (in `articles.py`, `memory.py`, and `web.py`), wrap the execution in a dedicated retrieval span:
```python
with _TRACER.start_as_current_span("rag.retrieval") as span:
    span.set_attribute("rag.backend", ctx.deps.config.knowledge_search_backend)
    span.set_attribute("rag.query_length", len(query))
    # ... perform search ...
    span.set_attribute("rag.documents_returned", len(results))
```
*Why:* Enables precise debugging of hallucination or context-miss issues by isolating retrieval performance from model inference performance.

### 3.3 Cost Tracking Attributes
Enhance the existing `ctx_overflow_check` area in `_orchestrate.py` (which already intercepts `turn_usage`) to calculate and inject estimated costs into the current trace, or create a custom span processor to enrich `gen_ai` spans with cost metrics based on the current `ResolvedModel`.
```python
span.set_attribute("llm.cost_estimated_usd", calculated_cost)
```
*Why:* Transforms observability from simple system health monitoring into an operational cost management tool.

### 3.4 PII/Hash Controls
Investigate Pydantic-AI's `InstrumentationSettings` or add an explicit span processor to optionally redact raw `gen_ai.prompt` and `gen_ai.completion` fields, replacing them with `sha256` hashes (`llm.prompt_hash`) based on user settings (`config.telemetry_privacy_mode = True`).

### 3.5 Approval / Evaluation Attributes
Since `co-cli` has an explicit approval boundary, emit events or attach attributes to the root turn span when a user approves, denies, or modifies a tool call.
```python
turn_span.set_attribute("co.eval.tool_approved", True)
```
*Why:* Uses explicit user signals (approvals) as a free, high-signal proxy for model evaluation and quality.

---

## Conclusion

`co-cli`'s current telemetry is structurally sound thanks to `SQLiteSpanExporter` and OTel conventions. However, by intentionally designing **semantic span boundaries** for turns and retrievals, and injecting **cost and evaluation attributes**, the trace data in `co logs` and `co traces` will transition from basic debugging logs into a complete observability platform for the assistant's behavior and cost.