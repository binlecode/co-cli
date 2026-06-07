# ISSUE — Compaction production-logic findings

> Source: adversarial trace of the context-compaction pipeline (trigger → L2 spill → L3 proactive
> summarize → boundaries → markers), performed during the TASK-B eval rewrite
> (`evals/eval_context_stability.py`). All seven findings were **verified cold against source** (file:line
> cited). These are production-code issues, **not** eval/test issues. None is a crash; severities range
> medium → low. Discovered 2026-06-07 against commit `b5667fbc` (v0.8.314).
>
> Pipeline processor order (`co_cli/agent/orchestrator.py:65-70`): `dedup → evict → spill → proactive`.
> Files: `co_cli/context/compaction.py`, `summarization.py`, `history_processors.py`,
> `_compaction_boundaries.py`, `_compaction_markers.py`, `tokens.py`, `co_cli/config/compaction.py`,
> `co_cli/tools/tool_io.py`.

## Severity summary

| # | Title | Severity | File(s) |
|---|---|---|---|
| 1 | Tail budget sized floor-exclusive vs floor-inclusive trigger — docstring overstates re-trigger headroom | **Medium** | `_compaction_boundaries.py:170,179`; `config/compaction.py:39-48` |
| 2 | No production no-progress / convergence guard — relies on thrash gate to make thrash *cheap*, not *stop* | **Medium** | `compaction.py:421-563` |
| 3 | Prior summary marker survival is prompt-only, not structural — cross-compaction memory can erode | **Medium** | `compaction.py` (no `is_compaction_marker` use in compaction path) |
| 4 | Spill aggregate accumulator drifts (per-item floor-division) and drives the terminal spill decision | **Low–Med** | `history_processors.py:371,481` |
| 5 | Proactive focus can latch onto a compaction-marker's boilerplate | **Low–Med** | `compaction.py:413-418` |
| 6 | `spill_errors` conflates "too small to spill" with real I/O failure | **Low** | `history_processors.py:363-365`; `tool_io.py:96-99` |
| 7 | `current_request_tokens_estimate` stale after an L3 pass (status display) | **Low** | `history_processors.py:461`; `compaction.py:488`; `main.py:467` |

---

## ISSUE-1 (Medium) — Tail budget is floor-exclusive while the trigger is floor-inclusive

**Where.** `co_cli/context/_compaction_boundaries.py:170` and `:179`; the trigger at `compaction.py:454`;
the config docstring at `co_cli/config/compaction.py:39-48`.

**What's wrong.** The proactive trigger fires when the **floor-inclusive** estimate crosses the threshold:

```python
# compaction.py:453-454
token_count = effective_request_tokens(ctx.deps, messages)   # = static_floor_tokens + message_tokens
token_threshold = int(budget * cfg.compaction_ratio)         # 0.50 × window
```

But `plan_compaction_boundaries` sizes the preserved tail **floor-exclusive**, against the full window:

```python
# _compaction_boundaries.py:170, 179
tail_budget = tail_fraction * budget                         # 0.10 × full window
...
group_tokens = estimate_message_tokens(group.messages)       # message tokens only — no static_floor
```

The config docstring claims "tail = 0.10 × budget = **20% of the trigger point**" (`compaction.py:43-46`).
That arithmetic (`0.10 / 0.50 = 20%`) only holds if both sides measure the same thing — they don't. The
trigger adds `static_floor_tokens` (instructions + ALWAYS tool schemas, ~10.8k tok); the tail sizing does
not. So the tail is **20% of the raw `0.50×window` threshold but ~30% of the *usable* message-token
trigger** once the floor is subtracted (at 64k: usable trigger ≈ `32,768 − 10,800 = 21,968`; tail
`0.10×65,536 = 6,554` ≈ 30%). The invariant `tail_fraction < compaction_ratio` (validated at
`compaction.py:75`) guards re-trigger headroom in **window units** but ignores the floor the trigger adds
back on every pass.

**Failure scenario.** On a small-context model where `static_floor_tokens` is a large share of the window,
post-compaction state = `static_floor + head + tail + marker`. Because the tail is sized ignoring the
floor, the real headroom below the *next* trigger is smaller than the docstring implies — the next request
re-triggers compaction sooner than the `tail_fraction < compaction_ratio` invariant suggests. This is the
exact regime TASK B operates in (qwen3 / 64k), and it directly muddies the `tail_fraction` lever: the knob's
effective tail-as-share-of-trigger is not what the docstring says.

**Concrete fix.** Two options; (B) is the correctness fix, (A) the minimal.

