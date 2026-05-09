# Plan: Compaction Flow — Bug Fixes and Anti-Pattern Cleanup

Task type: code

## Context

End-to-end trace of the compaction stack (`co_cli/context/`,
`co_cli/tools/lifecycle.py`, `co_cli/tools/tool_io.py`,
`co_cli/agent/tool_call_limit.py`, `co_cli/context/orchestrate.py`)
surfaced a set of correctness bugs and design smells in how thresholds,
runtime flags, and callbacks compose across the four budget layers (L0
admission cap, L1 emit-time spill, L2 per-request spill, L3 LLM window
compaction) plus overflow recovery and `/compact`.

The trace and runtime flag map are now part of `docs/specs/compaction.md`
§1.1–§1.5. This plan addresses the issues that the trace exposed.

The fixes split into two tiers:

- **Tier A (correctness)** — three subtle bugs that can mis-direct the
  thrash gate, mis-track L2 aggregate tokens, and leak partial runtime
  state on a mid-compaction exception. These are real and worth shipping
  together because they all touch the same `proactive_window_processor`
  / `enforce_request_size` chain.
- **Tier B (design smells / robustness)** — CQS clean-ups, observability
  symmetry, fragile invariants. Lower urgency; bundled to keep edits
  cohesive.

No spec writes are part of this plan (spec already updated). `/sync-doc`
is auto-invoked by `orchestrate-dev` and will reconcile §2 / §3 / §4
prose if any drifts after the code changes land.

### Current-state validation (inline)

- ✓ Trace verified against:
  `co_cli/agent/core.py:155-161` (history processor order)
  `co_cli/context/history_processors.py:372-467` (enforce_request_size)
  `co_cli/context/compaction.py:386-550` (proactive_window_processor)
  `co_cli/context/compaction.py:296-341` (recover_overflow_history)
  `co_cli/tools/lifecycle.py:180-199` (L0 cap), `:237-265` (MCP spill)
  `co_cli/tools/tool_io.py:66-129` (spill_if_oversized)
- ✓ Runtime flag map verified against
  `co_cli/deps.py:130-196` (CoRuntimeState dataclass)
  `co_cli/commands/clear.py:13-15` and `co_cli/commands/new.py:20-22`
- ✓ Validator invariants confirmed in
  `co_cli/config/compaction.py:65-86`

---

## Tier A — Correctness bugs

### TASK 1: Thrash counter mis-classifies reported-driven compactions

**Symptom.** `consecutive_low_yield_proactive_compactions` can
increment after a *legitimate* compaction triggered by high
provider-reported `input_tokens`. Two such "low-yield" events trip the
anti-thrash gate (default `proactive_thrash_window = 2`), suppressing
all future proactive compactions for that session until overflow
recovery, `/compact`, or `/new` resets the counter.

**Why.** `co_cli/context/compaction.py:444-546`:

```python
tokens_before_local = estimate_message_tokens(messages)         # local-only
token_count = max(tokens_before_local, reported)                # trigger uses both
...
tokens_after_local = estimate_message_tokens(result)            # local-only
savings = (tokens_before_local - tokens_after_local) / tokens_before_local
if savings < cfg.min_proactive_savings:
    runtime.consecutive_low_yield_proactive_compactions += 1
```

If `reported` is the binding contributor to `token_count`
(provider-reported tokens already account for tokenizer overhead the
char-based local estimate underweights), the trigger fires legitimately
but `tokens_before_local` may already be close to `tokens_after_local`,
producing tiny "savings" → false low-yield event.

**Fix.** Compute savings using the same metric used to gate. Smallest
correct change: compare `max(local, reported)` on both sides; on the
"after" side, `reported` is stale (the post-compaction history hasn't
been to the model yet), so substitute the local estimate of the result
list — same value `_mark_compaction_applied` writes into
`post_compaction_token_estimate`.

**Concrete impl** — `co_cli/context/compaction.py`:

