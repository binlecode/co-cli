# TODO: Replace `thinking` MCP with Built-in `delegate_think` Subagent

**Task type:** `code-feature`

## Context

Co-cli ships three default MCP servers: `github`, `context7`, and `thinking`
(`@modelcontextprotocol/server-sequential-thinking`). The `thinking` server exposes
a `sequentialthinking` tool that provides a stateful multi-call loop for structured
problem decomposition. It was included as a workaround for models that lack native
chain-of-thought.

Co-cli already has three idiomatic delegation tools (`delegate_coder`,
`delegate_research`, `delegate_analysis`) that spawn isolated pydantic-ai subagents
with structured output. The `ROLE_REASONING` role is already defined and configured —
it points to a reasoning/thinking model (Gemini Flash Thinking, Qwen3 thinking
variants, etc.) that does native extended thinking. The MCP's N-call stateful loop
adds npx startup overhead and subprocess complexity with no benefit over a single
`agent.run()` on the reasoning model.

**Workflow hygiene:** No orphaned DELIVERY or stale TODO files found for this scope.
**Doc/source accuracy:** `DESIGN-tools.md` delegation table lists coder/research/analysis
only — no `delegate_think` yet. Accurate for current state.

**Discoverability:** `delegate_think` is discoverable by the parent agent the same way
as all other delegation tools — pydantic-ai exposes the tool docstring as the function
description in the model's tool list. No prompt engineering required; the docstring is
the contract.

## Problem & Outcome

**Problem:** `thinking` MCP is an npx subprocess dependency that duplicates what the
native reasoning model already does. It never fires in practice (models use native
thinking tokens instead) and adds startup latency to every session.

**Outcome:** Remove `"thinking"` from `_DEFAULT_MCP_SERVERS`. Replace with a
`delegate_think` tool using the existing `ROLE_REASONING` model and the same
pydantic-ai subagent pattern as the other delegation tools. MCP server count drops
from 3 to 2 in status. No user-visible capability regression.

## Scope

**In scope:**
- `ThinkingResult` dataclass + `make_thinking_agent()` factory in `_delegation_agents.py`
- `delegate_think()` tool in `delegation.py`
- Registration in `agent.py` gated on `ROLE_REASONING`
- Remove `"thinking"` entry from `_DEFAULT_MCP_SERVERS` in `config.py`
- Add `max_requests < 1` guard to `delegate_coder` (parity fix, one-liner)
- Update `DESIGN-tools.md` delegation table + files table
- Tests mirroring `test_delegate_coder.py` pattern

**Out of scope:**
- Changing `github` or `context7` MCP entries
- Prompt engineering to direct the model to call `delegate_think`
- New role constants — `ROLE_REASONING` is sufficient

## High-Level Design

```
Parent agent
    └─ delegate_think(ctx, problem, max_requests=5)
            │
            ├─ guard: max_requests < 1 → ModelRetry
            ├─ guard: ROLE_REASONING not configured → ModelRetry
            │
            └─ make_thinking_agent(rm)        # no tools, output_type=ThinkingResult
                    └─ agent.run(problem, deps=make_subagent_deps(ctx.deps), ...)
                            │
                            └─ ThinkingResult { plan, steps, conclusion }
```

`make_thinking_agent` has **no tools** — the subagent reasons purely via model
native thinking. This is intentional: the whole point is to let the reasoning model
decompose the problem internally, not through external tool calls.

`ThinkingResult` fields:
- `plan: str` — high-level approach (1–3 sentences)
- `steps: list[str]` — ordered action steps
- `conclusion: str` — synthesized answer or recommendation

Return dict from `delegate_think` — display format mirrors peer tools:
```
f"{plan}\n\nSteps:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)) + f"\n\nConclusion:\n{conclusion}"
```
Plus metadata fields: `plan`, `steps`, `conclusion`.

Guard order and reference peers: `max_requests < 1` check first (same as
`delegate_research` line 86 and `delegate_analysis` line 174), then registry
availability check. `delegate_coder` intentionally omitted the `max_requests` guard
(it was added post-coder) — TASK-2 adds it there for full parity.