- **(A) Doc-only (low risk).** Correct `config/compaction.py:39-48`: state the tail is `tail_fraction ×
  full window`, **floor-exclusive**, and that as a share of the usable message-token trigger
  (`compaction_ratio × window − static_floor`) it is *larger* than `tail_fraction / compaction_ratio`. Drop
  the "20% of the trigger point" claim.

- **(B) Code (makes the intent literal + safer on small models).** Make `plan_compaction_boundaries`
  floor-aware so the tail is a true fraction of *usable* trigger headroom:

  ```python
  # signature: add static_floor_tokens
  def plan_compaction_boundaries(messages, budget, tail_fraction, static_floor_tokens=0):
      usable_trigger = max(0, int(budget * COMPACTION_RATIO) - static_floor_tokens)
      tail_budget = int(tail_fraction / COMPACTION_RATIO * usable_trigger)   # tail as a share of usable trigger
  ```

  Callers (`proactive_window_processor`, `spill_largest_tool_results`, `recover_overflow_history`) pass
  `deps.static_floor_tokens`. **Caveat:** (B) shrinks the tail when the floor is large, which interacts with
  TASK B's coherence gate — re-run `eval_context_stability.py` after, and re-pin `tail_fraction` from the
  coherence result.

**Verified:** read `_compaction_boundaries.py:160-194`, `compaction.py:453-454`, `config/compaction.py:39-78`.

---

## ISSUE-2 (Medium) — No production no-progress / convergence guard

**Where.** `co_cli/context/compaction.py:421-563` (`proactive_window_processor`); per-pass accounting in
`_record_proactive_outcome` (`compaction.py:330`).

**What's wrong.** The proactive processor's only stop conditions are:

```python
# compaction.py:491
if token_count <= token_threshold:
    ... return messages          # already under trigger
# compaction.py:521
if bounds is None:
    ... return messages          # too few turns to form a boundary
```

There is **no production guard that a fired pass actually reduced tokens**, and **no cap on how often
compaction may fire across a session** (the `_MAX_PROACTIVE_PASSES` ceiling lives only in the eval). The
anti-thrash gate (`compaction.py:511-518`) demotes to a cheap static marker after
`proactive_thrash_window` (2) low-yield passes — but that makes thrashing **cheap**, it does not **stop**
it. `_record_proactive_outcome` (`compaction.py:330`) computes `savings = (token_count − tokens_after) /
token_count` and feeds the thrash counter, but **does not reject a pass whose `tokens_after >=
token_count`** (a pass that grew or held tokens).

**Failure scenario.** A pathological transcript — e.g. an unconditionally-retained last turn group whose
tokens alone exceed `tail_budget` (`_MIN_RETAINED_TURN_GROUPS=1`, `_compaction_boundaries.py:180-184`), and
that group is itself over threshold — re-fires compaction on *every* model request, drops the same tiny
middle, never gets under threshold, and only the thrash gate keeps each pass cheap. The loop never
structurally converges; it just degrades to a static-marker-per-request treadmill with no escalation. An
LLM-summary pass that produces a summary larger than the dropped region (negative `savings`) is likewise
not rejected — only counted.

**Concrete fix.** Add a post-apply monotonicity guard + escalation:

```python
# after computing tokens_after for an applied pass (compaction.py, in/after _record_proactive_outcome)
if tokens_after >= token_count:
    # Even the static-marker fallback failed to reduce — do not silently continue.
    log.error("Compaction made no progress (%d → %d); escalating to overflow recovery",
              token_count, tokens_after)
    return recover_overflow_history(ctx.deps, messages)   # hard reclaim, not another no-op pass
```

The static-marker path *should* always reduce; this guard turns a silent treadmill into an explicit
escalate-or-error. Optionally add a per-session consecutive-no-progress counter on `deps.runtime` that, on
N hits, forces overflow recovery once rather than re-deciding every request.

