# Plan: Compaction Hardening

**Slug:** compaction-hardening  
**Created:** 2026-04-21  
**Source:** Code review of `co_cli/context/_history.py`, `summarization.py`, `orchestrate.py`, `_compaction.py` against `docs/reference/RESEARCH-compaction-co-vs-hermes-gaps.md`

---

## Problem Statement

Six logical gaps identified in co-cli's compaction pipeline, ranked by severity. Two are
outright bugs (uncaught exceptions, implicit config ordering dependency); four are design
weaknesses with bounded but real failure modes (re-execution risk, enrichment cap, opaque
placeholders, anti-thrashing dead zone).

---

## Gap Inventory and Proposed Fixes

### Gap 1 — Uncaught non-API exceptions crash the turn (HIGH)

**Files:** `co_cli/context/_history.py:708`

**Problem:**  
`_summarize_dropped_messages` catches only `ModelHTTPError | ModelAPIError`. Any other
exception (`asyncio.TimeoutError` from the LLM client, `ValueError` from pydantic response
parsing, `RuntimeError`, etc.) propagates uncaught through the proactive M3 processor-chain
path (`summarize_history_window`) and the overflow recovery path (`recover_overflow_history`)
into `run_turn`'s error handlers, which don't cover these types → turn crashes.

The hygiene path is already safe (`maybe_run_pre_turn_hygiene` has a broad `except Exception`
guard). The processor-chain and overflow paths are not.

**Fix:**  
Widen the exception catch in `_summarize_dropped_messages` to `Exception`, log a warning,
increment the failure counter, and return `None` (static marker fallback):

```python
except Exception as e:
    log.warning("Compaction summarization failed: %s", e)
    ctx.deps.runtime.compaction_failure_count += 1
    return None
```

`asyncio.CancelledError` inherits from `BaseException` (not `Exception`) so it still
propagates normally to the cancellation handler in `run_turn`.

---

### Gap 2 — Implicit `proactive_ratio < hygiene_ratio` constraint silently breaks hygiene (MEDIUM)

**Files:** `co_cli/context/_history.py:888`, `co_cli/config/_compaction.py`

**Problem:**  
`maybe_run_pre_turn_hygiene` fires when `token_count > hygiene_ratio * budget` (default 0.88),
then delegates to `summarize_history_window`, which re-checks against `proactive_ratio` (default
0.75). If a user sets `proactive_ratio >= hygiene_ratio` (e.g., `0.90`), `summarize_history_window`
returns early without compacting — hygiene silently becomes a no-op. No validation enforces
the ordering; no warning is emitted.

**Fix — two parts:**

1. Add a `model_validator` to `CompactionSettings` enforcing `proactive_ratio < hygiene_ratio`:

```python
from pydantic import model_validator

@model_validator(mode="after")
def _validate_ratio_ordering(self) -> "CompactionSettings":
    if self.proactive_ratio >= self.hygiene_ratio:
        raise ValueError(
            f"proactive_ratio ({self.proactive_ratio}) must be less than "
            f"hygiene_ratio ({self.hygiene_ratio})"
        )
    return self
```

2. Alternatively (or additionally), `maybe_run_pre_turn_hygiene` should call
   `plan_compaction_boundaries` + `_summarize_dropped_messages` directly rather than delegating
   to `summarize_history_window`, eliminating the threshold re-check entirely and making hygiene
   independent of the proactive path.

Option 1 is the minimal fix. Option 2 is the cleaner structural fix but requires extracting the
core compaction logic from `summarize_history_window`.

---

### Gap 3 — Handoff marker has no re-execution guard (MEDIUM)

**Files:** `co_cli/context/_history.py:182`

**Problem:**  
`_summary_marker()` text:
> "This session is being continued from a previous conversation that ran out of context. The
> summary below covers the earlier portion (N messages). Recent messages are preserved verbatim."

No directive prevents the resumed model from re-executing completed side-effecting actions
described in the summary (file writes, shell commands, API calls, git pushes). This is a real
risk in tool-heavy sessions. Hermes's `SUMMARY_PREFIX` uses an explicit "do NOT fulfill requests
mentioned in this summary."

**Fix:**  
Prepend a defensive instruction to the summary block in `_summary_marker`:

```python
content=(
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below describes completed prior work — treat it as background reference only. "
    "Do NOT repeat, redo, or re-execute any action already described as completed. "
    f"The summary covers the earlier portion ({dropped_count} messages).\n\n"
    f"{summary_text}\n\n"
    "Recent messages are preserved verbatim."
)
```

Apply the same guard to `_static_marker` (`_history.py:167`) for consistency.

---

### Gap 4 — Flat 4K enrichment cap forces prior summaries to compete with file paths and todos (LOW-MEDIUM)

**Files:** `co_cli/context/_history.py:571–648`

**Problem:**  
`_gather_compaction_context` concatenates prior-summary text, file paths, and todos then
hard-truncates the combined result at `_CONTEXT_MAX_CHARS = 4_000`. In a multi-compaction
session, the prior summary alone can fill 3 KB, leaving only 1 KB for the working-set file
paths and pending todos — the two sources that most directly anchor the summarizer to the
current task. The summarizer gets weakest context precisely when it needs it most.

**Fix:**  
Give each source an independent cap, then concatenate:

