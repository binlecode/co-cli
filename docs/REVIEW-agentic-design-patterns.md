# REVIEW: Agentic Design Patterns — Insights for co-cli

Source: `docs/Agentic_Design_Patterns.pdf` (Antonio Gulli, 424 pages, Google)
Reviewed: 2026-02-26

---

## Pattern-by-Pattern Analysis

### 1. Prompt Chaining (Ch. 1) — Pipeline Pattern

**Core principle:** Decompose complex tasks into sequential focused prompts.
Use **structured output (JSON)** between steps to prevent ambiguity propagation.
Combine with parallel steps where sub-tasks are independent.

**co-cli status:** The agent loop in `_orchestrate.py` does multi-turn tool calling but
doesn't explicitly decompose compound user requests into chained sub-calls.

**Gap:** When a user asks a multi-faceted question, the agent handles it as one LLM
context rather than a deliberate chain. Book rule: *use chaining when there are
multiple distinct processing stages or when one step's output quality gates the next.*

**Best practice:** Assign a **distinct role per step** (e.g., "Analyst" → "Writer" →
"Reviewer"). co-cli's personality system maps to this but is applied once per session,
not per-step within a complex task.

---

### 2. Routing (Ch. 2) — Conditional Dispatch

**Core principle:** Classify intent first, then route to specialized handler/tool/agent.
This is pre-flight classification, distinct from tool selection.

**co-cli status:** `_signal_analyzer.py` is already a routing pattern — classifies
signals as high/low/none then dispatches to different approval paths. **Correct.**

**Gap:** No intent routing for the main chat loop itself. "Is this a memory query, shell
task, or knowledge search?" is left to pydantic-ai's tool selection rather than a
lightweight pre-classifier.

---

### 3. Reflection (Ch. 4) — Producer-Critic Loop

**Core principle:** A Critic agent evaluates Producer's output against quality criteria,
then the producer revises. Loop: produce → critique → revise → until criteria met or
budget exhausted. Critic should be a separate prompt persona, same or cheaper model.

**co-cli status:** Not implemented. Agent produces one response per turn.

**Gap:** Especially valuable for:
- Memory save decisions (signal analyzer is a weak form)
- Shell command generation (generate → review safety → execute)

---

### 4. Tool Use (Ch. 5) — External Capability Pattern

**Core principle:** Tools should raise specific errors, never return error strings.
Tool docstrings are the primary contract for LLM tool selection. Another agent can
be a tool ("Supervisor-as-Tool" pattern).

**co-cli status:** Strong alignment. `agent.tool()` + `RunContext[CoDeps]` +
`requires_approval=True` + `dict[str, Any]` return with `display` field. **Best practice.**

**Confirmed:** Raise `ValueError`/exceptions on tool failure, not error strings.

---

### 5. Memory Management (Ch. 8) — Three-Tier Model

**Book's three tiers:**
- **In-context:** Current conversation — ephemeral, window-limited
- **Session scratchpad:** Key-value per session, scoped prefixes (`user:`, `app:`, `temp:`)
- **Long-term MemoryService:** Cross-session, persistent, semantic search (RAG)

**Key rules:**
- `temp:` prefix = data valid only for current turn, not persisted
- State updates must go through event append (not direct dict mutation)
- Long-term retrieval should use BM25 or semantic search, not grep

**co-cli status:**
- In-context: pydantic-ai message history ✓
- Long-term: `.co-cli/knowledge/memories/*.md` ✓
- **Gap:** No session scratchpad (`temp:` equivalent) for intermediate turn state
- **Gap:** Memory retrieval is grep-based → FTS5→semantic search roadmap confirmed correct

---

### 6. Exception Handling & Recovery (Ch. 12) — Resilience Pattern

**Recovery hierarchy:**
1. Detect — malformed output, API errors, timeout, incoherent response
2. Handle — log → retry (backoff) → fallback → graceful degradation → notify
3. Recover — state rollback, self-correction via reflection, escalation
4. Combine with reflection — use failure as input to a reflective re-prompt

**co-cli status:** `_provider_errors.py` handles provider errors. Basic retry exists.

**Gap:** No structured retry-with-reflection for tool failures. When `recall_memory()`
returns empty, agent doesn't auto-retry with alternative query formulation.

---

### 7. Human-in-the-Loop (Ch. 13) — Approval Pattern

