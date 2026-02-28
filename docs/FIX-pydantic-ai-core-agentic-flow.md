# FIX Plan: Align co-cli Core Agentic Flow with Idiomatic pydantic-ai

Date: 2026-02-28  
Status: Draft for review (A-P0 completed and verified)

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
- `co_cli/tools/personality.py`
- `tests/test_agent.py`
- `tests/test_history.py`
- `tests/test_orchestrate.py`
- design docs that describe prompt/context behavior

Out of scope:

- large redesign of memory storage backend (FTS/vector work stays in TODO docs)
- model/provider strategy changes
- full multi-agent architecture changes

## Current-State Assessment

## Agent Creation API

What is already strong:

- Uses `Agent(..., deps_type=CoDeps, output_type=[str, DeferredToolRequests], history_processors=[...])`.
- Uses `run_stream_events()` in orchestration, which is correct for deferred-tool workflows.
- Uses MCP toolsets via `toolsets=` and async `agent` context lifecycle.

Main gaps:

- Runtime context layers are currently registered with `@agent.system_prompt` default semantics, but intended as per-turn dynamic context.

## Deps API

What is already strong:

- Flat dataclass (`CoDeps`) with scalar config and session state.
- Tools access `ctx.deps` directly.

Main gaps:

- One history processor (`truncate_tool_returns`) reads global settings directly instead of `RunContext[CoDeps]`.

## Tools API

What is already strong:

- Canonical `agent.tool(...)` + `RunContext[CoDeps]`.
- Correct use of `requires_approval=True` for side-effectful tools.
- Deferred approval loop in `_orchestrate.py` is structurally correct.

Main gaps:

- Tool-name discovery currently uses private internal agent API (`agent._function_toolset`).

## Memory + Context API

What is already strong:

- Custom memory tools are integrated as first-class tools.
- History processors are chained in correct order for governance.
- Interrupt safety patching for dangling tool calls is present.

Main gaps:

- Runtime context layers still use `@agent.system_prompt` semantics where per-run reevaluation is intended.

## Completed and Verified

- A-P0 (secure summarization context) is implemented:
  - `summarize_messages()` now uses `instructions=_SUMMARIZER_SYSTEM_PROMPT`.
  - Added regression test that asserts guard text is present in `ModelRequest.instructions` when non-empty `message_history` is used.
  - Verified flow logic is unchanged (same node sequence and run behavior; only instruction payload changes).

## Confirmed Issues to Fix

Priority P0

1. Per-turn context injection is not actually per-turn.
- Evidence in co-cli: runtime layers use bare `@agent.system_prompt` decorators.
- Evidence in pydantic-ai: reevaluation with history requires either `@agent.system_prompt(dynamic=True)` or `@agent.instructions`; docs recommend `instructions` for most use cases.
- Impact: date/project instructions/personality memory layers can become stale or not re-evaluated as intended.

Priority P1

2. Mindset classification run is not isolated from tool graph.
- Evidence in co-cli: `run_turn()` invokes `agent.run(... output_type=MindsetDeclaration, message_history=[])` on full tool-enabled agent.
- Impact: unnecessary tool surface and approval/deferred behavior risk in a classification-only step.

Priority P2

3. Private agent internals used for native tool listing.
- Evidence: `list(agent._function_toolset.tools.keys())`.
- Impact: brittle coupling to internals.

4. History processor config source inconsistency.
- Evidence: `truncate_tool_returns(messages)` reads `co_cli.config.settings` directly.
- Impact: weaker run-context consistency and testability.

## Detailed Fix Plan

## Workstream B (P0): Make Runtime Context Truly Per-Turn

Goal:

- Align runtime context injection with pydantic-ai idioms and ensure reevaluation each run.

Implementation plan:

1. Convert dynamic runtime layers in `co_cli/agent.py` from `@agent.system_prompt` to `@agent.instructions`:
- `add_current_date`
- `add_shell_guidance`
- `add_project_instructions`
- `add_personality_memories`
- `inject_active_mindset`
- `inject_personality_critique`

