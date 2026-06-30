# SDK-coupling cleanup — post-cutover refresh: own the surviving pydantic-ai reaches

> **STATUS: REFRESHED post loop-decoupling cutover (2026-06-30).**
> The loop-decoupling milestone (owned loop, v0.8.506 cutover) **landed both of this plan's original actionable items at cutover** and **deleted the source the entire S1–S6 census pointed at**. This refresh discards the obsolete pre-cutover census and re-scopes the plan to the **two SDK reaches that survive in the owned loop**. Per the milestone's "resolve before `0.9.0`" gate, each surviving reach is **reviewed and given a verdict** here — not fixed in this cycle. Acting on the one actionable reach is this plan's own future Gate-1 cycle.
> Tracking plan: `docs/exec-plans/completed/2026-06-24-234633-loop-decoupling-milestone.md` (OQ-5); the pydantic-ai v2 migration consumes this refreshed census as its coupling-inventory baseline.

## Context

The pre-cutover census in this plan (six couplings S1–S6, all sited in the graph-era loop) is **fully obsolete**. The owned-loop cutover deleted the modules every row pointed at, and shipped both items this plan was going to act on:

### Pre-cutover census — obsolete (what happened to each row)

| # | Original coupling | Original site (now gone) | Disposition at cutover |
|---|----------|---------|-------|
| S1 | JSON-repair proxy depends on the graph validating `StreamedResponse.get()` | the deleted `llm/surrogate_recovery_model.py` | **Obsolete** — the surrogate-recovery model was deleted; JSON repair is now `RepairingStreamedResponse` (`co_cli/llm/_json_repair.py:132`), driven from `model_turn` (`co_cli/llm/model_turn.py:66`). The graph-validation dependency is gone with the graph. |
| S2 | Diffuse tool-cap state machine (3 loose `deps.runtime` fields, 2 modules) | the deleted `agent/orchestrate.py` + `agent/toolset.py` call seam | **LANDED at cutover** — exactly this plan's old TASK-1. The three fields are now one cohesive `ToolCapState` (`co_cli/agent/turn_state.py:60`, `note_calls()` / streak / latched `hard_stop`), reset at the step boundary in the owned loop. Nothing left to do. |
| S3 | Synthetic `RunContext(model=None)` for `prepare_tool_def` | `bootstrap/schema_budget.py:62` | **Survives unchanged** — still forced, still isolated, still documented. Not loop-coupled; outside this milestone's blast radius. Folds into reach **R-B** below as the same forced synthetic-`RunContext` class. |
| S4 | Reasoning-overflow detected by raw error-message substring | the deleted `agent/orchestrate.py` | **Obsolete as sited** — the orchestrate module is deleted. Provider-error classification now lives in the owned loop (`classify_provider_error`); whether a substring match survives there is a question for the **v2 migration** census, re-grounded against the owned source, not this plan. |
| S5 | MCP schema-shape: relies on the SDK copying raw `inputSchema` verbatim | `agent/mcp.py:19-56` | **Survives unchanged** — `_SequentialMCPToolset(WrapperToolset)` (`co_cli/agent/mcp.py:19`) is intact and still the legitimate MCP base. Forced + pinned + documented; no-action, unchanged by the cutover. |
| S6 | Compaction core takes `RunContext[CoDeps]` but reads only `ctx.deps` | `context/compaction.py` + 2 fabrication sites | **LANDED at cutover** — exactly this plan's old TASK-2. `compact_messages` / `commit_compaction` / `recover_overflow_history` now take `deps: CoDeps` (`co_cli/context/compaction.py:321,375,463`); both synthetic-`RunContext` fabrications are gone. Nothing left to do. |

Because S2 and S6 (the only two actionable rows) **shipped at cutover** and S1/S4 are sited in deleted modules, this plan's original TASK-1 and TASK-2 are **retired as landed** — neither describes pending work, and both task bodies cited a now-deleted module. The plan is **not** archived-as-subsumed: it is refreshed to own the coupling that the owned loop introduced.

### Post-cutover census — the two surviving reaches (source-grounded, pydantic-ai 1.92.0)

A repo-wide grep of the owned-loop source for reaches into pydantic-ai internals / synthetic SDK types finds exactly two:

| # | Reach | Site | Class | Verdict |
|---|-------|------|-------|---------|
| R-A | `_output.OutputToolset.build` — builds the subagent `final_result` output tool defs + validation processor by reaching into the private (non-re-exported) `pydantic_ai._output` module | `co_cli/agent/preflight.py:258` (in `build_output_toolset`, `:244`) | private-internal | **actionable → future task** |
| R-B | Synthetic `RunContext[CoDeps]` fabricated to satisfy the SDK toolset get/call API (the owned loop owns the loop, so it constructs the context the graph used to supply) | `co_cli/agent/dispatch.py:51` (`make_run_context`, builds `RunContext` at `:71`); same forced class as the bootstrap site `bootstrap/schema_budget.py:62` (old S3) | synthetic-SDK-type | **forced + isolated → no-action** |

**R-A — `_output.OutputToolset.build` (`preflight.py:258`).** `build_output_toolset` emits the `final_result` `ToolDefinition` list (for `ModelRequestParameters.output_tools`) and the `ObjectOutputProcessor` whose `.validate(args)` turns a `final_result` call into the typed `output_type` instance. It does so via `pydantic_ai._output.OutputToolset.build` — a **private module, not re-exported** — and additionally reads the private `toolset._tool_defs` / `toolset.processors`. The function's own docstring already flags this as "co's single documented reach into a pydantic-ai private module" and a "known v2 break point / cleanup item (replace with a hand-built public `ToolDefinition`, verified equivalent)." **Verdict: actionable.** co now owns the loop, so it can hand-build the `final_result` `ToolDefinition` + a small validator and drop the private reach entirely — provided the emitted tool name + JSON schema stay byte-equivalent to what the dream-reviewer model was tuned on (the parity constraint that kept it through Phase 2). This is the one genuine coupling reduction left, and it is **deferred to this plan's own future Gate-1 cycle** (the milestone gate is "reviewed," not "fixed"). The v2 migration must treat this as a hard break point regardless.

**R-B — synthetic `RunContext` in `make_run_context` (`dispatch.py:51`).** The owned loop dispatches tools itself and must hand the SDK toolset's `get_tools`/`call_tool` a `RunContext[CoDeps]`; the graph used to supply one, so the owned loop constructs a minimal `RunContext(deps=…, model=raw_model, usage=RunUsage())` (with a `# type: ignore[arg-type]` on `model`, since `model` may be the raw provider model). co's visibility filter and tools read only `ctx.deps`; the rest of the context is inert. **Verdict: forced + isolated → no-action.** The SDK toolset call API requires a `RunContext` — there is no public seam that accepts bare `deps` — so the fabrication is forced. Unlike the old scattered S6 fabrications (now gone), this is **consolidated into one owned constructor** with a single documented `# type: ignore`, mirrored only by the equally-forced bootstrap site (`schema_budget.py:62`, old S3, where `prepare_tool_def` genuinely requires a `RunContext`). Two forced synthetic-`RunContext` sites, each isolated and documented; nothing to subtract. Re-grounded against installed pydantic-ai 1.92.0: the toolset call signature still takes `RunContext`; the v2 migration should re-check whether v2 offers a deps-only call path.

## Problem & Outcome

**Problem:** after the owned-loop cutover, two pydantic-ai reaches remain in the loop source. One (**R-A**) is a private-module reach that co can now own; one (**R-B**) is a forced synthetic-`RunContext` the SDK toolset API requires. The milestone's "resolve before `0.9.0`" gate requires each to carry a reviewed verdict, not a blind stamp.

**Outcome:** both reaches are inventoried with a source-grounded verdict (above). R-B is recorded as forced-keep + pinned (no-action). R-A is recorded as the single actionable reduction — own a hand-built public `final_result` `ToolDefinition` — and is the scope of this plan's **own future Gate-1 cycle** (TASK-A), not Phase 6. The pydantic-ai v2 migration consumes this two-row census as its coupling-inventory baseline.

**Failure cost:** none functional. R-B works (the loop dispatches tools correctly through the fabricated context); R-A works (subagent `final_result` parses through the private builder). The cost is purely future-facing: R-A is a known v2 break point, so leaving it un-owned defers a break into the migration. This is optional clarity / migration-derisking work, honestly scoped as such.

## Scope

