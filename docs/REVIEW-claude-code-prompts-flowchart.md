# Claude Code Prompt Assembly Flowchart

## From User Action → Final LLM Prompt (Event-Driven)

### Example Scenario
**User wants:** "Use the feature-dev workflow to add authentication"

---

## Step 1: User Action Types

Claude Code has **3 ways** to trigger prompts:

```
┌────────────────────────────────────────────────────────┐
│             THREE PROMPT PRIMITIVES                    │
├────────────────────────────────────────────────────────┤
│                                                        │
│  1. COMMANDS (Slash commands)                          │
│     Trigger: User types /feature-dev                   │
│     Purpose: Multi-phase workflows                     │
│     Example: /feature-dev "add auth"                   │
│                                                        │
│  2. AGENTS (Autonomous subprocesses)                   │
│     Trigger: Description matching or proactive         │
│     Purpose: Specialized analysis/generation           │
│     Example: Auto-launch code-explorer agent           │
│                                                        │
│  3. SKILLS (Knowledge bases)                           │
│     Trigger: Questions or context matching             │
│     Purpose: Educational guidance                      │
│     Example: "How do I create an agent?"               │
│                                                        │
└────────────────────────────────────────────────────────┘
```

---

## Step 2: Event-Driven Composition Flow

**KEY DIFFERENCE:** Prompts are assembled **at runtime** through **5 lifecycle hooks**, not static files!

