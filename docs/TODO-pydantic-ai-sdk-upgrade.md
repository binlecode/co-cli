# TODO: Pydantic AI SDK Upgrade — 1.70.0 → 1.73.0

**Task type: refactor** (compatibility upgrade — no new user-visible behavior)

## Context

`co-cli` pins `pydantic-ai==1.70.0`. Three minor releases have shipped since then (1.71, 1.72, 1.73). The upgrade is a compatibility-first bump: bring the pin current, eliminate the known upgrade risks, and verify nothing regressed. Architecture adoption of new SDK features (Capabilities, Hooks, Thinking) is explicitly out of scope and tracked separately.

**Current-state validation findings:**

1. **Phantom risk eliminated — `_function_toolset`**: Original TODO warned about `agent._function_toolset` private attribute inspection in tests. Grep finds zero such usage. This risk does not exist.

2. **`test_context_overflow.py` / `test_doom_loop.py` referenced in original TODO do not exist**: The files were never created. Tasks referencing them are scoped to the tests that exist: `test_orchestrate.py` (absent — event handling tested via functional coverage in `test_commands.py`), `test_history.py`, `test_tool_approvals.py`.

3. **`RunContext` is already in the public API**: `from pydantic_ai import RunContext` works today. Tests import from `pydantic_ai._run_context` unnecessarily. Fix is a single-line import swap per file — no constructor or usage changes needed.

4. **Production code is clean**: No `pydantic_ai._*` imports exist in `co_cli/`. The private-import risk is test-only.

5. **`_bootstrap.py` `system_prompt=`**: This is a `CoConfig` field assignment (`dataclasses.replace(config, system_prompt=...)`), not an `Agent` constructor arg. It is not in scope for prompt-style standardization.

6. **Prompt-style split confirmed**: `Agent()` uses `instructions=` in `agent.py` (main agent, task agent) and `_history.py` (summarizer). Uses `system_prompt=` in `_subagent_agents.py` (4 subagent factories) and `_consolidator.py` and `_signal_detector.py`. Standardization decision needed.

7. **`InstrumentationSettings` import inconsistency confirmed**: `co_cli/main.py` uses `pydantic_ai.models.instrumented`; `evals/_trace.py` uses `pydantic_ai.agent`. Must be resolved to one path.

**Workflow hygiene**: No stale TODO files with all tasks `✓ DONE` found.

## Problem & Outcome

**Problem:** `co-cli` is on `pydantic-ai==1.70.0`, 3 minor releases behind.

**Failure cost:** Tests import from private `pydantic_ai._run_context` — any internal rename in those 3 releases would silently break the test suite. Instrumentation import inconsistency causes confusion. `system_prompt=` vs `instructions=` split is an accidental divergence, not a deliberate choice.

**Outcome:** Pin is `pydantic-ai==1.73.0`. Zero private SDK imports in `tests/`. Single instrumentation import path. Deliberate and documented prompt-style decision. Full test suite passes. No user-visible regression.

## Scope

**In scope — Phase 1 (this TODO):**
- Version bump
- Eliminate private test imports
- Normalize instrumentation import path
- Standardize prompt constructor style
- Verify orchestration and history contracts still hold
- Full regression

**Out of scope (follow-up TODO):**
- Capabilities adoption
- Hooks adoption
- Thinking capability
- State-isolated toolsets
- AgentSpec / YAML agents
- Any provider not currently used by co-cli

## Behavioral Constraints

- No user-visible behavior change from this upgrade. Every approval flow, MCP tool interaction, history compaction, reasoning subagent, and observability trace must behave identically before and after.
- `system_prompt=` and `instructions=` must not be mixed arbitrarily after TASK-4. A deliberate choice is required and must apply consistently within each category (main agent, subagents, singleton agents).
- `RunContext` construction in tests must use only the public `pydantic_ai` API after TASK-1. No `pydantic_ai._*` import may remain.
- The version pin in `pyproject.toml` must be exact (`pydantic-ai==1.73.0`), not a range.
- Instrumentation must use a single import path after TASK-3.

## High-Level Design

Phase 1 (pre-bump): De-risk the test harness by eliminating private imports. No version change yet.