## Implementation Plan

### ✓ DONE — TASK-1 — Add `ThinkingResult` + `make_thinking_agent()` to `_delegation_agents.py`
```
files:
  - co_cli/tools/_delegation_agents.py
done_when: |
  ThinkingResult(plan="p", steps=["s1"], conclusion="c") instantiates without error.
  make_thinking_agent(ResolvedModel(model="gemini-2.0-flash", settings=None))
  returns a non-None Agent with no registered function tools
  (len(agent._function_tools) == 0; _function_tools is a pydantic-ai private attr —
  document this in the test docstring).
```

### ✓ DONE — TASK-2 — Add `delegate_think()` to `delegation.py`; fix `delegate_coder` guard
```
files:
  - co_cli/tools/delegation.py
prerequisites: [TASK-1]
done_when: |
  delegate_think with max_requests=0: raises ModelRetry matching "max_requests must be at least 1".
  delegate_think with model_registry=None: raises ModelRetry matching "unavailable".
  Guard order: max_requests check fires before registry check (matches delegate_research
  and delegate_analysis — confirmed peers at lines 86 and 174 of delegation.py).
  delegate_coder now also raises ModelRetry("max_requests must be at least 1") for max_requests=0.
```

### ✓ DONE — TASK-3 — Register `delegate_think` in `agent.py` gated on `ROLE_REASONING`
```
files:
  - co_cli/agent.py
prerequisites: [TASK-2]
done_when: |
  _, tool_names, _ = build_agent(config=CoConfig.from_settings(settings, cwd=Path.cwd()))
  assert "delegate_think" in tool_names  # ROLE_REASONING is configured in real settings
  For absent ROLE_REASONING: construct CoConfig with role_models={} and verify
  "delegate_think" not in tool_names.
```

### ✓ DONE — TASK-4 — Remove `"thinking"` from `_DEFAULT_MCP_SERVERS`
```
files:
  - co_cli/config.py
done_when: |
  _DEFAULT_MCP_SERVERS has exactly 2 keys: "github" and "context7".
  Settings().mcp_servers has no "thinking" key.
```

### ✓ DONE — TASK-5 — Tests in `tests/test_delegate_coder.py` (extend existing file)
```
files:
  - tests/test_delegate_coder.py
prerequisites: [TASK-1, TASK-2]
done_when: |
  No live agent.run() calls added — all tests are guard or model-instantiation only.
  test_thinking_result_model: ThinkingResult instantiates with expected field values.
  test_make_thinking_agent_no_tools: agent._function_tools is empty (docstring notes
    private API); agent is not None.
  test_delegate_think_no_model: raises ModelRetry matching "unavailable" (registry=None).
  test_delegate_think_max_requests_guard: raises ModelRetry matching "max_requests" for 0.
  test_delegate_coder_max_requests_guard: raises ModelRetry matching "max_requests" for 0.
  Happy-path wiring is covered structurally: ThinkingResult shape validated by model test +
    agent factory test confirms agent is constructable with correct output_type — no live
    LLM call needed (eval boundary; live tests go in evals/).
  All tests pass: uv run pytest tests/test_delegate_coder.py -x
```

### ✓ DONE — TASK-6 — Update `DESIGN-tools.md`
```
files:
  - docs/DESIGN-tools.md
prerequisites: [TASK-3, TASK-4]
done_when: |
  Delegation table includes delegate_think row: role=reasoning, tools=none,
  behavior="Structured problem decomposition via native reasoning model".
  Files table _delegation_agents.py entry lists ThinkingResult and make_thinking_agent().
  No reference to "thinking" MCP remains anywhere in the doc.
```

## Testing

All tests are functional, no mocks, no live LLM calls. Extend `test_delegate_coder.py`:

1. **Guard tests** (async): `max_requests=0` → ModelRetry; `model_registry=None` → ModelRetry
2. **Result model test**: `ThinkingResult` field instantiation
3. **Agent factory test**: `make_thinking_agent()` returns non-None agent with zero tools
4. **Coder parity test**: `delegate_coder` with `max_requests=0` → ModelRetry

