# REVIEW: OpenCode Prompt System Architecture

**Repo:** `~/workspace_genai/opencode` (TypeScript/Bun)
**Analyzed:** 2026-02-13 | **~35 prompt sources** | **~2,800 lines** | **8 prompt variants** | **6 agents**

---

## Architecture

OpenCode uses a **per-model routing architecture**. System prompts are stored as **plain text files** (`.txt`), selected at runtime by **model family substring matching**, and assembled with dynamic environment blocks and filesystem-based instruction injection.

```
┌──────────────────────────────────────────────────────────────┐
│              PER-MODEL ROUTING COMPOSITION                    │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Model API ID String                                         │
│    ↓                                                          │
│  Substring Match → Select Provider Prompt Variant            │
│    "claude"  → PROMPT_ANTHROPIC  (TodoWrite-heavy)           │
│    "gemini-" → PROMPT_GEMINI     (concise, no TodoWrite)     │
│    "gpt-5"   → PROMPT_CODEX     (Codex header, apply_patch) │
│    "gpt-"/"o1"/"o3" → PROMPT_BEAST (autonomous, web-heavy)  │
│    "trinity"  → PROMPT_TRINITY  (ultra-concise, 4 lines max)│
│    fallback   → PROMPT_ANTHROPIC_WITHOUT_TODO (qwen.txt)     │
│    ↓                                                          │
│  + Agent Prompt Override (if agent.prompt is set)             │
│    ↓                                                          │
│  + Environment Block (<env> working dir, platform, date)     │
│    ↓                                                          │
│  + Instruction Files (AGENTS.md / CLAUDE.md, findUp)         │
│    ↓                                                          │
│  + Plugin Hook: experimental.chat.system.transform           │
│    ↓                                                          │
│  + Prompt Cache Optimization (2-part header/body split)      │
│    ↓                                                          │
│  = FINAL SYSTEM PROMPT                                       │
│                                                               │
│  Mid-Loop Injections:                                        │
│    • Plan mode reminders (synthetic <system-reminder> parts) │
│    • Queued user messages (wrapped in <system-reminder>)     │
│    • Max steps warning (tools disabled, text-only response)  │
│    • Build-switch transition (plan → build mode change)      │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Configuration space:** `6 model variants × 6 agents × plan_mode × instruction_files = variable`

### Directory Structure

```
packages/opencode/src/
├── session/
│   ├── system.ts                       # Model routing logic (55 lines)
│   ├── prompt.ts                       # Main loop orchestration (1867 lines)
│   ├── llm.ts                          # System prompt assembly (~300 lines)
│   ├── instruction.ts                  # Dynamic instruction file loading (198 lines)
│   ├── processor.ts                    # Streaming event processing (~500 lines)
│   ├── compaction.ts                   # Context compression (227 lines)
│   └── prompt/
│       ├── anthropic.txt               # Claude models (106 lines)
│       ├── anthropic-20250930.txt      # Claude latest/enhanced (166 lines)
│       ├── gemini.txt                  # Gemini models (156 lines)
│       ├── beast.txt                   # GPT-4/o1/o3 (148 lines)
│       ├── copilot-gpt-5.txt          # GPT-5 Copilot (143 lines)
│       ├── trinity.txt                 # Trinity/minimal (98 lines)
│       ├── qwen.txt                    # Qwen/fallback (110 lines)
│       ├── codex_header.txt            # OpenAI OAuth Codex header (80 lines)
│       ├── plan.txt                    # Plan mode reminder (27 lines)
│       ├── plan-reminder-anthropic.txt # Enhanced 5-phase plan workflow (67 lines)
│       ├── build-switch.txt            # Plan → build transition (5 lines)
│       └── max-steps.txt              # Max steps reached (16 lines)
├── agent/
│   ├── agent.ts                        # Agent registry & config (339 lines)
│   └── prompt/
│       ├── explore.txt                 # Explore subagent (19 lines)
│       ├── title.txt                   # Title generation (45 lines)
│       ├── summary.txt                 # Session summary (12 lines)
│       └── compaction.txt              # Context compaction (13 lines)
├── tool/
│   └── *.txt                           # 27 tool description files
├── config/
│   └── config.ts                       # Config loading, agent/command discovery
├── provider/
│   └── transform.ts                    # Model-specific message transforms
├── permission/
│   └── next.ts                         # Permission evaluation
└── plugin/
    └── index.ts                        # Plugin system with hooks