```
┌──────────────────────────────────────────────────────────┐
│                 LIFECYCLE HOOK EVENTS                     │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  1. SessionStart Hook                                    │
│     ↓                                                    │
│     When: Session begins                                │
│     Purpose: Inject global style/context                │
│     Examples:                                           │
│     • explanatory-output-style → "Explain before code"  │
│     • learning-output-style → "Teach, don't just answer"│
│                                                          │
│  2. UserPromptSubmit Hook                                │
│     ↓                                                    │
│     When: Before processing user input                  │
│     Purpose: Validate, transform, enforce rules         │
│     Example:                                            │
│     • hookify rule engine → Check local .md rules       │
│                                                          │
│  3. Agent/Command/Skill Selection                        │
│     ↓                                                    │
│     Load selected prompt:                               │
│     • /feature-dev → commands/feature-dev.md            │
│     • "explore code" → agents/code-explorer.md          │
│     • "how to?" → skills/agent-development/SKILL.md     │
│                                                          │
│  4. PreToolUse Hook                                      │
│     ↓                                                    │
│     When: Before executing tool                         │
│     Purpose: Security checks, approval gates            │
│     Example:                                            │
│     • security-guidance → Block command injection       │
│     Returns: allow | warn | block                       │
│                                                          │
│  5. Tool Execution                                       │
│     ↓                                                    │
│     Execute the tool (Read, Edit, Bash, etc.)           │
│                                                          │
│  6. PostToolUse Hook                                     │
│     ↓                                                    │
│     When: After tool execution                          │
│     Purpose: Logging, output transformation             │
│     Example:                                            │
│     • telemetry_hook → Log to database                  │
│                                                          │
│  7. Response to User                                     │
│     ↓                                                    │
│     Show result + any hook warnings                     │
│                                                          │
│  8. Stop Hook (on session end)                           │
│     ↓                                                    │
│     When: Session ending                                │
│     Purpose: Final checks, cleanup                      │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## Step 3: Detailed Flow Example

### User types: `/feature-dev "add JWT authentication"`

```
STEP 1: SessionStart Hook Fires
┌─────────────────────────────────────────────────────────┐
│ Plugin: explanatory-output-style                        │
│ Hook: session-start.sh                                  │
│                                                         │
│ Returns:                                                │
│ {                                                       │
│   "status": "allow",                                    │
│   "additionalContext": "# Output Style: Explanatory    │
│                         Explain 'why' before 'what'"   │
│ }                                                       │
│                                                         │
│ → This context gets injected into system prompt        │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 2: Load Command Prompt
┌─────────────────────────────────────────────────────────┐
│ File: plugins/feature-dev/commands/feature-dev.md       │
│                                                         │
│ YAML Frontmatter:                                       │
│ ---                                                     │
│ description: "7-phase feature development workflow"    │
│ argument-hint: "<feature-description>"                 │
│ ---                                                     │
│                                                         │
│ Markdown Body (1200 lines):                            │
│                                                         │
│ # Feature Development Workflow                         │
│                                                         │
│ ## Phase 1: Clarification                              │
│ **Goal**: Understand requirements                      │
│ **Actions**:                                           │
│ 1. Ask clarifying questions                            │
│ 2. Document requirements                               │
│ 3. Confirm with user                                   │
│                                                         │
│ ## Phase 2: Exploration                                │
│ **Goal**: Understand codebase                          │
│ **Actions**:                                           │
│ 1. Launch 2-3 `code-explorer` agents in parallel       │
│ 2. Wait for agents to complete                         │
│ 3. Consolidate findings                                │
│                                                         │
│ [Phases 3-7 continue...]                               │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 3: Agent Execution (Phase 2)
┌─────────────────────────────────────────────────────────┐
│ Command says: "Launch 2-3 code-explorer agents"        │
│                                                         │
│ For each agent:                                         │
│   1. Load agents/code-explorer.md                      │
│   2. Parse YAML frontmatter                            │
│      name: code-explorer                               │
│      color: blue                                       │
│      model: inherit                                    │
│      tools: ["Read", "Grep", "Glob", "Bash"]          │
│   3. Send markdown body as system prompt               │
│   4. Agent runs autonomously                           │
│   5. Agent returns structured report                   │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 4: Agent Wants to Edit File (Phase 5)
┌─────────────────────────────────────────────────────────┐
│ Agent: "I'll edit src/auth/jwt.js to add JWT support"  │
│                                                         │
│ Tool Call: Edit(file="src/auth/jwt.js", ...)           │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 5: PreToolUse Hook Fires
┌─────────────────────────────────────────────────────────┐
│ Plugin: security-guidance                               │
│ Hook: security_reminder_hook.py                         │
│ Event: PreToolUse                                       │
│ Matcher: "Edit|Write"                                   │
│                                                         │
│ Security checks:                                        │
│ - Command injection? No                                 │
│ - XSS vulnerability? No                                 │
│ - SQL injection? No                                     │
│ - Hardcoded secrets? No                                 │
│ - Path traversal? No                                    │
│                                                         │
│ Returns:                                                │
│ {                                                       │
│   "status": "allow",                                    │
│   "systemMessage": "Security check passed"             │
│ }                                                       │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 6: Tool Executes
┌─────────────────────────────────────────────────────────┐
│ Edit tool modifies src/auth/jwt.js                      │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 7: PostToolUse Hook Fires
┌─────────────────────────────────────────────────────────┐
│ Hook: telemetry_hook.py                                 │
│                                                         │
│ Logs to database:                                       │
│ - Tool: Edit                                            │
│ - File: src/auth/jwt.js                                 │
│ - Timestamp: 2026-02-08T10:30:00                        │
│ - Success: true                                         │
│                                                         │
│ Returns:                                                │
│ {                                                       │
│   "status": "allow"                                     │
│ }                                                       │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 8: Continue Through Phase 6 (Review)
┌─────────────────────────────────────────────────────────┐
│ Command: "Launch 3 code-reviewer agents"                │
│                                                         │
│ Each agent:                                             │
│ - Loads agents/code-reviewer.md                         │
│ - Reviews changes                                       │
│ - Returns findings with severity                        │
└─────────────────────────────────────────────────────────┘
                         ↓

