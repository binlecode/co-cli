# TODO: Context Management — pydantic-ai Alignment

**Slug:** `context-mgmt-alignment`
**Task type:** `code-feature` (T1–T3) + documentation-only (T4)
**Status:** Draft — awaiting Gate 1

---

## Context

Four gaps were confirmed by reviewing pydantic-ai source (`~/workspace_genai/pydantic-ai`, commit `edff59fe6`) and co-cli context management (`co_cli/context/`).

**Current-state validation notes:**

- `ctx.context_window_used` claimed in the original Background is a **phantom feature** — it does not exist in pydantic-ai. `RunContext` exposes `ctx.usage: RunUsage` with `input_tokens` / `output_tokens`; `ModelProfile` has no `context_window` attribute. T1 is revised to use `ModelResponse.usage.input_tokens` (real provider-reported token counts) instead.
- `ThinkingPart` is imported in `_orchestrate.py` but NOT in `_history.py`. T2 requires adding that import.
- `UnexpectedModelBehavior` and `IncompleteToolCall` confirmed at `pydantic_ai.exceptions:191,296`. T3 is accurate.
- T4 (doc side-effects) has no phantom feature; the code at lines 703–814 is as described.

**Workflow hygiene:** No prior stale TODO for this slug.

---

## Problem & Outcome

**Problem:** Four concrete gaps exist between co-cli's context management layer and pydantic-ai's current API surface, causing silent failures, incorrect compaction triggers, and unhandled exceptions.

**Failure cost:**
- T1: Cloud model sessions (Anthropic, OpenAI, Gemini) use a chars/4 heuristic and fire compaction at ~85k estimated tokens regardless of the model's actual context window, causing premature compaction or—on large-context models—silent under-compaction when actual usage exceeds the 100k budget.
- T2: Sessions using a reasoning-capable model (Anthropic claude-3-7 extended thinking) may trim a ThinkingPart-only response at the head/tail boundary, producing a context the model cannot properly interpret.
- T3: An `IncompleteToolCall` mid-stream (caused by context overflow) propagates as an unhandled exception to the chat REPL — the user sees a Python traceback instead of a recoverable status message.
- T4: The intentional side-effect pattern in history processors has no documentation, making it a maintenance hazard and a source of future regressions.

**Outcome:** Compaction triggers on real provider token counts; `ThinkingPart` is never orphaned by boundary logic; `IncompleteToolCall` is caught and surfaced gracefully; the side-effect pattern is documented.

---

## Scope

In scope:
- `co_cli/context/_history.py`: token estimation, `_find_first_run_end`, `_align_tail_start`, `truncate_tool_returns`, `detect_safety_issues`, `inject_opening_context`
- `co_cli/context/_orchestrate.py`: `_execute_stream_segment` exception handling

Out of scope:
- `_check_output_limits` (Ollama-only post-execution path, no `RunContext`)
- Adding a model context-window registry or new config scalars
- Refactoring the side-effect pattern in T4 (cost exceeds benefit)

---

## Behavioral Constraints

- **T1 fallback mandatory:** When `ModelResponse.usage.input_tokens` is 0 or messages contain no `ModelResponse`, fall back to `_estimate_message_tokens`. The char estimate path must not be removed.
- **T1 Ollama budget unchanged in `_check_output_limits`:** The Ollama path in `_check_output_limits` must not be changed. T1 replaces only the char-estimate token-count source in the compaction processors.
- **T1 Ollama budget in compaction processors (new behavior):** When `uses_ollama_openai()` is True and `llm_num_ctx > 0`, the budget for both `truncate_history_window()` and `precompute_compaction()` becomes `llm_num_ctx`; otherwise `_DEFAULT_TOKEN_BUDGET`. This is a new behavioral branch — verify against at least one Ollama test case.
- **T2 never drops a pair:** A `[ThinkingPart, TextPart]` pair within the same `ModelResponse` must never be split by boundary logic. Since both parts belong to the same message object, boundary logic operating on messages cannot split them; the constraint is enforced by keeping `_find_first_run_end` operating at message granularity.
- **T2 ThinkingPart-only response pinning:** A `ModelResponse` whose only part is a `ThinkingPart` (no `TextPart`) must be accepted as a valid first-run-end anchor. Not doing so leaves only the initial `ModelRequest` pinned, silently dropping the first thinking turn from the head.
- **T3 no retry:** `UnexpectedModelBehavior` is a framework-level structural error, not a transient network failure. It must never enter the HTTP retry loop.
- **T3 outcome field:** `turn_state.outcome` must be set to `"error"` before returning, so the OTel span and chat loop receive the correct outcome. The status message must include `str(e)` so the user has actionable context about what the model returned.
- **T4 behavior unchanged:** No logic changes in the processors. Comment additions only.

