# Gemini CLI Prompt Assembly Flowchart

## From User Configuration → Final LLM Prompt

### Example Scenario
**User wants:** "Interactive mode, Gemini 3 model, plan mode enabled, in a git repo"

---

## Step 1: User Configuration

```
User starts Gemini CLI with runtime context:
├─ interactive: true                  # Interactive vs autonomous
├─ gemini3: true                      # Model version (affects behavior)
├─ sandbox: "container"               # Sandbox type
├─ gitRepo: true                      # Is this a git repository?
├─ planMode: { enabled: true }        # Planning workflow
├─ skills: true                       # Skill system enabled
├─ codebaseInvestigator: true         # Sub-agent available
├─ writeTodos: false                  # Todo tracking
└─ shellEfficiency: false             # Shell optimization hints
```

---

## Step 2: Conditional Block Selection (What gets included?)

**KEY DIFFERENCE FROM CODEX:** All content lives in ONE TypeScript function (`snippets.ts`), not separate files!

```typescript
function getSystemPrompt(config) {
  let prompt = '';

  // Each section checks config and conditionally adds content

  if (config.interactive) {
    prompt += '[INTERACTIVE PREAMBLE]'
  } else {
    prompt += '[AUTONOMOUS PREAMBLE]'
  }

  prompt += '[CORE MANDATES - ALWAYS INCLUDED]'

  if (config.gemini3) {
    prompt += '[EXPLAIN BEFORE ACTING]'
  }

  if (config.skills) {
    prompt += '[SKILL GUIDANCE]'
  }

  // ... continues for all config flags
}
```

---

## Step 3: Content Assembly (Build the prompt string)

