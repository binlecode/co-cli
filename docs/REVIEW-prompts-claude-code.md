# REVIEW: Claude Code Prompt System Architecture

**Repo:** `~/workspace_genai/claude-code` (TypeScript/Anthropic)
**Analyzed:** 2026-02-08 | **79 prompt files** | **~182,400 lines** (incl. skill references)

---

## Architecture

Claude Code uses a **plugin-based, event-driven prompt architecture**. Prompts are organized into **three primitive types** (agents, commands, skills) stored as **markdown files with YAML frontmatter**, assembled at runtime through a **hook system** that injects context at 5 lifecycle events.

```
┌──────────────────────────────────────────────────────────────┐
│              PLUGIN-BASED EVENT COMPOSITION                   │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Session Init                                              │
│     ↓                                                         │
│  2. SessionStart Hooks Execute                                │
│     (inject style/context: explanatory, learning)            │
│     ↓                                                         │
│  3. Base System Prompt (inherited from parent session)       │
│     ↓                                                         │
│  4. User Input                                                │
│     ↓                                                         │
│  5. UserPromptSubmit Hooks Execute                            │
│     (hookify rule engine: validate against local rules)      │
│     ↓                                                         │
│  6. Agent/Command/Skill Selection                             │
│     IF /command → load command prompt                         │
│     ELSE IF trigger matches → spawn agent                    │
│     ELSE IF question matches → activate skill                │
│     ↓                                                         │
│  7. Load Selected Prompt (YAML frontmatter + markdown body)  │
│     ↓                                                         │
│  8. PreToolUse Hooks Execute                                  │
│     (security-guidance: pattern detection → allow|warn|block)│
│     ↓                                                         │
│  9. Tool Execution (if allowed)                               │
│     ↓                                                         │
│ 10. PostToolUse Hooks Execute (logging, transformation)      │
│     ↓                                                         │
│ 11. Response / Stop Hooks                                     │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
claude-code/
├── plugins/                           # 12 official plugins
│   ├── agent-sdk-dev/                 # Agent SDK development
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/*.md
│   │   ├── commands/*.md
│   │   └── hooks/
│   ├── code-review/                   # PR review
│   │   ├── agents/code-reviewer.md
│   │   └── commands/code-review.md
│   ├── commit-commands/               # Git workflow
│   ├── explanatory-output-style/      # Learning mode (SessionStart hook)
│   ├── feature-dev/                   # Feature development
│   │   ├── agents/
│   │   │   ├── code-explorer.md       # Codebase analysis
│   │   │   ├── code-architect.md      # Architecture design
│   │   │   └── code-reviewer.md       # Code review
│   │   └── commands/feature-dev.md    # 7-phase workflow
│   ├── frontend-design/               # UI/frontend tools
│   ├── hookify/                       # Rule engine framework
│   │   └── core/rule_engine.py
│   ├── learning-output-style/         # Interactive learning
│   ├── plugin-dev/                    # Plugin development
│   │   ├── agents/ (3 agents)
│   │   └── skills/ (4 skills with references/ and examples/)
│   ├── pr-review-toolkit/             # PR review agents
│   │   ├── agents/silent-failure-hunter.md
│   │   └── agents/code-simplifier.md
│   ├── ralph-wiggum/                  # Self-referential AI
│   └── security-guidance/             # Security hooks
│       └── hooks/security_reminder_hook.py
└── .claude/commands/                  # User-defined slash commands
```

**Total:** 12 plugins, 3 prompt primitives, 5 hook events

---

## Prompt Inventory

| Category | Files | Total Lines | Avg Length |
|----------|-------|-------------|------------|
| Agents | 12 | ~8,400 | 700 |
| Commands | 8 | ~6,000 | 750 |
| Skills | 12 | ~28,800 | 2,400 |
| Hooks | 5 | ~1,200 | 240 |
| Skill References | 24 | ~120,000 | 5,000 |
| Skill Examples | 18 | ~18,000 | 1,000 |
| **TOTAL** | **79** | **~182,400** | |

### Agents (12 files)

| Agent | Plugin | Lines | Color | Purpose |
|-------|--------|-------|-------|---------|
| code-explorer | feature-dev | 800 | blue | Codebase analysis |
| code-architect | feature-dev | 1000 | cyan | Architecture design |
| code-reviewer | feature-dev | 700 | yellow | Code review |
| agent-creator | plugin-dev | 900 | green | Agent generation |
| plugin-validator | plugin-dev | 600 | yellow | Plugin validation |
| silent-failure-hunter | pr-review-toolkit | ~500 | yellow | Error handling audit |
| code-simplifier | pr-review-toolkit | ~500 | yellow | Simplification |
| frontend-designer | frontend-design | 900 | magenta | UI/UX design |
| orchestrator | ralph-wiggum | 1150 | magenta | Multi-agent coordination |

### Commands (8 files)

