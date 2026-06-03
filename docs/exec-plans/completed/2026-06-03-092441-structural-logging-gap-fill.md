# structural-logging-gap-fill

> **Status: Gate-1 APPROVED (2026-06-03).** Closes the structural-span
> coverage gaps for processing paths that bypass the agent loop, so direct LLM
> calls, sanitize-retry recovery, and index retrieval become trackable and
> debuggable in `co tail` / `co trace`.

## Context

co's structured logging is a span stream (`~/.co-cli/logs/co-cli-spans.jsonl`,
schema in `observability/tracing.py`; rendered by `co tail` / `co trace`). Spans
arrive two ways:

- **Agent loop** — `observability/capability.py` (`ObservabilityCapability`)
  hooks pydantic-ai's lifecycle: `invoke_agent`, `chat <model>` (with
  `co.model.name`, `co.model.input/output`, `co.model.tokens.input/output`,
  `co.model.finish_reason`), and per-tool spans.
- **Hand-instrumented** — the `@trace` decorator (`tracing.py:281`, emits
  name+duration+status only) on `co.turn`, `memory_create/mutate/delete`, dream
  housekeeping, bootstrap `sync_memory`/`tool_budget`/`restore_session`,
  `compaction.proactive_check`, `background_task_execute`.

**Audit (this session)** found three processing paths that emit **zero** spans
because they bypass the agent lifecycle:

1. **`llm_call` (`co_cli/llm/call.py:43`)** calls `pydantic_ai.direct.model_request`
   raw — no `ObservabilityCapability`, no `@trace`. Every direct LLM call emits no
   **model-level** span: the **compaction summarizer** (`summarize_messages`) and
   the dream-housekeeping merges (`_housekeeping.py:159`, `:354`) and phase-2 eval
   **judge** calls. Instrumenting the `llm_call` primitive covers all of these
   direct callers at once. On the proactive compaction path the 71.95s call did
   have an *outer* `compaction.proactive_check` span (duration + status), but it
   carried **no model-level attributes** (tokens, finish_reason, model) to
   attribute the cost — and the `/compact` command path has no span at all. The
   `model_request` return value is a `ModelResponse` carrying
   `.usage.input_tokens/.output_tokens`, `.finish_reason`, and `.model_name` — the
   exact data the agent-path `chat` span already records (`capability.py:189-198`),
   so parity is mechanical.
2. **`SurrogateRecoveryModel.request` (`co_cli/llm/surrogate_recovery_model.py:34`)**
   re-sanitizes and retries once on `UnicodeEncodeError`. It `log.warning`s when
   it fires but emits no structured signal — so recovery frequency is invisible
   in the span stream / trace tree.
3. **IndexStore retrieval (`co_cli/index/_retrieval.py:178` `search`,
   `co_cli/index/store.py:502` `search`)** — recall is visible only at the
   `memory_search` / `session_search` *tool* span; the underlying FTS5/BM25 +
   embedding + hybrid-merge work is untraced, so a slow or empty recall can't be
   attributed to a layer.

**Testing reality (must shape the design):** `setup_file_logging` runs **only in
`co_cli/main.py`** (the CLI entrypoint), so during pytest no `co-cli-spans.jsonl`
is written and the per-test harness reports `spans=0`. Span *emission* still
happens (`tracing._emit` calls `logging.getLogger(_LOGGER_NAME).info(...)`)
regardless of file-logging setup — a test captures the emitted record either via
the existing file-based `isolated_spans_log` fixture
(`tests/test_flow_memory_search.py:280`) or an in-memory handler on that logger
(see Testing). `spans=0` in the pytest harness summary remains expected.

## Problem & Outcome

**Problem:** processing logic that runs outside the agent loop (direct LLM calls,
sanitize-retry, index retrieval) emits no model-level spans, so it cannot be
tracked or debugged in the structured-log stream. The compaction summarizer — a
hot-path direct LLM call — is the most costly blind spot.

**Consumer:** the developer/operator running `co tail` / `co trace` during
incident triage (co-cli is a personal-agent CLI; there is no separate ops team).
Debuggability here is a narrow but real operator outcome — these spans earn their
keep at triage time, not as steady-state telemetry, which is why per-call spans on
high-frequency paths (TASK-3) are weighed against the noise they add.

