# REVIEW: Claude Code Prompt System Architecture

## Table of Contents
1. Architecture Overview
2. Prompt Assembly Flow
3. Prompt Structure & Modularization
4. Dynamic Composition System
5. Design Principles & Innovations
6. Key Takeaways for co-cli
7. Comparison: Claude Code vs co-cli
8. Recommended Prompt Architecture for co-cli
9. Critical Gap Analysis: Fact Verification & Contradiction Handling
10. Final Assessment
11. Appendix: Complete File Listing

## Architecture Overview

### High-Level Design

Claude Code uses a plugin-oriented, event-driven prompt architecture with three prompt primitives:

- agents (autonomous subprocesses)
- commands (slash-command workflows)
- skills (knowledge packs)

Composition occurs at runtime through lifecycle hooks, not only through static file concatenation.

### Directory Structure

```text
claude-code/
├── plugins/
│   ├── feature-dev/
│   │   ├── agents/
│   │   ├── commands/
│   │   └── hooks/
│   ├── plugin-dev/
│   │   ├── agents/
│   │   └── skills/
│   ├── security-guidance/
│   │   └── hooks/
│   └── ...
├── .claude-plugin/
│   └── plugins/
└── .claude/
```

Documented scale in the review corpus is large (dozens of prompt/hook/skill assets).

## Prompt Assembly Flow

### Step 1: Runtime Inputs

User actions trigger one of three primitives:

- `/command` for workflows
- task/context match for agent activation
- question/context match for skill activation

### Step 2: Event and Content Selection

Hooks and prompt assets are selected by lifecycle event and trigger context:

1. `SessionStart`
2. `UserPromptSubmit`
3. Primitive selection (agent/command/skill)
4. `PreToolUse`
5. tool execution
6. `PostToolUse`
7. response rendering
8. `Stop`

### Step 3: Prompt Assembly

Selected primitive prompt files (frontmatter + markdown body) are loaded and combined with hook-injected context.

### Step 4: Runtime Behavior

Workflow behavior can include multi-phase command orchestration and parallel agent execution, with security and policy checks at tool boundaries.

### Example Scenarios

- `/feature-dev "add JWT auth"`:
  - command prompt drives phases
  - exploration and review agents run in parallel
  - every write/edit call passes through `PreToolUse` security hooks
  - post-tool hooks handle logging/telemetry transforms

## Prompt Structure & Modularization

### 1. Agents

Prompt files with frontmatter metadata (`name`, `description`, `model`, `tools`) and role/process/output instructions.

### 2. Commands

Slash-command markdown workflows with explicit phases, success criteria, and output templates.

### 3. Skills

`SKILL.md` as top-level guide plus optional `references/`, `examples/`, and `scripts/` for progressive disclosure.

### 4. Hook Layer

Lifecycle hook handlers provide dynamic policy and context injection:

- style/context injection at session start
- input checks at prompt submit
- policy/security gating before tool calls
- logging/transform after tool calls

### 5. Plugin Packaging

Prompts and hooks are grouped as reusable plugin units with manifests and versioning.

## Dynamic Composition System

### Composition Pseudocode

```typescript
function handleTurn(input, context) {
  runHooks('SessionStart', context);
  runHooks('UserPromptSubmit', { input, context });

  const primitive = selectPrimitive(input, context);
  const prompt = loadPrimitivePrompt(primitive);

  const toolPlan = planToolCalls(prompt, input, context);
  for (const call of toolPlan) {
    const pre = runHooks('PreToolUse', call);
    if (pre.blocked) continue;

    const result = executeTool(call);
    runHooks('PostToolUse', { call, result });
  }

  runHooks('Stop', context);
  return renderResponse();
}
```

### Configuration Space

Behavior varies by:

- selected primitive and plugin
- active hooks and local rules
- model selection per agent
- command phase progression

This creates high flexibility through plugin composition rather than fixed template stacks.

## Design Principles & Innovations

### 1. Three Prompt Primitives

