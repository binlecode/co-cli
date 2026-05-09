# Plan: Compaction Flow — Branch-B Removal and Summarizer-Pipeline Cleanup

Task type: code

## Context

Three tasks scoped to the summarizer pipeline. Single delivery.

1. **TASK 1** — Remove iterative summarization path ("branch B"). Branch B
   embeds `PREVIOUS SUMMARY:` inline in the prompt while the same prior
   summary is also present in `message_history` as the SUMMARY_MARKER carried
   in `dropped`. The LLM sees the prior summary twice. The from-scratch
   prompt's existing prior-summary integration block (`_SUMMARIZE_PROMPT`
   lines 146–151) already carries the carry-forward semantics; branch B is
   redundant.
2. **TASK 2** — Emit a closing status callback after compaction completes.
   Today the `"Compacting conversation..."` banner stays stale on every
   failure path.
3. **TASK 3** — `static_marker` / `summary_marker` claim "Recent messages
   are preserved verbatim." even when called with `(0, n, n)` from
   `/compact`, where no tail exists.

Adjacent edit already applied: `_SUMMARIZE_PROMPT` now has separate `## Goal`
and `## Constraints & Preferences` sections (was merged). No follow-up code
change; verify via existing summarization tests.

---

## Out of scope

- Iterative summarization re-implementation with structural filter
  (opencode-style). No eval evidence of advantage over from-scratch with the
  existing integration block. Defer indefinitely.
- Peer-survey gap 1 (terse-bullets rule) and gap 3 (no-meta-commentary rule).
  Speculative without eval evidence.

---

## TASK 1: Remove branch B

**Surface to remove:**

| File | Lines | Change |
|------|-------|--------|
| `co_cli/context/summarization.py` | 158–183 | Delete `_build_iterative_template` |
| `co_cli/context/summarization.py` | 232–273 | Remove `previous_summary` param from `summarize_messages`; drop branch-selection logic; simplify body and docstring |
| `co_cli/context/summarization.py` | 204–229 | `_build_summarizer_prompt` becomes single-template (`_SUMMARIZE_PROMPT` only); simplify or inline |
| `co_cli/context/compaction.py` | 140–161 | Remove `previous_summary` param from `summarize_dropped_messages` |
| `co_cli/context/compaction.py` | 169–214 | Remove `previous_summary` param from `_gated_summarize_or_none` |
| `co_cli/context/compaction.py` | 244–273 | Remove `previous_summary` read in `_compose_compacted_history` |
| `co_cli/context/compaction.py` | 310 | Remove `previous_compaction_summary` write in `compact_to_bounds` |
| `co_cli/context/compaction.py` | 446 | Remove `previous_compaction_summary` write in `compact_under_budget` |
| `co_cli/deps.py` | 148, 177–180 | Remove `previous_compaction_summary` field from `CoRuntimeState`; update class docstring |
| `co_cli/commands/compact.py` | 54–55 | Remove `previous_compaction_summary = None` clear |
| `co_cli/commands/clear.py` | 13 | Remove `previous_compaction_summary = None` clear |
| `co_cli/commands/new.py` | 20 | Remove `previous_compaction_summary = None` clear |
| `tests/test_flow_compaction_summarization.py` | 113–149 | Delete `test_summarize_messages_iterative_incorporates_new_turns` |
| `tests/test_flow_slash_commands.py` | 27, 41, 47–75 | Remove `previous_compaction_summary` setup, the assert, and `test_cmd_compact_clears_previous_summary_on_summarizer_failure` |

**Keep (do not touch):**
- `_SUMMARIZE_PROMPT` lines 146–151 — the prior-summary integration block.
  The SUMMARY_MARKER survives in `dropped` and the LLM sees it via
  `message_history`; this block carries the PENDING→RESOLVED transitions and
  carry-forward rules.
- `SUMMARY_MARKER_PREFIX`, `summary_marker`, `static_marker`,
  `build_compaction_marker` — needed for TASK 3.

**Done when:**
- `grep -rn "previous_compaction_summary\|_build_iterative_template\|previous_summary" co_cli/ tests/`
  returns no matches.
- New regression test (`tests/test_flow_compaction_summarization.py`):
  on a transcript with a prior SUMMARY_MARKER in `dropped`, the summarizer
  LLM input contains the prior summary text exactly once.
- Full suite green.

---

## TASK 2: Closing status callback after compaction

**Fix.** Move closing-status emission into `compact_to_bounds` and
`compact_under_budget` (post-`_gated_summarize_or_none`):

```python
if announce and (cb := ctx.deps.runtime.status_callback) is not None:
    if summary_text is not None:
        cb("Compacted.")
    elif ctx.deps.model is None:
        cb("LLM compaction unavailable — used static marker.")
    else:
        cb("Summarizer failed — used static marker.")
```

`/compact` passes `announce=False` and prints its own console output (no
regression). Overflow PATH 2 also passes `announce=False` and `run_turn`
emits its own banner.

**Files**: `co_cli/context/compaction.py`.

**Tests** (`tests/test_flow_compaction_proactive.py`): spy
`runtime.status_callback`; for each branch (success / no-model /
breaker-tripped / summarizer-raises) assert the expected closing message.

---

## TASK 3: Marker text accuracy when no tail preserved

**Fix.** Parameterize markers with `has_tail: bool = True`.

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
    # swap the closing line; for no-tail also drop the
    # "respond only to user messages that appear AFTER this summary" clause
    ...


def build_compaction_marker(dropped_count, summary_text, *, has_tail: bool = True) -> ModelRequest:
    if summary_text is not None:
        return summary_marker(dropped_count, summary_text, has_tail=has_tail)
    return static_marker(dropped_count, has_tail=has_tail)
```

`co_cli/context/compaction.py:_compose_compacted_history`:

```python
head_end, tail_start, dropped_count = bounds
has_tail = tail_start < len(messages)
marker = build_compaction_marker(dropped_count, summary_text, has_tail=has_tail)
```

For `/compact` bounds `(0, n, n)`, `tail_start == len(messages)` → `has_tail`
is False → trailer flips. For proactive / overflow bounds, `tail_start <
len(messages)` always (planner invariant) → `has_tail` is True → existing
text preserved.

**Files**:
- `co_cli/context/_compaction_markers.py` (`static_marker`, `summary_marker`,
  `build_compaction_marker`)
- `co_cli/context/compaction.py` (`_compose_compacted_history` call site)

**Tests** (`tests/test_flow_compaction_summarization.py` or
`tests/test_flow_slash_commands.py`): assert proactive-shape marker contains
`"preserved verbatim"`; `/compact`-shape marker does NOT contain that phrase
and DOES contain `"next message"`.

---

## Ship sequence

Single delivery. Version bump: behavior change (LLM no longer takes the
iterative branch; prompt template gains `## Constraints & Preferences`;
closing status callback fires; `/compact` marker text differs from proactive
marker text) → +2 even.