---

## High-Level Design

### T1 — Real token counts in compaction processors

`ModelResponse` carries `usage: RequestUsage` with `input_tokens` populated for all cloud providers (Anthropic, OpenAI, Gemini). Scanning the message list for the most recent `ModelResponse` with non-zero `input_tokens` yields an accurate token count without any new config or registry additions.

A single helper `_latest_response_input_tokens(messages) -> int` scans in reverse for the first `ModelResponse` with `usage.input_tokens > 0` and returns it; returns 0 when none is found. Both `truncate_history_window()` and `precompute_compaction()` call this helper. The value is the provider-reported per-request input count from the most recent model call — history-anchored and available with or without `RunContext`.

Budget for both functions: `deps.config.llm_num_ctx` when `uses_ollama_openai()` and `llm_num_ctx > 0`, otherwise `_DEFAULT_TOKEN_BUDGET`. This is a new Ollama branch in the compaction processors (previously they only used `_DEFAULT_TOKEN_BUDGET`).

Both retain the char-estimate path as fallback when real counts are unavailable (local/custom models with no usage reporting).

### T2 — ThinkingPart in boundary logic

`_find_first_run_end()`: currently requires `TextPart` to anchor the first run end. A `ModelResponse` with only `ThinkingPart` is an equally valid first-run anchor — it's the first substantive model output. Updated to accept `ThinkingPart` OR `TextPart` as qualifying parts.

`_align_tail_start()`: no change needed. The function advances over orphaned `ToolReturnPart` requests; `ThinkingPart` appears in `ModelResponse` (not `ModelRequest`) so it is unaffected by this logic.

`truncate_tool_returns()`: operates on `ModelRequest` messages only, looking for `ToolReturnPart`. `ThinkingPart` lives in `ModelResponse` and is never reached by this function. **No change needed.**

The net change: import `ThinkingPart` in `_history.py`; update the qualifying condition in `_find_first_run_end()`.

### T3 — Catch UnexpectedModelBehavior

Add an `except UnexpectedModelBehavior` block inside the `while True` retry loop in `run_turn()`, placed after the existing `ModelHTTPError` / `ModelAPIError` / `TimeoutError` handlers. Emits a user-visible status, sets `outcome = "error"`, returns `_build_error_turn_result`. Import `UnexpectedModelBehavior` from `pydantic_ai.exceptions`.

### T4 — Document side-effects in processors

Add a comment block at the top of `detect_safety_issues()` and `inject_opening_context()` explaining: (1) deliberate deviation from pure-transformer contract, (2) why state cannot be local (pydantic-ai constructs a fresh call per request), (3) the invariant that makes this safe (`reset_for_turn()` at each foreground turn entry).

---

## Implementation Plan

### ✓ DONE — TASK-1 — Use real token counts in compaction processors

**files:**
- `co_cli/context/_history.py`

**What to do:**
1. Add a helper `_latest_response_input_tokens(messages) -> int` that scans in reverse for the first `ModelResponse` with `usage.input_tokens > 0` and returns it; returns 0 if none.
2. In both `truncate_history_window()` and `precompute_compaction()`:
   - Call `_latest_response_input_tokens(messages)`.
   - If result > 0, use it as `token_count`; else fall back to `_estimate_message_tokens(messages)`.
   - Budget: `llm_num_ctx` if `uses_ollama_openai()` and `llm_num_ctx > 0`, else `_DEFAULT_TOKEN_BUDGET`.
   - Threshold stays at `int(budget * 0.85)`.
3. `_estimate_message_tokens` is unchanged (still the fallback path).
4. `ModelResponse` is already imported. `RequestUsage` is not needed explicitly — `usage.input_tokens` is accessed via attribute.
5. Do NOT use `ctx.usage.input_tokens` — it is a run-level cumulative accumulator and may be zero on the first processor call of a turn.

**done_when:**
```
uv run pytest tests/test_context_compaction.py -x
```
Three test cases required:
1. Messages containing `ModelResponse(usage=RequestUsage(input_tokens=90_000))` — verify compaction triggers (> 85% of `_DEFAULT_TOKEN_BUDGET`).
2. Messages with no `ModelResponse` usage data — verify char-estimate fallback path still triggers compaction correctly.
3. Ollama budget branch: `CoConfig` with `uses_ollama_openai()` returning True and `llm_num_ctx=8192`; messages with `input_tokens=7_200` (> 85% of 8192) — verify compaction triggers against `llm_num_ctx`, not `_DEFAULT_TOKEN_BUDGET`.

**success_signal:** A Anthropic/OpenAI chat session that has consumed 95k tokens triggers compaction based on the provider-reported count rather than the chars/4 heuristic.

---

### ✓ DONE — TASK-2 — Handle ThinkingPart in _find_first_run_end