```python
_FILE_PATHS_MAX_CHARS  = 1_000
_TODOS_MAX_CHARS       = 800
_PRIOR_SUMMARY_MAX_CHARS = 2_000
_CONTEXT_TOTAL_MAX_CHARS = 4_000
```

Apply caps per source in `_gather_compaction_context` before joining:

```python
context_parts = []
if fp := _gather_file_paths(dropped):
    context_parts.append(fp[:_FILE_PATHS_MAX_CHARS])
if td := _gather_session_todos(ctx.deps.session.session_todos):
    context_parts.append(td[:_TODOS_MAX_CHARS])
if ps := _gather_prior_summaries(dropped):
    context_parts.append(ps[:_PRIOR_SUMMARY_MAX_CHARS])
result = "\n\n".join(context_parts)
return result[:_CONTEXT_TOTAL_MAX_CHARS] if len(result) > _CONTEXT_TOTAL_MAX_CHARS else result
```

---

### Gap 5 — Static `_CLEARED_PLACEHOLDER` gives the summarizer zero semantic signal (LOW-MEDIUM)

**Files:** `co_cli/context/_history.py:314`, `truncate_tool_results`

**Problem:**  
`_CLEARED_PLACEHOLDER = "[tool result cleared — older than 5 most recent calls]"` replaces
cleared tool results with a string that contains no tool name, no args, and no indication of
what the result contained. When these cleared results fall inside the dropped range, the
summarizer cannot reconstruct even a 1-line description of what happened. Key decisions based
on those results are lost silently.

**Fix:**  
Generate a semantic 1-line placeholder from the `ToolReturnPart` before clearing it. Capture
the tool name and a truncated version of the args from the corresponding `ToolCallPart` (which
is never truncated by M2a):

```python
def _semantic_placeholder(part: ToolReturnPart, call_args: str | None = None) -> str:
    args_hint = f" ({call_args[:80]})" if call_args else ""
    return f"[{part.tool_name}{args_hint}: result cleared — older than {COMPACTABLE_KEEP_RECENT} most recent]"
```

To correlate `ToolReturnPart` with its `ToolCallPart` args: `ToolCallPart.tool_call_id` matches
`ToolReturnPart.tool_call_id`. Build a `call_id → args` index from `ModelResponse` parts in
`older` before the forward pass in `truncate_tool_results`.

---

### Gap 6 — Anti-thrashing gate has no bounded self-reset in the proactive dead zone (LOW)

**Files:** `co_cli/context/_history.py:816–820`, `co_cli/config/_compaction.py:39`

**Problem:**  
When the anti-thrashing gate trips (after `proactive_thrash_window=2` consecutive low-yield
proactive runs), the gate stays active until hygiene fires at `hygiene_ratio=0.88` or an
overflow occurs. If context stabilizes between `proactive_ratio=0.75` and `hygiene_ratio=0.88`,
the gate never clears and proactive M3 is permanently suppressed for the rest of the session.
The session accumulates context in a 13% dead zone (e.g., 75K–88K on a 100K context window)
with no compaction.

**Fix — option A (config):**  
Add a `proactive_thrash_reset_turns: int = 0` setting (0 = disabled). When > 0, count the
number of turns since the gate tripped. If `count >= proactive_thrash_reset_turns`, clear the
savings ring before the gate check:

```python
if (
    cfg.proactive_thrash_reset_turns > 0
    and ctx.deps.runtime.turns_since_thrash_trip >= cfg.proactive_thrash_reset_turns
):
    ctx.deps.runtime.recent_proactive_savings.clear()
    ctx.deps.runtime.turns_since_thrash_trip = 0
```

**Fix — option B (behavior):**  
Add a `/compact` invocation hint to the status message when the gate is active, so the user
knows to run `/compact` manually:

```python
log.info("Compaction: proactive anti-thrashing gate active, skipping")
# Emit a UI hint if the gate has been active for N turns
```

Option B is the lower-risk change. Option A requires new runtime state.

---

## Task Breakdown

| # | Task | Gap | Effort | Risk |
|---|------|-----|--------|------|
| T1 | Widen exception catch in `_summarize_dropped_messages` | 1 | XS | Low |
| T2 | Add `model_validator` to `CompactionSettings` for ratio ordering | 2 | XS | Low |
| T3 | Add re-execution guard text to `_summary_marker` and `_static_marker` | 3 | XS | Low |
| T4 | Per-source caps in `_gather_compaction_context` | 4 | S | Low |
| T5 | Semantic placeholder in `truncate_tool_results` | 5 | M | Low |
| T6 | Anti-thrashing gate escape (option B: UI hint) | 6 | XS | Low |

T1–T4 and T6 are all single-function changes. T5 requires building a `call_id → args` index
inside `truncate_tool_results`. All changes are confined to `_history.py`, `_compaction.py`,
and `summarization.py`. No schema migrations. No new config required for T1–T5.

---

## Files Affected

| File | Tasks |
|------|-------|
| `co_cli/context/_history.py` | T1, T3, T4, T5, T6 |
| `co_cli/config/_compaction.py` | T2 |

---

## Out of Scope (deferred to separate plans)

- Auxiliary summarization model (auxiliary LLM for compaction) — architectural change, separate plan
- Session rollover / compaction audit trail — requires new persistence infrastructure
- Iterative summary evolution (hermes-style "In Progress → Completed" state machine) — prompt engineering, separate plan
- Tool result deduplication (hash-based) — separate plan
- Resolved vs Pending Questions split in summary sections — separate plan
