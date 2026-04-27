# Plan: Compaction Workflow Audit Fixes

**Task type:** bug-fix bundle (multi-issue audit follow-up)

**Source:** Third-party audit of the compaction workflow on 2026-04-27, grounded in source code. Findings span `co_cli/context/`, `co_cli/tools/tool_io.py`, and the orchestration paths in `orchestrate.py` / `commands/session.py`. Each finding cites file:line.

---

## Context

The compaction system is well-structured and the high-level invariants hold (one summarizer call per pressure event, last turn group always retained, fail-open on processor errors). But a source-grounded audit surfaced two **real logical bugs** that produce wrong behavior under normal multi-turn operation, plus a cluster of **edge-case risks** and **dead code**.

The two real bugs are user-visible:

1. **Stale provider input_tokens leak across turn boundaries** — at the start of turn N+1 after a compaction in turn N, `latest_response_input_tokens(messages)` returns the pre-compaction count from the preserved tail. With `compaction_applied_this_turn` reset by `reset_for_turn()`, `max(local, reported)` picks the stale figure → unnecessary compaction fires → low savings → `consecutive_low_yield_proactive_compactions` increments. Two consecutive turn boundaries can trip the anti-thrash gate (`proactive_thrash_window: 2` default).

2. **`_check_output_limits` ratio is malformed** — `turn_usage.input_tokens` is the **sum** across segments (per `pydantic-ai/usage.py:215-220` `RunUsage.incr` adds), but the ratio compares it against the per-request `effective_num_ctx`. Multi-segment turns produce false-positive "Context limit reached" warnings (e.g., 5 segments × 30K = 150K against 100K ctx → ratio 1.5).

The remaining items are smaller surface area — a dedup→truncate ordering hazard that breaks back-reference fidelity, a static-marker prefix collision that pollutes summarizer enrichment, an orphan-tool-return assumption that depends on undocumented SDK behavior, and one block of dead code (`_anchor_tail_to_last_user`) that the planner can never reach.

This plan bundles all confirmed findings into a single delivery so the compaction subsystem's invariants and observability are tightened in one cycle.

---

## Problem & Outcome

**Problem.** Six sites in the compaction stack break their own contracts:

| # | Sev | Site | Symptom |
|---|---|---|---|
| 1 | 🔴 | `compaction.py:399-407` | Stale `latest_response_input_tokens` triggers spurious compaction at every turn boundary after a compaction. Risk: anti-thrash gate trips after 2 turn boundaries. |
| 2 | 🔴 | `orchestrate.py:486-507` | Cumulative `turn_usage.input_tokens` divided by per-request ctx window produces ratio > 1.0 on multi-segment turns. False-positive "Context limit reached" status. |
| 3 | 🟡 | `_history_processors.py:127-160` + `:222-254` | Dedup picks `latest_id` from `messages[:boundary]`, then truncate may clear that very call_id. Back-reference message points to a marker, not the live content. |
| 4 | 🟡 | `_compaction_markers.py:142-154` + `:42-57`, `:60-81` | `static_marker` and `summary_marker` share `SUMMARY_MARKER_PREFIX`. `_gather_prior_summaries` matches static markers (which contain no summary text) as "Prior summary". Pollutes enrichment when `previous_compaction_summary is None`. |
| 5 | 🟡 | `compaction.py:189-202` | `_preserve_search_tool_breadcrumbs` emits orphan `ToolReturnPart`s (no paired `ToolCallPart`). Correctness depends on undocumented pydantic-ai SDK filtering of `search_tools` returns; one SDK regression breaks the request shape. |
| 6 | 🟡 | `_history_processors.py:336-380` + `compaction.md:343` | M2b's `persist_if_oversized(max_size=0)` overrides the per-tool `file_read.max_result_size = math.inf` carve-out. The doc-stated invariant ("prevents persist→read→persist recursion") is reachable through M2b. |
| 7 | 🟡 | `compaction.py:230-231` | `previous_compaction_summary` is overwritten with raw LLM output without validation. Empty strings, refusals, or malformed responses persist into the iterative-update branch and compound across compactions. |
| 8 | 🟢 | `_compaction_boundaries.py:130-151` | `_anchor_tail_to_last_user` is unreachable: `group_by_turn` always splits at the last `UserPromptPart`, and `_MIN_RETAINED_TURN_GROUPS=1` guarantees the last group is in `acc_groups`, so `tail_start ≤ last_user_idx` always. Anchor condition is impossible. |

**Outcome.**

- Compaction does not fire on a turn boundary just because the prior turn's last `ModelResponse` carries a stale `input_tokens` figure. The anti-thrash counter only increments on genuine low-yield runs.
- The post-turn user-facing "Context X% full" / "Context limit reached" status reflects the most recent request size, not the multi-segment sum.
- Dedup back-references either point to a live source or are not emitted at all — no broken pointers.
- Static markers do not masquerade as prior summaries in summarizer enrichment.
- `search_tools` breadcrumb preservation either includes the paired `ToolCallPart` or converts to a structurally-normal user prompt — does not depend on SDK filtering of orphan tool returns.
- M2b honors `file_read`'s `math.inf` carve-out (or the invariant is dropped from the doc).
- `previous_compaction_summary` is only persisted when the LLM output passes a minimal sanity check (non-empty after strip, contains at least one `## ` section header).
- Dead code is removed.

No threshold tuning, no behavioral changes to the boundary planner's bounds output, no changes to `compaction_ratio` / `tail_fraction` / `min_proactive_savings`. The fixes are localized to specific sites; the overall mechanism graph in `compaction.md` Diagram 1 is unchanged.

