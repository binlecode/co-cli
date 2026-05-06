# Plan: bounded tool output — tool-call cap, per-call spill refit, per-llm-turn aggregate spill

Task type: code-feature

## Context

Existing context-pressure layers in co-cli:

- **Per-call spill** (`tool_io.spill_if_oversized`): emit-time spill when one tool result > threshold. Today: `ToolsSettings.result_persist_chars = 50_000` default + per-tool overrides (`shell=30_000`, `file_read=math.inf`).
- **Tool-result hygiene** (`_history_processors.py`): `dedup_tool_results`, `evict_old_tool_results` — operate **outside** the protected tail anchored at `_find_last_turn_start`.
- **Proactive window compaction** (`proactive_window_processor` + `apply_compaction`): turn-group window compaction at `compaction_ratio × model_max_ctx`. Protects last turn.

Inputs frozen post-bootstrap:
- `deps.model_max_ctx` — Ollama probe capped by `LlmSettings.max_ctx`.
- `compaction.tail_fraction` — default `0.20` (`co_cli/config/compaction.py:5-10`, env `CO_COMPACTION_TAIL_FRACTION`).

**Gap.** Per-call spill catches one oversized return but does nothing about *aggregate* growth: three sub-50K returns total 150K chars without any layer noticing. At 64K, two 50K-char returns alone saturate the ~13K-token tail. This plan adds two layers:

- **L0 tool-call cap** — prevent runaway parallel dispatch within a single model turn.
- **L2 aggregate spill** — guarantee tail-fit across all model iterations within a user turn; bail out (return messages unchanged) when force-spill candidates are exhausted, letting L3 (`proactive_window_processor`) handle compaction in the same processor chain.

## Design

### Layers

| Layer | Status | Behavior | Threshold source |
|---|---|---|---|
| **L0** Tool-call cap | NEW | `wrap_tool_execute` rejects calls beyond `cap` within a model turn (rejection is order-independent — increment-and-check is atomic per coroutine, so under `asyncio.gather` *which* `cap` calls dispatch is non-deterministic but the count is); rejected calls return a `MaxToolCallsExceededPayload` JSON synthesized as the tool result. | `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` (flat constant) |
| **L1** Per-call spill | EXISTING (refit) | Tool result > threshold → preview stub. | `info.spill_threshold_chars` if set, else `SPILL_THRESHOLD_CHARS = 4_000` |
| **L2** Aggregate spill | NEW | History processor; before each LLM call, aggregate user-turn tool returns (boundary `_find_last_turn_start`); if over threshold, force-spill non-spilled candidates largest-first. If all candidates already spilled, bail out (return messages unchanged) — L3 (`proactive_window_processor`) runs next in the same chain and may compact; only if the LLM call then overflows does `recover_overflow_history` → `emergency_recover_overflow_history` engage. | `tail_fraction × model_max_ctx` (bootstrap-cached) |
| **L3** Window compaction | EXISTING | Unchanged. | `compaction_ratio × model_max_ctx` |

Span tree per turn: `tool_budget.enforce_tool_call_limit` → `tool_budget.spill_tool_result × N` → `tool_budget.enforce_turn_aggregate` → `compaction.proactive_check`.

### Constants

Module-level, no scaling:

| Constant | Value | Defined in |
|---|---|---|
| `CHARS_PER_TOKEN` | `4` | `co_cli/context/tokens.py` (NEW, public — imported across packages) |
| `TOOL_RESULT_PREVIEW_CHARS` | `1_500` (~375 tokens) | `tool_io.py` |
| `SPILL_THRESHOLD_CHARS` | `4_000` (~1_000 tokens) | `tool_io.py` |
| `MAX_TOOL_CALLS_PER_MODEL_TURN` | `6` | `_tool_call_limit.py` |

### CoDeps state

Bootstrap-cached, immutable post-bootstrap:

| Field | Source |
|---|---|
| `deps.turn_aggregate_threshold_tokens` | `int(tail_fraction × model_max_ctx)` |

`MAX_TOOL_CALLS_PER_MODEL_TURN` and `SPILL_THRESHOLD_CHARS` are flat module constants — import them directly at every read site. They are NOT cached on `CoDeps`.

Per-turn runtime state on `CoDeps.runtime`:

