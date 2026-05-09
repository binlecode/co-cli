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

### TASK C1: Clear `previous_compaction_summary` on `/compact` failure ✓ DONE

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
