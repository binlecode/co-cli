# RESEARCH: agent-behavior-gaps — Remaining Behavioral Gaps for co-cli
_Date: 2026-03-16_

Status: forward-looking
Aspect: behavior quality and evidence quality
Pydantic-AI patterns: reflection, critique, retrieval quality

This document keeps only the non-overlapping conclusions from the earlier agentic-design-patterns review.

Baseline coverage that is now better handled elsewhere has been removed:

- Context architecture and state management gaps now live in [RESEARCH-co-agent-context-gap.md](./RESEARCH-co-agent-context-gap.md) (Note: "Recovery as a shared pattern" and "Bounded reflection for memory-save quality" were migrated to this document as they are natively solved by adopting Pydantic-AI idioms).
- Tools and skills comparison now lives in [RESEARCH-co-tools-skills-analysis.md](./RESEARCH-co-tools-skills-analysis.md)

The purpose of this file is narrower:

- Identify the **remaining agentic pattern gaps** that are strictly behavioral/functional, not structural.
- Avoid repeating current-state strengths already documented elsewhere.

---

## 1. Highest-Value Remaining Gaps

### 1.1 Critic loop for high-risk actions

`co-cli` still lacks a real producer-critic-revise loop for risky actions.

Best first target:

- shell command review before execution or approval

Why this still matters:

- shell actions are high-impact
- the system already has approvals and shell policy
- a critic layer would improve proposal quality before the user has to evaluate it

This is the clearest missing pattern that is not structural and remains a purely agentic workflow requirement.

### 1.2 Retrieval-unit quality for long knowledge sources

The retrieval stack is strong, but long-source handling is still a practical gap.

Best target:

- better chunking for long articles and long reference documents

Why this matters:

- full-document indexing reduces precision on long sources
- reranking helps, but weak chunk boundaries still cap quality

This sits at the border of context and retrieval, but it remains a concrete design-pattern gap around evidence gathering independent of the core Pydantic-AI FSM architecture.

---

## 2. Gaps That Should Stay Deferred

### 2.1 Broad supervisor-style multi-agent orchestration

Do not expand into a heavy supervisor/swarm pattern yet.

Why:

- current narrow delegation is easier to reason about
- broader agent swarms would add coordination overhead and harder debugging
- stronger scoped context and better bounded workflows (via Pydantic-AI dependency injection) should come first

### 2.2 Front-door routing for every turn

Do not add a heavyweight intent router by default.

Why:

- it would add latency
- current workflow dispatch and tool boundaries already cover the main need
- routing quality depends on a cleaner context model first

### 2.3 Platformizing MCP prematurely

Running `co-cli` as an MCP server is not a priority pattern investment right now.

Why:

- current value comes from using MCP tools, not exposing the whole system as a platform
- this is ecosystem expansion, not a core agent-quality bottleneck

---

## 3. Recommended Order

1. Finish the context architecture cleanup and Pydantic-AI alignment in [RESEARCH-co-agent-context-gap.md](./RESEARCH-co-agent-context-gap.md).
2. Let that context model drive tool and skill specs as described in [RESEARCH-co-tools-skills-analysis.md](./RESEARCH-co-tools-skills-analysis.md).
3. After that foundation is in place, invest in these behavioral pattern gaps:
   - critic loop for risky shell actions
   - better chunking/retrieval units for long knowledge sources

---

## Bottom Line

The older design-patterns review is no longer useful as a broad architecture summary because that content is now superseded by the context and tools/skills research docs. Furthermore, shared recovery and memory reflection gaps have been absorbed into the Pydantic-AI context alignment work.

What remains useful is much narrower:

- targeted reflection/critic loops (e.g., shell actions)
- better retrieval-unit design

Those are the remaining agentic pattern gaps worth preserving as a separate note.