**Book distinguishes:**
- **Human-in-the-loop:** Agent pauses for explicit approval (co-cli's model)
- **Human-on-the-loop:** Human sets policy, AI acts within it, human reviews async

**co-cli status:** `requires_approval=True` + `DeferredToolRequests` is canonical. **Best practice.**

**Insight:** Use a lightweight model to pre-classify whether a tool call needs approval
(high/low risk) rather than showing every approval to the user. Applies to shell
command safety classification.

---

### 8. Reasoning Techniques (Ch. 17) — ReAct + CoT + Self-Correction

**ReAct loop:** `Thought → Action → Observation → repeat`
This is exactly pydantic-ai's tool-calling loop. **co-cli already implements ReAct.**

**Chain-of-Thought (CoT):** Explicit step-by-step reasoning before acting.
`prompts/rules/03_reasoning.md` is the correct application of this.

**Scaling Inference Law:** More "thinking time" → better output. For difficult tasks,
agent should reason explicitly before calling tools, not jump to tool calls immediately.

**Tree-of-Thought / Self-Correction:** Explore multiple paths, backtrack on failure.
Not needed at co-cli's current scale — useful for complex planning agents.

---

### 9. Guardrails / Safety (Ch. 18) — Layered Defense

**Layers (input → behavior → output):**
1. Input validation — pre-screen with lightweight fast model before main agent
2. Behavioral constraints — system prompt rules (identity, refusal patterns)
3. Tool restrictions — `requires_approval=True`
4. Output filtering — post-process for policy violations
5. Observability — log all inputs/outputs/tool calls for auditing

**Key:** Use a **fast, cheap model** as guardrail evaluator (not the main model).

**co-cli status:**
- System prompt rules in `prompts/rules/` ✓
- `requires_approval=True` ✓
- Full SQLite telemetry ✓
- **Gap:** No input pre-screening layer before the main agent

---

### 10. Evaluation & Monitoring (Ch. 19) — Continuous Assessment

**Evaluation hierarchy:**
1. Response accuracy — ground truth comparison
2. Tool chain completion — correct tools, correct order
3. Goal achievement — final state matches intended outcome
4. Safety invariants — no constraint violations

**co-cli status:** Eval suite in `evals/` covers personality behavior, signal detection,
approval path, safety abort, memory recall. **Well aligned.**

**Gap:** Evals use heuristic scoring + LLM-as-judge in the same context. Book
recommends separating judge from producer — dedicated eval judge agent with a
specific evaluation prompt is more reliable.

---

### 11. Multi-Agent Topologies (Ch. 7)

**Six topologies:**
| Topology | Description |
|---|---|
| Network | Peer agents communicate freely |
| Supervisor | One orchestrator delegates to workers |
| **Supervisor-as-Tool** | Orchestrator calls sub-agents as tools ← recommended for co-cli |
| Hierarchical | Nested supervisors |
| Custom | Application-specific routing |

**co-cli status:** Single-agent. Sub-agent delegation in `TODO-subagent-delegation.md`.

**Recommended entry point:** Supervisor-as-Tool. Main chat agent calls specialized
sub-agents (signal analyzer, memory consolidator, web researcher) as tools.
Already partially implemented with signal analyzer as mini-agent.

---

### 12. Context Engineering (Ch. 1 sidebar) — Beyond Prompt Engineering

**Core principle:** Context = system prompt + retrieved docs + tool outputs + implicit
data (user identity, history, env state). The richness of context is a primary
determinant of output quality.

**Layers:**
- System prompt: operational parameters and role
- Retrieved documents: pulled from knowledge base at query time
- Tool outputs: real-time data from API calls
- Implicit data: user preferences, interaction history

**co-cli:** This is exactly the memory + tool system design. Confirmed correct direction.

---

## Priority Actions

| Priority | Pattern | Action |
|---|---|---|
| **High** | Memory retrieval | Move from grep to FTS5 (existing TODO confirmed correct) |
| **High** | Tool error recovery | Structured retry-with-reflection on tool failure |
| **High** | Eval judge separation | Dedicated eval judge separate from producer context |
| **Medium** | Input pre-screening | Lightweight guardrail classifier before main agent |
| **Medium** | Session scratchpad | `temp:` scoped state within a single agent run |
| **Medium** | Supervisor-as-Tool | Sub-agent delegation via tool pattern for specialized workflows |
| **Low** | CoT enforcement | Explicit reasoning step before first tool call on complex tasks |
| **Low** | Routing | Intent classifier before agent invocation for known task types |

---

## What co-cli Gets Right (confirmed by book)

- pydantic-ai tool loop = canonical ReAct implementation
- `requires_approval=True` = canonical human-in-the-loop
- Memory system direction (FTS5→semantic search) = book's recommended evolution
- Eval suite structure matches the book's evaluation hierarchy
- `dict[str, Any]` with `display` field = correct tool return contract
- SQLite telemetry = best practice for observability
- Personality system in `prompts/rules/` = correct behavioral constraint layer
