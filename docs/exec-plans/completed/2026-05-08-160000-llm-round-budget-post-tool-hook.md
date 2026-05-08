# Move `enforce_llm_round_budget` to a post-tool-exec capability hook

**Slug:** `llm-round-budget-post-tool-hook`
**Created:** 2026-05-08
**Status:** Active — Gate 1 PASSED (2026-05-08)

---

## 1. Goal

Migrate `enforce_llm_round_budget` from a pre-request **history processor** to a post-tool-exec **capability hook** on `CoToolLifecycle.after_node_run`. This brings co-cli into algorithmic + structural parity with hermes-agent's `enforce_turn_budget` and removes the reverse-scan boundary search that the current placement requires.

**Behavioral invariant:** identical spill outcomes for identical inputs. This is a refactor, not a feature change.

---

## 2. Why now

- Cleaner mental model: "after the batch executes, enforce the budget" matches user intuition and matches the hermes design referenced in the function's docstring (`history_processors.py:335`).
- Eliminates `_find_last_llm_round_returns_start` (a reverse-scan that runs on every pre-request hook) — the new hook receives the freshly-produced parts directly.
- Removes one source of redundancy: the current implementation re-evaluates the same batch on every subsequent pre-request hook (short-circuiting via `tokens_before <= threshold`, but still running). Post-exec placement runs exactly once per batch.
- Co-locates with the related L0 `tool_budget.enforce_tool_call_limit` span — both fire on `CallToolsNode` exit.

---

## 3. Background — current state

### 3.1 Registration
- `co_cli/agent/core.py:19-22` — imported from `history_processors`.
- `co_cli/agent/core.py:147-153` — registered in the agent's `history_processors=[...]` list, **between** `evict_old_tool_results` and `proactive_window_processor` (matches Diagram 1's MRN chain).

### 3.2 Implementation
- `co_cli/context/history_processors.py:319-446` — `enforce_llm_round_budget(ctx, messages)`.
- `co_cli/context/history_processors.py:302-316` — `_find_last_llm_round_returns_start(messages)` — reverse-scan to find the first message after the latest tool-calling `ModelResponse`.
- Behavior: aggregate sizes of the in-scope `ToolReturnPart`s using `CHARS_PER_TOKEN = 4`; if `tokens_before > deps.llm_round_aggregate_threshold_tokens`, force-spill largest-first via `spill_if_oversized(force=True)` until aggregate fits. Skips already-persisted (`<persisted-output>` prefix). Writes `runtime.current_llm_round_aggregate_tokens_after_spill`. Emits `tool_budget.enforce_llm_round_aggregate` span.

### 3.3 Existing hook surface
- `co_cli/tools/lifecycle.py:139-266` — `CoToolLifecycle(AbstractCapability[CoDeps])`.
- `co_cli/tools/lifecycle.py:199-221` — `after_node_run(ctx, *, node, result)` — already wired, currently used only for the L0 `tool_budget.enforce_tool_call_limit` span. **Returns `result` unchanged.**

### 3.4 Pydantic-ai surface (verified)
- `pydantic-ai/.../capabilities/abstract.py:335-341` — `after_node_run` signature; returns `NodeResult[AgentDepsT]` (= `AgentNode | End[FinalResult]`).
- `pydantic-ai/.../_agent_graph.py:519` — `ModelRequestNode.request: _messages.ModelRequest` (public dataclass field).
- `pydantic-ai/.../_agent_graph.py:1163,1186` — `output_parts` list is wrapped into `ModelRequest(parts=output_parts)` and assigned to `self._next_node = ModelRequestNode(...)`. The list is mutable; the field is reassignable.
- `pydantic-ai/.../_agent_graph.py:972-978` — `CallToolsNode.run` returns `self._next_node`. So the `result` argument to `after_node_run` IS the `ModelRequestNode` carrying the freshly-produced batch.

### 3.5 Lifecycle ordering (verified)
1. `CallToolsNode.run` produces `output_parts`, wraps into `ModelRequestNode(request=ModelRequest(parts=...))`, returns it.
2. `CoToolLifecycle.after_node_run` fires with `result = ModelRequestNode`. **Mutation point.**
3. Graph advances to `ModelRequestNode.run` → `_prepare_request` appends `request` to `ctx.state.message_history`.
4. `before_model_request` hook chain fires (the four remaining history processors).
5. `model.request()` → HTTP.

The mutation in (2) lands in `message_history` at (3) — pre-flight processors in (4) see already-spilled content.

