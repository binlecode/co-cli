# RESEARCH: Pydantic-AI vs Pi-Mono
_Date: 2026-03-16_

Status: framework-pattern reference
Aspect: framework comparison
Pydantic-AI patterns: dependency injection, typed outputs, streaming FSMs, workflow ownership

This technical review provides a deep architectural comparison between **Pydantic-AI** and **pi-mono**, two prominent 2025/2026-era agentic SDKs that solve the "autonomous assistant" problem from radically different angles. 

While **Pydantic-AI** is a robust, type-driven Python framework focused on building production-grade GenAI applications (the "FastAPI for GenAI"), **pi-mono** is an aggressively extensible, minimalist TypeScript terminal harness optimized for personal workflows and local operation.

---

## 1. Design Philosophy & Ethos

### Pydantic-AI: "FastAPI for GenAI" (Type-Safe & Production-Ready)
- **Constraint-Driven:** Built by the Pydantic team, it heavily relies on Python type hints, Pydantic schemas, and data validation to guarantee structured outputs. It aims for a Rust-like "if it compiles, it works" developer experience.
- **Batteries-Included but Modular:** It ships with rich out-of-the-box features tailored for complex systems: `pydantic-graph` for finite state machine workflows, durable execution (to survive transient errors/restarts), human-in-the-loop tool approval, and first-class observability via Pydantic Logfire.
- **Agent as a Function:** Agents are treated conceptually like FastAPI routers. They take typed dependencies (`deps_type`) and return typed outputs (`output_type`), making them highly reusable and composable.

### Pi-Mono: "Minimalist Harness" (Aggressively Extensible)
- **Workflow-Agnostic Minimalism:** Rejects "baked-in" bloat. It explicitly avoids built-in MCP support, sub-agents, plan modes, and to-do systems. Instead, it provides a stable runtime and expects users to adapt the tool to their workflow via plugins.
- **Extensibility First:** Everything is delegated to explicitly loaded "Extensions" (TypeScript logic), "Skills" (Markdown-based Agent Skills standard), "Prompt Templates", and "Themes".
- **Terminal & Session Focus:** Designed primarily as a local CLI/TUI (`pi-coding-agent`), focusing on rich developer UX (e.g., in-place session tree navigation, interruption handling, and compaction) rather than massive multi-agent enterprise deployments.

---

## 2. Core Architecture & Functional Modules

### Pydantic-AI Architecture
- **Agent Container (`Agent[Deps, Output]`):** The core abstraction holding system instructions, registered tools, LLM model settings, and execution bounds (usage limits).
- **Dependency Injection (`RunContext`):** Context is explicitly injected into tools and dynamic prompts. Dependencies (e.g., DB connections) are injected at runtime, making testing and modularity robust.
- **Pydantic-Graph:** A strictly typed finite state machine (FSM) library. Nodes are defined as Python dataclasses, and edges are defined purely by their `run` method return annotations (e.g., `-> NodeB | End`).
- **Observability Layer:** Native OpenTelemetry integration via Logfire, recording granular traces of LLM steps, tool invocations, and database queries automatically.

### Pi-Mono Architecture
- **Monorepo Packages:** Separates concerns cleanly across `@mariozechner/pi-ai` (unified LLM API), `pi-agent-core` (stateful event loop), and `pi-coding-agent` (CLI application).
- **Flexible Message Pipeline:** Relies on a generic `AgentMessage` type that can be extended via TypeScript declaration merging. The execution pipeline strictly enforces context translation:
  `AgentMessage[] -> transformContext() -> convertToLlm() -> LLM`
- **Session Trees:** State is stored in `.jsonl` files where each message points to a `parentId`. This implicitly forms a tree, allowing infinite branching without duplicating files (e.g., the `/fork` and `/tree` UI commands).

---

## 3. Workflow & Event Patterns