Happy-path end-to-end validation (live LLM) belongs in `evals/` per project policy.

## Open Questions

None — all answerable by inspection of existing source.

---

# Audit Log

## Cycle C1 — Team Lead
Submitting for Core Dev and PO parallel review.

## Cycle C1 — Core Dev

**Assessment:** revise
**Blocking:** CD-M-1, CD-M-2

**Major issues:**
- **CD-M-1** [TASK-2 / TASK-5]: Guard peer reference ambiguous — `delegate_coder` has no `max_requests` guard; `delegate_research`/`delegate_analysis` are the correct peers.
- **CD-M-2** [TASK-3]: `done_when` not machine-verifiable without specifying tuple extraction from `build_agent()` return value.

**Minor issues:**
- **CD-m-1** [TASK-2]: `delegate_coder` missing `max_requests < 1` guard — pre-existing parity gap.
- **CD-m-2** [TASK-5]: No explicit note that no live `agent.run()` calls are added.
- **CD-m-3** [TASK-1/TASK-2]: `display` format unspecified.
- **CD-m-4** [TASK-5]: `_function_tools` is private API — should be documented in test docstring.

## Cycle C1 — PO

**Assessment:** approve
**Blocking:** none

**Minor issues:**
- **PO-m-1** [TASK-5]: Guard-only tests leave happy-path shape unverified.
- **PO-m-2** [Scope]: No note on how parent agent discovers the tool.

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | adopt    | TASK-2 done_when now explicitly names delegate_research/delegate_analysis as reference peers; "Guard parity" phrase clarified |
| CD-M-2   | adopt    | TASK-3 done_when rewritten with exact tuple destructure pattern |
| CD-m-1   | adopt    | Expand TASK-2 to add max_requests guard to delegate_coder; one-liner, closes parity gap |
| CD-m-2   | adopt    | TASK-5 done_when now explicitly states no live agent.run() calls |
| CD-m-3   | adopt    | Display format spec added to High-Level Design |
| CD-m-4   | adopt    | Test docstring note added to done_when |
| PO-m-1   | modify   | Happy-path wiring covered structurally via factory + model tests; live LLM test belongs in evals/ per project policy — noted in Testing section |
| PO-m-2   | adopt    | Discoverability note added to Context section |

## Final — Team Lead

Plan approved.

> Gate 1 — PO review required before proceeding.
> Review this plan: right problem? correct scope?
> Once approved, run: `/orchestrate-dev thinking-subagent`

---

## Delivery Audit — 2026-03-19

### What Was Scanned

**Source modules:** `co_cli/tools/delegation.py`, `co_cli/tools/_delegation_agents.py`, `co_cli/agent.py`, `co_cli/config.py`
**DESIGN docs checked:** all `docs/DESIGN-*.md`

### Features Delivered

| Feature | Class | Source | Coverage | Severity | Gap |
|---------|-------|--------|----------|----------|-----|
| `delegate_think` | agent tool | `co_cli/agent.py:217` | full | — | Approval class, delegation table row, conditional registration note, files entry — all present in DESIGN-tools.md |
| `ThinkingResult` | model | `co_cli/tools/_delegation_agents.py` | full | — | Listed in DESIGN-tools.md files table and DESIGN-index.md |
| `make_thinking_agent` | factory | `co_cli/tools/_delegation_agents.py` | full | — | Listed in DESIGN-tools.md files table and DESIGN-index.md |
| `"thinking"` MCP removal | config | `co_cli/config.py` | full | — | MCP server table updated in DESIGN-tools.md; count updated in DESIGN-index.md |
| `max_requests` guard on `delegate_coder` | parity fix | `co_cli/tools/delegation.py` | full | — | Covered by existing delegation table row for `delegate_coder` |

**Summary: 0 blocking, 0 minor**

## Verdict

**CLEAN**

All delivered features have full DESIGN doc coverage. No gaps found.