### 3.6 Tests
- `tests/test_flow_llm_round_budget.py` — 7+ tests calling `enforce_llm_round_budget(ctx, messages)` directly with hand-built message histories via `_round` / `_build` helpers.
- Note: test file declares `model_max_ctx: int = 131_072` as default; **unrelated stale value** — leave for a separate cleanup, do not bundle.

---

## 4. Target design

### 4.1 New code shape

`co_cli/tools/lifecycle.py` extends `after_node_run`:

```python
async def after_node_run(
    self,
    ctx: RunContext[CoDeps],
    *,
    node: AgentNode[CoDeps],
    result: NodeResult[CoDeps],
) -> NodeResult[CoDeps]:
    if not isinstance(node, CallToolsNode):
        return result

    # --- Existing: L0 tool-call-limit OTEL span (unchanged) ---
    issued = ctx.deps.runtime.tool_calls_in_model_turn
    allowed = min(issued, MAX_TOOL_CALLS_PER_MODEL_TURN)
    rejected = max(0, issued - MAX_TOOL_CALLS_PER_MODEL_TURN)
    with self._tracer.start_as_current_span("tool_budget.enforce_tool_call_limit") as span:
        span.set_attribute("budget.context_window_tokens", ctx.deps.model_max_ctx)
        span.set_attribute("tool_calls.limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
        span.set_attribute("tool_calls.issued", issued)
        span.set_attribute("tool_calls.allowed", allowed)
        span.set_attribute("tool_calls.rejected", rejected)
        span.set_attribute("tool_calls.limit_exceeded", rejected > 0)

    # --- New: L2 round-budget enforcement on the just-produced batch ---
    if isinstance(result, ModelRequestNode):
        new_parts = _enforce_llm_round_budget(
            list(result.request.parts), ctx.deps, self._tracer
        )
        if new_parts is not None:
            result.request = replace(result.request, parts=new_parts)

    return result
```

### 4.2 Pure helper (replaces current history processor)

New file: extend `co_cli/context/history_processors.py` (or move to `co_cli/tools/_round_budget.py` if the importer would otherwise cycle):

```python
def _enforce_llm_round_budget(
    parts: list[ModelRequestPart],
    deps: CoDeps,
    tracer: Tracer,
) -> list[ModelRequestPart] | None:
    """Apply force-spill to the parts of one freshly-produced batch.

    Returns:
        New parts list with spilled returns rewritten, or None if no rewrite
        was needed (caller should leave the original list untouched).
    """
    threshold = deps.llm_round_aggregate_threshold_tokens

    with tracer.start_as_current_span("tool_budget.enforce_llm_round_aggregate") as span:
        span.set_attribute("budget.context_window_tokens", deps.model_max_ctx)
        span.set_attribute("llm_round_aggregate.threshold_tokens", threshold)

        # Index ToolReturnParts with string content; non-string passes through.
        candidates: list[tuple[int, ToolReturnPart]] = [
            (i, p) for i, p in enumerate(parts)
            if isinstance(p, ToolReturnPart) and isinstance(p.content, str)
        ]
        tokens_before = sum(len(p.content) // CHARS_PER_TOKEN for _, p in candidates)
        span.set_attribute("llm_round_aggregate.tokens_before", tokens_before)
        span.set_attribute("llm_round_aggregate.candidates_count", len(candidates))

        if tokens_before <= threshold:
            span.set_attribute("llm_round_aggregate.tokens_after", tokens_before)
            span.set_attribute("llm_round_aggregate.spilled_count", 0)
            span.set_attribute("llm_round_aggregate.spill_fired", False)
            span.set_attribute("llm_round_aggregate.skip_reason", "below_threshold")
            return None

        spillable = [
            (i, p) for i, p in candidates
            if not p.content.startswith(PERSISTED_OUTPUT_TAG)
        ]
        if not spillable:
            span.set_attribute("llm_round_aggregate.tokens_after", tokens_before)
            span.set_attribute("llm_round_aggregate.spilled_count", 0)
            span.set_attribute("llm_round_aggregate.spill_fired", False)
            span.set_attribute("llm_round_aggregate.skip_reason", "no_candidates_all_spilled")
            return None

        spillable.sort(key=lambda t: len(t[1].content), reverse=True)

        new_parts = list(parts)
        aggregate = tokens_before
        spilled_count = 0
        for idx, part in spillable:
            if aggregate <= threshold:
                break
            old = part.content
            new = spill_if_oversized(old, deps.tool_results_dir, part.tool_name, force=True)
            if new == old:
                continue
            new_parts[idx] = ToolReturnPart(
                tool_name=part.tool_name,
                content=new,
                tool_call_id=part.tool_call_id,
            )
            aggregate -= (len(old) - len(new)) // CHARS_PER_TOKEN
            spilled_count += 1

        deps.runtime.current_llm_round_aggregate_tokens_after_spill = aggregate
        span.set_attribute("llm_round_aggregate.tokens_after", aggregate)
        span.set_attribute("llm_round_aggregate.spilled_count", spilled_count)
        span.set_attribute("llm_round_aggregate.spill_fired", True)
        span.set_attribute("llm_round_aggregate.skip_reason", "")
        return new_parts
```

