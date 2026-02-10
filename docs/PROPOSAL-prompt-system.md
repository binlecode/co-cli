# DESIGN-14: Prompt System

## What & How

**Purpose:** Provide clear, tool-focused instructions to the LLM agent with minimal complexity.

**One-sentence summary:** Single markdown prompt with tool-specific sections, simple model conditionals, and project override support.

```
┌─────────────────────────────────────────────────────────────┐
│                    PROMPT ASSEMBLY                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Load: co_cli/prompts/system.md                             │
│    ↓                                                         │
│  Process model conditionals:                                │
│    [IF gemini] → include section                            │
│    [IF ollama] → include section                            │
│    ↓                                                         │
│  Load project overrides (if present):                       │
│    .co-cli/instructions.md                                  │
│    ↓                                                         │
│  = Final system prompt                                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Architecture:** Single markdown file with clear sections (identity, core principles, tool guidance, model notes), processed with simple string substitution.

**Design philosophy:** Start simple. No policy fragments, no plugins, no complex composition. Add complexity only when pain is felt, not copied from reference systems with different constraints.

---

## Core Logic

### Design Principles

**1. Single Source of Truth**
- One `system.md` file contains all prompt content
- Tool-focused organization (one section per tool)
- Human-readable markdown (no Python classes, no TypeScript functions)
- Git-friendly (easy to diff, review, edit)

**2. Minimal Dynamic Assembly**
- Model conditionals: `[IF gemini]...[ENDIF]`, `[IF ollama]...[ENDIF]`
- Project overrides: Append `.co-cli/instructions.md` if present
- No complex policy fragments, no plugin system, no inheritance hierarchies

**3. Tool-Centric Content**
- Each tool gets dedicated section with specific guidance
- Shell tool: Docker sandbox constraints, command approval flow
- Obsidian tool: Vault structure, link syntax, note patterns
- Google tools: API usage, authentication, data handling
- Web tools: Search strategies, fetch patterns

**4. Learn from Reference Systems (Why, Not What)**
- **Codex:** Policy fragments for enterprise → co-cli: one user, skip this
- **Gemini CLI:** Single file velocity → co-cli: adopt this
- **Claude Code:** Plugin extensibility → co-cli: not a platform, skip this
- **Aider:** Edit format specialization → co-cli: two models, skip this
- **All four:** Directive vs Inquiry → co-cli: brilliant, adopt this
- **All four:** Missing fact verification → co-cli: close this gap

### Prompt Structure

**File:** `co_cli/prompts/system.md`

**Sections:**

```markdown
# co-cli System Prompt

## Identity & Capabilities
[Who you are, what you can do]

## Core Principles

### Directive vs Inquiry
[Distinguish between "fix X" and "why does X happen?"]
[Inquiries: research only, no modifications]
[Directives: modify files to accomplish task]

### Fact Verification
[When tool output contradicts user assertion:]
[1. Trust tool output first]
[2. Verify calculable facts independently]
[3. Escalate contradictions]
[4. Never blindly accept corrections]

### File Handling
[Explain which files you need and why]
[Ask for files to be added to context]
[Read before editing]

## Tool Guidance

### Shell Tool
[Purpose: Execute shell commands with approval]
[Docker sandbox: constraints on file access, network]
[Approval flow: suggest commands, user approves]
[Best practices: clear descriptions, one command per suggestion]

### Obsidian Tool
[Purpose: Manage notes in Obsidian vault]
[Vault structure: explain organization]
[Link syntax: [[wiki-links]], tags, frontmatter]
[Search strategies: title vs content]

### Google Tools

#### Google Drive
[Purpose: Manage documents, spreadsheets]
[Authentication: OAuth flow]
[File operations: create, read, update, search]

#### Gmail
[Purpose: Email management]
[Read/send/search operations]
[Respect user privacy]

#### Google Calendar
[Purpose: Calendar management]
[Event operations: create, update, query]
[Timezone handling]

### Web Tools

#### Web Search (Brave API)
[Purpose: Search the web]
[Query formulation best practices]
[Result interpretation]

#### Web Fetch
[Purpose: Fetch and extract web content]
[HTML → markdown conversion]
[URL validation]

## Development Workflow

### Research → Strategy → Execute
[1. Understand request, explore codebase]
[2. Formulate plan, identify affected files]
[3. Make changes, validate with tests]

### Code Changes
[Read files before editing]
[Make surgical changes]
[Run tests to validate]
[Commit with clear messages]

### Error Handling
[When commands fail: read error, suggest fix]
[When tests fail: analyze output, fix issue]
[When stuck: ask user for guidance]

## Model-Specific Notes

[IF gemini]
### Gemini-Specific Guidance
- Explain your reasoning before tool calls
- You have strong context window (2M tokens)
- Chain-of-thought improves accuracy
- Use reasoning for complex decisions
[ENDIF]

[IF ollama]
### Ollama-Specific Guidance
- Keep responses concise
- Context window limited (typically 4K-32K)
- Prefer smaller tool outputs
- Summarize when context grows large
[ENDIF]

## Project-Specific Instructions
[Dynamically injected from .co-cli/instructions.md if present]
```

### Assembly Logic

**Function:** `get_system_prompt(settings: Settings) -> str`

**Pseudocode:**
```
function get_system_prompt(settings):
    # 1. Load base prompt
    base_prompt = read_file("co_cli/prompts/system.md")

    # 2. Process model conditionals
    if settings.llm_provider == "gemini":
        # Keep [IF gemini] sections, remove [IF ollama]
        base_prompt = base_prompt.replace("[IF gemini]", "")
        base_prompt = base_prompt.replace("[ENDIF]", "")
        base_prompt = remove_sections(base_prompt, "[IF ollama]", "[ENDIF]")
    else:  # ollama or other
        # Keep [IF ollama] sections, remove [IF gemini]
        base_prompt = base_prompt.replace("[IF ollama]", "")
        base_prompt = base_prompt.replace("[ENDIF]", "")
        base_prompt = remove_sections(base_prompt, "[IF gemini]", "[ENDIF]")

    # 3. Load project overrides
    project_instructions = load_project_instructions()
    if project_instructions:
        base_prompt += "\n\n## Project-Specific Instructions\n"
        base_prompt += project_instructions

    return base_prompt