| Field | Lifecycle |
|---|---|
| `tool_call_limit_run_step: int = -1`, `tool_calls_in_model_turn: int = 0` | Per-model-turn brake counter; resets implicitly on `ctx.run_step` transition |
| `current_turn_aggregate_tokens_after_spill: int \| None = None` | Written by `enforce_turn_budget` after spill; read by `compaction.proactive_check` span; reset in `reset_for_turn` |

### Sizing

Stub size = preview (1_500) + ~300 wrapper ≈ 1_800 chars (~450 tokens).
- **Spill saving**: 4_000 → 1_800 = 2_200 chars (~55% reduction, well above wrapper-overhead break-even).
- **Cap sized to floor context (64K)**: tail = `0.20 × 64K = 13_107` tokens. Worst-case non-spilling call ≈ 1_000 tokens (just under SPILL_THRESHOLD_CHARS); six of those = 6_000 tokens, leaving 7_107-token slack. Spilled stubs (~450 tokens each) are smaller, so this is a true upper bound. At 128K the same cap leaves proportionally more headroom that L2 handles.

| context_window | turn_aggregate_threshold (tokens) | worst-case post-turn tail (tokens) |
|---|---|---|
| 128K | 26_214 | 32_214 |
| 64K | 13_107 | 19_107 |

`worst-case post-turn tail = turn_aggregate_threshold + cap × spill_tokens`. L2 trims this to `turn_aggregate_threshold` before the next LLM call.

**Bound caveat**: the sizing math holds when L1 fires on all 6 tool calls, converting each raw ~1K-token return into a ~450-token stub before L2 sees them. Tools with `math.inf` overrides (currently `file_read`) bypass L1 entirely and arrive at L2 at full size. L2 handles this via force-spill, but the pre-L2 aggregate can be substantially larger than the stub-only scenario (e.g. 6 × 50K-char file reads ≈ 75K tokens pre-L2). The table formula still describes the post-L2 steady state; it does not bound the transient pre-L2 size when L1-exempt tools are in play.

### L2 firing rules (`enforce_turn_budget`)

History processor; runs before each LLM call. Aggregates token count across the current user turn's tool returns only (boundary: `_find_last_turn_start`; spans all model iterations within that user turn).

1. `aggregate ≤ threshold` → emit span (`skip_reason="below_threshold"`), return unchanged.
2. `aggregate > threshold`, non-spilled candidates exist (detect via `PERSISTED_OUTPUT_TAG` marker) → sort largest-first; force-spill via `force=True` until aggregate ≤ threshold; emit span (`spill_fired=True`); write `tokens_after` to `deps.runtime.current_turn_aggregate_tokens_after_spill`.
3. `aggregate > threshold`, all already spilled → bail out. Return messages unchanged; emit span (`skip_reason="no_candidates_all_spilled"`). The processor chain continues: L3 (`proactive_window_processor`) runs next and may compact if total context exceeds the compaction threshold. Only if the LLM call then throws a context-overflow error does `recover_overflow_history` → `emergency_recover_overflow_history` engage. **Do not call `apply_compaction` from `enforce_turn_budget`.** L3 already owns proactive compaction and runs at a higher threshold (`compaction_ratio × model_max_ctx`) — calling it from L2 would fire compaction at the tail-fraction threshold (20%) before L3 would have triggered, and L3 runs immediately after anyway.

L2 fires at the model-turn boundary (per-llm-turn cadence) but aggregates across the user-turn boundary (multi-iteration scope).

### Per-tool override handling

`ToolInfo.spill_threshold_chars` is retained as an optional per-tool override — consistent naming with `SPILL_THRESHOLD_CHARS` (constant). The `register()` kwarg and `tool_io.tool_output` override path are preserved. Disposition of existing overrides:
- `shell = 30_000` chars — drop. New `SPILL_THRESHOLD_CHARS = 4_000` default is already tighter; the override is redundant.
- `file_read = math.inf` — keep. Most source files exceed 4,000 chars; spilling on the first read wastes a round-trip with zero information gain. With L2 aggregate spill now providing context-pressure protection, the per-call exemption is safe.

`tool_output` threshold lookup: `info.spill_threshold_chars` (if set, including `math.inf`) else `SPILL_THRESHOLD_CHARS` (imported constant). Add a per-tool override only when a real use case arises.

### Constraints (load-bearing)