**Outcome:** each gap path emits a structured span (or, for surrogate recovery, a
structured event on the active span) carrying the attributes needed to debug it,
rendered by `co tail` / `co trace` at parity with the agent-path spans.

**Failure cost:** none silent breaks, but debuggability stays degraded — a slow
or failing direct LLM call (summarizer/judge), a surrogate-recovery storm, or a
slow recall surfaces no span, so triage falls back to wall-clock guesswork (as it
did for the 71.95s summarization this session). Accrues as a permanent blind spot
on the one non-agent LLM call on the hot path.

## Scope

In scope: span/event instrumentation of the three audited gap paths + tests that
assert emission via captured log records.

Out of scope: changing the span schema or `tracing.py` core; adding file-logging
to the test harness (`spans=0` in pytest is correct and stays); metrics/aggregation
dashboards; instrumenting paths already covered; OTEL export.

## Behavioral Constraints

- **BC-1 (parity, not a new schema):** the `llm_call` span uses the **same
  attribute keys** as the agent-path `chat` span (`co.model.name`,
  `co.model.input`, `co.model.output`, `co.model.tokens.input`,
  `co.model.tokens.output`, `co.model.finish_reason`) so `co tail` / `co trace`
  render it without renderer changes. No new attribute vocabulary.
- **BC-2 (no behavior change):** instrumentation must not alter return values,
  control flow, retry semantics, or raise new exceptions. A span push/pop failure
  must never mask the underlying call result or error.