function load_project_instructions():
    # Check for .co-cli/instructions.md in current directory
    instructions_file = Path(".co-cli/instructions.md")
    if instructions_file.exists():
        return instructions_file.read_text()

    return None

function remove_sections(text, start_marker, end_marker):
    # Remove all text between start_marker and end_marker (inclusive)
    pattern = f"{start_marker}.*?{end_marker}"
    return re.sub(pattern, "", text, flags=re.DOTALL)
```

### Key Innovations Adopted

**From Gemini CLI: Directive vs Inquiry**

Prevents unwanted modifications when user just wants analysis:

```markdown
## Directive vs Inquiry

Distinguish between two types of requests:

**Inquiry** (default assumption)
- User asks "why", "how", "what causes"
- User makes observations: "This code has a bug"
- Response: Research, analyze, explain
- Action: NO file modifications

**Directive** (explicit instruction)
- User says "fix", "add", "refactor", "update"
- User gives imperative: "Change X to Y"
- Response: Modify files to accomplish task
- Action: File modifications allowed

**Default to Inquiry unless explicit action verb present.**

Examples:
- "Why does the login fail?" → Inquiry (research only)
- "Fix the login bug" → Directive (modify files)
- "The API returns 500 errors" → Inquiry (statement, not instruction)
- "Update the API to return 200" → Directive (explicit action)
```

**From All Four Systems: Fact Verification**

Closes critical gap found in all reference systems:

```markdown
## Fact Verification

When tool output contradicts user assertion:

1. **Trust tool output first**
   - Tools access ground truth data (files, APIs, system state)
   - Tool output is deterministic and verifiable

2. **Verify calculable facts independently**
   - Dates, times: compute day-of-week, date arithmetic
   - File content: trust what was read, not memory
   - Checksums, counts: verify with tools

3. **Escalate contradictions**
   - State both values clearly
   - Explain the discrepancy
   - Ask user to verify which is correct

4. **Never blindly accept corrections**
   - Especially for deterministic facts (dates, file content)
   - User memory can be wrong
   - Cached information can be stale

Example:
- Tool returns: "2026-02-09 is Sunday"
- User says: "No, Feb 9 2026 is Monday"
- Response: "I see a discrepancy. The calendar tool shows Feb 9, 2026 is Sunday.
  Let me verify independently... [computes]... Confirmed: Sunday.
  The user may be thinking of a different date."
```

**From Aider: Tool-Specific File Handling**

Clear model for which files can be edited:

```markdown
## File Handling

### Files in Context
- Files explicitly added to chat can be edited
- Files shown by user can be edited
- Files not in context: suggest adding them first

### File Discovery
When you need to edit files not yet in context:
1. Identify which files need modification
2. Explain why each file needs changes
3. Ask user to add files to chat
4. Wait for confirmation before proposing edits

### Read Before Edit
- Always read a file before editing it
- Understand existing patterns and conventions
- Make surgical changes that fit the codebase
- Avoid making assumptions about file content
```

### What We Explicitly DON'T Include

**No policy fragments (from Codex)**
- **Why Codex has them:** Enterprise customers need different security policies
- **Why co-cli doesn't:** One user, one security model, no enterprise requirements
- **Decision:** Keep approval flow in chat loop (already working)

**No multiple edit formats (from Aider)**
- **Why Aider has them:** Supporting 50+ models, each excels at different formats
- **Why co-cli doesn't:** Supporting 2 models (Gemini, Ollama), one format sufficient
- **Decision:** Unified diff or natural language description is fine

**No plugin architecture (from Claude Code)**
- **Why Claude Code has it:** Platform play, want community marketplace
- **Why co-cli doesn't:** Personal tool, not building platform
- **Decision:** Single prompt file, simple override mechanism

**No personality system (from Codex)**
- **Why Codex has it:** Different users want different tones (pragmatic vs friendly)
- **Why co-cli doesn't:** One user, one preferred style
- **Decision:** Fixed tone in base prompt

**No complex composition (from all)**
- **Why they have it:** Different constraints led to different solutions
- **Why co-cli doesn't:** Simple use case, no need for complexity
- **Decision:** String substitution is sufficient

### Error Handling

**Missing project instructions file:**
- Gracefully skip if `.co-cli/instructions.md` doesn't exist
- No error, just use base prompt

**Invalid model conditionals:**
- If unknown model, treat as Ollama (conservative default)
- Log warning but continue

**Empty prompt sections:**
- Validate that processed prompt is non-empty
- Raise error if entire prompt disappears after processing

### Testing Strategy

**Unit tests for prompt assembly:**
```python
def test_gemini_model_conditional():
    settings = Settings(llm_provider="gemini")
    prompt = get_system_prompt(settings)
    assert "[IF gemini]" not in prompt
    assert "strong context window" in prompt
    assert "[IF ollama]" not in prompt

def test_ollama_model_conditional():
    settings = Settings(llm_provider="ollama")
    prompt = get_system_prompt(settings)
    assert "[IF ollama]" not in prompt
    assert "Context window limited" in prompt
    assert "[IF gemini]" not in prompt

def test_project_instructions_override(tmp_path):
    # Create temporary .co-cli/instructions.md
    instructions_dir = tmp_path / ".co-cli"
    instructions_dir.mkdir()
    (instructions_dir / "instructions.md").write_text("Use React for UI")

    os.chdir(tmp_path)
    prompt = get_system_prompt(Settings())
    assert "Use React for UI" in prompt

def test_no_project_instructions():
    prompt = get_system_prompt(Settings())
    assert "Project-Specific Instructions" not in prompt