```
SECTION 1: Preamble (CONDITIONAL)
┌─────────────────────────────────────────────────────────┐
│ IF interactive:                                         │
│   "You are Gemini CLI, an interactive CLI agent..."    │
│                                                         │
│ ELSE:                                                   │
│   "You are Gemini CLI, an autonomous CLI agent..."     │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 2: Core Mandates (ALWAYS INCLUDED)
┌─────────────────────────────────────────────────────────┐
│ # Core Mandates                                         │
│                                                         │
│ ## Security Protocols                                   │
│ - Never log/commit secrets                             │
│ - Protect .env, .git files                             │
│                                                         │
│ ## Engineering Standards                                │
│ - **Directive vs Inquiry**: Critical distinction       │
│   - Directive: "Fix X" → modify files                  │
│   - Inquiry: "Why X?" → research only                  │
│   - DEFAULT TO INQUIRY unless explicit action          │
│                                                         │
│ - **Conventions**: Analyze surrounding files, mimic    │
│ - **Libraries**: NEVER assume, verify usage            │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 3: Model-Specific (CONDITIONAL - Gemini 3)
┌─────────────────────────────────────────────────────────┐
│ IF gemini3:                                             │
│   - **Explain Before Acting**: Never call tools in     │
│     silence. Provide one-sentence explanation first.   │
│                                                         │
│ IF skills:                                              │
│   - **Skill Guidance**: Follow <instructions> from     │
│     activated skills                                   │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 4: Primary Workflows (ALWAYS INCLUDED)
┌─────────────────────────────────────────────────────────┐
│ # Primary Workflows                                     │
│                                                         │
│ ## Development Lifecycle                                │
│ Research → Strategy → Execution                         │
│ - Plan: Define approach + testing strategy             │
│ - Act: Apply changes + include tests                   │
│ - Validate: Run tests + standards                      │
│                                                         │
│ IF codebaseInvestigator:                                │
│   "Utilize specialized sub-agents (e.g.,               │
│    `codebase_investigator`) for complex analysis"      │
│ ELSE:                                                   │
│   "Use 'grep' and 'glob' search tools extensively"     │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 5: Operational Guidelines (ALWAYS INCLUDED)
┌─────────────────────────────────────────────────────────┐
│ # Operational Guidelines                                │
│                                                         │
│ ## Tone and Style                                       │
│ - Senior software engineer, collaborative peer         │
│ - Concise & Direct: <3 lines per response             │
│                                                         │
│ IF gemini3:                                             │
│   "- No Chitchat: Avoid preambles/postambles"         │
│ ELSE:                                                   │
│   "- Minimal conversation, focus on work"             │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 6: Sandbox Notice (CONDITIONAL)
┌─────────────────────────────────────────────────────────┐
│ IF sandbox === "macos":                                 │
│   # macOS Seatbelt                                     │
│   You are under macOS seatbelt with limited access.   │
│                                                         │
│ ELSE IF sandbox === "container":                        │
│   # Sandbox                                            │
│   You are in a container with limited access.         │
│                                                         │
│ ELSE (none):                                            │
│   # Outside of Sandbox                                 │
│   Running directly on system. Remind user to enable   │
│   sandboxing for critical commands.                    │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 7: Git Workflow (CONDITIONAL)
┌─────────────────────────────────────────────────────────┐
│ IF gitRepo:                                             │
│   # Git Repository                                     │
│   - NEVER stage or commit unless instructed            │
│   - When asked to commit:                              │
│     - Run `git status && git diff HEAD && git log`    │
│     - Propose draft commit message                     │
│     - Prefer "why" over "what" in messages            │
│   - Never push without explicit request                │
│                                                         │
│   IF interactive:                                       │
│     "Keep user informed, ask for clarification"        │
└─────────────────────────────────────────────────────────┘
                         ↓
                      APPEND

SECTION 8: Plan Mode (CONDITIONAL)
┌─────────────────────────────────────────────────────────┐
│ IF planMode.enabled:                                    │
│   # Active Approval Mode: Plan                         │
│                                                         │
│   You are operating in **Plan Mode** - structured      │
│   planning workflow.                                   │
│                                                         │
│   ## Workflow Phases (ONE AT A TIME)                   │
│                                                         │
│   ### Phase 1: Requirements Understanding              │
│   - Analyze request                                    │
│   - Ask clarifying questions                           │
│   - Do NOT explore project yet                         │
│                                                         │
│   ### Phase 2: Project Exploration                     │
│   - Only begin after requirements clear                │
│   - Use read-only tools only                           │
│                                                         │
│   ### Phase 3: Design & Planning                       │
│   - Create detailed implementation plan                │
│   - Save to plans directory                            │
│                                                         │
│   ### Phase 4: Review & Approval                       │
│   - Present plan                                       │
│   - Request approval using `exit_plan_mode`            │
│                                                         │
│   ## CONSTRAINTS                                        │
│   - You may ONLY use read-only tools                   │
│   - You MUST NOT modify files                          │
└─────────────────────────────────────────────────────────┘
```

---

## Step 4: Final Prompt (sent to LLM)

```
┌──────────────────────────────────────────────────────────┐
│      COMPLETE SYSTEM PROMPT (~3,000-4,000 lines)         │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  • Interactive preamble (collaborative, ask questions)  │
│  • Core mandates (Directive vs Inquiry, security)       │
│  • Gemini 3 specifics (explain before acting)           │
│  • Skill guidance (follow activated skills)             │
│  • Development lifecycle (Research→Strategy→Execution)  │
│  • Codebase investigator instructions (use sub-agents)  │
│  • Operational guidelines (concise, no chitchat)        │
│  • Container sandbox notice (limited access)            │
│  • Git workflow rules (never auto-commit)               │
│  • Plan mode instructions (4-phase workflow)            │
│                                                          │
└──────────────────────────────────────────────────────────┘
                         ↓
                  SENT TO GEMINI 3
                         ↓
          🤖 LLM generates response following
             ALL the combined instructions
```

---

## Key Innovation: Directive vs Inquiry

### The Pattern That Makes Gemini CLI Unique

```
User Input Classification:
                 ↓
         ┌───────┴───────┐
         │               │
    INQUIRY          DIRECTIVE
         │               │
         ↓               ↓
    Research         Modify Files
    Explain          Execute
    Analyze          Create
         │               │
         ↓               ↓
    READ ONLY        FULL TOOLS
```

### Examples

