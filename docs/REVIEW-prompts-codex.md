# REVIEW: Codex Prompt System Architecture

**Repo:** `~/workspace_genai/codex` (Rust/OpenAI)
**Analyzed:** 2026-02-08 | **24 prompt files** | **~2,225 lines**

---

## Architecture

Codex uses a **layered, composable prompt architecture**. Prompts are assembled at runtime from independent template files along 5 orthogonal axes:

```
┌──────────────────────────────────────────────────────────────┐
│                    RUNTIME COMPOSITION                        │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Base Instructions (protocol/prompts/base_instructions/)     │
│         ↓                                                     │
│  + Collaboration Mode (templates/collaboration_mode/)        │
│         ↓                                                     │
│  + Personality (templates/personalities/ or agents/)         │
│         ↓                                                     │
│  + Sandbox Mode (protocol/prompts/permissions/sandbox_mode/) │
│         ↓                                                     │
│  + Approval Policy (protocol/prompts/permissions/approval_policy/) │
│         ↓                                                     │
│  + Model-Specific (templates/model_instructions/)            │
│         ↓                                                     │
│  = FINAL SYSTEM PROMPT                                       │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Configuration space:** `4 modes × 3 personalities × 3 sandboxes × 5 approval × 2 model = 360 configs`

### Directory Structure

```
codex-rs/
├── protocol/src/prompts/
│   ├── base_instructions/
│   │   └── default.md                    # Core agent behavior (~800 lines)
│   └── permissions/
│       ├── approval_policy/               # 5 approval modes
│       │   ├── never.md
│       │   ├── on_failure.md
│       │   ├── on_request.md
│       │   ├── on_request_rule.md         # Most detailed (150 lines)
│       │   └── unless_trusted.md
│       └── sandbox_mode/                  # 3 sandbox configs
│           ├── read_only.md
│           ├── workspace_write.md
│           └── danger_full_access.md
│
└── core/templates/
    ├── collaboration_mode/                # 4 collab modes
    │   ├── default.md
    │   ├── execute.md
    │   ├── pair_programming.md
    │   └── plan.md                        # Most complex (220 lines)
    ├── personalities/                     # 2 personalities
    │   ├── gpt-5.2-codex_pragmatic.md
    │   └── gpt-5.2-codex_friendly.md
    ├── agents/
    │   └── orchestrator.md                # Multi-agent (350 lines)
    ├── model_instructions/
    │   └── gpt-5.2-codex_instructions_template.md
    ├── compact/                           # Compression templates
    │   ├── prompt.md
    │   └── summary_prefix.md
    └── review/                            # Code review (200 lines)
        └── review_prompt.md
```

---

## Prompt Inventory

| Category | Files | Lines | Purpose |
|----------|-------|-------|---------|
| Base Instructions | 1 | ~800 | Core agent behavior |
| Collaboration Modes | 4 | ~430 | Behavior style |
| Personalities | 3 | ~160 | Tone/values |
| Approval Policies | 5 | ~350 | Permission model |
| Sandbox Modes | 3 | ~60 | Filesystem access |
| Model Instructions | 1 | ~150 | GPT-5 overrides |
| Compact/Compression | 2 | ~25 | Context handoff |
| Review Templates | 4 | ~250 | Code review |
| **TOTAL** | **24** | **~2,225** | |

---

## Key Prompts (Verbatim)

### Base Instructions (Foundation)

**File:** `protocol/src/prompts/base_instructions/default.md`

Core sections: Identity & Capabilities → Personality Defaults → AGENTS.md Spec → Responsiveness → Planning → Task Execution → Validating Work → Presenting Work → Shell Commands.

Key preamble messages spec:
```
Before making tool calls, send a brief preamble to the user explaining what
you're about to do. Follow these principles:
- Logically group related actions
- Keep it concise: 8-12 words for quick updates
- Build on prior context: create a sense of momentum and clarity
- Keep your tone light, friendly and curious
- Exception: Avoid adding a preamble for every trivial read

Examples:
- "I've explored the repo; now checking the API route definitions."
- "Ok cool, so I've wrapped my head around the repo. Now digging into the API routes."
- "Finished poking at the DB gateway. I will now chase down error handling."
```

Key task execution rules:
```
- Fix the problem at the root cause rather than applying surface-level patches
- Avoid unneeded complexity
- Do not attempt to fix unrelated bugs or broken tests
- Keep changes consistent with the style of the existing codebase
- NEVER add copyright or license headers unless specifically requested
- Do not git commit your changes unless explicitly requested
```

### Plan Mode (Most Complex — 220 lines)

**File:** `templates/collaboration_mode/plan.md`

```
# Plan Mode (Conversational)

You work in 3 phases, and you should *chat your way* to a great plan before
finalizing it. A great plan is very detailed so that it can be handed to
another engineer or agent to be implemented right away. It must be
**decision complete**, where the implementer does not need to make any decisions.

## Mode rules (strict)

You are in **Plan Mode** until a developer message explicitly ends it.
Plan Mode is not changed by user intent, tone, or imperative language.

## Execution vs. mutation in Plan Mode

You may explore and execute **non-mutating** actions.
You must not perform **mutating** actions.

## PHASE 1 — Ground in the environment (explore first, ask second)

Begin by grounding yourself in the actual environment. Eliminate unknowns
by discovering facts, not by asking the user.

Before asking the user any question, perform at least one targeted
non-mutating exploration pass.

## PHASE 2 — Intent chat (what they actually want)

Keep asking until you can clearly state: goal + success criteria, audience,
in/out of scope, constraints, current state, and key preferences/tradeoffs.