---

## Scope

**In:**

- `co_cli/deps.py` — add `provider_tokens_stale: bool = False` to `CoRuntimeState`'s cross-turn fields. Cleared inside the next M3 trigger pass after a fresh `ModelResponse` has been observed (i.e., when `latest_response_input_tokens` reflects post-compaction context size).
- `co_cli/context/compaction.py` — set `provider_tokens_stale = True` in `apply_compaction` (alongside `compaction_applied_this_turn = True`). Update the M3 trigger to suppress the reported count while either flag is set, and clear the cross-turn flag on a fresh trigger pass when no compaction is needed.
- `co_cli/context/orchestrate.py` — fix the ratio computation in `_check_output_limits` to use `latest_response_input_tokens(turn_state.current_history)` (or the latest segment usage) rather than `deps.runtime.turn_usage.input_tokens`.
- `co_cli/context/_history_processors.py` — change `_build_latest_id_by_key` to scan the **full** message list, so `latest_id` resolves to a live source (in tail or in the kept-recent-5 window). Add a guard in `replacement_for` so back-references are only emitted when `latest_id_by_key` will survive truncation.
- `co_cli/context/_compaction_markers.py` — introduce a distinct `STATIC_MARKER_PREFIX` for `static_marker`'s output and update `_gather_prior_summaries` to match only on `summary_marker` output. `is_cleared_marker` and downstream prefix detection updated symmetrically.
- `co_cli/context/compaction.py` — `_preserve_search_tool_breadcrumbs` rebuilds each surviving `ModelRequest` with both the matching `ToolCallPart`s (lifted from the corresponding `ModelResponse` in dropped) and `ToolReturnPart`s. Pairing preserved → no orphan returns. Fallback: when call/return pairing cannot be found, drop the breadcrumb rather than emit an orphan.
- `co_cli/context/_history_processors.py` — `enforce_batch_budget` skips `file_read` parts when assembling the spill candidate list, preserving the M1 invariant. (Alternative: drop the doc invariant and accept M2b spill on file_read; **TL chooses the first option** so the `compaction.md` table on per-tool `max_result_size` overrides remains accurate.)
- `co_cli/context/compaction.py` — wrap the `previous_compaction_summary` write in a minimal validator (non-empty after `.strip()` AND contains at least one line starting with `## `). On fail, leave the field untouched; surface a `log.warning` so observability captures it.
- `co_cli/context/_compaction_boundaries.py` — delete `_anchor_tail_to_last_user` and the call site at line 207-208. Add an inline comment in the planner explaining why the latest user prompt is structurally guaranteed to land in `acc_groups[0]`.
- Tests for each fix:
  - `tests/context/test_context_compaction.py` — turn-boundary stale-token suppression, ratio-check uses single-request count.
  - `tests/context/test_dedup_tool_results.py` — back-reference always points to a live (post-truncate) call_id.
  - `tests/context/test_tool_result_markers.py` — `static_marker` no longer matches `_gather_prior_summaries`; `is_cleared_marker` predicate stays correct.
  - `tests/context/test_history.py` — `_preserve_search_tool_breadcrumbs` emits paired call/return; planner has no `_anchor_tail_to_last_user` reference.
- `docs/specs/compaction.md` — `/sync-doc` after delivery to update Section 2 (Core Logic) for the new stale-flag, the validator, and the `_preserve_search_tool_breadcrumbs` pairing. The "first-turn overflow" deferred note in `## Product Intent` is unchanged.
- `pyproject.toml` patch-version bump (odd = bug-fix, +1).

**Out:**