```

**Manual validation:**
- Read assembled prompt for each model
- Verify tool sections are clear
- Check that examples make sense
- Confirm model-specific notes are accurate

---

## Config

No configuration needed. Prompt assembly is driven by:

| Input | Source | Default | Description |
|-------|--------|---------|-------------|
| `llm_provider` | `Settings.llm_provider` | `"gemini"` | Model provider (gemini, ollama) |
| Project instructions | `.co-cli/instructions.md` | None | Project-specific overrides |

---

## Files

| File | Purpose |
|------|---------|
| `co_cli/prompts/system.md` | Single source of truth for all prompt content |
| `co_cli/prompts.py` | Prompt assembly logic (`get_system_prompt()`) |
| `.co-cli/instructions.md` | Per-project overrides (optional, user-created) |
| `tests/test_prompts.py` | Unit tests for prompt assembly |

---

## Implementation Plan

**Phase 1: Create base prompt (Current Sprint)**
1. Write `co_cli/prompts/system.md` with all sections
2. Include Directive vs Inquiry guidance
3. Include Fact Verification guidance
4. Include tool-specific sections for all tools
5. Add model conditionals `[IF gemini]` and `[IF ollama]`

**Phase 2: Implement assembly logic (Current Sprint)**
1. Write `get_system_prompt()` in `co_cli/prompts.py`
2. Implement model conditional processing
3. Implement project instructions loading
4. Update agent initialization to use new prompt

**Phase 3: Test and validate (Current Sprint)**
1. Write unit tests for prompt assembly
2. Manual testing with Gemini model
3. Manual testing with Ollama model
4. Validate tool usage with new prompts

**Phase 4: Document and iterate (Next Sprint)**
1. Update README with prompt customization guide
2. Add example `.co-cli/instructions.md` to docs
3. Gather feedback from actual usage
4. Refine tool guidance based on observed behavior

---

## Design Rationale

### Why Single File?

**Alternatives considered:**
1. **Multiple files (like Codex)** — Policy fragments, base prompts, personalities
2. **Python classes (like Aider)** — Inheritance hierarchy, edit format specialization
3. **TypeScript functions (like Gemini CLI)** — Conditional rendering functions
4. **Plugin system (like Claude Code)** — Binary core + markdown plugins

**Decision: Single markdown file**

**Reasoning:**
- **Simplicity:** One developer, one use case, no need for complex composition
- **Maintainability:** All content in one place, easy to edit and review
- **Git-friendly:** Markdown diffs are readable, changes are traceable
- **Human-readable:** No IDE required, no code to trace, just read the file
- **Sufficient:** Model conditionals + project overrides cover all current needs

**When to reconsider:**
- Multiple users with different preferences → Add personality system
- Enterprise customers with security requirements → Add policy fragments
- Supporting 20+ models with different capabilities → Add model-specific prompts
- Community plugin marketplace → Add plugin architecture

**Current state:** None of these conditions apply. Don't over-engineer.

### Why Not Copy Reference System Patterns?

**Key insight:** Each reference system optimized for their constraints:

| System | Constraint | Design Choice | Applies to co-cli? |
|--------|------------|---------------|-------------------|
| Codex | Enterprise security needs | Policy fragments | ❌ No - one user |
| Gemini CLI | Small team velocity | Single TypeScript file | ✅ Yes - velocity matters |
| Claude Code | Platform ecosystem | Plugin architecture | ❌ No - not a platform |
| Aider | 50+ models, format diversity | 8 edit format classes | ❌ No - 2 models only |

**co-cli's constraints:**
- One developer (binle)
- Personal productivity tool
- MVP stage (not production platform)
- Python + pydantic-ai (already working)
- 2 models (Gemini, Ollama)
- 10 tools (shell, obsidian, google, web)

**Best fit:** Gemini CLI's single-file simplicity + Aider's "don't over-engineer" philosophy

### Why Include Directive vs Inquiry?

**Problem it solves:**
User: "Why does the login fail?"
Agent without guidance: *reads code, modifies auth.py to add logging*
User: "I just wanted to understand, not change anything!"

**With Directive vs Inquiry:**
User: "Why does the login fail?"
Agent: *Inquiry detected → research only*
Agent: "The login fails because... [explanation]. Would you like me to fix it?"
User: "Yes, fix it"
Agent: *Directive detected → now makes changes*

**Evidence:** Gemini CLI added this after experiencing "berai am jit" problem (premature modifications). It works.

**Decision:** Include this. Solves real problem, minimal complexity.

### Why Include Fact Verification?

**Problem it solves:**
Tool: "2026-02-09 is Sunday"
User: "No, it's Monday"
Agent without guidance: "You're right, I apologize for the error."
Result: Agent now has wrong information.

**With Fact Verification:**
Tool: "2026-02-09 is Sunday"
User: "No, it's Monday"
Agent: *Detects contradiction, verifies independently*
Agent: "I see a discrepancy. Let me verify... [computes]... The calendar shows Sunday. Perhaps you're thinking of a different date?"
Result: Correct information preserved.

**Evidence:** All four reference systems lack this. Gap analysis found calendar tool contradiction acceptance issue.

**Decision:** Include this. Closes critical gap, prevents data integrity errors.

### Why No Edit Format Specialization?

**Aider's reasoning:** Different models excel at different formats
- GPT-4: Great at SEARCH/REPLACE blocks
- Claude-2: Better at whole file rewrites
- DeepSeek: Excels at unified diffs

**Aider supports:** 8 formats across 50+ models

**co-cli's reality:**
- Gemini: One format works fine
- Ollama: Same format works fine
- Total: 2 models, no evidence of format problems

**Decision:** Skip this. No pain felt, don't add complexity preemptively.

**When to reconsider:** If we observe model struggling with current edit format, try alternatives then.

### Why No Plugin Architecture?

**Claude Code's reasoning:** Want community to extend without modifying core

**Claude Code achieves:**
- 79+ community plugins
- Marketplace for distribution
- Versioned, isolated extensions

**co-cli's reality:**
- Personal tool, no community
- Not building platform
- Can edit source directly

**Decision:** Skip this. Significant complexity for zero current benefit.

**When to reconsider:** If open-sourcing with goal of community contributions. Not now.

### Why Simple String Substitution?

**Alternatives:**
1. **Jinja templates** — Full templating engine
2. **Python f-strings** — Dynamic code generation
3. **YAML + parser** — Structured configuration
4. **AST manipulation** — Parse and transform

**Decision: Simple regex string substitution**

**Reasoning:**
- Current needs: Model conditionals, project overrides
- Sufficient: `[IF gemini]...[ENDIF]` covers it
- Complexity: Regex is built-in, no dependencies
- Debuggable: Easy to print intermediate states
- Testable: Clear input/output, no side effects

**When to reconsider:** If conditionals become nested, complex, or numerous. Current plan has 2 conditionals. Not complex.

---

## Success Metrics

**Prompt quality:**
- ✅ Agent correctly distinguishes Directive vs Inquiry
- ✅ Agent verifies facts when contradictions occur
- ✅ Tool usage follows guidance (shell, obsidian, google, web)
- ✅ Model-specific notes improve behavior (Gemini explains reasoning, Ollama stays concise)

**Maintainability:**
- ✅ Can update tool guidance in < 5 minutes
- ✅ Prompt diffs are readable in git
- ✅ No "where is this coming from?" debugging sessions
- ✅ New developer can understand system in < 30 minutes

**Simplicity:**
- ✅ Prompt assembly logic < 50 lines
- ✅ No dependencies beyond stdlib (re, pathlib)
- ✅ Single file to read for understanding
- ✅ Zero prompt-related production issues

---

## Future Enhancements (Post-MVP)

**Only add when pain is felt:**

**Multi-model support:**
- If supporting Claude, GPT, etc. → Add `[IF claude]` conditionals
- If models diverge significantly → Consider model-specific prompt files
- **Trigger:** Supporting 5+ models with different behaviors

**Tool-specific few-shot examples:**
- If agent misuses tools repeatedly → Add examples section
- If new tools are frequently misunderstood → Add structured examples
- **Trigger:** >3 repeated tool usage errors per week

**Personality system:**
- If sharing with team → Add personality switching (pragmatic vs friendly)
- If different use cases need different tones → Add personality overlays
- **Trigger:** Multiple users with conflicting preferences

**Approval policy fragments:**
- If security requirements change → Add policy fragment system
- If different projects need different approval flows → Add policy composition
- **Trigger:** Enterprise customer or compliance requirement

**Plugin architecture:**
- If community wants to extend → Add plugin system
- If building platform → Add marketplace infrastructure
- **Trigger:** >10 external contributors wanting to add features

**Current decision:** None of these pains exist today. Start simple. Add complexity only when necessary.

---

## Prompt Writing Craft

### Overview: Learning from Master Prompts

After studying 1000+ lines of verbatim prompts from Codex, Gemini CLI, Claude Code, Aider, and OpenCode, clear patterns emerge in **how** to write effective LLM instructions. This section distills the craft: structure, sectioning, wording, tone, and techniques that make prompts work.

**Key insight:** Prompt writing is closer to technical specification than creative writing — clarity, specificity, and examples trump elegance.

### Content Structure Patterns

#### Pattern 1: Front-Load Identity & Core Philosophy

**All top systems** start with WHO and WHY before WHAT and HOW.

**Codex approach:**
```markdown
You are a coding agent running in the Codex CLI, a terminal-based coding assistant.
Codex CLI is an open source project led by OpenAI.
You are expected to be precise, safe, and helpful.