```

---

## Prompt Inventory

| Category | Files | Lines | Purpose |
|----------|-------|-------|---------|
| Provider Prompts | 8 | ~1,007 | Model-family-specific system prompts |
| Agent Prompts | 4 | ~89 | Subagent/utility agent prompts |
| Workflow Fragments | 4 | ~115 | Plan mode, build switch, max steps |
| Tool Descriptions | 27 | ~1,200 | Tool docstrings (loaded as descriptions) |
| Composition Logic | 4 | ~2,820 | System routing, loop, instruction injection, compaction |
| **TOTAL** | **47** | **~5,231** | |

### Provider Prompt Variants

| Variant | File | Lines | Model Family | Key Features |
|---------|------|-------|-------------|-------------|
| PROMPT_ANTHROPIC | `anthropic.txt` | 106 | Claude | TodoWrite-heavy, Task delegation, professional objectivity |
| PROMPT_ANTHROPIC (latest) | `anthropic-20250930.txt` | 166 | Claude (enhanced) | + hooks support, security warnings, env block baked in |
| PROMPT_GEMINI | `gemini.txt` | 156 | Gemini | Concise, no TodoWrite, no Task delegation, full examples |
| PROMPT_BEAST | `beast.txt` | 148 | GPT-4/o1/o3 | Autonomous, web-research-heavy, memory file, casual tone |
| PROMPT_CODEX | `copilot-gpt-5.txt` | 143 | GPT-5 | Agentic, sequential thinking, internet-first research |
| PROMPT_TRINITY | `trinity.txt` | 98 | Trinity | Ultra-concise (≤4 lines), one tool per message |
| PROMPT_ANTHROPIC_WITHOUT_TODO | `qwen.txt` | 110 | Qwen/fallback | Malware refusal, no TodoWrite, parallel tool calls |
| PROMPT_CODEX (header) | `codex_header.txt` | 80 | OpenAI OAuth | Sent as `instructions` field, ASCII-first, apply_patch |

### Agent Definitions

| Agent | Mode | Prompt | Description | Key Permissions |
|-------|------|--------|-------------|-----------------|
| `build` | primary | provider default | Default agent, full tools | question: allow, plan_enter: allow |
| `plan` | primary | provider default + plan reminders | Read-only planning | edit: deny (except plan file), plan_exit: allow |
| `general` | subagent | provider default | Multi-step research/execution | todoread/todowrite: deny |
| `explore` | subagent | `explore.txt` | Fast codebase search | read-only tools only |
| `compaction` | hidden | `compaction.txt` | Context compression | all tools denied |
| `title` | hidden | `title.txt` | Title generation | all tools denied, temperature: 0.5 |
| `summary` | hidden | `summary.txt` | Session summary | all tools denied |

---

## Key Prompts (Verbatim)

### PROMPT_ANTHROPIC (Claude — Primary Variant)

**File:** `session/prompt/anthropic.txt`

```
You are OpenCode, the best coding agent on the planet.

You are an interactive CLI tool that helps users with software engineering tasks.

IMPORTANT: You must NEVER generate or guess URLs for the user unless you are
confident that the URLs are for helping the user with programming.

# Professional objectivity
Prioritize technical accuracy and truthfulness over validating the user's beliefs.
Focus on facts and problem-solving, providing direct, objective technical info
without any unnecessary superlatives, praise, or emotional validation. It is best
for the user if OpenCode honestly applies the same rigorous standards to all ideas
and disagrees when necessary, even if it may not be what the user wants to hear.
Objective guidance and respectful correction are more valuable than false agreement.

# Task Management
You have access to the TodoWrite tools. Use these tools VERY frequently to ensure
that you are tracking your tasks and giving the user visibility into your progress.
It is critical that you mark todos as completed as soon as you are done with a task.
Do not batch up multiple tasks before marking them as completed.

# Tool usage policy
- When doing file search, prefer to use the Task tool to reduce context usage.
- VERY IMPORTANT: When exploring the codebase for broad context, use the Task tool
  instead of running search commands directly.
```

### PROMPT_GEMINI (Gemini — Concise, Convention-First)

**File:** `session/prompt/gemini.txt`

```
You are opencode, an interactive CLI agent specializing in software engineering
tasks. Your primary goal is to help users safely and efficiently.

# Core Mandates
- **Conventions:** Rigorously adhere to existing project conventions.
- **Libraries/Frameworks:** NEVER assume a library/framework is available.
  Verify its established usage within the project before employing it.