### Pydantic-AI: Synchronous & Streaming FSMs
- **Execution Modes:** Exposes varied execution modes (`run_sync`, `run`, `run_stream`, `run_stream_events`).
- **Graph Iteration (`agent.iter()`):** Developers can manually drive the FSM node-by-node, injecting logic between the `ModelRequestNode`, `CallToolsNode`, and `End` node.
- **Durable Workflows:** Built to support background processing and resuming workflows, aligning with enterprise app requirements.

### Pi-Mono: Event-Driven & Interruptible 
- **Reactive Turn-Based Loop:** Emits fine-grained events (`turn_start`, `message_update`, `tool_execution_start`) ideal for driving reactive user interfaces (CLI/Web UIs).
- **Steering & Follow-up Queues:** Explicitly tackles the "user interrupts the agent" problem. 
  - *Steering messages:* Sent while tools are running to interrupt execution gracefully.
  - *Follow-up messages:* Queued and executed after the agent completes its current task.
- **Context Compaction:** Implements lossy context compaction (summarizing old messages) to manage token limits while maintaining the immutable JSONL history tree underneath.

---

## 4. Code Implementation & Idiomatic Best Practices

### Idiomatic Pydantic-AI Usage
Best practice in Pydantic-AI dictates strict typing and dependency isolation. 

```python
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

class UserDeps(BaseModel):
    db: DatabaseConnection

class OutputSchema(BaseModel):
    summary: str
    confidence: float

# Generic types ensure static type checkers catch errors
agent = Agent('openai:gpt-4o', deps_type=UserDeps, output_type=OutputSchema)

@agent.tool
async def fetch_data(ctx: RunContext[UserDeps], query: str) -> str:
    # Dependency injection used idiomatically
    return await ctx.deps.db.execute(query)
```

**Key Patterns:** 
- Pass dependencies cleanly instead of relying on global state.
- Let Pydantic handle output retries (Reflection) automatically if the LLM output violates the `OutputSchema`.

### Idiomatic Pi-Mono Usage
Best practice in pi-mono involves keeping the core agent logic untouched and intercepting behavior via the Extension API and Context Transformers.

```typescript
import { ExtensionAPI } from "@mariozechner/pi-coding-agent";

export default function myExtension(pi: ExtensionAPI) {
  // Add tools without touching the core runtime
  pi.registerTool({
    name: "deploy",
    execute: async (callId, args, signal, onUpdate) => { ... }
  });

  // Intercept events for custom workflows
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "bash" && event.args.includes("rm -rf")) {
      // Custom permission gate
    }
  });
}
```

**Key Patterns:**
- Use "Agent Skills" (Markdown files in `.pi/skills/`) to guide the LLM instead of massive system prompts.
- Extend `AgentMessage` to support UI-only states (e.g., custom widgets) and use `convertToLlm()` to strip them out before passing context to the LLM.

---

## 5. Conclusion & Implication for `co`

Both frameworks reflect a maturation past early 2024 "langchain-style" wrappers, but represent different product destinies:

- **Pydantic-AI** proves that for complex, automated application logic, treating LLMs like strongly-typed functional endpoints embedded in an FSM (Graph) is the most reliable pattern. It is the template for "Agent-as-a-Service" backends.
- **Pi-Mono** proves that for local, user-facing operators, extreme extensibility, stateful session trees, and robust async queueing (steering/follow-up) are more valuable than rigid built-in orchestration.

**Signals for `co`:**
1. **From Pydantic-AI:** `co`'s reliance on Pydantic is validated. The `RunContext` dependency injection model is superior for managing state in tools compared to global singletons. We should adopt strict `deps_type` boundaries in our sub-agents.
2. **From pi-mono:** `co`'s Markdown-based "skills" overlay is heavily aligned with pi-mono's design. `co` could greatly benefit from pi-mono's **Steering and Follow-up Queues** (handling mid-task user interruptions cleanly) and its **JSONL Session Tree** model, which elegantly solves the "rollback" problem without destroying historical context.