Your capabilities:
- Receive user prompts and other context
- Communicate by streaming thinking & responses
- Emit function calls to run commands and apply patches
```

**Why it works:**
- Sets frame/context first
- Establishes identity (you are X, not just "do Y")
- Clarifies expected behavior before rules
- Uses **"You are..."** framing (not "The agent should...")

**Gemini CLI approach:**
```markdown
You are Gemini CLI, an interactive CLI agent specializing in software engineering tasks.
Your primary goal is to help users safely and effectively.
```

**Why it works:**
- One sentence, crystal clear
- **"Your primary goal"** anchors everything that follows
- Uses present tense, direct address

**Anti-pattern (what NOT to do):**
```markdown
❌ This system is designed to help with coding tasks using various tools.
❌ The assistant will attempt to understand user requests and...
```

Problems: Passive voice, vague "system", no identity, wordy.

**co-cli adoption:**
```markdown
You are Co, a CLI assistant running in the user's terminal.
Your goal: Get things done quickly and accurately using available tools.
```

#### Pattern 2: Hierarchical Sectioning (General → Specific)

**Consistent across all systems:**

```
Level 1: Core Philosophy (who, why, values)
Level 2: Behavioral Rules (how to think, how to respond)
Level 3: Operational Workflows (when to do what)
Level 4: Tool-Specific Guidance (detailed how-to per tool)
Level 5: Edge Cases & Reminders (what not to do)
```

**Codex structure:**
```markdown
# How you work
## Personality
[General tone and values]

## Responsiveness
### Preamble messages
[When to update user]

## Planning
[When to use plans]

## Task execution
[What you MUST adhere to]

## Validating your work
[How to verify success]
```

**Why this ordering works:**
- **Personality first** sets emotional register for everything else
- **Responsiveness** teaches when to speak
- **Planning** teaches when to think before acting
- **Execution** teaches what to do
- **Validation** teaches how to confirm success

**Gemini CLI structure:**
```markdown
# Core Mandates
## Security Protocols
## Engineering Standards

# Primary Workflows
## Development Lifecycle

# Operational Guidelines
## Tone and Style
## Security and Safety Rules

# Final Reminder
```

**Why this ordering works:**
- **Core Mandates** = non-negotiable rules (security, quality)
- **Workflows** = how to approach problems
- **Guidelines** = style and safety
- **Final Reminder** = critical emphasis at end

**Anti-pattern (what NOT to do):**
```markdown
❌ Random order: Tool X, then philosophy, then tool Y, then workflow
❌ Flat structure: Everything at same level
❌ Details before principles: How to use grep before why you're a CLI assistant
```

**co-cli adoption:**
```markdown
# Identity & Goal
# Core Principles (Directive/Inquiry, Fact Verification)
# Workflows (Research → Strategy → Execute)
# Tool Guidance (one section per tool)
# Model-Specific Notes
```

#### Pattern 3: Rule Formatting (MUST vs SHOULD vs MAY)

**Codex uses explicit modal verbs:**
```markdown
You MUST adhere to the following:
- Fix the problem at the root cause
- Avoid unneeded complexity
- Do not attempt to fix unrelated bugs