| Command | Plugin | Phases | Lines | Purpose |
|---------|--------|--------|-------|---------|
| feature-dev | feature-dev | 7 | 1200 | End-to-end feature |
| code-review | code-review | 3 | 600 | PR review workflow |
| commit | commit-commands | 1 | 300 | Git commit |
| plugin-create | plugin-dev | 5 | 800 | Create new plugin |

---

## Three Prompt Primitives

### A. Agents (Autonomous Subprocesses)

**File pattern:** `plugins/[name]/agents/[name].md`

**Frontmatter schema:**
```yaml
name: agent-identifier
description: |
  Use this agent when [scenario].
  Examples:
  <example>
    <context>[Situation]</context>
    <user>[User input]</user>
    <assistant>[Claude response]</assistant>
    <commentary>[Why agent triggers]</commentary>
  </example>
model: sonnet|opus|haiku|inherit
color: blue|cyan|green|yellow|magenta
tools: ["Tool1", "Tool2"]
```

**Body pattern:**
```markdown
You are [specific role] specializing in [specific domain].

**Your Core Responsibilities:**
1. [Primary responsibility with details]
2. [Secondary responsibility with details]

**[Process Name] Process:**
1. [Concrete step with specifics]
2. [Concrete step with specifics]

**Quality Standards:**
- [Standard with measurable criteria]

**Output Format:**
[Exact structure — JSON schema or markdown template]

**Edge Cases:**
- [Edge case]: [Handling approach]
```

### B. Commands (User-Triggered Workflows)

**File pattern:** `plugins/[name]/commands/[name].md`

**Structure:** Multi-phase workflows with success criteria and phase transitions.

```markdown
## Phase 1: [Name]
**Goal**: [What should be accomplished]
**Actions**:
1. [Specific action with tool/approach]
**Success Criteria**:
- [Measurable outcome]
**Next**: Proceed to Phase 2 when [condition]
```

### C. Skills (Educational Knowledge Bases)

**File pattern:** `plugins/[name]/skills/[skill-name]/SKILL.md`

**Structure:** 1000-3000 word overview with supporting subdirectories:
```
skills/[skill-name]/
├── SKILL.md           # Overview, quick reference, pointers
├── references/        # 5000-15000 word deep dives
├── examples/          # Working code examples
└── scripts/           # Utility scripts
```

---

## Key Prompts (Verbatim)

### Code Architect Agent

```
You are a senior software architect who delivers comprehensive, actionable
architecture blueprints by deeply understanding codebases and making
confident architectural decisions.

## Core Process
1. **Codebase Pattern Analysis** - Extract existing patterns, conventions
2. **Architecture Design** - Make decisive choices. Pick one approach and commit
3. **Complete Implementation Blueprint** - Specify every file to create/modify

## Output Guidance
- Patterns & Conventions Found (with file:line references)
- Architecture Decision (chosen approach with rationale)
- Component Design (file path, responsibilities, dependencies, interfaces)
- Implementation Map (specific files to create/modify)
- Data Flow (complete flow from entry points to outputs)
- Build Sequence (phased implementation steps as a checklist)

Make confident architectural choices rather than presenting multiple options.
```

### Code Reviewer Agent (Confidence Scoring)

```
You are an expert code reviewer. Primary responsibility: review code against
project guidelines in CLAUDE.md with high precision to minimize false positives.

## Confidence Scoring (0-100)
- 0: False positive
- 25: Might be real, might be false positive
- 50: Moderately confident, possibly a nitpick
- 75: Highly confident, verified, important
- 100: Absolutely certain, confirmed

**Only report issues with confidence >= 80.**
Focus on issues that truly matter - quality over quantity.
```

### Silent Failure Hunter Agent

```
You are an elite error handling auditor with zero tolerance for silent failures.

## Core Principles (non-negotiable)
1. Silent failures are unacceptable
2. Users deserve actionable feedback
3. Fallbacks must be explicit and justified
4. Catch blocks must be specific
5. Mock/fake implementations belong only in tests
```

### Frontend Design Skill

```
This skill guides creation of distinctive, production-grade frontend
interfaces that avoid generic "AI slop" aesthetics.

## Design Thinking
Before coding, commit to a BOLD aesthetic direction:
- **Purpose**: What problem does this interface solve?
- **Tone**: Pick an extreme: brutally minimal, maximalist chaos, retro-futuristic
- **Differentiation**: What makes this UNFORGETTABLE?

NEVER: overused font families (Inter, Roboto, Arial), cliched purple
gradients on white, predictable layouts.
```

### Explanatory Output Style Hook

```
You are in 'explanatory' output style mode. Provide educational insights
about the codebase as you help with the user's task.

Before and after writing code, provide brief educational explanations:
"★ Insight ─────────────────────────────────────
[2-3 key educational points]
─────────────────────────────────────────────────"
```

### Learning Output Style Hook

```
You are in 'learning' output style mode. Instead of implementing everything
yourself, identify opportunities where the user can write 5-10 lines of
meaningful code that shapes the solution.

Request code contributions for:
- Business logic with multiple valid approaches
- Error handling strategies
- Algorithm implementation choices

Don't request contributions for:
- Boilerplate or repetitive code
- Obvious implementations
```