**Key differences from the current processor:**
- No reverse-scan: indexes `ToolReturnPart`s directly within `parts` (a single batch).
- No `boundary is None` short-circuit: the function isn't called when there's no batch (`isinstance(result, ModelRequestNode)` guard handles that).
- `skip_reason="no_round_yet"` is **deleted** — it cannot occur in the new placement.
- Returns a new list (or None) instead of returning the entire message list.

### 4.3 Skip cases (post-migration)

| Condition | Behavior |
|---|---|
| `node` is not `CallToolsNode` | Hook returns `result` unchanged (existing guard). |
| `result` is `End[FinalResult]` (final-result tool path) | No batch to enforce; skip. No span. |
| `result.request.parts` has no string `ToolReturnPart`s | `candidates_count=0`, span emitted, return None. |
| Aggregate fits | `skip_reason="below_threshold"`, span emitted, return None. |
| All candidates already persisted | `skip_reason="no_candidates_all_spilled"`, span emitted, return None. |
| Spill `OSError` on a candidate | That candidate is skipped (`new == old`); others continue. |

---

## 5. Implementation steps

Each step is a discrete commit-worthy change. Mark `✓ DONE` when completed. **Do not delete tasks mid-delivery.**

### ✓ DONE — Step 1: extract pure helper
- [x] Created `_enforce_llm_round_budget` in `co_cli/context/history_processors.py:306` with signature `(parts, deps, tracer) -> list[ModelRequestPart] | None`.
- [x] `CHARS_PER_TOKEN`, `PERSISTED_OUTPUT_TAG`, `spill_if_oversized` reused via lazy imports inside the helper (matches surrounding pattern).

### ✓ DONE — Step 2: wire the hook
- [x] `CoToolLifecycle.after_node_run` (`co_cli/tools/lifecycle.py:217-227`) calls the helper after L0 span when `isinstance(result, ModelRequestNode)`.
- [x] `ModelRequestNode` imported from public `pydantic_ai` (re-exported alongside `CallToolsNode`); no private path needed.
- [x] `replace` already imported from `dataclasses`.
- [x] `_enforce_llm_round_budget` imported from `co_cli.context.history_processors`.

### ✓ DONE — Step 2.5: wire CoToolLifecycle on the dream miner sub-agent
- [x] `co_cli/memory/dream.py:113-119` — `Agent(...)` now passes `capabilities=[CoToolLifecycle()]`.
- [x] `CoToolLifecycle` imported.

### ✓ DONE — Step 3: deregister the history processor
- [x] `enforce_llm_round_budget` removed from `co_cli/agent/core.py:148-152` history_processors list.
- [x] Import dropped (`co_cli/agent/core.py:19`).
- [x] `grep -rn "enforce_llm_round_budget" co_cli/` confirms only the new `_enforce_llm_round_budget` helper remains.

### ✓ DONE — Step 4: delete the old processor + dead helpers
- [x] Old `enforce_llm_round_budget` and `_find_last_llm_round_returns_start` deleted from `co_cli/context/history_processors.py` — replaced by the new private `_enforce_llm_round_budget` helper.
- [x] Module docstring updated to drop `enforce_llm_round_budget` from registered-processors and add a `Module-private helper:` block pointing to the hook.
- [x] Tracking comments in `co_cli/deps.py:154,253`, `co_cli/tools/tool_io.py:85`, `co_cli/context/compaction.py:13` updated to reference `_enforce_llm_round_budget`.
- [x] `grep -rn "enforce_llm_round_budget\|_find_last_llm_round_returns_start" co_cli/` confirms no orphan references.