## PHASE 3 — Implementation chat (what/how we'll build)

Keep asking until the spec is decision complete: approach, interfaces,
data flow, edge cases/failure modes, testing + acceptance criteria.

## Two kinds of unknowns (treat differently)

1. **Discoverable facts** (repo/system truth): explore first.
   - Before asking, run targeted searches. Ask only if multiple plausible
     candidates exist. If asking, present concrete candidates + recommend one.

2. **Preferences/tradeoffs** (not discoverable): ask early.
   - Provide 2-4 mutually exclusive options + a recommended default.
   - If unanswered, proceed with recommended option and record as assumption.
```

### Execute Mode

**File:** `templates/collaboration_mode/execute.md`

```
## Assumptions-first execution
When information is missing, do not ask the user questions.
Instead:
- Make a sensible assumption.
- Clearly state the assumption in the final message (briefly).
- Continue executing.

## Execution principles
*Think out loud.* Share reasoning when it helps the user evaluate tradeoffs.
*Use reasonable assumptions.* Suggest a sensible choice instead of open-ended questions.
*Think ahead.* What else might the user need?
*Be mindful of time.* Spend only a few seconds on most turns, no more than
60 seconds when doing research.
```

### Personalities (Swappable Tone Layer)

**Pragmatic:**
```
You are a deeply pragmatic, effective software engineer. You take engineering
quality seriously, and collaboration is a kind of quiet joy.

Values: Clarity, Pragmatism, Rigor.
Interaction Style: Concise, respectful, focused on the task.
Escalation: You may challenge the user to raise their technical bar, but
you never patronize.
```

**Friendly:**
```
You optimize for team morale and being a supportive teammate as much as
code quality. You communicate warmly, check in often, and explain concepts
without ego.

Tone: Warm, encouraging, conversational. Use "we" and "let's"; affirm progress.
You are NEVER curt or dismissive.
```

### Approval Policy: on_request_rule (Most Detailed — 150 lines)

Key innovation — **prefix rules** for categorical approval:
```
Request categorical prefixes for similar future commands.
Good: ["pytest"], ["cargo", "test"]
Banned: ["python3"], ["rm"], any heredoc commands
```

### Review System (Structured JSON Output)

```json
{
  "findings": [{
    "title": "<≤ 80 chars, imperative>",
    "body": "<valid Markdown>",
    "confidence_score": "<float 0.0-1.0>",
    "priority": "<int 0-3>",
    "code_location": {
      "absolute_file_path": "<file path>",
      "line_range": {"start": "<int>", "end": "<int>"}
    }
  }],
  "overall_correctness": "patch is correct | patch is incorrect",
  "overall_confidence_score": "<float 0.0-1.0>"
}
```

Priority levels: `[P0]` blocking → `[P1]` urgent → `[P2]` normal → `[P3]` low

---

## Innovations

### 1. Separation of Concerns (5 Orthogonal Axes)

Each prompt file addresses ONE concern. Change personality without rewriting approval logic. 24 files, zero duplication.

### 2. Two Kinds of Unknowns

Discoverable facts → explore first, ask only if multiple candidates.
Preferences/tradeoffs → ask early, provide options + recommended default.

**Impact:** Reduces unnecessary user interruptions while still gathering critical info.

### 3. Explicit Mode Boundaries

"Plan Mode is not changed by user intent, tone, or imperative language." — prevents accidental mode exits.

### 4. Personality as Swappable Module

Same instructions, different emotional register. Friendly uses "we", pragmatic uses "you". Personality is orthogonal to capability.

### 5. Preamble Messages Spec

8-12 word updates before tool calls with concrete examples. Creates sense of momentum for the user.

### 6. Progressive Layering

Base → Mode → Personality → Permissions → Model. Each layer adds constraints without repeating prior content.

### 7. Template Variables for Runtime Injection

`{{ personality }}` placeholder in model instructions allows late binding of personality content.

### 8. Confidence Scoring in Reviews

Float 0.0-1.0 confidence with integer 0-3 priority. Enables threshold filtering ("only show findings ≥ 0.75").

### 9. Orchestrator Agent (Multi-Agent Mode)

Clear separation between single-agent and orchestrator modes. Sub-agents parallelize work; orchestrator coordinates and waits.

### 10. Examples as First-Class Content

Almost every rule includes 2-8 examples. Models learn from concrete patterns, not just abstract rules.

---

## Content Layout Patterns

| Pattern | Usage | Example |
|---------|-------|---------|
| Hierarchical headers | Structure | `# Topic` → `## Subsection` → `### Detail` |
| Bold-label bullets | Rules | `- **Bold Label:** Description` |
| Inline examples | Grounding | 8 preamble message examples |
| XML tags | Structured output | `<proposed_plan>`, `<user_action>` |
| Template variables | Runtime injection | `{{ personality }}` |

---

## Key Takeaways for co-cli

1. **Layered composition** — break prompts into orthogonal dimensions (mode × personality × permissions × model)
2. **Plan mode** — strict 3-phase non-mutating planning with "decision complete" finalization
3. **Two kinds of unknowns** — explore facts first, ask preferences early
4. **Personality as config** — separate tone from instructions, make it swappable
5. **Preamble messages** — 8-12 word updates before tool calls
6. **Examples everywhere** — every abstract rule grounded in 2-4 concrete examples
7. **Confidence-scored reviews** — numeric confidence + priority for filtering
8. **Template variables** — `{{ var }}` for late-binding composition

---

**Source:** `~/workspace_genai/codex` — all prompts traceable from directory structure above
