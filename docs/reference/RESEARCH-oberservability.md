# RESEARCH: Observability for Co

**Date:** 2026-03-11

## Verdict

`co` should not adopt "observability" as a new standalone subsystem. It should converge on one canonical execution record and expose better operator-facing views on top of it.

Best-practice convergence is:

- traces are the canonical execution spine
- task and subagent progress is emitted as structured events, not free-form logs
- live views are derived from persisted state, not ephemeral console output
- bounded execution is part of observability, because runaway loops are invisible systems
- protocol standardization matters, but only after local execution semantics are clear

For `co`, that means:

1. Keep OpenTelemetry + local SQLite as the source of truth.
2. Add a first-class task/subagent event model correlated to traces.
3. Add delegation bounds (`spawn_depth`, cycle detection, run budget).
4. Improve live and resumable UX from persisted data.
5. Defer ACP adoption until `co` has a stable internal event contract.

The wrong move would be to bolt on ACP or SSE first and call the problem solved. That would standardize transport before `co` has standardized what it needs to say.

---

## 1. Current Co Baseline

`co` already has a stronger observability base than the earlier gap note implied.

Today the system already provides:

- OpenTelemetry instrumentation bootstrapped in [`co_cli/main.py`](../../co_cli/main.py)
- local SQLite span export in [`co_cli/_telemetry.py`](../../co_cli/_telemetry.py)
- live terminal trace viewing via `co tail`
- nested trace inspection via `co traces`
- SQL/debug inspection via `co logs`
- background task lifecycle persistence in [`co_cli/_background.py`](../../co_cli/_background.py)
- task-to-span correlation hooks in [`co_cli/tools/task_control.py`](../../co_cli/tools/task_control.py)

This matters because the adoption question is not "how do we add observability?" It is "how do we converge the current tracing, task state, and future delegation flows into one coherent operator model?"

So the baseline assessment is:

- `co` is not missing telemetry
- `co` is missing a converged execution event model across chat turns, background work, and delegated work
- `co` is also missing some control-plane limits that make long-running execution observable and debuggable in practice

---

## 2. Reference Signals That Matter

This review draws from current local repository context, especially:

- [`docs/DESIGN-logging-and-tracking.md`](../DESIGN-logging-and-tracking.md)
- [`docs/reference/RESEARCH-peer-systems.md`](RESEARCH-peer-systems.md)
- [`docs/reference/RESEARCH-cron-scheduler.md`](RESEARCH-cron-scheduler.md)
- [`docs/reference/ROADMAP-co-evolution.md`](ROADMAP-co-evolution.md)
- [`docs/reference/RESEARCH-voice.md`](RESEARCH-voice.md)

The main convergences across peer systems and `co`'s own design direction are:

### 2.1 One durable execution record beats many ad hoc logs

`co` already follows the right direction here with OTel spans written locally to SQLite. The same pattern shows up elsewhere in different forms: durable run logs, persisted task state, and resumable status views all beat ephemeral stdout.

### 2.2 Events matter as much as spans

Spans are good for timing and hierarchy. They are not sufficient for operator-facing progress semantics like:

- queued
- waiting for approval
- resumed
- delegated
- child agent started
- child agent completed
- retrying
- blocked on dependency

The converged pattern is: keep spans for timing; add structured domain events for workflow state.

### 2.3 Live streaming is useful, but persistence matters more

SSE and similar streaming transports are useful for real-time UX, but they are presentation channels, not the canonical record. Best practice is persisted state first, stream second.

### 2.4 Boundedness is part of observability

OpenClaw's spawn limits matter for the same reason task queues and cron crash markers matter: systems that can recurse forever are hard to inspect because they never stabilize into understandable states.

### 2.5 Protocols are stabilizing, but they are not the first milestone

ACP is a real signal. It may become important for inter-agent interoperability. But for `co`, internal clarity comes first:

- stable event taxonomy
- correlation IDs
- delegation boundary semantics
- task lifecycle semantics

Without those, ACP would just serialize an unstable local model.

---

## 3. What Best Practice Actually Converges On

The relevant best practice for `co` is not "buy a SaaS observability tool" and not "emit more logs". It is the following stack.

### 3.1 Canonical trace spine

Every meaningful execution path should map to one correlated trace tree:

- root user turn
- model calls
- tool calls
- background task launch
- background task completion
- delegated/subagent runs
- approval waits
- retries and cancellations

`co` already has the foundations for this and should keep building on OTel.

### 3.2 Structured workflow event layer

Add an explicit event schema for operator semantics that do not fit neatly into span timing alone.

Recommended event families:

- `turn.*`
- `tool.*`
- `task.*`
- `approval.*`
- `delegation.*`
- `memory.*` only for major lifecycle events, not every internal detail

Recommended minimum fields on every event:

- `event_id`
- `ts`
- `trace_id`
- `span_id`
- `session_id`
- `run_id`
- `kind`
- `status`
- `actor`
- `summary`
- `data` JSON payload

This should stay local-first and SQLite-backed.

### 3.3 Resumable operator views

Best practice is not merely "show me what is happening now." It is:

- show what happened
- show what is happening
- show what is blocked
- show what can be resumed

For `co`, that implies:

- `co tail` remains the low-level live trace view
- add a higher-level task/delegation event view
- add resumable status summaries for long-running and delegated work

### 3.4 Correlated task persistence

`TaskStorage` already persists task metadata and output. The next step is to correlate task metadata and task event history directly to trace IDs and span IDs, so the operator can pivot between:

- the human task view
- the raw output log
- the trace tree

This is more valuable than adding more logging verbosity.