1. **Turn-aggregate threshold is a token-fraction of `model_max_ctx`, never absolute chars.** Static char thresholds don't scale with probed context variance.
2. **L2 force-spills under L1's threshold** (via `force=True` parameter). Without it, items individually under L1 but cumulatively over L2 silently no-op. `force=True` also catches L1-exempted items (per-tool `math.inf` overrides) that arrive at L2 at full size — the exemption means L1 never fired, not that the content is small.
3. **`SPILL_THRESHOLD_CHARS` and `TOOL_RESULT_PREVIEW_CHARS` are coupled.** Stub size = preview + ~300 wrapper. Spill threshold must exceed stub size by enough to recover wrapper overhead. ~55% saving is the current anchor.
4. **Layer labels (L0/L1/L2/L3) are prose-only.** No identifier in `co_cli/`, `tests/`, or OTEL attributes uses `l0_*`/`l1_*`/`l2_*`/`l3_*`.
5. **Spans fire unconditionally**, including no-op cases. 95th-percentile validation needs the denominator.

## Observability

New tracer: `co-cli.tool_budget`.

| Span | Where | Fired | Attributes |
|---|---|---|---|
| `tool_budget.resolved` | `bootstrap/core.py` after `model_max_ctx` is set | once per session | `budget.context_window_tokens`, `budget.tail_fraction`, `budget.tool_call_limit`, `budget.spill_threshold_chars`, `budget.turn_aggregate_threshold_tokens` |
| `tool_budget.enforce_tool_call_limit` | `after_node_run` for `CallToolsNode` (post-dispatch hook — all `wrap_tool_execute` calls complete before this fires; filter `isinstance(node, CallToolsNode)`, emit unconditionally when the hook fires) | every model turn with tool dispatch (pydantic-ai's `CallToolsNode` only runs when there are tools to call, so tool-less turns produce no span naturally) | `budget.context_window_tokens`, `tool_calls.limit`, `tool_calls.issued`, `tool_calls.allowed`, `tool_calls.rejected`, `tool_calls.limit_exceeded` |
| `tool_budget.spill_tool_result` | `tool_io.spill_if_oversized` | every call (incl. no-ops) | `tool.name`, `spill.threshold_chars`, `spill.content_chars`, `spill.fired`, `spill.forced`, `spill.savings_chars` (when spilled). **`spill.threshold_chars` for L1-exempt tools** (e.g. `file_read` with `math.inf`): emit `SPILL_THRESHOLD_CHARS` (the system default), not `math.inf` — OTEL attributes must be finite numerics. Implementation must guard `math.isinf(threshold)` and substitute the constant before setting the attribute. |
| `tool_budget.enforce_turn_aggregate` | `_history_processors.enforce_turn_budget` | every dispatch boundary | `budget.context_window_tokens`, `turn_aggregate.threshold_tokens`, `turn_aggregate.tokens_before`, `turn_aggregate.tokens_after`, `turn_aggregate.candidates_count`, `turn_aggregate.spilled_count`, `turn_aggregate.spill_fired`, `turn_aggregate.skip_reason` (`below_threshold` \| `no_candidates_all_spilled` \| `null` when force-spill fired) |

Existing `compaction.proactive_check` span (`compaction.py:495-557`) gains:
- `compaction.tool_call_limit` ← `MAX_TOOL_CALLS_PER_MODEL_TURN` (imported constant)
- `compaction.turn_aggregate_tokens_after_spill` ← `deps.runtime.current_turn_aggregate_tokens_after_spill` (None when L2 didn't fire)

Counter derivation (no extra runtime fields beyond `tool_calls_in_model_turn`):
- `tool_calls.issued = tool_calls_in_model_turn`
- `tool_calls.allowed = min(tool_calls_in_model_turn, MAX_TOOL_CALLS_PER_MODEL_TURN)`
- `tool_calls.rejected = max(0, tool_calls_in_model_turn - MAX_TOOL_CALLS_PER_MODEL_TURN)`
- `tool_calls.limit_exceeded = tool_calls.rejected > 0`

## Scope

**In:** TASK-1 (L0), TASK-2 (L1 refit + L2), TASK-3 (specs + CHANGELOG + quality gate).
**Out:** all eval work (sibling plan); algorithmic changes to existing hygiene/proactive-compaction/overflow-recovery processors.

**Atomic landing — TASK-1 + TASK-2 ship in one commit.** Both populate fields in one `bootstrap/core.py` block and share the `tool_budget.resolved` span. A partial landing publishes an incomplete-attr version of the span. TASK-3 (spec sync) ships in the same commit so docs never lag the code.

## Implementation

### TASK-1 — Tool-call cap (constant + cache + brake + OTEL)

prerequisites: []

files:
- `co_cli/context/tokens.py` (NEW, public — no underscore prefix because imported across packages, per `_prefix.py` convention in CLAUDE.md):
  ```python
  CHARS_PER_TOKEN = 4   # fast proxy, not a tokenizer
  ```
  Replace inline `// 4` at `summarization.py:61` with `from co_cli.context.tokens import CHARS_PER_TOKEN`.

- `co_cli/tools/tool_io.py`:
  - Rename + retune existing constant: `TOOL_RESULT_PREVIEW_SIZE` → `TOOL_RESULT_PREVIEW_CHARS` (line 42 + read site at line 96), value `2_000` → `1_500`.
  - Add `SPILL_THRESHOLD_CHARS = 4_000` (module-public).
  - Verify `grep -rn "TOOL_RESULT_PREVIEW_SIZE" co_cli/ tests/` returns no results.

- `co_cli/agent/_tool_call_limit.py` (NEW, package-private): ✓ DONE
  ```python
  MAX_TOOL_CALLS_PER_MODEL_TURN = 6   # one worst-case model turn fits the tail at 32K; see Sizing
  ```
  - `MaxToolCallsExceededPayload` — `TypedDict` with `error: Literal["max_tool_calls_per_turn_exceeded"]`, `max: int`, `issued: int`, `guidance: str`. Returned (json-serialized) for rejected calls.
  - **Guidance wording — pin during delivery.** Pre-implementation: scan `~/workspace_genai/hermes-agent/` and `~/workspace_genai/opencode/` for equivalent recovery-prompt phrasing — small models (Hermes-class 7-14B) follow imperative second-person prose better than structured error envelopes. Starting wording: `"Issued {issued} tool calls in one model turn; cap is {max}. Pick the {max} most important calls and try again."` — refine after the parity scan, pin in implementation. Test asserts non-empty + interpolated values present.

- `co_cli/deps.py`:
  - `CoRuntimeState`: add `tool_call_limit_run_step: int = -1` and `tool_calls_in_model_turn: int = 0`. Reset implicitly on `ctx.run_step` transition (no entry in `reset_for_turn`). (**`tool_call_limit` is a flat constant — not cached on `CoDeps`. Import `MAX_TOOL_CALLS_PER_MODEL_TURN` at every read site.**)

- `co_cli/tools/lifecycle.py` — add two methods to `CoToolLifecycle(AbstractCapability[CoDeps])`: ✓ DONE

  **`wrap_tool_execute` — the brake (fires N times per model turn, once per tool call)**
  ```python
  async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
      runtime = ctx.deps.runtime
      # Per-model-turn reset: ctx.run_step increments once per LLM call;
      # all tools from one assistant message share the same run_step.
      if ctx.run_step != runtime.tool_call_limit_run_step:
          runtime.tool_call_limit_run_step = ctx.run_step
          runtime.tool_calls_in_model_turn = 0
      runtime.tool_calls_in_model_turn += 1

      if runtime.tool_calls_in_model_turn > MAX_TOOL_CALLS_PER_MODEL_TURN:
          return json.dumps(MaxToolCallsExceededPayload(...))
      return await handler(args)
  ```
  Atomicity: no `await` between check and increment — asyncio cooperative scheduling makes the block atomic across parallel dispatch. `history_processors` cannot be used here: they operate on input messages to the next LLM call and cannot prevent tool dispatch.

  **`after_node_run` — the span emitter (fires once per model turn, after all tool calls complete)**
  ```python
  async def after_node_run(self, ctx, *, node, result):
      if isinstance(node, CallToolsNode):
          # tool_calls_in_model_turn is at its final value here
          # emit tool_budget.enforce_tool_call_limit span with all attrs
          ...
      return result
  ```
  pydantic-ai dispatches all tool calls in a model turn via `asyncio.gather`, so `wrap_tool_execute` fires N times concurrently — emitting the span there would produce N spans per turn with partial counts. `after_node_run` on `CallToolsNode` fires exactly once after all dispatches complete, when `tool_calls_in_model_turn` is final. `ModelRequestNode.after_node_run` is wrong: it fires before any tools dispatch and reads stale counters from the previous turn.

  Emit unconditionally when the hook fires — pydantic-ai only instantiates `CallToolsNode` when there are tools to call, so tool-less turns produce no span without any extra guard. (If that assumption breaks at delivery, fall back to a `runtime.tool_call_limit_run_step == ctx.run_step` guard rather than a counter-zero check; a counter-zero check misfires on the first tool-using turn after a tool-less one because the counter resets to 0 before the first increment.)

  No new registration is needed: `CoToolLifecycle` is already passed to `Agent(..., capabilities=[CoToolLifecycle()])` in `agent/core.py`; pydantic-ai routes all capability hooks automatically.

tests/test_flow_tool_call_limit.py (new):
- Constant pinned: `MAX_TOOL_CALLS_PER_MODEL_TURN == 6`; `fork_deps` does not touch it (not on `CoDeps`).
- Brake: stub `RunContext` with `run_step=1`, no-op handler. Invoke `wrap_tool_execute` 8× — 6 dispatched, 2 reject with non-empty `guidance` containing both `{issued}` and `{max}` interpolated.
- Run_step transition resets counter: 6 calls at `run_step=1` then 6 at `run_step=2`, all allowed.
- Concurrency: `asyncio.gather` 8× same `run_step` — exactly 6 reach `handler`, 2 reject.

tests/test_flow_tool_call_limit_integration.py (new): real bootstrap at `max_ctx = 65_536`; drive a real agent turn issuing ≥ 8 tool calls. Assert 6 dispatched, ≥ 2 rejection results injected, agent recovers next turn. Capture OTEL via in-memory exporter — `tool_budget.resolved` fires once with each of the named attributes from the Observability table present (assert by name, not by count, so future attr additions don't break the test); `tool_budget.enforce_tool_call_limit` fires with each of its named attributes present.

done_when:
- `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` in `_tool_call_limit.py`; imported directly at all read sites (NOT cached on `CoDeps`).
- `CoRuntimeState.tool_call_limit_run_step` and `tool_calls_in_model_turn` exist; reset implicitly on `ctx.run_step` transition.
- `CoToolLifecycle.wrap_tool_execute` is the brake site; rejections return `MaxToolCallsExceededPayload` (json-serialized) with non-empty `guidance`, without awaiting `handler`.
- `CoToolLifecycle.after_node_run` is the span site; filters `isinstance(node, CallToolsNode)`, emits `tool_budget.enforce_tool_call_limit` with all attrs, returns `result` unchanged.
- (Shared `tool_budget.resolved` is verified at the atomic landing — see Scope.)
- `uv run pytest tests/test_flow_tool_call_limit.py tests/test_flow_tool_call_limit_integration.py -x` green.

success_signal: an assistant message issuing > 6 tool calls produces a deterministic, recoverable rejection at every reachable context_window; OTEL traces show the cap and per-turn issued/allowed/rejected counts.

### TASK-2 — Per-call refit + L2 aggregate spill

prerequisites: [TASK-1]

The two layers are coupled: refit-only leaves L2 nothing to do; L2-only would force-spill everything. Land together.

files:
- `co_cli/tools/tool_io.py`:
  - `tool_output` (currently `tool_io.py:213-217`) — update threshold lookup: `info.spill_threshold_chars` (if set) else `SPILL_THRESHOLD_CHARS` (imported constant). Drop the old `ctx.deps.config.tools.result_persist_chars` fallback.
  - Rename `persist_if_oversized` → `spill_if_oversized` (definition `tool_io.py:61`, callsite `:219`, docstring `:137`, comment `_dedup_tool_results.py:36`).
  - Add `force: bool = False` parameter to `spill_if_oversized` — when True, skip the `> SPILL_THRESHOLD_CHARS` check, BUT still return content unchanged when `len(content) <= TOOL_RESULT_PREVIEW_CHARS`. Rationale: content that fits in the preview in full has no spill benefit — the stub would just add wrapper overhead without information gain (and for very small inputs like the ~150-char `MaxToolCallsExceededPayload` JSON, force-spilling would *grow* context). The guard is what makes `force=True` safe to use across L2's full candidate list.
  - Spill stub fix (`tool_io.py:105-114`): ✓ DONE
    - Add hermes-parity opening line: `"This tool result was too large ({size_chars:,} chars, {size_human}).\n"` as the first line inside the envelope, before `tool:` / `file:`. Gives small local models an explicit reason for the spill.
    - Rename `read_file` → `file_read` in the navigation instruction (pre-existing typo; tool is named `file_read`).
  - Keep `PERSISTED_OUTPUT_TAG = "<persisted-output>"` as-is (`tool_io.py:43`) — wire-format marker baked into existing histories.
  - Emit `tool_budget.spill_tool_result` span every call (incl. no-ops).

- `co_cli/deps.py`:
  - `CoDeps`: add `turn_aggregate_threshold_tokens: int = 0`. `fork_deps` propagates. (**`spill_threshold_chars` is a flat constant — not cached on `CoDeps`. Import `SPILL_THRESHOLD_CHARS` at every read site.**)
  - `CoRuntimeState` (`deps.py:133`): add `current_turn_aggregate_tokens_after_spill: int | None = None`. Reset in `reset_for_turn`.

- `co_cli/bootstrap/core.py` — after `model_max_ctx` is resolved (~line 269), one block:
  ```python
  tail_fraction = deps.config.compaction.tail_fraction
  deps.turn_aggregate_threshold_tokens = int(tail_fraction * deps.model_max_ctx)

  with otel_trace.get_tracer("co-cli.tool_budget").start_as_current_span("tool_budget.resolved") as span:
      span.set_attribute("budget.context_window_tokens", deps.model_max_ctx)
      span.set_attribute("budget.tail_fraction", tail_fraction)
      span.set_attribute("budget.tool_call_limit", MAX_TOOL_CALLS_PER_MODEL_TURN)
      span.set_attribute("budget.spill_threshold_chars", SPILL_THRESHOLD_CHARS)
      span.set_attribute("budget.turn_aggregate_threshold_tokens", deps.turn_aggregate_threshold_tokens)

  log.info(
      "tool-budget bounds: context_window=%d tool_call_limit=%d spill=%dc turn_aggregate=%d tokens",
      deps.model_max_ctx, MAX_TOOL_CALLS_PER_MODEL_TURN,
      SPILL_THRESHOLD_CHARS, deps.turn_aggregate_threshold_tokens,
  )
  ```

- Per-tool threshold override — partial cleanup:
  - `ToolInfo.max_result_size` → rename to `spill_threshold_chars` (`co_cli/deps.py:103`); type stays `int | float | None`.
  - `co_cli/tools/agent_tool.py:26,53` — rename `register()` kwarg `max_result_size` → `spill_threshold_chars` and forwarding callsite.
  - `co_cli/tools/shell/execute.py:17` — **drop** `spill_threshold_chars=30_000`; new 4_000 default is already tighter.
  - `co_cli/tools/files/read.py:449` — rename to `spill_threshold_chars=math.inf`; avoids a wasted round-trip on first read of any source file.
  - `co_cli/config/tools.py` — delete `result_persist_chars` field, `TOOLS_ENV_MAP` entry, and the module itself; delete `Settings.tools` field. Grep for `config.tools` first.
  - `settings.reference.json:66` — delete `"result_persist_chars": 50000`.
  - `tool_io.tool_output` threshold lookup: `info.spill_threshold_chars` (if set) else `SPILL_THRESHOLD_CHARS` (imported constant; replaces old `config.tools.result_persist_chars` fallback).
  - `tests/` — `grep -rn max_result_size` and rename all references; keep any test covering `file_read spill_threshold_chars=math.inf` behavior.

- `co_cli/context/_history_processors.py` — add `enforce_turn_budget(ctx: RunContext[CoDeps], messages: list[ModelMessage]) -> list[ModelMessage]`. Behavior per Design → L2 firing rules. Register in `co_cli/agent/core.py:143-147` between `evict_old_tool_results` and `proactive_window_processor`.

- `co_cli/context/compaction.py` — extend `compaction.proactive_check` span (`compaction.py:495-557`) with the two new attrs from Observability table.

tests/test_flow_spill_threshold.py (new):
- Constants pinned: `SPILL_THRESHOLD_CHARS == 4_000`, `TOOL_RESULT_PREVIEW_CHARS == 1_500`.
- Bootstrap copy verified.
- Single threshold path: payloads at 3_999 / 4_001 / 10_000 chars → no-spill / spill / spill.
- file_read still exempt: 5_000-char file_read payload does NOT spill (math.inf override retained); verify no stub is produced.
- shell no longer has a 30K override: 5_000-char shell payload spills at the 4_000 default; resulting stub contains `"This tool result was too large"` (opening line), `"file_read"` (not `"read_file"`), and `"start_line/end_line"`.
- **Force-spill size guard**: `spill_if_oversized(content, force=True)` with a 200-char payload (e.g. a `MaxToolCallsExceededPayload` JSON) returns content unchanged. With a 1_500-char payload (= `TOOL_RESULT_PREVIEW_CHARS`) returns content unchanged. With a 1_501-char payload returns a stub. Pins the guard so a regression that strips it surfaces here.

tests/test_flow_turn_budget.py (new):
- Sort-largest-first: 4K/6K/8K-token messages, `deps.turn_aggregate_threshold_tokens=5_000`, aggregate=18K → first iteration spills 8K (becomes ~450-token stub), aggregate now `4K+6K+450 = 10_450 > 5K`; second iteration spills 6K (~450 stub), aggregate now `4K+2×450 = 4_900 ≤ 5K`; loop exits. Assert exactly 2 spills, 8K spilled before 6K (sort verified), 4K message untouched.
- Below-threshold: 2 × 3K-token messages, threshold=12K → no spill, `skip_reason="below_threshold"`.
- All-already-spilled bails out: enough `PERSISTED_OUTPUT_TAG`-marked messages to push aggregate over a tight `deps.turn_aggregate_threshold_tokens=2_000` (e.g. 5+ stubs at ~450 tokens each totals ~2_250). Assert returned messages are identical to input (no transformation); assert `apply_compaction` was NOT invoked (patch with a recording wrapper, expect zero calls); span `spill_fired=False`, `skip_reason="no_candidates_all_spilled"`, `tokens_after == tokens_before`. After L2 bails, L3 (`proactive_window_processor`) runs next in the chain; if L3 also doesn't compact and the LLM call overflows, the overflow-recovery chain engages. Verifying L3 and the overflow-recovery chain is out of scope for L2's tests.
- Cache-read: `deps.turn_aggregate_threshold_tokens=12_000`; mutate `deps.config.compaction.tail_fraction=0.50`; aggregate 13K tokens; assert spill fires using cached 12K, not recomputed 30K.

tests/test_flow_spill_otel.py (new): in-memory OTEL exporter; small + large payloads; verify `tool_budget.spill_tool_result` fires with correct `spill.fired`.

done_when:
- `grep -rn "result_persist_chars" co_cli/ tests/ settings.reference.json` returns no results.
- `ToolInfo.max_result_size` renamed to `spill_threshold_chars`; `register()` kwarg renamed; `tool_output` per-tool override path retained. `config/tools.py` module and `Settings.tools` field deleted. `shell` override removed; `file_read spill_threshold_chars=math.inf` retained.
- `tool_output` reads `info.spill_threshold_chars` (if set) else `SPILL_THRESHOLD_CHARS` (imported constant); `enforce_turn_budget` reads `deps.turn_aggregate_threshold_tokens`. No recomputation at any read site.
- `enforce_turn_budget` registered between `evict_old_tool_results` and `proactive_window_processor`.
- `tool_budget.spill_tool_result`, `tool_budget.enforce_turn_aggregate`, and the shared `tool_budget.resolved` spans emit per Observability table.
- `compaction.proactive_check` span includes both new attrs.
- `uv run pytest tests/test_flow_spill_threshold.py tests/test_flow_turn_budget.py tests/test_flow_spill_otel.py tests/test_flow_compaction_summarization.py tests/test_flow_compaction_proactive.py -x` green.

success_signal: aggregate budget honored even when per-call spill doesn't fire; per-call spill fires at 4_000 chars for all tools except file_read (shell loses its own 30K override and falls to 4K default; file_read keeps `math.inf`); a single trace per turn shows L0 → per-call spills → L2 → L3 end-to-end.

### TASK-3 — Spec sync + CHANGELOG + quality gate

prerequisites: [TASK-2]

files:
- `docs/specs/compaction.md` (lines 21, 191, 263, 270, 313, 340, 521, 540, 551, 565) — replace removed-batch-spill refs and `ctx_token_budget` mentions with new layered design + unified `model_max_ctx` budget.
- `docs/specs/core-loop.md` (lines 253, 265, 280) — same.
- `docs/specs/prompt-assembly.md` (line 80) — same.
- `docs/specs/config.md` (lines 150, 206, 322) — drop `ctx_token_budget` row. Do **not** add a `max_tool_calls_per_turn` config field; `MAX_TOOL_CALLS_PER_MODEL_TURN` is a flat module constant. Add a section documenting `CoDeps.turn_aggregate_threshold_tokens` as the only bootstrap-cached tool-budget value (with file:line ref to `bootstrap/core.py`); document `MAX_TOOL_CALLS_PER_MODEL_TURN` and `SPILL_THRESHOLD_CHARS` as module constants (file:line refs to `_tool_call_limit.py` and `tool_io.py`) — explicitly note they are NOT on `CoDeps`.
- `docs/specs/system.md` (verify exact path) — paragraph on the `co-cli.tool_budget` tracer and what each span answers.
- `CHANGELOG.md` — version bump + entries:
  - **Feature**: tool-call cap (`MAX_TOOL_CALLS_PER_MODEL_TURN = 6`); per-llm-turn aggregate spill (force-spill largest-first, bails to existing overflow-recovery when candidates exhausted); per-call refit (`SPILL_THRESHOLD_CHARS = 4_000`, was 50_000); OTEL coverage on `co-cli.tool_budget`.
  - **Behavior**: `shell` results > 4_000 chars now spill (was 30_000); `file_read` retains `math.inf` exemption — no behavior change there.

done_when:
- `grep -rn "batch_spill_chars\|evict_batch_tool_outputs\|M2b\|ctx_token_budget" docs/specs/` returns no results.
- Each touched spec has a section referencing the new fields with file:line refs.
- `scripts/quality-gate.sh full` green (lint + full pytest, log to `.pytest-logs/<ts>-full.log`).
- Staged files scoped to the refactor — no incidental changes.

success_signal: specs reflect actual behavior; full test suite green; ready to ship.

## Post-ship validation

Sibling eval plan (out of scope here):
- 95th percentile of `tool_calls.issued` vs `MAX_TOOL_CALLS_PER_MODEL_TURN = 6` — consistent saturation indicates larger contexts are under-utilized; revisit the constant or switch to a context-scaled formula.
- `turn_aggregate.skip_reason="no_candidates_all_spilled"` rate — surface multi-iteration tail-saturation frequency. Note this is a *soft* signal: L2's threshold (`tail_fraction × model_max_ctx`) is below the actual context limit (`model_max_ctx`), so a bail-out doesn't necessarily mean the next LLM call overflows. The real signal is correlation between this rate and the existing overflow-recovery firing rate; high `no_candidates_all_spilled` *with* high overflow-recovery → real saturation, revisit L0 cap or L1 threshold; high `no_candidates_all_spilled` *alone* → L2 threshold may be too tight, revisit `tail_fraction`.

## Follow-ups (separate plans)

- Per-tool threshold override is retained (`ToolInfo.spill_threshold_chars`). Currently only `file_read` uses it (`math.inf`). Add new overrides only when a real driving use case appears.
- `web_fetch` truncates via its own `max_content_size` — confirm sufficient under the new system default; tighten if eval evidence shows otherwise.
- **Cache the proactive-compaction threshold for symmetry**: `proactive_window_processor` (`compaction.py:470`) recomputes `int(model_max_ctx × cfg.compaction_ratio)` each check — same immutability story. Add `CoDeps.proactive_compaction_threshold_tokens`, populate at bootstrap, read in the processor.
- `/reload-config` — would need to rerun bootstrap for all four cached thresholds. Not a feature today.
- OTEL cardinality: at heavy parallelism, ~12 spans/turn from per-call spill. If span budget becomes a problem post-eval, downgrade `tool_budget.spill_tool_result` to a counter metric and keep span only for actual spills.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Once approved: `/orchestrate-dev tool-aggregate-budget-spill`