**files:**
- `co_cli/context/_history.py`

**What to do:**
1. Add `ThinkingPart` to the `pydantic_ai.messages` import block at the top of `_history.py`.
2. In `_find_first_run_end()`, change the qualifying condition from `any(isinstance(p, TextPart) for p in msg.parts)` to `any(isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts)`.
3. No change to `_align_tail_start()` — it handles `ModelRequest` / `ToolReturnPart` only.
4. No change to `truncate_tool_returns()` — `ThinkingPart` is in `ModelResponse`, unreachable from that function.

**done_when:**
```
uv run pytest tests/test_context_thinking.py -x
```
Test constructs a message history where the first `ModelResponse` contains only `ThinkingPart` (no `TextPart`), calls `_find_first_run_end`, and asserts it returns the index of that response (not 0). A second case confirms a response with both `ThinkingPart` and `TextPart` is also correctly anchored.

**success_signal:** A session using claude-3-7 extended thinking with no text in the first turn response retains that turn as the head anchor in compaction rather than losing it.

---

### ✓ DONE — TASK-3 — Catch UnexpectedModelBehavior in run_turn

**files:**
- `co_cli/context/_orchestrate.py`

**What to do:**
1. Extend the existing `from pydantic_ai.exceptions import ModelHTTPError, ModelAPIError` line at `_orchestrate.py:20` to include `UnexpectedModelBehavior`. Do not add a new import statement.
2. Inside the `while True` retry loop in `run_turn()`, add:
   ```python
   except UnexpectedModelBehavior as e:
       frontend.on_status(f"Model returned malformed output: {e}")
       turn_state.outcome = "error"
       return _build_error_turn_result(turn_state)
   ```
   Place it after `TimeoutError` and before `(KeyboardInterrupt, asyncio.CancelledError)`.

**done_when:**
```
uv run pytest tests/test_orchestrate_error_handling.py -x
```
Preferred gate: integration assertion that confirms `run_turn()` returns `TurnResult(outcome="error")` when `UnexpectedModelBehavior` propagates from `_execute_stream_segment`. Fallback (if a live trigger requires a new test fixture beyond current scope):
```
grep -n "UnexpectedModelBehavior" co_cli/context/_orchestrate.py
# must show: import line and except block
```

**success_signal:** An `IncompleteToolCall` mid-stream shows a status message in the chat REPL ("Model returned malformed output: …") instead of a Python traceback.

---

### ✓ DONE — TASK-4 — Document side-effect deviation in processors

**files:**
- `co_cli/context/_history.py`

**What to do:**
Add the following comment block at the top of `detect_safety_issues()`:
```python
# INTENTIONAL DEVIATION from pydantic-ai's pure-transformer contract:
# This processor writes to ctx.deps.runtime.safety_state (doom_loop_injected,
# reflection_injected). Pure transformers should not mutate deps.
#
# Why state cannot be local: pydantic-ai constructs a fresh processor call per
# model request. Local variables would not survive across segments within a
# single turn (e.g. initial segment + approval-resume segments).
#
# Safety invariant: safety_state is reset by reset_for_turn() at each foreground
# turn entry, so cross-turn state leakage cannot occur.
```

Add an equivalent block at the top of `inject_opening_context()`:
```python
# INTENTIONAL DEVIATION from pydantic-ai's pure-transformer contract:
# This processor writes to ctx.deps.session.memory_recall_state
# (last_recall_user_turn, recall_count). Pure transformers should not mutate deps.
#
# Why state cannot be local: same reasoning as detect_safety_issues() — fresh
# call per request, state would not survive across segments.
#
# Safety invariant: memory_recall_state is initialised fresh per session in
# CoSessionState.__post_init__; it does not leak across sessions.
```

**done_when:**
```
grep -n "INTENTIONAL DEVIATION" co_cli/context/_history.py
# must return exactly 2 matches: one in detect_safety_issues, one in inject_opening_context
```

**success_signal:** N/A (documentation only).

---

## Testing