Phase 2 (bump): Change the pin, run `uv sync`, record transitive dependency changes if any. Fix any immediate compile/runtime errors only.

Phase 3 (post-bump verification and cleanup): Normalize instrumentation import, standardize prompt style, run targeted verification tests, run full regression.

Each task is scoped to be completable in a single agent session with ≤5 files.

## Implementation Plan

### TASK-1 — Remove private SDK imports from tests

Fix all `pydantic_ai._run_context` imports across the test suite before touching the version pin. All seven files use `from pydantic_ai._run_context import RunContext` — replace with `from pydantic_ai import RunContext`. No constructor or call-site changes needed.

**Implementation note:** `tests/test_tool_calling_functional.py` line 244 has a local import inside a function body — remove it from that function scope, not just from the module header. All other files have top-level imports only.

**files:**
- `tests/test_capabilities.py`
- `tests/test_history.py`
- `tests/test_memory.py`
- `tests/test_memory_decay.py`
- `tests/test_tools_files.py`
- `tests/test_subagent_tools.py`
- `tests/test_tool_calling_functional.py`

**done_when:**
1. `grep -r "pydantic_ai\._" tests/` returns no output.
2. `uv run pytest tests/test_capabilities.py tests/test_history.py tests/test_memory.py tests/test_memory_decay.py tests/test_tools_files.py tests/test_subagent_tools.py tests/test_tool_calling_functional.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task1.log` exits 0 (pre-bump, still on 1.70.0 — confirms public constructor is signature-compatible).

**success_signal:** N/A (test-harness only; no user-visible change)

---

### TASK-2 — Bump version pin and sync

**prerequisites:** [TASK-1]

Change the pin, sync, record any transitive changes.

**files:**
- `pyproject.toml`
- `uv.lock` (updated by `uv sync`)

**done_when:** `grep "pydantic-ai" pyproject.toml` shows `pydantic-ai==1.73.0` and `uv run python -c "import pydantic_ai; print(pydantic_ai.__version__)"` prints `1.73.0`.

**success_signal:** N/A (dependency-only change; no user-visible behavior at this step)

---

### TASK-3 — Normalize InstrumentationSettings import path

**prerequisites:** [TASK-2]

Both `co_cli/main.py` and `evals/_trace.py` must import `InstrumentationSettings` from the same public path. Choose whichever is the canonical public path in 1.73.0 (verify via `python -c "from pydantic_ai.agent import InstrumentationSettings"` and `from pydantic_ai.models.instrumented import InstrumentationSettings"`). Use the one that exists in 1.73.0; if both exist, prefer `pydantic_ai.agent` as the higher-level module.

**files:**
- `co_cli/main.py`
- `evals/_trace.py`

**done_when:** `grep -r "InstrumentationSettings" co_cli/ evals/` shows both lines import from the same module path.

**success_signal:** N/A (import normalization; instrumentation behavior unchanged)

---

### TASK-4 — Standardize prompt constructor style

**prerequisites:** [TASK-2]

Decision: standardize all `Agent(...)` constructor calls in `co_cli/` on `instructions=`. The SDK added `instructions=` as the preferred API; `system_prompt=` is the older form. Subagent factories and singleton agents should match the main agent style.

Files to update:
- `co_cli/tools/_subagent_agents.py` — 4 factories currently use `system_prompt=`
- `co_cli/memory/_consolidator.py` — uses `system_prompt=`
- `co_cli/memory/_signal_detector.py` — uses `system_prompt=`

Do not change `co_cli/bootstrap/_bootstrap.py` — its `system_prompt=` is a `CoConfig` field assignment, not an `Agent` constructor arg.

**files:**
- `co_cli/tools/_subagent_agents.py`
- `co_cli/memory/_consolidator.py`
- `co_cli/memory/_signal_detector.py`

**done_when:**
1. `grep -n "system_prompt=" co_cli/tools/_subagent_agents.py co_cli/memory/_consolidator.py co_cli/memory/_signal_detector.py` returns no output (only `co_cli/bootstrap/_bootstrap.py` and `co_cli/deps.py` may retain `system_prompt` as a field name).
2. `uv run pytest tests/test_subagent_tools.py tests/test_memory.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task4.log` exits 0.

