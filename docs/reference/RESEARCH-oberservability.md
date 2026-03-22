# RESEARCH: Observability for Co

**Date:** 2026-03-20

Status: shipped-foundations

Aspect: observability and operator visibility
Pydantic-AI patterns: OTel tracing, streaming lifecycle events, durable execution visibility

## Verdict

`co` should not adopt "observability" as a new standalone subsystem. It should converge on one canonical execution record and expose better operator-facing views on top of it.

Best-practice convergence is:

- **Traces are the canonical execution spine**: One explicit trace per user request spanning retrieval, execution, and evaluation.
- **Task and subagent progress is emitted as structured events**, not free-form logs.
- **Live views are derived from persisted state**, not ephemeral console output.
- **Bounded execution is part of observability**, because runaway loops are invisible systems.
- **Protocol standardization matters**, but only after local execution semantics are clear.

For `co`, that means:

1. Keep OpenTelemetry (OTel) + local SQLite as the source of truth, adopting explicit LLM observability semantics (cost attribution, RAG tracing, and privacy bounds).
2. Add a first-class task/subagent event model correlated to traces.
3. Add delegation bounds (`spawn_depth`, cycle detection, run budget).
4. Improve live and resumable UX from persisted data.
5. Defer ACP adoption until `co` has a stable internal event contract.

The wrong move would be to bolt on ACP or SSE first and call the problem solved. That would standardize transport before `co` has standardized what it needs to say.

---

## 1. Current Co Baseline

`co` already has a stronger observability base than the earlier gap note implied.

Today the system already provides:

- OpenTelemetry instrumentation bootstrapped in `co_cli/main.py`
- local SQLite span export in `co_cli/_telemetry.py`
- live terminal trace viewing via `co tail`
- nested trace inspection via `co traces`
- SQL/debug inspection via `co logs`
- background task lifecycle persistence in `co_cli/_background.py`
- task-to-span correlation hooks in `co_cli/tools/task_control.py`

This matters because the adoption question is not "how do we add observability?" It is "how do we converge the current tracing, task state, and future delegation flows into one coherent operator model?"

So the baseline assessment is:

- `co` is not missing telemetry, but it relies heavily on auto-instrumentation (via `pydantic-ai`) which captures raw prompts but lacks **cost attribution** and explicit **privacy controls** (e.g., prompt hashing).
- `co` is missing a converged execution event model across chat turns, background work, and delegated work.
- `co` lacks explicit semantic span boundaries for pre-agent operations (like FTS5 search) and overarching interactions (a root turn span).
- `co` is missing some control-plane limits that make long-running execution observable and debuggable in practice.

---

## 2. Reference Signals That Matter

This review draws from current local repository context and external industry best practices (e.g., FreeCodeCamp OTel LLM tracking patterns). 

The main convergences across peer systems and `co`'s own design direction are:

### 2.1 One durable execution record beats many ad hoc logs

`co` already follows the right direction here with OTel spans written locally to SQLite. The same pattern shows up elsewhere in different forms: durable run logs, persisted task state, and resumable status views all beat ephemeral stdout. The industry standard explicitly advocates for a **code-first, explicit span design** over black-box vendor agents: one end-to-end trace per user interaction.

### 2.2 Events matter as much as spans

Spans are good for timing and hierarchy. They are not sufficient for operator-facing progress semantics like: queued, waiting for approval, resumed, delegated, child agent started, blocked on dependency.

The converged pattern is: keep spans for timing; add structured domain events for workflow state.

### 2.3 Live streaming is useful, but persistence matters more

SSE and similar streaming transports are useful for real-time UX, but they are presentation channels, not the canonical record. Best practice is persisted state first, stream second.

### 2.4 Boundedness is part of observability

OpenClaw's spawn limits matter for the same reason task queues and cron crash markers matter: systems that can recurse forever are hard to inspect because they never stabilize into understandable states.

### 2.5 Protocols are stabilizing, but they are not the first milestone