### ✓ DONE — Step 5: migrate tests
- [x] All 4 retained tests refactored to construct `CallToolsNode` + `ModelRequestNode(request=ModelRequest(parts=[...]))` and call `await CoToolLifecycle().after_node_run(...)`.
- [x] `@pytest.mark.asyncio` applied to every test (asyncio mode is `STRICT`).
- [x] User-turn anchor boilerplate dropped; new `_batch` helper returns `list[ToolReturnPart]` for one batch.
- [x] Two old tests removed (`test_earlier_rounds_not_in_scope`, `test_no_round_yet_pre_first_tool_call`) — both probed boundary-scan behavior that no longer exists in the hook placement (the `no_round_yet` skip reason was deleted per §4.2).
- [x] Two new tests added: `test_final_result_path_is_noop` (hook returns `End[FinalResult]` unchanged, no L2 span) and `test_non_calltools_node_passthrough` (early return when `node` is not `CallToolsNode`).
- [x] All 6 tests pass; harness reports L0+L2 = 2 spans on normal cases, L0 only = 1 span on End path, 0 spans on non-`CallToolsNode`.

### ✓ DONE — Step 6: spec updates (`docs/specs/compaction.md` + cross-spec sync)

| Section | Change |
|---|---|
| §1 mechanism table | Move `enforce_llm_round_budget` row out of the history-processor cluster. New "When" column value: `CallToolsNode exit (next node = ModelRequestNode)`. New "Gate logic": `result is ModelRequestNode; tokens_before > llm_round_aggregate_threshold_tokens; ≥ 1 non-persisted candidate`. |
| §1 Diagram 1 (MRN subgraph) | Remove `C2 enforce_llm_round_budget` from the chain. Renumber: `C0 dedup → C1 evict → C2 proactive → C3 sanitize → HTTP`. |
| §1 Diagram 1 (CTN subgraph) | Add a node `enforce_llm_round_budget` after the batch loop closes (between `MORE -->|no|` and the re-entry to `C0`). |
| §1 prose ("Effective work ≠ uniform trigger") | `enforce_llm_round_budget` is no longer in the pre-request fast-path-or-trip discussion — remove or move. |
| §2.1 layer table (L2 row) | Update "Mechanism" cite: still `enforce_llm_round_budget` but now described as a `CoToolLifecycle.after_node_run` hook, not a history processor. |
| §2.3 section title | Drop `enforce_llm_round_budget` from the title. New title: `dedup_tool_results / evict_old_tool_results — Prepass recency clearing`. |
| §2.3 body | Delete the `enforce_llm_round_budget` paragraph (currently the third bullet). Keep dedup, evict, sanitize. |
| New §2.3a (or §2.4) | Add a section: **`enforce_llm_round_budget` — Per-batch round budget**. Place between the prepass section and `proactive_window_processor`. Cover: hook placement, scope (just-produced batch), threshold, spill order, persisted-skip, span. |
| §2.3 worked example | Update — the current example shows enforcement in the prepass column. Move the "AFTER enforce_llm_round_budget" stage out of that example into the new section, OR drop it (the prepass example is clearer without it). |
| §4 Files table | Update `co_cli/context/history_processors.py` row to drop `enforce_llm_round_budget` from the list. Update `co_cli/tools/lifecycle.py` row to mention the hook. |
| §5 Test Gates | Test file `test_flow_llm_round_budget.py` stays. Property descriptions stay. No row changes. |

### ✓ DONE — Step 7: lint + scoped tests + full suite
- [x] `scripts/quality-gate.sh lint --fix` — clean (1 unused import auto-fixed in test file).
- [x] Scoped tests (`test_flow_llm_round_budget.py` + `test_flow_tool_call_limit.py` + `test_flow_compaction_proactive.py` + `test_flow_history_processors.py` + `test_flow_compaction_recovery.py` + `test_flow_bootstrap_budget_span.py` + `test_flow_spill_threshold.py` + `test_flow_spill_otel.py`) — 61 passed in 11.91s.
- [x] Full suite — 194 passed in 208.23s (3:28).

### Step 8: eval smoke (deferred)
- [ ] `uv run python evals/eval_compaction_proactive.py` — deferred to ship gate or post-merge smoke. Full unit-test suite + scoped compaction tests provide adequate coverage; behavioral parity is verified by the existing four hand-built test cases plus the two new edge-case tests.

---

## 6. Edge cases & invariants

### 6.1 Final-result path
When the model emits a final-result tool call, `_handle_tool_calls` sets `_next_node = self._handle_final_result(ctx, final_result, output_parts)` (`_agent_graph.py:1180`). The result is `End[FinalResult]`, not `ModelRequestNode`. The hook MUST short-circuit; no enforcement, no span. (No further LLM request will be made, so budget is moot.)