STEP 9: Phase 7 Complete - Final Report
┌─────────────────────────────────────────────────────────┐
│ ## Feature Implementation Summary                       │
│                                                         │
│ **Feature**: JWT Authentication                         │
│ **Status**: Complete                                    │
│                                                         │
│ ### Changes Made                                        │
│ - src/auth/jwt.js: Added JWT generation and validation │
│ - src/middleware/auth.js: Updated to use JWT           │
│                                                         │
│ ### Tests Added                                         │
│ - tests/auth/jwt.test.js: JWT token lifecycle tests    │
│                                                         │
│ ### Quality Checks                                      │
│ - ✅ Code review passed (3 agents, 0 critical issues)   │
│ - ✅ Tests passing (12/12)                              │
│ - ✅ Security checks passed                             │
└─────────────────────────────────────────────────────────┘
```

---

## Step 4: Prompt Structure (Agents, Commands, Skills)

### Agent Prompt Example

**File:** `plugins/feature-dev/agents/code-explorer.md`

```markdown
---
name: code-explorer
description: |
  Use this agent to perform deep codebase exploration.

  Examples:
  <example>
    <context>User wants to add feature requiring understanding existing code</context>
    <user>Add JWT authentication</user>
    <assistant>I'll explore the codebase to understand current auth...</assistant>
    <commentary>Triggers because implementation needs context</commentary>
  </example>
model: inherit
color: blue
tools: ["Read", "Grep", "Glob", "Bash"]
---

You are an expert codebase analyst specializing in systematic code exploration.

**Your Core Responsibilities:**
1. Perform comprehensive codebase analysis
2. Identify all relevant files for the task
3. Document findings in structured format

**Exploration Process:**
1. Initial Scan: Use Glob to identify file structure
2. Pattern Recognition: Grep for key patterns
3. Dependency Mapping: Trace imports and relationships
4. Convention Analysis: Identify coding standards
5. Risk Assessment: Flag potential issues
6. Report Generation: Produce structured findings

**Quality Standards:**
- Explore systematically, not randomly
- Document all assumptions and uncertainties
- Prioritize findings by relevance

**Output Format:**
```markdown
## Exploration Summary
- **Task**: [What we're exploring for]
- **Scope**: [Files examined]

## Key Findings
1. [Finding with file:line references]
2. [Finding with file:line references]

## Relevant Files
| File | Purpose | Priority |
|------|---------|----------|
| path | description | high/medium/low |
```

**Edge Cases:**
- **Large codebases (1000+ files)**: Start with entry points, narrow scope
- **Monorepos**: Identify workspace boundaries
```

---

### Command Prompt Example

**File:** `plugins/feature-dev/commands/feature-dev.md`

```markdown
---
description: Structured 7-phase feature development workflow
argument-hint: "<feature-description>"
---

# Feature Development Workflow

This command guides through comprehensive feature development.

## Workflow Overview
1. **Clarification** - Understand requirements
2. **Exploration** - Analyze codebase (launch code-explorer agents)
3. **Context Review** - Read relevant files
4. **Architecture** - Design approach (launch code-architect agents)
5. **Implementation** - Write code with tests
6. **Review** - Quality check (launch code-reviewer agents)
7. **Finalization** - Summary and handoff

## Phase 1: Clarification
**Goal**: Fully understand requirements

**Actions**:
1. Ask clarifying questions
2. Document requirements
3. Confirm with user

**Success Criteria**:
- All ambiguities resolved
- User confirms understanding

**Next**: Proceed to Phase 2 when approved

[Phases 2-7 continue...]
```

---

### Skill Prompt Example

**File:** `plugins/plugin-dev/skills/agent-development/SKILL.md`

```markdown
---
name: Agent Development
description: |
  This skill should be used when creating or modifying agents.
  Trigger phrases: "create an agent", "write agent prompt"
version: 0.1.0
---

# Agent Development

This skill guides you through creating effective autonomous agents.

## Key Concepts

**Agent**: Autonomous subprocess with specialized expertise.

**System Prompt**: Second-person instructions defining role,
responsibilities, process, quality standards, output format.

## Core Processes

### Process 1: Agent Creation
1. Define Purpose: What task does this agent solve?
2. Choose Pattern: Analysis, generation, validation, orchestration
3. Write Frontmatter
4. Write System Prompt
5. Test Triggering

[Process details with examples...]

