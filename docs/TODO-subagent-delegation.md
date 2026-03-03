# TODO: Sub-Agent Delegation

**Goal**: Let the main agent delegate focused tasks (research, analysis) to specialized sub-agents that run with restricted tool sets and return typed results.

**Problem**: The main agent handles all work in a single context. Deep research or multi-step analysis tasks consume turns, pollute history, and are slower than a focused sub-agent with the right tool subset.

**Non-goals**: Parallel multi-agent execution, sub-agent memory persistence, UI for sub-agent traces.

**Prerequisite**: `TODO-background-execution.md` should ship first — background task infrastructure reduces the risk that long delegations block the interactive flow, and long-running delegations may evolve to use the background execution path.

---

## Pattern

Delegation is a tool call on the parent agent. The parent calls `delegate_research(query=...)`, which internally creates a focused sub-agent, runs `agent.run()`, and returns a typed result dict. This keeps delegation explicitly tool-invoked and traceable — pydantic-ai automatically nests the sub-agent's `run()` span inside the parent's tool span in OTel.

Sub-agent tools are restricted to read-only, no-approval tools only. Write tools are excluded from the sub-agent's tool set, not bypassed. Tool sets are fixed per agent type and cannot be extended at runtime — if additional read-only tools are needed, create a new sub-agent type. Tool failures within sub-agents follow pydantic-ai's standard retry logic. No new approval wiring needed.

Sub-agents receive `ctx.deps` directly. Since tools are read-only, no write contention.

---

## Phase A — Research Sub-Agent

**New files:**

`co_cli/agents/research.py`
- `make_research_agent(deps: CoDeps) → Agent[CoDeps, ResearchResult]`
- Tools: `web_search`, `web_fetch` (read-only, no approval needed)
- Output type: `ResearchResult`
- System prompt: research-focused — "search, fetch, synthesize; return a grounded summary with sources"

`ResearchResult` (Pydantic model):
- `summary: str`
- `sources: list[str]`
- `confidence: float` (0.0–1.0) — scoring: `0.0` if summary or sources empty after retry; LLM-assessed if populated

`co_cli/tools/delegation.py`
- `delegate_research(ctx, query, domains?, max_requests?)` → `dict[str, Any]`
- Creates sub-agent via `make_research_agent(ctx.deps)`
- Runs `agent.run(query, deps=ctx.deps, usage_limits=UsageLimits(request_limit=max_requests))`
- Returns `dict` with `display`, `summary`, `sources`, `confidence` fields

`co_cli/agent.py`
- Register: `agent.tool(delegate_research, requires_approval=False)`

**Tool docstring contract** (per DESIGN-tools.md):
- What it does: delegates a research task to a focused sub-agent (web_search + web_fetch only)
- Returns: `display` (summary + sources), `summary`, `sources`, `confidence`
- Does NOT perform write operations or memory saves
- If sub-agent hits request limit, returns partial results with `confidence=0.0`

**Acceptance criteria:**
- [ ] `delegate_research` tool registered on parent agent, visible in `uv run co status`
- [ ] Research sub-agent runs `web_search` + `web_fetch` only — no write tools
- [ ] Returns valid `ResearchResult` with non-empty summary and sources
- [ ] OTel traces show sub-agent span nested under parent tool span (verify: retrieve spans for `delegate_research`, check `parent_span_id` matches the parent tool span)
- [ ] Sub-agent respects `max_requests` cap; `max_requests < 1` raises `ModelRetry("max_requests must be at least 1")`
- [ ] No approval prompts triggered by sub-agent (read-only tools only)

---

## Phase B — Analysis Sub-Agent

`co_cli/agents/analysis.py`
- `make_analysis_agent(deps: CoDeps) → Agent[CoDeps, AnalysisResult]`
- Tools: `search_knowledge`, `search_drive_files` (read-only)
- Output type: `AnalysisResult`
- System prompt: "compare, evaluate, and synthesize the provided inputs"

`AnalysisResult` (Pydantic model):
- `conclusion: str`
- `evidence: list[str]`
- `reasoning: str`

`co_cli/tools/delegation.py` (extend)
- `delegate_analysis(ctx, question, inputs?, max_requests?)` → `dict[str, Any]`

**Acceptance criteria:**
- [ ] `delegate_analysis` registered, visible in `uv run co status`
- [ ] Analysis sub-agent runs only `search_knowledge` + `search_drive_files`
- [ ] Returns valid `AnalysisResult` with non-empty conclusion and evidence

---

## Phase C — Budget Sharing + Quality Gates

`co_cli/deps.py`
- Add: `turn_usage: Any = field(default=None, repr=False)`
- After `sub_agent.run()` completes, the delegation tool reads `sub_agent_result.usage` and calls `accumulate_usage(ctx.deps.turn_usage, sub_agent_result.usage)` — a helper that merges request counts. `run_turn()` sums all accumulated usage after the turn completes.
- Sub-agent `request_limit` is set to `max_requests` (a fixed cap per delegation, not drawn from remaining parent budget). If the sub-agent exhausts its cap, it returns partial results with `confidence=0.0`; the parent sees this and avoids re-delegating infinitely.

Quality gate in delegation tools:
- If result summary or sources are empty: retry once (max_retries=1) with a refined prompt: `"The previous search returned no results. Try with different keywords: {original_query} (alternative framing). If still no results, return confidence=0.0."`
- If still empty after one retry: return `{confidence: 0.0, summary: "No results found despite multiple searches.", sources: []}`
- Each retry uses at most 1 additional request from the sub-agent's cap

Re-delegation guidance in parent prompt:
- If `confidence < 0.4`, parent should retry delegation with a narrower query or different domains before concluding

**Acceptance criteria:**
- [ ] Sub-agent usage propagated back to parent via `turn_usage` field
- [ ] Retry logic fires on empty result before returning `confidence=0.0`
- [ ] Parent can re-delegate when confidence is below threshold (prompt-driven)

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `co_cli/agents/` | Create dir | Package for focused sub-agent factories |
| `co_cli/agents/__init__.py` | Create | Empty (docstring only) |
| `co_cli/agents/research.py` | Create | `make_research_agent`, `ResearchResult` |
| `co_cli/agents/analysis.py` | Create | `make_analysis_agent`, `AnalysisResult` (Phase B) |
| `co_cli/tools/delegation.py` | Create | `delegate_research`, `delegate_analysis` tools |
| `co_cli/agent.py` | Modify | Register delegation tools |
| `co_cli/deps.py` | Modify | Add `turn_usage` field (Phase C) |
| `docs/DESIGN-16-prompt-design.md` | Modify | Promote sub-agent delegation from Future to Current |
| `tests/test_delegation.py` | Create | Functional tests: research tool returns summary+sources, analysis tool returns conclusion |

---

## Related Documents

- `DESIGN-core.md` — agent factory, `CoDeps`, tool registration
- `DESIGN-tools.md` — tool return contract, approval pattern
- `DESIGN-16-prompt-design.md` — sub-agent delegation in Future Extensions section
- `TODO-background-execution.md` — prerequisite: background infrastructure (implement first)