2. Keep static assembled prompt as-is for first pass; do not bundle this with a broader prompt architecture rewrite.

3. Update docs wording that currently states these are per-turn `@agent.system_prompt` layers.

4. Add tests that validate reevaluation semantics across multi-turn history.

Files:

- `co_cli/agent.py`
- `docs/DESIGN-core.md`
- `docs/DESIGN-16-prompt-design.md`
- `docs/DESIGN-02-personality.md`
- `tests/test_agent.py`

Acceptance criteria:

- Dynamic runtime layers are re-evaluated every run with non-empty `message_history`.
- No behavior regressions in existing tool-calling functional tests.

## Workstream C (P1): Isolate Mindset Classification from Tool Graph

Goal:

- Ensure pre-turn mindset classification is a strict classifier call with no tool execution path.

Implementation plan:

1. In `_orchestrate.py`, run mindset classification under temporary override:
- `with agent.override(tools=[], toolsets=[]): ...`

2. Keep output schema `MindsetDeclaration` and existing `_apply_mindset` behavior.

3. Keep `message_history=[]` for classification pass.

4. Add regression test:
- classification run cannot produce deferred tool requests.

Files:

- `co_cli/_orchestrate.py`
- `tests/test_orchestrate.py`

Acceptance criteria:

- Classification pass is tool-free by construction.
- Main response pass remains unchanged.

## Workstream D (P2): Remove Private API Dependency for Tool Name Inventory

Goal:

- Avoid reliance on `agent._function_toolset`.

Implementation plan:

1. Introduce local explicit registry list in `get_agent()` while registering tools.

2. Return this explicit list as `tool_names`.

3. Keep tests asserting expected inventory; update tests to avoid private field dependency where possible.

Files:

- `co_cli/agent.py`
- `tests/test_agent.py`

Acceptance criteria:

- No direct reads of `_function_toolset` for inventory.
- Tool count and names remain stable.

## Workstream E (P2): Context-Aware Tool-Trim Processor

Goal:

- Make history processor config sourcing consistent with deps injection.

Implementation plan:

1. Change `truncate_tool_returns(messages)` signature to `truncate_tool_returns(ctx, messages)`.

2. Read threshold from `ctx.deps.tool_output_trim_chars`.

3. Keep behavior unchanged otherwise.

4. Update tests accordingly.

Files:

- `co_cli/_history.py`
- `tests/test_history.py`

Acceptance criteria:

- No direct global settings read in this processor.
- Existing trimming behavior preserved.

## Testing Plan

1. Targeted tests:
- `tests/test_agent.py` for instruction-layer registration semantics and tool inventory.
- `tests/test_orchestrate.py` for classifier isolation.

2. Broader regression tests:
- `tests/test_tool_calling_functional.py`
- `tests/test_llm_e2e.py` subset relevant to memory/context if available in current environment.

3. Manual checks:
- multi-turn chat confirms dynamic date/project/personality context remains current after turn 1.
- compaction path still yields coherent summaries.

## Rollout Sequence

1. Workstream B (P0)
2. Workstream C (P1)
3. Workstream D (P2)
4. Workstream E (P2)
5. Docs sync pass

## Risks and Mitigations

Risk:

- Prompt-behavior shift after moving runtime layers to `@agent.instructions`.

Mitigation:

- Keep static assembled prompt unchanged in first pass.
- Validate with existing functional tool-calling suite and focused multi-turn checks.

Risk:

- Subtle history behavior changes in compaction path.

Mitigation:

- Add behavior-level tests around summarizer request composition and keep fallback marker path unchanged.

## Definition of Done

1. P0 and P1 items implemented and passing tests.
2. No private API usage for tool-name inventory.
3. DESIGN docs updated to match actual prompt/context semantics.
4. Review sign-off before any broader prompt architecture refactor.
