# REVIEW: Agentic Design Patterns — Insights for co-cli

Source: `docs/Agentic_Design_Patterns.pdf` (Antonio Gulli, 424 pages, Google)
Last reviewed: 2026-03-03 (updated — codebase state: openclaw skills delivery shipped)

---

## Reading Principle

This is a pragmatic review, not a compliance checklist. co-cli is a **personal AI operator
CLI** — single-user, interactive, approval-first, personality-driven. Not every pattern in
the book applies. Patterns that belong to large-scale enterprise multi-agent pipelines,
autonomous background agents, or embodied systems are noted and consciously set aside.

The book covers 21 patterns. This review cherry-picks the **12 patterns that are directly
actionable for co-cli** and briefly notes which ones are deliberately skipped and why.

---

## Patterns That Matter for co-cli

### 1. Prompt Chaining (Ch. 1) — Pipeline Pattern

**Core principle:** Decompose complex tasks into sequential focused prompts with
structured output (JSON) between steps to prevent ambiguity propagation. Combine with
parallel steps where sub-tasks are independent.

**co-cli status:** The agent loop in `_orchestrate.py` does multi-turn tool calling.
No explicit task decomposition yet. `prompts/rules/` applies rules once per session.

**Gap:** When a user asks a multi-faceted question (e.g. "research X, summarize, and
save to memory"), the agent handles it as one LLM context rather than a deliberate chain.
The skills system is a lightweight form of this: skill body = explicit step sequence fed
to the model. But no structured JSON handoff between steps.

**Verdict for co-cli:** Low urgency. Most of co-cli's tasks are single-turn interactive
requests. Chaining becomes relevant when `context:fork` subagent runs (Gap 10 in
TODO-skills-system.md). Defer until subagent infrastructure ships.

---

### 2. Routing (Ch. 2) — Conditional Dispatch

**Core principle:** Classify intent first, then route to the best handler. This is
pre-flight classification, distinct from tool selection.

**co-cli status:** `_signal_analyzer.py` is already a canonical routing pattern —
classifies memory signals as high/low/none, dispatches to different approval paths.
**Correct and done.**

**Gap:** No intent router for the main chat loop. "Is this a memory query, shell task,
or knowledge search?" is left to pydantic-ai's tool selection. This is fine for co-cli's
scale — adding a lightweight pre-classifier adds latency overhead that isn't justified
for interactive single-user sessions.

**Verdict:** Signal analyzer = done. Main chat routing = let pydantic-ai's tool selection
handle it. Not worth adding a separate classifier.

---

### 3. Parallelization (Ch. 3) — Concurrent Execution

**Core principle:** Execute independent sub-tasks concurrently (parallel LLM calls, tool
calls, or sub-agents). Use `asyncio` / `RunnableParallel`. Synthesize at a join point.

**co-cli status:** Single agent, sequential tool calls. No parallel execution today.
The `asyncio`-based architecture supports it but nothing exercises it.

**Gap:** The clearest win: when a user asks to "check calendar, search web, and recall
memory for meeting prep" — three independent tool calls that could run in parallel.
pydantic-ai does not automatically parallelize tool calls; each is sequential in the
ReAct loop.

**When it matters:** Primarily relevant when subagent delegation ships. A `delegate_coder`
and a `recall_memory` call for the same task are perfectly parallel. No value in
parallelizing tool calls within a single agent turn today (latency is dominated by
network/LLM, not Python execution).

**Verdict:** **Deferred.** The pattern is understood. The infrastructure (asyncio, pydantic-ai)
supports it. Priority rises when `context:fork` and subagent delegation ship.

---

### 4. Reflection (Ch. 4) — Producer-Critic Loop

**Core principle:** A Critic prompt evaluates Producer output against quality criteria,
then the Producer revises. Loop until criteria met or budget exhausted. Critic should
be a separate prompt persona, same or cheaper model.

**co-cli status:** Not implemented. Agent produces one response per turn.
The personality critique layer in `souls/{role}/critique.md` is a static behavioral
constraint — it shapes *how* the agent writes, not a runtime quality feedback loop.

**Gap:** Two high-value applications for co-cli:
1. **Shell command safety review** — generate command → critic checks safety/intent
   alignment → execute only if critic approves (stronger than current allowlist approach).
2. **Memory save quality** — signal analyzer is a weak form; a full critic pass on "is
   this worth saving, does it contradict existing memories?" would improve precision.

**Verdict:** **Medium priority.** Shell critic is the clearest win. The pattern is cheap
to implement (separate system prompt + structured output). Prerequisite for moving beyond
flat prefix matching in the shell policy engine (TODO-coding-tool-convergence.md TODO 2).

---

### 5. Tool Use (Ch. 5) — External Capability Pattern

**Core principle:** Tools raise specific errors, never return error strings. Tool
docstrings are the primary LLM contract. Another agent can be a tool (Supervisor-as-Tool).
Return structured data, not raw strings.

**co-cli status:** **Fully aligned and best practice.**
- `agent.tool()` + `RunContext[CoDeps]` ✓
- `dict[str, Any]` with `display` field ✓
- `requires_approval=True` for side effects ✓
- `ValueError`/exceptions on failure ✓
- `check_capabilities` tool for self-introspection ✓

**No gaps.** This is co-cli's strongest pattern implementation.

---

### 6. Memory Management (Ch. 8) — Three-Tier Model

**Book's three tiers:**
- **In-context:** Current conversation — ephemeral, window-limited
- **Session scratchpad:** Key-value per session, scoped prefixes (`temp:`)
- **Long-term MemoryService:** Cross-session, persistent, semantic search (RAG)

**Key rules:**
- `temp:` prefix = data valid only for current turn, not persisted
- Long-term retrieval should use BM25 or semantic search, not grep
- State updates via event append, not direct mutation

**co-cli status:**
- In-context: pydantic-ai message history ✓
- Session persistence: `_session.py` (UUID, TTL, compaction counter) ✓
- Long-term: `.co-cli/knowledge/memories/*.md` with FTS5 BM25 + temporal decay ✓
- Hybrid vector search: shipped, config-gated (`knowledge_search_backend=hybrid`) ✓
- **Gap:** No `temp:` scoped state for intra-turn intermediate computation
- **Gap:** Semantic search requires embedding provider (Ollama/Gemini) — not default
- **Gap:** Memory lifecycle (decay, contradiction detection) is rule-based, not LLM-driven

**Letta reference (in CLAUDE.md):** letta's approach — agent decides what to archive,
no auto-save, consolidation is agent-initiated — is more correct at scale. co-cli's
auto-save via signal detection is pragmatic for personal use but less precise.

**Verdict:** **Strong foundation.** The remaining gap is embedding-based semantic search
(MMR re-ranking is a nice-to-have after that). Confirmed by both the book and openclaw
peer analysis.

---

### 7. Model Context Protocol — MCP (Ch. 10)

**Core principle:** Standardized protocol for agent-tool communication. Tools exposed
as MCP servers can be used by any compliant agent. Key benefit: separation of tool
implementation from agent implementation.

**co-cli status:** **Implemented.** `DESIGN-mcp-client.md` — stdio transport,
auto-prefixing, approval inheritance. MCP tools appear alongside native tools; the
agent doesn't distinguish. Tested.

**Gap:** No MCP *server* capability — co-cli can consume MCP servers but cannot expose
its own tools via MCP for other agents to use. Not a current priority.

**Verdict:** Done as a client. Server-side exposure deferred (no use case yet).

---

### 8. Exception Handling & Recovery (Ch. 12) — Resilience Pattern

**Recovery hierarchy:**
1. Detect — malformed output, API errors, timeout, incoherent response
2. Handle — log → retry (backoff) → fallback → graceful degradation → notify
3. Recover — state rollback, self-correction via reflection, escalation

**co-cli status:**
- `_provider_errors.py`: provider-level error classification ✓
- Model fallback loop: `llm_fallback_models` config, `_swap_model_inplace()` ✓
- Telemetry: full SQLite trace for post-mortem ✓
- **Gap:** No structured retry-with-reflection on tool failure. When `recall_memory()`
  returns empty, the agent doesn't auto-retry with an alternative query formulation.
- **Gap:** No state rollback for failed multi-step operations.

**Verdict:** **Basic resilience is solid.** Retry-with-reflection for `recall_memory` is
the clearest improvement — model fallback is the stronger win already shipped.

---

### 9. Human-in-the-Loop (Ch. 13) — Approval Pattern

**Book distinguishes:**
- **Human-in-the-loop:** Agent pauses for explicit approval (co-cli's model)
- **Human-on-the-loop:** Human sets policy, AI acts within it, human reviews async

**co-cli status:** **Canonical implementation and best practice.**
- `requires_approval=True` + `DeferredToolRequests` ✓
- Persistent shell command approvals: `_exec_approvals.py` (pattern-based, 90-day TTL) ✓
- Three-option UX: y / a (always) / n ✓
- Arg validation before approval: `_validate_args()` + `_FORBIDDEN_ARG_PATTERNS` ✓

**Book insight:** Use a lightweight model to pre-classify whether a tool call needs
approval (high/low risk) before showing to user. co-cli's safe command allowlist is a
rule-based version of this. A proper ML risk classifier would be more adaptive.

**Verdict:** Fully aligned. Risk classifier upgrade is a P2 item in
TODO-coding-tool-convergence.md — correct prioritization.

---

### 10. Knowledge Retrieval / RAG (Ch. 14)

**Core principle:** Retrieve relevant context at query time, inject into prompt. Quality
of retrieval directly determines output quality. Key patterns: hybrid search (BM25 +
vector), reranking, chunking with overlap, MMR for diversity.

**co-cli status:**
- FTS5 BM25 search: shipped ✓
- Hybrid search (BM25 + vector cosine merge): shipped, config-gated ✓
- Temporal decay scoring: shipped ✓
- Reranker: optional (default disabled) ✓
- **Gap:** MMR re-ranking for diversity (5 near-identical results crowd out coverage)
- **Gap:** Embedding provider requires setup (not zero-config)
- **Gap:** No chunking for long articles — full document retrieved

**Book rule:** The richness of retrieved context is a primary determinant of output
quality. co-cli's knowledge system design is correct; the gaps are optimization-tier.

**Verdict:** **Strong.** MMR is the next logical step. Embedding provider complexity is
a real onboarding barrier — worth providing a simpler default (e.g., local Ollama
nomic-embed-text).

---

### 11. Reasoning Techniques (Ch. 17) — ReAct + CoT + Self-Correction

**ReAct loop:** `Thought → Action → Observation → repeat`
This is exactly pydantic-ai's tool-calling loop. **co-cli already implements ReAct.**

**Chain-of-Thought (CoT):** Explicit step-by-step reasoning before acting.
`prompts/rules/03_reasoning.md` is the correct application.

**Scaling Inference Law:** More "thinking time" → better output. For complex tasks,
agent should reason explicitly before calling tools, not jump immediately.

**Self-Correction:** Explore multiple approaches, backtrack on failure. Not needed at
co-cli's current scale.

**co-cli status:** **Aligned.** pydantic-ai loop = canonical ReAct. Reasoning rules
in prompt layer. Mindset system (pending simplification) extends this with task-type
awareness.

**Verdict:** Done. The pending mindset simplification (TODO-mindset-static-fold.md) is
the right housekeeping — fold the 6 mindset bodies into static soul block, eliminate
the pre-turn classification LLM call.

---

### 12. Guardrails / Safety (Ch. 18) — Layered Defense

**Layers (input → behavior → output):**
1. Input validation — pre-screen with lightweight fast model before main agent
2. Behavioral constraints — system prompt rules (identity, refusal patterns)
3. Tool restrictions — `requires_approval=True`
4. Output filtering — post-process for policy violations
5. Observability — log all inputs/outputs/tool calls for auditing

**Key:** Use a **fast, cheap model** as guardrail evaluator (not the main model).

**co-cli status:**
- System prompt rules in `prompts/rules/` ✓
- `requires_approval=True` + arg validation ✓
- Persistent approval patterns ✓
- Security findings in `status.py` ✓
- Full SQLite telemetry ✓
- **Gap:** No input pre-screening layer before the main agent
- **Gap:** No output filtering layer (policy violations post-generation)

**Verdict:** Operationally safe for personal use. Input pre-screening would be valuable
for public deployments — not a current priority for single-user personal CLI.

---

### 13. Evaluation & Monitoring (Ch. 19) — Continuous Assessment

**Evaluation hierarchy:**
1. Response accuracy — ground truth comparison
2. Tool chain completion — correct tools, correct order
3. Goal achievement — final state matches intended outcome
4. Safety invariants — no constraint violations

**co-cli status:** Eval suite in `evals/` covers personality behavior, signal detection,
approval path, safety abort, memory recall. **Well aligned.**

**Gaps:**
- Evals use heuristic scoring + LLM-as-judge in the same context. The book recommends
  separating judge from producer — a dedicated eval judge agent with a specific
  evaluation prompt is more reliable (less confirmation bias).
- New `evals/eval_skill_finch.py` is good addition — skill consistency evaluation.
- No continuous monitoring in production (no alerting on degraded eval scores).

**Verdict:** Good eval discipline. The judge-separation improvement is worth doing before
the eval suite grows larger.

---

## Patterns Consciously Skipped

| Pattern | Chapter | Reason for Skipping |
|---|---|---|
| Planning | Ch. 6 | co-cli is reactive/interactive, not goal-autonomous. Skills body = implicit planning. ReAct loop is sufficient. |
| Multi-Agent Topologies | Ch. 7 | Supervisor-as-Tool is the right entry point when subagent delegation ships. Full mesh/hierarchical is premature. |
| Learning & Adaptation | Ch. 9 | Memory system already provides the relevant form of learning (persistent KV + FTS5). Online model fine-tuning is out of scope. |
| Goal Setting & Monitoring | Ch. 11 | co-cli is an interactive assistant, not an autonomous agent pursuing long-horizon goals. |
| Inter-Agent Communication A2A | Ch. 15 | Only relevant when co-cli orchestrates other agents. Deferred with subagent delegation. |
| Resource-Aware Optimization | Ch. 16 | Model fallback loop covers the critical case. Budget capping (token limits) is already in history processors. Full cost optimization is premature. |
| Prioritization | Ch. 20 | Relevant only when background execution ships (TODO-background-execution.md). |
| Exploration & Discovery | Ch. 21 | Skills registry injection into system prompt covers this adequately for now. |

---

## Updated Priority Actions

| Priority | Pattern | Action | Where |
|---|---|---|---|
| **P0** | Tool Use | Native file tools (read/write/find/list) to replace shell as primary editing surface | TODO-coding-tool-convergence.md TODO 1 |
| **P0** | Guardrails | Shell policy engine — replace flat prefix match with structured rule evaluator | TODO-coding-tool-convergence.md TODO 2 |
| **P1** | Reflection | Shell command critic: generate → critic safety/intent check → execute | TODO-coding-tool-convergence.md TODO 2 extension |
| **P1** | Multi-Agent | `delegate_coder` subagent via Supervisor-as-Tool pattern | TODO-coding-tool-convergence.md TODO 3 |
| **P1** | Prompt Chaining | Mindset simplification: fold 6 mindsets into static soul block, drop pre-turn LLM call | TODO-mindset-static-fold.md |
| **P2** | Memory | MMR re-ranking for knowledge search diversity | TODO-gap-openclaw-analysis.md Gap 8 |
| **P2** | Memory | Semantic search: embedding provider layer (Ollama default) | TODO-gap-openclaw-analysis.md Gap 9 |
| **P2** | Exception Handling | Retry-with-reflection on `recall_memory()` empty result | New gap |
| **P2** | Evaluation | Separate eval judge from producer context | New gap |
| **P3** | Skills | `allowed-tools` per-skill grants | TODO-skills-system.md Gap 8 |
| **P3** | Parallelization | Parallel tool calls when subagent delegation ships | Deferred |
| **P3** | Guardrails | Input pre-screening layer (lightweight model) | Low priority for personal CLI |

---

## What co-cli Gets Right (confirmed by book)

- pydantic-ai tool loop = canonical ReAct implementation ✓
- `requires_approval=True` + persistent patterns = canonical human-in-the-loop ✓
- Memory system direction (FTS5 → hybrid → semantic) = book's recommended evolution ✓
- Eval suite structure matches the evaluation hierarchy ✓
- `dict[str, Any]` with `display` field = correct tool return contract ✓
- SQLite telemetry = best practice for observability ✓
- Personality system in `prompts/rules/` = correct behavioral constraint layer ✓
- Session persistence (`_session.py`) = correct session scratchpad tier ✓
- MCP client = correct external tool protocol adoption ✓
- Skills system (bundled + project-local) = correct extensibility pattern ✓

---

## Refined Learning Gap List

These are the conceptual gaps to understand deeply to lead the next phase of co-cli.
Each maps to a specific open TODO and a real engineering decision.

**Complexity filter:** The book is written for enterprise systems at Google scale.
co-cli is a personal tool for one user. Most of these gaps, if implemented naively,
add complexity. The test before building any of them: *"what user pain does this solve,
and is that pain real today?"* Each gap is annotated with a net complexity direction
and a concrete build trigger — the observable condition that justifies implementing it.

### Gap 1 — Reflection / Critic Pattern
**What to learn:** How to design a lightweight critic agent that evaluates a generated
artifact against safety and quality criteria without adding excessive latency. The key
trade-off: separate LLM call vs. few-shot self-evaluation in a single call.

**Why it matters:** Shell command safety (replacing flat prefix matching) and memory save
quality both need this. The critic pattern is the conceptual foundation for TODO 2 in
coding-tool-convergence.

**Complexity assessment:** Net neutral to net simpler — *if* the critic replaces the
existing `_is_safe_command()` rule file and `_FORBIDDEN_ARG_PATTERNS`. One LLM call in
exchange for removing a growing brittle rule system is a good trade. If the critic is
added on top of the existing rules without removing them, it is purely additive and not
worth it.
**Build trigger:** `_is_safe_command()` or `_FORBIDDEN_ARG_PATTERNS` requires a third
special-case patch to handle a new dangerous pattern. At that point the rule system has
outgrown its complexity budget — replace it.

**Study:** Ch. 4 of this book. Also: [Constitutional AI (Anthropic, 2022)](https://arxiv.org/abs/2212.09251)
for how critique can be embedded in training vs. runtime.

---

### Gap 2 — Hybrid Search + Reranking
**What to learn:** How BM25 and vector search scores are merged (weighted linear
combination vs. reciprocal rank fusion vs. learned merge). How MMR balances relevance
with diversity. When a cross-encoder reranker adds value vs. just noise.

**Why it matters:** Hybrid search is shipped but the merge strategy is empirically
untuned. MMR re-ranking is the next step. Embedding provider complexity is a real
UX problem.

**Complexity assessment:** Purely additive. MMR adds a post-processing step to a working
system. Embedding provider adds config surface, a new dependency, and setup friction.
The knowledge search already works well for a personal knowledge base of ~hundreds of
notes. These are optimization-tier improvements, not correctness fixes.
**Build trigger:** You notice yourself getting 4-5 near-identical results when searching
for a topic with multiple distinct facets — that's the MMR signal. The embedding layer
is worth adding only if keyword mismatch causes a recall miss you can reproduce (e.g.
searching "productivity" fails to find a note you remember writing about "focus").

**Study:** Ch. 14 of this book. Also: [Pinecone Hybrid Search guide](https://www.pinecone.io/learn/hybrid-search-intro/),
openclaw `src/memory/` (in CLAUDE.md reference repos).

---

### Gap 3 — Subagent Delegation Architecture
**What to learn:** How Supervisor-as-Tool works in practice — context isolation, budget
sharing, result aggregation, failure handling. How to design subagent boundaries so the
main agent stays coherent while delegating deep work.

**Why it matters:** This is the prerequisite for `delegate_coder` (TODO-coding-tool-convergence),
skills `context:fork` (TODO-skills-system Gap 10), and any future parallel tool execution.

**Complexity assessment:** The largest complexity jump in this list. New state machines,
context isolation, budget propagation across agent boundaries, new failure modes, and a
significantly larger test surface. Subagent infrastructure is not free — it adds a whole
new tier to the system. For a personal CLI where the main agent can already handle most
tasks, the overhead may not be justified yet.
**Build trigger:** You have a concrete task that the single-agent loop fails or degrades
on — e.g. a long coding session where the main context fills with code before the task is
done, or a skill that needs isolated execution to avoid polluting the conversation history.
Do not build this speculatively. Build it when the pain is observable.

**Study:** Ch. 7 of this book (Multi-Agent topologies). Also: Claude Code agent architecture
in `~/workspace_genai/claude-code/packages/core/src/scheduler/` (local ref).

---

### Gap 4 — Shell Policy Engine Design
**What to learn:** How to go from flat prefix matching to a structured policy evaluator
that understands command semantics — tokenization, flag inspection, heredoc/env-injection
detection, recursive shell wrapper parsing. The trade-off between rule-based vs.
LLM-assisted classification.

**Why it matters:** P0 item. Current `_is_safe_command()` is brittle. Codex-rs
(CLAUDE.md reference: `codex-rs/shell-command/src/command_safety/`) is the deepest
implementation to study.

**Complexity assessment:** Net simpler. The goal is to *replace* ~200 lines of brittle
prefix matching and regex patterns with a clean, structured evaluator that has clear
semantics and is easier to test. This is removing accidental complexity, not adding
intentional complexity. The rule system grows unbounded as new attack vectors are found;
a policy engine with explicit semantics does not.
**Build trigger:** This one is already triggered. The current `_is_safe_command()` is
the known P0 weakness. Build it as part of the coding tool convergence work.

**Study:** `~/workspace_genai/codex/codex-rs/shell-command/src/command_safety/`.
Combine with Reflection pattern (Gap 1) for the critic-gated execution model.

---

### Gap 5 — LLM-Driven Memory Consolidation
**What to learn:** How to move from rule-based signal detection (current) to LLM-driven
fact extraction, contradiction resolution, and memory consolidation. The key question:
when should the agent decide to archive vs. discard vs. update an existing memory?

**Why it matters:** Signal analyzer is good enough today. At scale, LLM-driven
consolidation (as in letta and mem0) produces higher-quality long-term memory.

**Complexity assessment:** More complex and harder to test. The signal analyzer is
deterministic enough to cover with functional tests. LLM-driven consolidation is
probabilistic — harder to assert on, more expensive to run, and introduces a new class
of subtle failure (the model decides not to save something it should). The current
system trades some recall precision for strong testability and zero extra LLM calls.
That trade is correct for a personal CLI.
**Build trigger:** You observe the signal analyzer consistently missing or misclassifying
a category of valuable information that you'd want saved — and you can describe that
category clearly enough to write an eval for it. At that point the rule-based system
has hit its ceiling and the LLM-driven approach is justified.

**Study:** letta `letta/functions/function_sets/base.py` (`core_memory_*`, `archival_memory_*`).
mem0 `mem0/memory/main.py` (LLM-driven extraction). Ch. 8 of this book.

---

### Gap 6 — Context Governance Under Pressure
**What to learn:** How to make correct decisions about what to keep and what to drop when
the context window is filling up — not just sliding window truncation but smart
summarization that preserves task-critical state. When to summarize vs. when to drop.

**Why it matters:** Context compaction (`/compact`) exists but the summarization strategy
is simple. As multi-step tasks get longer, context quality degrades. This affects output
quality directly (Scaling Inference Law from Ch. 17).

**Complexity assessment:** Additive complexity. Smarter summarization means more LLM
calls at compaction time and more decisions to tune (what counts as task-critical?).
The current sliding window + summarizer may be entirely sufficient for most interactive
sessions — co-cli conversations are typically short to medium length, not multi-hour
agentic coding runs.
**Build trigger:** You regularly hit a point in a session where the agent loses the
thread of an earlier decision or forgets a constraint you stated — and `/compact` doesn't
restore it. That's the context degradation signal. Until that happens consistently, the
current approach is good enough.

**Study:** Ch. 17 reasoning techniques. `_history.py` history processors. Appendix A
(Advanced Prompting Techniques) in this book.

---

## Reference Material to Study

These are concrete materials that directly support the gaps above. Already available
locally or worth fetching.

| Material | Covers | Status |
|---|---|---|
| This book Ch. 4 (Reflection) | Critic pattern, producer-critic loop | Available: `docs/Agentic_Design_Patterns.pdf` |
| This book Ch. 7 (Multi-Agent) | Supervisor-as-Tool, topologies | Available: `docs/Agentic_Design_Patterns.pdf` |
| This book Ch. 8 (Memory) | Three-tier model, session scratchpad | Available: `docs/Agentic_Design_Patterns.pdf` |
| This book Ch. 14 (RAG) | Hybrid search, reranking, chunking | Available: `docs/Agentic_Design_Patterns.pdf` |
| This book Appendix A (Advanced Prompting) | Context engineering, CoT, few-shot | Available: `docs/Agentic_Design_Patterns.pdf` |
| This book Appendix E (AI Agents on the CLI) | CLI-specific agent design | Available: `docs/Agentic_Design_Patterns.pdf` |
| openclaw `src/memory/` | Production hybrid search: FTS5 + sqlite-vec + MMR | Local: `~/workspace_genai/openclaw` |
| letta `letta/functions/function_sets/base.py` | LLM-driven memory tools | Local: `~/workspace_genai/letta` |
| codex-rs `shell-command/src/command_safety/` | Deep shell safety classification | Local: `~/workspace_genai/codex` |
| mem0 `mem0/memory/main.py` | LLM-driven fact extraction + contradiction resolution | Local: `~/workspace_genai/mem0` |
| Constitutional AI (Anthropic 2022) | Critic/self-critique pattern | Paper: arxiv.org/abs/2212.09251 |