**In (this plan's future cycle):**
- **TASK-A (R-A):** replace the `pydantic_ai._output.OutputToolset.build` reach in `build_output_toolset` with a hand-built public `final_result` `ToolDefinition` + a small validation processor, verified to emit the byte-identical tool name + JSON schema (dream-reviewer parity), pinned by a test. Behavior-preserving.

**Out:**
- **R-B (`make_run_context` synthetic `RunContext`)** — forced by the SDK toolset call API, consolidated to one owned constructor, documented; no-action. The bootstrap twin (`schema_budget.py:62`) is likewise forced + isolated.
- **The retired pre-cutover census (S1, S2, S4, S5, S6)** — S2/S6 landed at cutover; S1/S4 were sited in deleted modules; S5 is unchanged forced-no-action. None describe pending work.
- **The pydantic-ai v2 migration** — separate independent plan; consumes this refreshed census (R-A actionable, R-B + bootstrap forced) as its baseline.

## Behavioral Constraints

- **TASK-A is behavior-preserving.** The hand-built `final_result` `ToolDefinition` must emit the exact tool name + JSON schema the dream-reviewer model was tuned on, and the validator must turn a `final_result` call's args into the same `output_type` instance the private `ObjectOutputProcessor` produced today. Verified by a parity test (schema + a real subagent round-trip), not by structural assertion.

## High-Level Design

`build_output_toolset` (`co_cli/agent/preflight.py:244`) currently returns `(list(toolset._tool_defs), processor)` from `pydantic_ai._output.OutputToolset.build([output_type])`. TASK-A constructs the same pair without the private reach:
- a public `ToolDefinition(name="final_result", parameters_json_schema=<output_type's JSON schema>, …)` matching the schema `OutputToolset.build` emits for a single `output_type`;
- a small validator equivalent to the `ObjectOutputProcessor` (`.validate(args) -> output_type` via the pydantic model), so the owned loop's final-result handling is unchanged.

The parity constraint is the whole risk: the emitted name + schema must be byte-identical to today's, or the tuned dream-reviewer model regresses. The design step is to snapshot today's `(tool_defs, processor)` output and assert the hand-built pair matches before swapping.

## Tasks

### TASK-A — Own the `final_result` output toolset (drop the `pydantic_ai._output` reach, R-A)
- `files:` `co_cli/agent/preflight.py`, plus a parity test under `tests/`.
- `done_when:` `build_output_toolset` no longer imports from `pydantic_ai._output` and no longer reads `toolset._tool_defs` / `toolset.processors`; it returns a hand-built public `ToolDefinition` list + a validator; a parity test asserts the emitted `final_result` tool name + JSON schema are byte-identical to the prior private-builder output and that a real subagent `final_result` round-trip validates to the same `output_type` instance; `scripts/quality-gate.sh full` passes.
- `success_signal:` a dream-reviewer / subagent eval produces the same structured `final_result` output as before the swap.
- `prerequisites:` none (independent; this is this plan's own future Gate-1 cycle).

## Testing

TASK-A's regression net is a **parity test**: snapshot the current `(tool_defs, processor)` from `build_output_toolset`, then assert the hand-built replacement emits the identical `final_result` tool name + JSON schema and validates a `final_result` args payload to the same `output_type` instance. A real subagent round-trip (the dream-reviewer path that consumes `final_result`) is the end-to-end check. No structural test (co's functional-only policy) — the parity assertion + round-trip is the proof of byte-identical behavior. R-B has no task and needs no test (forced, no change).

## Open Questions

None unresolved. The two surviving reaches are inventoried with source-grounded verdicts (R-A actionable, R-B forced-no-action), re-verified against installed pydantic-ai 1.92.0. Whether to act on TASK-A now or fold it into the v2 migration is the decision for this plan's own future Gate-1 cycle — it is not a Phase-6 question.

---

## Decisions

This refresh (2026-06-30) supersedes the pre-cutover C1 approval. The original S1–S6 census and its TASK-1/TASK-2 are retired (S2/S6 landed at cutover; S1/S4 sited in deleted modules). The refreshed scope is the two post-cutover reaches, each with a reviewed verdict per the milestone's "resolve before `0.9.0`" gate.

| Issue | Decision | Rationale | Change |
|-------|----------|-----------|--------|
| Retire old TASK-1 (S2) | landed | `ToolCapState` shipped at cutover (`co_cli/agent/turn_state.py:60`); the old task body cited a now-deleted module. | Removed from scope; recorded in the obsolete-census table. |
| Retire old TASK-2 (S6) | landed | `compact_messages` etc. now take `deps: CoDeps` (`co_cli/context/compaction.py:321`); both synthetic fabrications gone. | Removed from scope; recorded in the obsolete-census table. |
| R-A (`OutputToolset`) | actionable → TASK-A | Private-module reach co can now own post-cutover; the function docstring itself flags it as a v2 break / cleanup item; parity-constrained but eliminable. | New TASK-A (this plan's own future cycle). |
| R-B (`make_run_context`) | forced → no-action | The SDK toolset call API requires a `RunContext`; consolidated to one owned constructor + one documented `# type: ignore`, mirrored by the equally-forced bootstrap twin. | Recorded forced-no-action; out of scope. |
