# REVIEW: Codex (OpenAI) Prompt System Architecture

## Table of Contents
1. Architecture Overview
2. Prompt Assembly Flow
3. Prompt Structure & Modularization
4. Dynamic Composition System
5. Design Principles & Innovations
6. Key Takeaways for co-cli
7. Comparison: Codex vs co-cli
8. Recommended Prompt Architecture for co-cli
9. Critical Gap Analysis: Fact Verification & Contradiction Handling
10. Final Assessment
11. Appendix: Complete File Listing

## Architecture Overview

### High-Level Design

Codex uses a layered prompt composition model. The final system prompt is assembled at runtime from independent instruction files selected by configuration:

- Base instructions
- Collaboration mode
- Personality
- Sandbox policy
- Approval policy
- Model-specific overrides

This creates orthogonal behavior controls without duplicating the full prompt for each variant.

### Directory Structure

```text
codex-rs/
├── protocol/src/prompts/
│   ├── base_instructions/
│   │   └── default.md
│   └── permissions/
│       ├── approval_policy/
│       │   ├── never.md
│       │   ├── on_failure.md
│       │   ├── on_request.md
│       │   ├── on_request_rule.md
│       │   └── unless_trusted.md
│       └── sandbox_mode/
│           ├── read_only.md
│           ├── workspace_write.md
│           └── danger_full_access.md
└── core/templates/
    ├── collaboration_mode/
    ├── personalities/
    ├── agents/
    ├── model_instructions/
    ├── compact/
    └── review/
```

Total prompt files: ~24.

## Prompt Assembly Flow

### Step 1: Runtime Inputs

Codex behavior is configured by runtime settings:

- `mode`: `default | execute | pair_programming | plan`
- `personality`: pragmatic or friendly
- `sandbox_mode`: read-only/workspace-write/full-access
- `approval_policy`: never/on-request/on-failure/etc.
- `model`: determines model-specific overlays

### Step 2: Content Selection

The system selects one file per axis (plus always-on base instructions):

- Always load `base_instructions/default.md`
- Load one collaboration mode file
- Load one personality (or orchestrator agent profile)
- Load one sandbox policy file
- Load one approval policy file
- Conditionally load model instructions

### Step 3: Prompt Assembly

Selected files are appended in fixed order to produce one final system prompt. Swapping a single configuration dimension swaps only one instruction component.

### Step 4: Runtime Behavior

The same user request can produce different execution style depending on active configuration:

- `execute + pragmatic + full_access + never`: autonomous, direct, low-friction execution
- `pair + friendly + workspace_write + on_request`: collaborative and approval-driven execution
- `plan + read_only`: exploratory planning with no mutation

### Example Scenarios

- “Fix login bug” in execute mode: immediate edit/test/report loop.
- Same request in pair mode: explain first, ask before risky edits.

## Prompt Structure & Modularization

### 1. Base Instructions

Foundation layer defining tool usage, planning behavior, communication norms, and repo instruction handling (`AGENTS.md` semantics).

### 2. Collaboration Modes

Mode-specific behavior envelopes:

- `default`: balanced behavior
- `execute`: assumptions-first autonomous execution
- `pair_programming`: high collaboration
- `plan`: non-mutating planning workflow

### 3. Personalities

Tone-only overlays decoupled from capability:

- Pragmatic: direct, concise
- Friendly: warm and collaborative

### 4. Permissions

Two independent policy surfaces:

- Sandbox mode (what can execute/access)
- Approval policy (when escalation/approval is required)

### 5. Model-Specific Instructions

Template-based model overlays add provider/model quirks (formatting and behavioral constraints).

### 6. Compact and Review Layers

Separate prompt assets support compression and code-review output patterns without polluting core execution instructions.

## Dynamic Composition System

### Composition Pseudocode

```rust
fn build_system_prompt(config: Config) -> String {
    let mut out = String::new();
    out += load("base_instructions/default.md");
    out += load_mode(config.collaboration_mode);
    out += load_personality_or_orchestrator(config.personality);
    out += load_sandbox(config.sandbox_mode);
    out += load_approval(config.approval_policy);
    out += load_model_overrides_if_needed(config.model);
    out
}
```