- **Comments:** Add code comments sparingly. Focus on *why*, not *what*.
- **Confirm Ambiguity/Expansion:** Do not take significant actions beyond the
  clear scope of the request without confirming with the user.
- **Do Not revert changes:** Do not revert changes unless asked.

# Primary Workflows

## Software Engineering Tasks
1. **Understand:** Use grep and glob extensively. Use read to validate assumptions.
2. **Plan:** Build a coherent plan. Try to use a self-verification loop by writing
   unit tests if relevant.
3. **Implement:** Using available tools, strictly adhering to conventions.
4. **Verify (Tests):** NEVER assume standard test commands.
5. **Verify (Standards):** VERY IMPORTANT: Execute project-specific build, linting
   and type-checking commands.

## Tone and Style
- **Concise & Direct.** Fewer than 3 lines per response whenever practical.
- **No Chitchat:** Avoid filler, preambles ("Okay, I will now..."), or
  postambles ("I have finished..."). Get straight to the action.

# Final Reminder
You are an agent - please keep going until the user's query is completely resolved.
```

### PROMPT_BEAST (GPT-4/o1/o3 — Autonomous, Web-Heavy)

**File:** `session/prompt/beast.txt`

```
You are opencode, an agent - please keep going until the user's query is
completely resolved, before ending your turn and yielding back to the user.

You MUST iterate and keep going until the problem is solved.

THE PROBLEM CAN NOT BE SOLVED WITHOUT EXTENSIVE INTERNET RESEARCH.

You must use the webfetch tool to recursively gather all information from URLs.
Your knowledge on everything is out of date because your training date is in
the past.

Take your time and think through every step. Your solution must be perfect.
Failing to test your code sufficiently rigorously is the NUMBER ONE failure mode.

You MUST plan extensively before each function call, and reflect extensively
on the outcomes of the previous function calls.

# Workflow
1. Fetch any URLs provided by the user
2. Understand the problem deeply
3. Investigate the codebase
4. Research the problem on the internet
5. Develop a clear, step-by-step plan
6. Implement the fix incrementally
7. Debug as needed — determine root cause
8. Test frequently
9. Iterate until the root cause is fixed and all tests pass
10. Reflect and validate comprehensively

# Communication Guidelines
Casual, friendly yet professional tone.
"Let me fetch the URL you provided to gather more information."
"Whelp - I see we have some problems. Let's fix those up."

# Memory
You have a memory stored in `.github/instructions/memory.instruction.md`.
If the user asks you to remember something, update the memory file.
```

### PROMPT_TRINITY (Trinity — Ultra-Concise)

**File:** `session/prompt/trinity.txt`

```
You are opencode, an interactive CLI tool.

IMPORTANT: You should minimize output tokens as much as possible while
maintaining helpfulness, quality, and accuracy.

IMPORTANT: Keep your responses short. You MUST answer concisely with fewer
than 4 lines (not including tool use or code generation). One word answers
are best. Avoid introductions, conclusions, and explanations.

# Code style
- IMPORTANT: DO NOT ADD ***ANY*** COMMENTS unless asked

# Tool usage policy
- Use exactly one tool per assistant message. After each tool call, wait
  for the result before continuing.

Examples:
user: 2 + 2
assistant: 4

user: is 11 a prime number?
assistant: Yes
```

### PROMPT_CODEX Header (OpenAI OAuth — Instructions Field)

**File:** `session/prompt/codex_header.txt`

```
You are OpenCode, the best coding agent on the planet.

## Editing constraints
- Default to ASCII when editing or creating files.
- Try to use apply_patch for single file edits.
- Do not use apply_patch for auto-generated changes or when scripting is
  more efficient.

## Git and workspace hygiene
- NEVER revert existing changes you did not make unless explicitly requested.
- Do not amend commits unless explicitly requested.
- **NEVER** use destructive commands like `git reset --hard` unless
  specifically requested or approved by the user.

## Frontend tasks
- Avoid collapsing into bland, generic layouts.
- Typography: Use expressive, purposeful fonts. Avoid Inter, Roboto, Arial.
- Color: Choose a clear visual direction. No purple bias or dark mode bias.
- Motion: Use meaningful animations, not generic micro-motions.

## Presenting your work
- Default: be very concise; friendly coding teammate tone.
- Default: do the work without asking questions.
- Questions: only ask when truly blocked AND cannot safely pick a default.
- Never ask permission questions like "Should I proceed?"
```

### Plan Mode (5-Phase Enhanced Workflow)

**File:** `session/prompt/plan-reminder-anthropic.txt`

```
Plan mode is active. You MUST NOT make any edits (with the exception of the
plan file), run any non-readonly tools, or otherwise make any changes to the
system.