## Quick Reference
- Minimum Agent Length: ~500 words
- Standard: 1000-2000 words
- Comprehensive: 2000-5000 words

## Detailed Resources
- `references/system-prompt-design.md` - Deep dive (8000 lines)
- `references/triggering-examples.md` - 50+ examples (5000 lines)

## Examples
- `examples/analysis-agent.md` - Complete code-explorer
- `examples/generation-agent.md` - Complete code-generator
```

---

## Step 5: Hook System (Security Example)

### Security Hook Prevents Bad Code

```
Agent wants to write code:
                 ↓
┌────────────────────────────────────────────────────────┐
│ PreToolUse Hook: security-guidance                     │
│ File: security_reminder_hook.py                        │
├────────────────────────────────────────────────────────┤
│                                                        │
│ Scans code for patterns:                               │
│                                                        │
│ CRITICAL (block):                                      │
│ ❌ Command injection                                   │
│    subprocess.call(user_input, shell=True)            │
│                                                        │
│ ❌ SQL injection                                       │
│    "SELECT * FROM users WHERE id=" + user_id          │
│                                                        │
│ ❌ Hardcoded secrets                                   │
│    api_key = "sk-1234567890"                          │
│                                                        │
│ WARNING (allow with notice):                           │
│ ⚠️  XSS vulnerability                                  │
│    innerHTML = userInput                               │
│                                                        │
│ ⚠️  Path traversal risk                                │
│    open("../../../etc/passwd")                        │
│                                                        │
└────────────────────────────────────────────────────────┘
                 ↓
        ┌────────┴────────┐
        │                 │
    BLOCKED            WARNED
        │                 │
        ↓                 ↓
    Reject tool      Allow with
    execution        warning in
                     system msg
```

**Example Block:**
```python
# Agent wants to write this code:
subprocess.call(f"git commit -m {message}", shell=True)

# Hook detects command injection pattern
{
  "status": "block",
  "systemMessage": "CRITICAL: Command injection detected. Use parameterized calls.",
  "userMessage": "Blocked for security. Use subprocess.call(['git', 'commit', '-m', message])"
}

# Agent revises:
subprocess.call(['git', 'commit', '-m', message])  # ✅ Safe
```

---

## Plugin Architecture

### How Plugins Package Prompts

```
plugins/feature-dev/
├── .claude-plugin/
│   └── plugin.json              # Manifest
│       {
│         "name": "feature-dev",
│         "version": "1.0.0",
│         "description": "Feature development workflow"
│       }
│
├── agents/                      # Autonomous subprocesses
│   ├── code-explorer.md         # Analysis agent (800 lines)
│   ├── code-architect.md        # Design agent (1000 lines)
│   └── code-reviewer.md         # Review agent (700 lines)
│
├── commands/                    # Slash command workflows
│   └── feature-dev.md           # /feature-dev (1200 lines)
│
└── hooks/                       # Event handlers
    ├── hooks.json               # Event configuration
    └── session-start.sh         # SessionStart handler