- No threshold tuning. `compaction_ratio = 0.65`, `tail_fraction = 0.20`, `min_proactive_savings = 0.10`, `proactive_thrash_window = 2` all stay.
- No changes to circuit breaker logic. Issue #9 (clarifying probe cadence at count=3) is purely a code comment — folded into TASK-1's incidental cleanup, not its own task.
- No multi-shot overflow recovery (Issue #10). Acceptable design choice; the "single-shot per turn" comment in `compaction.md` Section 2.5 already documents it.
- No changes to `last_overbudget_batch_signature`'s lifecycle (Issue #12). `tool_call_id`s are unique per call so the de-dup is structurally safe even without explicit reset.
- No changes to the iterative-update prompt (`summarization.py`). Validation only; prompt template unchanged.
- No M0 / M1 design changes. M2b skipping `file_read` is a small carve-out, not a redesign.

---

## Behavioral Constraints

- **No regression in compaction firing under genuine pressure.** All TASK-1 changes operate ONLY on the stale-token suppression path; the local-estimate trigger remains unchanged. If `estimate_message_tokens(messages) > threshold`, M3 still fires regardless of the new flag.
- **No regression in turn-state isolation.** The new `provider_tokens_stale` cross-turn flag is reset on session reset (`/new`, `/clear`) the same way `previous_compaction_summary` is.
- **No regression in static-marker → summary-marker compatibility.** Old session transcripts loaded via `/resume` may contain markers with the old shared `SUMMARY_MARKER_PREFIX`. The new prefixes do not collide with the old prefix; old markers are still recognized as "summary-shaped" by the marker construction code paths, and the new `_gather_prior_summaries` discriminator falls through gracefully (treats unknown-prefix as summary, not as static — safer error direction).
- **No silent breakage of pydantic-ai `search_tools`.** TASK-5's pairing fix must be reviewed against `pydantic-ai/_agent_graph.py` to confirm paired `ToolCallPart`/`ToolReturnPart` survive history processors. Fallback-drop-rather-than-orphan is the default if pairing fails, which is strictly safer than the current behavior.
- **Validator is permissive on non-English summaries.** TASK-7's check is "at least one line starts with `## `" — works for any language since the section markers themselves are ASCII.
- **All fail-open paths preserved.** Every TASK keeps `try/except` wrappers (or adds them) at the same boundaries as today: summarizer raises → static marker fallback; M2 processor errors → return messages unchanged; extraction failures → compaction continues.

---

## High-Level Design

### Stale-token cross-turn suppression (Issue 1 → TASK-1)

Today's logic relies on a per-turn flag:

```
reported = 0 if compaction_applied_this_turn else latest_response_input_tokens(messages)
```

`compaction_applied_this_turn` is reset at every `run_turn()` entry. But the *staleness* of the preserved tail's last `ModelResponse.usage.input_tokens` outlives the turn boundary — it's structural, not turn-scoped.

Replace with a **cross-turn estimate** that stores the local post-compaction token count and uses it as the `reported` value until a real provider count arrives (hermes-agent approach):

```text
On apply_compaction success:
    runtime.post_compaction_token_estimate = estimate_message_tokens(result)
    runtime.message_count_at_last_compaction = len(result)

In proactive_window_processor (top of trigger):
    if compaction_applied_this_turn:
        reported = 0                                # same as today
    elif post_compaction_token_estimate is not None:
        count = message_count_at_last_compaction
        if count is not None and len(messages) >= count + 2:
            # Fresh ModelResponse has landed — clear estimate, use real count
            runtime.post_compaction_token_estimate = None
            runtime.message_count_at_last_compaction = None
            reported = latest_response_input_tokens(messages)
        else:
            reported = post_compaction_token_estimate
    else:
        reported = latest_response_input_tokens(messages)
```

`post_compaction_token_estimate` is a local char-based estimate of the post-compaction message list — the same signal that `estimate_message_tokens` already feeds into `token_count` on the local side. Substituting it for `reported` prevents `max(local, stale_reported)` from picking the stale pre-compaction figure. Once `len(messages) >= count + 2` (one new ModelRequest + one new ModelResponse have landed), the estimate is cleared and the trigger falls through to the real provider count.

This matches hermes-agent's approach (`run_agent.py:7325-7332`): after compression, overwrite the stored token count with a fresh local estimate; let the next real API response take over naturally.

### Cumulative-vs-single ratio fix (Issue 2 → TASK-2)

Replace:

```python
ratio = deps.runtime.turn_usage.input_tokens / effective_ctx
```

with:

```python
latest_input = latest_response_input_tokens(turn_state.current_history)
ratio = latest_input / effective_ctx if latest_input > 0 else 0.0
```

This uses the same helper that the M3 trigger uses, so the post-turn nudge and the in-turn trigger see the same view of "current request size." The trigger's `max(local, reported)` does not apply here — at post-turn check time, the local estimate is irrelevant; we want the provider's most recent count.

If `latest_input == 0` (local/custom model with no usage reporting), skip the ratio nudge entirely — current behavior already does the right thing because the existing path checks `deps.runtime.turn_usage is not None` AND `deps.config.llm.supports_context_ratio_tracking()`.

### Dedup latest-id correctness (Issue 3 → TASK-3)

Today: `latest_id_by_key = _build_latest_id_by_key(messages[:boundary])`. The "latest" is in pre-tail scope; truncate may clear it.

Fix (two-step):

1. Scan the **full** message list (not `messages[:boundary]`) when building `latest_id_by_key`. The latest occurrence is now genuinely the latest — protected tail copies are preferred when they exist.
2. In `replacement_for`, only emit a back-reference when the recorded `latest_id` either lives in the protected tail OR sits within the keep-recent-5 window for that tool. Otherwise, pass through unchanged (let truncate's semantic marker handle it).

Step 2 requires knowing the keep-recent-5 set, which `truncate_tool_results` computes via `_build_keep_ids`. To keep dedup standalone (it currently runs *before* truncate), compute a small "is `latest_id` durable" check inline: walk forward over `messages[:boundary]` and count occurrences per tool from the most recent backwards; the first 5 are durable. This is O(n) per dedup run, same complexity as today.

Alternative considered: invert the order (truncate first, then dedup). Rejected because semantic markers carry tool-specific size/outcome signal that dedup would lose if it ran on already-truncated content.

### Static-marker prefix discriminator (Issue 4 → TASK-4)

Add:

```python
STATIC_MARKER_PREFIX = "[CONTEXT COMPACTION — STATIC MARKER] "
SUMMARY_MARKER_PREFIX = "[CONTEXT COMPACTION — REFERENCE ONLY] "
```

`static_marker` produces content starting with `STATIC_MARKER_PREFIX + "..."`. `summary_marker` keeps `SUMMARY_MARKER_PREFIX + "..."`. `_gather_prior_summaries` matches only on `SUMMARY_MARKER_PREFIX`.

For backward compatibility on resumed sessions: keep `SUMMARY_MARKER_PREFIX` matching as a superset detector for "any compaction marker exists" via a new `is_compaction_marker(content)` helper that returns True for either prefix. Existing call sites that were doing `startswith(SUMMARY_MARKER_PREFIX)` for "is this a compaction marker?" (e.g., session-loading code, if any) call the new helper instead; sites that specifically want "this is a summary, not a static fallback" use `startswith(SUMMARY_MARKER_PREFIX)` only.

`is_cleared_marker` (in `_tool_result_markers.py`) is unchanged — it's already focused on tool-result clearing markers, not compaction markers.

### Search-tools breadcrumb pairing (Issue 5 → TASK-5)

Today: `_preserve_search_tool_breadcrumbs` emits orphan `ToolReturnPart`s. Fix the function to:

1. Walk dropped messages with a one-message lookback for the paired `ModelResponse` (which contains the `ToolCallPart` for that `ToolReturnPart`).
2. For each `search_tools` `ToolReturnPart` found in `dropped`, find the matching `ToolCallPart` in dropped's preceding `ModelResponse` (matched by `tool_call_id`).
3. Emit a paired sequence: `ModelResponse(parts=[ToolCallPart(...)])` followed by `ModelRequest(parts=[ToolReturnPart(...)])`.
4. If the call cannot be found (defensive — should not happen in practice), drop that breadcrumb rather than emit an orphan.

### M2b file_read carve-out (Issue 6 → TASK-6)

In `enforce_batch_budget`'s candidate filter (line 345-349), add `part.tool_name != "file_read"` to the filter:

```python
candidates = [
    (msg_idx, part_idx, part)
    for msg_idx, part_idx, part in batch_parts
    if isinstance(part.content, str)
    and PERSISTED_OUTPUT_TAG not in part.content
    and part.tool_name != "file_read"
]
```

If the batch is over budget AND all over-budget content is `file_read` returns, `enforce_batch_budget` returns messages unchanged with the existing "still over budget" warning. Acceptable: the model has already received the content; the next M3 pass can compact the surrounding context if pressure persists.

### Summary validator (Issue 7 → TASK-7)

In `apply_compaction`, between the summarizer call and the field write:

```python
def _is_valid_summary(text: str | None) -> bool:
    if text is None:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(line.startswith("## ") for line in stripped.splitlines())

if summary_text is not None and _is_valid_summary(summary_text):
    ctx.deps.runtime.previous_compaction_summary = summary_text
elif summary_text is not None:
    log.warning("Compaction summarizer returned malformed output (no '## ' sections); leaving previous_compaction_summary untouched.")
    summary_text = None  # downgrades the marker to static path below
marker = build_compaction_marker(dropped_count, summary_text)
```

This routes garbage outputs into the static-marker path AND prevents `previous_compaction_summary` corruption. The downgrade is observable via the existing `compaction_skip_count` mechanism if the validation fails repeatedly (treat as a summarizer failure).

### Dead-code removal (Issue 8 → TASK-8)

Delete `_anchor_tail_to_last_user` and `_find_last_turn_start`'s second consumer (`_anchor_tail_to_last_user` is the only caller besides `dedup_tool_results` and `truncate_tool_results`). Remove the call site at `plan_compaction_boundaries:207-208`. Add a one-line comment at the spot the call lived:

```python
# Last group always retained by _MIN_RETAINED_TURN_GROUPS=1 invariant —
# active-user anchoring is structurally guaranteed.
```

`_find_last_turn_start` itself stays — `dedup_tool_results` and `truncate_tool_results` still use it for their tail boundary.

---

## Implementation Plan

### TASK-1 — Stale provider-tokens cross-turn suppression

**Files:**
- `co_cli/deps.py` (add fields to `CoRuntimeState`)
- `co_cli/context/compaction.py` (set flag in `apply_compaction`; consult flag in `proactive_window_processor`)
- `co_cli/commands/session.py` (`/new` and `/clear` reset the new flag — same lifecycle as `previous_compaction_summary`)
- `tests/context/test_context_compaction.py` (turn-boundary suppression test)

**Detailed change:**

1. `co_cli/deps.py:117` `CoRuntimeState`: add two fields in the cross-turn block:
   ```python
   post_compaction_token_estimate: int | None = None
   message_count_at_last_compaction: int | None = None
   ```
2. `co_cli/context/compaction.py` `apply_compaction` line 245 (after the existing `compaction_applied_this_turn = True`):
   ```python
   ctx.deps.runtime.post_compaction_token_estimate = estimate_message_tokens(result)
   ctx.deps.runtime.message_count_at_last_compaction = len(result)
   ```
   Same two lines added to `emergency_recover_overflow_history` after its existing `compaction_applied_this_turn = True` (line 331). `estimate_message_tokens` is already imported in this file.
3. `co_cli/context/compaction.py` `proactive_window_processor` line 399-403, replace with:
   ```python
   if ctx.deps.runtime.compaction_applied_this_turn:
       reported = 0
   elif ctx.deps.runtime.post_compaction_token_estimate is not None:
       _count = ctx.deps.runtime.message_count_at_last_compaction
       if _count is not None and len(messages) >= _count + 2:
           # New ModelRequest + ModelResponse have landed — fresh provider count available.
           ctx.deps.runtime.post_compaction_token_estimate = None
           ctx.deps.runtime.message_count_at_last_compaction = None
           reported = latest_response_input_tokens(messages)
       else:
           reported = ctx.deps.runtime.post_compaction_token_estimate
   else:
       reported = latest_response_input_tokens(messages)
   ```
4. `co_cli/commands/session.py` `_cmd_clear` and `_cmd_new`: add reset of `post_compaction_token_estimate = None` and `message_count_at_last_compaction = None` next to the existing `previous_compaction_summary = None` reset.

**Test:**
- Construct a 3-turn message history with a final `ModelResponse(usage=RequestUsage(input_tokens=80_000))` representing pre-compaction state.
- Apply compaction (mock or real); assert `post_compaction_token_estimate` is set to the local estimate after.
- Run `proactive_window_processor` again immediately (simulating turn N+1 first ModelRequestNode entry). Assert `reported` equals the stored estimate (not the stale 80_000).
- Append a fresh `ModelRequest(UserPromptPart)` + `ModelResponse(usage=20_000)`. Run trigger again. Assert estimate is now cleared and `reported = 20_000`.

```text
files:
  - co_cli/deps.py
  - co_cli/context/compaction.py
  - co_cli/commands/session.py
  - tests/context/test_context_compaction.py

done_when:
  grep -n "post_compaction_token_estimate" co_cli/deps.py exits 0
  grep -n "post_compaction_token_estimate" co_cli/context/compaction.py exits 0
  grep -n "post_compaction_token_estimate = None" co_cli/commands/session.py exits 0
  uv run pytest tests/context/test_context_compaction.py -x passes
```

---

### TASK-2 — Fix `_check_output_limits` ratio computation

**Files:**
- `co_cli/context/orchestrate.py` (`_check_output_limits`, line 470-507)
- `tests/context/test_context_compaction.py` (multi-segment ratio test)

**Detailed change:**

Replace lines 486-507 with logic that uses `latest_response_input_tokens(turn_state.current_history)` for the ratio:

```python
latest_input = latest_response_input_tokens(turn_state.current_history)
if (
    latest_input > 0
    and deps.config.llm.supports_context_ratio_tracking()
):
    effective_ctx = deps.config.llm.effective_num_ctx()
    ratio = latest_input / effective_ctx
    with _TRACER.start_as_current_span("ctx_overflow_check") as ctx_span:
        ctx_span.set_attribute("ctx.input_tokens", latest_input)
        ctx_span.set_attribute("ctx.num_ctx", effective_ctx)
        ctx_span.set_attribute("ctx.ratio", ratio)
        if ratio >= 1.0:
            frontend.on_status(
                f"Context limit reached ({latest_input:,} / {effective_ctx:,} tokens)"
                " — prompt may have been truncated. Use /compact or /new."
            )
        elif ratio >= deps.config.compaction.compaction_ratio:
            thrash_count = deps.runtime.consecutive_low_yield_proactive_compactions
            if thrash_count >= deps.config.compaction.proactive_thrash_window:
                frontend.on_status(
                    f"Context {ratio:.0%} full ({latest_input:,} / {effective_ctx:,} tokens)."
                    " Auto-compaction paused — try /compact for one more pass or /new for a fresh session."
                )
```

Add `from co_cli.context.summarization import latest_response_input_tokens` to the imports if not already present (it is imported indirectly via the compaction module today; make it explicit).

**Test:**
- Construct a turn with 3 segments: ModelResponse(usage=40K), ModelResponse(usage=45K), ModelResponse(usage=50K). Sum = 135K.
- Set effective_ctx = 100K.
- Run `_check_output_limits`. Assert no "Context limit reached" status emitted (because latest_input = 50K < 100K).
- Now construct a single-segment turn with ModelResponse(usage=120K). Assert "Context limit reached" IS emitted.

```text
files:
  - co_cli/context/orchestrate.py
  - tests/context/test_context_compaction.py

done_when:
  grep -n "turn_usage.input_tokens / effective_ctx" co_cli/context/orchestrate.py returns empty
  grep -n "latest_response_input_tokens(turn_state.current_history)" co_cli/context/orchestrate.py exits 0
  uv run pytest tests/context/ -x passes
```

---

### TASK-3 — Dedup back-reference durability

**Files:**
- `co_cli/context/_history_processors.py` (`_build_latest_id_by_key`, `dedup_tool_results`)
- `tests/context/test_dedup_tool_results.py` (durability test)

**Detailed change:**

1. `_build_latest_id_by_key` accepts the **full** messages list, not `messages[:boundary]`. Update the call site in `dedup_tool_results` line 150:
   ```python
   latest_id_by_key = _build_latest_id_by_key(messages)
   ```
2. Add a small helper `_durable_compactable_ids(messages, boundary)` that returns the set of `id(ToolReturnPart)` that will survive `truncate_tool_results` — i.e., parts in `messages[boundary:]` (tail-protected) plus the keep-recent-5 set from `_build_keep_ids(messages[:boundary])`. Reuse `_build_keep_ids` directly.
3. In `dedup_tool_results.replacement_for`, only emit a back-reference when the `latest_id`'s corresponding ToolReturnPart is in the durable set:
   ```python
   def replacement_for(part: ToolReturnPart) -> ToolReturnPart | None:
       if not is_dedup_candidate(part):
           return None
       latest_id = latest_id_by_key.get(dedup_key(part))
       if latest_id is None or latest_id == part.tool_call_id:
           return None
       if latest_id not in durable_call_ids:
           return None
       return build_dedup_part(part, latest_id)
   ```
   `durable_call_ids` is computed once outside the closure as a set of `tool_call_id` values (not `id(part)`), since the back-reference uses `tool_call_id`. Build it from the durable parts collected in step 2.

**Test:**
- Construct a history with the same shell content appearing at indices 5, 10, 15, 20, 25, 30, 35 (7 calls) and a tail starting at index 40. With `COMPACTABLE_KEEP_RECENT=5`, the keep set is indices 15-35.
- Index 5 and 10 are pre-tail and not in keep-set → would be cleared by truncate.
- Assert: dedup does NOT emit back-references for them (because their `latest_id` would be one of the keep-set indices, but the test should verify the durability check fires).
- Add a contrasting test: identical content at index 30 (kept) and index 40 (tail). Index 30's back-reference should be emitted pointing at index 40.

```text
files:
  - co_cli/context/_history_processors.py
  - tests/context/test_dedup_tool_results.py

done_when:
  uv run pytest tests/context/test_dedup_tool_results.py -x passes
```

---

### TASK-4 — Static / summary marker prefix discriminator

**Files:**
- `co_cli/context/_compaction_markers.py` (new `STATIC_MARKER_PREFIX`, update `static_marker`, update `_gather_prior_summaries`, add `is_compaction_marker` helper)
- `co_cli/context/compaction.py` (update re-exports)
- Any call sites that used `startswith(SUMMARY_MARKER_PREFIX)` to mean "is this a compaction marker?" — switch to `is_compaction_marker`.
- `tests/context/test_history.py` (verify static marker not picked up by enrichment)
- `tests/context/test_tool_result_markers.py` (no change expected; spot-check `is_cleared_marker` not affected)

**Detailed change:**

1. Add `STATIC_MARKER_PREFIX = "[CONTEXT COMPACTION — STATIC MARKER] "` at module top alongside `SUMMARY_MARKER_PREFIX`.
2. `static_marker` (line 42-57): replace `f"{SUMMARY_MARKER_PREFIX} ..."` with `f"{STATIC_MARKER_PREFIX} ..."`. Same body otherwise.
3. Add helper:
   ```python
   def is_compaction_marker(content: object) -> bool:
       """True for both summary_marker and static_marker outputs."""
       if not isinstance(content, str):
           return False
       return content.startswith(SUMMARY_MARKER_PREFIX) or content.startswith(STATIC_MARKER_PREFIX)
   ```
4. `_gather_prior_summaries` keeps using `startswith(SUMMARY_MARKER_PREFIX)` only — static markers no longer match.
5. Search the codebase for any other consumer of `SUMMARY_MARKER_PREFIX` that means "any compaction marker"; switch those to `is_compaction_marker`.
6. Re-export `STATIC_MARKER_PREFIX` and `is_compaction_marker` from `co_cli/context/compaction.py`'s `__all__`.

**Backward compatibility:** old transcripts loaded via `/resume` may have static markers using the old `SUMMARY_MARKER_PREFIX`. Those will be (incorrectly but harmlessly) classified as summary markers by the new code — they'll get embedded as `Prior summary:\n[CONTEXT COMPACTION — REFERENCE ONLY] N earlier messages were removed...`. Same noise as today; no regression.

**Test:**
- Construct a `static_marker(5)` output and a `summary_marker(5, "## Active Task\n...")` output.
- Run `_gather_prior_summaries([static_msg, summary_msg])`. Assert result contains the summary's content but not the static marker's text.
- Assert `is_compaction_marker` returns True for both, `startswith(SUMMARY_MARKER_PREFIX)` returns True only for the summary.

```text
files:
  - co_cli/context/_compaction_markers.py
  - co_cli/context/compaction.py
  - tests/context/test_history.py

done_when:
  grep -n "STATIC_MARKER_PREFIX" co_cli/context/_compaction_markers.py exits 0
  grep -nE "startswith\(SUMMARY_MARKER_PREFIX\)" co_cli/ | wc -l   # Should be ≤ 2 (one in _gather_prior_summaries, possibly one elsewhere we deliberately keep)
  uv run pytest tests/context/ -x passes
```

---

### TASK-5 — Pair search-tools breadcrumbs with their tool calls

**Files:**
- `co_cli/context/compaction.py` (`_preserve_search_tool_breadcrumbs`)
- `tests/context/test_history.py` (pairing test, fallback test)

**Detailed change:**

Replace `_preserve_search_tool_breadcrumbs` (line 189-202) with a paired-walk implementation:

```python
def _preserve_search_tool_breadcrumbs(
    dropped: list[ModelMessage],
) -> list[ModelMessage]:
    """Keep paired search_tools tool-call/return cycles across compaction boundaries."""
    # Index: tool_call_id → ToolCallPart from dropped ModelResponses.
    call_part_by_id: dict[str, ToolCallPart] = {}
    for msg in dropped:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == "search_tools":
                    call_part_by_id[part.tool_call_id] = part

    result: list[ModelMessage] = []
    for msg in dropped:
        if not isinstance(msg, ModelRequest):
            continue
        for part in msg.parts:
            if not (isinstance(part, ToolReturnPart) and part.tool_name == "search_tools"):
                continue
            call_part = call_part_by_id.get(part.tool_call_id)
            if call_part is None:
                # Defensive: orphan return without paired call. Drop.
                continue
            result.append(ModelResponse(parts=[call_part]))
            result.append(ModelRequest(parts=[part]))
    return result
```

`ToolCallPart` is already imported in this file. Add `ModelResponse` to the imports if not present.

**Test:**
- Construct dropped messages with a paired `ModelResponse(ToolCallPart(search_tools, id=A))` + `ModelRequest(ToolReturnPart(search_tools, id=A))`. Assert output has both, in order, with tool_call_id linkage preserved.
- Construct dropped messages with an orphan `ToolReturnPart(search_tools, id=B)` and no matching call. Assert output is empty (not an orphan).
- Assert non-search_tools ToolReturnParts are still dropped (existing behavior).

```text
files:
  - co_cli/context/compaction.py
  - tests/context/test_history.py

done_when:
  grep -n "call_part_by_id" co_cli/context/compaction.py exits 0
  uv run pytest tests/context/test_history.py -x passes
```

---

### TASK-6 — M2b file_read carve-out

**Files:**
- `co_cli/context/_history_processors.py` (`enforce_batch_budget`)
- `tests/context/test_context_compaction.py` (file_read excluded from spill candidates)

**Detailed change:**

In `enforce_batch_budget` line 345-349, add `part.tool_name != "file_read"` to the candidate filter. Update the inline docstring to note the carve-out is to preserve the M1 invariant.

**Test:**
- Construct a batch with one `file_read` ToolReturnPart of 250K chars. batch_spill_chars = 200K.
- Assert `enforce_batch_budget` returns messages unchanged.
- Assert the "still over budget" warning is emitted (existing logic).
- Add a contrasting test: batch with `shell` ToolReturnPart of 250K chars. Assert it IS spilled.

```text
files:
  - co_cli/context/_history_processors.py
  - tests/context/test_context_compaction.py

done_when:
  grep -n 'tool_name != "file_read"' co_cli/context/_history_processors.py exits 0
  uv run pytest tests/context/test_context_compaction.py -x passes
```

---

### TASK-7 — Summary validator before write-back

**Files:**
- `co_cli/context/compaction.py` (`apply_compaction` — add validator)
- `tests/context/test_context_compaction.py` (validator test)

**Detailed change:**

Add `_is_valid_summary` helper at module scope:

```python
def _is_valid_summary(text: str | None) -> bool:
    """Minimal sanity check: non-empty after strip AND has at least one '## ' section."""
    if text is None:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return any(line.startswith("## ") for line in stripped.splitlines())
```

In `apply_compaction` line 230-232, replace:
```python
if summary_text is not None:
    ctx.deps.runtime.previous_compaction_summary = summary_text
marker = build_compaction_marker(dropped_count, summary_text)
```
with:
```python
if summary_text is not None and not _is_valid_summary(summary_text):
    log.warning(
        "Compaction summarizer returned malformed output (no '## ' sections); "
        "downgrading to static marker."
    )
    summary_text = None
if summary_text is not None:
    ctx.deps.runtime.previous_compaction_summary = summary_text
marker = build_compaction_marker(dropped_count, summary_text)
```

**Test:**
- Mock `_gated_summarize_or_none` to return `""`. Assert `previous_compaction_summary` unchanged after `apply_compaction`. Assert resulting marker is a `static_marker` (matched via `STATIC_MARKER_PREFIX`).
- Mock to return `"I cannot summarize"`. Same assertions.
- Mock to return `"## Active Task\n[do thing]\n## Goal\n..."`. Assert `previous_compaction_summary` is set, marker is `summary_marker`.

```text
files:
  - co_cli/context/compaction.py
  - tests/context/test_context_compaction.py

done_when:
  grep -n "_is_valid_summary" co_cli/context/compaction.py exits 0
  uv run pytest tests/context/test_context_compaction.py -x passes
```

---

### TASK-8 — Remove dead `_anchor_tail_to_last_user`

**Files:**
- `co_cli/context/_compaction_boundaries.py` (delete function, remove call site, add comment)
- `tests/context/test_history.py` (remove any test that exercises the dead path; the planner's "active-user anchoring" cases stay if they assert structural invariants, drop if they assert calls into the deleted function)

**Detailed change:**

1. Delete `_anchor_tail_to_last_user` (lines 130-151).
2. Delete the call at line 207-208:
   ```python
   tail_start = _anchor_tail_to_last_user(messages, groups, head_end, tail_start)
   ```
3. In its place, add:
   ```python
   # Last group is always retained by _MIN_RETAINED_TURN_GROUPS=1: the walk
   # adds the final group unconditionally on its first iteration. Since
   # group_by_turn splits at every UserPromptPart, the latest user prompt
   # is structurally guaranteed to land in acc_groups[0] — no anchoring needed.
   ```
4. Verify tests in `tests/context/test_history.py` for "active-user anchoring" still pass; if any test was specifically testing `_anchor_tail_to_last_user`'s extension behavior (rather than the planner's overall correctness), delete those tests.

**Test:**
- Existing planner tests must still pass.
- Add an explicit assertion: the planner's output `tail_start <= last_user_idx` for any non-None bounds — this is the invariant the deleted function was redundantly enforcing.

```text
files:
  - co_cli/context/_compaction_boundaries.py
  - tests/context/test_history.py

done_when:
  grep -rn "_anchor_tail_to_last_user" co_cli/ tests/ returns empty
  uv run pytest tests/context/test_history.py -x passes
```

---

### TASK-9 — Sync `docs/specs/compaction.md`

**Files:**
- `docs/specs/compaction.md` (Section 2.3 "M3 — Window compaction" — add stale-flag mention; Section 2.5 "Overflow recovery" — note breadcrumb pairing; Section 2.6 "Summarizer" — note validator; Section 2.9 "Error handling" — add validator-failure row; Section 4 "Files" — no changes; Section 5 "Test Gates" — human-maintained, may grow)

**Detailed change:**

Run `/sync-doc` after TASK-1 through TASK-8 land. The skill auto-generates the inaccuracy fixes for sections 1-4. For Section 5 (Test Gates), append the new test rows manually (not auto-touched by `/sync-doc`):

| New property | Test file |
|---|---|
| Stale provider-token suppression across turn boundary | `tests/context/test_context_compaction.py` |
| `_check_output_limits` ratio uses single-request count | `tests/context/test_context_compaction.py` |
| Dedup back-references only point to durable call_ids | `tests/context/test_dedup_tool_results.py` |
| Static marker not matched by `_gather_prior_summaries` | `tests/context/test_history.py` |
| `_preserve_search_tool_breadcrumbs` emits paired call/return | `tests/context/test_history.py` |
| M2b skips `file_read` candidates | `tests/context/test_context_compaction.py` |
| Summary validator downgrades malformed output to static marker | `tests/context/test_context_compaction.py` |

Also update Section 2.5's overflow-recovery prose — "Mixed-batch requests are rebuilt with only those parts" is no longer accurate. Replace with: "Each `search_tools` `ToolReturnPart` in the dropped range is preserved together with its paired `ToolCallPart` from the corresponding `ModelResponse`; orphans (return without matching call) are dropped."

```text
files:
  - docs/specs/compaction.md

done_when:
  grep -n "stale provider-token" docs/specs/compaction.md exits 0
  grep -n "paired ToolCallPart" docs/specs/compaction.md exits 0
  grep -n "_anchor_tail_to_last_user" docs/specs/compaction.md returns empty
```

---

### TASK-10 — Version bump

**Files:**
- `pyproject.toml` (patch version +1; current is `0.8.30` per the latest commit `496f574`)
- `CHANGELOG.md` (add entry under the new version)

**Detailed change:**

Bump `version = "0.8.30"` → `"0.8.31"` (odd = bug-fix per the project's ship convention). CHANGELOG entry:

```markdown
## 0.8.31 — Compaction audit fixes

- Fix stale provider input_tokens leak across turn boundaries (could prematurely trip anti-thrash gate).
- Fix `_check_output_limits` ratio computation (multi-segment turns no longer produce false-positive overflow warnings).
- Tighten dedup back-reference durability — pointers now always resolve to a live source.
- Distinguish static and summary compaction markers — static markers no longer pollute summarizer enrichment.
- `_preserve_search_tool_breadcrumbs` emits paired tool-call/return cycles instead of orphan returns.
- M2b honors `file_read.max_result_size = math.inf` carve-out.
- Validate summarizer output before persisting to `previous_compaction_summary`.
- Remove dead `_anchor_tail_to_last_user` planner code.
```

```text
files:
  - pyproject.toml
  - CHANGELOG.md

done_when:
  grep -n '0.8.31' pyproject.toml exits 0
  grep -n '0.8.31' CHANGELOG.md exits 0
```

---

## Testing

**Per-task tests** as specified in each TASK section above.

**Cross-cutting integration test** (added to `tests/context/test_context_compaction.py`):

- Simulate a 3-turn session: turn 1 (no compaction), turn 2 (M3 fires), turn 3 (no compaction expected — stale-token suppression should hold).
  - Assert turn 3's first `proactive_window_processor` call returns messages unchanged.
  - Assert `provider_tokens_stale` is True at start of turn 3 and clears after the new ModelResponse lands.
  - Assert `consecutive_low_yield_proactive_compactions == 0` at end of turn 3 (no spurious compaction → no low-yield increment).

**Eval gate** (existing): `evals/eval_compaction_flow_quality.py` should pass without regression. If it fails, the failure mode (low-yield compaction at turn boundary) was likely papered over by the bug — investigate before bumping.

**Lint:** `scripts/quality-gate.sh full` from repo root. All TASKs must keep this green.

---

## Cycle Decisions (TL)

- **Bundle vs split:** all eight findings ship together in one plan. Splitting would multiply review overhead without reducing risk; the changes are localized and the test surface is shared (`test_context_compaction.py`, `test_history.py`).
- **Validator strictness (TASK-7):** chose "≥ 1 line starting with `## `" over "all expected sections present" because the latter would create false negatives on legitimate iterative-update outputs that omit empty sections (the prompt explicitly says "Skip sections that have no content — do not generate filler"). The minimal check rejects garbage without rejecting valid abbreviated summaries.
- **Stale-token implementation (TASK-1):** chose the length-based heuristic over an index-tracking approach. The index approach is more precise but adds a second cross-turn field whose update logic interacts with `enforce_batch_budget`'s message rebuilding. Length-based is sufficient because the trigger only needs "we've seen at least one fresh ModelResponse since compaction" — exact index isn't load-bearing.
- **Backward compatibility (TASK-4):** old transcripts with `SUMMARY_MARKER_PREFIX`-prefixed static markers will be misclassified as summary markers when resumed. Accepted: the misclassification is harmless (same noise as today's behavior), and the sessions written after this fix will use the correct prefix going forward.
- **Search-tools fix scope (TASK-5):** chose the paired-emit approach over "convert to UserPromptPart summary". The paired approach preserves the SDK's internal tool-discovery state mechanism (which is the whole point of breadcrumbs); converting to text would break SDK awareness of which skills/tools have already been searched. Trade-off: slightly more code, but contract-correct.
- **Test runs:** all pytest invocations use `-x` per the team's fail-fast policy (per `.claude/.../MEMORY.md` — "Fail fast: run pytest with -x flag so it stops at the first failure").

---

## Out of Scope (for follow-up plans, if anyone wants them)

- **Multi-shot overflow recovery.** Currently `overflow_recovery_attempted` is one-shot per turn. A second consecutive overflow within one turn is terminal. The audit flagged this as a design choice, not a bug — but a future plan could add a second tier that drops the active turn's pending tool returns when the first compaction wasn't enough.
- **Aggressive shape revisit.** With these fixes in place, `tail_fraction = 0.20` may be conservative. An eval-gated tuning plan (`evals/eval_compaction_flow_quality.py`) could explore lower values now that the iterative summary feedback loop is stable AND the trigger no longer fires spuriously at turn boundaries.
- **Per-tool override audit.** This plan touches the `file_read` carve-out for M2b. A broader review of all per-tool `max_result_size` settings against current workload patterns is a separate cycle.
- **Circuit-breaker probe cadence comment.** The audit finding (Issue 9) is purely a code comment polish — folded into TASK-1's incidental doc updates if it lands cheaply, otherwise dropped.
- **`last_overbudget_batch_signature` lifecycle.** Audit Issue 12: never-reset cross-turn field. Structurally safe (tool_call_ids are unique), but a session-boundary reset would be hygienic. Not load-bearing; deferred.
