# TODO: Agentic Loop & Prompting (Remaining Work)

This TODO contains only unimplemented work. Implemented architecture and behavior live in `docs/DESIGN-16-prompt-design.md`, with supporting detail in `DESIGN-core.md`, `DESIGN-07-context-governance.md`, `DESIGN-02-personality.md`, and `DESIGN-tools.md`.

Recommended sequence across active TODOs:
1. `TODO-tool-docstring-template.md` (improve tool-selection reliability first)
2. `TODO-co-agentic-loop-and-prompting.md` item 3 (personality prompt-budget optimization)
3. `TODO-background-execution.md` (long-running task substrate)
4. `TODO-co-agentic-loop-and-prompting.md` item 1 (sub-agent delegation)
5. `TODO-sqlite-fts-and-sem-search-for-knowledge-files.md` + `TODO-knowledge-articles.md` core indexing/article flow
6. `TODO-co-agentic-loop-and-prompting.md` item 2 (confidence-scored advisory outputs)

## 1. Sub-Agent Delegation (Deferred)

Priority:
- P1 (high impact, medium complexity)

When to implement:
- After `TODO-tool-docstring-template.md` is shipped.
- After baseline `TODO-background-execution.md` infrastructure is available (so deep/long delegations can evolve toward background execution paths).
- Keep this before voice and other UX overlays.

Best sequence rationale:
- Sub-agents amplify tool usage. Tool docstrings should be hardened first to reduce wrong-call propagation.
- Background task primitives reduce risk that delegated long tasks block interactive flow.

Objective:
- Add focused research and analysis sub-agents for multi-step directives where prompt-only execution still exits early.

Scope:
- Research sub-agent: search -> fetch -> synthesize.
- Analysis sub-agent: compare/evaluate/synthesize multiple inputs.
- Structured outputs (`ResearchResult`, `AnalysisResult`) with required fields.
- Parent agent remains orchestrator/validator.
- Shared budget semantics (sub-agent usage counts toward parent turn budget).

Acceptance criteria:
- Delegation path is explicitly tool-invoked and traceable in OTel spans.
- Sub-agent output is typed and validated before parent response.
- Parent can re-delegate or escalate when output quality gates fail.
- No bypass of existing approval/safety constraints.

Likely files:
- `co_cli/agent.py`
- `co_cli/_orchestrate.py`
- `co_cli/tools/` (delegation entry tools)
- `co_cli/deps.py`
- `docs/DESIGN-16-prompt-design.md`

## 2. Confidence-Scored Advisory Outputs (Deferred)

Priority:
- P2 (quality enhancement, lower urgency than delegation)

When to implement:
- After `TODO-sqlite-fts-and-sem-search-for-knowledge-files.md` Phase 1 is complete (ranked retrieval baseline).
- Preferably after Phase 2 hybrid retrieval and `TODO-knowledge-articles.md` article flow, so confidence is derived from stronger ranking signals across sources.

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

## 3. Personality Prompt Budget Optimization (Deferred)

Priority:
- P1 (high leverage, low-to-medium complexity)

When to implement:
- After `TODO-tool-docstring-template.md` updates (to avoid conflating tool-guidance and personality-token effects).
- Before sub-agent rollout, so base prompt budget is lean before adding new delegation instructions.

Best sequence rationale:
- This is a low-risk optimization that can reclaim context budget early and improve headroom for upcoming loop/delegation features.

Objective:
- Reduce per-turn personality payload size while preserving behavioral fidelity.

Scope:
- Compress trait-driven behavior payload.
- Keep existing role fidelity and safety constraints unchanged.

Acceptance criteria:
- Measurable token reduction in per-turn personality contribution.
- No regression in personality adherence evals.
- `DESIGN-02-personality.md` and `DESIGN-16-prompt-design.md` updated with final mechanism.

Likely files:
- `co_cli/prompts/personalities/_composer.py`
- `co_cli/prompts/personalities/behaviors/*.md`
- `docs/DESIGN-02-personality.md`
- `docs/DESIGN-16-prompt-design.md`