### 3.5 Explicit execution limits

Observability and control converge here. Add:

- max delegation depth
- cycle detection on repeated delegation patterns
- max child-agent count per root run
- max retry count per bounded workflow
- explicit blocked/waiting states

If these are missing, the system generates noise instead of understandable execution records.

---

## 4. Recommendation for Co

### 4.1 Keep OTel as the canonical spine

This should be a hard decision.

Do not introduce a second primary observability substrate for:

- background tasks
- delegated runs
- voice
- future schedulers

Everything should correlate back to the existing local OTel trace store.

This is already consistent with the voice research direction: new capabilities should land in existing OTel tracing, not in a parallel log path.

### 4.2 Add a first-class `execution_events` table

Recommended purpose:

- domain-level status transitions and operator-readable progress
- durable feed for live and historical views
- join point between traces, tasks, approvals, and delegation
- unification with scheduled jobs (ensure `run_id` and `task_id` align semantically with the `TaskScheduler`'s `schedule_runs` table to prevent state bifurcation)

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

CREATE INDEX idx_execution_events_ts ON execution_events(ts DESC);
CREATE INDEX idx_execution_events_trace ON execution_events(trace_id);
CREATE INDEX idx_execution_events_run ON execution_events(run_id);
CREATE INDEX idx_execution_events_task ON execution_events(task_id);
```

Use it for semantic events like:

- `task.queued`
- `task.started`
- `task.output_ready`
- `task.completed`
- `approval.requested`
- `approval.granted`
- `approval.denied`
- `delegation.started`
- `delegation.depth_limit_hit`
- `delegation.cycle_detected`
- `delegation.completed`

### 4.3 Treat delegation as an observable workflow, not a text exchange

The old framing focused on "subagent logging." That is too narrow.

What `co` actually needs is:

- delegation run IDs
- parent/child correlation
- explicit state transitions
- depth accounting
- cycle detection
- completion/result summaries

The user does not primarily need to see "thought processes". The user needs to see:

- what child work was started
- why it was started
- what it is doing now
- whether it is blocked
- what it returned
- whether a safety limit stopped it

That is an operator observability problem, not a chain-of-thought streaming problem.

### 4.4 Improve live UX from persisted state

Recommended adoption order:

1. Persist richer events.
2. Extend `co tail` or add `co tasks --follow` / `co events`.
3. Add filtered views by `trace_id`, `task_id`, `run_id`.
4. Add resumable summaries for active workflows.

This preserves the local-first, inspectable design already present in `co`.

### 4.5 Defer ACP to Phase 2

ACP should not be the first deliverable.

Reason:

- `co` is still defining its bounded delegation model
- `co` does not yet have a mature internal event contract
- adopting a wire protocol too early adds compatibility burden before the product semantics settle

The right sequencing is:

1. define internal event taxonomy
2. define run/delegation IDs and lifecycle rules
3. ship local UX on those semantics
4. then evaluate mapping them onto ACP

Inference from peer signals: ACP is likely worth investigating, but only after `co` has something stable to expose.

---

## 5. What Co Should Explicitly Not Copy

### 5.1 Do not build a second observability plane for subagents only

If subagents emit one style of event and everything else lives in spans/tasks/logs, the operator model fragments.

### 5.2 Do not make SSE the source of truth

SSE is a transport for live updates. It is not the durable record.

### 5.3 Do not expose private reasoning as the primary observability goal

The right focus is execution transparency:

- actions
- state transitions
- tool usage
- waits
- retries
- failures
- results

Not raw hidden reasoning.

### 5.4 Do not over-adopt distributed-systems patterns before they are needed

`co` is still a local CLI product. It does not need:

- a full remote telemetry backend
- a gateway event bus
- multi-tenant observability design
- protocol complexity for its own sake

### 5.5 Do not keep delegation unbounded

This is both a safety problem and an observability problem. Unbounded delegation produces traces, but not understandable ones.

---

## 6. Concrete Adoption Plan

### Phase 1: Converge local execution visibility

Goal: make one root run inspectable end-to-end.

Ship:

- `run_id` and parent/child run correlation (aligning IDs with `TaskScheduler`)
- `execution_events` table
- task event emission
- approval event emission
- delegation event emission
- `spawn_depth` limit
- cycle detection for repeated delegation chains

Success condition:

- any long-running or delegated workflow can be reconstructed from persisted traces + events without reading arbitrary console logs

### Phase 2: Operator-grade UX

Goal: make observability useful without SQL.

Ship:

- filtered event views
- combined task + trace summaries
- blocked/waiting/resumable status surfaces
- better live progress for background work and delegated work

Success condition:

- the user can answer "what is co doing?" and "why did it stop?" from built-in CLI views

### Phase 3: External protocol compatibility

Goal: standardize only after semantics stabilize.

Ship:

- an internal-to-ACP mapping layer if still justified
- optional streaming transport for external viewers

Success condition:

- `co` can expose stable execution events externally without redefining its internal model

---

## 7. Final Recommendation

The converged best practice for observability in `co` is:

- one canonical local trace spine
- one structured event model for workflow semantics
- one operator-focused UX built from persisted state
- hard execution bounds for delegation and retries
- protocol adoption only after internal semantics stabilize

So the rewrite of the earlier gap is:

- the main gap is not "no observability"
- the main gap is "no converged execution event model across traces, tasks, approvals, and delegation"

That is the right target for `co` adoption because it matches the product's actual strengths:

- local-first
- inspectable
- operator-controlled
- bounded autonomy

And it avoids the common failure mode of agent systems: adding more streaming, more logging, and more protocol surface without making the system easier to understand.