## Enhanced Planning Workflow

### Phase 1: Initial Understanding
Goal: Gain comprehensive understanding by reading code and asking questions.
Critical: Only use Explore subagent type.
1. Understand the user's request
2. Launch up to 3 Explore agents IN PARALLEL
3. Use AskUserQuestion to clarify ambiguities

### Phase 2: Planning
Goal: Come up with an approach by launching a Plan subagent.

### Phase 3: Synthesis
Goal: Synthesize perspectives, ensure alignment by asking questions.

### Phase 4: Final Plan
Write final plan to the plan file with:
- Recommended approach with rationale
- Key insights from different perspectives
- Critical files that need modification

### Phase 5: Call ExitPlanMode
Your turn should only end with asking a question or calling ExitPlanMode.
```

### Explore Subagent

**File:** `agent/prompt/explore.txt`

```
You are a file search specialist. You excel at thoroughly navigating and
exploring codebases.

Guidelines:
- Use Glob for broad file pattern matching
- Use Grep for searching file contents with regex
- Use Read when you know the specific file path
- Use Bash for file operations like copying, moving, or listing
- Adapt your search approach based on the thoroughness level
- Return file paths as absolute paths
- Do not create any files or modify system state
```

### Session Summary

**File:** `agent/prompt/summary.txt`

```
Summarize what was done in this conversation. Write like a pull request
description.

Rules:
- 2-3 sentences max
- Describe the changes made, not the process
- Write in first person (I added..., I fixed...)
- Never ask questions or add new questions
- If the conversation ends with an unanswered question, preserve it
```

### Context Compaction

**File:** `agent/prompt/compaction.txt`

```
You are a helpful AI assistant tasked with summarizing conversations.

Focus on:
- What was done
- What is currently being worked on
- Which files are being modified
- What needs to be done next
- Key user requests, constraints, or preferences that should persist
- Important technical decisions and why they were made
```

### Title Generation

**File:** `agent/prompt/title.txt`

```
You are a title generator. You output ONLY a thread title. Nothing else.

Rules:
- ≤50 characters, single line, no explanations
- Same language as user message
- Focus on the main topic the user needs to retrieve
- Vary phrasing — avoid repetitive patterns
- Keep exact: technical terms, numbers, filenames
- Remove: the, this, my, a, an
- Never assume tech stack, never use tools
- NEVER respond to questions, just generate a title

Examples:
"debug 500 errors in production" → Debugging production 500 errors
"@src/auth.ts can you add refresh token support" → Auth refresh token support
```

---

## Innovations

### 1. Per-Model Prompt Routing (Most Important)

Different model families receive radically different system prompts optimized for their strengths and quirks:

| Model Family | Tone | Key Behavioral Difference |
|-------------|------|--------------------------|
| Claude | Professional, todo-driven | Heavy TodoWrite, Task delegation, objectivity |
| Gemini | Concise, convention-first | No TodoWrite, no Task tool, self-verification loops |
| GPT-4/o1/o3 | Casual, autonomous | Web-research-heavy, memory file, "whelp" tone |
| GPT-5 | Agentic, sequential | Internet-first, extensive reflection before tool calls |
| Trinity | Ultra-minimal | ≤4 lines, one tool per message, one-word answers |
| Qwen/fallback | Concise, parallel | Malware detection emphasis, no TodoWrite |

**Impact:** Each model gets instructions tuned to its native capabilities. Claude gets structured task management; GPT gets autonomous web research; Trinity gets extreme brevity constraints.

### 2. Professional Objectivity Section

```
Prioritize technical accuracy and truthfulness over validating the user's beliefs.
Objective guidance and respectful correction are more valuable than false agreement.
Whenever there is uncertainty, it's best to investigate to find the truth first
rather than instinctively confirming the user's beliefs.
```

Unique among peer systems. Explicitly instructs the agent to disagree with the user when warranted, prioritizing truth over agreeableness.

**Impact:** Prevents sycophantic responses that plague other coding agents.

### 3. Agent-as-Mode Pattern

Plan mode is not a flag — it's a distinct agent (`plan`) with:
- Its own permission ruleset (edit denied everywhere except plan file)
- A 5-phase enhanced workflow injected via synthetic `<system-reminder>` parts
- Explicit tool boundaries (explore agents only in Phase 1)
- Mandatory `ExitPlanMode` call to transition back

**Impact:** Permission enforcement at the agent level, not just prompt instructions.

### 4. Synthetic Part Injection for Workflow Control

System controls agent behavior by injecting synthetic text parts at runtime:
- Plan mode reminders wrapped in `<system-reminder>`
- Queued user messages wrapped to keep agent on track
- Max steps warning disabling all tool calls
- Build-switch transition changing operational mode

```
<system-reminder>
The user sent the following message:
[user text]

