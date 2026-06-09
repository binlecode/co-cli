# Exec-Plan: pydantic-ai SDK decouple

**Slug:** `pydantic-ai-sdk-decouple`
**Created:** 2026-06-08 01:24:52
**Source:** Originally `docs/reference/RESEARCH-pydantic-ai-sdk-usage.md` (§3 findings, §5 sequence) — re-verified against `v0.8.320`, HEAD `017ce81e`. That survey doc has been **removed**; its still-valuable, non-superseded content (SDK coupling surface, do-not-touch rationale, deferred follow-ups, rejected approaches, coding-practice notes) is folded into this plan — see the **Appendix** below.

## Context

co-cli is a deep structural consumer of `pydantic-ai==1.81.0`. The research survey ranked its SDK-integration smells by refactor leverage. This plan executes the mechanical, low-risk subset (§5 steps 1–5). The intentionally-justified clusters (§6 do-not-touch) and the `drop-capability-api`-owned approval protocol are out of scope.

**Verified current state (file:line):**
- `co_cli/bootstrap/schema_budget.py:23` — `from pydantic_ai._run_context import RunContext` (private module).
- `co_cli/bootstrap/schema_budget.py:24` — `from pydantic_ai.result import RunUsage` (non-canonical home; canonical is `pydantic_ai.usage`).
- `co_cli/bootstrap/schema_budget.py:39–62` — `_unwrap_function_toolset`, a 12×8-deep duck-typing walk over SDK toolset-chain internals (`.tools`/`.toolsets`/`.wrapped`).
- `co_cli/bootstrap/schema_budget.py:82` — synthetic `RunContext(deps=…, model=None, usage=RunUsage(), tool_name=name)` (genuinely required: `ToolInfo` carries no schema; `prepare_tool_def(ctx)` is the only source).
- `co_cli/bootstrap/core.py:358` — `native_toolset, tool_catalog = build_native_toolset(config)` — the inner `FunctionToolset` is **already in scope here**.
- `co_cli/bootstrap/core.py:380` — `assemble_routing_toolset(native_toolset, [...])` wraps it (`CombinedToolset → .filtered → _CallSeamToolset`, agent/core.py:53–67).
- `co_cli/bootstrap/core.py:417–426` — `CoDeps(...)` built; `:450` — `measure_always_schema_budget(deps)` re-discovers the toolset the same function already had at `:358`.
- `co_cli/agent/build.py:15,40` — `build_orchestrator(...) -> Agent[CoDeps, Any]` with `output_type=[str, DeferredToolRequests]`.
- `co_cli/context/orchestrate.py:90` — `type SessionAgent = Agent[CoDeps, str | DeferredToolRequests]` (alias already exists).
- `co_cli/context/orchestrate.py:387` — `usage_limits=UsageLimits(request_limit=None)` (orchestrator deliberately unbounded).
- `co_cli/agent/run.py:64` — real limit for task agents (keep).
- `co_cli/llm/surrogate_recovery_model.py:142–168` — `_RepairingStreamedResponse`; `get()` (159–162) repairs the assembled response; correctness pinned to the private `_agent_graph._streaming_handler` via docstring (145–149).

**Test landscape (verified):**
- `tests/test_orchestrator_schema_budget.py` — existing regression guard pinning the ALWAYS-schema bucket size (the docstring-named consumer of `measure_always_schema_budget`).
- `tests/test_flow_tool_call_repair.py` — covers `_repair_json_args`/`_repair_response` (unit) and `SurrogateRecoveryModel.request` **non-stream** gated repair.
- `tests/test_surrogate_recovery_model.py` — covers stream **unicode** recovery, but **no test exercises JSON repair through `_RepairingStreamedResponse.get()`** — the §3.3 gap is real and uncovered.

**Two refinements the survey understated:**
- §3.2 is **not repo-wide.** The only non-canonical `RunContext`/`RunUsage` imports in the entire tree are the two lines `schema_budget.py:23–24`. Every other site already imports `from pydantic_ai import RunContext` / `from pydantic_ai.usage import RunUsage`. §3.2 therefore **fully collapses into TASK-1** — no separate sweep, zero other files.
- §3.3's regression is **deterministic** (a fake malformed-args stream). It belongs in `tests/`, not `evals/` (per project policy evals are real-data UAT smoke runs with no fakes).