**Inquiry (research only, no file changes):**
```
User: "Why does the API return 500?"
Agent: Analyzes code, explains issue, proposes fix → NO file changes
```

**Directive (action requested):**
```
User: "Fix the 500 error in the API"
Agent: Analyzes code, fixes issue, runs tests → Modifies files
```

**Ambiguous (defaults to Inquiry):**
```
User: "The API has a bug"
Agent: Treats as inquiry, investigates and explains → NO file changes
```

### Discussion: Minimal Decision Space

**The core insight:** This is a **binary classification with a safe default**, which minimizes the decision space to a single small list of trigger words.

**Why this works:**
```python
action_verbs = ["fix", "add", "create", "refactor", "update"]

if any(verb in prompt.lower() for verb in action_verbs):
    return "directive"
else:
    return "inquiry"  # Everything else
```

Note that `question_words` are defined but **not actually used** in the logic—the else clause catches everything that doesn't match action verbs.

**Design advantages:**
1. **Minimal maintenance**: Only one list to curate (~5 action verbs)
2. **Safe by default**: Ambiguous inputs fall to inquiry (read-only)
3. **Clear semantics**: Action verb = opt-in to file modifications
4. **Cognitive simplicity**: Two states, one trigger list, one default

**Contrast with alternatives:**
- Multi-class classification (e.g., "question", "command", "statement", "ambiguous") requires complex decision boundaries and edge case handling
- Sentiment/intent analysis requires ML models and confidence thresholds
- Rule-based multi-criteria systems require maintaining multiple trigger lists and precedence rules

**Trade-off:** This approach may over-classify some directives as inquiries (e.g., "The API needs fixing" lacks action verb), but this is **intentional**—better to ask "Should I fix this?" than to accidentally modify files. Users quickly learn to use action verbs when they want changes.

---

## Real-World Example: Task Execution

### User: "The authentication is broken"

**With interactive + gemini3 + gitRepo + planMode:**
```
🤖 Gemini CLI behavior:

1. Classifies as INQUIRY (no action verb)

2. Explains: "I'll investigate the authentication issue to understand
   what's broken"

3. Uses read-only tools:
   - Searches for auth files
   - Reads auth logic
   - Checks tests

4. Reports findings:
   "## Issue Analysis

   **Problem**: Token validation fails for expired tokens
   **Location**: `src/auth/validate.js:45`
   **Root Cause**: Missing expiration check

   **Proposed Fix**: Add expiration validation before signature check

   Would you like me to implement this fix?"

5. Waits for directive before modifying files
```

**If user responds: "Yes, fix it" (Directive)**
```
🤖 Gemini CLI behavior:

1. NOW classified as DIRECTIVE

2. In plan mode, creates implementation plan:
   - Phase 1: Design the fix
   - Phase 2: Implement with tests
   - Phase 3: Validate

3. After approval, implements fix

4. Runs tests automatically (validation required)

5. Reports: "Fixed and validated. Tests passing."

6. Since gitRepo=true: "Ready to commit? I can create a commit message."
```

---

## Configuration Space

### Total Combinations

```
Boolean flags (7):
- interactive
- gemini3
- skills
- codebaseInvestigator
- writeTodos
- shellEfficiency
- gitRepo

Enum flag (1):
- sandbox: macos | container | none

Complex flag (1):
- planMode: { enabled, plansDir, existingPlan, tools[] }

Total: 2^7 × 3 × (planMode variants) = ~384 configurations
```

---

## Visual Decision Tree

```
User Request: "The auth is broken"
                    ↓
        ┌───────────┴───────────┐
        │ Interactive mode?     │
        └┬──────────────────────┘
         ↓
    Ask clarifying
    questions first
         ↓
        ┌───────────┴───────────┐
        │ Directive or Inquiry? │
        └┬──────────┬───────────┘
         │          │
         ↓          ↓
    INQUIRY    DIRECTIVE
    Research     Fix it
    only         fully
         │          │
         ↓          ↓
        ┌───────────┴───────────┐
        │ Plan mode enabled?    │
        └┬──────────┬───────────┘
         │          │
         ↓          ↓
    Create plan   Execute
    first         directly
         │          │
         ↓          ↓
        ┌───────────┴───────────┐
        │ Git repo?             │
        └┬──────────┬───────────┘
         │          │
         ↓          ↓
    Offer to      Just
    commit        report
```