Please address this message and continue with your tasks.
</system-reminder>
```

**Impact:** Flow control without modifying the base system prompt. Agent reads injected context naturally.

### 5. Instruction File Discovery (findUp Pattern)

Dynamic instruction injection from filesystem:

```
AGENTS.md / CLAUDE.md / CONTEXT.md
  ↓
FindUp from working directory → worktree root
  ↓
Global: ~/.config/opencode/AGENTS.md, ~/.claude/CLAUDE.md
  ↓
Config: instructions[] (paths, globs, URLs)
  ↓
Each prefixed: "Instructions from: {path}\n{content}"
```

Directory-scoped instructions: when a file is read/edited, walk up the directory tree, load any AGENTS.md/CLAUDE.md files not already loaded.

**Impact:** Layer-specific conventions (e.g., `frontend/AGENTS.md` for React patterns) without cluttering the global prompt.

### 6. Memory as Instruction File (BEAST prompt)

```
# Memory
You have a memory stored in `.github/instructions/memory.instruction.md`.
If the user asks you to remember something, update the memory file.

Frontmatter:
---
applyTo: '**'
---
```

Memory is a regular instruction file loaded via the instruction system. No separate memory tool — just file read/write. The `applyTo` frontmatter controls scope.

**Impact:** Memory reuses existing instruction injection infrastructure. No new tools needed.

### 7. Prompt Cache Optimization

System prompt split into 2-part structure for Anthropic prompt caching:

```typescript
// If header (provider prompt) unchanged, maintain 2-part split
if (system.length > 2 && system[0] === header) {
  const rest = system.slice(1)
  system.length = 0
  system.push(header, rest.join("\n"))
}
```

Part 1 (provider prompt) cached across turns; Part 2 (dynamic content) varies.

**Impact:** Reduces token costs and latency for Anthropic models with prompt caching.

### 8. TodoWrite as Model-Specific Feature

TodoWrite tool availability varies by model prompt:

| Prompt | TodoWrite | Rationale |
|--------|-----------|-----------|
| PROMPT_ANTHROPIC | Yes (heavy) | Claude excels at structured task tracking |
| PROMPT_BEAST | Yes (via workflow) | GPT uses checklist-style progress |
| PROMPT_GEMINI | No | Gemini works better with inline planning |
| PROMPT_TRINITY | No | Ultra-concise mode, no overhead |
| PROMPT_QWEN | No | Fallback, minimal features |

**Impact:** Features selectively enabled based on model capability, not universally imposed.

### 9. Plugin Hook System

Event-based hooks throughout the system:

| Hook | Purpose |
|------|---------|
| `experimental.chat.system.transform` | Modify system prompt before sending |
| `experimental.chat.messages.transform` | Modify message history |
| `experimental.session.compacting` | Customize compaction |
| `tool.execute.before/after` | Tool execution interception |
| `chat.params` | Modify temperature, topP, options |
| `shell.env` | Shell environment customization |

**Impact:** Extensibility without core prompt modification.

### 10. Doom Loop Detection

Permission system detects 3 identical tool calls in a row and forces a permission check:

```typescript
permission: {
  doom_loop: "ask"  // Trigger approval if stuck
}
```

**Impact:** Prevents infinite tool-call loops without dedicated loop detection prompts.

---

## Composition System Detail

### Model Routing

```typescript
// session/system.ts
export function provider(model: Provider.Model) {
  if (model.api.id.includes("gpt-5"))    return [PROMPT_CODEX]
  if (model.api.id.includes("gpt-") ||
      model.api.id.includes("o1") ||
      model.api.id.includes("o3"))        return [PROMPT_BEAST]
  if (model.api.id.includes("gemini-"))   return [PROMPT_GEMINI]
  if (model.api.id.includes("claude"))    return [PROMPT_ANTHROPIC]
  if (model.api.id.toLowerCase().includes("trinity"))
                                          return [PROMPT_TRINITY]
  return [PROMPT_ANTHROPIC_WITHOUT_TODO]  // qwen.txt fallback
}
```

### System Prompt Assembly

```
1. Select provider prompt by model family (or use agent.prompt override)
2. Add custom system prompt from caller
3. Add user system override from last message
4. Trigger plugin hook: experimental.chat.system.transform
5. If OpenAI OAuth (Codex): send provider prompt via instructions field
6. Split into 2-part structure for prompt cache optimization
7. Add environment block (<env> working dir, platform, date)
8. Add instruction files (AGENTS.md/CLAUDE.md via findUp)
```

### Agent Configuration

```typescript
// agent/agent.ts — custom agent via config
{
  "agent": {
    "custom-agent": {
      "name": "custom-agent",
      "description": "Agent description",
      "mode": "subagent",       // or "primary" or "all"
      "model": "anthropic/claude-sonnet-4-5-20250929",
      "prompt": "Custom system prompt...",
      "temperature": 0.7,
      "steps": 10,              // Max turns before max-steps
      "permission": {
        "edit": "allow",
        "bash": "ask"
      }
    }
  }
}
```

### Permission Ruleset

```typescript
// Default permissions for all agents:
{
  "*": "allow",
  doom_loop: "ask",
  external_directory: { "*": "ask" },
  question: "deny",
  plan_enter: "deny",
  plan_exit: "deny",
  read: { "*": "allow", "*.env": "ask", "*.env.*": "ask" }
}
```

Pattern-based glob matching against tool name + arguments. Agents override defaults.

---

## Content Layout Patterns

| Pattern | Usage | Example |
|---------|-------|---------|
| Plain `.txt` files | All prompts | `anthropic.txt`, `beast.txt` |
| Markdown in text files | Structure within prompts | `# Core Mandates`, `## Workflow` |
| `<system-reminder>` tags | Runtime injections | Plan mode, queued messages |
| `<env>` XML blocks | Environment context | Working dir, platform, date |
| `<example>` blocks | Few-shot examples | Every prompt variant |
| `<gptAgentInstructions>` | Model-specific XML tags | `copilot-gpt-5.txt` sections |
| Bold-label bullets | Rule emphasis | `**Conventions:** Rigorously adhere...` |
| Numbered workflows | Sequential processes | 10-step workflow in BEAST prompt |