## Problem & Outcome

**Problem:** `schema_budget.py` is the single most version-fragile module against the SDK — it reaches into a private module and duck-types the SDK's toolset-composition topology. Two cosmetic smells (orchestrator `Any` type, `UsageLimits(None)` ceremony) and one untested correctness assumption (streaming JSON repair) ride alongside.

**Failure cost (what silently breaks without this fix):**
- If a future `pydantic-ai` upgrade renames/moves `pydantic_ai._run_context` or `pydantic_ai.result.RunUsage`, bootstrap **fails to import** — hard crash at startup.
- If the upgrade changes toolset composition internals (`.tools`/`.toolsets`/`.wrapped` shape), `_unwrap_function_toolset` **silently returns the wrong toolset or `None`** → either a `RuntimeError` at bootstrap (`schema_budget.py:75`) or a wrong `static_floor_tokens`, which miscalibrates every compaction trigger for the whole session — a silent correctness regression.
- If the SDK moves where it validates streamed tool args, `_RepairingStreamedResponse` is silently bypassed and **every malformed-JSON tool call on the Ollama streaming path crashes the session**, with no test to catch the regression.

**Outcome:** co's only private-module SDK dependency and only topology heuristic are deleted; the streaming-repair assumption is pinned by a test; two cosmetic items cleaned. Measured schema-budget number is unchanged (the existing regression guard proves it).

## Scope

**In scope:**
- `co_cli/bootstrap/schema_budget.py` — import fixes + delete `_unwrap_function_toolset` + read the threaded toolset.
- `co_cli/bootstrap/core.py` — thread `native_toolset` into the measurer.
- `co_cli/agent/build.py` — `build_orchestrator` return type.
- `co_cli/context/orchestrate.py` — `UsageLimits` comment/cleanup.
- `tests/test_flow_tool_call_repair.py` + `tests/test_orchestrator_schema_budget.py` — streaming-path repair regression; schema-budget caller update.

