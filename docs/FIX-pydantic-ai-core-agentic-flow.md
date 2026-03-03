# FIX Plan: Align co-cli Core Agentic Flow with Idiomatic pydantic-ai

Date: 2026-03-02
Status: Active — A-P0 done; Workstreams B–E pending

## Objective

Fix high-impact mismatches between co-cli's current core agentic flow and idiomatic pydantic-ai patterns, with focus on:

- agent creation
- deps injection
- tools and approval flow
- memory and summarization safety
- context/message-history processing

## Scope

In scope:

- `co_cli/agent.py`
- `co_cli/_orchestrate.py`
- `co_cli/_history.py`
- `co_cli/main.py`
- `tests/test_agent.py`
- `tests/test_history.py`
- `tests/test_orchestrate.py`
- design docs that describe prompt/context behavior

Out of scope:

- large redesign of memory storage backend (FTS/vector work stays in TODO docs)
- model/provider strategy changes
- full multi-agent architecture changes

## Confirmed Issues to Fix

Priority P0

1. Per-turn context injection is not actually per-turn.
- Evidence in co-cli: `agent.py:222–264` — all 6 runtime layers (`add_current_date`, `add_shell_guidance`, `add_project_instructions`, `add_personality_memories`, `inject_active_mindset`, `inject_personality_critique`) use `@agent.system_prompt`.
- Evidence in pydantic-ai: `@agent.system_prompt` contributes to `SystemPromptPart` in message history. With non-empty `message_history` the old system prompt parts persist in history and compete with fresh ones. `@agent.instructions` is the idiomatic channel: out-of-band, not part of message history, reevaluated fresh on every `agent.run()`.
- Impact: date/project instructions/personality memory layers can become stale in multi-turn conversations.

Priority P1

2. Mindset classification run is not isolated from tool graph.
- Evidence in co-cli: `_orchestrate.py:466–474` — `run_turn()` invokes `agent.run(output_type=MindsetDeclaration, message_history=[])` on the full tool-enabled agent.
- `agent.override(tools=[], toolsets=[])` is the correct isolation primitive (confirmed in pydantic-ai API docs).
- Impact: unnecessary tool surface and approval/deferred behavior risk in a classification-only step.

Priority P2

3. Private agent internals used for native tool listing and approval inspection.
- Evidence: `agent.py:296` — `list(agent._function_toolset.tools.keys())`.
- Evidence: `tests/test_agent.py:54,81–82,89,98–99` — four tests access `agent._function_toolset` directly to inspect approval flags.
- Impact: brittle coupling to pydantic-ai internals across both production code and tests.

4. History processor config source inconsistency.
- Evidence: `_history.py:136` — `truncate_tool_returns(messages)` imports and reads `co_cli.config.settings` directly.
- Note: `CoDeps.tool_output_trim_chars` already exists (`deps.py:66`) — no deps schema change needed.
- Impact: weaker run-context consistency and testability.

## Detailed Fix Plan

## Workstream B (P0) + Workstream C (P1): Per-Turn Context + Classification Isolation

**These two workstreams must land atomically in a single commit.**

Reason: after B converts the 6 dynamic layers to `@agent.instructions`, those functions fire
during the mindset classification call. In particular `inject_personality_critique` IS populated
at session start (loaded from `souls/{role}/critique.md`), so it will inject into the
classification run until C adds the `agent.override` guard. Any interim state between B and C
landing contaminates the classifier.

### Workstream B (P0): Make Runtime Context Truly Per-Turn

Goal: align runtime context injection with pydantic-ai idioms, ensure reevaluation each run.

Implementation plan:

1. Convert dynamic runtime layers in `co_cli/agent.py` from `@agent.system_prompt` to `@agent.instructions`:
- `add_current_date`
- `add_shell_guidance`
- `add_project_instructions`
- `add_personality_memories`
- `inject_active_mindset`
- `inject_personality_critique`

2. Keep static assembled prompt as-is; do not bundle with a broader prompt architecture rewrite.

3. Update docs wording that currently states these are per-turn `@agent.system_prompt` layers.

4. Add an automated test that asserts the instructions are re-evaluated on turn 2 with non-empty
   `message_history`. Use `FunctionModel` or `TestModel` to capture the `ModelRequest.instructions`
   field at turn 2 and assert it contains fresh context (e.g. current date string). Manual
   multi-turn check alone is not sufficient for the P0 behavioral claim.