```python
# inside proactive_window_processor, after result is computed
tokens_after_local = estimate_message_tokens(result)
tokens_before_effective = token_count                  # already max(local, reported)
tokens_after_effective = tokens_after_local            # provider hasn't seen result yet
savings = (
    (tokens_before_effective - tokens_after_effective) / tokens_before_effective
    if tokens_before_effective > 0
    else 0.0
)
```

Replace the existing `savings = ... tokens_before_local ...` block. The
OTEL attributes `compaction.tokens_after` and `compaction.savings_pct`
should reflect the same effective metric for trace coherence.

**Files**: `co_cli/context/compaction.py`
**Tests** (`tests/test_flow_compaction_proactive.py`): add a behavioral
test where `latest_response_input_tokens` returns a high reported value
but local estimate is small; assert the thrash counter does NOT
increment after a single proactive compaction. Pin `compaction.fired ==
True` and `consecutive_low_yield_proactive_compactions == 0`.

---

### TASK 2: `enforce_request_size` aggregate tracking mixes metrics ✓ DONE

**Symptom.** L2 spill loop may stop too early (under-spilling, leaving
the next HTTP at risk of overflow) or too late (over-spilling to disk
with extra writes). Currently masked because L3 catches under-spill
fall-through and disk-write cost is small, but the `aggregate <=
threshold` exit test is misleading.

**Why.** `co_cli/context/history_processors.py:332-369`:

```python
def _spill_largest_first(spillable, *, starting_tokens, threshold, tool_results_dir):
    aggregate = starting_tokens                    # = max(local, reported)
    ...
    for part in sorted(spillable, key=lambda p: len(p.content), reverse=True):
        if aggregate <= threshold:
            break
        ...
        aggregate -= (len(old_content) - len(new_content)) // CHARS_PER_TOKEN
        # ↑ delta is local-char-based, applied to a max(local, reported) baseline
```

If `reported` is the dominant contributor, subtracting a local-char
delta produces an `aggregate` that no longer corresponds to either the
local estimate or the provider count. The exit condition is approximate
in a way the docstring does not acknowledge.

**Fix.** Track the aggregate in a single consistent space. Local-char
is the only signal we can recompute on a candidate-by-candidate basis,
so:

1. Compute `local_total = estimate_message_tokens(messages)` once.
2. Use `max(local_total, reported)` only as the *trigger*, not as the
   loop accumulator.
3. Track `local_aggregate = local_total` and check
   `local_aggregate <= threshold` inside the loop. The trigger is
   pessimistic (`max`), so spilling until `local_aggregate <= threshold`
   gives at least as much spill as the old loop, and the loop exit is
   self-consistent.

**Concrete impl** — `co_cli/context/history_processors.py`:

```python
def enforce_request_size(ctx, messages):
    deps = ctx.deps
    threshold = deps.spill_threshold_tokens
    with _TRACER.start_as_current_span("tool_budget.enforce_request_size") as span:
        span.set_attribute("budget.context_window_tokens", deps.model_max_ctx)
        span.set_attribute("request.threshold_tokens", threshold)
        local_total = estimate_message_tokens(messages)
        reported_total = latest_response_input_tokens(messages)
        trigger = max(local_total, reported_total)
        span.set_attribute("request.tokens_before", trigger)
        span.set_attribute("request.local_tokens", local_total)
        span.set_attribute("request.reported_tokens", reported_total)
        ...
        if trigger <= threshold:
            _emit_terminal("below_threshold", tokens_after=trigger)
            return messages
        ...
        spilled_by_id, local_after, spilled_count, spill_errors = _spill_largest_first(
            spillable,
            starting_tokens=local_total,        # local-only space for the loop
            threshold=threshold,
            tool_results_dir=deps.tool_results_dir,
        )
        # Effective post-spill estimate stays max-shaped: reported didn't change
        # because the tool returns getting spilled were already in the ModelResponses
        # the provider previously echoed back; conservatively keep reported as-is.
        effective_after = max(local_after, reported_total)
        ...
        _emit_terminal(
            "" if effective_after <= threshold else "fallback_to_summarize",
            tokens_after=effective_after,
            spilled=spilled_count,
            errors=spill_errors,
        )
```