Clear task taxonomy with distinct execution models.

### 2. Event-Driven Composition

Lifecycle hooks allow policy and context to be injected exactly where needed.

### 3. Plugin-Native Extensibility

Prompts are packaged/distributed as plugins instead of forcing core-repo edits.

### 4. Multi-Agent Orchestration

Commands can launch multiple specialist agents and aggregate outputs.

### 5. Security as a Hooked Layer

Security policy is centralized at tool boundary events, reducing prompt duplication.

## Key Takeaways for co-cli

### 1. Introduce Prompt Primitives

Separate workflows into agent, command, and skill classes.

### 2. Add Lifecycle Hooks

Create hook events for session start, pre-tool, post-tool, and stop.

### 3. Move Security Checks to Pre-Tool Hooks

Centralize risky pattern detection (command injection, path traversal, etc.).

### 4. Support Progressive Skill Disclosure

Keep top-level skill docs concise and load deep references only as needed.

### 5. Add Multi-Agent Workflow Steps

Allow command phases to run specialist agents in parallel and consolidate findings.

### 6. Prepare Plugin-Like Prompt Packaging

Even before full marketplace support, adopt plugin-shaped folder boundaries.

## Comparison: Claude Code vs co-cli

| Dimension | Claude Code | co-cli (current) | Recommendation |
|---|---|---|---|
| Prompt taxonomy | Agents/commands/skills | Less explicit | Adopt primitive taxonomy |
| Composition model | Event-driven hooks | Mostly static + runtime logic | Add hook-driven lifecycle |
| Extensibility | Plugin-oriented | Core-owned prompts | Introduce plugin-shaped modules |
| Security policy | PreToolUse hook layer | Mixed inline checks | Centralize at tool boundary |
| Orchestration | Parallel specialist agents | Limited | Add multi-agent command phases |

## Recommended Prompt Architecture for co-cli

### Recommended Structure

```text
co_cli/
├── prompts/
│   ├── agents/
│   ├── commands/
│   ├── skills/
│   └── base/
├── hooks/
│   ├── registry.py
│   ├── pre_tool_security.py
│   └── post_tool_telemetry.py
└── orchestration/
    ├── agent_selection.py
    └── workflow_runner.py
```

### Composition Contract

- Prompt primitive selection happens before tool planning.
- Hook execution wraps every tool call.
- Command phases can request parallel agent subtasks.
- Skills load main guide first, deep references on demand.

## Critical Gap Analysis: Fact Verification & Contradiction Handling

### Gap Discovery

Prompt systems reviewed across peers are strong on execution safety but weaker on factual contradiction handling.

### Findings in Claude Code

Claude Code has excellent hook architecture for policy/security control, but no default contradiction-resolution protocol for deterministic facts.

### Recommended Addition for co-cli

Use the hook model to enforce contradiction checks:

1. Detect conflicts between recent tool outputs and user corrections.
2. Recompute deterministic facts when feasible.
3. If unresolved, surface both claims and request confirmation.
4. Treat user preferences as user-authoritative; deterministic state as tool-authoritative unless verified otherwise.

### Gap Severity

High.

## Final Assessment

### Strengths

- Strong primitive taxonomy
- Event-driven policy injection
- Excellent extensibility model

### Weaknesses

- High operational complexity
- Fact-verification contradiction policy not first-class

### Innovation Score: 9/10

High for lifecycle hook architecture and plugin-native prompt packaging.

## Appendix: Complete File Listing

### Prompt Categories

- Agents
- Commands
- Skills (`SKILL.md` plus references/examples/scripts)
- Hooks
- Plugin manifests

### Key Files

- `plugins/feature-dev/commands/feature-dev.md`
- `plugins/feature-dev/agents/code-explorer.md`
- `plugins/plugin-dev/skills/agent-development/SKILL.md`
- `plugins/security-guidance/hooks/security_reminder_hook.py`
- `plugins/*/hooks/hooks.json`