You should:
- Start as specific as possible
- Make your way to broader tests

Feel free to:
- Be ambitious for brand new tasks
```

**Levels:**
- **MUST** = non-negotiable, will break things if violated
- **Should** = strong recommendation, deviation needs reason
- **Feel free to** = encouraged but optional

**Gemini CLI uses imperative lists:**
```markdown
- **Credential Protection:** Never log, print, or commit secrets
- **Source Control:** Do not stage or commit changes unless requested
- **Protocol:** Do not ask for permission to use tools
```

**Pattern:** `[Bold label]: [Imperative statement]`

**Why it works:**
- Scannable: Bold keywords jump out
- Action-oriented: Each is a do/don't
- Parallel structure: Every item follows same pattern

**Claude Code uses confidence scores:**
```markdown
## Confidence Scoring (0-100)
- 0: False positive
- 25: Might be real, might be false positive
- 50: Moderately confident, possibly a nitpick
- 75: Highly confident, verified, important
- 100: Absolutely certain, confirmed

**Only report issues with confidence >= 80.**
```

**Why this works:**
- Quantifies uncertainty
- Gives model calibration anchor
- Clear threshold for action

**Anti-pattern (what NOT to do):**
```markdown
❌ Try to avoid committing unless you're pretty sure the user wants it
❌ Be careful about secrets
❌ You might want to consider asking before destructive actions
```

Problems: Hedging language, no clear rule, wishy-washy.

**co-cli adoption:**
```markdown
### Core Rules

You MUST:
- Trust tool output over user assertions (verify contradictions)
- Distinguish Inquiry from Directive (default to Inquiry)
- Read files before editing them

You should:
- Explain your reasoning for non-trivial decisions
- Run tests after making changes
- Keep responses concise (< 3 sentences typical)

You may:
- Ask clarifying questions when ambiguous
- Suggest alternative approaches if stuck
```

#### Pattern 4: Examples (Positive + Negative + Contrast)

**All top systems use examples heavily.** But they differ in **format**.

**Codex style (narrative examples):**
```markdown
### Preamble messages
**Examples:**
- "I've explored the repo; now checking the API route definitions."
- "Next, I'll patch the config and update the related tests."
- "Ok cool, so I've wrapped my head around the repo. Now digging into the API routes."

**Exception:** Avoid adding a preamble for every trivial read
```

**Why it works:**
- Real voice examples
- Shows variety (not just one template)
- Exception highlights boundary

**Gemini CLI style (inline examples):**
```markdown
## Directive vs Inquiry

**Inquiry** (default assumption)
- User asks "why", "how", "what causes"
- Response: Research, analyze, explain
- Action: NO file modifications

**Directive** (explicit instruction)
- User says "fix", "add", "refactor"
- Response: Modify files to accomplish task
- Action: File modifications allowed

Examples:
- "Why does the login fail?" → Inquiry (research only)
- "Fix the login bug" → Directive (modify files)
```

**Why it works:**
- Paired definitions + examples
- Arrows (→) show mapping
- Contrast highlights difference

**Aider style (code examples):**
```markdown
**Few-shot examples** (embedded as assistant behavior):
```python
dict(role="user", content="Change get_factorial() to use math.factorial")
dict(role="assistant", content="""Here are the *SEARCH/REPLACE* blocks:
path/to/file.py
```python
<<<<<<< SEARCH
def get_factorial(n):
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result
=======
import math

def get_factorial(n):
    return math.factorial(n)
>>>>>>> REPLACE
```
""")
```

**Why it works:**
- Shows exact expected format
- Model learns from imitation
- Demonstrates tool use, not just description

**OpenCode style (contrast pairs):**
```markdown
Examples:
user: 2 + 2
assistant: 4

user: is 11 a prime number?
assistant: Yes

user: what command should I run to list files?
assistant: ls
```

**Why it works:**
- Brevity shown, not described
- Multiple examples reinforce pattern
- Simple format: `user: X\nassistant: Y`

**Anti-pattern (what NOT to do):**
```markdown
❌ "For example, you might say something like..."
❌ One example only (model can't generalize)
❌ Vague examples: "respond appropriately"
```

**co-cli adoption:**
```markdown
### Directive vs Inquiry Examples

| User Input | Classification | Action |
|------------|----------------|--------|
| "Why does login fail?" | Inquiry | Research, explain, NO modifications |
| "Fix the login bug" | Directive | Modify auth.py to resolve issue |
| "The API returns 500" | Inquiry | Statement, investigate cause |
| "Update API to return 200" | Directive | Modify response handling |

### Tool Output Examples

**Good (show display verbatim):**
```
User: "List my emails"
Tool returns: {"display": "1. Subject: Meeting...\n2. Subject: Report..."}
Assistant: [shows display exactly as formatted]
```

**Bad (reformatting):**
```
User: "List my emails"
Tool returns: {"display": "1. Subject: Meeting..."}
Assistant: "You have 2 emails: Meeting and Report" [WRONG - reformatted]
```
```

#### Pattern 5: Voice & Tone (Authoritative vs Collaborative)

**Codex tone (collaborative peer):**
```markdown
## Personality
Your default personality and tone is concise, direct, and friendly.
You communicate efficiently, always keeping the user clearly informed.

## Collaboration posture:
- Treat the user as an equal co-builder
- When the user is in flow, stay succinct
- When blocked, get more animated with hypotheses and experiments
```

**Keywords:** "equal co-builder", "in flow", "animated"
**Effect:** Feels like pairing with a senior engineer

**Gemini CLI tone (clinical precision):**
```markdown
## Tone and Style
- **Role:** A senior software engineer and collaborative peer programmer.
- **High-Signal Output:** Focus exclusively on intent and technical rationale.
  Avoid filler, apologies, and tool-use narration.
- **Concise & Direct:** Fewer than 3 lines of text per response whenever practical.
```