Add `request.local_tokens` and `request.reported_tokens` span
attributes so tracers can see both numbers.

**Files**: `co_cli/context/history_processors.py`
**Tests** (`tests/test_flow_enforce_request_size.py`): add a case where
`latest_response_input_tokens` is stubbed high but local content is
modest; assert spill fires at least as many times as the local-driven
case and the span emits both attributes. Pin
`skip_reason == "fallback_to_summarize"` when neither dimension fits.

---

### TASK 3: Proactive's `except Exception` leaks partial runtime state ✓ DONE

**Symptom.** If any code path in `proactive_window_processor` AFTER
`apply_compaction` raises (e.g., savings computation hits a
ZeroDivisionError on a degenerate empty-input edge, or
`estimate_message_tokens(result)` raises on malformed content), the
runtime state has already been mutated by `_mark_compaction_applied`
but the function returns the *original* `messages` list. Next processor
pass sees `compaction_applied_this_turn = True` → `reported = 0` →
unfiltered fast-path on a still-oversized history. `_finalize_turn`
then writes `history_compacted=True` and overwrites the on-disk
transcript with the unchanged history — a behavior the persistence
contract does not describe.

**Why.** `co_cli/context/compaction.py:404-550`:

```python
async def proactive_window_processor(ctx, messages):
    try:
        ...
        result = await _run_window_compaction(ctx, messages, budget)   # mutates runtime
        if result is None:
            ...
            return messages
        tokens_after_local = estimate_message_tokens(result)            # could raise
        savings = ...                                                   # could div-zero
        ...
        return result
    except Exception:
        log.warning("Mid-turn compaction failed — skipping", exc_info=True)
        return messages                                                 # original list
```

`apply_compaction` (called from `_run_window_compaction`) sets four
runtime fields *before* it returns — those mutations survive the bare
`except`.

**Fix.** Either (a) snapshot runtime fields and restore on exception,
or (b) restructure so mutations happen at a single commit point AFTER
the result is fully constructed. Option (b) is cleaner.

**Concrete impl** — `co_cli/context/compaction.py`:

1. Have `apply_compaction` return a typed result that includes the
   mutations *intended* but does NOT apply them:

```python
@dataclass
class _CompactionOutcome:
    result: list[ModelMessage]
    summary_text: str | None
    post_token_estimate: int           # estimate_message_tokens(result)
    message_count: int                 # len(result)


async def apply_compaction(ctx, messages, bounds, *, announce, focus=None):
    ...
    return _CompactionOutcome(
        result=result,
        summary_text=summary_text,
        post_token_estimate=estimate_message_tokens(result),
        message_count=len(result),
    )
```

2. Move the runtime commit to a single function callers invoke after
   they decide to keep the result:

```python
def _commit_compaction(ctx, outcome: _CompactionOutcome) -> None:
    ctx.deps.runtime.compaction_applied_this_turn = True
    ctx.deps.runtime.post_compaction_token_estimate = outcome.post_token_estimate
    ctx.deps.runtime.message_count_at_last_compaction = outcome.message_count
    if outcome.summary_text is not None:
        ctx.deps.runtime.previous_compaction_summary = outcome.summary_text
```

3. Update the three callers (proactive, overflow PATH 2, `/compact`) to
   compute the outcome first, then commit + return. In proactive,
   compute savings, decide thrash counter, AND only then commit.
   In recovery PATH 1 (`recover_overflow_history` strip-only-fits), keep
   the existing `_mark_compaction_applied(ctx, stripped)` semantics —
   that path has no risky post-compaction work.

This makes the failure mode explicit: any exception between
`apply_compaction` returning and `_commit_compaction` firing leaves
runtime untouched.

