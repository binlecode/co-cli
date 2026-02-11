# REVIEW: Gemini CLI Prompt System Architecture

## Table of Contents
1. Architecture Overview
2. Prompt Assembly Flow
3. Prompt Structure & Modularization
4. Dynamic Composition System
5. Design Principles & Innovations
6. Key Takeaways for co-cli
7. Comparison: Gemini CLI vs co-cli
8. Recommended Prompt Architecture for co-cli
9. Critical Gap Analysis: Fact Verification & Contradiction Handling
10. Final Assessment
11. Appendix: Complete File Listing

## Architecture Overview

### High-Level Design

Gemini CLI primarily composes prompt behavior through conditional sections inside a single prompt generator (`snippets.ts`). Instead of selecting many markdown files, it toggles prompt blocks based on runtime flags.

Core dimensions include:

- interactive vs autonomous behavior
- Gemini 3 specific rules
- sandbox context
- git repository context
- plan mode enablement
- skills/sub-agent support

### Directory Structure

```text
gemini-cli/packages/core/src/
├── prompts/
│   ├── snippets.ts
│   ├── snippets.legacy.ts
│   ├── prompt-registry.ts
│   └── promptProvider.ts
├── agents/
│   ├── codebase-investigator.ts
│   ├── cli-help-agent.ts
│   └── generalist-agent.ts
├── routing/strategies/
├── services/
└── utils/
```

Primary architecture center: `snippets.ts`.

## Prompt Assembly Flow

### Step 1: Runtime Inputs

Common runtime flags include:

- `interactive`
- `gemini3`
- `sandbox` (`macos | container | none`)
- `gitRepo`
- `planMode.enabled`
- `skills`
- `codebaseInvestigator`
- optional workflow hints (todos, shell efficiency)

### Step 2: Content Selection

`getSystemPrompt(context)` appends conditional sections:

- preamble variant (interactive/autonomous)
- always-on core mandates
- Gemini 3 behavior clauses
- workflow and operational guidance
- sandbox/git/plan-mode sections
- user memory/context block

### Step 3: Prompt Assembly

All selected sections are concatenated into a single final system prompt string. Composition logic is centralized, so behavior differences come from conditionals, not file swapping.

### Step 4: Runtime Behavior

A key behavior fork is directive vs inquiry classification:

- directive: explicit action request, file-modifying flow allowed
- inquiry: analysis-only default, no modifications

### Example Scenarios

- “Why does API return 500?” -> inquiry path, research/explain only.
- “Fix the 500 error” -> directive path, implementation and validation flow.
- Ambiguous phrasing defaults to inquiry.

## Prompt Structure & Modularization

### 1. Main Prompt Generator

`snippets.ts` contains most of the active system prompt logic.

### 2. Core Sections

Common section families in generated prompt output:

- preamble/identity
- core mandates (security, engineering standards)
- primary workflows
- operational guidelines
- sandbox notice
- git workflow rules
- plan mode protocol
- contextual user memory

### 3. Legacy Path

`snippets.legacy.ts` supports non-Gemini-3 behavior variants.

### 4. Supporting Prompt Assets

Prompt-related assets outside main prompt generation include:

- specialized agent prompts
- routing/classifier prompts
- service prompts (loop detection, summaries, edit correction)

## Dynamic Composition System

### Composition Pseudocode

```typescript
function getSystemPrompt(ctx) {
  let out = '';

  out += ctx.interactive ? interactivePreamble() : autonomousPreamble();
  out += coreMandates();

  if (ctx.gemini3) out += explainBeforeActing();
  if (ctx.skills) out += skillGuidance();

  out += primaryWorkflows(ctx);
  out += operationalGuidelines(ctx);

  if (ctx.sandbox) out += sandboxNotice(ctx.sandbox);
  if (ctx.gitRepo) out += gitRules(ctx);
  if (ctx.planMode?.enabled) out += planModeRules(ctx.planMode);
  if (ctx.userMemory) out += memoryContext(ctx.userMemory);

  return out;
}
```