**Keywords:** "High-Signal", "exclusively", "Fewer than 3 lines"
**Effect:** Feels like working with someone who values your time

**OpenCode tone (professional objectivity):**
```markdown
# Professional objectivity
Prioritize technical accuracy and truthfulness over validating the user's beliefs.
Focus on facts and problem-solving, providing direct, objective technical info
without unnecessary superlatives, praise, or emotional validation.
Objective guidance and respectful correction are more valuable than false agreement.
```

**Keywords:** "respectful correction", "more valuable than false agreement"
**Effect:** Feels like someone who'll tell you when you're wrong

**Claude Code tone (coach/mentor):**
```markdown
You are a senior software architect who delivers comprehensive, actionable
architecture blueprints by deeply understanding codebases and making confident
architectural decisions.

Make confident architectural choices rather than presenting multiple options.
```

**Keywords:** "confident", "actionable", "decisive"
**Effect:** Feels like an architect leading design review

**Anti-pattern (what NOT to do):**
```markdown
❌ Apologetic: "I apologize for any confusion..."
❌ Hedging: "You might possibly want to maybe consider..."
❌ Over-enthusiastic: "Great question! I'm so excited to help! 🎉"
❌ Robotic: "Acknowledged. Processing request. Generating response."
```

**co-cli adoption (terse professional):**
```markdown
### Response Style
- **Be terse:** Users want results, not explanations
- **High-signal output:** Focus on intent and technical rationale
  Avoid filler, apologies, and tool-use narration
- **Professional objectivity:** Prioritize technical accuracy over validating beliefs
  Respectful correction is more valuable than false agreement
- **Keep going:** Complete the request thoroughly before yielding
```

#### Pattern 6: Reminders & Emphasis (Repetition with Purpose)

**All systems use strategic repetition**, but placement matters.

**Pattern A: Triple Emphasis (Beginning, Middle, End)**

**Gemini CLI:**
```markdown
[Core Mandates section]
- **Protocol:** Do not ask for permission to use tools

[Operational Guidelines section]
## Security and Safety Rules
- Before executing commands... provide brief explanation

[Final Reminder]
Your core function is efficient and safe assistance.
Never make assumptions about file contents; use read_file.
Finally, you are an agent - please keep going until resolved.
```

**Why it works:**
- First mention: Sets rule
- Middle mention: Contextualizes rule
- Final mention: Reinforces rule
- Different phrasing each time (not copy-paste)

**Pattern B: In-Context Reminders**

**Codex (within specific sections):**
```markdown
## Planning
Use a plan when:
- The task is non-trivial
- There are logical dependencies

Do not use plans for simple or single-step queries.  [NEGATIVE REMINDER]
```

**Why it works:**
- Negative reminder after positive guidance
- Prevents over-application of technique

**Pattern C: Critical Rules at Top AND Bottom**

**OpenCode (PROMPT_BEAST):**
```markdown
[Line 1]
You are opencode, an agent - keep going until resolved.

[Line 10]
You MUST iterate and keep going until the problem is solved.

[Line 20]
Only terminate your turn when you are sure the problem is solved.
```

**Why it works:**
- Repetition creates emphasis
- Spaced repetition aids retention
- Different wording tests understanding

**Anti-pattern (what NOT to do):**
```markdown
❌ One mention only (model may miss it)
❌ Exact copy-paste repetition (sounds robotic)
❌ Reminder in wrong context (confuses scope)
```

**co-cli adoption:**
```markdown
[Beginning]
### Response Style
- **Keep going:** Complete the request thoroughly before yielding

[Middle - Tool section]
### Shell Tool
Execute commands until task is complete. Don't stop after first command.

[End - Final Note]
Remember: You are an agent. Keep going until the user's query is fully resolved.
```

### Sectioning Strategies (Information Architecture)

#### Strategy 1: Inverted Pyramid (Most Important First)

**Journalism principle:** Put critical info first, details later.

**Gemini CLI implementation:**
```markdown
# Core Mandates  [CRITICAL - Line 15]
## Security Protocols  [MOST CRITICAL - Line 20]
- Never log secrets  [FIRST ITEM]

# Primary Workflows  [IMPORTANT - Line 100]
# Operational Guidelines  [NICE-TO-HAVE - Line 300]
# Model-Specific Notes  [CONDITIONAL - Line 500]
```

**Reading order = Priority order**

**Aider implementation:**
```markdown
## Main System Prompt  [CRITICAL]
### Edit format instructions  [VERY IMPORTANT]
### File handling  [IMPORTANT]
### Repository map  [NICE-TO-HAVE]
### Shell commands  [CONDITIONAL]
```

**Why it works:**
- If model hits context limit, critical info already seen
- First impression sets frame
- Reinforces what matters most

#### Strategy 2: Chunking with Clear Boundaries

**All systems use visual separators**, but techniques vary.

**Codex (markdown headers + blank lines):**
```markdown
## Responsiveness

### Preamble messages

**Examples:**
- Example 1
- Example 2

## Planning
```

**Pattern:** H2 → H3 → Bold → List → (2 blank lines) → Next H2

**Gemini CLI (headers + bold labels):**
```markdown
## Engineering Standards

- **Contextual Precedence:** Instructions in GEMINI.md take precedence
- **Conventions & Style:** Adhere to existing workspace conventions
```

**Pattern:** H2 → Bullet with **Bold Label:** → Content

**Claude Code (headers + XML tags):**
```markdown
## Output Guidance

Deliver a decisive, complete architecture blueprint:
- Patterns & Conventions Found
- Architecture Decision

<example>
  <context>User needs authentication</context>
  <output>### Architecture Decision: JWT with...</output>
</example>
```

**Pattern:** H2 → Structured list → XML-wrapped example

**Why boundaries matter:**
- Model parses structure better with clear delimiters
- Humans scan faster with visual chunks
- Easy to reference specific sections ("see ## Responsiveness")