**Files**: `co_cli/context/compaction.py`,
`co_cli/commands/compact.py`
**Tests** (`tests/test_flow_compaction_proactive.py`): inject a
`pytest.MonkeyPatch` that makes `estimate_message_tokens` raise the
*second* time it's called; assert
`runtime.compaction_applied_this_turn is False` AND
`runtime.previous_compaction_summary` unchanged AND the returned list
is the original `messages`.

---

## Tier B — Anti-patterns and robustness

### TASK 4: Restore CQS for `compaction_skip_count` state machine ✓ DONE

**Symptom.** Circuit-breaker state is mutated from two functions with
read-mutate paths interleaved across the file. Auditing the cadence
(0–2 open / 3–12 closed / 13 probe / 14–22 closed / 23 probe) requires
tracing two write sites.

**Why.** `co_cli/context/compaction.py:132-226`:

- `_summarization_gate_open` mutates `compaction_skip_count` on the
  block path while named like a pure check.
- `_gated_summarize_or_none` mutates it on summarizer-failure and
  empty-output paths AND resets it to 0 on success.

**Fix.** Make the gate a pure read; centralize all writes in
`_gated_summarize_or_none` (which already owns the success and failure
write sites). The probe-cadence math then lives entirely in one place.

**Concrete impl** — `co_cli/context/compaction.py`:

```python
def _summarization_gate_open(ctx) -> tuple[bool, bool]:
    """Return (gate_open, is_probe). Read-only; never mutates runtime."""
    if not ctx.deps.model:
        return (False, False)
    count = ctx.deps.runtime.compaction_skip_count
    if count < _COMPACTION_BREAKER_TRIP:
        return (True, False)
    skips_since_trip = count - _COMPACTION_BREAKER_TRIP
    if skips_since_trip == 0 or skips_since_trip % _COMPACTION_BREAKER_PROBE_EVERY != 0:
        return (False, False)
    return (True, True)


async def _gated_summarize_or_none(ctx, dropped, *, announce, focus, previous_summary=None):
    gate_open, is_probe = _summarization_gate_open(ctx)
    if not gate_open:
        ctx.deps.runtime.compaction_skip_count += 1     # accounting moves here
        return None
    if is_probe:
        log.info("Compaction: circuit breaker probe (count=%d)",
                 ctx.deps.runtime.compaction_skip_count)
    ...
```

Behavior is unchanged; only ownership shifts. Existing tests pinning
the cadence (`tests/test_flow_compaction_proactive.py`) should pass
without change — if any of them rely on the specific mutation site,
update them to the new ownership.

**Files**: `co_cli/context/compaction.py`
**Tests**: existing cadence tests must continue to pass; no new tests
required.

---

### TASK 5: Rename `current_request_tokens_after_spill` ✓ DONE

**Symptom.** Field is set on every `enforce_request_size` invocation
including the `below_threshold` and `no_candidates` fast paths where no
spill ran. The name promises "after spill"; readers will misinterpret.

**Why.** `co_cli/context/history_processors.py:429`. The field is
currently OTEL-only on the read side, but the name is the bug — any
future consumer would draw the wrong inference.

**Fix.** Rename to `current_request_tokens_estimate` (or
`last_request_token_estimate` — TL choice). Single rename across:

- `co_cli/deps.py` (CoRuntimeState definition)
- `co_cli/context/history_processors.py` (write site)
- `co_cli/context/compaction.py` (OTEL read site, line ~493)
- `docs/specs/compaction.md` §2.4 "Side effect" prose

**Files**: `co_cli/deps.py`, `co_cli/context/history_processors.py`,
`co_cli/context/compaction.py`
**Tests**: grep-and-replace; no behavioral test required.

---

### TASK 6: Unify spill entry points (native `tool_output` + MCP) ✓ DONE

**Symptom.** Two spill paths drift independently:

- Native: `co_cli/tools/tool_io.py:213-248` `tool_output()` — emits
  `tool_budget.spill_tool_result` span.
- MCP: `co_cli/tools/lifecycle.py:237-258` `after_tool_execute` — calls
  `spill_if_oversized` directly, NO span.

