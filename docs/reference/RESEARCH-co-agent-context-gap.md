# RESEARCH: co-agent-context-gap
_Date: 2026-03-16_

## Executive Summary

This document analyzes the architectural gaps between `co`'s current context management implementation and **Pydantic-AI's idiomatic state management patterns**. 

While the previous research (`RESEARCH-co-agent-context.md`) correctly identified that `co` possesses a strong, layered context model (prompt identity, durable memory, transcript history, session summaries), it revealed a fundamental philosophical mismatch: **`co` manages context as a pipeline of implicit string mutations, whereas Pydantic-AI expects context to be explicitly managed via strongly-typed dependency injection (`RunContext[Deps]`) and bounded state transitions.**

Furthermore, cross-referencing with `RESEARCH-agentic-design-pattern-gaps.md` reveals that adopting Pydantic-AI's idioms will natively solve several lingering agentic pattern gaps—specifically **Recovery as a shared pattern** and **Bounded reflection for memory-save quality**.

To fully leverage the underlying `pydantic-ai` framework without over-engineering the system, `co` must shift its context assembly from opaque middleware processors to explicit, typed contracts.

---

## Gap 1: Implicit Context Injection vs. Explicit Dependency Injection

### The Issue
Currently, `co` builds its context implicitly. In `co_cli/context/_history.py`, a function like `inject_opening_context()` proactively fetches memory and injects it into the prompt. Simultaneously, `co_cli/agent.py` aggregates project instructions, the current date, and personality guidance into a monolithic system prompt string before the agent run begins.

### Detailed Analysis
Pydantic-AI's design philosophy rejects "magic" global state or hidden pre-processors. The idiomatic approach is to pass a strongly-typed `Deps` object into the agent at runtime (`agent.run(deps=...)`). System prompts and dynamic context should be constructed using `@agent.system_prompt` decorated functions that strictly read from the injected `RunContext[Deps]`. 

Because `co` builds context outside of Pydantic-AI's dependency injection loop, it breaks traceability. If a tool or a subagent needs to know *why* a certain memory is in the prompt, or needs to access the raw configuration, it cannot easily do so because the context has already been flattened into a string.

### The Pragmatic Fix
1. **Leverage `co_cli/deps.py`:** `co` already has a solid dependency container separating `services`, `config`, `session`, and `runtime`. Make this the official `Deps` type for the main `co` agent.
2. **Refactor Prompt Construction:** Remove the opaque `inject_opening_context()` pipeline. Instead, use Pydantic-AI's dynamic prompt decorators:
   ```python
   @agent.system_prompt
   async def build_standing_context(ctx: RunContext[CoDeps]) -> str:
       # Explicitly fetch and format memories/config using injected dependencies
       memories = await ctx.deps.services.memory.get_standing_context()
       return format_context(ctx.deps.config, memories)
   ```

---

## Gap 2: Read-Path Mutations vs. Functional Tool Purity

### The Issue
`co_cli/tools/memory.py` contains read-path "gravity" behavior—specifically, recalling or searching for a memory silently rewrites its timestamp or ranking state to surface it more easily in the future.

### Detailed Analysis
Pydantic-AI champions functional programming paradigms where tools are pure functions mapped to a `RunContext`. A tool executed by the LLM (or a background retrieval process) should only do what its schema and docstring declare. Silent background mutations on a "read" operation destroy the predictability of the tool execution loop and create invisible side effects that Pydantic-AI's observability tools (like Logfire) cannot trace logically.

### The Pragmatic Fix & Solving Pattern Gaps
1. **Remove Read-Path Mutations:** Strip any `update_timestamp` or ranking mutations out of the `search` and `recall` read paths in `memory.py`.
2. **Make State Changes Explicit (Solves "Bounded reflection for memory-save quality"):** `RESEARCH-agentic-design-pattern-gaps.md` notes the need for a critic/review pass for memory-save candidates. By removing silent read-path mutations, we force memory updates into the open. `co` can provide a strict `UpdateMemoryPriority` tool with a Pydantic `OutputSchema` that requires the LLM to provide a `reasoning` string before updating a memory's gravity. This natively introduces the missing quality-control pattern.

---

## Gap 3: Subagent Context Boundaries (Heuristics vs. Typed Contracts)

### The Issue
When `co` spawns a delegated read-only subagent, it isolates the subagent by giving it fresh session/runtime state via `make_subagent_deps()`. However, the exact context the subagent inherits relies on heuristics, leading to the open question: *"What standing memory should a delegated specialist see?"*

### Detailed Analysis
In Pydantic-AI, an agent's input is defined by its generic signature: `Agent[SubAgentDeps, OutputSchema]`. If a specialist needs certain project instructions, memory, or history, it must be explicitly declared in the `SubAgentDeps` Pydantic model passed from the parent agent. 

### The Pragmatic Fix
1. **Define Explicit Pydantic Models for Handoffs:** 
   ```python
   class SpecialistDeps(BaseModel):
       standing_instructions: str
       relevant_memories: list[MemoryRecord]
       task_objective: str
   ```
2. **Enforce the Boundary:** The parent agent must construct this `SpecialistDeps` object explicitly and inject it. This forces developers to answer the visibility policy question in the type signature itself.

---

## Gap 4: Hidden Middleware Processors vs. Explicit Graph/FSM

### The Issue
`co` manages transcript history, doom-loop checks, and sliding-window compaction via a hidden chain of processors in `_history.py`. 

### Detailed Analysis
Pydantic-AI explicitly pushes complex workflows (like summarization loops, tool trimming, or doom-loop recovery) out of hidden middleware and into `pydantic-graph`, or handles them via native framework features. Idiomatically, context compaction is an explicit agentic task, not a silent background string manipulation.

### The Pragmatic Fix & Solving Pattern Gaps
*Note: We must adhere to the rule "do not expand the architecture prematurely." A full rewrite into `pydantic-graph` is not recommended yet.*

1. **Refocus Compaction as an Explicit Task:** When a token threshold is breached, the runtime should explicitly invoke a `CompactionAgent` that returns a strongly-typed `SessionSummary` Pydantic model. 
2. **Formalize the Doom-Loop Guard (Solves "Recovery as a shared pattern"):** `RESEARCH-agentic-design-pattern-gaps.md` highlights the need for generalized reformulate-and-retry patterns. By moving away from hidden middleware trims, `co` can use Pydantic-AI's built-in `ModelRetry` exceptions and `UsageLimits`. If a doom-loop is detected, raising a `ModelRetry("You are looping, reformulate your approach")` natively plugs into Pydantic-AI's reflection cycle, providing the exact shared recovery pattern requested without building custom middleware.

---

## Conclusion & Implementation Priority

To resolve the mismatch between `co`'s legacy string-manipulation context and Pydantic-AI's idiomatic typed context—while simultaneously addressing lingering agentic pattern gaps—the following adoption path is recommended:

1. **Immediate (P0):** Eradicate read-path mutations in `memory.py`. Read operations must be strictly side-effect free.
2. **High (P1):** Refactor `agent.py` and `_history.py`. Move from `inject_opening_context()` to Pydantic-AI's `@agent.system_prompt` decorators, pulling strictly from `RunContext[CoDeps]`. Use native `ModelRetry` for shared recovery loops.
3. **Medium (P2):** Type-safe Subagents. Replace `make_subagent_deps()` heuristics with explicit `SpecialistDeps` Pydantic models for subagent spawning.
4. **Low/Ongoing (P3):** Gradually migrate hidden middleware (compaction) into explicit Pydantic-AI agent invocations, treating session summaries as explicit structured artifacts.