**Anti-pattern (what NOT to do):**
```markdown
❌ Wall of text (no headers)
❌ Inconsistent header levels (H2 → H4 → H3)
❌ No visual separation between concepts
```

**co-cli adoption:**
```markdown
## Core Principles
[3-4 key principles with ### subheaders]

---

## Workflows
[Research → Strategy → Execute]

---

## Tool Guidance
### Shell Tool
[Detailed guidance]

### Obsidian Tool
[Detailed guidance]

---

## Model-Specific Notes
```

#### Strategy 3: Progressive Disclosure (Tiers of Detail)

**Claude Code excels at this** with their skill system:

```
SKILL.md (2,400 lines - main guidance)
├── Core concepts (first 500 lines)
├── Workflow (next 800 lines)
├── Examples (next 600 lines)
└── Edge cases (final 500 lines)

references/ (25,000 lines - deep dives, NOT loaded by default)
├── architecture-patterns.md
├── api-design-principles.md
└── testing-strategies.md
```

**Rule:** Main prompt has essentials, references have depth.

**Aider implementation:**
```python
class CoderPrompts:
    main_system = """[500 lines of core guidance]"""

    system_reminder = """[50 lines - injected at end if context allows]"""

    lazy_prompt = """[Injected only for lazy models]"""
    overeager_prompt = """[Injected only for overeager models]"""
```

**Rule:** Base always included, reminders conditional.

**Why progressive disclosure works:**
- Prevents overwhelming model with everything upfront
- Allows context-sensitive depth
- Separates "must know" from "nice to know"

**co-cli adoption:**
```markdown
[system.md - always loaded]
- Identity & Core Principles (300 lines)
- Workflows (200 lines)
- Tool Guidance (400 lines)

[.co-cli/instructions.md - loaded if present]
- Project-specific conventions
- Codebase architecture notes
- Team preferences
```

### Wording Techniques (Language that Works)

#### Technique 1: Active Voice + Direct Address

**Effective:**
```markdown
✅ "You are a CLI assistant"
✅ "Use tools proactively"
✅ "Trust tool output first"
✅ "Read files before editing"
```

**Ineffective:**
```markdown
❌ "The assistant is a CLI tool"
❌ "Tools should be used proactively"
❌ "Tool output should be trusted"
❌ "Files should be read before editing"
```

**Why active works:**
- Creates agency ("you do X" not "X should be done")
- Direct address engages attention
- Shorter, clearer sentences

#### Technique 2: Imperatives (Commands, Not Suggestions)

**Codex:**
```markdown
Do not use plans for simple queries.
Fix the problem at the root cause.
NEVER add copyright headers unless requested.
```

**Gemini CLI:**
```markdown
Never log secrets.
Rigorously adhere to existing conventions.
Do not ask for permission to use tools.
```

**Pattern:** `[Never|Always|Do not] + [verb] + [object]`

**Why imperatives work:**
- Unambiguous (not "you might want to consider maybe...")
- Action-oriented (verb first)
- Models respond better to commands than suggestions

**Anti-pattern:**
```markdown
❌ "It would be good to avoid..."
❌ "Consider not using..."
❌ "Try to refrain from..."
```

#### Technique 3: Scoped Negatives (What NOT to Do)

**OpenCode:**
```markdown
IMPORTANT: You must NEVER generate or guess URLs for the user unless you
are confident that the URLs are for helping the user with programming.
```

**Pattern:** `NEVER [action] unless [exception]`

**Aider:**
```markdown
Don't try and edit any existing code without asking me to add the files to the chat!
```

**Pattern:** `Don't [action] without [precondition]`

**Codex:**
```markdown
Do not attempt to fix unrelated bugs or broken tests.
```

**Pattern:** `Do not [action] [scope]`

**Why scoped negatives work:**
- Prevents common mistakes
- Defines boundaries explicitly
- Models need to know what NOT to do, not just what to do

**Anti-pattern:**
```markdown
❌ "Be careful with URLs" [too vague]
❌ "Don't do bad things" [what are bad things?]
❌ "Avoid mistakes" [which mistakes?]
```

#### Technique 4: Concrete Over Abstract

**Effective (Codex):**
```markdown
✅ Use `rg` or `rg --files` because rg is much faster than alternatives.
```

**Ineffective:**
```markdown
❌ Prefer performant search tools.
```

**Effective (Gemini CLI):**
```markdown
✅ Fewer than 3 lines of text per response whenever practical.
```

**Ineffective:**
```markdown
❌ Be concise in responses.
```

**Effective (Claude Code):**
```markdown
✅ Only report issues with confidence >= 80.
```

**Ineffective:**
```markdown
❌ Only report high-confidence issues.
```

**Why concrete works:**
- Measurable (3 lines, >= 80, specific tool name)
- Testable (can verify compliance)
- Unambiguous (no interpretation needed)

#### Technique 5: Positive + Negative + Correct Example

**Pattern:** Show what TO do, what NOT to do, and HOW to do it right.

**Gemini CLI (Directive vs Inquiry):**
```markdown
**Inquiry:**
- User asks "why"
- Response: Research, analyze
- Action: NO modifications

**Directive:**
- User says "fix"
- Response: Modify files
- Action: File modifications

Examples:
- "Why does X fail?" → Inquiry [CORRECT CLASSIFICATION]
- "Fix X" → Directive [CORRECT CLASSIFICATION]
- "X is broken" → Inquiry (statement, not instruction) [EDGE CASE]
```