ACP is a real signal. It may become important for inter-agent interoperability. But for `co`, internal clarity comes first: stable event taxonomy, correlation IDs, delegation boundary semantics. Without those, ACP would just serialize an unstable local model.

---

## 3. What Best Practice Actually Converges On

The relevant best practice for `co` is not "buy a SaaS observability tool" and not "emit more logs". It is the following stack.

### 3.1 Canonical trace spine

Every meaningful execution path should map to one correlated trace tree:

- root user turn (e.g. `co.turn`)
- model calls
- tool calls
- retrieval bounds (e.g. `rag.retrieval`)
- post-processing and evaluation (e.g. `co.eval.tool_approved`)
- background task launch
- background task completion
- delegated/subagent runs
- approval waits
- retries and cancellations

This implies one end-to-end trace per user interaction, ensuring pre-agent operations (like FTS5 search) are cleanly bound under the same interaction trace. `co` already has the foundations for this and should keep building on OTel.

### 3.2 LLM-Specific Observability Semantics

Best practices for LLM tracking explicitly call for expanding spans beyond just latency:

- **Cost Tracking**: Runaway costs are a major operational blindspot. Attach `llm.usage.*` tokens and derived `llm.cost_estimated_usd` directly to the span.
- **Privacy-Preserving Prompt Tracing**: Avoid dumping raw prompts and responses directly into traces if privacy is a concern. Instead, use hashes like `llm.prompt_hash` and `llm.response_hash` combined with length metrics (`llm.prompt_length`).
- **RAG-Specific Attributes**: The retrieval span should explicitly record constraints and outcomes, e.g., `rag.top_k`, `rag.similarity_threshold`, and `rag.documents_returned`.

### 3.3 Structured workflow event layer

Add an explicit event schema for operator semantics that do not fit neatly into span timing alone.

Recommended event families:
`turn.*`, `tool.*`, `task.*`, `approval.*`, `delegation.*`, `memory.*` (major lifecycle events only).

Recommended minimum fields on every event:
`event_id`, `ts`, `trace_id`, `span_id`, `session_id`, `run_id`, `kind`, `status`, `actor`, `summary`, `data` (JSON payload).

### 3.4 Correlated task persistence

`TaskStorage` already persists task metadata and output. The next step is to correlate task metadata and task event history directly to trace IDs and span IDs, so the operator can pivot between the human task view, the raw output log, and the trace tree.

Additionally, ensure that spans tracking tasks and evaluations include semantic attributes (`llm.usage.*`, `llm.cost_estimated_usd`, `rag.documents_returned`) rather than generic metadata. This explicitly models operations like RAG retrievals or subagent executions so they are quantifiable.

### 3.5 Explicit execution limits

Observability and control converge here. Add:
- max delegation depth
- cycle detection on repeated delegation patterns
- max child-agent count per root run
- explicit blocked/waiting states

If these are missing, the system generates noise instead of understandable execution records.

---

## 4. Recommendation for Co

### 4.1 Keep OTel as the canonical spine

This should be a hard decision. Do not introduce a second primary observability substrate for background tasks, delegated runs, voice, or future schedulers. Everything should correlate back to the existing local OTel trace store.

### 4.2 Add a first-class `execution_events` table