Either consumer of the span (dashboard, eval, debugging) sees only
half the spill events.

**Fix.** Extract the spill-with-span pattern from `tool_output` into a
helper, and have both call sites use it.

**Concrete impl** — `co_cli/tools/tool_io.py`:

```python
def spill_with_span(
    content: str,
    *,
    tool_name: str,
    tool_results_dir: Path,
    threshold_chars: int | float,
    forced: bool = False,
) -> str:
    span_threshold = SPILL_THRESHOLD_CHARS if math.isinf(threshold_chars) else int(threshold_chars)
    content_chars = len(content)
    spill_fired = False
    if content_chars > threshold_chars:
        new_content = spill_if_oversized(content, tool_results_dir, tool_name, force=forced)
        spill_fired = new_content != content
        content = new_content
    with _TRACER.start_as_current_span("tool_budget.spill_tool_result") as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("spill.threshold_chars", span_threshold)
        span.set_attribute("spill.content_chars", content_chars)
        span.set_attribute("spill.fired", spill_fired)
        span.set_attribute("spill.forced", forced)
        if spill_fired:
            span.set_attribute("spill.savings_chars", content_chars - len(content))
    return content
```

Then `tool_output` and `lifecycle.after_tool_execute` both call
`spill_with_span(...)` instead of duplicating the logic.

**Files**: `co_cli/tools/tool_io.py`, `co_cli/tools/lifecycle.py`
**Tests** (`tests/test_flow_spill_otel.py`): add a parameterized case
for MCP-source tools — assert the span is emitted with `tool.name`
matching the MCP tool. Existing native-tool span tests stay.

---

### TASK 7: Guard hashing against lone surrogates

**Symptom.** Latent crash. `dedup_tool_results` and `enforce_request_size`
both call `hashlib.sha256(content.encode("utf-8"))` on tool-return
content (`co_cli/context/_dedup_tool_results.py:40`,
`co_cli/tools/tool_io.py:96`). `sanitize_surrogate_codepoints` runs
LAST in the processor chain, so any lone surrogate that reaches a
tool-return content string would crash with `UnicodeEncodeError` before
the sanitizer can scrub it.

In practice tool returns come from local Python and never carry
surrogates today. But the invariant is undocumented and brittle — a
future tool source (an MCP server speaking JSON over stdio, an
`obsidian_read` ingesting a file with corrupt UTF-16, a `web_fetch`
echoing model-generated text) could break it without warning.

**Fix.** Hash with `errors="replace"` at both call sites. Two-line
change.

**Concrete impl**:

- `co_cli/context/_dedup_tool_results.py:40` — change
  `content.encode("utf-8")` to `content.encode("utf-8", errors="replace")`.
- `co_cli/tools/tool_io.py:96` — same.

The hash collision risk from `errors="replace"` is negligible (only
matters for content that already contains lone surrogates, which is the
defect case — collisions there mean two corrupt blobs hash the same,
and we dedup them, which is what we want).

**Files**: `co_cli/context/_dedup_tool_results.py`,
`co_cli/tools/tool_io.py`
**Tests**: add a unit test in `tests/test_flow_history_processors.py`
that constructs a `ToolReturnPart` with content containing
`"\ud800prefix" * 100` (length ≥ 200 chars) and asserts
`dedup_tool_results` does not raise; same for spill.

---

### TASK 8: Consolidate per-request token-estimate walks

**Symptom.** ≥ 2 full O(N) walks of the message list per
`ModelRequestNode` (one in `enforce_request_size`, one in
`proactive_window_processor`, sometimes a third inside
`apply_compaction` for `post_compaction_token_estimate`). Each walk
does `len(part.content)` and JSON-serializes `ToolCallPart.args`.
Cheap individually, but for long histories pays repeatedly.

**Fix.** Cache the `(local_estimate, reported_estimate)` pair on
`RunContext` for the duration of one MRN pre-flight chain. Smallest
useful cache key: identity of the `messages` list reference plus its
length (lists are not hashable, but list identity + length is enough
because each processor returns either the input reference unchanged or
a freshly-built list with a different length).