---

## Comparison: Gemini CLI vs Codex

| Dimension | Codex | Gemini CLI |
|-----------|-------|------------|
| **Architecture** | 24 separate files | 1 TypeScript function |
| **Configuration** | File selection | Conditional blocks |
| **Total Lines** | ~2,225 across files | ~1,500 in one file |
| **Configurations** | ~360 combinations | ~384 combinations |
| **Git Diffs** | Small (one file) | Large (entire function) |
| **Key Innovation** | Two kinds of unknowns | Directive vs Inquiry |

### Tradeoffs

**Codex Advantages:**
- ✅ Git-friendly (small diffs)
- ✅ Modular (reuse personalities)
- ✅ Easy to read individual components

**Gemini CLI Advantages:**
- ✅ Single source of truth (no file sync)
- ✅ All logic visible in one place
- ✅ Easier to trace composition flow

---

## Summary: The Assembly Pipeline

```
┌─────────┐    ┌──────────┐    ┌─────────┐    ┌────────┐
│  User   │───▶│ Runtime  │───▶│  Build  │───▶│ Gemini │
│  Input  │    │  Config  │    │ Prompt  │    │  Acts  │
└─────────┘    └──────────┘    └─────────┘    └────────┘
               interactive=    ONE function   Classifies
               true            with 10+       as Inquiry
               gemini3=true    conditionals   Researches
               planMode=true   assembles      Explains
               gitRepo=true    ~3500 lines    Waits
```

**The magic:** Change one config flag → different sections included → completely different behavior!

**Core philosophy:** Default to research, not action. Require explicit directives for file modifications.

---

## Why This Matters for co-cli

**Gemini CLI's key insight:** Most user requests are questions, not commands. Default to safe (research) mode.

**Practical application:**
```python
# co_cli/agent.py
def classify_user_intent(prompt: str) -> str:
    action_verbs = ["fix", "add", "create", "refactor", "update"]
    question_words = ["why", "how", "what", "explain"]

    if any(verb in prompt.lower() for verb in action_verbs):
        return "directive"  # OK to modify files
    else:
        return "inquiry"    # Research only, no changes
```

**Impact:** Prevents accidental file modifications when user just wants to understand something.

### Discussion: Implementation Strategy for co-cli

**Key architectural decision:** Binary classification minimizes both implementation complexity and cognitive load for users.

**Implementation considerations:**

1. **Single trigger list maintenance:**
   ```python
   # Only this list needs curation
   ACTION_VERBS = ["fix", "add", "create", "refactor", "update", "implement", "remove", "delete"]

   # Default behavior handles everything else
   is_directive = any(verb in prompt.lower() for verb in ACTION_VERBS)
   ```

2. **Extension strategy:**
   - Start with ~5-8 core action verbs
   - Add new verbs only when users consistently need to rephrase
   - Avoid expanding to >15 verbs (signals design issue)

3. **Logging and feedback loop:**
   - Log classification decisions with user's original prompt
   - Track when users say "no, fix it" after inquiry classification
   - Use this data to refine the action verb list

4. **User visibility:**
   - Agent can optionally announce classification: "I'll research this issue" (inquiry) vs "I'll fix this" (directive)
   - Helps users learn the distinction and phrase future requests effectively

**Why this beats complex classifiers:**
- No ML model dependencies (latency, costs, errors)
- No confidence thresholds to tune
- No training data requirements
- Behavior is **fully explainable** and debuggable
- Users can easily understand and adapt to the system

**Critical constraint:** The LLM must be instructed to respect this classification in the system prompt. The classification logic is a pre-filter that determines which tools are available, but the LLM must still choose to use only read-only tools for inquiries. This is enforced through:
1. Explicit instructions in system prompt (as shown in Core Mandates section)
2. Tool availability filtering (directive tools disabled in inquiry mode)
3. Post-execution validation (catch attempts to modify files during inquiry)