### Configuration Space

Approximate space from documented flags is on the order of hundreds of combinations (~384+), driven by multiple booleans and sandbox/plan variants.

## Design Principles & Innovations

### 1. Directive vs Inquiry Distinction

Gemini CLI explicitly separates analysis requests from execution requests and defaults to inquiry when intent is not explicit.

### 2. Centralized Conditional Composition

A single generator keeps behavior logic in one place.

### 3. Safe Default Intent Handling

Ambiguous prompts resolve to non-mutating behavior.

### 4. Model-Specific Behavior

Gemini 3 adds explicit “explain before acting” style constraints.

### 5. Operational Guardrails

Core mandates include security and process constraints that remain active across configuration variants.

## Key Takeaways for co-cli

### 1. Add Directive vs Inquiry Classification

Default ambiguous requests to research-only behavior.

### 2. Keep Conditional Composition Explicit

Conditionally append prompt sections from runtime context; avoid hidden coupling.

### 3. Require Explain-Before-Acting in Interactive Mode

Improve transparency before tool execution.

### 4. Make Plan Mode a First-Class Prompt Branch

Separate planning and execution semantics clearly.

### 5. Enforce Validation-as-Completion

Treat test/verification as required end-of-task criteria.

### 6. Reserve Memory for Durable User Context

Avoid writing workspace-ephemeral data into long-lived memory.

## Comparison: Gemini CLI vs co-cli

| Dimension | Gemini CLI | co-cli (current) | Recommendation |
|---|---|---|---|
| Prompt architecture | Conditional blocks in one generator | More distributed/mixed | Keep composition explicit and observable |
| Intent policy | Directive vs inquiry | Less explicit | Adopt binary safe-default classifier |
| Plan mode | Explicit branch | Limited | Add formal prompt branch |
| Interactive transparency | Explain-before-acting | Partial | Standardize across sessions |
| Memory boundaries | Documented constraints | Evolving | Add strict memory policy text |

## Recommended Prompt Architecture for co-cli

### Recommended Structure

```text
co_cli/prompts/
├── base/
├── behaviors/
├── modes/
├── safety/
├── contextual/
└── model_overrides/
```

### Composition Contract

- Build one prompt from deterministic ordered sections.
- Keep conditional gates explicit in code.
- Log selected sections for observability/debugging.

## Critical Gap Analysis: Fact Verification & Contradiction Handling

### Gap Discovery

Peer systems commonly specify safe action behavior but under-specify contradiction handling when user statements conflict with tool outputs.

### Findings in Gemini CLI

Gemini CLI provides strong intent safety (directive vs inquiry), but does not define a robust resolution protocol for deterministic factual contradictions.

### Recommended Addition for co-cli

Add explicit contradiction policy:

1. Prefer recent tool output for system/file/API state.
2. Recompute deterministic facts when possible.
3. If contradiction remains, present both values and request source confirmation.
4. Treat user preferences and requirements as user-authoritative, not tool-authoritative.

### Gap Severity

High.

## Final Assessment

### Strengths

- Strong intent boundary (directive vs inquiry)
- Centralized composition model
- Good practical safety defaults for ambiguous requests

### Weaknesses

- Large single prompt generator can become hard to maintain
- Contradiction-handling policy needs explicit upgrade

### Innovation Score: 8.5/10

High for intent-safety model and operational prompt design.

## Appendix: Complete File Listing

### Prompt Categories

- Main system generator: `snippets.ts`
- Legacy system generator: `snippets.legacy.ts`
- Agent prompts: codebase investigator and helpers
- Routing prompts: classifier strategies
- Service prompts: loop detection, summaries, edit correction

### Key Files

- `packages/core/src/prompts/snippets.ts`
- `packages/core/src/prompts/snippets.legacy.ts`
- `packages/core/src/agents/codebase-investigator.ts`
- `packages/core/src/routing/strategies/classifierStrategy.ts`
- `packages/core/src/services/loopDetectionService.ts`