This is a perf clean-up; defer if Tier A consumes the cycle. Document
the deferral in the delivery summary.

**Files**: `co_cli/context/summarization.py` (or a new
`co_cli/context/_token_cache.py`)
**Tests**: pure perf — no new behavioral test, but existing tests must
continue to pass.

---

## Out of scope

The following items from the trace analysis are intentionally excluded
from this plan:

- **Negative savings on spill placeholder boundary** (~1.6KB content →
  larger placeholder). Real edge case, but masked by largest-first sort
  and only matters when ALL spillable returns are small. Document as a
  known limit; revisit only if observed in telemetry.
- **Static markers in dropped range not carried forward**
  (`_gather_prior_summaries` only matches `SUMMARY_MARKER_PREFIX`).
  Spec already documents this as intentional; revisit with the iterative
  summarizer if quality regresses.
- **`/compact` synthetic "Understood." `ModelResponse`**. Test gap, but
  shape is structurally valid; defer to a separate doc-or-test task.
- **OTEL attribute writes before fast-path gate in
  `proactive_window_processor`**. Cosmetic; the attributes are set on a
  span that gets created either way, so no real cost reduction.
- **Local import of `MAX_TOOL_CALLS_PER_MODEL_TURN` inside
  `proactive_window_processor`**. Removing requires breaking the import
  cycle properly (`co_cli.context.constants` shared module). Defer
  until another reason to refactor either side.

---

## Tier C — Summarizer pipeline (§2.6)

The §2.6 trace (added after the Tier A/B issues were scoped) found five
issues specific to the LLM summarizer pipeline: `apply_compaction`
assembly, the gated-summarize wrapper, the iterative-update prompt, and
the marker text the model receives. Four are user-visible (C1–C4); C5
fixes a model-facing directive that lies in the `/compact` case.

### TASK C1: Clear `previous_compaction_summary` on `/compact` failure

**Symptom.** `/compact` calls `apply_compaction(messages, (0, n, n))`.
On summarizer failure, `summary_text` is `None` and the static marker
replaces the full history. But `runtime.previous_compaction_summary`
retains text written by the LAST successful compaction, describing
content that no longer matches anything in the new history. The NEXT
compaction's iterative branch then prepends `PREVIOUS SUMMARY:
<unrelated text>` to the prompt, asking the summarizer to "PRESERVE all
existing information that is still relevant" against history it never
saw.

**Fix.** In `co_cli/commands/compact.py:_cmd_compact`, after
`apply_compaction` returns, clear the field on summarizer failure:

```python
new_history, summary = await apply_compaction(...)
new_history.append(ModelResponse(parts=[_TextPart(content="...")]))
ctx.deps.runtime.consecutive_low_yield_proactive_compactions = 0
if summary is None:
    ctx.deps.runtime.previous_compaction_summary = None