**Verified:** read `compaction.py:441-563` and the stop/return points; confirmed no max-pass guard exists
in production (only the eval's `_MAX_PROACTIVE_PASSES`).

---

## ISSUE-3 (Medium) — Prior summary marker survival is prompt-only, not structural

**Where.** `co_cli/context/compaction.py` — `is_compaction_marker` is imported and re-exported
(`compaction.py:44,76`) but **never called in the compaction path**; the marker is inserted at `head_end`
(in the droppable middle) and fed back into the summarizer on the next cycle; preservation rests on a soft
prompt line (`summarization.py:195`).

**What's wrong.** On compaction, the summary marker is inserted at index `head_end` — i.e. *after* the head,
inside the region that future `plan_compaction_boundaries` calls treat as droppable (`dropped =
messages[head_end:tail_start]`). On the next cycle the prior marker sits in `dropped` and is passed back
into `summarize_dropped_messages`. The only thing telling the LLM to keep it is prompt text
(`summarization.py:195`: *"If a prior summary exists, integrate its content — do not discard it"*).
`is_compaction_marker` exists (`_compaction_markers.py:113`) precisely to identify these markers, but
**nothing in the compaction path uses it** to protect the prior marker.

**Failure scenario.** Over multiple compaction cycles, the summarizer is cap-bounded
(`SUMMARY_BUDGET_CEIL`) and focus-steered (60–70% of budget toward the focus topic,
`summarization.py:303-304`). Each cycle it may drop or truncate the prior recap. Cross-compaction memory
(the thing the config docstring claims "lives in the iterative summary marker") erodes silently — there is
no structural guarantee it survives, only a hope. This is the most likely root cause if TASK B's coherence
probe fails for a *pre-first-compaction* fact.

**Concrete fix.** Protect the most-recent marker structurally rather than by prompt. Preferred: when planning
boundaries, detect a prior compaction marker in the dropped region via `is_compaction_marker` and **pin it**
(treat it as part of the protected head, so it is never re-summarized). Alternative: pass the prior marker's
content to `summarize_dropped_messages` as a **separate protected prefix** that is concatenated verbatim
into the new summary's `## Critical Context`, not left to the LLM to "integrate":

```python
# in proactive_window_processor, before summarizing the dropped region
prior = next((m for m in dropped if _msg_is_marker(m)), None)   # uses is_compaction_marker
carry = extract_marker_body(prior) if prior else ""
new_summary = summarize_dropped_messages(remaining_dropped, focus=..., carry_forward=carry)
```

**Verified:** `grep is_compaction_marker co_cli/context/` → defined `_compaction_markers.py:113`, only
imported/re-exported in `compaction.py:44,76`, **zero call sites in the compaction path**.

---

## ISSUE-4 (Low–Medium) — Spill aggregate accumulator drifts and drives the terminal decision

**Where.** `co_cli/context/history_processors.py:371` (per-item subtract), `:461` (writes
`current_request_tokens_estimate`), `:481` (`effective_after` for the terminal `fallback_to_summarize`
gate).

**What's wrong.** The L2 spill loop maintains a token accumulator by subtracting a **per-item
floor-divided** char delta:

```python
# history_processors.py:371
aggregate -= (len(old_content) - len(new_content)) // CHARS_PER_TOKEN
```

`aggregate` starts at `estimate_message_tokens(messages)`, which floor-divides the **total** char count by
4 **once**. Subtracting `delta // 4` **per item** discards up to 3 chars of remainder per spilled part, so
after N spills the accumulator can be up to N tokens away from a true recount. The spill processor's own
`below_threshold` decision and the `effective_after` it reports/uses for the `fallback_to_summarize` gate
(`:481`) are based on this drifted value. (L3 re-reads a fresh `effective_request_tokens` at
`compaction.py:453`, so the *next* layer is self-correcting — but L2's own terminal classification is not.)

**Failure scenario.** A spill that actually brought the payload under threshold can be misclassified as
still-over (or vice-versa) by up to N tokens, mislabeling the pass and possibly handing off to summarize
when the spill already fit (or the reverse). Bounded by candidate count, so small in practice — but it is
the basis of a terminal decision.

**Concrete fix.** Accumulate char deltas as an int and divide once, or recount fresh after the loop for the
terminal decision:

```python
# accumulate chars, divide once
chars_freed = 0
for part in ...:
    ...
    chars_freed += len(old_content) - len(new_content)
aggregate = starting_tokens - chars_freed // CHARS_PER_TOKEN
# OR, for the terminal classification, recount the rewritten list:
effective_after = effective_request_tokens(deps, rewritten_messages)
```

**Verified:** read `history_processors.py:345-373`, `:455-500`; `CHARS_PER_TOKEN` floor-division confirmed
in `summarization.py:65`.

---

## ISSUE-5 (Low–Medium) — Proactive focus can latch onto a compaction-marker's boilerplate

**Where.** `co_cli/context/compaction.py:413-418` (`_resolve_proactive_focus`).

**What's wrong.** Focus resolution walks messages in reverse for the most recent `UserPromptPart`, with **no
`is_compaction_marker` filter**:

```python
# compaction.py:413-418
for msg in reversed(messages):
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if isinstance(part, UserPromptPart):
                return part.content[-200:]
```

But the inserted summary marker and the todo-snapshot marker are themselves `ModelRequest` /
`UserPromptPart` (`_compaction_markers.py:53-66,144-155`). During a thrash sequence where compaction fires
before any new user input, the most recent `UserPromptPart` walking backward can be a just-inserted marker —
so `focus` becomes the **last 200 chars of marker boilerplate** ("…Recent messages are preserved
verbatim.") instead of the actual work, mis-steering the next summary's 60–70% focus weight.

**Concrete fix.** Skip markers in the walk:

```python
from co_cli.context._compaction_markers import is_compaction_marker
...
for msg in reversed(messages):
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if isinstance(part, UserPromptPart) and not is_compaction_marker(part.content):
                return part.content[-200:]
```

**Verified:** read `compaction.py:405-420`; marker shapes in `_compaction_markers.py`.

---

## ISSUE-6 (Low) — `spill_errors` conflates "too small to spill" with real I/O failure

**Where.** `co_cli/tools/tool_io.py:96-99` (the floor `force` cannot bypass); `history_processors.py:363-365`
(`spill_errors += 1`).

**What's wrong.** `spill_if_oversized(..., force=True)` only bypasses the `SPILL_THRESHOLD_CHARS` (4000)
check; it does **not** bypass the `TOOL_RESULT_PREVIEW_CHARS` (1500) floor:

```python
# tool_io.py:96-99
if not force and len(content) <= SPILL_THRESHOLD_CHARS:
    return content
if len(content) <= TOOL_RESULT_PREVIEW_CHARS:     # 1500 — force does NOT bypass this
    return content
```

So any candidate ≤1500 chars returns unchanged, and the spill loop counts it as a failure:

```python
# history_processors.py:363-365
if new_content == old_content:
    spill_errors += 1
    continue
```

**Failure scenario.** A transcript with many small-but-aggregately-large tool returns reports a misleading
`spill_errors` count (every too-small candidate looks like an I/O error) in the terminal span
(`request.spill_errors`), corrupting diagnosis of real spill failures. Not a correctness failure of the trim
itself — observability only.

**Concrete fix.** Pre-filter candidates by spillable size, or split the counter:

```python
# only attempt candidates that CAN spill
spillable = [p for p in spillable if len(p.content) > TOOL_RESULT_PREVIEW_CHARS]
# OR track separately:
if len(old_content) <= TOOL_RESULT_PREVIEW_CHARS:
    too_small += 1; continue   # not an error
```

**Verified:** read `tool_io.py:80-105`, `history_processors.py:353-373`.

---

## ISSUE-7 (Low) — `current_request_tokens_estimate` stale after an L3 pass

**Where.** Written at `history_processors.py:461` (post-spill); read for the status line at `main.py:467`
and as a span attribute at `compaction.py:488`. **Never updated after an L3 proactive compaction applies.**

**What's wrong.** L2 spill writes `deps.runtime.current_request_tokens_estimate = tokens_after`. The
proactive (L3) processor reads it only as a telemetry span attribute (`compaction.py:488`, `or -1`); it
recomputes the trigger fresh and **does not write the field back** after compacting. So for the rest of the
turn, `main.py:467`'s status line shows the **pre-compaction** spill estimate, not the compacted size.

**Failure scenario.** Cosmetic: the live token/context status display overstates usage after an L3 pass
fires. No behavioral impact (the trigger always recomputes fresh).

**Concrete fix.** Update the field when proactive compaction applies its result:

```python
# where proactive_window_processor returns the compacted messages
deps.runtime.current_request_tokens_estimate = tokens_after
```

**Verified:** `grep current_request_tokens_estimate co_cli/` → write at `history_processors.py:461`, reads at
`main.py:467` + `compaction.py:488`, resets in `commands/clear.py`/`new.py`; no L3 write-back.

---

## Disposition

These are **production-logic** findings, separate from the eval rewrite that surfaced them (per the eval
real-world-data and no-eval-test-driven-api rules, the eval was not shaped around them). Recommend a
dedicated fix plan, prioritizing **ISSUE-2** (no-progress escalation) and **ISSUE-3** (structural marker
preservation) — together they are the highest-value pair: production has no hard convergence guard *and*
relies on prompt text to preserve cross-compaction memory, so a pathological transcript can thrash
indefinitely while the summary erodes. **ISSUE-1** should be resolved alongside TASK B since it changes the
meaning of the `tail_fraction` lever the coherence gate tunes.