---

## Cross-Variant Comparison

| Feature | Anthropic | Gemini | Beast | Trinity | Codex (GPT-5) | Qwen |
|---------|-----------|--------|-------|---------|---------------|------|
| TodoWrite | Heavy | No | Yes | No | Yes | No |
| Task delegation | Yes | No | No | No | No | No |
| Professional objectivity | Yes | No | No | No | No | No |
| Web research | Optional | No | Mandatory | No | Mandatory | No |
| Memory file | No | No | Yes | No | No | No |
| Malware refusal | No | No | No | No | No | Yes |
| Max response lines | - | 3 | - | 4 | - | 4 |
| One tool per message | No | No | No | Yes | No | No |
| Parallel tool calls | Yes | Yes | No | No | No | Yes |
| Self-verification loop | No | Yes | Yes | No | Yes | No |
| Convention emphasis | Moderate | Strong | Low | Low | Low | Strong |
| Few-shot examples | 2 | 10 | 6 | 8 | 0 | 8 |
| "Keep going" instruction | No | Yes | Yes | No | Yes | No |

---

## Key Takeaways for co-cli

1. **Per-model prompt routing** — different models get radically different prompts optimized for their strengths (Claude: structured tasks, GPT: autonomous research, Gemini: concise conventions)
2. **Professional objectivity** — "respectful correction more valuable than false agreement" combats sycophantic responses
3. **Agent-as-mode pattern** — plan mode is a distinct agent with permission-enforced restrictions, not just a prompt flag
4. **Synthetic part injection** — `<system-reminder>` tags injected mid-loop for workflow control without modifying base prompts
5. **Instruction file discovery** — findUp pattern from cwd to worktree root + global + config-specified paths, with directory-scoped conventions
6. **TodoWrite as selective feature** — enabled per model capability, not universally imposed
7. **Memory as instruction file** — reuses existing instruction injection infrastructure, no separate tool
8. **Prompt cache optimization** — 2-part header/body split for Anthropic prompt caching
9. **Doom loop detection** — permission-based detection of 3 identical tool calls, not a separate prompt
10. **Plugin hooks for extensibility** — `experimental.chat.system.transform` allows external prompt modification without core changes

---

**Source:** `~/workspace_genai/opencode` — all prompts traceable from directory structure above
