---
name: review-todo
description: Review a TODO plan document for quality before implementation. Use when a TODO-*.md is drafted and needs critique.
disable-model-invocation: true
argument-hint: <TODO-filename.md>
context: fork
agent: Explore
---

# Review TODO Document

Target: `docs/$ARGUMENTS`

## Context to gather

1. Cross-reference against CLAUDE.md rules — check compliance, do not restate them
2. Check for overlap with existing `docs/DESIGN-*.md` and `docs/TODO-*.md` files
3. Spot-check source files referenced in the TODO to verify assumptions about current state

## Evaluate across 3 dimensions

### 1. Motivation & Scope

- Is there a concrete user problem or system gap — not just "improve things"?
- Does any existing DESIGN doc already cover this? Does another TODO overlap?
- Is there a clear MVP boundary with post-MVP items separated?
- Flag scope creep: speculative features, unnecessary config knobs, premature abstractions

### 2. Conformance & Completeness

- Does every code sketch, tool signature, config pattern, and test approach comply with CLAUDE.md?
- Are required sections present and proportional to the feature's complexity?
  - Small refactors need: goal, scope, implementation steps, acceptance criteria
  - Multi-phase features also need: peer survey, design contracts, config, error handling, security, file checklist
- For features that exist in peer systems (codex, gemini-cli, opencode, claude-code, aider): are at least 2 reference implementations surveyed with converged patterns synthesized?

### 3. Implementability & Risks

- Could someone execute this without making unguided design decisions?
- Are contracts (signatures, return shapes, constants) fully specified where the TODO introduces them?
- Does the implementation plan cover all affected files?
- Are error paths, security concerns, breaking changes, and migration needs addressed?
- Flag stale references to files, functions, or config fields that no longer exist

## Output

```markdown
# Review: $ARGUMENTS

**Verdict**: Pass | Minor issues | Needs revision

| Dimension | Rating | Key finding |
|---|---|---|
| Motivation & Scope | ... | ... |
| Conformance & Completeness | ... | ... |
| Implementability & Risks | ... | ... |

## Findings

(Group by dimension. Skip dimensions that fully pass. Be specific — cite sections/lines, not vague impressions.)

## Recommended Actions

1. Highest priority fix
2. ...

## Strengths

- What the document does well
```

Scale depth to document scope — a 2-file refactor gets a tight review, a multi-phase feature gets thorough treatment.
