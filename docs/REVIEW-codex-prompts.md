# REVIEW: Codex (OpenAI) Prompt System Architecture

**Repository:** `~/workspace_genai/codex` (Rust)
**Analysis Date:** 2026-02-08

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prompt Structure & Modularization](#prompt-structure--modularization)
3. [Content Layout Patterns](#content-layout-patterns)
4. [Dynamic Composition System](#dynamic-composition-system)
5. [Complete Prompt Inventory](#complete-prompt-inventory)
6. [Design Principles & Innovations](#design-principles--innovations)
7. [Key Takeaways for co-cli](#key-takeaways-for-co-cli)

---

## Architecture Overview

### High-Level Design

Codex uses a **layered, composable prompt architecture** implemented in Rust. The system assembles prompts from multiple independent template files at runtime based on:
- User collaboration mode (default, plan, execute, pair programming)
- Personality selection (pragmatic, friendly)
- Sandbox permissions (read-only, workspace-write, danger-full-access)
- Approval policy (never, on-failure, on-request, unless-trusted)
- Model type (GPT-5 specific vs general instructions)

```
┌─────────────────────────────────────────────────────────────┐
│                    RUNTIME COMPOSITION                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Base Instructions (protocol/prompts/base_instructions/)    │
│         ↓                                                    │
│  + Collaboration Mode (templates/collaboration_mode/)       │
│         ↓                                                    │
│  + Personality (templates/personalities/ or agents/)        │
│         ↓                                                    │
│  + Sandbox Mode (protocol/prompts/permissions/sandbox_mode/)│
│         ↓                                                    │
│  + Approval Policy (protocol/prompts/permissions/approval_policy/)│
│         ↓                                                    │
│  + Model-Specific (templates/model_instructions/)           │
│         ↓                                                    │
│  = FINAL SYSTEM PROMPT                                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
codex-rs/
├── protocol/src/prompts/
│   ├── base_instructions/
│   │   └── default.md                    # Core agent behavior
│   └── permissions/
│       ├── approval_policy/               # 5 approval modes
│       │   ├── never.md
│       │   ├── on_failure.md
│       │   ├── on_request.md
│       │   ├── on_request_rule.md
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
    │   └── plan.md
    ├── personalities/                     # 2 personalities
    │   ├── gpt-5.2-codex_friendly.md
    │   └── gpt-5.2-codex_pragmatic.md
    ├── agents/
    │   └── orchestrator.md                # Orchestrator persona
    ├── model_instructions/
    │   └── gpt-5.2-codex_instructions_template.md  # GPT-5 overrides
    ├── compact/                           # Compression templates
    │   ├── prompt.md
    │   └── summary_prefix.md
    └── review/                            # Code review templates
        ├── exit_success.xml
        ├── exit_interrupted.xml
        ├── history_message_completed.md
        └── history_message_interrupted.md
```

**Total Files:** 24 unique prompt templates

---

## Prompt Structure & Modularization

### 1. Base Instructions (Foundation Layer)

**File:** `protocol/src/prompts/base_instructions/default.md`
**Size:** ~800 lines
**Purpose:** Core agent behavior, tool usage, task execution philosophy

**Key Sections:**
1. **Identity & Capabilities** (lines 1-30)
   - Who Codex is, what it can do
   - Function calls, streaming, planning

2. **Personality Defaults** (lines 32-38)
   - Concise, direct, friendly tone
   - Minimal verbosity

3. **AGENTS.md Spec** (lines 40-60)
   - Repo-specific instruction files
   - Scope inheritance rules
   - Precedence hierarchy

4. **Responsiveness** (lines 62-100)
   - Preamble messages before tool calls
   - 8-12 word updates with examples
   - "I've explored X; now checking Y"

5. **Planning** (lines 102-150)
   - When to use `update_plan` tool
   - High-quality vs low-quality plan examples
   - Avoid single-step or trivial plans

6. **Task Execution** (lines 152-200)
   - "Keep going until resolved"
   - Fix root causes, avoid surface patches
   - Never add copyright headers unless requested

7. **Validating Work** (lines 202-230)
   - Run tests from specific to broad
   - Test philosophy varies by approval mode

8. **Presenting Work** (lines 232-280)
   - Natural teammate updates
   - 10 line brevity default
   - File reference format: `path:line` or `path#Lline`

9. **Shell Commands** (lines 282-285)
   - Prefer `rg` over `grep` for speed

### 2. Collaboration Modes (Behavior Layer)

**Purpose:** Change agent execution style without modifying core instructions

#### `default.md` (40 lines)
```markdown
# Collaboration Mode: Default

You are now in Default mode. Any previous instructions for other modes
are no longer active.

If a decision is necessary and cannot be discovered from local context,
ask the user directly. However, in Default mode you should strongly prefer
executing the user's request rather than stopping to ask questions.
```
**Pattern:** Minimal override, slight bias toward execution over questions

#### `execute.md` (120 lines)
```markdown
# Collaboration Style: Execute

You execute on a well-specified task independently and report progress.
You do not collaborate on decisions. You execute end-to-end.
You make reasonable assumptions when the user hasn't specified something.

## Assumptions-first execution
When information is missing, do not ask the user questions.
Instead:
- Make a sensible assumption.
- Clearly state the assumption in the final message (briefly).
- Continue executing.
```
**Pattern:** Strong autonomous execution, no questions, state assumptions

**Key Innovation:** Group assumptions logically (architecture/frameworks/implementation, features/behavior, design/themes/feel)

#### `pair_programming.md` (50 lines)
```markdown
# Collaboration Style: Pair Programming

## Build together as you go
You treat collaboration as pairing by default. The user is right with you
in the terminal, so avoid taking steps that are too large or take a lot of
time (like running long tests), unless asked for it.
```
**Pattern:** Tight feedback loop, small incremental steps, check alignment before moving forward

#### `plan.md` (220 lines — most complex)
```markdown
# Plan Mode (Conversational)

You work in 3 phases, and you should *chat your way* to a great plan
before finalizing it. A great plan is very detailed so that it can be
handed to another engineer or agent to be implemented right away.
It must be **decision complete**.

## Mode rules (strict)
You are in **Plan Mode** until a developer message explicitly ends it.
Plan Mode is not changed by user intent, tone, or imperative language.

## Execution vs. mutation in Plan Mode
You may explore and execute **non-mutating** actions.
You must not perform **mutating** actions.

### Allowed (non-mutating)
* Reading or searching files, configs, schemas, types, manifests, and docs
* Static analysis, inspection, and repo exploration
* Dry-run style commands when they do not edit repo-tracked files

### Not allowed (mutating)
* Editing or writing files
* Running formatters or linters that rewrite files
* Applying patches, migrations, or codegen
```

**3 Phases:**
1. **Ground in the environment** (explore first, ask second)
   - Eliminate unknowns by discovering facts
   - "Before asking the user any question, perform at least one targeted non-mutating exploration pass"

2. **Intent chat** (what they actually want)
   - Goal + success criteria, audience, in/out of scope, constraints

3. **Implementation chat** (what/how we'll build)
   - Approach, interfaces, data flow, edge cases, testing, rollout

**Two kinds of unknowns:**
1. **Discoverable facts** (repo/system truth): explore first
2. **Preferences/tradeoffs** (not discoverable): ask early

**Finalization:**
- Only output when decision complete
- Wrap in `<proposed_plan>` XML block for special rendering

### 3. Personalities (Tone Layer)

**Purpose:** Separate tone/values from instructions. Switchable without logic changes.

#### `gpt-5.2-codex_pragmatic.md` (50 lines)
```markdown
# Personality

You are a deeply pragmatic, effective software engineer. You take
engineering quality seriously, and collaboration is a kind of quiet joy.

## Values
- Clarity: You communicate reasoning explicitly and concretely.
- Pragmatism: You keep the end goal and momentum in mind.
- Rigor: You expect technical arguments to be coherent and defensible.

## Interaction Style
Concise, respectful, focused on the task. Great work and smart decisions
are acknowledged, while avoiding cheerleading.

## Escalation
You may challenge the user to raise their technical bar, but you never
patronize.
```

#### `gpt-5.2-codex_friendly.md` (60 lines)
```markdown
# Personality

You optimize for team morale and being a supportive teammate as much as
code quality. You communicate warmly, check in often, and explain concepts
without ego.

## Values
* Empathy: adjusting explanations, pacing, and tone to maximize understanding
* Collaboration: inviting input, synthesizing perspectives
* Ownership: Takes responsibility not just for code, but for whether
  teammates are unblocked

## Tone & User Experience
Warm, encouraging, conversational. Use "we" and "let's"; affirm progress.
You are NEVER curt or dismissive.
```

**Design Insight:** Same instructions, different emotional register. Friendly uses "we", pragmatic uses "you".

### 4. Orchestrator Agent (Multi-Agent Layer)

**File:** `templates/agents/orchestrator.md` (350 lines)
**Purpose:** When using sub-agents, orchestration-specific behavior

**Key Sections:**
- **Collaboration posture:** Treat user as equal co-builder
- **User Updates Spec:** Short 1-2 sentence updates at meaningful insights
- **Code style:** Explicit > clever, prefer verbose readable code
- **Reviews:** Bug/risk first, ordered by severity
- **Using GIT:** NEVER revert user changes, avoid `git reset --hard`
- **Sub-agents:**
  - "Sub-agents are there to make you go fast and time is a big constraint"
  - Prefer multiple sub-agents to parallelize work
  - Wait for sub-agents before yielding
  - When sub-agents are working, your only role becomes coordinator

**Innovation:** Clear separation between single-agent mode and orchestrator mode

### 5. Sandbox & Approval Policies (Permission Layer)

#### Approval Policies (5 files)

**`never.md`**
```markdown
`approval_policy` is `never`: This is a non-interactive mode where you may
NEVER ask the user for approval to run commands. Instead, you must always
persist and work around constraints to solve the task. If this mode is
paired with `danger-full-access`, take advantage of it to deliver the best
outcome.
```

**`on_failure.md`**
```markdown
`approval_policy` is `on-failure`: The harness will allow all commands to
run in the sandbox, and failures will be escalated to the user for approval
to run again without the sandbox.
```

**`on_request.md`**
```markdown
`approval_policy` is `on-request`: Commands will be run in the sandbox by
default, and you can specify in your tool call if you want to escalate a
command to run without sandboxing.
```

**`on_request_rule.md`** (150 lines — most detailed)
- Explains command segmentation at shell control operators (`|`, `&&`, `;`)
- How to request escalation with `sandbox_permissions` and `justification` parameters
- When to request: GUI apps, network access, potentially destructive actions
- **`prefix_rule` guidance**: Request categorical prefixes for similar future commands
  - Good: `["pytest"]`, `["cargo", "test"]`
  - Banned: `["python3"]`, `["rm"]`, any heredoc commands

**`unless_trusted.md`**
```markdown
`approval_policy` is `unless-trusted`: The harness will escalate most
commands for user approval, apart from a limited allowlist of safe "read"
commands.
```

#### Sandbox Modes (3 files)

**`read_only.md`**
```markdown
`sandbox_mode` is `read-only`: The sandbox only permits reading files.
Network access is {network_access}.
```

**`workspace_write.md`**
```markdown
`sandbox_mode` is `workspace-write`: The sandbox permits reading files,
and editing files in `cwd` and `writable_roots`. Editing files in other
directories requires approval. Network access is {network_access}.
```

**`danger_full_access.md`**
```markdown
`sandbox_mode` is `danger-full-access`: No filesystem sandboxing - all
commands are permitted. Network access is {network_access}.
```

**Pattern:** Short declarative statements, runtime variable interpolation (`{network_access}`)

### 6. Model-Specific Instructions

**File:** `templates/model_instructions/gpt-5.2-codex_instructions_template.md` (150 lines)

**Purpose:** Override base instructions for GPT-5 specific capabilities

**Key Overrides:**
- `{{ personality }}` — placeholder for personality injection
- Markdown formatting rules (no nested bullets, headers wrapped in `**...**`)
- File reference format enforcement
- "Don't use emojis" (explicit for GPT-5)
- Frontend design tasks: "avoid collapsing into AI slop"
  - No default fonts (Inter, Roboto, Arial)
  - No purple-on-white defaults
  - Use expressive fonts, gradients, motion

**Design Pattern:** Template variables (`{{ personality }}`) allow runtime composition

### 7. Compact Mode (Compression Layer)

**File:** `templates/compact/prompt.md` (15 lines)
```markdown
You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff
summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue
```

**File:** `templates/compact/summary_prefix.md` (8 lines)
```markdown
Another language model started to solve this problem and produced a summary
of its thinking process. Use this to build on the work that has already
been done and avoid duplicating work.
```

**Pattern:** LLM-to-LLM handoff, compress context window for continuation

### 8. Review Templates (Code Review Layer)

**File:** `core/review_prompt.md` (200 lines)

**Structure:**
1. **Bug Definition Guidelines** (lines 1-80)
   - 8 criteria for valid bugs
   - "It meaningfully impacts accuracy, performance, security, or maintainability"
   - "The bug was introduced in the commit (pre-existing bugs should not be flagged)"

2. **Comment Guidelines** (lines 82-120)
   - Clear about why it's a bug
   - Communicate severity appropriately
   - At most 1 paragraph body
   - No code chunks longer than 3 lines

3. **Priority Tagging** (lines 122-140)
   - `[P0]` — Drop everything to fix (blocking release)
   - `[P1]` — Urgent (next cycle)
   - `[P2]` — Normal (eventually)
   - `[P3]` — Low (nice to have)

4. **Output Schema** (lines 142-200)
```json
{
  "findings": [
    {
      "title": "<≤ 80 chars, imperative>",
      "body": "<valid Markdown>",
      "confidence_score": <float 0.0-1.0>,
      "priority": <int 0-3>,
      "code_location": {
        "absolute_file_path": "<file path>",
        "line_range": {"start": <int>, "end": <int>}
      }
    }
  ],
  "overall_correctness": "patch is correct" | "patch is incorrect",
  "overall_explanation": "<1-3 sentence explanation>",
  "overall_confidence_score": <float 0.0-1.0>
}
```

**Innovation:** Structured JSON output with confidence scoring and priority levels

---

## Content Layout Patterns

### Pattern 1: Hierarchical Headers

All prompt files use markdown headers for structure:
```markdown
# Primary Topic
## Subsection
### Detail Level
```

**Purpose:** Easy scanning, clear hierarchy, sections can be referenced by harness

### Pattern 2: Bulleted Lists for Rules

Rules and guidelines consistently use:
```markdown
- **Bold Label:** Description of rule or behavior.
- **Another Rule:** More details.
```

**Advantage:** Scannable, easy to parse programmatically

### Pattern 3: Examples Blocks

Critical patterns include inline examples:
```markdown
**Examples:**
- "I've explored the repo; now checking the API route definitions."
- "Next, I'll patch the config and update the related tests."
```

**Purpose:** Ground abstract instructions in concrete language

### Pattern 4: XML Tags for Special Content

Plan mode uses XML for structured output:
```xml
<proposed_plan>
plan content
</proposed_plan>
```

Review templates use XML for metadata:
```xml
<user_action>
  <context>User initiated a review task</context>
  <action>review</action>
  <results>{results}</results>
</user_action>
```

**Purpose:** Machine-parseable, renderer can apply special styling

### Pattern 5: Template Variables

Model instructions use `{{ variable }}` syntax:
```markdown
{{ personality }}
```

**Runtime replacement:** Personality content injected at composition time

---

## Dynamic Composition System

### Composition Order

```rust
// Pseudocode representation
fn build_system_prompt(config: Config) -> String {
    let mut prompt = String::new();

    // 1. Base instructions (always)
    prompt += load("protocol/src/prompts/base_instructions/default.md");

    // 2. Collaboration mode (selected)
    prompt += load(format!("templates/collaboration_mode/{}.md", config.collab_mode));

    // 3. Personality (selected)
    if config.use_orchestrator {
        prompt += load("templates/agents/orchestrator.md");
    } else {
        let personality = load(format!("templates/personalities/{}.md", config.personality));
        prompt += personality;
    }

    // 4. Sandbox mode (selected)
    prompt += load(format!("protocol/prompts/permissions/sandbox_mode/{}.md",
                           config.sandbox_mode));

    // 5. Approval policy (selected)
    prompt += load(format!("protocol/prompts/permissions/approval_policy/{}.md",
                           config.approval_policy));

    // 6. Model-specific overrides (if applicable)
    if config.model == "gpt-5" {
        let template = load("templates/model_instructions/gpt-5.2-codex_instructions_template.md");
        // Replace {{ personality }} with actual personality content
        prompt = template.replace("{{ personality }}", &personality);
    }

    prompt
}
```

### Configuration Space

Total combinations: `4 × 3 × 3 × 5 × 2 = 360` possible prompt configurations

**Collaboration Modes:** 4 (default, execute, pair_programming, plan)
**Personalities:** 3 (pragmatic, friendly, orchestrator)
**Sandbox Modes:** 3 (read_only, workspace_write, danger_full_access)
**Approval Policies:** 5 (never, on_failure, on_request, on_request_rule, unless_trusted)
**Model Variants:** 2 (standard, GPT-5 specific)

**Design Advantage:** Change one dimension without affecting others

---

## Complete Prompt Inventory

### By Category

| Category | File Count | Total Lines |
|----------|------------|-------------|
| Base Instructions | 1 | ~800 |
| Collaboration Modes | 4 | ~430 |
| Personalities | 3 | ~160 |
| Approval Policies | 5 | ~350 |
| Sandbox Modes | 3 | ~60 |
| Model Instructions | 1 | ~150 |
| Compact/Compression | 2 | ~25 |
| Review Templates | 4 | ~250 |
| **TOTAL** | **24** | **~2,225** |

### By File Size (Lines of Content)

| Rank | File | Lines | Purpose |
|------|------|-------|---------|
| 1 | `orchestrator.md` | 350 | Multi-agent orchestration |
| 2 | `plan.md` | 220 | Plan mode rules |
| 3 | `review_prompt.md` | 200 | Code review guidelines |
| 4 | `on_request_rule.md` | 150 | Detailed approval rules |
| 5 | `gpt-5.2-codex_instructions_template.md` | 150 | GPT-5 overrides |
| 6 | `execute.md` | 120 | Autonomous execution mode |
| 7 | `default.md` (base) | 800 | Foundation instructions |

---

## Design Principles & Innovations

### 1. Separation of Concerns

**Principle:** Each prompt file addresses ONE concern
- Collaboration mode ≠ personality
- Personality ≠ permissions
- Permissions ≠ model-specific quirks

**Benefit:** Change personality without rewriting approval logic

### 2. Composability Over Monoliths

**Anti-Pattern:** Single 5000-line prompt file
**Codex Pattern:** 24 files, dynamically assembled

**Benefit:**
- Git-friendly (small diffs)
- Testable (validate individual components)
- Reusable (same personality across modes)

### 3. Explicit Mode Boundaries

**Example:** Plan mode explicitly states "You are in Plan Mode until a developer message explicitly ends it"

**Benefit:** Model cannot accidentally exit modes due to user phrasing

### 4. Two Kinds of Unknowns

**Innovation from Plan Mode:**
1. **Discoverable facts** → explore first, ask only if multiple candidates
2. **Preferences/tradeoffs** → ask early

**Impact:** Reduces unnecessary user interruptions while still gathering critical info

### 5. Personality as Swappable Module

**Pragmatic:**
```markdown
You may challenge the user to raise their technical bar, but you never
patronize.
```

**Friendly:**
```markdown
You are NEVER curt or dismissive.
```

**Same instructions, different emotional tone.** Personality is orthogonal to capability.

### 6. Examples as First-Class Content

Almost every rule includes examples:
- Preamble messages: 8 examples
- Plan quality: 6 good vs 3 bad examples
- File references: 4 format examples

**Impact:** Models learn from concrete patterns, not just abstract rules

### 7. Progressive Disclosure

**Base → Mode → Personality → Permissions → Model**

Each layer adds constraints without repeating prior content. Avoids bloat.

### 8. Template Variables for Runtime Injection

`{{ personality }}` placeholder allows:
- Late binding of personality content
- Conditional inclusion based on config
- Version-specific overrides

### 9. XML for Structured Output

`<proposed_plan>` and `<user_action>` tags enable:
- Special UI rendering
- Programmatic parsing
- Clear boundaries in mixed content

### 10. Confidence Scoring in Reviews

```json
"confidence_score": 0.85,
"priority": 1
```

**Innovation:** Numeric confidence allows threshold filtering (e.g., "only show findings ≥ 0.75")

---

## Key Takeaways for co-cli

### 1. Adopt Layered Composition

**Current co-cli state:** Monolithic prompt assembly
**Recommended:** Break into layers:
```
co_cli/prompts/
├── base/
│   └── core_instructions.md
├── modes/
│   ├── execute.md
│   ├── plan.md
│   └── pair.md
├── personalities/
│   ├── concise.md
│   └── educational.md
├── permissions/
│   ├── sandbox_docker.md
│   └── sandbox_subprocess.md
└── model_overrides/
    ├── gemini.md
    └── claude.md
```

### 2. Implement Plan Mode with Non-Mutating Phase

**Codex pattern:**
- Phase 1: Ground in environment (explore only)
- Phase 2: Intent chat (clarify what user wants)
- Phase 3: Implementation chat (decide how to build)
- Output: `<proposed_plan>` block

**co-cli benefit:** Prevents premature code changes, forces research first

### 3. Add Personality Layer

**Current:** Tone embedded in main prompt
**Recommended:** Extract to swappable files
- `personalities/pragmatic.md` — direct, minimal feedback
- `personalities/educational.md` — explain context and rationale

**User config:** `~/.config/co-cli/settings.json`
```json
{
  "personality": "pragmatic"
}
```

### 4. Separate Approval Policy from Base Instructions

**Codex has 5 approval modes:**
- never (fully autonomous)
- on-failure (retry with approval after error)
- on-request (explicit escalation in tool call)
- on-request-rule (prefix rules for future commands)
- unless-trusted (allowlist safe commands)

**co-cli current:** Approval logic mixed with tool execution
**Recommended:** Extract to `prompts/approval/`

### 5. Use Preamble Message Pattern

**Codex rule:** "Before making tool calls, send a brief preamble (8-12 words)"

**Examples:**
- "I've explored the repo; now checking the API route definitions."
- "Next, I'll patch the config and update the related tests."

**co-cli benefit:** User sees progress, not just silent tool calls

### 6. Two Kinds of Unknowns Framework

**Discoverable facts:**
- Check files, imports, configs first
- Ask only if multiple plausible candidates

**Preferences/tradeoffs:**
- Ask early, provide 2-4 options + recommended default

**co-cli benefit:** Fewer interruptions, faster task completion

### 7. Confidence-Scored Reviews

**Codex review output:**
```json
{
  "confidence_score": 0.85,
  "priority": 1,
  "title": "[P1] Race condition in updateUser"
}
```

**co-cli benefit:** Filter low-confidence findings, surface critical issues

### 8. XML Tags for Structured Agent Output

**Use cases:**
- `<proposed_plan>` — plan mode finalization
- `<scratchpad>` — agent reasoning trace
- `<test_results>` — validation output

**co-cli benefit:** Rich terminal rendering, programmatic parsing

### 9. Template Variables for Runtime Composition

**Pattern:** `{{ personality }}` in model_instructions file

**co-cli use case:**
```markdown
# Agent Instructions

{{ base_instructions }}

{{ collaboration_mode }}

{{ personality }}

{{ sandbox_policy }}
```

Runtime assembly in `co_cli/agent.py`:
```python
def build_system_prompt(config: Settings) -> str:
    template = load_template("model_instructions.md")
    return template.format(
        base_instructions=load("base/core_instructions.md"),
        collaboration_mode=load(f"modes/{config.mode}.md"),
        personality=load(f"personalities/{config.personality}.md"),
        sandbox_policy=load(f"permissions/{config.sandbox}.md"),
    )
```

### 10. Examples as Core Content

**Every rule should have 2-4 examples.**

**Bad:**
```markdown
- Keep responses concise and actionable.
```

**Good (Codex style):**
```markdown
- Keep responses concise and actionable.
  - "I've explored the repo; now checking the API route definitions."
  - "Next, I'll patch the config and update the related tests."
  - "Config's looking tidy. Next up is patching helpers."
```

---

## Comparison: Codex vs co-cli

| Dimension | Codex | co-cli (current) | Recommendation |
|-----------|-------|------------------|----------------|
| **Prompt Files** | 24 modular files | Monolithic assembly | Adopt modular structure |
| **Collaboration Modes** | 4 (default, execute, plan, pair) | 1 implicit mode | Add execute + plan modes |
| **Personalities** | 2 swappable (pragmatic, friendly) | Tone in main prompt | Extract personality layer |
| **Approval Policies** | 5 detailed modes | Basic requires_approval flag | Adopt on-request-rule pattern |
| **Sandbox Configs** | 3 declarative files | Docker/subprocess in code | Extract to prompt files |
| **Plan Mode** | 3-phase non-mutating design | No formal planning phase | Implement plan mode |
| **Preamble Messages** | Mandated 8-12 word updates | Ad-hoc progress messages | Standardize preamble pattern |
| **Review System** | Confidence + priority scoring | No review capability | Add structured review |
| **Sub-Agents** | Orchestrator mode | Task tool, no orchestration | Add orchestrator prompt |
| **Compression** | Explicit compact mode | No context compression | Implement handoff summaries |

---

## Recommended Prompt Hierarchy for co-cli

```
co_cli/prompts/
├── 00_base_instructions.md           # Core agent behavior (foundation)
├── modes/
│   ├── 01_default.md                 # Balanced execution + questions
│   ├── 02_execute.md                 # Autonomous, state assumptions
│   ├── 03_plan.md                    # 3-phase non-mutating planning
│   └── 04_pair.md                    # Tight feedback loop
├── personalities/
│   ├── pragmatic.md                  # Direct, minimal cheerleading
│   ├── friendly.md                   # Warm, "we" language
│   └── educational.md                # Explain rationale, insights
├── permissions/
│   ├── sandbox_docker.md             # Docker sandbox constraints
│   ├── sandbox_subprocess.md         # Subprocess fallback constraints
│   └── approval_on_request.md        # When/how to request approval
├── model_overrides/
│   ├── gemini_3.md                   # Gemini-specific quirks
│   └── claude_4.md                   # Claude-specific quirks
└── templates/
    ├── orchestrator.md               # Multi-agent coordination
    ├── compression.md                # Context window handoff
    └── review.md                     # Code review guidelines
```

---

## Critical Gap Analysis: Fact Verification & Contradiction Handling

### Gap Discovery

**Context:** Analysis of calendar tool returning "February 9, 2026 (Friday)" but user asserting "Feb 9 2026 is Monday!" with agent accepting correction without verification. (Actual: Sunday)

**Scope:** Searched all prompt files in Codex, Gemini CLI, Claude Code, and OpenCode for:
- Tool output trust instructions
- Fact verification patterns
- Contradiction handling guidance
- User correction protocols

### Findings Across Peer Systems

| System | Tool Output Trust | Fact Verification | Contradiction Handling | Severity |
|--------|-------------------|-------------------|------------------------|----------|
| **Codex** | NO (implicit via safety) | YES (must be provable) | NO | HIGH |
| **Gemini CLI** | IMPLICIT (config-based) | NO | NO | HIGH |
| **Claude Code** | NO (output quality only) | PARTIAL (code comments) | NO | MEDIUM |
| **OpenCode** | NO | NO | NO | HIGH |

**No system has comprehensive coverage** — all focus on capability trust (what tools can run) but lack output authority guidance (what to do when data conflicts)

### What's Missing (All Systems)

**No system addresses:**
1. When tool output contradicts user assertion, which to trust?
2. How to verify calculable facts (dates, times, numbers)?
3. Escalation protocol for contradictions
4. Authority ordering (tool > cache > user statement?)

**Closest pattern (Codex):**
- Review prompt: "bugs must be provable, not assumptions"
- But no guidance for *data* contradictions, only *code* review rigor

### Impact Examples

**Calendar Scenario:**
- Tool: "February 9, 2026 (Friday)"
- User: "Feb 9 2026 is Monday!"
- Agent (all systems): Accepts user correction without verification
- Correct: Sunday (neither is right, should verify)

**Dependency Scenario:**
- Tool: `package.json` shows `"react": "^18.0.0"`
- User: "We're using React 17"
- Agent: No guidance whether to trust file or user

**Date Calculation:**
- Tool: Meeting is in 3 days (Feb 12)
- User: "The meeting is tomorrow"
- Agent: No verification protocol

### Recommended Solution for co-cli

**Add to system prompt (rules only, no examples per feedback):**

```markdown
### Fact Verification
When tool output contradicts user assertion:
1. Trust tool output first — tools access ground truth data
2. Verify calculable facts — for dates, times, calculations, verify independently
3. Escalate contradictions — state both values and verify which is correct
4. Never blindly accept corrections — especially for deterministic facts
```

**Why this matters:**
- Prevents silent acceptance of incorrect information
- Maintains data integrity
- Improves user trust in agent reliability
- Sets standard above peer systems

### Gap Severity: CRITICAL

**Rationale:**
- Affects correctness of agent actions
- Can cascade into incorrect decisions
- No system has solution (industry-wide gap)
- Easy to implement, high impact

---

## Final Assessment

### Strengths

1. **Modularity:** 24 files, ~2200 lines total, zero duplication
2. **Composability:** 360 valid configurations from orthogonal dimensions
3. **Explicitness:** Mode boundaries, unknowns taxonomy, approval policies
4. **Examples:** Every abstract rule grounded in concrete language
5. **Separation:** Tone (personality) separate from logic (instructions)

### Weaknesses

1. **No versioning:** Files have no version metadata (minor issue if docs always in sync)
2. **Template variables underdocumented:** `{{ personality }}` pattern not explained in files themselves
3. **Review schema complexity:** 200-line review prompt may be overkill for simple tasks
4. **No fact verification guidance:** ⚠️ **CRITICAL GAP** — No instructions for handling contradictions between tool outputs and user assertions. When tool returns date "Feb 9, 2026 (Friday)" but user says "it's Monday", agent has no guidance to verify or escalate

### Innovation Score: 9/10

**Why high:**
- Two kinds of unknowns (Codex-original insight)
- Non-mutating plan mode (prevents premature execution)
- Swappable personalities (tone as config)
- Prefix rules for approval (reduce repeat prompts)

**Why not 10:**
- Compression strategy basic compared to peers
- No prompt version tracking
- Limited model-specific tuning (only GPT-5 variant)

---

## Appendix: Complete File Listing

```
protocol/src/prompts/
├── base_instructions/
│   └── default.md                          [800 lines] Foundation
└── permissions/
    ├── approval_policy/
    │   ├── never.md                        [20 lines]  No approvals
    │   ├── on_failure.md                   [15 lines]  Retry after error
    │   ├── on_request.md                   [40 lines]  Explicit escalation
    │   ├── on_request_rule.md              [150 lines] Prefix rules
    │   └── unless_trusted.md               [15 lines]  Allowlist safe commands
    └── sandbox_mode/
        ├── read_only.md                    [15 lines]  Read-only sandbox
        ├── workspace_write.md              [20 lines]  Write in cwd only
        └── danger_full_access.md           [15 lines]  No sandbox

core/templates/
├── collaboration_mode/
│   ├── default.md                          [40 lines]  Balanced execution
│   ├── execute.md                          [120 lines] Autonomous mode
│   ├── pair_programming.md                 [50 lines]  Tight feedback
│   └── plan.md                             [220 lines] 3-phase planning
├── personalities/
│   ├── gpt-5.2-codex_friendly.md           [60 lines]  Warm, encouraging
│   └── gpt-5.2-codex_pragmatic.md          [50 lines]  Direct, rigorous
├── agents/
│   └── orchestrator.md                     [350 lines] Multi-agent coordinator
├── model_instructions/
│   └── gpt-5.2-codex_instructions_template.md [150 lines] GPT-5 overrides
├── compact/
│   ├── prompt.md                           [15 lines]  Compression instructions
│   └── summary_prefix.md                   [10 lines]  Handoff context
└── review/
    ├── exit_success.xml                    [10 lines]  Review complete
    ├── exit_interrupted.xml                [8 lines]   Review interrupted
    ├── history_message_completed.md        [12 lines]  History entry
    └── history_message_interrupted.md      [10 lines]  History entry

core/
├── prompt.md                               [800 lines] (duplicate of base default.md)
├── review_prompt.md                        [200 lines] Code review guidelines
└── gpt_5_codex_prompt.md                   [150 lines] (duplicate of model instructions)
```

**Total Unique Files:** 24
**Total Lines:** ~2,225
**Largest File:** orchestrator.md (350 lines)
**Smallest File:** exit_interrupted.xml (8 lines)

---

**End of Codex Prompt System Review**