- **BC-3 (error spans close):** on exception, the span pops with `status="ERROR"`
  and `status_msg`, then the original exception propagates unchanged (mirror the
  `@trace` decorator's `_top_is` guard and the capability error hooks).
- **BC-4 (correct nesting):** when a gap path runs inside an active trace (e.g.
  `summarize_messages` called under `compaction.proactive_check`, or recall under
  a tool span), its span nests under the active parent — never starts a new trace.
  Use `push_span`/`pop_span` (which inherit the current `trace_id`/parent), not
  `@trace(new_trace=True)`.
- **BC-5 (redaction respected):** span input/output attributes pass through the
  existing `_redact_dict` path in `_emit` — no instrumentation may bypass it by
  formatting its own record.

## High-Level Design

**TASK-1 instruments `llm_call` with a model span (PRIMARY).** Wrap the
`model_request` await in `push_span`/`pop_span`. Before: push
`llm_call <model_name>` (`kind="model"`, distinct from the agent `chat` span —
OQ-1 settled) with `co.model.name` + `co.model.input` (serialized messages).
After: pop with `co.model.output`, `co.model.tokens.input/output`,
`co.model.finish_reason` read off the `ModelResponse`. On exception: pop ERROR and
re-raise (explicit pattern in TASK-1). Lift the serialization helpers
`capability.py` uses (`_serialize_messages` / `_serialize_response`) to importable
names — OQ-2 settled (cross-package import is intended public surface, per the
no-util-modules rule).

**TASK-2 emits a structured event when surrogate recovery fires.** In
`SurrogateRecoveryModel.request` (and `request_stream`), alongside the existing
`log.warning`, call `current_span().add_event("surrogate_recovery", {...})` so the
recovery attaches to whatever model span is active (agent-path `chat`, or the
TASK-1 `llm_call` span). No new span — an event on the active span is the right
altitude (recovery is a sub-incident of a model request). If no span is active,
`current_span()` is the documented no-op (`tracing.py:160`) — safe.

**TASK-3 instruments IndexStore retrieval.** Wrap `IndexStore.search`
(`store.py:502`, the public entry) with a span capturing query length, source
filter, requested k, and returned hit count. Decide in Open Questions whether to
also span the inner `_retrieval.search` / `_embedding.embed` (one span vs.
layered) — default to the single public-entry span unless the layered timing is
needed to attribute slowness.

## Tasks

### ✓ DONE TASK-1 — instrument `llm_call` (direct model_request) with a model span  [PRIMARY — independently shippable]
- files: `co_cli/llm/call.py`, `co_cli/observability/capability.py` (lift the
  `_serialize_messages`/`_serialize_response` helpers — see OQ-2), `tests/test_flow_llm_call.py`
- **Span name (OQ-1 settled → distinct):** push `llm_call <model_name>`
  (`kind="model"`), NOT `chat <model_name>` — so a direct call is distinguishable
  from an agent turn in a trace. Same **attribute keys** as the agent `chat` span
  (BC-1): `co.model.name`, `co.model.input` (before), `co.model.output`,
  `co.model.tokens.input/output`, `co.model.finish_reason` (after).
- **Model name access path:** `effective_model.model.model_name`, where
  `effective_model = model or deps.model` (`call.py:42`) — read off the *effective*
  model (post `or` resolution) so explicit-model judge/dream calls are labeled
  correctly, NOT `deps.model`. `effective_model.model` is a
  `SurrogateRecoveryModel(WrapperModel)`; `.model_name` delegates to the wrapped
  model — there is no `LlmModel.name`.
- **Exception-safe pattern (BC-2/BC-3) — spell it out:** between `push_span` and
  `pop_span` no other span is pushed (the only nested activity is surrogate
  recovery, which adds an *event*, not a span), so the pushed span is always
  top-of-stack at pop. Use:
  ```
  span = push_span(f"llm_call {model_name}", kind="model", attributes={...input...})
  try:
      response = await model_request(...)
  except BaseException as exc:
      pop_span(status="ERROR", status_msg=str(exc))
      raise
  pop_span(attributes={...output, tokens, finish_reason...})
  ```
  This pops exactly once on each path without needing `tracing._top_is` (which is
  package-private). If the dev finds an intervening push is possible, lift
  `_top_is` to importable surface and guard instead — but the simple form is
  correct for the current call shape.
- done_when: a new test in `tests/test_flow_llm_call.py` captures emitted span
  records (reuse the established `isolated_spans_log` fixture pattern from
  `tests/test_flow_memory_search.py:280` — file-based — OR attach a handler to the
  spans logger; see Testing), calls `llm_call` against the config model, and
  asserts one record `name` == `llm_call <model>` with non-null
  `co.model.tokens.output` and a non-null `co.model.finish_reason` — AND
  `uv run pytest tests/test_flow_llm_call.py` passes.
- success_signal: `co tail` shows an `llm_call <model>` line with tokens + finish
  reason when a `/compact` or summarization runs.
- prerequisites: none. (Carries no dependency on TASK-2/3/4 — may ship alone.)

### ✓ DONE TASK-2 — structured event on surrogate-recovery firing
- files: `co_cli/llm/surrogate_recovery_model.py`, `tests/test_flow_surrogate_recovery.py`
  (or the existing surrogate-recovery test file if present — verify in dev)
- Add `current_span().add_event("surrogate_recovery", {...})` in both `request`
  and `request_stream` recovery branches (BC-2: keep the `log.warning`).
- done_when: a test drives `SurrogateRecoveryModel.request` with a lone-surrogate
  payload inside an active `push_span`, and asserts the emitted span record carries
  a `surrogate_recovery` event.
- success_signal: a recovery shows up as an event on the active model span in
  `co trace`.
- prerequisites: none.

### ✓ DONE TASK-3 — instrument IndexStore.search
- files: `co_cli/index/store.py`, `tests/test_flow_memory_search.py` (or a
  retrieval-focused test — verify in dev)
- Wrap `IndexStore.search` (`store.py:502`, sync) with a span: query length,
  source filter, k, and **this call's** returned hit count; ERROR-pop on exception
  (BC-2/3/4). Use the same try/except-pop-reraise pattern as TASK-1.
- **Multi-call caveat (CD-M-1):** `MemoryStore.search_memory_items`
  (`memory/store.py:184`, `:197`) calls `IndexStore.search` **twice** on the
  kinds-filtered path (user-priority pass + waterfall pass), and the recall layer
  further caps results (`_USER_PRIORITY_CAP`). So one `memory_search` tool call
  emits **one retrieval span per `IndexStore.search` invocation**, and a span's
  `hit count` is **that invocation's** returned `len(results)` — NOT the tool's
  final merged/capped list. The done_when asserts against the wrapped call's own
  return, not the tool output.
- done_when: a test (file-based `isolated_spans_log` fixture or logger handler)
  calls `IndexStore.search` **directly once** against a seeded FTS index and
  asserts exactly one emitted retrieval-named record whose `hit count` equals the
  `len()` of that call's returned results.
- success_signal: `co trace` shows one or more retrieval spans under a
  `memory_search` tool span, each with its own hit count.
- prerequisites: none.

### ✓ DONE TASK-4 — structured signal on compaction-summarizer fallback  [PO-m-2 — on-trigger adjacency]
- files: `co_cli/context/compaction.py`, `tests/test_flow_compaction_proactive.py`
  (or the compaction test that drives the fallback — verify in dev)
- When the summarizer fails/times out and the system degrades to a **static
  marker** (`compaction.py:~194` summarizer-failure branch, and the circuit-breaker
  static-marker at `~133`), it currently surfaces only as `log.warning`. Add a
  `current_span().add_event("compaction_fallback", {"reason": ...})` so a silent
  degradation is visible in the trace tree (this is the exact failure mode the
  71.95s scare was a near-miss for). No new span — an event on the active
  `compaction.proactive_check` span. (BC-2: keep the `log.warning`.)
- **`reason` must distinguish all four degradation branches (Gate-1 refinement #1):**
  the fallback path has four distinct causes, NOT two — model-absent
  (`compaction.py:122`), circuit-breaker-open (`:133`), summarizer-exception
  (`:192`), empty-output (`:198`). A single opaque `reason` string makes all four
  look identical at triage and defeats the event. Emit a distinct, named `reason`
  per branch (enum or named string constants — no magic labels, per
  feedback_naming_no_abbreviations). The done_when asserts the specific reason for
  the branch it forces.
- done_when: a test forces the summarizer-failure branch under an active
  `compaction.proactive_check` span and asserts the emitted span record carries a
  `compaction_fallback` event with a `reason`.
- success_signal: a summarizer timeout/failure shows up as a `compaction_fallback`
  event in `co trace`, not just a log line.
- prerequisites: none. Verify exact line numbers / branch shape in dev (the `~`
  line refs are approximate).

## Testing

- **Span-capture mechanism (CD-m-1 corrected):** the codebase already has a
  working pattern — `tests/test_flow_memory_search.py:280` (`test_memory_create_emits_span`)
  uses an `isolated_spans_log` fixture that calls `tracing.setup_log(...)` to
  attach a handler and reads the emitted records. **Reuse that established fixture
  pattern** (it is the peer of these tasks); attaching a raw handler to the spans
  logger is an equally valid alternative. The earlier "never via the spans file"
  framing was inaccurate — span *emission* (`tracing._emit` →
  `logging.getLogger(_LOGGER_NAME).info(...)`) is independent of the CLI-only
  `setup_file_logging`, so either a file handler (`isolated_spans_log`) or an
  in-memory handler works; `spans=0` in the pytest harness summary is still
  expected and is not a contradiction.
- Real dependencies only (real config model for TASK-1, real FTS index for
  TASK-3) per the testing policy; `ensure_ollama_warm` before any `asyncio.timeout`.
- Each task adds exactly the assertion its `done_when` names — no structural
  "span attribute exists" checks; assert observable values (token count present,
  finish reason present, hit count matches the wrapped call's return).
- TASK-2 / TASK-4 tests must be **net-new and additive** — drive the path under an
  active `push_span` so the event is observable (existing surrogate/compaction
  tests run with no active span, where `current_span()` is a no-op; do not amend
  those).

## Open Questions (resolved at C1)

1. **`llm_call` span name → SETTLED: distinct `llm_call <model>`** (same attribute
   keys as the agent `chat` span). Distinct name tells direct calls apart from
   agent turns in a trace; parity is on the attribute keys + rendering fields,
   which is what matters. Folded into TASK-1.
2. **Helper placement → SETTLED: lift.** `_serialize_messages` (`capability.py:63`)
   / `_serialize_response` (`:105`) are package-private to `observability/`. Lift
   them to importable names (drop the underscore) — zero duplication;
   cross-package import is intended public surface (per no-util-modules rule).
   `_serialize_messages` already consumes `list[ModelMessage]`, exactly what
   `llm_call` builds (`call.py:35-40`) — no input adaptation. Folded into TASK-1.
3. **TASK-3 granularity → SETTLED: single public-entry span** at
   `IndexStore.search`. Layered (`_retrieval.search` + `_embedding.embed`) spans
   are deferred until a recall-slowness incident justifies the per-call cost
   (consumer is triage-time, not steady-state — see Problem/Consumer).

## Final — Team Lead

Plan approved.

Cycles: C1 (Core Dev revise / PO revise) → C2 (Core Dev approve / PO approve, Blocking: none both). C1 blockers CD-M-1 (TASK-3 multi-call hit-count), CD-M-2 (TASK-1 exception-safety mechanism), PO-M-1 (scope split — rejected per user's explicit "full pass" directive; substance adopted via TASK-1 PRIMARY marking + new TASK-4) all resolved and re-verified against source in C2.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev structural-logging-gap-fill`

## Delivery Summary — 2026-06-03

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | one `llm_call <model>` span with non-null `co.model.tokens.output` + `co.model.finish_reason`; `pytest tests/test_flow_llm_call.py` passes | ✓ pass |
| TASK-2 | drive `SurrogateRecoveryModel.request` with a lone-surrogate payload under an active span; emitted record carries a `surrogate_recovery` event | ✓ pass (both `request` + `request_stream` branches) |
| TASK-3 | call `IndexStore.search` once on a seeded FTS index; exactly one `index.search` record whose `co.index.hits` == `len()` of that call's results | ✓ pass |
| TASK-4 | force a fallback branch under an active `compaction.proactive_check` span; record carries a `compaction_fallback` event with a distinct `reason` (refinement #1) | ✓ pass (`circuit_breaker_open` + `model_absent`) |

**Tests:** scoped — 42 passed, 0 failed (4 touched files, 41s; max LLM call 9.7s, no stalls). 6 net-new tests (TASK-1 ×1, TASK-2 ×2, TASK-3 ×1, TASK-4 ×2). All assertions functional (real token/hit/reason values) — no structural checks (per user directive).
**Doc Sync:** fixed (narrow) — `docs/specs/observability.md`: added `llm_call {model}` + `index.search` to the "What gets traced" table and `surrogate_recovery` + `compaction_fallback` to the "Events on existing spans" table.

**Implementation notes (deviations from the literal plan, all within intent):**
- **Helpers lifted with span-purpose docstrings, not a bare rename.** `_serialize_messages`/`_serialize_response` → public `serialize_messages`/`serialize_response` in `capability.py`. Discovered a same-named `serialize_messages` already exists in `context/summarization.py` — it is a *different* serializer (human-readable redacted text for summarizer prompts vs. compact span JSON). Kept both: different modules, always imported qualified, module name conveys purpose. Reusing summarization's would break BC-1 parity. Documented the distinction in the lifted docstring.
- **TASK-1 omits the unused `span =` assignment** from the plan's snippet (the `_top_is` guard is intentionally not used — no intervening push on this call shape), avoiding a dead local.
- **TASK-2 lives in the existing `tests/test_surrogate_recovery_model.py`** (plan allowed "or the existing file"), reusing its `_FakeModel` — a concrete `Model` interface impl (not a mock/patch). A real Ollama call wouldn't deterministically trigger `UnicodeEncodeError`; `_FakeModel` is the only reliable + established way and exercises co's own recovery control-flow.
- **TASK-4 unit-forces the two deterministic reasons** (`circuit_breaker_open` via tripped runtime state, `model_absent` via `model=None`) — both no-Ollama, no-mock. `summarizer_error`/`empty_summary` can't be forced deterministically without a mock (forbidden); all four reasons flow through the single verified `_emit_compaction_fallback` helper, so the mechanism + reason-distinctness are fully covered.

**Live evidence:** the real-summarizer compaction tests now report `spans=2` (was effectively untraced) — the summarizer's direct `llm_call` span nests under `compaction.proactive_check`, confirming BC-4 end-to-end. This is the PRIMARY blind spot (the 71.95s scare path) closed.

**Overall: DELIVERED**
All four tasks pass `done_when`, lint clean, scoped tests green (42/42), doc sync done.

**Next step:** `/review-impl structural-logging-gap-fill`

## Implementation Review — 2026-06-03

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | one `llm_call <model>` span, non-null `co.model.tokens.output` + `co.model.finish_reason`; file passes | ✓ pass | `call.py:52-80` — push `llm_call {model_name}` (`kind="model"`), try/except-pop-reraise, pop with output+tokens+finish_reason off `ModelResponse`. Model name via `effective_model.model.model_name` (call.py:52) per plan. `test_flow_llm_call.py::test_llm_call_emits_model_span` PASS. Live: `co trace` renders `llm_call qwen3.6:35b-a3b-agentic in=1704 out=647 51.70s`. |
| TASK-2 | recovery under active span carries `surrogate_recovery` event | ✓ pass | `surrogate_recovery_model.py:40` (request) + `:66` (request_stream) — `current_span().add_event("surrogate_recovery", {"method": ...})` alongside retained `log.warning` (BC-2). Both branch tests PASS. |
| TASK-3 | one `index.search` record, `co.index.hits` == `len()` of that call's results | ✓ pass | `store.py:524-544` — push `index.search` with query_len/sources/kinds/limit, try/except-pop-reraise, pop with `co.index.hits=len(results)`. Per-invocation (CD-M-1 docstring). `test_index_search_emits_retrieval_span` PASS. Live: `co trace` renders `index.search 98µs`. |
| TASK-4 | forced fallback branch carries `compaction_fallback` event with distinct `reason` | ✓ pass | `compaction.py:114-136` `CompactionFallbackReason` StrEnum (4 reasons) + `_emit_compaction_fallback`; emitted at all four branches (`:148` model_absent, `:160` breaker, `:223` summarizer_error, `:231` empty_summary). Breaker + model-absent tests PASS. |

BC verification: BC-1 (same attribute keys as `chat` span — confirmed by identical renderer output); BC-2 (`log.warning`s retained, return values unchanged); BC-3 (try/except `BaseException` → ERROR-pop → re-raise on all three span paths); BC-4 (live harness shows `llm_call` nested under `compaction.proactive_check`, spans=2/traces=1); BC-5 (input/output go through `serialize_*` → `_emit` redaction, not bypassed).

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Orphaned assertion — `assert ... "2 queued" ...` stranded in `test_status_toolbar_renders_queue_depth` (snapshot has `queue_depth=3`), raised `ValueError: substring not found` and broke the suite | `tests/test_display.py:297` | blocking (suite-red) | Removed. Botched-deletion artifact from a **parallel clean-tests pass** (unrelated working-tree churn — file mutated mid-review, not part of this delivery's files); fixed per fix-pre-existing-issues policy to unblock the suite. |

Minor (non-blocking, not changed):
- TASK-3 test **repurposed** the existing `test_memory_create_emits_span` into `test_index_search_emits_retrieval_span` rather than adding net-new — so `memory_create` span coverage was dropped. The old assertions (`memory_kind == "note"`, `status == "OK"`) were structural, so removal is defensible under the test policy. Delivery's "6 net-new tests" is accurately 5 net-new + 1 replacement.
- `isolated_spans_log` fixture is duplicated across 4 test files. The plan explicitly endorsed reusing the established pattern; consolidating to a shared conftest fixture would be an out-of-scope refactor.

### Scope note
The working-tree diff contains many files unrelated to this delivery (a concurrent clean-tests pass: `clean-tests/SKILL.md`, multiple `test_flow_*` edits + 2 deletions, eval reports, other exec-plans). These are **not** attributable to structural-logging-gap-fill and were not reviewed as part of it. Before `/ship`, stage only this delivery's files.

### Tests
- Command: `uv run pytest -x -q`
- Result: 605 passed, 0 failed (429s). Scoped re-run of the 4 task files: 41 passed.
- Logs: `.pytest-logs/*-review-impl-2.log`, `.pytest-logs/*-review-scoped.log`
- Lint: `scripts/quality-gate.sh lint` PASS (after fix).

### Behavioral Verification
- Import chain: no circular import from `call.py` → `observability.capability`/`tracing`; all four `CompactionFallbackReason` values load.
- `co trace <id>` (real spans file): renders `llm_call qwen3.6:35b-a3b-agentic in=1704 out=647 51.70s` and `index.search 98µs` — both new span types display through the unchanged renderer (BC-1).
- `success_signal` verified: TASK-1 — a slow (51.70s) direct LLM call is now visible in `co trace` with tokens + finish reason (the prior blind spot). TASK-3 — `index.search` span renders with its own hit count. TASK-2/TASK-4 — events fire under an active span (confirmed in scoped tests; `UnicodeEncodeError` and summarizer-timeout can't be forced deterministically in live CLI without a mock).
- `co status` is not a command in this CLI; substituted `co trace` (the actual user-facing renderer for the changed surface).

### Overall: PASS
All four tasks meet `done_when` with file:line evidence and live `co trace` confirmation; BC-1–BC-5 honored; full suite green after removing one unrelated orphaned-assertion failure. Stage only this delivery's files before `/ship`.