---

## Hook System (Event-Driven Composition)

### 5 Lifecycle Events

| Event | When | Use Cases | Example Plugins |
|-------|------|-----------|-----------------|
| **SessionStart** | Session begins | Style/tone injection | explanatory-output-style |
| **UserPromptSubmit** | Before processing input | Validation, rule enforcement | hookify (rule engine) |
| **PreToolUse** | Before tool execution | Security checks, approval | security-guidance |
| **PostToolUse** | After tool execution | Logging, transformation | (telemetry) |
| **Stop** | Before session end | Final checks, cleanup | (session mgmt) |

### Hook Handler Return Schema

```json
{
  "status": "allow | warn | block",
  "systemMessage": "[Inject into prompt]",
  "userMessage": "[Show to user]",
  "additionalContext": "[Extra context for agent]"
}
```

### Security Guidance Hook (PreToolUse)

Patterns detected: command injection, XSS, SQL injection, hardcoded secrets, path traversal, insecure deserialization.

Returns `block` for critical patterns, `warn` for potential issues, `allow` for safe operations.

### Hookify Rule Engine (User-Defined Rules)

**Rule format in `.claude/hookify.*.local.md`:**
```markdown
## Rule: no-force-push
- event: bash
- tool: Bash
- condition: command regex "git push.*--force"
- action: block
- message: "Force push blocked. Use --force-with-lease."
```

---

## Innovations

### 1. Three Prompt Primitives

Clear taxonomy: Agents (autonomous subprocess) / Commands (user-guided workflow) / Skills (educational knowledge). Each optimized for its use case.

| Type | Trigger | Lifespan | Output |
|------|---------|----------|--------|
| Agent | Description matching | Single task | Structured report |
| Command | Slash command | Multi-phase | Task completion |
| Skill | Question/context | Session-scoped | Learning |

### 2. Event-Driven Composition

Hooks inject context at lifecycle events, not static assembly. Security as a layer (PreToolUse), not embedded in every prompt. Extensibility without modifying core prompts.

### 3. Plugin Architecture

Prompts packaged as self-contained plugins with manifests, versioning, dependencies. Community extensibility — core stays lean.

### 4. Description-Based Agent Triggering

Agent descriptions include `<example>` blocks with context/user/assistant/commentary. Self-documenting triggers that serve as training data for agent selection.

### 5. Confidence-Scored Reviews

0-100 confidence scale, only report issues >= 80. Quality over quantity approach to code review.

### 6. Progressive Disclosure (Skills)

Main SKILL.md stays concise (1000-3000 words), deep dives in references/ subdirectory (5000-15000 words each). Balances accessibility with depth.

### 7. Multi-Agent Orchestration via Commands

Commands launch multiple agents in parallel (e.g., feature-dev: 3 explorers → 3 architects → 3 reviewers = 7 phases, 6-8 agent launches).

### 8. Security as a Hook Layer

Central security policy applies to all agents/commands. Easy to update without prompt rewrites. Pattern: detected via hook, not via instructions in every prompt.

### 9. Per-Agent Model Selection

```yaml
model: inherit   # Use parent/session model (recommended)
model: opus      # Force most capable
model: haiku     # Force fastest
```

Cost optimization and capability matching without code changes.

### 10. Rule-Based Hook Engine (Hookify)

User-defined content matching rules in markdown files. No code changes needed for project-specific safety policies.

---

## Content Layout Patterns

| Pattern | Usage | Example |
|---------|-------|---------|
| YAML frontmatter + markdown body | All prompts | `---\nname: ...\n---\nBody` |
| Second-person instructions | All agents/commands | "You are [role]" not "I am" |
| Bold-header sections | Structure within body | `**Section Name:**` |
| XML example blocks | Agent descriptions | `<example><context>...</example>` |
| Numbered process steps | All processes | 5-10 sequential steps |
| Prescriptive output format | Every agent | Exact markdown/JSON template |
| Edge case documentation | Every agent | `- [Case]: [Handling]` |
| Progressive disclosure | Skills only | SKILL.md → references/ → examples/ |

---

## Key Takeaways for co-cli

1. **Three prompt primitives** — clear taxonomy (agents/commands/skills) optimized per use case
2. **Event-driven hooks** — security and context as layers, not embedded in every prompt
3. **Plugin architecture** — prompts packaged as self-contained, versioned, distributable units
4. **Confidence-scored reviews** — numeric confidence with threshold filtering (>= 80)
5. **Description-based triggering** — self-documenting agent triggers with `<example>` blocks
6. **Progressive disclosure** — concise main doc, deep dives in references/
7. **Multi-agent orchestration** — commands as workflow orchestrators launching parallel agents
8. **Per-agent model selection** — cost/capability optimization in frontmatter
9. **Rule-based hook engine** — user-defined safety policies in markdown
10. **Prescriptive output format** — every agent specifies exact output structure

---

**Source:** `~/workspace_genai/claude-code` — all prompts traceable from directory structure above