### Configuration Space

Approximate combinations:

- 4 collaboration modes
- 3 personality/orchestration variants
- 3 sandbox modes
- 5 approval policies
- 2 model paths

Total: `4 x 3 x 3 x 5 x 2 = 360` compositions.

## Design Principles & Innovations

### 1. Separation of Concerns

Capabilities, tone, and permissions are isolated into independent files.

### 2. Composability Over Monoliths

Small files allow targeted changes and cleaner diffs.

### 3. Explicit Mode Boundaries

Plan mode and execute mode have strict instruction boundaries.

### 4. Unknowns Taxonomy

Codex distinguishes discoverable facts from preference/tradeoff questions.

### 5. Examples as Instruction Anchors

Instruction files include concrete examples that shape model behavior more reliably than abstract rules alone.

## Key Takeaways for co-cli

### 1. Adopt Layered Composition

Break prompt content into base, mode, personality, permissions, and model overlays.

### 2. Formalize Plan Mode

Adopt explicit non-mutating planning state before execution.

### 3. Decouple Tone from Capability

Move tone into dedicated personality prompt files.

### 4. Externalize Approval and Sandbox Policy

Keep policy in dedicated prompt modules instead of mixing with task logic.

### 5. Standardize Progress Preambles

Require short “what I’m doing next” preambles before tool actions.

### 6. Add Structured Review Outputs

Use review-specific templates with severity/confidence to prioritize findings.

## Comparison: Codex vs co-cli

| Dimension | Codex | co-cli (current) | Recommendation |
|---|---|---|---|
| Prompt architecture | Layered modular files | More centralized | Move to explicit layers |
| Collaboration modes | 4 | Limited implicit modes | Add execute/plan/pair variants |
| Personality | Swappable modules | Mostly embedded | Extract personality layer |
| Permissions | Dedicated prompt files | Mixed in runtime/tool flow | Split into policy modules |
| Review templates | First-class | Limited | Add review prompt assets |

## Recommended Prompt Architecture for co-cli

### Recommended Structure

```text
co_cli/prompts/
├── base/
├── modes/
├── personalities/
├── permissions/
├── model_overrides/
└── review/
```

### Composition Contract

- One selected file per axis.
- Stable assembly order.
- No duplicated policy text across layers.
- Model overrides appended last.

## Critical Gap Analysis: Fact Verification & Contradiction Handling

### Gap Discovery

Across peer prompt systems, contradiction handling between tool outputs and user assertions is weakly specified.

### Findings in Codex

Codex has strong process rigor and review provability language, but no explicit runtime protocol for “tool says X, user says Y” contradictions in normal task flow.

### Recommended Addition for co-cli

Add a base rule set:

1. Prefer recent tool output for deterministic state.
2. Independently verify calculable facts (dates, arithmetic, timestamps).
3. If unresolved, show both claims and request confirmation.
4. Never accept deterministic corrections without verification.

### Gap Severity

High.

## Final Assessment

### Strengths

- Strong modular layering and composition discipline
- Clear behavioral boundaries by mode
- Good maintainability and diff quality

### Weaknesses

- Contradiction/fact-verification policy is under-specified
- Complexity grows with many composable axes

### Innovation Score: 9/10

High for composability and operational clarity.

## Appendix: Complete File Listing

### Prompt Categories

- Base instructions: 1 file
- Collaboration modes: 4 files
- Personalities and orchestrator: ~3 files
- Approval policies: 5 files
- Sandbox modes: 3 files
- Model instructions: ~1 file
- Compact/review templates: multiple support files

### Key Files

- `protocol/src/prompts/base_instructions/default.md`
- `protocol/src/prompts/permissions/approval_policy/*.md`
- `protocol/src/prompts/permissions/sandbox_mode/*.md`
- `core/templates/collaboration_mode/*.md`
- `core/templates/personalities/*.md`
- `core/templates/model_instructions/*.md`
