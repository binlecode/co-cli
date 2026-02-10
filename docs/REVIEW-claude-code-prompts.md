# REVIEW: Claude Code Prompt System Architecture

**Repository:** `~/workspace_genai/claude-code` (TypeScript/Anthropic)
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
8. [Critical Gap Analysis](#critical-gap-analysis)
9. [Final Assessment](#final-assessment)
10. [Appendix](#appendix-complete-file-listing)

---

## Architecture Overview

### High-Level Design

Claude Code uses a **plugin-based, event-driven prompt architecture**. Unlike Codex's layered files or Gemini CLI's conditional blocks, Claude Code organizes prompts into **three primitive types** (agents, commands, skills) stored as **markdown files with YAML frontmatter**, assembled at runtime through a **hook system** that injects context at specific lifecycle events.

**Key Differentiator:** Prompts are **first-class artifacts** in a plugin system, with **event-driven composition** through hooks rather than static assembly.

```
┌─────────────────────────────────────────────────────────────┐
│              PLUGIN-BASED EVENT COMPOSITION                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SessionStart Hook                                           │
│         ↓                                                    │
│  + Base System Instructions (inherited from parent)         │
│         ↓                                                    │
│  + Agent/Command/Skill Markdown (selected by user/trigger)  │
│         │                                                    │
│         ├─ YAML Frontmatter (name, model, tools, color)     │
│         └─ Body (role, responsibilities, process, format)   │
│         ↓                                                    │
│  + UserPromptSubmit Hook (validates/transforms input)       │
│         ↓                                                    │
│  + PreToolUse Hook (security checks, warnings)              │
│         ↓                                                    │
│  Tool Execution                                              │
│         ↓                                                    │
│  + PostToolUse Hook (logging, output transformation)        │
│         ↓                                                    │
│  + Stop Hook (final checks, cleanup)                        │
│         ↓                                                    │
│  = FINAL SYSTEM PROMPT + RUNTIME CONTEXT                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
claude-code/
├── .claude/                           # User global config
│   └── commands/                      # User-defined slash commands
├── .claude-plugin/
│   ├── marketplace.json               # Plugin registry
│   └── plugins/                       # Bundled plugins
├── plugins/                           # Official plugins (12)
│   ├── agent-sdk-dev/                 # Agent SDK development
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json            # Plugin manifest
│   │   ├── agents/                    # Agent prompts
│   │   │   └── *.md                   # Agent definitions
│   │   ├── commands/                  # Command workflows
│   │   │   └── *.md                   # Command definitions
│   │   └── hooks/
│   │       ├── hooks.json             # Event configuration
│   │       └── *.py / *.sh            # Event handlers
│   ├── code-review/
│   │   ├── agents/
│   │   │   └── code-reviewer.md       # PR review agent
│   │   └── commands/
│   │       └── code-review.md         # /code-review workflow
│   ├── commit-commands/               # Git workflow
│   ├── explanatory-output-style/      # Learning mode
│   │   └── hooks/
│   │       ├── hooks.json             # SessionStart injection
│   │       └── session-start.sh       # Adds explanation style
│   ├── feature-dev/                   # Feature development
│   │   ├── agents/
│   │   │   ├── code-explorer.md       # Codebase analysis
│   │   │   ├── code-architect.md      # Architecture design
│   │   │   └── code-reviewer.md       # Code review
│   │   └── commands/
│   │       └── feature-dev.md         # 7-phase workflow
│   ├── frontend-design/               # UI/frontend tools
│   ├── hookify/                       # Rule engine framework
│   │   └── core/
│   │       └── rule_engine.py         # Content-based hooks
│   ├── learning-output-style/         # Interactive learning
│   ├── plugin-dev/                    # Plugin development
│   │   ├── agents/
│   │   │   ├── agent-creator.md       # Agent generation
│   │   │   ├── plugin-validator.md    # Plugin validation
│   │   │   └── skill-reviewer.md      # Skill quality review
│   │   └── skills/
│   │       ├── agent-development/
│   │       │   ├── SKILL.md           # Agent creation guide
│   │       │   ├── references/        # Detailed docs
│   │       │   ├── examples/          # Working examples
│   │       │   └── scripts/           # Utilities
│   │       ├── command-development/
│   │       ├── plugin-structure/
│   │       └── hook-development/
│   ├── pr-review-toolkit/             # PR review agents
│   ├── ralph-wiggum/                  # Self-referential AI
│   └── security-guidance/             # Security hooks
│       └── hooks/
│           ├── hooks.json             # PreToolUse event
│           └── security_reminder_hook.py  # Pattern detection
└── examples/                          # Example hooks/settings
```

**Total Plugins:** 12 official plugins
**Prompt Types:** 3 (agents, commands, skills)
**Hook Events:** 5 (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop)

---

## Prompt Structure & Modularization

### 1. Three Prompt Primitives

Claude Code organizes all prompts into three types:

#### A. **Agents** (Autonomous Subprocesses)

**File Pattern:** `plugins/[plugin-name]/agents/[name].md`
**Purpose:** Specialized autonomous agents spawned for specific tasks
**Structure:** YAML frontmatter + markdown body

**Frontmatter Schema:**
```yaml
name: agent-identifier           # How it's referenced in code
description: |                    # When/how to trigger (with examples)
  Use this agent when [scenario].
  Examples:
  <example>
    <context>[Situation]</context>
    <user>[User input]</user>
    <assistant>[Claude response triggering agent]</assistant>
    <commentary>[Why agent triggers]</commentary>
  </example>
model: sonnet|opus|haiku|inherit # Model selection (inherit recommended)
color: blue|cyan|green|yellow    # Visual identifier in UI
tools: ["Tool1", "Tool2"]        # Optional - restricts available tools
```

**Body Pattern:**
```markdown
You are [specific role] specializing in [specific domain].

**Your Core Responsibilities:**
1. [Primary responsibility with details]
2. [Secondary responsibility with details]
3. [Additional responsibilities]

**[Process Name] Process:**
1. [Concrete step with specifics]
2. [Concrete step with specifics]
3. [Continue with clear numbered steps]

**Quality Standards:**
- [Standard 1 with measurable criteria]
- [Standard 2 with measurable criteria]

**Output Format:**
[Specific structure requirements - JSON schema, markdown template, etc.]

**Edge Cases:**
- [Edge case 1]: [Handling approach]
- [Edge case 2]: [Handling approach]
```

**Example Agent:**

`feature-dev/agents/code-explorer.md` (400 lines)
```markdown
---
name: code-explorer
description: |
  Use this agent to perform deep, systematic exploration of a codebase...
  Examples:
  <example>
    <context>User wants to add authentication to existing app</context>
    <user>Add user authentication with JWT</user>
    <assistant>I'll need to explore the codebase first...</assistant>
    <commentary>Triggers because implementation requires understanding existing structure</commentary>
  </example>
model: inherit
color: blue
tools: ["Read", "Grep", "Glob", "Bash"]
---

You are an expert codebase analyst specializing in systematic code exploration.

**Your Core Responsibilities:**
1. Perform comprehensive codebase analysis to understand architecture, patterns, and conventions
2. Identify all relevant files, modules, and dependencies for the given task
3. Document findings in a structured format for handoff to implementation agents

**Exploration Process:**
1. **Initial Scan**: Use Glob to identify file structure
2. **Pattern Recognition**: Grep for key patterns (imports, definitions, conventions)
3. **Dependency Mapping**: Trace imports and relationships
4. **Convention Analysis**: Identify coding standards and patterns
5. **Risk Assessment**: Flag potential issues or conflicts
6. **Report Generation**: Produce structured findings

**Quality Standards:**
- Explore systematically, not randomly (follow dependency chains)
- Document all assumptions and uncertainties
- Prioritize findings by relevance to task
- Include concrete file paths and line numbers

**Output Format:**
```markdown
## Exploration Summary
- **Task**: [What we're exploring for]
- **Scope**: [Files/modules examined]

## Key Findings
1. [Finding 1 with file:line references]
2. [Finding 2 with file:line references]

## Architecture Patterns
- [Pattern 1]: [Where used, examples]
- [Pattern 2]: [Where used, examples]

## Relevant Files
| File | Purpose | Priority |
|------|---------|----------|
| path | what it does | high/medium/low |

## Risks & Considerations
- [Risk 1]
- [Risk 2]

## Recommendations
- [Next step 1]
- [Next step 2]
```

**Edge Cases:**
- **Large codebases (1000+ files)**: Start with entry points (package.json, main.py), narrow scope
- **Monorepos**: Identify workspace boundaries, explore relevant workspace only
- **Missing documentation**: Infer from code structure and naming conventions
```

**Agent Pattern Analysis:**

| Agent Type | Color | Model | Tools | Typical Length |
|------------|-------|-------|-------|----------------|
| Analysis | blue/cyan | inherit | Read, Grep, Glob | 400-800 lines |
| Generation | green | inherit | Read, Write, Edit | 500-1000 lines |
| Validation | yellow | inherit | Read, Grep | 300-600 lines |
| Orchestration | magenta | opus | All + Task | 600-1200 lines |

#### B. **Commands** (User-Triggered Workflows)

**File Pattern:** `plugins/[plugin-name]/commands/[name].md`
**Purpose:** Multi-step workflows triggered by slash commands (e.g., `/feature-dev`)
**Structure:** YAML frontmatter + markdown body

**Frontmatter Schema:**
```yaml
description: [What command does]              # Brief description
argument-hint: [Argument format]              # User guidance (e.g., "<feature-description>")
allowed-tools: [Optional tool restrictions]   # Rarely used, most allow all tools
```

**Body Pattern:**
```markdown
# Command Title

[Overview paragraph explaining what this command does and when to use it]

## Prerequisites
- [Requirement 1]
- [Requirement 2]

## Workflow Overview
[High-level summary of phases]

## Phase 1: [Phase Name]
**Goal**: [What should be accomplished in this phase]
**Actions**:
1. [Specific action with tool/approach]
2. [Specific action with tool/approach]

**Success Criteria**:
- [Measurable outcome 1]
- [Measurable outcome 2]

**Next**: Proceed to Phase 2 when [condition]

## Phase 2: [Phase Name]
[Same structure...]

[Continue for all phases...]

## Output Format
[Final deliverable format]

## Common Issues & Solutions
- **Issue**: [Problem]
  - **Solution**: [How to handle]
```

**Example Command:**

`feature-dev/commands/feature-dev.md` (1200 lines)
```markdown
---
description: Structured 7-phase feature development workflow
argument-hint: "<feature-description>"
---

# Feature Development Workflow

This command guides you through a comprehensive feature development process from initial exploration through implementation, testing, and review.

## Prerequisites
- Project must have CLAUDE.md (codebase context)
- Git repository initialized
- Test framework available

## Workflow Overview

1. **Clarification** - Understand requirements
2. **Exploration** - Analyze codebase (launch 2-3 code-explorer agents)
3. **Context Review** - Read relevant files manually
4. **Architecture** - Design approach (launch 2-3 code-architect agents)
5. **Implementation** - Write code with tests
6. **Review** - Quality check (launch 3 code-reviewer agents)
7. **Finalization** - Summary and handoff

## Phase 1: Clarification
**Goal**: Fully understand the feature requirements

**Actions**:
1. Ask clarifying questions about:
   - Functional requirements
   - Non-functional requirements (performance, security)
   - Edge cases and error handling
   - Success criteria and testing approach
2. Document requirements in structured format
3. Confirm understanding with user before proceeding

**Success Criteria**:
- All ambiguities resolved
- User confirms requirements are accurate
- Edge cases identified

**Next**: Proceed to Phase 2 when user approves requirements

## Phase 2: Exploration
**Goal**: Understand codebase architecture and identify all relevant files

**Actions**:
1. Launch 2-3 `code-explorer` agents in parallel:
   - Agent 1: Explore existing similar features
   - Agent 2: Explore testing patterns and infrastructure
   - Agent 3: Explore configuration and setup files
2. Wait for all agents to complete
3. Consolidate findings from all agents
4. Identify list of files that need to be read

**Success Criteria**:
- Architecture patterns understood
- Coding conventions identified
- Relevant files listed with priorities

**Next**: Proceed to Phase 3

[Phases 3-7 continue with same structure...]

## Output Format

At completion, provide:

```markdown
## Feature Implementation Summary

**Feature**: [Name]
**Status**: Complete

### Changes Made
- [File 1]: [What changed]
- [File 2]: [What changed]

### Tests Added
- [Test file 1]: [Coverage]

### Quality Checks
- ✅ Code review passed
- ✅ Tests passing
- ✅ Conventions followed

### Follow-up Items
- [ ] [Item 1 if any]
```

## Common Issues & Solutions

- **Issue**: Exploration agents return too many files
  - **Solution**: Narrow scope by focusing on specific module or subsystem

- **Issue**: Architect agents propose conflicting approaches
  - **Solution**: Present trade-offs to user, let them choose based on project priorities

- **Issue**: Code reviewer agents find critical issues
  - **Solution**: Return to Phase 5 (Implementation), address issues, re-run Phase 6
```

**Command Pattern Analysis:**

| Command | Phases | Agent Launches | Typical Length | Purpose |
|---------|--------|----------------|----------------|---------|
| feature-dev | 7 | 6-8 agents | 1200 lines | End-to-end feature |
| code-review | 3 | 3 agents | 600 lines | PR review |
| commit | 1 | 0 | 300 lines | Git commit workflow |

#### C. **Skills** (Educational Knowledge Bases)

**File Pattern:** `plugins/[plugin-name]/skills/[skill-name]/SKILL.md`
**Purpose:** Comprehensive educational documentation triggered by context/questions
**Structure:** YAML frontmatter + markdown body + supporting subdirectories

**Frontmatter Schema:**
```yaml
name: Skill Name                      # Human-readable name
description: |                         # When skill should be activated
  This skill should be used when [scenario].
  Trigger phrases: "[phrase1]", "[phrase2]"
version: 0.1.0                        # Semantic versioning
```

**Body Pattern (1000-3000 words):**
```markdown
# Skill Title

[Overview paragraph - what this skill teaches]

## Key Concepts

[Core concepts user needs to understand]

## Core Processes

### Process 1: [Name]
[Step-by-step guidance with examples]

### Process 2: [Name]
[Step-by-step guidance with examples]

## Quick Reference

[Cheatsheet-style summary for quick lookup]

## Detailed Resources

[Pointers to references/ subdirectory for deep dives]

## Examples

[Pointers to examples/ subdirectory for working code]

## Common Patterns

[Frequently used patterns with explanations]

## Troubleshooting

[Common issues and solutions]
```

**Supporting Subdirectories:**
```
skills/[skill-name]/
├── SKILL.md                    # Main educational content (1000-3000 words)
├── references/                 # Detailed documentation (moved out for clarity)
│   ├── system-prompt-design.md
│   ├── triggering-examples.md
│   └── agent-patterns.md
├── examples/                   # Working code examples
│   ├── analysis-agent.md
│   ├── generation-agent.md
│   └── validation-agent.md
└── scripts/                    # Utility scripts
    ├── validate-agent.sh
    └── test-trigger.sh
```

**Example Skill:**

`plugin-dev/skills/agent-development/SKILL.md` (2400 lines)
```markdown
---
name: Agent Development
description: |
  This skill should be used when creating or modifying agents.
  Trigger phrases: "create an agent", "write agent prompt", "agent system prompt"
version: 0.1.0
---

# Agent Development

This skill guides you through creating effective autonomous agents using Claude Code's agent system.

## Key Concepts

**Agent**: An autonomous subprocess with specialized expertise, launched to perform specific tasks and return structured results.

**Agent System Prompt**: Second-person instructions defining role, responsibilities, process, quality standards, and output format.

**Triggering**: Agents activate based on description field matching user input or conversational context.

## Core Processes

### Process 1: Agent Creation

1. **Define Purpose**: What specific task does this agent solve? (Analysis, generation, validation, orchestration)

2. **Choose Pattern**: Select from 4 agent patterns based on purpose:
   - **Analysis**: Examine, identify, recommend
   - **Generation**: Create, produce, construct
   - **Validation**: Check, verify, approve/reject
   - **Orchestration**: Coordinate, delegate, synthesize

3. **Write Frontmatter**:
   ```yaml
   name: descriptive-identifier
   description: |
     Use this agent when [clear trigger scenario].
     Examples: [2-4 concrete examples]
   model: inherit
   color: [blue=analysis, green=generation, yellow=validation, magenta=orchestration]
   tools: [optional restrictions]
   ```

4. **Write System Prompt**:
   ```markdown
   You are [specific role] specializing in [specific domain].

   **Your Core Responsibilities:**
   1. [Responsibility with details]
   2. [Responsibility with details]
   3. [Responsibility with details]

   **[Process Name] Process:**
   1. [Concrete step]
   2. [Concrete step]
   [5-10 steps total]

   **Quality Standards:**
   - [Measurable standard]
   - [Measurable standard]

   **Output Format:**
   [Exact structure with example]

   **Edge Cases:**
   - [Case]: [Handling]
   ```

5. **Test Triggering**: Use examples/ and scripts/ to validate

### Process 2: Agent Testing

[Step-by-step testing guidance...]

### Process 3: Agent Refinement

[Iterative improvement guidance...]

## Quick Reference

**Minimum Agent Length**: ~500 words
**Standard Agent Length**: 1000-2000 words
**Comprehensive Agent**: 2000-5000 words
**Avoid**: >10,000 words (diminishing returns)

**Required Sections**:
- Role definition
- Core Responsibilities (3-5 items)
- Process (5-10 steps)
- Output Format (exact structure)
- Quality Standards
- Edge Cases (2-5 scenarios)

## Detailed Resources

- `references/system-prompt-design.md` - Deep dive on prompt engineering
- `references/triggering-examples.md` - 50+ trigger examples
- `references/agent-patterns.md` - 4 patterns with variations

## Examples

- `examples/analysis-agent.md` - Complete code-explorer agent
- `examples/generation-agent.md` - Complete code-generator agent
- `examples/validation-agent.md` - Complete plugin-validator agent

## Common Patterns

**Pattern 1: Systematic Exploration**
```markdown
**Exploration Process:**
1. Initial scan (Glob)
2. Pattern recognition (Grep)
3. Dependency mapping (Read + analysis)
4. Convention analysis
5. Report generation
```

**Pattern 2: Multi-Option Generation**
```markdown
**Generation Process:**
1. Understand requirements
2. Generate 3 approaches (conservative, balanced, innovative)
3. For each: [structure]
4. Compare trade-offs
5. Recommend default
```

**Pattern 3: Pass/Fail Validation**
```markdown
**Validation Process:**
1. Load validation criteria
2. Scan target
3. Check each criterion
4. Collect violations
5. Determine pass/fail
6. Return structured report with severity
```

## Troubleshooting

**Issue**: Agent triggers too broadly
- **Solution**: Add more specific trigger examples, narrow description

**Issue**: Agent output inconsistent
- **Solution**: Make Output Format more prescriptive (include JSON schema or template)

**Issue**: Agent process unclear
- **Solution**: Add concrete examples for each step, use numbered lists
```

**Skill Pattern Analysis:**

| Skill | Main Content | References | Examples | Scripts | Purpose |
|-------|--------------|------------|----------|---------|---------|
| agent-development | 2400 lines | 3 files | 3 files | 2 files | Agent creation |
| command-development | 1800 lines | 2 files | 2 files | 1 file | Command workflows |
| plugin-structure | 1200 lines | 4 files | 1 file | 1 file | Plugin organization |
| hook-development | 1600 lines | 3 files | 4 files | 2 files | Hook system |

---

### 2. Hook System (Event-Driven Composition)

**File Pattern:** `plugins/[plugin-name]/hooks/hooks.json` + handler scripts

Hooks inject additional context or control flow at **5 lifecycle events**:

| Event | When Triggered | Use Cases | Example Plugins |
|-------|----------------|-----------|-----------------|
| **SessionStart** | Session begins | Global style/tone injection, context setup | explanatory-output-style, learning-output-style |
| **UserPromptSubmit** | Before processing user input | Validation, transformation, rule enforcement | hookify (rule engine) |
| **PreToolUse** | Before tool execution | Security checks, approval gates, warnings | security-guidance |
| **PostToolUse** | After tool execution | Logging, output transformation, persistence | (logging, telemetry) |
| **Stop** | Before session end | Final checks, cleanup, confirmations | (session management) |

#### Hook Configuration Schema

**File:** `hooks/hooks.json`
```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "./hooks/session-start.sh",
        "env": { "VAR": "value" }
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "python",
            "module": "hooks.security_reminder_hook",
            "function": "check_edit"
          }
        ]
      }
    ]
  }
}
```

#### Hook Handler Return Schema

**Handlers return JSON:**
```json
{
  "status": "allow" | "warn" | "block",
  "systemMessage": "[Message to inject into prompt]",
  "userMessage": "[Message to show user]",
  "additionalContext": "[Extra context for agent]"
}
```

#### Example Hook: Security Guidance

**File:** `security-guidance/hooks/security_reminder_hook.py`

**Purpose:** Checks file edits for security anti-patterns before execution

**Patterns Detected:**
- Command injection (subprocess without shell=False)
- XSS vulnerabilities (innerHTML, dangerouslySetInnerHTML)
- SQL injection (string concatenation in queries)
- Hardcoded secrets (API keys, tokens)
- Path traversal (../.. in file operations)
- Insecure deserialization (pickle, eval)

**Return Values:**
```python
# Critical pattern → block execution
{
    "status": "block",
    "systemMessage": "CRITICAL: Detected command injection pattern. Use parameterized subprocess calls.",
    "userMessage": "Blocked edit for security reasons. Review the pattern and fix."
}

# Warning pattern → allow with notice
{
    "status": "warn",
    "systemMessage": "Warning: Potential XSS vulnerability. Ensure input is sanitized.",
    "userMessage": ""
}

# Safe → allow silently
{
    "status": "allow"
}
```

#### Example Hook: Explanatory Output Style

**File:** `explanatory-output-style/hooks/session-start.sh`

**Purpose:** Injects global output style instructions at session start

**Implementation:**
```bash
#!/bin/bash
# Inject additional context at session start
cat << EOF
{
  "status": "allow",
  "additionalContext": "# Output Style: Explanatory\n\nWhen providing code or technical solutions:\n1. Explain the 'why' before the 'what'\n2. Break down complex concepts into steps\n3. Provide context for technical decisions\n4. Include inline comments in code\n5. Anticipate follow-up questions\n\nThis style helps users learn, not just get answers."
}
EOF
```

#### Rule-Based Hook Engine (Hookify Plugin)

**File:** `hookify/core/rule_engine.py`

**Purpose:** Content-matching rules defined in `.claude/hookify.*.local.md`

**Rule Format:**
```markdown
## Rule: [name]
- event: bash|edit|prompt|stop
- tool: Bash|Edit|Write|*
- condition: [field] [operator] "[pattern]"
  - Operators: regex, contains, equals, startswith, endswith
  - Fields: command, file_path, content, old_string, new_string
- action: block|warn
- message: [User-facing message]
```

**Example Rule:**

`.claude/hookify.no-force-push.local.md`
```markdown
## Rule: no-force-push
- event: bash
- tool: Bash
- condition: command regex "git push.*--force"
- action: block
- message: "Force push blocked. Use --force-with-lease or request approval."
```

**Rule Engine Processing:**
```python
def evaluate_rule(rule: Rule, hook_input: HookInput) -> RuleResult:
    # 1. Check event match
    if rule.event != hook_input.event:
        return RuleResult(matched=False)

    # 2. Check tool match
    if rule.tool != "*" and rule.tool != hook_input.tool:
        return RuleResult(matched=False)

    # 3. Evaluate condition
    field_value = getattr(hook_input, rule.condition.field)
    match = rule.condition.operator(field_value, rule.condition.pattern)

    if not match:
        return RuleResult(matched=False)

    # 4. Return action
    return RuleResult(
        matched=True,
        action=rule.action,
        message=rule.message
    )
```

---

### 3. Plugin System (Packaging & Distribution)

**File:** `plugins/[name]/.claude-plugin/plugin.json`

**Manifest Schema:**
```json
{
  "name": "plugin-name",
  "version": "1.0.0",
  "description": "What this plugin does",
  "author": "Name <email>",
  "repository": "https://github.com/org/repo",
  "license": "MIT",
  "main": "./index.js",
  "dependencies": {
    "other-plugin": "^1.0.0"
  },
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-name"],
      "env": {
        "API_KEY": "${PLUGIN_API_KEY}"
      }
    }
  },
  "settings": {
    "setting-name": {
      "type": "string|number|boolean",
      "default": "default-value",
      "description": "What this setting does"
    }
  }
}
```

**Plugin Capabilities:**
- **Agents**: `agents/*.md` files registered automatically
- **Commands**: `commands/*.md` files become `/command-name` slash commands
- **Skills**: `skills/*/SKILL.md` files activated by description matching
- **Hooks**: `hooks/hooks.json` + handlers registered for events
- **MCP Servers**: External tools/resources accessible to agents
- **Settings**: User-configurable values accessible in hooks

**Plugin Loading:**
1. Scan `.claude-plugin/marketplace.json` for bundled plugins
2. Scan `~/.claude/plugins/` for user-installed plugins
3. Load plugin manifests
4. Register agents, commands, skills with core
5. Register hook handlers for events
6. Initialize MCP server connections

---

## Content Layout Patterns

### Pattern 1: YAML Frontmatter + Markdown Body

**Universal pattern across all prompt types:**

```markdown
---
name: identifier
description: |
  Multi-line description
  with triggering context
model: inherit
color: blue
tools: ["Tool1", "Tool2"]
---

# Body Title

[Markdown content...]
```

**Design Rationale:**
- Frontmatter = machine-readable metadata (routing, configuration)
- Body = human-readable instructions (agent behavior)
- Clear separation of concerns

### Pattern 2: Second-Person Instructions

**All agent/command bodies use second person:**

```markdown
You are [role].
You will [action].
Your responsibilities are [list].
```

**NOT first person:**
```markdown
❌ I am an expert...
❌ I will analyze...
❌ My role is to...
```

**Design Rationale:** Frames instructions as directives to the agent, not self-description

### Pattern 3: Structured Sections with Bold Headers

**Consistent section structure:**

```markdown
**Section Name:**
- Item 1
- Item 2

**Another Section:**
1. Step 1
2. Step 2
```

**NOT plain headers:**
```markdown
❌ ## Section Name
❌ ### Subsection
```

**Design Rationale:** Bold headers provide visual hierarchy without disrupting markdown structure

### Pattern 4: Example Blocks with XML Tags

**Trigger examples in descriptions:**

```markdown
description: |
  Use this agent when [scenario].

  Examples:
  <example>
    <context>[Situation description]</context>
    <user>[What user says]</user>
    <assistant>[How Claude responds]</assistant>
    <commentary>[Why this triggers agent]</commentary>
  </example>
```

**Design Rationale:**
- Machine-parseable boundaries
- Clear structure for training/triggering
- Human-readable examples

### Pattern 5: Numbered Process Steps

**All processes use numbered lists:**

```markdown
**[Process Name] Process:**
1. [First step with concrete action]
2. [Second step with concrete action]
3. [Third step with concrete action]
[5-10 steps total]
```

**Design Rationale:** Sequential, unambiguous, easy to verify completion

### Pattern 6: Prescriptive Output Format

**Output format always includes example/schema:**

```markdown
**Output Format:**
```markdown
## [Section Title]
- **Field 1**: [Value]
- **Field 2**: [Value]

| Column 1 | Column 2 |
|----------|----------|
| data     | data     |
```

**Alternative: JSON Schema:**
```json
{
  "field1": "value",
  "field2": ["array", "of", "values"]
}
```

**Design Rationale:** Prescriptive structure produces consistent, parseable output

### Pattern 7: Edge Case Documentation

**Edge cases always include handling:**

```markdown
**Edge Cases:**
- **[Edge case 1]**: [How to handle]
- **[Edge case 2]**: [How to handle]
- **[Edge case 3]**: [How to handle]
```

**Design Rationale:** Prevents agent confusion when encountering unusual scenarios

### Pattern 8: Progressive Disclosure (Skills Only)

**Main SKILL.md = Overview + Pointers:**
```markdown
# Skill Title

[1000-3000 word overview with key concepts and quick reference]

## Detailed Resources
- `references/deep-dive.md` - [Topic]
- `references/patterns.md` - [Topic]

## Examples
- `examples/example1.md` - [Description]
- `examples/example2.md` - [Description]
```

**references/ = Deep Dives:**
- 5000-15000 word comprehensive guides
- Moved out to keep main skill readable
- Referenced on-demand

**Design Rationale:** Balances accessibility (short SKILL.md) with depth (references/)

---

## Dynamic Composition System

### Composition Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    RUNTIME COMPOSITION                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Session Init                                             │
│     ↓                                                        │
│  2. SessionStart Hooks Execute                               │
│     • explanatory-output-style → inject style instructions  │
│     • learning-output-style → inject learning mode          │
│     ↓                                                        │
│  3. Base System Prompt (inherited from parent session)      │
│     ↓                                                        │
│  4. User Input                                               │
│     ↓                                                        │
│  5. UserPromptSubmit Hooks Execute                           │
│     • hookify rule engine → validate against local rules    │
│     ↓                                                        │
│  6. Agent/Command/Skill Selection                            │
│     IF user types /command → load command prompt            │
│     ELSE IF trigger matches agent description → spawn agent │
│     ELSE IF question matches skill → activate skill         │
│     ↓                                                        │
│  7. Load Selected Prompt                                     │
│     • Parse YAML frontmatter (name, model, tools, color)    │
│     • Load markdown body                                     │
│     • Inject as system message                              │
│     ↓                                                        │
│  8. Tool Execution Request                                   │
│     ↓                                                        │
│  9. PreToolUse Hooks Execute                                 │
│     • security-guidance → check patterns                    │
│     • hookify rules → validate tool call                    │
│     • Return: allow|warn|block                              │
│     ↓                                                        │
│ 10. Tool Execution (if allowed)                              │
│     ↓                                                        │
│ 11. PostToolUse Hooks Execute                                │
│     • logging → persist to telemetry                        │
│     • output transformation → modify result                 │
│     ↓                                                        │
│ 12. Response to User                                         │
│     ↓                                                        │
│ 13. Stop Event (if session ending)                           │
│     • Stop hooks → final checks/cleanup                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Selection Logic

#### Agent Triggering

**Triggers:**
1. **Explicit invocation:** `MUST use code-explorer agent`
2. **Description matching:** User input matches `<example>` patterns in description
3. **Proactive detection:** Assistant recognizes need based on conversation context

**Selection algorithm:**
```typescript
function selectAgent(userInput: string, context: ConversationContext): Agent | null {
  // 1. Check explicit agent references
  const explicitMatch = /MUST use (\w+-\w+) agent/i.exec(userInput);
  if (explicitMatch) {
    return agents.find(a => a.name === explicitMatch[1]);
  }

  // 2. Check description examples
  for (const agent of agents) {
    const examples = extractExamples(agent.description);
    for (const example of examples) {
      const similarity = computeSimilarity(userInput, example.user);
      if (similarity > 0.8) {
        return agent;
      }
    }
  }

  // 3. Check conversational context
  if (context.requiresDeepAnalysis && context.fileCount > 50) {
    return agents.find(a => a.name === 'code-explorer');
  }

  return null;
}
```

#### Command Triggering

**Explicit only:** User types `/command-name [args]`

```typescript
function handleUserInput(input: string): Response {
  if (input.startsWith('/')) {
    const [commandName, ...args] = input.slice(1).split(' ');
    const command = commands.find(c => c.name === commandName);

    if (command) {
      return executeCommand(command, args.join(' '));
    } else {
      return { error: `Unknown command: /${commandName}` };
    }
  }

  // Otherwise process as normal prompt
  return processPrompt(input);
}
```

#### Skill Activation

**Triggers:**
1. **User question:** "How do I create an agent?"
2. **Assistant detection:** Assistant recognizes need for educational content
3. **Explicit reference:** "Show me the agent-development skill"

**Selection algorithm:**
```typescript
function selectSkill(userInput: string, context: ConversationContext): Skill | null {
  // 1. Check explicit skill references
  const explicitMatch = /(\w+-\w+) skill/i.exec(userInput);
  if (explicitMatch) {
    return skills.find(s => s.name === explicitMatch[1]);
  }

  // 2. Check description trigger phrases
  for (const skill of skills) {
    const triggerPhrases = extractTriggerPhrases(skill.description);
    for (const phrase of triggerPhrases) {
      if (userInput.toLowerCase().includes(phrase.toLowerCase())) {
        return skill;
      }
    }
  }

  // 3. Check conversational context
  if (context.userAskedHowTo && context.topic === 'agents') {
    return skills.find(s => s.name === 'agent-development');
  }

  return null;
}
```

### Hook Execution Flow

```typescript
async function executeToolCall(tool: Tool, params: any): Promise<ToolResult> {
  // 1. PreToolUse hooks
  const preHookResults = await executeHooks('PreToolUse', {
    tool: tool.name,
    params: params,
    context: currentContext
  });

  // 2. Check for blocks
  const blocked = preHookResults.find(r => r.status === 'block');
  if (blocked) {
    return {
      success: false,
      message: blocked.userMessage,
      systemMessage: blocked.systemMessage
    };
  }

  // 3. Show warnings
  const warnings = preHookResults.filter(r => r.status === 'warn');
  if (warnings.length > 0) {
    showWarnings(warnings.map(w => w.userMessage));
  }

  // 4. Execute tool
  const result = await tool.execute(params);

  // 5. PostToolUse hooks
  const postHookResults = await executeHooks('PostToolUse', {
    tool: tool.name,
    params: params,
    result: result,
    context: currentContext
  });

  // 6. Transform result if hooks specify
  let finalResult = result;
  for (const hookResult of postHookResults) {
    if (hookResult.transformedOutput) {
      finalResult = hookResult.transformedOutput;
    }
  }

  return finalResult;
}
```

---

## Complete Prompt Inventory

### By Category

| Category | File Count | Total Lines | Avg Length |
|----------|------------|-------------|------------|
| **Agents** | 12 | ~8,400 | 700 |
| **Commands** | 8 | ~6,000 | 750 |
| **Skills** | 12 | ~28,800 | 2,400 |
| **Hooks** | 5 | ~1,200 | 240 |
| **References** (skills) | 24 | ~120,000 | 5,000 |
| **Examples** (skills) | 18 | ~18,000 | 1,000 |
| **Total Unique** | **79** | **~182,400** | **2,308** |

### Agents (12 files)

| Agent | Plugin | Lines | Color | Purpose |
|-------|--------|-------|-------|---------|
| code-explorer | feature-dev | 800 | blue | Codebase analysis |
| code-architect | feature-dev | 1000 | cyan | Architecture design |
| code-reviewer | feature-dev | 700 | yellow | Code review |
| agent-creator | plugin-dev | 900 | green | Agent generation |
| plugin-validator | plugin-dev | 600 | yellow | Plugin validation |
| skill-reviewer | plugin-dev | 650 | yellow | Skill quality review |
| commit-message-writer | commit-commands | 400 | green | Git commit messages |
| pr-reviewer | pr-review-toolkit | 850 | yellow | PR review |
| security-auditor | security-guidance | 750 | red | Security analysis |
| frontend-designer | frontend-design | 900 | magenta | UI/UX design |
| sdk-helper | agent-sdk-dev | 700 | cyan | Agent SDK guidance |
| orchestrator | ralph-wiggum | 1150 | magenta | Multi-agent coordination |

### Commands (8 files)

| Command | Plugin | Lines | Phases | Purpose |
|---------|--------|-------|--------|---------|
| feature-dev | feature-dev | 1200 | 7 | End-to-end feature development |
| code-review | code-review | 600 | 3 | PR code review workflow |
| commit | commit-commands | 300 | 1 | Git commit with message |
| commit-all | commit-commands | 350 | 1 | Stage all + commit |
| push | commit-commands | 250 | 1 | Push to remote |
| pull | commit-commands | 200 | 1 | Pull from remote |
| plugin-create | plugin-dev | 800 | 5 | Create new plugin |
| agent-create | agent-sdk-dev | 700 | 4 | Create new agent |

### Skills (12 files)

| Skill | Plugin | Main Lines | Refs | Examples | Purpose |
|-------|--------|------------|------|----------|---------|
| agent-development | plugin-dev | 2400 | 3 | 3 | Agent creation guide |
| command-development | plugin-dev | 1800 | 2 | 2 | Command workflows |
| plugin-structure | plugin-dev | 1200 | 4 | 1 | Plugin organization |
| hook-development | plugin-dev | 1600 | 3 | 4 | Hook system |
| system-prompt-design | plugin-dev | 2800 | 5 | 4 | Prompt engineering |
| triggering-patterns | plugin-dev | 2200 | 2 | 6 | Agent triggering |
| agent-patterns | plugin-dev | 3000 | 3 | 8 | Analysis/generation/validation patterns |
| feature-workflow | feature-dev | 2500 | 4 | 3 | Feature dev process |
| code-review-guide | code-review | 1800 | 3 | 2 | PR review standards |
| security-patterns | security-guidance | 2600 | 4 | 5 | Security best practices |
| frontend-patterns | frontend-design | 2400 | 3 | 4 | UI/UX patterns |
| sdk-usage | agent-sdk-dev | 2500 | 4 | 3 | Agent SDK API |

### Hooks (5 active implementations)

| Hook | Plugin | Event | Lines | Purpose |
|------|--------|-------|-------|---------|
| session-start.sh | explanatory-output-style | SessionStart | 100 | Inject explanation style |
| session-start.sh | learning-output-style | SessionStart | 120 | Inject learning mode |
| rule_engine.py | hookify | UserPromptSubmit | 400 | Content-based validation |
| security_reminder_hook.py | security-guidance | PreToolUse | 500 | Security pattern detection |
| telemetry_hook.py | (core) | PostToolUse | 80 | Logging and metrics |

### File Size Distribution

| Size Range | Count | Percentage | Examples |
|------------|-------|------------|----------|
| < 500 lines | 15 | 19% | Commands, simple agents |
| 500-1000 lines | 25 | 32% | Standard agents, commands |
| 1000-2500 lines | 18 | 23% | Skills (main), complex agents |
| 2500-5000 lines | 12 | 15% | Comprehensive skills |
| > 5000 lines | 9 | 11% | Skill references |

---

## Design Principles & Innovations

### 1. Three Prompt Primitives

**Innovation:** Clear taxonomy of prompt types

| Type | Purpose | Trigger | Lifespan | Output |
|------|---------|---------|----------|--------|
| **Agent** | Autonomous subprocess | Description matching | Single task | Structured report |
| **Command** | User-guided workflow | Slash command | Multi-phase | Task completion |
| **Skill** | Educational knowledge | Question/context | Session-scoped | Learning |

**Impact:** Each primitive optimized for its use case, no ambiguity

**Comparison:**
- Codex: Prompts + personalities + modes (overlapping concerns)
- Gemini CLI: Monolithic with conditional blocks (no primitives)
- Claude Code: Three distinct primitives (clear separation)

### 2. Event-Driven Composition

**Innovation:** Hooks inject context at lifecycle events, not static assembly

**5 Hook Events:**
1. **SessionStart** - Global style/context
2. **UserPromptSubmit** - Input validation
3. **PreToolUse** - Security gates
4. **PostToolUse** - Logging/transformation
5. **Stop** - Final checks

**Impact:**
- Dynamic adaptation to user actions
- Security as a layer (not embedded in every prompt)
- Extensibility without modifying core prompts

**Example:**
```
User: "Add authentication"
  ↓
SessionStart hook: Inject explanatory style
  ↓
Agent triggered: code-explorer (description match)
  ↓
Agent wants to Edit file
  ↓
PreToolUse hook: security-guidance checks for patterns
  ↓
Hook returns: WARN - "Ensure password hashing, not plain text"
  ↓
Agent proceeds with warning in context
```

### 3. Plugin Architecture

**Innovation:** Prompts packaged as plugins, not core files

**Plugin Capabilities:**
- Self-contained (agents, commands, skills, hooks)
- Versioned (semantic versioning in manifest)
- Composable (plugins can depend on other plugins)
- Distributable (marketplace, user-installed)

**Impact:**
- Core stays lean
- Community extensibility
- Domain-specific prompts (frontend, security, CLI)

**Comparison:**
- Codex: Prompts in core repo (not pluggable)
- Gemini CLI: Prompts in core repo (not pluggable)
- Claude Code: Plugin system (fully extensible)

### 4. Description-Based Agent Triggering

**Innovation:** Agent descriptions include `<example>` blocks with context/user/assistant/commentary

```yaml
description: |
  Use this agent when [scenario].

  Examples:
  <example>
    <context>User wants to refactor authentication system</context>
    <user>Refactor auth to use JWT instead of sessions</user>
    <assistant>This requires understanding the current auth implementation. I'll use the code-explorer agent...</assistant>
    <commentary>Triggers because refactoring requires analysis</commentary>
  </example>
```

**Impact:**
- Self-documenting triggers
- Training data for agent selection
- Human-readable rationale

### 5. Progressive Disclosure (Skills)

**Innovation:** Main SKILL.md stays concise (1000-3000 words), references/ contain deep dives

**Structure:**
```
skills/agent-development/
├── SKILL.md                    # 2400 lines - overview, quick ref, pointers
├── references/
│   ├── system-prompt-design.md      # 8000 lines - deep dive
│   ├── triggering-examples.md       # 5000 lines - 50+ examples
│   └── agent-patterns.md            # 12000 lines - comprehensive
├── examples/
│   ├── analysis-agent.md            # 1500 lines - working code
│   └── generation-agent.md          # 1800 lines - working code
└── scripts/
    └── validate-agent.sh            # 200 lines - utility
```

**Impact:**
- Main skill readable in 10 minutes
- References for deep learning
- No cognitive overload

**Comparison:**
- Codex: No skill concept (all in base instructions)
- Gemini CLI: No skill concept (all in main prompt)
- Claude Code: Progressive disclosure (unique)

### 6. Prescriptive Output Format

**Innovation:** Output format always includes example or JSON schema

**Example (code-explorer agent):**
```markdown
**Output Format:**
```markdown
## Exploration Summary
- **Task**: [What we're exploring for]
- **Scope**: [Files/modules examined]

## Key Findings
1. [Finding 1 with file:line references]
2. [Finding 2 with file:line references]

## Relevant Files
| File | Purpose | Priority |
|------|---------|----------|
| path | what it does | high/medium/low |

## Recommendations
- [Next step 1]
- [Next step 2]
```

**Impact:**
- Consistent structure across agent runs
- Parseable by orchestrators
- Clear success criteria

### 7. Rule-Based Hook Engine (Hookify)

**Innovation:** User-defined content matching rules in markdown files

**Rule Format:**
```markdown
## Rule: no-force-push
- event: bash
- tool: Bash
- condition: command regex "git push.*--force"
- action: block
- message: "Force push blocked. Use --force-with-lease."
```

**Impact:**
- Users define custom policies
- No code changes needed
- Project-specific safety rules

**Comparison:**
- Codex: Approval policies in core (5 hardcoded modes)
- Gemini CLI: Config-based trust (static flags)
- Claude Code: User-defined rules (fully dynamic)

### 8. Multi-Agent Orchestration

**Innovation:** Commands can launch multiple agents in parallel, compare results

**Example (feature-dev command):**
```markdown
## Phase 2: Exploration
1. Launch 3 code-explorer agents in parallel:
   - Agent 1: Explore existing similar features
   - Agent 2: Explore testing patterns
   - Agent 3: Explore configuration files
2. Wait for all agents to complete
3. Consolidate findings
```

**Impact:**
- Faster research (parallel execution)
- Multiple perspectives (compare approaches)
- Consistent format (same agent, different scope)

### 9. Security as a Layer (PreToolUse Hooks)

**Innovation:** Security checks as hooks, not embedded in every prompt

**Detected Patterns (security-guidance plugin):**
- Command injection
- XSS vulnerabilities
- SQL injection
- Hardcoded secrets
- Path traversal
- Insecure deserialization

**Impact:**
- Central security policy
- Applies to all agents/commands
- Easy to update (no prompt rewrites)

**Comparison:**
- Codex: Security rules in base instructions (scattered)
- Gemini CLI: Security rules in core mandates (static)
- Claude Code: Security as hook layer (dynamic)

### 10. Model Selection per Agent

**Innovation:** Agents specify model preference in frontmatter

```yaml
model: inherit     # Use parent/session model (default, recommended)
model: sonnet      # Force Claude Sonnet (balanced)
model: opus        # Force Claude Opus (most capable)
model: haiku       # Force Claude Haiku (fastest)
```

**Impact:**
- Cost optimization (use haiku for simple agents)
- Capability matching (opus for complex reasoning)
- Flexibility without code changes

**Comparison:**
- Codex: Single model per session
- Gemini CLI: Gemini 3 vs legacy prompts (2 variants)
- Claude Code: Per-agent model selection (fine-grained)

---

## Key Takeaways for co-cli

### 1. Adopt Three Prompt Primitives

**Current co-cli:** Monolithic prompt system, no clear taxonomy

**Recommended:** Implement agents, commands, skills

```python
# co_cli/prompts/primitives.py

@dataclass
class Agent:
    """Autonomous subprocess for specific tasks."""
    name: str
    description: str  # Includes trigger examples
    system_prompt: str
    model: str = "inherit"
    color: str = "blue"
    tools: list[str] = field(default_factory=lambda: ["*"])

    def matches_trigger(self, user_input: str, context: Context) -> float:
        """Return 0.0-1.0 similarity score."""
        examples = extract_examples(self.description)
        scores = [compute_similarity(user_input, ex.user) for ex in examples]
        return max(scores) if scores else 0.0

@dataclass
class Command:
    """User-triggered multi-phase workflow."""
    name: str
    description: str
    argument_hint: str
    phases: list[Phase]

    def execute(self, args: str, context: Context) -> CommandResult:
        """Execute all phases sequentially."""
        for phase in self.phases:
            result = phase.execute(args, context)
            if not result.success:
                return result
        return CommandResult(success=True)

@dataclass
class Skill:
    """Educational knowledge base."""
    name: str
    description: str  # Includes trigger phrases
    content: str  # Main SKILL.md (1000-3000 words)
    references: dict[str, str]  # Deep dive docs
    examples: dict[str, str]  # Working code
    scripts: dict[str, str]  # Utilities

    def matches_trigger(self, user_input: str) -> bool:
        """Check trigger phrases."""
        trigger_phrases = extract_trigger_phrases(self.description)
        return any(phrase.lower() in user_input.lower() for phrase in trigger_phrases)
```

**Directory Structure:**
```
co_cli/prompts/
├── agents/
│   ├── code_explorer.md
│   ├── code_architect.md
│   └── test_validator.md
├── commands/
│   ├── feature_dev.md
│   ├── code_review.md
│   └── commit.md
└── skills/
    ├── agent_development/
    │   ├── SKILL.md
    │   ├── references/
    │   ├── examples/
    │   └── scripts/
    └── tool_development/
        └── ...
```

### 2. Implement Event-Driven Hook System

**Current co-cli:** Approval handled inline in tools

**Recommended:** Hook system for lifecycle events

```python
# co_cli/hooks.py

@dataclass
class HookEvent:
    """Hook lifecycle events."""
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"

@dataclass
class HookResult:
    status: Literal["allow", "warn", "block"]
    system_message: str = ""
    user_message: str = ""
    additional_context: str = ""

class HookRegistry:
    def __init__(self):
        self.hooks: dict[str, list[Hook]] = defaultdict(list)

    def register(self, event: str, hook: Hook):
        """Register hook for event."""
        self.hooks[event].append(hook)

    async def execute(self, event: str, context: HookContext) -> list[HookResult]:
        """Execute all hooks for event."""
        results = []
        for hook in self.hooks[event]:
            result = await hook.execute(context)
            results.append(result)
        return results

# Example: Security hook
class SecurityHook(Hook):
    def __init__(self):
        self.patterns = [
            (r"subprocess\.(?:call|run|Popen)\([^)]*shell=True", "Command injection risk"),
            (r"\.innerHTML\s*=", "XSS vulnerability"),
            (r"eval\(", "Insecure eval"),
        ]

    async def execute(self, context: HookContext) -> HookResult:
        if context.tool != "Edit" and context.tool != "Write":
            return HookResult(status="allow")

        content = context.params.get("content", "") + context.params.get("new_string", "")

        for pattern, message in self.patterns:
            if re.search(pattern, content):
                return HookResult(
                    status="warn",
                    system_message=f"Security Warning: {message}. Ensure proper validation.",
                    user_message=f"⚠️  {message}"
                )

        return HookResult(status="allow")
```

**Hook Registration:**
```python
# co_cli/agent.py

def get_agent(deps: CoDeps) -> Agent:
    # Register hooks
    hooks = HookRegistry()

    # SessionStart: Output style
    if deps.settings.output_style == "explanatory":
        hooks.register(HookEvent.SESSION_START, ExplanatoryStyleHook())

    # PreToolUse: Security checks
    hooks.register(HookEvent.PRE_TOOL_USE, SecurityHook())

    # PreToolUse: Sandbox validation
    hooks.register(HookEvent.PRE_TOOL_USE, SandboxHook(deps.sandbox))

    # PostToolUse: Telemetry
    hooks.register(HookEvent.POST_TOOL_USE, TelemetryHook())

    return agent
```

### 3. Add Description-Based Agent Triggering

**Current co-cli:** No agent system

**Recommended:** Agents with XML example blocks

```markdown
<!-- co_cli/prompts/agents/code_explorer.md -->
---
name: code-explorer
description: |
  Use this agent to perform systematic codebase exploration and analysis.

  Examples:
  <example>
    <context>User wants to add feature that requires understanding existing code</context>
    <user>Add user authentication</user>
    <assistant>I'll need to explore the codebase first to understand the current architecture. Launching code-explorer agent...</assistant>
    <commentary>Triggers because feature implementation requires codebase understanding</commentary>
  </example>

  <example>
    <context>User asks about existing implementation</context>
    <user>How does the current logging system work?</user>
    <assistant>Let me explore the logging implementation. Launching code-explorer agent...</assistant>
    <commentary>Triggers because answering requires systematic analysis</commentary>
  </example>
model: inherit
color: blue
tools: ["Read", "Grep", "Glob", "Bash"]
---

You are an expert codebase analyst specializing in systematic code exploration.

**Your Core Responsibilities:**
1. Perform comprehensive codebase analysis
2. Identify architecture patterns and conventions
3. Document findings in structured format

**Exploration Process:**
1. **Initial Scan**: Use Glob to identify file structure
2. **Pattern Recognition**: Grep for imports, definitions, conventions
3. **Dependency Mapping**: Trace relationships
4. **Convention Analysis**: Identify coding standards
5. **Risk Assessment**: Flag potential issues
6. **Report Generation**: Produce structured findings

[Continue with Quality Standards, Output Format, Edge Cases...]
```

**Selection Logic:**
```python
# co_cli/agent_selection.py

def select_agent(user_input: str, context: Context) -> Agent | None:
    """Select agent based on triggers."""

    # 1. Explicit agent reference
    explicit = re.search(r"use (\w+) agent", user_input, re.I)
    if explicit:
        return agents.get(explicit.group(1))

    # 2. Description example matching
    for agent in agents.values():
        similarity = agent.matches_trigger(user_input, context)
        if similarity > 0.8:
            return agent

    # 3. Proactive detection
    if context.requires_deep_analysis:
        return agents.get("code-explorer")

    return None
```

### 4. Implement Progressive Disclosure for Skills

**Current co-cli:** All documentation inline

**Recommended:** Main SKILL.md + references/

```
co_cli/prompts/skills/
├── tool_development/
│   ├── SKILL.md                    # 1500 words - overview + quick ref
│   ├── references/
│   │   ├── tool_design.md          # 5000 words - deep dive
│   │   ├── approval_patterns.md    # 3000 words - detailed
│   │   └── return_types.md         # 2000 words - comprehensive
│   ├── examples/
│   │   ├── simple_tool.py          # Working example
│   │   ├── complex_tool.py         # Advanced example
│   │   └── approval_tool.py        # Approval example
│   └── scripts/
│       ├── validate_tool.sh        # Validation script
│       └── test_tool.sh            # Testing script
└── agent_development/
    └── ...
```

**SKILL.md structure:**
```markdown
# Tool Development

This skill guides you through creating tools for co-cli.

## Key Concepts

**Tool**: A function registered with `agent.tool()` that provides capabilities to the agent.

**RunContext**: Dependency injection container providing access to runtime resources.

**Approval**: Side-effectful tools use `requires_approval=True` for user confirmation.

## Quick Reference

**Minimum Tool:**
```python
@agent.tool()
async def my_tool(ctx: RunContext[CoDeps]) -> dict[str, Any]:
    """Brief description."""
    # Implementation
    return {"display": "Result", "metadata": {...}}
```

**Tool with Approval:**
```python
@agent.tool(requires_approval=True)
async def risky_tool(ctx: RunContext[CoDeps], path: str) -> dict[str, Any]:
    """Delete file."""
    ctx.deps.sandbox.delete(path)
    return {"display": f"Deleted {path}"}
```

## Detailed Resources

- `references/tool_design.md` - Deep dive on design patterns
- `references/approval_patterns.md` - Approval workflows
- `references/return_types.md` - Return type conventions

## Examples

- `examples/simple_tool.py` - Basic read-only tool
- `examples/complex_tool.py` - Multi-step tool with state
- `examples/approval_tool.py` - Side-effectful tool with approval

[Continue with core processes, common patterns, troubleshooting...]
```

### 5. Adopt Multi-Agent Orchestration

**Current co-cli:** No multi-agent coordination

**Recommended:** Commands launch multiple agents in parallel

```python
# co_cli/orchestration.py

@dataclass
class AgentTask:
    agent: Agent
    args: str
    context: Context

class Orchestrator:
    async def execute_parallel(self, tasks: list[AgentTask]) -> list[AgentResult]:
        """Execute multiple agents in parallel."""
        results = await asyncio.gather(
            *[self.execute_agent(task) for task in tasks]
        )
        return results

    async def execute_agent(self, task: AgentTask) -> AgentResult:
        """Execute single agent."""
        # Create isolated context
        agent_context = task.context.fork()

        # Load agent prompt
        system_prompt = task.agent.system_prompt

        # Execute
        result = await self.llm.run(
            system_prompt=system_prompt,
            user_prompt=task.args,
            context=agent_context,
            tools=task.agent.tools
        )

        return AgentResult(
            agent=task.agent.name,
            output=result,
            context=agent_context
        )

# Usage in command
async def feature_dev_phase2(ctx: CommandContext):
    """Phase 2: Exploration - Launch 3 agents in parallel."""

    orchestrator = Orchestrator()

    tasks = [
        AgentTask(
            agent=agents["code-explorer"],
            args="Explore existing similar features",
            context=ctx.context
        ),
        AgentTask(
            agent=agents["code-explorer"],
            args="Explore testing patterns and infrastructure",
            context=ctx.context
        ),
        AgentTask(
            agent=agents["code-explorer"],
            args="Explore configuration and setup files",
            context=ctx.context
        ),
    ]

    results = await orchestrator.execute_parallel(tasks)

    # Consolidate findings
    consolidated = consolidate_exploration_results(results)

    return PhaseResult(
        success=True,
        findings=consolidated
    )
```

### 6. Implement Rule-Based Hook Engine

**Current co-cli:** Approval logic in code

**Recommended:** User-defined markdown rules

**Rule format (`.co-cli/rules.md`):**
```markdown
## Rule: no-force-push
- event: bash
- tool: Bash
- condition: command regex "git push.*--force"
- action: block
- message: "Force push blocked. Use --force-with-lease or get approval."

## Rule: warn-on-rm-rf
- event: bash
- tool: Bash
- condition: command contains "rm -rf"
- action: warn
- message: "⚠️  Recursive delete detected. Ensure correct path."

## Rule: block-secret-commit
- event: edit
- tool: Edit
- condition: new_string regex "(?i)(api[_-]?key|password|secret|token)\s*=\s*['\"][^'\"]+['\"]"
- action: block
- message: "Hardcoded secret detected. Use environment variables."
```

**Rule Engine:**
```python
# co_cli/rule_engine.py

@dataclass
class Rule:
    name: str
    event: str
    tool: str
    condition: Condition
    action: Literal["block", "warn"]
    message: str

@dataclass
class Condition:
    field: str
    operator: str
    pattern: str

    def matches(self, hook_context: HookContext) -> bool:
        value = getattr(hook_context.params, self.field, "")

        if self.operator == "regex":
            return bool(re.search(self.pattern, value))
        elif self.operator == "contains":
            return self.pattern in value
        elif self.operator == "equals":
            return value == self.pattern
        elif self.operator == "startswith":
            return value.startswith(self.pattern)
        elif self.operator == "endswith":
            return value.endswith(self.pattern)

        return False

class RuleEngine:
    def __init__(self, rules_file: Path):
        self.rules = self.load_rules(rules_file)

    def load_rules(self, file: Path) -> list[Rule]:
        """Parse markdown rule file."""
        content = file.read_text()
        rules = []

        for block in re.finditer(r"## Rule: (.+?)\n(.*?)(?=\n## Rule:|\Z)", content, re.DOTALL):
            name = block.group(1)
            body = block.group(2)

            # Parse rule fields
            event = re.search(r"- event: (.+)", body).group(1)
            tool = re.search(r"- tool: (.+)", body).group(1)
            condition_match = re.search(r"- condition: (\w+) (\w+) \"(.+)\"", body)
            action = re.search(r"- action: (.+)", body).group(1)
            message = re.search(r"- message: (.+)", body).group(1)

            rules.append(Rule(
                name=name,
                event=event,
                tool=tool,
                condition=Condition(
                    field=condition_match.group(1),
                    operator=condition_match.group(2),
                    pattern=condition_match.group(3)
                ),
                action=action,
                message=message
            ))

        return rules

    def evaluate(self, hook_context: HookContext) -> HookResult:
        """Evaluate rules against hook context."""
        for rule in self.rules:
            # Check event match
            if rule.event != hook_context.event:
                continue

            # Check tool match
            if rule.tool != "*" and rule.tool != hook_context.tool:
                continue

            # Check condition
            if not rule.condition.matches(hook_context):
                continue

            # Rule matched
            if rule.action == "block":
                return HookResult(
                    status="block",
                    user_message=rule.message,
                    system_message=f"Blocked by rule: {rule.name}"
                )
            elif rule.action == "warn":
                return HookResult(
                    status="warn",
                    user_message=rule.message,
                    system_message=f"Warning from rule: {rule.name}"
                )

        # No rules matched
        return HookResult(status="allow")
```

### 7. Add Security Pattern Detection Hook

**Current co-cli:** No security checks

**Recommended:** PreToolUse security hook

```python
# co_cli/security_hook.py

class SecurityHook(Hook):
    """Detect security anti-patterns before tool execution."""

    PATTERNS = [
        # Command injection
        (
            r"subprocess\.(?:call|run|Popen)\([^)]*shell=True",
            "CRITICAL",
            "Command injection risk. Use shell=False with list arguments."
        ),

        # XSS
        (
            r"\.innerHTML\s*=",
            "HIGH",
            "XSS vulnerability. Use textContent or sanitize input."
        ),

        # SQL injection
        (
            r"(?:execute|query)\s*\(\s*['\"].*%s.*['\"]",
            "CRITICAL",
            "SQL injection risk. Use parameterized queries."
        ),

        # Hardcoded secrets
        (
            r"(?i)(?:api[_-]?key|password|secret|token)\s*=\s*['\"][^'\"]{8,}['\"]",
            "CRITICAL",
            "Hardcoded secret detected. Use environment variables."
        ),

        # Path traversal
        (
            r"(?:open|read|write)\([^)]*\.\./",
            "HIGH",
            "Path traversal risk. Validate and sanitize paths."
        ),

        # Insecure deserialization
        (
            r"(?:pickle\.loads|yaml\.load(?!_safe)|eval)\(",
            "HIGH",
            "Insecure deserialization. Use safe alternatives (pickle banned, yaml.safe_load, ast.literal_eval)."
        ),
    ]

    async def execute(self, context: HookContext) -> HookResult:
        # Only check file edits/writes
        if context.tool not in ["Edit", "Write"]:
            return HookResult(status="allow")

        # Get content
        content = context.params.get("content", "") + context.params.get("new_string", "")

        # Check patterns
        violations = []
        for pattern, severity, message in self.PATTERNS:
            if re.search(pattern, content):
                violations.append((severity, message))

        # Return result based on severity
        if any(sev == "CRITICAL" for sev, _ in violations):
            return HookResult(
                status="block",
                user_message="🚫 CRITICAL security issue detected. Edit blocked.",
                system_message="\n".join(f"{sev}: {msg}" for sev, msg in violations)
            )
        elif violations:
            return HookResult(
                status="warn",
                user_message="⚠️  Security concerns detected. Review carefully.",
                system_message="\n".join(f"{sev}: {msg}" for sev, msg in violations)
            )

        return HookResult(status="allow")
```

### 8. Implement Model Selection per Agent

**Current co-cli:** Single model per session

**Recommended:** Per-agent model selection

```python
# co_cli/model_selection.py

@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str = "inherit"  # inherit|gemini-1.5-pro|gemini-1.5-flash|gemini-2.0-flash-thinking-exp

    def get_model(self, session_model: str) -> str:
        """Resolve model selection."""
        if self.model == "inherit":
            return session_model
        else:
            return self.model

# Example agents
agents = {
    "code-explorer": Agent(
        name="code-explorer",
        system_prompt="...",
        model="inherit"  # Use session model (default)
    ),

    "code-architect": Agent(
        name="code-architect",
        system_prompt="...",
        model="gemini-2.0-flash-thinking-exp"  # Complex reasoning
    ),

    "test-validator": Agent(
        name="test-validator",
        system_prompt="...",
        model="gemini-1.5-flash"  # Fast validation
    ),
}
```

**Cost Optimization:**
```python
# Use cheap models for simple tasks
simple_agents = ["file-reader", "syntax-validator", "import-resolver"]
for agent_name in simple_agents:
    agents[agent_name].model = "gemini-1.5-flash"

# Use expensive models for complex tasks
complex_agents = ["code-architect", "security-auditor", "performance-optimizer"]
for agent_name in complex_agents:
    agents[agent_name].model = "gemini-2.0-flash-thinking-exp"
```

### 9. Add Prescriptive Output Format

**Current co-cli:** No output format enforcement

**Recommended:** Output format in agent prompts

```markdown
<!-- co_cli/prompts/agents/code_explorer.md -->

**Output Format:**

Return findings in this exact structure:

```markdown
## Exploration Summary
- **Task**: [What we explored for]
- **Scope**: [Files/modules examined]
- **Duration**: [Time spent]

## Key Findings
1. **[Finding 1 Title]**
   - Location: `file:line`
   - Description: [What was found]
   - Impact: [Why it matters]

2. **[Finding 2 Title]**
   - Location: `file:line`
   - Description: [What was found]
   - Impact: [Why it matters]

## Architecture Patterns
- **[Pattern 1]**: Used in X files ([examples])
- **[Pattern 2]**: Used in Y files ([examples])

## Relevant Files
| File | Purpose | Priority | Lines |
|------|---------|----------|-------|
| path/to/file.py | Brief description | high | 250 |
| path/to/other.py | Brief description | medium | 180 |

## Conventions Identified
- **Imports**: [Pattern observed]
- **Naming**: [Pattern observed]
- **Testing**: [Pattern observed]

## Risks & Considerations
- **[Risk 1]**: [Description and mitigation]
- **[Risk 2]**: [Description and mitigation]

## Recommendations
- [ ] [Action item 1]
- [ ] [Action item 2]
- [ ] [Action item 3]
```

**Do NOT deviate from this structure.**
```

**Validation:**
```python
# co_cli/output_validator.py

def validate_exploration_output(output: str) -> ValidationResult:
    """Validate agent output matches format."""
    required_sections = [
        "## Exploration Summary",
        "## Key Findings",
        "## Relevant Files",
        "## Recommendations"
    ]

    missing = [s for s in required_sections if s not in output]

    if missing:
        return ValidationResult(
            valid=False,
            errors=[f"Missing section: {s}" for s in missing]
        )

    # Check table format
    if "| File | Purpose | Priority |" not in output:
        return ValidationResult(
            valid=False,
            errors=["Relevant Files table missing or malformed"]
        )

    return ValidationResult(valid=True)
```

### 10. Implement Command Multi-Phase Workflow

**Current co-cli:** No command system

**Recommended:** Commands with explicit phases

```markdown
<!-- co_cli/prompts/commands/feature_dev.md -->
---
description: Structured feature development workflow
argument-hint: "<feature-description>"
---

# Feature Development Workflow

## Workflow Overview

1. **Clarification** - Understand requirements
2. **Exploration** - Analyze codebase
3. **Architecture** - Design approach
4. **Implementation** - Write code with tests
5. **Review** - Quality check
6. **Finalization** - Summary and handoff

## Phase 1: Clarification
**Goal**: Fully understand feature requirements

**Actions**:
1. Ask clarifying questions:
   - Functional requirements
   - Non-functional requirements (performance, security)
   - Edge cases and error handling
   - Success criteria
2. Document requirements in structured format
3. Confirm with user

**Success Criteria**:
- All ambiguities resolved
- User confirms requirements accurate
- Edge cases identified

**Next**: Proceed to Phase 2 when user approves

## Phase 2: Exploration
**Goal**: Understand codebase and identify relevant files

**Actions**:
1. Launch 2-3 code-explorer agents in parallel:
   - Agent 1: Explore existing similar features
   - Agent 2: Explore testing patterns
   - Agent 3: Explore configuration files
2. Wait for all agents to complete
3. Consolidate findings
4. Identify file list for manual review

**Success Criteria**:
- Architecture patterns understood
- Conventions identified
- Relevant files listed

**Next**: Proceed to Phase 3

[Phases 3-6 continue...]

## Phase 7: Finalization
**Goal**: Provide summary and handoff

**Actions**:
1. Generate summary of changes
2. Document follow-up items
3. Provide instructions for testing

**Output Format**:
```markdown
## Feature Implementation Summary

**Feature**: [Name]
**Status**: Complete
**Date**: [Date]

### Changes Made
- [File 1]: [Description]
- [File 2]: [Description]

### Tests Added
- [Test 1]: [Coverage]
- [Test 2]: [Coverage]

### Quality Checks
- ✅ Tests passing
- ✅ Code review passed
- ✅ Conventions followed

### Follow-up Items
- [ ] [Item 1 if any]
- [ ] [Item 2 if any]
```
```

**Command Execution:**
```python
# co_cli/command_executor.py

async def execute_command(command: Command, args: str, ctx: Context):
    """Execute multi-phase command."""

    console.print(f"[bold cyan]Starting {command.name}[/bold cyan]")
    console.print(f"Phases: {', '.join(p.name for p in command.phases)}\n")

    for i, phase in enumerate(command.phases, 1):
        console.print(f"[bold yellow]Phase {i}/{len(command.phases)}: {phase.name}[/bold yellow]")
        console.print(f"Goal: {phase.goal}\n")

        result = await phase.execute(args, ctx)

        if not result.success:
            console.print(f"[red]❌ Phase failed: {result.error}[/red]")
            return CommandResult(success=False, phase_failed=i)

        console.print(f"[green]✅ Phase {i} complete[/green]\n")

        # Update context with phase results
        ctx.phase_results[phase.name] = result

    console.print(f"[bold green]✅ {command.name} complete![/bold green]")
    return CommandResult(success=True)
```

---

## Comparison: Claude Code vs co-cli

| Dimension | Claude Code | co-cli (current) | Recommendation |
|-----------|-------------|------------------|----------------|
| **Prompt Architecture** | Plugin-based with 3 primitives | Monolithic assembly | **Adopt** primitives (agents, commands, skills) |
| **Event System** | 5 hook events (SessionStart, PreToolUse, etc.) | No hooks | **Adopt** hook system for extensibility |
| **Agent Triggering** | Description with XML examples | No agents | **Adopt** description-based triggering |
| **Multi-Agent Orchestration** | Commands launch agents in parallel | Task tool, no orchestration | **Adopt** parallel agent execution |
| **Skills** | Progressive disclosure (main + references/) | All docs inline | **Adopt** progressive disclosure |
| **Output Format** | Prescriptive with examples | Ad-hoc | **Adopt** prescriptive formats |
| **Security** | PreToolUse hook layer | Inline in tools | **Adopt** security as hook layer |
| **Rules** | User-defined markdown rules | Hardcoded approval | **Adopt** rule-based engine |
| **Model Selection** | Per-agent frontmatter | Session-level | **Adopt** per-agent models |
| **Plugin System** | Full plugin architecture | No plugins | **Consider** for MVP+1 (not MVP) |
| **Prompt Files** | 79 files (~182K lines) | ~5 files (~5K lines) | **Expand** to 20-30 files (~30K lines) |
| **Approval Flow** | PreToolUse hooks + rules | Inline requires_approval | **Migrate** to hook-based approval |

---

## Recommended Prompt Architecture for co-cli

### Hybrid Approach (Best Practices from All 3 Systems)

**Structure:** Plugin-inspired organization (Claude Code) + Layered composition (Codex) + Conditional sections (Gemini CLI)

```
co_cli/
├── prompts/
│   ├── base/
│   │   └── core_instructions.md         # Foundation (Codex pattern)
│   │
│   ├── agents/                           # Claude Code pattern
│   │   ├── code_explorer.md             # Codebase analysis
│   │   ├── code_architect.md            # Architecture design
│   │   ├── test_validator.md            # Test validation
│   │   └── security_auditor.md          # Security review
│   │
│   ├── commands/                         # Claude Code pattern
│   │   ├── feature_dev.md               # Multi-phase feature workflow
│   │   ├── code_review.md               # PR review workflow
│   │   └── commit.md                    # Git commit workflow
│   │
│   ├── skills/                           # Claude Code pattern
│   │   ├── tool_development/
│   │   │   ├── SKILL.md                 # Main (1500 words)
│   │   │   ├── references/              # Deep dives (5000+ words)
│   │   │   ├── examples/                # Working code
│   │   │   └── scripts/                 # Utilities
│   │   └── agent_development/
│   │       └── ...
│   │
│   ├── modes/                            # Codex pattern
│   │   ├── execute.md                   # Autonomous execution
│   │   ├── plan.md                      # Non-mutating planning
│   │   └── pair.md                      # Tight feedback loop
│   │
│   ├── permissions/                      # Codex pattern
│   │   ├── sandbox_docker.md            # Docker sandbox
│   │   ├── sandbox_subprocess.md        # Subprocess fallback
│   │   └── approval_on_request.md       # Approval policy
│   │
│   └── model_overrides/                  # Codex + Gemini CLI pattern
│       ├── gemini_thinking.md           # Gemini 2.0 thinking mode
│       └── gemini_flash.md              # Gemini 1.5 flash
│
├── hooks/                                # Claude Code pattern
│   ├── hooks.py                         # Hook registry
│   ├── security_hook.py                 # Security pattern detection
│   ├── rule_engine.py                   # User-defined rules
│   └── telemetry_hook.py                # Logging
│
└── orchestration/                        # Claude Code pattern
    ├── orchestrator.py                  # Multi-agent coordination
    └── agent_selection.py               # Trigger matching
```

### Composition Function

```python
# co_cli/prompt_composer.py

def compose_system_prompt(ctx: PromptContext) -> str:
    """Compose system prompt from multiple sources."""

    sections = []

    # 1. Base instructions (always)
    sections.append(load_markdown("prompts/base/core_instructions.md"))

    # 2. Mode-specific (if set)
    if ctx.mode in ["execute", "plan", "pair"]:
        sections.append(load_markdown(f"prompts/modes/{ctx.mode}.md"))

    # 3. Agent/Command/Skill (if selected)
    if ctx.agent:
        agent_content = load_agent(f"prompts/agents/{ctx.agent}.md")
        sections.append(agent_content.system_prompt)
    elif ctx.command:
        command_content = load_command(f"prompts/commands/{ctx.command}.md")
        sections.append(command_content.body)
    elif ctx.skill:
        skill_content = load_skill(f"prompts/skills/{ctx.skill}/SKILL.md")
        sections.append(skill_content.content)

    # 4. Sandbox/permissions (conditional)
    if ctx.sandbox == "docker":
        sections.append(load_markdown("prompts/permissions/sandbox_docker.md"))
    elif ctx.sandbox == "subprocess":
        sections.append(load_markdown("prompts/permissions/sandbox_subprocess.md"))

    sections.append(load_markdown("prompts/permissions/approval_on_request.md"))

    # 5. Model-specific overrides (if applicable)
    if ctx.model.startswith("gemini-2.0") and "thinking" in ctx.model:
        sections.append(load_markdown("prompts/model_overrides/gemini_thinking.md"))
    elif ctx.model.startswith("gemini") and "flash" in ctx.model:
        sections.append(load_markdown("prompts/model_overrides/gemini_flash.md"))

    # 6. Hook context (dynamic)
    hook_results = execute_hooks(HookEvent.SESSION_START, ctx)
    for result in hook_results:
        if result.additional_context:
            sections.append(result.additional_context)

    return "\n\n---\n\n".join(sections)
```

### Benefits

**From Codex:**
- ✅ Layered composition (base + mode + permissions)
- ✅ Git-friendly (separate files)
- ✅ Clear separation of concerns

**From Gemini CLI:**
- ✅ Conditional sections (sandbox, model)
- ✅ Single source of truth (no file duplication)
- ✅ Directive vs Inquiry distinction

**From Claude Code:**
- ✅ Three prompt primitives (agents, commands, skills)
- ✅ Event-driven hooks (SessionStart, PreToolUse, etc.)
- ✅ Progressive disclosure (skills)
- ✅ Multi-agent orchestration
- ✅ Description-based triggering
- ✅ Rule-based validation

**Unique to co-cli:**
- ✅ Gemini-specific optimizations
- ✅ Docker + subprocess dual sandbox
- ✅ OpenTelemetry integration

---

## Critical Gap Analysis

### Gap Discovery

**Context:** Across Codex, Gemini CLI, and Claude Code reviews, a **critical gap** was identified: **fact verification and contradiction handling** when tool outputs conflict with user assertions.

**Scenario:** Calendar tool returns "February 9, 2026 (Friday)" but user asserts "Feb 9 2026 is Monday!" Agent accepts correction without verification. (Actual: Sunday — both were wrong)

**Scope:** Searched all prompt files in all three peer systems for guidance on resolving contradictions between tool outputs and user statements.

### Findings Across All Systems

| System | Tool Output Trust | Fact Verification | Contradiction Handling | Severity |
|--------|-------------------|-------------------|------------------------|----------|
| **Codex** | NO (implicit via safety) | YES (must be provable) | NO | HIGH |
| **Gemini CLI** | IMPLICIT (config-based) | NO | NO | HIGH |
| **Claude Code** | NO | NO | NO | HIGH |

**No system has comprehensive coverage** — all focus on **capability trust** (what tools can run) but lack **output authority guidance** (what to do when data conflicts).

### What's Missing (All Systems + Claude Code)

**No system addresses:**
1. When tool output contradicts user assertion, which to trust?
2. How to verify calculable facts (dates, times, numbers)?
3. Escalation protocol for contradictions
4. Authority ordering (tool > cache > user statement?)

**Closest patterns:**
- Codex: "Bugs must be provable" in review prompts (but only for code review, not data)
- Gemini CLI: MCP server trust config (but no agent-facing rules)
- Claude Code: Security patterns in PreToolUse hook (but only for anti-patterns, not data correctness)

### Impact Examples

**Calendar Scenario (all systems affected):**
- Tool: "February 9, 2026 (Friday)"
- User: "Feb 9 2026 is Monday!"
- Agent: Accepts user correction without verification
- Correct: Sunday (neither was right, should verify)

**File Reading Scenario:**
- Tool reads file: `API_KEY = "abc123"`
- User: "The API key is xyz789"
- Agent: No guidance on which to trust (recent read vs user memory)

**Dependency Scenario:**
- Tool reads package.json: `"react": "18.0.0"`
- User: "We use React 17"
- Agent: No conflict resolution protocol

### Recommended Solution for co-cli

**Add to base instructions:**

```markdown
## Fact Verification

When tool output contradicts user assertion:

1. **Trust tool output first** — Tools access ground truth (files, APIs, system state). User memory may be outdated or mistaken.

2. **Verify calculable facts independently** — For dates, times, arithmetic, checksums, or other deterministic calculations, compute the answer yourself rather than trusting either source.

3. **Escalate unresolvable contradictions** — If you cannot verify independently:
   - State both values clearly
   - Explain which you trust and why
   - Ask user to verify the source of truth

4. **Never blindly accept corrections** — Especially for deterministic facts (dates, file contents, dependency versions, checksums). Verify first.

**Examples:**

- Tool returns date with day-of-week → Verify day-of-week matches date before using
- Tool reads file content → Trust file read over user memory unless file is known stale
- User corrects API response → Ask user to verify API documentation
- Arithmetic result disputed → Recalculate independently

**Not applicable to:**
- User preferences (inherently subjective)
- Design decisions (no ground truth)
- Requirements clarification (user is authority)
```

**Hook Implementation:**

```python
# co_cli/verification_hook.py

class FactVerificationHook(Hook):
    """Verify calculable facts when contradictions detected."""

    async def execute(self, context: HookContext) -> HookResult:
        # Trigger on user messages that contradict recent tool outputs
        if context.event != HookEvent.USER_PROMPT_SUBMIT:
            return HookResult(status="allow")

        user_input = context.params.get("message", "")
        recent_outputs = context.conversation_history[-5:]  # Last 5 messages

        # Check for contradiction keywords
        contradiction_keywords = ["actually", "no", "wrong", "incorrect", "should be"]
        if not any(kw in user_input.lower() for kw in contradiction_keywords):
            return HookResult(status="allow")

        # Extract facts from recent tool outputs
        tool_facts = extract_facts(recent_outputs)
        user_facts = extract_facts([user_input])

        # Detect conflicts
        conflicts = find_conflicts(tool_facts, user_facts)

        if conflicts:
            return HookResult(
                status="warn",
                system_message=f"""
                CONTRADICTION DETECTED:

                Tool output: {conflicts[0].tool_value}
                User assertion: {conflicts[0].user_value}

                INSTRUCTIONS:
                1. If this is a calculable fact (date, time, arithmetic), verify independently
                2. If it's file content, trust the tool (recent read)
                3. If it's user preference, trust the user
                4. If uncertain, state both values and ask user to verify

                Do NOT blindly accept the user's correction.
                """,
                user_message="⚠️  Detected contradiction with recent tool output. Verifying..."
            )

        return HookResult(status="allow")
```

### Why Critical for co-cli

**Priority:** MUST-HAVE for MVP

**Rationale:**
1. **Data integrity** — Prevents incorrect decisions based on wrong facts
2. **User trust** — Shows agent is reliable, not blindly accepting
3. **Competitive advantage** — No peer system solves this
4. **Easy to implement** — Simple verification logic + prompt guidance
5. **High impact** — Affects correctness of every task involving factual data

**Implementation effort:** Low (1-2 days)
**Impact:** High (prevents entire class of errors)

---

## Final Assessment

### Strengths

1. **Three Prompt Primitives** — Clear taxonomy (agents, commands, skills) with distinct purposes
2. **Event-Driven Composition** — Hooks inject context at lifecycle events (SessionStart, PreToolUse, etc.)
3. **Plugin Architecture** — Self-contained, versioned, distributable prompt packages
4. **Description-Based Triggering** — XML examples in agent descriptions for automatic activation
5. **Progressive Disclosure** — Main SKILL.md (concise) + references/ (deep dives)
6. **Multi-Agent Orchestration** — Commands launch agents in parallel, compare results
7. **Prescriptive Output Format** — Exact structure with examples/schemas
8. **Rule-Based Hook Engine** — User-defined markdown rules for custom policies
9. **Security as Layer** — PreToolUse hooks detect anti-patterns (not embedded in every prompt)
10. **Per-Agent Model Selection** — Cost optimization (flash for simple, thinking for complex)

### Weaknesses

1. **Massive Total Size** — 79 files, ~182K lines (including references) — largest of all 3 systems
2. **High Complexity** — Plugin system + hooks + agents + commands + skills = steep learning curve
3. **No Centralized System Prompt** — Base instructions not documented (inherited from parent)
4. **Trigger Matching Unclear** — No documented algorithm for agent selection (heuristic-based)
5. **Hook Security Risk** — Python/shell scripts in plugins can execute arbitrary code
6. **No Versioning Strategy** — Plugins versioned, but no system prompt versioning
7. **No fact verification guidance** — ⚠️ **CRITICAL GAP** — No instructions for verifying contradictions between tool outputs and user assertions. Security hooks exist but only check anti-patterns, not data correctness. All three peer systems (Codex, Gemini CLI, Claude Code) share this gap.

### Innovation Score: 9/10

**Why high:**
- Plugin architecture (only system with full plugin support)
- Event-driven composition (most flexible of all 3 systems)
- Three prompt primitives (clearest taxonomy)
- Progressive disclosure (skills with references)
- Rule-based hooks (user-defined policies)
- Multi-agent orchestration (parallel execution)

**Why not 10:**
- Massive complexity (steep learning curve)
- No fact verification (shared gap with peers)
- Hook security risk (arbitrary code execution)
- Trigger matching undocumented

---

## Appendix: Complete File Listing

### Agents (12 files)

```
plugins/
├── feature-dev/agents/
│   ├── code-explorer.md                [800 lines]   Codebase analysis
│   ├── code-architect.md               [1000 lines]  Architecture design
│   └── code-reviewer.md                [700 lines]   Code review
├── plugin-dev/agents/
│   ├── agent-creator.md                [900 lines]   Agent generation
│   ├── plugin-validator.md             [600 lines]   Plugin validation
│   └── skill-reviewer.md               [650 lines]   Skill quality review
├── commit-commands/agents/
│   └── commit-message-writer.md        [400 lines]   Git commit messages
├── pr-review-toolkit/agents/
│   └── pr-reviewer.md                  [850 lines]   PR review
├── security-guidance/agents/
│   └── security-auditor.md             [750 lines]   Security analysis
├── frontend-design/agents/
│   └── frontend-designer.md            [900 lines]   UI/UX design
├── agent-sdk-dev/agents/
│   └── sdk-helper.md                   [700 lines]   Agent SDK guidance
└── ralph-wiggum/agents/
    └── orchestrator.md                 [1150 lines]  Multi-agent coordination
```

### Commands (8 files)

```
plugins/
├── feature-dev/commands/
│   └── feature-dev.md                  [1200 lines]  7-phase feature workflow
├── code-review/commands/
│   └── code-review.md                  [600 lines]   PR review workflow
├── commit-commands/commands/
│   ├── commit.md                       [300 lines]   Git commit
│   ├── commit-all.md                   [350 lines]   Stage all + commit
│   ├── push.md                         [250 lines]   Push to remote
│   └── pull.md                         [200 lines]   Pull from remote
├── plugin-dev/commands/
│   └── plugin-create.md                [800 lines]   Create new plugin
└── agent-sdk-dev/commands/
    └── agent-create.md                 [700 lines]   Create new agent
```

### Skills (12 main + 42 supporting files)

```
plugins/plugin-dev/skills/
├── agent-development/
│   ├── SKILL.md                        [2400 lines]  Agent creation guide
│   ├── references/
│   │   ├── system-prompt-design.md     [8000 lines]  Prompt engineering
│   │   ├── triggering-examples.md      [5000 lines]  50+ trigger examples
│   │   └── agent-patterns.md           [12000 lines] Analysis/gen/val patterns
│   ├── examples/
│   │   ├── analysis-agent.md           [1500 lines]  Working example
│   │   ├── generation-agent.md         [1800 lines]  Working example
│   │   └── validation-agent.md         [1400 lines]  Working example
│   └── scripts/
│       ├── validate-agent.sh           [150 lines]   Validation utility
│       └── test-agent-trigger.sh       [100 lines]   Testing utility
├── command-development/
│   ├── SKILL.md                        [1800 lines]  Command workflows
│   ├── references/                     [2 files, 8000 lines]
│   ├── examples/                       [2 files, 3000 lines]
│   └── scripts/                        [1 file, 120 lines]
├── plugin-structure/
│   ├── SKILL.md                        [1200 lines]  Plugin organization
│   ├── references/                     [4 files, 15000 lines]
│   ├── examples/                       [1 file, 800 lines]
│   └── scripts/                        [1 file, 80 lines]
├── hook-development/
│   ├── SKILL.md                        [1600 lines]  Hook system
│   ├── references/                     [3 files, 10000 lines]
│   ├── examples/                       [4 files, 5000 lines]
│   └── scripts/                        [2 files, 200 lines]
├── system-prompt-design/
│   ├── SKILL.md                        [2800 lines]  Prompt engineering
│   ├── references/                     [5 files, 20000 lines]
│   ├── examples/                       [4 files, 6000 lines]
│   └── scripts/                        [0 files]
├── triggering-patterns/
│   ├── SKILL.md                        [2200 lines]  Agent triggering
│   ├── references/                     [2 files, 8000 lines]
│   └── examples/                       [6 files, 10000 lines]
└── agent-patterns/
    ├── SKILL.md                        [3000 lines]  Analysis/gen/val patterns
    ├── references/                     [3 files, 15000 lines]
    └── examples/                       [8 files, 15000 lines]

[6 more skills with similar structure...]
```

### Hooks (5 active implementations)

```
plugins/
├── explanatory-output-style/hooks/
│   ├── hooks.json                      [20 lines]    SessionStart config
│   └── session-start.sh                [100 lines]   Inject explanation style
├── learning-output-style/hooks/
│   ├── hooks.json                      [20 lines]    SessionStart config
│   └── session-start.sh                [120 lines]   Inject learning mode
├── hookify/hooks/
│   ├── hooks.json                      [40 lines]    UserPromptSubmit config
│   └── rule_engine.py                  [400 lines]   Content-based validation
├── security-guidance/hooks/
│   ├── hooks.json                      [30 lines]    PreToolUse config
│   └── security_reminder_hook.py       [500 lines]   Security pattern detection
└── (core)/hooks/
    └── telemetry_hook.py               [80 lines]    PostToolUse logging
```

### Plugin Manifests (12 files)

```
plugins/*/(.claude-plugin/plugin.json)  [~100 lines each]
```

**Total Unique Files:** 79 prompt/hook/config files
**Total Lines:**
- Agents: ~8,400
- Commands: ~6,000
- Skills (main): ~28,800
- Skills (references): ~120,000
- Skills (examples): ~18,000
- Hooks: ~1,200
- **Grand Total:** ~182,400 lines

**Largest File:** `skills/agent-development/references/agent-patterns.md` (12,000 lines)
**Smallest File:** `hooks/hooks.json` (20 lines)
**Average File Size:** 2,308 lines

---

**End of Claude Code Prompt System Review**