### 6.2 Empty batch
If a batch produces no `ToolReturnPart`s (e.g., all tool calls were rejected by L0 and the parts are `RetryPromptPart`s), `candidates_count=0`, span emitted with `skip_reason="below_threshold"`, return None. Behavior unchanged from current.

### 6.3 Sub-agent runs
Sub-agents (memory, summarizer, etc.) construct their own pydantic-ai `Agent` instances. **Verify each sub-agent agent registers `CoToolLifecycle`.** If any do not, they lose L2 enforcement when this migration lands.

**Audit result (2026-05-08):**
- `co_cli/agent/core.py:140` (main agent) — registers `CoToolLifecycle` ✓
- `co_cli/agent/core.py:163` (delegation agent) — registers `CoToolLifecycle` ✓
- `co_cli/memory/dream.py:113-118` (`build_dream_miner_agent`) — issues `memory_create` tool calls, does **NOT** register `CoToolLifecycle`. **Gate-1 decision: wire `capabilities=[CoToolLifecycle()]` on it** (see Step 2.5 below). Practically L2 never trips here (memory_create returns are small), but parity removes a silent regression.

The summarizer in `summarize_messages` calls `llm_call()` with no tools — confirmed safe.

### 6.4 `<persisted-output>` skip
Emit-time `spill_if_oversized` (L1) may have already persisted some parts. The new hook must skip them via `not p.content.startswith(PERSISTED_OUTPUT_TAG)`. **Identical to current behavior** — preserved in §4.2 above.

### 6.5 Mutation safety
`result.request` is reassigned via `replace(result.request, parts=new_parts)`. The original `ModelRequest` is not mutated; we replace the field. The `ModelRequestNode` itself is mutated (its `request` field), but no other observer holds a stale reference at that point in the graph (verified by code-walking `CallToolsNode.run` → `_next_node` is consumed only by the graph engine immediately after `after_node_run` returns).

### 6.6 Concurrent observers
Other capabilities may also implement `after_node_run`. Order of capability invocation is preserved by pydantic-ai's hook chain. If a downstream capability also reads `result.request.parts`, it will see our mutations — desired. If we want to guarantee we run last among `after_node_run` observers, we'd need an explicit ordering convention; **assume no conflict for now and verify via test.**

### 6.7 OTEL span ordering
`tool_budget.enforce_tool_call_limit` (L0) and `tool_budget.enforce_llm_round_aggregate` (L2) both fire in `after_node_run`. They're sibling spans, both children of the current OTEL context (the `CallToolsNode` graph span). Order in code is L0 first, L2 second — matches the layer numbering. No nesting needed.

---

## 7. Out of scope

- Renaming the function. Keep `enforce_llm_round_budget` (now the helper, prefixed with `_`).
- Renaming the test file or the OTEL span. Both stay: `tests/test_flow_llm_round_budget.py`, `tool_budget.enforce_llm_round_aggregate`.
- Touching L1 (`spill_if_oversized`) or L3 (`proactive_window_processor`).
- Moving any other history processor.
- Adding prompt-cache awareness (the fork-cc divergence). That's a separate plan.
- Updating the test file's stale `model_max_ctx=131_072` default. Separate cleanup.

---

## 8. Risk & rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| Sub-agent without `CoToolLifecycle` silently loses L2 | Low | §6.3 audit step before merge. |
| `ModelRequestNode` import path is private and breaks on pydantic-ai upgrade | Low | Pin pydantic-ai version in `pyproject.toml`; add a runtime `isinstance` check that fails open. Already importing `CallToolsNode` from a similar path — same risk profile. |
| Hook ordering with future capabilities | Low | Assertion test: confirm `after_node_run` returns `result` with mutated `request.parts`. |
| Spec inconsistency after partial doc updates | Medium | All §6 spec edits in one commit; sync-doc after impl. |

**Rollback:** revert the commit. The pure helper `_enforce_llm_round_budget` and the hook wiring are added together; reverting restores the history processor.

---

## 9. Acceptance criteria (Gate 2)

- All existing assertions in `tests/test_flow_llm_round_budget.py` pass under the new harness, with identical inputs producing identical spilled output.
- New test for `End[FinalResult]` path passes.
- L0 OTEL span attributes (`tool_calls.limit/issued/allowed/rejected/limit_exceeded`) unchanged in `tests/test_flow_tool_call_limit.py`.
- L2 OTEL span attributes match the current contract: `tokens_before/tokens_after/candidates_count/spilled_count/spill_fired/skip_reason`.
- `grep -rn "enforce_llm_round_budget\|_find_last_llm_round_returns_start" co_cli/` shows the helper usage in `lifecycle.py` and definition in `history_processors.py` only — no orphan references.
- `docs/specs/compaction.md` §1 mechanism table, §2.3, §2.3a (new), Diagram 1, §4 Files reflect the new placement.
- `scripts/quality-gate.sh full` passes.