Files:

- `co_cli/agent.py`
- `docs/DESIGN-core.md`
- `docs/DESIGN-16-prompt-design.md`
- `docs/DESIGN-02-personality.md`
- `tests/test_agent.py`

Acceptance criteria:

- All 6 dynamic runtime layers use `@agent.instructions`.
- Automated test asserts `ModelRequest.instructions` content is present on turn 2.
- No behavior regressions in existing tool-calling functional tests.

### Workstream C (P1): Isolate Mindset Classification from Tool Graph

Goal: ensure pre-turn mindset classification is a strict classifier call with no tool execution path.

Implementation plan:

1. In `_orchestrate.py`, wrap the classification run in `agent.override`:

```
with agent.override(tools=[], toolsets=[]):
    _mindset_result = await agent.run(
        user_input,
        output_type=MindsetDeclaration,
        message_history=[],
        deps=deps,
        model_settings=model_settings,
    )
```

`agent.override(tools=[], toolsets=[])` replaces both the function toolset and MCP toolsets for
the duration of the context manager. This is a confirmed valid pydantic-ai API.

2. Keep output schema `MindsetDeclaration` and existing `_apply_mindset` behavior unchanged.

3. Keep `message_history=[]` for classification pass.

4. Add regression test: a classification run with `agent.override(tools=[], toolsets=[])` must not
   produce `DeferredToolRequests` output regardless of user input.

Files:

- `co_cli/_orchestrate.py`
- `tests/test_orchestrate.py`

Acceptance criteria:

- Classification pass is tool-free by construction.
- Main response pass (after classification) remains unchanged.
- Regression test passes.

## Workstream D (P2): Remove Private API Dependency for Tool Name Inventory

Goal: avoid reliance on `agent._function_toolset` in both production code and tests.

Implementation plan:

1. In `get_agent()`, maintain an explicit registry while registering tools:

```
tool_registry: list[tuple[str, bool]] = []  # (name, requires_approval)
# after each agent.tool() call:
# tool_registry.append((tool_fn.__name__, requires_approval_flag))
```

2. Derive `tool_names: list[str]` from the registry for the existing third return element.

3. **Add a fourth return element**: `tool_approval: dict[str, bool]` mapping tool name to its
   `requires_approval` flag. Update the function signature accordingly:

```
def get_agent(...) -> tuple[Agent[...], ModelSettings | None, list[str], dict[str, bool]]:
```

4. **Scope: the return type change breaks every existing 3-tuple caller.** Before implementing,
   run `grep -r "get_agent" --include="*.py"` and update every callsite. Approximate scope:
   ~49 callsites across 12+ files including:
   - `co_cli/main.py` (2 callsites: lines 186, 212)
   - `tests/test_agent.py`, `tests/test_mcp.py` (~10), `tests/test_signal_analyzer.py` (~8),
     `tests/test_llm_e2e.py` (~6), `tests/test_history.py` (~4),
     `tests/test_commands.py` (~3), `tests/test_tool_calling_functional.py` (~3),
     `tests/test_doom_loop.py`, `tests/test_memory_decay.py`
   - `scripts/` (~2) and `evals/` (~8)

   Callers that only need names: `agent, model_settings, tool_names, _ = get_agent(...)`
   Callers that need approval map: `agent, model_settings, tool_names, tool_approval = get_agent(...)`

5. Update `tests/test_agent.py`:
   - `test_get_agent_registers_all_tools` — update destructuring to 4-tuple.
   - `test_approval_tools_flagged` — replace `agent._function_toolset.tools.values()` with
     the returned `tool_approval` dict. Approval assertions remain unchanged.
   - `test_web_search_ask_requires_approval`, `test_all_approval_gates_precision_write_tools`,
     `test_web_fetch_ask_requires_approval` — same replacement pattern.

6. Remove the `_function_toolset` access from all test and production files.

Files:

- `co_cli/agent.py`
- `co_cli/main.py`
- `tests/test_agent.py`, `tests/test_mcp.py`, `tests/test_signal_analyzer.py`,
  `tests/test_llm_e2e.py`, `tests/test_history.py`, `tests/test_commands.py`,
  `tests/test_tool_calling_functional.py`, `tests/test_doom_loop.py`, `tests/test_memory_decay.py`