- **TASK-1**: New `tests/test_context_compaction.py`. Construct `ModelResponse` objects with real `usage.input_tokens` values. Exercise both `truncate_history_window` (via a `RunContext` with `CoDeps`) and `precompute_compaction` directly. No LLM call required — these are pure functions over message lists.
- **TASK-2**: New `tests/test_context_thinking.py`. Construct message lists with `ThinkingPart`-only responses. Call `_find_first_run_end` directly (it's a module-level function). No LLM call required.
- **TASK-3**: New `tests/test_orchestrate_error_handling.py` or extend existing orchestrate tests. Structural: confirm import and except block exist. Integration: existing error-path tests can be scanned for coverage; a live trigger of `IncompleteToolCall` is not required for the `done_when` gate.
- **TASK-4**: No test required. `done_when` is a grep check.

---

## Open Questions

None. All questions answerable by inspection:
- `ctx.context_window_used` existence: **not present** (`RunContext` source confirms).
- `ThinkingPart` import path: `pydantic_ai.messages` (`_orchestrate.py:27` already imports it).
- `UnexpectedModelBehavior` import path: `pydantic_ai.exceptions:191` confirmed.
- Whether `_align_tail_start` needs ThinkingPart changes: **no** (operates on `ModelRequest` only).
- Whether `truncate_tool_returns` needs ThinkingPart changes: **no** (operates on `ModelRequest` only).


## Final — Team Lead

Plan approved. All blocking items resolved. All minor issues adopted.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev context-mgmt-alignment`

---

## Independent Review

| File | Finding | Severity | Task |
|------|---------|----------|------|
| `co_cli/context/_history.py:688,752` | Comments placed inside docstrings instead of as `#` blocks after docstring — fixed before integration | blocking (fixed) | TASK-4 |

**Overall: 1 blocking (fixed before integration)**

---

## Delivery Summary — 2026-03-31

| Task | done_when | Status |
|------|-----------|--------|
| TASK-1 | `uv run pytest tests/test_context_compaction.py -x` (3 cases) | ✓ pass |
| TASK-2 | `uv run pytest tests/test_context_thinking.py -x` (2 cases) | ✓ pass |
| TASK-3 | `uv run pytest tests/test_orchestrate_error_handling.py -x` | ✓ pass |
| TASK-4 | `grep -n "INTENTIONAL DEVIATION" co_cli/context/_history.py` → 2 matches | ✓ pass |

**Tests:** full suite — 257 passed, 0 failed
**Independent Review:** 1 blocking (TASK-4 comment placement in docstring vs body — fixed)
**Doc Sync:** fixed — `DESIGN-context.md` (compaction token-counting section), `DESIGN-core-loop.md` (compaction behavior + UnexpectedModelBehavior in retry matrix)

**Overall: DELIVERED**
All four gaps between co-cli's context management layer and pydantic-ai's current API surface are closed: real provider token counts drive compaction, ThinkingPart-only turns are preserved as head anchors, UnexpectedModelBehavior surfaces gracefully, and the processor side-effect pattern is documented.

---

## Implementation Review — 2026-03-31

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `pytest tests/test_context_compaction.py -x` (3 cases) | ✓ pass | `_history.py:396` — `_latest_response_input_tokens` scans in reverse for `ModelResponse.usage.input_tokens > 0`; `_history.py:438-446` — used in `truncate_history_window`; `_history.py:520-528` — used in `precompute_compaction`; Ollama budget branch at both call sites |
| TASK-2 | `pytest tests/test_context_thinking.py -x` (2 cases) | ✓ pass | `_history.py:34` — `ThinkingPart` imported; `_history.py:96` — `any(isinstance(p, (TextPart, ThinkingPart)) for p in msg.parts)` |
| TASK-3 | `pytest tests/test_orchestrate_error_handling.py -x` | ✓ pass | `_orchestrate.py:20` — `UnexpectedModelBehavior` in import; `_orchestrate.py:545-548` — `except UnexpectedModelBehavior as e:` after `TimeoutError`, before `(KeyboardInterrupt, CancelledError)`; `outcome="error"` set, `_build_error_turn_result` returned |
| TASK-4 | `grep -n "INTENTIONAL DEVIATION"` → 2 matches | ✓ pass | `_history.py:688` — comment in `inject_opening_context`; `_history.py:751` — comment in `detect_safety_issues` |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Cross-doc index missing `DESIGN-context.md` and `DESIGN-observability.md` | `docs/DESIGN-system.md:348-352` | minor | Added both rows to Files table |

### Tests
- Command: `uv run pytest -v`
- Result: 257 passed, 0 failed, 1 warning (pre-existing `PytestUnraisableExceptionWarning` from `BaseSubprocessTransport.__del__`, unrelated to this delivery)
- Log: `.pytest-logs/` (timestamped)

### Doc Sync
- Scope: full
- Result: fixed — `docs/DESIGN-system.md` cross-doc index added `DESIGN-context.md` and `DESIGN-observability.md`. `DESIGN-context.md` and `DESIGN-core-loop.md` were already accurate (updated by the delivery).

### Behavioral Verification
- `uv run co config`: ✓ healthy — all components start, changed modules load without error
- T3 user-visible surface (status message vs traceback on `UnexpectedModelBehavior`) verified structurally — live trigger requires malformed model output, confirmed by import + handler check in test suite.

### Overall: PASS
All spec requirements met with file:line evidence. Full suite green. No blocking findings survived adversarial self-review. Doc sync clean. Ship directly.