**Out of scope (do NOT touch — with rationale, so these are not re-opened later):**
- **Approval-protocol types** (`DeferredToolRequests`/`Results`, `ApprovalRequired`, `ToolApproved`/`Denied`) — owned by the `drop-capability-api` plan. These are thin data carriers; co already drives the entire approval loop in `context/orchestrate.py` — the SDK contributes only the pause/resume plumbing and the dict-keyed result shape, not the policy. Whether co re-implements pause/resume around its own `call_tool` is a design decision for that plan, not a mechanical cleanup. `ToolApproved()` has exactly one call site (the clarify tool) — preserve that case if the protocol is ever reworked.
- **The two `WrapperToolset` subclasses** (`_CallSeamToolset`, `_SequentialMCPToolset`) — textbook usage; the minimum for two distinct boundaries (call-time vs list-time). Do **not** fold `_SequentialMCPToolset` into `_SanitizingMCPServer.list_tools()`: that operates on raw MCP-protocol `Tool` objects (`t.inputSchema`), whereas `sequential` lives on the pydantic-ai `ToolDefinition` produced *later* by the SDK's MCP toolset — different objects at different layers, cannot be merged.
- **`SurrogateRecoveryModel` behavior, `ModelMessagesTypeAdapter`, `_rewrite_tool_returns`** — the cleanest seams co has. `SurrogateRecoveryModel` isolates the two ugliest Ollama realities (lone surrogates, malformed tool-arg JSON) in one `WrapperModel` instead of polluting the agent loop; JSON repair lives entirely there, independent of any capability SDK. (TASK-2 only *adds a test* around the streaming path — it changes no behavior.)
- **`build_task_agent` return type** stays `Agent[CoDeps, Any]` — its `output_type` is genuinely variable per spec (only the orchestrator's is fixed).
- **The synthetic `RunContext(model=None)` construction** + its `# type: ignore[arg-type]` — required, stays. See the Appendix "Rejected approaches" for why measuring from co's own metadata instead is not viable. TASK-1 removes only the *private import path*, not the construction.

## Behavioral Constraints

- **Zero behavior change** for TASK-1/3: the measured schema-budget number and the orchestrator's runtime output union must be identical before/after. TASK-1 and TASK-3 are pure refactors (`success_signal: N/A`).
- **Zero-backward-compat** (per project rule): no aliases, no compat shims for the moved imports — change the import line outright.
- **Surgical:** touch only the lines each task requires. No adjacent cleanup.

## High-Level Design

**TASK-1 design decision — B1 (parameter) over B2 (deps field), DECIDED:**
`measure_always_schema_budget` gains a second parameter: the inner `FunctionToolset`. `bootstrap/core.py:450` passes the `native_toolset` it already holds from `:358`. The measurer reads `native_toolset.tools` directly and still reads `deps.tool_catalog` for visibility. `_unwrap_function_toolset` is deleted.

Rationale for B1 over B2 (add a `native_toolset` field to `CoDeps`): the toolset reference is a **build-time measurement input used exactly once**, not durable runtime state. B2 would plant an SDK-typed (`FunctionToolset[CoDeps]`) field on `CoDeps` that lives for the whole session and invites future misuse; B1 keeps the SDK type out of the deps surface and scopes the reference to the one call that needs it. Narrowest surface wins.

Import fixes fold in: `pydantic_ai._run_context` → `pydantic_ai` (line 23); `pydantic_ai.result` → `pydantic_ai.usage` (line 24). These are the whole of §3.2.

**TASK-2 design:** add a test to `tests/test_flow_tool_call_repair.py` that wraps a fake `StreamedResponse` whose `.get()` returns a `ModelResponse` with a malformed-JSON `ToolCallPart.args`, drives it through `SurrogateRecoveryModel.request_stream(...)` with `repair_tool_args=True`, and asserts the yielded stream's `.get()` returns repaired (valid-JSON) args — and that with `repair_tool_args=False` the args pass through verbatim. The streaming fake follows the **`StreamedResponse`-subclass style in `tests/test_surrogate_recovery_model.py`** (`_FakeStream`/`_FakeModel`, overriding `get()`), **not** the `FunctionModel` pattern used for the non-stream cases in `test_flow_tool_call_repair.py`.

**TASK-3 design:** `build_orchestrator` returns `SessionAgent`. Import the existing alias from `co_cli/context/orchestrate.py` **under `if TYPE_CHECKING:`** — `build.py` uses `from __future__ import annotations`, so the return type is a string and an annotation-only import keeps the new `build.py → orchestrate.py` edge immune to future import cycles (no cycle today, but orchestrate.py drags a heavy graph). Annotate the local `agent:` binding to match. Add a one-line comment at `orchestrate.py:387` stating the orchestrator is intentionally unbounded (human drives turn count, unlike task agents at `run.py:64`); drop the explicit `UsageLimits(request_limit=None)` only if verified to be the SDK default for `run_stream_events` — otherwise keep it with the comment. The sibling `metadata={... "request_limit": None}` field (`orchestrate.py:392`) is **independent span/observability metadata, not the limit mechanism** — it stays unchanged regardless of the `UsageLimits` decision (avoid an inconsistent half-edit).

> Note: the quality gate is ruff-only (no mypy/pyright), so "lint clean" does not type-check the annotation; correctness of `-> SessionAgent` is by inspection. This is acceptable — the change is a pure annotation with no runtime effect.

## Tasks

### TASK-1 — De-couple `schema_budget.py` from SDK internals — ✓ DONE
- **files:** `co_cli/bootstrap/schema_budget.py`, `co_cli/bootstrap/core.py`, `tests/test_orchestrator_schema_budget.py`
- **done_when:** `_unwrap_function_toolset` is deleted; no `pydantic_ai._run_context` or `pydantic_ai.result` import remains anywhere in `co_cli/` (grep returns zero); `measure_always_schema_budget` takes the inner `FunctionToolset` as a parameter fed from `bootstrap/core.py:450` (the `native_toolset` already in scope at `:358`); the two callers in `tests/test_orchestrator_schema_budget.py` (currently 1-arg, ~lines 51 and 84) are updated to pass a freshly built toolset via `build_native_toolset(deps.config)[0]` (same registry → same measurement); and `uv run pytest tests/test_orchestrator_schema_budget.py` passes pinning the **same** measured bucket value as before (proves zero behavior change through the real measurement path).
- **success_signal:** N/A (pure refactor).
- **prerequisites:** none.

### TASK-2 — Streaming-path JSON-repair regression test — ✓ DONE
- **files:** `tests/test_flow_tool_call_repair.py`
- **done_when:** a new test drives a malformed-JSON `ToolCallPart.args` through `SurrogateRecoveryModel.request_stream(..., repair_tool_args=True)` and asserts the yielded stream's `.get()` returns valid-JSON args; a paired assertion confirms `repair_tool_args=False` leaves args verbatim; `uv run pytest tests/test_flow_tool_call_repair.py` passes.
- **success_signal:** A future SDK change that bypasses `_RepairingStreamedResponse` on the streaming path turns this test red instead of silently breaking Ollama sessions.
- **prerequisites:** none.

### TASK-3 — Orchestrator output type + `UsageLimits` ceremony — ✓ DONE
- **files:** `co_cli/agent/build.py`, `co_cli/context/orchestrate.py`
- **done_when:** `build_orchestrator` is annotated `-> SessionAgent` (using the existing alias); `orchestrate.py:387` either drops `UsageLimits(request_limit=None)` (only if confirmed SDK default) or carries a one-line comment explaining the orchestrator is intentionally unbounded; `scripts/quality-gate.sh lint` is clean and the existing orchestrator/build tests pass.
- **success_signal:** N/A (pure refactor / cosmetic).
- **prerequisites:** none.

> **Cut: former TASK-4 (thin `_parts.py`).** A new module holding shared type-set constants + predicates is a util/helpers module by another name (`feedback_no_util_modules.md`), and the ~4 sites are heterogeneous (serialize.py is `part_kind` label fallback; _compaction_boundaries.py is `isinstance` boundary predicates) — not the same logic copy-pasted. Not worth minting a module for a 🟡-low-med, modest-payoff dedup inside an otherwise-mechanical plan. If ever wanted, raise as a standalone follow-up where the new-module-vs-no-util tradeoff is the explicit decision.

## Testing

- **TASK-1:** `uv run pytest tests/test_orchestrator_schema_budget.py` — the existing guard exercises the real bootstrap measurement; an unchanged bucket value proves the refactor preserved behavior. (Full suite runs at ship.)
- **TASK-2:** `uv run pytest tests/test_flow_tool_call_repair.py` — new streaming-repair case + existing non-stream cases.
- **TASK-3:** `scripts/quality-gate.sh lint` + existing build/orchestrate tests.
- All pytest runs pipe to a timestamped `.pytest-logs/` file per project policy; run with `-x` (fail fast) and tail the log for LLM-call timing (TASK-2 uses a fake model — no live LLM).

## Open Questions

1. **TASK-3 `UsageLimits` default:** does omitting `usage_limits=` from `run_stream_events` behave identically to `UsageLimits(request_limit=None)` in pydantic-ai 1.81.0? If yes → drop the arg; if no/unsure → keep it with the explanatory comment. Resolve by reading the SDK signature/default at dev time. (Low-risk either way — the comment path is always safe.)

> Resolved: former Q2 (TASK-4 inclusion) — **cut to a follow-up** (see the cut note under High-Level Design).

## Appendix — pydantic-ai SDK survey (merged from the removed RESEARCH doc)

> **Superseded by the runtime spec.** The durable design + coupling content below now lives in
> [`docs/specs/pydantic-ai-integration.md`](../../specs/pydantic-ai-integration.md) (§7 SDK Coupling Boundaries).
> This Appendix is retained only as the frozen build-time record; the spec is the living source of truth.

Carried over from `docs/reference/RESEARCH-pydantic-ai-sdk-usage.md` (deleted once this was merged). This is reference/background that outlives the 3 tasks above — not actionable plan scope. Verified against `pydantic-ai==1.81.0`, `v0.8.320`, HEAD `017ce81e`.

### A. co's pydantic-ai coupling surface

co is a deep structural consumer across five layers:
1. **Agent + run lifecycle** — `Agent`, `AgentRunResult`, `AgentRunResultEvent`, `run_stream_events`/`run`, `.output`/`.usage()`/`.new_messages()`/`.all_messages()`, `RunUsage`, `UsageLimits`. co does **not** hand-reconstruct messages from the event stream — it lets the agent build `ModelRequest`/`ModelResponse` and pulls them from the result (correct boundary).
2. **The message/part type system** (`pydantic_ai.messages`) — ~20 types pattern-matched / hand-built / serialized / rewritten across `context/` (compaction, history, summarization, boundaries) + `observability/serialize.py`. Breadth is intrinsic to client-side compaction (`feedback_context_management_self_contained`), not a smell.
3. **Toolset composition** — `FunctionToolset`, `CombinedToolset`, `WrapperToolset` (the two subclasses above), `.filtered()`, `ToolDefinition` patching. co's deferral is its own (`_tool_visibility_filter` on `tool_catalog` + `runtime.unlocked_tools`); the SDK's `defer_loading`/`search_tools` is deliberately unused.
4. **Deferred-tool / approval protocol** — see Out-of-scope; owned by `drop-capability-api`.
5. **Model wrapping** — `SurrogateRecoveryModel(WrapperModel)` overriding `request`/`request_stream`; provider/model factories (`OpenAIChatModel`+`OllamaProvider`, `GoogleModel`+`GoogleProvider`); `ModelSettings`/`GoogleModelSettings`.

### B. Coding-practice notes (preserve / watch)

**Strong practices to preserve under any future refactor:**
- Pure, non-mutating history processors — every processor returns a new list; `_rewrite_tool_returns` rebuilds a `ModelRequest` via `replace(...)` only when a part changed, else preserves the original object by identity.
- Single rewrite contract — dedup/evict/spill/strip all funnel through `_rewrite_tool_returns` with a `replacement_for` callback; the boundary-protected non-mutating invariant holds by construction.
- Frozen metadata — `ToolInfo`/`AlwaysSchemaBudget`/`MCPToolsetEntry` are frozen dataclasses set once at registration.
- Thin proxies over deep subclassing — `_SanitizingMCPServer`, `_RepairingStreamedResponse` delegate via `__getattr__` and override exactly one method.

**Latent risks NOT addressed by this plan (recorded for a future pass):**
- **`id(part)` identity tracking across processor passes** (`history_processors.py` ~225, 311, 366, 491) — `_build_keep_ids`/spill key replacements by `id(part)`, assuming pydantic-ai passes the *same* part objects to each processor within a request. True today; if the SDK ever deep-copies messages between processors this breaks silently. Worth a one-line invariant comment at each `id(part)` use.
- **String-based part fallbacks** — `getattr(msg, "kind", "request")` / `getattr(part, "part_kind", part.__class__.__name__)` (serialize.py) duck-type instead of `isinstance`; defensive but signals the part taxonomy isn't fully trusted.

### C. Deferred follow-ups (not in this plan)

- **Thin `co_cli/context/_parts.py`** (former TASK-4, cut) — would centralize the ~4 part-type-set / `part_kind` sites (serialize.py:43,68; _compaction_boundaries.py:70,110). Cut because the sites are heterogeneous (label fallback vs boundary predicate) and a shared-constants module tensions against `feedback_no_util_modules.md` for a 🟡-low-med payoff. Raise as a standalone follow-up only if the predicates genuinely converge; the new-module-vs-no-util tradeoff must be the explicit decision then.
- **Streaming-repair private-internal dependency** (§3.3, documented not refactored) — `_RepairingStreamedResponse.get()` repairs the assembled response because the agent graph validates/dispatches streamed tool args from `StreamedResponse.get()` (`_agent_graph.py` `_streaming_handler`), a **private** internal. There is no public alternative, so the seam stays as-is; TASK-2's regression test is what pins the assumption — if the SDK moves stream validation, the test goes red instead of Ollama silently breaking.

### D. Rejected approaches (do not re-litigate)

- **"Measure the ALWAYS schema bucket from co's own `ToolInfo` metadata and delete the synthetic `RunContext`."** Not viable — `ToolInfo` carries no `parameters_json_schema`; the schema is SDK-generated from the function signature and obtainable only via `tool.prepare_tool_def(ctx)` (which also honors per-turn `prepare` callbacks). So a synthetic `RunContext` is genuinely required; TASK-1 removes only its *private import path*, keeping the construction.
- **"Fold `_SequentialMCPToolset` into `_SanitizingMCPServer.list_tools()`."** Architecturally unsound — different object layers (raw MCP `Tool.inputSchema` vs pydantic-ai `ToolDefinition.sequential`); see Out-of-scope.

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev pydantic-ai-sdk-decouple`

## Implementation Review — 2026-06-08

Delivered directly (TL-as-Dev), bypassing `/orchestrate-dev`; reviewed here with three parallel cold-read evidence subagents + full-suite RCA + live bootstrap verification.

### Evidence
| Task | done_when | Spec Fidelity | Key Evidence |
|------|-----------|---------------|-------------|
| TASK-1 | `_unwrap_function_toolset` deleted; no `pydantic_ai._run_context`/`pydantic_ai.result` import; measurer takes inner `FunctionToolset`; tests pass same bucket | ✓ pass | `schema_budget.py:43-44` new param `native_toolset: FunctionToolset[CoDeps]`; `:61` iterates `native_toolset.tools`; `core.py:450` passes `native_toolset` from `:358`; grep for walker + private imports = zero; `test_orchestrator_schema_budget.py` green, bucket pinned **17,224** (unchanged) |
| TASK-2 | malformed args through `request_stream(repair_tool_args=True)` repaired; `False` verbatim; tests pass | ✓ pass | `test_flow_tool_call_repair.py:215-232` two new tests; non-tautological — `json.loads('{"cmd": "ls",')` raises, so bypassing `_RepairingStreamedResponse` (surrogate_recovery_model.py:159-162,270) turns it red; SDK-subclass fakes, no mocks |
| TASK-3 | `build_orchestrator -> SessionAgent`; `UsageLimits` kept-with-comment or dropped-if-default | ✓ pass | `build.py:16` return `SessionAgent`, `:13` alias under `TYPE_CHECKING`, `:41` binding matches; `orchestrate.py:391` `request_limit=None` KEPT (SDK default = 50, confirmed) + comment `:387-390`; sibling metadata `:396` untouched |

### Issues Found & Fixed
| Finding | File:Line | Severity | Resolution |
|---------|-----------|----------|------------|
| Module docstring still said "from the assembled toolset" after the measurer stopped walking | schema_budget.py:5 | minor | Updated to "from the native toolset" before review |
| `build_native_toolset` declares return `AbstractToolset[CoDeps]` while measurer param is `FunctionToolset[CoDeps]` (static widening) | core.py:20 vs schema_budget.py:44 | minor | Not fixed — runtime object is genuinely `FunctionToolset` (`_build_native_toolset` toolset.py:103); no type checker in the quality gate; narrowing `build_native_toolset` is out of declared scope for zero functional gain |

No blocking findings.

### Tests
- Command: `uv run pytest -x -q`
- Result: **627 passed, 0 failed** (1 warning)
- Log: `.pytest-logs/20260608-*-review-impl.log`

### Behavioral Verification
- `uv run co chat` (EOF boot): ✓ bootstrap healthy — banner renders, `Tools: 38`, model + orchestrator built, exit 0. Confirms `measure_always_schema_budget(deps, native_toolset)` and `build_orchestrator -> SessionAgent` run end-to-end. `(degraded)` flag is the pre-existing TEI-reranker fallback, unrelated.
- `success_signal`: TASK-1/TASK-3 N/A (pure refactor). TASK-2 verified — the regression test is non-tautological, so a future SDK change bypassing the streaming repair turns it red.

### Overall: PASS