**Structure:**
1. Positive definition (what it is)
2. Negative definition (what it's not)
3. Examples showing correct application

**Aider (File handling):**
```markdown
✅ Files in context can be edited
✅ Files shown by user can be edited
❌ Files not in context: ask to add first

Example:
User: "Update auth.py"
✅ If auth.py in context: proceed
❌ If auth.py not in context: "Let me read auth.py first..."
```

**Why this works:**
- Positive sets the rule
- Negative defines boundary
- Example shows application

### Anti-Patterns to Avoid

#### Anti-Pattern 1: Apologetic Language

**Bad examples from early systems:**
```markdown
❌ "I apologize if I misunderstood"
❌ "Sorry for any confusion"
❌ "I'm sorry, but I cannot..."
```

**Why it's bad:**
- Wastes tokens
- Sounds uncertain
- Creates adversarial frame (user vs assistant)

**Better:**
```markdown
✅ "Clarifying: Are you asking for X or Y?"
✅ "I need more information about Z"
✅ "That's outside my capabilities. I can help with..."
```

#### Anti-Pattern 2: Metacommentary

**Bad:**
```markdown
❌ "Now I will use the search tool to find..."
❌ "I am now thinking about how to approach this..."
❌ "Let me explain what I'm doing..."
```

**Why it's bad (Gemini CLI calls this out explicitly):**
- "Tool-use narration" wastes tokens
- User sees tool calls anyway
- Breaks flow

**Better:**
```markdown
✅ [Just call the tool]
✅ "Searching for API endpoints" [Brief preamble OK]
```

**Exception:** Preambles are OK when they add value (Codex style).

#### Anti-Pattern 3: Hedging

**Bad:**
```markdown
❌ "You might want to consider possibly..."
❌ "It could be that maybe..."
❌ "Perhaps it would be good to think about..."
```

**Why it's bad:**
- Sounds uncertain (models mimic uncertainty)
- Wastes tokens
- Doesn't give clear guidance

**Better:**
```markdown
✅ "Use approach X because Y"
✅ "Two options: A or B. Recommend A because..."
✅ "If uncertain, ask user for clarification"
```

#### Anti-Pattern 4: Scope Creep in Prompts

**Bad:**
```markdown
❌ [200-line section on code style]
❌ [500-line section on Git best practices]
❌ [1000-line guide to software architecture]
```

**Why it's bad:**
- Dilutes focus
- Context window waste
- Belongs in project instructions, not system prompt

**Better:**
```markdown
✅ "Follow existing code style conventions"
✅ "Use standard Git workflow"
✅ "Respect architecture patterns in codebase"
✅ "See .co-cli/instructions.md for project specifics"
```

**Rule:** System prompt = HOW to behave. Project instructions = WHAT to do.

### Putting It All Together: co-cli Prompt Blueprint

Based on all techniques above, here's the structure for `co_cli/prompts/system.md`:

```markdown
# Co CLI System Prompt

## Identity & Goal [50 lines]
You are Co, a CLI assistant running in the user's terminal.
Your goal: Get things done quickly and accurately using available tools.

[Active voice, direct address, concrete goal]

---

## Core Principles [150 lines]

### Directive vs Inquiry [GEMINI CLI TECHNIQUE]
[Positive + Negative + Examples]
[Contrast table showing classification]

### Fact Verification [GAP CLOSURE]
[Imperatives: "Trust tool output first"]
[Scoped guidance: "When to escalate"]
[Example showing correct behavior]

### Response Style [OPENCODE TECHNIQUE]
- **Be terse:** Users want results, not explanations
- **High-signal output:** Focus on intent, avoid filler
- **Professional objectivity:** Respectful correction > false agreement
- **Keep going:** Complete request thoroughly

[Parallel structure: Bold label + Imperative]

---

## Workflows [100 lines]

### Research → Strategy → Execute [CODEX/GEMINI PATTERN]
[Numbered steps, concrete actions]

### Tool Chaining [CONCRETE EXAMPLES]
[Read before edit, test after change]

---

## Tool Guidance [400 lines]

### Shell Tool
**Purpose:** Execute commands in Docker sandbox
**Approval:** Required (except safe commands: ls, pwd, cat, git status)
**Best practices:**
- Clear descriptions for each command
- One command per suggestion
- Explain why command is needed

[Structured: Purpose → Approval → Best Practices]

### Obsidian Tool
[Same structure]

### Google Tools
#### Drive [Sub-section]
#### Gmail [Sub-section]
#### Calendar [Sub-section]

### Web Tools
#### Search [Sub-section]
#### Fetch [Sub-section]

---

## Model-Specific Notes [50 lines]

[IF gemini]
### Gemini Guidance
- Explain reasoning before tool calls
- Strong context window (2M tokens)
- Use chain-of-thought for complex decisions
[ENDIF]

[IF ollama]
### Ollama Guidance
- Keep responses concise
- Limited context (4K-32K)
- Summarize when context grows
[ENDIF]

[Conditional injection technique]

---

## Final Reminders [50 lines]

[Strategic repetition of critical rules]
- You are an agent: keep going until resolved
- Trust tool output: verify contradictions
- Default to Inquiry: only modify on explicit directive

[Triple emphasis: rules repeated from earlier in different words]
```

**Total:** ~800 lines (manageable, scannable, effective)

---

## Conclusion

**Design summary:** Single markdown prompt with tool-focused sections, model conditionals, and project overrides. Dead simple assembly with string substitution.

**Philosophy:** Start simple. Learn from reference systems (why they chose their designs) without copying patterns that solve problems we don't have. Add complexity only when pain is felt.

**Key innovations adopted:**
- Directive vs Inquiry (from Gemini CLI) — solves real problem
- Fact Verification (closes gap in all systems) — prevents errors

**Key complexity avoided:**
- Policy fragments (from Codex) — no enterprise needs
- Edit format specialization (from Aider) — only 2 models
- Plugin architecture (from Claude Code) — not a platform
- Complex composition (from all) — no benefit for simple use case

**Prompt writing craft learned:**
- Front-load identity & philosophy
- Use hierarchical sectioning (general → specific)
- Active voice + imperatives (not suggestions)
- Concrete over abstract (3 lines, >= 80 confidence, specific tool names)
- Positive + Negative + Example pattern
- Strategic repetition (beginning, middle, end)
- Avoid apologetics, hedging, metacommentary

**Next steps:**
1. Write `system.md` applying all learned techniques
2. Implement `get_system_prompt()` with conditionals
3. Test with both Gemini and Ollama
4. Validate behavior, iterate based on real usage

**Success metric:** Agent does what you want, prompts are easy to change, writing style is clear and effective, no over-engineering regrets.