```

---

## Real-World Execution Flow

### User: `/feature-dev "add authentication"`

```
┌─────────────────────────────────────────────────────────┐
│ PHASE 1: Clarification (Interactive)                    │
├─────────────────────────────────────────────────────────┤
│ Claude: "To design authentication, I need to know:     │
│ 1. JWT or session-based?                               │
│ 2. OAuth providers or password-based?                  │
│ 3. Existing user model or create new?"                 │
│                                                         │
│ User: "JWT, password-based, existing User model"       │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 2: Exploration (Autonomous Agents)                │
├─────────────────────────────────────────────────────────┤
│ Launches 3 code-explorer agents in PARALLEL:           │
│                                                         │
│ Agent 1: "Explore existing User model"                 │
│   → Finds: models/User.js, schema has email/password   │
│                                                         │
│ Agent 2: "Explore auth patterns in codebase"           │
│   → Finds: middleware/auth.js (session-based, will     │
│     need replacement)                                  │
│                                                         │
│ Agent 3: "Explore test infrastructure"                 │
│   → Finds: Jest setup, example auth tests              │
│                                                         │
│ All agents complete → Consolidated report ready        │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 4: Architecture (Design Agents)                   │
├─────────────────────────────────────────────────────────┤
│ Launches 2 code-architect agents:                      │
│                                                         │
│ Agent 1: "Design JWT token generation/validation"      │
│   → Proposes: jsonwebtoken library, 24h expiry         │
│                                                         │
│ Agent 2: "Design middleware integration"               │
│   → Proposes: Replace session check with JWT verify    │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 5: Implementation                                 │
├─────────────────────────────────────────────────────────┤
│ Writes code:                                            │
│ • src/auth/jwt.js (new)                                 │
│ • middleware/auth.js (updated)                          │
│ • tests/auth/jwt.test.js (new)                          │
│                                                         │
│ Every Edit/Write call:                                  │
│   → PreToolUse hook runs security checks                │
│   → PostToolUse hook logs to telemetry                  │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 6: Review (Validation Agents)                     │
├─────────────────────────────────────────────────────────┤
│ Launches 3 code-reviewer agents:                       │
│                                                         │
│ Agent 1: Reviews jwt.js                                 │
│   ✅ No issues                                          │
│                                                         │
│ Agent 2: Reviews middleware changes                     │
│   ⚠️  Warning: Add rate limiting to prevent brute force │
│                                                         │
│ Agent 3: Reviews tests                                  │
│   ✅ Good coverage                                      │
└─────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 7: Finalization                                   │
├─────────────────────────────────────────────────────────┤
│ ## Feature Complete                                     │
│                                                         │
│ ✅ JWT auth implemented                                 │
│ ✅ Tests passing (15/15)                                │
│ ⚠️  Consider: Add rate limiting (from review)           │
│                                                         │
│ Files changed: 3                                        │
│ Lines added: 234                                        │
│ Tests added: 15                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Summary: Claude Code's Unique Approach

### Three Key Innovations

```
1. THREE PROMPT PRIMITIVES
   ├─ Agents: Autonomous analysis/generation
   ├─ Commands: Multi-phase workflows
   └─ Skills: Educational knowledge bases

2. EVENT-DRIVEN COMPOSITION
   ├─ SessionStart: Global context
   ├─ UserPromptSubmit: Input validation
   ├─ PreToolUse: Security gates
   ├─ PostToolUse: Logging
   └─ Stop: Cleanup

3. PLUGIN ARCHITECTURE
   ├─ Self-contained packages
   ├─ Versioned + composable
   └─ Community extensible
```

---

## Comparison Matrix

| Dimension | Codex | Gemini CLI | Claude Code |
|-----------|-------|------------|-------------|
| **Architecture** | Layered files | Conditional function | Plugin + hooks |
| **Prompt Types** | 1 type | 1 type | 3 types |
| **Composition** | Static assembly | Conditional blocks | Event-driven |
| **Security** | In prompts | In prompts | Hook layer |
| **Extensibility** | Fork repo | Fork repo | Plugin system |
| **Total Files** | 24 files | 1 file | 79+ files |
| **Key Innovation** | Two unknowns | Directive vs Inquiry | Event hooks |

---

## Why This Matters for co-cli

**Key Takeaways:**

1. **Three Primitives:** Consider separating concerns
   - Research tasks → Agent pattern
   - Workflows → Command pattern
   - Help → Skill pattern

2. **Hook System:** Security as a layer, not embedded everywhere
   - PreToolUse hooks can block dangerous operations
   - PostToolUse hooks can log/audit

3. **Plugin Architecture:** Extensibility without forking
   - Core stays lean
   - Users can add domain-specific agents

**Practical Implementation:**
```python
# co_cli/hooks.py
def run_pre_tool_hooks(tool: str, params: dict) -> HookResult:
    for hook in registered_hooks['PreToolUse']:
        result = hook.execute(tool, params)
        if result.status == 'block':
            return result
    return HookResult(status='allow')
```
