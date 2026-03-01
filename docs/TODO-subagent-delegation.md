# TODO: Sub-Agent Delegation & Advisory Outputs (Remaining Work)

This TODO contains only unimplemented work. Implemented architecture and behavior live in `docs/DESIGN-16-prompt-design.md`, with supporting detail in `DESIGN-core.md`, `DESIGN-07-context-governance.md`, `DESIGN-02-personality.md`, and `DESIGN-tools.md`.

**Nature of this work:** Both items are performance add-ons — they improve quality and reliability of existing behaviors but do not gate any other evolution path. The main agent already handles research, analysis, and retrieval without them. No other TODO depends on this work shipping first.

Recommended sequence across active TODOs (items below are in implementation order):
1. `TODO-sqlite-tag-fts-sem-search-for-knowledge.md` — foundational: knowledge recall is structurally incomplete without ranked retrieval; co is otherwise fully functional
2. `TODO-background-execution.md` — long-running task substrate
3. Item 1 — sub-agent delegation (P1, after background infrastructure)
4. Item 2 — confidence-scored advisory outputs (P2, after ranked retrieval baseline)

---

## 1. Sub-Agent Delegation (Deferred)

Priority:
- P1 (high impact, medium complexity)

When to implement:
- After baseline `TODO-background-execution.md` infrastructure is available (so deep/long delegations can evolve toward background execution paths).
- Keep this before voice and other UX overlays.

Best sequence rationale:
- Background task primitives reduce risk that delegated long tasks block interactive flow.

### Design

**Core pattern:** delegation is a tool call on the parent agent. The parent calls
`delegate_research(query=...)`, which internally creates a focused sub-agent, runs
`agent.run()`, and returns a typed `ResearchResult` dict. This keeps delegation
explicitly tool-invoked and traceable in OTel spans (sub-agent `run()` becomes a
child span inside the parent's tool span automatically).

**Approval constraint:** sub-agent tools are restricted to read-only, no-approval
tools only. This satisfies "no bypass of existing approval/safety constraints"
without needing to thread the frontend into the tool: write tools are simply
excluded from the sub-agent's tool set, not bypassed.

**CoDeps sharing:** the sub-agent receives `ctx.deps` directly. Since the
sub-agent's tools are read-only, no write contention. No new deps fields needed
for Phase A.

**Usage / budget:** sub-agent receives its own `UsageLimits(request_limit=N)` cap.
Full parent-budget sharing (sub-agent requests counted against parent's
`turn_limits`) requires adding `turn_usage` to `CoDeps` so the delegation tool can
forward accumulated usage back — deferred to Phase C.

### Implementation

#### Phase A — Research sub-agent

```
co_cli/agents/research.py           new file
  make_research_agent(deps: CoDeps) → Agent[CoDeps, ResearchResult]
    tools: web_search, web_fetch     (read-only, no approval needed)
    output_type: ResearchResult
    system_prompt: research-focused instructions — "search, fetch, synthesize"

ResearchResult(BaseModel):
  summary: str
  sources: list[str]
  confidence: float                  (0.0–1.0)

co_cli/tools/delegation.py          new file
  async def delegate_research(
      ctx: RunContext[CoDeps],
      query: str,
      domains: list[str] | None = None,
      max_requests: int = 10,
  ) -> dict[str, Any]:
    agent = make_research_agent(ctx.deps)
    result = await agent.run(
        query,
        deps=ctx.deps,
        usage_limits=UsageLimits(request_limit=max_requests),
    )
    r: ResearchResult = result.output
    return {
        "display": f"{r.summary}\n\nSources:\n" + "\n".join(f"- {s}" for s in r.sources),
        "summary": r.summary,
        "sources": r.sources,
        "confidence": r.confidence,
    }

co_cli/agent.py
  agent.tool(delegate_research, requires_approval=False)
```

Docstring dims (see DESIGN-tools.md standard):
- D1: delegates a research task to a focused sub-agent (web_search + web_fetch)
- D2: display (summary + sources), summary, sources, confidence fields
- D3e: does NOT perform write operations or memory saves
- D3g: if sub-agent hits request limit, returns partial results with confidence=0.0

#### Phase B — Analysis sub-agent

```
co_cli/agents/analysis.py
  make_analysis_agent(deps: CoDeps) → Agent[CoDeps, AnalysisResult]
    tools: search_knowledge, search_drive_files  (read-only)
    output_type: AnalysisResult
    system_prompt: "compare, evaluate, and synthesize the provided inputs"

AnalysisResult(BaseModel):
  conclusion: str
  evidence: list[str]
  reasoning: str

co_cli/tools/delegation.py
  async def delegate_analysis(
      ctx: RunContext[CoDeps],
      question: str,
      inputs: list[str] | None = None,
      max_requests: int = 10,
  ) -> dict[str, Any]:
```

#### Phase C — Budget sharing + quality gates

```
co_cli/deps.py
  Add: turn_usage: Any = field(default=None, repr=False)
  # delegation tool writes sub-agent usage here; run_turn() reads it back
  # to accumulate into turn_limits

Quality gate in delegation tools:
  if not result.summary or not result.sources:
      # retry once with refined prompt before returning
      result = await agent.run(f"Try again: {query}", ...)
  if still empty → return {"display": "...", "confidence": 0.0, ...}

Re-delegation path in parent agent:
  - Prompt instructs parent: if confidence < 0.4, retry delegation with
    narrower query or different domains before concluding
```

### Acceptance criteria

- [ ] `delegate_research` tool registered on parent, visible in `uv run co status`
- [ ] Research sub-agent runs web_search + web_fetch only — no write tools
- [ ] Delegation path produces a valid `ResearchResult` with non-empty summary and sources
- [ ] OTel traces show sub-agent span nested under parent tool span
- [ ] Sub-agent respects `max_requests` cap — does not exceed parent turn budget by more than `max_requests`
- [ ] Parent can re-delegate when confidence < threshold (prompt-driven, no orchestration change)
- [ ] No approval prompts triggered by sub-agent (read-only tools only)
- [ ] `DESIGN-16-prompt-design.md` Future Extensions → promoted to Current section

### Likely files

- `co_cli/agents/research.py` (new)
- `co_cli/agents/analysis.py` (new)
- `co_cli/tools/delegation.py` (new)
- `co_cli/agent.py` (register delegation tools)
- `co_cli/deps.py` (Phase C: `turn_usage` field)
- `docs/DESIGN-16-prompt-design.md`

---

## 2. Confidence-Scored Advisory Outputs (Deferred)

Priority:
- P2 (quality enhancement, lower urgency than delegation)

When to implement:
- After `TODO-sqlite-tag-fts-sem-search-for-knowledge.md` Phase 1 is complete (ranked retrieval baseline).
- Preferably after Phase 2 hybrid retrieval and Prereq B (articles), so confidence is derived from stronger ranking signals across sources.

Best sequence rationale:
- Confidence metadata is most useful when retrieval/ranking quality has meaningful signal; adding confidence before search quality work risks false precision.

Objective:
- Add confidence metadata for advisory tool outputs when it improves decision quality.

Scope:
- Candidate tools: search/recall style tools where ranking uncertainty matters.
- Confidence must be machine-readable and user-safe (never presented as certainty).

Acceptance criteria:
- Tool return contract updated consistently (`display` remains primary user-facing field).
- Confidence semantics documented per tool (what signal produced the score).
- No confidence field added to deterministic tools where it would be misleading.

Likely files:
- `co_cli/tools/memory.py`
- `co_cli/tools/web.py`
- `docs/DESIGN-tools.md`
- `docs/DESIGN-16-prompt-design.md`