**success_signal:** N/A (constructor arg normalization; no behavioral change)

---

### TASK-5 — Revalidate orchestration and approval flow

**prerequisites:** [TASK-2]

Run the test files that exercise orchestration, approval gating, tool events, and command dispatch. The goal is to surface any event shape or contract change introduced in 1.71–1.73 that affects `_orchestrate.py`.

Key surfaces to verify:
- `run_stream_events(...)` event ordering
- `FunctionToolCallEvent` / `FunctionToolResultEvent` shape
- `AgentRunResultEvent(result=...)` behavior
- `DeferredToolRequests` / `DeferredToolResults` resume loop (allow/deny/repeated hops)
- `ToolReturnPart` treatment of retry/error results

**files:** (no file changes expected; test execution only — fix `_orchestrate.py` or `_tool_approvals.py` only if a real regression is found)

**done_when:**
```
uv run pytest tests/test_commands.py tests/test_agent.py tests/test_task_control.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task5.log
```
exits 0 with no failures. `test_commands.py` covers `test_approval_approve` and `test_approval_deny` (the full DeferredToolRequests/DeferredToolResults loop); `test_agent.py` covers approval wiring (`requires_approval` flags).

**success_signal:** Approval-gated tools still prompt for allow/deny and resume correctly after the bump.

---

### TASK-6 — Revalidate history processor contracts

**prerequisites:** [TASK-2]

Verify that `history_processors=[...]` execution order, `ModelRequest`/`ModelResponse` construction, `ToolReturnPart` handling, and truncation/compaction behavior are unchanged in 1.73.0.

Key surfaces:
- `history_processors=` still fires in order with same sync/async semantics
- `ToolReturnPart.content` conventions unchanged
- Compaction and summary injection still produce valid resumed-turn history
- Eval fixtures in `evals/_fixtures.py` still construct valid message histories

**files:** (no file changes expected; fix `_history.py` only if a real regression is found)

**done_when:**
```
uv run pytest tests/test_history.py -x 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-task6.log
```
exits 0 with no failures.

**success_signal:** History compaction and summary injection behave identically to pre-upgrade behavior.

---

### TASK-7 — Full regression run

**prerequisites:** [TASK-3, TASK-4, TASK-5, TASK-6]

Run the complete test suite. If any failure appears, stop and root-cause before proceeding — never rerun past a known failure.

**files:** (no changes expected; this is a verification gate)

**done_when:**
```
uv run pytest 2>&1 | tee .pytest-logs/$(date +%Y%m%d-%H%M%S)-full-regression.log
```
exits 0 with no failures.

**success_signal:** Full test suite green on `pydantic-ai==1.73.0`. Ship is unblocked.

---

## Testing

All verification is done through the existing pytest suite:
- TASK-5 targets `test_commands.py`, `test_agent.py`, `test_task_control.py`
- TASK-6 targets `test_history.py`
- TASK-7 runs the full suite

No new test files are required for this compatibility upgrade. If a regression is found in TASK-5 or TASK-6, add a targeted regression test as part of fixing it — do not add tests preemptively.

Evals to run after TASK-7 if time permits (not blocking):
- `evals/eval_conversation_history.py`
- `evals/eval_thinking_subagent.py`

## Open Questions

None. All open questions from the original TODO are answerable from source inspection.

- "Is `system_prompt=` still valid in 1.73.0?" — Yes, both forms are accepted; standardizing to `instructions=` is a style decision, not a forced migration.
- "Which `InstrumentationSettings` import path is canonical?" — Determine at TASK-3 execution time by testing both imports under 1.73.0.
- "Does `RunContext` constructor shape change in 1.73.0?" — Unlikely given no breaking changes are noted. TASK-1 verifies by running `pytest -x` after the import swap but before the version bump.

## Final — Team Lead

Plan approved. C2 Core Dev confirmed all blocking issues resolved. PO was clean in C1.

> Gate 1 — PO + TL review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev pydantic-ai-sdk-upgrade`