Recommended purpose:
- domain-level status transitions and operator-readable progress
- durable feed for live and historical views
- join point between traces, tasks, approvals, and delegation
- unification with scheduled jobs (ensure `run_id` and `task_id` align semantically with the `TaskScheduler`'s `schedule_runs` table)

Suggested schema shape:

```sql
CREATE TABLE execution_events (
    event_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    trace_id TEXT,
    span_id TEXT,
    session_id TEXT,
    run_id TEXT,
    parent_run_id TEXT,
    task_id TEXT,
    kind TEXT NOT NULL,
    status TEXT,
    actor TEXT,
    summary TEXT NOT NULL,
    data TEXT NOT NULL
);
```

Use it for semantic events like:
- `task.queued`, `task.started`, `task.completed`
- `approval.requested`, `approval.granted`, `approval.denied`
- `delegation.started`, `delegation.depth_limit_hit`, `delegation.completed`
- `eval.tool_approved`, `rag.retrieval_success`

### 4.3 Treat delegation as an observable workflow, not a text exchange

The user does not primarily need to see "thought processes". The user needs to see: what child work was started, why it was started, what it is doing now, whether it is blocked, what it returned, and whether a safety limit stopped it. That is an operator observability problem, not a chain-of-thought streaming problem.

### 4.4 Improve live UX from persisted state

Recommended adoption order:
1. Persist richer events.
2. Extend `co tail` or add `co tasks --follow` / `co events`.
3. Add filtered views by `trace_id`, `task_id`, `run_id`.
4. Add resumable summaries for active workflows.

### 4.5 Defer ACP to Phase 2

ACP should not be the first deliverable.
The right sequencing is:
1. define internal event taxonomy
2. define run/delegation IDs and lifecycle rules
3. ship local UX on those semantics
4. then evaluate mapping them onto ACP

---

## 5. What Co Should Explicitly Not Copy

- **Do not build a second observability plane for subagents only**: The operator model fragments if subagents log differently than standard tasks.
- **Do not make SSE the source of truth**: SSE is a transport for live updates, not the durable record.
- **Do not expose private reasoning as the primary observability goal**: Focus on execution transparency (actions, state transitions, tool usage, failures, results), not raw hidden reasoning.
- **Do not keep delegation unbounded**: Unbounded delegation produces traces, but not understandable ones.

---

## 6. Concrete Adoption Plan

### Phase 1: Converge local execution visibility

**Goal:** Make one root run inspectable end-to-end with accurate LLM economics.

**Ship:**

- **Root Turn Spans**: Wrap `run_turn` with a `co.turn` span to track the full lifecycle of an interaction.
- **RAG Spans**: Add `rag.retrieval` spans to tools like `search_knowledge` and `search_memories`, explicitly recording `rag.documents_returned` and backend type.
- **Cost Attribution**: Inject `llm.cost_estimated_usd` into spans during the context overflow check or via a custom span processor to track operational cost.
- **Privacy Controls**: Introduce prompt hashing (`llm.prompt_hash`) based on a `telemetry_privacy_mode` configuration to avoid logging PII in `co logs`.
- **Execution Events**:
  - Add `run_id` and parent/child run correlation (aligning IDs with `TaskScheduler`)
  - Create the `execution_events` SQLite table
  - Emit structured events for task queues, approvals, and delegations
- **Control Limits**:
  - Enforce `spawn_depth` limits and cycle detection for repeated delegation chains

**Success condition:** Any long-running or delegated workflow can be reconstructed accurately from persisted traces + events without reading arbitrary console logs.

### Phase 2: Operator-grade UX

**Goal:** Make observability useful without writing SQL.

**Ship:**
- Filtered event views in the CLI.
- Combined task + trace summaries.
- Blocked/waiting/resumable status surfaces.
- Better live progress tracking for background work and delegated work.

**Success condition:** The user can answer "what is co doing?" and "why did it stop?" directly from built-in CLI views.

### Phase 3: External protocol compatibility

**Goal:** Standardize inter-agent protocol only after semantics stabilize.

**Ship:**
- An internal-to-ACP mapping layer (if still justified).
- Optional streaming transport for external viewers.

**Success condition:** `co` can expose stable execution events externally without redefining its internal model.

---

## 7. Final Recommendation

The converged best practice for observability in `co` is:

- one canonical local trace spine
- one structured event model for workflow semantics
- one operator-focused UX built from persisted state
- hard execution bounds for delegation and retries
- protocol adoption only after internal semantics stabilize

The main gap is **not "no observability"**. The main gap is **"no converged execution event model across traces, tasks, approvals, and delegation."** That is the right target for `co` adoption because it matches the product's actual strengths: local-first, inspectable, operator-controlled, and bounded autonomy.