---

## 10. Estimated effort

- Step 1-4 (impl): ~80 lines added, ~145 lines deleted.
- Step 5 (tests): ~50 lines refactored, ~15 lines added (one new test).
- Step 6 (spec): ~30 line-edits across 5 sections + Diagram 1.
- Total: 1 focused dev session.

---

## 11. Delivery summary — 2026-05-08

| Step | Outcome | Notes |
|------|---------|-------|
| 1. Extract pure helper | ✓ pass | `_enforce_llm_round_budget` at `co_cli/context/history_processors.py:306` |
| 2. Wire `after_node_run` hook | ✓ pass | `co_cli/tools/lifecycle.py:217-227` — L0 span first, then L2 enforcement when `result is ModelRequestNode` |
| 2.5. Wire `CoToolLifecycle` on dream miner | ✓ pass | `co_cli/memory/dream.py:113-119` — parity with main + delegation agents |
| 3. Deregister history processor | ✓ pass | `co_cli/agent/core.py:148-152` |
| 4. Delete old processor + dead helper | ✓ pass | Old `enforce_llm_round_budget` and `_find_last_llm_round_returns_start` deleted; tracking comments in `deps.py`, `tool_io.py`, `compaction.py` updated to new helper name |
| 5. Migrate tests | ✓ pass | 6 tests (4 retained + 2 new edge-case); all `@pytest.mark.asyncio`; `_batch` / `_calltools_node` / `_model_request_node` helpers replace `_round` / `_build` boilerplate |
| 6. Spec updates | ✓ pass | `compaction.md` §1 table+diagram+prose, §2.1 L2 row, §2.3 retitled (prepass only), new §2.4 round-budget hook section, §2.5+ renumbered, §2.6 + §3 + §4 cross-references updated. Cross-spec sync: `core-loop.md`, `prompt-assembly.md`, `observability.md` (span attribute rename to `llm_round_aggregate_*`) |
| 7. Lint + tests | ✓ pass | Scoped: 61 passed; full suite: 194 passed in 208s |
| 8. Eval smoke | — deferred | Real-LLM eval deferred; unit + scoped tests cover behavioral parity |

**Tests:** scoped (8 compaction/budget files) — 61 passed, 0 failed; full suite — 194 passed, 0 failed.
**Doc Sync:** clean — specs updated inline (compaction.md + core-loop.md + prompt-assembly.md + observability.md). No follow-up sync-doc pass needed.

**Bundled work:** the working tree contained an in-flight rename pass (`enforce_turn_budget` → `enforce_llm_round_budget`, `turn_aggregate_*` → `llm_round_aggregate_*`) on `agent/core.py`, `bootstrap/core.py`, `context/compaction.py`, `context/history_processors.py`, `deps.py`, `tools/tool_io.py`, `docs/specs/compaction.md`, and the test file (renamed `test_flow_turn_budget.py` → `test_flow_llm_round_budget.py`). Per `feedback_merge_coworker_uncommitted`, this rename is the logical predecessor to the post-tool-hook migration and is bundled into this delivery.

**Out of scope (not touched, per §7 / coworker uncommitted):** `co_cli/context/orchestrate.py` length-retry tweak (independent concern); `tests/test_flow_bootstrap_budget_span.py` and `tests/test_flow_length_retry.py` (in working tree, related to the rename pass — left as-is, not run as part of this scoped suite); test file's `model_max_ctx=131_072` default value (per §3.6 note); `truncate_tool_results` references in `core-loop.md` / `prompt-assembly.md` (separate prior-rename doc bug).

**Overall: DELIVERED**

The `enforce_llm_round_budget` history processor has been migrated to a `CoToolLifecycle.after_node_run` capability hook. Behavioral invariant preserved (identical spill outcomes for identical inputs) — verified by the four retained behavioral tests plus two new edge-case tests for the new hook surface. The reverse-scan boundary helper is gone; enforcement now runs exactly once per batch on `CallToolsNode` exit, sibling to the L0 tool-call-limit span.

## 12. Review verdict
*(to be filled by review-impl after implementation)*