- All `scripts/` and `evals/` files matching the grep

Acceptance criteria:

- No direct reads of `_function_toolset` in any `.py` file in the repo.
- All callers of `get_agent()` updated to 4-tuple destructuring.
- Tool count, names, and approval flags remain stable.

## Workstream E (P2): Context-Aware Tool-Trim Processor

Goal: make history processor config sourcing consistent with deps injection.

Implementation plan:

1. Change `truncate_tool_returns(messages)` signature to `truncate_tool_returns(ctx: RunContext[CoDeps], messages)`.
   This is the same pattern already used by `detect_safety_issues` in the same file (`_history.py:587`).
   pydantic-ai inspects the function signature and passes ctx inline when the processor accepts it —
   no threading change, same synchronous dispatch as `(messages)`-only processors.

2. Read threshold from `ctx.deps.tool_output_trim_chars` (field already exists at `deps.py:66`).

3. Remove the `from co_cli.config import settings` import inside the function body.

4. Update `tests/test_history.py` — all 7 existing `truncate_tool_returns` tests use
   `monkeypatch.setattr("co_cli.config.settings.tool_output_trim_chars", ...)` and call
   `truncate_tool_returns(msgs)` with no ctx. After the change these tests all fail.

   Migration path for each test:
   - First, extend the existing `_real_run_context` helper (`test_history.py:50`) to accept
     a `tool_output_trim_chars` keyword argument (default `2000`), and pass it through to `CoDeps`.
   - For each test: remove the `monkeypatch.setattr(...)` line, call
     `ctx = _real_run_context(FunctionModel(...), tool_output_trim_chars=<threshold_value>)`,
     then call `truncate_tool_returns(ctx, msgs)`.

   This is mechanical across all 7 tests once the helper is extended.

Files:

- `co_cli/_history.py`
- `tests/test_history.py`

Acceptance criteria:

- No direct global settings read in `truncate_tool_returns`.
- All 7 existing truncate tests pass with ctx-based threshold.
- Existing trimming behavior preserved.

## Testing Plan

1. Targeted tests:
   - `tests/test_agent.py` — instruction-layer registration, multi-turn instructions reevaluation
     (automated, via TestModel/FunctionModel), tool inventory and approval flags.
   - `tests/test_orchestrate.py` — classifier isolation: no DeferredToolRequests output.
   - `tests/test_history.py` — all 7 truncate tests updated to ctx-based threshold.

2. Broader regression tests:
   - `tests/test_tool_calling_functional.py`
   - `tests/test_llm_e2e.py` — exercises `get_agent()` with multi-turn runs; must be updated
     if return type changes (4-tuple destructuring).

3. Manual checks:
   - Multi-turn chat: dynamic date/project/personality context remains current after turn 1.
   - Compaction path yields coherent summaries.

## Rollout Sequence

1. Workstreams B + C together (single atomic commit — see B+C note above)
2. Workstream D (P2)
3. Workstream E (P2)
4. Docs sync pass

## Risks and Mitigations

Risk:

- Prompt-behavior shift after moving runtime layers to `@agent.instructions`.

Mitigation:

- Keep static assembled prompt unchanged in first pass.
- Validate with existing functional tool-calling suite and focused multi-turn checks.
- Automated multi-turn instructions test catches regressions before merge.

Risk:

- `get_agent()` return type change breaks callers not listed in files.

Mitigation:

- Before implementing D, grep for all `get_agent` callsites in the repo.
- Update every callsite in the same PR; CI must be green before merge.

Risk:

- Subtle history behavior changes in compaction path.

Mitigation:

- Add behavior-level tests around summarizer request composition.
- Keep fallback marker path unchanged.

## Definition of Done

1. All four workstreams implemented and passing tests.
2. No private API usage for tool-name inventory or approval inspection (`_function_toolset` absent from repo).
3. All callers of `get_agent()` updated to 4-tuple destructuring.
4. DESIGN docs updated to match actual prompt/context semantics.
5. `tests/test_llm_e2e.py` passes with updated get_agent signature.
6. Review sign-off before any broader prompt architecture refactor.