```

**Files**: `co_cli/commands/compact.py`
**Tests** (`tests/test_flow_compaction_proactive.py` or new
`tests/test_flow_compact_command.py`): set
`runtime.previous_compaction_summary = "OLD"`, invoke `_cmd_compact`
with summarizer stubbed to return empty/None, assert
`runtime.previous_compaction_summary is None`.

---

### TASK C2: Strip prior compaction markers from `dropped` before summarizing

**Symptom.** When the iterative branch fires
(`previous_summary is not None`), the prior summary is embedded in the
prompt template as `PREVIOUS SUMMARY: <raw>`. But `dropped` ALSO
contains a `UserPromptPart` whose content begins with
`SUMMARY_MARKER_PREFIX` and embeds the same raw text. Result: the LLM
receives duplicated tokens — once in instructions, once in
`message_history`.

The skip-when-set guard in `gather_compaction_context` only suppresses
the ENRICHMENT block; it does not touch `dropped` itself.

**Fix.** In `co_cli/context/compaction.py:summarize_dropped_messages`,
filter `dropped` when `previous_summary` is non-`None`. Add a
private helper using the existing `is_compaction_marker` predicate:

```python
def _strip_prior_markers(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Drop ModelRequests whose only part is a compaction marker UserPromptPart."""
    return [
        m for m in messages
        if not (
            isinstance(m, ModelRequest)
            and len(m.parts) == 1
            and isinstance(m.parts[0], UserPromptPart)
            and is_compaction_marker(m.parts[0].content)
        )
    ]


async def summarize_dropped_messages(ctx, dropped, *, focus=None, previous_summary=None):
    enrichment = gather_compaction_context(ctx, dropped)
    if previous_summary is not None:
        dropped = _strip_prior_markers(dropped)
    return await summarize_messages(...)
```

`is_compaction_marker` is already imported and re-exported in
`compaction.py:45,75` — no new module-boundary imports needed.

**Files**: `co_cli/context/compaction.py`
**Tests**
(`tests/test_flow_compaction_summarization.py`): construct `dropped`
with a prior summary marker `ModelRequest`; call
`summarize_dropped_messages` with `previous_summary` set and
`summarize_messages` mocked to capture `message_history`; assert the
captured list does NOT contain the prior marker message.

---

### TASK C3: Closing status callback on summarizer success / failure

**Symptom.** `_gated_summarize_or_none` fires
`"Compacting conversation..."` before the LLM call. On every failure
path (exception, empty output, breaker block-after-announce) the banner
stays stale — user has no visual confirmation of what happened.

**Fix.** Move closing-status emission into `apply_compaction` so all
three callers (proactive, overflow PATH 2, `/compact`) inherit it:

```python
async def apply_compaction(ctx, messages, bounds, *, announce, focus=None):
    ...
    summary_text = await _gated_summarize_or_none(...)
    if announce and (cb := ctx.deps.runtime.status_callback) is not None:
        if summary_text is not None:
            cb("Compacted.")
        elif ctx.deps.model is None:
            cb("LLM compaction unavailable — used static marker.")
        else:
            cb("Summarizer failed — used static marker.")
    ...
```

The `announce` flag is already the proactive-vs-recovery toggle.
`/compact` passes `announce=False` AND prints its own console output, so
no regression there. `recover_overflow_history` PATH 2 calls
`apply_compaction(announce=False)` and emits its own
`"Context overflow — compacting and retrying..."` from `run_turn`, so
PATH 2 status remains owned by the orchestrator.

**Files**: `co_cli/context/compaction.py`
**Tests** (`tests/test_flow_compaction_proactive.py`): spy
`runtime.status_callback`; for each branch (success / no-model /
breaker-tripped / summarizer-raises) assert the expected closing
message is emitted.

---

### TASK C4: Fence `previous_summary` with XML tags in iterative template

**Symptom.** `_build_iterative_template` embeds raw `previous_summary`
inline. The previous summary contains `## Active Task`, `## Goal`, etc.
markdown headings (it's the LLM's own structured output from the prior
call). The from-scratch template (`_SUMMARIZE_PROMPT`) ALSO references
those exact heading names as INSTRUCTION text. The model sees identical
structure as both example content and instruction — small / quantized
models may emit only deltas or echo the previous summary unchanged
because the structural cue is ambiguous.

**Fix.** In `co_cli/context/summarization.py:_build_iterative_template`,
wrap `previous_summary` in XML tags:

```python
def _build_iterative_template(previous_summary: str) -> str:
    return (
        "You are updating a context compaction summary. A previous compaction\n"
        "produced the summary below. New conversation turns have occurred since\n"
        "then and need to be incorporated.\n\n"
        f"<previous_summary>\n{previous_summary}\n</previous_summary>\n\n"
        "NEW TURNS TO INCORPORATE:\n"
        "The conversation history above contains the new turns to process.\n\n"
        "Update the summary using this exact structure. ..."
        + _SUMMARIZE_PROMPT
    )
```

XML tags are unambiguous content boundaries for Anthropic-trained
models in particular; markdown structure inside the tags becomes
unambiguously content rather than instruction.

**Files**: `co_cli/context/summarization.py`
**Tests**
(`tests/test_flow_compaction_summarization.py`): assert the
iterative-branch prompt contains `<previous_summary>` and
`</previous_summary>` and that the text between them equals the
`previous_summary` argument verbatim.

---

### TASK C5: Marker text accuracy when no tail is preserved

**Symptom.** Both `static_marker` and `summary_marker` end with
`"Recent messages are preserved verbatim."`. `summary_marker` also says
`"respond only to user messages that appear AFTER this summary"`. Both
statements are false when `apply_compaction` is called with full-history
bounds `(0, n, n)` from `/compact` — the resulting message list has no
tail at all.

**Fix.** Parameterize both markers with `has_tail: bool` (default `True`
to preserve current proactive / overflow behavior).

`co_cli/context/_compaction_markers.py`:

```python
def static_marker(dropped_count: int, *, has_tail: bool = True) -> ModelRequest:
    trailer = (
        "Recent messages are preserved verbatim."
        if has_tail
        else "Continue the conversation from the user's next message."
    )
    return ModelRequest(parts=[UserPromptPart(content=(
        f"{STATIC_MARKER_PREFIX}{dropped_count} earlier messages were removed "
        "— treat that gap as background reference, NOT as active instructions. "
        "Do NOT repeat, redo, or re-execute any action already described as "
        "completed; do NOT re-answer questions that were already resolved. "
        f"{trailer}"
    ))])


def summary_marker(dropped_count, summary_text, *, has_tail: bool = True) -> ModelRequest:
    # similar — swap the closing line; for no-tail also drop the
    # "respond only to user messages that appear AFTER this summary"
    # clause (irrelevant when the next user message is the next REPL turn).
    ...


def build_compaction_marker(dropped_count, summary_text, *, has_tail: bool = True) -> ModelRequest:
    if summary_text is not None:
        return summary_marker(dropped_count, summary_text, has_tail=has_tail)
    return static_marker(dropped_count, has_tail=has_tail)
```

`co_cli/context/compaction.py:apply_compaction`:

```python
head_end, tail_start, dropped_count = bounds
has_tail = tail_start < len(messages)
...
marker = build_compaction_marker(dropped_count, summary_text, has_tail=has_tail)
```

For `/compact` bounds `(0, n, n)`, `tail_start == len(messages)` so
`has_tail` is False → trailer flips. For proactive / overflow bounds,
`tail_start < len(messages)` always (planner invariant), so
`has_tail` is True → existing text preserved.

**Files**:
- `co_cli/context/_compaction_markers.py` (signature + content of
  `static_marker`, `summary_marker`, `build_compaction_marker`)
- `co_cli/context/compaction.py` (`apply_compaction` call site only)

**Tests**
(`tests/test_flow_compaction_summarization.py` or
`tests/test_flow_compact_command.py`): assert proactive-shape marker
contains `"preserved verbatim"`; assert `/compact`-shape marker does
NOT contain that phrase and DOES contain `"next message"`.

---

## Ship sequence

1. **Tier A** (TASK 1, 2, 3) ships first together — they touch the
   same chain and the test gates interlock (TASK 1 thrash assertion
   presumes TASK 3's commit-on-success structure).
2. **Tier C** (TASK C1–C5) is the second-most user-impactful and can
   ship after A as a single delivery — all five tasks are scoped to the
   summarizer pipeline (`apply_compaction`, summarizer prompt, marker
   text) and the test files are the same pair.
3. **Tier B** (TASK 4–8) is cleanup — ship as separate commits in any
   order or bundle into one delivery.

Version bump per `feedback_ship_version_bump.md`:
- Tier A: correctness with new test gates → +2 even.
- Tier C: user-visible behavior changes (C3 status, C5 marker text) →
  +2 even.
- Tier B: mixed cleanup → +2 even if bundled, +1 odd per commit if
  piecemeal.
